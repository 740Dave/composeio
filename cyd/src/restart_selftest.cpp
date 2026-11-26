/*
 * restart_selftest.cpp — host unit test for the remote-restart handler.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-REMOTE-RESTART] [CXC-ER-RESTART-BUS]
 * Compliance rows CXC-ER-C15 / C16 / C18 / C19.
 *
 * Pure host test (no hardware, no ESP-IDF) — same guarded-file idiom as
 * dsl_selftest.cpp: the whole body is behind RESTART_CORPUS_TEST, defined
 * only by [env:native_test].  In the flash envs this is an empty TU.
 *
 *   pio run -e native_test && .pio/build/native_test/program
 * Must print "ALL PASS" and exit 0.
 *
 * The two platform actions are injected seams (restart.h) — the reboot
 * seam here RECORDS the call and returns (real hardware never returns),
 * which lets us assert esp_restart is invoked on a truthy command and NOT
 * on a falsy/empty one, plus the boot-guard, without resetting anything.
 */

#ifdef RESTART_CORPUS_TEST

#include "restart.h"

#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Seam recorders                                                      */
/* ------------------------------------------------------------------ */

static int s_reboot_calls = 0;
static int s_publish_calls = 0;
static char s_last_pub_ns[96] = {0};
static char s_last_pub_val[64] = {0};

static void mock_publish(const char* ns, const char* value) {
    s_publish_calls++;
    snprintf(s_last_pub_ns, sizeof(s_last_pub_ns), "%s", ns ? ns : "");
    snprintf(s_last_pub_val, sizeof(s_last_pub_val), "%s", value ? value : "");
}
static void mock_reboot(void) { s_reboot_calls++; }

static void seams_clear(void) {
    s_reboot_calls = 0;
    s_publish_calls = 0;
    s_last_pub_ns[0] = s_last_pub_val[0] = '\0';
}

/* now_ms comfortably past the 3000 ms boot-guard window. */
static const uint32_t kPastGuard = 10000;
static const char* kDevId = "qualia-004df8";

/* Fresh handler for each case. */
static void arm(void) {
    restart_reset();
    seams_clear();
    restart_init(kDevId, mock_publish, mock_reboot, 3000);
}

/* ------------------------------------------------------------------ */
/* Test harness                                                        */
/* ------------------------------------------------------------------ */

static int s_pass = 0, s_fail = 0;
#define CHECK(cond, msg)                                                 \
    do {                                                                 \
        if (cond) { s_pass++; }                                          \
        else { s_fail++; printf("  FAIL: %s\n", msg); }                  \
    } while (0)

/* BusEvent::Kind mirror: 0=Number, 1=Bool, 2=String. */
static const int NUM = 0, BOOL = 1, STR = 2;

int main(void) {
    printf("restart handler host test "
           "([CXC-ER-REMOTE-RESTART]/[CXC-ER-RESTART-BUS])\n");

    /* --- C19/subscription: BOTH exact namespaces, from the config id --- */
    arm();
    {
        const char* ns[4] = {nullptr, nullptr, nullptr, nullptr};
        size_t n = restart_namespaces(ns, 4);
        CHECK(n == 2, "restart_namespaces returns 2");
        CHECK(n == 2 && strcmp(ns[0], "_device/qualia-004df8/restart") == 0,
              "per-device namespace = _device/<id>/restart");
        CHECK(n == 2 && strcmp(ns[1], "_device/restart") == 0,
              "broadcast namespace = _device/restart");
        CHECK(restart_is_restart_ns("_device/qualia-004df8/restart"),
              "per-device ns recognized");
        CHECK(restart_is_restart_ns("_device/restart"),
              "broadcast ns recognized");
        CHECK(!restart_is_restart_ns("_device/cyd-abc/restart"),
              "a DIFFERENT device's restart ns is NOT ours");
        CHECK(!restart_is_restart_ns("demo/gauge"),
              "an application ns is not a restart ns");
    }

    /* --- C15/C16: truthy forms restart + ack, past the boot guard ---- */
    struct TruthyCase { const char* label; int kind; double num; bool b;
                        const char* str; const char* ns; };
    const char* PER = "_device/qualia-004df8/restart";
    const char* BC = "_device/restart";
    TruthyCase truthy[] = {
        {"bool true",            BOOL, 0, true,  nullptr,             PER},
        {"number 1",            NUM,  1, false, nullptr,             PER},
        {"string \"restart\"",  STR,  0, false, "restart",           PER},
        {"object {\"restart\": true}", STR, 0, false, "{\"restart\": true}", PER},
        {"object spaced",       STR,  0, false, "{ \"restart\" : true }", PER},
        {"broadcast + true",    BOOL, 0, true,  nullptr,             BC},
        {"number 1 (string)",   STR,  0, false, "1",                 PER},
    };
    for (auto& c : truthy) {
        arm();
        bool consumed = restart_on_bus_value(c.ns, c.kind, c.num, c.b,
                                              c.str, kPastGuard);
        CHECK(consumed, "truthy: arrival consumed (restart ns)");
        char m[128];
        snprintf(m, sizeof(m), "truthy '%s' -> reboot invoked once", c.label);
        CHECK(s_reboot_calls == 1, m);
        snprintf(m, sizeof(m), "truthy '%s' -> ack published", c.label);
        CHECK(s_publish_calls == 1 &&
              strcmp(s_last_pub_ns, "_device/qualia-004df8/status") == 0 &&
              strcmp(s_last_pub_val, "restarting") == 0, m);
    }

    /* --- C18: falsy / empty forms are a NO-OP (no reboot, no ack) ---- */
    TruthyCase falsy[] = {
        {"bool false",          BOOL, 0, false, nullptr,             PER},
        {"number 0",            NUM,  0, false, nullptr,             PER},
        {"empty string",        STR,  0, false, "",                  PER},
        {"empty object {}",     STR,  0, false, "{}",                PER},
        {"object restart:false",STR,  0, false, "{\"restart\": false}", PER},
        {"object no restart key",STR, 0, false, "{\"other\": true}", PER},
        {"string \"off\"",      STR,  0, false, "off",               PER},
    };
    for (auto& c : falsy) {
        arm();
        bool consumed = restart_on_bus_value(c.ns, c.kind, c.num, c.b,
                                              c.str, kPastGuard);
        CHECK(consumed, "falsy: arrival still consumed (restart ns)");
        char m[128];
        snprintf(m, sizeof(m), "falsy '%s' -> NO reboot", c.label);
        CHECK(s_reboot_calls == 0, m);
        snprintf(m, sizeof(m), "falsy '%s' -> NO ack", c.label);
        CHECK(s_publish_calls == 0, m);
    }

    /* --- C18: boot-loop guard — a command inside the window is ignored - */
    arm();
    {
        bool consumed = restart_on_bus_value(PER, BOOL, 0, true, nullptr,
                                             /*now_ms=*/1500);
        CHECK(consumed, "boot-guard: arrival consumed");
        CHECK(s_reboot_calls == 0, "command within boot window -> NO reboot");
        CHECK(s_publish_calls == 0, "command within boot window -> NO ack");
        /* the SAME command past the window now restarts */
        restart_on_bus_value(PER, BOOL, 0, true, nullptr, kPastGuard);
        CHECK(s_reboot_calls == 1, "same command past boot window -> reboot");
    }

    /* --- C18 precedence: doubled command restarts exactly ONCE -------- */
    arm();
    {
        restart_on_bus_value(BC, BOOL, 0, true, nullptr, kPastGuard);
        restart_on_bus_value(PER, BOOL, 0, true, nullptr, kPastGuard);
        CHECK(s_reboot_calls == 1,
              "broadcast + targeted (both truthy) -> restart once");
    }

    /* --- a non-restart arrival is NOT consumed and never reboots ------ */
    arm();
    {
        bool consumed = restart_on_bus_value("demo/gauge", NUM, 72, false,
                                             nullptr, kPastGuard);
        CHECK(!consumed, "application arrival NOT consumed (routes to scene)");
        CHECK(s_reboot_calls == 0, "application arrival -> NO reboot");
    }

    /* --- payload-truthiness unit rows (dsl_truthy canon) ------------- */
    CHECK(restart_payload_truthy(BOOL, 0, true, nullptr), "truthy: bool true");
    CHECK(!restart_payload_truthy(BOOL, 0, false, nullptr), "falsy: bool false");
    CHECK(restart_payload_truthy(NUM, 1, false, nullptr), "truthy: number 1");
    CHECK(!restart_payload_truthy(NUM, 0, false, nullptr), "falsy: number 0");
    CHECK(restart_payload_truthy(STR, 0, false, "restart"), "truthy: \"restart\"");
    CHECK(!restart_payload_truthy(STR, 0, false, ""), "falsy: empty string");
    CHECK(!restart_payload_truthy(STR, 0, false, "{}"), "falsy: {}");
    CHECK(restart_payload_truthy(STR, 0, false, "{\"restart\": true}"),
          "truthy: {\"restart\": true}");
    CHECK(!restart_payload_truthy(STR, 0, false, "{\"restart\": 0}"),
          "falsy: {\"restart\": 0}");

    int total = s_pass + s_fail;
    printf("%d/%d checks pass\n", s_pass, total);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    printf("%d FAILED\n", s_fail);
    return 1;
}

#endif /* RESTART_CORPUS_TEST */
