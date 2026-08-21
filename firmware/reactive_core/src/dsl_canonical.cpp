/*
 * dsl_canonical.cpp — compile the CANONICAL expression DSL by reference.
 *
 * [SPEC:CXC-DSL] lives in cyd/src/dsl.{h,cpp} (Pass 2b, corpus-pinned on
 * Xtensa + native; re-verified on RISC-V by cyd env:esp32c3_dsl_test).
 * This translation unit pulls it in through the -I${PROJECT_DIR}/../../
 * cyd/src include path — NOT a fork, NOT a copy: any DSL change lands in
 * cyd/src and every firmware picks it up on the next build.
 */
#include "dsl.cpp"
