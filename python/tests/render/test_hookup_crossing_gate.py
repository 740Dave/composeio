"""
⛔ THE CROSSING GATES — a wiring diagram may not ship a crossing that reads as
a solder joint.

TWO DISTINCT FAILURES, TWO DISTINCT INSTRUMENTS, and conflating them is how the
first one survived for weeks:

  D4  THE PLANNER SKIPPED A CROSSING.  `plan_hops` returns `(hops, skipped)`
      and `SkippedHop`'s own docstring calls silence about the second value
      "the same class of lie as a wire that isn't there" — yet the only caller
      wrote `planned, _skipped = plan_hops(...)` and dropped it.  Measured on
      the committed TM1637 quad panel: 57 hops planned, 7 skipped, five piled
      at one congested column.  A skipped crossing has NO ARC IN THE DOCUMENT,
      so no raster check can ever see it.  Instrument: `CrossingReport.ok`,
      gated in `decision_ops.hookup_review` BEFORE anything is rasterized.

  D5  THE ARCS DID NOT REACH THE PIXELS.  The label gate exists because a
      rasterizer can silently drop every `<text>` and still return a valid PNG
      (`svgdom_rasterize` does exactly that).  Nothing stood beside it for the
      BRIDGES: a label-stripped render was refused while an arc-stripped one —
      in which every drawn crossing has become a plain intersection — sailed
      through the identical gate.  Instrument: `hop_ink_report`, the label
      check's shape over the SAME differential.

EVERY GATE HERE HAS A NEGATIVE CONTROL.  An instrument that can only report
success is not an instrument, and both directions are asserted: the TM1637
panel (7 REAL skips) must be REFUSED, and the dual-throttle example (0 skips)
must still DELIVER.  The hop gate is fault-injected with `hopless_rasterize`,
which drops the arcs and keeps the text — so the same fixture proves the hop
gate fires AND that the label gate does not notice, which is the whole reason
the second gate had to be built.
"""

from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.engine.parser import parse_compose_yaml
from composeio.maker.hookup_svg import (
    canvas_rasterize, count_hop_arcs, hookup_svg_to_png, hookup_to_svg,
    hop_ink_report, hopless_rasterize, label_ink_report, layout_hookup,
    plan_hops, rasterize_with_hop_proof, render_hookup, strip_hops,
    strip_text, wire_segments,
)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")

# The two committed scenes are the fixtures, and which is which is MEASURED,
# not assumed — see `test_the_fixtures_are_what_this_file_claims`.
CLEAN = os.path.join(EXAMPLES, "dual_throttle.compose.yaml")      # 0 skips
BUILDHOST = os.path.join(EXAMPLES, "tm1637_quad_panel.compose.yaml")  # 7 skips


def _hookup(path: str):
    assert os.path.isfile(path), (
        f"fixture missing: {path}. This file's positive control for the "
        f"crossing gate lives in the repo; without it the gate is proven only "
        f"on the passing case, which is no proof at all.")
    h = parse_compose_yaml(path).hookup
    assert h is not None, f"{path} carries no hookup: block"
    return h


# ===========================================================================
# the fixtures themselves — asserted, so this file cannot go vacuous silently
# ===========================================================================
def test_the_fixtures_are_what_this_file_claims():
    """⭐ THE POSITIVE CONTROL IS REAL.  If a future re-route fixes the TM1637
    panel's congested column, this test goes red FIRST and says so — rather
    than every gate test below quietly passing against a diagram that has
    nothing left to catch."""
    _, clean = render_hookup(_hookup(CLEAN))
    assert clean.ok and not clean.skipped, (
        f"{CLEAN} was the ZERO-SKIP fixture and now skips "
        f"{len(clean.skipped)}: {clean.skipped_text()}")
    assert clean.hops, "the clean fixture must still HOP, or it proves nothing"

    _, dirty = render_hookup(_hookup(BUILDHOST))
    assert not dirty.ok, (
        f"{BUILDHOST} was the POSITIVE CONTROL (7 skipped crossings) and now "
        "skips none. If the layout was re-routed, this file needs a new "
        "fixture that genuinely fails — do not delete the assertion.")
    assert len(dirty.skipped) == 7
    assert sorted({s.reason for s in dirty.skipped}) == ["corner", "crowded"]


# ===========================================================================
# A — the skip report is SURFACED, and the SVG is unchanged
# ===========================================================================
def test_render_hookup_surfaces_the_report_plan_hops_always_returned():
    for path in (CLEAN, BUILDHOST):
        h = _hookup(path)
        svg, report = render_hookup(h)
        hops, skipped = plan_hops(layout_hookup(h), wire_segments(h))
        assert report.hops == hops and report.skipped == skipped, path
        assert len(report.wires) == len(wire_segments(h))


def test_surfacing_the_report_does_not_change_one_svg_byte():
    """⛔ A PURE REPORTING ADDITION.  `hookup_to_svg` is `render_hookup`'s first
    element and nothing else, so every committed artifact re-renders
    identically — including the diagram that DOES skip crossings."""
    for path in (CLEAN, BUILDHOST):
        h = _hookup(path)
        assert hookup_to_svg(h) == render_hookup(h)[0], path


def test_the_refusal_payload_names_every_crossing_actionably():
    """"7 crossings skipped" is a dead end — the author has to re-route a
    specific column.  Every row names both conductors, the coordinate and the
    reason."""
    _, report = render_hookup(_hookup(BUILDHOST))
    rows = report.skipped_rows()
    assert len(rows) == len(report.skipped)
    for row in rows:
        assert row["wire_name"] and "->" in row["wire_name"]
        assert row["crosses_name"] and "->" in row["crosses_name"]
        assert isinstance(row["x"], float) and isinstance(row["y"], float)
        assert row["reason"] in ("pad", "corner", "crowded")
        assert f"{row['x']}" in report.skipped_text()
    d = report.as_dict()
    assert d["skipped"] == 7 and d["hopped"] == len(report.hops)
    assert sum(d["reasons"].values()) == 7


def test_the_report_is_deterministic():
    """A committed artifact re-renders byte-identically; so must its verdict."""
    first = render_hookup(_hookup(BUILDHOST))[1].as_dict()
    for _ in range(3):
        assert render_hookup(_hookup(BUILDHOST))[1].as_dict() == first


# ===========================================================================
# C — the ARCS in the document, counted and stripped
# ===========================================================================
def test_the_arc_count_equals_the_planned_hops():
    for path in (CLEAN, BUILDHOST):
        svg, report = render_hookup(_hookup(path))
        assert count_hop_arcs(svg) == len(report.hops), path


def test_a_naive_document_wide_arc_count_is_MEASURABLY_WRONG():
    """⛔ SCOPING TO `data-wire` CONDUCTORS IS LOAD-BEARING, not cosmetic.

    Measured on the TM1637 panel: a document-wide scan for the letter `A`
    returns 69 against 57 real bridges — the surplus is pin NAMES (`r1.A` in a
    `data-wire` attribute, and the same name in a `<text>` label).  A count
    that drifts with the netlist's pin names is not a count of bridges."""
    svg, report = render_hookup(_hookup(BUILDHOST))
    naive = len(re.findall(r"\bA\b", svg))
    assert naive > len(report.hops), (
        "the fixture no longer carries a pin named `A`, so this test no longer "
        "demonstrates the inflation it was written for — find another")
    assert count_hop_arcs(svg) == len(report.hops) < naive


def test_an_ARC_IN_FOOTPRINT_ART_is_neither_counted_nor_stripped():
    """⛔ THE STRUCTURAL CONTROL, and the more dangerous half.

    Today's cached footprints happen to contain no arcs, so the count above
    would pass even if the scoping were removed.  Inject one: an unmarked
    `<path>` with an `A` command, exactly what a future part's real Fritzing
    art would contribute.

    A document-wide STRIP would flatten it too — and then ART pixels would
    satisfy the survival differential, so the gate would pass on a render that
    lost every bridge.  A check that can pass because the ARTWORK moved is a
    check that can only report success."""
    svg, report = render_hookup(_hookup(CLEAN))
    art = ('<path d="M 10 10 L 20 10 A 5 5 0 0 1 30 10 L 40 10" fill="none" '
           'stroke="#000" stroke-width="1.00" stroke-linecap="round"/>')
    doped = svg.replace("</svg>", art + "\n</svg>")

    assert count_hop_arcs(doped) == len(report.hops), (
        "footprint art inflated the bridge count")
    assert art in strip_hops(doped), (
        "strip_hops flattened an ART arc — the differential would then be "
        "satisfied by artwork instead of by the bridges")
    # ...and it still flattens every CONDUCTOR arc in the same document
    assert count_hop_arcs(strip_hops(doped)) == 0


def test_strip_hops_flattens_the_bridges_and_nothing_else():
    """The reference document differs from the delivered one ONLY by the
    bridges: each arc becomes a straight `L` to the SAME endpoint, so the
    conductor stays a valid, identically-terminated polyline.  (Deleting the
    command instead would break the path and make the differential measure the
    breakage rather than the loss.)"""
    svg = render_hookup(_hookup(BUILDHOST))[0]
    flat = strip_hops(svg)
    assert count_hop_arcs(flat) == 0
    assert len(flat) < len(svg)
    # every conductor survives, with its identity and its endpoints
    wires = re.compile(r'data-wire="([^"]+)"')
    assert wires.findall(flat) == wires.findall(svg)
    for m in re.finditer(r'<path\b[^>]*\sd="([^"]*)"[^>]*data-wire=', flat):
        d = m.group(1)
        assert d.startswith("M ") and " A " not in d
    # and the text is untouched — the two strips are orthogonal
    assert strip_text(flat) == strip_text(strip_hops(svg))
    assert svg.count("<text") == flat.count("<text")


def test_strip_hops_REPLACES_each_arc_with_an_L_TO_ITS_ENDPOINT():
    """⛔ Pinned by MUTATION: deleting the `A` command instead of replacing it
    passes every other assertion in this file, because today every hop starts
    and ends on the same horizontal run, so a deletion happens to leave an
    identical polyline.  It is one routing change away from not being true — a
    hop whose endpoints are not collinear would silently move the conductor,
    and the survival differential would then be measuring the BREAKAGE instead
    of the lost bridge.  The endpoint must survive explicitly."""
    svg = render_hookup(_hookup(BUILDHOST))[0]
    flat = strip_hops(svg)
    arc = re.compile(r"\sA\s+[-\d.]+\s+[-\d.]+\s+[-\d.]+\s+[01]\s+[01]\s+"
                     r"([-\d.]+)\s+([-\d.]+)")
    cond = re.compile(r'<path\b[^>]*\bdata-wire="[^"]*"[^>]*/>')
    d_of = re.compile(r'\sd="([^"]*)"')
    seen = 0
    for orig, made in zip(cond.findall(svg), cond.findall(flat)):
        fd = d_of.search(made).group(1)
        for (ex, ey) in arc.findall(d_of.search(orig).group(1)):
            seen += 1
            assert f"L {ex} {ey}" in fd, (ex, ey, fd[:200])
    assert seen == count_hop_arcs(svg) == 57


def test_strip_hops_is_idempotent():
    svg = render_hookup(_hookup(BUILDHOST))[0]
    assert strip_hops(strip_hops(svg)) == strip_hops(svg)


# ===========================================================================
# C — hop survival: the gate, and PROOF IT CAN GO RED
# ===========================================================================
def test_the_bridges_reach_the_pixels(tmp_path):
    svg, report = render_hookup(_hookup(CLEAN))
    lay = layout_hookup(_hookup(CLEAN))
    rep = hop_ink_report(canvas_rasterize, svg, str(tmp_path / "ok.png"),
                         width=lay.width, height=lay.height, scale=1.0)
    assert rep["ok"] and rep["in_svg"] == len(report.hops)
    assert rep["ink_px"] > rep["in_svg"], rep
    assert not rep["sparse"] and rep["removed"] == ""
    assert os.path.exists(rep["png"])


def test_the_hop_gate_fires_on_an_ARC_DROPPING_rasterizer(tmp_path):
    """⛔ NEGATIVE CONTROL — the whole point of this file.

    `hopless_rasterize` renders the document with every bridge flattened: a
    valid, complete-looking PNG in which each crossing has become a plain
    intersection.  The hop gate must REFUSE it and leave no file."""
    h = _hookup(CLEAN)
    svg = hookup_to_svg(h)
    lay = layout_hookup(h)
    out = str(tmp_path / "flat.png")
    rep = hop_ink_report(hopless_rasterize, svg, out,
                         width=lay.width, height=lay.height, scale=1.0)
    assert rep["ok"] is False, rep
    assert rep["ink_px"] == 0 and rep["png"] == "" and rep["removed"] == out
    assert "line-hop" in rep["reason"] and "JUNCTION" in rep["reason"]
    assert not os.path.exists(out), "a bridge-free raster must not be left on disk"

    with pytest.raises(RuntimeError, match="line-hop"):
        rasterize_with_hop_proof(hopless_rasterize, svg,
                                 str(tmp_path / "flat2.png"),
                                 width=lay.width, height=lay.height, scale=1.0)
    assert not os.path.exists(str(tmp_path / "flat2.png"))


def test_the_LABEL_gate_does_not_notice_the_dropped_arcs(tmp_path):
    """⛔ WHY THE SECOND GATE HAD TO EXIST, on one fixture.

    The SAME arc-dropping rasterizer that the hop gate refuses PASSES the label
    gate cleanly — its text is perfect.  Before this work, that render was
    deliverable."""
    h = _hookup(CLEAN)
    svg = hookup_to_svg(h)
    lay = layout_hookup(h)
    labels = label_ink_report(hopless_rasterize, svg,
                             str(tmp_path / "flat_labels.png"),
                             width=lay.width, height=lay.height, scale=1.0)
    assert labels["ok"] is True, (
        "if the label gate caught this, the fixture is wrong, not the gate")
    assert labels["ink_px"] > 0


def test_a_diagram_with_no_crossings_is_not_refused(tmp_path):
    """⚠ THE DELIBERATE ASYMMETRY WITH THE LABEL GATE.  Zero `<text>` is an
    upstream regression; zero ARCS is legitimate — some diagrams simply have no
    crossings.  The gate against "no arcs because the planner SKIPPED them" is
    `CrossingReport.ok`, a different instrument entirely."""
    svg = strip_hops(hookup_to_svg(_hookup(CLEAN)))     # a hop-free document
    lay = layout_hookup(_hookup(CLEAN))
    rep = hop_ink_report(canvas_rasterize, svg, str(tmp_path / "nohops.png"),
                         width=lay.width, height=lay.height, scale=1.0)
    assert rep["ok"] is True and rep["in_svg"] == 0
    assert "CrossingReport.ok" in rep["reason"]
    assert not os.path.exists(str(tmp_path / "nohops.png")), (
        "the zero-arc branch must not manufacture a raster")


def test_hookup_svg_to_png_is_gated_on_both_and_still_delivers(tmp_path):
    out = str(tmp_path / "guide.png")
    hookup_svg_to_png(_hookup(CLEAN), out, scale=1.0)
    assert os.path.exists(out) and os.path.getsize(out) > 5000


# ===========================================================================
# B — the DELIVERY endpoint refuses, both directions
# ===========================================================================
def _send(path: str, **kw):
    from composeio.decision_ops import hookup_review as H
    published: list = []
    r = H.send_hookup_for_human_review(path, publish=published.append, **kw)
    return r, published


def test_the_endpoint_REFUSES_a_diagram_with_a_skipped_crossing():
    """⭐ THE POSITIVE CONTROL.  The TM1637 quad panel skips 7 crossings today.
    If this gate passes that scene, this gate does not work.

    ⚠ CONSEQUENCE, STATED: that scene is UNDELIVERABLE until its layout is
    re-routed.  That is the ruling ("a skipped crossing is a hard failure"),
    accepted deliberately — the alternative is seven perfboard intersections
    that read as solder joints."""
    r, published = _send(BUILDHOST)
    assert r["status"] == "error"
    assert r.get("refused_unhopped_crossing") is True
    assert published == [], "a refusal must publish NOTHING"
    assert "png" not in r, "no picture rides a refusal"
    rows = r["crossings"]["rows"]
    assert len(rows) == 7
    for row in rows:
        assert row["wire_name"] in r["message"]
        assert str(row["x"]) in r["message"] and str(row["y"]) in r["message"]
        assert row["reason"] in r["message"]
    assert "HOW TO FIX" in r["message"]


def test_the_endpoint_STILL_DELIVERS_a_zero_skip_diagram():
    """THE OTHER DIRECTION.  A gate that refuses everything is not a gate."""
    r, published = _send(CLEAN)
    assert r["status"] == "sent", r["message"]
    assert len(published) == 1
    intent = published[0]
    assert intent["crossings"]["skipped"] == 0
    assert intent["crossings"]["hopped"] > 0
    assert intent["hops"]["ink_px"] > 0
    assert os.path.isfile(intent["png"]) and os.path.isfile(intent["svg"])


def test_the_endpoint_REFUSES_a_render_whose_bridges_vanish():
    """The D5 half at the delivery boundary, with the arc-dropping injector."""
    r, published = _send(CLEAN, rasterize=hopless_rasterize)
    assert r["status"] == "error"
    assert r.get("refused_unhopped_crossing") is True
    assert "line-hop" in r["message"]
    assert r["labels"]["ok"] is True, "the label gate passed it — that is the point"
    assert published == []
    assert not os.path.exists(r.get("png", "") or "/nonexistent")


def test_the_endpoint_selftest_passes():
    """The module's own 26-check selftest, run in-process."""
    from composeio.decision_ops import hookup_review as H
    assert H._selftest() == 0


# ===========================================================================
# ⛔ D6 — A CO-LOCATED CROSSING IS ALREADY BRIDGED, NOT SKIPPED
#
# `plan_hops` groups crossings by the CROSSED WIRE, so N conductors sharing one
# lane — a star net, where every `mcu.GND -> tmN.GND` correctly rides the same
# trunk — produce N entries at the IDENTICAL (x, y).  The greedy pass bridged
# the first and then measured the second against `placed_at` at a distance of
# ZERO, which is `< 2 * HOP_R`, and filed it as `crowded`.
#
# ⚠ THE ARC IS RIGHT THERE ON THE PAGE.  This was a FALSE RED — the same
# disease as a false green and just as expensive.  Measured while re-laying the
# R503 panel: it reported 4 unbridgeable crossings that do not exist, which is
# what made every legible placement of the sensor look like it cost the
# crossing gate.  Over a thousand candidate layouts were rejected on a
# measurement error.
#
# ⭐ THE CONTROL RUNS BOTH WAYS: the co-located skip must go, and the SEVEN REAL
# skips on the TM1637 panel must remain — a fix that also silenced those would
# have turned the crossing gate into an instrument that can only report success.
# ===========================================================================
def test_a_crossing_at_an_ALREADY_BRIDGED_POINT_is_not_reported_as_skipped():
    from composeio.render.engine.hookup import parse_hookup
    # a star net: three GND wires from one pad share a lane, so a conductor
    # crossing that lane crosses all three at ONE point
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "mcu", "part": "esp32-s3-supermini", "at": [20, 2]},
                  {"id": "tm1", "part": "tm1637-4digit", "at": [4, 16]},
                  {"id": "tm2", "part": "tm1637-4digit", "at": [24, 16]},
                  {"id": "tm3", "part": "tm1637-4digit", "at": [4, 30]},
                  {"id": "tm4", "part": "tm1637-4digit", "at": [24, 30]}],
        "wires": [{"from": "mcu.5V", "to": f"tm{i}.VCC"} for i in (1, 2, 3, 4)]
                 + [{"from": "mcu.GND", "to": f"tm{i}.GND"} for i in (1, 2, 3, 4)],
    })
    _, report = render_hookup(hk)
    hopped = {(round(h.x, 3), round(h.y, 3), h.wire) for h in report.hops}
    for s in report.skipped:
        assert (round(s.x, 3), round(s.y, 3), s.wire) not in hopped, (
            f"({s.x}, {s.y}) is reported as an unbridged {s.reason} crossing "
            f"on wire {s.wire} — and that wire has a BRIDGE AT THAT EXACT "
            f"POINT. One arc spans every conductor through one point.")


def test_the_colocation_fix_does_not_silence_a_REAL_skip():
    """⭐ The positive control for the fix itself."""
    _, dirty = render_hookup(_hookup(BUILDHOST))
    assert len(dirty.skipped) == 7, (
        "the seven genuine skips on the TM1637 panel must survive the "
        "co-location fix — otherwise the fix disabled the gate")
