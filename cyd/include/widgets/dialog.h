/*
 * dialog.h — modal overlay widget ([CXC-R-DIALOG], [CXC-PRIM-TIER-PARITY]
 * port of composeio/render/widgets/dialog.py) for the CYD/ESP32 tier.
 *
 * A full-surface SCRIM + a centered rounded PANEL (title + word-wrapped
 * body + confirm/abort affordances) that CAPTURES all touch while shown.
 * The scrim + panel geometry and the tap classification come from the
 * shared overlay substrate (overlay.cpp — the first consumer); the body
 * is wrapped with the shared text_wrap substrate (the master_detail.cpp
 * detail idiom).  Uses ONLY the abstract Canvas contract.
 *
 * FLAT-TIER REDUCTION.  dialog.py hosts arbitrary CHILD widgets in its
 * panel (the M4 Command arm-confirm passes child Buttons).  The flat
 * pixel_stack tier has no child-container composition, so — exactly as
 * master_detail.cpp reduces its child-container detail pane to a
 * config-shared text body — DialogWidget renders BUILT-IN confirm/abort
 * affordance buttons (the flat analog of dialog.py's child Buttons).  The
 * modal BEHAVIOUR is faithful: scrim + centered panel + hit-capture +
 * dismiss-on-scrim + publish-the-decision.  The dialog.py list-select
 * (dropdown popup) mode is NOT ported here (a separate future consumer of
 * the same overlay substrate).
 *
 * OPEN / CLOSE.
 *   - `visible` gates draw + touch registration (SceneEntry.visible),
 *     driven from the bus by the generic `visible:` binding or the
 *     type's primary `namespace:` (reactive_primary_attr("dialog")
 *     → "visible") — hidden until the namespace speaks, like dialog.py.
 *   - A tap CLOSES the modal by flipping its own SceneEntry.visible via
 *     the scene dismiss hook (the RAttr::Visible analog — flip + force a
 *     touch re-register), and publishes to the bus for coherence:
 *       confirm → publish confirm_value to decision_namespace, then close;
 *       abort / scrim(dismissible) → close (publish "off" to namespace);
 *       dead panel space → swallowed (no-op).
 *     (Publishing rides the shared scene publish hook — logged until a bus
 *     transport lands on this tier, like Button / Checkbox.)
 */

#ifndef WIDGETS_DIALOG_H
#define WIDGETS_DIALOG_H

#include "widget.h"
#include "overlay.h"
#include <cstdint>

class DialogWidget : public Widget {
public:
    typedef void (*PublishFn)(const char* ns, const char* value);
    /* Scene dismiss hook: flip THIS widget's SceneEntry.visible off + force
     * a touch re-register (the modal stops capturing).  `user` is the
     * Scene* the builder binds. */
    typedef void (*DismissFn)(void* user, const char* widget_id);

    static constexpr int kBodyCap    = 240;   /* body char budget          */
    static constexpr int kTitleCap   = 64;
    static constexpr int kLabelCap   = 32;
    static constexpr int kMaxAff      = 2;    /* confirm + abort           */

    DialogWidget(const char* id,
                 const char* title = "",
                 const char* body = "",
                 bool dismissible = true,
                 float panel_width = 300.0f,
                 float panel_height = 0.0f);   /* 0 → sized from content */

    void set_scrim_color(uint8_t r, uint8_t g, uint8_t b, uint8_t a);
    void set_namespace(const char* ns);                 /* open/close gate */
    void set_decision_namespace(const char* ns);        /* confirm target  */
    void set_confirm(const char* label, const char* value);
    void set_abort(const char* label);

    static void set_publish_fn(PublishFn fn) { s_publish = fn; }
    static void set_dismiss_fn(DismissFn fn, void* user) {
        s_dismiss = fn; s_dismiss_user = user;
    }

    /* -- Widget interface ------------------------------------------ */
    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;
    void on_tap(float x, float y) override;

private:
    /* ONE geometry path: draw() computes + caches these; on_tap() reads
     * them (master_detail.cpp idiom — geometry persists from last draw). */
    void cache_layout(Rect panel, const Rect* affs, int n);
    void close(const char* decision);   /* decision==nullptr → abort/off  */

    char _title[kTitleCap];
    char _body[kBodyCap];
    char _confirm_label[kLabelCap];
    char _abort_label[kLabelCap];
    char _confirm_value[kLabelCap];
    char _namespace[64];        /* open/close gate ns ("" = none)         */
    char _decision_ns[64];      /* confirm publish target ("" = namespace) */

    bool  _dismissible;
    float _panel_w;
    float _panel_h;             /* 0 → content-sized                       */
    uint8_t _scrim[4];

    bool  _has_confirm = false;
    bool  _has_abort   = false;

    /* cached from the last draw() for on_tap classification */
    Rect _rect  = {0, 0, 0, 0};
    Rect _panel = {0, 0, 0, 0};
    Rect _aff[kMaxAff];
    int  _n_aff = 0;
    /* affordance i's role: 0 = confirm, 1 = abort */
    uint8_t _aff_role[kMaxAff] = {0, 0};

    static PublishFn s_publish;
    static DismissFn s_dismiss;
    static void*     s_dismiss_user;
};

#endif /* WIDGETS_DIALOG_H */
