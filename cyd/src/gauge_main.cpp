/*
 * gauge_main.cpp — the app shell for `rack_triple_gauge`.
 * [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW] [CXC-VISION-SEMANTIC-REALIZATION]
 * [DP-CIO-6] [CXC-ER-RESTART-BUS] [CXC-R-BOOT-FUNGIBLE]
 *
 * A DIRECT WiFi/MQTT ComposeBus peer.  Boot sequence, and it is main.cpp's
 * sequence with the raster half removed rather than a second design:
 *
 *   1. banner + NVS
 *   2. realization backend init (I2C, expander, LEDC)     <- rung 3
 *   3. HOMING SWEEP — 5.4 s, blocking the acceptance of values but NOT the
 *      loop, so the transport and the announcer come up in parallel
 *   4. transport seam (bus_transport(), NVS-selectable)   <- DP-CIO-6
 *   5. restart / transport / announce control planes      <- shared shell
 *   6. config fetch over HTTP from bus_storage            <- ruled
 *   7. bind the channel set, subscribe, run
 *
 * ⚠ WHY IT GOES THROUGH bus_transport() AND NOT bus_mqtt_* DIRECTLY.
 * can_bringup_main.cpp names its substrate on purpose because it IS a
 * substrate test.  This board is a LEAF, and a leaf that names MQTT is the
 * coupling [SPEC:CXC-BOARD-HW]'s known gap is about.  It is selected onto
 * MQTT by CXC_BUS_TRANSPORT_DEFAULT and can be flipped to UART by a bus
 * command with no reflash — which for a rack-mounted gauge cluster with no
 * console is not a nicety.
 */

#ifdef CXC_RACK_TRIPLE_GAUGE_APP

#include "announce.h"
#include "bus_transport.h"
#include "config_fetch.h"
#include "config_source.h"
#include "freshness.h"
#include "gauge_bind.h"
#include "rack_triple_gauge_panel.h"
#include "realization.h"
#include "restart.h"
#include "wifi.h"
#include "yaml_min.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs_flash.h"

static const char* TAG = "gauge_main";

/* Board entry points (boards/rack_triple_gauge.cpp). */
bool rack_triple_gauge_init();
void rack_triple_gauge_home(uint32_t now_ms);
void rack_triple_gauge_sweep_test();
int  rack_triple_gauge_sweep_stage();
void rack_triple_gauge_sweep_advance();
bool rack_triple_gauge_at_target();

static GaugeBindings s_bind;
static YamlDoc       s_doc;              /* owns the strings s_bind points at */
static uint32_t      s_dropped_pre_home = 0;
static const uint32_t kRestartBootGuardMs = 3000;

static void bus_publish_state(const char* ns, const char* value) {
    bus_transport().publish_state(ns, value);
}
static void reboot_now(void) {
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_restart();
}

static uint32_t now_ms() {
    return (uint32_t)(esp_timer_get_time() / 1000);
}

/* ------------------------------------------------------------------ */
/* Arrival routing                                                     */
/* ------------------------------------------------------------------ */
/* THE FRESHNESS SINK ON THE ACTUATOR TIER — the identical entry point a real
 * arrival uses, which here is GaugeBindings::dispatch().
 * [SPEC:CXC-FRESHNESS], David's ruling.
 *
 * ⛔ THIS IS THE TIER THE RULING IS ABOUT.  The x27.168 stepper is DEAD
 * RECKONING with NO feedback and LATCHES its position forever (x27168.h:76-81)
 * — so a publisher that dies leaves a physical needle pointing at a number
 * that stopped being true hours ago, and every machine signal on the board
 * agrees with it.  There is nothing on this board that can notice.
 *
 * ⚠ AND NOTE WHAT IS NOT HERE: no branch on the verdict, no `if (stale)`, no
 * park-the-needle call.  `<ns>/_fresh` is dispatched as an ORDINARY CHANNEL
 * VALUE, so whether a needle parks, dims a lamp or does nothing at all is
 * decided by the gauge YAML's channel set — configuration, not firmware.  See
 * the gap report: whether that YAML can currently EXPRESS "park on 0" is a
 * separate question from whether the value reaches it. */
struct GaugeFreshCtx { RealizationBackend* be; GaugeBindings* bind; };

static void freshness_gauge_sink(void* user, const char* ns, int kind,
                                 double num, bool b, const char* str) {
    GaugeFreshCtx* c = (GaugeFreshCtx*)user;
    double v = 0.0;
    switch (kind) {
        case 0: v = num; break;
        case 1: v = b ? 1.0 : 0.0; break;
        default: v = atof(str ? str : ""); break;
    }
    c->bind->dispatch(*c->be, ns, v);
}

static void drain_bus(RealizationBackend& be) {
    BusEvent ev;
    while (bus_transport().poll(&ev)) {
        const uint32_t t_ms = now_ms();

        /* ⛔ THE FRESHNESS RECEIVER SEES EVERY ARRIVAL FIRST — before the
         * control-plane sieve and, decisively, BEFORE THE HOMING GATE below.
         * A value dropped because the needles have not homed is still
         * EVIDENCE THAT THE PUBLISHER IS ALIVE, and stamping it after the gate
         * would mark a healthy publisher stale for the whole 5.4 s of homing
         * and then republish that verdict onto the needle it was protecting. */
        if (ev.unknown) {
            freshness_note_wire_unknown(ev.ns, t_ms);
            continue;
        }
        freshness_note_arrival(ev.ns, t_ms);
        if (freshness_consume_heartbeat(ev.ns, (int)ev.kind, ev.num, ev.str,
                                        t_ms))
            continue;

        /* Control plane first, exactly as main.cpp orders it — a control
         * command must never be routed to the scene. */
        if (restart_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str,
                                 now_ms()))
            continue;
        if (bus_transport_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        if (announce_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        if (config_source_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;

        /* ⛔ THE HOMING GATE.  A value arriving before the needles have
         * homed is DROPPED, counted and reported — never applied.  Applying
         * it would drive a real needle against a zero that does not exist
         * yet, and the result is a gauge that reads plausibly and is wrong
         * by the homing offset for the whole session, with every machine
         * signal agreeing.  Dropping it is visible (the needle stays put);
         * honouring it is invisible.  That asymmetry is the entire reason
         * ready() is on the seam. */
        if (!be.ready()) {
            if (++s_dropped_pre_home == 1 || (s_dropped_pre_home % 50) == 0)
                ESP_LOGW(TAG, "dropped %lu bus value(s) so far — backend "
                         "NOT READY (%s).  This is correct: a value applied "
                         "before homing is a needle that lies.",
                         (unsigned long)s_dropped_pre_home, be.status());
            continue;
        }

        double v = 0.0;
        switch (ev.kind) {
        case BusEvent::Kind::Number: v = ev.num; break;
        case BusEvent::Kind::Bool:   v = ev.b ? 1.0 : 0.0; break;
        case BusEvent::Kind::String: v = atof(ev.str); break;
        }
        s_bind.dispatch(be, ev.ns, v);
    }
}

/* ------------------------------------------------------------------ */
extern "C" void app_main(void) {
    RealizationBackend& be = realization_backend();

    ESP_LOGI(TAG, "==================================================");
    ESP_LOGI(TAG, "ComposeIO rack_triple_gauge — FIRST NON-RASTER BACKEND");
    ESP_LOGI(TAG, "build: %08lx == %s",
             (unsigned long)cxc_build_fingerprint(), cxc_build_stamp());
    ESP_LOGI(TAG, "backend: %s", be.name());
    ESP_LOGI(TAG, "device id: %s", device_id());
    ESP_LOGI(TAG, "I2C SDA=%d SCL=%d @%lu Hz, MCP23017 @0x%02X",
             kG3SdaGpio, kG3SclGpio, (unsigned long)kG3I2cHz, kG3McpAddr);
    ESP_LOGI(TAG, "==================================================");

    esp_err_t e = nvs_flash_init();
    if (e == ESP_ERR_NVS_NO_FREE_PAGES || e == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        e = nvs_flash_init();
    }
    if (e != ESP_OK)
        ESP_LOGW(TAG, "nvs_flash_init: %s — transport/config-source "
                 "overrides are NOT readable this boot", esp_err_to_name(e));

    /* 2. The backend.  If the expander is not there the needles are dead;
     *    say so and KEEP RUNNING — a board that can still announce its own
     *    failure over the bus is worth far more than one that halts. */
    const bool hw_ok = rack_triple_gauge_init();
    if (!hw_ok)
        ESP_LOGE(TAG, "*** BACKEND INIT FAILED: %s.  Continuing so this "
                 "board can still be seen and diagnosed over the bus. ***",
                 be.status());

    /* 3. HOMING starts NOW, before the radio, because it is 5.4 s of wall
     *    clock that overlaps a DHCP lease for free.  It does not block. */
    if (hw_ok) rack_triple_gauge_home(now_ms());

    /* 4. Transport + config source, both resolved BEFORE the radio, so a
     *    board that needs neither never takes a lease (DP-CIO-6). */
    BusTransport& bus = bus_transport();
    const bool need_net = bus.needs_network() || config_source_needs_network();
    ESP_LOGI(TAG, "transport: %s (from %s) | config: %s (from %s) | WiFi %s",
             bus_transport_selected_name(), bus_transport_selection_source(),
             config_source_selected_name(), config_source_selection_source(),
             need_net ? "REQUIRED" : "NOT REQUIRED");
    if (need_net) wifi_init();

    bus.start(device_id());

    /* THE RECEIVER'S STANDING TO JUDGE, wired to the SELECTED transport's real
     * link instrument ([SPEC:CXC-FRESHNESS]).  Once, here, right after the
     * link comes up — it survives scene swaps because impairment is a property
     * of the TRANSPORT, not of the composition.
     *
     * Without it a bus-off board reports the ENTIRE FLEET dead: every
     * namespace times out at once, consistently and confidently, and the
     * uniformity is exactly what makes it invisible. */
    freshness_attach_link_probe();

    /* 5. The shared control planes — inherited unmodified, which is the
     *    whole point of them living in the app shell ([CXC-ER-C19]). */
    restart_init(device_id(), bus_publish_state, reboot_now,
                 kRestartBootGuardMs);
    const char* ns[4];
    for (size_t i = 0, n = restart_namespaces(ns, 4); i < n; i++)
        bus.subscribe(ns[i]);
    bus_transport_control_init(device_id());
    for (size_t i = 0, n = bus_transport_control_namespaces(ns, 2); i < n; i++)
        bus.subscribe(ns[i]);
    announce_init(device_id(), cxc_build_fingerprint());
    announce_set_network_up(need_net && wifi_get_ip() && wifi_get_ip()[0]);
    for (size_t i = 0, n = announce_poll_namespaces(ns, 2); i < n; i++)
        bus.subscribe(ns[i]);
    config_source_control_init(device_id(), bus_publish_state);
    for (size_t i = 0, n = config_source_control_namespaces(ns, 2); i < n; i++)
        bus.subscribe(ns[i]);

    /* 6. Config over HTTP from bus_storage — RULED for this board: the path
     *    the Qualia already uses, not the chunked config-over-bus machinery.
     *    The device stays FUNGIBLE: nothing about which gauge shows what is
     *    compiled in ([CXC-R-BOOT-FUNGIBLE]). */
    size_t len = 0;
    char* text = config_fetch(&len);
    if (text) {
        const YamlNode* root = s_doc.parse(text, len);
        free(text);
        if (root) {
            gauge_bind(root, be, &s_bind);
            /* The freshness contract, from the SAME document the channel set
             * came from ([SPEC:CXC-FRESHNESS]). */
            int contradictions = 0;
            const int declared =
                freshness_declare_from_yaml(freshness(), root, &contradictions);
            if (freshness().refused() || contradictions)
                ESP_LOGE(TAG, "freshness: %lu declaration(s) REFUSED and %d "
                         "contradictory — those namespaces are watched by "
                         "NOTHING (capacity %d)",
                         (unsigned long)freshness().refused(), contradictions,
                         kMaxFreshnessEntries);
            ESP_LOGI(TAG, "freshness: %d namespace(s) declared a contract",
                     declared);
            /* SENSITIVITY LOST TO THE SAMPLER, said out loud.  A cadence faster than
             * the 250 ms evaluator keeps its contract but needs ~ceil(eval/interval)
             * consecutive misses instead of 3 before it is condemned.  That is the
             * honest price of a sampled reader; it is REPORTED rather than being the
             * silent difference between two constants nobody compares. */
            if (freshness().sampling_limited())
                ESP_LOGW(TAG, "freshness: %lu namespace(s) declare a cadence FASTER "
                         "than the %lu ms evaluator — their timeout is floored to it, "
                         "so they detect ~N consecutive misses, not one",
                         (unsigned long)freshness().sampling_limited(),
                         (unsigned long)kFreshnessEvalIntervalMs);
            announce_set_scene_hash((uint16_t)(len & 0xFFFF));
        } else {
            ESP_LOGE(TAG, "scene parse failed: %s", s_doc.error());
        }
    } else {
        ESP_LOGE(TAG, "*** NO SCENE FETCHED.  The needles will home and then "
                 "sit at zero forever.  A homed gauge reading zero and a "
                 "gauge with no binding look IDENTICAL on the dial — this "
                 "log line is the only difference. ***");
    }

    /* 7. Subscribe exactly what the channel set bound — PLUS every declared
     *    node's heartbeat, and nothing else.  On a framed substrate an
     *    inbound frame is matched by ns HASH, and a CRC-16 does not invert, so
     *    a beat from a node this board never declared cannot be named on
     *    arrival.  Declaring the node is what makes the beat receivable
     *    ([CXC-FRESH-HEARTBEAT-RX]). */
    const char* subs[kMaxGaugeInstances * kMaxChannelsPerInstance +
                     kMaxFreshnessNodes];
    size_t nsub = s_bind.namespaces(subs, kMaxGaugeInstances *
                                              kMaxChannelsPerInstance);
    const char* hb[kMaxFreshnessNodes];
    const size_t hbn = freshness().heartbeat_namespaces(hb, kMaxFreshnessNodes);
    for (size_t i = 0; i < hbn; i++) subs[nsub++] = hb[i];
    bus.set_subscriptions(subs, nsub);
    for (size_t i = 0; i < nsub; i++)
        ESP_LOGI(TAG, "subscribed: %s", subs[i]);

    ESP_LOGW(TAG, "READY-WHEN: %s", be.status());

    /* ---- the loop --------------------------------------------------
     * tick() before drain() on purpose: a step that is due should go out
     * before the queue is drained, or a burst of arrivals delays every
     * needle by the drain time and the motion visibly stutters. */
    uint32_t last_status_ms = 0;
    bool sweep_done = false;
    for (;;) {
        const uint32_t t = now_ms();
        be.tick(t);
        drain_bus(be);
        announce_tick(t);
        /* THE FRESHNESS TICK — a READ, on a loop the firmware already runs.
         * No new timer, no new FreeRTOS task ([CXC-FRESH-LAZY]).  It is
         * internally rate-limited to 4 Hz, so this 1 ms loop pays for a table
         * walk 4 times a second and for a publish only on a TRANSITION. */
        {
            GaugeFreshCtx fc{&be, &s_bind};
            freshness_tick(t, freshness_gauge_sink, &fc);
        }

        /* Bench sweep, ONCE, immediately after homing: the negative control
         * for the slew rate.  Costs 5 s of boot and is the only test on
         * this board that can FAIL VISIBLY. */
        if (!sweep_done && be.ready()) {
#ifdef CXC_G3_SWEEP_TEST
            if (rack_triple_gauge_sweep_stage() == 0) {
                rack_triple_gauge_sweep_test();
            } else if (rack_triple_gauge_at_target()) {
                rack_triple_gauge_sweep_advance();
                if (rack_triple_gauge_sweep_stage() == 0) sweep_done = true;
            }
#else
            sweep_done = true;
#endif
        }

        if (t - last_status_ms > 5000) {
            last_status_ms = t;
            ESP_LOGI(TAG, "%s | link %s | dropped-pre-home %lu",
                     be.status(), bus.connected() ? "up" : "DOWN",
                     (unsigned long)s_dropped_pre_home);
        }

        /* 1 ms cadence.  The fastest step period on this board is
         * 1000/800 = 1.25 ms, so a 1 ms tick can always meet it, and the
         * jitter it introduces (< 1 step period) is below the motor's
         * ability to care. */
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}

#endif /* CXC_RACK_TRIPLE_GAUGE_APP */
