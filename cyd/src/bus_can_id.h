/*
 * bus_can_id.h — the [BP-CAN-ID] 29-bit arbitration-id layout, on its own.
 * [CXC-CAN-BRIDGE-MIGRATE] [BP-TYPE-CODES] [BP-WIRE-PRIORITY]
 *
 *   29-bit extended id:  prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
 *   bit positions:       28..26  | 25..22  | 21..6     | 5..2   | 1..0
 *
 * VERSION 2, ratified 2026-08-13.  v1 was `ns_hash(16) | type(4) | src(9)`,
 * and python/composeio/transport_can.py spoke a THIRD layout
 * (`type(4)|scope(2)|ns(16)|seq(7)`).  Those two did not merely differ in bit
 * positions — they disagreed about what arbitration is FOR — and were recorded
 * as [BP-CAN-ID-DIVERGENCE], "cannot share a physical wire until reconciled".
 * There is now exactly ONE layout: this header, transport_can.py and
 * services/can_mqtt_bridge, parity-tested by env:native_can_id.
 *
 * WHAT EACH FIELD IS FOR
 *
 *   prio(3)  Arbitration tier, 0 most urgent, 7 bulk ([BP-WIRE-PRIORITY],
 *            bus_priority_for() in bus_wire_codes.h).  SENDER-SIDE ONLY:
 *            nothing decodes it, and this header deliberately offers no
 *            accessor for it on the receive path.  It exists because in v1
 *            `ns_hash` occupied the top 16 bits, so arbitration priority was a
 *            pseudo-random function of the namespace NAME — `throttle/left`
 *            (0x03F6) won everything by luck, `_device/wsc6-07f199/config`
 *            (0x8921) outranked `_device/hosyond-080739/config` (0xC6B6) on a
 *            MAC suffix, and `shields` (0xCC50) was the lowest-priority thing
 *            on the bus.  Renaming a signal silently re-ranked it.
 *
 *   type(4)  [BP-TYPE-CODES], UNCHANGED.  Renumbering the type codes by urgency
 *            was proposed and REJECTED: it breaks the UART frame's type_scope
 *            byte for a property point-to-point UART cannot use, welds identity
 *            to urgency permanently, and cannot express WITHIN-class priority
 *            at all.  `prio` makes it unnecessary.
 *
 *   ns_id(16) bus_ns_hash() — CRC-16/CCITT-FALSE.  SAME WIDTH as v1, so this
 *            change adds no collision exposure; the existing birthday curve
 *            (2.3% at 56 namespaces, 39% at 256) is inherited, not worsened.
 *
 *   src(4)   An ALLOCATED node id, 1..15, 0 = kBusCanNodeUnassigned.  NOT a
 *            hash slice.  v1 used `hash(source) & 0x1FF` on the bridge side,
 *            where CPython's str hash is SALTED PER PROCESS — that field
 *            differed between two runs of the same bridge and could not be an
 *            oracle for anything.  Four bits is a diagnostic ("which box put
 *            this on the wire"), never an address: CAN does not address nodes.
 *
 *   scope(2) local/global/remote/auto, AT THE BOTTOM.  v1 had NO scope field,
 *            so scope was silently LOST on every CAN round trip and
 *            reconstructed as "global" on the far side.  That is load-bearing:
 *            an internal CAN trunk between a mothercard and its daughtercards
 *            is a LOCALITY, and a relay that cannot see scope leaks all local
 *            chatter upstream.
 *
 * WHY scope IS AT THE BOTTOM AND NOT DIRECTLY UNDER type.  Under `type` it
 * becomes a second, undesigned arbitration axis — and a BACKWARDS one, because
 * `auto` (3) is the default for any unspecified message and would put every
 * unspecified message at the bottom of its class.  Below `ns_id` it cannot
 * distort arbitration between two different messages at all.
 *
 * SPLIT OUT OF bus_can.cpp SO IT CAN BE HOST-TESTED.  The id is the one part
 * of the CAN binding that is pure arithmetic, and it is also the part that a
 * board with no CAN hardware attached can still get WRONG in a way nothing
 * would notice until a wire existed.  env:native_can_id compares these
 * functions against ids baked FROM services/can_mqtt_bridge, which has spoken
 * this family of layouts to real hardware.
 *
 * THE PARITY CORPUS NOW COVERS ALL 29 BITS.  Under v1 it covered 20 of 29 and
 * masked the source field out, because the two sides were KNOWN to disagree
 * there (salted hash vs bus_ns_hash) and the test was built not to notice.
 * With `src` an allocated number both sides can name, there is nothing left to
 * mask: the corpus pins prio, type, ns_id, src and scope.
 *
 * Pure C++ (no ESP-IDF): it host-compiles, same idiom as bus_frame.h.
 */

#ifndef BUS_CAN_ID_H
#define BUS_CAN_ID_H

#include <cstdint>

#include "bus_wire_codes.h"

/* Widths first; every shift below is DERIVED, so the layout is stated once. */
static constexpr int kCanPrioBits = kBusPriorityBits;   /* 3 */
static constexpr int kCanTypeBits = kBusWireTypeCodeBits; /* 4 */
static constexpr int kCanNsBits = 16;
static constexpr int kCanSrcBits = 4;
static constexpr int kCanScopeBits = 2;
static constexpr int kCanIdBits = 29;

static_assert(kCanPrioBits + kCanTypeBits + kCanNsBits + kCanSrcBits +
                  kCanScopeBits == kCanIdBits,
              "[BP-CAN-ID] v2 must sum to exactly 29 bits");

static constexpr int kCanScopeShift = 0;                              /* 0  */
static constexpr int kCanSrcShift = kCanScopeShift + kCanScopeBits;   /* 2  */
static constexpr int kCanNsShift = kCanSrcShift + kCanSrcBits;        /* 6  */
static constexpr int kCanTypeShift = kCanNsShift + kCanNsBits;        /* 22 */
static constexpr int kCanPrioShift = kCanTypeShift + kCanTypeBits;    /* 26 */

static constexpr uint32_t kCanPrioMask = (1u << kCanPrioBits) - 1u;
static constexpr uint32_t kCanTypeMask = (1u << kCanTypeBits) - 1u;
static constexpr uint32_t kCanNsMask = (1u << kCanNsBits) - 1u;
static constexpr uint32_t kCanSrcMask = (1u << kCanSrcBits) - 1u;
static constexpr uint32_t kCanScopeMask = (1u << kCanScopeBits) - 1u;
static constexpr uint32_t kCanIdMask = (1u << kCanIdBits) - 1u;

/* src == 0 means "no node id has been allocated to me".  Honest, and
 * distinguishable from node 1 — which a hash slice never was. */
static constexpr uint8_t kBusCanNodeUnassigned = 0;
static constexpr uint8_t kBusCanNodeMax = (uint8_t)kCanSrcMask;   /* 15 */

/* EVERY bit is routed on or preserved now, so there is no "routing subset"
 * mask any more.  kCanRoutingMask survives as the whole id precisely so that a
 * reader who remembers the v1 masking sees, in one line, that it stopped. */
static constexpr uint32_t kCanRoutingMask = kCanIdMask;

inline uint32_t bus_can_id_encode(uint8_t prio, uint16_t ns_hash,
                                  uint8_t type_code, uint8_t src_node,
                                  uint8_t scope_bits) {
    return (((uint32_t)prio & kCanPrioMask) << kCanPrioShift) |
           (((uint32_t)type_code & kCanTypeMask) << kCanTypeShift) |
           (((uint32_t)ns_hash & kCanNsMask) << kCanNsShift) |
           (((uint32_t)src_node & kCanSrcMask) << kCanSrcShift) |
           (((uint32_t)scope_bits & kCanScopeMask) << kCanScopeShift);
}

inline uint16_t bus_can_id_ns_hash(uint32_t id) {
    return (uint16_t)((id >> kCanNsShift) & kCanNsMask);
}

inline uint8_t bus_can_id_type_code(uint32_t id) {
    return (uint8_t)((id >> kCanTypeShift) & kCanTypeMask);
}

inline uint8_t bus_can_id_source(uint32_t id) {
    return (uint8_t)((id >> kCanSrcShift) & kCanSrcMask);
}

inline uint8_t bus_can_id_scope_bits(uint32_t id) {
    return (uint8_t)((id >> kCanScopeShift) & kCanScopeMask);
}

/* The tier the SENDER chose.  Provided for captures and for the parity test,
 * NOT for the receive path: it is the sender's own opinion of its own urgency,
 * it has already done its only job (winning or losing arbitration) by the time
 * a frame is in hand, and a receiver that routed on it would be acting on an
 * unverifiable claim. */
inline uint8_t bus_can_id_priority(uint32_t id) {
    return (uint8_t)((id >> kCanPrioShift) & kCanPrioMask);
}

#endif /* BUS_CAN_ID_H */
