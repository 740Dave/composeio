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

#include <Preferences.h>
#include <esp_system.h>      /* esp_random() for the opaque template id */

#include "fingerprint_null_hal.h"  /* the INOPERABLE default — production HAL */
#ifdef FP_ALLOW_MOCK
#include "fingerprint_mock.h"       /* the FUNCTIONAL mock — gated, see below */
#endif
#include "fingerprint_device.h"
#include "bus_transport_select.h"
#include "real_r503_hal.h"   /* the REAL R503 backend — ADDITIVE, opt-in */
#include "fp_persist.h"       /* NVS (de)serialization of the device bridge */
#include "fp_bus_messages.h"  /* announce / enroll status+complete builders + router */

#ifdef TM1637_PANEL
/* ⛔ ADDITIVE, INERT WITHOUT THE FLAG.  The combined S3 build (env:
 * fingerprint_tm1637_s3) adds a 4x TM1637 tube panel as a SEPARATE subsystem
 * ([DEC-R503-AND-FOUR-TM1637-ON-ONE-ESP32-S3]).  Every hook below is
 * #ifdef TM1637_PANEL, so env:fingerprint_s3 compiles the identical R503 path
 * with none of this present — the guardrail that keeps a tube fault away from
 * finger validation. */
#include "tm1637/tm1637_panel.h"
#endif

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
 * PLATFORMIO_BUILD_FLAGS='-DMQTT_BROKER=\"host\"' when the Buildhost cutover
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
 * exactly: LIVE at buildhost.local:8423 (verified 2026-08-19). Override with
 * PLATFORMIO_BUILD_FLAGS='-DTEMPLATE_STORE_URL=\"http://host:port\"' — no
 * code change. */
#ifndef TEMPLATE_STORE_URL
#define TEMPLATE_STORE_URL "http://buildhost.local:8423"
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

/* =========================================================================
 * ⛔ HAL BINDING — STRUCTURALLY GUARDED.  [DEC-R503-MOCK-STRUCTURAL-GUARD] (D9)
 * [CXC-FPHAL-MOCK-ROLE] [CXC-FPHAL-MATCH-USES-ENROLL-SENSOR]
 * closes DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389.
 * -------------------------------------------------------------------------
 * g_null is the INOPERABLE default a PRODUCTION build (fingerprint_tm1637_s3,
 * no FP_ALLOW_MOCK) binds when no real R503 is confirmed live: match() can
 * NEVER grant, occupancy is empty, so the device can never impersonate an
 * armed/loaded sensor.  The functional MockFingerprintHAL (g_hal) — which CAN
 * script a granted match — is compiled and bindable ONLY under an EXPLICIT
 * -DFP_ALLOW_MOCK (env:native_test defines it; a deliberate bench build may).
 * In production g_hal DOES NOT EXIST: the just-fixed silent-mock fallback is
 * not merely discouraged, it does not compile.  See the ratified use-envelope
 * on MockFingerprintHAL in fingerprint_mock.h (X host oracle / Y gated bench /
 * Z gated demo / NEVER a silent production fallback). */
static NullFingerprintHAL g_null;
#ifdef FP_ALLOW_MOCK
static MockFingerprintHAL g_hal;   /* Y/Z ONLY — never a production device HAL */
#endif
static DevicePersistence g_nvs;
static SerialBus g_serial_bus;
static WiFiClient g_wifi_client;
static PubSubClient g_mqtt(g_wifi_client);
static MqttFpBusSink g_mqtt_bus(g_mqtt);
static FingerprintDevice* g_dev = nullptr;
static bool g_online = false;      /* INTENT: transport=mqtt (NOT "connected") */
static bool g_connected = false;   /* WiFi + MQTT actually up right now */

/* ESP32 NVS-backed persistence of the device bridge ([CXC-FPID-DEVICE-BRIDGE]).
 * The R503 flash keeps templates across reboot, but the bridge (slot ->
 * person/ordinal/character/role) lives in RAM, so without this a post-reboot
 * match against an enrolled slot resolves UNRECOGNIZED. */
static Preferences g_prefs;
static std::string g_last_persisted;     /* skip redundant NVS writes (flash wear) */
static unsigned long g_bus_seq = 0;      /* seq for the announce/status/complete envelopes */

/* A bus enroll runs a ~40s blocking ceremony, too long for the MQTT callback,
 * so cmd/enroll only RECORDS the request here; loop() runs it. */
struct PendingEnroll {
    bool active = false;
    bool cancel_requested = false;
    std::string person;
    std::string request_id;
    int ordinal = 0;
};
static PendingEnroll g_enroll;

/* The device's real-vs-inoperable HAL is chosen at RUNTIME (the ONLINE boot
 * promotes to the REAL R503 g_r503 when a sensor answers r503_ensure_open()),
 * but WHICH HAL fills the no-sensor slot is a COMPILE-TIME guard, never a
 * silent runtime fork ([DEC-R503-MOCK-STRUCTURAL-GUARD]):
 *   - PRODUCTION (no FP_ALLOW_MOCK): the inoperable NullFingerprintHAL. It can
 *     never grant, so a sensorless board announces fingerprint_inoperable and
 *     waits for a real rebind — it CANNOT impersonate armed/loaded.
 *   - FP_ALLOW_MOCK (native_test / opt-in bench): the functional mock, so the
 *     offline demo + scripted matches keep working byte-for-byte (Y/Z).
 * A template_set push then DownChars into whichever HAL this points at. */
#ifdef FP_ALLOW_MOCK
static FingerprintHAL* g_device_hal = &g_hal;
#else
static FingerprintHAL* g_device_hal = &g_null;
#endif


static Slot crew_slot() {
    for (const auto& kv : g_dev->bridge()) if (kv.second.role == "crew") return kv.first;
    return -1;
}

/* =========================================================================
 * NVS persistence + the retained/device-state bus publishers.
 * ========================================================================= */

/* Load the persisted bridge into g_nvs (BEFORE any heartbeat/match path runs). */
static void nvs_load_bridge() {
    g_prefs.begin("fpdev", /*readOnly=*/true);
    String blob = g_prefs.getString("state", "");
    g_prefs.end();
    if (blob.length() == 0) {
        Serial.println("[nvs] no persisted bridge (first boot or cleared).");
        return;
    }
    std::string s(blob.c_str(), blob.length());
    if (fp_persist::from_blob(s, g_nvs)) {
        g_last_persisted = s;
        Serial.printf("[nvs] restored bridge: %u slot(s), %u role(s), count=%d\n",
                      (unsigned)g_nvs.bridge.size(),
                      (unsigned)g_nvs.role_capability_table.size(),
                      g_nvs.count_authentication_attempts);
    } else {
        Serial.println("[nvs] persisted blob did not parse — ignoring (reset).");
    }
}

/* Persist g_nvs ONLY when it actually changed (bounded flash wear). */
static void nvs_persist_if_changed() {
    std::string blob = fp_persist::to_blob(g_nvs);
    if (blob == g_last_persisted) return;
    g_prefs.begin("fpdev", /*readOnly=*/false);
    g_prefs.putString("state", blob.c_str());
    g_prefs.end();
    g_last_persisted = blob;
    Serial.printf("[nvs] persisted bridge (%u bytes, %u slots)\n",
                  (unsigned)blob.size(), (unsigned)g_nvs.bridge.size());
}

/* One retain-capable publish — the helper that carries the retain flag inv-4's
 * device-state messages need (announce, enroll status).  The FingerprintBusSink
 * events (auth_event, template_set_loaded/failed) correctly stay non-retained
 * through MqttFpBusSink. */
static bool mqttPublish(const std::string& topic, const std::string& payload,
                        bool retain) {
    if (!g_mqtt.connected()) return false;
    bool ok = g_mqtt.publish(topic.c_str(),
                             reinterpret_cast<const uint8_t*>(payload.data()),
                             payload.size(), retain);
    Serial.printf("[MQTT PUB%s] %s (%u bytes) -> %s\n", retain ? " retained" : "",
                  topic.c_str(), (unsigned)payload.size(), ok ? "ok" : "FAILED");
    return ok;
}

/* Retained <dev>/announce ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]) — capabilities from
 * the BOUND HAL (g_device_hal: the real R503 when a sensor answered, else the
 * mock).  Unstarves the service's size-guard, which raises on a missing key. */
static void publishDeviceAnnounce() {
    Capabilities c = g_device_hal->capabilities();
    std::string msg = fp_bus::announce(kDeviceId, "open_hatch", c.slot_count,
                                       c.template_size_bytes, c.supports_readback,
                                       c.feedback_profile,
                                       millis() / 1000.0, ++g_bus_seq);
    std::string topic = std::string(CIO_TOPIC_PREFIX) + "/global/" +
                        std::string(kDeviceId) + "/announce";
    mqttPublish(topic, msg, fp_bus::retained(fp_bus::Outbound::ANNOUNCE));
}

static void publishEnrollStatus(const char* phase, int ordinal, int capture_n,
                                int capture_of) {
    std::string msg = fp_bus::enroll_status(kDeviceId, phase, ordinal, capture_n,
                                            capture_of, millis() / 1000.0, ++g_bus_seq);
    std::string topic = std::string(CIO_TOPIC_PREFIX) + "/global/" +
                        std::string(kDeviceId) + "/status/enroll";
    mqttPublish(topic, msg, fp_bus::retained(fp_bus::Outbound::ENROLL_STATUS));
}

static void publishEnrollComplete(const std::string& person, int ordinal,
                                  const std::string& template_ref,
                                  const std::string& request_id) {
    std::string msg = fp_bus::enroll_complete(kDeviceId, person, ordinal,
                                              template_ref, request_id,
                                              millis() / 1000.0, ++g_bus_seq);
    std::string topic = std::string(CIO_TOPIC_PREFIX) + "/global/" +
                        std::string(kDeviceId) + "/result/enroll/complete";
    mqttPublish(topic, msg, fp_bus::retained(fp_bus::Outbound::ENROLL_COMPLETE));
}

/* 32 hex chars of hardware randomness — an opaque template-store id. */
static std::string genOpaqueId() {
    static const char* h = "0123456789abcdef";
    std::string id;
    id.reserve(32);
    for (int w = 0; w < 4; w++) {
        uint32_t r = esp_random();
        for (int i = 7; i >= 0; i--) id += h[(r >> (i * 4)) & 0xF];
    }
    return id;
}

/* PUT raw template bytes to the dedicated template store; return the
 * fingerprint:// ref, or "" on failure.  ⛔ raw bytes NEVER go on the bus
 * ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F]); the store holds them and
 * the ref crosses the wire.  Same store URL config the resolver uses. */
static std::string uploadTemplate(const TemplateBytes& t) {
    std::string opaque = genOpaqueId();
    std::string url = std::string(TEMPLATE_STORE_URL) + "/api/v1/templates/" + opaque;
    HTTPClient http;
    if (!http.begin(url.c_str())) {
        Serial.printf("[enroll] template PUT: could not begin %s\n", url.c_str());
        return "";
    }
    int code = http.PUT(reinterpret_cast<uint8_t*>(const_cast<char*>(t.data())),
                        t.size());
    http.end();
    if (code != 200 && code != 201 && code != 204) {
        Serial.printf("[enroll] template PUT %s -> HTTP %d (FAILED)\n", url.c_str(), code);
        return "";
    }
    Serial.printf("[enroll] template PUT %s -> HTTP %d (ok, %u bytes)\n", url.c_str(),
                  code, (unsigned)t.size());
    return "fingerprint://" + opaque;
}

/* --- inbound: decode a template_set push off the wire (mirrors
 * fingerprint_inbound_push_topic()'s contract) and hand it to the SAME
 * receive_push() the offline path calls. --------------------------------- */
static void onMqttMessage(char* topic, uint8_t* payload, unsigned int length) {
    Serial.printf("[MQTT RX] %s (%u bytes)\n", topic, length);
    DynamicJsonDocument doc(16384);
    DeserializationError perr = deserializeJson(doc, payload, length);
    if (perr) {
        Serial.printf("[rx] JSON parse error on %s: %s\n", topic, perr.c_str());
        return;
    }
    JsonObject data = doc["data"].as<JsonObject>();

#ifdef TM1637_PANEL
    /* Tube-panel namespaces arrive as composeio/global/<ns> — a scalar STATE
     * message carrying its value in top-level "val" (transport.py BusMessage).
     * Intercept an EXACT panel-namespace match and stop; a device-addressed
     * finger topic (…/global/<device_id>/…) never equals one, so this cannot
     * shadow the finger routing below. */
    {
        const std::string prefix = std::string(CIO_TOPIC_PREFIX) + "/global/";
        const std::string topic_s(topic);
        if (topic_s.rfind(prefix, 0) == 0) {
            const std::string ns = topic_s.substr(prefix.size());
            const char* pns[8];
            size_t nn = tm1637_panel_namespaces(pns, 8);
            for (size_t i = 0; i < nn; i++) {
                if (ns == pns[i]) {
                    if (doc.containsKey("val"))
                        tm1637_panel_apply_ns(ns.c_str(), doc["val"].as<double>());
                    else
                        Serial.printf("[tm1637] %s arrived with no \"val\" — ignored\n",
                                      ns.c_str());
                    return;
                }
            }
        }
    }
#endif

    /* ⛔ [CXC-FPBUS-ADDRESSING]: route by the TRAILING-TAIL vocabulary after the
     * "<prefix>/global/<device_id>/" scope — NEVER split on the first slash
     * (the device_id "dev/s3-fp1" contains one). */
    fp_bus::Inbound kind =
        fp_bus::route_inbound(std::string(topic), CIO_TOPIC_PREFIX, kDeviceId);

    if (kind == fp_bus::Inbound::ENROLL) {
        /* Record only — loop() runs the ~40s ceremony out of this callback. */
        if (g_enroll.active) {
            Serial.println("[enroll] a ceremony is already in progress — ignoring cmd/enroll");
            return;
        }
        g_enroll.active = true;
        g_enroll.cancel_requested = false;
        g_enroll.person = std::string(data["person"] | "");
        g_enroll.ordinal = data["ordinal"] | 1;
        g_enroll.request_id = std::string(data["request_id"] | "");
        Serial.printf("[enroll] cmd/enroll queued: person=%s ordinal=%d request_id=%s\n",
                      g_enroll.person.c_str(), g_enroll.ordinal, g_enroll.request_id.c_str());
        return;
    }
    if (kind == fp_bus::Inbound::ENROLL_CANCEL) {
        std::string rid = std::string(data["request_id"] | "");
        if (g_enroll.active && (rid.empty() || rid == g_enroll.request_id)) {
            g_enroll.cancel_requested = true;
            Serial.printf("[enroll] cmd/enroll/cancel for request_id=%s — will abandon\n",
                          rid.c_str());
        } else {
            Serial.printf("[enroll] cancel for request_id=%s but no matching ceremony\n",
                          rid.c_str());
        }
        return;
    }
    if (kind != fp_bus::Inbound::TEMPLATE_SET) {
        const char* evt = doc["evt"] | "";
        Serial.printf("[rx] ignoring %s (tail not a device command; evt=\"%s\")\n",
                      topic, evt);
        return;
    }

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
    /* read_occupancy() is the FingerprintHAL virtual — it reflects whichever
     * HAL the bus path is bound to (mock or the real sensor).  occupancy_count()
     * was a mock-only convenience and would lie about the physical R503. */
    Serial.printf("[push] receive_push -> \"%s\" (occupancy now %u slots)\n",
                  status.c_str(), (unsigned)g_device_hal->read_occupancy().size());
    /* [CXC-FPID-DEVICE-BRIDGE]: the push rebuilt the bridge — persist it so a
     * reboot does not misread crew as intruder. */
    nvs_persist_if_changed();
    if (status == "loaded")
        Serial.println("[push] loaded — device is armed and continuously polling "
                       "for matches (loop() poll_match).");
}

/* --- ONLINE setup: WiFi + MQTT, blocking with a bounded retry, exactly the
 * connectivity shape main.cpp already uses on the C3/other S3 envs. --------- */
static bool connectWiFiBlocking(uint32_t timeout_ms = 15000) {
    Serial.printf("[wifi] connecting to \"%s\"...\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeout_ms) {
        delay(300);
        Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() != WL_CONNECTED) {
        Serial.printf("[wifi] FAILED to connect within %lus\n",
                      (unsigned long)(timeout_ms / 1000));
        return false;
    }
    Serial.printf("[wifi] connected, IP=%s\n", WiFi.localIP().toString().c_str());
    return true;
}

/* Set the PubSubClient parameters ONCE (buffer, broker, callback). */
static void mqttInit() {
    g_mqtt.setBufferSize(16384);  /* a template-set push runs a few KB */
    g_mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    g_mqtt.setCallback(onMqttMessage);
}

/* ONE non-blocking MQTT connect attempt.  On success (re)subscribes the three
 * device-addressed inbound topics and (re)publishes the retained announce. */
static bool mqttConnectOnce() {
    char clientId[32];
    uint64_t mac = ESP.getEfuseMac();
    snprintf(clientId, sizeof(clientId), "cio-fp-s3-%04x", (unsigned)(mac & 0xFFFF));
    Serial.printf("[mqtt] connect attempt to %s:%d as %s...\n", MQTT_BROKER, MQTT_PORT, clientId);
    if (!g_mqtt.connect(clientId)) {
        Serial.printf("[mqtt] connect failed (rc=%d)\n", g_mqtt.state());
        return false;
    }
    const std::string base = std::string(CIO_TOPIC_PREFIX) + "/global/" +
                             std::string(kDeviceId) + "/";
    const std::string push_topic   = base + "template_set";
    const std::string enroll_topic = base + "cmd/enroll";
    const std::string cancel_topic = base + "cmd/enroll/cancel";
    g_mqtt.subscribe(push_topic.c_str(), 1);
    g_mqtt.subscribe(enroll_topic.c_str(), 1);
    g_mqtt.subscribe(cancel_topic.c_str(), 1);
    Serial.printf("[mqtt] connected; subscribed: %s\n", push_topic.c_str());
    Serial.printf("[mqtt] subscribed: %s , %s\n", enroll_topic.c_str(), cancel_topic.c_str());
#ifdef TM1637_PANEL
    /* The tube panel subscribes GLOBAL namespaces (composeio/global/<ns>), NOT
     * the device-addressed inbound topics above.  Each is an EXACT subscription
     * (no wildcard), the restart.cpp idiom.  onMqttMessage() routes their
     * arrivals to tm1637_panel_apply_ns(). */
    {
        const char* ns[8];
        size_t nn = tm1637_panel_namespaces(ns, 8);
        for (size_t i = 0; i < nn; i++) {
            std::string t = std::string(CIO_TOPIC_PREFIX) + "/global/" + ns[i];
            g_mqtt.subscribe(t.c_str(), 0);
            Serial.printf("[mqtt] subscribed (tube panel): %s\n", t.c_str());
        }
    }
#endif
    publishDeviceAnnounce();   /* retained — refreshed on every (re)connect */
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

/* --- self-contained SHA-256 + base64 for `r503 dump`'s self-proving frame.
 * A standard SHA-256 (public-domain construction) so the digest EQUALS
 * Python hashlib.sha256's over the identical raw bytes — the host harness
 * re-hashes the decoded capture and refuses a mismatch, so the frame carries
 * its own falsifier ([CXC-VISION-EPISTEMICS]).  In-tree rather than mbedtls so
 * the byte-for-byte digest contract does not ride on which crypto header this
 * board's core happens to expose.  This TU compiles ONLY in env:fingerprint_s3
 * (guarded by FINGERPRINT_BRINGUP), so it adds nothing to the host build. */
namespace r503dump {

struct Sha256 {
    uint32_t s[8];
    uint64_t len;          /* total bytes fed */
    uint8_t  buf[64];
    size_t   buflen;

    static uint32_t ror(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

    void init() {
        static const uint32_t iv[8] = {
            0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
            0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u };
        for (int i = 0; i < 8; i++) s[i] = iv[i];
        len = 0; buflen = 0;
    }

    void block(const uint8_t* p) {
        static const uint32_t k[64] = {
            0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,0x923f82a4u,0xab1c5ed5u,
            0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,
            0xe49b69c1u,0xefbe4786u,0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
            0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,0x06ca6351u,0x14292967u,
            0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,
            0xa2bfe8a1u,0xa81a664bu,0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
            0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,0x5b9cca4fu,0x682e6ff3u,
            0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u };
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t)p[i*4]<<24 | (uint32_t)p[i*4+1]<<16 |
                   (uint32_t)p[i*4+2]<<8 | (uint32_t)p[i*4+3];
        for (int i = 16; i < 64; i++) {
            uint32_t s0 = ror(w[i-15],7) ^ ror(w[i-15],18) ^ (w[i-15]>>3);
            uint32_t s1 = ror(w[i-2],17) ^ ror(w[i-2],19) ^ (w[i-2]>>10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a=s[0],b=s[1],c=s[2],d=s[3],e=s[4],f=s[5],g=s[6],h=s[7];
        for (int i = 0; i < 64; i++) {
            uint32_t S1 = ror(e,6) ^ ror(e,11) ^ ror(e,25);
            uint32_t ch = (e & f) ^ (~e & g);
            uint32_t t1 = h + S1 + ch + k[i] + w[i];
            uint32_t S0 = ror(a,2) ^ ror(a,13) ^ ror(a,22);
            uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + maj;
            h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
        }
        s[0]+=a; s[1]+=b; s[2]+=c; s[3]+=d; s[4]+=e; s[5]+=f; s[6]+=g; s[7]+=h;
    }

    void update(const uint8_t* p, size_t n) {
        len += n;
        while (n > 0) {
            size_t take = 64 - buflen;
            if (take > n) take = n;
            for (size_t i = 0; i < take; i++) buf[buflen+i] = p[i];
            buflen += take; p += take; n -= take;
            if (buflen == 64) { block(buf); buflen = 0; }
        }
    }

    void finish(uint8_t out[32]) {
        uint64_t bitlen = len * 8;
        uint8_t pad = 0x80;
        update(&pad, 1);
        uint8_t zero = 0x00;
        while (buflen != 56) update(&zero, 1);
        uint8_t lenb[8];
        for (int i = 0; i < 8; i++) lenb[i] = (uint8_t)(bitlen >> (56 - i*8));
        update(lenb, 8);
        for (int i = 0; i < 8; i++) {
            out[i*4]   = (uint8_t)(s[i] >> 24);
            out[i*4+1] = (uint8_t)(s[i] >> 16);
            out[i*4+2] = (uint8_t)(s[i] >> 8);
            out[i*4+3] = (uint8_t)(s[i]);
        }
    }
};

inline std::string sha256_hex(const std::string& data) {
    Sha256 h; h.init();
    h.update(reinterpret_cast<const uint8_t*>(data.data()), data.size());
    uint8_t d[32]; h.finish(d);
    static const char* hexd = "0123456789abcdef";
    std::string out; out.reserve(64);
    for (int i = 0; i < 32; i++) { out += hexd[d[i]>>4]; out += hexd[d[i]&0xF]; }
    return out;
}

inline std::string base64(const std::string& in) {
    static const char* tbl =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((in.size() + 2) / 3) * 4);
    const unsigned char* p = reinterpret_cast<const unsigned char*>(in.data());
    size_t i = 0;
    while (i + 3 <= in.size()) {
        uint32_t n = ((uint32_t)p[i]<<16) | ((uint32_t)p[i+1]<<8) | p[i+2];
        out += tbl[(n>>18)&63]; out += tbl[(n>>12)&63];
        out += tbl[(n>>6)&63];  out += tbl[n&63];
        i += 3;
    }
    if (in.size() - i == 1) {
        uint32_t n = (uint32_t)p[i]<<16;
        out += tbl[(n>>18)&63]; out += tbl[(n>>12)&63];
        out += '='; out += '=';
    } else if (in.size() - i == 2) {
        uint32_t n = ((uint32_t)p[i]<<16) | ((uint32_t)p[i+1]<<8);
        out += tbl[(n>>18)&63]; out += tbl[(n>>12)&63];
        out += tbl[(n>>6)&63]; out += '=';
    }
    return out;
}

}  /* namespace r503dump */

static void r503_print_failure(const char* what, const FingerprintHALFailure& e) {
    Serial.printf("[r503] %s FAILED — axis=%s", what, to_string(e.axis));
    if (e.sensor_code.has_value())
        Serial.printf(" sensor_code=0x%02X", e.sensor_code.value());
    Serial.printf(" : %s\n", e.detail.c_str());
}

/* CHANGE-1(b)/(c) — promote the bus path to the REAL R503 and LIVE-REBIND the
 * running device (DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389). Called from
 * r503_ensure_open()'s success path so it fires from the FIRST successful open
 * AND from 'r503 ping'/'r503 match' — not only from boot promotion. Idempotent:
 * once g_device_hal is the real HAL there is nothing to do. Safe before g_dev
 * exists (boot): it flips the pointer and setup() constructs g_dev on it. */
static void r503_promote_and_rebind() {
    if (g_device_hal == &g_r503) return;   /* already the real HAL */
    g_device_hal = &g_r503;
    Serial.println("[r503] bus path PROMOTED to the REAL R503 (RealR503HAL).");
    if (g_dev != nullptr) {
        /* the device was born on the mock; repoint its HAL so poll_match /
         * receive_push reach the real sensor instead of the dead mock path. */
        g_dev->rebind_hal(g_device_hal);
        g_dev->set_hal_confirmed_live(true);
        /* mock-era slots != real slots: drop the stale bridge, zero the count,
         * and demand a fresh push so templates re-DownChar into REAL flash. */
        g_dev->invalidate_roster_request_push();
        Serial.println("[r503] LIVE-REBIND: device now on the real HAL; mock-era "
                       "roster invalidated, fingerprint_push_needed announced — "
                       "re-push the roster to arm the real match path.");
    }
}

/* Lazily open the UART and handshake.  Returns false LOUDLY. */
static bool r503_ensure_open() {
    if (g_r503_open && g_r503.ready()) { r503_promote_and_rebind(); return true; }
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
    r503_promote_and_rebind();
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

/* `r503 dump <slot>` — READ-ONLY.  LoadChar+UpChar a stored template out and
 * emit it as a base64 frame a host can scrape deterministically (raw binary is
 * unsafe on the CDC console).  Non-destructive: no Store, no Empty, no flash
 * write.  The sha256 is over the RAW template bytes, so a host that decodes the
 * frame and re-hashes proves the capture is byte-exact ([CXC-VISION-EPISTEMICS]
 * — the frame carries its own falsifier). */
static void r503_cmd_dump(int slot) {
    if (!r503_ensure_open()) return;
    TemplateBytes bytes;
    FingerprintHALFailure err;
    if (!g_r503.read_template((Slot)slot, bytes, err)) {
        r503_print_failure("read_template (dump)", err);
        return;
    }
    const std::string sha = r503dump::sha256_hex(bytes);
    const std::string b64 = r503dump::base64(bytes);
    Serial.printf("-----R503-DUMP-BEGIN slot=%d bytes=%u sha256=%s-----\n",
                  slot, (unsigned)bytes.size(), sha.c_str());
    for (size_t i = 0; i < b64.size(); i += 76)
        Serial.println(b64.substr(i, 76).c_str());
    Serial.println("-----R503-DUMP-END-----");
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

/* (r503 synctest store was REMOVED — it wrote a synthetic ramp to a real flash
 * slot, violating [CXC-FPHAL-PARITY-TEST] proportionality.  The read-only
 * synctest and the real-template-only `r503 storetest real <slot>` remain.) */

/* ⭐ THE STORE DE-RISK INSTRUMENT (Round 2, after the 2026-08-20 Store no-ack).
 * DownChar -> Store(0x06) flash -> readback, TRACED, with a settle/pacing
 * sweep.  synctest proved the char-buffer round trip but never touched flash;
 * this exercises the exact step the bus reload no-acked.  DESTRUCTIVE. */
static void r503_cmd_storetest(const R503StoreTestOptions& opt) {
    if (!r503_ensure_open()) return;
    if (opt.real_src_slot < 0) {
        Serial.println("[r503] storetest needs a REAL source: 'r503 storetest real "
                       "<srcslot>'.  Writing a synthetic ramp to real flash is not "
                       "permitted ([CXC-FPHAL-PARITY-TEST]) — enroll or push a "
                       "template first, then 'r503 storetest real <thatslot>'.");
        return;
    }
    Serial.println("\n============================================================");
    Serial.println("  R503 STORE DE-RISK  (DownChar -> Store(0x06) flash -> readback)");
    Serial.println("  ⛔ DESTRUCTIVE: writes a FLASH slot.  This is the step the");
    Serial.println("     2026-08-20 bus reload no-acked; synctest never touched flash.");
    Serial.println("============================================================");
    g_r503.diag().reset();
    R503StoreTestResult r = g_r503.run_store_test(opt);

    Serial.printf("  template              : %u bytes (source: %s)%s\n",
                  (unsigned)r.template_size, r.size_source,
                  r.used_real_template ? "  [REAL template]" : "  [synthetic ramp]");
    Serial.printf("  target slot           : %d\n", (int)r.slot);
    Serial.printf("  inter-packet delay    : %u ms\n",
                  (unsigned)r.inter_packet_delay_ms);
    Serial.printf("  post-data SETTLE      : %u ms  <-- the knob under test\n",
                  (unsigned)r.settle_ms);
    Serial.printf("  end-packet framing    : %s\n", r503::to_string(r.style));
    Serial.printf("  DownChar ack          : %s (0x%02X)\n",
                  r.downchar_acked ? "OK" : "REFUSED/absent", r.down_conf);
    Serial.printf("  data packets sent     : %u\n", (unsigned)r.data_packets_sent);
    if (!r.post_data_bytes.empty())
        Serial.printf("  volunteered post-data : %s\n",
                      r503::hex(r.post_data_bytes, 24).c_str());
    Serial.printf("  ⭐ alive AFTER data ph : %s\n",
                  r.alive_after_data_phase
                      ? "YES — data phase terminated, module in command mode"
                      : "NO — still in receive phase (Store would be eaten as data)");
    Serial.printf("  Store cmd bytes       : %s\n", r503::hex(r.store_cmd_bytes).c_str());
    Serial.printf("  Store ack             : %s%s\n",
                  r.store_acked ? "OK" : "NO ACK",
                  r.store_acked ? "" : (r.store_timed_out ? " (timed out)" : ""));
    if (r.store_acked)
        Serial.printf("  Store confirmation    : 0x%02X\n", r.store_conf);
    if (r.readback_attempted)
        Serial.printf("  readback              : %s, %u bytes, byte-exact: %s\n",
                      r.readback_complete ? "complete" : "INCOMPLETE",
                      (unsigned)r.readback_len, r.byte_exact ? "YES" : "NO");
    r503_print_diag(r.diag);

    if (r.pass()) {
        Serial.println("\n  RESULT: PASS — a Store-to-flash round trip held, byte-exact. "
                       "The template-sync architecture is proven END TO END, on flash.");
    } else {
        Serial.printf("\n  RESULT: FAIL — %s\n", r.failure.c_str());
        if (!r.store_acked && r.alive_after_data_phase)
            Serial.println("  READ THIS: the module WAS alive after the data phase but "
                           "dropped Store — RAISE SETTLE: 'r503 storetest settle 400'.");
        else if (!r.store_acked && !r.alive_after_data_phase)
            Serial.println("  READ THIS: the module was WEDGED before Store even went "
                           "out — the data phase did not terminate.  Try 'r503 storetest "
                           "end' (A2 framing) and 'r503 unwedge'.");
    }
    Serial.println("============================================================\n");
}

/* Walk ANY single ratified scene, in the CURRENT flow (set with `r503 flow`).
 * The one-command way to show David each scene on the ring. */
static void r503_cmd_glow(const std::string& what) {
    if (!r503_ensure_open()) return;
    FeedbackState s;
    const char* scene;
    if      (what == "off"  || what == "idle")      { s = FeedbackState::IDLE;             scene = "off"; }
    else if (what == "ready"|| what == "armed"|| what.empty()) { s = FeedbackState::READY;  scene = "armed"; }
    else if (what == "wait" || what == "awaiting")  { s = FeedbackState::AWAITING_FINGER;  scene = "awaiting"; }
    else if (what == "capture")                     { s = FeedbackState::CAPTURE_ACCEPTED; scene = "capture blip"; }
    else if (what == "busy")                        { s = FeedbackState::BUSY;             scene = "processing (match)"; }
    else if (what == "enroll"|| what == "progress") { s = FeedbackState::ENROLL_PROGRESS;  scene = "processing (enroll)"; }
    else if (what == "accept"|| what == "success")  { s = FeedbackState::ACCEPT;           scene = "success"; }
    else if (what == "reject")                      { s = FeedbackState::REJECT_SILENT;    scene = "rejected"; }
    else if (what == "retry")                       { s = FeedbackState::CAPTURE_RETRY;    scene = "retry"; }
    else if (what == "fault"|| what == "error")     { s = FeedbackState::FAULT;            scene = "error"; }
    else {
        Serial.printf("[r503] glow: unknown scene '%s'.  scenes: off armed awaiting "
                      "capture busy enroll accept reject retry fault\n", what.c_str());
        return;
    }
    FingerprintHALFailure err;
    if (!g_r503.command_feedback(s, FpProgress{}, err)) {
        r503_print_failure("command_feedback", err);
        return;
    }
    Serial.printf("[r503] ring -> %s (flow=%s).  ⛔ NEEDS EYES: an AuraLedConfig ack "
                  "proves the command was ACCEPTED, never the colour or pattern that "
                  "actually lit.\n", scene,
                  g_r503.feedback_flow() == FeedbackFlow::ENROLL ? "enroll" : "match");
}

/* ENROLL flow — the ratified all-PURPLE arc.  set_feedback_flow(ENROLL) makes
 * AWAITING_FINGER + ACCEPT render purple; CAPTURE_ACCEPTED/ENROLL_PROGRESS are
 * already purple in both flows.  EVERY exit (success, fault, full) restores
 * MATCH flow and returns to armed — a count-then-stop scene leaves the ring
 * dark otherwise. */
static void r503_cmd_enroll(int explicit_slot) {
    if (!r503_ensure_open()) return;
    FingerprintHALFailure err;
    g_r503.set_feedback_flow(FeedbackFlow::ENROLL);
    const int required = g_r503.capabilities().captures_per_template;
    Serial.printf("[r503] ENROLL (purple arc): %d captures required (RegModel "
                  "combines CharBuffer1 + CharBuffer2).\n", required);

    /* ⛔ every terminal path returns to armed (blue, match flow). */
    auto rearm = [&err]() {
        g_r503.set_feedback_flow(FeedbackFlow::MATCH);
        (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, err);
    };
    auto fault_and_rearm = [&err, &rearm]() {
        (void)g_r503.command_feedback(FeedbackState::FAULT, FpProgress{}, err);
        delay(1500);   /* let the red strobe show */
        rearm();
    };

    for (int cap = 1; cap <= required; cap++) {
        (void)g_r503.command_feedback(FeedbackState::AWAITING_FINGER, FpProgress{}, err);
        Serial.printf("[r503] place finger (capture %d of %d)...\n", cap, required);
        const uint32_t t0 = millis();
        bool got = false;
        while (millis() - t0 < 20000) {
            if (g_r503.enroll_capture(cap, err)) { got = true; break; }
            if (err.axis != FailureAxis::SENSOR_REFUSAL) {
                r503_print_failure("capture", err); fault_and_rearm(); return;
            }
            delay(120);
        }
        if (!got) {
            Serial.println("[r503] capture TIMED OUT after 20s.");
            fault_and_rearm(); return;
        }
        (void)g_r503.command_feedback(FeedbackState::CAPTURE_ACCEPTED, FpProgress{}, err);
        delay(250);   /* let the purple blip show */
        Serial.printf("[r503] capture %d accepted.\n", cap);
        if (cap < required) {
            (void)g_r503.command_feedback(FeedbackState::ENROLL_PROGRESS, FpProgress{}, err);
            Serial.println("[r503] lift finger...");
            const uint32_t t1 = millis();
            while (g_r503.finger_present() && millis() - t1 < 10000) delay(100);
            delay(400);
        }
    }
    /* RegModel — processing (purple breathing) */
    (void)g_r503.command_feedback(FeedbackState::ENROLL_PROGRESS, FpProgress{}, err);
    if (!g_r503.enroll_combine(err)) {
        r503_print_failure("RegModel", err);
        Serial.println("[r503] (0x0A means the two captures were not the same finger.)");
        fault_and_rearm(); return;
    }
    const Slot slot = explicit_slot >= 0 ? explicit_slot : g_r503.first_free_slot();
    if (slot < 0) { Serial.println("[r503] library FULL — no free slot."); rearm(); return; }
    if (!g_r503.enroll_store(slot, err)) {
        r503_print_failure("Store", err);
        fault_and_rearm(); return;
    }
    /* CHANGE-2 (David 2026-08-24, confirmed SAME for enroll): blank the ring
     * (AURA_OFF/IDLE) after capture+RegModel completes and BEFORE the purple
     * success 3-flash, so the three flashes read distinctly instead of merging
     * into the preceding purple breathing/lit state. */
    (void)g_r503.command_feedback(FeedbackState::IDLE, FpProgress{}, err);
    delay(180);
    (void)g_r503.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err);  /* purple 3-flash success */
    Serial.printf("[r503] ENROLLED into slot %d (purple success).  Now: 'r503 match'.\n", slot);

    /* If readback works, the whole sync architecture is proven end to end on
     * real biometric data. */
    TemplateBytes t;
    if (g_r503.read_template(slot, t, err))
        Serial.printf("[r503] UpChar of the JUST-ENROLLED template: %u bytes — "
                      "template sync is viable on REAL data, not just a ramp.\n",
                      (unsigned)t.size());
    else
        r503_print_failure("read_template (post-enroll)", err);
    delay(1500);   /* let the success 3-flash play */
    rearm();
}

/* MATCH flow — the ratified armed -> capture-blip -> process -> result ->
 * armed sequence.  command_feedback calls sit at each transition boundary;
 * THIS is where the sequencing lives (the HAL never writes the ring).  We are
 * POLLED (D4, WAKEUP deferred), so the capture blip is boundary-set at the
 * instant a finger is first detected.  The dwell()s let each scene play before
 * the next command overrides it — glass-tunable. */
static void r503_cmd_match() {
    if (!r503_ensure_open()) return;
    FingerprintHALFailure err;
    g_r503.set_feedback_flow(FeedbackFlow::MATCH);
    (void)g_r503.command_feedback(FeedbackState::AWAITING_FINGER, FpProgress{}, err);
    Serial.println("[r503] armed (blue steady) — place finger (10s)...");

    const uint32_t t0 = millis();
    bool finger = false;
    while (millis() - t0 < 10000) {
        if (g_r503.finger_present()) { finger = true; break; }  /* GenImg poll */
        delay(120);
    }
    if (!finger) {
        Serial.println("[r503] no finger within 10s — returning to armed.");
        (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, err);
        return;
    }

    /* capture instant: the purple "I'm reading you" blip, then blue breathing
     * while the Search runs.  (The poll above is the trigger; match() below
     * re-captures + searches — a bring-up simplification, one extra GenImg.) */
    (void)g_r503.command_feedback(FeedbackState::CAPTURE_ACCEPTED, FpProgress{}, err);
    delay(250);
    (void)g_r503.command_feedback(FeedbackState::BUSY, FpProgress{}, err);

    MatchResult m = g_r503.match();
    if (m.outcome == MatchOutcome::NO_FINGER) {
        Serial.println("[r503] finger lifted before the read completed — re-arming.");
    } else if (m.outcome == MatchOutcome::MATCHED) {
        Serial.printf("[r503] MATCHED slot=%d score=%d — GRANTED (blue 3-flash)\n",
                      m.slot.value(), m.score.value());
        /* CHANGE-2: OFF (AURA_OFF/IDLE) before the terminal 3-flash. */
        (void)g_r503.command_feedback(FeedbackState::IDLE, FpProgress{}, err);
        delay(180);
        (void)g_r503.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err);
        delay(1500);   /* let the 3-flash terminal scene play */
    } else {
        /* [CXC-FPID-RING-HIDES-DENIAL]: unrecognized and denied are the SAME
         * red 3-flash — the ring is not a mirror of the bus auth_event. */
        Serial.println("[r503] NO MATCH — REJECTED (red 3-flash; denied ≡ unrecognized)");
        /* CHANGE-2: OFF (AURA_OFF/IDLE) before the terminal 3-flash. */
        (void)g_r503.command_feedback(FeedbackState::IDLE, FpProgress{}, err);
        delay(180);
        (void)g_r503.command_feedback(FeedbackState::REJECT_SILENT, FpProgress{}, err);
        delay(1500);
    }
    r503_print_diag(g_r503.diag());
    /* ⛔ return to armed — a count-then-stop terminal scene leaves the ring dark. */
    (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, err);
    Serial.println("[r503] returned to armed (blue).");
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
    if (rest.rfind("dump", 0) == 0) {
        int slot = 1;
        const size_t sp = rest.find(' ');
        if (sp != std::string::npos) slot = atoi(rest.c_str() + sp + 1);
        r503_cmd_dump((Slot)slot);
        return;
    }
    if (rest == "match")   { r503_cmd_match(); return; }
    if (rest == "unwedge") { r503_cmd_unwedge(); return; }
    if (rest.rfind("flow", 0) == 0) {
        const std::string a = rest.size() > 4 ? rest.substr(5) : std::string();
        if (a == "enroll") {
            g_r503.set_feedback_flow(FeedbackFlow::ENROLL);
            Serial.println("[r503] feedback flow = ENROLL (awaiting + success render PURPLE)");
        } else if (a == "match") {
            g_r503.set_feedback_flow(FeedbackFlow::MATCH);
            Serial.println("[r503] feedback flow = MATCH (awaiting + success render BLUE)");
        } else {
            Serial.printf("[r503] flow is %s; set with 'r503 flow match' / 'r503 flow enroll'\n",
                          g_r503.feedback_flow() == FeedbackFlow::ENROLL ? "enroll" : "match");
        }
        return;
    }
    if (rest.rfind("synctest", 0) == 0) {
        const std::string arg = rest.size() > 8 ? rest.substr(9) : std::string();
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
    if (rest.rfind("storetest", 0) == 0) {
        const std::string arg = rest.size() > 9 ? rest.substr(10) : std::string();
        R503StoreTestOptions opt;
        size_t i = 0;
        auto next_int = [&arg, &i]() -> int {
            while (i < arg.size() && arg[i] == ' ') i++;
            size_t k = arg.find(' ', i);
            if (k == std::string::npos) k = arg.size();
            const int v = atoi(arg.substr(i, k - i).c_str());
            i = k;
            return v;
        };
        while (i < arg.size()) {
            while (i < arg.size() && arg[i] == ' ') i++;
            size_t j = arg.find(' ', i);
            if (j == std::string::npos) j = arg.size();
            const std::string tok = arg.substr(i, j - i);
            i = j;
            if (tok.empty()) continue;
            if      (tok == "end")    opt.style = r503::EndPacketStyle::SEPARATE_EMPTY_END;
            else if (tok == "noread") opt.readback = false;
            else if (tok == "settle") opt.settle_ms = (uint32_t)next_int();
            else if (tok == "delay")  opt.inter_packet_delay_ms = (uint32_t)next_int();
            else if (tok == "slot")   opt.slot = (Slot)next_int();
            else if (tok == "real")   opt.real_src_slot = (Slot)next_int();
            else if (tok == "buf") {
                const int b = next_int();
                opt.buffer_id = (uint8_t)(b >= 1 && b <= 6 ? b : 1);
            }
        }
        r503_cmd_storetest(opt);
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
    if (rest == "announce") {   /* re-publish the retained device announce (glass) */
        if (!g_connected) { Serial.println("[r503] announce needs a live MQTT connection."); return; }
        publishDeviceAnnounce();
        return;
    }
    if (rest.rfind("busenroll", 0) == 0) {
        /* Drive the BUS-enroll ceremony from serial, for glass — as if a
         * <dev>/cmd/enroll arrived.  loop() runs it (needs g_connected for the
         * store PUT + result publish). */
        std::string a = rest.size() > 9 ? rest.substr(10) : std::string();
        std::string person = "glass-person"; int ordinal = 1;
        const size_t sp = a.find(' ');
        if (!a.empty()) {
            person = (sp == std::string::npos) ? a : a.substr(0, sp);
            if (sp != std::string::npos) ordinal = atoi(a.c_str() + sp + 1);
        }
        if (g_enroll.active) { Serial.println("[r503] a bus enroll is already active."); return; }
        g_enroll.active = true; g_enroll.cancel_requested = false;
        g_enroll.person = person; g_enroll.ordinal = ordinal;
        g_enroll.request_id = std::string("serial-") + genOpaqueId().substr(0, 8);
        Serial.printf("[r503] busenroll queued: person=%s ordinal=%d (loop runs it; needs MQTT+store)\n",
                      person.c_str(), ordinal);
        return;
    }
    Serial.println("[r503] commands:");
    Serial.println("  ping                    VfyPwd handshake");
    Serial.println("  info                    ReadSysPara + ReadProdInfo + occupancy");
    Serial.println("  extract [slot=1]        ⭐ LoadChar+UpChar, NO DownChar — the");
    Serial.println("                          discriminator for a SILENT UpChar");
    Serial.println("  dump [slot=1]           READ-ONLY LoadChar+UpChar -> base64");
    Serial.println("                          frame (sha256-stamped) for host capture");
    Serial.println("  synctest [opts]         ⭐ DownChar->UpChar round trip");
    Serial.println("     opts: end        A2 sweep (payload 0x02 + separate empty 0x08)");
    Serial.println("           delay <ms> inter-packet pacing sweep");
    Serial.println("           buf <1-6>  char buffer      quiet  no per-packet trace");
    Serial.println("           noretry    show the RAW failure, no empty-end rescue");
    Serial.println("  storetest real <src>    ⭐ DESTRUCTIVE — re-Store a REAL template");
    Serial.println("                          (LoadChar<src>->Store->readback), TRACED");
    Serial.println("     opts: settle <ms> post-data settle (THE fix knob; default 200)");
    Serial.println("           delay <ms>  inter-packet   slot <n>   end   noread");
    Serial.println("  unwedge                 send a bare empty PID 0x08 + probe");
    Serial.println("  --- UX ring feedback (needs eyes on the ring) ---");
    Serial.println("  flow <match|enroll>     set the render flow (awaiting/success");
    Serial.println("                          BLUE in match, PURPLE in enroll)");
    Serial.println("  glow <scene>            walk ONE ratified scene: off armed");
    Serial.println("       awaiting capture busy enroll accept reject retry fault");
    Serial.println("  enroll [slot] | match   full flow with the ring sequence");
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
#ifdef TM1637_PANEL
    } else if (line == "tm1637 selftest" || line == "tm1637") {
        /* Isolation diagnostic: drive all four tubes directly, bypassing the
         * bus/throttle data path, and report per-tube ACK. Distinguishes a
         * shared-CLK/ground/protocol fault from a bus-data-path fault. */
        tm1637_panel_selftest(0);
    } else if (line.rfind("tm1637 selftest ", 0) == 0) {
        /* Single-tube: `tm1637 selftest N`, N in 1..4 -> drive ONLY tube N (one
         * module's worth of current, for the 3.3 V LDO). Out-of-range -> usage. */
        const int n = atoi(line.c_str() + 16);
        if (n >= 1 && n <= 4) {
            tm1637_panel_selftest(n);
        } else {
            Serial.printf("[tm1637] usage: 'tm1637 selftest' (all four) or "
                          "'tm1637 selftest N' with N in 1..4 (single tube). Got \"%s\".\n",
                          line.c_str() + 16);
        }
    } else if (line == "tm1637 wiggle") {
        /* Multimeter aid: slowly toggle tube-1's CLK (GPIO7) + DIO (GPIO8)
         * PUSH-PULL at 1 Hz so a human can meter both ends. */
        tm1637_panel_wiggle();
#endif
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

/* --- OFFLINE demo: FP_ALLOW_MOCK ONLY (Z — scripted UX/flow demo). --------
 * [DEC-R503-MOCK-STRUCTURAL-GUARD]: the offline demo installs synthetic
 * templates into the FUNCTIONAL mock and scripts granted/denied matches — it
 * is the whole reason the mock CAN grant.  A PRODUCTION build (no
 * FP_ALLOW_MOCK) does not compile g_hal, so the demo cannot exist there; the
 * production offline path (below) announces the device as inoperable instead
 * of ever pretending a mock finger was granted. */
#ifdef FP_ALLOW_MOCK
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
#else  /* !FP_ALLOW_MOCK — PRODUCTION: no mock, no scripted demo. */
static void runOfflineDemo() {
    /* [DEC-R503-MOCK-STRUCTURAL-GUARD] A production build has no functional
     * mock to demo against.  Offline with no confirmed sensor, the device is
     * INOPERABLE by construction (bound to the NULL HAL, not confirmed-live):
     * emit one heartbeat so the wire says so, and never fabricate a match. */
    Serial.println("\n--- OFFLINE (production build): no mock HAL compiled in. ---");
    HeartbeatState h = g_dev->emit_heartbeat();
    Serial.printf("[hb] state=%s — the NULL HAL can never arm/grant; wire 'r503 "
                  "ping' to promote to the real sensor.\n",
                  h == HeartbeatState::INOPERABLE ? "INOPERABLE" :
                  h == HeartbeatState::PUSH_NEEDED ? "PUSH_NEEDED" : "READY");
}
#endif /* FP_ALLOW_MOCK */

/* GAP-002 — the bus-driven enroll ceremony, run from loop() (out of the MQTT
 * callback, so the ~40s ceremony never blocks the callback).  Reuses the r503
 * enroll ceremony (2 captures, RegModel, Store, DEVICE-allocated slot — the
 * command carries no slot), the purple enroll ring arc, publishes retained
 * <dev>/status/enroll progress, honours cmd/enroll/cancel, and on success reads
 * the template back, uploads it to the store, publishes result/enroll/complete
 * with the fingerprint:// REF (never raw bytes), and binds the slot in the
 * device bridge (persisted). */
static void runBusEnroll() {
    const std::string person = g_enroll.person;
    const int ordinal = g_enroll.ordinal;
    const std::string request_id = g_enroll.request_id;

    FingerprintHALFailure err;
    auto rearm_match = [&err]() {
        g_r503.set_feedback_flow(FeedbackFlow::MATCH);
        (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, err);
    };
    auto terminal = [&](const char* phase) {
        publishEnrollStatus(phase, ordinal, 0, 0);
        rearm_match();
        g_enroll = PendingEnroll{};   /* clear the pending request */
    };
    auto fault = [&](const char* phase) {
        (void)g_r503.command_feedback(FeedbackState::FAULT, FpProgress{}, err);
        delay(400);
        terminal(phase);
    };

    Serial.printf("[enroll] BUS ENROLL start person=%s ordinal=%d request_id=%s\n",
                  person.c_str(), ordinal, request_id.c_str());
    if (!r503_ensure_open()) { Serial.println("[enroll] sensor not open."); terminal("failed"); return; }

    g_r503.set_feedback_flow(FeedbackFlow::ENROLL);
    const int required = g_r503.capabilities().captures_per_template;

    for (int cap = 1; cap <= required; cap++) {
        (void)g_r503.command_feedback(FeedbackState::AWAITING_FINGER, FpProgress{}, err);
        publishEnrollStatus("capturing", ordinal, cap, required);
        Serial.printf("[enroll] place finger (capture %d of %d)...\n", cap, required);
        const uint32_t t0 = millis();
        bool got = false;
        while (millis() - t0 < 20000) {
            g_mqtt.loop();                       /* keepalive + receive cancel */
            if (g_enroll.cancel_requested) { Serial.println("[enroll] CANCELLED."); fault("cancelled"); return; }
            if (g_r503.enroll_capture(cap, err)) { got = true; break; }
            if (err.axis != FailureAxis::SENSOR_REFUSAL) {
                r503_print_failure("capture", err); fault("failed"); return;
            }
            delay(120);
        }
        if (!got) { Serial.println("[enroll] capture TIMED OUT."); fault("failed"); return; }
        (void)g_r503.command_feedback(FeedbackState::CAPTURE_ACCEPTED, FpProgress{}, err);
        delay(250);
        if (cap < required) {
            (void)g_r503.command_feedback(FeedbackState::ENROLL_PROGRESS, FpProgress{}, err);
            Serial.println("[enroll] lift finger...");
            const uint32_t t1 = millis();
            while (g_r503.finger_present() && millis() - t1 < 10000) { g_mqtt.loop(); delay(100); }
            delay(400);
        }
    }

    (void)g_r503.command_feedback(FeedbackState::ENROLL_PROGRESS, FpProgress{}, err);
    publishEnrollStatus("processing", ordinal, required, required);
    if (!g_r503.enroll_combine(err)) {
        r503_print_failure("RegModel", err);
        Serial.println("[enroll] (0x0A means the two captures were not the same finger.)");
        fault("failed"); return;
    }
    const Slot slot = g_r503.first_free_slot();
    if (slot < 0) { Serial.println("[enroll] library FULL."); fault("failed"); return; }
    if (!g_r503.enroll_store(slot, err)) { r503_print_failure("Store", err); fault("failed"); return; }

    TemplateBytes t;
    if (!g_r503.read_template(slot, t, err)) { r503_print_failure("read_template (enroll)", err); fault("failed"); return; }
    const std::string ref = uploadTemplate(t);
    if (ref.empty()) { Serial.println("[enroll] template upload FAILED."); fault("failed"); return; }

    /* Bind the device bridge so a subsequent match resolves the person.  The
     * enroll command carries NO character/rbac (only person/ordinal), so those
     * fields stay empty until a roster push supplies the cast; then receive_push
     * rebuilds the bridge with them. */
    g_nvs.bridge[slot] = BridgeEntry{ person, ordinal, std::string(), std::string() };
    nvs_persist_if_changed();

    (void)g_r503.command_feedback(FeedbackState::ACCEPT, FpProgress{}, err);  /* purple 3-flash */
    publishEnrollComplete(person, ordinal, ref, request_id);
    Serial.printf("[enroll] COMPLETE person=%s ordinal=%d slot=%d ref=%s\n",
                  person.c_str(), ordinal, slot, ref.c_str());
    delay(1500);
    terminal("complete");
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

#ifdef TM1637_PANEL
    /* Bring up the tube panel FIRST — it is independent of transport and of the
     * finger path, and starting it before WiFi means the tubes are alive
     * (blank, awaiting values) the moment the board boots. */
    tm1637_panel_setup();
#endif

    /* Transport is a CONFIG VALUE (NVS, default OFFLINE). */
    const TransportSelection& sel = bus_transport_selection();
    Serial.printf("[boot] transport: %s (from %s; known=%s; needs_network=%s)\n",
                  sel.name.c_str(), sel.source.c_str(),
                  sel.known ? "yes" : "no", sel.needs_network ? "yes" : "no");
    Serial.printf("[boot] known transports: %s\n", transport_known_names());

    g_online = sel.known && sel.name == "mqtt";   /* INTENT — never flipped to the
                                                   * offline demo on a failed link
                                                   * (GAP-009); loop() keeps trying. */

    /* Bind the bus path to the REAL R503 when a sensor answers, else the mock —
     * INDEPENDENT of WiFi/MQTT, so a flaky link never demotes a present sensor to
     * the mock, and the announce carries the REAL capabilities. */
    if (g_online) {
        if (r503_ensure_open()) {
            g_device_hal = &g_r503;
            Serial.println("[boot] bus path bound to the REAL R503 (RealR503HAL) — a "
                           "template_set push / bus enroll drives the physical sensor.");
        } else {
            /* No sensor answered.  [DEC-R503-MOCK-STRUCTURAL-GUARD]: a PRODUCTION
             * build binds the INOPERABLE null HAL (never the mock — the mock is
             * not even compiled here), so the board joins the bus and announces
             * fingerprint_inoperable rather than impersonating an armed sensor.
             * 'r503 ping' + live-rebind promotes to the real HAL when wired. */
#ifdef FP_ALLOW_MOCK
            g_device_hal = &g_hal;
            Serial.println("[boot] no R503 answered r503_ensure_open() — FP_ALLOW_MOCK: "
                           "the bus path stays on the FUNCTIONAL MOCK HAL (bench/dev).  "
                           "'r503 ping' + 'restart' rebinds once the sensor is wired.");
#else
            g_device_hal = &g_null;
            Serial.println("[boot] no R503 answered r503_ensure_open() — bus path bound "
                           "to the INOPERABLE NULL HAL (production).  The board joins the "
                           "bus and announces fingerprint_inoperable; 'r503 ping' rebinds "
                           "to the real sensor once it is wired.");
#endif
        }
    }

    /* [CXC-FPID-DEVICE-BRIDGE] restore the persisted bridge BEFORE any heartbeat/
     * match path can run — else a reboot misreads enrolled crew as intruders. */
    nvs_load_bridge();

    if (g_online) {
        Serial.println("[boot] transport=mqtt: bringing up WiFi + MQTT (bounded retry).");
        mqttInit();
        const int kAttempts = 3;
        for (int i = 1; i <= kAttempts && !g_connected; i++) {
            Serial.printf("[boot] connect attempt %d/%d...\n", i, kAttempts);
            if (connectWiFiBlocking(10000) && mqttConnectOnce()) { g_connected = true; break; }
            if (i < kAttempts) { Serial.printf("[boot] backoff %ds...\n", i); delay(1000 * i); }
        }
        if (!g_connected)
            Serial.println("[boot] initial connect FAILED — staying in ONLINE intent; "
                           "loop() keeps retrying (NOT the offline demo).  The ring + "
                           "r503 commands work regardless.");
    } else if (sel.needs_network) {
        Serial.printf("[boot] WARNING: selected transport '%s' wants a network and "
                      "this build only implements mqtt — treating as offline.\n",
                      sel.name.c_str());
    } else {
        Serial.println("[boot] offline: no network brought up (by design).");
    }

    g_dev = new FingerprintDevice(
        g_device_hal, g_online ? static_cast<FingerprintBusSink*>(&g_mqtt_bus)
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

    /* CHANGE-1(a) FAIL-LOUD + D9 STRUCTURAL GUARD — who is confirmed-live?
     * Only the REAL R503 is EVER confirmed-live.  The inoperable NULL HAL
     * (production default) never is, so a sensorless production board announces
     * fingerprint_inoperable, never armed/loaded ([DEC-R503-MOCK-STRUCTURAL-GUARD],
     * DEFECT-ARMED-ROSTER-LOADED-R503-DEVICE-78C389).
     * Under FP_ALLOW_MOCK the ORIGINAL D8 rule is preserved exactly: the
     * offline demo legitimately runs confirmed on the mock, while an ONLINE
     * board still stranded on the mock is marked NOT confirmed (fail-loud). */
    const bool on_real = (g_device_hal == &g_r503);
#ifdef FP_ALLOW_MOCK
    const bool confirmed = !(g_online && g_device_hal != &g_r503);
#else
    const bool confirmed = on_real;   /* null HAL is never confirmed-live */
#endif
    g_dev->set_hal_confirmed_live(confirmed);
    if (!confirmed) {
        Serial.println("[boot] ⛔ bound to a NON-REAL HAL — device marked NOT confirmed "
                       "live; it will announce fingerprint_inoperable (never "
                       "armed/loaded) until 'r503 ping' rebinds the real sensor.");
    }

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

/* Heartbeat gated on the STATE-DEPENDENT interval (GAP-005): READY 60s, else
 * 15s — read from g_dev->heartbeat_interval() after each emit, never a fixed 5s. */
static void tickHeartbeat() {
    static uint32_t last_hb = 0;
    static double hb_interval_s = 15.0;
    if (millis() - last_hb < (uint32_t)(hb_interval_s * 1000.0)) return;
    last_hb = millis();
    HeartbeatState s = g_dev->emit_heartbeat();
    hb_interval_s = g_dev->heartbeat_interval();   /* recompute for the NEXT tick */
    Serial.printf("[tick] uptime=%lus heartbeat=%s (next in %.0fs)\n",
                  (unsigned long)(millis() / 1000),
                  s == HeartbeatState::READY ? "ready" :
                  s == HeartbeatState::PUSH_NEEDED ? "push_needed" : "inoperable",
                  hb_interval_s);
}

void loop() {
    pollSerialCommands();

#ifdef TM1637_PANEL
    /* LIVE tube path, ABOVE every early return below so it runs whether online
     * or offline and even during the enroll/match early-returns.  flush() is the
     * low-latency primary: it writes any tube a bus arrival marked dirty, showing
     * the LATEST value (intermediate throttle values coalesce, never replay).
     * service() is the 1 Hz safety-net repaint only — it does not gate flush. */
    tm1637_panel_flush();
    tm1637_panel_service();
#endif

    if (g_online) {
        /* --- GAP-009: WiFi + MQTT robustness, throttled + non-blocking --- */
        static uint32_t last_wifi_try = 0, last_mqtt_try = 0;
        if (WiFi.status() != WL_CONNECTED) {
            g_connected = false;
            if (millis() - last_wifi_try >= 10000) {
                last_wifi_try = millis();
                Serial.println("[wifi] not connected — WiFi.reconnect() (non-blocking)");
                WiFi.reconnect();
            }
        } else if (!g_mqtt.connected()) {
            g_connected = false;
            if (millis() - last_mqtt_try >= 5000) {
                last_mqtt_try = millis();
                Serial.println("[mqtt] disconnected — one reconnect attempt");
                g_connected = mqttConnectOnce();
            }
        } else {
            g_connected = true;
        }

        if (g_connected) {
            g_mqtt.loop();

            /* --- GAP-002: a queued bus enroll runs to completion HERE --- */
            if (g_enroll.active) { runBusEnroll(); return; }

            /* --- GAP-001: the device CONTINUOUSLY watches, auto-authenticates,
             * and publishes its own auth_event ([CXC-FPBUS-MSG-MATCH-HAS-NO-
             * COMMAND]).  "armed/blue" MEANS this poll is live. --- */
            const bool real = (g_device_hal == &g_r503);
            static bool ring_armed = false;
            if (real && !ring_armed) {
                FingerprintHALFailure e;
                g_r503.set_feedback_flow(FeedbackFlow::MATCH);
                (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, e);
                ring_armed = true;
            }
            static uint32_t last_poll = 0;
            if (millis() - last_poll >= 100) {
                last_poll = millis();
                std::optional<AuthOutcome> outcome = g_dev->poll_match();
                if (outcome.has_value()) {
                    Serial.printf("[match] outcome=%s (attempts=%d)\n",
                                  to_string(*outcome),
                                  g_dev->count_authentication_attempts());
                    nvs_persist_if_changed();   /* attempt count persists (C71) */
                    if (real) {
                        FingerprintHALFailure e;
                        FeedbackState s = (*outcome == AuthOutcome::GRANTED)
                                              ? FeedbackState::ACCEPT
                                              : FeedbackState::REJECT_SILENT;
                        /* CHANGE-2 (David 2026-08-24): blank the ring (AURA_OFF,
                         * the ratified IDLE control mode) after capture so the
                         * terminal blue/red 3-flash reads as THREE distinct
                         * flashes instead of merging into the lit armed/breathing
                         * state that preceded it. */
                        (void)g_r503.command_feedback(FeedbackState::IDLE, FpProgress{}, e);
                        delay(180);
                        (void)g_r503.command_feedback(s, FpProgress{}, e);
                        delay(1200);   /* let the 3-flash play (glass-tunable) */
                        /* debounce: wait for the finger to lift before re-arming */
                        uint32_t t = millis();
                        while (g_r503.finger_present() && millis() - t < 5000) {
                            g_mqtt.loop(); delay(100);
                        }
                        (void)g_r503.command_feedback(FeedbackState::READY, FpProgress{}, e);
                    }
                }
            }
            tickHeartbeat();
        }
    } else {
        tickHeartbeat();   /* offline demo: interval-gated heartbeat only */
    }
    delay(50);
}

#endif /* FINGERPRINT_BRINGUP */
