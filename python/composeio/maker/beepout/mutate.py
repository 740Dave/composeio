"""FAULT INJECTORS — deliberate netlist mutations that MUST change the derived sheet.

⛔ AN INSTRUMENT THAT CAN ONLY REPORT SUCCESS IS NOT AN INSTRUMENT.

A generator whose first run produced a plausible sheet, and which was never seen to FAIL,
is a claim. This module is the negative control that makes the claim falsifiable: mutate a
real board file so it describes a board with a known defect, re-derive, and prove the
output changed in the way that would catch it. If the output does NOT change, the row was
being TEMPLATED rather than derived — which is exactly the drop counter that reported zero
because enforcement happened somewhere it could never increment.

These are kept PUBLIC on purpose, mirroring the hookup surface's ``svgdom_rasterize``: the
fault injector is part of the instrument, not a test fixture hidden beside it. A gate whose
falsifier is not shipped cannot be re-run by the next reader.

Every function here is a pure text transform over ``.kicad_pcb`` source. Nothing under
``~/Dropbox/Maker/pcb/`` is ever written — the mutants are materialised in a temp dir.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

__all__ = [
    "short_two_nets",
    "break_net_pad",
    "displace_pad",
    "materialise",
]


def materialise(text: str, name: str, workdir: str | Path | None = None) -> Path:
    """Write mutated ``text`` to a temp ``.kicad_pcb`` and return its path."""
    d = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="beepout-mutant-"))
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.kicad_pcb"
    p.write_text(text, encoding="utf-8")
    return p


def short_two_nets(text: str, victim_net: int, absorbed_into: int,
                   absorbed_name: str) -> str:
    """Simulate a DEAD SHORT between two nets by MERGING them.

    A physical short between +5V and GND is copper the netlist does not describe — the two
    nets remain distinct in the file and only DRC would object. So the honest injection at
    this layer is the one that changes what the file CLAIMS: make every pad that carried
    ``victim_net`` carry ``absorbed_into`` instead, and drop the victim's declaration.

    The sheet must then STOP emitting the isolation row for that pair, because the two
    rails are no longer distinct nets and "expect OPEN" would be a lie. A sheet that still
    prints the row after this mutation was printing it from a template.
    """
    out = re.sub(
        rf'\(net {victim_net} "[^"]*"\)',
        f'(net {absorbed_into} "{absorbed_name}")',
        text,
    )
    # Remove the now-duplicated declaration line left at the top-level net table.
    out = re.sub(
        rf'\n\t\(net {absorbed_into} "{re.escape(absorbed_name)}"\)(?=\n\t\(net )',
        "",
        out,
        count=1,
    )
    return out


def break_net_pad(text: str, net_num: int, net_name: str, occurrence: int = 0) -> str:
    """Simulate an UNPLACED / ORPHANED pad by removing one pad from a net.

    The pad keeps existing; it simply stops belonging to the net. The derived sheet must
    react: the net's pad count drops, and because the continuity pair is chosen as the two
    pads FURTHEST APART, removing an extreme pad provably MOVES the chosen pair. If the
    pair does not move, the pair was not being chosen from geometry.
    """
    pattern = re.compile(rf'(\n\s*)\(net {net_num} "{re.escape(net_name)}"\)')
    matches = list(pattern.finditer(text))
    if not matches:
        raise ValueError(f"no pad references net {net_num} {net_name!r}")
    # Skip the top-level declaration (first match) unless explicitly targeted.
    usable = matches[1:] if len(matches) > 1 else matches
    if occurrence >= len(usable):
        raise ValueError(f"only {len(usable)} pad references available")
    m = usable[occurrence]
    return text[: m.start()] + text[m.end():]


def displace_pad(text: str, ref: str, pad_number: str, dx: float, dy: float) -> str:
    """Move one pad of one footprint, to prove ADJACENCY is read from geometry.

    Adjacency rows are emitted for neighbouring pads carrying different nets, with the
    threshold calibrated from each footprint's OWN minimum spacing. Pushing a pad away must
    drop the rows that named it. If they survive, adjacency was coming from a hardcoded
    notion of which parts are dense.
    """
    # Locate the footprint block whose Reference property is `ref`.
    fp_start = None
    for m in re.finditer(r'\n\t\(footprint ', text):
        seg = text[m.start(): m.start() + 4000]
        rm = re.search(r'\(property "Reference" "([^"]*)"', seg)
        if rm and rm.group(1) == ref:
            fp_start = m.start()
            break
    if fp_start is None:
        raise ValueError(f"footprint {ref!r} not found")

    nxt = text.find("\n\t(footprint ", fp_start + 5)
    if nxt == -1:
        nxt = len(text)
    block = text[fp_start:nxt]

    pm = re.search(
        rf'(\(pad "{re.escape(pad_number)}"[^\n]*\n\s*\(at )(-?[\d.]+) (-?[\d.]+)',
        block,
    )
    if not pm:
        raise ValueError(f"pad {pad_number!r} not found on {ref!r}")
    nx = float(pm.group(2)) + dx
    ny = float(pm.group(3)) + dy
    new_block = block[: pm.start()] + f"{pm.group(1)}{nx} {ny}" + block[pm.end():]
    return text[:fp_start] + new_block + text[nxt:]
