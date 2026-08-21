"""
[SPEC:CXC-PRIM] Widget primitives — compliance tests for the four
Sonnet-owned pure composites: progress_bar, switch, numpad, keyboard.

These are `components:` (spec 14) — a pixel-stack of existing primitives
wired with bindings ([SPEC:CXC-BIND]) + the expression DSL
([SPEC:CXC-DSL]).  No new engine code, no new Widget subclass; every
assertion here exercises the SAME expand_components + AttrBinding +
BehaviorEngine machinery every other composite uses.

Covers:
  progress_bar   fill width tracks value_ns at 0.0/0.6/1.0 (CXC-PRIM-C4);
                 warn_at threshold flips fill_color (never the accent)
  progress_bar_indeterminate  expands + the sweep behavior moves stack_x
                 over time (a looping behavior, no bus traffic)
  switch         tap toggles state_ns; knob.x + bg fill_color track it
                 (CXC-PRIM-C5)
  numpad         a key tap publishes its literal char to target_ns
                 (CXC-PRIM-C8); CLR publishes empty; the decimal key
                 respects allow_decimal
  keyboard       a letter key publishes its lowercase char to target_ns;
                 space/backspace publish their sentinel chars

Run:  python3 -m pytest tests/render/test_primitives_composites.py -q
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp

COMPONENTS_DIR = os.path.abspath(os.path.join(
    PY_ROOT, "composeio", "render", "examples", "components"))


def _app(config: dict) -> RenderApp:
    app = RenderApp.from_dict(config, windowed=False)
    app.start()
    return app


def _widget(app: RenderApp, wid: str):
    return next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == wid)


class _FakePointer:
    """Mirrors test_engine_m0_m1.py's pointer fake — the standard way
    these tests simulate a real tap through the full hit-test + publish
    pipeline (not just calling on_tap() directly)."""

    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = x, y
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _tap(app: RenderApp, x: float, y: float) -> None:
    app._dispatch_pointer(_FakePointer("down", x, y))
    app._dispatch_pointer(_FakePointer("up", x, y))


def _center(w) -> tuple:
    r = w.arranged_rect
    return (r.x + r.w / 2, r.y + r.h / 2)


def _load_component(name: str) -> dict:
    """Load a single component definition out of its demo file, so tests
    can re-instantiate it with test-controlled params (e.g. a clean
    width for exact fill-width arithmetic) without depending on the
    demo's own widgets: block."""
    import yaml
    path = os.path.join(COMPONENTS_DIR, f"{name}.compose.yaml")
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# progress_bar
# ---------------------------------------------------------------------------

def _progress_bar_app(value_ns="demo/level", width=246, **overrides):
    doc = _load_component("progress_bar")
    use = {"use": "progress_bar", "value_ns": value_ns, "width": width}
    use.update(overrides)
    config = {
        "design": {"width": 400, "height": 100},
        "namespaces": [{"name": value_ns, "state_engine": "json"}],
        "components": {"progress_bar": doc["components"]["progress_bar"]},
        "widgets": [use],
    }
    return _app(config)


def test_progress_bar_expands_to_track_and_fill():
    """CXC-PRIM-C1: the composite expands via expand_components — no
    engine Widget subclass involved."""
    app = _progress_bar_app()
    try:
        track = _widget(app, "demo/level_track")
        fill = _widget(app, "demo/level_fill")
        assert track.widget_type in ("rectangle", "rect")
        assert fill.widget_type in ("rectangle", "rect")
    finally:
        app.stop()


def test_progress_bar_fill_width_tracks_value():
    """CXC-PRIM-C4: fill width tracks a bound value 0..1.  width=246
    reserves 46px for the label, leaving a clean 200px drawable bar so
    0.6 * 200 == 120.0 exactly (no float-tolerance guesswork)."""
    app = _progress_bar_app(value_ns="demo/level", width=246)
    try:
        fill = _widget(app, "demo/level_fill")

        app.publish("demo/level", 0.0)
        app.render_frame()
        assert fill.stack_w == pytest.approx(0.0)

        app.publish("demo/level", 0.6)
        app.render_frame()
        assert fill.stack_w == pytest.approx(120.0)

        app.publish("demo/level", 1.0)
        app.render_frame()
        assert fill.stack_w == pytest.approx(200.0)
    finally:
        app.stop()


def test_progress_bar_warn_threshold_flips_color_not_accent():
    """CXC-PRIM-C4 + [CXC-PRIM-TOKENS]: warn is a threshold binding on a
    SEPARATE semantic axis — the fill never silently reuses the accent
    hex past warn_at, and reverts to accent below it."""
    accent = (0x34, 0xC3, 0xD0, 255)
    warn = (0xDD, 0x5B, 0x52, 255)
    app = _progress_bar_app(value_ns="demo/level", width=246, warn_at=0.8)
    try:
        fill = _widget(app, "demo/level_fill")

        app.publish("demo/level", 0.5)
        app.render_frame()
        assert fill.fill_color == accent

        app.publish("demo/level", 0.85)
        app.render_frame()
        assert fill.fill_color == warn
    finally:
        app.stop()


def test_progress_bar_label_visibility_is_compile_time_literal():
    """show_label: false is a COMP-3 exact-token literal — the label
    widget is hidden with zero engine change, no binding involved."""
    app = _progress_bar_app(value_ns="demo/level", width=246,
                            show_label=False)
    try:
        label = _widget(app, "demo/level_pct_label")
        assert label.visible is False
    finally:
        app.stop()


def test_progress_bar_indeterminate_sweeps_over_time():
    """Variants: determinate + indeterminate (a looping behavior).  No
    bus traffic drives it — it is purely a function of elapsed time
    (render_frame_at, [CXC-BIND-C6] determinism)."""
    doc = _load_component("progress_bar")
    config = {
        "design": {"width": 300, "height": 60},
        "components": {
            "progress_bar_indeterminate":
                doc["components"]["progress_bar_indeterminate"],
        },
        "widgets": [{"use": "progress_bar_indeterminate", "id": "pbi"}],
    }
    app = _app(config)
    try:
        sweep = _widget(app, "pbi_sweep")       # default id param: "pbi"
        app.render_frame_at(0.0)
        x0 = sweep.stack_x
        app.render_frame_at(0.5)
        x1 = sweep.stack_x
        app.render_frame_at(1.0)
        x2 = sweep.stack_x
        # a genuinely looping behavior: NOT frozen, and never leaves the
        # safe px range (>= 2, per the from/to tuning note in the file)
        assert len({x0, x1, x2}) > 1
        assert all(x >= 2.0 for x in (x0, x1, x2))
    finally:
        app.stop()


def test_progress_bar_indeterminate_standalone_is_pure_composite():
    """The standalone progress_bar_indeterminate.compose.yaml expands via
    expand_components into a plain track+sweep pixel-stack — no bespoke
    Widget subclass, no bound value ([CXC-PRIM-C1]/[CXC-PRIM-WHY])."""
    doc = _load_component("progress_bar_indeterminate")
    config = {
        "design": {"width": 300, "height": 60},
        "components": {
            "progress_bar_indeterminate":
                doc["components"]["progress_bar_indeterminate"],
        },
        "widgets": [{"use": "progress_bar_indeterminate", "id": "pbi",
                     "width": 220, "to_x": 158}],
    }
    app = _app(config)
    try:
        track = _widget(app, "pbi_track")
        sweep = _widget(app, "pbi_sweep")
        assert track.widget_type in ("rectangle", "rect")
        assert sweep.widget_type in ("rectangle", "rect")
        # no bespoke widget type leaked into the tree
        types = {w.widget_type for w in app.comp.root.iter_widgets()}
        assert "progress_bar_indeterminate" not in types
    finally:
        app.stop()


def test_progress_bar_indeterminate_standalone_sweeps_over_time():
    """The standalone file's sweep is driven purely by a LOOPING behavior
    (pulse on stack_x), not a bound value — no bus traffic moves it, it is a
    function of elapsed time ([CXC-PRIM-CATALOG] indeterminate variant,
    [CXC-BIND-C6] determinism).  from/to stay >= 2px per the file's note."""
    doc = _load_component("progress_bar_indeterminate")
    config = {
        "design": {"width": 300, "height": 60},
        "components": {
            "progress_bar_indeterminate":
                doc["components"]["progress_bar_indeterminate"],
        },
        "widgets": [{"use": "progress_bar_indeterminate", "id": "pbi",
                     "width": 220, "to_x": 158, "period_ms": 1400}],
    }
    app = _app(config)
    try:
        sweep = _widget(app, "pbi_sweep")
        app.render_frame_at(0.0)
        x0 = sweep.stack_x
        app.render_frame_at(0.5)
        x1 = sweep.stack_x
        app.render_frame_at(1.0)
        x2 = sweep.stack_x
        # genuinely looping: not frozen, never leaves the safe px range
        assert len({x0, x1, x2}) > 1
        assert all(x >= 2.0 for x in (x0, x1, x2))
        # and stays within the tuned track span (from_x .. to_x default 2..158)
        assert all(2.0 <= x <= 158.0 for x in (x0, x1, x2))
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# switch
# ---------------------------------------------------------------------------

def _switch_app(state_ns="demo/armed", **overrides):
    doc = _load_component("switch")
    use = {"use": "switch", "state_ns": state_ns}
    use.update(overrides)
    config = {
        "design": {"width": 200, "height": 100},
        "namespaces": [{"name": state_ns, "state_engine": "binary"}],
        "components": {"switch": doc["components"]["switch"]},
        "widgets": [use],
    }
    return _app(config)


def test_switch_knob_and_bg_track_state():
    """CXC-PRIM-C5: knob position is a bound expression on the SAME
    binary namespace the tap toggles; the bg pill visible-swaps between
    two discrete, fixed-color widgets (see the file's IMPLEMENTATION
    NOTE on why not a single fill_color map)."""
    app = _switch_app()
    try:
        knob = _widget(app, "demo/armed_knob")
        bg_off = _widget(app, "demo/armed_bg_off")
        bg_on = _widget(app, "demo/armed_bg_on")

        app.publish("demo/armed", "off")
        app.render_frame()
        assert knob.stack_x == pytest.approx(2.0)
        assert bg_off.visible is True
        assert bg_on.visible is False

        app.publish("demo/armed", "on")
        app.render_frame()
        assert knob.stack_x == pytest.approx(22.0)
        assert bg_off.visible is False
        assert bg_on.visible is True
    finally:
        app.stop()


def test_switch_tap_toggles_state_ns():
    """Tap publishes the OPPOSITE literal state — an Intelligence publish
    moves it identically to a finger (the same publish_namespace/
    publish_value mechanism every other interactive widget uses; no new
    interaction seam needed for a discrete switch)."""
    app = _switch_app()
    try:
        app.publish("demo/armed", "off")
        app.render_frame()
        bg_off = _widget(app, "demo/armed_bg_off")
        cx, cy = _center(bg_off)

        _tap(app, cx, cy)                    # tap the OFF pill -> on
        app.render_frame()
        assert app.bus.get_state("demo/armed") == "on"

        bg_on = _widget(app, "demo/armed_bg_on")
        cx, cy = _center(bg_on)
        _tap(app, cx, cy)                    # tap the ON pill -> off
        app.render_frame()
        assert app.bus.get_state("demo/armed") == "off"
    finally:
        app.stop()


def test_switch_state_label_map_binding():
    app = _switch_app(show_label=True, on_label="ARMED", off_label="SAFE")
    try:
        label = _widget(app, "demo/armed_state_label")
        app.publish("demo/armed", "on")
        app.render_frame()
        assert label.text == "ARMED"
        app.publish("demo/armed", "off")
        app.render_frame()
        assert label.text == "SAFE"
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# numpad
# ---------------------------------------------------------------------------

def _numpad_app(target_ns="demo/keys", **overrides):
    doc = _load_component("numpad")
    use = {"use": "numpad", "target_ns": target_ns}
    use.update(overrides)
    config = {
        "design": {"width": 300, "height": 300},
        "namespaces": [{"name": target_ns, "state_engine": "json"}],
        "components": {"numpad": doc["components"]["numpad"]},
        "widgets": [use],
    }
    return _app(config)


def test_numpad_key_publishes_char_to_target_ns():
    """CXC-PRIM-C8: a key press edits (publishes to) the target text
    namespace a text_input would consume."""
    app = _numpad_app()
    try:
        app.render_frame()          # arrange, so keys have a rect
        key7 = _widget(app, "key_7_bg")
        cx, cy = _center(key7)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/keys") == "7"

        key3 = _widget(app, "key_3_bg")
        cx, cy = _center(key3)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/keys") == "3"
    finally:
        app.stop()


def test_numpad_clr_publishes_empty():
    app = _numpad_app()
    try:
        app.render_frame()
        app.publish("demo/keys", "9")
        clr = _widget(app, "key_clr_bg")
        cx, cy = _center(clr)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/keys") == ""
    finally:
        app.stop()


def test_numpad_decimal_key_respects_allow_decimal():
    app_off = _numpad_app(target_ns="demo/keys_a", allow_decimal=False)
    try:
        app_off.render_frame()
        dot = _widget(app_off, "key_dot_bg")
        assert dot.visible is False
    finally:
        app_off.stop()

    app_on = _numpad_app(target_ns="demo/keys_b", allow_decimal=True)
    try:
        app_on.render_frame()
        dot = _widget(app_on, "key_dot_bg")
        assert dot.visible is True
        cx, cy = _center(dot)
        _tap(app_on, cx, cy)
        app_on.render_frame()
        assert app_on.bus.get_state("demo/keys_b") == "."
    finally:
        app_on.stop()


def test_numpad_enter_key_optional_on_enter_prunes_cleanly():
    """on_enter is optional-and-prunable (COMP-5 null default): with no
    on_enter supplied, the ENTER key's publish_namespace: key vanishes
    entirely rather than crashing on an empty namespace string."""
    app = _numpad_app()          # no on_enter override
    try:
        app.render_frame()
        enter = _widget(app, "key_enter_bg")
        assert getattr(enter, "_publish_namespace", None) is None
    finally:
        app.stop()

    app2 = _numpad_app(target_ns="demo/keys_c", on_enter="demo/keys_c_enter")
    try:
        app2.render_frame()
        enter = _widget(app2, "key_enter_bg")
        cx, cy = _center(enter)
        _tap(app2, cx, cy)
        app2.render_frame()
        assert app2.bus.get_state("demo/keys_c_enter") == "commit"
    finally:
        app2.stop()


# ---------------------------------------------------------------------------
# keyboard
# ---------------------------------------------------------------------------

def _keyboard_app(target_ns="demo/msg", **overrides):
    doc = _load_component("keyboard")
    use = {"use": "keyboard", "target_ns": target_ns}
    use.update(overrides)
    config = {
        "design": {"width": 500, "height": 300},
        "namespaces": [{"name": target_ns, "state_engine": "json"}],
        "components": {"keyboard": doc["components"]["keyboard"]},
        "widgets": [use],
    }
    return _app(config)


def test_keyboard_letter_key_publishes_lowercase_char():
    app = _keyboard_app()
    try:
        app.render_frame()
        key_q = _widget(app, "key_r0_q_bg")
        cx, cy = _center(key_q)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/msg") == "q"
    finally:
        app.stop()


def test_keyboard_keycap_label_shows_uppercase():
    """Keycaps display uppercase while the published char stays
    lowercase — the same convention a physical keyboard uses."""
    app = _keyboard_app()
    try:
        label = _widget(app, "key_r0_q_lbl")
        assert label.text == "Q"
    finally:
        app.stop()


def test_keyboard_space_and_backspace_publish_sentinels():
    app = _keyboard_app()
    try:
        app.render_frame()
        space = _widget(app, "key_space_bg")
        cx, cy = _center(space)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/msg") == " "

        back = _widget(app, "key_back_bg")
        cx, cy = _center(back)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/msg") == "⌫"
    finally:
        app.stop()


def test_keyboard_shift_toggles_companion_namespace():
    """shift_mode: a separate {{target_ns}}_shift binary toggle for a
    consumer to honor (documented limitation: letter keys do not
    themselves case-shift — publish_value is a static per-key literal)."""
    app = _keyboard_app()
    try:
        app.render_frame()
        shift = _widget(app, "key_shift_bg")
        cx, cy = _center(shift)
        _tap(app, cx, cy)
        app.render_frame()
        assert app.bus.get_state("demo/msg_shift") in (1, True, "on")
    finally:
        app.stop()


# ---------------------------------------------------------------------------
# demo files load end-to-end (the shipped .compose.yaml, not a re-derived
# config) — catches drift between the component body and its own demo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["progress_bar", "progress_bar_indeterminate",
                                  "switch", "numpad", "keyboard"])
def test_demo_file_loads_and_renders(name):
    path = os.path.join(COMPONENTS_DIR, f"{name}.compose.yaml")
    app = RenderApp.from_file(path, windowed=False)
    app.start()
    try:
        app.render_frame()          # no exception == expansion + layout + draw OK
    finally:
        app.stop()
