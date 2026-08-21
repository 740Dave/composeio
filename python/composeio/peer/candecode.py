"""Foreign-vehicle CAN decoder — a declarative map, not a code plugin ([CXC-CAN-DECODER]).

The "what would SDR do?" call: vehicle specificity lives in a DBC-subset
declarative map ([CXC-CAN-DECODER-SCHEMA]), never in reader branches. The same
SocketCAN reader ([CXC-CAN-SOCKETCAN]) becomes a Fiat / Tesla / HVAC decoder
purely by loading a different map. This is the Python evaluator; the ESP32 C++
evaluator (Phase 2) crosschecks the SAME golden decode ([CXC-CAN-VERIFY] CAN-C4).

This module NEVER runs the ComposeBus-over-CAN namespace-hash codec
(``message_from_can`` / ``can_id_from_message``) — that is a DISTINCT concern on
the same wire ([CXC-CAN-TWO-CANS]). It only turns opaque OEM bytes into
engineering values on Layer-2 ``fiat/*`` namespaces.

THE SIGABRT RULE ([CXC-CAN-JSON-RULE], load-bearing): an unmapped enum index or
an out-of-domain value MUST NEVER raise into a callback. ``json`` is the safe
default; a strict engine is used ONLY where the map GUARANTEES the domain
(a total ``map:`` for ``enum``). An unmapped enum sample drops with a warning and
the signal RETAINS its last valid value — the process is never aborted.

Bit-extraction conventions (documented so the C++ crosscheck matches exactly):

  * ``little`` (Intel): the payload is one little-endian integer (byte 0 = LSB).
    ``raw = (int.from_bytes(data, "little") >> start_bit) & mask``. ``start_bit``
    is the signal's LSB; increasing bit index = increasing significance.
  * ``big`` (Motorola, the OBD-II default): MSB0 contiguous numbering — bit 0 is
    the MSB of byte 0. A field of ``length`` bits at MSB0 ``start_bit`` has its
    LSB at absolute bit ``start_bit + length - 1`` from the top, i.e.
    ``shift = total_bits - (start_bit + length)`` from the LSB of
    ``int.from_bytes(data, "big")``. For a byte-aligned signal this is simply the
    big-endian value of the covered bytes (``start_bit = byte_index * 8``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("composeio.can.decoder")

_MAX_PAYLOAD_BITS = 64          # classic CAN: 8 data bytes
_REQUIRED_FIELDS = ("frame_id", "publish", "start_bit", "length")
_VALID_BYTE_ORDERS = ("big", "little")


class DecoderMapError(ValueError):
    """A decoder map failed validation at load ([CXC-CAN-DECODER-SCHEMA]).

    A subclass of ``ValueError`` for callers that catch broadly, but raised ONLY
    at map-LOAD time. Decode-time never raises ([CXC-CAN-JSON-RULE])."""


def extract_bits(data: bytes, start_bit: int, length: int,
                 byte_order: str = "big") -> int:
    """Extract an unsigned ``length``-bit field. Pure; byte_order-explicit.

    Raises ValueError only for a runtime overrun of the ACTUAL payload — callers
    in the decode path treat that as a drop, never a raise into a callback."""
    total_bits = len(data) * 8
    if start_bit < 0 or length <= 0 or start_bit + length > total_bits:
        raise ValueError(
            f"signal [{start_bit}:{start_bit + length}) overruns "
            f"{len(data)}-byte payload")
    mask = (1 << length) - 1
    if byte_order == "little":
        return (int.from_bytes(data, "little") >> start_bit) & mask
    # big / Motorola, MSB0 contiguous
    shift = total_bits - (start_bit + length)
    return (int.from_bytes(data, "big") >> shift) & mask


def _coerce_frame_id(value) -> int:
    if isinstance(value, bool):
        raise DecoderMapError("frame_id must be an int, not a bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value, 16 if value.lower().startswith("0x") else 10)
        except ValueError as exc:
            raise DecoderMapError(f"frame_id {value!r} is not a valid int") from exc
    raise DecoderMapError(f"frame_id {value!r} is not a valid int")


class SignalEntry:
    """One validated signal extraction ([CXC-CAN-DECODER-SCHEMA] table row)."""

    __slots__ = ("frame_id", "eff", "publish", "start_bit", "length",
                 "byte_order", "scale", "offset", "map", "engine")

    def __init__(self, raw: dict):
        if not isinstance(raw, dict):
            raise DecoderMapError(f"signal entry must be a mapping, got {raw!r}")
        missing = [f for f in _REQUIRED_FIELDS if f not in raw]
        if missing:
            raise DecoderMapError(
                f"signal entry {raw!r} missing required field(s): "
                f"{', '.join(missing)}")

        self.frame_id = _coerce_frame_id(raw["frame_id"])
        self.eff = bool(raw.get("eff", False))
        self.publish = raw["publish"]
        self.start_bit = int(raw["start_bit"])
        self.length = int(raw["length"])
        self.byte_order = raw.get("byte_order", "big")
        self.map = raw.get("map")
        self.engine = raw.get("engine", "json")

        has_scale_offset = ("scale" in raw) or ("offset" in raw)
        has_map = self.map is not None
        # scale/offset XOR map ([CXC-CAN-DECODER-SCHEMA]).
        if has_map and has_scale_offset:
            raise DecoderMapError(
                f"signal {self.publish!r} declares BOTH map and scale/offset — "
                f"mutually exclusive")
        if self.engine == "enum" and not has_map:
            raise DecoderMapError(
                f"signal {self.publish!r} engine=enum requires a total 'map'")

        self.scale = float(raw.get("scale", 1.0))
        self.offset = float(raw.get("offset", 0.0))

        if self.byte_order not in _VALID_BYTE_ORDERS:
            raise DecoderMapError(
                f"signal {self.publish!r} byte_order {self.byte_order!r} "
                f"not in {_VALID_BYTE_ORDERS}")
        if self.length <= 0:
            raise DecoderMapError(
                f"signal {self.publish!r} length must be > 0")
        if self.start_bit < 0:
            raise DecoderMapError(
                f"signal {self.publish!r} start_bit must be >= 0")
        # Overrun the frame's max payload at LOAD, not silently at decode
        # ([CXC-CAN-DECODER] error-case).
        if self.start_bit + self.length > _MAX_PAYLOAD_BITS:
            raise DecoderMapError(
                f"signal {self.publish!r} [{self.start_bit}:"
                f"{self.start_bit + self.length}) overruns the "
                f"{_MAX_PAYLOAD_BITS}-bit (8-byte) classic-CAN payload")
        if self.map is not None:
            if not isinstance(self.map, dict) or not self.map:
                raise DecoderMapError(
                    f"signal {self.publish!r} map must be a non-empty object")
            # Normalize map keys to int (YAML may give int or str keys).
            self.map = {_coerce_frame_id(k): v for k, v in self.map.items()}

    @property
    def is_enum(self) -> bool:
        return self.map is not None

    def engine_config(self):
        """The ComposeBus engine to register ``publish`` with."""
        if self.engine == "enum":
            values = list(dict.fromkeys(self.map.values()))
            return {"type": "enum", "values": values, "default": values[0]}
        return self.engine  # "json" or a proven-strict engine name

    def decode(self, data: bytes):
        """Decode one payload -> (published_value, is_droppable_enum_miss).

        Returns ``(value, False)`` on success. For an enum whose raw value is
        absent from the map, returns ``(raw_int, True)`` so the runtime can drop
        with a warning WITHOUT raising ([CXC-CAN-JSON-RULE])."""
        raw = extract_bits(data, self.start_bit, self.length, self.byte_order)
        if self.is_enum:
            if raw in self.map:
                return self.map[raw], False
            return raw, True  # unmapped -> caller drops, never raises
        return raw * self.scale + self.offset, False


class DecoderMap:
    """A validated foreign-vehicle decoder map ([CXC-CAN-DECODER-SCHEMA])."""

    def __init__(self, bus: str | None, signals: list[SignalEntry]):
        self.bus = bus
        self.signals = signals
        # frame_id (+eff) -> [entries] for fast dispatch.
        self._by_frame: dict[tuple[int, bool], list[SignalEntry]] = {}
        for s in signals:
            self._by_frame.setdefault((s.frame_id, s.eff), []).append(s)

    @classmethod
    def from_dict(cls, data: dict) -> "DecoderMap":
        if not isinstance(data, dict):
            raise DecoderMapError(f"decoder map must be a mapping, got {data!r}")
        raw_signals = data.get("signals", [])
        if raw_signals is None:
            raw_signals = []
        if not isinstance(raw_signals, list):
            raise DecoderMapError("'signals' must be a list")
        # An empty signals:[] loads cleanly and decodes nothing — a valid
        # raw-capture-only configuration ([CXC-CAN-DECODER-SCHEMA]).
        signals = [SignalEntry(entry) for entry in raw_signals]
        return cls(bus=data.get("bus"), signals=signals)

    @classmethod
    def from_yaml(cls, path) -> "DecoderMap":
        import yaml  # PyYAML (MIT) — the core hard dep; not in the reader path.
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh) or {})

    def entries_for(self, arb: int, eff: bool) -> list[SignalEntry]:
        return self._by_frame.get((arb, eff), [])


def raw_namespace(bus: str, arb: int) -> str:
    """Layer-1 raw namespace ([CXC-CAN-RAW]): ``can/<bus>/<hex>`` (lower, 0x-)."""
    return f"can/{bus}/0x{arb:x}"


class CanIngest:
    """Ties a reader to the bus: Layer-1 raw (opt-in) + Layer-2 decode.

    Layer-1 ([CXC-CAN-RAW]) is OFF by default per bus — a firehose enabled only
    for candump-style capture ([CXC-CAN-OQ-CANDUMP]). Layer-2 ([CXC-CAN-SIGNAL])
    is the product surface. Both layers use the ``json`` engine for raw and the
    map-declared engine for signals; the SIGABRT rule is enforced here."""

    def __init__(self, bus_obj, logical_bus: str = "can0",
                 decoder: DecoderMap | None = None, raw_enabled: bool = False,
                 source: str = "can"):
        self.bus = bus_obj
        self.logical_bus = logical_bus
        self.decoder = decoder
        self.raw_enabled = raw_enabled
        self.source = source
        self.enum_misses = 0
        self.decode_errors = 0
        if decoder is not None:
            for s in decoder.signals:
                self.bus.register_namespace(s.publish, s.engine_config())

    def acceptance_filters(self) -> list[tuple[int, int]]:
        """Kernel/hardware acceptance filters for exactly the decoder's frame ids
        ([CXC-CAN-SOCKETCAN], [CXC-PS-CAN]). Empty => accept-all (capture)."""
        if self.decoder is None:
            return []
        out: list[tuple[int, int]] = []
        seen = set()
        from composeio.peer.socketcan import (
            CAN_EFF_FLAG, CAN_EFF_MASK, CAN_SFF_MASK)
        for s in self.decoder.signals:
            if s.eff:
                cid = (s.frame_id & CAN_EFF_MASK) | CAN_EFF_FLAG
                mask = CAN_EFF_MASK | CAN_EFF_FLAG
            else:
                cid = s.frame_id & CAN_SFF_MASK
                mask = CAN_SFF_MASK | CAN_EFF_FLAG
            key = (cid, mask)
            if key not in seen:
                seen.add(key)
                out.append(key)
        return out

    def feed(self, arb: int, eff: bool, data: bytes) -> None:
        """Handle one accepted data frame: raw publish (if on) + decode."""
        import time
        if self.raw_enabled:
            payload = {
                "id": arb, "eff": eff, "dlc": len(data),
                "data": data.hex(), "t": time.time(),
            }
            ns = raw_namespace(self.logical_bus, arb)
            self.bus.register_namespace(ns, "json")
            self.bus.publish_state(ns, payload, source=self.source)

        if self.decoder is None:
            return
        for s in self.decoder.entries_for(arb, eff):
            self._decode_and_publish(s, data)

    def _decode_and_publish(self, s: SignalEntry, data: bytes) -> None:
        try:
            value, enum_miss = s.decode(data)
        except ValueError as exc:
            # Runtime payload too short for the field: drop, retain, never raise.
            self.decode_errors += 1
            logger.warning("Dropping %s decode: %s (signal retains last value)",
                           s.publish, exc)
            return
        if enum_miss:
            # Unmapped enum index — the SIGABRT trap ([CXC-CAN-JSON-RULE]).
            self.enum_misses += 1
            logger.warning(
                "Unmapped enum raw=%s for %s; dropping, %s retains last value",
                value, s.publish, s.publish)
            return
        try:
            self.bus.publish_state(s.publish, value, source=self.source)
        except ValueError as exc:
            # Belt-and-suspenders: a strict engine rejecting a value MUST NOT
            # abort the process ([CXC-CAN-JSON-RULE]). Log, never re-raise.
            self.decode_errors += 1
            logger.error("Refused out-of-domain value %r for %s: %s "
                         "(no SIGABRT)", value, s.publish, exc)

    def run_once(self, reader, timeout: float | None = 0.0) -> bool:
        """Pull one frame from a reader and feed it. Returns True if a frame was
        processed."""
        frame = reader.read_frame(timeout=timeout)
        if frame is None:
            return False
        arb, eff, data = frame
        self.feed(arb, eff, data)
        return True
