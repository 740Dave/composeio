/*
 * bus_frame_selftest.cpp — host PARITY harness for the UART frame codec.
 * [BP-TRANSPORT-UART] [BP-UART-FRAME] [BP-TYPE-CODES]
 *
 * Pure host test (no hardware, no ESP-IDF) — same guarded-file idiom as
 * dsl_selftest.cpp / restart_selftest.cpp; the body is behind
 * FRAME_CORPUS_TEST, defined only by [env:native_frame].
 *
 *   pio run -e native_frame && .pio/build/native_frame/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHY IT IS A PARITY TEST AND NOT A ROUND-TRIP TEST.  Encoding and then
 * decoding with the same code proves only self-consistency: it passes with
 * a wrong CRC polynomial, a byte-swapped ns_hash or a mis-shifted type
 * nibble, because both halves make the same mistake.  The oracle here is
 * the real Python encoder (composeio.transport_uart.encode_message), baked
 * into cyd/test/uart_frame_corpus_generated.h, so what is checked is
 * "these are the bytes a live ComposeBus host would put on the wire".
 *
 * WHAT THIS CANNOT PROVE (say it plainly rather than let a green line
 * imply it): there is no upstream ComposeBus host attached to any board's
 * UART, so the TRANSPORT has never carried a live scene.  This covers the
 * codec — frames, CRCs, namespace hashes, value encodings and the
 * no-silent-fallback rules — and nothing about wires, levels or timing.
 */

#ifdef FRAME_CORPUS_TEST

#include "bus_frame.h"
#include "uart_frame_corpus_generated.h"

#include <cstdio>
#include <cstring>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

static void hexdump(const char* label, const uint8_t* p, int n) {
    printf("      %s:", label);
    for (int i = 0; i < n; i++) printf(" %02X", p[i]);
    printf("\n");
}

int main(void) {
    printf("UART frame parity: composeio.transport_uart (oracle) vs "
           "cyd/src/bus_frame.cpp\n");

    /* ---- primitives ---------------------------------------------- */
    for (int i = 0; i < kNsHashCaseCount; i++) {
        uint16_t got = bus_ns_hash(kNsHashCases[i].ns);
        CHECK(got == kNsHashCases[i].hash,
              "ns_hash(\"%s\"): Python 0x%04X, C++ 0x%04X",
              kNsHashCases[i].ns, kNsHashCases[i].hash, got);
    }
    for (int i = 0; i < kCrc8CaseCount; i++) {
        uint8_t got = bus_crc8(kCrc8Cases[i].data, (size_t)kCrc8Cases[i].len);
        CHECK(got == kCrc8Cases[i].crc,
              "crc8 case %d (%d bytes): Python 0x%02X, C++ 0x%02X", i,
              kCrc8Cases[i].len, kCrc8Cases[i].crc, got);
    }

    /* ---- value encodings ----------------------------------------- */
    for (int i = 0; i < kUartFrameCaseCount; i++) {
        const UartFrameCase& c = kUartFrameCases[i];
        uint8_t v[256];
        size_t n = 0;
        switch (c.value_kind) {
            case 0: n = 0; break;
            case 1: n = bus_value_encode_u8(c.u8, v); break;
            case 2: n = bus_value_encode_i32(c.i32, v); break;
            case 3: n = bus_value_encode_f32(c.f32, v); break;
            case 4: n = bus_value_encode_str(c.str, v, sizeof(v)); break;
            case 5: n = bus_value_encode_unknown(v); break;
        }
        bool ok = (int)n == c.value_len &&
                  (n == 0 || memcmp(v, c.value, n) == 0);
        CHECK(ok, "%s: value bytes differ (Python %d, C++ %u)", c.id,
              c.value_len, (unsigned)n);
        if (!ok) {
            hexdump("python", c.value, c.value_len);
            hexdump("c++   ", v, (int)n);
        }
    }

    /* ---- the 253 carve-out, THROUGH THE u8 ENTRY POINT ------------
     *
     * ⚠ THIS EXISTS BECAUSE THE GENERATOR STOPPED COVERING IT.  While the
     * generator restated the encoder's rule it classified 253 as "small int",
     * so the loop above called bus_value_encode_u8(253) and the C++ carve-out
     * was checked against Python for free.  Making the generator ASK the
     * encoder (the correct fix — a restated rule can disagree with its oracle)
     * reclassified 253 as a tagged int32, and the u8 path quietly lost its
     * parity coverage.  A correctness fix that silently removes a test is how
     * a suite rots while staying green, so the case is pinned here explicitly.
     *
     * `bus_value_encode_u8(253)` MUST emit exactly the bytes Python emits for
     * the integer 253, which are the tagged-int32 five and NOT a bare 0xFD. */
    {
        const UartFrameCase* c253 = nullptr;
        for (int i = 0; i < kUartFrameCaseCount; i++)
            if (strcmp(kUartFrameCases[i].id, "state_int_253") == 0)
                c253 = &kUartFrameCases[i];
        CHECK(c253 != nullptr, "the 253 corpus case is missing entirely");
        if (c253) {
            uint8_t v[kBusValueMaxNumeric];
            size_t n = bus_value_encode_u8(253, v);
            bool ok = (int)n == c253->value_len &&
                      memcmp(v, c253->value, n) == 0;
            CHECK(ok, "bus_value_encode_u8(253): Python %d bytes, C++ %u — the "
                  "carve-out is missing and the codec is NOT INJECTIVE",
                  c253->value_len, (unsigned)n);
            if (!ok) {
                hexdump("python", c253->value, c253->value_len);
                hexdump("c++   ", v, (int)n);
            }
            /* And the neighbours it must NOT have taken with it. */
            uint8_t w[kBusValueMaxNumeric];
            CHECK(bus_value_encode_u8(252, w) == 1 && w[0] == 252,
                  "252 lost its single-byte fast path — it never collided");
            CHECK(bus_value_encode_u8(254, w) == 1 && w[0] == 254,
                  "254 was carve-out'd — cargo-culting onto a value that "
                  "never collided (0xFE is discriminated by LENGTH)");
            CHECK(bus_value_encode_u8(255, w) == 1 && w[0] == 255,
                  "255 was carve-out'd — it never collided");
        }
    }

    /* ---- whole frames -------------------------------------------- */
    for (int i = 0; i < kUartFrameCaseCount; i++) {
        const UartFrameCase& c = kUartFrameCases[i];

        BusMessageType t;
        CHECK(bus_message_type_from_name(c.type_name, &t),
              "%s: C++ knows type '%s'", c.id, c.type_name);
        if (!bus_message_type_from_name(c.type_name, &t)) continue;

        uint8_t frame[kBusFrameMaxLen];
        size_t n = bus_frame_encode(bus_ns_hash(c.ns), t,
                                    bus_scope_bits_for_name(c.scope),
                                    c.value_len ? c.value : nullptr,
                                    (size_t)c.value_len,
                                    frame, sizeof(frame));
        bool ok = (int)n == c.frame_len && memcmp(frame, c.frame, n) == 0;
        CHECK(ok, "%s: frame differs (Python %d bytes, C++ %u)", c.id,
              c.frame_len, (unsigned)n);
        if (!ok) {
            hexdump("python", c.frame, c.frame_len);
            hexdump("c++   ", frame, (int)n);
        }

        /* ...and the PYTHON bytes decode correctly on the firmware side. */
        BusFrame f;
        size_t used = 0;
        BusFrameResult r = bus_frame_decode(c.frame, (size_t)c.frame_len,
                                            &f, &used);
        CHECK(r == BusFrameResult::Ok, "%s: Python frame decodes Ok", c.id);
        if (r != BusFrameResult::Ok) continue;
        CHECK((int)used == c.frame_len, "%s: consumed the whole frame", c.id);
        CHECK(f.ns_hash == bus_ns_hash(c.ns), "%s: ns hash round-trips", c.id);
        CHECK(f.type == t, "%s: type round-trips", c.id);
        CHECK(f.scope_bits == bus_scope_bits_for_name(c.scope),
              "%s: scope round-trips", c.id);
        CHECK(f.value_len == c.value_len, "%s: value length round-trips",
              c.id);
    }

    /* ---- decode of the VALUE, in Python's precedence -------------- */
    {
        double num = 0;
        char str[64] = "";
        /* 1 byte -> number, even when it is a bool on the far side. */
        const uint8_t one[] = { 0x2A };
        CHECK(bus_value_decode(one, 1, &num, str, sizeof(str)) ==
                  BusValueKind::Number && num == 42.0,
              "1 byte decodes as the number 42 (Python: data[0])");
        /* "cap" is 4 value bytes and MUST decode as a string, not a
         * float — transport_uart.decode_value tries the length-prefix
         * first, and a display showing 1.4e-42 instead of "cap" is what
         * disagreeing here would look like. */
        const uint8_t s3[] = { 0x03, 'c', 'a', 'p' };
        CHECK(bus_value_decode(s3, 4, &num, str, sizeof(str)) ==
                  BusValueKind::String && strcmp(str, "cap") == 0,
              "a 3-char length-prefixed string beats the float32 reading");
        /* TAGGED float32 -> exactly float32, no guessing ([BP-VALUE-CODEC]). */
        uint8_t f4[kBusValueMaxNumeric];
        size_t fn = bus_value_encode_f32(87.5f, f4);
        CHECK(fn == 5 && f4[0] == kBusValueTagFloat32,
              "a float32 encodes as [0xFF][4] (got %u bytes, tag 0x%02X)",
              (unsigned)fn, f4[0]);
        CHECK(bus_value_decode(f4, fn, &num, str, sizeof(str)) ==
                  BusValueKind::Number && num > 87.4 && num < 87.6,
              "a tagged float32 decodes as 87.5 (got %f)", num);

        /* TAGGED int32, INCLUDING NEGATIVES — the case that was wrong under
         * BOTH decoders before the tag existed: this codec read -5 as nan and
         * the CAN bridge read it as 4294967291.  Neither was a safe
         * convergence target, which is why the fix was a wire bit and not a
         * winner ([BP-VALUE-CODEC] / DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-
         * EACAE9). */
        uint8_t i4[kBusValueMaxNumeric];
        size_t in = bus_value_encode_i32(-5, i4);
        CHECK(in == 5 && i4[0] == kBusValueTagInt32,
              "an int32 encodes as [0xFE][4] (got %u bytes, tag 0x%02X)",
              (unsigned)in, i4[0]);
        CHECK(bus_value_decode(i4, in, &num, str, sizeof(str)) ==
                  BusValueKind::Number && num == -5.0,
              "a tagged int32 decodes as -5 exactly (got %f)", num);
        bus_value_encode_i32(1000, i4);
        CHECK(bus_value_decode(i4, 5, &num, str, sizeof(str)) ==
                  BusValueKind::Number && num == 1000.0,
              "a tagged int32 decodes as 1000, not 1.4e-42 (got %f)", num);

        /* An UNTAGGED 4-byte value is Raw now, NOT a guessed float32.  That
         * guess was the defect.  A frame from a peer this fleet has not
         * reflashed must be visibly undecodable, not invisibly wrong. */
        const uint8_t bare4[] = { 0x00, 0x00, 0xAF, 0x42 };   /* was 87.5f */
        CHECK(bus_value_decode(bare4, 4, &num, str, sizeof(str)) ==
                  BusValueKind::Raw,
              "an UNTAGGED 4-byte value is Raw, never a guessed float32");

        CHECK(bus_value_decode(nullptr, 0, &num, str, sizeof(str)) ==
                  BusValueKind::None,
              "an empty value is None, not zero");
    }

    /* ---- NO SILENT FALLBACK -------------------------------------- */
    {
        /* An unallocated type code must be DROPPED, never read as State:
         * that substitution is precisely how an ACK became a spurious
         * state change ([BP-TYPE-CODES]). */
        uint8_t frame[16];
        size_t n = bus_frame_encode(0x1234, BusMessageType::State, 1,
                                    nullptr, 0, frame, sizeof(frame));
        CHECK(n == 6, "valueless frame is 6 bytes (sync+len+3+crc), got %u",
              (unsigned)n);
        frame[4] = (uint8_t)((15 << 4) | (1 << 2));      /* code 15 */
        frame[2 + frame[1]] = bus_crc8(frame + 1, 1 + frame[1]);
        BusFrame f;
        size_t used = 0;
        CHECK(bus_frame_decode(frame, n, &f, &used) ==
                  BusFrameResult::UnallocatedType,
              "type code 15 -> UnallocatedType (dropped, not State)");
        CHECK(used == n, "the dropped frame is consumed, not re-scanned");
    }
    {
        /* A corrupt frame must be reported, not delivered. */
        uint8_t frame[32];
        const uint8_t v[] = { 0x01 };
        size_t n = bus_frame_encode(0x1234, BusMessageType::State, 1, v, 1,
                                    frame, sizeof(frame));
        frame[n - 1] ^= 0xFF;
        BusFrame f;
        size_t used = 0;
        CHECK(bus_frame_decode(frame, n, &f, &used) == BusFrameResult::BadCrc,
              "a flipped CRC is BadCrc");
    }
    {
        /* Leading garbage resyncs; a truncated frame waits. */
        uint8_t buf[64];
        buf[0] = 0x00; buf[1] = 0xFF;
        const uint8_t v[] = { 0x07 };
        size_t n = bus_frame_encode(0x1234, BusMessageType::State, 1, v, 1,
                                    buf + 2, sizeof(buf) - 2);
        BusFrame f;
        size_t used = 0;
        CHECK(bus_frame_decode(buf, n + 2, &f, &used) ==
                  BusFrameResult::NoSync && used == 2,
              "leading garbage is skipped, exactly 2 bytes consumed");
        CHECK(bus_frame_decode(buf + 2, n, &f, &used) == BusFrameResult::Ok,
              "the frame after the garbage decodes");
        CHECK(bus_frame_decode(buf + 2, n - 1, &f, &used) ==
                  BusFrameResult::Incomplete && used == 0,
              "a truncated frame is Incomplete and consumes nothing");
    }
    {
        /* Encoding refuses rather than substitutes when it cannot fit. */
        uint8_t out[4];
        CHECK(bus_frame_encode(0x1234, BusMessageType::State, 1, nullptr, 0,
                               out, sizeof(out)) == 0,
              "encode into too small a buffer returns 0 (drop, not truncate)");
    }

    int total = s_pass + s_fail;
    printf("%d/%d checks pass\n", s_pass, total);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    printf("%d FAILED\n", s_fail);
    return 1;
}

#endif /* FRAME_CORPUS_TEST */
