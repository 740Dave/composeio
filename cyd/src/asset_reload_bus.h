/*
 * asset_reload_bus.h — the FIRMWARE-side glue that wires the pure
 * asset-change-notify consumer (asset_reload.{h,cpp}) onto the shared display
 * mains (main.cpp, qualia_main.cpp), mirroring the wiring
 * firmware/reactive_core/src/controller_main.cpp already carries.
 * [SPEC:CXC-DEVICE-BASELINE] cap 1 ([CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD]).
 *
 * ⛔ THE ONE DELIBERATE DIVERGENCE FROM controller_main.cpp: this baseline's
 * ruling is that THE DEVICE NEVER COMPUTES sha256
 * ([CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD].self-select-rule, david-ruling).  The
 * held hash is the SERVER-PROVIDED content-hash read off the GET response
 * header (X-Content-Hash / ETag — [CXC-DEVICE-BASELINE-CAP-HASH-ON-GET], the
 * bus_storage side landing in parallel), NOT an mbedtls hash of the fetched
 * bytes.  controller_main.cpp still hashes locally; that is a pre-baseline
 * divergence flagged for the architect, not changed here.
 *
 * DESIGN: PURE C++ — no ESP-IDF, no logging, no network.  Every decision
 * (route the ns, parse the {old,new} event, self-select, decide whether to
 * reload) lives here and is host-tested with NEGATIVE CONTROLS
 * (asset_reload_selftest.cpp, env:native_asset_reload).  The two effects the
 * host cannot exercise — the reboot itself (esp_restart, the recovery FLOOR)
 * and the HTTP re-GET that yields the backstop's fresh server hash — are
 * INJECTED by the firmware: a reboot callback, and (for the backstop) a hash
 * the main fetched.  So this module carries no untested branch of its own.
 */

#ifndef ASSET_RELOAD_BUS_H
#define ASSET_RELOAD_BUS_H

#include "asset_reload.h"

/* The reboot seam — the recovery FLOOR
 * ([CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD].recovery-floor-statement).  On device
 * this is main.cpp's reboot_now() (esp_restart -> boot config-fetch).  In the
 * host test it is a spy that records the call so a NEGATIVE control can assert
 * "reboot NOT invoked" on a not-mine / already-latest event. */
typedef void (*AssetReloadReboot)(void* user, const char* reason);

/* Arm the consumer (clears the held hash) — call once at boot BEFORE the first
 * fetch, exactly as controller_main.cpp calls asset_reload_init before its
 * fetch loop. */
void asset_reload_bus_init(void);

/* The namespace to subscribe: composeio/+/bus_storage/asset/changed. */
const char* asset_reload_bus_namespace(void);

/* Record the SERVER-PROVIDED content-hash of the config we now hold, read off
 * the GET response header.  `server_hash` may be empty when the server does not
 * yet serve the header ([CXC-DEVICE-BASELINE-CAP-HASH-ON-GET] not yet landed):
 * held then stays empty, the event path is inert (AR_IGNORE_NO_HELD), and the
 * caller SHOULD shout — the capability is present but cannot self-select until
 * the server serves a hash.  Returns true iff a non-empty hash was recorded, so
 * the caller can emit that shout. */
bool asset_reload_bus_note_held(const char* server_hash);

/* The currently held (server-provided) hash ("" when none). */
const char* asset_reload_bus_held(void);

/* Consume one bus arrival on the EVENT path.  Returns true iff `ns` is the
 * asset-change namespace (the arrival is ours to handle and MUST NOT be routed
 * onward to a scene) — mirrors the every-other-control-plane `*_on_bus_value`
 * shape the drain already uses.  On a self-select RELOAD it invokes `reboot`;
 * on already-latest / not-mine it does nothing (the backstop covers a skipped
 * multi-PUT).  `kind` is BusEvent::Kind (2 == String); a structured {old,new}
 * val always arrives as String (bus_transport.h), so a non-String arrival on
 * this ns is malformed and ignored without reboot. */
bool asset_reload_bus_consume(const char* ns, int kind, const char* str,
                              AssetReloadReboot reboot, void* user);

/* The BACKSTOP: the firmware has just re-GET the config and read the server's
 * fresh content-hash off the response header; decide whether our held version
 * advanced and, if so, reboot.  `fetched_server_hash` empty (fetch failed, or
 * server serves no hash) is a safe no-op.  Closes the two event-path gaps: a
 * retained event carrying only the LAST transition past our held version, and a
 * best-effort emit missed while the broker was down. */
void asset_reload_bus_backstop(const char* trigger,
                               const char* fetched_server_hash,
                               AssetReloadReboot reboot, void* user);

#endif /* ASSET_RELOAD_BUS_H */
