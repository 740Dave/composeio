#!/usr/bin/env python3
"""BSG threat-scenario driver for the DRADIS scope.

Publishes `dradis/contacts` (and toggles `demo/alert`) over the ComposeIO
bus, animating a Cylon attack for the firmware `dradis_contacts` widget.
This script owns *what is out there*; the widget owns *how it looks and
moves* (docs/dradis/contacts-contract.md — the narrow-waist contract both
sides lower to).

    python3 python/scripts/dradis_scenario.py --dry-run       # print the timeline, no broker
    python3 python/scripts/dradis_scenario.py                 # publish to mqtt-broker.local:1883
    python3 python/scripts/dradis_scenario.py --broker HOST   # publish elsewhere
    python3 python/scripts/dradis_scenario.py --loop          # repeat idle -> attack forever
    python3 python/scripts/dradis_scenario.py --reset         # publish the idle picture and exit

Scenario (~30 s, ticked at 4 Hz):
  1. IDLE (0-5 s):    two distant blips — one neutral contact, one hostile
                      recon scout — both holding range ~40-60 light-seconds
                      (near the scope edge; edge = 60 ls = 1 light-minute).
                      demo/alert stays off.
  2. ATTACK (5-30 s): a raider_squadron ("raider-lead") and a basestar
                      ("cyl-base-1") spawn at range ~60 ls and close inbound
                      to ~1 ls, on distinct/weaving bearings so they read as
                      two separate contacts under way.  The SAME ids are
                      republished every tick — stable ids are how the
                      widget tracks/animates motion instead of respawning.
                      When the nearest hostile's range crosses ~2 light-
                      seconds, demo/alert trips true and holds red alert
                      for the rest of the run.
  3. RESET:           on normal exit, Ctrl-C, or `--reset`, the scope is
                      restored to the idle picture and demo/alert is
                      cleared — the console is never left mid-attack.

Own-ship (the Battlestar) is never published here — per the contract it's
a static center `image` widget, not a contact.

Wire form (matches composeio.transport.BusMessage.to_json — the SAME
envelope the MQTT transport binding uses; topic `composeio/global/<ns>`):

    {"ns": "<namespace>", "type": "state", "val": <value>, "src": "<id>",
     "ts": <unix time>, "scope": "global", "seq": <n>}

`--dry-run` prints exactly these payloads and opens no socket — it must
run with no broker present and no `paho-mqtt` installed.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
sys.path.insert(0, PY_ROOT)

from composeio.transport import BusMessage, Transport    # noqa: E402

# --- bus wiring -------------------------------------------------------------

BROKER_DEFAULT = "mqtt-broker.local"
PORT_DEFAULT = 1883
SOURCE_ID = "dradis-scenario"
NS_CONTACTS = "dradis/contacts"
NS_ALERT = "demo/alert"

# --- contract vocabulary (docs/dradis/contacts-contract.md) -----------------
# `battlestar` exists as an icon asset but is own-ship only — never publish
# it as a contact.
ALLOWED_TYPES = {"basestar", "raider_squadron"}
ALLOWED_ALLEGIANCE = {"hostile", "friendly", "neutral"}

# --- scenario timing ----------------------------------------------------

TICK_HZ = 4.0
DT = 1.0 / TICK_HZ
IDLE_S = 5.0
ATTACK_S = 25.0
TOTAL_TICKS = int(round((IDLE_S + ATTACK_S) / DT))  # 120 -> t in [0, 30]

# range is physical distance in LIGHT-SECONDS (contract, changed 2026-07-16
# from a 0..1 screen fraction). The scope edge is 60 ls (1 light-minute);
# the widget projects it on a log scale — the driver just publishes the
# physical ranges below.
SCOPE_EDGE_LS = 60.0
RANGE_MAX_SANITY = 120.0  # generous upper bound for validate_contacts()

RANGE_FAR = 60.0    # attack spawn range, at the scope edge
RANGE_NEAR = 1.0    # attack closing range, the "1 LS" ring
ALERT_TRIP_RANGE = 2.0  # light-seconds; demo/alert trips inside this


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def contact(id_: str, type_: str, bearing: float, rng: float, allegiance: str) -> dict:
    """Build one contact object, normalized to the contract's field ranges.

    `range` is light-seconds, > 0 (the widget clamps/projects it on a log
    scale; the driver just keeps it inside a sane sanity bound)."""
    return {
        "id": id_,
        "type": type_,
        "bearing": round(bearing % 360.0, 1),
        "range": round(clamp(rng, 0.001, RANGE_MAX_SANITY), 3),
        "allegiance": allegiance,
    }


def validate_contacts(contacts: list[dict]) -> list[dict]:
    """Self-check against the contract schema before it ever hits the bus."""
    seen_ids: set[str] = set()
    for c in contacts:
        assert isinstance(c.get("id"), str) and c["id"], f"bad id: {c}"
        assert c["id"] not in seen_ids, f"duplicate id: {c['id']}"
        seen_ids.add(c["id"])
        assert c.get("type") in ALLOWED_TYPES, f"bad type: {c}"
        assert 0.0 <= c.get("bearing", -1) < 360.0, f"bad bearing: {c}"
        assert 0.0 < c.get("range", -1) <= RANGE_MAX_SANITY, f"bad range: {c}"
        assert c.get("allegiance") in ALLOWED_ALLEGIANCE, f"bad allegiance: {c}"
    return contacts


def idle_contacts(t: float = 0.0) -> list[dict]:
    """The two background blips — present for the whole run, gently
    wandering so the idle picture doesn't look frozen. Both sit out near
    the scope edge, ~40-60 light-seconds."""
    return [
        contact("neutral-1", "raider_squadron",
                bearing=200 + 5 * math.sin(t * 0.10), rng=55.0, allegiance="neutral"),
        contact("scout-1", "raider_squadron",
                bearing=40 - 3 * math.sin(t * 0.15), rng=45.0, allegiance="hostile"),
    ]


def frame_at(t: float) -> tuple[list[dict], bool]:
    """Contact array + alert bit for scenario time `t` seconds (0 = start)."""
    contacts = idle_contacts(t)
    nearest_hostile = min((c["range"] for c in contacts if c["allegiance"] == "hostile"),
                          default=SCOPE_EDGE_LS)

    if t >= IDLE_S:
        at = t - IDLE_S                       # seconds into the attack
        frac = clamp(at / ATTACK_S, 0.0, 1.0)  # 0 -> 1 over the closing run
        # geometric (log-linear) interpolation: RANGE_FAR -> RANGE_NEAR.
        # The scope is logarithmic, so this sweeps evenly across the log
        # rings rather than bunching up near the edge like a linear blend
        # would.
        base_range = RANGE_FAR * (RANGE_NEAR / RANGE_FAR) ** frac

        # raiders: fast and evasive, weave across a bearing band
        raider_bearing = 55 + 20 * math.sin(at * 0.9)
        raider_range = base_range

        # basestar: slow and ponderous, a long straight arc, trailing
        # slightly behind the raiders' closing rate (proportionally, since
        # the scope reads distance on a log scale)
        basestar_bearing = 25 + 70 * frac
        basestar_range = clamp(base_range * 1.15, RANGE_NEAR, RANGE_FAR)

        contacts.append(contact("raider-lead", "raider_squadron",
                                raider_bearing, raider_range, "hostile"))
        contacts.append(contact("cyl-base-1", "basestar",
                                basestar_bearing, basestar_range, "hostile"))
        nearest_hostile = min(nearest_hostile, raider_range, basestar_range)

    alert = nearest_hostile <= ALERT_TRIP_RANGE
    return validate_contacts(contacts), alert


def idle_picture() -> tuple[list[dict], bool]:
    """The at-rest scope: what --reset (and normal exit) restores."""
    return validate_contacts(idle_contacts(0.0)), False


# --- publishing ---------------------------------------------------------

class _PrintTransport(Transport):
    """The ``--dry-run`` substrate: a Transport that prints instead of sending.

    A real ``Transport`` subclass on purpose. The alternative — branching inside
    `Publisher.publish` — means the dry run and the live run take DIFFERENT code
    paths through the framer, so the thing you inspect is not the thing that
    ships. Here they diverge only at the last inch.
    """

    def __init__(self) -> None:
        self.sent: list[BusMessage] = []

    def send(self, message: BusMessage) -> None:
        self.sent.append(message)
        # The topic is DERIVED for readability and never used to address
        # anything — this transport has no wire.
        print(f"composeio/{message.scope}/{message.namespace}  "
              f"{json.dumps(message.to_json())}")

    def subscribe(self, namespace, callback): pass
    def subscribe_all(self, callback): pass
    def unsubscribe(self, namespace, callback): pass
    def start(self): pass
    def stop(self): pass

    @property
    def is_connected(self) -> bool:
        return True


class Publisher:
    """The scenario's bus leg — a NAMESPACE and a SCOPE, never a topic
    ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], converted 2026-08-11).

    It used to build its own paho client and hand-compose
    ``composeio/global/<ns>``, which made this scenario MQTT-ONLY. That is the
    wrong constraint for THIS file in particular: a scenario DRIVER is exactly
    the thing you want to point at a CAN rig or a synthetic transport to
    exercise a device-tier DRADIS without a broker. Now the substrate is a
    ``transport_registry`` spec, so ``--transport can --channel can0`` is a flag
    away rather than a rewrite.

    ``--dry-run`` keeps its contract — no socket, and it must run with
    ``paho-mqtt`` absent. It is now a real ``Transport`` (``_PrintTransport``)
    handed to the SAME ``acquire_bus`` seam, which is strictly stronger than the
    old "skip the import" branch: the dry-run path exercises the identical
    ``publish_state`` -> ``_send_message`` -> ``transport.send`` chain the live
    path does, so a framing bug can no longer hide in the branch that never
    runs in CI. What it prints is the canonical ``BusMessage.to_json()``, not a
    second hand-built copy.

    NOT ``bus.on_message`` — that was the first cut, and it printed EVERY frame
    TWICE. Root cause, verified 2026-08-11: ``ComposeIOBus.on_message``
    registers the callback in ``_on_message_callbacks`` AND on
    ``self._local.subscribe_all``, and a local publish dispatches through both
    (``bus.py:587`` + ``bus.py:865``). That is a PRE-EXISTING defect affecting
    every bus MONITOR on the platform, not a dradis problem, and it is recorded
    rather than patched here — fixing it changes monitor semantics repo-wide and
    wants its own slice. This driver simply does not depend on it.
    """

    def __init__(self, broker: str, port: int, dry_run: bool,
                 transport_spec: dict | None = None):
        from composeio import source_bus
        from composeio.namespace import Scope
        self.dry_run = dry_run
        spec = (transport_spec if transport_spec is not None
                else {"type": "mqtt", "broker": broker, "port": port})
        builder = (lambda _spec, app_id="": _PrintTransport()) if dry_run else None
        self.bus = source_bus.acquire_bus(
            label=f"{SOURCE_ID}-{os.getpid()}", transport_spec=spec,
            transport_builder=builder,
            log=(open(os.devnull, "w") if dry_run else None))
        for ns in (NS_CONTACTS, NS_ALERT):
            source_bus.declare(self.bus, ns, scope=Scope.GLOBAL,
                               source=SOURCE_ID, label="dradis scenario")
        source_bus.manifest(SOURCE_ID, log=sys.stderr)

    def publish(self, namespace: str, value) -> None:
        self.bus.publish_state(namespace, value, source=SOURCE_ID)

    def close(self) -> None:
        self.bus.stop()


def publish_frame(pub: Publisher, t: float, contacts: list[dict], alert: bool,
                  prev_alert: bool | None) -> None:
    if pub.dry_run:
        print(f"# t={t:5.2f}s")
    pub.publish(NS_CONTACTS, contacts)
    # publish demo/alert as a TOGGLE — only on change (plus the very first
    # frame) rather than spamming a constant value every tick.
    if prev_alert is None or alert != prev_alert:
        pub.publish(NS_ALERT, alert)


def publish_reset(pub: Publisher) -> None:
    contacts, alert = idle_picture()
    if pub.dry_run:
        print("# reset -> idle picture")
    pub.publish(NS_CONTACTS, contacts)
    pub.publish(NS_ALERT, alert)


def run_scenario(pub: Publisher, loop: bool) -> None:
    prev_alert: bool | None = None
    try:
        while True:
            for i in range(TOTAL_TICKS + 1):
                t = i * DT
                contacts, alert = frame_at(t)
                publish_frame(pub, t, contacts, alert, prev_alert)
                prev_alert = alert
                if not pub.dry_run:
                    time.sleep(DT)
            if not loop:
                break
            prev_alert = None  # force a re-announce of the idle-alert state next lap
    except KeyboardInterrupt:
        print("\ninterrupted -- resetting scope", file=sys.stderr)
    finally:
        publish_reset(pub)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--broker", default=BROKER_DEFAULT,
                    help=f"MQTT broker host (default: {BROKER_DEFAULT})")
    ap.add_argument("--port", type=int, default=PORT_DEFAULT,
                    help=f"MQTT broker port (default: {PORT_DEFAULT})")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the payload timeline to stdout; publish nothing")
    ap.add_argument("--loop", action="store_true",
                    help="repeat the idle->attack scenario until interrupted")
    ap.add_argument("--reset", action="store_true",
                    help="publish the idle picture and demo/alert=false, then exit")
    # [CXC-SOB-THESIS]: the whole point of converting this driver — the same
    # scenario can now drive a CAN rig or a synthetic transport, because the
    # substrate is a `transport_registry` spec rather than a hardcoded client.
    ap.add_argument("--transport", default="mqtt",
                    help="substrate for the scenario: mqtt | can | uart | "
                         "synthetic | replay (default: mqtt)")
    ap.add_argument("--channel", default=None,
                    help="CAN channel / UART port, for --transport can|uart")
    args = ap.parse_args()

    spec: dict = {"type": args.transport}
    if args.transport == "mqtt":
        spec.update(broker=args.broker, port=args.port)
    elif args.channel:
        spec["channel" if args.transport == "can" else "port"] = args.channel
    pub = Publisher(args.broker, args.port, args.dry_run, transport_spec=spec)
    try:
        if args.reset:
            publish_reset(pub)
        else:
            run_scenario(pub, args.loop)
    finally:
        pub.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
