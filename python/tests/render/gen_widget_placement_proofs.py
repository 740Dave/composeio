#!/usr/bin/env python3
"""Generate the VISUAL PROOFS for the fit-aware widget-placement build
([DEC-WIDGET-PLACEMENT-CALLS-SETTLED]) — the artifacts David reviews on glass:

  * config_collab_fit_aware.png — the collaboration widget-select surface with
    the rail-MISFIT rows greyed + carrying a "→ add on Dashboard" redirect
    (D2: the silent no-op made visible).
  * decisions_summary_card.png — the compact Decisions-summary rail card
    rendered with a real self/decisions projection (D4: OPEN/SUPERSEDED/TOTAL +
    the latest call + freshness + the Explore ▸ jump chip).
  * rail_hot_added_decisions.png — a live console rail (account card) AFTER the
    Decisions-summary card was hot-added into it via add_widget_live, bound to
    live self/decisions data (D3: the config genuinely ADDs, lit up now).

    PYTHONPATH=python:compose_projects/intelligence/console python3 \\
        python/tests/render/gen_widget_placement_proofs.py
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
for p in (PY_ROOT, os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")):
    if p not in sys.path:
        sys.path.insert(0, p)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.widget_select import WidgetSelectWidget
from composeio.render.widgets.decisions_summary import DecisionsSummaryWidget
from composeio.render.engine.runtime import RenderApp
import personalization as P

OUT = os.path.join(HERE, "refs", "widget_placement")

DECISIONS = {
    "nodes": [
        {"id": "DEC-95-HANDOFF-LINE",
         "title": "The handoff line is the contract",
         "lifecycle-position": "active", "is_anchor": True,
         "session": "2026-07-22"},
        {"id": "DEC-90-COMPACT-PREP", "title": "Compact prep is the transcript",
         "lifecycle-position": "superseded", "superseded": True,
         "session": "2026-07-20"},
        {"id": "DEC-CROSSING-IS-CHEAP", "title": "Crossing the wire is cheap",
         "lifecycle-position": "active", "session": "2026-07-21"},
        {"id": "DEC-WIDGET-PLACEMENT", "title": "Fit-aware placement",
         "lifecycle-position": "active", "session": "2026-07-25"},
    ],
    "edges": [], "anchor": "DEC-95-HANDOFF-LINE", "corpus": "intelligence-console",
}


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, val, source=None):
        pass


def _draw(name, widget, payload_ns, payload, *, w, h):
    ct.set_theme("dark")
    widget.on_mount(_Ctx())
    if payload is not None:
        widget.on_bus_state(payload_ns, payload)
    c = SkiaCanvas(); c.create_surface(w, h)
    c.set_fill_color(*ct.CONSOLE.active.bg)
    c.fill_rect(0, 0, w, h)
    widget.arranged_rect = Rect(8, 8, w - 16, h - 16)
    widget.draw(c, Rect(8, 8, w - 16, h - 16))
    path = os.path.join(OUT, name)
    c.save_png(path)
    print("  wrote", os.path.relpath(path, REPO_ROOT), os.path.getsize(path),
          "bytes")


def main():
    os.makedirs(OUT, exist_ok=True)

    print("D2 fit-aware collaboration selector (rail misfits disabled):")
    _draw("config_collab_fit_aware.png", WidgetSelectWidget("w1"), None, None,
          w=560, h=760)

    print("D4 compact Decisions-summary rail card:")
    _draw("decisions_summary_card.png", DecisionsSummaryWidget("ds"),
          "self/decisions", DECISIONS, w=360, h=176)

    print("D3 hot-add — Decisions card mounted into a live rail:")
    ct.set_theme("dark")
    scene = {"design": {"width": 372, "height": 560, "padding": 20, "gap": 13},
             "widgets": [{"type": "ordered_stack", "id": "rail_col",
                          "direction": "column", "align": "stretch", "gap": 13,
                          "width": 332,
                          "state_namespace": "_app/console/collab_order",
                          "children": [
                              {"type": "account_card", "id": "acct",
                               "namespace": "self/rate_limit",
                               "account_id": "david740burnett", "width": 332}]}]}
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    app.publish("self/rate_limit", {"used_pct": 42, "window": "5h",
                                    "resets_in": "2h 14m"})
    app.publish("self/decisions", DECISIONS)
    P.hot_add_collaboration_widget(app, "decisions_summary")
    app.render_frame()
    path = os.path.join(OUT, "rail_hot_added_decisions.png")
    app.canvas.save_png(path)
    print("  wrote", os.path.relpath(path, REPO_ROOT),
          os.path.getsize(path), "bytes")
    app.stop()
    print("done ->", os.path.relpath(OUT, REPO_ROOT))


if __name__ == "__main__":
    main()
