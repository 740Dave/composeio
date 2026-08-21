/*
 * fingerprint_bringup_main.cpp — ON-CHIP bring-up of the R503 device half with
 * a MOCK sensor.  [SPEC:CXC-FPHAL] C71/C72/C73.  ESP32-S3, native USB-CDC.
 *
 * This is the ONLY Arduino/ESP file in the fingerprint stack; the HAL, the
 * mock, and the device logic are pure C++ and are the SAME translation units
 * proven green in env:native_test.  Here they run on silicon.
 *
 * TWO BOOT PATHS, selected at runtime by `transport` (bus_transport_select.{h,
 * cpp} — an NVS value, default "offline"), never a compile-time fork:
 *
 *   transport=offline (default) — SERIAL ONLY, no WiFi, no MQTT.  A SerialBus
 *   sink stands in for ComposeBus; setup() runs the same fixed five-step demo
 *   this file has always run, so a device that has never been repointed keeps
 *   behaving exactly as before.
 *
 *   transport=mqtt — WiFi + PubSubClient bring up the REAL external bus
 *   (composeio/global/<ns>, the same wire format transport.py's
 *   BusMessage.to_json()/from_json() define — see MqttFpBusSink below), the
 *   device announces itself with a live heartbeat, subscribes the inbound
 *   template_set push, and scripts one crew + one intruder match once a push
 *   has actually loaded — the SAME five-step story, driven by the live bus
 *   instead of a synchronous in-RAM call.
 *
 * The repoint: `bus_transport_set_selection()` (NVS, "takes effect next
 * boot") plus a one-line SERIAL COMMAND ("transport mqtt" / "transport
 * offline" + "restart") wired in loop() below — so a FUTURE transport change
 * needs no reflash. Today's build needed a reflash because the mqtt path
 * (WiFi/PubSubClient/JSON codec) did not exist in any prior build; the NVS/
 * serial mechanism is what makes every transport change AFTER this one free.
 *
 * The broker host is a CONFIG value (MQTT_BROKER/MQTT_PORT build flags,
 * defaulted below, overridable via PLATFORMIO_BUILD_FLAGS or wifi_config.h) —
 * DP-CIO-6: transport, and where it connects, are never hardcoded into the
 * semantic model. Compiled ONLY in env:fingerprint_s3 (guarded by
 * -DFINGERPRINT_BRINGUP), so it never collides with main.cpp / controller_main.cpp.
 */
#ifdef FINGERPRINT_BRINGUP

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>

#include "fingerprint_mock.h"
#include "fingerprint_device.h"
#include "bus_transport_select.h"
#include "real_r503_hal.h"   /* the REAL R503 backend — ADDITIVE, opt-in */

/* --- Configuration ------------------------------------------------------
 * Placeholders by default; the dispatcher injects real credentials at flash
 * time via PLATFORMIO_BUILD_FLAGS or a gitignored wifi_config.h (mirrors
 * main.cpp's convention exactly — one credential file, every env that needs
 * WiFi includes it the same way). Never hardcode credentials here. */
#if defined(__has_include)
#  if __has_include("wifi_config.h")
#    include "wifi_config.h"
#  endif
#endif
#ifndef WIFI_SSID
#define WIFI_SSID "YOUR_WIFI_SSID"
#endif
#ifndef WIFI_PASS
#define WIFI_PASS "YOUR_WIFI_PASSWORD"
#endif
/* CONFIG, not a hardcoded connection — a build-flag default naming the
 * CURRENTLY-VERIFIED live broker (confirmed live 2026-08-18: a real
 * publish/subscribe round trip on mqtt-broker.local:1883; see
 * services/fingerprint_storage/config.py DEFAULT_BROKER_URL, the same
 * default every other Python-side bus client resolves to). Override with
 * PLATFORMIO_BUILD_FLAGS='-DMQTT_BROKER=\"host\"' when the Skipper cutover
 * lands — no code change. */
#ifndef MQTT_BROKER
#define MQTT_BROKER "mqtt-broker.local"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef CIO_TOPIC_PREFIX
#define CIO_TOPIC_PREFIX "composeio"
#endif
/* CONFIG, not a hardcoded connection — the dedicated fingerprint template
 * store (services/fingerprint_template_store/, GET /api/v1/templates/<id>,
 * NEVER bus_storage's :8420 — a SEPARATE service on a SEPARATE port per
 * [DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]). Mirrors
 * python/composeio/resolve.py's COMPOSEIO_TEMPLATE_STORE_URL default
 * exactly: LIVE at node.example.local:8423 (verified 2026-08-19). Override with
 * PLATFORMIO_BUILD_FLAGS='-DTEMPLATE_STORE_URL=\"http://host:port\"' — no
 * code change. */
#ifndef TEMPLATE_STORE_URL
#define TEMPLATE_STORE_URL "http://node.example.local:8423"
#endif

static const char* kDeviceId = "dev/s3-fp1";

/* A bus sink that prints every publish to the serial console (offline path,
 * and mirrored alongside every online publish too — see MqttFpBusSink). */
class SerialBus final : public FingerprintBusSink {
public:
    void publish_event(const std::string& ns, const std::string& event_type,
                       const FpData& data, const std::string& source) override {
        Serial.printf("[BUS] %-24s ns=%s {", event_type.c_str(), ns.c_str());
        bool first = true;
        for (const auto& kv : data) {
            Serial.printf("%s%s=", first ? "" : ", ", kv.first.c_str());
            if (kv.second.kind == FpValue::K::Str)
                Serial.printf("\"%s\"", kv.second.s.c_str());
            else
                Serial.printf("%g", kv.second.n);
            first = false;
        }
        Serial.printf("} src=%s\n", source.c_str());
    }
};

/* --- the ONLINE bus sink: publish_event -> a real composeio/global/<ns> MQTT
 * publish, wire-identical to transport.py's BusMessage.to_json() ({ns, type,
 * src, ts, scope, seq, evt, data}) so the Python-side fingerprint_storage
 * service (an ordinary ComposeIOBus client) decodes it with no special case
 * for this device. Also echoes to Serial (same line shape as SerialBus) so
 * on-console bring-up stays legible with the live path too. */
class MqttFpBusSink final : public FingerprintBusSink {
public:
    explicit MqttFpBusSink(PubSubClient& mqtt) : mqtt_(mqtt) {}

    void publish_event(const std::string& ns, const std::string& event_type,
                       const FpData& data, const std::string& source) override {
        Serial.printf("[BUS->MQTT] %-24s ns=%s {", event_type.c_str(), ns.c_str());
        bool first = true;
        for (const auto& kv : data) {
            Serial.printf("%s%s=", first ? "" : ", ", kv.first.c_str());
            if (kv.second.kind == FpValue::K::Str)
                Serial.printf("\"%s\"", kv.second.s.c_str());
            else
                Serial.printf("%g", kv.second.n);
            first = false;
        }
        Serial.printf("} src=%s\n", source.c_str());

        DynamicJsonDocument doc(2048);
        doc["ns"] = ns;
        doc["type"] = "event";
        doc["src"] = source;
        doc["ts"] = millis() / 1000.0;
        doc["scope"] = "global";
        doc["seq"] = ++seq_;
        doc["evt"] = event_type;
        JsonObject d = doc.createNestedObject("data");
        for (const auto& kv : data) {
            if (kv.second.kind == FpValue::K::Str) d[kv.first] = kv.second.s;
            else d[kv.first] = kv.second.n;
        }

        std::string topic = std::string(CIO_TOPIC_PREFIX) + "/global/" + ns;
        char payload[2048];
        size_t n = serializeJson(doc, payload, sizeof(payload));
        bool ok = mqtt_.publish(topic.c_str(), reinterpret_cast<const uint8_t*>(payload), n, false);
        Serial.printf("[MQTT PUB] %s (%u bytes) -> %s\n", topic.c_str(), (unsigned)n,
                      ok ? "ok" : "FAILED (buffer/connection?)");
    }

private:
    PubSubClient& mqtt_;
    uint32_t seq_ = 0;
};

/* --- the REAL template-reference resolver: an ESP32 HTTPClient GET against
 * the dedicated fingerprint template store, matching TemplateResolver's
 * seam exactly ([CXC-FPBUS-REQ-LOAD-FAILURE] via the SAME handle_load_
 * failure() path a HAL write failure drives). Only reachable on the ONLINE
 * (transport=mqtt) path — the offline demo pushes raw bytes directly and
 * never needs this. Core Arduino-ESP32 HTTPClient + the existing WiFiClient;
 * no lib_dep addition required. */
/* Bounded raw-byte read off an open HTTPClient stream — never
 * HTTPClient::getString()/Arduino String for a biometric template: a real
 * R503 template is 1536 bytes of binary with embedded nulls, and String's
 * embedded-null handling via c_str() is version-dependent and not worth
 * trusting on biometric data. Reads exactly `want` bytes (or as many as
 * arrive before `deadline_ms` elapses with zero progress), appending into
 * `body`. Returns false (stalled) only on a genuine no-progress timeout —
 * a slow-but-arriving stream keeps resetting its own deadline. */
static bool http_read_bytes_bounded(WiFiClient* stream, std::string& body, size_t want,
                                    uint32_t timeout_ms) {
    uint32_t deadline = millis() + timeout_ms;
    uint8_t buf[256];
    while (want > 0) {
        size_t chunk = want < sizeof(buf) ? want : sizeof(buf);
        int n = stream->readBytes(reinterpret_cast<char*>(buf), chunk);
        if (n > 0) {
            body.append(reinterpret_cast<char*>(buf), static_cast<size_t>(n));
            want -= static_cast<size_t>(n);
            deadline = millis() + timeout_ms;   /* progress resets the guard */
        } else if (millis() > deadline) {
            return false;   /* stalled: no bytes for a full timeout window */
        }
    }
    return true;
}

static bool http_resolve_template(const std::string& ref, TemplateBytes& out,
                                  FingerprintHALFailure& err) {
    static const std::string kRefPrefix = "fingerprint://";
    const std::string opaque_id = ref.substr(kRefPrefix.size());
    const std::string url = std::string(TEMPLATE_STORE_URL) + "/api/v1/templates/" + opaque_id;
    const uint32_t kReadTimeoutMs = 10000;

    HTTPClient http;
    if (!http.begin(url.c_str())) {
        err.axis = FailureAxis::TRANSPORT;
        err.sensor_code = std::nullopt;
        err.detail = "template fetch: could not begin HTTP request to " + url;
        return false;
    }
    int code = http.GET();
    if (code != HTTP_CODE_OK) {
        err.axis = FailureAxis::TRANSPORT;
        err.sensor_code = std::nullopt;
        err.detail = "template fetch: GET " + url + " -> HTTP " + std::to_string(code);
        http.end();
        return false;
    }

    /* RAW BYTES by content-length — the binary-safe path. getSize() is the
     * Content-Length header; -1 means unknown/chunked. */
    int content_len = http.getSize();
    WiFiClient* stream = http.getStreamPtr();
    if (stream == nullptr) {
        err.axis = FailureAxis::TRANSPORT;
        err.sensor_code = std::nullopt;
        err.detail = "template fetch: no stream from " + url;
        http.end();
        return false;
    }

    std::string body;
    if (content_len >= 0) {
        body.reserve(static_cast<size_t>(content_len));
        bool ok = http_read_bytes_bounded(stream, body, static_cast<size_t>(content_len),
                                          kReadTimeoutMs);
        if (!ok || body.size() != static_cast<size_t>(content_len)) {
            err.axis = FailureAxis::TRANSPORT;
            err.sensor_code = std::nullopt;
            err.detail = "template fetch: short read from " + url + " — got " +
                        std::to_string(body.size()) + " of " + std::to_string(content_len);
            http.end();
            return false;
        }
    } else {
        /* No content-length (unknown/chunked): read until the stream
         * closes, one bounded chunk at a time, or bail on a stall. */
        uint32_t deadline = millis() + kReadTimeoutMs;
        uint8_t buf[256];
        while (http.connected() || stream->available() > 0) {
            int avail = stream->available();
            if (avail <= 0) {
                if (millis() > deadline) {
                    err.axis = FailureAxis::TRANSPORT;
                    err.sensor_code = std::nullopt;
                    err.detail = "template fetch: timeout (no content-length) from " + url +
                                " — short read: got " + std::to_string(body.size()) +
                                " of unknown";
                    http.end();
                    return false;
                }
                continue;
            }
            size_t chunk = static_cast<size_t>(avail) < sizeof(buf) ? static_cast<size_t>(avail)
                                                                    : sizeof(buf);
            int n = stream->readBytes(reinterpret_cast<char*>(buf), chunk);
            if (n > 0) {
                body.append(reinterpret_cast<char*>(buf), static_cast<size_t>(n));
                deadline = millis() + kReadTimeoutMs;
            }
        }
    }
    http.end();

    if (body.empty()) {
        err.axis = FailureAxis::TRANSPORT;
        err.sensor_code = std::nullopt;
        err.detail = "template fetch: empty body from " + url;
        return false;
    }
    out = std::move(body);
    return true;
}

static MockFingerprintHAL g_hal;
static DevicePersistence g_nvs;
static SerialBus g_serial_bus;
static WiFiClient g_wifi_client;
static PubSubClient g_mqtt(g_wifi_client);
static MqttFpBusSink g_mqtt_bus(g_mqtt);
static FingerprintDevice* g_dev = nullptr;
static bool g_online = false;

/* Online-path state: the push arrives asynchronously off the bus, so the
 * scripted-match sequence is driven by a small timer instead of running
 * inline right after a synchronous demo_push() the way the offline path does. */
static bool g_push_loaded = false;
static uint32_t g_push_loaded_at_ms = 0;
static bool g_crew_scripted = false;
static bool g_intruder_scripted = false;

static Slot crew_slot() {
    for (const auto& kv : g_dev->bridge()) if (kv.second.role == "crew") return kv.first;
    return -1;
}

/* --- inbound: decode a template_set push off the wire (mirrors
 * fingerprint_inbound_push_topic()'s contract) and hand it to the SAME
 * receive_push() the offline path calls. --------------------------------- */
static void onMqttMessage(char* topic, uint8_t* payload, unsigned int length) {
    Serial.printf("[MQTT RX] %s (%u bytes)\n", topic, length);
    DynamicJsonDocument doc(16384);
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("[push] JSON parse error on %s: %s\n", topic, err.c_str());
        return;
    }
    const char* evt = doc["evt"] | "";
    if (strcmp(evt, "template_set") != 0) {
        Serial.printf("[push] ignoring evt=\"%s\" on %s (not template_set)\n", evt, topic);
        return;
    }
    JsonObject data = doc["data"].as<JsonObject>();

    TemplatePush push;
    push.template_set_uuid = data["template_set_uuid"] | "";
    JsonObject rct = data["role_capability_table"].as<JsonObject>();
    for (JsonPair kv : rct) {
        std::vector<std::string> caps;
        for (JsonVariant v : kv.value().as<JsonArray>()) caps.push_back(v.as<std::string>());
        push.role_capability_table[kv.key().c_str()] = caps;
    }
    for (JsonObject e : data["entries"].as<JsonArray>()) {
        PushEntry pe;
        pe.person = e["person"].as<std::string>();
        pe.ordinal = e["ordinal"].as<int>();
        pe.character = e["character"].as<std::string>();
        pe.rbac = e["rbac"].as<std::string>();
        pe.tmpl = e["template"].as<std::string>();
        push.entries.push_back(pe);
    }
    Serial.printf("[push] decoded uuid=%s entries=%u\n", push.template_set_uuid.c_str(),
                  (unsigned)push.entries.size());

    std::string status = g_dev->receive_push(push);
    Serial.printf("[push] receive_push -> \"%s\" (occupancy now %u slots)\n",
                  status.c_str(), (unsigned)g_hal.occupancy_count());
    if (status == "loaded" && !g_push_loaded) {
        g_push_loaded = true;
        g_push_loaded_at_ms = millis();
        Serial.println("[demo] push loaded — scripting a crew match in ~2s, "
                       "an intruder match ~3s after that");
    }
}

/* --- ONLINE setup: WiFi + MQTT, blocking with a bounded retry, exactly the
 * connectivity shape main.cpp already uses on the C3/other S3 envs. --------- */
static bool connectWiFiBlocking() {
    Serial.printf("[wifi] connecting to \"%s\"...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[wifi] FAILED to connect within 15s");
        return false;
    }
    Serial.printf("[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

static bool connectMqttBlocking() {
    g_mqtt.setBufferSize(16384);  /* a template-set push runs a few KB */
    g_mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    g_mqtt.setCallback(onMqttMessage);

    char clientId[32];
    uint64_t mac = ESP.getEfuseMac();
    snprintf(clientId, sizeof(clientId), "cio-fp-s3-%04x", (unsigned)(mac & 0xFFFF));

    Serial.printf("[mqtt] connecting to %s:%d as %s...\n", MQTT_BROKER, MQTT_PORT, clientId);
    uint32_t t0 = millis();
    bool ok = false;
    while (millis() - t0 < 15000) {
        if (g_mqtt.connect(clientId)) { ok = true; break; }
        Serial.printf("[mqtt] connect failed (rc=%d), retrying...\n", g_mqtt.state());
        delay(1000);
    }
    if (!ok) {
        Serial.println("[mqtt] FAILED to connect within 15s");
        return false;
    }
    Serial.println("[mqtt] connected");

    std::string push_topic = std::string(CIO_TOPIC_PREFIX) + "/global/" +
                             fingerprint_inbound_push_topic(kDeviceId);
    g_mqtt.subscribe(push_topic.c_str(), 1);
    Serial.printf("[mqtt] subscribed: %s\n", push_topic.c_str());
    return true;
}

/* =========================================================================
 * THE REAL R503 — opt-in, additive, and NOT the boot path.
 * =========================================================================
 * ⛔ NOTHING BELOW RUNS UNLESS AN `r503 ...` COMMAND IS TYPED.  The mock HAL
 * (g_hal) remains the device's HAL and the boot sequence, the transport
 * selection, the push path and the `fingerprint://` fetch are untouched — a
 * board with no sensor attached boots and joins the bus exactly as before.
 * The UART is not even opened until the first r503 command.
 *
 * WIRING (bench-observed this session, NOT a ratified pin decision):
 *   ESP32-S3 GPIO4 (TX) -> R503 RXD (white)
 *   ESP32-S3 GPIO5 (RX) <- R503 TXD (green)
 *   57600 8N1
 * ⛔ R503 3.3VT (WAKEUP supply) MUST tie to 3V3, never 5 V — WAKEUP is a CMOS
 * output that TRACKS 3.3VT, so a 5 V 3.3VT drives ~5 V into a GPIO
 * ([CXC-R503-WAKEUP-TRACKS-3V3T], [DEC-R503-AND-FOUR-TM1637-ON-ONE-ESP32-S3]).
 *
 * ⛔ MACHINE-GREEN != GLASS-GREEN.  `pio run` succeeding and 218 host checks
 * passing prove the FRAMING is right.  Only `r503 synctest` on the physical
 * part can answer whether this module's firmware honours template sync.  */
static ArduinoR503Port g_r503_port;
static RealR503HAL     g_r503(&g_r503_port);
static bool            g_r503_open = false;

static void r503_print_failure(const char* what, const FingerprintHALFailure& e) {
    Serial.printf("[r503] %s FAILED — axis=%s", what, to_string(e.axis));
    if (e.sensor_code.has_value())
        Serial.printf(" sensor_code=0x%02X", e.sensor_code.value());
    Serial.printf(" : %s\n", e.detail.c_str());
}

/* Lazily open the UART and handshake.  Returns false LOUDLY. */
static bool r503_ensure_open() {
    if (g_r503_open && g_r503.ready()) return true;
    if (!g_r503_open) {
        Serial.printf("[r503] opening UART%d tx=GPIO%d rx=GPIO%d @%d 8N1\n",
                      R503_UART_NUM, R503_TX_PIN, R503_RX_PIN, R503_BAUD);
        g_r503_port.begin();
        g_r503_open = true;
    }
    FingerprintHALFailure err;
    if (!g_r503.begin(err)) {
        r503_print_failure("begin", err);
        Serial.println("[r503] check: TX/RX not swapped, common ground, 3.3VT->3V3, "
                       "VCC 3.3V.  A UART link that never answers is a WIRING "
                       "question before it is a firmware question.");
        return false;
    }
    return true;
}

static void r503_print_diag(const R503LinkDiag& d) {
    Serial.printf("[r503] link: %u packets ok, %u stray bytes, %u bad checksum, "
                  "%u bad length, %u timeouts, %u rx / %u tx bytes\n",
                  d.packets_ok, d.stray_bytes, d.bad_checksum, d.bad_length,
                  d.timeouts, d.bytes_rx, d.bytes_tx);
}

static void r503_cmd_info() {
    if (!r503_ensure_open()) return;
    const r503::SysPara& sp = g_r503.sys_para();
    Serial.println("[r503] --- ReadSysPara (0x0F) ---");
    Serial.printf("  status register    : 0x%04X\n", sp.status_register);
    Serial.printf("  system identifier  : 0x%04X (fixed 0x0000)\n", sp.system_identifier);
    Serial.printf("  finger library size: %u\n", sp.finger_library_size);
    Serial.printf("  security level     : %u\n", sp.security_level);
    Serial.printf("  device address     : 0x%08lX\n", (unsigned long)sp.device_address);
    Serial.printf("  packet size code   : %u -> %u bytes%s\n", sp.packet_size_code,
                  (unsigned)sp.packet_content_len(),
                  sp.packet_size_code_known() ? "" : "  (UNKNOWN CODE — fell back to 128)");
    Serial.printf("  baud N             : %u -> %u bps\n", sp.baud_n, sp.baud_n * 9600u);
    if (g_r503.prod_info_valid()) {
        const r503::ProdInfo& pi = g_r503.prod_info();
        Serial.println("[r503] --- ReadProdInfo (0x3C) ---");
        Serial.printf("  module / batch / sn: %s / %s / %s\n", pi.module_model.c_str(),
                      pi.batch_number.c_str(), pi.serial_number.c_str());
        Serial.printf("  hw version         : %u.%u\n", pi.hw_major, pi.hw_minor);
        Serial.printf("  sensor             : %s %ux%u\n", pi.sensor_model.c_str(),
                      pi.sensor_width, pi.sensor_height);
        Serial.printf("  PARAM_TMPL_SIZE    : %u bytes\n", pi.template_size);
        Serial.printf("  PARAM_TMPL_TOTAL   : %u templates\n", pi.template_total);
    } else {
        Serial.println("[r503] ReadProdInfo (0x3C) NOT answered by this part — the "
                       "template size will be MEASURED by the first UpChar, never "
                       "assumed.");
    }
    Capabilities c = g_r503.capabilities();
    Serial.printf("[r503] capabilities(): slot_count=%d template_size_bytes=%d "
                  "(source: %s) captures_per_template=%d readback=%s\n",
                  c.slot_count, c.template_size_bytes, g_r503.template_size_source(),
                  c.captures_per_template, c.supports_readback ? "claimed" : "no");
    std::set<Slot> occ = g_r503.read_occupancy();
    Serial.printf("[r503] occupancy: %u slot(s) in use\n", (unsigned)occ.size());
    std::optional<RosterStamp> st = g_r503.read_roster_stamp();
    Serial.printf("[r503] roster stamp (module notepad): %s\n",
                  st.has_value() ? st.value().c_str() : "(none)");
    r503_print_diag(g_r503.diag());
}

/* ⭐ THE DISCRIMINATOR (added after the 2026-08-20 bench run).
 * LoadChar a STORED template into CharBuffer1, then UpChar it out — with NO
 * DownChar anywhere in the path.  The bench saw DownChar ACK 0x00 and then
 * UpChar go silent; that signature has two causes needing opposite
 * responses, and this separates them:
 *   - template comes out  -> UpChar is FINE; our DownChar data phase wedged
 *                            the module.  Our bug, and fixable.
 *   - also silent         -> real evidence against UpChar on this firmware,
 *                            and the push-templates architecture needs a
 *                            rethink.
 * The bench handed us the fixture free: this module already holds a real
 * leftover template in slot 1. */
static void r503_cmd_extract(int slot) {
    if (!r503_ensure_open()) return;
    Serial.println("\n============================================================");
    Serial.println("  R503 EXTRACT — UpChar (0x08) with NO DownChar involved");
    Serial.println("  THE DISCRIMINATOR for the 2026-08-20 UpChar silence.");
    Serial.println("============================================================");
    g_r503.diag().reset();
    R503ExtractResult r = g_r503.run_extract(slot);

    Serial.printf("  slot                  : %d\n", (int)r.slot);
    Serial.printf("  LoadChar cmd bytes    : %s\n",
                  r503::hex(r.loadchar_cmd_bytes).c_str());
    Serial.printf("  LoadChar ack          : %s (0x%02X)\n",
                  r.load_acked ? "OK" : "REFUSED/absent", r.load_conf);
    Serial.printf("  UpChar   cmd bytes    : %s\n",
                  r503::hex(r.upchar_cmd_bytes).c_str());
    Serial.printf("  UpChar   ack          : %s (0x%02X)\n",
                  r.up_acked ? "OK" : "REFUSED/absent", r.up_conf);
    Serial.printf("  received              : %u bytes in %u packets (stream %s)\n",
                  (unsigned)r.bytes, (unsigned)r.packets,
                  r.stream_complete ? "terminated on PID 0x08" : "NOT TERMINATED");
    if (r.expected_bytes)
        Serial.printf("  expected              : %u bytes (announced template size)\n",
                      (unsigned)r.expected_bytes);
    Serial.printf("  head=[%s]  tail=[%s]\n", r503::hex(r.head).c_str(),
                  r503::hex(r.tail).c_str());
    if (!r.unsolicited.empty())
        Serial.printf("  volunteered bytes     : %s\n",
                      r503::hex(r.unsolicited, 24).c_str());
    r503_print_diag(r.diag);

    if (r.pass()) {
        Serial.println("\n  RESULT: PASS — UpChar WORKS on this firmware.");
        Serial.println("  THEREFORE: the synctest failure is NOT 'template download "
                       "is unsupported'.  It is isolated to the DownChar DATA "
                       "PHASE — our framing, our pacing, or the A5 buffer split.");
        Serial.println("  NEXT: 'r503 synctest' (verbose trace), then "
                       "'r503 synctest end' to sweep the A2 end-packet framing.");
    } else {
        Serial.printf("\n  RESULT: FAIL — %s\n", r.failure.c_str());
        if (!r.load_acked)
            Serial.println("  NOTE: LoadChar itself did not succeed, so this run says "
                           "NOTHING about UpChar.  Check the slot holds a template "
                           "('r503 info' reports occupancy).");
        else
            Serial.println("  ⛔ THIS IS THE ARCHITECTURE-RELEVANT READING: UpChar "
                           "failed with no DownChar anywhere near it.  If it went "
                           "SILENT (0xFF above, not a code), the module may still be "
                           "wedged from an earlier run — 'r503 unwedge' then retry "
                           "before concluding anything.");
    }
    Serial.println("============================================================\n");
}

static void r503_cmd_unwedge() {
    if (!r503_ensure_open()) return;
    r503::Bytes vol;
    const bool ok = g_r503.unwedge(vol);
    Serial.printf("[r503] unwedge: sent a bare empty PID 0x08 end packet; module "
                  "%s\n", ok ? "ANSWERS again (it WAS stuck mid-receive — that is "
                               "a diagnosis: our data phase never terminated for it)"
                             : "still does NOT answer (stuck for some other reason, "
                               "or the link is down)");
    if (!vol.empty())
        Serial.printf("[r503] it volunteered: %s\n", r503::hex(vol, 24).c_str());
}

/* ⭐ THE DE-RISK COMMAND.  Answers, without a finger: does THIS part's
 * firmware actually honour DownChar (0x09) upload and UpChar (0x08) download?
 * Non-destructive: char buffers only, no Store, no flash write. */
static void r503_cmd_synctest(const R503SyncTestOptions& opt, bool verbose) {
    const uint8_t buffer_id = opt.buffer_id;
    (void)buffer_id;
    if (!r503_ensure_open()) return;
    Serial.println("\n============================================================");
    Serial.println("  R503 TEMPLATE-SYNC DE-RISK  (DownChar 0x09 / UpChar 0x08)");
    Serial.println("  NON-DESTRUCTIVE: char buffer only, no Store, no flash.");
    Serial.println("============================================================");
    g_r503.diag().reset();
    R503SyncTestResult r = g_r503.run_sync_test(opt);

    Serial.printf("  end-packet framing    : %s%s\n", r503::to_string(r.style),
                  r.style == r503::EndPacketStyle::LAST_DATA_IS_END
                      ? "  (the manual's own framing)"
                      : "  (A2 sweep: payload all 0x02 + a separate EMPTY 0x08)");
    Serial.printf("  inter-packet delay    : %u ms%s\n",
                  (unsigned)r.inter_packet_delay_ms,
                  r.inter_packet_delay_ms ? "" : "  (back to back)");
    Serial.printf("  packet content length : %u bytes  (from ReadSysPara, "
                  "NOT assumed)\n", (unsigned)r.packet_content_len);
    Serial.printf("  template size         : %u bytes  (source: %s)\n",
                  (unsigned)r.template_size, r.size_source);
    Serial.printf("  DownChar ack          : %s (0x%02X)\n",
                  r.downchar_acked ? "OK" : "REFUSED/absent", r.down_conf);
    Serial.printf("  data packets sent     : %u\n", (unsigned)r.data_packets_sent);
    Serial.printf("  UpChar ack            : %s (0x%02X)\n",
                  r.upchar_acked ? "OK" : "REFUSED/absent", r.up_conf);
    Serial.printf("  data packets received : %u  (stream %s)\n",
                  (unsigned)r.data_packets_recv,
                  r.stream_complete ? "terminated on PID 0x08" : "NEVER TERMINATED");
    Serial.printf("  sent %u bytes  head=[%s] tail=[%s]\n", (unsigned)r.sent_len,
                  r503::hex(r.sent_head).c_str(), r503::hex(r.sent_tail).c_str());
    Serial.printf("  recv %u bytes  head=[%s] tail=[%s]\n", (unsigned)r.recv_len,
                  r503::hex(r.recv_head).c_str(), r503::hex(r.recv_tail).c_str());
    Serial.printf("  DownChar cmd bytes    : %s\n",
                  r503::hex(r.downchar_cmd_bytes).c_str());
    Serial.printf("  UpChar   cmd bytes    : %s\n",
                  r503::hex(r.upchar_cmd_bytes).c_str());
    Serial.printf("  sample data packet #0 : %s\n",
                  r503::hex(r.sample_data_packet).c_str());

    /* --- THE PER-PACKET TRACE, so the divergence is VISIBLE ------------- */
    if (verbose && !r.trace.empty()) {
        Serial.println("  --- data-phase trace (idx  PID  len   content  head        "
                       "bytes-back) ---");
        for (size_t i = 0; i < r.trace.size(); i++) {
            const R503DataPacketTrace& t = r.trace[i];
            Serial.printf("   %3u  0x%02X  0x%04X  %3u B   %-11s %s\n",
                          (unsigned)t.index, t.pid, t.length_field,
                          (unsigned)t.content_bytes,
                          r503::hex(t.head).c_str(),
                          t.bytes_back ? "<-- MODULE SPOKE MID-STREAM" : "");
        }
        Serial.println("  (a healthy module says NOTHING during the data phase; any "
                       "'MODULE SPOKE' row is where it started complaining)");
    }

    /* --- what the module volunteered, PRESERVED not flushed ------------- */
    if (!r.post_data_bytes.empty()) {
        Serial.printf("  post-data bytes       : %s\n",
                      r503::hex(r.post_data_bytes, 24).c_str());
        if (r.post_data_ack_parsed)
            Serial.printf("  post-data ACK decoded : confirmation 0x%02X%s\n",
                          r.post_data_conf,
                          r.post_data_conf == 0x01
                              ? "  <-- 0x01 'error when receiving package': the "
                                "module REJECTED our data phase"
                              : "");
    } else {
        Serial.println("  post-data bytes       : (none — the module said nothing "
                       "after the data phase)");
    }
    if (r.retried_with_empty_end)
        Serial.printf("  empty-end retry       : %s\n",
                      r.rescued_by_empty_end
                          ? "RESCUED the transfer — DIAGNOSIS: our data phase was "
                            "not TERMINATING for this module"
                          : "did not help");
    Serial.printf("  module alive after    : %s\n",
                  r.alive_after ? "YES" : "NO — it is wedged; run 'r503 unwedge' "
                                          "before trusting the next command");
    r503_print_diag(r.diag);

    if (r.pass()) {
        Serial.println("\n  RESULT: PASS — this module round-tripped a known byte "
                       "pattern through DownChar and back out through UpChar, "
                       "BYTE-EXACT.");
        Serial.println("  WHAT THAT DOES NOT PROVE: that those bytes are a valid "
                       "TEMPLATE, or that a template moved this way will MATCH a "
                       "finger.  Prove that with: r503 enroll -> r503 match.");
    } else {
        Serial.printf("\n  RESULT: FAIL — %s\n", r.failure.c_str());
        if (!r.byte_exact && r.recv_len == r.sent_len && r.sent_len)
            Serial.printf("  first differing byte at index %u\n",
                          (unsigned)r.first_diff_index);
        /* ⛔ SILENCE AND A REFUSAL MEAN DIFFERENT THINGS.  Say which one
         * happened, because only one of them is evidence about UpChar. */
        if (r.downchar_acked && !r.upchar_acked && r.up_conf == 0xFF) {
            Serial.println("  ⛔ READ THIS CAREFULLY: UpChar returned SILENCE, not a "
                           "refusal code.  A refusal (0x0D/0xFC) would mean 'UpChar "
                           "exists and declined'.  SILENCE means the command was "
                           "never parsed AS a command — most likely the module was "
                           "still in its DownChar receive phase and ate those 13 "
                           "bytes as data.  That is OUR data phase failing to "
                           "terminate, NOT proof that download is unsupported.");
            Serial.println("  NEXT, in this order:");
            Serial.println("    1. r503 unwedge         (free the module)");
            Serial.println("    2. r503 extract 1       (UpChar with NO DownChar — "
                           "the discriminator)");
            Serial.println("    3. r503 synctest end    (sweep the A2 end-packet "
                           "framing)");
            Serial.println("    4. r503 synctest delay 5  (sweep inter-packet pacing)");
        }
        Serial.println("  ARCHITECTURAL CONSEQUENCE: if template UPLOAD/DOWNLOAD is "
                       "genuinely unsupported by this firmware, the "
                       "enroll-once-push-everywhere design needs a different "
                       "mechanism (enroll on every device, or a different part).  "
                       "⛔ Do NOT conclude that from a SILENT UpChar until "
                       "'r503 extract' has been run.");
    }
    Serial.println("============================================================\n");
}

/* Full FLASH round trip — EXPLICITLY OPTED INTO, because it WRITES A SLOT.
 * `r503 synctest store <slot>` only. */
static void r503_cmd_synctest_store(int slot) {
    if (!r503_ensure_open()) return;
    const int n = g_r503.capabilities().template_size_bytes;
    if (n <= 0) {
        Serial.println("[r503] template size unknown — run 'r503 synctest' first so "
                       "it can be measured.");
        return;
    }
    Serial.printf("[r503] DESTRUCTIVE: writing slot %d in FLASH.\n", slot);
    TemplateBytes ramp;
    ramp.reserve(n);
    for (int i = 0; i < n; i++) ramp.push_back((char)((i * 7 + 3) & 0xFF));

    FingerprintHALFailure err;
    Slot got = -1;
    if (!g_r503.store_template(ramp, got, err)) { r503_print_failure("store_template", err); return; }
    Serial.printf("[r503] store_template -> slot %d\n", got);
    TemplateBytes back;
    if (!g_r503.read_template(got, back, err)) { r503_print_failure("read_template", err); return; }
    const bool same = (back == ramp);
    Serial.printf("[r503] FLASH round trip: %u bytes back, byte-exact: %s\n",
                  (unsigned)back.size(), same ? "YES — PASS" : "NO — FAIL");
    (void)slot;
}

static void r503_cmd_glow(const std::string& what) {
    if (!r503_ensure_open()) return;
    FeedbackState s = FeedbackState::READY;
    if      (what == "idle")    s = FeedbackState::IDLE;
    else if (what == "wait")    s = FeedbackState::AWAITING_FINGER;
    else if (what == "busy")    s = FeedbackState::BUSY;
    else if (what == "fault")   s = FeedbackState::FAULT;
    else if (what == "accept")  s = FeedbackState::ACCEPT;
    else if (what == "reject")  s = FeedbackState::REJECT_KNOWN;
    else if (what == "off")     s = FeedbackState::IDLE;
    FingerprintHALFailure err;
    if (!g_r503.command_feedback(s, FpProgress{}, err)) {
        r503_print_failure("command_feedback", err);
        return;
    }
    Serial.printf("[r503] Aura ring -> %s.  ⛔ NEEDS EYES: the ring's COLOUR cannot "
                  "be self-verified — an AuraLedConfig ack proves the command was "
                  "accepted, never that the LED lit, or in which colour.\n",
                  what.c_str());
}

static void r503_cmd_enroll(int explicit_slot) {
    if (!r503_ensure_open()) return;
    const int required = g_r503.capabilities().captures_per_template;
    Serial.printf("[r503] enrollment: %d captures required (RegModel combines "
                  "CharBuffer1 + CharBuffer2).\n", required);

    FingerprintHALFailure err;
    for (int cap = 1; cap <= required; cap++) {
        (void)g_r503.command_feedback(FeedbackState::AWAITING_FINGER, FpProgress{}, err);
        Serial.printf("[r503] place finger (capture %d of %d)...\n", cap, required);
        const uint32_t t0 = millis();
        bool got = false;
        while (millis() - t0 < 20000) {
            if (g_r503.enroll_capture(cap, err)) { got = true; break; }
            if (err.axis != FailureAxis::SENSOR_REFUSAL) { r503_print_failure("capture", err); return; }
            delay(120);
        }
        if (!got) {
            Serial.println("[r503] capture TIMED OUT after 20s.");
            (void)g_r503.command_feedback(FeedbackState::FAULT, FpProgress{}, err);
            return;
        }
        (void)g_r503.command_feedback(FeedbackState::CAPTURE_ACCEPTED, FpProgress{}, err);
        Serial.printf("[r503] capture %d accepted.\n", cap);
        if (cap < required) {
            Serial.println("[r503] lift finger...");
            const uint32_t t1 = millis();
            while (g_r503.finger_present() && millis() - t1 < 10000) delay(100);
            delay(400);
        }
    }
    if (!g_r503.enroll_combine(err)) {
        r503_print_failure("RegModel", err);
        Serial.println("[r503] (0x0A means the two captures were not the same finger.)");
        (void)g_r503.command_feedback(FeedbackState::FAULT, FpProgress{}, err);
        return;
    }
    const Slot slot = explicit_slot >= 0 ? explicit_slot : g_r503.first_free_slot();
    if (slot < 0) { Serial.println("[r503] library FULL — no free slot."); return; }
    if (!g_r503.enroll_store(slot, err)) {
        r503_print_failure("Store", err);
        (void)g_r503.command_feedback(FeedbackState::FAULT, FpProgress{}, err);
        return;
    }
    (void)g_r503.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err);
    Serial.printf("[r503] ENROLLED into slot %d.  Now: 'r503 match'.\n", slot);

    /* If readback works, this is the moment the whole sync architecture is
     * actually proven end to end on real biometric data. */
    TemplateBytes t;
    if (g_r503.read_template(slot, t, err))
        Serial.printf("[r503] UpChar of the JUST-ENROLLED template: %u bytes — "
                      "template sync is viable on REAL data, not just a ramp.\n",
                      (unsigned)t.size());
    else
        r503_print_failure("read_template (post-enroll)", err);
}

static void r503_cmd_match() {
    if (!r503_ensure_open()) return;
    Serial.println("[r503] place finger (10s)...");
    FingerprintHALFailure err;
    (void)g_r503.command_feedback(FeedbackState::AWAITING_FINGER, FpProgress{}, err);
    const uint32_t t0 = millis();
    while (millis() - t0 < 10000) {
        MatchResult m = g_r503.match();
        if (m.outcome == MatchOutcome::NO_FINGER) { delay(120); continue; }
        if (m.outcome == MatchOutcome::MATCHED) {
            Serial.printf("[r503] MATCHED slot=%d score=%d\n", m.slot.value(),
                          m.score.value());
            (void)g_r503.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err);
        } else {
            Serial.println("[r503] NO MATCH (finger seen, nothing in the library "
                           "matched)");
            /* [CXC-FPID-RING-HIDES-DENIAL]: unrecognized and denied are the
             * SAME red — the ring is not a mirror of the bus event. */
            (void)g_r503.command_feedback(FeedbackState::REJECT_SILENT, FpProgress{}, err);
        }
        r503_print_diag(g_r503.diag());
        return;
    }
    Serial.println("[r503] no finger within 10s.");
    (void)g_r503.command_feedback(FeedbackState::IDLE, FpProgress{}, err);
}

static void r503_cmd_sweep() {
    if (!r503_ensure_open()) return;
    const char* names[8] = {"", "RED(0x01)","BLUE(0x02)","PURPLE(0x03)",
                            "GREEN(0x04)","YELLOW(0x05)","CYAN(0x06)","WHITE(0x07)"};
    Serial.println("[r503] COLOUR SWEEP: steady-on, ~3.5s each, index 0x01..0x07. Watch the ring; name them in order.");
    for (uint8_t cc = 1; cc <= 7; cc++) {
        FingerprintHALFailure err;
        Serial.printf("[r503] >>> #%u colour index 0x%02X (driver: %s)\n", cc, cc, names[cc]);
        if (!g_r503.aura_raw(r503::AURA_ON, 0x00, cc, 0x00, err)) r503_print_failure("aura_raw", err);
        delay(3500);
    }
    FingerprintHALFailure err;
    g_r503.aura_raw(r503::AURA_BREATHING, 0x50, r503::AURA_PURPLE, 0x00, err);
    Serial.println("[r503] sweep done -> back to breathing purple.");
}

static void r503_cmd_aura(const std::string& args) {
    if (!r503_ensure_open()) return;
    unsigned ctrl=3, speed=0, colour=2, count=0;
    sscanf(args.c_str(), "%x %x %x %x", &ctrl, &speed, &colour, &count);
    Serial.printf("[r503] aura ctrl=0x%02X speed=0x%02X colour=0x%02X count=0x%02X\n",
                  ctrl, speed, colour, count);
    FingerprintHALFailure err;
    if (!g_r503.aura_raw((uint8_t)ctrl,(uint8_t)speed,(uint8_t)colour,(uint8_t)count, err))
        r503_print_failure("aura_raw", err);
}

static void handleR503Command(const std::string& rest) {
    if (rest == "ping") {
        if (r503_ensure_open())
            Serial.printf("[r503] PONG — VfyPwd acknowledged, address 0x%08lX, "
                          "%u-slot library.\n",
                          (unsigned long)g_r503.link().address(),
                          g_r503.sys_para().finger_library_size);
        return;
    }
    if (rest == "info")  { r503_cmd_info(); return; }
    if (rest.rfind("extract", 0) == 0) {
        int slot = 1;
        const size_t sp = rest.find(' ');
        if (sp != std::string::npos) slot = atoi(rest.c_str() + sp + 1);
        r503_cmd_extract((Slot)slot);
        return;
    }
    if (rest == "match")   { r503_cmd_match(); return; }
    if (rest == "unwedge") { r503_cmd_unwedge(); return; }
    if (rest.rfind("synctest", 0) == 0) {
        const std::string arg = rest.size() > 8 ? rest.substr(9) : std::string();
        if (arg.rfind("store", 0) == 0) {
            const int slot = arg.size() > 5 ? atoi(arg.substr(6).c_str()) : 0;
            r503_cmd_synctest_store(slot);
            return;
        }
        /* Whitespace-separated options, any order:
         *   end          A2 sweep — payload all PID 0x02 + a separate EMPTY 0x08
         *   delay <ms>   inter-packet pacing sweep
         *   buf <1-6>    which char buffer
         *   quiet        suppress the per-packet trace
         *   noretry      skip the empty-end rescue (shows the RAW bench signature) */
        R503SyncTestOptions opt;
        bool verbose = true;
        size_t i = 0;
        while (i < arg.size()) {
            while (i < arg.size() && arg[i] == ' ') i++;
            size_t j = arg.find(' ', i);
            if (j == std::string::npos) j = arg.size();
            const std::string tok = arg.substr(i, j - i);
            i = j;
            if (tok.empty()) continue;
            /* consume the NEXT whitespace-separated token as an int */
            auto next_int = [&arg, &i]() -> int {
                while (i < arg.size() && arg[i] == ' ') i++;
                size_t k = arg.find(' ', i);
                if (k == std::string::npos) k = arg.size();
                const int v = atoi(arg.substr(i, k - i).c_str());
                i = k;
                return v;
            };
            if (tok == "end")
                opt.style = r503::EndPacketStyle::SEPARATE_EMPTY_END;
            else if (tok == "quiet")   verbose = false;
            else if (tok == "noretry") opt.retry_with_empty_end = false;
            else if (tok == "delay")   opt.inter_packet_delay_ms = (uint32_t)next_int();
            else if (tok == "buf") {
                const int b = next_int();
                opt.buffer_id = (uint8_t)(b >= 1 && b <= 6 ? b : 1);
            } else {
                /* a bare number keeps the original `synctest 3` spelling working */
                const int b = atoi(tok.c_str());
                if (b >= 1 && b <= 6) opt.buffer_id = (uint8_t)b;
            }
        }
        r503_cmd_synctest(opt, verbose);
        return;
    }
    if (rest == "sweep") { r503_cmd_sweep(); return; }
    if (rest.rfind("aura", 0) == 0) { r503_cmd_aura(rest.size()>4?rest.substr(5):std::string()); return; }
    if (rest.rfind("glow", 0) == 0) {
        r503_cmd_glow(rest.size() > 4 ? rest.substr(5) : std::string("ready"));
        return;
    }
    if (rest.rfind("enroll", 0) == 0) {
        r503_cmd_enroll(rest.size() > 6 ? atoi(rest.substr(7).c_str()) : -1);
        return;
    }
    Serial.println("[r503] commands:");
    Serial.println("  ping                    VfyPwd handshake");
    Serial.println("  info                    ReadSysPara + ReadProdInfo + occupancy");
    Serial.println("  extract [slot=1]        ⭐ LoadChar+UpChar, NO DownChar — the");
    Serial.println("                          discriminator for a SILENT UpChar");
    Serial.println("  synctest [opts]         ⭐ DownChar->UpChar round trip");
    Serial.println("     opts: end        A2 sweep (payload 0x02 + separate empty 0x08)");
    Serial.println("           delay <ms> inter-packet pacing sweep");
    Serial.println("           buf <1-6>  char buffer      quiet  no per-packet trace");
    Serial.println("           noretry    show the RAW failure, no empty-end rescue");
    Serial.println("  synctest store <slot>   DESTRUCTIVE — full FLASH round trip");
    Serial.println("  unwedge                 send a bare empty PID 0x08 + probe");
    Serial.println("  glow <ready|wait|busy|fault|accept|reject|off>");
    Serial.println("  enroll [slot] | match   need a real finger");
}

/* --- serial command: the no-reflash repoint mechanism for every transport
 * change AFTER this build. "transport mqtt" / "transport offline" persists
 * to NVS (bus_transport_set_selection); "restart" reboots to pick it up. --- */
static void handleSerialLine(const std::string& line) {
    if (line.rfind("r503 ", 0) == 0) {
        handleR503Command(line.substr(5));
    } else if (line == "r503") {
        handleR503Command("");
    } else if (line.rfind("transport ", 0) == 0) {
        std::string name = line.substr(10);
        bool ok = bus_transport_set_selection(name.c_str());
        Serial.printf("[cmd] transport '%s' -> %s%s\n", name.c_str(),
                      ok ? "STORED (NVS)" : "REFUSED (unknown transport)",
                      ok ? " — type 'restart' or power-cycle to apply" : "");
    } else if (line == "restart") {
        Serial.println("[cmd] restarting...");
        delay(100);
        ESP.restart();
    } else if (!line.empty()) {
        Serial.printf("[cmd] unknown: \"%s\" (try \"transport mqtt\", "
                      "\"transport offline\", \"restart\", \"r503\")\n", line.c_str());
    }
}

static void pollSerialCommands() {
    static std::string buf;
    while (Serial.available() > 0) {
        char c = (char)Serial.read();
        if (c == '\n' || c == '\r') {
            if (!buf.empty()) { handleSerialLine(buf); buf.clear(); }
        } else {
            buf += c;
        }
    }
}

/* --- OFFLINE demo: unchanged from every prior build. ---------------------- */
static TemplateBytes fp_synth(const std::string& seed) {
    const size_t n = static_cast<size_t>(g_hal.capabilities().template_size_bytes);
    std::string out;
    out.reserve(n);
    while (out.size() < n) out += seed;
    out.resize(n);
    return out;
}

static TemplatePush demo_push() {
    TemplatePush p;
    p.template_set_uuid = "uuid-S3-DEMO-001";
    p.role_capability_table = { { "crew", { "open_hatch" } }, { "guest", {} } };
    p.entries = {
        { "alice", 1, "Captain",   "crew",  fp_synth("tmpl-alice-1") },
        { "alice", 2, "Captain",   "crew",  fp_synth("tmpl-alice-2") },
        { "bob",   1, "Passenger", "guest", fp_synth("tmpl-bob-1")   },
    };
    return p;
}

static void runOfflineDemo() {
    Serial.println("\n--- 1. unconfigured device: heartbeat should be push_needed ---");
    HeartbeatState h1 = g_dev->emit_heartbeat();
    Serial.printf("[hb] state=%s (interval %.0fs)\n",
                  h1 == HeartbeatState::PUSH_NEEDED ? "PUSH_NEEDED" :
                  h1 == HeartbeatState::READY ? "READY" : "INOPERABLE",
                  g_dev->heartbeat_interval());

    Serial.println("\n--- 2. receive a template-set push (mock HAL installs it) ---");
    Serial.printf("[push] inbound topic (live bus): %s  (event_type \"template_set\")\n",
                  fingerprint_inbound_push_topic(kDeviceId).c_str());
    std::string status = g_dev->receive_push(demo_push());
    Serial.printf("[push] receive_push -> \"%s\"  (occupancy now %u slots)\n",
                  status.c_str(), (unsigned)g_hal.occupancy_count());

    Serial.println("\n--- 3. configured device: heartbeat should be ready ---");
    HeartbeatState h2 = g_dev->emit_heartbeat();
    Serial.printf("[hb] state=%s (interval %.0fs)\n",
                  h2 == HeartbeatState::READY ? "READY" :
                  h2 == HeartbeatState::PUSH_NEEDED ? "PUSH_NEEDED" : "INOPERABLE",
                  g_dev->heartbeat_interval());

    Serial.println("\n--- 4. inject a granted fingerprint match (crew finger) ---");
    Slot cs = crew_slot();
    g_hal.script_match(MatchResult::matched(cs, 200));
    std::optional<AuthOutcome> outcome = g_dev->poll_match();
    Serial.printf("[match] crew slot=%d -> outcome=%s  (attempts=%d)\n", cs,
                  outcome.has_value() ? to_string(*outcome) : "none",
                  g_dev->count_authentication_attempts());

    Serial.println("\n--- 5. inject an intruder match (no bridge binding) ---");
    g_hal.script_match(MatchResult::no_match());
    std::optional<AuthOutcome> intruder = g_dev->poll_match();
    Serial.printf("[match] no_match -> outcome=%s  (ring hides denial as one red)\n",
                  intruder.has_value() ? to_string(*intruder) : "none");

    bool green = (h1 == HeartbeatState::PUSH_NEEDED) && (status == "loaded") &&
                 (h2 == HeartbeatState::READY) &&
                 outcome.has_value() && *outcome == AuthOutcome::GRANTED &&
                 intruder.has_value() && *intruder == AuthOutcome::UNRECOGNIZED;
    Serial.println("\n============================================================");
    Serial.printf ("  ON-CHIP RESULT: %s\n",
                   green ? "MACHINE-GREEN (mock HAL + device logic tick correctly)"
                         : "RED — a step did not behave as the reference does");
    Serial.println("  Real R503 silicon: GLASS-PENDING by design.");
    Serial.println("============================================================\n");
}

void setup() {
    Serial.begin(115200);
    uint32_t t0 = millis();
    while (!Serial && millis() - t0 < 3000) { delay(10); }  /* CDC enumerate */
    delay(300);

    Serial.println();
    Serial.println("============================================================");
    Serial.println("  R503 FINGERPRINT DEVICE HALF — MOCK SENSOR BRING-UP");
    Serial.println("  [SPEC:CXC-FPHAL] C71/C72/C73  (ESP32-S3, native USB-CDC)");
    Serial.printf ("  build: %s %s\n", __DATE__, __TIME__);   /* compiler-written stamp */
    Serial.println("============================================================");

    /* Transport is a CONFIG VALUE (NVS, default OFFLINE). */
    const TransportSelection& sel = bus_transport_selection();
    Serial.printf("[boot] transport: %s (from %s; known=%s; needs_network=%s)\n",
                  sel.name.c_str(), sel.source.c_str(),
                  sel.known ? "yes" : "no", sel.needs_network ? "yes" : "no");
    Serial.printf("[boot] known transports: %s\n", transport_known_names());

    g_online = sel.known && sel.name == "mqtt";

    if (g_online) {
        Serial.println("[boot] transport=mqtt: bringing up WiFi + MQTT (deliberate, "
                       "confirmed-live-broker join).");
        bool wifi_ok = connectWiFiBlocking();
        bool mqtt_ok = wifi_ok && connectMqttBlocking();
        if (!mqtt_ok) {
            Serial.println("[boot] WiFi/MQTT bring-up FAILED — falling back to the "
                           "offline demo this boot (transport selection unchanged; "
                           "retry with 'restart' once the link is confirmed).");
            g_online = false;
        }
    } else if (sel.needs_network) {
        Serial.printf("[boot] WARNING: selected transport '%s' wants a network and "
                      "this build only implements mqtt — treating as offline.\n",
                      sel.name.c_str());
    } else {
        Serial.println("[boot] offline: no network brought up (by design).");
    }

    g_dev = new FingerprintDevice(
        &g_hal, g_online ? static_cast<FingerprintBusSink*>(&g_mqtt_bus)
                         : static_cast<FingerprintBusSink*>(&g_serial_bus),
        kDeviceId, "open_hatch", &g_nvs,
        /*uplink*/ nullptr, /*match_threshold*/ std::nullopt,
        /*clock*/ []() { return millis() / 1000.0; },
        /*event_id_factory*/ nullptr,
        /* the OFFLINE demo path pushes raw synthetic bytes directly — its
         * templates are never `fingerprint://` refs, so a null resolver
         * there is correct and must keep working unchanged. Only the
         * ONLINE (transport=mqtt) path receives real pushes whose
         * `template` field may be a reference, so only it gets the real
         * HTTPClient resolver. */
        g_online ? TemplateResolver(http_resolve_template) : TemplateResolver(nullptr));

    if (g_online) {
        Serial.println("\n--- ONLINE: device is live on the ComposeBus ---");
        Serial.printf("[boot] device_id=%s gated_capability=open_hatch\n", kDeviceId);
        HeartbeatState h = g_dev->emit_heartbeat();
        Serial.printf("[hb] initial state=%s (interval %.0fs) — published on the "
                      "live bus\n",
                      h == HeartbeatState::PUSH_NEEDED ? "PUSH_NEEDED" :
                      h == HeartbeatState::READY ? "READY" : "INOPERABLE",
                      g_dev->heartbeat_interval());
        Serial.printf("[boot] awaiting a template_set push on %s/global/%s/template_set "
                      "(fingerprint_storage, run locally against this same broker, "
                      "originates it)\n", CIO_TOPIC_PREFIX, kDeviceId);
    } else {
        runOfflineDemo();
    }

    Serial.println("[cmd] serial commands: \"transport mqtt\", \"transport offline\", "
                   "\"restart\" (repoint without reflashing, effective next boot)");
    Serial.println("[cmd] REAL R503 (opt-in; the UART is not opened until you ask): "
                   "\"r503 ping\", \"r503 info\", \"r503 synctest\", "
                   "\"r503 glow ready\", \"r503 enroll\", \"r503 match\"");
    Serial.printf ("[cmd] r503 wiring: UART%d tx=GPIO%d rx=GPIO%d @%d 8N1; "
                   "3.3VT MUST tie to 3V3 (it drives WAKEUP, and at 5V it "
                   "overdrives a GPIO)\n",
                   R503_UART_NUM, R503_TX_PIN, R503_RX_PIN, R503_BAUD);
    Serial.println("[cmd] ⛔ START WITH \"r503 synctest\" — it answers, without a "
                   "finger, whether this part's firmware supports template "
                   "upload/download at all.  That is the open architectural "
                   "question, and no amount of compiling answers it.");
}

void loop() {
    pollSerialCommands();

    if (g_online) {
        if (!g_mqtt.connected()) {
            Serial.println("[mqtt] connection dropped — reconnecting...");
            connectMqttBlocking();
        }
        g_mqtt.loop();

        static uint32_t last_hb = 0;
        if (millis() - last_hb >= 5000) {
            last_hb = millis();
            HeartbeatState s = g_dev->emit_heartbeat();
            Serial.printf("[tick] uptime=%lus heartbeat=%s\n",
                          (unsigned long)(millis() / 1000),
                          s == HeartbeatState::READY ? "ready" :
                          s == HeartbeatState::PUSH_NEEDED ? "push_needed" : "inoperable");
        }

        /* Script the same crew + intruder match the offline demo runs,
         * timed off the ASYNC push arrival instead of a synchronous call. */
        if (g_push_loaded && !g_crew_scripted &&
            millis() - g_push_loaded_at_ms >= 2000) {
            g_crew_scripted = true;
            Serial.println("\n--- scripted: crew fingerprint match ---");
            Slot cs = crew_slot();
            g_hal.script_match(MatchResult::matched(cs, 200));
            std::optional<AuthOutcome> outcome = g_dev->poll_match();
            Serial.printf("[match] crew slot=%d -> outcome=%s (attempts=%d)\n", cs,
                          outcome.has_value() ? to_string(*outcome) : "none",
                          g_dev->count_authentication_attempts());
        }
        if (g_push_loaded && g_crew_scripted && !g_intruder_scripted &&
            millis() - g_push_loaded_at_ms >= 5000) {
            g_intruder_scripted = true;
            Serial.println("\n--- scripted: intruder (no bridge binding) ---");
            g_hal.script_match(MatchResult::no_match());
            std::optional<AuthOutcome> intruder = g_dev->poll_match();
            Serial.printf("[match] no_match -> outcome=%s\n",
                          intruder.has_value() ? to_string(*intruder) : "none");
        }
    } else {
        static uint32_t last = 0;
        if (millis() - last >= 5000) {
            last = millis();
            HeartbeatState s = g_dev->emit_heartbeat();
            Serial.printf("[tick] uptime=%lus heartbeat=%s\n",
                          (unsigned long)(millis() / 1000),
                          s == HeartbeatState::READY ? "ready" :
                          s == HeartbeatState::PUSH_NEEDED ? "push_needed" : "inoperable");
        }
    }
    delay(50);
}

#endif /* FINGERPRINT_BRINGUP */
