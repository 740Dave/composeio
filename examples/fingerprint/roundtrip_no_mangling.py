#!/usr/bin/env python3
"""roundtrip_no_mangling.py — prove a REAL R503 template survives the round trip
OUT to the template store and BACK into the physical sensor, BYTE-EXACT, and
when the RELOAD leg fails, CAPTURE THE FAILURE AXIS verbatim off the bus.

Two shapes of the same run:

  FULL (default)      dump src slot -> store(PUT+GET) -> template_set push
                      -> capture terminal event -> if loaded, dump dst slot,
                      assert A == B byte-for-byte.

  --reload-only       the sensor is already EMPTY (the "delete all" half already
                      happened), so there is no source slot to dump.  Obtain
                      bytes A from the store (--ref) or a local file (--a-file),
                      re-PUT to a FRESH ref, publish the push, and CAPTURE THE
                      template_set_failed EVENT so we can read {axis, sensor_code,
                      detail} — the one field that says TRANSPORT (the store GET
                      failed) vs SENSOR (DownChar+Store of the real template
                      failed), and, if SENSOR, the exact R503 confirmation code.

⛔ THIS ORCHESTRATES AN ON-HARDWARE RUN.  It opens a serial port and talks to a
live broker + template store.  The architect runs it against silicon; a subagent
with no hardware runs ONLY the pure-logic pytest beside it (test_roundtrip_no_mangling.py).

WHY A BUS SUBSCRIPTION.  receive_push returns "load_failed" on serial, but the
DIAGNOSTIC {axis, sensor_code, detail} is published as a `template_set_failed`
BUS EVENT (handle_load_failure), never printed to serial.  So this harness
subscribes to composeio/global/<device>/# BEFORE publishing and prints that
event verbatim ([CXC-VISION-EPISTEMICS]: an instrument that reports only
"it failed" without the axis is not one).

SERIAL DISCIPLINE (S3 USB-Serial-JTAG).  Opening the port RESETS the S3 every
time, and after ~15 reopen cycles RX wedges dead until a cold power-cycle.  So
this harness opens the port ONCE for the whole run, tolerates the reset, WAITS
for a boot-complete sentinel before commanding, retries a dump on timeout, and
never reopens mid-run.  `--no-serial` skips the port entirely (use when the board
is already booted+subscribed and you want to avoid even one reset) — the failure
event still arrives over MQTT.

The device-side dump frame this scrapes is emitted by `r503 dump <slot>`:
    -----R503-DUMP-BEGIN slot=<n> bytes=<len> sha256=<hex>-----
    <base64, wrapped at 76 cols>
    -----R503-DUMP-END-----
sha256 is over the RAW bytes; this scraper re-hashes and REFUSES a mismatch.

CONFIG, never hardcoded (env with these defaults):
    COMPOSEIO_TEMPLATE_STORE_URL   http://buildhost.local:8423   (the store leg)
    COMPOSEIO_MQTT_BROKER          mqtt-broker.local:1883 (the bus leg)

⛔ PAYLOAD SHAPE.  The `template_set` entry shape is read straight from the
firmware's own decoder (onMqttMessage) and receive_push: an entry is
{person, ordinal, character, rbac, template}, and the DESTINATION SLOT IS NOT IN
THE PUSH — receive_push empties the sensor then store_template() places each
template in first_free_slot() (slot 0 on a freshly-emptied module).  So
--dst-slot is what you DUMP, not what you command.
"""
from __future__ import annotations

import argparse
import base64 as _b64
import hashlib
import os
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass


# ======================================================================
# PURE LOGIC — no serial, no mqtt, no network.  Unit-tested in isolation.
# ======================================================================

_BEGIN_RE = re.compile(
    r"-----R503-DUMP-BEGIN\s+slot=(-?\d+)\s+bytes=(\d+)\s+sha256=([0-9a-fA-F]{64})-----"
)
_END_MARK = "-----R503-DUMP-END-----"
# The base64 alphabet, so stray console noise between the markers cannot be
# mistaken for payload — anything outside it is dropped before decoding, and the
# sha check then catches any real corruption.
_B64_CHARS = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
)


@dataclass
class DumpFrame:
    """A scraped, not-yet-decoded dump frame."""
    slot: int
    nbytes: int          # declared byte length (from the BEGIN line)
    sha256: str          # declared hex digest (lowercased)
    b64: str             # the base64 body, whitespace/noise stripped


def scrape_frame(text: str) -> DumpFrame:
    """Find the FIRST complete BEGIN..END frame in `text` and return it.

    Raises ValueError if no complete frame is present (never returns a partial).
    """
    m = _BEGIN_RE.search(text)
    if not m:
        raise ValueError("no R503-DUMP-BEGIN header found in captured text")
    end_idx = text.find(_END_MARK, m.end())
    if end_idx < 0:
        raise ValueError("R503-DUMP-BEGIN found but no matching -----R503-DUMP-END-----")
    body = text[m.end():end_idx]
    # Keep only lines that are ENTIRELY base64 — a stray console line
    # interleaved in the frame (spaces, brackets, '=' in prose) is dropped
    # whole rather than having its base64-ish characters spliced into the
    # payload.  Any real corruption still trips the sha check in decode_frame.
    b64 = "".join(
        line for raw_line in body.splitlines()
        if (line := raw_line.strip()) and all(ch in _B64_CHARS for ch in line)
    )
    return DumpFrame(
        slot=int(m.group(1)),
        nbytes=int(m.group(2)),
        sha256=m.group(3).lower(),
        b64=b64,
    )


def decode_frame(frame: DumpFrame) -> bytes:
    """Decode a scraped frame to raw bytes, VERIFYING both declared invariants.

    Raises ValueError if the decoded length or the sha256 does not match what the
    BEGIN line declared — a corrupt capture must fail here, not downstream.
    """
    raw = _b64.b64decode(frame.b64, validate=True)
    if len(raw) != frame.nbytes:
        raise ValueError(
            f"length mismatch: frame declared {frame.nbytes} bytes, "
            f"decoded {len(raw)}"
        )
    got = hashlib.sha256(raw).hexdigest()
    if got != frame.sha256:
        raise ValueError(
            f"sha256 mismatch: frame declared {frame.sha256}, decoded bytes hash {got}"
        )
    return raw


def scrape_and_decode(text: str) -> bytes:
    """Convenience: scrape the frame out of `text` and return VERIFIED bytes."""
    return decode_frame(scrape_frame(text))


def first_diff(a: bytes, b: bytes) -> int | None:
    """Return the index of the first differing byte, or None if a == b.

    If one is a prefix of the other, returns the length of the shorter (the first
    index at which they diverge in length).
    """
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    if len(a) != len(b):
        return n
    return None


def compare(a: bytes, b: bytes) -> tuple[bool, int | None]:
    """(equal, first_diff_index).  first_diff_index is None iff equal."""
    idx = first_diff(a, b)
    return (idx is None, idx)


def build_template_set_payload(*, template_set_uuid: str, ref: str,
                               person: str = "roundtrip", ordinal: int = 1,
                               character: str = "Test", rbac: str = "crew",
                               role_capability_table: dict | None = None) -> dict:
    """Build the exact JSON the firmware's onMqttMessage decoder consumes.

    The decoder reads doc["evt"] and doc["data"] at the TOP LEVEL, and each entry
    as {person, ordinal, character, rbac, template} — this mirrors that shape
    verbatim (NOT the {slot,tmpl,role} shape a stale note might suggest; there is
    no slot field, receive_push assigns the slot itself).
    """
    if role_capability_table is None:
        role_capability_table = {rbac: ["open_hatch"]}
    return {
        # envelope fields the decoder ignores, included for wire realism / debugging
        "type": "event",
        "src": "roundtrip-harness",
        "scope": "global",
        # the two fields the decoder actually reads:
        "evt": "template_set",
        "data": {
            "template_set_uuid": template_set_uuid,
            "role_capability_table": role_capability_table,
            "entries": [
                {
                    "person": person,
                    "ordinal": ordinal,
                    "character": character,
                    "rbac": rbac,
                    "template": ref,
                },
            ],
        },
    }


# Terminal outcomes receive_push publishes on the bus, matched by uuid.
_TERMINAL_EVENTS = ("template_set_loaded", "template_set_failed")


def format_failure_axis(data: dict) -> str:
    """Render a template_set_failed event's diagnostic verbatim: the axis, the
    sensor_code (present only on SENSOR/UNKNOWN axes), and the detail string."""
    axis = data.get("axis", "<no axis field>")
    detail = data.get("detail", "<no detail field>")
    code = data.get("sensor_code")
    code_s = "absent" if code is None else (
        f"0x{int(code):02X}" if isinstance(code, (int, float)) else str(code))
    return f"axis={axis}  sensor_code={code_s}  detail={detail!r}"


# ======================================================================
# HARDWARE ORCHESTRATION — serial + mqtt + store.  Not run by the subagent.
# ======================================================================

_FAIL_MARK = "read_template (dump) FAILED"


class R503DumpError(RuntimeError):
    """A dump that the firmware refused (e.g. sensor_code 0x0C — empty slot)."""


def _mqtt_broker() -> tuple[str, int]:
    raw = os.environ.get("COMPOSEIO_MQTT_BROKER", "mqtt-broker.local:1883")
    host, _, port = raw.partition(":")
    return host, int(port or "1883")


class SerialSession:
    """A SINGLE serial session for the whole run.

    Opens ONCE, tolerates the reset-on-open, waits for a boot sentinel, and keeps
    a rolling buffer with a consume cursor so repeated dump frames (each ending in
    the same END marker) are read in order without re-matching an old one.
    """

    def __init__(self, port: str, baud: int = 115200, echo: bool = True):
        self._port = port
        self._baud = baud
        self._echo = echo
        self.ser = None
        self._buf = ""
        self._pos = 0

    def open(self) -> None:
        import serial  # lazy: pyserial is a hardware-only dependency
        s = serial.Serial()
        s.port = self._port
        s.baudrate = self._baud
        # dtr/rts False so we don't add a SECOND toggle-triggered reset on top of
        # the USB-Serial-JTAG chip reset that open() already causes.
        s.dtr = False
        s.rts = False
        s.timeout = 0.2
        s.open()
        self.ser = s

    def close(self) -> None:
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass

    def _pump(self) -> None:
        data = self.ser.read(4096)
        if data:
            text = data.decode("utf-8", errors="replace")
            if self._echo:
                sys.stdout.write(text)
                sys.stdout.flush()
            self._buf += text

    def read_until_any(self, sentinels: list[str], timeout_s: float) -> tuple[str, str]:
        """Read until the EARLIEST of `sentinels` appears past the cursor.

        Returns (which_sentinel, text_from_cursor_through_sentinel) and advances
        the cursor past it.  Raises TimeoutError if none appears in time.
        """
        deadline = time.time() + timeout_s
        while True:
            best_idx, best_sent = -1, None
            for sent in sentinels:
                idx = self._buf.find(sent, self._pos)
                if idx >= 0 and (best_idx < 0 or idx < best_idx):
                    best_idx, best_sent = idx, sent
            if best_idx >= 0:
                end = best_idx + len(best_sent)
                chunk = self._buf[self._pos:end]
                self._pos = end
                return best_sent, chunk
            if time.time() >= deadline:
                raise TimeoutError(
                    f"none of {sentinels!r} within {timeout_s:.0f}s")
            self._pump()

    def read_until(self, sentinel: str, timeout_s: float) -> str:
        return self.read_until_any([sentinel], timeout_s)[1]

    def read_line_remainder(self, timeout_s: float = 2.0) -> str:
        """After a mid-line sentinel, grab the rest of that line."""
        try:
            return self.read_until("\n", timeout_s)
        except TimeoutError:
            return ""

    def send_line(self, line: str) -> None:
        self.ser.write((line + "\n").encode())
        self.ser.flush()

    def wait_for_boot(self, sentinel: str, timeout_s: float) -> bool:
        """Wait for the boot-complete sentinel after the reset-on-open.

        Returns True if seen.  On timeout, returns False (and the caller warns)
        rather than aborting — the board may already have been booted+subscribed
        before this session opened, in which case the sentinel is in the past.
        """
        print(f"[serial] waiting up to {timeout_s:.0f}s for boot sentinel "
              f"{sentinel!r} (the S3 resets on open)...")
        try:
            self.read_until(sentinel, timeout_s)
            print(f"[serial] boot sentinel seen — device is up and subscribed.")
            return True
        except TimeoutError:
            print(f"[serial] WARNING: boot sentinel {sentinel!r} not seen in "
                  f"{timeout_s:.0f}s.  Proceeding anyway (the board may already "
                  f"have been booted).  If nothing responds, the board is likely "
                  f"NOT in transport=mqtt, or RX has wedged — cold power-cycle.")
            return False

    def dump_slot(self, slot: int, attempts: int = 3, timeout_s: float = 15.0) -> bytes:
        """Send `r503 dump <slot>`, scrape the frame, return VERIFIED bytes.

        Retries on TIMEOUT (a transient no-answer).  Does NOT retry on a firmware
        REFUSAL (e.g. empty-slot 0x0C) — that is a definitive answer, raised as
        R503DumpError with the failure line.
        """
        last_err: Exception | None = None
        for attempt in range(1, attempts + 1):
            self.send_line(f"r503 dump {slot}")
            try:
                which, chunk = self.read_until_any([_END_MARK, _FAIL_MARK], timeout_s)
            except TimeoutError as e:
                last_err = e
                print(f"[serial] dump slot {slot} attempt {attempt}/{attempts} "
                      f"timed out; retrying" if attempt < attempts else
                      f"[serial] dump slot {slot} timed out after {attempts} attempts")
                continue
            if which == _FAIL_MARK:
                rest = self.read_line_remainder()
                raise R503DumpError(
                    f"firmware refused dump of slot {slot}: {(_FAIL_MARK + rest).strip()}")
            # chunk runs from before the BEGIN through the END marker
            return scrape_and_decode(chunk)
        raise TimeoutError(f"dump slot {slot} failed after {attempts} attempts: {last_err}")


class BusListener:
    """Subscribe to composeio/global/<device>/# BEFORE publishing, capture the
    terminal template_set_* events, and expose the publish side on the same
    connection.  Waits for the transport link before returning so no event is
    missed.

    THROUGH THE COMPOSEBUS WAIST, NEVER RAW paho ([SPEC:CXC-SOURCES-ON-BUS]
    [CXC-SOB-THESIS], DP-CIO-6 — transport is an implementation detail). The
    transport is chosen by `composeio.transport_registry` from a `type: mqtt`
    spec via `source_bus.acquire_bus`, so this same code rides CAN or UART with
    no edit here: it names a NAMESPACE and a SCOPE, never a topic string and
    never a client.

    THE WIRE THE FIRMWARE READS IS UNCHANGED. `onMqttMessage` decodes `evt` and
    `data` at the TOP LEVEL, and that is exactly a ComposeBus EVENT envelope
    (`BusMessage.to_json()` emits `evt`/`data` there) — so `publish_event` puts
    byte-identical `evt`/`data` on the topic the old hand-framed dict did. Only
    the transport moved; what the harness proves did not."""

    def __init__(self, host: str, port: int, device_id: str):
        # lazy: composeio is a hardware-orchestration dependency, kept out of
        # the module's import path so the pure-logic unit tests stay broker-free.
        from composeio import source_bus
        from composeio.namespace import Scope
        self._events: list[tuple[str, str, dict, float]] = []
        self._lock = threading.Lock()
        self._device_id = device_id
        # the ONE namespace we PUBLISH to (topic composeio/global/<device>/template_set)
        self._set_ns = f"{device_id}/template_set"
        # ONE self-created bus for this standalone driver; the registry builds
        # the mqtt transport behind it (an unreachable broker degrades LOUDLY,
        # publishing local-only, rather than silently — see source_bus).
        self._bus = source_bus.acquire_bus(
            f"mqtt://{host}:{port}", label="roundtrip-harness")
        # DECLARE the publish namespace GLOBAL, explicitly. An AUTO scope with a
        # pattern subscriber present could _resolve_scope to LOCAL and the push
        # would die in-process LOOKING SENT ([DEC-COMPOSER-SEND-GLOBAL-SCOPE]);
        # naming the scope is exactly what source_bus.declare exists to force.
        source_bus.declare(self._bus, self._set_ns, scope=Scope.GLOBAL,
                           source="roundtrip-harness")
        # CAPTURE every inbound message on the device subtree. A bus monitor
        # (on_message) hands back the full BusMessage — namespace, event_type
        # (== evt) and data — which is precisely the {topic, evt, data} the raw
        # paho on_message used to assemble by hand, and it fires for EVENTs
        # (a state callback does not).
        self._bus.on_message(self._on_bus_message)
        # WIDEN reception to composeio/+/<device>/# via MQTT's native wildcard.
        # subscribe_pattern registers the filter now and _on_connect re-applies
        # it on CONNACK, so a filter set before the link is up is never lost.
        self._bus.subscribe_pattern(f"{device_id}/*", self._noop_state)

    @staticmethod
    def _noop_state(_ns, _new, _old) -> None:
        # subscribe_pattern needs a STATE callback; the capture happens in the
        # on_message monitor above, which — unlike a state callback — also sees
        # EVENT traffic (the terminal template_set_* events are EVENTs).
        pass

    def _on_bus_message(self, msg) -> None:
        # EVENTs only — mirrors the old decoder, which read doc["evt"]/["data"].
        # `_dispatch_incoming` already dropped our own published echo before we
        # get here ([CXC-PS-NO-SELF-ECHO]), so our template_set command never
        # lands in the capture; only the firmware's replies do.
        if getattr(msg.type, "value", msg.type) != "event":
            return
        with self._lock:
            self._events.append(
                (msg.namespace, msg.event_type, msg.data or {}, time.time()))

    def wait_for_subscribed(self, timeout_s: float = 10.0) -> bool:
        # The seam's readiness gate: the global transport reached CONNACK, at
        # which point _on_connect has (re)issued our pattern SUBSCRIBE. Polling
        # is_connected() is the seam-level equivalent of the paho on_subscribe
        # SUBACK wait — same intent (do not publish into a dead subscription),
        # expressed without holding a client.
        transport = self._bus.global_transport
        if transport is None:
            return False
        # `is_connected` is a PROPERTY on the MQTT transport (a bool) but could
        # be a method on another substrate; read it either way, and treat a
        # transport with no such notion (a CAN/UART loopback) as ready.
        if not hasattr(transport, "is_connected"):
            return True
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            state = transport.is_connected
            if (state() if callable(state) else state):
                return True
            time.sleep(0.05)
        return False

    def publish(self, topic: str, payload: dict, qos: int = 1) -> None:
        # The push. `topic` is composeio/global/<device>/template_set and
        # `payload` carries {evt, data, ...}; over the seam that is one
        # publish_event(namespace, evt, data). The bus frames the canonical
        # envelope and the transport puts it on composeio/<scope>/<namespace> —
        # the namespace+scope come from the declared namespace, the evt/data
        # come from the payload, so nothing the firmware READS changes.
        ns = self._namespace_from_topic(topic)
        self._bus.publish_event(
            ns, payload.get("evt", ""), payload.get("data", {}) or {},
            source="roundtrip-harness")

    @staticmethod
    def _namespace_from_topic(topic: str) -> str:
        # composeio/<scope>/<namespace...> -> <namespace...>. Mirrors the MQTT
        # transport's own _namespace_from_topic so publish() honours whatever
        # topic the caller passed rather than assuming self._set_ns.
        parts = topic.split("/", 2)
        return parts[2] if len(parts) == 3 else topic

    def wait_for_terminal(self, template_set_uuid: str,
                          timeout_s: float = 30.0) -> tuple[str, dict] | None:
        """Poll for a template_set_loaded / _failed event matching the uuid."""
        deadline = time.time() + timeout_s
        seen = 0
        while time.time() < deadline:
            with self._lock:
                events = list(self._events[seen:])
                seen = len(self._events)
            for _topic, evt, data, _ts in events:
                if evt in _TERMINAL_EVENTS and \
                        data.get("template_set_uuid") == template_set_uuid:
                    return evt, data
            time.sleep(0.1)
        return None

    def all_events(self) -> list[tuple[str, str, dict, float]]:
        with self._lock:
            return list(self._events)

    def close(self) -> None:
        try:
            self._bus.off_message(self._on_bus_message)
            self._bus.stop()
        except Exception:
            pass


def _obtain_source_bytes(args, session: SerialSession | None):
    """Return (bytes_A, provenance_str) for whichever mode is active."""
    from composeio import resolve  # lazy: only needed for the store leg
    if args.reload_only:
        if args.a_file:
            with open(args.a_file, "rb") as f:
                a = f.read()
            return a, f"local file {args.a_file}"
        if args.ref:
            print(f"[src] resolving A from the store: {args.ref}")
            a = resolve.resolve_bytes(args.ref)
            return a, f"store ref {args.ref}"
        raise SystemExit(
            "--reload-only needs a source for bytes A: pass --ref "
            "fingerprint://<id> (resolve from the store) or --a-file <path>")
    # FULL mode: dump the source slot off the sensor
    assert session is not None
    print(f"[src] r503 dump {args.src_slot}  (source template -> bytes A)")
    a = session.dump_slot(args.src_slot)
    return a, f"sensor slot {args.src_slot}"


def run(args) -> int:
    from composeio import resolve  # lazy

    print("=" * 68)
    print("  R503 NO-MANGLING ROUND TRIP" +
          ("  [--reload-only: capture the failure axis]" if args.reload_only else ""))
    print(f"  device={args.device_id}  broker={os.environ.get('COMPOSEIO_MQTT_BROKER', 'mqtt-broker.local:1883')}")
    print(f"  store={resolve.TEMPLATE_STORE_URL}")
    print("=" * 68)

    session: SerialSession | None = None
    listener: BusListener | None = None
    try:
        # --- serial: open ONCE, wait for boot (unless --no-serial) ---
        if not args.no_serial:
            session = SerialSession(args.port, echo=True)
            session.open()
            session.wait_for_boot(args.boot_sentinel, args.boot_timeout)
        else:
            print("[serial] --no-serial: not opening the port; the failure event "
                  "is captured over MQTT.  (Board must already be booted + "
                  "subscribed in transport=mqtt.)")

        # --- obtain bytes A ---
        a, prov = _obtain_source_bytes(args, session)
        print(f"    A: {len(a)} bytes, sha256={hashlib.sha256(a).hexdigest()}  (from {prov})")

        # --- store leg: re-PUT to a FRESH ref, GET it back byte-identical ---
        ref = f"fingerprint://{uuid.uuid4()}"
        print(f"\n[store] PUT A -> {ref}")
        resolve.put_bytes(ref, a)
        got = resolve.resolve_bytes(ref)
        if got != a:
            print("    STORE LEG FAILED: GET did not return byte-identical A")
            print(f"    A={len(a)}B got={len(got)}B first_diff={first_diff(a, got)}")
            return 2
        print("    store leg OK: GET(ref) == A byte-exact")

        # --- subscribe BEFORE publish, then publish the push ---
        host, mqtt_port = _mqtt_broker()
        listener = BusListener(host, mqtt_port, args.device_id)
        if not listener.wait_for_subscribed(10.0):
            print("[bus] WARNING: SUBACK not confirmed in 10s; publishing anyway.")
        set_uuid = f"roundtrip-{uuid.uuid4()}"
        payload = build_template_set_payload(template_set_uuid=set_uuid, ref=ref)
        topic = f"composeio/global/{args.device_id}/template_set"
        print(f"\n[push] publish template_set uuid={set_uuid}")
        print(f"    topic={topic}")
        listener.publish(topic, payload)

        # --- capture the terminal event off the bus (the diagnostic) ---
        print(f"\n[capture] waiting up to {args.event_timeout:.0f}s for the "
              f"template_set_loaded / _failed event on "
              f"composeio/global/{args.device_id}/# ...")
        # opportunistically echo the serial receive_push line too, if we have serial
        if session is not None:
            try:
                line = session.read_until("receive_push ->", timeout_s=args.event_timeout)
                tail = session.read_line_remainder()
                for ln in (line + tail).splitlines():
                    if "receive_push ->" in ln:
                        print(f"    serial: {ln.strip()}")
            except TimeoutError:
                print("    (no receive_push line on serial within the window)")

        result = listener.wait_for_terminal(set_uuid, timeout_s=args.event_timeout)
        if result is None:
            print("\n" + "=" * 68)
            print("  RESULT: INCONCLUSIVE — no terminal event for this uuid arrived.")
            evs = listener.all_events()
            print(f"    {len(evs)} bus event(s) captured on the device namespace:")
            for topic, evt, data, _ts in evs[-10:]:
                print(f"      {topic}  evt={evt}  uuid={data.get('template_set_uuid')}")
            print("    Likely: the device is not subscribed (not in transport=mqtt),")
            print("    or the push never reached it.  Re-run WITHOUT --no-serial to")
            print("    drive a boot and confirm the [mqtt] subscribed: line.")
            print("=" * 68)
            return 3

        evt, data = result
        if evt == "template_set_failed":
            print("\n" + "=" * 68)
            print("  RESULT: RELOAD FAILED — and here is the axis, verbatim:")
            print(f"    {format_failure_axis(data)}")
            print("  READ IT: axis=transport => the firmware's HTTPClient GET from the")
            print("           template store (buildhost.local:8423) failed — the STORE")
            print("           leg from the DEVICE's side, not this harness's side.")
            print("           axis=sensor_refusal/unknown_code => DownChar+Store of the")
            print("           REAL template into the R503 failed; sensor_code is the")
            print("           exact R503 confirmation byte.  axis=capacity => size/slot.")
            print("=" * 68)
            return 1

        # loaded -> the reload worked; if we have serial, prove A == B
        print(f"\n[push] receive_push loaded (uuid={data.get('template_set_uuid')})")
        if session is None:
            print("\n" + "=" * 68)
            print("  RESULT: RELOAD LOADED (bus event).  Re-run WITHOUT --no-serial")
            print("  and with --dst-slot to dump B and prove A == B byte-exact.")
            print("=" * 68)
            return 0
        time.sleep(1.0)  # let the swap settle
        print(f"\n[verify] r503 dump {args.dst_slot}  (loaded template -> bytes B)")
        b = session.dump_slot(args.dst_slot)
        print(f"    B: {len(b)} bytes, sha256={hashlib.sha256(b).hexdigest()}")

        out_dir = args.out_dir
        os.makedirs(out_dir, exist_ok=True)
        a_path = os.path.join(out_dir, "roundtrip_A.bin")
        b_path = os.path.join(out_dir, f"roundtrip_B_slot{args.dst_slot}.bin")
        with open(a_path, "wb") as f:
            f.write(a)
        with open(b_path, "wb") as f:
            f.write(b)
        print(f"    wrote {a_path}\n    wrote {b_path}")

        equal, idx = compare(a, b)
        print("\n" + "=" * 68)
        if equal:
            print("  RESULT: PASS — A == B byte-exact.  The template survived the")
            print("  store round trip AND the DownChar into the physical sensor with")
            print("  NO MANGLING.")
            print("=" * 68)
            return 0
        print("  RESULT: FAIL — A != B.")
        print(f"    len(A)={len(a)}  len(B)={len(b)}  first differing index={idx}")
        print("=" * 68)
        return 1
    finally:
        if listener is not None:
            listener.close()
        if session is not None:
            session.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default="/dev/cu.usbmodem11301",
                    help="S3 CDC serial port (default /dev/cu.usbmodem11301)")
    ap.add_argument("--src-slot", type=int, default=1,
                    help="FULL mode: slot to dump as the source template (default 1)")
    ap.add_argument("--dst-slot", type=int, default=0,
                    help="slot to dump AFTER a successful load; where the swap "
                         "landed (default 0 — lowest free page after empty())")
    ap.add_argument("--device-id", default="dev/s3-fp1",
                    help="device id in the topic (default dev/s3-fp1)")
    ap.add_argument("--reload-only", action="store_true",
                    help="skip the source dump (sensor already empty); obtain A "
                         "from --ref or --a-file, publish, and CAPTURE the failure axis")
    ap.add_argument("--ref", default=None,
                    help="reload-only: fingerprint://<id> to resolve bytes A from the store")
    ap.add_argument("--a-file", default=None,
                    help="reload-only: local path to bytes A (fallback if --ref expired)")
    ap.add_argument("--no-serial", action="store_true",
                    help="do NOT open the serial port (avoid the reset-on-open); "
                         "the failure event is still captured over MQTT")
    ap.add_argument("--boot-sentinel", default="[mqtt] subscribed:",
                    help="serial line that means boot-complete + subscribed "
                         "(default '[mqtt] subscribed:')")
    ap.add_argument("--boot-timeout", type=float, default=60.0,
                    help="seconds to wait for the boot sentinel (default 60)")
    ap.add_argument("--event-timeout", type=float, default=30.0,
                    help="seconds to wait for the terminal bus event (default 30)")
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)),
                    help="where to write A/B .bin captures (default: beside this script)")
    args = ap.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
