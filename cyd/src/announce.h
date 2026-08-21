/*
 * announce.h — DEVICE SELF-ANNOUNCEMENT, the app-shell handler.
 * [SPEC:CXC-BOARD-HW] [CXC-ER-RESTART-BUS] — workstream 0.2 of
 * docs/PLAN-fleet-baseline-and-bus-native.md.
 *
 * The encoding, the namespace table and the reasoning behind every field are
 * in announce_pack.h.  READ THAT FIRST.  This header is only the wiring.
 *
 * It lives in the SHARED APP SHELL, not in a board profile, for the same
 * reason restart.cpp does ([CXC-ER-C19]): every board must inherit it
 * unmodified, or the fleet is only as enumerable as the last board somebody
 * remembered to edit.
 *
 * IT NAMES NO SUBSTRATE.  Everything it needs from the link it asks the
 * BusTransport seam for — `name()`, `connected()`, `link_health()`,
 * `link_has_errors()`, `link_listen_only()` — so the same code announces over
 * MQTT, UART or CAN with no per-transport branch (DP-CIO-6).  The one thing
 * that differs per substrate is what the substrate can CARRY, and that is
 * handled by the transport refusing an oversized value loudly.
 *
 * ⚠ CAN REACHABILITY — RESOLVED 2026-08-13, WITH ONE CONDITION.
 * `_device/<id>/*` embeds a per-device id, so a bridge cannot name these
 * namespaces from the wire alone: the 16-bit id is a hash of the FULL name, so
 * a prefix rule `_device/*` cannot be expanded without already knowing every
 * device id — which is the thing being discovered.  This file publishes VALUES
 * on those namespaces and cannot fix that by itself.
 *
 * ns_announce.{h,cpp} (workstream G2) does: announce_init() hands it the six
 * namespaces this device owns, and it chunks their NAMES over the one
 * well-known root `_device/announce` (0x31AE), which every bridge hardcodes.
 * The bridge learns name->hash from that and every field above becomes
 * nameable on CAN.
 *
 * THE CONDITION: a bridge only knows what it has HEARD.  A board reflashed
 * mid-session announces on boot and on any `_device/broadcast/announce` poll;
 * a bridge restarted after that either restored its table from disk (G4) or
 * polls for a fresh one at startup.  A board running firmware from before
 * ns_announce still produces irreversible hashes — loudly, which is the point.
 *
 * ⚠ NOTHING BELOW HAS RUN ON SILICON.  Host-proven (byte-for-byte against the
 * Python oracle, and end-to-end into the real bridge with the same bytes);
 * zero boards flashed.  MQTT and UART are proven on hardware; CAN is not.
 */

#ifndef ANNOUNCE_H
#define ANNOUNCE_H

#include <cstddef>
#include <cstdint>

/* ------------------------------------------------------------------ */
/* THE BUILD FINGERPRINT                                               */
/* ------------------------------------------------------------------ */
/*
 * CRC-32 of "<pio-env> <date> <time>", generated per-env at EVERY build by
 * cyd/tools/gen_build_stamp.py (a PlatformIO `pre:` extra_script) into
 * src/gen/build_stamp.h, and recorded in cyd/build_fingerprints.json so
 * `composeio fleet` can print the human string the wire never carries.
 *
 * ⚠ WHY IT IS A FUNCTION IN ITS OWN TINY TU (build_stamp.cpp) AND NOT A
 * MACRO THE APP SHELL INCLUDES.  Two reasons, both learned:
 *
 *   1. STALENESS.  A TU that has not changed is NOT RECOMPILED, so a stamp
 *      baked into main.cpp keeps the timestamp of the last time main.cpp
 *      happened to change.  A hand-maintained tag read "TF13" through ~10
 *      flashes across three boards on 2026-08-11, including a board that did
 *      not exist when the string was written — the instrument built to detect
 *      staleness had gone stale.  __DATE__ in a stable TU is the same bug
 *      with the compiler holding the pen.  The generated header changes on
 *      every build, so whatever includes it recompiles on every build.
 *   2. BUILD COST.  Given (1), the include must be confined to the SMALLEST
 *      possible TU — otherwise every build recompiles main.cpp (~1,200 lines
 *      plus the whole widget header set) to move four bytes.
 *
 * Returns 0 and logs loudly if the generator did not run: a fleet where every
 * device reports fingerprint 0 cannot verify a reflash, which is the entire
 * point of the field.
 */
uint32_t cxc_build_fingerprint(void);

/* The human string the fingerprint is OF — e.g. "hosyond_s3 Aug 13 2026
 * 21:41:02".  For the boot banner only; it never goes on the wire (a classic
 * CAN frame is 8 data bytes).  The bus carries the fingerprint; tooling
 * resolves it from cyd/build_fingerprints.json. */
const char* cxc_build_stamp(void);

/* Install the announcer.  Computes the five `_device/<id>/…` namespaces and
 * the two poll namespaces, and latches the build fingerprint + reset reason
 * (both are constant for the life of the boot).
 *
 * `build_fingerprint` comes from the GENERATED gen/build_stamp.h, never from
 * `__DATE__`/`__TIME__` read in this TU: a translation unit that does not
 * change is not recompiled, so a stamp baked into an unchanged TU goes stale
 * silently.  A hand-maintained tag read "TF13" through ~10 flashes across
 * three boards on 2026-08-11 — the instrument built to detect staleness had
 * itself gone stale — and an incremental-build __DATE__ is the same failure
 * with a compiler holding the pen instead of a human. */
void announce_init(const char* device_id, uint32_t build_fingerprint);

/* Fill out[] with the namespaces the caller must SUBSCRIBE (the per-device
 * poll then the broadcast poll) and return the count (2 when initialised,
 * else 0).  Exact subscriptions, never a `_device` wildcard — same rule as
 * restart_namespaces(). */
size_t announce_poll_namespaces(const char** out, size_t cap);

/* Handle a decoded bus arrival.  Returns true iff `ns` was a poll namespace —
 * i.e. a control-plane message CONSUMED here that must NOT be routed to the
 * scene.  A truthy payload (the `dsl_truthy` canon, same as restart) arms an
 * immediate full republication on the next announce_tick().
 *
 * kind mirrors BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool announce_on_bus_value(const char* ns, int kind, double num, bool b,
                           const char* str);

/* Facts the app shell knows and the announcer cannot see for itself.  Each is
 * cheap and idempotent; calling them with an unchanged value does nothing, and
 * calling them with a CHANGED value marks the set dirty so the next tick
 * republishes immediately rather than waiting out the 60 s period. */
void announce_set_scene_hash(uint16_t content_hash);   /* 0 == no scene */
void announce_set_network_up(bool up);
void announce_set_config_stale(bool stale);

/* Drive the cadence.  Call once per loop pass with ms-since-boot; it is
 * non-blocking and publishes at most five messages.  Publishes the heartbeat
 * every kAnnHeartbeatIntervalMs, the full set every kAnnFullIntervalMs, and
 * the full set IMMEDIATELY on a poll or on any tracked change. */
void announce_tick(uint32_t now_ms);

/* Clear all state (namespaces, latches, timers).  For host-test isolation;
 * a no-op cost in firmware. */
void announce_reset(void);

#endif /* ANNOUNCE_H */
