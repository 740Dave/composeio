"""paho client construction that works on paho-mqtt 1.x AND 2.x.

A PURE helper: it imports nothing (the caller passes its own paho module), so
it has no paho, bus, namespace or transport import. That is deliberate, so code whose import
footprint is pinned to stay off the transports (the Intelligence launcher's
[CXC-SOB-EXEMPT] entry) can build its client here too. `transport_mqtt`
re-exports `new_paho_client`.
"""
from __future__ import annotations


def new_paho_client(*, mqtt, protocol, **kwargs):
    """Build a paho ``Client`` on paho 1.x AND 2.x. Every paho client in the
    repository is built here, except one test that branches on the version
    itself (both pinned by test_paho2_and_bus_client_backoff.py).

    WHY. ``pyproject.toml`` allows ``paho-mqtt>=1.6.0``, so 2.x installs. paho
    2.0.0 made ``callback_api_version`` the REQUIRED first parameter of
    ``Client.__init__``, so the 1.x call ``Client(client_id=..., protocol=...)``
    is a TypeError there; 2.1.0 defaults it to VERSION1 with a
    DeprecationWarning. When the module has ``CallbackAPIVersion`` this passes
    ``VERSION1``, which keeps every callback in this repo on the signatures it
    was written for (``on_connect(client, userdata, flags, rc, properties)``
    under v5, ``on_connect_fail(client, userdata)``). Without the enum (1.x) it
    is the old call, unchanged. Moving to VERSION2 is a separate change: it
    rewrites every callback signature.

    ``protocol`` is REQUIRED, keyword-only: the generic ``mqtt.Client(`` call
    below is invisible to the v5 grep guard in test_mqtt5_negotiation.py, so the
    "no client takes paho's 3.1.1 default by omission" rule lives in this
    signature instead.

    ``mqtt`` is the caller's own ``paho.mqtt.client`` module, also REQUIRED:
    this module never imports paho, so it stays a pure helper that the
    "no paho outside the waist" ratchet has nothing to say about, and every
    caller already holds the module it constructs from.
    """
    api = getattr(mqtt, "CallbackAPIVersion", None)
    if api is None:
        return mqtt.Client(protocol=protocol, **kwargs)
    return mqtt.Client(callback_api_version=api.VERSION1, protocol=protocol,
                       **kwargs)
