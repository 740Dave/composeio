/*
 * wire_codes_selftest.cpp — the firmware/Python parity check for THE ONE
 * TABLE.  [SPEC:CIO-BUS-PROTOCOL] [BP-TYPE-CODES]
 *
 * Pure host test (no hardware, no ESP-IDF) — same guarded-file idiom as
 * dsl_selftest.cpp / restart_selftest.cpp: the body is behind
 * WIRE_CODES_CORPUS_TEST, defined only by [env:native_wire_codes].
 *
 *   pio run -e native_wire_codes && .pio/build/native_wire_codes/program
 * Must print "ALL PASS" and exit 0.
 *
 * WHAT IT DEFENDS.  The message-type wire codes live in TWO places by
 * necessity — composeio/transport.py for the host tier and
 * cyd/src/bus_wire_codes.h for the firmware — and two copies of a table is
 * exactly the shape that produced the defect the one-table rule exists to
 * kill: transport_uart had 8 of 10 entries, transport_can had 2, the CAN
 * bridge 5, and every copy ended in `.get(type, 0)`, so an ACK arrived on
 * the wire as a STATE.  This makes drift a FAILING TEST.
 *
 * It checks BOTH DIRECTIONS plus the two properties a one-directional
 * check would miss:
 *   - every Python (name, code) pair resolves the same way in C++;
 *   - every C++ entry exists in Python (no firmware-only code);
 *   - the two tables are the same SIZE (so neither has an extra row);
 *   - every MessageType MEMBER has a code (Python enforces this with its
 *     own test; a C++ enumerator with no table row would otherwise be
 *     un-encodable and only fail on a live wire);
 *   - an UNALLOCATED code (10..15) resolves to NOTHING, never to State.
 */

#ifdef WIRE_CODES_CORPUS_TEST

#include "bus_wire_codes.h"
#include "wire_codes_generated.h"

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
    printf("wire-code parity: Python MESSAGE_TYPE_WIRE_CODES vs "
           "cyd/src/bus_wire_codes.h\n");

    /* ---- size ---------------------------------------------------- */
    CHECK((int)kBusWireCodeCount == kPyWireCodeCount,
          "table size: C++ %d, Python %d — one tier has a row the other "
          "does not (that IS the drift this test exists for)",
          (int)kBusWireCodeCount, kPyWireCodeCount);
    CHECK(kBusWireTypeCodeBits == kPyWireTypeCodeBits,
          "type field width: C++ %d bits, Python %d bits",
          kBusWireTypeCodeBits, kPyWireTypeCodeBits);
    CHECK(kPyMessageTypeCount == kPyWireCodeCount,
          "every MessageType member has an allocated code (Python: %d "
          "members, %d codes)", kPyMessageTypeCount, kPyWireCodeCount);

    /* ---- Python -> C++, by NAME and by CODE ---------------------- */
    for (int i = 0; i < kPyWireCodeCount; i++) {
        const char* name = kPyWireCodes[i].name;
        uint8_t code = kPyWireCodes[i].code;

        BusMessageType t;
        bool named = bus_message_type_from_name(name, &t);
        CHECK(named, "C++ knows the type name '%s'", name);
        if (!named) continue;

        uint8_t got = 0xFF;
        CHECK(bus_wire_code_for_type(t, &got) && got == code,
              "'%s': Python code %u, C++ code %u", name, code, got);

        BusMessageType back;
        CHECK(bus_type_for_wire_code(code, &back) && back == t,
              "code %u decodes back to '%s' in C++", code, name);

        const char* cname = bus_message_type_name(t);
        CHECK(cname && strcmp(cname, name) == 0,
              "code %u names '%s' in C++, '%s' in Python", code,
              cname ? cname : "(null)", name);
    }

    /* ---- C++ -> Python (no firmware-only entry) ------------------ */
    for (size_t i = 0; i < kBusWireCodeCount; i++) {
        bool found = false;
        for (int j = 0; j < kPyWireCodeCount; j++)
            if (strcmp(kBusWireCodes[i].name, kPyWireCodes[j].name) == 0 &&
                kBusWireCodes[i].code == kPyWireCodes[j].code)
                found = true;
        CHECK(found, "C++ entry ('%s', %u) exists in Python",
              kBusWireCodes[i].name, kBusWireCodes[i].code);
    }

    /* ---- every C++ enumerator is IN the table -------------------- */
    /* A member with no row would be silently un-encodable: bus_frame_encode
     * refuses it (correctly), but the refusal would surface only when a
     * real frame failed to go out. */
    const BusMessageType kAll[] = {
        BusMessageType::State, BusMessageType::Event,
        BusMessageType::ConfigPush, BusMessageType::ConfigRequest,
        BusMessageType::Heartbeat, BusMessageType::Discovery,
        BusMessageType::TimeSync, BusMessageType::Binding,
        BusMessageType::Ack, BusMessageType::LogEntry,
    };
    const int kAllCount = (int)(sizeof(kAll) / sizeof(kAll[0]));
    CHECK(kAllCount == kPyMessageTypeCount,
          "C++ enumerates %d types, Python's MessageType has %d",
          kAllCount, kPyMessageTypeCount);
    for (int i = 0; i < kAllCount; i++) {
        uint8_t code;
        CHECK(bus_wire_code_for_type(kAll[i], &code),
              "C++ enumerator #%d has an allocated wire code", i);
    }

    /* ---- NO SILENT FALLBACK -------------------------------------- */
    for (uint8_t code = (uint8_t)kPyWireCodeCount; code < 16; code++) {
        BusMessageType t = BusMessageType::LogEntry;   /* poison */
        bool ok = bus_type_for_wire_code(code, &t);
        CHECK(!ok, "unallocated code %u resolves to NOTHING (a guessed "
              "State is the ACK-arrives-as-STATE defect)", code);
    }
    CHECK(bus_message_type_from_name("mqqt", nullptr) == false,
          "an unknown type NAME resolves to nothing");
    CHECK(bus_message_type_from_name(nullptr, nullptr) == false,
          "a null type name resolves to nothing");

    /* ---- scope table --------------------------------------------- */
    CHECK((int)kBusScopeCodeCount == kPyScopeCodeCount,
          "scope table size: C++ %d, Python %d",
          (int)kBusScopeCodeCount, kPyScopeCodeCount);
    for (int i = 0; i < kPyScopeCodeCount; i++) {
        CHECK(bus_scope_bits_for_name(kPyScopeCodes[i].name) ==
                  kPyScopeCodes[i].bits,
              "scope '%s' -> %u", kPyScopeCodes[i].name,
              kPyScopeCodes[i].bits);
        const char* back = bus_scope_name_for_bits(kPyScopeCodes[i].bits);
        CHECK(back && strcmp(back, kPyScopeCodes[i].name) == 0,
              "scope bits %u -> '%s'", kPyScopeCodes[i].bits,
              back ? back : "(null)");
    }
    /* Python's _SCOPE_TO_BITS.get(scope, 3) — an unknown scope is `auto`,
     * which is the bus's own "I was not told" value.  Unlike a message
     * type there is no wrong claim being made. */
    CHECK(bus_scope_bits_for_name("nonsense") == 3,
          "an unknown scope encodes as auto(3), matching Python's default");

    /* ---- arbitration priority tables [BP-WIRE-PRIORITY] ----------- */
    /* THE TABLES, member for member and IN ORDER.  The evaluated answers
     * are pinned by env:native_can_id's corpus (a Python-computed tier per
     * case); these checks catch the other failure — a table that gained or
     * lost an entry on one tier only, for a namespace no corpus case names. */
    CHECK(kBusPriorityBits == kPyPriorityBits,
          "priority width: C++ %d, Python %d", kBusPriorityBits,
          kPyPriorityBits);
    CHECK(kBusDefaultPriority == kPyDefaultPriority,
          "DEFAULT tier: C++ %u, Python %u — an unlisted namespace must land "
          "in the same class on both tiers", (unsigned)kBusDefaultPriority,
          (unsigned)kPyDefaultPriority);
    CHECK((int)kBusTypePriorityCount == kPyTypePriorityCount,
          "type-priority table size: C++ %d, Python %d",
          (int)kBusTypePriorityCount, kPyTypePriorityCount);
    for (int i = 0; i < kPyTypePriorityCount; i++) {
        BusMessageType t;
        if (!bus_message_type_from_name(kPyTypePriorities[i].name, &t)) {
            s_fail++;
            printf("FAIL: priority table names unknown type '%s'\n",
                   kPyTypePriorities[i].name);
            continue;
        }
        /* bus_priority_for with a NULL namespace: a control-plane type takes
         * its tier from the TYPE alone, so the namespace must not matter. */
        CHECK(bus_priority_for(nullptr, t) == kPyTypePriorities[i].tier,
              "type '%s' tier: C++ %u, Python %u", kPyTypePriorities[i].name,
              (unsigned)bus_priority_for(nullptr, t),
              (unsigned)kPyTypePriorities[i].tier);
        /* And it must OVERRIDE the namespace rule — a config push of
         * `alert_status` is bulk, because the urgency belongs to the
         * transfer and not to the name it happens to carry. */
        CHECK(bus_priority_for("alert_status", t) ==
                  kPyTypePriorities[i].tier,
              "type '%s' must beat the namespace rule for alert_status",
              kPyTypePriorities[i].name);
    }
    CHECK((int)kBusNsPriorityRuleCount == kPyNsPriorityRuleCount,
          "namespace-rule table size: C++ %d, Python %d",
          (int)kBusNsPriorityRuleCount, kPyNsPriorityRuleCount);
    for (int i = 0; i < kPyNsPriorityRuleCount &&
                    i < (int)kBusNsPriorityRuleCount; i++) {
        /* ORDER IS DATA: first match wins, so a table that holds the same
         * rules in a different order is a different table. */
        CHECK(strcmp(kBusNsPriorityRules[i].pattern,
                     kPyNsPriorityRules[i].pattern) == 0,
              "namespace rule %d: C++ '%s', Python '%s' (order is data — "
              "first match wins)", i, kBusNsPriorityRules[i].pattern,
              kPyNsPriorityRules[i].pattern);
        CHECK(kBusNsPriorityRules[i].tier == kPyNsPriorityRules[i].tier,
              "namespace rule '%s' tier: C++ %u, Python %u",
              kPyNsPriorityRules[i].pattern,
              (unsigned)kBusNsPriorityRules[i].tier,
              (unsigned)kPyNsPriorityRules[i].tier);
        /* The MATCHER, on the rule's own pattern: an exact rule must match
         * its literal, a `prefix/` rule must match a member of the family.
         * A port that dropped the trailing-'/' branch passes every table
         * comparison above and fails here. */
        const char* p = kPyNsPriorityRules[i].pattern;
        size_t n = strlen(p);
        if (n && p[n - 1] == '/') {
            char probe[64];
            snprintf(probe, sizeof(probe), "%sprobe", p);
            CHECK(bus_priority_for_namespace(probe) ==
                      kPyNsPriorityRules[i].tier,
                  "prefix rule '%s' must place '%s' in tier %u, got %u", p,
                  probe, (unsigned)kPyNsPriorityRules[i].tier,
                  (unsigned)bus_priority_for_namespace(probe));
        } else {
            CHECK(bus_priority_for_namespace(p) == kPyNsPriorityRules[i].tier,
                  "exact rule '%s' must place itself in tier %u, got %u", p,
                  (unsigned)kPyNsPriorityRules[i].tier,
                  (unsigned)bus_priority_for_namespace(p));
            /* An exact rule must NOT match by prefix — `shields` must not
             * capture `shields_ui`. */
            char longer[64];
            snprintf(longer, sizeof(longer), "%s_not_this", p);
            CHECK(bus_priority_for_namespace(longer) == kBusDefaultPriority,
                  "exact rule '%s' must not capture '%s'", p, longer);
        }
    }

    int total = s_pass + s_fail;
    printf("%d/%d checks pass\n", s_pass, total);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    printf("%d FAILED\n", s_fail);
    return 1;
}

#endif /* WIRE_CODES_CORPUS_TEST */
