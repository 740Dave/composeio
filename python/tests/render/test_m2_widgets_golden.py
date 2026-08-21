"""
Golden pixel tests for the M2 production widget parity set ([CXC-RM-M2]).

Every ported widget renders its reference states against the Skia
reference backend and is compared to a committed golden (guardrail p5:
pixel-verify every widget before it counts as done).  Time-driven
states (blink phases, cursors) pin the widget's `_now` seam so goldens
are deterministic.

Goldens live in reference_images/ and regenerate via:

    CXC_REGEN_GOLDENS=1 python -m pytest tests/render/test_m2_widgets_golden.py
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import (BusMonitorWidget, ButtonWidget,
                                      CalloutWidget, CheckboxWidget,
                                      CommandWidget, CoordinateWidget,
                                      DocPickerWidget, ImageWidget,
                                      PowerToggleWidget, RelayWidget,
                                      TabWidget, TextInputWidget)
from composeio.render.engine.layout import FlexContainer

from .conftest import REFERENCE_DIR, images_match, load_reference, surface_to_array

_BLINK_ON = staticmethod(lambda: 0.25)   # phase < 0.5 -> lit / cursor shown
_BLINK_OFF = staticmethod(lambda: 0.75)


def _canvas(w=260, h=170):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x21, 0x21, 0x21)   # CTk frame gray13 backdrop
    c.fill_rect(0, 0, w, h)
    return c


# -- scene builders -----------------------------------------------------------

def _button_states():
    c = _canvas(260, 200)
    rows = [
        ("FTL Arm", "binary", "off", "follow_state", False),
        ("FTL Arm", "binary", "on", "follow_state", False),
        ("Shields", "tri_level", "half", "follow_state", False),
        ("Alert", "stop_light", "red", "follow_state", False),
        ("Hold", "binary", "off", "while_pressed", True),
    ]
    for i, (label, engine, value, behavior, pressed) in enumerate(rows):
        w = ButtonWidget(f"b{i}", label=label, state_engine=engine,
                         led_behavior=behavior)
        w.on_bus_state("ns", value)
        w._pressed = pressed
        w._now = lambda: 0.25
        w.draw(c, Rect(10, 6 + i * 38, 240, 36))
    return c


def _button_blink():
    c = _canvas(260, 84)
    for i, phase in enumerate([0.25, 0.75]):     # lit / dark half-cycle
        w = ButtonWidget(f"bb{i}", label="Blink", state_engine="binary",
                         led_behavior="blink_500ms")
        w.on_bus_state("ns", "on")
        w._now = lambda p=phase: p
        w.draw(c, Rect(10, 4 + i * 38, 240, 36))
    return c


def _relay_states():
    c = _canvas(420, 100)
    cols = [
        ("binary", "off", "Off"), ("binary", "on", "On"),
        ("stop_light", "yellow", "Warn"), ("stop_light", "red", "Alarm"),
        ("tri_level", "half", "Half"),
        ("continuous_0_255", 128, "50%"),
    ]
    for i, (engine, value, label) in enumerate(cols):
        w = RelayWidget(f"r{i}", label=label, state_engine=engine)
        w.on_bus_state("ns", value)
        w.draw(c, Rect(6 + i * 68, 10, 66, 80))
    return c


def _checkbox_states():
    c = _canvas(280, 76)
    w1 = CheckboxWidget("c1", label="avahi (mDNS discovery)")
    w1.draw(c, Rect(6, 6, 268, 30))
    w2 = CheckboxWidget("c2", label="tailscale (install client)")
    w2.checked = True
    w2.draw(c, Rect(6, 40, 268, 30))
    return c


def _text_input_states():
    c = _canvas(260, 250)
    w1 = TextInputWidget("t1", label="Board name (hostname)",
                         placeholder="ginger")
    w1.draw(c, Rect(6, 2, 248, 66))

    w2 = TextInputWidget("t2", label="Board name (hostname)")
    w2.text = "professor"
    w2.cursor = len(w2.text)
    w2.focused = True
    w2._now = lambda: 0.25            # cursor visible
    w2._set_status("✓ set", (0x2E, 0xCC, 0x71))
    w2.draw(c, Rect(6, 82, 248, 66))

    w3 = TextInputWidget("t3", label="Password", secret=True)
    w3.text = "hunter2"
    w3._set_status("✓ saved password loaded (from document store)",
                   (0x2E, 0xCC, 0x71))
    w3.draw(c, Rect(6, 162, 248, 66))
    return c


def _command_states():
    c = _canvas(300, 260)
    binding = {"tool": "fleetconsole.provision_run",
               "args": {"recipe": "minnowboard-debian13"}}

    w1 = CommandWidget("k1", label="Provision (Debian 13)",
                       binding=dict(binding, confirm=True))
    w1._status_text = "idle  ⚠ confirm"
    w1.draw(c, Rect(6, 2, 288, 84))

    w2 = CommandWidget("k2", label="Health check", binding=binding)
    w2._status_key, w2._status_text = "running", "running 40%"
    w2._result_text = "fleetconsole.provision_run(recipe=minnowboard-debian13)"
    w2.draw(c, Rect(6, 88, 288, 84))

    w3 = CommandWidget("k3", label="Health check", binding=binding)
    w3._status_key, w3._status_text = "ok", "done ✓"
    w3._result_text = "provisioned ginger in 210s"
    w3._result_color = (0xCC, 0xCC, 0xCC)
    w3.draw(c, Rect(6, 174, 288, 84))
    return c


def _power_toggle_states():
    c = _canvas(260, 250)
    w1 = PowerToggleWidget("p1", label="Power")
    w1.draw(c, Rect(6, 2, 248, 60))          # no plug -> disabled

    w2 = PowerToggleWidget("p2", label="Power")
    w2._plug, w2._state, w2._dot = "5", "on", (0x2E, 0xCC, 0x71)
    w2.draw(c, Rect(6, 64, 248, 60))          # outlet on -> "Power Off" warm

    w3 = PowerToggleWidget("p3", label="Power")
    w3._plug, w3._state = "5", "off"
    w3.draw(c, Rect(6, 126, 248, 60))         # outlet off -> "Power On"

    w4 = PowerToggleWidget("p4", label="Power")
    w4._plug, w4._state, w4._busy, w4._dot = "5", "on", True, (0xF1, 0xC4, 0x0F)
    w4.draw(c, Rect(6, 188, 248, 60))         # busy
    return c


def _doc_picker_rows():
    c = _canvas(300, 190)
    w = DocPickerWidget("d1", label="Saved MinnowBoards")
    w._rows = [
        {"doc_id": "ginger", "device": "ginger", "role": "fleet unit"},
        {"doc_id": "professor", "device": "professor", "role": "bench"},
        {"doc_id": "gilligan", "device": "gilligan", "role": "fleet unit"},
    ]
    w._status_text = "3 saved — pick one to load"
    w.draw(c, Rect(6, 4, 288, 182))
    return c


def _bus_monitor_lines():
    c = _canvas(430, 250)
    w = BusMonitorWidget("m1", label="Bus Monitor")
    w._lines = [
        ("[10:15:01] engine_rpm: state 178 (simulator)", (0xFF, 0xFF, 0xFF)),
        ("[10:15:02] ftl_arm: event armed (dashboard)", (0x00, 0xBC, 0xD4)),
        ("[10:15:02] _bus/discovery/cyd-01: discovery", (0xFF, 0xD7, 0x00)),
        ("[10:15:03] alert_status: state green", (0xFF, 0xFF, 0xFF)),
        ("[10:15:04] _bus/config/cyd-01: config_push", (0xE0, 0x40, 0xFB)),
    ]
    w.draw(c, Rect(4, 2, 424, 246))
    return c


def _tab_widget(active: int):
    c = _canvas(320, 200)
    tab = TabWidget("tabs", labels=["Dashboard", "Controls"],
                    initial_tab=active)
    page1 = FlexContainer("pg1", direction="column", align="stretch")
    page1.gap, page1.padding = 6, (8, 8, 8, 8)
    r = RelayWidget("tr", label="Alert", state_engine="stop_light")
    r.on_bus_state("ns", "green")
    page1.add(r)
    page2 = FlexContainer("pg2", direction="column", align="stretch")
    page2.gap, page2.padding = 6, (8, 8, 8, 8)
    b = ButtonWidget("tb", label="Start", state_engine="binary")
    page2.add(b)
    cb = CheckboxWidget("tc", label="auto-restart")
    cb.checked = True
    page2.add(cb)
    tab.add(page1).add(page2)
    tab.active_index = active
    rect = Rect(6, 4, 308, 190)
    tab.measure(Constraints(rect.w, rect.h))
    tab.arrange(rect)
    tab.draw(c, rect)
    return c


def _coordinate_states():
    c = _canvas(260, 250)
    states = [
        ("dark", "", False),
        ("empty_flashing", "", True),
        ("partial", "314", True),
        ("committed", "31415926", True),
    ]
    for i, (status, digits, armed) in enumerate(states):
        w = CoordinateWidget(f"co{i}", label="Coordinates", max_digits=8)
        w.digits, w.status, w.armed = digits, status, armed
        w._now = lambda: 0.25          # blink-on phase
        w.draw(c, Rect(6, 2 + i * 62, 248, 60))
    return c


def _callout_scene():
    c = _canvas(320, 200)
    w = CalloutWidget("ca1", text="coolant pump", target=(0.72, 0.68))
    w.draw(c, Rect(20, 20, 280, 160))
    w2 = CalloutWidget("ca2", text="alert", target=(0.9, 0.2),
                       color=(0xEF, 0x53, 0x50))
    w2.draw(c, Rect(20, 20, 280, 160))
    return c


def _image_decode_contract():
    """Render a decoded image through the decode_image contract seam
    ([CXC-R-DRAW] 2026-07-06) with fit=contain."""
    src = SkiaCanvas()
    src.create_surface(80, 40)
    src.set_fill_color(0x26, 0xA6, 0x9A)
    src.fill_rect(0, 0, 80, 40)
    src.set_fill_color(0xEF, 0x53, 0x50)
    src.fill_circle(20, 20, 12)
    import io
    import skia as _skia
    img = src._surface.makeImageSnapshot()
    png_bytes = bytes(img.encodeToData(_skia.EncodedImageFormat.kPNG, 100))

    c = _canvas(260, 170)
    w = ImageWidget("img", src="test://inline", fit="contain")
    w._handle = c.decode_image(png_bytes)   # the promoted contract method
    assert w._handle is not None, "decode_image must decode PNG bytes"
    w._tried = True
    w.draw(c, Rect(30, 15, 200, 140))
    return c


_SCENES = {
    "m2_button_states": _button_states,
    "m2_button_blink": _button_blink,
    "m2_relay_states": _relay_states,
    "m2_checkbox_states": _checkbox_states,
    "m2_text_input_states": _text_input_states,
    "m2_command_states": _command_states,
    "m2_power_toggle_states": _power_toggle_states,
    "m2_doc_picker_rows": _doc_picker_rows,
    "m2_bus_monitor_lines": _bus_monitor_lines,
    "m2_tab_active0": lambda: _tab_widget(0),
    "m2_tab_active1": lambda: _tab_widget(1),
    "m2_coordinate_states": _coordinate_states,
    "m2_callout_scene": _callout_scene,
    "m2_image_decode_contract": _image_decode_contract,
}


@pytest.mark.parametrize("name", sorted(_SCENES))
def test_m2_widget_matches_golden(name):
    c = _SCENES[name]()
    actual = surface_to_array(c)
    path = REFERENCE_DIR / f"{name}.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        c.save_png(str(path))
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference(name)
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"{name}: {ratio * 100:.2f}% of pixels exceed tolerance"


# -- full composition golden ([CXC-RM-M2] acceptance) -----------------------

def test_m2_dashboard_composition_golden(tmp_path):
    """The production test_dashboard.compose.yaml renders end-to-end via
    the engine — the M2 gate composition, golden-pinned."""
    from composeio.render.engine.runtime import RenderApp
    root_yaml = os.path.join(PY_ROOT, os.pardir, "test_dashboard.compose.yaml")
    if not os.path.isfile(root_yaml):
        pytest.skip("production sample not present")
    app = RenderApp.from_file(root_yaml, windowed=False)
    app.start()
    out = os.path.join(str(tmp_path), "dash.png")
    app.render_headless(out)
    actual = surface_to_array(app.canvas)
    app.stop()

    path = REFERENCE_DIR / "m2_dashboard_composition.png"
    if not path.exists() or os.environ.get("CXC_REGEN_GOLDENS"):
        import shutil
        shutil.copy(out, path)
        pytest.skip(f"golden (re)generated: {path}")
    expected = load_reference("m2_dashboard_composition")
    match, ratio = images_match(actual, expected, tolerance=2,
                                max_diff_ratio=0.005)
    assert match, f"composition: {ratio * 100:.2f}% of pixels exceed tolerance"
