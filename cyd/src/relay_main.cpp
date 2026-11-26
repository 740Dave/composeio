/*
 * relay_main.cpp — a headless ComposeBus RELAY node.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY] [CXC-CONFIG-BUS-TTL]
 *
 * CAN upstream, UART downstream.  It drives no glass, fetches no config and
 * holds no scene; it repeats frames between two substrates chunk by chunk and
 * reassembles nothing.  Built by [env:esp32_can_relay].
 *
 * WHY THIS APP EXISTS SEPARATELY FROM main.cpp.  A relay is the ONE node that
 * legitimately holds two transports at once, so it cannot go through
 * `bus_transport()` — that function resolves exactly ONE substrate per boot,
 * and that singularity is a property worth keeping for every other node in the
 * fleet.  The relay therefore names `bus_can_transport()` and
 * `bus_uart_transport()` explicitly, which is the same thing a BRIDGE does
 * (`services/can_mqtt_bridge` names MQTT and CAN).  A Transport lets one app
 * speak one substrate; a relay/bridge translates between two live ones — they
 * are complementary, not alternatives.
 *
 * WHY AN ESP32 AND NOT THE CH32V203 — see config_relay.h.  Short version: two
 * new things at once (a protocol and a toolchain) produce a failure nobody can
 * attribute, and the relay logic is transport-agnostic so the port is later a
 * driver swap.
 *
 * VERIFICATION STATUS, stated so a green build cannot imply more than it is:
 * this app has been COMPILED, and config_relay's forwarding rule and TTL
 * ladder are host-tested against the Python oracle (env:native_config_chunk).
 * Whether it has carried a live transfer between two real segments depends on
 * hardware that may not exist yet; the boot banner says which, every time.
 */

#ifdef CXC_RELAY_APP

#include "bus_can.h"
#include "bus_uart.h"
#include "config_relay.h"
#include "config_fetch.h"      /* device_id() only */

#include <cstdio>

#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char* TAG = "relay";

/* A relay's own diagnostics go UPSTREAM: the head is what can act on a TTL
 * expiry, and a downstream leaf can do nothing with the news. */
static void announce_upstream(const char* ns, const char* value) {
    bus_can_transport().publish_state(ns, value);
}

extern "C" void app_main(void) {
    ESP_LOGI(TAG, "ComposeIO config RELAY — CAN upstream, UART downstream");
    ESP_LOGI(TAG, "Device id: %s", device_id());
    ESP_LOGI(TAG, "Firmware build: %s %s", __DATE__, __TIME__);

    BusTransport& up = bus_can_transport();
    BusTransport& down = bus_uart_transport();

    bool up_ok = up.start(device_id());
    bool down_ok = down.start(device_id());

    /* A KNOWN substrate whose backend is unreachable degrades LOUDLY and the
     * node keeps running (transport_registry.py rule 2) — but a relay with one
     * dead side relays nothing, so it must not look alive. */
    if (!up_ok || !down_ok) {
        ESP_LOGE(TAG, "RELAY IS HALF-OPEN: upstream(CAN)=%s downstream(UART)=%s"
                 " — nothing will cross this node until both are up.  Check "
                 "the pin assignments (NVS can.tx/can.rx, uart.tx/uart.rx) and "
                 "that a CAN TRANSCEIVER is powered and the segment is "
                 "terminated.", up_ok ? "up" : "DOWN", down_ok ? "up" : "DOWN");
    }

    config_relay_init(device_id(), bus_uart_send_raw, bus_can_send_raw,
                      up_ok ? announce_upstream : nullptr);

    /* PROMISCUOUS, in both directions.  A relay forwards for devices it has
     * never heard of; the wire carries a namespace HASH and a hash is not
     * reversible, so relaying happens below the namespace and without a
     * subscription set.  Giving a relay a subscription set would make topology
     * a thing somebody configures — the leak this design refuses. */
    bus_can_set_raw_sink(config_relay_from_upstream, nullptr);
    bus_uart_set_raw_sink(config_relay_from_downstream, nullptr);

    ESP_LOGW(TAG, "NOTE: this node reassembles NOTHING.  A config transfer "
             "crosses it frame by frame; only the endpoint holds a payload "
             "([CXC-CONFIG-BUS-RELAY]).");

    /* A relay that is silently doing nothing looks exactly like a relay that
     * is working, so it says what it has moved — on the console every 10 s and
     * on the bus with the same numbers. */
    uint32_t last_down = 0, last_up = 0;
    while (true) {
        vTaskDelay(pdMS_TO_TICKS(10000));
        uint32_t d = config_relay_down_frames();
        uint32_t u = config_relay_up_frames();
        ESP_LOGI(TAG, "relay: %lu frames down (+%lu), %lu up (+%lu), "
                 "%lu TTL expiries, %lu drops, %lu membrane-blocked (local)",
                 (unsigned long)d, (unsigned long)(d - last_down),
                 (unsigned long)u, (unsigned long)(u - last_up),
                 (unsigned long)config_relay_ttl_expiries(),
                 (unsigned long)config_relay_drops(),
                 (unsigned long)config_relay_membrane_blocked());
        last_down = d;
        last_up = u;
    }
}

#endif /* CXC_RELAY_APP */
