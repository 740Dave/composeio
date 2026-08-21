/*
 * main.cpp — Generic ComposeIO display firmware ([CXC-R-BOOT], M5 + R3)
 *
 * The device is FUNGIBLE: this binary contains no composition.  It
 * boots, joins the network, fetches its .compose.yaml from bus_storage
 * (bus://assets/displays/<device_id>.compose.yaml, falling back to
 * displays/default.compose.yaml), builds the widget scene, and renders
 * it with the band renderer.  Changing the config changes the device's
 * function — zero firmware changes ([CXC-R-BOOT-FUNGIBLE], [CXC-R-C12]).
 *
 * R3 ([SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER]): the display is
 * REACTIVE.  The scene owns an R1 ReactiveCore (firmware/reactive_core,
 * compiled by reference); a ComposeBus client behind the transport
 * seam (bus_transport.h; MQTT by default) subscribes
 * the namespaces the scene's bindings read; an arrival re-evaluates
 * exactly the bindings that read it and applies changed results to
 * widget attributes; ONLY the band(s) the widget occupies repaint
 * (widget-level dirty — [PERF] full frame is 56 ms; a 5-band gauge
 * repaint is ~1/4 of that).  Buttons publish over the same client.
 * An on-device capture server (capture_http.cpp) serves the CURRENT
 * frame at GET /capture.bmp for eyes-free verification.
 *
 * Boot sequence (as-built subset of [CXC-R-BOOT-SEQUENCE]):
 *   1. init display via the board profile        (board_profile.h)
 *   2. splash frame (device id on screen)
 *   3. WiFi up                                    (wifi.cpp)
 *   3b. bus client + capture server               (bus_transport.h,
 *       capture_http.cpp)
 *   4. fetch config                               (config_fetch.cpp)
 *   5. parse + build widget scene + bindings      (compose_scene.cpp)
 *   6. render + register touch + subscribe        ([CXC-R-C9])
 *   7. one on-device capture push per scene       (capture.cpp)
 *   8. loop: touch poll -> bus drain -> dirty-band repaint (reactive)
 *      / full frame (animation or tap); config re-poll every
 *      kConfigPollSec ([CXC-R-C10])
 *   Deferred on this tier (logged, not faked): bus discovery announce,
 *   behaviors, map:/when:/invert: binding modifiers.
 */

/* DSL_CORPUS_TEST builds ([env:cyd_dsl_test]) replace this app with the
 * [SPEC:CXC-DSL] Pass-2b conformance runner — see dsl_selftest.cpp. */
#ifndef DSL_CORPUS_TEST

#include "announce.h"
#include "board_profile.h"
#include "canvas_esp32.h"
#include "compose_scene.h"
#include "freshness.h"
#include "config_chunk.h"       /* config_content_hash() — the scene fingerprint */
#include "config_fetch.h"
#include "widgets/sprite.h"
#include "widgets/button.h"
#include "capture_http.h"
#include "bus_transport.h"
#include "config_bus.h"
#include "config_source.h"
#include "restart.h"
#include "bus_binding.h"
#include "touch_dispatch.h"
#include "wifi.h"

/* ComposePlatform boot logo, baked CI16 into a compiled-in array
 * (renders before any network dependency — cannot be bus-fetched).
 * Regenerate with python/scripts/bake_logo_splash.py. */
#include "gen/compose_logo_splash_cyd.h"


#include <cstdlib>
#include <cstring>
#include <cstdio>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"

static const char* TAG = "main";

/* Config re-poll interval ([CXC-R-BOOT] step 12 says 5 min; 60 s here
 * to make config-swap turnaround practical while this tier is POC). */
static constexpr int64_t kConfigPollSec = 60;

/* ---- Config poll vs. INTERACTION ([CXC-ER-STALL]) -------------------
 *
 * MEASURED on the C3/ST7796, 5 minutes, stall detector armed: the 60 s
 * config re-poll blocks this task for 160-219 ms and reports
 * "unchanged" every single time.  It is the only unbounded thing in the
 * loop (an HTTP GET), it runs on the same task as the bus drain and the
 * repaint, and it fires on a timer with no relationship to what the
 * user is doing — which is exactly the shape of "it freezes for no
 * correlatable reason and then catches up".
 *
 * The fix here is NOT to make the fetch faster; it is to stop it
 * colliding with interaction.  A poll is DEFERRED while the bus is
 * live, so a 200 ms block can no longer land in the middle of someone
 * working the levers.  It is only a deferral: kMaxDeferSec bounds it,
 * so a board under continuous traffic still picks up a config change
 * within a few minutes rather than never.
 *
 * The honest alternative — moving the fetch to its own task — buys a
 * strictly better result (zero block, ever) at the cost of another
 * stack on a board with ~50 KB of free heap, plus a handoff for the
 * scene swap, which must still be serialised against the renderer.
 * That is the right change if this deferral proves insufficient; it is
 * not the right first change against an UNCONFIRMED cause. */
static constexpr int64_t kBusQuietBeforePollSec = 3;
static constexpr int64_t kMaxDeferSec = 300;

/* Boot-loop guard window ([CXC-ER-RESTART-BUS]): restart commands whose
 * receive time is within this window of boot are ignored. */
static constexpr uint32_t kRestartBootGuardMs = 3000;

/* Reboot seam ([CXC-ER-REMOTE-RESTART]): give the pre-restart ack + WiFi
 * TX a brief moment to flush before the hard reset (best-effort ack —
 * the post-boot presence announce is the authoritative loop-close). */
static void reboot_now(void) {
    ESP_LOGW(TAG, "[CXC-ER-REMOTE-RESTART] restart command honored — "
             "esp_restart() (re-runs boot-fetch)");
    vTaskDelay(pdMS_TO_TICKS(250));
    esp_restart();
}

/* ------------------------------------------------------------------ */
/* Splash / error screens (the only firmware-baked drawing)            */
/* ------------------------------------------------------------------ */

struct SplashCtx {
    ImageHandle logo;       /* decoded ComposePlatform logo (or nullptr) */
    const char* line1;
    const char* line2;
    const char* line3;
};

#ifdef CXC_SPLASH_COLOR_BARS
/* THE COLOUR BASELINE — A BRING-UP INSTRUMENT, DEFAULT OFF
 * ([CXC-BOARD-HW-SELF-CONFIRMING], [CXC-BRINGUP-VERDICT] tier 2).
 *
 * Five LABELLED bands of PURE RGB565 drawn onto the STANDARD ComposePlatform
 * splash — the logo and device id are untouched — so one screen carries both
 * the identity frame and the diagnostic.  Enabled per board with
 * `CXC_SPLASH_COLOR_BARS` in src/CMakeLists.txt, held for
 * CXC_SPLASH_COLOR_BARS_MS, then the boot continues automatically.
 *
 * IT IS OFF IN A SHIPPING BUILD AND ON FOR EXACTLY ONE FLASH.  The procedure
 * is in docs/board-bringup/RUNBOOK.md: build once with it on, get the owner's
 * eyes on the labelled bands, record the answer in the board descriptor,
 * rebuild without it.  It is kept rather than deleted because every NEW panel
 * needs this same baseline and the labelling — not the bands — is the part
 * that took work to get right.
 *
 * WHY THIS EXISTS AT ALL.  Pixel byte order and colour channel order are the
 * class of property that NO on-device instrument can check, because every
 * readback path decodes the same buffer with the same assumption that wrote
 * it — GET /capture.bmp renders a perfect image over a byte-swapped panel,
 * which is exactly what happened on the Waveshare C6 on 2026-08-11.  Human
 * eyes are the only channel that exists.
 *
 * WHY LABELLED, AND WHY NOT JUST THE WIDGET.  A widget conflates layers: if
 * the engine bars look wrong the cause could be MADCTL/BGR, RGB565 byte
 * order, the scene's own colour constants, or the renderer's mapping.  One
 * pure band with the name of the colour it is SUPPOSED to be isolates a
 * single variable, so "the band labelled BLUE looks green" is not an opinion,
 * it is arithmetic:
 *
 *   BYTE swap     blue  0x001F -> 0x1F00 renders GREEN
 *                 green 0x07E0 -> 0xE007 renders RED
 *   CHANNEL swap  blue renders RED and red renders BLUE; green is unchanged
 *
 * Those two are told apart by WHICH colour goes wrong, which is how the C6's
 * RAMCTRL endian bit was found and fixed in one bit rather than hunted.
 *
 * The 8-bit values below are chosen so the canvas's rgb565_be() quantisation
 * lands on the exact target word — (r&0xF8)<<8 | (g&0xFC)<<3 | b>>3 — so
 * these really are 0xF800 / 0x07E0 / 0x001F / 0xFFFF / 0x8410 and not
 * something near them.  Labels are drawn ABOVE the strip, never inside it,
 * so each band stays a single pure colour.
 *
 * Compiled in only where a board's CMake branch defines
 * CXC_SPLASH_COLOR_BARS; every other board's splash is byte-identical to
 * before, and so is this board's once the flag comes out. */
#ifndef CXC_SPLASH_COLOR_BARS_MS
#define CXC_SPLASH_COLOR_BARS_MS 6000
#endif

struct BaselineBand {
    const char* name;
    uint8_t r, g, b;
    uint16_t rgb565;        /* what the canvas must produce; log-only */
};
static const BaselineBand kBaselineBands[] = {
    {"RED",   0xFF, 0x00, 0x00, 0xF800},
    {"GREEN", 0x00, 0xFF, 0x00, 0x07E0},
    {"BLUE",  0x00, 0x00, 0xFF, 0x001F},
    {"WHITE", 0xFF, 0xFF, 0xFF, 0xFFFF},
    {"GREY",  0x80, 0x80, 0x80, 0x8410},
};
static constexpr int kBaselineBandCount =
    (int)(sizeof(kBaselineBands) / sizeof(kBaselineBands[0]));

static void draw_color_baseline(Canvas& canvas) {
    Size sz = canvas.get_surface_size();
    float strip_h = 44.0f;
    float strip_y = (float)sz.height - strip_h - 2.0f;
    float col_w = (float)sz.width / (float)kBaselineBandCount;

    canvas.set_fill_color(0x88, 0x88, 0x99);
    canvas.set_font("sans-serif", 9, "regular");
    canvas.draw_text("RGB565 baseline — each band must be the colour it names",
                     sz.width / 2.0f, strip_y - 16.0f, "center");

    for (int i = 0; i < kBaselineBandCount; i++) {
        const BaselineBand& b = kBaselineBands[i];
        float x = i * col_w;
        canvas.set_fill_color(0xDD, 0xDD, 0xEE);
        canvas.set_font("sans-serif", 11, "bold");
        canvas.draw_text(b.name, x + col_w / 2.0f, strip_y - 4.0f, "center");
        canvas.set_fill_color(b.r, b.g, b.b);
        canvas.fill_rect(x, strip_y, col_w, strip_h);
    }
}
#endif /* CXC_SPLASH_COLOR_BARS */

static void draw_splash(Canvas& canvas, void* ud) {
    auto* ctx = static_cast<SplashCtx*>(ud);
    Size sz = canvas.get_surface_size();
    canvas.set_fill_color(0x1E, 0x1E, 0x2E);
    canvas.fill_rect(0, 0, sz.width, sz.height);

#ifdef CXC_SPLASH_COLOR_BARS
    /* One call site, before the logo/captions, so BOTH splash layouts (logo
     * hero and wordmark fallback) carry the bands — a fallback splash is
     * exactly the case where you most want the baseline. */
    draw_color_baseline(canvas);
#endif

    float cx = sz.width / 2.0f;
    float cy = sz.height / 2.0f;

    if (ctx->logo) {
        /* Logo is the hero: centered + lifted so the operator captions
         * (status / device id) sit below it.  Baked bg matches the fill
         * above, so the square tile blends seamlessly. */
        float s = (float)COMPOSE_LOGO_CYD_W;
        float ly = cy - 34.0f;
        canvas.draw_image(ctx->logo, cx - s / 2.0f, ly - s / 2.0f, s, s);
        float cap = ly + s / 2.0f + 20.0f;
        canvas.set_fill_color(0xDD, 0xDD, 0xEE);
        canvas.set_font("sans-serif", 12, "regular");
        if (ctx->line1) canvas.draw_text(ctx->line1, cx, cap, "center");
        canvas.set_fill_color(0xAA, 0xAA, 0xBB);
        canvas.set_font("sans-serif", 11, "regular");
        if (ctx->line2) canvas.draw_text(ctx->line2, cx, cap + 18.0f, "center");
        canvas.set_fill_color(0x88, 0x88, 0x99);
        canvas.set_font("sans-serif", 10, "regular");
        if (ctx->line3) canvas.draw_text(ctx->line3, cx, cap + 34.0f, "center");
        return;
    }

    /* Fallback wordmark (logo decode failed — still proves the panel). */
    canvas.set_fill_color(0x26, 0xA6, 0x9A);
    canvas.set_font("sans-serif", 16, "bold");
    canvas.draw_text("ComposeUX", cx, cy - 40, "center");

    canvas.set_fill_color(0xDD, 0xDD, 0xEE);
    canvas.set_font("sans-serif", 12, "regular");
    if (ctx->line1) canvas.draw_text(ctx->line1, cx, cy - 8, "center");
    canvas.set_fill_color(0xAA, 0xAA, 0xBB);
    if (ctx->line2) canvas.draw_text(ctx->line2, cx, cy + 16, "center");
    canvas.set_fill_color(0x88, 0x88, 0x99);
    canvas.set_font("sans-serif", 10, "regular");
    if (ctx->line3) canvas.draw_text(ctx->line3, cx, cy + 40, "center");
}

/* Decoded once at boot; nullptr until decode_logo() runs. */
static ImageHandle s_logo = nullptr;

/* The one canvas, published for wire_scene ([CXC-ER-DAMAGE] text
 * metrics).  Set once in app_main before any scene is wired. */
static ESP32Canvas* s_measure_canvas = nullptr;

static void show_splash(ESP32Canvas& canvas, const char* l1, const char* l2,
                        const char* l3) {
    SplashCtx ctx{s_logo, l1, l2, l3};
    canvas.render_frame(draw_splash, &ctx);
}

/* ------------------------------------------------------------------ */
/* Scene draw trampoline                                               */
/* ------------------------------------------------------------------ */

/* THE STALENESS BADGE ([CXC-CONFIG-BUS-LEAF] staleness).
 *
 * A board that boots from its cached scene while re-requesting is resilient —
 * and can render a months-old composition while looking perfectly healthy.
 * "A dark or stale panel is indistinguishable from a working one" is the whole
 * failure this project has already paid for once, so a cached scene wears a
 * mark that eyes cannot miss and no machine signal is asked to substitute for.
 *
 * It is drawn HERE, in the app shell's scene trampoline, and not in a widget:
 * the narrow waist stays where it is.  Every widget, the Canvas contract and
 * the scene builder are untouched; this is one shell-level overlay through the
 * same public Canvas calls the splash already uses.
 *
 * It rides the SCENE PASS, so any dirty-band repaint that covers those rows
 * redraws it — the badge cannot be left behind by a partial repaint. */
static void draw_stale_badge(Canvas& canvas) {
    Size sz = canvas.get_surface_size();
    float w = 74.0f, h = 16.0f;
    float x = sz.width - w - 2.0f, y = 2.0f;
    canvas.set_fill_color(0xC0, 0x10, 0x60);      /* magenta: nothing else is */
    canvas.fill_rect(x, y, w, h);
    canvas.set_fill_color(0xFF, 0xFF, 0xFF);
    canvas.set_font("sans-serif", 9, "bold");
    static char line[24];
    snprintf(line, sizeof(line), "CACHED +%lub",
             (unsigned long)config_cache_age_boots());
    canvas.draw_text(line, x + w / 2.0f, y + 12.0f, "center");
}

static void draw_scene_cb(Canvas& canvas, void* ud) {
    scene_draw(canvas, static_cast<Scene*>(ud));
    if (config_is_stale()) draw_stale_badge(canvas);
}

/* ------------------------------------------------------------------ */
/* Perf instrumentation ([CXC-RM-M5] perf-floor)                       */
/* ------------------------------------------------------------------ */

struct PerfWindow {
    int64_t sum_us = 0;
    int64_t min_us = INT64_MAX;
    int64_t max_us = 0;
    int frames = 0;

    void add(int64_t us) {
        sum_us += us;
        if (us < min_us) min_us = us;
        if (us > max_us) max_us = us;
        frames++;
    }
    void reset() { sum_us = 0; min_us = INT64_MAX; max_us = 0; frames = 0; }
};

/* ------------------------------------------------------------------ */
/* Config fetch + scene swap                                           */
/* ------------------------------------------------------------------ */

static char* s_config_text = nullptr;
static size_t s_config_len = 0;

/* The leaf's own voice when it has no scene ([CXC-CONFIG-BUS-LEAF]
 * must-announce).  config_bus writes here; the boot retry loop puts it on the
 * glass.  A board with no scene must SAY SO — on the bus if it can and on its
 * own glass if it cannot, and under `config: bus` with the radio off the glass
 * is the ONLY channel left. */
static char s_announce_line[160] = "";
static void announce_on_glass(const char* line) {
    snprintf(s_announce_line, sizeof(s_announce_line), "%s", line ? line : "");
}

/* WHERE THE SCENE COMES FROM ([CXC-CONFIG-BUS-STRATEGY]).  One switch, three
 * strategies, and NOTHING above this line knows which one ran — the scene
 * builder, the widgets and the canvas are handed identical bytes whichever
 * path delivered them.  Returns malloc'd text (caller frees) or nullptr. */
static char* config_obtain(size_t* out_len) {
    if (out_len) *out_len = 0;
    switch (config_source()) {
        case ConfigSource::Http: {
            size_t len = 0;
            char* text = config_fetch(&len);
            if (text) {
                /* Every successful fetch refreshes the cache, whatever the
                 * strategy, so `config: cached` is a fallback that EXISTS
                 * rather than one somebody has to provision. */
                config_cache_store(text, len);
                config_set_stale(false);
                /* [CXC-REPOINT-CONFIRM] a 200 + non-empty body from the ACTIVE
                 * tier's store is the STORE plane of a commit-confirmed trial. */
                bus_binding_note_store_ok();
                if (out_len) *out_len = len;
            } else if (bus_binding_active_is_trial()) {
                /* [CXC-REPOINT-REVERT] Note 1: name WHY the store plane failed —
                 * an HTTP code (`store-http-404`) vs no response at all
                 * (`store-unreachable`) — so a revert is diagnosable on the
                 * bench without a serial console. */
                int st = config_fetch_last_status();
                char reason[24];
                if (st > 0) snprintf(reason, sizeof(reason), "store-http-%d", st);
                else        snprintf(reason, sizeof(reason), "store-unreachable");
                bus_binding_note_reason(reason);
            }
            return text;
        }
        case ConfigSource::Bus: {
            size_t len = 0;
            char* text = config_bus_take(&len);
            if (text) {
                config_cache_store(text, len);
                config_set_stale(false);
                if (out_len) *out_len = len;
            }
            return text;
        }
        case ConfigSource::Cached: {
            static bool loaded = false;
            if (loaded) return nullptr;      /* no fetch: there is nothing new */
            loaded = true;
            size_t len = 0;
            char* text = config_cache_load(&len);
            if (!text) {
                /* KNOWN strategy, unreachable backend -> LOUD degrade, keep
                 * running (transport_registry.py rule 2). */
                ESP_LOGE(TAG, "`config: cached` selected but NOTHING is "
                         "cached.  This board has no scene and no way to get "
                         "one; switch it with _device/<id>/config_source = "
                         "\"bus\" or \"http\".");
                announce_on_glass("NO CACHED CONFIG — set config_source");
                return nullptr;
            }
            /* The cache is by definition of unknown age.  Mark it, so the
             * glass says so for as long as it is showing. */
            config_set_stale(true);
            if (out_len) *out_len = len;
            return text;
        }
        case ConfigSource::Unknown:
            /* config_source() already said why, loudly, once.  Say it on the
             * glass too — a refusal nobody can see is a dark panel. */
            announce_on_glass("UNKNOWN config_source — see console");
            return nullptr;
    }
    return nullptr;
}

/* Obtain config; returns a new scene when the config changed (or first
 * boot), nullptr when unchanged/unavailable. */
static Scene* try_fetch_scene(ESP32Canvas& canvas, const PanelGeometry& geo,
                              bool force) {
    size_t len = 0;
    char* text = config_obtain(&len);
    if (!text) return nullptr;

    if (!force && s_config_text && len == s_config_len &&
        memcmp(text, s_config_text, len) == 0) {
        free(text);
        return nullptr;    /* unchanged */
    }

    Scene* scene = scene_build(text, len, canvas, geo);
    if (!scene) {
        free(text);
        return nullptr;
    }
    free(s_config_text);
    s_config_text = text;
    s_config_len = len;
    return scene;
}

/* ------------------------------------------------------------------ */
/* R3 reactive wiring                                                  */
/* ------------------------------------------------------------------ */

/* Publish over the SELECTED transport — the display is a full bus peer,
 * and it does not know (or say) which substrate carries this.  One thunk,
 * two consumers: ButtonWidget's publish hook and restart.cpp's injected
 * RestartPublishFn, which is a plain function pointer and so cannot bind
 * a member function directly. */
static void bus_publish_state(const char* ns, const char* value) {
    bus_transport().publish_state(ns, value);
}

/* Point the bus subscriptions + capture server + publish hook at a
 * (new) scene.  Call while holding the render lock. */
static void wire_scene(Scene* scene) {
    /* Widgets whose damage span needs real text metrics measure through
     * the canvas that will do the drawing ([CXC-ER-DAMAGE]). */
    scene_set_measure_canvas(scene, s_measure_canvas);

    /* THE FRESHNESS CONTRACT, RE-DECLARED FOR THIS SCENE.
     * [SPEC:CXC-FRESHNESS].  Reset first: a scene swap replaces the whole
     * declaration set, and carrying a namespace forward from a scene that no
     * longer binds it would leave the board judging something nothing is
     * watching — and, worse, publishing a verdict about it. */
    freshness().reset();
    int contradictions = 0;
    const int declared = freshness_declare_from_yaml(
        freshness(), scene_yaml_root(scene), &contradictions);
    /* ⛔ REFUSALS AND CONTRADICTIONS ARE ERRORS, NOT DEBUG COUNTERS.  A
     * refused declaration means some namespace on this board is watched by
     * NOTHING, and the failure is invisible from the glass because an
     * unwatched namespace looks exactly like a fresh one.  `kMaxPersist` was
     * 4, exactly the size of the control plane the day it was written, and it
     * announced its own overflow in one WARN line nobody read. */
    if (freshness().refused() || contradictions)
        ESP_LOGE(TAG, "freshness: %lu declaration(s) REFUSED and %d "
                 "contradictory — those namespaces are watched by NOTHING and "
                 "will read as UNDECLARED (capacity %d)",
                 (unsigned long)freshness().refused(), contradictions,
                 kMaxFreshnessEntries);
    if (declared)
        ESP_LOGI(TAG, "freshness: %d namespace(s) declared a contract",
                 declared);
    /* SENSITIVITY LOST TO THE SAMPLER, said out loud.  A cadence faster than
     * the 250 ms evaluator keeps its contract but needs ~ceil(eval/interval)
     * consecutive misses instead of 3 before it is condemned.  That is the
     * honest price of a sampled reader; it is REPORTED rather than being the
     * silent difference between two constants nobody compares. */
    if (freshness().sampling_limited())
        ESP_LOGW(TAG, "freshness: %lu namespace(s) declare a cadence FASTER "
                 "than the %lu ms evaluator — their timeout is floored to it, "
                 "so they detect ~N consecutive misses, not one",
                 (unsigned long)freshness().sampling_limited(),
                 (unsigned long)kFreshnessEvalIntervalMs);

    /* SUBSCRIPTIONS = the scene's namespaces PLUS every declared node's
     * heartbeat.  On a FRAMED substrate an inbound frame is matched by ns
     * HASH against the registered set and a CRC-16 does not invert — so a
     * beat from a node this board never declared literally cannot be NAMED on
     * arrival.  Declaring the node is what makes its beat receivable, which is
     * [CXC-FRESH-HEARTBEAT-RX]'s "no attributed publisher means no verdict"
     * stated from the transport end. */
    static const char* subs[24];
    size_t n = scene_subscription_count(scene);
    if (n > 16) n = 16;                    /* the scene's own budget, as before */
    for (size_t i = 0; i < n; i++) subs[i] = scene_subscription_at(scene, i);
    const char* hb[8];
    size_t hbn = freshness().heartbeat_namespaces(hb, 8);
    for (size_t i = 0; i < hbn && n < 24; i++) subs[n++] = hb[i];
    if (hbn)
        ESP_LOGI(TAG, "freshness: subscribing %u peer heartbeat namespace(s)",
                 (unsigned)hbn);
    bus_transport().set_subscriptions(subs, n);
    capture_http_set_scene(draw_scene_cb, scene, scene_title(scene));
    ButtonWidget::set_publish_fn(bus_publish_state);

    /* WHAT IS ACTUALLY ON THE GLASS, as a 16-bit content hash of the config
     * bytes that produced it.  Deliberately the SAME hash config_bus already
     * ACKs a transfer with (config_content_hash — CRC-16/CCITT-FALSE), so an
     * operator can read `_device/<id>/scene` and the config-push log line
     * against each other with no translation.
     *
     * It is set HERE rather than in try_fetch_scene because this is the
     * function that runs on EVERY path a scene reaches the renderer by —
     * first boot, HTTP re-poll swap, and a config-over-bus push.  A device
     * reporting a scene it did not wire would be worse than reporting none. */
    announce_set_scene_hash(
        (s_config_text && s_config_len)
            ? config_content_hash((const uint8_t*)s_config_text, s_config_len)
            : 0);
    announce_set_config_stale(config_is_stale());

    ESP_LOGI(TAG, "[CXC-ER-RENDER] scene '%s' wired: %d bindings, "
             "%u bus subscriptions", scene_title(scene),
             scene_binding_count(scene), (unsigned)n);
}

/* CONTROL-PLANE-ONLY DRAIN — for the pre-scene boot retry loop.
 *
 * ⚠ IT FIXES A LATENT DEFECT, NOT ONLY THE POLL.  The retry loop below spins
 * until a config arrives and NEVER called bus.poll().  `_device/<id>/restart`
 * survived that because every transport dispatches it on its own RX TASK
 * before queueing — but `transport`, `config_source` and (now) the fleet poll
 * are all dispatched from the DRAIN, so on a board stuck at "waiting for
 * config" they were never seen at all, and their arrivals sat in a 16-deep
 * queue until it overflowed.  That is the exact board an operator most needs
 * to reach, and `config_source` is the exact command that would rescue it.
 *
 * It routes NOTHING to a scene (there is no scene), and it deliberately does
 * not touch config_bus or restart: both are handled on the transport's rx
 * task and never reach this queue. */
static void drain_control_plane(void) {
    BusEvent ev;
    while (bus_transport().poll(&ev)) {
        if (bus_transport_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        if (announce_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        if (config_source_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        /* [CXC-REPOINT] a binding is retained state — adopt it even pre-scene
         * and on non-mqtt transports whose rx task does not intercept it. */
        if (bus_binding_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        ESP_LOGD(TAG, "pre-scene arrival on %s ignored — no scene yet", ev.ns);
    }
}

/* THE FRESHNESS SINK — one verdict, delivered through the IDENTICAL entry
 * point a real bus arrival uses.  [SPEC:CXC-FRESHNESS], David's ruling: "we
 * shouldn't have to program for that, it should just happen because a new
 * state has been emitted."
 *
 * ⛔ NOTE WHAT IS *NOT* HERE.  There is no branch on the verdict, no widget
 * call, no draw call and no staleness handling of any kind — this function
 * hands `<ns>/_fresh` and `<ns>/_verdict` to scene_on_bus_value() exactly as
 * drain_bus hands it `dradis/contacts`.  Every consumer reacts through the
 * declarative binding it already had, and the acceptance test for the whole
 * design is that you cannot point at a line of C++ outside the receiver that
 * "handles staleness".  If a future edit adds one here, that is the fork this
 * design exists to prevent. */
struct FreshSinkCtx { Scene* scene; uint32_t dirty; };

static void freshness_scene_sink(void* user, const char* ns, int kind,
                                 double num, bool b, const char* str) {
    FreshSinkCtx* c = (FreshSinkCtx*)user;
    const double t = esp_timer_get_time() / 1000000.0;
    c->dirty |= scene_on_bus_value(c->scene, ns, kind, num, b, str, t);
}

static uint32_t freshness_tick_scene(Scene* scene) {
    FreshSinkCtx c{scene, 0};
    freshness_tick((uint32_t)(esp_timer_get_time() / 1000),
                   freshness_scene_sink, &c);
    return c.dirty;
}

/* Drain queued bus arrivals into the scene's reactive core; returns
 * the union dirty band mask. */
static uint32_t drain_bus(Scene* scene, int* arrivals) {
    BusEvent ev;
    uint32_t dirty = 0;
    /* The band mask resets per call and is OR-ed here; the exact row
     * spans ACCUMULATE inside the scene, so this drain owns their
     * reset ([CXC-ER-DAMAGE]). */
    scene_clear_dirty_spans(scene);
    while (bus_transport().poll(&ev)) {
        double t = esp_timer_get_time() / 1000000.0;
        const uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);

        /* ⛔ THE FRESHNESS RECEIVER SEES EVERY INBOUND MESSAGE FIRST —
         * BEFORE the control-plane sieve ([CXC-FRESH-HEARTBEAT-RX] ordering).
         * A peer whose value a later stage rejects is still a peer that is
         * ALIVE, and losing its attribution because its data was malformed
         * would condemn it for the wrong reason.  It consumes nothing and is
         * therefore not part of the sieve. */
        if (ev.unknown) {
            /* An explicit 0xFD: "alive, and I know that I do not know."  It is
             * CONSUMED here and never routed to the scene — see
             * BusEvent::unknown for why a fourth Kind would arrive at a widget
             * (or a needle) as a real 0.0. */
            freshness_note_wire_unknown(ev.ns, now_ms);
            continue;
        }
        freshness_note_arrival(ev.ns, now_ms);

        switch (ev.kind) {
            case BusEvent::Kind::Number:
                ESP_LOGI(TAG, "BUS %s = %g", ev.ns, ev.num);
                break;
            case BusEvent::Kind::Bool:
                ESP_LOGI(TAG, "BUS %s = %s", ev.ns, ev.b ? "true" : "false");
                break;
            default:
                ESP_LOGI(TAG, "BUS %s = '%s'", ev.ns, ev.str);
                break;
        }
        /* Transport control plane, consumed HERE and never routed to the
         * scene ([CXC-BOARD-HW-UART-SLAVE]). */
        if (bus_transport_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        /* Fleet-discovery poll — same shape, same reason.  A truthy
         * `_device/broadcast/announce` (or the per-device form) republishes
         * this board's whole announcement set on the next tick. */
        if (announce_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        /* Config-source control plane ([CXC-CONFIG-BUS-STRATEGY]) — same
         * shape, same reason: a command is never routed to the scene. */
        if (config_source_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        /* Bus-binding control plane ([CXC-REPOINT]) — the retained binding
         * fact; a differing value on the active tier arms a trial and restarts.
         * On MQTT the rx task already intercepts it (bus_mqtt.cpp), so this
         * covers the framed transports and the pre-scene path. */
        if (bus_binding_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        /* PEER HEARTBEATS — same shape, same reason, and this is the stage
         * that answers "can heartbeats reach the receiver WITHOUT flooding
         * the scene binding path?".  They do reach the queue (both framed
         * transports used to drop every non-State message at the door, which
         * made NODE_SILENT structurally unreachable), and they are eaten HERE,
         * one stage before scene_on_bus_value() — so a beat carries no value
         * into the reactive core, dirties no band, and repaints nothing. */
        if (freshness_consume_heartbeat(ev.ns, (int)ev.kind, ev.num, ev.str,
                                        now_ms))
            continue;
        dirty |= scene_on_bus_value(scene, ev.ns, (int)ev.kind, ev.num,
                                    ev.b, ev.str, t);
        (*arrivals)++;
    }

    /* THE FRESHNESS TICK, HERE AND NOT AT THE BOTTOM OF THE LOOP.
     * [SPEC:CXC-FRESHNESS] — expiry is LAZY, so this is a READ, not a sweep,
     * and it runs on a loop the firmware already has (no new timer, no new
     * FreeRTOS task).  It must be INSIDE the drain because a verdict is
     * published through scene_on_bus_value() like any other arrival and
     * therefore returns a DIRTY BAND MASK — dropping that mask would leave the
     * glass showing the pre-verdict frame until some unrelated arrival
     * happened to repaint it, which is a staleness bug inside the staleness
     * fix. */
    dirty |= freshness_tick_scene(scene);
    return dirty;
}

/* Cost of one dirty repaint, split so the two halves can be judged
 * separately (rasterisation vs SPI transfer — they scale differently
 * and only one of them is helped by a narrower X range). */
struct RepaintCost {
    int bands = 0;
    int rows = 0;
    int spans = 0;
    int64_t draw_us = 0;
    int64_t xfer_us = 0;
};

/* Repaint the bands in *mask* (contiguous runs batched).
 *
 * On the band tier each run is a band walk; on the full-framebuffer tier
 * each run is ONE scene pass gated to the run's rows, re-rasterised in
 * place and transferred as just those rows ([CXC-ER-RENDER]).  Either
 * way the widgets are untouched — the granularity lives in the backend. */
static RepaintCost repaint_dirty_bands(ESP32Canvas& canvas, Scene* scene,
                                       uint32_t mask) {
#ifdef CXC_FORCE_FULL_REPAINT
    /* MEASUREMENT BASELINE ONLY — reproduces the pre-dirty-span cost
     * (every arrival repaints and re-transfers the whole frame) so the
     * before/after numbers come from ONE binary under the SAME traffic.
     * Never defined in a shipping build. */
    if (mask) mask = 0xFFFFFFFFu;
#endif
    RepaintCost c;

#ifdef CXC_FINE_DAMAGE
    /* EXACT ROW SPANS ([CXC-ER-DAMAGE]) — repaint what the widgets said
     * changed, not the 16-row quantisation of the rects they live in.
     *
     * The mask is still computed and still logged; it is the span set's
     * conservative shadow, and a span is never wider than the bands the
     * mask would have painted (a widget that does not answer contributes
     * its BANDS as its span, deliberately).  So this can only ever
     * transfer a subset of the rows the mask path would have.
     *
     * Under CXC_FORCE_FULL_REPAINT above, mask has been widened to
     * every band; fall through to the mask walk so the measurement
     * baseline stays the pre-damage code path exactly. */
#ifndef CXC_FORCE_FULL_REPAINT
    int nspans = scene_dirty_span_count(scene);
    if (nspans > 0) {
        for (int i = 0; i < nspans; i++) {
            DirtySpan d = scene_dirty_span_at(scene, i);
            if (d.y1 < d.y0) continue;
            canvas.render_rows(draw_scene_cb, scene, d.y0, d.y1, d.bottom_up);
            c.spans++;
            c.rows += canvas.last_rows_painted();
            c.draw_us += canvas.last_draw_us();
            c.xfer_us += canvas.last_xfer_us();
            /* bands == scene passes, which is what draw cost scales
             * with; rows is what transfer cost scales with. */
            c.bands += d.y1 / kDirtyBandHeight - d.y0 / kDirtyBandHeight + 1;
        }
        return c;
    }
    /* No spans but a non-zero mask should be impossible (every mask bit
     * is set beside a span).  Falling through to the mask walk is the
     * safe direction if it ever happens. */
    if (mask)
        ESP_LOGW(TAG, "[CXC-ER-DAMAGE] mask 0x%08x with no spans — "
                 "falling back to the band walk", (unsigned)mask);
#endif
#endif

    int b = 0;
    while (b < 32) {
        if (!(mask & (1u << b))) { b++; continue; }
        int b1 = b;
        while (b1 + 1 < 32 && (mask & (1u << (b1 + 1)))) b1++;
        canvas.render_bands(draw_scene_cb, scene, b, b1);
        c.bands += b1 - b + 1;
        c.spans++;
        c.rows += canvas.last_rows_painted();
        c.draw_us += canvas.last_draw_us();
        c.xfer_us += canvas.last_xfer_us();
        b = b1 + 1;
    }
    return c;
}

/* ------------------------------------------------------------------ *
 * STALL DETECTOR ([CXC-ER-STALL])
 *
 * "Sometimes it would freeze for no correlatable reason and then catch
 * up."  The standing suspicion was the 60 s config re-poll, which does
 * run on THIS task and does block it.  That is a hypothesis, and the
 * evidence for it was a 22 s console gap that could equally have been
 * an idle board with nothing to log — a gap between log lines is not a
 * stall, it is an absence of lines.
 *
 * So: time every phase of the loop and let the board name its own
 * longest one.  The threshold is deliberately below the poll burst
 * (100 ms on a static scene) so a phase that exceeds its own budget
 * says so, with the wall clock at which it happened.
 *
 * This is instrumentation, not a fix.  It stays compiled in — it costs
 * five esp_timer reads per loop and it is the only thing that can tell
 * a stall from an idle board.
 * ------------------------------------------------------------------ */
static constexpr int64_t kStallWarnUs = 250000;   /* 250 ms */

struct LoopPhases {
    int64_t poll_us = 0;
    int64_t drain_us = 0;
    int64_t repaint_us = 0;
    int64_t config_us = 0;
    int64_t total_us = 0;
};

static void report_stall(const LoopPhases& p, int arrivals) {
    int64_t worst = p.poll_us;
    const char* who = "touch-poll-burst";
    if (p.drain_us > worst)   { worst = p.drain_us;   who = "bus-drain(+logging)"; }
    if (p.repaint_us > worst) { worst = p.repaint_us; who = "repaint"; }
    if (p.config_us > worst)  { worst = p.config_us;  who = "config-poll(HTTP)"; }
    ESP_LOGW(TAG, "[STALL] loop %.0f ms — worst phase %s %.0f ms "
             "(poll %.0f / drain %.0f / repaint %.0f / config %.0f), "
             "%d arrival(s), heap %lu",
             p.total_us / 1000.0f, who, worst / 1000.0f,
             p.poll_us / 1000.0f, p.drain_us / 1000.0f,
             p.repaint_us / 1000.0f, p.config_us / 1000.0f,
             arrivals, (unsigned long)esp_get_free_heap_size());
}

/* ------------------------------------------------------------------ */
/* Damage self-test ([CXC-ER-DAMAGE])                                  */
/*                                                                     */
/* Drives a matrix of value transitions through the PRODUCTION path —  */
/* scene_on_bus_value -> reactive core -> binding -> damage span ->    */
/* repaint — and after each one runs the row-CRC audit, which is       */
/* independent of every step above it.                                 */
/*                                                                     */
/* The transitions are scripted rather than driven from the bus for    */
/* one reason: the bus here is CAN in LISTEN-ONLY mode, so this node    */
/* cannot ask for a value.  A human working the physical levers        */
/* sweeps the range but cannot be asked for 9->10 on demand.  The      */
/* script covers the boundaries a lever sweep would only hit by luck.  */
/* ------------------------------------------------------------------ */
#ifdef CXC_DAMAGE_SELFTEST
struct DamageStep {
    const char* label;
    const char* ns_a;   double val_a;
    const char* ns_b;   double val_b;   /* nullptr ns_b == single arrival */
};

static void run_damage_selftest(ESP32Canvas& canvas, Scene* scene) {
    static const char* kL = "throttle/left";
    static const char* kR = "throttle/right";
    static const DamageStep kSteps[] = {
        /* Seed both channels so the first real transition has a known
         * predecessor (a scene at its `default:` has never rendered a
         * number, and "---" -> "0" is itself a case worth covering). */
        {"seed L=0",             kL,   0, nullptr, 0},
        {"seed R=0",             kR,   0, nullptr, 0},
        /* Single-step, mid-segment: must relight NOTHING */
        {"R 0->1 (no segment)",  kR,   1, nullptr, 0},
        {"R 1->2 (no segment)",  kR,   2, nullptr, 0},
        /* Segment boundaries: 255/10 == 25.5 per segment */
        {"R 2->26 (seg 1 on)",   kR,  26, nullptr, 0},
        {"R 26->25 (seg 1 off)", kR,  25, nullptr, 0},
        {"R 25->51 (seg 2 on)",  kR,  51, nullptr, 0},
        /* Digit-count changes on the numeral */
        {"R 51->9",              kR,   9, nullptr, 0},
        {"R 9->10 (1->2 dig)",   kR,  10, nullptr, 0},
        {"R 10->99",             kR,  99, nullptr, 0},
        {"R 99->100 (2->3 dig)", kR, 100, nullptr, 0},
        {"R 100->99 (3->2 dig)", kR,  99, nullptr, 0},
        {"R 99->9 (2->1 dig)",   kR,   9, nullptr, 0},
        /* The AHEAD FLANK threshold at 230 (an expr binding on two
         * widgets that are hidden/shown, over the footer) */
        {"R 9->229 (below)",     kR, 229, nullptr, 0},
        {"R 229->230 (FLANK)",   kR, 230, nullptr, 0},
        {"R 230->231",           kR, 231, nullptr, 0},
        {"R 231->229 (clear)",   kR, 229, nullptr, 0},
        /* The defect as measured: one segment at the top of the bar */
        {"R 229->245",           kR, 245, nullptr, 0},
        {"R 245->255 (1 seg)",   kR, 255, nullptr, 0},
        {"R 255->245 (1 seg)",   kR, 245, nullptr, 0},
        /* Full-range slams */
        {"R 245->0",             kR,   0, nullptr, 0},
        {"R 0->255",             kR, 255, nullptr, 0},
        {"R 255->0",             kR,   0, nullptr, 0},
        /* The LEFT channel, to prove the spans are per-widget */
        {"L 0->128",             kL, 128, nullptr, 0},
        {"L 128->255",           kL, 255, nullptr, 0},
        /* BOTH channels in one drain — two widgets, two numerals, and
         * the flank expr, all merged into one span set */
        {"L+R both -> 64",       kL,  64, kR, 64},
        {"L+R both -> 250",      kL, 250, kR, 250},
        {"L+R both -> 0",        kL,   0, kR, 0},
        {"L=255 R=0 (split)",    kL, 255, kR, 0},
        {"L=0 R=255 (split)",    kL,   0, kR, 255},
    };

    ESP_LOGW(TAG, "[DAMAGE] self-test: %d transitions, row-CRC audited",
             (int)(sizeof(kSteps) / sizeof(kSteps[0])));

    int failures = 0;
    int total_rows = 0;
    double t = esp_timer_get_time() / 1000000.0;

    for (const DamageStep& st : kSteps) {
        t += 0.05;
        scene_clear_dirty_spans(scene);
        uint32_t mask = scene_on_bus_value(scene, st.ns_a, 0, st.val_a,
                                           false, nullptr, t);
        if (st.ns_b)
            mask |= scene_on_bus_value(scene, st.ns_b, 0, st.val_b,
                                       false, nullptr, t);
        if (scene_touch_dirty(scene)) scene_register_touch(scene);

        RepaintCost c = repaint_dirty_bands(canvas, scene, mask);
        total_rows += c.rows;

        int first_bad = -1;
        int bad = canvas.audit_rows(draw_scene_cb, scene, &first_bad);
        if (bad < 0) {
            ESP_LOGE(TAG, "[DAMAGE] audit UNAVAILABLE — self-test proves "
                     "nothing.  Aborting.");
            return;
        }
        if (bad == 0) {
            ESP_LOGI(TAG, "[DAMAGE] PASS  %-24s %d span(s) %3d rows",
                     st.label, c.spans, c.rows);
        } else {
            failures++;
            ESP_LOGE(TAG, "[DAMAGE] FAIL  %-24s %d span(s) %3d rows — "
                     "%d STALE ROW(S), first at y=%d",
                     st.label, c.spans, c.rows, bad, first_bad);
        }
        /* The audit re-rendered the frame into the band buffer without
         * transferring, so the buffer is scratch — the next repaint
         * re-renders whatever rows it touches.  Nothing to restore. */
    }

    if (failures == 0)
        ESP_LOGW(TAG, "[DAMAGE] self-test PASSED — %d transitions, 0 stale "
                 "rows, %d rows transferred total (a full repaint of each "
                 "would have been %d)",
                 (int)(sizeof(kSteps) / sizeof(kSteps[0])), total_rows,
                 (int)(sizeof(kSteps) / sizeof(kSteps[0])) * canvas.height());
    else
        ESP_LOGE(TAG, "[DAMAGE] self-test FAILED — %d/%d transitions left "
                 "stale rows.  The damage spans UNDER-INVALIDATE; do not "
                 "ship this.", failures,
                 (int)(sizeof(kSteps) / sizeof(kSteps[0])));

    /* Leave the panel showing a full, correct frame rather than the
     * last audited state — the human tier looks at this next. */
    canvas.render_frame(draw_scene_cb, scene);
}
#endif /* CXC_DAMAGE_SELFTEST */

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

extern "C" void app_main(void) {
    int64_t t_boot = esp_timer_get_time();

    BoardProfile& board = board_profile();
    PanelGeometry geo = board.geometry();

    ESP_LOGI(TAG, "ComposeIO generic display firmware (M5 config-driven boot)");
    ESP_LOGI(TAG, "Board profile: %s", board.name());
    ESP_LOGI(TAG, "Panel: %dx%d %s", geo.width, geo.height,
             geo.shape == PanelShape::Round ? "round" : "rect");
    ESP_LOGI(TAG, "Device id: %s", device_id());

    /* 1. Display up (board profile seam) */
    board.display().init();

    static ESP32Canvas canvas;
    s_measure_canvas = &canvas;
    canvas.set_display(&board.display());
    canvas.create_surface(geo.width, geo.height);
    if (!canvas.band_buffer()) {
        ESP_LOGE(TAG, "Band buffer allocation failed — aborting");
        return;
    }

    /* 2. Splash (also proves the panel before any network dependency).
     * Decode the compiled-in ComposePlatform logo once; on decode failure
     * the splash falls back to the wordmark. */
    s_logo = canvas.decode_image(compose_logo_splash_cyd,
                                 compose_logo_splash_cyd_len);
    if (!s_logo)
        ESP_LOGW(TAG, "boot logo decode failed — wordmark fallback");
    static char id_line[48];
    snprintf(id_line, sizeof(id_line), "device: %s", device_id());

    /* BUILD TAG ON THE GLASS ([CXC-BRINGUP] acceptance: "a test pattern
     * with an on-screen build tag").  This is the cheapest defence
     * against the canonical failure this project already suffered once —
     * a panel frozen on an old frame while every machine signal reads
     * green.  A stale panel shows a stale tag, and eyes catch instantly
     * what ESP_OK cannot report. */
    static char build_line[48];
    snprintf(build_line, sizeof(build_line), "build: %s %s",
             __DATE__, __TIME__);
    ESP_LOGI(TAG, "Firmware build: %s %s", __DATE__, __TIME__);
    show_splash(canvas, "connecting WiFi...", id_line, build_line);

#ifdef CXC_SPLASH_COLOR_BARS
    /* Hold the labelled baseline long enough to be READ, then carry on
     * automatically.  No input, no second flash, no waiting on anybody: the
     * boundary that is real here is "a human has to look at this", and it is
     * satisfied by putting the baseline and the payload in ONE image, in
     * sequence.  CXC_SPLASH_COLOR_BARS_MS is the dwell in milliseconds; both are set
     * per board in src/CMakeLists.txt and are OFF in a shipping build. */
    for (int i = 0; i < kBaselineBandCount; i++)
        ESP_LOGW(TAG, "[BASELINE] band %d labelled %-5s must render RGB565 "
                 "0x%04X", i, kBaselineBands[i].name,
                 kBaselineBands[i].rgb565);
    ESP_LOGW(TAG, "[BASELINE] holding %d ms.  If the band labelled BLUE looks "
             "GREEN and GREEN looks RED that is a BYTE swap; if BLUE looks RED "
             "and RED looks BLUE that is a CHANNEL swap (MADCTL bit 3).  NO "
             "machine signal on this device can tell you which — a capture "
             "decodes the same buffer with the same assumption that wrote it.",
             (int)CXC_SPLASH_COLOR_BARS_MS);
    vTaskDelay(pdMS_TO_TICKS(CXC_SPLASH_COLOR_BARS_MS));
#endif

    /* 3. WHO NEEDS THE RADIO?  ([SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-INTENT])
     *
     * Both halves of the answer are resolved BEFORE anything is brought up,
     * because the decision costs ten seconds and ~40 KB of heap and cannot be
     * unwound:
     *   - the TRANSPORT: MQTT needs an IP; UART and CAN do not.
     *   - the CONFIG SOURCE: `http` needs an IP; `bus` and `cached` do not.
     * If NEITHER needs it, the radio does not come up at all — that is the
     * address saving the whole UART/CAN exercise exists for, and until now a
     * "UART-slaved" board still took a DHCP lease.
     *
     * The consequence is deliberate and must be stated where it is felt:
     * NO WIFI MEANS NO `GET /capture.bmp`.  On a slaved board the human tier
     * is the ONLY rendering verification there is ([CXC-CONFIG-BUS-OPEN]
     * capture-loss).  Bring a board up on WiFi, ACCEPT IT ON THE GLASS, then
     * convert it. */
    /* [CXC-REPOINT] Resolve the bus BINDING and arm the commit-confirmed revert
     * timer BEFORE the network comes up ([CXC-REPOINT-COMMIT-CONFIRMED]
     * ordering-is-load-bearing) and before the transport reads the active
     * broker URI.  Also reads the two-stage escape-hatch GPIO here.  120 s is
     * [DEC-SKIPPER-COMMIT-CONFIRM-WINDOW-120S]. */
    bus_binding_boot_init(device_id(), 120);

    BusTransport& bus = bus_transport();
    ConfigSource cfg_src = config_source();
    (void)cfg_src;
    /* [CXC-REPOINT-CONFIRM] interaction-a-builder-must-honor: when config is
     * NOT fetched over HTTP, the store plane is never exercised, so a trial
     * confirms on the broker echo ALONE.  Mark the store plane vacuously
     * satisfied (recording the strategy for the disclosing ack, Note 2) so a
     * trial is not doomed to revert under `config: bus/cached`. */
    if (cfg_src != ConfigSource::Http)
        bus_binding_note_store_vacuous(config_source_selected_name());
    bool bus_wants_net = bus.needs_network();
    bool cfg_wants_net = config_source_needs_network();
    bool need_net = bus_wants_net || cfg_wants_net;

    ESP_LOGI(TAG, "Bus transport: %s (from %s) | config source: %s (from %s)",
             bus_transport_selected_name(), bus_transport_selection_source(),
             config_source_selected_name(), config_source_selection_source());
    ESP_LOGW(TAG, "WiFi %s — transport '%s' %s an IP stack, config source "
             "'%s' %s one", need_net ? "REQUIRED" : "NOT REQUIRED",
             bus_transport_selected_name(),
             bus_wants_net ? "needs" : "does NOT need",
             config_source_selected_name(),
             cfg_wants_net ? "needs" : "does NOT need");

    static char ip_line[48];
    if (need_net) {
        wifi_init();
        snprintf(ip_line, sizeof(ip_line), "ip: %s", wifi_get_ip());
        /* [CXC-REPOINT-REVERT] Note 1: if this is a TRIAL boot and WiFi never
         * came up, the revert reason is `no-wifi`, not a bare `timeout`.  Set
         * before the store/broker planes are attempted so the earliest cause
         * wins; a later plane failure would derive its own reason otherwise. */
        if (bus_binding_active_is_trial() && !wifi_is_connected())
            bus_binding_note_reason("no-wifi");
    } else {
        ESP_LOGW(TAG, "RADIO STAYS DOWN: no DHCP lease, no MQTT client slot, "
                 "no HTTP server — and NO /capture.bmp.  Rendering on this "
                 "board can only be confirmed by EYES.");
        snprintf(ip_line, sizeof(ip_line), "no ip (%s)",
                 bus_transport_selected_name());
    }
    show_splash(canvas, "fetching config...", id_line, ip_line);

    /* 3b. Bus client + on-device capture server (R3).  This is the only line
     * in the app shell that has any opinion about the bus at all
     * ([CXC-BOARD-HW-UART-SLAVE], DP-CIO-6). */
    bus.start(device_id());

    /* THE RECEIVER'S STANDING TO JUDGE, wired to the SELECTED transport's real
     * link instrument ([SPEC:CXC-FRESHNESS]).  Once, here, right after the
     * link comes up — it survives scene swaps because impairment is a property
     * of the TRANSPORT, not of the composition.
     *
     * Without it a bus-off board reports the ENTIRE FLEET dead: every
     * namespace times out at once, consistently and confidently, and the
     * uniformity is exactly what makes it invisible. */
    freshness_attach_link_probe();
    if (need_net && capture_http_start(&canvas, device_id())) {
        ESP_LOGI(TAG, "Capture server: http://%s/capture.bmp",
                 wifi_get_ip());
    } else if (!need_net) {
        ESP_LOGW(TAG, "capture server NOT started (no network) — this is the "
                 "expected cost of a slaved board, not a fault");
    }

    /* 3c. Remote-restart control plane ([CXC-ER-REMOTE-RESTART]): install
     * the handler and subscribe the two exact `_device/` restart
     * namespaces persistently (they survive scene swaps).  A truthy
     * command re-runs the boot-fetch and loads the latest config. */
    restart_init(device_id(), bus_publish_state, reboot_now,
                 kRestartBootGuardMs);
    const char* rns[4];
    size_t rn = restart_namespaces(rns, 4);
    for (size_t i = 0; i < rn; i++) {
        bus.subscribe(rns[i]);
        ESP_LOGI(TAG, "[CXC-ER-RESTART-BUS] restart namespace: %s", rns[i]);
    }

    /* Transport control plane ([CXC-BOARD-HW-UART-SLAVE] bootstrap-trap):
     * `_device/<id>/transport` re-selects the substrate for the NEXT boot,
     * over the CURRENT one — the only way to flip a board to UART without
     * a reflash, since the scene YAML that could otherwise declare it
     * ARRIVES over the transport being chosen. */
    bus_transport_control_init(device_id());
    const char* tns[2];
    size_t tn = bus_transport_control_namespaces(tns, 2);
    for (size_t i = 0; i < tn; i++) {
        bus.subscribe(tns[i]);
        ESP_LOGI(TAG, "[CXC-BOARD-HW-UART-SLAVE] transport namespace: %s",
                 tns[i]);
    }

    /* 3c-bis. SELF-ANNOUNCEMENT (workstream 0.2).  A bus that cannot be polled
     * for what is on it is a bus you have to visit with a screwdriver: a 25 s
     * census on 2026-08-12 found ZERO `_device/*` namespaces on the production
     * broker.  From here this board says, periodically and unprompted, what it
     * is, what it is running and whether it is alive — and answers a poll
     * immediately.  See announce_pack.h for the field table. */
    announce_init(device_id(), cxc_build_fingerprint());
    announce_set_network_up(need_net && wifi_get_ip() && wifi_get_ip()[0]);
    const char* ans[2];
    size_t an = announce_poll_namespaces(ans, 2);
    for (size_t i = 0; i < an; i++) {
        bus.subscribe(ans[i]);
        ESP_LOGI(TAG, "[FLEET] announce poll namespace: %s", ans[i]);
    }
    ESP_LOGI(TAG, "[FLEET] build fingerprint %08lx == %s",
             (unsigned long)cxc_build_fingerprint(), cxc_build_stamp());

    /* 3d. Config control plane ([SPEC:CXC-CONFIG-BUS]).  Same bootstrap-trap
     * answer as the transport: the strategy cannot live in the scene YAML
     * because that YAML ARRIVES BY the strategy, so it is settable over the
     * CURRENT link on `_device/<id>/config_source` and stored in NVS. */
    config_source_control_init(device_id(), bus_publish_state);
    const char* cns[2];
    size_t cn = config_source_control_namespaces(cns, 2);
    for (size_t i = 0; i < cn; i++) {
        bus.subscribe(cns[i]);
        ESP_LOGI(TAG, "[CXC-CONFIG-BUS-STRATEGY] config namespace: %s", cns[i]);
    }

    /* 3d-bis. Bus BINDING control plane ([SPEC:CXC-REPOINT]).  The SAME
     * bootstrap-trap answer as config_source, applied to the rendezvous
     * ADDRESS: it is retained STATE on `_device/<id>/bus` (per-device) and
     * `_device/broadcast/bus` (global), settable over the CURRENT link and
     * stored in NVS.  Also subscribe the confirm-echo namespace so the broker
     * round-trip plane ([CXC-REPOINT-CONFIRM]) can land. */
    bus_binding_control_init(device_id(), bus_publish_state);
    const char* bns[2];
    size_t bn = bus_binding_control_namespaces(bns, 2);
    for (size_t i = 0; i < bn; i++) {
        bus.subscribe(bns[i]);
        ESP_LOGI(TAG, "[CXC-REPOINT] bus binding namespace: %s", bns[i]);
    }
    if (bus_binding_confirm_namespace()[0])
        bus.subscribe(bus_binding_confirm_namespace());

    /* 3e. Config OVER THE BUS: subscribe `_device/<id>/config` persistently
     * and fire the first CONFIG_REQUEST.  Under any other strategy this is
     * inert — config_bus only acts on frames for a namespace nothing else
     * pushes, and its timer is never armed. */
    config_bus_init(device_id(), bus_publish_state, announce_on_glass);
    if (config_source() == ConfigSource::Bus) {
        const char* fns[2];
        size_t fn = config_bus_namespaces(fns, 2);
        for (size_t i = 0; i < fn; i++) {
            bus.subscribe(fns[i]);
            ESP_LOGI(TAG, "[CXC-CONFIG-BUS-CHUNKS] config transfer namespace: "
                     "%s", fns[i]);
        }
        config_bus_request();
    }

    /* 3f. RECLAIM THE BOOT LOGO BEFORE THE LARGEST ALLOCATION OF BOOT.
     *
     * The decoded logo is W*H*2 bytes — 51,200 B for the 160 px hero — and it
     * used to be freed AFTER the scene was built.  On a board with headroom
     * that is invisible.  On a no-PSRAM board it is not: config fetch
     * (config length) and the YAML parse arena (len*2 + 8192 + len/8*24, so
     * ~44 KB for an 8 KB scene) are the two largest transient allocations in
     * the whole boot, and holding 51 KB of decoded PNG across both of them is
     * what turns a comfortable margin into `yaml arena malloc failed`.
     *
     * MEASURED ON THIS BOARD 2026-08-12, not inferred: an 8,139-byte scene
     * failed to parse on every retry, forever, with the logo resident; the
     * SAME scene parses with it freed.  The heap numbers on either side of
     * this free are logged below precisely so the next reader can check
     * whether the trade is still worth making on their board.
     *
     * WHAT IT COSTS, stated plainly: the logo has already been drawn — twice —
     * and the panel holds those pixels; freeing the DECODE BUFFER does not
     * erase the glass.  The only visible consequence is that a RETRY splash
     * (config unavailable) falls back to the ComposeUX wordmark instead of the
     * logo.  On a healthy board nobody ever sees that frame, and on an
     * unhealthy one a visibly different splash is arguably the honest signal. */
    ESP_LOGI(TAG, "Free heap before config fetch: %lu bytes (logo decode "
             "buffer still held)", (unsigned long)esp_get_free_heap_size());
    if (s_logo) {
        canvas.free_image(s_logo);
        s_logo = nullptr;
        ESP_LOGI(TAG, "boot logo decode buffer released — free heap now %lu "
                 "bytes.  The YAML parse arena is the largest single malloc "
                 "of boot and this is the headroom it gets.",
                 (unsigned long)esp_get_free_heap_size());
    }

    /* 4-5. Config -> scene (retry until we have one; the device is
     * useless without its composition, so keep the operator informed) */
    Scene* scene = nullptr;
    while (!scene) {
        uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);
        config_bus_tick(now_ms);
        /* ANNOUNCE FROM INSIDE THE RETRY LOOP TOO.  A board that never gets a
         * scene is precisely the board somebody needs to find from the bus,
         * and it is the one that would otherwise never reach the event loop —
         * so it would be invisible exactly when it is broken.  It announces
         * `scene = 0000`, which is the fault report. */
        drain_control_plane();
        announce_tick(now_ms);
        bus_binding_tick(now_ms);   /* [CXC-REPOINT] announce which bus + drive confirm echo */
        scene = try_fetch_scene(canvas, geo, true);
        if (!scene) {
            /* THE ANNOUNCE, ON THE GLASS.  Under `config: bus` with the radio
             * off there is no console for a remote operator and no capture
             * endpoint; the panel is the only thing that can report. */
            show_splash(canvas,
                        s_announce_line[0] ? "config unavailable"
                                           : "waiting for config",
                        s_announce_line[0] ? s_announce_line
                                           : config_source_selected_name(),
                        id_line);
            vTaskDelay(pdMS_TO_TICKS(config_source() == ConfigSource::Bus
                                         ? 250 : 5000));
        }
    }

    /* Already reclaimed at 3f, before the fetch/parse allocations rather
     * than after them.  Kept as a guarded no-op so no future path that
     * re-decodes the logo can leak it. */
    if (s_logo) { canvas.free_image(s_logo); s_logo = nullptr; }

    /* 6. First render + touch registration + reactive wiring */
    scene_register_touch(scene);
    wire_scene(scene);
    canvas.render_frame(draw_scene_cb, scene);

    /* The full-frame cost, measured on THIS board with THIS scene — the
     * reference every dirty repaint is judged against (and, on the
     * full-frame tier, exactly what an arrival used to cost before the
     * dirty row span existed). */
    ESP_LOGI(TAG, "[PERF] full frame: %d rows, draw %.1f ms + xfer %.1f ms "
             "= %.1f ms", canvas.last_rows_painted(),
             canvas.last_draw_us() / 1000.0f,
             canvas.last_xfer_us() / 1000.0f,
             (canvas.last_draw_us() + canvas.last_xfer_us()) / 1000.0f);

    int64_t t_first_render = esp_timer_get_time();
    ESP_LOGI(TAG, "[CXC-R-C9] boot -> first config-driven frame: %lld ms "
             "(scene '%s', %d widgets)",
             (long long)((t_first_render - t_boot) / 1000),
             scene_title(scene), scene_widget_count(scene));
    ESP_LOGI(TAG, "Free heap after scene build: %lu bytes",
             (unsigned long)esp_get_free_heap_size());

    /* ([TF7]) The dev-only SD push-capture step is GONE.  The device
     * needs no SD card: it boots straight into the scene.  Remote
     * preview/verification is served by the on-device PULL server
     * (capture_http.cpp, GET /capture.bmp) — no SD, streams bands over
     * HTTP.  Removing the SD/VSPI bus also makes the shared-DMA bug
     * class structurally impossible (one SPI bus, no channel sharing). */

#ifdef CXC_DAMAGE_SELFTEST
    /* Before the loop, before any bus traffic: a known-state matrix
     * through the production repaint path, each step audited. */
    run_damage_selftest(canvas, scene);
#endif

    /* Touch AFTER display init + renders — the ILI9341 init sequence
     * corrupts the shared SPI bus state for touch (board quirk; the
     * profile still owns the driver). */
    TouchDriver* touch = board.touch();
    if (touch) {
        touch->init();
        ESP_LOGI(TAG, "Touch ready — [CXC-R-C9] responds from frame 1");
    }

    /* 8. Event loop.
     *
     * R3 render policy: animated scenes keep the M5 continuous
     * full-frame cadence (sprites need it); static scenes are
     * EVENT-DRIVEN — a bus arrival repaints only the dirty band(s)
     * ([CXC-ER-RENDER] widget-level dirty), a dispatched tap repaints
     * the full frame (a tap may mutate state anywhere), and an idle
     * loop paints nothing. */
    PerfWindow perf;
    int64_t last_poll_us = esp_timer_get_time();
    int64_t last_arrival_us = 0;      /* 0 == the bus has never spoken */
    unsigned last_tap = tap_serial();

    while (true) {
        /* The render lock serializes this whole body against the
         * capture server's pull-render (shared band buffer + SPI). */
        capture_http_lock();
        LoopPhases phase;
        int64_t t_loop = esp_timer_get_time();
        int64_t t_phase = t_loop;

        /* Touch poll burst (render blocks the loop; catch short taps).
         * Animated scenes use a short burst so the sprite rate stays
         * watchable; static scenes favor tap capture.
         *
         * A QUEUED BUS ARRIVAL CUTS THE BURST SHORT.  The burst is 100 ms
         * on a static scene, and it used to run to completion no matter
         * what was waiting — so an arrival's worst case was 100 ms of
         * touch polling BEFORE the repaint even started, which dwarfed
         * the repaint itself.  One 10 ms tick still always elapses (the
         * idle task must run and touch must be sampled at least once per
         * pass), so the loop can never spin. */
        int polls = scene_has_animation(scene) ? 2 : 10;
        for (int poll = 0; poll < polls; poll++) {
            if (touch) process_touch(touch->read());
            vTaskDelay(pdMS_TO_TICKS(10));
#ifndef CXC_LEGACY_POLL_BURST      /* measurement baseline only */
            if (bus.pending()) break;
#endif
        }

        phase.poll_us = esp_timer_get_time() - t_phase;
        t_phase = esp_timer_get_time();

        /* Reactive drain: bus arrivals -> core -> widget attrs. */
        int arrivals = 0;
        int64_t queued_us = bus.pending_since_us();
        uint32_t dirty = drain_bus(scene, &arrivals);
        if (scene_touch_dirty(scene))
            scene_register_touch(scene);   /* visibility flipped */

        phase.drain_us = esp_timer_get_time() - t_phase;
        if (arrivals > 0) last_arrival_us = esp_timer_get_time();
        t_phase = esp_timer_get_time();

        bool tapped = tap_serial() != last_tap;
        last_tap = tap_serial();

        if (scene_has_animation(scene)) {
            SpriteWidget::tick();     /* latch playhead once per pass */
            int64_t t0 = esp_timer_get_time();
            canvas.render_frame(draw_scene_cb, scene);
            perf.add(esp_timer_get_time() - t0);

            if (perf.frames >= 30) {
                float avg_ms = perf.sum_us / 1000.0f / perf.frames;
                ESP_LOGI(TAG, "[PERF] scene '%s' %dw: render avg %.1f ms "
                         "(min %.1f / max %.1f) = %.2f fps raster "
                         "(%.1f ms/band over 20 bands); loop fps %.2f; heap %lu",
                         scene_title(scene), scene_widget_count(scene),
                         avg_ms, perf.min_us / 1000.0f, perf.max_us / 1000.0f,
                         1000.0f / avg_ms, avg_ms / 20.0f,
                         1000.0f / (avg_ms + 100.0f),
                         (unsigned long)esp_get_free_heap_size());
                perf.reset();
            }
        } else if (dirty) {
            int64_t t0 = esp_timer_get_time();
            RepaintCost c = repaint_dirty_bands(canvas, scene, dirty);
            int total = (canvas.height() + kDirtyBandHeight - 1) /
                        kDirtyBandHeight;
            int64_t done = esp_timer_get_time();
            ESP_LOGI(TAG, "[REACT] %d arrival(s) -> %d span(s), %d/%d bands "
                     "= %d/%d rows in %.1f ms (draw %.1f + xfer %.1f); "
                     "bus->glass %.1f ms",
                     arrivals, c.spans, c.bands, total, c.rows, canvas.height(),
                     (done - t0) / 1000.0f,
                     c.draw_us / 1000.0f, c.xfer_us / 1000.0f,
                     queued_us ? (done - queued_us) / 1000.0f : -1.0f);
        } else if (tapped) {
            canvas.render_frame(draw_scene_cb, scene);
        }

        phase.repaint_us = esp_timer_get_time() - t_phase;

        capture_http_unlock();

        /* SELF-ANNOUNCEMENT, outside the render lock.  It publishes at most
         * five small messages and only when a deadline or a change says so,
         * so it is nearly always a handful of comparisons.  Outside the lock
         * because it must never make the capture server wait on a bus write,
         * and after the repaint because the operator's frame outranks the
         * fleet's census. */
        announce_tick((uint32_t)(esp_timer_get_time() / 1000));
        bus_binding_tick((uint32_t)(esp_timer_get_time() / 1000));

        /* Config re-poll ([CXC-R-C10]).  Fetch + build happen OUTSIDE
         * the render lock (network seconds must not block the capture
         * server); the swap itself takes it.
         *
         * UNDER `config: bus` THERE IS NOTHING TO POLL ([CXC-CONFIG-BUS-PUSH]):
         * the head PUSHES when bus_storage changes, and boot-time pull is the
         * degenerate case of that rather than a separate mechanism.  So this
         * checks every pass — a completed transfer is already sitting in
         * config_bus, reassembled on the rx task at wire speed — and the
         * 60-second timer only gates the HTTP strategy, which has to ask. */
        int64_t t_config = esp_timer_get_time();
        int64_t now = t_config;
        bool over_bus = config_source() == ConfigSource::Bus;
        if (over_bus) config_bus_tick((uint32_t)(now / 1000));
        bool due = over_bus ||
                   (now - last_poll_us) > kConfigPollSec * 1000000LL;

        /* DEFER a due poll while the bus is live ([CXC-ER-STALL]).  The
         * deferral is bounded: past kMaxDeferSec the poll wins, because
         * a board that never re-reads its config is a worse failure
         * than a 200 ms hitch. */
        if (due && !over_bus && last_arrival_us != 0 &&
            (now - last_arrival_us) < kBusQuietBeforePollSec * 1000000LL &&
            (now - last_poll_us) < kMaxDeferSec * 1000000LL) {
            due = false;
        }

        if (due) {
            if (!over_bus) last_poll_us = now;
            int64_t t_fetch = esp_timer_get_time();
            Scene* fresh = try_fetch_scene(canvas, geo, false);
            int64_t fetch_us = esp_timer_get_time() - t_fetch;
            /* ⚠ ALWAYS LOGGED, changed or not.  This runs ON THE RENDER
             * TASK and blocks the bus drain for its whole duration —
             * an HTTP GET with the network in a bad mood is the one
             * unbounded thing in this loop ([CXC-ER-STALL]). */
            ESP_LOGI(TAG, "[CONFIG] poll took %.0f ms (%s)",
                     fetch_us / 1000.0f,
                     fresh ? "CHANGED — rebuilding scene" : "unchanged");
            if (fresh) {
                ESP_LOGI(TAG, "Config changed — swapping scene "
                         "'%s' -> '%s'", scene_title(scene), scene_title(fresh));
                capture_http_lock();
                scene_free(canvas, scene);
                scene = fresh;
                scene_register_touch(scene);
                wire_scene(scene);
                canvas.render_frame(draw_scene_cb, scene);
                capture_http_unlock();
            }
        }

        phase.config_us = esp_timer_get_time() - t_config;
        phase.total_us = esp_timer_get_time() - t_loop;
        if (phase.total_us > kStallWarnUs) report_stall(phase, arrivals);
    }
}

#endif /* !DSL_CORPUS_TEST */
