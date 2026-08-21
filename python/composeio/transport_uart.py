"""UART transport binding — binary framed protocol for edge MCUs.

DP-CIO-6: Same bus semantics over UART as over MQTT/CAN/local.
Binary frame format optimized for CH32V003's 2KB SRAM.

Wire format:
    [0xC1][len][namespace_hash_hi][namespace_hash_lo][type_scope][value_bytes...][crc8]

    0xC1        — sync byte (ComposeIO marker)
    len         — payload length (after sync, before CRC): 3 + len(value_bytes)
    ns_hash     — 16-bit CRC-16/CCITT namespace hash (big-endian)
    type_scope  — bits 7..4: message type, bits 3..2: scope, bits 1..0: reserved
    value_bytes — variable length, SELF-DESCRIBING ([BP-VALUE-CODEC]):
                  binary: 1 byte (0=off, 1=on)
                  enum: 1 byte (index into values list)
                  continuous: 1 byte (0-255)
                  int32:  [0xFE][4 bytes little-endian signed]
                  float32:[0xFF][4 bytes little-endian IEEE-754]
                  string: length-prefixed UTF-8
    crc8        — CRC-8/MAXIM over all bytes from len through value_bytes

Type nibble values: allocated ONCE by [BP-TYPE-CODES] and imported from
`composeio.transport` — this module deliberately keeps no table of its own. See
`MESSAGE_TYPE_WIRE_CODES`; codes 0..9 are allocated, 10..15 reserved.

Scope values (2 bits):
    0 = local, 1 = global, 2 = remote, 3 = auto

SPEC: this frame layout is [BP-TRANSPORT-UART] / [BP-UART-FRAME] in
spec/01-composeio-bus-protocol.smd. It was written there on 2026-08-10; before
that the format existed only in this docstring even though
firmware/ftl_jump_button/src/main.cpp independently implements the same frame.
Change the spec and this module together, never one alone.
"""

from __future__ import annotations
import logging
import struct
import threading
import time
from collections import defaultdict
from typing import Callable, Any

from composeio.transport import (
    Transport, BusMessage, MessageType,  # noqa: F401  (MessageType re-exported for callers)
    message_type_for_wire_code, wire_code_for_type,
)
from composeio.config import namespace_hash
from composeio.freshness import UNKNOWN, is_unknown

logger = logging.getLogger("composeio.transport.uart")

# Sync byte
_SYNC = 0xC1

# The type nibble table is NOT here. It is `MESSAGE_TYPE_WIRE_CODES` in
# composeio.transport, reused by reference via the two accessors imported above
# ([BP-TYPE-CODES], one-implementation requirement). A local `_TYPE_TO_NIBBLE`
# copy lived here until 2026-08-10 and was missing ACK (8) and LOG_ENTRY (9),
# which `.get(type, 0)` then silently encoded as STATE.

# Scope to 2-bit value
_SCOPE_TO_BITS = {"local": 0, "global": 1, "remote": 2, "auto": 3}
_BITS_TO_SCOPE = {v: k for k, v in _SCOPE_TO_BITS.items()}

# Namespace hash cache (avoid recomputing)
_hash_cache: dict[str, int] = {}
_reverse_hash: dict[int, str] = {}


def _get_hash(ns: str) -> int:
    if ns not in _hash_cache:
        h = namespace_hash(ns)
        _hash_cache[ns] = h
        _reverse_hash[h] = ns
    return _hash_cache[ns]


def register_namespace_hash(ns: str):
    """Pre-register a namespace so incoming hashes can be reverse-mapped."""
    _get_hash(ns)


def _crc8(data: bytes) -> int:
    """CRC-8/MAXIM (Dallas/Maxim 1-Wire)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = (crc << 1) ^ 0x31
            else:
                crc = crc << 1
            crc &= 0xFF
    return crc


# --- the VALUE TYPE TAG  [BP-VALUE-CODEC] ---------------------------------- #
# DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9, closed 2026-08-13.
#
# int32 and float32 both encoded to FOUR BYTES WITH NO DISCRIMINATOR, so every
# decoder had to guess. `transport_uart.decode_value` guessed float (1000 came
# back as 1.4e-42, -5 as nan); `can_mqtt_bridge.message_from_can` guessed int
# (42.0 came back as 1109917696). NEGATIVE INTEGERS WERE WRONG IN BOTH, which is
# why "make them agree" was never a fix — it would only have moved which values
# corrupt. The wire had to say what it carries.
#
# WHY THE TAG IS IN-BAND, one byte, and not the "one spare bit" the plan costed.
# The plan proposed the UART `type_scope` byte's two spare bits. That works for
# UART and CANNOT work for CAN: the ratified [BP-CAN-ID] v2 layout
# `prio(3)|type(4)|ns_id(16)|src(4)|scope(2)` sums to exactly 29 and has no spare
# bit, and spending a CAN data byte on a flags field would cost a byte anyway
# while shrinking the chunk payload from 6 to 5. Two mechanisms — a header bit on
# UART and something else on CAN — is a FORK, which is the defect class this file
# has already paid for twice. So there is ONE mechanism: the value encoding is
# self-describing, which also makes `decode_value(encode_value(x)) == x` true as
# a property of the codec ALONE rather than of a codec plus its frame. The cost
# is one byte on values wider than a byte; small ints (all live throttle traffic
# is 0..255) and strings are unchanged.
#
# THE TAG BYTES CANNOT COLLIDE WITH A STRING. A string encodes as [len][utf8] and
# `encode_value` truncates at 200 bytes, so a leading byte of 0xFE or 0xFF can
# never be a length this encoder emitted.
VALUE_TAG_INT32 = 0xFE
VALUE_TAG_FLOAT32 = 0xFF

# `unknown` — [DEC-UNKNOWN-IS-A-TYPE-TAG-NOT-A-RESERVED-VALUE]. A ZERO-LENGTH
# payload, so the whole encoded value is this one byte.
#
# ⛔ AND THAT IS EXACTLY WHY IT NEEDED A CARVE-OUT THE OTHER TWO TAGS DID NOT.
# 0xFE/0xFF are safe because their discriminator is LENGTH: they are only ever
# READ as tags at five bytes, so the one-byte values 254 and 255 sail through
# the small-int fast path untouched. 0xFD HAS NO LENGTH TO BE DISCRIMINATED BY.
# Measured on the unmodified codec: `encode_value(253)` returned b"\xfd" —
# byte-identical to unknown — and `decode_value(b"\xfd")` returned integer 253
# through the `len(data) == 1` branch. So the defect was not merely that unknown
# would be mis-decoded; it is that THE CODEC STOPPED BEING INJECTIVE, with two
# distinct values sharing one encoding, and no amount of care at the decoder can
# recover information the encoder threw away.
#
# THE FIX IS AT THE ENCODER, and it is one integer wide: 253 leaves the small-int
# fast path and goes out TAGGED as int32. That costs four extra bytes for exactly
# one value in the entire domain, leaves 0..252 and 254..255 byte-for-byte
# unchanged, and buys back injectivity — after which a leading `fd` at length 1
# means unknown and can mean nothing else. Widening the carve-out to 254/255 as
# well would be cargo-culting: they never collided.
VALUE_TAG_UNKNOWN = 0xFD


def fits_small_int(value: int) -> bool:
    """Does this integer take the ONE-BYTE fast path?

    ⛔ THIS PREDICATE EXISTS SO NOBODY RESTATES IT.  `encode_value` below is the
    only authority on which integers are one byte, and the firmware parity
    GENERATOR has to know the same thing so it can tell the C++ harness which
    `bus_value_encode_*` entry point to call. It knew it by carrying a SECOND
    COPY of the `0 <= value <= 255` test — and that copy did NOT gain the
    `!= VALUE_TAG_UNKNOWN` clause when the 253 carve-out landed here.

    A generator that restates its oracle's rule can DISAGREE with the oracle and
    then blame the codec: the vectors would say "call encode_u8" while the
    encoder emitted five bytes, and the failure would be reported against
    bus_frame.cpp, which was right. This repo has paid for the fork-generator
    shape more than once ([BP-TYPE-CODES] is the same defect at the table
    level), so the rule lives in exactly one function and both callers call it.
    """
    return 0 <= value <= 255 and value != VALUE_TAG_UNKNOWN


def encode_value(value: Any) -> bytes:
    """Encode a value to bytes for the wire."""
    if value is None:
        return b""
    # `unknown` FIRST — before the bool/int branches, because `_Unknown` is
    # falsy and any `if not value` style test downstream would swallow it.
    if is_unknown(value):
        return bytes((VALUE_TAG_UNKNOWN,))
    # OPAQUE BYTES GO ON THE WIRE VERBATIM. Added 2026-08-12 for
    # [SPEC:CXC-CONFIG-BUS]: a config chunk is a byte string with its own
    # internal framing ([seq(2)][content...]) and MUST NOT acquire a second
    # length prefix or, worse, fall through to the `str(value)` branch below
    # and go out as the ASCII text "b'\\x00\\x00...'". There was no way to send
    # opaque bytes through this encoder at all — a genuine gap, not a policy.
    # No length prefix: the frame's own `len` byte already delimits the value,
    # and the config layer's decoder reads raw bytes rather than `decode_value`.
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, bool):
        return b"\x01" if value else b"\x00"
    if isinstance(value, int):
        # ⛔ 253 IS CARVED OUT OF THE FAST PATH. A bare 0xFD byte is the unknown
        # tag, so emitting 253 as one byte would make the codec non-injective —
        # see VALUE_TAG_UNKNOWN. Tagged int32 costs four extra bytes for this
        # ONE value; every other 0..255 integer keeps the single byte.
        if fits_small_int(value):
            return struct.pack("B", value)
        return bytes((VALUE_TAG_INT32,)) + struct.pack("<i", value)
    if isinstance(value, float):
        return bytes((VALUE_TAG_FLOAT32,)) + struct.pack("<f", value)
    # String values: length-prefixed UTF-8
    s = str(value).encode("utf-8")
    if len(s) > 200:
        s = s[:200]
    return struct.pack("B", len(s)) + s


def decode_value(data: bytes) -> Any:
    """Decode a value from wire bytes. Returns string for ambiguous single-byte values.

    TAGGED WIDTHS ARE READ FIRST and are exact — a 5-byte value led by
    `VALUE_TAG_INT32` is a signed int32 and a decoder may not read it as
    anything else ([BP-VALUE-CODEC]).

    An UNTAGGED 4-byte value is no longer guessed as a float32. That guess is
    the defect: it is what turned `1000` into 1.4e-42 and `-5` into nan. Bare
    bytes come back as bytes, which is this codec's existing "I will not guess"
    return and what the firmware reports as `BusValueKind::Raw` — so a frame
    from a peer that has not been reflashed is visibly undecodable rather than
    invisibly wrong. That is a FLAG DAY between tagged and untagged peers, on
    purpose: the whole fleet moves in one coordinated reflash.
    """
    if len(data) == 0:
        return None
    # `unknown` BEFORE the single-byte branch, which would otherwise return
    # integer 253. The encoder's 253 carve-out is what makes this unambiguous.
    if len(data) == 1 and data[0] == VALUE_TAG_UNKNOWN:
        return UNKNOWN
    if len(data) == 5:
        if data[0] == VALUE_TAG_INT32:
            return struct.unpack("<i", data[1:])[0]
        if data[0] == VALUE_TAG_FLOAT32:
            return struct.unpack("<f", data[1:])[0]
    if len(data) == 1:
        return data[0]  # Could be bool, int, or enum index
    if data[0] <= len(data) - 1:
        # Looks like length-prefixed string
        str_len = data[0]
        if str_len == len(data) - 1:
            try:
                return data[1:1 + str_len].decode("utf-8")
            except UnicodeDecodeError:
                pass
    return data


def encode_message(msg: BusMessage) -> bytes:
    """Encode a BusMessage to binary UART frame.

    Raises `UnmappedMessageType` if the message's type has no allocated wire
    code — never encodes it as something else ([BP-TYPE-CODES]).
    """
    ns_h = _get_hash(msg.namespace)
    type_nibble = wire_code_for_type(msg.type)
    scope_bits = _SCOPE_TO_BITS.get(msg.scope, 3)
    type_scope = (type_nibble << 4) | (scope_bits << 2)

    value_bytes = encode_value(msg.value)
    # Payload: ns_hash(2) + type_scope(1) + value_bytes
    payload = struct.pack(">HB", ns_h, type_scope) + value_bytes
    crc = _crc8(struct.pack("B", len(payload)) + payload)

    return bytes([_SYNC, len(payload)]) + payload + bytes([crc])


def decode_message(frame: bytes) -> BusMessage | None:
    """Decode a binary UART frame to BusMessage."""
    if len(frame) < 5:  # Minimum: sync + len + ns_hash(2) + type_scope + crc
        return None
    if frame[0] != _SYNC:
        return None

    payload_len = frame[1]
    if len(frame) < 2 + payload_len + 1:
        return None

    payload = frame[2:2 + payload_len]
    crc_received = frame[2 + payload_len]
    crc_computed = _crc8(frame[1:2 + payload_len])

    if crc_received != crc_computed:
        logger.warning(f"CRC mismatch: received {crc_received:#x}, computed {crc_computed:#x}")
        return None

    ns_hash_val = struct.unpack(">H", payload[0:2])[0]
    type_scope = payload[2]
    type_nibble = (type_scope >> 4) & 0x0F
    scope_bits = (type_scope >> 2) & 0x03
    value_bytes = payload[3:]

    ns_name = _reverse_hash.get(ns_hash_val, f"_unknown_{ns_hash_val:#06x}")
    msg_type = message_type_for_wire_code(type_nibble)
    if msg_type is None:
        # An unallocated type code (10..15, or one a newer peer was taught).
        # DROP and say so. Defaulting to STATE — which this line did until
        # 2026-08-10 — manufactures a state change that never happened, and
        # is why an ACK frame silently arrived as a STATE ([BP-TYPE-CODES]).
        logger.warning(
            "UART: frame for ns %s carries unallocated message-type code %d "
            "(allocated 0..9, see [BP-TYPE-CODES]) — DROPPED, not guessed.",
            ns_name, type_nibble,
        )
        return None
    scope = _BITS_TO_SCOPE.get(scope_bits, "auto")
    value = decode_value(value_bytes)

    return BusMessage(
        namespace=ns_name,
        type=msg_type,
        value=value,
        scope=scope,
        source="uart",
    )


class UARTTransport(Transport):
    """UART transport using pyserial for physical CH32V003 communication."""

    def __init__(
        self,
        port: str = "/dev/cu.usbserial-3110",
        baud: int = 115200,
        timeout: float = 0.1,
    ):
        self._port = port
        self._baud = baud
        self._timeout = timeout
        self._serial = None
        self._connected = False
        self._running = False
        self._read_thread: threading.Thread | None = None
        self._subscribers: dict[str, list[Callable[[BusMessage], None]]] = defaultdict(list)
        self._wildcard_subscribers: list[Callable[[BusMessage], None]] = []
        self._rx_buffer = bytearray()

    def send(self, message: BusMessage):
        """Send a bus message as a binary UART frame."""
        if not self._connected or not self._serial:
            return
        frame = encode_message(message)
        try:
            self._serial.write(frame)
        except Exception as e:
            logger.error(f"UART write error: {e}")

    def subscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        register_namespace_hash(namespace)
        self._subscribers[namespace].append(callback)

    def subscribe_all(self, callback: Callable[[BusMessage], None]):
        """Receive EVERY decoded frame on this link ([Transport] ABC).

        Added 2026-08-08. The rx machinery for this already existed and worked —
        `_dispatch` has always fanned out to `_wildcard_subscribers` — but the
        public method that fills that list was never written, so `UARTTransport`
        was ABSTRACT and could not be instantiated at all. That is why UART was
        unreachable from a scene: not "unimplemented", just missing its last
        four lines. `bus._wire_transport_incoming()` calls this to route inbound
        traffic to namespace subscribers, so without it a UART-backed app could
        transmit but never receive.
        """
        self._wildcard_subscribers.append(callback)

    def unsubscribe(self, namespace: str, callback: Callable[[BusMessage], None]):
        if namespace in self._subscribers:
            try:
                self._subscribers[namespace].remove(callback)
            except ValueError:
                pass

    def start(self):
        """Open serial port and start read thread."""
        try:
            import serial
        except ImportError:
            logger.error("pyserial not installed. Run: pip install pyserial")
            return

        try:
            self._serial = serial.Serial(
                self._port, self._baud, timeout=self._timeout
            )
            self._connected = True
            self._running = True
            self._read_thread = threading.Thread(target=self._read_loop, daemon=True)
            self._read_thread.start()
            logger.info(f"UART connected: {self._port} @ {self._baud}")
        except Exception as e:
            logger.error(f"UART open failed: {e}")

    def stop(self):
        """Close serial port."""
        self._running = False
        if self._serial:
            self._serial.close()
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _read_loop(self):
        """Background thread: read bytes, frame, decode, dispatch."""
        while self._running:
            try:
                data = self._serial.read(256)
                if data:
                    self._rx_buffer.extend(data)
                    self._process_buffer()
            except Exception as e:
                if self._running:
                    logger.error(f"UART read error: {e}")
                    time.sleep(0.5)

    def _process_buffer(self):
        """Extract complete frames from the receive buffer."""
        while len(self._rx_buffer) >= 5:
            # Find sync byte
            try:
                sync_idx = self._rx_buffer.index(_SYNC)
            except ValueError:
                self._rx_buffer.clear()
                return

            if sync_idx > 0:
                self._rx_buffer = self._rx_buffer[sync_idx:]

            if len(self._rx_buffer) < 3:
                return

            payload_len = self._rx_buffer[1]
            frame_len = 2 + payload_len + 1  # sync + len + payload + crc

            if len(self._rx_buffer) < frame_len:
                return  # Incomplete frame

            frame = bytes(self._rx_buffer[:frame_len])
            self._rx_buffer = self._rx_buffer[frame_len:]

            msg = decode_message(frame)
            if msg:
                self._dispatch(msg)

    def _dispatch(self, msg: BusMessage):
        """Dispatch decoded message to subscribers."""
        for cb in self._subscribers.get(msg.namespace, []):
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"UART subscriber error: {e}")
        for cb in self._wildcard_subscribers:
            try:
                cb(msg)
            except Exception as e:
                logger.error(f"UART wildcard subscriber error: {e}")
