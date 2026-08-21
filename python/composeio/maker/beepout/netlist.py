"""Parse a KiCad ``.kicad_pcb`` into the pad / net / geometry model that a beep-out
sheet is DERIVED from.

⛔ THE SOURCE OF TRUTH IS THE ``.kicad_pcb``, NEVER ``generate_pcb.py``.

That is not a style preference, it is the whole value of the instrument. Measured on
``rack_triple_gauge`` (2026-08-15):

* ``generate_pcb.py``'s ``generate_traces()`` draws the copper labelled **SDA** out of the
  header pin its OWN pad table assigns to **GND** (line 871), and the copper labelled
  **SCL** out of the pin its own table assigns to **+5V** (line 880). ``+3V3`` comes out of
  a ``GPIO20`` pin (line 861).
* Worse, and decisive: **the script's net-id space is not the board's.** The script's
  ``NETS`` reserves sparse ranges (10..21, 30..41, 50..58, 70..77); the shipped board uses
  dense 1..52 with a different assignment. Script net 50 is ``LED1_GPIO``; file net 50 is
  ``GPIO9``. Anything lifted from the script is wrong against the fabricated board.

A pad pair typed from the generator is a CLAIM. A pad pair read out of the fabricated
board's own copper is a MEASUREMENT. The sheet exists to carry measurements.

This module therefore reads only the emitted board file, whose per-pad ``(net N "name")``
carries both the id and the name and is thus self-checking.

Format notes (KiCad ``version 20241229``, ``generator_version "9.0"``):

* The net DECLARATION table is a run of ``(net N "name")`` forms at the top level of the
  ``kicad_pcb`` node. The same form appears again INSIDE each ``(pad ...)`` as a membership
  reference. Parsing by containment (not by indentation) is required to tell them apart.
* A pad's ``(at ...)`` is FOOTPRINT-RELATIVE; the footprint's ``(at ...)`` is absolute
  board mm. Either may carry an optional third rotation token.
* Pads with no ``(net ...)`` child exist (mounting holes) and mean "no net".
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "Pad",
    "Footprint",
    "Board",
    "parse_board",
    "SExprError",
]


class SExprError(ValueError):
    """The board file did not parse as the s-expression we require."""


# --------------------------------------------------------------------------------------
# s-expression reader
# --------------------------------------------------------------------------------------

_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<open>\()
      | (?P<close>\))
      | "(?P<str>(?:[^"\\]|\\.)*)"
      | (?P<atom>[^\s()"]+)
    )
    """,
    re.VERBOSE,
)


def _unescape(s: str) -> str:
    return s.replace("\\\\", "\\").replace('\\"', '"').replace("\\n", "\n")


def parse_sexpr(text: str):
    """Return the nested list form of ``text``.

    Strings are returned as ``('str', value)`` tuples so a quoted ``"("`` can never be
    confused with structure, and so an atom that merely LOOKS like a number is not coerced
    here — coercion is the caller's business, at the point where it knows the field.
    """
    stack: list[list] = []
    root: list | None = None
    pos = 0
    n = len(text)
    while pos < n:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise SExprError(f"unparsable at offset {pos}: {text[pos:pos + 40]!r}")
        pos = m.end()
        if m.group("open"):
            node: list = []
            if stack:
                stack[-1].append(node)
            else:
                if root is not None:
                    raise SExprError("more than one top-level form")
                root = node
            stack.append(node)
        elif m.group("close"):
            if not stack:
                raise SExprError(f"unbalanced ')' at offset {pos}")
            stack.pop()
        elif m.group("str") is not None:
            if not stack:
                raise SExprError("string outside any form")
            stack[-1].append(("str", _unescape(m.group("str"))))
        else:
            if not stack:
                raise SExprError("atom outside any form")
            stack[-1].append(m.group("atom"))
    if stack:
        raise SExprError("unterminated form — missing ')'")
    if root is None:
        raise SExprError("empty file")
    return root


def _head(node) -> str | None:
    """The symbol naming a form, e.g. ``pad`` for ``(pad "1" ...)``."""
    if isinstance(node, list) and node and isinstance(node[0], str):
        return node[0]
    return None


def _children(node, name: str):
    """Direct children of ``node`` whose head is ``name`` — containment, not indentation."""
    if not isinstance(node, list):
        return
    for c in node:
        if _head(c) == name:
            yield c


def _child(node, name: str):
    for c in _children(node, name):
        return c
    return None


def _sval(item) -> str | None:
    """The value of a ``('str', v)`` tuple, else None."""
    if isinstance(item, tuple) and len(item) == 2 and item[0] == "str":
        return item[1]
    return None


def _text(item) -> str | None:
    """A string tuple's value, or a bare atom, whichever this is."""
    s = _sval(item)
    if s is not None:
        return s
    if isinstance(item, str):
        return item
    return None


def _floats(node, start: int = 1) -> list[float]:
    out: list[float] = []
    for item in node[start:]:
        t = _text(item)
        if t is None:
            break
        try:
            out.append(float(t))
        except ValueError:
            break
    return out


# --------------------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Pad:
    """One pad, with its net membership and its ABSOLUTE board position."""

    ref: str
    """Reference designator of the owning footprint, as it appears in the file."""
    number: str
    """Pad name/number, e.g. ``"13"``. May be ``""`` (mounting holes)."""
    net_num: int | None
    net_name: str | None
    x: float
    """Absolute board mm."""
    y: float
    rel_x: float
    """Footprint-relative mm — rotation-invariant, so intra-footprint adjacency uses these."""
    rel_y: float
    pad_type: str
    """``thru_hole`` / ``smd`` / ``np_thru_hole``."""
    size_w: float
    size_h: float
    footprint_lib: str
    ref_hidden_on_silk: bool
    """True when the owning footprint's Reference property carries ``(hide yes)`` — i.e.
    the designator is NOT printed on the board, so a human cannot find this pad by label."""

    @property
    def label(self) -> str:
        if self.number:
            return f"{self.ref} pin {self.number}"
        return f"{self.ref} (unnumbered pad)"

    def distance_to(self, other: "Pad") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def rel_distance_to(self, other: "Pad") -> float:
        return math.hypot(self.rel_x - other.rel_x, self.rel_y - other.rel_y)


@dataclass
class Footprint:
    ref: str
    value: str
    lib: str
    x: float
    y: float
    rot: float
    ref_hidden_on_silk: bool
    pads: list[Pad] = field(default_factory=list)

    @property
    def min_pad_pitch(self) -> float | None:
        """The smallest centre-to-centre spacing between any two pads of THIS footprint.

        Self-calibrating: adjacency is defined against a footprint's own minimum spacing,
        never against a global "fine pitch" constant. A guess about which parts are
        fine-pitch is exactly the hand-authored claim this instrument exists to replace.
        """
        best: float | None = None
        pads = self.pads
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                d = pads[i].rel_distance_to(pads[j])
                if d <= 0:
                    continue
                if best is None or d < best:
                    best = d
        return best


@dataclass
class Board:
    name: str
    source_path: Path
    source_sha256: str
    kicad_version: str
    generator: str
    nets: dict[int, str]
    """The DECLARED net table: net number -> net name."""
    footprints: list[Footprint]
    track_endpoints: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    """Per net, the endpoints of every copper segment/arc. Used only by
    :meth:`geometry_coincidence` as an independent check on our own pad geometry."""

    # -- derived views ------------------------------------------------------------------

    @property
    def pads(self) -> list[Pad]:
        return [p for fp in self.footprints for p in fp.pads]

    @property
    def pads_by_net(self) -> dict[int, list[Pad]]:
        out: dict[int, list[Pad]] = {}
        for p in self.pads:
            if p.net_num is None:
                continue
            out.setdefault(p.net_num, []).append(p)
        return out

    def net_name(self, num: int) -> str:
        return self.nets.get(num, f"<net {num}>")

    # -- self-check ---------------------------------------------------------------------

    def geometry_coincidence(self, tol_mm: float = 0.6) -> tuple[int, int]:
        """An INDEPENDENT check on our own pad-position arithmetic.

        Returns ``(matched, total)`` counting netted pads that lie within ``tol_mm`` of a
        copper endpoint on the SAME net. The rotation convention and the relative/absolute
        composition are the two places this parser could be silently wrong, and both would
        yield plausible-looking coordinates rather than an error. Copper endpoints are
        written by KiCad from a different code path than pad positions, so agreement
        between them is evidence that does not share this parser's assumption.

        This is a DIAGNOSTIC, not a gate: boards rerouted by Freerouting legitimately have
        segments that stop short of a pad centre.
        """
        matched = 0
        total = 0
        for p in self.pads:
            if p.net_num is None:
                continue
            ends = self.track_endpoints.get(p.net_num)
            if not ends:
                continue
            total += 1
            for (ex, ey) in ends:
                if math.hypot(p.x - ex, p.y - ey) <= tol_mm:
                    matched += 1
                    break
        return matched, total


# --------------------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------------------


def _at(node) -> tuple[float, float, float]:
    """``(at X Y [rot])`` -> (x, y, rot). Missing rotation is 0."""
    a = _child(node, "at")
    if a is None:
        return 0.0, 0.0, 0.0
    vals = _floats(a)
    x = vals[0] if len(vals) > 0 else 0.0
    y = vals[1] if len(vals) > 1 else 0.0
    r = vals[2] if len(vals) > 2 else 0.0
    return x, y, r


def _property(node, key: str) -> tuple[str | None, bool]:
    """Return ``(value, hidden)`` for ``(property "key" "value" ... (hide yes))``.

    ``hidden`` matters: a Reference with ``(hide yes)`` is NOT printed on the silkscreen,
    so the human holding the meter has no label to find that part by. Reporting a probe
    point as ``U1 pin 13`` when the board says nothing anywhere is a sheet that cannot be
    followed, and it fails silently — the reader assumes they are looking in the wrong
    place.
    """
    for p in _children(node, "property"):
        if len(p) >= 2 and _sval(p[1]) == key:
            value = _sval(p[2]) if len(p) >= 3 else None
            hidden = False
            h = _child(p, "hide")
            if h is not None and len(h) >= 2 and _text(h[1]) in ("yes", "true"):
                hidden = True
            return value, hidden
    return None, False


def _rotate(x: float, y: float, deg: float) -> tuple[float, float]:
    """Rotate a footprint-relative offset into board coordinates.

    KiCad's board space is Y-DOWN, and a footprint's stored orientation is
    counter-clockwise as drawn. In that system the composition that reproduces the
    on-screen geometry is a rotation by -deg in ordinary maths convention. The sign is not
    self-evident from the file and getting it backwards produces coordinates that look
    entirely reasonable — which is why :meth:`Board.geometry_coincidence` exists to check
    it against copper written by a different code path.
    """
    if deg == 0:
        return x, y
    t = math.radians(-deg)
    c, s = math.cos(t), math.sin(t)
    return x * c - y * s, x * s + y * c


def _parse_pad(pad_node, fp: Footprint) -> Pad | None:
    number = _sval(pad_node[1]) if len(pad_node) >= 2 else None
    if number is None:
        number = ""
    pad_type = _text(pad_node[2]) if len(pad_node) >= 3 else "unknown"

    px, py, _prot = _at(pad_node)
    rx, ry = _rotate(px, py, fp.rot)
    ax, ay = fp.x + rx, fp.y + ry

    size = _child(pad_node, "size")
    sw = sh = 0.0
    if size is not None:
        sv = _floats(size)
        sw = sv[0] if len(sv) > 0 else 0.0
        sh = sv[1] if len(sv) > 1 else 0.0

    net_num: int | None = None
    net_name: str | None = None
    net = _child(pad_node, "net")
    if net is not None and len(net) >= 2:
        try:
            net_num = int(_text(net[1]))
        except (TypeError, ValueError):
            net_num = None
        if len(net) >= 3:
            net_name = _sval(net[2])

    return Pad(
        ref=fp.ref,
        number=number,
        net_num=net_num,
        net_name=net_name,
        x=ax,
        y=ay,
        rel_x=px,
        rel_y=py,
        pad_type=pad_type or "unknown",
        size_w=sw,
        size_h=sh,
        footprint_lib=fp.lib,
        ref_hidden_on_silk=fp.ref_hidden_on_silk,
    )


def parse_board(path: str | Path) -> Board:
    """Parse ``path`` (a ``.kicad_pcb``) into a :class:`Board`.

    The returned board carries the sha256 of the exact bytes read, so every artifact
    derived from it can name the source it is true of. That closes by ENFORCEMENT the
    residual risk ``[PCB-CORPUS-PLACEMENT]`` records as accepted: *"the trigger is a
    CONVENTION, not an enforced check — nothing runs shasum on a schedule today."*
    """
    p = Path(path).expanduser().resolve()
    raw = p.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    root = parse_sexpr(raw.decode("utf-8", errors="replace"))

    if _head(root) != "kicad_pcb":
        raise SExprError(f"{p} is not a kicad_pcb (top-level form is {_head(root)!r})")

    ver = _child(root, "version")
    kicad_version = _text(ver[1]) if ver is not None and len(ver) >= 2 else "unknown"
    gen = _child(root, "generator_version")
    generator = _text(gen[1]) if gen is not None and len(gen) >= 2 else "unknown"

    # Net DECLARATION table — direct children of the root only. The identical form also
    # appears inside every pad; containment is what separates them.
    nets: dict[int, str] = {}
    for net in _children(root, "net"):
        if len(net) < 3:
            continue
        try:
            num = int(_text(net[1]))
        except (TypeError, ValueError):
            continue
        nets[num] = _sval(net[2]) or ""

    footprints: list[Footprint] = []
    for fnode in _children(root, "footprint"):
        lib = _sval(fnode[1]) if len(fnode) >= 2 else ""
        fx, fy, frot = _at(fnode)
        ref, ref_hidden = _property(fnode, "Reference")
        value, _ = _property(fnode, "Value")
        fp = Footprint(
            ref=ref or "<no-reference>",
            value=value or "",
            lib=lib or "",
            x=fx,
            y=fy,
            rot=frot,
            ref_hidden_on_silk=ref_hidden,
        )
        for pnode in _children(fnode, "pad"):
            pad = _parse_pad(pnode, fp)
            if pad is not None:
                fp.pads.append(pad)
        footprints.append(fp)

    # Copper endpoints, for the geometry self-check only.
    endpoints: dict[int, list[tuple[float, float]]] = {}
    for kind in ("segment", "arc", "via"):
        for seg in _children(root, kind):
            nnode = _child(seg, "net")
            if nnode is None or len(nnode) < 2:
                continue
            try:
                nnum = int(_text(nnode[1]))
            except (TypeError, ValueError):
                continue
            for key in ("start", "end", "at"):
                sub = _child(seg, key)
                if sub is None:
                    continue
                v = _floats(sub)
                if len(v) >= 2:
                    endpoints.setdefault(nnum, []).append((v[0], v[1]))

    return Board(
        name=p.stem,
        source_path=p,
        source_sha256=digest,
        kicad_version=kicad_version,
        generator=generator,
        nets=nets,
        footprints=footprints,
        track_endpoints=endpoints,
    )
