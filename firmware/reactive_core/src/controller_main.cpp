/*
 * controller_main.cpp — the config-FETCHED headless controller shell.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-DEVICE] [CXC-ER-BOARDS] [CXC-R-BOOT].
 *
 * The generic reactive_core firmware, config-driven end to end: it boots,
 * connects WiFi/MQTT, FETCHES its .compose.yaml from the bus_storage asset
 * store by device id (reusing cyd/src/config_fetch BY REFERENCE), lowers the
 * SAME file's `hookup:` + `audio_cover:` blocks into a runtime device model
 * (controller_config, via cyd/src/yaml_min), and self-configures — NO baked
 * board profile, NO baked pins.  The dual-throttle C3 is the reference unit:
 *
 *   PHYSICAL half  — read the ADC pots the hookup wires declare and PUBLISH
 *     each on its slot namespace (throttle/left <- GPIO0, throttle/right <-
 *     GPIO1; continuous_0_255) so the calibration app + world-app helm see
 *     the live thrust.
 *   S6 AUDIO half  — a PURE bus SUBSCRIBER + DY-SV5F UART actuator: subscribe
 *     dradis/contacts + game/jump, derive the two leaf edges (contact
 *     unknown->hostile; game/jump->"jumping"), play the mapped track over
 *     UART.  NEVER publishes (audio_cover.h == composeio.maker.audio_cover).
 *
 * Only [env:esp32c3] defines CONTROLLER_CONFIG_FETCH, so this file is the
 * whole shell there; main.cpp (the R1/R2 demo + actuator tier) is compiled
 * out under the same flag.  Other envs keep main.cpp.
 *
 * Wire format: BusMessage JSON over composeio/{scope}/{namespace}
 * (python/composeio/transport.py), same as main.cpp.
 */

#ifdef CONTROLLER_CONFIG_FETCH

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <esp_system.h>          /* esp_restart() for the remote-reload seam */
#include <string>
#include <vector>

#include "config_fetch.h"        /* cyd/src via -I: device_id(), config_fetch() */
#include "controller_config.h"
#include "signal_conditioning.h"
#include "relay_actuator.h"      /* break-before-make relay bank ([CXC-HAL-ACTUATE]) */
#include "restart.h"             /* cyd/src via -I: remote config-reload ([CXC-ER-REMOTE-RESTART]) */
#include "audio_cover.h"
#include "bus_binding.h"         /* cyd/src via -I: the ComposeBus repoint binding ([SPEC:CXC-REPOINT]) */
#include "bus_uri.h"             /* split the active mqtt:// URI into host+port for PubSubClient */

/* --- Configuration (creds injected at flash; see platformio.ini) --------- */
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
#ifndef MQTT_BROKER
#define MQTT_BROKER "mqtt-broker.local"
#endif
#ifndef MQTT_PORT
#define MQTT_PORT 1883
#endif
#ifndef CIO_TOPIC_PREFIX
#define CIO_TOPIC_PREFIX "composeio"
#endif

/* --- Globals ------------------------------------------------------------- */
static WiFiClient espClient;
static PubSubClient mqtt(espClient);
static ControllerConfig g_cfg;
static RelayActuator g_relay;

/* Audio cover standing state (successive-frame edge detectors). */
static audio_cover::ContactIdentificationDetector g_contacts;
static audio_cover::JumpDetector g_jump;

/* Per-sensor sampling state (parallel to g_cfg.sensors). */
static std::vector<uint32_t> g_next_sample;
/* Per-sensor conditioning state (EMA accumulator + last-emitted), owned
 * here and threaded through the pure condition_sample(). */
static std::vector<ConditionState> g_cond;

static char moduleId[24] = "cio-ctrl-c3";
static bool wifiConnected = false;
static bool configReady = false;
static unsigned long lastReconnect = 0;
static const unsigned long kReconnectMs = 5000;
static uint32_t seq = 0;

/* [CXC-REPOINT] the ACTIVE broker, resolved at boot from bus_binding (per-device
 * > global > compiled-IP floor), split into host+port for PubSubClient.  Held
 * at file scope because PubSubClient::setServer stores the host POINTER, not a
 * copy, so the buffer must outlive setup().  MQTT_BROKER/MQTT_PORT #defines are
 * now the rescue floor alone (resolved THROUGH bus_binding), not read directly. */
static char     mqttHost[64] = "";
static uint16_t mqttPort = MQTT_PORT;

/* --- Publish a numeric slot value (BusMessage JSON) ---------------------- */
static void publishNumber(const char* ns, double value) {
    if (!mqtt.connected()) return;
    StaticJsonDocument<192> d;
    d["ns"] = ns;
    d["type"] = "state";
    d["val"] = value;
    d["src"] = moduleId;
    d["ts"] = millis() / 1000.0;
    d["scope"] = "global";
    d["seq"] = ++seq;
    char payload[192];
    serializeJson(d, payload, sizeof(payload));
    char topic[96];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    /* RETAINED: a numeric slot is a DURABLE CURRENT FACT ("the lever is at
     * 255"), so a late-joining subscriber must be able to READ it rather
     * than wait for the next change.  Without retain, a value the deadband
     * is legitimately suppressing is INVISIBLE to the whole bus — which is
     * why diagnosing this channel required a bridge log instead of a
     * one-line broker query.  A state namespace whose retained value cannot
     * be read is not really state. */
    mqtt.publish(topic, payload, /*retained=*/true);
}

/* --- Publish a string state (BusMessage JSON) — restart ack seam --------- */
static void publishString(const char* ns, const char* value) {
    if (!mqtt.connected()) return;
    StaticJsonDocument<192> d;
    d["ns"] = ns;
    d["type"] = "state";
    d["val"] = value;
    d["src"] = moduleId;
    d["ts"] = millis() / 1000.0;
    d["scope"] = "global";
    d["seq"] = ++seq;
    char payload[192];
    serializeJson(d, payload, sizeof(payload));
    char topic[96];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    /* ⛔ NOT RETAINED, deliberately.  This seam carries the restart ACK —
     * a MOMENT, not a durable fact.  A retained ack would be replayed to
     * every future subscriber forever, so each new joiner would be told the
     * device had just restarted.  Retain is for "what IS", never for "what
     * HAPPENED": the same reasoning that makes publishNumber retained makes
     * this one not. */
    mqtt.publish(topic, payload, /*retained=*/false);
}

/* --- Reboot seam ([CXC-ER-REMOTE-RESTART]): flush the ack + hard reset,
 * which re-runs the boot config-fetch and loads the freshly-PUT config.
 * On real hardware this never returns. */
static void rebootNow() {
    Serial.println("[CXC-ER-REMOTE-RESTART] restart command honored — "
                   "esp_restart() (re-runs boot config-fetch)");
    Serial.flush();
    delay(50);
    esp_restart();
}

/* --- Relay bank drive sink: a physical GPIO level (polarity pre-applied) -- */
static void relaySink(void*, int pin, bool level) {
    digitalWrite(pin, level ? HIGH : LOW);
}

/* --- DY-SV5F: write the "play track" UART frame -------------------------- */
static void playTrack(int index) {
    if (index <= 0 || !g_cfg.audio.present || g_cfg.audio.tx_pin < 0) return;
    uint8_t buf[audio_cover::DYSV5F_FRAME_LEN];
    if (!audio_cover::dysv5f_play_track(index, buf)) return;
    Serial1.write(buf, sizeof(buf));   /* pure actuator — never publishes */
    Serial.printf("DY-SV5F <- play track %d  [AA 07 02 %02X %02X %02X]\n",
                  index, buf[3], buf[4], buf[5]);

    /* --- BUSY diagnostic: after firing the frame, watch the BUSY line for
     * ~1.2s and report whether the module REACTED.  Polarity-agnostic: we
     * report the level before + whether it CHANGED (a change == the module
     * received the command and started/stopped playback).  No change == the
     * module never acted on the frame (wrong mode/protocol, missing file, or
     * the RXD wire isn't landing). */
    if (g_cfg.audio.busy_pin >= 0) {
        const int before = digitalRead(g_cfg.audio.busy_pin);
        int changed = 0, hi = 0, lo = 0;
        const unsigned long t0 = millis();
        while (millis() - t0 < 1200) {
            const int b = digitalRead(g_cfg.audio.busy_pin);
            if (b) hi++; else lo++;
            if (b != before) changed = 1;
            delay(4);
        }
        Serial.printf("BUSY(GPIO%d) before=%d  window[hi=%d lo=%d]  -> %s\n",
                      g_cfg.audio.busy_pin, before, hi, lo,
                      changed ? "REACTED (module acted on the frame)"
                              : "NO REACTION (module did not act)");
    }
}

/* --- Route a subscribed bus message to the audio cover ------------------- */
static void routeAudio(const char* ns, JsonDocument& doc) {
    AudioCoverConfig& a = g_cfg.audio;
    if (!a.present) return;

    if (!a.contacts_ns.empty() && a.contacts_ns == ns) {
        /* dradis/contacts: a LIST of contact dicts (the scalar path can't
         * carry this — that is exactly why the controller shell decodes it
         * here rather than through the reactive core). */
        std::vector<audio_cover::Contact> contacts;
        JsonArrayConst arr = doc["val"].as<JsonArrayConst>();
        for (JsonObjectConst o : arr) {
            audio_cover::Contact c;
            c.id = o["id"].as<String>().c_str();   /* stringifies id number */
            c.allegiance = o["allegiance"] | "";
            contacts.push_back(c);
        }
        int fired = g_contacts.update(contacts);
        for (int i = 0; i < fired; i++) playTrack(a.condition_one_track);
    } else if (!a.jump_ns.empty() && a.jump_ns == ns) {
        const char* v = doc["val"] | "";
        if (g_jump.update(std::string(v))) playTrack(a.jump_track);
    }
}

/* --- Inbound MQTT -------------------------------------------------------- */
static void mqttCallback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<2048> doc;
    if (deserializeJson(doc, payload, length) != DeserializationError::Ok) {
        Serial.printf("JSON parse error on %s\n", topic);
        return;
    }
    const char* src = doc["src"] | "";
    const bool self = (strcmp(src, moduleId) == 0);

    const char* ns = doc["ns"] | "";
    char nsbuf[96];
    if (!*ns) {
        const char* p = strchr(topic, '/');
        p = p ? strchr(p + 1, '/') : nullptr;
        if (!p) return;
        snprintf(nsbuf, sizeof(nsbuf), "%s", p + 1);
        ns = nsbuf;
    }

    /* [CXC-REPOINT-CONFIRM] the broker round-trip echo — with ONE scoped
     * exception to the self-echo loop guard.  The commit-confirmed broker plane
     * REQUIRES the device to receive its OWN confirm publication fanned back by
     * the broker (that round-trip is the proof of a LIVE, fanning broker, not a
     * bare TCP accept).  That echo carries src == moduleId, so the blanket guard
     * below would drop it before the confirm handler runs — the confirm plane
     * would be structurally unreachable.  The exception is scoped to the
     * `_device/<id>/bus_confirm` topic ALONE; every OTHER self-publish is still
     * dropped.  The seq rides as a JSON string ("5") or a number. */
    const char* cns = bus_binding_confirm_namespace();
    if (cns[0] && strcmp(ns, cns) == 0) {
        JsonVariantConst v = doc["val"];
        uint32_t rseq = 0;
        if (v.is<const char*>())
            rseq = (uint32_t)strtoul(v.as<const char*>(), nullptr, 10);
        else if (v.is<int>() || v.is<float>())
            rseq = (uint32_t)v.as<double>();
        bus_binding_note_echo(rseq);
        return;                          /* consumed; never routed further */
    }

    if (self) return;                    /* never our own echo (loop guard) */

    /* CONTROL PLANE first ([CXC-ER-REMOTE-RESTART]): a truthy command to
     * `_device/<id>/restart` (or broadcast `_device/restart`) re-runs the
     * boot config-fetch — the live re-fetch trigger.  Consumed here, never
     * routed to an actuator. */
    if (restart_is_restart_ns(ns)) {
        JsonVariantConst v = doc["val"];
        int kind = 2; double num = 0.0; bool b = false; const char* str = "";
        if (v.is<bool>()) { kind = 1; b = v.as<bool>(); }
        else if (v.is<int>() || v.is<float>()) { kind = 0; num = v.as<double>(); }
        else { kind = 2; str = v.as<const char*>() ? v.as<const char*>() : ""; }
        restart_on_bus_value(ns, kind, num, b, str, millis());
        return;
    }

    /* [CXC-REPOINT] bus-BINDING control plane: a DIFFERING value on the active
     * tier arms a trial (writes NVS, restarts via the reboot seam); an identical
     * value is absorbed; a value targeting a lower tier is cached without arming
     * ([CXC-REPOINT-PRECEDENCE]).  Consumed here, never routed to an actuator. */
    {
        JsonVariantConst v = doc["val"];
        int kind = 2; double num = 0.0; bool b = false; const char* str = "";
        if (v.is<bool>()) { kind = 1; b = v.as<bool>(); }
        else if (v.is<int>() || v.is<float>()) { kind = 0; num = v.as<double>(); }
        else { kind = 2; str = v.as<const char*>() ? v.as<const char*>() : ""; }
        if (bus_binding_on_bus_value(ns, kind, num, b, str)) return;
    }

    /* RELAY BANK: an enum arrival on the subscribed speed namespace drives
     * the break-before-make sequence (the make completes in loop()'s tick). */
    if (g_relay.present() && strcmp(ns, g_relay.ns()) == 0) {
        const char* val = doc["val"] | "";
        int fired = g_relay.on_value(val, millis());
        if (fired) Serial.printf("relay %s <- '%s' (break %d pins; make in %dms)\n",
                                 g_relay.ns(), val, fired, g_cfg.relay.dead_time_ms);
        else if (*g_relay.last_error())
            Serial.printf("relay: %s\n", g_relay.last_error());
        return;
    }

    routeAudio(ns, doc);
}

/* --- Connectivity -------------------------------------------------------- */
static void setupWiFi() {
    Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 40) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
        Serial.printf("\nWiFi connected. IP: %s\n",
                      WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\nWiFi failed — cannot fetch config (will retry)");
    }
}

static void reconnectMQTT() {
    if (mqtt.connected()) return;
    unsigned long now = millis();
    if (now - lastReconnect < kReconnectMs) return;
    lastReconnect = now;

    Serial.printf("MQTT connecting to %s:%d as %s...\n",
                  mqttHost, (int)mqttPort, moduleId);
    if (mqtt.connect(moduleId)) {
        Serial.println("MQTT connected");
        char topic[96];
        /* subscribe the audio-cover's namespaces (pure subscriber) */
        for (const auto& ns : g_cfg.audio.subscribes) {
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                     ns.c_str());
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed: %s\n", topic);
        }
        /* subscribe the relay bank's enum namespace (the speed command) */
        if (g_relay.present()) {
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                     g_relay.ns());
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed: %s\n", topic);
        }
        /* subscribe the two remote-reload control namespaces ([CXC-ER-
         * RESTART-BUS]: two EXACT namespaces, not a `_device` glob) */
        const char* rns[4];
        size_t rn = restart_namespaces(rns, 4);
        for (size_t i = 0; i < rn; i++) {
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX, rns[i]);
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed (reload): %s\n", topic);
        }
        /* [CXC-REPOINT] the bus-BINDING control plane: the retained address
         * fact on `_device/<id>/bus` (per-device) + `_device/broadcast/bus`
         * (global), settable over the CURRENT link and stored in NVS. */
        const char* bns[2];
        size_t bn = bus_binding_control_namespaces(bns, 2);
        for (size_t i = 0; i < bn; i++) {
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX, bns[i]);
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed (binding): %s\n", topic);
        }
        /* [CXC-REPOINT-CONFIRM] the confirm-echo namespace: subscribe it so our
         * OWN confirm publication, fanned back by the broker, lands as the
         * broker round-trip plane of a commit-confirmed trial. */
        const char* cns = bus_binding_confirm_namespace();
        if (cns[0]) {
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX, cns);
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed (confirm): %s\n", topic);
        }
    } else {
        Serial.printf("MQTT failed (rc=%d)\n", mqtt.state());
    }
}

/* --- Boot config fetch + apply ------------------------------------------- */
static bool fetchConfig() {
    size_t len = 0;
    char* text = config_fetch(&len);          /* cyd/src by reference */
    if (!text) {
        Serial.println("config_fetch: no config available");
        /* [CXC-REPOINT-REVERT] Note 1: on a trial boot, name WHY the store plane
         * failed — an HTTP code (`store-http-404`) vs no response at all
         * (`store-unreachable`) — so a revert is diagnosable on the bench. */
        if (bus_binding_active_is_trial()) {
            int st = config_fetch_last_status();
            char reason[24];
            if (st > 0) snprintf(reason, sizeof(reason), "store-http-%d", st);
            else        snprintf(reason, sizeof(reason), "store-unreachable");
            bus_binding_note_reason(reason);
        }
        return false;
    }
    Serial.printf("Fetched %u bytes of config\n", (unsigned)len);
    /* [CXC-REPOINT-CONFIRM] a 200 + non-empty body from the ACTIVE tier's store
     * is the STORE plane of a commit-confirmed trial.  This shell always fetches
     * over HTTP, so the store plane is genuinely exercised and store_required
     * stays true — a trial confirms only with BOTH this and the broker echo. */
    bus_binding_note_store_ok();
    g_cfg = controller_config_parse(text, len);
    free(text);
    if (!g_cfg.ok()) {
        Serial.printf("Config error: %s\n", g_cfg.error.c_str());
        return false;
    }
    return true;
}

static void applyConfig() {
    /* PHYSICAL half: ADC input pins */
    analogReadResolution(12);
    for (const auto& s : g_cfg.sensors)
        analogSetPinAttenuation(s.pin, ADC_11db);   /* full 0..~3.1V range */
    g_next_sample.assign(g_cfg.sensors.size(), 0);
    g_cond.assign(g_cfg.sensors.size(), ConditionState());

    /* OUTPUT: the break-before-make relay bank.  Configure OUTPUT + drive
     * every owned pin de-energized (fan OFF) the instant the pins are live,
     * THEN assert the default state.  configure() emits the safe baseline;
     * on_value(default) sequences to the default speed. */
    if (g_cfg.relay.present) {
        for (int p : g_cfg.relay.pins) {
            pinMode(p, OUTPUT);
            digitalWrite(p, g_cfg.relay.active_high ? LOW : HIGH);  /* open */
        }
        g_relay.set_sink(relaySink, nullptr);
        g_relay.configure(&g_cfg.relay);
        g_relay.on_value(g_cfg.relay.default_value.c_str(), millis());
    }

    /* S6 audio: the DY-SV5F UART (TX only) + optional BUSY input */
    if (g_cfg.audio.present && g_cfg.audio.tx_pin >= 0) {
        Serial1.begin(g_cfg.audio.baud, SERIAL_8N1, /*rx*/ -1,
                      /*tx*/ g_cfg.audio.tx_pin);
        if (g_cfg.audio.busy_pin >= 0) pinMode(g_cfg.audio.busy_pin, INPUT);
    }

    /* Legible self-report — the config that became this device. */
    Serial.printf("Sensors: %u\n", (unsigned)g_cfg.sensors.size());
    for (const auto& s : g_cfg.sensors) {
        /* Print the EFFECTIVE band, not the raw fields: 0/0 means "unset",
         * and a log that echoes 0/0 tells you nothing about what the
         * arithmetic will actually do.  A self-report that cannot show a
         * MISCONFIGURED band is not a self-report. */
        const int lo = s.in_min > 0 ? s.in_min : 0;
        const int hi = (s.in_max > 0 && s.in_max <= s.adc_max) ? s.in_max
                                                               : s.adc_max;
        Serial.printf("  %s <- ADC GPIO%d  (%d..%d -> 0..%d @ %dHz, %s%s "
                      "db=%d ema=%.2f%s)\n",
                      s.ns.c_str(), s.pin, lo, hi, s.out_max, s.sample_hz,
                      s.state_engine.c_str(), s.invert ? " INVERT" : "",
                      s.deadband, s.ema_alpha,
                      (lo > 0 || hi < s.adc_max) ? " BANDED" : " no-band");
    }
    if (g_cfg.relay.present) {
        Serial.printf("Relay bank: '%s' -> %u pins, %s, dead-time %dms, "
                      "default '%s'\n",
                      g_cfg.relay.ns.c_str(), (unsigned)g_cfg.relay.pins.size(),
                      g_cfg.relay.active_high ? "active-HIGH" : "active-LOW",
                      g_cfg.relay.dead_time_ms, g_cfg.relay.default_value.c_str());
        for (const auto& st : g_cfg.relay.states)
            Serial.printf("  %-8s -> %s\n", st.value.c_str(),
                          st.pin < 0 ? "(all open)"
                                     : (std::string("GPIO") +
                                        std::to_string(st.pin)).c_str());
    }
    if (g_cfg.audio.present) {
        Serial.printf("Audio: DY-SV5F UART TX GPIO%d busy GPIO%d @ %d baud\n",
                      g_cfg.audio.tx_pin, g_cfg.audio.busy_pin,
                      g_cfg.audio.baud);
        Serial.printf("  contacts '%s' -> track %d;  jump '%s' -> track %d\n",
                      g_cfg.audio.contacts_ns.c_str(),
                      g_cfg.audio.condition_one_track,
                      g_cfg.audio.jump_ns.c_str(), g_cfg.audio.jump_track);
    }
}

/* --- Setup / loop -------------------------------------------------------- */
void setup() {
    Serial.begin(115200);
    delay(2000);                       /* USB-CDC settle */
    Serial.println("\n=== ComposeUX Reactive Controller (config-fetched) "
                   "[CXC-ER-DEVICE] ===");
    /* Build stamp — COMPILER-WRITTEN, never hand-maintained.  A tag a human
     * must remember to bump is an instrument that goes stale exactly when it
     * matters; __DATE__/__TIME__ cannot.  This is how a flash is PROVEN to
     * have landed rather than assumed. */
    Serial.printf("build: %s %s  [CXC-HAL-CONDITION-RAILS]\n",
                  __DATE__, __TIME__);

    uint64_t mac = ESP.getEfuseMac();
    snprintf(moduleId, sizeof(moduleId), "cio-ctrl-%04x",
             (unsigned)(mac & 0xFFFF));

    /* Remote config-reload control plane ([CXC-ER-REMOTE-RESTART]): install
     * the handler so a truthy `_device/<id>/restart` (or broadcast
     * `_device/restart`) re-runs the boot config-fetch.  The 3s boot-window
     * guard drops a re-delivered/retained command at the reconnect burst so
     * the device cannot loop.  Installed before WiFi so it is armed the
     * moment MQTT subscribes. */
    restart_init(device_id(), publishString, rebootNow, /*boot_window_ms*/ 3000);

    /* [CXC-REPOINT] Resolve the bus BINDING and arm the commit-confirmed revert
     * timer BEFORE the network comes up ([CXC-REPOINT-COMMIT-CONFIRMED]
     * ordering-is-load-bearing: a DHCP/TLS hang after this point still reverts)
     * and before config_fetch/mqtt read the active store/broker.  Also reads the
     * two-stage escape-hatch GPIO.  120 s is [DEC-SKIPPER-COMMIT-CONFIRM-WINDOW-
     * 120S].  Installing the control plane here sets the publish seam + the
     * confirm-echo namespace so the broker round-trip plane can land. */
    bus_binding_boot_init(device_id(), 120);
    bus_binding_control_init(device_id(), publishString);

    setupWiFi();

    /* [CXC-REPOINT-REVERT] Note 1: on a TRIAL boot where WiFi never came up, the
     * revert reason is `no-wifi`, not a bare `timeout`.  Set before the store/
     * broker planes are attempted so the earliest cause wins. */
    if (bus_binding_active_is_trial() && !wifiConnected)
        bus_binding_note_reason("no-wifi");

    Serial.printf("Device id: %s\n", device_id());
    /* config_fetch resolves bus://assets/displays/<device_id>.compose.yaml
     * -> {BUS_STORAGE_URL}/api/v1/assets/displays/<device_id>.compose.yaml */
    for (int attempt = 0; attempt < 5 && !configReady; attempt++) {
        if (fetchConfig()) { configReady = true; break; }
        delay(1500);
    }
    if (configReady) applyConfig();
    else Serial.println("No valid config yet — idling; will re-fetch in loop");

    /* [CXC-REPOINT] point PubSubClient at the ACTIVE broker, not the #define.
     * bus_binding_active_mqtt() is the resolved tier's `mqtt://host:port`; with
     * no NVS binding it is the compiled-IP floor, so an un-bound board connects
     * exactly where it does today (the bus master), just addressed by IP. */
    bus_uri_host_port(bus_binding_active_mqtt(), mqttHost, sizeof(mqttHost),
                      &mqttPort, MQTT_PORT);
    mqtt.setServer(mqttHost, mqttPort);
    mqtt.setBufferSize(2048);          /* dradis/contacts can be a big list */
    mqtt.setCallback(mqttCallback);
}

void loop() {
    /* Recover config if the first boot missed it (bus_storage was down). */
    if (!configReady) {
        static unsigned long lastTry = 0;
        if (wifiConnected && millis() - lastTry > 10000) {
            lastTry = millis();
            if (fetchConfig()) { configReady = true; applyConfig(); }
        }
    }

    if (wifiConnected) {
        if (!mqtt.connected()) reconnectMQTT();
        mqtt.loop();
    } else if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
    }

    /* [CXC-REPOINT] drive the announce cadence + confirm-echo probe, and fire
     * the commit-confirmed revert when the window elapses.  UNCONDITIONAL and
     * BEFORE the connectivity early-return: a bad broker binding means mqtt
     * NEVER connects, and that trial must still auto-revert — reverting only on
     * a healthy link would strand exactly the boards a bad binding bricked.
     * (The publish seam no-ops while mqtt is down, so this is safe offline.) */
    bus_binding_tick(millis());

    if (!configReady || !mqtt.connected()) return;

    /* OUTPUT: complete any pending break-before-make (energize the target
     * relay once the dead-time has elapsed). */
    if (g_relay.present()) g_relay.tick(millis());

    /* PHYSICAL half: sample each ADC at its rate, publish on change. */
    const uint32_t now = millis();
    for (size_t i = 0; i < g_cfg.sensors.size(); i++) {
        const SensorSpec& s = g_cfg.sensors[i];
        if (now < g_next_sample[i]) continue;
        g_next_sample[i] = now + (s.sample_hz > 0 ? 1000u / s.sample_hz : 50u);
        /* ⛔ The conditioning arithmetic lives in signal_conditioning.cpp,
         * NOT here.  It used to be inline in this loop — a file that
         * env:native_test cannot compile — so there was NO SEAM AT WHICH
         * IT COULD BE TESTED, and a defect in the path BOTH channels share
         * was invisible to every per-channel check.  Extend the pure
         * function and its host tests; never re-inline this.
         * [CXC-HAL-CONDITION-RAILS] */
        ConditionResult cr = condition_sample(s, analogRead(s.pin), g_cond[i]);
        if (cr.emit) publishNumber(s.ns.c_str(), (double)cr.value);
    }
}

#endif /* CONTROLLER_CONFIG_FETCH */
