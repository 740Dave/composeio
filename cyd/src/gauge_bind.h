/*
 * gauge_bind.h — turn a fetched .compose.yaml into the CHANNEL-SET binding
 * table a RealizationBackend consumes.  [CXC-VISION-SEMANTIC-REALIZATION]
 * [DD-G3-CHANNELS] [CXC-R-BOOT-FUNGIBLE]
 *
 * PURE C++ (yaml_min + realization.h only) so env:native_gauge proves the
 * binding arithmetic on a host, with no board.
 *
 * ------------------------------------------------------------------
 * THE DIALECT — and why it accepts TWO forms
 * ------------------------------------------------------------------
 * The tree's existing gauge dialect (python/composeio/render/examples/
 * dual_throttle.compose.yaml) is single-valued:
 *
 *     - type: gauge
 *       id: left
 *       min_val: 0
 *       max_val: 255
 *       namespace: throttle/left
 *
 * The channel-set form adds the second channel WITHOUT breaking it:
 *
 *     - type: gauge
 *       id: left
 *       min_val: 0
 *       max_val: 255
 *       namespace: throttle/left        # legacy == channels.value
 *       channels:
 *         value: { namespace: throttle/left,  min: 0,   max: 255 }
 *         illum: { namespace: rack/backlight, min: 0,   max: 255 }
 *
 * Rules, and they are the migration answer the vision asked for:
 *   - a bare `namespace:` is SUGAR for `channels.value`, inheriting
 *     min_val/max_val as its domain.  Every scene in the tree keeps working
 *     and no raster backend has to change.
 *   - an explicit `channels.value` WINS over `namespace:`.  Two sources for
 *     one fact need a stated precedence or they become a defect nobody can
 *     attribute.
 *   - a channel the backend does not realize is reported ONCE, HERE, at
 *     bind time — never per value.  Unknown is LOUD (a scene/firmware
 *     mismatch); NotApplicable is silent (a design).
 *
 * PHYSICAL INSTANCE ORDER: gauge widgets are assigned to physical gauges in
 * DOCUMENT ORDER — the first `type: gauge` in the tree is gauge 1.  That is
 * a convention and it is fragile in exactly one way: reordering the scene
 * silently rewires the rack.  A future `slot:` key should override it; the
 * hook is here and the check is in the bring-up ladder (rung 8 asks the
 * operator which needle moved).
 */

#ifndef GAUGE_BIND_H
#define GAUGE_BIND_H

#include <cstddef>

#include "realization.h"

struct YamlNode;

static constexpr int kMaxGaugeInstances = 4;

/*
 * The bound table.  Strings point INTO the YamlDoc arena, so the doc must
 * outlive this — the same lifetime rule compose_scene.cpp already has.
 */
struct GaugeBindings {
    int count = 0;
    InstanceBinding inst[kMaxGaugeInstances];

    /* Diagnostics, so a bind that produced nothing can say why rather than
     * leaving an operator staring at three still needles. */
    int gauges_seen = 0;        /* `type: gauge` widgets found */
    int gauges_dropped = 0;     /* seen beyond instance_count() */
    int channels_unknown = 0;   /* names the backend never heard of */
    int channels_unbound = 0;   /* declared with no namespace */

    /* Every namespace in the table, deduplicated — what the app subscribes.
     * Returns the count written. */
    size_t namespaces(const char** out, size_t cap) const;

    /* Route one arrival.  Returns true iff it matched a bound channel.
     * The caller has already ensured the backend is ready(). */
    bool dispatch(RealizationBackend& be, const char* ns, double value) const;
};

/*
 * Walk a parsed .compose.yaml and bind every `type: gauge` widget, at any
 * depth, in document order.  `be` is consulted for realizes() only; nothing
 * is driven here.
 */
void gauge_bind(const YamlNode* root, RealizationBackend& be,
                GaugeBindings* out);

#endif /* GAUGE_BIND_H */
