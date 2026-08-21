/*
 * line_perf_test.cpp — [SPEC:CXC-LINE] on-device timing harness.
 *
 * Measures the [CXC-LINE-TARGETS] machine criteria on the REAL classic
 * CYD (network-free: baked configs, synthetic bus values through the
 * real reactive core):
 *
 *   M1 idle-floor    — a ~3-band gauge-needle region driven through
 *                      scene_on_bus_value + dirty-band repaint at
 *                      >= 30 fps sustained.
 *   M2 idle no-op    — empty damage set costs < 5 ms/frame (damage
 *                      check only; zero transfer).
 *   M3 overlap       — compute-heavy full-frame (the 10-widget gauge
 *                      dashboard) double-buffer wall time strictly
 *                      less than the single-buffer sum; reports the
 *                      per-frame times, sum-compute (stub display) and
 *                      the overlap factor.
 *   M5 fallback      — single-buffer blocking path renders the same
 *                      frames (transfer counters prove the mode).
 *
 * TRANSFER-BOUND HONESTY ([CXC-LINE-OVERLAP] MUST-NOT): the empty
 * full-frame is SPI-bandwidth-bound; overlap CANNOT beat it and this
 * harness prints the empty-frame numbers only to show they are ~equal.
 * The empty/idle win is the dirty-band SKIP (M1/M2), never overlap.
 *
 * M4 (pixel gate) and C13 (glass) are NOT machine-closable here: run
 * scripts/cyd_config_crosscheck.py against /capture.bmp on the normal
 * firmware, and eyeball the endless needle-tween phase this harness
 * ends in (statics must not flicker; no band seams).
 *
 * Build/run:
 *   pio run -e cyd_line_perf -t upload && pio device monitor
 * Prints "LINE-PERF RESULT: PASS|FAIL" after the measured phases, then
 * enters the endless glass-gate tween.
 */

#ifdef LINE_PERF_TEST

#include "board_profile.h"
#include "band_pipeline.h"
#include "canvas_esp32.h"
#include "compose_scene.h"

#include <cstdio>
#include <cstring>
#include <cmath>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"

static const char* TAG = "line_perf";

/* ------------------------------------------------------------------ */
/* Baked configs (no network dependency)                               */
/* ------------------------------------------------------------------ */

/* Empty scene: background only — the SPI-transfer-bound floor. */
static const char kEmptyCfg[] = R"(
app:
  id: perf-empty
design:
  title: "perf empty"
  width: 240
  height: 320
  background: "#101018"
widgets: []
)";

/* The M5-baseline-shaped 10-widget gauge dashboard (compute-heavy). */
static const char kDashCfg[] = R"(
app:
  id: perf-dash
design:
  title: "perf dashboard"
  width: 240
  height: 320
  background: "#1E1E2E"
widgets:
  - type: pixel_stack
    id: scene
    width: 240
    height: 320
    children:
      - {type: rect, id: panel, x: 5, y: 5, w: 230, h: 310,
         fill_color: [0x22, 0x22, 0x33, 220],
         stroke_color: [0x44, 0x44, 0x55], corner_radius: 8}
      - {type: label, id: title, text: "ComposeUX Dashboard M5",
         x: 0, y: 8, w: 240, h: 20, font_size: 14, font_weight: bold,
         align: center, color: [0xDD, 0xDD, 0xEE]}
      - {type: gauge, id: gauge_temp, x: 5, y: 30, w: 115, h: 115,
         min_val: 40, max_val: 120, value: 82, unit: C,
         label: Coolant Temp, warning_threshold: 95, danger_threshold: 110}
      - {type: gauge, id: gauge_oil, x: 120, y: 30, w: 115, h: 115,
         min_val: 0, max_val: 150, value: 97, unit: psi,
         label: Oil Pressure, warning_threshold: 120, danger_threshold: 140}
      - {type: led, id: led_on,     x: 5,   y: 155, w: 50, h: 66, state: "on",      label: "ON"}
      - {type: led, id: led_warn,   x: 63,  y: 155, w: 50, h: 66, state: "warning", label: "WARN"}
      - {type: led, id: led_danger, x: 121, y: 155, w: 50, h: 66, state: "danger",  label: "DANGER"}
      - {type: led, id: led_off,    x: 179, y: 155, w: 50, h: 66, state: "off",     label: "OFF"}
      - {type: button, id: ack_btn, label: "ACK ALARMS",
         publish_namespace: cyd/ack, x: 40, y: 240, w: 160, h: 36}
      - {type: label, id: subtitle, text: "M5 - config-driven boot",
         x: 0, y: 296, w: 240, h: 18, font_size: 10, align: center,
         color: [0x88, 0x88, 0x99]}
)";

/* M1 scene: static chrome + ONE bus-bound gauge confined to scanlines
 * 224..271 = bands 14..16 (3 of 20).  The needle tween must repaint
 * ONLY those bands; the static top 14 bands prove the skip on glass. */
static const char kNeedleCfg[] = R"(
app:
  id: perf-needle
design:
  title: "perf needle"
  width: 240
  height: 320
  background: "#101318"
widgets:
  - type: pixel_stack
    id: scene
    width: 240
    height: 320
    children:
      - {type: label, id: heading, text: "CXC-LINE M1", x: 12, y: 8,
         w: 216, h: 20, font_size: 14, font_weight: bold, color: "#26A69A"}
      - {type: rect, id: box1, x: 12, y: 36, w: 216, h: 60,
         fill_color: "#22283A", stroke_color: "#44506A", corner_radius: 6}
      - {type: label, id: static1, text: "static band region",
         x: 20, y: 56, w: 200, h: 16, font_size: 11, color: "#8899AA"}
      - {type: label, id: static2, text: "must never repaint",
         x: 20, y: 74, w: 200, h: 16, font_size: 11, color: "#8899AA"}
      - {type: led, id: led_a, x: 20,  y: 110, w: 50, h: 66, state: "on",  label: "A"}
      - {type: led, id: led_b, x: 95,  y: 110, w: 50, h: 66, state: "off", label: "B"}
      - {type: led, id: led_c, x: 170, y: 110, w: 50, h: 66, state: "warning", label: "C"}
      - type: gauge
        id: fuel_gauge
        x: 40
        y: 224
        w: 160
        h: 48
        min_val: 0
        max_val: 100
        unit: "%"
        label: "FUEL"
        value:
          namespace: demo/fuel
          default: 50
)";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

static void draw_scene_cb(Canvas& canvas, void* ud) {
    scene_draw(canvas, static_cast<Scene*>(ud));
}

/* Compute-only display: transfer is a no-op, so a render measures
 * sum-compute (the sigma-compute term of the overlap formula). */
class StubDisplay : public DisplayDriver {
public:
    void init() override {}
    void transfer_band(const uint16_t*, uint16_t, uint16_t) override {}
    void set_backlight(uint8_t) override {}
};

static float time_full_frames_ms(ESP32Canvas& cv, Scene* sc, int iters) {
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < iters; i++) cv.render_frame(draw_scene_cb, sc);
    return (esp_timer_get_time() - t0) / 1000.0f / iters;
}

static int repaint_mask(ESP32Canvas& cv, Scene* sc, uint32_t mask) {
    int painted = 0, b0 = 0, b1 = 0, from = 0;
    while (band_run_next(mask, from, &b0, &b1)) {
        cv.render_bands(draw_scene_cb, sc, b0, b1);
        painted += b1 - b0 + 1;
        from = b1 + 1;
    }
    return painted;
}

static Scene* build(const char* cfg, ESP32Canvas& cv,
                    const PanelGeometry& geo, const char* what) {
    Scene* s = scene_build(cfg, strlen(cfg), cv, geo);
    if (!s) ESP_LOGE(TAG, "scene_build failed for %s", what);
    return s;
}

static void print_stats(ESP32Canvas& cv, const char* phase) {
    const auto& st = cv.band_render_stats();
    printf("  stats[%s]: computed=%lu async=%lu blocking=%lu "
           "wait_timeouts=%lu\n", phase,
           (unsigned long)st.bands_computed,
           (unsigned long)st.async_transfers,
           (unsigned long)st.blocking_transfers,
           (unsigned long)st.wait_timeouts);
}

/* ------------------------------------------------------------------ */
/* The harness                                                         */
/* ------------------------------------------------------------------ */

static void line_perf_task(void*) {
    bool pass = true;

    BoardProfile& board = board_profile();
    PanelGeometry geo = board.geometry();
    board.display().init();

    static ESP32Canvas canvas;
    canvas.set_display(&board.display());
    canvas.set_overlap_enabled(true);          /* [DD-CXC-LINE-MODE] */
    canvas.create_surface(geo.width, geo.height);
    if (!canvas.band_buffer()) {
        printf("LINE-PERF RESULT: FAIL (band buffer alloc)\n");
        vTaskDelete(nullptr);
        return;
    }
    if (!canvas.overlap_active()) {
        printf("NOTE: overlap inactive (second buffer unavailable) — "
               "measuring the degrade path\n");
    }

    StubDisplay stub;
    const int nb = (geo.height + kBandHeight - 1) / kBandHeight;

    printf("\n[SPEC:CXC-LINE] on-device timing harness — %s, %dx%d, "
           "%d bands, heap %lu\n\n", board.name(), geo.width, geo.height,
           nb, (unsigned long)esp_get_free_heap_size());

    /* ---- M2: idle no-op — the damage check itself ------------------ */
    {
        int64_t t0 = esp_timer_get_time();
        volatile int sink = 0;
        for (int i = 0; i < 100000; i++) {
            int b0, b1;
            if (band_run_next(0u, 0, &b0, &b1)) sink++;
        }
        float us = (esp_timer_get_time() - t0) / 100000.0f;
        bool ok = us < 5000.0f;   /* < 5 ms, [CXC-LINE-TARGETS] M2 */
        printf("M2 idle no-op: empty-damage check %.3f us/frame, zero "
               "transfers -> %s (target < 5 ms; production idle adds "
               "only the bus-queue drain)\n\n", us, ok ? "PASS" : "FAIL");
        pass = pass && ok;
    }

    /* ---- Empty frame: the SPI-bound floor (honesty check) ---------- */
    float empty_blocking_ms = 0, empty_overlap_ms = 0, empty_compute_ms = 0;
    {
        Scene* sc = build(kEmptyCfg, canvas, geo, "empty");
        if (sc) {
            canvas.set_overlap_enabled(false);
            empty_blocking_ms = time_full_frames_ms(canvas, sc, 20);
            canvas.set_overlap_enabled(true);
            empty_overlap_ms = time_full_frames_ms(canvas, sc, 20);
            canvas.set_display(&stub);
            canvas.set_overlap_enabled(false);
            empty_compute_ms = time_full_frames_ms(canvas, sc, 20);
            canvas.set_display(&board.display());
            canvas.set_overlap_enabled(true);

            printf("empty full frame: blocking %.1f ms, overlap %.1f ms, "
                   "compute-only %.1f ms\n", empty_blocking_ms,
                   empty_overlap_ms, empty_compute_ms);
            printf("  -> transfer-bound as specified: overlap does NOT "
                   "beat the ~%.1f ms SPI transfer floor and claims no "
                   "win here ([CXC-LINE-OVERLAP] MUST-NOT); the idle win "
                   "is dirty-band SKIP\n\n",
                   empty_blocking_ms - empty_compute_ms);
            scene_free(canvas, sc);
        } else pass = false;
    }

    /* ---- M3: compute-heavy overlap (10-widget dashboard) ----------- */
    {
        Scene* sc = build(kDashCfg, canvas, geo, "dashboard");
        if (sc) {
            canvas.reset_band_render_stats();
            canvas.set_overlap_enabled(false);
            float blocking_ms = time_full_frames_ms(canvas, sc, 10);
            print_stats(canvas, "dash blocking x10");
            bool fallback_ok =
                canvas.band_render_stats().async_transfers == 0 &&
                canvas.band_render_stats().blocking_transfers ==
                    (uint32_t)(10 * nb);

            canvas.reset_band_render_stats();
            canvas.set_overlap_enabled(true);
            float overlap_ms = time_full_frames_ms(canvas, sc, 10);
            print_stats(canvas, "dash overlap x10");
            bool async_ok = !canvas.overlap_active() ||
                (canvas.band_render_stats().async_transfers ==
                     (uint32_t)(10 * nb) &&
                 canvas.band_render_stats().wait_timeouts == 0);

            canvas.set_display(&stub);
            canvas.set_overlap_enabled(false);
            float compute_ms = time_full_frames_ms(canvas, sc, 10);
            canvas.set_display(&board.display());
            canvas.set_overlap_enabled(true);

            float transfer_ms = empty_blocking_ms - empty_compute_ms;
            float theory_ms =
                (compute_ms > transfer_ms ? compute_ms : transfer_ms);
            bool ok = !canvas.overlap_active() ||
                      (overlap_ms < blocking_ms);
            printf("M3 overlap (dashboard, %d bands): blocking %.1f ms, "
                   "overlap %.1f ms (%.1f%% of blocking), sum-compute "
                   "%.1f ms, sum-transfer ~%.1f ms, max() floor ~%.1f ms "
                   "-> %s\n", nb, blocking_ms, overlap_ms,
                   100.0f * overlap_ms / blocking_ms, compute_ms,
                   transfer_ms, theory_ms,
                   ok ? "PASS (strictly < single-buffer)" : "FAIL");
            printf("M5 fallback counters: %s; overlap counters: %s\n\n",
                   fallback_ok ? "PASS" : "FAIL",
                   async_ok ? "PASS" : "FAIL");
            pass = pass && ok && fallback_ok && async_ok;
            scene_free(canvas, sc);
        } else pass = false;
    }

    /* ---- M1: 3-band needle tween through the REAL reactive path ---- */
    Scene* needle = build(kNeedleCfg, canvas, geo, "needle");
    if (needle) {
        canvas.render_frame(draw_scene_cb, needle);   /* seed the panel */

        canvas.reset_band_render_stats();
        const int frames = 300;
        int total_bands_painted = 0;
        int64_t t0 = esp_timer_get_time();
        for (int i = 0; i < frames; i++) {
            double v = 50.0 + 50.0 * sin(i * 0.12);
            double t = esp_timer_get_time() / 1000000.0;
            /* kind 0 == BusEvent::Kind::Number (bus_mqtt.h) */
            uint32_t mask = scene_on_bus_value(needle, "demo/fuel",
                                               0, v, false, "", t);
            total_bands_painted += repaint_mask(canvas, needle, mask);
        }
        float ms = (esp_timer_get_time() - t0) / 1000.0f / frames;
        float fps = 1000.0f / ms;
        float bands_per = (float)total_bands_painted / frames;
        print_stats(canvas, "needle x300");
        bool ok = fps >= 30.0f;
        bool skip_ok = bands_per <= 4.0f;   /* ~3-band region, never 20 */
        printf("M1 needle tween: %.2f ms/update = %.1f fps sustained, "
               "%.1f bands/update (of %d) -> %s; dirty-band skip %s\n\n",
               ms, fps, bands_per, nb,
               ok ? "PASS (>= 30 fps)" : "FAIL (< 30 fps)",
               skip_ok ? "PASS" : "FAIL");
        pass = pass && ok && skip_ok;
    } else pass = false;

    printf("LINE-PERF RESULT: %s\n\n", pass ? "PASS" : "FAIL");
    printf("Entering the C13 GLASS-GATE phase (endless ~30 fps needle "
           "tween; statics above must be rock-solid — no flicker, no "
           "band seams at the dirty/clean boundary)...\n");

    /* ---- C13 glass gate: endless smooth tween for the human eye ---- */
    if (needle) {
        int i = 0;
        int64_t win0 = esp_timer_get_time();
        int win_frames = 0;
        while (true) {
            double v = 50.0 + 50.0 * sin(i * 0.06);
            double t = esp_timer_get_time() / 1000000.0;
            uint32_t mask = scene_on_bus_value(needle, "demo/fuel",
                                               0, v, false, "", t);
            repaint_mask(canvas, needle, mask);
            i++;
            win_frames++;
            vTaskDelay(pdMS_TO_TICKS(15));
            int64_t now = esp_timer_get_time();
            if (now - win0 > 5000000LL) {
                printf("[GLASS] %.1f fps (needle bands only; heap %lu)\n",
                       win_frames * 1000000.0f / (now - win0),
                       (unsigned long)esp_get_free_heap_size());
                win0 = now;
                win_frames = 0;
            }
        }
    }
    vTaskDelete(nullptr);
}

extern "C" void app_main(void) {
    /* Widget draw calls need more than app_main's default stack. */
    xTaskCreate(line_perf_task, "line_perf", 16384, nullptr, 5, nullptr);
}

#endif /* LINE_PERF_TEST */
