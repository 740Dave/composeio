/*
 * capture_push.cpp — Push framebuffer captures to document_store
 *
 * Captures the current framebuffer as a BMP and POSTs it to the
 * document_store endpoint configured in wifi_config.h.
 *
 * BMP is built scanline-by-scanline: one 720-byte row buffer on stack,
 * no full-image buffering. The HTTP client streams the body via a
 * chunked POST or Content-Length with repeated writes.
 *
 * Since esp_http_client doesn't support streaming POST body easily,
 * we buffer the full BMP in PSRAM... but CYD has no PSRAM. So we
 * use a simpler approach: build the BMP in chunks and POST with
 * Content-Length pre-calculated.
 */

#include "capture_server.h"
#include "wifi_config.h"
#include "wifi.h"
#include "bus_binding.h"   /* [CXC-REPOINT] active-tier docstore URL, not the #define */

#include <cstring>
#include <cstdio>
#include <ctime>
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char* TAG = "capture";

/* Framebuffer reference */
static uint16_t* s_fb = nullptr;
static int s_width = 0;
static int s_height = 0;

void capture_init(uint16_t* framebuffer, int width, int height) {
    s_fb = framebuffer;
    s_width = width;
    s_height = height;
    ESP_LOGI(TAG, "Capture initialized: %dx%d framebuffer", width, height);
}

/* ------------------------------------------------------------------ */
/* RGB565 BE → RGB888 conversion                                       */
/* ------------------------------------------------------------------ */

static inline void rgb565be_to_rgb888(uint16_t pixel, uint8_t& r, uint8_t& g, uint8_t& b) {
    uint16_t c = (pixel >> 8) | (pixel << 8);  /* swap to native endian */
    r = (c >> 8) & 0xF8; r |= r >> 5;
    g = (c >> 3) & 0xFC; g |= g >> 6;
    b = (c << 3) & 0xF8; b |= b >> 5;
}

/* ------------------------------------------------------------------ */
/* BMP construction + HTTP POST                                        */
/* ------------------------------------------------------------------ */

/* BMP row stride: 3 bytes per pixel, padded to 4-byte boundary */
static int bmp_row_stride(int width) {
    int raw = width * 3;
    return (raw + 3) & ~3;
}

int capture_and_push(void) {
    if (!s_fb || !wifi_is_connected()) {
        ESP_LOGW(TAG, "Cannot capture: %s",
                 !s_fb ? "no framebuffer" : "WiFi not connected");
        return -1;
    }

    int stride = bmp_row_stride(s_width);
    int pixel_data_size = stride * s_height;
    int file_size = 54 + pixel_data_size;  /* 14 (file hdr) + 40 (info hdr) + pixels */

    /* Build timestamp for filename */
    int64_t uptime_ms = esp_timer_get_time() / 1000;
    char url[256];
    /* [CXC-REPOINT] docstore base = the ACTIVE tier's binding; DOCUMENT_STORE_URL
     * is now the rescue floor alone. */
    snprintf(url, sizeof(url), "%s/api/v1/documents/captures/cyd/%lld.bmp",
             bus_binding_active_docs(), (long long)uptime_ms);

    ESP_LOGI(TAG, "Capturing %dx%d → %s (%d bytes)", s_width, s_height, url, file_size);

    /* Allocate the full BMP in memory — at 240x320x3 = ~225KB this is tight.
     * If allocation fails, fall back to a smaller capture. */
    uint8_t* bmp = (uint8_t*)malloc(file_size);
    if (!bmp) {
        ESP_LOGE(TAG, "Cannot allocate %d bytes for BMP", file_size);
        return -1;
    }

    /* -- BMP File Header (14 bytes) -- */
    memset(bmp, 0, 54);
    bmp[0] = 'B'; bmp[1] = 'M';
    bmp[2] = file_size & 0xFF;
    bmp[3] = (file_size >> 8) & 0xFF;
    bmp[4] = (file_size >> 16) & 0xFF;
    bmp[5] = (file_size >> 24) & 0xFF;
    bmp[10] = 54;  /* pixel data offset */

    /* -- BMP Info Header (40 bytes) -- */
    bmp[14] = 40;  /* header size */
    bmp[18] = s_width & 0xFF;
    bmp[19] = (s_width >> 8) & 0xFF;
    bmp[22] = s_height & 0xFF;
    bmp[23] = (s_height >> 8) & 0xFF;
    bmp[26] = 1;   /* planes */
    bmp[28] = 24;  /* bits per pixel */

    /* -- Pixel data (bottom-up) -- */
    uint8_t* dst = bmp + 54;
    for (int y = s_height - 1; y >= 0; y--) {
        uint16_t* row = s_fb + y * s_width;
        uint8_t* row_dst = dst;
        for (int x = 0; x < s_width; x++) {
            uint8_t r, g, b;
            rgb565be_to_rgb888(row[x], r, g, b);
            /* BMP stores BGR */
            *row_dst++ = b;
            *row_dst++ = g;
            *row_dst++ = r;
        }
        /* Pad row to 4-byte boundary */
        int pad = stride - s_width * 3;
        for (int p = 0; p < pad; p++) *row_dst++ = 0;
        dst += stride;
    }

    /* -- HTTP POST -- */
    esp_http_client_config_t config = {};
    config.url = url;
    config.method = HTTP_METHOD_POST;
    config.timeout_ms = 10000;

    esp_http_client_handle_t client = esp_http_client_init(&config);
    esp_http_client_set_header(client, "Content-Type", "image/bmp");

    esp_err_t err = esp_http_client_open(client, file_size);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "HTTP open failed: %s", esp_err_to_name(err));
        esp_http_client_cleanup(client);
        free(bmp);
        return -1;
    }

    int written = esp_http_client_write(client, (const char*)bmp, file_size);
    free(bmp);  /* Free immediately after write */

    if (written < 0) {
        ESP_LOGE(TAG, "HTTP write failed");
        esp_http_client_cleanup(client);
        return -1;
    }

    int status = esp_http_client_fetch_headers(client);
    status = esp_http_client_get_status_code(client);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);

    if (status >= 200 && status < 300) {
        ESP_LOGI(TAG, "Capture pushed successfully (HTTP %d)", status);
    } else {
        ESP_LOGW(TAG, "Capture push returned HTTP %d", status);
    }

    return status;
}
