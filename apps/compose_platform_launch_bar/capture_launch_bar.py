#!/usr/bin/env python3
"""Headless capture of the ComposePlatform Launch Bar ribbon
([SPEC:CXC-LAUNCHBAR] [CXC-LB-VERIFY] LB-C1 glass evidence).

Renders launch_bar.compose.yaml via the raster reference (windowed=False) in the
IDLE state — no persistent highlight anywhere (David 2026-07-29: the previs dark
box is the click-and-hold PRESSED chip, not an active-app indicator).  Asset
`src`es are repo-root-relative, so the cwd is pinned to the repo root — the same
cwd the launcher spawns under.

    python3 apps/compose_platform_launch_bar/capture_launch_bar.py [out.png]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "python"))
os.chdir(REPO_ROOT)

from composeio.render.engine.runtime import RenderApp  # noqa: E402

SCENE = REPO_ROOT / "apps/compose_platform_launch_bar" / "launch_bar.compose.yaml"


def capture(out_png: str) -> str:
    app = RenderApp.from_file(str(SCENE), windowed=False)
    app.start()
    app.render_frame_at(0.0)
    app.canvas.save_png(out_png)  # type: ignore[attr-defined]
    app.stop()
    return out_png


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else str(
        REPO_ROOT / "apps/compose_platform_launch_bar" / "capture.png")
    print("captured ->", capture(out))
