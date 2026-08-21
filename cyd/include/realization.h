/*
 * realization.h — THE NON-RASTER REALIZATION SEAM, and the CHANNEL-SET
 * contract that [CXC-VISION-SEMANTIC-REALIZATION] required to be settled
 * BEFORE the first non-raster backend shipped.
 *
 * [SPEC:CXC-VISION] [CXC-VISION-SEMANTIC-REALIZATION] [DP-CIO-6]
 * [SPEC:CXC-HAL-DECLARE] [PCB-GAUGE3] stresses S3 + S4
 *
 * ------------------------------------------------------------------
 * WHY THIS FILE EXISTS AT ALL (read this before adding to it)
 * ------------------------------------------------------------------
 * `board_profile.h` is a RASTER seam: PanelGeometry + DisplayDriver +
 * TouchDriver.  Every one of its five profiles ends in pixels.  The vision
 * pillar says:
 *
 *     ONE semantic value has MANY physical realizations.  `throttle/left`
 *     drives an LED bar on an ST7796S, a stepper needle on a gauge cluster
 *     and a 7-segment numeral on a TM1637 — SIMULTANEOUSLY, with no device
 *     aware of any other.  The WIDGET is SEMANTIC; the BACKEND is the
 *     REALIZATION.
 *
 * `rack_triple_gauge` is the second realization family.  It has no
 * geometry, no framebuffer and no touch.  The choice was:
 *
 *   (a) implement BoardProfile with a null DisplayDriver — which would drag
 *       the canvas, the band tier and the whole widget set onto a board
 *       with no glass, and would make `geometry()` a lie;
 *   (b) make RASTER and ACTUATOR peers under one channel contract.
 *
 * (b), and the deciding argument is [CXC-VISION-ANNOUNCE-BIND]'s design
 * check: a mechanism built for level N must be reusable at N+1.  A seam
 * that only fits pixels is a seam built for exactly one level.
 *
 * ------------------------------------------------------------------
 * THE CHANNEL-SET DECISION  [DD-G3-CHANNELS]
 * ------------------------------------------------------------------
 * The pillar states the design point and its deadline verbatim:
 *
 *     "A widget maps to a SET OF NAMED CHANNELS, not a single value.  This
 *      is the design point that must be settled first, because it is the
 *      one that is expensive later.  Settle the channel-set shape BEFORE
 *      the first non-raster backend ships.  Retrofitting a second channel
 *      means touching every backend, and there is currently exactly one."
 *
 * This board is the forcing case it names: three x27.168 needles AND three
 * independently-switchable 12 V backlights.  Per-gauge ILLUMINATION is an
 * actuator that belongs to a SPECIFIC widget and has NO raster counterpart
 * — on a screen, brightness is just pixel colour — so a single-value widget
 * contract has nowhere to put it.
 *
 * SETTLED, four rules:
 *
 *  R1. A widget instance owns a SET of named channels.  The names are
 *      scoped to the WIDGET KIND and are SEMANTIC, never realizational.
 *      `illum`, not `backlight` — a backlight is one way to illuminate.
 *      `value`, not `needle_position`.  The moment a channel name encodes
 *      a realization, the contract has leaked and a second backend cannot
 *      honour it.  Vocabulary for kind `gauge`:
 *
 *          value   REQUIRED   the semantic quantity
 *          illum   OPTIONAL   illumination of this instance, 0..1
 *
 *  R2. The DOMAIN belongs to the BINDING, not to the backend.  The scene
 *      says `min: 0, max: 255` (throttle) or `min: 0, max: 100` (a percent);
 *      the seam carries a UNIT float in [0,1]; the backend maps unit onto
 *      whatever it physically has — 1890 half-steps, an 8-bit duty, two
 *      7-segment digits.  THAT MAPPING IS THE WHOLE "one value, many
 *      realizations" MECHANISM.  A backend that took raw 0..255 would be
 *      silently wrong the first time it met a percent.
 *
 *  R3. A backend answers `realizes(channel)` for every name it is offered,
 *      and the answer is THREE-VALUED, not two:
 *
 *          Realized      it drives it
 *          NotApplicable it legitimately has no such actuator — a raster
 *                        gauge has no `illum`, and that is a DESIGN, not a
 *                        fault.  Silent, because warning per frame is this
 *                        fleet's "diagnostic that causes the failure it
 *                        reports" shape (25 ESP_LOGW in 60 ms).
 *          Unknown       it has never heard of this name.  LOUD, ONCE, at
 *                        BIND time.  This is a scene/firmware mismatch and
 *                        the only reason two values would not do.
 *
 *      Two values would collapse "I chose not to" and "I do not understand
 *      you" into one answer, and only the second is a defect.
 *
 *  R4. `ready()` GATES the whole backend, and it is the most important
 *      method here.  A raster backend is ready once the panel initialises.
 *      A STEPPER backend is not ready until it has HOMED, because until
 *      then its zero is fiction.  Putting the gate on the SEAM rather than
 *      inside gauge code means every future backend with a calibration
 *      phase — a servo finding its stop, a 7-segment waiting for its
 *      display RAM, an ammeter zeroing — inherits it instead of
 *      re-inventing it.  The app shell must not route a value into a
 *      backend that is not ready; see gauge_main.cpp.
 *
 * ------------------------------------------------------------------
 * WHAT THIS IS NOT
 * ------------------------------------------------------------------
 * NOT the HAL declaration.  [PCB-GAUGE3] stress S4 records that
 * [SPEC:CXC-HAL-DECLARE]'s nine kinds have no `stepper` and no
 * `motor/position` — only `motor/{speed,direction}`.  Fixing that
 * vocabulary is not this file's job and is not done here.  What this file
 * DOES do is state precisely what a later `stepper` kind must be able to
 * express, so the declaration can be written against a real consumer rather
 * than guessed:
 *
 *     kind: stepper
 *       driver:        l293d                 (an H-bridge PAIR per motor)
 *       on:            i2c{addr:0x20, driver:mcp23017}   <- S2's pin
 *                      EXPANDER as a port PROVIDER, not a sensor
 *       pins:          [in1, in2, in3, in4]  as EXPANDER pin indices
 *       sequence:      half                  (full | half | micro)
 *       travel_steps:  1890                  full-scale, in the chosen
 *                                            sequence's quantum
 *       home:
 *         method:      hard-stop             (there IS no other on x27.168)
 *         direction:   down
 *         steps:       2160                  >= travel + tolerance
 *         rate_hz:     400
 *         rest_offset: 18
 *       slew_rate_hz:  800
 *       namespace:     gauge/{n}/value       <- the ABSENT `motor/position`
 *
 * and that a `gauge` is a COMPOUND peripheral (stress S3): the needle and
 * the backlight are ONE semantic object with TWO channels, not two
 * unrelated atomic entries that happen to share an index.
 */

#ifndef REALIZATION_H
#define REALIZATION_H

#include <cstddef>
#include <cstdint>

/* ---- R3: the three-valued answer ---------------------------------- */
enum class ChannelSupport : uint8_t {
    Realized,        /* this backend drives it */
    NotApplicable,   /* no such actuator here, BY DESIGN — silent */
    Unknown,         /* never heard of it — LOUD, once, at bind time */
};

/* ---- R2: one bound channel, as the scene declared it --------------- */
struct ChannelBinding {
    const char* channel;   /* "value" | "illum" — the SEMANTIC name */
    const char* ns;        /* the ComposeBus namespace bound to it */
    float lo;              /* domain low  (scene `min:`,  default 0)   */
    float hi;              /* domain high (scene `max:`,  default 1)   */

    /* Map a raw bus value onto the unit interval the seam carries.
     * CLAMPS.  An out-of-domain value is a real event (a controller
     * publishing 300 on an 0..255 namespace) and the honest realization of
     * it is "hard against the end of the scale", not a needle driven past
     * its mechanical stop.  Clamping here rather than in each backend is
     * what stops three backends disagreeing about the same overrange. */
    float to_unit(double raw) const {
        if (hi == lo) return 0.0f;
        float u = (float)((raw - (double)lo) / (double)(hi - lo));
        return u < 0.0f ? 0.0f : (u > 1.0f ? 1.0f : u);
    }
};

/* Channel-set of ONE widget instance mapped onto ONE physical instance. */
static constexpr int kMaxChannelsPerInstance = 4;

struct InstanceBinding {
    int   instance;                                   /* physical index */
    int   count;
    ChannelBinding ch[kMaxChannelsPerInstance];
};

/* ---- the seam ------------------------------------------------------ */
class RealizationBackend {
public:
    virtual ~RealizationBackend() = default;

    /* Board/backend name for the boot banner.  A device must be able to
     * SAY what it is; a fleet is only as enumerable as its quietest node. */
    virtual const char* name() const = 0;

    /* How many physical instances (gauges, bars, digits) exist. */
    virtual int instance_count() const = 0;

    /* R3.  Asked ONCE per binding, at bind time, never per value. */
    virtual ChannelSupport realizes(const char* channel) const = 0;

    /* R4.  THE GATE.  False means "my zero is fiction, do not send me
     * values yet".  Cheap — it is read on the hot path.  It must NOT
     * default to true anywhere: an instrument that can only report success
     * is not an instrument, and a homing gate that answers `true` before it
     * has homed is precisely that. */
    virtual bool ready() const = 0;

    /* R2.  Deliver one channel value, ALREADY NORMALISED to [0,1].  Called
     * only when ready() and only for a channel that answered Realized. */
    virtual void set_channel(int instance, const char* channel,
                             float unit) = 0;

    /* Drive time-based work — one homing step, one slew step, one ramp
     * update.  Non-blocking, called every loop pass with ms since boot.
     * Anything that would block belongs behind this, not in set_channel:
     * a 5.4 s homing sweep inside a setter would stall the bus queue and
     * the announcer with it. */
    virtual void tick(uint32_t now_ms) = 0;

    /* A one-line human-readable state for the boot banner and for
     * `_device/<id>/status`.  Must be able to say something BAD — "homing
     * 812/2160", "NOT HOMED", "i2c fault" — or it is decoration. */
    virtual const char* status() const = 0;
};

/* The ONE realization backend linked into this firmware, resolved exactly
 * as board_profile() resolves the one display driver: the selected
 * boards/<name>.cpp translation unit defines it. */
RealizationBackend& realization_backend();

#endif /* REALIZATION_H */
