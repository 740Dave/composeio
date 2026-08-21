/*
 * dsl_ch32_main.cpp — CH32V203C8T6 (QingKe V4B RISC-V) on-chip runner for
 * the [SPEC:CXC-DSL] conformance corpus.
 *
 * This is the THIRD RISC-V-class proof of soft-`double` bit-exactness for
 * the ComposeUX expression-DSL, after the ESP32-C3 (RISC-V) and the Xtensa
 * cores.  The QingKe V4B has NO double-precision FPU, so every `double` in
 * the DSL is IEEE-754 soft-float via libgcc — built at -Os with NO
 * -ffast-math, so the 234-case corpus must reproduce bit-for-bit.
 *
 * Everything load-bearing is REUSED BY REFERENCE, byte-identical with the
 * ESP dsl_test envs (see dsl_reuse.py):
 *     cyd/src/dsl.cpp           — the interpreter
 *     cyd/src/dsl_selftest.cpp  — the harness (extern "C" dsl_selftest_run)
 *     cyd/test/dsl_corpus_generated.h — the 234 cases (flash .rodata)
 *
 * This file ONLY: brings up the chip's native USB CDC (the openwch core's
 * TinyUSB `Serial`), retargets C stdio (the harness prints via printf) to
 * it, and calls dsl_selftest_run() on a ~2 s repeat so the dispatcher can
 * catch the summary on the serial console after flashing.
 */

#include <Arduino.h>
#include <cstdio>
#include <unistd.h>   /* STDOUT_FILENO / STDERR_FILENO */

/* The shared harness (cyd/src/dsl_selftest.cpp).  Prints one line per
 * failure, then "N/234 conformance cases pass"; returns 0 iff all pass. */
extern "C" int dsl_selftest_run(void);

/* Retarget newlib stdout/stderr (printf/putchar/puts, used by the harness)
 * to the USB CDC.  Overrides the core's weak _write (which targets the
 * debug UART).  Signature matches the core's weak decl (char*, not const)
 * to satisfy the C++ ODR. */
extern "C" int _write(int file, char *ptr, int len) {
    if (len > 0 && (file == STDOUT_FILENO || file == STDERR_FILENO)) {
        Serial.write(reinterpret_cast<const uint8_t *>(ptr),
                     static_cast<size_t>(len));
    }
    return len;
}

void setup() {
    Serial.begin(115200);                 /* baud ignored on USB CDC */
    setvbuf(stdout, nullptr, _IONBF, 0);  /* unbuffered: lines reach USB now */
    /* The USB device enumerates a couple of seconds after boot; the ~2 s
     * loop below repeats the summary, so a late console still catches it. */
    delay(1500);
}

void loop() {
    printf("\n[CXC-DSL] CH32V203C8T6 (QingKe V4B, RISC-V) conformance run "
           "-- float64 soft-double, IEEE-754\n");

    int rc = dsl_selftest_run();          /* prints failures + N/234 line */

    if (rc == 0) {
        printf("DSL corpus: 234/234 PASS\n");
    } else {
        printf("DSL corpus: FAIL (see the N/234 line above)\n");
    }

    delay(2000);
}
