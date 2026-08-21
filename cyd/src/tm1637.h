/*
 * tm1637.h — TM1637 4/6-digit LED driver: the FIRST NON-RASTER BACKEND.
 * [SPEC:CXC-VISION] [CXC-VISION-SEMANTIC-REALIZATION] [CXC-VISION-EPISTEMICS]
 * PLAN item I3.  Titan Micro TM1637 datasheet V2.4 is the ONLY authority
 * cited below; every page reference is to that document.
 *
 * WHAT THIS IS.  One semantic value has many physical realizations
 * ([CXC-VISION-SEMANTIC-REALIZATION]).  `throttle/left` already drives an
 * SVG seven-segment readout on a raster panel
 * (python/composeio/render/widgets/seven_segment.py).  This is the SAME
 * semantic widget realized on silicon that has no pixels: four digits,
 * seven segments, one 8-bit register per digit.  The WIDGET is semantic;
 * this file is a REALIZATION.  Nothing above the widget changes.
 *
 * WHY IT IS PURE C++ WITH INJECTED PINS.  No ESP-IDF include, no GPIO
 * number, no delay implementation — the same seam idiom as restart.h.  A
 * bit-banged protocol is exactly the class of code whose defects are
 * invisible on a bench (a wrong bit order still produces well-formed
 * glyphs) so it MUST be assertable on the host, waveform by waveform,
 * against the datasheet.  See tm1637_selftest.cpp.
 *
 * ============ THE TWO-WIRE INTERFACE IS NOT I2C ============
 * Datasheet p.3, "Interface interpretation", verbatim:
 *
 *   "Microprocessor data realize the communication with TM1637 by means
 *    of two-wire bus interface (Note: The communication method is not
 *    equal to I2C bus protocol totally BECAUSE THERE IS NO SLAVE
 *    ADDRESS)."
 *
 * There is NO device address anywhere in the command set.  The only
 * "address" the part has is the DISPLAY REGISTER address C0H..C5H (p.5),
 * which selects a DIGIT within one chip, not a chip on a bus.  Two parts
 * sharing both CLK and DIO therefore both latch every command and show
 * IDENTICAL content — a legitimate topology if mirrored displays are what
 * you want, and the ONLY topology those two shared wires can express.
 *
 * MULTI-DISPLAY: shared CLK, one DIO per display (N displays = N+1 pins).
 * The load-bearing property, from p.3:
 *
 *   "When CLK is a high level and DIO changes from high to low level,
 *    data input STARTS."
 *
 * Data input begins on a DIO EDGE, gated on CLK high.  A display whose
 * DIO never moves is never armed, so clock traffic addressed to a sibling
 * is not data to it.  Two independent facts make that safe rather than
 * merely likely:
 *
 *   1. Display refresh runs off the INTERNAL RC OSCILLATOR (fosc typ
 *      450 kHz, p.10), NOT off CLK.  An unaddressed part keeps
 *      multiplexing its digits from its own SRAM no matter what CLK does.
 *      There is no clock-starvation state.
 *   2. Every transaction is bracketed by a START on that part's OWN DIO,
 *      and p.4 states a STOP "initializes serial communication".  Even
 *      under the pessimistic assumption that some die counts clock edges
 *      without a start, each transaction RESYNCHRONIZES the part.  The
 *      damage from a phantom bit is bounded by one transaction.
 *
 * ⚠ THE DRIVER'S OBLIGATION THAT MAKES THIS TRUE: a non-selected DIO must
 * be PARKED HIGH and never touched.  Pulling an idle display's DIO low
 * while CLK is high IS a start condition — it would arm it.  Selection is
 * therefore "drive exactly one DIO, leave the rest released", which
 * tm1637_select() enforces structurally by owning one pin set at a time.
 *
 * ⛔ WHAT THE DATASHEET DOES NOT SAY.  There is no sentence reading "a
 * part with a static DIO ignores CLK", and there is no state diagram.
 * The claim above is a STRONG INFERENCE from the stated start condition,
 * not a verbatim guarantee.  It has not been checked on a part.  If it is
 * ever falsified, the fallback is SHARED DIO + one CLK per display, which
 * is mechanically stronger — a part whose CLK is parked LOW cannot latch
 * a bit at all, because latching is defined on the CLK RISING edge (p.2,
 * CLK: "used for data input and output at rising edge") and no rising
 * edge exists.  Recorded so the fallback is not re-derived under pressure.
 *
 * ============ ELECTRICAL: BOTH LINES ARE OPEN-DRAIN ============
 * The seams below express "drive low" and "RELEASE", never "drive high".
 * That is not stylistic:
 *   - The chip drives DIO low itself to ACK (p.3, 8th falling edge).  A
 *     master actively driving DIO high during the ACK window is a supply
 *     short through two output stages.
 *   - The reference network (p.8) is a 10K pull-up to VCC on each line,
 *     and VCC is 5 V — p.8 note 3: "Since blue-ray nixie tube break over
 *     step-down voltage is 3V, TM1637 power supply should be 5V."  A
 *     3.3 V part must NEVER drive these lines high; released, they rise
 *     to 5 V, which a non-5V-tolerant GPIO must not be asked to sink.
 *     Level shifting / a series resistor is a BOARD concern, flagged in
 *     the descriptor, not something this driver can fix.
 *
 * ⚠ 3.3 V HOSTS ARE OUT OF SPEC ON PAPER.  VIH min is 0.7*VDD (p.10) =
 * 3.5 V at VDD=5V.  A 3.3 V push-pull high does not reach it.  With
 * open-drain + the module's 10K pull-up to 5 V the HIGH level is 5 V and
 * the spec is met — which is a second, independent reason the seams
 * forbid driving high.  Running VDD at 3.3 V instead makes VIH 2.31 V and
 * is in spec, at the cost of LED brightness (and p.8 note 3 says blue/
 * white tubes will not light).  This is a per-board decision.
 */

#ifndef TM1637_H
#define TM1637_H

#include <cstddef>
#include <cstdint>

/* ------------------------------------------------------------------
 * Pin seams.  Open-drain semantics: low() sinks, release() floats and
 * lets the module's 10K pull-up take the line high.  read() samples DIO.
 * ------------------------------------------------------------------ */
struct Tm1637Bus {
    void (*clk_low)(void* ctx);
    void (*clk_release)(void* ctx);
    void (*dio_low)(void* ctx);
    void (*dio_release)(void* ctx);
    bool (*dio_read)(void* ctx);
    void (*delay_us)(void* ctx, uint32_t us);
    void* ctx;
};

/* ------------------------------------------------------------------
 * Datasheet timing (p.10, "Timing character" / "Switching character").
 * These are ENFORCED IN CODE, not documented in a comment — a limit any
 * caller can exceed is not a limit.
 * ------------------------------------------------------------------ */

/* tWAIT min 1 us (CLK rising -> CLK falling), i.e. CLK HIGH >= 1 us.
 * This is the binding constraint; PWCLK (400 ns) and tSETUP/tHOLD
 * (100 ns) are all satisfied by any half-period that satisfies tWAIT.
 * Fmax is 500 kHz at 50% duty (p.10) — the same 1 us high time. */
#define TM1637_HALF_PERIOD_US_MIN 1u

/* p.3: "the clock frequency should be less than 250K" WHEN READING KEYS.
 * 250 kHz at 50% duty = 2 us half-period.  Writes may run at 1 us; key
 * reads may not.  Two different floors, so the driver carries two. */
#define TM1637_HALF_PERIOD_US_MIN_KEYREAD 2u

/* Default: 100 kHz.  Deliberately slack, not maximal.  A 4-digit refresh
 * is ~50 clocks = ~500 us at this rate, which is ~2000 full refreshes a
 * second — thousands of times more than any bus namespace will change.
 * Slack buys immunity to the shared-CLK rise time of a multi-module
 * harness (see the descriptor's capacitance analysis) for nothing. */
#define TM1637_HALF_PERIOD_US_DEFAULT 5u

/* ------------------------------------------------------------------
 * Commands (p.4 "Data command", p.5 "Address command" / "Display
 * control").  B7:B6 selects the command class: 01 data, 10 display
 * control, 11 address.
 * ------------------------------------------------------------------ */
#define TM1637_CMD_DATA_WRITE_AUTOINC 0x40u  /* write display, auto-increment */
#define TM1637_CMD_DATA_WRITE_FIXED   0x44u  /* write display, fixed address  */
#define TM1637_CMD_DATA_READ_KEYS     0x42u  /* read key scan data            */
#define TM1637_CMD_ADDR_BASE          0xC0u  /* | 0..5  -> C0H..C5H           */
#define TM1637_CMD_DISPLAY_BASE       0x80u  /* | bright(0..7) | 0x08 if ON   */
#define TM1637_DISPLAY_ON_BIT         0x08u

/* p.5: six display registers C0H..C5H drive GRID1..GRID6.  The common
 * breakout populates FOUR; the die always has six. */
#define TM1637_MAX_DIGITS 6

/* p.5: brightness is a 3-bit "extinction number" -> pulse widths
 * 1/16, 2/16, 4/16, 10/16, 11/16, 12/16, 13/16, 14/16.  Note it is NOT
 * linear and NOT 1..8/16: there is no 3/16 and no 5..9/16 step. */
#define TM1637_BRIGHTNESS_MAX 7

/* ------------------------------------------------------------------
 * Segment mapping.  ⛔ THIS IS A MODULE FACT, NOT A CHIP FACT, AND IT IS
 * UNVERIFIED ON HARDWARE.  The die exposes SEG1..SEG8; which physical
 * segment each drives is decided by the PCB traces to the tube.  The
 * near-universal breakout wiring is SEG1..SEG8 = a,b,c,d,e,f,g,dp, which
 * is what this default encodes.
 *
 * A WRONG TABLE HERE IS THE CANONICAL SILENT FAILURE ([CXC-VISION-
 * EPISTEMICS]): every transfer returns success, the host test passes, and
 * the display shows CONFIDENT, WELL-FORMED, WRONG glyphs.  There is no
 * machine signal on the wire that can distinguish a correct table from a
 * permuted one, because the only observer is a human looking at the tube.
 * ------------------------------------------------------------------ */
struct Tm1637SegmentMap {
    uint8_t a, b, c, d, e, f, g, dp;   /* bit INDEX 0..7 within the byte */
};

/* The default breakout wiring: bit0=a .. bit6=g, bit7=dp. */
extern const Tm1637SegmentMap kTm1637SegmentMapDefault;

/* Encode one character to a segment byte, via the SEMANTIC truth table
 * shared with the raster widget.  Unknown characters render BLANK (0x00),
 * matching seven_segment.lit_segments(), so the display never breaks. */
uint8_t tm1637_glyph(char ch, const Tm1637SegmentMap& map);

/* The dp/colon bit for this map, for folding punctuation into a digit. */
uint8_t tm1637_dp_bit(const Tm1637SegmentMap& map);

/* ------------------------------------------------------------------
 * Device
 * ------------------------------------------------------------------ */
struct Tm1637 {
    Tm1637Bus bus;
    uint8_t   digits;              /* populated digits, 1..6            */
    uint8_t   brightness;          /* 0..7                              */
    bool      on;
    uint32_t  half_period_us;      /* EFFECTIVE, post-clamp             */
    bool      timing_was_clamped;  /* true if the caller asked too fast */
    Tm1637SegmentMap map;
};

/* Initialize.  half_period_us is CLAMPED UP to TM1637_HALF_PERIOD_US_MIN
 * and the clamp is RECORDED in timing_was_clamped rather than performed
 * silently — a silent clamp is a limit that lies about having been hit. */
void tm1637_init(Tm1637& d, const Tm1637Bus& bus, uint8_t digits,
                 uint32_t half_period_us);

/* Display control command (p.5).  brightness 0..7, clamped. */
bool tm1637_set_brightness(Tm1637& d, uint8_t brightness);
bool tm1637_set_on(Tm1637& d, bool on);

/* Write `count` raw segment bytes starting at digit `first`, using the
 * auto-increment mode (p.4 diagram 2).  Returns false if any byte was
 * NAKed.  Segment bytes, not characters — the formatter is separate. */
bool tm1637_write_segments(Tm1637& d, uint8_t first, const uint8_t* seg,
                           uint8_t count);

/* Blank every populated digit (writes 0x00), leaving the display ON. */
bool tm1637_clear(Tm1637& d);

/* Read the key-scan byte (p.2 matrix, p.3 diagram 1).  Enforces the
 * SEPARATE 250 kHz ceiling for reads.  The common 4-digit display
 * breakout populates NO keys, so on that module this returns the
 * no-key-pressed pattern 0xFF forever — which is a correct result, not a
 * fault, and the caller must not read it as evidence the link works. */
bool tm1637_read_keys(Tm1637& d, uint8_t& out);

/* ------------------------------------------------------------------
 * FORMATTING — a BACKEND concern, stated explicitly.
 *
 * [CXC-VISION-SEMANTIC-REALIZATION]: the widget is semantic, the backend
 * is the realization.  The bus carries a VALUE; how many digits it
 * occupies, whether it is right-justified, whether the pad is a blank or
 * a zero, and where a decimal point physically goes are all properties of
 * THIS SURFACE and belong here.  A publisher that had to know it was
 * feeding a 4-digit tube would have leaked the realization back onto the
 * bus, which is the exact coupling DP-CIO-6 exists to prevent.
 *
 * THE POLICY, and it is deliberately the SAME as the raster widget's
 * (python/composeio/render/widgets/seven_segment.py::_display_value):
 *   - the semantic value is a STRING;
 *   - RIGHT-JUSTIFIED into the digit count;
 *   - short values pad LEFT with BLANKS, not zeros — a blank-padded "42"
 *     reads as forty-two, a zero-padded "0042" reads as a different
 *     quantity, and the widget contract has no field saying which was
 *     meant;
 *   - long values keep the RIGHTMOST digits (the least-significant end),
 *     because dropping the high end of an over-range reading is a visible
 *     nonsense while dropping the low end is a plausible wrong number.
 * Choosing the same policy as the raster surface is the point: the same
 * namespace must READ THE SAME on both realizations, or "one semantic
 * value, many realizations" is a slogan.
 *
 * ⚠ WHERE THE TWO REALIZATIONS GENUINELY CANNOT MATCH, and this is a
 * FINDING for the I2 decision rather than a detail:  on the raster widget
 * '.' and ':' each consume their OWN narrow CELL (_PUNCT_WEIGHT).  A
 * TM1637 has no cell to give them — a '.' must fold into the PRECEDING
 * digit's dp BIT, and ':' into whichever single bit the module wires the
 * colon to.  So the string "12.34" is FIVE cells on a panel and FOUR
 * digits on a tube.  `digits` is therefore NOT a portable count across
 * realizations, and any scene format that carries a digit count is
 * carrying a raster-shaped number.
 * ------------------------------------------------------------------ */

struct Tm1637Format {
    int8_t decimals;   /* <0: render a number as an integer; >=0: fixed  */
    bool   pad_zero;   /* false (default) = blank pad, per the policy    */
};

/* Format a semantic STRING into `digits` segment bytes, folding '.' into
 * the preceding digit's dp bit.  Writes exactly d.digits bytes to out[].
 * Returns the number of characters CONSUMED from value (diagnostic; a
 * value that did not fit is a truncation the caller may want to report).
 */
int tm1637_format_string(const Tm1637& d, const char* value,
                         const Tm1637Format& fmt, uint8_t* out);

/* Format a numeric bus value.  Number->text is done here, in the backend,
 * with the surface's digit count in hand. */
int tm1637_format_number(const Tm1637& d, double value,
                         const Tm1637Format& fmt, uint8_t* out);

/* ------------------------------------------------------------------
 * BUS BINDING — one namespace, one surface.
 *
 * Kept as a plain value-application function rather than a subscription
 * so it host-tests without a transport, and so the SAME code serves a
 * poll-drained BusEvent (bus_transport.h) and a dispatched callback.  The
 * app shell adapts; this does not name a substrate (DP-CIO-6).
 * ------------------------------------------------------------------ */
struct Tm1637Binding {
    Tm1637*      dev;
    char         value_ns[96];
    char         brightness_ns[96];   /* "" = not bound                 */
    char         power_ns[96];        /* "" = not bound                 */
    Tm1637Format fmt;
};

void tm1637_bind(Tm1637Binding& b, Tm1637& dev, const char* value_ns,
                 const char* brightness_ns, const char* power_ns,
                 const Tm1637Format& fmt);

/* Fill out[] with the namespaces this binding subscribes; returns count.
 * The caller registers each as an EXACT subscription — the restart.cpp
 * idiom, not a wildcard. */
size_t tm1637_namespaces(const Tm1637Binding& b, const char** out,
                         size_t cap);

/* Apply one arrival.  kind: 0=number, 1=bool, 2=string.  Returns true if
 * the namespace matched AND the device write succeeded.
 *
 * NOTE THE THREE CHANNELS.  A single-value binding cannot express this
 * surface: `value`, `brightness` and `power` are three independently
 * addressable actuators on ONE widget, which is the same shape as the
 * gauge cluster's {needle + illumination}.  See the I2 decision material. */
bool tm1637_apply(Tm1637Binding& b, const char* ns, int kind, double num,
                  bool boolean, const char* str);

#endif /* TM1637_H */
