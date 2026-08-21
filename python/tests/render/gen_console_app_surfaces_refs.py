#!/usr/bin/env python3
"""Generate the app-level console-surface REFERENCE images
([SPEC:CXC-CONSOLE-PROD] surfaces 4-6 + the Config Appearance selector) — the
artifacts David reviews on the glass:

  * config_appearance_dark.png / _light.png — the Config Appearance selector
    (theme · font-scale · dark custom colors), proving the selector CHANGES the
    live look (dark vs light render).
  * config_collab_widgets.png — the S4 Collaboration widget-selection editor,
    registry-driven, with one widget hidden + one reordered.
  * dashboard_empty.png — the empty-but-usable Dashboard (palette add affordance).
  * dashboard_placed.png — the Dashboard HOSTING gauge + intelligence_card +
    avatar at their grid cells.
  * dashboard_plugged.png — after a (simulated) restart with a NEW registry
    descriptor: it appears in the palette with no selector edit (S6).

Deterministic (ft_text) so byte-stable across a re-run and across macOS/RPi.

    PYTHONPATH=python python3 python/tests/render/gen_console_app_surfaces_refs.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect, Constraints
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.console_prefs import ConsolePrefsWidget
from composeio.render.widgets.widget_select import WidgetSelectWidget
from composeio.render.widgets.dashboard import DashboardWidget
from composeio.render.engine.widget_registry import (
    register, WidgetDescriptor, WIDGET_REGISTRY, canonical_key)
from composeio.render.widgets.gauge import GaugeWidget

OUT = os.path.join(HERE, "refs", "console_app_surfaces")


class _Ctx:
    theme = "dark"
    reduced_motion = False

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, val, source=None):
        pass


def _render(name, widget, *, w=974, h=1000, theme="dark", font_scale=1.0,
            custom=None):
    ct.set_theme(theme)
    ct.clear_custom_colors()
    if custom:
        ct.apply_custom_colors(custom)
    ct.set_font_scale(font_scale)
    ctx = _Ctx()
    ctx.theme = theme
    widget.on_mount(ctx)
    if hasattr(widget, "arrange"):
        widget.arranged_rect = Rect(0, 0, w, h)
        try:
            widget.arrange(Rect(0, 0, w, h))
        except Exception:
            pass
    c = SkiaCanvas(); c.create_surface(w, h)
    c.set_fill_color(*ct.CONSOLE.active.bg)
    c.fill_rect(0, 0, w, h)
    widget.draw(c, Rect(0, 0, w, h))
    path = os.path.join(OUT, name)
    c.save_png(path)
    ct.set_theme("dark"); ct.clear_custom_colors(); ct.set_font_scale(1.0)
    print("  wrote", os.path.relpath(path, os.path.join(PY_ROOT, os.pardir)),
          os.path.getsize(path), "bytes")


def main():
    os.makedirs(OUT, exist_ok=True)

    print("Config Appearance selector (theme/font/custom):")
    _render("config_appearance_dark.png", ConsolePrefsWidget("a1"),
            theme="dark")
    _render("config_appearance_light.png", ConsolePrefsWidget("a2"),
            theme="light")
    _render("config_appearance_dark_amber.png", ConsolePrefsWidget("a3"),
            theme="dark", custom={"bg": [12, 8, 20], "accent": [255, 138, 0]})

    print("S4 Collaboration widget selection:")
    ws = WidgetSelectWidget("w1")
    # hide the first + move the third to the top (a realistic edit)
    ws._rows[0]["visible"] = False
    ws._rows.insert(0, ws._rows.pop(2))
    _render("config_collab_widgets.png", ws, h=1200)

    print("S5 Dashboard:")
    _render("dashboard_empty.png", DashboardWidget("d0"), w=1256, h=900)
    placed = DashboardWidget("d1", placements=[
        {"widget": "gauge", "row": 0, "col": 0, "w": 2, "h": 1},
        {"widget": "intelligence_card", "row": 0, "col": 2, "w": 1, "h": 1},
        {"widget": "avatar", "row": 0, "col": 3, "w": 1, "h": 1},
        {"widget": "account_card", "row": 1, "col": 0, "w": 2, "h": 1}])
    _render("dashboard_placed.png", placed, w=1256, h=900)

    print("S6 pluggable — a NEW descriptor in the palette after restart:")
    key = canonical_key("newkpiwidget")
    added = key not in WIDGET_REGISTRY
    try:
        register(WidgetDescriptor(
            type="newkpiwidget", cls=GaugeWidget,
            display_name="AAA New KPI",     # sorts first so it shows in the strip
            required_namespaces=["kpi/value"], category="dashboard-eligible"))
        _render("dashboard_plugged.png", DashboardWidget("d2"), w=1256, h=900)
    finally:
        if added:
            WIDGET_REGISTRY.pop(key, None)

    print("done ->", os.path.relpath(OUT, os.path.join(PY_ROOT, os.pardir)))


if __name__ == "__main__":
    main()
