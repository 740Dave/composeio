/*
 * dsl_std_math_shim.h — force-included (C++ only) for env:ch32v203_dsl_test.
 *
 * The WCH RISC-V toolchain's libstdc++ (<cmath>, gcc 12.2.0) gates the C99
 * math additions `copysign` and `round` behind _GLIBCXX_USE_C99_MATH_TR1,
 * which its config leaves undefined — so `std::copysign` / `std::round`
 * are absent even though newlib declares ::copysign (math.h:312) and
 * ::round (math.h:328) in the global namespace.
 *
 * cyd/src/dsl.cpp is REUSED BY REFERENCE (byte-identical with the ESP /
 * host builds, where these ARE in std) and must NOT be edited, so we hoist
 * exactly those two globals into `std` here. Nothing else is touched, and
 * this is a no-op if a future toolchain enables the TR1 block. Force-
 * enabling _GLIBCXX_USE_C99_MATH_TR1 instead is avoided on purpose — its
 * block also `using ::roundf/::copysignl/...`, single/long-double variants
 * newlib may not declare.
 */

#ifndef DSL_STD_MATH_SHIM_H
#define DSL_STD_MATH_SHIM_H

#include <math.h>
#include <cmath>

#ifndef _GLIBCXX_USE_C99_MATH_TR1
namespace std {
using ::copysign;
using ::round;
}  // namespace std
#endif

#endif  // DSL_STD_MATH_SHIM_H
