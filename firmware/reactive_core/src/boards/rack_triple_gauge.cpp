/*
 * boards/rack_triple_gauge.cpp — the rack triple-gauge board (R2).
 * [CXC-ER-HAL] Output Tier 2 (stepper via driver) + Tier 1 (PWM
 * backlights), [CXC-HAL-ACTUATE] "rack_triple_gauge" reference — the
 * define-once flagship: a `gauge/N` namespace drives this physical
 * needle AND an on-screen GaugeWidget.
 *
 * Hardware (~/Dropbox/Maker/pcb/rack_triple_gauge/generate_pcb.py):
 * ESP32-C3 SuperMini -> MCP23017 (I2C) -> 3x L293D -> 3x stepper
 * motors (the dials) + 3x 2N2222 low-side PWM -> LED backlights.
 *
 * Pin map extracted from the generator:
 *   I2C            GPIO4 = SDA, GPIO5 = SCL (4.7k pullups to 3V3)
 *   MCP23017       A0..A2 = GND -> address 0x20; RESET tied high;
 *                  all L293D EN/VS/VCC pins tied +5V (always enabled)
 *   Motor 1        MCP GPA0..GPA3 -> L293D#1 IN1..IN4 -> J_MOT1
 *   Motor 2        MCP GPA4..GPA7 -> L293D#2 IN1..IN4 -> J_MOT2
 *   Motor 3        MCP GPB0..GPB3 -> L293D#3 IN1..IN4 -> J_MOT3
 *   Backlight 1    GPIO0 -> 2N2222 (low-side, active-high)
 *   Backlight 2    GPIO1 -> 2N2222
 *   Backlight 3    GPIO3 -> 2N2222
 *   (MCP GPB4..GPB7 + ESP GPIO2/6/7/8/9/10/20/21 = expansion headers)
 *
 * Actuator table: gauge/1..3 = steppers (channels 0..2), 0..100 ->
 * 0..600 full-step positions (TUNE out_max to the real motor/dial
 * sweep at bring-up), homing against the mechanical stop on boot;
 * gauge/N/light = PWM backlights, 0..100 -> duty 0..255.
 */

#ifdef ARDUINO

#include <Arduino.h>
#include <Wire.h>
#include "../board.h"

static const BoardProfile kProfile = {
    "rack-triple-gauge",
    /* demo_gpio     */ -1,
    /* neopixel_gpio */ -1,
};

const BoardProfile& board_profile() { return kProfile; }

/* --- MCP23017 (BANK=0 register map) -------------------------------- */
static const uint8_t kMcpAddr  = 0x20;   /* A0..A2 = GND */
static const uint8_t kRegIodirA = 0x00;
static const uint8_t kRegIodirB = 0x01;
static const uint8_t kRegOlatA  = 0x14;
static const uint8_t kRegOlatB  = 0x15;

static uint8_t olatA = 0x00, olatB = 0x00;   /* output shadows */
static bool mcpOk = false;

static bool mcp_write(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(kMcpAddr);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
}

/* --- I2C pins + PWM backlight pins (from the generator) ------------- */
static const int kSdaPin = 4;
static const int kSclPin = 5;

static const ActuatorSpec kActuators[] = {
    /* ns              mode                  pin/ch act_hi in_min in_max out_max  step_ms home */
    { "gauge/1",       ActuatorMode::Stepper,  0,   true,  0.0,   100.0, 600,     3,      true },
    { "gauge/2",       ActuatorMode::Stepper,  1,   true,  0.0,   100.0, 600,     3,      true },
    { "gauge/3",       ActuatorMode::Stepper,  2,   true,  0.0,   100.0, 600,     3,      true },
    { "gauge/1/light", ActuatorMode::Pwm,      0,   true,  0.0,   100.0, 255 },
    { "gauge/2/light", ActuatorMode::Pwm,      1,   true,  0.0,   100.0, 255 },
    { "gauge/3/light", ActuatorMode::Pwm,      3,   true,  0.0,   100.0, 255 },
};

const ActuatorSpec* board_actuator_specs(size_t* count) {
    *count = sizeof(kActuators) / sizeof(kActuators[0]);
    return kActuators;
}

/* LEDC channel = table order of the Pwm rows (0..2). */
static int pwm_channel_of(const ActuatorSpec& spec) {
    int ch = 0;
    size_t n;
    const ActuatorSpec* specs = board_actuator_specs(&n);
    for (size_t i = 0; i < n; i++) {
        if (specs[i].mode != ActuatorMode::Pwm) continue;
        if (&specs[i] == &spec) return ch;
        ch++;
    }
    return -1;
}

void board_outputs_init() {
    /* PWM backlights (Tier 1) */
    int ch = 0;
    size_t n;
    const ActuatorSpec* specs = board_actuator_specs(&n);
    for (size_t i = 0; i < n; i++) {
        if (specs[i].mode != ActuatorMode::Pwm) continue;
        ledcSetup(ch, 5000 /*Hz*/, 8 /*bits*/);
        ledcAttachPin(specs[i].pin, ch);
        ledcWrite(ch, 0);
        ch++;
    }

    /* MCP23017 -> L293D (Tier 2): both ports all-output, coils off */
    Wire.begin(kSdaPin, kSclPin);
    Wire.setClock(400000);
    mcpOk = mcp_write(kRegIodirA, 0x00) &&
            mcp_write(kRegIodirB, 0x00) &&
            mcp_write(kRegOlatA, 0x00) &&
            mcp_write(kRegOlatB, 0x00);
    if (!mcpOk)
        Serial.printf("MCP23017 @0x%02x not responding — steppers "
                      "disabled (board attached?)\n", kMcpAddr);
}

void board_apply_demo_output(bool) { /* no demo pin on this board */ }

void board_drive_binary(const ActuatorSpec&, bool) { /* none declared */ }

void board_drive_pwm(const ActuatorSpec& spec, int duty) {
    const int ch = pwm_channel_of(spec);
    if (ch >= 0) ledcWrite(ch, duty);
}

/* 4-bit IN1..IN4 pattern -> the channel's MCP nibble:
 *   channel 0 = OLATA bits 0..3 (GPA0..3 -> L293D#1)
 *   channel 1 = OLATA bits 4..7 (GPA4..7 -> L293D#2)
 *   channel 2 = OLATB bits 0..3 (GPB0..3 -> L293D#3)   */
void board_drive_coils(const ActuatorSpec& spec, uint8_t pattern) {
    if (!mcpOk) return;
    switch (spec.pin) {
        case 0:
            olatA = (olatA & 0xF0) | (pattern & 0x0F);
            mcp_write(kRegOlatA, olatA);
            break;
        case 1:
            olatA = (olatA & 0x0F) | (uint8_t)((pattern & 0x0F) << 4);
            mcp_write(kRegOlatA, olatA);
            break;
        case 2:
            olatB = (olatB & 0xF0) | (pattern & 0x0F);
            mcp_write(kRegOlatB, olatB);
            break;
        default:
            break;
    }
}

#endif /* ARDUINO */
