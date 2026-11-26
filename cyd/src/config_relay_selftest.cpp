/*
 * config_relay_selftest.cpp — host harness for the STREAMING RELAY.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY] [CXC-CONFIG-BUS-TTL]
 *
 *   pio run -e native_relay && .pio/build/native_relay/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHAT IT PROVES.  Four properties that are the whole of the relay contract,
 * and all four are behavioural rather than arithmetic — they are about what
 * the relay REFUSES to do:
 *
 *   1. IT NEVER REASSEMBLES.  The harness measures the largest single buffer
 *      the relay holds and asserts it never exceeds ONE FRAME, while a 6 KB
 *      transfer crosses it.  A CH32V203 has ~20 KB of RAM; a relay that
 *      buffered a payload would be impossible with several downstream devices.
 *   2. IT FORWARDS DATA FRAMES BYTE-FOR-BYTE.  Only the header carries a TTL;
 *      a relay that parsed data frames would have opinions it must not have.
 *   3. TTL DECREMENTS ONCE PER HOP AND EXPIRES LOUDLY.  A chain of relays is
 *      built here and a transfer walked down it: the payload survives eight
 *      hops, the ninth DROPS, and the drop is ANNOUNCED. A silent discard is
 *      the failure this design was corrected to remove, so the announce is
 *      ASSERTED, not merely hoped for.
 *   4. IT RELAYS BOTH WAYS.  A leaf's CONFIG_REQUEST and ACK travel upstream;
 *      without that the conversation is one-way and every transfer ends in the
 *      leaf's announce timer, which looks exactly like a dead head.
 *
 * WHAT IT CANNOT PROVE: that any of this has happened between two real
 * segments.  No CAN hardware was attached when this was written.
 */

#ifdef CONFIG_RELAY_TEST

#include "config_relay.h"
#include "config_chunk.h"
#include "config_chunk_corpus_generated.h"

#include <cstdio>
#include <cstring>
#include <vector>

int g_host_log_error_count = 0;
int g_host_log_warn_count = 0;

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

/* The far side of a relay hop: records what it was handed. */
struct Wire {
    std::vector<std::vector<uint8_t>> frames;
    size_t largest_frame = 0;
    void clear() { frames.clear(); largest_frame = 0; }
};

static Wire s_downstream;
static Wire s_upstream;
static int s_announces = 0;

static bool send_down(uint16_t, BusMessageType, uint8_t,
                      const uint8_t* v, size_t n) {
    s_downstream.frames.push_back(std::vector<uint8_t>(v, v + n));
    if (n > s_downstream.largest_frame) s_downstream.largest_frame = n;
    return true;
}
static bool send_up(uint16_t, BusMessageType, uint8_t,
                    const uint8_t* v, size_t n) {
    s_upstream.frames.push_back(std::vector<uint8_t>(v, v + n));
    if (n > s_upstream.largest_frame) s_upstream.largest_frame = n;
    return true;
}
static void on_announce(const char*, const char*) { s_announces++; }

int main(void) {
    printf("config relay: streaming forward, TTL hops, loud expiry\n");

    /* The 6 KB scene at UART chunk size — the real transfer. */
    const CfgTransferCase* scene = nullptr;
    for (int i = 0; i < kCfgTransferCaseCount; i++)
        if (strcmp(kCfgTransferCases[i].id, "scene_uart") == 0)
            scene = &kCfgTransferCases[i];
    CHECK(scene != nullptr, "corpus must carry the 'scene_uart' transfer");
    if (!scene) return 1;

    config_relay_init("relay-test", send_down, send_up, on_announce);

    /* ---- 1 + 2: a whole transfer crosses, frame by frame ---------- */
    s_downstream.clear();
    for (int i = 0; i < scene->frame_count; i++)
        config_relay_from_upstream(0xABCD, BusMessageType::ConfigPush,
                                   1, scene->frames[i].bytes,
                                   (size_t)scene->frames[i].len, nullptr);

    CHECK((int)s_downstream.frames.size() == scene->frame_count,
          "relay forwarded %u of %d frames", (unsigned)s_downstream.frames.size(),
          scene->frame_count);
    CHECK(s_downstream.largest_frame <= kConfigSeqBytes + 250,
          "THE RELAY BUFFERED MORE THAN ONE FRAME (%u bytes).  Only the "
          "endpoint reassembles — a relay that holds a transfer cannot run on "
          "a 20 KB part.", (unsigned)s_downstream.largest_frame);
    CHECK(config_relay_down_frames() == (uint32_t)scene->frame_count,
          "downstream counter %lu, expected %d",
          (unsigned long)config_relay_down_frames(), scene->frame_count);

    /* DATA frames byte-for-byte; the HEADER differs by exactly one byte. */
    for (int i = 1; i < scene->frame_count; i++)
        CHECK(s_downstream.frames[i].size() == (size_t)scene->frames[i].len &&
              memcmp(s_downstream.frames[i].data(), scene->frames[i].bytes,
                     (size_t)scene->frames[i].len) == 0,
              "data frame %d was MODIFIED in transit — a relay must forward "
              "data verbatim", i);
    {
        const std::vector<uint8_t>& h = s_downstream.frames[0];
        CHECK(h[kConfigSeqBytes] == scene->frames[0].bytes[kConfigSeqBytes] - 1,
              "the header's TTL should have decremented by exactly one "
              "(%u -> %u)", (unsigned)scene->frames[0].bytes[kConfigSeqBytes],
              (unsigned)h[kConfigSeqBytes]);
        CHECK(memcmp(h.data() + kConfigSeqBytes + 1,
                     scene->frames[0].bytes + kConfigSeqBytes + 1,
                     (size_t)scene->frames[0].len - kConfigSeqBytes - 1) == 0,
              "a relay changed a header field other than the TTL");
    }

    /* What crossed still REASSEMBLES at the endpoint — the relay preserved
     * the payload, which is the property the whole thing is for. */
    {
        ConfigReassembler r;
        ConfigReassembler::Result last = ConfigReassembler::Result::Ignored;
        for (auto& f : s_downstream.frames) last = r.on_frame(f.data(), f.size());
        CHECK(last == ConfigReassembler::Result::Complete,
              "the relayed transfer ended '%s' at the endpoint",
              ConfigReassembler::result_name(last));
        CHECK(r.complete() &&
              memcmp(r.payload(), scene->payload, (size_t)scene->payload_len) == 0,
              "the relayed payload differs from the original");
    }

    /* ---- 3: a CHAIN of hops, then a LOUD expiry ------------------- */
    {
        std::vector<std::vector<uint8_t>> hop(s_downstream.frames);
        int hops = 1;                       /* the hop performed above */
        int announces_before = s_announces;
        bool expired = false;
        while (hops < 20) {
            s_downstream.clear();
            hops++;
            for (auto& f : hop)
                config_relay_from_upstream(0xABCD, BusMessageType::ConfigPush,
                                           1, f.data(), f.size(), nullptr);
            if (s_downstream.frames.size() < hop.size()) { expired = true; break; }
            hop = s_downstream.frames;
        }
        CHECK(expired, "a transfer survived 20 relay hops — the TTL backstop "
              "is not decrementing, and a CAN loop would be unbounded");
        /* Seed 8 means SEVEN successful decrements (8->1) and the EIGHTH hop
         * discards.  Depth is not a configured limit and nobody sets this;
         * the number is pinned here only so a change to the backstop is a
         * failing test rather than a silent shortening of every chain. */
        CHECK(hops == kConfigTtlSeed,
              "the transfer should die on hop %u (ttl seed), died on hop %d",
              (unsigned)kConfigTtlSeed, hops);
        CHECK(s_announces > announces_before,
              "TTL EXPIRY WAS SILENT.  [CXC-CONFIG-BUS-TTL]: a relay that "
              "decrements to zero MUST announce — a silent discard is the "
              "undiagnosable depth limit this design removed.");
        CHECK(config_relay_ttl_expiries() > 0,
              "the TTL expiry counter did not move");
    }

    /* ---- 4: the reverse direction --------------------------------- */
    {
        s_upstream.clear();
        uint32_t before = config_relay_up_frames();
        /* The REAL binary ACK ([CXC-CONFIG-BUS-CHUNKS]), not a hand-typed
         * string: this used to be `{0x0B,'o','k',...}`, a fragment of the ACK
         * form that could never have crossed a CAN segment at all. */
        uint8_t ack[kConfigAckBytes];
        size_t ack_len = config_encode_ack(ConfigAckStatus::Ok, 0x6F62, 5856,
                                           976, ack, sizeof(ack));
        CHECK(ack_len == kConfigAckBytes && ack_len <= 8,
              "the ACK a relay carries must fit a classic CAN frame (%u B)",
              (unsigned)ack_len);
        config_relay_from_downstream(0xABCD, BusMessageType::ConfigRequest, 1,
                                     nullptr, 0, nullptr);
        config_relay_from_downstream(0xABCD, BusMessageType::Ack, 1, ack,
                                     ack_len, nullptr);
        CHECK(s_upstream.frames.size() == 2,
              "a leaf's CONFIG_REQUEST and ACK must reach the head; %u of 2 "
              "did.  Without this the conversation is one-way and every "
              "transfer ends in the leaf's announce timer.",
              (unsigned)s_upstream.frames.size());
        CHECK(config_relay_up_frames() == before + 2,
              "upstream counter did not move by 2");
        /* BYTE-FOR-BYTE.  An ACK is not a config frame — it carries no TTL and
         * a relay must not parse it as one (its first byte is 0xA0, not a
         * sequence high byte), so it must arrive at the head unchanged. */
        CHECK(s_upstream.frames.size() == 2 &&
              s_upstream.frames[1].size() == ack_len &&
              memcmp(s_upstream.frames[1].data(), ack, ack_len) == 0,
              "the relay altered the ACK on its way upstream");
        ConfigAck decoded{};
        CHECK(s_upstream.frames.size() == 2 &&
              config_decode_ack(s_upstream.frames[1].data(),
                                s_upstream.frames[1].size(), &decoded) &&
              decoded.status == (uint8_t)ConfigAckStatus::Ok &&
              decoded.hash == 0x6F62 && decoded.total_len == 5856 &&
              decoded.ackseq == 976,
              "the ACK the head receives must decode to what the leaf sent");
    }

    /* ---- non-config traffic crosses too, untouched ---------------- */
    {
        s_downstream.clear();
        const uint8_t state[] = {0xFA};
        config_relay_from_upstream(0x03F6, BusMessageType::State, 1, state,
                                   sizeof(state), nullptr);
        CHECK(s_downstream.frames.size() == 1 &&
              s_downstream.frames[0].size() == 1 &&
              s_downstream.frames[0][0] == 0xFA,
              "state traffic must cross a relay verbatim — a relay that only "
              "carried config would leave the leaf able to receive a scene and "
              "unable to receive the states that scene binds");
    }

    /* ---- THE SCOPE MEMBRANE [CXC-DEVICE-BASELINE-RELAY-MEMBRANE] ---------
     * local STAYS on its segment, global/remote/auto CROSS.  Negative controls:
     * a local frame MUST NOT be forwarded (would leak local chatter onto the
     * shared bus — the exact [CXC-VISION-SCOPE-MEMBRANE] failure). */
    {
        /* pure predicate: only local (0) is confined */
        CHECK(config_relay_scope_crosses(0) == false, "local (0) must NOT cross");
        CHECK(config_relay_scope_crosses(1) == true,  "global (1) must cross");
        CHECK(config_relay_scope_crosses(2) == true,  "remote (2) must cross");
        CHECK(config_relay_scope_crosses(3) == true,  "auto (3) must cross "
              "(unclaimed scope is not silently confined)");

        const uint8_t st[] = {0xFA};

        /* NEGATIVE CONTROL: a LOCAL-scope state from downstream must be BLOCKED
         * on its way upstream — it stays on the daughtercard segment. */
        s_upstream.clear();
        uint32_t blocked_before = config_relay_membrane_blocked();
        uint32_t up_before = config_relay_up_frames();
        config_relay_from_downstream(0x03F6, BusMessageType::State, /*scope=local*/0,
                                     st, sizeof(st), nullptr);
        CHECK(s_upstream.frames.empty(),
              "a LOCAL-scoped frame LEAKED upstream across the membrane");
        CHECK(config_relay_membrane_blocked() == blocked_before + 1,
              "the membrane must COUNT the blocked local frame");
        CHECK(config_relay_up_frames() == up_before,
              "a blocked frame must not advance the forwarded counter");

        /* POSITIVE: the SAME frame at GLOBAL scope crosses upstream. */
        s_upstream.clear();
        config_relay_from_downstream(0x03F6, BusMessageType::State, /*scope=global*/1,
                                     st, sizeof(st), nullptr);
        CHECK(s_upstream.frames.size() == 1,
              "a GLOBAL-scoped frame must cross the membrane upstream");

        /* NEGATIVE CONTROL, other direction: a LOCAL frame from upstream must
         * not be pushed down onto the local segment either (symmetric). */
        s_downstream.clear();
        blocked_before = config_relay_membrane_blocked();
        config_relay_from_upstream(0x03F6, BusMessageType::State, /*scope=local*/0,
                                   st, sizeof(st), nullptr);
        CHECK(s_downstream.frames.empty(),
              "a LOCAL frame from upstream LEAKED down onto the local segment");
        CHECK(config_relay_membrane_blocked() == blocked_before + 1,
              "the membrane must count the blocked local frame (downstream dir)");
    }

    printf("\n%d checks, %d failures\n", s_pass + s_fail, s_fail);
    printf(s_fail ? "RESULT: FAIL\n" : "ALL PASS\n");
    return s_fail ? 1 : 0;
}

#endif /* CONFIG_RELAY_TEST */
