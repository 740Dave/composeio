/*
 * ns_announce.h — THE WELL-KNOWN ROOT, firmware side.  The wire-visible math.
 * [SPEC:CXC-CAN] [SPEC:CXC-CONFIG-BUS] [SPEC:CXC-BOARD-HW] — workstream G1/G2
 * of docs/PLAN-fleet-baseline-and-bus-native.md.
 *
 * THE PROBLEM THIS EXISTS TO SOLVE, and it is not the one it looks like.
 * announce.h's own header carries a ⚠ saying CAN IS NOT REACHABLE THROUGH THE
 * BRIDGE, and explains why: `_device/<id>/*` embeds a per-device id, the 16-bit
 * wire id is a CRC over the FULL name, so a bridge cannot expand `_device/*`
 * into hashes without already knowing every device id — WHICH IS THE THING
 * BEING DISCOVERED.  It is circular.  Nine registry entries were hand-typed
 * during one C3 bring-up and a tenth board reports an irreversible number.
 *
 * So a board announces its namespace NAMES.  `_device/announce` (0x31AE) is
 * hardcoded in every bridge AND every node — the one entry that cannot be
 * learned — and everything else is discovered from what rides on it.  Same
 * shape as a DNS root: one constant so that no other address has to be one.
 *
 * THIS FILE IS PURE C++ — no ESP-IDF, no transport — for the same reason
 * announce_pack.h is: it is the SECOND implementation of a format whose first
 * implementation is python/composeio/ns_announce.py, and two copies of a format
 * is exactly the shape that produced the [BP-TYPE-CODES] defect.  Drift is a
 * FAILING TEST ([env:native_announce]) rather than a board nobody can name.
 * ns_announce.cpp holds the cadence and the publishing, which need the seam.
 *
 * ⚠ WHAT THIS DOES NOT BUY.  Nothing here adds bits.  A 16-bit id collides with
 * probability 2.3% at 56 namespaces, 7.3% at 100 and 39% at 256, and a
 * collision is not a drop — it is a receiver confidently decoding the WRONG
 * namespace.  Announcement makes a collision VISIBLE AND REFUSED at the bridge
 * instead of silent and wrong.  It buys no headroom whatsoever.  The endpoint
 * is to stop hashing and ALLOCATE ids from a registrar, which a dynamic table
 * finally makes possible; deliberately not built, deliberately not foreclosed.
 */

#ifndef NS_ANNOUNCE_H
#define NS_ANNOUNCE_H

#include <cstddef>
#include <cstdint>
#include <cstring>

/* For the frame builder below. Declarations only — [env:native_announce] links
 * config_chunk.cpp, and every board that includes ns_announce.cpp already
 * builds it (config_chunk is what carries the announcement). */
#include "config_chunk.h"

/* ------------------------------------------------------------------ */
/* THE ROOT                                                            */
/* ------------------------------------------------------------------ */

/* The one namespace whose hash is a CONSTANT everywhere. */
static const char* const kNsAnnounce = "_device/announce";

/* The poll that makes a node re-send its names.  REUSED from workstream 0.3's
 * fleet poll rather than duplicated: "what are you" and "what are you called"
 * are the same question asked of the same board, and a second subscription is
 * not free on a 2 KB-class part. */
static const char* const kNsAnnouncePoll = "_device/broadcast/announce";

/*
 * PINNED.  These are not "the hash of that string" — they are the numbers every
 * already-flashed node and every deployed bridge ALREADY AGREE ON, and that is
 * a different kind of fact.  Renaming kNsAnnounce would leave every board in
 * the field transmitting on an id nothing listens to: a fleet-wide silence with
 * no error anywhere, which is the exact failure class this workstream removes.
 *
 * ns_announce_selftest (in announce_selftest.cpp, [env:native_announce])
 * asserts these against bus_ns_hash() AND against the Python constant.  Three
 * places, one number, checked — instead of three places and a hope.
 */
static constexpr uint16_t kNsAnnounceHash = 0x31AE;
static constexpr uint16_t kNsAnnouncePollHash = 0xC77C;

/* ------------------------------------------------------------------ */
/* THE PAYLOAD                                                         */
/* ------------------------------------------------------------------ */
/*
 * Newline-separated ASCII names, and nothing else.  No version byte, no count,
 * no per-name length: the config_chunk transfer carrying this already states
 * its own total length AND a CRC-16 over exactly these bytes, and a second
 * framing layer is a second thing that can disagree with the first.
 *
 * FULL names, never "a device id plus suffixes to recombine".  Recombination is
 * the prefix rule wearing a disguise — the receiver would have to know the
 * naming convention, and a board with one namespace off the pattern (a plain
 * `throttle/left`, no `_device/` at all) could not announce it.
 */
static constexpr char kNsAnnounceSep = '\n';

/* A single name's ceiling.  NOT arbitrary: BusEvent::ns is char[96], so a name
 * a receiver could not hold is one no node can usefully announce.  REFUSED at
 * both ends, never truncated — a truncated namespace is a DIFFERENT namespace
 * that resolves cleanly, the same failure class as the truncated CAN value the
 * bridge was rebuilt to stop producing. */
static constexpr size_t kNsAnnounceMaxNameBytes = 95;

/* Names per announcement.  A bound exists so a corrupt-but-CRC-valid payload
 * cannot make a receiver allocate without limit.  A baseline board has 6. */
static constexpr size_t kNsAnnounceMaxNames = 64;

/* The payload buffer a board must own.  Sized for the worst legal case this
 * firmware can actually produce (kNsAnnounceMaxNames is the PROTOCOL limit; a
 * board announcing 64 namespaces would need 6 KB and does not exist), so the
 * static allocation stays honest about what this build emits. */
static constexpr size_t kNsAnnounceBoardNames = 8;
static constexpr size_t kNsAnnouncePayloadCap =
    kNsAnnounceBoardNames * (kNsAnnounceMaxNameBytes + 1);

/* Is `name` announceable?  Returns nullptr when it is, else the REASON.
 *
 * Deliberately strict: printable ASCII, no whitespace, no control bytes, first
 * character alphanumeric or '_'.  A namespace is an ADDRESS on a shared
 * segment, and a receiver that accepts "\x07throttle" republishes it to MQTT
 * where it is indistinguishable from a name somebody actually wrote.
 *
 * Checked at the SENDER as well as the receiver, because a board that cannot
 * announce one of its namespaces has a namespace nothing will ever name — and
 * it is the only party that still knows what it meant. */
inline const char* ns_announce_name_problem(const char* name) {
    if (!name || !name[0]) return "empty";
    size_t n = strlen(name);
    if (n > kNsAnnounceMaxNameBytes) return "over the 95-byte ceiling";
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)name[i];
        bool alnum = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
                     (c >= '0' && c <= '9');
        if (i == 0) {
            if (!alnum && c != '_') return "must start with a letter, digit or _";
            continue;
        }
        if (alnum) continue;
        if (c == '_' || c == '.' || c == '/' || c == ':' || c == '@' ||
            c == '+' || c == '-')
            continue;
        return "contains a character a namespace may not contain";
    }
    return nullptr;
}

/* Build the announcement payload into `out`.
 *
 * Returns the byte count, or 0 when NOTHING could be announced.  `refused` (if
 * given) receives the number of names skipped — the caller MUST log it: a
 * silently short announcement is a board that is confidently, partially
 * nameable, which is worse than one that is honestly not.
 *
 * An over-capacity payload is REFUSED WHOLE (returns 0), not filled to the
 * brim: a half payload has a valid CRC and would teach a bridge a partial
 * namespace table it has no way to know is partial. */
inline size_t ns_announce_build_payload(const char* const* names, size_t count,
                                        uint8_t* out, size_t cap,
                                        size_t* refused = nullptr) {
    size_t bad = 0, len = 0, emitted = 0;
    for (size_t i = 0; i < count; i++) {
        if (ns_announce_name_problem(names[i]) != nullptr) { bad++; continue; }
        /* AN ANNOUNCEMENT IS A SET.  A repeated name is skipped, and it is NOT
         * counted as refused — the caller asked for something already granted.
         *
         * This was caught by [env:native_announce] rather than by review: the
         * Python oracle collapsed duplicates and this builder repeated them, so
         * the two tiers produced different bytes and therefore a different
         * CRC-16 — and a CRC mismatch makes the receiver DISCARD the whole
         * announcement, which from the board's side is indistinguishable from
         * success.  Exactly the class of divergence the parity corpus exists
         * for, found the first time it ran. */
        bool dup = false;
        for (size_t j = 0; j < i && !dup; j++)
            dup = (ns_announce_name_problem(names[j]) == nullptr) &&
                  strcmp(names[i], names[j]) == 0;
        if (dup) continue;
        if (emitted >= kNsAnnounceMaxNames) { bad++; continue; }
        size_t n = strlen(names[i]);
        size_t need = len + (emitted ? 1 : 0) + n;
        if (need > cap) {                 /* REFUSE WHOLE, never a prefix */
            if (refused) *refused = count;
            return 0;
        }
        if (emitted) out[len++] = (uint8_t)kNsAnnounceSep;
        memcpy(out + len, names[i], n);
        len += n;
        emitted++;
    }
    if (refused) *refused = bad;
    return emitted ? len : 0;
}

/* ------------------------------------------------------------------ */
/* THE FRAME SEQUENCE — the part that fails INVISIBLY                  */
/* ------------------------------------------------------------------ */
/*
 * ONE function builds EVERY frame of an announcement, header included, and it
 * lives in the header so [env:native_announce] can drive the whole sequence
 * off hardware and compare it byte-for-byte against the Python oracle.
 *
 * WHY THIS IS NOT LEFT INLINE IN ns_announce.cpp.  The cursor arithmetic —
 * `off = (seq - 1) * chunk_payload`, and the short final chunk — is the part of
 * a chunked transfer that fails with NO ERROR ANYWHERE: an off-by-one on `seq`
 * produces frames that are individually well formed, arrive in order, reassemble
 * into a payload of exactly the right LENGTH, and then fail a CRC-16 the sender
 * never sees.  From the board it looks like a successful announcement.  Every
 * other part of this module (pacing, arming, logging) fails loudly; this one
 * does not, so this one is the part that gets host-tested.
 *
 * `seq == 0` is the HEADER, 1..chunk_count are DATA — config_chunk's own
 * numbering, reused rather than translated.  Returns bytes written, or 0.
 */
inline size_t ns_announce_frame(const uint8_t* payload, size_t payload_len,
                                uint16_t crc, uint16_t seq,
                                uint8_t chunk_payload, uint8_t* out,
                                size_t cap) {
    if (seq == kConfigHeaderSeq)
        return config_encode_header((uint16_t)payload_len, chunk_payload, crc,
                                    kConfigTtlSeed, out, cap);
    size_t off = (size_t)(seq - 1) * chunk_payload;
    if (off >= payload_len) return 0;          /* past the end: REFUSE, not pad */
    size_t n = payload_len - off;
    if (n > chunk_payload) n = chunk_payload;
    return config_encode_data(seq, payload + off, n, out, cap);
}

/* ------------------------------------------------------------------ */
/* THE EMITTER (ns_announce.cpp — cadence and publishing)              */
/* ------------------------------------------------------------------ */
/*
 * Declarations only, so this header stays pure C++ and [env:native_announce]
 * can compile it with nothing linked.  The definitions need the transport seam
 * and ESP-IDF logging; the MATH above needs neither, and that split is what
 * lets the format be parity-tested against Python off hardware.
 */

/* Latch the namespace NAMES this board will announce.  Copies nothing — the
 * caller's strings must outlive the board, which they do: announce.cpp holds
 * them in static buffers for exactly this reason. */
void ns_announce_init(const char* const* names, size_t count);

/* Arm a full re-announcement.  Called on boot and on the fleet poll.
 * IDEMPOTENT: arming while one is in flight RESTARTS it rather than queueing a
 * second, because the names cannot have changed and two interleaved
 * announcements on a BROADCAST root are exactly what the receiver cannot
 * demultiplex today (node ids are unallocated, so every node is src=0). */
void ns_announce_arm(void);

/* Drive the emission.  Non-blocking; sends at most a few frames per call and
 * paces itself, because a classic-CAN controller's transmit queue is short
 * (txqueuelen 10 on the bridge's MCP2515 side) and a 28-frame burst offered
 * back to back is refused with ENOBUFS — a refusal that, at the far end, is
 * indistinguishable from a board that never announced. */
void ns_announce_tick(uint32_t now_ms);

/* Diagnostics for the boot banner and for `_device/<id>/health` consumers.
 * `refused` is the count of namespaces this board could NOT announce — it must
 * be visible, because those are the namespaces that will never resolve. */
uint32_t ns_announce_transfers(void);   /* completed announcements */
uint32_t ns_announce_frames(void);      /* frames actually put on the wire */
uint32_t ns_announce_failures(void);    /* frames the transport REFUSED */
uint32_t ns_announce_refused_names(void);

/* Clear all state. For host-test isolation; a no-op cost in firmware. */
void ns_announce_reset(void);

#endif /* NS_ANNOUNCE_H */
