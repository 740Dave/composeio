/*
 * image.h — Image widget for ComposeUX embedded rendering (M5)
 *
 * Port of the Python ImageWidget essentials: draws a decoded image
 * scaled to its rect via canvas.draw_image; renders the documented
 * crossed-box fallback when the asset is missing/undecodable.
 *
 * Asset FETCHING is the scene builder's job (bus://assets over HTTP
 * at scene build); this widget only holds the decoded handle.
 */

#ifndef WIDGETS_IMAGE_H
#define WIDGETS_IMAGE_H

#include "widget.h"

class ImageWidget : public Widget {
public:
    ImageWidget(const char* id, const char* src);

    /** Decoded handle ownership stays with the scene (freed on reload). */
    void set_handle(ImageHandle h) { _handle = h; }
    ImageHandle handle() const { return _handle; }

    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    const char* _src;
    ImageHandle _handle = nullptr;
};

#endif /* WIDGETS_IMAGE_H */
