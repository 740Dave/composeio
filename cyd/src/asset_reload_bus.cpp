/*
 * asset_reload_bus.cpp — see asset_reload_bus.h.  PURE C++: the pure decisions
 * live in asset_reload.{h,cpp}; this is the thin, host-testable router that
 * mirrors controller_main.cpp's handleAssetChange / assetBackstopCheck onto the
 * shared display mains, header-based (the device never computes sha256).
 * [SPEC:CXC-DEVICE-BASELINE] [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD].
 */

#include "asset_reload_bus.h"

/* BusEvent::Kind::String == 2 (bus_transport.h).  A structured {old,new} val
 * always arrives as a String carrying the raw JSON verbatim. */
static const int kKindString = 2;

void asset_reload_bus_init(void) { asset_reload_init(nullptr); }

const char* asset_reload_bus_namespace(void) { return asset_reload_namespace(); }

bool asset_reload_bus_note_held(const char* server_hash) {
    asset_reload_set_held(server_hash);            /* empty clears it */
    const char* h = asset_reload_held();
    return h && h[0];
}

const char* asset_reload_bus_held(void) { return asset_reload_held(); }

bool asset_reload_bus_consume(const char* ns, int kind, const char* str,
                              AssetReloadReboot reboot, void* user) {
    if (!asset_reload_is_change_ns(ns)) return false;   /* not ours */

    /* Ours to handle — consumed here regardless of what we decide, never routed
     * onward.  A non-String arrival on this ns is malformed; ignore it (no
     * reboot) but still consume it. */
    if (kind != kKindString || !str) return true;

    char oldh[ASSET_HASH_HEX_LEN + 1];
    char newh[ASSET_HASH_HEX_LEN + 1];
    if (!asset_reload_parse_change(str, oldh, sizeof(oldh), newh, sizeof(newh)))
        return true;   /* no actionable `new` — safe no-op */

    if (asset_reload_decide_held(oldh, newh) == AR_RELOAD && reboot)
        reboot(user, "asset-change");   /* the recovery FLOOR: re-runs boot fetch */
    return true;
}

void asset_reload_bus_backstop(const char* trigger,
                               const char* fetched_server_hash,
                               AssetReloadReboot reboot, void* user) {
    (void)trigger;
    if (asset_backstop_should_reload(fetched_server_hash, asset_reload_held())
        && reboot)
        reboot(user, "asset-backstop");
}
