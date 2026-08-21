/*
 * master_detail_canonical.cpp — compile the CANONICAL MasterDetailWidget by
 * reference for native_test (host-tested RAttr::NavSelection apply / index_for,
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-RM-M5]).  Pure C++ (abstract Canvas + the
 * ScrollList + text_wrap substrates).  Pulled in via
 * -I${PROJECT_DIR}/../../cyd/src (+ cyd/include).  native_test-only
 * (src/hosttest/, excluded from every device env).
 */
#include "master_detail.cpp"
