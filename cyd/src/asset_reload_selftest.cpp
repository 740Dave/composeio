/*
 * asset_reload_selftest.cpp — host unit test for the asset-change-notify
 * consumer ([SPEC:CXC-DEVICE-BASELINE] cap 1) and the {old,new} change-event
 * parser.  Built by [env:native_asset_reload]; PURE C++, no ESP-IDF.
 *
 * ⛔ NEGATIVE CONTROLS ([CXC-VISION-EPISTEMICS] rule-negative-control): an
 * instrument that can only report SUCCESS is not an instrument.  Every "reload"
 * assertion is paired with a "must NOT reload" case (a foreign asset changed; a
 * retained replay where new==held; a malformed event; the wrong namespace; a
 * non-String arrival) that would FAIL if the self-select logic were too eager.
 * The reboot seam is a SPY that counts calls, so "did not reboot" is a checked
 * fact, not an unobserved absence.
 */

#include "asset_reload.h"
#include "asset_reload_bus.h"

#include <cstdio>
#include <cstring>

static int g_fail = 0;
#define CHECK(cond) do { \
    if (!(cond)) { printf("  FAIL: %s  (line %d)\n", #cond, __LINE__); g_fail++; } \
} while (0)

/* The reboot SPY — asset_reload_bus_consume/backstop invoke this on RELOAD.
 * A negative control asserts it stayed 0. */
static int g_reboot_calls = 0;
static char g_last_reason[32] = "";
static void spy_reboot(void* /*user*/, const char* reason) {
    g_reboot_calls++;
    snprintf(g_last_reason, sizeof(g_last_reason), "%s", reason ? reason : "");
}

static const char* H0 = "0000000000000000000000000000000000000000000000000000000000000000";
static const char* H1 = "1111111111111111111111111111111111111111111111111111111111111111";
static const char* H2 = "2222222222222222222222222222222222222222222222222222222222222222";

static void test_parse_change() {
    printf("test: asset_reload_parse_change\n");
    char oldb[65], newb[65];

    /* normal transition */
    CHECK(asset_reload_parse_change("{\"old\":\"aaa\",\"new\":\"bbb\"}",
                                    oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(strcmp(oldb, "aaa") == 0);
    CHECK(strcmp(newb, "bbb") == 0);

    /* create: old is null -> empty, new present -> actionable */
    CHECK(asset_reload_parse_change("{\"old\":null,\"new\":\"bbb\"}",
                                    oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(oldb[0] == '\0');
    CHECK(strcmp(newb, "bbb") == 0);

    /* NEGATIVE: missing new -> returns false, both buffers empty (no reload) */
    CHECK(!asset_reload_parse_change("{\"old\":\"aaa\"}",
                                     oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(newb[0] == '\0');

    /* NEGATIVE: new is null (not a string) -> false */
    CHECK(!asset_reload_parse_change("{\"old\":\"aaa\",\"new\":null}",
                                     oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(newb[0] == '\0');

    /* NEGATIVE: null json */
    CHECK(!asset_reload_parse_change(nullptr, oldb, sizeof(oldb),
                                     newb, sizeof(newb)));

    /* order independence: new before old */
    CHECK(asset_reload_parse_change("{\"new\":\"bbb\",\"old\":\"aaa\"}",
                                    oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(strcmp(oldb, "aaa") == 0 && strcmp(newb, "bbb") == 0);

    /* a value literal that CONTAINS the token "new" must not spoof the key */
    CHECK(asset_reload_parse_change("{\"old\":\"new\",\"new\":\"real\"}",
                                    oldb, sizeof(oldb), newb, sizeof(newb)));
    CHECK(strcmp(oldb, "new") == 0 && strcmp(newb, "real") == 0);
}

static void test_decide() {
    printf("test: asset_reload_decide (all four branches)\n");
    /* nothing held -> cannot self-select */
    CHECK(asset_reload_decide("", H1, "") == AR_IGNORE_NO_HELD);
    /* new == held -> already latest (absorbs retained replay) */
    CHECK(asset_reload_decide(H0, H1, H1) == AR_IGNORE_ALREADY_LATEST);
    /* old == held -> mine, reload */
    CHECK(asset_reload_decide(H1, H2, H1) == AR_RELOAD);
    /* neither matches -> not mine (backstop covers a skipped multi-PUT) */
    CHECK(asset_reload_decide(H1, H2, H0) == AR_IGNORE_NOT_MINE);
}

static void test_backstop_pure() {
    printf("test: asset_backstop_should_reload / _due\n");
    CHECK(asset_backstop_should_reload(H2, H1) == true);   /* advanced */
    CHECK(asset_backstop_should_reload(H1, H1) == false);  /* unchanged */
    CHECK(asset_backstop_should_reload("", H1) == false);  /* fetch failed */
    CHECK(asset_backstop_should_reload(H2, "") == false);  /* nothing held */
    CHECK(asset_backstop_due(1000, 1000) == true);
    CHECK(asset_backstop_due(999, 1000) == false);
    /* wraparound: now just past a due near UINT32_MAX */
    CHECK(asset_backstop_due(5, 0xFFFFFFF0u) == true);
}

static void test_bus_consume() {
    printf("test: asset_reload_bus_consume (with reboot SPY + negative controls)\n");
    const char* ns = asset_reload_bus_namespace();
    const int kString = 2, kNumber = 0;

    /* held = H1 */
    asset_reload_bus_init();
    CHECK(asset_reload_bus_note_held(H1) == true);
    CHECK(strcmp(asset_reload_bus_held(), H1) == 0);

    char evt[160];

    /* NEGATIVE CONTROL 1: wrong namespace -> NOT consumed, no reboot */
    g_reboot_calls = 0;
    CHECK(asset_reload_bus_consume("some/other/ns", kString, "{\"old\":\"x\",\"new\":\"y\"}",
                                   spy_reboot, nullptr) == false);
    CHECK(g_reboot_calls == 0);

    /* RELOAD: old==held -> consumed AND reboot fires exactly once */
    g_reboot_calls = 0;
    snprintf(evt, sizeof(evt), "{\"old\":\"%s\",\"new\":\"%s\"}", H1, H2);
    CHECK(asset_reload_bus_consume(ns, kString, evt, spy_reboot, nullptr) == true);
    CHECK(g_reboot_calls == 1);
    CHECK(strcmp(g_last_reason, "asset-change") == 0);

    /* NEGATIVE CONTROL 2: new==held (retained replay) -> consumed, NO reboot */
    g_reboot_calls = 0;
    snprintf(evt, sizeof(evt), "{\"old\":\"%s\",\"new\":\"%s\"}", H0, H1);
    CHECK(asset_reload_bus_consume(ns, kString, evt, spy_reboot, nullptr) == true);
    CHECK(g_reboot_calls == 0);

    /* NEGATIVE CONTROL 3: foreign change (neither old nor new is held) ->
     * consumed, NO reboot (the backstop, not the event, covers a skipped PUT) */
    g_reboot_calls = 0;
    snprintf(evt, sizeof(evt), "{\"old\":\"%s\",\"new\":\"%s\"}", H2, H0);
    CHECK(asset_reload_bus_consume(ns, kString, evt, spy_reboot, nullptr) == true);
    CHECK(g_reboot_calls == 0);

    /* NEGATIVE CONTROL 4: our ns but a NON-String arrival (malformed) ->
     * consumed (ours to eat) but NO reboot */
    g_reboot_calls = 0;
    CHECK(asset_reload_bus_consume(ns, kNumber, "42", spy_reboot, nullptr) == true);
    CHECK(g_reboot_calls == 0);

    /* NEGATIVE CONTROL 5: nothing held (server served no hash) -> a real
     * transition event is ambiguous and must NOT reboot */
    asset_reload_bus_init();
    CHECK(asset_reload_bus_note_held("") == false);   /* empty -> inert */
    g_reboot_calls = 0;
    snprintf(evt, sizeof(evt), "{\"old\":\"%s\",\"new\":\"%s\"}", H1, H2);
    CHECK(asset_reload_bus_consume(ns, kString, evt, spy_reboot, nullptr) == true);
    CHECK(g_reboot_calls == 0);
}

static void test_bus_backstop() {
    printf("test: asset_reload_bus_backstop (with reboot SPY + negative controls)\n");
    asset_reload_bus_init();
    asset_reload_bus_note_held(H1);

    /* advanced -> reboot */
    g_reboot_calls = 0;
    asset_reload_bus_backstop("timer", H2, spy_reboot, nullptr);
    CHECK(g_reboot_calls == 1);
    CHECK(strcmp(g_last_reason, "asset-backstop") == 0);

    /* NEGATIVE: unchanged -> no reboot */
    g_reboot_calls = 0;
    asset_reload_bus_backstop("timer", H1, spy_reboot, nullptr);
    CHECK(g_reboot_calls == 0);

    /* NEGATIVE: empty fetched hash (fetch failed / server serves none) -> no reboot */
    g_reboot_calls = 0;
    asset_reload_bus_backstop("timer", "", spy_reboot, nullptr);
    CHECK(g_reboot_calls == 0);
}

int main() {
    printf("=== asset_reload_selftest [CXC-DEVICE-BASELINE cap 1] ===\n");
    test_parse_change();
    test_decide();
    test_backstop_pure();
    test_bus_consume();
    test_bus_backstop();
    if (g_fail) { printf("FAILED: %d check(s)\n", g_fail); return 1; }
    printf("PASS: all asset-reload checks (incl. negative controls)\n");
    return 0;
}
