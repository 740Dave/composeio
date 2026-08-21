/*
 * dradis_contacts.h — DRADIS semantic radar contacts widget
 * [SPEC:CXC-EMBED-REACTIVE] — framebuffer (Qualia) tier only.
 *
 * Renders the `dradis/contacts` bus namespace (a JSON array of contact
 * objects) onto the round scope: each contact's (bearing, range) is
 * projected to a screen pixel and its type icon (a BMP32 fetched ONCE
 * per type from bus://assets/icons/dradis/<type>.bmp and decoded via
 * the Canvas contract's decode_image) is blitted centered there.
 *
 * NORMATIVE CONTRACT: docs/dradis/contacts-contract.md.  Projection is
 * LOGARITHMIC (2026-07-16): `range` is a physical distance in
 * LIGHT-SECONDS (float > 0) and radius is linear in log10(range) — the
 * scope is a x60 light-clock (three rings, each a factor of 60):
 *
 *     radius_px = clamp(2R/3 + (R/3) * log10(range_ls)/log10(60), 0, R)
 *     x = center_x + radius_px * sin(bearing)
 *     y = center_y - radius_px * cos(bearing)     # bearing 0 = up, CW+
 *
 * For the default R=330 the ring anchors are (110 px, 1/60 ls),
 * (220 px, 1 ls), (330 px, 60 ls = 1 light-minute).  The widget also
 * DRAWS the three faint range rings + labels (`1/60 LS`, `1 LS`,
 * `1 LM`) — into the background snapshot on the 3-buffer path (they
 * are static), per frame on the classic full-composite path.
 *
 * Behaviors (widget-owned — state on the bus is just WHAT is out
 * there):
 *   - DIRECTIONAL MOTION INDICATOR (the decentralized-model test
 *     case): the heading is NOT on the bus.  The widget tracks each
 *     contact by stable `id`, keeps its previous PROJECTED (x, y), and
 *     derives heading = atan2(dy, dx) from the screen-space delta per
 *     update (smoothed across updates so jitter doesn't spin it).  The
 *     indicator asset (bus://assets/icons/dradis/
 *     directional_indicator.bmp, 128x128 BMP32, points DOWN = tip
 *     toward +y) is fetched+decoded ONCE like the type icons and drawn
 *     BEHIND the sprite rotated by heading - pi/2 through the canvas
 *     transform ops (the sweep's rotate path).  Rendered ONLY while
 *     the per-update motion exceeds a small epsilon — a still contact
 *     shows none.
 *   - BLINK on alert: allegiance == hostile AND `demo/alert` truthy
 *     => ~2 Hz blink (icon dimmed on the off phase).
 *   - Allegiance tint: a colored ring under each icon (hostile red /
 *     friendly green / neutral amber) — one stroke_circle per contact,
 *     cheap on this tier.
 *   - HUD tally (`hud: true`): "CONTACTS n   THREATS h" DERIVED from
 *     the array — never a separate namespace.
 *
 * REMOVED 2026-07-16: ping-on-sweep-pass (fired inconsistently, cost
 * horsepower).  The `beam:` binding and the sweep-angle read went with
 * it; the sweep widget still renders as the visual beam but no longer
 * drives any contact behavior.
 *
 * STELLAR BODIES (2026-07-16, contract "Game layer / Stellar bodies"):
 * the widget also renders the `dradis/bodies` namespace — a JSON array
 * of stellar objects ({id, kind: star|planet|asteroid, type, name,
 * bearing, range_ls, size}) — as a BACKDROP: log-projected with the
 * exact same project_radius/bearing math as contacts (they live on the
 * same scope and DO sweep inward as own-ship closes), drawn UNDER the
 * ship contacts and ABOVE the beam/rings, with NO threat blink, NO
 * directional motion indicator, and NO allegiance tint — they are
 * navigation terrain, not tracked targets.  Icons come from
 * bus://assets/icons/stellar/<type>.bmp (192x192 BMP32, ~147 KB — the
 * device-fit downscale; fits the 160 KB fetch cap), fetched+decoded
 * ONCE per type into a SEPARATE cache (main-loop context, never
 * draw()).  A missing/undecodable stellar icon warns once and that
 * body is SKIPPED (no dot fallback — terrain either reads or stays
 * out of the picture); everything else is unaffected.  Default body
 * size 120 px (bigger than a ship's 72) — a planet reads as a disc.
 *
 * Data discipline: empty / "[]" source renders nothing; malformed JSON
 * warns ONCE (re-armed by the next good parse) and renders as empty —
 * never a hang, never log spam.  `bodies:` follows the identical
 * discipline independently (its warn-once flags are its own).
 *
 * .compose.yaml dialect (compose_scene):
 *   type: dradis_contacts
 *   center_x: 360 ; center_y: 360 ; radius: 330
 *   hud: true
 *   body_size: 120               # optional default body px
 *   source: {namespace: dradis/contacts, default: "[]"}
 *   bodies: {namespace: dradis/bodies,   default: "[]"}
 *   alert:  {namespace: demo/alert}
 *
 * Rendering tier plumbing: the widget participates in the per-frame
 * animated overlay pass of the 3-buffer radar path (contacts draw with
 * the beam, NOT into the background snapshot; the range rings draw
 * ONLY into it — see compose_scene scene_draw_sweeps + the
 * SweepRenderPhase gate in draw()).
 */

#ifndef WIDGETS_DRADIS_CONTACTS_H
#define WIDGETS_DRADIS_CONTACTS_H

#include "widget.h"
#include <cstdint>

class DradisContactsWidget : public Widget {
public:
    static constexpr int kMaxContacts = 16;
    static constexpr int kMaxIconTypes = 8;
    static constexpr int kMaxBodies = 12;
    static constexpr int kMaxBodyIconTypes = 6;   /* 192x192 RGBA decodes
                                                     (~147 KB PSRAM each) */

    DradisContactsWidget(const char* id, Canvas* canvas);

    /** Latch the animation clock — once per render pass (the
     * SweepWidget/SpriteWidget idiom; never per band). */
    static void tick();

    /* -- scene-build configuration ---------------------------------- */
    void set_geometry(float cx, float cy, float radius) {
        _cx = cx; _cy = cy; _radius = radius;
    }
    void set_default_size(float px) { _dflt_size = px > 4.0f ? px : 4.0f; }
    void set_default_body_size(float px) {
        _dflt_body_size = px > 4.0f ? px : 4.0f;
    }
    void set_hud(bool on) { _hud = on; }

    /* -- reactive attribute setters (apply_binding_value) ------------ */
    /** `source:` — the JSON contact array (parsed here; icons for new
     * types are fetched+decoded here too, once per type — main-loop
     * context, never draw()).  Projection + heading derivation also
     * happen here, once per update — draw() only reads. */
    void set_source(const char* json);
    /** `bodies:` — the stellar-bodies JSON array (dradis/bodies).
     * Same parse/warm/project-once discipline as set_source; bodies
     * carry NO motion state (no id tracking — terrain, not targets). */
    void set_bodies(const char* json);
    /** `alert:` — truthy gates the hostile 2 Hz blink. */
    void set_alert(bool on) { _alert = on; }
    /** `own_heading:` — own-ship (Galactica, scope center) heading in
     * DEGREES from the bus.  Own-ship doesn't move on screen so its
     * heading can't be derived from motion; when set, the SAME
     * directional indicator is drawn at scope center rotated to
     * own_heading - 90 deg (same convention as the contact indicators).
     * Warms the indicator asset here (main-loop context). */
    void set_own_heading(float degrees);

    /** Release cached decoded icons incl. the directional indicator
     * (scene_free hook — mirrors the image/sprite lifecycle). */
    void free_icons(Canvas& canvas);

    int contact_count() const { return _count; }
    int body_count() const { return _body_count; }

    Size measure(const Constraints& constraints) override;
    void draw(Canvas& canvas, const Rect& rect) override;

private:
    static constexpr int kMaxLabelLen = 48;   /* contact label cap */

    struct Contact {
        char id[32];
        char type[32];
        char label[kMaxLabelLen]; /* optional caption below the icon
                                     ("\n" = line break); "" = none */
        float bearing;        /* deg, 0=up, CW+ */
        float range;          /* LIGHT-SECONDS, > 0 (log-projected) */
        float size;           /* icon px (square) */
        float px, py;         /* projected screen pos (this update) */
        float heading;        /* rad, screen-space motion dir (smoothed) */
        uint8_t allegiance;   /* 0 neutral, 1 friendly, 2 hostile */
        bool heading_valid;   /* heading has ever been derived */
        bool moving;          /* last update moved > epsilon */
    };
    struct Body {
        char type[32];        /* stellar icon key (stellar/<type>.bmp) */
        float bearing;        /* deg, 0=up, CW+ */
        float range;          /* LIGHT-SECONDS (range_ls, log-projected) */
        float size;           /* icon px (square), default 120 */
        float px, py;         /* projected screen pos (this update) */
    };
    struct Icon {
        char type[32];
        ImageHandle handle;   /* nullptr when fetch/decode failed */
        bool failed;
    };

    /* Shared fetch+decode+cache path (one HTTP fetch per type, keyed
     * by type within a cache; `dir` picks the bus asset directory —
     * "dradis" for ship contacts, "stellar" for bodies). */
    static ImageHandle icon_lookup(const Icon* icons, int count,
                                   const char* type);
    void icon_warm(Icon* icons, int& count, int cap, const char* dir,
                   const char* type, bool& warned_full);
    void indicator_warm();
    float project_radius(float range_ls) const;
    void draw_rings(Canvas& canvas) const;

    Canvas* _canvas;          /* decode/free seam (scene's canvas) */
    float _cx = 360.0f, _cy = 360.0f, _radius = 330.0f;
    float _dflt_size = 72.0f;
    bool _hud = false;
    bool _alert = false;
    float _own_heading = 0.0f;      /* degrees, bus-driven (own_heading) */
    bool _own_heading_set = false;  /* absent/unset -> no arrow */

    float _dflt_body_size = 120.0f;     /* bodies read BIGGER than ships */

    Contact _contacts[kMaxContacts];
    int _count = 0;

    Body _bodies[kMaxBodies];
    int _body_count = 0;

    Icon _icons[kMaxIconTypes];
    int _icon_count = 0;

    Icon _body_icons[kMaxBodyIconTypes];   /* stellar/ cache (separate:
                                              different asset dir) */
    int _body_icon_count = 0;

    ImageHandle _indicator = nullptr;   /* directional_indicator.bmp */
    bool _indicator_tried = false;      /* fetch attempted (warn-once) */

    bool _warned_malformed = false;
    bool _warned_overflow = false;
    bool _warned_icons_full = false;
    bool _warned_bodies_malformed = false;
    bool _warned_bodies_overflow = false;
    bool _warned_body_icons_full = false;
};

#endif /* WIDGETS_DRADIS_CONTACTS_H */
