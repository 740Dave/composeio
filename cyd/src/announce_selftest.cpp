/*
 * announce_selftest.cpp — the firmware/Python parity check for DEVICE
 * SELF-ANNOUNCEMENT.  [SPEC:CXC-BOARD-HW] workstream 0.2.
 *
 * Pure host test (no hardware, no ESP-IDF) — the same guarded-file idiom as
 * wire_codes_selftest.cpp / restart_selftest.cpp: the body is behind
 * ANNOUNCE_CORPUS_TEST, defined only by [env:native_announce].
 *
 *   pio run -e native_announce && .pio/build/native_announce/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHAT IT DEFENDS.  The announcement encoding lives in TWO places by
 * necessity — python/composeio/device_announce.py for the fleet tool and
 * cyd/src/announce_pack.h for the firmware, which cannot import Python.  Two
 * copies of a table is exactly the shape that produced the [BP-TYPE-CODES]
 * defect, and the consequence here is specific and nasty: a `health` word
 * whose heap field is shifted 16 on one tier and 15 on the other yields two
 * PLAUSIBLE numbers and no error anywhere.  A fleet report would confidently
 * name the wrong firmware, the wrong substrate, or show a bus-off board as
 * healthy — and every one of those is the failure this workstream exists to
 * remove, reintroduced one layer down.
 *
 * IT CHECKS THE ARITHMETIC, NOT ONLY THE SPELLING.  Comparing two name tables
 * proves the tiers agree about words.  What has to agree is the packing, so
 * the corpus is the PYTHON packer's OUTPUT over boundary cases (zero, the
 * field maximum, one past the maximum, every reset reason, every link state)
 * and the C++ packer must reproduce each word exactly.
 *
 * WHAT IT DOES NOT PROVE, stated because this file's PASS gets believed:
 * nothing about cadence, nothing about whether a board actually publishes,
 * and nothing about whether the bytes survive a substrate.  Those are proven
 * by an INDEPENDENT MQTT subscriber against a live board — a machine signal
 * from the publishing process's own counters would not be evidence.
 */

#ifdef ANNOUNCE_CORPUS_TEST

#include "announce_pack.h"
#include "bus_frame.h"                /* bus_ns_hash — the THIRD source of the
                                       * pinned root hash, so a drifted pin
                                       * cannot agree with itself */
#include "config_chunk.h"             /* the CRC the receiver will check */
#include "ns_announce.h"
#include "device_announce_generated.h"
#include "ns_announce_generated.h"

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
    printf("device-announcement parity: python/composeio/device_announce.py "
           "vs cyd/src/announce_pack.h\n");

    /* ---- transport names, both directions ------------------------- */
    for (int i = 0; i < kPyTransportCodeCount; i++) {
        const PyCodeName& e = kPyTransportCodes[i];
        CHECK(strcmp(ann_transport_name_for_code(e.code), e.name) == 0,
              "transport code %u: C++ says '%s', Python says '%s'",
              (unsigned)e.code, ann_transport_name_for_code(e.code), e.name);
        /* Reverse: only for the REAL substrates.  "unknown" is deliberately
         * not a name a device can be configured with — code 0 means "this
         * build does not know what it is on", and mapping the STRING
         * "unknown" back to 0 would let a typo round-trip as a valid
         * selection. */
        if (e.code != 0)
            CHECK(ann_transport_code_for_name(e.name) == e.code,
                  "transport name '%s': C++ -> %u, Python -> %u", e.name,
                  (unsigned)ann_transport_code_for_name(e.name),
                  (unsigned)e.code);
    }
    /* An unknown NAME must land on 0, never on a working substrate.  A
     * mistyped `mqqt` that resolved to mqtt would make a device report a
     * configuration it does not have. */
    CHECK(ann_transport_code_for_name("mqqt") == kAnnTransportUnknown,
          "an unknown transport name must map to UNKNOWN, not to a substrate");
    CHECK(ann_transport_code_for_name(nullptr) == kAnnTransportUnknown,
          "a null transport name must map to UNKNOWN");

    /* ---- link-health names ---------------------------------------- */
    for (int i = 0; i < kPyLinkHealthCount; i++) {
        const PyCodeName& e = kPyLinkHealth[i];
        CHECK(strcmp(ann_link_health_name(e.code), e.name) == 0,
              "link health %u: C++ says '%s', Python says '%s'",
              (unsigned)e.code, ann_link_health_name(e.code), e.name);
    }
    /* THE ONE THAT MATTERS MOST.  0 must be UNKNOWN on both tiers, because
     * BusTransport::link_health()'s default returns a literal 0 and every
     * substrate with no error model takes it.  If 0 ever meant "ok", every
     * such substrate would start reporting a clean bill of health it has no
     * basis for — which is the H2 defect exactly. */
    CHECK(strcmp(ann_link_health_name(0), "unknown") == 0,
          "link health 0 MUST be 'unknown' — it is the default every "
          "substrate without an error model reports");

    /* ---- bit positions -------------------------------------------- */
    CHECK(kAnnTransportNvsBit == kPyTransportNvsBit,
          "transport NVS bit: C++ 0x%02X, Python 0x%02X",
          (unsigned)kAnnTransportNvsBit, (unsigned)kPyTransportNvsBit);
    CHECK(kAnnTransportCodeMask == kPyTransportCodeMask,
          "transport code mask: C++ 0x%02X, Python 0x%02X",
          (unsigned)kAnnTransportCodeMask, (unsigned)kPyTransportCodeMask);
    CHECK(kAnnHealthHeapShift == kPyHealthHeapShift,
          "health heap shift: C++ %d, Python %d", kAnnHealthHeapShift,
          kPyHealthHeapShift);
    CHECK(kAnnHealthResetShift == kPyHealthResetShift,
          "health reset shift: C++ %d, Python %d", kAnnHealthResetShift,
          kPyHealthResetShift);
    CHECK(kAnnHealthLinkShift == kPyHealthLinkShift,
          "health link shift: C++ %d, Python %d", kAnnHealthLinkShift,
          kPyHealthLinkShift);

    /* ---- flag bits ------------------------------------------------- */
    /* Every Python flag bit must exist in C++ with the same value.  The C++
     * side is an anonymous enum, so this is spelled as an explicit list —
     * which is deliberate: adding a flag on one tier and forgetting the other
     * has to fail HERE rather than produce a report with a missing column. */
    struct { uint8_t bit; const char* name; } cxx_flags[] = {
        { kAnnFlagSceneLoaded, "scene"     },
        { kAnnFlagConfigStale, "stale"     },
        { kAnnFlagConnected,   "connected" },
        { kAnnFlagNetworkUp,   "net"       },
        { kAnnFlagLinkErrors,  "linkerr"   },
        { kAnnFlagListenOnly,  "listen"    },
    };
    const int cxx_flag_count = (int)(sizeof(cxx_flags) / sizeof(cxx_flags[0]));
    CHECK(cxx_flag_count == kPyHealthFlagCount,
          "flag count: C++ %d, Python %d — one tier has a flag the other does "
          "not", cxx_flag_count, kPyHealthFlagCount);
    for (int i = 0; i < kPyHealthFlagCount; i++) {
        const PyCodeName& e = kPyHealthFlags[i];
        bool found = false;
        for (int j = 0; j < cxx_flag_count; j++)
            if (strcmp(cxx_flags[j].name, e.name) == 0) {
                found = true;
                CHECK(cxx_flags[j].bit == e.code,
                      "flag '%s': C++ 0x%02X, Python 0x%02X", e.name,
                      (unsigned)cxx_flags[j].bit, (unsigned)e.code);
            }
        CHECK(found, "flag '%s' exists in Python and not in C++", e.name);
    }

    /* ---- transport PACKING corpus ---------------------------------- */
    for (int i = 0; i < kPyTransportCaseCount; i++) {
        const PyTransportCase& c = kPyTransportCases[i];
        uint8_t got = ann_pack_transport(c.code, c.from_nvs != 0);
        CHECK(got == c.packed,
              "pack_transport(code=%u, nvs=%d): C++ 0x%02X, Python 0x%02X",
              (unsigned)c.code, c.from_nvs, (unsigned)got,
              (unsigned)c.packed);
        CHECK(ann_transport_substrate(c.packed) == c.code,
              "unpack substrate of 0x%02X", (unsigned)c.packed);
        CHECK(ann_transport_from_nvs(c.packed) == (c.from_nvs != 0),
              "unpack nvs bit of 0x%02X", (unsigned)c.packed);
    }

    /* ---- health PACKING corpus ------------------------------------- */
    for (int i = 0; i < kPyHealthCaseCount; i++) {
        const PyHealthCase& c = kPyHealthCases[i];
        uint32_t got = ann_pack_health(c.free_heap_bytes, c.reset_reason,
                                       c.link, c.flags);
        CHECK(got == c.packed,
              "pack_health(%s): C++ 0x%08X, Python 0x%08X", c.label,
              (unsigned)got, (unsigned)c.packed);
        /* Round trip through the C++ accessors, against the PYTHON word — so
         * a matched pair of encode/decode bugs cannot cancel out. */
        CHECK(ann_health_reset_reason(c.packed) ==
                  (c.reset_reason > 15 ? 15 : c.reset_reason),
              "health(%s): reset reason round trip", c.label);
        CHECK(ann_health_link(c.packed) == (c.link > 15 ? 15 : c.link),
              "health(%s): link round trip", c.label);
        CHECK(ann_health_flags(c.packed) == c.flags,
              "health(%s): flags round trip", c.label);
        uint32_t kb = c.free_heap_bytes / 1024u;
        if (kb > 0xFFFFu) kb = 0xFFFFu;
        CHECK(ann_health_heap_kb(c.packed) == (uint16_t)kb,
              "health(%s): heap KB round trip (expected %lu, got %u)", c.label,
              (unsigned long)kb, (unsigned)ann_health_heap_kb(c.packed));
    }

    /* ---- hex rendering --------------------------------------------- */
    /* The form 32- and 16-bit words ACTUALLY take on the wire today.  If the
     * two tiers disagree about zero padding or case, a fleet report's string
     * comparison against a recorded fingerprint silently never matches. */
    for (int i = 0; i < kPyHex32CaseCount; i++) {
        char buf[9];
        ann_hex32(kPyHex32Cases[i].value, buf);
        CHECK(strcmp(buf, kPyHex32Cases[i].hex32) == 0,
              "hex32(0x%08X): C++ '%s', Python '%s'",
              (unsigned)kPyHex32Cases[i].value, buf, kPyHex32Cases[i].hex32);
    }
    for (int i = 0; i < kPyHex16CaseCount; i++) {
        char buf[5];
        ann_hex16((uint16_t)kPyHex16Cases[i].value, buf);
        CHECK(strcmp(buf, kPyHex16Cases[i].hex32) == 0,
              "hex16(0x%04X): C++ '%s', Python '%s'",
              (unsigned)kPyHex16Cases[i].value, buf, kPyHex16Cases[i].hex32);
    }

    /* ================================================================ */
    /* THE WELL-KNOWN ANNOUNCEMENT ROOT  [PLAN G1/G2]                   */
    /* ================================================================ */
    /*
     * Everything above proves the two tiers agree about the CONTENT of a
     * device announcement.  None of it proves the announcement can be NAMED on
     * CAN, and until this section existed it could not be: `_device/<id>/*`
     * embeds a device id, the wire id is a CRC over the FULL name, and a
     * `_device/*` prefix cannot be expanded without already knowing the ids —
     * the thing being discovered.  A board announcing over CAN produced frames
     * the bridge correctly dropped as irreversible hashes.
     *
     * The fix is one hardcoded namespace whose hash both tiers pin.  These
     * checks are what make "pinned" mean something: THREE independent sources
     * for one number — the literal in ns_announce.h, the firmware's own
     * bus_ns_hash(), and the literal baked from Python — all compared.  Two of
     * three agreeing is how a constant drifts.
     */
    printf("\nwell-known announcement root: cyd/src/ns_announce.h vs "
           "python/composeio/ns_announce.py\n");

    CHECK(strcmp(kNsAnnounce, kPyNsAnnounce) == 0,
          "root namespace: C++ '%s', Python '%s'", kNsAnnounce, kPyNsAnnounce);
    CHECK(kNsAnnounceHash == kPyNsAnnounceHash,
          "root hash: C++ 0x%04X, Python 0x%04X — every flashed node would "
          "transmit on an id nothing listens to", kNsAnnounceHash,
          kPyNsAnnounceHash);
    /* THE PIN vs THE HASH FUNCTION.  The literal is what is deployed;
     * bus_ns_hash is what this build computes.  If they diverge, the constant
     * is a lie and nothing else in this file would notice. */
    CHECK(bus_ns_hash(kNsAnnounce) == kNsAnnounceHash,
          "PINNED root hash 0x%04X but bus_ns_hash('%s') == 0x%04X — the pin "
          "no longer matches this build's hash function",
          kNsAnnounceHash, kNsAnnounce, bus_ns_hash(kNsAnnounce));
    CHECK(strcmp(kNsAnnouncePoll, kPyNsAnnouncePoll) == 0,
          "poll namespace: C++ '%s', Python '%s'", kNsAnnouncePoll,
          kPyNsAnnouncePoll);
    CHECK(kNsAnnouncePollHash == kPyNsAnnouncePollHash &&
          bus_ns_hash(kNsAnnouncePoll) == kNsAnnouncePollHash,
          "poll hash: C++ pin 0x%04X, bus_ns_hash 0x%04X, Python 0x%04X",
          kNsAnnouncePollHash, bus_ns_hash(kNsAnnouncePoll),
          kPyNsAnnouncePollHash);
    CHECK(kNsAnnounceMaxNameBytes == kPyNsMaxNameBytes,
          "name ceiling: C++ %u, Python %u — a name one tier announces and the "
          "other refuses is a namespace that never resolves",
          (unsigned)kNsAnnounceMaxNameBytes, (unsigned)kPyNsMaxNameBytes);
    CHECK(kNsAnnounceMaxNames == kPyNsMaxNames,
          "name count ceiling: C++ %u, Python %u",
          (unsigned)kNsAnnounceMaxNames, (unsigned)kPyNsMaxNames);
    CHECK(kNsAnnounceSep == kPyNsSep, "separator: C++ 0x%02X, Python 0x%02X",
          (unsigned)kNsAnnounceSep, (unsigned)kPyNsSep);

    /* ---- the payload, byte for byte -------------------------------- */
    /* A serializer that round-trips through itself would pass with a trailing
     * separator, a NUL terminator, or duplicates left in.  Every one of those
     * changes the CRC-16, and a CRC mismatch makes the receiver DISCARD the
     * whole announcement — which from the board's side looks like success. */
    for (int i = 0; i < kPyNsPayloadCaseCount; i++) {
        const PyNsPayloadCase& c = kPyNsPayloadCases[i];
        uint8_t buf[kNsAnnouncePayloadCap];
        size_t refused = 0;
        size_t n = ns_announce_build_payload(c.names, c.name_count, buf,
                                             sizeof(buf), &refused);
        CHECK(n == c.payload_len,
              "%s: C++ built %u payload byte(s), Python %u", c.label,
              (unsigned)n, (unsigned)c.payload_len);
        CHECK(n == c.payload_len && memcmp(buf, c.payload, n) == 0,
              "%s: payload BYTES differ from the Python oracle", c.label);
        CHECK(refused == 0, "%s: C++ refused %u legal name(s)", c.label,
              (unsigned)refused);
        /* The CRC the receiver will check, and the frame count a classic-CAN
         * segment will actually carry. */
        CHECK(config_content_hash(buf, n) == c.crc,
              "%s: CRC 0x%04X, Python 0x%04X", c.label,
              config_content_hash(buf, n), c.crc);
        CHECK((size_t)config_chunk_count(n, (uint8_t)kConfigCanContentBytes) + 1
                  == c.can_frames,
              "%s: %u classic-CAN frame(s), Python %u", c.label,
              (unsigned)config_chunk_count(n, (uint8_t)kConfigCanContentBytes) + 1,
              (unsigned)c.can_frames);

        /* ---- EVERY FRAME, BYTE FOR BYTE ---------------------------- */
        /* The payload matching is not enough.  What goes on the wire is the
         * FRAME SEQUENCE, and its cursor arithmetic — `(seq-1)*chunk_payload`
         * plus the short final chunk — is the one part of this module that
         * fails with no error anywhere: an off-by-one yields frames that are
         * individually well formed, arrive in order, reassemble to exactly the
         * right LENGTH, and fail a CRC the sender never sees.  Driving the
         * SHIPPED builder (ns_announce_frame, the same call ns_announce.cpp
         * makes for both header and data) against Python's frames closes the
         * loop: the bytes proven learnable by tests/peer are these bytes. */
        {
            uint16_t cp = (uint16_t)kConfigCanContentBytes;
            size_t off = 0;
            for (size_t s = 0; s < c.can_frames; s++) {
                uint8_t frame[16];
                size_t fl = ns_announce_frame(buf, n, c.crc, (uint16_t)s,
                                              (uint8_t)cp, frame, sizeof(frame));
                CHECK(fl == c.frame_lens[s],
                      "%s frame %u: C++ %u byte(s), Python %u", c.label,
                      (unsigned)s, (unsigned)fl, (unsigned)c.frame_lens[s]);
                CHECK(fl == c.frame_lens[s] &&
                      memcmp(frame, c.frames + off, fl) == 0,
                      "%s frame %u: BYTES differ from the Python oracle — a "
                      "receiver would reassemble the right length and the wrong "
                      "CRC, and this board would see a successful announcement",
                      c.label, (unsigned)s);
                CHECK(fl <= 8, "%s frame %u is %u bytes; a classic CAN frame "
                      "carries 8", c.label, (unsigned)s, (unsigned)fl);
                off += c.frame_lens[s];
            }
            /* One past the end must REFUSE, not pad. A padded frame has a
             * valid length and corrupts the tail of the payload. */
            uint8_t extra[16];
            CHECK(ns_announce_frame(buf, n, c.crc, (uint16_t)c.can_frames,
                                    (uint8_t)cp, extra, sizeof(extra)) == 0,
                  "%s: a frame PAST the end of the payload was built",
                  c.label);
        }
    }

    /* ---- the validator, BOTH directions ---------------------------- */
    /* Refusals only would pass a validator that refuses everything; the
     * acceptance list is the negative control that makes the refusals mean
     * something. */
    for (int i = 0; i < kPyNsRefusedCount; i++) {
        CHECK(ns_announce_name_problem(kPyNsRefused[i].name) != nullptr,
              "C++ ACCEPTS %s, which Python refuses (%s) — a namespace nobody "
              "wrote would reach a shared segment",
              kPyNsRefused[i].name, kPyNsRefused[i].why);
    }
    for (int i = 0; i < kPyNsAcceptedCount; i++) {
        const char* why = ns_announce_name_problem(kPyNsAccepted[i]);
        CHECK(why == nullptr,
              "C++ REFUSES '%s' (%s), which Python accepts — this board could "
              "not announce that namespace and it would never resolve",
              kPyNsAccepted[i], why ? why : "?");
    }

    /* A refused name must be SKIPPED AND COUNTED, never silently dropped and
     * never allowed to take the good names down with it. */
    {
        const char* mixed[] = {"good/one", "has a space", "good/two"};
        uint8_t buf[kNsAnnouncePayloadCap];
        size_t refused = 0;
        size_t n = ns_announce_build_payload(mixed, 3, buf, sizeof(buf),
                                             &refused);
        CHECK(refused == 1, "a mixed list refused %u name(s), expected 1",
              (unsigned)refused);
        CHECK(n == strlen("good/one") + 1 + strlen("good/two"),
              "a mixed list built %u byte(s); the two legal names are %u",
              (unsigned)n,
              (unsigned)(strlen("good/one") + 1 + strlen("good/two")));
    }

    /* An OVER-CAPACITY payload is refused WHOLE.  A prefix has a valid CRC and
     * would teach a bridge a partial namespace table it cannot know is
     * partial — the confidently-incomplete census, one layer down. */
    {
        const char* many[] = {"aaaa/bbbb/cccc", "dddd/eeee/ffff"};
        uint8_t small[8];
        size_t refused = 0;
        CHECK(ns_announce_build_payload(many, 2, small, sizeof(small),
                                        &refused) == 0,
              "an over-capacity payload was TRUNCATED instead of refused");
    }

    /* ---- cadence sanity -------------------------------------------- */
    /* Not a parity check — a refusal to ship an obviously wrong cadence.
     * The heartbeat must be strictly faster than the full set, or "presence
     * is the liveness signal" stops being true. */
    CHECK(kAnnHeartbeatIntervalMs > 0 &&
          kAnnHeartbeatIntervalMs < kAnnFullIntervalMs,
          "heartbeat interval (%lu ms) must be shorter than the full-set "
          "interval (%lu ms)", (unsigned long)kAnnHeartbeatIntervalMs,
          (unsigned long)kAnnFullIntervalMs);

    printf("\n%d checks, %d pass, %d fail\n", s_pass + s_fail, s_pass, s_fail);
    if (s_fail == 0) {
        printf("ALL PASS\n");
        return 0;
    }
    printf("FAILED — the two tiers disagree.  Regenerate with\n"
           "  python3 python/scripts/gen_device_announce_header.py\n"
           "and if that does not fix it, one of the two implementations is "
           "wrong and a fleet report is currently lying.\n");
    return 1;
}

#endif /* ANNOUNCE_CORPUS_TEST */
