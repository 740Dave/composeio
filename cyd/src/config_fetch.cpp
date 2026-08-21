/*
 * config_fetch.cpp — Config + asset fetch over HTTP ([CXC-R-BOOT])
 */

#include "config_fetch.h"
#include "wifi_config.h"
#include "bus_binding.h"   /* [CXC-REPOINT] active-tier store URL, not the #define */

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"

static const char* TAG = "config_fetch";

static constexpr size_t kMaxConfigBytes = 32 * 1024;
static constexpr size_t kMaxAssetBytes = 160 * 1024;

/* The HTTP status of the LAST http_fetch: >0 is a real response code, 0 means
 * the connection never got that far (open/DNS failure).  Read by the caller to
 * distinguish `store-http-<code>` from `store-unreachable` on a failed trial
 * store plane ([CXC-REPOINT-REVERT], Note 1). */
static int s_last_http_status = 0;
int config_fetch_last_status(void) { return s_last_http_status; }

/* ------------------------------------------------------------------ */
/* Device identity                                                     */
/* ------------------------------------------------------------------ */

const char* device_id(void) {
#ifdef DISPLAY_DEVICE_ID
    return DISPLAY_DEVICE_ID;
#else
    /* Board prefix, so the dispatcher can tell unit KINDS apart on one
     * bus/bus_storage — and so each board can own a per-device scene
     * config instead of every unit falling back to displays/default.
     *
     * CXC_DEVICE_PREFIX is set as a CMAKE compile definition by the
     * board's branch in src/CMakeLists.txt.  It must NOT be a
     * platformio.ini build_flag: for framework=espidf those never reach
     * the component compile and are silently dropped, which is how a
     * board ends up quietly answering to the wrong prefix.  The
     * QUALIA_DISPLAY fallback below is kept EXACTLY as it was so no
     * existing board's identity shifts under it. */
#ifndef CXC_DEVICE_PREFIX
#  ifdef QUALIA_DISPLAY
#    define CXC_DEVICE_PREFIX "qualia"
#  else
#    define CXC_DEVICE_PREFIX "cyd"
#  endif
#endif
    static char s_id[16] = {0};
    if (!s_id[0]) {
        uint8_t mac[6] = {0};
        esp_read_mac(mac, ESP_MAC_WIFI_STA);
        snprintf(s_id, sizeof(s_id), CXC_DEVICE_PREFIX "-%02x%02x%02x",
                 mac[3], mac[4], mac[5]);
    }
    return s_id;
#endif
}

/* ------------------------------------------------------------------ */
/* HTTP GET into heap                                                  */
/* ------------------------------------------------------------------ */

char* http_fetch(const char* uri, size_t* out_len, size_t max_len) {
    if (out_len) *out_len = 0;

    /* bus://assets/... -> bus_storage HTTP */
    static char url[300];
    if (strncmp(uri, "bus://assets/", 13) == 0) {
        /* [CXC-REPOINT] the bus_storage base is the ACTIVE tier's binding,
         * resolved through bus_binding (per-device > global > compiled floor);
         * BUS_STORAGE_URL is now the rescue floor alone. */
        snprintf(url, sizeof(url), "%s/api/v1/assets/%s",
                 bus_binding_active_store(), uri + 13);
        uri = url;
    }

    esp_http_client_config_t config = {};
    config.url = uri;
    config.method = HTTP_METHOD_GET;
    config.timeout_ms = 10000;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (!client) return nullptr;

    char* buf = nullptr;
    s_last_http_status = 0;   /* 0 until a response code is actually read */
    do {
        if (esp_http_client_open(client, 0) != ESP_OK) {
            ESP_LOGW(TAG, "GET %s: open failed", uri);
            break;
        }
        int64_t clen = esp_http_client_fetch_headers(client);
        int status = esp_http_client_get_status_code(client);
        s_last_http_status = status;
        if (status != 200) {
            ESP_LOGW(TAG, "GET %s -> HTTP %d", uri, status);
            break;
        }
        size_t cap = (clen > 0) ? (size_t)clen : 8192;
        if (cap > max_len) {
            ESP_LOGW(TAG, "GET %s: %u bytes exceeds cap %u",
                     uri, (unsigned)cap, (unsigned)max_len);
            break;
        }
        buf = static_cast<char*>(malloc(cap + 1));
        if (!buf) {
            ESP_LOGE(TAG, "GET %s: malloc(%u) failed", uri, (unsigned)cap + 1);
            break;
        }
        size_t total = 0;
        while (true) {
            if (total == cap) {
                /* chunked responses: grow up to max_len */
                size_t new_cap = cap * 2;
                if (new_cap > max_len) new_cap = max_len;
                if (new_cap == cap) { free(buf); buf = nullptr; break; }
                char* nb = static_cast<char*>(realloc(buf, new_cap + 1));
                if (!nb) { free(buf); buf = nullptr; break; }
                buf = nb;
                cap = new_cap;
            }
            int n = esp_http_client_read(client, buf + total, cap - total);
            if (n < 0) { free(buf); buf = nullptr; break; }
            if (n == 0) break;
            total += (size_t)n;
        }
        if (buf) {
            buf[total] = '\0';
            if (out_len) *out_len = total;
            ESP_LOGI(TAG, "GET %s -> %u bytes", uri, (unsigned)total);
        }
    } while (false);

    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return buf;
}

/* ------------------------------------------------------------------ */
/* Config fetch (per-device, then default)                             */
/* ------------------------------------------------------------------ */

char* config_fetch(size_t* out_len) {
    char uri[128];

    snprintf(uri, sizeof(uri), "bus://assets/displays/%s.compose.yaml",
             device_id());
    ESP_LOGI(TAG, "Fetching config for device '%s'...", device_id());
    char* cfg = http_fetch(uri, out_len, kMaxConfigBytes);
    if (cfg) return cfg;

#ifdef QUALIA_DISPLAY
    /* A 240x320 CYD default would be wrong on the 720x720 round panel
     * (and vice versa) — the framebuffer tier gets its own fallback. */
    ESP_LOGW(TAG, "Per-device config missing; trying displays/default-qualia");
    snprintf(uri, sizeof(uri),
             "bus://assets/displays/default-qualia.compose.yaml");
#else
    ESP_LOGW(TAG, "Per-device config missing; trying displays/default");
    snprintf(uri, sizeof(uri), "bus://assets/displays/default.compose.yaml");
#endif
    return http_fetch(uri, out_len, kMaxConfigBytes);
}
