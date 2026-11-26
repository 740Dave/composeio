/*
 * signal_conditioning.h — THE ONE value-conditioning pipeline.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-DEVICE] [CXC-HAL-CONDITION]
 *
 * Every analog input published by the reactive-core controller tier goes
 * through condition_sample():
 *   invert -> clamp -> EMA -> scale/band -> quantize(step) -> deadband.
 * It is a PURE function with caller-owned state, so it host-compiles and
 * host-tests in env:native_test with no Arduino and no hardware.
 *
 * WHY THIS IS A FILE AND NOT AN INLINE BLOCK IN loop():
 * it used to be an inline block in controller_main.cpp, which env:native_test
 * cannot compile.  There was therefore NO SEAM AT WHICH THE ARITHMETIC COULD
 * BE TESTED, and a defect that lived in the shared path was invisible to every
 * per-channel check.  The extraction is the fix for that; the arithmetic
 * corrections below are the fix for what it was hiding.
 *
 * ⛔ INVARIANT [CXC-HAL-CONDITION-RAILS]: BOTH RAILS MUST BE EMITTABLE.
 * A conditioning pipeline must be able to emit both 0 and out_max, for EVERY
 * combination of invert / deadband / ema, and a quiet-bus optimization must
 * never make a rail unreachable.  Full-off and full-on are SEMANTIC values —
 * a consumer reads "255" as "the lever is at the stop", and a pipeline that
 * can only ever say 254 is lying about a physical fact.  See the two rules
 * this invariant forced, below.
 */

#ifndef SIGNAL_CONDITIONING_H
#define SIGNAL_CONDITIONING_H

#include "controller_config.h"   /* SensorSpec */

/* Per-sensor conditioning state, owned by the caller (one per sensor).
 * Explicit state is what makes the function pure and therefore testable. */
struct ConditionState {
    float ema = 0.0f;        /* EMA accumulator, in RAW ADC units */
    int   last_val = 0;      /* last value actually EMITTED */
    bool  have_last = false; /* has anything been emitted yet? */
};

struct ConditionResult {
    int  value = 0;    /* the conditioned output code, 0..out_max */
    bool emit = false; /* did the deadband gate pass it? */
};

/* Condition one raw ADC reading for `s`, advancing `st`.
 * `st` is mutated ONLY when the sample is emitted (plus the EMA glide). */
ConditionResult condition_sample(const SensorSpec& s, int raw, ConditionState& st);

#endif /* SIGNAL_CONDITIONING_H */
