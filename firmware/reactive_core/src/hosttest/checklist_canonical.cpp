/*
 * checklist_canonical.cpp — compile the CANONICAL ChecklistWidget by
 * reference for the native_test parity harness (host-tested RAttr::
 * ChecklistItems apply, [SPEC:CXC-EMBED-REACTIVE] [CXC-RM-M5]).
 *
 * The widget lives in cyd/src/checklist.cpp (pure C++: only the abstract
 * Canvas contract + the ScrollList substrate — no ESP-IDF).  This TU pulls
 * it in through -I${PROJECT_DIR}/../../cyd/src (+ cyd/include for the
 * widget headers) — NOT a fork, NOT a copy.  ONLY the native_test env
 * compiles src/hosttest/ (every other env excludes it, -<hosttest/>), so
 * the headless controller firmware never links widget code.
 */
#include "checklist.cpp"
