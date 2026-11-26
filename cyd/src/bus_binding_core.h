/*
 * bus_binding_core.h — the PURE logic of the ComposeBus repoint binding.
 * [SPEC:CXC-REPOINT] [DEC-BUS-BINDING-IS-STATE-NOT-A-COMMAND]
 *                    [DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]
 *
 * A device's bus binding — MQTT broker + bus_storage + document_store URLs,
 * which move together as ONE fact — is retained config STATE the device
 * reacts to, NOT a command sent to it.  This file owns every DECISION that
 * mechanism makes and NONE of the platform it makes them on: no NVS, no
 * esp_timer, no esp-mqtt, no GPIO.  It is PURE C++ (the restart.cpp / dsl.h
 * idiom) so the whole state machine host-compiles for the native_bus_binding
 * suite and is proven off hardware.
 *
 *   bus_binding.cpp is the ESP-IDF sibling of config_source.cpp: it persists
 *   these records in NVS, reads the escape-hatch GPIO, arms the revert timer,
 *   and hands the ANSWERS this file computes to the app shell.  It NEVER
 *   re-derives a decision that lives here.
 *
 * WHY THE SPLIT MATTERS ([CXC-VISION-EPISTEMICS]).  An ESP_OK from an NVS
 * write and a green compile say NOTHING about whether precedence is right,
 * whether an identical retained value is correctly absorbed, or whether three
 * reverts latch a refusal.  Those are the falsifiable claims, and they are all
 * in this file precisely so a host test — with negative controls — can fail
 * when they are wrong.  The real repoint on silicon is a separate, bench-only
 * gate the selftests deliberately cannot stand in for.
 */

#ifndef BUS_BINDING_CORE_H
#define BUS_BINDING_CORE_H

#include <cstddef>
#include <cstdint>

/* ------------------------------------------------------------------ */
/* Sizes — allocation-free, sized to DP-CIO-18's 256 B payload guide.  */
/* ------------------------------------------------------------------ */
/* A URI is validated to be < 96 bytes ([CXC-REPOINT-PAYLOAD]); the name is a
 * short human label.  These are the NVS string ceilings too. */
static constexpr size_t kBindUriMax  = 96;
static constexpr size_t kBindNameMax = 32;

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-BINDING] A binding is ONE record, not three loose URLs */
/* ------------------------------------------------------------------ */
struct BusBinding {
    char name[kBindNameMax];   /* human label (Haven / maker / another project) */
    char mqtt[kBindUriMax];    /* broker URI      (mqtt://host:port)           */
    char store[kBindUriMax];   /* bus_storage URL (http://host:port)           */
    char docs[kBindUriMax];    /* document_store URL (http://host:port)        */
};

/* Zero every field. */
void binding_clear(BusBinding* b);

/* Byte-for-byte equality of the THREE ADDRESS planes (name is a label and is
 * deliberately excluded — a pure rename is not a binding change,
 * [CXC-REPOINT-PAYLOAD] required rule).  This is the value-compare primitive
 * [CXC-REPOINT-RETAINED-STATE] makes retain safe with. */
bool binding_addresses_equal(const BusBinding* a, const BusBinding* b);

/* The canonical binding string "<mqtt>|<store>|<docs>" and its CRC-16
 * fingerprint ([CXC-REPOINT-ANNOUNCE]).  Two devices on the same bus produce
 * the SAME number; a device that drifted produces a different one, without
 * anyone having to trust a self-reported name. */
size_t   binding_canonical(const BusBinding* b, char* out, size_t cap);
uint16_t binding_fingerprint(const BusBinding* b);

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-PAYLOAD] `bus=<name> mqtt= store= docs= confirm=` parse */
/* ------------------------------------------------------------------ */
/* Partial-update is allowed: an omitted key means UNCHANGED (by ABSENCE of the
 * key, never by an empty value).  The parser therefore reports PRESENCE per
 * key so the caller can merge onto the current binding. */
enum class BindParse : uint8_t {
    Ok,        /* well-formed; merge the present fields                */
    Refused,   /* malformed / missing-required — write NOTHING         */
};

struct BindParseResult {
    BindParse status;
    BusBinding fields;      /* only the PRESENT fields are populated   */
    bool has_name;
    bool has_mqtt;
    bool has_store;
    bool has_docs;
    int32_t confirm_s;      /* clamped [30,600]; -1 when not specified */
    bool confirm_clamped;   /* an out-of-range confirm= was clamped    */
    bool unknown_key;       /* an unrecognised key was seen (REPORTED) */
    char error[64];         /* human reason when Refused               */
};

/* Parse a one-line `name key=value` payload with the SAME scanner shape as
 * bus_transport_on_bus_value (bus_transport_select.cpp), extended to string
 * values.  Validation is fail-closed: a malformed URI is Refused and nothing
 * is written ([CXC-REPOINT-PAYLOAD] validation-before-write). */
BindParseResult binding_parse(const char* payload);

/* Merge a parsed partial onto a base binding: present fields overwrite, absent
 * fields are inherited.  Returns the resulting full binding. */
BusBinding binding_merge(const BusBinding* base, const BindParseResult* update);

/* Clamp a confirm window to [30,600]; sets *clamped when it moved the value. */
int32_t binding_clamp_confirm(int32_t seconds, bool* clamped);

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-PRECEDENCE] Where the address comes from, in order     */
/* ------------------------------------------------------------------ */
enum class BindTier : uint8_t { PerDevice, Global, Compiled };

/* The lifecycle of ONE tier's stored record. */
enum class BindState : uint8_t { Empty, Confirmed, Trial };

/* ANNOUNCE source codes ([CXC-REPOINT-ANNOUNCE] bits 11..8). */
enum class BindSource : uint8_t {
    Unknown       = 0,
    Compiled      = 1,
    NvsPerDevice  = 2,
    NvsGlobal     = 3,
    ButtonCleared = 4,
    NaFramed      = 5,
};

/* A read-only view of one tier for the resolver. */
struct BindTierView {
    BindState  state;    /* Empty / Confirmed / Trial                */
    BusBinding binding;  /* the tier's CURRENT (* ) fields           */
};

struct BindResolution {
    BindTier   tier;      /* which tier supplied the active binding   */
    BindSource source;    /* the ANNOUNCE source code                 */
    BusBinding active;    /* the binding this boot actually runs      */
    bool       is_trial;  /* the active tier is mid-trial             */
};

/* Resolve the active binding.  `button_forced` is the physical override
 * ([CXC-REPOINT-PRECEDENCE] step 1): it bypasses BOTH NVS tiers for this boot
 * and yields the compiled floor with source=ButtonCleared.  A trial binding
 * IS the binding for its boot — that is what makes it testable (steps 3 & 5).
 */
BindResolution binding_resolve(const BindTierView* per_device,
                               const BindTierView* global,
                               const BusBinding* compiled,
                               bool button_forced);

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-COMMIT-CONFIRMED] the arm / confirm / revert machine    */
/* ------------------------------------------------------------------ */
/* One tier's full mutable record — the * and p* fields plus the trial
 * bookkeeping.  `deny` latches the fingerprint of a target that reverted three
 * times ([CXC-REPOINT-BOUNCE]); `reverts` is the SATURATING consecutive-revert
 * count toward `tfp`, the fingerprint of the target it is counting.  `tfp` is
 * one field beyond the spec's original key list — noted in the header of
 * bus_binding.cpp and comfortably inside the 15-char NVS ceiling. */
struct BindRecord {
    BusBinding cur;      /* the * fields — the current binding        */
    BusBinding prev;     /* the p* fields — the last KNOWN-GOOD        */
    BindState  state;    /* confirmed / trial (empty only before use) */
    uint32_t   seq;      /* increments on every armed trial           */
    uint32_t   reverts;  /* consecutive reverts toward tfp (saturating)*/
    uint16_t   tfp;      /* fingerprint reverts is counting           */
    uint16_t   deny;     /* latched-refusal fingerprint (0 = none)    */
    char       why[24];  /* revert reason, survives the reboot        */
};

enum class BindArm : uint8_t {
    Armed,      /* prev<-cur, cur<-new, state<-trial, seq++ — go trial */
    NoOp,       /* identical to the confirmed value — absorbing        */
    RefusedTrial,   /* a trial is already in progress on this tier     */
    RefusedDeny,    /* this target is latched-denied (3 reverts)       */
};

/* Evaluate a NEW full binding against a tier record.  The value-compare /
 * absorbing rule ([CXC-REPOINT-RETAINED-STATE]) and the single-trial and
 * bounce guards all live here.  On Armed the record is mutated in place; on
 * every Refused/NoOp the record is UNTOUCHED.  `ack` (<=96) receives the
 * human status line ([CXC-REPOINT-*] ack contract). */
BindArm binding_arm_trial(BindRecord* r, const BusBinding* newb,
                          char* ack, size_t ack_cap);

/* The trial boot confirmed BOTH planes ([CXC-REPOINT-CONFIRM]): state<-
 * confirmed, the consecutive-revert counter resets, `prev` is RETAINED as the
 * one-step undo.  `deny` is left as-is unless it named this very target, in
 * which case a proven-good target clears its own latch. */
void binding_confirm(BindRecord* r);

/* The window elapsed / a plane failed ([CXC-REPOINT-REVERT]): cur<-prev,
 * state<-confirmed, reverts (toward the failed target) saturating-increments,
 * `why`<-reason, and on the THIRD consecutive revert to the same target `deny`
 * latches its fingerprint ([CXC-REPOINT-BOUNCE]).  `reason` is one of the
 * short codes (no-wifi / no-dns / store-unreachable / store-http-<code> /
 * broker-no-connack / broker-no-echo / timeout). */
void binding_revert(BindRecord* r, const char* reason);

/* reverts, clamped to the 4-bit ANNOUNCE field (saturates at 15). */
uint8_t binding_reverts_saturated(const BindRecord* r);

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-CONFIRM] the two-plane commit gate                      */
/* ------------------------------------------------------------------ */
/* A trial CONFIRMS only when this gate is ready.  The broker-echo plane is
 * ALWAYS required (it proves a LIVE, fanning broker — the whole point of the
 * round-trip).  The store plane is required only under config:http, where a
 * 200+body from the active tier's store is genuinely exercised; under
 * config:bus / config:cached it is `store_required=false` (vacuously
 * satisfied), and the trial confirms on the echo alone.  This is the exact
 * decision bus_binding.cpp::try_confirm runs, so the host test proves the real
 * firmware gate — the "instrument that can report SUCCESS" the confirm plane
 * was missing ([CXC-VISION-EPISTEMICS]). */
struct BindConfirmGate {
    bool store_required;  /* false => store plane vacuously satisfied      */
    bool store_ok;        /* a 200+body from the active tier's store        */
    bool echo_ok;         /* our OWN confirm seq fanned back by the broker  */
};
bool binding_confirm_ready(const BindConfirmGate* g);

/* The revert reason floor ([CXC-REPOINT-REVERT]) derived from which plane was
 * still outstanding when the window closed — used when no more-specific cause
 * was recorded.  Returns a stable literal (store-unreachable / broker-no-echo /
 * timeout); never allocates. */
const char* binding_default_reason(bool store_required, bool store_ok,
                                   bool echo_ok);

/* ------------------------------------------------------------------ */
/* [DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE] the physical rescue          */
/* ------------------------------------------------------------------ */
enum class HatchAction : uint8_t {
    None,           /* a tap — retains its download-mode meaning       */
    ClearPerDevice, /* SHORT hold — accidental-bump-safe personal reset*/
    ClearBoth,      /* LONG hold — the full rescue that beats a bad GLOBAL */
};

/* Classify a measured hold.  Below `short_ms` is a tap (NOT a clear — the BOOT
 * button is also the download-mode strap).  [short_ms, long_ms) clears the
 * per-device tier only; >= long_ms clears both tiers.  Durations are tunable
 * board constants (~3 s / ~10 s), not load-bearing to the two-tier design. */
HatchAction binding_hatch_action(uint32_t hold_ms,
                                 uint32_t short_ms, uint32_t long_ms);

/* ------------------------------------------------------------------ */
/* [CXC-REPOINT-ANNOUNCE] the u32 published as 8 lowercase hex digits   */
/* ------------------------------------------------------------------ */
/* bits 31..16 fingerprint | 15..12 state | 11..8 source | 7..4 reverts |
 * 3..0 flags.  state: 0 unknown 1 confirmed 2 trial 3 fallback 4 refused. */
enum class AnnState : uint8_t {
    Unknown = 0, Confirmed = 1, Trial = 2, Fallback = 3, Refused = 4,
};
/* flags bit 0 = no escape-hatch button on this board; bit 1 = no captive-
 * portal capability on this board; bits 2..3 reserved. */
static constexpr uint8_t kFlagNoEscapeHatch = 0x1;
static constexpr uint8_t kFlagNoCaptivePortal = 0x2;

uint32_t binding_announce_word(uint16_t fingerprint, AnnState state,
                               BindSource source, uint8_t reverts,
                               uint8_t flags);

#endif /* BUS_BINDING_CORE_H */
