"""print_services ComposeServer-plugin acceptance — pure-logic, network-free.

Exercises the ADDITIVE plugin wrapper (`services/print_services/plugin.py`)
through a real `ComposeServer` tenant + a real `MCPConnector` peer, with NO
printer hardware and NO broker: every backend that would touch the network is a
fast in-memory fake, so the whole submit -> result -> record -> query path is
asserted deterministically.

Every check carries its NEGATIVE CONTROL ([CXC-VISION-EPISTEMICS]): an
instrument that can only report success is not an instrument. The load-bearing
gates here are (a) the MCP submit tool actually reaches the named backend and
NOTHING reaches an unknown one, and (b) a job-status query answers found=False
for an id it never saw rather than fabricating a job.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

# The print_services package lives under <repo>/services (as `python -m
# print_services` requires services/ on PYTHONPATH). conftest already seats
# <repo>/python; add its sibling so `print_services.plugin` imports.
_SERVICES = Path(__file__).resolve().parents[2] / "services"
if str(_SERVICES) not in sys.path:
    sys.path.insert(0, str(_SERVICES))

from composeio.composeserver import ComposeServer
from composeio.composeserver_loader import load_tenant_manifest
from composeio.transport_mcp import MCPConnector

from print_services.plugin import (  # noqa: E402
    EXAMPLE_MANIFEST_ENTRY,
    GetPrintJobStatusByIdArgs,
    PrintServicesPlugin,
    SubmitPrintJobToNamedBackendArgs,
)
from print_services.backends import HpBackend, Elegoo3dBackend  # noqa: E402


# --------------------------------------------------------------------------- #
# A network-free backend double honoring the real Backend contract (id,
# descriptor, status(), submit(job, page_cap)).
# --------------------------------------------------------------------------- #
class _FakeBackend:
    def __init__(self, bid: str, pages: int = 1, state: str = "ready"):
        self.id = bid
        self._pages = pages
        self._state = state
        self.submitted: list[dict] = []
        self.descriptor = {
            "id": bid, "kind": "test", "content_types": ["text"],
            "status_ns": f"print_services/{bid}/status", "backend": "fake",
        }

    def status(self) -> dict:
        return {"state": self._state, "ts": time.time()}

    def submit(self, job: dict, page_cap=None) -> dict:
        job_id = job.get("job_id") or f"{self.id}-fake"
        total = self._pages
        if page_cap is not None:
            ok, err = page_cap.try_consume(total)
            if not ok:
                return {"job_id": job_id, "status": "error",
                        "detail": err, "pages": total}
        self.submitted.append(job)
        return {"job_id": job_id, "status": "done", "pages": total,
                "detail": "fake print"}


def _peer(server, tenant):
    conn = MCPConnector(registry=server)
    peer = conn.new_peer()
    peer.select_tenant(tenant)
    return peer


def _tenant_with(server, tenant, backends, **cfg):
    cfg.setdefault("heartbeat_seconds", 3600)  # never tick during a test
    plugin = PrintServicesPlugin(config=cfg, backends=backends)
    server.add_tenant(tenant, plugins=[plugin], start=True)
    return plugin


# ========================================================================== #
# Surface — the MCP tools are declared, named for an Intelligence, schema-derived
# ========================================================================== #
def test_declares_intelligence_named_mcp_surface():
    ann = PrintServicesPlugin().announce()
    local = {t["local_name"] for t in ann.mcp_tools}
    assert local == {
        "submit_print_job_to_named_backend",
        "list_available_print_backends",
        "get_print_job_status_by_id",
    }
    exposed = {t["name"] for t in ann.mcp_tools}
    assert "print_services.submit_print_job_to_named_backend" in exposed
    # schema is DERIVED from input_model, never hand-written.
    sub = next(t for t in ann.mcp_tools
               if t["local_name"] == "submit_print_job_to_named_backend")
    assert sub["input_schema"] == SubmitPrintJobToNamedBackendArgs.model_json_schema()
    # NEGATIVE CONTROL: the terse human-oriented name is NOT what is exposed —
    # the surface is verbose-for-an-Intelligence by construction.
    assert "submit" not in local and "list" not in local and "status" not in local


def test_default_factory_builds_hp_and_elegoo_backends():
    # construction only — no start(), no network.
    p = PrintServicesPlugin(config={"hp_ipp_ip": "198.51.100.10",
                                     "elegoo_sdcp_ip": "198.51.100.11"})
    backends = p._build_backends()
    assert set(backends) == {"hp", "elegoo3d"}
    assert isinstance(backends["hp"], HpBackend) and backends["hp"].ip == "198.51.100.10"
    assert isinstance(backends["elegoo3d"], Elegoo3dBackend)
    assert backends["elegoo3d"].ip == "198.51.100.11"
    # NEGATIVE CONTROL: disabling a backend drops it from the built set.
    p2 = PrintServicesPlugin(config={"enable_elegoo_sdcp_backend": False})
    assert set(p2._build_backends()) == {"hp"}


# ========================================================================== #
# Discovery + load via the config-manifest loader
# ========================================================================== #
def test_discovered_and_loaded_via_manifest():
    # Resolve the dotted ref through the real loader. Backends disabled so
    # start() (which standup runs even at start=False) probes NO hardware.
    entry = {"plugin": EXAMPLE_MANIFEST_ENTRY["plugin"],
             "config": {"enable_hp_ipp_backend": False,
                        "enable_elegoo_sdcp_backend": False}}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "haven", {"plugins": [entry]})
    assert res.is_clean(), res.config_parked
    assert "print_services" in [p.name for p in srv.tenant_plugin_specs("haven")]


def test_manifest_bad_config_parks_with_named_taxonomy():
    # NEGATIVE CONTROL for the loader: an invalid config field parks LOUDLY with
    # the config:<field> taxonomy — it never silently instantiates.
    bad = {"plugins": [{
        "plugin": EXAMPLE_MANIFEST_ENTRY["plugin"],
        "config": {"page_cap_per_period": "not-an-int",
                   "enable_hp_ipp_backend": False,
                   "enable_elegoo_sdcp_backend": False},
    }]}
    srv = ComposeServer()
    res = load_tenant_manifest(srv, "haven", bad)
    assert not res.is_clean()
    parks = [r for rs in res.config_parked.values() for r in rs]
    assert any("page_cap_per_period" in r for r in parks), parks


# ========================================================================== #
# THE LOAD-BEARING GATE: the MCP submit tool reaches the named backend, and
# an unknown backend reaches NOTHING.
# ========================================================================== #
def test_mcp_submit_round_trips_to_named_backend():
    srv = ComposeServer()
    fakes = {"hp": _FakeBackend("hp")}
    _tenant_with(srv, "haven", fakes)
    bus = srv.tenant_bus("haven")
    results: list[dict] = []
    bus.subscribe_events("print_services/hp/result",
                         lambda n, e, d: results.append(d))

    peer = _peer(srv, "haven")
    r = peer.invoke("print_services.submit_print_job_to_named_backend",
                    {"backend_name": "hp", "content_type": "text",
                     "content": "hello", "job_id": "j1"})
    # the WRITE landed on the aggregate submit namespace.
    assert r["ok"] and r["published"] == ["print_services/submit"]
    # the named backend actually received the job (synchronous bus delivery).
    assert fakes["hp"].submitted and fakes["hp"].submitted[0]["job_id"] == "j1"
    # a terminal 'done' result was published on the per-backend result stream.
    assert any(d.get("status") == "done" and d.get("job_id") == "j1"
               for d in results)

    # NEGATIVE CONTROL: an unknown backend name reaches NO real backend.
    before = len(fakes["hp"].submitted)
    nope_results: list[dict] = []
    bus.subscribe_events("print_services/nope/result",
                         lambda n, e, d: nope_results.append(d))
    r2 = peer.invoke("print_services.submit_print_job_to_named_backend",
                     {"backend_name": "nope", "content": "x", "job_id": "j2"})
    assert r2["ok"]  # the write ack still succeeds; routing failure is a result
    assert len(fakes["hp"].submitted) == before  # hp never saw the misroute
    assert any(d.get("status") == "error" for d in nope_results)


# ========================================================================== #
# list_available_print_backends — reads the retained aggregate registry
# ========================================================================== #
def test_list_available_print_backends_reflects_hosted_backends():
    srv = ComposeServer()
    _tenant_with(srv, "haven", {"hp": _FakeBackend("hp"),
                                "elegoo3d": _FakeBackend("elegoo3d")})
    peer = _peer(srv, "haven")
    r = peer.invoke("print_services.list_available_print_backends", {})
    assert r["ok"] and r["present"] is True
    assert set(r["value"]) == {"hp", "elegoo3d"}
    assert r["value"]["hp"]["id"] == "hp"

    # NEGATIVE CONTROL: the list reflects ACTUAL hosted backends, not a
    # fabricated full roster — a tenant hosting only hp never reports elegoo3d.
    srv.add_tenant("solo", plugins=[
        PrintServicesPlugin(config={"heartbeat_seconds": 3600},
                            backends={"hp": _FakeBackend("hp")})], start=True)
    r2 = _peer(srv, "solo").invoke(
        "print_services.list_available_print_backends", {})
    assert set(r2["value"]) == {"hp"} and "elegoo3d" not in r2["value"]


# ========================================================================== #
# get_print_job_status_by_id — CXC-BUS-QUERY round-trip
# ========================================================================== #
def test_get_print_job_status_by_id_query_round_trip():
    srv = ComposeServer()
    _tenant_with(srv, "haven", {"hp": _FakeBackend("hp")})
    peer = _peer(srv, "haven")
    peer.invoke("print_services.submit_print_job_to_named_backend",
                {"backend_name": "hp", "content": "hi", "job_id": "jX"})

    r = peer.invoke("print_services.get_print_job_status_by_id",
                    {"job_id": "jX"}, timeout=1.0)
    assert r["ok"], r
    assert r["result"]["found"] is True
    assert r["result"]["job"]["job_id"] == "jX"
    assert r["result"]["job"]["status"] == "done"

    # NEGATIVE CONTROL: an id never submitted answers found=False, never a
    # fabricated job.
    r2 = peer.invoke("print_services.get_print_job_status_by_id",
                     {"job_id": "never"}, timeout=1.0)
    assert r2["ok"] and r2["result"]["found"] is False
    assert "job" not in r2["result"]


# ========================================================================== #
# The page cap is preserved through the plugin path
# ========================================================================== #
def test_page_cap_refuses_over_cap_but_prints_freely_under_it():
    srv = ComposeServer()
    fakes = {"hp": _FakeBackend("hp", pages=3)}
    _tenant_with(srv, "haven", fakes, page_cap_per_period=5)
    bus = srv.tenant_bus("haven")
    results: list[dict] = []
    bus.subscribe_events("print_services/hp/result",
                         lambda n, e, d: results.append(d))
    peer = _peer(srv, "haven")

    # first 3-page job is under the 5-page cap -> prints freely.
    peer.invoke("print_services.submit_print_job_to_named_backend",
                {"backend_name": "hp", "content": "a", "job_id": "p1"})
    # second 3-page job would make 6 > 5 -> refused honestly with a cap message.
    peer.invoke("print_services.submit_print_job_to_named_backend",
                {"backend_name": "hp", "content": "b", "job_id": "p2"})

    done = [d for d in results if d.get("status") == "done"]
    errs = [d for d in results if d.get("status") == "error"]
    assert any(d.get("job_id") == "p1" for d in done)
    assert any(d.get("job_id") == "p2" and "cap" in d.get("detail", "")
               for d in errs)
    # only the first job actually reached the backend.
    assert [j["job_id"] for j in fakes["hp"].submitted] == ["p1"]


# ========================================================================== #
# stop() is graceful — tombstones + no exception
# ========================================================================== #
def test_stop_tombstones_registry_and_is_clean():
    srv = ComposeServer()
    plugin = _tenant_with(srv, "haven", {"hp": _FakeBackend("hp")})
    bus = srv.tenant_bus("haven")
    # aggregate registry is present after start.
    assert bus.get_state("print_services/registry") is not None
    plugin.stop()
    # graceful shutdown tombstones the aggregate registry to explicit None.
    assert bus.get_state("print_services/registry") is None
