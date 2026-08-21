/**
 * ComposeIO ESP32 MQTT Client — WiFi exit node for CH32V003 modules.
 *
 * Architecture (Phase 2B-3):
 *   CH32V003 (I2C slave, button/LED) ↔ ESP32 (I2C master, MQTT client) ↔ WiFi ↔ Mosquitto ↔ Mac simulator
 *
 * This ESP32 acts as a ComposeIO mainboard (WiFi Bridge variant):
 *   - I2C master: polls CH32V003 daughterboard for button events, pushes LED commands
 *   - MQTT client: publishes button events, subscribes to LED commands
 *   - Bridges between I2C binary protocol and MQTT JSON protocol
 *
 * DP-CIO-6: Transport is an implementation detail. The CH32V003 doesn't know
 * it's talking to WiFi/MQTT. It just sees I2C register reads/writes.
 */

#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <Wire.h>
#include <ArduinoJson.h>

// --- Configuration (from build flags) ---
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
#ifndef I2C_SDA
#define I2C_SDA 8
#endif
#ifndef I2C_SCL
#define I2C_SCL 9
#endif
#ifndef CIO_TOPIC_PREFIX
#define CIO_TOPIC_PREFIX "composeio"
#endif

// CH32V003 I2C slave address (from module roles spec)
#define CH32V_I2C_ADDR 0x42

// I2C register map (from 03-composeio-module-roles.smd)
#define REG_ROLE_ID     0x00
#define REG_CONFIG_CRC  0x01
#define REG_DATA_BASE   0x10  // Role-specific data registers
#define REG_BTN_STATE   0x10  // Button state register
#define REG_LED_CMD     0x11  // LED command register

// --- Globals ---
WiFiClient espClient;
PubSubClient mqtt(espClient);

unsigned long lastPoll = 0;
unsigned long lastReconnect = 0;
const unsigned long POLL_INTERVAL_MS = 50;   // Poll I2C every 50ms
const unsigned long RECONNECT_INTERVAL_MS = 5000;

uint8_t lastButtonState = 0;
bool wifiConnected = false;
bool mqttConnected = false;

// Module ID
const char* MODULE_ID = "cio-ESP1";

// --- WiFi ---
void setupWiFi() {
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
        Serial.printf("\nWiFi connected. IP: %s\n", WiFi.localIP().toString().c_str());
    } else {
        Serial.println("\nWiFi failed — running offline");
    }
}

// --- MQTT ---
void mqttCallback(char* topic, byte* payload, unsigned int length) {
    // Parse topic: composeio/{scope}/{namespace}
    String topicStr(topic);
    int lastSlash = topicStr.lastIndexOf('/');
    if (lastSlash < 0) return;
    String ns = topicStr.substring(lastSlash + 1);

    // Parse JSON payload
    StaticJsonDocument<256> doc;
    DeserializationError err = deserializeJson(doc, payload, length);
    if (err) {
        Serial.printf("JSON parse error: %s\n", err.c_str());
        return;
    }

    const char* msgNs = doc["ns"] | "";
    const char* msgType = doc["type"] | "";
    const char* msgVal = doc["val"] | "";

    // Handle ftl_jump_led state changes → forward to CH32V003 via I2C
    if (strcmp(msgNs, "ftl_jump_led") == 0 && strcmp(msgType, "state") == 0) {
        uint8_t ledCmd = 0;  // off
        if (strcmp(msgVal, "blink_fast") == 0) ledCmd = 1;
        else if (strcmp(msgVal, "blink_slow") == 0) ledCmd = 2;
        else if (strcmp(msgVal, "solid") == 0) ledCmd = 3;

        // Write LED command to CH32V003 via I2C
        Wire.beginTransmission(CH32V_I2C_ADDR);
        Wire.write(REG_LED_CMD);
        Wire.write(ledCmd);
        Wire.endTransmission();

        Serial.printf("LED cmd → CH32V003: %s (%d)\n", msgVal, ledCmd);
    }
}

void reconnectMQTT() {
    if (mqtt.connected()) {
        mqttConnected = true;
        return;
    }

    unsigned long now = millis();
    if (now - lastReconnect < RECONNECT_INTERVAL_MS) return;
    lastReconnect = now;

    Serial.printf("MQTT connecting to %s:%d...\n", MQTT_BROKER, MQTT_PORT);
    if (mqtt.connect(MODULE_ID)) {
        mqttConnected = true;
        Serial.println("MQTT connected");

        // Subscribe to LED command namespace
        char topic[64];
        snprintf(topic, sizeof(topic), "%s/+/ftl_jump_led", CIO_TOPIC_PREFIX);
        mqtt.subscribe(topic);
        Serial.printf("Subscribed: %s\n", topic);
    } else {
        mqttConnected = false;
        Serial.printf("MQTT failed (rc=%d)\n", mqtt.state());
    }
}

void publishEvent(const char* ns, const char* eventType) {
    if (!mqtt.connected()) return;

    StaticJsonDocument<128> doc;
    doc["ns"] = ns;
    doc["type"] = "event";
    doc["evt"] = eventType;
    doc["src"] = MODULE_ID;
    doc["ts"] = millis() / 1000;
    doc["scope"] = "global";

    char payload[128];
    serializeJson(doc, payload, sizeof(payload));

    char topic[64];
    snprintf(topic, sizeof(topic), "%s/global/%s", CIO_TOPIC_PREFIX, ns);
    mqtt.publish(topic, payload);

    Serial.printf("Published: %s ! %s\n", ns, eventType);
}

// --- I2C polling ---
void pollCH32V003() {
    // Read button state register
    Wire.beginTransmission(CH32V_I2C_ADDR);
    Wire.write(REG_BTN_STATE);
    Wire.endTransmission(false);

    uint8_t bytesRead = Wire.requestFrom((uint16_t)CH32V_I2C_ADDR, (uint8_t)1);
    if (bytesRead >= 1) {
        uint8_t btnState = Wire.read();

        // Detect button press (transition from 0 to 1)
        if (btnState && !lastButtonState) {
            publishEvent("ftl_jump", "jump");
        }
        lastButtonState = btnState;
    }
}

// --- Setup ---
void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== ComposeIO ESP32 MQTT Client ===");
    Serial.printf("Module: %s\n", MODULE_ID);

    // I2C master
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(100000);  // 100kHz standard mode
    Serial.printf("I2C master on SDA=%d, SCL=%d\n", I2C_SDA, I2C_SCL);

    // WiFi
    setupWiFi();

    // MQTT
    mqtt.setServer(MQTT_BROKER, MQTT_PORT);
    mqtt.setCallback(mqttCallback);
}

// --- Loop ---
void loop() {
    // MQTT maintenance
    if (wifiConnected) {
        if (!mqtt.connected()) {
            reconnectMQTT();
        }
        mqtt.loop();
    }

    // Poll CH32V003 at interval
    unsigned long now = millis();
    if (now - lastPoll >= POLL_INTERVAL_MS) {
        lastPoll = now;
        pollCH32V003();
    }
}
