/*
 * bus_mailbox_selftest.cpp — host harness for the per-namespace coalescing
 * mailbox (bus_mailbox.h) that replaced the 16-deep inbound FIFO on both the
 * MQTT and UART receive paths.  [SPEC:CXC-EMBED-REACTIVE] DP-CIO-6.
 *
 *   pio run -e native_mailbox && .pio/build/native_mailbox/program
 * Must print "ALL PASS" and exit 0.
 *
 * ⛔ WHAT THIS PROVES, and what it deliberately cannot.  It exercises the
 * MAILBOX LOGIC directly — coalescing, the retained-flood survival that was
 * the whole point, and the drop counter's boundary — and it hammers post()
 * against poll() from two real threads under the SAME std::mutex discipline
 * the firmware uses, checking every drained event is internally consistent
 * (no torn slot).  It does NOT run FreeRTOS: the on-target sync is a
 * priority-inheriting FreeRTOS mutex, and this host proxy proves the LOGIC is
 * corruption-free when the critical section is honoured, not that the target
 * mutex is wired correctly (that is a code-review fact, checked by reading
 * mailbox_post / poll in bus_mqtt.cpp and bus_uart.cpp).
 *
 * ⛔ EVERY RULE IS ASSERTED IN BOTH DIRECTIONS where an instrument that can
 * only report success would otherwise pass.  The drop counter is checked to
 * STAY ZERO below capacity AND to move above it; coalescing is checked to
 * collapse duplicates AND to preserve distinct namespaces.
 */

#ifdef MAILBOX_CORPUS_TEST

#include "bus_mailbox.h"

#include <atomic>
#include <cstdio>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

/* Build an event whose fields are a deterministic function of a key, so a
 * drained event can be checked for INTERNAL CONSISTENCY (ns, num and str must
 * all agree) — the tell of a torn slot. */
static BusEvent mk(int key, double value) {
    BusEvent ev = {};
    snprintf(ev.ns, sizeof(ev.ns), "n%04d", key);
    ev.kind = BusEvent::Kind::Number;
    ev.num = value;
    snprintf(ev.str, sizeof(ev.str), "n%04d", key);   /* mirrors ns */
    ev.type = BusMessageType::State;
    return ev;
}

/* ------------------------------------------------------------------ */
/* (a) two updates to the SAME namespace coalesce to the LATEST         */
/* ------------------------------------------------------------------ */
static void test_coalesce() {
    BusMailbox<8> m;
    CHECK(m.post(mk(1, 10.0)), "coalesce: first post stored");
    CHECK(m.post(mk(1, 20.0)), "coalesce: second post stored");
    CHECK(m.post(mk(1, 30.0)), "coalesce: third post stored");
    /* Three posts to one ns must leave exactly ONE pending slot... */
    CHECK(m.pending() == 1, "coalesce: 3 same-ns posts -> 1 pending (got %zu)",
          m.pending());
    BusEvent out = {};
    CHECK(m.poll(&out), "coalesce: one slot drains");
    /* ...carrying the LATEST value, not the first or a blend. */
    CHECK(out.num == 30.0, "coalesce: latest value wins (got %g)", out.num);
    CHECK(!m.poll(&out), "coalesce: nothing more pending");
    CHECK(m.dropped() == 0, "coalesce: no drops (got %u)", m.dropped());
}

/* ------------------------------------------------------------------ */
/* (b) the RETAINED FLOOD: N distinct namespaces, N far past the old    */
/*     16-deep FIFO, all arriving before a single drain — all survive.  */
/* ------------------------------------------------------------------ */
static void test_retained_flood() {
    /* CAP mirrors the MQTT transport (kMaxSubs 16 + kMaxPersist 4 + 4). */
    static BusMailbox<24> m;
    const int N = 24;                 /* full width — every subscribed ns */
    for (int i = 0; i < N; i++)
        CHECK(m.post(mk(i, i * 1.5)), "flood: ns %d stored", i);
    CHECK(m.pending() == (size_t)N, "flood: all %d survive the burst "
          "(got %zu) — this is the bug the FIFO had", N, m.pending());
    CHECK(m.dropped() == 0, "flood: nothing dropped at capacity (got %u)",
          m.dropped());
    /* Drain and confirm EVERY namespace came back exactly once with its own
     * value — no slot aliased another. */
    bool seen[24] = {};
    BusEvent out = {};
    int drained = 0;
    while (m.poll(&out)) {
        int k = atoi(out.ns + 1);
        CHECK(k >= 0 && k < N && !seen[k], "flood: %s drained once", out.ns);
        CHECK(out.num == k * 1.5, "flood: %s value intact (got %g)", out.ns,
              out.num);
        CHECK(strcmp(out.ns, out.str) == 0, "flood: %s slot not torn", out.ns);
        seen[k] = true;
        drained++;
    }
    CHECK(drained == N, "flood: drained all %d (got %d)", N, drained);
}

/* ------------------------------------------------------------------ */
/* (c) the drop counter moves ONLY on genuine structure-full, and it    */
/*     stays put right up to the boundary (both directions).            */
/* ------------------------------------------------------------------ */
static void test_drop_boundary() {
    BusMailbox<4> m;
    /* Fill every slot with a DISTINCT namespace — no coalescing possible. */
    for (int i = 0; i < 4; i++) CHECK(m.post(mk(i, i)), "boundary: fill %d", i);
    CHECK(m.dropped() == 0, "boundary: full but no drop yet (got %u)",
          m.dropped());
    /* A fifth DISTINCT namespace has nowhere to go — a genuine overflow. */
    CHECK(!m.post(mk(99, 99)), "boundary: 5th distinct ns refused");
    CHECK(m.dropped() == 1, "boundary: exactly one drop (got %u)", m.dropped());
    /* But a repeat of an ALREADY-pending ns still coalesces — a full mailbox
     * is not a wall to updates it can absorb. */
    CHECK(m.post(mk(0, 123)), "boundary: coalesce into full mailbox");
    CHECK(m.dropped() == 1, "boundary: coalesce did not count as a drop "
          "(got %u)", m.dropped());
    /* Drain one, and the freed slot lets a new ns in again — no drop. */
    BusEvent out = {};
    CHECK(m.poll(&out), "boundary: drain one");
    CHECK(m.post(mk(50, 50)), "boundary: freed slot accepts a new ns");
    CHECK(m.dropped() == 1, "boundary: still one drop total (got %u)",
          m.dropped());
}

/* ------------------------------------------------------------------ */
/* (d) CROSS-TASK: a writer and a reader on separate threads, under the  */
/*     mutex discipline the transport uses, never produce a torn slot.   */
/* ------------------------------------------------------------------ */
static void test_concurrent() {
    static BusMailbox<32> m;   /* >= distinct-ns so no drops expected */
    std::mutex lock;
    std::atomic<bool> done{false};
    std::atomic<long> torn{0}, drained{0};
    const int kDistinct = 32;
    const long kWrites = 400000;

    std::thread writer([&] {
        for (long i = 0; i < kWrites; i++) {
            int key = (int)(i % kDistinct);
            BusEvent ev = mk(key, (double)key);   /* value == key, always */
            std::lock_guard<std::mutex> g(lock);
            m.post(ev);
        }
        done = true;
    });

    auto reap = [&](const BusEvent& out) {
        /* The slot must be internally consistent: if ns says key K then num
         * must be K and str must match ns.  Anything else is a torn read. */
        int k = atoi(out.ns + 1);
        if (out.num != (double)k || strcmp(out.ns, out.str) != 0) torn++;
        drained++;
    };

    std::thread reader([&] {
        BusEvent out = {};
        while (!done.load()) {
            std::lock_guard<std::mutex> g(lock);
            while (m.poll(&out)) reap(out);
        }
        /* final sweep */
        std::lock_guard<std::mutex> g(lock);
        while (m.poll(&out)) reap(out);
    });

    writer.join();
    reader.join();

    CHECK(torn.load() == 0, "concurrent: no torn slots across %ld writes "
          "(saw %ld)", kWrites, torn.load());
    CHECK(drained.load() > 0, "concurrent: reader actually drained events "
          "(%ld) — the test is not vacuous", drained.load());
    CHECK(m.dropped() == 0, "concurrent: no drops with CAP >= distinct ns "
          "(got %u)", m.dropped());
    CHECK(m.pending() == 0, "concurrent: mailbox empty at end (got %zu)",
          m.pending());
}

int main() {
    printf("bus_mailbox_selftest — coalescing inbound mailbox\n");
    test_coalesce();
    test_retained_flood();
    test_drop_boundary();
    test_concurrent();
    printf("\n%d passed, %d failed\n", s_pass, s_fail);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    return 1;
}

#endif /* MAILBOX_CORPUS_TEST */
