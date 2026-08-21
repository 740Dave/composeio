"""
ComposeUX Audio Engine - Platform-Independent Sound Contract

Spec: [SPEC:CXC-AUDIO] [CXC-AUDIO-CONTRACT]  (25-composeux-audio.lts)

Sound is an ACTUATOR OUTPUT parallel to the display.  Just as every
display backend implements the Canvas drawing contract (render/canvas.py),
every audio backend implements this ``AudioSink`` contract.  Actuator code
and the semantic binding call against this contract and never import a
platform-specific audio library directly.

The contract is deliberately SMALL and CLOSED (the discipline of
[CXC-AUDIO-CONTRACT]): it must be realizable on a $3 MCU (a DFPlayer over
UART) and a Pi (ALSA/CoreAudio) alike.  Growth is a spec change, not an
author liberty.

    load(source)              decode/resolve a sound to an opaque handle
                              (the audio analog of Canvas.load_image /
                              decode_image) — a WAV/MP3 path, raw bytes,
                              a ``Sound`` PCM buffer, a ``bus://`` asset,
                              or a backend-native ref (a DFPlayer track #).
    play(sound, gain, loop)   start a voice; returns a voice id
    stop(voice_id)            stop one voice
    stop_all()                stop every voice
    set_gain(voice_id, gain)  live level change on a playing voice
    close()                   release the device

`gain` is a linear multiplier 0.0-1.0 (the [CXC-AUDIO-CONTRACT]
``set_volume`` intent).  `loop` is the ``loop(on)`` intent folded into
``play`` so an alarm ("loop play_clip('alarm')", [CXC-AUDIO-BIND]) is one
call.  Parametric tones ([CXC-AUDIO-CONTRACT] play_tone) are produced by
``composeio.audio.tone`` and handed to ``load`` as PCM — the sink never
synthesizes, exactly as the Canvas backend never authors geometry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from array import array
from dataclasses import dataclass
from typing import Any, Union


# ---------------------------------------------------------------------------
# Value types used across the contract
# ---------------------------------------------------------------------------

@dataclass
class Sound:
    """A decoded PCM buffer — the audio analog of a decoded image handle.

    ``samples`` is interleaved signed-16-bit PCM (an ``array('h')``);
    ``nchannels`` frames are one sample-instant.  This is the normalized,
    backend-neutral form a software mixer consumes.  A hardware backend
    that plays whole files (DFPlayer, MIDI) ignores ``samples`` and keys
    off ``ref`` instead.
    """
    samples: array
    sample_rate: int
    nchannels: int
    name: str = ""

    @property
    def num_frames(self) -> int:
        return len(self.samples) // self.nchannels if self.nchannels else 0

    @property
    def duration(self) -> float:
        return self.num_frames / self.sample_rate if self.sample_rate else 0.0


@dataclass
class SoundRef:
    """A backend-native sound reference — the "fungible device" seam
    ([CXC-AUDIO-OPEN] clip-asset addressing).

    The config names a clip (``'alarm'``); a board profile maps it to
    whatever the wired backend understands: a DFPlayer *track number*, a
    MIDI *note*, an ALSA *file path*.  A software backend resolves a
    ``SoundRef`` by loading ``.uri``; a hardware backend reads ``.track``
    / ``.note`` and never touches PCM.
    """
    name: str = ""
    uri: str = ""
    track: int | None = None          # DFPlayer track number
    note: int | None = None           # MIDI note number
    dur: float | None = None          # note/tone duration (s)


# A ``load`` source is a path/URI string, raw encoded bytes, a decoded
# ``Sound``, or a backend-native ``SoundRef``.
SoundSource = Union[str, bytes, Sound, SoundRef]


class AudioBackendUnavailable(RuntimeError):
    """Raised when a backend's underlying library or device is absent.

    Callers catch this to degrade gracefully ([CXC-AUDIO] parallels the
    render rule "the screen is never broken" — a missing speaker must not
    crash the actuator; the bus state is still correct).
    """


class GlassPendingError(NotImplementedError):
    """Raised by hardware backend stubs.

    The interface is defined and machine-checkable; the wire-level
    realization needs physical hardware ("glass-pending").  See
    ``composeio.audio.hardware``.
    """


# ---------------------------------------------------------------------------
# The audio output contract
# ---------------------------------------------------------------------------

class AudioSink(ABC):
    """Platform-independent audio output surface — [CXC-AUDIO-CONTRACT].

    A concrete backend (software mixer, I2S/DAC, DFPlayer, MIDI, ALSA)
    subclasses this and translates every call to its peripheral.  The
    widget/config is backend-agnostic; portability is by construction,
    the same guarantee the Canvas contract gives the display.
    """

    @abstractmethod
    def load(self, source: SoundSource) -> Any:
        """Resolve a sound to an opaque, backend-owned handle.

        The audio analog of ``Canvas.load_image``/``decode_image``: a
        software backend decodes (WAV/MP3/...) to PCM; a DFPlayer backend
        returns its track number.  Returns None if the source cannot be
        resolved (the actuator plays nothing; the bus state stays correct).
        """

    @abstractmethod
    def play(self, sound: Any, gain: float = 1.0, loop: bool = False) -> int:
        """Start playing ``sound`` (a handle from ``load``).

        Returns a voice id usable with ``stop``/``set_gain``.  ``gain`` is
        a 0.0-1.0 linear multiplier; ``loop`` restarts at end-of-clip
        until stopped ([CXC-AUDIO-BIND] "loop play_clip('alarm')").
        """

    @abstractmethod
    def stop(self, voice_id: int) -> None:
        """Stop one voice.  A no-op if the id is unknown/already stopped."""

    @abstractmethod
    def stop_all(self) -> None:
        """Stop every active voice."""

    @abstractmethod
    def set_gain(self, voice_id: int, gain: float) -> None:
        """Change the level of a playing voice ([CXC-AUDIO] set_volume)."""

    @abstractmethod
    def close(self) -> None:
        """Release the device/peripheral.  Idempotent."""
