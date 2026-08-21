/*
 * restart_canonical.cpp — compile the CANONICAL remote config-reload handler
 * by reference.  [CXC-ER-REMOTE-RESTART] / [CXC-ER-RESTART-BUS] live in
 * cyd/src/restart.{h,cpp} (pure C++, seam-injected reboot + ack publish).
 * This TU pulls it in through -I${PROJECT_DIR}/../../cyd/src so the headless
 * controller shell honors `_device/<id>/restart` the SAME way the display
 * tier (qualia_main.cpp) does — reuse-by-reference, never a fork.
 *
 * restart.cpp is pure C++ (no ESP-IDF), so this compiles on host too and
 * feeds the relay/reload host tests in env:native_test.
 */
#include "restart.cpp"
