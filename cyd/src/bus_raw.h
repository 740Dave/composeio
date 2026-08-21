/*
 * bus_raw.h — the FRAMED-substrate relay hook.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY]
 *
 * A relay forwards frames CHUNK BY CHUNK, as they arrive, for namespaces it
 * has never heard of.  Two consequences follow, and both are why this hook
 * exists separately from `BusTransport`:
 *
 *   1. It is BELOW the namespace.  The wire carries a 16-bit HASH, not a name
 *      ([BP-UART-FRAME], [BP-CAN-ID]), and a hash is not reversible.  A relay
 *      that had to name what it forwards could only forward what it was
 *      configured to know — which is a routing table, which is the one thing
 *      [SPEC:CXC-CONFIG-BUS] refuses to have.
 *   2. It MUST NOT REASSEMBLE.  A CH32V203 has ~20 KB of RAM and cannot buffer
 *      a 6 KB payload to re-emit it; with several downstream devices it is not
 *      merely hostile but impossible.  Streaming also removes reassembly
 *      latency at every hop.  Only the ENDPOINT reassembles.
 *
 * This is NOT part of the `BusTransport` ABC on purpose.  `BusTransport` is
 * the seam an APP sees, and an app must never see one; a relay is a different
 * kind of node and names its two substrates explicitly, exactly as a bridge
 * does (`services/can_mqtt_bridge` names MQTT and CAN).
 */

#ifndef BUS_RAW_H
#define BUS_RAW_H

#include <cstddef>
#include <cstdint>

#include "bus_wire_codes.h"

/* One decoded frame, still identified only by its namespace HASH.  `value`
 * points into the receiving driver's buffer and is valid for the duration of
 * the call ONLY — a sink that wants to keep bytes must copy them, and a sink
 * that wants to keep a whole transfer is doing the thing this design forbids. */
typedef void (*BusRawFrameFn)(uint16_t ns_hash, BusMessageType type,
                              uint8_t scope_bits, const uint8_t* value,
                              size_t len, void* ud);

#endif /* BUS_RAW_H */
