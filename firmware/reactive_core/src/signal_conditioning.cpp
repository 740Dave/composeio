/* signal_conditioning.cpp — see signal_conditioning.h for the invariant. */

#include "signal_conditioning.h"

ConditionResult condition_sample(const SensorSpec& s, int raw, ConditionState& st) {
    const int full = s.adc_max > 0 ? s.adc_max : 4095;

    /* leaf-owned invert (P1b) */
    if (s.invert) raw = full - raw;
    if (raw < 0) raw = 0; else if (raw > full) raw = full;

    /* EMA denoiser on the raw ADC (0 alpha = passthrough). */
    float sample = (float)raw;
    if (s.ema_alpha > 0.0f) {
        if (!st.have_last) st.ema = sample;   /* seed on first read */
        st.ema = s.ema_alpha * sample + (1.0f - s.ema_alpha) * st.ema;
        sample = st.ema;
    }

    /* RULE 1 — ROUND, NEVER TRUNCATE.  [CXC-HAL-CONDITION-RAILS]
     * `+ full/2` before the divide is not a nicety, it is what makes the
     * scale an exact MIRROR: f(x) + f(full-x) == out_max for every input
     * code.  C's truncating divide is NOT mirror-symmetric -- it biases
     * every code DOWNWARD, which costs nothing at the bottom rail (0 is
     * reached early and stays) and costs the TOP RAIL ENTIRELY: under
     * truncation `val == out_max` requires raw == full EXACTLY, a preimage
     * of ONE ADC code out of 4096.  An UNINVERTED channel survives that
     * because the ADC hard-saturates at full scale, so a whole band of
     * input voltage pins raw to 4095; an INVERTED channel must instead
     * reach the ADC's noisy, offset zero end, where code 0 is not
     * reliably produced.  THAT is the whole asymmetry -- it is created by
     * the arithmetic, not by the device.  Rounding widens the top code's
     * preimage from 1 to 9 codes and removes the systematic downward bias
     * across the entire range, on every channel.
     * This also converges the INPUT path onto the semantic the OUTPUT
     * path already implements -- actuator_hal.cpp `_map_scaled` uses
     * llround(), and python/composeio/maker/mcu_config.py already
     * DOCUMENTS the two as "the same clamped linear map". They were not. */

    /* RULE 3 — THE SAFETY BANDS.  [CXC-HAL-CONDITION-BANDS]
     * in_min/in_max are the calibrated raw-code span, and they complete the
     * convergence Rule 1 started: this is now LITERALLY the map
     * actuator_hal.cpp `_map_scaled` runs, in_min..in_max -> 0..out_max,
     * clamped and rounded.  The input path finally has the vocabulary that
     * mcu_config.py has been claiming it had.
     *
     * ⛔ THE BANDS ARE A SAFETY MECHANISM, NOT DEAD RANGE.  Rule 1 fixed the
     * arithmetic's contribution to rail-unreachability; it could not fix the
     * DEVICE's.  The host test's boundary report measures the residual at
     * ~8 codes of ADC zero-offset -- past that the rail's preimage is a code
     * the ADC never produces, and NO rounding scheme reaches it.  Clamping
     * to a calibrated span moves rail-reachability from "the arithmetic is
     * unbiased" (an argument) to "each rail owns a whole neighbourhood of
     * raw codes" (a structure).  A future reader will see the end codes all
     * mapping to the same output and read it as wasted range: it is not, it
     * is the only thing standing between a pot at its mechanical stop and a
     * bus that reports 97%.  Removing it re-opens the defect.
     *
     * Unset (0/0) means in_min=0, in_max=full -- byte-identical to the
     * pre-band map, which is what makes this additive on every deployed
     * wire that declares neither. */
    int lo = s.in_min > 0 ? s.in_min : 0;
    int hi = (s.in_max > 0 && s.in_max <= full) ? s.in_max : full;
    if (lo >= hi) { lo = 0; hi = full; }   /* degenerate config -> no band */

    long x = (long)(sample + 0.5f);
    if (x < lo) x = lo; else if (x > hi) x = hi;
    const long span = (long)hi - (long)lo;

    int val = (int)((((x - lo) * s.out_max) + span / 2) / span);
    if (val < 0) val = 0;
    if (val > s.out_max) val = s.out_max;

    const int db = s.deadband > 0 ? s.deadband : 1;

    /* RULE 2 — A QUIET-BUS OPTIMIZATION MAY NEVER STRAND A RAIL.
     * The deadband suppresses |delta| < db.  Sitting at out_max-1 with
     * db=2, a computed out_max is delta 1 and was suppressed FOREVER --
     * an ABSORBING state one code short of the stop.  0 and out_max are
     * SEMANTIC: a consumer reads out_max as "the lever is against the
     * stop", so a pipeline that can only ever say 254 is misreporting a
     * physical fact.  Rails therefore escape the deadband.
     * ⛔ Guarded on `val != st.last_val`, or holding at a rail would
     * re-publish every sample and reintroduce exactly the chatter the
     * deadband exists to kill.
     *
     * ⛔ THE ESCAPE IS BIDIRECTIONAL, AND THE FIRST VERSION OF IT WAS NOT.
     * This test used to read `(val <= 0 || val >= out_max)` -- the NEW value
     * only.  That cures the absorbing state on ENTRY and leaves its exact
     * MIRROR on EXIT: latched AT out_max with db=2, a computed out_max-1 is
     * delta 1 and is suppressed FOREVER, so a lever that has LEFT the stop
     * goes on reporting "against the stop".  Same lie, sign flipped, and the
     * more dangerous half -- the original defect under-reported a control at
     * maximum; this one OVER-reports a control that is no longer there.
     * A rail is not just a value to be REACHED, it is a state to be LEFT, so
     * the predicate is over the TRANSITION (either endpoint), not the
     * destination.  Measured on the live dual-throttle (db=2, 20 Hz): the top
     * led_bar segment held FULL for up to 1.6 s after release, and forever
     * with the lever parked one code down. */
    const bool touches_rail = (val <= 0 || val >= s.out_max ||
                               st.last_val <= 0 || st.last_val >= s.out_max);

    ConditionResult r;
    r.value = val;
    r.emit = (!st.have_last ||
              val - st.last_val >= db ||
              st.last_val - val >= db ||
              (touches_rail && val != st.last_val));
    if (r.emit) { st.last_val = val; st.have_last = true; }
    return r;
}
