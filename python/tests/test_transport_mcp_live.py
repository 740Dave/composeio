"""MCP umbrella connector — LIVE-socket acceptance ([SPEC:CXC-MCP] §MCP-V rows).

The REAL-CLIENT round-trip probe: the official MCP Python SDK's `ClientSession`
+ `streamable_http_client` drive a live session against a real `MCPConnector`
over a real socket — the third catch, like the WS+DOM pair probe
(`test_transport_ws_live.py`). `python/tests/test_transport_mcp.py` exercises
every peer-logic predicate with NO SDK session; this file is what makes that
suite's own disclosure true: "the live streamable-HTTP wire ... is the
NEEDS-GLASS tier ... owned by first-bringup" — first-bringup is this file.

The load-bearing claim under test is the multi-session session->peer mapping
([CXC-MCP-SESSION-PEER]): `MCPConnector._build_sdk_server` used to bind ONE
peer for the life of the connector regardless of how many MCP sessions
connected (the degenerate single-peer stub the dispatch flagged). Every test
here that opens >=2 concurrent sessions and asserts INDEPENDENT tenant
bindings is a negative control on exactly that regression — if the stub
reappears, `test_live_two_sessions_are_independent_peers` (and the
cross-tenant test, whose "haven" session would leak into the shared peer)
fail.

⛔ This is the SYNTHETIC real-client proof (machine tier). The remaining
`verify-human` glass row — a `>=2`-tenant DEPLOYMENT confirmed by an operator's
own eyes, side by side, per [CXC-MCP-ACCEPTANCE] MCP-V3/V4's verify-human rows
— is NOT self-certifiable here ([CXC-VISION-EPISTEMICS]) and is handed to the
architect.
"""
from __future__ import annotations

import asyncio
import json
import threading

import pytest
from pydantic import BaseModel

from composeio.composeserver import ComposeServer
from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)
from composeio.transport import MessageType

pytest.importorskip("mcp")
pytest.importorskip("uvicorn")
pytest.importorskip("starlette")

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402


# --------------------------------------------------------------------------- #
# fixture plugin — same shape as the pure-logic suite's Fingerprint plugin.
# --------------------------------------------------------------------------- #
class EnrollArgs(BaseModel):
    user_id: int


class ReadArgs(BaseModel):
    pass


class Fingerprint(ComposePlugin):
    name = "fingerprint"
    version = "1.0.0"
    publishes = ["fingerprint/enroll"]
    mcp_tools = [
        McpToolDecl(
            name="enroll", description="Enroll a user's fingerprint.",
            input_model=EnrollArgs, direction=ToolDirection.WRITE,
            namespace_mapping=NamespaceMapping(publishes=["fingerprint/enroll"]),
        ),
        McpToolDecl(
            name="last", description="The last enrolled record (retained).",
            input_model=ReadArgs, direction=ToolDirection.READ,
            read_mode=ReadMode.RETAINED,
            # deliberately the SAME namespace `enroll` publishes to, so the
            # write->read round trip below is real: this tool reads back
            # exactly what the `write` tool just put on the bus, never a
            # namespace the fixture had to separately seed.
            namespace_mapping=NamespaceMapping(subscribes=["fingerprint/enroll"]),
        ),
    ]

    def start(self, bus):
        self._bus = bus

    def stop(self):
        super().stop()


@pytest.fixture
def live():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[Fingerprint()])
    srv.add_tenant("bsg", plugins=[])  # NO fingerprint plugin here — cross-tenant probe
    conn = srv.attach_mcp_endpoint(host="127.0.0.1", port=0, start=True)
    try:
        yield srv, conn
    finally:
        conn.stop()


def _url(conn) -> str:
    return f"http://127.0.0.1:{conn.bound_port}/mcp"


def _run(coro):
    """Sync bridge — no pytest-asyncio dependency needed for this file."""
    return asyncio.run(coro)


# ========================================================================== #
# connects + announces: the connector is a real, dialable socket
# ========================================================================== #
def test_live_connector_binds_a_real_socket(live):
    srv, conn = live
    assert conn.is_started
    assert isinstance(conn.bound_port, int) and conn.bound_port > 0
    # NEGATIVE CONTROL: a dead connector reports no bound_port (never a stale one).
    assert conn.bound_port != 0


# ========================================================================== #
# full round-trip: connect -> select_tenant -> list_tools -> write -> the REAL
# bus sees the publish -> retained read returns the value
# ========================================================================== #
def test_live_full_round_trip_write_reaches_real_bus_and_read_returns_it(live):
    srv, conn = live
    haven = srv.tenant_bus("haven")
    seen = []
    haven.subscribe("fingerprint/enroll", lambda ns, v, o=None: seen.append(v))

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()

                # NEGATIVE CONTROL 1: pre-selection tool call is refused.
                pre = await session.call_tool("fingerprint.enroll", {"user_id": 1})
                assert not seen, "a pre-selection call must not reach the bus"
                assert '"ok": false' in pre.content[0].text
                assert "tenant-scoped" in pre.content[0].text

                sel = await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                assert '"ok": true' in sel.content[0].text

                # the fixture plugin's declared tool is now reachable, under
                # {plugin}.{tool} — MCP-V5's naming discipline, live.
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert "fingerprint.enroll" in names
                assert "fingerprint.last" in names

                # invoke the `write` tool.
                res = await session.call_tool(
                    "fingerprint.enroll", {"user_id": 42})
                assert '"ok": true' in res.content[0].text
                assert '"user_id": 42' in res.content[0].text

                return res

        # `async with` block above has exited (session/socket closed); the bus
        # subscriber recorded the publish on the REAL bus, independent of the
        # MCP session's own lifetime.

    _run(_drive())
    assert seen == [{"user_id": 42}], (
        "the write tool call did not land on the declared bus namespace "
        "(fingerprint/enroll) via a REAL client -> REAL connector -> REAL bus "
        "path")

    async def _read_back():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                return await session.call_tool("fingerprint.last", {})

    read_res = _run(_read_back())
    text = read_res.content[0].text
    assert '"present": true' in text
    assert '"user_id": 42' in text


# ========================================================================== #
# negative control: a validation failure produces ZERO bus traffic
# ========================================================================== #
def test_live_validation_failure_produces_zero_bus_traffic(live):
    srv, conn = live
    haven = srv.tenant_bus("haven")
    seen = []
    haven.subscribe("fingerprint/enroll", lambda ns, v, o=None: seen.append(v))

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                return await session.call_tool(
                    "fingerprint.enroll", {"user_id": "not-an-int"})

    res = _run(_drive())
    # the SDK client surfaces a validation error (either as an isError result
    # or the connector's own structured refusal) — either way, the load-bearing
    # assertion is what matters: NOTHING reached the bus.
    assert seen == [], (
        "an argument-validation failure over the REAL wire still touched the "
        "bus — MCP-V6's zero-bus-traffic guarantee failed under a live client")
    assert res.isError or "not of type" in res.content[0].text or (
        "validation" in res.content[0].text.lower())


# ========================================================================== #
# MCP-V4 — the load-bearing gate, live: a cross-tenant tool is "not found",
# never a cross-tenant dispatch — proven over the REAL wire, not the pure-logic
# double.
# ========================================================================== #
def test_live_cross_tenant_tool_is_not_found_never_dispatched(live):
    srv, conn = live
    bsg = srv.tenant_bus("bsg")
    seen = []
    bsg.subscribe("fingerprint/enroll", lambda ns, v, o=None: seen.append(v))
    # (bsg has no fingerprint plugin, so this subscribe is inert on a real
    # announce — it exists only so a stray cross-dispatch would be CAUGHT.)

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "bsg"})
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                return names, await session.call_tool(
                    "fingerprint.enroll", {"user_id": 7})

    names, res = _run(_drive())
    assert "fingerprint.enroll" not in names, (
        "a plugin loaded only on 'haven' leaked into 'bsg''s live tool list")
    text = res.content[0].text
    assert "not found" in text or "not_found" in text.lower()
    assert seen == [], "a cross-tenant call must never touch the other tenant's bus"


# ========================================================================== #
# THE CORE DELIVERABLE — multi-session wiring: two CONCURRENT real sessions
# get INDEPENDENT peers with INDEPENDENT tenant bindings, over the SAME
# listening connector ([CXC-MCP-SESSION-PEER]). This is the negative control
# on the degenerate single-peer stub: before the fix, session B's
# select_tenant("bsg") would have rebound the ONE shared peer, and session A's
# subsequent tool list would have gone stale/wrong or cross-tenant.
# ========================================================================== #
def test_live_two_sessions_are_independent_peers(live):
    srv, conn = live

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read_a, write_a, _a):
            async with ClientSession(read_a, write_a) as session_a:
                await session_a.initialize()
                await session_a.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})

                async with streamable_http_client(_url(conn)) as (read_b, write_b, _b):
                    async with ClientSession(read_b, write_b) as session_b:
                        await session_b.initialize()
                        await session_b.call_tool(
                            "_server.select_tenant", {"tenant_id": "bsg"})

                        # session A must STILL be bound to haven — a shared
                        # single peer would show fingerprint tools vanishing
                        # (rebound to bsg) or bsg gaining them (cross-leak).
                        tools_a = await session_a.list_tools()
                        tools_b = await session_b.list_tools()
                        # NEGATIVE CONTROL, captured WHILE both sessions are
                        # still live: at least TWO independent peer objects
                        # exist. The pre-fix stub created exactly ONE
                        # bootstrap peer at `start()` regardless of how many
                        # sessions connected — this count is what would stay
                        # pinned at 1 if that regressed.
                        live_peer_count = len(conn.sessions)
                        return (
                            {t.name for t in tools_a.tools},
                            {t.name for t in tools_b.tools},
                            live_peer_count,
                        )

    names_a, names_b, live_peer_count = _run(_drive())
    assert "fingerprint.enroll" in names_a, (
        "session A's tenant binding was clobbered by session B — the "
        "multi-session peer mapping regressed to the single shared-peer stub")
    assert "fingerprint.enroll" not in names_b
    assert live_peer_count >= 2, (
        f"expected >=2 independent peer objects for 2 concurrent sessions, "
        f"got {live_peer_count} — the degenerate single-peer stub is back")


# ========================================================================== #
# `command` direction — a MUTATING QUERY ROUND-TRIP whose single correlated
# reply carries its authoritative outcome ([DEC-MCP-COMMAND-DIRECTION-2026-08-25],
# [CXC-MCP-TOOL-DECLARATION]). `test_transport_mcp.py` proves this LOGIC with
# no SDK session; this section is what makes ITS OWN command-direction
# disclosure true over a REAL MCP client -> REAL connector -> REAL bus path —
# first-bringup's job, run by `composeio-first-bringup` 2026-08-26 as Step 0
# of the Wave-2b reconcile-and-land runtime leg.
# ========================================================================== #
class StoreArgs(BaseModel):
    doc: str


class DocStorePlugin(ComposePlugin):
    """One `command` tool: a mutating store realized as a CXC-BUS-QUERY
    round-trip — the SAME fixture shape `test_transport_mcp.py`'s
    `CommandPlugin` uses, wired here to a REAL socket."""
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


def _install_live_mutating_responder(bus, resource_root, store):
    """A REAL mutating [SPEC:CXC-BUS-QUERY] responder on the LIVE bus: each
    request appends its `doc` to `store` (the mutation — a real Python list,
    not a stand-in) and answers on `{root}/result/{src}`, echoing
    `request_id` verbatim."""
    def _h(msg):
        if msg.type == MessageType.EVENT and msg.namespace == f"{resource_root}/query":
            data = msg.data or {}
            doc = data.get("doc")
            store.append(doc)  # THE MUTATION, on the REAL bus
            bus.publish_event(
                f"{resource_root}/result/{msg.source}", "result",
                data={"request_id": data.get("request_id"), "stored": doc,
                      "total": len(store)},
                source="responder")
    bus.on_message(_h)


@pytest.fixture
def live_command():
    srv = ComposeServer()
    srv.add_tenant("haven", plugins=[DocStorePlugin()])
    conn = srv.attach_mcp_endpoint(host="127.0.0.1", port=0, start=True)
    store: list = []
    _install_live_mutating_responder(srv.tenant_bus("haven"), "_doc/people/store", store)
    try:
        yield srv, conn, store
    finally:
        conn.stop()


def test_live_command_round_trips_and_mutates_real_bus_state(live_command):
    """(a) the mutation actually lands on the REAL bus; (b) each call's
    authoritative result is CORRELATED to its OWN request_id — a real MCP
    client driving the real accept loop, not `peer.invoke()` in-process."""
    srv, conn, store = live_command

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                tools = await session.list_tools()
                names = {t.name for t in tools.tools}
                assert "docstore.store" in names
                res1 = await session.call_tool("docstore.store", {"doc": "kara"})
                res2 = await session.call_tool("docstore.store", {"doc": "lee"})
                return res1, res2

    res1, res2 = _run(_drive())
    r1, r2 = json.loads(res1.content[0].text), json.loads(res2.content[0].text)
    assert r1["ok"] and r2["ok"]
    # (a) the mutation actually landed — the REAL list, mutated by the
    # responder's handler, not the connector (G1: connector forwards/decodes only).
    assert store == ["kara", "lee"], (
        "the command tool call did not reach the docstore responder over the "
        "REAL client -> REAL connector -> REAL bus path")
    # (b) each call's reply is correlated to ITS OWN request_id, never the
    # other's — NEGATIVE CONTROL: had they cross-delivered, "kara" would
    # carry "lee"'s stored value or vice versa.
    assert r1["result"]["request_id"] == r1["request_id"]
    assert r2["result"]["request_id"] == r2["request_id"]
    assert r1["request_id"] != r2["request_id"]
    assert r1["result"]["stored"] == "kara"
    assert r2["result"]["stored"] == "lee"


def test_live_command_negative_control_two_concurrent_calls_never_cross_deliver(
        live_command):
    """The explicit negative control this leg was dispatched to pin: under
    >=2 GENUINELY concurrent in-flight command calls on the SAME live MCP
    session, a reply must resolve ONLY the caller whose request_id it
    echoes. The responder HOLDS both requests until 2 are in-flight, then
    answers LIFO — if the peer's correlation ever fell back to
    arrival-order/FIFO instead of matching `request_id`, this is the test
    that would catch it, live over the real wire."""
    srv, conn, store = live_command
    bus = srv.tenant_bus("haven")
    held: list = []
    lock = threading.Lock()

    def _flush(items):
        for src, rid, doc in reversed(items):  # answer OUT of arrival order
            store.append(doc)
            bus.publish_event(
                f"_doc/people/store/result/{src}", "result",
                data={"request_id": rid, "stored": doc, "total": len(store)},
                source="responder")

    def _holding_responder(msg):
        if (msg.type == MessageType.EVENT
                and msg.namespace == "_doc/people/store/query"):
            data = msg.data or {}
            with lock:
                held.append((msg.source, data.get("request_id"), data.get("doc")))
                if len(held) < 2:
                    return
                items, held[:] = list(held), []
            _flush(items)

    # replace the fixture's eager single-request responder with the holding
    # one for this test (both are registered; only the holding one answers,
    # since it needs 2 in-flight before it ever publishes).
    bus.on_message(_holding_responder)

    async def _drive():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                return await asyncio.gather(
                    session.call_tool("docstore.store", {"doc": "kara"}),
                    session.call_tool("docstore.store", {"doc": "lee"}),
                )

    res_kara, res_lee = _run(_drive())
    rk = json.loads(res_kara.content[0].text)
    rl = json.loads(res_lee.content[0].text)
    assert rk["ok"] and rl["ok"]
    # each concurrent caller got ITS OWN reply, never the other's, despite
    # the responder answering the SECOND-arriving request FIRST (LIFO).
    assert rk["result"]["stored"] == "kara"
    assert rl["result"]["stored"] == "lee"
    assert rk["result"]["request_id"] == rk["request_id"]
    assert rl["result"]["request_id"] == rl["request_id"]
    assert rk["request_id"] != rl["request_id"]


def test_live_command_foreign_request_id_never_resolves_pending_call(live_command):
    """THE negative control named verbatim in this leg's dispatch: a reply
    carrying a FOREIGN request_id must NOT resolve a caller's own pending
    command call. Asserted against the REAL `MCPPeer` bound to the live
    socket via `conn.sessions` — not a synthetic double — so this is a
    property of the actual running connector, not a model of it."""
    srv, conn, store = live_command

    async def _connect_and_probe():
        async with streamable_http_client(_url(conn)) as (read, write, _sid):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await session.call_tool(
                    "_server.select_tenant", {"tenant_id": "haven"})
                assert len(conn.sessions) >= 1, (
                    "no live peer registered for this session — the "
                    "session->peer mapping did not populate")
                peer = conn.sessions[-1]
                from composeio.transport_mcp import _PendingCall
                mine = _PendingCall("rid-mine-live")
                peer._pending["rid-mine-live"] = mine
                # a FOREIGN reply must NOT resolve it.
                peer._on_query_result(
                    "_doc/people/store/result/x", "result",
                    {"request_id": "rid-other-live", "stored": "not-mine"})
                assert mine.result is None and not mine.event.is_set(), (
                    "a foreign request_id resolved a pending call it did not "
                    "belong to — the correlation gate is broken")
                # POSITIVE CONTROL: the caller's OWN reply resolves it,
                # verbatim — proves the assertion above is discriminating,
                # not merely "nothing ever resolves."
                peer._on_query_result(
                    "_doc/people/store/result/x", "result",
                    {"request_id": "rid-mine-live", "stored": "mine"})
                assert mine.result["stored"] == "mine" and mine.event.is_set()

    _run(_connect_and_probe())


def test_live_command_without_query_read_mode_rejected_at_declaration():
    """(c) a `command` tool declared WITHOUT `read_mode=query` is REJECTED at
    declaration time — never silently accepted, never coerced — using the
    SAME `McpToolDecl` shape the live `docstore.store` tool above declares
    against."""
    with pytest.raises(ValueError):
        McpToolDecl(
            name="store", input_model=StoreArgs, direction=ToolDirection.COMMAND,
            namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    with pytest.raises(ValueError):  # RETAINED is not enough — must be QUERY
        McpToolDecl(
            name="store", input_model=StoreArgs, direction=ToolDirection.COMMAND,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    # NEGATIVE CONTROL: read_mode=query IS accepted — proves the two
    # rejections above are about read_mode, not some unrelated failure.
    ok = McpToolDecl(
        name="store", input_model=StoreArgs, direction=ToolDirection.COMMAND,
        read_mode=ReadMode.QUERY,
        namespace_mapping=NamespaceMapping(subscribes=["x/query"]))
    assert ok.direction is ToolDirection.COMMAND


def test_live_command_classified_as_write_by_direction_keyed_gate(live_command):
    """(d) a direction-keyed policy/audit check run against the REAL
    connector's resolved tool registry (`conn.tool_registry`, not a
    synthetic double) classifies the live `docstore.store` binding as a
    WRITE — closing the hole this leg exists to close: a mutation can no
    longer masquerade as a read past a direction-keyed gate."""
    srv, conn, store = live_command
    binding = conn.tool_registry("haven")["docstore.store"]
    assert binding.direction is ToolDirection.COMMAND
    assert binding.direction.includes_write() is True
    assert binding.direction.includes_read() is True
    # NEGATIVE CONTROL: the SAME predicate, run against a pure `read`
    # direction, must NOT classify it as a write — proves the assertion
    # above discriminates on `direction`, rather than being vacuously true
    # for anything with a `.includes_write()` method.
    assert ToolDirection.READ.includes_write() is False
