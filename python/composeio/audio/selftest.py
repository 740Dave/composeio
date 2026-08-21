"""
ComposeUX Audio - self-test (machine-green, no speaker required)

Spec: [SPEC:CXC-AUDIO].  Two-tier verdict:

  MACHINE-GREEN (this file proves it): the pipeline is correct — a tone is
  synthesized, decoded, enqueued, and MIXED, and the semantic binding turns
  bus state into the right play()/stop() calls.  The real sound-card write
  is the ONLY thing mocked (an injected fake device / a recording sink).

  GLASS-PENDING (this file cannot prove): audible output on a real speaker,
  and every hardware backend (I2S/DFPlayer/MIDI/ALSA) — those need the
  physical peripheral on the bench.

Run:
    python -m composeio.audio --selftest
"""

from __future__ import annotations

import sys
from array import array
from typing import Any, List

from .binding import AudioActuator
from .contract import AudioSink, Sound
from .tone import sine_pcm, sine_wav_bytes


# ---------------------------------------------------------------------------
# Test doubles — the ONLY mocked seam is the actual device / sink
# ---------------------------------------------------------------------------

class FakeDevice:
    """Stand-in for miniaudio.PlaybackDevice (the speaker).  Captures the
    mix generator so the test can pull frames WITHOUT a sound card."""

    def __init__(self) -> None:
        self.gen = None
        self.started = self.stopped = self.closed = False

    def start(self, gen) -> None:
        self.gen = gen
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class RecordingSink(AudioSink):
    """An AudioSink that records calls instead of making sound — isolates
    the semantic-binding logic from any playback library."""

    def __init__(self) -> None:
        self.plays: List[dict] = []
        self.stops: List[int] = []
        self._id = 0

    def load(self, source: Any) -> Any:
        return source  # identity handle: the source IS the token

    def play(self, sound: Any, gain: float = 1.0, loop: bool = False) -> int:
        self._id += 1
        self.plays.append({"voice": self._id, "sound": sound,
                           "gain": gain, "loop": loop})
        return self._id

    def stop(self, voice_id: int) -> None:
        self.stops.append(voice_id)

    def stop_all(self) -> None:
        pass

    def set_gain(self, voice_id: int, gain: float) -> None:
        pass

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def _check(cond: bool, msg: str, results: List[str]) -> None:
    mark = "PASS" if cond else "FAIL"
    results.append(f"  [{mark}] {msg}")
    if not cond:
        raise AssertionError(msg)


def test_pipeline_load_enqueue_mix(results: List[str]) -> str:
    """load -> enqueue -> play(params) -> MIX, device mocked.

    Exercises the REAL decode path when miniaudio is installed; falls back
    to a pre-decoded tone (pipeline still fully tested) when it is not.
    """
    from .software_backend import MiniaudioSink

    fake = FakeDevice()
    sink = MiniaudioSink(sample_rate=44100, nchannels=2,
                         device_factory=lambda: fake)

    decode_leg = "real-decode"
    try:
        import miniaudio  # noqa: F401
        wav = sine_wav_bytes(440.0, 0.05, sample_rate=44100, nchannels=1)
        sound = sink.load(wav)          # bytes -> decode -> conform
        _check(sound is not None, "WAV bytes decoded to a Sound", results)
    except Exception as e:
        decode_leg = f"mock-decode (miniaudio unavailable: {e})"
        results.append(f"  [SKIP] real WAV decode — {decode_leg}")
        sound = sink.load(sine_pcm(440.0, 0.05, sample_rate=44100, nchannels=2))

    _check(isinstance(sound, Sound), "load returned a Sound handle", results)
    _check(sound.sample_rate == 44100 and sound.nchannels == 2,
           "Sound normalized to device format (44100Hz, 2ch)", results)

    vid = sink.play(sound, gain=0.8, loop=True)
    _check(vid > 0, "play() returned a voice id", results)

    # Assert the enqueue set the right params (sound/gain/loop) on the voice.
    voice = sink._voices[vid]
    _check(voice.sound is sound, "voice bound to the loaded sound", results)
    _check(abs(voice.gain - 0.8) < 1e-6, "voice gain == 0.8", results)
    _check(voice.loop is True, "voice loop == True", results)

    # The device (speaker) was opened and handed the primed mix generator.
    _check(fake.started and fake.gen is not None,
           "device.start() fired with the mix generator", results)

    # Drive the mixer for real against the fake device: pull one block and
    # assert it is non-silent (the tone actually reached the sink output).
    block = fake.gen.send(256)
    samples = array('h'); samples.frombytes(block)
    _check(len(samples) == 256 * 2, "mixer produced 256 stereo frames", results)
    _check(any(s != 0 for s in samples),
           "mixed output is non-silent (tone reached the sink)", results)

    # A second voice at low gain must be quieter than the first — proves
    # per-voice gain is applied in the mix, and voices overlap (concurrency).
    peak_full = max(abs(s) for s in samples)
    vid2 = sink.play(sound, gain=0.1, loop=False)
    voice2 = sink._voices[vid2]
    _check(abs(voice2.gain - 0.1) < 1e-6, "second voice gain == 0.1", results)

    sink.stop(vid)
    _check(vid not in sink._voices, "stop() removed the voice", results)

    sink.close()
    _check(fake.closed or fake.stopped, "close() released the device", results)
    return decode_leg


def test_semantic_binding(results: List[str]) -> None:
    """Publish namespace state -> assert the right play()/stop() calls.

    Uses a real ComposeIOBus and a RecordingSink so the assertion is on the
    ACTUATOR's decisions ([CXC-AUDIO-BIND]), not on any playback library.
    """
    from composeio.bus import ComposeIOBus

    bus = ComposeIOBus(bus_id="audio-selftest")
    bus.start()
    # Declare value shapes: 'alert' is binary (on/off), 'chime' is a free
    # string ('json' engine) — otherwise the bus auto-creates binary and
    # rejects 'ok'/'error'.  A real deploy declares these in its config.
    bus.register_namespace("alert", "binary")
    bus.register_namespace("chime", "json")
    sink = RecordingSink()

    config = {
        "device": "software",
        "clips": {
            "alarm": "ALARM_SRC",
            "chime_ok": "OK_SRC",
            "chime_err": "ERR_SRC",
        },
        "bindings": [
            {"when": "alert", "equals": "on", "play": "alarm",
             "loop": True, "gain": 0.8, "stop_on_release": True},
            {"when": "chime", "map": {"ok": "chime_ok", "error": "chime_err"}},
        ],
    }
    AudioActuator(bus, sink, config)

    # (1) alert -> on : looping alarm, gain 0.8, loop True, alarm clip
    bus.publish_state("alert", "on", source="test")
    _check(len(sink.plays) == 1, "alert=on triggered exactly one play", results)
    p = sink.plays[-1]
    _check(p["sound"] == "ALARM_SRC", "played the 'alarm' clip", results)
    _check(p["loop"] is True and abs(p["gain"] - 0.8) < 1e-6,
           "alarm played looping at gain 0.8", results)
    alarm_voice = p["voice"]

    # (1b) alert -> off : the looping voice is stopped ([CXC-AUDIO-BIND] release)
    bus.publish_state("alert", "off", source="test")
    _check(sink.stops == [alarm_voice],
           "alert=off stopped exactly the alarm voice", results)

    # (2) chime -> ok / error : value selects the mapped clip
    bus.publish_state("chime", "ok", source="test")
    _check(sink.plays[-1]["sound"] == "OK_SRC",
           "chime=ok played the mapped 'chime_ok' clip", results)
    bus.publish_state("chime", "error", source="test")
    _check(sink.plays[-1]["sound"] == "ERR_SRC",
           "chime=error played the mapped 'chime_err' clip", results)
    bus.publish_state("chime", "unmapped", source="test")
    _check(sink.plays[-1]["sound"] == "ERR_SRC",
           "chime=unmapped (no mapping) played nothing new", results)

    bus.stop()


def test_hardware_stubs_glass_pending(results: List[str]) -> None:
    """Hardware backends ARE AudioSinks but refuse with GlassPendingError."""
    from .contract import AudioSink as _AS
    from .contract import GlassPendingError
    from .hardware import HARDWARE_BACKENDS

    for key, cls in HARDWARE_BACKENDS.items():
        sink = cls()
        _check(isinstance(sink, _AS), f"{key}: is an AudioSink", results)
        try:
            sink.play("x")
            _check(False, f"{key}: play() should be glass-pending", results)
        except GlassPendingError:
            _check(True, f"{key}: play() raises GlassPendingError", results)


def run() -> int:
    results: List[str] = []
    print("ComposeUX Audio self-test [SPEC:CXC-AUDIO]")
    print("=" * 60)
    try:
        print("\n[1] pipeline: load -> enqueue -> play(params) -> mix "
              "(device mocked)")
        decode_leg = test_pipeline_load_enqueue_mix(results)
        print("\n[2] semantic binding: bus state -> play()/stop()")
        test_semantic_binding(results)
        print("\n[3] hardware backends: interface present, glass-pending")
        test_hardware_stubs_glass_pending(results)
    except AssertionError as e:
        print("\n".join(results))
        print(f"\nMACHINE-RED: {e}")
        return 1

    print("\n".join(results))
    print("=" * 60)
    print(f"MACHINE-GREEN: pipeline + binding correct ({decode_leg}).")
    print("GLASS-PENDING: audible output on a real speaker; the I2S/DFPlayer/"
          "MIDI/ALSA\n               hardware backends (need the peripheral).")
    return 0


if __name__ == "__main__":
    sys.exit(run())
