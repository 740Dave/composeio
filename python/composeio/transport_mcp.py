"""transport_mcp — the MCP umbrella surface: the AGENT-FACING sibling of the WS
endpoint ([SPEC:CXC-MCP], `90-composeserver-mcp-surface.lts`).

Where a browser tab becomes a live bus peer over WS carrying raw namespace
traffic ([SPEC:CXC-WS]), an Intelligence becomes a live bus peer over MCP
carrying TOOL CALLS. ONE aggregating router per ComposeServer
([CXC-MCP-UMBRELLA-RATIFIED]) enumerates every loaded plugin's DECLARED tools
(one typed pydantic manifest, [CXC-MCP-TOOL-DECLARATION]) and exposes them
per-plugin-namespaced (`fingerprint.enroll`). A session selects its tenant
post-connect, PRE-BINDING -> BOUND, mirroring [CXC-WS-LIFECYCLE] exactly.

THE THREE RESOLVED [DD-*] THIS BUILD ASSUMES (DEC-MCP-DD-RESOLVED-2026-08-25):
  * TOPOLOGY: ONE connector per ComposeServer, tenant-scoped per session
    ([CXC-MCP-ENDPOINT-TOPOLOGY]) — NOT per-tenant endpoints.
  * WIRE: streamable-HTTP is the v1 transport (official MCP Python SDK); stdio
    is the degenerate peer-count-1 case ([CXC-MCP-SESSION-PEER]).
  * AUTH: deferred ([DD-MCP-AUTH-GATE]) — but the listen surface binds the
    trusted LAN only; a world-facing `0.0.0.0`/`::` bind is REFUSED here,
    because the network-transport choice makes that guard load-bearing.

WHAT THIS MODULE OWNS (its net-new surface, per [CXC-MCP-CONNECTOR-INTERFACE]):
  * `MCPPeer` — one singleton persistent peer per MCP session, socket-free and
    fully unit-testable: PRE-BINDING/BOUND state, `bound_tenant`, the
    tool-list aggregation, the encode/decode dispatch, and the in-flight
    `request_id` correlation for `query`-mode reads.
  * `MCPConnector` — the accept surface; owns the SDK server (LAZY-imported so a
    missing MCP dependency never breaks the other transport factories) and a
    collection of peers, and derives each peer's tool registry from the loader's
    EXISTING derived-announce ([CXC-MCP-ROUTER-MECHANICS]).
  * the `_mcp(spec, app_id) -> MCPConnector` registry factory.

WHAT IT REUSES BY REFERENCE (never reimplements):
  * the derived-announce spine [CXC-CS-LOADER-PUBLISHES-ANNOUNCE] publishes to
    `_server/plugins/<name>/announce` — the tool list is read off THAT, never a
    second announce mechanism;
  * `bus.namespaces`/`Namespace.value` for `retained`-mode reads — the same
    accessor [CXC-WS-RETAINED-REPLAY] reads;
  * [SPEC:CXC-BUS-QUERY]'s idiom VERBATIM for `query`-mode reads
    (subscribe-before-publish, `request_id` correlation, requester-owns-timeout);
  * `ComposeServer`'s per-tenant orchestration for the tenant boundary
    ([CXC-CS-MULTI-TENANCY]).

THE CONNECTOR NEVER TRANSFORMS ([DX-CONNECTOR-FORWARDS-NEVER-TRANSFORMS]).
ENCODE is table-driven from the tool's OWN declared schema and never derives a
value from ANOTHER namespace's state ([CXC-MCP-INVOKE-WRITE]); DECODE recovers
the published/queried fact and stops ([CXC-MCP-INVOKE-READ]).
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from composeio.freshness import is_unknown
from composeio.plugin import ComposePlugin, ReadMode, ToolDirection
from composeio.transport_registry import TransportPolicyError

logger = logging.getLogger(__name__)

PRE_BINDING = "PRE-BINDING"
BOUND = "BOUND"

# `_server/plugins/<name>/announce` — the derived-announce namespace the loader
# already publishes ([CXC-CS-LOADER-PUBLISHES-ANNOUNCE]); the router reads the
# tool list off THIS, never a second spine ([CXC-MCP-ROUTER-MECHANICS]).
_ANNOUNCE_RE = re.compile(r"^_server/plugins/([^/]+)/announce$")

# control-plane tool names — the tenant-selection surface
# ([CXC-MCP-ENDPOINT-TOPOLOGY] tenant-selection-surface). NOT tenant-scoped, so
# reachable in PRE-BINDING; every other tool requires a BOUND tenant.
CTL_LIST_TENANTS = "_server.list_tenants"
CTL_SELECT_TENANT = "_server.select_tenant"


# --------------------------------------------------------------------------- #
# Errors — every one loud and named ([CXC-MCP-BOUNDARIES]).
# --------------------------------------------------------------------------- #
class UnknownTenantError(ValueError):
    """A selection named a tenant the registry does not serve ([CXC-MCP-BOUNDARIES] n/a)."""


class NotBoundError(RuntimeError):
    """A tenant-scoped tool call arrived while PRE-BINDING ([CXC-MCP-BOUNDARIES] row 2)."""


class ToolNotFoundError(KeyError):
    """A qualified tool name does not exist on the BOUND tenant ([CXC-MCP-BOUNDARIES] row 3).

    Raised for a cross-tenant name too: a tool that exists only on ANOTHER
    tenant is 'not found' here, never a cross-tenant dispatch
    ([CXC-MCP-TENANT-BOUNDARY] negative-control)."""

    def __str__(self) -> str:  # KeyError repr adds quotes; keep the message clean
        return self.args[0] if self.args else ""


class ToolCollisionError(RuntimeError):
    """Two derived exposed names collide ([CXC-MCP-BOUNDARIES] row 8).

    Reported LOUDLY, never silently resolved by first-wins
    ([CXC-MCP-TOOL-DECLARATION] duplicate-name-parks-loudly)."""


class UnsafeBindError(TransportPolicyError):
    """A world-facing bind host was requested with no auth in v1.

    v1 ships no auth ([DD-MCP-AUTH-GATE]); the listen surface MUST stay on the
    trusted LAN. `0.0.0.0`/`::`/empty is refused so the deferred gate is not
    silently bypassed ([CXC-MCP-BOUNDARIES] row 11).

    A ``TransportPolicyError`` (and therefore still a ``ValueError``): this is a
    construction-time refusal on a REACHABLE, well-formed config, so
    ``transport_registry.build()`` RE-RAISES it rather than degrading it to a
    silent ``None`` — the None+log path is for a KNOWN backend that is merely
    unreachable, a different axis entirely."""


class McpDependencyError(RuntimeError):
    """The MCP SDK is required to actually SERVE but is not installed.

    Raised only from `start()` — construction, `known_types()`, and every other
    transport factory stay unaffected because the SDK import is LAZY
    ([CXC-MCP-CONNECTOR-INTERFACE] imported-lazily)."""


# --------------------------------------------------------------------------- #
# Load/announce-time collision detection — the PROACTIVE sibling of
# MCPPeer.tool_registry()'s reactive raise ([CXC-MCP-TOOL-DECLARATION]
# duplicate-name-parks-loudly). The reactive raise only fires when some MCP
# session calls list_tools/invoke, so a collision in a deployment nobody has yet
# pointed an MCP client at would never surface. This runs at announce-publish
# time (the loader's derived-announce spine), so a collision fails LOUDLY at
# boot, with NO MCP client ever connecting ([CXC-MCP-BOUNDARIES] row 8).
# --------------------------------------------------------------------------- #
def assert_no_tool_collisions(announces: "list[dict]") -> None:
    """Raise ``ToolCollisionError`` if two derived exposed MCP tool names collide
    across the plugins PARTICIPATING in one tenant's umbrella.

    ``announces`` is one entry per participating plugin: a mapping carrying
    ``plugin`` (the plugin name) and ``mcp_tools`` (its derived descriptors, each
    with an exposed ``{plugin}.{tool}`` ``name``). The CALLER selects who
    participates — RUNNING, non-``own`` plugins — mirroring
    ``MCPPeer.tool_registry()`` exactly (a PARKED plugin contributes zero tools,
    an ``own``-declared plugin is excluded entirely), so this proactive screen
    and the reactive raise agree on the same aggregate and can never disagree on
    whether a given config collides.

    Detection spans the WHOLE flattened set, so it catches BOTH a within-plugin
    duplicate (one plugin declaring the same tool name twice) and a cross-plugin
    collision (two plugins whose ``{plugin}.{tool}`` names coincide, e.g. plugin
    ``svc`` tool ``do.thing`` vs plugin ``svc.do`` tool ``thing``)."""
    seen: dict[str, str] = {}  # exposed_name -> the plugin that first declared it
    for announce in announces:
        plugin_name = announce.get("plugin", "")
        for desc in announce.get("mcp_tools", []) or []:
            exposed = desc.get("name")
            if exposed is None:
                continue
            prior = seen.get(exposed)
            if prior is not None:
                raise ToolCollisionError(
                    f"duplicate MCP tool name {exposed!r} — declared by both "
                    f"{prior!r} and {plugin_name!r}; a derived-name collision "
                    f"MUST be reported at load/announce time, never silently "
                    f"resolved by first-wins ([CXC-MCP-TOOL-DECLARATION] "
                    f"duplicate-name-parks-loudly).")
            seen[exposed] = plugin_name


# --------------------------------------------------------------------------- #
# The kernel seam: what the MCP connector needs from the ComposeServer/kernel.
# ComposeServer satisfies this duck-typed. Injected once at kernel standup, never
# rebuilt per session — the cross-tenant surface is the KERNEL's
# ([CXC-MCP-TENANT-BOUNDARY]).
# --------------------------------------------------------------------------- #
@runtime_checkable
class ServerRegistry(Protocol):
    def tenants(self) -> list[str]: ...
    def tenant_bus(self, tenant_id: str) -> Any: ...
    def tenant_plugin_specs(self, tenant_id: str) -> list[ComposePlugin]: ...


# --------------------------------------------------------------------------- #
# ToolBinding — one resolved, invokable tool on a bound tenant. The descriptor
# (name/schema/mapping/direction/read_mode) comes from the derived-announce; the
# `input_model` is the LIVE pydantic class, resolved from the plugin instance so
# validation is FAITHFUL ([CXC-MCP-INVOKE-WRITE] input_model's own validation).
# --------------------------------------------------------------------------- #
@dataclass
class ToolBinding:
    exposed_name: str
    plugin_name: str
    local_name: str
    description: str
    input_schema: dict
    direction: ToolDirection
    publishes: list[str]
    subscribes: list[str]
    read_mode: Optional[ReadMode]
    idempotent: bool
    input_model: Optional[type[BaseModel]]  # live class; None only if unresolved

    def descriptor(self) -> dict:
        """The MCP tool-list entry an SDK client sees."""
        return {
            "name": self.exposed_name,
            "description": self.description,
            "inputSchema": self.input_schema,
        }


@dataclass
class _PendingCall:
    request_id: str
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None


# --------------------------------------------------------------------------- #
# MCPPeer — one singleton persistent peer per MCP session ([CXC-MCP-SESSION-PEER]).
#
# Deliberately wire-free: it owns lifecycle/binding/tool-dispatch/correlation
# LOGIC, so the whole of [CXC-MCP-ENDPOINT-TOPOLOGY]/[CXC-MCP-TENANT-BOUNDARY]/
# [CXC-MCP-INVOKE-WRITE]/[CXC-MCP-INVOKE-READ]/[CXC-MCP-ROUTER-MECHANICS] is
# unit-testable with NO SDK and NO sockets. `MCPConnector` binds a real MCP
# session's list_tools/call_tool to these methods.
# --------------------------------------------------------------------------- #
class MCPPeer:
    #: default per-call timeout for a `query`-mode read (seconds). The REQUESTER
    #: owns the timeout ([CXC-BUS-QUERY-ORDERING]); a query that never answers
    #: surfaces a named "no answer" error, never an indefinite hang.
    DEFAULT_QUERY_TIMEOUT = 5.0

    def __init__(self, peer_id: str, registry: ServerRegistry):
        self.peer_id = peer_id
        self._registry = registry
        self.state: str = PRE_BINDING
        self.bound_tenant: Optional[str] = None
        self._bus = None
        # in-flight `query`-mode correlation, keyed by request_id — owned by the
        # peer for the life of the session ([CXC-MCP-SESSION-PEER]).
        self._pending: dict[str, _PendingCall] = {}
        self._result_subs: set[str] = set()  # result namespaces already subscribed
        self._lock = threading.Lock()

    # -- identity / addressing ------------------------------------------- #
    def _src(self) -> str:
        """This peer's bus identity — the `{src}` a query result is addressed to
        ([CXC-BUS-QUERY-IDIOM]). The peer_id is topic-safe by construction."""
        return self.peer_id

    # -- lifecycle ([CXC-MCP-ENDPOINT-TOPOLOGY]) -------------------------- #
    def select_tenant(self, tenant_id: str) -> dict:
        """PRE-BINDING -> BOUND(tenant_id) ([CXC-MCP-ENDPOINT-TOPOLOGY]), the
        selection step that MUST precede any tenant-scoped tool. Loud on an
        unknown tenant, never a silent fallback to a default."""
        if tenant_id not in set(self._registry.tenants()):
            raise UnknownTenantError(
                f"tenant {tenant_id!r} does not exist; known: "
                f"{sorted(self._registry.tenants())}")
        # a re-selection is a clean rebind: drop any prior in-flight state so no
        # tenant-A result can resolve against a tenant-B binding.
        self._teardown_pending()
        self.bound_tenant = tenant_id
        self._bus = self._registry.tenant_bus(tenant_id)
        self.state = BOUND
        return {"bound_tenant": tenant_id}

    def close(self) -> None:
        """The session ended; the peer is simply gone."""
        self._teardown_pending()
        self.bound_tenant = None
        self._bus = None
        self.state = PRE_BINDING

    def _teardown_pending(self) -> None:
        with self._lock:
            for pending in self._pending.values():
                pending.event.set()  # unblock any waiter
            self._pending.clear()
        # leave result subscriptions on the (old) bus behind; they are harmless
        # no-ops once _pending is empty and the peer is rebinding/closing.
        self._result_subs.clear()

    # -- tool aggregation ([CXC-MCP-ROUTER-MECHANICS]) -------------------- #
    def tool_registry(self) -> dict[str, ToolBinding]:
        """The BOUND tenant's aggregated tool set, derived FRESH from the
        loader's existing derived-announce every call — never a cached snapshot
        ([CXC-MCP-ROUTER-MECHANICS] MUST NEVER cache). Excludes `own`-declared
        plugins ([CXC-MCP-FORK-EXCEPTION]) and PARKED plugins
        ([CXC-MCP-ROUTER-MECHANICS] park-excludes). Raises ToolCollisionError on
        a duplicate exposed name ([CXC-MCP-TOOL-DECLARATION])."""
        if self.state != BOUND or self._bus is None:
            return {}
        bus = self._bus
        specs = {p.name: p for p in self._registry.tenant_plugin_specs(self.bound_tenant)}
        registry: dict[str, ToolBinding] = {}
        for ns_name, ns in bus.namespaces.items():
            m = _ANNOUNCE_RE.match(ns_name)
            if not m:
                continue
            plugin_name = m.group(1)
            announce = ns.value
            if is_unknown(announce) or not isinstance(announce, dict):
                continue
            # fork-exception: an `own`-declared plugin is excluded entirely.
            if announce.get("mcp_endpoint") == "own":
                continue
            # park-exclusion: a PARKED plugin contributes zero tools.
            status_ns = bus.namespaces.get(f"_server/plugins/{plugin_name}/status")
            if status_ns is not None:
                status_val = status_ns.value
                if isinstance(status_val, dict) and status_val.get("status") == "parked":
                    continue
            plugin = specs.get(plugin_name)
            for desc in announce.get("mcp_tools", []) or []:
                binding = self._bind_tool(plugin_name, desc, plugin)
                if binding.exposed_name in registry:
                    raise ToolCollisionError(
                        f"duplicate MCP tool name {binding.exposed_name!r} on tenant "
                        f"{self.bound_tenant!r} — collision reported loudly, never "
                        f"first-wins ([CXC-MCP-TOOL-DECLARATION])")
                registry[binding.exposed_name] = binding
        return registry

    def _bind_tool(self, plugin_name: str, desc: dict,
                   plugin: Optional[ComposePlugin]) -> ToolBinding:
        mapping = desc.get("namespace_mapping", {}) or {}
        read_mode = desc.get("read_mode")
        # resolve the LIVE input_model from the plugin instance (faithful
        # validation); the announce carries only the derived JSON-schema.
        input_model = None
        if plugin is not None:
            for tool in getattr(plugin, "mcp_tools", []):
                if tool.name == desc.get("local_name"):
                    input_model = tool.input_model
                    break
        return ToolBinding(
            exposed_name=desc["name"],
            plugin_name=plugin_name,
            local_name=desc.get("local_name", ""),
            description=desc.get("description", ""),
            input_schema=desc.get("input_schema", {}),
            direction=ToolDirection(desc["direction"]),
            publishes=list(mapping.get("publishes", [])),
            subscribes=list(mapping.get("subscribes", [])),
            read_mode=ReadMode(read_mode) if read_mode else None,
            idempotent=bool(desc.get("idempotent", False)),
            input_model=input_model,
        )

    def list_tools(self) -> list[dict]:
        """The MCP tool-list an SDK client sees. The control-plane selection
        tools are ALWAYS present (so a fresh session can discover how to bind);
        tenant-scoped tools appear ONLY once BOUND ([CXC-MCP-ENDPOINT-TOPOLOGY]
        verify-machine: no tenant-scoped tool reachable pre-selection)."""
        tools = [
            {"name": CTL_LIST_TENANTS,
             "description": "List the tenants this ComposeServer hosts.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": CTL_SELECT_TENANT,
             "description": "Bind this session to a tenant before any "
                            "tenant-scoped tool ([CXC-MCP-ENDPOINT-TOPOLOGY]).",
             "inputSchema": {
                 "type": "object",
                 "properties": {"tenant_id": {"type": "string"}},
                 "required": ["tenant_id"]}},
        ]
        for binding in self.tool_registry().values():
            tools.append(binding.descriptor())
        return tools

    # -- invocation dispatch --------------------------------------------- #
    def invoke(self, tool_name: str, args: Optional[dict] = None,
               *, timeout: Optional[float] = None) -> dict:
        """Dispatch one MCP tool call. Control tools are handled regardless of
        binding; every other tool requires a BOUND tenant and resolves against
        ONLY that tenant ([CXC-MCP-TENANT-BOUNDARY])."""
        args = args or {}
        if tool_name == CTL_LIST_TENANTS:
            return {"ok": True, "tenants": list(self._registry.tenants())}
        if tool_name == CTL_SELECT_TENANT:
            tenant_id = args.get("tenant_id")
            if not tenant_id:
                return {"ok": False, "error": "select_tenant requires 'tenant_id'"}
            try:
                bound = self.select_tenant(str(tenant_id))
            except UnknownTenantError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, **bound}

        if self.state != BOUND or self._bus is None:
            raise NotBoundError(
                f"tool {tool_name!r} is tenant-scoped; select a tenant first "
                f"([CXC-MCP-ENDPOINT-TOPOLOGY])")

        registry = self.tool_registry()
        binding = registry.get(tool_name)
        if binding is None:
            raise ToolNotFoundError(
                f"tool {tool_name!r} not found on tenant {self.bound_tenant!r} "
                f"([CXC-MCP-TENANT-BOUNDARY]: never a cross-tenant dispatch)")

        if binding.direction is ToolDirection.WRITE:
            return self._invoke_write(binding, args)
        if binding.direction is ToolDirection.READ:
            return self._invoke_read(binding, args, timeout)
        if binding.direction is ToolDirection.COMMAND:
            return self._invoke_command(binding, args, timeout)
        # read-write: publish-then-return ([CXC-MCP-TOOL-DECLARATION]).
        write_result = self._invoke_write(binding, args)
        if not write_result.get("ok"):
            return write_result
        read_result = self._invoke_read(binding, args, timeout)
        return {"ok": read_result.get("ok", True),
                "write": write_result, "read": read_result}

    # -- ENCODE: write = table-driven publish ([CXC-MCP-INVOKE-WRITE]) ---- #
    def _invoke_write(self, binding: ToolBinding, args: dict) -> dict:
        """A `write` tool call is EXACTLY one publish per declared namespace,
        val = the validated `input_model` args — NEVER a derive from another
        namespace's state ([CXC-MCP-INVOKE-WRITE]). Validation failure produces
        ZERO bus traffic and a structured error naming the field(s)."""
        if binding.input_model is None:
            return {"ok": False,
                    "error": f"tool {binding.exposed_name!r} has no resolvable "
                             f"input_model — cannot validate arguments"}
        try:
            validated = binding.input_model(**args)
        except ValidationError as exc:
            # zero bus traffic; name the offending field(s).
            fields = sorted({".".join(str(p) for p in e.get("loc", ())) or "<root>"
                             for e in exc.errors()})
            return {"ok": False, "error": "validation failed",
                    "fields": fields, "detail": exc.errors()}
        val = validated.model_dump()
        # THE SINGLE TOUCH: publish onto the declared namespaces and nothing
        # else. No read of any other namespace happens on this path — the
        # single-namespace-touch invariant ([CXC-MCP-INVOKE-WRITE] boundary,
        # MCP-V6) is structural here, not a runtime check.
        for ns in binding.publishes:
            self._bus.publish_state(ns, val, source=self._src())
        return {"ok": True, "published": list(binding.publishes), "value": val}

    # -- DECODE: read = retained-direct OR query round-trip -------------- #
    def _invoke_read(self, binding: ToolBinding, args: dict,
                     timeout: Optional[float]) -> dict:
        if binding.read_mode is ReadMode.RETAINED:
            return self._read_retained(binding)
        if binding.read_mode is ReadMode.QUERY:
            return self._read_query(binding, args, timeout)
        return {"ok": False,
                "error": f"tool {binding.exposed_name!r} is a read tool with no "
                         f"read_mode declared"}

    def _read_retained(self, binding: ToolBinding) -> dict:
        """Retained-direct read from the bound tenant bus's live state
        (`bus.namespaces`, [CXC-WS-RETAINED-REPLAY]'s accessor). A namespace with
        no retained value yet returns an explicit "no value yet", NEVER a
        fabricated default ([CXC-MCP-INVOKE-READ])."""
        bus = self._bus
        values: dict[str, Any] = {}
        for ns_name in binding.subscribes:
            ns = bus.namespaces.get(ns_name)
            # "no value yet" is THREE things, never a fabricated default
            # ([CXC-MCP-INVOKE-READ]): the namespace is unregistered, OR it was
            # registered but nothing ever arrived (`age is None`), OR its last
            # value is no longer believable (`is_unknown`, the freshness gate).
            if ns is None or ns.age is None or is_unknown(ns.value):
                values[ns_name] = {"present": False}
            else:
                values[ns_name] = {"present": True, "value": ns.value}
        if len(binding.subscribes) == 1:
            only = values[binding.subscribes[0]]
            return {"ok": True, "namespace": binding.subscribes[0],
                    "present": only["present"],
                    "value": only.get("value")}
        return {"ok": True, "values": values}

    # -- COMMAND: a MUTATING query round-trip ([CXC-MCP-INVOKE-COMMAND]) --- #
    def _invoke_command(self, binding: ToolBinding, args: dict,
                        timeout: Optional[float]) -> dict:
        """A `command` tool is a MUTATING QUERY ROUND-TRIP
        ([DEC-MCP-COMMAND-DIRECTION-2026-08-25]). Mechanically it is IDENTICAL
        to `_invoke_read(read_mode=query)`: the connector subscribes the
        per-session result namespace BEFORE publishing the request, correlates
        by `request_id`, and returns the responder's authoritative reply
        VERBATIM. It differs from a `read` ONLY in intent — the responder
        plugin's own handler MUTATES then answers; the connector still only
        FORWARDS the request and DECODES the correlated reply, deriving nothing
        and reading no other namespace ([DX-CONNECTOR-FORWARDS-NEVER-TRANSFORMS],
        G1). It is DISTINCT from `read-write`, whose reply is an UNCORRELATED
        re-read rather than the mutation's own authoritative outcome."""
        return self._query_round_trip(binding, args, timeout)

    def _read_query(self, binding: ToolBinding, args: dict,
                    timeout: Optional[float]) -> dict:
        """A `query`-mode read invokes [SPEC:CXC-BUS-QUERY]'s idiom VERBATIM:
        subscribe to the per-session result namespace BEFORE publishing the
        query ([CXC-BUS-QUERY-ORDERING]), correlate by `request_id`
        ([CXC-BUS-QUERY-IDIOM]), and decode the responder's `val` VERBATIM. The
        REQUESTER owns the timeout; a query that never answers surfaces a named
        "no answer" error, never a hang ([CXC-BUS-QUERY-ORDERING])."""
        return self._query_round_trip(binding, args, timeout)

    def _query_round_trip(self, binding: ToolBinding, args: dict,
                          timeout: Optional[float]) -> dict:
        """The shared [SPEC:CXC-BUS-QUERY] correlated round-trip that BOTH a
        `query`-mode read and a `command` mutation ride on: subscribe the
        per-session result namespace BEFORE publishing the request, correlate by
        `request_id`, return the responder's reply VERBATIM. The mechanism is
        indifferent to whether the responder's handler mutates — that is the
        plugin's concern, never the connector's."""
        if not binding.subscribes:
            return {"ok": False,
                    "error": f"round-trip tool {binding.exposed_name!r} declares "
                             f"no subscribes namespace to query"}
        query_ns = binding.subscribes[0]
        resource_root = (query_ns[: -len("/query")]
                         if query_ns.endswith("/query") else query_ns)
        src = self._src()
        result_ns = f"{resource_root}/result/{src}"
        request_id = uuid.uuid4().hex
        pending = _PendingCall(request_id=request_id)
        with self._lock:
            self._pending[request_id] = pending
        # (1) subscribe-before-publish — once per result namespace per session.
        self._ensure_result_subscription(result_ns)
        # (2) publish the query EVENT (val carries request_id + the args).
        query_val = {"request_id": request_id, **args}
        self._bus.publish_event(query_ns, "query", data=query_val, source=src)
        # (3) requester-owns-timeout.
        wait = self.DEFAULT_QUERY_TIMEOUT if timeout is None else timeout
        got = pending.event.wait(wait)
        with self._lock:
            self._pending.pop(request_id, None)
        if not got:
            return {"ok": False, "error": "no answer",
                    "request_id": request_id, "timeout": wait}
        # decode VERBATIM — no summarize/aggregate/reformat ([CXC-MCP-INVOKE-READ]).
        return {"ok": True, "request_id": request_id, "result": pending.result}

    def _ensure_result_subscription(self, result_ns: str) -> None:
        if result_ns in self._result_subs:
            return
        self._result_subs.add(result_ns)
        self._bus.subscribe_events(result_ns, self._on_query_result)

    def _on_query_result(self, namespace: str, event_type: str, data: dict) -> None:
        """Correlate an answer to its outstanding request by `request_id`; two
        concurrent in-flight calls on the SAME session never cross-deliver
        ([CXC-MCP-BOUNDARIES] row 12)."""
        rid = (data or {}).get("request_id")
        with self._lock:
            pending = self._pending.get(rid)
        if pending is None:
            return  # not ours, or already resolved/timed-out
        pending.result = data
        pending.event.set()


# --------------------------------------------------------------------------- #
# MCPConnector — the accept surface. Owns the SDK server (LAZY-imported) and a
# collection of peers, one per MCP session ([CXC-MCP-SESSION-PEER]).
#
# NOT a Transport ABC subclass in the DIAL sense: like the WS connector it
# accepts inbound sessions rather than dialing one fabric, so it exposes a
# `sessions` collection and a peer factory instead of send/subscribe.
# --------------------------------------------------------------------------- #
def _validate_bind_host(host: str) -> str:
    """Refuse a world-facing bind — v1 has no auth ([DD-MCP-AUTH-GATE]).

    `0.0.0.0`/`::`/empty exposes the surface beyond the trusted LAN; that MUST
    be a loud refusal, never a silent default that bypasses the deferred gate
    ([CXC-MCP-BOUNDARIES] row 11)."""
    h = (host or "").strip()
    if h in ("", "0.0.0.0", "::", "*"):
        raise UnsafeBindError(
            f"refusing to bind MCP listen surface to {host!r}: v1 ships no auth "
            f"([DD-MCP-AUTH-GATE]); bind the trusted LAN interface explicitly "
            f"(a specific host/IP), never the world.")
    return h


class MCPConnector:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8431,
        registry: Optional[ServerRegistry] = None,
        *,
        wire: str = "streamable-http",
    ):
        # bind guard is load-bearing (no auth in v1) — refuse a world bind up
        # front, at construction, so a mis-declared spec fails LOUDLY.
        self.host = _validate_bind_host(host)
        self.port = int(port)
        self._registry = registry
        self.wire = str(wire)
        self._peers: dict[str, MCPPeer] = {}
        self._peer_seq = 0
        self._lock = threading.Lock()
        self._server = None        # the lazy-built SDK server
        self._started = False
        # session -> peer, resolved via the SDK's own per-request contextvar
        # ([CXC-MCP-SESSION-PEER]) — populated by `_build_sdk_server`.
        self._session_peers: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
        # the background accept-loop thread + its event loop, mirroring
        # `WSConnector`'s pattern ([CXC-WS-CONNECTOR-INTERFACE]) — a real
        # streamable-HTTP listen socket, not merely a constructed SDK object.
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._session_manager = None
        self._uvicorn_server = None
        self._serve_task = None
        self._bound_port: Optional[int] = None
        self._ready = threading.Event()  # set on EITHER bind success or failure
        self._bind_error: Optional[BaseException] = None

    # -- kernel wiring --------------------------------------------------- #
    def bind_registry(self, registry: ServerRegistry) -> None:
        """The kernel hands the connector its cross-tenant registry — ONCE at
        server level, the equivalent of a per-tenant `attach_connector`. Must be
        set before a peer can select a tenant ([CXC-MCP-TENANT-BOUNDARY])."""
        self._registry = registry

    @property
    def sessions(self) -> list[MCPPeer]:
        """Enumerate live peers (status/debug, [CXC-MCP-CONNECTOR-INTERFACE])."""
        with self._lock:
            return list(self._peers.values())

    @property
    def is_started(self) -> bool:
        return self._started

    @property
    def bound_port(self) -> Optional[int]:
        """The actual listen port (resolves a `port=0` ephemeral bind), mirroring
        `WSConnector.bound_port` — a caller/test can discover the real port."""
        return self._bound_port

    def tool_registry(self, tenant_id: str) -> dict[str, ToolBinding]:
        """A tenant's aggregated tool set, resolved through a throwaway BOUND
        peer — the accessor [CXC-MCP-CONNECTOR-INTERFACE] names for status
        surfaces. (A live session resolves its own via `MCPPeer.tool_registry`.)"""
        peer = MCPPeer(peer_id=f"{self._probe_id()}", registry=self._require_registry())
        peer.select_tenant(tenant_id)
        return peer.tool_registry()

    # -- peer factory (also the pure-logic entry point tests use) -------- #
    def new_peer(self) -> MCPPeer:
        registry = self._require_registry()
        with self._lock:
            self._peer_seq += 1
            peer_id = f"mcp-{self._peer_seq}"
            peer = MCPPeer(peer_id, registry)
            self._peers[peer_id] = peer
        return peer

    def drop_peer(self, peer: MCPPeer) -> None:
        peer.close()
        with self._lock:
            self._peers.pop(peer.peer_id, None)

    def _require_registry(self) -> ServerRegistry:
        if self._registry is None:
            raise RuntimeError(
                "MCPConnector has no ServerRegistry — call bind_registry() at "
                "kernel standup before accepting sessions")
        return self._registry

    def _probe_id(self) -> str:
        with self._lock:
            self._peer_seq += 1
            return f"mcp-probe-{self._peer_seq}"

    # -- lifecycle ------------------------------------------------------- #
    def start(self) -> None:
        """Stand up a REAL streamable-HTTP listen socket on the trusted-LAN
        surface, mirroring `WSConnector.start()`'s background-thread pattern
        ([CXC-WS-CONNECTOR-INTERFACE]) — this is a live accept loop an actual
        MCP client can dial, not merely a constructed SDK object.

        The MCP SDK is imported HERE, lazily — its absence never breaks
        construction, `known_types()`, or the other transport factories; it only
        fails a real `start()` loudly ([CXC-MCP-CONNECTOR-INTERFACE]).

        NEEDS-GLASS: a real MCP client driving this wire, and a >=2-tenant
        deployment confirming no cross-tenant leak, are the two-tier human gate
        ([CXC-MCP-ACCEPTANCE] MCP-V3/V4/V7/V8). This method wires the SDK
        server's per-session `ServerSession` to its OWN `MCPPeer`
        ([CXC-MCP-SESSION-PEER]); the peer methods themselves are fully
        machine-proven (`test_transport_mcp.py`)."""
        self._require_registry()
        if self.wire != "streamable-http":
            # [DD-MCP-TRANSPORT-CHOICE]: v1 ships streamable-HTTP only (this
            # build's own resolved assumption, DEC-MCP-DD-RESOLVED-2026-08-25).
            # A declared `wire` this connector cannot actually serve MUST fail
            # loudly here — never silently degrade to serving streamable-HTTP
            # anyway, which is what omitting this guard would do.
            raise NotImplementedError(
                f"MCP connector wire {self.wire!r} is not implemented — v1 "
                f"serves 'streamable-http' only; stdio is the degenerate "
                f"peer-count-1 case of the SAME MCPPeer model but has no wire "
                f"binding built yet ([DD-MCP-TRANSPORT-CHOICE]).")
        try:
            from mcp.server.lowlevel import Server as _MCPServer  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            raise McpDependencyError(
                "the MCP SDK is not installed — `pip install composeio[mcp]`. "
                "The umbrella cannot serve without it; every other transport "
                "factory is unaffected because this import is lazy.") from exc
        self._server = self._build_sdk_server(_MCPServer)
        self._ready = threading.Event()
        self._bind_error = None
        self._thread = threading.Thread(
            target=self._run_loop, name=f"mcp-connector-{self.port}", daemon=True)
        self._thread.start()
        # [CXC-WS-BOUNDARIES]-equivalent: a bind failure (port in use, no
        # permission) MUST degrade LOUDLY, naming the substrate and reason —
        # `_ready` is set on EITHER outcome, so a failed bind surfaces
        # immediately, not after the full timeout.
        if not self._ready.wait(timeout=10.0):
            raise RuntimeError(
                f"MCP connector failed to bind {self.host}:{self.port} in 10s "
                f"(no response from accept-loop thread)")
        if self._bind_error is not None:
            self._thread = None
            raise RuntimeError(
                f"MCP connector failed to bind {self.host}:{self.port}: "
                f"{self._bind_error}") from self._bind_error
        self._started = True
        logger.info(
            "MCP umbrella endpoint LISTENING on %s:%s/mcp (wire=%s) — a real "
            "streamable-HTTP accept loop, per-session MCPPeer mapping wired "
            "([CXC-MCP-SESSION-PEER]).", self.host, self._bound_port or self.port,
            self.wire)

    def _build_sdk_server(self, server_cls):
        """Bridge the MCP low-level Server's list_tools/call_tool to a per-session
        peer. Each MCP session maps to ONE MCPPeer ([CXC-MCP-SESSION-PEER]); the
        session's tenant binding lives on that peer. A single-process (stdio)
        session is the degenerate peer-count-1 case, the SAME peer class.

        The SDK's low-level `Server` registers ONE set of handlers shared by
        every session on the connector — sessions are told apart at CALL TIME
        via `server.request_context.session`, a request-scoped contextvar the
        SDK sets around every `list_tools`/`call_tool` dispatch (unique per
        `ServerSession`, i.e. unique per streamable-HTTP session/per stdio
        process). `_session_peers` maps that live `ServerSession` object to its
        own `MCPPeer`, created on first use and torn down automatically via
        `weakref.finalize` once the session object is collected — never one
        shared mutable peer serving two sessions
        ([CXC-MCP-SESSION-PEER] negative-control)."""
        import mcp.types as types

        server = server_cls("composeserver")

        def _peer_for_current_session() -> MCPPeer:
            try:
                session = server.request_context.session
            except LookupError:  # pragma: no cover - defensive; always in-request here
                session = None
            if session is None:
                # No request in flight — should never happen inside a
                # list_tools/call_tool handler, but degrade to a fresh ad-hoc
                # peer rather than crash the call.
                return self.new_peer()
            peer = self._session_peers.get(session)
            if peer is None:
                peer = self.new_peer()
                self._session_peers[session] = peer
                weakref.finalize(session, self.drop_peer, peer)
            return peer

        @server.list_tools()
        async def _list_tools():
            peer = _peer_for_current_session()
            out = []
            for t in peer.list_tools():
                out.append(types.Tool(
                    name=t["name"],
                    description=t.get("description", ""),
                    inputSchema=t.get("inputSchema", {"type": "object"})))
            return out

        @server.call_tool()
        async def _call_tool(name: str, arguments: dict):
            peer = _peer_for_current_session()
            try:
                result = peer.invoke(name, arguments)
            except (NotBoundError, ToolNotFoundError, ToolCollisionError,
                    UnknownTenantError) as exc:
                result = {"ok": False, "error": str(exc)}
            return [types.TextContent(type="text", text=_json_dump(result))]

        return server

    def _run_loop(self) -> None:
        """The accept-loop thread body — a private event loop running the real
        uvicorn/Starlette streamable-HTTP server, mirroring `WSConnector._run`."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            try:
                loop.run_until_complete(self._serve())
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "mcp connector failed to bind %s:%s: %s", self.host, self.port, exc)
                self._bind_error = exc
                self._ready.set()
                return
            self._ready.set()
            loop.run_forever()
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001
                pass
            loop.close()

    async def _serve(self) -> None:
        """Stand up the streamable-HTTP session manager behind a Starlette app
        served by uvicorn, at `POST/GET /mcp` — the SDK's own convention. Runs
        to completion of `startup()` only; `run_forever()` (called by
        `_run_loop`) keeps the accept loop and `main_loop()` alive afterward."""
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import Mount
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        self._session_manager = StreamableHTTPSessionManager(
            app=self._server, stateless=False)

        async def _asgi(scope, receive, send):
            await self._session_manager.handle_request(scope, receive, send)

        app = Starlette(
            routes=[Mount("/mcp", app=_asgi)],
            lifespan=lambda _app: self._session_manager.run())
        config = uvicorn.Config(
            app, host=self.host, port=self.port, log_level="warning", lifespan="on")
        self._uvicorn_server = uvicorn.Server(config)
        self._serve_task = asyncio.ensure_future(self._uvicorn_server.serve())
        # wait for uvicorn to either bind (`.started`) or die trying
        while not self._uvicorn_server.started and not self._serve_task.done():
            await asyncio.sleep(0.01)
        if self._serve_task.done():
            exc = self._serve_task.exception()
            if exc is not None:
                raise exc
            if not self._uvicorn_server.started:
                raise RuntimeError("uvicorn server exited before starting")
        try:
            sock = self._uvicorn_server.servers[0].sockets[0]
            self._bound_port = sock.getsockname()[1]
        except Exception:  # noqa: BLE001
            self._bound_port = self.port

    def stop(self) -> None:
        for peer in self.sessions:
            self.drop_peer(peer)
        loop = self._loop
        if loop is not None and self._uvicorn_server is not None:
            async def _shutdown():
                self._uvicorn_server.should_exit = True
                if self._serve_task is not None:
                    try:
                        await asyncio.wait_for(self._serve_task, timeout=5.0)
                    except Exception:  # noqa: BLE001
                        pass
                # a short grace period lets nested cleanup callbacks scheduled
                # during shutdown (e.g. sse_starlette's per-session watcher
                # tasks) actually run before the loop stops, rather than being
                # torn down mid-flight ("Task was destroyed but it is pending").
                for _ in range(5):
                    await asyncio.sleep(0.02)
            try:
                fut = asyncio.run_coroutine_threadsafe(_shutdown(), loop)
                fut.result(timeout=10.0)
            except Exception as exc:  # noqa: BLE001
                logger.debug("mcp shutdown: %s", exc)
            loop.call_soon_threadsafe(loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._thread = None
        self._loop = None
        self._server = None
        self._session_manager = None
        self._uvicorn_server = None
        self._serve_task = None
        self._bound_port = None
        self._session_peers = weakref.WeakKeyDictionary()
        self._started = False
        self._ready = threading.Event()
        self._bind_error = None


def _json_dump(obj: Any) -> str:
    import json
    try:
        return json.dumps(obj, default=str)
    except Exception:  # noqa: BLE001
        return str(obj)


# --------------------------------------------------------------------------- #
# Registry factory — `type: mcp`, registered into the SAME `_FACTORIES` idiom
# the DIAL transports use. Imported LAZILY by the registry so the MCP SDK being
# absent never breaks the other factories ([CXC-MCP-CONNECTOR-NOT-PLUGIN]).
# --------------------------------------------------------------------------- #
def _mcp(spec: dict, app_id: str) -> MCPConnector:
    """Build the MCP umbrella connector from a declared `type: mcp` spec.

    `spec` carries `host`/`port`/`wire` (and, when built by the kernel, a live
    `registry`). `app_id` is accepted for signature parity with the DIAL
    factories. A missing `host`/`port` applies a documented default
    (127.0.0.1:8431) — never a silent world-facing bind, and a declared
    `0.0.0.0`/`::` is REFUSED ([DD-MCP-AUTH-GATE]). The kernel calls
    `bind_registry()` post-build if it did not pass one in `spec`.

    Building constructs NO SDK object — the MCP SDK is imported only at
    `start()`, so this factory succeeds (and `known_types()` reports `mcp`) even
    with the SDK absent."""
    return MCPConnector(
        host=str(spec.get("host", "127.0.0.1")),
        port=int(spec.get("port", 8431)),
        registry=spec.get("registry"),
        wire=str(spec.get("wire", "streamable-http")),
    )


def register_mcp() -> None:
    """Register `type: mcp` into the transport registry. Idempotent."""
    from composeio import transport_registry
    transport_registry.register("mcp", _mcp)
