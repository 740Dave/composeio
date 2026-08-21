/*
 * config_bus.cpp — see config_bus.h.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-LEAF] [CXC-CONFIG-BUS-CHUNKS]
 */

#include "config_bus.h"
#include "config_chunk.h"
#include "bus_transport.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"

static const char* TAG = "config_bus";

/* ---- retry policy ([CXC-CONFIG-BUS-OPEN] retry-and-backoff) ----------
 *
 * These are PROTOCOL CONSTANTS, not deployment settings, for the same reason
 * the TTL is: a deployer who can tune them can build a fleet that storms its
 * own head, and nothing about them belongs in a scene or a descriptor.
 *
 * The first request goes out immediately (a booting board should not wait a
 * second to ask for the one thing it cannot run without); the interval then
 * doubles to a 30 s ceiling.  Jitter is +/-25%, applied to every wait: without
 * it, twelve boards that lost their head together come back in LOCKSTEP and
 * re-create the outage the moment it clears. */
static constexpr uint32_t kRetryBaseMs = 1000;
static constexpr uint32_t kRetryCapMs = 30000;
static constexpr uint32_t kAnnounceAfterMs = 8000;   /* then keep saying it */
static constexpr uint32_t kAnnounceEveryMs = 30000;

static char s_ns[128] = "";
static char s_status_ns[128] = "";
static char s_device[32] = "";
static ConfigBusPublishFn s_publish = nullptr;
static ConfigBusAnnounceFn s_announce = nullptr;

static ConfigReassembler s_asm;
static char* s_ready = nullptr;
static size_t s_ready_len = 0;

static uint32_t s_attempts = 0;
static uint32_t s_transfers = 0;
static uint32_t s_mismatches = 0;
static uint32_t s_acks_sent = 0;
/* ACKs the TRANSPORT REFUSED.  Counted because an ACK that never left is
 * indistinguishable, from the head, from an ACK that was never built — and
 * that indistinguishability is the whole defect this file just fixed one layer
 * up.  A leaf whose ack_failures climbs is a leaf whose head is guessing. */
static uint32_t s_ack_failures = 0;
static uint32_t s_next_ms = 0;
static uint32_t s_interval_ms = kRetryBaseMs;
static uint32_t s_last_announce_ms = 0;
static uint32_t s_first_request_ms = 0;
static bool s_ever = false;
static bool s_armed = false;

void config_bus_init(const char* device_id, ConfigBusPublishFn publish,
                     ConfigBusAnnounceFn announce) {
    s_publish = publish;
    s_announce = announce;
    if (!device_id || !device_id[0]) return;
    snprintf(s_device, sizeof(s_device), "%s", device_id);
    /* THE NAMESPACE IS THE ADDRESS ([CXC-CONFIG-BUS-ADDRESSING]) — no
     * destination field exists anywhere in the chunk format because this
     * string already is one. */
    snprintf(s_ns, sizeof(s_ns), "_device/%s/config", device_id);
    snprintf(s_status_ns, sizeof(s_status_ns), "_device/%s/status", device_id);
    ESP_LOGI(TAG, "config namespace: %s", s_ns);
}

size_t config_bus_namespaces(const char** out, size_t cap) {
    if (!s_ns[0] || cap < 1 || !out) return 0;
    out[0] = s_ns;
    return 1;
}

/* +/-25% jitter.  esp_random() is available before WiFi starts on every part
 * in this tree (RC fast clock entropy); it does not need the radio, which
 * matters because this code path exists precisely for boards with no radio. */
static uint32_t jitter(uint32_t ms) {
    uint32_t span = ms / 2;                 /* full width = 50% of ms */
    if (!span) return ms;
    return ms - span / 2 + (esp_random() % span);
}

void config_bus_request(void) {
    if (!s_ns[0]) return;
    s_attempts++;
    s_armed = true;
    /* SCHEDULE THE NEXT ATTEMPT HERE, not in the caller.  When main.cpp fired
     * the first request explicitly and the boot loop's first tick() then found
     * s_next_ms still 0, the board sent attempt 1 and attempt 2 back to back —
     * two full 6 KB transfers from the head for one boot, and the second one
     * arriving mid-first is exactly the interleaving the content hash exists
     * to catch.  One entry point owns the timer. */
    uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);
    s_next_ms = now_ms + jitter(s_interval_ms);
    s_interval_ms = s_interval_ms >= kRetryCapMs
                        ? kRetryCapMs
                        : (s_interval_ms * 2 > kRetryCapMs ? kRetryCapMs
                                                           : s_interval_ms * 2);
    BusTransport& bus = bus_transport();
    bool sent = bus.publish_raw(s_ns, BusMessageType::ConfigRequest, nullptr, 0);
    if (!sent) {
        /* KNOWN strategy, substrate that cannot carry it — a LOUD degrade, and
         * the announce path below is what the operator actually sees. */
        ESP_LOGE(TAG, "CONFIG_REQUEST could not be sent over transport '%s'. "
                 "`config: bus` needs a FRAMED substrate (uart, can); this "
                 "board will announce and keep trying.", bus.name());
    } else {
        ESP_LOGI(TAG, "CONFIG_REQUEST -> %s (attempt %lu, next retry in "
                 "~%lu ms)", s_ns, (unsigned long)s_attempts,
                 (unsigned long)s_interval_ms);
    }
}

/* The ACK rides the SAME namespace as the transfer — the request is the
 * address, and so is the answer.
 *
 * SEVEN BINARY BYTES, NOT A FORMATTED STRING ([CXC-CONFIG-BUS-CHUNKS]).  Until
 * 2026-08-13 this built `snprintf("ok %04x %u", ...)` — thirteen bytes for the
 * proven 5,856 B scene, twenty-four for the mismatch form — and bus_can.cpp
 * refuses anything over eight.  So on CAN the ACK was PHYSICALLY UNSENDABLE:
 * the leaf verified its scene, cleared its retry timer, rendered, and the head
 * never learned a thing.  Nothing detected the silence because there is no ACK
 * timeout, and on a mismatch the leaf's re-request reached the head as an
 * unexplained second CONFIG_REQUEST with no diagnosis attached.
 *
 * The transport's REFUSAL is now checked, too.  It was discarded before, so a
 * listen-only node (the live workaround for a dead transceiver) dropped every
 * ACK it built and reported nothing about it. */
static void ack(ConfigAckStatus status, uint16_t hash, uint16_t total_len,
                uint16_t ackseq) {
    if (!s_ns[0]) return;
    uint8_t v[kConfigAckBytes];
    size_t n = config_encode_ack(status, hash, total_len, ackseq, v, sizeof(v));
    if (!n) {
        s_ack_failures++;
        ESP_LOGE(TAG, "could not BUILD the ACK for hash 0x%04X — the head "
                 "will see silence", hash);
        return;
    }
    if (bus_transport().publish_raw(s_ns, BusMessageType::Ack, v, n)) {
        s_acks_sent++;
        ESP_LOGI(TAG, "ACK %s -> %s (hash 0x%04X, %u B, ackseq %u, %u wire "
                 "bytes)", config_ack_status_name((uint8_t)status), s_ns, hash,
                 (unsigned)total_len, (unsigned)ackseq, (unsigned)n);
    } else {
        s_ack_failures++;
        ESP_LOGE(TAG, "ACK %s for hash 0x%04X could NOT BE SENT over '%s' — "
                 "the head cannot distinguish this from a transfer that never "
                 "arrived.  %lu ACK(s) have now failed to leave this node.",
                 config_ack_status_name((uint8_t)status), hash,
                 bus_transport().name(), (unsigned long)s_ack_failures);
    }
}

bool config_bus_on_frame(const char* ns, BusMessageType type,
                         const uint8_t* value, size_t len) {
    if (!ns || !s_ns[0] || strcmp(ns, s_ns) != 0) return false;
    /* Consumed here whatever the type: this namespace is the config control
     * plane and nothing on it is a scene binding. */
    if (type != BusMessageType::ConfigPush) return true;

    /* SNAPSHOT BEFORE on_frame().  A header arriving mid-transfer RESETS the
     * reassembler, and the transfer it abandons is exactly the one the head has
     * never been told about — after the reset there is nothing left to name it
     * with. */
    bool prev_active = s_asm.active();
    uint16_t prev_hash = s_asm.expected_hash();
    uint16_t prev_len = (uint16_t)s_asm.payload_len();
    uint16_t prev_gap = s_asm.first_missing();

    ConfigReassembler::Result r = s_asm.on_frame(value, len);
    switch (r) {
        case ConfigReassembler::Result::Started:
        case ConfigReassembler::Result::Restarted:
            ESP_LOGI(TAG, "CONFIG_PUSH %s: %u bytes in %u chunk(s) of %u, "
                     "hash 0x%04X, ttl %u%s", ns,
                     (unsigned)s_asm.payload_len(),
                     (unsigned)s_asm.chunk_count(),
                     (unsigned)s_asm.chunk_payload(), s_asm.expected_hash(),
                     (unsigned)s_asm.ttl(),
                     r == ConfigReassembler::Result::Restarted
                         ? " (ABANDONED the transfer in progress)" : "");
            if (r == ConfigReassembler::Result::Restarted && prev_active) {
                /* SAY WHAT WAS ABANDONED.  Without this the head sees only a
                 * second transfer starting and has no way to learn that the
                 * first one died, nor where it stopped — `ackseq` carries the
                 * first chunk that never arrived, which is precisely what a
                 * repair would resume from. */
                ESP_LOGW(TAG, "the abandoned transfer (hash 0x%04X, %u B) was "
                         "missing chunk %u — ACKing it as INCOMPLETE so the "
                         "head learns it died rather than inferring it",
                         prev_hash, (unsigned)prev_len, (unsigned)prev_gap);
                ack(ConfigAckStatus::Incomplete, prev_hash, prev_len, prev_gap);
            }
            break;
        case ConfigReassembler::Result::Complete: {
            s_transfers++;
            s_ever = true;
            /* Read the hash AND the ack sequence BEFORE take() — take() hands
             * ownership over and resets the reassembler, so every accessor
             * reads zero after it. */
            uint16_t verified = s_asm.expected_hash();
            uint16_t ackseq = s_asm.ack_seq();
            free(s_ready);
            s_ready = s_asm.take(&s_ready_len);
            ESP_LOGI(TAG, "config transfer COMPLETE: %u bytes, hash verified",
                     (unsigned)s_ready_len);
            ack(ConfigAckStatus::Ok, verified, (uint16_t)s_ready_len, ackseq);
            s_armed = false;                    /* stop the retry timer */
            s_interval_ms = kRetryBaseMs;
            break;
        }
        case ConfigReassembler::Result::HashMismatch: {
            s_mismatches++;
            ESP_LOGE(TAG, "config transfer hash MISMATCH (got 0x%04X, wanted "
                     "0x%04X) — DISCARDED and re-requesting.  Every frame's "
                     "CRC-8 passed; only the whole-payload hash sees a missing "
                     "or duplicated frame.", s_asm.actual_hash(),
                     s_asm.expected_hash());
            /* The ACK names the transfer the HEAD announced (its own hash), so
             * the head's check is one comparison for every status; the hash
             * this leaf computed is a leaf-side diagnosis and stays in the log
             * above.  ackseq is 0 BY CONSTRUCTION here: a mismatch is only
             * reachable once every chunk has arrived, so nothing is missing and
             * this is corruption or interleaving, not loss. */
            ack(ConfigAckStatus::HashMismatch, s_asm.expected_hash(), prev_len,
                0);
            /* Re-request promptly but not instantly: an immediate re-request
             * from both ends is how two transfers end up interleaved, which is
             * the very thing the hash just caught. */
            s_interval_ms = kRetryBaseMs;
            s_next_ms = 0;
            s_armed = true;
            break;
        }
        case ConfigReassembler::Result::NoMemory: {
            /* The reassembler has already reset, so the transfer's identity has
             * to come back out of the frame that failed — otherwise this ACK
             * would say "no memory for hash 0000" and be unattributable.  A
             * NoMemory result is only ever produced by a HEADER frame. */
            ConfigChunkHeader hdr{};
            uint16_t seq = 0;
            const uint8_t* content = nullptr;
            size_t content_len = 0;
            uint16_t hash = 0, total = 0;
            if (config_frame_parse(value, len, &hdr, &seq, &content,
                                   &content_len) == ConfigFrameKind::Header) {
                hash = hdr.content_hash;
                total = hdr.total_len;
            }
            ESP_LOGE(TAG, "config transfer could not be buffered (out of "
                     "heap) — DISCARDED.  free heap is the constraint, not "
                     "the wire.");
            /* ACKed, where it used to be silent.  A head that hears nothing
             * cannot tell an out-of-heap leaf from an absent one, and the two
             * want opposite responses: one wants a smaller scene, the other
             * wants a wire diagnosed. */
            ack(ConfigAckStatus::NoMemory, hash, total, 0);
            break;
        }
        case ConfigReassembler::Result::Stray:
            ESP_LOGW(TAG, "config data frame with no header (or out of range) "
                     "— dropped; waiting for a header");
            break;
        case ConfigReassembler::Result::Duplicate:
        case ConfigReassembler::Result::Progress:
        case ConfigReassembler::Result::Ignored:
            break;
    }
    return true;
}

void config_bus_tick(uint32_t now_ms) {
    if (!s_ns[0]) return;
    if (!s_first_request_ms) s_first_request_ms = now_ms;

    if (s_armed && now_ms >= s_next_ms) config_bus_request();

    /* ANNOUNCE ([CXC-CONFIG-BUS-LEAF] must-announce).  A dark or stale panel
     * is indistinguishable from a working one, so silence is the ONE
     * unacceptable response.  Say it on the bus if the bus is up, and on the
     * glass always — the glass is the channel that still works when the head
     * is the thing that died. */
    if (s_ever || !s_armed) return;
    uint32_t waited = now_ms - s_first_request_ms;
    if (waited < kAnnounceAfterMs) return;
    if (s_last_announce_ms && now_ms - s_last_announce_ms < kAnnounceEveryMs)
        return;
    s_last_announce_ms = now_ms;

    char line[160];
    snprintf(line, sizeof(line),
             "NO CONFIG: asked %lu time(s) over %s for %s, nothing arrived in "
             "%lu s", (unsigned long)s_attempts, bus_transport().name(), s_ns,
             (unsigned long)(waited / 1000));
    ESP_LOGE(TAG, "%s", line);
    if (s_announce) s_announce(line);
    if (s_publish && s_status_ns[0]) s_publish(s_status_ns, line);
}

char* config_bus_take(size_t* out_len) {
    char* p = s_ready;
    if (out_len) *out_len = s_ready_len;
    s_ready = nullptr;
    s_ready_len = 0;
    return p;
}

uint32_t config_bus_attempts(void) { return s_attempts; }
uint32_t config_bus_transfers(void) { return s_transfers; }
uint32_t config_bus_mismatches(void) { return s_mismatches; }
uint32_t config_bus_acks_sent(void) { return s_acks_sent; }
uint32_t config_bus_ack_failures(void) { return s_ack_failures; }
bool config_bus_ever_received(void) { return s_ever; }
