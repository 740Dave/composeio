/*
 * mcp23017.h — the MCP23017 as a PORT PROVIDER (a pin multiplier), not a
 * sensor.  [PCB-GAUGE3] stress S2, [SPEC:CXC-HAL-DECLARE].
 *
 * The part appears on three of the fleet's four PCBs and has no physical
 * quantity of its own: it re-exposes 16 pins behind one I2C address.  This
 * driver therefore has no namespace and publishes nothing — it is a bus
 * onto which ordinary point-to-point kinds hang.
 *
 * ESP-IDF only (i2c_master).  The pure-C++ half of this board's logic is in
 * x27168.cpp; keep it that way.
 */

#ifndef MCP23017_H
#define MCP23017_H

#include <cstdint>

/*
 * ⚠ THE BOARD OFFERS NO WAY TO RESET THIS PART.  U1 pin 18 (RESET) is tied
 * hard to the supply rail, and R_RST is a resistor with BOTH ENDS on the
 * same net (generate_pcb.py: R_RST_NETS = {1: 4, 2: 4}) — a decorative
 * pull-up across a hard tie.  So a wedged expander can only be cleared by a
 * power cycle, which on a rack-mounted board means physical access.
 *
 * mcp23017_bus_recover() is the software half of the answer: nine SCL
 * clocks bit-banged BEFORE the I2C driver is installed, which releases a
 * slave that is stuck mid-byte holding SDA low.  It cannot fix a wedged
 * MASTER and it cannot fix a latched-up expander — say so, do not sell it
 * as a reset.
 */
bool mcp23017_bus_recover(int sda_gpio, int scl_gpio);

/* Install the I2C master and configure the expander for this board:
 * IOCON=0x00 (BANK 0, address auto-increment ON — that is what makes one
 * 4-byte transaction drive all twelve coil lines), then OLAT=0 and only
 * THEN IODIR.  Returns false and logs loudly on any NAK. */
bool mcp23017_init(int sda_gpio, int scl_gpio, uint32_t hz, uint8_t addr,
                   uint8_t iodir_a, uint8_t iodir_b);

/* Write GPIOA and GPIOB in ONE transaction (addr, 0x12, a, b). */
bool mcp23017_write_ports(uint8_t gpioa, uint8_t gpiob);

/* Read IODIRA/IODIRB back.  THE NEGATIVE CONTROL for init: it is the one
 * check on this board that does not read a value back through the same
 * assumption that wrote it — a NAK, a wrong address, a half-powered part
 * and a floating SDA all produce a readback that DISAGREES, whereas
 * "i2c_master_transmit returned ESP_OK" produces the same OK for a device
 * that is not there if anything on the bus happens to ACK. */
bool mcp23017_read_iodir(uint8_t* iodir_a, uint8_t* iodir_b);

/* Total NAK/timeout count since init — fed into the board's status line so
 * an intermittent bus is visible rather than merely survived. */
uint32_t mcp23017_error_count();

#endif /* MCP23017_H */
