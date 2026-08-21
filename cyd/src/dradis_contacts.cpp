/*
 * dradis_contacts.cpp — DRADIS contacts widget implementation.
 * See include/widgets/dradis_contacts.h; normative contract:
 * docs/dradis/contacts-contract.md (LOG projection, 2026-07-16).
 *
 * Compiled ONLY on the framebuffer tier (RENDER_TIER_TRANSFORMS —
 * qualia_display); the band tier renders its honest placeholder.
 */

#include "widgets/dradis_contacts.h"
#include "widgets/sweep.h"        /* SweepRenderPhase gate (3-buffer path) */
#include "config_fetch.h"         /* http_fetch — the image widget's path */

#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>

#include "cJSON.h"
#include "esp_log.h"
#include "esp_timer.h"

static const char* TAG = "dradis_contacts";

/* Behavior tuning (contract: ~2 Hz hostile blink; motion indicator
 * only above a small screen-space epsilon, heading smoothed across a
 * few updates so bus jitter doesn't spin it). */
static constexpr int kBlinkHalfPeriodMs = 250;   /* 2 Hz cycle */
/* Icon fetch cap: 160 KB (the image widget's cap).  Sized for the
 * LARGEST bus icon class: stellar bodies are 192x192 BMP32 ~147 KB
 * (contract "Device fit", 2026-07-16 — assets are downscaled to fit
 * this cap, per David; the cap does not grow).  Ship icons ~65 KB. */
static constexpr size_t kIconFetchCap = 160 * 1024;
static constexpr float kMotionEpsPx = 0.5f;      /* per-update px */
static constexpr float kHeadingSmooth = 0.45f;   /* EMA toward new heading */
static constexpr float kIndicatorScale = 1.75f;  /* x contact size (72 -> 126,
                                                    near the 128px asset) */
static constexpr float kOwnHeadingPx = 112.5f;   /* own-ship arrow size
                                                    (+25%: was 90, the
                                                    artwork size — hard
                                                    to see) */
static constexpr float kLabelPx = 14.0f;         /* contact caption size */
static constexpr float kLabelLineH = 16.0f;      /* multi-line spacing */
static constexpr float kLabelGap = 6.0f;         /* icon-bottom -> text */
static constexpr float kLog60 = 1.7781512503836436f;  /* log10(60) */

static const char* kIndicatorUri =
    "bus://assets/icons/dradis/directional_indicator.bmp";

/* Clock latched once per render pass (SweepWidget idiom). */
static int64_t s_now_ms = 0;

void DradisContactsWidget::tick() {
    s_now_ms = esp_timer_get_time() / 1000;
}

DradisContactsWidget::DradisContactsWidget(const char* id, Canvas* canvas)
    : Widget(id, "dradis_contacts"), _canvas(canvas) {
    memset(_contacts, 0, sizeof(_contacts));
    memset(_bodies, 0, sizeof(_bodies));
    memset(_icons, 0, sizeof(_icons));
    memset(_body_icons, 0, sizeof(_body_icons));
}

Size DradisContactsWidget::measure(const Constraints& c) {
    return Size{c.max_width, c.max_height};
}

/* ------------------------------------------------------------------ */
/* Icon cache — fetch ONCE per type via the image widget's exact path  */
/* (http_fetch -> canvas.decode_image; BMP32 carries alpha)            */
/* ------------------------------------------------------------------ */

/* Icon keys must be asset-path-safe: [a-z A-Z 0-9 _ -] only. */
static bool type_key_safe(const char* t) {
    if (!t || !t[0]) return false;
    for (const char* p = t; *p; p++) {
        char c = *p;
        if (!((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
              (c >= '0' && c <= '9') || c == '_' || c == '-'))
            return false;
    }
    return true;
}

ImageHandle DradisContactsWidget::icon_lookup(const Icon* icons, int count,
                                              const char* type) {
    for (int i = 0; i < count; i++)
        if (strcmp(icons[i].type, type) == 0) return icons[i].handle;
    return nullptr;
}

/* One fetch+decode per type into *icons* (keyed by type; a failed slot
 * is remembered so the fetch never repeats).  `dir` selects the bus
 * asset directory: icons/dradis/ (ship contacts, ~65 KB BMP32) or
 * icons/stellar/ (bodies, 192x192 BMP32 ~147 KB) — both fit under
 * kIconFetchCap.  Main-loop context only; draw() never fetches. */
void DradisContactsWidget::icon_warm(Icon* icons, int& count, int cap,
                                     const char* dir, const char* type,
                                     bool& warned_full) {
    for (int i = 0; i < count; i++)
        if (strcmp(icons[i].type, type) == 0) return;    /* cached (or
                                                            failed once) */
    if (count >= cap) {
        if (!warned_full) {
            ESP_LOGW(TAG, "'%s': %s icon cache full (%d types) — '%s' "
                     "degraded", widget_id(), dir, cap, type);
            warned_full = true;
        }
        return;
    }
    if (!type_key_safe(type)) {
        ESP_LOGW(TAG, "'%s': %s type '%s' is not an asset-safe key — "
                 "degraded", widget_id(), dir, type);
        Icon& slot = icons[count++];
        snprintf(slot.type, sizeof(slot.type), "%s", type);
        slot.handle = nullptr;
        slot.failed = true;
        return;
    }

    char uri[128];
    snprintf(uri, sizeof(uri), "bus://assets/icons/%s/%s.bmp", dir, type);
    size_t alen = 0;
    char* bytes = http_fetch(uri, &alen, kIconFetchCap);
    ImageHandle h = nullptr;
    if (bytes && _canvas) {
        h = _canvas->decode_image(reinterpret_cast<const uint8_t*>(bytes),
                                  alen);
    }
    free(bytes);
    if (!h) {
        ESP_LOGW(TAG, "'%s': icon %s %s — degraded", widget_id(), uri,
                 bytes == nullptr ? "fetch failed" : "undecodable "
                 "(BMP24/32 + CI16 on this tier)");
    } else {
        ESP_LOGI(TAG, "'%s': icon '%s' cached (%s)", widget_id(), type, uri);
    }
    Icon& slot = icons[count++];
    snprintf(slot.type, sizeof(slot.type), "%s", type);
    slot.handle = h;
    slot.failed = (h == nullptr);
}

/* Directional-indicator asset — fetched ONCE (same path as the type
 * icons; main-loop context).  A missing/undecodable asset degrades to
 * "no motion indicators" with a single warn — contacts still render. */
void DradisContactsWidget::indicator_warm() {
    if (_indicator_tried) return;
    _indicator_tried = true;
    size_t alen = 0;
    char* bytes = http_fetch(kIndicatorUri, &alen, kIconFetchCap);
    if (bytes && _canvas) {
        _indicator = _canvas->decode_image(
            reinterpret_cast<const uint8_t*>(bytes), alen);
    }
    free(bytes);
    if (!_indicator) {
        ESP_LOGW(TAG, "'%s': %s %s — motion indicators disabled",
                 widget_id(), kIndicatorUri,
                 bytes == nullptr ? "fetch failed" : "undecodable");
    } else {
        ESP_LOGI(TAG, "'%s': directional indicator cached (%s)",
                 widget_id(), kIndicatorUri);
    }
}

void DradisContactsWidget::free_icons(Canvas& canvas) {
    for (int i = 0; i < _icon_count; i++) {
        if (_icons[i].handle) canvas.free_image(_icons[i].handle);
        _icons[i].handle = nullptr;
    }
    _icon_count = 0;
    for (int i = 0; i < _body_icon_count; i++) {
        if (_body_icons[i].handle) canvas.free_image(_body_icons[i].handle);
        _body_icons[i].handle = nullptr;
    }
    _body_icon_count = 0;
    if (_indicator) canvas.free_image(_indicator);
    _indicator = nullptr;
    _indicator_tried = false;
}

/* ------------------------------------------------------------------ */
/* Projection — LOGARITHMIC (contract 2026-07-16)                      */
/* ------------------------------------------------------------------ */

/* radius_px is linear in log10(range_ls), anchored on the three rings
 * (R/3, 2R/3, R) <- (1/60 ls, 1 ls, 60 ls), generalized from the
 * configured radius so it scales:
 *
 *   radius_px = clamp(2R/3 + (R/3) * log10(range_ls)/log10(60), 0, R)
 *
 * (R=330: 220 + 61.86*log10(range) — the contract's spelled form.)
 * range <= 0 (or missing) clamps to center; > 60 ls clamps to edge. */
float DradisContactsWidget::project_radius(float range_ls) const {
    if (!(range_ls > 0.0f)) return 0.0f;   /* also catches NaN */
    float r = _radius * (2.0f / 3.0f) +
              (_radius / 3.0f) * log10f(range_ls) / kLog60;
    if (r < 0.0f) return 0.0f;
    if (r > _radius) return _radius;
    return r;
}

/* ------------------------------------------------------------------ */
/* Source parsing (contract schema; warn-once on malformed)            */
/* ------------------------------------------------------------------ */

static uint8_t parse_allegiance(const char* s) {
    if (s) {
        if (strcmp(s, "hostile") == 0) return 2;
        if (strcmp(s, "friendly") == 0) return 1;
    }
    return 0;   /* neutral (contract default) */
}

/* Wrapped angular distance, radians in [-pi, pi). */
static float ang_diff_rad(float a, float b) {
    float d = fmodf(a - b, 2.0f * (float)M_PI);
    if (d < -(float)M_PI) d += 2.0f * (float)M_PI;
    if (d >= (float)M_PI) d -= 2.0f * (float)M_PI;
    return d;
}

void DradisContactsWidget::set_source(const char* json) {
    /* Empty source == empty picture, silently (contract). */
    if (!json || !json[0]) {
        _count = 0;
        _warned_malformed = false;
        return;
    }

    cJSON* root = cJSON_Parse(json);
    if (!root || !cJSON_IsArray(root)) {
        if (!_warned_malformed) {
            ESP_LOGW(TAG, "'%s': malformed contacts JSON (%.48s%s) — "
                     "rendering empty (warn-once)", widget_id(), json,
                     strlen(json) > 48 ? "..." : "");
            _warned_malformed = true;   /* re-armed by the next good parse */
        }
        if (root) cJSON_Delete(root);
        _count = 0;
        return;
    }
    _warned_malformed = false;

    /* Carry motion state across updates by stable contact id (the
     * widget TRACKS contacts; it does not respawn them per publish).
     * The previous PROJECTED (px, py) is the heading source. */
    Contact prev[kMaxContacts];
    int prev_count = _count;
    memcpy(prev, _contacts, sizeof(Contact) * (size_t)prev_count);

    int n = 0;
    int dropped = 0;
    for (cJSON* it = root->child; it; it = it->next) {
        if (!cJSON_IsObject(it)) { dropped++; continue; }
        if (n >= kMaxContacts) { dropped++; continue; }

        const cJSON* jid = cJSON_GetObjectItemCaseSensitive(it, "id");
        const cJSON* jtype = cJSON_GetObjectItemCaseSensitive(it, "type");
        const cJSON* jbear = cJSON_GetObjectItemCaseSensitive(it, "bearing");
        const cJSON* jrange = cJSON_GetObjectItemCaseSensitive(it, "range");
        const cJSON* jalleg =
            cJSON_GetObjectItemCaseSensitive(it, "allegiance");
        const cJSON* jsize = cJSON_GetObjectItemCaseSensitive(it, "size");
        const cJSON* jlabel = cJSON_GetObjectItemCaseSensitive(it, "label");

        if (!cJSON_IsString(jtype) || !jtype->valuestring) {
            dropped++;
            continue;   /* no type -> no icon key -> skip the entry */
        }

        Contact& c = _contacts[n];
        memset(&c, 0, sizeof(c));
        snprintf(c.type, sizeof(c.type), "%s", jtype->valuestring);
        /* optional caption below the icon; "\n" splits lines at draw.
         * memset above already left it "" when absent/non-string. */
        if (cJSON_IsString(jlabel) && jlabel->valuestring)
            snprintf(c.label, sizeof(c.label), "%s", jlabel->valuestring);
        snprintf(c.id, sizeof(c.id), "%s",
                 (cJSON_IsString(jid) && jid->valuestring) ? jid->valuestring
                                                           : c.type);
        c.bearing = cJSON_IsNumber(jbear) ? (float)jbear->valuedouble : 0.0f;
        /* range: LIGHT-SECONDS, float > 0 (contract 2026-07-16).  The
         * log projection clamps <=0 to center and >60 ls to the edge. */
        c.range = cJSON_IsNumber(jrange) ? (float)jrange->valuedouble : 0.0f;
        c.allegiance = parse_allegiance(
            cJSON_IsString(jalleg) ? jalleg->valuestring : nullptr);
        c.size = (cJSON_IsNumber(jsize) && jsize->valuedouble > 4.0)
                     ? (float)jsize->valuedouble : _dflt_size;

        /* Project ONCE per update (draw() only reads) — the log-scaled
         * screen position is also the heading-derivation input. */
        float th = c.bearing * (float)M_PI / 180.0f;
        float rad_px = project_radius(c.range);
        c.px = _cx + rad_px * sinf(th);
        c.py = _cy - rad_px * cosf(th);

        /* Derive the motion heading LOCALLY from the screen-space delta
         * vs. this id's previous projected position (contract behavior
         * 1 — the heading is NOT on the bus). */
        c.heading = 0.0f;
        c.heading_valid = false;
        c.moving = false;
        for (int p = 0; p < prev_count; p++) {
            if (strcmp(prev[p].id, c.id) != 0) continue;
            float dx = c.px - prev[p].px;
            float dy = c.py - prev[p].py;
            c.heading = prev[p].heading;
            c.heading_valid = prev[p].heading_valid;
            if (dx * dx + dy * dy > kMotionEpsPx * kMotionEpsPx) {
                float target = atan2f(dy, dx);
                if (c.heading_valid) {
                    /* Smooth across updates (wrap-aware EMA) so bus
                     * jitter doesn't spin the indicator. */
                    c.heading += kHeadingSmooth *
                                 ang_diff_rad(target, c.heading);
                } else {
                    c.heading = target;   /* first motion: snap */
                }
                c.heading_valid = true;
                c.moving = true;
            }
            break;
        }
        n++;
    }
    _count = n;
    cJSON_Delete(root);

    if (dropped > 0 && !_warned_overflow) {
        ESP_LOGW(TAG, "'%s': %d contact entrie(s) dropped (no type / not "
                 "an object / > %d cap) — warn-once", widget_id(), dropped,
                 kMaxContacts);
        _warned_overflow = true;
    } else if (dropped == 0) {
        _warned_overflow = false;
    }

    /* Warm the icon cache for any new types NOW (main-loop context —
     * a one-time HTTP fetch per type; draw() only ever reads cache). */
    for (int i = 0; i < _count; i++)
        icon_warm(_icons, _icon_count, kMaxIconTypes, "dradis",
                  _contacts[i].type, _warned_icons_full);
    if (_count > 0) indicator_warm();
}

/* Own-ship heading (Galactica at scope center) — from the bus in
 * DEGREES.  Warm the indicator asset here (main-loop context, never
 * draw()) so the arrow can render even in a scene with zero contacts. */
void DradisContactsWidget::set_own_heading(float degrees) {
    _own_heading = degrees;
    _own_heading_set = true;
    indicator_warm();
}

/* ------------------------------------------------------------------ */
/* Stellar bodies (contract "Game layer / Stellar bodies", 2026-07-16) */
/* ------------------------------------------------------------------ */

/* `bodies:` — the dradis/bodies JSON array: [{id, kind, type, name,
 * bearing, range_ls, size}].  Same discipline as set_source (empty ==
 * empty picture; malformed warns ONCE, re-armed by the next good
 * parse; project ONCE here, draw() only reads) but bodies are TERRAIN:
 * no id tracking, no heading derivation, no allegiance.  `kind` and
 * `name` are semantic properties for bus peers — this widget renders
 * from `type` alone and ignores them tolerantly. */
void DradisContactsWidget::set_bodies(const char* json) {
    if (!json || !json[0]) {
        _body_count = 0;
        _warned_bodies_malformed = false;
        return;
    }

    cJSON* root = cJSON_Parse(json);
    if (!root || !cJSON_IsArray(root)) {
        if (!_warned_bodies_malformed) {
            ESP_LOGW(TAG, "'%s': malformed bodies JSON (%.48s%s) — "
                     "rendering empty (warn-once)", widget_id(), json,
                     strlen(json) > 48 ? "..." : "");
            _warned_bodies_malformed = true;
        }
        if (root) cJSON_Delete(root);
        _body_count = 0;
        return;
    }
    _warned_bodies_malformed = false;

    int n = 0;
    int dropped = 0;
    for (cJSON* it = root->child; it; it = it->next) {
        if (!cJSON_IsObject(it)) { dropped++; continue; }
        if (n >= kMaxBodies) { dropped++; continue; }

        const cJSON* jtype = cJSON_GetObjectItemCaseSensitive(it, "type");
        const cJSON* jbear = cJSON_GetObjectItemCaseSensitive(it, "bearing");
        const cJSON* jrange =
            cJSON_GetObjectItemCaseSensitive(it, "range_ls");
        if (!cJSON_IsNumber(jrange))   /* tolerate the contacts spelling */
            jrange = cJSON_GetObjectItemCaseSensitive(it, "range");
        const cJSON* jsize = cJSON_GetObjectItemCaseSensitive(it, "size");

        if (!cJSON_IsString(jtype) || !jtype->valuestring) {
            dropped++;
            continue;   /* no type -> no icon key -> skip the entry */
        }

        Body& b = _bodies[n];
        memset(&b, 0, sizeof(b));
        snprintf(b.type, sizeof(b.type), "%s", jtype->valuestring);
        b.bearing = cJSON_IsNumber(jbear) ? (float)jbear->valuedouble : 0.0f;
        b.range = cJSON_IsNumber(jrange) ? (float)jrange->valuedouble : 0.0f;
        b.size = (cJSON_IsNumber(jsize) && jsize->valuedouble > 4.0)
                     ? (float)jsize->valuedouble : _dflt_body_size;

        /* Log-projected EXACTLY like contacts — same scope, same
         * project_radius, same bearing->(x,y) math. */
        float th = b.bearing * (float)M_PI / 180.0f;
        float rad_px = project_radius(b.range);
        b.px = _cx + rad_px * sinf(th);
        b.py = _cy - rad_px * cosf(th);
        n++;
    }
    _body_count = n;
    cJSON_Delete(root);

    if (dropped > 0 && !_warned_bodies_overflow) {
        ESP_LOGW(TAG, "'%s': %d body entrie(s) dropped (no type / not an "
                 "object / > %d cap) — warn-once", widget_id(), dropped,
                 kMaxBodies);
        _warned_bodies_overflow = true;
    } else if (dropped == 0) {
        _warned_bodies_overflow = false;
    }

    /* Warm the STELLAR icon cache for any new types NOW (main-loop
     * context; a 147 KB fetch per type, once — never in draw()). */
    for (int i = 0; i < _body_count; i++)
        icon_warm(_body_icons, _body_icon_count, kMaxBodyIconTypes,
                  "stellar", _bodies[i].type, _warned_body_icons_full);
}

/* ------------------------------------------------------------------ */
/* Draw                                                                */
/* ------------------------------------------------------------------ */

/* Range rings (contract): three faint circles at R/3, 2R/3, R = the
 * 1/60 ls, 1 ls, 1 light-minute anchors of the log scale, with small
 * dim labels just inside each ring's top.  Static geometry — on the
 * 3-buffer path they bake into the background snapshot (GridOnly). */
void DradisContactsWidget::draw_rings(Canvas& canvas) const {
    struct Ring { float frac; const char* label; };
    static constexpr Ring kRings[] = {
        {1.0f / 3.0f, "1/60 LS"},
        {2.0f / 3.0f, "1 LS"},
        {1.0f, "1 LM"},
    };
    canvas.save();
    canvas.set_stroke_width(1.0f);
    for (const Ring& ring : kRings) {
        float r = _radius * ring.frac;
        canvas.set_stroke_color(0x22, 0xFF, 0x88, 36);
        canvas.stroke_circle(_cx, _cy, r);
        canvas.set_font("sans-serif", 12.0f, "regular");
        canvas.set_fill_color(0x22, 0xFF, 0x88, 96);
        canvas.draw_text(ring.label, _cx, _cy - r + 14.0f, "center");
    }
    canvas.restore();
}

void DradisContactsWidget::draw(Canvas& canvas, const Rect& rect) {
    (void)rect;   /* geometry is contract-config (center/radius) */

    /* 3-buffer radar path: the range rings are STATIC — drawn in the
     * background-snapshot pass (GridOnly) and in the classic Full
     * composite, never per animated frame (BeamOnly).  Contacts are
     * the opposite: a PER-FRAME overlay (they move/blink), skipped
     * while the snapshot is being built. */
    SweepRenderPhase phase = SweepWidget::render_phase();
    if (phase != SweepRenderPhase::BeamOnly) draw_rings(canvas);
    if (phase == SweepRenderPhase::GridOnly) return;

    /* STELLAR BODIES — the backdrop layer: beam/rings under, ship
     * contacts over (drawn next), own-ship on top (scene order).
     * Bodies are terrain: no blink, no motion indicator, no tint —
     * just the icon, log-projected on the same scope.  They ride this
     * per-frame overlay pass because they DO move on the own-ship-
     * centric scope as the fleet closes.  A missing/undecodable
     * stellar icon was warn-onced at warm time and its body is
     * SKIPPED (no dot fallback for terrain). */
    for (int i = 0; i < _body_count; i++) {
        const Body& b = _bodies[i];
        ImageHandle icon = icon_lookup(_body_icons, _body_icon_count,
                                       b.type);
        if (!icon) continue;
        canvas.draw_image(icon, b.px - b.size / 2.0f, b.py - b.size / 2.0f,
                          b.size, b.size);
    }

    /* OWN-SHIP HEADING ARROW (2026-07-16): Galactica sits at scope
     * center (range 0) and never moves on screen, so its heading is not
     * derivable from motion — it comes from the bus (own_heading, deg).
     * When set, draw the SAME directional indicator at CENTER, rotated
     * own_heading - 90 (same convention as the contact indicators; the
     * asset points DOWN by default).  Drawn here — before contacts and
     * under the separate battlestar image widget — so it reads as the
     * arrow BEHIND the ship. */
    if (_own_heading_set && _indicator) {
        canvas.save();
        canvas.translate(_cx, _cy);
        canvas.rotate((_own_heading - 90.0f) * (float)M_PI / 180.0f);
        canvas.draw_image(_indicator, -kOwnHeadingPx / 2.0f,
                          -kOwnHeadingPx / 2.0f, kOwnHeadingPx,
                          kOwnHeadingPx);
        canvas.restore();
    }

    const bool blink_off =
        _alert && ((s_now_ms / kBlinkHalfPeriodMs) % 2 != 0);

    int threats = 0;

    for (int i = 0; i < _count; i++) {
        Contact& c = _contacts[i];
        if (c.allegiance == 2) threats++;

        /* Projected in set_source (log scale); draw only reads. */
        float x = c.px;
        float y = c.py;
        float sz = c.size;

        canvas.save();
        if (c.allegiance == 2 && blink_off)
            canvas.set_opacity(0.25f);   /* hostile blink (dim phase) */

        /* Directional motion indicator FIRST (behind the sprite):
         * asset points DOWN (+y) by default, so rotate by
         * heading - pi/2 — the sweep's exact canvas-transform path
         * (save/translate/rotate; draw_image inverse-maps). */
        if (c.moving && c.heading_valid && _indicator) {
            float isz = sz * kIndicatorScale;
            canvas.save();
            canvas.translate(x, y);
            canvas.rotate(c.heading - (float)M_PI / 2.0f);
            canvas.draw_image(_indicator, -isz / 2.0f, -isz / 2.0f,
                              isz, isz);
            canvas.restore();
        }

        /* Allegiance tint RING removed 2026-07-16 (David: the thin
         * colored circle around each contact is gone for ALL
         * allegiances).  The hostile 2 Hz blink above is separate and
         * KEPT; the icons carry the identity.  The allegiance color is
         * retained ONLY for the dot fallback when an icon is missing. */
        ImageHandle icon = icon_lookup(_icons, _icon_count, c.type);
        if (icon) {
            canvas.draw_image(icon, x - sz / 2.0f, y - sz / 2.0f, sz, sz);
        } else {
            /* Missing/undecodable icon: a small allegiance-colored dot
             * so the contact still reads (hostile red / friendly green /
             * neutral amber). */
            uint8_t rr = 0xFF, rg = 0xB0, rb = 0x30;          /* neutral */
            if (c.allegiance == 2) { rr = 0xFF; rg = 0x30; rb = 0x30; }
            else if (c.allegiance == 1) { rr = 0x30; rg = 0xFF; rb = 0x70; }
            canvas.set_fill_color(rr, rg, rb, 230);
            canvas.fill_circle(x, y, 6.0f);
        }

        /* CONTACT LABEL (2026-07-16): optional caption BELOW the icon,
         * centered on the contact x, in the COMMP pixel font ~14px.
         * Multi-line: split on "\n" and stack the lines centered
         * ("ENEMY\nSQUADRON 2" -> two centered lines).  Absent/empty
         * label -> nothing. */
        if (c.label[0]) {
            canvas.set_font("commp", kLabelPx, "regular");
            canvas.set_fill_color(0xCF, 0xF5, 0xE3, 235);
            float base_y = y + sz * 0.5f + kLabelGap + kLabelPx;
            char line[kMaxLabelLen];
            int li = 0;
            for (const char* p = c.label;; p++) {
                if (*p == '\n' || *p == '\0') {
                    line[li] = '\0';
                    if (li > 0) canvas.draw_text(line, x, base_y, "center");
                    base_y += kLabelLineH;   /* advance even on blank
                                                lines so stacking stays */
                    li = 0;
                    if (*p == '\0') break;
                } else if (li < kMaxLabelLen - 1) {
                    line[li++] = *p;
                }
            }
        }
        canvas.restore();
    }

    /* HUD tally — DERIVED from the array (never a bus namespace). */
    if (_hud) {
        char hud[48];
        snprintf(hud, sizeof(hud), "CONTACTS %d   THREATS %d",
                 _count, threats);
        canvas.save();
        canvas.set_font("sans-serif", 16.0f, "bold");
        canvas.set_fill_color(0x7F, 0xFF, 0xB8,
                              (_alert && blink_off) ? 140 : 220);
        canvas.draw_text(hud, _cx, _cy + _radius - 26.0f, "center");
        canvas.restore();
    }
}
