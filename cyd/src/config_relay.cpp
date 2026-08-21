/*
 * config_relay.cpp — see config_relay.h.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY] [CXC-CONFIG-BUS-TTL]
 */

#include "config_relay.h"
#include "config_chunk.h"

#include <cstdio>
#include <cstring>

#include "esp_log.h"

static const char* TAG = "config_relay";

static char s_device[32] = "relay";
static char s_status_ns[128] = "";
static ConfigRelaySendFn s_down = nullptr;
static ConfigRelaySendFn s_up = nullptr;
static ConfigRelayAnnounceFn s_announce = nullptr;

static uint32_t s_down_frames = 0;
static uint32_t s_up_frames = 0;
static uint32_t s_ttl_expiries = 0;
static uint32_t s_drops = 0;

void config_relay_init(const char* device_id, ConfigRelaySendFn downstream,
                       ConfigRelaySendFn upstream,
                       ConfigRelayAnnounceFn announce) {
    if (device_id && device_id[0])
        snprintf(s_device, sizeof(s_device), "%s", device_id);
    snprintf(s_status_ns, sizeof(s_status_ns), "_device/%s/status", s_device);
    s_down = downstream;
    s_up = upstream;
    s_announce = announce;
    ESP_LOGI(TAG, "relay '%s' up: forwarding chunk-by-chunk in BOTH "
             "directions, reassembling NOTHING (only the endpoint "
             "reassembles).  TTL seed %u, decremented per hop; expiry is "
             "ANNOUNCED, never silent.", s_device, (unsigned)kConfigTtlSeed);
}

static void announce(const char* text) {
    ESP_LOGE(TAG, "%s", text);
    if (s_announce && s_status_ns[0]) s_announce(s_status_ns, text);
}

/* The one shared body.  Direction only decides which send function runs and
 * which counter moves — the FORWARDING RULE is identical, which is the point:
 * a relay has no policy, only a wire. */
static void forward(uint16_t ns_hash, BusMessageType type, uint8_t scope_bits,
                    const uint8_t* value, size_t len, ConfigRelaySendFn send,
                    uint32_t* counter, const char* dir) {
    if (!send) return;

    /* NON-CONFIG TRAFFIC IS FORWARDED TOO, UNTOUCHED.  A relay that only
     * carried config would leave the leaf able to receive a scene and unable
     * to receive the states that scene binds — a display that renders once and
     * never moves.  State traffic has no TTL and needs none: it is a single
     * frame, so a loop is bounded by whatever the loop's own rate is, and
     * inventing a TTL for it would mean inventing a header for it. */
    if (type != BusMessageType::ConfigPush) {
        if (send(ns_hash, type, scope_bits, value, len)) (*counter)++;
        else s_drops++;
        return;
    }

    /* A CONFIG_PUSH frame: copy (the sink's buffer is the driver's), decrement
     * the TTL if this is the header, forward.  The copy is one FRAME — at most
     * 252 bytes on UART, 8 on CAN — never a transfer.  That distinction is the
     * whole of [CXC-CONFIG-BUS-RELAY]. */
    uint8_t frame[256];
    if (len > sizeof(frame)) {
        s_drops++;
        char msg[128];
        snprintf(msg, sizeof(msg), "relay %s: %u-byte config frame exceeds "
                 "the relay's per-frame buffer — DROPPED", s_device,
                 (unsigned)len);
        announce(msg);
        return;
    }
    memcpy(frame, value, len);

    if (!config_ttl_decrement(frame, len)) {
        s_ttl_expiries++;
        char msg[160];
        snprintf(msg, sizeof(msg),
                 "TTL EXPIRED at relay %s: config transfer for ns hash 0x%04X "
                 "discarded %s. This is a LOOP or a chain deeper than the "
                 "backstop, not a depth limit anyone configured.",
                 s_device, ns_hash, dir);
        announce(msg);
        return;
    }

    if (send(ns_hash, type, scope_bits, frame, len)) (*counter)++;
    else s_drops++;
}

void config_relay_from_upstream(uint16_t ns_hash, BusMessageType type,
                                uint8_t scope_bits, const uint8_t* value,
                                size_t len, void* ud) {
    (void)ud;
    forward(ns_hash, type, scope_bits, value, len, s_down, &s_down_frames,
            "on its way downstream");
}

void config_relay_from_downstream(uint16_t ns_hash, BusMessageType type,
                                  uint8_t scope_bits, const uint8_t* value,
                                  size_t len, void* ud) {
    (void)ud;
    /* A leaf's CONFIG_REQUEST and ACK travel this way.  Without it the
     * conversation is one-way and every transfer ends in the leaf's announce
     * timer — which would look exactly like a dead head. */
    forward(ns_hash, type, scope_bits, value, len, s_up, &s_up_frames,
            "on its way upstream");
}

uint32_t config_relay_down_frames(void) { return s_down_frames; }
uint32_t config_relay_up_frames(void) { return s_up_frames; }
uint32_t config_relay_ttl_expiries(void) { return s_ttl_expiries; }
uint32_t config_relay_drops(void) { return s_drops; }
