#!/usr/bin/env python3
"""
Live-window smoke test for M0+M1 (requires a display; not run by pytest).

    python3 tests/render/smoke_live_window.py [--keep-open SECONDS]

Sequence:
  1. Launch the demo composition in a real SDL window.
  2. Capture frame 1 -> /tmp/cxc_smoke/window_initial.png and pixel-diff
     it against a headless reference render of the same composition
     (window contents must be byte-identical to the raster reference).
  3. Inject a synthetic click on the composite's z4 LED through the SDL
     event queue (the real input path), run frames, and assert:
     bus value toggled, interaction event published, LED redrew.
  4. Animate the composite gauge 0 -> 6000 RPM and measure achieved fps.
  5. Publish a bus value from "outside" and verify the gauge updated
     within one frame.
  6. Capture the final frame for the human eyeball.
Exit code 0 = all checks passed.
"""

from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
sys.path.insert(0, PY_ROOT)
sys.path.insert(0, os.path.join(PY_ROOT, "scripts"))

from composeio.render.engine.runtime import INTERACTION_NS, RenderApp, _pace
from pixel_comparison import pixel_equivalency

DEMO = os.path.join(PY_ROOT, "composeio", "render", "examples",
                    "render_demo.compose.yaml")
OUT = "/tmp/cxc_smoke"


def main() -> int:
    keep_open = 0.0
    if "--keep-open" in sys.argv:
        keep_open = float(sys.argv[sys.argv.index("--keep-open") + 1])
    os.makedirs(OUT, exist_ok=True)
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}  {detail}")

    # -- 1. headless reference ------------------------------------------------
    ref_app = RenderApp.from_file(DEMO, windowed=False)
    ref_app.start()
    ref_png = ref_app.render_headless(os.path.join(OUT, "reference.png"))
    ref_app.stop()

    # -- 2. live window --------------------------------------------------------
    app = RenderApp.from_file(DEMO, windowed=True)
    app.start()
    app.run(max_frames=5)  # warm-up frames; run() calls stop() at the end...
    # run() tears down; restart for the interactive phase
    app = RenderApp.from_file(DEMO, windowed=True)
    app.start()
    for _ in range(3):
        app.process_events()
        app.render_frame()

    win_png = os.path.join(OUT, "window_initial.png")
    app.canvas.save_png(win_png)
    stats = pixel_equivalency(ref_png, win_png,
                              output_dir=os.path.join(OUT, "window_vs_ref"))
    check("window surface == raster reference",
          stats["identical_pct"] == 100.0,
          f"identical={stats['identical_pct']}%")

    # ground truth: pixels the RENDERER presented (catches channel-order
    # bugs in the texture blit that surface reads cannot see)
    data = app.canvas.read_presented_pixels()
    ok_present = False
    detail = "readback unavailable"
    if data:
        import numpy as np
        from PIL import Image
        pw, ph = app.canvas._presented_size
        a = np.array(Image.frombytes("RGBA", (pw, ph), data)
                     .convert("RGB")
                     .resize((app.comp.width, app.comp.height), Image.NEAREST))
        b = np.array(Image.open(win_png).convert("RGB"))
        d = np.abs(a.astype(int) - b.astype(int)).max(axis=2)
        ok_present = float((d <= 3).mean()) == 1.0
        detail = f"max_delta={int(d.max())}"
        Image.frombytes("RGBA", (pw, ph), data).save(
            os.path.join(OUT, "window_presented.png"))
    check("presented pixels == surface (channel order)", ok_present, detail)

    # -- 3. synthetic click through the REAL SDL event path ---------------------
    led = next(w for w in app.comp.root.iter_widgets()
               if w.widget_id == "z4_led")
    r = led.arranged_rect
    cx, cy = r.x + r.w / 2, r.y + r.h / 2
    app.canvas.inject_pointer("down", cx, cy)
    app.canvas.inject_pointer("up", cx, cy)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline and led.state == "off":
        app.process_events()
        if app._dirty:
            app.render_frame()
        time.sleep(0.005)
    check("SDL click -> hit-test -> bus toggle -> LED on",
          led.state == "on" and app.bus.get_state("system_status") == 1,
          f"led.state={led.state}")
    evt = app.bus.get_state(INTERACTION_NS)
    check("interaction event on bus",
          bool(evt) and evt.get("widget_id") == "z4_led", str(evt))
    app.canvas.save_png(os.path.join(OUT, "window_after_click.png"))

    # -- 4. animation fps in the LIVE window ------------------------------------
    gauge = next(w for w in app.comp.root.iter_widgets()
                 if w.widget_id == "z3_rpm")
    gauge.animate("value", 0, 6000, duration=1000, easing="ease_in_out")
    frames = 0
    t0 = time.monotonic()
    while app.animator.active and time.monotonic() - t0 < 3.0:
        f0 = time.monotonic()
        app.process_events()
        app.animator.tick()
        app.render_frame()
        frames += 1
        _pace(f0 + 1 / 60)
    elapsed = time.monotonic() - t0
    fps = frames / elapsed
    check("gauge animates at 60fps (windowed)", fps >= 55,
          f"{fps:.1f} fps over {frames} frames")

    # -- 5. external bus publish -> one-frame update ------------------------------
    before = gauge.value
    app.publish("engine_rpm", 3200, source="smoke-external")
    updated_immediately = gauge.value == 3200 and app._dirty
    app.render_frame()
    check("bus publish updates widget within one frame",
          updated_immediately, f"value {before} -> {gauge.value}")

    app.publish("engine_temp", 101.5, source="smoke-external")
    app.publish("status_text", "SMOKE: all systems exercised", source="smoke")
    app.render_frame()
    app.canvas.save_png(os.path.join(OUT, "window_final.png"))

    if keep_open > 0:
        print(f"  window open for {keep_open}s (eyeball it)...")
        t0 = time.monotonic()
        while time.monotonic() - t0 < keep_open:
            if not app.process_events():
                break
            if app.animator.tick() or app._dirty:
                app.render_frame()
            time.sleep(1 / 60)

    app.stop()

    failed = [c for c in checks if not c[1]]
    print(f"\n{'FAIL' if failed else 'PASS'}: "
          f"{len(checks) - len(failed)}/{len(checks)} live-window checks")
    print(f"captures in {OUT}/")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
