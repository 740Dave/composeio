/*
 * bus_transport_select.cpp — reactive_core transport SELECTION (offline default).
 * [SPEC:CXC-BOARD-HW] [CXC-BOARD-HW-UART-SLAVE] DP-CIO-6.
 *
 * The pure resolve/validate core is host-tested in env:native_test; the NVS
 * read/write lives behind #ifdef ESP_PLATFORM so the native build needs no
 * ESP-IDF headers.  Default is "offline" — no broker baked, no radio brought
 * up (this dispatch: firmware + logic only, live-bus join is later).
 */
#include "bus_transport_select.h"

#include <cstring>

namespace {
struct Row { const char* name; bool needs_network; };
/* THE REGISTRY.  "offline" is first and is the default: a device that carries
 * no traffic and brings up no network.  mqtt needs an IP stack; a framed
 * substrate (uart/can) does not. */
const Row kRegistry[] = {
    { "offline", false },
    { "mqtt",    true  },
    { "uart",    false },
    { "can",     false },
};
constexpr size_t kRegistryCount = sizeof(kRegistry) / sizeof(kRegistry[0]);

const Row* find_row(const char* name) {
    if (!name) return nullptr;
    for (size_t i = 0; i < kRegistryCount; i++)
        if (std::strcmp(kRegistry[i].name, name) == 0) return &kRegistry[i];
    return nullptr;
}
}  // namespace

const char* transport_known_names() { return "offline mqtt uart can"; }

bool transport_name_known(const char* name) { return find_row(name) != nullptr; }

bool transport_needs_network(const char* name) {
    const Row* r = find_row(name);
    return r ? r->needs_network : false;   /* unknown never brings the radio up */
}

TransportSelection transport_select_resolve(const char* nvs_value) {
    TransportSelection sel;
    if (nvs_value && nvs_value[0]) {
        sel.name = nvs_value;
        sel.source = "nvs";
    } else {
        sel.name = CXC_BUS_TRANSPORT_DEFAULT;
        sel.source = "default";
    }
    sel.known = transport_name_known(sel.name.c_str());
    sel.needs_network = transport_needs_network(sel.name.c_str());
    return sel;
}

/* ------------------------------------------------------------------------ */
/* Boot-time resolution + persistence.  ESP-backed on device; pure on host.   */
/* ------------------------------------------------------------------------ */

#if defined(ESP_PLATFORM)

#include "nvs.h"
#include "nvs_flash.h"
#include "esp_log.h"

#define CXC_NVS_NAMESPACE "composeio"
#define CXC_NVS_KEY_TRANSPORT "transport"
static const char* TAG = "bus_select";

static bool nvs_read_selection(char* out, size_t cap) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return false;
    size_t len = cap;
    esp_err_t e = nvs_get_str(h, CXC_NVS_KEY_TRANSPORT, out, &len);
    nvs_close(h);
    return e == ESP_OK && out[0];
}

const TransportSelection& bus_transport_selection() {
    static TransportSelection sel;
    static bool resolved = false;
    if (resolved) return sel;

    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
    char from_nvs[16] = "";
    bool have = nvs_read_selection(from_nvs, sizeof(from_nvs));
    sel = transport_select_resolve(have ? from_nvs : nullptr);
    resolved = true;

    if (!sel.known) {
        ESP_LOGE(TAG, "UNKNOWN transport '%s' (from %s).  Known: %s.  This is "
                 "NOT falling back to a working transport.", sel.name.c_str(),
                 sel.source.c_str(), transport_known_names());
    } else {
        ESP_LOGI(TAG, "transport: %s (from %s; known: %s)", sel.name.c_str(),
                 sel.source.c_str(), transport_known_names());
    }
    return sel;
}

bool bus_transport_set_selection(const char* name) {
    if (!transport_name_known(name)) {
        ESP_LOGE(TAG, "REFUSED transport selection '%s' — known: %s.  Nothing "
                 "stored.", name ? name : "(null)", transport_known_names());
        return false;
    }
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t e = nvs_set_str(h, CXC_NVS_KEY_TRANSPORT, name);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    return e == ESP_OK;
}

#else  /* host / native_test — pure default, no NVS */

const TransportSelection& bus_transport_selection() {
    static TransportSelection sel = transport_select_resolve(nullptr);
    return sel;
}

bool bus_transport_set_selection(const char* name) {
    return transport_name_known(name);
}

#endif
