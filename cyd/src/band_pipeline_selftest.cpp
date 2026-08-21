/*
 * band_pipeline_selftest.cpp — [SPEC:CXC-LINE] host selftest for the
 * pure band-pipeline control core (band_pipeline.h).
 *
 * Binds the pipeline ops to a RECORDING MOCK that models the hardware
 * invariants and asserts, off-hardware:
 *
 *   C2  buffer-reuse safety — no compute ever writes a buffer whose
 *       transfer is in flight (checked on EVERY compute, including a
 *       1000-run randomized stress).
 *   C1  overlap schedule — kick(N-1) precedes compute(N)'s completion
 *       window; buffers alternate; exactly one transfer in flight at
 *       a time; all transfers drained before return.
 *   C3  degradation — kick refusal (at frame start and mid-frame)
 *       falls back to blocking; frame still fully transferred.
 *   C11 timeout — a wait timeout degrades to blocking and the run
 *       completes (no wedge).
 *   C4  mode selection — >=2 scan-out fbs -> FullFB, <2 -> Band;
 *       full-fb REFUSED (downgraded) on an incapable board.
 *   C6/C9  dirty-run coalescing — adjacent bands coalesce, gaps split,
 *       empty mask yields nothing, full mask is one run.
 *       Rect->mask mapping (reactive_band_mask): clamping, band-
 *       boundary crossing, empty damage.
 *
 * Build/run (hardware-free CI foothold):
 *   pio run -e native_band_test && .pio/build/native_band_test/program
 * or standalone:
 *   clang++ -std=c++17 -Icyd/src -Icyd/include \
 *       -o /tmp/band_selftest cyd/src/band_pipeline_selftest.cpp
 * Must print "BAND-PIPELINE RESULT: PASS" and exit 0.
 */

#include "band_pipeline.h"
#include "reactive_apply.h"   /* reactive_band_mask — reuse by reference */

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>

static int g_failures = 0;

#define CHECK(cond, ...)                                                   \
    do {                                                                   \
        if (!(cond)) {                                                     \
            g_failures++;                                                  \
            printf("FAIL %s:%d: ", __FILE__, __LINE__);                    \
            printf(__VA_ARGS__);                                           \
            printf("\n");                                                  \
        }                                                                  \
    } while (0)

/* ------------------------------------------------------------------ */
/* Recording mock                                                      */
/* ------------------------------------------------------------------ */

struct MockDriver {
    /* Behavior knobs */
    bool async_supported = true;
    int fail_kick_at = -1;        /* kick # (0-based) that returns false */
    int timeout_wait_at = -1;     /* wait # (0-based) that returns false */

    /* Hardware model */
    int in_flight_buf = -1;       /* buffer with a transfer in flight    */
    int kicks = 0;
    int waits = 0;

    /* Trace + violation record */
    std::vector<std::string> trace;
    std::vector<int> transferred_bands;   /* order bands hit the panel  */
    int reuse_violations = 0;     /* compute on an in-flight buffer      */
    int kick_while_busy = 0;      /* kick with a transfer in flight      */

    void compute(int band, int buf) {
        if (buf == in_flight_buf) reuse_violations++;
        char b[48];
        snprintf(b, sizeof(b), "compute(%d,%d)", band, buf);
        trace.push_back(b);
    }
    bool kick(int band, int buf) {
        int n = kicks++;
        if (!async_supported) return false;
        if (fail_kick_at >= 0 && n == fail_kick_at) return false;
        if (in_flight_buf != -1) kick_while_busy++;
        in_flight_buf = buf;
        transferred_bands.push_back(band);
        char b[48];
        snprintf(b, sizeof(b), "kick(%d,%d)", band, buf);
        trace.push_back(b);
        return true;
    }
    bool wait() {
        int n = waits++;
        trace.push_back("wait");
        if (timeout_wait_at >= 0 && n == timeout_wait_at) {
            /* Model the ILI9341 driver: on timeout it marks itself
             * broken; the stuck transfer eventually completes or the
             * blocking path drains defensively.  Clear here so the
             * blocking fallback can proceed (mirror of drain-on-entry). */
            in_flight_buf = -1;
            return false;
        }
        in_flight_buf = -1;
        return true;
    }
    void blocking(int band, int buf) {
        /* The real blocking transfer drains any pending DMA first
         * (ILI9341::transfer_band drain-on-entry). */
        in_flight_buf = -1;
        transferred_bands.push_back(band);
        char b[48];
        snprintf(b, sizeof(b), "blocking(%d,%d)", band, buf);
        trace.push_back(b);
    }
};

static BandPipelineOps ops_for(MockDriver& m) {
    BandPipelineOps ops;
    ops.user = &m;
    ops.compute = [](void* u, int band, int buf) {
        static_cast<MockDriver*>(u)->compute(band, buf);
    };
    ops.kick = [](void* u, int band, int buf) {
        return static_cast<MockDriver*>(u)->kick(band, buf);
    };
    ops.wait = [](void* u) { return static_cast<MockDriver*>(u)->wait(); };
    ops.blocking = [](void* u, int band, int buf) {
        static_cast<MockDriver*>(u)->blocking(band, buf);
    };
    return ops;
}

/* Bands 0..n-1 all reached the panel exactly once, in order. */
static void check_all_transferred(const MockDriver& m, int first, int last,
                                  const char* which) {
    CHECK((int)m.transferred_bands.size() == last - first + 1,
          "%s: %d bands transferred, want %d", which,
          (int)m.transferred_bands.size(), last - first + 1);
    for (size_t i = 0; i < m.transferred_bands.size(); i++) {
        CHECK(m.transferred_bands[i] == first + (int)i,
              "%s: transfer order[%zu]=%d want %d", which, i,
              m.transferred_bands[i], first + (int)i);
    }
}

/* ------------------------------------------------------------------ */
/* Tests                                                               */
/* ------------------------------------------------------------------ */

static void test_mode_selection() {
    CHECK(render_mode_select(0) == RenderMode::Band, "0 fbs -> band");
    CHECK(render_mode_select(1) == RenderMode::Band, "1 fb -> band");
    CHECK(render_mode_select(2) == RenderMode::FullFB, "2 fbs -> full-fb");
    CHECK(render_mode_select(3) == RenderMode::FullFB, "3 fbs -> full-fb");

    bool refused = true;
    CHECK(render_mode_validate(RenderMode::Band, 0, &refused) ==
              RenderMode::Band && !refused,
          "band on incapable board is allowed");
    CHECK(render_mode_validate(RenderMode::Band, 2, &refused) ==
              RenderMode::Band && !refused,
          "band on capable board is allowed (band is universal)");
    CHECK(render_mode_validate(RenderMode::FullFB, 2, &refused) ==
              RenderMode::FullFB && !refused,
          "full-fb on capable board is allowed");
    /* [CXC-LINE-MODES] MUST: full-fb refused on an incapable board. */
    CHECK(render_mode_validate(RenderMode::FullFB, 1, &refused) ==
              RenderMode::Band && refused,
          "full-fb REFUSED (downgraded to band) on 1-fb board");
    CHECK(render_mode_validate(RenderMode::FullFB, 0, &refused) ==
              RenderMode::Band && refused,
          "full-fb REFUSED (downgraded to band) on 0-fb board");
}

static void test_run_coalescing() {
    int b0, b1;

    CHECK(!band_run_next(0x0, 0, &b0, &b1), "empty mask -> no run (idle no-op)");

    /* Single band */
    CHECK(band_run_next(1u << 5, 0, &b0, &b1) && b0 == 5 && b1 == 5,
          "single band run");
    CHECK(!band_run_next(1u << 5, 6, &b0, &b1), "no run after it");

    /* Adjacent bands coalesce ([CXC-LINE-DIRTY]) */
    uint32_t m = (1u << 3) | (1u << 4) | (1u << 5) | (1u << 9) | (1u << 10);
    CHECK(band_run_next(m, 0, &b0, &b1) && b0 == 3 && b1 == 5,
          "first run 3..5 (coalesced), got %d..%d", b0, b1);
    CHECK(band_run_next(m, b1 + 1, &b0, &b1) && b0 == 9 && b1 == 10,
          "second run 9..10, got %d..%d", b0, b1);
    CHECK(!band_run_next(m, b1 + 1, &b0, &b1), "no third run");

    /* Full 20-band mask is ONE run (full-damage degradation, C9) */
    uint32_t full = (1u << 20) - 1;
    CHECK(band_run_next(full, 0, &b0, &b1) && b0 == 0 && b1 == 19,
          "full mask is one run 0..19, got %d..%d", b0, b1);
    CHECK(!band_run_next(full, 20, &b0, &b1), "one run only");

    /* Band 31 edge */
    CHECK(band_run_next(0x80000000u, 0, &b0, &b1) && b0 == 31 && b1 == 31,
          "band 31 run");
}

static void test_rect_to_band_mask() {
    /* reactive_band_mask(y, h, band_h=16, screen_h=320) — reuse by
     * reference; assert the [CXC-LINE-DIRTY] clamping contract. */

    /* Empty damage -> nothing */
    CHECK(reactive_band_mask(50, 0, 16, 320) == 0, "h=0 -> empty");
    CHECK(reactive_band_mask(50, -4, 16, 320) == 0, "h<0 -> empty");

    /* Fully inside one band */
    CHECK(reactive_band_mask(0, 16, 16, 320) == 0x1u, "rows 0..15 -> band 0");
    CHECK(reactive_band_mask(16, 16, 16, 320) == 0x2u, "rows 16..31 -> band 1");

    /* Band-boundary crossing marks BOTH bands */
    CHECK(reactive_band_mask(15, 2, 16, 320) == 0x3u,
          "rows 15..16 -> bands 0+1");

    /* OOB clamping: negative y */
    CHECK(reactive_band_mask(-40, 60, 16, 320) == 0x3u,
          "y=-40 h=60 clamps to rows 0..19 -> bands 0+1");
    /* OOB clamping: past the bottom */
    CHECK(reactive_band_mask(310, 100, 16, 320) == (1u << 19),
          "y=310 h=100 clamps to rows 310..319 -> band 19");
    /* Entirely off-panel */
    CHECK(reactive_band_mask(400, 50, 16, 320) == 0, "below panel -> empty");
    CHECK(reactive_band_mask(-100, 50, 16, 320) == 0,
          "entirely above panel -> empty");
    CHECK(reactive_band_mask(-10, 20, 16, 320) == 0x1u,
          "rect straddling the top clamps to band 0");

    /* Fractional coverage rounds outward (last covered scanline) */
    CHECK(reactive_band_mask(15.5f, 1.0f, 16, 320) == 0x3u,
          "fractional rect crossing 15..16 marks both bands");
}

static void test_overlap_happy_path() {
    MockDriver m;
    auto ops = ops_for(m);
    BandPipelineStats st = band_pipeline_run(ops, 0, 4, true);

    CHECK(st.computed == 5, "5 computed, got %d", st.computed);
    CHECK(st.kicked == 5, "5 async kicks, got %d", st.kicked);
    CHECK(st.blocking == 0, "0 blocking, got %d", st.blocking);
    CHECK(st.waits == 5, "5 waits (4 inner + tail), got %d", st.waits);
    CHECK(st.wait_timeouts == 0, "no timeouts");
    CHECK(!st.degraded, "not degraded");
    CHECK(m.reuse_violations == 0, "C2: no buffer written while in flight");
    CHECK(m.kick_while_busy == 0, "kick only with idle queue");
    CHECK(m.in_flight_buf == -1, "no transfer in flight after return");
    check_all_transferred(m, 0, 4, "overlap");

    /* Schedule shape: kick(N-1) precedes compute(N); buffers alternate. */
    std::vector<std::string> want = {
        "compute(0,0)", "kick(0,0)",
        "compute(1,1)", "wait", "kick(1,1)",
        "compute(2,0)", "wait", "kick(2,0)",
        "compute(3,1)", "wait", "kick(3,1)",
        "compute(4,0)", "wait", "kick(4,0)",
        "wait",
    };
    CHECK(m.trace == want, "overlap trace mismatch");
    if (m.trace != want) {
        for (auto& t : m.trace) printf("  trace: %s\n", t.c_str());
    }
}

static void test_single_buffer_path() {
    /* overlap=false == today's render_bands: buffer 0, blocking. */
    MockDriver m;
    auto ops = ops_for(m);
    BandPipelineStats st = band_pipeline_run(ops, 3, 7, false);

    CHECK(st.computed == 5 && st.blocking == 5 && st.kicked == 0 &&
              st.waits == 0,
          "single-buffer: 5 compute/5 blocking/0 kick/0 wait "
          "(got %d/%d/%d/%d)",
          st.computed, st.blocking, st.kicked, st.waits);
    for (auto& t : m.trace) {
        CHECK(t.find(",1)") == std::string::npos,
              "single-buffer must never touch buffer 1: %s", t.c_str());
    }
    check_all_transferred(m, 3, 7, "single-buffer");
}

static void test_kick_refusal_degrade() {
    /* Driver without async (default DisplayDriver): full blocking
     * fallback, frame still complete ([CXC-LINE-C3]). */
    MockDriver m;
    m.async_supported = false;
    auto ops = ops_for(m);
    BandPipelineStats st = band_pipeline_run(ops, 0, 19, true);

    CHECK(st.degraded, "degraded flagged");
    CHECK(st.kicked == 0 && st.blocking == 20,
          "all 20 blocking (got kick %d blocking %d)", st.kicked, st.blocking);
    CHECK(m.reuse_violations == 0, "C2 holds in degrade");
    check_all_transferred(m, 0, 19, "kick-refusal");
}

static void test_midframe_kick_failure() {
    /* Async dies at the 3rd kick (band 2): bands 0-1 async, 2-4
     * blocking; every band still reaches the panel exactly once. */
    MockDriver m;
    m.fail_kick_at = 2;
    auto ops = ops_for(m);
    BandPipelineStats st = band_pipeline_run(ops, 0, 4, true);

    CHECK(st.degraded, "degraded flagged");
    CHECK(st.kicked == 2 && st.blocking == 3,
          "2 async + 3 blocking (got %d/%d)", st.kicked, st.blocking);
    CHECK(m.reuse_violations == 0, "C2 holds across mid-frame degrade");
    CHECK(m.in_flight_buf == -1, "drained at return");
    check_all_transferred(m, 0, 4, "mid-frame degrade");
}

static void test_wait_timeout_no_wedge() {
    /* First wait times out ([CXC-LINE-C11]): pipeline reports it,
     * degrades to blocking, completes the frame — no wedge. */
    MockDriver m;
    m.timeout_wait_at = 0;
    auto ops = ops_for(m);
    BandPipelineStats st = band_pipeline_run(ops, 0, 4, true);

    CHECK(st.wait_timeouts == 1, "one timeout recorded, got %d",
          st.wait_timeouts);
    CHECK(st.degraded, "degraded after timeout");
    CHECK(st.kicked == 1, "only band 0 kicked async, got %d", st.kicked);
    CHECK(st.blocking == 4, "bands 1..4 blocking, got %d", st.blocking);
    CHECK(st.computed == 5, "frame completed (5 computed), got %d",
          st.computed);
    check_all_transferred(m, 0, 4, "timeout degrade");
}

static void test_randomized_stress() {
    /* C2 under stress: random kick failures + timeouts over many runs;
     * the reuse invariant and the full-transfer postcondition must
     * hold in every schedule shape. */
    unsigned seed = 0xC0FFEE;
    auto rnd = [&seed]() {
        seed = seed * 1664525u + 1013904223u;
        return (seed >> 16) & 0x7FFF;
    };
    for (int run = 0; run < 1000; run++) {
        MockDriver m;
        int first = rnd() % 20;
        int last = first + rnd() % (20 - first);
        if (rnd() % 4 == 0) m.async_supported = false;
        if (rnd() % 3 == 0) m.fail_kick_at = rnd() % 8;
        if (rnd() % 5 == 0) m.timeout_wait_at = rnd() % 8;
        bool overlap = (rnd() % 8) != 0;

        auto ops = ops_for(m);
        BandPipelineStats st = band_pipeline_run(ops, first, last, overlap);

        CHECK(m.reuse_violations == 0,
              "stress run %d: buffer reuse violation", run);
        CHECK(m.kick_while_busy == 0,
              "stress run %d: kick with busy queue", run);
        CHECK(st.computed == last - first + 1,
              "stress run %d: computed %d want %d", run, st.computed,
              last - first + 1);
        CHECK((int)m.transferred_bands.size() == last - first + 1,
              "stress run %d: %d transfers want %d", run,
              (int)m.transferred_bands.size(), last - first + 1);
        if (g_failures) return;   /* stop the flood */
    }
}

/* ------------------------------------------------------------------ */

int main() {
    test_mode_selection();
    test_run_coalescing();
    test_rect_to_band_mask();
    test_overlap_happy_path();
    test_single_buffer_path();
    test_kick_refusal_degrade();
    test_midframe_kick_failure();
    test_wait_timeout_no_wedge();
    test_randomized_stress();

    if (g_failures == 0) {
        printf("band pipeline selftest: all invariants hold\n");
        printf("BAND-PIPELINE RESULT: PASS\n");
        return 0;
    }
    printf("band pipeline selftest: %d failure(s)\n", g_failures);
    printf("BAND-PIPELINE RESULT: FAIL\n");
    return 1;
}
