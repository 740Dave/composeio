/*
 * tm1637_arduino_seam.h — the SHARED Arduino GPIO seam for the TM1637 driver.
 *
 * WHY THIS EXISTS (root cause of DEFECT-TM1637-ALL-FOUR-7-SEGMENT-E11525).
 * The driver cyd/src/tm1637.{h,cpp} is a pure bit-bang whose PROTOCOL is
 * correct — proven byte-identical, edge-for-edge, to the known-good reference
 * library RobTillaart/TM1637_RT (LSB-first, same start/stop, same 9-clock ACK
 * sequence).  What differed was the SEAM'S DRIVE DISCIPLINE, and that is what
 * left every tube dark with ACK=NO:
 *
 *   - The original seams released a line HIGH by going Hi-Z (pinMode INPUT)
 *     and TRUSTING THE MODULE'S PULL-UP to raise it.  On the cheap 4-digit
 *     TM1637 breakout at 3.3 V that pull-up is absent/too weak, so a released
 *     line never reaches a valid HIGH: CLK presents no clean rising edge, the
 *     START condition (DIO falling while CLK high) never forms, nothing
 *     latches, and no ACK is ever asserted.  Uniform-dark, 0/4 ACK.
 *   - The reference library instead DRIVES THE LINE HIGH (push-pull) and only
 *     floats DIO to INPUT for the single ACK-read bit.  It reaches 3.3 V hard
 *     and fast regardless of any pull-up — which is why the SAME module on the
 *     SAME pins lit under the reference and stayed dark under ours.
 *     RobTillaart says it outright: relying on the pull-up makes the rise
 *     "too long ... to be read reliably."
 *
 * SO THIS SEAM IS PUSH-PULL: release() actively drives HIGH; the ONE place a
 * line is floated to Hi-Z is dio_read(), because the chip drives DIO low to
 * ACK and the master must be off the line to sample it.  This is the exact
 * discipline the reference proved on the bench.
 *
 * ⚠ THE HEADER'S "NEVER DRIVE HIGH" NOTE APPLIES TO A 5 V MODULE WITH REAL
 * 10K PULL-UPS (its VIH analysis is still correct there).  For the 3.3 V cheap
 * breakouts this fleet actually uses, push-pull is REQUIRED, not forbidden —
 * the brief contention against the chip's ACK pulldown is bounded to a single
 * half-period and is exactly what the proven-good reference does.
 *
 * SHARED ON PURPOSE.  Every board that bit-bangs a TM1637 (the ESP32-C3 smoke
 * test, the reactive_core S3 4-tube panel) should build its Tm1637Bus from
 * this ONE factory so the drive fix lives in a single place and cannot drift
 * back to Hi-Z per board.  The pin pair rides in ctx, so N tubes = N pin
 * structs sharing this code.
 *
 * Arduino-only (pinMode/digitalWrite/delayMicroseconds); guarded so the pure
 * host build (native_test) never sees it.
 */
#ifndef TM1637_ARDUINO_SEAM_H
#define TM1637_ARDUINO_SEAM_H

#if defined(ARDUINO) || defined(ESP_PLATFORM)

#include <Arduino.h>
#include "tm1637.h"

/* One tube's two pins.  Keep the instance alive for the life of the Tm1637
 * (it is referenced through Tm1637Bus.ctx). */
struct Tm1637ArduinoPins {
    uint8_t clk;
    uint8_t dio;
    /* Settle time after floating DIO to INPUT before sampling the ACK, in us.
     * The chip drives DIO low actively so it falls fast; a few us is ample. */
    uint8_t read_settle_us;
};

static inline void tm1637_ard_clk_low(void* c) {
    auto* p = (Tm1637ArduinoPins*)c;
    pinMode(p->clk, OUTPUT); digitalWrite(p->clk, LOW);
}
static inline void tm1637_ard_clk_release(void* c) {   /* push-pull HIGH */
    auto* p = (Tm1637ArduinoPins*)c;
    pinMode(p->clk, OUTPUT); digitalWrite(p->clk, HIGH);
}
static inline void tm1637_ard_dio_low(void* c) {
    auto* p = (Tm1637ArduinoPins*)c;
    pinMode(p->dio, OUTPUT); digitalWrite(p->dio, LOW);
}
static inline void tm1637_ard_dio_release(void* c) {   /* push-pull HIGH */
    auto* p = (Tm1637ArduinoPins*)c;
    pinMode(p->dio, OUTPUT); digitalWrite(p->dio, HIGH);
}
static inline bool tm1637_ard_dio_read(void* c) {      /* the ONE Hi-Z point */
    auto* p = (Tm1637ArduinoPins*)c;
    pinMode(p->dio, INPUT);                 /* float: chip drives DIO low to ACK */
    if (p->read_settle_us) delayMicroseconds(p->read_settle_us);
    return digitalRead(p->dio) != 0;
}
static inline void tm1637_ard_delay_us(void* /*c*/, uint32_t us) {
    delayMicroseconds(us);
}

/* Build a Tm1637Bus over `pins` (which the caller must keep alive). */
static inline Tm1637Bus tm1637_arduino_bus(Tm1637ArduinoPins& pins) {
    Tm1637Bus b;
    b.clk_low     = tm1637_ard_clk_low;
    b.clk_release = tm1637_ard_clk_release;
    b.dio_low     = tm1637_ard_dio_low;
    b.dio_release = tm1637_ard_dio_release;
    b.dio_read    = tm1637_ard_dio_read;
    b.delay_us    = tm1637_ard_delay_us;
    b.ctx         = &pins;
    return b;
}

#endif /* ARDUINO || ESP_PLATFORM */
#endif /* TM1637_ARDUINO_SEAM_H */
