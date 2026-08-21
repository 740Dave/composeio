/*
 * capture_http_fb.cpp — framebuffer pull capture server.
 * See capture_http_fb.h.  BMP geometry matches the R3 capture
 * ([CYD-CAPTURE-BMP]): 24-bit BGR, negative height = top-down, so the
 * framebuffer streams row-by-row with one row of buffering.
 */

#include "capture_http_fb.h"

#include <cstdio>
#include <cstring>

#include "esp_heap_caps.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char* TAG = "capture_fb";

static QualiaCanvas* s_canvas = nullptr;
static char s_title[64] = "(boot)";
static char s_device[32] = "qualia";
static SemaphoreHandle_t s_lock = nullptr;
static httpd_handle_t s_server = nullptr;

void capture_http_fb_lock(void) {
    if (s_lock) xSemaphoreTake(s_lock, portMAX_DELAY);
}

void capture_http_fb_unlock(void) {
    if (s_lock) xSemaphoreGive(s_lock);
}

void capture_http_fb_set_title(const char* title) {
    snprintf(s_title, sizeof(s_title), "%s", title ? title : "(untitled)");
}

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

static void build_bmp_header(uint8_t hdr[54], int w, int h) {
    memset(hdr, 0, 54);
    int row = w * 3;                       /* 720*3 = 2160, 4-byte aligned */
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

static esp_err_t capture_get(httpd_req_t* req) {
    if (!s_canvas || !s_canvas->framebuffer()) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR,
                            "no framebuffer yet");
        return ESP_FAIL;
    }

    capture_http_fb_lock();
    ESP_LOGI(TAG, "GET /capture.bmp (scene '%s')", s_title);

    httpd_resp_set_type(req, "image/bmp");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");

    int w = s_canvas->width();
    int h = s_canvas->height();
    const uint16_t* fb = s_canvas->framebuffer();

    uint8_t hdr[54];
    build_bmp_header(hdr, w, h);

    bool error = false;
    if (httpd_resp_send_chunk(req, (const char*)hdr, sizeof(hdr)) != ESP_OK)
        error = true;

    /* One row of BGR888 at a time — 2160 bytes on a 720-wide panel.
     * Heap, not stack: the httpd task stack is shared with logging. */
    static uint8_t row_buf[720 * 3];
    if (w * 3 > (int)sizeof(row_buf)) error = true;

    for (int y = 0; y < h && !error; y++) {
        const uint16_t* src = fb + (size_t)y * w;
        uint8_t* dst = row_buf;
        for (int x = 0; x < w; x++) {
            uint16_t px = src[x];          /* NATIVE-endian RGB565 */
            uint8_t r = (px >> 11) & 0x1F;
            uint8_t g = (px >> 5) & 0x3F;
            uint8_t b = px & 0x1F;
            *dst++ = (uint8_t)((b << 3) | (b >> 2));
            *dst++ = (uint8_t)((g << 2) | (g >> 4));
            *dst++ = (uint8_t)((r << 3) | (r >> 2));
        }
        if (httpd_resp_send_chunk(req, (const char*)row_buf, w * 3) != ESP_OK)
            error = true;
    }

    esp_err_t rc = ESP_OK;
    if (error) {
        ESP_LOGW(TAG, "capture stream aborted (client gone?)");
        rc = ESP_FAIL;
    } else {
        httpd_resp_send_chunk(req, nullptr, 0);
        ESP_LOGI(TAG, "capture streamed (%dx%d)", w, h);
    }
    capture_http_fb_unlock();
    return rc;
}

static esp_err_t status_get(httpd_req_t* req) {
    char line[192];
    snprintf(line, sizeof(line),
             "%s scene='%s' heap=%lu psram=%lu capture=/capture.bmp\n",
             s_device, s_title,
             (unsigned long)esp_get_free_heap_size(),
             (unsigned long)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    httpd_resp_set_type(req, "text/plain");
    httpd_resp_send(req, line, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

bool capture_http_fb_start(QualiaCanvas* canvas, const char* device_id) {
    if (s_server) return true;
    s_canvas = canvas;
    if (device_id) snprintf(s_device, sizeof(s_device), "%s", device_id);
    if (!s_lock) s_lock = xSemaphoreCreateMutex();

    httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
    cfg.server_port = 80;
    cfg.max_open_sockets = 2;
    cfg.stack_size = 8192;
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
    ESP_LOGI(TAG, "capture server up: GET /capture.bmp (port 80)");
    return true;
}
