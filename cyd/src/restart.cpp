/*
 * restart.cpp — Remote config-reload handler.  See restart.h.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-REMOTE-RESTART] [CXC-ER-RESTART-BUS]
 *
 * Pure C++ (no ESP-IDF).  The truthiness canon is dsl_truthy
 * (cyd/src/dsl.h) — the SAME truthiness the binary actuator HAL uses —
 * so `_device/restart` and a `binary` output agree bit-for-bit on what
 * "truthy" means.  esp_restart() and the ack publish are injected seams.
 */

#include "restart.h"
#include "dsl.h"

#include <cstring>

/* ------------------------------------------------------------------ */
/* Handler state                                                       */
/* ------------------------------------------------------------------ */

static bool s_inited = false;
static char s_ns_device[96] = {0};   /* _device/<id>/restart          */
static char s_ns_broadcast[24] = {0};/* _device/restart               */
static char s_ns_status[96] = {0};   /* _device/<id>/status           */
static RestartPublishFn s_publish = nullptr;
static RestartRebootFn s_reboot = nullptr;
static uint32_t s_boot_window_ms = 3000;
/* Latched once a restart is honored so a second command (e.g. a
 * broadcast plus a targeted command, [CXC-ER-RESTART-BUS] precedence)
 * cannot fire a second reboot within the same boot.  On real hardware
 * the reboot seam never returns, so this only matters to the host test
 * whose seam DOES return — it makes "restarts once" assertable. */
static bool s_restarting = false;

/* ------------------------------------------------------------------ */
/* Init / reset                                                        */
/* ------------------------------------------------------------------ */

void restart_init(const char* device_id, RestartPublishFn publish,
                  RestartRebootFn reboot, uint32_t boot_window_ms) {
    const char* id = (device_id && device_id[0]) ? device_id : "unknown";
    snprintf(s_ns_device, sizeof(s_ns_device), "_device/%s/restart", id);
    snprintf(s_ns_broadcast, sizeof(s_ns_broadcast), "_device/restart");
    snprintf(s_ns_status, sizeof(s_ns_status), "_device/%s/status", id);
    s_publish = publish;
    s_reboot = reboot;
    s_boot_window_ms = boot_window_ms;
    s_restarting = false;
    s_inited = true;
}

void restart_reset(void) {
    s_inited = false;
    s_ns_device[0] = s_ns_broadcast[0] = s_ns_status[0] = '\0';
    s_publish = nullptr;
    s_reboot = nullptr;
    s_boot_window_ms = 3000;
    s_restarting = false;
}

size_t restart_namespaces(const char** out, size_t cap) {
    if (!s_inited || !out) return 0;
    size_t n = 0;
    if (cap > n) out[n++] = s_ns_device;
    if (cap > n) out[n++] = s_ns_broadcast;
    return n;
}

bool restart_is_restart_ns(const char* ns) {
    if (!s_inited || !ns) return false;
    return strcmp(ns, s_ns_device) == 0 || strcmp(ns, s_ns_broadcast) == 0;
}

/* ------------------------------------------------------------------ */
/* Payload truthiness (dsl_truthy canon + canonical object form)       */
/* ------------------------------------------------------------------ */

/* Locate the value of top-level `"restart"` in a flat JSON object and
 * return a pointer to its first non-space value char, or nullptr.  A
 * nested object cannot spoof the key — we require the "restart": shape.
 * (Same flat-scan idiom as bus_mqtt.cpp's json_value.) */
static const char* find_restart_value(const char* s) {
    const char* p = s;
    while ((p = strchr(p, '"')) != nullptr) {
        const char* k = p + 1;
        if (strncmp(k, "restart", 7) == 0 && k[7] == '"') {
            const char* q = k + 8;
            while (*q == ' ' || *q == '\t') q++;
            if (*q == ':') {
                q++;
                while (*q == ' ' || *q == '\t') q++;
                return q;
            }
        }
        /* skip this string literal (escape-aware) */
        p++;
        while (*p && *p != '"') {
            if (*p == '\\' && p[1]) p++;
            p++;
        }
        if (*p) p++;
    }
    return nullptr;
}

/* Truthiness of the value token of the canonical {"restart": <v>} form.
 * No truthy restart field (incl. the empty object {}) -> false. */
static bool object_restart_truthy(const char* obj) {
    const char* v = find_restart_value(obj);
    if (!v) return false;                       /* {} or no restart key */
    if (strncmp(v, "true", 4) == 0) return true;
    if (strncmp(v, "false", 5) == 0) return false;
    if (strncmp(v, "null", 4) == 0) return false;
    if (*v == '"') {                            /* string value */
        char buf[24];
        size_t n = 0;
        const char* p = v + 1;
        while (*p && *p != '"' && n + 1 < sizeof(buf)) buf[n++] = *p++;
        buf[n] = '\0';
        return dsl_truthy(DslValue::string(buf, n));
    }
    /* numeric value */
    char* end = nullptr;
    double d = strtod(v, &end);
    if (end == v) return false;                 /* garbage -> safe no-op */
    return dsl_truthy(DslValue::number(d));
}

bool restart_payload_truthy(int kind, double num, bool b, const char* str) {
    switch (kind) {
        case 0:  /* Number */
            return dsl_truthy(DslValue::number(num));
        case 1:  /* Bool */
            return dsl_truthy(DslValue::boolean(b));
        case 2: {/* String — bare scalar OR the canonical object form */
            if (!str) return false;
            const char* p = str;
            while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r') p++;
            if (*p == '{')
                return object_restart_truthy(p);
            return dsl_truthy(DslValue::string(str, strlen(str)));
        }
        default:
            return false;
    }
}

/* ------------------------------------------------------------------ */
/* Dispatch                                                            */
/* ------------------------------------------------------------------ */

bool restart_on_bus_value(const char* ns, int kind, double num, bool b,
                          const char* str, uint32_t now_ms) {
    if (!restart_is_restart_ns(ns)) return false;   /* not for us */

    /* Consumed from here on — a control-plane command is never routed to
     * the scene, whatever the outcome. */
    if (s_restarting) return true;                  /* already going down */

    if (!restart_payload_truthy(kind, num, b, str))
        return true;                                /* falsy/empty = NO-OP */

    /* Boot-loop guard ([CXC-ER-RESTART-BUS]): ignore a command received
     * within the boot window.  now_ms is ms-since-boot (esp_timer), so a
     * retained/doubled command re-delivered at the post-reboot MQTT
     * reconnect burst lands here and is dropped — the device cannot loop.
     * A time window (rather than a config-fetch-complete flag) keeps a
     * config-LESS boot restartable (the Qualia idles on the boot splash
     * when no config is present and must still honor a remote restart). */
    if (now_ms < s_boot_window_ms)
        return true;

    /* Honor: pre-restart ack, then hard reset.  Latch first so a second
     * queued command cannot double-fire. */
    s_restarting = true;
    if (s_publish && s_ns_status[0])
        s_publish(s_ns_status, "restarting");
    if (s_reboot)
        s_reboot();
    return true;
}
