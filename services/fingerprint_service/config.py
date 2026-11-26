"""Endpoint configuration for fingerprint_service — CONFIG, never hardcoded.

The service is an ordinary ComposeBus client ([CXC-FPSVC-STATION-EDITOR]
not-this: "The service and station are ordinary bus clients"). Where it
connects is resolved from the environment with a default that points at the
CURRENT live bus master, exactly as :mod:`composeio.resolve` does for the
storage services — so the same code follows the broker when the cutover to a
dedicated Buildhost lands, with no edit.

⛔ Nothing here opens a socket. Resolution is a pure function; the actual
connect lives in :mod:`.__main__` (the deploy path, glass-pending) and is the
job of Haven-Deployer, who verifies the live broker host at deploy time. Tests
inject an in-process bus and never read these.
"""

from __future__ import annotations

import os

# The current live bus master (mqtt-broker.local / mqtt-broker.local, MQTT on
# :1883). The move to a dedicated Buildhost on Servers/VLAN30 is a LOCKED plan
# but not yet cut over, so this default follows the broker via env when it does.
DEFAULT_BROKER_URL = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")


def broker_url() -> str:
    """The ComposeBus broker URL. ``COMPOSEIO_BUS_BROKER_URL`` overrides;
    ``COMPOSEUX_MQTT_BROKER`` (bare host, the launcher convention) is honored as
    a fallback; otherwise the current live bus master."""
    explicit = os.environ.get("COMPOSEIO_BUS_BROKER_URL")
    if explicit:
        return explicit
    host = os.environ.get("COMPOSEUX_MQTT_BROKER")
    if host:
        return host if "://" in host else f"mqtt://{host}:1883"
    return DEFAULT_BROKER_URL


# Templates live in this service's OWN store (a dedicated biometric service with
# its own retention + hard delete — [CXC-FPID-FINGERPRINT-STORAGE-SERVICE]), not
# in the generic bus_storage. Exposed for a future durable backend; the
# in-process store is the reference realization.
DEFAULT_DATA_DIR = os.environ.get(
    "FINGERPRINT_SERVICE_DATA_DIR", "/var/lib/composeio/fingerprint"
)

# Retention: six months of inactivity totally deletes ([DD-FPSVC-DELETE-IS-TOTAL]).
SIX_MONTHS_SECONDS = 6 * 30 * 24 * 60 * 60  # 180 days
