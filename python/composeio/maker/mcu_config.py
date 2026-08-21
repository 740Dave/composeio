"""
[FLOW:MAKER-PROJECT] S4 — the MCU config-emitter (maker-project-flow §5).

Step (e) of a maker project — "configure the MCU to *pull* from the designated
slots" — is **derived, not hand-authored**.  A maker `.compose.yaml` already
carries both halves in one artifact: the PHYSICAL netlist (`hookup:`, parsed by
S2 into ``comp.hookup``) and the LOGICAL half (``comp.namespaces`` — each slot's
state engine).  This module falls the MCU's read/publish config out of the
pin->slot seam so it **cannot drift** from the wiring or the widget.

    emit_mcu_config(comp) -> McuConfig

For every wire that carries a ``publishes:`` slot, the emitter derives one
**sensor descriptor**: "sample THIS MCU pin -> normalize -> publish to THIS slot
namespace."  For dual_throttle that is::

    sensors:
      - {publish: throttle/left,  pin: GPIO0, mode: analog,
         engine: continuous_0_255, sensor: pot-10k,
         normalize: {in_min: 0, in_max: 4095, out_min: 0, out_max: 255},
         sample_hz: 20}
      - {publish: throttle/right, pin: GPIO1, ...}


Output format — WHOSE contract (flagged for the architect)
----------------------------------------------------------
This emits the **Input Tier-1 sensor descriptor** of [SPEC:CXC-HAL] — the input
dual of the OUTPUT actuator table that is *already implemented on silicon* in
``firmware/reactive_core/src/actuator_hal.h`` (``ActuatorSpec`` +
``boards/esp32c3_devkitm.cpp``).  The HAL spec's canonical Tier-1 example is
``{sensor, pin, engine, publish, debounce_ms}`` ([CXC-HAL-TIERS]); the fields
here are that example plus the analog scale carried, field-for-field, from the
actuator dual (``in_min``/``in_max``/``out_max``).

IMPORTANT — the exact descriptor **schema is an OPEN decision** ([CXC-HAL-OPEN]:
"the exact descriptor schema" is explicitly OUT of the concept-capture spec).
There is an implemented ACTUATOR (output) table but no frozen SENSOR (input)
config the firmware reads yet — the reactive-core sensor read loop is future
([CXC-HAL-PLATFORM] "GPIO/ADC read loops publishing to the bus").  So this
emitter **defines** the sensor-side format by mirroring (a) the HAL spec's
Tier-1 descriptor example and (b) the implemented ``ActuatorSpec`` dual.  It is
NOT yet a ratified firmware contract — see the module-level FLAGS below and the
final report.

The result composes into the device `.compose.yaml` the ESP32 baseline stack
pulls at boot (``bus://assets/displays/<device>.compose.yaml``,
``firmware/.../config_fetch.cpp``) — a device config is a composition of render
widgets + HAL descriptors on one local bus ([CXC-HAL-DEVICE]).  ``to_yaml()``
gives the mergeable block; ``to_json()`` gives the doc_store pull shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .audio_cover import (
    dysv5f_play_track, dysv5f_frame_hex, track_filename, DYSV5F_BAUD,
)

# ---------------------------------------------------------------------------
# FLAGGED DEFAULTS — need the architect's glass ([CXC-HAL-OPEN]).
# ---------------------------------------------------------------------------

#: ADC full-scale raw reading per MCU part, keyed by catalog `part:` id.  The
#: ESP32-C3 ADC is 12-bit -> 0..4095.  This is an MCU *capability* that ideally
#: becomes a parts-catalog hint (`adc_bits`/`adc_max`, alongside `adc_pins`);
#: until then it lives here with a safe 12-bit default.  FLAG: catalog hint.
ADC_FULL_SCALE: Dict[str, int] = {
    "esp32-c3": 4095,   # 12-bit ADC1 (SAR), 0..4095
    "esp32-c3-supermini": 4095,   # same 12-bit ADC1 silicon as the C3
}
_DEFAULT_ADC_FULL_SCALE = 4095  # 12-bit — the ESP32 family default

#: The numeric value domain each state engine expects on the bus.  Analog
#: normalization maps the ADC raw span onto this so the published value never
#: violates the slot's engine (an out-of-domain value SIGABRTs a strict engine).
#: ``None`` -> the engine has no linear analog domain (digital / enum / json);
#: such a slot is emitted as ``mode: digital`` (threshold) or flagged.
ENGINE_DOMAINS: Dict[str, Optional[Tuple[float, float]]] = {
    "continuous_0_255": (0.0, 255.0),
    "continuous_0_100": (0.0, 100.0),   # a PERCENTAGE of full scale
    "binary": None,       # digital read (threshold), no analog scale
    "tri_level": None,    # enum-ish; a HAL value-map, not a linear scale
    "stop_light": None,   # enum
    "enum": None,
    "json": None,
}

#: Default analog sample rate (Hz).  A throttle wants to feel live without
#: flooding the bus; 20 Hz is a defensible starting point.  FLAG: per-project /
#: per-engine tuning ([CXC-HAL-OPEN] "sample rate ... bound the set").
DEFAULT_SAMPLE_HZ: int = 20

#: Default digital debounce (ms) — the HAL spec's Tier-1 example value
#: ([CXC-HAL-TIERS] `debounce_ms: 20`).  Only emitted for digital sensors.
DEFAULT_DEBOUNCE_MS: int = 20


# ---------------------------------------------------------------------------
# The emitted descriptor shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Normalize:
    """The analog value-map: ADC raw span -> the slot engine's value domain.

    This IS the normalization the task asks to be surfaced as a config field.
    ``in_*`` is the MCU ADC raw span; ``out_*`` is the state engine's numeric
    domain (continuous_0_255 -> 0..255, continuous_0_100 -> 0..100).  The
    firmware applies a clamped linear map — the same in_min..in_max -> 0..out_max
    scale the actuator HAL runs (``actuator_hal.cpp`` ``_map_scaled``).

    ⛔ THAT SENTENCE WAS ASPIRATIONAL UNTIL 2026-08-15.  This docstring claimed
    the two paths ran "the same clamped linear map" while the input path had no
    ``in_min`` AT ALL — it hardcoded 0 and used ``adc_max`` as ``in_max``, so
    the claim was true only for the degenerate case.  The firmware's
    ``SensorSpec`` now carries both, and ``in_min``/``in_max`` here are no
    longer always (0, adc_full_scale): when a wire declares CALIBRATION /
    SAFETY BANDS they are the banded span, and the emitted descriptor is the
    device's actual map rather than a plausible-looking default.
    """
    in_min: float
    in_max: float
    out_min: float
    out_max: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "in_min": _num(self.in_min),
            "in_max": _num(self.in_max),
            "out_min": _num(self.out_min),
            "out_max": _num(self.out_max),
        }


@dataclass(frozen=True)
class SensorSpec:
    """One derived read/publish descriptor — the input dual of ``ActuatorSpec``.

    Field names track the [CXC-HAL] Tier-1 descriptor example
    (``sensor``/``pin``/``engine``/``publish``/``debounce_ms``) plus the analog
    ``normalize`` scale carried from the actuator dual.
    """
    publish: str                       # slot namespace (HAL: `publish`)
    pin: str                           # MCU GPIO name (HAL: `pin`)
    mode: str                          # "analog" (ADC) | "digital" (GPIO)
    engine: str                        # the slot's state engine (HAL: `engine`)
    sensor: Optional[str] = None       # source peripheral part id (HAL: `sensor`)
    normalize: Optional[Normalize] = None   # analog only
    sample_hz: Optional[int] = None    # analog sample rate
    debounce_ms: Optional[int] = None  # digital debounce
    #: The leaf-owned conditioning knobs declared on the wire, VERBATIM, minus
    #: the two (``in_min``/``in_max``) that are already carried structurally in
    #: ``normalize``.
    #:
    #: ⛔ THIS EMITTER USED TO DROP THEM ALL.  The firmware read `invert`,
    #: `deadband` and `ema` straight off the wire, and this — the artifact whose
    #: entire job is "the MCU's read/publish config, derived so it cannot drift
    #: from the wiring" — emitted none of the three.  The emitted config
    #: therefore described a DIFFERENT DEVICE than the one that booted, and
    #: agreed with itself while doing it.  A fork that silently drops a knob is
    #: invisible to every "are these two lists the same?" check, because the
    #: dropped knob is in neither list.
    conditioning: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "publish": self.publish,
            "pin": self.pin,
            "mode": self.mode,
            "engine": self.engine,
        }
        if self.sensor:
            out["sensor"] = self.sensor
        if self.normalize is not None:
            out["normalize"] = self.normalize.to_dict()
        if self.sample_hz is not None:
            out["sample_hz"] = self.sample_hz
        if self.debounce_ms is not None:
            out["debounce_ms"] = self.debounce_ms
        # Emitted only when non-empty, so a wire that declares no conditioning
        # serializes byte-identically to before.
        if self.conditioning:
            out["conditioning"] = dict(self.conditioning)
        return out


# ---------------------------------------------------------------------------
# The OUTPUT/actuator side (S6) — the DY-SV5F audio cover.
#
# The input dual (SensorSpec) says "sample THIS pin -> publish THIS slot".  This
# is the OUTPUT dual: "SUBSCRIBE these namespaces -> derive an edge -> drive THIS
# module".  It is emitted ADDITIVELY: a scene without an ``audio_cover:`` block
# yields no actuators and the existing sensor path is byte-identical.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioCue:
    """One cue in the actuator's event->track map.

    ``track`` and ``file`` are TWO INDEPENDENT FACTS (decision D8.2, the
    2026-07-29 ears-verified DY-SV5F bring-up): the module's "select and play"
    command indexes by the card's NUMERIC FILE ORDER, not by the digits in the
    file name, so on a 2-file card ``00008.mp3`` IS track 1.  ``track`` is
    therefore the play ORDINAL and ``file`` is what is actually on the card —
    ``file`` is DECLARED in the ``audio_cover:`` config, not derived from
    ``track`` (that identity held only under the pre-D8 model).

    ``uart_frame`` is the EXACT play frame the C3 writes for ``track`` (single
    source of truth: ``audio_cover.dysv5f_play_track``), carried so the
    firmware/doc never re-derives the protocol.
    """
    cue: str                           # semantic event name (condition_one|jump)
    source: str                        # the namespace whose edge fires it
    edge: str                          # edge semantics (firmware/doc readable)
    track: int                         # DY-SV5F play ORDINAL (1..65535)
    file: str                          # the NNNNN.mp3 actually on the card
    uart_frame: str                    # the frame for `track`, e.g. "AA 07 02 00 01 B4"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cue": self.cue,
            "source": self.source,
            "edge": self.edge,
            "track": self.track,
            "file": self.file,
            "uart_frame": self.uart_frame,
        }


@dataclass(frozen=True)
class ActuatorSpec:
    """One derived OUTPUT descriptor — a bus SUBSCRIBER that drives a module.

    For the DY-SV5F audio cover: the namespaces it subscribes, the UART pin(s)
    driving the module, and the cue->track map (each with its exact play frame).
    The cover is INPUT-only from the bus and OUTPUT-only to the module (it never
    publishes) — a property the firmware inherits, recorded here as ``publishes:
    []`` so the contract is explicit in the emitted config.
    """
    kind: str                          # "audio" (a DY-SV5F voice module)
    module: str                        # catalog part id, e.g. "dy-sv5f"
    driver: str                        # firmware driver, e.g. "dy-sv5f-uart"
    subscribes: Tuple[str, ...]        # the namespaces the cover reads
    uart: Dict[str, Any]               # {tx_pin, busy_pin?, baud, protocol}
    cues: Tuple[AudioCue, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "module": self.module,
            "driver": self.driver,
            "subscribes": list(self.subscribes),
            "publishes": [],           # explicit: a pure subscriber (never publishes)
            "uart": dict(self.uart),
            "cues": [c.to_dict() for c in self.cues],
        }


@dataclass(frozen=True)
class McuConfig:
    """The derived MCU read/publish config — step (e), from the pin->slot map.

    A no-op config (no publishing wires / no hookup) has ``sensors == ()`` and
    serializes to ``{"sensors": []}``.

    ``actuators`` is the ADDITIVE OUTPUT side (S6): the DY-SV5F audio cover derived
    from a scene's ``audio_cover:`` block.  Empty for every scene without one, and
    the ``actuators:`` key is omitted from the serialized shape when empty so the
    existing sensor-only configs are byte-identical.
    """
    device: str                        # the maker project / app id
    board: Optional[str]               # the MCU catalog part id (single MCU)
    sensors: Tuple[SensorSpec, ...] = ()
    actuators: Tuple[ActuatorSpec, ...] = ()
    #: Non-fatal derivation notes (an ambiguous wire, a non-linear engine on an
    #: analog pin, a fallback default used) — surfaced, never silently dropped.
    warnings: Tuple[str, ...] = ()

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"sensors": [s.to_dict() for s in self.sensors]}
        # The OUTPUT side is emitted only when present, so a sensor-only config
        # is byte-identical to before (additive).
        if self.actuators:
            out["actuators"] = [a.to_dict() for a in self.actuators]
        # Provenance keys go first when present (a device pulls `sensors:`; the
        # rest is legible header).
        header: Dict[str, Any] = {}
        if self.device:
            header["device"] = self.device
        if self.board:
            header["board"] = self.board
        return {**header, **out}

    def to_yaml(self) -> str:
        """The mergeable `.compose.yaml` `sensors:` block (device config)."""
        import yaml
        return yaml.safe_dump(self.to_dict(), sort_keys=False,
                              default_flow_style=False)

    def to_json(self, *, indent: int = 2) -> str:
        """The doc_store pull shape."""
        return json.dumps(self.to_dict(), indent=indent)

    @property
    def is_noop(self) -> bool:
        return not self.sensors and not self.actuators


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

def emit_mcu_config(
    comp: Any,
    *,
    sample_hz: int = DEFAULT_SAMPLE_HZ,
    debounce_ms: int = DEFAULT_DEBOUNCE_MS,
    adc_full_scale: Optional[int] = None,
) -> McuConfig:
    """Derive the MCU read/publish config from a parsed maker composition.

    ``comp`` is a ``render.engine.parser.Composition`` (or any object exposing
    ``.hookup``, ``.namespaces``, ``.app_id``).  A composition without a
    ``hookup:`` block, or whose hookup has no ``publishes:`` wires, yields a
    no-op config (``sensors == ()``).

    ``adc_full_scale`` overrides the per-MCU ADC full-scale lookup for boards
    whose ADC resolution differs from the catalog default.
    """
    device = str(getattr(comp, "app_id", "") or "")
    hookup = getattr(comp, "hookup", None)
    if hookup is None:
        return McuConfig(device=device, board=None, sensors=())

    # name -> state_engine, from the LOGICAL half (comp.namespaces).
    engine_of: Dict[str, str] = {}
    for ns in getattr(comp, "namespaces", None) or []:
        if isinstance(ns, dict) and ns.get("name"):
            engine_of[str(ns["name"])] = str(ns.get("state_engine", "binary"))

    board = _sole_mcu_part(hookup)
    warnings: List[str] = []
    sensors: List[SensorSpec] = []

    for wire in hookup.publishing_wires:
        slot = wire.publishes
        picked = _pick_mcu_side(wire, hookup)
        if picked is None:
            warnings.append(
                f"wire {wire.frm}->{wire.to} publishes '{slot}' but neither "
                f"endpoint resolves to an MCU (a part with adc_pins); "
                f"no sensor descriptor emitted")
            continue
        mcu_end, sensor_end, mcu_def, is_adc = picked

        engine = engine_of.get(slot)
        if engine is None:
            warnings.append(
                f"slot '{slot}' has no namespace declaration; engine unknown, "
                f"defaulting to 'json' (no analog normalization)")
            engine = "json"

        domain = ENGINE_DOMAINS.get(engine, None)
        sensor_part = _part_id(hookup, sensor_end)

        cond = dict(getattr(wire, "conditioning", None) or {})

        if is_adc and domain is not None:
            full = (adc_full_scale if adc_full_scale is not None
                    else _adc_full_scale(mcu_def, board, warnings))
            # The CALIBRATION / SAFETY BANDS, if the wire declares them, are
            # the real in_min/in_max of the device's map.  They live in RAW ADC
            # CODES on purpose: expressed on the OUTPUT scale a band's width
            # would change whenever the engine changed (exactly the
            # continuous_0_255 -> continuous_0_100 migration that introduced
            # them), so the safety property would have silently rescaled.
            band_min = cond.pop("in_min", None)
            band_max = cond.pop("in_max", None)
            in_min = float(band_min) if band_min else 0.0
            in_max = float(band_max) if band_max else float(full)
            if not 0.0 <= in_min < in_max <= float(full):
                warnings.append(
                    f"slot '{slot}' declares an out-of-range band "
                    f"in_min={band_min} in_max={band_max} against a 0..{full} "
                    f"ADC; falling back to the full span (NO band). The device "
                    f"applies the same fallback, so this is a real loss of the "
                    f"rail-reachability guarantee, not a cosmetic warning.")
                in_min, in_max = 0.0, float(full)
            sensors.append(SensorSpec(
                publish=slot,
                pin=mcu_end.pin,
                mode="analog",
                engine=engine,
                sensor=sensor_part,
                normalize=Normalize(in_min, in_max, domain[0], domain[1]),
                sample_hz=sample_hz,
                conditioning=cond,
            ))
        else:
            # Digital: an ADC pin whose engine is non-linear (binary/enum) is a
            # threshold read; a non-ADC MCU GPIO is digital by construction.
            if is_adc and domain is None:
                warnings.append(
                    f"slot '{slot}' engine '{engine}' has no linear analog "
                    f"domain; pin {mcu_end.pin} emitted as a digital "
                    f"(threshold) read")
            sensors.append(SensorSpec(
                publish=slot,
                pin=mcu_end.pin,
                mode="digital",
                engine=engine,
                sensor=sensor_part,
                debounce_ms=debounce_ms,
                conditioning=cond,
            ))

    # S6 — the OUTPUT/actuator side (additive): the DY-SV5F audio cover.
    actuators = _emit_audio_actuators(comp, hookup, warnings)

    return McuConfig(
        device=device,
        board=board,
        sensors=tuple(sensors),
        actuators=tuple(actuators),
        warnings=tuple(warnings),
    )


def _emit_audio_actuators(comp: Any, hookup: Any,
                          warnings: List[str]) -> List[ActuatorSpec]:
    """Derive the audio-actuator block from a scene's ``audio_cover:`` config.

    Returns ``[]`` for any scene without an ``audio_cover:`` block (additive).
    The cue->track map + subscribe list + UART pin + the on-card ``file:`` come
    from the CONFIG; only the exact play frame is derived, from the single-source
    DY-SV5F protocol builder, so the protocol cannot drift.
    """
    cfg = getattr(comp, "audio_cover", None)
    if not isinstance(cfg, dict):
        return []

    module = str(cfg.get("module", "") or "")
    # Resolve the module's catalog part id (the hookup part id -> its `part:`).
    module_part = None
    if hookup is not None and module:
        hp = hookup.part(module)
        if hp is not None:
            module_part = hp.part
        else:
            warnings.append(
                f"audio_cover.module '{module}' is not a part in the hookup; "
                f"the actuator's `module` falls back to the raw id")
    module_id = module_part or module or "dy-sv5f"

    uart_cfg = cfg.get("uart", {}) or {}
    tx_pin = str(uart_cfg.get("tx_pin", "") or "")
    if tx_pin and hookup is not None:
        # sanity: the UART TX pin should be a real MCU pin AND actually wired to
        # the module — a drift guard (config vs netlist).
        mcu_pins = set()
        for hp in hookup.parts:
            if hp.definition and hp.definition.adc_pins:  # the MCU
                mcu_pins.update(hp.definition.pins)
        if mcu_pins and tx_pin not in mcu_pins:
            warnings.append(
                f"audio_cover.uart.tx_pin '{tx_pin}' is not an MCU pin; "
                f"check the hookup wiring")
    uart_out: Dict[str, Any] = {
        "tx_pin": tx_pin,
        "baud": int(uart_cfg.get("baud", DYSV5F_BAUD) or DYSV5F_BAUD),
        "protocol": "dy-sv5f",
    }
    if uart_cfg.get("busy_pin"):
        uart_out["busy_pin"] = str(uart_cfg.get("busy_pin"))

    subscribes = tuple(str(s) for s in (cfg.get("subscribes") or []))

    cues: List[AudioCue] = []
    for entry in cfg.get("cues") or []:
        if not isinstance(entry, dict):
            continue
        track = entry.get("track")
        if track is None:
            warnings.append(
                f"audio_cover cue '{entry.get('cue')}' has no track; skipped")
            continue
        try:
            idx = int(track)
            frame = dysv5f_frame_hex(dysv5f_play_track(idx))
        except (TypeError, ValueError) as exc:
            warnings.append(
                f"audio_cover cue '{entry.get('cue')}' bad track {track!r}: {exc}")
            continue
        # The on-card FILE NAME is DECLARED, never derived.  Under D8.2 the play
        # ordinal and the file name are independent facts (see AudioCue), so
        # `track_filename(idx)` is only a legacy convenience for a card named in
        # index order — and it is a LOUD one, because emitting a file name the
        # card does not contain is worse than emitting none: this field is what a
        # human reads when loading the card.
        fname = entry.get("file")
        if fname:
            fname = str(fname)
        else:
            fname = track_filename(idx)
            warnings.append(
                f"audio_cover cue '{entry.get('cue')}' declares no `file:`; the "
                f"on-card file name is NOT derivable from track {idx} (the module "
                f"indexes by numeric file ORDER, decision D8.2) — falling back to "
                f"the index-order convention '{fname}', which is only correct if "
                f"the card is named that way. Declare `file:` to be sure.")
        cues.append(AudioCue(
            cue=str(entry.get("cue", "")),
            source=str(entry.get("source", "")),
            edge=str(entry.get("edge", "")),
            track=idx,
            file=fname,
            uart_frame=frame,
        ))

    return [ActuatorSpec(
        kind="audio",
        module=module_id,
        driver="dy-sv5f-uart",
        subscribes=subscribes,
        uart=uart_out,
        cues=tuple(cues),
    )]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pick_mcu_side(wire: Any, hookup: Any):
    """Return (mcu_end, sensor_end, mcu_def, is_adc) or None.

    Same MCU-side rule as ``Hookup.pin_slot_map`` — prefer the endpoint whose
    catalog def declares the pin in ``adc_pins`` (analog).  Falls back to the
    endpoint whose part is an MCU (has *any* adc_pins) for a digital GPIO read.
    """
    ends = (wire.frm, wire.to)
    # 1. an ADC pin -> analog.
    for i, end in enumerate(ends):
        hp = hookup.part(end.part)
        if hp and hp.definition and hp.definition.is_adc_pin(end.pin):
            return end, ends[1 - i], hp.definition, True
    # 2. an MCU pin that isn't ADC-capable -> digital read.
    for i, end in enumerate(ends):
        hp = hookup.part(end.part)
        if hp and hp.definition and hp.definition.adc_pins:
            return end, ends[1 - i], hp.definition, False
    return None


def _part_id(hookup: Any, end: Any) -> Optional[str]:
    hp = hookup.part(end.part)
    return hp.part if hp else None


def _sole_mcu_part(hookup: Any) -> Optional[str]:
    """The catalog part id of the hookup's single MCU (a part with adc_pins).

    Returns None when there is no MCU or more than one (ambiguous `board`);
    the per-sensor ``mcu_def`` still resolves the ADC scale correctly either
    way — this is only the config header hint.
    """
    mcus = []
    for hp in hookup.parts:
        if hp.definition and hp.definition.adc_pins:
            mcus.append(hp.part)
    uniq = list(dict.fromkeys(mcus))
    return uniq[0] if len(uniq) == 1 else None


def _adc_full_scale(mcu_def: Any, board: Optional[str],
                    warnings: List[str]) -> int:
    part_id = getattr(mcu_def, "id", None) or board
    if part_id in ADC_FULL_SCALE:
        return ADC_FULL_SCALE[part_id]
    warnings.append(
        f"ADC full-scale unknown for MCU '{part_id}'; defaulting to "
        f"{_DEFAULT_ADC_FULL_SCALE} (12-bit).  FLAG: add an `adc_bits`/"
        f"`adc_max` hint to the parts catalog.")
    return _DEFAULT_ADC_FULL_SCALE


def _num(v: float):
    """Emit whole floats as ints for a clean YAML/JSON (0.0 -> 0, 255.0 -> 255)."""
    f = float(v)
    return int(f) if f.is_integer() else f
