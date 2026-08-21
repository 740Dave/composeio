/*
 * reactive_apply.h — binding-result -> widget-attribute application
 * logic for the reactive display tier (R3).
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-RENDER]
 *
 * The PURE half of "a bus value moves a needle": given a DslValue fired
 * by the R1 reactive core (firmware/reactive_core), decide
 *
 *   - which widget attribute a (type, attr-name) pair addresses
 *     (the embedded mirror of bindings.py BINDABLE_ATTRS_BY_TYPE,
 *     honest subset: the widget types this tier renders),
 *   - how the value coerces to that attribute's type (the embedded
 *     mirror of bindings.py coerce_bound_value — a failed coercion
 *     WITHHOLDS the application, mirroring the eval-error path),
 *   - which 16-scanline band(s) of the panel must repaint when the
 *     attribute changes (widget-level dirty, [CXC-ER-RENDER]:
 *     the bands the widget's rect occupies; sub-widget pixel dirty
 *     is a later refinement).
 *
 * PURE C++: no ESP-IDF, no display, no Widget classes — only dsl.h
 * value types.  Host-tested by firmware/reactive_core env:native_test
 * (which already -I's cyd/src for the canonical DSL).
 */

#ifndef REACTIVE_APPLY_H
#define REACTIVE_APPLY_H

#include "dsl.h"

#include <cstdint>
#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Bindable attributes (embedded subset)                               */
/* ------------------------------------------------------------------ */

enum class RAttr : uint8_t {
    None,          /* not bindable on this tier                        */
    Value,         /* gauge.value / sweep.speed (numeric)              */
    State,         /* led.state              (on/off/warning/danger)   */
    Text,          /* label.text             (string)                  */
    LabelText,     /* gauge/led .label       (string)                  */
    Unit,          /* gauge.unit             (string)                  */
    Visible,       /* any widget             (truthy)                  */
    FillColor,     /* rect/circle fill_color (#RRGGBB[AA])             */
    StrokeColor,   /* rect/circle stroke_color                         */
    TextColor,     /* label/led color                                  */
    Rotation,      /* any widget, degrees — transform tiers only       */
    Scale,         /* any widget, uniform — transform tiers only       */
    PosX,          /* any widget, x — geometry binding ([CXC-PRIM-...]) */
    PosY,          /* any widget, y                                     */
    Width,         /* any widget, w (alias width)                       */
    Height,        /* any widget, h (alias height)                      */
    ContactsSource,/* dradis_contacts.source (JSON array string)       */
    ContactsAlert, /* dradis_contacts.alert  (truthy)                  */
    BodiesSource,  /* dradis_contacts.bodies (JSON array string —
                      stellar backdrop, dradis/bodies)                 */
    OwnHeading,    /* dradis_contacts.own_heading (degrees, own-ship
                      arrow at scope center)                           */
    RelayValue,    /* relay.value  (state-engine string / number text) */
    Checked,       /* checkbox     (truthy preset)                     */
    CalloutText,   /* callout.text (string)                            */
    ChecklistItems,/* checklist/status_list .items (JSON array string,
                      the on_bus_state payload — same dradis_contacts
                      namespace-subscription precedent, not a Composer
                      binding-editor attr).  LIVE ([CXC-RM-M5]): a retained
                      MQTT publish on the widget's `namespace:` replaces the
                      list via ChecklistWidget::set_items_json (compose_scene
                      apply_binding_value), mirroring nightwatch_checklist.py
                      _coerce — bounded by kMaxRows, host-tested for parity in
                      firmware/reactive_core native_test.                 */
    NavSelection,  /* master_detail selection (section-name string, or numeric
                      index, on the nav_namespace subscription — the subclass
                      index seam tab.h referenced; NOT a bindings.py
                      binding-editor row).  LIVE ([CXC-RM-M5]): a publish on
                      nav_namespace selects via MasterDetailWidget::index_for +
                      set_active (compose_scene apply_binding_value), mirroring
                      master_detail.py _index_for / on_bus_state.           */
    ChatMessages,  /* chat_thread messages ([SPEC:CXC-IC]).  The Skia widget
                      subscribes a LIST of namespaces and resolves (topic,
                      from) -> voice on_bus_state; it has NO
                      BINDABLE_ATTRS_BY_TYPE row (a namespace-subscription
                      attr, the ChecklistItems / NavSelection precedent — NOT
                      a Composer binding-editor attr, so bindings.py stays
                      authoritative and unforked).  DEFERRED ([CXC-RM-M5],
                      WITHHELD — no ChatMessages case in apply_binding_value):
                      unlike ChecklistItems (single-namespace REPLACE) and
                      NavSelection (single-namespace SELECT), chat_thread.py
                      subscribes a LIST of namespaces and APPENDS one turn per
                      message, deriving voice from (topic, from-field).  The
                      single-namespace ReactiveCore delivery here cannot carry
                      that contract without a deeper multi-subscription +
                      per-message-append mechanism; a single-namespace
                      JSON-array REPLACE would FORK the cross-tier payload (a
                      publisher could not target both tiers with one message).
                      The static `messages:` list stays the render default
                      until that mechanism lands.                            */
};

/* bindings.py BINDABLE_ATTRS_BY_TYPE, restricted to the C++ widget set.
 * Keys are the .compose.yaml `type:` spellings (compose_scene dialect). */
inline RAttr reactive_attr_lookup(const char* wtype, const char* attr) {
    if (!wtype || !attr) return RAttr::None;
    if (strcmp(attr, "visible") == 0) return RAttr::Visible;  /* universal */

    /* Geometry ([CXC-PRIM-DRAGVAL], spec 23) — universal bindable attrs
     * on BOTH tiers (a bound geometry sets the widget's SceneEntry rect,
     * which every widget draws from; no transform needed, so no tier
     * gate).  This unlocks the CXC-PRIM composite family (a progress bar
     * is a pixel_stack of rects whose fill `w:` is `{expr:...}`). */
    if (strcmp(attr, "x") == 0) return RAttr::PosX;
    if (strcmp(attr, "y") == 0) return RAttr::PosY;
    if (strcmp(attr, "w") == 0 || strcmp(attr, "width") == 0)
        return RAttr::Width;
    if (strcmp(attr, "h") == 0 || strcmp(attr, "height") == 0)
        return RAttr::Height;

#ifdef REACTIVE_TRANSFORM_ATTRS
    /* Universal transform attrs ([CXC-ER-C6]) — compiled ONLY on tiers
     * whose canvas renders transforms (the Qualia framebuffer tier);
     * the band tier's honest refusal ("attr not bindable on this
     * tier") is preserved by leaving them out there. */
    if (strcmp(attr, "rotation") == 0) return RAttr::Rotation;
    if (strcmp(attr, "scale") == 0) return RAttr::Scale;
#endif

    struct Row { const char* type; const char* attr; RAttr kind; };
    static const Row kRows[] = {
        /* gauge rows drive EVERY gauge style incl. led_bar
         * ([CXC-PRIM-LEDBAR]).  The led_bar geometry params (segments/
         * segment_colors/segment_shape/segment_long_axis/segment_gap/
         * padding/min_segment_size + style/orientation/reverse) are
         * CONSTRUCTOR-STATIC config on BOTH tiers — bindings.py
         * BINDABLE_ATTRS_BY_TYPE keeps gauge = value/label/unit, and
         * this table stays in lockstep (an embedded-only bindable row
         * would silently fork tier behavior).  compose_scene.cpp
         * build_widget parses the static keys. */
        {"gauge",     "value",        RAttr::Value},
        {"gauge",     "label",        RAttr::LabelText},
        {"gauge",     "unit",         RAttr::Unit},
        {"led",       "state",        RAttr::State},
        {"led",       "label",        RAttr::LabelText},
        {"led",       "color",        RAttr::TextColor},
        {"label",     "text",         RAttr::Text},
        {"label",     "color",        RAttr::TextColor},
        /* relay: a state-engine value (string / number) picks the color.
         * checkbox: an inbound truthy value presets the checked box.
         * callout: a string value replaces the chip text (dict payloads
         * are not representable on the DslValue tier — Skia-only). */
        {"relay",     "value",        RAttr::RelayValue},
        {"checkbox",  "value",        RAttr::Checked},
        {"callout",   "text",         RAttr::CalloutText},
        /* bus_monitor: the ONLY bindable attr is its header `label:`
         * (bindings.py BINDABLE_ATTRS_BY_TYPE bus_monitor -> ("label",);
         * applied live via RAttr::LabelText).  Its live LINE content
         * arrives from the Skia widget's bus.on_message() firehose (no
         * namespace, no BINDABLE_ATTRS_BY_TYPE row) — deferred on this
         * tier ([CXC-RM-M5]); the static `lines:` config is the render
         * default, so there is nothing to withhold in apply. */
        {"bus_monitor",          "label",     RAttr::LabelText},
        /* checklist / status_list: the whole task/item list arrives as a
         * JSON-array string on the widget's `namespace:` subscription (the
         * on_bus_state payload — nightwatch_checklist.py has NO
         * BINDABLE_ATTRS_BY_TYPE row; this is a namespace-subscription
         * attr, mirroring dradis_contacts source, NOT a binding-editor
         * attr, so bindings.py stays authoritative and unforked).  LIVE:
         * apply_binding_value -> ChecklistWidget::set_items_json. */
        {"checklist",            "items", RAttr::ChecklistItems},
        {"status_list",          "items", RAttr::ChecklistItems},
        {"nightwatch_checklist", "tasks", RAttr::ChecklistItems},
        /* master_detail: the LEFT nav selection arrives as a section-name
         * string on the widget's nav_namespace subscription (the
         * on_bus_state analog).  master_detail.py has NO
         * BINDABLE_ATTRS_BY_TYPE row — it is the SUBCLASS index seam
         * tab.h referenced (a nav_namespace comp.subscription), so this
         * stays a namespace-subscription attr, NOT a binding-editor attr,
         * and bindings.py remains authoritative and unforked.  LIVE:
         * apply_binding_value -> index_for + set_active (a TAP also selects). */
        {"master_detail",        "selection", RAttr::NavSelection},
        /* chat_thread: the conversation arrives as turns on a LIST of
         * subscribed namespaces (voice resolved from topic+from), NOT a
         * single BINDABLE_ATTRS_BY_TYPE attr — chat_thread.py has no such
         * row, so this is a namespace-subscription attr (the ChecklistItems /
         * NavSelection precedent), keeping bindings.py authoritative and
         * unforked.  `messages` is the static-config render default; on-device
         * live-turn apply is deferred ([CXC-RM-M5], WITHHELD like the above). */
        {"chat_thread",          "messages",  RAttr::ChatMessages},
        /* tab: no row — the BASE TabWidget has no BINDABLE_ATTRS_BY_TYPE
         * entry and no `namespace:` (selection keys off local UI/children;
         * only the console_tabs / master_detail / cic_tab_row SUBCLASSES
         * add an index seam, each with its own payload shape).  An
         * embedded-only tab binding would silently fork tier behaviour —
         * a future cic_tab_row port carries its own row. */
        {"rect",      "fill_color",   RAttr::FillColor},
        {"rect",      "stroke_color", RAttr::StrokeColor},
        {"rectangle", "fill_color",   RAttr::FillColor},
        {"rectangle", "stroke_color", RAttr::StrokeColor},
        {"circle",    "fill_color",   RAttr::FillColor},
        {"circle",    "stroke_color", RAttr::StrokeColor},
#ifdef REACTIVE_TRANSFORM_ATTRS
        {"sweep",     "speed",        RAttr::Value},
        /* DRADIS contacts layer (framebuffer tier only — the band tier
         * renders a placeholder, so its honest refusal is preserved). */
        {"dradis_contacts", "source", RAttr::ContactsSource},
        {"dradis_contacts", "alert",  RAttr::ContactsAlert},
        {"dradis_contacts", "bodies", RAttr::BodiesSource},
        {"dradis_contacts", "own_heading", RAttr::OwnHeading},
#endif
    };
    for (const Row& r : kRows)
        if (strcmp(r.type, wtype) == 0 && strcmp(r.attr, attr) == 0)
            return r.kind;
    return RAttr::None;
}

/* Widget-level `namespace:` shorthand — the attribute the widget's
 * primary subscription drives (engine parser comp.subscriptions
 * semantics for the types this tier renders). */
inline const char* reactive_primary_attr(const char* wtype) {
    if (!wtype) return nullptr;
    if (strcmp(wtype, "gauge") == 0) return "value";
    if (strcmp(wtype, "led") == 0) return "state";
    if (strcmp(wtype, "label") == 0) return "text";
    if (strcmp(wtype, "relay") == 0) return "value";
    if (strcmp(wtype, "checkbox") == 0) return "value";
    if (strcmp(wtype, "callout") == 0) return "text";
    if (strcmp(wtype, "checklist") == 0) return "items";
    if (strcmp(wtype, "status_list") == 0) return "items";
    if (strcmp(wtype, "nightwatch_checklist") == 0) return "tasks";
    if (strcmp(wtype, "master_detail") == 0) return "selection";
    if (strcmp(wtype, "chat_thread") == 0) return "messages";
    /* dialog: the type's primary `namespace:` is the open/close gate,
     * driving the universal `visible` attr (RAttr::Visible) — hidden
     * until the namespace speaks (dialog.py on_bus_state truthy→shown). */
    if (strcmp(wtype, "dialog") == 0) return "visible";
#ifdef REACTIVE_TRANSFORM_ATTRS
    if (strcmp(wtype, "sweep") == 0) return "speed";
    if (strcmp(wtype, "dradis_contacts") == 0) return "source";
#endif
    return nullptr;
}

/* ------------------------------------------------------------------ */
/* Coercion (binding result -> typed attribute value)                  */
/* ------------------------------------------------------------------ */

/* Numeric attrs: the one dsl numeric-coercion rule (bool -> 1/0,
 * numeric string -> float64).  false => application withheld. */
inline bool reactive_coerce_number(const DslValue& v, double& out) {
    return dsl_try_number(v, out);
}

/* Text attrs: number -> %g (the DSL's number spelling), bool -> the
 * canon vocabulary on/off, string -> itself.  Always succeeds. */
inline bool reactive_format_text(const DslValue& v, char* buf, size_t cap) {
    if (!buf || cap == 0) return false;
    switch (v.type) {
        case DslValue::Type::Number:
            snprintf(buf, cap, "%g", v.num);
            return true;
        case DslValue::Type::Bool:
            snprintf(buf, cap, "%s", v.b ? "on" : "off");
            return true;
        default:
            snprintf(buf, cap, "%s", v.str.c_str());
            return true;
    }
}

/* led.state: the LEDState index this value selects.
 * Order matches widgets/led.h: 0=On 1=Off 2=Warning 3=Danger.
 * canon "warning"/"danger" hit their states; everything else follows
 * DSL truthiness (so an expr's bool result binds naturally). */
inline int reactive_led_state(const DslValue& v) {
    if (v.type == DslValue::Type::String) {
        std::string c = dsl_canon(v.str);
        if (c == "warning") return 2;
        if (c == "danger") return 3;
    }
    return dsl_truthy(v) ? 0 : 1;
}

/* Color attrs: '#RRGGBB' / '#RRGGBBAA' strings only (the bus-portable
 * spelling); anything else withholds. */
inline bool reactive_parse_color(const DslValue& v, uint8_t rgba[4]) {
    if (v.type != DslValue::Type::String) return false;
    const char* s = v.str.c_str();
    if (s[0] != '#') return false;
    size_t len = strlen(s + 1);
    if (len != 6 && len != 8) return false;
    auto hex = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
    };
    uint8_t out[4] = {0, 0, 0, 255};
    for (size_t i = 0; i < len / 2; i++) {
        int hi = hex(s[1 + i * 2]);
        int lo = hex(s[2 + i * 2]);
        if (hi < 0 || lo < 0) return false;
        out[i] = (uint8_t)((hi << 4) | lo);
    }
    rgba[0] = out[0]; rgba[1] = out[1]; rgba[2] = out[2]; rgba[3] = out[3];
    return true;
}

/* visible: DSL truthiness (binary canon "off"/"" falsy, -0.0 falsy). */
inline bool reactive_visible(const DslValue& v) { return dsl_truthy(v); }

/* ------------------------------------------------------------------ */
/* Dirty bands (widget-level dirty region)                             */
/* ------------------------------------------------------------------ */

/* The bands (band_h scanlines each, bit N = band N) the vertical span
 * [y, y+h) occupies, clipped to the panel.  A changed attribute
 * repaints exactly this mask ([CXC-ER-RENDER] widget-level dirty). */
inline uint32_t reactive_band_mask(float y, float h, int band_h,
                                   int screen_h) {
    if (h <= 0.0f || band_h <= 0 || screen_h <= 0) return 0;
    int y0 = (int)y;
    int y1 = (int)(y + h + 0.999f) - 1;      /* last covered scanline */
    if (y1 < 0 || y0 >= screen_h) return 0;
    if (y0 < 0) y0 = 0;
    if (y1 >= screen_h) y1 = screen_h - 1;
    int b0 = y0 / band_h;
    int b1 = y1 / band_h;
    if (b1 >= 32) b1 = 31;                   /* mask width guard */
    uint32_t mask = 0;
    for (int b = b0; b <= b1; b++) mask |= (1u << b);
    return mask;
}

inline int reactive_first_band(uint32_t mask) {
    if (!mask) return -1;
    for (int b = 0; b < 32; b++)
        if (mask & (1u << b)) return b;
    return -1;
}

inline int reactive_last_band(uint32_t mask) {
    if (!mask) return -1;
    for (int b = 31; b >= 0; b--)
        if (mask & (1u << b)) return b;
    return -1;
}

#endif /* REACTIVE_APPLY_H */
