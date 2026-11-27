"""Contract 0.2, item 3: the append-only ``installs.jsonl`` event log
([DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA] section 4), and the stores' compatibility
with 0.1 ([CXC-PC-V02-STORES-COMPAT]).
"""
from __future__ import annotations

import dataclasses
import fcntl
import io
import json
import os
import random
import shutil
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from composebus.bus import ComposeIOBus

from compose_plugins import conformance
from compose_plugins.fixtures import package_dir
from compose_plugins.host import ConsentDecision, Host
from compose_plugins.lifecycle import ConsentDenied, PluginManager

from v02_helpers import GOLDEN, consent_lines, cp, log_lines, snapshot, web_app, wsgi
from wave2_helpers import MADE, fixture_copy, install, make

D = "hello_world"
EVENT_KEYS = {"seq", "at", "host", "event", "domain", "from_state", "to_state", "version",
              "manifest_sha256", "consent", "consent_basis"}


def ev(**kw) -> dict:
    """A valid event body (no seq)."""
    base = {"at": "2026-11-27T00:00:00Z", "host": "testhost", "event": "install",
            "domain": D, "from_state": "available", "to_state": "installed",
            "version": "1.0.0", "manifest_sha256": "a" * 64, "consent": None,
            "consent_basis": "none"}
    base.update(kw)
    return base


def line(seq: int, **kw) -> bytes:
    return (json.dumps({**ev(**kw), "seq": seq}, sort_keys=True, ensure_ascii=True)
            + "\n").encode()


def findings(pm):
    return pm.install_log_findings()


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-FILE]
# =========================================================================== #
def test_CXC_PC_V02_T_INSTALL_LOG_FILE(tmp_path):
    InstallLog = cp().InstallLog
    from compose_plugins.store import InstallLog as StoreInstallLog
    assert InstallLog is StoreInstallLog
    public = sorted(n for n in dir(InstallLog) if not n.startswith("_")
                    and callable(getattr(InstallLog, n)))
    assert public == ["append", "events", "last_seq", "scan"]
    log = InstallLog(tmp_path / "installs.jsonl")
    assert log.events() == [] and log.last_seq() == 0 and log.scan() == ([], [])
    assert [log.append(ev(event=e)) for e in ("install", "enable", "disable")] == [1, 2, 3]
    got = log.events()
    assert [e["seq"] for e in got] == [1, 2, 3]
    assert [e["event"] for e in got] == ["install", "enable", "disable"]
    assert log.events(domain="other") == [] and log.last_seq() == 3
    assert stat.S_IMODE(os.stat(log.path).st_mode) == 0o600
    # two threads, 100 each: 200 whole lines, seq 1..200
    log2 = InstallLog(tmp_path / "two.jsonl")
    threads = [threading.Thread(target=lambda: [log2.append(ev()) for _ in range(100)])
               for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    raw = log2.path.read_bytes().splitlines()
    assert len(raw) == 200 and [json.loads(x)["seq"] for x in raw] == list(range(1, 201))
    # U+2028 / U+2029 / LF in a value: escaped, one line per event
    log3 = InstallLog(tmp_path / "esc.jsonl")
    log3.append(ev(version="1.0.0 x y\nz"))
    log3.append(ev())
    b = log3.path.read_bytes()
    assert b.count(b"\n") == 2 and b"\\u2028" in b and b"\\u2029" in b and b.isascii()
    assert [e["version"] for e in log3.events()] == ["1.0.0 x y\nz", "1.0.0"]
    log4 = InstallLog(tmp_path / "u2028.jsonl")
    from compose_plugins.consent import ConsentRecord
    rec = ConsentRecord(domain=D, version="1.0.0", manifest_sha256="b" * 64, granted=(),
                        decided_by="opé", decided_at="2026-11-27T00:00:00Z",
                        host="testhost", decision="granted")
    log4.append(ev(consent=rec.to_json(), consent_basis="recorded"))
    data = log4.path.read_bytes()
    assert data.count(b"\n") == 1 and b"\\u00e9" in data and data.isascii()


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-EVENTS-OF-THE-LIFECYCLE]
# =========================================================================== #
class StubHost(Host):
    """A 0.1-style Host: only the 0.1 protocol members."""

    def __init__(self, root, level):
        super().__init__("composeio", state_root=root, minimum_isolation=level)
        self._bus = ComposeIOBus(bus_id="stub")

    def consent_gate(self, r):
        return ConsentDecision("granted" if r.accepted and r.operator else "refused",
                               r.operator or "stub:no-operator")

    def validate_section(self, s):
        return []

    def render_section(self, s):
        return []

    def bus(self):
        return self._bus


class Target:
    foreign_host = "gnomon"

    def __init__(self, isolation, workdir):
        self.host = StubHost(workdir, isolation)
        self.manager = PluginManager(self.host)
        self.bus = self.host.bus()
        self.isolation = isolation
        self.operator = "stub-operator"

    def close(self):
        self.manager.close()


def _conformance_root(tmp_path) -> Path:
    out = io.StringIO()
    rc = conformance.run(Target, "inprocess", out=out, workdir=tmp_path / "conf")
    assert rc == 0, out.getvalue()
    return tmp_path / "conf" / "host-1"


def test_CXC_PC_V02_T_INSTALL_LOG_EVENTS_OF_THE_LIFECYCLE(tmp_path):
    root = _conformance_root(tmp_path)
    events = [json.loads(x) for x in (root / "installs.jsonl").read_text().splitlines()]
    consents = [json.loads(x) for x in (root / "consent.jsonl").read_text().splitlines()]
    print(json.dumps(events, indent=1)[:3000])
    assert [e["event"] for e in events] == ["install", "enable", "upgrade", "enable",
                                            "disable", "enable", "uninstall"]
    assert [e["seq"] for e in events] == list(range(1, 8))
    assert all(set(e) == EVENT_KEYS for e in events)
    first, upg, last = events[0], events[2], events[6]
    assert first["from_state"] == "available" and first["consent_basis"] == "recorded"
    assert upg["consent_basis"] == "recorded"
    assert upg["consent"]["manifest_sha256"] == upg["manifest_sha256"]
    assert last["to_state"] == "uninstalled" and last["manifest_sha256"] == upg["manifest_sha256"]
    assert [(e["from_state"], e["to_state"]) for e in events] == [
        ("available", "installed"), ("installed", "enabled"), ("enabled", "installed"),
        ("installed", "enabled"), ("enabled", "disabled"), ("disabled", "enabled"),
        ("enabled", "uninstalled")]
    for e in events:
        if e["consent"] is not None:
            assert e["consent"] in consents
    assert [e["consent_basis"] for e in events] == ["recorded", "carried", "recorded",
                                                    "carried", "carried", "carried", "carried"]


def test_CXC_PC_V02_T_INSTALL_LOG_EVENTS_OF_THE_LIFECYCLE_carried_and_refused(tmp_path):
    host, pm = make(tmp_path / "h")
    install(pm, fixture_copy(tmp_path, "v1"))
    same = fixture_copy(tmp_path, "v1", edit=lambda d: d.update(version="1.0.1"), name="v101")
    p = pm.prepare_upgrade(same)
    pm.upgrade(same, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    e = log_lines(pm)[-1]
    assert e["event"] == "upgrade" and e["consent_basis"] == "carried"
    assert e["consent"]["manifest_sha256"] != e["manifest_sha256"]
    assert e["consent"] == consent_lines(pm)[-1]
    n_events, n_consent = len(log_lines(pm)), len(consent_lines(pm))
    v2 = fixture_copy(tmp_path, "v2")
    p2 = pm.prepare_upgrade(v2)
    with pytest.raises(ConsentDenied):
        pm.upgrade(v2, consented_sha256=p2.manifest_sha256, operator="op", accepted=False)
    assert len(log_lines(pm)) == n_events and len(consent_lines(pm)) == n_consent + 1
    # configure, record_decline and a status change write no event
    pm.configure(D, {"greeting": "x"})
    pm.record_decline(pm.prepare_upgrade(v2), decided_by="op")
    assert len(log_lines(pm)) == n_events


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-ORDER-AND-FAULTS]
# =========================================================================== #
class PlantedError(Exception):
    pass


OPS = ["install", "upgrade", "enable", "disable", "uninstall"]


def _ready(tmp_path, op):
    """A manager just before ``op``; returns (host, pm, run_op)."""
    host, pm = make(tmp_path / f"root-{op}")
    v1 = fixture_copy(tmp_path, "v1", name=f"v1-{op}")
    if op == "install":
        p = pm.prepare_install(v1)
        return host, pm, lambda: pm.install(v1, consented_sha256=p.manifest_sha256,
                                            operator="op", accepted=True)
    install(pm, v1)
    if op != "enable":
        pm.enable(D)
    if op == "upgrade":
        v2 = fixture_copy(tmp_path, "v2", name=f"v2-{op}")
        p = pm.prepare_upgrade(v2)
        return host, pm, lambda: pm.upgrade(v2, consented_sha256=p.manifest_sha256,
                                            operator="op", accepted=True)
    return host, pm, lambda: getattr(pm, op)(D)


@pytest.mark.parametrize("op", OPS)
def test_CXC_PC_V02_T_INSTALL_LOG_ORDER_AND_FAULTS_F1_append_fails(tmp_path, op, monkeypatch):
    InstallLogRefused = cp().InstallLogRefused
    host, pm, run = _ready(tmp_path, op)
    ip = host.state_root / "installs.json"
    before = ip.read_bytes() if ip.exists() else None
    calls = []
    for name in ("_stop", "_place_code"):
        real = getattr(pm, name)
        monkeypatch.setattr(pm, name, lambda *a, _n=name, _r=real, **k: (calls.append(_n),
                                                                          _r(*a, **k))[1])

    def boom(event):
        raise OSError("append failed")
    monkeypatch.setattr(pm.install_log, "append", boom)
    with pytest.raises(InstallLogRefused) as ei:
        run()
    print(ei.value)
    assert str(pm.install_log.path) in str(ei.value) and "OSError" in str(ei.value)
    assert (ip.read_bytes() if ip.exists() else None) == before
    if op != "enable":
        assert calls == []


@pytest.mark.parametrize("op", OPS)
def test_CXC_PC_V02_T_INSTALL_LOG_ORDER_AND_FAULTS_F2_snapshot_fails(tmp_path, op, monkeypatch):
    import compose_plugins.store as store
    host, pm, run = _ready(tmp_path, op)
    n = len(log_lines(pm))
    real = store._atomic_write

    def failing(path, data):
        if Path(path).name == "installs.json":
            raise PlantedError("snapshot write failed")
        return real(path, data)
    monkeypatch.setattr(store, "_atomic_write", failing)
    with pytest.raises(PlantedError):
        run()
    lines = log_lines(pm)
    assert len(lines) == n + 2
    event, abort = lines[-2], lines[-1]
    assert event["event"] == op and abort["event"] == "abort"
    assert abort["aborts_seq"] == event["seq"] and abort["reason"] == "PlantedError"
    assert set(abort) == EVENT_KEYS | {"aborts_seq", "reason"}
    assert abort["consent"] is None and abort["consent_basis"] == "none"


def test_CXC_PC_V02_T_INSTALL_LOG_ORDER_AND_FAULTS_F3_F5_ordering(tmp_path, monkeypatch):
    host, pm = make(tmp_path)
    order = []
    real_append, real_put = pm.install_log.append, pm.installs.put
    monkeypatch.setattr(pm.install_log, "append",
                        lambda e: (lambda s: (order.append(("append", s)), s)[1])(real_append(e)))
    monkeypatch.setattr(pm.installs, "put",
                        lambda d, r: (order.append(("put", r.get("event_seq"))), real_put(d, r))[1])
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    v2 = fixture_copy(tmp_path, "v2")
    p = pm.prepare_upgrade(v2)
    pm.upgrade(v2, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    pm.disable(D)
    pm.enable(D)
    pm.uninstall(D)
    print(order)
    assert len(order) == 14
    for i in range(0, 14, 2):
        assert order[i][0] == "append" and order[i + 1] == ("put", order[i][1])
    # F5: every record carries the seq of its domain's last non-abort event
    last = [e for e in log_lines(pm) if e["event"] != "abort" and e["domain"] == D][-1]
    assert json.loads((host.state_root / "installs.json").read_text())[D]["event_seq"] == \
        last["seq"] == 7


CHILD = r"""
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[2])
import wave2_helpers as h
root = Path(sys.argv[1])
host, pm = h.make(root)
h.install(pm, h.fixture_copy(root, "v1"))
real = pm.install_log.append
def die(event):
    real(event)
    os._exit(0)
pm.install_log.append = die
pm.enable("hello_world")
os._exit(3)
"""


def test_CXC_PC_V02_T_INSTALL_LOG_ORDER_AND_FAULTS_F4_crash_after_append(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(p for p in sys.path if p))
    r = subprocess.run([sys.executable, str(script), str(tmp_path),
                        str(Path(__file__).resolve().parent)],
                       capture_output=True, text=True, env=env, timeout=120)
    assert r.returncode == 0, r.stderr
    root = tmp_path / "state-inprocess"
    events = [json.loads(x) for x in (root / "installs.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events] == ["install", "enable"]
    rec = json.loads((root / "installs.json").read_text())[D]
    assert rec["event_seq"] == 1 and rec["state"] == "installed"
    from wave2_helpers import TestHost
    pm = PluginManager(TestHost(root))
    MADE.append(pm)
    (f,) = findings(pm)
    assert f.kind == "unreflected-event" and f.seq == 2 and f.domain == D


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-DIVERGENCE]
# =========================================================================== #
def _installed(tmp_path, name="h"):
    host, pm = make(tmp_path / name)
    install(pm, fixture_copy(tmp_path, "v1", name=f"v1-{name}"))
    return host, pm


def _append_raw(pm, data: bytes):
    with open(pm.install_log.path, "ab") as fh:
        fh.write(data)


def _set_event_seq(host, value):
    ip = host.state_root / "installs.json"
    d = json.loads(ip.read_text())
    d[D]["event_seq"] = value
    ip.write_text(json.dumps(d))


def test_CXC_PC_V02_T_INSTALL_LOG_DIVERGENCE(tmp_path):
    host, pm, app = web_app(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    sha1 = pm.record(D)["manifest_sha256"]
    assert findings(pm) == ()
    # the record at N: nothing
    st, h, clean = wsgi(app, "", "/")
    norm = clean.replace(app.csrf_token("op"), "{CSRF}")
    golden = (GOLDEN / "index_hello_world_installed.html").read_text()
    assert norm == golden.replace(", contract 0.1</footer>", ", contract 0.2</footer>")
    assert "Install log findings" not in clean
    # event N in the log, the record at N-1
    _append_raw(pm, line(2, event="enable", from_state="installed", to_state="enabled",
                         manifest_sha256=sha1))
    (f,) = findings(pm)
    assert isinstance(f, cp().InstallLogFinding) and f.kind == "unreflected-event"
    msg = ("installs.jsonl event 2 (enable of hello_world 1.0.0) is not reflected in "
           "installs.json (record is at event 1)")
    assert f.message == msg and f.seq == 2 and f.domain == D
    st, h, page = wsgi(app, "", "/")
    assert "Install log findings" in page and msg in page
    # a finding does not stop enable; only enable's own line is added
    before = (host.state_root / "installs.jsonl").read_bytes()
    pm.enable(D)
    assert pm.state(D) == "enabled"
    after = (host.state_root / "installs.jsonl").read_bytes()
    assert after.startswith(before) and after[len(before):].count(b"\n") == 1
    assert json.loads(after[len(before):])["seq"] == 3
    assert findings(pm) == ()
    # an event followed by its abort: nothing
    host2, pm2 = _installed(tmp_path, "abort")
    _append_raw(pm2, line(2, event="enable") + line(3, event="abort", consent=None,
                                                    **{"aborts_seq": 2, "reason": "X"}))
    assert findings(pm2) == ()
    # the record beyond the log: "behind"
    host3, pm3 = _installed(tmp_path, "ahead")
    _set_event_seq(host3, 9)
    (f3,) = findings(pm3)
    assert f3.kind == "snapshot-ahead" and f3.message == (
        "installs.json record for hello_world is at event 9, beyond the last event 1 of "
        "installs.jsonl")
    # an empty log with a record carrying event_seq: behind
    (host3.state_root / "installs.jsonl").write_bytes(b"")
    (f4,) = findings(pm3)
    assert f4.kind == "snapshot-ahead" and "beyond the last event 0" in f4.message
    # a 0.1 state root: nothing
    root = tmp_path / "v01"
    shutil.copytree(GOLDEN / "state_root_after_conformance", root)
    pm4 = PluginManager(StubHost(root, "inprocess"))
    MADE.append(pm4)
    assert findings(pm4) == ()


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-MALFORMED-LINES]
# =========================================================================== #
def test_CXC_PC_V02_T_INSTALL_LOG_MALFORMED_LINES_torn_last_line(tmp_path):
    from wave2_helpers import TestHost
    host, pm = _installed(tmp_path)
    pm.enable(D)
    pm.disable(D)
    log_path = host.state_root / "installs.jsonl"
    base_log = log_path.read_bytes()
    base_snap = (host.state_root / "installs.json").read_bytes()
    sha1 = pm.record(D)["manifest_sha256"]
    full = line(4, event="enable", from_state="disabled", to_state="enabled",
                manifest_sha256=sha1).rstrip(b"\n")
    rng = random.Random(95)
    pm.close()
    for cut in [rng.randrange(1, len(full)) for _ in range(50)]:
        log_path.write_bytes(base_log + full[:cut])
        (host.state_root / "installs.json").write_bytes(base_snap)
        m = PluginManager(TestHost(host.state_root))          # never raises
        (f,) = m.install_log_findings()
        assert (f.kind, f.code, f.line) == ("malformed-line", "torn-last-line", 4), cut
        assert [e["seq"] for e in m.install_log.events()] == [1, 2, 3]
        m.enable(D)
        raw = log_path.read_bytes()
        rows = raw.split(b"\n")
        assert raw.endswith(b"\n") and rows[3] == full[:cut]
        assert json.loads(rows[4])["seq"] == 4 and json.loads(rows[4])["event"] == "enable"
        events, problems = m.install_log.scan()
        assert [p[0] for p in problems] == [4] and [e["seq"] for e in events] == [1, 2, 3, 4]
        m.disable(D)
        m.close()


def test_CXC_PC_V02_T_INSTALL_LOG_MALFORMED_LINES_garbage_unreadable_never_quoted(tmp_path):
    from wave2_helpers import TestHost
    marker = b"GARBAGE-MARKER-91 secret=hunter2"
    host, pm = _installed(tmp_path)
    _append_raw(pm, marker + b"\n")
    pm.enable(D)                                   # takes highest valid seq + 1
    assert [e["seq"] for e in pm.install_log.events()] == [1, 2]
    m = PluginManager(TestHost(host.state_root))
    MADE.append(m)
    (f,) = m.install_log_findings()
    assert (f.kind, f.code, f.line) == ("malformed-line", "not-json", 2)
    assert m.install_log.last_seq() == 2
    assert marker.decode() not in f.message and "GARBAGE" not in f.message
    # a directory, and a mode-000 file: one `unreadable` finding, no raise
    for kind in ("directory", "mode-000"):
        root = tmp_path / kind
        root.mkdir()
        p = root / "installs.jsonl"
        if kind == "directory":
            p.mkdir()
        else:
            p.write_bytes(line(1))
            p.chmod(0)
        try:
            mm = PluginManager(TestHost(root))
            MADE.append(mm)
            (u,) = mm.install_log_findings()
            assert u.kind == "unreadable" and u.domain is None
            assert u.message in ("installs.jsonl cannot be read (IsADirectoryError)",
                                 "installs.jsonl cannot be read (PermissionError)")
        finally:
            if kind == "mode-000":
                p.chmod(0o600)


@pytest.mark.parametrize("name,data,codes", [
    ("empty", b"", []),
    ("only-torn", b'{"seq": 1, "at"', [(1, "torn-last-line")]),
    ("only-garbage", b"x\n\x00\xff\n[1]\n", [(1, "not-json"), (2, "not-json"), (3, "not-an-object")]),
    ("garbage-middle", line(1) + b"junk\n" + line(2), [(2, "not-json")]),
    ("valid-no-lf", line(1) + line(2).rstrip(b"\n"), []),
    ("seq-repeated", line(1) + line(1), [(2, "seq-not-increasing")]),
    ("bad-keys", line(1).replace(b'"at"', b'"when"'), [(1, "bad-keys")]),
    ("bad-value", line(1).replace(b'"install"', b'"explode"'), [(1, "bad-value")]),
])
def test_CXC_PC_V02_T_INSTALL_LOG_MALFORMED_LINES_scan(tmp_path, name, data, codes):
    InstallLog = cp().InstallLog
    log = InstallLog(tmp_path / "installs.jsonl")
    log.path.write_bytes(data)
    events, problems = log.scan()
    assert problems == codes
    nxt = log.append(ev())
    assert nxt == max([e["seq"] for e in events] or [0]) + 1
    raw = log.path.read_bytes()
    assert raw.endswith(b"\n") and json.loads(raw.split(b"\n")[-2])["seq"] == nxt


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-FAILURE-TEXT]
# =========================================================================== #
MARKER = "PLANTED-7f3a9c path=/x/y secret=hunter2"


def test_CXC_PC_V02_T_INSTALL_LOG_FAILURE_TEXT(tmp_path, monkeypatch):
    InstallLogRefused = cp().InstallLogRefused
    host, pm = make(tmp_path / "a")
    src = fixture_copy(tmp_path, "v1")
    p = pm.prepare_install(src)

    def planted(*a, **k):
        raise PlantedError(MARKER + "\nline2 more")
    monkeypatch.setattr(pm, "_place_code", planted)
    with pytest.raises(PlantedError) as ei:
        pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert str(ei.value) == MARKER + "\nline2 more"
    lines = log_lines(pm)
    assert lines[-1]["event"] == "abort" and lines[-1]["reason"] == "PlantedError"
    assert "PLANTED-7f3a9c" not in (host.state_root / "installs.jsonl").read_text()
    assert all("PLANTED-7f3a9c" not in f.message for f in pm.install_log_findings())
    # the append itself fails with an OSError carrying the marker
    host2, pm2 = make(tmp_path / "b")
    install(pm2, fixture_copy(tmp_path, "v1", name="v1b"))
    real_flock = fcntl.flock

    def failing_flock(fd, op):
        if op & fcntl.LOCK_EX:
            raise OSError(MARKER)
        return real_flock(fd, op)
    monkeypatch.setattr(fcntl, "flock", failing_flock)
    with pytest.raises(InstallLogRefused) as ei2:
        pm2.enable(D)
    monkeypatch.setattr(fcntl, "flock", real_flock)
    assert "PLANTED-7f3a9c" not in str(ei2.value) and "OSError" in str(ei2.value)
    assert "PLANTED-7f3a9c" not in (host2.state_root / "installs.jsonl").read_text()
    assert all("PLANTED-7f3a9c" not in f.message for f in pm2.install_log_findings())


# =========================================================================== #
# [CXC-PC-V02-T-INSTALL-LOG-FINDINGS-METHOD]
# =========================================================================== #
def test_CXC_PC_V02_T_INSTALL_LOG_FINDINGS_METHOD(tmp_path):
    InstallLogFinding = cp().InstallLogFinding
    fields = [f.name for f in dataclasses.fields(InstallLogFinding)]
    assert fields == ["kind", "domain", "seq", "line", "code", "message"]
    scenarios = {}
    h, pm = _installed(tmp_path, "unreflected")
    _append_raw(pm, line(2, event="enable", from_state="installed", to_state="enabled"))
    scenarios["unreflected-event"] = (h, pm, dict(domain=D, seq=2, line=None, code=None))
    h, pm = _installed(tmp_path, "ahead")
    _set_event_seq(h, 5)
    scenarios["snapshot-ahead"] = (h, pm, dict(domain=D, seq=None, line=None, code=None))
    h, pm = _installed(tmp_path, "garbage")
    _append_raw(pm, b"{not json\n")
    scenarios["malformed-line"] = (h, pm, dict(domain=None, seq=None, line=2, code="not-json"))
    h, pm = _installed(tmp_path, "gap")
    _append_raw(pm, line(3, event="enable", from_state="installed", to_state="enabled"))
    _set_event_seq(h, 3)
    scenarios["seq-gap"] = (h, pm, dict(domain=None, seq=3, line=2, code=None))
    h, pm = _installed(tmp_path, "unreadable")
    lp = h.state_root / "installs.jsonl"
    lp.unlink()
    lp.mkdir()
    scenarios["unreadable"] = (h, pm, dict(domain=None, seq=None, line=None, code=None))
    from compose_plugins.web import PluginManagerApp
    import html
    for kind, (h, pm, want) in scenarios.items():
        before = snapshot(h.state_root)
        r1, r2 = pm.install_log_findings(), pm.install_log_findings()
        assert isinstance(r1, tuple) and r1 == r2 and len(r1) == 1, (kind, r1)
        (f,) = r1
        assert isinstance(f, InstallLogFinding) and f.kind == kind
        assert {k: getattr(f, k) for k in want} == want, (kind, f)
        assert snapshot(h.state_root) == before
        h.auth_hook = lambda env: "op"
        st, hd, page = wsgi(PluginManagerApp(pm), "", "/")
        assert html.escape(f.message) in page and "Install log findings" in page
        with pytest.raises(dataclasses.FrozenInstanceError):
            f.kind = "x"
    assert scenarios["seq-gap"][1].install_log_findings()[0].message == \
        "installs.jsonl seq jumps to 3 at line 2"
    assert scenarios["malformed-line"][1].install_log_findings()[0].message == \
        "installs.jsonl line 2 is malformed (not-json) and is skipped"
    assert scenarios["unreadable"][1].install_log_findings()[0].message == \
        "installs.jsonl cannot be read (IsADirectoryError)"
    host, clean = make(tmp_path / "clean")
    assert clean.install_log_findings() == ()
    # sorted by (kind, domain or "", line or 0, seq or 0)
    h, pm = _installed(tmp_path, "many")
    _append_raw(pm, b"junk\n" + line(5, event="enable", from_state="installed",
                                     to_state="enabled"))
    got = pm.install_log_findings()
    assert [f.kind for f in got] == sorted(f.kind for f in got)
    assert {f.kind for f in got} == {"malformed-line", "seq-gap", "unreflected-event"}


# =========================================================================== #
# [CXC-PC-V02-T-STORES-COMPAT]
# =========================================================================== #
def test_CXC_PC_V02_T_STORES_COMPAT_a_0_1_state_root_loads(tmp_path):
    root = tmp_path / "state"
    shutil.copytree(GOLDEN / "state_root_after_conformance", root)
    assert not (root / "installs.jsonl").exists()
    pm = PluginManager(StubHost(root, "inprocess"))
    MADE.append(pm)
    assert pm.state(D) == "uninstalled" and len(pm.consent.records()) == 2
    assert pm.install_log_findings() == ()
    src = tmp_path / "pkg"
    shutil.copytree(package_dir("v1"), src, ignore=shutil.ignore_patterns("__pycache__"))
    p = pm.prepare_install(src)
    pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    (e,) = [json.loads(x) for x in (root / "installs.jsonl").read_text().splitlines()]
    assert e["seq"] == 1 and e["event"] == "install" and e["from_state"] == "uninstalled"
    assert len(pm.consent.records()) == 3


def test_CXC_PC_V02_T_STORES_COMPAT_b_a_0_1_host_runs_steps_1_to_9(tmp_path):
    for member in ("record_decline", "install_log", "install_log_findings"):
        assert member not in vars(Host)           # the Host protocol gained nothing
    root = _conformance_root(tmp_path)
    assert (root / "installs.jsonl").exists()


def test_CXC_PC_V02_T_STORES_COMPAT_c_d_reader_refuses_unknown_and_bad_keys(tmp_path):
    from compose_plugins.consent import ConsentStore
    from compose_plugins.errors import ConsentRefused
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    good = consent_lines(pm)[0]
    for bad, key in (({**good, "kind": "install"}, "kind"),
                     ({**good, "eula_sha256": "nothex"}, "eula_sha256")):
        p = tmp_path / f"{key}.jsonl"
        p.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
        with pytest.raises(ConsentRefused) as ei:
            ConsentStore(p).records()
        assert key in str(ei.value) and "line 2" in str(ei.value)


PROBE_010 = r"""
import json, sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import compose_plugins
from compose_plugins.consent import ConsentRecord
from compose_plugins.errors import ConsentRefused
print("compose_plugins", compose_plugins.__version__, compose_plugins.__file__)
try:
    ConsentRecord.from_json(json.loads(sys.argv[3]))
except ConsentRefused as exc:
    print("REFUSED:", exc)
    sys.exit(0)
print("ACCEPTED")
sys.exit(3)
"""


def test_CXC_PC_V02_T_STORES_COMPAT_e_the_0_1_0_wheel_refuses_loudly(tmp_path):
    """The published 0.1.0 wheel is imported from its zip (never installed);
    COMPOSE_PLUGINS_V010_WHEEL names its path."""
    whl = os.environ.get("COMPOSE_PLUGINS_V010_WHEEL")
    if not whl:
        pytest.skip("COMPOSE_PLUGINS_V010_WHEEL is not set: the published 0.1.0 wheel "
                    "check (STORES-COMPAT e) was NOT exercised here")
    import hashlib
    print("wheel sha256", hashlib.sha256(Path(whl).read_bytes()).hexdigest())
    host, pm = make(tmp_path)
    from v02_helpers import eula_package, install_eula
    install_eula(pm, eula_package(tmp_path))
    rec = consent_lines(pm)[-1]
    assert "eula_sha256" in rec
    composebus = Path(__file__).resolve().parents[2] / "composebus"
    r = subprocess.run([sys.executable, "-I", "-c", PROBE_010, whl, str(composebus),
                        json.dumps(rec)], capture_output=True, text=True, timeout=60)
    print(r.stdout, r.stderr)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "compose_plugins 0.1.0" in r.stdout and ".whl" in r.stdout
    assert "REFUSED: consent record REFUSED: keys must be exactly" in r.stdout
