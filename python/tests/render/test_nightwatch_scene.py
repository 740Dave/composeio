"""
Headless render proof for the redesigned Night Watch window
(compose_projects/intelligence/console/nightwatch.compose.yaml, David 2026-07-22).

Proves the scene: (a) opens at its 540×636 default AND is RESIZABLE with
content reflow + persisted geometry ([SPEC:CXC-RENDER] [CXC-R-WINDOW-RESIZE],
David 2026-07-29 — SUPERSEDES the 2026-07-22 "size to content, nothing growable"
directive: the Away Watch is a Designer-family helper and meets the same resize
parity bar as the console); (b) carries the title "Away Watch: <weekday, date @
HH:MM>"; (c) has NO gauge and NO avatar; (d) surfaces Decisions/Tokens/Elapsed +
explicit start/end; (e) draws the done/partial/deferred task checklist; (f)
shows the git-tree state line.  Renders the exact deployed scene through
RenderApp.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.dirname(PY_ROOT)
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest

from composeio.render.engine.runtime import RenderApp
from composeio.render.widgets import console_theme as ct

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "nightwatch.compose.yaml")


def teardown_function(_fn):
    ct.set_theme("dark")


def _app():
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    return app


def test_scene_default_size_and_resize_optin():
    # Opens at 540x636 by default AND opts into the Designer-family resize +
    # persist seam ([CXC-R-WINDOW-RESIZE], window_key away-watch).
    app = _app()
    try:
        assert (app.comp.width, app.comp.height) == (540, 636)
        assert app.comp.resizable is True
        assert app.comp.persist_geometry is True
        assert app.comp.window_key == "away-watch"
        assert (app.comp.min_width, app.comp.min_height) == (480, 560)
    finally:
        app.stop()


def _leaf_rects(app):
    """Arranged rects of every non-container leaf (the actual content ink)."""
    app.render_frame_at(0.0)
    rects = []
    for w in app.comp.root.iter_widgets():
        if getattr(w, "children", None):        # skip root + pixel_stack
            continue
        r = getattr(w, "arranged_rect", None)
        if r is not None:
            rects.append(r)
    return rects


def test_panel_fills_window_no_overflow_no_clip():
    """padding:0/gap:0 on the root -> the chrome frame (the flex 'watch' column,
    grow:1) fills the window exactly, so its rounded bottom corner and the
    closing line are never clipped, at the default size AND when resized."""
    for (W, H) in [(540, 636), (480, 560), (1100, 900)]:
        app = RenderApp.from_file(SCENE, windowed=False)
        app.start()
        try:
            app.comp.width, app.comp.height = W, H
            app.render_frame_at(0.0)
            panel = next(w for w in app.comp.root.iter_widgets()
                         if w.widget_id == "watch")
            r = panel.arranged_rect
            assert (r.x, r.y) == (0.0, 0.0)
            assert (r.w, r.h) == (float(W), float(H))
        finally:
            app.stop()


def test_content_fits_window_snugly():
    """Content spans the window with only intended interior padding: nothing
    is clipped (max bottom <= height) and there is no large empty band
    (top/bottom gutters are modest and comparable)."""
    app = _app()
    try:
        rects = _leaf_rects(app)
        top = min(r.y for r in rects)
        bottom = max(r.y + r.h for r in rects)
        H = app.comp.height
        # no VERTICAL clipping (the flagged axis).  (Horizontal label rects are
        # rough-measured overestimates the chrome panel legitimately clips;
        # real text ink fits — see the render proof.)
        assert bottom <= H
        # snug: interior gutters are real padding, not a big empty margin
        assert 0 < top <= 30
        assert 0 <= (H - bottom) <= 30
        # top and bottom gutters are comparable (not lopsided)
        assert abs(top - (H - bottom)) <= 20
    finally:
        app.stop()


def test_layout_reflows_the_detail_pane_fills_free_height():
    """[CXC-R-WINDOW-RESIZE] SUPERSEDES the old 'nothing growable' directive:
    the frame + detail tab pane declare flex grow so the layout REFLOWS, and the
    detail pane actually consumes the extra height when the window grows taller
    (the metrics stay at the top, the footer stays at the bottom)."""
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    ids = {w.widget_id: w for w in comp.root.iter_widgets()}
    assert getattr(ids["watch"].layout, "grow", 0.0) == 1.0     # frame fills
    assert getattr(ids["detail"].layout, "grow", 0.0) == 1.0    # pane grows

    from composeio.render.widget import Constraints, Rect

    def _pane_h(H):
        c = parse_compose_yaml(SCENE)
        c.width, c.height = 540, H
        c.root.measure(Constraints(540, H))
        c.root.arrange(Rect(0, 0, 540, H))
        detail = next(w for w in c.root.iter_widgets()
                      if w.widget_id == "detail")
        return detail.arranged_rect.h

    # a taller window gives the detail pane more height (it grows to fill)
    assert _pane_h(900) > _pane_h(636) + 100


def test_scene_has_no_gauge_or_avatar():
    app = _app()
    try:
        types = {w.widget_type for w in app.comp.root.iter_widgets()}
        assert "gauge" not in types
        assert "avatar" not in types
        # the checklist leaf IS present
        assert "nightwatch_checklist" in types
    finally:
        app.stop()


def test_scene_renders_new_content(tmp_path):
    app = _app()
    try:
        drawn = []
        real = app.canvas.draw_text
        app.canvas.draw_text = lambda s, *a, **k: (drawn.append(s),
                                                   real(s, *a, **k))[1]
        app.render_frame_at(0.0)
        joined = "".join(drawn)
        # new title (weekday computed into the default)
        assert "Away Watch: Tue, Jul 21 @ 23:50" in joined
        # swapped metrics + their labels
        assert "decisions made" in joined and "tokens consumed" in joined
        assert "elapsed" in joined
        assert "23.7M" in joined
        # explicit start / end
        assert "Started" in joined and "Ended" in joined
        # the task checklist (mixed states) + git-tree line
        assert "DONE" in joined and "DEFERRED" in joined and "PARTIAL" in joined
        assert "GIT TREE" in joined
        # the removed vocabulary is gone
        assert "night energy" not in joined
        # save the proof PNG
        out = os.path.join(str(tmp_path), "nightwatch_window.png")
        app.canvas.save_png(out)
        assert os.path.exists(out) and os.path.getsize(out) > 4000
    finally:
        app.stop()
