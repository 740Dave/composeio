#!/usr/bin/env python3
"""
M2 live-window interaction smoke (requires a display; not run by pytest).

    python3 tests/render/smoke_m2_interaction.py [--keep-open SECONDS]

Proves the M2 interactive acceptance on the REAL input path (SDL event
queue -> engine dispatch), using the minnow-bringup production config:

  1. Window opens on the production composition (no placeholders).
  2. Click the hostname TextInput -> it takes focus (CXC-R-ENGINE-FOCUS).
  3. Inject SDL_TEXTINPUT "fleetnode" + Return -> characters appear at the
     cursor and the commit publishes bringup/hostname ([CXC-R-C8]).
  4. Click the avahi Checkbox -> bringup/opt_avahi == "on".
  5. Click a Button (test dashboard config) -> state engine advances and
     publishes; LED follows.
  6. Frame-rate sanity while redrawing (cursor blink forces frames).
  7. Screenshots for the human eyeball.

Exit code 0 = all checks passed.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.widgets import (ButtonWidget, CheckboxWidget,
                                      TextInputWidget)

MINNOW = os.path.join(PY_ROOT, os.pardir, "apps", "compose_projects", "minnow-bringup",
                      "minnow-bringup.compose.yaml")
DASH = os.path.join(PY_ROOT, os.pardir, "test_dashboard.compose.yaml")
OUT = os.environ.get("CXC_SMOKE_OUT", "/tmp/cxc_smoke_m2")


def _frames(app: RenderApp, n: int) -> None:
    for _ in range(n):
        app.process_events()
        if app.animator.tick():
            app._dirty = True
        app.render_frame()


def _center(w) -> tuple:
    r = w.arranged_rect
    return (r.x + r.w / 2, r.y + r.h / 2)


def _click(app: RenderApp, x: float, y: float) -> None:
    app.canvas.inject_pointer("down", x, y)
    _frames(app, 1)
    app.canvas.inject_pointer("up", x, y)
    _frames(app, 2)


def main() -> int:
    keep_open = 0.0
    if "--keep-open" in sys.argv:
        keep_open = float(sys.argv[sys.argv.index("--keep-open") + 1])
    os.makedirs(OUT, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    # ---- minnow-bringup: TextInput + Checkbox on the real window ---------
    print("== minnow-bringup (production config) ==")
    app = RenderApp.from_file(MINNOW, windowed=True)
    app.start()
    _frames(app, 3)

    widgets = list(app.comp.root.iter_widgets())
    check("no placeholders in production config",
          all(w.widget_type != "placeholder" for w in widgets))

    hostname = next(w for w in widgets
                    if isinstance(w, TextInputWidget)
                    and w.bound_namespace == "bringup/hostname")
    hx, hy = _center(hostname)
    _click(app, hx, hy)
    check("TextInput takes focus on click", hostname.focused)

    for ch in "fleetnode":
        app.canvas.inject_text(ch)
    _frames(app, 2)
    check("typed characters appear at cursor (CXC-R-C8)",
          hostname.text == "fleetnode", f"text={hostname.text!r}")

    app.canvas.inject_key("Return")
    _frames(app, 2)
    committed = app.bus.get_state("bringup/hostname")
    check("Return commits -> namespace published",
          committed == "fleetnode", f"bus={committed!r}")

    avahi = next(w for w in widgets
                 if isinstance(w, CheckboxWidget)
                 and w.bound_namespace == "bringup/opt_avahi")
    _click(app, *_center(avahi))
    check("Checkbox toggle publishes on-value",
          app.bus.get_state("bringup/opt_avahi") == "on")

    # frame rate while a focused TextInput forces cursor-blink redraws
    _click(app, hx, hy)
    n, t0 = 90, time.monotonic()
    _frames(app, n)
    fps = n / (time.monotonic() - t0)
    check("frame budget with live cursor", fps >= 55, f"{fps:.1f} fps")

    shot = os.path.join(OUT, "m2_minnow_window.png")
    app.canvas.save_png(shot)
    print(f"  screenshot: {shot}")
    if keep_open:
        time.sleep(keep_open)
    app.stop()

    # ---- test dashboard: Button advance-and-publish ------------------------
    print("== test_dashboard (buttons + relay + gauge) ==")
    app2 = RenderApp.from_file(DASH, windowed=True)
    app2.start()
    _frames(app2, 3)
    btn = next(w for w in app2.comp.root.iter_widgets()
               if isinstance(w, ButtonWidget) and w.bound_namespace == "ftl_arm")
    _click(app2, *_center(btn))
    val = app2.bus.get_state("ftl_arm")
    check("Button tap advances state engine + publishes",
          val == "on", f"ftl_arm={val!r}")
    _frames(app2, 2)
    check("Button LED follows bus state", btn._is_active)

    evt = app2.bus.get_state("_app/render/interaction")
    check("interaction event published for Intelligence observers",
          bool(evt) and evt.get("type") == "tap")

    shot2 = os.path.join(OUT, "m2_dashboard_window.png")
    app2.canvas.save_png(shot2)
    print(f"  screenshot: {shot2}")
    if keep_open:
        time.sleep(keep_open)
    app2.stop()

    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
