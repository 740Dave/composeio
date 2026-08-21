/*
 * ili9341.h — ILI9341 display driver for CYD (ESP32-2432S024)
 *
 * Direct SPI DMA transfers using ESP-IDF SPI master driver.
 * Handles CYD quirks: custom MADCTL, RGB color order, backlight PWM.
 *
 * Spec: [ED-PHASE1], [ED-HW-CYD-QUIRKS]
 */

#ifndef ILI9341_H
#define ILI9341_H

#include <cstdint>
#include "driver/spi_master.h"
#include "board_profile.h"

class ILI9341 : public DisplayDriver {
public:
    ILI9341() = default;
    ~ILI9341() override = default;

    /* Non-copyable */
    ILI9341(const ILI9341&) = delete;
    ILI9341& operator=(const ILI9341&) = delete;

    /**
     * Initialize the display: configure SPI bus, send ILI9341 init
     * sequence, set MADCTL for portrait mode, turn on backlight.
     */
    void init() override;

    /**
     * Transfer a band of scanlines from an RGB565 framebuffer to the display.
     * Uses SPI DMA for the transfer.
     *
     * @param framebuffer  Pointer to full 240x320 RGB565 framebuffer
     * @param y_start      First scanline to transfer (inclusive)
     * @param y_end        Last scanline to transfer (inclusive)
     */
    void transfer_framebuffer(const uint16_t* framebuffer,
                              uint16_t y_start, uint16_t y_end);

    /**
     * Transfer a band buffer directly to the display.
     * The buffer contains pixel data starting at y_start (no offset applied).
     *
     * @param band_buf  Pointer to band buffer (width * num_lines pixels)
     * @param y_start   First scanline on display (inclusive)
     * @param y_end     Last scanline on display (inclusive)
     */
    void transfer_band(const uint16_t* band_buf,
                       uint16_t y_start, uint16_t y_end) override;

    /**
     * Set backlight brightness (0-255).
     */
    void set_backlight(uint8_t brightness) override;

    uint16_t width() const { return 240; }
    uint16_t height() const { return 320; }

private:
    spi_device_handle_t _spi = nullptr;

    void send_command(uint8_t cmd);
    void send_data(const uint8_t* data, size_t len);
    void send_data_byte(uint8_t data);
    void set_address_window(uint16_t x0, uint16_t y0, uint16_t x1, uint16_t y1);
    void init_sequence();
    void init_backlight();
    void init_spi();
    void init_gpio();
};

#endif /* ILI9341_H */
