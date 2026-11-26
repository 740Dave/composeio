/*
 * main.cpp — TM1637 first-light smoke test on a bare ESP32-C3, driving the
 * CANONICAL driver (cyd/src/tm1637.{h,cpp}, compiled by reference via
 * tm1637_canonical.cpp).
 *
 * ============ ROOT CAUSE THIS BUILD FIXES ============
 * The first cut of this test left every tube DARK with ACK=NO — the same
 * failure as the soldered S3 board.  The driver's PROTOCOL was never wrong:
 * it is byte-identical, edge-for-edge, to two independent known-good
 * libraries (RobTillaart/TM1637_RT and the classic SeeedStudio Grove
 * <TM1637.h>).  What was wrong was the SEAM'S DRIVE DISCIPLINE.
 *
 * The original seams released a line HIGH by going Hi-Z (pinMode INPUT) and
 * trusting the module's pull-up to raise it.  On a cheap 4-digit breakout at
 * 3.3 V with a MARGINAL breadboard link that pull-up never lifts the line to
 * a valid HIGH: CLK presents no clean rising edge, the START condition never
 * forms, nothing latches, no ACK — dark.  BOTH trusted libraries instead
 * DRIVE THE LINE HIGH (push-pull) and float DIO to INPUT only for the ACK
 * read.  That is why the SAME module on the SAME pins lit under them and
 * stayed dark under us.
 *
 * The fix lives in the SHARED seam cyd/src/tm1637_arduino_seam.h (push-pull
 * drive), so it propagates to every board that bit-bangs a TM1637 — including
 * the reactive_core S3 4-tube panel (same driver, same bug).  The driver's
 * byte sequence is UNCHANGED (host-test 792-count intact).
 *
 * ============ PINS — CONFIRMED SAFE ON ESP32-C3 ============
 * CLK = GPIO4, DIO = GPIO3.  Not strapping (C3 straps are GPIO2/8/9), not the
 * native-USB pins (GPIO18/19), both broken out on the SuperMini edge.  GPIO4
 * doubles as JTAG MTMS but that only matters under a live debugger.
 */

#include <Arduino.h>
#include "tm1637.h"
#include "tm1637_arduino_seam.h"   // SHARED push-pull seam (via -I cyd/src)

// ---- pins (see the safety note above) -------------------------------------
static const int CLK_PIN = 4;   // TM1637 CLK
static const int DIO_PIN = 3;   // TM1637 DIO

// The seam's pin/ctx struct — kept alive for the life of the device.
// read_settle_us: after floating DIO to INPUT, wait before sampling the ACK.
static Tm1637ArduinoPins g_pins = { (uint8_t)CLK_PIN, (uint8_t)DIO_PIN, /*read_settle_us=*/8 };

// GENEROUS clock: 50 us half-period (10 kHz).  Push-pull already makes the
// rise fast, but a slack clock buys extra immunity to a marginal connection —
// the datasheet floor is 1 us, so this is 50x margin and still ~5 ms for a
// full 6-byte refresh, thousands of times faster than any value changes.
static const uint8_t  DIGITS         = 4;
static const uint32_t HALF_PERIOD_US = 50;
static const uint8_t  BRIGHTNESS     = 3;

static Tm1637 g_dev;

static const char* yn(bool ok) { return ok ? "yes" : "NO"; }

void setup() {
    Serial.begin(115200);
    delay(300);   // let USB-CDC enumerate before the first print

    Serial.println();
    Serial.println("==== TM1637 C3 smoke test [OUR DRIVER, push-pull seam] ====");
    Serial.printf("pins: CLK=GPIO%d  DIO=GPIO%d  (VCC=3.3V, GND=GND)\n",
                  CLK_PIN, DIO_PIN);
    Serial.printf("config: %u digits, half_period=%luus (~%lu kHz), brightness=%u\n",
                  (unsigned)DIGITS, (unsigned long)HALF_PERIOD_US,
                  (unsigned long)(500UL / (HALF_PERIOD_US ? HALF_PERIOD_US : 1)),
                  (unsigned)BRIGHTNESS);

    Tm1637Bus bus = tm1637_arduino_bus(g_pins);

    // Idle the bus HIGH (push-pull) before the first transaction.
    bus.clk_release(bus.ctx);
    bus.dio_release(bus.ctx);
    delay(5);

    tm1637_init(g_dev, bus, DIGITS, HALF_PERIOD_US);
    if (g_dev.timing_was_clamped) {
        Serial.println("WARN: half_period was clamped UP to the driver minimum");
    }

    // tm1637_init issues no bus traffic; the first ACK comes from the display-
    // control command that set_brightness / set_on send.  Print both so we get
    // an electrical signal even before any glyph is on the glass.
    bool ack_bright = tm1637_set_brightness(g_dev, BRIGHTNESS);
    bool ack_on     = tm1637_set_on(g_dev, true);
    Serial.printf("init ACK: set_brightness=%s  set_on=%s\n",
                  yn(ack_bright), yn(ack_on));

    // ---- all segments ON: a solid 88:88 for ~1s -----------------------------
    uint8_t all_on[DIGITS];
    for (uint8_t i = 0; i < DIGITS; i++) all_on[i] = 0xFF;   // a..g + dp + colon
    bool ack_allon = tm1637_write_segments(g_dev, 0, all_on, DIGITS);
    Serial.printf("all-segments-ON (0xFF x%u -> solid 88:88) ACK=%s\n",
                  (unsigned)DIGITS, yn(ack_allon));
    delay(1000);
}

void loop() {
    // Sweep 0 -> 100 -> 0, 0.5s/step, right-justified (blank-padded left) —
    // the SAME formatting policy as the raster seven_segment widget, done in
    // the backend where the digit count lives.
    Tm1637Format fmt;
    fmt.decimals = -1;     // render as an integer
    fmt.pad_zero = false;  // blank-pad left (so "42" reads forty-two, not 0042)

    for (int dir = 0; dir < 2; dir++) {
        int start = dir == 0 ? 0 : 100;
        int end   = dir == 0 ? 100 : 0;
        int step  = dir == 0 ? 1 : -1;
        for (int v = start; v != end + step; v += step) {
            uint8_t seg[DIGITS];
            tm1637_format_number(g_dev, (double)v, fmt, seg);
            bool ack = tm1637_write_segments(g_dev, 0, seg, g_dev.digits);
            Serial.printf("value=%3d  write ACK=%s\n", v, yn(ack));
            delay(500);
        }
    }
}
