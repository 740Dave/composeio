/*
 * asset_reload_canonical.cpp — compile the CANONICAL asset-change-notify
 * consumer by reference.  [CXC-ASSET-CHANGE-NOTIFY] [CXC-ACN-SHAPE] live in
 * cyd/src/asset_reload.{h,cpp} (pure C++, hash-string decisions only).  This
 * TU pulls it in through -I${PROJECT_DIR}/../../cyd/src so the headless
 * controller shell (controller_main.cpp) picks up a bus_storage asset change
 * automatically — the SAME reuse-by-reference idiom as restart_canonical.cpp,
 * never a fork.
 *
 * asset_reload.cpp is pure C++ (no ESP-IDF, no mbedtls), so this compiles on
 * host too and feeds the consumer host tests in env:native_test.
 */
#include "asset_reload.cpp"
