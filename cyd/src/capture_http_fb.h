/*
 * capture_http_fb.h — pull capture server for the FRAMEBUFFER tier
 * (Qualia R5).  [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER]
 *
 * The framebuffer sibling of capture_http.{h,cpp} (which is welded to
 * the band renderer): GET /capture.bmp streams the CURRENT PSRAM
 * framebuffer as a 24-bit top-down BMP — no re-render needed, the
 * back buffer always holds the last composite.  This is the eyes-free
 * verification hook: publish -> GET -> diff.
 *
 * GET / returns a one-line text status (device id, scene, heap).
 *
 * Concurrency: the render lock serializes streaming (httpd task)
 * against the main loop's composites — take it around ANY canvas
 * render or scene mutation, exactly the R3 contract.
 */

#ifndef CAPTURE_HTTP_FB_H
#define CAPTURE_HTTP_FB_H

#include "canvas_qualia.h"

/* Start the server (once, after WiFi).  Returns true on listen. */
bool capture_http_fb_start(QualiaCanvas* canvas, const char* device_id);

/* Update the status line's scene title (call on scene swap). */
void capture_http_fb_set_title(const char* title);

/* The render lock (coarse). */
void capture_http_fb_lock(void);
void capture_http_fb_unlock(void);

#endif /* CAPTURE_HTTP_FB_H */
