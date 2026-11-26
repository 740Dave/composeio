"""ComposeBus client for print_services — over the transport SEAM, not raw paho.

DP-CIO-6: transport is an implementation detail. This service speaks the SAME
wire envelope every other ComposeIO peer speaks, so bus monitors, Intelligences
and humans read `print_services/*` with no special case — it is just another
transmitter on the bus.

WHY THIS RIDES THE PLATFORM WAIST, NOT A PRIVATE PAHO CLIENT.
  The previous `BusClient` was a hand-rolled MQTTv5 paho client whose envelope
  and `composeio/{scope}/{namespace}` topic map were, per its own docstring,
  copied "by REFERENCE from transport.py/transport_mqtt.py; keep in sync by
  hand". That is the exact DP-CIO-6 violation the transport seam exists to
  remove: two copies of the wire format that drift silently, and a service that
  hardcodes MQTT and so publishes into the void on a CAN-only / UART-only device.

  This module now names NO substrate and imports NO paho. It hands an
  `mqtt://host:port` URL to `composeio.source_bus.acquire_bus`, which builds the
  transport through `transport_registry` (an unknown `type:` RAISES; a known type
  whose broker is merely unreachable degrades LOUDLY to local-only). Every
  publish goes through `ComposeIOBus.publish_state` / `.publish_event`, so the
  topic mapping, the envelope, the retain-by-type table and the scope semantics
  are the platform's, reused by reference — mirroring
  `services/document_store/bus.py` and `services/bus_storage/change_notify.py`.

  The DEVICE backends (HP IPP, Elegoo SDCP, the HTTP status server) stay PRIVATE
  inside print_services — they are legitimately not bus. Only the bus transport
  changed here.

BEHAVIOR-PRESERVING PORT — the namespaces and their retained/event semantics are
identical to the hand-rolled client:
  * `print_services/registry/<id>` — RETAINED STATE (age-contract heartbeat),
    cleared on graceful shutdown.
  * `print_services/<id>/submit`   — inbound STATE, consumed here.
  * `print_services/<id>/result`   — transient EVENT (not retained).
  * `print_services/<id>/status`   — RETAINED STATE.
STATE retains (QoS 1) and EVENT is transient (QoS 0) come from the platform's
`RETAIN_BY_TYPE` / `_QOS_MAP`, so they are inherent now rather than hand-set.

TWO PLATFORM-CANONICAL SHAPES REPLACE HAND-ROLLED ONES (see the port FINDINGS in
the handover):
  * TOMBSTONE. The old client cleared a retained registry row with a true
    zero-length MQTT retain-clear (`publish(topic, b"", retain=True)`). The
    platform's own idiom (`countdown.py::cancel`) is a retained `None` instead,
    BECAUSE a zero-length payload raises in `BusMessage.from_json` on every
    listener on this stack. So `publish_tombstone` now publishes a retained
    `None` — "cleared" in vocabulary the stack already reads — which is both
    seam-native and strictly safer than the byte it replaced.
  * RESULT EVENT. The platform event envelope carries its payload in `data`
    (with an `evt` type tag), not in `val`; `bus.publish_event(ns, "result",
    data=result)` is exactly how the named reference `document_store` publishes.
    The one wire delta from the old hand-rolled event (`val` -> `data`+`evt`) has
    zero in-repo consumers.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable
from urllib.parse import urlparse

logger = logging.getLogger("print_services.bus")


def parse_mqtt_url(url: str) -> tuple[str, int]:
    """`mqtt://host:port` -> (host, port). Bare `host:port` and `host` also work.

    Kept as a tiny stdlib helper for the HTTP status snapshot; the bus itself
    hands the URL straight to `acquire_bus`, which does its own parsing through
    `source_bus.mqtt_spec`.
    """
    if "://" not in url:
        url = "mqtt://" + url
    u = urlparse(url)
    host = u.hostname or "127.0.0.1"
    port = u.port or 1883
    return host, port


class BusClient:
    """print_services' bus face over the ComposeBus transport seam.

    Same public surface the hand-rolled paho client exposed — `connect`,
    `disconnect`, `connected`, `subscribe`, `publish_state`, `publish_event`,
    `publish_tombstone` — so `server.py` is unchanged. The difference is entirely
    below this surface: the wire is the platform's, reached through
    `composeio.source_bus`, and no `paho` is imported anywhere in print_services.
    """

    def __init__(self, mqtt_url: str, client_id: str, source: str):
        self.mqtt_url = mqtt_url
        # `client_id` is retained for the HTTP status snapshot / log identity;
        # the seam names the bus by `label` (the source), which is the broker-
        # side identity now.
        self.client_id = client_id
        self.source = source
        self._bus = None
        self._declared: set[str] = set()

    # -- lifecycle ---------------------------------------------------------

    def connect(self, connect_wait_s: float = 10.0) -> bool:
        """Acquire the bus through the seam and wait (bounded) for the transport.

        Returns True only when the external transport actually CONNECTED — so a
        caller that gets False runs HTTP-only, exactly as before. A
        broker-unreachable / local-only bus degrades LOUDLY (the
        `transport_registry` rule) and returns False; the background paho loop
        the transport owns keeps retrying, but this service does not wire the bus
        on a dead connection.
        """
        try:
            from composeio.source_bus import acquire_bus
        except Exception as exc:  # noqa: BLE001 — no composeio on path -> HTTP-only
            logger.error("composeio bus stack unavailable (%s) — bus DISABLED, "
                         "service will run HTTP-only", exc)
            return False

        try:
            # `label` is the source's broker-side identity; the seam memoizes one
            # transport per broker for the whole process (this service is the
            # only bus user, so that is simply "one transport").
            self._bus = acquire_bus(self.mqtt_url, label=self.source)
        except Exception as exc:  # noqa: BLE001 — unknown transport type raises here
            logger.error("acquire_bus(%s) failed: %s — bus DISABLED, HTTP-only",
                         self.mqtt_url, exc)
            self._bus = None
            return False

        transport = getattr(self._bus, "global_transport", None)
        if transport is None:
            logger.error("bus for %s is LOCAL-ONLY (no external transport) — "
                         "nothing published would leave the process; running "
                         "HTTP-only", self.mqtt_url)
            return False

        deadline = time.monotonic() + connect_wait_s
        while time.monotonic() < deadline:
            if getattr(transport, "is_connected", False):
                logger.info("bus connected: %s (via transport seam)", self.mqtt_url)
                return True
            time.sleep(0.05)
        logger.error("bus connect to %s timed out after %.1fs — running HTTP-only",
                     self.mqtt_url, connect_wait_s)
        return False

    def disconnect(self):
        if self._bus is not None:
            try:
                self._bus.stop()
            except Exception:  # noqa: BLE001 — teardown must never raise
                pass

    @property
    def connected(self) -> bool:
        transport = getattr(self._bus, "global_transport", None) if self._bus else None
        return bool(transport is not None and getattr(transport, "is_connected", False))

    # -- namespace declaration --------------------------------------------

    def _declare(self, namespace: str) -> None:
        """Declare `namespace` GLOBAL with the json engine, exactly once.

        GLOBAL (explicit, never AUTO) so the message leaves the process onto MQTT
        and never hits `_resolve_scope`'s local-if-self-subscribed heuristic
        ([DEC-COMPOSER-SEND-GLOBAL-SCOPE]). json because these payloads are
        free-form dicts/None — a strict engine would reject them, and an empty /
        None value under `binary` is the engine-mismatch SIGABRT hazard.
        """
        if self._bus is None or namespace in self._declared:
            return
        from composeio.source_bus import declare
        from composeio.namespace import Scope
        declare(self._bus, namespace, scope=Scope.GLOBAL, engine="json",
                source=self.source)
        self._declared.add(namespace)

    # -- publish -----------------------------------------------------------

    def publish_state(self, namespace: str, value: Any, scope: str = "global",
                      retain: bool = True):
        """Publish a retained STATE (current value) — registry, status.

        `scope`/`retain` are accepted for call-site compatibility; scope is pinned
        GLOBAL at declaration and retain is inherent to STATE via the platform's
        `RETAIN_BY_TYPE` table, so they are not re-derived here.
        """
        if self._bus is None:
            logger.warning("publish_state before connect, dropping %s", namespace)
            return
        self._declare(namespace)
        self._bus.publish_state(namespace, value, source=self.source)

    def publish_event(self, namespace: str, value, scope: str = "global"):
        """Publish a transient EVENT (not retained) — job results.

        Mirrors `document_store`: the event payload rides `data` with an `evt`
        type tag (here `"result"`), which is the platform-canonical event shape.
        """
        if self._bus is None:
            logger.warning("publish_event before connect, dropping %s", namespace)
            return
        self._declare(namespace)
        self._bus.publish_event(namespace, "result", data=value, source=self.source)

    def publish_tombstone(self, namespace: str, scope: str = "global"):
        """Clear a retained namespace on graceful shutdown.

        Publishes a retained `None` — the platform idiom (`countdown.py::cancel`)
        — NOT a zero-length MQTT retain-clear: on this stack every subscriber runs
        `BusMessage.from_json`, and a zero-length payload raises there, turning one
        shutdown into an exception on every listener. A retained `None` says
        "cleared" in vocabulary the stack already reads, and a future subscriber
        sees a null registry value rather than a stale row.
        """
        if self._bus is None:
            return
        self._declare(namespace)
        self._bus.publish_state(namespace, None, source=self.source)

    # -- subscribe ---------------------------------------------------------

    def subscribe(self, namespace: str, callback: Callable):
        """Subscribe to a namespace's inbound STATE (the `/submit` idiom).

        `callback(value, raw)` — the decoded value and a raw-envelope slot, kept
        for signature compatibility with the old client (`server.py`'s handler
        ignores `raw`). The seam opens the `composeio/+/{namespace}` broker
        subscription (both scopes) and routes inbound STATE here via the bus's
        `_dispatch_incoming`; our own publishes are dropped by the bus's
        self-echo guard, so a submit handler never hears its own results.
        """
        if self._bus is None:
            logger.warning("subscribe before connect, dropping %s", namespace)
            return
        self._declare(namespace)

        def _on_state(_name: str, new_value, _old_value):
            callback(new_value, {})

        self._bus.subscribe(namespace, _on_state)
