#!/usr/bin/env python3
"""Generate the render-deep console personalization REFERENCE images
([SPEC:CXC-CONSOLE-PROD] surfaces 1-3) — the artifacts David reviews on the
glass: the 3 themes, the S/M/L font steps, the dark custom-colors, and two
reflow sizes.  Deterministic (ft_text) so they are byte-stable across a
re-run and across macOS/RPi.

    PYTHONPATH=python python3 python/tests/render/gen_console_personalization_refs.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import console_theme as ct

# [CXC-FC-UNIT] the console's collaboration surface is now N `intelligence_pane`
# instantiations, so every per-instance widget id is rooted at
# `<id_prefix>_<instance_id>_`. These ids are DERIVED from the scene (see
# `console_scene_ids`) rather than re-typed, so adding or repointing an instance
# moves the scene and these tests together.
from tests.render.console_scene_ids import (composer_field as _cf,  # noqa: E402
                                              composer_send as _cs,
                                              send_trigger_ns as _send_ns,
                                              thread as _th)

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")
OUT = os.path.join(HERE, "refs", "console_personalization")


def _find(root, wid):
    stack = [root]
    while stack:
        w = stack.pop()
        if getattr(w, "widget_id", None) == wid:
            return w
        stack.extend(getattr(w, "children", []) or [])
    return None


def render(name, *, w=1280, h=1740, theme="dark", font_scale=1.0, custom=None):
    ct.set_theme(theme)
    ct.clear_custom_colors()
    if custom:
        ct.apply_custom_colors(custom)
    ct.set_font_scale(font_scale)
    app = RenderApp.from_file(SCENE, windowed=False)
    app.comp.width, app.comp.height = w, h
    app.comp.font_scale = font_scale
    app.comp.theme = theme
    # accent chrome (send button) — mirror the launch applier: re-point to the
    # ACTIVE accent so a theme swap AND a custom accent both reach it.
    a = ct.CONSOLE.active.accent
    send = _find(app.comp.root, _cs())
    if send is not None:
        send.background = (a[0], a[1], a[2], 255)
    app.start()
    app.comp.root.measure(Constraints(w, h))
    app.comp.root.arrange(Rect(0, 0, w, h))
    path = os.path.join(OUT, name)
    app.render_headless(path)
    app.stop()
    ct.set_theme("dark")
    ct.clear_custom_colors()
    ct.set_font_scale(1.0)
    print("  wrote", os.path.relpath(path, REPO_ROOT),
          os.path.getsize(path), "bytes")


def main():
    os.makedirs(OUT, exist_ok=True)
    print("S3 themes:")
    for t in ct.list_themes():
        render(f"theme_{t}.png", theme=t)
    print("S2 font steps (S/M/L):")
    for label, step in (("s", 0.875), ("m", 1.0), ("l", 1.25)):
        render(f"font_{label}_{step}.png", font_scale=step)
    print("S3 dark custom colors (David's D2: bg + accent):")
    render("dark_custom_amber.png", theme="dark",
           custom={"bg": [12, 8, 20], "accent": [255, 138, 0]})
    render("dark_custom_magenta.png", theme="dark",
           custom={"bg": [8, 10, 22], "accent": [230, 60, 160]})
    print("S1 reflow sizes:")
    render("reflow_wide_1920x1200.png", w=1920, h=1200)
    render("reflow_compact_1024x768.png", w=1024, h=768)
    render("reflow_default_1280x1740.png", w=1280, h=1740)
    print("done ->", os.path.relpath(OUT, REPO_ROOT))


if __name__ == "__main__":
    main()
