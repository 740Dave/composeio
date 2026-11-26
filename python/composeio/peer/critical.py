"""Critical-ack origin + designated-acker machinery ([AMEND:BP-ACK]).

THE correctness bar ([BPR-CRITICAL-ACK]): a ``pri: critical`` message MUST
resolve at the ORIGIN as a genuine end-to-end ack OR an explicit
``_bus/fault`` degrade. There is NO path in which an ack-pending entry clears
without one of those two. The peer is a *transparent forwarder* — it never
synthesizes an ``ok``; the origin's ack-pending table (this module) is the
single source of delivery truth across the hop.

This is the reference Python realization of the [AMEND:BP-ACK] protocol
(previously spec-only; ``MessageType.ACK`` code 8 already existed in
transport.py, the ``cid``/``_priority`` fields already rode capture/replay).
It binds to the existing bus via ``bus.publish_critical`` / ``bus.publish_ack``
and ``bus.on_message`` — no new transport authority.

``silent_clear_count`` is the conformance probe: ``_resolve`` is the SOLE
mutator that removes an ack-pending entry, and it always records a reason in
{``acked``, ``fault_timeout``, ``reject``}. The dropped-hop test asserts this
counter is 0.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from composeio.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.peer.critical")

# Default retry backoff GAPS in ms ([BP-ACK-RETRY]: 50 / 200 / 1000).
DEFAULT_BACKOFF_MS = (50, 200, 1000)


# --------------------------------------------------------------------------- #
# FRAME-STAMP HELPERS — relocated OUT of the bus kernel ([DEC-...-BUS-STANDUP-
# 2026-08-23]). Stamping `pri: critical` / `cid` / the ACK type-8 frame is
# transport QoS, not bus-core; it now lives beside the retry machinery that is
# its sole caller. The kernel provides only the generic `bus.send_message`
# addressing seam.
# --------------------------------------------------------------------------- #
def stamp_and_send_critical(bus, namespace: str, value: Any, cid: int,
                            source: str = "") -> None:
    """Stamp a ``pri: critical`` STATE frame carrying its correlation id and
    route it over whatever transport the bus carries ([AMEND:BP-ACK] BP-C13).

    Lands the value locally (tolerant of a strict engine, so the frame keeps
    flowing) and notifies pattern subscribers, exactly as the former
    ``bus.publish_critical`` did — behavior is preserved byte-for-byte.
    """
    resolved_ns = bus._resolve_namespace(namespace)
    ns = bus.ensure_namespace(resolved_ns)
    old = ns.value
    try:
        ns.publish_state(value, source=source)
    except ValueError:
        pass  # keep the frame flowing even if the local engine is strict
    bus._notify_pattern_subs(resolved_ns, ns.value, old)
    bus.send_message(BusMessage(
        namespace=resolved_ns,
        type=MessageType.STATE,
        value=value,
        source=source,
        scope="global",
        sequence=bus._next_seq(),
        data={"_priority": "critical", "_cid": cid},
    ))


def stamp_and_send_ack(bus, namespace: str, status: Any, cid: int,
                       source: str = "") -> None:
    """Stamp an application-level ``ack`` (type code 8) for a critical message,
    correlated by ``cid`` ([AMEND:BP-ACK] BP-TYPE-ACK / BP-C12)."""
    bus.send_message(BusMessage(
        namespace=namespace,
        type=MessageType.ACK,
        value=status,
        source=source,
        scope="global",
        sequence=bus._next_seq(),
        data={"_cid": cid},
    ))


@dataclass
class AckPending:
    cid: int
    namespace: str
    value: Any
    source: str
    expected: set[str]
    started: float
    acked: dict[str, str] = field(default_factory=dict)
    retry_idx: int = 0


class CriticalAckOrigin:
    """Origin-side ack-pending table + retry/fault machinery ([BP-ACK-PROTOCOL]).

    Publishes ``pri: critical`` messages via ``bus.publish_critical`` (which
    routes over whatever transport the bus carries — here, the peer channel),
    records an ack-pending entry, retries on the 50/200/1000ms schedule, and
    resolves each entry EXACTLY ONCE as ack-or-fault.
    """

    def __init__(
        self,
        bus,
        backoff_ms: tuple[int, ...] = DEFAULT_BACKOFF_MS,
        fault_grace_ms: int | None = None,
        tick_ms: int = 5,
        auto_retry: bool = True,
    ):
        self._bus = bus
        self._backoff = tuple(backoff_ms)
        # Cumulative retry offsets from send: (50, 250, 1250) for the default.
        self._retry_offsets: list[float] = []
        acc = 0.0
        for gap in self._backoff:
            acc += gap
            self._retry_offsets.append(acc)
        # Exhaustion/fault deadline: one final gap after the last retry
        # ([BP-ACK-RETRY] "Exhaustion: 1000ms after retry3").
        grace = self._backoff[-1] if fault_grace_ms is None else fault_grace_ms
        self._fault_deadline = (self._retry_offsets[-1] if self._retry_offsets else 0) + grace
        self._tick = tick_ms / 1000.0
        self._auto_retry = auto_retry

        self._table: dict[int, AckPending] = {}
        self._lock = threading.RLock()
        self._cid_counter = 0

        # Conformance probe + audit trail.
        self.resolutions: dict[int, str] = {}
        self.silent_clear_count = 0
        self.faults: list[dict] = []

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        bus.on_message(self._on_bus_message)

    # --- publish -----------------------------------------------------------

    def _next_cid(self) -> int:
        # 12-bit rolling counter ([BP-ACK-CAN-CRITICAL]: wraps at 4095).
        self._cid_counter = (self._cid_counter + 1) % 4096
        return self._cid_counter

    def publish_critical(self, namespace: str, value: Any, ack_from: list[str],
                         source: str = "origin") -> int:
        """Publish a critical message and open an ack-pending entry.

        ``ack_from`` is the publisher-declared set of designated ackers
        ([BP-ACK-WHO]) — NOT derived from the subscriber registry.
        """
        if not ack_from:
            raise ValueError(
                "critical publish requires at least one designated acker "
                "([BP-ACK-WHO]); a critical namespace with no expected acker "
                "is a config error")
        cid = self._next_cid()
        with self._lock:
            self._table[cid] = AckPending(
                cid=cid, namespace=namespace, value=value, source=source,
                expected=set(ack_from), started=time.monotonic(),
            )
        stamp_and_send_critical(self._bus, namespace, value, cid, source=source)
        if self._auto_retry:
            self._ensure_thread()
        return cid

    # --- ack ingress -------------------------------------------------------

    def _on_bus_message(self, msg) -> None:
        if msg.type != MessageType.ACK:
            return
        cid = msg.data.get("_cid")
        if cid is None:
            return
        # An ack crossing the peer arrives with a provenance prefix
        # (``peer:<id>/<module>``); the ack-pending table keys on the raw
        # designated-acker module id ([BP-ACK-WHO]), so strip it.
        src = msg.source
        if src.startswith("peer:") and "/" in src:
            src = src.split("/", 1)[1]
        with self._lock:
            entry = self._table.get(cid)
            if entry is None:
                return  # already resolved (late/duplicate ack) — ignore
            if src not in entry.expected:
                return  # ack from a non-designated module — not our truth
            status = _ack_status(msg.value)
            if status == "error":
                return  # transient; keep retrying ([BP-ACK] error != ack)
            entry.acked[src] = status
            if status == "reject":
                self._emit_reject_fault(entry, src, msg.value)
                self._resolve(cid, "reject")
                return
            # status == ok
            if all(entry.acked.get(m) == "ok" for m in entry.expected):
                self._resolve(cid, "acked")

    # --- retry / fault -----------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump_loop, name="crit-ack",
                                        daemon=True)
        self._thread.start()

    def _pump_loop(self) -> None:
        while not self._stop.is_set():
            self.pump()
            if self._stop.wait(self._tick):
                return

    def pump(self, now: float | None = None) -> None:
        """Advance retry/fault timers. Public so tests can drive deterministically
        with ``auto_retry=False``."""
        now = time.monotonic() if now is None else now
        due_retries: list[AckPending] = []
        due_faults: list[AckPending] = []
        with self._lock:
            for entry in list(self._table.values()):
                elapsed = (now - entry.started) * 1000.0
                while (entry.retry_idx < len(self._retry_offsets)
                       and elapsed >= self._retry_offsets[entry.retry_idx]):
                    entry.retry_idx += 1
                    due_retries.append(entry)
                if elapsed >= self._fault_deadline:
                    due_faults.append(entry)
        # Re-publish OUTSIDE the lock (same cid — [BP-ACK-RETRY]). Faults win
        # over a same-tick retry.
        fault_cids = {e.cid for e in due_faults}
        for entry in due_retries:
            if entry.cid in fault_cids:
                continue
            with self._lock:
                if entry.cid not in self._table:
                    continue
            stamp_and_send_critical(self._bus, entry.namespace, entry.value, entry.cid,
                                       source=entry.source)
        for entry in due_faults:
            with self._lock:
                if entry.cid not in self._table:
                    continue
            self._emit_timeout_fault(entry)
            self._resolve(entry.cid, "fault_timeout")

    # --- resolution (SOLE mutator that removes an entry) -------------------

    def _resolve(self, cid: int, reason: str) -> None:
        with self._lock:
            entry = self._table.pop(cid, None)
            if entry is None:
                # Removed by something other than _resolve — a SILENT clear.
                # By construction this never happens; the probe proves it.
                self.silent_clear_count += 1
                logger.error(f"SILENT CLEAR of cid={cid} — conformance failure")
                return
            self.resolutions[cid] = reason

    def _emit_timeout_fault(self, entry: AckPending) -> None:
        unacked = sorted(m for m in entry.expected if entry.acked.get(m) != "ok")
        acked = sorted(m for m in entry.expected if entry.acked.get(m) == "ok")
        fault = {
            "fault": "ack_timeout",
            "target_ns": entry.namespace,
            "original_cid": entry.cid,
            "original_val": entry.value,
            "original_src": entry.source,
            "unacked": unacked,
            "acked": acked,
            "retries": entry.retry_idx,
            "elapsed_ms": int((time.monotonic() - entry.started) * 1000),
        }
        self.faults.append(fault)
        self._bus.publish_event("_bus/fault", "fault", data=fault, source=entry.source)

    def _emit_reject_fault(self, entry: AckPending, rejected_by: str, val: Any) -> None:
        reason = val.get("reason") if isinstance(val, dict) else None
        fault = {
            "fault": "ack_reject",
            "target_ns": entry.namespace,
            "original_cid": entry.cid,
            "original_val": entry.value,
            "rejected_by": rejected_by,
            "reject_reason": reason,
        }
        self.faults.append(fault)
        self._bus.publish_event("_bus/fault", "fault", data=fault, source=entry.source)

    # --- introspection -----------------------------------------------------

    def resolution(self, cid: int) -> str | None:
        return self.resolutions.get(cid)

    def pending_cids(self) -> list[int]:
        with self._lock:
            return list(self._table)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        try:
            self._bus.off_message(self._on_bus_message)
        except Exception:
            pass


def _ack_status(val: Any) -> str:
    if isinstance(val, dict):
        return val.get("status", "ok")
    return val if val in ("ok", "reject", "error") else "ok"


class CriticalAcker:
    """A designated acker ([BP-ACK-SUBSCRIBER]) — confirms receipt+processing
    of a critical message on its namespace by publishing an ``ack``.

    Idempotent per [BP-ACK-IDEMPOTENT]: a duplicate ``cid`` is NOT re-executed;
    it is re-acked from the processed-cid cache. This is what makes a retry
    (same cid) after a dropped hop converge without double-actuation.

    NOTE: acking is a *delivery confirmation*, distinct from *actuation*.
    Driving real hardware from relayed input is separately gated by
    ``ActuationGuard`` ([BPR-PROVENANCE]).
    """

    CACHE_MAX = 32          # [BP-C16]: >= 32 entries
    CACHE_EXPIRY_S = 10.0   # 10-second expiry

    def __init__(self, bus, namespace: str, module_id: str,
                 handler: Callable[[str, Any], Any] | None = None):
        self._bus = bus
        self._ns = namespace
        self._module_id = module_id
        self._handler = handler
        self._cache: OrderedDict[int, tuple[str, float]] = OrderedDict()
        self.processed = 0
        self.reacks = 0
        bus.on_message(self._on_msg)

    def _on_msg(self, msg) -> None:
        if msg.type != MessageType.STATE:
            return
        if msg.data.get("_priority") != "critical":
            return
        if msg.namespace != self._ns:
            return
        cid = msg.data.get("_cid")
        if cid is None:
            return
        self._expire()
        if cid in self._cache:
            # Duplicate critical (a retry) — re-ack, do NOT re-execute.
            self.reacks += 1
            status = self._cache[cid][0]
            stamp_and_send_ack(self._bus, msg.namespace, status, cid, source=self._module_id)
            return
        status: Any = "ok"
        if self._handler is not None:
            try:
                r = self._handler(msg.namespace, msg.value)
            except Exception:
                status = "error"
                r = None
            else:
                if isinstance(r, tuple):
                    status = {"status": r[0], "reason": r[1]} if len(r) > 1 else r[0]
                elif r in ("ok", "reject", "error"):
                    status = r
        cache_status = status.get("status") if isinstance(status, dict) else status
        self._cache[cid] = (status if isinstance(status, str) else cache_status,
                            time.monotonic())
        if len(self._cache) > self.CACHE_MAX:
            self._cache.popitem(last=False)
        self.processed += 1
        stamp_and_send_ack(self._bus, msg.namespace, status, cid, source=self._module_id)

    def _expire(self) -> None:
        now = time.monotonic()
        stale = [c for c, (_, t) in self._cache.items()
                 if now - t > self.CACHE_EXPIRY_S]
        for c in stale:
            self._cache.pop(c, None)

    def close(self) -> None:
        try:
            self._bus.off_message(self._on_msg)
        except Exception:
            pass
