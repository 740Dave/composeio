/*
 * bus_monitor.h — scrolling, colour-coded bus-traffic log
 * ([CXC-PRIM-TIER-PARITY] port of
 *  composeio/render/widgets/bus_monitor.py).
 *
 * A device debug panel: a header label over a dark textbox that holds a
 * monospace log of bus messages, one compact line each, COLOUR-CODED by
 * message type:
 *
 *     [stamp] namespace: type value (source)
 *
 * Colours are the Skia reference's verbatim tokens (bus_monitor.py):
 *   state      -> white   (0xFFFFFF)
 *   event      -> cyan    (0x00BCD4)
 *   heartbeat  -> gray    (0x888888)
 *   time_sync  -> gray    (0x888888)
 *   discovery  -> yellow  (0xFFD700)
 *   config_push-> magenta (0xE040FB)
 *   other      -> #AAAAAA (0xAAAAAA)
 *
 * The label + textbox chrome uses the style.py tokens the desktop widget
 * draws with: TEXT (0xD6D6D6) header, TEXTBOX_BG (0x333333) body, mono
 * body at 11px on a 15px line pitch.
 *
 * Lines are FIXED height (LINE_H = 15) -> the scroll model is COMPOSED
 * from the shared ScrollList substrate in UNIFORM-ROW mode (the
 * checklist.cpp idiom, NOT a re-derivation): ScrollList owns
 * content_h / max_scroll / offset / clamp; visible_range() yields the
 * tight [first,last) window so draw() is O(visible lines), not O(lines)
 * — band-renderer friendly; classify() resolves a tap into gutter
 * page-up / page-down (the honest interim for the CYD's tap-on-release
 * touch tier — no fabricated drag delta the substrate can't observe).
 *
 * The log AUTO-SCROLLS to the newest line: the first draw pins the offset
 * to the bottom (the terminal-log default), and a gutter page-up reveals
 * history.  (The Skia reference top-aligns the newest `fit` lines; when
 * the log OVERFLOWS its box this tier bottom-anchors them instead — the
 * conventional log behaviour.  When the log FITS — the parity config
 * case — both tiers render the identical top-aligned block.)
 *
 * TIER REDUCTION (documented, not a silent fork — the checklist /
 * chat_thread discipline): the Skia widget subscribes the LIVE bus via
 * bus.on_message() (a firehose, no namespace) and appends formatted lines
 * as traffic arrives.  This static-config tier renders a `lines:` list as
 * the DEFAULT (exactly like checklist's `tasks:`); on-device live append
 * from the on_message firehose is DEFERRED with the rest of on-device
 * MQTT binding ([CXC-RM-M5]) — bus_monitor has no `namespace:` and no
 * namespace-subscription attr, so there is nothing to withhold in
 * apply_binding_value; only its `label:` is a BINDABLE_ATTRS_BY_TYPE row
 * (bindings.py), applied live via RAttr::LabelText.
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_BUS_MONITOR_H
#define WIDGETS_BUS_MONITOR_H

#include "widget.h"
#include "scroll_list.h"
#include <cstdint>

class BusMonitorWidget : public Widget {
public:
    static constexpr int kMaxLines = 64;   /* ring; oldest dropped on overflow */
    static constexpr int kLineCap  = 96;   /* per formatted line               */
    static constexpr int kLabelCap = 40;   /* header label buffer              */

    enum class MsgType : uint8_t {
        State, Event, Heartbeat, TimeSync, Discovery, ConfigPush, Other
    };

    explicit BusMonitorWidget(const char* id,
                              const char* label = "Bus Monitor",
                              bool show_heartbeats = false);

    /* Append a PRE-FORMATTED line (already spelled the bus_monitor.py way),
     * coloured by `type`.  Dropped from the FRONT past kMaxLines (ring). */
    void add_line(const char* text, MsgType type);

    /* Build + append a line from structured fields the SAME way
     * bus_monitor.py _on_bus_message does:
     *   "[stamp] namespace: type value (source)"
     * (source clause omitted when empty).  `stamp` is the already-formatted
     * display stamp (e.g. "13:15:04") — epoch formatting is a desktop
     * concern the static config carries pre-rendered.  Honours the
     * show_heartbeats filter: a heartbeat/time_sync line is skipped unless
     * show_heartbeats was set (bus_monitor.py parity). */
    void add_message(const char* stamp, const char* namespace_,
                     const char* type, const char* value, const char* source);

    void clear_lines();
    int  line_count() const { return _count; }

    /* Header label (bindable: bindings.py bus_monitor -> ("label",)). */
    void set_label(const char* label);

    /* Canon type normalization (state/event/heartbeat/time_sync/discovery/
     * config_push; else Other). */
    static MsgType type_from_name(const char* type);

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    struct Line {
        char    text[kLineCap];
        MsgType type;
    };

    Line       _lines[kMaxLines];
    int        _count = 0;
    char       _label[kLabelCap];
    bool       _show_heartbeats;
    ScrollList _sl;                  /* shared scroll state (uniform-row) */
    bool       _init_bottom = true;  /* first draw pins to the newest line */
};

#endif /* WIDGETS_BUS_MONITOR_H */
