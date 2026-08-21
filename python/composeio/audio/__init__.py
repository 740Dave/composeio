"""
ComposeUX Audio — sound as an actuator output, parallel to the display.

Spec: [SPEC:CXC-AUDIO] (25-composeux-audio.lts) · roadmap [CXC-RM-AUDIO]

"State on the bus, sound in the (audio) actuator."  This package is the
audio analog of the render engine:

    contract.py          the AudioSink ABC — the audio analog of the
                         Canvas drawing contract ([CXC-AUDIO-CONTRACT]).
    software_backend.py  MiniaudioSink — the portable software mixer, one
                         self-contained pip wheel (analog of skia_backend).
    hardware.py          I2S/DFPlayer/MIDI/ALSA backend STUBS — future
                         backends, glass-pending, interface-defined.
    tone.py              parametric tone synthesis (play_tone), stdlib.
    binding.py           AudioActuator — the headless semantic binding:
                         subscribe a namespace, play a clip in response
                         ([CXC-AUDIO-BIND] / [CXC-AUDIO-HEADLESS]).

Heavy deps are LAZY: importing this package needs only stdlib, so the
contract, tone synthesis, binding and stubs all work where the playback
library is absent.  Only the final device write is glass-pending.

    optional dependency (pyproject [project.optional-dependencies]):
        audio = ["miniaudio>=1.60"]

Run the self-test (no speaker required):
    python -m composeio.audio --selftest
"""

from .binding import AudioActuator, make_sink
from .contract import (
    AudioBackendUnavailable,
    AudioSink,
    GlassPendingError,
    Sound,
    SoundRef,
)
from .tone import sine_pcm, sine_wav_bytes

__all__ = [
    # Contract
    "AudioSink",
    "Sound",
    "SoundRef",
    "AudioBackendUnavailable",
    "GlassPendingError",
    # Backend factory + semantic binding
    "make_sink",
    "AudioActuator",
    # Tone synthesis
    "sine_pcm",
    "sine_wav_bytes",
]
