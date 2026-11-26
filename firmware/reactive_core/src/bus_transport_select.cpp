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

/* ---- [CXC-DEVICE-BASELINE] cap 6d: the ROLE axis (pure) ----------------- */

namespace {
struct ModeRow { const char* name; bool needs_capability; };
/* "standalone" is first and is the default.  Only "mothercard" must clear the
 * capability gate (it hosts the relay); a daughtercard is relayed and a
 * standalone rides the bus directly. */
const ModeRow kModeRegistry[] = {
    { "standalone",   false },
    { "mothercard",   true  },
    { "daughtercard", false },
};
constexpr size_t kModeCount = sizeof(kModeRegistry) / sizeof(kModeRegistry[0]);

const ModeRow* find_mode(const char* name) {
    if (!name) return nullptr;
    for (size_t i = 0; i < kModeCount; i++)
        if (std::strcmp(kModeRegistry[i].name, name) == 0) return &kModeRegistry[i];
    return nullptr;
}
}  // namespace

const char* transport_mode_known_names() {
    return "standalone mothercard daughtercard";
}

bool transport_mode_name_known(const char* name) { return find_mode(name) != nullptr; }

TransportMode transport_mode_resolve(const char* nvs_value) {
    TransportMode m;
    if (nvs_value && nvs_value[0]) {
        m.name = nvs_value;
        m.source = "nvs";
    } else {
        m.name = CXC_TRANSPORT_MODE_DEFAULT;
        m.source = "default";
    }
    const ModeRow* r = find_mode(m.name.c_str());
    m.known = r != nullptr;
    m.needs_capability = r ? r->needs_capability : false;
    return m;
}

UartPins uart_pins_resolve(int nvs_tx, int nvs_rx, int profile_tx, int profile_rx) {
    UartPins p;
    bool from_nvs = false;
    if (nvs_tx >= 0) { p.tx = nvs_tx; from_nvs = true; } else { p.tx = profile_tx; }
    if (nvs_rx >= 0) { p.rx = nvs_rx; from_nvs = true; } else { p.rx = profile_rx; }
    p.source = from_nvs ? "nvs" : "profile";
    return p;
}

bool board_can_host_baseline(int ram_kb, int flash_kb) {
    /* Fail CLOSED on undeclared resources: an unknown board is a daughtercard,
     * never silently promoted to mothercard. */
    if (ram_kb <= 0 || flash_kb <= 0) return false;
    return ram_kb >= CXC_BASELINE_MIN_RAM_KB && flash_kb >= CXC_BASELINE_MIN_FLASH_KB;
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

/* --- [CXC-DEVICE-BASELINE] cap 6d: role + UART pins, NVS-backed ----------- */
#define CXC_NVS_KEY_MODE    "transport_mode"
#define CXC_NVS_KEY_UART_TX "uart_tx"
#define CXC_NVS_KEY_UART_RX "uart_rx"

static bool nvs_read_str_key(const char* key, char* out, size_t cap) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return false;
    size_t len = cap;
    esp_err_t e = nvs_get_str(h, key, out, &len);
    nvs_close(h);
    return e == ESP_OK && out[0];
}

/* Read an i32 NVS key; return the default when absent. */
static int nvs_read_i32_key(const char* key, int def) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return def;
    int32_t v = def;
    esp_err_t e = nvs_get_i32(h, key, &v);
    nvs_close(h);
    return (e == ESP_OK) ? (int)v : def;
}

const TransportMode& bus_transport_mode() {
    static TransportMode m;
    static bool resolved = false;
    if (resolved) return m;
    char from_nvs[24] = "";
    bool have = nvs_read_str_key(CXC_NVS_KEY_MODE, from_nvs, sizeof(from_nvs));
    m = transport_mode_resolve(have ? from_nvs : nullptr);
    resolved = true;
    if (!m.known)
        ESP_LOGE(TAG, "UNKNOWN role '%s' (from %s).  Known: %s.  NOT falling "
                 "back to a working role.", m.name.c_str(), m.source.c_str(),
                 transport_mode_known_names());
    else
        ESP_LOGI(TAG, "role: %s (from %s)", m.name.c_str(), m.source.c_str());
    return m;
}

bool bus_transport_set_mode(const char* name, int ram_kb, int flash_kb) {
    if (!transport_mode_name_known(name)) {
        ESP_LOGE(TAG, "REFUSED role '%s' — known: %s.  Nothing stored.",
                 name ? name : "(null)", transport_mode_known_names());
        return false;
    }
    if (std::strcmp(name, "mothercard") == 0 &&
        !board_can_host_baseline(ram_kb, flash_kb)) {
        ESP_LOGE(TAG, "REFUSED role 'mothercard' — this board (%d KB RAM / %d KB "
                 "flash) FAILS the capability gate (>= %d / %d).  It is a "
                 "daughtercard by construction.", ram_kb, flash_kb,
                 CXC_BASELINE_MIN_RAM_KB, CXC_BASELINE_MIN_FLASH_KB);
        return false;
    }
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) return false;
    esp_err_t e = nvs_set_str(h, CXC_NVS_KEY_MODE, name);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    return e == ESP_OK;
}

UartPins bus_uart_pins(int profile_tx, int profile_rx) {
    int nvs_tx = nvs_read_i32_key(CXC_NVS_KEY_UART_TX, -1);
    int nvs_rx = nvs_read_i32_key(CXC_NVS_KEY_UART_RX, -1);
    return uart_pins_resolve(nvs_tx, nvs_rx, profile_tx, profile_rx);
}

#else  /* host / native_test — pure default, no NVS */

const TransportSelection& bus_transport_selection() {
    static TransportSelection sel = transport_select_resolve(nullptr);
    return sel;
}

bool bus_transport_set_selection(const char* name) {
    return transport_name_known(name);
}

const TransportMode& bus_transport_mode() {
    static TransportMode m = transport_mode_resolve(nullptr);
    return m;
}

bool bus_transport_set_mode(const char* name, int ram_kb, int flash_kb) {
    if (!transport_mode_name_known(name)) return false;
    if (std::strcmp(name, "mothercard") == 0 &&
        !board_can_host_baseline(ram_kb, flash_kb))
        return false;
    return true;
}

UartPins bus_uart_pins(int profile_tx, int profile_rx) {
    return uart_pins_resolve(-1, -1, profile_tx, profile_rx);
}

#endif
