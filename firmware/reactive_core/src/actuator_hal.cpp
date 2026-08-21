/*
 * actuator_hal.cpp — [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-HAL], R2.
 * See actuator_hal.h for the model and the pinned semantics.
 */

#include "actuator_hal.h"

#include <cmath>
#include <cstring>

/* Full-step bipolar sequence (coil A = IN1/IN2, coil B = IN3/IN4):
 *   pos&3 == 0:  A+ B+   IN1,IN3        0b0101
 *   pos&3 == 1:  A- B+   IN2,IN3        0b0110
 *   pos&3 == 2:  A- B-   IN2,IN4        0b1010
 *   pos&3 == 3:  A+ B-   IN1,IN4        0b1001
 * Both coils always energized -> full torque + holding torque at rest. */
const uint8_t ActuatorHal::kFullStep[4] = { 0x05, 0x06, 0x0A, 0x09 };

void ActuatorHal::configure(const ActuatorSpec* specs, size_t count) {
    _states.clear();
    _namespaces.clear();
    for (size_t i = 0; i < count; i++) {
        State s;
        s.spec = &specs[i];
        if (s.spec->mode == ActuatorMode::Stepper && s.spec->home_on_boot) {
            /* No feedback: assume the worst-case start and walk the
             * full scale against the mechanical stop -> position 0 is
             * then true zero (the gauge-stepper homing idiom). */
            s.position = s.spec->out_max;
            s.target = 0;
        }
        _states.push_back(s);

        bool seen = false;
        for (const auto& n : _namespaces)
            if (n == s.spec->ns) { seen = true; break; }
        if (!seen) _namespaces.push_back(s.spec->ns);
    }
    /* Report homing intents (sink may already be set). */
    for (const auto& s : _states) {
        if (s.spec->mode == ActuatorMode::Stepper &&
            s.position != s.target) {
            ActuatorCommand cmd;
            cmd.kind = ActuatorCommand::Kind::StepperTarget;
            cmd.target = s.target;
            cmd.position = s.position;
            _emit(s, cmd);
        }
    }
}

void ActuatorHal::_emit(const State& s, const ActuatorCommand& cmd) {
    if (_sink) _sink(_sink_user, *s.spec, cmd);
}

/* Linear scale in_min..in_max -> 0..out_max, clamped, half-away-from-
 * zero rounded.  false (+ last_error) on a non-numeric/NaN value or a
 * degenerate scale — the drive is WITHHELD, never garbage. */
bool ActuatorHal::_map_scaled(const State& s, const DslValue& v, int& out) {
    double x;
    if (!dsl_try_number(v, x) || x != x) {
        _error = std::string(s.spec->ns) + ": non-numeric value withheld";
        return false;
    }
    const double span = s.spec->in_max - s.spec->in_min;
    if (span == 0.0) {
        _error = std::string(s.spec->ns) + ": degenerate in_min==in_max";
        return false;
    }
    double frac = (x - s.spec->in_min) / span;
    if (frac < 0.0) frac = 0.0;
    if (frac > 1.0) frac = 1.0;
    out = (int)llround(frac * (double)s.spec->out_max);
    return true;
}

int ActuatorHal::on_value(const char* ns, const DslValue& v) {
    if (!ns || !*ns) return 0;
    _error.clear();
    int fired = 0;

    for (auto& s : _states) {
        if (strcmp(s.spec->ns, ns) != 0) continue;

        switch (s.spec->mode) {
        case ActuatorMode::Binary: {
            const bool on = dsl_truthy(v);
            if (s.has_last && s.last_on == on) break;   /* on-change */
            s.has_last = true;
            s.last_on = on;
            ActuatorCommand cmd;
            cmd.kind = ActuatorCommand::Kind::Binary;
            cmd.on = on;
            cmd.level = s.spec->active_high ? on : !on;
            _emit(s, cmd);
            fired++;
            break;
        }
        case ActuatorMode::Pwm: {
            int duty;
            if (!_map_scaled(s, v, duty)) break;        /* withheld  */
            if (s.has_last && s.last_duty == duty) break;
            s.has_last = true;
            s.last_duty = duty;
            ActuatorCommand cmd;
            cmd.kind = ActuatorCommand::Kind::PwmDuty;
            cmd.duty = duty;
            _emit(s, cmd);
            fired++;
            break;
        }
        case ActuatorMode::Stepper: {
            int target;
            if (!_map_scaled(s, v, target)) break;      /* withheld  */
            if (s.has_last && s.target == target) break;
            s.has_last = true;
            if (target == s.target) break;  /* first value == homing
                                             * target: nothing new    */
            ActuatorCommand cmd;
            cmd.kind = ActuatorCommand::Kind::StepperTarget;
            cmd.target = target;
            cmd.position = s.position;
            s.target = target;
            _emit(s, cmd);
            fired++;
            break;
        }
        }
    }
    return fired;
}

int ActuatorHal::tick(uint32_t now_ms) {
    int fired = 0;
    for (auto& s : _states) {
        if (s.spec->mode != ActuatorMode::Stepper) continue;
        if (s.position == s.target) { s.due_armed = false; continue; }
        /* signed wrap-safe pacing (millis() wraps at ~49 days) */
        if (s.due_armed && (int32_t)(now_ms - s.next_due_ms) < 0) continue;

        const int dir = s.target > s.position ? +1 : -1;
        s.position += dir;
        s.next_due_ms = now_ms + (uint32_t)s.spec->step_interval_ms;
        s.due_armed = true;

        ActuatorCommand cmd;
        cmd.kind = ActuatorCommand::Kind::StepperStep;
        cmd.position = s.position;
        cmd.target = s.target;
        cmd.direction = dir;
        cmd.coils = kFullStep[s.position & 3];
        _emit(s, cmd);
        fired++;

        if (s.position == s.target) {
            ActuatorCommand done;
            done.kind = ActuatorCommand::Kind::StepperArrived;
            done.position = s.position;
            done.target = s.target;
            _emit(s, done);
            fired++;
            s.due_armed = false;
        }
    }
    return fired;
}

bool ActuatorHal::stepping() const {
    for (const auto& s : _states)
        if (s.spec->mode == ActuatorMode::Stepper && s.position != s.target)
            return true;
    return false;
}

int ActuatorHal::stepper_position(const char* ns) const {
    for (const auto& s : _states)
        if (s.spec->mode == ActuatorMode::Stepper &&
            strcmp(s.spec->ns, ns) == 0)
            return s.position;
    return -1;
}

int ActuatorHal::stepper_target(const char* ns) const {
    for (const auto& s : _states)
        if (s.spec->mode == ActuatorMode::Stepper &&
            strcmp(s.spec->ns, ns) == 0)
            return s.target;
    return -1;
}
