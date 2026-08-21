"""
Scene-level regression guard for the console render-loop SPIN
([CXC-R-DAMAGE-ONCHANGE]) — the guard the suite lacked.

The widget-placement build ([DEC-WIDGET-PLACEMENT-CALLS-SETTLED]) shipped a new
always-visible rail projection card whose ``on_bus_state`` damaged on EVERY
delivery.  Unit tests and single-frame captures were green (one delivery, one
paint), but a LIVE redelivery stream — the retained-value replay a hot-add
re-bind fires, a family fan-out, or the decisions_publisher re-emitting the same
graph on its interval — turned the idempotent card into a continuous-damage
source: the render loop never idled and pegged a core.

These drive the REAL console scene and assert damage/redraw STABILIZES:

  * IDLE (no input, ambient motion off): after the first layout, the scene
    requests ZERO further redraws — a healthy scene is quiescent.
  * A REDELIVERY STREAM of an UNCHANGED projection marks NO damage after the
    first real change — the spin's exact trigger, now bounded.

Both are invisible to a single-frame capture; only a multi-frame / multi-
delivery drive sees a live redraw loop.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.widget import Constraints, Rect
from composeio.render.widgets import DecisionsSummaryWidget
from composeio.render.widgets import console_theme as ct

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")

W, H = 1280, 1740

DECISIONS = {
    "nodes": [
        {"id": "DEC-A", "title": "Alpha decision", "lifecycle-position": "active",
         "is_anchor": True, "session": "2026-07-22"},
        {"id": "DEC-B", "title": "Beta decision", "lifecycle-position": "superseded",
         "superseded": True, "session": "2026-07-20"},
    ],
    "edges": [], "anchor": "DEC-A", "corpus": "intelligence-console",
}


def teardown_function(_fn):
    ct.set_theme("dark")


def _walk(w):
    yield w
    for c in getattr(w, "children", []) or []:
        yield from _walk(c)


def _console(reduced_motion=True):
    app = RenderApp.from_file(SCENE, windowed=False)
    app.comp.width, app.comp.height = W, H
    # ambient motion OFF: the avatar breathe / live-dot beat legitimately damage
    # every frame ([CXC-ICV-MOTION]); reduced_motion isolates the state-driven
    # damage this guard is about, so a HEALTHY idle scene is quiescent.
    app.comp.reduced_motion = reduced_motion
    app.start()
    app._running = True
    app.comp.root.measure(Constraints(W, H))
    app.comp.root.arrange(Rect(0, 0, W, H))
    app.render_frame()          # settle the first layout
    return app


def _idle_frame(app, t):
    app.process_events()
    app._maybe_publish_bbox(t)
    app._poll_geometry(t)
    if app.animator.tick():
        app._damage_widgets(app.animator.last_touched)
    if app.behaviors.tick(t):
        app._damage_widgets(app.behaviors.last_touched)
    app._tick_clocked(t)
    dirty = app._dirty
    if app._dirty:
        app.render_dirty()
    return dirty


def test_idle_console_stabilizes_to_zero_redraws():
    """A quiescent console (no input, ambient off) requests ~0 redraws/frame
    after the initial layout — the loop IDLES."""
    app = _console(reduced_motion=True)
    t = 1000.0
    for _ in range(5):                # settle
        t += 1 / 60.0
        _idle_frame(app, t)
    dirty = 0
    N = 40
    for _ in range(N):
        t += 1 / 60.0
        if _idle_frame(app, t):
            dirty += 1
    assert dirty == 0, f"idle console re-rendered {dirty}/{N} frames (spin)"


def test_redelivery_stream_of_unchanged_projection_is_bounded():
    """A rail Decisions card fed the SAME projection repeatedly (a hot-add
    re-bind replay / family fan-out / publisher re-emit) marks damage ONCE for
    the real change, then NOTHING — [CXC-R-DAMAGE-ONCHANGE]."""
    app = _console(reduced_motion=True)
    rail = next(w for w in _walk(app.comp.root)
                if getattr(w, "widget_id", None) == "rail_col")
    card = DecisionsSummaryWidget("rail_decisions_guard")
    rail.children.insert(0, card)
    card.on_mount(app._engine_ctx)                   # set _ctx (like a hot-add)
    app.comp.subscriptions.append(("self/decisions", card))
    app._wire_subscription("self/decisions", card)   # bind like a launch/hot-add
    app.comp.root.measure(Constraints(W, H))
    app.comp.root.arrange(Rect(0, 0, W, H))

    # count damage marks the card drives through the engine
    marks = [0]
    orig = app.mark_damage
    orig_dirty = app.mark_dirty

    def _md(rect):
        marks[0] += 1
        return orig(rect)

    def _mdirty():
        marks[0] += 1
        return orig_dirty()

    app.mark_damage = _md
    app.mark_dirty = _mdirty

    card.on_bus_state("self/decisions", DECISIONS)   # the one real change
    first = marks[0]
    assert first >= 1                                # it DID damage on the change
    for _ in range(30):                              # 30 identical redeliveries
        card.on_bus_state("self/decisions", DECISIONS)
    assert marks[0] == first, (
        f"unchanged redelivery kept damaging: {marks[0] - first} extra marks "
        f"over 30 redeliveries (continuous-damage spin)")
