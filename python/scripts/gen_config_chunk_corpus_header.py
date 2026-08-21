#!/usr/bin/env python3
"""
gen_config_chunk_corpus_header.py — [SPEC:CXC-CONFIG-BUS] chunk PARITY corpus.

The load-bearing claim for the firmware config path is that the frames it
reassembles are the SAME BYTES the head-side chunker produces. A C++ round trip
proves only self-consistency — it passes with the sequence bytes swapped, with a
little-endian total_len, or with a CRC-16 seeded 0x0000 — because both halves
make the same mistake. So the ORACLE is the real Python chunker
(`composeio.config_chunk`), and this script bakes its output.

cyd/src/config_chunk_selftest.cpp (env:native_config_chunk) then:
  - encodes the same transfers through the firmware chunker and compares bytes;
  - feeds the PYTHON frames to the firmware reassembler in order, REVERSED, and
    with a corrupted byte, and checks the verdicts match;
  - checks the CRC-16 content hash against Python's value on binary payloads
    (not just ASCII — the namespace hash only ever sees ASCII, and a
    sign-extension bug in the byte loop hides until a 0x80+ byte arrives).

Covered on purpose:
  - BOTH substrate chunk sizes: 6 (classic CAN — the size the format is
    designed against) and 250 (UART), plus 1 and 250 as edge cases;
  - a payload that divides EXACTLY by the chunk size and one that does not,
    because the final short chunk is where an off-by-one lives;
  - a 6,012 B payload — the real scene size [CXC-CONFIG-BUS-BUDGET] does its
    arithmetic with;
  - an empty payload (the degenerate transfer: header only);
  - TTL decrement including the EXPIRY step.

REGENERATE after any change to the chunk format:
    python3 python/scripts/gen_config_chunk_corpus_header.py
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "python"))

from composeio.config_chunk import (  # noqa: E402
    ACK_BYTES, ACK_HASH_MISMATCH, ACK_INCOMPLETE, ACK_NO_MEMORY, ACK_OK,
    ACK_TAG, CAN_CONTENT_BYTES, HEADER_CONTENT_BYTES, SEQ_BYTES, TTL_SEED,
    UART_CONTENT_BYTES, chunk_config, chunk_count, content_hash, encode_ack,
    encode_header, ttl_decrement,
)

OUT = os.path.join(REPO, "cyd", "test", "config_chunk_corpus_generated.h")


def _payload(n: int, seed: int) -> bytes:
    """Deterministic pseudo-random bytes, deliberately including 0x00 and
    0x80+ values so a sign-extension bug in the CRC loop cannot hide."""
    out = bytearray()
    x = seed & 0xFFFF
    for _ in range(n):
        x = (x * 1103515245 + 12345) & 0xFFFF
        out.append(x >> 8)
    return bytes(out)


# (id, payload, chunk_payload)
CASES = [
    ("empty_uart",      b"",                    UART_CONTENT_BYTES),
    ("one_byte_can",    b"\x5a",                CAN_CONTENT_BYTES),
    ("exact_can",       _payload(60, 1),        CAN_CONTENT_BYTES),   # 10 chunks
    ("ragged_can",      _payload(61, 2),        CAN_CONTENT_BYTES),   # 11, last=1
    # A CAN transfer long enough that a wrong offset multiplier cannot hide
    # (301 chunks, last one ragged). NOT the full 6,012 B scene at 6 B/chunk:
    # that is 1,002 frames and ~300 KB of generated header for arithmetic that
    # is linear in the sequence number and already exercised here. The full
    # scene IS baked at UART size below, and `scene_uart`'s payload is the
    # same bytes, so the content hash is compared over the real length.
    ("many_chunks_can", _payload(1806, 3),      CAN_CONTENT_BYTES),
    ("scene_uart",      _payload(6012, 3),      UART_CONTENT_BYTES),
    ("exact_uart",      _payload(1000, 4),      250),
    ("ragged_uart",     _payload(1001, 5),      250),
    ("single_chunk",    _payload(200, 6),       UART_CONTENT_BYTES),
    ("byte_per_chunk",  _payload(37, 7),        1),
    ("ascii_yaml",      b"title: engine thrust\nwidgets:\n  - type: gauge\n",
                        UART_CONTENT_BYTES),
]

# ACK vectors. (id, status, hash, total_len, ackseq)
#
# The real transfer is in here on purpose: `scene_ok` is the 5,856 B scene whose
# STRING ack ("ok beef 5856", 13 bytes) could not be sent on a substrate that
# carries 8 — the corpus asserts the replacement is 7, in both implementations,
# against the same bytes.
ACK_CASES = [
    ("scene_ok",        ACK_OK,            0xBEEF, 5856, 976),
    ("zero",            ACK_OK,            0x0000, 0,    0),
    ("maxima",          ACK_HASH_MISMATCH, 0xFFFF, 0xFFFF, 0xFFFF),
    ("mismatch_nogap",  ACK_HASH_MISMATCH, 0xCC50, 6012, 0),
    ("incomplete_gap",  ACK_INCOMPLETE,    0x6F62, 5856, 13),
    ("no_memory",       ACK_NO_MEMORY,     0x1234, 60000, 0),
    # Every allocated status, so a firmware switch missing an arm is caught.
    ("status_0",        ACK_OK,            0x00FF, 1,    1),
    ("status_1",        ACK_HASH_MISMATCH, 0x00FF, 1,    1),
    ("status_2",        ACK_INCOMPLETE,    0x00FF, 1,    1),
    ("status_3",        ACK_NO_MEMORY,     0x00FF, 1,    1),
]

# Values that are NOT acks and must be REFUSED, not parsed. The last one is the
# retired text form, which is what an un-reflashed leaf still emits.
ACK_REJECT = [
    ("empty",       b""),
    ("short",       b"\xa0\x00\x00\x00\x00\x00"),
    ("long",        b"\xa0\x00\x00\x00\x00\x00\x00\x00"),
    ("bad_tag",     bytes(ACK_BYTES)),
    ("string_like", b"\x06" + b"abcdef"),
    ("text_ack",    b"ok beef 5856"[:ACK_BYTES]),
]

# Content-hash vectors on their own, so a hash failure is attributable without
# reading a 6 KB frame dump.
HASH_CASES = [
    ("empty",       b""),
    ("nul",         b"\x00"),
    ("high_bit",    b"\x80\xff\x7f"),
    ("ascii",       b"composeio"),
    ("all_bytes",   bytes(range(256))),
    ("scene",       _payload(6012, 3)),
]


def c_bytes(data: bytes, indent: str = "    ") -> str:
    if not data:
        return indent + "0"
    lines = []
    for i in range(0, len(data), 16):
        lines.append(indent + ", ".join(f"0x{b:02X}" for b in data[i:i + 16]))
    return ",\n".join(lines)


def main() -> int:
    out = []
    w = out.append
    w("/* GENERATED by python/scripts/gen_config_chunk_corpus_header.py — "
      "DO NOT EDIT.")
    w(" * Oracle: composeio.config_chunk ([SPEC:CXC-CONFIG-BUS]).")
    w(" * Consumed by cyd/src/config_chunk_selftest.cpp (env:native_config_chunk).")
    w(" */")
    w("#ifndef CONFIG_CHUNK_CORPUS_GENERATED_H")
    w("#define CONFIG_CHUNK_CORPUS_GENERATED_H")
    w("")
    w("#include <cstdint>")
    w("")
    w(f"static const int kCfgSeqBytes = {SEQ_BYTES};")
    w(f"static const int kCfgHeaderContentBytes = {HEADER_CONTENT_BYTES};")
    w(f"static const int kCfgTtlSeed = {TTL_SEED};")
    w(f"static const int kCfgCanContentBytes = {CAN_CONTENT_BYTES};")
    w(f"static const int kCfgUartContentBytes = {UART_CONTENT_BYTES};")
    w(f"static const int kCfgAckBytes = {ACK_BYTES};")
    w(f"static const int kCfgAckTag = 0x{ACK_TAG:02X};")
    w("")

    # ---- ACK vectors
    w("struct CfgAckCase { const char* id; int status; uint16_t hash;")
    w("                    uint16_t total_len; uint16_t ackseq;")
    w("                    const uint8_t* bytes; int len; };")
    for aid, status, chash, total, seq in ACK_CASES:
        w(f"static const uint8_t kCfgAck_{aid}[] = {{")
        w(c_bytes(encode_ack(status, chash, total, seq)))
        w("};")
    w("static const CfgAckCase kCfgAckCases[] = {")
    for aid, status, chash, total, seq in ACK_CASES:
        enc = encode_ack(status, chash, total, seq)
        w(f"    {{ \"{aid}\", {status}, 0x{chash:04X}, {total}, {seq}, "
          f"kCfgAck_{aid}, {len(enc)} }},")
    w("};")
    w(f"static const int kCfgAckCaseCount = {len(ACK_CASES)};")
    w("")
    w("struct CfgAckReject { const char* id; const uint8_t* bytes; int len; };")
    for rid, raw in ACK_REJECT:
        w(f"static const uint8_t kCfgAckBad_{rid}[] = {{")
        w(c_bytes(raw))
        w("};")
    w("static const CfgAckReject kCfgAckRejects[] = {")
    for rid, raw in ACK_REJECT:
        w(f"    {{ \"{rid}\", kCfgAckBad_{rid}, {len(raw)} }},")
    w("};")
    w(f"static const int kCfgAckRejectCount = {len(ACK_REJECT)};")
    w("")

    # ---- content hash vectors
    w("struct CfgHashCase { const char* id; const uint8_t* data; int len; "
      "uint16_t hash; };")
    for cid, data in HASH_CASES:
        w(f"static const uint8_t kCfgHashData_{cid}[] = {{")
        w(c_bytes(data))
        w("};")
    w("static const CfgHashCase kCfgHashCases[] = {")
    for cid, data in HASH_CASES:
        w(f"    {{ \"{cid}\", kCfgHashData_{cid}, {len(data)}, "
          f"0x{content_hash(data):04X} }},")
    w("};")
    w(f"static const int kCfgHashCaseCount = {len(HASH_CASES)};")
    w("")

    # ---- transfers
    w("struct CfgFrame { const uint8_t* bytes; int len; };")
    w("struct CfgTransferCase {")
    w("    const char* id;")
    w("    const uint8_t* payload; int payload_len;")
    w("    int chunk_payload; int chunk_count; uint16_t content_hash;")
    w("    const CfgFrame* frames; int frame_count;   /* [0] is the header */")
    w("};")
    for cid, payload, cp in CASES:
        frames = chunk_config(payload, cp)
        w(f"static const uint8_t kCfgPayload_{cid}[] = {{")
        w(c_bytes(payload))
        w("};")
        for i, f in enumerate(frames):
            w(f"static const uint8_t kCfgFrame_{cid}_{i}[] = {{")
            w(c_bytes(f))
            w("};")
        w(f"static const CfgFrame kCfgFrames_{cid}[] = {{")
        for i, f in enumerate(frames):
            w(f"    {{ kCfgFrame_{cid}_{i}, {len(f)} }},")
        w("};")
    w("static const CfgTransferCase kCfgTransferCases[] = {")
    for cid, payload, cp in CASES:
        frames = chunk_config(payload, cp)
        w(f"    {{ \"{cid}\", kCfgPayload_{cid}, {len(payload)}, {cp}, "
          f"{chunk_count(len(payload), cp)}, 0x{content_hash(payload):04X}, "
          f"kCfgFrames_{cid}, {len(frames)} }},")
    w("};")
    w(f"static const int kCfgTransferCaseCount = {len(CASES)};")
    w("")

    # ---- TTL walk: seed -> ... -> expiry
    w("/* A header frame walked down the TTL ladder by successive relay hops.")
    w(" * The LAST entry is the one where Python returns None == EXPIRED, and")
    w(" * an expiry a relay swallows is the silent depth limit")
    w(" * [CXC-CONFIG-BUS-TTL] exists to remove. */")
    hdr = encode_header(100, CAN_CONTENT_BYTES, 0xA5A5, TTL_SEED)
    ladder = [hdr]
    while True:
        nxt = ttl_decrement(ladder[-1])
        if nxt is None:
            break
        ladder.append(nxt)
    for i, f in enumerate(ladder):
        w(f"static const uint8_t kCfgTtlStep_{i}[] = {{")
        w(c_bytes(f))
        w("};")
    w("static const CfgFrame kCfgTtlLadder[] = {")
    for i, f in enumerate(ladder):
        w(f"    {{ kCfgTtlStep_{i}, {len(f)} }},")
    w("};")
    w(f"static const int kCfgTtlLadderLen = {len(ladder)};")
    w("")
    w("#endif /* CONFIG_CHUNK_CORPUS_GENERATED_H */")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as fh:
        fh.write("\n".join(out) + "\n")
    total = sum(len(chunk_config(p, cp)) for _, p, cp in CASES)
    print(f"wrote {OUT}: {len(CASES)} transfers / {total} frames, "
          f"{len(HASH_CASES)} hash vectors, {len(ACK_CASES)} acks + "
          f"{len(ACK_REJECT)} rejects, TTL ladder {len(ladder)} steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
