/*
 * x27168.h — the Switec/VID x27.168 gauge stepper: half-step sequencer,
 * homing state machine and position mapping.
 * [SPEC:CXC-BOARD-HW] [SPEC:CXC-BRINGUP] [PCB-GAUGE3] stress S4
 *
 * PURE C++ — no ESP-IDF, no I2C, no time source of its own.  Same idiom as
 * restart.cpp / dsl.cpp / yaml_min.cpp, and for the same reason: this is
 * the module whose arithmetic decides whether a needle means what it reads,
 * and it must be testable on a host WITHOUT a board.  It emits COIL
 * PATTERNS and consumes a caller-supplied clock; who writes those patterns
 * to what silicon is not its business.
 *
 * ------------------------------------------------------------------
 * THE DRIVE SEQUENCE
 * ------------------------------------------------------------------
 * The x27.168 is a two-phase BIPOLAR stepper: two coils, four terminals,
 * one L293D per motor (coil A across OUT1/OUT2 driven by IN1/IN2, coil B
 * across OUT3/OUT4 driven by IN3/IN4).
 *
 * Nibble bit layout, chosen to match the BOARD so no translation layer can
 * get it wrong: bit0=IN1, bit1=IN2, bit2=IN3, bit3=IN4.  That is exactly
 * MCP23017 GPA0..GPA3 for gauge 1, GPA4..GPA7 for gauge 2, GPB0..GPB3 for
 * gauge 3 (rack_triple_gauge nets M1_IN1..M3_IN4).
 *
 * HALF-STEP, 8 states, not full-step.  Reasons, in order of weight:
 *   1. it halves the audible step noise, which on three gauges in a rack
 *      next to a person is the difference between an instrument and an
 *      irritant;
 *   2. it doubles resolution to 1/6 degree, so a 0..255 throttle maps onto
 *      1890 quanta (7.4 per unit) and needle motion has no visible stair;
 *   3. it costs nothing — the same one I2C transaction per step.
 * The price is alternating torque between one-phase-on and two-phase-on
 * states, which is irrelevant for a needle with no load and IS relevant to
 * the achievable step rate (hence the conservative rates in the panel
 * header).
 *
 * IN1=IN2 (both low) shorts the coil through the low-side transistors: no
 * current, and a mild dynamic brake that helps the needle hold against
 * vibration.  That is the OFF state, and it is why 0x0 is safe to write at
 * any moment — including the instant before IODIR is flipped to output.
 */

#ifndef X27168_H
#define X27168_H

#include <cstdint>

/* ---- coil patterns ------------------------------------------------- */
/*
 *  idx  A    B    IN4..IN1   hex
 *   0   +    .    0b0001     0x1
 *   1   +    +    0b0101     0x5
 *   2   .    +    0b0100     0x4
 *   3   -    +    0b0110     0x6
 *   4   -    .    0b0010     0x2
 *   5   -    -    0b1010     0xA
 *   6   .    -    0b1000     0x8
 *   7   +    -    0b1001     0x9
 */
extern const uint8_t kX27HalfStep[8];
static constexpr uint8_t kX27CoilsOff = 0x0;

/* ---- the homing/run state machine ---------------------------------- */
enum class X27Phase : uint8_t {
    Idle,        /* constructed, nothing driven, NOT homed */
    Homing,      /* sweeping down against the stop */
    Resting,     /* backing off the stop by the rest offset */
    Ready,       /* homed; accepting targets */
};

/*
 * One needle.  Position is carried in HALF-STEPS from the homed zero, which
 * is the rest position (kG3RestOffsetHalfSteps off the stop), NOT the stop
 * itself.
 *
 * ⛔ EVERY POSITION IN THIS CLASS IS DEAD RECKONING.  There is no feedback
 * anywhere in the part.  `position()` is what we have COMMANDED, and the
 * only thing that makes it agree with the physical needle is (a) having
 * homed and (b) not having exceeded the pull-in rate since.  Nothing in
 * software can verify either.  That is stated here, in the header of the
 * module that returns the number, so nobody downstream reads `position()`
 * as a measurement.
 */
class X27168 {
public:
    void reset();

    /* Begin the homing sweep.  Idempotent; a second call re-homes, which
     * is the recovery path for a needle that was knocked or that lost steps
     * (bus command `_device/<id>/gauge/rehome`). */
    void start_homing(uint32_t now_ms);

    /* Drive the state machine.  Returns TRUE iff the coil pattern changed
     * and the caller should write it out.  Non-blocking; the caller polls
     * this every loop pass.
     *
     * Batching note: three of these share one I2C transaction, so the
     * caller ORs the "changed" flags and writes once. */
    bool tick(uint32_t now_ms);

    /* Commanded target, in half-steps from zero.  Clamped to
     * [0, travel_half_steps].  Ignored while not Ready — a target accepted
     * before homing would be the exact silent failure homing exists to
     * prevent, so it is refused rather than queued. */
    void set_target(int half_steps);

    /* Convenience: a unit value in [0,1] onto the full travel. */
    void set_unit(float unit);

    uint8_t  coils()   const { return coils_; }
    int      position() const { return pos_; }
    int      target()   const { return target_; }
    X27Phase phase()    const { return phase_; }
    bool     homed()    const { return phase_ == X27Phase::Ready; }
    /* Steps remaining in the homing sweep — the ONLY progress signal this
     * part can give, and it is a count of commands issued, not of motion
     * observed.  Named `_commanded` so no caller mistakes it. */
    int      home_steps_commanded() const { return home_done_; }

    /* Tunables, injected so the host test can exercise the boundaries
     * without recompiling the board header.  Defaults come from
     * rack_triple_gauge_panel.h at construction in the board profile. */
    void configure(int travel_half_steps, int home_half_steps,
                   int rest_offset, int home_rate_hz, int slew_rate_hz);

    int travel_half_steps() const { return travel_; }

private:
    void step(int dir);          /* dir = +1 / -1; advances seq_ and pos_ */

    uint8_t  coils_     = kX27CoilsOff;
    int      seq_       = 0;     /* index into kX27HalfStep */
    int      pos_       = 0;     /* half-steps from zero (dead reckoning) */
    int      target_    = 0;
    X27Phase phase_     = X27Phase::Idle;
    uint32_t next_due_  = 0;     /* ms of the next step */
    int      home_done_ = 0;     /* homing steps COMMANDED so far */
    int      rest_done_ = 0;

    int travel_    = 1890;
    int home_      = 2160;
    int rest_      = 18;
    int home_hz_   = 400;
    int slew_hz_   = 800;
};

/* Pack three needles' nibbles into the two MCP23017 port bytes.
 * GPIOA = M2<<4 | M1   (GPA0-3 = M1_IN1..4, GPA4-7 = M2_IN1..4)
 * GPIOB = M3           (GPB0-3 = M3_IN1..4; GPB4-7 belong to J_EXP_MCP and
 *                       MUST stay 0 here — IODIRB keeps them inputs, but
 *                       writing a 1 into an input's OLAT arms a pull-up-
 *                       looking edge the moment somebody flips IODIR.) */
void x27_pack_ports(const X27168& m1, const X27168& m2, const X27168& m3,
                    uint8_t* gpioa, uint8_t* gpiob);

#endif /* X27168_H */
