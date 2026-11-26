/*
 * tm1637_canonical.cpp — compile the CANONICAL TM1637 driver by reference.
 *
 * The TM1637 4/6-digit LED backend lives in cyd/src/tm1637.{h,cpp} (pure C++,
 * seam-injected open-drain bit-bang, host-tested waveform-by-waveform in
 * cyd/src/tm1637_selftest.cpp).  This translation unit pulls it in through the
 * -I${PROJECT_DIR}/../../cyd/src include path — NOT a fork, NOT a copy: any
 * driver change lands in cyd/src and every firmware picks it up on the next
 * build.  Same reuse-by-reference idiom as dsl_canonical.cpp / restart_
 * canonical.cpp ([DX-NARROW-WAIST]: reuse by reference, never fork).
 *
 * tm1637.cpp is pure C++ (includes only tm1637.h + <cstdio>/<cstring>), so this
 * compiles on host too and feeds the reactive_core TM1637 panel host tests in
 * env:native_test.
 */
#include "tm1637.cpp"
