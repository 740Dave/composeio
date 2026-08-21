/*
 * bus_binding_canonical.cpp — compile the CANONICAL, host-tested repoint
 * binding CORE by reference.  [SPEC:CXC-REPOINT]
 *
 * cyd/src/bus_binding_core.{h,cpp} is PURE C++ (no ESP-IDF, no Arduino) and
 * is the VALIDATED reference — 83/83 host checks, independently re-run.  It
 * owns EVERY decision the mechanism makes (parse/validate, precedence,
 * value-compare/absorbing, arm/confirm/revert, the three-revert bounce latch,
 * the two-stage hatch, the two-plane confirm gate).  This TU pulls it in
 * through -I${PROJECT_DIR}/../../cyd/src so the Arduino glue
 * (firmware/reactive_core/src/bus_binding.cpp) and the ESP-IDF glue
 * (cyd/src/bus_binding.cpp) share ONE implementation of the logic —
 * reuse-by-reference, NEVER a fork.
 *
 * Guarded to the controller (config-fetched) tier: only the office_fan /
 * office_hepa / esp32c3 dual-throttle envs define CONTROLLER_CONFIG_FETCH and
 * link the Arduino glue that needs the core.  The pure demo/actuator envs and
 * the host test never reference it, so it is compiled out there.  The
 * native_bus_uri positive-control host env also pulls the core through this
 * same shim (BUS_URI_SELFTEST) so there is ONE way the core enters this tree.
 */
#if defined(CONTROLLER_CONFIG_FETCH) || defined(BUS_URI_SELFTEST)
#include "bus_binding_core.cpp"
#endif
