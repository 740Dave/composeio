#!/usr/bin/env python3
"""uart_mqtt_bridge — put a UART-slaved node on the real ComposeBus.

WHY THIS EXISTS
    A display board slaved over UART has no IP and no broker connection. This
    bridge is the upstream peer: it speaks the framed UART protocol on one side
    ([BP-TRANSPORT-UART] / [BP-UART-FRAME]) and MQTT on the other, and relays
    between them. That is the same shape as `services/can_mqtt_bridge`, and the
    same distinction the corpus draws — a Transport lets ONE app speak ONE
    substrate; a BRIDGE translates between TWO live substrates. Bridges are
    complementary to transports, not alternatives.

    Its first job is validation. The firmware's C++ frame codec has only ever
    been tested against itself and against a Python-generated corpus; this puts
    it on a wire opposite the real `transport_uart.py` implementation, which is
    the interop that actually matters. A bug where both C++ ends agree on the
    WRONG framing sails through a board-to-board test and fails the moment
    anything Python touches the bus.

    Its second job is the product: this is what a C5/C6 bridge node will do in
    firmware, prototyped somewhere debuggable first.

TWO THINGS THAT ARE NOT OBVIOUS

  1. THE FRAME CARRIES A HASH, NOT A NAME. `[0xC1][len][ns_hash_hi][ns_hash_lo]
     [type_scope][value...][crc8]` identifies the namespace by a 16-bit
     CRC-16/CCITT hash. A hash is not reversible, so a namespace the bridge has
     never heard of arrives as an UNRESOLVABLE frame. Every namespace that may
     cross this link must be pre-registered (`--ns`), and an unknown hash is
     reported loudly rather than dropped — a silently ignored frame is
     indistinguishable from a wiring fault, which is the debugging trap this
     whole rig exists to avoid.

  2. ECHO LOOPS. The bridge subscribes MQTT and publishes to MQTT; without a
     guard, a message from the board would be relayed up, echoed back by the
     broker, relayed down, and round again forever. `transport_uart.decode_message`
     tags what it decodes with `source="uart"`, so anything already carrying that
     source is never sent back down — the same guard can_mqtt_bridge uses with
     `source="can"`.

USAGE
    python3 -m services.uart_mqtt_bridge.bridge \\
        --port /dev/cu.usbserial-FTZ29P6A \\
        --broker mqtt-broker.local \\
        --ns throttle/left --ns throttle/right

    Add --verbose to see every frame in both directions (what you want for a
    first-light test; too noisy for a long soak).
"""
from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from composeio.config_chunk import UART_CONTENT_BYTES
from composeio.transport import BusMessage, MessageType
from composeio.transport_uart import UARTTransport, register_namespace_hash, _reverse_hash
from composeio.transport_mqtt import MQTTTransport

from .config_server import BusStorageScenes, ConfigServer, config_namespace

log = logging.getLogger("uart_mqtt_bridge")

#: Messages decoded off the UART carry this source; never relay them back down.
UART_SOURCE = "uart"


class UartMqttBridge:
    def __init__(self, port: str, baud: int, broker: str, mqtt_port: int,
                 namespaces: list[str], app_id: str = "uart-bridge",
                 config_devices: list[str] | None = None,
                 bus_storage: str = "http://mqtt-broker.local:8420",
                 config_pace_ms: float = 2.0,
                 config_poll_sec: float = 15.0):
        self.namespaces = list(namespaces)
        self._port_path = port
        self._stop = threading.Event()
        self.up = 0        # UART -> MQTT
        self.down = 0      # MQTT -> UART
        self.unresolved = 0

        # [SPEC:CXC-CONFIG-BUS] — the head-side config server, mounted INSIDE
        # the bridge rather than beside it. It has to be here: the leaf's
        # CONFIG_REQUEST arrives as a UART frame on a link this process already
        # owns, and a second process cannot open the same serial port. That is
        # a deployment fact about a point-to-point link, not a statement that
        # config service belongs to bridging — `ConfigServer` takes a `send`
        # callable and would mount identically on a CAN transport.
        self.config: ConfigServer | None = None
        if config_devices:
            for device_id in config_devices:
                # The frame carries a HASH, not a name, so every namespace that
                # may cross this link must be pre-registered in BOTH directions
                # or an inbound CONFIG_REQUEST cannot be reversed to a name.
                register_namespace_hash(config_namespace(device_id))
                register_namespace_hash(f"_device/{device_id}/status")
            self.config = ConfigServer(
                send=self._send_uart,
                fetch=BusStorageScenes(bus_storage),
                devices=list(config_devices),
                chunk_payload=UART_CONTENT_BYTES,
                pace_ms=config_pace_ms,
                poll_sec=config_poll_sec)
            log.info("config server ON for %d device(s): %s (scenes from %s, "
                     "%d B/chunk)", len(config_devices),
                     ", ".join(config_devices), bus_storage,
                     UART_CONTENT_BYTES)

        # Pre-register EVERY namespace that may cross the link, in both
        # directions. Without this an inbound hash cannot be reversed to a name.
        for ns in namespaces:
            register_namespace_hash(ns)
        log.info("registered %d namespace hash(es): %s",
                 len(namespaces),
                 ", ".join(f"{ns}=0x{h:04X}"
                           for h, ns in sorted(_reverse_hash.items(), key=lambda kv: kv[1])
                           if ns in namespaces))

        self.uart = UARTTransport(port=port, baud=baud)
        self.mqtt = MQTTTransport(broker=broker, port=mqtt_port,
                                  client_id=app_id)

    # -- relay directions ------------------------------------------------

    def _send_uart(self, msg: BusMessage) -> None:
        """The config server's write path. Separate from `_on_mqtt` so a config
        transfer is never mistaken for relayed bus traffic in the counters."""
        self.uart.send(msg)

    def _on_uart(self, msg: BusMessage) -> None:
        """A frame arrived from the slaved board — publish it to the real bus."""
        # Config control plane FIRST, consumed here and not relayed: a
        # CONFIG_REQUEST is addressed to this head, not to the wider bus, and
        # echoing it to MQTT would put a request on the bus that nothing else
        # can answer.
        if self.config is not None and self.config.on_message(msg):
            return
        self.up += 1
        log.info("UART -> MQTT  %-24s = %-12r (type=%s scope=%s)",
                 msg.namespace, msg.value, msg.type.value, msg.scope)
        try:
            self.mqtt.send(msg)
        except Exception:
            log.exception("failed to relay upward: %s", msg.namespace)

    def _on_mqtt(self, msg: BusMessage) -> None:
        """Bus traffic — relay down to the board, unless we put it there."""
        if msg.source == UART_SOURCE:
            return                      # our own echo; see module docstring
        if msg.namespace not in self.namespaces:
            return                      # not wired across this link
        self.down += 1
        log.info("MQTT -> UART  %-24s = %-12r (src=%s)",
                 msg.namespace, msg.value, msg.source)
        try:
            self.uart.send(msg)
        except Exception:
            log.exception("failed to relay downward: %s", msg.namespace)

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.uart.start()
        if not self.uart.is_connected:
            raise SystemExit("UART did not open — check the port path and that "
                             "nothing else holds it")
        self.mqtt.start()
        # Give MQTT a moment; a broker that never connects is an environment
        # problem, not a bridge bug, and must say so rather than sit mute.
        for _ in range(50):
            if self.mqtt.is_connected:
                break
            time.sleep(0.1)
        if not self.mqtt.is_connected:
            log.error("MQTT did NOT connect — the UART side will still be read, "
                      "but nothing will reach the bus")

        self.uart.subscribe_all(self._on_uart)
        for ns in self.namespaces:
            self.mqtt.subscribe(ns, self._on_mqtt)

        if self.config is not None:
            self._watch = threading.Thread(target=self.config.run_watch,
                                           args=(self._stop,), daemon=True)
            self._watch.start()

        log.info("bridge up: UART %s <-> MQTT %s | relaying %d namespace(s)%s",
                 self._port_path,
                 "connected" if self.mqtt.is_connected else "DOWN",
                 len(self.namespaces),
                 "" if self.config is None
                 else f" | config server for {len(self.config.devices)}")

    def stop(self) -> None:
        self._stop.set()
        try:
            self.uart.stop()
        finally:
            self.mqtt.stop()
        log.info("bridge down. relayed up=%d down=%d unresolved=%d",
                 self.up, self.down, self.unresolved)
        if self.config is not None:
            # SILENCE IS IN THE SUMMARY. `ok`/`bad` between them can only
            # report a leaf that SPOKE; a transfer that went unanswered has to
            # appear here or the operator reads a clean line over a dead board.
            c = self.config.counters()
            log.info("config server: %d request(s), %d transfer(s), "
                     "%d frame(s), acks ok=%d refused=%d TIMEOUT=%d "
                     "superseded=%d unreadable=%d unmatched=%d stale=%d, "
                     "%d still in flight",
                     c["requests"], c["transfers"], c["frames"], c["acks_ok"],
                     c["acks_refused"], c["acks_timeout"],
                     c["acks_superseded"], c["acks_unreadable"],
                     c["acks_unmatched"], c["acks_stale"], c["pending"])
            for device_id, row in self.config.census_rows():
                log.info("  %s: %s", device_id, row)

    def run(self) -> None:
        self.start()
        try:
            while not self._stop.is_set():
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True, help="serial device for the UART side")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--broker", default="mqtt-broker.local")
    ap.add_argument("--mqtt-port", type=int, default=1883)
    ap.add_argument("--ns", action="append", default=[], metavar="NAMESPACE",
                    help="a namespace to relay (repeatable). REQUIRED: the frame "
                         "carries a hash, so an unregistered namespace cannot be "
                         "reversed on the way in.")
    ap.add_argument("--app-id", default="uart-bridge")
    ap.add_argument("--config-device", action="append", default=[],
                    metavar="DEVICE_ID",
                    help="serve this device's scene over the bus "
                         "([SPEC:CXC-CONFIG-BUS], repeatable). Answers its "
                         "CONFIG_REQUEST on _device/<id>/config and pushes an "
                         "unsolicited transfer when the scene changes.")
    ap.add_argument("--bus-storage", default="http://mqtt-broker.local:8420",
                    help="where the head dereferences scenes on the leaf's "
                         "behalf (the leaf has no IP; that is the point)")
    ap.add_argument("--config-pace-ms", type=float, default=2.0,
                    help="delay between config frames (leaf rx-ring headroom)")
    ap.add_argument("--config-poll-sec", type=float, default=15.0,
                    help="how often to re-read scenes and push on change")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S")

    if not args.ns and not args.config_device:
        log.error("no --ns and no --config-device given. The UART frame "
                  "identifies namespaces by a 16-bit hash, so the bridge "
                  "cannot name anything it was not told about. Pass at least "
                  "one of each kind you want carried.")
        return 2

    bridge = UartMqttBridge(args.port, args.baud, args.broker, args.mqtt_port,
                            args.ns, args.app_id,
                            config_devices=args.config_device,
                            bus_storage=args.bus_storage,
                            config_pace_ms=args.config_pace_ms,
                            config_poll_sec=args.config_poll_sec)

    def _sig(_signum, _frame):
        bridge._stop.set()
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    bridge.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
