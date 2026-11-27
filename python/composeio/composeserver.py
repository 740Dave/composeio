"""ComposeServer — the micro-kernel: supervise, load, config-persist,
bus-provision, and NOTHING else ([SPEC:CXC-COMPOSESERVER] [CXC-CS-MICROKERNEL]).

Two layers live here, above the ComposeBus kernel ([SPEC:CXC-COMPOSEBUS-KERNEL]):

  * ``standup(...)`` — the canonical THREE-STEP standup for ONE bus/locality
    ([CXC-CBK-STANDUP]): (1) instantiate the bare kernel with no transport,
    (2) attach every DECLARED connector (MULTI-ATTACH), (3) load plugins, each
    respecting its own ``requires:`` execution floor. The kernel itself knows
    nothing of plugins or loading — that boundary is the whole point.

  * ``ComposeServer`` — the MULTI-TENANCY outer loop. One ComposeServer MAY host
    N ``ComposeIOBus`` objects, one per tenant. Plugins are ALWAYS per-tenant;
    there are NO shared plugin instances, ever — a LOCKED core-contract invariant
    ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23] multi-tenancy). The kernel is the
    SOLE cross-tenant entity. This holds because the plugin contract is
    bus-agnostic: multi-tenancy is pure kernel orchestration.

ComposeServer is the BINDER standing on the ``requires:`` floor: it re-evaluates
continuously, PARKS a plugin whose floor is unmet (naming the specific unmet
item), and UNPARKS it with no restart the moment the floor is met
([CXC-CS-REQUIRES-FLOOR] dynamic-rebinding).

A subprocess-pinned plugin (David's Q1 ruling — a HARD ``subprocess`` floor
ComposeServer MUST honor) lives INSIDE its tenant's locality, reached over an
internal IPC connector — a daughtercard of ComposeServer over IPC, supervised
but NEVER bus-mediated. The spawning transport is Phase 1; this module resolves
the isolation and marks the seam ([CXC-CS-ISOLATION-FLOOR]).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from composeio.bus import ComposeIOBus
from composeio.bus_clock import shared_clock
from composeio.namespace import Scope
from composeio.plugin import ComposePlugin, Isolation

logger = logging.getLogger("composeio.composeserver")

# The loader re-announces on this cadence, reusing the same BusClock tick pattern
# `_bus/time` / `_bus/heartbeat` use. ANNOUNCE-BIND requires PERIODIC re-announce:
# a MISSING publication is itself the fault report, which a one-shot announce (or
# a query) cannot provide ([DEC-PHASE0-COMPOSESERVER-PUBLISHES-DERIVED-ANNOUNCE]).
SERVER_ANNOUNCE_INTERVAL_S = 5.0


# --------------------------------------------------------------------------- #
# THE ANNOUNCE-BIND SPINE — the LOADER publishes each plugin's DERIVED announce
# to `_server/plugins/<name>/*` on the plugin's tenant bus. The PLUGIN stays
# bus-agnostic and never writes announce code; the loader emits it on the
# plugin's behalf ([DEC-PHASE0-COMPOSESERVER-PUBLISHES-DERIVED-ANNOUNCE]).
#
# SCOPE IS LOCAL — this is intra-server discovery and `_server/*` is PER-TENANT
# (the multi-tenancy lock); it is never uplinked globally.
# --------------------------------------------------------------------------- #
def _register_server_ns(bus: ComposeIOBus, name: str) -> None:
    """Register a plugin's `_server/*` namespaces LOCAL+json so a subscriber can
    bind them. json (not binary) because the payloads are dicts, and LOCAL so
    `_resolve_scope` keeps them off the global uplink."""
    for suffix in ("announce", "status"):
        bus.register_namespace(
            f"_server/plugins/{name}/{suffix}", "json", scope=Scope.LOCAL)


def publish_plugin_state(bus: ComposeIOBus, plugin: ComposePlugin,
                         status: str, unmet: list[str]) -> None:
    """Publish ONE plugin's derived announce + status to `_server/plugins/<name>/*`."""
    _register_server_ns(bus, plugin.name)
    bus.publish_state(f"_server/plugins/{plugin.name}/announce",
                      plugin.announce().model_dump(), source="_server")
    bus.publish_state(f"_server/plugins/{plugin.name}/status",
                      {"status": status, "unmet": unmet}, source="_server")


def publish_server_state(
    bus: ComposeIOBus,
    plugins: "list[ComposePlugin]",
    loaded: "list[ComposePlugin]",
    parked: "dict[str, list[str]]",
    subprocess_pending: "list[str]",
    external_parked: "dict[str, list[str]] | None" = None,
) -> None:
    """Publish the whole per-tenant `_server/*` surface: one derived announce +
    status per declared plugin, plus a `_server/status` server summary.

    This is the kernel (the loader micro-kernel) instrumenting ITSELF as a peer
    on its own bus — the sole cross-tenant entity, published per-tenant
    ([CXC-CS-MICROKERNEL] kernel-publishes-its-own-state).

    ``external_parked`` (default empty) carries OUT-OF-BAND parks that never had
    an instance to `.announce()` — the loader's `config:`/`resolve:` parks
    ([CXC-CSL-STATUS-SURFACE]). They are a SEPARATE dict, never aliased into
    ``StandupResult.parked`` (which stays pure requires-floor semantics per
    [CXC-CSL-LOADER-INTERFACE]). Each is re-published STATUS-ONLY on every call
    (no announce — there is no instance) for ANNOUNCE-BIND periodicity parity
    with instantiated plugins, and merged into the `_server/status` aggregate."""
    external_parked = external_parked or {}

    # LOAD/ANNOUNCE-TIME MCP tool-name collision detection
    # ([CXC-MCP-TOOL-DECLARATION] duplicate-name-parks-loudly). The umbrella
    # router aggregates every RUNNING, non-`own` plugin's derived tools per
    # tenant; two whose derived `{plugin}.{tool}` names collide MUST fail HERE,
    # on the derived-announce spine, never only reactively when some MCP client
    # first calls list_tools ([CXC-MCP-BOUNDARIES] row 8). This runs on the
    # standup publish (synchronous — a colliding config fails add_tenant/standup
    # LOUDLY at boot, with NO MCP client connecting) AND on every re-announce
    # tick (a runtime surface change that introduces a collision is logged-and-
    # survived by the clock, never silent). Parked plugins contribute zero tools,
    # so only `loaded` (the running set) participates — the same aggregate the
    # reactive `MCPPeer.tool_registry()` screens, so the two can never disagree.
    from composeio.transport_mcp import assert_no_tool_collisions
    assert_no_tool_collisions([
        {"plugin": p.name, "mcp_tools": p.mcp_tool_descriptors()}
        for p in loaded
        if getattr(p, "mcp_endpoint", None) != "own"
    ])

    # MATCH ON plugin.name, NOT id(plugin): a caller that re-publishes the
    # `_server/*` surface with FRESH INSTANCES of the same plugin classes
    # (the public `standup()`/`publish_server_state()` are directly reachable)
    # would otherwise have EVERY announce silently dropped — no log, no error —
    # contradicting [CXC-CS-LOADER-PUBLISHES-ANNOUNCE] (a missing publication is
    # itself the fault report). `name` is already the unique per-tenant key
    # (the registry and the `_server/plugins/<name>/*` namespace both key on it).
    loaded_names = {p.name for p in loaded}
    for plugin in plugins:
        if plugin.name in parked:
            status, unmet = "parked", parked[plugin.name]
        elif plugin.name in subprocess_pending:
            status, unmet = "subprocess_pending", []
        elif plugin.name in loaded_names:
            status, unmet = "running", []
        else:
            continue
        publish_plugin_state(bus, plugin, status, unmet)

    # OUT-OF-BAND parks: a status-only publish per key, refreshed on every call
    # so a late pattern-subscriber to `_server/plugins/<key>/status` still gets a
    # push on a tick (announce-bind periodicity — a MISSING publication is itself
    # the fault report). NO `_server/plugins/<key>/announce` — no instance exists.
    for key, reasons in external_parked.items():
        bus.register_namespace(
            f"_server/plugins/{key}/status", "json", scope=Scope.LOCAL)
        bus.publish_state(f"_server/plugins/{key}/status",
                          {"status": "parked", "unmet": list(reasons)},
                          source="_server")

    bus.register_namespace("_server/status", "json", scope=Scope.LOCAL)
    bus.publish_state("_server/status", {
        "bus_id": bus.bus_id,
        "loaded": [p.name for p in loaded],
        # requires-floor parks + out-of-band (config/resolve) parks in ONE
        # aggregate, so a single `_server/status` query sees every reason a
        # tenant is not fully running. `parked` (the StandupResult's dict) is
        # never mutated here — the merge is a fresh dict.
        "parked": {**{n: u for n, u in parked.items()}, **external_parked},
        "subprocess_pending": list(subprocess_pending),
    }, source="_server")


# --------------------------------------------------------------------------- #
# Connectors — declared config, built through the transport registry
# ([CXC-CBK-CONNECTOR-CONTRACT]: a connector is declared, not coded per service).
# --------------------------------------------------------------------------- #
def build_connector(spec: dict) -> tuple[Any, str] | None:
    """Build one declared connector -> (transport, scope_class), or None when a
    KNOWN backend is simply unreachable (broker down, no CAN iface).

    Reuses ``transport_registry`` — the SAME ``type:`` factory idiom lifted from
    the transport layer to the connector layer ([CXC-CBK-CONNECTOR-CONTRACT]).
    ``scope`` selects the scope-class the connector attaches to (default global).
    An UNKNOWN ``type:`` raises, per the registry's loud-failure rule.
    """
    from composeio import transport_registry

    scope_class = str(spec.get("scope", "global")).strip().lower()
    transport = transport_registry.build(spec, app_id=spec.get("app_id", "composeio"))
    if transport is None:
        return None
    return transport, scope_class


@dataclass
class StandupResult:
    """What a standup produced — inspectable, so a park is never silent."""
    bus: ComposeIOBus
    loaded: list[ComposePlugin] = field(default_factory=list)
    parked: dict[str, list[str]] = field(default_factory=dict)   # name -> unmet
    subprocess_pending: list[str] = field(default_factory=list)  # names needing IPC host
    #: the connector objects built from the DECLARED specs and attached, so
    #: health can tell a declared connector from one attached later.
    connectors: list = field(default_factory=list)

    def is_clean(self) -> bool:
        return not self.parked


def standup(
    *,
    bus_id: str = "bus-0",
    connectors: "list[dict] | None" = None,
    plugins: "list[ComposePlugin] | None" = None,
    start: bool = False,
    available_capabilities: "set[str] | None" = None,
    ready_evaluator: "Callable[[str], bool] | None" = None,
    server_isolation_minimum: "Isolation | None" = None,
) -> StandupResult:
    """The canonical three-step standup for ONE locality ([CXC-CBK-STANDUP]).

    (1) BARE BUS — a zero-transport kernel; no paho, no substrate baked in
        ([CXC-CBK-MQTT-INDEPENDENT]).
    (2) ATTACH DECLARED CONNECTORS — MULTI-ATTACH; more than one may be live.
    (3) LOAD PLUGINS — each respecting its ``requires:`` floor; an unmet floor
        PARKS the plugin loudly rather than starting it degraded.

    ``start`` starts the bus AFTER connectors are attached and in-process
    plugins are started, matching "attach before start".
    """
    # (1) bare bus
    bus = ComposeIOBus(bus_id=bus_id)

    # (2) attach declared connectors (multi-attach)
    attached: list = []
    for spec in (connectors or []):
        built = build_connector(spec)
        if built is None:
            logger.warning(
                "connector %r unreachable at standup of %r — bus continues "
                "without it", spec.get("type"), bus_id)
            continue
        transport, scope_class = built
        bus.attach_connector(transport, scope_class)
        attached.append(transport)

    result = StandupResult(bus=bus, connectors=attached)
    caps = set(available_capabilities or set())

    # (3) load plugins, respecting each requires: floor
    for plugin in (plugins or []):
        unmet = plugin.unmet_requirements(caps, ready_evaluator)
        if unmet:
            result.parked[plugin.name] = unmet
            logger.warning("plugin %r PARKED — unmet: %s", plugin.name, unmet)
            continue
        actual = plugin.resolved_isolation(server_isolation_minimum)
        if actual >= Isolation.SUBPROCESS:
            # HARD subprocess floor (David's Q1). The plugin lives in-tenant,
            # reached over an internal IPC connector; spawning is Phase 1.
            result.subprocess_pending.append(plugin.name)
            logger.info(
                "plugin %r resolved isolation=%s — subprocess host is Phase 1; "
                "recorded as pending rather than started in-process",
                plugin.name, actual.name.lower())
            continue
        plugin.start(bus)
        result.loaded.append(plugin)

    # ANNOUNCE AT LOAD — the loader publishes every declared plugin's derived
    # announce + status to `_server/plugins/<name>/*`, closing the announce-bind
    # spine ([DEC-PHASE0-COMPOSESERVER-PUBLISHES-DERIVED-ANNOUNCE]). The value is
    # stored + local subscribers notified even before `start()`; the periodic
    # re-announce (the server's clock tick) begins when the bus starts.
    publish_server_state(bus, list(plugins or []), result.loaded,
                         result.parked, result.subprocess_pending)

    if start:
        bus.start()
    return result


# --------------------------------------------------------------------------- #
# ComposeServer — the multi-tenancy outer loop
# --------------------------------------------------------------------------- #
@dataclass
class _Tenant:
    tenant_id: str
    bus: ComposeIOBus
    plugins: list[ComposePlugin] = field(default_factory=list)
    parked: dict[str, list[str]] = field(default_factory=dict)
    capabilities: set[str] = field(default_factory=set)
    plugin_specs: list[ComposePlugin] = field(default_factory=list)  # all declared
    subprocess_pending: list[str] = field(default_factory=list)
    # OUT-OF-BAND parks (loader `config:`/`resolve:` parks) — a SEPARATE dict,
    # never aliased to `StandupResult.parked` ([CXC-CSL-STATUS-SURFACE]).
    external_parked: dict[str, list[str]] = field(default_factory=dict)
    _announce_tick: "Callable | None" = None
    #: extra callables run on every announce tick (ADDITIVE; spec 95 r18
    #: composeio-health-tick: the plug-in host's status tick rides this one)
    tick_hooks: list = field(default_factory=list)
    #: how many EXTERNAL (global/remote) connectors the tenant's manifest
    #: DECLARED. Health is judged against this, not against what attached: a
    #: connector whose construction failed is skipped by standup with a
    #: warning, and judged on what attached the tenant would look healthy.
    declared_connectors: int = 0
    #: the declared connectors that were actually built and attached
    #: (identity, from standup); anything else attached is an EXTRA.
    declared_attached: list = field(default_factory=list)

    def republish(self) -> None:
        """Re-publish the whole `_server/*` surface for this tenant."""
        publish_server_state(self.bus, self.plugin_specs, self.plugins,
                             self.parked, self.subprocess_pending,
                             self.external_parked)


class ComposeServer:
    """Supervise, load, config-persist, bus-provision — nothing else.

    Instantiate N ``ComposeIOBus`` objects, one per tenant, binding each
    plugin/connector to its tenant. Plugins are ALWAYS per-tenant; NO shared
    instances (LOCKED invariant). The server holds no data path between plugins
    — it supervises lifecycle only ([CXC-CS-MICROKERNEL] supervisor-not-mediator).
    """

    def __init__(self, ready_evaluator: "Callable[[str], bool] | None" = None,
                 server_isolation_minimum: "Isolation | None" = None):
        self._tenants: dict[str, _Tenant] = {}
        self._ready_evaluator = ready_evaluator
        self._server_isolation_minimum = server_isolation_minimum
        # SERVER/kernel-level connectors — LISTEN-role transports that span
        # tenants rather than dialing one tenant's fabric ([SPEC:CXC-WS] kernel
        # seam). Unlike a per-tenant DIAL connector these are bound ONCE, to the
        # server's own cross-tenant TenantRegistry (this ComposeServer), and torn
        # down on a full-server stop(). Separate from any tenant's connectors.
        self._server_connectors: list = []

    # ---- tenancy -------------------------------------------------------- #
    def add_tenant(
        self,
        tenant_id: str,
        *,
        connectors: "list[dict] | None" = None,
        plugins: "list[ComposePlugin] | None" = None,
        capabilities: "set[str] | None" = None,
        start: bool = False,
    ) -> StandupResult:
        """Stand up one tenant's own root bus + its own plugin instances.

        A plugin instance passed here belongs to THIS tenant alone; the caller
        must never hand the same instance to two tenants — the loader binds it
        to this bus at start. (Pass a factory result per tenant.)
        """
        if tenant_id in self._tenants:
            raise ValueError(f"tenant {tenant_id!r} already exists")
        plugins = list(plugins or [])
        result = standup(
            bus_id=tenant_id,
            connectors=connectors,
            plugins=plugins,
            start=start,
            available_capabilities=capabilities,
            ready_evaluator=self._ready_evaluator,
            server_isolation_minimum=self._server_isolation_minimum,
        )
        tenant = _Tenant(
            tenant_id=tenant_id, bus=result.bus,
            # COPY, never the aliased reference: `StandupResult.parked` is the
            # object the loader interface promises stays pure requires-floor
            # semantics; sharing it here would let any later tenant-side merge
            # silently contaminate the returned result ([CXC-CSL-LOADER-INTERFACE]).
            plugins=result.loaded, parked=dict(result.parked),
            capabilities=set(capabilities or set()),
            plugin_specs=plugins,
            subprocess_pending=list(result.subprocess_pending),
            declared_connectors=self.declared_external(connectors),
            declared_attached=list(result.connectors),
        )
        self._tenants[tenant_id] = tenant
        if start:
            self._install_announce_tick(tenant)
        return result

    # ---- the periodic re-announce (reuses the BusClock tick) ------------- #
    def _install_announce_tick(self, tenant: _Tenant) -> None:
        if tenant._announce_tick is not None:
            return

        def _tick(now, t=tenant):
            t.republish()
            for hook in list(t.tick_hooks):
                try:
                    hook()
                except Exception as exc:  # noqa: BLE001 — a hook never kills the tick
                    logger.error("tenant %r tick hook error: %s", t.tenant_id, exc)

        tenant._announce_tick = _tick
        shared_clock().attach(_tick, interval=SERVER_ANNOUNCE_INTERVAL_S)

    def _remove_announce_tick(self, tenant: _Tenant) -> None:
        if tenant._announce_tick is not None:
            shared_clock().detach(tenant._announce_tick)
            tenant._announce_tick = None

    def add_tick_hook(self, tenant_id: str, hook: "Callable[[], None]") -> None:
        """Run ``hook()`` on every announce tick of ``tenant_id`` (ADDITIVE seam)."""
        self._tenants[tenant_id].tick_hooks.append(hook)

    def remove_tick_hook(self, tenant_id: str, hook: "Callable[[], None]") -> None:
        hooks = self._tenants[tenant_id].tick_hooks
        if hook in hooks:
            hooks.remove(hook)

    def tenant_bus(self, tenant_id: str) -> ComposeIOBus:
        return self._tenants[tenant_id].bus

    @staticmethod
    def declared_external(specs: "list | None") -> int:
        """How many of these connector specs are EXTERNAL (scope not local)."""
        n = 0
        for spec in specs or []:
            scope = (spec.get("scope", "global") if isinstance(spec, dict)
                     else "global")
            if str(scope).strip().lower() != "local":
                n += 1
        return n

    def connector_health(self) -> dict:
        """Every tenant's EXTERNAL connectors, judged against what was DECLARED.

        Reads each transport's own `is_connected` (the signal the bus and
        `ComposeApp.status()` already use), plus its `health()` detail when it
        has one. `connectors` lists the ones built from the manifests'
        declarations, `extra_connectors` any attached by other means, and
        `expected_connectors` is how many the manifests declared.

        `healthy` is True iff (a) every DECLARED connector is attached (a
        declared connector whose construction failed is missing, however many
        extras there are) and (b) EVERY attached connector, declared or extra,
        is connected. Extras are allowed; an extra that is down is unhealthy.
        A server that declares none and has none attached is healthy.
        Local connectors are in-process and not counted.
        """
        declared_rows, extra_rows = [], []
        expected = 0
        for tid, tenant in self._tenants.items():
            expected += tenant.declared_connectors
            ours = {id(c) for c in tenant.declared_attached}
            for scope in ("global", "remote"):
                for conn in tenant.bus.connectors(scope):
                    row = {"tenant": tid, "scope": scope,
                           "transport": type(conn).__name__}
                    detail = getattr(conn, "health", None)
                    if callable(detail):
                        try:
                            row.update(detail())
                        except Exception as exc:  # noqa: BLE001
                            row["health_error"] = str(exc)
                    row["connected"] = bool(getattr(conn, "is_connected", False))
                    (declared_rows if id(conn) in ours else extra_rows).append(row)
        everything = declared_rows + extra_rows
        return {"healthy": (len(declared_rows) >= expected
                            and all(r["connected"] for r in everything)),
                "expected_connectors": expected,
                "connectors": declared_rows,
                "extra_connectors": extra_rows}

    def tenants(self) -> list[str]:
        return list(self._tenants)

    def tenant_plugin_specs(self, tenant_id: str) -> list[ComposePlugin]:
        """Every DECLARED plugin instance on a tenant (loaded AND parked).

        The MCP umbrella router ([SPEC:CXC-MCP] [CXC-MCP-CONNECTOR-INTERFACE])
        needs the LIVE plugin instances to resolve each tool's ``input_model``
        for faithful pydantic validation at invoke time — the derived-announce
        it reads for the tool LIST carries only the JSON-schema, not the class.
        The tool LIST authority stays the announce ([CXC-MCP-ROUTER-MECHANICS]);
        this only supplies the class a listed tool names. Returns a copy."""
        return list(self._tenants[tenant_id].plugin_specs)

    def parked(self, tenant_id: str) -> dict[str, list[str]]:
        return dict(self._tenants[tenant_id].parked)

    def merge_parked(self, tenant_id: str, parks: "dict[str, list[str]]") -> None:
        """Record OUT-OF-BAND parks on a tenant and re-announce ([CXC-CSL-STATUS-
        SURFACE]). The sanctioned seam for the plugin loader's `config:`/`resolve:`
        parks — entries that never had an instance, so the binder's per-plugin
        announce path cannot surface them.

        These land in the tenant's SEPARATE ``external_parked`` dict (never the
        requires-floor ``parked`` the binder tracks, and never ``StandupResult.
        parked``), so they appear in the `_server/status` aggregate + their own
        `_server/plugins/<key>/status` WITHOUT touching requires-floor semantics
        and WITHOUT ever entering `plugin_specs` — the re-evaluation loop cannot
        spuriously unpark them ([CXC-CSL-CONTINUOUS-REBIND] boundary)."""
        tenant = self._tenants[tenant_id]
        tenant.external_parked.update({k: list(v) for k, v in parks.items()})
        tenant.republish()

    # ---- the binder: dynamic requires-floor rebinding ------------------- #
    def provide_capability(self, tenant_id: str, capability: str) -> list[str]:
        """Announce a capability to a tenant and UNPARK any plugin whose floor
        is now met — no restart ([CXC-CS-REQUIRES-FLOOR] dynamic-rebinding).

        Returns the names of plugins unparked by this call.
        """
        tenant = self._tenants[tenant_id]
        tenant.capabilities.add(capability)
        return self._reevaluate(tenant)

    def revoke_capability(self, tenant_id: str, capability: str) -> None:
        self._tenants[tenant_id].capabilities.discard(capability)

    def _reevaluate(self, tenant: _Tenant) -> list[str]:
        unparked: list[str] = []
        for plugin in tenant.plugin_specs:
            if plugin.name not in tenant.parked:
                continue
            unmet = plugin.unmet_requirements(
                tenant.capabilities, self._ready_evaluator)
            if unmet:
                tenant.parked[plugin.name] = unmet  # refresh the reason
                continue
            actual = plugin.resolved_isolation(self._server_isolation_minimum)
            if actual >= Isolation.SUBPROCESS:
                # subprocess host is Phase 1; leave parked-reason cleared but do
                # not start in-process. Recorded via the log for the operator.
                logger.info("plugin %r now meets its floor but is subprocess-"
                            "pinned; IPC host is Phase 1", plugin.name)
                del tenant.parked[plugin.name]
                if plugin.name not in tenant.subprocess_pending:
                    tenant.subprocess_pending.append(plugin.name)
                unparked.append(plugin.name)
                continue
            plugin.start(tenant.bus)
            tenant.plugins.append(plugin)
            del tenant.parked[plugin.name]
            unparked.append(plugin.name)
        # ON-CHANGE RE-ANNOUNCE ([CXC-CS-DYNAMIC-SURFACE]): a status transition
        # (parked -> running / subprocess_pending) changes the `_server/*`
        # surface, so re-publish it the moment the binder acts.
        if unparked:
            tenant.republish()
        return unparked

    # ---- lifecycle ------------------------------------------------------ #
    def start(self, tenant_id: "str | None" = None) -> None:
        for tid, tenant in self._tenants.items():
            if tenant_id in (None, tid):
                tenant.bus.start()
                self._install_announce_tick(tenant)
                tenant.republish()   # announce promptly on start, then periodically

    def stop(self, tenant_id: "str | None" = None) -> None:
        for tid, tenant in self._tenants.items():
            if tenant_id in (None, tid):
                self._remove_announce_tick(tenant)
                for plugin in tenant.plugins:
                    try:
                        plugin.stop()
                    except Exception as exc:  # noqa: BLE001
                        logger.error("plugin %r stop() error: %s", plugin.name, exc)
                tenant.bus.stop()
        # A full-server stop tears down the server-level (LISTEN) connectors too;
        # a single-tenant stop leaves them running, since they span tenants
        # ([SPEC:CXC-WS] kernel seam). ADDITIVE — no tenant/connector path changes.
        if tenant_id is None:
            self._stop_server_connectors()

    # ---- server/kernel-level connectors ([SPEC:CXC-WS]) ----------------- #
    def _stop_server_connectors(self) -> None:
        for conn in self._server_connectors:
            try:
                conn.stop()
            except Exception as exc:  # noqa: BLE001
                logger.error("server connector %r stop() error: %s", conn, exc)
        self._server_connectors.clear()

    def attach_ws_endpoint(
        self,
        host: str = "127.0.0.1",
        port: int = 8430,
        *,
        deeplink_parser=None,
        start: bool = True,
    ):
        """Attach a SERVER/kernel-level WS LISTEN endpoint ([SPEC:CXC-WS]).

        A LISTEN connector INVERTS the socket direction of every DIAL transport
        and SPANS tenants: each accepted browser tab becomes one bus peer that
        may select ANY tenant this server serves. So — unlike a per-tenant DIAL
        connector attached to one bus — it is bound ONCE, at the kernel, to the
        server's own cross-tenant ``TenantRegistry`` (this ``ComposeServer``,
        which already satisfies that Protocol via ``tenant_bus``/``tenants``),
        never to a single tenant bus ([CXC-WS-TENANT-BOUNDARY]).

        Constructs the ``WSConnector`` through the SAME ``type: ws`` registry
        factory the DIAL transports use, hands it this server via
        ``bind_registry()``, starts the listener (``start=True`` default), and
        records it for teardown on a full-server ``stop()``. Returns the
        connector — its ``bound_port`` resolves a ``port=0`` ephemeral bind, so a
        caller (or a test) can discover the real listen port.

        ADDITIVE: this touches no ``standup()``/``add_tenant()``/per-tenant
        connector path; a server that never calls it behaves exactly as before.
        """
        from composeio import transport_registry
        conn = transport_registry.build(
            {"type": "ws", "host": host, "port": port,
             "deeplink_parser": deeplink_parser},
            app_id="composeserver",
        )
        conn.bind_registry(registry=self)
        if start:
            conn.start()
        self._server_connectors.append(conn)
        return conn

    def attach_mcp_endpoint(
        self,
        host: str = "127.0.0.1",
        port: int = 8431,
        *,
        wire: str = "streamable-http",
        start: bool = True,
    ):
        """Attach a SERVER/kernel-level MCP umbrella endpoint ([SPEC:CXC-MCP]).

        The agent-facing sibling of ``attach_ws_endpoint``: ONE aggregating MCP
        connector per ComposeServer ([CXC-MCP-UMBRELLA-RATIFIED]), tenant-scoped
        per session ([CXC-MCP-ENDPOINT-TOPOLOGY]). Like the WS endpoint it SPANS
        tenants, so it is bound ONCE, at the kernel, to this server's own
        cross-tenant registry (this ``ComposeServer``, which satisfies the MCP
        connector's ``ServerRegistry`` Protocol via ``tenants``/``tenant_bus``/
        ``tenant_plugin_specs``), never to a single tenant bus
        ([CXC-MCP-TENANT-BOUNDARY]).

        Constructed through the SAME ``type: mcp`` registry factory the DIAL
        transports use; the MCP SDK is lazy-imported so its absence never breaks
        the other factories. ``host`` binds the trusted LAN surface only — a
        world-facing ``0.0.0.0``/``::`` bind is refused, because v1 ships no auth
        ([DD-MCP-AUTH-GATE]) and the network-transport choice makes that guard
        load-bearing. ADDITIVE: touches no ``standup``/``add_tenant`` path."""
        from composeio import transport_registry
        conn = transport_registry.build(
            {"type": "mcp", "host": host, "port": port, "wire": wire},
            app_id="composeserver",
        )
        if conn is None:
            # build() returns None ONLY for a KNOWN transport whose backend is
            # genuinely unreachable (its documented environment-degrade path) —
            # never for a POLICY/CONFIG refusal. A world-facing bind
            # (UnsafeBindError, a TransportPolicyError) is RE-RAISED by build()
            # and propagates straight through this method as its OWN exception;
            # we never reach this branch for it, and never assert a cause we did
            # not observe. The MCP SDK's absence is NOT this branch either — it
            # surfaces from conn.start(), not build(), because the SDK import is
            # lazy. So do not guess "the SDK is likely absent" here.
            raise RuntimeError(
                "MCP endpoint could not be built: the `type: mcp` transport "
                "factory returned None, which means a KNOWN backend reported "
                "itself unreachable. See the transport_registry warning log for "
                "the underlying reason (a policy/config refusal such as an "
                "unsafe bind would have RAISED here, not returned None).")
        conn.bind_registry(registry=self)
        if start:
            conn.start()
        self._server_connectors.append(conn)
        return conn

    def registry(self) -> dict:
        """Every declared plugin's registry entry, per tenant — the DERIVED
        registry artifact ([CXC-CS-PLUGIN-CONTRACT])."""
        return {
            tid: {p.name: p.registry_entry() for p in t.plugin_specs}
            for tid, t in self._tenants.items()
        }
