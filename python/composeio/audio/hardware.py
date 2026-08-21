"""
ComposeUX Audio - Hardware Backend Stubs (GLASS-PENDING)

Spec: [SPEC:CXC-AUDIO] [CXC-AUDIO-BACKENDS] "One Intent, Many Backends"

Each class here is a FUTURE audio backend that realizes the AudioSink
contract for a specific peripheral.  They are stubs on purpose: the
interface is defined and machine-checkable (they ARE AudioSinks, so the
same actuator config drives them), but the wire-level realization needs
the physical device on the bench.  Every method raises GlassPendingError
with a precise "what glass is missing" note.

This is [CXC-AUDIO-BACKENDS] / [CXC-HW-INTENT] taken at its word: which
backend a node has is a **board-profile fact**, like its display driver.
A sound-only ESP32 (DFPlayer, no screen) is a headless ComposeUX node
whose peripheral is audio ([CXC-AUDIO-HEADLESS]).

DO NOT implement these without the hardware — a green unit test that
never touched a UART/I2S/MIDI wire would be a false machine-green.  The
honest verdict is: contract shape verified, hardware behavior pending.
"""

from __future__ import annotations

from typing import Any

from .contract import AudioSink, GlassPendingError, SoundRef, SoundSource


class _HardwareStub(AudioSink):
    """Shared stub: presents the full contract, refuses every call with a
    clear glass-pending reason so a caller cannot mistake it for working."""

    #: One-line description of the wiring this backend needs.
    GLASS = "hardware backend not implemented"

    def _pending(self, op: str) -> GlassPendingError:
        return GlassPendingError(
            f"{type(self).__name__}.{op}: GLASS-PENDING — {self.GLASS}. "
            f"The AudioSink interface is defined; the device realization "
            f"needs the physical peripheral ([CXC-AUDIO-BACKENDS])."
        )

    def load(self, source: SoundSource) -> Any:
        raise self._pending("load")

    def play(self, sound: Any, gain: float = 1.0, loop: bool = False) -> int:
        raise self._pending("play")

    def stop(self, voice_id: int) -> None:
        raise self._pending("stop")

    def stop_all(self) -> None:
        raise self._pending("stop_all")

    def set_gain(self, voice_id: int, gain: float) -> None:
        raise self._pending("set_gain")

    def close(self) -> None:  # closing an unopened device is harmless
        pass


class I2SDACSink(_HardwareStub):
    """I2S DAC / amp (ESP32, MinnowBoard) — synthesized tones or streamed
    WAV to a MAX98357A-class amp over I2S ([CXC-AUDIO-BACKENDS] I2S/DAC).

    Glass-pending: needs an I2S bus + DAC.  ``load`` would keep PCM (like
    the software sink); ``play`` would DMA frames to the I2S peripheral.
    """
    GLASS = "needs an I2S DAC/amp (e.g. MAX98357A) + I2S DMA driver"


class DFPlayerSink(_HardwareStub):
    """DFPlayer Mini over UART — serial "play track N" of an MP3 on the
    module's SD card ([CXC-AUDIO-BACKENDS]; highest-level, cheapest).

    Glass-pending: needs the DFPlayer wired to a UART.  ``load`` would map
    a clip name / ``SoundRef.track`` to a track number (the board-profile
    fungible-device map); ``play`` would send the 0x3F/0x0F serial frame.
    Ignores PCM entirely — the module owns decode.
    """
    GLASS = "needs a DFPlayer Mini on a UART (serial 'play track N' frames)"

    def load(self, source: SoundSource) -> Any:
        # Shape note: a real impl resolves to an int track number.
        if isinstance(source, SoundRef) and source.track is not None:
            raise self._pending("load")  # track known; wire still pending
        raise self._pending("load")


class MIDISynthSink(_HardwareStub):
    """MIDI synth (SAM2695 / Seeed XIAO) over serial MIDI — musical/
    expressive; ``play_note`` -> MIDI note-on/off ([CXC-AUDIO-BACKENDS]).

    Glass-pending: needs a serial MIDI synth.  Keys off ``SoundRef.note``
    / ``.dur`` rather than PCM.
    """
    GLASS = "needs a serial MIDI synth (SAM2695); play_note -> note-on/off"


class ALSADirectSink(_HardwareStub):
    """ALSA-direct (Linux/RPi) without the miniaudio abstraction — a thin
    ``aplay``/libasound path for boards that want the OS mixer directly
    ([CXC-AUDIO-BACKENDS] HDMI/ALSA tier).

    Glass-pending: for hosts where the portable software backend is not
    the desired route; needs libasound bindings / an aplay pipe.
    """
    GLASS = "needs a Linux ALSA device + libasound/aplay path"


#: Registry of future hardware backends by board-profile key.  The board
#: profile ([CXC-RM-M5] board_profile.h parallel) names its audio backend;
#: the runtime looks it up here.  ``software`` lives in the factory
#: (composeio.audio.__init__) since it is the only non-pending sink.
HARDWARE_BACKENDS = {
    "i2s": I2SDACSink,
    "dfplayer": DFPlayerSink,
    "midi": MIDISynthSink,
    "alsa_direct": ALSADirectSink,
}
