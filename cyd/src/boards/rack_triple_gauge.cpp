/*
 * boards/rack_triple_gauge.cpp — the fleet's FIRST NON-RASTER REALIZATION
 * BACKEND.  [CXC-VISION-SEMANTIC-REALIZATION] [PCB-GAUGE3] [SPEC:CXC-BRINGUP]
 *
 * Three x27.168 stepper needles behind an MCP23017 -> 3x L293D, plus three
 * 12 V LED backlights on LEDC.  A DIRECT WiFi/MQTT ComposeBus peer — no
 * CAN, no mothercard, no daughtercard framing; DP-CIO-6 says transport is
 * an implementation detail and this part has a radio, so it picks the easy
 * one.
 *
 * This file is a PEER of boards/*.cpp, not a member of the same family: it
 * defines realization_backend(), not board_profile().  Everything above the
 * seam that this board shares — the transport, announce, restart, the
 * config fetch — is untouched.
 *
 * ⚠ ALL BOARD CONSTANTS ARE IN include/rack_triple_gauge_panel.h.  Retune
 * there.  There is not one magic number in this file.
 */

#ifdef CXC_RACK_TRIPLE_GAUGE_APP

#include "realization.h"
#include "rack_triple_gauge_panel.h"
#include "mcp23017.h"
#include "x27168.h"

#include <cstdio>
#include <cstring>

#include "driver/ledc.h"
#include "esp_log.h"

static const char* TAG = "gauge3";

namespace {

class RackTripleGauge final : public RealizationBackend {
public:
    bool init() {
        for (int i = 0; i < 3; i++) {
            m_[i].configure(kG3SweepHalfSteps, kG3HomeHalfSteps,
                            kG3RestOffsetHalfSteps,
                            kG3HomeHalfStepsPerSec, kG3SlewHalfStepsPerSec);
            m_[i].reset();
        }
        init_illum();

        if (!mcp23017_init(kG3SdaGpio, kG3SclGpio, kG3I2cHz, kG3McpAddr,
                           kG3IodirA, kG3IodirB)) {
            snprintf(status_, sizeof(status_),
                     "I2C FAULT — no MCP23017 at 0x%02X; needles DEAD",
                     kG3McpAddr);
            i2c_ok_ = false;
            return false;
        }
        i2c_ok_ = true;

        /* Coils off, explicitly, before anything moves.  The expander was
         * just switched from inputs to outputs and the L293D enables are
         * hard-tied high — the first thing down the wire should be a known
         * quiescent pattern, not whatever the sequencer happens to hold. */
        mcp23017_write_ports(0x00, 0x00);
        return true;
    }

    /* Start the mandatory homing sweep.  Separate from init() so the app
     * shell can order it after the banner and can re-run it on a bus
     * command without re-initialising the bus. */
    void start_homing(uint32_t now_ms) {
        ESP_LOGW(TAG, "HOMING all three needles: %d half-steps (%d deg "
                 "commanded into a %d deg range) at %d half-steps/s "
                 "= %d ms.  The needles WILL stall against the stop for "
                 "the last part of this — that is the mechanism, not a "
                 "fault.",
                 kG3HomeHalfSteps, kG3HomeHalfSteps / kG3HalfStepsPerDeg,
                 kG3SweepDegrees, kG3HomeHalfStepsPerSec, kG3HomeDurationMs);
        for (int i = 0; i < 3; i++) m_[i].start_homing(now_ms);
        homing_started_ms_ = now_ms;
    }

    /* ---- RealizationBackend ---------------------------------------- */

    const char* name() const override {
        return "rack_triple_gauge (ESP32-C3, 3x x27.168 + 3x 12V backlight)";
    }

    int instance_count() const override { return 3; }

    ChannelSupport realizes(const char* channel) const override {
        if (!channel) return ChannelSupport::Unknown;
        if (!strcmp(channel, "value")) return ChannelSupport::Realized;
        if (!strcmp(channel, "illum")) return ChannelSupport::Realized;
        return ChannelSupport::Unknown;
    }

    /* ⛔ THE GATE.  Everything about this board's correctness hangs here.
     * It is deliberately a conjunction of TWO facts and neither is
     * optional: the bus must be alive, and every needle must have homed. */
    bool ready() const override {
        if (!i2c_ok_) return false;
        for (int i = 0; i < 3; i++) if (!m_[i].homed()) return false;
        return true;
    }

    void set_channel(int instance, const char* channel,
                     float unit) override {
        if (instance < 0 || instance >= 3) return;
        if (!strcmp(channel, "value")) {
            m_[instance].set_unit(unit);
        } else if (!strcmp(channel, "illum")) {
            set_illum(instance, unit);
        }
    }

    void tick(uint32_t now_ms) override {
        bool changed = false;
        for (int i = 0; i < 3; i++) changed |= m_[i].tick(now_ms);

        /* ONE transaction for all twelve coil lines: GPA0-3 = gauge 1,
         * GPA4-7 = gauge 2, GPB0-3 = gauge 3, and the MCP23017's address
         * auto-increment carries both port bytes after one register
         * pointer.  That is why homing all three costs 5.4 s and not
         * 16.2 s, and why the I2C bus sits at ~8% at the slew rate. */
        if (changed && i2c_ok_) {
            uint8_t a = 0, b = 0;
            x27_pack_ports(m_[0], m_[1], m_[2], &a, &b);
            if (!mcp23017_write_ports(a, b)) {
                /* Rate-limited: a per-step failure on a wedged bus would
                 * be 800 ESP_LOGE per second, and console writes at
                 * 115200 would then BE the fault they report.  This
                 * fleet has already shipped that bug once (25 warnings in
                 * 60 ms on a receive path). */
                if (now_ms - last_i2c_warn_ms_ > 2000) {
                    last_i2c_warn_ms_ = now_ms;
                    ESP_LOGE(TAG, "I2C write failed (%lu errors total) — "
                             "needle positions are now FICTION until a "
                             "re-home",
                             (unsigned long)mcp23017_error_count());
                }
            }
        }

        if (!homed_announced_ && ready()) {
            homed_announced_ = true;
            ESP_LOGW(TAG, "HOMED in %lu ms.  Zero is now %d half-steps off "
                     "the mechanical stop.  ⚠ NOTHING HAS VERIFIED THAT "
                     "THE NEEDLES ACTUALLY REACHED THE STOP — no machine "
                     "signal on this board can.  Confirm all three read "
                     "ZERO ON THE DIAL before believing any value.",
                     (unsigned long)(now_ms - homing_started_ms_),
                     kG3RestOffsetHalfSteps);
        }
    }

    const char* status() const override {
        if (!i2c_ok_) return status_;
        if (!ready()) {
            snprintf(status_, sizeof(status_), "HOMING %d/%d (NOT READY)",
                     m_[0].home_steps_commanded(), kG3HomeHalfSteps);
        } else {
            snprintf(status_, sizeof(status_),
                     "ready — needles %d/%d/%d of %d (dead reckoning)",
                     m_[0].position(), m_[1].position(), m_[2].position(),
                     kG3SweepHalfSteps);
        }
        return status_;
    }

    /* ---- bench instrument ------------------------------------------ */
    /*
     * FULL-SCALE SWEEP.  Rung 7 of the ladder, and the ONE test on this
     * board that can FAIL VISIBLY: drive every needle to full scale, then
     * back to zero, then re-home.  If a needle does not reach the printed
     * full-scale mark, or does not come back to zero, the slew rate is
     * above this board's real pull-in rate and steps are being lost.
     *
     * It exists because every other signal here can only report success —
     * `ready()` is true, the I2C writes return ESP_OK, the positions count
     * perfectly.  An instrument that cannot fail is not an instrument.
     */
    void sweep_test() {
        ESP_LOGW(TAG, "SWEEP TEST: all needles to FULL SCALE, then zero. "
                 "Watch the dials — this is the negative control for the "
                 "slew rate (%d half-steps/s).", kG3SlewHalfStepsPerSec);
        for (int i = 0; i < 3; i++) m_[i].set_unit(1.0f);
        sweep_stage_ = 1;
    }
    int sweep_stage() const { return sweep_stage_; }
    void sweep_advance() {
        if (sweep_stage_ == 1) { for (int i=0;i<3;i++) m_[i].set_unit(0.0f);
                                 sweep_stage_ = 2; }
        else if (sweep_stage_ == 2) { sweep_stage_ = 0; }
    }
    bool needles_at_target() const {
        for (int i = 0; i < 3; i++)
            if (m_[i].position() != m_[i].target()) return false;
        return true;
    }

private:
    /* ---- backlights -------------------------------------------------
     *
     * v1 IS ON/OFF, HELD ON — a SCOPING decision, not the contract.  The
     * hardware gives each gauge its own transistor on its own GPIO, so the
     * second channel EXISTS in copper; only the binding is absent.  LEDC is
     * therefore configured from day one at full duty, and turning this into
     * real dimming is adding an `illum:` namespace to the scene — a change
     * of VALUE, not a change of SHAPE.  Nothing in this file, in
     * realization.h, or in the app shell has to move.
     */
    void init_illum() {
        ledc_timer_config_t t = {};
        t.speed_mode      = LEDC_LOW_SPEED_MODE;   /* the C3 has only this */
        t.duty_resolution = (ledc_timer_bit_t)kG3IllumDutyBits;
        t.timer_num       = LEDC_TIMER_0;
        t.freq_hz         = kG3IllumFreqHz;
        t.clk_cfg         = LEDC_AUTO_CLK;
        ledc_timer_config(&t);

        for (int i = 0; i < 3; i++) {
            ledc_channel_config_t c = {};
            c.gpio_num   = kG3IllumGpio[i];
            c.speed_mode = LEDC_LOW_SPEED_MODE;
            c.channel    = (ledc_channel_t)(LEDC_CHANNEL_0 + i);
            c.timer_sel  = LEDC_TIMER_0;
            c.duty       = 0;
            c.hpoint     = 0;
            ledc_channel_config(&c);
            set_illum(i, 1.0f);          /* v1: held ON */
        }
        ESP_LOGI(TAG, "backlights on GPIO %d/%d/%d via LEDC @%lu Hz, "
                 "%d-bit — held ON (v1 scope; the `illum` channel is wired "
                 "and unbound)",
                 kG3IllumGpio[0], kG3IllumGpio[1], kG3IllumGpio[2],
                 (unsigned long)kG3IllumFreqHz, kG3IllumDutyBits);
    }

    void set_illum(int i, float unit) {
        if (unit < 0.0f) unit = 0.0f;
        if (unit > 1.0f) unit = 1.0f;
        const uint32_t max = (1u << kG3IllumDutyBits) - 1u;
        const uint32_t duty = (uint32_t)(unit * (float)max + 0.5f);
        ledc_set_duty(LEDC_LOW_SPEED_MODE, (ledc_channel_t)(LEDC_CHANNEL_0 + i),
                      duty);
        ledc_update_duty(LEDC_LOW_SPEED_MODE,
                         (ledc_channel_t)(LEDC_CHANNEL_0 + i));
    }

    X27168 m_[3];
    bool i2c_ok_ = false;
    bool homed_announced_ = false;
    uint32_t homing_started_ms_ = 0;
    uint32_t last_i2c_warn_ms_ = 0;
    int sweep_stage_ = 0;
    mutable char status_[128] = "uninitialised";
};

RackTripleGauge s_backend;

}  /* namespace */

RealizationBackend& realization_backend() { return s_backend; }

/* Board-local entry points the app shell calls by name (there is exactly
 * one backend in a build, same as board_profile()). */
bool rack_triple_gauge_init()                 { return s_backend.init(); }
void rack_triple_gauge_home(uint32_t now_ms)  { s_backend.start_homing(now_ms); }
void rack_triple_gauge_sweep_test()           { s_backend.sweep_test(); }
int  rack_triple_gauge_sweep_stage()          { return s_backend.sweep_stage(); }
void rack_triple_gauge_sweep_advance()        { s_backend.sweep_advance(); }
bool rack_triple_gauge_at_target()            { return s_backend.needles_at_target(); }

#endif /* CXC_RACK_TRIPLE_GAUGE_APP */
