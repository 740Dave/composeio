/*
 * main_reflib.cpp — REFERENCE-LIBRARY CONTROL for the TM1637 smoke test.
 *
 * WHY.  With OUR driver (cyd/src/tm1637.cpp) the bare-C3 test is DARK, ACK=NO
 * on every write — the SAME failure as the soldered S3 board, which exonerates
 * that board and leaves two suspects: OUR driver, or the module itself.  This
 * TU drives the SAME module on the SAME pins with a KNOWN-GOOD third-party
 * Arduino library (RobTillaart/TM1637_RT, MIT, actively maintained) that
 * shares NONE of our code.  It is the definitive split:
 *   - lights here  => our driver is at fault (module is fine).
 *   - dark here too => the module (or its wiring/level) is at fault.
 *
 * ISOLATION.  This whole file is guarded by REFLIB_CONTROL, which is defined
 * ONLY in [env:esp32-c3-reflib].  In the our-driver [env:esp32-c3] this file
 * compiles to an EMPTY translation unit (no library include, no setup/loop),
 * so the two envs never collide and the our-driver env's block is untouched.
 * The reflib env's build_src_filter compiles ONLY this file, so the control
 * binary contains none of our driver.  The reflib env also has NO -I to
 * cyd/src, so <TM1637.h> below resolves to the library, never our lowercase
 * cyd/src/tm1637.h (macOS is case-insensitive; keeping our dir off the path
 * is what makes that unambiguous).
 *
 * No serial ACK — the reference library exposes none, and it is not needed:
 * this test is purely "does a known-good library light the module."  The glass
 * is the verdict.  A boot banner still names the env + pins.
 *
 * NOTE ON THE "ALL SEGMENTS ON" STEP.  RobTillaart's displayRaw() is NOT a
 * byte-raw API — it reinterprets each byte as a DIGIT VALUE (<=18 hex, <=37
 * ASCII), so a literal 0xFF would render BLANK, not all-segments-on.  The
 * supported, tested path to the brief's "solid 88:88" is displayTime(88,88,
 * colon=true): four lit '8's (all seven segments per digit) plus the colon.
 * The only segments it does not light are the four decimal points, which this
 * module's tested API does not expose independently — an ample liveness
 * pattern, and the exact "88:88" glyph the brief describes.
 */

#ifdef REFLIB_CONTROL

#include <Arduino.h>
#include <TM1637.h>   // RobTillaart/TM1637_RT — class TM1637

// SAME pins as the our-driver test: CLK=GPIO4, DIO=GPIO3.  Module VCC=3.3V.
static const int CLK_PIN = 4;
static const int DIO_PIN = 3;

static TM1637 g_display;

void setup() {
    Serial.begin(115200);
    delay(300);   // let USB-CDC enumerate before the first print

    Serial.println();
    Serial.println("==== TM1637 C3 smoke test [REFLIB CONTROL: RobTillaart/TM1637_RT] ====");
    Serial.printf("pins: CLK=GPIO%d  DIO=GPIO%d  (VCC=3.3V, GND=GND)\n",
                  CLK_PIN, DIO_PIN);
    Serial.println("known-good library drive; glass is the verdict (no ACK reported)");

    g_display.begin(CLK_PIN, DIO_PIN, 4 /* 4-digit module */);
    g_display.setBrightness(7 /* max; on-bit is baked into the display cmd */);

    // "solid 88:88" for ~1s — four lit '8's + colon (see the note above).
    g_display.displayTime(88, 88, true /* colon */);
    delay(1000);
}

void loop() {
    // 0 -> 100 -> 0, 0.5s/step.  displayInt(v, hideLeadingZeros=true) is
    // right-justified and blank-padded — the same shape as the our-driver test.
    for (int v = 0; v <= 100; v++) {
        g_display.displayInt(v, true);
        delay(500);
    }
    for (int v = 100; v >= 0; v--) {
        g_display.displayInt(v, true);
        delay(500);
    }
}

#endif /* REFLIB_CONTROL */
