#!/usr/bin/env python3
"""config_chunk — the [SPEC:CXC-CONFIG-BUS] chunk format, sized for classic CAN.

A device's scene config travels on `_device/<id>/config` as a `CONFIG_PUSH`
transfer: one HEADER frame, then N sequenced DATA frames, then one `ACK` naming
the content hash the leaf reassembled ([CXC-CONFIG-BUS-CHUNKS]).

THE ONE CONSTRAINT THAT SHAPES THE WHOLE FORMAT ([CXC-CONFIG-BUS-BUDGET]).
Every CAN controller in the fleet is classic CAN 2.0B — eight data bytes, and
the limit is the CONTROLLER, so no purchase changes it. A chunk header carrying
destination + transfer id + sequence + total + TTL is ~7 bytes, which on UART is
3% overhead and on CAN leaves ONE usable byte. So the format is sized for CAN
FIRST and merely happens to be efficient on UART:

    frame value := [seq_hi][seq_lo][content ...]

TWO bytes of per-frame overhead and nothing else, because the NAMESPACE IS THE
ADDRESS ([CXC-CONFIG-BUS-ADDRESSING]) — the 16-bit ns hash already in every
frame (UART payload, CAN arbitration id) names the destination AND the transfer,
so there is no destination field, no transfer id, and no routing table. On CAN
that leaves 6 of 8 bytes for content; on UART, 250 of 252.

    seq == 0  -> HEADER frame, content is exactly 6 bytes:
                   [0]    ttl              seed 8, decremented by each relay
                   [1]    chunk_payload    content bytes per DATA frame (1..250)
                   [2..3] total_len        payload bytes, uint16 BIG-endian
                   [4..5] content_hash     CRC-16/CCITT-FALSE, uint16 BIG-endian
    seq >= 1  -> DATA frame; its content belongs at payload offset
                 (seq - 1) * chunk_payload.

Six bytes exactly, so the header is ONE classic-CAN frame with nothing to spare.

WHY `chunk_payload` AND NOT A LITERAL `chunk_count` FIELD.  [CXC-CONFIG-BUS-
CHUNKS] names four informations the header must carry: total byte length, chunk
count, a content hash, and the TTL seed.  Six bytes admits total_len(2) +
hash(2) + ttl(1) and exactly ONE more byte.  chunk_count is exactly derivable
from (total_len, chunk_payload) — `ceil(total/payload)` — while the converse is
FALSE (25 chunks of a 6,012 B payload implies ceil(6012/25) = 241, not the real
250).  chunk_payload is therefore the strictly more informative of the two, and
carrying it delivers the chunk count as well.  Deriving it instead — "learn the
chunk size from the first data frame" — fails when the first frame to arrive is
the short final one, which is precisely the reordering case the format must
survive.  `chunk_count()` below is the field the spec names; it is computed, not
guessed.

WHY CRC-16 AND NOT CRC-32 FOR THE CONTENT HASH.  The 6-byte ceiling is the
reason there is room for two hash bytes and not four.  The hash is not carrying
the whole load: per-frame CRC-8 catches a corrupt frame, and the reassembler's
received-bitmap catches a MISSING or DUPLICATED frame exactly (not
probabilistically).  What only a whole-payload hash can catch is corruption that
slipped a CRC-8 and two INTERLEAVED transfers, and CRC-16/CCITT-FALSE — already
the fleet's namespace hash, so no new primitive on a 2 KB-class part — answers
both at 1-in-65536.  A leaf that reassembles to the wrong hash MUST discard and
re-request rather than build a scene from a plausible-looking ruin.

THIS MODULE IS THE ORACLE.  cyd/src/config_chunk.cpp is a hand port for the
firmware, and `env:native_config_chunk` compares it against frames baked FROM
this module (python/scripts/gen_config_chunk_corpus_header.py) — the same idiom
as `env:native_frame` and for the same reason: a C++ round trip proves only
self-consistency, and would pass with the seq bytes swapped.

NOT IN HERE: the transport.  These functions produce and consume frame VALUES;
what carries them (a UART frame, a CAN frame, an in-process queue) is decided by
the caller, which is the whole point of [SPEC:CXC-CONFIG-BUS] riding ComposeBus
rather than a substrate.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator, List, Optional, Tuple

#: Bytes of per-DATA-frame overhead. TWO — see the module docstring.
SEQ_BYTES = 2

#: The header frame's content length. SIX, so header + seq == one CAN frame.
HEADER_CONTENT_BYTES = 6

#: Sequence number reserved for the header frame. Data frames are 1..N.
HEADER_SEQ = 0

#: TTL seed. A PROTOCOL CONSTANT ([CXC-CONFIG-BUS-TTL]) — never a deployment
#: setting, never in a scene, never in a descriptor, exactly as IP's TTL is not
#: configured per host. Eight is a loop backstop, NOT a declared depth limit:
#: relaying is transitive and unbounded, and a relay that decrements to zero
#: must ANNOUNCE rather than silently drop.
TTL_SEED = 8

#: Content bytes a classic-CAN frame can carry (8 - SEQ_BYTES). The number the
#: whole format is sized against.
CAN_CONTENT_BYTES = 8 - SEQ_BYTES

#: Content bytes a [BP-UART-FRAME] value can carry (252 - SEQ_BYTES).
UART_CONTENT_BYTES = 252 - SEQ_BYTES

#: total_len is a uint16 in the header, so this is a HARD protocol ceiling.
#: Enforced in code below, not merely documented — a limit any caller can
#: exceed is not a limit.
MAX_PAYLOAD_BYTES = 0xFFFF


class ConfigChunkError(ValueError):
    """A payload or frame this format cannot represent. Always loud."""


def content_hash(data: bytes) -> int:
    """CRC-16/CCITT-FALSE over arbitrary BYTES.

    The same algorithm as `composeio.config.namespace_hash`, which only accepts
    an ASCII `str`. Deliberately not a new hash: one polynomial across ns
    hashing, config content and the firmware's `bus_ns_hash` means one table of
    test vectors and one implementation to get wrong.
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def chunk_count(total_len: int, chunk_payload: int) -> int:
    """The `chunk count` [CXC-CONFIG-BUS-CHUNKS] names — DERIVED, not carried.

    See the module docstring: the header has room for exactly one of
    chunk_count and chunk_payload, and only chunk_payload determines the other.
    """
    if chunk_payload <= 0:
        raise ConfigChunkError("chunk_payload must be >= 1")
    if total_len == 0:
        return 0
    return math.ceil(total_len / chunk_payload)


def encode_header(total_len: int, chunk_payload: int, chash: int,
                  ttl: int = TTL_SEED) -> bytes:
    """The seq-0 HEADER frame value (8 bytes: seq(2) + 6 content)."""
    if not (0 <= total_len <= MAX_PAYLOAD_BYTES):
        raise ConfigChunkError(
            f"config payload is {total_len} B; the header's total_len is a "
            f"uint16 so {MAX_PAYLOAD_BYTES} B is a HARD protocol ceiling. "
            "Refusing to send a transfer whose length cannot be stated.")
    if not (1 <= chunk_payload <= 250):
        raise ConfigChunkError(
            f"chunk_payload {chunk_payload} out of range 1..250 (it is a "
            "single header byte, and [BP-UART-FRAME] caps a value at 252)")
    if not (0 <= ttl <= 255):
        raise ConfigChunkError(f"ttl {ttl} out of range 0..255")
    return bytes([
        HEADER_SEQ >> 8, HEADER_SEQ & 0xFF,
        ttl & 0xFF,
        chunk_payload & 0xFF,
        (total_len >> 8) & 0xFF, total_len & 0xFF,
        (chash >> 8) & 0xFF, chash & 0xFF,
    ])


def encode_data(seq: int, content: bytes) -> bytes:
    """One DATA frame value: [seq(2)][content...]. seq is 1-based."""
    if not (1 <= seq <= 0xFFFF):
        raise ConfigChunkError(f"data sequence {seq} out of range 1..65535 "
                               "(0 is reserved for the header frame)")
    return bytes([(seq >> 8) & 0xFF, seq & 0xFF]) + bytes(content)


def chunk_config(payload: bytes, chunk_payload: int = UART_CONTENT_BYTES,
                 ttl: int = TTL_SEED) -> List[bytes]:
    """Split `payload` into frame VALUES: the header, then the data frames.

    `chunk_payload` is set by the SUBSTRATE the caller is about to use
    (`CAN_CONTENT_BYTES` / `UART_CONTENT_BYTES`), never by the format — that
    separation is what lets the same transfer format run on both.
    """
    payload = bytes(payload)
    total = len(payload)
    chash = content_hash(payload)
    out = [encode_header(total, chunk_payload, chash, ttl)]
    for i in range(chunk_count(total, chunk_payload)):
        off = i * chunk_payload
        out.append(encode_data(i + 1, payload[off:off + chunk_payload]))
    return out


def parse_frame(value: bytes) -> Tuple[int, bytes]:
    """(seq, content) for any config frame value. Raises on a runt."""
    if len(value) < SEQ_BYTES:
        raise ConfigChunkError(
            f"config frame value is {len(value)} B — every frame carries a "
            f"{SEQ_BYTES}-byte sequence")
    return ((value[0] << 8) | value[1]), bytes(value[SEQ_BYTES:])


def parse_header(value: bytes) -> Tuple[int, int, int, int]:
    """(ttl, chunk_payload, total_len, content_hash) from a seq-0 frame."""
    seq, content = parse_frame(value)
    if seq != HEADER_SEQ:
        raise ConfigChunkError(f"not a header frame (seq {seq})")
    if len(content) != HEADER_CONTENT_BYTES:
        raise ConfigChunkError(
            f"header content is {len(content)} B, must be exactly "
            f"{HEADER_CONTENT_BYTES}")
    return (content[0], content[1],
            (content[2] << 8) | content[3],
            (content[4] << 8) | content[5])


def ttl_decrement(value: bytes) -> Optional[bytes]:
    """Relay hop on a HEADER frame ([CXC-CONFIG-BUS-TTL]).

    Returns the rewritten header, or None when the TTL has EXPIRED — and an
    expiry the caller swallows is the silent depth limit this design was
    corrected to remove, so every caller must ANNOUNCE. Non-header frames are
    returned unchanged: only the header carries the TTL, and a relay forwards
    data frames verbatim without parsing them ([CXC-CONFIG-BUS-RELAY]).
    """
    seq, content = parse_frame(value)
    if seq != HEADER_SEQ:
        return bytes(value)
    if len(content) != HEADER_CONTENT_BYTES:
        raise ConfigChunkError("malformed header frame")
    if content[0] <= 1:
        return None
    return bytes(value[:SEQ_BYTES]) + bytes([content[0] - 1]) + bytes(content[1:])


class Reassembler:
    """Endpoint-side reassembly. ONLY the endpoint reassembles — a relay is a
    dumb repeater for this artifact class ([CXC-CONFIG-BUS-RELAY]).

    ONE transfer at a time: a header frame arriving mid-transfer ABANDONS the
    old one ([CXC-CONFIG-BUS-CHUNKS] interleaving). That is cheaper than
    transfer ids and sufficient, because a leaf has exactly one scene.
    """

    #: on_frame outcomes
    IGNORED = "ignored"          # not a config frame we can use
    STARTED = "started"          # header accepted, buffer sized
    RESTARTED = "restarted"      # header mid-transfer: old one abandoned
    PROGRESS = "progress"        # data chunk stored
    DUPLICATE = "duplicate"      # already had this sequence
    COMPLETE = "complete"        # all chunks in AND the hash matches
    HASH_MISMATCH = "mismatch"   # all chunks in and the hash does NOT match
    STRAY = "stray"              # data frame with no header

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.ttl = 0
        self.chunk_payload = 0
        self.total_len = 0
        self.expect_hash = 0
        self.count = 0
        self._buf = bytearray()
        self._have: List[bool] = []
        self.received = 0
        self.payload: Optional[bytes] = None
        self.actual_hash: Optional[int] = None

    @property
    def active(self) -> bool:
        return self.count > 0 and self.payload is None

    def on_frame(self, value: bytes) -> str:
        try:
            seq, content = parse_frame(value)
        except ConfigChunkError:
            return self.IGNORED

        if seq == HEADER_SEQ:
            restarted = self.active
            ttl, cp, total, chash = parse_header(value)
            self.reset()
            self.ttl, self.chunk_payload = ttl, cp
            self.total_len, self.expect_hash = total, chash
            self.count = chunk_count(total, cp)
            self._buf = bytearray(total)
            self._have = [False] * self.count
            if self.count == 0:               # a zero-length config
                return self._finish()
            return self.RESTARTED if restarted else self.STARTED

        if self.count == 0:
            return self.STRAY
        if seq > self.count:
            return self.STRAY
        if self._have[seq - 1]:
            return self.DUPLICATE

        off = (seq - 1) * self.chunk_payload
        self._buf[off:off + len(content)] = content[:max(0, self.total_len - off)]
        self._have[seq - 1] = True
        self.received += 1
        if self.received < self.count:
            return self.PROGRESS
        return self._finish()

    def _finish(self) -> str:
        data = bytes(self._buf)
        self.actual_hash = content_hash(data)
        if self.actual_hash != self.expect_hash:
            # DISCARD. Never build a scene from a plausible-looking ruin.
            self.payload = None
            return self.HASH_MISMATCH
        self.payload = data
        return self.COMPLETE

    def missing(self) -> Iterator[int]:
        for i, got in enumerate(self._have):
            if not got:
                yield i + 1

    def first_missing(self) -> int:
        """The first sequence number NOT received, or 0 when none is missing.

        This is `ackseq` for every non-OK status — the field a future go-back-N
        repair restarts from. It is the first caller `missing()` has ever had;
        the iterator existed unused since the format was written, which is how a
        transfer that lost one of 977 frames could only ever be recovered whole.
        """
        return next(self.missing(), 0)

    def ack_seq(self) -> int:
        """The `ackseq` this reassembler's CURRENT state should report.

        ONE rule, stated once and ported verbatim to the firmware
        (`ConfigReassembler::ack_seq()`): on a complete transfer it is the LAST
        correctly received sequence (== the chunk count); otherwise it is the
        FIRST MISSING sequence, and 0 means "nothing is missing" — which on a
        hash mismatch is real information, because it distinguishes corruption
        (every frame arrived, the bytes are wrong) from loss (a frame never
        came). A repair that cannot tell those apart retransmits the wrong thing.
        """
        if self.payload is not None:
            return self.count
        return self.first_missing()


# --------------------------------------------------------------------------- #
# The ACK  [CXC-CONFIG-BUS-CHUNKS]                                             #
# --------------------------------------------------------------------------- #
# BINARY, 7 BYTES, AND THAT IS THE WHOLE POINT. The ACK used to be a formatted
# string — `"ok %04x %u"`, which for the proven 5,856 B scene is `"ok beef 5856"`
# = 13 bytes, and the failure ACK `"mismatch beef want cc50"` = 24. A classic CAN
# frame carries EIGHT data bytes and `bus_can.cpp` refuses anything longer, so
# BOTH forms were PHYSICALLY UNSENDABLE on CAN: the leaf applied its scene, the
# head learned nothing, and `ConfigServer.acks_ok`/`acks_bad` were structurally
# incapable of incrementing. An instrument that cannot report is not an
# instrument — the same class of defect as the bridge's `filtered` counter that
# was pinned at zero by where it was placed.
#
#     [0]     0xA0 | status      tag(4) | status(4)
#     [1..2]  hash               uint16 BE — the TRANSFER's announced hash
#     [3..4]  total_len          uint16 BE — payload bytes of that transfer
#     [5..6]  ackseq             uint16 BE — see `Reassembler.ack_seq`
#
# Seven bytes, so it fits a classic frame WITH A BYTE SPARE, and every field is
# machine-comparable — which is what makes [D2] possible at all: the head can
# check the returned hash against the one it sent instead of trusting the ASCII
# letters "ok".
#
# WHY A TAGGED FIRST BYTE AND NOT A BARE STATUS. The proposed layout was
# `[status u8][hash][len][ackseq]`. It fits, but it is not SELF-IDENTIFYING, and
# it collides with the value codec: `transport_uart.decode_value` reads a
# leading byte of N in an (N+1)-byte value as a length-prefixed string, so a
# 7-byte value whose first byte were 6 would come back as a str, not bytes. With
# the high nibble pinned to 0xA the first byte is always >= 0xA0 > 6, so no
# status this format can ever allocate is decodable as a string, the value is
# never confusable with the `[0xFE]`/`[0xFF]` tagged numerics ([BP-VALUE-CODEC]
# only reads those at length 5), and a receiver can POSITIVELY IDENTIFY a config
# ACK rather than parsing whatever seven bytes arrived on the namespace. The tag
# costs zero bytes — status needs 4 bits and four are allocated.
#
# WHY `hash` ALWAYS NAMES THE TRANSFER, NEVER THE CORRUPT VALUE. On a mismatch
# the leaf knows two hashes: what it was told to expect and what it computed.
# The field carries the EXPECTED one, because the head's question is "which of
# my transfers is this about?" and a uniform answer is what lets one comparison
# cover every status. The computed hash is a leaf-side diagnosis and stays in
# the leaf's log; spending a wire field on a number the head cannot act on is
# how 8 bytes becomes 9.

#: Bytes on the wire. Seven — one spare in a classic CAN frame.
ACK_BYTES = 7

#: High nibble of byte 0. Not a version field: a second ACK format would be a
#: second status, not a second tag.
ACK_TAG = 0xA0
ACK_TAG_MASK = 0xF0
ACK_STATUS_MASK = 0x0F

ACK_OK = 0              #: reassembled, hash verified, scene applied
ACK_HASH_MISMATCH = 1   #: every frame in, whole-payload hash wrong — DISCARDED
ACK_INCOMPLETE = 2      #: the transfer was abandoned before it completed
ACK_NO_MEMORY = 3       #: could not buffer it — the heap, not the wire

ACK_STATUS_NAMES = {
    ACK_OK: "ok",
    ACK_HASH_MISMATCH: "hash-mismatch",
    ACK_INCOMPLETE: "incomplete",
    ACK_NO_MEMORY: "no-memory",
}


class ConfigAckError(ConfigChunkError):
    """A value on a config namespace that is not a decodable ACK. Always loud.

    Raised rather than returned as None because the head MUST count it: an ACK
    it cannot read is a failed transfer it has not been told about, and the
    silence around that was the whole defect.
    """


@dataclass(frozen=True)
class ConfigAck:
    """A decoded ACK. `ok` is a STATUS, never an inference from a prefix."""
    status: int
    hash: int
    total_len: int
    ackseq: int

    @property
    def ok(self) -> bool:
        return self.status == ACK_OK

    @property
    def status_name(self) -> str:
        return ACK_STATUS_NAMES.get(self.status, f"unallocated({self.status})")

    def __str__(self) -> str:
        return (f"{self.status_name} hash={self.hash:04x} "
                f"len={self.total_len} ackseq={self.ackseq}")


def encode_ack(status: int, chash: int, total_len: int,
               ackseq: int = 0) -> bytes:
    """Build the 7-byte ACK value. Refuses out-of-range fields, never masks.

    Masking is how two different failures become one indistinguishable report:
    a status of 17 masked to 1 would claim a hash mismatch that never happened.
    """
    if not (0 <= status <= ACK_STATUS_MASK):
        raise ConfigChunkError(
            f"ack status {status} is outside 0..{ACK_STATUS_MASK}; it is the "
            f"low nibble of the tag byte and is NOT masked here, because a "
            f"masked status reports a failure that did not occur")
    for name, v in (("hash", chash), ("total_len", total_len),
                    ("ackseq", ackseq)):
        if not (0 <= v <= 0xFFFF):
            raise ConfigChunkError(
                f"ack {name} {v} is outside 0..65535 (uint16 on the wire)")
    return bytes([
        ACK_TAG | (status & ACK_STATUS_MASK),
        (chash >> 8) & 0xFF, chash & 0xFF,
        (total_len >> 8) & 0xFF, total_len & 0xFF,
        (ackseq >> 8) & 0xFF, ackseq & 0xFF,
    ])


def ack_ok(chash: int, total_len: int, ackseq: int = 0) -> bytes:
    """The ACK a leaf publishes on success ([CXC-CONFIG-BUS-CHUNKS]).

    Returns BYTES now, not a string. The string form was unsendable on the one
    substrate the config bus exists for.
    """
    return encode_ack(ACK_OK, chash, total_len, ackseq)


def ack_mismatch(want: int, total_len: int, ackseq: int = 0) -> bytes:
    """The ACK a leaf publishes when the whole-payload hash does not match.

    `want` is the hash the HEAD ANNOUNCED — see the block comment: the field
    names the transfer, so the head's check is the same for every status.
    """
    return encode_ack(ACK_HASH_MISMATCH, want, total_len, ackseq)


def decode_ack(value) -> ConfigAck:
    """Decode an ACK value. Raises `ConfigAckError` on anything else.

    Accepts the raw `bytes` a framed substrate delivers. A `str` is refused with
    the diagnosis spelled out, because the one string it will realistically ever
    see is the RETIRED text ACK from a leaf nobody reflashed — and "an old peer"
    is a different fault from "a corrupt frame".
    """
    if isinstance(value, str):
        raise ConfigAckError(
            f"ACK is the RETIRED TEXT form {value!r}. That leaf is running "
            f"firmware from before the binary ACK: the text form is "
            f"{len(value)} bytes and classic CAN carries 8, so it could never "
            f"be acknowledged over CAN at all. REFLASH the leaf; this transfer "
            f"is NOT confirmed.")
    if value is None:
        raise ConfigAckError(
            "ACK carries no value; a config ACK is "
            f"{ACK_BYTES} bytes ([status|tag][hash u16][len u16][ackseq u16])")
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise ConfigAckError(
            f"ACK value is {type(value).__name__}, not bytes — a config ACK is "
            f"{ACK_BYTES} opaque bytes and this one has been decoded as "
            f"something else somewhere upstream")
    raw = bytes(value)
    if len(raw) != ACK_BYTES:
        raise ConfigAckError(
            f"ACK is {len(raw)} bytes, must be exactly {ACK_BYTES}: "
            f"{raw.hex()}")
    if (raw[0] & ACK_TAG_MASK) != ACK_TAG:
        raise ConfigAckError(
            f"ACK tag byte is 0x{raw[0]:02X}; a config ACK's high nibble is "
            f"0x{ACK_TAG:02X} ([CXC-CONFIG-BUS-CHUNKS]). This value is seven "
            f"bytes of something else: {raw.hex()}")
    status = raw[0] & ACK_STATUS_MASK
    if status not in ACK_STATUS_NAMES:
        # NOT an error — a newer leaf may report a status this build has not
        # been taught, and dropping it would re-create the silence. It is
        # carried through and is NOT `ok`, which is the safe direction.
        pass
    return ConfigAck(status=status,
                     hash=(raw[1] << 8) | raw[2],
                     total_len=(raw[3] << 8) | raw[4],
                     ackseq=(raw[5] << 8) | raw[6])


def _selftest() -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # (1) the header frame is EXACTLY one classic-CAN frame.
    h = encode_header(6012, CAN_CONTENT_BYTES, 0xBEEF)
    check(len(h) == 8, f"(1) header frame is {len(h)} B, must be 8 (one CAN frame)")

    # (2) round trip over BOTH substrate sizes, same payload, same hash.
    payload = bytes((i * 7 + 3) & 0xFF for i in range(6012))
    for cp in (CAN_CONTENT_BYTES, UART_CONTENT_BYTES, 1, 250):
        frames = chunk_config(payload, cp)
        r = Reassembler()
        res = [r.on_frame(f) for f in frames]
        check(res[-1] == Reassembler.COMPLETE,
              f"(2) cp={cp}: last frame -> {res[-1]}")
        check(r.payload == payload, f"(2b) cp={cp}: payload differs")
        check(len(frames) - 1 == chunk_count(len(payload), cp),
              f"(2c) cp={cp}: frame count vs chunk_count")

    # (3) REORDERED delivery reassembles — offsets come from the header, not
    #     from arrival order.
    frames = chunk_config(payload, UART_CONTENT_BYTES)
    r = Reassembler()
    r.on_frame(frames[0])
    for f in reversed(frames[1:]):
        last = r.on_frame(f)
    check(last == Reassembler.COMPLETE, f"(3) reversed order -> {last}")
    check(r.payload == payload, "(3b) reversed order payload differs")

    # (4) a MISSING frame never completes; a CORRUPTED one fails the hash.
    r = Reassembler()
    r.on_frame(frames[0])
    for f in frames[1:-1]:
        r.on_frame(f)
    check(r.payload is None, "(4) a transfer missing its last chunk completed")

    bad = list(frames)
    corrupt = bytearray(bad[3])
    corrupt[5] ^= 0xFF
    bad[3] = bytes(corrupt)
    r = Reassembler()
    out = [r.on_frame(f) for f in bad]
    check(out[-1] == Reassembler.HASH_MISMATCH,
          f"(4b) corrupted chunk -> {out[-1]}, must be a hash mismatch")
    check(r.payload is None, "(4c) a hash mismatch must DISCARD the payload")

    # (5) a DUPLICATE frame is reported, never double-counted.
    r = Reassembler()
    r.on_frame(frames[0])
    r.on_frame(frames[1])
    check(r.on_frame(frames[1]) == Reassembler.DUPLICATE, "(5) duplicate detect")

    # (6) a header mid-transfer ABANDONS the old transfer.
    r = Reassembler()
    r.on_frame(frames[0])
    r.on_frame(frames[1])
    check(r.on_frame(frames[0]) == Reassembler.RESTARTED, "(6) restart on header")
    check(r.received == 0, "(6b) restart must clear progress")

    # (7) TTL decrements on the header and EXPIRES loudly (None), and data
    #     frames pass through untouched.
    v = encode_header(10, 6, 0x1234, ttl=2)
    v2 = ttl_decrement(v)
    check(v2 is not None and parse_header(v2)[0] == 1, "(7) ttl 2 -> 1")
    check(ttl_decrement(v2) is None, "(7b) ttl 1 -> EXPIRED (None)")
    d = encode_data(4, b"abcdef")
    check(ttl_decrement(d) == d, "(7c) data frames are forwarded verbatim")

    # (8) an oversize payload is REFUSED, not truncated.
    try:
        encode_header(MAX_PAYLOAD_BYTES + 1, 250, 0)
        fails.append("(8) an oversize total_len was accepted")
    except ConfigChunkError:
        pass

    # (9) a zero-length payload is a legal (degenerate) transfer.
    r = Reassembler()
    check(r.on_frame(chunk_config(b"", 250)[0]) == Reassembler.COMPLETE,
          "(9) empty payload should complete on the header alone")

    # (10) THE ACK FITS A CLASSIC CAN FRAME. The defect this format replaced
    #      was 13 bytes on success and 24 on failure, and the firmware's CAN
    #      send path refuses anything over 8 — so neither could ever be sent.
    a = ack_ok(0xBEEF, 5856, 976)
    check(len(a) == ACK_BYTES and len(a) <= 8,
          f"(10) ack is {len(a)} B; a classic CAN frame carries 8")
    d = decode_ack(a)
    check(d.ok and d.hash == 0xBEEF and d.total_len == 5856 and d.ackseq == 976,
          f"(10b) ack round trip: {d}")

    # (11) every allocated status round-trips, and NONE of them is decodable as
    #      a length-prefixed string by the value codec (the reason for the tag).
    from composeio.transport_uart import decode_value
    for st in sorted(ACK_STATUS_NAMES):
        v = encode_ack(st, 0xCC50, 42, 7)
        got = decode_ack(v)
        check(got.status == st and got.ok == (st == ACK_OK),
              f"(11) status {st} round trip -> {got}")
        check(isinstance(decode_value(v), (bytes, bytearray)),
              f"(11b) status {st}: the value codec turned the ACK into "
              f"{type(decode_value(v)).__name__}, not bytes")

    # (12) the RETIRED text ack is REFUSED, and says why.
    try:
        decode_ack("ok beef 5856")
        fails.append("(12) the retired text ACK was accepted")
    except ConfigAckError as e:
        check("REFLASH" in str(e), f"(12b) text-ack diagnosis is vague: {e}")

    # (13) a 7-byte value that is not an ACK is refused, not parsed.
    for bad in (b"", b"\xa0\x00", bytes(7), b"\x06" + b"abcdef"):
        try:
            decode_ack(bad)
            fails.append(f"(13) non-ack {bad!r} decoded as an ACK")
        except ConfigAckError:
            pass

    # (14) ackseq: complete -> the LAST sequence; a gap -> the FIRST missing.
    frames = chunk_config(payload, UART_CONTENT_BYTES)
    r = Reassembler()
    for f in frames:
        r.on_frame(f)
    check(r.ack_seq() == len(frames) - 1,
          f"(14) complete transfer ack_seq {r.ack_seq()}, expected "
          f"{len(frames) - 1}")
    r = Reassembler()
    r.on_frame(frames[0])
    for f in frames[1:]:
        if f is not frames[3]:
            r.on_frame(f)
    check(r.ack_seq() == 3 and r.first_missing() == 3,
          f"(14b) a gap at 3 reported ack_seq {r.ack_seq()}")
    # A hash mismatch has NO gap — every frame arrived and the bytes are wrong.
    bad_frames = list(frames)
    _c = bytearray(bad_frames[3])
    _c[5] ^= 0xFF
    bad_frames[3] = bytes(_c)
    r = Reassembler()
    out = [r.on_frame(f) for f in bad_frames]
    check(out[-1] == Reassembler.HASH_MISMATCH and r.ack_seq() == 0,
          f"(14c) corrupted transfer ack_seq {r.ack_seq()}, expected 0 "
          f"(nothing missing — corruption, not loss)")

    if fails:
        for m in fails:
            print(f"FAIL {m}")
        return 1
    print("PASS: config_chunk selftest "
          f"(header {HEADER_CONTENT_BYTES + SEQ_BYTES} B = 1 CAN frame; "
          f"CAN {CAN_CONTENT_BYTES} B/chunk, UART {UART_CONTENT_BYTES} B/chunk)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
