/*
 * bus_mqtt.cpp — ComposeBus MQTT client (ESP-IDF esp-mqtt), R3.
 * See bus_mqtt.h.  Wire format parity with R1's mqttCallback:
 * payload ns wins, else strip {prefix}/{scope}/ off the topic;
 * loop-guard on src; state messages only.
 *
 * 2026-08-12: this became the first implementation BEHIND the
 * `BusTransport` seam (bus_transport.h).  The protocol code below is
 * unchanged — the file-scope state, the JSON scanner, the event handler,
 * the subscription bookkeeping and the publish payloads are all as they
 * were; MqttTransport at the bottom is a facade over them.  That is
 * deliberate: a refactor whose acceptance bar is SAMENESS should be
 * readable as "nothing moved" in the diff.
 */

#include "bus_mqtt.h"
#include "announce_pack.h"
#include "bus_wire_codes.h"
#include "restart.h"
#include "bus_binding.h"   /* [CXC-REPOINT] active broker URI + binding intercept */
#include "wifi_config.h"

#include <cstdio>
#include <cstring>
#include <cstdlib>

#include "esp_log.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

static const char* TAG = "bus_mqtt";

#ifndef MQTT_BROKER_URI
/* Ecosystem broker (Composer / render smokes / headless all ride it). */
#define MQTT_BROKER_URI "mqtt://mqtt-broker.local:1883"
#endif
#ifndef CIO_TOPIC_PREFIX
#define CIO_TOPIC_PREFIX "composeio"
#endif

static constexpr int kMaxSubs = 16;
static constexpr int kQueueDepth = 16;

static esp_mqtt_client_handle_t s_client = nullptr;
static QueueHandle_t s_queue = nullptr;
/* esp_timer stamp of the arrival that made the queue non-empty (0 when
 * idle) — the bus->glass latency origin. */
static volatile int64_t s_pending_since_us = 0;
static volatile bool s_connected = false;
static char s_client_id[32] = "cyd-display";
static char s_subs[kMaxSubs][96];
static int s_sub_count = 0;
/* Persistent control-plane subscriptions (the `_device/` restart
 * namespaces) — kept across scene swaps, re-subscribed on reconnect. */
static constexpr int kMaxPersist = 4;
static char s_persist[kMaxPersist][96];
static int s_persist_count = 0;
static uint32_t s_seq = 0;

/* ------------------------------------------------------------------ */
/* Minimal flat-JSON field extraction (BusMessage payloads)            */
/* ------------------------------------------------------------------ */

/* Find `"key"` at object top level and return a pointer to its value
 * (first non-space char after the colon), or nullptr.  Flat scan — good
 * for the flat BusMessage dict; a nested dict as a VALUE cannot spoof a
 * key because we require the quote-key-quote-colon shape. */
static const char* json_value(const char* s, const char* key) {
    size_t klen = strlen(key);
    const char* p = s;
    while ((p = strchr(p, '"')) != nullptr) {
        const char* k = p + 1;
        if (strncmp(k, key, klen) == 0 && k[klen] == '"') {
            const char* q = k + klen + 1;
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

/* Copy a JSON string literal at *p* into buf (unescaping \" \\ \/;
 * other escapes copied through).  Returns false if not a string. */
static bool json_string(const char* p, char* buf, size_t cap) {
    if (!p || *p != '"' || cap == 0) return false;
    p++;
    size_t n = 0;
    while (*p && *p != '"' && n + 1 < cap) {
        char c = *p;
        if (c == '\\' && p[1]) {
            p++;
            c = *p;
            if (c == 'n') c = '\n';
            else if (c == 't') c = '\t';
        }
        buf[n++] = c;
        p++;
    }
    buf[n] = '\0';
    return *p == '"';
}

/* Extract the ComposeBus namespace from a (topic, payload): the payload `ns`
 * field wins, else strip `{prefix}/{scope}/` off the topic (namespaces may
 * contain '/', so only the first two segments strip).  Returns false when no
 * namespace can be recovered.  Shared by the confirm-topic loop-guard exception
 * and the main ns-resolution below so they agree byte-for-byte. */
static bool extract_ns(const char* payload, const char* topic, int topic_len,
                       char* out, size_t cap) {
    if (json_string(json_value(payload, "ns"), out, cap) && out[0]) return true;
    char tbuf[128];
    int n = topic_len < (int)sizeof(tbuf) - 1 ? topic_len : (int)sizeof(tbuf) - 1;
    memcpy(tbuf, topic, n);
    tbuf[n] = '\0';
    const char* p = strchr(tbuf, '/');
    p = p ? strchr(p + 1, '/') : nullptr;
    if (!p) { out[0] = '\0'; return false; }
    snprintf(out, cap, "%s", p + 1);
    return out[0] != '\0';
}

/* ------------------------------------------------------------------ */
/* Inbound: MQTT data event -> BusEvent queue                          */
/* ------------------------------------------------------------------ */

/* Payload capacity mirrors BUS_EVENT_STR_CAP's tiering (bus_mqtt.h):
 * the Qualia tier accepts structured states (contact arrays); the CYD
 * band tier keeps its original budget. */
#ifdef QUALIA_DISPLAY
#define BUS_PAYLOAD_CAP 2048
#else
#define BUS_PAYLOAD_CAP 512
#endif

static void handle_data(const char* topic, int topic_len,
                        const char* data, int data_len) {
    /* STATIC buffers: handle_data runs only on the esp-mqtt event task
     * (single-threaded), and its stack (5 KB) cannot carry the enlarged
     * structured-state buffers. */
    static char payload[BUS_PAYLOAD_CAP];
    if (data_len >= (int)sizeof(payload)) {
        ESP_LOGW(TAG, "payload too large (%d) — dropped", data_len);
        return;
    }
    memcpy(payload, data, data_len);
    payload[data_len] = '\0';

    /* Loop guard: never re-ingest our own publishes off the broker echo —
     * with ONE scoped exception.  [CXC-REPOINT-CONFIRM]: the commit-confirmed
     * broker plane REQUIRES the device to receive its OWN confirm publication
     * fanned back by the broker (that round-trip is the proof of a LIVE,
     * fanning broker, not just a TCP accept).  That echo carries src ==
     * s_client_id, so the blanket guard was dropping it before the confirm
     * handler ran — the confirm plane was structurally unreachable.  The
     * exception is scoped to the `_device/<id>/bus_confirm` topic ALONE; every
     * OTHER self-publish is still dropped exactly as before. */
    char src[48] = "";
    json_string(json_value(payload, "src"), src, sizeof(src));
    if (strcmp(src, s_client_id) == 0) {
        bool self_confirm = false;
        const char* cns = bus_binding_confirm_namespace();
        if (cns && cns[0]) {
            char ns_probe[96] = "";
            if (extract_ns(payload, topic, topic_len, ns_probe, sizeof(ns_probe)))
                self_confirm = strcmp(ns_probe, cns) == 0;
        }
        if (!self_confirm) return;
    }

    /* state messages only (events/heartbeats carry no value).  The type
     * NAME comes from the one table (bus_wire_codes.h), so MQTT and the
     * framed transports agree on the vocabulary; an unrecognised name is
     * dropped exactly as a non-state type is — never treated as a state
     * ([BP-TYPE-CODES]). */
    char type[24] = "state";
    json_string(json_value(payload, "type"), type, sizeof(type));
    BusMessageType mtype;
    if (!bus_message_type_from_name(type, &mtype)) return;
    /* ⛔ HEARTBEATS PASS TOO — see the long note at the same filter in
     * bus_uart.cpp.  Dropping every non-State message here is what made
     * NODE_SILENT structurally unreachable ([CXC-FRESH-HEARTBEAT-RX]).  They
     * do NOT flood the scene binding path: the drain's control-plane sieve
     * consumes them one stage before scene_on_bus_value(), exactly as it
     * already consumes transport / announce / config_source commands.
     *
     * ⚠ ON MQTT THERE IS NO `unknown` REPRESENTATION.  0xFD is a FRAMED-wire
     * tag; JSON has no member of the value domain that means it, and minting
     * one (the string "unknown", or null) would collide with a legitimate
     * value.  So on this substrate WIRE_UNKNOWN cannot arrive and only the
     * receiver's own expiry produces it.  Named rather than papered over. */
    if (mtype != BusMessageType::State &&
        mtype != BusMessageType::Heartbeat) return;

    static BusEvent ev;               /* mqtt-task-only, like payload */
    memset(&ev, 0, sizeof(ev));
    ev.type = mtype;
    /* The topic is `{prefix}/{scope}/{ns}` — segment 2 IS the scope on
     * this substrate, and is what a framed transport carries in two bits.
     * Recorded here so BusEvent stops being MQTT-shaped; a payload
     * `"scope"` field is the fallback (transport_mqtt.py builds the topic
     * FROM the scope, so they agree). */
    {
        char tbuf[128];
        int n = topic_len < (int)sizeof(tbuf) - 1 ? topic_len
                                                  : (int)sizeof(tbuf) - 1;
        memcpy(tbuf, topic, n);
        tbuf[n] = '\0';
        const char* s = strchr(tbuf, '/');
        const char* e = s ? strchr(s + 1, '/') : nullptr;
        if (s && e && e > s + 1) {
            size_t m = (size_t)(e - (s + 1));
            if (m > sizeof(ev.scope) - 1) m = sizeof(ev.scope) - 1;
            memcpy(ev.scope, s + 1, m);
            ev.scope[m] = '\0';
        } else {
            json_string(json_value(payload, "scope"), ev.scope,
                        sizeof(ev.scope));
        }
    }
    /* namespace: payload ns wins; else strip {prefix}/{scope}/ off the
     * topic (namespaces may contain '/', so only two segments strip). */
    if (!json_string(json_value(payload, "ns"), ev.ns, sizeof(ev.ns)) ||
        !ev.ns[0]) {
        char tbuf[128];
        int n = topic_len < (int)sizeof(tbuf) - 1 ? topic_len
                                                  : (int)sizeof(tbuf) - 1;
        memcpy(tbuf, topic, n);
        tbuf[n] = '\0';
        const char* p = strchr(tbuf, '/');
        p = p ? strchr(p + 1, '/') : nullptr;
        if (!p) return;
        snprintf(ev.ns, sizeof(ev.ns), "%s", p + 1);
    }

    const char* val = json_value(payload, "val");
    if (!val) return;
    if (*val == '"') {
        if (!json_string(val, ev.str, sizeof(ev.str))) return;
        ev.kind = BusEvent::Kind::String;
    } else if (strncmp(val, "true", 4) == 0) {
        ev.kind = BusEvent::Kind::Bool;
        ev.b = true;
    } else if (strncmp(val, "false", 5) == 0) {
        ev.kind = BusEvent::Kind::Bool;
        ev.b = false;
    } else if (strncmp(val, "null", 4) == 0) {
        return;
    } else if (*val == '[' || *val == '{') {
        /* Structured JSON value (e.g. the dradis/contacts array,
         * [contacts-contract]): capture the balanced bracket span
         * VERBATIM as a String event — the consuming widget parses it.
         * String-aware depth scan so brackets inside literals can't
         * truncate the span. */
        const char* p = val;
        const char* end = nullptr;
        int depth = 0;
        bool in_str = false;
        for (; *p; p++) {
            char c = *p;
            if (in_str) {
                if (c == '\\' && p[1]) p++;
                else if (c == '"') in_str = false;
            } else if (c == '"') {
                in_str = true;
            } else if (c == '[' || c == '{') {
                depth++;
            } else if (c == ']' || c == '}') {
                if (--depth == 0) { end = p + 1; break; }
            }
        }
        if (!end) return;                       /* unbalanced — not a state */
        size_t n = (size_t)(end - val);
        if (n >= sizeof(ev.str)) {
            ESP_LOGW(TAG, "structured val too large (%u > %u) — %s dropped",
                     (unsigned)n, (unsigned)sizeof(ev.str) - 1, ev.ns);
            return;
        }
        memcpy(ev.str, val, n);
        ev.str[n] = '\0';
        ev.kind = BusEvent::Kind::String;
    } else {
        char* end = nullptr;
        double d = strtod(val, &end);
        if (end == val) return;            /* nested structure / garbage */
        ev.kind = BusEvent::Kind::Number;
        ev.num = d;
    }

    /* Control-plane intercept ([CXC-ER-RESTART-BUS]): a restart command
     * is consumed HERE, at receive time — so the boot-loop guard sees the
     * true arrival time (a command queued during boot and processed later
     * would defeat the window) and a restart works even if the render
     * loop is wedged.  now_ms = ms since boot (esp_timer). */
    if (restart_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str,
                             (uint32_t)(esp_timer_get_time() / 1000)))
        return;

    /* [CXC-REPOINT] the bus BINDING is retained STATE the device reacts to —
     * consumed HERE at receive time, beside restart, so a differing value on
     * the active tier arms a trial (writes NVS, restarts) reliably and so a
     * pre-scene arrival still lands.  An identical retained value is absorbed
     * (value-compare, [CXC-REPOINT-RETAINED-STATE]); never routed to a scene. */
    if (bus_binding_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
        return;

    /* [CXC-REPOINT-CONFIRM] the broker round-trip echo: our own confirm
     * publication comes straight back (MQTT 3.1.1 has no no-local), and its
     * arrival — carrying our own seq — is one of the two planes a trial needs. */
    if (ev.ns[0] && bus_binding_confirm_namespace()[0] &&
        strcmp(ev.ns, bus_binding_confirm_namespace()) == 0) {
        /* The confirm seq rides as a JSON STRING ("5"), because it is
         * published through the string-only BusBindingPublishFn ->
         * mqtt_publish_state, which quotes `val`.  Accept the actual kind:
         * parse a String seq as well as a Number, or the echo plane never
         * arms (the second half of the Finding-1 dead-code break). */
        if (ev.kind == BusEvent::Kind::Number)
            bus_binding_note_echo((uint32_t)ev.num);
        else if (ev.kind == BusEvent::Kind::String)
            bus_binding_note_echo((uint32_t)strtoul(ev.str, nullptr, 10));
        return;
    }

    if (s_queue) {
        /* Stamp the arrival that WAKES an idle queue: the render loop
         * reports (paint - this) as the bus->glass latency, which is the
         * number the operator actually feels. */
        if (uxQueueMessagesWaiting(s_queue) == 0)
            s_pending_since_us = esp_timer_get_time();
        if (xQueueSend(s_queue, &ev, 0) != pdTRUE)
            ESP_LOGW(TAG, "bus queue full — %s dropped", ev.ns);
    }
}

/* ------------------------------------------------------------------ */
/* Subscription management                                             */
/* ------------------------------------------------------------------ */

static void subscribe_all(void) {
    for (int i = 0; i < s_sub_count; i++) {
        char topic[128];
        snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                 s_subs[i]);
        esp_mqtt_client_subscribe(s_client, topic, 1);
        ESP_LOGI(TAG, "subscribed %s", topic);
    }
    /* Persistent control-plane subs (restart namespaces) — always
     * re-subscribed, including on every broker reconnect. */
    for (int i = 0; i < s_persist_count; i++) {
        char topic[128];
        snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                 s_persist[i]);
        esp_mqtt_client_subscribe(s_client, topic, 1);
        ESP_LOGI(TAG, "subscribed %s (persistent)", topic);
    }
}

static void mqtt_add_subscription(const char* ns) {
    if (!ns || !ns[0]) return;
    for (int i = 0; i < s_persist_count; i++)
        if (strcmp(s_persist[i], ns) == 0) return;   /* already present */
    if (s_persist_count >= kMaxPersist) {
        ESP_LOGW(TAG, "persistent subscription set full — %s dropped", ns);
        return;
    }
    snprintf(s_persist[s_persist_count], sizeof(s_persist[0]), "%s", ns);
    s_persist_count++;
    if (s_client && s_connected) {
        char topic[128];
        snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX, ns);
        esp_mqtt_client_subscribe(s_client, topic, 1);
        ESP_LOGI(TAG, "subscribed %s (persistent)", topic);
    }
}

static void mqtt_set_subscriptions(const char* const* namespaces,
                                   size_t count) {
    /* Unsubscribe the outgoing set (scene swap). */
    if (s_client && s_connected) {
        for (int i = 0; i < s_sub_count; i++) {
            char topic[128];
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                     s_subs[i]);
            esp_mqtt_client_unsubscribe(s_client, topic);
        }
    }
    s_sub_count = 0;
    for (size_t i = 0; i < count && s_sub_count < kMaxSubs; i++) {
        if (!namespaces[i] || !namespaces[i][0]) continue;
        snprintf(s_subs[s_sub_count], sizeof(s_subs[0]), "%s",
                 namespaces[i]);
        s_sub_count++;
    }
    if ((int)count > kMaxSubs)
        ESP_LOGW(TAG, "subscription set truncated to %d (asked %u)",
                 kMaxSubs, (unsigned)count);
    if (s_client && s_connected) subscribe_all();
}

/* ------------------------------------------------------------------ */
/* Client lifecycle                                                    */
/* ------------------------------------------------------------------ */

static void mqtt_event(void*, esp_event_base_t, int32_t event_id,
                       void* event_data) {
    auto* ev = (esp_mqtt_event_handle_t)event_data;
    switch ((esp_mqtt_event_id_t)event_id) {
        case MQTT_EVENT_CONNECTED:
            s_connected = true;
            ESP_LOGI(TAG, "connected to %s as %s", MQTT_BROKER_URI,
                     s_client_id);
            subscribe_all();
            break;
        case MQTT_EVENT_DISCONNECTED:
            s_connected = false;
            ESP_LOGW(TAG, "disconnected (auto-reconnect)");
            break;
        case MQTT_EVENT_DATA:
            /* States are small; a fragmented payload (> internal buffer)
             * is not a state — skip fragments honestly. */
            if (ev->current_data_offset != 0 ||
                ev->data_len != ev->total_data_len) {
                ESP_LOGW(TAG, "fragmented payload skipped (%d/%d)",
                         ev->data_len, ev->total_data_len);
                break;
            }
            handle_data(ev->topic, ev->topic_len, ev->data, ev->data_len);
            break;
        default:
            break;
    }
}

static bool mqtt_init(const char* client_id) {
    if (s_client) return true;
    if (client_id && *client_id)
        snprintf(s_client_id, sizeof(s_client_id), "%s", client_id);

    s_queue = xQueueCreate(kQueueDepth, sizeof(BusEvent));

    esp_mqtt_client_config_t cfg = {};
    /* [CXC-REPOINT] the broker is the ACTIVE tier's binding (per-device >
     * global > compiled floor), resolved once through bus_binding.  On a TRIAL
     * boot this is the trial broker — that is what makes it testable.
     * MQTT_BROKER_URI is now the rescue floor alone. */
    cfg.broker.address.uri = bus_binding_active_mqtt();
    cfg.credentials.client_id = s_client_id;
    cfg.session.keepalive = 30;
#ifdef QUALIA_DISPLAY
    cfg.buffer.size = 4096;   /* structured states (contact arrays) */
#else
    cfg.buffer.size = 1024;
#endif
    cfg.task.stack_size = 5120;

    s_client = esp_mqtt_client_init(&cfg);
    if (!s_client) {
        ESP_LOGE(TAG, "client init failed");
        return false;
    }
    esp_mqtt_client_register_event(s_client, MQTT_EVENT_ANY, mqtt_event,
                                   nullptr);
    esp_mqtt_client_start(s_client);
    ESP_LOGI(TAG, "starting: %s (client id %s)", bus_binding_active_mqtt(),
             s_client_id);
    return true;
}

static bool mqtt_connected(void) { return s_connected; }

static bool mqtt_poll(BusEvent* out) {
    if (!s_queue || !out) return false;
    bool got = xQueueReceive(s_queue, out, 0) == pdTRUE;
    if (got && uxQueueMessagesWaiting(s_queue) == 0) s_pending_since_us = 0;
    return got;
}

static size_t mqtt_pending(void) {
    return s_queue ? (size_t)uxQueueMessagesWaiting(s_queue) : 0;
}

static int64_t mqtt_pending_since_us(void) { return s_pending_since_us; }

static void mqtt_publish_state(const char* ns, const char* value) {
    if (!s_client || !s_connected || !ns || !value) return;
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"ns\":\"%s\",\"type\":\"state\",\"val\":\"%s\","
             "\"src\":\"%s\",\"scope\":\"global\",\"seq\":%lu}",
             ns, value, s_client_id, (unsigned long)++s_seq);
    char topic[128];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    esp_mqtt_client_publish(s_client, topic, payload, 0, 1, 0);
    ESP_LOGI(TAG, "PUBLISH %s = %s", ns, value);
}

static void mqtt_publish_number(const char* ns, double value) {
    if (!s_client || !s_connected || !ns) return;
    char payload[256];
    /* %.10g -> compact ("87", "87.3"); UNQUOTED val so the receiver parses
     * it as BusEvent::Kind::Number.
     *
     * ⚠ WHY .10 AND NOT THE DEFAULT.  Plain %g is SIX significant digits, so
     * an integer above 999999 was silently reformatted: uptime in seconds
     * became "1e+06" after 11.5 days, and a 32-bit word became "4.29497e+09".
     * That is a value the receiver parses cleanly into the WRONG number — the
     * worst shape of failure, because nothing anywhere reports an error.
     * Found while wiring `_device/<id>/heartbeat` (announce.cpp).
     *
     * .10 is the smallest width that makes every integer this fleet publishes
     * exact, and it does NOT make ordinary values ugly: %.10g of 0.1 is still
     * "0.1" and of 87.3 still "87.3".  It is still not 32 significant BITS,
     * which is why the 32-bit words in the announcement ride as hex strings —
     * see the ⚠ block in announce_pack.h. */
    snprintf(payload, sizeof(payload),
             "{\"ns\":\"%s\",\"type\":\"state\",\"val\":%.10g,"
             "\"src\":\"%s\",\"scope\":\"global\",\"seq\":%lu}",
             ns, value, s_client_id, (unsigned long)++s_seq);
    char topic[128];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    esp_mqtt_client_publish(s_client, topic, payload, 0, 1, 0);
}

/* ------------------------------------------------------------------ */
/* The seam ([SPEC:CXC-BOARD-HW] [CXC-BOARD-HW-UART-SLAVE], DP-CIO-6)  */
/* ------------------------------------------------------------------ */

/* A facade over the module state above — the MQTT client is a process
 * singleton (one esp-mqtt handle, one arrival queue), so the object holds
 * no state of its own.  Everything it forwards to is byte-for-byte the
 * code that was here before the seam existed. */
class MqttTransport final : public BusTransport {
public:
    const char* name() const override { return "mqtt"; }

    /* MQTT rides TCP over WiFi on every board in this tree: no IP, no bus.
     * This is the ONE transport for which wifi_init() is mandatory. */
    bool needs_network() const override { return true; }

    /* TRUE means the CLIENT came up, not that the broker answered:
     * esp-mqtt connects asynchronously and reconnects forever.  The
     * distinction transport_registry.py draws — "we don't know what you
     * asked for" (bug) vs "we know, and it isn't reachable right now"
     * (environment) — puts an unreachable broker firmly in the second
     * class, so this must not report failure for a down broker. */
    bool start(const char* client_id) override { return mqtt_init(client_id); }

    void stop() override {
        if (s_client) {
            esp_mqtt_client_stop(s_client);
            s_connected = false;
            ESP_LOGI(TAG, "stopped");
        }
    }

    bool connected() const override { return mqtt_connected(); }

    void subscribe(const char* ns) override { mqtt_add_subscription(ns); }

    void set_subscriptions(const char* const* ns, size_t count) override {
        mqtt_set_subscriptions(ns, count);
    }

    bool poll(BusEvent* out) override { return mqtt_poll(out); }
    size_t pending() const override { return mqtt_pending(); }
    int64_t pending_since_us() const override { return mqtt_pending_since_us(); }

    void publish_state(const char* ns, const char* value) override {
        mqtt_publish_state(ns, value);
    }
    void publish_number(const char* ns, double value) override {
        mqtt_publish_number(ns, value);
    }

    /* NOT SUPPORTED, AND IT SAYS SO ([SPEC:CXC-CONFIG-BUS]).
     *
     * This transport speaks JSON on a topic; it has no type field on the wire
     * and no way to carry opaque bytes without inventing an encoding (base64
     * in a JSON string) that NOTHING on the head side would produce or
     * consume.  Inventing it here would create a config path that exists in
     * the firmware and nowhere else — a declaration that looks obeyed and is
     * not, which is the whole defect class this seam was built against.
     *
     * So `config: bus` over MQTT is a KNOWN strategy whose backend cannot
     * carry it, and per transport_registry.py's second rule it degrades
     * LOUDLY: this returns false, config_bus announces "no config", and the
     * board says so on its glass.  It does NOT silently fall back to HTTP —
     * a device that quietly ignores its own configuration is rule 1's defect.
     * A WiFi board wanting config over the air uses `config: http`, which is
     * the default and unchanged. */
    bool publish_raw(const char* ns, BusMessageType type,
                     const uint8_t* value, size_t len) override {
        (void)value;
        static int64_t last_us = 0;
        int64_t now = esp_timer_get_time();
        if (!last_us || now - last_us > 10 * 1000000LL) {
            last_us = now;
            ESP_LOGE(TAG, "publish_raw(%s, type %s, %u bytes) REFUSED: the "
                     "MQTT transport carries JSON states, not typed opaque "
                     "frames.  Config over the BUS needs a framed substrate "
                     "(uart, can); on WiFi use `config: http`.", ns,
                     bus_message_type_name(type) ? bus_message_type_name(type)
                                                 : "?",
                     (unsigned)len);
        }
        return false;
    }

    size_t max_raw_value() const override { return 0; }

    /* MQTT is the one substrate here with a genuine SESSION, so it is the one
     * that can answer this honestly without inventing anything: the broker
     * either has us connected or it does not.
     *
     * Note it can disagree with `connected()` only in the STOPPED case, which
     * is the useful distinction — "nobody ever called start()" and "start()
     * ran and the broker is unreachable" produce the same `false` from
     * connected() and different values here. */
    uint8_t link_health() const override {
        if (!s_client) return kAnnLinkStopped;
        return s_connected ? kAnnLinkOk : kAnnLinkDown;
    }
};

BusTransport& bus_mqtt_transport() {
    static MqttTransport t;
    return t;
}
