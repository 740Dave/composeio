/*
 * bus_wire_codes.h — THE ONE TABLE, firmware side.
 * [SPEC:CIO-BUS-PROTOCOL] [BP-TYPE-CODES] [SPEC:CXC-BOARD-HW]
 * [CXC-BOARD-HW-UART-SLAVE]
 *
 * The 4-bit message-type code carried by every FRAMED substrate (UART,
 * CAN, I2C), and the 2-bit scope code that rides beside it.  This is the
 * C++ mirror of `MESSAGE_TYPE_WIRE_CODES` in python/composeio/transport.py.
 *
 * WHY IT IS ONE TABLE (the defect that paid for this file).  In Python the
 * table used to live in each transport, and the copies drifted:
 * transport_uart had 8 of 10 entries, transport_can had 2,
 * services/can_mqtt_bridge had 5 — and all three ended in
 * `.get(msg.type, 0)`, so an unlisted type was silently re-labelled STATE
 * on the wire.  A `type=ACK` message came back from a UART round-trip as a
 * spurious STATE; on CAN so did HEARTBEAT, DISCOVERY, CONFIG_PUSH and four
 * others.  Python collapsed those forks into ONE table on 2026-08-10.
 *
 * The firmware must not become the FOURTH copy.  Two rules keep it honest:
 *
 *   1. Every framed transport in cyd/ reads THIS header.  A transport that
 *      writes its own `#define CIO_TYPE_*` list is the defect returning.
 *      (firmware/ftl_jump_button/src/main.cpp carries a two-entry SUBSET
 *      and says so in its comment — it is a different repo target on a
 *      2 KB part; if it ever grows a third type it should include this.)
 *   2. Drift from Python is a FAILING TEST, not a mystery on the wire:
 *      cyd/test/wire_codes_generated.h is generated FROM
 *      composeio.transport by python/scripts/gen_wire_codes_header.py, and
 *      env:native_wire_codes compares the two tables member-for-member in
 *      BOTH directions.
 *
 * NO SILENT FALLBACK.  Python's accessors raise (`wire_code_for_type`) or
 * return None (`message_type_for_wire_code`) rather than defaulting to 0.
 * ESP-IDF builds without exceptions, so the C++ mirror returns `false` and
 * writes nothing — and EVERY caller must treat that as "drop the frame and
 * log it", never "assume State".  A guessed STATE manufactures a state
 * change that never happened; that is the whole bug.
 *
 * Pure C++ (no ESP-IDF, no Arduino) so it host-compiles for the native
 * parity harness, same idiom as dsl.h / restart.h / text_wrap.h.
 */

#ifndef BUS_WIRE_CODES_H
#define BUS_WIRE_CODES_H

#include <cstddef>
#include <cstdint>
#include <cstring>

/* Message type.  Mirrors composeio.transport.MessageType member-for-member.
 *
 * THE ENUMERATOR VALUES ARE NOT WIRE CODES.  They happen to coincide today
 * (Python allocated 0..9 in declaration order), and that coincidence is
 * exactly why this warning is here: the moment someone serialises
 * `(uint8_t)type` directly, the table below stops being load-bearing and
 * the next re-ordering silently re-labels every frame.  Always go through
 * bus_wire_code_for_type(). */
enum class BusMessageType : uint8_t {
    State = 0,
    Event,
    ConfigPush,
    ConfigRequest,
    Heartbeat,
    Discovery,
    TimeSync,
    Binding,
    Ack,        /* code 8 — critical acknowledgement [BP-TYPE-ACK] */
    LogEntry,   /* code 9 — append-only log entry    [BP-EXT-LOG]  */
};

/* Width of the type field on every binary transport: 4 bits — codes 0..15,
 * of which 0..9 are allocated and 10..15 are RESERVED ([BP-TYPE-CODES]). */
static constexpr int kBusWireTypeCodeBits = 4;
static constexpr uint8_t kBusWireTypeCodeMask =
    (uint8_t)((1u << kBusWireTypeCodeBits) - 1u);

struct BusWireCodeEntry {
    BusMessageType type;
    uint8_t code;
    const char* name;   /* == the Python MessageType `.value` string */
};

/* THE TABLE.  Order is the allocation order; the code column is what goes
 * on the wire.  `name` is the JSON `type` field used by the unframed
 * (MQTT) transport, so the two encodings stay defined in one place. */
static const BusWireCodeEntry kBusWireCodes[] = {
    { BusMessageType::State,         0, "state"          },
    { BusMessageType::Event,         1, "event"          },
    { BusMessageType::ConfigPush,    2, "config_push"    },
    { BusMessageType::ConfigRequest, 3, "config_request" },
    { BusMessageType::Heartbeat,     4, "heartbeat"      },
    { BusMessageType::Discovery,     5, "discovery"      },
    { BusMessageType::TimeSync,      6, "time_sync"      },
    { BusMessageType::Binding,       7, "binding"        },
    { BusMessageType::Ack,           8, "ack"            },
    { BusMessageType::LogEntry,      9, "log_entry"      },
};
static constexpr size_t kBusWireCodeCount =
    sizeof(kBusWireCodes) / sizeof(kBusWireCodes[0]);

/* type -> 4-bit code.  FALSE (and *out untouched) when the type has no
 * allocated code — the caller must DROP, never encode a substitute.
 * Mirrors Python's `wire_code_for_type` raising UnmappedMessageType. */
inline bool bus_wire_code_for_type(BusMessageType t, uint8_t* out) {
    for (size_t i = 0; i < kBusWireCodeCount; i++) {
        if (kBusWireCodes[i].type == t) {
            if (out) *out = kBusWireCodes[i].code;
            return true;
        }
    }
    return false;
}

/* 4-bit code -> type.  FALSE for an UNALLOCATED code (10..15, or one a
 * newer peer was taught): this build cannot know what the frame is, so the
 * caller must log it and DROP it.  Guessing State invents traffic that
 * never happened.  Mirrors Python's `message_type_for_wire_code` -> None. */
inline bool bus_type_for_wire_code(uint8_t code, BusMessageType* out) {
    code = (uint8_t)(code & kBusWireTypeCodeMask);
    for (size_t i = 0; i < kBusWireCodeCount; i++) {
        if (kBusWireCodes[i].code == code) {
            if (out) *out = kBusWireCodes[i].type;
            return true;
        }
    }
    return false;
}

/* The canonical type NAME (the JSON `type` field).  nullptr when unmapped —
 * again, no substitute. */
inline const char* bus_message_type_name(BusMessageType t) {
    for (size_t i = 0; i < kBusWireCodeCount; i++)
        if (kBusWireCodes[i].type == t) return kBusWireCodes[i].name;
    return nullptr;
}

/* JSON `type` string -> type.  FALSE for an unknown name (the MQTT
 * transport drops such a payload rather than treating it as a state). */
inline bool bus_message_type_from_name(const char* name, BusMessageType* out) {
    if (!name) return false;
    for (size_t i = 0; i < kBusWireCodeCount; i++) {
        if (strcmp(kBusWireCodes[i].name, name) == 0) {
            if (out) *out = kBusWireCodes[i].type;
            return true;
        }
    }
    return false;
}

/* ------------------------------------------------------------------ */
/* Scope — 2 bits beside the type nibble                               */
/* ------------------------------------------------------------------ */
/* Mirrors transport_uart._SCOPE_TO_BITS.  Same no-guessing rule, with one
 * documented exception that is Python's too: an unknown scope ENCODES as
 * `auto` (3), because "auto" is precisely the bus's declared "I was not
 * told" value — unlike a message type, there is no wrong claim to make. */
struct BusScopeEntry {
    uint8_t bits;
    const char* name;
};
static const BusScopeEntry kBusScopeCodes[] = {
    { 0, "local"  },
    { 1, "global" },
    { 2, "remote" },
    { 3, "auto"   },
};
static constexpr size_t kBusScopeCodeCount =
    sizeof(kBusScopeCodes) / sizeof(kBusScopeCodes[0]);
static constexpr uint8_t kBusWireScopeMask = 0x03;

inline uint8_t bus_scope_bits_for_name(const char* name) {
    if (name)
        for (size_t i = 0; i < kBusScopeCodeCount; i++)
            if (strcmp(kBusScopeCodes[i].name, name) == 0)
                return kBusScopeCodes[i].bits;
    return 3;   /* auto — see the note above */
}

inline const char* bus_scope_name_for_bits(uint8_t bits) {
    bits = (uint8_t)(bits & kBusWireScopeMask);
    for (size_t i = 0; i < kBusScopeCodeCount; i++)
        if (kBusScopeCodes[i].bits == bits) return kBusScopeCodes[i].name;
    return "auto";
}

/* ------------------------------------------------------------------ */
/* Arbitration PRIORITY — [BP-WIRE-PRIORITY], ratified 2026-08-13      */
/* ------------------------------------------------------------------ */
/* Three bits, eight tiers, 0 most urgent.  Rides in the top three bits of
 * the CAN arbitration id (bus_can_id.h) where a numerically LOWER id wins
 * the bus.  SENDER-SIDE ONLY: nothing decodes it.
 *
 * This is the C++ mirror of composeio.transport.MESSAGE_TYPE_PRIORITY /
 * NAMESPACE_PRIORITY_RULES / DEFAULT_PRIORITY, and it is a mirror for the
 * same reason bus_wire_codes was: a firmware that computed a different
 * tier from the bridge would produce a bus whose arbitration order
 * depended on which node transmitted — the exact class of "mysterious
 * delays in how different devices react" this table exists to remove.
 * cyd/src/wire_codes_selftest.cpp (env:native_wire_codes) compares both
 * TABLES and a corpus of evaluated (namespace, type) -> tier answers, so
 * the MATCHER is checked and not just the data.
 *
 *   0  alarm                  — a human reacts; late is *wrong*
 *   1  live control           — closed loop, lag is visible
 *   2  transaction control    — terminates something; must not be starved
 *                               by the thing it terminates
 *   3  time sync              — small, and its value IS its timeliness
 *   4  liveness & coordination
 *   5  live telemetry         — late is stale, not wrong    (THE DEFAULT)
 *   6  display state
 *   7  bulk                   — nothing functional may live here
 *
 * THE ASSIGNMENT RULE: control-plane messages take their tier from their
 * TYPE; application data (nearly all of it State/Event) takes it from its
 * NAMESPACE.  A new namespace is placed by asking WHICH CLASS IS THIS,
 * never what is it like — a tier named by example goes stale the moment
 * someone adds a namespace. */
static constexpr int kBusPriorityBits = 3;
static constexpr uint8_t kBusPriorityMask =
    (uint8_t)((1u << kBusPriorityBits) - 1u);

static constexpr uint8_t kBusPriorityAlarm = 0;
static constexpr uint8_t kBusPriorityLiveControl = 1;
static constexpr uint8_t kBusPriorityTransaction = 2;
static constexpr uint8_t kBusPriorityTimeSync = 3;
static constexpr uint8_t kBusPriorityLiveness = 4;
static constexpr uint8_t kBusPriorityTelemetry = 5;
static constexpr uint8_t kBusPriorityDisplay = 6;
static constexpr uint8_t kBusPriorityBulk = 7;

/* The tier an application namespace gets when NO rule names it.  A DEFAULT,
 * NOT A FAILURE, and deliberately not the most urgent one: below every
 * control-plane tier (2, 3, 4), so an unclassified namespace can never
 * starve an ack, a time sync or a heartbeat, and above the bulk floor so it
 * is not silently buried either.  the `dradis/` family is the ratified example of this
 * class and is therefore deliberately ABSENT from the rules below — it lands
 * here by rule.  A rule that restates the default is a list that goes stale. */
static constexpr uint8_t kBusDefaultPriority = kBusPriorityTelemetry;

/* TYPE -> tier, control plane only.  A type listed here takes its tier from
 * this table REGARDLESS of namespace: a ConfigPush of `alert_status` is
 * still bulk, because the urgency belongs to the transfer and not to the
 * name it happens to carry.  State and Event are ABSENT on purpose — they
 * are application data and are placed by namespace. */
struct BusTypePriorityEntry {
    BusMessageType type;
    uint8_t tier;
};
static const BusTypePriorityEntry kBusTypePriorities[] = {
    { BusMessageType::Ack,           kBusPriorityTransaction },
    { BusMessageType::ConfigRequest, kBusPriorityTransaction },
    { BusMessageType::TimeSync,      kBusPriorityTimeSync    },
    { BusMessageType::Heartbeat,     kBusPriorityLiveness    },
    { BusMessageType::Discovery,     kBusPriorityLiveness    },
    { BusMessageType::Binding,       kBusPriorityLiveness    },
    { BusMessageType::ConfigPush,    kBusPriorityBulk        },
    { BusMessageType::LogEntry,      kBusPriorityBulk        },
};
static constexpr size_t kBusTypePriorityCount =
    sizeof(kBusTypePriorities) / sizeof(kBusTypePriorities[0]);

/* NAMESPACE -> tier, as an ORDERED RULE LIST, not a lookup of every
 * namespace.  A pattern ending in '/' matches by PREFIX (the whole family);
 * any other pattern is an exact namespace.  FIRST MATCH WINS, so order is
 * meaningful — and the matcher is prefix/exact rather than glob precisely so
 * that this 20-line MCU implementation and Python's can be byte-for-byte
 * identical.  KEEP TIER 0 NEARLY EMPTY: if everything is urgent, nothing is. */
struct BusNsPriorityRule {
    const char* pattern;
    uint8_t tier;
};
static const BusNsPriorityRule kBusNsPriorityRules[] = {
    { "alert_status", kBusPriorityAlarm       },
    { "shields",      kBusPriorityAlarm       },
    { "alarm/",       kBusPriorityAlarm       },
    { "emergency/",   kBusPriorityAlarm       },
    { "throttle/",    kBusPriorityLiveControl },
    { "helm/",        kBusPriorityLiveControl },
    { "control/",     kBusPriorityLiveControl },
    /* `_device/` IS THE CONTROL PLANE WEARING A NAMESPACE.  kBusTypePriorities
     * already lifts a TYPED Heartbeat to the liveness tier, but this fleet's
     * boards publish liveness as a STATE on `_device/<id>/heartbeat`
     * (announce.cpp) — and State is deliberately absent from the type table
     * because it is application data.  So the one message class that says
     * "this board still exists" was arbitrating in the TELEMETRY pool, behind
     * every gauge reading on the segment: on a loaded CAN bus it is exactly
     * the traffic that loses, which makes a healthy board look dead precisely
     * when the bus is busy.  Same for `_device/<id>/restart` — a command aimed
     * at a wedged board must not queue behind that board's telemetry. */
    { "_device/",     kBusPriorityLiveness    },
    { "_app/",        kBusPriorityDisplay     },
};
static constexpr size_t kBusNsPriorityRuleCount =
    sizeof(kBusNsPriorityRules) / sizeof(kBusNsPriorityRules[0]);

/* Namespace -> tier, by rule, WITH A DEFAULT.  Never fails: a namespace
 * nobody anticipated must get a sane tier, not an error on a wire. */
inline uint8_t bus_priority_for_namespace(const char* ns) {
    if (!ns) return kBusDefaultPriority;
    for (size_t i = 0; i < kBusNsPriorityRuleCount; i++) {
        const char* p = kBusNsPriorityRules[i].pattern;
        size_t n = strlen(p);
        if (n && p[n - 1] == '/') {
            if (strncmp(ns, p, n) == 0) return kBusNsPriorityRules[i].tier;
        } else if (strcmp(ns, p) == 0) {
            return kBusNsPriorityRules[i].tier;
        }
    }
    return kBusDefaultPriority;
}

/* (namespace, type) -> the 3-bit tier.  THE RULE in one line: the control
 * plane is placed by its TYPE, application data by its NAMESPACE. */
inline uint8_t bus_priority_for(const char* ns, BusMessageType t) {
    for (size_t i = 0; i < kBusTypePriorityCount; i++)
        if (kBusTypePriorities[i].type == t)
            return kBusTypePriorities[i].tier;
    return bus_priority_for_namespace(ns);
}

#endif /* BUS_WIRE_CODES_H */
