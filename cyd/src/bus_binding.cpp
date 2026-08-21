/*
 * bus_binding.cpp — see bus_binding.h.  [SPEC:CXC-REPOINT]
 *
 * The ESP-IDF sibling of config_source.cpp, and deliberately shaped like it:
 * NVS-resident, resolve-once, loud-refusal-never-silent-fallback, validate-on-
 * write.  It persists TWO tiers of the binding record, reads the escape-hatch
 * GPIO, arms the commit-confirmed revert timer, and hands the read sites the
 * active tier's addresses.  Every DECISION it makes it delegates to the pure,
 * host-tested bus_binding_core.* — this file adds no policy the selftest cannot
 * see.
 *
 * NVS KEY LAYOUT (namespace "composeio", the SAME one config_source uses so
 * recovery erases ONE namespace).  Two tiers, letter 'd' (per-device) / 'g'
 * (global), same fields.  Keys are <=15 chars (the NVS ceiling):
 *   bus.<t>.name  bus.<t>.mqtt  bus.<t>.store  bus.<t>.docs   (the binding)
 *   bus.<t>.pname bus.<t>.pmqtt bus.<t>.pstore bus.<t>.pdocs  (last known-good)
 *   bus.<t>.state (u8)  bus.<t>.seq (u32)  bus.<t>.reverts (u32)
 *   bus.<t>.why (str)   bus.<t>.deny (u32<-u16)  bus.<t>.tfp (u32<-u16)
 * `bus.<t>.tfp` is ONE field beyond spec-74's original key list — it holds the
 * fingerprint the consecutive-revert counter is counting, which makes the
 * bounce latch exact.  `bus.d.reverts`/`bus.g.reverts` are 13 chars, `tfp` is
 * 9 — comfortably inside 15 ([CXC-REPOINT-PRECEDENCE] key-length-note).
 *
 * ⚠ BENCH-UNVERIFIED LIVE PATH.  A green compile proves none of: NVS survives
 * a reboot, the store+broker round-trip confirms, the timer reverts within the
 * window, or a held GPIO clears the right tier.  Those are [CXC-REPOINT-AC-*],
 * the flash-and-bench gate the host selftest cannot stand in for.
 */

#include "bus_binding.h"
#include "bus_binding_core.h"
#include "bus_binding_board.h"
#include "wifi_config.h"

#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"
#include "nvs.h"
#include "nvs_flash.h"
#include "driver/gpio.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "bus_binding";

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
/* [CXC-REPOINT-CONFIRM] whether the store plane must be genuinely exercised.
 * True under config:http (a 200+body is required); false under config:bus /
 * config:cached, where the store is never fetched and the trial confirms on
 * the broker echo ALONE ([CXC-REPOINT-AMEND-CONFIG-BUS]).  Defaults true so a
 * trial is never accidentally confirmed on a half-exercised gate. */
static bool     s_store_required = true;
/* the config-source name recorded when the store plane is vacuous, so the
 * confirm ack can DISCLOSE a broker-only confirm (Note 2). */
static char     s_store_note[16] = "";
static char     s_pending_reason[24] = "timeout";
static bool     s_reason_explicit = false;  /* a specific cause was recorded */
static esp_timer_handle_t s_revert_timer = nullptr;

static void default_reboot(void) {
    ESP_LOGW(TAG, "restarting to apply/revert bus binding");
    vTaskDelay(pdMS_TO_TICKS(50));   /* let a final publish flush */
    esp_restart();
}
static BusBindingRebootFn s_reboot = default_reboot;

void bus_binding_set_reboot(BusBindingRebootFn reboot) {
    s_reboot = reboot ? reboot : default_reboot;
}

/* ------------------------------------------------------------------ */
/* NVS plumbing                                                        */
/* ------------------------------------------------------------------ */

static void ensure_nvs(void) {
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
}

static char tier_letter(BindTier t) {
    return t == BindTier::PerDevice ? 'd' : 'g';
}

static void keyname(char* out, size_t cap, BindTier t, const char* field) {
    /* bus.<t>.<field> — always <=15 chars for the fields we use */
    snprintf(out, cap, "bus.%c.%s", tier_letter(t), field);
}

static void nvs_get_string(nvs_handle_t h, BindTier t, const char* field,
                           char* dst, size_t cap) {
    dst[0] = '\0';
    char k[16];
    keyname(k, sizeof(k), t, field);
    size_t len = cap;
    if (nvs_get_str(h, k, dst, &len) != ESP_OK) dst[0] = '\0';
}

static void nvs_set_string(nvs_handle_t h, BindTier t, const char* field,
                           const char* val) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    nvs_set_str(h, k, val ? val : "");
}

static uint32_t nvs_get_num(nvs_handle_t h, BindTier t, const char* field) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    uint32_t v = 0;
    nvs_get_u32(h, k, &v);
    return v;
}

static void nvs_set_num(nvs_handle_t h, BindTier t, const char* field,
                        uint32_t v) {
    char k[16];
    keyname(k, sizeof(k), t, field);
    nvs_set_u32(h, k, v);
}

/* Load one tier's full record.  A tier with no `state` key is Empty. */
static bool load_record(BindTier t, BindRecord* r) {
    memset(r, 0, sizeof(*r));
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return false;

    char k[16];
    keyname(k, sizeof(k), t, "state");
    uint8_t st = 0;
    if (nvs_get_u8(h, k, &st) != ESP_OK) { nvs_close(h); return false; }
    r->state = (BindState)st;

    nvs_get_string(h, t, "name",  r->cur.name,  sizeof(r->cur.name));
    nvs_get_string(h, t, "mqtt",  r->cur.mqtt,  sizeof(r->cur.mqtt));
    nvs_get_string(h, t, "store", r->cur.store, sizeof(r->cur.store));
    nvs_get_string(h, t, "docs",  r->cur.docs,  sizeof(r->cur.docs));
    nvs_get_string(h, t, "pname",  r->prev.name,  sizeof(r->prev.name));
    nvs_get_string(h, t, "pmqtt",  r->prev.mqtt,  sizeof(r->prev.mqtt));
    nvs_get_string(h, t, "pstore", r->prev.store, sizeof(r->prev.store));
    nvs_get_string(h, t, "pdocs",  r->prev.docs,  sizeof(r->prev.docs));
    r->seq     = nvs_get_num(h, t, "seq");
    r->reverts = nvs_get_num(h, t, "reverts");
    r->deny    = (uint16_t)nvs_get_num(h, t, "deny");
    r->tfp     = (uint16_t)nvs_get_num(h, t, "tfp");
    nvs_get_string(h, t, "why", r->why, sizeof(r->why));
    nvs_close(h);
    return true;
}

static bool store_record(BindTier t, const BindRecord* r) {
    ensure_nvs();
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — binding NOT stored");
        return false;
    }
    char k[16];
    keyname(k, sizeof(k), t, "state");
    nvs_set_u8(h, k, (uint8_t)r->state);
    nvs_set_string(h, t, "name",  r->cur.name);
    nvs_set_string(h, t, "mqtt",  r->cur.mqtt);
    nvs_set_string(h, t, "store", r->cur.store);
    nvs_set_string(h, t, "docs",  r->cur.docs);
    nvs_set_string(h, t, "pname",  r->prev.name);
    nvs_set_string(h, t, "pmqtt",  r->prev.mqtt);
    nvs_set_string(h, t, "pstore", r->prev.store);
    nvs_set_string(h, t, "pdocs",  r->prev.docs);
    nvs_set_num(h, t, "seq",     r->seq);
    nvs_set_num(h, t, "reverts", r->reverts);
    nvs_set_num(h, t, "deny",    r->deny);
    nvs_set_num(h, t, "tfp",     r->tfp);
    nvs_set_string(h, t, "why", r->why);
    esp_err_t e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs_commit failed (%s) — binding NOT stored",
                 esp_err_to_name(e));
        return false;
    }
    return true;
}

/* Erase every key of one tier (the escape-hatch clear). */
static void erase_tier(BindTier t) {
    ensure_nvs();
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return;
    static const char* kFields[] = {
        "name","mqtt","store","docs","pname","pmqtt","pstore","pdocs",
        "state","seq","reverts","why","deny","tfp" };
    char k[16];
    for (size_t i = 0; i < sizeof(kFields) / sizeof(kFields[0]); i++) {
        keyname(k, sizeof(k), t, kFields[i]);
        nvs_erase_key(h, k);   /* ESP_ERR_NVS_NOT_FOUND is fine */
    }
    nvs_commit(h);
    nvs_close(h);
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
/* The compiled floor (wifi_config.h) — the rescue-only default        */
/* ------------------------------------------------------------------ */

static BusBinding compiled_floor(void) {
    BusBinding b;
    binding_clear(&b);
    snprintf(b.name,  sizeof(b.name),  "compiled");
    snprintf(b.mqtt,  sizeof(b.mqtt),  "%s", MQTT_BROKER_URI);
    snprintf(b.store, sizeof(b.store), "%s", BUS_STORAGE_URL);
    snprintf(b.docs,  sizeof(b.docs),  "%s", DOCUMENT_STORE_URL);
    return b;
}

/* ------------------------------------------------------------------ */
/* Resolve-once                                                        */
/* ------------------------------------------------------------------ */

static void resolve(void) {
    if (s_resolved) return;
    s_resolved = true;

    ensure_nvs();
    BusBinding floor = compiled_floor();
    BindTierView pd, gl;
    load_view(BindTier::PerDevice, &pd);
    load_view(BindTier::Global, &gl);

    BindResolution r = binding_resolve(&pd, &gl, &floor, s_button_forced);
    s_active = r.active;
    s_source = r.source;
    s_is_trial = r.is_trial;
    s_active_tier = r.tier;

    ESP_LOGI(TAG, "bus binding: name='%s' mqtt=%s (tier=%s source=%u trial=%d)",
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
    gpio_config_t io = {};
    io.pin_bit_mask = 1ULL << CXC_ESCAPE_HATCH_PIN;
    io.mode = GPIO_MODE_INPUT;
    io.pull_up_en = CXC_ESCAPE_HATCH_ACTIVE_LEVEL == 0 ? GPIO_PULLUP_ENABLE
                                                       : GPIO_PULLUP_DISABLE;
    io.pull_down_en = CXC_ESCAPE_HATCH_ACTIVE_LEVEL == 1 ? GPIO_PULLDOWN_ENABLE
                                                         : GPIO_PULLDOWN_DISABLE;
    io.intr_type = GPIO_INTR_DISABLE;
    gpio_config(&io);

    auto pressed = []() {
        return gpio_get_level((gpio_num_t)CXC_ESCAPE_HATCH_PIN) ==
               CXC_ESCAPE_HATCH_ACTIVE_LEVEL;
    };
    if (!pressed()) return 0;   /* not held — normal boot, no delay */

    int64_t start = esp_timer_get_time();
    uint32_t held = 0;
    while (pressed()) {
        vTaskDelay(pdMS_TO_TICKS(20));
        held = (uint32_t)((esp_timer_get_time() - start) / 1000);
        if (held >= CXC_ESCAPE_HATCH_LONG_MS) break;   /* long is decisive */
    }
    return held;
#else
    return 0;
#endif
}

void bus_binding_boot_init(const char* device_id, uint32_t confirm_window_s) {
    if (device_id && device_id[0])
        snprintf(s_device_id, sizeof(s_device_id), "%s", device_id);
    ensure_nvs();

    /* Two-stage escape hatch ([DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]). */
    uint32_t hold = measure_hatch_hold();
    HatchAction act = binding_hatch_action(hold, CXC_ESCAPE_HATCH_SHORT_MS,
                                           CXC_ESCAPE_HATCH_LONG_MS);
    if (act == HatchAction::ClearPerDevice) {
        ESP_LOGW(TAG, "ESCAPE HATCH (short, %lums): clearing PER-DEVICE tier",
                 (unsigned long)hold);
        erase_tier(BindTier::PerDevice);
        s_button_forced = true;
        s_button_cleared = true;
    } else if (act == HatchAction::ClearBoth) {
        ESP_LOGW(TAG, "ESCAPE HATCH (long, %lums): clearing BOTH NVS tiers",
                 (unsigned long)hold);
        erase_tier(BindTier::PerDevice);
        erase_tier(BindTier::Global);
        s_button_forced = true;
        s_button_cleared = true;
    }

    resolve();

    /* Arm the commit-confirmed revert timer BEFORE the network is brought up.
     * ⛔ ordering-is-load-bearing ([CXC-REPOINT-COMMIT-CONFIRMED]): a DHCP or
     * TLS hang after this point still reverts, because the timer is already
     * scheduled. */
    if (s_is_trial) {
        BindRecord r;
        if (load_record(s_active_tier, &r)) {
            s_trial_active = true;
            s_trial_seq = r.seq;
            s_store_ok = false;
            s_echo_ok = false;
            snprintf(s_pending_reason, sizeof(s_pending_reason), "timeout");

            const esp_timer_create_args_t targs = {
                .callback = [](void*) {
                    ESP_LOGW(TAG, "commit-confirmed WINDOW ELAPSED — reverting");
                    BindRecord rr;
                    if (load_record(s_active_tier, &rr)) {
                        /* [CXC-REPOINT-REVERT] name the plane that was still
                         * outstanding rather than a bare "timeout", UNLESS a
                         * more specific cause (no-wifi/no-dns/store-http-<code>)
                         * was already recorded via bus_binding_note_reason. */
                        const char* reason = s_pending_reason;
                        if (!s_reason_explicit)
                            reason = binding_default_reason(s_store_required,
                                                            s_store_ok, s_echo_ok);
                        binding_revert(&rr, reason);
                        store_record(s_active_tier, &rr);
                        if (s_publish && s_ns_status[0]) {
                            char msg[128];
                            snprintf(msg, sizeof(msg),
                                     "bus REVERTED from %s: %s",
                                     s_active.name, reason);
                            s_publish(s_ns_status, msg);
                        }
                    }
                    s_reboot();
                },
                .arg = nullptr,
                .dispatch_method = ESP_TIMER_TASK,
                .name = "bus_revert",
                .skip_unhandled_events = false,
            };
            if (esp_timer_create(&targs, &s_revert_timer) == ESP_OK)
                esp_timer_start_once(s_revert_timer,
                                     (uint64_t)confirm_window_s * 1000000ULL);
            ESP_LOGW(TAG, "TRIAL binding '%s' (seq %lu) — revert armed at %lus",
                     s_active.name, (unsigned long)s_trial_seq,
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
    if (s_revert_timer) esp_timer_stop(s_revert_timer);
    s_trial_active = false;
    s_is_trial = false;
    ESP_LOGI(TAG, "commit-confirmed: binding '%s' KEPT%s", s_active.name,
             s_store_required ? " (both planes)" : " (broker only)");
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
     * failed attempt is now stale; let the callback re-derive from the true
     * final plane state (e.g. broker-no-echo) if a revert still happens. */
    s_reason_explicit = false;
    try_confirm();
}

void bus_binding_note_store_vacuous(const char* config_source) {
    /* [CXC-REPOINT-CONFIRM] the store plane is not exercised under this config
     * strategy — mark it NOT required so the trial confirms on the broker echo
     * alone, and record the strategy name for the disclosing ack (Note 2). */
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
        s_reason_explicit = true;   /* overrides the derived plane reason */
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
 * WIRE ack, not only in an ESP_LOGW nobody off-device can read.  Appends the
 * marker if there is room; a truncated ack is still correct, just clipped. */
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
        ESP_LOGE(TAG, "%s: expected a `bus=… mqtt=…` string — ignored", ns);
        ack("bus REFUSED (non-string value)");
        return true;
    }

    BindParseResult p = binding_parse(str);
    if (p.status != BindParse::Ok) {
        ESP_LOGE(TAG, "%s REFUSED: %s", ns, p.error);
        char msg[96];
        snprintf(msg, sizeof(msg), "bus REFUSED: %s", p.error);
        ack(msg);
        return true;
    }

    BindTier target = is_perdev ? BindTier::PerDevice : BindTier::Global;

    /* Is the target tier the ACTIVE tier?  A per-device publish always targets
     * the (new) active tier; a broadcast publish arms only when the per-device
     * tier is empty, otherwise it is CACHED ([CXC-REPOINT-PRECEDENCE]
     * cross-tier-caching). */
    BindTierView pd;
    load_view(BindTier::PerDevice, &pd);
    bool target_is_active =
        (target == BindTier::PerDevice) ||
        (target == BindTier::Global && pd.state == BindState::Empty);

    /* Merge the partial onto what is effectively in force on the target tier.
     * Note 5: an UNPOPULATED target tier merges onto the COMPILED FLOOR — the
     * tier-independent default — NOT s_active.  Using s_active leaked the
     * per-device active binding into a never-set GLOBAL tier (a partial
     * `store=`-only publish would inherit the per-device mqtt/docs), which is a
     * cross-tier leak; a partial must apply per tier independently. */
    BindRecord r;
    bool populated = load_record(target, &r);
    BusBinding base = populated ? r.cur : compiled_floor();
    BusBinding newb = binding_merge(&base, &p);
    if (!populated) memset(&r, 0, sizeof(r));

    if (p.unknown_key)
        ESP_LOGW(TAG, "%s: unknown key(s) present (UNKNOWN KEY IGNORED)", ns);

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
        ESP_LOGI(TAG, "%s", msg);
        return true;
    }

    /* the target IS active — run the commit-confirmed arm decision */
    char ackbuf[96];
    BindArm a = binding_arm_trial(&r, &newb, ackbuf, sizeof(ackbuf));
    if (p.unknown_key) ack_mark_unknown_key(ackbuf, sizeof(ackbuf));
    ack(ackbuf);
    switch (a) {
        case BindArm::Armed:
            /* persist the trial and RESTART onto it ([CXC-REPOINT-COMMIT-
             * CONFIRMED] state-machine).  The trial boot arms the revert
             * timer in bus_binding_boot_init before wifi. */
            store_record(target, &r);
            ESP_LOGW(TAG, "%s: %s — restarting onto trial", ns, ackbuf);
            s_reboot();
            break;
        case BindArm::NoOp:
            ESP_LOGI(TAG, "%s: %s (absorbing)", ns, ackbuf);
            break;
        case BindArm::RefusedTrial:
        case BindArm::RefusedDeny:
            ESP_LOGW(TAG, "%s: %s", ns, ackbuf);
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
static constexpr uint32_t kAnnounceIntervalMs = 60000;  /* fleet census cadence */
static constexpr uint32_t kEchoIntervalMs = 2000;       /* trial confirm probe  */

void bus_binding_tick(uint32_t now_ms) {
    if (!s_publish) return;

    if (s_ns_perdev[0] && (now_ms >= s_next_announce_ms || s_next_announce_ms == 0)) {
        s_next_announce_ms = now_ms + kAnnounceIntervalMs;
        char hex[9];
        snprintf(hex, sizeof(hex), "%08lx",
                 (unsigned long)bus_binding_announce_word());
        s_publish(s_ns_perdev, hex);
    }

    /* During a trial, keep publishing the confirm seq so the broker echoes it
     * back to us — that returned value is the broker plane of [CXC-REPOINT-
     * CONFIRM].  ⚠ whether this actually confirms is a bench fact. */
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
    if (s_revert_timer) { esp_timer_delete(s_revert_timer); s_revert_timer = nullptr; }
    s_reboot = default_reboot;
    s_next_announce_ms = 0;
    s_next_echo_ms = 0;
}
