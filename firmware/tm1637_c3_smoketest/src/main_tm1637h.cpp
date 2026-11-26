/*
 * main_tm1637h.cpp — CLASSIC-LIBRARY control for the TM1637 smoke test.
 *
 * David trusts the classic Arduino TM1637 library (header <TM1637.h>, class
 * TM1637, author Frankie Chu / SeeedStudio "Grove 4-Digit Display", MIT).
 * RobTillaart/TM1637_RT lit the module but rendered garbage (its internal
 * encoding does not match these breakouts); this classic library's segment
 * table (tube_tab: 0=0x3f .. 8=0x7f, bit0=a..bit6=g) is IDENTICAL to our
 * driver's default map, so if it counts CLEANLY it is the right model for our
 * fix.
 *
 * Like RobTillaart — and unlike our original seams — this library drives the
 * lines PUSH-PULL (constructor sets both pins OUTPUT; writeByte does
 * digitalWrite(dataPin, HIGH) for a 1-bit), floating DIO to INPUT only for the
 * ACK read.  Two independent trusted references driving push-pull is the
 * evidence our open-drain-via-Hi-Z release was the bug.
 *
 * ISOLATION: guarded by TM1637H_CONTROL (defined only in
 * [env:esp32-c3-tm1637h]); inert empty TU elsewhere.  That env compiles ONLY
 * this file and has no -I to cyd/src, so <TM1637.h> resolves to the classic
 * library, never our lowercase cyd/src/tm1637.h.
 */

#ifdef TM1637H_CONTROL

#include <Arduino.h>
#include <TM1637.h>   // SeeedStudio "Grove 4-Digit Display" — class TM1637

// SAME pins as the our-driver test: CLK=GPIO4, DIO=GPIO3.  Module VCC=3.3V.
static const int CLK_PIN = 4;
static const int DIO_PIN = 3;

static TM1637 g_tm(CLK_PIN, DIO_PIN);

void setup() {
    Serial.begin(115200);
    delay(300);

    Serial.println();
    Serial.println("==== TM1637 C3 smoke test [CLASSIC: SeeedStudio Grove TM1637.h] ====");
    Serial.printf("pins: CLK=GPIO%d  DIO=GPIO%d  (VCC=3.3V, GND=GND)\n",
                  CLK_PIN, DIO_PIN);
    Serial.println("classic library drive; glass is the verdict (no ACK reported)");

    g_tm.set(BRIGHTEST);   // 0x88 + 7; takes effect on next display
    g_tm.init();           // clears

    // "all segments on"-ish: 8888 (every 7-seg segment) + colon, ~1s.
    g_tm.point(POINT_ON);
    int8_t all8[4] = {8, 8, 8, 8};
    g_tm.display(all8);
    delay(1000);
    g_tm.point(POINT_OFF);
}

void loop() {
    // 0 -> 100 -> 0, 0.5s/step, via the library's own number formatter.
    for (int v = 0; v <= 100; v++) { g_tm.displayNum(v); delay(500); }
    for (int v = 100; v >= 0; v--) { g_tm.displayNum(v); delay(500); }
}

#endif /* TM1637H_CONTROL */
