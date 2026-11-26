/*
 * tm1637_canonical.cpp — compile the CANONICAL TM1637 driver BY REFERENCE.
 *
 * The driver lives in cyd/src/tm1637.{h,cpp} (pure C++, seam-injected pins).
 * This translation unit pulls the .cpp in through
 * -I${PROJECT_DIR}/../../cyd/src — NOT a fork, NOT a copy.  The whole point of
 * this smoke test is that a lit display validates OUR driver, so it MUST be
 * the very same source the S3 board compiles.  The SAME reuse-by-reference
 * idiom as firmware/reactive_core/src/restart_canonical.cpp.
 */
#include "tm1637.cpp"
