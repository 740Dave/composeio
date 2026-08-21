/*
 * gauge_bind.cpp — see gauge_bind.h.  Pure C++; host-proven by
 * env:native_gauge.
 */

#include "gauge_bind.h"
#include "yaml_min.h"

#include <cstdio>
#include <cstring>

/* Logging seam: ESP_LOGx in firmware, printf on the host.  Kept trivial so
 * this TU stays free of ESP-IDF. */
#ifdef ESP_PLATFORM
#include "esp_log.h"
#define GB_W(...) ESP_LOGW("gauge_bind", __VA_ARGS__)
#define GB_I(...) ESP_LOGI("gauge_bind", __VA_ARGS__)
#else
#define GB_W(...) do { printf("W gauge_bind: "); printf(__VA_ARGS__); printf("\n"); } while (0)
#define GB_I(...) do { printf("I gauge_bind: "); printf(__VA_ARGS__); printf("\n"); } while (0)
#endif

namespace {

void add_channel(InstanceBinding& ib, RealizationBackend& be,
                 const char* channel, const char* ns, float lo, float hi,
                 GaugeBindings* out) {
    if (!ns || !*ns) { out->channels_unbound++; return; }
    if (ib.count >= kMaxChannelsPerInstance) return;

    switch (be.realizes(channel)) {
    case ChannelSupport::Realized:
        break;
    case ChannelSupport::NotApplicable:
        /* SILENT, and deliberately.  A raster gauge has no `illum`; saying
         * so once per scene is noise and saying so per value is the
         * "diagnostic that causes the failure it reports" shape. */
        return;
    case ChannelSupport::Unknown:
        /* LOUD, once, here.  This is a scene/firmware mismatch — the only
         * reason the support answer is three-valued instead of two. */
        GB_W("gauge %d: channel '%s' -> '%s' is UNKNOWN to backend '%s'. "
             "The scene declares an actuator this firmware has never heard "
             "of; the value will be silently ignored at runtime unless you "
             "fix one of the two.",
             ib.instance, channel, ns, be.name());
        out->channels_unknown++;
        return;
    }

    ChannelBinding& cb = ib.ch[ib.count++];
    cb.channel = channel;
    cb.ns = ns;
    cb.lo = lo;
    cb.hi = hi;
}

void bind_one_gauge(const YamlNode* w, RealizationBackend& be,
                    GaugeBindings* out) {
    out->gauges_seen++;

    if (out->count >= be.instance_count() ||
        out->count >= kMaxGaugeInstances) {
        out->gauges_dropped++;
        /* LOUD.  "A limit sized exactly for today" is this fleet's first
         * named failure shape (kMaxPersist was 4 for a control plane of
         * exactly 4, and the whole symptom was one WARN line).  A fourth
         * gauge widget on a three-gauge board is a real authoring error and
         * it gets a real error. */
        GB_W("*** scene declares gauge widget #%d but this board has only "
             "%d physical gauges — DROPPED.  Nothing downstream will "
             "mention it again. ***",
             out->gauges_seen, be.instance_count());
        return;
    }

    InstanceBinding& ib = out->inst[out->count];
    ib.instance = out->count;
    ib.count = 0;

    const float lo = w->get_float("min_val", 0.0f);
    const float hi = w->get_float("max_val", 1.0f);

    /* 1. explicit channel set wins */
    const YamlNode* chans = w->get("channels");
    bool got_value = false;
    if (chans && chans->type == YamlNode::Type::Map) {
        for (const YamlNode* c = chans->first_child; c; c = c->next_sibling) {
            if (!c->key) continue;
            const char* ns = nullptr;
            float clo = lo, chi = hi;
            if (c->type == YamlNode::Type::Scalar) {
                ns = c->scalar;                 /* value: throttle/left */
            } else {
                ns  = c->get_str("namespace", nullptr);
                clo = c->get_float("min", lo);
                chi = c->get_float("max", hi);
            }
            if (!strcmp(c->key, "value")) got_value = true;
            add_channel(ib, be, c->key, ns, clo, chi, out);
        }
    }

    /* 2. legacy `namespace:` is sugar for channels.value — applied ONLY if
     *    the explicit form did not already provide it.  Stated precedence,
     *    because two sources for one fact without one is a defect nobody
     *    can attribute. */
    if (!got_value) {
        const char* ns = w->get_str("namespace", nullptr);
        add_channel(ib, be, "value", ns, lo, hi, out);
    }

    if (ib.count == 0) {
        GB_W("gauge widget #%d ('%s') bound NOTHING — no `namespace:` and "
             "no realizable `channels:`.  This needle will never move.",
             out->gauges_seen, w->get_str("id", "?"));
        return;
    }

    for (int i = 0; i < ib.count; i++)
        GB_I("gauge %d.%s <- %s  [%g..%g]", ib.instance, ib.ch[i].channel,
             ib.ch[i].ns, (double)ib.ch[i].lo, (double)ib.ch[i].hi);

    out->count++;
}

void walk(const YamlNode* n, RealizationBackend& be, GaugeBindings* out) {
    for (; n; n = n->next_sibling) {
        if (n->type == YamlNode::Type::Map) {
            const char* t = n->get_str("type", nullptr);
            if (t && !strcmp(t, "gauge")) {
                bind_one_gauge(n, be, out);
                continue;              /* a gauge has no gauge children */
            }
        }
        if (n->first_child) walk(n->first_child, be, out);
    }
}

}  /* namespace */

void gauge_bind(const YamlNode* root, RealizationBackend& be,
                GaugeBindings* out) {
    *out = GaugeBindings();
    if (!root) return;
    walk(root->type == YamlNode::Type::Scalar ? nullptr
                                              : root->first_child, be, out);

    if (out->count == 0)
        GB_W("*** NO GAUGE WIDGETS BOUND.  The scene parsed but contains no "
             "`type: gauge` this board can realize; all %d needles will sit "
             "at zero after homing.  That is INDISTINGUISHABLE on the dial "
             "from a working board reading zero — check this line, not the "
             "needles. ***", be.instance_count());
    else
        GB_I("bound %d of %d gauges (%d seen, %d dropped, %d unknown "
             "channels, %d unbound)",
             out->count, be.instance_count(), out->gauges_seen,
             out->gauges_dropped, out->channels_unknown,
             out->channels_unbound);
}

size_t GaugeBindings::namespaces(const char** out, size_t cap) const {
    size_t n = 0;
    for (int i = 0; i < count; i++) {
        for (int c = 0; c < inst[i].count; c++) {
            const char* ns = inst[i].ch[c].ns;
            bool dup = false;
            for (size_t k = 0; k < n; k++)
                if (!strcmp(out[k], ns)) { dup = true; break; }
            if (dup) continue;
            if (n >= cap) return n;
            out[n++] = ns;
        }
    }
    return n;
}

bool GaugeBindings::dispatch(RealizationBackend& be, const char* ns,
                             double value) const {
    bool hit = false;
    /* NOT an early return: one namespace may legitimately drive several
     * instances AND several channels — that IS the "one semantic value,
     * many physical realizations" claim, expressed inside a single board.
     * `throttle/left` driving both a needle and its own backlight is the
     * smallest possible instance of the pillar. */
    for (int i = 0; i < count; i++) {
        for (int c = 0; c < inst[i].count; c++) {
            const ChannelBinding& cb = inst[i].ch[c];
            if (strcmp(cb.ns, ns)) continue;
            be.set_channel(inst[i].instance, cb.channel, cb.to_unit(value));
            hit = true;
        }
    }
    return hit;
}
