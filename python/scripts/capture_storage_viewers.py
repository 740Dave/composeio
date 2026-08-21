#!/usr/bin/env python3
"""Headless captures for the two storage-viewer navigation shells + the
all-7-active launch-bar ribbon ([SPEC:CXC-LAUNCHBAR] [CXC-DOCNAV-*]/[CXC-BUSNAV-*]).

Populated browse lists are captured by injecting an in-memory fake adapter into
the scene's `store_browser` widget (no network); reflow is captured by rendering
the SAME scene at two window sizes.  Unavailable state = a fake with reachable
False.  Run:  python3 python/scripts/capture_storage_viewers.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "python"))

from composeio.render.engine.runtime import RenderApp
from composeio.render.widgets.store_browser import StoreBrowserWidget, StoreEntry, BrowseListing

OUT = REPO / "captures" / "storage_viewers"
OUT.mkdir(parents=True, exist_ok=True)


class _Fake:
    def __init__(self, root_label, tree, reachable=True):
        self.root_label, self._tree, self.reachable = root_label, tree, reachable

    def probe(self):
        return self.reachable

    def list(self, segments):
        if not self.reachable:
            return BrowseListing(available=False,
                                 note="docstore unreachable (mqtt-broker.local)")
        return BrowseListing(entries=list(self._tree.get(tuple(segments), [])))

    def resolve_for_view(self, entry):
        return None


def _doc_fake(reachable=True):
    return _Fake("doc://", {
        (): [StoreEntry("bsg_planets", "dir", ("bsg_planets",), hint="12 docs"),
             StoreEntry("configs", "dir", ("configs",), hint="collection"),
             StoreEntry("fleet_devices", "dir", ("fleet_devices",), hint="collection"),
             StoreEntry("decisions", "dir", ("decisions",), hint="collection")],
        ("bsg_planets",): [
            StoreEntry("kobol", "leaf", ("bsg_planets", "kobol"), hint="bsg_planets"),
            StoreEntry("caprica", "leaf", ("bsg_planets", "caprica"), hint="bsg_planets"),
            StoreEntry("gemenon", "leaf", ("bsg_planets", "gemenon"), hint="bsg_planets"),
            StoreEntry("tauron", "leaf", ("bsg_planets", "tauron"), hint="bsg_planets")],
    }, reachable=reachable)


def _bus_fake():
    return _Fake("bus://assets", {
        (): [StoreEntry("cyd-45302c.compose.yaml", "leaf",
                        ("displays/cyd-45302c.compose.yaml",), hint="displays"),
             StoreEntry("dradis.json", "leaf", ("animations/dradis.json",),
                        hint="animations"),
             StoreEntry("IBMPlexSans-Regular.ttf", "leaf",
                        ("fonts/IBMPlexSans-Regular.ttf",), hint="fonts"),
             StoreEntry("compose_console.svg", "leaf",
                        ("icons/compose_console.svg",), hint="icons")],
    })


def _find(w, wid):
    if getattr(w, "widget_id", None) == wid:
        return w
    for ch in getattr(w, "children", []) or []:
        r = _find(ch, wid)
        if r:
            return r
    return None


def _capture(scene, adapter, out, size=None, path=()):
    app = RenderApp.from_file(str(scene), windowed=False)
    if size:
        app.comp.width, app.comp.height = size
    w = _find(app.comp.root, "browser")
    w._adapter = adapter
    w._path = tuple(path)
    app.start()
    w.list_now()
    app.render_frame()
    app.canvas.save_png(str(out))
    print("wrote", out, "(", os.path.getsize(out), "bytes )")


def main():
    doc = REPO / "apps/compose_platform_launch_bar" / "docstore_viewer.compose.yaml"
    bus = REPO / "apps/compose_platform_launch_bar" / "busstore_viewer.compose.yaml"
    ribbon = REPO / "apps/compose_platform_launch_bar" / "launch_bar.compose.yaml"

    # Document Storage: collections list + a reflow at min size + drilled-in.
    _capture(doc, _doc_fake(), OUT / "docstore_browse_820x720.png")
    _capture(doc, _doc_fake(), OUT / "docstore_browse_640x480.png", size=(640, 480))
    _capture(doc, _doc_fake(), OUT / "docstore_drilled_1100x820.png",
             size=(1100, 820), path=("bsg_planets",))
    _capture(doc, _doc_fake(reachable=False), OUT / "docstore_unavailable_820x720.png")

    # Bus Storage: asset list + reflow at two sizes.
    _capture(bus, _bus_fake(), OUT / "busstore_browse_820x720.png")
    _capture(bus, _bus_fake(), OUT / "busstore_browse_640x480.png", size=(640, 480))

    # the ribbon — all 7 tiles now active.
    app = RenderApp.from_file(str(ribbon), windowed=False)
    app.render_headless(str(OUT / "launch_bar_all7_active.png"))
    print("wrote", OUT / "launch_bar_all7_active.png")


if __name__ == "__main__":
    main()
