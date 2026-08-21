/*
 * x27168.cpp — see x27168.h.  Pure C++; host-compiled by env:native_gauge.
 */

#include "x27168.h"

const uint8_t kX27HalfStep[8] = { 0x1, 0x5, 0x4, 0x6, 0x2, 0xA, 0x8, 0x9 };

void X27168::configure(int travel_half_steps, int home_half_steps,
                       int rest_offset, int home_rate_hz, int slew_rate_hz) {
    travel_  = travel_half_steps > 1 ? travel_half_steps : 1;
    home_    = home_half_steps   > 1 ? home_half_steps   : 1;
    rest_    = rest_offset       > 0 ? rest_offset       : 0;
    home_hz_ = home_rate_hz      > 1 ? home_rate_hz      : 1;
    slew_hz_ = slew_rate_hz      > 1 ? slew_rate_hz      : 1;
}

void X27168::reset() {
    coils_ = kX27CoilsOff;
    seq_ = 0; pos_ = 0; target_ = 0;
    phase_ = X27Phase::Idle;
    next_due_ = 0; home_done_ = 0; rest_done_ = 0;
}

void X27168::step(int dir) {
    seq_ = (seq_ + (dir > 0 ? 1 : 7)) & 7;      /* +1 / -1 mod 8 */
    coils_ = kX27HalfStep[seq_];
    pos_ += dir;
}

void X27168::start_homing(uint32_t now_ms) {
    /*
     * The needle is wherever it is; we do not know and cannot ask.  pos_ is
     * therefore meaningless until the sweep completes, and is deliberately
     * NOT zeroed here — it is zeroed at the END, when it becomes true.
     * Zeroing it now would make position() report a confident 0 for the
     * whole 5.4 s sweep, which is a number that looks like an answer.
     */
    phase_     = X27Phase::Homing;
    home_done_ = 0;
    rest_done_ = 0;
    target_    = 0;
    next_due_  = now_ms;
}

void X27168::set_target(int half_steps) {
    /*
     * REFUSED, not queued, while not Ready.  A queued pre-homing target
     * would be applied against a zero that does not exist yet; refusing it
     * means an early publisher sees its value dropped (visible: the needle
     * stays at zero) rather than honoured against fiction (invisible: the
     * needle reads plausibly and is wrong by the homing offset forever).
     */
    if (phase_ != X27Phase::Ready) return;
    if (half_steps < 0) half_steps = 0;
    if (half_steps > travel_) half_steps = travel_;
    target_ = half_steps;
}

void X27168::set_unit(float unit) {
    if (unit < 0.0f) unit = 0.0f;
    if (unit > 1.0f) unit = 1.0f;
    set_target((int)(unit * (float)travel_ + 0.5f));
}

bool X27168::tick(uint32_t now_ms) {
    if (phase_ == X27Phase::Idle) return false;

    /* Signed compare so a wrapped uint32 (49.7 days) does not stall the
     * machine forever — the classic embedded timer bug, and on a gauge it
     * would present as "the needles simply stopped" with every other
     * signal healthy. */
    if ((int32_t)(now_ms - next_due_) < 0) return false;

    const int rate = (phase_ == X27Phase::Ready) ? slew_hz_ : home_hz_;
    const uint32_t period_ms = (uint32_t)(1000 / (rate > 0 ? rate : 1));
    /* A rate above 1000 Hz would floor to a 0 ms period and spin; the
     * board's rates are 400/800 so this is a guard, not a path. */
    next_due_ = now_ms + (period_ms ? period_ms : 1);

    switch (phase_) {
    case X27Phase::Homing:
        if (home_done_ < home_) {
            /* DOWN, into the low mechanical stop.  Once the needle arrives
             * the remaining commands SLIP — that slip is the mechanism, not
             * a fault, and it is why the count is deliberately larger than
             * the travel. */
            step(-1);
            home_done_++;
            return true;
        }
        pos_   = 0;                       /* the stop is now the origin */
        phase_ = X27Phase::Resting;
        rest_done_ = 0;
        return false;

    case X27Phase::Resting:
        if (rest_done_ < rest_) {
            step(+1);
            rest_done_++;
            return true;
        }
        /* THIS is zero: rest_ half-steps off the stop, so the needle has
         * somewhere to go below the printed zero and no coil is holding
         * against a jam. */
        pos_    = 0;
        target_ = 0;
        phase_  = X27Phase::Ready;
        return false;

    case X27Phase::Ready:
        if (pos_ < target_) { step(+1); return true; }
        if (pos_ > target_) { step(-1); return true; }
        return false;                     /* at rest: hold the pattern */

    default:
        return false;
    }
}

void x27_pack_ports(const X27168& m1, const X27168& m2, const X27168& m3,
                    uint8_t* gpioa, uint8_t* gpiob) {
    *gpioa = (uint8_t)((m1.coils() & 0x0F) | ((m2.coils() & 0x0F) << 4));
    *gpiob = (uint8_t)(m3.coils() & 0x0F);   /* top nibble = J_EXP_MCP: 0 */
}
