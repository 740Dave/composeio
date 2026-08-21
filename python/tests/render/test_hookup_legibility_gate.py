"""⛔ THE LEGIBILITY GATE — presence is not readability.

A hookup guide shipped with EVERY gate green (67 crossings bridged / 0 skipped;
labels in_svg 178, ink_px 59153, ok true; hops in_svg 67, ink_px 19103, ok true)
and an additional measurement of "0 body overlaps".  David opened it: *"the
fingerprint sensor placement is so close to the pins that the wiring is
unreadable."*  He was right — one part printed across another, and both edge
columns carried DOUBLED pin names.

These tests pin the three findings that produced, and the instrument that now
catches them:

  1. the body-overlap check answered "no overlap" about two parts at EXACTLY
     ZERO clearance — a correct interior-intersection test asked the wrong
     question.  The clearance rule already existed (`_NESTLE_MARGIN`); it was
     simply never applied to `at:`-anchored parts;
  2. `label_survival` proves labels EXIST and structurally cannot prove they are
     READABLE — 178 present, 178 colliding;
  3. nothing measured collisions at all.

⛔ EVERY GATE HERE HAS A NEGATIVE CONTROL.  An instrument that has only ever
been observed to report success is not an instrument — that is the transplanted
scar (`svgdom_rasterize` returned a valid, well-formed, entirely label-free
PNG with no error).  So each check below is also shown going RED on an injected
fault, and the rotated-label geometry is pinned against ACTUAL INK rather than
against the algebra that produced it.
"""
from dataclasses import replace

import pytest

from composeio.maker.hookup_svg import (
    _measure_fn, _text_boxes, canvas_rasterize, layout_hookup,
    legibility_report, render_hookup)
from composeio.render.engine import hookup_layout as hl
from composeio.render.engine.hookup import parse_hookup
from composeio.render.engine.hookup_layout import wire_segments

pytest.importorskip("skia")


def _hk(fp_col: int, *, label_pins: str = "wired"):
    """The R503 + ESP32-S3 arrangement, with `fp` on a chosen grid column.
    `fp_col=3` reproduces the DELIVERED drawing; `fp_col=9` is the fix."""
    return parse_hookup({
        "board": "perfboard",
        "label_pins": label_pins,
        "parts": [
            {"id": "mcu", "part": "esp32-s3-supermini", "at": [22, 2]},
            {"id": "fp", "part": "r503-fingerprint", "at": [22, fp_col]},
        ],
        "wires": [
            {"from": "mcu.3V3", "to": "fp.VCC"},
            {"from": "mcu.3V3", "to": "fp.3V3T"},
            {"from": "mcu.GND", "to": "fp.GND"},
            {"from": "mcu.GPIO10", "to": "fp.RXD"},
            {"from": "mcu.GPIO11", "to": "fp.TXD"},
        ],
    })


def _report(hk):
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    return svg, lay, legibility_report(svg, lay, segs=wire_segments(hk, lay))


# ===========================================================================
# 1. THE GATE GOES RED ON THE DELIVERED GEOMETRY  (the acceptance bar: it must
#    fail on the scene as shipped, or it does not work)
# ===========================================================================
def test_the_delivered_placement_is_refused():
    _, _, rep = _report(_hk(3))
    assert rep["ok"] is False, (
        "the geometry David called unreadable must not pass the gate built "
        "to catch it")
    assert rep["body_body"], "the two overlapping parts must be named"
    row = rep["body_body"][0]
    assert {row["a"], row["b"]} == {"mcu", "fp"}
    assert rep["label_body"], "fp's labels print across the MCU's art"


def test_the_tangent_case_the_old_check_called_clean():
    """⛔ THE DEFECT, ISOLATED.  `mcu` and `fp` at `at: [22, 2]` / `[22, 3]`
    have extents that meet at EXACTLY y=862.4 — clearance 0.0.  Every strict
    interior-overlap predicate in the repo correctly answers "they do not
    overlap" about a drawing in which one part is printed on the other.  The
    gate asks for CLEARANCE instead, against the margin the router already
    enforces on nestled parts and never enforced on anchored ones."""
    lay = layout_hookup(_hk(3))
    a = hl.part_extent(lay.parts["mcu"])
    b = hl.part_extent(lay.parts["fp"])
    # strictly tangent: they touch, so an intersection test says "clean"
    assert b[1] + b[3] == pytest.approx(a[1], abs=0.01)
    strictly_overlapping = (min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])) > 0
    assert strictly_overlapping is False, "this is why 0 body overlaps was reported"
    # ...and the gate still refuses it, because 0.0 < PART_CLEARANCE
    _, _, rep = _report(_hk(3))
    assert rep["body_body"][0]["clearance"] == pytest.approx(0.0, abs=0.01)
    assert rep["body_body"][0]["required"] == hl.PART_CLEARANCE


def test_the_fix_passes_the_same_gate():
    svg, lay, rep = _report(_hk(9))
    assert rep["ok"] is True, rep
    assert rep["label_label"] == [] and rep["label_body"] == []
    gap = (hl.part_extent(lay.parts["fp"])[0]
           - sum(hl.part_extent(lay.parts["mcu"])[i] for i in (0, 2)))
    assert gap >= hl.PART_CLEARANCE


# ===========================================================================
# 2. NEGATIVE CONTROLS — each class is shown going RED on an injected fault
# ===========================================================================
def test_negative_control_injected_body_overlap_goes_red():
    """Take the PASSING drawing and shove one part onto another."""
    svg, lay, rep = _report(_hk(9))
    assert rep["ok"] is True

    mcu = lay.parts["mcu"]
    x, y, w, h = mcu.body
    moved = replace(lay.parts["fp"], body=(x + 10, y + 10, 40, 40))
    hurt = replace(lay, parts={**lay.parts, "fp": moved})
    bad = legibility_report(svg, hurt)
    assert bad["ok"] is False
    assert any({r["a"], r["b"]} == {"mcu", "fp"} for r in bad["body_body"])


def test_negative_control_injected_label_collision_goes_red():
    """Two labels stamped on the same point must be caught."""
    svg, lay, rep = _report(_hk(9))
    assert rep["ok"] is True
    twin = ('<text x="700.00" y="700.00" font-size="7.5" '
            'font-family="monospace" font-weight="normal" fill="#1A2230" '
            'text-anchor="start" data-owner="mcu">GPIO10</text>')
    injected = svg.replace("</svg>", twin + twin.replace("GPIO10", "GPIO11")
                           + "</svg>")
    bad = legibility_report(injected, lay)
    assert bad["ok"] is False
    assert any({r["a"], r["b"]} == {"GPIO10", "GPIO11"}
               for r in bad["label_label"])


def test_negative_control_injected_label_over_another_part_goes_red():
    svg, lay, rep = _report(_hk(9))
    assert rep["ok"] is True
    bx, by, _, _ = hl.part_extent(lay.parts["mcu"])
    stray = (f'<text x="{bx + 40:.2f}" y="{by + 40:.2f}" font-size="7.5" '
             'font-family="monospace" font-weight="normal" fill="#1A2230" '
             'text-anchor="start" data-owner="fp">VCC</text>')
    bad = legibility_report(svg.replace("</svg>", stray + "</svg>"), lay)
    assert bad["ok"] is False
    assert any(r["part"] == "mcu" and r["owner"] == "fp"
               for r in bad["label_body"])


# ===========================================================================
# 3. THE MEASUREMENT ITSELF — pinned against pixels, not against its own algebra
# ===========================================================================
@pytest.mark.parametrize("anchor,rot", [("start", -90.0), ("end", -90.0),
                                        ("start", 0.0)])
def test_rotated_label_box_matches_the_ink(tmp_path, anchor, rot):
    """⛔ THE GATE'S OWN GEOMETRY IS VERIFIED AGAINST WHAT IS DRAWN.

    The first draft of the rotated-box math had the sign of the second
    component backwards, which measured every downward-hanging label 24 px
    ABOVE where it draws.  A gate reading the wrong rectangle can hide a real
    collision as easily as invent one, and NO amount of re-reading the algebra
    finds that — rasterizing does."""
    np = pytest.importorskip("numpy")
    Image = pytest.importorskip("PIL.Image", reason="pillow")
    w = h = 160
    tr = f' transform="rotate({rot:.1f} 80.00 80.00)"' if rot else ""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
           f'viewBox="0 0 {w} {h}">'
           f'<rect x="0" y="0" width="{w}" height="{h}" fill="#FFFFFF"/>'
           f'<text x="80.00" y="80.00" font-size="7.5" font-family="monospace" '
           f'font-weight="normal" fill="#000000" text-anchor="{anchor}"{tr} '
           f'data-owner="t">GPIO10</text></svg>')
    png = str(tmp_path / "rot.png")
    canvas_rasterize(svg, png, width=w, height=h, scale=4.0)
    arr = np.asarray(Image.open(png).convert("L"))
    ys, xs = np.where(arr < 200)
    s = arr.shape[0] / h
    ink = (xs.min() / s, ys.min() / s, (xs.max() + 1) / s, (ys.max() + 1) / s)
    box = _text_boxes(svg, _measure_fn())[0]["box"]
    assert box[0] - 0.6 <= ink[0] and box[1] - 0.6 <= ink[1]
    assert ink[2] <= box[2] + 0.6 and ink[3] <= box[3] + 0.6


def test_art_silkscreen_is_not_measured_as_a_label():
    """⛔ `data-owner` IS THE MEMBERSHIP TEST, and the first draft got it wrong.

    The inlined Fritzing art carries its own `<text>` (`TM1637 · 4-DIGIT`, the
    unlit digits) inside a `<g transform>`, in the ART's user space.  Counting
    those as labels compared pre-transform coordinates against board
    coordinates and manufactured 99 phantom collisions — loud enough to look
    thorough, wrong enough to be useless."""
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "tm1", "part": "tm1637-4digit", "at": [4, 4]},
                  {"id": "mcu", "part": "esp32-s3-supermini", "at": [4, 20]}],
        "wires": [{"from": "mcu.GPIO10", "to": "tm1.DIO"}],
    })
    svg, _ = render_hookup(hk)
    assert "4-DIGIT" in svg, "the art's own silkscreen is in the document"
    boxes = _text_boxes(svg, _measure_fn())
    assert all("4-DIGIT" not in b["text"] for b in boxes)
    assert all(b["owner"] for b in boxes), "every measured label is attributed"


def test_every_label_the_guide_draws_carries_an_owner():
    """Chrome, wires and parts alike — an unattributed label would silently
    drop out of the gate's population."""
    hk = _hk(9)
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    owners = {b["owner"] for b in _text_boxes(svg, _measure_fn())}
    assert "chrome" in owners
    assert {"mcu", "fp"} <= owners


# ===========================================================================
# 4. PIN-LABEL PLACEMENT — the shared decision, and what it must not disturb
# ===========================================================================
def test_a_row_of_pads_turns_its_labels_and_a_column_does_not():
    """⛔ THE CLASSIFICATION IS THE FIX.  Clamping a label to the art edge at
    the pad's own y is exactly right for a COLUMN of pads (the C3, the TM1637 —
    which is why those drawings have always been clean) and stacks a whole ROW
    on one point.  Six S3 labels landed on one spot."""
    lay = layout_hookup(_hk(9))
    plan = {p.pin: p.kind for p in hl.pin_label_plan(lay.parts["mcu"], None)}
    # the S3's top pad ROW: nine pads sharing one y -> turned
    assert plan["5V"] == hl.LABEL_ROT_ABOVE
    assert plan["GPIO8"] == hl.LABEL_ROT_ABOVE
    # its battery pads are alone at their y -> the untouched edge placement
    assert plan["B-"] == hl.LABEL_EDGE_LEFT

    tm = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "tm1", "part": "tm1637-4digit", "at": [4, 4]}],
        "wires": [],
    })
    box = layout_hookup(tm).parts["tm1"]
    kinds = {p.kind for p in hl.pin_label_plan(box, None)}
    assert kinds <= {hl.LABEL_EDGE_LEFT, hl.LABEL_EDGE_RIGHT}, (
        "a two-column module must keep the placement that already reads well")


def test_suppressing_an_unwired_label_never_moves_a_surviving_one():
    """Row/column grouping is a property of the PART, so dropping a label
    cannot re-classify the survivors."""
    box = layout_hookup(_hk(9)).parts["mcu"]
    everything = {p.pin: p.kind for p in hl.pin_label_plan(box, None)}
    wired = {"3V3", "GND", "GPIO10", "GPIO11"}
    subset = {p.pin: p.kind for p in hl.pin_label_plan(box, wired)}
    assert set(subset) == wired
    for pin, kind in subset.items():
        assert everything[pin] == kind


# ===========================================================================
# 5. THE Z-ORDER — a covered label is a deleted label, and no ink check sees it
# ===========================================================================
def test_labels_are_drawn_after_the_wires():
    """⛔ FOUND BY OPENING THE PNG, NOT BY A GATE.  Labels used to be drawn
    with their part and the wires painted over them, so the MCU's whole top row
    vanished under the ground rail — with `label_survival` still green, because
    the text was present and merely covered."""
    svg, _ = render_hookup(_hk(9))
    last_wire = max(svg.rfind("<path"), svg.rfind("<polyline"))
    first_pin_label = svg.find('data-owner="mcu"')
    assert last_wire != -1 and first_pin_label != -1
    assert first_pin_label > last_wire, (
        "a part label emitted before the conductors can be overpainted")


def test_label_over_wire_is_reported_but_never_a_refusal():
    """A note, deliberately: on a 26-wire board a label crossing SOME conductor
    is unavoidable, and a gate with an unreachable bar gets relaxed until it
    means nothing."""
    hk = _hk(9)
    svg, lay, rep = _report(hk)
    assert rep["ok"] is True
    assert "label_wire" in rep
    no_segs = legibility_report(svg, lay)
    assert no_segs["label_wire"] == []
    assert no_segs["ok"] == rep["ok"]


# ===========================================================================
# 6. ⛔ A CONDUCTOR UNDER A PART BODY — the HARD class (David, 2026-08-16)
#
# The guide came back a SECOND time, machine-green again: 117 crossings all
# bridged, 0 skipped, 0 collisions, `ok: true`.  David: *"I COULD use this
# diagram, but it's really not quite right — see how the FP component and its
# wires overlay the gnd for tm2 and tm4."*
#
# ⛔ AND THIS IS NOT THE `label_wire` NOTE ONE CLASS UP.  `plan_hops` bridges
# wire x wire so a crossing READS as a crossing.  A part body cannot be hopped
# and cannot be seen through, so the reader loses the conductor for the width of
# the part — and "where does this ground go" is the question a wiring guide
# exists to answer.  Hard, therefore, and kept OUT of the soft bucket: the soft
# bucket is soft because a hard bar there would be unreachable, and this bar is
# reachable (the same scene passes it today).
# ===========================================================================
import os                                                  # noqa: E402

from composeio.maker.hookup_svg import (CONDUCTOR_HALF_W,  # noqa: E402
                                        conductor_body_runs)
from composeio.render.engine.hookup_layout import WireSeg  # noqa: E402

# ⛔ THE DELIVERED GEOMETRY, PINNED AS DATA — and the reason it is data is the
# point.  The router has since been fixed (`_assign_routes` avoids
# `_part_extent`, not `body`), so RE-RENDERING the scene no longer reproduces
# what David was sent.  A negative control that a later fix can silently disarm
# is not a control, so the two ground trunks are recorded here verbatim, exactly
# as they were routed and drawn on `1c7b4d2`.
_DELIVERED_GND_TRUNKS = (
    ("mcu.GND", "tm2.GND",
     ((230.46, 874.47), (230.46, 858.0), (771.6, 858.0),
      (771.6, 1140.14), (828.6, 1140.14))),
    ("mcu.GND", "tm4.GND",
     ((230.46, 874.47), (230.46, 858.0), (1356.8, 858.0),
      (1356.8, 1140.14), (1413.8, 1140.14))),
)


def _delivered_segs():
    return [WireSeg(frm=a, to=b, frm_xy=pts[0], to_xy=pts[-1], role="ground",
                    color="#1F2733", points=pts)
            for a, b, pts in _DELIVERED_GND_TRUNKS]


_SCENE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      os.pardir, os.pardir, "composeio", "render", "examples",
                      "r503_tm1637_panel.compose.yaml")


def _scene_hookup(*, r4_col: int = None):
    """THE COMMITTED SCENE, optionally with `r4` put back in `r3`'s column.

    ⛔ The netlist is never touched — only an `at:` anchor — so a mutated
    fixture cannot quietly become a different board."""
    import yaml
    block = yaml.safe_load(open(_SCENE))["hookup"]
    if r4_col is not None:
        for p in block["parts"]:
            if p["id"] == "r4":
                p["at"] = [p["at"][0], r4_col]
    return parse_hookup(block)


def test_the_delivered_ground_trunks_are_refused():
    """⛔ THE NEGATIVE CONTROL, ON THE EXACT DRAWING DAVID REJECTED.

    `fp` is anchored at [22, 9] and its extent is (460.1, 823.9) - (594.3,
    862.4).  Both ground trunks ride ONE lane at y=858.0 — 4.4 px above the
    sensor's six pads — for the full 134.2 px width of the part."""
    lay = layout_hookup(_hk(9))
    rows = conductor_body_runs(lay, _delivered_segs())
    assert len(rows) == 2, rows
    assert {r["part"] for r in rows} == {"fp"}
    assert {r["wire"] for r in rows} == {"mcu.GND -> tm2.GND",
                                         "mcu.GND -> tm4.GND"}
    for r in rows:
        # the WHOLE part, plus the drawn stroke either side
        assert r["run"] == pytest.approx(134.2 + 2 * CONDUCTOR_HALF_W, abs=0.2)
        assert r["at"] == [457.5, 858.0]


def test_the_delivered_geometry_fails_the_whole_report_not_just_a_note():
    """It must set `ok` False — a NOTE would have shipped this drawing twice."""
    hk = _hk(9)
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    rep = legibility_report(svg, lay, segs=_delivered_segs())
    assert rep["ok"] is False
    assert rep["body_wire"], "the class must be populated"
    # ...and the OTHER classes stay silent: nothing was folded together
    assert rep["label_label"] == [] and rep["label_body"] == []
    assert rep["body_body"] == []


def test_the_scene_as_delivered_still_fails_on_a_second_defect():
    """⛔ THE INSTRUMENT FOUND SOMETHING NOBODY REPORTED, which is the whole
    reason to build one.  With the router fixed, the R503 scene as it stood on
    `1c7b4d2` — `r4` at column 28 — STILL refuses: `r3` and `r4` shared that
    column, so `r3.A` and `r4.A` share an x, and the shared-CLK trunk climbing
    to the TOP resistor was drawn straight through the BOTTOM one, 43.7 px of
    conductor across a 46.2 px part.

    ⚠ MEASURED ON THE WHOLE SCENE, NOT A REDUCTION.  A five-part cut-down of
    the same collinearity does NOT reproduce it: with the tube cluster's other
    traffic removed the router finds a clear lane and routes around `r4`.  The
    defect is a property of THIS board's congestion, so the fixture is the
    committed scene with one anchor put back."""
    from composeio.render.engine.hookup_layout import wire_segments
    hk = _scene_hookup(r4_col=28)
    lay = layout_hookup(hk)
    rows = conductor_body_runs(lay, wire_segments(hk, lay))
    assert any(r["part"] == "r4" and r["wire"] == "mcu.GPIO7 -> r3.A"
               for r in rows), rows


def test_the_shipped_scene_passes_every_class():
    """The delivered artifact, re-measured end to end: the two ground trunks now
    run ABOVE `fp` and `r4` has left the trunk's column."""
    from composeio.render.engine.hookup_layout import wire_segments
    hk = _scene_hookup()
    svg, cross = render_hookup(hk)
    lay = layout_hookup(hk)
    rep = legibility_report(svg, lay, segs=wire_segments(hk, lay))
    assert cross.ok, cross.skipped_text()
    assert rep["ok"] is True, rep
    assert rep["body_wire"] == []


def test_a_wire_that_terminates_on_a_part_is_exempt_from_that_part():
    """⛔ THE EXEMPTION IS TOTAL AND DELIBERATE, and it is not a hole.  A lead
    must reach its own pad THROUGH its own pin band, and the reader does not
    lose it there — it ENDS there.  What stops a lead raking across its own part
    is a different rule that already holds: the router's `_polyline_body_clear`,
    bounded by `_exit_budget`."""
    lay = layout_hookup(_hk(9))
    x, y, w, h = hl.part_extent(lay.parts["fp"])
    across = ((x - 50, y + h / 2), (x + w + 50, y + h / 2))
    mine = WireSeg(frm="mcu.GND", to="fp.GND", frm_xy=across[0],
                   to_xy=across[1], role="ground", color="#1F2733",
                   points=across)
    theirs = WireSeg(frm="mcu.GND", to="tm2.GND", frm_xy=across[0],
                     to_xy=across[1], role="ground", color="#1F2733",
                     points=across)
    assert [r["part"] for r in conductor_body_runs(lay, [mine])] == []
    assert [r["part"] for r in conductor_body_runs(lay, [theirs])] == ["fp"]


def test_a_conductor_grazing_the_edge_as_drawn_is_a_failure():
    """⛔ THE TANGENT TRAP, ONE CLASS OVER.  The body-overlap check answered "no
    overlap" about two parts at EXACTLY 0.0 clearance because it measured centre
    lines and the drawing has WIDTH.  A trunk whose centre line runs
    `CONDUCTOR_HALF_W - epsilon` from a part's extent still prints copper on it,
    and is caught; one at `CONDUCTOR_HALF_W + epsilon` clears and is not."""
    lay = layout_hookup(_hk(9))
    x, y, w, h = hl.part_extent(lay.parts["fp"])

    def run_at(dy):
        pts = ((x - 50, y + h + dy), (x + w + 50, y + h + dy))
        seg = WireSeg(frm="mcu.GND", to="tm2.GND", frm_xy=pts[0], to_xy=pts[-1],
                      role="ground", color="#1F2733", points=pts)
        return conductor_body_runs(lay, [seg])

    assert run_at(CONDUCTOR_HALF_W - 0.2), "copper printed on the part"
    assert run_at(CONDUCTOR_HALF_W + 0.2) == [], "clear of the part as drawn"


def test_the_soft_bucket_was_not_widened_to_absorb_the_hard_one():
    """⛔ THE ONE THING THIS CHANGE MUST NOT HAVE DONE.  `label_wire` stays a
    NOTE (a label crossing SOME conductor on a 26-wire board is unavoidable, and
    an unreachable bar gets relaxed until it means nothing) and `body_wire`
    stays a REFUSAL.  They are different populations and different verdicts."""
    hk = _hk(9)
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    rep = legibility_report(svg, lay, segs=_delivered_segs())
    assert rep["body_wire"] and rep["ok"] is False

    from composeio.render.engine.hookup_layout import wire_segments
    clean = legibility_report(svg, lay, segs=wire_segments(hk, lay))
    assert clean["ok"] is True
    assert clean["body_wire"] == []
    assert clean["label_wire"], (
        "labels still graze conductors and that is still only a note")


def test_the_router_avoids_the_pin_band_not_just_the_body_slab():
    """⛔ THE GAP WAS ONE RECTANGLE WIDE.  A generated horizontal part draws its
    `body` slab ABOVE its pin row, so the pads, the stubs and every caption sit
    OUTSIDE the rectangle the router protected.  It now routes against
    `_part_extent`, which is the rectangle the gate measures — so the gate can
    be satisfied rather than merely obeyed."""
    from composeio.render.engine.hookup_layout import wire_segments
    lay = layout_hookup(_hk(9))
    fp = lay.parts["fp"]
    bx, by, bw, bh = fp.body
    ex, ey, ew, eh = hl.part_extent(fp)
    assert by + bh < ey + eh, "the pin band is real: pads hang below the slab"
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-s3-supermini", "at": [22, 2]},
                  {"id": "fp", "part": "r503-fingerprint", "at": [22, 9]},
                  {"id": "tm2", "part": "tm1637-4digit", "at": [26, 17]}],
        "wires": [{"from": "mcu.3V3", "to": "fp.VCC"},
                  {"from": "mcu.GND", "to": "fp.GND"},
                  {"from": "mcu.GND", "to": "tm2.GND"},
                  {"from": "mcu.5V", "to": "tm2.VCC"}],
    })
    lay2 = layout_hookup(hk)
    assert conductor_body_runs(lay2, wire_segments(hk, lay2)) == []


# ===========================================================================
# 7. A TURNED CAPTION STANDS BESIDE ITS LEAD, NOT ON IT
#
# `_ROT_NUDGE` centres a turned caption's glyph column on the pad's x — which is
# exactly the x the pad's lead escapes along.  Measured before the offset: 9 of
# 9 turned captions printed on their own conductor in `dual_throttle`, 23 of 29
# on the R503 panel, and the near-black `GND` caption sat on the near-black
# ground conductor and was, in the raster, gone.
#
# ⛔ THE BACKING PLATE WAS REFUSED, AGAIN.  A plate restores contrast by
# BREAKING THE CONDUCTOR beneath it — a reader losing a conductor for the width
# of an opaque thing, which is precisely the class section 6 above just made
# hard.  A smaller copy of the same lie is not a fix.
# ===========================================================================
def test_a_turned_caption_does_not_print_on_its_own_lead():
    from composeio.maker.hookup_svg import _rot_lead_clear, _wire_boxes, _gap
    from composeio.render.engine.hookup_layout import wire_segments
    hk = _hk(9)
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    boxes = {b["text"]: b["box"] for b in _text_boxes(svg, _measure_fn())}
    # fp's WIRED pads are a ROW, so every surviving caption is turned
    # (`label_pins: wired` suppresses the rest — the pad keeps its ring)
    named = sorted(hl.wired_pins(hk, "fp"))
    assert len(named) >= 4, named
    for pin in named:
        px, _py = lay.parts["fp"].pins[pin]
        assert boxes[pin][0] > px + CONDUCTOR_HALF_W, (
            f"{pin}'s caption still straddles the lead leaving its pad")
    wb = _wire_boxes(wire_segments(hk, lay))
    on_ink = [p for p in named if any(_gap(boxes[p], w) < 0 for _, w in wb)]
    assert on_ink == [], on_ink
    assert _rot_lead_clear(7.5) > CONDUCTOR_HALF_W


def test_the_step_aside_is_one_direction_for_the_whole_drawing():
    """A builder should never have to work out WHICH side of a pad a name
    belongs to — the same reason every turned label reads bottom-to-top."""
    hk = _hk(9)
    svg, _ = render_hookup(hk)
    lay = layout_hookup(hk)
    boxes = {b["text"]: b["box"] for b in _text_boxes(svg, _measure_fn())}
    for part in ("fp", "mcu"):
        for lbl in hl.pin_label_plan(lay.parts[part],
                                     hl.wired_pins(hk, part)):
            if lbl.kind not in (hl.LABEL_ROT_ABOVE, hl.LABEL_ROT_BELOW):
                continue
            assert boxes[lbl.pin][0] > lbl.x, (
                f"{part}.{lbl.pin} stepped the wrong way")
