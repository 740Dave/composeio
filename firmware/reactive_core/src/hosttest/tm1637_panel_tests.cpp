/*
 * tm1637_panel_tests.cpp — host tests for the TM1637 panel's PURE logic:
 * the thrust->display mapping and digit encoding.  [SPEC:CXC-VISION-EPISTEMICS]
 * [CXC-VISION-SEMANTIC-REALIZATION]  Own counters, folded into host_test_main.
 *
 * ⛔ WHY THIS EXISTS.  A bit-banged 7-segment surface is the canonical silent
 * failure: a wrong justification or a wrong digit still renders well-formed
 * glyphs, every transfer returns success, and the only observer is a human at
 * the tube.  The value->digits decision is therefore proven HERE, on the host,
 * before any glass is trusted.  This suite proves the "0..100 on the rightmost
 * three digits" requirement STRUCTURALLY (which digit carries which glyph),
 * not by re-asserting absolute segment hex the cyd driver selftest already
 * covers.
 *
 * ⛔ MACHINE-GREEN != GLASS-GREEN.  These checks prove the mapping is right.
 * The segment-map->physical-segment wiring is a MODULE fact only eyes on a
 * tube can confirm ([CXC-VISION-EPISTEMICS]).
 */
#include "../tm1637/tm1637_panel.h"

#include <cstdio>
#include <string>

static int tp_checks = 0;
static int tp_fail = 0;

#define PCHECK(cond, msg) do {                                              \
    tp_checks++;                                                            \
    if (!(cond)) { tp_fail++; printf("FAIL [tm1637] %s (line %d)\n", msg, __LINE__); } \
} while (0)

/* A 4-digit Tm1637 with NO real bus — tm1637_init + the formatters are pure
 * (they never call a bus function), so null seams are safe. */
static Tm1637 make_dev() {
    Tm1637 d;
    Tm1637Bus b{};   /* all-null seams — never invoked by init/format */
    tm1637_init(d, b, TM1637_PANEL_DIGITS, TM1637_PANEL_HALF_PERIOD_US);
    return d;
}

int tm1637_panel_tests(int* out_checks) {
    tp_checks = 0;
    tp_fail = 0;

    Tm1637 d = make_dev();
    const uint8_t BLANK = 0x00;
    const uint8_t g0 = tm1637_glyph('0', d.map);
    const uint8_t g1 = tm1637_glyph('1', d.map);
    const uint8_t g2 = tm1637_glyph('2', d.map);
    const uint8_t g4 = tm1637_glyph('4', d.map);

    /* digit encoding is real (a blank is not a digit; digits differ) */
    PCHECK(g0 != BLANK && g1 != BLANK && g2 != BLANK && g4 != BLANK,
           "glyphs 0/1/2/4 are non-blank segment bytes");
    PCHECK(g0 != g1 && g1 != g2 && g0 != g2 && g1 != g4,
           "distinct digits encode to distinct segment bytes (not a stuck table)");
    PCHECK(tm1637_glyph(' ', d.map) == BLANK, "space encodes to a BLANK digit");

    uint8_t out[TM1637_PANEL_DIGITS];

    /* --- 42 %: rightmost two digits, leftmost two blank --- */
    tm1637_panel_format_thrust(d, 42.0, out);
    PCHECK(out[0] == BLANK && out[1] == BLANK && out[2] == g4 && out[3] == g2,
           "thrust 42 -> {blank,blank,'4','2'} (rightmost, leftmost blank)");

    /* --- 100 %: three digits on the RIGHTMOST THREE, leftmost blank --- */
    tm1637_panel_format_thrust(d, 100.0, out);
    PCHECK(out[0] == BLANK && out[1] == g1 && out[2] == g0 && out[3] == g0,
           "thrust 100 -> {blank,'1','0','0'} (fills exactly the rightmost 3)");

    /* --- 0 %: a single rightmost zero, the rest blank --- */
    tm1637_panel_format_thrust(d, 0.0, out);
    PCHECK(out[0] == BLANK && out[1] == BLANK && out[2] == BLANK && out[3] == g0,
           "thrust 0 -> {blank,blank,blank,'0'}");

    /* --- fractional truncates toward zero (no over-range rounding) --- */
    tm1637_panel_format_thrust(d, 99.7, out);
    PCHECK(out[0] == BLANK && out[1] == BLANK &&
           out[2] == tm1637_glyph('9', d.map) && out[3] == tm1637_glyph('9', d.map),
           "thrust 99.7 -> \"99\" (TRUNCATES toward zero, not \"100\")");

    /* --- clamp ABOVE range: 150 -> 100 (never paints a 4th digit) --- */
    tm1637_panel_format_thrust(d, 150.0, out);
    PCHECK(out[0] == BLANK && out[1] == g1 && out[2] == g0 && out[3] == g0,
           "thrust 150 CLAMPS to 100 (still {blank,'1','0','0'})");

    /* --- clamp BELOW range: -5 -> 0 --- */
    tm1637_panel_format_thrust(d, -5.0, out);
    PCHECK(out[0] == BLANK && out[1] == BLANK && out[2] == BLANK && out[3] == g0,
           "thrust -5 CLAMPS to 0 -> {blank,blank,blank,'0'}");

    /* --- the subscribed namespace list: tube order, no vendor name --- */
    const char* ns[8];
    size_t n = tm1637_panel_namespaces(ns, 8);
    PCHECK(n == 4, "panel subscribes exactly four namespaces");
    PCHECK(std::string(ns[0]) == "throttle/left" && std::string(ns[1]) == "throttle/right",
           "tubes 1&2 subscribe throttle/left + throttle/right (the gauge's namespaces)");
    bool no_vendor = true;
    for (size_t i = 0; i < n; i++)
        if (std::string(ns[i]).find("r503") != std::string::npos ||
            std::string(ns[i]).find("R503") != std::string::npos ||
            std::string(ns[i]).find("fingerprint") != std::string::npos)
            no_vendor = false;
    PCHECK(no_vendor, "no tube namespace names the vendor or the sensor half");

    if (out_checks) *out_checks += tp_checks;
    if (tp_fail == 0)
        printf("   [tm1637] %d checks, 0 failed  — panel value->digits MACHINE-GREEN "
               "(GLASS still pending: segment-map wiring is a module fact).\n", tp_checks);
    else
        printf("   [tm1637] %d checks, %d FAILED\n", tp_checks, tp_fail);
    return tp_fail;
}
