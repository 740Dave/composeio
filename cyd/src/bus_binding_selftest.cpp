/*
 * bus_binding_selftest.cpp — host unit test for the repoint binding CORE.
 * [SPEC:CXC-REPOINT] acceptance §9, the machine-testable half.
 *
 * Pure host test (no hardware, no ESP-IDF) — the restart_selftest.cpp idiom:
 * the whole body is behind BUS_BINDING_CORPUS_TEST, defined only by
 * [env:native_bus_binding].  In the flash envs this is an empty TU.
 *
 *   pio run -e native_bus_binding && .pio/build/native_bus_binding/program
 * Must print "ALL PASS" and exit 0.
 *
 * ⛔ WHAT THIS DOES NOT PROVE.  A green run here says the DECISIONS are right —
 * precedence, value-compare/absorbing, arm/confirm/revert, bounce, two-stage
 * hatch tier-clearing.  It says NOTHING about a real repoint on silicon: NVS
 * persistence across a reboot, the store+broker round-trip confirm, the revert
 * timer firing, or a held GPIO.  Those are the bench gate ([CXC-REPOINT-AC-*]),
 * and this file deliberately cannot stand in for them.  Every check below has
 * a negative control where the spec names one, because an instrument that can
 * only report success is not an instrument ([CXC-VISION-EPISTEMICS]).
 */

#ifdef BUS_BINDING_CORPUS_TEST

#include "bus_binding_core.h"

#include <cstdio>
#include <cstring>

static int s_pass = 0, s_fail = 0;
#define CHECK(cond, msg)                                                 \
    do {                                                                 \
        if (cond) { s_pass++; }                                          \
        else { s_fail++; printf("  FAIL: %s\n", msg); }                  \
    } while (0)

static BusBinding mk(const char* name, const char* mqtt, const char* store,
                     const char* docs) {
    BusBinding b;
    binding_clear(&b);
    if (name)  snprintf(b.name,  sizeof(b.name),  "%s", name);
    if (mqtt)  snprintf(b.mqtt,  sizeof(b.mqtt),  "%s", mqtt);
    if (store) snprintf(b.store, sizeof(b.store), "%s", store);
    if (docs)  snprintf(b.docs,  sizeof(b.docs),  "%s", docs);
    return b;
}

/* Canonical maker / skipper / nexus test bindings. */
static BusBinding maker() {
    return mk("maker", "mqtt://192.0.2.90:1883",
              "http://192.0.2.90:8420", "http://192.0.2.90:8421");
}
static BusBinding skipper() {
    return mk("skipper", "mqtt://192.0.2.10:1883",
              "http://192.0.2.10:8420", "http://192.0.2.10:8421");
}
static BusBinding compiled_floor() {
    return mk("rescue", "mqtt://mqtt-broker.local:1883",
              "http://mqtt-broker.local:8420", "http://mqtt-broker.local:8421");
}

/* ================================================================== */
static void test_parse_serialize(void) {
    printf("-- parse / serialize ([CXC-REPOINT-PAYLOAD]) --\n");

    /* full valid payload */
    BindParseResult r = binding_parse(
        "bus=maker mqtt=mqtt://192.0.2.90:1883 "
        "store=http://192.0.2.90:8420 docs=http://192.0.2.90:8421 "
        "confirm=120");
    CHECK(r.status == BindParse::Ok, "full payload parses Ok");
    CHECK(r.has_name && r.has_mqtt && r.has_store && r.has_docs,
          "all four fields present");
    CHECK(strcmp(r.fields.mqtt, "mqtt://192.0.2.90:1883") == 0,
          "mqtt extracted");
    CHECK(r.confirm_s == 120, "confirm=120 accepted");
    CHECK(!r.confirm_clamped, "120 not clamped");

    /* partial update: store only, omission = unchanged */
    BindParseResult p = binding_parse("bus=maker store=http://192.0.2.9:8420");
    CHECK(p.status == BindParse::Ok, "partial (store-only) parses Ok");
    CHECK(p.has_store && !p.has_mqtt && !p.has_docs,
          "only store present; mqtt/docs absent (=unchanged)");

    /* NEGATIVE: bus= alone is a rename, refused */
    BindParseResult rename = binding_parse("bus=maker");
    CHECK(rename.status == BindParse::Refused, "bus= alone REFUSED (rename)");

    /* NEGATIVE: missing bus= */
    BindParseResult noname = binding_parse("mqtt://x");
    CHECK(noname.status == BindParse::Refused, "missing bus= REFUSED");

    /* NEGATIVE: malformed mqtt scheme */
    BindParseResult bad = binding_parse("bus=x mqtt=http://192.0.2.90:1883");
    CHECK(bad.status == BindParse::Refused,
          "mqtt= with http:// scheme REFUSED");

    /* NEGATIVE: store with no host */
    BindParseResult nohost = binding_parse("bus=x store=http://");
    CHECK(nohost.status == BindParse::Refused, "store=http:// (no host) REFUSED");

    /* NEGATIVE: oversize URI (>=96 B) */
    char big[160];
    snprintf(big, sizeof(big), "bus=x store=http://%0*d", 100, 0);
    BindParseResult over = binding_parse(big);
    CHECK(over.status == BindParse::Refused, ">=96 B URI REFUSED");

    /* unknown key is REPORTED, not fatal on its own */
    BindParseResult unk = binding_parse(
        "bus=maker mqtt=mqtt://h:1883 frobnicate=1");
    CHECK(unk.status == BindParse::Ok && unk.unknown_key,
          "unknown key flagged (reported) but payload still Ok");

    /* confirm clamp both directions, reported */
    bool cl = false;
    CHECK(binding_clamp_confirm(5, &cl) == 30 && cl, "confirm=5 -> 30 clamped");
    CHECK(binding_clamp_confirm(9000, &cl) == 600 && cl,
          "confirm=9000 -> 600 clamped");
    CHECK(binding_clamp_confirm(120, &cl) == 120 && !cl, "confirm=120 as-is");

    /* canonical + fingerprint: same bus -> same number; drift -> different */
    BusBinding m1 = maker(), m2 = maker(), sk = skipper();
    CHECK(binding_fingerprint(&m1) == binding_fingerprint(&m2),
          "identical binding -> identical fingerprint");
    CHECK(binding_fingerprint(&m1) != binding_fingerprint(&sk),
          "different binding -> different fingerprint");
    char canon[300];
    binding_canonical(&m1, canon, sizeof(canon));
    CHECK(strcmp(canon,
                 "mqtt://192.0.2.90:1883|http://192.0.2.90:8420|"
                 "http://192.0.2.90:8421") == 0,
          "canonical is <mqtt>|<store>|<docs>");

    /* merge: present overwrites, absent inherits */
    BusBinding merged = binding_merge(&m1, &p); /* p = store-only http://192.0.2.9 */
    CHECK(strcmp(merged.mqtt, m1.mqtt) == 0, "merge keeps unchanged mqtt");
    CHECK(strcmp(merged.store, "http://192.0.2.9:8420") == 0,
          "merge overwrites store");
    CHECK(strcmp(merged.docs, m1.docs) == 0, "merge keeps unchanged docs");
}

/* ================================================================== */
static void test_precedence(void) {
    printf("-- precedence ([CXC-REPOINT-PRECEDENCE]) --\n");
    BusBinding floor = compiled_floor();

    BindTierView pd, gl;
    BusBinding mk_b = maker(), sk_b = skipper();

    /* per-device confirmed wins over global confirmed */
    pd = { BindState::Confirmed, mk_b };
    gl = { BindState::Confirmed, sk_b };
    BindResolution r = binding_resolve(&pd, &gl, &floor, false);
    CHECK(r.tier == BindTier::PerDevice && r.source == BindSource::NvsPerDevice,
          "per-device confirmed > global confirmed");
    CHECK(binding_addresses_equal(&r.active, &mk_b), "active = per-device value");
    CHECK(!r.is_trial, "confirmed is not a trial");

    /* per-device TRIAL is still the active binding for its boot */
    pd = { BindState::Trial, sk_b };
    r = binding_resolve(&pd, &gl, &floor, false);
    CHECK(r.tier == BindTier::PerDevice && r.is_trial,
          "per-device trial IS the active binding, flagged trial");

    /* empty per-device falls through to global */
    pd = { BindState::Empty, {} };
    gl = { BindState::Confirmed, sk_b };
    r = binding_resolve(&pd, &gl, &floor, false);
    CHECK(r.tier == BindTier::Global && r.source == BindSource::NvsGlobal,
          "empty per-device -> global");

    /* both empty -> compiled floor */
    gl = { BindState::Empty, {} };
    r = binding_resolve(&pd, &gl, &floor, false);
    CHECK(r.tier == BindTier::Compiled && r.source == BindSource::Compiled,
          "both tiers empty -> compiled floor");
    CHECK(binding_addresses_equal(&r.active, &floor), "active = compiled floor");

    /* NEGATIVE / physical override: button bypasses BOTH tiers this boot */
    pd = { BindState::Confirmed, mk_b };
    gl = { BindState::Confirmed, sk_b };
    r = binding_resolve(&pd, &gl, &floor, /*button_forced=*/true);
    CHECK(r.tier == BindTier::Compiled &&
          r.source == BindSource::ButtonCleared,
          "button forces compiled floor over BOTH populated tiers");
    CHECK(binding_addresses_equal(&r.active, &floor),
          "button-forced active = compiled floor");
}

/* ================================================================== */
static void test_absorbing(void) {
    printf("-- value-compare / absorbing ([CXC-REPOINT-RETAINED-STATE]) --\n");
    BindRecord r;
    memset(&r, 0, sizeof(r));
    r.cur = maker();
    r.state = BindState::Confirmed;

    char ack[96];
    BusBinding same = maker();
    BindArm a = binding_arm_trial(&r, &same, ack, sizeof(ack));
    CHECK(a == BindArm::NoOp, "identical confirmed value -> NoOp (absorbing)");
    CHECK(r.state == BindState::Confirmed && r.seq == 0,
          "no-op leaves state confirmed, seq unchanged");

    /* a name-only difference is still absorbing — addresses are what matter */
    BusBinding renamed = maker();
    snprintf(renamed.name, sizeof(renamed.name), "maker-renamed");
    a = binding_arm_trial(&r, &renamed, ack, sizeof(ack));
    CHECK(a == BindArm::NoOp,
          "same addresses, different name -> NoOp (not a binding change)");

    /* POSITIVE control: a genuinely different value arms exactly one trial */
    BusBinding sk = skipper();
    a = binding_arm_trial(&r, &sk, ack, sizeof(ack));
    CHECK(a == BindArm::Armed, "differing value ARMS a trial");
    CHECK(r.state == BindState::Trial && r.seq == 1, "armed: state trial, seq++");
    CHECK(binding_addresses_equal(&r.prev, &same), "prev = the old known-good");
    CHECK(binding_addresses_equal(&r.cur, &sk), "cur = the new target");
}

/* ================================================================== */
static void test_commit_confirmed(void) {
    printf("-- arm / confirm / revert ([CXC-REPOINT-COMMIT-CONFIRMED]) --\n");
    BindRecord r;
    memset(&r, 0, sizeof(r));
    r.cur = maker();
    r.state = BindState::Confirmed;
    char ack[96];

    /* arm a trial to skipper */
    BusBinding sk = skipper();
    binding_arm_trial(&r, &sk, ack, sizeof(ack));
    CHECK(r.state == BindState::Trial, "armed trial");

    /* SINGLE-TRIAL: a second change while trialling is REFUSED, prev intact */
    BusBinding other = mk("other", "mqtt://192.0.2.9:1883",
                          "http://192.0.2.9:8420", "http://192.0.2.9:8421");
    BusBinding prev_before = r.prev;
    BindArm a = binding_arm_trial(&r, &other, ack, sizeof(ack));
    CHECK(a == BindArm::RefusedTrial, "2nd change during trial REFUSED");
    CHECK(binding_addresses_equal(&r.prev, &prev_before),
          "refused trial did NOT overwrite prev (the way home)");

    /* confirm: state confirmed, prev retained as one-step undo */
    binding_confirm(&r);
    BusBinding mk_ref = maker();
    CHECK(r.state == BindState::Confirmed, "confirm -> confirmed");
    CHECK(binding_addresses_equal(&r.cur, &sk), "confirmed cur = the target");
    CHECK(binding_addresses_equal(&r.prev, &mk_ref),
          "prev RETAINED after confirm (one-step undo)");

    /* now arm again and REVERT: cur goes home, reason recorded */
    BusBinding maker_again = maker();
    binding_arm_trial(&r, &maker_again, ack, sizeof(ack)); /* trial back to maker */
    CHECK(r.state == BindState::Trial, "re-armed");
    binding_revert(&r, "broker-no-connack");
    CHECK(r.state == BindState::Confirmed, "revert -> confirmed");
    CHECK(binding_addresses_equal(&r.cur, &sk),
          "reverted cur = the last known-good (skipper)");
    CHECK(strcmp(r.why, "broker-no-connack") == 0,
          "revert reason recorded (survives reboot via NVS why)");
    CHECK(r.reverts == 1, "one revert counted");
}

/* ================================================================== */
static void test_bounce(void) {
    printf("-- three reverts latch a refusal ([CXC-REPOINT-BOUNCE]) --\n");
    BindRecord r;
    memset(&r, 0, sizeof(r));
    r.cur = maker();
    r.state = BindState::Confirmed;
    char ack[96];

    BusBinding sk = skipper();
    uint16_t skfp = binding_fingerprint(&sk);

    for (int i = 0; i < 3; i++) {
        BindArm a = binding_arm_trial(&r, &sk, ack, sizeof(ack));
        CHECK(a == BindArm::Armed, "trial toward the flaky target arms");
        binding_revert(&r, "broker-no-connack");
    }
    CHECK(r.reverts == 3, "three consecutive reverts counted");
    CHECK(r.deny == skfp, "the flaky target's fingerprint is LATCHED in deny");

    /* now the SAME target is refused without arming */
    BindArm a = binding_arm_trial(&r, &sk, ack, sizeof(ack));
    CHECK(a == BindArm::RefusedDeny, "4th attempt to same target REFUSED");
    CHECK(r.state == BindState::Confirmed, "refusal did not arm a trial");

    /* NEGATIVE control: a DIFFERENT good target is still accepted while
     * the latch stands (a latch that blocks everything is the stranding it
     * was built to prevent). */
    BusBinding good = mk("nexus", "mqtt://192.0.2.1:1883",
                         "http://192.0.2.1:8420", "http://192.0.2.1:8421");
    a = binding_arm_trial(&r, &good, ack, sizeof(ack));
    CHECK(a == BindArm::Armed, "a DIFFERENT target is accepted despite the latch");

    /* SPEC [CXC-REPOINT-BOUNCE] cleared-by: "a change to a DIFFERENT target on
     * that tier, or the button".  A CONFIRMED change to a different target
     * clears that tier's deny latch UNCONDITIONALLY, so a transiently-bad
     * target is retryable later WITHOUT physical access.  (This asserts the
     * SPEC, not the build's stickier deviation, which persisted the latch here
     * — the [DEC-GLASS-FINDS-WHAT-TESTS-CERTIFY] trap.) */
    binding_confirm(&r);
    CHECK(r.deny == 0, "a confirmed change to a DIFFERENT target CLEARS the "
                       "tier's deny latch (spec bounce-clear)");

    /* POSITIVE control: the once-latched target can now be re-armed — the
     * proof the clear is real and not merely a zeroed field.  skfp differs
     * from nexus's current binding, so it arms a fresh trial. */
    BindArm retry = binding_arm_trial(&r, &sk, ack, sizeof(ack));
    CHECK(retry == BindArm::Armed,
          "the once-latched target is retryable after the latch clears");
}

/* ================================================================== */
static void test_confirm_gate(void) {
    printf("-- two-plane commit gate ([CXC-REPOINT-CONFIRM]) --\n");

    /* ⛔ POSITIVE CONTROL (Finding 1): a REAL/echoing broker makes the echo
     * plane true and the trial COMMITS.  This is the check the confirm plane
     * was missing — the gate was structurally stuck closed (the self-src loop
     * guard dropped our own echo, and a Kind mismatch dropped it again), so it
     * could only ever report "revert".  binding_confirm_ready IS the exact
     * decision bus_binding.cpp::try_confirm runs. */
    BindConfirmGate both = { /*store_required=*/true, /*store_ok=*/true,
                             /*echo_ok=*/true };
    CHECK(binding_confirm_ready(&both),
          "store 200 + broker echo BOTH present -> trial COMMITS (positive)");

    /* NEGATIVE: the exact stuck-closed bug — store fetched, but the echo never
     * came back (a broker that accepts TCP but does not fan the echo).  MUST
     * still fail to confirm, so the impostor semantics hold. */
    BindConfirmGate no_echo = { true, /*store_ok=*/true, /*echo_ok=*/false };
    CHECK(!binding_confirm_ready(&no_echo),
          "store ok but NO broker echo -> does NOT commit (impostor reverts)");

    /* NEGATIVE: echo present but the store plane failed under config:http. */
    BindConfirmGate no_store = { /*store_required=*/true, /*store_ok=*/false,
                                 /*echo_ok=*/true };
    CHECK(!binding_confirm_ready(&no_store),
          "echo ok but store unreachable (config:http) -> does NOT commit");

    /* POSITIVE (vacuous store): under config:bus / config:cached the store
     * plane is never exercised, so the echo ALONE confirms.  The broker echo
     * is still mandatory — a missing echo here still reverts. */
    BindConfirmGate vac_ok = { /*store_required=*/false, /*store_ok=*/false,
                               /*echo_ok=*/true };
    CHECK(binding_confirm_ready(&vac_ok),
          "config:bus/cached: broker echo ALONE commits (store vacuous)");
    BindConfirmGate vac_no_echo = { /*store_required=*/false, false,
                                    /*echo_ok=*/false };
    CHECK(!binding_confirm_ready(&vac_no_echo),
          "config:bus/cached with NO echo -> still does NOT commit");

    /* the revert-reason floor names the outstanding plane, never bare timeout */
    CHECK(strcmp(binding_default_reason(true, false, false),
                 "store-unreachable") == 0,
          "reason floor: store required + not ok -> store-unreachable");
    CHECK(strcmp(binding_default_reason(true, true, false),
                 "broker-no-echo") == 0,
          "reason floor: store ok, echo missing -> broker-no-echo");
    CHECK(strcmp(binding_default_reason(false, false, false),
                 "broker-no-echo") == 0,
          "reason floor: vacuous store, echo missing -> broker-no-echo");
}

/* ================================================================== */
static void test_hatch(void) {
    printf("-- two-stage escape hatch "
           "([DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]) --\n");
    const uint32_t SHORT = 3000, LONG = 10000;

    /* NEGATIVE: a tap is NOT a clear (retains download-mode meaning) */
    CHECK(binding_hatch_action(500, SHORT, LONG) == HatchAction::None,
          "a 0.5 s tap clears NOTHING");
    CHECK(binding_hatch_action(2999, SHORT, LONG) == HatchAction::None,
          "just under short threshold clears nothing");

    /* SHORT hold clears per-device only (accidental-bump-safe) */
    CHECK(binding_hatch_action(3000, SHORT, LONG) == HatchAction::ClearPerDevice,
          "3 s hold clears per-device tier ONLY");
    CHECK(binding_hatch_action(5000, SHORT, LONG) == HatchAction::ClearPerDevice,
          "5 s hold clears per-device tier ONLY");

    /* LONG hold clears BOTH (beats a bad global) */
    CHECK(binding_hatch_action(10000, SHORT, LONG) == HatchAction::ClearBoth,
          "10 s hold clears BOTH tiers");
    CHECK(binding_hatch_action(20000, SHORT, LONG) == HatchAction::ClearBoth,
          "20 s hold clears BOTH tiers");
}

/* ================================================================== */
static void test_announce(void) {
    printf("-- announce word ([CXC-REPOINT-ANNOUNCE]) --\n");
    /* known layout: fp 0xABCD, confirmed(1), nvs-per-device(2), 3 reverts,
     * flags 0 */
    uint32_t w = binding_announce_word(0xABCD, AnnState::Confirmed,
                                       BindSource::NvsPerDevice, 3, 0);
    CHECK((w >> 16) == 0xABCD, "bits 31..16 = fingerprint");
    CHECK(((w >> 12) & 0xF) == 1, "bits 15..12 = state (confirmed)");
    CHECK(((w >> 8) & 0xF) == 2, "bits 11..8 = source (nvs-per-device)");
    CHECK(((w >> 4) & 0xF) == 3, "bits 7..4 = reverts");
    CHECK((w & 0xF) == 0, "bits 3..0 = flags");

    /* reverts saturate at 15 in the 4-bit field */
    uint32_t w2 = binding_announce_word(0, AnnState::Trial, BindSource::Compiled,
                                        99, kFlagNoEscapeHatch);
    CHECK(((w2 >> 4) & 0xF) == 15, "reverts saturate at 15");
    CHECK((w2 & 0xF) == kFlagNoEscapeHatch, "no-escape-hatch flag bit 0 set");

    /* flags carry both capability-absence bits */
    uint32_t w3 = binding_announce_word(1, AnnState::Refused, BindSource::NaFramed,
                                        0, kFlagNoEscapeHatch | kFlagNoCaptivePortal);
    CHECK((w3 & 0xF) == 0x3, "both capability-absence flags set");
    CHECK(((w3 >> 8) & 0xF) == 5, "source n/a-framed = 5");

    BindRecord r;
    memset(&r, 0, sizeof(r));
    r.reverts = 20;
    CHECK(binding_reverts_saturated(&r) == 15, "reverts_saturated caps at 15");
}

/* ================================================================== */
int main(void) {
    printf("bus_binding core host test ([SPEC:CXC-REPOINT] §9)\n");
    test_parse_serialize();
    test_precedence();
    test_absorbing();
    test_commit_confirmed();
    test_bounce();
    test_confirm_gate();
    test_hatch();
    test_announce();

    int total = s_pass + s_fail;
    printf("%d/%d checks pass\n", s_pass, total);
    if (s_fail == 0) { printf("ALL PASS\n"); return 0; }
    printf("%d FAILED\n", s_fail);
    return 1;
}

#endif /* BUS_BINDING_CORPUS_TEST */
