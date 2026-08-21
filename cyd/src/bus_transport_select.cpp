/*
 * bus_transport_select.cpp — which substrate does this boot ride?
 * [SPEC:CXC-BOARD-HW] [CXC-BOARD-HW-UART-SLAVE] DP-CIO-6.
 *
 * The firmware mirror of python/composeio/transport_registry.py.  One
 * name -> one factory, and the SAME two failure rules, because they are
 * about the same defect:
 *
 *   1. An UNKNOWN name is FATAL and LOUD.  In Python a scene declaring
 *      `type: can` used to be dropped on the floor: no exception, no log,
 *      the app ran local-only and the operator saw only missing telemetry.
 *      A mistyped `mqqt` must be a startup error, never a silent degrade.
 *   2. A KNOWN name whose backend is unreachable degrades LOUDLY and the
 *      app keeps running — a down broker genuinely is not a broken app.
 *      That is start() returning false, not a selection failure.
 *
 * HOW "FATAL" IS SPELLED ON AN MCU, AND WHY IT IS NOT abort().  Python
 * raises and the process exits, which a human sees.  A firmware abort() is
 * a panic-reboot LOOP, and since this selection is settable OVER THE BUS,
 * one typo would leave a board that cannot be reached to fix it —
 * unrecoverable without a serial cable and a reflash, on the exact boards
 * whose whole point is not having a cable.  So "fatal" here means: the
 * device does NOT fall back to a working transport, it selects a
 * MisconfiguredTransport that carries nothing and says why, forever, on
 * every render pass.  It is loud, it is not silently local-only, and it is
 * still recoverable.  Belt and braces: an unknown name is also REJECTED AT
 * WRITE TIME (bus_transport_set_selection), so reaching this state at all
 * requires writing NVS by some other means.
 */

#include "bus_transport.h"
#include "bus_can.h"
#include "bus_mqtt.h"
#include "bus_uart.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_log.h"
#include "esp_timer.h"
#include "nvs.h"
#include "nvs_flash.h"

static const char* TAG = "bus_select";

/* The compile-time default.  "mqtt" everywhere today: adopting the seam
 * must not, by itself, change what any board does. */
#ifndef CXC_BUS_TRANSPORT_DEFAULT
#define CXC_BUS_TRANSPORT_DEFAULT "mqtt"
#endif

#define CXC_NVS_NAMESPACE "composeio"
#define CXC_NVS_KEY_TRANSPORT "transport"

/* THE REGISTRY.  Adding a transport is one row here plus its .cpp in
 * CMakeLists — the same one-edit property transport_registry.py has. */
struct TransportFactory {
    const char* name;
    BusTransport& (*get)();
};
static const TransportFactory kRegistry[] = {
    { "mqtt", bus_mqtt_transport },
    { "uart", bus_uart_transport },
    { "can",  bus_can_transport  },
};
static constexpr size_t kRegistryCount =
    sizeof(kRegistry) / sizeof(kRegistry[0]);

const char* bus_transport_known_names() { return "mqtt uart can"; }

static bool known(const char* name) {
    if (!name) return false;
    for (size_t i = 0; i < kRegistryCount; i++)
        if (strcmp(kRegistry[i].name, name) == 0) return true;
    return false;
}

/* ------------------------------------------------------------------ */
/* The refusal ([CXC-BOARD-HW-UART-SLAVE] — see the header comment)     */
/* ------------------------------------------------------------------ */

class MisconfiguredTransport final : public BusTransport {
public:
    const char* name() const override { return "MISCONFIGURED"; }

    /* TRUE, deliberately.  This board carries no traffic on any substrate, so
     * the only way anyone reaches it to fix the selection is over the radio.
     * Refusing the network here would turn a typo into a board that needs a
     * serial cable — on exactly the boards whose point is not having one. */
    bool needs_network() const override { return true; }

    bool start(const char*) override { complain(); return false; }
    void stop() override {}
    bool connected() const override { return false; }
    void subscribe(const char*) override {}
    void set_subscriptions(const char* const*, size_t) override {}
    bool poll(BusEvent*) override { return false; }

    /* pending() is read once per render pass, which makes it the one place
     * that can keep saying so for as long as the fault lasts.  A single
     * boot-time error scrolls off a console; this does not. */
    size_t pending() const override {
        const_cast<MisconfiguredTransport*>(this)->complain();
        return 0;
    }
    int64_t pending_since_us() const override { return 0; }
    void publish_state(const char*, const char*) override {}
    void publish_number(const char*, double) override {}
    bool publish_raw(const char*, BusMessageType, const uint8_t*,
                     size_t) override { complain(); return false; }
    size_t max_raw_value() const override { return 0; }

private:
    int64_t last_us_ = 0;
    void complain() {
        int64_t now = esp_timer_get_time();
        if (last_us_ && now - last_us_ < 10 * 1000000LL) return;
        last_us_ = now;
        ESP_LOGE(TAG, "NO BUS: transport %s is not a transport this build "
                 "knows (known: %s).  Nothing is published and nothing is "
                 "received.  Fix the NVS key '%s' in namespace '%s'.",
                 bus_transport_selected_name(), bus_transport_known_names(),
                 CXC_NVS_KEY_TRANSPORT, CXC_NVS_NAMESPACE);
    }
};

/* ------------------------------------------------------------------ */
/* Selection                                                           */
/* ------------------------------------------------------------------ */

static char s_selected[16] = "";
static const char* s_source = "default";

static bool nvs_read_selection(char* out, size_t cap) {
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) return false;
    size_t len = cap;
    esp_err_t e = nvs_get_str(h, CXC_NVS_KEY_TRANSPORT, out, &len);
    nvs_close(h);
    return e == ESP_OK && out[0];
}

BusTransport& bus_transport() {
    static BusTransport* chosen = nullptr;
    if (chosen) return *chosen;

    /* NVS is already initialised by wifi_init() on every current board;
     * do it defensively so selection never depends on boot ORDER. */
    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    char from_nvs[16] = "";
    if (nvs_read_selection(from_nvs, sizeof(from_nvs))) {
        snprintf(s_selected, sizeof(s_selected), "%s", from_nvs);
        s_source = "nvs";
    } else {
        snprintf(s_selected, sizeof(s_selected), "%s",
                 CXC_BUS_TRANSPORT_DEFAULT);
        s_source = "default";
    }

    for (size_t i = 0; i < kRegistryCount; i++) {
        if (strcmp(kRegistry[i].name, s_selected) == 0) {
            chosen = &kRegistry[i].get();
            ESP_LOGI(TAG, "transport: %s (from %s; known: %s)", s_selected,
                     s_source, bus_transport_known_names());
            return *chosen;
        }
    }

    static MisconfiguredTransport bad;
    chosen = &bad;
    ESP_LOGE(TAG, "UNKNOWN transport '%s' (from %s).  Known: %s.  This is "
             "NOT falling back to a working transport — a device that "
             "quietly ignores its own configuration is the defect "
             "transport_registry.py's rule 1 exists to prevent.",
             s_selected, s_source, bus_transport_known_names());
    return *chosen;
}

const char* bus_transport_selected_name() {
    return s_selected[0] ? s_selected : CXC_BUS_TRANSPORT_DEFAULT;
}

const char* bus_transport_selection_source() { return s_source; }

bool bus_transport_set_selection(const char* name) {
    if (!known(name)) {
        ESP_LOGE(TAG, "REFUSED transport selection '%s' — known: %s.  "
                 "Nothing was stored.", name ? name : "(null)",
                 bus_transport_known_names());
        return false;
    }
    nvs_handle_t h;
    if (nvs_open(CXC_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        ESP_LOGE(TAG, "nvs_open failed — selection NOT stored");
        return false;
    }
    esp_err_t e = nvs_set_str(h, CXC_NVS_KEY_TRANSPORT, name);
    if (e == ESP_OK) e = nvs_commit(h);
    nvs_close(h);
    if (e != ESP_OK) {
        ESP_LOGE(TAG, "nvs write failed (%s) — selection NOT stored",
                 esp_err_to_name(e));
        return false;
    }
    ESP_LOGW(TAG, "transport selection stored: '%s' — takes effect on the "
             "NEXT boot (this link is still %s).  Rewire FIRST, then power "
             "cycle.", name, bus_transport_selected_name());
    return true;
}

/* ------------------------------------------------------------------ */
/* `_device/<id>/transport` — settable over the CURRENT link            */
/* ------------------------------------------------------------------ */

static char s_ctrl_ns[128] = "";
static char s_status_ns[128] = "";

void bus_transport_control_init(const char* device_id) {
    if (!device_id || !device_id[0]) return;
    snprintf(s_ctrl_ns, sizeof(s_ctrl_ns), "_device/%s/transport", device_id);
    /* The same status namespace restart.cpp acks on — a device reports its
     * own state in one place, and a COMMAND namespace never doubles as a
     * state namespace. */
    snprintf(s_status_ns, sizeof(s_status_ns), "_device/%s/status", device_id);
}

size_t bus_transport_control_namespaces(const char** out, size_t cap) {
    if (!s_ctrl_ns[0] || cap < 1 || !out) return 0;
    out[0] = s_ctrl_ns;
    return 1;
}

bool bus_transport_on_bus_value(const char* ns, int kind, double num,
                                bool b, const char* str) {
    (void)num;
    (void)b;
    if (!ns || !s_ctrl_ns[0] || strcmp(ns, s_ctrl_ns) != 0) return false;

    /* A transport is named, never numbered: only a STRING is a command here.
     *
     * ⚠ ONE NAMESPACE, TWO DIRECTIONS, AND `kind` IS THE DISCRIMINATOR.
     * `_device/<id>/transport` is a COMMAND (a transport NAME, in) and, since
     * workstream 0.2, also this device's ANNOUNCEMENT of the substrate it is
     * actually on (a packed u8, out — announce_pack.h).  The two are told
     * apart by the value's KIND, which every substrate carries: a STRING is a
     * command, a NUMBER is state.
     *
     * That collision is not hypothetical.  MQTT 3.1.1 has no no-local option,
     * so the broker echoes this device's own announcement straight back to the
     * device, which is subscribed here.  Before this branch existed that echo
     * produced an ESP_LOGE every 60 seconds, forever, on every board — a log
     * line that says "something is wrong" when nothing is, which is how a
     * console stops being read.
     *
     * It is a DEBUG line rather than silence because a NUMBER on this
     * namespace from a HUMAN is still a mistake worth being able to find; and
     * it is not an ERROR because the overwhelmingly common case is the device
     * hearing itself. */
    if (kind != (int)BusEvent::Kind::String || !str || !str[0]) {
        ESP_LOGD(TAG, "%s: non-string value ignored (this is normally this "
                 "device's own announcement echoing back; a transport COMMAND "
                 "is a NAME — one of: %s)", s_ctrl_ns,
                 bus_transport_known_names());
        return true;
    }

    /* The payload is a NAME optionally followed by `key=value` wiring, the
     * one-line form of transport_registry.py's spec dict:
     *     "uart"
     *     "uart port=0 tx=43 rx=44 baudrate=115200"
     *     "can tx=5 rx=6 bitrate=500000"
     *     "can listen=1"   (receive only — see bus_can.h; a workaround)
     *     "can listen=0"   (restore normal mode after a transceiver swap)
     * so a bring-up rig is re-pointed over the bus instead of by rebuild.
     * Unrecognised keys are REPORTED, never ignored — a typo'd `bitrate=`
     * that silently did nothing would leave the operator debugging a link
     * that was never reconfigured. */
    char name[16] = "";
    int32_t port = -1, tx = -1, rx = -1, baud = -1, bitrate = -1;
    int32_t listen = -1;                /* CAN only; -1 = not mentioned */
    bool bad_key = false;
    {
        const char* p = str;
        while (*p == ' ') p++;
        size_t n = 0;
        while (p[n] && p[n] != ' ' && n < sizeof(name) - 1) n++;
        memcpy(name, p, n);
        name[n] = '\0';
        p += n;
        while (*p) {
            while (*p == ' ') p++;
            if (!*p) break;
            char key[16] = "";
            size_t k = 0;
            while (p[k] && p[k] != '=' && p[k] != ' ' && k < sizeof(key) - 1)
                k++;
            memcpy(key, p, k);
            key[k] = '\0';
            p += k;
            if (*p != '=') { if (*p) p++; bad_key = true; continue; }
            p++;
            long v = strtol(p, (char**)&p, 10);
            if (strcmp(key, "port") == 0) port = (int32_t)v;
            else if (strcmp(key, "tx") == 0) tx = (int32_t)v;
            else if (strcmp(key, "rx") == 0) rx = (int32_t)v;
            else if (strcmp(key, "baudrate") == 0 ||
                     strcmp(key, "baud") == 0) baud = (int32_t)v;
            else if (strcmp(key, "bitrate") == 0) bitrate = (int32_t)v;
            /* CAN only.  `listen=1` is TWAI_MODE_LISTEN_ONLY — receive
             * only, never transmit, never ACK.  It exists to keep a node
             * usable behind a transceiver whose driver is dead, and it is
             * settable BOTH WAYS over the link so replacing the part
             * restores normal mode without a reflash. */
            else if (strcmp(key, "listen") == 0) listen = (int32_t)v;
            else {
                ESP_LOGE(TAG, "%s: unknown wiring key '%s' — IGNORED "
                         "(known: port tx rx baudrate bitrate listen)",
                         s_ctrl_ns, key);
                bad_key = true;
            }
        }
    }

    /* Wiring is stored against the transport BEING SELECTED.  `tx`/`rx` mean
     * different pins on a UART and on TWAI, and writing both sets from one
     * command would silently re-pin a link the operator did not mention. */
    if (strcmp(name, "can") == 0) {
        if (tx >= 0 || rx >= 0 || bitrate > 0)
            bus_can_set_config(tx, rx, bitrate);
        if (listen >= 0) bus_can_set_listen_only(listen != 0);
        if (port >= 0 || baud > 0)
            ESP_LOGE(TAG, "%s: port=/baudrate= are UART keys and were IGNORED "
                     "for transport 'can' (it takes tx rx bitrate listen)",
                     s_ctrl_ns);
    } else {
        if (port >= 0 || tx >= 0 || rx >= 0 || baud > 0)
            bus_uart_set_config(port, tx, rx, baud);
        if (bitrate > 0)
            ESP_LOGE(TAG, "%s: bitrate= is a CAN key and was IGNORED for "
                     "transport '%s'", s_ctrl_ns, name);
        if (listen >= 0)
            ESP_LOGE(TAG, "%s: listen= is a CAN key and was IGNORED for "
                     "transport '%s'", s_ctrl_ns, name);
    }

    char ack[64];
    if (bus_transport_set_selection(name)) {
        snprintf(ack, sizeof(ack), "transport=%s next boot%s", name,
                 bad_key ? " (UNKNOWN KEY IGNORED)" : "");
    } else {
        snprintf(ack, sizeof(ack), "transport=%s REFUSED", name);
    }
    if (s_status_ns[0]) bus_transport().publish_state(s_status_ns, ack);
    return true;
}
