#!/usr/bin/env python3
"""
gen_uart_frame_corpus_header.py — UART frame PARITY corpus -> C++ header.
[BP-TRANSPORT-UART] [BP-UART-FRAME] [BP-TYPE-CODES]

The load-bearing claim for the firmware UART transport is that its frames are
the SAME BYTES as the ones python/composeio/transport_uart.py produces — the
frame a CH32V003 already speaks (firmware/ftl_jump_button/src/main.cpp). A
"round-trip test" that encodes and decodes with the same C++ code proves only
self-consistency; it would pass with a wrong CRC polynomial, a byte-swapped
ns_hash or a little-endian type nibble.

So the ORACLE here is the actual Python encoder: this script runs
`transport_uart.encode_message` over a battery of messages and bakes the
resulting FRAME BYTES. cyd/src/bus_frame_selftest.cpp (env:native_frame) then
encodes the same messages through the CYD codec (bus_frame.cpp) and compares
byte-for-byte, decodes the baked Python frames back, and checks the CRC/hash
primitives against their Python values.

Covered on purpose:
  - every allocated message type (0..9), so the type nibble is exercised
    across the whole of [BP-TYPE-CODES], not just STATE;
  - every scope (local/global/remote/auto);
  - every value encoding (none / bool / small int / big int / float / string),
    including a string whose length makes it collide with the 4-byte float
    case — the ambiguity is real and both tiers must resolve it the same way;
  - namespaces whose CRC-16 has a high bit set and one containing '/'.

REGENERATE after any change to the frame layout or the value codec:
    python3 python/scripts/gen_uart_frame_corpus_header.py
(python/tests/test_wire_codes_firmware_parity.py fails if you forget.)
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "python"))

from composeio.config import namespace_hash  # noqa: E402
from composeio.freshness import UNKNOWN, is_unknown  # noqa: E402
from composeio.transport import BusMessage, MessageType  # noqa: E402
from composeio.transport_uart import (  # noqa: E402
    _crc8,
    encode_message,
    encode_value,
    fits_small_int,
)

OUT = os.path.join(REPO, "cyd", "test", "uart_frame_corpus_generated.h")

# (id, namespace, MessageType, scope, value)
CASES = [
    ("state_num_small",  "throttle/left",  MessageType.STATE,  "global", 42),
    ("state_num_zero",   "throttle/right", MessageType.STATE,  "global", 0),
    ("state_num_max",    "throttle/left",  MessageType.STATE,  "global", 255),
    ("state_bool_true",  "demo/alert",     MessageType.STATE,  "global", True),
    ("state_bool_false", "demo/alert",     MessageType.STATE,  "local",  False),
    ("state_float",      "sys/cpu",        MessageType.STATE,  "global", 87.5),
    ("state_float_frac", "game/ftl_ready", MessageType.STATE,  "global", 0.04),
    ("state_int_big",    "game/seq",       MessageType.STATE,  "global", 70000),
    ("state_str",        "game/system",    MessageType.STATE,  "global", "Picon"),
    ("state_str_empty",  "game/order",     MessageType.STATE,  "global", ""),
    # A 3-char string is 4 value bytes — the same width as a float32. Both
    # tiers must resolve that ambiguity identically (decode_value tries the
    # length-prefixed string FIRST).
    ("state_str_3",      "game/vipers",    MessageType.STATE,  "global", "cap"),
    ("state_none",       "game/idle",      MessageType.STATE,  "auto",   None),
    ("event",            "ftl_jump",       MessageType.EVENT,  "global", "jump"),
    ("config_push",      "_device/cfg",    MessageType.CONFIG_PUSH, "remote", 1),
    ("config_request",   "_device/cfg",    MessageType.CONFIG_REQUEST, "auto", None),
    ("heartbeat",        "_device/hb",     MessageType.HEARTBEAT, "global", 1),
    ("discovery",        "_device/disco",  MessageType.DISCOVERY, "global", "wsc6"),
    ("time_sync",        "_device/time",   MessageType.TIME_SYNC, "global", 12345678),
    ("binding",          "_bus/binding",   MessageType.BINDING, "local", 7),
    # The two types every forked copy of the table was MISSING — an ack that
    # arrives as a state is the whole reason [BP-TYPE-CODES] is one table.
    ("ack",              "_bus/ack",       MessageType.ACK,    "global", 1),
    ("log_entry",        "_bus/log",       MessageType.LOG_ENTRY, "global", "boot"),
    # A long-ish string, and a namespace with a high-bit CRC.
    ("state_str_long",   "fun/nightwatch/message", MessageType.STATE, "global",
     "All waves shipped + committed."),
    # ---- [CXC-FRESH-WIRE] — `unknown` and the 253 carve-out -------------
    # ⛔ THESE TWO CASES ARE THE INJECTIVITY CONTROL, and they only mean
    # something TOGETHER.  `unknown` encodes to the single byte 0xFD with a
    # ZERO-length payload; 253 used to encode to the SAME single byte, so the
    # codec was not injective and no decoder could recover the difference.
    # 253 is therefore carved out of the small-int fast path and goes out
    # TAGGED as int32 — four extra bytes for exactly one integer in the whole
    # domain. Baking BOTH here is what makes a firmware regression on either
    # half a FAILING TEST against the Python oracle rather than a divergence
    # somebody notices on a wire months later.
    ("state_unknown",    "dradis/contacts", MessageType.STATE, "global",
     UNKNOWN),
    ("state_int_253",    "dradis/contacts", MessageType.STATE, "global", 253),
    # 252 / 254 / 255 are the NEGATIVE control for the carve-out: they never
    # collided (0xFE/0xFF are discriminated by LENGTH, read as tags only at
    # five bytes) and must keep their single-byte fast path. Widening the
    # carve-out to them would be cargo-culting a fix onto values that were
    # never broken, and only a baked vector can catch that.
    ("state_int_252",    "dradis/contacts", MessageType.STATE, "global", 252),
    ("state_int_254",    "dradis/contacts", MessageType.STATE, "global", 254),
    ("state_int_255b",   "dradis/contacts", MessageType.STATE, "global", 255),
    # A heartbeat namespace, so the frame the freshness receiver's liveness
    # signal rides in is itself pinned to the Python bytes.
    ("heartbeat_uptime", "_device/ctrl-01/heartbeat", MessageType.STATE,
     "global", 42),
]

# Namespaces whose CRC-16/CCITT is checked directly.
HASH_CASES = [
    "throttle/left", "throttle/right", "sys/cpu", "ftl_jump", "ftl_jump_led",
    "_device/wsc6-07f199/restart", "game/system", "a", "",
]

# CRC-8/MAXIM vectors, checked directly.
CRC8_CASES = [b"", b"\x00", b"\x01\x02\x03", bytes(range(16)), b"\xff" * 8]


def cbytes(b: bytes) -> str:
    return "{" + ", ".join("0x%02X" % x for x in b) + "}"


def cstr(s: str) -> str:
    out = '"'
    for ch in s:
        if ch == '"':
            out += '\\"'
        elif ch == "\\":
            out += "\\\\"
        elif 32 <= ord(ch) < 127:
            out += ch
        else:
            out += "\\x%02x" % ord(ch)
    return out + '"'


def main() -> int:
    L: list[str] = []
    w = L.append
    w("/*")
    w(" * uart_frame_corpus_generated.h — AUTO-GENERATED. DO NOT HAND-EDIT.")
    w(" *")
    w(" * Generated by python/scripts/gen_uart_frame_corpus_header.py.  The")
    w(" * ORACLE is composeio.transport_uart.encode_message — the encoder a")
    w(" * live ComposeBus host would use — so these are the exact bytes the")
    w(" * firmware must put on the wire ([BP-UART-FRAME]).")
    w(" *")
    w(" * Driven by cyd/src/bus_frame_selftest.cpp (env:native_frame).")
    w(" */")
    w("")
    w("#ifndef UART_FRAME_CORPUS_GENERATED_H")
    w("#define UART_FRAME_CORPUS_GENERATED_H")
    w("")
    w("#include <cstdint>")
    w("")
    w("/* Value kind as the Python encoder chose it, so the harness can call")
    w(" * the matching bus_value_encode_* entry point:")
    w(" *   0 none, 1 u8 (bool/int 0..255), 2 i32, 3 f32, 4 string,")
    w(" *   5 unknown (tag 0xFD, ZERO-length payload) */")
    w("struct UartFrameCase {")
    w("    const char* id;")
    w("    const char* ns;")
    w("    const char* type_name;   /* MessageType.value */")
    w("    const char* scope;")
    w("    int value_kind;")
    w("    uint8_t u8;              /* value_kind 1 */")
    w("    int32_t i32;             /* value_kind 2 */")
    w("    float f32;               /* value_kind 3 */")
    w("    const char* str;         /* value_kind 4 */")
    w("    const uint8_t* frame;    /* the PYTHON bytes */")
    w("    int frame_len;")
    w("    const uint8_t* value;    /* the PYTHON value bytes */")
    w("    int value_len;")
    w("};")
    w("")

    rows = []
    for i, (cid, ns, mtype, scope, value) in enumerate(CASES):
        msg = BusMessage(namespace=ns, type=mtype, value=value, scope=scope)
        frame = encode_message(msg)
        vbytes = encode_value(value)

        if value is None:
            kind, u8, i32, f32, s = 0, 0, 0, 0.0, ""
        elif is_unknown(value):
            # kind 5: the harness calls bus_value_encode_unknown().
            kind, u8, i32, f32, s = 5, 0, 0, 0.0, ""
        elif isinstance(value, bool):
            kind, u8, i32, f32, s = 1, (1 if value else 0), 0, 0.0, ""
        elif isinstance(value, int):
            # ⛔ ASK THE ENCODER, DO NOT RESTATE ITS RULE.  This branch used to
            # carry its own `0 <= value <= 255` copy, and that copy did NOT gain
            # the 253 carve-out when transport_uart.encode_value did — so the
            # generator could tell the C++ harness "call encode_u8" while the
            # oracle emitted a tagged int32, and the resulting failure would be
            # reported against bus_frame.cpp, which was right. A generator that
            # can disagree with its own oracle is worse than no generator.
            if fits_small_int(value):
                kind, u8, i32, f32, s = 1, value, 0, 0.0, ""
            else:
                kind, u8, i32, f32, s = 2, 0, value, 0.0, ""
        elif isinstance(value, float):
            kind, u8, i32, f32, s = 3, 0, 0, value, ""
        else:
            kind, u8, i32, f32, s = 4, 0, 0, 0.0, value

        w("static const uint8_t k_uf%d_frame[] = %s;" % (i, cbytes(frame)))
        if vbytes:
            w("static const uint8_t k_uf%d_value[] = %s;" % (i, cbytes(vbytes)))
        else:
            w("static const uint8_t k_uf%d_value[1] = {0};" % i)
        rows.append((i, cid, ns, mtype.value, scope, kind, u8, i32, f32, s,
                     len(frame), len(vbytes)))
    w("")
    w("static const UartFrameCase kUartFrameCases[] = {")
    for (i, cid, ns, tname, scope, kind, u8, i32, f32, s, flen,
         vlen) in rows:
        w("    {%s, %s, %s, %s, %d, %d, %d, %rf, %s, k_uf%d_frame, %d, "
          "k_uf%d_value, %d},"
          % (cstr(cid), cstr(ns), cstr(tname), cstr(scope), kind, u8, i32,
             f32, cstr(s), i, flen, i, vlen))
    w("};")
    w("static const int kUartFrameCaseCount = %d;" % len(rows))
    w("")
    w("/* CRC-16/CCITT-FALSE namespace hashes (composeio.config.namespace_hash) */")
    w("struct NsHashCase { const char* ns; uint16_t hash; };")
    w("static const NsHashCase kNsHashCases[] = {")
    for ns in HASH_CASES:
        w("    {%s, 0x%04X}," % (cstr(ns), namespace_hash(ns)))
    w("};")
    w("static const int kNsHashCaseCount = %d;" % len(HASH_CASES))
    w("")
    w("/* CRC-8/MAXIM (transport_uart._crc8) */")
    w("struct Crc8Case { const uint8_t* data; int len; uint8_t crc; };")
    for i, d in enumerate(CRC8_CASES):
        if d:
            w("static const uint8_t k_c8_%d[] = %s;" % (i, cbytes(d)))
        else:
            w("static const uint8_t k_c8_%d[1] = {0};" % i)
    w("static const Crc8Case kCrc8Cases[] = {")
    for i, d in enumerate(CRC8_CASES):
        w("    {k_c8_%d, %d, 0x%02X}," % (i, len(d), _crc8(d)))
    w("};")
    w("static const int kCrc8CaseCount = %d;" % len(CRC8_CASES))
    w("")
    w("#endif /* UART_FRAME_CORPUS_GENERATED_H */")
    w("")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("wrote %s (%d frames)" % (OUT, len(CASES)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
