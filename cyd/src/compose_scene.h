/*
 * compose_scene.h — Config-driven scene ([CXC-R-BOOT] steps 6/8-10)
 *
 * Turns a fetched .compose.yaml into a renderable widget scene.  The
 * SAME binary renders whatever config it is pointed at ([CXC-R-C12]).
 *
 * Supported dialect on this tier (M5 first cut, honest subset):
 *   - design: {width, height, background, title}
 *   - variables: $name parse-time substitution ([CXC-BIND-VARS] subset)
 *   - widgets: a top-level pixel_stack (Composer's export format) with
 *     absolutely-positioned children (x/y/w/h), plus top-level widgets
 *     carrying their own x/y.  Nested pixel_stacks compose offsets.
 *   - widget types: rect, circle, label, gauge, led, button, image;
 *     everything else renders an honest PlaceholderWidget.
 *   - REACTIVE BINDINGS (R3, [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER],
 *     [SPEC:CXC-BIND] subset): the scene owns an R1 ReactiveCore
 *     (firmware/reactive_core, compiled by reference) —
 *       widget-level  namespace: <ns>       -> the type's primary attr
 *       attr:         <bare ns string>      -> visible (the
 *                                              NAMESPACE_STRING_ATTRS
 *                                              form on this tier)
 *       attr:         {namespace: n, default: d}  -> object form
 *       attr:         {expr: "<dsl>", default: d} -> value-expr site
 *                                              via the canonical
 *                                              on-chip DSL
 *     A bus arrival re-evaluates exactly the bindings that read it and
 *     applies changed results to the widget attribute (reactive_apply.h
 *     coercion); the affected band(s) repaint (widget-level dirty).
 *     when:/map:/invert: remain LOGGED AND DEFERRED (honest subset).
 *   - behaviors: still LOGGED AND IGNORED (no behavior engine here).
 *   - flex stack / grid containers: NOT laid out on this tier (a
 *     placeholder marks them); CYD-class configs are authored/exported
 *     as pixel_stack compositions.
 */

#ifndef COMPOSE_SCENE_H
#define COMPOSE_SCENE_H

#include "canvas.h"
#include "widget.h"
#include "board_profile.h"

#include <cstddef>
#include <cstdint>

static constexpr int kMaxSceneWidgets = 48;

/* Compositor layer ([CXC- HUD foreground], 2026-07-16): a GENERIC,
 * reusable z-band — NOT id-hardcoded.  `background` widgets bake into
 * the 3-buffer background snapshot (under the DRADIS bodies/contacts
 * overlay); `foreground` widgets are excluded from the snapshot and
 * drawn in the animated overlay pass AFTER bodies/contacts/own-ship but
 * BEFORE the sweep beam — so a HUD element (e.g. the FTL-ready bar)
 * reads over everything except the topmost radar sweep.  Config key
 * `layer:` with values `background` (default) | `foreground`. */
enum SceneLayer : uint8_t {
    kLayerBackground = 0,
    kLayerForeground = 1,
};

struct SceneEntry {
    Widget* widget;
    Rect rect;
    bool interactive;
    bool visible;
    uint8_t layer;                /* SceneLayer: background|foreground */
    /* Widget transform (R5, [CXC-ER-C6]): applied about the rect
     * center in scene_draw via the Canvas transform ops on the
     * framebuffer tier.  The band tier compiles without the transform
     * wrap and without the binding rows that drive these (its honest
     * refusal is preserved); the fields simply stay 0/1 there. */
    float rotation;               /* degrees, engine convention */
    float scale;                  /* uniform, 1.0 = none */
};

struct Scene;

/**
 * Build a scene from config text.  Fetches + decodes image assets
 * (canvas provides the codec).  Returns nullptr on parse failure.
 */
Scene* scene_build(const char* yaml_text, size_t len,
                   Canvas& canvas, const PanelGeometry& geo);

/**
 * The scene's PARSED YAML root, still alive (the Scene owns the YamlDoc for
 * the lifetime of the composition).
 *
 * ⛔ IT EXISTS SO NOTHING RE-PARSES THE CONFIG.  The freshness receiver reads
 * the same document's `namespaces:` block ([SPEC:CXC-FRESHNESS]); a second
 * YamlDoc over the same 6 KB would double the largest single malloc of boot on
 * a board whose YAML arena is already the tightest allocation it makes.  Valid
 * until scene_free().
 */
struct YamlNode;
const YamlNode* scene_yaml_root(Scene* scene);

/** Draw the whole scene (call from the band-render draw callback). */
void scene_draw(Canvas& canvas, Scene* scene);

/** Register interactive widgets with the touch dispatcher. */
void scene_register_touch(Scene* scene);

/** Scene title (design.title or app id) for logs. */
const char* scene_title(Scene* scene);

/** Number of widgets built (perf-floor reporting). */
int scene_widget_count(Scene* scene);

/** True when the scene contains animated widgets (sprite) — the main
 * loop tightens its touch-poll burst to raise the animation rate. */
bool scene_has_animation(Scene* scene);

/** True when the scene's ONLY animated widgets are sweeps — the
 * framebuffer tier's 3-buffer radar path applies (background snapshot
 * + per-frame beam); sprite scenes keep the full-composite path. */
bool scene_sweep_only_animation(Scene* scene);

/** Draw ONLY the sweep widget(s) — the per-frame beam pass of the
 * 3-buffer radar path.  The caller selects the sweep render phase
 * (sweep.h) around this; transform wrap and visibility match
 * scene_draw.  No background fill. */
void scene_draw_sweeps(Canvas& canvas, Scene* scene);

/** Free the scene, its widgets, decoded images, and the config doc. */
void scene_free(Canvas& canvas, Scene* scene);

/* ------------------------------------------------------------------ */
/* Reactive tier (R3) — see the dialect note above                     */
/* ------------------------------------------------------------------ */

/** Deduped bus namespaces the scene's bindings read (the MQTT
 * subscription list — ReactiveCore::subscriptions passthrough). */
size_t scene_subscription_count(Scene* scene);
const char* scene_subscription_at(Scene* scene, size_t i);

/** Number of live reactive bindings (0 == static scene). */
int scene_binding_count(Scene* scene);

/** Feed one external bus value into the scene's reactive core at time
 * *t* (seconds).  Re-evaluates the bindings that read *ns*, applies
 * changed results to widget attributes (serial-logged as
 * `WIDGET id.attr <- value (repaint bands a..b)`), and returns the
 * dirty band mask (bit N = band N) to repaint.  kind/num/b/str mirror
 * BusEvent (bus_mqtt.h) without including it here. */
uint32_t scene_on_bus_value(Scene* scene, const char* ns,
                            int kind, double num, bool b,
                            const char* str, double t);

/** True when the last scene_on_bus_value toggled an interactive
 * widget's visibility (caller should scene_register_touch again). */
bool scene_touch_dirty(Scene* scene);

/* ------------------------------------------------------------------ *
 * EXACT dirty ROW SPANS ([CXC-ER-DAMAGE])
 *
 * The band mask above is a QUANTISATION of the answer: 16 scanlines
 * per bit, so a 10-row LED segment invalidates 32 rows and a 15-row
 * numeral invalidates 48.  Measured on the ESP32-C3 / ST7796S, SPI
 * transfer is 73% of a repaint and the panel is already at 2/3 of its
 * datasheet clock ceiling — the only lever left is sending fewer rows.
 *
 * scene_on_bus_value fills BOTH: the mask (unchanged, every existing
 * caller keeps its exact behaviour) and this span set, which is the
 * union of what each changed widget said it damaged (Widget::
 * damage_rows) or, for widgets that do not answer, the rows their
 * band mask covers — so a span-driven repaint is never NARROWER than
 * a mask-driven one for an un-opted-in widget.
 * ------------------------------------------------------------------ */

/** Max simultaneous spans tracked per drain (they merge past this). */
static constexpr int kMaxDirtySpans = 6;

struct DirtySpan {
    int16_t y0;   /* first dirty scanline, inclusive */
    int16_t y1;   /* last dirty scanline, inclusive  */
    /* Paint this span from y1 upward ([CXC-ER-DAMAGE] paint-order).  A
     * band-tier repaint cannot be atomic, so the wavefront is visible;
     * this points it the way the change GREW.  Purely presentational —
     * the pixels are identical either way, and the row-CRC audit is
     * order-independent because it is keyed by scanline. */
    bool bottom_up;
};

/** Spans accumulated since the last scene_clear_dirty_spans().  Never
 * overlapping, never adjacent (they coalesce), unordered.
 *
 * ⚠ They ACCUMULATE across scene_on_bus_value calls, unlike the band
 * mask, which resets per call and is OR-ed by the caller.  A drain
 * feeds many arrivals into ONE repaint and needs their union; the
 * caller clears before draining.  Forgetting to clear merges old spans
 * into new ones, which repaints too much — never too little. */
int scene_dirty_span_count(Scene* scene);
DirtySpan scene_dirty_span_at(Scene* scene, int i);
void scene_clear_dirty_spans(Scene* scene);

/** Attach a canvas for widgets whose damage span needs real text
 * metrics.  Null (the default) simply costs precision — those widgets
 * fall back to their whole rect.  Call it while holding the render
 * lock; the canvas is only ever used between render passes. */
void scene_set_measure_canvas(Scene* scene, Canvas* canvas);

#endif /* COMPOSE_SCENE_H */
