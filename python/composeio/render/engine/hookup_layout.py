"""
Hookup board layout + orthogonal wire routing — the SHARED, render-target-
agnostic intermediate of the maker hookup guide ([FLOW:MAKER-PROJECT] S3).

ONE netlist spec (`Composition.hookup`, S2) -> ONE deterministic geometry that
EVERY hookup renderer consumes (define-once, the narrow waist):

  * the SVG exporter (`composeio.maker.hookup_svg`) — the portable artifact
    the doc-viewer opens;
  * the native `hookup` widget (`composeio.render.widgets.hookup`) — the
    in-app live guide drawn straight onto the Canvas contract.

The intermediate is pure data + pure functions — no clocks, no state, no
drawing:

    layout_hookup(hookup)   -> BoardLayout   part boxes + every pin's (x, y)
    wire_segments(hookup)   -> [WireSeg]     endpoints + routed orthogonal
                                             polylines + role/color
    label_anchor(points)    -> (x, y)        stable publish-label spot

so the same hookup yields byte-identical geometry in both renderers, and the
routing invariants (pin anchoring, >= PITCH lane separation, body avoidance)
are enforced ONCE.  The invariant documentation lives inline below; it moved
here VERBATIM from `composeio.maker.hookup_svg` (2026-07-26), which now
re-exports these names for backward compatibility.

Placement: a part with an `at:` [row, col] anchors on the stretched grid.  A
part WITHOUT one nestles NEXT TO its most-wired already-placed peer (the wired-
adjacency rule, David 2026-07-26: the speaker terminus belongs beside the
DY-SV5F that drives it, not flung down an arbitrary stack slot) — falling back
to the legacy vertical auto-stack only when it has no placed wired peer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from .hookup import Hookup, HookupPart, HookupWire
from .parts.footprint import PartFootprint, load_part_footprint

# ---------------------------------------------------------------------------
# geometry constants (SVG user units; 1 grid step == one 0.1" hole)
# ---------------------------------------------------------------------------
PITCH = 22.0            # px per 0.1" hole — readable on a doc-viewer window
HOLE_R = 2.6            # tie-point / perf hole radius
PAD_R = 3.4            # perfboard solder-pad radius
ORIGIN_X = 96.0         # left inset (room for the left power rail + pin labels)
ORIGIN_Y = 88.0         # top inset (room for the title bar + legend)
MARGIN = 44.0           # right/bottom board margin past the last feature
VERTICAL_PIN_THRESHOLD = 6   # parts with more pins are drawn as a vertical DIP

# part spacing -------------------------------------------------------------
# The fatter (>= PITCH) wire lanes below need room in the channels BETWEEN
# parts.  Each part keeps its LOGICAL grid position (relative order, row/column
# alignment) but the gap between adjacent grid columns/rows is stretched by
# these factors, so the fan of parallel lanes fits without overrunning a
# neighbour.  Only the anchor ORIGIN is stretched — a part's own internal pin
# pitch stays PITCH, so footprints keep true physical size.  (David 2026-07-26:
# fat lanes -> spread the parts, readability wins over compactness.)
COL_SPREAD = 1.9        # horizontal anchor-gap stretch (× PITCH between columns)
ROW_SPREAD = 1.6        # vertical anchor-gap stretch (× PITCH between rows)

# orthogonal (Manhattan) wire routing -------------------------------------
# Wires are routed as right-angle polylines: a short escape STUB out of each
# pin's part edge to clear the body, then a straight TRUNK on a dedicated LANE,
# then a stub into the far pin.  The endpoints never move — only the PATH between
# the two fixed pins changes — so the pin-anchoring contract is preserved.
#
# Lane separation invariant (David 2026-07-26): adjacent parallel runs must be
# >= ONE grid unit (PITCH) apart — the prior sub-grid lanes packed the DY-SV5F's
# five leads into the space between two holes and read as one smear.  The trunks
# therefore live on a GLOBAL grid of PITCH-spaced lanes: every trunk snaps to a
# grid line ``ORIGIN + k * PITCH``, and a lane already carrying an
# extent-overlapping run of a DIFFERENT net bumps the newcomer to the next free
# line.  So any two distinct-net parallel trunks are either the same line (never
# — collisions are bumped) or a full PITCH apart.  ``LANE_STEP`` IS that grid
# step.  The escape stub is SHORT + UNIFORM: the grid does the separating, so a
# rank-fanned stub would only make same-edge risers longer and MORE parallel
# (it fans length, not lateral offset) — exactly the DY-SV5F backfire.
#
# Body avoidance (David 2026-07-26): a wire must NEVER draw over a part body,
# except at its own endpoint pads (the escape out of / into its own pin).  Two
# mechanisms enforce it: (1) every escape leaves a pin far enough to fully exit
# its OWN body bbox (``_escape_point`` measures the distance to that edge and
# adds ``BODY_CLEAR``), so the escape point sits in open space; (2) the trunk is
# routed on a LANE chosen so its whole polyline clears every body — the placement
# + relaxation sweeps reject any lane whose run crosses a box.  When the two pins
# sit on OPPOSITE sides of overlapping stacked bodies (a single straight trunk
# can never clear them — e.g. the DY-SV5F's left-edge VCC to the ESP32-C3's
# right-edge 5V), the router falls back from a single mid-line TRUNK to a
# CORRIDOR: two drops on each part's clear side joined by a leg routed through the
# body-free gap between them.  ``BODY_CLEAR`` is the margin kept outside a box.
STUB_BASE = 15.0        # base escape length out of a pin's part edge
BODY_CLEAR = 13.0       # margin kept clear OUTSIDE a part body when escaping / routing
ESC_STEP = PITCH        # per-(part,edge) escape bump = ONE grid unit (fans same-edge JOGS apart)
LANE_STEP = PITCH       # trunk lane grid step = ONE grid unit (parallel trunks >= PITCH apart)
_LANE_SEARCH = 40       # max grid lanes to search outward when placing / relaxing a lane

# line hops ---------------------------------------------------------------
# Where two conductors CROSS, the horizontal one is drawn with a small bridge so
# the crossing reads as a crossing and not as a junction (David 2026-08-15: an
# un-hopped crossing is the same class of lie as a wire that isn't there).
# ``HOP_R`` is the bridge radius, pinned between two hard numbers:
#   UPPER — distinct nets are guaranteed >= PITCH apart (the lane invariant), so
#     a bridge rising HOP_R plus half a stroke (5.2 / 2) must stay inside
#     PITCH / 2 == 11 px or it would touch the very neighbour it exists to
#     distinguish itself from.  7.5 + 2.6 = 10.1 < 11.
#   LOWER — the arc must enclose visible white or it reads as a BUMP on the
#     wire rather than a bridge over it.  At r = 5.0 against a 4.8 px trace the
#     void is 0.2 px wide and the hop looked like a solder blob; at 7.5 the void
#     is ~10 px, unmistakable at doc-viewer zoom.
HOP_R = 7.5
_HOP_CORNER_CLEAR = 2.0   # a bridge must clear its run's own corners by this much

# role palette -------------------------------------------------------------
POWER_TOKENS = {"vcc", "3v3", "5v", "v+", "vin", "vbus", "vdd"}
GROUND_TOKENS = {"gnd", "agnd", "dgnd", "vss", "-"}
COLOR_POWER = "#D64027"        # red rail / lead
COLOR_GROUND = "#1F2733"       # near-black lead
COLOR_SIGNAL = ("#1F8A3B", "#8A5CF6", "#E0821E", "#0EA5A5", "#C2185B")
COLOR_RAIL_POS = "#D64027"
COLOR_RAIL_NEG = "#2F6FEB"

BOARD_BREADBOARD = "#EBDCA6"   # classic tan
BOARD_PERFBOARD = "#FFFFFF"    # white (David 2026-07-26: the brown backdrop distracted)
HOLE_FILL_BB = "#C9B67E"
INK = "#1A2230"
DIM = "#5B6675"
BODY_FILL = "#2C333F"
BODY_TEXT = "#EAF0F7"


def _pin_role(pin: str) -> str:
    p = pin.strip().lower()
    if p in POWER_TOKENS:
        return "power"
    if p in GROUND_TOKENS:
        return "ground"
    return "signal"


def _wire_role(w: HookupWire) -> str:
    """A wire is power/ground if EITHER endpoint is (potX.vcc -> mcu.3V3 is a
    power lead); otherwise it is a signal lead."""
    roles = {_pin_role(w.frm.pin), _pin_role(w.to.pin)}
    if "power" in roles:
        return "power"
    if "ground" in roles:
        return "ground"
    return "signal"


# ---------------------------------------------------------------------------
# layout model — pin coordinates + part boxes (shared by both substrates)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FootprintPlacement:
    """A real Fritzing footprint placed on the board: the loaded asset +
    the affine that maps its viewBox user space to board coordinates
    (board = (tx, ty) + svg_point * scale)."""
    fp: PartFootprint
    tx: float
    ty: float
    scale: float


@dataclass(frozen=True)
class PartBox:
    """A placed part's drawn footprint (body rect) + its pin coordinates.

    ``footprint`` is set when the part composites REAL Fritzing art; its
    ``pins`` are then the real connector-terminal centers in board coords
    (the wire-anchoring contract holds either way)."""
    id: str
    part: str
    orient: str                     # "horizontal" | "vertical" | "footprint"
    body: Tuple[float, float, float, float]       # x, y, w, h
    pins: Dict[str, Tuple[float, float]]          # pin name -> (x, y)
    footprint: Optional[FootprintPlacement] = None


@dataclass(frozen=True)
class BoardLayout:
    """The full geometry: every `<part>.<pin>` -> (x, y), plus part boxes and
    the computed board size.  Pure function of the hookup — no clocks, no
    state, so the same hookup always yields byte-identical geometry."""
    board: str
    width: float
    height: float
    parts: Dict[str, PartBox]
    pins: Dict[str, Tuple[float, float]]          # "part.pin" -> (x, y)

    def pin_xy(self, part: str, pin: str) -> Tuple[float, float]:
        return self.pins[f"{part}.{pin}"]


def _auto_anchor(index: int) -> Tuple[int, int]:
    """Fallback (row, col) for a part with no `at:` — a simple vertical stack
    with generous spacing so nothing overlaps."""
    return (1 + index * 4, 2)


def _shift_box(box: PartBox, dx: float, dy: float) -> PartBox:
    """Translate a placed part (body, pins, and footprint affine) rigidly."""
    bx, by, bw, bh = box.body
    fpp = box.footprint
    if fpp is not None:
        fpp = replace(fpp, tx=fpp.tx + dx, ty=fpp.ty + dy)
    return replace(
        box,
        body=(bx + dx, by + dy, bw, bh),
        pins={name: (round(x + dx, 2), round(y + dy, 2))
              for name, (x, y) in box.pins.items()},
        footprint=fpp)


def _place_part_footprint(hp: HookupPart, anchor: Tuple[int, int],
                          fp: PartFootprint) -> PartBox:
    """Place REAL Fritzing art: the art's viewBox bbox top-left lands on the
    grid anchor (predictable extent for the author), scaled so the SVG's
    declared physical size matches the board grid (0.1" == PITCH — Fritzing
    breadboard art is authored at true physical scale, so header pitches
    land exactly on grid).  Pin coordinates are the TRANSFORM-RESOLVED
    connector-terminal centers — the real pads wires must land on."""
    scale = (PITCH * 10.0) / fp.units_per_inch          # px per svg unit
    ax = ORIGIN_X + anchor[1] * PITCH * COL_SPREAD
    ay = ORIGIN_Y + anchor[0] * PITCH * ROW_SPREAD
    doc = fp.doc
    tx = ax - doc.vb_x * scale
    ty = ay - doc.vb_y * scale
    coords = {
        pin: (round(tx + x * scale, 2), round(ty + y * scale, 2))
        for pin, (x, y) in fp.terminals.items()
    }
    body = (ax, ay, doc.vb_w * scale, doc.vb_h * scale)
    return PartBox(id=hp.id, part=hp.part, orient="footprint",
                   body=body, pins=coords,
                   footprint=FootprintPlacement(fp=fp, tx=tx, ty=ty,
                                                scale=scale))


def _place_part(hp: HookupPart, anchor: Tuple[int, int]) -> PartBox:
    """Compute a part's body rect + per-pin coordinates.

    Real-footprint path: a part with cached Fritzing art + a complete
    connector map composites that art (see :func:`_place_part_footprint`).
    Generated fallback: many-pin parts (an MCU module) are drawn as a
    vertical DIP with pins down the left edge; few-pin parts (a pot) as a
    horizontal row with the body above.  The pin ORDER is the catalog order
    (the netlist speaks those)."""
    fp = load_part_footprint(hp.definition)
    if fp is not None:
        return _place_part_footprint(hp, anchor, fp)

    pins = list(hp.definition.pins) if hp.definition else []
    ax = ORIGIN_X + anchor[1] * PITCH * COL_SPREAD
    ay = ORIGIN_Y + anchor[0] * PITCH * ROW_SPREAD
    coords: Dict[str, Tuple[float, float]] = {}

    if len(pins) > VERTICAL_PIN_THRESHOLD:
        # vertical DIP: pins march DOWN the left edge; body to their right.
        for i, name in enumerate(pins):
            coords[name] = (ax, ay + i * PITCH)
        body_x = ax + PITCH * 0.62
        body_y = ay - PITCH * 0.55
        body_w = PITCH * 3.1
        body_h = (len(pins) - 1) * PITCH + PITCH * 1.1
        orient = "vertical"
    else:
        # horizontal row: pins march RIGHT; body sits above them.
        for i, name in enumerate(pins):
            coords[name] = (ax + i * PITCH, ay)
        body_x = ax - PITCH * 0.55
        body_y = ay - PITCH * 1.75
        body_w = (len(pins) - 1) * PITCH + PITCH * 1.1
        body_h = PITCH * 1.25
        orient = "horizontal"

    return PartBox(id=hp.id, part=hp.part, orient=orient,
                   body=(body_x, body_y, body_w, body_h), pins=coords)


def _part_extent(box: PartBox) -> Tuple[float, float, float, float]:
    """A part's FULL occupied bbox (x, y, w, h): the body union every pin
    coordinate (generated footprints hang pins outside the body rect)."""
    bx, by, bw, bh = box.body
    xs = [bx, bx + bw] + [p[0] for p in box.pins.values()]
    ys = [by, by + bh] + [p[1] for p in box.pins.values()]
    x0, y0 = min(xs), min(ys)
    return (x0, y0, max(xs) - x0, max(ys) - y0)


# The PUBLIC name.  ``_part_extent`` stays as the internal alias every routing
# helper below already calls; the legibility gate
# (``maker.hookup_svg.legibility_report``) needs the SAME extent the router
# avoids, because a gate that measures a different rectangle from the one the
# layout reasons about is a gate that can disagree with the drawing.
part_extent = _part_extent


def _extents_overlap(a: Tuple[float, float, float, float],
                     b: Tuple[float, float, float, float],
                     margin: float) -> bool:
    """Do two extents come closer than *margin* (the wire-channel clearance)?"""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + margin <= bx or bx + bw + margin <= ax
                or ay + ah + margin <= by or by + bh + margin <= ay)


# ===========================================================================
# ⛔ PIN-LABEL PLACEMENT — the DECISION lives here, the OFFSETS live in the
# renderers.  (David 2026-08-15: "the wiring is unreadable.")
#
# WHAT WENT WRONG.  Both renderers independently carried the same rule: a pad
# is `left` or `right` by which half of the art it sits in, and its label is
# then CLAMPED to the art's edge — ``min(px - 7, bx - 4)`` /
# ``max(px + 7, bx + bw + 4)``.  That rule is exactly right for a part whose
# pads form vertical COLUMNS (the ESP32-C3 SuperMini, the TM1637 module: every
# pad alone at its y, one label per y, and the drawing is clean).  It is a
# catastrophe for a part whose pads form horizontal ROWS: every pad in the row
# shares a y AND every one of them clamps to the SAME edge x, so the whole row
# prints on one spot.  Measured on `esp32-s3-supermini`: SIX labels
# (GPIO16/17/18/21/33/34) at one point, five more at another, and both edge
# columns of the delivered R503 guide were unreadable stacks.
#
# THE FIX IS A CLASSIFICATION, NOT A NUDGE.  Group the pads by (side, y).  A
# group of ONE is a genuine edge pad and keeps the clamped placement byte for
# byte — so every part that renders well today is untouched.  A group of MORE
# than one is a ROW, and a row's labels are turned 90 degrees and stood at each
# pad's OWN x: rotation trades the dimension that is scarce (horizontal, one
# pad pitch) for the one that is free (vertical, open board margin), which is
# why every real pinout drawing does it.
#
# ⚠ THE ROTATION TRIGGER IS A HEURISTIC AND IS NOT THE GATE.  A name of
# `ROTATE_MIN_NAME_CHARS` or fewer stays horizontal (`A`/`B` on a resistor: at
# the drawing's 7.5 px pin size the monospace advance is 4 px/char, so <= 2
# chars is <= 8 px and fits inside the tightest pad spacing any part in the
# catalog produces — 15.1 px, the S3's underside row).  Longer names rotate.
# If that estimate is ever wrong for a part nobody has drawn yet, the drawing
# does not silently ship: `maker.hookup_svg.legibility_report` MEASURES the
# rendered boxes through the engine's own font metrics and refuses.  The
# heuristic decides; the gate is the truth.
# ===========================================================================
ROTATE_MIN_NAME_CHARS = 2

# Placement kinds.  A renderer maps each to its own offsets (the SVG guide and
# the on-canvas widget draw at different scales), but never re-decides WHICH.
LABEL_INSIDE = "inside"          # inside the body slab (generated vertical part)
LABEL_BELOW = "below"            # under the pad, at the pad's own x
LABEL_EDGE_LEFT = "edge-left"    # clamped to the art's LEFT edge, at the pad's y
LABEL_EDGE_RIGHT = "edge-right"  # clamped to the art's RIGHT edge, at the pad's y
LABEL_ROT_ABOVE = "rot-above"    # turned 90 deg, standing UP off the art's top
LABEL_ROT_BELOW = "rot-below"    # turned 90 deg, hanging DOWN off the art's bottom


@dataclass(frozen=True)
class PinLabel:
    """WHERE one pin's name goes, decided once for every renderer.

    ``kind`` is one of the ``LABEL_*`` constants; ``x``/``y`` is the pad the
    label belongs to (renderers apply their own gap off the body edge, which
    is why no absolute label coordinate is stored here)."""
    pin: str
    kind: str
    x: float
    y: float


def _pad_side(box: PartBox, px: float, py: float) -> str:
    bx, by, bw, bh = box.body
    if py > by + bh - PITCH:
        return "bottom"
    return "left" if px < bx + bw / 2 else "right"


def pin_label_plan(box: PartBox,
                   wired: Optional[set] = None) -> Tuple[PinLabel, ...]:
    """Every pin label this part should draw, and WHERE — the one rule both
    hookup renderers consume.

    *wired* is the set of pin names the netlist actually connects.  When it is
    given, a pin outside it gets NO label: a pad nobody wires has nothing to
    say to a builder and it competes for the same space as the pads that do
    (David 2026-08-15).  The pad itself is still DRAWN — only the catalog-name
    overlay is suppressed — so a builder can still count positions along the
    header.  Pass ``None`` to label everything (the `label_pins: all` opt-in).

    Deterministic: pure function of the placed geometry, in catalog pin order.
    """
    bx, by, bw, bh = box.body
    names = [n for n in box.pins
             if wired is None or n in wired]

    if box.footprint is None and box.orient == "vertical":
        return tuple(PinLabel(n, LABEL_INSIDE, *box.pins[n]) for n in names)

    # group EVERY pad (not just the labelled ones) by (side, y): a row is a
    # property of the PART, so suppressing an unwired label cannot turn a row
    # into a column and quietly move the survivors.
    groups: Dict[Tuple[str, float], List[str]] = {}
    for n, (px, py) in box.pins.items():
        groups.setdefault((_pad_side(box, px, py), round(py, 1)), []).append(n)

    out: List[PinLabel] = []
    for n in names:
        px, py = box.pins[n]
        side = _pad_side(box, px, py)
        peers = groups[(side, round(py, 1))]
        row = len(peers) > 1 or (box.footprint is None
                                 and box.orient == "horizontal"
                                 and len(box.pins) > 1)
        long_name = max((len(p) for p in peers), default=0) > ROTATE_MIN_NAME_CHARS
        if row and long_name:
            # stand it off the NEARER horizontal edge, so it never crosses the art
            near_top = (py - by) <= (by + bh - py)
            out.append(PinLabel(n, LABEL_ROT_ABOVE if near_top
                                else LABEL_ROT_BELOW, px, py))
        elif side == "bottom" or (box.footprint is None and row):
            out.append(PinLabel(n, LABEL_BELOW, px, py))
        elif side == "left":
            out.append(PinLabel(n, LABEL_EDGE_LEFT, px, py))
        else:
            out.append(PinLabel(n, LABEL_EDGE_RIGHT, px, py))
    return tuple(out)


def wired_pins(hookup: Hookup, part_id: str) -> set:
    """The pin names *part_id* actually has a wire on."""
    out = set()
    for w in hookup.wires:
        for end in (w.frm, w.to):
            if end.part == part_id:
                out.add(end.pin)
    return out


def _wired_peer(hookup: Hookup, part_id: str,
                placed: Dict[str, PartBox]) -> Optional[str]:
    """The already-placed part *part_id* shares the MOST wires with (its
    electrical neighbour — where an unanchored part belongs).  Deterministic:
    counts accumulate in wire order; ties keep the first peer seen."""
    counts: Dict[str, int] = {}
    order: List[str] = []
    for w in hookup.wires:
        ends = {w.frm.part, w.to.part}
        if part_id not in ends:
            continue
        other = (ends - {part_id}).pop() if len(ends) == 2 else part_id
        if other == part_id or other not in placed:
            continue
        if other not in counts:
            counts[other] = 0
            order.append(other)
        counts[other] += 1
    if not order:
        return None
    best = max(counts[p] for p in order)
    for p in order:
        if counts[p] == best:
            return p
    return None


# wired-adjacency placement: the gap kept between an unanchored part and its
# peer (room for two escape jogs + a PITCH-separated lane between the bodies)
# and the clearance margin no other part may intrude into.
_NESTLE_GAP = 3 * PITCH
_NESTLE_MARGIN = PITCH

# ⛔ THE CLEARANCE RULE ALREADY EXISTED — IT WAS JUST NEVER APPLIED TO
# `at:`-ANCHORED PARTS.  ``_nestle_box`` rejects any slot whose extent comes
# within ``_NESTLE_MARGIN`` of a placed part, so an UNANCHORED part can never
# land on a neighbour.  ``layout_hookup`` places an ANCHORED part straight onto
# the stretched grid with NO check at all — which is how `mcu at [22, 2]` and
# `fp at [22, 3]` shipped with their extents EXACTLY TANGENT (both y-bands meet
# at 862.4, clearance 0.0) and every strict interior-overlap test in the repo
# correctly answered "no overlap" about a drawing where one part is printed on
# the other.  ⚠ THAT IS THE WHOLE DEFECT: not a wrong comparison, an
# UNAPPLIED one, on a predicate that needs a MARGIN and was asked for an
# INTERSECTION.  ``PART_CLEARANCE`` is that same margin under a public name so
# the legibility gate can hold anchored parts to the rule the router already
# holds nestled ones to.
PART_CLEARANCE = _NESTLE_MARGIN


def _nestle_box(box: PartBox, peer: PartBox,
                obstacles: List[PartBox]) -> PartBox:
    """Shift *box* (placed at the origin anchor) to the first clear spot NEXT
    TO *peer*: candidate slots sweep right of the peer top-to-bottom, then
    below it left-to-right, then left of it — deterministic order, so the
    same hookup always nestles identically.  A slot is clear when the part's
    full extent keeps ``_NESTLE_MARGIN`` from every placed part."""
    ex, ey, ew, eh = _part_extent(box)
    px, py, pw, ph = _part_extent(peer)
    others = [_part_extent(o) for o in obstacles]

    slots: List[Tuple[float, float]] = []
    for j in range(0, 13):                       # right of the peer
        slots.append((px + pw + _NESTLE_GAP, py + j * PITCH))
    for j in range(0, 13):                       # below the peer
        slots.append((px + j * PITCH, py + ph + _NESTLE_GAP))
    for j in range(0, 13):                       # left of the peer
        slots.append((px - _NESTLE_GAP - ew, py + j * PITCH))

    for (sx, sy) in slots:
        cand = (sx, sy, ew, eh)
        if not any(_extents_overlap(cand, o, _NESTLE_MARGIN) for o in others):
            dx = round(sx - ex, 2)
            dy = round(sy - ey, 2)
            return _shift_box(box, dx, dy)
    # defensive: every slot occupied -> far right of everything (still finite)
    far_x = max(o[0] + o[2] for o in others) + _NESTLE_GAP
    return _shift_box(box, round(far_x - ex, 2), round(py - ey, 2))


def layout_hookup(hookup: Hookup) -> BoardLayout:
    """Resolve every part placement + pin coordinate and the board size.

    Two placement passes: parts WITH an `at:` anchor land on the stretched
    grid first; parts WITHOUT one then nestle next to their most-wired placed
    peer (``_nestle_box`` — the wired-adjacency rule), falling back to the
    legacy vertical auto-stack when no placed wired peer exists.

    Deterministic: identical hookup -> identical geometry.  Both substrate
    renderers and the tests read THIS (the wire-to-pin contract)."""
    parts: Dict[str, PartBox] = {}
    pins: Dict[str, Tuple[float, float]] = {}
    boxes: List[PartBox] = []
    by_id: Dict[str, PartBox] = {}
    pending: List[Tuple[int, HookupPart]] = []
    for idx, hp in enumerate(hookup.parts):
        if hp.at is not None:
            box = _place_part(hp, hp.at)
            boxes.append(box)
            by_id[hp.id] = box
        else:
            pending.append((idx, hp))
    for idx, hp in pending:
        peer = _wired_peer(hookup, hp.id, by_id)
        if peer is not None:
            box = _nestle_box(_place_part(hp, (0, 0)), by_id[peer], boxes)
        else:
            box = _place_part(hp, _auto_anchor(idx))
        boxes.append(box)
        by_id[hp.id] = box

    # Safety shift: real footprint art can extend above/left of its anchor's
    # neighborhood (a tall pot near row 0).  If any body pokes past the board
    # slab, shift EVERYTHING down/right by a PITCH-snapped delta — relative
    # geometry (and therefore the wire-to-pin contract) is unchanged.
    top_limit = ORIGIN_Y - 30.0
    left_limit = ORIGIN_X - 40.0
    min_x = min([b.body[0] for b in boxes]
                + [xy[0] for b in boxes for xy in b.pins.values()]
                + [left_limit])
    min_y = min([b.body[1] for b in boxes]
                + [xy[1] for b in boxes for xy in b.pins.values()]
                + [top_limit])
    dx = (math.ceil((left_limit - min_x) / PITCH) * PITCH
          if min_x < left_limit else 0.0)
    dy = (math.ceil((top_limit - min_y) / PITCH) * PITCH
          if min_y < top_limit else 0.0)
    if dx or dy:
        boxes = [_shift_box(b, dx, dy) for b in boxes]

    for box in boxes:
        parts[box.id] = box
        for pin_name, xy in box.pins.items():
            pins[f"{box.id}.{pin_name}"] = xy

    # board size = furthest feature + margin (also account for pin labels)
    max_x = ORIGIN_X + PITCH
    max_y = ORIGIN_Y + PITCH
    for box in parts.values():
        bx, by, bw, bh = box.body
        max_x = max(max_x, bx + bw)
        max_y = max(max_y, by + bh)
        for (px, py) in box.pins.values():
            max_x = max(max_x, px)
            max_y = max(max_y, py)
    width = round(max_x + MARGIN, 2)
    height = round(max_y + MARGIN, 2)
    return BoardLayout(board=hookup.board, width=width, height=height,
                       parts=parts, pins=pins)


# ---------------------------------------------------------------------------
# wire model — resolved endpoints (the netlist -> geometry contract)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WireSeg:
    """One netlist edge with both endpoints resolved to board coordinates.

    ``frm_xy``/``to_xy`` are the REAL pin terminals the wire must land on (the
    tested pin-anchoring contract).  ``points`` is the ORTHOGONAL polyline drawn
    between them — ``points[0] == frm_xy`` and ``points[-1] == to_xy`` always,
    so the routing only ever changes the PATH, never the endpoints."""
    frm: str                       # "<part>.<pin>"
    to: str                        # "<part>.<pin>"
    frm_xy: Tuple[float, float]
    to_xy: Tuple[float, float]
    role: str                      # power | ground | signal
    color: str
    publishes: Optional[str] = None
    points: Tuple[Tuple[float, float], ...] = ()


# ---------------------------------------------------------------------------
# orthogonal router — right-angle polylines with per-net lane separation
# ---------------------------------------------------------------------------
def _escape_dir(box: PartBox, xy: Tuple[float, float]) -> Tuple[float, float]:
    """Outward unit normal of the part-body edge the pin sits on — the
    direction a lead should leave the pin to clear the body.  Ties break in
    left/right/top/bottom order (deterministic)."""
    bx, by, bw, bh = box.body
    px, py = xy
    dl, dr = px - bx, (bx + bw) - px
    dt, db = py - by, (by + bh) - py
    m = min(dl, dr, dt, db)
    if m == dl:
        return (-1.0, 0.0)
    if m == dr:
        return (1.0, 0.0)
    if m == dt:
        return (0.0, -1.0)
    return (0.0, 1.0)


def _clean_polyline(pts: List[Tuple[float, float]]
                    ) -> Tuple[Tuple[float, float], ...]:
    """Drop duplicate vertices and COLLAPSE colinear runs, preserving the exact
    first/last points (the pin terminals — the anchoring contract).

    Three colinear vertices are always reduced to two, whether the middle one
    lies between its neighbours (a plain monotonic run) or OUTSIDE them (a
    reversal — a lead that escapes past the trunk lane it then turns onto).

    The reversal case is DEFECT 1 (David 2026-08-15).  It used to be preserved
    deliberately, on the reasoning that the stored polyline should be an honest
    record of what is drawn.  But what was drawn was wrong: a stroked, round-
    capped stub overshooting the corner and terminating in open space — on the
    TM1637 quad panel, ``M 619.6 158.94 L 540.6 158.94 L 558.0 158.94 …`` put a
    17-px green tail past the corner of the shared-CLK chain, and a wire end
    that lands on nothing is read as a connection point that does not exist.
    Twenty of that panel's thirty wires carried one.

    Collapsing here — rather than at draw time — keeps the honesty property
    intact, and strengthens it: ``_topo`` runs every candidate route through
    this function, so the router's body-clearance and lane-clash checks
    evaluate EXACTLY the geometry that will be drawn.  The collapse is safe by
    construction because it only ever SHRINKS the drawn point set (the merged
    ``a -> c`` is a subset of ``a -> b -> c``), so it can never introduce a body
    crossing or a lane clash that the checks would have caught."""
    first, last = pts[0], pts[-1]
    out: List[Tuple[float, float]] = []
    for p in pts:
        if out and abs(p[0] - out[-1][0]) < 1e-6 and abs(p[1] - out[-1][1]) < 1e-6:
            continue
        out.append(p)
    merged: List[Tuple[float, float]] = []
    for p in out:
        while len(merged) >= 2:
            a, b = merged[-2], merged[-1]
            cross = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
            if abs(cross) >= 1e-6:
                break
            # colinear: b is redundant either way — monotonic (b between a and
            # c) or a reversal (the overshoot spur).  Drop it and re-test, so a
            # run of colinear vertices folds to one straight piece.
            merged.pop()
            if (abs(merged[-1][0] - p[0]) < 1e-6
                    and abs(merged[-1][1] - p[1]) < 1e-6):
                p = None                       # full retrace — nothing to add
                break
        if p is not None:
            merged.append(p)
    if len(merged) < 2:
        merged = [first, last]
    merged[0], merged[-1] = first, last
    return tuple(merged)


def _trunk_axis(a: Tuple[float, float], b: Tuple[float, float]) -> str:
    """Which way the wire's ONE long leg runs.  The trunk follows the DOMINANT
    separation: parts stacked (dy > dx) -> a VERTICAL trunk carries the long
    vertical travel; side-by-side (dx >= dy) -> a HORIZONTAL trunk.  Putting the
    long leg on the trunk (not the escapes) is what lets the grid separate the
    long parallel runs — otherwise many escapes off one edge pile up."""
    return "H" if abs(a[0] - b[0]) >= abs(a[1] - b[1]) else "V"


def _escape_point(box: PartBox, xy: Tuple[float, float], extra: float
                  ) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """The point a lead reaches after escaping its pin's nearest body edge FAR
    ENOUGH to fully clear the body bbox: the distance to that edge + BODY_CLEAR
    (so the escape point sits outside the body) + a per-(part,edge) rank bump
    (``extra``) that fans same-edge escapes onto distinct offsets.  Returns
    ``(escape_point, outward_dir)``."""
    d = _escape_dir(box, xy)
    bx, by, bw, bh = box.body
    px, py = xy
    if d[0] < 0:
        dist = px - bx
    elif d[0] > 0:
        dist = (bx + bw) - px
    elif d[1] < 0:
        dist = py - by
    else:
        dist = (by + bh) - py
    length = max(STUB_BASE, dist + BODY_CLEAR) + extra
    return ((round(px + d[0] * length, 2), round(py + d[1] * length, 2)), d)


def _seg_rect_cross(a: Tuple[float, float], b: Tuple[float, float],
                    rect: Tuple[float, float, float, float],
                    eps: float = 1e-6) -> bool:
    """Does the axis-aligned segment ``a-b`` pass through the INTERIOR of ``rect``
    (x, y, w, h)?  Touching an edge (co-linear with a side, or ending on the
    boundary) does not count — only a real interior overlap."""
    rx, ry, rw, rh = rect
    x1, y1 = a
    x2, y2 = b
    if abs(y1 - y2) < eps:                              # horizontal
        y = y1
        if not (ry + eps < y < ry + rh - eps):
            return False
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return min(hi, rx + rw) - max(lo, rx) > eps
    if abs(x1 - x2) < eps:                              # vertical
        x = x1
        if not (rx + eps < x < rx + rw - eps):
            return False
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return min(hi, ry + rh) - max(lo, ry) > eps
    return False


def _seg_rect_overlap(a: Tuple[float, float], b: Tuple[float, float],
                      rect: Tuple[float, float, float, float],
                      eps: float = 1e-6) -> float:
    """How far the axis-aligned segment ``a-b`` runs through ``rect``'s interior
    (0.0 when it only touches an edge or misses)."""
    rx, ry, rw, rh = rect
    (x1, y1), (x2, y2) = a, b
    if abs(y1 - y2) < eps and abs(x1 - x2) > eps:               # horizontal
        if not (ry + eps < y1 < ry + rh - eps):
            return 0.0
        lo, hi = (x1, x2) if x1 <= x2 else (x2, x1)
        return max(0.0, min(hi, rx + rw) - max(lo, rx))
    if abs(x1 - x2) < eps and abs(y1 - y2) > eps:               # vertical
        if not (rx + eps < x1 < rx + rw - eps):
            return 0.0
        lo, hi = (y1, y2) if y1 <= y2 else (y2, y1)
        return max(0.0, min(hi, ry + rh) - max(lo, ry))
    return 0.0


def _exit_budget(rect: Tuple[float, float, float, float],
                 pin: Tuple[float, float]) -> float:
    """How much of its OWN body a lead may legitimately cover leaving *pin*.

    The endpoint-pad exception exists so a lead can get off a pad that sits on
    (or inside) its part's art — the pin-to-nearest-edge exit.  It is NOT a
    licence to traverse the part: an unbounded exception let a candidate route
    run 144 px straight across the ESP32-C3's body and still be accepted,
    because the route's second-to-last vertex was technically outside the box
    (David 2026-08-15 — the reversal collapse exposed it by making that route
    reachable).  The budget is the distance from the pin to the nearest body
    edge; a pin outside the body gets none."""
    bx, by, bw, bh = rect
    px, py = pin
    if not (bx < px < bx + bw and by < py < by + bh):
        return 0.0
    return min(px - bx, (bx + bw) - px, py - by, (by + bh) - py)


def _polyline_body_clear(pts: Tuple[Tuple[float, float], ...],
                         own_a: str, own_b: str,
                         rects: List[Tuple[str, Tuple[float, float, float, float]]]
                         ) -> bool:
    """A polyline is body-clear when NO segment crosses a part body — EXCEPT the
    first segment (the escape out of pin a) may cover its OWN body up to that
    pin's :func:`_exit_budget`, and the last segment (into pin b) likewise.

    The exemption is BOUNDED by the exit budget, so it can only ever cover the
    pin-to-edge exit it was written for; a lead that would enter a part from the
    far side and run across it is rejected, whichever end it belongs to."""
    return _polyline_body_excess(pts, own_a, own_b, rects) <= 1e-6


def _polyline_body_excess(pts: Tuple[Tuple[float, float], ...],
                          own_a: str, own_b: str,
                          rects: List[Tuple[str, Tuple[float, float, float, float]]]
                          ) -> float:
    """Total length by which a polyline covers part bodies BEYOND what the
    endpoint exits allow.  ``0.0`` means body-clear; larger is worse.

    Scored rather than boolean so the router's last resort can pick the LEAST
    bad route when no fully clear one exists, instead of taking whichever
    candidate happened to be first (David 2026-08-15)."""
    n = len(pts)
    by_id = dict(rects)
    excess = 0.0
    for k in range(n - 1):
        a, b = pts[k], pts[k + 1]
        first, last = (k == 0), (k == n - 2)
        for pid, rect in rects:
            ov = _seg_rect_overlap(a, b, rect)
            if ov <= 1e-6:
                continue
            budget = 0.0
            if first and pid == own_a:
                budget = _exit_budget(by_id[own_a], pts[0])
            elif last and pid == own_b:
                budget = _exit_budget(by_id[own_b], pts[-1])
            if ov > budget + 1e-6:
                excess += ov - budget
    return excess


def _topo(a, a2, b2, b, lane: float, mode: str
          ) -> Tuple[Tuple[float, float], ...]:
    """Build a candidate orthogonal polyline between the two escaped points.

    Modes (endpoints ``a``/``b`` copied through verbatim — the anchoring
    contract):

      * ``vtrunk`` — one VERTICAL trunk at ``x=lane``; horizontal legs at the two
        pin levels (good when the pins face a shared vertical channel).
      * ``htrunk`` — one HORIZONTAL corridor at ``y=lane``; vertical drops down
        each escape's x (the body-free-gap detour for stacked / opposite-side
        pins).
      * ``Lab`` / ``Lba`` — a single-bend L (mixed escape directions)."""
    a2x, a2y = a2
    b2x, b2y = b2
    if mode == "vtrunk":
        cx = round(lane, 2)
        mids = [(cx, a2y), (cx, b2y)]
    elif mode == "htrunk":
        cy = round(lane, 2)
        mids = [(a2x, cy), (b2x, cy)]
    elif mode == "Lab":
        mids = [(round(b2x, 2), a2y)]
    else:                                               # "Lba"
        mids = [(a2x, round(b2y, 2))]
    return _clean_polyline([a, a2, *mids, b2, b])


def _oriented_segments(pts: Tuple[Tuple[float, float], ...]
                       ) -> List[Tuple[str, float, float, float]]:
    """Break a polyline into axis-aligned pieces as ``(orient, fixed, lo, hi)``:
    an ``H`` piece has a fixed y and spans x in [lo, hi]; a ``V`` piece a fixed x
    spanning y.  Zero-length pieces are dropped (they cannot run parallel)."""
    out: List[Tuple[str, float, float, float]] = []
    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
        if abs(ay - by) < 1e-6 and abs(ax - bx) > 1e-6:
            out.append(("H", ay, min(ax, bx), max(ax, bx)))
        elif abs(ax - bx) < 1e-6 and abs(ay - by) > 1e-6:
            out.append(("V", ax, min(ay, by), max(ay, by)))
    return out


def _pieces_clash(p: Tuple[str, float, float, float],
                  q: Tuple[str, float, float, float]) -> bool:
    """Two axis-aligned pieces run PARALLEL closer than one grid unit: same
    orientation, perpendicular gap < PITCH, and overlapping extent along the run.
    (Perpendicular crossings are fine — only parallel proximity is a smear.)"""
    if p[0] != q[0]:
        return False
    if abs(p[1] - q[1]) >= LANE_STEP - 1e-6:
        return False
    return min(p[3], q[3]) - max(p[2], q[2]) > 1e-6


def _snap(v: float, origin: float) -> float:
    """Snap a coordinate to the nearest global PITCH-lane ``origin + k·PITCH``."""
    return origin + round((v - origin) / LANE_STEP) * LANE_STEP


def _assign_routes(lay: BoardLayout, segs: List[WireSeg]) -> List[WireSeg]:
    """Give every wire an orthogonal :attr:`WireSeg.points` polyline that (a)
    never draws over a part's OCCUPIED EXTENT (slab + pads + the pin band
    between them) except at its own endpoint pads, and (b) never runs parallel
    to a DISTINCT net closer than one grid unit (PITCH).

    Three cooperating mechanisms:

      * **escape-to-clear + rank** (per (part, edge)) — every lead leaves its pin
        far enough to fully exit its OWN body bbox (:func:`_escape_point`), and
        same-edge escapes are rank-staggered (step :data:`ESC_STEP` = PITCH) so
        their jogs land on distinct grid lines instead of overlaying;
      * **body-clear lane placement** — each wire picks a route MODE (a single
        mid-line ``vtrunk``/``htrunk``, or a mixed-direction ``L``) and, for the
        swept-trunk modes, the nearest LANE snapped to ``origin + k·PITCH`` whose
        WHOLE polyline clears every body (obstacle-aware routing — the opposite-
        side stacked pins fall back to the ``htrunk`` corridor through the gap);
      * **relaxation** — a sweep then bumps any wire whose polyline still runs
        parallel-close to a DIFFERENT net onto the nearest lane that is BOTH
        clash-free AND body-clear, until the layout is clean or stable.

    Nets that share a pin are exempt from the parallel check — converging at a
    common node is correct.  Pure function of the layout -> byte-identical
    geometry for one hookup (fixed iteration order + deterministic search).

    ⛔ THE OBSTACLE IS ``_part_extent``, NOT ``body`` — CHANGED 2026-08-16, AND
    THE ONE-RECTANGLE GAP IS THE WHOLE DEFECT.  David, on the second
    machine-green delivery: *"see how the FP component and its wires overlay the
    gnd for tm2 and tm4."*  He was right, and every gate was green: a GENERATED
    horizontal part draws its ``body`` slab ABOVE its pin row, so the band
    holding the PADS, the pin stubs and every pin caption sits OUTSIDE ``body``
    and inside ``_part_extent``.  Four conductors — ``mcu.GND -> tm2.GND``,
    ``mcu.GND -> tm4.GND`` and both 5 V runs — rode one lane at y=858.0 straight
    through that band, 4.4 px above the R503's six pads, for the full 134.2 px
    width of the part.  The router was TRUTHFUL about the slab and BLIND to the
    band, which is the same shape as the tangent-body defect before it: not a
    wrong comparison, a comparison against the wrong rectangle.

    ⛔ AND A CONDUCTOR UNDER A PART IS NOT A CROSSING.  :func:`plan_hops`
    bridges wire x wire so a crossing READS as a crossing; a part body cannot be
    hopped and cannot be seen through, so the reader loses the conductor for the
    width of the part — on a wiring guide, exactly the question the drawing
    exists to answer.  ``maker.hookup_svg.legibility_report``'s ``body_wire``
    class REFUSES it, and this is the rule that lets a layout satisfy that
    refusal instead of merely being told about it: the router and the gate now
    measure the SAME rectangle, which is what makes the gate's docstring claim
    ("the same rectangle the router avoids") true rather than aspirational.

    ⚠ THE ENDPOINT EXEMPTION STILL WORKS, and it is worth saying why it does not
    need widening.  A generated part's pads sit ON the extent boundary, so
    :func:`_exit_budget` gives them ZERO — but a lead approaching from OUTSIDE
    ends ON the boundary and :func:`_seg_rect_overlap` is strict-interior, so it
    scores 0.0 and is accepted.  What the change actually forbids is approaching
    a pad from INSIDE the part's own pin band, which is precisely the trunk that
    shipped.  Measured: r503 panel 117 -> 118 crossings, 0 skipped either way, 4
    of 5 ``body_wire`` rows gone; dual_throttle and the C3 panel unchanged."""
    rects: List[Tuple[str, Tuple[float, float, float, float]]] = [
        (pid, _part_extent(box)) for pid, box in lay.parts.items()]

    # --- per-wire escapes (rank-staggered per shared body edge) ------------
    edge_seen: Dict[Tuple[str, Tuple[float, float]], int] = {}
    esc_a: Dict[int, Tuple[float, float]] = {}
    esc_b: Dict[int, Tuple[float, float]] = {}
    dir_a: Dict[int, Tuple[float, float]] = {}
    dir_b: Dict[int, Tuple[float, float]] = {}
    own_a: Dict[int, str] = {}
    own_b: Dict[int, str] = {}
    for i, seg in enumerate(segs):
        pid_a = seg.frm.split(".", 1)[0]
        pid_b = seg.to.split(".", 1)[0]
        own_a[i], own_b[i] = pid_a, pid_b
        ea = _escape_dir(lay.parts[pid_a], seg.frm_xy)
        eb = _escape_dir(lay.parts[pid_b], seg.to_xy)
        ka, kb = (pid_a, ea), (pid_b, eb)
        ra = edge_seen.get(ka, 0); edge_seen[ka] = ra + 1
        rb = edge_seen.get(kb, 0); edge_seen[kb] = rb + 1
        a2, da = _escape_point(lay.parts[pid_a], seg.frm_xy, ra * ESC_STEP)
        b2, db = _escape_point(lay.parts[pid_b], seg.to_xy, rb * ESC_STEP)
        esc_a[i], esc_b[i], dir_a[i], dir_b[i] = a2, b2, da, db

    def build(i: int, mode: str, lane: float) -> Tuple[Tuple[float, float], ...]:
        return _topo(segs[i].frm_xy, esc_a[i], esc_b[i], segs[i].to_xy,
                     lane, mode)

    def _outside(box: PartBox, pt: Tuple[float, float]) -> bool:
        bx, by, bw, bh = box.body
        return (pt[0] <= bx + 1e-6 or pt[0] >= bx + bw - 1e-6
                or pt[1] <= by + 1e-6 or pt[1] >= by + bh - 1e-6)

    def route_ok(i: int, pts) -> bool:
        """A candidate route is acceptable when it clears every body AND its
        two escape vertices sit OUTSIDE their own part body.

        The second condition used to hold by accident: the escape point was
        always outside, and the reversal it caused was simply drawn.  Now that
        reversals collapse (:func:`_clean_polyline`), the corner can fall
        WHERE the escape used to be — so the invariant becomes an explicit
        acceptance test and the lane sweep is what satisfies it.  Without it,
        the endpoint-pad exemption in :func:`_polyline_body_clear` would let a
        lead run along the inside of its own part body."""
        if not _polyline_body_clear(pts, own_a[i], own_b[i], rects):
            return False
        if len(pts) < 3:
            return True
        return (_outside(lay.parts[own_a[i]], pts[1])
                and _outside(lay.parts[own_b[i]], pts[-2]))

    def body_clear(i: int, pts) -> bool:
        return route_ok(i, pts)

    # candidate MODE order + nominal lane per wire.  The LONG leg must ride the
    # SWEPT trunk (so distinct nets' long parallel runs separate by >= PITCH via
    # relaxation): dominant-vertical separation -> a ``vtrunk`` (swept x) whose
    # long leg is vertical; dominant-horizontal -> an ``htrunk`` corridor (swept y).
    # The OTHER axis is the body-avoidance fallback — when a straight trunk can't
    # clear overlapping bodies (opposite-side stacked pins), the corridor detours
    # the long leg through the body-free gap between the parts.
    def candidates(i: int) -> List[Tuple[str, float]]:
        a2, b2 = esc_a[i], esc_b[i]
        mid_x = _snap((a2[0] + b2[0]) / 2.0, ORIGIN_X)
        mid_y = _snap((a2[1] + b2[1]) / 2.0, ORIGIN_Y)
        if _trunk_axis(segs[i].frm_xy, segs[i].to_xy) == "V":
            return [("vtrunk", mid_x), ("htrunk", mid_y)]
        return [("htrunk", mid_y), ("vtrunk", mid_x)]

    # --- body-clear placement: first mode with a body-clear lane -----------
    mode_of: Dict[int, str] = {}
    lane_of: Dict[int, float] = {}
    swept_of: Dict[int, bool] = {}
    for i in range(len(segs)):
        placed = False
        for mode, nominal in candidates(i):
            swept = mode in ("vtrunk", "htrunk")
            origin = ORIGIN_X if mode == "vtrunk" else ORIGIN_Y
            if not swept:
                if body_clear(i, build(i, mode, nominal)):
                    mode_of[i], lane_of[i], swept_of[i] = mode, nominal, False
                    placed = True
                    break
                continue
            # sweep grid lanes outward from the nominal for a body-clear one
            for step in range(0, _LANE_SEARCH + 1):
                for cand in ((nominal,) if step == 0
                             else (nominal + step * LANE_STEP,
                                   nominal - step * LANE_STEP)):
                    if body_clear(i, build(i, mode, cand)):
                        mode_of[i], lane_of[i], swept_of[i] = mode, cand, True
                        placed = True
                        break
                if placed:
                    break
            if placed:
                break
        if not placed:
            # Last resort: no lane of either mode is fully clear (overlapping
            # bodies with no free channel between them).  Take the LEAST BAD
            # route — least body coverage, then nearest the nominal lane, then
            # candidate order — rather than whichever candidate came first,
            # which is how a lead ended up drawn straight across a part
            # (David 2026-08-15).  Deterministic: total order on the score.
            scored = []
            for ci, (mode, nominal) in enumerate(candidates(i)):
                for step in range(0, _LANE_SEARCH + 1):
                    for cand in ((nominal,) if step == 0
                                 else (nominal + step * LANE_STEP,
                                       nominal - step * LANE_STEP)):
                        pts = build(i, mode, cand)
                        pen = _polyline_body_excess(pts, own_a[i], own_b[i],
                                                    rects)
                        if len(pts) >= 3:
                            if not _outside(lay.parts[own_a[i]], pts[1]):
                                pen += PITCH
                            if not _outside(lay.parts[own_b[i]], pts[-2]):
                                pen += PITCH
                        scored.append((round(pen, 3), step, ci, cand, mode))
            scored.sort()
            _, _, _, lane, mode = scored[0]
            mode_of[i], lane_of[i] = mode, lane
            swept_of[i] = mode in ("vtrunk", "htrunk")

    def endpoints(i: int):
        return {segs[i].frm, segs[i].to}

    pieces_of: Dict[int, List[Tuple[str, float, float, float]]] = {
        i: _oriented_segments(build(i, mode_of[i], lane_of[i]))
        for i in range(len(segs))}

    def clashes(i: int, mine) -> bool:
        for j in range(len(segs)):
            if j == i or (endpoints(i) & endpoints(j)):
                continue
            for p in mine:
                for q in pieces_of[j]:
                    if _pieces_clash(p, q):
                        return True
        return False

    # --- relaxation: bump clashing swept wires onto a clash-free AND body-clear
    #     lane; wires already both-clean (or on a fixed L) stay put.
    #
    # The search covers the current mode's lanes FIRST (nearest-lane bump, the
    # smallest change) and then the OTHER trunk axis (David 2026-08-15).  The
    # cross-axis fallback exists because a lane bump cannot always help: the
    # dual-throttle DY-SV5F leads have a leg pinned at the mcu's pin level, so
    # EVERY lane of their vertical trunk runs that leg parallel-close to another
    # lead and only the horizontal corridor separates them.  Before reversal
    # collapse the router reached the corridor by accident — the overshooting
    # vtrunk candidate crossed a body and was rejected — so the fallback makes
    # explicit what used to depend on a route being drawn wrong.
    def _relax_candidates(i: int):
        seen = set()
        for step in range(1, _LANE_SEARCH + 1):
            for cand in (lane_of[i] + step * LANE_STEP,
                         lane_of[i] - step * LANE_STEP):
                if (mode_of[i], cand) not in seen:
                    seen.add((mode_of[i], cand))
                    yield (mode_of[i], cand)
        for mode, nominal in candidates(i):
            if mode == mode_of[i]:
                continue
            for step in range(0, _LANE_SEARCH + 1):
                for cand in ((nominal,) if step == 0
                             else (nominal + step * LANE_STEP,
                                   nominal - step * LANE_STEP)):
                    if (mode, cand) not in seen:
                        seen.add((mode, cand))
                        yield (mode, cand)

    for _ in range(len(segs) * 4):
        changed = False
        for i in range(len(segs)):
            if not swept_of[i]:
                continue
            if not clashes(i, pieces_of[i]):
                continue
            best = None
            for (mode, cand) in _relax_candidates(i):
                pts = build(i, mode, cand)
                if not body_clear(i, pts):
                    continue
                m = _oriented_segments(pts)
                if not clashes(i, m):
                    best = (mode, cand, m)
                    break
            if best is not None:
                mode_of[i], lane_of[i], pieces_of[i] = best
                swept_of[i] = mode_of[i] in ("vtrunk", "htrunk")
                changed = True
        if not changed:
            break

    routed: List[WireSeg] = []
    for i, seg in enumerate(segs):
        routed.append(replace(seg, points=build(i, mode_of[i], lane_of[i])))
    return routed


def wire_nets(hookup: Hookup) -> Tuple[str, ...]:
    """The ELECTRICAL NET each declared wire belongs to, in wire order.

    A net is a connected component of the netlist graph over ``<part>.<pin>``
    endpoints — so a daisy-chain (the TM1637 panel's shared CLK: ``ls1.HV1 ->
    tm1.CLK -> tm2.CLK -> tm3.CLK -> tm4.CLK``) is ONE net across four wires.
    The net id is the lexicographically smallest endpoint in the component, so
    it is a pure, stable function of the netlist — never of wire order."""
    parent: Dict[str, str] = {}

    def find(k: str) -> str:
        parent.setdefault(k, k)
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)      # smallest key wins -> stable

    for w in hookup.wires:
        union(f"{w.frm.part}.{w.frm.pin}", f"{w.to.part}.{w.to.pin}")
    return tuple(find(f"{w.frm.part}.{w.frm.pin}") for w in hookup.wires)


def wire_segments(hookup: Hookup,
                  layout: Optional[BoardLayout] = None) -> List[WireSeg]:
    """Resolve every hookup wire to a :class:`WireSeg` with both endpoints in
    board coordinates AND an orthogonal :attr:`WireSeg.points` polyline routed
    between them.  This is the tested seam: ``seg.frm_xy`` is exactly
    ``layout.pin_xy(part, pin)`` for the wire's source endpoint, and
    ``seg.points[0] == seg.frm_xy`` / ``seg.points[-1] == seg.to_xy``."""
    lay = layout or layout_hookup(hookup)
    # Signal color is a property of the NET, not of the wire's position in the
    # list (DEFECT 1, David 2026-08-15).  Indexing by wire made the TM1637
    # shared-CLK chain come out green / purple / orange — ONE net wearing three
    # signal colors while green was simultaneously reused on an unrelated net,
    # which is what "there seem to be two green clk wires" was reading.  Nets
    # take palette slots in first-appearance order over the declared wires, so
    # a color is only ever shared once the palette is genuinely exhausted.
    nets = wire_nets(hookup)
    net_color: Dict[str, str] = {}
    for w, net in zip(hookup.wires, nets):
        if _wire_role(w) == "signal" and net not in net_color:
            net_color[net] = COLOR_SIGNAL[len(net_color) % len(COLOR_SIGNAL)]

    segs: List[WireSeg] = []
    for w, net in zip(hookup.wires, nets):
        role = _wire_role(w)
        if role == "power":
            color = COLOR_POWER
        elif role == "ground":
            color = COLOR_GROUND
        else:
            color = net_color[net]
        frm_key = f"{w.frm.part}.{w.frm.pin}"
        to_key = f"{w.to.part}.{w.to.pin}"
        segs.append(WireSeg(
            frm=frm_key, to=to_key,
            frm_xy=lay.pins[frm_key], to_xy=lay.pins[to_key],
            role=role, color=color, publishes=w.publishes))
    return _assign_routes(lay, segs)




# ---------------------------------------------------------------------------
# line hops — a crossing must READ as a crossing, not as a junction
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WireHop:
    """One bridge to draw on a conductor where it crosses another.

    ``piece`` indexes the polyline VERTEX the hopping run starts at, so the
    renderer walks ``points[piece] -> points[piece + 1]`` and splices the arc in
    at ``x``.  ``sweep`` is the SVG arc sweep flag that makes the bridge rise
    (toward smaller y) for that run's direction of travel."""
    wire: int
    piece: int
    x: float
    y: float
    radius: float
    sweep: int
    over: int                      # the wire index being crossed


@dataclass(frozen=True)
class SkippedHop:
    """A crossing that is deliberately NOT bridged, and why.

    Every crossing is either hopped or listed here — silence would let an
    un-hopped crossing (which reads as a junction) pass unnoticed, which is the
    defect this whole mechanism exists to close.  ``reason`` is one of:

      ``pad``      the bridge would land on a solder pad — the one mark on the
                   diagram that IS a junction; obscuring it is worse than the
                   ambiguity, so the crossing is left plain;
      ``corner``   the bridge would swallow the run's own 90-degree corner;
      ``crowded``  a three-way / near-coincident crossing: a second bridge
                   within one bridge-width of the previous one would merge into
                   an unreadable blob, so only the first is drawn."""
    wire: int
    piece: int
    x: float
    y: float
    over: int
    reason: str


def plan_hops(layout: BoardLayout, segs: List[WireSeg]
              ) -> Tuple[Tuple[WireHop, ...], Tuple[SkippedHop, ...]]:
    """Decide, for every crossing of two drawn conductors, which one bridges.

    THE RULE: **the horizontal run hops the vertical one.**  Every crossing of
    two axis-aligned runs is exactly one horizontal / vertical pair, so the rule
    picks a side with no tie to break and no dependence on wire order, index,
    color or net — which is what makes a committed diagram re-render identically
    forever.  (Order-dependent alternatives — "the later wire hops", "the signal
    hops the rail" — flip the moment a wire is inserted upstream, and every
    re-render becomes a spurious diff.)

    Only PROPER crossings count: both runs must cross through each other's
    interior.  A run that merely ENDS on another (a T) is a touch, not a
    crossing, and bridging it would break the conductor that lands there.

    Pure function of (layout, segs) — deterministic iteration and a
    left-to-right greedy pass per run."""
    pads = [xy for xy in layout.pins.values()]
    horiz: List[Tuple[int, int, float, float, float]] = []   # w, piece, y, lo, hi
    vert: List[Tuple[int, int, float, float, float]] = []    # w, piece, x, lo, hi
    for i, seg in enumerate(segs):
        for k, ((ax, ay), (bx, by)) in enumerate(zip(seg.points,
                                                     seg.points[1:])):
            if abs(ay - by) < 1e-6 and abs(ax - bx) > 1e-6:
                horiz.append((i, k, ay, min(ax, bx), max(ax, bx)))
            elif abs(ax - bx) < 1e-6 and abs(ay - by) > 1e-6:
                vert.append((i, k, ax, min(ay, by), max(ay, by)))

    # crossings, grouped by the HORIZONTAL run that will carry the bridge
    found: Dict[Tuple[int, int], List[Tuple[float, float, int]]] = {}
    for (wi, pi, hy, hlo, hhi) in horiz:
        for (wj, pj, vx, vlo, vhi) in vert:
            if wj == wi:
                continue                       # a route never bridges itself
            if not (hlo + 1e-6 < vx < hhi - 1e-6):
                continue
            if not (vlo + 1e-6 < hy < vhi - 1e-6):
                continue
            found.setdefault((wi, pi), []).append((vx, hy, wj))

    hops: List[WireHop] = []
    skipped: List[SkippedHop] = []
    for (wi, pi) in sorted(found):
        ax, ay = segs[wi].points[pi]
        bx, _ = segs[wi].points[pi + 1]
        forward = bx > ax
        lo, hi = min(ax, bx), max(ax, bx)
        placed_at: Optional[float] = None
        bridged_pts: List[Tuple[float, float]] = []
        # travel order along the run, so "the previous bridge" means the
        # previous one the reader's eye meets — deterministic either way.
        crossings = sorted(set(found[(wi, pi)]), reverse=not forward)
        for (x, y, wj) in crossings:
            # ⛔ ONE POINT, ONE BRIDGE — A CO-LOCATED CROSSING IS ALREADY
            # BRIDGED, NOT SKIPPED (2026-08-15).  `found` is keyed by the
            # CROSSED WIRE, so N conductors sharing one trunk (a star net —
            # every `mcu.GND -> tmN.GND` rides the same lane, correctly) yield
            # N entries at the IDENTICAL (x, y).  The greedy pass bridged the
            # first and then measured the second against `placed_at` at a
            # distance of ZERO, which is `< 2 * HOP_R`, and filed it as
            # `crowded`.  ⚠ THE ARC IS RIGHT THERE ON THE PAGE.  Nothing reads
            # as a junction; the drawing is correct and the INSTRUMENT was
            # wrong — a false RED, the same disease as a false green and just
            # as expensive: measured on this scene it reported 4 unbridgeable
            # crossings that do not exist, which is what made a legible layout
            # look like it cost the crossing gate.  A bridge spans the POINT,
            # so every conductor through that point is spanned.
            if any(abs(px - x) < 1e-6 and abs(py - y) < 1e-6
                   for (px, py) in bridged_pts):
                continue
            if any(max(abs(px - x), abs(py - y)) <= PAD_R + HOP_R
                   for (px, py) in pads):
                skipped.append(SkippedHop(wi, pi, x, y, wj, "pad"))
                continue
            if (x - lo < HOP_R + _HOP_CORNER_CLEAR
                    or hi - x < HOP_R + _HOP_CORNER_CLEAR):
                skipped.append(SkippedHop(wi, pi, x, y, wj, "corner"))
                continue
            if placed_at is not None and abs(x - placed_at) < 2 * HOP_R:
                skipped.append(SkippedHop(wi, pi, x, y, wj, "crowded"))
                continue
            hops.append(WireHop(wire=wi, piece=pi, x=x, y=y, radius=HOP_R,
                                sweep=1 if forward else 0, over=wj))
            placed_at = x
            bridged_pts.append((x, y))
    hops.sort(key=lambda h: (h.wire, h.piece, h.x, h.over))
    skipped.sort(key=lambda s: (s.wire, s.piece, s.x, s.over))
    return tuple(hops), tuple(skipped)


# ---------------------------------------------------------------------------
# ⛔ THE SKIP REPORT — plan_hops' second return value, given somewhere to GO
#
# `plan_hops` has always returned `(hops, skipped)`, and `SkippedHop`'s docstring
# above states the reason: "silence would let an un-hopped crossing (which reads
# as a junction) pass unnoticed".  Until 2026-08-15 the ONE caller
# (`hookup_svg.hookup_to_svg`) bound that second value to `_skipped` and dropped
# it on the floor, so the mechanism built to prevent the silence WAS the silence.
# Measured on `render/examples/tm1637_quad_panel.compose.yaml`: 57 hops planned,
# 7 crossings skipped, five of them piled at one congested column (x=360) — seven
# intersections on a perfboard guide that read as solder joints, invisibly.
#
# This type is where the second value goes.  It is deliberately NOT a bool: a
# refusal that says "7 crossings skipped" and stops is a dead end for the author,
# who has to re-route a specific column.  Every row names the two conductors by
# `part.pin`, the coordinate, and the reason, so the refusal is a work order.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class CrossingReport:
    """Every conductor crossing in one routed diagram, ACCOUNTED FOR.

    ``hops`` bridge, ``skipped`` do not, and the union is every proper crossing
    the planner found — that totality is the property the whole mechanism rests
    on, and it is asserted by
    ``tests/render/test_hookup_render_defects.py::test_every_crossing_is_hopped_or_explicitly_suppressed``.

    ``wires`` indexes wire-number -> ``"part.pin -> part.pin"`` so a consumer can
    name a crossing without holding the ``WireSeg`` list; ``ok`` is the one-line
    verdict (**nothing was skipped**) that a delivery gate reads."""
    hops: Tuple[WireHop, ...]
    skipped: Tuple[SkippedHop, ...]
    wires: Tuple[str, ...]

    @property
    def ok(self) -> bool:
        """True iff EVERY crossing bridges.  A False here means the diagram
        contains at least one intersection a reader may take for a junction."""
        return not self.skipped

    def wire_name(self, index: int) -> str:
        try:
            return self.wires[index]
        except IndexError:                                 # defensive
            return f"wire#{index}"

    def skipped_rows(self) -> List[Dict[str, object]]:
        """The skipped crossings as JSON-native rows — the payload a refusal
        hands back and an intent carries.  ORDERED as ``plan_hops`` sorted them
        (wire, piece, x, over), so two runs produce byte-identical rows."""
        return [{"wire": s.wire, "wire_name": self.wire_name(s.wire),
                 "crosses": s.over, "crosses_name": self.wire_name(s.over),
                 "x": round(float(s.x), 2), "y": round(float(s.y), 2),
                 "reason": s.reason}
                for s in self.skipped]

    def as_dict(self) -> Dict[str, object]:
        """``{hopped, skipped, reasons, rows}`` — the whole verdict, JSON-native."""
        reasons: Dict[str, int] = {}
        for s in self.skipped:
            reasons[s.reason] = reasons.get(s.reason, 0) + 1
        return {"hopped": len(self.hops), "skipped": len(self.skipped),
                "reasons": dict(sorted(reasons.items())),
                "rows": self.skipped_rows()}

    def skipped_text(self, indent: str = "  ") -> str:
        """The skipped crossings as human lines, one per crossing, with the
        coordinate and the reason.  ONE wording, shared by every consumer that
        refuses — the endpoint's message and any future CLI read the same
        sentences rather than each inventing their own."""
        return "\n".join(
            f"{indent}{r['wire_name']} crosses {r['crosses_name']} at "
            f"({r['x']}, {r['y']}) — NOT bridged [{r['reason']}]"
            for r in self.skipped_rows())


_SKIP_REMEDY = {
    "pad": ("the bridge would land on a solder pad (the one mark that IS a "
            "junction). Move the crossing off the pad: shift the part by a grid "
            "step, or re-order the wires so this run takes a different lane."),
    "corner": ("the bridge would swallow the run's own 90-degree corner. Give "
               "the run more straight length before it turns — move the part, "
               "or re-order the wire so it routes on a different lane."),
    "crowded": ("two crossings sit closer together than one bridge is wide, so "
                "only the first could be drawn. This is a CONGESTED COLUMN: "
                "spread the parts, or split the bus so fewer conductors cross "
                "the same vertical run at the same place."),
}


def crossing_report(layout: BoardLayout, segs: List[WireSeg]) -> CrossingReport:
    """Route *segs* and return the FULL crossing verdict — hops AND skips.

    Thin over :func:`plan_hops` (same pure function, same determinism); all it
    adds is the wire names, so the second return value can be reported rather
    than discarded.  Callers that only draw want ``report.hops``; callers that
    DELIVER want ``report.ok``."""
    hops, skipped = plan_hops(layout, segs)
    return CrossingReport(hops=hops, skipped=skipped,
                          wires=tuple(f"{s.frm} -> {s.to}" for s in segs))


def skip_remedy(reason: str) -> str:
    """What an author actually DOES about a skipped crossing of *reason*.  A
    refusal a reader cannot act on gets worked around, not fixed."""
    return _SKIP_REMEDY.get(reason, "re-route this crossing.")


def label_anchor(points: Tuple[Tuple[float, float], ...]
                 ) -> Tuple[float, float]:
    """Midpoint of the longest segment of a routed polyline — a stable,
    uncluttered spot to drop the net's publish label (shared by both
    renderers so the label sits in the same place on every substrate)."""
    best = (points[0], points[1])
    best_len = -1.0
    for a, b in zip(points, points[1:]):
        seg_len = abs(b[0] - a[0]) + abs(b[1] - a[1])
        if seg_len > best_len:
            best_len, best = seg_len, (a, b)
    (ax, ay), (bx, by) = best
    return ((ax + bx) / 2.0, (ay + by) / 2.0)


__all__ = [
    "PITCH", "HOLE_R", "PAD_R", "ORIGIN_X", "ORIGIN_Y", "MARGIN",
    "VERTICAL_PIN_THRESHOLD", "COL_SPREAD", "ROW_SPREAD",
    "STUB_BASE", "BODY_CLEAR", "ESC_STEP", "LANE_STEP", "HOP_R",
    "POWER_TOKENS", "GROUND_TOKENS",
    "COLOR_POWER", "COLOR_GROUND", "COLOR_SIGNAL",
    "COLOR_RAIL_POS", "COLOR_RAIL_NEG",
    "BOARD_BREADBOARD", "BOARD_PERFBOARD", "HOLE_FILL_BB",
    "INK", "DIM", "BODY_FILL", "BODY_TEXT",
    "FootprintPlacement", "PartBox", "BoardLayout", "WireSeg",
    "WireHop", "SkippedHop", "CrossingReport",
    "layout_hookup", "wire_segments", "wire_nets", "plan_hops", "label_anchor",
    "crossing_report", "skip_remedy",
    # legibility: the shared extent + the shared pin-label decision
    "part_extent", "PART_CLEARANCE", "PinLabel", "pin_label_plan", "wired_pins",
    "ROTATE_MIN_NAME_CHARS",
    "LABEL_INSIDE", "LABEL_BELOW", "LABEL_EDGE_LEFT", "LABEL_EDGE_RIGHT",
    "LABEL_ROT_ABOVE", "LABEL_ROT_BELOW",
]
