/*
 * compose_scene.cpp — Config-driven scene builder ([CXC-R-BOOT])
 *
 * Property mapping mirrors python/composeio/render/engine/parser.py
 * (build_leaf) for the widget types implemented on this tier.
 */

#include "compose_scene.h"
#include "yaml_min.h"
#include "config_fetch.h"
#include "touch_dispatch.h"

/* R3 reactive tier: the R1 core (compiled by reference — see
 * reactive_core_canonical.cpp) + the pure apply logic. */
#include "reactive_core.h"
#include "reactive_apply.h"
#ifdef QUALIA_DISPLAY
#include "canvas_qualia.h"     /* kBandHeight=32 dirty quantum (720-row
                                  panel: 23 bands fit the 32-bit mask)
                                  + RENDER_TIER_TRANSFORMS */
#include "xform2d.h"           /* transformed dirty span */
#else
#include "canvas_esp32.h"      /* kDirtyBandHeight (dirty-band geometry —
                                  NOT kBandHeight: on the full-frame tier
                                  the buffer is one 320-row band while the
                                  dirty quantum stays 16 rows) */
#endif

#include "widgets/rectangle.h"
#include "widgets/circle.h"
#include "widgets/label.h"
#include "widgets/gauge.h"
#include "widgets/led.h"
#include "widgets/button.h"
#include "widgets/image.h"
#include "widgets/sprite.h"
#include "widgets/placeholder.h"
#include "widgets/relay.h"
#include "widgets/checkbox.h"
#include "widgets/callout.h"
#include "widgets/emit_button.h"
#include "widgets/tab.h"
#include "widgets/checklist.h"
#include "widgets/master_detail.h"
#include "widgets/chat_thread.h"
#include "widgets/bus_monitor.h"
#include "widgets/dialog.h"
#include "widgets/coordinate.h"
#include "widgets/text_input.h"
#ifdef RENDER_TIER_TRANSFORMS
#include "widgets/sweep.h"     /* DRADIS sweep — needs live rotate() */
#include "widgets/dradis_contacts.h"  /* DRADIS contacts overlay      */
#endif

#include <cstdlib>
#include <cstring>
#include <cstdio>
#include <cmath>
#include "esp_log.h"

static const char* TAG = "compose_scene";

/* ------------------------------------------------------------------ */
/* Scene storage                                                       */
/* ------------------------------------------------------------------ */

static constexpr int kMaxSceneBindings = 24;

/* One live reactive binding: a core output routed to a widget attr.
 * The core's synthetic output namespace is "b<index>". */
struct SceneBinding {
    int entry;                    /* index into Scene::entries          */
    RAttr attr;
    const char* attr_name;        /* static string (candidate table)    */
    char text[64];                /* owned storage for string attrs     */
};

struct Scene {
    YamlDoc* doc;                 /* owns all config strings */
    uint8_t bg_r, bg_g, bg_b;
    char title[64];
    int count;
    SceneEntry entries[kMaxSceneWidgets];
    const YamlNode* variables;    /* variables: map (may be null) */
    int deferred_bindings;        /* behavior/map/when decls seen+ignored */
    bool has_animation;           /* sprite present -> tighter render loop */
    bool has_sweep;               /* sweep widget(s) built (R5 beam path) */
    bool has_sprite;              /* animated sprite built (full path)   */

    /* R3 reactive state ([CXC-ER-RENDER]) */
    ReactiveCore* core;
    int binding_count;
    SceneBinding bindings[kMaxSceneBindings];
    uint32_t dirty_mask;          /* accumulated by the sink per arrival */

    /* EXACT dirty ROW spans ([CXC-ER-DAMAGE]) — the finer currency the
     * band mask quantises.  Accumulated beside the mask, never instead
     * of it: a board that does not consume spans sees the identical
     * mask it always did.  Bounded and MERGING, so a burst of arrivals
     * degrades into fewer/wider spans rather than overflowing. */
    DirtySpan dirty_spans[kMaxDirtySpans];
    int dirty_span_count;

    /* A live canvas, for widgets whose damage span needs real text
     * metrics (LabelWidget).  Null => those widgets fall back to the
     * whole rect. */
    Canvas* measure_canvas;

    bool touch_dirty;             /* interactive visibility flipped      */
    int screen_h;
    int screen_w;                 /* full viewport width (dialog scrim)  */
};

/* Merge [y0,y1] into the scene's span set.  Overlapping or ADJACENT
 * spans coalesce (adjacent because two spans that touch cost strictly
 * more as two transfers than as one).  On overflow the two spans whose
 * union wastes the fewest rows are merged — bounded storage, and the
 * degradation is "repaint a few extra rows", never "miss a row". */
static void add_dirty_span(Scene* s, float fy0, float fy1,
                           bool bottom_up = false) {
    if (!s || fy1 < fy0) return;
    int y0 = (int)floorf(fy0);
    int y1 = (int)ceilf(fy1);
    if (y1 < 0) return;
    if (y0 < 0) y0 = 0;
    if (y0 >= s->screen_h) return;
    if (y1 >= s->screen_h) y1 = s->screen_h - 1;

    for (int i = 0; i < s->dirty_span_count; i++) {
        DirtySpan& d = s->dirty_spans[i];
        if (y0 <= d.y1 + 1 && d.y0 <= y1 + 1) {      /* overlap/adjacent */
            if (y0 < d.y0) d.y0 = (int16_t)y0;
            if (y1 > d.y1) d.y1 = (int16_t)y1;
            /* Direction is a PRESENTATION hint, so a merge takes it if
             * ANY contributor asked for it.  Two widgets in one span
             * disagreeing is possible and harmless — one of them gets
             * its preferred wavefront, both get correct pixels. */
            d.bottom_up = d.bottom_up || bottom_up;
            /* One merge can bridge two existing spans — re-coalesce. */
            for (int j = 0; j < s->dirty_span_count; j++) {
                if (j == i) continue;
                DirtySpan& e = s->dirty_spans[j];
                if (d.y0 <= e.y1 + 1 && e.y0 <= d.y1 + 1) {
                    if (e.y0 < d.y0) d.y0 = e.y0;
                    if (e.y1 > d.y1) d.y1 = e.y1;
                    d.bottom_up = d.bottom_up || e.bottom_up;
                    s->dirty_spans[j] =
                        s->dirty_spans[--s->dirty_span_count];
                    j = -1;                            /* restart sweep  */
                }
            }
            return;
        }
    }

    if (s->dirty_span_count < kMaxDirtySpans) {
        s->dirty_spans[s->dirty_span_count].y0 = (int16_t)y0;
        s->dirty_spans[s->dirty_span_count].y1 = (int16_t)y1;
        s->dirty_spans[s->dirty_span_count].bottom_up = bottom_up;
        s->dirty_span_count++;
        return;
    }

    /* Full: merge the new span into whichever existing span it wastes
     * the fewest rows against. */
    int best = 0;
    int best_cost = 0x7FFFFFFF;
    for (int i = 0; i < s->dirty_span_count; i++) {
        const DirtySpan& d = s->dirty_spans[i];
        int lo = y0 < d.y0 ? y0 : d.y0;
        int hi = y1 > d.y1 ? y1 : d.y1;
        int cost = (hi - lo) - (d.y1 - d.y0) - (y1 - y0);
        if (cost < best_cost) { best_cost = cost; best = i; }
    }
    DirtySpan& d = s->dirty_spans[best];
    if (y0 < d.y0) d.y0 = (int16_t)y0;
    if (y1 > d.y1) d.y1 = (int16_t)y1;
    d.bottom_up = d.bottom_up || bottom_up;
}

/* ------------------------------------------------------------------ */
/* Value helpers ($variables + colors)                                 */
/* ------------------------------------------------------------------ */

/* Resolve a scalar through variables: ($name -> variables.name). */
static const YamlNode* resolve_var(const Scene* s, const YamlNode* n) {
    if (n && n->type == YamlNode::Type::Scalar && n->scalar &&
        n->scalar[0] == '$' && s->variables) {
        const YamlNode* v = s->variables->get(n->scalar + 1);
        if (v) return v;
        ESP_LOGW(TAG, "undefined variable %s", n->scalar);
    }
    return n;
}

static const YamlNode* getv(const Scene* s, const YamlNode* map, const char* k) {
    return map ? resolve_var(s, map->get(k)) : nullptr;
}

static float getf(const Scene* s, const YamlNode* map, const char* k, float dflt) {
    const YamlNode* n = getv(s, map, k);
    float v;
    return (n && n->as_float(v)) ? v : dflt;
}

static const char* gets(const Scene* s, const YamlNode* map, const char* k,
                        const char* dflt) {
    const YamlNode* n = getv(s, map, k);
    return (n && n->type == YamlNode::Type::Scalar && n->scalar) ? n->scalar : dflt;
}

/* Parse "#RRGGBB[AA]" or [r, g, b(, a)] into RGBA. */
static bool parse_color(const Scene* s, const YamlNode* n,
                        uint8_t& r, uint8_t& g, uint8_t& b, uint8_t& a) {
    n = resolve_var(s, n);
    if (!n) return false;
    a = 255;
    if (n->type == YamlNode::Type::Scalar && n->scalar && n->scalar[0] == '#') {
        size_t len = strlen(n->scalar + 1);
        if (len != 6 && len != 8) return false;
        char hx[3] = {0, 0, 0};
        auto byte_at = [&](int i) -> uint8_t {
            hx[0] = n->scalar[1 + i * 2];
            hx[1] = n->scalar[2 + i * 2];
            return (uint8_t)strtol(hx, nullptr, 16);
        };
        r = byte_at(0); g = byte_at(1); b = byte_at(2);
        if (len == 8) a = byte_at(3);
        return true;
    }
    if (n->type == YamlNode::Type::List && n->count >= 3) {
        int vals[4] = {0, 0, 0, 255};
        int i = 0;
        for (const YamlNode* c = n->first_child; c && i < 4; c = c->next_sibling, i++) {
            if (!c->as_int(vals[i])) return false;
        }
        r = (uint8_t)vals[0]; g = (uint8_t)vals[1];
        b = (uint8_t)vals[2]; a = (uint8_t)vals[3];
        return true;
    }
    return false;
}

/* ------------------------------------------------------------------ */
/* Widget factory (mirrors engine/parser.py build_leaf for this tier)  */
/* ------------------------------------------------------------------ */

static bool note_deferred(Scene* s, const YamlNode* spec, const char* wid) {
    /* Bindings are REAL since R3 (extracted after the widget lands in
     * the scene); temporal behaviors remain honestly deferred (no
     * behavior engine on this tier). */
    bool any = false;
    if (spec->get("behaviors")) {
        ESP_LOGW(TAG, "widget '%s': 'behaviors' deferred (no behavior "
                 "engine on this tier)", wid);
        s->deferred_bindings++;
        any = true;
    }
    return any;
}

/* ------------------------------------------------------------------ */
/* Reactive bindings (R3, [CXC-ER-RENDER])                             */
/* ------------------------------------------------------------------ */

static void describe_dsl_value(const DslValue& v, char* buf, size_t n) {
    switch (v.type) {
        case DslValue::Type::Number: snprintf(buf, n, "%g", v.num); break;
        case DslValue::Type::Bool:
            snprintf(buf, n, "%s", v.b ? "true" : "false"); break;
        default: snprintf(buf, n, "'%s'", v.str.c_str()); break;
    }
}

/* Apply one binding result to its widget attribute.  Returns the dirty
 * band mask (0 when the application was withheld — bad coercion, or an
 * unchanged visible).  reactive_apply.h owns the pure decisions; this
 * is the thin widget-setter dispatch. */
static uint32_t apply_binding_value(Scene* s, int bi, const DslValue& v,
                                    bool log) {
    SceneBinding& b = s->bindings[bi];
    SceneEntry& e = s->entries[b.entry];
    Widget* w = e.widget;
    const char* wt = w->widget_type();
    bool applied = false;
#ifdef RENDER_TIER_TRANSFORMS
    /* Pre-application transform (the OLD pose must also be repainted
     * when a transform attr changes — see the dirty-span note below). */
    const float pre_rotation = e.rotation;
    const float pre_scale = e.scale;
#endif
    /* Pre-application geometry: a bound x/y/w/h moves the widget, so
     * BOTH the vacated rect AND the new rect must repaint (both tiers). */
    const Rect pre_rect = e.rect;

#ifdef CXC_FINE_DAMAGE
    /* Pre-application RENDER STATE, for the sub-widget damage query
     * ([CXC-ER-DAMAGE]).  Must be taken BEFORE the setter runs — after
     * it, the old state is gone and the widget can only report its
     * rect.  Widgets that do not implement it leave valid=false and
     * everything below behaves exactly as it did before.
     *
     * ⚠ THE #ifdef IS LOAD-BEARING, AND WAS FOUND BY MEASUREMENT.  The
     * first cut gated only the span-driven REPAINT in main.cpp and left
     * this query unconditional — which narrowed the BAND MASK on every
     * board that links this file, including boards nobody could audit.
     * The A/B run caught it: the "before" leg reported 80 rows for a
     * transition the bench had measured at 224.  An opt-in that is only
     * opt-in at the last step is not opt-in. */
    WidgetDamage before;
    w->damage_snapshot(b.attr_name, before);
#endif

    switch (b.attr) {
        case RAttr::Value: {
            double d;
            if (reactive_coerce_number(v, d)) {
                if (!strcmp(wt, "gauge")) {
                    static_cast<GaugeWidget*>(w)->set_value((float)d);
                    applied = true;
                }
#ifdef RENDER_TIER_TRANSFORMS
                else if (!strcmp(wt, "sweep")) {
                    static_cast<SweepWidget*>(w)->set_speed((float)d);
                    applied = true;
                }
#endif
            }
            break;
        }
        case RAttr::State:
            if (!strcmp(wt, "led")) {
                static_cast<LEDWidget*>(w)->set_state(
                    (LEDState)reactive_led_state(v));
                applied = true;
            }
            break;
        case RAttr::Text:
            if (!strcmp(wt, "label") &&
                reactive_format_text(v, b.text, sizeof(b.text))) {
                static_cast<LabelWidget*>(w)->set_text(b.text);
                applied = true;
            }
            break;
        case RAttr::LabelText:
            if (reactive_format_text(v, b.text, sizeof(b.text))) {
                if (!strcmp(wt, "gauge")) {
                    static_cast<GaugeWidget*>(w)->set_label(b.text);
                    applied = true;
                } else if (!strcmp(wt, "led")) {
                    static_cast<LEDWidget*>(w)->set_label(b.text);
                    applied = true;
                } else if (!strcmp(wt, "bus_monitor")) {
                    static_cast<BusMonitorWidget*>(w)->set_label(b.text);
                    applied = true;
                }
            }
            break;
        case RAttr::Unit:
            if (!strcmp(wt, "gauge") &&
                reactive_format_text(v, b.text, sizeof(b.text))) {
                static_cast<GaugeWidget*>(w)->set_unit(b.text);
                applied = true;
            }
            break;
        case RAttr::RelayValue:
            /* The state-engine value, as its canonical text (a number is
             * "%g"-spelled — relay parses it for the continuous engine). */
            if (!strcmp(wt, "relay") &&
                reactive_format_text(v, b.text, sizeof(b.text))) {
                static_cast<RelayWidget*>(w)->set_value(b.text);
                applied = true;
            }
            break;
        case RAttr::Checked:
            if (!strcmp(wt, "checkbox")) {
                static_cast<CheckboxWidget*>(w)->set_checked(
                    reactive_visible(v));   /* DSL truthiness */
                applied = true;
            }
            break;
        case RAttr::CalloutText:
            if (!strcmp(wt, "callout") &&
                reactive_format_text(v, b.text, sizeof(b.text))) {
                static_cast<CalloutWidget*>(w)->set_text(b.text);
                applied = true;
            }
            break;
        case RAttr::ChecklistItems:
            /* The whole task list arrives as a JSON-array STRING on the
             * widget's `namespace:` subscription (the on_bus_state payload).
             * set_items_json mirrors nightwatch_checklist.py _coerce exactly
             * and is bounded by kMaxRows (no dynamic allocation).  A non-string
             * value, or a string that is not a JSON list, keeps the last good
             * list (python `_coerce -> None` no-op) -> applied stays false ->
             * WITHHELD.  All the checklist type spellings construct a widget
             * whose runtime type() is "checklist". */
            if (!strcmp(wt, "checklist") && v.type == DslValue::Type::String)
                applied =
                    static_cast<ChecklistWidget*>(w)->set_items_json(
                        v.str.c_str());
            break;
        case RAttr::NavSelection:
            /* The LEFT nav selection arrives on nav_namespace as a section-NAME
             * string (case-insensitive label match) or a numeric INDEX
             * (range-checked) — master_detail.py _index_for / on_bus_state
             * parity.  A bool, an unmatched name, or an out-of-range index
             * withholds (python `_index_for -> None`). */
            if (!strcmp(wt, "master_detail")) {
                auto* md = static_cast<MasterDetailWidget*>(w);
                int idx = -1;
                if (v.type == DslValue::Type::Number) {
                    int n = (int)v.num;
                    if (n >= 0 && n < md->section_count()) idx = n;
                } else if (v.type == DslValue::Type::String) {
                    idx = md->index_for(v.str.c_str());
                }
                if (idx >= 0) { md->set_active(idx); applied = true; }
            }
            break;
        case RAttr::Visible: {
            bool vis = reactive_visible(v);
            if (e.visible != vis && e.interactive) s->touch_dirty = true;
            e.visible = vis;
            applied = true;
            break;
        }
        case RAttr::Rotation: {
            /* Live transform ([CXC-ER-C6], framebuffer tier only —
             * the lookup row is compiled out elsewhere). */
            double d;
            if (reactive_coerce_number(v, d)) {
                e.rotation = (float)d;
                applied = true;
            }
            break;
        }
        case RAttr::Scale: {
            double d;
            if (reactive_coerce_number(v, d)) {
                e.scale = (float)d;
                applied = true;
            }
            break;
        }
        case RAttr::PosX:
        case RAttr::PosY:
        case RAttr::Width:
        case RAttr::Height: {
            /* Geometry binding ([CXC-PRIM-DRAGVAL]) — set the widget's
             * SceneEntry rect (every widget draws from the rect passed
             * to draw(); no widget-side geometry state to fork).  A
             * negative w/h clamps to 0 (an empty draw, not a wrap). */
            double d;
            if (reactive_coerce_number(v, d)) {
                float f = (float)d;
                switch (b.attr) {
                    case RAttr::PosX:   e.rect.x = f; break;
                    case RAttr::PosY:   e.rect.y = f; break;
                    case RAttr::Width:  e.rect.w = f < 0.0f ? 0.0f : f; break;
                    case RAttr::Height: e.rect.h = f < 0.0f ? 0.0f : f; break;
                    default: break;
                }
                applied = true;
            }
            break;
        }
#ifdef RENDER_TIER_TRANSFORMS
        case RAttr::ContactsSource:
            if (!strcmp(wt, "dradis_contacts")) {
                /* The contract value is a JSON ARRAY — anything that is
                 * not a string cannot be one; the widget's own warn-once
                 * malformed path owns the verdict. */
                static_cast<DradisContactsWidget*>(w)->set_source(
                    v.type == DslValue::Type::String ? v.str.c_str() : "{");
                applied = true;
            }
            break;
        case RAttr::ContactsAlert:
            if (!strcmp(wt, "dradis_contacts")) {
                static_cast<DradisContactsWidget*>(w)->set_alert(
                    dsl_truthy(v));
                applied = true;
            }
            break;
        case RAttr::BodiesSource:
            if (!strcmp(wt, "dradis_contacts")) {
                /* dradis/bodies — the stellar backdrop.  Same JSON-
                 * array-string discipline as source:. */
                static_cast<DradisContactsWidget*>(w)->set_bodies(
                    v.type == DslValue::Type::String ? v.str.c_str() : "{");
                applied = true;
            }
            break;
        case RAttr::OwnHeading:
            if (!strcmp(wt, "dradis_contacts")) {
                /* own_heading — own-ship arrow at scope center, degrees.
                 * Coerce to a number (bool/numeric-string per the one
                 * DSL rule); a non-numeric value withholds. */
                double d;
                if (reactive_coerce_number(v, d)) {
                    static_cast<DradisContactsWidget*>(w)->set_own_heading(
                        (float)d);
                    applied = true;
                }
            }
            break;
#endif
        case RAttr::FillColor:
        case RAttr::StrokeColor: {
            uint8_t c[4];
            if (reactive_parse_color(v, c)) {
                if (!strcmp(wt, "rectangle")) {
                    auto* r = static_cast<RectangleWidget*>(w);
                    if (b.attr == RAttr::FillColor)
                        r->set_fill_color(c[0], c[1], c[2], c[3]);
                    else
                        r->set_stroke_color(c[0], c[1], c[2], c[3]);
                    applied = true;
                } else if (!strcmp(wt, "circle")) {
                    auto* ci = static_cast<CircleWidget*>(w);
                    if (b.attr == RAttr::FillColor)
                        ci->set_fill_color(c[0], c[1], c[2], c[3]);
                    else
                        ci->set_stroke_color(c[0], c[1], c[2], c[3]);
                    applied = true;
                }
            }
            break;
        }
        case RAttr::TextColor: {
            uint8_t c[4];
            if (reactive_parse_color(v, c)) {
                if (!strcmp(wt, "label")) {
                    static_cast<LabelWidget*>(w)->set_color(c[0], c[1], c[2]);
                    applied = true;
                } else if (!strcmp(wt, "led")) {
                    static_cast<LEDWidget*>(w)->set_custom_color(c[0], c[1],
                                                                 c[2]);
                    applied = true;
                }
            }
            break;
        }
        default:
            break;
    }

    char vbuf[80];
    describe_dsl_value(v, vbuf, sizeof(vbuf));
    if (!applied) {
        ESP_LOGW(TAG, "WIDGET %s.%s <- %s WITHHELD (bad coercion for "
                 "attr type)", w->widget_id(), b.attr_name, vbuf);
        return 0;
    }

#ifdef RENDER_TIER_TRANSFORMS
    /* Contacts attrs drive a PER-FRAME animated overlay (contacts
     * scenes always animate) — never dirty the background snapshot
     * (the 3-buffer path would pointlessly recomposite it). */
    if (b.attr == RAttr::ContactsSource || b.attr == RAttr::ContactsAlert ||
        b.attr == RAttr::BodiesSource || b.attr == RAttr::OwnHeading) {
        if (log)
            ESP_LOGI(TAG, "WIDGET %s.%s <- %.60s (animated overlay — no "
                     "band repaint)", w->widget_id(), b.attr_name, vbuf);
        return 0;
    }
#endif

    float dirty_y = e.rect.y;
    float dirty_h = e.rect.h;
#ifdef RENDER_TIER_TRANSFORMS
    /* A transformed widget sweeps beyond its rect: dirty its bounding
     * circle so a transform change repaints BOTH the old and the new
     * pose (incl. rotation returning to 0 / scale shrinking — the
     * pre-application values gate and size the span too). */
    if (e.rotation != 0.0f || e.scale != 1.0f ||
        pre_rotation != 0.0f || pre_scale != 1.0f) {
        float span_scale = e.scale > pre_scale ? e.scale : pre_scale;
        if (span_scale < 1.0f) span_scale = 1.0f;
        xf_transformed_vspan(e.rect.x, e.rect.y, e.rect.w, e.rect.h,
                             span_scale, dirty_y, dirty_h);
    }
#endif
    /* Geometry binding: union the OLD (vacated) and NEW vertical spans
     * so the widget is erased at its old y..y+h and repainted at the
     * new one.  (x/w moves within the same bands still repaint the
     * whole band row, so the horizontal shift is covered too.) */
    if (b.attr == RAttr::PosX || b.attr == RAttr::PosY ||
        b.attr == RAttr::Width || b.attr == RAttr::Height) {
        float y0 = pre_rect.y < dirty_y ? pre_rect.y : dirty_y;
        float y1a = pre_rect.y + pre_rect.h;
        float y1b = dirty_y + dirty_h;
        float y1 = y1a > y1b ? y1a : y1b;
        dirty_y = y0;
        dirty_h = y1 - y0;
    }

    bool exact = false;
    float span_y0 = dirty_y;
    float span_y1 = dirty_y + dirty_h - 1.0f;
    bool span_bottom_up = false;

#ifdef CXC_FINE_DAMAGE
    /* SUB-WIDGET DAMAGE ([CXC-ER-DAMAGE]) — ask the widget which of its
     * OWN rows actually changed.  Only for a plain attribute change:
     * a geometry or transform binding has already widened dirty_y/h to
     * a union of two poses above, which the widget knows nothing about,
     * so those keep the rect span. */
    const bool plain_attr =
        (b.attr != RAttr::PosX && b.attr != RAttr::PosY &&
         b.attr != RAttr::Width && b.attr != RAttr::Height);
    if (plain_attr && before.valid && pre_rect.y == e.rect.y &&
        pre_rect.h == e.rect.h && s->measure_canvas) {
        DamageSpan dmg;
        if (w->damage_rows(b.attr_name, before, e.rect, *s->measure_canvas,
                           dmg)) {
            if (dmg.y1 < dmg.y0) {
                /* The widget states that NOTHING on the glass changed
                 * (a value that relit no segment, a re-published
                 * identical string).  Not a withheld application — the
                 * attribute IS updated; there is simply nothing to
                 * send.  This is the cheapest repaint there is. */
                if (log)
                    ESP_LOGI(TAG, "WIDGET %s.%s <- %s (no visible change "
                             "— 0 rows)", w->widget_id(), b.attr_name, vbuf);
                return 0;
            }
            span_y0 = dmg.y0;
            span_y1 = dmg.y1;
            span_bottom_up = dmg.bottom_up;
            exact = true;
        }
    }
#endif /* CXC_FINE_DAMAGE */

    uint32_t mask = exact
        ? reactive_band_mask(span_y0, span_y1 - span_y0 + 1.0f,
                             kDirtyBandHeight, s->screen_h)
        : reactive_band_mask(dirty_y, dirty_h, kDirtyBandHeight,
                             s->screen_h);

    /* A widget that did NOT answer gets the rows its BANDS cover, not
     * the rows its rect covers.  The difference is load-bearing: a
     * widget may legitimately draw a little outside its rect (a 24 px
     * line of text in a 28 px box overflows it), and band quantisation
     * is what has been covering that.  Narrowing an un-opted-in widget
     * to its bare rect would be a silent under-invalidation for every
     * widget in the catalogue at once. */
    if (exact) {
        add_dirty_span(s, span_y0, span_y1, span_bottom_up);
    } else if (mask) {
        int b0 = reactive_first_band(mask);
        int b1 = reactive_last_band(mask);
        add_dirty_span(s, (float)(b0 * kDirtyBandHeight),
                       (float)((b1 + 1) * kDirtyBandHeight - 1));
    }
    if (log) {
        int b0 = reactive_first_band(mask);
        int b1 = reactive_last_band(mask);
        ESP_LOGI(TAG, "WIDGET %s.%s <- %s (repaint bands %d..%d, "
                 "y %d..%d%s)", w->widget_id(), b.attr_name, vbuf, b0, b1,
                 exact ? (int)floorf(span_y0) : b0 * kDirtyBandHeight,
                 exact ? (int)ceilf(span_y1)
                       : (b1 + 1) * kDirtyBandHeight - 1,
                 exact ? " EXACT" : "");
    }
    return mask;
}

/* ReactiveCore output sink: "b<N>" -> binding N. */
static void scene_reactive_sink(void* user, const char* output_ns,
                                const DslValue& v) {
    Scene* s = static_cast<Scene*>(user);
    if (!output_ns || output_ns[0] != 'b') return;
    int bi = atoi(output_ns + 1);
    if (bi < 0 || bi >= s->binding_count) return;
    s->dirty_mask |= apply_binding_value(s, bi, v, true);
}

/* Register one binding on the scene core.  *source* is a bare
 * namespace or "expr: <dsl>" (ReactiveCore::add_binding grammar).
 * *dflt* (nullable YAML scalar) is the value until the bus speaks. */
static void add_scene_binding(Scene* s, int entry_idx, const char* wtype,
                              const char* attr_name, const char* source,
                              const YamlNode* dflt) {
    const char* wid = s->entries[entry_idx].widget->widget_id();
    RAttr kind = reactive_attr_lookup(wtype, attr_name);
    if (kind == RAttr::None) {
        ESP_LOGW(TAG, "widget '%s': attr '%s' not bindable on this tier "
                 "— ignored", wid, attr_name);
        return;
    }
    if (s->binding_count >= kMaxSceneBindings) {
        ESP_LOGW(TAG, "binding table full (%d) — '%s.%s' dropped",
                 kMaxSceneBindings, wid, attr_name);
        return;
    }

    char out_ns[8];
    snprintf(out_ns, sizeof(out_ns), "b%d", s->binding_count);
    if (!s->core->add_binding(out_ns, source)) {
        ESP_LOGW(TAG, "binding '%s.%s' REFUSED: %s", wid, attr_name,
                 s->core->error());
        return;
    }

    SceneBinding& b = s->bindings[s->binding_count];
    b.entry = entry_idx;
    b.attr = kind;
    b.attr_name = attr_name;
    b.text[0] = '\0';
    int bi = s->binding_count++;

    ESP_LOGI(TAG, "BINDING %s.%s <- %s", wid, attr_name, source);

    /* Initial state: the declared default, or (visible only) the M4
     * hidden-until-the-namespace-speaks semantics. */
    if (dflt && dflt->type == YamlNode::Type::Scalar && dflt->scalar) {
        DslValue v;
        bool bv;
        float fv;
        if (dflt->as_bool(bv)) v = DslValue::boolean(bv);
        else if (dflt->as_float(fv)) v = DslValue::number((double)fv);
        else v = DslValue::string(dflt->scalar, strlen(dflt->scalar));
        apply_binding_value(s, bi, v, false);
    } else if (kind == RAttr::Visible) {
        s->entries[entry_idx].visible = false;
    }
}

/* Bindable-attr candidates this tier scans on every widget spec. */
static const char* kBindableCandidates[] = {
    "value", "state", "text", "label", "unit",
    "fill_color", "stroke_color", "color", "visible",
    /* Geometry ([CXC-PRIM-DRAGVAL]) — universal, BOTH tiers.  A scalar
     * x/y/w/h stays static (consumed by getf in build_widget); only the
     * {expr:|namespace:} map form registers a binding. */
    "x", "y", "w", "width", "h", "height",
#ifdef REACTIVE_TRANSFORM_ATTRS
    "rotation", "scale", "speed",     /* framebuffer tier ([CXC-ER-C6]) */
    "source", "alert", "bodies",      /* dradis_contacts (contract keys) */
    "own_heading",                    /* dradis own-ship arrow (degrees) */
#endif
};

/* Pull reactive bindings out of *spec* for the widget at *entry_idx*
 * (the embedded mirror of bindings.py extract_bindings, R3 subset). */
static void extract_scene_bindings(Scene* s, const YamlNode* spec,
                                   int entry_idx) {
    const char* type = gets(s, spec, "type", "unknown");
    const char* wid = s->entries[entry_idx].widget->widget_id();

    /* Widget-level namespace: — the type's primary attribute. */
    const YamlNode* nsn = getv(s, spec, "namespace");
    if (nsn && nsn->type == YamlNode::Type::Scalar && nsn->scalar) {
        const char* pattr = reactive_primary_attr(type);
        if (pattr) {
            add_scene_binding(s, entry_idx, type, pattr, nsn->scalar,
                              nullptr);
        } else {
            ESP_LOGW(TAG, "widget '%s': type '%s' has no primary attr — "
                     "'namespace:' ignored", wid, type);
        }
    }

    for (const char* key : kBindableCandidates) {
        const YamlNode* n = getv(s, spec, key);
        if (!n) continue;

        if (n->type == YamlNode::Type::Scalar) {
            /* Bare-string namespace form: visible only on this tier
             * (NAMESPACE_STRING_ATTRS ∩ implemented attrs). */
            bool bv;
            if (!strcmp(key, "visible") && n->scalar && !n->as_bool(bv))
                add_scene_binding(s, entry_idx, type, key, n->scalar,
                                  nullptr);
            continue;                     /* other scalars are literals */
        }
        if (n->type != YamlNode::Type::Map) continue;

        const YamlNode* dflt = n->get("default");
        if (!dflt) dflt = n->get("initial");

        const YamlNode* expr = n->get("expr");
        if (!expr) expr = n->get("expression");
        if (expr && expr->type == YamlNode::Type::Scalar && expr->scalar) {
            if (n->get("namespace") || n->get("map")) {
                ESP_LOGW(TAG, "widget '%s': expr binding on '%s' cannot "
                         "combine with namespace:/map: — refused", wid, key);
                continue;
            }
            char source[DslExpression::kMaxSource + 8];
            snprintf(source, sizeof(source), "expr: %s", expr->scalar);
            add_scene_binding(s, entry_idx, type, key, source, dflt);
            continue;
        }

        const YamlNode* ns = n->get("namespace");
        if (!ns || ns->type != YamlNode::Type::Scalar || !ns->scalar) {
            ESP_LOGW(TAG, "widget '%s': binding object on '%s' has no "
                     "namespace:/expr: — ignored", wid, key);
            continue;
        }
        if (n->get("map") || n->get("when") || n->get("invert")) {
            /* Wrong semantics are worse than none: a raw-value bind
             * under a declared map/when/invert would silently invert
             * author intent.  Refused, statically rendered. */
            ESP_LOGW(TAG, "widget '%s': map:/when:/invert: on '%s' not "
                     "on this tier — binding refused (static render)",
                     wid, key);
            s->deferred_bindings++;
            continue;
        }
        add_scene_binding(s, entry_idx, type, key, ns->scalar, dflt);
    }
}

/* Compact JSON-ish serialization of an emit VALUE node (scalar / list /
 * map) for emit_button — the value is carried as text through the
 * log-only publish hook (no bus transport on this tier yet). */
static int serialize_yaml(const YamlNode* n, char* buf, int cap, int pos) {
    if (!n || pos >= cap - 1) return pos;
    if (n->type == YamlNode::Type::Scalar) {
        pos += snprintf(buf + pos, cap - pos, "%s", n->scalar ? n->scalar : "");
    } else if (n->type == YamlNode::Type::List) {
        pos += snprintf(buf + pos, cap - pos, "[");
        int i = 0;
        for (const YamlNode* c = n->first_child; c && pos < cap - 1;
             c = c->next_sibling) {
            if (i++) pos += snprintf(buf + pos, cap - pos, ",");
            pos = serialize_yaml(c, buf, cap, pos);
        }
        pos += snprintf(buf + pos, cap - pos, "]");
    } else {
        pos += snprintf(buf + pos, cap - pos, "{");
        int i = 0;
        for (const YamlNode* c = n->first_child; c && pos < cap - 1;
             c = c->next_sibling) {
            if (i++) pos += snprintf(buf + pos, cap - pos, ",");
            pos += snprintf(buf + pos, cap - pos, "%s:", c->key ? c->key : "");
            pos = serialize_yaml(c, buf, cap, pos);
        }
        pos += snprintf(buf + pos, cap - pos, "}");
    }
    if (pos > cap - 1) pos = cap - 1;
    return pos;
}

/* Read a 2-element float list (callout target/offset [a, b]). */
static bool read_pair(const Scene* s, const YamlNode* n, float& a, float& b) {
    n = resolve_var(s, n);
    if (!n || n->type != YamlNode::Type::List || n->count < 2) return false;
    const YamlNode* c0 = n->first_child;
    const YamlNode* c1 = c0 ? c0->next_sibling : nullptr;
    float v0, v1;
    if (c0 && c1 && c0->as_float(v0) && c1->as_float(v1)) {
        a = v0; b = v1; return true;
    }
    return false;
}

static Widget* build_widget(Scene* s, const YamlNode* spec, Canvas& canvas,
                            bool& interactive) {
    interactive = false;
    const char* type = gets(s, spec, "type", "unknown");
    const char* wid = gets(s, spec, "id", type);

    note_deferred(s, spec, wid);

    uint8_t r, g, b, a;

    if (!strcmp(type, "rect")) {
        float w = getf(s, spec, "w", getf(s, spec, "width", 100));
        float h = getf(s, spec, "h", getf(s, spec, "height", 60));
        auto* wdg = new RectangleWidget(wid, w, h);
        if (parse_color(s, spec->get("fill_color"), r, g, b, a))
            wdg->set_fill_color(r, g, b, a);
        if (parse_color(s, spec->get("stroke_color"), r, g, b, a))
            wdg->set_stroke_color(r, g, b, a);
        wdg->set_stroke_width(getf(s, spec, "stroke_width", 1.0f));
        wdg->set_corner_radius(getf(s, spec, "corner_radius", 0.0f));
        return wdg;
    }

    if (!strcmp(type, "circle")) {
        float w = getf(s, spec, "w", getf(s, spec, "width", 60));
        float h = getf(s, spec, "h", getf(s, spec, "height", w));
        float radius = (w < h ? w : h) / 2.0f;
        auto* wdg = new CircleWidget(wid, radius);
        if (parse_color(s, spec->get("fill_color"), r, g, b, a))
            wdg->set_fill_color(r, g, b, a);
        if (parse_color(s, spec->get("stroke_color"), r, g, b, a))
            wdg->set_stroke_color(r, g, b, a);
        wdg->set_stroke_width(getf(s, spec, "stroke_width", 1.0f));
        return wdg;
    }

    if (!strcmp(type, "label")) {
        auto* wdg = new LabelWidget(
            wid,
            gets(s, spec, "text", ""),
            getf(s, spec, "font_size", 16.0f),
            gets(s, spec, "align", "left"),
            gets(s, spec, "font_weight", gets(s, spec, "weight", "regular")),
            gets(s, spec, "font_family", "sans-serif"));
        if (parse_color(s, spec->get("color"), r, g, b, a))
            wdg->set_color(r, g, b);
        return wdg;
    }

    if (!strcmp(type, "gauge")) {
        auto* wdg = new GaugeWidget(
            wid,
            getf(s, spec, "min_val", getf(s, spec, "min", 0)),
            getf(s, spec, "max_val", getf(s, spec, "max", 100)),
            getf(s, spec, "value", 0),
            gets(s, spec, "unit", ""),
            gets(s, spec, "label", ""));
        const YamlNode* warn = getv(s, spec, "warning_threshold");
        const YamlNode* dang = getv(s, spec, "danger_threshold");
        float v;
        if (warn && warn->as_float(v)) wdg->set_warning_threshold(v);
        if (dang && dang->as_float(v)) wdg->set_danger_threshold(v);

        /* [CXC-PRIM-LEDBAR] style MODE + led_bar config (parser.py
         * build_leaf parity; every key inert for the dial).  These are
         * CONSTRUCTOR-STATIC on both tiers — bindings drive only
         * value/label/unit (reactive_apply.h stays in lockstep with
         * bindings.py BINDABLE_ATTRS_BY_TYPE). */
        wdg->set_style(gets(s, spec, "style", "dial"));
        wdg->set_orientation(gets(s, spec, "orientation", "vertical"));
        const YamlNode* rev = getv(s, spec, "reverse");
        bool bv;
        if (rev && rev->as_bool(bv)) wdg->set_reverse(bv);
        wdg->set_segments((int)getf(s, spec, "segments", 10.0f));
        const YamlNode* segc = getv(s, spec, "segment_colors");
        if (segc && segc->type == YamlNode::Type::List) {
            for (const YamlNode* c = segc->first_child; c;
                 c = c->next_sibling) {
                const YamlNode* rc = resolve_var(s, c);
                /* non-scalar entry -> nullptr -> mid-grey, keeping the
                 * entry COUNT (the repeat-last extension depends on it) */
                wdg->add_segment_color(
                    (rc && rc->type == YamlNode::Type::Scalar)
                        ? rc->scalar : nullptr);
            }
        }
        wdg->set_segment_shape(gets(s, spec, "segment_shape", "rectangular"));
        wdg->set_segment_long_axis(
            gets(s, spec, "segment_long_axis", "across"));
        wdg->set_segment_gap(getf(s, spec, "segment_gap", 4.0f));
        wdg->set_padding(getf(s, spec, "padding", 8.0f));
        wdg->set_min_segment_size(getf(s, spec, "min_segment_size", 0.0f));
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "led")) {
        const char* state = gets(s, spec, "state", "off");
        LEDState st = LEDState::Off;
        if (!strcmp(state, "on")) st = LEDState::On;
        else if (!strcmp(state, "warning")) st = LEDState::Warning;
        else if (!strcmp(state, "danger")) st = LEDState::Danger;
        const char* label = gets(s, spec, "label", nullptr);
        auto* wdg = new LEDWidget(wid, st, label);
        if (parse_color(s, spec->get("color"), r, g, b, a))
            wdg->set_custom_color(r, g, b);
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "button")) {
        auto* wdg = new ButtonWidget(
            wid,
            gets(s, spec, "label", gets(s, spec, "text", "Button")),
            gets(s, spec, "publish_namespace", nullptr));
        wdg->set_led_behavior(gets(s, spec, "led_behavior", "follow_state"));
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "image")) {
        const char* src = gets(s, spec, "src", "");
        auto* wdg = new ImageWidget(wid, src);
        if (src[0] &&
            (!strncmp(src, "bus://", 6) || !strncmp(src, "http://", 7))) {
            size_t alen = 0;
            char* bytes = http_fetch(src, &alen, 160 * 1024);
            if (bytes) {
                ImageHandle h = canvas.decode_image(
                    reinterpret_cast<const uint8_t*>(bytes), alen);
                free(bytes);
                if (h) {
                    wdg->set_handle(h);
                } else {
                    ESP_LOGW(TAG, "image '%s': undecodable on this tier "
                             "(BMP/CI16 only) — fallback box", wid);
                }
            }
        } else if (src[0]) {
            ESP_LOGW(TAG, "image '%s': unsupported src scheme '%s' — fallback",
                     wid, src);
        }
        return wdg;
    }

    if (!strcmp(type, "sprite")) {
        /* [CXC-R-C18]: pre-baked frame sequence.  src is a pattern with
         * {i} for the frame index (bake_lottie_sprite.py layout). */
        auto* wdg = new SpriteWidget(wid, getf(s, spec, "fps", 8.0f));
        const char* pattern = gets(s, spec, "src", "");
        int frames = (int)getf(s, spec, "frames", 0.0f);
        if (frames > kMaxSpriteFrames) frames = kMaxSpriteFrames;
        for (int i = 0; i < frames; i++) {
            char uri[192];
            const char* mark = strstr(pattern, "{i}");
            if (!mark) break;
            snprintf(uri, sizeof(uri), "%.*s%d%s",
                     (int)(mark - pattern), pattern, i, mark + 3);
            size_t alen = 0;
            char* bytes = http_fetch(uri, &alen, 160 * 1024);
            if (!bytes) { ESP_LOGW(TAG, "sprite frame %d fetch failed", i); break; }
            ImageHandle hdl = canvas.decode_image(
                reinterpret_cast<const uint8_t*>(bytes), alen);
            free(bytes);
            if (!hdl) { ESP_LOGW(TAG, "sprite frame %d undecodable", i); break; }
            wdg->add_frame(hdl);
        }
        ESP_LOGI(TAG, "sprite '%s': %d/%d frames loaded", wid,
                 wdg->frame_count(), frames);
        if (wdg->frame_count() > 0) {
            s->has_animation = true;
            s->has_sprite = true;      /* full-composite animation path */
        }
        return wdg;
    }

#ifdef RENDER_TIER_TRANSFORMS
    if (!strcmp(type, "sweep")) {
        /* DRADIS radar sweep — the live-transform showcase.  Only on
         * tiers whose canvas implements rotate() ([CXC-ER-C6]); the
         * band tier keeps its honest placeholder below. */
        const YamlNode* gridn = getv(s, spec, "grid");
        bool grid = true;
        if (gridn) { bool bv; if (gridn->as_bool(bv)) grid = bv; }
        auto* wdg = new SweepWidget(wid, getf(s, spec, "period_s", 4.0f),
                                    grid);
        float sp = getf(s, spec, "speed", 1.0f);
        wdg->set_speed(sp);
        if (parse_color(s, spec->get("color"), r, g, b, a))
            wdg->set_color(r, g, b);
        s->has_animation = true;
        s->has_sweep = true;           /* 3-buffer beam path candidate */
        return wdg;
    }

    if (!strcmp(type, "dradis_contacts")) {
        /* DRADIS semantic contacts overlay — normative contract:
         * docs/dradis/contacts-contract.md (LOG projection, range in
         * light-seconds).  Bus-driven state (source:/alert:) rides the
         * R3 binding machinery (extract_scene_bindings); motion
         * headings are derived locally by the widget from screen-space
         * deltas.  Contacts ride the sweep's per-frame overlay pass;
         * the widget's static range rings bake into the background
         * snapshot — see scene_draw_sweeps + the SweepRenderPhase gate
         * in the widget. */
        auto* wdg = new DradisContactsWidget(wid, &canvas);
        wdg->set_geometry(getf(s, spec, "center_x", 360.0f),
                          getf(s, spec, "center_y", 360.0f),
                          getf(s, spec, "radius", 330.0f));
        wdg->set_default_size(getf(s, spec, "size", 72.0f));
        wdg->set_default_body_size(getf(s, spec, "body_size", 120.0f));
        const YamlNode* hudn = getv(s, spec, "hud");
        if (hudn) { bool bv; if (hudn->as_bool(bv)) wdg->set_hud(bv); }
        /* Blink/motion are temporal: the scene must animate even
         * without a sweep (has_sprite stays false so a sweep+contacts
         * scene keeps the 3-buffer beam path). */
        s->has_animation = true;
        return wdg;
    }
#endif

    if (!strcmp(type, "relay")) {
        /* state_engine: scalar "binary"|... OR a map {type:, values:}. */
        const YamlNode* sen = getv(s, spec, "state_engine");
        const char* etype = "binary";
        const YamlNode* vals = nullptr;
        if (sen && sen->type == YamlNode::Type::Scalar && sen->scalar) {
            etype = sen->scalar;
        } else if (sen && sen->type == YamlNode::Type::Map) {
            const YamlNode* t = sen->get("type");
            if (t && t->type == YamlNode::Type::Scalar && t->scalar)
                etype = t->scalar;
            vals = sen->get("values");
        }
        auto* wdg = new RelayWidget(wid, etype, gets(s, spec, "label", nullptr));
        if (vals && vals->type == YamlNode::Type::List) {
            for (const YamlNode* c = vals->first_child; c; c = c->next_sibling) {
                const YamlNode* rc = resolve_var(s, c);
                if (rc && rc->type == YamlNode::Type::Scalar && rc->scalar)
                    wdg->add_enum_value(rc->scalar);
            }
        }
        return wdg;
    }

    if (!strcmp(type, "checkbox")) {
        /* Initial state is unchecked (bus/tap only), matching the Python
         * CheckboxWidget — no static `checked:` key, to avoid forking the
         * initial render between tiers. */
        auto* wdg = new CheckboxWidget(
            wid,
            gets(s, spec, "label", ""),
            gets(s, spec, "on_value", "on"),
            gets(s, spec, "off_value", "off"),
            gets(s, spec, "namespace", nullptr));
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "callout")) {
        auto* wdg = new CalloutWidget(wid, gets(s, spec, "text", ""));
        float p0, p1;
        if (read_pair(s, spec->get("target"), p0, p1)) wdg->set_target(p0, p1);
        if (read_pair(s, spec->get("offset"), p0, p1)) wdg->set_offset(p0, p1);
        if (parse_color(s, spec->get("color"), r, g, b, a))
            wdg->set_color(r, g, b);
        wdg->set_font_size(getf(s, spec, "font_size", 12.0f));
        return wdg;
    }

    if (!strcmp(type, "emit_button")) {
        auto* wdg = new EmitButtonWidget(wid, gets(s, spec, "label", "Emit"));
        const YamlNode* emits = getv(s, spec, "emits");
        if (emits && emits->type == YamlNode::Type::List) {
            for (const YamlNode* e = emits->first_child; e;
                 e = e->next_sibling) {
                if (e->type != YamlNode::Type::Map) continue;
                const YamlNode* nsn = e->get("namespace");
                if (!nsn) nsn = e->get("ns");
                if (!nsn || nsn->type != YamlNode::Type::Scalar || !nsn->scalar)
                    continue;
                char vbuf[80] = {0};
                const YamlNode* vn = e->get("value");
                if (vn) serialize_yaml(vn, vbuf, (int)sizeof(vbuf), 0);
                wdg->add_emit(nsn->scalar, vbuf);
            }
        }
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "tab") || !strcmp(type, "tabcontainer") ||
        !strcmp(type, "tab_container")) {
        /* Base TabWidget bar (parser.py _build_tab dialect): labels come
         * from `tabs:` or `children:` tab defs (label / tab_label, with
         * a "Tab N" default), initial_tab selects the starting cell.
         * Per-tab CONTENT and the subclass index-namespace seam are out
         * of scope on this tier (see widgets/tab.h). */
        auto* wdg = new TabWidget(wid, (int)getf(s, spec, "initial_tab", 0.0f));
        const YamlNode* defs = spec->get("tabs");
        if (!defs) defs = spec->get("children");
        if (defs && defs->type == YamlNode::Type::List) {
            int i = 0;
            for (const YamlNode* t = defs->first_child; t;
                 t = t->next_sibling, i++) {
                const YamlNode* lb = (t->type == YamlNode::Type::Map)
                                         ? t->get("label") : nullptr;
                if (!lb && t->type == YamlNode::Type::Map)
                    lb = t->get("tab_label");
                if (lb && lb->type == YamlNode::Type::Scalar && lb->scalar) {
                    wdg->add_label(lb->scalar);
                } else {
                    char buf[16];
                    snprintf(buf, sizeof(buf), "Tab %d", i);
                    wdg->add_label(buf);
                }
            }
        }
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "checklist") || !strcmp(type, "status_list") ||
        !strcmp(type, "nightwatch_checklist") ||
        !strcmp(type, "task_checklist") || !strcmp(type, "taskchecklist")) {
        /* Scrollable status-list ([CXC-PRIM-TIER-PARITY] port of
         * nightwatch_checklist.py, generalized).  Static `tasks:` (or the
         * generic `items:`) list is the render default — a live publisher
         * on `namespace:` overrides it (on-device JSON-apply deferred with
         * on-device MQTT binding, [CXC-RM-M5]).  The scroll model is the
         * shared ScrollList substrate — this is its first consumer. */
        auto* wdg = new ChecklistWidget(wid, getf(s, spec, "font_size", 12.5f));
        const YamlNode* rows = getv(s, spec, "tasks");
        if (!rows) rows = getv(s, spec, "items");
        if (rows && rows->type == YamlNode::Type::List) {
            for (const YamlNode* it = rows->first_child; it;
                 it = it->next_sibling) {
                const YamlNode* ri = resolve_var(s, it);
                if (!ri) continue;
                if (ri->type == YamlNode::Type::Map) {
                    const YamlNode* ln = ri->get("label");
                    if (!ln) ln = ri->get("name");
                    const YamlNode* sn = ri->get("status");
                    const char* label = (ln && ln->type == YamlNode::Type::Scalar)
                                            ? ln->scalar : nullptr;
                    const char* status = (sn && sn->type == YamlNode::Type::Scalar)
                                             ? sn->scalar : nullptr;
                    if (label) wdg->add_row(label, status);
                } else if (ri->type == YamlNode::Type::Scalar && ri->scalar) {
                    /* bare string entry -> deferred (Python _coerce parity) */
                    wdg->add_row(ri->scalar, nullptr);
                }
            }
        }
        interactive = true;   /* catch taps for gutter page-scroll */
        return wdg;
    }

    if (!strcmp(type, "master_detail") || !strcmp(type, "masterdetail")) {
        /* Two-region nav viewer ([CXC-PRIM-TIER-PARITY] port of
         * master_detail.py).  Same `sections:`/`children:` section-def
         * dialect as parser.py _build_master_detail (label / tab_label,
         * default "Section N").  The LEFT nav is the shared ScrollList
         * substrate; the RIGHT detail body is text_wrap-wrapped.  The
         * detail text is the config-shared `text:` field (present when a
         * section is a text leaf — the same field the Skia reference
         * renders as a label in the detail container); a section with
         * `children:` takes its first child's `text:`.  Full
         * child-container detail layout is the deferred flat-tier seam
         * (widgets/master_detail.h). */
        auto* wdg = new MasterDetailWidget(
            wid,
            getf(s, spec, "nav_width", 264.0f),
            gets(s, spec, "nav_namespace", "_app/config/section"),
            (int)getf(s, spec, "initial_tab", 0.0f));
        const YamlNode* defs = getv(s, spec, "sections");
        if (!defs) defs = getv(s, spec, "children");
        if (defs && defs->type == YamlNode::Type::List) {
            int i = 0;
            for (const YamlNode* sec = defs->first_child; sec;
                 sec = sec->next_sibling, i++) {
                if (sec->type != YamlNode::Type::Map) continue;
                /* nav label */
                const YamlNode* lb = sec->get("label");
                if (!lb) lb = sec->get("tab_label");
                char lbuf[64];
                if (lb && lb->type == YamlNode::Type::Scalar && lb->scalar) {
                    snprintf(lbuf, sizeof(lbuf), "%s", lb->scalar);
                } else {
                    snprintf(lbuf, sizeof(lbuf), "Section %d", i);
                }
                /* detail text: the section's own `text:`, else its first
                 * child's `text:` (config-shared fields only). */
                const YamlNode* tn = sec->get("text");
                if (!tn) {
                    const YamlNode* ch = sec->get("children");
                    if (ch && ch->type == YamlNode::Type::List &&
                        ch->first_child &&
                        ch->first_child->type == YamlNode::Type::Map) {
                        tn = ch->first_child->get("text");
                    }
                }
                const char* detail =
                    (tn && tn->type == YamlNode::Type::Scalar) ? tn->scalar
                                                               : nullptr;
                wdg->add_section(lbuf, detail);
            }
        }
        interactive = true;   /* nav taps: select + gutter page-scroll */
        return wdg;
    }

    if (!strcmp(type, "chat_thread") || !strcmp(type, "chatthread")) {
        /* Scrolling multi-voice conversation ([SPEC:CXC-IC] port of
         * chat_thread.py).  The flagship consumer of BOTH shared substrates:
         * text_wrap wraps each bubble body, scroll_list owns the (variable-
         * height) scroll state + gutter paging.  Static `messages:` is the
         * render default (like checklist `tasks:`); a live publisher on the
         * subscribed namespaces would override it (on-device live-turn apply
         * deferred with on-device MQTT binding, [CXC-RM-M5]).  Each message
         * carries an explicit `voice:` (reply/narration/david/harness — the
         * static-tier analog of the Skia widget's (topic, from) resolution),
         * a `text:`, an optional pre-formatted `stamp:` and (harness) `kind:`.
         * `from:` is a `sender:` alias. */
        auto* wdg = new ChatThreadWidget(
            wid, getf(s, spec, "font_size", 12.0f));
        const YamlNode* msgs = getv(s, spec, "messages");
        if (msgs && msgs->type == YamlNode::Type::List) {
            for (const YamlNode* it = msgs->first_child; it;
                 it = it->next_sibling) {
                const YamlNode* ri = resolve_var(s, it);
                if (!ri || ri->type != YamlNode::Type::Map) continue;
                const YamlNode* vn = ri->get("voice");
                const YamlNode* tn = ri->get("text");
                const YamlNode* sn = ri->get("sender");
                if (!sn) sn = ri->get("from");
                const YamlNode* stn = ri->get("stamp");
                const YamlNode* kn = ri->get("kind");
                const char* voice = (vn && vn->type == YamlNode::Type::Scalar)
                                        ? vn->scalar : nullptr;
                const char* text = (tn && tn->type == YamlNode::Type::Scalar)
                                       ? tn->scalar : nullptr;
                const char* sender = (sn && sn->type == YamlNode::Type::Scalar)
                                         ? sn->scalar : nullptr;
                const char* stamp = (stn && stn->type == YamlNode::Type::Scalar)
                                        ? stn->scalar : nullptr;
                const char* kind = (kn && kn->type == YamlNode::Type::Scalar)
                                       ? kn->scalar : nullptr;
                if (!text) continue;
                wdg->add_message(sender, text,
                                 ChatThreadWidget::voice_from_name(voice),
                                 stamp, kind);
            }
        }
        interactive = true;   /* catch taps for gutter page-scroll */
        return wdg;
    }

    if (!strcmp(type, "bus_monitor") || !strcmp(type, "busmonitor")) {
        /* Scrolling colour-coded bus-traffic log ([CXC-PRIM-TIER-PARITY]
         * port of bus_monitor.py) — a device debug panel; a ScrollList
         * uniform-row consumer (the checklist idiom).  Static `lines:` is
         * the render DEFAULT (like checklist `tasks:`); the Skia widget's
         * live bus.on_message() firehose append is deferred with on-device
         * MQTT binding ([CXC-RM-M5]).  Each entry is either a structured
         * message {namespace:, type:, value:, source:, stamp:/timestamp:}
         * or a pre-formatted {text:, type:}.  `label:` is bindable
         * (bindings.py bus_monitor -> ("label",), RAttr::LabelText). */
        bool show_hb = false;
        const YamlNode* hbn = getv(s, spec, "show_heartbeats");
        if (hbn) { bool bv; if (hbn->as_bool(bv)) show_hb = bv; }
        auto* wdg = new BusMonitorWidget(
            wid, gets(s, spec, "label", "Bus Monitor"), show_hb);
        const YamlNode* lines = getv(s, spec, "lines");
        if (!lines) lines = getv(s, spec, "messages");
        if (lines && lines->type == YamlNode::Type::List) {
            for (const YamlNode* it = lines->first_child; it;
                 it = it->next_sibling) {
                const YamlNode* ri = resolve_var(s, it);
                if (!ri) continue;
                if (ri->type == YamlNode::Type::Map) {
                    auto sca = [&](const char* k) -> const char* {
                        const YamlNode* n = ri->get(k);
                        return (n && n->type == YamlNode::Type::Scalar)
                                   ? n->scalar : nullptr;
                    };
                    const char* type_s = sca("type");
                    const YamlNode* tn = ri->get("text");
                    if (tn && tn->type == YamlNode::Type::Scalar && tn->scalar) {
                        /* pre-formatted line */
                        wdg->add_line(tn->scalar,
                                      BusMonitorWidget::type_from_name(type_s));
                    } else {
                        const char* stamp = sca("stamp");
                        if (!stamp) stamp = sca("timestamp");
                        wdg->add_message(stamp, sca("namespace"), type_s,
                                         sca("value"), sca("source"));
                    }
                } else if (ri->type == YamlNode::Type::Scalar && ri->scalar) {
                    /* bare string -> pre-formatted line, Other colour */
                    wdg->add_line(ri->scalar, BusMonitorWidget::MsgType::Other);
                }
            }
        }
        interactive = true;   /* catch taps for gutter page-scroll */
        return wdg;
    }

    if (!strcmp(type, "dialog")) {
        /* Modal overlay ([CXC-R-DIALOG] port of dialog.py, flat-tier
         * reduction: built-in confirm/abort affordances instead of child
         * Buttons — see widgets/dialog.h).  Scrim + centered panel + body
         * (shared text_wrap) + hit-capture (shared overlay substrate).
         * The scene forces its rect to the full viewport (build_node) so
         * the flat reverse-z touch registry gives modal z-capture. */
        const YamlNode* dismn = getv(s, spec, "dismissible");
        bool dismissible = true;
        if (dismn) { bool bv; if (dismn->as_bool(bv)) dismissible = bv; }
        auto* wdg = new DialogWidget(
            wid,
            gets(s, spec, "title", ""),
            gets(s, spec, "body", gets(s, spec, "text", "")),
            dismissible,
            getf(s, spec, "panel_width", 300.0f),
            getf(s, spec, "panel_height", 0.0f));
        /* open/close gate namespace (also drives `visible` via the type's
         * primary attr — reactive_primary_attr("dialog") == "visible"). */
        const char* ns = gets(s, spec, "namespace", nullptr);
        if (ns) wdg->set_namespace(ns);
        const char* dns = gets(s, spec, "decision_namespace", nullptr);
        if (dns) wdg->set_decision_namespace(dns);
        /* confirm affordance: labelled OR given a decision namespace */
        const YamlNode* cl = getv(s, spec, "confirm_label");
        if ((cl && cl->type == YamlNode::Type::Scalar) || dns) {
            wdg->set_confirm(gets(s, spec, "confirm_label", "OK"),
                             gets(s, spec, "confirm_value", "confirm"));
        }
        const YamlNode* al = getv(s, spec, "abort_label");
        if (al && al->type == YamlNode::Type::Scalar) {
            wdg->set_abort(al->scalar);
        } else if (dismissible && (cl || dns)) {
            wdg->set_abort("Cancel");   /* pair a Cancel with a confirm */
        }
        if (parse_color(s, spec->get("scrim_color"), r, g, b, a))
            wdg->set_scrim_color(r, g, b, a);
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "coordinate")) {
        /* Multi-digit coordinate entry + on-screen numeric keypad (port
         * of coordinate.py, backlog #10 — first consumer of the keyboard
         * substrate).  The digit EDIT MODEL is keyboard.cpp's, host-
         * verified (env:native_keyboard_test); the keypad is the CYD
         * text-entry addition (no desktop on-screen keyboard). */
        const char* ns_coord = gets(s, spec, "namespace_coordinates",
                                    gets(s, spec, "namespace", nullptr));
        auto* wdg = new CoordinateWidget(
            wid,
            gets(s, spec, "label", "Coordinates"),
            (int)getf(s, spec, "max_digits", 8.0f),
            ns_coord,
            gets(s, spec, "namespace_status", nullptr));
        interactive = true;
        return wdg;
    }

    if (!strcmp(type, "text_input")) {
        /* Labeled single-line text entry + on-screen QWERTY (port of
         * text_input.py default chrome, backlog #13 — the keyboard
         * substrate's second consumer).  The text EDIT MODEL is
         * keyboard.cpp's, host-verified (env:native_keyboard_test).
         * Commit is an explicit widget-drawn SEND bar (the console-
         * composer SEND / commit_namespace symmetry — the qwerty has no
         * Enter key and a touch panel has no focus-out).  secret /
         * selection / clipboard / multi-line are desktop-only (text_input.h). */
        bool clear_oc = false;
        const YamlNode* con = getv(s, spec, "clear_on_commit");
        if (con && con->scalar) con->as_bool(clear_oc);
        auto* wdg = new TextInputWidget(
            wid,
            gets(s, spec, "label", ""),
            gets(s, spec, "placeholder", ""),
            gets(s, spec, "namespace", nullptr),
            clear_oc,
            (int)getf(s, spec, "max_len", (float)(kEditCap - 1)));
        interactive = true;
        return wdg;
    }

    /* Honest placeholder for everything not yet ported to C++
     * (lottie, command, power_toggle, doc_picker, touch_viz). */
    ESP_LOGW(TAG, "widget '%s': type '%s' has no C++ impl — placeholder",
             wid, type);
    return new PlaceholderWidget(wid, type);
}

/* ------------------------------------------------------------------ */
/* Tree walk                                                           */
/* ------------------------------------------------------------------ */

static void add_entry(Scene* s, Widget* w, Rect rect, bool interactive,
                      bool visible, float rotation = 0.0f,
                      float scale = 1.0f, uint8_t layer = kLayerBackground) {
    if (s->count >= kMaxSceneWidgets) {
        ESP_LOGW(TAG, "scene full (%d widgets) — '%s' dropped",
                 kMaxSceneWidgets, w->widget_id());
        delete w;
        return;
    }
    s->entries[s->count++] =
        SceneEntry{w, rect, interactive, visible, layer, rotation, scale};
}

/* Build one widget spec (leaf or pixel_stack) at parent offset. */
static void build_node(Scene* s, const YamlNode* spec, Canvas& canvas,
                       float off_x, float off_y, float fallback_y);

static void build_children(Scene* s, const YamlNode* children, Canvas& canvas,
                           float off_x, float off_y) {
    if (!children || children->type != YamlNode::Type::List) return;
    float fallback_y = 4.0f;
    for (const YamlNode* c = children->first_child; c; c = c->next_sibling) {
        if (c->type != YamlNode::Type::Map) continue;
        build_node(s, c, canvas, off_x, off_y, fallback_y);
        fallback_y += 84.0f;
    }
}

static void build_node(Scene* s, const YamlNode* spec, Canvas& canvas,
                       float off_x, float off_y, float fallback_y) {
    const char* type = gets(s, spec, "type", "unknown");

    /* Literal visible: false skips the widget entirely; a BOUND
     * visible was already logged by note_deferred and renders. */
    const YamlNode* vis = spec->get("visible");
    bool visible = true;
    if (vis) {
        bool bv;
        if (vis->as_bool(bv)) visible = bv;
    }

    /* Compositor layer ([CXC HUD foreground]): `layer: foreground` lifts
     * a widget OUT of the background snapshot and into the animated
     * overlay (over bodies/contacts/own-ship, under the sweep beam).
     * Default background.  Generic — any widget, reusable for HUD. */
    uint8_t layer = kLayerBackground;
    const char* layer_s = gets(s, spec, "layer", nullptr);
    if (layer_s && !strcmp(layer_s, "foreground")) layer = kLayerForeground;

    /* Position: absolute x/y (pixel_stack dialect).  The fallback
     * stacking offset applies ONLY to unpositioned leaves so several
     * of them don't hide each other; containers default to origin. */
    bool positioned = spec->get("x") || spec->get("y");
    bool is_container = !strcmp(type, "pixel_stack");
    float dflt_y = (positioned || is_container) ? 0.0f : fallback_y;

    float x = off_x + getf(s, spec, "x", 0.0f);
    float y = off_y + getf(s, spec, "y", dflt_y);
    float w = getf(s, spec, "w", getf(s, spec, "width", 100.0f));
    float h = getf(s, spec, "h", getf(s, spec, "height", 60.0f));

    /* A dialog is a MODAL: its scrim + hit-capture own the whole surface
     * regardless of nesting/authoring.  Force its rect to the full
     * viewport at the origin so the flat reverse-z touch registry (a
     * full-viewport topmost entry) swallows every tap beneath it. */
    if (!strcmp(type, "dialog")) {
        x = 0.0f; y = 0.0f;
        w = (float)s->screen_w; h = (float)s->screen_h;
    }

    if (!strcmp(type, "pixel_stack")) {
        /* Optional container chrome: background + border (mirrors
         * engine/layout.py Container.draw: 1px border, round if
         * corner_radius > 0) */
        uint8_t r, g, b, a;
        float cr = getf(s, spec, "corner_radius", 0.0f);
        if (parse_color(s, spec->get("background"), r, g, b, a)) {
            auto* bg = new RectangleWidget("ps_bg", w, h);
            bg->set_fill_color(r, g, b, a);
            bg->set_corner_radius(cr);
            add_entry(s, bg, Rect{x, y, w, h}, false, visible,
                      0.0f, 1.0f, layer);
        }
        if (parse_color(s, spec->get("border"), r, g, b, a)) {
            auto* bd = new RectangleWidget("ps_border", w, h);
            bd->set_fill_color(0, 0, 0, 0);
            bd->set_stroke_color(r, g, b, a);
            bd->set_stroke_width(1.0f);
            bd->set_corner_radius(cr);
            add_entry(s, bd, Rect{x, y, w, h}, false, visible,
                      0.0f, 1.0f, layer);
        }
        build_children(s, spec->get("children"), canvas, x, y);
        return;
    }

    if (!strcmp(type, "stack") || !strcmp(type, "grid")) {
        /* Flex/grid layout is NOT computed on this tier — honest
         * placeholder so nothing silently vanishes ([CXC-RM-M5]). */
        ESP_LOGW(TAG, "container '%s' not laid out on this tier — "
                 "author CYD configs as pixel_stack", type);
        auto* ph = new PlaceholderWidget(gets(s, spec, "id", type), type);
        add_entry(s, ph, Rect{x, y, w, h}, false, visible,
                  0.0f, 1.0f, layer);
        return;
    }

    bool interactive = false;
    Widget* wdg = build_widget(s, spec, canvas, interactive);
    if (wdg) {
        /* Static transform props (engine dialect: rotation in degrees,
         * uniform scale).  Literal scalars only — a binding object on
         * rotation:/scale: is picked up by extract_scene_bindings on
         * transform-capable tiers.  On the band tier the canvas ops
         * are no-ops, so these stay inert (documented [CXC-R-DRAW]). */
        float rotation = getf(s, spec, "rotation", 0.0f);
        float wscale = getf(s, spec, "scale", 1.0f);
        int before = s->count;
        add_entry(s, wdg, Rect{x, y, w, h}, interactive, visible,
                  rotation, wscale, layer);
        if (s->count > before)
            extract_scene_bindings(s, spec, before);   /* R3 */
    }
}

/* ------------------------------------------------------------------ */
/* Publish hook (no bus transport on this tier yet — log seam)         */
/* ------------------------------------------------------------------ */

static void scene_publish_log(const char* ns, const char* value) {
    ESP_LOGI(TAG, "PUBLISH (deferred, no transport): %s = %s", ns, value);
}

/* DialogWidget dismiss hook: flip the matching entry's visibility off +
 * force a touch re-register (the RAttr::Visible analog — the modal stops
 * capturing).  The scene owns SceneEntry.visible; the widget cannot reach
 * it, so this is the precedented static seam (like the publish hook). */
static void dialog_dismiss_cb(void* user, const char* wid) {
    Scene* s = static_cast<Scene*>(user);
    if (!s || !wid) return;
    for (int i = 0; i < s->count; i++) {
        if (!strcmp(s->entries[i].widget->widget_id(), wid)) {
            if (s->entries[i].visible) {
                s->entries[i].visible = false;
                s->touch_dirty = true;   /* main loop re-registers touch */
            }
            return;
        }
    }
}

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

Scene* scene_build(const char* yaml_text, size_t len,
                   Canvas& canvas, const PanelGeometry& geo) {
    auto* doc = new YamlDoc();
    const YamlNode* root = doc->parse(yaml_text, len);
    if (!root || root->type != YamlNode::Type::Map) {
        ESP_LOGE(TAG, "config parse failed: %s", doc->error());
        delete doc;
        return nullptr;
    }

    auto* s = static_cast<Scene*>(calloc(1, sizeof(Scene)));
    if (!s) { delete doc; return nullptr; }
    s->doc = doc;
    s->bg_r = 0x1E; s->bg_g = 0x1E; s->bg_b = 0x2E;     /* engine _BG */
    s->variables = root->get("variables");
    s->screen_h = geo.height;
    s->screen_w = geo.width;
    s->core = new ReactiveCore();          /* R3: the R1 core, reused */
    s->core->set_sink(scene_reactive_sink, s);

    const YamlNode* design = root->get("design");
    const char* title = "ComposeUX";
    if (design) {
        title = gets(s, design, "title", title);
        uint8_t r, g, b, a;
        if (parse_color(s, design->get("background"), r, g, b, a)) {
            s->bg_r = r; s->bg_g = g; s->bg_b = b;
        }
        int dw = design->get_int("width", geo.width);
        int dh = design->get_int("height", geo.height);
        if (dw != geo.width || dh != geo.height) {
            ESP_LOGW(TAG, "design %dx%d != panel %dx%d — rendering 1:1 "
                     "(no scaling on this tier)", dw, dh, geo.width, geo.height);
        }
    }
    snprintf(s->title, sizeof(s->title), "%s", title);

    ButtonWidget::set_publish_fn(scene_publish_log);
    CheckboxWidget::set_publish_fn(scene_publish_log);
    EmitButtonWidget::set_publish_fn(scene_publish_log);
    MasterDetailWidget::set_publish_fn(scene_publish_log);
    DialogWidget::set_publish_fn(scene_publish_log);
    CoordinateWidget::set_publish_fn(scene_publish_log);
    TextInputWidget::set_publish_fn(scene_publish_log);
    DialogWidget::set_dismiss_fn(dialog_dismiss_cb, s);

    const YamlNode* widgets = root->get("widgets");
    if (!widgets || widgets->type != YamlNode::Type::List) {
        ESP_LOGE(TAG, "config has no widgets: list");
        scene_free(canvas, s);
        return nullptr;
    }
    build_children(s, widgets, canvas, 0.0f, 0.0f);

    ESP_LOGI(TAG, "scene '%s': %d widgets built, %d reactive bindings "
             "(%d subscriptions), %d declarations deferred",
             s->title, s->count, s->binding_count,
             (int)s->core->subscription_count(), s->deferred_bindings);
    return s;
}

/* Draw one entry with its widget transform (shared by the full pass
 * and the sweep-only beam pass). */
static void draw_scene_entry(Canvas& canvas, SceneEntry& e) {
#ifdef RENDER_TIER_TRANSFORMS
    /* Widget transform about the rect center (engine layout
     * draw_child parity: rotation degrees, uniform scale). */
    bool xf = (e.rotation != 0.0f || e.scale != 1.0f);
    if (xf) {
        float cx = e.rect.x + e.rect.w / 2.0f;
        float cy = e.rect.y + e.rect.h / 2.0f;
        canvas.save();
        canvas.translate(cx, cy);
        if (e.rotation != 0.0f)
            canvas.rotate(e.rotation * (float)M_PI / 180.0f);
        if (e.scale != 1.0f)
            canvas.scale(e.scale, e.scale);
        canvas.translate(-cx, -cy);
    }
    e.widget->draw(canvas, e.rect);
    if (xf) canvas.restore();
#else
    e.widget->draw(canvas, e.rect);
#endif
}

/* Build-stamp overlay — OFF by default, and no longer a hand-typed tag.
 *
 * HISTORY, because the failure is the point.  This began as a manual
 * token ("TF3" -> "TF4" -> ... "TF13") bumped by hand on every rebuild,
 * rendered top-left on every frame so that a FROZEN panel would still
 * announce which build was flashed — the instrument that would have
 * exposed the original sprite-freeze bug.  Sound idea.
 *
 * It rotted exactly as a manual constant does.  By 2026-08-11 the string
 * still read "TF13" — last bumped in the very commit that fixed the
 * sprite freeze — after roughly ten flashes across THREE boards, one of
 * which (the Waveshare C6) did not exist when the string was written.
 * It reported the same value on two different boards running two
 * different firmwares.  The instrument built to detect stale frames had
 * itself gone stale, and it looked authoritative the whole time.
 *
 * So: the manual string is GONE, and cannot come back.  If the overlay
 * is ever needed again for a freeze investigation, build with
 * -DCXC_BUILD_OVERLAY (a CMake target_compile_definitions, NOT a
 * platformio.ini build_flag — those are silently dropped for
 * framework=espidf) and it stamps __TIME__, which the COMPILER writes.
 * A value no human maintains is a value that cannot silently lie.
 *
 * Default OFF because the boards are finalized and it costs 40x18 px of
 * glass — a real fraction of a 172-wide panel — while the same question
 * is answered by the boot splash's build tag (main.cpp) and by the
 * serial banner. */
#ifdef CXC_BUILD_OVERLAY
static void draw_build_overlay(Canvas& canvas) {
    /* Dead-simple, high-contrast, top-left.  Black chip + bright amber
     * text so it reads over any background and can't silently fail. */
    canvas.set_fill_color(0x00, 0x00, 0x00);
    canvas.fill_rect(0, 0, 46, 18);
    canvas.set_font("sans-serif", 14, "bold");
    canvas.set_fill_color(0xFF, 0xD0, 0x00);
    canvas.draw_text(__TIME__ + 3, 3, 14, "left");   /* MM:SS of build */
}
#endif

void scene_draw(Canvas& canvas, Scene* s) {
    if (!s) return;
    Size sz = canvas.get_surface_size();
    canvas.set_fill_color(s->bg_r, s->bg_g, s->bg_b);
    canvas.fill_rect(0, 0, sz.width, sz.height);
#ifdef RENDER_TIER_TRANSFORMS
    /* 3-buffer path: while BUILDING THE BACKGROUND SNAPSHOT (GridOnly),
     * skip `foreground`-layer widgets — they belong to the animated
     * overlay (scene_draw_sweeps), not the baked background.  In Full /
     * on the band tier they draw normally in scene order. */
    const bool snapshot =
        (SweepWidget::render_phase() == SweepRenderPhase::GridOnly);
#endif
    for (int i = 0; i < s->count; i++) {
        SceneEntry& e = s->entries[i];
        if (!e.visible) continue;
#ifdef RENDER_TIER_TRANSFORMS
        if (snapshot && e.layer == kLayerForeground) continue;
#endif
        draw_scene_entry(canvas, e);
    }
#ifdef CXC_BUILD_OVERLAY
    draw_build_overlay(canvas);   /* on top, every frame */
#endif
}

void scene_draw_sweeps(Canvas& canvas, Scene* s) {
    /* The per-frame ANIMATED OVERLAY pass of the 3-buffer radar path:
     * the DRADIS bodies/contacts layer plus the sweep beam — everything
     * that must repaint every frame over the restored background
     * snapshot (grid + rings + own-ship image are baked there).
     *
     * Z-ORDER (David, 2026-07-16): the sweep BEAM draws LAST so it
     * sweeps OVER the stellar bodies, contacts, and own-ship arrow —
     * illuminating the art like a real radar.  Two explicit passes
     * (contacts first, beam on top) instead of relying on scene order.
     * The grid stays in the background snapshot (GridOnly), unaffected —
     * only the beam's z-order relative to the icons changes. */
    if (!s) return;
    /* Pass 1: DRADIS bodies + contacts + own-ship arrow (under). */
    for (int i = 0; i < s->count; i++) {
        SceneEntry& e = s->entries[i];
        if (!e.visible) continue;
        if (strcmp(e.widget->widget_type(), "dradis_contacts") != 0) continue;
        draw_scene_entry(canvas, e);
    }
    /* Pass 2: FOREGROUND-layer HUD (e.g. the FTL-ready bar) — over the
     * icons, still under the sweep beam.  Any widget type; excludes the
     * sweep (drawn last) and dradis_contacts (already drawn). */
    for (int i = 0; i < s->count; i++) {
        SceneEntry& e = s->entries[i];
        if (!e.visible || e.layer != kLayerForeground) continue;
        const char* wt = e.widget->widget_type();
        if (!strcmp(wt, "sweep") || !strcmp(wt, "dradis_contacts")) continue;
        draw_scene_entry(canvas, e);
    }
    /* Pass 3: sweep beam ON TOP of everything. */
    for (int i = 0; i < s->count; i++) {
        SceneEntry& e = s->entries[i];
        if (!e.visible) continue;
        if (strcmp(e.widget->widget_type(), "sweep") != 0) continue;
        draw_scene_entry(canvas, e);
    }
}

void scene_register_touch(Scene* s) {
    if (!s) return;
    clear_registry();
    for (int i = 0; i < s->count; i++) {
        if (s->entries[i].interactive && s->entries[i].visible) {
            register_widget(s->entries[i].widget, s->entries[i].rect);
        }
    }
}

const char* scene_title(Scene* s) { return s ? s->title : "(none)"; }
int scene_widget_count(Scene* s) { return s ? s->count : 0; }
bool scene_has_animation(Scene* s) { return s && s->has_animation; }
bool scene_sweep_only_animation(Scene* s) {
    return s && s->has_sweep && !s->has_sprite;
}

void scene_free(Canvas& canvas, Scene* s) {
    if (!s) return;
    clear_registry();
    for (int i = 0; i < s->count; i++) {
        Widget* w = s->entries[i].widget;
        if (w && !strcmp(w->widget_type(), "image")) {
            auto* img = static_cast<ImageWidget*>(w);
            canvas.free_image(img->handle());
        } else if (w && !strcmp(w->widget_type(), "sprite")) {
            auto* spr = static_cast<SpriteWidget*>(w);
            for (int f = 0; f < spr->frame_count(); f++) {
                canvas.free_image(spr->frame_handle(f));
            }
        }
#ifdef RENDER_TIER_TRANSFORMS
        else if (w && !strcmp(w->widget_type(), "dradis_contacts")) {
            static_cast<DradisContactsWidget*>(w)->free_icons(canvas);
        }
#endif
        delete w;
    }
    delete s->core;
    delete s->doc;
    free(s);
}

/* ------------------------------------------------------------------ */
/* Reactive tier public API (R3)                                       */
/* ------------------------------------------------------------------ */

size_t scene_subscription_count(Scene* s) {
    return (s && s->core) ? s->core->subscription_count() : 0;
}

const char* scene_subscription_at(Scene* s, size_t i) {
    return (s && s->core) ? s->core->subscription_at(i) : "";
}

int scene_binding_count(Scene* s) {
    return s ? s->binding_count : 0;
}

const YamlNode* scene_yaml_root(Scene* s) {
    return (s && s->doc) ? s->doc->root() : nullptr;
}

uint32_t scene_on_bus_value(Scene* s, const char* ns,
                            int kind, double num, bool b,
                            const char* str, double t) {
    if (!s || !s->core || !ns) return 0;
    CoreValue cv;
    switch (kind) {
        case 0: cv = CoreValue::number(num); break;
        case 1: cv = CoreValue::boolean(b); break;
        default: cv = CoreValue::string(str ? str : ""); break;
    }
    /* ⚠ dirty_mask resets per CALL (the caller ORs across a drain), but
     * the SPANS do not — the caller drains many arrivals into ONE
     * repaint and needs the union.  scene_clear_dirty_spans() is the
     * caller's reset, and forgetting it merges into wider spans, which
     * over-invalidates.  Never the other direction. */
    s->dirty_mask = 0;
    int fired = s->core->on_bus_value(ns, cv, t);
    if (fired == 0 && *s->core->last_eval_error())
        ESP_LOGW(TAG, "EVAL WITHHELD: %s", s->core->last_eval_error());
    return s->dirty_mask;
}

bool scene_touch_dirty(Scene* s) {
    if (!s || !s->touch_dirty) return false;
    s->touch_dirty = false;
    return true;
}

int scene_dirty_span_count(Scene* s) { return s ? s->dirty_span_count : 0; }

void scene_clear_dirty_spans(Scene* s) { if (s) s->dirty_span_count = 0; }

DirtySpan scene_dirty_span_at(Scene* s, int i) {
    if (!s || i < 0 || i >= s->dirty_span_count) return DirtySpan{0, -1};
    return s->dirty_spans[i];
}

void scene_set_measure_canvas(Scene* s, Canvas* c) {
    if (s) s->measure_canvas = c;
}
