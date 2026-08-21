#!/usr/bin/env python3
"""Headless frame-capture proof for the target-designation lock-on
([CXC-BIND-STATES] / [CXC-BIND-TRANSITION] / [CXC-RM-GESTURE]).

Drives examples/target_designation.compose.yaml through the full lock-on
trace by GESTURE (long-press -> drag -> release), sampling deterministic
frames via render_frame_at(t), and writes PNGs + a SHA-256 manifest.

    PYTHONPATH=python python3 python/scripts/capture_target_designation.py [outdir]

The manifest hashes prove the transition is byte-reproducible (they match
across a re-run and across platforms, the [CXC-BIND-C6] gate).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp

EXAMPLE = os.path.join(PY_ROOT, "composeio", "render", "examples",
                       "target_designation.compose.yaml")


class _P:
    def __init__(self, type_, x, y):
        self.type = type_
        self.x, self.y = float(x), float(y)
        self.pointer_id = 0
        self.dx = self.dy = 0.0


def _shot(app, outdir, name):
    app.canvas.save_png(os.path.join(outdir, f"{name}.png"))
    import numpy as np  # noqa
    from tests.render.conftest import surface_to_array
    h = hashlib.sha256(surface_to_array(app.canvas).tobytes()).hexdigest()
    print(f"  {name:22s} {h}")
    return h


def main() -> None:
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        PY_ROOT, "tests", "render", "captures", "target_designation")
    os.makedirs(outdir, exist_ok=True)
    sys.path.insert(0, PY_ROOT)  # for tests.render.conftest

    app = RenderApp.from_file(EXAMPLE, windowed=False)
    app.start()
    manifest = {}

    app.render_frame_at(0.0)
    manifest["00_rest"] = _shot(app, outdir, "00_rest_none")

    # finger: long-press the scope -> fire_control/lock = acquiring
    app._dispatch_pointer(_P("down", 240, 240))
    app._pointer_state[0]["t"] -= 1.0
    app._dispatch_pointer(_P("up", 240, 240))
    app.render_frame_at(0.02)                 # activation stamps tween start
    app.render_frame_at(0.09)                 # ~40% into the 180ms fade
    manifest["01_acquiring_in"] = _shot(app, outdir, "01_acquiring_rising")
    app.render_frame_at(0.20)
    manifest["02_acquiring_settled"] = _shot(app, outdir, "02_acquiring_settled")

    # finger: drag to steer the reticle, release -> lock = tracking
    app._dispatch_pointer(_P("down", 240, 240))
    app._dispatch_pointer(_P("move", 300, 190))
    app._dispatch_pointer(_P("up", 300, 190))
    app.render_frame_at(0.30)                 # tracking activation (t re-based)
    manifest["03_lock_settling"] = _shot(app, outdir, "03_lock_settling")
    app.render_frame_at(0.55)                 # cubic-bezier scale settled + red
    manifest["04_locked"] = _shot(app, outdir, "04_locked")
    app.render_frame_at(0.95)                 # pulse breathing while locked
    manifest["05_locked_pulse"] = _shot(app, outdir, "05_locked_pulse")

    with open(os.path.join(outdir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"wrote {len(manifest)} frames + manifest.json -> {outdir}")


if __name__ == "__main__":
    main()
