/*
 * boards/qualia_s3.cpp — Adafruit Qualia ESP32-S3 board profile (R4).
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-BOARDS] — the THIRD ESP32 variant
 * (Xtensa LX7), run HEADLESS: the Qualia's RGB666 parallel panel is R5
 * and is NOT touched here.
 *
 * This is a new boards/*.cpp behind the exact R1/R2 seam (board.h) — the
 * reactive CORE (reactive_core.{h,cpp}, compiled by reference) and the
 * app shell (main.cpp) are byte-untouched; only this profile is new,
 * proving [CXC-ER-C3]/[CXC-ER-C8] "board differences isolated to the
 * profile" on a third silicon.
 *
 * R4 scope = the headless reactive loop only: a demo GPIO the
 * `demo/led` binding drives, NO actuators (the R2 ActuatorSpec table is
 * intentionally empty — the actuator HAL then has nothing to subscribe
 * or drive, so the S3 runs as a pure headless reactive node exactly like
 * RenderApp(headless=True)).
 *
 *   demo_gpio = GPIO17 — a general-purpose S3 pin, no strapping role, not
 *   on the native-USB (D+/D- 19/20) or default UART0 (43/44) lines.  On
 *   the Qualia dev board this is a header/pad pin; probe with an LED or
 *   scope.  It is a DEMO placeholder — the essential R4 verification is
 *   the DSL corpus (env:qualia_s3_dsl_test), which uses no GPIO — so if
 *   the bonus reactive loop is wired, repin here to a pad free on your
 *   Qualia and rebuild; the core/shell are unaffected.
 *
 *   neopixel_gpio = -1 — the generic S3 DevKitC-1 puts a NeoPixel on
 *   GPIO48, but on the Qualia RGB666 that pin belongs to the panel; we
 *   do NOT drive an unknown-on-this-board pin at boot.  The demo GPIO
 *   plus the serial self-report make the loop legible without it.
 */

#ifdef ARDUINO

#include <Arduino.h>
#include "../board.h"

static const BoardProfile kProfile = {
    "qualia-esp32s3",
    /* demo_gpio     */ 17,
    /* neopixel_gpio */ -1,
};

const BoardProfile& board_profile() { return kProfile; }

/* R4 headless core: no actuators (R2 boards are the C3 lego_dots /
 * rack_triple_gauge).  An empty table = the HAL subscribes nothing and
 * drives nothing; the S3 is a pure reactive/DSL node. */
static const ActuatorSpec kActuators[] = {};

const ActuatorSpec* board_actuator_specs(size_t* count) {
    *count = 0;
    return kActuators;
}

void board_outputs_init() {
    if (kProfile.demo_gpio >= 0) {
        pinMode(kProfile.demo_gpio, OUTPUT);
        digitalWrite(kProfile.demo_gpio, LOW);
    }
    if (kProfile.neopixel_gpio >= 0)
        neopixelWrite(kProfile.neopixel_gpio, 0, 0, 0);
}

void board_apply_demo_output(bool on) {
    if (kProfile.demo_gpio >= 0)
        digitalWrite(kProfile.demo_gpio, on ? HIGH : LOW);
    if (kProfile.neopixel_gpio >= 0)
        neopixelWrite(kProfile.neopixel_gpio, 0, on ? 32 : 0, 0);
}

/* No actuators on the R4 headless S3 — the drive primitives are part of
 * the seam (main.cpp's actuatorSink is generic) but are never reached
 * because the ActuatorSpec table is empty. */
void board_drive_binary(const ActuatorSpec&, bool) {}
void board_drive_pwm(const ActuatorSpec&, int) {}
void board_drive_coils(const ActuatorSpec&, uint8_t) {}

#endif /* ARDUINO */
