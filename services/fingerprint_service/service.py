"""fingerprint_service — the service facade that wires the superset authority,
push origination, reconciliation, and enroll orchestration onto ComposeBus.

The service is an ordinary bus client ([CXC-FPSVC-STATION-EDITOR] not-this): it
originates the semantic push and the semantic enroll command, and it SUBSCRIBES
to device state. It never calls a method on a sensor and never queries a device.

Device discovery is by the retained announce ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]):
in a live deploy a wildcard announce subscription drives :meth:`follow_device`;
in-process a test calls it directly. Once following a device, the service
subscribes to that device's heartbeats, push acks, enroll readback, and
auth_event — all EVENTS, all device-addressed.

The bus is INJECTED (``attach``), not constructed here: the connect is
config-driven and lives in :mod:`.__main__` (deploy path, glass-pending).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import topics
from .enroll import EnrollOrchestrator
from .roster_push import PushResult, RosterPublisher
from .store import SupersetStore

# The closed set of device-emitted message TAILS this service understands
# ([CXC-FPBUS-ADDRESSING]). Order matters only for readability; matching is by
# exact suffix, not position. See `_split_device_namespace` for why this list
# — not a left-hand split on the first '/' — is what recovers `device_id` from
# an inbound `<device_id>/<tail>` namespace.
_EVENT_TAILS: tuple[str, ...] = (
    "announce",
    "fingerprint_ready",
    "fingerprint_push_needed",
    "fingerprint_inoperable",
    "template_set_loaded",
    "template_set_failed",
    "result/enroll/complete",
    "auth_event",
)


@dataclass
class FingerprintService:
    """The whole service, minus its transport. Give it a bus with
    ``subscribe_events`` + ``publish_event`` (an in-process ``ComposeIOBus`` or a
    bridged one) and follow the devices it should reconcile."""

    clock: Callable[[], float] = time.time
    source: str = "fingerprint_service"
    # The template-store PUT seam, forwarded to the RosterPublisher unchanged
    # ([DEFECT-ROSTERPUBLISHER-PUSH-TO-DEVICES-RAISES-69DC18]). None (the
    # default) leaves RosterPublisher's own default in place — the REAL
    # dedicated store via composeio.resolve.put_bytes; a test overrides with
    # an in-memory fake so the reference/integration suites never need a live
    # HTTP server.
    template_store_put: Callable[[str, bytes], str] | None = None
    # The template-store GET seam, forwarded to the EnrollOrchestrator
    # unchanged ([DEFECT-ENROLL-PY-ENROLL-COMPLETE-READBACK-A4128F] — the
    # mirror, DEVICE->SERVICE, of the seam above). None (the default) leaves
    # EnrollOrchestrator's own default in place — the REAL dedicated store via
    # composeio.resolve.resolve_bytes; a test overrides with an in-memory fake
    # (the SAME fake a test's device-side harness uploads to via its own
    # template_store_put) so the enroll suites never need a live HTTP server.
    template_resolver: Callable[[str], bytes] | None = None
    store: SupersetStore = field(init=False)
    publisher: RosterPublisher = field(init=False)
    enroller: EnrollOrchestrator = field(init=False)
    _bus: Any = field(default=None, init=False)
    _followed: set[str] = field(default_factory=set, init=False)
    # device -> announced capabilities ([CXC-FPBUS-MSG-DEVICE-ANNOUNCE]); the
    # service reads these SECOND-HAND from the announce, never by calling
    # capabilities() ([CXC-FPID-POPULATION-HEADROOM]).
    _capabilities: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    # devices flagged unconverged by the reconciler, awaiting a re-push.
    _repush_due: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        self.store = SupersetStore(clock=self.clock)
        publisher_kwargs: dict[str, Any] = dict(
            store=self.store,
            bus=_Deferred(self),
            source=self.source,
            # the publisher reads announced capabilities SECOND-HAND, from the
            # SAME dict this service fills from the announce ([CXC-FPBUS-MSG-
            # DEVICE-ANNOUNCE]) — never a query, never a constant.
            capabilities_lookup=self._capabilities.get,
        )
        if self.template_store_put is not None:
            publisher_kwargs["template_store_put"] = self.template_store_put
        self.publisher = RosterPublisher(**publisher_kwargs)
        enroller_kwargs: dict[str, Any] = dict(
            store=self.store,
            bus=_Deferred(self),
            source=self.source,
            readback_capable=self._readback_capable,
        )
        if self.template_resolver is not None:
            enroller_kwargs["template_resolver"] = self.template_resolver
        self.enroller = EnrollOrchestrator(**enroller_kwargs)

    # ---- transport ------------------------------------------------------

    def attach(self, bus: Any) -> None:
        """Inject the bus. Publishing goes through it; subscriptions are wired
        per-device by :meth:`follow_device`."""
        self._bus = bus

    def follow_device(self, device_id: str) -> None:
        """Begin reconciling a device: subscribe to its heartbeats, acks, enroll
        readback, announce, and auth_event. Idempotent."""
        if self._bus is None:
            raise RuntimeError("attach(bus) before follow_device()")
        if device_id in self._followed:
            return
        self._followed.add(device_id)
        b = self._bus
        b.subscribe_events(topics.device_announce(device_id), self._on_event)
        b.subscribe_events(topics.fingerprint_ready(device_id), self._on_event)
        b.subscribe_events(topics.fingerprint_push_needed(device_id), self._on_event)
        b.subscribe_events(topics.fingerprint_inoperable(device_id), self._on_event)
        b.subscribe_events(topics.template_set_loaded(device_id), self._on_event)
        b.subscribe_events(topics.template_set_failed(device_id), self._on_event)
        b.subscribe_events(topics.enroll_complete(device_id), self._on_event)
        b.subscribe_events(topics.auth_event(device_id), self._on_event)

    # ---- the single event router ----------------------------------------

    @staticmethod
    def _split_device_namespace(namespace: str) -> tuple[str, str]:
        """Recover ``(device_id, tail)`` from a device-addressed namespace
        ``<device_id>/<tail>`` ([CXC-FPBUS-ADDRESSING]).

        RIGHT-ANCHORED against the closed set of message tails this service
        understands (``_EVENT_TAILS``) — NEVER a left-hand ``partition("/")``
        on the first slash. ``device_id`` is an OPAQUE identifier everywhere
        else it is used on this bus (:mod:`.topics` and the reference device
        twin both build ``f"{device_id}/{message}"`` treating it as any
        string), and at least one live device's id legitimately contains a
        '/' of its own (the firmware convention ``"dev/<name>"``, e.g.
        ``"dev/s3-fp1"``). A left-partition split is NOT the true inverse of
        that build: for ``"dev/s3-fp1/fingerprint_ready"`` it yields
        ``device_id="dev"``, ``tail="s3-fp1/fingerprint_ready"`` — which
        matches none of this router's branches, so the event is silently
        dropped and `RosterPublisher.on_fingerprint_ready` (the ONLY place
        the service learns a held uuid) is never called. That is invisible
        in-process because every existing test's device ids are flat
        (``"dev-1"``, ``"dev-big"``, ...); it reproduces over live MQTT with
        any device whose id embeds a '/', with wire traffic that is
        provably correct and service bookkeeping that never updates.

        A namespace whose tail is not in the known set falls back to the
        historical single-segment split, so a genuinely novel flat
        ``device_id/tail`` still routes rather than being refused outright.
        """
        for tail in _EVENT_TAILS:
            suffix = "/" + tail
            if namespace.endswith(suffix):
                return namespace[: -len(suffix)], tail
        device_id, _, tail = namespace.partition("/")
        return device_id, tail

    def _on_event(self, namespace: str, event_type: str, data: dict) -> None:
        """One dispatcher for every device-addressed event."""
        device_id, tail = self._split_device_namespace(namespace)
        data = data or {}
        if tail == "announce" or event_type == "announce":
            self._capabilities[device_id] = dict(data.get("capabilities", data))
        elif tail == "fingerprint_ready":
            if self.publisher.on_fingerprint_ready(device_id, data):
                self._repush_due.add(device_id)
            else:
                self._repush_due.discard(device_id)
        elif tail == "fingerprint_push_needed":
            if self.publisher.on_fingerprint_push_needed(device_id):
                self._repush_due.add(device_id)
        elif tail == "template_set_loaded":
            self.publisher.on_template_set_loaded(device_id, data)
        elif tail == "template_set_failed":
            self.publisher.on_template_set_failed(device_id, data)
        elif tail == "result/enroll/complete":
            self.enroller.on_enroll_complete(device_id, data)
        elif tail == "auth_event":
            self._on_auth_event(device_id, data)

    def _on_auth_event(self, device_id: str, data: dict) -> None:
        """Advance the retention clock for the resolved player. granted/denied
        carry a player; unrecognized carries none and touches nothing
        ([DD-FPSVC-INACTIVITY-CLOCK], [CXC-FPBUS-MSG-AUTH-EVENT])."""
        player = data.get("player")
        if player:
            self.store.record_auth_attempt(player, at=data.get("timestamp"))

    # ---- capability helpers ---------------------------------------------

    def _readback_capable(self, device_id: str) -> bool | None:
        caps = self._capabilities.get(device_id)
        if caps is None or "supports_readback" not in caps:
            return None
        return bool(caps["supports_readback"])

    # ---- the operations a trigger/UI calls ------------------------------

    def start_game(self, module: str, devices: list[str]) -> PushResult:
        """[CXC-FPSVC-PUSH-TRIGGER] — starting a game decides the cast, so the
        push is originated here. Every device in scope gets the same cast."""
        for d in devices:
            self.follow_device(d)
        result = self.publisher.push_to_devices(module, devices)
        for d in devices:
            self._repush_due.discard(d)
        return result

    def reconcile_pending(self) -> list[str]:
        """The devices the reconciler flagged unconverged (held uuid != desired,
        or a push_needed heartbeat). Learned by SUBSCRIPTION; never a query."""
        return sorted(self._repush_due)

    def reconcile_and_repush(self) -> list[str]:
        """Re-push every device flagged unconverged and return the ids re-pushed.
        The subscription-driven repair loop: a stale ``fingerprint_ready`` or a
        ``fingerprint_push_needed`` is what put a device on this list, and the
        device confirms convergence on its next heartbeat — no device is ever
        queried ([CXC-FPSVC-RECONCILE-BY-SUBSCRIPTION])."""
        repushed: list[str] = []
        for device_id in sorted(self._repush_due):
            result = self.publisher.repush_device(device_id)
            if result is not None and result.published:
                repushed.append(device_id)
        return repushed

    def sweep_retention(self, *, now: float | None = None) -> list[str]:
        return self.store.sweep_retention(now=now)


class _Deferred:
    """A publish seam that forwards to whatever bus is currently attached, so
    the publisher/enroller can be built before ``attach``. Matches
    ``publish_event`` only — the sub-components never subscribe."""

    def __init__(self, service: "FingerprintService"):
        self._service = service

    def publish_event(self, namespace, event_type, data=None, source=""):
        bus = self._service._bus
        if bus is None:
            raise RuntimeError("attach(bus) before publishing")
        bus.publish_event(namespace, event_type, data, source=source)
