/*
 * bus_role_selftest.cpp — host unit test for the ROLE axis + capability gate
 * ([SPEC:CXC-DEVICE-BASELINE] cap 6d + [CXC-DEVICE-BASELINE-CAPABILITY-THRESHOLD]).
 * Built by [env:native_role]; PURE C++, no ESP-IDF.
 *
 * ⛔ NEGATIVE CONTROLS ([CXC-VISION-EPISTEMICS]):
 *   - the CH32V003 floor MUST FAIL the capability gate (the whole point of the
 *     gate is to reject it) — if it passed, the gate proves nothing;
 *   - an undeclared board (0/0 resources) MUST FAIL CLOSED, never be silently
 *     promoted to mothercard;
 *   - an UNKNOWN role MUST resolve known=false and MUST NOT fall back to a
 *     working role (a device lying about its own configuration);
 *   - set_mode("mothercard") on a sub-threshold board MUST be REFUSED.
 * The gate is NEVER a chip allowlist — ESP32/RP2040 are checked by their
 * declared RESOURCES, exactly as the CH32V003 is.
 */

#include "bus_transport_select.h"

#include <cstdio>
#include <cstring>

static int g_fail = 0;
#define CHECK(cond) do { \
    if (!(cond)) { printf("  FAIL: %s  (line %d)\n", #cond, __LINE__); g_fail++; } \
} while (0)

static void test_mode_resolve() {
    printf("test: transport_mode_resolve (role axis)\n");
    TransportMode d = transport_mode_resolve(nullptr);
    CHECK(d.name == "standalone" && d.source == "default" && d.known);
    CHECK(!d.needs_capability);

    TransportMode m = transport_mode_resolve("mothercard");
    CHECK(m.name == "mothercard" && m.source == "nvs" && m.known);
    CHECK(m.needs_capability);          /* only mothercard needs the gate */

    TransportMode g = transport_mode_resolve("daughtercard");
    CHECK(g.known && !g.needs_capability);

    /* NEGATIVE: an unknown role does NOT fall back to standalone */
    TransportMode bad = transport_mode_resolve("boss");
    CHECK(bad.name == "boss" && !bad.known);
}

static void test_capability_gate() {
    printf("test: board_can_host_baseline (capability gate, resources not chips)\n");
    /* ESP32-class: ~320 KB RAM, 4 MB flash -> clears */
    CHECK(board_can_host_baseline(320, 4096) == true);
    /* RP2040: 264 KB RAM, 2 MB flash -> clears (David's own exemplar) */
    CHECK(board_can_host_baseline(264, 2048) == true);
    /* NEGATIVE CONTROL — the CH32V003 floor: 2 KB RAM, 16 KB flash -> FAILS */
    CHECK(board_can_host_baseline(2, 16) == false);
    /* NEGATIVE — undeclared resources fail CLOSED (never silently promoted) */
    CHECK(board_can_host_baseline(0, 0) == false);
    CHECK(board_can_host_baseline(-1, 4096) == false);
    /* boundary: exactly at the threshold clears; one below fails */
    CHECK(board_can_host_baseline(64, 512) == true);
    CHECK(board_can_host_baseline(63, 512) == false);
    CHECK(board_can_host_baseline(64, 511) == false);
}

static void test_uart_pins() {
    printf("test: uart_pins_resolve (NVS over board_profile default)\n");
    /* both unset -> profile defaults, source "profile" */
    UartPins p = uart_pins_resolve(-1, -1, 17, 16);
    CHECK(p.tx == 17 && p.rx == 16 && p.source == "profile");
    /* tx overridden -> nvs tx, profile rx, source "nvs" */
    UartPins q = uart_pins_resolve(43, -1, 17, 16);
    CHECK(q.tx == 43 && q.rx == 16 && q.source == "nvs");
    /* both overridden */
    UartPins r = uart_pins_resolve(43, 44, 17, 16);
    CHECK(r.tx == 43 && r.rx == 44 && r.source == "nvs");
    /* gpio 0 is a valid override (>= 0), not "unset" */
    UartPins s = uart_pins_resolve(0, -1, 17, 16);
    CHECK(s.tx == 0 && s.source == "nvs");
}

static void test_set_mode_refusals() {
    printf("test: bus_transport_set_mode refusals (host)\n");
    /* unknown role refused */
    CHECK(bus_transport_set_mode("boss", 320, 4096) == false);
    /* NEGATIVE CONTROL: mothercard on the CH32V003 floor is REFUSED */
    CHECK(bus_transport_set_mode("mothercard", 2, 16) == false);
    /* mothercard on a capable board is accepted */
    CHECK(bus_transport_set_mode("mothercard", 320, 4096) == true);
    /* daughtercard/standalone accepted regardless of resources */
    CHECK(bus_transport_set_mode("daughtercard", 2, 16) == true);
    CHECK(bus_transport_set_mode("standalone", 2, 16) == true);
}

int main() {
    printf("=== bus_role_selftest [CXC-DEVICE-BASELINE cap 6d + capability] ===\n");
    test_mode_resolve();
    test_capability_gate();
    test_uart_pins();
    test_set_mode_refusals();
    if (g_fail) { printf("FAILED: %d check(s)\n", g_fail); return 1; }
    printf("PASS: all role/capability checks (incl. negative controls)\n");
    return 0;
}
