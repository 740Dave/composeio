/*
 * freshness_yaml.cpp — the DECLARATION half on the leaf.
 * [SPEC:CXC-FRESHNESS] [CXC-FRESH-CPP-FOLLOWON]
 *
 * Reads the SAME `namespaces: - name: ... freshness: {...}` schema that
 * python/composeio/config.py reads (config.py:328-361), so a namespace's
 * freshness contract is AUTHORED ONCE and means the same thing on the Python
 * tier and on the leaf.  Two schemas for one contract is the fork
 * [BP-TYPE-CODES] exists to prevent, one layer up.
 *
 * ⛔ AN INTERVAL IS DECLARED, NEVER INFERRED.  There is deliberately no code
 * here that learns a cadence from traffic.  The fleet's most-watched
 * namespace (`throttle/<n>`) is SILENT AT REST BY DESIGN, so any inference
 * would learn the wrong number from precisely the signal it matters most on —
 * and would then be confidently wrong in a way no test that only checks "did
 * it go stale?" can see.
 *
 * Split out of freshness.cpp so the receiver itself depends on nothing at all
 * (not even yaml_min), which is what lets env:native_freshness compile the
 * verdict logic in isolation.  Pure C++ either way — yaml_min.h host-compiles.
 */

#include "freshness.h"
#include "yaml_min.h"

#include <cstdlib>
#include <cstring>

/* Seconds (float, as the YAML and the Python tier both express it) ->
 * milliseconds (uint32, as a monotonic esp_timer stamp expresses it).
 * Rounds rather than truncates: a declared 0.05 s must not become 50 ms on
 * one tier and 49 on the other. */
static uint32_t seconds_to_ms(float s) {
    if (s <= 0.0f) return 0;
    double ms = (double)s * 1000.0 + 0.5;
    if (ms > (double)0xFFFFFFFFu) return 0xFFFFFFFFu;
    return (uint32_t)ms;
}

int freshness_declare_from_yaml(FreshnessTable& t, const YamlNode* root,
                                int* contradictions) {
    if (contradictions) *contradictions = 0;
    if (!root) return 0;
    const YamlNode* list = root->get("namespaces");
    if (!list) return 0;

    int declared = 0;
    for (const YamlNode* n = list->first_child; n; n = n->next_sibling) {
        if (n->type != YamlNode::Type::Map) continue;

        /* A list entry may be `- name: foo` (Python's form) or `- foo: {...}`
         * (the terser dialect some cyd configs use).  Accept both; a schema
         * that refuses the sibling dialect just moves the failure to the
         * author. */
        const char* name = n->get_str("name", nullptr);
        if (!name && n->count == 1 && n->first_child && n->first_child->key)
            name = n->first_child->key;
        if (!name || !name[0]) continue;

        const YamlNode* f = n->get("freshness");
        if (!f && n->first_child && n->first_child->key &&
            strcmp(n->first_child->key, name) == 0)
            f = n->first_child->get("freshness");
        if (!f || f->type != YamlNode::Type::Map) continue;

        const bool quiet = f->get_bool("quiet_by_construction", false);
        const float interval_s = f->get_float("refresh_interval", 0.0f);
        const float timeout_s = f->get_float("timeout", 0.0f);
        const char* node = f->get_str("node", nullptr);

        /* ⛔ BOTH IS A CONTRADICTION, NOT A MERGE.  namespace.py raises here
         * for the same reason: `quiet_by_construction` says "silence is the
         * design" and `refresh_interval` says "it speaks every N seconds".
         * Picking one silently would make the wrong half of a contradictory
         * declaration authoritative, and the author would never learn which.
         * COUNTED and returned so the app shell can say it out loud — a
         * contradiction that is merely skipped is a declaration the author
         * believes they made. */
        if (quiet && interval_s > 0.0f) {
            if (contradictions) (*contradictions)++;
            continue;
        }

        FreshCadence cadence = FreshCadence::Undeclared;
        if (quiet) cadence = FreshCadence::EventDriven;
        else if (interval_s > 0.0f) cadence = FreshCadence::Periodic;
        else continue;                              /* no contract declared */

        if (t.declare(name, cadence, seconds_to_ms(interval_s),
                      seconds_to_ms(timeout_s), node))
            declared++;
    }

    /* ---- freshness CLASSES  [CXC-FRESH-CLASS] ----------------------------
     *
     *   freshness_classes:
     *     - name: nav_solution
     *       members: [gps/fix, imu/heading, log/speed]
     *
     * ⛔ AFTER the per-namespace loop, and that ordering is load-bearing: a
     * class naming a namespace this document has not yet declared must be
     * REFUSED, and it can only be refused if the members already exist.
     * Declaring classes first would make every class in every scene fail open. */
    const YamlNode* classes = root->get("freshness_classes");
    if (classes) {
        for (const YamlNode* n = classes->first_child; n; n = n->next_sibling) {
            if (n->type != YamlNode::Type::Map) continue;
            const char* name = n->get_str("name", nullptr);
            const YamlNode* mem = n->get("members");
            if (!name || !name[0]) continue;

            const char* members[kMaxFreshnessClassMembers];
            size_t m = 0;
            if (mem && mem->type == YamlNode::Type::List) {
                for (const YamlNode* c = mem->first_child;
                     c && m < (size_t)kMaxFreshnessClassMembers;
                     c = c->next_sibling)
                    if (c->type == YamlNode::Type::Scalar && c->scalar)
                        members[m++] = c->scalar;
            }
            /* m == 0 falls through to declare_class, which REFUSES it and
             * counts the refusal — an empty class must not be silently
             * skipped, because a green light wired to nothing is the one
             * output this module must never produce. */
            if (t.declare_class(name, members, m)) declared++;
        }
    }

    return declared;
}
