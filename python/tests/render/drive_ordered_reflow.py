"""
Visual proof for [SPEC:CXC-ORDER] + [CXC-ICP-REBIND].

Renders four captures into tests/render/captures/ordered/:

  console_rail_before.png  — the console collaboration rail, declared order
  console_rail_after.png   — after an S4 reorder+hide publish (every card
                             participated: 3 reordered, 1 hidden, compacted)
  dashboard_before.png     — a live dashboard with one card
  dashboard_after.png      — after a gauge is HOT-ADDED and lit with live data

Run:  python tests/render/drive_ordered_reflow.py
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

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")
OUT = os.path.join(HERE, "captures", "ordered")
os.makedirs(OUT, exist_ok=True)


def _rail_crop(app, path):
    """Save just the collaboration rail region so the reflow is legible."""
    rail = next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == "rail_col")
    r = rail.arranged_rect
    # a little context margin around the rail
    m = 20
    x0 = max(0, int(r.x - m))
    y0 = max(0, int(r.y - m))
    x1 = min(app.comp.width, int(r.x + r.w + m))
    y1 = min(app.comp.height, int(r.y + r.h + m))
    import skia
    img = app.canvas._surface.makeImageSnapshot(
        skia.IRect.MakeLTRB(x0, y0, x1, y1))
    img.save(path, skia.kPNG)
    return (x0, y0, x1 - x0, y1 - y0)


def console_reflow():
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    app.render_frame()
    box = _rail_crop(app, os.path.join(OUT, "console_rail_before.png"))
    order_before = [c.widget_id for c in
                    next(w for w in app.comp.root.iter_widgets()
                         if w.widget_id == "rail_col")._flow_children()]

    # S4: reorder ALL cards + hide one — the reflow reaches every widget.
    app.publish("_app/console/collab_order", {
        "order": ["away_watch_panel", "intelligence_panel",
                  "deliverables_panel"],
        "hidden": ["account_panel"]})
    app.render_frame()
    rail = next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == "rail_col")
    order_after = [c.widget_id for c in rail._flow_children()]
    _rail_crop(app, os.path.join(OUT, "console_rail_after.png"))
    print("console rail BEFORE:", order_before)
    print("console rail AFTER :", order_after, "(account_panel hidden:",
          not next(c for c in rail.children
                   if c.widget_id == "account_panel").visible, ")")
    print("  crop box:", box)


def dashboard_hot_add():
    scene = {
        "design": {"width": 380, "height": 300, "padding": 16, "gap": 12,
                   "background": [0x0D, 0x12, 0x1B]},
        "widgets": [
            {"type": "ordered_stack", "id": "dashboard", "direction": "column",
             "align": "stretch", "gap": 12, "state_namespace": "dash/order",
             "children": [
                 {"type": "gauge", "id": "seed_gauge", "label": "SEEDED",
                  "min_val": 0, "max_val": 100, "namespace": "dash/seed",
                  "height": 120, "style": "dial"},
             ]},
        ],
    }
    app = RenderApp.from_dict(scene, windowed=False)
    app.start()
    app.publish("dash/seed", 55.0)
    app.publish("dash/live", 88.0)             # producer already live
    app.render_frame()
    app.canvas.save_png(os.path.join(OUT, "dashboard_before.png"))

    dash = next(w for w in app.comp.root.iter_widgets()
                if w.widget_id == "dashboard")
    g = app.add_widget_live(dash, {
        "type": "gauge", "id": "added_gauge", "label": "HOT-ADDED",
        "min_val": 0, "max_val": 100, "namespace": "dash/live",
        "height": 120, "style": "dial"})
    app.render_frame()
    app.canvas.save_png(os.path.join(OUT, "dashboard_after.png"))
    print("dashboard hot-add: added_gauge.value =", g.value,
          "(live at add time, blank would be 0.0)")


if __name__ == "__main__":
    console_reflow()
    dashboard_hot_add()
    print("captures ->", OUT)
