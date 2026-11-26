/*
 * config_relay.h — a node that FORWARDS config chunks without reassembling.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY] [CXC-CONFIG-BUS-TTL]
 *
 * A relay holds TWO substrates and repeats between them, chunk by chunk, as
 * frames arrive.  It MUST NOT buffer a whole transfer:
 *
 *   - a CH32V203 has ~20 KB of RAM.  Buffering a 6 KB payload to re-emit it is
 *     hostile with one downstream device and impossible with several.
 *   - streaming removes reassembly latency at EVERY hop, so depth costs wire
 *     time and nothing else.
 *
 * Sequence and addressing live in the message ([CXC-CONFIG-BUS-ADDRESSING]),
 * so ONLY the endpoint reassembles and a relay is a dumb repeater for this
 * artifact class.  That is what makes arbitrary depth cheap — and there is NO
 * hop-count setting and no deployer who configures topology, because a
 * configured depth "exposes transport specifics that we're trying to abstract
 * away in the first place" and a silent limit is a future undiagnosable
 * failure.
 *
 * TTL IS A LOOP BACKSTOP, NOT A DEPTH DECLARATION, AND ITS EXPIRY IS LOUD.
 * CAN is MULTI-DROP: two relays bridging the same pair of segments will
 * circulate a frame indefinitely, and at 8 bytes a frame that is destructive
 * rather than merely wasteful.  So the header carries a TTL, seeded 8, and
 * each relay decrements it.  A relay that decrements to ZERO must ANNOUNCE —
 * naming the namespace hash and its own device id — because discarding
 * silently would reintroduce exactly the failure this design was corrected to
 * avoid: a loop or an over-deep chain becoming a panel that quietly never
 * updates instead of an EVENT.
 *
 * WHY THIS RUNS ON AN ESP32 AND NOT THE CH32V203 (say it where the next reader
 * will look).  The CH32V203 is a different toolchain end to end — wchisp, no
 * ESP-IDF, its own bring-up — and conflating a brand-new protocol with a
 * brand-new toolchain produces a failure nobody can attribute.  The relay
 * logic here is transport-agnostic by construction: it names only
 * `BusRawFrameFn` and two send functions.  Porting it to the CH32V203 later is
 * a DRIVER SWAP, not a redesign.
 */

#ifndef CONFIG_RELAY_H
#define CONFIG_RELAY_H

#include <cstddef>
#include <cstdint>

#include "bus_wire_codes.h"

/* Write one frame out on the far substrate.  Returns false when it did not go
 * — a relay that loses a frame silently is a transfer that fails the endpoint
 * hash for no discoverable reason. */
typedef bool (*ConfigRelaySendFn)(uint16_t ns_hash, BusMessageType type,
                                  uint8_t scope_bits, const uint8_t* value,
                                  size_t len);

/* `announce` reports a TTL expiry (and any refused forward) onto whichever
 * substrate can still carry it. Injected, so this module names no transport. */
typedef void (*ConfigRelayAnnounceFn)(const char* ns, const char* value);

void config_relay_init(const char* device_id,
                       ConfigRelaySendFn downstream,
                       ConfigRelaySendFn upstream,
                       ConfigRelayAnnounceFn announce);

/* Feed a frame that arrived from UPSTREAM; it is forwarded downstream.
 * Install as the upstream transport's raw sink. */
void config_relay_from_upstream(uint16_t ns_hash, BusMessageType type,
                                uint8_t scope_bits, const uint8_t* value,
                                size_t len, void* ud);

/* Feed a frame that arrived from DOWNSTREAM; it is forwarded upstream.  A leaf
 * must be able to REQUEST and to ACK through the relay, or the whole
 * conversation is one-way and every transfer times out. */
void config_relay_from_downstream(uint16_t ns_hash, BusMessageType type,
                                  uint8_t scope_bits, const uint8_t* value,
                                  size_t len, void* ud);

/* [CXC-DEVICE-BASELINE-RELAY-MEMBRANE] THE SCOPE MEMBRANE.  True iff a frame at
 * this wire scope MAY cross the relay ([CXC-VISION-SCOPE-MEMBRANE], David
 * 2026-08-10: "a relay must respect scope on the PUBLISH path exactly as it
 * does on the subscribe path, or a naive forward-everything relay leaks local
 * chatter onto the shared bus").  LOCAL (wire code 0) is confined to its own
 * segment and NEVER crosses, in either direction; global/remote/auto cross.
 * Pure, host-tested with negative controls.  Scope codes: canonical
 * python/composeio/transport_can.py _SCOPE_CODES (local=0,global=1,remote=2,
 * auto=3), which BP-CAN-ID v2 (dd7e049) puts on the wire. */
bool config_relay_scope_crosses(uint8_t scope_bits);

/* Counters for the periodic banner — a relay that is silently doing nothing
 * looks exactly like a relay that is working. */
uint32_t config_relay_down_frames(void);
uint32_t config_relay_up_frames(void);
uint32_t config_relay_ttl_expiries(void);
uint32_t config_relay_drops(void);
/* Frames the scope membrane BLOCKED from crossing (local-scoped) — a normal,
 * expected count, not an error; reported in the banner so a membrane that is
 * confining traffic is visible rather than looking like a dead relay. */
uint32_t config_relay_membrane_blocked(void);

#endif /* CONFIG_RELAY_H */
