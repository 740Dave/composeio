/*
 * yaml_min_canonical.cpp — compile the CANONICAL YAML-subset parser by
 * reference.  [CXC-R-BOOT] step 6 lives in cyd/src/yaml_min.{h,cpp} (the
 * display tier's on-device .compose.yaml parser).  This TU pulls it in
 * through -I${PROJECT_DIR}/../../cyd/src — NOT a fork, NOT a copy: the
 * headless controller tier parses the SAME .compose.yaml with the SAME
 * parser the display tier uses, and any parser change lands once in cyd/src.
 *
 * yaml_min is pure C++ (no ESP-IDF), so this compiles on host too and feeds
 * the controller_config host tests in env:native_test.
 */
#include "yaml_min.cpp"
