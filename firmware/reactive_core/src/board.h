/*
 * board.h — the R1/R2 board seam ([CXC-ER-C3] / [CXC-ER-C8] discipline).
 *
 * The reactive CORE (reactive_core.{h,cpp}) and the actuator HAL
 * (actuator_hal.{h,cpp}) never include this file — only the app shell
 * (main.cpp) does.  A new board = a new boards/<name>.cpp implementing
 * these functions; the core, the HAL, and the shell logic compile
 * unchanged (the same seam idiom as the CYD's include/board_profile.h).
 *
 * R2 adds the declarative OUTPUT-actuator surface ([CXC-ER-HAL] /
 * [CXC-HAL-ACTUATE]): the board publishes a static ActuatorSpec table
 * (its descriptor set) and implements three drive primitives:
 *   board_drive_binary  Tier 1: a GPIO level
 *   board_drive_pwm     Tier 1: a GPIO duty (LEDC)
 *   board_drive_coils   Tier 2: a 4-bit IN1..IN4 coil pattern to the
 *                       stepper CHANNEL spec.pin, through whatever
 *                       driver the board has (MCP23017 -> L293D on
 *                       rack_triple_gauge).  Boards without a driver
 *                       no-op it.
 */

#ifndef REACTIVE_BOARD_H
#define REACTIVE_BOARD_H

#include <cstddef>

#include "actuator_hal.h"   /* pure C++: ActuatorSpec descriptor */

struct BoardProfile {
    const char* name;        /* human-readable board id                */
    int demo_gpio;           /* plain GPIO the R1 demo output drives,
                              * -1: none on this board                 */
    int neopixel_gpio;       /* on-board addressable RGB LED, -1: none */
};

const BoardProfile& board_profile();

/* Configure the output pins / driver chips.  Call once from setup(). */
void board_outputs_init();

/* Drive the R1 demo output: the plain GPIO high/low plus the on-board
 * RGB LED (green/dark) when the board has one. */
void board_apply_demo_output(bool on);

/* --- R2: the board's declarative actuator table + drive primitives -- */

/* The static descriptor table ([CXC-HAL-OPEN]: profile owns the pins). */
const ActuatorSpec* board_actuator_specs(size_t* count);

/* Tier 1 — direct: *level* is the physical pin level (polarity already
 * applied by the HAL). */
void board_drive_binary(const ActuatorSpec& spec, bool level);

/* Tier 1 — direct PWM: duty 0..spec.out_max on GPIO spec.pin. */
void board_drive_pwm(const ActuatorSpec& spec, int duty);

/* Tier 2 — via driver: 4-bit coil pattern (bit0=IN1..bit3=IN4) to
 * stepper channel spec.pin. */
void board_drive_coils(const ActuatorSpec& spec, uint8_t pattern);

#endif /* REACTIVE_BOARD_H */
