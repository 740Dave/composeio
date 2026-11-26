/*
 * tm1637_panel.cpp — the ARDUINO half of the TM1637 panel: the four Tm1637
 * instances on the ratified pins built over the SHARED PUSH-PULL seam, plus the
 * setup / apply / flush / 1 Hz-refresh integration the combined firmware calls.
 *
 * ⛔ WHOLE FILE IS #ifdef ARDUINO.  The pure value->digits logic lives in
 * tm1637_panel_map.cpp (host-tested).  This file touches silicon and is
 * compiled ONLY into the combined device env (env:fingerprint_tm1637_s3).
 *
 * ============ PUSH-PULL, NOT OPEN-DRAIN (root cause of the dark tubes) ======
 * DEFECT-TM1637-ALL-FOUR-7-SEGMENT-E11525.  The original seams released a line
 * HIGH by going Hi-Z (pinMode INPUT), trusting the module's pull-up to raise
 * it.  On the cheap 4-digit breakouts at 3.3 V that pull-up is absent/too weak,
 * so a released line never reaches a valid HIGH: no clean CLK edge, no START,
 * nothing latches, 0 ACK, dark.  GLASS-CONFIRMED on both the bare ESP32-C3 and
 * this S3 board once the seam went push-pull.  The bit-bang PROTOCOL was never
 * wrong — it is byte-identical to two known-good libraries; only the SEAM'S
 * DRIVE DISCIPLINE was.  Every tube's bus is now built from the SHARED push-pull
 * seam (cyd/src/tm1637_arduino_seam.h): release() DRIVES the pin HIGH; the only
 * Hi-Z point is dio_read() for the ACK bit.
 *
 * ⛔ SHARED-CLK PARKING IS DRIVEN-HIGH, NOT HIGH-Z.  A non-selected tube's DIO
 * is DRIVEN HIGH (park_high), because a released (Hi-Z) DIO on a pull-up-less
 * module could float low and see a spurious START while the shared CLK toggles
 * for another tube.  Each DIO is point-to-point, so driving them high never
 * contends; only that tube's own ACK ever pulls its DIO low, in its own txn.
 *
 * ============ LIVE, EVENT-DRIVEN, LATEST-VALUE-WINS (the latency fix) ========
 * A bus arrival does NOT write the tube synchronously.  It updates the tube's
 * last[] segments and sets a DIRTY flag — nothing more.  tm1637_panel_flush(),
 * called every loop() iteration, writes only the dirty tubes and clears the
 * flag.  This COALESCES: when several throttle/* values arrive between two
 * flushes (faster than a ~few-ms tube write completes), only the LATEST is ever
 * put on the glass — intermediate values are skipped, never queued and played
 * back.  The tube therefore tracks the CURRENT throttle with one-loop latency.
 *
 * WHY THIS WAS THE BUG: the previous apply wrote the tube (and Serial.printf'd)
 * INSIDE the MQTT callback, per message.  A backlog drained by one g_mqtt.loop()
 * pump was replayed value-by-value at write+log speed, so the display lagged
 * real time and dwelt on each stale value — exactly the reported symptom.
 *
 * The 1 Hz tm1637_panel_service() is now ONLY a safety net: an unconditional
 * repaint that recovers a dropped write or a glitch on a stateful display with
 * no readback.  It does NOT gate the live path — flush runs every iteration
 * regardless of the 1 Hz tick.
 */
#ifdef ARDUINO

#include <Arduino.h>
#include <cstring>

#include "tm1637_panel.h"
#include "tm1637_arduino_seam.h"   /* SHARED push-pull seam, via -I .../cyd/src */

namespace {

struct Tube {
    Tm1637            dev;
    Tm1637ArduinoPins pins;                   /* {shared clk, own dio, read_settle_us} */
    const char*       ns;
    bool              is_thrust;              /* tubes 1&2: 0..100 % thrust map */
    uint8_t           last[TM1637_PANEL_DIGITS];   /* last-known segments (blank init) */
    bool              dirty;                  /* last[] changed since the last write */
};

Tube g_tubes[TM1637_PANEL_TUBES];
bool g_ready = false;
uint32_t g_last_refresh_ms = 0;
uint32_t g_last_flush_ms   = 0;   /* last physical dirty-write pass (rate gate) */

/* Drive one pin to a clean push-pull idle HIGH (the parked state). */
void park_high(uint8_t pin) { pinMode(pin, OUTPUT); digitalWrite(pin, HIGH); }

/* Push a tube's stored `last[]` segments to the physical module. */
void write_tube(Tube& t) {
    tm1637_set_on(t.dev, true);                 /* re-assert display-control */
    tm1637_write_segments(t.dev, 0, t.last, TM1637_PANEL_DIGITS);
    t.dirty = false;
}

}  /* namespace */

void tm1637_panel_setup() {
    const uint8_t dio[TM1637_PANEL_TUBES] = {
        TM1637_PANEL_DIO1_PIN, TM1637_PANEL_DIO2_PIN,
        TM1637_PANEL_DIO3_PIN, TM1637_PANEL_DIO4_PIN,
    };
    const char* ns[TM1637_PANEL_TUBES] = {
        TM1637_PANEL_NS_TUBE1, TM1637_PANEL_NS_TUBE2,
        TM1637_PANEL_NS_TUBE3, TM1637_PANEL_NS_TUBE4,
    };

    /* Park every line DRIVEN HIGH (push-pull) BEFORE any transaction, so no
     * idle DIO can float low while the shared CLK is high (a start condition on
     * an unselected tube).  Driven, not high-Z: the whole reason for this fix
     * is that the module pull-up cannot be trusted to hold a released line. */
    park_high(TM1637_PANEL_CLK_PIN);
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) park_high(dio[i]);

    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        Tube& t = g_tubes[i];
        t.pins = { TM1637_PANEL_CLK_PIN, dio[i], /*read_settle_us=*/8 };
        t.ns = ns[i];
        t.is_thrust = (i < 2);                  /* tubes 1 & 2 are L/R thrust */
        for (int d = 0; d < TM1637_PANEL_DIGITS; d++) t.last[d] = 0x00;  /* BLANK */
        t.dirty = false;
        tm1637_init(t.dev, tm1637_arduino_bus(t.pins), TM1637_PANEL_DIGITS,
                    TM1637_PANEL_HALF_PERIOD_US);
        tm1637_set_brightness(t.dev, TM1637_PANEL_BRIGHTNESS);
        tm1637_set_on(t.dev, true);
        /* ⛔ blank, NOT zero — a tube must not assert a value nobody sent
         * (the RAW-bars lesson, commit 80429ee).  It fills on first arrival. */
        tm1637_clear(t.dev);
    }
    g_ready = true;
    g_last_refresh_ms = millis();
    Serial.printf("[tm1637] panel up: 4 tubes, shared CLK GPIO%d, DIO GPIO%d/%d/%d/%d, "
                  "push-pull 10 kHz, step %d\n",
                  TM1637_PANEL_CLK_PIN, TM1637_PANEL_DIO1_PIN, TM1637_PANEL_DIO2_PIN,
                  TM1637_PANEL_DIO3_PIN, TM1637_PANEL_DIO4_PIN, TM1637_PANEL_BRIGHTNESS);
    Serial.printf("[tm1637] tube1<-%s  tube2<-%s  tube3<-%s  tube4<-%s\n",
                  TM1637_PANEL_NS_TUBE1, TM1637_PANEL_NS_TUBE2,
                  TM1637_PANEL_NS_TUBE3, TM1637_PANEL_NS_TUBE4);
}

bool tm1637_panel_apply_ns(const char* ns, double value) {
    if (!g_ready || ns == nullptr) return false;
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        Tube& t = g_tubes[i];
        if (strcmp(ns, t.ns) != 0) continue;
        /* Update last[] and mark dirty — do NOT write here.  Writing (or
         * logging) inside the MQTT callback per message is what replayed a
         * backlog value-by-value and made the display lag.  flush() writes the
         * LATEST on the next loop iteration; faster arrivals coalesce. */
        if (t.is_thrust) {
            tm1637_panel_format_thrust(t.dev, value, t.last);
        } else {
            Tm1637Format fmt; fmt.decimals = -1; fmt.pad_zero = false;
            tm1637_format_number(t.dev, value, fmt, t.last);
        }
        t.dirty = true;
        return true;
    }
    return false;
}

void tm1637_panel_flush() {
    if (!g_ready) return;
    /* Primary low-latency path — RATE-GATED, latest-value-wins.
     *
     * The physical write rate is decoupled from the message-arrival rate: a
     * dirty tube is written AT MOST once per TM1637_PANEL_FLUSH_INTERVAL_MS
     * (~25 Hz).  apply_ns() keeps overwriting last[] with every arrival for
     * free (memory only), so when the gate opens we write the CURRENT (latest)
     * value and every value that arrived since the last write is simply
     * skipped — never queued, never replayed.  THIS is the coalescing: without
     * the gate, g_mqtt.loop() surfaces ~one message per loop iteration and a
     * per-iteration write plays the whole 100..0 ramp out one value per write.
     *
     * The timestamp advances only when we actually write, so after any quiet
     * gap the very next dirty tube is written IMMEDIATELY (zero added latency);
     * only a fast stream is throttled to the display rate. */
    if (millis() - g_last_flush_ms < TM1637_PANEL_FLUSH_INTERVAL_MS) return;
    bool wrote = false;
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        Tube& t = g_tubes[i];
        if (t.dirty) { write_tube(t); wrote = true; }   /* write_tube clears dirty */
    }
    if (wrote) g_last_flush_ms = millis();
}

void tm1637_panel_service() {
    if (!g_ready) return;
    uint32_t now = millis();
    if (now - g_last_refresh_ms < 1000) return;   /* 1 Hz SAFETY-NET repaint */
    g_last_refresh_ms = now;
    /* Unconditional repaint of every tube's last-known value — recovers a
     * dropped write / glitch on a stateful display with no readback.  This does
     * NOT drive the live path (flush does); it is a slow backstop only. */
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) write_tube(g_tubes[i]);
}

void tm1637_panel_selftest(int only_tube) {
    if (!g_ready) {
        Serial.println("[tm1637] selftest: panel not ready (tm1637_panel_setup has not run)");
        return;
    }

    /* --- SINGLE-TUBE mode (only_tube 1..4): drive EXACTLY one module, so only
     * one draws current under the 3.3 V LDO.  No other tube is touched. ------ */
    if (only_tube >= 1 && only_tube <= TM1637_PANEL_TUBES) {
        const int i = only_tube - 1;
        Tube& t = g_tubes[i];
        Serial.printf("[tm1637] ===== SELFTEST tube %d ONLY (single-tube, LDO-safe) — bus-independent =====\n",
                      only_tube);
        Serial.printf("[tm1637] shared CLK GPIO%d, tube %d DIO GPIO%u, push-pull 10 kHz\n",
                      TM1637_PANEL_CLK_PIN, only_tube, (unsigned)t.pins.dio);

        tm1637_set_on(t.dev, true);
        tm1637_set_brightness(t.dev, TM1637_BRIGHTNESS_MAX);

        uint8_t all_on[TM1637_PANEL_DIGITS];
        for (int d = 0; d < TM1637_PANEL_DIGITS; d++) all_on[d] = 0xFF;
        Serial.printf("[tm1637] Phase A: tube %d ALL SEGMENTS ON — should show '8.8.8.8.' full brightness (hold 2s)\n",
                      only_tube);
        const bool ack = tm1637_write_segments(t.dev, 0, all_on, TM1637_PANEL_DIGITS);
        Serial.printf("[tm1637]   tube %d (DIO GPIO%u): ACK=%s\n",
                      only_tube, (unsigned)t.pins.dio, ack ? "yes" : "NO");
        Serial.println(ack
            ? "[tm1637]   >>> ACKed — this module hears CLK GPIO7 + its DIO. If it also lights, this tube's path is good."
            : "[tm1637]   >>> NO ACK — this module never heard us. Suspect this tube's DIO joint, or CLK integrity on GPIO7.");
        delay(2000);

        Serial.printf("[tm1637] Phase C: tube %d walking single segment a..g,dp (0.4s each)\n", only_tube);
        for (int s = 0; s < 8; s++) {
            uint8_t pat[TM1637_PANEL_DIGITS];
            for (int d = 0; d < TM1637_PANEL_DIGITS; d++) pat[d] = (uint8_t)(1u << s);
            tm1637_write_segments(t.dev, 0, pat, TM1637_PANEL_DIGITS);
            delay(400);
        }

        tm1637_set_brightness(t.dev, TM1637_PANEL_BRIGHTNESS);
        write_tube(t);
        Serial.printf("[tm1637] selftest tube %d complete — brightness restored, tube repainted. No other tube driven.\n",
                      only_tube);
        return;
    }

    /* --- only_tube == 0: the full three-phase, all-four sweep. -------------- */
    Serial.println("[tm1637] ===== SELFTEST — INDEPENDENT of bus/throttle data =====");
    Serial.printf("[tm1637] shared CLK GPIO%d, DIO GPIO%d/%d/%d/%d, push-pull 10 kHz\n",
                  TM1637_PANEL_CLK_PIN, TM1637_PANEL_DIO1_PIN, TM1637_PANEL_DIO2_PIN,
                  TM1637_PANEL_DIO3_PIN, TM1637_PANEL_DIO4_PIN);

    /* Force display ON + FULL brightness so neither is a confound during test. */
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        tm1637_set_on(g_tubes[i].dev, true);
        tm1637_set_brightness(g_tubes[i].dev, TM1637_BRIGHTNESS_MAX);
    }

    /* --- Phase A: ALL SEGMENTS ON, every digit, every tube. --------------- */
    uint8_t all_on[TM1637_PANEL_DIGITS];
    for (int d = 0; d < TM1637_PANEL_DIGITS; d++) all_on[d] = 0xFF;
    Serial.println("[tm1637] Phase A: ALL SEGMENTS ON — every tube should show '8.8.8.8.' at full brightness (hold 2s)");
    int ack_count = 0;
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        const bool ack = tm1637_write_segments(g_tubes[i].dev, 0, all_on,
                                               TM1637_PANEL_DIGITS);
        if (ack) ack_count++;
        Serial.printf("[tm1637]   tube %d (DIO GPIO%u): ACK=%s\n",
                      i + 1, (unsigned)g_tubes[i].pins.dio, ack ? "yes" : "NO");
    }
    if (ack_count == 0) {
        Serial.println("[tm1637]   >>> NO tube ACKed. The modules never heard us. Suspect the SHARED path "
                       "FIRST: CLK not reaching (GPIO7 wire / its 470R) or COMMON GROUND missing.");
    } else if (ack_count < TM1637_PANEL_TUBES) {
        Serial.printf("[tm1637]   >>> %d/%d tubes ACKed. CLK+ground reach SOME modules, so the shared path is "
                      "OK; the non-ACKing tube's own DIO wire is suspect.\n", ack_count, TM1637_PANEL_TUBES);
    } else {
        Serial.println("[tm1637]   >>> All four ACKed — CLK, ground and protocol all reach every module. "
                       "If the glass is still dark with all four ACKing, suspect segment/Vcc wiring at the module.");
    }
    delay(2000);

    /* --- Phase B: per-tube identity (isolates ONE dead DIO). -------------- */
    Serial.println("[tm1637] Phase B: per-tube identity — ONLY tube N lights, showing 'NNNN' (1.2s each)");
    uint8_t blank[TM1637_PANEL_DIGITS];
    for (int d = 0; d < TM1637_PANEL_DIGITS; d++) blank[d] = 0x00;
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        for (int j = 0; j < TM1637_PANEL_TUBES; j++)
            tm1637_write_segments(g_tubes[j].dev, 0, blank, TM1637_PANEL_DIGITS);
        const uint8_t g = tm1637_glyph((char)('1' + i), g_tubes[i].dev.map);
        uint8_t id[TM1637_PANEL_DIGITS];
        for (int d = 0; d < TM1637_PANEL_DIGITS; d++) id[d] = g;
        tm1637_write_segments(g_tubes[i].dev, 0, id, TM1637_PANEL_DIGITS);
        Serial.printf("[tm1637]   ONLY tube %d should read '%d%d%d%d'\n",
                      i + 1, i + 1, i + 1, i + 1, i + 1);
        delay(1200);
    }

    /* --- Phase C: walking single segment a..g,dp across all tubes. -------- */
    Serial.println("[tm1637] Phase C: walking single segment (raw bit a,b,c,d,e,f,g,dp) on all tubes (0.4s each)");
    for (int s = 0; s < 8; s++) {
        uint8_t pat[TM1637_PANEL_DIGITS];
        for (int d = 0; d < TM1637_PANEL_DIGITS; d++) pat[d] = (uint8_t)(1u << s);
        for (int i = 0; i < TM1637_PANEL_TUBES; i++)
            tm1637_write_segments(g_tubes[i].dev, 0, pat, TM1637_PANEL_DIGITS);
        delay(400);
    }

    /* --- Restore: normal brightness, repaint last-known bus values. ------- */
    for (int i = 0; i < TM1637_PANEL_TUBES; i++) {
        tm1637_set_brightness(g_tubes[i].dev, TM1637_PANEL_BRIGHTNESS);
        write_tube(g_tubes[i]);   /* repaint last[]; flush / 1 Hz service also would */
    }
    Serial.println("[tm1637] selftest complete — brightness restored to step 3, tubes repainted from bus values.");
}

void tm1637_panel_wiggle() {
    const uint8_t clk = TM1637_PANEL_CLK_PIN;    /* GPIO7  shared CLK */
    const uint8_t dio = TM1637_PANEL_DIO1_PIN;   /* GPIO8  tube-1 DIO */
    const int cycles = 20;

    Serial.printf("[wiggle] PUSH-PULL toggling CLK GPIO%u + DIO1 GPIO%u at 1 Hz for %d cycles (~%ds)\n",
                  (unsigned)clk, (unsigned)dio, cycles, cycles);
    Serial.println("[wiggle] CLK and DIO are on OPPOSITE phases, so they are distinguishable on the meter.");

    /* PUSH-PULL for the meter: OUTPUT + digitalWrite gives a clean 0 V / 3.3 V a
     * cheap DMM reads regardless of the module pull-up.  Safe now the whole panel
     * is 3.3 V — there is no 5 V line for the S3 pad to fight.  This is the SAME
     * drive discipline the panel now uses in production. */
    pinMode(clk, OUTPUT);
    pinMode(dio, OUTPUT);
    const uint32_t t0 = millis();
    for (int c = 0; c < cycles; c++) {
        digitalWrite(clk, HIGH);
        digitalWrite(dio, LOW);
        Serial.printf("[wiggle] t=%.1fs CLK=HIGH DIO=LOW\n", (millis() - t0) / 1000.0);
        delay(500);
        digitalWrite(clk, LOW);
        digitalWrite(dio, HIGH);
        Serial.printf("[wiggle] t=%.1fs CLK=LOW  DIO=HIGH\n", (millis() - t0) / 1000.0);
        delay(500);
    }

    /* Restore to the panel's push-pull idle: parked DRIVEN HIGH (matches the
     * production park; flush / the 1 Hz service repaint on the next tick). */
    park_high(clk);
    park_high(dio);
    Serial.println("[wiggle] done — pins restored to push-pull idle (driven HIGH).");
    Serial.println("[wiggle] meter GPIO7 & GPIO8 at the ESP32 pin AND at the module's CLK/DIO pins — "
                   "swinging 0<->3.3V at both ends = MCU driving + wire good; swinging at the pin but not "
                   "the module = broken/mis-wired; not swinging at the pin = MCU/firmware.");
}

#endif /* ARDUINO */
