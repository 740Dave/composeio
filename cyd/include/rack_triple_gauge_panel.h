/*
 * rack_triple_gauge_panel.h — THE ONE PLACE every board-specific number
 * for `rack_triple_gauge` lives.  [SPEC:CXC-BOARD-HW] [SPEC:CXC-BRINGUP]
 * [CXC-VISION-SEMANTIC-REALIZATION]
 *
 * David's first custom PCB.  Three automotive-style analog gauges, each a
 * Switec/VID x27.168 stepper needle plus a 12 V LED backlight, driven by a
 * socketed ESP32-C3 Super Mini as a direct WiFi/MQTT ComposeBus peer.
 *
 * ⚠ THIS BOARD HAS NO GLASS.  It is the fleet's FIRST NON-RASTER
 * REALIZATION BACKEND, so it does NOT implement BoardProfile
 * (board_profile.h is PanelGeometry + DisplayDriver + TouchDriver — a
 * RASTER seam, and a gauge cluster has none of those three).  It implements
 * `RealizationBackend` (realization.h) instead.  Those two are PEERS under
 * [CXC-VISION-SEMANTIC-REALIZATION]: "the WIDGET is SEMANTIC; the BACKEND is
 * the REALIZATION".  Forcing a needle into a DisplayDriver would have been
 * the exact mistake the seam exists to prevent.
 *
 * EVERY number below was read from the board's generator source
 * ~/Dropbox/Maker/pcb/rack_triple_gauge/generate_pcb.py and CONFIRMED against
 * the pad->net assignments in the emitted rack_triple_gauge.kicad_pcb
 * (2026-08-14).  Where the two disagreed, the .kicad_pcb won and the
 * disagreement is recorded — see `pcb-generator-dead-routing` in the
 * descriptor.
 *
 * ⚠ NOTHING IN THIS FILE HAS RUN ON SILICON.  The board is not fabbed.
 * Every constant is a DESIGN VALUE awaiting a bench measurement, and the
 * ones that are measurements-in-waiting are marked ⇢MEASURE.
 */

#ifndef RACK_TRIPLE_GAUGE_PANEL_H
#define RACK_TRIPLE_GAUGE_PANEL_H

#include <cstdint>

/* ================================================================== */
/* 1. PIN MAP — ESP32-C3 Super Mini, socketed on 2x 1x8 headers        */
/* ================================================================== */
/*
 * J_ESP_L (left, pin 1 at the GPIO0 end, USB-C at the bottom):
 *   1 GPIO0  LED1_GPIO      2 GPIO1  LED2_GPIO
 *   3 GPIO2  (expansion)    4 GPIO3  LED3_GPIO
 *   5 GPIO4  SDA            6 +3V3
 *   7 GND                   8 +5V
 *
 * J_ESP_R:
 *   1 GPIO21 (exp)          2 GPIO20 (exp)
 *   3 GPIO10 (exp)          4 GPIO9  (exp) ⚠STRAP
 *   5 GPIO8  (exp) ⚠STRAP   6 GPIO7  (exp)
 *   7 GPIO6  (exp)          8 GPIO5  SCL
 *
 * ⚠ GPIO0/1/3 ARE NOT STRAPPING PINS ON THE ESP32-C3.  This was checked
 * first and deliberately, because the brief that produced this file assumed
 * they were.  The ESP32-C3's boot straps are GPIO2, GPIO8 and GPIO9 ONLY
 * (datasheet §2.4; independently corroborated by [SPEC:CXC-BOARD-HW]
 * `strapping-and-uart-caveats`, which was owner-confirmed on this very
 * module during the 2026-08-12 bring-up).  GPIO0 is the boot strap on the
 * CLASSIC ESP32 and on the S3 — carrying that across parts is the same
 * class of habit-collision as assuming a CAN pair crosses the way a UART
 * pair does.  The three 2N2222 backlight nets are therefore BOOT-SAFE.
 *
 * ⚠ WHAT THE CHECK *DID* FIND: J_EXP_MCP is safe, but J_EXP_ESP breaks out
 * TWO of the three straps — pin 1 = GPIO2 and pin 4 = GPIO9 — unlabelled.
 * Anything a future user hangs there that presents a low impedance to
 * ground at reset (an LED to GND, an NPN base — i.e. EXACTLY this board's
 * own three-times-repeated design idiom) prevents boot, silently and
 * permanently.  See `exp-header-exposes-two-straps` in the descriptor.
 */

/* --- I2C (ESP32-C3 <-> MCP23017) --- */
static constexpr int      kG3SdaGpio      = 4;      /* J_ESP_L pin 5 */
static constexpr int      kG3SclGpio      = 5;      /* J_ESP_R pin 8 */
static constexpr uint32_t kG3I2cHz        = 400000; /* fast mode */
static constexpr uint8_t  kG3McpAddr      = 0x20;   /* A0=A1=A2=GND */

/* --- Backlight PWM (ESP32-C3 -> 150R -> 2N2222 base; 12 V strip on the
 *     collector).  LEDC, low-speed mode (the C3 has no high-speed group). */
static constexpr int kG3IllumGpio[3]      = { 0, 1, 3 };
static constexpr uint32_t kG3IllumFreqHz  = 5000;   /* 8-bit @ 5 kHz is well
                                                     * inside the C3 LEDC
                                                     * timer's range */
static constexpr int kG3IllumDutyBits     = 8;

/* ================================================================== */
/* 2. MCP23017 — the pin MULTIPLIER, not a sensor                      */
/* ================================================================== */
/*
 * GPA0-3 = M1_IN1..IN4   -> L293D #1 (U2) IN1/IN2/IN3/IN4  -> gauge 1
 * GPA4-7 = M2_IN1..IN4   -> L293D #2 (U3)                  -> gauge 2
 * GPB0-3 = M3_IN1..IN4   -> L293D #3 (U4)                  -> gauge 3
 * GPB4-7 = J_EXP_MCP     -> NOT OURS
 *
 * ⚠ IODIRB IS 0xF0, NOT 0x00.  GPB4-7 leave the chip on an expansion
 * header.  Declaring the whole B port an output would have this firmware
 * actively driving four pins belonging to whatever a user plugs in — a
 * contention the user could not see and we would never report.  The top
 * nibble stays an INPUT (high-Z) until something claims it.
 *
 * ⚠ ORDER OF INITIALISATION IS LOAD-BEARING: OLAT before IODIR.  At
 * power-on every MCP23017 GPIO is an INPUT, so all twelve L293D input pins
 * FLOAT, and the L293D has no internal pull-downs and its ENABLE pins are
 * hard-tied to +5 V (U2/U3/U4 pins 1 and 9 -> net 4) — the bridges are
 * always live.  Writing OLAT=0 first and IODIR second makes the transition
 * float -> driven-LOW.  The other order is float -> driven-to-whatever-the-
 * latch-happened-to-hold, which on a cold part is 0x00 anyway but on a warm
 * esp_restart() is the PREVIOUS step pattern — i.e. a coil kicking at the
 * instant of reset.
 */
static constexpr uint8_t kG3RegIODIRA = 0x00, kG3RegIODIRB = 0x01;
static constexpr uint8_t kG3RegIOCON_A = 0x0A, kG3RegIOCON_B = 0x0B;
static constexpr uint8_t kG3RegOLATA  = 0x14, kG3RegOLATB  = 0x15;
static constexpr uint8_t kG3RegGPIOA  = 0x12, kG3RegGPIOB  = 0x13;

static constexpr uint8_t kG3IodirA    = 0x00;   /* GPA0-7 all outputs */
static constexpr uint8_t kG3IodirB    = 0xF0;   /* GPB0-3 out, GPB4-7 IN */

/* ================================================================== */
/* 3. THE x27.168 STEPPER — geometry, and why homing is not optional   */
/* ================================================================== */
/*
 * ⛔ THE x27.168 HAS NO ABSOLUTE POSITION FEEDBACK.  None.  Not an index
 * pulse, not an encoder, not a limit switch — the ONLY position reference
 * on the whole part is the internal mechanical stop at each end of its
 * sweep.  At power-on the firmware does not know, and CANNOT ask, where the
 * needle is.
 *
 * This is the board's headline silent-failure mode and it belongs to the
 * class [CXC-VISION-EPISTEMICS] is about: without homing, a needle sitting
 * at 40 will be driven as though it were at 0, every I2C write will return
 * ESP_OK, every step will be counted correctly, the transport will report
 * healthy, `_device/<id>/health` will be green — and the gauge will read
 * 40 units high forever.  Every machine signal AGREES WITH THE WRONG
 * ANSWER.  There is no instrument that can catch it, on this hardware, at
 * any price, other than a human looking at the dial.
 *
 * So homing is a HARD GATE, not a best effort: RackTripleGauge::ready()
 * returns false until the sweep completes, and the app shell refuses to
 * route any bus value through the backend before then.
 */

/* Sweep and resolution.  x27.168: 315 degrees of travel, 1/3 degree per
 * FULL step.  This firmware HALF-steps (see x27168.h for why), so the
 * quantum is 1/6 degree. */
static constexpr int kG3SweepDegrees      = 315;
static constexpr int kG3FullStepsPerDeg   = 3;
static constexpr int kG3HalfStepsPerDeg   = 6;
static constexpr int kG3SweepHalfSteps    = kG3SweepDegrees * kG3HalfStepsPerDeg; /* 1890 */

/*
 * HOMING SWEEP LENGTH — 2160 half-steps = 360.0 degrees commanded into a
 * 315-degree mechanical range.
 *
 * JUSTIFICATION, because a bound with no stated N+1 behaviour is the first
 * of this fleet's four named failure shapes ("a limit sized exactly for
 * today" — kMaxPersist was 4 for a control plane of exactly 4):
 *
 *   worst-case start    1890  needle parked hard against the FAR stop
 *   pointer press-fit   +180  the pointer is HAND-fitted to the shaft at
 *                             assembly; +/-30 degrees of fitting error is
 *                             generous but not absurd, and a pointer fitted
 *                             past the stop is invisible to every other check
 *   unit-to-unit sweep   +90  15 degrees of tolerance on the 315 spec
 *   ------------------------
 *   total               2160  = exactly 360 degrees.  ONE FULL TURN.
 *
 * Chosen as a round 360 deliberately: "command a whole revolution against a
 * 315-degree stop" is a sentence an operator can hold, and it degrades
 * gracefully — at N+1 (a 270-degree gauge, a 20-degree pointer offset, a
 * replacement motor from a different lot) 2160 still covers it, and the fix
 * is one constant in one file rather than a hunt through a driver.
 *
 * The overtravel is NOT wasted motion, it is the guarantee: once the needle
 * reaches the stop the remaining steps SLIP, harmlessly, which is precisely
 * the mechanism by which an unknown start position becomes a known zero.
 */
static constexpr int kG3HomeHalfSteps     = 2160;

/*
 * REST OFFSET — after homing, back off 18 half-steps (3.0 degrees) and call
 * THAT zero.  Two independent reasons, both real:
 *   1. a coil energised while the rotor is jammed against a hard stop is
 *      where this motor family buzzes and where it heats;
 *   2. it leaves the needle somewhere to GO below the dial's printed zero,
 *      so an under-range value is VISIBLE as a needle below the mark
 *      instead of being clipped into invisibility against the stop.  An
 *      out-of-range input that looks identical to a valid zero is another
 *      instrument that can only report success.
 */
static constexpr int kG3RestOffsetHalfSteps = 18;

/*
 * STEP RATES.  ⇢MEASURE — these are DESIGN VALUES, not measurements, and
 * the true pull-in rate of THESE motors behind THIS driver is a bench
 * number (bring-up ladder rung 6).
 *
 * The coils see roughly 3.2 V, not 5 V: the L293D is a BJT bridge and drops
 * ~1.8 V total (high-side + low-side) out of the +5 V rail it is fed from
 * (U2/U3/U4 pin 8 VS -> net 4 = +5V, NOT +12V).  Across a ~280 ohm coil
 * that is ~11.4 mA against a ~20 mA rating, so this board runs the motors
 * at roughly 57% of rated torque — which LOWERS the achievable step rate
 * and is why the numbers below are conservative rather than datasheet-led.
 *
 * HOMING RUNS SLOWER THAN SLEWING, ON PURPOSE.  A step lost while slewing
 * costs one quantum and is corrected at the next re-home.  A step lost
 * while HOMING corrupts the definition of zero for the whole session and
 * nothing downstream can detect it.  The two are not the same risk, so they
 * do not get the same rate.  Homing also runs FLAT, with no acceleration
 * ramp, so that during bring-up the ramp is never a second live suspect.
 */
static constexpr int kG3HomeHalfStepsPerSec = 400;   /* 66.7 deg/s */
static constexpr int kG3SlewHalfStepsPerSec = 800;   /* 133 deg/s -> 2.4 s
                                                      * full-scale */
/* 2160 / 400 = 5.4 s, and ALL THREE MOTORS HOME CONCURRENTLY — the twelve
 * coil lines are two bytes at one I2C address, so a step for gauge 1 costs
 * the same transaction as a step for all three.  Boot cost is 5.4 s total,
 * not 16.2 s. */
static constexpr int kG3HomeDurationMs =
    (kG3HomeHalfSteps * 1000) / kG3HomeHalfStepsPerSec;

/* ================================================================== */
/* 4. POWER — stated because rung 1 of the ladder measures it          */
/* ================================================================== */
/*
 * 12 V screw terminal -> 1N5401 (3 A) reverse-protection -> +12V rail
 *                     -> LM7805 (TO-220) -> +5V -> everything.
 *
 * ⚠ THE HEATSINK IS MANDATORY, and the arithmetic says so:
 *
 *   ESP32-C3 (WiFi STA)     ~120 mA avg      ~350 mA TX peak
 *   3x L293D quiescent       ~87 mA          ~138 mA max
 *   6x coils energised        ~72 mA           ~72 mA
 *   MCP23017                   ~1 mA            ~1 mA
 *   ------------------------------------------------------
 *   total                    ~280 mA          ~561 mA
 *
 *   LM7805 dissipation = (12 - 5) x I  =  1.96 W typ,  3.93 W peak.
 *
 * A bare TO-220 in free air is ~65 C/W: 1.96 W is a 127 C rise, i.e. a
 * junction at ~152 C against a 150 C shutdown — it would thermal-cycle at
 * IDLE.  With a clip-on sink at <=25 C/W total it settles around 78 C typ.
 * ⇢MEASURE the regulator tab temperature at rung 1; it is the cheapest
 * early warning this board offers.
 *
 * ⚠ 13.8 V "12 V" supplies (bench, automotive) push the typical figure to
 * 2.46 W and the peak to 4.9 W.  Feed this board a REGULATED 12 V.
 */
static constexpr float kG3VinNominal      = 12.0f;
static constexpr float kG3EstIdleAmps     = 0.280f;
static constexpr float kG3EstPeakAmps     = 0.561f;

/* ================================================================== */
/* 5. IDENTITY                                                         */
/* ================================================================== */
/* device_id() is CXC_DEVICE_PREFIX + last 3 MAC octets and the buffer is
 * char[16] — "gauge3-" is 7, leaving room for 6 hex + NUL.  It is also the
 * prefix of every `_device/<id>/...` namespace, some of which have to be
 * typed by hand into a bridge register, so short is not cosmetic. */
#ifndef CXC_DEVICE_PREFIX
#define CXC_DEVICE_PREFIX "gauge3"
#endif

#endif /* RACK_TRIPLE_GAUGE_PANEL_H */
