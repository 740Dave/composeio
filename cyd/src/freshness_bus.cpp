/*
 * freshness_bus.cpp — the freshness receiver's SEAM ONTO THE BUS.
 * [SPEC:CXC-FRESHNESS] [CXC-FRESH-CPP-FOLLOWON] [CXC-FRESH-HEARTBEAT-RX]
 *
 * freshness.cpp holds the JUDGEMENT and depends on nothing.  This file holds
 * the three things it takes to reach a real bus on a real board — the arrival
 * stamp, the heartbeat sieve entry, and the tick that publishes a transition
 * — and it is the ONLY file in the receiver that includes ESP-IDF.
 *
 * ⛔ ZERO CONSUMER CODE, WHICH IS THE WHOLE POINT.  David's ruling: "we
 * shouldn't have to program for that, it should just happen because a new
 * state has been emitted."  So the tick does not call a widget, does not call
 * a draw path, and does not call a gauge.  It hands a namespace and a value to
 * the app shell's sink, which passes it to the IDENTICAL entry point a real
 * arrival uses — scene_on_bus_value() on the display tier,
 * GaugeBindings::dispatch() on the actuator tier.  There is no parallel
 * delivery path, so every existing declarative binding reacts to a verdict
 * exactly as it reacts to any other state, and no consumer contains the word
 * "stale".
 */

#include "freshness.h"
#include "announce_pack.h"
#include "bus_transport.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "esp_log.h"
#include "esp_timer.h"

static const char* TAG = "freshness";

/* THE SECOND COPY, NAMED.  [CXC-FRESH-HEARTBEAT-RX] says the cadence must
 * ride ON the beat so the receiver never holds a configured copy — "the second
 * copy being the thing that eventually disagrees".  The Python form
 * (`_bus/heartbeat`, a JSON object) obeys that and carries `interval_s`.  The
 * C++ fleet form (`_device/<id>/heartbeat`, announce.cpp:368) carries UPTIME
 * SECONDS and nothing else, so a receiver has no declared cadence to read and
 * must fall back.
 *
 * This IS that second copy.  It is pinned to the publisher's own constant so
 * the two cannot drift WITHIN this repo, and the residual risk — a peer that
 * beats at some other rate and does not say so — is a real gap this tier
 * cannot see and is reported as one, not hidden behind a plausible default. */
static constexpr uint32_t kFreshDefaultBeatMs = kAnnHeartbeatIntervalMs;
static_assert(kFreshDefaultBeatMs == 5000,
              "the heartbeat fallback cadence and announce.cpp's publish "
              "cadence have drifted — one of them is now lying about the "
              "other, which is exactly what a second copy does");

/* THE RECEIVER'S OWN STANDING TO JUDGE — wired to the REAL instrument.
 * [CXC-FRESH-ASYMMETRY] is about not exporting a verdict; this is the other
 * half, about not IMPORTING confidence the link cannot support.
 *
 * `link_health()` reads hardware on CAN (twai_get_status_info's TEC/REC), and
 * on UART it honestly answers kAnnLinkUnknown because a port being open says
 * nothing about the far end.  freshness_link_impaired() is the one place that
 * knows Unknown must mean "no opinion, PROCEED" rather than "impaired" — see
 * its comment; getting that backwards silences every UART leaf in the fleet.
 *
 * ⚠ It is NOT `connected()`.  `connected()` returns `s_started` on CAN, so a
 * BUS-OFF board reports connected forever (defect H2b, recorded and
 * deliberately unfixed).  Gating on it would gate on the exact instrument that
 * cannot fail. */
static const uint8_t kAnnLinkDownLiteral = 4;
static const uint8_t kAnnLinkStoppedLiteral = 5;
static const uint8_t kAnnLinkRecoveringLiteral = 6;
static_assert(kAnnLinkDown == kAnnLinkDownLiteral &&
              kAnnLinkStopped == kAnnLinkStoppedLiteral &&
              kAnnLinkRecovering == kAnnLinkRecoveringLiteral,
              "freshness_link_impaired()'s literals and announce_pack.h's "
              "kAnnLink* have drifted — the freshness seam deliberately does "
              "not include the announcement encoding, so this assert is the "
              "only thing holding the two in step");
static_assert(kAnnLinkUnknown == 0 && kAnnLinkOk == 1,
              "Unknown/Ok moved; freshness_link_impaired() must keep treating "
              "Unknown as NO OPINION or every UART leaf stops judging");

static bool probe_link_impaired(void*) {
    return freshness_link_impaired(bus_transport().link_health());
}

void freshness_attach_link_probe(void) {
    freshness().set_impaired_probe(probe_link_impaired, nullptr);
}

/* ------------------------------------------------------------------ */
/* Arrivals                                                            */
/* ------------------------------------------------------------------ */

void freshness_note_arrival(const char* ns, uint32_t now_ms) {
    freshness().on_arrival(ns, now_ms);
}

void freshness_note_wire_unknown(const char* ns, uint32_t now_ms) {
    freshness().on_wire_unknown(ns, now_ms);
    /* Rate-limited on PURPOSE.  A publisher stuck emitting 0xFD at its full
     * cadence would otherwise produce one log line per arrival forever — the
     * "diagnostic that causes the failure it reports" shape this codebase has
     * already paid for once (25 ESP_LOGW in 60 ms delayed the very ring
     * draining being reported). */
    static int64_t last_us = 0;
    static uint32_t suppressed = 0;
    int64_t now = esp_timer_get_time();
    if (!last_us || now - last_us > 5000000LL) {
        ESP_LOGI(TAG, "%s: publisher sent `unknown` (0xFD) — alive and "
                 "explicitly without a value (%lu further suppressed)",
                 ns, (unsigned long)suppressed);
        last_us = now;
        suppressed = 0;
    } else {
        suppressed++;
    }
}

/* ------------------------------------------------------------------ */
/* The heartbeat sieve                                                 */
/* ------------------------------------------------------------------ */

/* Extract `<id>` from `_device/<id>/heartbeat`.  Returns false for anything
 * else — including `_device/<id>/health`, which is NOT a liveness signal. */
static bool node_from_device_ns(const char* ns, char* out, size_t cap) {
    static const char kPrefix[] = "_device/";
    static const char kSuffix[] = "/heartbeat";
    size_t n = strlen(ns);
    size_t pre = sizeof(kPrefix) - 1, suf = sizeof(kSuffix) - 1;
    if (n <= pre + suf) return false;
    if (memcmp(ns, kPrefix, pre) != 0) return false;
    if (memcmp(ns + n - suf, kSuffix, suf) != 0) return false;
    size_t m = n - pre - suf;
    if (m >= cap) return false;
    memcpy(out, ns + pre, m);
    out[m] = '\0';
    return true;
}

bool freshness_consume_heartbeat(const char* ns, int kind, double num,
                                 const char* str, uint32_t now_ms) {
    if (!ns) return false;

    /* FORM 1 — the Python tier: `_bus/heartbeat`, a JSON object whose
     * `interval_s` IS the declared cadence.  The beat names its own node
     * because "a beat that does not say whose it is cannot mark a node dead;
     * it is a pulse with no patient". */
    if (strcmp(ns, "_bus/heartbeat") == 0) {
        if (kind != 2 || !str || !str[0]) {
            /* A heartbeat we cannot attribute is not a heartbeat we may act
             * on.  CONSUMED anyway — it is still not scene traffic. */
            ESP_LOGD(TAG, "_bus/heartbeat with no parseable body — ignored");
            return true;
        }
        char node[kFreshnessNodeCap] = "";
        uint32_t interval_ms = 0;
        /* Parsed by the PURE half, which knows BOTH wire forms — JSON on MQTT
         * and the Python dict REPR (single quotes) on a framed substrate.  See
         * freshness_parse_beat(); a JSON-only parser silently finds no node on
         * UART and NODE_SILENT becomes unreachable there. */
        if (!freshness_parse_beat(str, node, sizeof(node), &interval_ms)) {
            ESP_LOGD(TAG, "_bus/heartbeat carries no `node` — a pulse with no "
                          "patient; ignored");
            return true;
        }
        /* interval_ms == 0 here means the beat declared no cadence; the table
         * then keeps whatever it last knew, and only falls back if it never
         * knew one. */
        freshness().note_heartbeat(node, now_ms,
                                   interval_ms ? interval_ms
                                               : kFreshDefaultBeatMs);
        return true;
    }

    /* FORM 2 — the C++ fleet: `_device/<id>/heartbeat`, uptime seconds.
     * PRESENCE is the signal; the value is only useful for telling a fresh
     * boot from a long-running board. */
    char node[kFreshnessNodeCap];
    if (node_from_device_ns(ns, node, sizeof(node))) {
        (void)num;
        freshness().note_heartbeat(node, now_ms, kFreshDefaultBeatMs);
        return true;
    }

    return false;
}

/* ------------------------------------------------------------------ */
/* The tick                                                            */
/* ------------------------------------------------------------------ */

struct SinkCtx {
    FreshnessSink sink;
    void* user;
};

static void emit_one(void* user, const char* ns, bool fresh,
                     bool fresh_changed, const char* verdict_name,
                     const char* culprit) {
    SinkCtx* c = (SinkCtx*)user;
    char buf[kFreshnessNsCap + 16];

    /* `<ns>/_fresh` — the BOOLEAN, carried as the NUMBER 1.0 / 0.0.
     *
     * ⛔ A NUMBER, NOT A BOOL, AND THAT IS LOAD-BEARING.  dsl.h's `ns()`
     * returns 0.0 for an UNSET namespace, and dsl_coerce_input turns a bool
     * into the STRING "on"/"off".  If the verdict were a bool, then
     * `ns("x/_fresh")` would be "on"/"off" where the namespace exists and 0.0
     * where it does not — two different TYPES for the same question, so any
     * arithmetic guard would behave differently on a board where this receiver
     * never ran.  As a number, absent and not-believable are the SAME VALUE,
     * 0.0, and a guard FAILS SAFE BY CONSTRUCTION rather than by discipline. */
    if (fresh_changed) {
        snprintf(buf, sizeof(buf), "%s%s", ns, kFreshSuffix);
        c->sink(c->user, buf, 0 /*Number*/, fresh ? 1.0 : 0.0, false, "");
    }

    /* `<ns>/_verdict` — the six-case string.  An ACTUATOR needs two states;
     * an OPERATOR needs six, because "the publisher died", "the board is
     * gone", "it has not booted yet" and "nobody ever declared this" call for
     * four different responses. */
    snprintf(buf, sizeof(buf), "%s%s", ns, kVerdictSuffix);
    c->sink(c->user, buf, 2 /*String*/, 0.0, false, verdict_name);

    /* `<class>/_culprit` — WHICH member broke a class.  Emitted only for a
     * class (a plain namespace passes ""), because "the nav solution is bad"
     * and "the GPS died" are different sentences and only the second one tells
     * an operator what to go and touch. */
    if (culprit && culprit[0]) {
        snprintf(buf, sizeof(buf), "%s/_culprit", ns);
        c->sink(c->user, buf, 2 /*String*/, 0.0, false, culprit);
        ESP_LOGI(TAG, "%s -> %s (culprit: %s)", ns, verdict_name, culprit);
        return;
    }
    ESP_LOGI(TAG, "%s -> %s", ns, verdict_name);
}

int freshness_tick(uint32_t now_ms, FreshnessSink sink, void* user) {
    if (!sink) return 0;

    /* ⛔ RATE-LIMIT THE WALK, NOT JUST THE EMISSION.  The emission is already
     * edge-triggered, but the WALK is O(entries) and this is called from a
     * render loop that runs at frame rate.  4 Hz bounds the latency of a
     * verdict at a quarter second and the cost at 4 walks a second.
     *
     * ⚠ IT DOES NOT MAKE THE VERDICT APPROXIMATE.  verdict() is exact at any
     * instant it is asked; this only decides how often anyone asks. */
    static uint32_t next_ms = 0;
    if (next_ms && (int32_t)(now_ms - next_ms) < 0) return 0;
    next_ms = now_ms + kFreshnessEvalIntervalMs;

    SinkCtx ctx{sink, user};
    return freshness().emit_transitions(now_ms, emit_one, &ctx);
}
