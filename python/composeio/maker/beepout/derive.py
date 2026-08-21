"""Derive beep-out probe rows from a parsed board.

A hookup guide says *"connect these."* A beep-out sheet says *"probe these two pads and
expect THIS."* It is a FALSIFICATION INSTRUMENT FOR A BARE BOARD, and bare is the only
time some faults are visible: once parts are on, a fab defect and a solder defect are
indistinguishable.

⛔ EVERY ROW CARRIES ITS DERIVATION STATE, AND THERE ARE THREE OF THEM.

Two states cannot say "I looked and could not tell". That gap is not hypothetical here —
the descriptor-parity checker had exactly this shape, and wiring an unprobed pin produced
SILENCE: neither clean nor reserved, with a two-state contract unable to report it. A third
state was added. The same three states are used here:

* ``DERIVED``    — the row's expectation follows from the netlist/geometry. Falsifiable:
                   mutate the source and the row changes.
* ``UNVERIFIED`` — the check is REAL and NECESSARY but the board file cannot settle it
                   (which physical part you bought; whether a designator is printed).
                   ⛔ EMITTED, NEVER OMITTED. An omitted check is indistinguishable from a
                   check nobody thought of.
* ``BLOCKED``    — the check is required but cannot even be stated from this source (a net
                   with one pad has no second probe point). Also emitted, also never
                   dropped, because "there is nothing to test here" is a FINDING.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .netlist import Board, Footprint, Pad

__all__ = [
    "RowState",
    "Category",
    "ProbeRow",
    "Sheet",
    "derive_sheet",
    "classify_rails",
    "MeterFunction",
]


class RowState(str, Enum):
    DERIVED = "DERIVED"
    UNVERIFIED = "UNVERIFIED"
    BLOCKED = "BLOCKED"


class Category(str, Enum):
    RAIL_ISOLATION = "rail-isolation"
    PART_ORIENTATION = "part-orientation"
    ADJACENT_PAD = "adjacent-pad-isolation"
    NET_CONTINUITY = "net-continuity"
    NET_BLOCKED = "net-untestable"


class MeterFunction(str, Enum):
    RESISTANCE_2M = "resistance, 2 MΩ"
    CONTINUITY = "continuity / beep"
    DIODE = "diode mode"


#: Probe ORDER. Front-loads the checks whose failure DESTROYS A PART ON POWER-UP.
#: A rail-to-rail short puts 12 V on a 5 V part the instant power is applied; an open
#: trace merely fails to work. So shorts are hunted before opens, always — and a reversed
#: polarised part is hunted before the bridges, because it is the single most common cause
#: of the rail short the first block just looked for.
CATEGORY_ORDER: dict[Category, int] = {
    Category.RAIL_ISOLATION: 1,
    Category.PART_ORIENTATION: 2,
    Category.ADJACENT_PAD: 3,
    Category.NET_CONTINUITY: 4,
    Category.NET_BLOCKED: 5,
}


# --------------------------------------------------------------------------------------
# rail classification
# --------------------------------------------------------------------------------------

_GROUND = re.compile(r"^(GND|AGND|DGND|PGND|VSS|GROUND|0V)$", re.I)
_SUPPLY = re.compile(
    r"""^(
          [+-]?\d+V\d*(_\w+)?      # +12V, +3V3, 5V, +12V_IN
        | \d+V\d+                  # 3V3
        | VCC|VDD|VBUS|VIN|VBAT|AVCC|VVDD
      )$""",
    re.I | re.VERBOSE,
)


def classify_rails(board: Board) -> tuple[list[int], dict[int, str]]:
    """Return ``(rail_net_numbers, reason_by_net)``.

    Classification is BY NAME, and the matching pattern is recorded per net so the sheet
    can state on what basis it called something a rail. That matters: the three boards in
    this fleet spell the same rail three ways (``+3V3``, ``3V3``, and ``VCC`` for what is
    physically a 5 V supply), so a hardcoded list silently misses rails — and a missed rail
    is a missed short, which is the one failure that destroys parts.

    ⛔ Classification being a heuristic does NOT make the resulting rows unfalsifiable.
    The row's EXPECTATION (isolated) comes from the two nets being DISTINCT IN THE NETLIST,
    not from the name. Merge them in the source and the row provably disappears.
    """
    rails: list[int] = []
    why: dict[int, str] = {}
    for num, name in sorted(board.nets.items()):
        if num == 0 or not name:
            continue
        if _GROUND.match(name):
            rails.append(num)
            why[num] = "name matches a ground pattern"
        elif _SUPPLY.match(name):
            rails.append(num)
            why[num] = "name matches a supply pattern"
    return rails, why


# --------------------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------------------


@dataclass
class ProbeRow:
    category: Category
    state: RowState
    from_label: str
    to_label: str
    meter: MeterFunction
    expect: str
    why: str
    """WHY this row exists and what in the source produced it — the provenance a reader
    needs to decide whether to trust it."""
    caution: str | None = None
    on_fail: str | None = None
    net_hint: str | None = None
    span_mm: float | None = None

    @property
    def order(self) -> int:
        return CATEGORY_ORDER[self.category]


@dataclass
class Sheet:
    board: Board
    rows: list[ProbeRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    rails: list[int] = field(default_factory=list)

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.category.value] = out.get(r.category.value, 0) + 1
        return out

    def by_state(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rows:
            out[r.state.value] = out.get(r.state.value, 0) + 1
        return out

    def ordered(self) -> list[ProbeRow]:
        return sorted(self.rows, key=lambda r: (r.order, r.from_label, r.to_label))


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _best_probe_pad(pads: list[Pad]) -> Pad:
    """Prefer a pad a human can actually reach and identify.

    A test header or terminal block beats an IC pad: it is bigger, it is on the edge, and
    its designator is more likely to be printed. Among equals, prefer one whose reference
    is NOT hidden on the silkscreen.
    """
    def rank(p: Pad) -> tuple:
        lib = p.footprint_lib.lower()
        is_header = any(k in lib for k in ("pinheader", "terminalblock", "conn_", "jst"))
        return (
            0 if is_header else 1,
            0 if not p.ref_hidden_on_silk else 1,
            -(p.size_w * p.size_h),
        )

    return sorted(pads, key=rank)[0]


def _diameter_pair(pads: list[Pad]) -> tuple[Pad, Pad, float]:
    """The two pads of a net that are FURTHEST APART.

    A short hop proves nothing about the far end: two adjacent pads of a pour are connected
    by an inch of copper regardless of whether the trace to the other side of the board
    ever got etched. Spanning the longest run is what makes the row informative, and it is
    derived from actual pad geometry — remove a pad and the chosen pair provably moves.
    """
    best = (pads[0], pads[1] if len(pads) > 1 else pads[0], -1.0)
    for i in range(len(pads)):
        for j in range(i + 1, len(pads)):
            d = pads[i].distance_to(pads[j])
            if d > best[2]:
                best = (pads[i], pads[j], d)
    return best


def _label(p: Pad) -> str:
    if p.ref_hidden_on_silk:
        return f"{p.label} [no silk label]"
    return p.label


# --------------------------------------------------------------------------------------
# per-category derivation
# --------------------------------------------------------------------------------------


#: Footprint families whose pins are CONDUCTIVELY joined by the part itself. Deliberately
#: narrow: diodes, resistors, linear regulators and inductors/ferrites/jumpers. Headers,
#: connectors, terminal blocks, ICs and capacitors are excluded — see `_bridging_parts`.
#: ⚠ Patterns are anchored to a NAME BOUNDARY (start of string or just after the library
#: ``:``). An unanchored ``L_`` matched "Radia**l_**D5.0mm" and silently re-admitted every
#: electrolytic as a conducting bridge — a capacitor is exactly the part whose DC-open
#: behaviour is WHY the pair is expected to read open, so the false positive inverted the
#: meaning of the row it attached to.
_CONDUCTS_BETWEEN_PINS = re.compile(
    r"(?:^|:)(?:Diode_|D_DO-|D_A|R_Axial|R_\d{4}|TO-220|TO-263|L_|Inductor|Ferrite"
    r"|Jumper|Fuse|SolderJumper)",
    re.I,
)


def _bridging_parts(board: Board, net_a: int, net_b: int) -> list[Footprint]:
    """Footprints with a pad on BOTH nets — i.e. a part that JOINS the two rails.

    This is the difference between "these rails must never touch" and "these rails are
    deliberately connected THROUGH a component". A reverse-protection diode sits between
    ``+12V_IN`` and ``+12V``; a regulator sits between ``+12V`` and ``+5V``. On a BARE
    board those parts are not fitted and the pair genuinely reads open — but the moment the
    part goes in, the expectation changes, and a sheet that still says "expect OPEN" is
    then wrong in a way that looks like a fault.

    Derived, not guessed: it falls straight out of pad->net membership.

    ⛔ Only parts that ACTUALLY CONDUCT between their pins count. A pin header has pins on
    both nets and joins nothing; a decoupling capacitor is an open circuit at DC — and that
    is precisely WHY the pair is expected to read open. Counting either as a bridge would
    bury the two real cases (the diode and the regulator) under a dozen false ones, and a
    caution nobody can act on is noise that trains the reader to skip cautions.
    """
    out: list[Footprint] = []
    for fp in board.footprints:
        if not _CONDUCTS_BETWEEN_PINS.search(fp.lib):
            continue
        nets = {p.net_num for p in fp.pads if p.net_num is not None}
        if net_a in nets and net_b in nets:
            out.append(fp)
    return out


def _rail_isolation_rows(board: Board, rails: list[int], why: dict[int, str],
                         has_bulk_cap: bool) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    pads_by_net = board.pads_by_net
    present = [n for n in rails if pads_by_net.get(n)]
    for i in range(len(present)):
        for j in range(i + 1, len(present)):
            a, b = present[i], present[j]
            pa = _best_probe_pad(pads_by_net[a])
            pb = _best_probe_pad(pads_by_net[b])
            na, nb = board.net_name(a), board.net_name(b)
            bridges = _bridging_parts(board, a, b)
            caution = (
                "⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. This board carries bulk "
                "electrolytic(s); an UNCHARGED 100 µF BEEPS on a continuity tester as the "
                "meter's test current charges it. That is a CORRECT reading of a GOOD "
                "board. Watch the number climb and read the SETTLED value."
                if has_bulk_cap else
                "⛔ USE RESISTANCE (2 MΩ), NOT CONTINUITY. Continuity mode reports a "
                "threshold, not a value; a partial short reads as a pass. Read the SETTLED "
                "resistance."
            )
            bridge_note = ""
            if bridges:
                names = ", ".join(f"`{f.ref}` ({f.value or f.lib.split(':')[-1]})"
                                  for f in bridges)
                bridge_note = (
                    f" ⚠ **THESE TWO RAILS ARE DELIBERATELY JOINED THROUGH {names}.** On a "
                    "BARE board that part is not fitted, so OPEN is still the correct "
                    "reading and a short here is still a defect. ⛔ But once the part IS "
                    "fitted this pair reads THROUGH it — a diode ~0.4–0.8 V forward and OL "
                    "reversed in diode mode; a linear regulator some hundreds of kΩ. Do "
                    "not re-run this row on a stuffed board and read the result as a "
                    "short."
                )
            rows.append(ProbeRow(
                category=Category.RAIL_ISOLATION,
                state=RowState.DERIVED,
                from_label=f"{na} @ {_label(pa)}",
                to_label=f"{nb} @ {_label(pb)}",
                meter=MeterFunction.RESISTANCE_2M,
                expect="settles > 100 kΩ (typically OL)",
                why=(f"`{na}` (net {a}) and `{nb}` (net {b}) are DISTINCT nets in the board "
                     f"file, so no copper may join them. {why.get(a, '')}; "
                     f"{why.get(b, '')}.{bridge_note}"),
                caution=caution,
                on_fail=(f"< 1 kΩ is a SHORT between {na} and {nb}. ⛔ DO NOT APPLY POWER. "
                         "Suspect a reversed electrolytic, a bridge under a regulator tab, "
                         "or solder splash on the widest pour."),
                net_hint=f"{na} <-> {nb}",
            ))
    return rows


def _continuity_rows(board: Board) -> tuple[list[ProbeRow], list[ProbeRow]]:
    """Return ``(derived_rows, blocked_rows)``."""
    derived: list[ProbeRow] = []
    blocked: list[ProbeRow] = []
    pads_by_net = board.pads_by_net
    for num, name in sorted(board.nets.items()):
        if num == 0 or not name:
            continue
        pads = pads_by_net.get(num, [])
        if len(pads) >= 2:
            a, b, span = _diameter_pair(pads)
            derived.append(ProbeRow(
                category=Category.NET_CONTINUITY,
                state=RowState.DERIVED,
                from_label=_label(a),
                to_label=_label(b),
                meter=MeterFunction.CONTINUITY,
                expect="BEEP (short)",
                why=(f"`{name}` (net {num}) spans {len(pads)} pads; this pair is the "
                     f"FURTHEST APART ({span:.1f} mm), chosen so the check crosses the "
                     "longest run of the net rather than a short hop."),
                on_fail=("No beep is an OPEN. ⛔ An open reads IDENTICALLY to a good trace "
                         "from the MCU side — no firmware diagnostic can find this. It is "
                         "found here or it is found as a mystery symptom later."),
                net_hint=name,
                span_mm=span,
            ))
        else:
            n = len(pads)
            blocked.append(ProbeRow(
                category=Category.NET_BLOCKED,
                state=RowState.BLOCKED,
                from_label=(_label(pads[0]) if pads else f"net `{name}`"),
                to_label="— no second pad —",
                meter=MeterFunction.CONTINUITY,
                expect="NOT TESTABLE on a bare board",
                why=(f"`{name}` (net {num}) has {n} pad(s) in the board file. A continuity "
                     "check needs two probe points; there is no second one."),
                caution=("⛔ EMITTED, NOT OMITTED. A net with one pad is either a "
                         "single-ended signal that is fine, or a net whose other end was "
                         "never placed — and those look identical here. Confirm against "
                         "the intended design which it is."),
                net_hint=name,
            ))
    return derived, blocked


def _adjacency_rows(board: Board, pitch_tolerance: float = 1.25,
                    max_pitch_mm: float = 3.0) -> list[ProbeRow]:
    """Adjacent-pad isolation, with adjacency DERIVED FROM GEOMETRY.

    Each footprint is measured against ITS OWN minimum pad spacing. There is no global
    "fine pitch" constant and no list of which parts are dense — a guess about which parts
    are fine-pitch is precisely the hand-authored claim this instrument replaces.
    """
    rows: list[ProbeRow] = []
    for fp in board.footprints:
        pitch = fp.min_pad_pitch
        if pitch is None or pitch > max_pitch_mm:
            continue
        limit = pitch * pitch_tolerance
        pads = fp.pads
        for i in range(len(pads)):
            for j in range(i + 1, len(pads)):
                a, b = pads[i], pads[j]
                d = a.rel_distance_to(b)
                if d <= 0 or d > limit:
                    continue
                if a.net_num is None or b.net_num is None:
                    continue
                if a.net_num == b.net_num:
                    continue  # same net: they are SUPPOSED to be connected
                rows.append(ProbeRow(
                    category=Category.ADJACENT_PAD,
                    state=RowState.DERIVED,
                    from_label=f"{_label(a)} (`{a.net_name}`)",
                    to_label=f"{_label(b)} (`{b.net_name}`)",
                    meter=MeterFunction.CONTINUITY,
                    expect="NO BEEP (open)",
                    why=(f"Neighbouring pads on `{fp.ref}` ({fp.lib.split(':')[-1]}), "
                         f"{d:.2f} mm apart — this footprint's own minimum pad spacing is "
                         f"{pitch:.2f} mm — carrying DIFFERENT nets "
                         f"(`{a.net_name}` vs `{b.net_name}`)."),
                    on_fail=("A beep is a SOLDER BRIDGE or an etch defect between two nets "
                             "that must never meet."),
                    net_hint=f"{fp.ref}",
                    span_mm=d,
                ))
    return rows


# Footprints that ENCODE AN ASSUMPTION the board file cannot verify. The pattern is
# matched against the footprint library name, which is stable, rather than against the
# Value string, which on these boards is inconsistent (`100uF_25V`, `100uF_16V`, `100uF`).
_ASSUMING_FOOTPRINTS: list[tuple[re.Pattern, str, str, str]] = [
    (
        re.compile(r"TO-92", re.I),
        MeterFunction.DIODE.value,
        "transistor pin order",
        "⛔ THE FOOTPRINT ASSUMES A PIN ORDER AND THE PART IN YOUR HAND MAY NOT MATCH IT. "
        "Not every TO-92 sold under a given part number has the same pinout: a 2N2222/"
        "PN2222A is commonly EBC flat-face-forward, but a BC337 is commonly CBE — the "
        "SAME footprint, the opposite end pins. DIODE-CHECK THE LOOSE PART BEFORE "
        "SOLDERING: red probe on a leg, black on each other in turn; the BASE is the one "
        "leg reading 0.5–0.8 V forward to BOTH others. With red on base, the HIGHER "
        "forward reading is the EMITTER. Confirm emitter<->collector is OL both ways.",
    ),
    (
        re.compile(r"\bCP_|CP_Radial|_Polarized", re.I),
        MeterFunction.RESISTANCE_2M.value,
        "electrolytic polarity",
        "⛔ POLARISED PART. Confirm the NEGATIVE STRIPE faces the pad on the ground/return "
        "net before soldering. A reversed electrolytic on a 12 V rail vents, and it is the "
        "most likely single cause of a rail-to-rail short in the block above.",
    ),
    (
        re.compile(r"D_DO-|Diode_THT", re.I),
        MeterFunction.DIODE.value,
        "diode orientation",
        "⛔ ORIENTATION MATTERS AND THE SILKSCREEN MAY BE DRAWN ROTATED. Read the CATHODE "
        "BAND on the part, not the outline. Forward should read 0.4–0.8 V one way and OL "
        "reversed. A reverse-protection diode fitted backwards means the board never "
        "powers up; fitted shorted means it never protects.",
    ),
    (
        re.compile(r"TO-220", re.I),
        MeterFunction.CONTINUITY.value,
        "regulator pinout",
        "⛔ CONFIRM THE PART'S PINOUT AGAINST THE FOOTPRINT. A linear regulator fitted "
        "backwards puts its input voltage onto the output rail through the substrate "
        "diode, destroying everything downstream. Note the TAB is usually tied to one of "
        "the pins — check what it touches.",
    ),
]


def _orientation_rows(board: Board) -> list[ProbeRow]:
    rows: list[ProbeRow] = []
    for fp in board.footprints:
        for pattern, meter, what, caution in _ASSUMING_FOOTPRINTS:
            if not pattern.search(fp.lib):
                continue
            pad_map = ", ".join(
                f"pad {p.number}->`{p.net_name}`" for p in fp.pads if p.net_name
            )
            rows.append(ProbeRow(
                category=Category.PART_ORIENTATION,
                state=RowState.UNVERIFIED,
                from_label=f"{_label(fp.pads[0]) if fp.pads else fp.ref} — {fp.ref}",
                to_label=f"loose part ({fp.value or 'unmarked'})",
                meter=MeterFunction(meter),
                expect="part pinout MATCHES the footprint's assumption",
                why=(f"`{fp.ref}` uses `{fp.lib.split(':')[-1]}`, which ENCODES a "
                     f"{what} assumption. The board file records which net each pad "
                     f"carries ({pad_map or 'no netted pads'}) but CANNOT know which "
                     "physical part you will fit. ⛔ UNVERIFIED BY CONSTRUCTION — this is "
                     "emitted, not omitted, because a silently absent check is "
                     "indistinguishable from one nobody thought of."),
                caution=caution,
                on_fail="Re-orient the part, or source one matching the footprint. "
                        "⛔ Record the deviation on the board and in this sheet — an "
                        "UNDOCUMENTED deviation is worse than either choice.",
                net_hint=fp.ref,
            ))
            break
    return rows


# --------------------------------------------------------------------------------------
# top level
# --------------------------------------------------------------------------------------


def derive_sheet(board: Board) -> Sheet:
    """Derive every probe row for ``board``. Pure — no I/O, no formatting."""
    rails, why = classify_rails(board)
    has_bulk_cap = any(
        re.search(r"\bCP_|CP_Radial", fp.lib, re.I) for fp in board.footprints
    )

    sheet = Sheet(board=board, rails=rails)
    sheet.rows.extend(_rail_isolation_rows(board, rails, why, has_bulk_cap))
    sheet.rows.extend(_orientation_rows(board))
    sheet.rows.extend(_adjacency_rows(board))
    cont, blocked = _continuity_rows(board)
    sheet.rows.extend(cont)
    sheet.rows.extend(blocked)

    # Notes that are board-wide facts a prober must hold, not per-row checks.
    hidden = sorted({fp.ref for fp in board.footprints if fp.ref_hidden_on_silk})
    if hidden:
        sheet.notes.append(
            f"⛔ {len(hidden)} footprint(s) carry a Reference marked `(hide yes)` — their "
            "designator is NOT printed on the silkscreen, so those probe points cannot be "
            "found by label. Locate them by position against the board render. Affected: "
            + ", ".join(hidden[:16]) + ("…" if len(hidden) > 16 else "")
        )
    if has_bulk_cap:
        sheet.notes.append(
            "This board's BOM includes bulk electrolytic(s) (`CP_` footprint). On a TRULY "
            "BARE board they are not fitted and the rails read hard-open; if this sheet is "
            "reused on a STUFFED board, an uncharged 100 µF beeps and the 2 MΩ settled "
            "reading is the only trustworthy one."
        )
    matched, total = board.geometry_coincidence()
    if total:
        sheet.notes.append(
            f"Geometry self-check: {matched}/{total} netted pads lie within 0.6 mm of a "
            f"copper endpoint on the same net ({100.0 * matched / total:.0f}%). This is an "
            "independent check on this tool's own pad-position arithmetic — copper "
            "endpoints are written by a different code path than pad positions, so "
            "agreement is evidence that does not share the parser's assumption."
        )
    return sheet
