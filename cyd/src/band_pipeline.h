/*
 * band_pipeline.h — [SPEC:CXC-LINE] pure band-render pipeline control
 *
 * The HOST-TESTABLE control core of the classic-CYD line-renderer lift:
 *
 *   - render-mode selection: full-framebuffer pointer-swap vs strict
 *     line-by-line band transfer, keyed on DRIVER CAPABILITY (the
 *     scan-out framebuffer count), never a per-scene flag
 *     ([CXC-LINE-MODES]); full-fb is REFUSED on a board advertising
 *     fewer than 2 scan-out framebuffers.
 *
 *   - the ping-pong double-band-buffer schedule with compute/transfer
 *     overlap ([CXC-LINE-OVERLAP]): band N composites into one buffer
 *     while band N-1 streams out of the other over DMA.  The
 *     buffer-reuse invariant (a buffer is NEVER written while its
 *     transfer is in flight) is enforced STRUCTURALLY by the schedule:
 *     the wait for the in-flight transfer always precedes the kick of
 *     the next one, so by the time buffer A is recomposited, the wait
 *     issued before buffer B's kick has already drained A.
 *
 *   - dirty-band run coalescing ([CXC-LINE-DIRTY]): contiguous dirty
 *     band indices batch into single render_bands calls to keep SPI
 *     window-setting overhead low.
 *
 * PURE C++: no ESP-IDF, no display, no Canvas.  canvas_esp32.cpp binds
 * the ops to the real band buffers + DisplayDriver; the native selftest
 * (band_pipeline_selftest.cpp, env:native_band_test) binds them to a
 * recording mock and asserts the invariants off-hardware.
 *
 * Transfer-bound honesty ([CXC-LINE-OVERLAP] MUST-NOT): overlap trends
 * per-frame wall time toward max(sum compute, sum transfer) + one band
 * — it CANNOT beat SPI bandwidth on a transfer-bound (e.g. empty)
 * full-frame.  The empty/idle win is the dirty-band SKIP, not overlap.
 *
 * Clean-room note ([CXC-LINE-C14]): the technique is implemented from
 * the ratified spec (which lifted the ALGORITHM from public Slint
 * documentation).  No Slint (GPLv3) source was consulted or vendored.
 */

#ifndef BAND_PIPELINE_H
#define BAND_PIPELINE_H

#include <cstdint>

/* ------------------------------------------------------------------ */
/* Render-mode selection seam ([CXC-LINE-MODES])                       */
/* ------------------------------------------------------------------ */

enum class RenderMode : uint8_t {
    Band,       /* strict line-by-line band transfer (classic CYD)     */
    FullFB,     /* full-framebuffer pointer swap (PSRAM/scan-out tier) */
};

/**
 * Select the render mode from driver capability: a driver exposing
 * >= 2 scan-out framebuffers (DisplayDriver::scanout_framebuffers)
 * takes the full-fb pointer-swap path; anything else is band.
 * Band is the DEFAULT and the universal-deployability floor.
 */
inline RenderMode render_mode_select(int scanout_fb_count) {
    return (scanout_fb_count >= 2) ? RenderMode::FullFB : RenderMode::Band;
}

/**
 * Validate a requested mode against driver capability.  Full-fb MUST
 * NOT be entered on a board whose driver returns < 2 scan-out
 * framebuffers (prevents an OOM on a config authored for a capable
 * board): the request is REFUSED and downgraded to band.  *refused is
 * set when a downgrade happened (caller logs it).
 */
inline RenderMode render_mode_validate(RenderMode requested,
                                       int scanout_fb_count,
                                       bool* refused) {
    if (refused) *refused = false;
    if (requested == RenderMode::FullFB && scanout_fb_count < 2) {
        if (refused) *refused = true;
        return RenderMode::Band;
    }
    return requested;
}

/* ------------------------------------------------------------------ */
/* Dirty-band run coalescing ([CXC-LINE-DIRTY])                        */
/* ------------------------------------------------------------------ */

/**
 * Find the next contiguous run of set bits in *mask* at/after band
 * index *from*.  Returns true and fills [*b0, *b1] (inclusive) with
 * the run; false when no set bit remains.  Adjacent dirty bands
 * coalesce into one run so the caller issues one render_bands /
 * SPI-window per run.  (Rect -> band-mask mapping, with clamping and
 * band-boundary handling, is reactive_band_mask in reactive_apply.h —
 * reused by reference, not duplicated here.)
 */
inline bool band_run_next(uint32_t mask, int from, int* b0, int* b1) {
    if (from < 0) from = 0;
    for (int b = from; b < 32; b++) {
        if (!(mask & (1u << b))) continue;
        int e = b;
        while (e + 1 < 32 && (mask & (1u << (e + 1)))) e++;
        *b0 = b;
        *b1 = e;
        return true;
    }
    return false;
}

/* ------------------------------------------------------------------ */
/* Ping-pong overlap schedule ([CXC-LINE-OVERLAP])                     */
/* ------------------------------------------------------------------ */

/**
 * The pipeline's view of the world.  All ops receive the opaque user
 * pointer; `buf` is a ping-pong slot index (0/1).
 */
struct BandPipelineOps {
    void* user;

    /** Composite band `band` into buffer slot `buf`. */
    void (*compute)(void* user, int band, int buf);

    /**
     * Start the ASYNC transfer of buffer `buf` for `band`.  Only ever
     * called with no transfer in flight (the pipeline waits first).
     * Returns false when the driver cannot go async — the pipeline
     * degrades to blocking for the rest of the run ([CXC-LINE-EDGE]).
     */
    bool (*kick)(void* user, int band, int buf);

    /**
     * Wait (BOUNDED — the driver owns the timeout, [CXC-LINE-C11]) for
     * the in-flight transfer.  Returns false on timeout; the pipeline
     * then degrades to blocking and never wedges.
     */
    bool (*wait)(void* user);

    /** Today's blocking transfer (transfer_band). */
    void (*blocking)(void* user, int band, int buf);
};

struct BandPipelineStats {
    int computed = 0;        /* bands composited                       */
    int kicked = 0;          /* async transfers started                */
    int blocking = 0;        /* blocking transfers issued              */
    int waits = 0;           /* completion waits issued                */
    int wait_timeouts = 0;   /* bounded waits that timed out           */
    bool degraded = false;   /* fell back to blocking mid-run          */
};

/**
 * Render bands [band_first, band_last] (inclusive).  With overlap:
 *
 *   compute(N, buf)   — overlaps the in-flight transfer of band N-1
 *                       (which is in the OTHER buffer)
 *   wait()            — drain band N-1's transfer (kick precondition:
 *                       the SPI window-set needs an idle queue, and
 *                       this is ALSO what frees the other buffer
 *                       before its reuse at band N+1)
 *   kick(N, buf)      — start band N's transfer, swap buffers
 *
 * The frame's last transfer is waited before returning: NO async
 * transfer is ever in flight outside this function (touch/capture SPI
 * traffic elsewhere in the loop stays safe, [CXC-LINE-EDGE]
 * coexistence).
 *
 * Without overlap (single buffer / board override / degrade): buffer 0
 * only, compute + blocking transfer per band — byte-identical control
 * flow to the pre-lift render_bands ([CXC-LINE-TARGETS] M5).
 */
inline BandPipelineStats band_pipeline_run(const BandPipelineOps& ops,
                                           int band_first, int band_last,
                                           bool overlap) {
    BandPipelineStats st;
    bool in_flight = false;
    int buf = 0;

    for (int band = band_first; band <= band_last; band++) {
        ops.compute(ops.user, band, buf);
        st.computed++;

        if (in_flight) {
            st.waits++;
            if (!ops.wait(ops.user)) {
                /* Bounded timeout: report + degrade, never wedge. */
                st.wait_timeouts++;
                st.degraded = true;
                overlap = false;
            }
            in_flight = false;
        }

        if (overlap && ops.kick(ops.user, band, buf)) {
            st.kicked++;
            in_flight = true;
            buf ^= 1;
        } else {
            if (overlap) {
                /* Driver refused async — degrade for the rest. */
                st.degraded = true;
                overlap = false;
            }
            ops.blocking(ops.user, band, buf);
            st.blocking++;
        }
    }

    if (in_flight) {
        st.waits++;
        if (!ops.wait(ops.user)) st.wait_timeouts++;
    }
    return st;
}

#endif /* BAND_PIPELINE_H */
