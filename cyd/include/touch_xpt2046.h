/*
 * touch_xpt2046.h -- XPT2046 resistive touch driver for CYD
 *
 * Spec: [CYD-TOUCH-DRIVER]
 *
 * Shares SPI2_HOST (HSPI) bus with ILI9341 display.
 * The SPI bus is already initialized by ILI9341::init_spi().
 * This driver adds the XPT2046 as a second SPI device.
 */

#ifndef TOUCH_XPT2046_H
#define TOUCH_XPT2046_H

#include <cstdint>
#include "driver/spi_master.h"

/* Pressure threshold for touch detection */
static constexpr uint16_t kTouchPressureThreshold = 200;

struct TouchReading {
    uint16_t raw_x;     /* 0-4095 */
    uint16_t raw_y;     /* 0-4095 */
    uint16_t pressure;  /* 0 = no touch */
    bool touched;       /* pressure > threshold */
};

class XPT2046 {
public:
    XPT2046() = default;
    ~XPT2046() = default;

    /* Non-copyable */
    XPT2046(const XPT2046&) = delete;
    XPT2046& operator=(const XPT2046&) = delete;

    /**
     * Add XPT2046 as second SPI device on SPI2_HOST.
     * The bus is already initialized -- only calls spi_bus_add_device.
     */
    void init();
    void init_with_cs(int cs_pin);

    /**
     * Scan candidate GPIO pins for XPT2046 CS.
     * Call with screen touched — logs which pin gets real data.
     */
    void scan_cs_pins();

    /**
     * Read touch state with median-of-3 sampling.
     * Returns raw ADC values and pressure/touched state.
     */
    TouchReading read();

private:
    spi_device_handle_t _spi = nullptr;

    /**
     * Read a single 12-bit ADC channel from XPT2046.
     * @param cmd  Command byte (0xD0=X, 0x90=Y, 0xB0=Z1, 0xC0=Z2)
     * @return 12-bit ADC value (0-4095)
     */
    uint16_t read_channel(uint8_t cmd);
};

#endif /* TOUCH_XPT2046_H */
