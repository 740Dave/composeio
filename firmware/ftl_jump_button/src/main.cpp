/**
 * ComposeIO FTL Jump Button — First Physical Module
 *
 * CH32V003 dev board:
 *   - PA2: Momentary switch (active low, external pullup or internal)
 *   - PA1: LED output
 *
 * Speaks ComposeIO binary UART protocol:
 *   [0xC1][len][ns_hash_hi][ns_hash_lo][type_scope][value...][crc8]
 *
 * Publishes:
 *   ftl_jump event ("jump") on button press
 *
 * Subscribes:
 *   ftl_jump_led state changes (off/blink_fast/blink_slow/solid)
 */

#include <Arduino.h>

// Pin definitions
#ifndef LED_PIN
#define LED_PIN PA1
#endif
#ifndef BTN_PIN
#define BTN_PIN PA2
#endif

// ComposeIO protocol constants.
//
// Frame layout: [BP-TRANSPORT-UART] / [BP-UART-FRAME].
// Type codes:   [BP-TYPE-CODES] — spec/01-composeio-bus-protocol.smd is the ONE
//               allocation (0=state .. 9=log_entry, 10-15 reserved). This
//               firmware needs only two of them; the constants below are a
//               SUBSET of that table, not a table of their own. Adding a type
//               here means copying its code from the spec, never inventing one,
//               and a received code this firmware does not know must be ignored
//               — never treated as state.
#define CIO_SYNC 0xC1
#define CIO_TYPE_STATE  0
#define CIO_TYPE_EVENT  1
#define CIO_SCOPE_LOCAL  0
#define CIO_SCOPE_GLOBAL 1
#define CIO_SCOPE_AUTO   3

// Pre-computed namespace hashes (CRC-16/CCITT of namespace strings)
// These must match the Python namespace_hash() function
static uint16_t hash_ftl_jump = 0;
static uint16_t hash_ftl_jump_led = 0;

// CRC-16/CCITT-FALSE (same as Python config.namespace_hash)
uint16_t crc16_ccitt(const char* str) {
    uint16_t crc = 0xFFFF;
    while (*str) {
        crc ^= ((uint16_t)*str++) << 8;
        for (int i = 0; i < 8; i++) {
            if (crc & 0x8000)
                crc = (crc << 1) ^ 0x1021;
            else
                crc = crc << 1;
        }
    }
    return crc;
}

// CRC-8/MAXIM (same as Python _crc8)
uint8_t crc8(const uint8_t* data, uint8_t len) {
    uint8_t crc = 0;
    for (uint8_t i = 0; i < len; i++) {
        crc ^= data[i];
        for (uint8_t j = 0; j < 8; j++) {
            if (crc & 0x80)
                crc = (crc << 1) ^ 0x31;
            else
                crc = crc << 1;
        }
    }
    return crc;
}

// --- LED state machine ---
enum LedMode { LED_OFF, LED_BLINK_FAST, LED_BLINK_SLOW, LED_SOLID };
volatile LedMode led_mode = LED_OFF;
unsigned long last_blink = 0;
bool led_state = false;

// LED enum value indices (must match Python enum order: off=0, blink_fast=1, blink_slow=2, solid=3)
#define LED_VAL_OFF        0
#define LED_VAL_BLINK_FAST 1
#define LED_VAL_BLINK_SLOW 2
#define LED_VAL_SOLID      3

// --- Module Identity (Architect spec: flash region 0x3F00-0x3FFF, hardcoded for now) ---
// TODO: Read from flash identity region once flash station auto-burn is built
const char* get_module_id() {
    return "cio-FTL1";  // Hardcoded test ID — swap to flash-read later
}

// --- Button debounce ---
bool last_btn = false;
unsigned long last_debounce = 0;
#define DEBOUNCE_MS 50

// --- UART receive buffer ---
#define RX_BUF_SIZE 32
uint8_t rx_buf[RX_BUF_SIZE];
uint8_t rx_pos = 0;

// --- TX: Send a ComposeIO message ---
void cio_send_event(uint16_t ns_hash, const char* event_str) {
    // Encode event string as length-prefixed
    uint8_t evt_len = strlen(event_str);
    uint8_t payload_len = 3 + 1 + evt_len;  // ns_hash(2) + type_scope(1) + len_prefix(1) + string

    uint8_t frame[2 + payload_len + 1];  // sync + len + payload + crc
    frame[0] = CIO_SYNC;
    frame[1] = payload_len;
    frame[2] = (ns_hash >> 8) & 0xFF;    // ns_hash high
    frame[3] = ns_hash & 0xFF;            // ns_hash low
    frame[4] = (CIO_TYPE_EVENT << 4) | (CIO_SCOPE_GLOBAL << 2);  // type=event, scope=global
    frame[5] = evt_len;                    // string length prefix
    memcpy(&frame[6], event_str, evt_len);

    // CRC over len + payload
    frame[2 + payload_len] = crc8(&frame[1], 1 + payload_len);

    Serial.write(frame, 2 + payload_len + 1);
}

// --- RX: Process incoming frame ---
void process_frame(uint8_t* frame, uint8_t frame_len) {
    if (frame_len < 5) return;
    if (frame[0] != CIO_SYNC) return;

    uint8_t payload_len = frame[1];
    if (frame_len < (uint8_t)(2 + payload_len + 1)) return;

    // Verify CRC
    uint8_t expected_crc = crc8(&frame[1], 1 + payload_len);
    if (frame[2 + payload_len] != expected_crc) return;

    // Parse
    uint16_t ns_hash = ((uint16_t)frame[2] << 8) | frame[3];
    uint8_t type_scope = frame[4];
    uint8_t msg_type = (type_scope >> 4) & 0x0F;

    // Only handle state messages for ftl_jump_led
    if (ns_hash == hash_ftl_jump_led && msg_type == CIO_TYPE_STATE) {
        uint8_t* value_bytes = &frame[5];
        uint8_t value_len = payload_len - 3;

        if (value_len >= 1) {
            uint8_t val = value_bytes[0];
            // Value is either a raw byte (enum index) or length-prefixed string
            // For enum index: 0=off, 1=blink_fast, 2=blink_slow, 3=solid
            switch (val) {
                case LED_VAL_OFF:        led_mode = LED_OFF; break;
                case LED_VAL_BLINK_FAST: led_mode = LED_BLINK_FAST; break;
                case LED_VAL_BLINK_SLOW: led_mode = LED_BLINK_SLOW; break;
                case LED_VAL_SOLID:      led_mode = LED_SOLID; break;
                default:
                    // Try string matching for named values
                    if (value_len > 1 && val == value_len - 1) {
                        // Length-prefixed string
                        char str_buf[16] = {0};
                        uint8_t slen = val < 15 ? val : 15;
                        memcpy(str_buf, &value_bytes[1], slen);
                        if (strcmp(str_buf, "off") == 0) led_mode = LED_OFF;
                        else if (strcmp(str_buf, "blink_fast") == 0) led_mode = LED_BLINK_FAST;
                        else if (strcmp(str_buf, "blink_slow") == 0) led_mode = LED_BLINK_SLOW;
                        else if (strcmp(str_buf, "solid") == 0) led_mode = LED_SOLID;
                    }
                    break;
            }
        }
    }
}

// --- RX: Read and frame incoming bytes ---
void rx_poll() {
    while (Serial.available()) {
        uint8_t b = Serial.read();

        if (rx_pos == 0) {
            // Looking for sync byte
            if (b == CIO_SYNC) {
                rx_buf[rx_pos++] = b;
            }
        } else {
            rx_buf[rx_pos++] = b;

            // Check if we have enough for a complete frame
            if (rx_pos >= 3) {
                uint8_t payload_len = rx_buf[1];
                uint8_t expected_len = 2 + payload_len + 1;  // sync + len + payload + crc

                if (rx_pos >= expected_len) {
                    process_frame(rx_buf, rx_pos);
                    rx_pos = 0;
                } else if (rx_pos >= RX_BUF_SIZE) {
                    rx_pos = 0;  // Buffer overflow, reset
                }
            }
        }
    }
}

// --- LED update ---
void update_led() {
    unsigned long now = millis();

    switch (led_mode) {
        case LED_OFF:
            digitalWrite(LED_PIN, LOW);
            break;

        case LED_SOLID:
            digitalWrite(LED_PIN, HIGH);
            break;

        case LED_BLINK_FAST:
            if (now - last_blink >= 150) {
                led_state = !led_state;
                digitalWrite(LED_PIN, led_state ? HIGH : LOW);
                last_blink = now;
            }
            break;

        case LED_BLINK_SLOW:
            if (now - last_blink >= 500) {
                led_state = !led_state;
                digitalWrite(LED_PIN, led_state ? HIGH : LOW);
                last_blink = now;
            }
            break;
    }
}

// --- Button poll with debounce ---
void poll_button() {
    bool current = !digitalRead(BTN_PIN);  // Active low
    unsigned long now = millis();

    if (current != last_btn && (now - last_debounce) > DEBOUNCE_MS) {
        last_debounce = now;
        last_btn = current;

        if (current) {
            // Button pressed — send jump event
            cio_send_event(hash_ftl_jump, "jump");
        }
    }
}

void setup() {
    // Pre-compute namespace hashes
    hash_ftl_jump = crc16_ccitt("ftl_jump");
    hash_ftl_jump_led = crc16_ccitt("ftl_jump_led");

    // Pin setup
    pinMode(LED_PIN, OUTPUT);
    pinMode(BTN_PIN, INPUT_PULLUP);
    digitalWrite(LED_PIN, LOW);

    // UART
    Serial.begin(115200);

    // Startup indication: quick LED flash
    for (int i = 0; i < 3; i++) {
        digitalWrite(LED_PIN, HIGH);
        delay(100);
        digitalWrite(LED_PIN, LOW);
        delay(100);
    }
}

void loop() {
    rx_poll();
    poll_button();
    update_led();
}
