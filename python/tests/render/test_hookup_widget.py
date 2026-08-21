"""
Native HookupWidget + the shared layout intermediate + the lowered-cache
([FLOW:MAKER-PROJECT] S3 native pass, 2026-07-26).

Pins the refactor's contracts:

  * the layout/routing intermediate is ONE module
    (`composeio.render.engine.hookup_layout`) that BOTH renderers consume —
    `composeio.maker.hookup_svg` re-exports the very same objects;
  * an UNANCHORED part (no `at:`) nestles beside its most-wired placed peer
    (the speaker lands next to the DY-SV5F, not off-right);
  * the native widget draws FROM that intermediate: every routed wire becomes
    one Canvas draw_path whose vertices are the transformed polyline, and the
    endpoints land on the transformed pin terminals;
  * part labels are BIG (screen-space floors — readable without zoom);
  * the per-footprint lowered-cache is HIT on redraw (one SVG lowering per
    (part, size), zero re-lowerings for subsequent frames);
  * `RecordingCanvas.replay` is a faithful fan-out.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.engine import hookup_layout as hl
from composeio.render.engine.hookup import Hookup
from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.record import RecordingCanvas
from composeio.render.widget import Rect
from composeio.render.widgets.hookup import (HookupWidget,
                                             clear_footprint_cache,
                                             footprint_cache_stats)

EXAMPLE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                       "dual_throttle.compose.yaml")


def _example_hookup() -> Hookup:
    comp = parse_compose_yaml(EXAMPLE)
    assert comp.hookup is not None
    return comp.hookup


def _drawn_widget(rect=Rect(0, 0, 1400, 1000)):
    """A HookupWidget drawn once onto a RecordingCanvas (no backend)."""
    clear_footprint_cache()
    w = HookupWidget("guide")
    w.set_hookup(_example_hookup())
    rc = RecordingCanvas()
    w.draw(rc, rect)
    return w, rc, rect


def _fit(lay, rect, max_scale=2.0):
    f = min(rect.w / lay.width, rect.h / lay.height, max_scale)
    ox = rect.x + (rect.w - lay.width * f) / 2.0
    oy = rect.y + (rect.h - lay.height * f) / 2.0
    return f, ox, oy


# ---------------------------------------------------------------------------
# ONE shared intermediate — both renderers consume the same objects
# ---------------------------------------------------------------------------

def test_svg_module_reexports_the_shared_layout():
    import composeio.maker.hookup_svg as svg_mod
    assert svg_mod.layout_hookup is hl.layout_hookup
    assert svg_mod.wire_segments is hl.wire_segments
    assert svg_mod.BoardLayout is hl.BoardLayout
    assert svg_mod.WireSeg is hl.WireSeg
    assert svg_mod.PITCH == hl.PITCH
    assert svg_mod._label_anchor is hl.label_anchor


def test_both_renderers_read_identical_geometry():
    h = _example_hookup()
    a = hl.layout_hookup(h)
    import composeio.maker.hookup_svg as svg_mod
    b = svg_mod.layout_hookup(h)
    assert a.pins == b.pins and (a.width, a.height) == (b.width, b.height)


# ---------------------------------------------------------------------------
# wired-adjacency placement (the speaker fix)
# ---------------------------------------------------------------------------

def test_unanchored_speaker_nestles_beside_the_dysv5f():
    h = _example_hookup()
    spk = h.part("spk")
    assert spk is not None and spk.at is None, \
        "the example's speaker must stay unanchored (the rule under test)"
    lay = hl.layout_hookup(h)
    d = lay.parts["dysv5f"].body
    s = lay.parts["spk"].body
    # beside (to the right of) the module, within the nestle gap + a lane
    gap = s[0] - (d[0] + d[2])
    assert 0 < gap <= 4 * hl.PITCH, f"speaker gap {gap}px is not 'beside'"
    # vertically overlapping the module's band (not flung below/above)
    assert s[1] < d[1] + d[3] and s[1] + s[3] > d[1]
    # and the board stays compact (the old at:[30,30] pushed width ~1350)
    assert lay.width < 900


def test_unanchored_part_without_wired_peer_keeps_auto_stack():
    from composeio.render.engine.hookup import parse_hookup
    hk = parse_hookup({
        "board": "perfboard",
        "parts": [{"id": "a", "part": "pot-10k"},
                  {"id": "b", "part": "pot-10k"}],
        "wires": [],
    })
    lay = hl.layout_hookup(hk)          # no wires -> legacy vertical stack
    assert lay.parts["a"].body[1] < lay.parts["b"].body[1]


def test_routing_invariants_hold_with_nestled_speaker():
    """Body avoidance + lane separation survive the compact placement."""
    h = _example_hookup()
    lay = hl.layout_hookup(h)
    rects = [(pid, box.body) for pid, box in lay.parts.items()]
    for seg in hl.wire_segments(h, lay):
        own_a = seg.frm.split(".", 1)[0]
        own_b = seg.to.split(".", 1)[0]
        assert hl._polyline_body_clear(seg.points, own_a, own_b, rects), \
            (seg.frm, seg.to)
        assert seg.points[0] == seg.frm_xy
        assert seg.points[-1] == seg.to_xy


# ---------------------------------------------------------------------------
# the native widget consumes the intermediate
# ---------------------------------------------------------------------------

def _texts(rc):
    return [args[0] for name, args in rc.ops if name == "draw_text"]


def test_widget_draws_every_wire_from_the_shared_polylines():
    w, rc, rect = _drawn_widget()
    h = _example_hookup()
    lay = hl.layout_hookup(h)
    segs = hl.wire_segments(h, lay)
    f, ox, oy = _fit(lay, rect)

    paths = [args[0] for name, args in rc.ops if name == "draw_path"]
    # wire paths are the draw_path calls whose vertex count matches a routed
    # polyline; there must be at least one per wire
    assert len(paths) >= len(segs)
    # every wire's transformed polyline appears verbatim as one path
    for seg in segs:
        want = [(round(ox + px * f, 3), round(oy + py * f, 3))
                for (px, py) in seg.points]
        found = False
        for cmds in paths:
            got = [(round(c.x, 3), round(c.y, 3)) for c in cmds
                   if hasattr(c, "x")]
            if got == want:
                found = True
                break
        assert found, f"no drawn path matches wire {seg.frm}->{seg.to}"


def test_widget_labels_present_and_big():
    w, rc, _ = _drawn_widget()
    texts = _texts(rc)
    for expected in ("mcu", "potL", "potR", "dysv5f", "spk",
                     "throttle/left", "throttle/right",
                     "GPIO0", "wiper"):
        assert expected in texts, expected
    # BIG-label floor: walk the op stream tracking the current font; every
    # part-id draw must be >= 15px bold (readable without zooming)
    part_ids = {"mcu", "potL", "potR", "dysv5f", "spk"}
    size, weight = 0.0, "regular"
    seen = set()
    for name, args in rc.ops:
        if name == "set_font":
            _, size, weight = args
        elif name == "draw_text" and args[0] in part_ids:
            assert size >= 15.0, (args[0], size)
            assert weight == "bold", args[0]
            seen.add(args[0])
    assert seen == part_ids
    # pin labels carry a readability floor too
    size = 0.0
    for name, args in rc.ops:
        if name == "set_font":
            size = args[1]
        elif name == "draw_text" and args[0] == "GPIO0":
            assert size >= 9.0


def test_widget_placeholder_when_no_hookup(tmp_path):
    scene = tmp_path / "plain.compose.yaml"
    scene.write_text("app: {id: x}\nwidgets: []\n", encoding="utf-8")
    w = HookupWidget("guide", src=str(scene))
    rc = RecordingCanvas()
    w.draw(rc, Rect(0, 0, 400, 300))
    assert "no hookup: block" in _texts(rc)


# ---------------------------------------------------------------------------
# the lowered-cache is HIT, not re-parsed/re-lowered
# ---------------------------------------------------------------------------

def test_footprint_cache_hit_on_redraw(monkeypatch):
    import composeio.render.widgets.hookup as hw
    calls = {"lowered": 0}
    real = hw.render_svg

    def counting(*a, **k):
        calls["lowered"] += 1
        return real(*a, **k)

    monkeypatch.setattr(hw, "render_svg", counting)
    clear_footprint_cache()
    w = HookupWidget("guide")
    w.set_hookup(_example_hookup())
    rect = Rect(0, 0, 1400, 1000)
    w.draw(RecordingCanvas(), rect)
    first = calls["lowered"]
    s1 = footprint_cache_stats()
    # dual-throttle composites 4 footprint parts over 3 DISTINCT footprints
    # (the two pots share one lowering)
    assert first == 3 and s1["lowered"] == 3 and s1["replayed"] == 4
    # second + third frame at the same size: ZERO new lowerings, pure replay
    w.draw(RecordingCanvas(), rect)
    w.draw(RecordingCanvas(), rect)
    s3 = footprint_cache_stats()
    assert calls["lowered"] == first
    assert s3["lowered"] == 3 and s3["replayed"] == 12
    # a NEW size is a new cache key -> lowers again exactly once per footprint
    w.draw(RecordingCanvas(), Rect(0, 0, 700, 500))
    assert calls["lowered"] == 2 * first


def test_recording_canvas_replay_is_faithful():
    rc = RecordingCanvas()
    rc.set_fill_color(1, 2, 3, 4)
    rc.fill_rect(10, 20, 30, 40)
    rc.set_font("monospace", 13, "bold")
    rc.draw_text("hi", 5, 6, "center")
    out = RecordingCanvas()
    rc.replay(out)
    assert out.ops == rc.ops
    # metrics fallback is deterministic without a delegate
    m = rc.measure_text("abcd")
    assert m.width == pytest.approx(0.6 * 13 * 4)


# ---------------------------------------------------------------------------
# headless pixel proof — the widget reaches a real raster
# ---------------------------------------------------------------------------

def test_widget_renders_headless_png(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    scene = {"app": {"id": "hookup-proof", "transports": []},
             "design": {"title": "t", "width": 1200, "height": 900},
             "widgets": [{"type": "hookup", "id": "guide", "src": EXAMPLE}]}
    out = str(tmp_path / "hookup.png")
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out) and os.path.getsize(out) > 20000
