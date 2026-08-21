#!/usr/bin/env python3
"""Smoke — the two storage viewers end to end ([CXC-DOCNAV-*]/[CXC-BUSNAV-*]).

Two legs, no broker required (logic-level; an optional live-MQTT leg runs only
if a broker is reachable):

  1. LAUNCH ROUTING — app_launcher routes `_app/docstore/open` and
     `_app/busstore/open` to their new scenes, each spawned `runtime <scene>
     --transports` (so the scene's `_app/doc/open` reaches the global bus).
  2. SELECT -> VIEW — the store_browser widget, on selecting a leaf, publishes
     `_app/doc/open` with a REPO-SCOPED path the doc_viewer_launcher accepts.

Run:  python3 python/tests/render/smoke_storage_viewers.py
      python3 python/tests/render/smoke_storage_viewers.py --mqtt mqtt://mqtt-broker.local:1883
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "python"))
sys.path.insert(0, str(REPO / "compose_projects" / "intelligence" / "tools"))

import app_launcher as al
import doc_viewer_launcher as dvl
from composeio.render.widgets.store_browser import (
    StoreBrowserWidget, StoreEntry, BrowseListing, _CACHE_ROOT)

_results = []


def check(cond, msg):
    _results.append((bool(cond), msg))
    print(("PASS" if cond else "FAIL"), "-", msg)


class _FakeProc:
    pid = 4242

    def poll(self):
        return None


class _FakeAdapter:
    root_label = "doc://"

    def __init__(self):
        self.tree = {("bsg_planets",): [
            StoreEntry("kobol", "leaf", ("bsg_planets", "kobol"),
                       "doc://bsg_planets/kobol", "bsg_planets")]}

    def probe(self):
        return True

    def list(self, seg):
        return BrowseListing(entries=list(self.tree.get(tuple(seg), [])))

    def resolve_for_view(self, entry):
        out = _CACHE_ROOT / "smoke" / ("_".join(entry.segments) + ".yaml")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("name: " + entry.label + "\n", encoding="utf-8")
        return str(out)


class _Ctx:
    def __init__(self):
        self.published = []

    def publish(self, ns, val, source="widget"):
        self.published.append((ns, val))

    def request_redraw(self, *_a, **_k):
        pass


def leg_launch_routing():
    spawned = []
    lau = al.AppLauncher(spawn=lambda argv: (spawned.append(argv) or _FakeProc()))
    for target, scene_const in (("docstore", al.DOCSTORE_SCENE),
                                ("busstore", al.BUSSTORE_SCENE)):
        # the credo: an Intelligence publish == a click; both arrive as a topic.
        topic = f"composeio/global/_app/{target}/open"
        tgt = al.target_from_topic(topic)
        check(tgt == target, f"routing: topic -> target {target}")
        check(al.parse_intent("open"), f"routing: {target} open is truthy")
        status = lau.open(tgt)
        scene_abs = al.resolve_scene(scene_const)
        check(status.startswith(f"opened {target}"),
              f"routing: {target} spawned ({status})")
        check(spawned[-1][-2:] == [scene_abs, "--transports"],
              f"routing: {target} argv = <scene> --transports")


def leg_select_publishes_open():
    w = StoreBrowserWidget("browser", store="docstore", adapter=_FakeAdapter())
    w._ctx = _Ctx()
    w._path = ("bsg_planets",)
    w.list_now()
    check(len(w._entries) == 1 and w._entries[0].label == "kobol",
          "select: leaf listed")
    w._view_worker(w._entries[0])            # synchronous select
    pubs = w._ctx.published
    check(len(pubs) == 1 and pubs[0][0] == "_app/doc/open",
          "select: publishes _app/doc/open")
    path = pubs[0][1]
    check(dvl.resolve_doc(path) is not None,
          "select: published path is repo-scoped (doc-viewer accepts it)")


def leg_live_mqtt(mqtt_url):
    """Optional: assert the launcher's `_app/docstore/open` topic + the viewer's
    `_app/doc/open` topic round-trip a real broker (control-is-a-peer)."""
    try:
        import paho.mqtt.client as mqtt
    except Exception as e:
        print("SKIP live-mqtt (no paho):", e)
        return
    host, port = al._parse_mqtt_url(mqtt_url)
    got = []
    try:
        sub = mqtt.Client(client_id="smoke-storage-sub")
        sub.on_message = lambda c, u, m: got.append(m.topic)
        sub.connect(host, port, keepalive=10)
        sub.subscribe("composeio/global/_app/#", 0)
        sub.loop_start()
        pub = mqtt.Client(client_id="smoke-storage-pub")
        pub.connect(host, port, keepalive=10)
        pub.loop_start()
        import time
        time.sleep(0.5)
        pub.publish("composeio/global/_app/docstore/open", '{"val":"open"}')
        pub.publish("composeio/global/_app/doc/open", '{"val":"x"}')
        time.sleep(0.7)
        sub.loop_stop(); pub.loop_stop()
        sub.disconnect(); pub.disconnect()
        check("composeio/global/_app/docstore/open" in got,
              "live-mqtt: docstore/open reached the broker")
        check("composeio/global/_app/doc/open" in got,
              "live-mqtt: doc/open reached the broker")
    except Exception as e:
        print("SKIP live-mqtt (broker unreachable):", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mqtt", default=None)
    a = ap.parse_args()
    leg_launch_routing()
    leg_select_publishes_open()
    if a.mqtt:
        leg_live_mqtt(a.mqtt)
    n_pass = sum(1 for ok, _ in _results if ok)
    n = len(_results)
    print(f"\n{n_pass}/{n} checks passed")
    return 0 if n_pass == n else 1


if __name__ == "__main__":
    raise SystemExit(main())
