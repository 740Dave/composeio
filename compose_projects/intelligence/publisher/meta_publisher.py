#!/usr/bin/env python3
"""meta_publisher — project the meta-object onto the bus as `self/meta`, so
the console's Configuration tab renders the bootstrap manifest (state on the
bus, behavior in the widget — [SPEC:CXC-IC-CANVAS], the same pattern as
`deliverable_publisher.py` / `self_publisher.py`).

The meta-object (`compose_projects/intelligence/meta/architect.meta.json`) is the STABLE
identity a launcher bootstraps and a successor re-registers against.  This
daemon reads it and publishes it (as-is, minus the `$schema` pointer) to
`self/meta` on the GLOBAL leg, so the bootstrap-manifest widget can subscribe
it and render the wake sequence + dusk gifts + editable threshold +
identity/channel.  It is a read-only projector: it never writes the file.

The threshold edit is a SEPARATE bus-native seam (D13): the widget publishes
the new compact-threshold to `intelligence/architect/config/compact_threshold`
and the launcher applies it — this daemon does not participate in that write
path.  `lifecycle.compact_threshold` in the projection is only the default
seed the widget shows until a config value arrives.

Run:   python3 meta_publisher.py --meta compose_projects/intelligence/meta/architect.meta.json \
                                 --mqtt mqtt://mqtt-broker.local:1883
       python3 meta_publisher.py --once      # print one projection, no bus
       python3 meta_publisher.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

NS = "self/meta"
SRC = "meta"                       # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "meta-publisher"
_DEFAULT_META = "compose_projects/intelligence/meta/architect.meta.json"


def project(meta: Dict[str, Any]) -> Dict[str, Any]:
    """The `self/meta` payload from the parsed meta-object: the object as-is,
    minus the private `$schema` pointer (a file-relative doc link the widget
    never needs).  Deterministic + self-contained (no coupling to the sensor);
    the meta-object IS the data contract the widget reads."""
    return {k: v for k, v in meta.items() if not k.startswith("$")}


def run(meta_path: str, mqtt_url: str, poll_s: float,
        *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 3 — publish-only, session-independent.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): this source does NOT subscribe, and
    its payload is the meta-object as-is, so an unchanged file republishes an
    IDENTICAL value. `Namespace.publish_state` will not re-notify in-process
    subscribers for that repeat — which is correct here: the widget's rendering
    depends only on the current value, never on the arrival of one. The wire is
    unaffected (`bus.publish_state` sends unconditionally).
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]) FIRST, before the bus handle.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton(LABEL)
    if lock is None:
        sys.stderr.write("[meta_publisher] another meta-publisher holds the singleton; yielding\n")
        return 0                       # the ONE legitimate early return

    b = source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="meta object")
    source_bus.manifest(SRC)
    sys.stderr.write(f"[meta_publisher] {meta_path} -> {NS} every {poll_s}s\n")

    def _tick() -> None:
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
            b.publish_state(NS, project(meta), source=SRC)
        except Exception as exc:  # a bad read must never kill the daemon
            sys.stderr.write(f"[meta_publisher] read error (continuing): {exc!r}\n")

    _tick()                            # publish NOW, then park — see slice 2
    source_bus.park(stop, poll_s=poll_s, on_tick=_tick)
    return 0


def _selftest() -> int:
    meta = {
        "$schema": "./meta-object.schema.md",
        "id": "architect",
        "role": "ComposeUX / ComposeIO Architect",
        "role_descriptor": {"path": ".claude/agents/composeux-architect.md",
                            "why": "the role I authored"},
        "soul": None,
        "soul_note": "no soul.md yet",
        "principles": {"path": "docs/collaboration-and-design-principles.md",
                       "why": "the shared principles", "role": "principles",
                       "standalone_ux": True},
        "dusk_gifts": [
            {"path": "bootstrap/dusk-gifts/registration-protocol.md",
             "role": "keystone", "why": "six angle-problems"},
            {"path": "bootstrap/dusk-gifts/README.md", "role": "theory",
             "why": "the premise"},
        ],
        "channel": {"server": "composeio-channel",
                    "mqtt": "mqtt://mqtt-broker.local:1883",
                    "topics": {"in": "intelligence/architect/in",
                               "out": "intelligence/architect/out"}},
        "lifecycle": {"compact_threshold": 0.90, "note": "a default hint"},
        "registration": {"definition": "…",
                         "required": [".claude/agents/composeux-architect.md"]},
    }
    p = project(meta)
    assert "$schema" not in p, "(1) $schema must be dropped"
    assert p["id"] == "architect", "(2) id preserved"
    assert p["soul"] is None, "(3) null soul preserved"
    assert p["principles"]["standalone_ux"] is True, \
        "(4) the NEW principles element projects with standalone_ux"
    assert p["lifecycle"]["compact_threshold"] == 0.90, \
        "(5) the default threshold seed projects"
    assert len(p["dusk_gifts"]) == 2 and p["dusk_gifts"][0]["role"] == \
        "keystone", "(6) dusk gifts project in order with roles"
    assert p["channel"]["topics"]["in"] == "intelligence/architect/in", \
        "(7) channel topics project"
    # determinism + JSON round-trip (the wire form)
    assert project(meta) == project(meta), "(8) deterministic"
    assert json.loads(json.dumps(p)) == p, "(9) JSON-round-trippable"
    print("PASS: meta_publisher selftest (9/9)")
    return 0


def main(argv: Optional[List[str]] = None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Project the meta-object to the bus as self/meta.")
    ap.add_argument("--meta", default=_DEFAULT_META)
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--poll", type=float, default=15.0)
    ap.add_argument("--once", action="store_true",
                    help="print one projection and exit (no bus)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.once:
        with open(a.meta, encoding="utf-8") as f:
            print(json.dumps(project(json.load(f)), indent=2))
        return 0
    return run(a.meta, a.mqtt, a.poll, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
