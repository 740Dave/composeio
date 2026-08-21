/*
 * capture_http.cpp — on-device pull capture server.  See capture_http.h.
 *
 * BMP geometry matches capture.cpp ([CYD-CAPTURE-BMP]): 24-bit BGR,
 * negative height = top-down, so scanlines stream in band order with
 * zero buffering beyond one 720-byte row.
 */

#include "capture_http.h"

#include <cstdio>
#include <cstring>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char* TAG = "capture_http";

static ESP32Canvas* s_canvas = nullptr;
static ESP32Canvas::DrawCallback s_draw = nullptr;
static void* s_draw_user = nullptr;
static char s_title[64] = "(no scene)";
static char s_device[32] = "cyd";
static SemaphoreHandle_t s_lock = nullptr;
static httpd_handle_t s_server = nullptr;

void capture_http_lock(void) {
    if (s_lock) xSemaphoreTake(s_lock, portMAX_DELAY);
}

void capture_http_unlock(void) {
    if (s_lock) xSemaphoreGive(s_lock);
}

void capture_http_set_scene(ESP32Canvas::DrawCallback draw_fn,
                            void* user_data, const char* title) {
    s_draw = draw_fn;
    s_draw_user = user_data;
    snprintf(s_title, sizeof(s_title), "%s", title ? title : "(untitled)");
}

/* ------------------------------------------------------------------ */
/* BMP header (24-bit, top-down — capture.cpp geometry)                */
/* ------------------------------------------------------------------ */

static void write_le16(uint8_t* p, uint16_t v) {
    p[0] = v & 0xFF;
    p[1] = (v >> 8) & 0xFF;
}

static void write_le32(uint8_t* p, int32_t v) {
    uint32_t u = (uint32_t)v;
    p[0] = u & 0xFF;
    p[1] = (u >> 8) & 0xFF;
    p[2] = (u >> 16) & 0xFF;
    p[3] = (u >> 24) & 0xFF;
}

/* THE WIDEST PANEL IN THE FLEET, and a bound that must be re-asked at N+1.
 *
 * This was `240` — sized exactly for the panels that existed when it was
 * written, which is the [kMaxPersist] failure shape: a limit that is correct
 * on the day and one short the moment the fleet grows.  The 480-wide ST7796S
 * board arrived and every /capture.bmp on it would have 500'd through the
 * guard below.
 *
 * 480 * 3 = 1,440 B.  It is FILE-STATIC rather than on the stack because the
 * httpd task's stack is not sized for it, and that is safe here for a reason
 * that must stay true: the capture handler holds capture_http_lock() for the
 * whole render+stream, so exactly one caller is ever inside stream_rows.
 *
 * If a wider panel is ever added, this constant and the PanelGeometry it
 * shadows must move together — the guard below turns the mismatch into a
 * loud failure rather than a buffer overrun, but a loud failure is still a
 * broken endpoint. */
static constexpr int kMaxCaptureWidth = 480;

static void build_bmp_header(uint8_t hdr[54], int w, int h) {
    memset(hdr, 0, 54);
    int row = w * 3;                       /* 480*3 = 1440, 4-byte aligned */
    int pixels = row * h;
    hdr[0] = 'B'; hdr[1] = 'M';
    write_le32(hdr + 2, 54 + pixels);
    write_le32(hdr + 10, 54);
    write_le32(hdr + 14, 40);
    write_le32(hdr + 18, w);
    write_le32(hdr + 22, -h);              /* top-down */
    write_le16(hdr + 26, 1);
    write_le16(hdr + 28, 24);
    write_le32(hdr + 34, pixels);
}

/* ------------------------------------------------------------------ */
/* GET /capture.bmp — band re-render streamed into the response        */
/* ------------------------------------------------------------------ */

struct StreamCtx {
    ESP32Canvas::DrawCallback draw;
    void* user;
    httpd_req_t* req;
    bool error;
};

/* Stream rows [y0, y1] of the buffer (buffer row 0 == scene row
 * buf_y0) as BMP scanlines. */
static void stream_rows(StreamCtx* ctx, ESP32Canvas* c, int buf_y0,
                        int y0, int y1) {
    const uint16_t* band = c->band_buffer();
    int w = c->width();

    /* Serialised by capture_http_lock() — see kMaxCaptureWidth. */
    static uint8_t row_buf[kMaxCaptureWidth * 3];
    if (w * 3 > (int)sizeof(row_buf)) {
        ESP_LOGE(TAG, "panel is %d px wide but the capture row buffer is "
                 "sized for %d — raise kMaxCaptureWidth", w,
                 kMaxCaptureWidth);
        ctx->error = true;
        return;
    }

    for (int y = y0; y <= y1; y++) {
        const uint16_t* src = band + (size_t)(y - buf_y0) * w;
        uint8_t* dst = row_buf;
        for (int x = 0; x < w; x++) {
            uint16_t px = (uint16_t)((src[x] >> 8) | (src[x] << 8));
            uint8_t r = (px >> 11) & 0x1F;
            uint8_t g = (px >> 5) & 0x3F;
            uint8_t b = px & 0x1F;
            *dst++ = (uint8_t)((b << 3) | (b >> 2));
            *dst++ = (uint8_t)((g << 2) | (g >> 4));
            *dst++ = (uint8_t)((r << 3) | (r >> 2));
        }
        if (httpd_resp_send_chunk(ctx->req, (const char*)row_buf,
                                  w * 3) != ESP_OK) {
            ctx->error = true;
            return;
        }
    }
}

static void stream_band_cb(Canvas& canvas, void* vctx) {
    auto* ctx = (StreamCtx*)vctx;
    if (ctx->error) return;

    /* Populate the band buffer with the real scene. */
    ctx->draw(canvas, ctx->user);

    /* Convert this band's rows RGB565-BE -> BGR888 and stream them. */
    auto* c = (ESP32Canvas*)&canvas;
    int band_y = c->band_y_offset();
    int y_end = band_y + kBandHeight - 1;
    if (y_end > c->height() - 1) y_end = c->height() - 1;
    stream_rows(ctx, c, band_y, band_y, y_end);
}

static esp_err_t capture_get(httpd_req_t* req) {
    if (!s_canvas || !s_draw) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "no scene yet");
        return ESP_FAIL;
    }

    capture_http_lock();
    ESP_LOGI(TAG, "GET /capture.bmp (scene '%s')", s_title);

    httpd_resp_set_type(req, "image/bmp");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");

    uint8_t hdr[54];
    build_bmp_header(hdr, s_canvas->width(), s_canvas->height());

    StreamCtx ctx{s_draw, s_draw_user, req, false};
    if (httpd_resp_send_chunk(req, (const char*)hdr, sizeof(hdr)) != ESP_OK)
        ctx.error = true;

    if (!ctx.error) {
        if (s_canvas->full_frame()) {
            /* FRAME TIER — the buffer already holds exactly the pixels
             * last SENT to the panel, including every sub-frame dirty
             * repaint.  Stream it AS-IS: no re-render, no SPI traffic,
             * nothing perturbed.
             *
             * This is not just cheaper, it is more HONEST: a capture
             * that re-renders reports what the scene WOULD look like,
             * which would paper over exactly the stale-region bug a
             * dirty repaint can introduce.  (It still proves only the
             * framebuffer — never the glass.) */
            stream_rows(&ctx, s_canvas, 0, 0, s_canvas->height() - 1);
        } else {
            /* BAND TIER — the buffer holds 16 rows, so the frame has to
             * be re-rendered band by band to be streamed.  The pass also
             * refreshes the panel, same as capture.cpp. */
            s_canvas->render_frame(stream_band_cb, &ctx);
        }
    }

    esp_err_t rc = ESP_OK;
    if (ctx.error) {
        ESP_LOGW(TAG, "capture stream aborted (client gone?)");
        rc = ESP_FAIL;                     /* closes the socket */
    } else {
        httpd_resp_send_chunk(req, nullptr, 0);
        ESP_LOGI(TAG, "capture streamed");
    }
    capture_http_unlock();
    return rc;
}

/* ------------------------------------------------------------------ */
/* GET / — one-line status                                             */
/* ------------------------------------------------------------------ */

static esp_err_t status_get(httpd_req_t* req) {
    char line[192];
    snprintf(line, sizeof(line),
             "%s scene='%s' heap=%lu capture=/capture.bmp\n",
             s_device, s_title,
             (unsigned long)esp_get_free_heap_size());
    httpd_resp_set_type(req, "text/plain");
    httpd_resp_send(req, line, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

#ifdef CXC_DAMAGE_AUDIT
/* ------------------------------------------------------------------ *
 * GET /damage_audit — run the row-CRC damage audit on demand
 *
 * ⚠ THIS, NOT /capture.bmp, IS THE INSTRUMENT FOR STALE PIXELS.  On the
 * band tier /capture.bmp RE-RENDERS the scene to stream it, so an
 * incremental frame and a forced-full frame decode the same fresh
 * render and agree whether or not a row on the panel is stale — it is
 * self-confirming for this class of bug and cannot be used for it.
 *
 * This endpoint compares a fresh full render against a CRC of what was
 * last SENT to the panel, per scanline.  A non-zero count is a row the
 * panel is holding that a full repaint would draw differently.
 *
 * It costs one full-frame rasterisation (~230 ms on this board) under
 * the render lock and transfers NOTHING, so it perturbs timing but not
 * the glass.  On-demand only — do not poll it.
 *
 * It still cannot see below the driver handoff (a dropped transfer, a
 * bad address window, a dead panel), and it assumes every transfer is
 * full-width.
 * ------------------------------------------------------------------ */
static esp_err_t damage_audit_get(httpd_req_t* req) {
    if (!s_canvas || !s_draw) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "no scene yet");
        return ESP_FAIL;
    }
    capture_http_lock();
    int first_bad = -1;
    int64_t t0 = esp_timer_get_time();
    int bad = s_canvas->audit_rows(s_draw, s_draw_user, &first_bad);
    int64_t us = esp_timer_get_time() - t0;
    capture_http_unlock();

    char line[256];
    if (bad < 0) {
        snprintf(line, sizeof(line),
                 "UNAVAILABLE  (frame tier, or the CRC table failed to "
                 "allocate)\n");
        ESP_LOGW("capture_http", "[DAMAGE] audit unavailable");
    } else {
        snprintf(line, sizeof(line),
                 "%s  stale_rows=%d first_bad_row=%d of %d  took=%.0fms\n"
                 "scene='%s'\n"
                 "note: proves the pixels SENT, never that they arrived.\n",
                 bad == 0 ? "CLEAN" : "STALE", bad, first_bad,
                 s_canvas->height(), us / 1000.0f, s_title);
        if (bad == 0)
            ESP_LOGI("capture_http", "[DAMAGE] audit CLEAN (%.0f ms)",
                     us / 1000.0f);
        else
            ESP_LOGE("capture_http", "[DAMAGE] audit STALE — %d row(s), "
                     "first at y=%d", bad, first_bad);
    }
    httpd_resp_set_type(req, "text/plain");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_send(req, line, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}
#endif /* CXC_DAMAGE_AUDIT */

/* ------------------------------------------------------------------ */
/* Server                                                              */
/* ------------------------------------------------------------------ */

bool capture_http_start(ESP32Canvas* canvas, const char* device_id) {
    if (s_server) return true;
    s_canvas = canvas;
    if (device_id) snprintf(s_device, sizeof(s_device), "%s", device_id);
    if (!s_lock) s_lock = xSemaphoreCreateMutex();

    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 80;
    cfg.max_open_sockets = 2;              /* heap-lean: one puller */
    cfg.stack_size = 8192;                 /* widget draw calls run here */
    cfg.lru_purge_enable = true;

    if (httpd_start(&s_server, &cfg) != ESP_OK) {
        ESP_LOGE(TAG, "httpd_start failed");
        s_server = nullptr;
        return false;
    }

    static const httpd_uri_t capture_uri = {
        "/capture.bmp", HTTP_GET, capture_get, nullptr
#ifdef CONFIG_HTTPD_WS_SUPPORT
        , false, false, nullptr
#endif
    };
    static const httpd_uri_t status_uri = {
        "/", HTTP_GET, status_get, nullptr
#ifdef CONFIG_HTTPD_WS_SUPPORT
        , false, false, nullptr
#endif
    };
    httpd_register_uri_handler(s_server, &capture_uri);
    httpd_register_uri_handler(s_server, &status_uri);
#ifdef CXC_DAMAGE_AUDIT
    static const httpd_uri_t audit_uri = {
        "/damage_audit", HTTP_GET, damage_audit_get, nullptr
#ifdef CONFIG_HTTPD_WS_SUPPORT
        , false, false, nullptr
#endif
    };
    httpd_register_uri_handler(s_server, &audit_uri);
    ESP_LOGW(TAG, "[CXC-ER-DAMAGE] audit ARMED: GET /damage_audit "
             "(/capture.bmp re-renders on this tier and CANNOT see a "
             "stale row)");
#endif
    ESP_LOGI(TAG, "capture server up: GET /capture.bmp (port 80)");
    return true;
}
