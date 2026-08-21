/*
 * bus_can.h — the CAN implementation of the ComposeBus transport seam.
 * [SPEC:CXC-CONFIG-BUS] [CXC-CAN-BRIDGE-MIGRATE] [BP-CAN-ID] [BP-TYPE-CODES]
 * [SPEC:CXC-CROSS-TIER-CAN] DP-CIO-6.
 *
 * THE CONTROLLER IS THE ESP32'S OWN TWAI PERIPHERAL.  An SN65HVD230 (or
 * TJA1050, or MCP2551) is a TRANSCEIVER only — it converts the TWAI
 * peripheral's TX/RX logic pins to the differential pair and nothing else.  It
 * is worth being blunt about because the fleet's binding constraint follows
 * from it: TWAI is classic CAN 2.0B, EIGHT data bytes, no CAN-FD.  Re-buying
 * transceivers changes nothing, which is why [CXC-CONFIG-BUS-BUDGET] sizes the
 * config chunk format against 8 bytes and not against UART's 252.
 *
 * WIRE LAYOUT — [BP-CAN-ID] v2, ONE layout, everywhere (see bus_can_id.h).
 *
 *     29-bit extended id:  prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
 *     data:                up to 8 bytes, value-encoded exactly as UART
 *
 * This ENDS [BP-CAN-ID-DIVERGENCE].  Until 2026-08-13 this file and
 * `services/can_mqtt_bridge` spoke `ns_hash(16)|type(4)|src(9)` while
 * `python/composeio/transport_can.py` spoke `type(4)|scope(2)|ns(16)|seq(7)`,
 * and the two disagreed about what arbitration is FOR — so they could not
 * share a physical segment.  All three now speak the layout above and
 * env:native_can_id pins it.
 *
 * SCOPE NOW CROSSES THIS SUBSTRATE.  It did not before: v1 had no scope field,
 * so an outbound frame dropped the caller's scope on the floor and every
 * inbound frame was reported as "global".  That was a declared degradation and
 * it has been retired, because it is load-bearing for the mothercard
 * architecture — an internal CAN trunk between a mothercard and its
 * daughtercards is a LOCALITY, and a relay that cannot see scope leaks all
 * local chatter upstream.
 *
 * THE SOURCE FIELD IS AN ALLOCATED NODE ID, NOT A HASH.  v1 put a 9-bit slice
 * of a hash there; on the bridge side that was CPython's SALTED str hash, so
 * the field differed between two runs of the same program.  It is now 4 bits
 * of allocated node id (1..15, 0 = unassigned), settable by NVS `can.node`, and
 * it is DIAGNOSTIC ONLY — CAN does not address nodes.
 *
 * ARBITRATION PRIORITY IS NOW DONE, AND [CXC-CONFIG-BUS-OPEN] IS ANSWERED.
 * That item said a 1,000-frame config transfer SHOULD carry a lower-priority id
 * than a throttle update, and left it unresolved because 29 bits had no spare
 * room.  The `prio` field resolves it by rule rather than by type-code
 * position: ConfigPush is tier 7 (bulk) BY TYPE, throttle/* is tier 1 (live
 * control) BY NAMESPACE, and Ack is tier 2 so it can never be starved by the
 * bulk transfer it terminates.  See bus_priority_for() in bus_wire_codes.h.
 */

#ifndef BUS_CAN_H
#define BUS_CAN_H

#include "bus_transport.h"
#include "bus_raw.h"

/* The CAN transport singleton.  Constructing it claims no pins and installs no
 * driver — start() does, and start() is only ever called on the SELECTED
 * transport, so a UART boot never touches TWAI. */
BusTransport& bus_can_transport(void);

/* Persist the link's WIRING for the next boot, the same shape as
 * bus_uart_set_config: a rig is re-pointed by command, never by rebuild.  A
 * NEGATIVE value leaves that field alone.  Stored in NVS namespace "composeio"
 * as can.tx / can.rx / can.bitrate. */
bool bus_can_set_config(int32_t tx, int32_t rx, int32_t bitrate);

/* ⚠ LISTEN-ONLY: RECEIVE ONLY, NEVER TRANSMIT, NEVER ACK.
 *
 * TWAI_MODE_LISTEN_ONLY, stored in NVS as can.listen and settable over the
 * EXISTING transport control plane (`_device/<id>/transport` = "can
 * listen=0|1"), so a bench workaround is reversible without a reflash.
 *
 * ⚠ THAT COMMAND DOES NOT FIT A CLASSIC-CAN FRAME.  A CAN state string is
 * [len][utf8…] in EIGHT bytes = SEVEN characters, and "can listen=0" is 13,
 * so publish_state refuses it — correctly, since a truncated string decodes
 * cleanly as a DIFFERENT command.  Only bare transport NAMES fit on CAN.  So
 * on a CAN-only board the route out is two hops: set transport = "mqtt" (5
 * bytes) over CAN, reboot, then send the full "can listen=0" over MQTT.  On
 * UART, which has no 8-byte limit, the one-hop form works as written.
 *
 * This exists for ONE reason: a transceiver whose DRIVER is dead but whose
 * RECEIVER works (bench 2026-08-12, three independent instruments).  In
 * normal mode such a node reaches BUS-OFF in about five seconds and then
 * stops receiving as well, because ESP-IDF's TWAI driver does not
 * auto-recover and this transport never calls twai_initiate_recovery().
 *
 * It is a WORKAROUND FOR BROKEN HARDWARE, not a deployment mode.  It
 * defaults to 0 on every board, it is announced at ERROR level on every
 * boot in plain words, and every publish through the transport is REFUSED
 * (rate-limited) rather than silently dropped — a node that cannot transmit
 * and does not say so is a node whose publishes vanish with no error. */
bool bus_can_set_listen_only(bool listen_only);
bool bus_can_is_listen_only(void);

/* ---- relay hooks ([CXC-CONFIG-BUS-RELAY]) ---- */
void bus_can_set_raw_sink(BusRawFrameFn fn, void* ud);
bool bus_can_send_raw(uint16_t ns_hash, BusMessageType type,
                      uint8_t scope_bits, const uint8_t* value, size_t len);

#endif /* BUS_CAN_H */
