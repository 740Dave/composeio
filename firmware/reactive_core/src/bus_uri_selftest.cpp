/*
 * bus_uri_selftest.cpp — host positive-control for the PURE logic the Arduino
 * bus_binding glue adds over the host-tested core.  [SPEC:CXC-REPOINT]
 * [CXC-VISION-EPISTEMICS] (an instrument that can only report SUCCESS is not an
 * instrument — every check here has a NEGATIVE control beside its positive one).
 *
 *   pio run -e native_bus_uri && .pio/build/native_bus_uri/program
 * Must print "ALL PASS" and exit 0.
 *
 * Two things are proven here:
 *  1. bus_uri_host_port() — the mqtt://host:port split PubSubClient needs
 *     (the glue's only new pure code), across scheme/no-scheme/bare/path forms.
 *  2. THE POSITIVE CONTROL THE cyd/ QA DEMANDED: that a REAL, echoing broker
 *     plus a reachable store makes a commit-confirmed trial COMMIT — not merely
 *     that a bad one reverts.  This exercises binding_confirm_ready exactly as
 *     the glue's try_confirm() does (store_required=true under config:http),
 *     with the positive case (both planes) AND the negatives (each plane alone).
 */
#ifdef BUS_URI_SELFTEST

#include "bus_uri.h"
#include "bus_binding_core.h"

#include <cstdio>
#include <cstring>

static int s_pass = 0, s_fail = 0;
#define CHECK(cond, msg) do { \
    if (cond) s_pass++; \
    else { s_fail++; printf("FAIL: %s (line %d)\n", msg, __LINE__); } \
} while (0)

static void test_uri_split(void) {
    printf("-- bus_uri_host_port split --\n");
    char h[64]; uint16_t p;

    /* the compiled mqtt floor shape */
    bus_uri_host_port("mqtt://mqtt-broker.local:1883", h, sizeof(h), &p, 1);
    CHECK(strcmp(h, "mqtt-broker.local") == 0, "mqtt scheme host");
    CHECK(p == 1883, "mqtt scheme port");

    /* http store URL (the floor is DERIVED from this host) */
    bus_uri_host_port("http://mqtt-broker.local:8420", h, sizeof(h), &p, 1);
    CHECK(strcmp(h, "mqtt-broker.local") == 0, "http host");
    CHECK(p == 8420, "http port");

    /* no scheme, host:port */
    bus_uri_host_port("broker.example:1884", h, sizeof(h), &p, 1);
    CHECK(strcmp(h, "broker.example") == 0, "no-scheme host");
    CHECK(p == 1884, "no-scheme port");

    /* bare host -> default port (NEGATIVE control: no ':' must NOT invent one) */
    bus_uri_host_port("mqtt-broker.local", h, sizeof(h), &p, 1883);
    CHECK(strcmp(h, "mqtt-broker.local") == 0, "bare host");
    CHECK(p == 1883, "bare host -> default port");

    /* a path after host must not be mistaken for a port */
    bus_uri_host_port("mqtt://192.0.2.9/foo:5", h, sizeof(h), &p, 1883);
    CHECK(strcmp(h, "192.0.2.9") == 0, "path host");
    CHECK(p == 1883, "path colon is NOT a port");

    /* empty / null -> host cleared, default port (no crash) */
    bus_uri_host_port(nullptr, h, sizeof(h), &p, 1883);
    CHECK(h[0] == '\0', "null uri clears host");
    CHECK(p == 1883, "null uri -> default port");
}

/* Mirror the glue's try_confirm gate: store_required is TRUE under config:http
 * (this shell always fetches over HTTP), so a trial confirms only with BOTH the
 * store plane AND the broker echo plane. */
static bool would_commit(bool store_required, bool store_ok, bool echo_ok) {
    BindConfirmGate g = { store_required, store_ok, echo_ok };
    return binding_confirm_ready(&g);
}

static void test_commit_positive_control(void) {
    printf("-- POSITIVE CONTROL: a real/echoing broker COMMITS --\n");

    /* ⛔ THE POSITIVE CONTROL.  config:http => store_required=true.  A reachable
     * store (store_ok) AND our own confirm seq fanned back by a LIVE broker
     * (echo_ok) => the trial COMMITS.  Without this assertion the suite could
     * pass while only ever proving reverts. */
    CHECK(would_commit(true, true, true) == true,
          "http trial COMMITS with store + broker echo (positive control)");

    /* Negatives: each plane alone must NOT commit (would revert on timeout). */
    CHECK(would_commit(true, true, false) == false,
          "store ok but NO broker echo -> does not commit");
    CHECK(would_commit(true, false, true) == false,
          "broker echo but store unreachable -> does not commit");
    CHECK(would_commit(true, false, false) == false,
          "neither plane -> does not commit");

    /* config:bus/cached (store vacuous): the broker echo ALONE commits, and its
     * absence still does not — the second positive control + its negative. */
    CHECK(would_commit(false, false, true) == true,
          "broker-only trial COMMITS on echo alone (config:bus positive)");
    CHECK(would_commit(false, false, false) == false,
          "broker-only trial without echo -> does not commit");

    /* The derived revert-reason names the OUTSTANDING plane (diagnosable). */
    CHECK(strcmp(binding_default_reason(true, false, true), "store-unreachable") == 0,
          "reason: store outstanding -> store-unreachable");
    CHECK(strcmp(binding_default_reason(true, true, false), "broker-no-echo") == 0,
          "reason: echo outstanding -> broker-no-echo");
}

int main(void) {
    printf("bus_uri + confirm-gate host test ([SPEC:CXC-REPOINT])\n");
    test_uri_split();
    test_commit_positive_control();
    int total = s_pass + s_fail;
    printf("%d/%d checks pass\n", s_pass, total);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    printf("%d FAILED\n", s_fail);
    return 1;
}

#endif /* BUS_URI_SELFTEST */
