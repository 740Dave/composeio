/*
 * controller_config.h — the headless-controller device model, PURE C++.
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-DEVICE] [CXC-ER-BOARDS]
 *
 * The config-driven replacement for a BAKED board profile.  The generic
 * reactive_core firmware fetches its .compose.yaml from bus_storage at boot
 * ([CXC-R-BOOT], reusing cyd/src/config_fetch by reference) and lowers the
 * SAME file's declarative blocks into this runtime device model:
 *
 *   hookup: wires:      each wire with `publishes:` + a `to: mcu.GPIOn` sink
 *                       -> a SensorSpec (ADC pin -> published slot ns).  The
 *                       slot's state_engine (from `namespaces:`) sets the
 *                       output scale (continuous_0_255 -> 0..255).
 *   audio_cover:        the DY-SV5F actuator block -> AudioCoverConfig (UART
 *                       tx/busy pins, subscribed namespaces, cue -> track).
 *
 * No pins are baked in firmware — the fetched compose.yaml IS the board.
 * Parsing uses cyd/src/yaml_min (the SAME bounded parser the display tier
 * uses, compiled by reference), and all strings are COPIED into std::string
 * so the ControllerConfig outlives the transient YamlDoc arena.  Pure C++ so
 * it host-compiles + host-tests in env:native_test against the real
 * dual_throttle.compose.yaml structure.
 */

#ifndef CONTROLLER_CONFIG_H
#define CONTROLLER_CONFIG_H

#include <string>
#include <vector>

struct YamlNode;   /* cyd/src/yaml_min.h */

/* One ADC-input sensor: read GPIO `pin`, map adc 0..adc_max -> 0..out_max,
 * publish the rounded value on `ns` at `sample_hz`. */
struct SensorSpec {
    std::string ns;                 /* published slot namespace           */
    int pin = -1;                   /* MCU ADC GPIO number                */
    int adc_max = 4095;             /* full-scale ADC reading (12-bit)    */
    int out_max = 255;              /* engine scale (continuous_0_255)    */
    int sample_hz = 20;
    std::string state_engine;       /* informational (drives out_max)     */
    /* --- leaf-owned signal conditioning (config-driven; P1b) ------------ */
    bool invert = false;            /* flip: raw -> adc_max - raw (any pot) */
    int deadband = 2;               /* default denoiser: re-emit only when  */
                                    /*   |val - last_emitted| >= deadband   */
    float ema_alpha = 0.0f;         /* 0 = off; else EMA on raw ADC:        */
                                    /*   a*raw + (1-a)*state (smooth glide)  */
    /* --- OUTPUT-SCALE QUANTIZATION (config-driven) ---------------------- */
    /* When step > 0, snap the conditioned output to the nearest multiple of
     * `step` (in OUT-SCALE codes: 0..out_max) so a pot emits coarse LEVELS
     * (e.g. step=10 -> 0,10,20,...) instead of the full ramp.  With the
     * existing emit-on-change gate this makes the wire publish ONLY on a
     * LEVEL change — cutting bus volume for every consumer.  Snapping uses
     * boundary HYSTERESIS (margin = `deadband`) so ADC noise near a level
     * boundary does not chatter, and RAILS (0 / out_max) escape the snap so
     * they stay exactly reachable — see signal_conditioning.cpp RULE 4.
     * ⛔ DEFAULT 0 = DISABLED (no quantize): every existing config that does
     * NOT declare `step` is byte-identical in behaviour. */
    int step = 0;                   /* 0 = off; else out-scale level size    */
    /* --- CALIBRATION / SAFETY BANDS, in RAW ADC CODES -------------------- */
    /* The INPUT half of the SAME clamped linear map the OUTPUT half already
     * runs (actuator_hal.cpp `_map_scaled`: in_min/in_max -> 0..out_max).
     * Before these existed the input path hardcoded in_min = 0 and used
     * adc_max as in_max, which is why the two paths were DOCUMENTED as "the
     * same clamped linear map" (mcu_config.py) while only one of them had
     * the vocabulary to be one.
     *
     * `in_min` is the raw code AT AND BELOW WHICH the output is 0; `in_max`
     * is the raw code AT AND ABOVE WHICH the output is out_max.  They are
     * the SAFETY BANDS: an end band wide enough to swallow pot end-
     * resistance and ADC zero-offset makes both rails reachable BY
     * CONSTRUCTION rather than by arithmetic luck.
     *
     * ⛔ THE BANDS ARE NOT DEAD RANGE — DO NOT "OPTIMIZE" THEM AWAY.
     * [CXC-HAL-CONDITION-RAILS] is satisfied at the arithmetic level only up
     * to ~8 codes of ADC zero-offset (the host test's boundary report prints
     * the measured figure).  Past that, NO rounding scheme can reach the
     * far rail, because the preimage of the rail is a code the ADC never
     * produces.  The band is the structural fix for that residual: it moves
     * rail-reachability from "the arithmetic is unbiased" to "the rail owns
     * a whole neighbourhood of codes", which no ADC offset inside the band
     * can defeat.  Deleting the band re-opens a defect that presents as a
     * throttle that will not reach full.
     *
     * 0 / 0 means "unset": in_min = 0, in_max = adc_max — EXACTLY today's
     * behaviour, so a wire that declares neither is byte-identical. */
    int in_min = 0;                 /* raw code <= this -> 0                */
    int in_max = 0;                 /* raw code >= this -> out_max (0=adc_max) */
};

/* One row of a relay bank's value->pin state map: the enum `value` energizes
 * relay GPIO `pin` (pin == -1 for a pin-less state, e.g. "off" = all open). */
struct RelayState {
    std::string value;              /* enum value: off | low | medium | ...  */
    int pin = -1;                   /* energized MCU GPIO (-1 = none/off)     */
};

/* A break-before-make relay-bank actuator ([CXC-HAL-ACTUATE] Output Tier 1):
 * subscribe one SEMANTIC ENUM namespace, drive a set of relay pins through a
 * value->pin map, NEVER two energized at once (dead_time_ms between break and
 * make).  The multi-pin dual of a single binary actuator. */
struct RelayActuatorSpec {
    bool present = false;
    std::string ns;                 /* subscribed enum namespace              */
    std::vector<int> pins;          /* ALL owned relay GPIOs (break de-energizes
                                     *   every one — includes any spare)      */
    std::vector<RelayState> states; /* enum value -> energized pin            */
    bool active_high = true;        /* relay energizes on GPIO HIGH           */
    int dead_time_ms = 80;          /* break-before-make dead-time            */
    std::string default_value = "off"; /* state asserted at boot              */
};

/* The DY-SV5F audio cover config (a pure bus SUBSCRIBER + UART actuator). */
struct AudioCoverConfig {
    bool present = false;
    int tx_pin = -1;                /* C3 UART TX -> DY-SV5F RXD          */
    int busy_pin = -1;              /* DY-SV5F BUSY -> C3 (optional, -1)  */
    int baud = 9600;
    std::vector<std::string> subscribes;   /* namespaces to subscribe    */
    std::string contacts_ns;        /* the contact-identify source ns    */
    int condition_one_track = 0;    /* 0 = cue absent                    */
    std::string jump_ns;            /* the FTL-jump source ns            */
    int jump_track = 0;             /* 0 = cue absent                    */
};

struct ControllerConfig {
    std::vector<SensorSpec> sensors;
    RelayActuatorSpec relay;        /* break-before-make relay bank       */
    AudioCoverConfig audio;
    std::string error;              /* non-empty on parse/shape failure  */

    bool ok() const { return error.empty(); }
};

/* Lower a parsed .compose.yaml (yaml_min root) into the device model.
 * Never throws; shape problems land in cfg.error (and leave partial data). */
ControllerConfig controller_config_from_yaml(const YamlNode* root);

/* Convenience: parse `text` (len bytes) with yaml_min, then lower it.
 * On a YAML parse failure, cfg.error carries the parser message. */
ControllerConfig controller_config_parse(const char* text, size_t len);

/* Extract a GPIO number from a pin token: "mcu.GPIO0" -> 0, "GPIO5" -> 5,
 * "5" -> 5.  Returns -1 when no number is present. */
int controller_parse_gpio(const char* token);

#endif /* CONTROLLER_CONFIG_H */
