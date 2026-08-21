/*
 * touch_ft6336.h — FT6336G capacitive touch driver (I2C)
 *
 * Spec: [CXC-BRINGUP], [CXC-ER-BOARDS].
 *
 * The first I2C touch controller in this tree — the CYD's XPT2046 is
 * resistive and SPI, and shares the display bus.  This one owns its own
 * I2C bus (shared with an audio codec on the Hosyond S3, but nothing
 * else claims it while audio is out of scope).
 *
 * CAPACITIVE MEANS NO CALIBRATION.  The XPT2046 needed a four-corner
 * raw-ADC calibration ([CYD-TOUCH-CAL]); the FT6336G is self-calibrating
 * and reports PANEL-NATIVE PIXELS directly.  There is no cal range to
 * carry — only the orientation mapping (swap/invert), which the BOARD
 * PROFILE owns because it is a property of how the glass is mounted,
 * not of the controller.
 *
 * This driver therefore returns RAW CONTROLLER coordinates and leaves
 * the mapping to the profile — the same division of labour as
 * touch_xpt2046.h / ClassicCydTouch.
 */

#ifndef TOUCH_FT6336_H
#define TOUCH_FT6336_H

#include <cstdint>

#include "driver/i2c_master.h"

/* 7-bit I2C slave address — fixed on the FT6x36 family. */
static constexpr uint8_t kFt6336Addr = 0x38;

/* Register map (FT6336G_Register.xlsx / D-FT6336G-DataSheet-V1.0.pdf) */
static constexpr uint8_t kFt6336RegDevMode    = 0x00;
static constexpr uint8_t kFt6336RegGestureId  = 0x01;
static constexpr uint8_t kFt6336RegTdStatus   = 0x02;  /* touch count, low nibble */
static constexpr uint8_t kFt6336RegP1Xh       = 0x03;  /* burst base: XH XL YH YL W */
static constexpr uint8_t kFt6336RegThreshold  = 0x80;
static constexpr uint8_t kFt6336RegCtrl       = 0x86;
static constexpr uint8_t kFt6336RegPeriodActive = 0x88;
static constexpr uint8_t kFt6336RegChipId     = 0xA3;  /* 0x36 on FT6336 */
static constexpr uint8_t kFt6336RegFirmwareId = 0xA6;
static constexpr uint8_t kFt6336RegPanelId    = 0xA8;
static constexpr uint8_t kFt6336RegReleaseCode = 0xAF;

/* Coordinate high-byte carries the 4 MSBs plus an event flag in 7:6 */
static constexpr uint8_t kFt6336MsbMask = 0x0F;

/* Keep the controller in Active mode rather than auto-dropping to
 * Monitor when idle: a polled render loop wants a predictable report
 * rate more than it wants the microamps. */
static constexpr uint8_t kFt6336CtrlKeepActive = 0x00;

struct Ft6336Reading {
    uint16_t raw_x;     /* controller-native pixels */
    uint16_t raw_y;
    uint8_t  weight;    /* touch "pressure" proxy, 0 when not touched */
    uint8_t  points;    /* touch points reported (0..2) */
    bool     touched;
};

class FT6336 {
public:
    FT6336() = default;
    ~FT6336() = default;

    /* Non-copyable */
    FT6336(const FT6336&) = delete;
    FT6336& operator=(const FT6336&) = delete;

    /**
     * Bring up the I2C bus, hardware-reset the controller via its RESET
     * pin (active LOW), and read out its identity registers.
     *
     * Unlike the display, the touch controller DOES have a real reset
     * line, so this is a genuine pulse — not a software stand-in.
     *
     * @param sda,scl   I2C pins
     * @param rst_pin   active-LOW reset (-1 if not wired)
     * @param int_pin   active-LOW touch interrupt (-1 if not wired);
     *                  configured as input but polled, not attached
     * @param freq_hz   bus clock
     * @return false if the controller never answered on the bus
     */
    bool init(int sda, int scl, int rst_pin, int int_pin, int freq_hz);

    /** True once init() has seen the controller ACK. */
    bool present() const { return _present; }

    /** Identity read at init (0 if the read failed). */
    uint8_t chip_id() const { return _chip_id; }
    uint8_t firmware_id() const { return _firmware_id; }
    uint8_t panel_id() const { return _panel_id; }

    /**
     * One burst read of the point-1 registers.  Returns touched=false
     * (and zeroed coordinates) when no finger is down or the bus errors.
     */
    Ft6336Reading read();

private:
    i2c_master_bus_handle_t _bus = nullptr;
    i2c_master_dev_handle_t _dev = nullptr;
    bool _present = false;
    uint8_t _chip_id = 0;
    uint8_t _firmware_id = 0;
    uint8_t _panel_id = 0;

    bool read_regs(uint8_t reg, uint8_t* out, size_t len);
    bool write_reg(uint8_t reg, uint8_t val);
};

#endif /* TOUCH_FT6336_H */
