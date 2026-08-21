/*
 * announce.cpp — DEVICE SELF-ANNOUNCEMENT, the app-shell handler.
 * [SPEC:CXC-BOARD-HW] — workstream 0.2 of
 * docs/PLAN-fleet-baseline-and-bus-native.md.
 *
 * Read announce_pack.h first: it carries the namespace table, the encodings
 * and the reasoning.  This file is the cadence, the poll handler and the
 * sampling of the three facts only ESP-IDF can answer (free heap, reset
 * reason, uptime).
 *
 * WHY IT IS NOT PURE C++ LIKE restart.cpp.  restart.cpp's whole contract is
 * "did a truthy command arrive, and did we reboot" — both expressible through
 * seams, and worth host-testing because a reboot is not something a test can
 * be allowed to actually do.  This module's contract is "what is true about
 * this board right now", and every one of those facts IS an ESP-IDF call.
 * Injecting `esp_get_free_heap_size` as a function pointer would test that a
 * mock returns what the mock was told to return.  So the split runs the other
 * way: the WIRE-VISIBLE MATH lives in announce_pack.h and is host-parity-
 * tested against the Python oracle ([env:native_announce]), and the plumbing
 * lives here and is proven by an INDEPENDENT MQTT subscriber on the real bus.
 * That is the right division — the parts a machine signal could lie about are
 * the parts that got a second, independent implementation.
 */

#include "announce.h"
#include "announce_pack.h"
#include "bus_transport.h"
#include "dsl.h"
#include "ns_announce.h"

#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_system.h"

static const char* TAG = "announce";

/* The seam spells its default as a literal 0 so it does not depend on this
 * header (see BusTransport::link_health).  If the two ever disagree, a
 * substrate with no error model would start reporting a REAL state — which is
 * the H2 defect wearing a new hat. */
static_assert(kAnnLinkUnknown == 0,
              "BusTransport::link_health()'s default literal 0 must remain "
              "kAnnLinkUnknown");

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

static char s_ns_heartbeat[96];
static char s_ns_build[96];
static char s_ns_transport[96];
static char s_ns_scene[96];
static char s_ns_health[96];

/* The two POLL namespaces.  `_device/broadcast/announce` is the fleet-wide
 * one from the plan; `_device/<id>/announce` is added so a single device can
 * be re-read without waking every board on a shared segment — which matters
 * on CAN, where the fleet-wide poll makes N devices transmit at once.
 *
 * NOTE the shape differs from restart's broadcast (`_device/restart`, no
 * middle segment).  That inconsistency is real and is inherited from the plan
 * rather than invented here; `_device/broadcast/*` is the better shape (it
 * cannot ever collide with a device literally named "restart") and restart's
 * form is the one that should eventually move.  Recorded, not silently
 * "fixed", because changing `_device/restart` would strand every board in the
 * field that is listening for it. */
static char s_ns_poll_device[96];
static const char* const kNsPollBroadcast = "_device/broadcast/announce";

static bool s_inited = false;

/* Latched once at init — constant for the life of the boot. */
static uint32_t s_build_fingerprint = 0;
static uint8_t s_reset_reason = 0;

/* Facts the shell feeds in. */
static uint16_t s_scene_hash = 0;
static bool s_network_up = false;
static bool s_config_stale = false;

/* Cadence. */
static uint32_t s_next_heartbeat_ms = 0;
static uint32_t s_next_full_ms = 0;
static bool s_full_due = true;         /* first tick publishes everything */

/* Last published, for on-change detection.  `s_have_published` is separate
 * from "the values are zero" because 0 is a legitimate value for scene
 * (no scene) and for health flags — without it the first publication of a
 * genuinely-zero field would be indistinguishable from "unchanged". */
static bool s_have_published = false;
static uint8_t s_last_transport = 0;
static uint16_t s_last_scene = 0;
static uint32_t s_last_health = 0;

/* ------------------------------------------------------------------ */
/* Publication                                                         */
/* ------------------------------------------------------------------ */

/* A 32-bit WORD, as 8 lowercase hex digits.
 *
 * ⚠ THE ONE PLACE TO CHANGE WHEN WORKSTREAM F LANDS.  See the ⚠ block in
 * announce_pack.h for why a u32 cannot ride as a NUMBER today: the framed
 * substrates have no int32/float32 discriminator
 * (DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9) and float32's 24-bit
 * mantissa silently mangles a CRC-32.  A build fingerprint that is silently
 * mangled is worse than no fingerprint at all — it would report a DIFFERENT
 * build, which is exactly the question the reflash campaign asks it. */
static void publish_u32(BusTransport& bus, const char* ns, uint32_t v) {
    char hex[9];
    ann_hex32(v, hex);
    bus.publish_state(ns, hex);
}

static void publish_u16(BusTransport& bus, const char* ns, uint16_t v) {
    char hex[5];
    ann_hex16(v, hex);
    bus.publish_state(ns, hex);
}

static uint8_t sample_transport(BusTransport& bus) {
    return ann_pack_transport(
        ann_transport_code_for_name(bus.name()),
        strcmp(bus_transport_selection_source(), "nvs") == 0);
}

static uint32_t sample_health(BusTransport& bus) {
    uint8_t flags = 0;
    if (s_scene_hash)          flags |= kAnnFlagSceneLoaded;
    if (s_config_stale)        flags |= kAnnFlagConfigStale;
    if (bus.connected())       flags |= kAnnFlagConnected;
    if (s_network_up)          flags |= kAnnFlagNetworkUp;
    if (bus.link_has_errors()) flags |= kAnnFlagLinkErrors;
    if (bus.link_listen_only())flags |= kAnnFlagListenOnly;

    return ann_pack_health(esp_get_free_heap_size(), s_reset_reason,
                           bus.link_health(), flags);
}

/* The full set, minus the heartbeat (which has its own faster cadence). */
static void publish_full(BusTransport& bus) {
    uint8_t tr = sample_transport(bus);
    uint32_t health = sample_health(bus);

    publish_u32(bus, s_ns_build, s_build_fingerprint);
    bus.publish_number(s_ns_transport, (double)tr);
    publish_u16(bus, s_ns_scene, s_scene_hash);
    publish_u32(bus, s_ns_health, health);

    s_last_transport = tr;
    s_last_scene = s_scene_hash;
    s_last_health = health;
    s_have_published = true;
}

/* ------------------------------------------------------------------ */
/* Init                                                                */
/* ------------------------------------------------------------------ */

void announce_init(const char* device_id, uint32_t build_fingerprint) {
    if (!device_id || !device_id[0]) {
        ESP_LOGE(TAG, "announce_init with an empty device id — this device "
                 "will NOT appear in any fleet poll.  Nothing was installed.");
        return;
    }
    snprintf(s_ns_heartbeat, sizeof(s_ns_heartbeat), "_device/%s/heartbeat",
             device_id);
    snprintf(s_ns_build,     sizeof(s_ns_build),     "_device/%s/build", device_id);
    snprintf(s_ns_transport, sizeof(s_ns_transport), "_device/%s/transport",
             device_id);
    snprintf(s_ns_scene,     sizeof(s_ns_scene),     "_device/%s/scene", device_id);
    snprintf(s_ns_health,    sizeof(s_ns_health),    "_device/%s/health", device_id);
    snprintf(s_ns_poll_device, sizeof(s_ns_poll_device), "_device/%s/announce",
             device_id);

    s_build_fingerprint = build_fingerprint;

    /* esp_reset_reason() is only meaningful before anything calls
     * esp_restart(), and it is constant for the boot — so latch it here rather
     * than sampling it per announcement.  It is the field that separates "a
     * board somebody just power-cycled" from "a board that panics every 40 s",
     * which from the bus look identical (both show a small uptime). */
    s_reset_reason = (uint8_t)esp_reset_reason();

    s_inited = true;
    s_full_due = true;
    s_next_heartbeat_ms = 0;
    s_next_full_ms = 0;
    s_have_published = false;

    /* WORKSTREAM G2 — ANNOUNCE THE NAMES, NOT ONLY THE VALUES.
     *
     * The ⚠ block in announce.h says CAN IS NOT REACHABLE THROUGH THE BRIDGE
     * and explains why: the wire id is a CRC over the FULL namespace INCLUDING
     * the device id, so a bridge cannot expand `_device/*` without already
     * knowing every device id — the thing being discovered.  Everything above
     * this line publishes VALUES on namespaces a CAN bridge cannot name.  This
     * call publishes the NAMES, over the one well-known root every bridge
     * hardcodes, and that is what makes the five fields above reachable over
     * CAN at all.
     *
     * The list is the six namespaces this device OWNS: the five it publishes
     * plus its own poll, so a tool that learns the names can also address one
     * board without waking a whole segment.  `kNsPollBroadcast` is deliberately
     * NOT in it — it belongs to no device, and every node claiming it would
     * make the bridge's census read as N devices all owning one namespace.
     *
     * These are the static buffers above, so they outlive the call, which is
     * what ns_announce_init requires (it copies nothing — a 2 KB-class part
     * does not need a second copy of six strings). */
    static const char* s_owned[6];
    s_owned[0] = s_ns_heartbeat;
    s_owned[1] = s_ns_build;
    s_owned[2] = s_ns_transport;
    s_owned[3] = s_ns_scene;
    s_owned[4] = s_ns_health;
    s_owned[5] = s_ns_poll_device;
    ns_announce_init(s_owned, 6);

    if (build_fingerprint == 0)
        ESP_LOGE(TAG, "build fingerprint is 0 — gen/build_stamp.h was not "
                 "generated for this build.  Every device would report the "
                 "same fingerprint, which makes a reflash UNVERIFIABLE.  Check "
                 "that cyd/tools/gen_build_stamp.py ran (extra_scripts in "
                 "platformio.ini).");

    ESP_LOGI(TAG, "self-announcement armed for '%s': build %08lx, reset "
             "reason %u, heartbeat every %lu ms, full set every %lu ms, poll "
             "on %s and %s", device_id, (unsigned long)build_fingerprint,
             (unsigned)s_reset_reason, (unsigned long)kAnnHeartbeatIntervalMs,
             (unsigned long)kAnnFullIntervalMs, s_ns_poll_device,
             kNsPollBroadcast);
}

void announce_reset(void) {
    ns_announce_reset();
    s_inited = false;
    s_ns_heartbeat[0] = s_ns_build[0] = s_ns_transport[0] = '\0';
    s_ns_scene[0] = s_ns_health[0] = s_ns_poll_device[0] = '\0';
    s_build_fingerprint = 0;
    s_reset_reason = 0;
    s_scene_hash = 0;
    s_network_up = false;
    s_config_stale = false;
    s_next_heartbeat_ms = s_next_full_ms = 0;
    s_full_due = true;
    s_have_published = false;
    s_last_transport = 0;
    s_last_scene = 0;
    s_last_health = 0;
}

size_t announce_poll_namespaces(const char** out, size_t cap) {
    if (!s_inited || !out || cap < 2) return 0;
    out[0] = s_ns_poll_device;
    out[1] = kNsPollBroadcast;
    return 2;
}

/* ------------------------------------------------------------------ */
/* Facts fed in by the shell                                           */
/* ------------------------------------------------------------------ */

void announce_set_scene_hash(uint16_t content_hash) {
    if (s_scene_hash == content_hash) return;
    s_scene_hash = content_hash;
    s_full_due = true;
}

void announce_set_network_up(bool up) {
    if (s_network_up == up) return;
    s_network_up = up;
    s_full_due = true;
}

void announce_set_config_stale(bool stale) {
    if (s_config_stale == stale) return;
    s_config_stale = stale;
    s_full_due = true;
}

/* ------------------------------------------------------------------ */
/* Poll                                                                */
/* ------------------------------------------------------------------ */

bool announce_on_bus_value(const char* ns, int kind, double num, bool b,
                           const char* str) {
    if (!s_inited || !ns) return false;
    if (strcmp(ns, s_ns_poll_device) != 0 &&
        strcmp(ns, kNsPollBroadcast) != 0)
        return false;

    /* Truthiness through the dsl_truthy canon, exactly like restart — one
     * truthiness rule for the whole control plane, so `1`, `true` and
     * `"announce"` all work and `0` / `""` are a deliberate no-op rather than
     * an accident. */
    bool truthy = false;
    switch (kind) {
        case 0: truthy = dsl_truthy(DslValue::number(num)); break;
        case 1: truthy = dsl_truthy(DslValue::boolean(b)); break;
        case 2: truthy = str && dsl_truthy(DslValue::string(str, strlen(str)));
                break;
        default: break;
    }

    /* NO BOOT-GUARD WINDOW, unlike restart.  A re-delivered restart command
     * loops a device; a re-delivered announce command costs five messages.
     * Guarding it would make the poll unreliable in exactly the first seconds
     * after a reflash — which is when the campaign asks the question. */
    if (truthy) {
        s_full_due = true;
        s_next_heartbeat_ms = 0;     /* answer with a fresh uptime too */
        /* AND THE NAMES.  A poll from a bridge that just restarted is exactly
         * the case where the values are useless without them: five frames
         * carrying five irreversible hashes is not a device report.  Re-arming
         * is idempotent and costs ~33 frames once, paced. */
        ns_announce_arm();
        ESP_LOGI(TAG, "poll on %s — republishing the full set AND re-announcing "
                 "namespace names on %s", ns, kNsAnnounce);
    }
    return true;   /* consumed either way; never routed to the scene */
}

/* ------------------------------------------------------------------ */
/* Cadence                                                             */
/* ------------------------------------------------------------------ */

void announce_tick(uint32_t now_ms) {
    if (!s_inited) return;
    BusTransport& bus = bus_transport();

    /* Names before values, every pass.  It is paced and self-limiting (a few
     * frames per tick, then idle until re-armed), and it goes first because a
     * value published under a name the far side cannot resolve is a dropped
     * frame — so the ordering is not cosmetic on the first boot. */
    ns_announce_tick(now_ms);

    /* ON-CHANGE, checked every tick and not only on the 60 s boundary.  The
     * three shell-fed facts arm s_full_due themselves; transport and health
     * change underneath us (a broker drop, a bus-off, a heap collapse) and
     * nothing would notice for up to a minute otherwise.  Sampling them here
     * is two register reads and a struct read — cheap enough to do per pass,
     * which is what makes a fault VISIBLE within 5 s instead of 60. */
    if (s_have_published && !s_full_due) {
        if (sample_transport(bus) != s_last_transport ||
            s_scene_hash != s_last_scene ||
            sample_health(bus) != s_last_health)
            s_full_due = true;
    }

    if (s_full_due || (int32_t)(now_ms - s_next_full_ms) >= 0) {
        publish_full(bus);
        s_full_due = false;
        s_next_full_ms = now_ms + kAnnFullIntervalMs;
    }

    if ((int32_t)(now_ms - s_next_heartbeat_ms) >= 0) {
        /* UPTIME SECONDS, and PRESENCE is the signal.  The value is useful
         * (it separates a fresh boot from a long-running board) but the
         * MECHANISM is that this message keeps arriving.  A consumer decides
         * a device is gone by MISSING heartbeats, not by reading a field —
         * which is the one liveness rule that works identically on MQTT, UART
         * and CAN.  MQTT's Last Will would detect death faster and is worth
         * adding later, but only as an OPTIMISATION over this rule, never as
         * the mechanism: otherwise UART and CAN nodes get worse liveness
         * semantics than MQTT ones for no designed reason. */
        bus.publish_number(s_ns_heartbeat, (double)(now_ms / 1000u));
        s_next_heartbeat_ms = now_ms + kAnnHeartbeatIntervalMs;
    }
}
