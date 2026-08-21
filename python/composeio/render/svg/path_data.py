"""
SVG path-data grammar -> Canvas-contract PathCommand list ([SPEC:CXC-SVG]).

Hand-written tokenizer + parser for the SVG 1.1 path `d` grammar — zero
dependency, mirroring the Lottie subset evaluator's no-heavy-dep posture
(render/lottie/).  Every command decomposes to the EXISTING Canvas
primitives (MoveTo/LineTo/CurveTo/QuadTo/ArcTo/Close), so a parsed path
renders on every backend through the one contract.

Supported (abs UPPER + rel lower):
    M/m L/l H/h V/v  C/c S/s  Q/q T/t  A/a  Z/z
H/V collapse to LineTo; S/T synthesise the reflected control point; A maps
straight to the contract's ArcTo (rx, ry, x-rotation deg, large-arc, sweep).
Implicit repeated commands (extra coord sets after M/L/etc.) are honoured,
including the SVG rule that repeated pairs after a moveto are linetos.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from ..canvas import (ArcTo, Close, CurveTo, LineTo, MoveTo, PathCommand,
                      QuadTo)

# number token: optional sign, int/float, optional exponent.  A leading
# sign or a '.' also acts as an implicit token separator ("1-2" -> 1,-2;
# "1.2.3" -> 1.2,.3), which the split-on-command tokenizer below relies on.
_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_CMD = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]")


def _tokenize_numbers(s: str) -> List[float]:
    return [float(m.group()) for m in _NUM.finditer(s)]


def _split_commands(d: str) -> List[Tuple[str, List[float]]]:
    """Split a path string into (command-letter, number-args) chunks."""
    out: List[Tuple[str, List[float]]] = []
    pos = 0
    n = len(d)
    while pos < n:
        m = _CMD.search(d, pos)
        if not m:
            break
        cmd = m.group()
        nxt = _CMD.search(d, m.end())
        end = nxt.start() if nxt else n
        out.append((cmd, _tokenize_numbers(d[m.end():end])))
        pos = end
    return out


def parse_path(d: str) -> List[PathCommand]:
    """Parse an SVG path `d` string into contract PathCommands.

    Malformed / truncated data is tolerated: a command with too few
    numbers to complete its final tuple stops consuming there (the screen
    is never broken — [CXC-R-DRAWIMAGE] precedent)."""
    cmds: List[PathCommand] = []
    if not d:
        return cmds

    cx = cy = 0.0            # current point
    sx = sy = 0.0            # start of current subpath (for Z)
    # last control points (absolute) for smooth-curve reflection
    last_c: Tuple[float, float] | None = None    # cubic
    last_q: Tuple[float, float] | None = None     # quad
    prev_cmd = ""

    for letter, args in _split_commands(d):
        rel = letter.islower()
        up = letter.upper()
        i = 0

        if up == "Z":
            cmds.append(Close())
            cx, cy = sx, sy
            last_c = last_q = None
            prev_cmd = up
            continue

        while True:
            if up == "M":
                if i + 2 > len(args):
                    break
                x, y = args[i], args[i + 1]
                if rel:
                    x, y = cx + x, cy + y
                cx, cy = x, y
                sx, sy = x, y
                cmds.append(MoveTo(cx, cy))
                i += 2
                up = "L"        # subsequent pairs are implicit linetos
            elif up == "L":
                if i + 2 > len(args):
                    break
                x, y = args[i], args[i + 1]
                if rel:
                    x, y = cx + x, cy + y
                cx, cy = x, y
                cmds.append(LineTo(cx, cy))
                i += 2
            elif up == "H":
                if i + 1 > len(args):
                    break
                x = args[i]
                cx = cx + x if rel else x
                cmds.append(LineTo(cx, cy))
                i += 1
            elif up == "V":
                if i + 1 > len(args):
                    break
                y = args[i]
                cy = cy + y if rel else y
                cmds.append(LineTo(cx, cy))
                i += 1
            elif up == "C":
                if i + 6 > len(args):
                    break
                c1x, c1y, c2x, c2y, x, y = args[i:i + 6]
                if rel:
                    c1x, c1y = cx + c1x, cy + c1y
                    c2x, c2y = cx + c2x, cy + c2y
                    x, y = cx + x, cy + y
                cmds.append(CurveTo(c1x, c1y, c2x, c2y, x, y))
                last_c = (c2x, c2y)
                cx, cy = x, y
                i += 6
            elif up == "S":
                if i + 4 > len(args):
                    break
                c2x, c2y, x, y = args[i:i + 4]
                if rel:
                    c2x, c2y = cx + c2x, cy + c2y
                    x, y = cx + x, cy + y
                if prev_cmd in ("C", "S") and last_c is not None:
                    c1x, c1y = 2 * cx - last_c[0], 2 * cy - last_c[1]
                else:
                    c1x, c1y = cx, cy
                cmds.append(CurveTo(c1x, c1y, c2x, c2y, x, y))
                last_c = (c2x, c2y)
                cx, cy = x, y
                i += 4
            elif up == "Q":
                if i + 4 > len(args):
                    break
                qx, qy, x, y = args[i:i + 4]
                if rel:
                    qx, qy = cx + qx, cy + qy
                    x, y = cx + x, cy + y
                cmds.append(QuadTo(qx, qy, x, y))
                last_q = (qx, qy)
                cx, cy = x, y
                i += 4
            elif up == "T":
                if i + 2 > len(args):
                    break
                x, y = args[i:i + 2]
                if rel:
                    x, y = cx + x, cy + y
                if prev_cmd in ("Q", "T") and last_q is not None:
                    qx, qy = 2 * cx - last_q[0], 2 * cy - last_q[1]
                else:
                    qx, qy = cx, cy
                cmds.append(QuadTo(qx, qy, x, y))
                last_q = (qx, qy)
                cx, cy = x, y
                i += 2
            elif up == "A":
                if i + 7 > len(args):
                    break
                rx, ry, rot, large, sweep, x, y = args[i:i + 7]
                if rel:
                    x, y = cx + x, cy + y
                cmds.append(ArcTo(abs(rx), abs(ry), rot,
                                  bool(int(large)), bool(int(sweep)), x, y))
                cx, cy = x, y
                i += 7
            else:
                break

            prev_cmd = up
            if up not in ("C", "S"):
                last_c = None
            if up not in ("Q", "T"):
                last_q = None
            if i >= len(args):
                break

    return cmds
