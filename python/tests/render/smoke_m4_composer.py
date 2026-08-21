#!/usr/bin/env python3
"""
M4 Composer live-window interaction smoke (requires a display; not pytest).

    python3 tests/render/smoke_m4_composer.py [--keep-open SECONDS]

Drives the REAL input path (SDL event queue -> engine dispatch) through
the redesigned Composer:

  1. Window opens on the three-zone chrome (WW / WC tabs / telemetry).
  2. Click the "Widgets" tab -> catalog shows (tab switch publishes
     composer/wc/active_tab).
  3. Click the Button catalog row -> widget lands on the composite,
     becomes the selection.
  4. Drag the selected widget +60,+40 -> composer/canvas/widget_moved
     command updates the document.
  5. Drag the SE selection handle -> widget_resized updates w/h.
  6. Click the "Layers" tab; click the row's eye -> layer hidden.
  7. Click the Bus cluster -> engine DialogWidget Bus Monitor opens
     (modal, hit-captured); click the scrim -> closes.
  8. Frame-rate sanity + screenshots.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

from composeio.composer.app import ComposerApp

OUT = os.environ.get("CXC_SMOKE_OUT", "/tmp/cxc_smoke_m4")


def _frames(app: ComposerApp, n: int) -> None:
    for _ in range(n):
        app.render.process_events()
        if app.render.animator.tick():
            app.render._dirty = True
        app.render.render_frame()


def _click(app: ComposerApp, x: float, y: float) -> None:
    app.render.canvas.inject_pointer("down", x, y)
    _frames(app, 1)
    app.render.canvas.inject_pointer("up", x, y)
    _frames(app, 3)


def _drag(app: ComposerApp, x0, y0, x1, y1, steps=6) -> None:
    app.render.canvas.inject_pointer("down", x0, y0)
    _frames(app, 1)
    for i in range(1, steps + 1):
        xi = x0 + (x1 - x0) * i / steps
        yi = y0 + (y1 - y0) * i / steps
        app.render.canvas.inject_pointer("move", xi, yi)
        _frames(app, 1)
    app.render.canvas.inject_pointer("up", x1, y1)
    _frames(app, 3)


def main() -> int:
    keep_open = 0.0
    if "--keep-open" in sys.argv:
        keep_open = float(sys.argv[sys.argv.index("--keep-open") + 1])
    os.makedirs(OUT, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    print("== M4 Composer (live window) ==")
    app = ComposerApp(windowed=True, connect_mqtt=False)
    app.start()
    _frames(app, 5)
    app.render.canvas.save_png(os.path.join(OUT, "m4_01_open.png"))
    check("window opens on the Composer chrome", True)

    ib = app.core.internal_bus
    doc = app.core.document
    tabs = app.ui.tabs

    # -- 2. Widgets tab via a real click on the segmented bar --------------
    tr = tabs.arranged_rect
    seg = tabs._seg_bounds
    wx = (seg[2][0] + seg[2][1]) / 2
    _click(app, wx, tr.y + 14)
    check("Widgets tab active after click",
          tabs.active_index == 2,
          f"bus state={ib.get_state('composer/wc/active_tab')}")
    check("tab state on the bus",
          ib.get_state("composer/wc/active_tab") == "widgets")

    # -- 3. add a Button from the catalog -----------------------------------
    cat = app.ui.catalog
    cr = cat.arranged_rect
    _click(app, cr.x + cr.w / 2, cr.y + 20)          # first row = Button
    layer = doc.layers[0] if doc.layers else None
    check("catalog click adds a Button layer",
          layer is not None and layer.widget_type == "button",
          f"layers={[(l.widget_id, l.widget_type) for l in doc.layers]}")
    check("new widget becomes the selection",
          app.ui.workshop.selected_id == (layer.widget_id if layer else ""))
    app.render.canvas.save_png(os.path.join(OUT, "m4_02_added.png"))

    # -- 4. drag it +60,+40 -----------------------------------------------------
    ox, oy = app.ui.workshop._origin
    x0, y0 = layer.x, layer.y
    cx = ox + layer.x + (layer.w or 100) / 2
    cy = oy + layer.y + (layer.h or 30) / 2
    _drag(app, cx, cy, cx + 60, cy + 40)
    check("drag moves the widget (widget_moved over the bus)",
          abs(layer.x - (x0 + 60)) <= 1 and abs(layer.y - (y0 + 40)) <= 1,
          f"({x0},{y0}) -> ({layer.x},{layer.y})")

    # -- 5. resize via the SE handle ---------------------------------------
    w0, h0 = layer.w, layer.h
    hx = ox + layer.x + layer.w
    hy = oy + layer.y + layer.h
    _drag(app, hx, hy, hx + 30, hy + 20)
    check("SE-handle drag resizes (widget_resized over the bus)",
          abs(layer.w - (w0 + 30)) <= 1 and abs(layer.h - (h0 + 20)) <= 1,
          f"{w0}x{h0} -> {layer.w}x{layer.h}")
    app.render.canvas.save_png(os.path.join(OUT, "m4_03_moved_resized.png"))

    # -- 6. Layers tab: eye toggles visibility ------------------------------
    wx = (seg[1][0] + seg[1][1]) / 2
    _click(app, wx, tr.y + 14)
    lp = app.ui.layers
    lr = lp.arranged_rect
    row_y = lr.y + lp._header_h() + lp.ROW_H / 2
    _click(app, lr.x + lr.w - 18, row_y)             # eye zone
    check("Layers-tab eye hides the layer",
          layer.visible is False)
    _click(app, lr.x + lr.w - 18, row_y)
    check("eye again shows the layer", layer.visible is True)
    app.render.canvas.save_png(os.path.join(OUT, "m4_04_layers.png"))

    # -- 7. Bus Monitor dialog ----------------------------------------------
    sb = app.ui.status.arranged_rect
    bz = app.ui.status._bus_zone
    _click(app, bz.x + 20, sb.y + sb.h / 2)
    check("Bus click opens the engine Dialog Bus Monitor",
          app.ui.bus_dialog.visible is True)
    app.render.canvas.save_png(os.path.join(OUT, "m4_05_busmon.png"))
    _click(app, 60, 200)                              # scrim
    check("scrim click dismisses the dialog",
          app.ui.bus_dialog.visible is False)

    # -- 8. fps sanity ----------------------------------------------------------
    t0 = time.monotonic()
    n = 120
    for _ in range(n):
        app.render.process_events()
        app.render.mark_dirty()
        app.render.render_frame()
    dt = time.monotonic() - t0
    fps = n / dt
    check("redraw rate sane", fps > 30, f"{fps:.1f} fps full-redraw")
    app.render.canvas.save_png(os.path.join(OUT, "m4_06_final.png"))

    if keep_open > 0:
        end = time.monotonic() + keep_open
        while time.monotonic() < end:
            _frames(app, 1)
            time.sleep(1 / 60)

    app.stop()
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed; "
          f"screenshots in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
