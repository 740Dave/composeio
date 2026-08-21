/*
 * bus_binding.h — WHERE is this board's ComposeBus?  The ESP-IDF sibling of
 * config_source.h.  [SPEC:CXC-REPOINT] [DEC-BUS-BINDING-IS-STATE-NOT-A-COMMAND]
 *
 * config_source selects HOW config is fetched (bus/http/cached); bus_binding
 * selects WHERE FROM — the MQTT broker, bus_storage and document_store URLs,
 * which move together as ONE retained-state fact the device REACTS to
 * ([CXC-REPOINT-AMEND-CONFIG-BUS]: strategy and address are peers).  Both are
 * NVS-resident, both settable over the CURRENT link, both take effect on the
 * next boot.
 *
 * TWO TIERS, SAME NAMESPACE AS config_source ("composeio").  A PER-DEVICE
 * override (`_device/<id>/bus`) over a fleet-wide GLOBAL default
 * (`_device/broadcast/bus`) over the COMPILED FLOOR (wifi_config.h's three
 * #defines — the one address that cannot be corrupted by a bus message,
 * [CXC-REPOINT-COMPILED-DEFAULT]).  Precedence: per-device > global > compiled.
 *
 * ⛔ WHAT THIS HEADER'S CALLERS GET, AND WHAT THEY DON'T.  The three
 * accessors below are the ONLY things bus_mqtt / config_fetch / capture_server
 * should read — the compiled #defines are now the rescue floor alone, resolved
 * THROUGH here.  Every DECISION (parse, precedence, value-compare, arm/confirm/
 * revert, bounce, hatch) lives in the pure, host-tested bus_binding_core.*;
 * this file only persists records in NVS, reads the GPIO, and runs the timer.
 *
 * ⚠ THE REPOINT / CONFIRM / REVERT LOOP IS BENCH-UNVERIFIED.  The state
 * machine is host-proven; the LIVE path — a published binding actually
 * repointing a board, a bad binding auto-reverting within the window, the two
 * hold durations — cannot be proven by a green compile ([CXC-REPOINT-AC-HAPPY]
 * / -IMPOSTOR / -BUTTON are the bench gate).
 */

#ifndef BUS_BINDING_H
#define BUS_BINDING_H

#include <cstddef>
#include <cstdint>

/* ---- The active binding: the three accessors the read sites call ---- */
/* Resolved ONCE, on first call, from NVS (per-device > global) else the
 * compiled floor, honoring a boot-time escape-hatch override.  Each returns a
 * stable pointer valid for the life of the boot. */
const char* bus_binding_active_mqtt(void);
const char* bus_binding_active_store(void);
const char* bus_binding_active_docs(void);

/* Human/telemetry: the active tier's name, the ANNOUNCE source code
 * ([CXC-REPOINT-ANNOUNCE] bits 11..8) and whether the active tier is trialling.
 */
const char* bus_binding_active_name(void);
uint8_t     bus_binding_active_source(void);
bool        bus_binding_active_is_trial(void);

/* ---- Boot sequence ---- */
/* Called EARLY in main(), BEFORE wifi_init() ([CXC-REPOINT-COMMIT-CONFIRMED]
 * ordering-is-load-bearing: the revert timer must be armed before the network
 * so a DHCP/TLS hang still reverts).  Reads the escape-hatch GPIO (two-stage
 * clear per [DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]), resolves the active tier,
 * and if it is a TRIAL arms the one-shot revert timer for `confirm_window_s`.
 * device_id keys the per-device tier and the confirm-echo namespace. */
void bus_binding_boot_init(const char* device_id, uint32_t confirm_window_s);

/* True when the escape hatch cleared NVS this boot (drives the mandatory
 * full-screen `BUS CONFIG CLEARED` panel feedback, [CXC-REPOINT-BUTTON]). */
bool bus_binding_button_cleared(void);

/* ---- Confirmation planes ([CXC-REPOINT-CONFIRM]) ---- */
/* Fed by the app shell during a trial boot.  A trial CONFIRMS only when BOTH
 * arrive; either failing (or the window elapsing) REVERTS.  These are no-ops
 * when the active tier is not trialling. */
void bus_binding_note_store_ok(void);          /* a store fetch returned 200 + body */
/* The store plane is NOT exercised under this config strategy (config:bus /
 * config:cached): the trial confirms on the broker echo ALONE, and the confirm
 * ack discloses it as `(broker only, config_source=<x>)`.  Pass the strategy
 * name.  A no-op after the trial has already confirmed/reverted. */
void bus_binding_note_store_vacuous(const char* config_source);
/* The broker round-trip echo: the app shell subscribes the confirm namespace,
 * publishes the active tier's seq, and calls this with each value received on
 * it.  Only the device's OWN seq counts. */
void bus_binding_note_echo(uint32_t seq);
/* The seq the app shell must publish on the confirm namespace for the echo. */
uint32_t bus_binding_trial_seq(void);
/* The confirm-echo namespace `_device/<id>/bus_confirm` (empty until inited). */
const char* bus_binding_confirm_namespace(void);
/* Record why a plane could not be reached, so a revert can name it
 * ([CXC-REPOINT-REVERT] reason contract).  Overwrites the pending reason. */
void bus_binding_note_reason(const char* reason);

/* ---- Control plane `_device/<id>/bus` + `_device/broadcast/bus` ---- */
/* The restart.cpp / config_source.cpp injected-publish shape: this module must
 * not name a transport. */
typedef void (*BusBindingPublishFn)(const char* ns, const char* value);
void   bus_binding_control_init(const char* device_id, BusBindingPublishFn publish);
/* Fill out[] with the two binding namespaces to SUBSCRIBE (per-device then
 * broadcast); returns the count (2 when inited, else 0). */
size_t bus_binding_control_namespaces(const char** out, size_t cap);
/* Handle a decoded bus arrival.  Returns true iff `ns` was one of this
 * device's binding namespaces (consumed here, never routed to the scene).  A
 * DIFFERING value on the ACTIVE tier arms a trial (writes NVS, restarts via the
 * injected reboot seam); an identical value is absorbed; a value targeting a
 * lower tier is cached without arming ([CXC-REPOINT-PRECEDENCE] cross-tier).
 * kind mirrors BusEvent::Kind (0=Number, 1=Bool, 2=String). */
bool bus_binding_on_bus_value(const char* ns, int kind, double num, bool b,
                              const char* str);

/* Seam: the hard reset performed after arming a trial (esp_restart in
 * firmware; a recorder in a host test).  Injected so the adopt path stays
 * testable and to keep this header ESP-IDF-agnostic at its edge. */
typedef void (*BusBindingRebootFn)(void);
void bus_binding_set_reboot(BusBindingRebootFn reboot);

/* ---- Announce ([CXC-REPOINT-ANNOUNCE]) ---- */
/* The u32 for `_device/<id>/bus`, published as 8 lowercase hex digits on the
 * periodic announce cadence.  Encodes fingerprint/state/source/reverts/flags;
 * the capability-absence flags come from bus_binding_board.h. */
uint32_t bus_binding_announce_word(void);
/* The confirm namespace excluded, the periodic ANNOUNCE namespace for this
 * device (`_device/<id>/bus`); empty until control-inited. */
const char* bus_binding_announce_namespace(void);

/* Drive the periodic cadence.  Call once per loop pass with ms-since-boot; it
 * is non-blocking.  Publishes `_device/<id>/bus` (the ANNOUNCE word) on a
 * bounded interval so a MISSING publication is the fault report, and — while a
 * trial is in flight — re-publishes the confirm seq on the confirm namespace so
 * the broker round-trip echo ([CXC-REPOINT-CONFIRM]) keeps being exercised
 * until the window closes one way or the other. */
void bus_binding_tick(uint32_t now_ms);

/* Clear all module state (for host-test isolation; a no-op cost in firmware). */
void bus_binding_reset(void);

#endif /* BUS_BINDING_H */
