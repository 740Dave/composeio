/*
 * main.cpp — R1/R2 app shell: the reactive core as a live ComposeBus
 * node with a declarative output-actuator HAL.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-CORE] [CXC-ER-HAL] [CXC-ER-SLICES].
 *
 * Wires the board-agnostic core (reactive_core.{h,cpp} + the canonical
 * DSL) to this board's transports: WiFi + MQTT (the esp32_mqtt_client
 * foundation) in, MQTT publish + the board-seam demo GPIO out.
 *
 * R2 ([CXC-ER-C4], [CXC-HAL-ACTUATE]): the board profile declares an
 * ActuatorSpec table; the HAL subscribes each actuator namespace and
 * drives on change — Binary/PWM GPIOs directly (Tier 1), steppers via
 * the board's driver chip (Tier 2).  Every output state self-reports
 * over serial (`ACTUATOR light/5 <- ON (GPIO 4)`, `STEPPER gauge/2 <-
 * 128 steps`) so board bring-up is legible.  Values reach the HAL from
 * BOTH external bus arrivals and core-fired derived outputs — define
 * once, actuate many.
 *
 *   Mac publishes demo/level        composeio/global/demo/level
 *        -> core re-evaluates the bindings reading it
 *        -> demo/led      = expr: ns('demo/level') > 50      (GPIO + publish)
 *           demo/level_pct = expr: clamp(ns('demo/level') * 2, 0, 100)
 *           demo/echo     = demo/level          (bare-namespace identity)
 *        -> derived namespaces published back on the bus, LED flips,
 *           serial reports every hop.
 *
 * Wire format: BusMessage JSON over composeio/{scope}/{namespace}
 * (python/composeio/transport.py to_json / transport_mqtt.py).
 *
 * Demo drive (from the Mac):
 *   mosquitto_pub -h mqtt-broker.local -p 1884 \
 *     -t 'composeio/global/demo/level' \
 *     -m '{"ns":"demo/level","type":"state","val":72,"src":"mac","scope":"global"}'
 */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#include "reactive_core.h"
#include "actuator_hal.h"
#include "board.h"

/* The config-FETCHED controller tier ([CXC-ER-DEVICE]) provides its OWN
 * setup()/loop() in controller_main.cpp.  When that tier is selected
 * ([env:esp32c3] defines CONTROLLER_CONFIG_FETCH) this R1/R2 demo + actuator
 * shell is compiled out so there is exactly one setup()/loop().  Every other
 * env leaves the flag undefined and keeps this shell verbatim. */
#ifndef CONTROLLER_CONFIG_FETCH

/* --- Configuration ---------------------------------------------------
 * Placeholders by default; the dispatcher injects real credentials at
 * flash time via PLATFORMIO_BUILD_FLAGS or a gitignored wifi_config.h
 * (see platformio.ini header note).  Never hardcode credentials here. */
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
#define MQTT_PORT 1884
#endif
#ifndef CIO_TOPIC_PREFIX
#define CIO_TOPIC_PREFIX "composeio"
#endif

/* The binding whose result drives the board-seam demo output. */
static const char* kGpioOutputNs = "demo/led";

/* --- Globals --------------------------------------------------------- */
static WiFiClient espClient;
static PubSubClient mqtt(espClient);
static ReactiveCore core;
static ActuatorHal hal;

static char moduleId[24] = "cio-reactive-c3";
static bool wifiConnected = false;
static unsigned long lastReconnect = 0;
static const unsigned long kReconnectMs = 5000;
static uint32_t seq = 0;

/* --- Output sink: publish derived namespace + drive the board seam --- */
static void publishState(const char* ns, const DslValue& v) {
    if (!mqtt.connected()) return;

    StaticJsonDocument<256> doc;
    doc["ns"] = ns;
    doc["type"] = "state";
    switch (v.type) {
        case DslValue::Type::Number: doc["val"] = v.num; break;
        case DslValue::Type::Bool:   doc["val"] = v.b; break;
        default:                     doc["val"] = v.str.c_str(); break;
    }
    doc["src"] = moduleId;
    doc["ts"] = millis() / 1000.0;
    doc["scope"] = "global";
    doc["seq"] = ++seq;

    char payload[256];
    serializeJson(doc, payload, sizeof(payload));
    char topic[96];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    mqtt.publish(topic, payload);
}

static void describeValue(const DslValue& v, char* buf, size_t n) {
    switch (v.type) {
        case DslValue::Type::Number: snprintf(buf, n, "%g", v.num); break;
        case DslValue::Type::Bool:
            snprintf(buf, n, "%s", v.b ? "true" : "false"); break;
        default: snprintf(buf, n, "'%s'", v.str.c_str()); break;
    }
}

static void outputSink(void*, const char* output_ns, const DslValue& v) {
    char vbuf[64];
    describeValue(v, vbuf, sizeof(vbuf));
    Serial.printf("OUTPUT %s = %s\n", output_ns, vbuf);
    publishState(output_ns, v);
    if (strcmp(output_ns, kGpioOutputNs) == 0) {
        const bool on = dsl_truthy(v);
        board_apply_demo_output(on);
        if (board_profile().demo_gpio >= 0)
            Serial.printf("GPIO%d <- %s\n", board_profile().demo_gpio,
                          on ? "HIGH" : "LOW");
    }
    /* Define once, actuate many: a core-DERIVED namespace can drive an
     * actuator too ([CXC-HAL-ACTUATE]). */
    hal.on_value(output_ns, v);
}

/* --- R2 actuator sink: board drive + serial self-report --------------- */
static void actuatorSink(void*, const ActuatorSpec& spec,
                         const ActuatorCommand& cmd) {
    switch (cmd.kind) {
        case ActuatorCommand::Kind::Binary:
            Serial.printf("ACTUATOR %s <- %s (GPIO %d%s)\n", spec.ns,
                          cmd.on ? "ON" : "OFF", spec.pin,
                          spec.active_high ? "" : ", active-low");
            board_drive_binary(spec, cmd.level);
            break;
        case ActuatorCommand::Kind::PwmDuty:
            Serial.printf("ACTUATOR %s <- duty %d/%d (PWM GPIO %d)\n",
                          spec.ns, cmd.duty, spec.out_max, spec.pin);
            board_drive_pwm(spec, cmd.duty);
            break;
        case ActuatorCommand::Kind::StepperTarget:
            Serial.printf("STEPPER %s <- %d steps (from %d, ch %d)\n",
                          spec.ns, cmd.target, cmd.position, spec.pin);
            break;
        case ActuatorCommand::Kind::StepperStep:
            /* one line per step would flood serial at 3 ms/step —
             * the coil pattern goes straight to the driver */
            board_drive_coils(spec, cmd.coils);
            break;
        case ActuatorCommand::Kind::StepperArrived:
            Serial.printf("STEPPER %s @ %d steps (arrived, ch %d)\n",
                          spec.ns, cmd.position, spec.pin);
            break;
    }
}

/* --- Inbound: MQTT message -> CoreValue -> core ----------------------- */
static void mqttCallback(char* topic, byte* payload, unsigned int length) {
    StaticJsonDocument<512> doc;
    if (deserializeJson(doc, payload, length) != DeserializationError::Ok) {
        Serial.printf("JSON parse error on %s\n", topic);
        return;
    }

    /* Loop guard: never re-ingest our own publishes off the broker echo
     * (the core already wrote them back into its store). */
    const char* src = doc["src"] | "";
    if (strcmp(src, moduleId) == 0) return;

    /* state messages only (events/heartbeats carry no value) */
    const char* type = doc["type"] | "state";
    if (strcmp(type, "state") != 0) return;

    /* namespace: payload ns wins; else strip {prefix}/{scope}/ off the
     * topic (namespaces may contain '/', so only two segments strip). */
    const char* ns = doc["ns"] | "";
    char nsbuf[96];
    if (!*ns) {
        const char* p = strchr(topic, '/');
        p = p ? strchr(p + 1, '/') : nullptr;
        if (!p) return;
        snprintf(nsbuf, sizeof(nsbuf), "%s", p + 1);
        ns = nsbuf;
    }

    JsonVariant val = doc["val"];
    CoreValue cv;
    if (val.isNull()) return;
    if (val.is<bool>()) cv = CoreValue::boolean(val.as<bool>());
    else if (val.is<double>() || val.is<long>())
        cv = CoreValue::number(val.as<double>());
    else if (val.is<const char*>()) cv = CoreValue::string(val.as<const char*>());
    else return;                       /* nested structures: not a state */

    char vbuf[64];
    describeValue(dsl_coerce_input(cv.raw()), vbuf, sizeof(vbuf));
    Serial.printf("BUS %s = %s\n", ns, vbuf);

    const int fired = core.on_bus_value(ns, cv, millis() / 1000.0);
    if (fired == 0 && *core.last_eval_error())
        Serial.printf("EVAL WITHHELD: %s\n", core.last_eval_error());

    /* R2: the same arrival drives any actuator subscribed to *ns*
     * (the ns()-read view, exactly what a binding sees). */
    const int acted = hal.on_value(ns, dsl_coerce_input(cv.raw()));
    if (acted == 0 && *hal.last_error())
        Serial.printf("DRIVE WITHHELD: %s\n", hal.last_error());
}

/* --- Connectivity ----------------------------------------------------- */
static void setupWiFi() {
    Serial.printf("Connecting to WiFi: %s\n", WIFI_SSID);
    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASS);
    int attempts = 0;
    while (WiFi.status() != WL_CONNECTED && attempts < 20) {
        delay(500);
        Serial.print(".");
        attempts++;
    }
    if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
        Serial.printf("\nWiFi connected. IP: %s\n",
                      WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\nWiFi failed — offline (core still testable "
                       "via serial once transport lands)");
    }
}

static void reconnectMQTT() {
    if (mqtt.connected()) return;
    unsigned long now = millis();
    if (now - lastReconnect < kReconnectMs) return;
    lastReconnect = now;

    Serial.printf("MQTT connecting to %s:%d as %s...\n",
                  MQTT_BROKER, MQTT_PORT, moduleId);
    if (mqtt.connect(moduleId)) {
        Serial.println("MQTT connected");
        for (size_t i = 0; i < core.subscription_count(); i++) {
            char topic[96];
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                     core.subscription_at(i));
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed: %s\n", topic);
        }
        /* R2: actuator namespaces (deduped against the core's list) */
        for (size_t i = 0; i < hal.namespace_count(); i++) {
            const char* ns = hal.namespace_at(i);
            bool dup = false;
            for (size_t j = 0; j < core.subscription_count(); j++)
                if (strcmp(ns, core.subscription_at(j)) == 0) {
                    dup = true;
                    break;
                }
            if (dup) continue;
            char topic[96];
            snprintf(topic, sizeof(topic), "%s/+/%s", CIO_TOPIC_PREFIX,
                     ns);
            mqtt.subscribe(topic, 1);
            Serial.printf("Subscribed: %s (actuator)\n", topic);
        }
    } else {
        Serial.printf("MQTT failed (rc=%d)\n", mqtt.state());
    }
}

/* --- Setup / loop ------------------------------------------------------ */
void setup() {
    Serial.begin(115200);
    delay(2000);                       /* USB-CDC settle */
    Serial.println("\n=== ComposeUX Reactive Core R1 [CXC-ER-CORE] ===");
    Serial.printf("Board: %s (core is board-agnostic; seam: board.h)\n",
                  board_profile().name);

    /* Distinct client id — two peers sharing one get rc=7-kicked. */
    uint64_t mac = ESP.getEfuseMac();
    snprintf(moduleId, sizeof(moduleId), "cio-react-%04x",
             (unsigned)(mac & 0xFFFF));

    board_outputs_init();

    core.set_sink(outputSink, nullptr);

    /* R2: adopt the board's declarative actuator table ([CXC-ER-HAL]).
     * Sink first so homing steppers self-report their walk to zero. */
    hal.set_sink(actuatorSink, nullptr);
    {
        size_t n_specs = 0;
        const ActuatorSpec* specs = board_actuator_specs(&n_specs);
        Serial.printf("Actuators: %u declared\n", (unsigned)n_specs);
        for (size_t i = 0; i < n_specs; i++) {
            const ActuatorSpec& a = specs[i];
            switch (a.mode) {
                case ActuatorMode::Binary:
                    Serial.printf("  %s binary GPIO %d%s\n", a.ns, a.pin,
                                  a.active_high ? "" : " active-low");
                    break;
                case ActuatorMode::Pwm:
                    Serial.printf("  %s pwm GPIO %d (%g..%g -> 0..%d)\n",
                                  a.ns, a.pin, a.in_min, a.in_max,
                                  a.out_max);
                    break;
                case ActuatorMode::Stepper:
                    Serial.printf("  %s stepper ch %d (%g..%g -> 0..%d "
                                  "steps, %d ms/step%s)\n",
                                  a.ns, a.pin, a.in_min, a.in_max,
                                  a.out_max, a.step_interval_ms,
                                  a.home_on_boot ? ", homing" : "");
                    break;
            }
        }
        hal.configure(specs, n_specs);
    }

    /* R1 demo bindings (config-driven tier comes with the device model,
     * [CXC-ER-DEVICE]).  One expr driving the GPIO, one derived-math
     * expr, one bare-namespace identity — all three binding shapes. */
    struct { const char* out; const char* src; } kBindings[] = {
        {"demo/led",       "expr: ns('demo/level') > 50"},
        {"demo/level_pct", "expr: clamp(ns('demo/level') * 2, 0, 100)"},
        {"demo/echo",      "demo/level"},
    };
    for (auto& b : kBindings) {
        if (!core.add_binding(b.out, b.src))
            Serial.printf("BINDING REFUSED %s: %s\n", b.out, core.error());
        else
            Serial.printf("BINDING %s <- %s\n", b.out, b.src);
    }

    setupWiFi();
    mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    mqtt.setBufferSize(512);
    mqtt.setCallback(mqttCallback);

    Serial.printf("Drive it:  mosquitto_pub -h %s -p %d"
                  " -t '%s/global/demo/level' -m "
                  "'{\"ns\":\"demo/level\",\"type\":\"state\",\"val\":72,"
                  "\"src\":\"mac\",\"scope\":\"global\"}'\n",
                  MQTT_BROKER, (int)MQTT_PORT, CIO_TOPIC_PREFIX);
}

void loop() {
    /* R2: advance the steppers regardless of connectivity (boot homing
     * must finish even offline). */
    hal.tick(millis());

    if (wifiConnected) {
        if (!mqtt.connected()) reconnectMQTT();
        mqtt.loop();
    } else if (WiFi.status() == WL_CONNECTED) {
        wifiConnected = true;
    }
}

#endif /* !CONTROLLER_CONFIG_FETCH */
