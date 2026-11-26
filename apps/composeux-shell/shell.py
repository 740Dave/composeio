#!/usr/bin/env python3
"""ComposeUX Shell — a bus-native app launcher / home screen.

Boots a launcher surface (bus://surfaces/launcher.json) whose DocPicker (launch mode) lists the
apps registry (bus://apps/*.json). Selecting an app:
  - resolves the app's surface (bus://surfaces/<id>.json) fresh from the bus,
  - RESIZES the window to the app's declared size (surface.design.width/height) — different
    apps have different window needs, so size travels with the surface,
  - wires the app's driver (invoke_action + optional feed) imported from the app doc,
  - shows a persistent "← Apps" back button to return to the launcher.

The shell is part of the local bootstrap kernel (composeio + resolve + this file). Everything
above it — the launcher surface, the apps, their surfaces and drivers — streams from the bus.
Adding an app is `put_doc` into `apps` + `surfaces`; it appears on the launcher, no redeploy.

Rendering surface: the Skia render engine (`composeio.render.engine`). The engine owns the
window and the frame loop, so the shell mounts ONE RenderApp per surface and swaps by
stopping the current run-loop and starting the next — which is also how each app gets its
own declared window size for free. The "← Apps" affordance is not shell chrome outside the
surface any more: it is a real ButtonWidget the shell STAMPS into the surface, publishing
`composeux/shell/back`. A finger and an Intelligence return to the launcher the same way —
by putting that value on the bus.

Run:  python3 shell.py
"""
from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # .../composeio
sys.path.insert(0, str(REPO / "python"))

LAUNCHER_SURFACE = "bus://surfaces/launcher.json"
_APP_DEFAULT = (820, 640)
_LAUNCHER_DEFAULT = (460, 560)
BACK_NS = "composeux/shell/back"     # a back-button tap lands here; the shell listens


def _strip(doc: dict) -> dict:
    for k in ("_id", "_created_at", "_updated_at"):
        doc.pop(k, None)
    return doc


class ShellLogic:
    """GUI-free resolution: app_id -> (surface, driver, window_size, app_doc). Unit-testable."""

    def resolve_app(self, app_id: str):
        from composeio.resolve import resolve_doc
        app = _strip(resolve_doc(f"bus://apps/{app_id}.json"))
        surface = _strip(resolve_doc(app["surface"]))
        d = surface.get("design", {})
        size = (int(d.get("width", _APP_DEFAULT[0])), int(d.get("height", _APP_DEFAULT[1])))
        return surface, self.import_driver(app.get("driver")), size, app

    def import_driver(self, spec):
        """Import an app driver from a repo-relative path; exposes invoke_action + optional feed(bus)."""
        if not spec:
            return None
        path = (REPO / spec) if not Path(spec).is_absolute() else Path(spec)
        if not path.exists():
            return None
        s = importlib.util.spec_from_file_location("appdriver_" + path.stem, path)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        return m


def _build_bus(surface: dict):
    from composeio.bus import ComposeIOBus
    from composeio.render.engine.components import expand_components
    surface = expand_components(surface)
    bus = ComposeIOBus(bus_id="composeux-shell")
    bus.start()
    for ns in surface.get("namespaces", []):
        bus.register_namespace(ns["name"], ns.get("state_engine", "json"), label=ns.get("label"))
    return bus, surface


def add_back_button(surface: dict, title: str) -> dict:
    """Stamp the "← Apps" affordance into *surface* as a real widget (shallow copy).

    A bar module at grid_row 0 carrying the app title + a ButtonWidget bound to
    BACK_NS; every other module shifts down one row. `json` engine deliberately —
    BACK_NS carries a free-form token, and a strict engine that rejects it would
    abort the process (the classic engine-mismatch fault).
    """
    out = dict(surface)
    bar = {"name": "shell", "label": title, "grid_row": 0, "grid_column": 0,
           "widgets": [{"type": "ButtonWidget", "label": "← Apps", "row": 0, "column": 0,
                        "publish_namespace": BACK_NS, "publish_value": "launcher"}]}
    shifted = []
    for m in surface.get("modules", []) or []:
        m = dict(m) if isinstance(m, dict) else m
        if isinstance(m, dict):
            m["grid_row"] = int(m.get("grid_row", 0)) + 1
        shifted.append(m)
    out["modules"] = [bar] + shifted
    out["namespaces"] = list(surface.get("namespaces", []) or []) + [
        {"name": BACK_NS, "state_engine": "json", "label": "Back to launcher"}]
    return out


def request_stop(app) -> None:
    """Ask the engine run-loop to close after the current frame — the same deferred
    shutdown a widget reaches through EngineContext.request_stop()."""
    from composeio.render.engine.runtime import EngineContext
    EngineContext(app).request_stop()


class Shell:
    """Mounts one surface at a time on the render engine and swaps on launch/back."""

    def __init__(self):
        self.logic = ShellLogic()
        self.app = None          # the live RenderApp
        self.bus = None          # the live surface's bus
        self._next = None        # ("launcher", None) | ("app", app_id) | None => quit

    # -- swap requests (both arrive on the render thread) ---------------------

    def launch(self, app_id: str):
        """ctx.launch — the DocPicker's launch-mode selection."""
        self._next = ("app", str(app_id))
        if self.app is not None:
            request_stop(self.app)

    def show_launcher(self, *_a):
        """The "← Apps" button's BACK_NS publish (bus subscriber signature)."""
        self._next = ("launcher", None)
        if self.app is not None:
            request_stop(self.app)

    # -- mount ----------------------------------------------------------------

    def _mount(self, surface, size, title, invoke=None, feed=None, back=False):
        """Build a RenderApp for *surface* and run it until launch/back/close."""
        from composeio.render.engine.parser import parse_widget_tree
        from composeio.render.engine.runtime import RenderApp
        if back:
            surface = add_back_button(surface, title)
        self.bus, surface = _build_bus(surface)
        comp = parse_widget_tree(surface)
        comp.title = title
        comp.width, comp.height = size                 # per-app window size
        comp.resizable = True
        self.app = RenderApp(comp, bus=self.bus, invoke=invoke, launch=self.launch)
        if back:
            self.bus.subscribe(BACK_NS, self.show_launcher)
        if callable(feed):
            threading.Thread(target=lambda: feed(self.bus), daemon=True).start()
        try:
            self.app.run()                             # blocks until the loop stops
        finally:
            self.app = None
            try:
                self.bus.stop()                        # shell-owned bus; engine won't
            except Exception:
                pass
            self.bus = None

    def _mount_launcher(self):
        from composeio.resolve import resolve_doc
        surface = _strip(resolve_doc(LAUNCHER_SURFACE))
        d = surface.get("design", {})
        size = (int(d.get("width", _LAUNCHER_DEFAULT[0])), int(d.get("height", _LAUNCHER_DEFAULT[1])))
        self._mount(surface, size, d.get("title", "ComposeUX"))

    def _mount_app(self, app_id: str):
        surface, driver, size, app = self.logic.resolve_app(app_id)
        self._mount(surface, size, app.get("title", app_id),
                    invoke=getattr(driver, "invoke_action", None) if driver else None,
                    feed=getattr(driver, "feed", None) if driver else None,
                    back=True)

    def run(self):
        target = ("launcher", None)
        while target is not None:
            self._next = None                          # cleared before each mount
            kind, app_id = target
            if kind == "launcher":
                self._mount_launcher()
            else:
                self._mount_app(app_id)
            target = self._next                        # None (window closed) => quit


def main():
    Shell().run()


if __name__ == "__main__":
    main()
