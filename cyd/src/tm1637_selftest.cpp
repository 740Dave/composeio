/*
 * tm1637_selftest.cpp — host harness for the TM1637 backend.  PLAN I3.
 * [SPEC:CXC-VISION] [CXC-VISION-SEMANTIC-REALIZATION] [CXC-VISION-EPISTEMICS]
 *
 * Pure host test (no hardware, no ESP-IDF) — the guarded-file idiom of
 * bus_frame_selftest.cpp / restart_selftest.cpp; the body is behind
 * TM1637_CORPUS_TEST, defined only by [env:native_tm1637].
 *
 *   pio run -e native_tm1637 && .pio/build/native_tm1637/program
 * Must print "ALL PASS" and exit 0.
 *
 * FOUR LAYERS, and they are deliberately independent instruments.  A
 * bit-banged protocol driving a display is the worst case in this repo's
 * catalogue of self-confirming evidence: every write "succeeds", nothing
 * on the wire is readable back, and a wrong table produces confident,
 * well-formed, WRONG glyphs.  So:
 *
 *   1. GLYPH PARITY vs the raster widget's truth table (external oracle,
 *      cyd/test/tm1637_corpus_generated.h).
 *   2. JUSTIFICATION PARITY vs the raster widget's _display_value().
 *   3. WAVEFORM — a GOLDEN transaction transcribed BY HAND from the
 *      datasheet's own description, plus generic protocol invariants
 *      checked over every transaction the suite emits.
 *   4. NEGATIVE CONTROLS — deliberately corrupted waveforms that the
 *      layer-3 checker MUST reject.  [CXC-VISION-EPISTEMICS]
 *      rule-negative-control: an instrument that can only report success
 *      is not an instrument.  Without these, a checker with an inverted
 *      comparison passes everything and reads as a clean run.
 *
 * ⛔ WHAT NO LAYER HERE CAN PROVE.  Say it plainly rather than let an
 * ALL PASS imply it.  There is no TM1637 on any bench.  The SEGMENT MAP
 * (which SEG pin lights which bar), the DIGIT ORDER (whether GRID1 is the
 * leftmost tube), the COLON wiring, real ACK behaviour, the 3.3V/5V level
 * question, and the multi-display shared-clock inference are ALL
 * unverified and NONE of them is observable from this process.  They need
 * a lit tube and David's eyes.
 */

#ifdef TM1637_CORPUS_TEST

#include "tm1637.h"
#include "tm1637_corpus_generated.h"

#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static int s_pass = 0, s_fail = 0;

#define CHECK(cond, ...)                                                  \
    do {                                                                  \
        if (cond) { s_pass++; }                                           \
        else { s_fail++; printf("FAIL: "); printf(__VA_ARGS__);           \
               printf("\n"); }                                            \
    } while (0)

/* ------------------------------------------------------------------
 * A recording virtual bus.  Tokens: C0/C1 = CLK low/released,
 * D0/D1 = DIO low/released, R = master sampled DIO.  Each event carries
 * a virtual timestamp so TIMING minima are checkable, not just ordering.
 * ------------------------------------------------------------------ */
struct Event { const char* tok; uint32_t t_us; };

struct Recorder {
    std::vector<Event> ev;
    uint32_t t = 0;
    bool clk_high = true;
    bool dio_high = true;
    bool ack_low = true;      /* what a present, ACKing part would do   */

    void push(const char* tok) { ev.push_back({tok, t}); }
};

static void r_clk_low(void* c)     { auto* r = (Recorder*)c; r->clk_high = false; r->push("C0"); }
static void r_clk_release(void* c) { auto* r = (Recorder*)c; r->clk_high = true;  r->push("C1"); }
static void r_dio_low(void* c)     { auto* r = (Recorder*)c; r->dio_high = false; r->push("D0"); }
static void r_dio_release(void* c) { auto* r = (Recorder*)c; r->dio_high = true;  r->push("D1"); }
static bool r_dio_read(void* c)    { auto* r = (Recorder*)c; r->push("R");
                                     return r->ack_low ? false : true; }
static void r_delay(void* c, uint32_t us) { ((Recorder*)c)->t += us; }

static Tm1637Bus recorder_bus(Recorder& r) {
    Tm1637Bus b;
    b.clk_low = r_clk_low;   b.clk_release = r_clk_release;
    b.dio_low = r_dio_low;   b.dio_release = r_dio_release;
    b.dio_read = r_dio_read; b.delay_us = r_delay;
    b.ctx = &r;
    return b;
}

static std::string trace_of(const Recorder& r) {
    std::string s;
    for (size_t i = 0; i < r.ev.size(); i++) {
        if (i) s += " ";
        s += r.ev[i].tok;
    }
    return s;
}

/* ------------------------------------------------------------------
 * Layer 3b — generic protocol invariants, straight from p.3.
 *
 * THE CENTRAL RULE:  "DIO signal should not change for high level CLK and
 * DIO signal should change for low level CLK signal."  The ONLY two
 * legal DIO transitions while CLK is high are the START (falling) and the
 * STOP (rising).  Any other is a protocol violation that a real part may
 * decode as a spurious start/stop — the precise failure a bench would
 * show as intermittent garbage and a host round-trip test would miss.
 *
 * Returns "" if clean, else a description of the first violation.
 * ------------------------------------------------------------------ */
static std::string check_invariants(const std::vector<Event>& ev,
                                    uint32_t min_clk_high_us) {
    bool clk = true, dio = true;
    bool in_frame = false;          /* between a START and its STOP */
    int  rising = 0;                /* CLK rising edges this frame  */
    uint32_t clk_rose_at = 0;
    char buf[256];

    for (size_t i = 0; i < ev.size(); i++) {
        const char* t = ev[i].tok;

        if (strcmp(t, "C1") == 0) {
            if (!clk) { clk_rose_at = ev[i].t_us; if (in_frame) rising++; }
            clk = true;
        } else if (strcmp(t, "C0") == 0) {
            if (clk) {
                /* tWAIT: CLK rising -> CLK falling must be >= 1 us. */
                uint32_t high = ev[i].t_us - clk_rose_at;
                if (high < min_clk_high_us) {
                    snprintf(buf, sizeof(buf),
                             "event %zu: CLK high only %u us, tWAIT min is %u",
                             i, high, min_clk_high_us);
                    return buf;
                }
            }
            clk = false;
        } else if (strcmp(t, "D0") == 0) {
            if (clk) {
                if (in_frame) {
                    snprintf(buf, sizeof(buf),
                             "event %zu: DIO fell while CLK high INSIDE a "
                             "frame — a spurious START", i);
                    return buf;
                }
                in_frame = true;      /* legal START */
                rising = 0;
            } else if (!in_frame) {
                snprintf(buf, sizeof(buf),
                         "event %zu: DIO driven low outside any frame", i);
                return buf;
            }
            dio = false;
        } else if (strcmp(t, "D1") == 0) {
            if (clk && in_frame) {
                if (!dio) {
                    /* Legal STOP.  p.3 diagrams: 9 clocks per byte (8 data
                     * + 1 ack).  EXACTLY ONE of the counted rising edges
                     * belongs to the STOP itself and is not a data clock:
                     * a stop is "DIO rises while CLK is high", so CLK must
                     * be taken high first.  Subtract it explicitly rather
                     * than fudging the modulus — the off-by-one is a real
                     * property of the sequence, and hiding it in a magic
                     * constant is how the next reader gets it wrong. */
                    const int data_clocks = rising - 1;
                    if (data_clocks <= 0 || data_clocks % 9 != 0) {
                        snprintf(buf, sizeof(buf),
                                 "event %zu: STOP after %d data CLK rising "
                                 "edges — not a whole number of 9-clock bytes",
                                 i, data_clocks);
                        return buf;
                    }
                    in_frame = false;
                }
            }
            dio = true;
        }
    }
    if (in_frame) return "trace ended inside a frame — missing STOP";
    return "";
}

/* Run a transaction and return its trace + invariant verdict. */
struct Run { std::string trace; std::string violation; };

template <typename F>
static Run run(F fn, uint32_t half_us = TM1637_HALF_PERIOD_US_DEFAULT,
               uint8_t digits = 4) {
    Recorder r;
    Tm1637 d;
    tm1637_init(d, recorder_bus(r), digits, half_us);
    fn(d);
    Run out;
    out.trace = trace_of(r);
    out.violation = check_invariants(r.ev, TM1637_HALF_PERIOD_US_MIN);
    return out;
}

int main(void) {
    printf("TM1637 backend: parity vs seven_segment.py (oracle) + waveform "
           "vs datasheet V2.4\n");

    /* ============ Layer 1 — glyph semantics parity ============ */
    for (int i = 0; i < kTm1637GlyphCaseCount; i++) {
        const Tm1637GlyphCase& c = kTm1637GlyphCases[i];
        /* Build the expected byte from the oracle's LETTERS through the
         * map under test — so this checks the truth table, and the map is
         * checked separately (below) as the distinct claim it is. */
        uint8_t want = 0;
        for (const char* p = c.segs; *p; p++) {
            switch (*p) {
                case 'a': want |= 1u << kTm1637SegmentMapDefault.a; break;
                case 'b': want |= 1u << kTm1637SegmentMapDefault.b; break;
                case 'c': want |= 1u << kTm1637SegmentMapDefault.c; break;
                case 'd': want |= 1u << kTm1637SegmentMapDefault.d; break;
                case 'e': want |= 1u << kTm1637SegmentMapDefault.e; break;
                case 'f': want |= 1u << kTm1637SegmentMapDefault.f; break;
                case 'g': want |= 1u << kTm1637SegmentMapDefault.g; break;
            }
        }
        uint8_t got = tm1637_glyph(c.ch, kTm1637SegmentMapDefault);
        CHECK(got == want, "glyph '%c': Python segs \"%s\" -> 0x%02X, "
              "C++ 0x%02X", c.ch, c.segs, want, got);
    }

    /* An unknown character must blank, never garbage. */
    CHECK(tm1637_glyph('Z', kTm1637SegmentMapDefault) == 0x00,
          "unknown char must render blank");
    /* dp is NEVER part of a glyph — it is punctuation, folded separately. */
    for (int i = 0; i < kTm1637GlyphCaseCount; i++) {
        uint8_t g = tm1637_glyph(kTm1637GlyphCases[i].ch,
                                 kTm1637SegmentMapDefault);
        CHECK((g & tm1637_dp_bit(kTm1637SegmentMapDefault)) == 0,
              "glyph '%c' must not set the dp bit", kTm1637GlyphCases[i].ch);
    }

    /* ============ Layer 2 — justification parity ============ */
    for (int i = 0; i < kTm1637JustifyCaseCount; i++) {
        const Tm1637JustifyCase& c = kTm1637JustifyCases[i];
        Recorder r;
        Tm1637 d;
        tm1637_init(d, recorder_bus(r), (uint8_t)c.digits,
                    TM1637_HALF_PERIOD_US_DEFAULT);
        Tm1637Format fmt = {-1, false};
        uint8_t got[TM1637_MAX_DIGITS];
        tm1637_format_string(d, c.value, fmt, got);

        bool ok = true;
        for (int k = 0; k < c.digits; k++) {
            if (got[k] != tm1637_glyph(c.shown[k], d.map)) { ok = false; break; }
        }
        CHECK(ok, "justify \"%s\" x%d: Python shows \"%s\", C++ bytes differ",
              c.value, c.digits, c.shown);
    }

    /* '.' folds into the PRECEDING digit's dp rather than taking a cell —
     * the realization difference from the raster widget, pinned so it
     * cannot drift into silently dropping the point. */
    {
        Recorder r; Tm1637 d;
        tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        Tm1637Format fmt = {-1, false};
        uint8_t seg[TM1637_MAX_DIGITS];
        tm1637_format_string(d, "12.34", fmt, seg);
        const uint8_t dp = tm1637_dp_bit(d.map);
        CHECK((seg[1] & dp) != 0, "\"12.34\": dp must land on digit 1");
        CHECK((seg[0] & dp) == 0 && (seg[2] & dp) == 0 && (seg[3] & dp) == 0,
              "\"12.34\": dp must land on ONLY digit 1");
        CHECK((seg[0] & ~dp) == tm1637_glyph('1', d.map) &&
              (seg[3] & ~dp) == tm1637_glyph('4', d.map),
              "\"12.34\": five characters must occupy FOUR digits");
    }

    /* ============ Layer 3a — the GOLDEN waveform ============
     * Transcribed BY HAND from the datasheet's prose, not captured from
     * this driver.  Transaction: the display-control command for
     * brightness 7, display ON = 0x80 | 0x07 | 0x08 = 0x8F.
     *
     *   0x8F = 1000 1111b.  LSB FIRST on the wire: 1,1,1,1,0,0,0,1.
     *
     *   START (p.3)          CLK released, DIO released, DIO FALLS, CLK falls
     *   bits 0..3 = 1        CLK low, DIO released, CLK rises (latch)   x4
     *   bits 4..6 = 0        CLK low, DIO low,      CLK rises (latch)   x3
     *   bit  7    = 1        CLK low, DIO released, CLK rises (latch)
     *   ACK   (p.3)          CLK low (8th fall: part pulls DIO), master
     *                        releases DIO, SAMPLES, 9th clock high, low
     *   STOP  (p.3)          CLK low, DIO low, CLK rises, DIO RISES
     */
    {
        Run g = run([](Tm1637& d) { tm1637_set_brightness(d, 7); });
        const std::string golden =
            "C1 D1 D0 C0 "
            "C0 D1 C1 C0 D1 C1 C0 D1 C1 C0 D1 C1 "
            "C0 D0 C1 C0 D0 C1 C0 D0 C1 "
            "C0 D1 C1 "
            "C0 D1 R C1 C0 "
            "C0 D0 C1 D1";
        CHECK(g.trace == golden,
              "golden waveform for cmd 0x8F mismatch\n      want: %s\n"
              "      got : %s", golden.c_str(), g.trace.c_str());
        CHECK(g.violation.empty(), "golden transaction violates invariants: %s",
              g.violation.c_str());
    }

    /* ============ Layer 3b — invariants over every transaction ======== */
    {
        struct { const char* name; Run r; } cases[] = {
            {"set_brightness", run([](Tm1637& d){ tm1637_set_brightness(d,3); })},
            {"set_on(false)",  run([](Tm1637& d){ tm1637_set_on(d,false); })},
            {"clear",          run([](Tm1637& d){ tm1637_clear(d); })},
            {"write 4 digits", run([](Tm1637& d){
                uint8_t s[4] = {0x3F,0x06,0x5B,0x4F};
                tm1637_write_segments(d,0,s,4); })},
            {"write 6 digits", run([](Tm1637& d){
                uint8_t s[6] = {1,2,3,4,5,6};
                tm1637_write_segments(d,0,s,6); }, 5, 6)},
            {"read_keys",      run([](Tm1637& d){ uint8_t k; tm1637_read_keys(d,k); })},
            {"min half-period",run([](Tm1637& d){ tm1637_set_brightness(d,0); },
                                   TM1637_HALF_PERIOD_US_MIN)},
        };
        for (auto& c : cases) {
            CHECK(c.r.violation.empty(), "%s: %s", c.name,
                  c.r.violation.c_str());
        }
    }

    /* The auto-increment run must be ONE frame (p.4 diagram 2): address
     * command and all N data bytes inside a single START..STOP, or the
     * address auto-increment does not apply and every byte overwrites
     * digit 0.  9 clocks x (1 addr + 4 data) = 45 rising edges. */
    {
        Run g = run([](Tm1637& d) {
            uint8_t s[4] = {0x3F, 0x06, 0x5B, 0x4F};
            tm1637_write_segments(d, 0, s, 4);
        });
        /* Three frames: data-cmd, addr+data, display-cmd. */
        int starts = 0;
        size_t pos = 0;
        while ((pos = g.trace.find("D1 D0 C0", pos)) != std::string::npos) {
            starts++; pos += 8;
        }
        CHECK(starts == 3, "auto-increment write must be 3 frames, saw %d",
              starts);
    }

    /* ============ Layer 4 — NEGATIVE CONTROLS ============
     * The layer-3 checker must REJECT each of these.  A checker that
     * cannot fail is not measuring anything; these are the fault
     * injections that prove its clean verdicts mean something.
     * [CXC-VISION-EPISTEMICS] rule-negative-control. */
    {
        struct Neg { const char* name; std::vector<Event> ev; };
        const uint32_t H = TM1637_HALF_PERIOD_US_DEFAULT;

        /* (a) A DIO transition while CLK is high, mid-frame — a spurious
         *     START.  This is the exact defect a shared-clock harness
         *     would produce if the driver touched an idle display's DIO. */
        std::vector<Event> a = {
            {"C1",0},{"D1",0},{"D0",H},{"C0",2*H},
            {"C0",2*H},{"D1",2*H},{"C1",3*H},
            {"D0",4*H},                       /* <-- DIO falls, CLK HIGH */
        };
        /* (b) CLK high for less than tWAIT. */
        std::vector<Event> b = {
            {"C1",0},{"D1",0},{"D0",H},{"C0",2*H},
            {"C0",2*H},{"D1",2*H},{"C1",3*H},{"C0",3*H},  /* 0 us high */
        };
        /* (c) A STOP after a non-multiple-of-9 clock count — a truncated
         *     byte, which is what an off-by-one bit loop produces. */
        std::vector<Event> c = {
            {"C1",0},{"D1",0},{"D0",H},{"C0",2*H},
            {"C0",2*H},{"D1",2*H},{"C1",3*H},{"C0",4*H},
            {"D0",4*H},{"C1",5*H},{"D1",6*H},  /* stop after 2 risings */
        };
        /* (d) A frame that never stops. */
        std::vector<Event> dd = {
            {"C1",0},{"D1",0},{"D0",H},{"C0",2*H},
        };

        Neg negs[] = {
            {"spurious START mid-frame", a},
            {"CLK high shorter than tWAIT", b},
            {"STOP at a non-byte boundary", c},
            {"frame never closed", dd},
        };
        for (auto& n : negs) {
            std::string v = check_invariants(n.ev, TM1637_HALF_PERIOD_US_MIN);
            CHECK(!v.empty(),
                  "NEGATIVE CONTROL FAILED — checker accepted \"%s\"; a clean "
                  "run from this checker would mean nothing", n.name);
        }
        /* And the golden must still be accepted, or the checker is simply
         * rejecting everything — the mirror-image vacuous instrument. */
        Run g = run([](Tm1637& d) { tm1637_set_brightness(d, 7); });
        CHECK(g.violation.empty(),
              "NEGATIVE CONTROL FAILED — checker rejects a VALID trace too");
    }

    /* ============ Timing limits are enforced, not documented ========= */
    {
        Recorder r; Tm1637 d;
        tm1637_init(d, recorder_bus(r), 4, 0);       /* caller asks 0 us */
        CHECK(d.half_period_us == TM1637_HALF_PERIOD_US_MIN,
              "half-period 0 must clamp to %u us, got %u",
              TM1637_HALF_PERIOD_US_MIN, d.half_period_us);
        CHECK(d.timing_was_clamped,
              "a clamp must be RECORDED, not applied silently");

        Recorder r2; Tm1637 d2;
        tm1637_init(d2, recorder_bus(r2), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        CHECK(!d2.timing_was_clamped, "a legal half-period must not flag");
    }

    /* Key reads carry the SEPARATE 250 kHz ceiling (p.3) and must slow
     * down for it even when writes are legally faster. */
    {
        Recorder r; Tm1637 d;
        tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_MIN);
        uint8_t k = 0;
        tm1637_read_keys(d, k);
        std::string v = check_invariants(r.ev,
                                         TM1637_HALF_PERIOD_US_MIN_KEYREAD);
        CHECK(v.empty(), "key read must honour the 250 kHz ceiling: %s",
              v.c_str());
        CHECK(d.half_period_us == TM1637_HALF_PERIOD_US_MIN,
              "the write half-period must be RESTORED after a key read");
    }

    /* ============ Brightness / power command encoding (p.5) ========== */
    {
        /* The display-control byte is 0x80 | brightness | 0x08-if-on.
         * Checked by decoding it back out of the waveform, LSB first. */
        struct { uint8_t bright; bool on; uint8_t want; } cs[] = {
            {0, true, 0x88}, {7, true, 0x8F}, {3, true, 0x8B},
            {7, false, 0x87}, {0, false, 0x80},
        };
        for (auto& c : cs) {
            Recorder r; Tm1637 d;
            tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_DEFAULT);
            d.on = c.on;
            tm1637_set_brightness(d, c.bright);
            /* Decode: after the START (C1 D1 D0 C0), each bit is the D
             * token preceding a C1.  D1 = 1, D0 = 0, LSB first. */
            uint8_t got = 0; int bit = 0; const char* last_d = nullptr;
            for (size_t i = 4; i < r.ev.size() && bit < 8; i++) {
                const char* t = r.ev[i].tok;
                if (t[0] == 'D') last_d = t;
                else if (strcmp(t, "C1") == 0 && last_d) {
                    if (strcmp(last_d, "D1") == 0) got |= (uint8_t)(1u << bit);
                    bit++; last_d = nullptr;
                }
            }
            CHECK(got == c.want,
                  "display-control byte for brightness %u %s: want 0x%02X, "
                  "wire carried 0x%02X", c.bright, c.on ? "ON" : "OFF",
                  c.want, got);
        }
        /* Out-of-range brightness must clamp, not wrap into the ON bit —
         * a wrap would turn "too bright" into "display off", which is the
         * limit-sized-for-today shape. */
        Recorder r; Tm1637 d;
        tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        tm1637_set_brightness(d, 200);
        CHECK(d.brightness == TM1637_BRIGHTNESS_MAX,
              "brightness 200 must clamp to %u, got %u",
              TM1637_BRIGHTNESS_MAX, d.brightness);
    }

    /* ============ ACK is actually consulted ============
     * A driver that ignores the ACK reports success against a DISCONNECTED
     * part forever — "MCP2515 successfully initialized" in another
     * costume.  With the virtual part not acking, the write must FAIL. */
    {
        Recorder r; Tm1637 d;
        r.ack_low = false;                     /* nobody home */
        tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        CHECK(!tm1637_set_brightness(d, 4),
              "a NAK must be reported as failure, not swallowed");

        Recorder r2; Tm1637 d2;
        r2.ack_low = true;
        tm1637_init(d2, recorder_bus(r2), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        CHECK(tm1637_set_brightness(d2, 4),
              "an ACKed write must be reported as success");
    }

    /* ============ Bus binding ============ */
    {
        Recorder r; Tm1637 d;
        tm1637_init(d, recorder_bus(r), 4, TM1637_HALF_PERIOD_US_DEFAULT);
        Tm1637Binding b;
        Tm1637Format fmt = {-1, false};
        tm1637_bind(b, d, "throttle/left", "panel/brightness",
                    "panel/power", fmt);

        const char* ns[4];
        size_t n = tm1637_namespaces(b, ns, 4);
        CHECK(n == 3, "binding must subscribe 3 exact namespaces, got %zu", n);

        CHECK(tm1637_apply(b, "throttle/left", 0, 42, false, nullptr),
              "value namespace must apply");
        CHECK(!tm1637_apply(b, "throttle/right", 0, 42, false, nullptr),
              "an unbound namespace must NOT apply");

        tm1637_apply(b, "panel/brightness", 0, 0.5, false, nullptr);
        CHECK(d.brightness == 4, "fraction 0.5 -> step 4, got %u", d.brightness);
        tm1637_apply(b, "panel/brightness", 0, 6, false, nullptr);
        CHECK(d.brightness == 6, "raw 6 -> step 6, got %u", d.brightness);

        tm1637_apply(b, "panel/power", 1, 0, false, nullptr);
        CHECK(!d.on, "power false must switch the display off");
        tm1637_apply(b, "panel/power", 1, 0, true, nullptr);
        CHECK(d.on, "power true must switch the display on");
    }

    printf("\n%d passed, %d failed\n", s_pass, s_fail);
    if (s_fail == 0) {
        printf("ALL PASS\n");
        printf("\n"
        "  NOT PROVEN BY THIS RUN — needs a lit tube and David's eyes:\n"
        "    * the SEGMENT MAP (SEG1..SEG8 -> a,b,c,d,e,f,g,dp)\n"
        "    * the DIGIT ORDER (is GRID1 the leftmost tube?)\n"
        "    * the COLON bit (which digit's dp, if any, drives it)\n"
        "    * real ACK behaviour and 3.3V/5V levels on the actual module\n"
        "    * that an unaddressed part on a SHARED CLK truly ignores it\n");
    }
    return s_fail == 0 ? 0 : 1;
}

#endif /* TM1637_CORPUS_TEST */
