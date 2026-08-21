/*
 * bus_frame.h — the ComposeIO FRAMED-substrate codec.
 * [BP-TRANSPORT-UART] [BP-UART-FRAME] [BP-TYPE-CODES]
 *
 *   [0xC1][len][ns_hash_hi][ns_hash_lo][type_scope][value...][crc8]
 *
 *   0xC1        sync byte (ComposeIO marker)
 *   len         payload length (after sync, before crc) = 3 + value_len
 *   ns_hash     16-bit CRC-16/CCITT-FALSE of the namespace, BIG-endian
 *   type_scope  bits 7..4 message type, bits 3..2 scope, bits 1..0 reserved
 *   value       0..n bytes, encoding per bus_frame_encode_* below
 *   crc8        CRC-8/MAXIM over [len .. end of value]
 *
 * PORTED, NOT DERIVED.  The reference implementations are
 * python/composeio/transport_uart.py (encode_message / decode_message /
 * encode_value / decode_value, and namespace_hash in composeio/config.py)
 * and firmware/ftl_jump_button/src/main.cpp, which has spoken this exact
 * frame from a CH32V003 since before the spec was written.  The frame is
 * sized for that part's 2 KB SRAM, which is why it is this terse.
 *
 * Every ambiguity below is Python's ambiguity, kept ON PURPOSE so the two
 * tiers agree byte-for-byte: a 1-byte value is a number (a bool encodes as
 * 0/1 and comes back as 0/1), a length-prefixed run that happens to be
 * 4 bytes decodes as a STRING before it decodes as a float.  Changing the
 * rule here without changing transport_uart.py would be a silent fork —
 * env:native_frame compares against vectors generated FROM the Python
 * encoder for exactly that reason.
 *
 * Pure C++ (no ESP-IDF, no Arduino): it host-compiles for the parity
 * harness, same idiom as dsl.h / restart.h.  The ESP-IDF UART driver that
 * wraps it lives in bus_uart.cpp.
 */

#ifndef BUS_FRAME_H
#define BUS_FRAME_H

#include <cstddef>
#include <cstdint>

#include "bus_wire_codes.h"

static constexpr uint8_t kBusFrameSync = 0xC1;

/* Longest value the frame can carry: `len` is one byte and covers
 * ns_hash(2) + type_scope(1) + value. */
static constexpr size_t kBusFrameMaxValue = 255 - 3;
/* Python truncates strings at 200 bytes (transport_uart.encode_value). */
static constexpr size_t kBusFrameMaxString = 200;
static constexpr size_t kBusFrameMaxLen = 2 + 255 + 1;   /* sync+len+payload+crc */
static constexpr size_t kBusFrameMinLen = 5;

/* CRC-16/CCITT-FALSE — composeio.config.namespace_hash.  ASCII namespaces. */
uint16_t bus_ns_hash(const char* ns);

/* CRC-8/MAXIM (Dallas/Maxim 1-Wire) — transport_uart._crc8. */
uint8_t bus_crc8(const uint8_t* data, size_t len);

/* ---- value encoders (mirror transport_uart.encode_value by TYPE) ---- */

/* THE VALUE TYPE TAG — [BP-VALUE-CODEC], closed 2026-08-13.
 * DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9: int32 and float32 both
 * encoded to FOUR BYTES WITH NO DISCRIMINATOR, so every decoder had to
 * guess.  This codec and transport_uart guessed float (1000 came back as
 * 1.4e-42, -5 as nan); can_mqtt_bridge guessed int (42.0 came back as
 * 1109917696).  NEGATIVE INTEGERS WERE WRONG IN BOTH, so "make them agree"
 * was never a fix — it would only have moved which values corrupt.
 *
 * The tag is IN-BAND, one byte, on the wide numeric forms only.  Not the
 * "one spare bit in type_scope" the plan costed: that works for UART and
 * CANNOT work for CAN, whose ratified id sums to exactly 29 bits with none
 * spare, and two different mechanisms on two substrates is the fork this
 * file has already paid for twice.  Small ints (all live throttle traffic
 * is 0..255) and strings are UNCHANGED.
 *
 * A tag can never be mistaken for a string length prefix: strings are
 * truncated at 200 bytes, so 0xFE/0xFF is not a length either tier emits. */
static constexpr uint8_t kBusValueTagInt32 = 0xFE;
static constexpr uint8_t kBusValueTagFloat32 = 0xFF;

/* `unknown` — [DEC-UNKNOWN-IS-A-TYPE-TAG-NOT-A-RESERVED-VALUE],
 * [CXC-FRESH-WIRE].  A ZERO-LENGTH payload, so the whole encoded value is
 * this one byte.  Never a reserved number like -1 or 255: no reserved value
 * is safe in a dynamically-typed value domain.
 *
 * ⛔ AND THAT ZERO LENGTH IS EXACTLY WHY IT NEEDS A CARVE-OUT THE OTHER TWO
 * TAGS DID NOT.  0xFE/0xFF are safe because their discriminator is LENGTH —
 * they are only ever READ as tags at FIVE bytes, so the one-byte values 254
 * and 255 sail through the small-int fast path untouched.  0xFD HAS NO LENGTH
 * TO BE DISCRIMINATED BY.  Measured on the unmodified C++ codec, exactly as
 * on the unmodified Python one: `bus_value_encode_u8(253)` emitted a single
 * byte 0xFD, byte-identical to unknown, and `bus_value_decode` read a 1-byte
 * 0xFD back as the number 253 (the `n == 1` branch).
 *
 * THE FAILURE IS NOT THAT UNKNOWN MIS-DECODES.  It is that THE CODEC STOPS
 * BEING INJECTIVE — two distinct values share one encoding — and no care at
 * the decoder can recover information the encoder threw away.
 *
 * THE FIX IS AT THE ENCODER and it is ONE INTEGER WIDE: 253 leaves the
 * small-int fast path and goes out TAGGED as int32.  Four extra bytes for
 * exactly one value in the entire domain; 0..252 and 254..255 are unchanged,
 * byte for byte.  Widening the carve-out to 254/255 would be cargo-culting a
 * fix onto values that never collided.  Byte-parity with
 * python/composeio/transport_uart.py:VALUE_TAG_UNKNOWN, and env:native_frame
 * proves it against that encoder rather than asserting it. */
static constexpr uint8_t kBusValueTagUnknown = 0xFD;

/* Bytes a tagged numeric occupies: 1 tag + 4.  NAMED, because every call
 * site used to declare `uint8_t v[4]` from the old width, and a literal 4
 * left behind after the tag landed is a one-byte stack overwrite that
 * builds clean and corrupts whatever the compiler put next to it. */
static constexpr size_t kBusValueMaxNumeric = 5;

/* bool -> 1 byte 0/1; int in 0..255 -> 1 byte. */
size_t bus_value_encode_u8(uint8_t v, uint8_t* out);
/* int outside 0..255 -> [0xFE][4-byte LITTLE-endian int32] (struct "<i"). */
size_t bus_value_encode_i32(int32_t v, uint8_t* out);
/* float -> [0xFF][4-byte LITTLE-endian IEEE-754 float32] (struct "<f"). */
size_t bus_value_encode_f32(float v, uint8_t* out);
/* str -> [len][utf8...], truncated at kBusFrameMaxString. */
size_t bus_value_encode_str(const char* s, uint8_t* out, size_t cap);
/* `unknown` -> the single byte [0xFD], zero-length payload.  Always 1. */
size_t bus_value_encode_unknown(uint8_t* out);

/* ---- value decode (mirrors transport_uart.decode_value) ---- */

enum class BusValueKind : uint8_t {
    None,     /* empty value — Python returns None; drop it            */
    Number,   /* 1-byte int, tagged int32, or tagged float32            */
    String,   /* length-prefixed UTF-8                                  */
    Raw,      /* something this codec will not guess at — drop and log  */
    Unknown,  /* an explicit [0xFD] — "alive, and I do not know"        */
};

/* Decodes in PYTHON'S ORDER: a 5-byte TAGGED int32/float32 first (exact,
 * never guessed); then 1 byte -> number; then a length-prefix that exactly
 * spans the rest and is valid UTF-8 -> string; else Raw.
 *
 * AN UNTAGGED 4-BYTE VALUE IS NOW `Raw`, not a guessed float32.  That guess
 * WAS the defect.  A frame from a peer that has not been reflashed is
 * therefore visibly undecodable rather than invisibly wrong — a deliberate
 * FLAG DAY, since the whole fleet moves in one coordinated reflash. */
BusValueKind bus_value_decode(const uint8_t* v, size_t n,
                              double* num, char* str, size_t str_cap);

/* ---- frame ---- */

struct BusFrame {
    uint16_t ns_hash;
    BusMessageType type;
    uint8_t scope_bits;
    const uint8_t* value;      /* points INTO the caller's buffer */
    uint8_t value_len;
};

enum class BusFrameResult : uint8_t {
    Ok,
    Incomplete,       /* need more bytes — keep the buffer                */
    NoSync,           /* no sync byte in the buffer — discard             */
    BadCrc,           /* framed but corrupt — resync past it              */
    UnallocatedType,  /* type code 10..15 — DROP, never guess State       */
};

/* Build a frame.  Returns the total byte count, or 0 when the type has no
 * allocated wire code (see bus_wire_codes.h — the caller must drop, not
 * substitute) or the value/capacity will not fit. */
size_t bus_frame_encode(uint16_t ns_hash, BusMessageType type,
                        uint8_t scope_bits,
                        const uint8_t* value, size_t value_len,
                        uint8_t* out, size_t cap);

/* Decode the FIRST frame in *buf*.
 * *consumed* is always set to how many bytes the caller should drop from
 * the front of its buffer (0 for Incomplete — wait for more). */
BusFrameResult bus_frame_decode(const uint8_t* buf, size_t len,
                                BusFrame* out, size_t* consumed);

#endif /* BUS_FRAME_H */
