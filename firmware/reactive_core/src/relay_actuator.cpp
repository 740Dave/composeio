/*
 * relay_actuator.cpp — [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-HAL] /
 * [SPEC:CXC-HAL] [CXC-HAL-ACTUATE].  See relay_actuator.h for the model and
 * the break-before-make + dead-time invariant.
 */

#include "relay_actuator.h"

#include <cstring>

void RelayActuator::_drive(int pin, bool energized) {
    if (pin < 0 || !_sink) return;
    /* polarity: an active-HIGH relay energizes on a HIGH level. */
    const bool level = _spec->active_high ? energized : !energized;
    _sink(_sink_user, pin, level);
}

bool RelayActuator::_resolve(const char* value, int& pin) const {
    if (!_spec || !value) return false;
    for (const auto& st : _spec->states) {
        if (st.value == value) { pin = st.pin; return true; }
    }
    return false;
}

void RelayActuator::configure(const RelayActuatorSpec* spec) {
    _spec = spec;
    _current.clear();
    _target_pin = -1;
    _armed = false;
    _due_ms = 0;
    _error.clear();
    if (!_spec) return;
    /* SAFE BOOT BASELINE + defined initial state: every owned relay pin
     * de-energized -> the fan is OFF the instant the pins are live.  Break-
     * before-make's precondition (all open) holds from the very first
     * on_value. */
    for (int p : _spec->pins) _drive(p, /*energized=*/false);
}

int RelayActuator::on_value(const char* value, uint32_t now_ms) {
    if (!_spec) return 0;
    _error.clear();

    int pin = -1;
    if (!_resolve(value, pin)) {
        _error = _spec->ns + ": unknown relay state '" +
                 std::string(value ? value : "") + "' withheld";
        return 0;                            /* leave relays as-is */
    }

    /* on-change: an unchanged state with no pending make is a no-op. */
    if (!_armed && _current == (value ? value : ""))
        return 0;

    /* BREAK: de-energize EVERY owned relay pin, unconditionally, before any
     * make.  This is the motor-safety core — no two speed relays ever
     * overlap.  A re-command mid-sequence simply re-breaks and re-arms. */
    int fired = 0;
    for (int p : _spec->pins) { _drive(p, /*energized=*/false); fired++; }

    _current = value ? value : "";
    _target_pin = pin;

    if (pin < 0) {
        /* off / pin-less state: all open, nothing to make. */
        _armed = false;
        return fired;
    }

    /* MAKE is deferred by the dead-time — armed for tick(). */
    _armed = true;
    _due_ms = now_ms + (uint32_t)(_spec->dead_time_ms > 0 ? _spec->dead_time_ms
                                                          : 0);
    return fired;
}

int RelayActuator::tick(uint32_t now_ms) {
    if (!_armed) return 0;
    /* signed wrap-safe compare (millis() wraps at ~49 days) */
    if ((int32_t)(now_ms - _due_ms) < 0) return 0;
    _armed = false;
    if (_target_pin < 0) return 0;           /* defensive: off has no make */
    _drive(_target_pin, /*energized=*/true);
    return 1;
}
