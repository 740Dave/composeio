/*
 * chat_thread.h — scrolling multi-voice conversation surface
 * ([SPEC:CXC-IC] port of composeio/render/widgets/chat_thread.py).
 *
 * A vertical, word-wrapping thread of message BUBBLES in four distinct
 * voices — the flagship consumer of BOTH shared CYD substrates:
 *
 *   - text_wrap.h  wraps each message body at the bubble's inner width, so
 *     the line breaks are the SAME bytes the desktop widget makes (proven:
 *     text_wrap == MultiLineEditMixin._wrap_lines bit-for-bit, and
 *     chat_thread._layout_body == _wrap_lines for plain prose — the wrap
 *     never forks per tier).  This is the whole reason this widget was
 *     blocked until the substrate landed.
 *   - scroll_list.h owns the scroll STATE + clamp + gutter paging.  Bubbles
 *     are VARIABLE height (line count differs per message), so this widget
 *     uses the substrate's NON-BREAKING *_for(content_h) overloads (the
 *     uniform-row API stays for checklist/master_detail): it computes its
 *     own cumulative per-bubble y offsets, clamps the shared offset against
 *     its own content height, and culls to the viewport itself.
 *
 * Voices (chat_thread.py [CXC-IC-CHAT], resolved from (topic, from) there;
 * on this static-config tier each message carries an explicit `voice:`):
 *
 *   reply      architect deliberate reply — accent-edged LEFT bubble
 *   narration  architect thinking-aloud   — the SAME accent LEFT bubble as
 *              reply; only its who-line "(Narration)" label distinguishes it
 *   david      a human turn — panel2 bubble anchored RIGHT
 *   harness    a harness packet — centered mono card (report/guidance/beat)
 *
 * Newest renders at the BOTTOM; older turns scroll up and clip.  Under-full
 * content bottom-anchors (chat_thread.py draw()).
 *
 * TIER REDUCTIONS (documented, not a silent fork — the master_detail.h
 * discipline).  On the Skia/Python tier a body renders BASIC inline markdown
 * (**bold** / `code` / "- " bullets / ## headings / pipe tables) and the
 * thread supports drag-select + Cmd/Ctrl-C copy + an image affordance.  The
 * flat band tier renders the faithful, config-shared CORE: PLAIN word-wrapped
 * prose bubbles per voice with the who-line + optional stamp, scroll + gutter
 * paging.  Inline markdown, tables, text selection/copy and the image
 * affordance are the Skia-only surfaces this tier declines (same seam
 * markdown_table/markdown_inline are — Skia-only helpers).  The card gradient
 * + ambient shadow degrade to a flat panel fill + 1px border (draw_card, the
 * [CXC-ICV-TOKENS] documented band degrade), and the harness packet drops the
 * badge/attrs/glow chrome for a plain centered card.  Colours are the
 * console_theme DARK role tokens (the theme the Skia golden renders with),
 * baked as constants the checklist.cpp / master_detail.cpp way.
 *
 * The static `messages:` list in the scene spec is the render DEFAULT
 * (exactly like checklist's `tasks:`); a live publisher on the widget's
 * `namespace:` would override it (the on_bus_state analog — reactive_apply.h
 * RAttr::ChatMessages; the on-device JSON-apply path is DEFERRED with the rest
 * of on-device MQTT binding, [CXC-RM-M5], like ChecklistItems / NavSelection).
 *
 * Uses ONLY the abstract Canvas contract.
 */

#ifndef WIDGETS_CHAT_THREAD_H
#define WIDGETS_CHAT_THREAD_H

#include "widget.h"
#include "scroll_list.h"
#include <cstdint>

class ChatThreadWidget : public Widget {
public:
    static constexpr int kMaxMessages = 40;   /* overflow turns are dropped   */
    static constexpr int kSenderCap   = 32;   /* per-turn sender/handle       */
    static constexpr int kTextCap     = 512;  /* per-turn body                */
    static constexpr int kStampCap    = 40;   /* pre-formatted @stamp display */

    enum class Voice : uint8_t { David, Reply, Narration, Harness };

    explicit ChatThreadWidget(const char* id, float font_size = 12.0f);

    /* Append a turn (all strings copied/truncated).  `stamp` is the already
     * formatted display stamp (e.g. "13:15 on Jul 22") or null/empty for a
     * name-only who-line — epoch/ISO formatting is a desktop concern the
     * static config carries pre-rendered.  Extra turns past kMaxMessages are
     * dropped from the FRONT (the ring idiom: keep the newest). */
    void add_message(const char* sender, const char* text, Voice voice,
                     const char* stamp = nullptr, const char* kind = nullptr);
    int  message_count() const { return _count; }

    /* Voice from a config `voice:` scalar (reply/narration/harness/david;
     * unknown -> David) — the static-tier analog of chat_thread.py's
     * (topic, from) resolution. */
    static Voice voice_from_name(const char* voice);

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    struct Message {
        char   sender[kSenderCap];
        char   text[kTextCap];
        char   stamp[kStampCap];
        char   kind[kSenderCap];   /* harness packet kind (report/guidance/…) */
        Voice  voice;
    };

    /* Measure-or-paint one turn, returning its total block height (who-line +
     * bubble/packet).  `measure_only` skips every draw op and returns the
     * SAME height the paint pass produces (chat_thread.py._paint_turn's
     * measure_only contract) — so the cumulative scroll math and the paint
     * can never disagree.  The measure pass (all turns, y ignored) runs first
     * to size the content; the paint pass draws each visible turn at its y. */
    float paint_turn(Canvas& canvas, const Message& m, float x0, float cw,
                     float y, bool measure_only) const;
    float paint_bubble(Canvas& canvas, const Message& m, float x0, float cw,
                       float y, bool measure_only) const;
    float paint_packet(Canvas& canvas, const Message& m, float x0, float cw,
                       float y, bool measure_only) const;

    Message    _msgs[kMaxMessages];
    int        _count = 0;
    float      _font_size;
    ScrollList _sl;            /* shared scroll state (variable-height mode) */
    bool       _init_bottom = true;  /* first draw pins to the newest turn   */
    float      _content_h = 0.0f;    /* last draw's content height (on_tap)  */
};

#endif /* WIDGETS_CHAT_THREAD_H */
