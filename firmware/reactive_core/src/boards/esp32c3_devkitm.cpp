/*
 * boards/esp32c3_devkitm.cpp — ESP32-C3-DevKitM-1 board profile (R1+R2).
 *
 * R1: demo_gpio = GPIO3 (free on this devkit — ADC pin, no strapping,
 * no USB/UART conflict); the on-board WS2812 (GPIO8) mirrors the demo
 * state green/dark so the loop is visible with no wiring.
 *
 * R2 demo actuator table ([CXC-ER-HAL]) — all three modes so the HAL
 * logic is verifiable on the bare devkit over serial (dispatcher):
 *   light/1        Binary  GPIO4  (probe with LED/scope)
 *   light/2        Pwm     GPIO5  (LED brightness follows 0..100)
 *   gauge/1        Stepper channel 0 — NO driver on the devkit;
 *                  board_drive_coils is a no-op, but the sequencer's
 *                  target/arrival self-report proves the Tier-2 math
 *                  on silicon (240 steps full scale, homing on boot).
 */

#ifdef ARDUINO

#include <Arduino.h>
#include "../board.h"

static const BoardProfile kProfile = {
    "esp32c3-devkitm-1",
    /* demo_gpio     */ 3,
    /* neopixel_gpio */ 8,
};

const BoardProfile& board_profile() { return kProfile; }

static const ActuatorSpec kActuators[] = {
    /* ns          mode                  pin  act_hi in_min in_max out_max */
    { "light/1",   ActuatorMode::Binary,   4, true,  0.0,   100.0, 255 },
    { "light/2",   ActuatorMode::Pwm,      5, true,  0.0,   100.0, 255 },
    { "gauge/1",   ActuatorMode::Stepper,  0, true,  0.0,   100.0, 240,
      /* step_interval_ms */ 3, /* home_on_boot */ true },
};

const ActuatorSpec* board_actuator_specs(size_t* count) {
    *count = sizeof(kActuators) / sizeof(kActuators[0]);
    return kActuators;
}

/* LEDC: one PWM channel per Pwm actuator, assigned in table order. */
static void pwm_attach() {
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
}

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
    pinMode(kProfile.demo_gpio, OUTPUT);
    digitalWrite(kProfile.demo_gpio, LOW);
    if (kProfile.neopixel_gpio >= 0)
        neopixelWrite(kProfile.neopixel_gpio, 0, 0, 0);

    size_t n;
    const ActuatorSpec* specs = board_actuator_specs(&n);
    for (size_t i = 0; i < n; i++) {
        if (specs[i].mode == ActuatorMode::Binary) {
            pinMode(specs[i].pin, OUTPUT);
            digitalWrite(specs[i].pin,
                         specs[i].active_high ? LOW : HIGH);
        }
    }
    pwm_attach();
}

void board_apply_demo_output(bool on) {
    digitalWrite(kProfile.demo_gpio, on ? HIGH : LOW);
    if (kProfile.neopixel_gpio >= 0)
        neopixelWrite(kProfile.neopixel_gpio, 0, on ? 32 : 0, 0);
}

void board_drive_binary(const ActuatorSpec& spec, bool level) {
    digitalWrite(spec.pin, level ? HIGH : LOW);
}

void board_drive_pwm(const ActuatorSpec& spec, int duty) {
    const int ch = pwm_channel_of(spec);
    if (ch >= 0) ledcWrite(ch, duty);
}

void board_drive_coils(const ActuatorSpec&, uint8_t) {
    /* no stepper driver on the bare devkit — the sequencer's serial
     * self-report is the silicon-level verification */
}

#endif /* ARDUINO */
