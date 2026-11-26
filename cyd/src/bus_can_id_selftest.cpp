/*
 * bus_can_id_selftest.cpp — host PARITY harness for the [BP-CAN-ID] v2 layout.
 * [SPEC:CXC-CROSS-TIER-CAN] [CXC-CAN-BRIDGE-MIGRATE] [BP-TYPE-CODES]
 * [BP-WIRE-PRIORITY]
 *
 * Pure host test (no hardware, no ESP-IDF) — the guarded-file idiom of
 * bus_frame_selftest.cpp; the body is behind CAN_ID_CORPUS_TEST, defined only
 * by [env:native_can_id].
 *
 *   pio run -e native_can_id && .pio/build/native_can_id/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHY THIS EXISTS AND WHAT IT DOES NOT CLAIM.  There is no CAN hardware on any
 * board in this tree today, so the CAN transport has never carried a live
 * message.  The arbitration id, though, is pure arithmetic AND it is the part
 * that fails invisibly: a mis-shifted namespace field builds, boots, logs
 * "started", and puts frames on the wire that no peer will ever match.  The
 * oracle here is `services/can_mqtt_bridge`, which has spoken this family of
 * layouts to real hardware — so what is checked is "the id a live peer expects".
 *
 * WHAT CHANGED AT v2.  The v1 corpus masked the source field out and compared
 * 20 of 29 bits, because the two sides were KNOWN to disagree there and the test
 * was built not to notice.  `src` is now an allocated node id both sides can
 * name, so ALL 29 BITS are compared — and the corpus is baked at node 5, not
 * node 0, so a firmware that dropped the field entirely cannot pass.
 *
 * IT PROVES NOTHING ABOUT: bit timing, transceiver wiring, termination,
 * acknowledgement, arbitration, or that a TWAI driver comes up.  Those need a
 * segment with two nodes on it.
 */

#ifdef CAN_ID_CORPUS_TEST

#include "bus_can_id.h"
#include "bus_frame.h"
#include "can_id_corpus_generated.h"

#include <cstdio>
#include <cstring>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

int main(void) {
    printf("CAN arbitration-id parity ([BP-CAN-ID] v2): "
           "services/can_mqtt_bridge (oracle) vs cyd/src/bus_can_id.h\n");

    /* The layout itself, before any case: a field width that drifted would
     * otherwise show up as 110 identical failures with no cause named. */
    CHECK(kCanPrioBits == 3 && kCanTypeBits == 4 && kCanNsBits == 16 &&
          kCanSrcBits == 4 && kCanScopeBits == 2,
          "field widths: prio %d type %d ns %d src %d scope %d "
          "(expected 3/4/16/4/2)", kCanPrioBits, kCanTypeBits, kCanNsBits,
          kCanSrcBits, kCanScopeBits);
    CHECK(kCanScopeShift == 0 && kCanSrcShift == 2 && kCanNsShift == 6 &&
          kCanTypeShift == 22 && kCanPrioShift == 26,
          "field shifts: scope %d src %d ns %d type %d prio %d "
          "(expected 0/2/6/22/26)", kCanScopeShift, kCanSrcShift, kCanNsShift,
          kCanTypeShift, kCanPrioShift);
    CHECK(kBusCanNodeMax == 15 && kBusCanNodeUnassigned == 0,
          "node id range: 0..%u, unassigned %u (expected 0..15, 0)",
          (unsigned)kBusCanNodeMax, (unsigned)kBusCanNodeUnassigned);

    for (int i = 0; i < kCanIdCaseCount; i++) {
        const CanIdCase& c = kCanIdCases[i];

        /* The namespace hash the firmware computes must match the one the
         * bridge hashed — same CRC-16, one implementation, no second hash. */
        uint16_t h = bus_ns_hash(c.ns);
        CHECK(h == c.ns_hash, "%s: ns hash Python 0x%04X, C++ 0x%04X", c.ns,
              c.ns_hash, h);

        /* The type nibble comes from THE ONE TABLE, never from the enum's
         * ordinal ([BP-TYPE-CODES]). */
        BusMessageType t;
        CHECK(bus_message_type_from_name(c.type_name, &t),
              "%s: C++ knows message type '%s'", c.ns, c.type_name);
        if (!bus_message_type_from_name(c.type_name, &t)) continue;
        uint8_t code = 0;
        CHECK(bus_wire_code_for_type(t, &code),
              "%s: type '%s' has an allocated wire code", c.ns, c.type_name);
        CHECK(code == c.type_code, "%s/%s: wire code Python %u, C++ %u", c.ns,
              c.type_name, (unsigned)c.type_code, (unsigned)code);

        /* THE PRIORITY TIER, evaluated — not just the table compared.  Two
         * implementations can hold identical tables and still disagree,
         * because the ANSWER is a matcher (exact vs prefix vs default) and a
         * matcher is where a port goes wrong.  This is the check that catches
         * a firmware that forgot the trailing-'/' prefix rule, or that
         * defaulted an unlisted namespace to 0 instead of 5. */
        uint8_t prio = bus_priority_for(c.ns, t);
        CHECK(prio == c.prio,
              "%s/%s: priority tier Python %u, C++ %u ([BP-WIRE-PRIORITY])",
              c.ns, c.type_name, (unsigned)c.prio, (unsigned)prio);

        uint8_t scope_bits = bus_scope_bits_for_name(c.scope_name);

        /* THE LOAD-BEARING ONE: the WHOLE id, all 29 bits, nothing masked. */
        uint32_t id = bus_can_id_encode(prio, h, code, kCanCorpusNodeIdPy,
                                        scope_bits);
        CHECK(id == c.id, "%s/%s scope=%s: id Python 0x%08X, C++ 0x%08X", c.ns,
              c.type_name, c.scope_name, c.id, id);

        /* Round trip: a peer's id must decode back to the same ns hash, type,
         * source node and SCOPE on this side.  (Every half is checked against
         * Python above, so this is not the self-consistency trap — it is the
         * decode path.)  Scope is the field v1 did not have at all, so it was
         * reconstructed as "global" on every inbound frame; this is the check
         * that proves it now survives. */
        CHECK(bus_can_id_ns_hash(c.id) == c.ns_hash,
              "%s: decode ns hash from 0x%08X gave 0x%04X, expected 0x%04X",
              c.ns, c.id, bus_can_id_ns_hash(c.id), c.ns_hash);
        CHECK(bus_can_id_type_code(c.id) == c.type_code,
              "%s/%s: decode type code gave %u, expected %u", c.ns,
              c.type_name, (unsigned)bus_can_id_type_code(c.id),
              (unsigned)c.type_code);
        CHECK(bus_can_id_source(c.id) == kCanCorpusNodeIdPy,
              "%s/%s: decode src gave %u, expected %u", c.ns, c.type_name,
              (unsigned)bus_can_id_source(c.id),
              (unsigned)kCanCorpusNodeIdPy);
        CHECK(strcmp(bus_scope_name_for_bits(bus_can_id_scope_bits(c.id)),
                     c.scope_name) == 0,
              "%s/%s: decode scope gave '%s', expected '%s' — scope is LOST "
              "on this substrate again", c.ns, c.type_name,
              bus_scope_name_for_bits(bus_can_id_scope_bits(c.id)),
              c.scope_name);
        CHECK(bus_can_id_priority(c.id) == c.prio,
              "%s/%s: decode prio gave %u, expected %u", c.ns, c.type_name,
              (unsigned)bus_can_id_priority(c.id), (unsigned)c.prio);

        /* Every id must fit 29 bits.  A 30th bit is silently truncated by the
         * controller into somebody else's namespace. */
        CHECK(id <= 0x1FFFFFFFu, "%s/%s: id 0x%08X exceeds 29 bits", c.ns,
              c.type_name, id);
    }

    /* ARBITRATION ORDER — the property the whole layout change exists for,
     * asserted directly rather than inferred from the ids above.
     *
     * On CAN a numerically LOWER id wins.  Under v1 these two comparisons went
     * the WRONG WAY: `shields` (0xCC50) was the lowest-priority thing on the
     * bus, and `ConfigPush` (type code 2) outranked the `Ack` (code 8) that
     * terminates it. */
    {
        uint8_t st = 0, cp = 0, ack = 0;
        bus_wire_code_for_type(BusMessageType::State, &st);
        bus_wire_code_for_type(BusMessageType::ConfigPush, &cp);
        bus_wire_code_for_type(BusMessageType::Ack, &ack);
        const uint8_t g = bus_scope_bits_for_name("global");

        uint32_t alarm = bus_can_id_encode(
            bus_priority_for("alert_status", BusMessageType::State),
            bus_ns_hash("alert_status"), st, 1, g);
        uint32_t bulk = bus_can_id_encode(
            bus_priority_for("_device/hosyond-080739/config",
                             BusMessageType::ConfigPush),
            bus_ns_hash("_device/hosyond-080739/config"), cp, 1, g);
        uint32_t ack_id = bus_can_id_encode(
            bus_priority_for("_bus/ack", BusMessageType::Ack),
            bus_ns_hash("_bus/ack"), ack, 1, g);

        CHECK(alarm < bulk,
              "an alarm STATE (0x%08X) must WIN arbitration against a bulk "
              "CONFIG_PUSH (0x%08X)", alarm, bulk);
        CHECK(ack_id < bulk,
              "an ACK (0x%08X) must WIN arbitration against the bulk transfer "
              "it terminates (0x%08X)", ack_id, bulk);
        CHECK(bus_priority_for("shields", BusMessageType::State) <
                  bus_priority_for("_app/console/bbox", BusMessageType::State),
              "`shields` must outrank a console bounding box");
        /* Renaming a signal must NOT re-rank it — the v1 defect in one line.
         * Both of these are tier 1 by the `throttle/` prefix rule whatever
         * their hashes happen to be. */
        CHECK(bus_priority_for("throttle/left", BusMessageType::State) ==
                  bus_priority_for("throttle/right", BusMessageType::State),
              "two signals of identical role must get identical tiers");
        /* An unlisted namespace gets the DEFAULT, not an error and not 0. */
        CHECK(bus_priority_for("something/nobody/classified",
                               BusMessageType::State) == kBusDefaultPriority,
              "an unlisted namespace must get the default tier %u, got %u",
              (unsigned)kBusDefaultPriority,
              (unsigned)bus_priority_for("something/nobody/classified",
                                         BusMessageType::State));
        CHECK(bus_priority_for(nullptr, BusMessageType::State) ==
                  kBusDefaultPriority,
              "a NULL namespace must get the default tier, not crash");
    }

    /* Field isolation: an oversized value in one field must be MASKED into its
     * own bits and must never carry into a neighbour — a source that bled into
     * the type nibble would RELABEL the message. */
    {
        uint16_t h = bus_ns_hash("throttle/left");
        const uint8_t g = bus_scope_bits_for_name("global");
        uint32_t a = bus_can_id_encode(1, h, 0, 0, g);
        uint32_t b = bus_can_id_encode(1, h, 0, 0xFF, g);
        CHECK(bus_can_id_ns_hash(b) == h && bus_can_id_type_code(b) == 0 &&
                  bus_can_id_priority(b) == 1,
              "an oversized source must be masked, not carried into ns/type/prio");
        CHECK(bus_can_id_source(a) == 0, "src 0 round trip");
        CHECK(bus_can_id_source(b) == 0x0F, "an oversized src masks to 0x0F");
        uint32_t c = bus_can_id_encode(0xFF, h, 0, 0, g);
        CHECK(bus_can_id_type_code(c) == 0 && bus_can_id_ns_hash(c) == h,
              "an oversized prio must be masked, not carried into type/ns");
        CHECK(bus_can_id_encode(7, 0xFFFF, 0x0F, 0x0F, 0x03) <= 0x1FFFFFFFu,
              "the all-ones id must still fit 29 bits");
    }

    printf("\n%d checks, %d failures\n", s_pass + s_fail, s_fail);
    printf(s_fail ? "RESULT: FAIL\n" : "ALL PASS\n");
    return s_fail ? 1 : 0;
}

#endif /* CAN_ID_CORPUS_TEST */
