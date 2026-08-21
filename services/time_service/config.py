"""Endpoint + cadence configuration for time_service — CONFIG, never hardcoded.

time_service is an ordinary ComposeBus client ([CXC-TIME-SERVICE-INTENT], same
posture as ``bus_storage`` / ``fingerprint_storage``). WHERE it connects and on
WHAT substrate are resolved from the environment with defaults that follow the
current live bus master — exactly as :mod:`composeio.resolve` and
``fingerprint_storage/config.py`` do — so the same code follows the broker when
the cutover to a dedicated Skipper lands, with no edit.

⛔ Nothing here opens a socket and nothing here names a concrete Transport class.
Resolution is a pure function; the transport is BUILT from a spec by
``composeio.transport_registry.build`` in :mod:`.__main__` (the deploy path), the
one layer allowed to name a substrate. Tests inject an in-process bus and never
read these.
"""

from __future__ import annotations

import os

# The current live bus master (mqtt-broker.local / mqtt-broker.local, MQTT on
# :1883). The move to a dedicated Skipper is a LOCKED plan but not yet cut over,
# so this default follows the broker via env when it does.
DEFAULT_BROKER_HOST = "mqtt-broker.local"
DEFAULT_BROKER_PORT = 1883

#: The DEFAULT substrate — read from the environment exactly like the broker
#: address, so an operator changes the WIRE the same way they change the ADDRESS.
#: The fallback string is a default, not a decision: nothing in this package
#: constructs a transport class or writes a ``type:`` into a spec literal outside
#: the CLI entrypoint (DP-CIO-6: transport is an implementation detail).
DEFAULT_TRANSPORT_TYPE = os.environ.get("COMPOSEIO_TRANSPORT", "mqtt")

#: The DEFAULT publish cadence for ``time/base`` ([CXC-TIME-BASE] `tick-schedule`:
#: David confirmed "1hz signal is fine"). This is a DEFAULT, not a fixed constant
#: — a game-supplied time profile MAY override it ([CXC-TIME-GAME-PROFILE]).
DEFAULT_TICK_HZ = 1.0

#: This service's identity on the bus (message ``source``).
SOURCE = "time_service"


def broker_host() -> str:
    """The ComposeBus broker host. ``COMPOSEUX_MQTT_BROKER`` (the launcher
    convention) overrides; otherwise the current live bus master."""
    return os.environ.get("COMPOSEUX_MQTT_BROKER", DEFAULT_BROKER_HOST)


def broker_port() -> int:
    return int(os.environ.get("COMPOSEIO_MQTT_PORT", str(DEFAULT_BROKER_PORT)))
