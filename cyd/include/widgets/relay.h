/*
 * relay.h — Read-only status indicator ([CXC-PRIM-TIER-PARITY] port of
 * composeio/render/widgets/relay.py).
 *
 * A large flat colored circle whose color is decided by a state-engine
 * value, with an optional label below.  Richer than the fixed-state
 * `led`: the color follows a per-engine mapping.
 *
 * Color mapping (verbatim from RelayWidget._resolve_color):
 *   binary:     off -> gray, on -> green
 *   tri_level:  off -> gray, half -> yellow, full -> green
 *   stop_light: green / yellow / red
 *   enum:       first -> gray, last -> green, middle interpolated
 *   continuous: 0 -> gray … 255 -> green (interpolated)
 *
 * The bus value arrives as a string (or a number spelled as one) through
 * the R3 reactive binding (RAttr::RelayValue) — this widget never touches
 * the bus itself.  Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_RELAY_H
#define WIDGETS_RELAY_H

#include "widget.h"
#include <cstdint>

class RelayWidget : public Widget {
public:
    /* Embedded bound on enum `values:` — far above any authored config. */
    static constexpr int kMaxEnumValues = 16;

    RelayWidget(const char* id, const char* engine_type = "binary",
                const char* label = nullptr);

    void set_label(const char* l) { _label = l; }
    /* enum `values:` in declared order (call once per value at build). */
    void add_enum_value(const char* v);

    /* The current bus value, as its canonical string spelling (a numeric
     * payload is passed as its "%g" text — the reactive tier formats it). */
    void set_value(const char* v);

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    enum class Engine : uint8_t {
        Binary, TriLevel, StopLight, Enum, Continuous
    };

    Engine _engine;
    const char* _label;
    char _value[32];
    char _enum_values[kMaxEnumValues][24];
    int _enum_count = 0;

    void resolve_color(uint8_t rgb[3]) const;
};

#endif /* WIDGETS_RELAY_H */
