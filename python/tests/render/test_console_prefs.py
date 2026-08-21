"""
Tests for the Configuration-tab Appearance selector ([SPEC:CXC-CONSOLE-PROD]
Config selectors — the WRITE side of the render-deep personalization,
[CXC-ICP-S3-THEME] / [CXC-ICP-S2-FONTSCALE] + David's D2 dark custom colors).

Acceptance: the theme / font-scale options are DRIVEN BY the theme registry
(list_themes() / FONT_SCALE_STEPS), never a literal; a pick PUBLISHES Foundation
A's console/* namespace (a theme string, a font-scale FLOAT, a merged
theme_custom dict); a bus re-seed reflects the persisted value.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.console_prefs import (
    ConsolePrefsWidget, DEFAULT_THEME_NS, DEFAULT_FONT_NS, DEFAULT_CUSTOM_NS)

from .conftest import surface_to_array

RECT = Rect(0, 0, 974, 900)


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def teardown_function(_fn):
    ct.set_theme("dark")
    ct.clear_custom_colors()
    ct.set_font_scale(1.0)


def _mounted():
    w = ConsolePrefsWidget("appr")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    return w, ctx


def _drawn(w):
    c = SkiaCanvas(); c.create_surface(974, 900)
    w.draw(c, RECT)
    return c


def _zone(w, kind, value=None):
    for (x0, y0, x1, y1, k, v) in w._zones:
        if k == kind and (value is None or v == value):
            return (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return None


# --- registry-driven options (never a literal) -------------------------------

def test_theme_chips_come_from_the_registry():
    w, _ = _mounted()
    _drawn(w)
    theme_vals = [v for (_a, _b, _c, _d, k, v) in w._zones if k == "theme"]
    assert theme_vals == ct.list_themes()            # exact registry order
    assert "high-contrast" in theme_vals             # D2 a11y theme present


def test_font_chips_come_from_the_step_table():
    w, _ = _mounted()
    _drawn(w)
    font_vals = sorted(v for (_a, _b, _c, _d, k, v) in w._zones if k == "font")
    assert font_vals == sorted(ct.FONT_SCALE_STEPS.values())


# --- picks publish Foundation A namespaces -----------------------------------

def test_theme_pick_publishes_the_theme_string():
    w, ctx = _mounted()
    _drawn(w)
    cx, cy = _zone(w, "theme", "high-contrast")
    w.on_tap(cx, cy)
    assert (DEFAULT_THEME_NS, "high-contrast") in ctx.published


def test_font_pick_publishes_a_float_not_a_label():
    """config_persister coerces font_scale via float() — a step LABEL would
    no-op, so the widget must publish the numeric step value."""
    w, ctx = _mounted()
    _drawn(w)
    cx, cy = _zone(w, "font", ct.FONT_SCALE_STEPS["l"])
    w.on_tap(cx, cy)
    ns, val = ctx.published[-1]
    assert ns == DEFAULT_FONT_NS
    assert isinstance(val, float) and val == ct.FONT_SCALE_STEPS["l"]


def test_custom_swatch_publishes_merged_dict():
    w, ctx = _mounted()
    _drawn(w)
    cx, cy = _zone(w, "custom:accent", [255, 138, 0])
    w.on_tap(cx, cy)
    ns, val = ctx.published[-1]
    assert ns == DEFAULT_CUSTOM_NS
    assert val["accent"] == [255, 138, 0]
    # add a bg too — the payload MERGES both channels
    cx, cy = _zone(w, "custom:bg", [3, 4, 8])
    w.on_tap(cx, cy)
    _ns, val2 = ctx.published[-1]
    assert val2["accent"] == [255, 138, 0] and val2["bg"] == [3, 4, 8]


def test_custom_reset_publishes_empty_map():
    w, ctx = _mounted()
    _drawn(w)
    w.on_tap(*_zone(w, "custom:accent", [230, 60, 160]))
    w.on_tap(*_zone(w, "custom_reset"))
    ns, val = ctx.published[-1]
    assert ns == DEFAULT_CUSTOM_NS and val == {}


# --- bus reflection (config_persister re-seed) -------------------------------

def test_bus_reseed_reflects_persisted_values():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_THEME_NS, "light")
    w.on_bus_state(DEFAULT_FONT_NS, 1.25)
    w.on_bus_state(DEFAULT_CUSTOM_NS, {"accent": [255, 138, 0]})
    assert w._theme == "light"
    assert w._font_scale == 1.25
    assert list(w._custom["accent"])[:3] == [255, 138, 0]
    # a malformed color is dropped, not stored
    w.on_bus_state(DEFAULT_CUSTOM_NS, {"accent": ["x", 1, 2]})
    assert "accent" not in w._custom


# --- parser wiring -----------------------------------------------------------

def test_parser_wires_three_subscriptions():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "console_prefs", "id": "a"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert DEFAULT_THEME_NS in subbed
    assert DEFAULT_FONT_NS in subbed
    assert DEFAULT_CUSTOM_NS in subbed
    assert isinstance(comp.subscriptions[0][1], ConsolePrefsWidget)


# --- render (glass-checkable) ------------------------------------------------

def test_render_paints_all_three_cards():
    w, _ = _mounted()
    c = _drawn(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    # each card recorded its chips/swatches
    kinds = {k for (_a, _b, _c, _d, k, _v) in w._zones}
    assert "theme" in kinds and "font" in kinds
    assert any(k.startswith("custom:") for k in kinds)
