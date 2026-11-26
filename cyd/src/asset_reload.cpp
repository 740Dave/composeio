/*
 * asset_reload.cpp — Asset-change-notify consumer decisions.  See asset_reload.h.
 * [SPEC:CXC-ASSET-CHANGE-NOTIFY] [CXC-ACN-SHAPE] consumer half.
 *
 * Pure C++ (no ESP-IDF, no mbedtls): host-compiles for env:native_test the
 * same way restart.cpp does.  Hash strings in, decisions out.
 */

#include "asset_reload.h"

#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------ */
/* PURE, STATELESS decisions                                           */
/* ------------------------------------------------------------------ */

static bool nonempty(const char* s) { return s && s[0]; }

AssetReloadDecision asset_reload_decide(const char* old_hash,
                                        const char* new_hash,
                                        const char* held) {
    /* Nothing held yet -> the boot/loop fetch owns getting the first config;
     * an event cannot self-select against an empty held hash. */
    if (!nonempty(held)) return AR_IGNORE_NO_HELD;

    /* A malformed event with no `new` is not actionable — ignore safely. */
    if (!nonempty(new_hash)) return AR_IGNORE_ALREADY_LATEST;

    /* I already hold the latest.  This is THE guard that absorbs the RETAINED
     * replay: on (re)connect the broker re-delivers the last change event,
     * whose `new` is exactly the version I fetched at boot -> no spurious
     * reload ([CXC-ACN-RETENTION] / brief pt 6). */
    if (strcmp(new_hash, held) == 0) return AR_IGNORE_ALREADY_LATEST;

    /* The exact bytes I hold just changed -> this change is mine. */
    if (nonempty(old_hash) && strcmp(old_hash, held) == 0) return AR_RELOAD;

    /* old != held AND new != held: a foreign asset changed, OR I skipped an
     * intermediate PUT (retained carries only the last transition).  The event
     * is identity-free so these are indistinguishable — reacting here would
     * re-fetch on EVERY asset's change across the fleet (brief pt 6).  Ignore
     * on the event path; the periodic re-hash BACKSTOP closes the skipped-PUT /
     * missed-event gap (brief pt 7). */
    return AR_IGNORE_NOT_MINE;
}

bool asset_backstop_should_reload(const char* fetched_hash, const char* held) {
    if (!nonempty(fetched_hash)) return false;   /* fetch failed -> keep held */
    if (!nonempty(held)) return false;           /* nothing to compare against */
    return strcmp(fetched_hash, held) != 0;
}

bool asset_backstop_due(uint32_t now_ms, uint32_t next_due_ms) {
    /* Wraparound-safe: (int32_t)(now - due) >= 0 iff now is at/after due,
     * even across the ~49.7-day millis() wrap. */
    return (int32_t)(now_ms - next_due_ms) >= 0;
}

static bool is_ws(char c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }

/* Skip one JSON value token starting at *p (which points at its first char).
 * Handles string / balanced object|array / bare literal (number/true/false/
 * null).  Returns a pointer just past the value.  String- and escape-aware so
 * brackets or quotes inside a string cannot desynchronize the scan. */
static const char* skip_value(const char* p) {
    if (!p || !*p) return p;
    if (*p == '"') {
        p++;
        while (*p && *p != '"') { if (*p == '\\' && p[1]) p++; p++; }
        return (*p == '"') ? p + 1 : p;
    }
    if (*p == '{' || *p == '[') {
        int depth = 0; bool in_str = false;
        for (; *p; p++) {
            char c = *p;
            if (in_str) { if (c == '\\' && p[1]) p++; else if (c == '"') in_str = false; }
            else if (c == '"') in_str = true;
            else if (c == '{' || c == '[') depth++;
            else if (c == '}' || c == ']') { if (--depth == 0) return p + 1; }
        }
        return p;
    }
    while (*p && *p != ',' && *p != '}' && *p != ']' && !is_ws(*p)) p++;
    return p;
}

/* Find the value token for top-level "key" in a flat JSON object.  Returns a
 * pointer to the first non-space char AFTER the colon (a '"', 'n' for null,
 * etc.), or nullptr if the key is absent.  Walks the object as KEY:VALUE pairs
 * and SKIPS each value wholesale, so a value STRING that contains the key token
 * (e.g. {"old":"new","new":"real"}) can never be mistaken for the key — the
 * desync a naive quote-hop scanner suffers, caught by a negative control. */
static const char* json_top_value(const char* s, const char* key) {
    if (!s) return nullptr;
    size_t klen = strlen(key);
    const char* p = s;
    while (*p) {
        if (*p != '"') { p++; continue; }
        const char* kstart = p + 1;
        const char* q = kstart;                       /* end of this string */
        while (*q && *q != '"') { if (*q == '\\' && q[1]) q++; q++; }
        if (*q != '"') return nullptr;                /* unterminated */
        const char* c = q + 1;
        while (is_ws(*c)) c++;
        if (*c != ':') { p = q + 1; continue; }       /* a string, not a key */
        c++;
        while (is_ws(*c)) c++;
        size_t len = (size_t)(q - kstart);
        if (len == klen && strncmp(kstart, key, klen) == 0) return c;  /* found */
        p = skip_value(c);                            /* not ours — skip value */
    }
    return nullptr;
}

/* Copy a JSON string literal at *p (which must point at the opening quote)
 * into buf, unescaping \" \\ \/.  Returns false if *p is not a string. */
static bool json_copy_string(const char* p, char* buf, size_t cap) {
    if (!p || *p != '"' || cap == 0) return false;
    p++;
    size_t o = 0;
    while (*p && *p != '"') {
        char c = *p++;
        if (c == '\\' && *p) {
            char e = *p++;
            c = (e == 'n') ? '\n' : (e == 't') ? '\t' : e;  /* \" \\ \/ pass thru */
        }
        if (o + 1 < cap) buf[o++] = c;
    }
    if (o < cap) buf[o] = '\0'; else buf[cap - 1] = '\0';
    return true;
}

bool asset_reload_parse_change(const char* json,
                               char* old_out, size_t old_cap,
                               char* new_out, size_t new_cap) {
    if (old_out && old_cap) old_out[0] = '\0';
    if (new_out && new_cap) new_out[0] = '\0';
    if (!json) return false;

    /* "old": a string is the prior hash; `null`/absent is the create case ->
     * leave empty.  Never fatal — an absent old is legal. */
    const char* ov = json_top_value(json, "old");
    if (ov && *ov == '"') json_copy_string(ov, old_out, old_cap);

    /* "new": REQUIRED and must be a string to be actionable.  A missing or
     * non-string new leaves new_out empty and returns false, so the caller's
     * self-select sees no advertised hash and safely does nothing. */
    const char* nv = json_top_value(json, "new");
    if (!nv || *nv != '"') return false;
    return json_copy_string(nv, new_out, new_cap) && new_out[0] != '\0';
}

/* ------------------------------------------------------------------ */
/* Thin stateful holder                                                */
/* ------------------------------------------------------------------ */

static char s_namespace[64] = ASSET_CHANGE_NAMESPACE;
static char s_held[ASSET_HASH_HEX_LEN + 1] = {0};

void asset_reload_init(const char* namespace_opt) {
    const char* ns = (namespace_opt && namespace_opt[0]) ? namespace_opt
                                                         : ASSET_CHANGE_NAMESPACE;
    snprintf(s_namespace, sizeof(s_namespace), "%s", ns);
    s_held[0] = '\0';
}

void asset_reload_reset(void) {
    snprintf(s_namespace, sizeof(s_namespace), "%s", ASSET_CHANGE_NAMESPACE);
    s_held[0] = '\0';
}

const char* asset_reload_namespace(void) { return s_namespace; }

bool asset_reload_is_change_ns(const char* ns) {
    return ns && strcmp(ns, s_namespace) == 0;
}

void asset_reload_set_held(const char* hash) {
    if (!nonempty(hash)) { s_held[0] = '\0'; return; }
    snprintf(s_held, sizeof(s_held), "%s", hash);
}

const char* asset_reload_held(void) { return s_held; }

AssetReloadDecision asset_reload_decide_held(const char* old_hash,
                                             const char* new_hash) {
    return asset_reload_decide(old_hash, new_hash, s_held);
}
