"""WLED transport senders — how a WLED state fragment reaches a device.

Two paths per [CXC-HAL-ACTUATE], HTTP primary + MQTT optional:

  (a) HTTP  -- POST http://<host>/json/state  {on, bri, seg:[...], ps, ...}
              stdlib urllib only -- NO new dependency.
  (b) MQTT  -- publish JSON state to WLED's own `<deviceTopic>/api` topic,
              with a `/col` helper for a bare hex color. Carried by the
              ComposeBus waist's FOREIGN-CHANNEL seam (see below) -- no paho
              import here.

Both implement `send(state: dict)`. WLED merges partial state statefully, so a
sender ships exactly the fragment the mapping produced for one namespace change.

THE MQTT SENDER ON THE WAIST ([SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], 2026-08-11)
------------------------------------------------------------------------------------
`<deviceTopic>/api` is WLED's OWN protocol, not a ComposeBus namespace: a bare
topic carrying a bare JSON document, defined by the firmware on the other end.
That is precisely a FOREIGN CHANNEL, so it goes through
`ComposeIOBus.publish_foreign` -- the module names the device's address and the
transport owns the substrate.

An adaptor's job IS to speak the foreign protocol, so this is not a "bypass
wearing a hat": what changed is that the adaptor no longer OWNS a connection.
Before, every WLED device built its own `mqtt.Client`, so N lamps meant N broker
sockets from one process, and none of them could ride anything but MQTT. Now the
device spec goes to `transport_registry`, which means a WLED bridge on a CAN
segment is a spec change rather than a rewrite -- and if the chosen transport
cannot carry a foreign channel it says so LOUDLY instead of the lamp just never
lighting.

WHAT UNBLOCKED IT: `transport_registry._mqtt` did not pass `username`/`password`
through, so a per-device credential could not reach a registry-built transport --
the one real reason this file stayed on raw paho. That was a missing seam, not a
missing capability (`MQTTTransport` has always accepted both). Fixed in the same
commit; see the comment on `_mqtt`.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

# Imported into the module namespace so tests can patch `senders.urlopen`.
from urllib.request import Request, urlopen

logger = logging.getLogger("composeio.adaptors.wled")


class WLEDSender(ABC):
    """A sink for WLED JSON state fragments."""

    @abstractmethod
    def send(self, state: dict) -> Any:
        """Deliver a WLED state fragment to the device."""

    def close(self) -> None:  # optional lifecycle hook
        pass


class WLEDHTTPSender(WLEDSender):
    """POST WLED state fragments to `http://<host>/json/state` via stdlib urllib.

    `host` may be a bare host/IP ("wled.local", "192.0.2.60") or include a
    port ("192.0.2.60:80"). No dependency beyond the standard library.
    """

    def __init__(self, host: str, timeout: float = 3.0):
        self.host = host
        self.timeout = timeout
        self.url = f"http://{host}/json/state"

    def send(self, state: dict) -> Any:
        body = json.dumps(state).encode("utf-8")
        req = Request(
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (fixed http scheme)
                return resp.read()
        except Exception as exc:
            logger.warning("WLED HTTP POST to %s failed: %s", self.url, exc)
            return None

    def __repr__(self) -> str:
        return f"WLEDHTTPSender(url={self.url!r})"


class WLEDMQTTSender(WLEDSender):
    """Publish WLED state fragments to WLED's native device topics.

    WLED (with MQTT enabled) subscribes `<deviceTopic>/api`, which accepts a
    full JSON state document -- so this publishes `json.dumps(state)` there,
    identical semantics to the HTTP path. `<deviceTopic>/col` accepts a bare
    `#rrggbb` and is exposed via `send_color()`. Both are FOREIGN CHANNELS (the
    module docstring says why), carried by `bus.publish_foreign`.

    `client=` STILL WORKS and is still the test seam, but it is now a duck-typed
    FOREIGN SINK rather than a paho client: anything with
    `publish(channel, payload)`. The existing fake in `wled/selftest.py` already
    has exactly that shape and needed no change -- which is the point, because a
    test double that has to be rewritten to follow a refactor stops being
    evidence that behaviour was preserved.
    """

    def __init__(
        self,
        device_topic: str,
        broker: str = "localhost",
        port: int = 1883,
        client: Any | None = None,
        client_id: str = "composeio-wled",
        username: str | None = None,
        password: str | None = None,
    ):
        self.device_topic = device_topic.rstrip("/")
        self.broker = broker
        self.port = port
        self.client_id = client_id
        self._username = username
        self._password = password
        self._client = client  # injectable sink for tests / reuse
        self._bus = None
        self._own_bus = client is None

    @property
    def api_topic(self) -> str:
        return f"{self.device_topic}/api"

    @property
    def col_topic(self) -> str:
        return f"{self.device_topic}/col"

    def _transport_spec(self) -> dict:
        """The `transports:`-shaped spec for THIS device.

        Credentials ride the spec, by reference from whatever the scene resolved
        -- this class never reads a credential store and never logs one.
        """
        spec: dict = {"type": "mqtt", "broker": self.broker, "port": self.port,
                      "client_id": self.client_id}
        if self._username:
            spec["username"] = self._username
            spec["password"] = self._password
        return spec

    def _publish(self, channel: str, payload: str) -> Any:
        if self._client is not None:
            return self._client.publish(channel, payload)
        if self._bus is None:
            from composeio import source_bus
            self._bus = source_bus.acquire_bus(
                label=self.client_id, transport_spec=self._transport_spec())
        ok = self._bus.publish_foreign(channel, payload)
        if not ok:
            logger.warning(
                "WLED frame to %s was NOT delivered — this transport cannot "
                "carry a foreign channel, or it is not connected", channel)
        return ok

    def send(self, state: dict) -> Any:
        return self._publish(self.api_topic, json.dumps(state))

    def send_color(self, rgb_or_hex: Any) -> Any:
        """Publish a bare `#rrggbb` to `<deviceTopic>/col` (WLED convenience)."""
        if isinstance(rgb_or_hex, (list, tuple)):
            payload = "#%02x%02x%02x" % (int(rgb_or_hex[0]), int(rgb_or_hex[1]), int(rgb_or_hex[2]))
        else:
            payload = str(rgb_or_hex)
        return self._publish(self.col_topic, payload)

    def close(self) -> None:
        if self._bus is not None and self._own_bus:
            try:
                self._bus.stop()
            except Exception:
                pass

    def __repr__(self) -> str:
        return f"WLEDMQTTSender(api_topic={self.api_topic!r}, broker={self.broker!r})"
