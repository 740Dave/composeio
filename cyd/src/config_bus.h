/*
 * config_bus.h — the LEAF side of [SPEC:CXC-CONFIG-BUS].
 * [CXC-CONFIG-BUS-CHUNKS] [CXC-CONFIG-BUS-LEAF] [CXC-CONFIG-BUS-PUSH]
 *
 * A board with no IP asks for its scene over ComposeBus and reassembles it:
 *
 *     leaf  --CONFIG_REQUEST-->  _device/<id>/config   (value: nothing —
 *                                                       the request IS the
 *                                                       address)
 *     head  --CONFIG_PUSH---->   header frame, then N sequenced data frames
 *     leaf  --ACK----------->    7 BINARY bytes naming the transfer's hash,
 *                                its length, a status, and the sequence a
 *                                repair would resume from — sized to fit a
 *                                classic CAN frame, which the string form it
 *                                replaced could not ([CXC-CONFIG-BUS-CHUNKS])
 *
 * THE CONTRACT IS DEPTH-INDEPENDENT, AND THAT IS WHAT MAKES FAILURE LOUD
 * ([CXC-CONFIG-BUS-LEAF]).  Request, start a timer, then either
 * reassemble-and-verify or ANNOUNCE that no config arrived.  This leaf never
 * knows, and must never need to know, how many nodes lie between it and the
 * store.  Bounding depth would have made a too-deep chain fail QUIETLY;
 * making an unanswered request an EVENT removes the whole class.
 *
 * A HASH MISMATCH DISCARDS AND RE-REQUESTS.  Never build a scene from a
 * plausible-looking ruin: per-frame CRC-8 catches a corrupt frame, but only
 * the whole-payload hash catches a MISSING frame, a duplicated one, or two
 * interleaved transfers.
 *
 * RETRY IS EXPONENTIAL WITH JITTER AND A CAP.  [CXC-CONFIG-BUS-OPEN] named
 * this as unspecified and warned about it: a leaf that re-requests on a fixed
 * short interval storms a bus whose head is down, and a rack of them does it
 * in lockstep.  Jitter is what breaks the lockstep; the cap is what keeps a
 * long outage cheap; and the retry NEVER stops, because a head that comes
 * back must be able to answer a board nobody power-cycled.
 *
 * LIVE PUSH IS FREE ([CXC-CONFIG-BUS-PUSH]).  An unsolicited transfer is the
 * same code path as an answered request — boot-time pull is the degenerate
 * case, not a separate mechanism — so a board that killed its radio can still
 * be re-scened from the head.
 */

#ifndef CONFIG_BUS_H
#define CONFIG_BUS_H

#include <cstddef>
#include <cstdint>

#include "bus_wire_codes.h"

/* Announce to the operator when the bus cannot be reached at all.  Injected so
 * this module never names a transport or a canvas (the restart.cpp shape). */
typedef void (*ConfigBusPublishFn)(const char* ns, const char* value);
/* Called with a one-line human-readable state for the GLASS.  A board with no
 * scene must SAY SO — on the bus if it can, and on its own glass if it cannot,
 * because silence is the one unacceptable response ([CXC-CONFIG-BUS-LEAF]). */
typedef void (*ConfigBusAnnounceFn)(const char* line);

void config_bus_init(const char* device_id, ConfigBusPublishFn publish,
                     ConfigBusAnnounceFn announce);

/* This device's config namespace(s) — subscribe each PERSISTENTLY, so they
 * survive scene swaps exactly like the restart namespaces. */
size_t config_bus_namespaces(const char** out, size_t cap);

/* Send a CONFIG_REQUEST now and arm the retry timer. */
void config_bus_request(void);

/* Feed a decoded NON-STATE frame in.  True iff it was consumed here.  Called
 * from the framed transport's rx path — reassembly happens on the RX TASK, not
 * the render task, on purpose: a 25-frame transfer would overflow the 16-deep
 * arrival queue, and the render loop's touch-poll burst would pace it. */
bool config_bus_on_frame(const char* ns, BusMessageType type,
                         const uint8_t* value, size_t len);

/* Drive the retry/announce timer.  now_ms = ms since boot.  Cheap; call it
 * from the boot wait loop and from the render loop. */
void config_bus_tick(uint32_t now_ms);

/* A completed, hash-verified scene, or nullptr.  Ownership passes to the
 * caller (free()).  Returns each transfer exactly ONCE, so the render loop can
 * poll it every pass and a live push becomes a scene swap for free. */
char* config_bus_take(size_t* out_len);

/* Diagnostics for the boot banner and the status namespace. */
uint32_t config_bus_attempts(void);
uint32_t config_bus_transfers(void);
uint32_t config_bus_mismatches(void);
/* ACKs this leaf PUT ON THE WIRE, and ACKs the transport REFUSED.  Both, and
 * separately: an ACK that was built but never left looks, from the head, exactly
 * like one that was never built — and a counter that can only report success is
 * not an instrument. */
uint32_t config_bus_acks_sent(void);
uint32_t config_bus_ack_failures(void);
bool config_bus_ever_received(void);

#endif /* CONFIG_BUS_H */
