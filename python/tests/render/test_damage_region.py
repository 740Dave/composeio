"""
Tests for the engine dirty-region / damage rendering path ([CXC-R-DAMAGE]).

The input-latency fix: a widget damages a rect and the engine rasters +
presents ONLY that region instead of the whole surface.  These tests pin the
two properties that make it correct and useful:

  1. SCOPED   — a partial redraw draws only the widgets whose paint bounds
                intersect the damage; far-away subtrees are pruned (never
                draw()'d), which is the raster win the embedded backends need.
  2. IDENTICAL — the region a partial redraw composites is byte-for-byte the
                 same as the corresponding region of a full redraw
                 ([CXC-R-DAMAGE-C1]); scribbling then partial-redrawing
                 restores the exact pixels.

Plus the Canvas-contract addition (present_partial) and the DamageRegion
accumulator unit behavior.
"""

from __future__ import annotations

import os
import sys

import numpy as np
import skia

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.damage import (AA_MARGIN, DamageRegion,
                                             rects_intersect)
from composeio.render.engine.runtime import RenderApp
from composeio.render.widget import Rect, Widget

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


def _arr(canvas) -> np.ndarray:
    return canvas._surface.makeImageSnapshot().toarray(
        colorType=skia.ColorType.kRGBA_8888_ColorType)


def _find(w, wid):
    if getattr(w, "widget_id", None) == wid:
        return w
    for ch in getattr(w, "children", []) or []:
        r = _find(ch, wid)
        if r is not None:
            return r
    return None


# ===========================================================================
# DamageRegion accumulator
# ===========================================================================

def test_damage_region_full_and_empty():
    d = DamageRegion()
    assert d.is_empty and not d.is_full
    d.add_full()
    assert d.is_full and not d.is_empty
    # once full, a rect cannot narrow it back
    d.add_rect(Rect(0, 0, 10, 10))
    assert d.is_full


def test_damage_region_none_rect_escalates_to_full():
    d = DamageRegion()
    d.add_rect(None)                    # a widget with no arranged rect yet
    assert d.is_full


def test_damage_region_bounds_union_and_aa_margin():
    d = DamageRegion()
    d.add_rect(Rect(100, 100, 20, 20))
    d.add_rect(Rect(200, 140, 10, 10))
    b = d.bounds()
    # union of the two rects, each inflated by the AA margin
    assert b.x == 100 - AA_MARGIN
    assert b.y == 100 - AA_MARGIN
    assert b.x + b.w == 210 + AA_MARGIN
    assert b.y + b.h == 150 + AA_MARGIN


def test_damage_region_intersects():
    d = DamageRegion()
    d.add_rect(Rect(100, 100, 20, 20))
    assert d.intersects(Rect(110, 110, 5, 5))
    assert not d.intersects(Rect(400, 400, 5, 5))
    assert not d.intersects(None)
    # a FULL region intersects everything, including unknown bounds
    d.add_full()
    assert d.intersects(None) and d.intersects(Rect(999, 999, 1, 1))


# ===========================================================================
# Widget paint bounds contract
# ===========================================================================

def test_paint_bounds_default_is_arranged_rect():
    w = Widget("x", "test")
    assert w.paint_bounds() is None            # no layout yet
    w.arranged_rect = Rect(10, 20, 30, 40)
    assert w.paint_bounds() == Rect(10, 20, 30, 40)


def test_paint_bounds_grows_by_overflow():
    w = Widget("x", "test")
    w.arranged_rect = Rect(10, 20, 30, 40)
    w.paint_overflow = 5.0
    pb = w.paint_bounds()
    assert pb == Rect(5, 15, 40, 50)


def test_console_cards_declare_shadow_overflow():
    """The rail cards whose ambient shadow paints past their rect declare a
    non-zero paint_overflow, so partial redraws include them correctly."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        # [DEC-INTELLIGENCE-WIDGET-CONSOLIDATION] the six rail cards consolidated
        # into three (Intelligence / Account / Artifacts) + the header avatar;
        # each still declares the ambient-shadow overflow.
        for wid in ("presence", "intelligence_panel", "account_panel",
                    "deliverables_panel"):
            w = _find(app.comp.root, wid)
            assert w.paint_overflow > 0, wid
    finally:
        app.stop()


# ===========================================================================
# Pruning: only intersecting subtrees draw
# ===========================================================================

def test_partial_redraw_prunes_non_intersecting_widgets():
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        thread = _find(app.comp.root, _th())

        drawn = set()
        for w in (field, thread):
            orig = w.draw

            def wrap(canvas, rect, _o=orig, _id=w.widget_id):
                drawn.add(_id)
                return _o(canvas, rect)
            w.draw = wrap

        # Damage only the composer field: the thread (far above, opaque field
        # occludes its shadow) must be pruned.
        dmg = DamageRegion()
        dmg.add_rect(field.arranged_rect)
        app._draw_partial(dmg)

        assert _cf() in drawn
        assert "thread" not in drawn
    finally:
        app.stop()


# ===========================================================================
# Correctness: partial redraw == full redraw (pixel-identical)
# ===========================================================================

def _full_reference() -> np.ndarray:
    ref = RenderApp.from_file(SCENE, windowed=False)
    ref.start()
    ref.render_frame()      # drain the initial: seed updates -> steady state
    out = _arr(ref.canvas).copy()
    ref.stop()
    return out


def test_partial_redraw_pixel_identical_to_full():
    """Scribble a damaged region, partial-redraw it, and assert the whole
    surface matches a full render within the golden tolerance
    ([CXC-R-DAMAGE-C1])."""
    full = _full_reference()
    # tolerance mirrors the golden comparison (conftest.images_match)
    TOL, MAX_RATIO = 2, 0.005
    for wid in (_cf(), "presence", "intelligence_panel", "live_dot",
                "account_panel", "deliverables_panel"):
        app = RenderApp.from_file(SCENE, windowed=False)
        app.start()
        try:
            w = _find(app.comp.root, wid)
            app.render_frame()          # steady state (seeds drained)
            r = w.arranged_rect
            c = app.canvas
            # prove the region is genuinely repainted (not left untouched)
            c.save()
            c.set_fill_color(255, 0, 255)
            c.fill_rect(r.x, r.y, r.w, r.h)
            c.restore()
            dmg = DamageRegion()
            dmg.add_rect(w.arranged_rect)
            app._draw_partial(dmg)
            got = _arr(app.canvas)
            diff = np.abs(got.astype(np.int16) - full.astype(np.int16))
            ratio = np.any(diff > TOL, axis=-1).sum() / diff[..., 0].size
            assert ratio <= MAX_RATIO, f"{wid}: diff ratio {ratio:.2e}"
        finally:
            app.stop()


def test_keystroke_damages_only_the_composer_field():
    """A text event through the real engine path damages a region contained
    in the focused field's paint bounds — not the whole surface."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.windowed = True     # let process-less event dispatch mark damage
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        app.focus.focus(field)
        # fresh frame clears startup damage
        app._damage = DamageRegion()
        app._dirty = False

        class _TextEvent:
            type = "text"
            text = "h"
        _TextEvent.__name__ = "KeyEvent"
        app._handle_key(_TextEvent())

        assert app._dirty
        assert not app._damage.is_full, "keystroke must not dirty whole scene"
        b = app._damage.bounds()
        fb = field.paint_bounds()
        # the damaged bbox lies within the field's (AA-inflated) paint bounds
        assert b.x >= fb.x - AA_MARGIN - 0.01
        assert b.y >= fb.y - AA_MARGIN - 0.01
        assert b.x + b.w <= fb.x + fb.w + AA_MARGIN + 0.01
        assert b.y + b.h <= fb.y + fb.h + AA_MARGIN + 0.01
    finally:
        app.windowed = False
        app.stop()


def test_keydown_printable_does_not_dirty_whole_scene():
    """A printable key's DOWN event must NOT force a full redraw ([CXC-R-DAMAGE]).

    Regression: SDL emits BOTH a KEYDOWN and a TEXTINPUT per printable char, and
    focus.handle_key returned True whenever any widget was focused (on_key is a
    no-op for a plain letter, but the return was unconditional), so every keydown
    hit mark_dirty() and re-rastered the whole (fidelity-rich, expensive) scene
    alongside the scoped text path — the active-typing half of the laggy-composer
    report.  The down event is now scoped to the focused widget."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.windowed = True     # let process-less event dispatch mark damage
    app.start()
    try:
        field = _find(app.comp.root, _cf())
        app.focus.focus(field)
        app._damage = DamageRegion()
        app._dirty = False

        class _KeyDown:
            type = "down"
            key = "A"
            modifiers = 0
        _KeyDown.__name__ = "KeyEvent"
        app._handle_key(_KeyDown())

        assert app._dirty
        assert not app._damage.is_full, \
            "printable keydown must not dirty the whole scene"
        b = app._damage.bounds()
        fb = field.paint_bounds()
        assert b.x >= fb.x - AA_MARGIN - 0.01
        assert b.y >= fb.y - AA_MARGIN - 0.01
        assert b.x + b.w <= fb.x + fb.w + AA_MARGIN + 0.01
        assert b.y + b.h <= fb.y + fb.h + AA_MARGIN + 0.01
    finally:
        app.windowed = False
        app.stop()


_FANIN_SCENE = {
    "app": {"id": "fanin"},
    "design": {"width": 400, "height": 800, "theme": "dark"},
    "namespaces": [
        {"name": "a/val", "state_engine": "json", "initial": 10},
        {"name": "b/val", "state_engine": "json", "initial": 10},
    ],
    "widgets": [{
        "type": "stack", "direction": "column", "gap": 20, "padding": 10,
        "children": [
            {"type": "gauge", "id": "gA", "namespace": "a/val",
             "min_val": 0, "max_val": 100, "height": 120},
            {"type": "rectangle", "id": "spacer", "width": 380, "height": 480,
             "fill_color": [20, 20, 20, 255]},
            {"type": "gauge", "id": "gB", "namespace": "b/val",
             "min_val": 0, "max_val": 100, "height": 120},
        ],
    }],
}


def test_bus_fanin_targeted_update_scopes_damage():
    """A bus publish to a bound widget's namespace damages ONLY that widget's
    card, never the whole surface ([CXC-R-DAMAGE]).  Regression: schedule_update
    used to escalate EVERY cross-thread bus update to a full redraw, so routine
    self/* telemetry re-rastered the (fidelity-rich, expensive) console every
    tick and stalled keystrokes.  The subscription wiring knows the target
    widget; the fan-in is now scoped to it, post-layout."""
    app = RenderApp.from_dict(_FANIN_SCENE, windowed=False)
    app.start()
    app.render_frame()                       # clean baseline: _damage empty
    try:
        gA = _find(app.comp.root, "gA")
        gB = _find(app.comp.root, "gB")
        assert gB.arranged_rect.y > 400       # gB sits low

        app.bus.publish_state("a/val", 85, source="test")
        app.drain_updates()
        app._layout()
        app._apply_drained_damage()

        assert not app._damage.is_full, \
            "targeted bus update must not dirty the whole scene"
        rects = app._damage.clamped_rects(400, 800)
        assert rects, "targeted update produced no damage"
        bottom = max(r.y + r.h for r in rects)
        assert bottom <= gB.arranged_rect.y, \
            f"damage bottom {bottom} reached gB at {gB.arranged_rect.y}"
        gy = gA.arranged_rect.y
        assert any(r.y <= gy + 60 and r.y + r.h >= gy for r in rects), \
            "damage does not cover the updated gauge gA"
    finally:
        app.stop()


def test_bus_fanin_untargeted_update_stays_full():
    """A cross-thread update with no known target (a behavior trigger, a
    widget-authored update) keeps the conservative full redraw."""
    app = RenderApp.from_dict(_FANIN_SCENE, windowed=False)
    app.start()
    app.render_frame()
    try:
        app.schedule_update(lambda: None, target=None)
        app.drain_updates()
        app._layout()
        app._apply_drained_damage()
        assert app._damage.is_full, "untargeted update must escalate to full"
    finally:
        app.stop()


def test_render_dirty_partial_matches_full_end_to_end():
    """Drive the full run-loop frame method (render_dirty) with a scoped
    damage and confirm the surface equals a full render."""
    full = _full_reference()
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        app.render_frame()              # steady state (seeds drained)
        gauge = _find(app.comp.root, "intelligence_panel")
        # scribble, then let render_dirty repaint only the damaged region
        r = gauge.arranged_rect
        c = app.canvas
        c.save(); c.set_fill_color(0, 255, 0); c.fill_rect(r.x, r.y, r.w, r.h)
        c.restore()
        app._damage = DamageRegion()
        app.mark_damage(gauge.arranged_rect)
        app.render_dirty()
        got = _arr(app.canvas)
        diff = np.abs(got.astype(np.int16) - full.astype(np.int16))
        ratio = np.any(diff > 2, axis=-1).sum() / diff[..., 0].size
        assert ratio <= 0.005, f"render_dirty diff ratio {ratio:.2e}"
    finally:
        app.stop()


# ===========================================================================
# Canvas contract: present_partial
# ===========================================================================

# ===========================================================================
# Disjoint damage rects: a partial redraw touches ONLY the rects, never their
# bounding box — the rail-card FLASH regression ([CXC-R-DAMAGE-C1]).
# ===========================================================================

def test_clamped_rects_are_disjoint_not_the_bounding_box():
    """Two far-apart damage rects clamp to TWO rects; a point that lies in
    their bounding box but in NEITHER rect (a card between them) is covered by
    the old bbox path but NOT by the per-rect list — the flash fix."""
    d = DamageRegion()
    d.add_rect(Rect(20, 1236, 826, 32))     # composer field (bottom-left)
    d.add_rect(Rect(936, 76, 332, 205))     # avatar presence (top-right)
    rects = d.clamped_rects(1280, 1300)
    assert len(rects) == 2

    def contains(x, y, w, h, px, py):
        return x <= px <= x + w and y <= py <= y + h

    # a rail card sitting between the two rects
    mid_x, mid_y = 1000, 700
    bx, by, bw, bh = d.clamped_bounds(1280, 1300)
    assert contains(bx, by, bw, bh, mid_x, mid_y)          # bbox WOULD clear it
    assert not any(contains(r.x, r.y, r.w, r.h, mid_x, mid_y)
                   for r in rects)                          # per-rect does not


def test_keystroke_frame_leaves_rail_cards_untouched():
    """A composer keystroke frame accumulates DISJOINT damage together with the
    ambient animators (header live-dot beat, avatar breathe, gauge hotpulse).
    Their bounding box spans the rail cards between them (registered / ledger),
    but a partial redraw must NOT clear or repaint those cards — that was the
    "two widgets flash on each keypress" regression.

    Proof: scribble the two cards, run the multi-rect partial redraw, and
    assert the scribble SURVIVES (the cards fell in no damage rect, so they are
    never touched).  The old bounding-box path cleared them to background."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        app.render_frame()                  # steady state (seeds drained)
        reg = _find(app.comp.root, "account_panel")
        led = _find(app.comp.root, "deliverables_panel")

        # scribble both card bodies magenta
        c = app.canvas
        c.save()
        c.set_fill_color(255, 0, 255)
        for w in (reg, led):
            r = w.arranged_rect
            c.fill_rect(r.x, r.y, r.w, r.h)
        c.restore()

        # a keystroke frame's disjoint damage: composer + ambient animators
        # (header live-dot beat + avatar breathe) — far from the two mid/lower
        # rail cards, whose bounding box the damage still spans.
        dmg = DamageRegion()
        for wid in (_cf(), "live_dot", "presence"):
            dmg.add_rect(_find(app.comp.root, wid).arranged_rect)

        # the damaged rect SET excludes the two cards' own rects
        rects = dmg.clamped_rects(app.comp.width, app.comp.height)
        for w in (reg, led):
            assert not any(rects_intersect(r, w.arranged_rect) for r in rects), \
                f"{w.widget_id} rect is in the keystroke damage set"

        app._draw_partial(dmg)
        got = _arr(app.canvas)

        # the scribble is intact -> the cards were never repainted (no flash)
        for w in (reg, led):
            r = w.arranged_rect
            sub = got[int(r.y):int(r.y + r.h), int(r.x):int(r.x + r.w)]
            assert sub[..., 0].mean() > 200 and sub[..., 1].mean() < 60, \
                f"{w.widget_id} was repainted by the keystroke (flash)"
    finally:
        app.stop()


def test_multi_rect_partial_redraw_pixel_identical_to_full():
    """The end-to-end guard: a scoped multi-rect damage (composer + ambient),
    rendered through render_dirty, composites the whole surface identically to
    a full render ([CXC-R-DAMAGE-C1]) — the per-rect path stays correct."""
    full = _full_reference()
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        app.render_frame()                  # steady state
        c = app.canvas
        # scribble each damaged widget's region so the redraw must restore it
        wids = (_cf(), "live_dot", "presence", "intelligence_panel")
        app._damage = DamageRegion()
        for wid in wids:
            r = _find(app.comp.root, wid).arranged_rect
            c.save(); c.set_fill_color(0, 255, 0)
            c.fill_rect(r.x, r.y, r.w, r.h); c.restore()
            app.mark_damage(_find(app.comp.root, wid).arranged_rect)
        app.render_dirty()
        got = _arr(app.canvas)
        diff = np.abs(got.astype(np.int16) - full.astype(np.int16))
        ratio = np.any(diff > 2, axis=-1).sum() / diff[..., 0].size
        assert ratio <= 0.005, f"multi-rect render_dirty diff ratio {ratio:.2e}"
    finally:
        app.stop()


def test_present_partial_default_falls_back_to_present():
    """A backend that does not override present_partial inherits a correct
    full present() — the damage info still flows, the backend just can't
    scope the flush."""
    from composeio.render.skia_backend import SkiaCanvas
    c = SkiaCanvas()
    c.create_surface(64, 64)
    # raster backend present() is a no-op; the point is it must not raise and
    # must accept the damage rects the engine hands every backend generically.
    c.present_partial([(0.0, 0.0, 10.0, 10.0)])


# ===========================================================================
# Redundant damage: the tree is rastered ONCE PER RECT, so duplicates cost
# whole frames ([CXC-R-DAMAGE] frame budget; found 2026-08-11)
# ===========================================================================

def test_contained_and_duplicate_rects_are_pruned_not_merged():
    """A frame accumulates damage for one widget from several sources — the
    widget's own request_redraw, and the pre/post-layout rects of each drained
    update targeting it.  Those are the SAME rect, and `_draw_partial` walks
    the pruned tree once per rect: four copies = four full rasters of the same
    pixels (measured 20.4 ms of draw for 5.1 ms of content on a Terminal Widget
    mid-burst).  Contained rects are dropped; DISJOINT rects are never merged."""
    from composeio.render.engine.damage import prune_contained

    big = Rect(100, 100, 400, 300)
    d = DamageRegion()
    for _ in range(4):
        d.add_rect(big)                       # the same rect, four sources
    d.add_rect(Rect(150, 150, 50, 50))        # strictly inside it
    d.add_rect(Rect(900, 900, 60, 60))        # far away — must SURVIVE
    rects = d.clamped_rects(1280, 1300)
    assert len(rects) == 2, f"expected big + far, got {rects}"
    assert any(r.w > 300 for r in rects) and any(r.w < 100 for r in rects)

    # Pure unit: order preserved, identical rects keep exactly one.
    a, b = Rect(0, 0, 10, 10), Rect(0, 0, 10, 10)
    assert prune_contained([a, b]) == [a]
    disjoint = [Rect(0, 0, 10, 10), Rect(50, 50, 10, 10)]
    assert prune_contained(disjoint) == disjoint


def test_pruning_damage_does_not_change_a_single_pixel():
    """The prune is an optimization, so it must be pixel-invisible: a frame whose
    damage carries redundant rects composites exactly what the un-pruned one did."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        app.render_frame()
        reference = _arr(app.canvas).copy()

        c = app.canvas
        r = _find(app.comp.root, _cf()).arranged_rect
        c.save(); c.set_fill_color(255, 0, 255)
        c.fill_rect(r.x, r.y, r.w, r.h); c.restore()

        app._damage = DamageRegion()
        app.mark_damage(r)                                    # duplicate...
        app.mark_damage(r)                                    # ...and again
        app.mark_damage(Rect(r.x + 4, r.y + 4, 8, 8))         # contained
        assert len(app._damage.clamped_rects(app.comp.width, app.comp.height)) == 1
        app.render_dirty()

        got = _arr(app.canvas)
        diff = np.abs(got.astype(np.int16) - reference.astype(np.int16))
        assert not np.any(diff > 2), "pruned damage changed pixels"
    finally:
        app.stop()


def test_a_scheduled_update_that_reschedules_itself_lands_NEXT_frame():
    """drain_updates runs the work QUEUED AT FRAME START, not until the queue is
    empty.  Without that boundary a widget consuming a bounded slice per tick and
    re-scheduling itself (the Terminal Widget's drain_once) has its follow-up run
    in the SAME frame — its cap bounds nothing and the frame is unbounded."""
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    try:
        for _ in range(50):                         # quiesce the scene's own updates
            if app.drain_updates() == 0:
                break

        runs = []

        def step():
            runs.append(len(runs))
            if len(runs) < 5:                       # re-schedule from inside
                app.schedule_update(step)

        app.schedule_update(step)
        assert app.drain_updates() == 1 and len(runs) == 1, "re-entered its own frame"
        assert app.drain_updates() == 1 and len(runs) == 2
        for _ in range(3):
            app.drain_updates()
        assert len(runs) == 5
        assert app.drain_updates() == 0             # and then it stops
    finally:
        app.stop()
