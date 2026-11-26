"""LOCAL-ONLY live-bus demo runner — NOT the deploy path (that stays
GLASS-PENDING in __main__.py, gated on Haven-Deployer verifying the live
broker at deploy time). This script is the dispatch-scoped alternative: run
fingerprint_service LOCALLY on this Mac, pointed at the SAME live broker
`__main__.py`/config.py already resolve to, to prove the R503 S3 mock-HAL
firmware round-trips through it end to end. Never deployed to a shared host.

Broker verification performed by hand before this script exists (see the
composeio-expert dispatch report): a real pub/sub round trip against
mqtt-broker.local:1883, and buildhost.local:1883 confirmed to have
nothing listening (cutover not started). This script reuses
`fingerprint_service.config.broker_url()` so it never re-hardcodes that.

⛔ KNOWN GAP THIS SCRIPT WORKS AROUND, DOCUMENTED, NOT HIDDEN: SupersetStore
templates are `bytes` (store.py TemplateRecord.template), and
`transport.BusMessage.to_json()` REFUSES to serialise raw bytes for a JSON
substrate (measured: `UnserialisableValue`, citing DP-CIO-18 — opaque binary
belongs behind a `bus://` reference on a JSON substrate, never silently
base64'd). `RosterPublisher.push_to_devices()` hands `dict(push)` with
`bytes` templates straight to `bus.publish_event()`, so calling
`service.start_game()` against a REAL MQTT-backed bus raises inside the
publish, not before it — the in-process integration test never surfaces
this because `LocalTransport` never serialises to JSON.

For THIS demo the seed templates are plain printable ASCII (the same
`tmpl-<person>-<ordinal>` fixture text every other fingerprint test in this
repo already uses), so decoding them to `str` before the wire hop is a
faithful, lossless representation of the SAME bytes — not a production fix.
A real R503 template is genuine binary and this gap is real: production needs
either a ratified base64 wire convention or a `bus://` reference per
DP-CIO-18, and that is a decision for the architect/human, not an ad hoc
choice buried in a demo script.

Run:
    PYTHONPATH=python:services python3 services/fingerprint_service/local_live_demo.py
"""

from __future__ import annotations

import copy
import time

from composeio.bus import ComposeIOBus
from composeio import transport_registry

from fingerprint_service import topics
from fingerprint_service.config import broker_url
from fingerprint_service.service import FingerprintService

DEVICE_ID = "dev/s3-fp1"          # matches fingerprint_bringup_main.cpp kDeviceId
MODULE = "s3-mock-hal-livebus-demo"
TEMPLATE_SIZE = 1536              # MockFingerprintHAL().capabilities().template_size_bytes,
                                   # both sides (mock.py:49, fingerprint_mock.h) — the
                                   # device-side receive_push() re-checks this exactly.


def _synth_ascii(seed: str, size: int = TEMPLATE_SIZE) -> bytes:
    out = (seed * (size // len(seed) + 1))[:size]
    return out.encode("ascii")


def _seed_cast(store, module: str) -> None:
    """Same shape as tests/test_integration.py::_seed_cast — a crew role
    gating 'open_hatch' (matches the firmware's gated_capability), one
    character, two templates."""
    store.define_role("crew", ["open_hatch"])
    store.define_role("guest", [])
    store.create_person("alice", "Alice")
    store.define_character("Captain", "crew")
    store.cast(module, "alice", "Captain")
    store.upsert_template("alice", 1, _synth_ascii("tmpl-alice-1"))
    store.upsert_template("alice", 2, _synth_ascii("tmpl-alice-2"))


def _wire_safe_push(push: dict) -> dict:
    """Decode each entry's `template` bytes -> ASCII str so the push can
    cross the MQTT/JSON substrate (see module docstring: DP-CIO-18 gap)."""
    safe = copy.deepcopy(push)
    for entry in safe["entries"]:
        entry["template"] = entry["template"].decode("ascii")
    return safe


def main() -> int:
    url = broker_url()
    print(f"[demo] resolved broker URL: {url}")
    scheme, sep, host_port = url.partition("://")
    assert sep and host_port, f"malformed broker URL {url!r}"
    host, _, port_s = host_port.partition(":")
    port = int(port_s) if port_s else 1883
    print(f"[demo] connecting to {host}:{port} via {scheme!r} (verified live 2026-08-18)")

    # Substrate comes from CONFIGURATION, not this module: `type` is the scheme of
    # the resolved broker_url() ([CXC-SOB-THESIS] — a module may not name or
    # hardcode its own substrate, not even as a string literal; change the config
    # to can://… and this demo rides CAN). The registry's mqtt factory defaults
    # topic_prefix to "composeio", which is what this demo needs.
    transport = transport_registry.build(
        {"type": scheme, "broker": host, "port": port,
         "client_id": "fingerprint-storage-local-demo"},
        app_id="fingerprint-storage-local-demo")
    if transport is None:
        print(f"[demo] broker {host}:{port} ({scheme}) is unreachable — aborting")
        return 1
    bus = ComposeIOBus(bus_id="fingerprint-storage-local-demo",
                       global_transport=transport)
    # LEAF bus: no _bus/time/_bus/heartbeat announce — this is not the bus master.
    bus.start(announce_time=False)

    # Wait for the ACTUAL MQTT CONNACK before subscribing. subscribe_events()
    # only issues the broker-level SUBSCRIBE immediately when the transport is
    # already connected (transport_mqtt.py:640); a subscribe registered in the
    # race window between start() returning and the async on_connect handshake
    # completing sits in `_subscribers` but is never sent to the broker until
    # the NEXT reconnect (measured: this is what silently starved
    # follow_device() the first time this script ran).
    t0 = time.time()
    while not transport.is_connected and time.time() - t0 < 10.0:
        time.sleep(0.1)
    print(f"[demo] transport connected: {transport.is_connected} "
         f"(after {time.time() - t0:.2f}s)")

    svc = FingerprintService()
    svc.attach(bus)
    _seed_cast(svc.store, MODULE)
    svc.follow_device(DEVICE_ID)
    print(f"[demo] following {DEVICE_ID}; waiting a moment for its retained "
         "heartbeat to arrive...")
    time.sleep(2.0)

    push = svc.publisher.build_push(MODULE)
    print(f"[demo] built push uuid={push['template_set_uuid']} "
         f"entries={push['entry_count'] if 'entry_count' in push else len(push['entries'])}")

    from composeio.peripherals.fingerprint.push_validation import validate_publishable
    ok, reason = validate_publishable(push)
    if not ok:
        print(f"[demo] REFUSED before publish: {reason}")
        return 1

    wire_push = _wire_safe_push(push)
    uuid = push["template_set_uuid"]
    svc.publisher._device_desired_uuid[DEVICE_ID] = uuid
    svc.publisher._device_module[DEVICE_ID] = MODULE
    bus.publish_event(topics.template_set(DEVICE_ID), "template_set",
                      wire_push, source=svc.source)
    print(f"[demo] PUBLISHED template_set to {DEVICE_ID} "
         f"(topic composeio/global/{topics.template_set(DEVICE_ID)}) uuid={uuid}")

    print("[demo] listening 15s for the device's ack + heartbeat + scripted "
         "auth_events...")
    t_end = time.time() + 15.0
    while time.time() < t_end:
        time.sleep(0.5)
        print(f"[demo] ack={svc.publisher.ack(DEVICE_ID)!r} "
             f"held_uuid={svc.publisher.held_uuid(DEVICE_ID)!r} "
             f"converged={svc.publisher.is_converged(DEVICE_ID)}")
        if svc.publisher.is_converged(DEVICE_ID):
            break

    bus.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
