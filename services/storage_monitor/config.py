"""Endpoint + cadence + path configuration for storage_monitor — CONFIG, never
hardcoded.

storage_monitor is an ordinary ComposeBus client (same posture as ``time_service`` /
``bus_storage``). WHERE it connects, on WHAT substrate, HOW OFTEN it measures, and WHICH
filesystem it watches are all resolved from the environment with defaults that follow the
current live bus master — exactly as ``time_service/config.py`` does — so the same code
follows the broker when the cutover to a dedicated Skipper lands, with no edit.

⛔ Nothing here opens a socket and nothing here names a concrete Transport class.
Resolution is a pure function; the transport is BUILT from a spec by
``composeio.transport_registry.build`` in :mod:`.__main__` (the deploy path), the one
layer allowed to name a substrate (DP-CIO-6: transport is an implementation detail).
Tests inject an in-process bus and never read the broker knobs.
"""

from __future__ import annotations

import os

# The current live bus master (mqtt-broker.local / mqtt-broker.local, MQTT on :1883).
# The move to a dedicated Skipper is a LOCKED plan but not yet cut over, so this default
# follows the broker via env when it does.
DEFAULT_BROKER_HOST = "mqtt-broker.local"
DEFAULT_BROKER_PORT = 1883

#: The DEFAULT substrate — read from the environment exactly like the broker address, so
#: an operator changes the WIRE the same way they change the ADDRESS. The fallback string
#: is a default, not a decision (DP-CIO-6).
DEFAULT_TRANSPORT_TYPE = os.environ.get("COMPOSEIO_TRANSPORT", "mqtt")

#: The DEFAULT publish cadence, in SECONDS between measurements. Disk space changes
#: slowly, so a 30 s interval is plenty — a 1 Hz sensor here would be wasteful. This is a
#: DEFAULT, overridable via ``COMPOSEIO_STORAGE_MONITOR_INTERVAL`` or ``--interval``.
DEFAULT_INTERVAL_S = 30.0

#: The DEFAULT filesystem to watch. Skipper's Compose storage data (bus_storage :8420 +
#: document_store :8421 + the bus_scribe logs) lives on this filesystem. David flagged
#: skipper's drive is SMALL and a spinny disk may later be added on a DIFFERENT mount, so
#: the path MUST be re-pointable without a code change — hence env-driven, defaulting to
#: root. Override via ``COMPOSEIO_STORAGE_MONITOR_PATH`` or ``--path``.
DEFAULT_PATH = "/"

#: This service's identity on the bus (message ``source``).
SOURCE = "storage_monitor"


def broker_host() -> str:
    """The ComposeBus broker host. ``COMPOSEUX_MQTT_BROKER`` (the launcher convention)
    overrides; otherwise the current live bus master."""
    return os.environ.get("COMPOSEUX_MQTT_BROKER", DEFAULT_BROKER_HOST)


def broker_port() -> int:
    return int(os.environ.get("COMPOSEIO_MQTT_PORT", str(DEFAULT_BROKER_PORT)))


def monitored_path() -> str:
    """The filesystem to measure free space on. ``COMPOSEIO_STORAGE_MONITOR_PATH``
    overrides; otherwise root (``/``). Re-pointable to a future spinny-disk mount with no
    code change."""
    return os.environ.get("COMPOSEIO_STORAGE_MONITOR_PATH", DEFAULT_PATH)


def interval_s() -> float:
    """Seconds between measurements. ``COMPOSEIO_STORAGE_MONITOR_INTERVAL`` overrides;
    otherwise 30 s."""
    return float(os.environ.get("COMPOSEIO_STORAGE_MONITOR_INTERVAL", str(DEFAULT_INTERVAL_S)))
