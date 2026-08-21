/*
 * CH32V203C8T6 bring-up blink.
 *
 * Board: nanoCH32V203 (MuseLab/wuxx) = CH32V203C8T6 (QingKe V4B RISC-V).
 * Goal: prove code executes. The user-LED GPIO is unknown, so we drive a broad
 * set of pins as push-pull outputs and toggle them at ~2 Hz. Whatever pin the
 * (blue) LED sits on will blink.
 *
 * Clock: Arduino core startup runs SystemInit() -> PLL from 8 MHz HSE -> 144 MHz.
 * delay() is SysTick-calibrated to SystemCoreClock, so delay(250) == 250 ms.
 *
 * Pins driven (register-level, read-modify-write so we NEVER disturb reserved pins):
 *   GPIOA: PA0..PA10, PA15   (mask 0x87FF) -- excludes PA11/PA12 USB D-/D+,
 *                                              PA13/PA14 SWDIO/SWCLK
 *   GPIOB: PB0..PB15         (mask 0xFFFF)
 *   GPIOC: PC0..PC13         (mask 0x3FFF) -- excludes PC14/PC15 LSE crystal
 *   GPIOD: NOT TOUCHED       -- PD0/PD1 HSE crystal / limited port D
 */

#include <Arduino.h>
#include "ch32v20x.h"

// Which pins to drive on each port (1 = drive, 0 = leave untouched).
#define MASK_A  0x87FFu   // PA0..PA10, PA15
#define MASK_B  0xFFFFu   // all of PB
#define MASK_C  0x3FFFu   // PC0..PC13

// Configure the pins selected by `mask` on `port` as push-pull output, 50 MHz.
// Read-modify-write each 4-bit field: MODE=0b11 (50MHz out), CNF=0b00 (PP) => 0x3.
static void configPortOutputs(GPIO_TypeDef *port, uint16_t mask) {
    for (uint8_t pin = 0; pin < 16; pin++) {
        if (!(mask & (1u << pin))) continue;
        if (pin < 8) {
            uint32_t shift = pin * 4u;
            port->CFGLR = (port->CFGLR & ~(0xFu << shift)) | (0x3u << shift);
        } else {
            uint32_t shift = (pin - 8u) * 4u;
            port->CFGHR = (port->CFGHR & ~(0xFu << shift)) | (0x3u << shift);
        }
    }
}

void setup() {
    // Enable GPIOA/B/C peripheral clocks on APB2.
    RCC->APB2PCENR |= RCC_IOPAEN | RCC_IOPBEN | RCC_IOPCEN;

    configPortOutputs(GPIOA, MASK_A);
    configPortOutputs(GPIOB, MASK_B);
    configPortOutputs(GPIOC, MASK_C);

    // Start with all driven pins low.
    GPIOA->OUTDR &= ~((uint32_t)MASK_A);
    GPIOB->OUTDR &= ~((uint32_t)MASK_B);
    GPIOC->OUTDR &= ~((uint32_t)MASK_C);
}

void loop() {
    // ~2 Hz: toggle every driven pin, 250 ms on / 250 ms off.
    GPIOA->OUTDR ^= (uint32_t)MASK_A;
    GPIOB->OUTDR ^= (uint32_t)MASK_B;
    GPIOC->OUTDR ^= (uint32_t)MASK_C;
    delay(250);
}
