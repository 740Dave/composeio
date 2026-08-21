"""
ComposeUX Audio - Portable Software Backend (miniaudio)

Spec: [SPEC:CXC-AUDIO] [CXC-AUDIO-BACKENDS] "HDMI / ALSA / CoreAudio" tier

The reference cross-platform sink, the audio analog of render/skia_backend.
It implements :class:`AudioSink` on **miniaudio** — chosen over
sounddevice+soundfile / simpleaudio because it is the MOST self-contained:
a SINGLE pip wheel that bundles its own C audio backends (CoreAudio /
WASAPI / ALSA / PulseAudio) AND its own decoders (WAV/MP3/FLAC/Vorbis),
with no system libraries to install (no PortAudio, no libsndfile).  One
dependency, works on macOS / Windows / Linux / aarch64 by construction.

    dependency line (pyproject [project.optional-dependencies]):
        audio = ["miniaudio>=1.60"]

miniaudio is imported LAZILY (only when a device is actually opened), so
this module imports cleanly on a host where the lib is absent — the
contract, tone synthesis, binding and stubs all remain usable, and the
pipeline stays machine-testable.  Only the final device write is
glass-pending.

Mixing model ([CXC-AUDIO-OPEN] concurrency): ONE device, N voices summed
per-frame with per-voice gain and clamped to int16 — two sounds overlap,
as the spec asks.  All sounds are normalized on ``load`` to the device
format (s16, ``nchannels``, ``sample_rate``) so the mix is uniform.

TEST SEAM: the real ``miniaudio.PlaybackDevice`` (the speaker) is the ONLY
part a self-test cannot exercise silently, so it is injected.  Pass
``device_factory=`` a callable returning any object with ``start(gen)`` /
``stop()`` / ``close()``; the default lazily constructs the real device.
The decode, normalize, enqueue and MIX paths all run for real against the
fake device — machine-green covers everything up to the sound card.
"""

from __future__ import annotations

import logging
import threading
from array import array
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from .contract import (
    AudioBackendUnavailable,
    AudioSink,
    Sound,
    SoundRef,
    SoundSource,
)

logger = logging.getLogger("composeio.audio.software")

_INT16_MIN = -32768
_INT16_MAX = 32767


@dataclass
class _Voice:
    """One playing sound.  ``pos`` is a frame cursor into ``sound.samples``."""
    voice_id: int
    sound: Sound
    gain: float = 1.0
    loop: bool = False
    pos: int = 0
    active: bool = True


class MiniaudioSink(AudioSink):
    """Software mixing sink on miniaudio ([CXC-AUDIO-BACKENDS] OS-audio tier)."""

    def __init__(self, sample_rate: int = 44100, nchannels: int = 2,
                 device_factory: Optional[Callable[[], Any]] = None) -> None:
        self.sample_rate = sample_rate
        self.nchannels = nchannels
        self._device_factory = device_factory
        self._device: Any = None
        self._voices: Dict[int, _Voice] = {}
        self._next_id = 1
        self._lock = threading.RLock()
        self._closed = False

    # -- lazy miniaudio handle ---------------------------------------------

    @staticmethod
    def _miniaudio():
        try:
            import miniaudio  # lazy: absent-lib must not break import
        except Exception as e:  # pragma: no cover - env dependent
            raise AudioBackendUnavailable(
                "miniaudio is not installed — `pip install miniaudio` "
                "(optional extra: composeio[audio]).  The audio pipeline is "
                "still testable with a mock device_factory."
            ) from e
        return miniaudio

    # -- load / decode ------------------------------------------------------

    def load(self, source: SoundSource) -> Optional[Sound]:
        """Decode/normalize ``source`` to a device-format :class:`Sound`.

        Accepts a :class:`Sound` (from tone synthesis), raw encoded bytes
        (WAV/MP3/...), a path / ``file://`` / ``bus://`` URI, or a
        :class:`SoundRef` (its ``.uri`` is resolved).  Returns None if the
        source cannot be decoded (actuator plays nothing; bus stays right).
        """
        try:
            if isinstance(source, Sound):
                return self._conform(source)
            if isinstance(source, SoundRef):
                if not source.uri:
                    logger.warning("SoundRef %r has no uri for the software "
                                   "backend (a track/note ref needs a "
                                   "hardware backend)", source.name)
                    return None
                return self._decode_bytes(_fetch(source.uri))
            if isinstance(source, (bytes, bytearray)):
                return self._decode_bytes(bytes(source))
            if isinstance(source, str):
                return self._decode_bytes(_fetch(source))
        except AudioBackendUnavailable:
            raise
        except Exception as e:
            logger.warning("audio load failed for %r: %s", source, e)
            return None
        logger.warning("unsupported audio source type: %r", type(source))
        return None

    def _decode_bytes(self, data: bytes) -> Optional[Sound]:
        ma = self._miniaudio()
        decoded = ma.decode(data, output_format=ma.SampleFormat.SIGNED16,
                            nchannels=self.nchannels,
                            sample_rate=self.sample_rate)
        return Sound(samples=array('h', decoded.samples),
                     sample_rate=self.sample_rate, nchannels=self.nchannels,
                     name=getattr(decoded, "name", "") or "")

    def _conform(self, snd: Sound) -> Sound:
        """Normalize an already-PCM sound (e.g. a synthesized tone) to the
        device format.

        Matching format is returned as-is.  A rate mismatch is resampled by
        round-tripping through miniaudio's decoder (uniform, no bespoke DSP).
        A same-rate channel mismatch is up/down-mixed cheaply here — the
        common mono-tone -> stereo-device case."""
        if snd.nchannels == self.nchannels and snd.sample_rate == self.sample_rate:
            return snd
        if snd.sample_rate != self.sample_rate:
            import io
            import wave
            buf = io.BytesIO()
            with wave.open(buf, 'wb') as w:
                w.setnchannels(snd.nchannels)
                w.setsampwidth(2)
                w.setframerate(snd.sample_rate)
                w.writeframes(snd.samples.tobytes())
            return self._decode_bytes(buf.getvalue())
        # Same rate, different channel count: replicate channel 0.
        out = array('h', bytes(2 * snd.num_frames * self.nchannels))
        src, sc, dc = snd.samples, snd.nchannels, self.nchannels
        for f in range(snd.num_frames):
            # mono source value or channel-0 as the mix seed
            v = src[f * sc]
            base = f * dc
            for c in range(dc):
                out[base + c] = v if c >= sc else src[f * sc + c]
        return Sound(samples=out, sample_rate=self.sample_rate,
                     nchannels=self.nchannels, name=snd.name)

    # -- transport ----------------------------------------------------------

    def _ensure_device(self) -> None:
        if self._device is not None:
            return
        if self._device_factory is not None:
            self._device = self._device_factory()
        else:  # pragma: no cover - needs a real sound card
            ma = self._miniaudio()
            self._device = ma.PlaybackDevice(
                output_format=ma.SampleFormat.SIGNED16,
                nchannels=self.nchannels, sample_rate=self.sample_rate)
        gen = self._mix_stream()
        next(gen)  # prime to the first ``yield`` (init handshake)
        self._device.start(gen)

    def play(self, sound: Any, gain: float = 1.0, loop: bool = False) -> int:
        if self._closed:
            raise RuntimeError("sink is closed")
        if not isinstance(sound, Sound):
            # Convenience: allow a raw source to be played directly.
            sound = self.load(sound)
        if sound is None:
            return -1
        with self._lock:
            vid = self._next_id
            self._next_id += 1
            self._voices[vid] = _Voice(voice_id=vid, sound=sound,
                                       gain=_clampf(gain), loop=loop)
        self._ensure_device()
        logger.debug("play voice=%d sound=%s gain=%.2f loop=%s",
                     vid, sound.name, gain, loop)
        return vid

    def stop(self, voice_id: int) -> None:
        with self._lock:
            v = self._voices.pop(voice_id, None)
        if v is not None:
            v.active = False

    def stop_all(self) -> None:
        with self._lock:
            self._voices.clear()

    def set_gain(self, voice_id: int, gain: float) -> None:
        with self._lock:
            v = self._voices.get(voice_id)
            if v is not None:
                v.gain = _clampf(gain)

    def close(self) -> None:
        self._closed = True
        self.stop_all()
        dev = self._device
        self._device = None
        if dev is not None:
            for m in ("stop", "close"):
                try:
                    getattr(dev, m)()
                except Exception:
                    pass

    # -- the mixer ----------------------------------------------------------

    def _mix_stream(self):
        """Generator handed to the playback device.

        miniaudio protocol: first ``yield`` is the init handshake; each
        subsequent ``send(required_frames)`` must return that many frames
        of interleaved s16.  We sum active voices with per-voice gain and
        clamp — real DSP, exercised in the self-test against a fake device.
        """
        nch = self.nchannels
        required = yield b""
        while True:
            frames = int(required or 0)
            mix = [0] * (frames * nch)  # int accumulators, clamp on pack
            with self._lock:
                voices = list(self._voices.values())
            for v in voices:
                if not v.active:
                    continue
                self._mix_voice_into(v, mix, frames)
            # reap finished non-loop voices
            with self._lock:
                for v in voices:
                    if not v.active:
                        self._voices.pop(v.voice_id, None)
            out = array('h', (_clip16(s) for s in mix))
            required = yield out.tobytes()

    def _mix_voice_into(self, v: _Voice, mix: list, frames: int) -> None:
        s = v.sound.samples
        total = v.sound.num_frames
        nch = self.nchannels
        g = v.gain
        pos = v.pos
        for f in range(frames):
            if pos >= total:
                if v.loop and total:
                    pos = 0
                else:
                    v.active = False
                    break
            sbase = pos * nch
            dbase = f * nch
            for c in range(nch):
                mix[dbase + c] += int(s[sbase + c] * g)
            pos += 1
        v.pos = pos


# --- helpers ---------------------------------------------------------------

def _clampf(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else float(x))


def _clip16(v: int) -> int:
    return _INT16_MIN if v < _INT16_MIN else (_INT16_MAX if v > _INT16_MAX else v)


def _fetch(uri: str) -> bytes:
    """Read encoded audio bytes from a path / file:// / bus:// URI.

    ``bus://assets/…`` resolves via composeio.resolve against the bus
    master's storage service — the SAME asset seam the render backend uses
    for images ([CXC-AUDIO-OPEN] clip-asset addressing, mirroring the
    display asset path)."""
    if uri.startswith("bus://"):
        from composeio.resolve import resolve_bytes
        return resolve_bytes(uri)
    path = uri[7:] if uri.startswith("file://") else uri
    with open(path, "rb") as f:
        return f.read()
