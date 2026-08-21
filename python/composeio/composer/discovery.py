"""BusDiscovery — live ComposeBus namespace observation ([SPEC:CXC-FLOW]).

The "not deaf to the outside world" piece ([CXC-FL-DISCOVERY]): observes
EVERY namespace speaking on the Composer's design/external bus — including
traffic this design never declared (an ESP32 emitting ``jumpdrive/readiness``,
another app's exports, a simulator) — and keeps a registry the Bus·Live
rail, the Flow graph, the Bindings-tab live browse, and the Intelligence
state snapshot all read.

Mechanics:
    - ``bus.subscribe_pattern("*", ...)`` — the new trailing-glob bus
      primitive.  On MQTT this widens reception to ``composeio/#``, so
      external namespaces arrive without any prior registration.
    - ``bus.on_message`` — liveness for events/logs (not just state), and
      the publisher's source id.
    - ``_bus/discovery`` events — peers announce their exported namespaces
      WITH engine types (the additive ``engines`` field, [CXC-FL-ENGINE]),
      so a discovered signal is legible, not just visible.

State-engine legibility ([CXC-FL-ENGINE]) resolves in priority order:
    declared (this design's own NamespaceDef) > peer (_bus/discovery)
    > inferred from observed values (marked '~', honest about guessing).

Thread model: bus callbacks arrive on transport threads; the registry
mutates under a lock and the UI reads immutable snapshot() copies.

Tool/infrastructure namespaces (``_bus/*``, ``_app/*``, ``composer/*``)
are design-tool plumbing, not design signals — excluded from discovery.
"""

from __future__ import annotations

import fnmatch
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from composeio.bus import is_pattern, pattern_matches

# prefixes that are tool plumbing, never design signals
_IGNORED_PREFIXES = ("_bus/", "_app/", "composer/")

# "active" category window ([CXC-FL-FILTER]): seconds since last traffic
ACTIVE_WINDOW_S = 10.0


def _ignored(name: str) -> bool:
    return any(name.startswith(p) for p in _IGNORED_PREFIXES) \
        or name in ("_bus", "_app")


def infer_engine(value: Any) -> str:
    """Honest engine guess from an observed value — prefixed '~' by the
    caller so an inference never masquerades as a declaration."""
    if isinstance(value, bool):
        return "binary"
    if isinstance(value, (dict, list)):
        return "json"
    if isinstance(value, (int, float)):
        if 0 <= float(value) <= 255:
            return "continuous_0_255"
        return "numeric"
    s = str(value).strip().lower()
    if s in ("on", "off", "true", "false", "0", "1", "yes", "no"):
        return "binary"
    return "string"


@dataclass
class NamespaceInfo:
    """One observed (or declared/expected) namespace."""
    name: str
    engine: str = ""              # display string; '~' prefix = inferred
    engine_source: str = ""       # declared | peer | inferred | ""
    value: Any = None
    first_seen: float = 0.0       # 0 = never observed (declare-expected)
    last_seen: float = 0.0
    count: int = 0                # messages observed
    source: str = ""              # last publisher id
    app_id: str = ""              # peer app (from _bus/discovery)

    @property
    def live(self) -> bool:
        return self.first_seen > 0.0

    def active(self, now: Optional[float] = None) -> bool:
        return self.live and ((now or time.time()) - self.last_seen
                              <= ACTIVE_WINDOW_S)


class BusDiscovery:
    """The Bus·Live registry over the Composer's design/external bus."""

    def __init__(self, core: Any) -> None:
        self.core = core
        self._lock = threading.Lock()
        self._entries: dict[str, NamespaceInfo] = {}
        self._peer_engines: dict[str, tuple[str, str]] = {}  # ns -> (engine, app)
        self._listeners: List[Callable[[], None]] = []
        self._started = False
        # [CXC-FLAUTH-CONFIRM] headless runtime heartbeats: _bus/headless/
        # <rid>/health payloads keyed by runtime_id.  Tracked HERE (tool
        # telemetry, not a design signal) so the rail/graph stay clean
        # while the deploy card reads liveness.  runtime_id -> (payload,
        # wall-clock arrival).
        self._headless_health: dict[str, tuple] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        bus = self.core.design_bus
        # every state publish, every namespace, incl. ones appearing later
        bus.subscribe_pattern("*", self._on_state)
        # liveness beyond state (events/logs) + publisher source
        bus.on_message(self._on_bus_message)
        # peer exports announce engines ([CXC-FL-ENGINE])
        bus.subscribe_events("_bus/discovery", self._on_peer_discovery)
        # seed with anything the bus already holds
        for name, ns in bus.namespaces.items():
            if _ignored(name):
                continue
            with self._lock:
                e = self._entries.setdefault(name, NamespaceInfo(name=name))
                if ns.value is not None and not e.live:
                    e.value = ns.value
                    e.first_seen = e.last_seen = time.time()
                    e.count += 1
        self._started = True

    def stop(self) -> None:
        bus = self.core.design_bus
        try:
            bus.unsubscribe_pattern("*", self._on_state)
        except Exception:
            pass
        try:
            bus.off_message(self._on_bus_message)
        except Exception:
            pass
        self._started = False

    def on_update(self, fn: Callable[[], None]) -> None:
        """UI hook: called (on the CALLBACK thread) after registry changes —
        marshal to the render thread yourself."""
        self._listeners.append(fn)

    # -- bus callbacks (transport threads) ------------------------------------

    def _touch(self, name: str, value: Any, source: str = "") -> None:
        if name.startswith("_bus/headless/") and name.endswith("/health"):
            # [CXC-FLAUTH-CONFIRM] the runtime heartbeat — recorded, never
            # surfaced as a design signal.  (_bus/headless/<id>/* beyond
            # /health is the RESERVED Pass-4 Diagnosis family.)
            rid = name[len("_bus/headless/"):-len("/health")]
            with self._lock:
                self._headless_health[rid] = (value, time.time())
            self._notify()
            return
        if _ignored(name) or is_pattern(name):
            return
        now = time.time()
        with self._lock:
            e = self._entries.get(name)
            if e is None:
                e = NamespaceInfo(name=name)
                self._entries[name] = e
            if value is not None:
                e.value = value
            if not e.first_seen:
                e.first_seen = now
            e.last_seen = now
            e.count += 1
            if source:
                e.source = source
        self._notify()

    def _on_state(self, namespace: str, new_value: Any, old_value: Any) -> None:
        self._touch(namespace, new_value)

    def _on_bus_message(self, msg: Any) -> None:
        try:
            type_str = getattr(getattr(msg, "type", None), "value", "")
            if type_str in ("heartbeat", "time_sync"):
                return
            self._touch(msg.namespace, getattr(msg, "value", None),
                        getattr(msg, "source", "") or "")
        except Exception:
            pass

    def _on_peer_discovery(self, namespace: str, event_type: str,
                           data: dict) -> None:
        if not isinstance(data, dict):
            return
        app_id = str(data.get("app_id", "") or "")
        engines = data.get("engines")
        names = data.get("namespaces") or []
        with self._lock:
            for n in names:
                n = str(n)
                if _ignored(n):
                    continue
                eng = (engines or {}).get(n, "") if isinstance(engines, dict) \
                    else ""
                self._peer_engines[n] = (str(eng), app_id)
        self._notify()

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    # -- classification against the document ----------------------------------

    def _engine_for(self, e: NamespaceInfo) -> tuple[str, str]:
        """(display_engine, source) per the declared > peer > inferred order."""
        doc = self.core.document
        ns = doc.get_namespace(e.name)
        if ns is None:
            # a declared PATTERN covers its members
            for d in doc.namespaces:
                if is_pattern(d.name) and pattern_matches(d.name, e.name):
                    ns = d
                    break
        if ns is not None and not ns.external:
            eng = ns.state_engine if isinstance(ns.state_engine, str) \
                else ns.state_engine.get("type", "enum")
            return str(eng), "declared"
        peer = self._peer_engines.get(e.name)
        if peer and peer[0]:
            return peer[0], "peer"
        if ns is not None:      # subscribed-external with a declared engine
            eng = ns.state_engine if isinstance(ns.state_engine, str) \
                else ns.state_engine.get("type", "enum")
            return str(eng), "declared"
        if e.live:
            return "~" + infer_engine(e.value), "inferred"
        return "?", ""

    def kind_of(self, name: str) -> str:
        """declared-internal | subscribed-external | expected |
        live-available — the three visually-distinct kinds (+ the
        declare-expected pre-live state) of [CXC-FL-DISCOVERY]."""
        doc = self.core.document
        ns = doc.get_namespace(name)
        if ns is None:
            for d in doc.namespaces:
                if is_pattern(d.name) and pattern_matches(d.name, name):
                    ns = d
                    break
        with self._lock:
            e = self._entries.get(name)
            live = bool(e and e.live)
        if ns is not None:
            if ns.external:
                return "subscribed-external" if live else "expected"
            return "declared-internal"
        return "live-available"

    def headless_health(self, runtime_id: str) -> Optional[dict]:
        """The last heartbeat from `_bus/headless/<runtime_id>/health`
        ([CXC-FLAUTH-CONFIRM]) with its wall-clock age, or None when the
        runtime has never spoken."""
        with self._lock:
            got = self._headless_health.get(str(runtime_id))
        if got is None:
            return None
        payload, seen = got
        out = dict(payload) if isinstance(payload, dict) else {"value": payload}
        out["age_s"] = round(time.time() - seen, 2)
        return out

    def wired_namespaces(self) -> set:
        """Namespaces (and patterns) the design actually references —
        widget subscribe / publish / attr bindings / behavior triggers."""
        out = set()
        for row in self.core.document.bindings():
            ns = str(row.get("namespace", ""))
            if ns and ns != "always":
                out.add(ns)
        return out

    def is_wired(self, name: str) -> bool:
        for w in self.wired_namespaces():
            if w == name or (is_pattern(w) and pattern_matches(w, name)):
                return True
        return False

    # -- snapshots -------------------------------------------------------------

    def snapshot(self) -> List[NamespaceInfo]:
        """All entries (observed + declared-but-silent), enriched with the
        resolved engine.  Sorted: live first (most recent first), then
        expected, alphabetical within groups."""
        doc = self.core.document
        with self._lock:
            entries = {n: NamespaceInfo(**vars(e))
                       for n, e in self._entries.items()}
        # declared namespaces that never spoke still appear (declare-expected
        # + the design's own not-yet-published ones)
        for ns in doc.namespaces:
            if is_pattern(ns.name) or _ignored(ns.name):
                continue
            entries.setdefault(ns.name, NamespaceInfo(name=ns.name))
        out = list(entries.values())
        for e in out:
            e.engine, e.engine_source = self._engine_for(e)
            peer = self._peer_engines.get(e.name)
            if peer:
                e.app_id = peer[1]
        out.sort(key=lambda e: (not e.live, -e.last_seen, e.name))
        return out

    def snapshot_brief(self, limit: int = 200) -> List[dict]:
        """Intelligence-facing summary ([CXC-FL-C9] parity: what the rail
        shows a human, request_state shows a peer)."""
        now = time.time()
        out = []
        for e in self.snapshot()[:limit]:
            out.append({
                "namespace": e.name,
                "engine": e.engine,
                "value": e.value,
                "kind": self.kind_of(e.name),
                "wired": self.is_wired(e.name),
                "live": e.live,
                "active": e.active(now),
                "age_s": round(now - e.last_seen, 1) if e.live else None,
            })
        return out

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    # -- filtering ([CXC-FL-FILTER], P0) ---------------------------------------

    def filter(self, entries: List[NamespaceInfo], *,
               text: str = "", category: str = "all") -> List[NamespaceInfo]:
        """One filter language for the rail: substring OR trailing-glob on
        the name; categories all/external/internal/active/live/wired plus
        an engine-type name."""
        text = (text or "").strip()
        out = []
        now = time.time()
        for e in entries:
            if text:
                if "*" in text:
                    pat = text if is_pattern(text) else text
                    # any '*' form filters permissively via fnmatch, but the
                    # canonical trailing-glob uses bus semantics (matches
                    # the prefix itself too)
                    if is_pattern(pat):
                        if not pattern_matches(pat, e.name):
                            continue
                    elif not fnmatch.fnmatch(e.name, pat):
                        continue
                elif text.lower() not in e.name.lower():
                    continue
            if category and category != "all":
                kind = self.kind_of(e.name)
                if category == "external":
                    if kind not in ("subscribed-external", "expected",
                                    "live-available"):
                        continue
                elif category == "internal":
                    if kind != "declared-internal":
                        continue
                elif category == "active":
                    if not e.active(now):
                        continue
                elif category == "live":
                    if not e.live:
                        continue
                elif category == "wired":
                    if not self.is_wired(e.name):
                        continue
                else:   # engine-type category (binary / tri_level / ...)
                    if e.engine.lstrip("~") != category:
                        continue
            out.append(e)
        return out

    def prefix_chips(self, max_chips: int = 4) -> List[str]:
        """The most common namespace-prefix globs among live traffic —
        hierarchical namespaces give grouping for free ([CXC-FL-FILTER])."""
        counts: dict[str, int] = {}
        with self._lock:
            for e in self._entries.values():
                if "/" in e.name:
                    counts[e.name.split("/", 1)[0]] = \
                        counts.get(e.name.split("/", 1)[0], 0) + 1
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        return [f"{p}/*" for p, c in ranked[:max_chips] if c >= 2]
