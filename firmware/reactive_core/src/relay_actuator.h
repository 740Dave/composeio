/*
 * relay_actuator.h — the declarative ENUM->RELAY-BANK output actuator.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-HAL] + [SPEC:CXC-HAL] [CXC-HAL-ACTUATE]
 * (Output Tier 1 — direct GPIO, break-before-make relay bank).
 *
 * The multi-pin dual of the binary actuator ([CXC-HAL-ACTUATE]).  Where
 * ActuatorHal::Binary maps ONE namespace to ONE pin, a relay bank maps ONE
 * SEMANTIC ENUM namespace (e.g. `office_fan/speed` = off|low|medium|high) to
 * a SET of relay pins through a value->pin state map, with a hard motor-
 * safety invariant baked in:
 *
 *   BREAK-BEFORE-MAKE + DEAD-TIME.  On every state change the actuator
 *   FIRST de-energizes EVERY owned relay pin, waits `dead_time_ms`, THEN
 *   energizes the single target pin for the new state.  It is NEVER possible
 *   for two speed relays to be closed at once (that would back-feed the
 *   motor windings on a speed-tap AC fan).  The dead-time is realized by a
 *   tick() sequencer, exactly like the stepper pacing in actuator_hal.
 *
 * Config-driven (SDR credo): the pins, the value->pin map, the polarity, and
 * the dead-time all live in the fetched .compose.yaml `relay_actuator:` block
 * (controller_config lowers it into a RelayActuatorSpec).  NO pins are baked
 * in firmware.
 *
 * PURE C++ — no Arduino, no pins touched here.  The actuator computes
 * (pin, level) drive commands and hands them to a sink; the app shell routes
 * them to digitalWrite + a serial self-report.  Host-testable in
 * env:native_test exactly like actuator_hal / reactive_core.
 *
 * SEAM (deferred, [CXC-HAL] reactive sensor rules): the subscribed enum is a
 * plain namespace.  Today it is published by a human / an Intelligence / any
 * bus peer.  Tomorrow a local ReactiveCore binding could DERIVE it from a
 * sensor namespace (e.g. HEPA speed <- particulate) — the actuator is
 * unchanged; it just subscribes the final enum.  Nothing here forecloses it.
 */

#ifndef RELAY_ACTUATOR_H
#define RELAY_ACTUATOR_H

#include <cstddef>
#include <cstdint>
#include <string>

#include "controller_config.h"   /* RelayActuatorSpec / RelayState */

class RelayActuator {
public:
    /* Drive a single relay pin to a PHYSICAL level (polarity already
     * applied).  Bound to digitalWrite in firmware, to a recorder in the
     * host test. */
    using Sink = void (*)(void* user, int pin, bool level);

    void set_sink(Sink sink, void* user) { _sink = sink; _sink_user = user; }

    /* Adopt the spec (borrowed — must outlive the actuator; the shell holds
     * it in g_cfg).  Emits the SAFE BOOT BASELINE: every owned relay pin
     * de-energized, so the fan is OFF the instant the pins are configured.
     * No value is applied yet (current() == ""); the shell asserts the
     * default with on_value(spec.default_value, now). */
    void configure(const RelayActuatorSpec* spec);

    bool present() const { return _spec != nullptr; }
    const char* ns() const { return _spec ? _spec->ns.c_str() : ""; }

    /* A new enum value arrived (external bus arrival on ns()).  If it names
     * a known state AND differs from the current state, this BREAKS (de-
     * energizes every owned pin now) and, when the target state energizes a
     * pin, arms the MAKE for now_ms + dead_time_ms.  An unknown value is
     * WITHHELD (last_error set, relays left as-is — never thrash the motor
     * on a bad command).  Returns the number of drive commands emitted (the
     * break emits; the make fires later from tick()). */
    int on_value(const char* value, uint32_t now_ms);

    /* Complete a pending break-before-make: once now_ms >= the armed due
     * time, energize the target pin.  Call every loop().  Returns commands
     * fired (0 or 1). */
    int tick(uint32_t now_ms);

    /* True while a make is armed (dead-time in progress). */
    bool sequencing() const { return _armed; }

    /* The last applied enum value ("" before the first on_value). */
    const char* current() const { return _current.c_str(); }

    /* The pin the current/pending state energizes (-1 = none, i.e. off). */
    int target_pin() const { return _target_pin; }

    /* Last withheld-drive error ("" when none). */
    const char* last_error() const { return _error.c_str(); }

private:
    /* Resolve an enum value to its energized pin.  Returns true when the
     * value is a known state; *pin* is the energized GPIO (>=0) or -1 for a
     * pin-less state (off). */
    bool _resolve(const char* value, int& pin) const;

    /* Emit the physical level for one pin (polarity applied here). */
    void _drive(int pin, bool energized);

    const RelayActuatorSpec* _spec = nullptr;
    Sink _sink = nullptr;
    void* _sink_user = nullptr;

    std::string _current;      /* last applied enum value                */
    int _target_pin = -1;      /* pin the current/pending state energizes */
    bool _armed = false;       /* a make is pending (dead-time running)   */
    uint32_t _due_ms = 0;      /* when the make fires                     */
    std::string _error;
};

#endif /* RELAY_ACTUATOR_H */
