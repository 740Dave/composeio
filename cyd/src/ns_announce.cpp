/*
 * ns_announce.cpp — THE WELL-KNOWN ROOT, firmware side.  Cadence and emission.
 * [SPEC:CXC-CAN] [SPEC:CXC-CONFIG-BUS] — workstream G2 of
 * docs/PLAN-fleet-baseline-and-bus-native.md.
 *
 * Read ns_announce.h first: it carries the root, the pinned hashes, the payload
 * format and the reasoning.  This file is only WHEN the frames go out and WHAT
 * HAPPENS WHEN THEY DO NOT.
 *
 * IT NAMES NO SUBSTRATE.  Everything it needs from the link it asks the
 * BusTransport seam for — `max_raw_value()` for the chunk size and
 * `publish_raw()` for the frames — so the same code announces over CAN's 6
 * usable content bytes and UART's 250 with no per-transport branch (DP-CIO-6).
 * The announcement is USEFUL on CAN and merely harmless on MQTT (where names
 * ride in the topic and nothing needed announcing), and that asymmetry is not a
 * reason to branch: a board must not behave differently depending on which wire
 * it woke up on, or the CAN path becomes the one nobody exercises.
 *
 * ---------------------------------------------------------------------------
 * THREE DECISIONS THAT LOOK LIKE DETAILS AND ARE NOT
 * ---------------------------------------------------------------------------
 *
 * 1. IT IS PACED, ONE SMALL BURST PER TICK.  A 6-namespace payload is ~190
 *    bytes = 33 classic-CAN frames.  Offered back to back that is a burst into
 *    a transmit queue the bridge measured at ~a dozen frames before ENOBUFS on
 *    an MCP2515 — and a refused frame at the far end is INDISTINGUISHABLE from
 *    a board that never announced.  Pacing costs ~300 ms once per boot.
 *
 * 2. THE BOOT ANNOUNCEMENT IS JITTERED, BY DEVICE ID.  A rack powered from one
 *    switch boots in lockstep, and `_device/announce` is a BROADCAST root: two
 *    nodes announcing at once interleave, and with node ids unallocated
 *    (src == 0 for everyone) the receiver cannot demultiplex them.  It detects
 *    the collision and refuses — loudly, which is correct — but a rack that
 *    collides on every boot never gets named.  The jitter is derived from the
 *    device id, NOT from rand(): it is deterministic, different per board, and
 *    the same on every boot, so a rack that works keeps working and a rack that
 *    does not can be reasoned about.
 *
 * 3. A REFUSED FRAME IS COUNTED AND THE WHOLE ANNOUNCEMENT IS ABANDONED.  Not
 *    retried mid-transfer, and not pushed through regardless.  A transfer with
 *    a hole fails the receiver's CRC-16 and teaches nothing, so continuing to
 *    push the remaining 20 frames is 20 frames of contention spent on an
 *    outcome already known.  The next poll retries the whole thing, which is
 *    the same shape config_bus.cpp uses and for the same reason.
 */

#include "ns_announce.h"

#include "bus_transport.h"
#include "bus_wire_codes.h"
#include "config_chunk.h"

#include <cstdio>

#include "esp_log.h"

static const char* TAG = "ns_announce";

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

static const char* const* s_names = nullptr;
static size_t s_name_count = 0;
static bool s_inited = false;

static uint8_t s_payload[kNsAnnouncePayloadCap];
static size_t s_payload_len = 0;
static uint16_t s_payload_hash = 0;
static size_t s_refused_names = 0;

/* Emission cursor.  kSeqIdle means "nothing in flight"; 0 is the HEADER frame
 * and 1..N are the data frames, matching config_chunk's own numbering so the
 * cursor and the wire agree without a translation nobody would maintain. */
static constexpr uint32_t kSeqIdle = 0xFFFFFFFFu;
static uint32_t s_seq = kSeqIdle;
static uint16_t s_chunk_payload = 0;
static uint16_t s_chunk_count = 0;

static uint32_t s_next_ms = 0;
static uint32_t s_jitter_ms = 0;
static bool s_armed = false;

static uint32_t s_transfers = 0;
static uint32_t s_frames = 0;
static uint32_t s_failures = 0;

/* Frames per tick, and the gap between ticks that carry frames.  Small numbers
 * chosen against the ONE measurement that exists: the bridge's MCP2515 refused
 * a back-to-back burst after roughly a dozen frames. */
static constexpr uint32_t kFramesPerTick = 4;
static constexpr uint32_t kFrameGapMs = 20;

/* ------------------------------------------------------------------ */
/* Init                                                                */
/* ------------------------------------------------------------------ */

void ns_announce_init(const char* const* names, size_t count) {
    ns_announce_reset();
    if (!names || count == 0) {
        ESP_LOGE(TAG, "ns_announce_init with no namespaces — this board will "
                 "announce NOTHING, so every namespace it publishes stays an "
                 "irreversible 16-bit hash at the bridge.  Nothing installed.");
        return;
    }
    s_names = names;
    s_name_count = count;

    s_payload_len = ns_announce_build_payload(names, count, s_payload,
                                              sizeof(s_payload),
                                              &s_refused_names);
    if (s_payload_len == 0) {
        ESP_LOGE(TAG, "could not build an announcement payload from %u "
                 "namespace(s) — %u were refused.  Nothing will be announced, "
                 "and every namespace this board publishes will be unnameable "
                 "at the bridge.", (unsigned)count, (unsigned)s_refused_names);
        return;
    }
    if (s_refused_names) {
        /* LOUD, and it names the count.  These are namespaces that will never
         * resolve anywhere; a silent skip here is the fleet census being
         * confidently incomplete, which is the failure this whole workstream
         * exists to remove. */
        ESP_LOGE(TAG, "%u of %u namespace(s) are NOT announceable and were "
                 "REFUSED (see ns_announce_name_problem) — they will never be "
                 "resolvable from a CAN frame.", (unsigned)s_refused_names,
                 (unsigned)count);
        for (size_t i = 0; i < count; i++) {
            const char* why = ns_announce_name_problem(names[i]);
            if (why) ESP_LOGE(TAG, "  refused %s: %s",
                              names[i] ? names[i] : "(null)", why);
        }
    }

    s_payload_hash = config_content_hash(s_payload, s_payload_len);

    /* Jitter from the payload's own CRC — a number that already differs per
     * board because it is a hash of names containing the device id.  No new
     * entropy source, no rand(), no per-boot variation. */
    s_jitter_ms = (uint32_t)(s_payload_hash % 2000u);

    s_inited = true;
    ESP_LOGI(TAG, "namespace announcement armed: %u name(s), %u payload byte(s), "
             "CRC 0x%04X, on %s (0x%04X), boot jitter %u ms",
             (unsigned)(count - s_refused_names), (unsigned)s_payload_len,
             s_payload_hash, kNsAnnounce, kNsAnnounceHash,
             (unsigned)s_jitter_ms);
    ns_announce_arm();
}

void ns_announce_reset(void) {
    s_names = nullptr;
    s_name_count = 0;
    s_inited = false;
    s_payload_len = 0;
    s_payload_hash = 0;
    s_refused_names = 0;
    s_seq = kSeqIdle;
    s_chunk_payload = 0;
    s_chunk_count = 0;
    s_next_ms = 0;
    s_jitter_ms = 0;
    s_armed = false;
    s_transfers = s_frames = s_failures = 0;
}

void ns_announce_arm(void) {
    if (!s_inited) return;
    /* RESTART rather than queue — see the header.  Two announcements from one
     * board would be two interleaved transfers on a shared root, which the
     * receiver would have to discard. */
    s_armed = true;
    s_seq = kSeqIdle;
}

/* ------------------------------------------------------------------ */
/* Emission                                                            */
/* ------------------------------------------------------------------ */

static bool send_frame(BusTransport& bus, const uint8_t* v, size_t n) {
    /* DISCOVERY (type code 5), not STATE, and the distinction is [BP-TYPE-CODES]
     * doing its job.  `_bus/discovery` already carries a module's exported
     * namespaces as DISCOVERY on the Python tier; a node announcing its OWN
     * namespace names is the same act one tier down.  The five
     * `_device/<id>/*` fields stay STATE because they are current VALUES a late
     * subscriber must be able to read; a name list is an EVENT about naming. */
    if (bus.publish_raw(kNsAnnounce, BusMessageType::Discovery, v, n)) {
        s_frames++;
        return true;
    }
    s_failures++;
    return false;
}

static void abandon(const char* why) {
    ESP_LOGW(TAG, "announcement ABANDONED at frame %u/%u: %s.  The transfer "
             "would fail the receiver's CRC anyway, so the remaining frames "
             "are contention spent on a known outcome.  It retries on the next "
             "poll.", (unsigned)(s_seq == kSeqIdle ? 0 : s_seq),
             (unsigned)s_chunk_count, why);
    s_seq = kSeqIdle;
    s_armed = false;
}

void ns_announce_tick(uint32_t now_ms) {
    if (!s_inited) return;
    BusTransport& bus = bus_transport();

    if (s_armed && s_seq == kSeqIdle) {
        if (s_next_ms == 0) {                 /* first arm: apply the jitter */
            s_next_ms = now_ms + s_jitter_ms;
            return;
        }
        if ((int32_t)(now_ms - s_next_ms) < 0) return;

        /* THE SUBSTRATE DECIDES THE CHUNK SIZE, through the seam.  8 on classic
         * CAN, 252 on UART — and this file names neither number. */
        size_t maxv = bus.max_raw_value();
        if (maxv <= kConfigSeqBytes) {
            ESP_LOGE(TAG, "transport '%s' carries %u opaque byte(s); a config "
                     "chunk needs more than the %u sequence bytes.  This board "
                     "CANNOT announce on this substrate.", bus.name(),
                     (unsigned)maxv, (unsigned)kConfigSeqBytes);
            s_armed = false;
            return;
        }
        size_t cp = maxv - kConfigSeqBytes;
        if (cp > 250) cp = 250;               /* the header's one-byte field */
        s_chunk_payload = (uint16_t)cp;
        s_chunk_count = config_chunk_count(s_payload_len, (uint8_t)cp);

        uint8_t hdr[8];
        size_t n = ns_announce_frame(s_payload, s_payload_len, s_payload_hash,
                                     kConfigHeaderSeq, (uint8_t)cp, hdr,
                                     sizeof(hdr));
        if (n == 0 || !send_frame(bus, hdr, n)) {
            abandon("the transport refused the header frame");
            return;
        }
        s_seq = 1;
        s_next_ms = now_ms + kFrameGapMs;
        return;
    }

    if (s_seq == kSeqIdle) return;
    if ((int32_t)(now_ms - s_next_ms) < 0) return;

    for (uint32_t i = 0; i < kFramesPerTick && s_seq <= s_chunk_count; i++) {
        /* THE SAME BUILDER THE HOST TEST DRIVES. The cursor arithmetic lives in
         * ns_announce_frame() precisely so the shipped path and the tested path
         * are one path — an off-by-one here produces a transfer that reassembles
         * to the right LENGTH and the wrong CRC, which the board cannot see. */
        uint8_t frame[252];
        size_t len = ns_announce_frame(s_payload, s_payload_len, s_payload_hash,
                                       (uint16_t)s_seq, (uint8_t)s_chunk_payload,
                                       frame, sizeof(frame));
        if (len == 0 || !send_frame(bus, frame, len)) {
            abandon("the transport refused a data frame");
            return;
        }
        s_seq++;
    }
    s_next_ms = now_ms + kFrameGapMs;

    if (s_seq > s_chunk_count) {
        s_transfers++;
        s_seq = kSeqIdle;
        s_armed = false;
        ESP_LOGI(TAG, "announced %u namespace name(s) in %u frame(s) on %s "
                 "(CRC 0x%04X, %u B).  A bridge that heard this can now name "
                 "every frame this board sends; one that did not still cannot, "
                 "and says so.", (unsigned)(s_name_count - s_refused_names),
                 (unsigned)(s_chunk_count + 1), kNsAnnounce, s_payload_hash,
                 (unsigned)s_payload_len);
    }
}

/* ------------------------------------------------------------------ */
/* Diagnostics                                                         */
/* ------------------------------------------------------------------ */

uint32_t ns_announce_transfers(void) { return s_transfers; }
uint32_t ns_announce_frames(void) { return s_frames; }
uint32_t ns_announce_failures(void) { return s_failures; }
uint32_t ns_announce_refused_names(void) { return (uint32_t)s_refused_names; }
