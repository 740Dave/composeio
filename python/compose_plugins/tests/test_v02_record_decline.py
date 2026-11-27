"""Contract 0.2, item 2: ``PluginManager.record_decline(prompt, *, decided_by)``
([DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA] section 3): the six contract tests.
"""
from __future__ import annotations

import builtins
import copy
import dataclasses
import hashlib
import importlib
import json
import random
import subprocess
import threading
from pathlib import Path

import pytest

from compose_plugins.lifecycle import ConsentDenied, ConsentPrompt, PluginManager

from v02_helpers import consent_lines, cp, snapshot
from wave2_helpers import fixture_copy, install, make, wait_for

D = "hello_world"
EIGHT = {"domain", "version", "manifest_sha256", "granted", "decided_by", "decided_at",
         "host", "decision"}


def _split(root: Path):
    """(every file but consent.jsonl, the bytes of consent.jsonl or None)."""
    c = root / "consent.jsonl"
    return snapshot(root, skip=("consent.jsonl",)), (c.read_bytes() if c.exists() else None)


# =========================================================================== #
# Test 1 [CXC-PC-V02-T-RD-ONE-RECORD-NOTHING-ELSE-CHANGES]
# =========================================================================== #
def test_CXC_PC_V02_T_RD_ONE_RECORD_NOTHING_ELSE_CHANGES(tmp_path):
    host, pm = make(tmp_path / "a")
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.enable(D)
    q = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    others, c0 = _split(host.state_root)
    r = pm.record_decline(q, decided_by="operator-declined")
    assert r.decision == "refused" and r.manifest_sha256 == q.manifest_sha256
    assert r.version == q.manifest.version and r.granted == q.content.granted
    assert r.decided_by == "operator-declined" and r.host == host.name
    assert set(r.to_json()) == EIGHT
    c1 = (host.state_root / "consent.jsonl").read_bytes()
    assert c1.startswith(c0) and c1[len(c0):].count(b"\n") == 1
    assert json.loads(c1[len(c0):]) == r.to_json()
    assert snapshot(host.state_root, skip=("consent.jsonl",)) == others
    # an INSTALL prompt for a domain never installed
    host2, pm2 = make(tmp_path / "b")
    q2 = pm2.prepare_install(fixture_copy(tmp_path, "v1", name="fresh"))
    others2, c02 = _split(host2.state_root)
    assert c02 is None and not (host2.state_root / "installs.json").exists()
    r2 = pm2.record_decline(q2, decided_by="operator-declined")
    assert (host2.state_root / "consent.jsonl").read_bytes() == \
        (json.dumps(r2.to_json(), sort_keys=True, ensure_ascii=False) + "\n").encode()
    assert snapshot(host2.state_root, skip=("consent.jsonl",)) == others2
    assert not (host2.state_root / "installs.json").exists()
    assert not (host2.state_root / "installs.jsonl").exists()
    assert list(host2.install_root.iterdir()) == []


@pytest.mark.parametrize("bad", ["", "line two", 7, None])
def test_CXC_PC_V02_T_RD_ONE_RECORD_NOTHING_ELSE_CHANGES_decided_by_refused(tmp_path, bad):
    from compose_plugins.errors import ConsentRefused
    host, pm = make(tmp_path)
    q = pm.prepare_install(fixture_copy(tmp_path, "v1"))
    before = snapshot(host.state_root)
    with pytest.raises(ConsentRefused) as ei:
        pm.record_decline(q, decided_by=bad)
    assert "decided_by" in str(ei.value)
    assert snapshot(host.state_root) == before
    with pytest.raises(TypeError):
        pm.record_decline(q, "positional")          # decided_by is keyword-only
    with pytest.raises(TypeError):
        pm.record_decline(q)                        # and has no default


# =========================================================================== #
# Test 2 [CXC-PC-V02-T-RD-INSTALLED-DOMAIN-UNCHANGED]
# =========================================================================== #
def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            h.update(p.relative_to(root).as_posix().encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


@pytest.mark.parametrize("enabled", [True, False], ids=["running", "stopped-installed"])
def test_CXC_PC_V02_T_RD_INSTALLED_DOMAIN_UNCHANGED(tmp_path, enabled):
    """The delta's second repeat says "disabled", but [CXC-PC-LIFECYCLE-STATES]
    allows upgrade (so ``prepare_upgrade``) only from installed or enabled; a
    prompt prepared while enabled is stale once disabled. The not-running
    repeat is therefore the ``installed`` state (stopped, never enabled)."""
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    pm.configure(D, {"greeting": "kept"})
    if enabled:
        pm.enable(D)
        assert wait_for(lambda: (pm.tick(), pm.status(D))[1] == "running")
    q = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    rec = copy.deepcopy(pm.record(D))
    state, status, running = pm.state(D), pm.status(D), pm.running(D)
    n, cfg = pm.instantiations, pm.configs.get(D)
    code = _tree_hash(Path(rec["path"]))
    published = []
    bus = host.bus()
    real_ps, real_pe = bus.publish_state, bus.publish_event
    bus.publish_state = lambda *a, **k: (published.append(a), real_ps(*a, **k))[1]
    bus.publish_event = lambda *a, **k: (published.append(a), real_pe(*a, **k))[1]
    statuses = list(host.statuses)
    pm.record_decline(q, decided_by="op")
    assert pm.record(D) == rec
    assert (pm.state(D), pm.status(D)) == (state, status)
    assert pm.running(D) is running and pm.instantiations == n
    assert pm.configs.get(D) == cfg and _tree_hash(Path(rec["path"])) == code
    assert published == [] and host.statuses == statuses


# =========================================================================== #
# Test 3 [CXC-PC-V02-T-RD-CONCURRENT-WITH-UPGRADE]
# =========================================================================== #
def test_CXC_PC_V02_T_RD_CONCURRENT_WITH_UPGRADE(tmp_path):
    PromptStale = cp().PromptStale
    rng = random.Random(20261127)
    v1 = fixture_copy(tmp_path, "v1")
    v2 = fixture_copy(tmp_path, "v2")
    outcomes = {"A": set(), "B": set()}
    for i in range(200):
        variant = "A" if i % 2 == 0 else "B"
        host, pm = make(tmp_path / f"it-{i}")
        install(pm, v1)
        q = pm.prepare_upgrade(v2)
        barrier = threading.Barrier(2)
        errors = {}

        def decline():
            barrier.wait()
            try:
                pm.record_decline(q, decided_by="op-a")
                errors["decline"] = None
            except PromptStale as exc:
                errors["decline"] = exc

        def upgrade():
            barrier.wait()
            try:
                pm.upgrade(v2, consented_sha256=q.manifest_sha256, operator="op",
                           accepted=(variant == "B"))
                errors["upgrade"] = None
            except ConsentDenied as exc:
                errors["upgrade"] = exc
        threads = [threading.Thread(target=decline), threading.Thread(target=upgrade)]
        rng.shuffle(threads)
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        recs = consent_lines(pm)              # parses line by line
        json.loads((host.state_root / "installs.json").read_text())
        version = pm.record(D)["version"]
        if variant == "A":
            assert version == "1.0.0"
            assert not any(r["decision"] == "granted" and r["version"] == "2.0.0" for r in recs)
            assert errors["decline"] is None
        else:
            granted = [k for k, r in enumerate(recs) if r["decision"] == "granted"
                       and r["manifest_sha256"] == q.manifest_sha256]
            assert (version == "2.0.0") == bool(granted) and errors["upgrade"] is None
            declines = [k for k, r in enumerate(recs) if r["decided_by"] == "op-a"]
            assert not any(d > g for d in declines for g in granted), recs
            if errors["decline"] is not None:
                assert declines == []
            outcomes["B"].add("decline-lost" if errors["decline"] else "decline-first")
        pm.close()
    print("variant B orders seen:", sorted(outcomes["B"]))


# =========================================================================== #
# Test 4 [CXC-PC-V02-T-RD-ZERO-GATE-AND-HOST-CALLBACKS]
# =========================================================================== #
class RecordingHost:
    """Proxies a Host; logs every method call (reads of name/state_root excluded)."""

    def __init__(self, inner, log):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_log", log)

    def __getattr__(self, name):
        value = getattr(self._inner, name)
        if callable(value) and name not in ("name", "state_root"):
            def call(*a, **k):
                self._log.append(name)
                return value(*a, **k)
            return call
        return value


def test_CXC_PC_V02_T_RD_ZERO_GATE_AND_HOST_CALLBACKS(tmp_path, monkeypatch):
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    q_up = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    host_b, pm_b = make(tmp_path / "other")
    q_in = pm_b.prepare_install(fixture_copy(tmp_path, "v1", name="fresh"))
    counts = {}
    for target in (pm, pm_b):
        log = []
        target.host = RecordingHost(target.host, log)
        counts[id(target)] = log
        for name in ("_gate", "_fresh", "_place_code", "install", "upgrade", "uninstall",
                     "enable", "disable", "configure", "prepare_install", "prepare_upgrade"):
            real = getattr(target, name)

            def counted(*a, _n=name, _l=log, _r=real, **k):
                _l.append(_n)
                return _r(*a, **k)
            monkeypatch.setattr(target, name, counted)
    spawned, imported, read = [], [], []
    for fn in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, fn, lambda *a, _f=fn, **k: spawned.append(_f))
    real_import, real_im = builtins.__import__, importlib.import_module
    monkeypatch.setattr(builtins, "__import__", lambda name, *a, **k: (
        imported.append(name) if name.split(".")[0] == "hello_world" else None,
        real_import(name, *a, **k))[1])
    monkeypatch.setattr(importlib, "import_module", lambda name, *a, **k: (
        imported.append(name), real_im(name, *a, **k))[1])
    import compose_plugins.package as pkg
    import compose_plugins.lifecycle as lc
    for mod in (pkg, lc):
        for fn in ("read_manifest", "open_package"):
            if hasattr(mod, fn):
                real = getattr(mod, fn)
                monkeypatch.setattr(mod, fn, lambda *a, _r=real, **k: (read.append(a),
                                                                        _r(*a, **k))[1])
    pm.record_decline(q_up, decided_by="op")
    pm_b.record_decline(q_in, decided_by="op")
    assert counts[id(pm)] == [] and counts[id(pm_b)] == []
    assert spawned == [] and imported == [] and read == []


# =========================================================================== #
# Test 5 [CXC-PC-V02-T-RD-UNPREPARED-REFUSED]
# =========================================================================== #
def test_CXC_PC_V02_T_RD_UNPREPARED_REFUSED(tmp_path):
    PromptNotPrepared = cp().PromptNotPrepared
    from compose_plugins.errors import PluginContractRefusal
    assert issubclass(PromptNotPrepared, PluginContractRefusal)
    host, pm = make(tmp_path)
    install(pm, fixture_copy(tmp_path, "v1"))
    q = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    hand = ConsentPrompt(q.kind, q.source, q.manifest, q.manifest_sha256, q.content, q.runs_as)
    assert hand == q                          # the token is excluded from comparison
    assert "token" not in repr(q)
    other = PluginManager(host)
    q_other = other.prepare_upgrade(fixture_copy(tmp_path, "v2", name="v2b"))
    cases = [hand, q_other, dataclasses.replace(q, manifest_sha256="0" * 64),
             dataclasses.replace(q, kind="install"), None, {"kind": "upgrade"}, "prompt"]
    for case in cases:
        before = snapshot(host.state_root)
        with pytest.raises(PromptNotPrepared) as ei:
            pm.record_decline(case, decided_by="op")
        assert "the prompt was not prepared by this manager" in str(ei.value)
        assert snapshot(host.state_root) == before
    other.close()
    # the real prompt still works
    pm.record_decline(q, decided_by="op")


# =========================================================================== #
# Test 6 [CXC-PC-V02-T-RD-STALE-REFUSED]
# =========================================================================== #
def test_CXC_PC_V02_T_RD_STALE_REFUSED(tmp_path):
    PromptStale, PromptNotPrepared = cp().PromptStale, cp().PromptNotPrepared
    from compose_plugins.errors import PluginContractRefusal
    assert issubclass(PromptStale, PluginContractRefusal)
    changed = f"the install state of {D} changed since the prompt was prepared"

    def refused(pm, q, exc_type, reason):
        before = snapshot(pm.host.state_root)
        with pytest.raises(exc_type) as ei:
            pm.record_decline(q, decided_by="op")
        assert reason in str(ei.value), str(ei.value)
        assert snapshot(pm.host.state_root) == before
    # (a) install prompt, then installed by another call
    host, pm = make(tmp_path / "a")
    src = fixture_copy(tmp_path, "v1")
    q = pm.prepare_install(src)
    install(pm, src)
    refused(pm, q, PromptStale, changed)
    # (b) upgrade prompt, then disabled
    pm.enable(D)
    q = pm.prepare_upgrade(fixture_copy(tmp_path, "v2"))
    pm.disable(D)
    refused(pm, q, PromptStale, changed)
    # (c) upgrade prompt, then uninstalled
    pm.enable(D)
    q = pm.prepare_upgrade(fixture_copy(tmp_path, "v2", name="v2c"))
    pm.uninstall(D)
    refused(pm, q, PromptStale, changed)
    # (d) single use
    host, pm = make(tmp_path / "d")
    q = pm.prepare_install(src)
    pm.record_decline(q, decided_by="op")
    refused(pm, q, PromptStale, "the prompt was already declined")
    # (e) 1025 prompts: the first was evicted
    host, pm = make(tmp_path / "e")
    first = pm.prepare_install(src)
    for _ in range(1024):
        pm.prepare_install(src)
    refused(pm, first, PromptNotPrepared, "the prompt was not prepared by this manager")
