"""
ComposeUX Audio - Semantic Binding (the headless audio actuator)

Spec: [SPEC:CXC-AUDIO] [CXC-AUDIO-BIND] "Declarative Trigger -> Sound"
      [CXC-AUDIO-HEADLESS] "Sound Decouples from the Head"

"State on the bus, sound in the (audio) actuator."  An :class:`AudioActuator`
subscribes namespaces and plays a configured clip in response to bus state,
exactly the way a behavior binds to state for the display ([CXC-BIND]) — and
it needs no screen, so it IS a headless ComposeUX node whose peripheral is
audio.  An Intelligence "sounds the alarm" by publishing one bus bit, same
as any other actor; the actuator does the rest.

Config (backend-agnostic — the same config drives any AudioSink):

    device: software                 # software | i2s | dfplayer | midi | ...
    clips:                           # name -> a load() source
      alarm: bus://assets/audio/alarm.wav
      chime_ok: sounds/ok.wav
      chime_err: sounds/err.wav
    bindings:
      # (1) threshold: while `alert == on`, loop `alarm`; release stops it.
      - when: alert
        equals: "on"
        play: alarm
        loop: true
        gain: 0.8
        stop_on_release: true
      # (2) value-mapped: the namespace VALUE selects which clip to play.
      - when: chime
        map: { ok: chime_ok, error: chime_err }

Both binding shapes are the generic examples from the task/spec: an
``alert`` bit triggers a looping alarm; a ``chime`` value fires a mapped
one-shot.  The actuator holds the looping voice id per binding so a release
(``alert != on``) can stop exactly that voice ([CXC-AUDIO-BIND] "release
stops it").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .contract import AudioSink, SoundRef

logger = logging.getLogger("composeio.audio.actuator")


@dataclass
class _Binding:
    namespace: str
    equals: Optional[str] = None            # threshold form
    value_map: Optional[Dict[str, str]] = None  # value-mapped form
    clip: Optional[str] = None              # threshold: clip to play
    loop: bool = False
    gain: float = 1.0
    stop_on_release: bool = False
    # runtime state: the voice currently playing for this binding
    voice: Optional[int] = None


def make_sink(device: str = "software", **kwargs) -> AudioSink:
    """Factory: build an AudioSink for a board-profile device key.

    ``software`` is the portable miniaudio mixer; every other key is a
    (glass-pending) hardware backend from ``composeio.audio.hardware``.
    Which key a node uses is a board-profile fact ([CXC-AUDIO-BACKENDS]).
    """
    if device in ("software", "miniaudio", "os", "hdmi", "coreaudio"):
        from .software_backend import MiniaudioSink
        return MiniaudioSink(**kwargs)
    from .hardware import HARDWARE_BACKENDS
    cls = HARDWARE_BACKENDS.get(device)
    if cls is None:
        raise ValueError(
            f"unknown audio device {device!r}; known: software, "
            f"{', '.join(sorted(HARDWARE_BACKENDS))}")
    return cls()


class AudioActuator:
    """Binds bus namespaces to sound on an :class:`AudioSink`.

    Construct with a live ``ComposeIOBus`` and a sink (or use
    :meth:`from_config` to build the sink from the ``device:`` key).  The
    actuator loads every declared clip once, then subscribes each binding's
    namespace; a matching publish plays/stops through the sink.
    """

    def __init__(self, bus: Any, sink: AudioSink, config: dict) -> None:
        self.bus = bus
        self.sink = sink
        self._clips: Dict[str, Any] = {}
        self._bindings: List[_Binding] = []
        self._load_clips(config.get("clips", {}) or {})
        self._wire_bindings(config.get("bindings", []) or [])

    @classmethod
    def from_config(cls, bus: Any, config: dict) -> "AudioActuator":
        """Build the sink from ``config['device']`` and wire the actuator."""
        sink = make_sink(config.get("device", "software"))
        return cls(bus, sink, config)

    # -- setup --------------------------------------------------------------

    def _load_clips(self, clips: Dict[str, Any]) -> None:
        for name, source in clips.items():
            src = _coerce_source(source)
            handle = self.sink.load(src)
            if handle is None:
                logger.warning("clip %r failed to load from %r "
                               "(actuator will skip it)", name, source)
            self._clips[name] = handle

    def _wire_bindings(self, bindings: List[dict]) -> None:
        for spec in bindings:
            ns = spec.get("when")
            if not ns:
                logger.warning("audio binding without 'when' namespace: %r",
                               spec)
                continue
            b = _Binding(
                namespace=ns,
                equals=(None if spec.get("equals") is None
                        else str(spec["equals"])),
                value_map={str(k): str(v)
                           for k, v in (spec.get("map") or {}).items()} or None,
                clip=spec.get("play"),
                loop=bool(spec.get("loop", False)),
                gain=float(spec.get("gain", 1.0)),
                stop_on_release=bool(spec.get("stop_on_release", False)),
            )
            self._bindings.append(b)
            # subscribe once per binding — callback closes over the binding
            self.bus.subscribe(ns, self._make_cb(b))

    def _make_cb(self, b: _Binding):
        def _cb(namespace: str, new_value: Any, old_value: Any = None) -> None:
            self._evaluate(b, new_value)
        return _cb

    # -- evaluation ---------------------------------------------------------

    def _evaluate(self, b: _Binding, value: Any) -> None:
        sval = "" if value is None else str(value)
        if b.value_map is not None:
            # value-mapped one-shot ([CXC-AUDIO-BIND]): the value picks a clip
            clip = b.value_map.get(sval)
            if clip is not None:
                self._play(b, clip)
            return
        # threshold form: play while equals matches, stop on release
        matches = (b.equals is None) or (sval == b.equals)
        if matches:
            if b.voice is None or not b.loop:
                self._play(b, b.clip, remember=b.loop)
        elif b.stop_on_release and b.voice is not None:
            self.sink.stop(b.voice)
            b.voice = None

    def _play(self, b: _Binding, clip_name: Optional[str],
              remember: bool = False) -> None:
        if not clip_name:
            return
        handle = self._clips.get(clip_name)
        if handle is None:
            logger.warning("binding on %r references unknown/failed clip %r",
                           b.namespace, clip_name)
            return
        vid = self.sink.play(handle, gain=b.gain, loop=b.loop)
        if remember:
            b.voice = vid

    def close(self) -> None:
        self.sink.close()


def _coerce_source(source: Any):
    """A clip source may be a string/bytes/Sound, or a dict describing a
    SoundRef (name/uri/track/note) — the board-profile fungible-device
    form ([CXC-AUDIO-OPEN] clip addressing)."""
    if isinstance(source, dict):
        return SoundRef(name=source.get("name", ""), uri=source.get("uri", ""),
                        track=source.get("track"), note=source.get("note"),
                        dur=source.get("dur"))
    return source
