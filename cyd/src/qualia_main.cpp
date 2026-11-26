/*
 * qualia_main.cpp — Generic ComposeIO REACTIVE display firmware for the
 * FRAMEBUFFER tier (Qualia ESP32-S3 RGB666 + NV3052C 720x720 round).
 * [SPEC:CXC-EMBED-REACTIVE] R5 — [CXC-ER-RENDER], [CXC-ER-C5], [CXC-ER-C6]
 *
 * The framebuffer sibling of main.cpp (the CYD band tier).  Same boot
 * contract ([CXC-R-BOOT] as-built subset), same R3 reactive stack —
 * compose_scene + the R1 ReactiveCore (by reference) + the bus
 * transport seam (bus_transport.h) + the
 * pull capture server — with the display architecture swapped:
 *
 *   - Compositing happens in a 720x720 PSRAM framebuffer (QualiaCanvas)
 *     the LCD_CAM RGB peripheral scans continuously; render_bands
 *     restricts redraw+flush to the dirty rows ([CXC-ER-C5]).
 *   - Live transforms are real ([CXC-ER-C6]) — the DRADIS sweep widget
 *     rotates through canvas.rotate() every frame, and rotation/scale
 *     are bindable widget attributes on this tier.
 *   - PANEL BRING-UP: a bare flash shows the ComposePlatform LOGO
 *     (baked CI16, show_splash) with the device id/IP — its coloured
 *     hex-cube proves colours/geometry/orientation — and the device
 *     idles on it until a config exists in bus_storage.  (The HSV
 *     colour-wheel test frame, canvas.draw_test_wheel(), remains
 *     available for hardware bring-up.)
 *   - No touch (Adafruit 5793 has no touchscreen).
 *
 * Boot: panel init (NV3052C over PCA9554 + esp_lcd RGB) -> logo splash
 * -> WiFi -> bus client + capture server -> config fetch (non-blocking:
 * the splash idles) -> scene -> event loop (animated scenes render
 * continuously; static scenes repaint dirty rows per bus arrival;
 * config re-poll every 60 s).
 */

#if defined(QUALIA_DISPLAY) && !defined(DSL_CORPUS_TEST)

#include "announce.h"
#include "board_profile.h"
#include "canvas_qualia.h"
#include "compose_scene.h"
#include "freshness.h"
#include "config_chunk.h"      /* config_content_hash() — the scene fingerprint */
#include "config_fetch.h"
#include "asset_reload_bus.h"    /* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] */
#include "capture_http_fb.h"
#include "bus_transport.h"
#include "config_bus.h"
#include "config_source.h"
#include "restart.h"
#include "bus_binding.h"
#include "widgets/button.h"
#include "widgets/sprite.h"
#include "widgets/sweep.h"
#include "widgets/dradis_contacts.h"
#include "wifi.h"

/* ComposePlatform boot logo, baked CI16 into a compiled-in array
 * (renders before any network dependency — cannot be bus-fetched).
 * Regenerate with python/scripts/bake_logo_splash.py. */
#include "gen/compose_logo_splash_qualia.h"

#include <cstdlib>
#include <cstring>
#include <cstdio>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_system.h"

static const char* TAG = "qualia_main";

static constexpr int64_t kConfigPollSec = 60;
static constexpr int64_t kLoadPublishUs = 1000000;  /* CPU-load gauge cadence */

/* Boot-loop guard window ([CXC-ER-RESTART-BUS]). */
static constexpr uint32_t kRestartBootGuardMs = 3000;

/* Reboot seam ([CXC-ER-REMOTE-RESTART]): flush the pre-restart ack + WiFi
 * TX briefly, then hard-reset (re-runs the boot-fetch). */
static void reboot_now(void) {
    ESP_LOGW(TAG, "[CXC-ER-REMOTE-RESTART] restart command honored — "
             "esp_restart() (re-runs boot-fetch)");
    vTaskDelay(pdMS_TO_TICKS(250));
    esp_restart();
}

/* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] reload seam + backstop cadence — the
 * shared header-based asset-change consumer, wired identically to main.cpp
 * (the device never computes sha256; held is the server's GET-header hash). */
static void asset_reload_reboot(void* /*user*/, const char* reason) {
    ESP_LOGW(TAG, "[CXC-ACN] %s -> reload (esp_restart -> boot fetch)", reason);
    reboot_now();
}
static const uint32_t kAssetBackstopMs = 300000;   /* 5 min */

/* ------------------------------------------------------------------ */
/* Device self-telemetry: true CPU load (FreeRTOS run-time stats).     */
/* Averaged across BOTH cores over the interval since the last call —  */
/* published to the bus so a gauge can meter the chip's own workload.  */
/* Requires CONFIG_FREERTOS_GENERATE_RUN_TIME_STATS + USE_TRACE_       */
/* FACILITY (sdkconfig.qualia*).                                        */
/* ------------------------------------------------------------------ */
static bool cpu_load_pct(float* out) {
    UBaseType_t n = uxTaskGetNumberOfTasks();
    TaskStatus_t* st = (TaskStatus_t*)malloc(sizeof(TaskStatus_t) * (n + 4));
    if (!st) return false;
    uint32_t total = 0;
    UBaseType_t got = uxTaskGetSystemState(st, n + 4, &total);
    /* Per-core idle: the SMP idle tasks are named "IDLE0"/"IDLE1". */
    uint64_t idle[2] = {0, 0};
    for (UBaseType_t i = 0; i < got; i++) {
        const char* nm = st[i].pcTaskName;
        if (nm && strncmp(nm, "IDLE", 4) == 0) {
            int core = (nm[4] == '1') ? 1 : 0;
            idle[core] += st[i].ulRunTimeCounter;
        }
    }
    free(st);

    static uint32_t last_total = 0;
    static uint64_t last_idle[2] = {0, 0};
    uint32_t dt = total - last_total;              /* wall time (stats clock) */
    last_total = total;
    if (dt == 0) return false;

    /* Load = busiest core.  A single-threaded full-frame render pins one
     * core (~98%) while the other idles; the peak is the honest "is the
     * chip maxed" signal, and falls into the green as rendering cheapens. */
    float peak = 0.0f;
    for (int c = 0; c < 2; c++) {
        uint64_t di = idle[c] - last_idle[c];
        last_idle[c] = idle[c];
        float busy = 100.0f * (1.0f - (float)di / (float)dt);
        if (busy < 0.0f) busy = 0.0f;
        if (busy > 100.0f) busy = 100.0f;
        if (busy > peak) peak = busy;
    }
    *out = peak;
    return true;
}

/* ------------------------------------------------------------------ */
/* Boot frame: ComposePlatform logo + identity captions                */
/* ------------------------------------------------------------------ */

/* Decoded once at boot; nullptr until decode_image() runs. */
static ImageHandle s_logo = nullptr;

static void show_splash(QualiaCanvas& canvas, const char* line1,
                        const char* line2) {
    Size sz = canvas.get_surface_size();
    float cx = sz.width / 2.0f, cy = sz.height / 2.0f;

    /* Baked bg matches the fill so the square logo tile blends into the
     * dark face; the coloured hex-cube proves colours/geometry/
     * orientation exactly as the old bring-up wheel did. */
    canvas.set_fill_color(0x10, 0x12, 0x1C);
    canvas.fill_rect(0, 0, sz.width, sz.height);

    if (s_logo) {
        float s = (float)COMPOSE_LOGO_QUALIA_W;
        float ly = cy - 28.0f;      /* lift so captions sit below, un-clipped */
        canvas.draw_image(s_logo, cx - s / 2.0f, ly - s / 2.0f, s, s);
        float cap = ly + s / 2.0f + 26.0f;
        canvas.set_fill_color(0xDD, 0xDD, 0xEE);
        canvas.set_font("sans-serif", 18, "regular");
        if (line1) canvas.draw_text(line1, cx, cap, "center");
        canvas.set_fill_color(0xAA, 0xAA, 0xBB);
        canvas.set_font("sans-serif", 15, "regular");
        if (line2) canvas.draw_text(line2, cx, cap + 26.0f, "center");
    } else {
        /* Fallback wordmark (logo decode failed). */
        canvas.set_fill_color(0x26, 0xA6, 0x9A);
        canvas.set_font("sans-serif", 24, "bold");
        canvas.draw_text("ComposeUX", cx, cy - 10, "center");
        canvas.set_fill_color(0xDD, 0xDD, 0xEE);
        canvas.set_font("sans-serif", 15, "regular");
        if (line1) canvas.draw_text(line1, cx, cy + 20, "center");
        canvas.set_fill_color(0xCC, 0xCC, 0xDD);
        if (line2) canvas.draw_text(line2, cx, cy + 44, "center");
    }

    canvas.present();
}

/* ------------------------------------------------------------------ */
/* Scene plumbing (R3 idioms)                                          */
/* ------------------------------------------------------------------ */

static void draw_scene_cb(Canvas& canvas, void* ud) {
    scene_draw(canvas, static_cast<Scene*>(ud));
}

/* Beam-only pass of the 3-buffer radar path (render_animated). */
static void draw_sweeps_cb(Canvas& canvas, void* ud) {
    scene_draw_sweeps(canvas, static_cast<Scene*>(ud));
}

/* (Re)build the background snapshot — the whole scene with sweeps in
 * GridOnly — for the row-bands set in *mask* (0xFFFFFFFF = full).
 * Composites into the canvas's background buffer only; the next
 * animated frame's restore memcpy carries it to the glass. */
static void rebuild_background(QualiaCanvas& canvas, Scene* scene,
                               uint32_t mask) {
    SweepWidget::set_render_phase(SweepRenderPhase::GridOnly);
    int b = 0;
    while (b < 32) {
        if (!(mask & (1u << b))) { b++; continue; }
        int b1 = b;
        while (b1 + 1 < 32 && (mask & (1u << (b1 + 1)))) b1++;
        canvas.render_background_bands(draw_scene_cb, scene, b, b1);
        b = b1 + 1;
    }
    SweepWidget::set_render_phase(SweepRenderPhase::Full);
}

/* Publish over the SELECTED transport (see main.cpp's twin) — one thunk
 * for ButtonWidget's hook and restart.cpp's injected function pointer. */
static void bus_publish_state(const char* ns, const char* value) {
    bus_transport().publish_state(ns, value);
}

/* Declared ABOVE wire_scene, which reads them for the scene content hash
 * (main.cpp has the same pair in the opposite order and so did not care). */
static char* s_config_text = nullptr;
static size_t s_config_len = 0;

static void wire_scene(Scene* scene) {
    /* THE FRESHNESS CONTRACT, RE-DECLARED FOR THIS SCENE — the twin of
     * main.cpp's ([SPEC:CXC-FRESHNESS]).  Reset first: a scene swap replaces
     * the whole declaration set, and carrying a namespace forward from a scene
     * that no longer binds it would leave the board publishing a verdict about
     * something nothing is watching. */
    freshness().reset();
    int contradictions = 0;
    const int declared = freshness_declare_from_yaml(
        freshness(), scene_yaml_root(scene), &contradictions);
    if (freshness().refused() || contradictions)
        ESP_LOGE(TAG, "freshness: %lu declaration(s) REFUSED and %d "
                 "contradictory - those namespaces are watched by NOTHING "
                 "(capacity %d)", (unsigned long)freshness().refused(),
                 contradictions, kMaxFreshnessEntries);
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

    /* Scene namespaces PLUS every declared node's heartbeat - on a framed
     * substrate a beat from an undeclared node cannot be NAMED on arrival
     * (ns-hash matching, and a CRC-16 does not invert). */
    static const char* subs[24];
    size_t n = scene_subscription_count(scene);
    if (n > 16) n = 16;
    for (size_t i = 0; i < n; i++) subs[i] = scene_subscription_at(scene, i);
    const char* hb[8];
    const size_t hbn = freshness().heartbeat_namespaces(hb, 8);
    for (size_t i = 0; i < hbn && n < 24; i++) subs[n++] = hb[i];
    bus_transport().set_subscriptions(subs, n);
    capture_http_fb_set_title(scene_title(scene));
    ButtonWidget::set_publish_fn(bus_publish_state);
    /* What is actually on the glass, as a 16-bit content hash — the twin
     * of main.cpp's, and the SAME hash config_bus ACKs a transfer with. */
    announce_set_scene_hash(
        (s_config_text && s_config_len)
            ? config_content_hash((const uint8_t*)s_config_text, s_config_len)
            : 0);
    announce_set_config_stale(config_is_stale());
    ESP_LOGI(TAG, "[CXC-ER-RENDER] scene '%s' wired: %d bindings, "
             "%u bus subscriptions", scene_title(scene),
             scene_binding_count(scene), (unsigned)n);
}


/* The leaf's own voice when it has no scene ([CXC-CONFIG-BUS-LEAF]) —
 * see main.cpp's twin. */
static char s_announce_line[160] = "";
static void announce_on_glass(const char* line) {
    snprintf(s_announce_line, sizeof(s_announce_line), "%s", line ? line : "");
}

/* WHERE THE SCENE COMES FROM ([CXC-CONFIG-BUS-STRATEGY]) — the twin of
 * main.cpp's config_obtain(); the two tiers must agree, because a strategy
 * that behaved differently on the framebuffer tier would be a per-board
 * config path, which is exactly what this seam removes. */
static char* config_obtain(size_t* out_len) {
    if (out_len) *out_len = 0;
    switch (config_source()) {
        case ConfigSource::Http: {
            size_t len = 0;
            char* text = config_fetch(&len);
            if (text) {
                config_cache_store(text, len);
                config_set_stale(false);
                bus_binding_note_store_ok();   /* [CXC-REPOINT-CONFIRM] store plane */
                /* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] hold the server's
                 * GET-header content-hash; SHOUT while it is not yet served. */
                if (!asset_reload_bus_note_held(config_fetch_last_content_hash()))
                    ESP_LOGW(TAG, "[CXC-ACN] no server content-hash on GET — "
                             "asset-change self-select INERT until served; "
                             "restart floor still recovers");
                if (out_len) *out_len = len;
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
            if (loaded) return nullptr;
            loaded = true;
            size_t len = 0;
            char* text = config_cache_load(&len);
            if (!text) {
                ESP_LOGE(TAG, "`config: cached` selected but NOTHING is "
                         "cached — this board has no scene and no way to get "
                         "one; set _device/<id>/config_source.");
                announce_on_glass("NO CACHED CONFIG — set config_source");
                return nullptr;
            }
            config_set_stale(true);
            if (out_len) *out_len = len;
            return text;
        }
        case ConfigSource::Unknown:
            announce_on_glass("UNKNOWN config_source — see console");
            return nullptr;
    }
    return nullptr;
}

static Scene* try_fetch_scene(QualiaCanvas& canvas, const PanelGeometry& geo,
                              bool force) {
    size_t len = 0;
    char* text = config_obtain(&len);
    if (!text) return nullptr;
    if (!force && s_config_text && len == s_config_len &&
        memcmp(text, s_config_text, len) == 0) {
        free(text);
        return nullptr;
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

/* THE FRESHNESS SINK - one verdict, through the IDENTICAL entry point a real
 * arrival uses.  No branch on the verdict, no widget call, no draw call:
 * every consumer reacts through the declarative binding it already had
 * ([SPEC:CXC-FRESHNESS], David's ruling). */
struct FreshSinkCtx { Scene* scene; uint32_t dirty; };

static void freshness_scene_sink(void* user, const char* ns, int kind,
                                 double num, bool b, const char* str) {
    FreshSinkCtx* c = (FreshSinkCtx*)user;
    if (!c->scene) return;
    const double t = esp_timer_get_time() / 1000000.0;
    c->dirty |= scene_on_bus_value(c->scene, ns, kind, num, b, str, t);
}

static uint32_t drain_bus(Scene* scene, int* arrivals) {
    BusEvent ev;
    uint32_t dirty = 0;
    while (bus_transport().poll(&ev)) {
        double t = esp_timer_get_time() / 1000000.0;
        const uint32_t now_ms = (uint32_t)(esp_timer_get_time() / 1000);

        /* THE FRESHNESS RECEIVER SEES EVERY ARRIVAL FIRST, before the
         * control-plane sieve: a peer whose value a later stage rejects is
         * still a peer that is ALIVE ([CXC-FRESH-HEARTBEAT-RX] ordering). */
        if (ev.unknown) {
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
        /* Fleet-discovery poll — same shape, same reason. */
        if (announce_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        if (config_source_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b,
                                       ev.str))
            continue;
        /* Bus-binding control plane ([CXC-REPOINT]) — retained address state. */
        if (bus_binding_on_bus_value(ev.ns, (int)ev.kind, ev.num, ev.b, ev.str))
            continue;
        /* Peer heartbeats - eaten HERE, one stage before the scene, so a beat
         * carries no value into the reactive core and repaints nothing. */
        if (freshness_consume_heartbeat(ev.ns, (int)ev.kind, ev.num, ev.str,
                                        now_ms))
            continue;
        /* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] asset-change-notify event —
         * self-select by held hash, reload-via-restart if ours changed. */
        if (asset_reload_bus_consume(ev.ns, (int)ev.kind, ev.str,
                                     asset_reload_reboot, nullptr))
            continue;
        if (scene)
            dirty |= scene_on_bus_value(scene, ev.ns, (int)ev.kind, ev.num,
                                        ev.b, ev.str, t);
        (*arrivals)++;
    }

    /* THE FRESHNESS TICK - a READ on a loop the firmware already runs, inside
     * the drain so its DIRTY MASK reaches this pass's repaint.  Dropping that
     * mask would leave the glass on the pre-verdict frame until some unrelated
     * arrival happened to repaint it: a staleness bug inside the staleness
     * fix. */
    {
        FreshSinkCtx fc{scene, 0};
        freshness_tick((uint32_t)(esp_timer_get_time() / 1000),
                       freshness_scene_sink, &fc);
        dirty |= fc.dirty;
    }
    return dirty;
}

static int repaint_dirty_bands(QualiaCanvas& canvas, Scene* scene,
                               uint32_t mask) {
    int painted = 0;
    int b = 0;
    while (b < 32) {
        if (!(mask & (1u << b))) { b++; continue; }
        int b1 = b;
        while (b1 + 1 < 32 && (mask & (1u << (b1 + 1)))) b1++;
        canvas.render_bands(draw_scene_cb, scene, b, b1);
        painted += b1 - b + 1;
        b = b1 + 1;
    }
    return painted;
}

/* ------------------------------------------------------------------ */
/* Main                                                                */
/* ------------------------------------------------------------------ */

extern "C" void app_main(void) {
    int64_t t_boot = esp_timer_get_time();

    BoardProfile& board = board_profile();
    PanelGeometry geo = board.geometry();

    ESP_LOGI(TAG, "ComposeIO generic display firmware (R5 framebuffer tier)");
    ESP_LOGI(TAG, "Board profile: %s", board.name());
    ESP_LOGI(TAG, "Panel: %dx%d %s", geo.width, geo.height,
             geo.shape == PanelShape::Round ? "round" : "rect");
    ESP_LOGI(TAG, "Device id: %s", device_id());

    /* 1. Panel up (NV3052C init + LCD_CAM RGB scan-out). */
    board.display().init();

    static QualiaCanvas canvas;
    canvas.set_display(&board.display());
    canvas.set_shape(geo.shape);
    canvas.create_surface(geo.width, geo.height);
    if (!canvas.framebuffer()) {
        ESP_LOGE(TAG, "PSRAM framebuffer allocation failed — aborting");
        return;
    }

    /* 2. BOOT SPLASH frame: the ComposePlatform logo proves the panel
     * before any network dependency (its coloured hex-cube exercises
     * colours / geometry / orientation).  Decode the compiled-in logo
     * once; on failure the splash falls back to the wordmark. */
    s_logo = canvas.decode_image(compose_logo_splash_qualia,
                                 compose_logo_splash_qualia_len);
    if (!s_logo)
        ESP_LOGW(TAG, "boot logo decode failed — wordmark fallback");
    static char id_line[48];
    snprintf(id_line, sizeof(id_line), "device: %s", device_id());
    show_splash(canvas, id_line, "connecting WiFi...");

    /* 3. WHO NEEDS THE RADIO?  ([SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-INTENT])
     * Both halves resolved BEFORE anything comes up — see main.cpp for the
     * full reasoning.  If neither the transport nor the config source needs an
     * IP stack, the radio stays down, and with it goes /capture.bmp: on a
     * slaved board the human tier is the ONLY rendering verification. */
    /* [CXC-REPOINT] resolve the binding + arm the revert timer + read the
     * escape hatch BEFORE the network and before the transport reads the broker
     * URI (ordering-is-load-bearing).  120 s = [DEC-BUILDHOST-COMMIT-CONFIRM-
     * WINDOW-120S]. */
    bus_binding_boot_init(device_id(), 120);
    if (config_source() != ConfigSource::Http)
        bus_binding_note_store_ok();   /* store plane not exercised -> echo alone */

    BusTransport& bus = bus_transport();
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
    } else {
        ESP_LOGW(TAG, "RADIO STAYS DOWN: no DHCP lease, no HTTP server — and "
                 "NO /capture.bmp.  Rendering can only be confirmed by EYES.");
        snprintf(ip_line, sizeof(ip_line), "no ip (%s)",
                 bus_transport_selected_name());
    }
    show_splash(canvas, id_line, ip_line);

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
    if (need_net && capture_http_fb_start(&canvas, device_id())) {
        ESP_LOGI(TAG, "Capture server: http://%s/capture.bmp", wifi_get_ip());
    } else if (!need_net) {
        ESP_LOGW(TAG, "capture server NOT started (no network) — the expected "
                 "cost of a slaved board, not a fault");
    }
    capture_http_fb_set_title("boot-splash (no config)");

    /* Remote-restart control plane ([CXC-ER-REMOTE-RESTART]): install the
     * handler and subscribe the two exact `_device/` restart namespaces
     * persistently.  A truthy command re-runs the boot-fetch — the path
     * that stranded qualia-004df8 (no OTA, not USB-tethered) now reloads
     * live.  The boot-guard is time-based so a config-LESS boot (idling
     * on the splash) still honors a remote restart. */
    restart_init(device_id(), bus_publish_state, reboot_now,
                 kRestartBootGuardMs);
    const char* rns[4];
    size_t rn = restart_namespaces(rns, 4);
    for (size_t i = 0; i < rn; i++) {
        bus.subscribe(rns[i]);
        ESP_LOGI(TAG, "[CXC-ER-RESTART-BUS] restart namespace: %s", rns[i]);
    }

    /* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] arm the consumer (clears held)
     * BEFORE the boot fetch set held, then subscribe the retained
     * asset-change-notify (composeio/+/bus_storage/asset/changed). */
    asset_reload_bus_init();
    bus.subscribe(asset_reload_bus_namespace());
    ESP_LOGI(TAG, "[CXC-ACN] asset-change namespace: %s",
             asset_reload_bus_namespace());

    /* Transport control plane ([CXC-BOARD-HW-UART-SLAVE] bootstrap-trap) —
     * see main.cpp. */
    bus_transport_control_init(device_id());
    const char* tns[2];
    size_t tn = bus_transport_control_namespaces(tns, 2);
    for (size_t i = 0; i < tn; i++) {
        bus.subscribe(tns[i]);
        ESP_LOGI(TAG, "[CXC-BOARD-HW-UART-SLAVE] transport namespace: %s",
                 tns[i]);
    }

    /* SELF-ANNOUNCEMENT (workstream 0.2) — the twin of main.cpp's.  This
     * board in particular is why it exists: qualia-004df8 has no OTA and is
     * not USB-tethered, so "which binary is it running" has been an
     * unanswerable question about it for a day. */
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

    /* Config control plane + config OVER THE BUS ([SPEC:CXC-CONFIG-BUS]) —
     * the twin of main.cpp's 3d/3e. */
    config_source_control_init(device_id(), bus_publish_state);
    const char* cns[2];
    size_t cn = config_source_control_namespaces(cns, 2);
    for (size_t i = 0; i < cn; i++) {
        bus.subscribe(cns[i]);
        ESP_LOGI(TAG, "[CXC-CONFIG-BUS-STRATEGY] config namespace: %s", cns[i]);
    }

    /* Bus BINDING control plane ([SPEC:CXC-REPOINT]) — retained address STATE,
     * the twin of the config-source strategy plane. */
    bus_binding_control_init(device_id(), bus_publish_state);
    const char* bns[2];
    size_t bn = bus_binding_control_namespaces(bns, 2);
    for (size_t i = 0; i < bn; i++) {
        bus.subscribe(bns[i]);
        ESP_LOGI(TAG, "[CXC-REPOINT] bus binding namespace: %s", bns[i]);
    }
    if (bus_binding_confirm_namespace()[0])
        bus.subscribe(bus_binding_confirm_namespace());

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

    /* 4-5. Config -> scene, NON-blocking: without a config the device
     * idles on the boot splash (a bare flash is still meaningful). */
    Scene* scene = try_fetch_scene(canvas, geo, true);
    if (scene) {
        wire_scene(scene);
        SweepWidget::tick();
        SpriteWidget::tick();
        DradisContactsWidget::tick();
        canvas.render_frame(draw_scene_cb, scene);
        int64_t t_first = esp_timer_get_time();
        ESP_LOGI(TAG, "[CXC-R-C9] boot -> first config-driven frame: "
                 "%lld ms (scene '%s', %d widgets)",
                 (long long)((t_first - t_boot) / 1000),
                 scene_title(scene), scene_widget_count(scene));
    } else {
        ESP_LOGW(TAG, "No config yet — idling on the boot splash "
                 "(re-polling every %lld s)", (long long)kConfigPollSec);
    }
    ESP_LOGI(TAG, "Free heap: %lu, free PSRAM: %lu",
             (unsigned long)esp_get_free_heap_size(),
             (unsigned long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    /* 6. Event loop.  Render policy ([CXC-ER-RENDER], framebuffer):
     *    - animated scene (sweep/sprite): continuous full-frame
     *      composite at the frame budget below;
     *    - static scene: EVENT-DRIVEN — a bus arrival recomposites and
     *      flushes only the dirty rows ([CXC-ER-C5]); idle paints
     *      nothing;
     *    - no scene: the wheel just sits in the framebuffer. */
    int64_t last_poll_us = esp_timer_get_time();
    int64_t last_load_us = esp_timer_get_time();
    int64_t perf_sum_us = 0;
    int perf_frames = 0;
    /* 3-buffer radar path: background snapshot validity.  False forces
     * a full rebuild (scene load/swap); a reactive dirty mask rebuilds
     * just the dirty bands. */
    bool bg_valid = false;

    while (true) {
        /* [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD] periodic backstop, OUTSIDE the
         * capture lock — re-GET config and compare the server's fresh hash to
         * held (HTTP source only; device never hashes).  See main.cpp. */
        {
            static uint32_t s_backstopNext = 0;
            static bool s_backstopArmed = false;
            const uint32_t nowb = (uint32_t)(esp_timer_get_time() / 1000);
            if (!s_backstopArmed) {
                s_backstopNext = nowb + kAssetBackstopMs;
                s_backstopArmed = true;
            } else if (scene && config_source() == ConfigSource::Http &&
                       asset_reload_bus_held()[0] &&
                       asset_backstop_due(nowb, s_backstopNext)) {
                s_backstopNext = nowb + kAssetBackstopMs;
                size_t blen = 0;
                char* bt = config_fetch(&blen);
                if (bt) {
                    asset_reload_bus_backstop("timer",
                                              config_fetch_last_content_hash(),
                                              asset_reload_reboot, nullptr);
                    free(bt);
                }
            }
        }

        capture_http_fb_lock();

        int arrivals = 0;
        uint32_t dirty = drain_bus(scene, &arrivals);

        /* Device self-telemetry: sample true CPU load ~1/s and feed it in
         * as a local bus value on sys/cpu.  We inject into the store
         * DIRECTLY (not via the broker) because our own loop guard drops
         * echoes of our own src — the broker publish below is purely for
         * external observers.  The R5 demo gauge binds sys/cpu, so it
         * meters the chip's own workload: pinned in the danger zone while
         * the sweep composites full-frame, falling as rendering gets
         * cheaper. */
        int64_t t_load = esp_timer_get_time();
        if (scene && (t_load - last_load_us) > kLoadPublishUs) {
            last_load_us = t_load;
            float load;
            if (cpu_load_pct(&load)) {
                dirty |= scene_on_bus_value(scene, "sys/cpu",
                             (int)BusEvent::Kind::Number, load, false, "",
                             t_load / 1000000.0);
                bus_transport().publish_number("sys/cpu", load);
                ESP_LOGI(TAG, "[LOAD] sys/cpu = %.0f%%", load);
            }
        }

        if (scene && scene_has_animation(scene)) {
            SweepWidget::tick();
            SpriteWidget::tick();
            DradisContactsWidget::tick();
            int64_t t0 = esp_timer_get_time();
            if (canvas.zero_copy() && scene_sweep_only_animation(scene)) {
                /* 3-BUFFER RADAR PATH: background snapshot is rebuilt
                 * only on scene load or a reactive dirty mask; each
                 * frame is restore-memcpy + beam-only draw + pointer
                 * swap ([SPLIT2] in canvas_qualia measures the split). */
                if (!bg_valid) {
                    rebuild_background(canvas, scene, 0xFFFFFFFFu);
                    bg_valid = true;
                } else if (dirty) {
                    rebuild_background(canvas, scene, dirty);
                }
                SweepWidget::set_render_phase(SweepRenderPhase::BeamOnly);
                canvas.render_animated(draw_sweeps_cb, scene);
                SweepWidget::set_render_phase(SweepRenderPhase::Full);
            } else {
                /* Sprite (full-repaint) animation, or no swap seam:
                 * classic continuous full composite. */
                canvas.render_frame(draw_scene_cb, scene);
            }
            int64_t dt = esp_timer_get_time() - t0;
            perf_sum_us += dt;
            if (++perf_frames >= 60) {
                float avg_ms = perf_sum_us / 1000.0f / perf_frames;
                ESP_LOGI(TAG, "[PERF] scene '%s' %dw: composite+flush avg "
                         "%.1f ms = %.1f fps; heap %lu psram %lu",
                         scene_title(scene), scene_widget_count(scene),
                         avg_ms, 1000.0f / avg_ms,
                         (unsigned long)esp_get_free_heap_size(),
                         (unsigned long)heap_caps_get_free_size(
                             MALLOC_CAP_SPIRAM));
                perf_sum_us = 0;
                perf_frames = 0;
            }
        } else if (scene && dirty) {
            int64_t t0 = esp_timer_get_time();
            int painted = repaint_dirty_bands(canvas, scene, dirty);
            int total = (canvas.height() + kBandHeight - 1) / kBandHeight;
            ESP_LOGI(TAG, "[REACT] %d arrival(s) -> repainted %d/%d row-"
                     "bands in %.1f ms (full frame would be %d)",
                     arrivals, painted, total,
                     (esp_timer_get_time() - t0) / 1000.0f, total);
        }

        capture_http_fb_unlock();

        /* Self-announcement, outside the render lock — see main.cpp. */
        announce_tick((uint32_t)(esp_timer_get_time() / 1000));
        bus_binding_tick((uint32_t)(esp_timer_get_time() / 1000));

        /* Frame pacing: animated scenes target the frame budget; static
         * scenes poll the bus queue gently. */
        vTaskDelay(pdMS_TO_TICKS(
            (scene && scene_has_animation(scene)) ? 10 : 50));

        /* Config re-poll ([CXC-R-C10]) — fetch outside the render lock.
         * Under `config: bus` there is nothing to poll ([CXC-CONFIG-BUS-PUSH]);
         * the head pushes and the reassembled scene is already waiting. */
        int64_t now = esp_timer_get_time();
        bool over_bus = config_source() == ConfigSource::Bus;
        if (over_bus) config_bus_tick((uint32_t)(now / 1000));
        if (over_bus || (now - last_poll_us) > kConfigPollSec * 1000000LL) {
            if (!over_bus) last_poll_us = now;
            Scene* fresh = try_fetch_scene(canvas, geo, false);
            if (fresh) {
                ESP_LOGI(TAG, "Config %s — swapping scene '%s' -> '%s'",
                         scene ? "changed" : "arrived",
                         scene ? scene_title(scene) : "(wheel)",
                         scene_title(fresh));
                capture_http_fb_lock();
                if (scene) scene_free(canvas, scene);
                scene = fresh;
                bg_valid = false;      /* new scene -> full bg rebuild */
                wire_scene(scene);
                SweepWidget::tick();
                SpriteWidget::tick();
                DradisContactsWidget::tick();
                canvas.render_frame(draw_scene_cb, scene);
                capture_http_fb_unlock();
            }
        }
    }
}

#endif /* QUALIA_DISPLAY && !DSL_CORPUS_TEST */
