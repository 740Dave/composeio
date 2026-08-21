"""fleet — poll the ComposeBus and print what is actually on it.

[SPEC:CXC-BOARD-HW] — workstream 0.5 of
``docs/PLAN-fleet-baseline-and-bus-native.md``.

    composeio fleet
    composeio fleet --broker mqtt-broker.local --wait 10
    composeio fleet --transport type=uart,port=/dev/ttyUSB0,baudrate=115200
    composeio fleet --transport type=can,channel=can0
    composeio fleet --json

IT READS THE BUS, NOT A DOCUMENT.  That is the whole point and it is worth
saying plainly, because the repo already has five ``device.yaml`` files, eight
board descriptors and a plan table, and on 2026-08-12 three of five records
carried ``mac: "TBD"`` while ``dual_throttle``'s declared id did not match the
id the device was actually publishing under.  A reflash campaign driven off
those records would target the wrong hardware or skip it.  This tool asks the
devices.

HOW THE POLL WORKS
------------------
Publish ``_device/broadcast/announce = 1`` once; every device republishes its
whole announcement set immediately.  One message, everyone answers, and it
works identically on MQTT, UART and CAN because it is an ordinary bus message.
Then listen for ``--wait`` seconds and report what arrived.

WHY IT ALSO WAITS RATHER THAN JUST POLLING
------------------------------------------
Because the LIVENESS signal is the PERIODIC heartbeat, not the answer to the
poll.  A device that answers the poll and then dies is indistinguishable, from
one poll, from a healthy one.  So the window is long enough to observe the 5 s
heartbeat at least once for every device that answered, and the report prints
the OBSERVED interval rather than the configured one — the configured cadence
is what the firmware intends, and the entire reason this mechanism exists is
that intent and reality had been diverging unobserved.

IT DOES NOT CHOOSE THE SUBSTRATE — AND UNTIL 2026-08-13 IT DID
--------------------------------------------------------------
``MQTTTransport`` is a transport; everything above it binds namespaces
(DP-CIO-6).  ``test_no_raw_paho_outside_transport.py`` is the ratchet that keeps
paho under the waist, and a fleet tool that reached beneath it would be the
first thing anyone copied.

That ratchet was always green here, and it was measuring the wrong thing.  This
module imported no paho and named no broker library — and then constructed
``MQTTTransport(broker=…, port=…)`` on one line, which pins the tool to one wire
exactly as hard as the import would have.  The docstring three paragraphs up has
said since the day it was written that the poll "works identically on MQTT, UART
and CAN because it is an ordinary bus message".  That claim is TRUE — every call
this module makes (``subscribe_pattern``, ``subscribe_all``, ``send``, ``start``,
``stop``, ``is_connected``) is declared on the `Transport` ABC — and the
construction was the single line contradicting it.  So the inventory tool you
reach for when a device is missing was structurally unable to see the CAN and
UART population, which is precisely the population ``_device/<id>/*`` was built
substrate-agnostic to reach.  ([CXC-SOB-THESIS]; door 2 of
``test_substrate_choice_at_the_waist.py``.)

The transport is now SELECTED, never named:

  * ``--transport type=<t>,k=v,…`` on the CLI — a CLI is allowed to name a
    substrate; this library module is not;
  * else ``COMPOSEIO_TRANSPORT`` from the environment;
  * else ``mqtt``, which is a DEFAULT an operator can override without editing
    source, not a choice this module makes.  It sits beside ``DEFAULT_BROKER``,
    which has been env-driven all along — somebody made the ADDRESS
    configurable and never asked the same question of the WIRE.

and the spec goes to ``transport_registry.build``, so an unknown ``type:``
RAISES rather than silently degrading to a poll nobody hears.  Callers may also
inject a ready ``Transport`` (``poll_fleet(transport=…)``) and this module
constructs nothing at all.

⚠ WHAT THIS TOOL CANNOT SEE, and it says so in its own output rather than
leaving you to infer it:

  * **CAN device names from a board that has not ANNOUNCED.**  ``_device/<id>/*``
    embeds a per-device id and the CAN wire id is a 16-bit hash of the FULL
    namespace, so a ``_device/*`` prefix rule cannot be expanded without already
    knowing every device id — the thing being discovered.  That is a property of
    the wire format and no bridge change fixes it.

    Workstream G closes it from the other end (2026-08-13): a node announces its
    namespace NAMES over the one well-known root ``_device/announce`` (0x31AE,
    hardcoded in every bridge and node), chunked with ``config_chunk``, and both
    ``CANTransport`` and ``services/can_mqtt_bridge`` learn ``name -> hash``
    from it.  So this tool CAN now name a CAN device — **on the condition that
    the board is running firmware with ``ns_announce`` in it.**  A board flashed
    before that still reports irreversible hashes, and it reports them loudly
    rather than being absent.  ⚠ As of this writing NO BOARD HAS BEEN FLASHED
    with it: the mechanism is host-proven and silicon-unproven, and this note
    stays until a real board answers.
  * **A device that has never announced.**  Absence in this report means "did
    not speak during the window", which is not the same as "does not exist".
    That ambiguity is exactly why announcement is periodic: after a fleet-wide
    reflash, absence becomes meaningful.
"""

from __future__ import annotations

import json
import os
import socket
import time

from composeio import device_announce as da
from composeio.device_announce import DeviceReport
from composeio.transport import BusMessage, MessageType, Transport

#: The bus master (see the project memory: mqtt-broker.local /
#: mqtt-broker.local).  Overridable, because a bench segment is a legitimate target
#: and because this host is planned to move to a dedicated Skipper.
DEFAULT_BROKER = os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local")
DEFAULT_PORT = int(os.environ.get("COMPOSEIO_MQTT_PORT", "1883"))

#: The DEFAULT substrate — read from the environment exactly like the broker
#: above, so an operator changes the wire the same way they change the address.
#: The fallback string is a default, not a decision: nothing here constructs a
#: transport class and nothing writes a `type:` into a spec literal.
DEFAULT_TRANSPORT_TYPE = os.environ.get("COMPOSEIO_TRANSPORT", "mqtt")

#: Long enough to see at least one 5 s heartbeat plus the poll answer, short
#: enough that nobody reaches for Ctrl-C.
DEFAULT_WAIT_S = 8.0


def _client_id_stem() -> str:
    """The pid-free stem.  `transport_registry._mqtt` appends `-<pid>` itself.

    Passing the already-suffixed `_client_id()` through the registry would
    produce `…-<pid>-<pid>`; passing the stem reproduces the pre-conversion id
    byte for byte.  Substrates that have no notion of a client id ignore the
    key, which is why it can be set unconditionally.
    """
    return f"composeio-fleet-{socket.gethostname().split('.')[0]}"


def _client_id() -> str:
    """A DISTINCT client id, always.

    Two MQTT peers sharing an id kick each other off the broker (rc=7), and the
    broker at mqtt-broker.local is LIVE PRODUCTION — a fleet poll must never be the
    thing that disconnects a running console.
    """
    return f"{_client_id_stem()}-{os.getpid()}"


def transport_spec(transport_type: str | None = None,
                   broker: str = DEFAULT_BROKER,
                   port: int = DEFAULT_PORT,
                   options: dict | None = None) -> dict:
    """The ``transports:``-shaped spec for a poll.  A VALUE, not a decision.

    `--broker` / `--port` are MQTT-shaped flags and are only meaningful to an
    MQTT spec, so they are folded in only when the caller asked for that type.
    Every other substrate takes its parameters from *options* (`channel=`,
    `bitrate=`, `baudrate=`, …), which is `--transport`'s `k=v` tail — the same
    keys `transport_registry`'s factories already read.  There is no per-family
    branch here beyond that one, and deliberately: the registry is the
    dispatcher, this is a spec builder.
    """
    ttype = (transport_type or DEFAULT_TRANSPORT_TYPE).strip().lower()
    spec: dict = {"type": ttype, "client_id": _client_id_stem()}
    if ttype == "mqtt":
        spec["broker"] = broker
        spec["port"] = int(port)
    spec.update(options or {})
    return spec


def describe_spec(spec: dict) -> str:
    """One line naming the WIRE and its address, for the report header.

    The header used to print the broker unconditionally.  On a UART or CAN poll
    that would name a host nobody contacted — a report that misdescribes what it
    listened to is the same class of lie as one that omits a failed connect.
    """
    ttype = str(spec.get("type", "?"))
    if ttype == "mqtt":
        return f"{ttype}://{spec.get('broker')}:{spec.get('port')}"
    detail = ", ".join(f"{k}={v}" for k, v in sorted(spec.items())
                       if k not in ("type", "client_id"))
    return f"{ttype}({detail})" if detail else ttype


def poll_fleet(
    broker: str = DEFAULT_BROKER,
    port: int = DEFAULT_PORT,
    wait: float = DEFAULT_WAIT_S,
    *,
    poll: bool = True,
    connect_timeout: float = 5.0,
    transport_type: str | None = None,
    spec: dict | None = None,
    transport: Transport | None = None,
) -> tuple[dict[str, DeviceReport], list[str]]:
    """Poll and listen.  Returns (reports by device id, warnings).

    THREE WAYS IN, in precedence order, and none of them names a substrate here:

      * *transport* — an already-built `Transport`.  This module then constructs
        nothing whatsoever, which is the in-process/injected shape and the one
        the tests drive.
      * *spec* — a ``transports:``-shaped dict handed to
        `transport_registry.build`.  An unknown ``type:`` RAISES; a known type
        whose backend is unreachable returns None, and that is reported as a
        WARNING rather than as an empty fleet.
      * *transport_type* / the environment — the ordinary CLI path, which builds
        the spec from `transport_spec()` above.

    Warnings are returned rather than printed so the JSON output can carry them
    too — a machine-readable report that silently omits "the broker never
    connected" would be the same class of lie this whole workstream is about.
    """
    warnings: list[str] = []
    reports: dict[str, DeviceReport] = {}

    if spec is None:
        spec = transport_spec(transport_type, broker, port)
    if transport is None:
        from composeio.transport_registry import build
        transport = build(spec)
    if transport is None:
        # A KNOWN substrate whose backend is unreachable.  Same distinction the
        # registry draws and for the same reason: "we know what you asked for
        # and it is not reachable right now" is an environment fact, and an
        # EMPTY report caused by it must never read as a silent fleet.
        warnings.append(
            f"could not build the {describe_spec(spec)} transport (the substrate "
            f"is known but its backend is unavailable) — this report is EMPTY "
            f"BECAUSE OF THE TOOL, not because the fleet is silent.  Those are "
            f"different facts."
        )
        return reports, warnings

    def on_message(msg: BusMessage) -> None:
        parsed = da.parse_namespace(msg.namespace)
        if parsed is None:
            return
        device_id, leaf = parsed
        rep = reports.get(device_id)
        if rep is None:
            rep = reports[device_id] = DeviceReport(device_id=device_id)
        rep.apply(leaf, msg.value, time.monotonic())

    # Registered BEFORE start() so the on-connect handler establishes the
    # wildcard subscription in the same callback that flushes our poll — no
    # window in which a device could answer into a socket nobody is reading.
    transport.subscribe_pattern("_device/*")
    transport.subscribe_all(on_message)
    transport.start()

    deadline = time.monotonic() + connect_timeout
    while not transport.is_connected and time.monotonic() < deadline:
        time.sleep(0.05)
    if not transport.is_connected:
        warnings.append(
            f"never connected to {describe_spec(spec)} within "
            f"{connect_timeout:.0f}s — this report is EMPTY BECAUSE OF THE "
            f"TOOL, not because the fleet is silent.  Those are different facts."
        )
        transport.stop()
        return reports, warnings

    if poll:
        transport.send(BusMessage(
            namespace=da.POLL_BROADCAST,
            value=1,
            type=MessageType.STATE,
            source=_client_id(),
            scope="global",
        ))

    time.sleep(wait)
    transport.stop()
    return reports, warnings


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #

_HEADER = ("DEVICE", "UPTIME", "BUILD", "TRANSPORT", "SCENE", "HEAP", "RESET",
           "LINK", "FLAGS", "HB")


def _row(rep: DeviceReport, fp_map: dict) -> tuple[str, ...]:
    h = rep.health_decoded
    if rep.transport is None:
        transport = "?"
    else:
        name, from_nvs = da.unpack_transport(rep.transport)
        transport = f"{name}({'nvs' if from_nvs else 'dflt'})"

    hb = rep.heartbeat_interval_s
    return (
        rep.device_id,
        da.format_uptime(rep.uptime_s),
        da.describe_build(rep.build, fp_map),
        transport,
        "?" if rep.scene is None else f"{rep.scene:04x}",
        "?" if h is None else f"{h.heap_kb}K",
        "?" if h is None else h.reset_reason_name,
        # A DASH, not "ok".  kAnnLinkUnknown is what a substrate with no error
        # model reports, and printing it as anything reassuring would rebuild
        # the H2 defect in the reporting layer.
        "?" if h is None else ("—" if h.link == 0 else h.link_name),
        "?" if h is None else (",".join(h.flag_names) or "-"),
        f"{rep.heartbeats}" + (f"/{hb:.1f}s" if hb else ""),
    )


def format_report(reports: dict[str, DeviceReport], warnings: list[str],
                  fp_map: dict | None = None, *, wait: float = DEFAULT_WAIT_S,
                  broker: str = DEFAULT_BROKER, target: str | None = None) -> str:
    """*target* names the WIRE that was actually listened to.

    `broker=` is kept, and kept as the fallback, so every existing caller keeps
    working unchanged; but a UART or CAN poll passes *target* instead, because a
    header reading `mqtt-broker.local` above a `can0` poll would describe
    the wrong bus with total confidence.
    """
    target = target if target is not None else broker
    fp_map = fp_map if fp_map is not None else da.load_fingerprint_map()
    lines: list[str] = []

    rows = [_row(reports[k], fp_map) for k in sorted(reports)]
    widths = [max(len(_HEADER[i]), *(len(r[i]) for r in rows)) if rows
              else len(_HEADER[i]) for i in range(len(_HEADER))]
    fmt = "  ".join("{:<%d}" % w for w in widths)

    lines.append(f"ComposeBus fleet — {target}, {wait:.0f}s window, "
                 f"polled {da.POLL_BROADCAST}")
    lines.append("")
    lines.append(fmt.format(*_HEADER))
    lines.append(fmt.format(*("-" * w for w in widths)))
    for r in rows:
        lines.append(fmt.format(*r))
    lines.append("")
    lines.append(f"{len(rows)} device(s) announced.")

    # ---- what the table cannot say in a column --------------------------- #
    notes: list[str] = list(warnings)
    for k in sorted(reports):
        rep = reports[k]
        h = rep.health_decoded
        if h is not None and h.contradiction:
            notes.append(f"{k}: {h.contradiction} — this is defect H2b "
                         f"(BusTransport::connected() returns s_started), "
                         f"reported rather than fixed")
        missing = [f for f in da.ANNOUNCE_FIELDS if f not in rep.arrivals]
        if missing:
            notes.append(f"{k}: never published {', '.join(missing)} within "
                         f"the window")
        if rep.build is not None and rep.build not in fp_map:
            notes.append(f"{k}: build {rep.build:08x} is not in "
                         f"cyd/build_fingerprints.json — it was built on "
                         f"another machine, or the map was not committed")
        hb = rep.heartbeat_interval_s
        if hb is not None and hb > 8.0:
            notes.append(f"{k}: heartbeat interval {hb:.1f}s is well over the "
                         f"5s cadence — a stalled render loop or a lossy link")

    if not rows:
        notes.append(
            "NOTHING ANSWERED.  Absence here means 'did not speak during the "
            "window', which is NOT the same as 'does not exist'.  Check that a "
            "board is powered, that it runs firmware built after the "
            "announcement landed, and that you polled the SUBSTRATE the board is "
            "on (`--transport type=uart,…`; the default is $COMPOSEIO_TRANSPORT). "
            "On CAN specifically: a device is named here ONLY if it announced "
            "its namespace NAMES on `_device/announce` (workstream G) — the "
            "wire carries a 16-bit hash of the full namespace and a hash is not "
            "reversible, so a board running firmware from before ns_announce "
            "reports unresolvable numbers instead of appearing. No board has "
            "been flashed with it yet, so on CAN this is currently the EXPECTED "
            "outcome, not a fault to chase.")

    if notes:
        lines.append("")
        lines.append("NOTES")
        for n in notes:
            lines.append(f"  - {n}")
    return "\n".join(lines)


def report_json(reports: dict[str, DeviceReport], warnings: list[str],
                fp_map: dict | None = None, *, target: str | None = None) -> str:
    """*target* is carried into the JSON for the same reason it is in the header:
    a machine-readable inventory that does not say WHICH WIRE it polled cannot be
    compared with one taken on another substrate, which is the entire point of
    being able to poll more than one.  Absent when the caller did not say."""
    fp_map = fp_map if fp_map is not None else da.load_fingerprint_map()
    out = {"warnings": warnings, "devices": {}}
    if target is not None:
        out["target"] = target
    for k in sorted(reports):
        rep = reports[k]
        h = rep.health_decoded
        transport = (da.unpack_transport(rep.transport)
                     if rep.transport is not None else (None, None))
        out["devices"][k] = {
            "uptime_s": rep.uptime_s,
            "build": None if rep.build is None else f"{rep.build:08x}",
            "build_stamp": (fp_map.get(rep.build, {}) or {}).get("stamp"),
            "transport": transport[0],
            "transport_from_nvs": transport[1],
            "scene": None if rep.scene is None else f"{rep.scene:04x}",
            "health_raw": None if rep.health is None else f"{rep.health:08x}",
            "heap_kb": None if h is None else h.heap_kb,
            "reset_reason": None if h is None else h.reset_reason_name,
            "link": None if h is None else h.link_name,
            "flags": None if h is None else h.flag_names,
            "contradiction": None if h is None else h.contradiction,
            "heartbeats": rep.heartbeats,
            "heartbeat_interval_s": rep.heartbeat_interval_s,
            "fields_seen": sorted(rep.arrivals),
        }
    return json.dumps(out, indent=2)
