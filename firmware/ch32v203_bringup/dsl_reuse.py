#
# dsl_reuse.py — PRE-script for env:ch32v203_dsl_test.
#
# Compiles the SHARED, board-agnostic ComposeUX expression-DSL and its
# conformance-corpus harness straight from cyd/src, REUSED BY REFERENCE
# (byte-identical with the ESP32-C3 / Xtensa dsl_test envs — never forked):
#
#     cyd/src/dsl.cpp
#     cyd/src/dsl_selftest.cpp     (+ cyd/test/dsl_corpus_generated.h, 234 cases)
#
# Two jobs:
#   1. Add cyd/src + cyd/test to the include path (dsl.h + the generated
#      corpus header) and pull the two shared .cpp into THIS build without
#      copying them.
#   2. dsl_selftest.cpp defines its own host `int main()` (the non-ESP
#      branch). That clashes with the Arduino core's `main()`. We rename it
#      for THIS one object only, via an additive per-file
#      `-Dmain=dsl_selftest_hosted_main_unused`, so the shared source stays
#      untouched. The renamed function is unused and dropped by
#      --gc-sections; the harness entry point we call is the (extern "C")
#      `dsl_selftest_run()`.
#
# Deliberately NOT touched: optimisation stays at the core default `-Os`
# (NO -ffast-math / -Ofast) so `double` keeps standard IEEE-754 soft-float
# (libgcc) semantics — the whole point of the bit-exactness proof.
#

Import("env")
import os

project_dir = env.subst("$PROJECT_DIR")
repo_root = os.path.realpath(os.path.join(project_dir, "..", ".."))
cyd_src = os.path.join(repo_root, "cyd", "src")
cyd_test = os.path.join(repo_root, "cyd", "test")

# dsl.h lives in cyd/src, the generated corpus header in cyd/test.
env.Append(CPPPATH=[cyd_src, cyd_test])

# C++-only force-include: hoists std::copysign / std::round into std on the
# WCH toolchain (its <cmath> gates them behind an undefined TR1 macro), so
# cyd/src/dsl.cpp compiles UNMODIFIED. CXXFLAGS -> C++ TUs only (not C).
env.Append(
    CXXFLAGS=["-include", os.path.join(project_dir, "dsl_std_math_shim.h")]
)


def rename_host_main(env, node):
    # Additive: only $CPPFLAGS is overridden (with itself + the rename);
    # $CCFLAGS / $CPPDEFINES / $CPPPATH stay env-referenced and expand
    # normally at build time.
    obj = env.Object(
        node,
        CPPFLAGS=["$CPPFLAGS", "-Dmain=dsl_selftest_hosted_main_unused"],
    )
    return obj[0] if isinstance(obj, (list, tuple)) else obj


env.AddBuildMiddleware(rename_host_main, "*dsl_selftest.cpp")

env.BuildSources(
    os.path.join("$BUILD_DIR", "cyd_dsl"),
    cyd_src,
    src_filter=["-<*>", "+<dsl.cpp>", "+<dsl_selftest.cpp>"],
)
