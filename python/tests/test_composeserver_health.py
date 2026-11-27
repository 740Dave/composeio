"""ComposeServer's host must make a DOWN connector visible to a HEALTHCHECK.

Before: a ComposeServer whose global MQTT connector never connected still
answered HTTP and looked healthy for days; nothing on the host could tell.
Now the host writes the connectors' state (each transport's own `is_connected`,
plus its `health()` detail when it has one) to a health file, and
`python -m composeio.composeserver_host --healthcheck` reads it and exits
non-zero when a connector is down, when the file is stale (a hung host) or when
it is missing (a host that never got as far as serving).

Every positive is paired with its negative control: the same check must FAIL
for a down connector, a stale file and a missing file.
"""
from __future__ import annotations

import json
import math
import os
import time

import pytest

from composeio.composeserver import ComposeServer
from composeio import composeserver_host as host


class FakeConnector:
    """Any transport: the host reads only `is_connected` (and `health()` if any)."""

    def __init__(self, connected: bool):
        self.is_connected = connected

    def start(self):
        pass

    def stop(self):
        pass

    def health(self):
        return {"connected": self.is_connected, "broker": "broker.invalid:1883"}


@pytest.fixture
def make_server(monkeypatch):
    """A server whose tenant DECLARES one connector (through the transport
    registry, as a manifest would), built as a FakeConnector."""
    from composeio import transport_registry
    built = []

    def factory(spec, app_id):
        built.append(FakeConnector(spec.get("connected", False)))
        return built[-1]

    monkeypatch.setitem(transport_registry._FACTORIES, "fakeconn", factory)

    def _make(connected: bool):
        srv = ComposeServer()
        srv.add_tenant("t", connectors=[{"type": "fakeconn",
                                         "connected": connected}])
        return srv, built[-1]
    return _make


def test_connector_health_reports_each_external_connector(make_server):
    srv, conn = make_server(connected=False)
    h = srv.connector_health()
    assert h["healthy"] is False
    [row] = h["connectors"]
    assert row["tenant"] == "t" and row["scope"] == "global"
    assert row["connected"] is False
    assert row["broker"] == "broker.invalid:1883"

    conn.is_connected = True
    assert srv.connector_health()["healthy"] is True


def test_a_server_with_no_external_connector_is_healthy():
    srv = ComposeServer()
    srv.add_tenant("t")
    h = srv.connector_health()
    assert h == {"healthy": True, "expected_connectors": 0, "connectors": [],
                 "extra_connectors": []}


def test_healthcheck_exit_codes(tmp_path, capsys, make_server):
    path = tmp_path / "health.json"
    srv, conn = make_server(connected=False)

    # missing file -> unhealthy (the host never started serving)
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1

    host.write_health_file(srv, path)
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
    assert "global connector down" in capsys.readouterr().out

    conn.is_connected = True
    host.write_health_file(srv, path)
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 0

    # stale file -> unhealthy even though it says healthy (a hung host)
    old = time.time() - 3600
    data = json.loads(path.read_text())
    data["written_at"] = old
    path.write_text(json.dumps(data))
    assert host.main(["--healthcheck", "--health-file", str(path),
                      "--health-max-age", "30"]) == 1


def test_healthcheck_reads_the_file_from_the_environment(tmp_path, monkeypatch,
                                                         make_server):
    path = tmp_path / "health.json"
    monkeypatch.setenv(host.HEALTH_FILE_ENV, str(path))
    srv, _ = make_server(connected=True)
    host.write_health_file(srv, path)
    assert host.main(["--healthcheck"]) == 0
    os.remove(path)
    assert host.main(["--healthcheck"]) == 1


def test_healthcheck_without_any_health_file_is_a_usage_error(monkeypatch):
    monkeypatch.delenv(host.HEALTH_FILE_ENV, raising=False)
    assert host.main(["--healthcheck"]) == 2


# --------------------------------------------------------------------------- #
# A1: health is judged against what the manifest DECLARES, not what attached
# --------------------------------------------------------------------------- #

def _manifest(tmp_path, connectors):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"tenant": "t", "connectors": connectors,
                             "plugins": []}))
    return p


def test_a_declared_connector_that_failed_to_be_created_is_unhealthy(
        tmp_path, monkeypatch, capsys):
    """The skipped-connector case: the registry returns None for a connector
    whose construction raised, standup logs a warning and carries on, and the
    host then has ZERO attached connectors. Judged on what attached, that is
    'healthy: 0 connector(s) up'. Judged on what was declared, it is down."""
    from composeio import transport_registry

    def boom(spec, app_id):
        raise RuntimeError("backend unavailable")

    monkeypatch.setitem(transport_registry._FACTORIES, "mqtt", boom)
    srv, _ = host.run(_manifest(tmp_path, [
        {"type": "mqtt", "broker": "127.0.0.1", "port": 1}]))
    try:
        assert srv.connector_health()["connectors"] == []   # it really was skipped
        path = tmp_path / "health.json"
        data = host.write_health_file(srv, path)
        assert data["expected_connectors"] == 1
        assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
        assert "expected 1" in capsys.readouterr().out
    finally:
        srv.stop()


def test_a_manifest_declaring_zero_connectors_is_healthy(tmp_path):
    srv, _ = host.run(_manifest(tmp_path, []))
    try:
        path = tmp_path / "health.json"
        data = host.write_health_file(srv, path)
        assert data["expected_connectors"] == 0
        assert host.main(["--healthcheck", "--health-file", str(path)]) == 0
    finally:
        srv.stop()


def test_a_local_scope_connector_is_not_expected_externally():
    srv = ComposeServer()
    srv.add_tenant("t", connectors=[])
    assert srv.connector_health()["expected_connectors"] == 0
    assert ComposeServer.declared_external(
        [{"type": "mqtt"}, {"type": "x", "scope": "local"},
         {"type": "y", "scope": "remote"}]) == 2


def test_healthcheck_fails_when_fewer_connectors_than_expected(tmp_path, capsys):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({
        "written_at": time.time(), "healthy": True, "expected_connectors": 2,
        "connectors": [{"tenant": "t", "scope": "global", "connected": True}]}))
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
    assert "expected 2" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# N1: a timestamp from the future is stale, not fresh
# --------------------------------------------------------------------------- #

def _good(**over):
    d = {"written_at": time.time(), "healthy": True, "expected_connectors": 1,
         "connectors": [{"tenant": "t", "scope": "global", "connected": True}]}
    d.update(over)
    return d


def test_a_future_timestamp_is_stale(tmp_path, capsys):
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_good(written_at=time.time() + 3600)))
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
    assert "future" in capsys.readouterr().out
    # control: inside the allowed skew is still fresh
    path.write_text(json.dumps(_good(written_at=time.time() + 1)))
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 0


# --------------------------------------------------------------------------- #
# N3: valid JSON of the wrong shape is an explicit exit 1, never a traceback
# --------------------------------------------------------------------------- #

def test_wrong_shape_health_files_exit_1_with_a_message(tmp_path):
    path = tmp_path / "health.json"
    for bad in ([], "x", 3, _good(written_at="abc"), _good(connectors={"a": 1}),
                _good(connectors=["not-a-dict"]), _good(expected_connectors="1")):
        path.write_text(json.dumps(bad))
        code, reason = host.check_health_file(path)       # must not raise
        assert code == 1, bad
        assert reason.startswith("unhealthy: malformed health file"), (bad, reason)


# --------------------------------------------------------------------------- #
# N3 (round 2): a non-finite timestamp says nothing about age
# --------------------------------------------------------------------------- #

def test_a_non_finite_timestamp_is_malformed(tmp_path):
    path = tmp_path / "health.json"
    for raw in ("NaN", "Infinity", "-Infinity"):
        d = _good()
        d["written_at"] = 0
        text = json.dumps(d).replace('"written_at": 0', f'"written_at": {raw}')
        assert f'"written_at": {raw}' in text          # the token really landed
        path.write_text(text)
        w = json.loads(path.read_text())["written_at"]
        assert isinstance(w, float) and not math.isfinite(w)
        code, reason = host.check_health_file(path)
        assert code == 1, raw
        assert reason.startswith("unhealthy: malformed health file"), reason


# --------------------------------------------------------------------------- #
# R2-N3: the future-timestamp tolerance is documented where operators look
# --------------------------------------------------------------------------- #

def test_healthcheck_help_documents_the_future_tolerance(capsys):
    import pytest as _pytest
    with _pytest.raises(SystemExit):
        host.main(["--help"])
    out = " ".join(capsys.readouterr().out.split())
    assert f"{host.HEALTH_FUTURE_SKEW_S:.0f} s in the future" in out


# --------------------------------------------------------------------------- #
# R2-N1, the ruling: declared connectors must ALL be attached; extras are
# allowed; EVERY attached connector, extra or declared, must be connected
# --------------------------------------------------------------------------- #

def test_one_declared_plus_one_extra_both_up_is_healthy(tmp_path, make_server):
    srv, _ = make_server(connected=True)
    extra = FakeConnector(True)
    srv.tenant_bus("t").attach_connector(extra, "remote")
    path = tmp_path / "health.json"
    data = host.write_health_file(srv, path)
    assert len(data["connectors"]) == 1 and len(data["extra_connectors"]) == 1
    assert data["expected_connectors"] == 1
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 0


def test_an_extra_connector_that_is_down_is_unhealthy(tmp_path, make_server,
                                                      capsys):
    srv, _ = make_server(connected=True)
    srv.tenant_bus("t").attach_connector(FakeConnector(False), "remote")
    path = tmp_path / "health.json"
    host.write_health_file(srv, path)
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
    assert "extra" in capsys.readouterr().out


def test_a_missing_declared_connector_is_unhealthy_even_with_an_extra_up(
        tmp_path, monkeypatch, capsys):
    from composeio import transport_registry

    def boom(spec, app_id):
        raise RuntimeError("backend unavailable")

    monkeypatch.setitem(transport_registry._FACTORIES, "fakeconn", boom)
    srv = ComposeServer()
    srv.add_tenant("t", connectors=[{"type": "fakeconn"}])
    srv.tenant_bus("t").attach_connector(FakeConnector(True), "global")
    path = tmp_path / "health.json"
    data = host.write_health_file(srv, path)
    assert data["connectors"] == [] and len(data["extra_connectors"]) == 1
    assert host.main(["--healthcheck", "--health-file", str(path)]) == 1
    assert "expected 1" in capsys.readouterr().out


def test_extra_connectors_of_the_wrong_shape_is_malformed(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_good(extra_connectors="x")))
    code, reason = host.check_health_file(path)
    assert code == 1 and "extra_connectors" in reason


# --------------------------------------------------------------------------- #
# N3 (round 3): inputs that raise something OTHER than OSError/ValueError
# --------------------------------------------------------------------------- #

def _run_healthcheck(path):
    """--healthcheck in a REAL subprocess, so a traceback would reach stderr."""
    import subprocess
    import sys
    from pathlib import Path
    py_dir = str(Path(host.__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=py_dir)
    env.pop(host.HEALTH_FILE_ENV, None)
    return subprocess.run(
        [sys.executable, "-m", "composeio.composeserver_host", "--healthcheck",
         "--health-file", str(path)],
        capture_output=True, text=True, env=env, cwd=str(Path(path).parent),
        timeout=60)


def test_a_401_digit_written_at_exits_1_without_a_traceback(tmp_path):
    """A valid JSON integer too large for a float: OverflowError at isfinite."""
    path = tmp_path / "health.json"
    text = json.dumps(_good(written_at=0)).replace(
        '"written_at": 0', '"written_at": 1' + "0" * 400)
    assert '"written_at": 1' + "0" * 400 in text
    path.write_text(text)
    r = _run_healthcheck(path)
    assert r.returncode == 1, (r.stdout, r.stderr[-500:])
    assert "Traceback" not in r.stderr, r.stderr[-500:]
    assert r.stdout.startswith("unhealthy:"), r.stdout


def test_deeply_nested_json_exits_1_without_a_traceback(tmp_path):
    """100,000 nested `[`: RecursionError inside json.loads."""
    path = tmp_path / "health.json"
    path.write_text("[" * 100_000)
    r = _run_healthcheck(path)
    assert r.returncode == 1, (r.stdout, r.stderr[-500:])
    assert "Traceback" not in r.stderr, r.stderr[-500:]
    assert r.stdout.strip() == \
        "unhealthy: cannot read health file (RecursionError)", r.stdout
    assert "[[[" not in r.stdout          # the class name only, never content


# --------------------------------------------------------------------------- #
# The catch-all FAILS CLOSED: plant an exception nothing anticipates
# --------------------------------------------------------------------------- #

def _plant(monkeypatch):
    def planted(path, max_age_s):
        raise RuntimeError("planted")
    monkeypatch.setattr(host, "_check_health_file", planted)


def test_a_planted_exception_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_good()))           # a HEALTHY file underneath
    _plant(monkeypatch)
    result = host.check_health_file(path)
    assert result == (1, "unhealthy: cannot read health file (RuntimeError)")
    assert "planted" not in result[1]               # content never echoes


def test_a_planted_exception_fails_closed_through_the_cli(tmp_path,
                                                          monkeypatch, capsys):
    path = tmp_path / "health.json"
    path.write_text(json.dumps(_good()))           # a HEALTHY file underneath
    _plant(monkeypatch)
    code = host.main(["--healthcheck", "--health-file", str(path)])
    out, err = capsys.readouterr()
    assert code == 1                                # never 0
    assert out.strip() == "unhealthy: cannot read health file (RuntimeError)"
    assert "Traceback" not in out + err
    assert "planted" not in out + err
