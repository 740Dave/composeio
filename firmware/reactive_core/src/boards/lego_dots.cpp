/*
 * boards/lego_dots.cpp — the lego_dots 12-channel light board (R2).
 * [CXC-ER-HAL] Output Tier 1, [CXC-HAL-ACTUATE] "lego_dots" reference.
 *
 * Hardware (~/Dropbox/Maker/pcb/lego_dots/generate_pcb.py, v1.0):
 * ESP32-C3 SuperMini; each channel is GPIO -> 150R -> BC337 base,
 * emitter to GND, collector = the low-side switch for a 12/24 V light
 * (JST-XH pin3 return, pin2 = +12V).  GPIO HIGH = transistor ON =
 * light ON (active-high).
 *
 * Pin map extracted from the generator's CHANNELS table:
 *   CH1..CH12 -> GPIO 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20
 *
 * Notes: GPIO8/GPIO9 are C3 strapping pins (CH9/CH10) — fine as
 * outputs after boot; all channels init LOW (lights off).  GPIO8 also
 * carries the SuperMini's on-board LED, which will mirror light/9.
 * No addressable RGB LED on the SuperMini -> neopixel_gpio = -1; the
 * R1 demo output is not wired on this board -> demo_gpio = -1 (the
 * demo bindings still publish, they just drive no pin).
 */

#ifdef ARDUINO

#include <Arduino.h>
#include "../board.h"

static const BoardProfile kProfile = {
    "lego-dots-12ch",
    /* demo_gpio     */ -1,
    /* neopixel_gpio */ -1,
};

const BoardProfile& board_profile() { return kProfile; }

static const ActuatorSpec kActuators[] = {
    /* ns          mode                  pin  (binary: scale unused) */
    { "light/1",   ActuatorMode::Binary,  0 },
    { "light/2",   ActuatorMode::Binary,  1 },
    { "light/3",   ActuatorMode::Binary,  2 },
    { "light/4",   ActuatorMode::Binary,  3 },
    { "light/5",   ActuatorMode::Binary,  4 },
    { "light/6",   ActuatorMode::Binary,  5 },
    { "light/7",   ActuatorMode::Binary,  6 },
    { "light/8",   ActuatorMode::Binary,  7 },
    { "light/9",   ActuatorMode::Binary,  8 },
    { "light/10",  ActuatorMode::Binary,  9 },
    { "light/11",  ActuatorMode::Binary, 10 },
    { "light/12",  ActuatorMode::Binary, 20 },
};

const ActuatorSpec* board_actuator_specs(size_t* count) {
    *count = sizeof(kActuators) / sizeof(kActuators[0]);
    return kActuators;
}

void board_outputs_init() {
    size_t n;
    const ActuatorSpec* specs = board_actuator_specs(&n);
    for (size_t i = 0; i < n; i++) {
        pinMode(specs[i].pin, OUTPUT);
        digitalWrite(specs[i].pin, LOW);    /* all lights off */
    }
}

void board_apply_demo_output(bool) { /* no demo pin on this board */ }

void board_drive_binary(const ActuatorSpec& spec, bool level) {
    digitalWrite(spec.pin, level ? HIGH : LOW);
}

void board_drive_pwm(const ActuatorSpec&, int) { /* none declared */ }

void board_drive_coils(const ActuatorSpec&, uint8_t) { /* no driver */ }

#endif /* ARDUINO */
