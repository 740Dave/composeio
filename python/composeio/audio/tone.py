"""
ComposeUX Audio - parametric tone synthesis (stdlib only)

Spec: [SPEC:CXC-AUDIO] [CXC-AUDIO-CONTRACT] (play_tone / play_note)

The ``play_tone(hz, dur)`` / ``play_note(note, dur)`` intents produce PCM
that is handed to a sink's ``load``.  Synthesis lives HERE, not in the
sink — mirroring the render split where geometry is authored by widgets
and a Canvas backend only rasterizes.  Pure stdlib (``math``, ``array``,
``wave``): no heavy dependency, so a headless tone works even where the
playback library is absent (the pipeline is testable; audible output is
the only glass-pending part).
"""

from __future__ import annotations

import io
import math
import wave
from array import array

from .contract import Sound

_INT16_MAX = 32767


def sine_pcm(freq_hz: float, dur_s: float, *, sample_rate: int = 44100,
             nchannels: int = 1, gain: float = 0.8,
             fade_s: float = 0.005) -> Sound:
    """Synthesize a sine tone as a decoded :class:`Sound` (interleaved s16).

    A short linear fade in/out (``fade_s``) removes click transients at the
    clip edges.  ``gain`` here shapes the SYNTHESIZED amplitude; the sink's
    per-voice ``gain`` is an independent playback multiplier.
    """
    n = max(1, int(sample_rate * dur_s))
    fade = max(1, int(sample_rate * fade_s))
    samples = array('h', bytes(2 * n * nchannels))
    two_pi_f = 2.0 * math.pi * freq_hz
    for i in range(n):
        env = 1.0
        if i < fade:
            env = i / fade
        elif i > n - fade:
            env = max(0.0, (n - i) / fade)
        v = int(_INT16_MAX * gain * env * math.sin(two_pi_f * i / sample_rate))
        base = i * nchannels
        for c in range(nchannels):
            samples[base + c] = v
    return Sound(samples=samples, sample_rate=sample_rate,
                 nchannels=nchannels, name=f"tone_{int(freq_hz)}hz")


def sine_wav_bytes(freq_hz: float, dur_s: float, *, sample_rate: int = 44100,
                   nchannels: int = 1, gain: float = 0.8) -> bytes:
    """Same tone, encoded as a WAV container in memory.

    Used to exercise a sink's real DECODE path (bytes -> PCM) without a
    file on disk — the ``--selftest`` load-decode leg.
    """
    snd = sine_pcm(freq_hz, dur_s, sample_rate=sample_rate,
                   nchannels=nchannels, gain=gain)
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(nchannels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(snd.samples.tobytes())
    return buf.getvalue()
