/*
 * reactive_core_canonical.cpp — compile the CANONICAL R1 reactive core
 * (firmware/reactive_core/src/reactive_core.{h,cpp}) into the CYD
 * firmware BY REFERENCE.  [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-BOARDS]
 *
 * Same discipline as firmware/reactive_core/src/dsl_canonical.cpp (which
 * pulls in cyd/src/dsl.cpp): ONE core, compiled per target, never forked
 * — the C3 (R1), the actuator boards (R2), and the CYD (R3) all build
 * this exact translation unit.  The include path is provided by
 * src/CMakeLists.txt (INCLUDE_DIRS ../../firmware/reactive_core/src).
 */

#include "reactive_core.cpp"
