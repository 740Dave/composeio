#!/usr/bin/env python3
"""loopback_test.py — prove the CAN <-> MQTT bridge on a two-channel rig.

THE RIG
    A Waveshare 2-CH CAN HAT with can0 and can1 physically jumpered
    (CAN_H<->CAN_H, CAN_L<->CAN_L, 120R at both ends), both at 500 kbit/s. The
    bridge owns can0. can1 is THIS script's channel and nothing else's.

WHY THIS FILE DOES NOT IMPORT composeio
    Deliberately, and it is the whole point. A property read back through the
    same assumption that wrote it cannot be self-verified: if `namespace_hash`
    were wrong, a verifier that called `namespace_hash` would agree with it and
    the test would pass on a broken wire. So this script re-implements
    CRC-16/CCITT-FALSE from the polynomial (with a published check vector), and
    packs/parses SocketCAN frames from the kernel's 16-byte struct directly with
    stdlib `socket`. Its only third-party import is paho, for the MQTT side.

    It also prints the raw `candump`-style hex of every frame, so a human can
    check the bytes without trusting any decoder at all.

    It is ALSO deployed alone — `deploy.sh` copies this one file to
    /opt/composeio-can-bridge/, where `composeio` is not on the path. So the
    reimplementation is not merely preferred, it is required at the target.

⛔ AND WHY THAT LICENCE EXPIRED THE MOMENT IT DRIFTED (fixed 2026-08-14)
    An independent oracle is only an oracle WHILE IT AGREES. This file shipped
    with three restatements that had gone stale against the wire they verify,
    and nothing in the repo compared them, so the drift was invisible:

      * `decode_value` was, in its own words, a "mirror of the bridge's inline
        value decode" — a decoder DELETED on 2026-08-13. It carried
        `int.from_bytes(data, "little")`, no float branch, and no knowledge of
        the [BP-VALUE-CODEC] 0xFE/0xFF type tags, so a tagged float32 42.0
        (`FF 00 00 28 42`) printed as 284138930431. That is
        DEFECT-FLOAT32-PUBLISHED-FROM-CAN-ARRIVES-5AA80B, alive in the
        instrument after being killed in the code.
      * `expected_can_id`/`split_can_id` implemented [BP-CAN-ID] **v1**
        (`ns<<13 | type<<9 | src9`). The bridge speaks **v2**
        (`prio(3)|type(4)|ns(16)|src(4)|scope(2)`), so no id could ever match
        and `mqtt-to-can` reported "no frame with the expected arbitration id"
        against a PERFECTLY WORKING bridge.
      * `can-to-mqtt` hand-packed an untagged `struct.pack("<i", v)`, which a
        correct bridge is right to refuse.

    A forked instrument does not just fail to catch a defect — it MANUFACTURES
    one, and two of the three failures above are loud lies about working code
    while the third (a `capture` printing 284138930431 for 42.0) is silent.

    The reimplementation is KEPT, because the self-verification argument above
    is still true. What changed is that the AGREEMENT is now asserted, by
    `python/tests/test_can_deploy_oracle_agrees_with_canon.py`, which imports
    the canon, loads this file, and pins them field by field. Edit either side
    and that test says so. A deliberate second implementation without such a
    test is an ordinary fork with a good excuse.

SUBCOMMANDS
    selftest      CRC check vector + frame pack/parse round trip. No hardware.
    mqtt-to-can   publish to MQTT, expect the frame on can1, verify id + value
    can-to-mqtt   transmit on can1, expect the bridge's MQTT publish
    capture       dump + decode can1 for N seconds (the live-throttle proof)
    echo-check    watch BOTH directions for a relay loop

USAGE (on the Pi; the deployed venv has paho)
    /opt/composeio-can-bridge/venv/bin/python loopback_test.py selftest
    ... mqtt-to-can --ns throttle/left --value 200
    ... can-to-mqtt --ns throttle/right --value 42
    ... capture --seconds 60 --ns throttle/left --ns throttle/right

    `--value` takes an int, A FLOAT, or a string, in that order. `--value 42.0`
    is a float32 and `--value 42` is an int — the distinction the wire now
    carries, and the one this harness could not previously express at all.
    `--scope` and `--node-id` set the v2 id fields that v1 did not have.

    `selftest` needs no hardware and no broker, and it now round-trips the
    VALUE CODEC as well as the id — run it first, on any machine.
"""
from __future__ import annotations

import argparse
import json
import select
import socket
import struct
import sys
import threading
import time

# --- SocketCAN wire format, from the kernel ABI -----------------------------
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CAN_EFF_MASK = 0x1FFFFFFF
CAN_SFF_MASK = 0x000007FF
AF_CAN = PF_CAN = 29
CAN_RAW = 1
FRAME_FMT = "=IB3x8s"
FRAME_SIZE = struct.calcsize(FRAME_FMT)      # 16

#: [BP-TYPE-CODES]. Independently transcribed from the spec, NOT imported, so a
#: drift in composeio.transport's table shows up here as a mismatch instead of
#: being silently agreed with. This is the same defect class that made a UART
#: ACK decode as STATE in three separate per-transport copies.
WIRE_CODES = {
    "state": 0, "event": 1, "config_push": 2, "config_request": 3,
    "heartbeat": 4, "discovery": 5, "time_sync": 6, "binding": 7,
    "ack": 8, "log_entry": 9,
}
CODE_NAMES = {v: k for k, v in WIRE_CODES.items()}


def crc16_ccitt_false(name: str) -> int:
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, no reflection, no xorout.

    Written from the polynomial rather than imported. `selftest` asserts the
    published check vector CRC("123456789") == 0x29B1."""
    crc = 0xFFFF
    for b in name.encode("ascii"):
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


#: [BP-CAN-ID] **v2**, 29 bits: prio(3) | type(4) | ns_hash(16) | src(4) | scope(2).
#: Widths first, shifts derived, sum asserted — the same shape the canon uses, so
#: a future width change cannot leave one shift behind in either copy.
_PRIO_BITS, _TYPE_BITS, _NS_BITS, _SRC_BITS, _SCOPE_BITS = 3, 4, 16, 4, 2
assert _PRIO_BITS + _TYPE_BITS + _NS_BITS + _SRC_BITS + _SCOPE_BITS == 29

_SCOPE_SHIFT = 0
_SRC_SHIFT = _SCOPE_BITS                                 # 2
_NS_SHIFT = _SRC_SHIFT + _SRC_BITS                       # 6
_TYPE_SHIFT = _NS_SHIFT + _NS_BITS                       # 22
_PRIO_SHIFT = _TYPE_SHIFT + _TYPE_BITS                   # 26

#: Everything BELOW priority. A receiver routes on these and on nothing else.
CORE_MASK = (1 << _PRIO_SHIFT) - 1

#: [BP-MSG-FIELDS] 2-bit scope enum. `auto` is CARRIED as `auto`, not collapsed
#: to `global` — collapsing it on the wire is what made a CAN node's `local`
#: message arrive relabelled, which is the leak an internal trunk cannot have.
SCOPE_CODES = {"local": 0x0, "global": 0x1, "remote": 0x2, "auto": 0x3}
SCOPE_NAMES = {v: k for k, v in SCOPE_CODES.items()}

#: `src` is an ALLOCATED 4-bit node id, not a hash slice. 0 = UNASSIGNED, which
#: is honest and is distinguishable from node 1 — a 9-bit hash of a name never
#: was. The bridge's own id comes from CAN_BRIDGE_NODE_ID (default 0).
NODE_ID_UNASSIGNED = 0
NODE_ID_MAX = (1 << _SRC_BITS) - 1                       # 15


def expected_can_id(namespace: str, msg_type: str, scope: str = "global",
                    node_id: int = NODE_ID_UNASSIGNED, prio: int = 0) -> int:
    """[BP-CAN-ID] v2 -> a 29-bit extended arbitration id.

    PRIORITY DEFAULTS TO 0 AND IS NOT COMPUTED HERE, ON PURPOSE. The canonical
    tier comes from a RULE TABLE (`transport.priority_for`), and restating a
    rule table in the oracle would be forking a second table in order to check
    the first — the exact move this file just paid for three times. Priority is
    also the sender's own opinion of its own urgency: it has already done its
    only job (winning or losing arbitration) by the time a receiver sees the
    frame, and `decode_arbitration_id` deliberately does not return it. So
    matching is done on `CORE_MASK` and the observed tier is REPORTED instead.
    """
    if not (NODE_ID_UNASSIGNED <= node_id <= NODE_ID_MAX):
        raise ValueError(f"node id {node_id} outside 0..{NODE_ID_MAX} — it is "
                         f"NOT masked, because 16 would silently become 0")
    return (((prio & 0x7) << _PRIO_SHIFT)
            | ((WIRE_CODES[msg_type] & 0x0F) << _TYPE_SHIFT)
            | ((crc16_ccitt_false(namespace) & 0xFFFF) << _NS_SHIFT)
            | ((node_id & 0x0F) << _SRC_SHIFT)
            | (SCOPE_CODES.get(scope, 0x3) << _SCOPE_SHIFT))


def split_can_id(arb: int) -> tuple[int, int, int, str]:
    """-> (ns_hash, type_code, src_node, scope). Priority via `id_priority`."""
    return ((arb >> _NS_SHIFT) & 0xFFFF,
            (arb >> _TYPE_SHIFT) & 0x0F,
            (arb >> _SRC_SHIFT) & 0x0F,
            SCOPE_NAMES.get((arb >> _SCOPE_SHIFT) & 0x03, "auto"))


def id_priority(arb: int) -> int:
    """The tier the frame was SENT with. For captures and eyes, never routing."""
    return (arb >> _PRIO_SHIFT) & 0x07


class Can:
    """A raw AF_CAN socket. No composeio, no python-can."""

    def __init__(self, iface: str):
        self.iface = iface
        self.sock = socket.socket(AF_CAN, socket.SOCK_RAW, CAN_RAW)
        self.sock.bind((iface,))

    def send(self, arb: int, data: bytes, eff: bool = True) -> None:
        cid = (arb & (CAN_EFF_MASK if eff else CAN_SFF_MASK)) | (CAN_EFF_FLAG if eff else 0)
        payload = bytes(data[:8])
        self.sock.send(struct.pack(FRAME_FMT, cid, len(payload),
                                   payload.ljust(8, b"\x00")))

    def recv(self, timeout: float):
        """-> (arb, eff, data, hexdump) or None on timeout."""
        ready, _, _ = select.select([self.sock], [], [], timeout)
        if not ready:
            return None
        raw = self.sock.recv(FRAME_SIZE)
        if len(raw) < FRAME_SIZE:
            return None
        cid, dlc, d8 = struct.unpack(FRAME_FMT, raw[:FRAME_SIZE])
        if cid & (CAN_RTR_FLAG | CAN_ERR_FLAG) or dlc > 8:
            return None
        eff = bool(cid & CAN_EFF_FLAG)
        arb = cid & (CAN_EFF_MASK if eff else CAN_SFF_MASK)
        data = d8[:dlc]
        # candump's own text form, so the two can be compared by eye.
        dump = f"{self.iface}  {arb:08X}   [{dlc}]  " + " ".join(f"{b:02X}" for b in data)
        return arb, eff, data, dump

    def close(self):
        self.sock.close()


def describe(arb: int, data: bytes, known: dict[int, str]) -> str:
    ns_h, tcode, src, scope = split_can_id(arb)
    ns = known.get(ns_h, f"<UNRESOLVED 0x{ns_h:04X}>")
    t = CODE_NAMES.get(tcode, f"<UNALLOCATED {tcode}>")
    return (f"ns_hash=0x{ns_h:04X} ({ns})  type={tcode} ({t})  "
            f"src={src}{' UNASSIGNED' if src == NODE_ID_UNASSIGNED else ''}  "
            f"scope={scope}  prio={id_priority(arb)}  "
            f"data={data.hex(' ') or '-'}  value={decode_value(data)!r}")


# --- [BP-VALUE-CODEC]: the wire value CARRIES ITS TYPE ---------------------- #
# int32 and float32 were both four bytes with NO discriminator, so every decoder
# had to GUESS, and the two live decoders guessed differently: the canon read
# float (1000 came back as 1.4e-42, -5 as nan) and the bridge read int (42.0
# came back as its IEEE-754 bit pattern). Negatives were wrong in BOTH, which is
# why "make them agree" was never the fix — it only moves which values corrupt.
#
# One in-band tag byte, not a header bit: a header bit works for UART and cannot
# work for CAN, whose v2 id sums to exactly 29 with none spare. It also makes
# `decode_value(encode_value(x)) == x` a property of the CODEC ALONE, which is
# what the agreement test asserts. 0xFE/0xFF can never be a length this encoder
# emits, because strings are truncated at 200 bytes.
VALUE_TAG_INT32 = 0xFE
VALUE_TAG_FLOAT32 = 0xFF


def encode_value(value) -> bytes:
    """Pack a value the way a correct peer packs it.

    `can-to-mqtt` INJECTS a value onto the segment, so the harness is a
    TRANSMITTER and needs the encode side too. It used to hand-pack
    `struct.pack("<i", v)` inline at the call site — an UNTAGGED int32 that a
    correct bridge refuses on sight, so the harness would blame the bridge for
    its own frame.
    """
    if value is None:
        return b""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, bool):
        return b"\x01" if value else b"\x00"
    if isinstance(value, int):
        if 0 <= value <= 255:
            return struct.pack("B", value)
        return bytes((VALUE_TAG_INT32,)) + struct.pack("<i", value)
    if isinstance(value, float):
        return bytes((VALUE_TAG_FLOAT32,)) + struct.pack("<f", value)
    s = str(value).encode("utf-8")[:200]
    return struct.pack("B", len(s)) + s


def decode_value(data: bytes):
    """Read a wire value. Independently written; pinned against the canon by
    `python/tests/test_can_deploy_oracle_agrees_with_canon.py`.

    AN UNTAGGED 4-BYTE VALUE IS RETURNED AS BYTES, NOT GUESSED. That is not
    laziness — it is the flag day made visible. A peer that has not been
    reflashed emits untagged widths, and this harness must show that frame as
    UNDECODABLE rather than as a plausible-looking number, or a stale node
    reads as a healthy one in the acceptance capture.
    """
    if not data:
        return None
    if len(data) == 5:
        if data[0] == VALUE_TAG_INT32:
            return struct.unpack("<i", data[1:])[0]
        if data[0] == VALUE_TAG_FLOAT32:
            return struct.unpack("<f", data[1:])[0]
    if len(data) == 1:
        return data[0]
    if data[0] == len(data) - 1:
        try:
            return data[1:].decode("utf-8")
        except UnicodeDecodeError:
            pass
    return data


def wire_value(text: str):
    """argparse type: int, else float, else string — so `--value 42.0` can
    finally BE a float. `--value` used to be `type=int`, which is the other
    reason the float defect could never be caught by this instrument: the one
    tool built to exercise the wire was incapable of putting a float on it.
    """
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


# --- MQTT helper ------------------------------------------------------------

def mqtt_client(broker: str, port: int, client_id: str):
    import paho.mqtt.client as mqtt
    # MQTT 5, matching the bridge and transport this instrument measures. An
    # acceptance instrument on a different protocol version than the thing it
    # accepts is measuring a configuration nobody ships.
    c = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)
    c.connect(broker, port, 60)
    c.loop_start()
    return c


def publish(c, prefix: str, ns: str, value, source: str, msg_type="state",
            scope="global"):
    """retain=False, always. A bridge relays live traffic; a retained test
    message would resurrect on the production bus for every future subscriber."""
    payload = json.dumps({"ns": ns, "type": msg_type, "val": value,
                          "src": source, "ts": time.time(),
                          "scope": scope, "seq": 0})
    c.publish(f"{prefix}/{scope}/{ns}", payload, qos=1, retain=False)
    return payload


# --- subcommands ------------------------------------------------------------

def cmd_selftest(a) -> int:
    ok = True
    got = crc16_ccitt_false("123456789")
    print(f"CRC-16/CCITT-FALSE('123456789') = 0x{got:04X} (expect 0x29B1)")
    ok &= got == 0x29B1
    for ns in ("throttle/left", "throttle/right", "shields"):
        print(f"  {ns:16s} -> 0x{crc16_ccitt_false(ns):04X}")

    arb = expected_can_id("throttle/left", "state", "local", 7)
    ns_h, t, src, scope = split_can_id(arb)
    print(f"id 0x{arb:08X} splits to ns=0x{ns_h:04X} type={t} src={src} "
          f"scope={scope} prio={id_priority(arb)}")
    ok &= ns_h == crc16_ccitt_false("throttle/left") and t == 0
    ok &= src == 7 and scope == "local"          # v1 had NEITHER field
    ok &= arb <= CAN_EFF_MASK

    # THE VALUE CODEC, ROUND TRIP, WITH VALUES THAT HAVE DISTINCT BYTES.
    # 0.0 is deliberately absent from the float cases: four zero bytes decode to
    # 0 under a correct codec AND under the int-guessing one this file used to
    # carry, so a selftest built on 0.0 passes while the wire is broken. That is
    # exactly how DEFECT-FLOAT32-PUBLISHED-FROM-CAN-ARRIVES-5AA80B survived
    # months of live traffic: it is invisible when the bus looks healthy.
    for v in (0, 1, 255, 256, 1000, -5, 42.0, 3.14, -1.5, "ahead flank"):
        wire = encode_value(v)
        back = decode_value(wire)
        same = (abs(back - v) < 1e-3) if isinstance(v, float) else (back == v)
        print(f"  value {v!r:14} -> {wire.hex(' '):20} -> {back!r:14} "
              f"{'ok' if same else 'MISMATCH'}")
        ok &= bool(same)

    # An UNTAGGED 4-byte value must come back as BYTES, never as a number. A
    # pre-flag-day peer has to read as undecodable, not as plausible.
    stale = decode_value(b"\xe8\x03\x00\x00")
    print(f"  untagged 4-byte e8 03 00 00 -> {stale!r} "
          f"({'ok, not guessed' if isinstance(stale, bytes) else 'GUESSED'})")
    ok &= isinstance(stale, bytes)

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_mqtt_to_can(a) -> int:
    """Publish on MQTT; the bridge must put exactly one matching frame on can0,
    which can1 sees over the jumper."""
    can = Can(a.can)
    # Match on CORE_MASK, not on the whole id: priority is set by the BRIDGE's
    # rule table and this oracle deliberately does not restate that table.
    exp = expected_can_id(a.ns, a.type, a.scope, a.node_id)
    print(f"expect on {a.can}: id=0x{exp:08X} & core "
          f"(ns 0x{crc16_ccitt_false(a.ns):04X} | type {WIRE_CODES[a.type]} | "
          f"src {a.node_id} | scope {a.scope}); any priority tier accepted")

    c = mqtt_client(a.broker, a.port, a.client_id)
    time.sleep(0.5)
    payload = publish(c, a.prefix, a.ns, a.value, a.source, a.type, a.scope)
    print(f"published {a.prefix}/{a.scope}/{a.ns}: {payload}")

    known = {crc16_ccitt_false(a.ns): a.ns}
    deadline = time.time() + a.timeout
    hits = []
    while time.time() < deadline:
        f = can.recv(0.2)
        if f is None:
            continue
        arb, eff, data, dump = f
        print(f"  RX {dump}")
        print(f"     {describe(arb, data, known)}")
        if arb & CORE_MASK == exp & CORE_MASK:
            hits.append((arb, data, eff))
    c.loop_stop(); c.disconnect(); can.close()

    if not hits:
        print("FAIL: no frame with the expected arbitration id")
        return 1
    arb, data, eff = hits[0]
    got = decode_value(data)
    print(f"matched {len(hits)} frame(s); eff={eff} prio={id_priority(arb)} "
          f"value={got!r} (published {a.value!r})")
    if not eff:
        print("FAIL: frame was not extended (29-bit) format")
        return 1
    # Floats are compared with a TOLERANCE: the wire carries IEEE-754 binary32
    # and the value published was a Python float64, so 3.14 cannot survive as
    # bit-equal and an `!=` here would report a correct bridge as broken.
    if isinstance(a.value, float):
        mismatch = not isinstance(got, float) or abs(got - a.value) > 1e-3
    else:
        mismatch = got != a.value
    if mismatch:
        print(f"FAIL: value mismatch — sent {a.value!r}, wire decodes {got!r}")
        return 1
    if len(hits) > 1:
        print(f"WARN: {len(hits)} identical frames — check for a relay loop")
    print("MQTT->CAN PASS")
    return 0


def cmd_can_to_mqtt(a) -> int:
    """Transmit on can1; the bridge must publish it to MQTT, named correctly."""
    got: list[dict] = []
    lock = threading.Lock()

    import paho.mqtt.client as mqtt
    topic = f"{a.prefix}/{a.scope}/{a.ns}"

    def on_msg(_c, _u, m):
        with lock:
            got.append(json.loads(m.payload.decode()))

    c = mqtt.Client(client_id=a.client_id, protocol=mqtt.MQTTv5)
    c.on_message = on_msg
    c.connect(a.broker, a.port, 60)
    c.subscribe(f"{a.prefix}/+/{a.ns}", qos=1)
    c.loop_start()
    time.sleep(1.0)

    can = Can(a.can)
    arb = expected_can_id(a.ns, a.type, a.scope, a.node_id)
    # ENCODED BY THE CODEC, NOT HAND-PACKED. This line used to be
    # `bytes([v]) if 0 <= v <= 255 else struct.pack("<i", v)` — an UNTAGGED
    # int32 for anything over 255, which a correct bridge refuses on sight
    # ([BP-VALUE-CODEC]). The harness would then report FAIL for a frame it had
    # malformed itself.
    data = encode_value(a.value)
    print(f"TX on {a.can}: id=0x{arb:08X} data={data.hex(' ')}  "
          f"(ns {a.ns}, type {a.type}, scope {a.scope}, src {a.node_id}, "
          f"value {a.value!r})")
    if len(data) > 8:
        print(f"FAIL: {len(data)} bytes will not fit a classic CAN frame — "
              f"the bridge DROPS oversize rather than truncating, so this "
              f"value cannot be tested on this substrate")
        can.close(); c.loop_stop(); c.disconnect()
        return 1
    can.send(arb, data, eff=True)

    deadline = time.time() + a.timeout
    while time.time() < deadline:
        with lock:
            if got:
                break
        time.sleep(0.1)
    time.sleep(0.5)
    can.close(); c.loop_stop(); c.disconnect()

    if not got:
        print(f"FAIL: nothing published to {topic} within {a.timeout}s")
        return 1
    for m in got:
        print(f"  MQTT {m}")
    m = got[0]
    ok = True
    if m.get("ns") != a.ns:
        print(f"FAIL: namespace is {m.get('ns')!r}, expected {a.ns!r}"); ok = False
    val = m.get("val")
    bad_val = (not isinstance(val, (int, float)) or abs(val - a.value) > 1e-3) \
        if isinstance(a.value, float) else (val != a.value)
    if bad_val:
        print(f"FAIL: value is {val!r}, expected {a.value!r}"); ok = False
    if m.get("scope") != a.scope:
        print(f"FAIL: scope is {m.get('scope')!r}, expected {a.scope!r} — "
              f"scope had no field at all in the v1 id, so it arrived "
              f"relabelled 'global' on every round trip")
        ok = False
    if m.get("type") != a.type:
        print(f"FAIL: type is {m.get('type')!r}, expected {a.type!r}"); ok = False
    if m.get("src") != "can":
        print(f"FAIL: src is {m.get('src')!r}, expected 'can' — this tag IS the "
              f"echo guard; without it the message relays back to CAN forever")
        ok = False
    print("CAN->MQTT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def cmd_capture(a) -> int:
    """Watch can1 and report every frame, resolving the namespaces given."""
    known = {crc16_ccitt_false(ns): ns for ns in a.ns}
    can = Can(a.can)
    print(f"capturing {a.can} for {a.seconds}s; resolving "
          + ", ".join(f"{n}=0x{h:04X}" for h, n in known.items()))
    print(f"{'time':>8}  {'iface':5}  {'canid':>8}  dlc  data        decoded")
    t0 = time.time()
    n = 0
    per_ns: dict[str, int] = {}
    unresolved: dict[int, int] = {}
    while time.time() - t0 < a.seconds:
        f = can.recv(0.5)
        if f is None:
            continue
        arb, eff, data, dump = f
        n += 1
        ns_h, tcode, src, scope = split_can_id(arb)
        name = known.get(ns_h)
        if name is None:
            unresolved[ns_h] = unresolved.get(ns_h, 0) + 1
        else:
            per_ns[name] = per_ns.get(name, 0) + 1
        value = decode_value(data)
        # An undecodable value is FLAGGED in the capture, not printed as a
        # b'...' a reader skims past. This column is the acceptance evidence,
        # and its whole job is to make a stale peer look stale.
        note = "  <- UNTAGGED/UNDECODABLE" if isinstance(value, bytes) else ""
        print(f"{time.time() - t0:8.3f}  {a.can:5}  {arb:08X}  "
              f"[{len(data)}]  {data.hex(' '):11}  "
              f"{name or f'0x{ns_h:04X}?':16} type={CODE_NAMES.get(tcode, tcode)} "
              f"src={src} scope={scope} value={value!r}{note}")
    can.close()
    print(f"\n{n} frame(s) in {a.seconds}s")
    for k, v in sorted(per_ns.items()):
        print(f"  {k:20s} {v}")
    for h, v in sorted(unresolved.items()):
        print(f"  UNRESOLVED 0x{h:04X}  {v}")
    return 0 if n else 1


def cmd_echo_check(a) -> int:
    """A frame the bridge PUBLISHED must never come back down onto CAN.

    Injects one frame on can1 for a namespace that is BOTH registered and
    allowlisted — the worst case, where nothing but the `source == "can"` tag
    stands between the bus and a loop."""
    can = Can(a.can)
    arb = expected_can_id(a.ns, "state", a.scope, a.node_id)
    print(f"injecting one frame id=0x{arb:08X} ns={a.ns} src={a.node_id} "
          f"value={a.value!r}")
    can.send(arb, encode_value(a.value), eff=True)

    seen = []
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        f = can.recv(0.5)
        if f is None:
            continue
        a_, _e, d, dump = f
        seen.append((round(time.time() - t0, 3), a_, d))
        print(f"  RX {dump}")
    can.close()
    # A CAN_RAW socket does not receive its OWN transmit (CAN_RAW_RECV_OWN_MSGS
    # is off), so anything here came from the bridge.
    #
    # Match on the NAMESPACE HASH, not the full arbitration id. A looped frame
    # does NOT carry the id we injected: the bridge republishes it to MQTT and
    # would re-emit it under ITS OWN src node id (CAN_BRIDGE_NODE_ID, 0 unless
    # allocated) and its own priority tier. An equality test on the full id
    # would pass while the loop ran.
    #
    # (In v1 this comment said the returning frame would carry "the hash of
    # 'can'" in a 9-bit source field. There is no source-NAME field in
    # [BP-CAN-ID] v2 — `src` is a 4-bit ALLOCATED node id — so that expectation
    # named a thing the wire no longer has.)
    ns_h = crc16_ccitt_false(a.ns)
    echoes = [s for s in seen if split_can_id(s[1])[0] == ns_h and s[0] > 0.05]
    print(f"\n{len(seen)} frame(s) observed in {a.seconds}s; "
          f"{len(echoes)} carrying ns_hash 0x{ns_h:04X} after the injection "
          f"(a loop would show the BRIDGE's src node id, not {a.node_id})")
    if echoes:
        print("FAIL: the injected namespace came back onto CAN — the "
              "source=='can' guard is NOT holding")
        for e in echoes:
            print(f"   t={e[0]}s id=0x{e[1]:08X} "
                  f"src={split_can_id(e[1])[2]} data={e[2].hex(' ')}")
        return 1
    print("ECHO-CHECK PASS (no relay loop)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--can", default="can1", help="the HARNESS channel, not the bridge's")
    p.add_argument("--broker", default="mqtt-broker.local")
    p.add_argument("--port", type=int, default=1883)
    p.add_argument("--prefix", default="composeio")
    p.add_argument("--client-id", default="canbridge-loopback-test")
    p.add_argument("--timeout", type=float, default=6.0)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)

    def _wire_args(sp):
        sp.add_argument("--ns", required=True)
        # `wire_value`, NOT `type=int`. A harness that can only send integers
        # cannot exercise the float path, and the float path is where
        # DEFECT-FLOAT32-PUBLISHED-FROM-CAN-ARRIVES-5AA80B lived.
        sp.add_argument("--value", type=wire_value, required=True,
                        help="int, float (42.0) or string — parsed in that order")
        sp.add_argument("--type", default="state", choices=sorted(WIRE_CODES))
        sp.add_argument("--source", default="canbridge-test",
                        help="MQTT-side source tag; NOT a wire field in v2")
        sp.add_argument("--scope", default="global", choices=sorted(SCOPE_CODES),
                        help="[BP-CAN-ID] v2 carries scope; v1 lost it")
        sp.add_argument("--node-id", type=int, default=NODE_ID_UNASSIGNED,
                        help=f"the 4-bit src node id, 0..{NODE_ID_MAX} "
                             f"(0 = UNASSIGNED)")

    m2c = sub.add_parser("mqtt-to-can")
    _wire_args(m2c)
    m2c.set_defaults(fn=cmd_mqtt_to_can)

    c2m = sub.add_parser("can-to-mqtt")
    _wire_args(c2m)
    c2m.set_defaults(fn=cmd_can_to_mqtt)

    cap = sub.add_parser("capture")
    cap.add_argument("--seconds", type=float, default=30.0)
    cap.add_argument("--ns", action="append", default=[])
    cap.set_defaults(fn=cmd_capture)

    ec = sub.add_parser("echo-check")
    ec.add_argument("--ns", default="throttle/left")
    ec.add_argument("--value", type=wire_value, default=77)
    ec.add_argument("--source", default="canbridge-echo")
    ec.add_argument("--scope", default="global", choices=sorted(SCOPE_CODES))
    ec.add_argument("--node-id", type=int, default=NODE_ID_UNASSIGNED)
    ec.add_argument("--seconds", type=float, default=8.0)
    ec.set_defaults(fn=cmd_echo_check)

    a = p.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
