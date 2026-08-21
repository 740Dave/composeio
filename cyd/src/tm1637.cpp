/*
 * tm1637.cpp — TM1637 bit-banged two-wire driver + semantic formatter.
 * [SPEC:CXC-VISION] [CXC-VISION-SEMANTIC-REALIZATION]  PLAN I3.
 * Every timing and command constant is cited to Titan Micro TM1637 V2.4.
 *
 * HOST-PROVEN ONLY.  No TM1637 has been on a bench.  What is checked is
 * the WAVEFORM and the GLYPH TABLE, against a hand-transcribed datasheet
 * golden and against the Python widget's truth table respectively
 * (tm1637_selftest.cpp).  What is NOT checked is anything requiring the
 * part: segment mapping, digit order, colon wiring, ACK behaviour, and
 * whether a shared clock is genuinely ignored by an unaddressed device.
 */

#include "tm1637.h"

#include <cstdio>
#include <cstring>

/* ------------------------------------------------------------------
 * Segment map
 * ------------------------------------------------------------------ */
const Tm1637SegmentMap kTm1637SegmentMapDefault = {0, 1, 2, 3, 4, 5, 6, 7};

/* The SEMANTIC truth table.  Kept in the a..g LETTER domain — identical
 * to python/composeio/render/widgets/seven_segment.py TRUTH_TABLE — and
 * only then projected through the map into bits.  Keeping the letters is
 * what makes the parity test against Python possible at all: if this were
 * pre-baked bytes, the shared semantics and the module wiring would be
 * fused into one constant and neither could be checked independently.
 *
 * The degree glyph "°" is in the Python table but is multi-byte UTF-8 and
 * has no char literal, so it is reachable here only via its own entry
 * below keyed on the ASCII placeholder '~'.  Called out because a silent
 * omission is exactly what the parity test is for. */
struct GlyphRow { char ch; const char* segs; };

static const GlyphRow kGlyphs[] = {
    {'0', "abcdef"},  {'1', "bc"},      {'2', "abged"},   {'3', "abgcd"},
    {'4', "fgbc"},    {'5', "afgcd"},   {'6', "afgedc"},  {'7', "abc"},
    {'8', "abcdefg"}, {'9', "abcfgd"},
    {'a', "abcefg"},  {'b', "cdefg"},   {'c', "adef"},    {'d', "bcdeg"},
    {'e', "adefg"},   {'f', "aefg"},
    {'-', "g"},       {'_', "d"},       {'~', "abfg"},    {' ', ""},
};
static const int kGlyphCount = (int)(sizeof(kGlyphs) / sizeof(kGlyphs[0]));

static uint8_t seg_bit(const Tm1637SegmentMap& m, char letter) {
    switch (letter) {
        case 'a': return (uint8_t)(1u << m.a);
        case 'b': return (uint8_t)(1u << m.b);
        case 'c': return (uint8_t)(1u << m.c);
        case 'd': return (uint8_t)(1u << m.d);
        case 'e': return (uint8_t)(1u << m.e);
        case 'f': return (uint8_t)(1u << m.f);
        case 'g': return (uint8_t)(1u << m.g);
        default:  return 0;
    }
}

uint8_t tm1637_dp_bit(const Tm1637SegmentMap& map) {
    return (uint8_t)(1u << map.dp);
}

uint8_t tm1637_glyph(char ch, const Tm1637SegmentMap& map) {
    /* Case-insensitive, matching lit_segments()'s str.lower(). */
    if (ch >= 'A' && ch <= 'Z') ch = (char)(ch - 'A' + 'a');
    for (int i = 0; i < kGlyphCount; i++) {
        if (kGlyphs[i].ch != ch) continue;
        uint8_t out = 0;
        for (const char* p = kGlyphs[i].segs; *p; p++) out |= seg_bit(map, *p);
        return out;
    }
    return 0x00;   /* unknown -> blank, never a broken display */
}

/* ------------------------------------------------------------------
 * Bit-banged protocol.
 *
 * Both lines are OPEN-DRAIN: low() sinks, release() floats to the 10K
 * pull-up.  Nothing here ever drives a line high (see tm1637.h).
 * ------------------------------------------------------------------ */

static inline void hold(const Tm1637& d) {
    d.bus.delay_us(d.bus.ctx, d.half_period_us);
}

/* START (p.3): "When CLK is a high level and DIO changes from high to low
 * level, data input starts."  So: both released, then DIO falls, then CLK
 * falls to open the first bit window. */
static void start_cond(const Tm1637& d) {
    d.bus.clk_release(d.bus.ctx);
    d.bus.dio_release(d.bus.ctx);
    hold(d);
    d.bus.dio_low(d.bus.ctx);      /* <-- the start: DIO falls, CLK high */
    hold(d);
    d.bus.clk_low(d.bus.ctx);
    hold(d);
}

/* STOP (p.3): "When CLK is a high level and DIO changes from low to high
 * level, data input ends."  DIO must be LOW before CLK is released, or
 * the rising DIO edge happens with no preceding low and no stop is seen. */
static void stop_cond(const Tm1637& d) {
    d.bus.clk_low(d.bus.ctx);
    d.bus.dio_low(d.bus.ctx);
    hold(d);
    d.bus.clk_release(d.bus.ctx);
    hold(d);
    d.bus.dio_release(d.bus.ctx);  /* <-- the stop: DIO rises, CLK high  */
    hold(d);
}

/* Write one byte, LSB first, and sample the ACK.
 *
 * p.2 (DIO): "The input data has a low level fluctuation while high level
 * transfer at SCLK" — i.e. DIO changes while CLK is LOW and is stable
 * while CLK is HIGH.  p.2 (CLK): data is latched on the RISING edge.
 *
 * p.3 (ACK): "an answering signal ACK is generated inside the chip to
 * lower the DIO pin at the falling edge of the 8th clock.  DIO interface
 * wire is released at the end of the 9th clock."  So the master releases
 * DIO after the 8th bit, the part drives it low on the 8th FALLING edge,
 * and the master samples before clocking the 9th pulse. */
static bool write_byte(const Tm1637& d, uint8_t byte) {
    for (int i = 0; i < 8; i++) {
        d.bus.clk_low(d.bus.ctx);                 /* open the bit window  */
        if (byte & 0x01) d.bus.dio_release(d.bus.ctx);
        else             d.bus.dio_low(d.bus.ctx);
        hold(d);                                  /* tSETUP (>=100 ns)    */
        d.bus.clk_release(d.bus.ctx);             /* rising edge: latch   */
        hold(d);                                  /* tWAIT (>=1 us)       */
        byte >>= 1;
    }
    /* 8th falling edge — the part asserts ACK on DIO here. */
    d.bus.clk_low(d.bus.ctx);
    d.bus.dio_release(d.bus.ctx);                 /* master off the line  */
    hold(d);
    const bool acked = (d.bus.dio_read(d.bus.ctx) == false);
    /* The 9th clock, at whose end the part releases DIO. */
    d.bus.clk_release(d.bus.ctx);
    hold(d);
    d.bus.clk_low(d.bus.ctx);
    hold(d);
    return acked;
}

/* Read one byte, LSB first (key scan, p.3 diagram 1).  The master holds
 * DIO released throughout; the part drives it. */
static uint8_t read_byte(const Tm1637& d) {
    uint8_t v = 0;
    d.bus.dio_release(d.bus.ctx);
    for (int i = 0; i < 8; i++) {
        d.bus.clk_low(d.bus.ctx);
        hold(d);
        d.bus.clk_release(d.bus.ctx);
        if (d.bus.dio_read(d.bus.ctx)) v |= (uint8_t)(1u << i);
        hold(d);
    }
    /* The master's ACK back to the part. */
    d.bus.clk_low(d.bus.ctx);
    d.bus.dio_low(d.bus.ctx);
    hold(d);
    d.bus.clk_release(d.bus.ctx);
    hold(d);
    d.bus.clk_low(d.bus.ctx);
    hold(d);
    return v;
}

/* One command byte in its own start/stop bracket (p.4 diagrams). */
static bool cmd(const Tm1637& d, uint8_t c) {
    start_cond(d);
    const bool ok = write_byte(d, c);
    stop_cond(d);
    return ok;
}

/* ------------------------------------------------------------------
 * Public API
 * ------------------------------------------------------------------ */

void tm1637_init(Tm1637& d, const Tm1637Bus& bus, uint8_t digits,
                 uint32_t half_period_us) {
    d.bus = bus;
    d.digits = digits;
    if (d.digits < 1) d.digits = 1;
    if (d.digits > TM1637_MAX_DIGITS) d.digits = TM1637_MAX_DIGITS;
    d.timing_was_clamped = (half_period_us < TM1637_HALF_PERIOD_US_MIN);
    d.half_period_us = d.timing_was_clamped ? TM1637_HALF_PERIOD_US_MIN
                                            : half_period_us;
    d.brightness = TM1637_BRIGHTNESS_MAX;
    d.on = true;
    d.map = kTm1637SegmentMapDefault;
}

bool tm1637_set_brightness(Tm1637& d, uint8_t brightness) {
    if (brightness > TM1637_BRIGHTNESS_MAX) brightness = TM1637_BRIGHTNESS_MAX;
    d.brightness = brightness;
    return cmd(d, (uint8_t)(TM1637_CMD_DISPLAY_BASE | d.brightness |
                            (d.on ? TM1637_DISPLAY_ON_BIT : 0u)));
}

bool tm1637_set_on(Tm1637& d, bool on) {
    d.on = on;
    return cmd(d, (uint8_t)(TM1637_CMD_DISPLAY_BASE | d.brightness |
                            (d.on ? TM1637_DISPLAY_ON_BIT : 0u)));
}

/* Auto-increment write (p.4 diagram 2): [start]Cmd1[stop]
 * [start]Cmd2 Data1..DataN[stop] [start]Cmd3[stop].
 *
 * Cmd1 and Cmd3 get their OWN brackets; the address command and the data
 * run share ONE bracket.  That grouping is not cosmetic — the address
 * auto-increments only within a single start..stop, so splitting the data
 * run would rewrite digit 0 N times. */
bool tm1637_write_segments(Tm1637& d, uint8_t first, const uint8_t* seg,
                           uint8_t count) {
    if (first >= TM1637_MAX_DIGITS || count == 0) return false;
    if ((uint8_t)(first + count) > TM1637_MAX_DIGITS) return false;

    bool ok = cmd(d, TM1637_CMD_DATA_WRITE_AUTOINC);

    start_cond(d);
    ok = write_byte(d, (uint8_t)(TM1637_CMD_ADDR_BASE | first)) && ok;
    for (uint8_t i = 0; i < count; i++) {
        ok = write_byte(d, seg[i]) && ok;
    }
    stop_cond(d);

    ok = cmd(d, (uint8_t)(TM1637_CMD_DISPLAY_BASE | d.brightness |
                          (d.on ? TM1637_DISPLAY_ON_BIT : 0u))) && ok;
    return ok;
}

bool tm1637_clear(Tm1637& d) {
    uint8_t blank[TM1637_MAX_DIGITS];
    memset(blank, 0, sizeof(blank));
    return tm1637_write_segments(d, 0, blank, d.digits);
}

bool tm1637_read_keys(Tm1637& d, uint8_t& out) {
    /* p.3: "the clock frequency should be less than 250K" for key reads.
     * Enforced, not commented: the read runs at a temporarily slower
     * half-period if the configured one is too fast for this path. */
    const uint32_t saved = d.half_period_us;
    if (d.half_period_us < TM1637_HALF_PERIOD_US_MIN_KEYREAD) {
        d.half_period_us = TM1637_HALF_PERIOD_US_MIN_KEYREAD;
    }
    start_cond(d);
    const bool ok = write_byte(d, TM1637_CMD_DATA_READ_KEYS);
    out = read_byte(d);
    stop_cond(d);
    d.half_period_us = saved;
    return ok;
}

/* ------------------------------------------------------------------
 * Formatter — the BACKEND's concern (see tm1637.h for the policy and why
 * it deliberately matches the raster widget's).
 * ------------------------------------------------------------------ */

int tm1637_format_string(const Tm1637& d, const char* value,
                         const Tm1637Format& fmt, uint8_t* out) {
    for (uint8_t i = 0; i < d.digits; i++) out[i] = 0x00;
    if (value == nullptr) return 0;

    /* Pass 1: split into (glyph char, dp?) cells.  '.' does NOT take a
     * cell — it folds into the PRECEDING cell's dp bit, because a tube
     * has no cell to give it.  A leading '.' (no preceding digit) folds
     * into a blank cell rather than being dropped, so ".5" still shows a
     * point. */
    char cells[TM1637_MAX_DIGITS * 4];
    bool  dots[TM1637_MAX_DIGITS * 4];
    int   n = 0;
    const int cap = (int)(sizeof(cells) / sizeof(cells[0]));
    int consumed = 0;

    for (const char* p = value; *p && n < cap; p++) {
        consumed++;
        if (*p == '.') {
            if (n == 0) { cells[n] = ' '; dots[n] = true; n++; }
            else        { dots[n - 1] = true; }
            continue;
        }
        if (*p == ':') {
            /* The colon is ONE bit on a module that has one, and there is
             * no portable answer for where.  Folding it into the
             * preceding digit's dp is the common clock-module wiring; a
             * module that wires it elsewhere needs a board override.
             * Flagged rather than guessed silently. */
            if (n > 0) dots[n - 1] = true;
            continue;
        }
        cells[n] = *p;
        dots[n] = false;
        n++;
    }

    /* Pass 2: right-justify into d.digits, keeping the RIGHTMOST cells,
     * padding LEFT with blanks (or '0' when pad_zero).  Identical to
     * seven_segment._display_value(). */
    const int digits = (int)d.digits;
    const int first_src = (n > digits) ? (n - digits) : 0;
    const int pad = (n < digits) ? (digits - n) : 0;

    for (int i = 0; i < pad; i++) {
        out[i] = fmt.pad_zero ? tm1637_glyph('0', d.map) : 0x00;
    }
    for (int i = 0; i + first_src < n && (pad + i) < digits; i++) {
        uint8_t byte = tm1637_glyph(cells[first_src + i], d.map);
        if (dots[first_src + i]) byte |= tm1637_dp_bit(d.map);
        out[pad + i] = byte;
    }
    return consumed;
}

int tm1637_format_number(const Tm1637& d, double value,
                         const Tm1637Format& fmt, uint8_t* out) {
    char buf[32];
    if (fmt.decimals < 0) {
        /* Integer render.  Truncation toward zero, not rounding: a gauge
         * reading 99.7 shown as "100" on a 3-digit surface would be an
         * over-range the operator cannot see. */
        long long iv = (long long)value;
        snprintf(buf, sizeof(buf), "%lld", iv);
    } else {
        snprintf(buf, sizeof(buf), "%.*f", (int)fmt.decimals, value);
    }
    return tm1637_format_string(d, buf, fmt, out);
}

/* ------------------------------------------------------------------
 * Bus binding
 * ------------------------------------------------------------------ */

static void copy_ns(char* dst, size_t cap, const char* src) {
    if (src == nullptr) { dst[0] = '\0'; return; }
    size_t i = 0;
    for (; src[i] && i + 1 < cap; i++) dst[i] = src[i];
    dst[i] = '\0';
}

void tm1637_bind(Tm1637Binding& b, Tm1637& dev, const char* value_ns,
                 const char* brightness_ns, const char* power_ns,
                 const Tm1637Format& fmt) {
    b.dev = &dev;
    copy_ns(b.value_ns, sizeof(b.value_ns), value_ns);
    copy_ns(b.brightness_ns, sizeof(b.brightness_ns), brightness_ns);
    copy_ns(b.power_ns, sizeof(b.power_ns), power_ns);
    b.fmt = fmt;
}

size_t tm1637_namespaces(const Tm1637Binding& b, const char** out,
                         size_t cap) {
    size_t n = 0;
    if (b.value_ns[0] && n < cap)      out[n++] = b.value_ns;
    if (b.brightness_ns[0] && n < cap) out[n++] = b.brightness_ns;
    if (b.power_ns[0] && n < cap)      out[n++] = b.power_ns;
    return n;
}

bool tm1637_apply(Tm1637Binding& b, const char* ns, int kind, double num,
                  bool boolean, const char* str) {
    if (b.dev == nullptr || ns == nullptr) return false;

    if (b.brightness_ns[0] && strcmp(ns, b.brightness_ns) == 0) {
        /* A bus brightness is semantic 0..1 or 0..7; both are in use in
         * the fleet, so the rule is explicit rather than sniffed: a value
         * in [0,1] is a FRACTION, anything above is a raw step.  The
         * TM1637's eight steps are NOT linear in perceived brightness
         * (p.5: 1,2,4,10,11,12,13,14 /16), so a fraction maps to the step
         * index, not to a duty cycle. */
        double v = (kind == 1) ? (boolean ? 1.0 : 0.0) : num;
        uint8_t step;
        if (v <= 1.0) {
            if (v < 0.0) v = 0.0;
            step = (uint8_t)(v * TM1637_BRIGHTNESS_MAX + 0.5);
        } else {
            step = (uint8_t)(v > TM1637_BRIGHTNESS_MAX ? TM1637_BRIGHTNESS_MAX
                                                       : v);
        }
        return tm1637_set_brightness(*b.dev, step);
    }

    if (b.power_ns[0] && strcmp(ns, b.power_ns) == 0) {
        bool on;
        if (kind == 1)      on = boolean;
        else if (kind == 0) on = (num != 0.0);
        else                on = (str && str[0] && strcmp(str, "0") != 0 &&
                                  strcmp(str, "off") != 0 &&
                                  strcmp(str, "false") != 0);
        return tm1637_set_on(*b.dev, on);
    }

    if (b.value_ns[0] && strcmp(ns, b.value_ns) == 0) {
        uint8_t seg[TM1637_MAX_DIGITS];
        if (kind == 2 && str != nullptr) {
            tm1637_format_string(*b.dev, str, b.fmt, seg);
        } else if (kind == 1) {
            tm1637_format_string(*b.dev, boolean ? "1" : "0", b.fmt, seg);
        } else {
            tm1637_format_number(*b.dev, num, b.fmt, seg);
        }
        return tm1637_write_segments(*b.dev, 0, seg, b.dev->digits);
    }

    return false;
}
