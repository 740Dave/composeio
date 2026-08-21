/*
 * config_chunk_selftest.cpp — host PARITY harness for the config chunk format.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-CHUNKS] [CXC-CONFIG-BUS-TTL]
 *
 * Pure host test (no hardware, no ESP-IDF) — the same guarded-file idiom as
 * bus_frame_selftest.cpp; the body is behind CONFIG_CHUNK_CORPUS_TEST, defined
 * only by [env:native_config_chunk].
 *
 *   pio run -e native_config_chunk && .pio/build/native_config_chunk/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHY PARITY AND NOT ROUND TRIP.  Chunking and reassembling with the same code
 * proves only self-consistency: it passes with the sequence bytes swapped, a
 * little-endian total_len, or a CRC-16 seeded 0x0000, because both halves make
 * the same mistake.  The oracle is the real Python chunker
 * (composeio.config_chunk), baked into cyd/test/config_chunk_corpus_generated.h.
 *
 * WHAT THIS CANNOT PROVE, said plainly: nothing about a wire.  It covers the
 * format, the offsets, the hash, the discard-on-mismatch rule and the TTL
 * ladder.  Whether a real leaf receives a real transfer is a bench question,
 * and whether the resulting panel is CORRECT is a question only eyes answer.
 */

#ifdef CONFIG_CHUNK_CORPUS_TEST

#include "config_chunk.h"
#include "config_chunk_corpus_generated.h"

#include <cstdio>
#include <cstring>
#include <vector>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

static void hexdump(const char* label, const uint8_t* p, int n) {
    printf("      %s:", label);
    for (int i = 0; i < n && i < 24; i++) printf(" %02X", p[i]);
    if (n > 24) printf(" ... (%d bytes)", n);
    printf("\n");
}

int main(void) {
    printf("config chunk parity: composeio.config_chunk (oracle) vs "
           "cyd/src/config_chunk.cpp\n");

    /* ---- the constants THE FORMAT IS ABOUT ------------------------ */
    CHECK((int)kConfigSeqBytes == kCfgSeqBytes,
          "seq bytes: Python %d, C++ %d", kCfgSeqBytes, (int)kConfigSeqBytes);
    CHECK((int)kConfigHeaderContentBytes == kCfgHeaderContentBytes,
          "header content bytes: Python %d, C++ %d", kCfgHeaderContentBytes,
          (int)kConfigHeaderContentBytes);
    CHECK((int)kConfigTtlSeed == kCfgTtlSeed,
          "ttl seed: Python %d, C++ %d", kCfgTtlSeed, (int)kConfigTtlSeed);
    CHECK((int)kConfigCanContentBytes == kCfgCanContentBytes,
          "CAN content bytes: Python %d, C++ %d", kCfgCanContentBytes,
          (int)kConfigCanContentBytes);
    CHECK((int)kConfigUartContentBytes == kCfgUartContentBytes,
          "UART content bytes: Python %d, C++ %d", kCfgUartContentBytes,
          (int)kConfigUartContentBytes);
    /* THE load-bearing sizing claim: a header frame is ONE classic-CAN frame.
     * If this ever fails, the format has become UART-shaped and
     * [CXC-CONFIG-BUS-BUDGET] has been violated. */
    CHECK(kConfigSeqBytes + kConfigHeaderContentBytes == 8,
          "a header frame must be exactly 8 bytes (one classic-CAN frame); "
          "it is %d", (int)(kConfigSeqBytes + kConfigHeaderContentBytes));

    /* ---- content hash on BINARY data ------------------------------ */
    for (int i = 0; i < kCfgHashCaseCount; i++) {
        const CfgHashCase& c = kCfgHashCases[i];
        uint16_t got = config_content_hash(c.data, (size_t)c.len);
        CHECK(got == c.hash, "content_hash(%s, %d B): Python 0x%04X, C++ 0x%04X",
              c.id, c.len, c.hash, got);
    }

    /* ---- the transfers -------------------------------------------- */
    for (int t = 0; t < kCfgTransferCaseCount; t++) {
        const CfgTransferCase& c = kCfgTransferCases[t];

        /* (a) derived chunk count matches Python's */
        uint16_t n = config_chunk_count((uint32_t)c.payload_len,
                                        (uint8_t)c.chunk_payload);
        CHECK(n == c.chunk_count, "%s: chunk_count Python %d, C++ %u", c.id,
              c.chunk_count, (unsigned)n);

        /* (b) the header we build is byte-identical to Python's */
        uint8_t hdr[16];
        size_t hn = config_encode_header((uint16_t)c.payload_len,
                                         (uint8_t)c.chunk_payload,
                                         c.content_hash, (uint8_t)kCfgTtlSeed,
                                         hdr, sizeof(hdr));
        bool ok = (int)hn == c.frames[0].len &&
                  memcmp(hdr, c.frames[0].bytes, hn) == 0;
        CHECK(ok, "%s: header frame differs (Python %d B, C++ %u)", c.id,
              c.frames[0].len, (unsigned)hn);
        if (!ok) {
            hexdump("python", c.frames[0].bytes, c.frames[0].len);
            hexdump("c++   ", hdr, (int)hn);
        }

        /* (c) every DATA frame we build is byte-identical */
        for (int i = 1; i < c.frame_count; i++) {
            uint8_t buf[300];
            size_t off = (size_t)(i - 1) * (size_t)c.chunk_payload;
            size_t len = (size_t)c.payload_len - off;
            if (len > (size_t)c.chunk_payload) len = (size_t)c.chunk_payload;
            size_t dn = config_encode_data((uint16_t)i, c.payload + off, len,
                                           buf, sizeof(buf));
            bool dok = (int)dn == c.frames[i].len &&
                       memcmp(buf, c.frames[i].bytes, dn) == 0;
            CHECK(dok, "%s: data frame %d differs (Python %d B, C++ %u)",
                  c.id, i, c.frames[i].len, (unsigned)dn);
            if (!dok) {
                hexdump("python", c.frames[i].bytes, c.frames[i].len);
                hexdump("c++   ", buf, (int)dn);
                break;
            }
        }

        /* (d) the PYTHON frames reassemble, IN ORDER */
        {
            ConfigReassembler r;
            ConfigReassembler::Result last = ConfigReassembler::Result::Ignored;
            for (int i = 0; i < c.frame_count; i++)
                last = r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
            CHECK(last == ConfigReassembler::Result::Complete,
                  "%s: in-order reassembly ended '%s', expected complete",
                  c.id, ConfigReassembler::result_name(last));
            CHECK(r.complete() && r.payload_len() == (size_t)c.payload_len &&
                  (c.payload_len == 0 ||
                   memcmp(r.payload(), c.payload, (size_t)c.payload_len) == 0),
                  "%s: reassembled payload differs (%u vs %d bytes)", c.id,
                  (unsigned)r.payload_len(), c.payload_len);
            CHECK(r.actual_hash() == c.content_hash,
                  "%s: reassembled hash 0x%04X, expected 0x%04X", c.id,
                  r.actual_hash(), c.content_hash);
        }

        if (c.frame_count < 2) continue;

        /* (e) REVERSED delivery reassembles identically — offsets come from
         *     the header, never from arrival order. */
        {
            ConfigReassembler r;
            r.on_frame(c.frames[0].bytes, (size_t)c.frames[0].len);
            ConfigReassembler::Result last = ConfigReassembler::Result::Ignored;
            for (int i = c.frame_count - 1; i >= 1; i--)
                last = r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
            CHECK(last == ConfigReassembler::Result::Complete,
                  "%s: reversed reassembly ended '%s'", c.id,
                  ConfigReassembler::result_name(last));
            CHECK(r.complete() &&
                  memcmp(r.payload(), c.payload, (size_t)c.payload_len) == 0,
                  "%s: reversed reassembly payload differs", c.id);
        }

        /* (f) a MISSING frame never completes.  This is the case per-frame
         *     CRC-8 cannot see at all — every frame that DID arrive was
         *     perfectly valid. */
        {
            ConfigReassembler r;
            for (int i = 0; i < c.frame_count; i++) {
                if (i == c.frame_count - 1) continue;
                r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
            }
            CHECK(!r.complete(), "%s: a transfer missing its last chunk "
                  "reported COMPLETE", c.id);
        }

        /* (g) a CORRUPTED byte fails the hash and DISCARDS. */
        {
            ConfigReassembler r;
            ConfigReassembler::Result last = ConfigReassembler::Result::Ignored;
            for (int i = 0; i < c.frame_count; i++) {
                if (i == 1) {
                    std::vector<uint8_t> bad(c.frames[i].bytes,
                                             c.frames[i].bytes + c.frames[i].len);
                    bad[kConfigSeqBytes] ^= 0xFF;
                    last = r.on_frame(bad.data(), bad.size());
                } else {
                    last = r.on_frame(c.frames[i].bytes,
                                      (size_t)c.frames[i].len);
                }
            }
            CHECK(last == ConfigReassembler::Result::HashMismatch,
                  "%s: a corrupted chunk ended '%s', expected mismatch", c.id,
                  ConfigReassembler::result_name(last));
            CHECK(!r.complete(), "%s: a hash mismatch must DISCARD, never "
                  "leave a plausible-looking ruin available", c.id);
        }

        /* (h) a DUPLICATE is reported, never double-counted. */
        {
            ConfigReassembler r;
            r.on_frame(c.frames[0].bytes, (size_t)c.frames[0].len);
            r.on_frame(c.frames[1].bytes, (size_t)c.frames[1].len);
            uint16_t before = r.received();
            CHECK(r.on_frame(c.frames[1].bytes, (size_t)c.frames[1].len) ==
                  ConfigReassembler::Result::Duplicate,
                  "%s: a repeated frame was not reported as a duplicate", c.id);
            CHECK(r.received() == before,
                  "%s: a duplicate advanced the received count", c.id);
        }

        /* (i) a header MID-TRANSFER abandons the old transfer.  Only
         *     meaningful for a multi-chunk transfer: with a single data frame
         *     the transfer has already COMPLETED, so a new header is a fresh
         *     Started — which is Python's behaviour too (`Reassembler.active`
         *     is false once a payload is held). */
        if (c.frame_count > 2) {
            ConfigReassembler r;
            r.on_frame(c.frames[0].bytes, (size_t)c.frames[0].len);
            r.on_frame(c.frames[1].bytes, (size_t)c.frames[1].len);
            CHECK(r.on_frame(c.frames[0].bytes, (size_t)c.frames[0].len) ==
                  ConfigReassembler::Result::Restarted,
                  "%s: a header mid-transfer did not restart", c.id);
            CHECK(r.received() == 0, "%s: a restart must clear progress", c.id);
        }
    }

    /* ---- a DATA frame with no header is STRAY, never a silent start --- */
    {
        ConfigReassembler r;
        const CfgTransferCase& c = kCfgTransferCases[5];
        CHECK(r.on_frame(c.frames[1].bytes, (size_t)c.frames[1].len) ==
              ConfigReassembler::Result::Stray,
              "a data frame with no header must be STRAY");
    }

    /* ---- take() transfers ownership exactly once --------------------- */
    {
        const CfgTransferCase& c = kCfgTransferCases[5];
        ConfigReassembler r;
        for (int i = 0; i < c.frame_count; i++)
            r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
        size_t n = 0;
        char* p = r.take(&n);
        CHECK(p != nullptr && n == (size_t)c.payload_len,
              "take() should yield the payload (%u vs %d)", (unsigned)n,
              c.payload_len);
        CHECK(r.take(nullptr) == nullptr, "take() twice must yield nullptr — "
              "a scene is delivered ONCE");
        free(p);
    }

    /* ---- the TTL ladder ([CXC-CONFIG-BUS-TTL]) ----------------------- */
    for (int i = 0; i + 1 < kCfgTtlLadderLen; i++) {
        std::vector<uint8_t> v(kCfgTtlLadder[i].bytes,
                               kCfgTtlLadder[i].bytes + kCfgTtlLadder[i].len);
        bool alive = config_ttl_decrement(v.data(), v.size());
        CHECK(alive, "ttl step %d: C++ expired early (Python did not)", i);
        CHECK(memcmp(v.data(), kCfgTtlLadder[i + 1].bytes, v.size()) == 0,
              "ttl step %d -> %d: bytes differ from Python", i, i + 1);
    }
    {
        /* The LAST rung is where Python returns None. */
        int last = kCfgTtlLadderLen - 1;
        std::vector<uint8_t> v(kCfgTtlLadder[last].bytes,
                               kCfgTtlLadder[last].bytes + kCfgTtlLadder[last].len);
        CHECK(!config_ttl_decrement(v.data(), v.size()),
              "ttl must EXPIRE at the rung Python expires at (step %d)", last);
        CHECK(kCfgTtlLadderLen == kCfgTtlSeed,
              "the ladder should have exactly ttl-seed rungs (%d vs %d)",
              kCfgTtlLadderLen, kCfgTtlSeed);
    }
    {
        /* A DATA frame is forwarded verbatim — a relay never parses one. */
        uint8_t d[8] = {0x00, 0x07, 1, 2, 3, 4, 5, 6};
        uint8_t before[8];
        memcpy(before, d, 8);
        CHECK(config_ttl_decrement(d, sizeof(d)), "a data frame must survive "
              "a relay hop");
        CHECK(memcmp(d, before, 8) == 0, "a relay must forward data frames "
              "BYTE-for-byte; it changed them");
    }

    /* ---- the ACK  [CXC-CONFIG-BUS-CHUNKS] ---------------------------- */
    /* THE SIZING CLAIM, asserted rather than described: the ACK fits a classic
     * CAN frame.  Its predecessor — snprintf("ok %04x %u") — was 13 bytes for
     * the proven scene and 24 on the mismatch path, and bus_can.cpp refuses
     * anything over 8, so a CAN transfer could not be acknowledged AT ALL. */
    CHECK((int)kConfigAckBytes == kCfgAckBytes,
          "ack size: Python %d, C++ %d", kCfgAckBytes, (int)kConfigAckBytes);
    CHECK(kConfigAckBytes <= 8,
          "the ACK is %d bytes; a classic CAN frame carries 8 and bus_can.cpp "
          "DROPS anything longer — this is the defect the binary ACK exists to "
          "remove", (int)kConfigAckBytes);
    CHECK((int)kConfigAckTag == kCfgAckTag,
          "ack tag: Python 0x%02X, C++ 0x%02X", kCfgAckTag,
          (int)kConfigAckTag);

    for (int i = 0; i < kCfgAckCaseCount; i++) {
        const CfgAckCase& c = kCfgAckCases[i];
        uint8_t enc[16];
        size_t n = config_encode_ack((ConfigAckStatus)c.status, c.hash,
                                     c.total_len, c.ackseq, enc, sizeof(enc));
        bool same = (n == (size_t)c.len) && memcmp(enc, c.bytes, n) == 0;
        CHECK(same, "ack %s: firmware bytes differ from Python", c.id);
        if (!same) {
            hexdump("python", c.bytes, c.len);
            hexdump("c++   ", enc, (int)n);
        }
        /* And the decode is the exact inverse, on PYTHON's bytes. */
        ConfigAck got{};
        bool ok = config_decode_ack(c.bytes, (size_t)c.len, &got);
        CHECK(ok, "ack %s: firmware REFUSED a value Python produced", c.id);
        CHECK(ok && got.status == c.status && got.hash == c.hash &&
              got.total_len == c.total_len && got.ackseq == c.ackseq,
              "ack %s: decoded status %u hash 0x%04X len %u seq %u, expected "
              "%d 0x%04X %u %u", c.id, (unsigned)got.status, got.hash,
              (unsigned)got.total_len, (unsigned)got.ackseq, c.status, c.hash,
              (unsigned)c.total_len, (unsigned)c.ackseq);
    }

    /* Seven bytes of SOMETHING ELSE is refused, never parsed — including the
     * retired text ack, which an un-reflashed leaf still emits. */
    for (int i = 0; i < kCfgAckRejectCount; i++) {
        const CfgAckReject& c = kCfgAckRejects[i];
        ConfigAck got{};
        CHECK(!config_decode_ack(c.bytes, (size_t)c.len, &got),
              "non-ack '%s' was decoded AS an ack", c.id);
    }

    /* Every allocated status has a NAME, and an unallocated one does not get
     * quietly called "ok". */
    CHECK(strcmp(config_ack_status_name(0), "ok") == 0, "status 0 name");
    CHECK(strcmp(config_ack_status_name(15), "ok") != 0,
          "an unallocated status must NOT be named 'ok'");

    /* ---- ackseq: the field a repair would resume from ---------------- */
    {
        /* A gap at chunk 3 is reported as 3; a COMPLETE transfer reports the
         * last sequence; a hash mismatch reports 0 (nothing missing). */
        const CfgTransferCase& c = kCfgTransferCases[3];   /* ragged_can */
        ConfigReassembler r;
        r.on_frame(c.frames[0].bytes, (size_t)c.frames[0].len);
        for (int i = 1; i < c.frame_count; i++) {
            if (i == 3) continue;
            r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
        }
        CHECK(r.first_missing() == 3,
              "first_missing() with chunk 3 dropped is %u, expected 3",
              (unsigned)r.first_missing());
        CHECK(r.ack_seq() == 3, "ack_seq() on an incomplete transfer must be "
              "the FIRST MISSING chunk (%u)", (unsigned)r.ack_seq());
        r.reset();
        for (int i = 0; i < c.frame_count; i++)
            r.on_frame(c.frames[i].bytes, (size_t)c.frames[i].len);
        CHECK(r.ack_seq() == c.chunk_count,
              "ack_seq() on a complete transfer is %u, expected the chunk "
              "count %d", (unsigned)r.ack_seq(), c.chunk_count);
        CHECK(r.first_missing() == 0,
              "a complete transfer must have NO missing chunk (%u)",
              (unsigned)r.first_missing());
    }

    printf("\n%d checks, %d failures\n", s_pass + s_fail, s_fail);
    printf(s_fail ? "RESULT: FAIL\n" : "ALL PASS\n");
    return s_fail ? 1 : 0;
}

#endif /* CONFIG_CHUNK_CORPUS_TEST */
