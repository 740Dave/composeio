/*
 * placeholder.h — Placeholder widget (M5)
 *
 * Port of composeio/render/widgets/placeholder.py: honest stand-in
 * for config widget types with no C++ implementation on this tier.
 * Dashed outline + the original type name, occupying real layout
 * space, so a config never silently drops a widget.
 */

#ifndef WIDGETS_PLACEHOLDER_H
#define WIDGETS_PLACEHOLDER_H

#include "widget.h"

class PlaceholderWidget : public Widget {
public:
    PlaceholderWidget(const char* id, const char* original_type);

    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    const char* _original_type;
};

#endif /* WIDGETS_PLACEHOLDER_H */
