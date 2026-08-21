/*
 * announce_pack.h — DEVICE SELF-ANNOUNCEMENT, the wire-visible math.
 * [SPEC:CXC-BOARD-HW] [BP-TYPE-CODES] — workstream 0.2 of
 * docs/PLAN-fleet-baseline-and-bus-native.md.
 *
 * A ComposeBus that cannot be polled for what is on it is a bus you have to
 * visit with a screwdriver.  A 25 s census of `composeio/#` on 2026-08-12
 * found ZERO `_device/*` namespaces: nothing announced itself, nothing said
 * what firmware it ran, and nothing distinguished "offline" from "idle".
 * This header is the encoding half of the fix.
 *
 * ANNOUNCEMENT IS PERIODIC, NOT ON-CHANGE, and that is the load-bearing
 * choice.  A MISSING publication is itself the fault report.  A query cannot
 * provide that: silence in response to a query is ambiguous between "the
 * device is absent" and "the device did not hear me".  Same argument that
 * made the CAN bridge's 5 s table publication periodic.
 *
 * THE WIRE CARRIES FINGERPRINTS; THE TOOLING RESOLVES THEM.  A classic CAN
 * frame is EIGHT data bytes, so "Aug 13 2026 21:41:02" cannot go on the wire.
 * Each field is its own namespace and each is sized to fit one frame.  The
 * build system emits a fingerprint -> build-string map (cyd/tools/
 * gen_build_stamp.py -> cyd/build_fingerprints.json) so `composeio fleet` can
 * print the human string without the wire ever carrying it.
 *
 * WHY THIS FILE IS SEPARATE FROM announce.cpp.  Everything here is PURE C++
 * (no ESP-IDF) and is the SECOND implementation of a table whose first
 * implementation is python/composeio/device_announce.py.  Two copies of a
 * table is exactly the shape that produced the [BP-TYPE-CODES] defect (three
 * forks of the message-type map, each ending in `.get(t, 0)`), so drift is a
 * FAILING TEST rather than a mystery in a fleet report:
 * cyd/test/device_announce_generated.h is generated FROM the Python module by
 * python/scripts/gen_device_announce_header.py, and [env:native_announce]
 * compares the two field-for-field in both directions.
 *
 * ---------------------------------------------------------------------------
 * THE NAMESPACES
 * ---------------------------------------------------------------------------
 *   _device/<id>/heartbeat   uptime seconds     PRESENCE is the liveness signal
 *   _device/<id>/build       32-bit fingerprint CRC-32 of "<env> <date> <time>"
 *   _device/<id>/transport   8-bit  substrate + whether default or NVS
 *   _device/<id>/scene       16-bit content hash of the running scene, 0=none
 *   _device/<id>/health      32-bit heap KB | reset reason | link | flags
 *
 * Poll: `_device/broadcast/announce` (any truthy value) makes EVERY device
 * republish its full set immediately; `_device/<id>/announce` polls one.
 *
 * ---------------------------------------------------------------------------
 * ⚠ HOW A 32-BIT WORD RIDES THE WIRE TODAY, AND WHY IT IS NOT A NUMBER
 * ---------------------------------------------------------------------------
 * The plan calls `build` and `health` u32.  On the FRAMED substrates there is
 * no u32 encoding: bus_frame.h offers 1-byte, int32, float32 and a
 * length-prefixed string, and int32 and float32 are INDISTINGUISHABLE on the
 * wire — DEFECT-VALUE-DECODED-FROM-BINARY-WIRE-EACAE9, deliberately unfixed
 * because the fix is a wire bit that lands in workstream F.  Both publish
 * paths therefore destroy a 32-bit word today:
 *
 *   - UART/CAN `publish_number` encodes float32.  A 24-bit mantissa cannot
 *     hold a CRC-32; 0xDEADBEEF does not round-trip.
 *   - MQTT `mqtt_publish_number` formatted with "%g" (6 significant digits)
 *     turned 4294967295 into "4.29497e+09".  (That is now "%.10g" — see
 *     bus_mqtt.cpp — which fixes uptime past 11.5 days but still cannot carry
 *     32 significant bits.)
 *
 * So a 32-bit WORD is published as its 8 lowercase HEX DIGITS and a 16-bit
 * word as 4 — exact on MQTT and on UART, readable in a console log, and
 * comparable by a machine.  It is ONE encoding on every substrate (the
 * DP-CIO-6 property), it is simply 9 bytes rather than 4, so a classic CAN
 * frame cannot carry it and `CanTransport::publish_state` REFUSES it loudly
 * rather than truncating.  That refusal is correct: a truncated fingerprint
 * decodes cleanly as a DIFFERENT build, which is the worst possible failure
 * for the one field the reflash campaign is checked against.
 *
 * WHEN WORKSTREAM F LANDS (a value type tag on the wire), flip
 * announce_publish_u32/u16 in announce.cpp to a real 4-byte integer.  That is
 * ONE function, and this comment is the reason it is one function.
 */

#ifndef ANNOUNCE_PACK_H
#define ANNOUNCE_PACK_H

#include <cstddef>
#include <cstdint>
#include <cstring>

/* ------------------------------------------------------------------ */
/* transport (u8) — WHICH substrate, and WHERE the choice came from    */
/* ------------------------------------------------------------------ */
/*
 * bit 7      1 = the selection came from NVS, 0 = the compiled default
 * bits 6..4  reserved (0)
 * bits 3..0  substrate code
 *
 * The SOURCE bit is not decoration.  "This board is on UART" and "somebody
 * told this board to be on UART and it remembered" are different facts, and
 * the second one is what a reflash campaign has to reconcile: a device whose
 * NVS says `can` after being flashed with a `mqtt` default is not running the
 * build you think it is running.  One bit, and the fleet report can say so.
 */
static constexpr uint8_t kAnnTransportNvsBit = 0x80;
static constexpr uint8_t kAnnTransportCodeMask = 0x0F;

enum : uint8_t {
    /* 0 is UNKNOWN, never "mqtt".  A MisconfiguredTransport (an NVS key this
     * build does not know) carries nothing on any substrate, and reporting it
     * as the default would be a device lying about its own configuration —
     * the exact defect bus_transport_select.cpp's rule 1 exists to prevent. */
    kAnnTransportUnknown = 0,
    kAnnTransportMqtt    = 1,
    kAnnTransportUart    = 2,
    kAnnTransportCan     = 3,
};

/* name -> code.  0 (unknown) for anything this build does not know, which is
 * the honest answer for "MISCONFIGURED". */
inline uint8_t ann_transport_code_for_name(const char* name) {
    if (!name) return kAnnTransportUnknown;
    if (strcmp(name, "mqtt") == 0) return kAnnTransportMqtt;
    if (strcmp(name, "uart") == 0) return kAnnTransportUart;
    if (strcmp(name, "can") == 0)  return kAnnTransportCan;
    return kAnnTransportUnknown;
}

inline const char* ann_transport_name_for_code(uint8_t code) {
    switch (code & kAnnTransportCodeMask) {
        case kAnnTransportMqtt: return "mqtt";
        case kAnnTransportUart: return "uart";
        case kAnnTransportCan:  return "can";
        default:                return "unknown";
    }
}

inline uint8_t ann_pack_transport(uint8_t substrate_code, bool from_nvs) {
    uint8_t v = (uint8_t)(substrate_code & kAnnTransportCodeMask);
    if (from_nvs) v = (uint8_t)(v | kAnnTransportNvsBit);
    return v;
}

inline uint8_t ann_transport_substrate(uint8_t packed) {
    return (uint8_t)(packed & kAnnTransportCodeMask);
}
inline bool ann_transport_from_nvs(uint8_t packed) {
    return (packed & kAnnTransportNvsBit) != 0;
}

/* ------------------------------------------------------------------ */
/* link health — the ONE generic fact every substrate can be asked for */
/* ------------------------------------------------------------------ */
/*
 * [SPEC:CXC-BOARD-HW] H2a.  The TWAI instrument on the C3 bring-up node
 * (can_bringup_main.cpp) publishes six controller-specific counters.  Those
 * stay where they are — they are TWAI's, and a UART has no TEC.  What is
 * GENERIC is the five-state summary below, and folding only that into
 * `_device/<id>/health` is what keeps the health word substrate-independent.
 *
 * UNKNOWN IS 0 AND IS NOT "HEALTHY".  This is the whole lesson of H2: a
 * bus-off ESP32 reported CONNECTED forever because `connected()` returned
 * `s_started`.  "What would this instrument show if the thing were broken?"
 * — the same.  It was not an instrument.  So a substrate with no error model
 * answers Unknown, the fleet report prints "—", and nobody reads a dash as a
 * clean bill of health.
 */
enum : uint8_t {
    kAnnLinkUnknown    = 0,   /* this substrate has NO error model           */
    kAnnLinkOk         = 1,
    kAnnLinkWarning    = 2,   /* error counters climbing (CAN: >= 96)        */
    kAnnLinkDegraded   = 3,   /* error-passive / retrying                    */
    kAnnLinkDown       = 4,   /* bus-off / broker unreachable / port closed  */
    kAnnLinkStopped    = 5,   /* never started                               */
    kAnnLinkRecovering = 6,
};

inline const char* ann_link_health_name(uint8_t v) {
    switch (v) {
        case kAnnLinkOk:         return "ok";
        case kAnnLinkWarning:    return "warning";
        case kAnnLinkDegraded:   return "degraded";
        case kAnnLinkDown:       return "down";
        case kAnnLinkStopped:    return "stopped";
        case kAnnLinkRecovering: return "recovering";
        default:                 return "unknown";
    }
}

/* ------------------------------------------------------------------ */
/* health (u32)                                                        */
/* ------------------------------------------------------------------ */
/*
 * bits 31..16  free heap KB, SATURATING at 0xFFFF (64 MB — no ESP32 in this
 *              fleet has that, so saturation is unreachable rather than
 *              merely unlikely; it exists so the field can never wrap and
 *              report a starving board as a roomy one)
 * bits 15..12  reset reason — esp_reset_reason_t verbatim.  IDF allocates
 *              0..15 (UNKNOWN..CPU_LOCKUP), which is exactly four bits.
 *              A value above 15 saturates rather than aliasing onto a
 *              different reason.
 * bits 11..8   link health (above)
 * bits  7..0   flags
 *
 * WHY RESET REASON IS IN THE PERIODIC ANNOUNCEMENT.  A board that panics and
 * reboots every 40 s presents, from the bus, as a board with a small uptime —
 * which is indistinguishable from a board somebody just power-cycled.  The
 * reason byte separates them without anybody attaching a serial cable.
 */
static constexpr int kAnnHealthHeapShift = 16;
static constexpr int kAnnHealthResetShift = 12;
static constexpr int kAnnHealthLinkShift = 8;

enum : uint8_t {
    kAnnFlagSceneLoaded  = 0x01,  /* a scene is built and rendering          */
    kAnnFlagConfigStale  = 0x02,  /* rendering the CACHED scene (unknown age)*/
    kAnnFlagConnected    = 0x04,  /* what BusTransport::connected() claims   */
    kAnnFlagNetworkUp    = 0x08,  /* the radio came up (so /capture.bmp lives)*/
    kAnnFlagLinkErrors   = 0x10,  /* substrate reports non-zero error counters*/
    kAnnFlagListenOnly   = 0x20,  /* receive-only substrate (a WORKAROUND)   */
    /* 0x40, 0x80 reserved — do NOT reuse without regenerating the Python
     * table; a flag that means two things is worse than a missing flag. */
};

inline uint32_t ann_pack_health(uint32_t free_heap_bytes, uint8_t reset_reason,
                                uint8_t link_health, uint8_t flags) {
    uint32_t kb = free_heap_bytes / 1024u;
    if (kb > 0xFFFFu) kb = 0xFFFFu;
    uint32_t rr = reset_reason > 15u ? 15u : reset_reason;
    uint32_t lh = link_health > 15u ? 15u : link_health;
    return (kb << kAnnHealthHeapShift) | (rr << kAnnHealthResetShift) |
           (lh << kAnnHealthLinkShift) | (uint32_t)flags;
}

inline uint16_t ann_health_heap_kb(uint32_t h) {
    return (uint16_t)((h >> kAnnHealthHeapShift) & 0xFFFFu);
}
inline uint8_t ann_health_reset_reason(uint32_t h) {
    return (uint8_t)((h >> kAnnHealthResetShift) & 0x0Fu);
}
inline uint8_t ann_health_link(uint32_t h) {
    return (uint8_t)((h >> kAnnHealthLinkShift) & 0x0Fu);
}
inline uint8_t ann_health_flags(uint32_t h) { return (uint8_t)(h & 0xFFu); }

/* ------------------------------------------------------------------ */
/* hex rendering — see the ⚠ block at the top of this file             */
/* ------------------------------------------------------------------ */
/*
 * LOWERCASE, ZERO-PADDED, no `0x`.  Lowercase because that is what
 * `"%08x"` gives on both tiers and because a fleet report that mixes cases
 * makes a string comparison a trap; zero-padded because the width is how a
 * reader tells a 16-bit scene hash from a 32-bit build fingerprint at a
 * glance.  `out` must have room for digits + 1.
 */
inline void ann_hex32(uint32_t v, char* out) {
    static const char* d = "0123456789abcdef";
    for (int i = 7; i >= 0; i--) { out[i] = d[v & 0xF]; v >>= 4; }
    out[8] = '\0';
}

inline void ann_hex16(uint16_t v, char* out) {
    static const char* d = "0123456789abcdef";
    for (int i = 3; i >= 0; i--) { out[i] = d[v & 0xF]; v >>= 4; }
    out[4] = '\0';
}

/* ------------------------------------------------------------------ */
/* cadence                                                             */
/* ------------------------------------------------------------------ */
/*
 * heartbeat every 5 s; the full set every 60 s AND on change.  For ten
 * devices that is trivial on MQTT and ~0.7 frames/s on a 500 kbit segment.
 *
 * The full-set interval is deliberately 12x the heartbeat rather than, say,
 * 6x: `build`, `transport` and `scene` change only at a reflash, a bus
 * command or a scene swap, and all three are published ON CHANGE anyway, so
 * the periodic republication is a REFRESH for late subscribers (nothing here
 * is retained on MQTT — see bus_mqtt.cpp, retain flag 0), not the mechanism.
 */
static constexpr uint32_t kAnnHeartbeatIntervalMs = 5000;
static constexpr uint32_t kAnnFullIntervalMs = 60000;

#endif /* ANNOUNCE_PACK_H */
