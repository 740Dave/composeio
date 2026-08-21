/*
 * capture_http.h — on-device capture server (R3 verification hook).
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER] / [CYD-CAPTURE-PIPELINE]
 *
 * GET http://<device-ip>/capture.bmp returns the CURRENT frame as a
 * 24-bit BMP: a fresh band-render pass streams straight into the HTTP
 * response (no SD card, no document_store round-trip, no full-frame
 * buffer).  This is the pull-based sibling of capture.cpp's push
 * pipeline — reactive verification needs "publish, then GET, then
 * diff", which push-on-scene-swap cannot serve.
 *
 * GET / returns a one-line text status (device id, scene, heap).
 *
 * Concurrency: the render lock serializes the capture render pass
 * (httpd task) against the main loop's renders, touch SPI use, and
 * scene swaps.  main.cpp takes it around its loop body; the capture
 * handler takes it for the duration of the streamed response.
 */

#ifndef CAPTURE_HTTP_H
#define CAPTURE_HTTP_H

#include "canvas_esp32.h"

/* Start the server (call once, after WiFi).  Returns true on listen. */
bool capture_http_start(ESP32Canvas* canvas, const char* device_id);

/* Point the server at the live scene (call on every scene swap, while
 * holding the render lock).  scene_title_fn may be null. */
void capture_http_set_scene(ESP32Canvas::DrawCallback draw_fn,
                            void* user_data, const char* title);

/* The render lock (recursive-free, coarse).  Take around ANY canvas
 * render, touch SPI burst, or scene mutation. */
void capture_http_lock(void);
void capture_http_unlock(void);

#endif /* CAPTURE_HTTP_H */
