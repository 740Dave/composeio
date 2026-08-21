/*
 * actuator_hal.h — the declarative OUTPUT-actuator HAL, R2.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-HAL] [CXC-ER-C4]
 * + [SPEC:CXC-HAL] [CXC-HAL-ACTUATE] (Output Tier 1 / Tier 2).
 *
 * The output dual of a sensor descriptor: a board profile declares a
 * table of ActuatorSpec rows — {namespace, mode, pin/channel, scale} —
 * and the HAL subscribes each namespace and DRIVES on change:
 *
 *   Tier 1 (direct)      Binary  value -> canon truthy -> GPIO level
 *                        Pwm     value -> numeric scale -> duty 0..out_max
 *   Tier 2 (via driver)  Stepper value -> numeric scale -> target STEP
 *                                position; a full-step sequencer walks
 *                                current -> target emitting 4-bit coil
 *                                patterns (IN1..IN4) the board routes
 *                                through its driver (MCP23017 -> L293D
 *                                on rack_triple_gauge)
 *
 * Engine-semantics parity (the same view a binding result gets):
 *   - Binary uses dsl_truthy() — the [CXC-BIND] binary canon: bool ->
 *     itself, number -> != 0, string -> canon(s) not in {"off", ""}
 *     (so "on"/"ON"/"true"/"yes"/"1" all switch ON).
 *   - Pwm/Stepper use dsl_try_number() — bool -> 1/0, numeric strings
 *     parse; a non-numeric or NaN value WITHHOLDS the drive (warn
 *     path, see last_error()), mirroring the core's eval-error
 *     withhold.  Scale is linear in_min..in_max -> 0..out_max, clamped
 *     at both ends, half-away-from-zero rounded (llround).
 *   - drive-on-change: an unchanged mapped output is suppressed; the
 *     FIRST mapped value always fires (defined initial state — the
 *     runtime._publish_outputs idiom).
 *
 * PURE C++ — no Arduino, no pins touched here.  The HAL computes
 * ActuatorCommands and hands them to a sink; the app shell routes them
 * to the board seam (board.h drive primitives) + serial self-report.
 * Host-testable in env:native_test exactly like the core.
 */

#ifndef ACTUATOR_HAL_H
#define ACTUATOR_HAL_H

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "dsl.h"    /* canonical: cyd/src/dsl.h via -I */

enum class ActuatorMode : uint8_t { Binary, Pwm, Stepper };

/* One declarative actuator row.  Lives in the board profile as a
 * static table ([CXC-HAL-OPEN] descriptor-home: profile = pins;
 * a config-supplied table can feed the same struct later). */
struct ActuatorSpec {
    const char* ns = nullptr;     /* subscribed namespace              */
    ActuatorMode mode = ActuatorMode::Binary;
    int pin = -1;                 /* Tier 1: MCU GPIO.  Stepper: the
                                   * board driver CHANNEL index        */
    bool active_high = true;      /* Binary polarity                   */
    double in_min = 0.0;          /* numeric input scale (engine       */
    double in_max = 100.0;        /*   numeric convention 0..100)      */
    int out_max = 255;            /* Pwm: full duty.  Stepper: full-
                                   * scale step position               */
    int step_interval_ms = 3;     /* Stepper pacing (per step)         */
    bool home_on_boot = false;    /* Stepper: assume worst-case start
                                   * (out_max) and walk to 0 against
                                   * the mechanical stop — the no-
                                   * feedback gauge zeroing idiom      */
};

/* What the sink receives.  Binary/PwmDuty/StepperTarget fire from
 * on_value(); StepperStep/StepperArrived fire from tick(). */
struct ActuatorCommand {
    enum class Kind : uint8_t {
        Binary,          /* on = logical, level = physical (polarity) */
        PwmDuty,         /* duty 0..spec.out_max                      */
        StepperTarget,   /* target (new), position (current)          */
        StepperStep,     /* one step taken: position, direction,      */
                         /*   coils = 4-bit IN1..IN4 pattern          */
        StepperArrived,  /* position == target                        */
    };
    Kind kind = Kind::Binary;
    bool on = false;
    bool level = false;
    int duty = 0;
    int target = 0;
    int position = 0;
    int direction = 0;
    uint8_t coils = 0;
};

class ActuatorHal {
public:
    using Sink = void (*)(void* user, const ActuatorSpec& spec,
                          const ActuatorCommand& cmd);

    void set_sink(Sink sink, void* user) { _sink = sink; _sink_user = user; }

    /* Adopt the board's descriptor table (borrowed — must outlive the
     * HAL; board tables are static).  Homing steppers fire an initial
     * StepperTarget (out_max -> 0) so bring-up is legible. */
    void configure(const ActuatorSpec* specs, size_t count);

    /* Deduped subscribed namespaces, first-appearance order — union
     * these with the core's subscriptions for the bus client. */
    size_t namespace_count() const { return _namespaces.size(); }
    const char* namespace_at(size_t i) const {
        return _namespaces[i].c_str();
    }

    /* A value arrived for *ns* (external bus arrival OR a core-fired
     * derived output — define once, actuate many).  Pass the ns()-read
     * view (dsl_coerce_input on raw bus values; core sink results as
     * is).  Returns commands fired. */
    int on_value(const char* ns, const DslValue& v);

    /* Advance the steppers.  Call every loop(); paces each stepper at
     * spec.step_interval_ms.  Returns commands fired. */
    int tick(uint32_t now_ms);

    /* True while any stepper still has steps pending. */
    bool stepping() const;

    /* Last withheld-drive error ("" when none). */
    const char* last_error() const { return _error.c_str(); }

    size_t actuator_count() const { return _states.size(); }
    const ActuatorSpec& spec_at(size_t i) const { return *_states[i].spec; }

    /* Test/serial introspection: -1 when *ns* is not a stepper. */
    int stepper_position(const char* ns) const;
    int stepper_target(const char* ns) const;

    /* The full-step bipolar sequence, coil A = IN1/IN2, coil B =
     * IN3/IN4; bit0=IN1 .. bit3=IN4.  Pattern for a position is
     * kFullStep[position & 3] — stateless in the position. */
    static const uint8_t kFullStep[4];

private:
    struct State {
        const ActuatorSpec* spec = nullptr;
        bool has_last = false;
        bool last_on = false;         /* Binary  */
        int last_duty = -1;           /* Pwm     */
        int target = 0;               /* Stepper */
        int position = 0;
        uint32_t next_due_ms = 0;
        bool due_armed = false;       /* next_due_ms valid             */
    };

    void _emit(const State& s, const ActuatorCommand& cmd);
    bool _map_scaled(const State& s, const DslValue& v, int& out);

    std::vector<State> _states;
    std::vector<std::string> _namespaces;
    Sink _sink = nullptr;
    void* _sink_user = nullptr;
    std::string _error;
};

#endif /* ACTUATOR_HAL_H */
