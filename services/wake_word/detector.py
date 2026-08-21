"""ComposeIO "XO" Wake Word Detector — cross-platform voice trigger.

Listens for the wake word "XO" (or configurable alternatives) using
OpenWakeWord, then publishes an `xo_wake` event to the ComposeIO bus.

Cross-platform: macOS (M4 Pro), Linux (RPi 5, mqtt-broker).
Uses sounddevice for audio capture (PortAudio backend).

Runs as a standalone ComposeIO module or service hub workload.

Usage:
    # Standalone
    python3 -m services.wake_word.detector

    # As ComposeIO module
    from services.wake_word.detector import WakeWordDetector
    detector = WakeWordDetector(bus=bus)
    detector.start()
"""

from __future__ import annotations
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger("wake_word")

# Configuration
SAMPLE_RATE = 16000
CHUNK_DURATION_MS = 80  # OpenWakeWord expects 80ms chunks
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION_MS / 1000)  # 1280 samples
THRESHOLD = 0.5  # Detection confidence threshold
COOLDOWN_S = 2.0  # Minimum time between detections (debounce)

# Default wake word model — "hey jarvis" is a pre-trained model in openwakeword
# Custom "XO" model would need training; use a close pre-trained model for now
DEFAULT_MODEL = os.environ.get("COMPOSEIO_WAKE_MODEL", "hey_jarvis_v0.1")


class WakeWordDetector:
    """Continuously listens for a wake word and fires callbacks/bus events."""

    def __init__(
        self,
        bus=None,
        model_name: str = DEFAULT_MODEL,
        threshold: float = THRESHOLD,
        namespace: str = "xo_wake",
        on_detect: callable = None,
    ):
        self.bus = bus
        self.model_name = model_name
        self.threshold = threshold
        self.namespace = namespace
        self.on_detect = on_detect
        self._running = False
        self._thread: threading.Thread | None = None
        self._last_detection = 0.0
        self._oww_model = None

    def start(self):
        """Start listening in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info(f"Wake word detector started (model={self.model_name}, threshold={self.threshold})")

    def stop(self):
        """Stop listening."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        logger.info("Wake word detector stopped")

    def _listen_loop(self):
        """Main audio capture and detection loop."""
        try:
            import sounddevice as sd
        except ImportError:
            logger.error("sounddevice not installed. Run: pip install sounddevice")
            return

        try:
            from openwakeword.model import Model as OWWModel
            self._oww_model = OWWModel(
                wakeword_models=[self.model_name],
                inference_framework="onnx",
            )
            logger.info(f"OpenWakeWord model loaded: {self.model_name}")
        except ImportError:
            logger.error("openwakeword not installed. Run: pip install openwakeword")
            return
        except Exception as e:
            logger.error(f"Failed to load wake word model: {e}")
            return

        logger.info(f"Listening on default audio input at {SAMPLE_RATE}Hz...")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=1,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=self._audio_callback,
            ):
                while self._running:
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Audio stream error: {e}")

    def _audio_callback(self, indata, frames, time_info, status):
        """Called by sounddevice for each audio chunk."""
        if status:
            logger.warning(f"Audio status: {status}")
        if not self._running or self._oww_model is None:
            return

        # Convert to the format OpenWakeWord expects (int16 numpy array)
        audio_chunk = indata[:, 0]  # Mono

        # Run prediction
        try:
            prediction = self._oww_model.predict(audio_chunk)
            # Check all model scores
            for model_name, score in prediction.items():
                if score >= self.threshold:
                    now = time.time()
                    if now - self._last_detection >= COOLDOWN_S:
                        self._last_detection = now
                        self._on_wake_detected(model_name, score)
        except Exception as e:
            logger.debug(f"Prediction error: {e}")

    def _on_wake_detected(self, model_name: str, confidence: float):
        """Handle wake word detection."""
        logger.info(f"Wake word detected: {model_name} (confidence={confidence:.3f})")

        # Fire callback
        if self.on_detect:
            try:
                self.on_detect(model_name, confidence)
            except Exception as e:
                logger.error(f"Detection callback error: {e}")

        # Publish to bus
        if self.bus:
            try:
                self.bus.publish_event(
                    self.namespace, "wake",
                    data={"model": model_name, "confidence": round(confidence, 3)},
                    source="wake_word",
                )
            except Exception as e:
                logger.error(f"Bus publish error: {e}")


def main():
    """Standalone wake word detector."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Try to connect to ComposeIO bus via MQTT
    bus = None
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))
        from composeio.bus import ComposeIOBus
        from composeio.transport_mqtt import MQTTTransport

        mqtt_broker = os.environ.get("MQTT_BROKER", "mqtt-broker.local")
        mqtt_port = int(os.environ.get("MQTT_PORT", "1884"))

        mqtt = MQTTTransport(broker=mqtt_broker, port=mqtt_port, client_id="wake-word")
        bus = ComposeIOBus(bus_id="wake-word", global_transport=mqtt)
        bus.start()
        logger.info(f"Connected to ComposeIO bus via MQTT ({mqtt_broker}:{mqtt_port})")
    except Exception as e:
        logger.warning(f"No ComposeIO bus available ({e}), running standalone")

    def on_detect(model, confidence):
        print(f"\n*** WAKE WORD: {model} ({confidence:.1%}) ***\n")

    detector = WakeWordDetector(
        bus=bus,
        on_detect=on_detect,
    )
    detector.start()

    # Handle shutdown
    def shutdown(sig, frame):
        logger.info("Shutting down")
        detector.stop()
        if bus:
            bus.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Keep alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown(None, None)


if __name__ == "__main__":
    main()
