/*
 * tm1637_panel.h — the reactive_core 4-tube TM1637 PANEL subsystem.
 * [SPEC:CXC-VISION-SEMANTIC-REALIZATION] [CXC-TM1637-SHARED-CLK]
 * [DEC-R503-AND-FOUR-TM1637-ON-ONE-ESP32-S3]
 *
 * ⛔ A SEPARATE SUBSYSTEM FROM THE R503 FINGER PATH.  This file names no HAL
 * type, no fingerprint namespace, and no R503 pin.  It is compiled ONLY into
 * the COMBINED env (env:fingerprint_tm1637_s3, -DTM1637_PANEL) and its host
 * tests; a fault here cannot touch finger validation, which is the guardrail
 * this split exists to hold.
 *
 * TOPOLOGY — ratified in python/composeio/render/examples/
 * r503_tm1637_panel.compose.yaml (design-locked pins, quoted below):
 *   shared CLK  = GPIO7  (host->panel only, through a 470 R at each module)
 *   tube 1 DIO  = GPIO8      tube 3 DIO  = GPIO10
 *   tube 2 DIO  = GPIO9      tube 4 DIO  = GPIO11
 * Four tubes, ONE shared CLK, four INDIVIDUAL DIO — the N+1 topology.  A
 * non-selected DIO is PARKED HIGH (driven HIGH, push-pull) and never pulled low, which the
 * driver's own start-condition (DIO high->low while CLK high) makes safe:
 * an idle tube whose DIO never moves is never armed ([CXC-TM1637-DIO-PARK-HIGH]).
 *
 * SEMANTIC BINDINGS (state-on-bus, behaviour-in-leaf).  The tubes SUBSCRIBE
 * bus namespaces and render the arriving value; they invent nothing:
 *   tube 1 <- throttle/left        (engine L thrust, 0..100 %)   [Change 3]
 *   tube 2 <- throttle/right       (engine R thrust, 0..100 %)   [Change 3]
 *   tube 3 <- dradis/contacts/count                              (scene default)
 *   tube 4 <- ship/population                                    (scene default)
 * ⛔ throttle/left + throttle/right are the SAME namespaces the on-screen
 * engine-thrust gauge subscribes (compose_projects/bsg/game_hardware/
 * engine_thrust_panel/engine_thrust.compose.yaml) — subscribed, not a parallel
 * spelling ([DD-R503-NAMESPACE-SPELLING]).  No namespace here names a vendor.
 */
#ifndef TM1637_PANEL_H
#define TM1637_PANEL_H

#include <cstddef>
#include <cstdint>

#include "tm1637.h"   /* canonical driver, resolved via -I .../cyd/src */

/* --- the ratified pins (quoted from the design-locked scene netlist) ------ */
#define TM1637_PANEL_CLK_PIN   7   /* mcu.GPIO7 shared CLK */
#define TM1637_PANEL_DIO1_PIN  8   /* mcu.GPIO8  -> tm1.DIO */
#define TM1637_PANEL_DIO2_PIN  9   /* mcu.GPIO9  -> tm2.DIO */
#define TM1637_PANEL_DIO3_PIN 10   /* mcu.GPIO10 -> tm3.DIO */
#define TM1637_PANEL_DIO4_PIN 11   /* mcu.GPIO11 -> tm4.DIO */

/* --- the four tube bus bindings ------------------------------------------- */
#define TM1637_PANEL_NS_TUBE1 "throttle/left"
#define TM1637_PANEL_NS_TUBE2 "throttle/right"
#define TM1637_PANEL_NS_TUBE3 "dradis/contacts/count"
#define TM1637_PANEL_NS_TUBE4 "ship/population"

/* --- panel constants (ratified scene: 4 digits, 50 kHz, step 3) ----------- */
#define TM1637_PANEL_DIGITS          4
#define TM1637_PANEL_HALF_PERIOD_US 50   /* 10 kHz push-pull — generous margin on a marginal link (was 50 kHz open-drain) */
#define TM1637_PANEL_BRIGHTNESS      3   /* step index (NON-linear), not a duty */
#define TM1637_PANEL_TUBES           4
/* ⛔ BOUNDED DISPLAY RATE — decouples the physical write rate from the
 * message-arrival rate.  A dirty tube is written AT MOST once per this
 * interval; values that arrive in between are COALESCED (only the latest
 * is ever shown).  This is what stops the sequential replay: without it,
 * g_mqtt.loop() surfaces ~one message per loop iteration and a per-iteration
 * write plays the whole 100..0 backlog value-by-value.  40 ms ~= 25 Hz —
 * fast enough to read as instant, slow enough that many arrivals collapse
 * to one write. */
#define TM1637_PANEL_FLUSH_INTERVAL_MS 40

/* ==========================================================================
 * PURE mapping — host-testable, no Arduino.  Compiled into env:native_test.
 * ==========================================================================
 *
 * THE THRUST -> DISPLAY MAP.  A thrust percentage 0..100 is clamped to range
 * and rendered RIGHT-JUSTIFIED, blank-padded, into the tube's digits.  On a
 * 4-digit tube a 0..100 value is at most three characters ("100"), so it
 * occupies the RIGHTMOST THREE digits with the leftmost blank — exactly the
 * requirement, achieved by the driver's own justification policy rather than a
 * bespoke one.  Integer render TRUNCATES toward zero (99.7 -> "99"), matching
 * tm1637_format_number and the raster seven_segment widget: the same namespace
 * MUST read the same on both realizations ([CXC-VISION-SEMANTIC-REALIZATION]).
 *
 * `dev` supplies only .digits and .map; no bus function is called, so a
 * default-constructed / dummy-bus Tm1637 is a valid argument on the host.
 * Writes exactly dev.digits segment bytes to out[].
 */
void tm1637_panel_format_thrust(const Tm1637& dev, double thrust, uint8_t* out);

/* Enumerate the namespaces the panel subscribes, in tube order (1..4).  The
 * app shell registers each as an EXACT subscription (composeio/global/<ns>).
 * Returns the count written (<= cap). */
size_t tm1637_panel_namespaces(const char** out, size_t cap);

/* ==========================================================================
 * ARDUINO integration — the on-silicon face.  Compiled ONLY into the combined
 * device env (tm1637_panel.cpp is #ifdef ARDUINO in whole).  Declared here
 * unconditionally so the app-shell hooks reference a stable signature.
 * ========================================================================== */

/* Bring up the four tubes on the ratified pins: park every line released
 * (high), init each Tm1637 (4 digits, 10 kHz push-pull), set brightness step 3, display
 * ON, and blank every tube.  ⛔ Blank, NOT zero: a tube must not assert a value
 * nobody sent — it fills only when its namespace arrives. */
void tm1637_panel_setup();

/* Apply one bus arrival.  If `ns` is one of the four subscribed namespaces,
 * format `value` into that tube's last[] and mark it DIRTY — it does NOT write
 * the module.  Returns true if a tube matched.  ⛔ EVENT-DRIVEN, LATEST-WINS:
 * writing (or logging) here, per message, inside the MQTT callback replayed a
 * backlog value-by-value and made the display lag; the physical write is
 * deferred to tm1637_panel_flush() so faster-than-a-write arrivals COALESCE to
 * the latest value. */
bool tm1637_panel_apply_ns(const char* ns, double value);

/* Flush tick — call EVERY loop() iteration.  Writes the tubes marked dirty by
 * tm1637_panel_apply_ns(), showing the LATEST value, then clears the flag —
 * but RATE-GATED to at most one write pass per TM1637_PANEL_FLUSH_INTERVAL_MS
 * (~25 Hz).  That gate is the coalescing: it decouples the physical write rate
 * from the message-arrival rate, so a fast 100..0 stream collapses to the
 * current value instead of replaying one write per message.  The gate opens
 * immediately after any quiet gap (a lone change is instant); only a fast
 * stream is throttled.  Not gated by the 1 Hz service tick. */
void tm1637_panel_flush();

/* Service tick — call every loop().  A 1 Hz SAFETY NET only: once a second it
 * REWRITES all four tubes unconditionally from their last-known values ([the
 * ratified scene's refresh_unconditional]) so a glitch on a stateful display
 * with no readback cannot stay wrong forever.  It does NOT drive the live path
 * (flush does) and must NOT gate it. */
void tm1637_panel_service();

/* ISOLATION SELFTEST — drive an UNAMBIGUOUS pattern INDEPENDENT of the bus/
 * throttle data path, on the SAME g_tubes instances (same shared CLK, same
 * DIO pins, same push-pull seams) production uses, so a PASS here
 * exonerates CLK+ground+protocol and indicts the bus-data path, while a FAIL
 * here indicts the shared physical layer.  Three phases:
 *   A  all segments ON ('8.8.8.8.'), full brightness, every tube — hold;
 *   B  per-tube identity (tube N shows 'NNNN', one tube at a time) — isolates
 *      ONE dead DIO from a global fault;
 *   C  walking single segment a..g,dp across all tubes — isolates a dead seg.
 * Also reports, per tube, the ACK the module asserts on the 8th clock: ACK=NO
 * means the module never heard us (shared CLK/ground, or that DIO) — an
 * ELECTRICAL probe that does not depend on eyes.  Restores brightness (step 3)
 * and repaints each tube's last-known bus value on exit.  No-op if setup has
 * not run; only compiled/linked in the combined env (TM1637_PANEL).
 *
 * only_tube == 0 -> the full three-phase, all-four sweep above.
 * only_tube 1..4 -> SINGLE-TUBE mode: drive ONLY that tube (all segments on +
 * walking segment), report ONLY that tube's ACK, and touch NO other tube — so
 * exactly one module draws current (stay under the 3.3 V LDO's headroom). */
void tm1637_panel_selftest(int only_tube);

/* MULTIMETER AID — slowly toggle tube-1's two signal pins (CLK GPIO7 + DIO1
 * GPIO8) at 1 Hz for ~20 cycles so a human can meter them, then restore the
 * pins to open-drain idle.  ⛔ Driven PUSH-PULL (OUTPUT + digitalWrite), NOT
 * the open-drain release — a clean 0 V / 3.3 V a cheap DMM reads regardless of
 * whether the module pull-up is present; safe now the whole panel is 3.3 V.
 * CLK and DIO toggle on OPPOSITE phases so they are distinguishable on a meter.
 * Only compiled/linked in the combined env (TM1637_PANEL). */
void tm1637_panel_wiggle();

#endif /* TM1637_PANEL_H */
