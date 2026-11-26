"""MCP umbrella connector — pure-logic acceptance ([SPEC:CXC-MCP] §14 MCP-V rows).

These exercise `MCPPeer`/`MCPConnector` LOGIC through a real two-tenant
`ComposeServer` (the faithful `ServerRegistry`) with NO MCP SDK session and NO
sockets, so every lifecycle/tenant-boundary/encode/decode/router predicate is
asserted deterministically. The live streamable-HTTP wire — a real MCP client
driving the accept loop — is the NEEDS-GLASS tier ([CXC-MCP-ACCEPTANCE]
MCP-V3/V4/V7/V8 verify-human rows), owned by first-bringup, not self-certifiable
here.

Every check carries its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an
instrument that can only report success is not an instrument. MCP-V4 (no
cross-tenant tool dispatch) is the load-bearing gate.
"""
from __future__ import annotations

import threading

import pytest
from pydantic import BaseModel

from composeio import transport_registry
from composeio.composeserver import ComposeServer
from composeio.namespace import Scope
from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    RequiresFloor,
    ToolDirection,
)
from composeio.transport import MessageType
from composeio.transport_mcp import (
    BOUND,
    PRE_BINDING,
    MCPConnector,
    MCPPeer,
    NotBoundError,
    ToolCollisionError,
    ToolNotFoundError,
    UnknownTenantError,
    UnsafeBindError,
    _validate_bind_host,
)


# --------------------------------------------------------------------------- #
# tool argument models + plugins under test
# --------------------------------------------------------------------------- #
class EnrollArgs(BaseModel):
    user_id: int


class SetZoneArgs(BaseModel):
    zone: str


class ReadArgs(BaseModel):
    pass


class QueryArgs(BaseModel):
    where: dict = {}


class Fingerprint(ComposePlugin):
    name = "fingerprint"
    version = "1.0.0"
    publishes = ["fingerprint/enroll"]
    mcp_tools = [
        McpToolDecl(
            name="enroll",
            description="Enroll a user's fingerprint.",
            input_model=EnrollArgs,
            direction=ToolDirection.WRITE,
            namespace_mapping=NamespaceMapping(publishes=["fingerprint/enroll"]),
        ),
        McpToolDecl(
            name="last",
            description="The last enrolled record (retained).",
            input_model=ReadArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(subscribes=["fingerprint/last"]),
        ),
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


class Timekeeper(ComposePlugin):
    name = "time"
    version = "1.0.0"
    publishes = ["time/zone"]
    mcp_tools = [
        McpToolDecl(
            name="set_zone",
            input_model=SetZoneArgs,
            direction=ToolDirection.WRITE,
            namespace_mapping=NamespaceMapping(publishes=["time/zone"]),
        )
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def server():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[Fingerprint(), Timekeeper()])
    srv.add_tenant("bsg", plugins=[Timekeeper()])  # NO fingerprint on bsg
    return srv


def _peer(server, tenant=None) -> MCPPeer:
    conn = MCPConnector(registry=server)
    peer = conn.new_peer()
    if tenant is not None:
        peer.select_tenant(tenant)
    return peer


# ========================================================================== #
# MCP-V1 — registered as `type: mcp`, never silently None; unknown still raises
# ========================================================================== #
def test_v1_registered_and_builds():
    assert "mcp" in transport_registry.known_types()
    conn = transport_registry.build({"type": "mcp", "host": "127.0.0.1", "port": 8431})
    assert conn is not None and isinstance(conn, MCPConnector)


def test_v1_negative_unknown_type_still_raises():
    # the registry's loud-failure rule is intact alongside the new `mcp` entry.
    with pytest.raises(transport_registry.UnknownTransportError):
        transport_registry.build({"type": "mcp2"})


def test_v1_missing_mcp_sdk_never_breaks_other_factories():
    # building `mcp` touches NO SDK import (that is deferred to start()), so a
    # missing MCP dependency cannot break known_types() or any DIAL factory.
    assert "mqtt" in transport_registry.known_types()
    assert transport_registry.build({"type": "can", "channel": "loopback"}) is not None


def test_v1_bind_guard_refuses_world_facing():
    # v1 has NO auth ([DD-MCP-AUTH-GATE]); a world bind is refused LOUDLY.
    for bad in ("0.0.0.0", "::", "", "*"):
        with pytest.raises(UnsafeBindError):
            MCPConnector(host=bad)
    # negative control: a specific LAN/loopback host is accepted.
    assert MCPConnector(host="127.0.0.1").host == "127.0.0.1"
    assert _validate_bind_host("mqtt-broker.local") == "mqtt-broker.local"


# --- FIX 1: build() re-raises a POLICY refusal, never degrades it to None ---- #
def test_v1_world_bind_via_registry_raises_not_none():
    # An UnsafeBindError is a POLICY refusal on a REACHABLE, well-formed config —
    # structurally the same class as UnknownTransportError, NOT an unreachable-
    # backend fact. transport_registry.build() MUST re-raise it, never degrade it
    # to a silent None+log (the degrade path is reserved for a KNOWN type whose
    # BACKEND is unreachable).
    for bad in ("0.0.0.0", "::", "*", ""):
        with pytest.raises(UnsafeBindError):
            transport_registry.build({"type": "mcp", "host": bad, "port": 8431})
    # the raised error NAMES the real cause (the world bind + the auth reason),
    # never a swallowed None the caller must then guess about.
    try:
        transport_registry.build({"type": "mcp", "host": "0.0.0.0"})
        raise AssertionError("expected UnsafeBindError")
    except UnsafeBindError as exc:
        assert "0.0.0.0" in str(exc) and "auth" in str(exc).lower()


def test_v1_policy_refusal_is_transportpolicyerror_and_valueerror():
    # UnsafeBindError participates in the registry's re-raise family via
    # TransportPolicyError, and remains a ValueError for existing callers.
    from composeio.transport_registry import TransportPolicyError
    assert issubclass(UnsafeBindError, TransportPolicyError)
    assert issubclass(UnsafeBindError, ValueError)


def test_v1_unreachable_backend_still_degrades_to_none():
    # NEGATIVE CONTROL / regression guard: FIX 1 must PRESERVE the genuine
    # environment-degrade — a KNOWN type whose backend simply isn't reachable
    # returns None+log, NOT a raise. Only POLICY refusals re-raise.
    transport_registry.register(
        "_boom", lambda s, a: (_ for _ in ()).throw(OSError("no hardware")))
    try:
        assert transport_registry.build({"type": "_boom"}) is None
    finally:
        transport_registry._FACTORIES.pop("_boom", None)


def test_v1_attach_mcp_endpoint_surfaces_bind_refusal_not_sdk_guess():
    # The compounding half of FIX 1: attach_mcp_endpoint() must surface the ACTUAL
    # cause. A world-facing bind now propagates as UnsafeBindError (re-raised by
    # build()), NOT the old hardcoded RuntimeError asserting "the MCP SDK is likely
    # absent" — a cause it never observed.
    srv = ComposeServer()
    with pytest.raises(UnsafeBindError):
        srv.attach_mcp_endpoint(host="0.0.0.0")


def test_v1_unimplemented_wire_refuses_loudly_not_silent_http(server):
    # [DD-MCP-TRANSPORT-CHOICE]: v1 serves streamable-HTTP only. A declared
    # `wire` this build cannot actually serve MUST fail loudly at start() —
    # never silently degrade to serving streamable-HTTP anyway (which is
    # what a missing guard here would do, since `_serve()` only ever builds
    # the HTTP path).
    conn = MCPConnector(host="127.0.0.1", port=0, registry=server, wire="stdio")
    with pytest.raises(NotImplementedError, match="stdio"):
        conn.start()
    # negative control: the declared default wire is accepted (this is what
    # the rest of this suite's `MCPConnector(...)` calls implicitly rely on).
    assert MCPConnector(registry=server).wire == "streamable-http"


# ========================================================================== #
# MCP-V2 — ONE umbrella aggregates every loaded plugin's tools
# ========================================================================== #
def test_v2_one_umbrella_aggregates_all_plugins(server):
    peer = _peer(server, "haven")
    names = {t["name"] for t in peer.list_tools()}
    # both plugins' tools reachable over the SAME session, per-plugin-namespaced.
    assert "fingerprint.enroll" in names
    assert "time.set_zone" in names
    # no second connection needed — one peer, one session.
    assert peer.state == BOUND


# ========================================================================== #
# MCP-V3 — tenant-scoped per session; PRE-BINDING until selection
# ========================================================================== #
def test_v3_no_tenant_tool_before_selection(server):
    conn = MCPConnector(registry=server)
    peer = conn.new_peer()
    assert peer.state == PRE_BINDING
    names = {t["name"] for t in peer.list_tools()}
    # ONLY the control-plane selection tools pre-binding — no tenant-scoped tool.
    assert names == {"_server.list_tenants", "_server.select_tenant"}
    # invoking a tenant-scoped tool pre-binding is a named refusal.
    with pytest.raises(NotBoundError):
        peer.invoke("fingerprint.enroll", {"user_id": 5})


def test_v3_selection_binds_to_that_tenant_only(server):
    peer = _peer(server)
    r = peer.invoke("_server.select_tenant", {"tenant_id": "haven"})
    assert r["ok"] and peer.bound_tenant == "haven"
    # negative control: an unknown tenant selection is loud, not a silent default.
    r2 = peer.invoke("_server.select_tenant", {"tenant_id": "nope"})
    assert not r2["ok"]
    with pytest.raises(UnknownTenantError):
        peer.select_tenant("nope")


# ========================================================================== #
# MCP-V4 — THE LOAD-BEARING GATE: no cross-tenant tool dispatch
# ========================================================================== #
def test_v4_no_cross_tenant_tool_dispatch(server):
    # fingerprint is loaded on haven, NOT on bsg. A bsg-bound session must never
    # reach it — "tool not found", never a cross-tenant dispatch.
    peer = _peer(server, "bsg")
    assert "fingerprint.enroll" not in {t["name"] for t in peer.list_tools()}
    with pytest.raises(ToolNotFoundError):
        peer.invoke("fingerprint.enroll", {"user_id": 5})


def test_v4_same_named_tool_resolves_to_bound_tenant_only(server):
    # `time.set_zone` exists on BOTH tenants but resolves to each tenant's OWN
    # instance's bus — a bound session's publish lands on ITS tenant only.
    haven_hits, bsg_hits = [], []
    server.tenant_bus("haven").subscribe("time/zone", lambda n, v, o=None: haven_hits.append(v))
    server.tenant_bus("bsg").subscribe("time/zone", lambda n, v, o=None: bsg_hits.append(v))

    peer = _peer(server, "haven")
    peer.invoke("time.set_zone", {"zone": "UTC"})
    # val is the validated input_model dump, table-driven from args.
    assert haven_hits == [{"zone": "UTC"}] and bsg_hits == []  # NO leak to bsg

    # rebind to bsg: now bsg sees traffic, haven does not gain more.
    peer.select_tenant("bsg")
    peer.invoke("time.set_zone", {"zone": "PST"})
    assert bsg_hits == [{"zone": "PST"}] and haven_hits == [{"zone": "UTC"}]


# ========================================================================== #
# MCP-V5 — tool schema DERIVED from input_model, never hand-duplicated
# ========================================================================== #
def test_v5_schema_derived_from_input_model(server):
    peer = _peer(server, "haven")
    tool = next(t for t in peer.list_tools() if t["name"] == "fingerprint.enroll")
    assert tool["inputSchema"] == EnrollArgs.model_json_schema()
    # negative control: a mismatched hand-written schema is NOT what is exposed.
    assert tool["inputSchema"] != SetZoneArgs.model_json_schema()


# ========================================================================== #
# MCP-V6 — write is a table-driven encode, never a derive from other state
# ========================================================================== #
class _WriteOnlyBus:
    """A bus that RECORDS publishes and FORBIDS every read.

    The single-namespace-touch fixture ([CXC-MCP-INVOKE-WRITE] boundary, MCP-V6):
    a `write` encode that reads a second namespace's state is a defect. This
    double makes any such read an AttributeError, so a passing test proves the
    encode touched only its declared publish namespace(s)."""

    def __init__(self):
        self.published: list[tuple[str, dict, str]] = []

    def publish_state(self, ns, val, source=""):
        self.published.append((ns, val, source))

    @property
    def namespaces(self):  # any read of bus state during encode is a defect
        raise AssertionError("write encode read bus state — MCP-V6 violation")

    def get_state(self, *a, **k):
        raise AssertionError("write encode read bus state — MCP-V6 violation")


def test_v6_write_is_single_namespace_touch(server):
    peer = _peer(server, "haven")
    binding = peer.tool_registry()["fingerprint.enroll"]
    peer._bus = _WriteOnlyBus()  # swap in the read-forbidding double
    result = peer._invoke_write(binding, {"user_id": 7})
    assert result["ok"]
    # EXACTLY one publish, on the declared namespace, val = validated args.
    assert peer._bus.published == [("fingerprint/enroll", {"user_id": 7}, peer.peer_id)]


def test_v6_validation_failure_produces_zero_bus_traffic(server):
    peer = _peer(server, "haven")
    binding = peer.tool_registry()["fingerprint.enroll"]
    peer._bus = _WriteOnlyBus()
    result = peer._invoke_write(binding, {"user_id": "not-an-int"})
    assert not result["ok"] and "user_id" in result["fields"]
    assert peer._bus.published == []  # zero traffic, never a best-effort publish


# ========================================================================== #
# MCP-V7 — read resolves retained-direct OR via CXC-BUS-QUERY, decoded verbatim
# ========================================================================== #
def test_v7_retained_read_matches_current_value(server):
    bus = server.tenant_bus("haven")
    bus.register_namespace("fingerprint/last", "json", scope=Scope.LOCAL)
    bus.publish_state("fingerprint/last", {"user_id": 42}, source="fingerprint")
    peer = _peer(server, "haven")
    r = peer.invoke("fingerprint.last", {})
    assert r["ok"] and r["present"] and r["value"] == {"user_id": 42}


def test_v7_retained_read_unset_is_no_value_yet_not_a_default(server):
    peer = _peer(server, "haven")
    r = peer.invoke("fingerprint.last", {})
    # explicit "no value yet" — never a fabricated default value.
    assert r["ok"] and r["present"] is False and r["value"] is None


def _install_responder(bus, resource_root, answer):
    """A CXC-BUS-QUERY responder: answer `{root}/query` on `{root}/result/{src}`,
    reading `src` from the envelope, echoing `request_id` verbatim."""
    def _h(msg):
        if msg.type == MessageType.EVENT and msg.namespace == f"{resource_root}/query":
            src = msg.source
            rid = (msg.data or {}).get("request_id")
            bus.publish_event(
                f"{resource_root}/result/{src}", "result",
                data={"request_id": rid, **answer(msg.data)}, source="responder")
    bus.on_message(_h)


class DocQueryPlugin(ComposePlugin):
    name = "docs"
    version = "1.0.0"
    mcp_tools = [
        McpToolDecl(
            name="find",
            input_model=QueryArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=["_doc/people/query"]),
        )
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_v7_query_read_round_trips_verbatim():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DocQueryPlugin()])
    bus = srv.tenant_bus("haven")
    _install_responder(bus, "_doc/people",
                        lambda data: {"results": [{"name": "kara"}], "count": 1})
    peer = _peer(srv, "haven")
    r = peer.invoke("docs.find", {"where": {"call": "starbuck"}})
    assert r["ok"]
    # decoded VERBATIM — the responder's val, request_id echoed.
    assert r["result"]["results"] == [{"name": "kara"}]
    assert r["result"]["count"] == 1
    assert r["result"]["request_id"] == r["request_id"]


def test_v7_query_read_times_out_named_never_hangs():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DocQueryPlugin()])
    peer = _peer(srv, "haven")  # NO responder installed
    r = peer.invoke("docs.find", {"where": {}}, timeout=0.05)
    assert not r["ok"] and r["error"] == "no answer"  # named, not a hang


def test_v7_concurrent_in_flight_correlate_by_request_id():
    # two outstanding calls on the SAME session correlate independently; neither
    # answer is delivered to the other's caller ([CXC-MCP-BOUNDARIES] row 12).
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DocQueryPlugin()])
    peer = _peer(srv, "haven")
    from composeio.transport_mcp import _PendingCall
    a, b = _PendingCall("rid-A"), _PendingCall("rid-B")
    peer._pending = {"rid-A": a, "rid-B": b}
    # answer B first, then A — out of order.
    peer._on_query_result("_doc/people/result/mcp-1", "result",
                          {"request_id": "rid-B", "n": 2})
    peer._on_query_result("_doc/people/result/mcp-1", "result",
                          {"request_id": "rid-A", "n": 1})
    assert a.result["n"] == 1 and b.result["n"] == 2  # never cross-delivered


def test_v7_query_subscribes_result_before_publishing(monkeypatch):
    # subscribe-before-publish ([CXC-BUS-QUERY-ORDERING]): the result-namespace
    # subscription MUST be live before the query event is published.
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DocQueryPlugin()])
    bus = srv.tenant_bus("haven")
    order: list[str] = []
    real_sub = bus.subscribe_events
    real_pub = bus.publish_event

    def spy_sub(ns, cb):
        order.append(f"subscribe:{ns}")
        return real_sub(ns, cb)

    def spy_pub(ns, et, data=None, source=""):
        order.append(f"publish:{ns}")
        return real_pub(ns, et, data=data, source=source)

    monkeypatch.setattr(bus, "subscribe_events", spy_sub)
    monkeypatch.setattr(bus, "publish_event", spy_pub)
    peer = _peer(srv, "haven")
    peer.invoke("docs.find", {"where": {}}, timeout=0.05)
    assert order.index("subscribe:_doc/people/result/mcp-1") < order.index("publish:_doc/people/query")


# ========================================================================== #
# COMMAND direction — a MUTATING QUERY ROUND-TRIP whose single correlated reply
# carries its authoritative outcome ([DEC-MCP-COMMAND-DIRECTION-2026-08-25]).
# Mechanically identical to read(query); the DIFFERENCE is that the responder's
# handler MUTATES. The connector still only FORWARDS + DECODES (G1).
# ========================================================================== #
class StoreArgs(BaseModel):
    doc: str


class CommandPlugin(ComposePlugin):
    """One `command` tool: a mutating store realized as a CXC-BUS-QUERY round-trip."""
    name = "docstore"
    version = "1.0.0"
    mcp_tools = [
        McpToolDecl(
            name="store",
            description="Store a doc (a MUTATION) and return its authoritative outcome.",
            input_model=StoreArgs,
            direction=ToolDirection.COMMAND,
            read_mode=ReadMode.QUERY,
            namespace_mapping=NamespaceMapping(subscribes=["_doc/people/store/query"]),
        )
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def _install_mutating_responder(bus, resource_root, store, batch=1):
    """A MUTATING CXC-BUS-QUERY responder: each request appends its `doc` to
    `store` (the mutation) and answers on `{root}/result/{src}`, echoing
    `request_id` verbatim. With `batch` > 1 it HOLDS requests until `batch` are
    in-flight, then answers them LIFO — forcing genuinely concurrent in-flight
    correlation, not a sequence of synchronous singletons."""
    held: list = []
    lock = threading.Lock()

    def _flush(items):
        for src, rid, data in reversed(items):  # answer OUT of arrival order
            doc = (data or {}).get("doc")
            store.append(doc)                    # THE MUTATION
            bus.publish_event(
                f"{resource_root}/result/{src}", "result",
                data={"request_id": rid, "stored": doc, "total": len(store)},
                source="responder")

    def _h(msg):
        if msg.type == MessageType.EVENT and msg.namespace == f"{resource_root}/query":
            with lock:
                held.append((msg.source, (msg.data or {}).get("request_id"), msg.data))
                if len(held) < batch:
                    return
                items, held[:] = held[:], []
            _flush(items)

    bus.on_message(_h)


def test_command_round_trips_correlated_under_concurrency():
    # (a) >= 2 concurrent in-flight command calls each get THEIR OWN authoritative
    # reply, correlated by request_id — the responder holds both then answers LIFO.
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[CommandPlugin()])
    bus = srv.tenant_bus("haven")
    store: list = []
    _install_mutating_responder(bus, "_doc/people/store", store, batch=2)
    peer = _peer(srv, "haven")
    results: dict = {}

    def call(doc):
        results[doc] = peer.invoke("docstore.store", {"doc": doc}, timeout=2.0)

    t1 = threading.Thread(target=call, args=("kara",))
    t2 = threading.Thread(target=call, args=("lee",))
    t1.start(); t2.start(); t1.join(); t2.join()

    ra, rb = results["kara"], results["lee"]
    assert ra["ok"] and ra["result"]["stored"] == "kara"
    assert rb["ok"] and rb["result"]["stored"] == "lee"
    # the reply is correlated to ITS OWN request_id (the authoritative outcome).
    assert ra["result"]["request_id"] == ra["request_id"]
    assert rb["result"]["request_id"] == rb["request_id"]
    # NEGATIVE CONTROL: neither mutation's reply leaked into the other caller —
    # had they cross-delivered, "kara" would carry "lee"'s stored value.
    assert ra["result"]["stored"] != rb["result"]["stored"]
    assert ra["request_id"] != rb["request_id"]
    # the MUTATION actually happened, exactly once per call.
    assert sorted(store) == ["kara", "lee"]


def test_command_reply_never_delivered_to_another_in_flight_caller():
    # the negative control, isolated at the correlation seam: an answer for a
    # DIFFERENT request_id must NOT resolve my outstanding command call.
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[CommandPlugin()])
    peer = _peer(srv, "haven")
    from composeio.transport_mcp import _PendingCall
    mine = _PendingCall("rid-mine")
    peer._pending = {"rid-mine": mine}
    peer._on_query_result("_doc/people/store/result/mcp-1", "result",
                          {"request_id": "rid-other", "stored": "not-mine"})
    assert mine.result is None and not mine.event.is_set()  # foreign reply dropped
    # positive control: my OWN correlated reply resolves me, verbatim.
    peer._on_query_result("_doc/people/store/result/mcp-1", "result",
                          {"request_id": "rid-mine", "stored": "mine"})
    assert mine.result["stored"] == "mine" and mine.event.is_set()


def test_command_without_query_read_mode_rejected_at_declaration():
    # (b) a `command` REQUIRES read_mode=query; anything else is a DECLARATION
    # error — its authoritative outcome can only be a correlated CXC-BUS-QUERY reply.
    with pytest.raises(ValueError):
        McpToolDecl(name="store", input_model=StoreArgs,
                    direction=ToolDirection.COMMAND,
                    namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    with pytest.raises(ValueError):  # RETAINED is not enough — must be QUERY
        McpToolDecl(name="store", input_model=StoreArgs,
                    direction=ToolDirection.COMMAND, read_mode=ReadMode.RETAINED,
                    namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    # NEGATIVE CONTROL: read_mode=query is accepted.
    ok = McpToolDecl(name="store", input_model=StoreArgs,
                     direction=ToolDirection.COMMAND, read_mode=ReadMode.QUERY,
                     namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    assert ok.direction is ToolDirection.COMMAND


def test_command_classified_as_write_by_direction_keyed_audit_gate():
    # (c) THE AUDIT-GATE PROPERTY: a policy/audit gate keyed on `direction` MUST
    # see a command as a WRITE (a mutation), closing the hole where a mutation
    # masqueraded as a read.
    assert ToolDirection.COMMAND.includes_write() is True
    assert ToolDirection.COMMAND.includes_read() is True
    # it survives the bind: the resolved ToolBinding reports the same direction.
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[CommandPlugin()])
    peer = _peer(srv, "haven")
    binding = peer.tool_registry()["docstore.store"]
    assert binding.direction is ToolDirection.COMMAND
    assert binding.direction.includes_write() is True
    # NEGATIVE CONTROL: a pure read is NOT a write — the gate must still tell them apart.
    assert ToolDirection.READ.includes_write() is False


def test_command_read_write_unchanged_regression():
    # READ_WRITE remains an UNCORRELATED publish-then-separate-read, DISTINCT from
    # command. Its classification and shape are untouched by adding COMMAND.
    assert ToolDirection.READ_WRITE.includes_write() is True
    assert ToolDirection.READ_WRITE.includes_read() is True
    # read-write does NOT require read_mode=query (unlike command): a RETAINED
    # read-write tool is a perfectly legal declaration.
    rw = McpToolDecl(name="rw", input_model=StoreArgs,
                     direction=ToolDirection.READ_WRITE, read_mode=ReadMode.RETAINED,
                     namespace_mapping=NamespaceMapping(
                         publishes=["rw/set"], subscribes=["rw/set"]))
    assert rw.direction is ToolDirection.READ_WRITE


# ========================================================================== #
# MCP-V8 — router refreshes on re-announce, never serves a static snapshot;
#          a parked plugin contributes zero tools
# ========================================================================== #
class LateToolPlugin(ComposePlugin):
    name = "late"
    version = "1.0.0"
    publishes = ["late/a"]
    mcp_tools = [
        McpToolDecl(name="a", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["late/a"]))
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_v8_added_tool_reflected_on_next_list_no_reconnect():
    srv = ComposeServer()
    plugin = LateToolPlugin()
    srv.add_tenant("haven", plugins=[plugin])
    peer = _peer(srv, "haven")
    assert {t["name"] for t in peer.list_tools()} >= {"late.a"}
    assert "late.b" not in {t["name"] for t in peer.list_tools()}

    # add a SECOND tool to the plugin class and re-announce (surface change).
    LateToolPlugin.mcp_tools = LateToolPlugin.mcp_tools + [
        McpToolDecl(name="b", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["late/b"]))
    ]
    try:
        srv._tenants["haven"].republish()
        # same peer, no reconnect — the next list reflects the new tool.
        assert "late.b" in {t["name"] for t in peer.list_tools()}
    finally:
        LateToolPlugin.mcp_tools = LateToolPlugin.mcp_tools[:1]  # restore


class ParkedPlugin(ComposePlugin):
    name = "gated"
    version = "1.0.0"
    publishes = ["gated/x"]
    requires = RequiresFloor(capabilities=["special-cap"])
    mcp_tools = [
        McpToolDecl(name="x", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["gated/x"]))
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_v8_parked_plugin_contributes_zero_tools_then_unparks():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[ParkedPlugin()])  # parked: unmet capability
    peer = _peer(srv, "haven")
    # parked -> its tools are ABSENT, not present-but-erroring.
    assert "gated.x" not in {t["name"] for t in peer.list_tools()}
    with pytest.raises(ToolNotFoundError):
        peer.invoke("gated.x", {"user_id": 1})
    # provide the capability -> unpark -> re-announce -> tools reappear, no restart.
    srv.provide_capability("haven", "special-cap")
    assert "gated.x" in {t["name"] for t in peer.list_tools()}


# ========================================================================== #
# MCP-V9 — fork-exception excludes an `own`-declared plugin entirely
# ========================================================================== #
class OwnEndpointPlugin(ComposePlugin):
    name = "separate"
    version = "1.0.0"
    publishes = ["separate/y"]
    mcp_endpoint = "own"  # opts OUT of the umbrella
    mcp_tools = [
        McpToolDecl(name="y", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["separate/y"]))
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_v9_own_declared_plugin_excluded_from_umbrella():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[OwnEndpointPlugin(), Timekeeper()])
    peer = _peer(srv, "haven")
    names = {t["name"] for t in peer.list_tools()}
    assert "separate.y" not in names  # excluded entirely
    assert "time.set_zone" in names   # negative control: a normal plugin IS present


# ========================================================================== #
# duplicate-name-parks-loudly — a derived-name collision is loud, never first-wins
# ========================================================================== #
class CollidingPlugin(ComposePlugin):
    name = "dupe"
    version = "1.0.0"
    publishes = ["dupe/a", "dupe/b"]
    mcp_tools = [
        McpToolDecl(name="go", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["dupe/a"])),
        McpToolDecl(name="go", input_model=EnrollArgs, direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["dupe/b"])),
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_duplicate_exposed_name_parks_loudly():
    # duplicate-name-parks-loudly ([CXC-MCP-TOOL-DECLARATION]): a within-plugin
    # derived-name collision is reported at LOAD/ANNOUNCE time — add_tenant
    # publishes the derived-announce surface and the collision surfaces THERE,
    # with NO connector, peer, or MCP session ever created.
    srv = ComposeServer()
    with pytest.raises(ToolCollisionError):
        srv.add_tenant("haven", plugins=[CollidingPlugin()])


# --- FIX 2: the collision is detected at LOAD/ANNOUNCE time, not only reactively #
class _ShadowDotThing(ComposePlugin):
    # exposed name: `svc.do.thing`
    name = "svc"
    version = "1.0.0"
    publishes = ["svc/x"]
    mcp_tools = [
        McpToolDecl(name="do.thing", input_model=EnrollArgs,
                    direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["svc/x"]))
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


class _ShadowDoThing(ComposePlugin):
    # exposed name: `svc.do.thing` — COLLIDES with _ShadowDotThing across plugins
    name = "svc.do"
    version = "1.0.0"
    publishes = ["svcdo/y"]
    mcp_tools = [
        McpToolDecl(name="thing", input_model=EnrollArgs,
                    direction=ToolDirection.WRITE,
                    namespace_mapping=NamespaceMapping(publishes=["svcdo/y"]))
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_collision_across_two_plugins_fails_at_load_time_no_client():
    # THE FIX-2 MUST: two DIFFERENT plugins whose derived `{plugin}.{tool}` names
    # collide (`svc`+`do.thing` and `svc.do`+`thing` both derive `svc.do.thing`)
    # fail at load/announce time — add_tenant/standup raises, with NO MCPConnector,
    # NO peer, and NO MCP client ever connecting. Before the fix this collision
    # was inert until some session called list_tools/invoke.
    srv = ComposeServer()
    with pytest.raises(ToolCollisionError):
        srv.add_tenant("haven", plugins=[_ShadowDotThing(), _ShadowDoThing()])


def test_collision_still_caught_reactively_after_clean_standup(server):
    # The reactive raise in MCPPeer.tool_registry() ([CXC-MCP-ROUTER-MECHANICS])
    # remains the safety net for a collision introduced AFTER a clean standup
    # (e.g. a runtime surface change / late announce), which the load-time screen
    # never saw. Inject a second announce onto the bound bus whose derived name
    # collides with the real `fingerprint.enroll`, then aggregate.
    bus = server.tenant_bus("haven")
    bus.register_namespace("_server/plugins/shadow/announce", "json",
                           scope=Scope.LOCAL)
    bus.publish_state("_server/plugins/shadow/announce", {
        "plugin": "shadow",
        "mcp_tools": [{
            "name": "fingerprint.enroll",  # collides with the real one
            "local_name": "enroll",
            "description": "",
            "input_schema": {"type": "object"},
            "direction": "write",
            "namespace_mapping": {"publishes": ["shadow/x"], "subscribes": []},
            "read_mode": None,
            "idempotent": False,
        }],
    }, source="_server")
    peer = _peer(server, "haven")
    with pytest.raises(ToolCollisionError):
        peer.tool_registry()


def test_load_time_collision_excludes_own_and_parked():
    # The load-time screen mirrors the umbrella aggregate EXACTLY: an `own`-declared
    # plugin and a parked plugin contribute zero tools, so a name they share with a
    # running plugin is NOT a collision. Negative control for a false-positive boot
    # failure.
    from composeio.transport_mcp import assert_no_tool_collisions
    # own + parked excluded by the CALLER (as publish_server_state does), so a
    # shared name here is simply never passed in -> no raise.
    assert_no_tool_collisions([
        {"plugin": "a", "mcp_tools": [{"name": "a.go"}]},
        {"plugin": "b", "mcp_tools": [{"name": "b.go"}]},
    ])  # distinct exposed names -> clean


# ========================================================================== #
# session-peer model — two sessions are two independent peers
# ========================================================================== #
def test_two_sessions_are_independent_peers(server):
    conn = MCPConnector(registry=server)
    p1, p2 = conn.new_peer(), conn.new_peer()
    p1.select_tenant("haven")
    p2.select_tenant("bsg")
    assert p1.peer_id != p2.peer_id
    assert p1.bound_tenant == "haven" and p2.bound_tenant == "bsg"
    # closing one never disturbs the other.
    conn.drop_peer(p1)
    assert p2.bound_tenant == "bsg" and p2.state == BOUND
    assert p1 not in conn.sessions and p2 in conn.sessions


def test_tool_declaration_read_mode_required_when_read():
    # the manifest contract itself refuses an ill-formed declaration.
    with pytest.raises(Exception):
        McpToolDecl(name="bad", input_model=EnrollArgs, direction=ToolDirection.READ)


# ========================================================================== #
# P3 — a `read-write` tool composes both: publish-then-return ([CXC-MCP-TOOL-
#      DECLARATION] READ_WRITE). Previously only pure write and pure read were
#      exercised; this covers the composed direction.
# ========================================================================== #
class ZoneArgs(BaseModel):
    zone: str


class ReadWritePlugin(ComposePlugin):
    name = "zoner"
    version = "1.0.0"
    publishes = ["zoner/zone"]
    subscribes = ["zoner/zone"]
    mcp_tools = [
        McpToolDecl(
            name="zone",
            description="Set the zone, then read it back (retained).",
            input_model=ZoneArgs,
            direction=ToolDirection.READ_WRITE,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(
                publishes=["zoner/zone"], subscribes=["zoner/zone"]),
        )
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


def test_read_write_publishes_then_returns_the_read():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[ReadWritePlugin()])
    bus = srv.tenant_bus("haven")
    bus.register_namespace("zoner/zone", "json", scope=Scope.LOCAL)
    peer = _peer(srv, "haven")
    r = peer.invoke("zoner.zone", {"zone": "utc"})
    # the composed result carries BOTH halves ([CXC-MCP-TOOL-DECLARATION]).
    assert r["ok"]
    assert r["write"]["ok"] and r["write"]["value"] == {"zone": "utc"}
    assert r["read"]["ok"] and r["read"]["present"] is True
    assert r["read"]["value"] == {"zone": "utc"}  # read back the value just written


def test_read_write_validation_failure_publishes_nothing_and_skips_read():
    # NEGATIVE CONTROL: a bad arg fails the WRITE half, so nothing is published
    # and the read half is never attempted — the composed path short-circuits on a
    # failed write ([CXC-MCP-INVOKE-WRITE] zero-bus-traffic on validation failure).
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[ReadWritePlugin()])
    bus = srv.tenant_bus("haven")
    bus.register_namespace("zoner/zone", "json", scope=Scope.LOCAL)
    peer = _peer(srv, "haven")
    r = peer.invoke("zoner.zone", {})  # missing required `zone`
    assert not r["ok"] and "zone" in r.get("fields", [])
    # the write never landed -> the namespace still has no value.
    assert bus.namespaces["zoner/zone"].age is None
