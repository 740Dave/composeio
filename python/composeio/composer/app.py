"""ComposerApp — the M4 Composer: core + chrome on the render engine.

    python -m composeio.composer [design.compose.yaml] [--fullscreen]

The Composer window is itself a RenderApp: the same engine, event loop,
and Canvas backend that runs deployed widgets runs the tool that builds
them ([CXC-RM-M4] dogfooding gate).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from ..render.engine.runtime import RenderApp

from .core import ComposerCore
from .document import CompositeDocument
from .ui import ComposerUI

logger = logging.getLogger("composeio.composer.app")


_MOD_SHIFT = 0x0003
_MOD_CTRL = 0x00C0
_MOD_GUI = 0x0C00       # Cmd on macOS


class ComposerRenderApp(RenderApp):
    """RenderApp with Composer key routing: when nothing holds focus,
    keys go to the Workshop (arrow-nudge, delete).  Cmd/Ctrl+Z undoes,
    Cmd+Shift+Z / Ctrl+Y redoes ([CXC-BE-POLISH])."""

    workshop: Any = None
    core: Any = None

    def _handle_key(self, ev: Any) -> None:
        if ev.type == "down" and self.core is not None:
            mods = int(ev.modifiers or 0)
            primary = mods & (_MOD_CTRL | _MOD_GUI)
            key = str(ev.key).lower()
            if primary and key == "z":
                if mods & _MOD_SHIFT:
                    self.core.redo()
                else:
                    self.core.undo()
                self.mark_dirty()
                return
            if primary and key == "y":
                self.core.redo()
                self.mark_dirty()
                return
        if (ev.type == "down" and self.workshop is not None
                and self.focus.focused is None
                and ev.key not in ("Tab",)):
            self.workshop.on_key(ev.key, ev.modifiers)
            self.mark_dirty()
            return
        super()._handle_key(ev)


class ComposerApp:
    """Bus-native Composer: ComposerCore (dual-bus, headless) + ComposerUI
    (engine chrome) + ComposerRenderApp (window/event loop)."""

    def __init__(self, path: str = "", *, windowed: bool = True,
                 fullscreen: bool = False,
                 connect_mqtt: bool = True) -> None:
        document = CompositeDocument()
        if path and os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                document = CompositeDocument.from_yaml(f.read())

        self.core = ComposerCore(document)
        self.core.start(connect_mqtt=connect_mqtt)
        if path and os.path.isfile(path):
            self.core.file_path = path

        self.ui = ComposerUI(self.core)
        self.render = ComposerRenderApp(
            self.ui.composition(), windowed=windowed, fullscreen=fullscreen,
            bus=self.core.internal_bus)
        self.render.workshop = self.ui.workshop
        self.render.core = self.core
        self.ui.wire(self.render)

    # -- lifecycle ------------------------------------------------------------

    def run(self, **kwargs: Any) -> None:
        try:
            self.render.run(**kwargs)
        finally:
            self.core.stop()

    def start(self) -> None:
        self.render.start()

    def stop(self) -> None:
        self.render.stop()
        self.core.stop()

    def render_headless(self, out_png: str) -> str:
        return self.render.render_headless(out_png)


def main() -> None:
    import argparse
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(
        description="ComposeIO Composer (render-engine, M4)")
    ap.add_argument("design", nargs="?", default="",
                    help="a .compose.yaml composite to open")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--headless", metavar="OUT_PNG",
                    help="render one frame to PNG and exit")
    args = ap.parse_args()

    if args.headless:
        app = ComposerApp(args.design, windowed=False, connect_mqtt=False)
        app.start()
        print(app.render_headless(args.headless))
        app.stop()
        return

    app = ComposerApp(args.design, fullscreen=args.fullscreen)
    app.run()


if __name__ == "__main__":
    main()
