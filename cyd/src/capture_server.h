/*
 * capture_push.h — Push framebuffer captures to document_store
 *
 * No HTTP server on the device. Captures the framebuffer as BMP,
 * POSTs it to the document_store via esp_http_client.
 */

#ifndef CAPTURE_PUSH_H
#define CAPTURE_PUSH_H

#include <cstdint>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize the capture subsystem with a reference to the live framebuffer.
 *
 * @param framebuffer  Pointer to the live RGB565 framebuffer (big-endian)
 * @param width        Framebuffer width in pixels (240)
 * @param height       Framebuffer height in pixels (320)
 */
void capture_init(uint16_t* framebuffer, int width, int height);

/**
 * Capture the current framebuffer and push to document_store as BMP.
 * Returns the HTTP status code from the POST, or -1 on failure.
 * Builds BMP scanline-by-scanline to minimize memory usage.
 */
int capture_and_push(void);

#ifdef __cplusplus
}
#endif

#endif /* CAPTURE_PUSH_H */
