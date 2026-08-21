/*
 * gauge_selftest.cpp — host proof of the two things on rack_triple_gauge
 * that NO instrument on the board can check: the homing state machine and
 * the channel-set binding arithmetic.
 *   pio run -e native_gauge && .pio/build/native_gauge/program
 *
 * WHAT THIS CAN AND CANNOT PROVE, stated up front because these numbers
 * will get believed:
 *   - it CAN prove the sequencer emits a legal 8-state half-step ring, that
 *     the gate refuses targets before homing, that the sweep is long enough
 *     to reach the stop from the worst start, that the rest offset lands,
 *     and that the binder honours the documented precedence;
 *   - it CANNOT prove a needle moved, that the stop is where the datasheet
 *     says, or that the pull-in rate is met.  Those are EYES, on hardware,
 *     and the descriptor says so.
 */

#ifdef GAUGE_SELFTEST

#include "gauge_bind.h"
#include "realization.h"
#include "x27168.h"
#include "yaml_min.h"

#include <cstdio>
#include <cstring>

static int g_fail = 0;
#define CHECK(cond, ...) do { if (!(cond)) { \
    printf("FAIL %s:%d  ", __FILE__, __LINE__); printf(__VA_ARGS__); \
    printf("\n"); g_fail++; } } while (0)

/* A backend stub that answers exactly like the real one, so the binder is
 * tested against the real three-valued contract. */
struct FakeBackend : RealizationBackend {
    int  n = 3;
    bool rdy = true;
    struct Hit { int inst; const char* ch; float u; };
    Hit hits[16]; int nhits = 0;

    const char* name() const override { return "fake"; }
    int instance_count() const override { return n; }
    ChannelSupport realizes(const char* c) const override {
        if (!strcmp(c, "value")) return ChannelSupport::Realized;
        if (!strcmp(c, "illum")) return ChannelSupport::Realized;
        if (!strcmp(c, "alarm")) return ChannelSupport::NotApplicable;
        return ChannelSupport::Unknown;
    }
    bool ready() const override { return rdy; }
    void set_channel(int i, const char* c, float u) override {
        if (nhits < 16) hits[nhits++] = { i, c, u };
    }
    void tick(uint32_t) override {}
    const char* status() const override { return "fake"; }
};

/* Run the machine to completion, counting steps and the distinct patterns
 * seen.  `start_offset` simulates a needle that begins somewhere unknown —
 * the machine cannot see it, so we only assert what the machine commands. */
static void run(X27168& m, uint32_t& t, int max_ms) {
    for (int i = 0; i < max_ms; i++) { m.tick(t); t++; }
}

int main() {
    printf("== rack_triple_gauge host selftest ==\n");

    /* ---- 1. the half-step ring ---------------------------------- */
    {
        /* Every state must energise at least one coil in a legal way and
         * the ring must be a permutation with no repeats. */
        for (int i = 0; i < 8; i++) {
            uint8_t p = kX27HalfStep[i];
            CHECK((p & 0x3) != 0x3, "state %d shorts coil A (IN1=IN2=1)", i);
            CHECK((p & 0xC) != 0xC, "state %d shorts coil B (IN3=IN4=1)", i);
            for (int j = i + 1; j < 8; j++)
                CHECK(kX27HalfStep[i] != kX27HalfStep[j],
                      "duplicate state %d/%d", i, j);
        }
        /* Adjacent states differ in exactly ONE coil — that is what makes it
         * a half-step ring rather than an arbitrary table, and a table that
         * violates it would run rough and lose steps at speed. */
        for (int i = 0; i < 8; i++) {
            uint8_t a = kX27HalfStep[i], b = kX27HalfStep[(i + 1) & 7];
            bool coilA_same = (a & 0x3) == (b & 0x3);
            bool coilB_same = (a & 0xC) == (b & 0xC);
            CHECK(coilA_same != coilB_same,
                  "states %d->%d change %s coils", i, (i + 1) & 7,
                  coilA_same && coilB_same ? "no" : "both");
        }
        printf("  half-step ring: 8 legal, non-repeating, one-coil-at-a-time\n");
    }

    /* ---- 2. the gate ------------------------------------------- */
    {
        X27168 m; m.configure(1890, 2160, 18, 400, 800); m.reset();
        CHECK(!m.homed(), "a fresh needle must NOT report homed");
        m.set_target(900);
        CHECK(m.target() == 0,
              "*** A TARGET WAS ACCEPTED BEFORE HOMING (%d).  This is the "
              "defect the whole design exists to prevent. ***", m.target());
        printf("  gate: target refused before homing\n");
    }

    /* ---- 3. the homing sweep ----------------------------------- */
    {
        X27168 m; m.configure(1890, 2160, 18, 400, 800); m.reset();
        uint32_t t = 0;
        m.start_homing(t);
        CHECK(!m.homed(), "homing must not complete instantly");

        /* 2160 down + 18 up at 400 Hz = 2.5 ms/step -> 5445 ms.  Give it a
         * generous window; assert on the RESULT, not the schedule. */
        run(m, t, 8000);
        CHECK(m.homed(), "homing did not complete in 8 s (phase=%d, %d/%d)",
              (int)m.phase(), m.home_steps_commanded(), 2160);
        CHECK(m.position() == 0, "post-home position must be 0, got %d",
              m.position());
        CHECK(m.home_steps_commanded() == 2160,
              "homing commanded %d steps, expected 2160",
              m.home_steps_commanded());

        /* THE MARGIN CHECK, and it is the one that matters: the sweep must
         * exceed the full travel, or a needle starting at full scale never
         * reaches the stop and zero is wherever it happened to stop. */
        CHECK(2160 > m.travel_half_steps(),
              "*** HOMING SWEEP (2160) IS NOT LONGER THAN TRAVEL (%d) — a "
              "needle starting at full scale would never reach the stop "
              "and zero would be a fiction. ***", m.travel_half_steps());
        const int margin = 2160 - m.travel_half_steps();
        printf("  homing: 2160 commanded, travel %d, margin %d half-steps "
               "(%.1f deg)\n", m.travel_half_steps(), margin, margin / 6.0);
        CHECK(margin >= 180, "margin %d half-steps (%.1f deg) is thin — a "
              "hand-fitted pointer can be that far out", margin,
              margin / 6.0);

        /* Now it accepts targets. */
        m.set_target(1890);
        CHECK(m.target() == 1890, "full scale refused after homing");
        m.set_target(99999);
        CHECK(m.target() == 1890, "target not clamped to travel (%d)",
              m.target());
        m.set_target(-5);
        CHECK(m.target() == 0, "negative target not clamped (%d)", m.target());
        printf("  post-home: targets accepted and clamped to [0,1890]\n");
    }

    /* ---- 4. full-scale slew reaches the target ------------------ */
    {
        X27168 m; m.configure(1890, 2160, 18, 400, 800); m.reset();
        uint32_t t = 0; m.start_homing(t); run(m, t, 8000);
        m.set_unit(1.0f);
        CHECK(m.target() == 1890, "unit 1.0 -> %d, expected 1890", m.target());
        run(m, t, 4000);                    /* 1890 / 800 Hz = 2363 ms */
        CHECK(m.position() == 1890, "full-scale slew stalled at %d",
              m.position());
        m.set_unit(0.5f);
        CHECK(m.target() == 945, "unit 0.5 -> %d, expected 945", m.target());
        printf("  slew: unit 1.0 reached 1890 half-steps in < 4 s\n");
    }

    /* ---- 5. port packing --------------------------------------- */
    {
        X27168 a, b, c;
        a.configure(1890,2160,18,400,800); a.reset();
        b.configure(1890,2160,18,400,800); b.reset();
        c.configure(1890,2160,18,400,800); c.reset();
        uint32_t t = 0;
        a.start_homing(t); b.start_homing(t); c.start_homing(t);
        a.tick(t); b.tick(t); c.tick(t);
        uint8_t ga = 0, gb = 0;
        x27_pack_ports(a, b, c, &ga, &gb);
        CHECK((ga & 0x0F) == a.coils(), "GPIOA low nibble != gauge 1");
        CHECK(((ga >> 4) & 0x0F) == b.coils(), "GPIOA high nibble != gauge 2");
        CHECK((gb & 0x0F) == c.coils(), "GPIOB low nibble != gauge 3");
        CHECK((gb & 0xF0) == 0,
              "*** GPIOB TOP NIBBLE IS NON-ZERO (%02X) — those four pins "
              "leave the chip on J_EXP_MCP and are not ours to drive. ***",
              gb);
        printf("  packing: 12 coil lines in 2 bytes, J_EXP_MCP nibble clean\n");
    }

    /* ---- 6. the channel-set binder ----------------------------- */
    {
        static const char kYaml[] =
            "widgets:\n"
            "  - type: stack\n"
            "    children:\n"
            "      - type: gauge\n"
            "        id: left\n"
            "        min_val: 0\n"
            "        max_val: 255\n"
            "        namespace: throttle/left\n"
            "      - type: gauge\n"
            "        id: right\n"
            "        min_val: 0\n"
            "        max_val: 255\n"
            "        namespace: throttle/right\n"
            "      - type: gauge\n"
            "        id: ctx\n"
            "        min_val: 0\n"
            "        max_val: 100\n"
            "        namespace: self/context_pct\n";
        YamlDoc d;
        const YamlNode* root = d.parse(kYaml, strlen(kYaml));
        CHECK(root != nullptr, "yaml parse failed: %s", d.error());
        FakeBackend be;
        GaugeBindings gb;
        gauge_bind(root, be, &gb);
        CHECK(gb.count == 3, "bound %d gauges, expected 3", gb.count);
        CHECK(gb.gauges_seen == 3, "saw %d gauges", gb.gauges_seen);

        const char* subs[8];
        size_t n = gb.namespaces(subs, 8);
        CHECK(n == 3, "subscribed %zu namespaces, expected 3", n);

        /* THE FIRST-VALIDATION BINDING, verified as arithmetic:
         * throttle 0..255 and context 0..100 must BOTH land on the same
         * unit interval — that is the "one semantic value, many physical
         * realizations" mechanism, and getting it wrong is a gauge that is
         * 2.55x out with every signal healthy. */
        be.nhits = 0;
        gb.dispatch(be, "throttle/left", 255.0);
        CHECK(be.nhits == 1 && be.hits[0].inst == 0, "throttle/left -> g1");
        CHECK(be.hits[0].u > 0.999f, "255/255 -> unit %f, expected 1.0",
              (double)be.hits[0].u);

        be.nhits = 0;
        gb.dispatch(be, "self/context_pct", 50.0);
        CHECK(be.nhits == 1 && be.hits[0].inst == 2, "context -> g3");
        CHECK(be.hits[0].u > 0.499f && be.hits[0].u < 0.501f,
              "50/100 -> unit %f, expected 0.5", (double)be.hits[0].u);

        be.nhits = 0;
        gb.dispatch(be, "throttle/right", 400.0);      /* OVERRANGE */
        CHECK(be.hits[0].u == 1.0f,
              "overrange 400 on 0..255 -> unit %f; it must CLAMP, or a "
              "needle is driven past its stop", (double)be.hits[0].u);
        printf("  binder: legacy `namespace:` form, 0..255 and 0..100 both "
               "normalise, overrange clamps\n");
    }

    /* ---- 7. the channel-set form and its precedence ------------ */
    {
        static const char kYaml[] =
            "widgets:\n"
            "  - type: gauge\n"
            "    id: g1\n"
            "    min_val: 0\n"
            "    max_val: 255\n"
            "    namespace: legacy/ignored\n"
            "    channels:\n"
            "      value: {namespace: throttle/left, min: 0, max: 255}\n"
            "      illum: {namespace: rack/backlight, min: 0, max: 255}\n"
            "      alarm: {namespace: rack/alarm}\n"
            "      bogus: {namespace: rack/bogus}\n";
        YamlDoc d;
        const YamlNode* root = d.parse(kYaml, strlen(kYaml));
        CHECK(root != nullptr, "yaml parse failed: %s", d.error());
        FakeBackend be;
        GaugeBindings gb;
        gauge_bind(root, be, &gb);
        CHECK(gb.count == 1, "bound %d, expected 1", gb.count);
        /* value + illum bind; alarm is NotApplicable (silent, unbound);
         * bogus is Unknown (loud, counted). */
        CHECK(gb.inst[0].count == 2, "bound %d channels, expected 2 "
              "(value+illum; alarm NotApplicable, bogus Unknown)",
              gb.inst[0].count);
        CHECK(gb.channels_unknown == 1, "unknown channels %d, expected 1",
              gb.channels_unknown);

        /* PRECEDENCE: explicit channels.value must beat `namespace:`. */
        const char* subs[8];
        size_t n = gb.namespaces(subs, 8);
        bool saw_legacy = false;
        for (size_t i = 0; i < n; i++)
            if (!strcmp(subs[i], "legacy/ignored")) saw_legacy = true;
        CHECK(!saw_legacy,
              "*** legacy `namespace:` bound DESPITE an explicit "
              "channels.value — the documented precedence is broken ***");
        printf("  channel set: value+illum bound, NotApplicable silent, "
               "Unknown loud, explicit beats legacy\n");
    }

    /* ---- 8. more gauges than the board has --------------------- */
    {
        static const char kYaml[] =
            "widgets:\n"
            "  - type: gauge\n    namespace: a\n"
            "  - type: gauge\n    namespace: b\n"
            "  - type: gauge\n    namespace: c\n"
            "  - type: gauge\n    namespace: d\n";
        YamlDoc d;
        const YamlNode* root = d.parse(kYaml, strlen(kYaml));
        FakeBackend be;
        GaugeBindings gb;
        gauge_bind(root, be, &gb);
        CHECK(gb.count == 3, "bound %d, expected 3", gb.count);
        CHECK(gb.gauges_dropped == 1,
              "the 4th gauge must be DROPPED and COUNTED (dropped=%d)",
              gb.gauges_dropped);
        printf("  N+1: a 4th gauge on a 3-gauge board is dropped and "
               "counted, not silently truncated\n");
    }

    printf("\n%s — %d failure(s)\n", g_fail ? "RESULT: FAIL" : "RESULT: PASS",
           g_fail);
    return g_fail ? 1 : 0;
}

#endif /* GAUGE_SELFTEST */
