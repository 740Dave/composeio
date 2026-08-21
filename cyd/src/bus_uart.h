/*
 * bus_uart.h — the UART implementation of the ComposeBus transport seam.
 * [SPEC:CXC-BOARD-HW] [CXC-BOARD-HW-UART-SLAVE] [BP-TRANSPORT-UART]
 * DP-CIO-6.
 *
 * WHY THIS EXISTS.  David (2026-08-12): increasingly intends to turn the
 * ESP32's WiFi OFF and slave display boards over UART to an upstream node
 * that carries the ComposeBus.  The driver is address exhaustion — panels
 * now arrive faster than subnet space, and every one consumes a DHCP
 * lease, an MQTT client slot and an HTTP server just to render a scene.
 * "Is UART brought out?" became a board-SELECTION question because of it.
 *
 * The frame is the one already spoken by python/composeio/transport_uart.py
 * and by firmware/ftl_jump_button/src/main.cpp on a CH32V003; the codec is
 * bus_frame.h and is host-parity-tested against the Python encoder
 * (env:native_frame).  This file is only the ESP-IDF driver around it:
 * a UART port, an rx task, and the same queue-to-the-render-task shape
 * bus_mqtt.cpp has always had.
 *
 * WHAT IS NOT DONE, AND MUST NOT BE ASSUMED (2026-08-12):
 *   - There is NO upstream ComposeBus host on the other end of either
 *     bench board's UART.  This transport is verified by host loopback of
 *     its codec ONLY.  It has never carried a live scene.
 *   - `config_fetch` (scene YAML over HTTP) and `capture_http`
 *     (verification over HTTP) are still WiFi.  A genuinely UART-slaved
 *     board needs an answer for both, and that answer depends on an
 *     UNMADE decision about what the upstream host does — proxy HTTP, or
 *     serve config over the frame protocol.  Out of scope on purpose.
 *   - Nothing announces this device's SUBSCRIPTION SET upstream.  On MQTT
 *     the broker filters; on a point-to-point link the upstream must
 *     already know what to send, or send everything.  Open protocol
 *     question, deliberately not invented here.
 */

#ifndef BUS_UART_H
#define BUS_UART_H

#include "bus_transport.h"
#include "bus_raw.h"

/* The UART transport singleton.  Constructing it does NOT claim pins or
 * install a driver — start() does, and start() is only ever called on the
 * SELECTED transport, so an MQTT boot never touches the UART. */
BusTransport& bus_uart_transport();

/* Persist the link's WIRING for the next boot, the MCU analogue of
 * transport_registry.py's `_uart(spec)` reading `port` / `baudrate`: a rig
 * is re-pointed by command, never by rebuild.  A NEGATIVE value leaves
 * that field alone, so `baudrate` alone can be changed without restating
 * the pin map.  Stored in NVS namespace "composeio" as uart.port /
 * uart.tx / uart.rx / uart.baudrate. */
bool bus_uart_set_config(int32_t port, int32_t tx, int32_t rx,
                         int32_t baudrate);

/* ---- relay hooks ([CXC-CONFIG-BUS-RELAY]) ----
 *
 * Every decoded frame, BEFORE namespace resolution and without needing one —
 * because the wire carries a HASH, and a relay forwards for devices it has
 * never heard of.  See the comment on the sink in bus_uart.cpp for why this
 * cannot be expressed as a subscription. */
void bus_uart_set_raw_sink(BusRawFrameFn fn, void* ud);
bool bus_uart_send_raw(uint16_t ns_hash, BusMessageType type,
                       uint8_t scope_bits, const uint8_t* value, size_t len);

#endif /* BUS_UART_H */
