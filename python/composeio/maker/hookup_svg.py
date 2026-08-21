"""
Hookup SVG generator — S3 of the maker-project flow
(`docs/maker/maker-project-flow.md` §5).

ONE spec, TWO substrates (the narrow waist).  A maker `.compose.yaml` carries a
`hookup:` block that S2 parsed + validated into a
:class:`composeio.render.engine.hookup.Hookup` — a board choice, placed parts
(each a resolved catalog :class:`PartDef`), and a wire netlist.  This module
renders that ONE netlist to an SVG wiring guide two different ways:

  * **breadboard** — power rails (+/-) down the sides, a field of tie-point
    holes split by the center gap, parts placed on the grid, wires drawn as
    jumper leads between tie-points/rails.
  * **perfboard**  — a uniform 0.1" hole grid, parts placed, wires drawn as
    EXPLICIT point-to-point solder traces (a copper trace + a solder pad at
    each end).

Same parts + same wires; only the board background and the connection
semantics differ.  The electrical geometry (where each `<part>.<pin>` sits, and
therefore where a wire's two ends land) is generated deterministically from the
catalog pin map by :func:`layout_hookup`, so a wire ALWAYS locks to the right
pin coordinate — that is the property S3's tests assert.

Output lowering (doc-viewer): the SVG is emitted with only the primitive subset
the ComposeUX SVG pipeline decomposes to the Canvas contract (rect, circle,
line, path, text, g/transform — no `<style>`, no filters), so the console
doc-viewer renders it natively via the `svg` widget ([SPEC:CXC-SVG]) with no
external app.  See :func:`hookup_svg_to_file` +
`compose_projects/intelligence/tools/doc_viewer_launcher.py` (`.svg` open branch).

Component art (the compositing pass): a part whose catalog def carries a
cached Fritzing SVG + a complete `fritzing_connectors` map renders the REAL
footprint art, inlined as an id-prefixed `<g>` and placed art-bbox-top-left
on the part's `at:` grid anchor, scaled so the SVG's declared physical size
matches the board grid (0.1" == PITCH).  Its pin coordinates in the layout
ARE the real `connectorN(pin|terminal)` centers — transform-RESOLVED through
the engine's SVG model (`SvgDocument.element_center`, never raw cx/cy) — so
wires land ON the real pads (see `render/engine/parts/footprint.py`).  A
labeled affordance (instance id + per-pin name + a terminal ring) keeps pin
identity readable over the art.  Parts WITHOUT a complete real footprint
keep the clean GENERATED footprint (a labeled body + named pin stubs from
the catalog pin map, first pin anchored on the grid point) — the additive
fallback (e.g. `esp32-c3`, whose art is not cached).

Reading a crossing (2026-08-15).  Every conductor carries `data-wire` +
`data-net`, so "one drawn conductor per declared wire" is a checkable property
of the output rather than a hope — footprint art contributes most of the
document's `<path>` elements and a naive count cannot tell them apart.  Where
two conductors cross, the HORIZONTAL one is drawn with a small bridge
(:func:`plan_hops`) so the crossing cannot be read as a junction; junctions are
FILLED pads, bridges are unfilled arcs, and the two are disjoint by
construction.

⛔ A SKIPPED CROSSING IS A HARD FAILURE (David, 2026-08-15).  `plan_hops`
returns `(hops, skipped)` and the second value USED TO BE DISCARDED at the only
call site — so the diagram could ship 7 un-bridged crossings (measured, TM1637
quad panel) that each read as a solder joint.  :func:`render_hookup` returns the
`CrossingReport` alongside the SVG; anything that hands a guide to a human must
gate on `report.ok`.

Public API::

    render_hookup(hookup)               -> (str, CrossingReport)  # ⛔ full result
    hookup_to_svg(hookup)               -> str          # SVG document text
    hookup_svg_to_file(hookup, path)    -> str           # writes, returns path
    layout_hookup(hookup)               -> BoardLayout    # pin coords + boxes
    wire_segments(hookup, layout=None)  -> list[WireSeg]  # resolved wire ends
    hookup_viewer_scene(svg_path, ...)  -> dict           # doc-viewer scene
"""

from __future__ import annotations

import html
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from composeio.render.engine import hookup_layout as hl
from composeio.render.engine.hookup import Hookup
# The layout/routing intermediate is SHARED with the native `hookup` widget
# ([FLOW:MAKER-PROJECT] S3 refactor, 2026-07-26): geometry moved VERBATIM to
# `composeio.render.engine.hookup_layout` and is re-exported here so existing
# consumers (tests, the S5 build, downstream imports) are byte-compatible.
from composeio.render.engine.hookup_layout import (  # noqa: F401  (re-export)
    BOARD_BREADBOARD, BOARD_PERFBOARD, BODY_CLEAR, BODY_FILL, BODY_TEXT,
    COL_SPREAD, COLOR_GROUND, COLOR_POWER, COLOR_RAIL_NEG, COLOR_RAIL_POS,
    COLOR_SIGNAL, DIM, ESC_STEP, GROUND_TOKENS, HOLE_FILL_BB, HOLE_R, HOP_R,
    INK, LANE_STEP, MARGIN, ORIGIN_X, ORIGIN_Y, PAD_R, PITCH, POWER_TOKENS,
    ROW_SPREAD, STUB_BASE, VERTICAL_PIN_THRESHOLD, BoardLayout, CrossingReport,
    FootprintPlacement, PartBox, SkippedHop, WireHop, WireSeg, crossing_report,
    label_anchor, layout_hookup, plan_hops, skip_remedy, wire_nets,
    wire_segments)

_label_anchor = label_anchor        # internal alias (pre-refactor name)


def _esc(text: str) -> str:
    """XML-escape text for an SVG text node / attribute value."""
    return html.escape(str(text), quote=True)


# ---------------------------------------------------------------------------
# small SVG builder (emits only the Canvas-lowerable primitive subset)
# ---------------------------------------------------------------------------
class _Svg:
    def __init__(self, width: float, height: float) -> None:
        self.w = width
        self.h = height
        self._body: List[str] = []

    def rect(self, x, y, w, h, *, fill="none", stroke="none",
             sw=1.0, rx=0.0, opacity=1.0) -> None:
        self._body.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
            f'rx="{rx:.2f}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw:.2f}" opacity="{opacity:.3f}"/>')

    def circle(self, cx, cy, r, *, fill="none", stroke="none", sw=1.0) -> None:
        self._body.append(
            f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="{fill}" '
            f'stroke="{stroke}" stroke-width="{sw:.2f}"/>')

    def line(self, x1, y1, x2, y2, *, stroke="#000", sw=1.0,
             cap="round", opacity=1.0) -> None:
        self._body.append(
            f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
            f'stroke="{stroke}" stroke-width="{sw:.2f}" '
            f'stroke-linecap="{cap}" opacity="{opacity:.3f}"/>')

    def path(self, d, *, fill="none", stroke="#000", sw=1.0,
             cap="round", extra: str = "") -> None:
        self._body.append(
            f'<path d="{d}" fill="{fill}" stroke="{stroke}" '
            f'stroke-width="{sw:.2f}" stroke-linecap="{cap}"{extra}/>')

    def text(self, x, y, s, *, size=10.0, fill=INK, anchor="start",
             weight="normal", family="monospace", rotate: float = 0.0,
             owner: str = "", stack: str = "") -> None:
        """One text run.

        *owner* names the part (or ``"chrome"`` / ``"wire"``) the label belongs
        to, emitted as ``data-owner``.  It is INERT to every renderer — the
        Canvas lowering ignores unknown attributes — and exists so
        :func:`legibility_report` can tell "this part's own id, drawn on its own
        body, on purpose" from "another part's label printed across it".  A gate
        that cannot attribute a label can only measure collisions it must then
        hand-wave about; carrying ownership in the document means the drawing
        answers the question rather than the gate guessing.

        ⛔ EVERY label this renderer draws MUST carry an owner, because the gate
        treats "has ``data-owner``" as "is a label the GUIDE drew".  The
        footprint art inlined by :meth:`raw` carries its OWN ``<text>`` (a
        TM1637's silkscreen, its unlit digits) in the ART's user space, under a
        group transform — those are ARTWORK, already accounted for by the part
        extent, and measuring them as labels would compare pre-transform
        coordinates against board coordinates and invent collisions.  The
        marker is what separates the two populations.

        *stack* names a DELIBERATE multi-line stack (the id-over-catalog-ref
        caption).  Adjacent lines of one caption share the ascent/descent slack
        of their line boxes by design; the gate exempts a pair that shares BOTH
        owner and stack, and nothing else — two PIN labels of the same owner
        printing on each other is precisely the defect being hunted.

        *rotate* is degrees about ``(x, y)`` — see the ``LABEL_ROT_*`` note in
        ``hookup_layout``: a row of pads has one pad-pitch of horizontal room
        and a whole board margin of vertical room, so a row's labels are turned."""
        tr = (f' transform="rotate({rotate:.1f} {x:.2f} {y:.2f})"'
              if rotate else "")
        own = f' data-owner="{_esc(owner)}"' if owner else ""
        stk = f' data-stack="{_esc(stack)}"' if stack else ""
        self._body.append(
            f'<text x="{x:.2f}" y="{y:.2f}" font-size="{size:.1f}" '
            f'font-family="{family}" font-weight="{weight}" '
            f'fill="{fill}" text-anchor="{anchor}"{tr}{own}{stk}>'
            f'{_esc(s)}</text>')

    def raw(self, fragment: str) -> None:
        """Append a pre-serialized SVG fragment (the inlined footprint art —
        already id-prefixed + attribute-escaped by serialize_svg_group)."""
        self._body.append(fragment)

    def render(self) -> str:
        body = "\n  ".join(self._body)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'width="{self.w:.0f}" height="{self.h:.0f}" '
            f'viewBox="0 0 {self.w:.0f} {self.h:.0f}">\n  {body}\n</svg>\n')


# ---------------------------------------------------------------------------
# shared chrome: title bar + role legend
# ---------------------------------------------------------------------------
def _draw_title(svg: _Svg, hookup: Hookup, subtitle: str) -> None:
    svg.text(ORIGIN_X - 60, 30, f"Hookup guide — {hookup.board}", owner="chrome",
             size=16, weight="bold", fill=INK, family="sans-serif")
    svg.text(ORIGIN_X - 60, 48, subtitle, size=10, fill=DIM, owner="chrome",
             family="monospace")
    # legend chips
    lx = ORIGIN_X - 60
    ly = 62
    for label, color in (("power", COLOR_POWER), ("ground", COLOR_GROUND),
                         ("signal", COLOR_SIGNAL[0])):
        svg.line(lx, ly, lx + 16, ly, stroke=color, sw=3.0)
        svg.text(lx + 21, ly + 3.5, label, size=9, fill=DIM, owner="chrome",
                 family="sans-serif")
        lx += 78


# ---------------------------------------------------------------------------
# part footprint (generated; the electrical geometry wires lock to)
# ---------------------------------------------------------------------------
PIN_LABEL_SIZE = 7.5      # px — the catalog pin-name overlay
_ROT_GAP = 5.0            # clearance between the art edge and a turned label
# A turned label's baseline runs vertically; its glyph column straddles that
# baseline (ascent one side, descent the other), so nudging the baseline by
# about a third of the em centres the column on the pad it names.
_ROT_NUDGE = 0.33
# Upper bound on the monospace advance, in ems, at the sizes this drawing uses
# (measured through the engine: 0.533 at 7.5 px, 0.625 at 8, 0.636 at 11).  Used
# ONLY to reserve headroom above a rotated row for the part-id caption — an
# over-estimate merely leaves air, and `legibility_report` measures the real
# boxes either way.
_ADVANCE_EM = 0.64
# Half a turned label's glyph COLUMN, in ems.  Measured through the engine at
# the drawing's 7.5 px pin size: ascent 8.0 + descent 3.0 = 11.0 px of column,
# so half is 5.5 px = 0.733 em.  Same contract as `_ADVANCE_EM` above — an
# over-estimate merely leaves air, and `legibility_report` measures the REAL
# boxes either way.
_ROT_HALF_COL_EM = 0.74
# ⛔ A TURNED CAPTION USED TO BE CENTRED ON THE PAD — AND THEREFORE ON THE PAD'S
# OWN LEAD (2026-08-16).  `_ROT_NUDGE` centres the glyph column on the pad's x,
# which is exactly the x a lead escapes along, so EVERY turned caption on a part
# whose leads leave perpendicular to its pin row printed on its own conductor.
# Measured before the offset: 9 of 9 turned captions on ink in dual_throttle, 23
# of 29 on the R503 panel — and on the R503 the near-black `GND` caption sat on
# the near-black ground conductor and was, in the raster, GONE.  On the one part
# in this fleet whose headline hazard is a reversed VCC/GND, that is a safety
# surface, not typography.
#
# ⛔ AND THE OBVIOUS FIX IS THE WRONG ONE.  A backing plate behind the label
# would restore contrast by BREAKING THE CONDUCTOR under it — a reader losing a
# conductor for the width of an opaque thing, which is the exact class
# `legibility_report.body_wire` was just made to refuse.  Trading one lie for a
# smaller copy of the same lie is not a fix.  The caption moves INSTEAD: it
# stands BESIDE its lead, half a glyph column plus half a conductor plus a
# pixel of air, in the one dimension this drawing has to spare.
_ROT_LEAD_GAP = 1.0


def _rot_lead_clear(size: float) -> float:
    """How far a turned caption steps aside so it stands BESIDE its pad's lead
    rather than on it."""
    return size * _ROT_HALF_COL_EM + CONDUCTOR_HALF_W + _ROT_LEAD_GAP


def _rot_label(svg: _Svg, box: PartBox, lbl, *, size: float, fill: str) -> None:
    """Draw one turned (90 deg) pin label standing off the art's nearer edge,
    stepped aside from the lead that leaves the pad it names.

    ``rotate(-90)`` maps local +x onto global -y, so the run reads
    bottom-to-top in BOTH directions: ``anchor="start"`` off the top edge grows
    upward, ``anchor="end"`` off the bottom edge grows downward.  One reading
    direction for the whole drawing — a builder never has to tilt their head
    two ways on one board.

    The step aside is always the SAME direction (+x) for the whole drawing, for
    the same reason the reading direction is: a builder should never have to
    work out which side of a pad a name belongs to."""
    # stand off the part's FULL extent, not its body slab: a generated part
    # hangs its pads BELOW the body, and a label standing off the body would
    # run straight down through the pads it names.
    bx, by, bw, bh = hl.part_extent(box)
    x = lbl.x + size * _ROT_NUDGE + _rot_lead_clear(size)
    if lbl.kind == hl.LABEL_ROT_ABOVE:
        svg.text(x, by - _ROT_GAP, lbl.pin, size=size, fill=fill,
                 anchor="start", family="monospace", rotate=-90.0,
                 owner=box.id)
    else:
        svg.text(x, by + bh + _ROT_GAP, lbl.pin, size=size, fill=fill,
                 anchor="end", family="monospace", rotate=-90.0,
                 owner=box.id)


def _caption_headroom(plan, size: float) -> float:
    """How far above the art the part-id caption must sit to clear a rotated
    top row (0 when there is none)."""
    ups = [l for l in plan if l.kind == hl.LABEL_ROT_ABOVE]
    if not ups:
        return 0.0
    return _ROT_GAP + max(len(l.pin) for l in ups) * size * _ADVANCE_EM


# ⛔ LABELS ARE DRAWN LAST, ON TOP OF THE WIRES — AND THAT IS A GLASS FINDING,
# NOT A PREFERENCE (2026-08-15).  Parts used to be drawn complete (art, pads,
# captions, pin names) and the wires painted afterwards, so every label sitting
# in the wire channel immediately outside a part was OVERPAINTED.  With the
# turned row labels standing off the art's top edge — exactly where the ground
# rail runs — the MCU's whole top row disappeared under a near-black conductor.
# ⚠ EVERY MACHINE SIGNAL STAYED GREEN THROUGH THAT: `label_survival` counts ink
# from a text-stripped differential and the text was still THERE, merely
# covered; `legibility_report` compares labels to labels and to bodies, and a
# WIRE is neither.  It was caught by opening the PNG and looking.  The fix is
# z-order: a guide's labels are its instruction, so nothing may occlude them.
def _draw_footprint_part(svg: _Svg, box: PartBox,
                         wired: Optional[set] = None) -> None:
    """Composite REAL Fritzing art + the labeled affordance.

    The art is inlined as an id-prefixed group under the placing affine
    (translate + uniform scale).  Because the layout's pin coordinates ARE
    the transform-resolved connector-terminal centers, the wires drawn later
    land on the real pads by construction.  The affordance (instance id
    above the art, a ring + catalog pin name at every terminal) keeps pin
    identity readable even where the art's own silkscreen is dense.

    WHICH pin gets a label and WHERE is not decided here — it is
    ``hookup_layout.pin_label_plan``, shared with the on-canvas widget so the
    two surfaces cannot drift.  This function owns only the offsets."""
    fpp = box.footprint
    assert fpp is not None
    bx, by, bw, bh = box.body

    svg.raw(f'<g transform="translate({fpp.tx:.2f} {fpp.ty:.2f}) '
            f'scale({fpp.scale:.6f})">')
    svg.raw(fpp.fp.inline_markup(id_prefix=f"{box.id}-"))
    svg.raw("</g>")

    # the terminal ring marks EVERY real pad — including the ones whose label
    # is suppressed, so a suppressed name never costs a builder a countable pad
    for name, (px, py) in box.pins.items():
        svg.circle(px, py, HOLE_R + 1.0, fill="none", stroke="#1A2230",
                   sw=0.9)


def _draw_footprint_labels(svg: _Svg, box: PartBox,
                           wired: Optional[set] = None) -> None:
    """The footprint part's TEXT, drawn after the wires (see the z-order note)."""
    bx, by, bw, bh = box.body
    plan = hl.pin_label_plan(box, wired)

    # instance id + catalog ref above the art, lifted clear of any turned row
    head = _caption_headroom(plan, PIN_LABEL_SIZE)
    svg.text(bx + bw / 2, by - 19 - head, box.id, size=11, weight="bold",
             fill=INK, anchor="middle", family="monospace", owner=box.id,
             stack="caption")
    svg.text(bx + bw / 2, by - 6 - head, box.part, size=8, fill=DIM,
             anchor="middle", family="monospace", owner=box.id,
             stack="caption")

    for lbl in plan:
        if lbl.kind in (hl.LABEL_ROT_ABOVE, hl.LABEL_ROT_BELOW):
            _rot_label(svg, box, lbl, size=PIN_LABEL_SIZE, fill=INK)
        elif lbl.kind == hl.LABEL_BELOW:
            svg.text(lbl.x, lbl.y + 14, lbl.pin, size=PIN_LABEL_SIZE, fill=INK,
                     anchor="middle", family="monospace", owner=box.id)
        elif lbl.kind == hl.LABEL_EDGE_LEFT:
            svg.text(min(lbl.x - 7, bx - 4), lbl.y + 3.2, lbl.pin,
                     size=PIN_LABEL_SIZE, fill=INK, anchor="end",
                     family="monospace", owner=box.id)
        else:
            svg.text(max(lbl.x + 7, bx + bw + 4), lbl.y + 3.2, lbl.pin,
                     size=PIN_LABEL_SIZE, fill=INK, anchor="start",
                     family="monospace", owner=box.id)


def _draw_part(svg: _Svg, box: PartBox, wired: Optional[set] = None) -> None:
    """A part's ARTWORK — body, pads, stubs.  Its text is
    :func:`_draw_part_labels`, drawn after the wires."""
    if box.footprint is not None:
        _draw_footprint_part(svg, box, wired)
        return
    bx, by, bw, bh = box.body
    svg.rect(bx, by, bw, bh, fill=BODY_FILL, stroke="#0F1622", sw=1.2, rx=4.0)
    # pin stubs + holes for EVERY pad (a suppressed label never hides a pad)
    for name, (px, py) in box.pins.items():
        if box.orient == "vertical":
            svg.line(bx, py, px, py, stroke="#8894A6", sw=1.4)
        else:
            svg.line(px, by + bh, px, py, stroke="#8894A6", sw=1.4)
        svg.circle(px, py, HOLE_R + 0.8, fill="#E9EEF5",
                   stroke="#4A5568", sw=1.0)


def _draw_part_labels(svg: _Svg, box: PartBox,
                      wired: Optional[set] = None) -> None:
    """A part's TEXT — the LAST thing on the page, so no conductor covers it."""
    if box.footprint is not None:
        _draw_footprint_labels(svg, box, wired)
        return
    bx, by, bw, bh = box.body
    plan = hl.pin_label_plan(box, wired)
    # instance id + catalog ref centred on the body
    svg.text(bx + bw / 2, by + 13, box.id, size=11, weight="bold",
             fill=BODY_TEXT, anchor="middle", family="monospace", owner=box.id,
             stack="caption")
    svg.text(bx + bw / 2, by + 25, box.part, size=8, fill="#9AA7B8",
             anchor="middle", family="monospace", owner=box.id,
             stack="caption")
    for lbl in plan:
        if lbl.kind == hl.LABEL_INSIDE:
            svg.text(bx + 6, lbl.y + 3.2, lbl.pin, size=PIN_LABEL_SIZE,
                     fill=BODY_TEXT, anchor="start", family="monospace",
                     owner=box.id)
        elif lbl.kind in (hl.LABEL_ROT_ABOVE, hl.LABEL_ROT_BELOW):
            _rot_label(svg, box, lbl, size=PIN_LABEL_SIZE, fill=INK)
        else:
            svg.text(lbl.x, lbl.y + 15, lbl.pin, size=PIN_LABEL_SIZE, fill=INK,
                     anchor="middle", family="monospace", owner=box.id)


# ---------------------------------------------------------------------------
# breadboard substrate
# ---------------------------------------------------------------------------
def _draw_breadboard_bg(svg: _Svg, lay: BoardLayout) -> None:
    # board slab
    svg.rect(ORIGIN_X - 66, ORIGIN_Y - 34,
             lay.width - (ORIGIN_X - 66) - 12,
             lay.height - (ORIGIN_Y - 34) - 12,
             fill=BOARD_BREADBOARD, stroke="#B9A86E", sw=1.5, rx=8.0)

    field_left = ORIGIN_X - 44
    field_right = lay.width - 44
    field_top = ORIGIN_Y - 12
    field_bot = lay.height - 30
    center_y = (field_top + field_bot) / 2.0
    gap = PITCH * 1.4

    # tie-point field: columns of holes split by the horizontal center gap
    x = field_left
    while x <= field_right:
        y = field_top
        while y <= field_bot:
            if abs(y - center_y) > gap / 2.0:
                svg.circle(x, y, HOLE_R - 0.4, fill=HOLE_FILL_BB,
                           stroke="#B4A163", sw=0.6)
            y += PITCH
        x += PITCH

    # center-gap trough
    svg.rect(field_left - 6, center_y - gap / 2.0,
             (field_right - field_left) + 12, gap,
             fill="#DDCB92", stroke="none")

    # power rails down BOTH sides (+ red outer, - blue inner)
    for side, xr in (("L", ORIGIN_X - 60), ("R", lay.width - 26)):
        pos_x = xr if side == "L" else xr
        neg_x = xr + 12 if side == "L" else xr - 12
        svg.line(pos_x, field_top, pos_x, field_bot,
                 stroke=COLOR_RAIL_POS, sw=1.6)
        svg.line(neg_x, field_top, neg_x, field_bot,
                 stroke=COLOR_RAIL_NEG, sw=1.6)
        svg.text(pos_x, field_top - 6, "+", size=11, fill=COLOR_RAIL_POS, owner="chrome",
                 anchor="middle", family="sans-serif", weight="bold")
        svg.text(neg_x, field_top - 6, "−", size=11, owner="chrome",
                 fill=COLOR_RAIL_NEG, anchor="middle", family="sans-serif",
                 weight="bold")
        y = field_top
        while y <= field_bot:
            svg.circle(pos_x, y, HOLE_R - 0.6, fill=HOLE_FILL_BB,
                       stroke="#B4A163", sw=0.5)
            svg.circle(neg_x, y, HOLE_R - 0.6, fill=HOLE_FILL_BB,
                       stroke="#B4A163", sw=0.5)
            y += PITCH


def _poly_d(points: Tuple[Tuple[float, float], ...],
            hops: Sequence[WireHop] = ()) -> str:
    """An SVG path `d` tracing an orthogonal polyline, with LINE HOPS spliced in.

    Straight runs are `M`/`L`; where :func:`plan_hops` decided this conductor
    bridges another, the run is broken at ``x -+ r`` and rejoined with an `A`
    arc that rises above the line — so the crossing reads as a crossing instead
    of a junction.  The arc is a plain elliptical-arc command, inside the
    Canvas-lowerable subset the doc-viewer decomposes ([CXC-SVG]: `A` lowers to
    the contract's ``ArcTo``), so a hopped guide still renders natively.

    A bridge is an UNFILLED arc in the conductor's own stroke; a junction is a
    FILLED pad.  Nothing on the diagram can be read as both."""
    by_piece: Dict[int, List[WireHop]] = {}
    for hp in hops:
        by_piece.setdefault(hp.piece, []).append(hp)

    d = f"M {points[0][0]:.2f} {points[0][1]:.2f}"
    for k, ((ax, ay), (bx, by)) in enumerate(zip(points, points[1:])):
        for hp in sorted(by_piece.get(k, ()),
                         key=lambda h: h.x, reverse=(bx < ax)):
            step = hp.radius if bx > ax else -hp.radius
            d += (f" L {hp.x - step:.2f} {ay:.2f}"
                  f" A {hp.radius:.2f} {hp.radius:.2f} 0 0 {hp.sweep} "
                  f"{hp.x + step:.2f} {ay:.2f}")
        d += f" L {bx:.2f} {by:.2f}"
    return d


def _wire_attrs(seg: WireSeg, net: str) -> str:
    """Machine-readable identity on every drawn conductor.

    The TM1637 guide shipped with a wire that appeared to exist and did not,
    and no test could count the conductors because footprint art contributes
    136 of the document's 166 `<path>` elements.  Marking the conductors makes
    "one drawn conductor per declared wire" a checkable property rather than a
    hope.  Both attributes are inert data-* — the SVG model carries unknown
    attributes verbatim and the renderer ignores them."""
    return (f' data-wire="{_esc(seg.frm)} {_esc(seg.to)}"'
            f' data-net="{_esc(net)}"')


def _draw_breadboard_wires(svg: _Svg, segs: List[WireSeg],
                           hops: Dict[int, List[WireHop]],
                           nets: Tuple[str, ...]) -> None:
    for i, seg in enumerate(segs):
        # orthogonal jumper lead: right-angle polyline through a per-net lane,
        # bridged where it crosses another conductor.  Stroke DOUBLED (David
        # 2026-07-26: fatter leads are easier to color-trace across the guide).
        svg.path(_poly_d(seg.points, hops.get(i, ())), stroke=seg.color,
                 sw=5.2, extra=_wire_attrs(seg, nets[i]))
        # lead ends (land exactly on the real pins)
        (x1, y1), (x2, y2) = seg.frm_xy, seg.to_xy
        svg.circle(x1, y1, 2.4, fill=seg.color)
        svg.circle(x2, y2, 2.4, fill=seg.color)
        if seg.publishes:
            lx, ly = _label_anchor(seg.points)
            svg.text(lx, ly - 4, seg.publishes, size=7.5, fill=seg.color, owner="wire",
                     anchor="middle", family="monospace")


# ---------------------------------------------------------------------------
# perfboard substrate
# ---------------------------------------------------------------------------
def _draw_perfboard_bg(svg: _Svg, lay: BoardLayout) -> None:
    # Plain WHITE board slab — no hole-grid dot field (David 2026-07-26: nothing
    # snaps to the grid, so the dots were decorative debt; the real connection
    # points are the solder PADS drawn per-wire in _draw_perfboard_wires).
    svg.rect(ORIGIN_X - 66, ORIGIN_Y - 34,
             lay.width - (ORIGIN_X - 66) - 12,
             lay.height - (ORIGIN_Y - 34) - 12,
             fill=BOARD_PERFBOARD, stroke="#D0D5DD", sw=1.5, rx=6.0)


def _draw_perfboard_wires(svg: _Svg, segs: List[WireSeg],
                          hops: Dict[int, List[WireHop]],
                          nets: Tuple[str, ...]) -> None:
    for i, seg in enumerate(segs):
        # EXPLICIT orthogonal solder trace: a right-angle copper run (routed
        # through a per-net lane so converging leads stay individually
        # traceable) with a solder pad at each end (perfboard has no bus
        # strips — every net is a discrete trace the maker solders).  Stroke
        # DOUBLED (David 2026-07-26: fatter traces are easier to color-trace).
        # Crossings are BRIDGED (David 2026-08-15) — on a perfboard especially,
        # a crossing that reads as a junction is a wrong solder joint.
        svg.path(_poly_d(seg.points, hops.get(i, ())), stroke=seg.color,
                 sw=4.8, cap="round", extra=_wire_attrs(seg, nets[i]))
        (x1, y1), (x2, y2) = seg.frm_xy, seg.to_xy
        for (ex, ey) in ((x1, y1), (x2, y2)):
            svg.circle(ex, ey, PAD_R, fill="#C9CDD4", stroke=seg.color, sw=1.2)
            svg.circle(ex, ey, 1.1, fill="#2A2E36")
        if seg.publishes:
            lx, ly = _label_anchor(seg.points)
            svg.text(lx, ly - 5, seg.publishes, size=7.5, fill=seg.color, owner="wire",
                     anchor="middle", family="monospace")


# ---------------------------------------------------------------------------
# top-level: ONE spec -> the chosen substrate renderer
# ---------------------------------------------------------------------------
def _subtitle(hookup: Hookup) -> str:
    n_parts = len(hookup.parts)
    n_wires = len(hookup.wires)
    slots = ", ".join(hookup.slots) if hookup.slots else "-"
    return f"{n_parts} parts · {n_wires} wires · slots: {slots}"


def render_hookup(hookup: Hookup) -> Tuple[str, CrossingReport]:
    """Render *hookup* to ``(svg_text, crossing_report)`` — **the full result.**

    :func:`hookup_to_svg` is this function's first element and NOTHING ELSE, so
    the SVG bytes are identical either way; what this entry point adds is the
    verdict the renderer has always computed and thrown away.

    ⛔ WHY IT EXISTS.  ``plan_hops`` returns ``(hops, skipped)`` deliberately —
    ``SkippedHop``'s docstring says silence "would let an un-hopped crossing
    (which reads as a junction) pass unnoticed" — and until 2026-08-15 the single
    caller bound the second value to ``_skipped`` and dropped it.  On
    ``render/examples/tm1637_quad_panel.compose.yaml`` that was 7 real skips (6
    ``crowded``, 1 ``corner``; five piled at x=360) silently shipped on a
    perfboard guide, where an intersection that reads as a junction is a solder
    joint that must not exist.  A DELIVERY path must call this one and gate on
    ``report.ok``; a caller that only needs pixels may keep calling
    :func:`hookup_to_svg`."""
    lay = layout_hookup(hookup)
    segs = wire_segments(hookup, lay)
    nets = wire_nets(hookup)
    report = crossing_report(lay, segs)
    hops: Dict[int, List[WireHop]] = {}
    for hp in report.hops:
        hops.setdefault(hp.wire, []).append(hp)
    svg = _Svg(lay.width, lay.height)
    # ⛔ A PIN NOBODY WIRES DOES NOT COMPETE FOR LABEL SPACE (David 2026-08-15;
    # the DEFAULT, opt out per scene with `hookup: label_pins: all`).  See
    # ``hookup_layout.pin_label_plan`` for the rule and `Hookup.label_pins` for
    # why default-on is the safe direction: the PAD is still drawn and still
    # ringed, so only the redundant catalog-name overlay goes.
    wired_by_part = ({p.id: hl.wired_pins(hookup, p.id) for p in hookup.parts}
                     if hookup.label_pins == "wired" else {})

    def _art() -> None:
        for box in lay.parts.values():
            _draw_part(svg, box, wired_by_part.get(box.id))

    def _labels() -> None:
        for box in lay.parts.values():
            _draw_part_labels(svg, box, wired_by_part.get(box.id))

    if hookup.board == "breadboard":
        _draw_breadboard_bg(svg, lay)
        _draw_title(svg, hookup, _subtitle(hookup))
        _art()
        _draw_breadboard_wires(svg, segs, hops, nets)
        _labels()
    elif hookup.board == "perfboard":
        _draw_perfboard_bg(svg, lay)
        _draw_title(svg, hookup, _subtitle(hookup))
        _art()
        _draw_perfboard_wires(svg, segs, hops, nets)
        _labels()
    else:                                          # defensive; S2 already guards
        raise ValueError(f"unknown board {hookup.board!r}")

    return svg.render(), report


def hookup_to_svg(hookup: Hookup) -> str:
    """Render *hookup* to an SVG wiring guide for its declared board.

    Dispatches on ``hookup.board``: ``breadboard`` -> rails + tie-point field +
    jumper leads; ``perfboard`` -> uniform hole grid + point-to-point solder
    traces.  Both read the SAME parts + wires; only the substrate background and
    the wire semantics differ.  Returns a self-contained SVG document string
    (Canvas-lowerable subset only).

    ⚠ THIS RESULT IS INCOMPLETE FOR A DELIVERY DECISION: it discards the
    crossing verdict.  It is kept, unchanged and byte-for-byte, because most
    callers only want pixels — but anything that hands the drawing to a human
    with a soldering iron must call :func:`render_hookup` and gate on
    ``report.ok``."""
    return render_hookup(hookup)[0]


def hookup_svg_to_file(hookup: Hookup, path: str) -> str:
    """Render *hookup* and write the SVG to *path* (utf-8).  Returns *path*.

    The written file is what the doc-viewer opens: publish its repo-relative
    path to ``_app/doc/open`` and the `.svg` branch of
    `compose_projects/intelligence/tools/doc_viewer_launcher.py` spawns a viewer window that
    lowers it through the Canvas contract ([SPEC:CXC-SVG])."""
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    svg = hookup_to_svg(hookup)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return path


# ---------------------------------------------------------------------------
# RASTERIZERS — two, and the difference between them is load-bearing
#
# Both take the SAME arguments (`svg`, `out_png`, width/height/scale) so either
# can be injected wherever a rasterizer is a parameter — which is exactly how
# `composeio.decision_ops.hookup_review` fault-injects the broken one to prove
# its label gate fires, and how :func:`rasterize_with_label_proof` below gates
# whichever one it is handed.
# ---------------------------------------------------------------------------
def svgdom_rasterize(svg: str, out_png: str, *, width: float, height: float,
                     scale: float = 1.0) -> str:
    """⛔ RASTERIZE *svg* VIA skia's ``SVGDOM`` — **THIS SILENTLY DROPS EVERY TEXT
    LABEL.**  Returns *out_png*.

    skia-python's ``SVGDOM.MakeFromStream`` has no ``Builder`` overload, so the
    DOM is constructed with NO font manager: every ``<text>`` element renders to
    nothing.  There is no exception, no warning, and no missing-glyph box — the
    PNG is valid, the geometry is perfect, and the labels are simply gone.  An
    instrument that can only report success.

    KEPT, NOT DELETED, and kept PUBLIC on purpose: it is the fault injector for
    the label gate in ``decision_ops.hookup_review`` (a gate whose failure mode
    cannot be reproduced is not a tested gate).  Do NOT use it to produce an
    artifact a human will read a wiring diagram off — use
    :func:`canvas_rasterize`, which lowers through this repo's own Canvas
    contract and therefore through its font stack."""
    import os
    import skia

    vb_w, vb_h = float(width), float(height)
    w = max(1, int(round(vb_w * scale)))
    h = max(1, int(round(vb_h * scale)))

    stream = skia.MemoryStream(svg.encode("utf-8"), True)
    dom = skia.SVGDOM.MakeFromStream(stream)
    if dom is None:
        raise ValueError("skia could not parse the hookup SVG for PNG rasterization")
    dom.setContainerSize(skia.Size(vb_w, vb_h))

    surface = skia.Surface(w, h)
    with surface as canvas:
        canvas.clear(skia.ColorWHITE)
        if scale != 1.0:
            canvas.scale(scale, scale)
        dom.render(canvas)
    image = surface.makeImageSnapshot()

    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    image.save(out_png, skia.kPNG)
    return out_png


def canvas_rasterize(svg: str, out_png: str, *, width: float, height: float,
                     scale: float = 1.0) -> str:
    """Rasterize *svg* by LOWERING IT THROUGH THIS REPO'S CANVAS CONTRACT
    (``render.svg.render_svg`` onto a ``SkiaCanvas``).  Returns *out_png*.

    The labels survive here because the text goes through the engine's own font
    stack rather than skia's SVG module — the same path the `svg` widget and the
    native ``HookupWidget`` draw with, so the raster and the on-screen guide
    agree.  This is the renderer a human-facing hookup artifact must use."""
    import os
    from ..render.skia_backend import SkiaCanvas
    from ..render.svg import SvgDocument, render_svg
    from ..render.widget import Rect

    w = max(1, int(round(float(width) * scale)))
    h = max(1, int(round(float(height) * scale)))
    canvas = SkiaCanvas()
    canvas.create_surface(w, h)
    canvas.set_fill_color(255, 255, 255, 255)
    canvas.fill_rect(0, 0, w, h)
    render_svg(canvas, SvgDocument.from_string(svg), Rect(0, 0, w, h),
               fit="stretch")
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    canvas.save_png(out_png)
    return out_png


# ---------------------------------------------------------------------------
# ⛔ THE LABEL-SURVIVAL CHECK — ONE IMPLEMENTATION, N CONSUMERS
#
# This block is the SINGLE definition of "did the labels reach the pixels?".
# Two consumers ride it and neither reimplements it:
#
#   * :func:`rasterize_with_label_proof` — the RENDERER's own gate, so a mute
#     PNG cannot be produced at all (raises, and leaves no file).
#   * ``decision_ops.hookup_review.label_survival`` — the REVIEW ENDPOINT's
#     gate, which needs the measurement as a report so it can refuse to publish
#     and annotate a sparse-but-present result.
#
# WHY IT IS WRITTEN ONCE (2026-08-15).  It was written TWICE, hours apart, by
# two agents that could not see each other: once here, once in the endpoint.
# That is the third instance of the same failure — `[BP-TYPE-CODES]` collapsed
# four drifting copies of the message-type table after every copy ended in a
# `.get(type, 0)` and an ACK silently arrived as a STATE, and the value codec
# repeated it one layer down.  Concurrent dispatch MANUFACTURES forks unless the
# shared thing is NAMED.  The naming is the prevention; this comment is the name.
# ---------------------------------------------------------------------------
_TEXT_EL = re.compile(r"<text\b[^>]*/>|<text\b.*?</text>", re.S | re.I)

# A raster is only "labelled" if stripping the text CHANGES it.  The HARD gate
# is `ink_px == 0` — total loss, the known defect's binary signature.  This
# floor only ADDS a note: a partial-drop threshold would be arbitrary and would
# false-refuse a legitimately sparse diagram.
SPARSE_LABEL_RATIO = 0.0005


def count_text_elements(svg: str) -> int:
    """How many NON-EMPTY ``<text>`` elements *svg* declares."""
    n = 0
    for m in _TEXT_EL.finditer(svg):
        if re.sub(r"<[^>]*>", "", m.group(0)).strip():
            n += 1
    return n


def strip_text(svg: str) -> str:
    """*svg* with every ``<text>`` element removed — the reference document for
    the label-survival comparison.  Pure."""
    return _TEXT_EL.sub("", svg)


def _raster_pixels(png_path: str):
    """*png_path*'s raw RGBA array.  Lazy — skia only on the gate path."""
    import skia
    img = skia.Image.open(png_path)
    return img.toarray(colorType=skia.ColorType.kRGBA_8888_ColorType)


def _ink_vs_reference(rasterize, reference_svg: str, drawn_png: str, *,
                      width: float, height: float, scale: float
                      ) -> Tuple[int, int]:
    """⛔ THE DIFFERENTIAL — **the one comparison**, shared by every survival
    check in this module.  Returns ``(ink_px, total_px)``.

    Rasterizes *reference_svg* (the delivered document with ONE feature removed)
    THROUGH THE SAME *rasterize* callable that produced *drawn_png*, and counts
    the pixels that differ.  Zero means the removed feature contributed exactly
    nothing to the delivered image.

    RENDERER-AGNOSTIC BY CONSTRUCTION: both sides go through the same
    rasterizer, so there is no OCR, no font or geometry knowledge, no golden
    image, and no false verdict from antialiasing differences between two
    renderers.  It fires on ANY renderer that loses the feature — including one
    not yet written.

    Written ONCE on purpose.  The label check and the hop check are two features
    over ONE measurement; a second copy of this loop is exactly the fork
    ``[BP-TYPE-CODES]`` has already collapsed three times in this codebase.  The
    probe is a temp file and is always removed."""
    import os
    import tempfile

    fd, probe = tempfile.mkstemp(prefix="inkproof-", suffix=".png")
    os.close(fd)
    try:
        rasterize(reference_svg, probe, width=width, height=height, scale=scale)
        drawn, without = _raster_pixels(drawn_png), _raster_pixels(probe)
    finally:
        if os.path.exists(probe):
            os.remove(probe)

    if drawn.shape != without.shape:
        ink = int(drawn.size)          # a rasterizer that is not a pure
    else:                              # function of the doc — assume ink
        import numpy as np             # noqa: F401  (used via ndarray ops)
        ink = int((drawn != without).any(axis=-1).sum())
    total = max(1, int(drawn.shape[0]) * int(drawn.shape[1]))
    return ink, total


_MUTE_REASON = (
    "the raster is IDENTICAL with all {declared} text elements removed — the "
    "labels did not reach the pixels. A label-free wiring diagram is not a "
    "wiring diagram. (Rasterizer: {who!r}.) {remedy}"
)

# The remedy is CONDITIONAL on which rasterizer was used, because a refusal that
# says "render through canvas_rasterize" when canvas_rasterize is exactly what
# just failed sends the reader somewhere they already are — and a reader who
# cannot act on a refusal starts building their own instrument, which is how
# this check came to exist twice.
_REMEDY_SVGDOM = (
    "The classic cause is skia's SVGDOM, which skia-python builds with NO font "
    "manager (there is no `Builder` overload), so every <text> renders to "
    "nothing, with no error and no missing-glyph box. Render through the Canvas "
    "contract (composeio.maker.hookup_svg.canvas_rasterize) instead."
)
_REMEDY_CANVAS = (
    "This is ALREADY the Canvas contract, so the usual remedy does not apply: "
    "the engine's own font stack drew no glyphs. Check that `SkiaCanvas."
    "draw_text` is not stubbed or short-circuited, that the SVG's <text> names a "
    "resolvable font-family, and that a font is actually loadable in this "
    "process."
)

_NO_TEXT_REASON = (
    "the SVG declares NO text elements at all — unlabelled at the SOURCE, "
    "which is an upstream renderer regression and not a rendering choice."
)


def label_ink_report(rasterize, svg: str, out_png: str, *,
                     width: float, height: float,
                     scale: float = 1.0) -> Dict[str, object]:
    """DID THE LABELS SURVIVE RASTERIZATION?  **The one implementation.**

    Rasterizes *svg* to *out_png* and :func:`strip_text` of *svg* to a throwaway
    probe THROUGH THE SAME *rasterize* callable, and counts the pixels that
    differ.  Zero differing pixels means the text contributed exactly nothing.

    RENDERER-AGNOSTIC BY CONSTRUCTION.  Both sides go through whichever
    rasterizer produced the delivered image, so there is no OCR, no font
    knowledge, no golden image to regenerate, and no false verdict from
    antialiasing differences between two different renderers.  It fires on ANY
    renderer that loses text — including one not yet written — rather than on
    one known skia bug.

    ⛔ A MUTE RASTER IS DELETED, NOT RETURNED.  When ``ok`` is False the output
    file is removed and its path is reported under ``removed`` instead of
    ``png``: a mute PNG left on disk is exactly the artifact somebody opens by
    hand later, and it is indistinguishable from a good one until you read it.

    Returns ``{in_svg, ink_px, ratio, ok, sparse, png, removed, rasterizer,
    reason}``.  Never raises for a *label* verdict — the caller decides whether
    that verdict is a refusal (:func:`rasterize_with_label_proof`) or a report
    (``hookup_review.label_survival``).  Rasterizer failures still propagate."""
    import os

    who = getattr(rasterize, "__name__", rasterize)
    declared = count_text_elements(svg)
    if not declared:
        # Nothing is rasterized: there is no question left to ask, and writing
        # the file first only to delete it is how the earlier version left a
        # mute PNG behind on this branch.
        return {"in_svg": 0, "ink_px": 0, "ratio": 0.0, "ok": False,
                "sparse": False, "png": "", "removed": "",
                "rasterizer": str(who), "reason": _NO_TEXT_REASON}

    path = rasterize(svg, out_png, width=width, height=height, scale=scale)
    ink, total = _ink_vs_reference(rasterize, strip_text(svg), path,
                                   width=width, height=height, scale=scale)
    ratio = ink / float(total)
    ok = ink > 0
    if not ok and os.path.exists(path):
        os.remove(path)
    return {
        "in_svg": declared,
        "ink_px": ink,
        "ratio": round(ratio, 6),
        "ok": ok,
        "sparse": bool(ok and ratio < SPARSE_LABEL_RATIO),
        "png": path if ok else "",
        "removed": "" if ok else path,
        "rasterizer": str(who),
        "reason": "" if ok else _MUTE_REASON.format(
            declared=declared, who=who,
            remedy=(_REMEDY_CANVAS if rasterize is canvas_rasterize
                    else _REMEDY_SVGDOM)),
    }


def rasterize_with_label_proof(rasterize, svg: str, out_png: str, *,
                               width: float, height: float,
                               scale: float = 1.0) -> str:
    """Rasterize *svg* through *rasterize* and REFUSE if the labels made no
    pixels.  Returns *out_png*; raises ``RuntimeError`` on a mute raster, and
    leaves NO file behind when it does.

    This is :func:`label_ink_report`'s verdict turned into a REFUSAL — the
    renderer's own gate, so a mute PNG cannot be produced at all.  (The review
    endpoint rides the same report and turns the same verdict into a refusal to
    PUBLISH; see ``decision_ops.hookup_review.label_survival``.)"""
    rep = label_ink_report(rasterize, svg, out_png, width=width, height=height,
                           scale=scale)
    if not rep["ok"]:
        raise RuntimeError(
            f"rasterize_with_label_proof: {rep['reason']} "
            f"Refusing to write {out_png!r}.")
    return str(rep["png"])


# ---------------------------------------------------------------------------
# ⛔ THE HOP-SURVIVAL CHECK — the label check's SHAPE, a different feature
#
# The label gate exists because `svgdom_rasterize` drops every <text> and
# returns a valid PNG.  Nobody built the equivalent for the BRIDGES, and the
# asymmetry was load-bearing: a label-stripped render is refused, while an
# arc-stripped one sailed through the same gate.  Both losses are the SAME
# defect — a crossing with no bridge reads as a junction, exactly what
# `plan_hops` exists to prevent — and the second one is arguably worse, because
# the drawing still looks complete.
#
# It is NOT a fork of the label check: the measurement is
# `_ink_vs_reference`, shared, and only the "feature removed" differs
# (`strip_text` there, `strip_hops` here).
#
# TWO ASYMMETRIES, both deliberate and both documented on the report:
#   * ZERO ARCS IS FINE HERE.  A diagram whose conductors never cross declares
#     no bridges and loses none — `ok` is True.  (Zero <text> is NOT fine: an
#     unlabelled hookup guide is an upstream regression.)  The gate against
#     "the arcs are missing because the PLANNER skipped them" is a different
#     instrument entirely — `CrossingReport.ok`, gated in the delivery path.
#   * SPARSE IS PER-BRIDGE, not a fraction of the page.  Fewer changed pixels
#     than there are declared bridges means bridges are individually vanishing;
#     that floor needs no arbitrary constant.
# ---------------------------------------------------------------------------
# Conductors are MARKED (`data-wire`), and the scoping to them is NOT cosmetic:
# footprint art contributes arcs of its own (8 of the TM1637 panel's 69), so a
# document-wide arc count would be inflated AND — far worse — a document-wide
# strip would let ART pixels satisfy the differential.  A check that can pass
# because the artwork moved is a check that can only report success.
_CONDUCTOR_EL = re.compile(r'<path\b[^>]*\bdata-wire="[^"]*"[^>]*/>', re.S)
_D_ATTR = re.compile(r'(\sd=")([^"]*)(")')
# `A rx ry x-rot large-arc sweep x y` as `_poly_d` emits it.
_ARC_CMD = re.compile(
    r"\s*A\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[01]\s+[01]\s+([-\d.]+)\s+([-\d.]+)")


def _map_conductor_paths(svg: str, fn) -> str:
    """*svg* with *fn* applied to the `d` of every MARKED conductor path."""
    def _one(m: "re.Match[str]") -> str:
        el = m.group(0)
        return _D_ATTR.sub(lambda d: d.group(1) + fn(d.group(2)) + d.group(3),
                           el, count=1)
    return _CONDUCTOR_EL.sub(_one, svg)


def count_hop_arcs(svg: str) -> int:
    """How many LINE-HOP arcs *svg*'s conductors declare.

    Counted only inside `data-wire` paths, so footprint art cannot inflate it.
    Equals ``len(render_hookup(hookup)[1].hops)`` for a document this module
    produced — measured 57/57 on the TM1637 quad panel."""
    n = 0
    for m in _CONDUCTOR_EL.finditer(svg):
        d = _D_ATTR.search(m.group(0))
        if d:
            n += len(_ARC_CMD.findall(d.group(2)))
    return n


def strip_hops(svg: str) -> str:
    """*svg* with every conductor's LINE HOPS flattened — the reference document
    for the hop-survival comparison.  Pure.

    Each arc becomes a straight `L` to the SAME endpoint, so the conductor stays
    a valid, closed, identically-terminated polyline and the ONLY difference
    from the delivered document is the bridge itself.  (Deleting the command
    instead would break the path and make the differential measure the
    breakage.)"""
    return _map_conductor_paths(
        svg, lambda d: _ARC_CMD.sub(lambda a: f" L {a.group(1)} {a.group(2)}", d))


_NO_HOPS_REASON = (
    "the SVG's conductors declare NO line hops — this diagram has no drawn "
    "crossings to lose. Unlike a diagram with no TEXT, that is legitimate. "
    "Whether crossings were skipped by the PLANNER is a different question, "
    "answered by CrossingReport.ok, not by this raster check."
)
_FLAT_REASON = (
    "the raster is IDENTICAL with all {declared} line-hop arcs flattened to "
    "straight runs — the bridges did not reach the pixels. Every one of those "
    "{declared} crossings then reads as a JUNCTION, which on a wiring guide is "
    "a solder joint that must not exist. (Rasterizer: {who!r}.) The classic "
    "cause is an SVG consumer that does not implement the elliptical-arc `A` "
    "command and silently skips it; check that the renderer lowers `A` to the "
    "Canvas contract's ArcTo ([SPEC:CXC-SVG])."
)


def hop_ink_report(rasterize, svg: str, out_png: str, *,
                   width: float, height: float, scale: float = 1.0,
                   drawn_png: str = "") -> Dict[str, object]:
    """DID THE LINE HOPS SURVIVE RASTERIZATION?  The label check's sibling.

    Rasterizes *svg* and :func:`strip_hops` of *svg* through the SAME
    *rasterize* callable and counts the differing pixels (via the shared
    :func:`_ink_vs_reference`).  Zero means the bridges contributed nothing and
    every drawn crossing in the delivered image reads as a junction.

    *drawn_png* — an ALREADY-RENDERED delivered raster to measure against,
    which must have come from THIS *rasterize* at THIS width/height/scale (the
    review endpoint passes the PNG the label gate just wrote, so the full
    document is rasterized once instead of twice).  Omit it and this function
    renders *out_png* itself.

    ⛔ A HOPLESS RASTER IS DELETED, NOT RETURNED — same rule, same reason as
    :func:`label_ink_report`: an artifact somebody opens by hand later is
    indistinguishable from a good one until they read it.  Returns the same
    ``{in_svg, ink_px, ratio, ok, sparse, png, removed, rasterizer, reason}``
    shape, so a consumer handles both verdicts with one code path."""
    import os

    who = getattr(rasterize, "__name__", rasterize)
    declared = count_hop_arcs(svg)
    reuse = bool(drawn_png) and os.path.exists(drawn_png)
    if not declared:
        # Nothing to lose. Do NOT rasterize: writing a file only to keep it is
        # the caller's business, and this branch must not manufacture one.
        return {"in_svg": 0, "ink_px": 0, "ratio": 0.0, "ok": True,
                "sparse": False, "png": drawn_png if reuse else "",
                "removed": "", "rasterizer": str(who),
                "reason": _NO_HOPS_REASON}

    path = (drawn_png if reuse else
            rasterize(svg, out_png, width=width, height=height, scale=scale))
    ink, total = _ink_vs_reference(rasterize, strip_hops(svg), path,
                                   width=width, height=height, scale=scale)
    ratio = ink / float(total)
    ok = ink > 0
    if not ok and os.path.exists(path):
        os.remove(path)
    return {
        "in_svg": declared,
        "ink_px": ink,
        "ratio": round(ratio, 6),
        "ok": ok,
        # per-BRIDGE, not per-page: fewer changed pixels than declared bridges
        # means individual bridges are vanishing even though some survived.
        "sparse": bool(ok and ink < declared),
        "png": path if ok else "",
        "removed": "" if ok else path,
        "rasterizer": str(who),
        "reason": "" if ok else _FLAT_REASON.format(declared=declared, who=who),
    }


def rasterize_with_hop_proof(rasterize, svg: str, out_png: str, *,
                             width: float, height: float, scale: float = 1.0,
                             drawn_png: str = "") -> str:
    """:func:`hop_ink_report`'s verdict turned into a REFUSAL — the renderer's
    own gate, so a bridge-free raster of a bridged diagram cannot be produced.
    Returns the PNG path; raises ``RuntimeError`` and leaves NO file on loss."""
    rep = hop_ink_report(rasterize, svg, out_png, width=width, height=height,
                         scale=scale, drawn_png=drawn_png)
    if not rep["ok"]:
        raise RuntimeError(
            f"rasterize_with_hop_proof: {rep['reason']} "
            f"Refusing to write {out_png!r}.")
    return str(rep["png"])


def hopless_rasterize(svg: str, out_png: str, *, width: float, height: float,
                      scale: float = 1.0) -> str:
    """⛔ THE FAULT INJECTOR FOR THE HOP GATE — rasterizes *svg* WITH EVERY LINE
    HOP FLATTENED, through the honest :func:`canvas_rasterize`.

    The label gate has a REAL broken renderer to test itself against
    (:func:`svgdom_rasterize`).  The hop gate has no known one, and a gate whose
    failure mode cannot be reproduced is not a tested gate — so this synthesises
    exactly the loss: a valid, complete-looking PNG in which every crossing has
    become a plain intersection.  Public for the same reason ``svgdom_rasterize``
    is: the negative control has to be importable by every consumer that gates.

    ⛔ NEVER deliver its output. It is a lie by construction."""
    return canvas_rasterize(strip_hops(svg), out_png, width=width,
                            height=height, scale=scale)


def hookup_svg_to_png(hookup: Hookup, path: str, *, scale: float = 1.0) -> str:
    """Rasterize *hookup*'s guide SVG to a PNG at *path* (HEADLESS).  Returns *path*.

    A pixel PROOF the compositing pass placed every part's real footprint art —
    and, since 2026-08-15, that the LABELS are on it.

    This used to delegate to :func:`svgdom_rasterize` and therefore returned a
    valid, well-formed, entirely label-free PNG with no error: 78 text elements,
    zero pixels, and a hookup guide with no pin names on it.  It now lowers
    through :func:`canvas_rasterize` (the engine's own SVG -> Canvas path, the
    same one the doc-viewer shows, so the artifact and the screen are one
    pipeline instead of two backends that disagree about text) and is GATED by
    :func:`rasterize_with_label_proof`, which REFUSES rather than writing a mute
    PNG.  The failure is no longer possible to ship from here.

    GATED TWICE since 2026-08-15: the labels, then the LINE HOPS
    (:func:`rasterize_with_hop_proof`).  The second gate closes the asymmetry
    that let an arc-stripped render sail through the one that catches a
    text-stripped one — the bridges are the only thing keeping a crossing from
    reading as a solder joint.  The hop gate re-uses the PNG the label gate just
    wrote, so the full document is still rasterized ONCE.

    ⚠ WHAT NEITHER GATE ANSWERS: whether the PLANNER skipped a crossing.  These
    prove the drawn bridges reached the pixels; a crossing skipped upstream has
    no arc to lose and is invisible here BY CONSTRUCTION.  That question is
    ``render_hookup(hookup)[1].ok``, gated in the delivery path
    (``decision_ops.hookup_review``), and this function deliberately does NOT
    refuse on it — it is the artifact-build entry point, and a build that cannot
    produce the picture cannot show an author what to re-route.

    ``scale`` supersamples the raster for a crisp doc image (2.0 = double
    resolution)."""
    lay = layout_hookup(hookup)
    svg = hookup_to_svg(hookup)
    w, h = float(lay.width), float(lay.height)
    png = rasterize_with_label_proof(canvas_rasterize, svg, path,
                                     width=w, height=h, scale=scale)
    return rasterize_with_hop_proof(canvas_rasterize, svg, path,
                                    width=w, height=h, scale=scale,
                                    drawn_png=png)


# ---------------------------------------------------------------------------
# doc-viewer scene wrapper — host the SVG in an `svg` widget
# ---------------------------------------------------------------------------
def hookup_viewer_scene(svg_path: str, *, title: str = "Hookup",
                        width: int = 900, height: int = 900) -> dict:
    """A self-contained ComposeUX scene dict that renders *svg_path* through the
    `svg` widget (pan/zoom on).  This is what the doc-viewer launcher's `.svg`
    branch bakes + spawns so a hookup SVG opens natively (no external app)."""
    src = svg_path
    if "://" not in src:
        src = "file://" + src
    return {
        "app": {"id": "hookup-viewer", "transports": []},
        "design": {"title": f"Hookup · {title}", "target": "desktop",
                   "width": width, "height": height, "theme": "dark",
                   "padding": 0, "gap": 0},
        "widgets": [
            {"type": "pixel_stack", "id": "frame", "width": width,
             "height": height, "background": "#0A0E17", "border": "#1E2B3C",
             "corner_radius": 0, "clip": True, "children": [
                 {"type": "label", "id": "header", "x": 20, "y": 15,
                  "text": title, "font_size": 14, "font_weight": "bold",
                  "color": "#EAF0F7", "font_family": "monospace"},
                 {"type": "label", "id": "badge", "x": 20, "y": 37,
                  "text": "HOOKUP GUIDE · READ-ONLY", "font_size": 9,
                  "color": "#5C6E82", "font_family": "monospace"},
                 {"type": "svg", "id": "hookup", "x": 10, "y": 56,
                  "width": width - 20, "height": height - 66,
                  "src": src, "fit": "contain", "pan_zoom": True},
             ]},
        ],
    }


# ===========================================================================
# ⛔ THE LEGIBILITY GATE — PRESENCE IS NOT READABILITY (David 2026-08-15)
#
# THE FAILURE THIS EXISTS FOR.  A hookup guide shipped with EVERY GATE GREEN:
#   67 crossings bridged, 0 skipped   labels: in_svg 178, ink_px 59153, ok true
#   67 hop arcs in the raster         hops:   in_svg  67, ink_px 19103, ok true
# and, additionally measured, "0 body overlaps".  David opened it: "the
# fingerprint sensor placement is so close to the pins that the wiring is
# unreadable."  He was right.  `fp` printed across `mcu`, its caption over the
# MCU's caption, and both edge columns carried DOUBLED pin names.
#
# ⛔ SO THERE WERE TWO DEFECTIVE INSTRUMENTS AND ONE MISSING ONE, AND THE
# MISSING ONE IS THE POINT:
#
#  1. `label_survival` PROVES LABELS EXIST AND CAN NEVER PROVE THEY ARE
#     READABLE.  178 of 178 present — and colliding.  It is a differential
#     against a text-STRIPPED render, so it answers "did any text reach the
#     pixels", and 178 labels printed on one spot answer it YES.  That
#     instrument is not wrong; it was ASKED THE WRONG QUESTION and its answer
#     was read as the other one.
#  2. The body-overlap check reported 0 against two visibly overlapping parts,
#     because `mcu` and `fp` are EXACTLY TANGENT (both extents meet at y=862.4,
#     clearance 0.0) and a strict interior-intersection test is correct and
#     useless there.  ``hookup_layout.PART_CLEARANCE`` documents the real
#     defect: the router's own clearance rule was never applied to
#     `at:`-anchored parts.
#  3. NOTHING measured collisions.  This does.
#
# THIS IS THE CROSSING GATE'S SHAPE, one layer up.  An un-hopped crossing reads
# as a junction; an overprinted label reads as a different word.  Both are a
# drawing that says something false while every signal about it is green, and
# both refuse with COORDINATES so the refusal is a work order.
#
# ⛔ IT MEASURES THE DOCUMENT, NOT THE DRAW CODE.  Label boxes come from the
# emitted `<text>` elements, measured through the ENGINE'S OWN font metrics —
# the same `set_font` + `measure_text` path `canvas_rasterize` uses to put them
# on the glass — so it cannot drift from the renderer by construction, and it
# would still catch a collision introduced by a draw rule nobody has written
# yet.  Part extents come from ``hookup_layout.part_extent``.
#
# ⛔ AND A FOURTH CLASS, ADDED 2026-08-16 — A CONDUCTOR UNDER A PART BODY.
# David, on the second machine-green delivery: *"I COULD use this diagram, but
# it's really not quite right — see how the FP component and its wires overlay
# the gnd for tm2 and tm4."*  Measured on the scene as delivered: the
# `mcu.GND -> tm2.GND` and `mcu.GND -> tm4.GND` trunks (and both 5 V runs) each
# ran the FULL 134.2 px width of `fp`'s extent, at y=858.0 — 4.4 px above the
# sensor's six pads, straight through its pin band and its captions.
#
# ⛔ THIS IS A DIFFERENT CLASS FROM A LABEL GRAZING A WIRE, AND IT IS HARD.
# `plan_hops` bridges wire x wire so a crossing READS as a crossing.  A
# conductor passing under an OPAQUE PART BODY gets no such treatment: it cannot
# be hopped and it cannot be followed, and the reader loses the conductor for
# the width of the part.  On a wiring guide "where does this ground go" is
# precisely the question the drawing exists to answer, so this is the
# un-hopped-crossing failure ONE LAYER UP — and it is kept strictly separate
# from the `label_wire` NOTE below.  ⛔ The soft bucket was NOT widened to
# absorb it and MUST NOT BE: a bar that is unreachable gets relaxed until it
# means nothing, and this bar is reachable.
#
# ⚠ WHY THE ROUTER DID NOT ALREADY PREVENT IT — the gap is one rectangle wide.
# `_assign_routes` rejects any polyline crossing a part's ``body`` (the opaque
# slab).  A GENERATED horizontal part draws its slab ABOVE its pin row, so the
# band between the slab and the pads — where the pads, the pin stubs and every
# pin caption live — is outside ``body`` and inside ``part_extent``.  The router
# was truthful about the slab and blind to the band.  The gate measures the
# EXTENT, so it sees what the reader sees.
#
# ⚠ AND IT STILL CANNOT CLOSE THE QUESTION.  Zero collisions means no two
# things are printed on each other.  It does not mean the drawing is legible —
# density, contrast, colour and crossing-count all live outside it.
# `[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]`: this gate can only ever
# refuse; a PASS is a precondition for asking David, never a substitute.
# ===========================================================================
_LABEL_PAD = 1.0        # px of slack: touching bounding boxes still read apart
#: Label boxes closer than this leave a NOTE, not a refusal — a near miss is
#: worth surfacing to a human without blocking a drawing on taste.
CROWDED_LABEL_GAP = 2.0

#: Half the DRAWN conductor stroke (``sw=4.8`` in ``_draw_perfboard_wires``),
#: plus 0.2 px of slack.  ⛔ ONE constant, two callers — the ``label_wire`` note
#: and the ``body_wire`` refusal both ask "does the conductor AS DRAWN share
#: space with this", and a second copy of the number is how the gate and the
#: renderer drift apart.  It is also what makes the tangent case a FAILURE: a
#: trunk whose centre line grazes a part's extent still prints 2.6 px of copper
#: on it, which is the same mistake as the 0.0-clearance body overlap that
#: shipped.
CONDUCTOR_HALF_W = 2.6


def _measure_fn() -> Callable[[str, float, str, str], Tuple[float, float, float]]:
    """``(text, size, family, weight) -> (width, ascent, descent)`` through the
    ENGINE's own text stack — the exact deterministic integer-advance path
    ``canvas_rasterize`` draws with, so a measured box is the drawn box."""
    from composeio.render.skia_backend import SkiaCanvas
    c = SkiaCanvas()
    c.create_surface(8, 8)

    def measure(text: str, size: float, family: str, weight: str):
        c.set_font(family, size, weight)
        m = c.measure_text(text)
        return (m.width, m.ascent, m.descent)
    return measure


def _text_boxes(svg: str, measure) -> List[Dict[str, Any]]:
    """Every LABEL as an axis-aligned box in board coordinates.

    ⛔ ``data-owner`` IS THE MEMBERSHIP TEST, and the first draft of this
    function got it wrong in a way worth recording.  It assumed every ``<text>``
    in the document was a label.  It is not: the inlined Fritzing art carries
    its own silkscreen (``TM1637 · 4-DIGIT``, the unlit digits, ``VDD 5V —
    headers A/B are the SAME four nets``) inside a ``<g transform>``, in the
    ART's user space.  Measuring those produced 133 label-on-label rows made
    almost entirely of pre-transform coordinates compared against board
    coordinates — an instrument loud enough to look thorough and wrong enough
    to be useless.  Art text is ARTWORK: it is inside the part and already
    accounted for by that part's extent.  Only runs the guide itself drew — the
    ones carrying an owner — are labels.

    Anchor is applied, then a turned label's box is rotated about its own
    baseline point (exact for the 90-degree case, the only rotation emitted)."""
    out: List[Dict[str, Any]] = []
    for m in _TEXT_EL.finditer(svg):
        el = m.group(0)
        body = re.sub(r"<[^>]+>", "", el).strip()
        if not body or 'data-owner="' not in el:
            continue

        def attr(name: str, default: str = "") -> str:
            a = re.search(rf'\b{name}="([^"]*)"', el)
            return a.group(1) if a else default
        x = float(attr("x", "0") or 0)
        y = float(attr("y", "0") or 0)
        size = float(attr("font-size", "10") or 10)
        family = attr("font-family", "monospace")
        weight = attr("font-weight", "normal")
        anchor = attr("text-anchor", "start")
        owner = attr("data-owner", "")
        stack = attr("data-stack", "")
        rot = 0.0
        tr = attr("transform")
        r = re.search(r"rotate\(\s*(-?[\d.]+)", tr)
        if r:
            rot = float(r.group(1))
        w, asc, desc = measure(html.unescape(body), size, family, weight)
        # unrotated box relative to the baseline point
        dx = {"middle": -w / 2.0, "end": -w}.get(anchor, 0.0)
        x0, y0, x1, y1 = dx, -asc, dx + w, desc
        if abs(rot) > 1e-6:
            if abs(abs(rot) - 90.0) > 1e-6:            # defensive: only 90 emitted
                raise ValueError(f"legibility_report: unsupported rotation {rot}")
            # SVG rotate(a): (x, y) -> (x cos a - y sin a, x sin a + y cos a).
            # At -90 that is (y, -x); at +90, (-y, x).  ⚠ THE SIGN ON THE
            # SECOND COMPONENT IS LOAD-BEARING AND THE FIRST DRAFT HAD IT
            # BACKWARDS, which measured every downward-hanging label 24 px
            # ABOVE where it draws — a gate reading the wrong rectangle, which
            # can hide a real collision as easily as invent one.  Pinned
            # against actual ink by `test_rotated_label_box_matches_the_ink`.
            s = -1.0 if rot < 0 else 1.0
            xs = [-s * y0, -s * y1]
            ys = [s * x0, s * x1]
            x0, x1 = min(xs), max(xs)
            y0, y1 = min(ys), max(ys)
        out.append({"text": body, "owner": owner, "stack": stack,
                    "box": (x + x0, y + y0, x + x1, y + y1)})
    return out


def _gap(a, b) -> float:
    """Separation between two (x0,y0,x1,y1) boxes; NEGATIVE means overlap
    (magnitude = the smaller penetration axis)."""
    dx = max(a[0] - b[2], b[0] - a[2])
    dy = max(a[1] - b[3], b[1] - a[3])
    if dx >= 0 or dy >= 0:
        return max(dx, dy)
    return max(dx, dy)          # both negative -> shallower penetration


def _wire_boxes(segs) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """Each conductor SEGMENT as a fattened box (half a stroke either side)."""
    half = CONDUCTOR_HALF_W
    out = []
    for i, seg in enumerate(segs):
        for (ax, ay), (bx, by) in zip(seg.points, seg.points[1:]):
            out.append((str(i), (min(ax, bx) - half, min(ay, by) - half,
                                 max(ax, bx) + half, max(ay, by) + half)))
    return out


def conductor_body_runs(layout: BoardLayout, segs) -> List[Dict[str, Any]]:
    """⛔ EVERY PLACE A CONDUCTOR IS DRAWN ACROSS A PART IT DOES NOT TERMINATE ON.

    One row per (wire, part) pair, carrying the RUN LENGTH in pixels and the
    coordinate where the conductor disappears under the part — a refusal has to
    be a work order, and "the drawing is not quite right" is a dead end.

    ⛔ THE RECTANGLE IS ``part_extent``, NOT ``body``, AND THAT IS THE WHOLE
    FINDING.  The router already refuses to route across a part's ``body``; a
    generated horizontal part hangs its pads BELOW that slab, so the pin band —
    pads, stubs, captions — is outside the rectangle the router protects.  Four
    conductors ran the full width of the R503 through exactly that band with
    every gate green.  The extent is the union of the slab and every pad, which
    is what a reader sees as "the part".

    ⛔ THE OWN-PART EXEMPTION IS TOTAL, DELIBERATELY, AND IT IS NAMED RATHER
    THAN QUIETLY ASSUMED.  A wire that TERMINATES on a part is exempt from that
    part: the reader does not lose it there, it ENDS there, and a lead has to
    reach its own pad through its own pin band.  The failure this class exists
    for is a conductor the reader cannot FOLLOW.  What stops a lead from raking
    across its own part instead is a DIFFERENT rule that already holds — the
    router's ``_polyline_body_clear``, bounded by ``_exit_budget`` — so the slab
    is covered there and the band is covered here, and neither is covered twice.

    The conductor is measured AS DRAWN: the extent is grown by
    ``CONDUCTOR_HALF_W`` so a trunk whose centre line merely grazes the part
    still counts, because 2.6 px of copper is printed on it.  ⚠ Geometry is
    ``hookup_layout._seg_rect_overlap`` — the router's own predicate, not a
    second copy that can disagree with it."""
    ext = {pid: hl.part_extent(box) for pid, box in layout.parts.items()}
    rows: List[Dict[str, Any]] = []
    for seg in segs:
        own = {seg.frm.split(".", 1)[0], seg.to.split(".", 1)[0]}
        for pid, (x, y, w, h) in sorted(ext.items()):
            if pid in own:
                continue
            grown = (x - CONDUCTOR_HALF_W, y - CONDUCTOR_HALF_W,
                     w + 2 * CONDUCTOR_HALF_W, h + 2 * CONDUCTOR_HALF_W)
            run = 0.0
            at: Optional[Tuple[float, float]] = None
            for a, b in zip(seg.points, seg.points[1:]):
                ov = hl._seg_rect_overlap(a, b, grown)
                if ov <= 1e-6:
                    continue
                run += ov
                if at is None:
                    at = (max(min(a[0], b[0]), grown[0]),
                          max(min(a[1], b[1]), grown[1]))
            if run > 1e-6:
                rows.append({
                    "part": pid,
                    "wire": f"{seg.frm} -> {seg.to}",
                    "role": seg.role,
                    "at": [round(at[0], 1), round(at[1], 1)] if at else None,
                    "run": round(run, 1),
                    "part_width": round(w, 1),
                })
    return rows


def legibility_report(svg: str, layout: BoardLayout, *,
                      measure=None, segs=None) -> Dict[str, Any]:
    """CAN THE DRAWING BE READ?  Three collision classes, coordinates included.

    * ``label_label`` — two label boxes printed on each other.  178 present and
      178 colliding is what "labels: ok true" looked like.
    * ``label_body`` — a label printed across a part it does not belong to.
      A part's OWN labels over its OWN body are exempt: a generated part draws
      its id and pin names on its slab deliberately.  Attribution comes from
      the ``data-owner`` the renderer emits, so this is not a guess.
    * ``body_body`` — two parts closer than ``PART_CLEARANCE``, the clearance
      the router already enforces on nestled parts and never enforced on
      `at:`-anchored ones.  Reported as CLEARANCE, so the tangent case that
      fooled the previous check (gap exactly 0.0) is a failure, not a pass.
    * ``body_wire`` — a conductor drawn ACROSS a part it does not terminate on
      (:func:`conductor_body_runs`).  Requires *segs*.  ⛔ HARD, and kept
      strictly apart from the ``label_wire`` note below: a label grazing a wire
      is dense, while a conductor vanishing under an opaque body is UNFOLLOWABLE
      — the un-hopped-crossing failure one layer up, and unlike a crossing it
      cannot be bridged.  David, 2026-08-16: *"see how the FP component and its
      wires overlay the gnd for tm2 and tm4."*

    ``ok`` is False iff any class has a row.  ``crowded`` rows are near misses
    (< ``CROWDED_LABEL_GAP`` apart, not overlapping) and are a NOTE only.

    ⚠ ``label_wire`` IS A NOTE, NOT A VERDICT, AND THE REASON MATTERS.  Pass
    *segs* (``wire_segments(hookup, layout)``) and every label sharing space
    with a conductor is counted.  It is not a refusal because on a board with
    26 wires a label crossing SOME conductor is unavoidable, and a gate with an
    unreachable bar gets relaxed until it means nothing.  It is reported
    because the labels were, at one point, drawn UNDERNEATH the wires and the
    MCU's entire top row vanished under the ground rail with every other signal
    green — the z-order fix in `_draw_part_labels` is what makes an overlap
    merely dense rather than a deletion, and this count is how a human sees how
    dense.  ⛔ IT IS NOT A LEGIBILITY VERDICT: only David's eyes are."""
    measure = measure or _measure_fn()
    labels = _text_boxes(svg, measure)
    extents = {pid: hl.part_extent(box) for pid, box in layout.parts.items()}
    bodies = {pid: (x, y, x + w, y + h) for pid, (x, y, w, h) in extents.items()}

    label_label: List[Dict[str, Any]] = []
    crowded: List[Dict[str, Any]] = []
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            a, b = labels[i], labels[j]
            if (a["stack"] and a["stack"] == b["stack"]
                    and a["owner"] == b["owner"]):
                continue          # two lines of ONE deliberate caption stack
            g = _gap(a["box"], b["box"])
            row = {"a": a["text"], "a_owner": a["owner"],
                   "b": b["text"], "b_owner": b["owner"],
                   "at": [round(a["box"][0], 1), round(a["box"][1], 1)],
                   "gap": round(g, 2)}
            if g < -_LABEL_PAD:
                label_label.append(row)
            elif g < CROWDED_LABEL_GAP:
                crowded.append(row)

    label_body: List[Dict[str, Any]] = []
    for lb in labels:
        for pid, bx in bodies.items():
            if lb["owner"] == pid:
                continue                      # its own part, drawn on purpose
            if _gap(lb["box"], bx) < -_LABEL_PAD:
                label_body.append(
                    {"label": lb["text"], "owner": lb["owner"], "part": pid,
                     "at": [round(lb["box"][0], 1), round(lb["box"][1], 1)],
                     "overlap": round(-_gap(lb["box"], bx), 2)})

    body_body: List[Dict[str, Any]] = []
    ids = list(bodies)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            g = _gap(bodies[ids[i]], bodies[ids[j]])
            if g < hl.PART_CLEARANCE:
                body_body.append({"a": ids[i], "b": ids[j],
                                  "clearance": round(g, 2),
                                  "required": hl.PART_CLEARANCE})

    body_wire: List[Dict[str, Any]] = []
    if segs is not None:
        body_wire = conductor_body_runs(layout, segs)

    label_wire: List[Dict[str, Any]] = []
    if segs is not None:
        for lb in labels:
            hits = sum(1 for _, wb in _wire_boxes(segs)
                       if _gap(lb["box"], wb) < 0)
            if hits:
                label_wire.append({"label": lb["text"], "owner": lb["owner"],
                                   "at": [round(lb["box"][0], 1),
                                          round(lb["box"][1], 1)],
                                   "conductors": hits})

    return {"labels": len(labels),
            "label_label": label_label,
            "label_body": label_body,
            "body_body": body_body,
            "body_wire": body_wire,
            "crowded": crowded,
            "label_wire": label_wire,
            "ok": not (label_label or label_body or body_body or body_wire)}


__all__ = [
    "PITCH", "BoardLayout", "PartBox", "FootprintPlacement", "WireSeg",
    "layout_hookup", "wire_segments", "hookup_to_svg", "hookup_svg_to_file",
    "hookup_svg_to_png", "hookup_viewer_scene",
    # ⛔ the FULL render result — the SVG *and* the crossing verdict a delivery
    # path must gate on
    "render_hookup", "CrossingReport", "crossing_report", "skip_remedy",
    # rasterizers — one honest, two kept public as the fault injectors
    "canvas_rasterize", "svgdom_rasterize", "hopless_rasterize",
    # ⛔ the survival checks: THE shared surface, named so a third copy
    # has somewhere to be instead.  One differential, two features.
    "label_ink_report", "rasterize_with_label_proof", "strip_text",
    "count_text_elements", "SPARSE_LABEL_RATIO",
    "hop_ink_report", "rasterize_with_hop_proof", "strip_hops",
    "count_hop_arcs",
    # ⛔ the LEGIBILITY gate — presence is not readability
    "legibility_report", "CROWDED_LABEL_GAP", "PIN_LABEL_SIZE",
    # ⛔ ...and its HARD part-body x conductor class: a conductor under an
    # opaque body cannot be hopped and cannot be followed
    "conductor_body_runs", "CONDUCTOR_HALF_W",
]
