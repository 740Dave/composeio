"""
[SPEC:CXC-LOTTIE] Lottie Animation Widget — compliance tests.

  C1  deterministic Skia-tier playback: frame at logical time t is pure
      and pixel-identical across processes/platforms (render_frame_at)
  C2  bound `speed` changes the play rate within one frame
  C3  bound `go_to_and_stop` holds the matching frame (frame or 0..1
      progress form)
  C4  `play_segments [a,b]` (frames or marker names) plays/loops exactly
      the declared range
  C5  dradis-as-Lottie: the sweep renders at the namespace-bound position
  C7  the Lottie JSON loads from doc:// / bus:// via the AssetManager
  (C6, the CYD sprite sheet, is deferred to M5 per the kickoff scope.)

Goldens live in reference_images/ and regenerate via:
    CXC_REGEN_GOLDENS=1 python3 -m pytest tests/render/test_lottie.py

Cross-platform byte-identity evidence: test_capture_sampled_frames writes
PNGs + decoded-RGBA SHA-256 hashes to captures/lottie/<platform>/ — run
the file on macOS and the RPi and diff the manifests.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.engine.bindings import bindable_attrs_for
from composeio.render.lottie import LottieAnimation

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array

DRADIS_JSON = os.path.join(
    PY_ROOT, "composeio", "render", "examples", "assets",
    "dradis_lottie.json")


def _golden(canvas, name):
    actual = surface_to_array(canvas)
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        canvas.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference(name)
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"{name}: {ratio * 100:.2f}% of pixels exceed tolerance"


def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str):
    return next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == wid)


# ---------------------------------------------------------------------------
# Fixture animation: a 100-frame slider at fr=50 — frame number is
# directly observable (square slides x: 0 -> 200 linearly).
# ---------------------------------------------------------------------------

SLIDER = {
    "v": "5.7.0", "nm": "slider", "fr": 50, "ip": 0, "op": 100,
    "w": 240, "h": 60, "assets": [],
    "markers": [{"cm": "front", "tm": 10, "dr": 30},
                {"cm": "back", "tm": 60, "dr": 30}],
    "layers": [{
        "ddd": 0, "ind": 1, "ty": 4, "nm": "sq", "sr": 1, "st": 0,
        "ip": 0, "op": 101,
        "ks": {
            "a": {"a": 0, "k": [0, 0]},
            "p": {"a": 1, "k": [
                {"t": 0, "s": [20, 30],
                 "i": {"x": [1], "y": [1]}, "o": {"x": [0], "y": [0]}},
                {"t": 100, "s": [220, 30]},
            ]},
            "s": {"a": 0, "k": [100, 100]},
            "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100},
        },
        "shapes": [{"ty": "gr", "it": [
            {"ty": "rc", "p": {"a": 0, "k": [0, 0]},
             "s": {"a": 0, "k": [16, 16]}, "r": {"a": 0, "k": 0}},
            {"ty": "fl", "c": {"a": 0, "k": [0.15, 0.65, 0.60]},
             "o": {"a": 0, "k": 100}},
            {"ty": "tr", "a": {"a": 0, "k": [0, 0]},
             "p": {"a": 0, "k": [0, 0]}, "s": {"a": 0, "k": [100, 100]},
             "r": {"a": 0, "k": 0}, "o": {"a": 0, "k": 100}},
        ]}],
    }],
}


@pytest.fixture
def slider_src(tmp_path) -> str:
    p = tmp_path / "slider.json"
    p.write_text(json.dumps(SLIDER))
    return "file://" + str(p)


def _slider_config(src: str, **lottie_extra) -> dict:
    return {
        "design": {"width": 240, "height": 60, "title": "lottie-slider"},
        "widgets": [{
            "type": "pixel_stack", "id": "scene",
            "width": 240, "height": 60, "background": "#000000",
            "children": [{
                "type": "lottie", "id": "anim", "src": src,
                "x": 0, "y": 0, "w": 240, "h": 60, "fit": "stretch",
                "speed": {"namespace": "fx/speed", "default": 1.0},
                "go_to_and_stop": {"namespace": "fx/hold"},
                "play_segments": {"namespace": "fx/segment"},
                "autoplay": {"namespace": "fx/playing", "default": True},
                **lottie_extra,
            }],
        }],
    }


def _dradis_config(**lottie_extra) -> dict:
    return {
        "design": {"width": 256, "height": 256, "title": "dradis-lottie"},
        "widgets": [{
            "type": "pixel_stack", "id": "scene",
            "width": 256, "height": 256, "background": "#0A0F0A",
            "children": [{
                "type": "lottie", "id": "dradis",
                "src": "file://" + DRADIS_JSON,
                "x": 0, "y": 0, "w": 256, "h": 256,
                "speed": {"namespace": "dradis/speed", "default": 1.0},
                "go_to_and_stop": {"namespace": "dradis/hold"},
                "play_segments": {"namespace": "dradis/segment"},
                **lottie_extra,
            }],
        }],
    }


# ---------------------------------------------------------------------------
# C1 — deterministic playback
# ---------------------------------------------------------------------------

def test_c1_frame_advances_on_logical_clock(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)                    # activation tick
        assert w._frame == pytest.approx(0.0)
        app.render_frame_at(1.0)                    # 1s * fr 50 = frame 50
        assert w._frame == pytest.approx(50.0)
        app.render_frame_at(1.5)
        assert w._frame == pytest.approx(75.0)
        app.render_frame_at(2.5)                    # 125 wraps: loop=true
        assert w._frame == pytest.approx(25.0)
    finally:
        app.stop()


def test_c1_same_logical_time_is_byte_identical():
    app1 = _app(_dradis_config())
    app2 = _app(_dradis_config())
    try:
        for app in (app1, app2):
            app.render_frame_at(0.0)
            app.render_frame_at(0.6180339887)
        a = surface_to_array(app1.canvas)
        b = surface_to_array(app2.canvas)
        assert np.array_equal(a, b)                 # exact, not tolerance
    finally:
        app1.stop()
        app2.stop()


def test_c1_resampling_same_time_is_stable():
    app = _app(_dradis_config())
    try:
        app.render_frame_at(0.0)
        app.render_frame_at(0.5)
        first = surface_to_array(app.canvas).copy()
        app.render_frame_at(0.5)
        assert np.array_equal(first, surface_to_array(app.canvas))
    finally:
        app.stop()


@pytest.mark.parametrize("t,name", [(0.5, "f030"), (1.5, "f090")])
def test_c1_dradis_golden_sampled_frames(t, name):
    app = _app(_dradis_config())
    try:
        app.render_frame_at(0.0)
        app.render_frame_at(t)
        _golden(app.canvas, f"lottie_dradis_{name}")
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C2 — bound speed changes the rate within one frame
# ---------------------------------------------------------------------------

def test_c2_bound_speed_changes_rate_within_one_frame(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        app.render_frame_at(1.0)                    # frame 50 at 1x
        assert w._frame == pytest.approx(50.0)
        app.publish("fx/speed", 2.0)                # ONE publish
        app.render_frame_at(1.2)                    # VERY NEXT sampled frame
        # rebased at t=1.0/frame 50: + 0.2s * 50fr * 2x = 20 frames
        assert w._frame == pytest.approx(70.0)
        app.publish("fx/speed", 0.5)
        app.render_frame_at(1.4)
        assert w._frame == pytest.approx(75.0)      # + 0.2 * 50 * 0.5
    finally:
        app.stop()


def test_c2_speed_change_is_continuous_no_jump(slider_src):
    """A rate change must never teleport the playhead."""
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        app.render_frame_at(0.8)
        before = w._frame
        app.publish("fx/speed", 5.0)
        app.render_frame_at(0.8001)                 # eps later
        assert abs(w._frame - before) < 1.0         # continuous
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C3 — goToAndStop holds the bound frame
# ---------------------------------------------------------------------------

def test_c3_goto_holds_frame_number(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        app.publish("fx/hold", 30)
        app.render_frame_at(0.5)
        assert w._frame == pytest.approx(30.0)
        app.render_frame_at(5.0)                    # holds, ignores time
        assert w._frame == pytest.approx(30.0)
        pix = surface_to_array(app.canvas).copy()
        app.render_frame_at(9.0)
        assert np.array_equal(pix, surface_to_array(app.canvas))
    finally:
        app.stop()


def test_c3_goto_progress_form(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.publish("fx/hold", 0.5)                 # 0..1 progress
        app.render_frame_at(0.0)
        assert w._frame == pytest.approx(50.0)      # mid of [0, 100]
    finally:
        app.stop()


def test_c3_goto_release_resumes(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.publish("fx/hold", 40)
        app.render_frame_at(0.0)
        assert w._frame == pytest.approx(40.0)
        app.publish("fx/hold", "")                  # release the hold
        app.render_frame_at(1.0)                    # stamps origin @40
        app.render_frame_at(1.5)
        assert w._frame == pytest.approx(65.0)      # 40 + 0.5 * 50
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C4 — playSegments loops exactly the range
# ---------------------------------------------------------------------------

def test_c4_segment_frames_loop_range(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.publish("fx/segment", [12, 36])
        app.render_frame_at(0.0)                    # restart at a=12
        assert w._frame == pytest.approx(12.0)
        app.render_frame_at(0.2)                    # +10 frames
        assert w._frame == pytest.approx(22.0)
        app.render_frame_at(0.6)                    # +30: wraps 24-frame span
        assert w._frame == pytest.approx(18.0)      # 12 + (30 % 24)
        for t in (1.0, 2.3, 7.7):
            app.render_frame_at(t)
            assert 12.0 <= w._frame < 36.0          # never leaves the range
    finally:
        app.stop()


def test_c4_segment_by_marker_name(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.publish("fx/segment", "front")          # marker [10, 40]
        app.render_frame_at(0.0)
        assert w._frame == pytest.approx(10.0)
        app.render_frame_at(0.4)                    # +20
        assert w._frame == pytest.approx(30.0)
        app.render_frame_at(0.8)                    # +40: wraps span 30
        assert w._frame == pytest.approx(20.0)      # 10 + (40 % 30)
        app.publish("fx/segment", "back")           # switch: [60, 90]
        app.render_frame_at(1.0)                    # restarts at 60
        assert w._frame == pytest.approx(60.0)
    finally:
        app.stop()


def test_c4_marker_resolution():
    anim = LottieAnimation.from_bytes(
        open(DRADIS_JSON, "rb").read())
    assert anim.resolve_segment("sweep") == (0.0, 120.0)
    assert anim.resolve_segment("calibrate") == (120.0, 180.0)
    assert anim.resolve_segment([15, 45]) == (15.0, 45.0)
    assert anim.resolve_segment("15, 45") == (15.0, 45.0)
    assert anim.resolve_segment("no_such_marker") is None


# ---------------------------------------------------------------------------
# transport: autoplay pause/resume + finite loop
# ---------------------------------------------------------------------------

def test_autoplay_bound_pause_and_resume(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        app.render_frame_at(0.6)
        assert w._frame == pytest.approx(30.0)
        app.publish("fx/playing", "off")            # pause holds the frame
        app.render_frame_at(1.0)
        assert w._frame == pytest.approx(30.0)
        app.render_frame_at(3.0)
        assert w._frame == pytest.approx(30.0)
        app.publish("fx/playing", "on")             # resume from held frame
        app.render_frame_at(4.0)                    # stamps origin
        app.render_frame_at(4.4)
        assert w._frame == pytest.approx(50.0)      # 30 + 0.4 * 50
    finally:
        app.stop()


def test_loop_false_plays_once_and_finishes(slider_src):
    app = _app(_slider_config(slider_src, loop=False))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        app.render_frame_at(1.0)
        assert w._frame == pytest.approx(50.0)
        app.render_frame_at(3.0)                    # past op
        assert w._frame == pytest.approx(100.0)     # clamped at end
        assert w.on_clock_tick(4.0) is False        # no longer needs frames
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# C5 — dradis-as-Lottie: sweep at the namespace-bound position
# ---------------------------------------------------------------------------

def test_c5_bound_hold_positions_the_needle():
    """goToAndStop progress -> needle angle; two bound values give two
    distinct, golden-pinned needle positions."""
    def render_at_hold(progress):
        app = _app(_dradis_config())
        try:
            app.publish("dradis/segment", "sweep")
            app.publish("dradis/hold", progress)
            app.render_frame_at(0.0)
            return app, surface_to_array(app.canvas).copy()
        finally:
            app.stop()

    _, up = render_at_hold(0.001)                   # needle ~straight up
    _, right = render_at_hold(0.25)                 # quarter sweep: right
    assert not np.array_equal(up, right)

    app = _app(_dradis_config())
    try:
        app.publish("dradis/segment", "sweep")
        app.publish("dradis/hold", 0.25)
        app.render_frame_at(0.0)
        _golden(app.canvas, "lottie_dradis_hold_q1")
    finally:
        app.stop()


def test_c5_bound_speed_alters_dradis_sweep():
    """Same logical instant, different bound speed -> different needle."""
    def sample(speed):
        app = _app(_dradis_config())
        try:
            app.publish("dradis/speed", speed)
            app.render_frame_at(0.0)
            app.render_frame_at(0.5)
            return surface_to_array(app.canvas).copy()
        finally:
            app.stop()
    assert not np.array_equal(sample(1.0), sample(2.0))


def test_c5_demo_yaml_parses_with_bound_transport():
    from composeio.render.engine.parser import parse_compose_yaml
    path = os.path.join(PY_ROOT, "composeio", "render", "examples",
                        "dradis_lottie.compose.yaml")
    comp = parse_compose_yaml(path)
    bound = {(b.attr, b.namespace) for b in comp.attr_bindings}
    assert ("speed", "dradis/speed") in bound
    assert ("play_segments", "dradis/segment") in bound
    assert ("go_to_and_stop", "dradis/hold") in bound
    assert ("autoplay", "dradis/scanning") in bound


# ---------------------------------------------------------------------------
# C7 — asset fetch via doc:// and bus:// through the AssetManager
# ---------------------------------------------------------------------------

def test_c7_bus_uri_via_asset_manager(monkeypatch, slider_src):
    from composeio import resolve as _resolve
    payload = json.dumps(SLIDER).encode()

    def fake_bytes(uri, **kw):
        assert uri == "bus://assets/animations/slider.json"
        return payload
    monkeypatch.setattr(_resolve, "resolve_bytes", fake_bytes)

    app = _app(_slider_config("bus://assets/animations/slider.json"))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        assert w._anim is not None                  # fetched + parsed
        assert w._anim.fr == 50.0
    finally:
        app.stop()


def test_c7_doc_uri_via_asset_manager(monkeypatch):
    from composeio import resolve as _resolve
    doc = {"content": base64.b64encode(json.dumps(SLIDER).encode()).decode()}

    def fake_doc(uri, **kw):
        assert uri == "doc://animations/slider"
        return doc
    monkeypatch.setattr(_resolve, "resolve_doc", fake_doc)

    app = _app(_slider_config("doc://animations/slider"))
    try:
        w = _widget(app, "anim")
        app.render_frame_at(0.0)
        assert w._anim is not None
        assert w._anim.op == 100.0
    finally:
        app.stop()


def test_c7_missing_asset_renders_placeholder_not_crash():
    app = _app(_slider_config("bus://assets/animations/definitely_missing_xyz.json"))
    try:
        app.render_frame_at(0.0)                    # must not raise
        w = _widget(app, "anim")
        assert w._anim is None
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Bindable-surface introspection + parser aliases
# ---------------------------------------------------------------------------

def test_bindable_surface_registered_for_composer():
    attrs = bindable_attrs_for("lottie")
    for a in ("speed", "autoplay", "loop", "go_to_and_stop",
              "play_segments", "src", "visible", "opacity"):
        assert a in attrs, a


def test_parser_normalizes_lottie_web_aliases(slider_src):
    app = _app({
        "design": {"width": 240, "height": 60},
        "widgets": [{
            "type": "pixel_stack", "id": "s", "width": 240, "height": 60,
            "children": [{
                "type": "lottie", "id": "anim", "src": slider_src,
                "x": 0, "y": 0, "w": 240, "h": 60,
                "setSpeed": {"namespace": "fx/speed"},
                "goToAndStop": {"namespace": "fx/hold"},
                "playSegments": {"namespace": "fx/segment"},
            }],
        }],
    })
    try:
        bound = {(b.attr, b.namespace) for b in app.comp.attr_bindings}
        assert ("speed", "fx/speed") in bound
        assert ("go_to_and_stop", "fx/hold") in bound
        assert ("play_segments", "fx/segment") in bound
    finally:
        app.stop()


def test_stale_instance_hygiene_unmount_unregisters(slider_src):
    app = _app(_slider_config(slider_src))
    try:
        w = _widget(app, "anim")
        assert any(x is w for x in app._clocked)
        w.on_unmount()
        assert not any(x is w for x in app._clocked)
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# Cross-platform byte-identity capture ([CXC-LOT-C1] evidence)
# ---------------------------------------------------------------------------

def test_capture_sampled_frames_for_crosscheck():
    """Write sampled dradis frames + decoded-RGBA SHA-256 hashes to
    captures/lottie/<platform>/.  Run on macOS and the RPi, then diff
    the manifests — identical hashes == byte-identical frames."""
    plat = "darwin" if sys.platform == "darwin" else "linux"
    out_dir = os.path.join(HERE, "captures", "lottie", plat)
    os.makedirs(out_dir, exist_ok=True)

    samples = [0.0, 0.25, 0.5, 1.0, 1.5, 1.9833333333, 2.25, 2.5]
    manifest = {}
    app = _app(_dradis_config())
    try:
        app.render_frame_at(0.0)                    # activation
        for t in samples:
            app.render_frame_at(t)
            arr = surface_to_array(app.canvas)
            digest = hashlib.sha256(arr.tobytes()).hexdigest()
            name = f"dradis_t{t:.6f}"
            manifest[name] = digest
            app.canvas.save_png(os.path.join(out_dir, name + ".png"))
    finally:
        app.stop()

    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1, sort_keys=True)
    assert len(manifest) == len(samples)
