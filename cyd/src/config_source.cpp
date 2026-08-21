/*
 * config_source.cpp — see config_source.h.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-STRATEGY]
 *
 * Deliberately shaped like bus_transport_select.cpp, down to the refusal
 * object, because it is the same decision with a different noun and the fleet
 * should not have two idioms for it.
 *
 * HOW "FATAL" IS SPELLED ON AN MCU, AND WHY IT IS NOT abort().  Python raises
 * and the process exits, which a human sees.  A firmware abort() is a
 * panic-reboot LOOP — and since this selection is settable OVER THE BUS, one
 * typo would leave a board that cannot be reached to fix it, on exactly the
 * boards whose whole point is not having a cable.  So "fatal" here means: the
 * device does NOT fall back to a working strategy, it fetches nothing, it says
 * why on every attempt, and it keeps its radio up so it can be repaired.
 */

#include "config_source.h"
#include "config_chunk.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char* TAG = "config_src";

#ifndef CXC_CONFIG_SOURCE_DEFAULT
#define CXC_CONFIG_SOURCE_DEFAULT "http"
#endif

#define CXC_NVS_NAMESPACE "composeio"
#define CXC_NVS_KEY_SOURCE "config"
#define CXC_NVS_KEY_CACHE "config.text"
#define CXC_NVS_KEY_CACHE_HASH "config.hash"
#define CXC_NVS_KEY_CACHE_BOOT "config.boot"
#define CXC_NVS_KEY_BOOTS "boots"

/* THE REGISTRY.  One row per strategy — the same one-edit property
 * transport_registry.py has. */
struct SourceEntry {
    const char* name;
    ConfigSource value;
    bool needs_network;
};
static const SourceEntry kRegistry[] = {
    { "bus",    ConfigSource::Bus,    false },
    { "http",   ConfigSource::Http,   true  },
    { "cached", ConfigSource::Cached, false },
};
static constexpr size_t kRegistryCount =
    sizeof(kRegistry) / sizeof(kRegistry[0]);

const char* config_source_known_names(void) { return "bus http cached"; }

static const SourceEntry* lookup(const char* name) {
    if (!name) return nullptr;
    for (size_t i = 0; i < kRegistryCount; i++)
        if (strcmp(kRegistry[i].name, name) == 0) return &kRegistry[i];
    return nullptr;
}

static char s_selected[16] = "";
static const char* s_src = "default";
static ConfigSource s_value = ConfigSource::Unknown;
static bool s_resolved = false;
static uint32_t s_boots = 0;
static bool s_stale = false;

static void ensure_nvs(void) {
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
}

/* The only monotonic clock a radio-less board has.  Bumped once per boot; a
 * cache records the boot it was written at, and the difference is what the
 * staleness badge shows.  It is not a duration and is never presented as one. */
static void bump_boot_count(void) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return;
    uint32_t n = 0;
    nvs_get_u32(h, CXC_NVS_KEY_BOOTS, &n);
    n++;
    nvs_set_u32(h, CXC_NVS_KEY_BOOTS, n);
    nvs_commit(h);
    nvs_close(h);
    s_boots = n;
}

uint32_t config_boot_count(void) { return s_boots; }

ConfigSource config_source(void) {
    if (s_resolved) return s_value;
    s_resolved = true;

    ensure_nvs();
    bump_boot_count();

    char from_nvs[16] = "";
    nvs_handle_t h;
    bool got = false;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) == ESP_OK) {
        size_t len = sizeof(from_nvs);
        got = nvs_get_str(h, CXC_NVS_KEY_SOURCE, from_nvs, &len) == ESP_OK &&
              from_nvs[0];
        nvs_close(h);
    }
    snprintf(s_selected, sizeof(s_selected), "%s",
             got ? from_nvs : CXC_CONFIG_SOURCE_DEFAULT);
    s_src = got ? "nvs" : "default";

    const SourceEntry* e = lookup(s_selected);
    if (e) {
        s_value = e->value;
        ESP_LOGI(TAG, "config source: %s (from %s; known: %s; boot #%lu)",
                 s_selected, s_src, config_source_known_names(),
                 (unsigned long)s_boots);
    } else {
        s_value = ConfigSource::Unknown;
        ESP_LOGE(TAG, "UNKNOWN config source '%s' (from %s).  Known: %s.  "
                 "This board will fetch NOTHING and is NOT falling back to a "
                 "working strategy — a device that quietly ignores its own "
                 "configuration is the defect transport_registry.py's rule 1 "
                 "exists to prevent.  Fix the NVS key '%s' in namespace '%s', "
                 "or publish _device/<id>/config_source.  The radio stays UP "
                 "so this board remains reachable to be fixed.",
                 s_selected, s_src, config_source_known_names(),
                 CXC_NVS_KEY_SOURCE, CXC_NVS_NAMESPACE);
    }
    return s_value;
}

const char* config_source_selected_name(void) {
    return s_selected[0] ? s_selected : CXC_CONFIG_SOURCE_DEFAULT;
}
const char* config_source_selection_source(void) { return s_src; }

bool config_source_needs_network(void) {
    ConfigSource v = config_source();
    if (v == ConfigSource::Unknown) return true;   /* recovery — see the .h */
    for (size_t i = 0; i < kRegistryCount; i++)
        if (kRegistry[i].value == v) return kRegistry[i].needs_network;
    return true;
}

bool config_source_set_selection(const char* name) {
    if (!lookup(name)) {
        ESP_LOGE(TAG, "REFUSED config source '%s' — known: %s.  Nothing was "
                 "stored.", name ? name : "(null)",
                 config_source_known_names());
        return false;
    }
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — selection NOT stored");
        return false;
    }
    esp_err_t e = nvs_set_str(h, CXC_NVS_KEY_SOURCE, name);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs write failed (%s) — selection NOT stored",
                 esp_err_to_name(e));
        return false;
    }
    ESP_LOGW(TAG, "config source stored: '%s' — takes effect on the NEXT boot "
             "(this boot is still '%s').  If you are also switching transport, "
             "set BOTH, rewire, THEN power cycle.", name,
             config_source_selected_name());
    return true;
}

/* ------------------------------------------------------------------ */
/* `_device/<id>/config_source` — settable over the CURRENT link        */
/* ------------------------------------------------------------------ */

static char s_ctrl_ns[128] = "";
static char s_status_ns[128] = "";
static ConfigStatusPublishFn s_publish = nullptr;

void config_source_control_init(const char* device_id,
                                ConfigStatusPublishFn publish) {
    s_publish = publish;
    if (!device_id || !device_id[0]) return;
    snprintf(s_ctrl_ns, sizeof(s_ctrl_ns), "_device/%s/config_source",
             device_id);
    snprintf(s_status_ns, sizeof(s_status_ns), "_device/%s/status", device_id);
}

size_t config_source_control_namespaces(const char** out, size_t cap) {
    if (!s_ctrl_ns[0] || cap < 1 || !out) return 0;
    out[0] = s_ctrl_ns;
    return 1;
}

bool config_source_on_bus_value(const char* ns, int kind, double num, bool b,
                                const char* str) {
    (void)num;
    (void)b;
    if (!ns || !s_ctrl_ns[0] || strcmp(ns, s_ctrl_ns) != 0) return false;

    /* A strategy is NAMED, never numbered.  Anything else is reported rather
     * than coerced — the same rule the transport control namespace applies. */
    if (kind != 2 /* String */ || !str || !str[0]) {
        ESP_LOGE(TAG, "%s: expected a strategy NAME (one of: %s); got a "
                 "non-string value — ignored", s_ctrl_ns,
                 config_source_known_names());
        return true;
    }

    char ack[80];
    if (config_source_set_selection(str))
        snprintf(ack, sizeof(ack), "config_source=%s next boot", str);
    else
        snprintf(ack, sizeof(ack), "config_source=%s REFUSED", str);
    if (s_status_ns[0] && s_publish) s_publish(s_status_ns, ack);
    return true;
}

/* ------------------------------------------------------------------ */
/* The cache                                                           */
/* ------------------------------------------------------------------ */

bool config_cache_store(const char* text, size_t len) {
    if (!text) return false;
    if (len == 0 || len > kConfigCacheMax) {
        /* REFUSED, not truncated.  Half a scene stored is a scene that will
         * one day be loaded and will build a partial widget tree that looks
         * like a rendering bug. */
        ESP_LOGE(TAG, "config cache REFUSED: %u bytes (limit %u).  Nothing "
                 "was stored — a truncated cache is worse than none.",
                 (unsigned)len, (unsigned)kConfigCacheMax);
        return false;
    }
    ensure_nvs();
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t e = nvs_set_blob(h, CXC_NVS_KEY_CACHE, text, len);
    if (e == ESP_OK)
        e = nvs_set_u32(h, CXC_NVS_KEY_CACHE_HASH,
                        config_content_hash((const uint8_t*)text, len));
    if (e == ESP_OK) e = nvs_set_u32(h, CXC_NVS_KEY_CACHE_BOOT, s_boots);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGW(TAG, "config cache write failed (%s) — the board still runs, "
                 "but `config: cached` has nothing fresh to load",
                 esp_err_to_name(e));
        return false;
    }
    ESP_LOGI(TAG, "config cached: %u bytes, hash 0x%04X, boot #%lu",
             (unsigned)len,
             config_content_hash((const uint8_t*)text, len),
             (unsigned long)s_boots);
    return true;
}

char* config_cache_load(size_t* out_len) {
    if (out_len) *out_len = 0;
    ensure_nvs();
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return nullptr;
    size_t len = 0;
    esp_err_t e = nvs_get_blob(h, CXC_NVS_KEY_CACHE, nullptr, &len);
    if (e != ESP_OK || len == 0 || len > kConfigCacheMax) {
        nvs_close(h);
        return nullptr;
    }
    char* buf = (char*)malloc(len + 1);
    if (!buf) { nvs_close(h); return nullptr; }
    e = nvs_get_blob(h, CXC_NVS_KEY_CACHE, buf, &len);
    nvs_close(h);
    if (e != ESP_OK) { free(buf); return nullptr; }
    buf[len] = 0;
    if (out_len) *out_len = len;
    ESP_LOGW(TAG, "loaded CACHED config: %u bytes, written %lu boot(s) ago.  "
             "This scene may be arbitrarily old and the panel will look "
             "perfectly healthy either way — that is why it carries a "
             "staleness badge.", (unsigned)len,
             (unsigned long)config_cache_age_boots());
    return buf;
}

uint32_t config_cache_age_boots(void) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return 0;
    uint32_t written = 0;
    esp_err_t e = nvs_get_u32(h, CXC_NVS_KEY_CACHE_BOOT, &written);
    nvs_close(h);
    if (e != ESP_OK || s_boots < written) return 0;
    return s_boots - written;
}

bool config_is_stale(void) { return s_stale; }
void config_set_stale(bool stale) { s_stale = stale; }
