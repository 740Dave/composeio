"""`send_hookup_for_human_review` — the delivery gate for the artifact with the
HIGHEST cost of being misread.

A decision-support doc that is misread produces a wrong ANSWER, caught in the
next exchange.  A wiring diagram that is misread produces a wrong JOINT — it is
executed with a soldering iron, on boards that may already be panelized, and the
failure surfaces hours later as hardware behaving inexplicably.  So this suite
holds the endpoint to a HIGHER bar than its DSD sibling, not an equal one.

What is pinned here:

  H1  the endpoint is on BOTH decision-ops registration surfaces (`tools/list`
      AND the dispatch handler map) — the built-and-never-registered class.
  H2  it takes the `.compose.yaml` SCENE and RENDERS; a pre-rendered image is
      REFUSED with an actionable message (a stale PNG cannot prove which netlist
      it came from).
  H3  validation-only failure publishes NOTHING (the DSD contract, and it
      matters more here).
  H4  ⛔ THE LABEL GATE.  `hookup_svg.svgdom_rasterize` — the rasterization core
      of the shipped `hookup_svg_to_png` — silently drops EVERY text label
      (skia-python's `SVGDOM` has no `Builder`, so no font manager) and returns a
      VALID PNG with no error.  Injecting that REAL function as the rasterizer
      must make the endpoint REFUSE and publish nothing.  This is the fault
      injector: an endpoint that cannot refuse is not a gate.
  H5  the intent carries the SOURCE `.compose.yaml`, not just the image — the
      scene is single-source for both halves and a viewer that can reach it can
      show what the picture cannot.
  H6  NEGATIVE SPACE rides the intent: a netlist can only express connections
      that EXIST, and the load-bearing knowledge in a hookup guide is frequently
      about connections that must NOT exist.
  H7  ⛔ THE CROSSING GATE (2026-08-15).  Where two conductors cross, the
      horizontal one is drawn with a bridge so the crossing cannot be read as a
      junction — and `plan_hops` returns the crossings it COULD NOT bridge as a
      second value that the only caller DISCARDED.  Measured on the committed
      TM1637 quad panel: 57 hops planned, 7 skipped.  Seven perfboard
      intersections that read as solder joints, shipped invisibly.  David's
      ruling: a skipped crossing is a HARD FAILURE.  Both directions are pinned
      here (the 7-skip scene REFUSED; the 0-skip scene still DELIVERS), plus
      the raster half — a rasterizer that drops the ARCS while keeping the text
      passes H4's gate cleanly and must be refused by this one.
      The renderer-side depth lives in
      `python/tests/render/test_hookup_crossing_gate.py`.
"""
from __future__ import annotations

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'python'))
from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS, INTELLIGENCE_CONSOLE_CORPUS_DIR as _ICC  # corpora live OUTSIDE the repo since 2026-08-17
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "python"))

import composeio.decision_ops as decision_ops  # noqa: E402,F401  (seats engine path)
from composeio.decision_ops import dsd_review as dr  # noqa: E402
from composeio.decision_ops import hookup_review as hr  # noqa: E402
from composeio.decision_ops.mcp_server import DecisionOpsMcp, Server  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
EXAMPLE_SCENE = REPO / "python" / "composeio" / "render" / "examples" / \
    "dual_throttle.compose.yaml"
NO_HOOKUP_SCENE = REPO / "compose_projects" / "intelligence" / "console" / "dsdviewer.compose.yaml"
PRERENDERED_PNG = REPO / "python" / "composeio" / "maker" / "projects" / \
    "dual_throttle" / "artifacts" / "hookup-guide.perfboard.png"
# ⭐ THE POSITIVE CONTROL FOR H7: a committed scene that skips 7 crossings TODAY.
# It is not synthetic and it is not a fixture written to fail — it is the guide
# that was open on glass when the discard was found.
SKIPPED_CROSSING_SCENE = REPO / "python" / "composeio" / "render" / \
    "examples" / "tm1637_quad_panel.compose.yaml"


@pytest.fixture
def sink():
    """A publish sink that records intents instead of reaching a broker."""
    out = []
    return out, out.append


# ===========================================================================
# H1 — on BOTH registration surfaces
# ===========================================================================
def test_h1_endpoint_present_on_both_registration_surfaces():
    srv = Server(DecisionOpsMcp(default_graph=None, profile=None))
    tl = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    names = {t["name"] for t in tl["result"]["tools"]}
    assert "send_hookup_for_human_review" in names, "dropped from tools/list"
    # its sibling is untouched — this is an addition, not a replacement.
    assert "send_dsd_for_human_review" in names

    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_hookup_for_human_review", {"hookup_path": ""})
    assert "unknown" not in (res.get("error") or "").lower(), \
        "send_hookup_for_human_review dropped from the dispatch handler map"


# ===========================================================================
# H2/H5 — scene in, rendered artifacts out, the SOURCE carried on the intent
# ===========================================================================
def test_h2_valid_scene_renders_and_publishes(sink):
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE),
                                          correlation_id="hk-1",
                                          publish=publish)
    assert res["status"] == "sent", res["message"]
    assert len(published) == 1
    intent = published[0]

    # H5: the SOURCE scene rides the intent (not only the picture).
    assert intent["scene"] == str(EXAMPLE_SCENE.resolve())
    assert intent["path"] == intent["scene"]        # doc/dsd-family `path` alias
    # the rendered halves exist and are non-trivial
    assert Path(intent["svg"]).is_file() and Path(intent["svg"]).stat().st_size > 0
    assert Path(intent["png"]).is_file() and Path(intent["png"]).stat().st_size > 0
    assert intent["correlation_id"] == "hk-1" == res["correlation_id"]
    # the netlist summary the viewer titles itself with
    assert intent["board"] == "perfboard"
    assert intent["parts"] >= 4 and intent["wires"] >= 8


def test_h2_prerendered_image_is_refused_with_an_actionable_message(sink):
    """RENDER, don't accept.  A rendered image cannot prove which netlist it came
    from; a stale PNG is indistinguishable from a fresh one and the failure it
    produces is a wrong joint hours later."""
    if not PRERENDERED_PNG.is_file():
        pytest.skip("committed hookup PNG not present on this checkout")
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(PRERENDERED_PNG), publish=publish)
    assert res["status"] == "error"
    assert not published
    assert ".compose.yaml" in res["message"], \
        "the refusal must name what to pass instead"


# ===========================================================================
# H3 — validation-only failure publishes NOTHING
# ===========================================================================
def test_h3_outside_repo_publishes_nothing(sink):
    published, publish = sink
    res = hr.send_hookup_for_human_review("/etc/passwd", publish=publish)
    assert res["status"] == "error" and not published


def test_h3_missing_path_publishes_nothing(sink):
    published, publish = sink
    res = hr.send_hookup_for_human_review("python/nope/none.compose.yaml",
                                          publish=publish)
    assert res["status"] == "error" and not published


def test_h3_scene_without_a_hookup_block_publishes_nothing(sink):
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(NO_HOOKUP_SCENE), publish=publish)
    assert res["status"] == "error" and not published
    assert "hookup:" in res["message"]


def test_h3_bad_netlist_publishes_nothing(tmp_path, monkeypatch, sink):
    """A wire to a pin that does not exist on that part is an ERROR, not a
    silent pass — the netlist has to be sound before a picture of it is sent."""
    monkeypatch.setattr(dr, "REPO_ROOT", tmp_path)
    scene = tmp_path / "bad.compose.yaml"
    scene.write_text(
        "app: {id: bad}\n"
        "design: {title: bad}\n"
        "widgets: []\n"
        "hookup:\n"
        "  board: perfboard\n"
        "  parts:\n"
        "    - {id: mcu, part: esp32-c3-supermini, at: [2, 2]}\n"
        "  wires:\n"
        "    - {from: mcu.GPIO0, to: mcu.NOT_A_REAL_PIN}\n",
        encoding="utf-8")
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(scene), publish=publish)
    assert res["status"] == "error" and not published


# ===========================================================================
# H4 — ⛔ THE LABEL GATE, with the REAL broken rasterizer as the fault injector
# ===========================================================================
def test_h4_unlabelled_render_is_refused_and_publishes_nothing(sink):
    """THE control this endpoint exists for.

    `hookup_svg.svgdom_rasterize` is the rasterization core of the shipped
    `hookup_svg_to_png`.  It returns a valid PNG, no error, NO LABELS.  A diagram
    with no labels is not a diagram — every wire endpoint's identity lives in the
    text — and today it ships looking fine.  Feeding the endpoint that REAL
    function must produce a REFUSAL, not a warning."""
    from composeio.maker.hookup_svg import svgdom_rasterize

    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE),
                                          publish=publish,
                                          rasterize=svgdom_rasterize)
    assert res["status"] == "error", \
        "an unlabelled wiring diagram was accepted — the gate did not fire"
    assert res.get("refused_unlabelled") is True
    assert not published, "an unlabelled diagram was PUBLISHED"
    assert "label" in res["message"].lower()
    # the refusal names the remedy, not just the symptom
    assert "svgdom" in res["message"].lower() or "font" in res["message"].lower()


def test_h4_default_rasterizer_keeps_the_labels(sink):
    """The paired positive: the endpoint's OWN rasterizer lowers the SVG through
    the repo's Canvas contract (which has a font stack), so label ink is present
    and the same gate passes.  Without this, H4 could pass by refusing
    everything."""
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish)
    assert res["status"] == "sent"
    labels = published[0]["labels"]
    assert labels["in_svg"] > 10          # the SVG really does carry labels
    assert labels["ink_px"] > 0           # ...and they reached the raster
    assert labels["ratio"] > 0.0


def test_h4_label_survival_is_a_same_rasterizer_comparison(tmp_path):
    """The detector, in isolation: render the SVG and the SAME SVG with every
    `<text>` stripped THROUGH THE SAME RASTERIZER and compare.  Identical rasters
    mean the text contributed zero pixels.  Renderer-agnostic by construction —
    no OCR, no font knowledge, no golden image."""
    from composeio.maker.hookup_svg import svgdom_rasterize
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.maker import hookup_to_svg, layout_hookup

    hookup = parse_compose_yaml(str(EXAMPLE_SCENE)).hookup
    svg = hookup_to_svg(hookup)
    lay = layout_hookup(hookup)

    stripped = hr.strip_text(svg)
    assert "<text" not in stripped and "<text" in svg

    broken = hr.label_survival(svg, lay.width, lay.height,
                               rasterize=svgdom_rasterize, workdir=str(tmp_path))
    assert broken["in_svg"] > 10          # the labels ARE in the SVG source...
    assert broken["ink_px"] == 0          # ...and NONE of them reached the raster
    assert broken["ok"] is False

    good = hr.label_survival(svg, lay.width, lay.height,
                             rasterize=hr.canvas_rasterize, workdir=str(tmp_path))
    assert good["ink_px"] > 0 and good["ok"] is True


def test_h4_the_detector_is_NOT_implemented_in_this_module(monkeypatch,
                                                           tmp_path):
    """⛔ THE FORK TRIPWIRE.

    This endpoint and `maker/hookup_svg.py` each grew their OWN copy of the
    same-rasterizer differential, hours apart on 2026-08-15, written by two
    agents that could not see each other.  That is the third instance of the
    class `[BP-TYPE-CODES]` collapsed for the message-type table and the value
    codec.  Concurrent dispatch MANUFACTURES forks; the collapse is the fix and
    the NAMING is the prevention.

    So this does not test the measurement — the maker suite does that.  It tests
    that the measurement is not RE-implemented here: replace the primitive with
    a sentinel and `hr.label_survival` must hand it straight back.  A future
    agent who quietly writes a second compare in this file fails HERE."""
    from composeio.maker import hookup_svg

    sentinel = {"in_svg": 1, "ink_px": 1, "ratio": 1.0, "ok": True,
                "sparse": False, "png": "", "removed": "", "rasterizer": "s",
                "reason": ""}
    monkeypatch.setattr(hookup_svg, "label_ink_report", lambda *a, **k: sentinel)
    got = hr.label_survival("<svg><text>x</text></svg>", 10, 10,
                            rasterize=lambda *a, **k: "",
                            workdir=str(tmp_path))
    assert got is sentinel, \
        "hookup_review re-implemented the label check instead of calling " \
        "composeio.maker.hookup_svg.label_ink_report — that is the fork"

    # the pure halves are the same functions too, not near-copies of them
    probe = '<svg><rect/><text x="1">GPIO0</text><text/></svg>'
    assert hr.strip_text(probe) == hookup_svg.strip_text(probe)
    assert hr.count_text_elements(probe) == hookup_svg.count_text_elements(probe)


def test_h4_control_with_the_gate_DISABLED_the_mute_diagram_is_PUBLISHED(
        monkeypatch, sink):
    """⛔ THE ANTI-VACUITY CONTROL — what the gate is actually WORTH.

    H4 proves the gate refuses.  It does not prove the gate is the only thing
    standing in the way: if some other guard already stopped a mute diagram, the
    gate could be deleted tomorrow and every test here would stay green.

    So: leave the MEASUREMENT real (the labels genuinely make zero pixels) and
    neuter only the VERDICT, then watch what the endpoint does.  It does not
    merely accept — it PUBLISHES, and the intent it publishes names a PNG a
    human opens and solders from.  That published intent is the failure this
    endpoint exists to prevent, exhibited on demand.

    (Written 2026-08-15 with the fork collapse: the brief for that work assumed
    this control existed.  It did not.  An assumed control is worse than an
    absent one.)"""
    import os

    from composeio.maker.hookup_svg import svgdom_rasterize

    real = hr.label_survival
    measured = {}

    def gate_off(svg, w, h, *, rasterize, workdir, scale=hr.DEFAULT_SCALE,
                 out_png=None):
        rep = real(svg, w, h, rasterize=rasterize, workdir=workdir,
                   scale=scale, out_png=out_png)
        measured.update(rep)
        # the primitive deleted the mute raster; put it back, because the point
        # of this control is that WITHOUT the gate the file is delivered
        rasterize(svg, out_png, width=w, height=h, scale=scale)
        return {**rep, "ok": True, "png": out_png, "removed": "", "reason": ""}

    monkeypatch.setattr(hr, "label_survival", gate_off)
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish,
                                          rasterize=svgdom_rasterize)

    assert measured["in_svg"] > 10, "the SVG must really carry labels"
    assert measured["ink_px"] == 0, "the raster must really be mute"
    assert res["status"] == "sent", "with the gate off this must be ACCEPTED"
    assert len(published) == 1, \
        "with the gate off a MUTE diagram must be PUBLISHED — if it is not, " \
        "something else is doing the gate's job and H4 is vacuous"
    assert os.path.exists(published[0]["png"]), \
        "the published intent names a real file a human would open"


def test_h4_svg_with_no_text_at_all_is_refused(tmp_path, monkeypatch, sink):
    """The other end of the gate: if the SVG itself carries no labels (an
    upstream renderer regression), refuse at the source — before rasterizing.

    The injection seam is `render_guide` (2026-08-15): the endpoint no longer
    calls `hookup_to_svg`, because that entry point drops the crossing verdict.
    The real crossing report is passed through untouched, so this test injects
    ONE fault (no text) and not two."""
    real_render = hr.render_guide
    monkeypatch.setattr(hr, "render_guide",
                        lambda h: ('<svg viewBox="0 0 10 10"><rect '
                                   'width="10" height="10"/></svg>',
                                   real_render(h)[1]))
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish)
    assert res["status"] == "error" and not published
    assert res.get("refused_unlabelled") is True


# ===========================================================================
# H7 — ⛔ THE CROSSING GATE.  A crossing that reads as a junction is a solder
# joint that must not exist, and `plan_hops`' skip list was thrown away at the
# only call site.  Both directions are pinned, and the raster half beside them.
# Renderer-side depth: python/tests/render/test_hookup_crossing_gate.py
# ===========================================================================
def test_h7_the_positive_control_still_skips_crossings():
    """⭐ THE FIXTURE IS REAL, and stays asserted.  If the TM1637 panel's
    congested column is ever re-routed, this goes red FIRST and says so —
    rather than every H7 test below quietly passing against a diagram with
    nothing left to catch."""
    assert SKIPPED_CROSSING_SCENE.is_file(), SKIPPED_CROSSING_SCENE
    hookup, why = hr.load_hookup(str(SKIPPED_CROSSING_SCENE))
    assert hookup is not None, why
    _, report = hr.render_guide(hookup)
    assert not report.ok and len(report.skipped) == 7, (
        "the positive control no longer fails. If the layout was re-routed, "
        "find a fixture that genuinely skips — do not delete the assertion.")


def test_h7_a_skipped_crossing_is_REFUSED_and_publishes_nothing(sink):
    """David's ruling, verbatim: "let's start with skipped crossing being a hard
    failure, and if needed we can relax."

    ⚠ CONSEQUENCE, STATED RATHER THAN DISCOVERED: `tm1637_quad_panel` is
    UNDELIVERABLE through this endpoint until its layout is re-routed."""
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(SKIPPED_CROSSING_SCENE),
                                          publish=publish)
    assert res["status"] == "error"
    assert res.get("refused_unhopped_crossing") is True
    assert not published, "a refusal must publish NOTHING"
    assert "png" not in res, "no picture rides a refusal"


def test_h7_the_refusal_names_every_crossing_with_coords_and_reason(sink):
    """A refusal a reader cannot act on gets worked around, not fixed. "7
    crossings skipped" is a dead end — the author must re-route a SPECIFIC
    column, so every row names both conductors, the coordinate and the reason,
    and each reason carries its remedy."""
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(SKIPPED_CROSSING_SCENE),
                                          publish=publish)
    rows = res["crossings"]["rows"]
    assert len(rows) == 7 and res["crossings"]["hopped"] == 57
    for row in rows:
        assert row["wire_name"] in res["message"]
        assert row["crosses_name"] in res["message"]
        assert str(row["x"]) in res["message"]
        assert row["reason"] in res["message"]
    assert "HOW TO FIX" in res["message"]
    assert "CONGESTED COLUMN" in res["message"], "the `crowded` remedy"


def test_h7_a_zero_skip_diagram_STILL_DELIVERS(sink):
    """THE OTHER DIRECTION — a gate that refuses everything is not a gate.  The
    dual-throttle example bridges 29 crossings and skips none."""
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish)
    assert res["status"] == "sent", res["message"]
    assert res["crossings"]["skipped"] == 0
    assert res["crossings"]["hopped"] > 0
    assert published[0]["crossings"]["skipped"] == 0
    assert published[0]["hops"]["ink_px"] > 0, \
        "the intent must carry PROOF the bridges made pixels, not just a count"


def test_h7_an_ARC_DROPPING_rasterizer_is_refused(sink):
    """The raster half, with its fault injector.  `hopless_rasterize` flattens
    every bridge and keeps the text: a valid, complete-LOOKING PNG in which
    every crossing has become a plain intersection."""
    from composeio.maker.hookup_svg import hopless_rasterize
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish,
                                          rasterize=hopless_rasterize)
    assert res["status"] == "error"
    assert res.get("refused_unhopped_crossing") is True
    assert "line-hop" in res["message"]
    assert not published
    import os as _os
    assert not _os.path.exists(res.get("png", "") or "/nonexistent"), \
        "the bridge-free PNG must be DELETED, not delivered"


def test_h7_the_LABEL_gate_does_not_catch_a_dropped_ARC(sink):
    """⛔ WHY THE SECOND GATE HAD TO EXIST, asserted rather than narrated.

    The SAME injected render that the hop gate refuses PASSES H4's label gate
    cleanly.  Before 2026-08-15 that render was deliverable: a label-stripped
    diagram was refused, an arc-stripped one sailed through the same gate."""
    from composeio.maker.hookup_svg import hopless_rasterize
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish,
                                          rasterize=hopless_rasterize)
    assert res["labels"]["ok"] is True, \
        "if the LABEL gate caught this, the injector is wrong, not the gate"
    assert res["labels"]["ink_px"] > 0
    assert res.get("refused_unlabelled") is not True


def test_h7_the_detector_is_NOT_implemented_in_this_module():
    """The fork tripwire, extended to the hop check.  ONE differential
    (`hookup_svg._ink_vs_reference`), two features — the second check was
    written against the named shared surface instead of beside it."""
    src = (REPO / "python" / "composeio" / "decision_ops" /
           "hookup_review.py").read_text(encoding="utf-8")
    assert "hop_ink_report" in src, "the endpoint must CALL the primitive"
    for forked in ("def _ink_vs_reference", "toarray(", "!= mute", "np."):
        assert forked not in src, (
            f"{forked!r} — a second copy of the survival differential is "
            "growing in the consumer again")


# ===========================================================================
# H6 — negative space: the connections that must NOT exist
# ===========================================================================
def test_h6_caller_supplied_warnings_ride_the_intent(sink):
    published, publish = sink
    warn = ["Do not butt the modules together — it commons DIO.",
            "Leave the -B header unconnected."]
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish,
                                          warnings=warn)
    assert res["status"] == "sent"
    carried = published[0]["warnings"]
    assert [w["text"] for w in carried] == warn
    assert all(w["source"] == "caller" for w in carried)
    assert res["negative_space"]["count"] == 2


def test_h6_do_not_block_in_the_scene_is_read(tmp_path, monkeypatch, sink):
    """The PROPOSED `hookup: do_not:` schema addition, read tolerantly (it is not
    yet in the strict parser).  Putting the negative space in the SCENE keeps the
    single-source property the netlist already has."""
    monkeypatch.setattr(dr, "REPO_ROOT", tmp_path)
    scene = tmp_path / "warn.compose.yaml"
    scene.write_text(
        "app: {id: warn}\n"
        "design: {title: warn}\n"
        "widgets: []\n"
        "hookup:\n"
        "  board: perfboard\n"
        "  parts:\n"
        "    - {id: mcu, part: esp32-c3-supermini, at: [2, 2]}\n"
        "  wires:\n"
        "    - {from: mcu.GPIO0, to: mcu.GND}\n"
        "  do_not:\n"
        "    - text: Never use GPIO2/8/9 — ESP32-C3 boot straps.\n"
        "      kind: reserved-pin\n"
        "      about: [mcu.GPIO2, mcu.GPIO8, mcu.GPIO9]\n"
        "    - Do not add pull-ups; the module has them.\n",
        encoding="utf-8")
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(scene), publish=publish)
    assert res["status"] == "sent", res["message"]
    carried = published[0]["warnings"]
    assert len(carried) == 2
    assert carried[0]["kind"] == "reserved-pin"
    assert carried[0]["about"] == ["mcu.GPIO2", "mcu.GPIO8", "mcu.GPIO9"]
    assert carried[1]["text"].startswith("Do not add pull-ups")
    assert all(w["source"] == "scene" for w in carried)


def test_h6_no_warnings_is_noted_not_refused(sink):
    """A diagram with no negative space is SUSPICIOUS, not invalid — almost every
    hookup guide has at least one 'do not'.  Note it; do not block on it."""
    published, publish = sink
    res = hr.send_hookup_for_human_review(str(EXAMPLE_SCENE), publish=publish)
    assert res["status"] == "sent"
    assert res["negative_space"]["count"] == 0
    assert "no" in res["negative_space"]["note"].lower()


# ===========================================================================
# through the MCP dispatch surface
# ===========================================================================
def test_dispatch_validates_and_publishes(monkeypatch):
    published = []
    monkeypatch.setattr(hr, "_publish_intent_bus",
                        lambda url, payload: published.append(payload))
    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_hookup_for_human_review",
                       {"hookup_path": str(EXAMPLE_SCENE),
                        "correlation_id": "c-mcp",
                        "warnings": ["do not common the DIO line"]})
    assert res["success"] is True and res["status"] == "sent"
    assert len(published) == 1
    assert published[0]["correlation_id"] == "c-mcp"
    assert published[0]["warnings"][0]["text"] == "do not common the DIO line"


def test_dispatch_invalid_path_publishes_nothing(monkeypatch):
    published = []
    monkeypatch.setattr(hr, "_publish_intent_bus",
                        lambda url, payload: published.append(payload))
    ops = DecisionOpsMcp(default_graph=None, profile=None)
    res = ops.dispatch("send_hookup_for_human_review",
                       {"hookup_path": "/etc/passwd"})
    assert res["success"] is False and res["status"] == "error"
    assert not published


def test_selftest_is_green():
    assert hr._selftest() == 0


# ===========================================================================
# ⛔ THE HARNESS MEASURES ITS OWN CHECKOUT  [CXC-HKR-SELFTEST-ROOT]
# ===========================================================================
def test_selftest_reads_fixtures_from_its_own_checkout_not_the_canonical_one(
        tmp_path, monkeypatch):
    """⛔ THE SPLIT-BRAIN TRIPWIRE (2026-08-15).

    `_selftest` used repo-RELATIVE fixture paths.  The containment guard resolves
    a relative path against the CANONICAL checkout FIRST — so this module,
    imported from a linked worktree, rendered the CANONICAL tree's netlist while
    executing the worktree's code.  When `origin/main` grew a netlist seam the
    canonical checkout's branch did not carry, checks (9), (10) and (16) went RED
    on a tree where all seventeen were green, and (16) reads as "the label gate
    did not fire" — the loudest false alarm the repo can produce.

    The false FAILURE is what was noticed.  The false PASS is the dangerous half:
    a broken change in a worktree, validated against known-good canonical
    fixtures, reporting 17/17.

    So: stand up a decoy "canonical" root whose copy of the fixture scene is
    BROKEN in exactly the way the real one was (the seam declaration removed),
    point the guard at it, and require the selftest to stay GREEN — because it
    must read the scene from the tree this code is made of.

    Before the fix this returns 1 and prints FAIL (9)/(10)/(16).  After, 0."""
    import composeio.decision_ops.dsd_review as dsd_review

    scene_rel = Path("python/composeio/render/examples/dual_throttle.compose.yaml")
    decoy = tmp_path / "decoy-canonical"
    (decoy / scene_rel.parent).mkdir(parents=True)
    # the SAME breakage that bit on 2026-08-15: the actuator seam declaration
    # stripped off the wire, so the scene no longer parses under current code.
    good = (REPO / scene_rel).read_text(encoding="utf-8")
    broken = re.sub(r",\s*\n\s*subscribes: \[[^\]]*\]\}", "}", good)
    assert broken != good, "fixture shape changed — re-derive the decoy breakage"
    (decoy / scene_rel).write_text(broken, encoding="utf-8")

    monkeypatch.setattr(dsd_review, "REPO_ROOT", decoy)
    assert decoy.resolve() != REPO.resolve(), \
        "the decoy must not BE the checkout under test, or this proves nothing"

    # the decoy really is poison: fed to the endpoint directly, it does not load
    poisoned, _ = hr.load_hookup(str(decoy / scene_rel))
    assert poisoned is None, "the decoy scene must genuinely fail to parse"

    assert hr._selftest() == 0, (
        "the selftest read its fixtures from the CANONICAL root instead of the "
        "checkout it was loaded from — a harness measuring a different tree "
        "than the code under test")


def test_a_fixture_that_will_not_load_is_reported_as_HARNESS_not_as_the_gate(
        capsys, monkeypatch):
    """The second half of [CXC-HKR-SELFTEST-ROOT]: an instrument that cannot tell
    "the gate did not fire" from "the gate never ran" mis-reports a WORKING gate
    as a broken one — and that is a false alarm about the one control standing
    between a mute wiring diagram and a soldering iron.  A fixture that will not
    load is a HARNESS failure, named once, and the gate checks do not run."""
    monkeypatch.setattr(hr, "load_hookup",
                        lambda p: (None, "scene does not parse: synthetic"))
    rc = hr._selftest()
    out = capsys.readouterr().out
    assert rc == 1
    assert "HARNESS" in out, "a fixture that will not load must say so"
    assert "(16)" not in out, \
        "a scene that never parsed was reported as the LABEL GATE failing"
    assert "(9)" not in out and "(10)" not in out


def test_the_containment_guard_still_refuses_outside_the_repo():
    """The widened root set must not have widened CONTAINMENT.  Every allowed
    root is still a repo root."""
    import composeio.decision_ops.dsd_review as dsd_review

    assert dsd_review.resolve_dsd("/etc/passwd") is None
    assert hr.resolve_scene("/etc/passwd") is None
    assert hr.resolve_scene("../../../../etc/passwd") is None
    assert hr.own_checkout_root() == REPO, \
        "own_checkout_root must name the tree this test file lives in"


# ===========================================================================
# the invariants must live in the SPEC, not only in code — the under-spec that
# let a dead DSD primary path hide behind its documented fallback for weeks
# ===========================================================================
HKR_SPEC = _SPECS / "70-hookup-review.lts"


def test_the_gate_is_spec_backed_not_code_only():
    assert HKR_SPEC.is_file(), (
        f"missing governing spec {HKR_SPEC} — the hookup endpoint's refusal "
        "invariants would be code-only (under-spec)")
    text = HKR_SPEC.read_text(encoding="utf-8")
    assert "[SPEC:CXC-HKR]" in text
    assert "send_hookup_for_human_review" in text
    # the three rulings, each spec'd as its own node
    for node in ("[CXC-HKR-MCP]", "[CXC-HKR-RENDER-NOT-ACCEPT]",
                 "[CXC-HKR-LABEL-GATE]", "[CXC-HKR-LABEL-DETECTOR]",
                 "[CXC-HKR-FAULT-INJECTOR]", "[CXC-HKR-NEGATIVE-SPACE]",
                 "[CXC-HKR-DO-NOT-SCHEMA]", "[CXC-HKR-LAUNCHER]",
                 "[CXC-HKR-SELFTEST-ROOT]",
                 # H7 — the crossing gate and its raster half
                 "[CXC-HKR-CROSSING-GATE]", "[CXC-HKR-HOP-SURVIVAL]",
                 "[CXC-HKR-HOP-FAULT-INJECTOR]"):
        assert node in text, f"{node} missing from the governing spec"
    low = text.lower()
    assert "refus" in low and "publishes nothing" in low


# ===========================================================================
# the CONSUMER half — an intent nobody consumes is an intent published into the
# void (the exact defect the DSD notify leg already shipped once)
# ===========================================================================
def test_launcher_consumes_the_intent_and_is_registered():
    import importlib.util
    path = REPO / "compose_projects" / "intelligence" / "tools" / "hookup_viewer_launcher.py"
    assert path.is_file(), "no consumer for _app/hookup/open"
    spec = importlib.util.spec_from_file_location("hookup_viewer_launcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.INTENT_NS == hr.INTENT_NS
    assert mod._selftest() == 0

    src = (REPO / "compose_projects" / "intelligence" / "console" / "data_sources.py").read_text(
        encoding="utf-8")
    assert "hookup_viewer_launcher" in src, \
        "built, tested, and NEVER REGISTERED — the known class"
