/*
 * asset_reload.h — Asset-change-notify CONSUMER (config-fetch live update)
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-DEVICE] + [SPEC:CXC-ASSET-CHANGE-NOTIFY]
 * ([CXC-ACN-SHAPE] consumer half, the P-b device side of the P-a producer in
 *  services/bus_storage/change_notify.py).
 *
 * PROBLEM (DEFECT-CONFIG-FETCH-FIRMWARE-DOES-NOT-405602): a config-fetched
 * device fetches its .compose.yaml from bus_storage ONCE at boot and never
 * notices when that asset is re-PUT — a MANUAL `_device/<id>/restart` was the
 * only way to pick up a change.  bus_storage already EMITS a retained change
 * event on every content change; this module is the missing consumer.
 *
 * THE EMIT CONTRACT WE CONSUME (confirmed against change_notify.py):
 *   topic     composeio/global/bus_storage/asset/changed   (namespace
 *             "bus_storage/asset/changed", scope GLOBAL, json engine)
 *   retained  yes (STATE type), QoS 1, source "bus_storage"
 *   payload   the BusMessage `val` is the identity-free dict
 *               {"old": <sha256-hex | null>, "new": <sha256-hex>}
 *             `old` is null on a create.  There is NO asset path/id — a
 *             consumer CANNOT tell which asset changed by name.  The ONLY
 *             scoping is by the hash it currently holds ([CXC-ACN-SHAPE]:
 *             "consumers self-select by the hash they hold").
 *   hash      sha256 LOWERCASE HEX of the raw asset bytes
 *             (hashlib.sha256(data).hexdigest()).  A no-op re-PUT (old==new)
 *             emits nothing.
 *
 * SELF-SELECT RULE (the ratified mechanism, [CXC-ACN-SHAPE] / brief pt 6):
 *   new == held  -> IGNORE   (I already hold the latest; this absorbs the
 *                             RETAINED replay a subscriber gets on connect)
 *   old == held  -> RELOAD   (the exact bytes I hold just changed -> mine)
 *   otherwise    -> NOT MINE (a foreign asset changed, OR I skipped a
 *                             multi-PUT: the event carries no path so this is
 *                             ambiguous and MUST NOT re-fetch — doing so would
 *                             fire on every asset's change across the fleet,
 *                             brief pt 6.  The skipped-multi-PUT / missed-event
 *                             gap is closed by the periodic re-hash BACKSTOP
 *                             below, NOT by reacting to this event.)
 *
 * BACKSTOP (brief pt 7 — the known limitation of the event path).  The
 * retained event is only the LAST transition, so a device holding H0 across
 * H0->H1->H2 sees {old:H1,new:H2} (old != held) and the event path correctly
 * ignores it; a best-effort emit can also be missed if the broker was down at
 * PUT time.  Both are covered by re-GET-ing the asset, sha256-ing it and
 * comparing to `held` on a timer and on every MQTT RE-connect — a re-apply on
 * mismatch.  This module supplies the PURE decisions; the firmware owns the
 * GET + sha256 + apply.
 *
 * DESIGN: like restart.{h,cpp}, this is PURE C++ (no ESP-IDF, no mbedtls) so it
 * host-compiles for env:native_test.  It deals only in hash STRINGS — the
 * sha256 of fetched bytes and the platform apply (esp_restart) are the
 * firmware's, injected/performed there.  The core decisions are STATELESS pure
 * functions so a host test asserts every branch directly; a thin stateful
 * holder keeps the held hash + the subscribe namespace for firmware
 * convenience.
 */

#ifndef ASSET_RELOAD_H
#define ASSET_RELOAD_H

#include <cstddef>
#include <cstdint>

/* The bus namespace the change event rides (scope GLOBAL -> MQTT topic
 * composeio/global/bus_storage/asset/changed).  Matches change_notify.py's
 * DEFAULT_NAMESPACE. */
#define ASSET_CHANGE_NAMESPACE "bus_storage/asset/changed"

/* Length of a sha256 lowercase-hex digest, excluding the NUL. */
#define ASSET_HASH_HEX_LEN 64

typedef enum {
    AR_IGNORE_NO_HELD = 0,     /* no config held yet — cannot self-select     */
    AR_IGNORE_ALREADY_LATEST,  /* new == held — retained replay / up to date  */
    AR_IGNORE_NOT_MINE,        /* mismatch — foreign asset or skipped multi-  */
                               /* PUT; the BACKSTOP covers the skipped case   */
    AR_RELOAD,                 /* old == held — the version I hold changed     */
} AssetReloadDecision;

/* ---- PURE, STATELESS decisions (the whole testable core) ------------------ */

/* The self-select decision for one change event against a held hash.
 *   old_hash  may be null/empty (create case)
 *   new_hash  the advertised new content hash
 *   held      the hash of the config this device currently holds (empty ==
 *             "none held yet")
 * All comparisons are exact-string on lowercase-hex. */
AssetReloadDecision asset_reload_decide(const char* old_hash,
                                        const char* new_hash,
                                        const char* held);

/* The BACKSTOP decision: having just re-fetched and hashed our OWN asset,
 * should we re-apply?  True iff both hashes are present and differ.  A failed
 * fetch (empty fetched_hash) is a NO-OP — we keep what we have. */
bool asset_backstop_should_reload(const char* fetched_hash, const char* held);

/* Parse the identity-free change-event payload {"old":<hash|null>,"new":<hash>}
 * out of the raw JSON `val` a structured bus arrival carries VERBATIM as a
 * String (bus_transport.h: a nested dict rides as Kind::String, the raw text).
 * Extracts the "old" and "new" string members into the caller's buffers; a
 * `null`/absent member yields an EMPTY string (the create case, which
 * asset_reload_decide reads as "no old").  Returns true iff a "new" member was
 * found (the event is actionable); false leaves the buffers as empty strings so
 * a malformed payload is a safe no-op, never a spurious reload.  Pure: no JSON
 * library, no allocation — a hand-rolled top-level string-member scan matching
 * the flat {old,new} contract, host-tested with negative controls. */
bool asset_reload_parse_change(const char* json,
                               char* old_out, size_t old_cap,
                               char* new_out, size_t new_cap);

/* Is `now_ms` at/after `next_due_ms`?  32-bit-wraparound-safe (the ESP32
 * millis() wraps at ~49.7 days). */
bool asset_backstop_due(uint32_t now_ms, uint32_t next_due_ms);

/* ---- Thin stateful holder (firmware convenience; also host-tested) -------- */

/* Set the subscribe namespace (null -> ASSET_CHANGE_NAMESPACE) and clear the
 * held hash. */
void asset_reload_init(const char* namespace_opt);

/* Clear all state (held hash + namespace).  For host-test isolation. */
void asset_reload_reset(void);

/* The namespace to subscribe (composeio/+/<this>). */
const char* asset_reload_namespace(void);

/* True iff ns is this device's change-notify namespace. */
bool asset_reload_is_change_ns(const char* ns);

/* Record the hash of the config we now hold (called after each successful
 * fetch+parse).  A null/empty hash clears it.  Truncated/copied to
 * ASSET_HASH_HEX_LEN. */
void asset_reload_set_held(const char* hash);

/* The currently held hash ("" when none). */
const char* asset_reload_held(void);

/* Convenience: decide for an event against the CURRENTLY held hash. */
AssetReloadDecision asset_reload_decide_held(const char* old_hash,
                                             const char* new_hash);

#endif /* ASSET_RELOAD_H */
