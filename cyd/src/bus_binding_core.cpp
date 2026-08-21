/*
 * bus_binding_core.cpp — see bus_binding_core.h.
 * [SPEC:CXC-REPOINT]
 *
 * PURE C++ (no ESP-IDF), the restart.cpp / dsl.h idiom.  Every decision the
 * repoint mechanism makes is here and is host-tested with negative controls in
 * bus_binding_selftest.cpp; the ESP-IDF sibling bus_binding.cpp only plumbs
 * NVS / timer / GPIO / mqtt around these answers.
 */

#include "bus_binding_core.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

/* ------------------------------------------------------------------ */
/* Binding record helpers                                              */
/* ------------------------------------------------------------------ */

void binding_clear(BusBinding* b) {
    if (b) memset(b, 0, sizeof(*b));
}

bool binding_addresses_equal(const BusBinding* a, const BusBinding* b) {
    if (!a || !b) return false;
    return strcmp(a->mqtt, b->mqtt) == 0 &&
           strcmp(a->store, b->store) == 0 &&
           strcmp(a->docs, b->docs) == 0;
}

size_t binding_canonical(const BusBinding* b, char* out, size_t cap) {
    if (!out || cap == 0) return 0;
    if (!b) { out[0] = '\0'; return 0; }
    int n = snprintf(out, cap, "%s|%s|%s", b->mqtt, b->store, b->docs);
    if (n < 0) { out[0] = '\0'; return 0; }
    return (size_t)n < cap ? (size_t)n : cap - 1;
}

/* CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF) — a deterministic 16-bit hash
 * of the canonical string.  `composeio fleet` resolves this fingerprint to a
 * name via cyd/bus_bindings.json ([CXC-REPOINT-TOOLING]); the wire never
 * carries the name.  The exact polynomial is a firmware<->tool AGREEMENT: the
 * tool computes it the same way, and the pair is the checked artifact, not
 * this literal in isolation. */
static uint16_t crc16_ccitt(const uint8_t* data, size_t len) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < len; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int bit = 0; bit < 8; bit++)
            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021)
                                 : (uint16_t)(crc << 1);
    }
    return crc;
}

uint16_t binding_fingerprint(const BusBinding* b) {
    char canon[3 * kBindUriMax + 4];
    size_t n = binding_canonical(b, canon, sizeof(canon));
    return crc16_ccitt((const uint8_t*)canon, n);
}

/* ------------------------------------------------------------------ */
/* URI validation ([CXC-REPOINT-PAYLOAD] validation-before-write)      */
/* ------------------------------------------------------------------ */

/* A URI must begin with `scheme` and carry a non-empty host, and stay under
 * kBindUriMax.  Returns false (and leaves the error to the caller) otherwise. */
static bool uri_valid(const char* v, const char* scheme) {
    if (!v || !v[0]) return false;
    size_t slen = strlen(scheme);
    if (strncmp(v, scheme, slen) != 0) return false;
    if (strlen(v) >= kBindUriMax) return false;
    /* host is the run after the scheme up to ':' or '/' — must be non-empty */
    const char* host = v + slen;
    if (*host == '\0' || *host == ':' || *host == '/') return false;
    return true;
}

/* ------------------------------------------------------------------ */
/* Parse                                                               */
/* ------------------------------------------------------------------ */

static void copy_bounded(char* dst, size_t cap, const char* src, size_t n) {
    if (n >= cap) n = cap - 1;
    memcpy(dst, src, n);
    dst[n] = '\0';
}

BindParseResult binding_parse(const char* payload) {
    BindParseResult r;
    memset(&r, 0, sizeof(r));
    r.status = BindParse::Refused;
    r.confirm_s = -1;

    if (!payload || !payload[0]) {
        snprintf(r.error, sizeof(r.error), "empty payload");
        return r;
    }

    /* Scanner: leading token would be a bare NAME in the transport idiom, but
     * this payload REQUIRES `bus=` (a bare name with no `=` is a rename we must
     * refuse anyway), so we scan `key=value` pairs uniformly and treat any
     * leading bare word as an unknown token. */
    const char* p = payload;
    while (*p) {
        while (*p == ' ' || *p == '\t') p++;
        if (!*p) break;

        char key[16] = "";
        size_t k = 0;
        while (p[k] && p[k] != '=' && p[k] != ' ' && k < sizeof(key) - 1) k++;
        copy_bounded(key, sizeof(key), p, k);
        p += k;
        /* consume any overlong key remainder up to '=' or space */
        while (*p && *p != '=' && *p != ' ') p++;

        if (*p != '=') {
            /* a bare word with no value — unknown/rename token, reported */
            r.unknown_key = true;
            if (*p) p++;
            continue;
        }
        p++; /* past '=' */

        /* value runs to the next unquoted space (URIs carry no spaces) */
        const char* vstart = p;
        size_t vlen = 0;
        while (p[vlen] && p[vlen] != ' ' && p[vlen] != '\t') vlen++;
        p += vlen;

        /* An over-length value is REFUSED, never truncated: a silently
         * shortened URI is a typo generator ([CXC-REPOINT-PAYLOAD] size). */
        if ((strcmp(key, "mqtt") == 0 || strcmp(key, "store") == 0 ||
             strcmp(key, "docs") == 0) && vlen >= kBindUriMax) {
            snprintf(r.error, sizeof(r.error), "%s= too long (>=96 B) — refused",
                     key);
            return r;
        }

        char val[kBindUriMax];
        copy_bounded(val, sizeof(val), vstart, vlen);

        if (strcmp(key, "bus") == 0) {
            copy_bounded(r.fields.name, sizeof(r.fields.name), vstart,
                         vlen < kBindNameMax - 1 ? vlen : kBindNameMax - 1);
            r.has_name = true;
        } else if (strcmp(key, "mqtt") == 0) {
            if (!uri_valid(val, "mqtt://")) {
                snprintf(r.error, sizeof(r.error),
                         "mqtt= must be mqtt://host, <96 B");
                return r;
            }
            copy_bounded(r.fields.mqtt, sizeof(r.fields.mqtt), val, strlen(val));
            r.has_mqtt = true;
        } else if (strcmp(key, "store") == 0) {
            if (!uri_valid(val, "http://")) {
                snprintf(r.error, sizeof(r.error),
                         "store= must be http://host, <96 B");
                return r;
            }
            copy_bounded(r.fields.store, sizeof(r.fields.store), val,
                         strlen(val));
            r.has_store = true;
        } else if (strcmp(key, "docs") == 0) {
            if (!uri_valid(val, "http://")) {
                snprintf(r.error, sizeof(r.error),
                         "docs= must be http://host, <96 B");
                return r;
            }
            copy_bounded(r.fields.docs, sizeof(r.fields.docs), val, strlen(val));
            r.has_docs = true;
        } else if (strcmp(key, "confirm") == 0) {
            char* end = nullptr;
            long v = strtol(val, &end, 10);
            if (end == val) {
                snprintf(r.error, sizeof(r.error), "confirm= not a number");
                return r;
            }
            bool clamped = false;
            r.confirm_s = binding_clamp_confirm((int32_t)v, &clamped);
            r.confirm_clamped = clamped;
        } else {
            /* unknown key — REPORTED, never silently ignored */
            r.unknown_key = true;
        }
    }

    /* required: bus= plus at least one of mqtt/store/docs.  bus= alone is a
     * RENAME and is refused ([CXC-REPOINT-PAYLOAD] required). */
    if (!r.has_name) {
        snprintf(r.error, sizeof(r.error), "missing required bus=");
        return r;
    }
    if (!r.has_mqtt && !r.has_store && !r.has_docs) {
        snprintf(r.error, sizeof(r.error),
                 "bus= only is a RENAME — refused (need mqtt/store/docs)");
        return r;
    }

    r.status = BindParse::Ok;
    return r;
}

BusBinding binding_merge(const BusBinding* base, const BindParseResult* u) {
    BusBinding out;
    if (base) out = *base;
    else binding_clear(&out);
    if (!u) return out;
    if (u->has_name)
        copy_bounded(out.name, sizeof(out.name), u->fields.name,
                     strlen(u->fields.name));
    if (u->has_mqtt)
        copy_bounded(out.mqtt, sizeof(out.mqtt), u->fields.mqtt,
                     strlen(u->fields.mqtt));
    if (u->has_store)
        copy_bounded(out.store, sizeof(out.store), u->fields.store,
                     strlen(u->fields.store));
    if (u->has_docs)
        copy_bounded(out.docs, sizeof(out.docs), u->fields.docs,
                     strlen(u->fields.docs));
    return out;
}

int32_t binding_clamp_confirm(int32_t seconds, bool* clamped) {
    int32_t v = seconds;
    if (v < 30) v = 30;
    else if (v > 600) v = 600;
    if (clamped) *clamped = (v != seconds);
    return v;
}

/* ------------------------------------------------------------------ */
/* Precedence                                                          */
/* ------------------------------------------------------------------ */

BindResolution binding_resolve(const BindTierView* per_device,
                               const BindTierView* global,
                               const BusBinding* compiled,
                               bool button_forced) {
    BindResolution out;
    memset(&out, 0, sizeof(out));

    /* Step 1: the physical override bypasses BOTH NVS tiers for this boot. */
    if (button_forced) {
        out.tier = BindTier::Compiled;
        out.source = BindSource::ButtonCleared;
        if (compiled) out.active = *compiled;
        out.is_trial = false;
        return out;
    }

    /* Steps 2-3: per-device confirmed, then per-device trial. */
    if (per_device && per_device->state != BindState::Empty) {
        out.tier = BindTier::PerDevice;
        out.source = BindSource::NvsPerDevice;
        out.active = per_device->binding;
        out.is_trial = (per_device->state == BindState::Trial);
        return out;
    }

    /* Steps 4-5: global confirmed, then global trial. */
    if (global && global->state != BindState::Empty) {
        out.tier = BindTier::Global;
        out.source = BindSource::NvsGlobal;
        out.active = global->binding;
        out.is_trial = (global->state == BindState::Trial);
        return out;
    }

    /* Step 6: the compiled floor. */
    out.tier = BindTier::Compiled;
    out.source = BindSource::Compiled;
    if (compiled) out.active = *compiled;
    out.is_trial = false;
    return out;
}

/* ------------------------------------------------------------------ */
/* Commit-confirmed state machine                                     */
/* ------------------------------------------------------------------ */

static uint32_t sat_inc_u32(uint32_t v) {
    return v == 0xFFFFFFFFu ? v : v + 1;
}

BindArm binding_arm_trial(BindRecord* r, const BusBinding* newb,
                          char* ack, size_t ack_cap) {
    if (!r || !newb) {
        if (ack && ack_cap) snprintf(ack, ack_cap, "bus REFUSED (null)");
        return BindArm::RefusedTrial;
    }

    uint16_t newfp = binding_fingerprint(newb);

    /* [CXC-REPOINT-SINGLE-TRIAL] a second change to a trialling tier is
     * REFUSED, not queued — accepting it would overwrite prev (the last
     * known-good) with the trial binding and make the device strandable. */
    if (r->state == BindState::Trial) {
        if (ack && ack_cap)
            snprintf(ack, ack_cap, "bus=%s REFUSED (trial in progress, seq %lu)",
                     newb->name, (unsigned long)r->seq);
        return BindArm::RefusedTrial;
    }

    /* [CXC-REPOINT-BOUNCE] a latched-denied target is refused until a
     * DIFFERENT target (or the button) clears the latch. */
    if (r->deny != 0 && r->deny == newfp) {
        if (ack && ack_cap)
            snprintf(ack, ack_cap, "bus=%s REFUSED (3 reverts: %s)",
                     newb->name, r->why[0] ? r->why : "unreachable");
        return BindArm::RefusedDeny;
    }

    /* [CXC-REPOINT-RETAINED-STATE] value-compare / absorbing: identical to the
     * confirmed value is a NO-OP — no write, no trial, no restart.  This is
     * what makes a RETAINED fact safe to re-deliver on every reconnect. */
    if (r->state == BindState::Confirmed &&
        binding_addresses_equal(&r->cur, newb)) {
        if (ack && ack_cap)
            snprintf(ack, ack_cap, "bus=%s unchanged (no-op)", newb->name);
        return BindArm::NoOp;
    }

    /* Arm: prev<-cur, cur<-new, state<-trial, seq++.  The invariant is that
     * prev is NEVER overwritten while state==trial (guarded above), so the
     * device is only ever in one of two bindings on this tier. */
    r->prev = r->cur;
    r->cur = *newb;
    r->state = BindState::Trial;
    r->seq++;
    if (ack && ack_cap)
        snprintf(ack, ack_cap, "bus=%s trial (seq %lu)", newb->name,
                 (unsigned long)r->seq);
    return BindArm::Armed;
}

void binding_confirm(BindRecord* r) {
    if (!r) return;
    r->state = BindState::Confirmed;
    /* consecutive-revert counter resets on a proven target */
    r->reverts = 0;
    r->tfp = 0;
    /* [CXC-REPOINT-BOUNCE] cleared-by: "a change to a DIFFERENT target on that
     * tier, or the button".  A CONFIRMED change clears this tier's deny latch
     * UNCONDITIONALLY, so a transiently-bad target that once latched is
     * retryable later without physical access.  (A trial toward the still-
     * denied fingerprint can never reach here: binding_arm_trial refuses it,
     * so every binding that reaches confirm is a DIFFERENT, proven target.)
     * The build's earlier version only cleared when deny named the now-current
     * target — strictly stickier than the spec, and it stranded a tier whose
     * flaky target was later fixed. */
    r->deny = 0;
    r->why[0] = '\0';
}

void binding_revert(BindRecord* r, const char* reason) {
    if (!r) return;

    /* the target that failed is the current (trial) binding */
    BusBinding failed = r->cur;
    uint16_t failfp = binding_fingerprint(&failed);

    /* go home */
    r->cur = r->prev;
    r->state = BindState::Confirmed;

    /* [CXC-REPOINT-BOUNCE] count CONSECUTIVE reverts toward the SAME target */
    if (r->tfp == failfp && r->reverts > 0) {
        r->reverts = sat_inc_u32(r->reverts);
    } else {
        r->tfp = failfp;
        r->reverts = 1;
    }
    if (r->reverts >= 3) r->deny = failfp;

    snprintf(r->why, sizeof(r->why), "%s", reason ? reason : "timeout");
}

uint8_t binding_reverts_saturated(const BindRecord* r) {
    if (!r) return 0;
    return r->reverts > 15 ? 15 : (uint8_t)r->reverts;
}

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-CONFIRM] the two-plane commit gate                     */
/* ------------------------------------------------------------------ */

bool binding_confirm_ready(const BindConfirmGate* g) {
    if (!g) return false;
    /* The broker round-trip echo plane is ALWAYS required — it is the proof of
     * a LIVE, fanning broker (a broker that accepts TCP but does not fan the
     * echo back must still fail to confirm and revert).  The store plane is
     * required only when the trial's config strategy actually fetches from the
     * store (config:http); under config:bus / config:cached the store plane is
     * vacuously satisfied and the trial confirms on the echo ALONE. */
    return g->echo_ok && (g->store_ok || !g->store_required);
}

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-REVERT] the reason a trial reverts                     */
/* ------------------------------------------------------------------ */

const char* binding_default_reason(bool store_required, bool store_ok,
                                   bool echo_ok) {
    /* The floor the timer callback uses when no more-specific cause
     * (no-wifi / no-dns / store-http-<code>) was recorded via
     * bus_binding_note_reason.  It names the plane that was still outstanding
     * so a revert is NEVER a bare "timeout" that hides which plane failed. */
    if (store_required && !store_ok) return "store-unreachable";
    if (!echo_ok) return "broker-no-echo";
    return "timeout";   /* both planes satisfied — should not revert */
}

/* ------------------------------------------------------------------ */
/* Two-stage escape hatch                                             */
/* ------------------------------------------------------------------ */

HatchAction binding_hatch_action(uint32_t hold_ms,
                                 uint32_t short_ms, uint32_t long_ms) {
    if (hold_ms >= long_ms) return HatchAction::ClearBoth;
    if (hold_ms >= short_ms) return HatchAction::ClearPerDevice;
    return HatchAction::None;
}

/* ------------------------------------------------------------------ */
/* Announce word                                                      */
/* ------------------------------------------------------------------ */

uint32_t binding_announce_word(uint16_t fingerprint, AnnState state,
                               BindSource source, uint8_t reverts,
                               uint8_t flags) {
    uint32_t w = 0;
    w |= (uint32_t)fingerprint << 16;
    w |= ((uint32_t)state & 0xF) << 12;
    w |= ((uint32_t)source & 0xF) << 8;
    w |= ((uint32_t)(reverts > 15 ? 15 : reverts) & 0xF) << 4;
    w |= ((uint32_t)flags & 0xF);
    return w;
}
