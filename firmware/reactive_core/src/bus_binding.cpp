/*
 * bus_binding.cpp (Arduino) — the ARDUINO sibling of cyd/src/bus_binding.cpp.
 * [SPEC:CXC-REPOINT] [DEC-BUS-BINDING-IS-STATE-NOT-A-COMMAND]
 *                    [DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]
 *                    [DEC-REPOINT-RESCUE-FLOOR-IS-COMPILED-IP]
 *
 * WHERE is this board's ComposeBus?  Same answer, same record shape, same NVS
 * key layout as the ESP-IDF glue — only the platform primitives differ:
 *   NVS      -> Arduino Preferences (namespace "composeio")
 *   Timer    -> Ticker + a flag drained on the main task (esp_timer -> Ticker)
 *   GPIO     -> Arduino pinMode/digitalRead for the two-stage escape hatch
 *   Reboot   -> ESP.restart()
 *   MQTT/WiFi are the SHELL's (controller_main.cpp): this file names no
 *   transport ([DP-CIO-6]); it only computes ANSWERS and persists records.
 *
 * ⛔ EVERY DECISION is delegated to the pure, host-tested bus_binding_core.*
 * (compiled by reference via bus_binding_canonical.cpp).  This file adds NO
 * policy the selftest cannot see — it is the Arduino platform edge only.
 *
 * NVS KEY LAYOUT — IDENTICAL to cyd/src/bus_binding.cpp so a board reflashed
 * between the ESP-IDF and Arduino firmwares KEEPS its binding.  Namespace
 * "composeio", two tiers, letter 'd' (per-device) / 'g' (global).  Keys
 * <=15 chars (the NVS ceiling, which Preferences also enforces):
 *   bus.<t>.name  bus.<t>.mqtt  bus.<t>.store  bus.<t>.docs   (the binding)
 *   bus.<t>.pname bus.<t>.pmqtt bus.<t>.pstore bus.<t>.pdocs  (last known-good)
 *   bus.<t>.state (u8)  bus.<t>.seq (u32)  bus.<t>.reverts (u32)
 *   bus.<t>.why (str)   bus.<t>.deny (u32<-u16)  bus.<t>.tfp (u32<-u16)
 *
 * ⚠ BENCH-UNVERIFIED LIVE PATH.  A green compile proves none of: Preferences
 * survives a reboot, the store+broker round-trip confirms, the Ticker reverts
 * within the window, or a held GPIO clears the right tier.  Those are the
 * [CXC-REPOINT-AC-*] flash-and-bench gate the host selftest cannot stand in
 * for — the SAME bench items as the cyd/ glue.
 */

#ifdef CONTROLLER_CONFIG_FETCH

#include <Arduino.h>
#include <Preferences.h>
#include <Ticker.h>

#include <cstdio>
#include <cstring>

#include "bus_binding.h"        /* cyd/src via -I: the header the shell calls  */
#include "bus_binding_core.h"   /* cyd/src via -I: the pure, host-tested logic */
#include "bus_uri.h"            /* mqtt:// host+port split for the floor        */

/* --- Compiled rescue floor (the ONE address a bus message cannot corrupt) ---
 * [DEC-REPOINT-RESCUE-FLOOR-IS-COMPILED-IP]: a COMPILED IP, never a `.local`
 * mDNS name (mDNS does not resolve on these boards; the escape hatch erases
 * NVS -> boots this floor -> always works, so the device is brick-proof).
 * These come from reactive_core's existing build_flags (platformio.ini): the
 * bus master IS mqtt-broker.local for store/mqtt.  The mqtt floor is DERIVED from
 * the store host + MQTT_PORT so it inherits the compiled IP and never the
 * `.local` MQTT_BROKER default the demo shell carries. */
#ifndef BUS_STORAGE_URL
#define BUS_STORAGE_URL "http://mqtt-broker.local:8420"
#endif
#ifndef DOCUMENT_STORE_URL
#define DOCUMENT_STORE_URL "http://mqtt-broker.local:8421"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif

/* --- Per-board escape-hatch + capability sentinels ---------------------------
 * The HONEST DEFAULTS from cyd/include/bus_binding_board.h, declared locally so
 * this Arduino TU does not entangle the whole build's include path with cyd's
 * include/ (which also carries a creds-bearing wifi_config.h).  Absent a
 * bench-verified GPIO0-free-at-reset check ([DD-REPOINT-BUTTON-SCOPE]), a board
 * has NO hatch (pin -1, announced via flags bit 0) and NO captive-portal
 * budget (0).  A real pin/level is set ONLY via a build flag AFTER the bench
 * verifies it — asserting one here unverified would make the ANNOUNCE flag LIE.
 * None of office_fan / office_hepa / dual-throttle is bench-verified, so all
 * three ride the -1 sentinel. */
#ifndef CXC_ESCAPE_HATCH_PIN
#define CXC_ESCAPE_HATCH_PIN (-1)
#endif
#ifndef CXC_ESCAPE_HATCH_ACTIVE_LEVEL
#define CXC_ESCAPE_HATCH_ACTIVE_LEVEL (0)
#endif
#ifndef CXC_ESCAPE_HATCH_SHORT_MS
#define CXC_ESCAPE_HATCH_SHORT_MS (3000)
#endif
#ifndef CXC_ESCAPE_HATCH_LONG_MS
#define CXC_ESCAPE_HATCH_LONG_MS (10000)
#endif
#ifndef CXC_HAS_CAPTIVE_PORTAL
#define CXC_HAS_CAPTIVE_PORTAL (0)
#endif

#define CXC_NVS_NAMESPACE "composeio"

/* ------------------------------------------------------------------ */
/* Module state                                                        */
/* ------------------------------------------------------------------ */

static bool       s_resolved = false;
static BusBinding s_active;
static BindSource s_source = BindSource::Unknown;
static bool       s_is_trial = false;
static BindTier   s_active_tier = BindTier::Compiled;

static bool s_button_forced = false;   /* this boot bypasses both NVS tiers */
static bool s_button_cleared = false;  /* drives the panel feedback          */

static char s_device_id[24] = "";
static char s_ns_perdev[96] = "";      /* _device/<id>/bus            */
static char s_ns_broadcast[32] = "";   /* _device/broadcast/bus       */
static char s_ns_status[96] = "";      /* _device/<id>/status         */
static char s_ns_confirm[96] = "";     /* _device/<id>/bus_confirm    */
static BusBindingPublishFn s_publish = nullptr;

/* trial confirm bookkeeping */
static bool     s_trial_active = false;
static uint32_t s_trial_seq = 0;
static bool     s_store_ok = false;
static bool     s_echo_ok = false;
static bool     s_store_required = true;
static char     s_store_note[16] = "";
static char     s_pending_reason[24] = "timeout";
static bool     s_reason_explicit = false;

/* The revert window is a real one-shot timer ARMED BEFORE WiFi so a DHCP/TLS
 * hang still reverts ([CXC-REPOINT-COMMIT-CONFIRMED] ordering-is-load-bearing).
 * The Ticker sets a flag; the mutation (NVS write + publish + reboot) runs on
 * the MAIN task in bus_binding_tick(), because Preferences and PubSubClient are
 * not safe to touch from the Ticker's esp_timer task while loop() uses them. */
static Ticker          s_revert_ticker;
static volatile bool   s_window_elapsed = false;

static void default_reboot(void) {
    Serial.println("[bus_binding] restarting to apply/revert bus binding");
    Serial.flush();
    delay(50);                          /* let a final publish flush */
    ESP.restart();
}
static BusBindingRebootFn s_reboot = default_reboot;

void bus_binding_set_reboot(BusBindingRebootFn reboot) {
    s_reboot = reboot ? reboot : default_reboot;
}

/* ------------------------------------------------------------------ */
/* NVS plumbing (Arduino Preferences)                                  */
/* ------------------------------------------------------------------ */

static char tier_letter(BindTier t) {
    return t == BindTier::PerDevice ? 'd' : 'g';
}

static void keyname(char* out, size_t cap, BindTier t, const char* field) {
    snprintf(out, cap, "bus.%c.%s", tier_letter(t), field);
}

static void pf_get_string(Preferences& p, BindTier t, const char* field,
                          char* dst, size_t cap) {
    dst[0] = '\0';
    char k[16];
    keyname(k, sizeof(k), t, field);
    if (p.isKey(k)) p.getString(k, dst, cap);
}

static void pf_set_string(Preferences& p, BindTier t, const char* field,
                          const char* val) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    p.putString(k, val ? val : "");
}

static uint32_t pf_get_num(Preferences& p, BindTier t, const char* field) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    return p.getUInt(k, 0);
}

static void pf_set_num(Preferences& p, BindTier t, const char* field,
                       uint32_t v) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    p.putUInt(k, v);
}

/* Load one tier's full record.  A tier with no `state` key is Empty. */
static bool load_record(BindTier t, BindRecord* r) {
    memset(r, 0, sizeof(*r));
    Preferences p;
    if (!p.begin(CXC_NVS_NAMESPACE, /*readOnly=*/true)) return false;

    char k[16];
    keyname(k, sizeof(k), t, "state");
    if (!p.isKey(k)) { p.end(); return false; }
    r->state = (BindState)p.getUChar(k, 0);

    pf_get_string(p, t, "name",  r->cur.name,  sizeof(r->cur.name));
    pf_get_string(p, t, "mqtt",  r->cur.mqtt,  sizeof(r->cur.mqtt));
    pf_get_string(p, t, "store", r->cur.store, sizeof(r->cur.store));
    pf_get_string(p, t, "docs",  r->cur.docs,  sizeof(r->cur.docs));
    pf_get_string(p, t, "pname",  r->prev.name,  sizeof(r->prev.name));
    pf_get_string(p, t, "pmqtt",  r->prev.mqtt,  sizeof(r->prev.mqtt));
    pf_get_string(p, t, "pstore", r->prev.store, sizeof(r->prev.store));
    pf_get_string(p, t, "pdocs",  r->prev.docs,  sizeof(r->prev.docs));
    r->seq     = pf_get_num(p, t, "seq");
    r->reverts = pf_get_num(p, t, "reverts");
    r->deny    = (uint16_t)pf_get_num(p, t, "deny");
    r->tfp     = (uint16_t)pf_get_num(p, t, "tfp");
    pf_get_string(p, t, "why", r->why, sizeof(r->why));
    p.end();
    return true;
}

static bool store_record(BindTier t, const BindRecord* r) {
    Preferences p;
    if (!p.begin(CXC_NVS_NAMESPACE, /*readOnly=*/false)) {
        Serial.println("[bus_binding] Preferences open failed — binding NOT stored");
        return false;
    }
    char k[16];
    keyname(k, sizeof(k), t, "state");
    p.putUChar(k, (uint8_t)r->state);
    pf_set_string(p, t, "name",  r->cur.name);
    pf_set_string(p, t, "mqtt",  r->cur.mqtt);
    pf_set_string(p, t, "store", r->cur.store);
    pf_set_string(p, t, "docs",  r->cur.docs);
    pf_set_string(p, t, "pname",  r->prev.name);
    pf_set_string(p, t, "pmqtt",  r->prev.mqtt);
    pf_set_string(p, t, "pstore", r->prev.store);
    pf_set_string(p, t, "pdocs",  r->prev.docs);
    pf_set_num(p, t, "seq",     r->seq);
    pf_set_num(p, t, "reverts", r->reverts);
    pf_set_num(p, t, "deny",    r->deny);
    pf_set_num(p, t, "tfp",     r->tfp);
    pf_set_string(p, t, "why", r->why);
    p.end();                            /* Preferences commits on end() */
    return true;
}

/* Erase every key of one tier (the escape-hatch clear). */
static void erase_tier(BindTier t) {
    Preferences p;
    if (!p.begin(CXC_NVS_NAMESPACE, /*readOnly=*/false)) return;
    static const char* kFields[] = {
        "name","mqtt","store","docs","pname","pmqtt","pstore","pdocs",
        "state","seq","reverts","why","deny","tfp" };
    char k[16];
    for (size_t i = 0; i < sizeof(kFields) / sizeof(kFields[0]); i++) {
        keyname(k, sizeof(k), t, kFields[i]);
        if (p.isKey(k)) p.remove(k);
    }
    p.end();
}

static void load_view(BindTier t, BindTierView* v) {
    BindRecord r;
    if (load_record(t, &r)) {
        v->state = r.state;
        v->binding = r.cur;
    } else {
        v->state = BindState::Empty;
        binding_clear(&v->binding);
    }
}

/* ------------------------------------------------------------------ */
/* The compiled floor — the rescue-only default                        */
/* ------------------------------------------------------------------ */

static BusBinding compiled_floor(void) {
    BusBinding b;
    binding_clear(&b);
    snprintf(b.name,  sizeof(b.name),  "compiled");
    snprintf(b.store, sizeof(b.store), "%s", BUS_STORAGE_URL);
    snprintf(b.docs,  sizeof(b.docs),  "%s", DOCUMENT_STORE_URL);
    /* mqtt floor = compiled IP derived from the store host + MQTT_PORT, so it
     * is NEVER the `.local` MQTT_BROKER default ([DEC-REPOINT-RESCUE-FLOOR-IS-
     * COMPILED-IP]).  An explicit -DMQTT_BROKER_URI overrides. */
#ifdef MQTT_BROKER_URI
    snprintf(b.mqtt, sizeof(b.mqtt), "%s", MQTT_BROKER_URI);
#else
    {
        char host[64];
        uint16_t port = 0;
        bus_uri_host_port(BUS_STORAGE_URL, host, sizeof(host), &port, 0);
        snprintf(b.mqtt, sizeof(b.mqtt), "mqtt://%s:%u", host,
                 (unsigned)MQTT_PORT);
    }
#endif
    return b;
}

/* ------------------------------------------------------------------ */
/* Resolve-once                                                        */
/* ------------------------------------------------------------------ */

static void resolve(void) {
    if (s_resolved) return;
    s_resolved = true;

    BusBinding floor = compiled_floor();
    BindTierView pd, gl;
    load_view(BindTier::PerDevice, &pd);
    load_view(BindTier::Global, &gl);

    BindResolution r = binding_resolve(&pd, &gl, &floor, s_button_forced);
    s_active = r.active;
    s_source = r.source;
    s_is_trial = r.is_trial;
    s_active_tier = r.tier;

    Serial.printf("[bus_binding] name='%s' mqtt=%s (tier=%s source=%u trial=%d)\n",
                  s_active.name, s_active.mqtt,
                  r.tier == BindTier::PerDevice ? "per-device"
                      : r.tier == BindTier::Global ? "global" : "compiled",
                  (unsigned)s_source, (int)s_is_trial);
}

/* ------------------------------------------------------------------ */
/* Read-site accessors                                                 */
/* ------------------------------------------------------------------ */

const char* bus_binding_active_mqtt(void)  { resolve(); return s_active.mqtt; }
const char* bus_binding_active_store(void) { resolve(); return s_active.store; }
const char* bus_binding_active_docs(void)  { resolve(); return s_active.docs; }
const char* bus_binding_active_name(void)  { resolve(); return s_active.name; }
uint8_t     bus_binding_active_source(void){ resolve(); return (uint8_t)s_source; }
bool        bus_binding_active_is_trial(void){ resolve(); return s_is_trial; }

/* ------------------------------------------------------------------ */
/* Escape hatch + boot init                                            */
/* ------------------------------------------------------------------ */

/* Measure how long the escape-hatch button is held at boot, in ms, capped at
 * the long threshold.  Returns 0 immediately when the board has no hatch pin
 * or the button is not pressed — so a normal boot pays NOTHING. */
static uint32_t measure_hatch_hold(void) {
#if CXC_ESCAPE_HATCH_PIN >= 0
    pinMode(CXC_ESCAPE_HATCH_PIN,
            CXC_ESCAPE_HATCH_ACTIVE_LEVEL == 0 ? INPUT_PULLUP : INPUT_PULLDOWN);
    auto pressed = []() {
        return digitalRead(CXC_ESCAPE_HATCH_PIN) == CXC_ESCAPE_HATCH_ACTIVE_LEVEL;
    };
    if (!pressed()) return 0;           /* not held — normal boot, no delay */

    uint32_t start = millis();
    uint32_t held = 0;
    while (pressed()) {
        delay(20);
        held = millis() - start;
        if (held >= CXC_ESCAPE_HATCH_LONG_MS) break;   /* long is decisive */
    }
    return held;
#else
    return 0;
#endif
}

static void revert_ticker_cb(void) { s_window_elapsed = true; }

void bus_binding_boot_init(const char* device_id, uint32_t confirm_window_s) {
    if (device_id && device_id[0])
        snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);

    /* Two-stage escape hatch ([DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]). */
    uint32_t hold = measure_hatch_hold();
    HatchAction act = binding_hatch_action(hold, CXC_ESCAPE_HATCH_SHORT_MS,
                                           CXC_ESCAPE_HATCH_LONG_MS);
    if (act == HatchAction::ClearPerDevice) {
        Serial.printf("[bus_binding] ESCAPE HATCH (short, %lums): clearing "
                      "PER-DEVICE tier\n", (unsigned long)hold);
        erase_tier(BindTier::PerDevice);
        s_button_forced = true;
        s_button_cleared = true;
    } else if (act == HatchAction::ClearBoth) {
        Serial.printf("[bus_binding] ESCAPE HATCH (long, %lums): clearing BOTH "
                      "NVS tiers\n", (unsigned long)hold);
        erase_tier(BindTier::PerDevice);
        erase_tier(BindTier::Global);
        s_button_forced = true;
        s_button_cleared = true;
    }

    resolve();

    /* Arm the commit-confirmed revert timer BEFORE the network is brought up
     * ([CXC-REPOINT-COMMIT-CONFIRMED] ordering-is-load-bearing): a DHCP/TLS
     * hang after this point still reverts, because the Ticker is scheduled and
     * fires from the esp_timer task independent of loop(). */
    if (s_is_trial) {
        BindRecord r;
        if (load_record(s_active_tier, &r)) {
            s_trial_active = true;
            s_trial_seq = r.seq;
            s_store_ok = false;
            s_echo_ok = false;
            s_window_elapsed = false;
            snprintf(s_pending_reason, sizeof(s_pending_reason), "timeout");
            s_revert_ticker.once((float)confirm_window_s, revert_ticker_cb);
            Serial.printf("[bus_binding] TRIAL binding '%s' (seq %lu) — revert "
                          "armed at %lus\n", s_active.name,
                          (unsigned long)s_trial_seq,
                          (unsigned long)confirm_window_s);
        }
    }
}

bool bus_binding_button_cleared(void) { return s_button_cleared; }

/* ------------------------------------------------------------------ */
/* Confirm planes                                                      */
/* ------------------------------------------------------------------ */

static void try_confirm(void) {
    if (!s_trial_active) return;
    /* The EXACT gate the host test proves (binding_confirm_ready): the broker
     * echo is always required; the store plane only under config:http. */
    BindConfirmGate gate = { s_store_required, s_store_ok, s_echo_ok };
    if (!binding_confirm_ready(&gate)) return;

    BindRecord r;
    if (load_record(s_active_tier, &r)) {
        binding_confirm(&r);
        store_record(s_active_tier, &r);
    }
    s_revert_ticker.detach();
    s_trial_active = false;
    s_is_trial = false;
    Serial.printf("[bus_binding] commit-confirmed: binding '%s' KEPT%s\n",
                  s_active.name, s_store_required ? " (both planes)"
                                                  : " (broker only)");
    if (s_publish && s_ns_status[0]) {
        char msg[96];
        /* Note 2: when the store plane was vacuously satisfied (config:bus /
         * cached) the ack MUST disclose it, so a half-exercised gate never
         * reads as a full two-plane confirm. */
        if (s_store_required)
            snprintf(msg, sizeof(msg), "bus=%s confirmed", s_active.name);
        else
            snprintf(msg, sizeof(msg),
                     "bus=%s confirmed (broker only, config_source=%s)",
                     s_active.name, s_store_note[0] ? s_store_note : "bus");
        s_publish(s_ns_status, msg);
    }
}

void bus_binding_note_store_ok(void) {
    if (!s_trial_active) return;
    s_store_ok = true;
    /* the store plane recovered — a store-* reason recorded on an earlier
     * failed attempt is now stale; let the revert re-derive from the true
     * final plane state (e.g. broker-no-echo) if a revert still happens. */
    s_reason_explicit = false;
    try_confirm();
}

void bus_binding_note_store_vacuous(const char* config_source) {
    s_store_required = false;
    if (config_source && config_source[0])
        snprintf(s_store_note, sizeof(s_store_note), "%s", config_source);
    if (s_trial_active) try_confirm();
}

void bus_binding_note_echo(uint32_t seq) {
    if (!s_trial_active || seq != s_trial_seq) return;
    s_echo_ok = true;
    try_confirm();
}

uint32_t    bus_binding_trial_seq(void) { return s_trial_seq; }
const char* bus_binding_confirm_namespace(void) { return s_ns_confirm; }

void bus_binding_note_reason(const char* reason) {
    if (reason && reason[0]) {
        snprintf(s_pending_reason, sizeof(s_pending_reason), "%s", reason);
        s_reason_explicit = true;       /* overrides the derived plane reason */
    }
}

/* ------------------------------------------------------------------ */
/* Control plane                                                       */
/* ------------------------------------------------------------------ */

void bus_binding_control_init(const char* device_id,
                              BusBindingPublishFn publish) {
    s_publish = publish;
    if (device_id && device_id[0])
        snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);
    if (!s_device_id[0]) return;
    snprintf(s_ns_perdev, sizeof(s_ns_perdev), "_device/%s/bus", s_device_id);
    snprintf(s_ns_broadcast, sizeof(s_ns_broadcast), "_device/broadcast/bus");
    snprintf(s_ns_status, sizeof(s_ns_status), "_device/%s/status", s_device_id);
    snprintf(s_ns_confirm, sizeof(s_ns_confirm), "_device/%s/bus_confirm",
             s_device_id);
}

size_t bus_binding_control_namespaces(const char** out, size_t cap) {
    if (!s_ns_perdev[0] || !out) return 0;
    size_t n = 0;
    if (cap > n) out[n++] = s_ns_perdev;
    if (cap > n) out[n++] = s_ns_broadcast;
    return n;
}

const char* bus_binding_announce_namespace(void) { return s_ns_perdev; }

static void ack(const char* msg) {
    if (s_publish && s_ns_status[0]) s_publish(s_ns_status, msg);
}

/* Note 3 ([CXC-REPOINT-PAYLOAD]): an ignored unknown key must appear on the
 * WIRE ack, not only in a log nobody off-device can read. */
static void ack_mark_unknown_key(char* buf, size_t cap) {
    static const char kMark[] = " (UNKNOWN KEY IGNORED)";
    size_t len = strlen(buf);
    if (len + sizeof(kMark) <= cap)
        snprintf(buf + len, cap - len, "%s", kMark);
}

bool bus_binding_on_bus_value(const char* ns, int kind, double num, bool b,
                              const char* str) {
    (void)num;
    (void)b;
    if (!ns) return false;
    bool is_perdev = s_ns_perdev[0] && strcmp(ns, s_ns_perdev) == 0;
    bool is_bcast  = s_ns_broadcast[0] && strcmp(ns, s_ns_broadcast) == 0;
    if (!is_perdev && !is_bcast) return false;   /* not a binding namespace */

    /* A binding is a NAMED string payload; anything else is reported. */
    if (kind != 2 /* String */ || !str || !str[0]) {
        Serial.printf("[bus_binding] %s: expected a `bus=… mqtt=…` string — "
                      "ignored\n", ns);
        ack("bus REFUSED (non-string value)");
        return true;
    }

    BindParseResult p = binding_parse(str);
    if (p.status != BindParse::Ok) {
        Serial.printf("[bus_binding] %s REFUSED: %s\n", ns, p.error);
        char msg[96];
        snprintf(msg, sizeof(msg), "bus REFUSED: %s", p.error);
        ack(msg);
        return true;
    }

    BindTier target = is_perdev ? BindTier::PerDevice : BindTier::Global;

    /* A per-device publish always targets the (new) active tier; a broadcast
     * publish arms only when the per-device tier is empty, otherwise it is
     * CACHED ([CXC-REPOINT-PRECEDENCE] cross-tier-caching). */
    BindTierView pd;
    load_view(BindTier::PerDevice, &pd);
    bool target_is_active =
        (target == BindTier::PerDevice) ||
        (target == BindTier::Global && pd.state == BindState::Empty);

    /* Note 5: an UNPOPULATED target tier merges onto the COMPILED FLOOR — the
     * tier-independent default — NOT s_active, so a partial publish cannot leak
     * the per-device active binding into a never-set GLOBAL tier. */
    BindRecord r;
    bool populated = load_record(target, &r);
    BusBinding base = populated ? r.cur : compiled_floor();
    BusBinding newb = binding_merge(&base, &p);
    if (!populated) memset(&r, 0, sizeof(r));

    if (p.unknown_key)
        Serial.printf("[bus_binding] %s: unknown key(s) present (UNKNOWN KEY "
                      "IGNORED)\n", ns);

    if (!target_is_active) {
        /* cross-tier cache: validate + write as CONFIRMED, no trial, no reboot */
        r.cur = newb;
        r.state = BindState::Confirmed;
        store_record(target, &r);
        char msg[96];
        snprintf(msg, sizeof(msg), "bus=%s cached (global; per-device pinned)",
                 newb.name);
        if (p.unknown_key) ack_mark_unknown_key(msg, sizeof(msg));
        ack(msg);
        Serial.printf("[bus_binding] %s\n", msg);
        return true;
    }

    /* the target IS active — run the commit-confirmed arm decision */
    char ackbuf[96];
    BindArm a = binding_arm_trial(&r, &newb, ackbuf, sizeof(ackbuf));
    if (p.unknown_key) ack_mark_unknown_key(ackbuf, sizeof(ackbuf));
    ack(ackbuf);
    switch (a) {
        case BindArm::Armed:
            /* persist the trial and RESTART onto it.  The trial boot arms the
             * revert timer in bus_binding_boot_init before wifi. */
            store_record(target, &r);
            Serial.printf("[bus_binding] %s: %s — restarting onto trial\n",
                          ns, ackbuf);
            s_reboot();
            break;
        case BindArm::NoOp:
            Serial.printf("[bus_binding] %s: %s (absorbing)\n", ns, ackbuf);
            break;
        case BindArm::RefusedTrial:
        case BindArm::RefusedDeny:
            Serial.printf("[bus_binding] %s: %s\n", ns, ackbuf);
            break;
    }
    return true;
}

/* ------------------------------------------------------------------ */
/* Announce                                                            */
/* ------------------------------------------------------------------ */

uint32_t bus_binding_announce_word(void) {
    resolve();
    uint16_t fp = binding_fingerprint(&s_active);

    AnnState st = AnnState::Confirmed;
    if (s_source == BindSource::ButtonCleared) st = AnnState::Confirmed;
    else if (s_is_trial) st = AnnState::Trial;

    BindRecord r;
    uint8_t reverts = 0;
    if (s_active_tier != BindTier::Compiled && load_record(s_active_tier, &r)) {
        reverts = binding_reverts_saturated(&r);
        if (r.deny != 0) st = AnnState::Refused;
    }

    uint8_t flags = 0;
#if CXC_ESCAPE_HATCH_PIN < 0
    flags |= kFlagNoEscapeHatch;
#endif
#if CXC_HAS_CAPTIVE_PORTAL == 0
    flags |= kFlagNoCaptivePortal;
#endif
    return binding_announce_word(fp, st, s_source, reverts, flags);
}

/* ------------------------------------------------------------------ */
/* Periodic cadence                                                    */
/* ------------------------------------------------------------------ */

static uint32_t s_next_announce_ms = 0;
static uint32_t s_next_echo_ms = 0;
static const uint32_t kAnnounceIntervalMs = 60000;  /* fleet census cadence */
static const uint32_t kEchoIntervalMs = 2000;       /* trial confirm probe  */

void bus_binding_tick(uint32_t now_ms) {
    /* THE REVERT, run on the main task when the Ticker's window has elapsed
     * ([CXC-REPOINT-REVERT]).  Done here (not in the Ticker callback) so the
     * NVS write + publish + reboot never race loop()'s Preferences/PubSubClient
     * use.  Runs even before s_publish is set (a pre-network revert is valid). */
    if (s_trial_active && s_window_elapsed) {
        s_window_elapsed = false;
        Serial.println("[bus_binding] commit-confirmed WINDOW ELAPSED — reverting");
        BindRecord rr;
        if (load_record(s_active_tier, &rr)) {
            /* name the plane that was still outstanding rather than a bare
             * "timeout", UNLESS a more specific cause (no-wifi/store-http-<code>)
             * was already recorded via bus_binding_note_reason. */
            const char* reason = s_pending_reason;
            if (!s_reason_explicit)
                reason = binding_default_reason(s_store_required, s_store_ok,
                                                s_echo_ok);
            binding_revert(&rr, reason);
            store_record(s_active_tier, &rr);
            if (s_publish && s_ns_status[0]) {
                char msg[128];
                snprintf(msg, sizeof(msg), "bus REVERTED from %s: %s",
                         s_active.name, reason);
                s_publish(s_ns_status, msg);
            }
        }
        s_trial_active = false;
        s_reboot();
        return;                         /* reboot normally does not return */
    }

    if (!s_publish) return;

    if (s_ns_perdev[0] && (now_ms >= s_next_announce_ms || s_next_announce_ms == 0)) {
        s_next_announce_ms = now_ms + kAnnounceIntervalMs;
        char hex[9];
        snprintf(hex, sizeof(hex), "%08lx",
                 (unsigned long)bus_binding_announce_word());
        s_publish(s_ns_perdev, hex);
    }

    /* During a trial, keep publishing the confirm seq so the broker echoes it
     * back to us — that returned value is the broker plane of
     * [CXC-REPOINT-CONFIRM].  ⚠ whether this actually confirms is a bench fact. */
    if (s_trial_active && s_ns_confirm[0] &&
        (now_ms >= s_next_echo_ms || s_next_echo_ms == 0)) {
        s_next_echo_ms = now_ms + kEchoIntervalMs;
        char seq[16];
        snprintf(seq, sizeof(seq), "%lu", (unsigned long)s_trial_seq);
        s_publish(s_ns_confirm, seq);
    }
}

/* ------------------------------------------------------------------ */
/* Reset (host-test isolation; a no-op cost in firmware)               */
/* ------------------------------------------------------------------ */

void bus_binding_reset(void) {
    s_resolved = false;
    binding_clear(&s_active);
    s_source = BindSource::Unknown;
    s_is_trial = false;
    s_active_tier = BindTier::Compiled;
    s_button_forced = false;
    s_button_cleared = false;
    s_device_id[0] = s_ns_perdev[0] = s_ns_broadcast[0] = '\0';
    s_ns_status[0] = s_ns_confirm[0] = '\0';
    s_publish = nullptr;
    s_trial_active = false;
    s_trial_seq = 0;
    s_store_ok = s_echo_ok = false;
    s_store_required = true;
    s_store_note[0] = '\0';
    s_reason_explicit = false;
    snprintf(s_pending_reason, sizeof(s_pending_reason), "timeout");
    s_revert_ticker.detach();
    s_window_elapsed = false;
    s_reboot = default_reboot;
    s_next_announce_ms = 0;
    s_next_echo_ms = 0;
}

#endif /* CONTROLLER_CONFIG_FETCH */
