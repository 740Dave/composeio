#!/usr/bin/env python3
"""test_singleton_hardening — [SPEC:CXC-IC-SINGLETON] / IC-C17.

Proves singleton-ness is STRUCTURAL across the console daemons:
  (a) a second acquire_singleton(name) is denied while the first is held, and
      releases on close;
  (b) exactly ONE fcntl.flock( call site exists in the repo (the shared helper) —
      the grep-provable invariant that keeps the fix from fork-diverging;
  (c) a representative sample of daemons' run() YIELD (return 0, no broker
      connect) when the host lock is pre-held.

Run: python3 -m pytest compose_projects/intelligence/publisher/test_singleton_hardening.py -q
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))          # compose_projects/intelligence/publisher
INTEL = os.path.dirname(HERE)                               # intelligence
REPO = os.path.dirname(os.path.dirname(INTEL))                      # repo root (compose_projects is in between)
TOOLS = os.path.join(INTEL, "tools")
for _d in (HERE, TOOLS, os.path.join(REPO, "python")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from singleton import acquire_singleton, singleton_lock_path  # noqa: E402


# ── (a) contention + release ────────────────────────────────────────────────
def test_second_acquire_denied_while_held(tmp_path):
    d = str(tmp_path)
    first = acquire_singleton("demo", lock_dir=d)
    assert first is not None, "the first acquirer must win the lock"
    assert acquire_singleton("demo", lock_dir=d) is None, "a second must be denied"
    assert acquire_singleton("demo", lock_dir=d) is None, "and again (still held)"
    first.close()                                           # release
    again = acquire_singleton("demo", lock_dir=d)
    assert again is not None, "after release the lock is re-acquirable"
    again.close()


def test_distinct_names_do_not_contend(tmp_path):
    d = str(tmp_path)
    a = acquire_singleton("role-a", lock_dir=d)
    b = acquire_singleton("role-b", lock_dir=d)
    assert a is not None and b is not None, "distinct roles never contend"
    assert singleton_lock_path("role-a", d) != singleton_lock_path("role-b", d)
    a.close()
    b.close()


def test_pid_written_into_lock(tmp_path):
    d = str(tmp_path)
    fh = acquire_singleton("pidrole", lock_dir=d)
    assert fh is not None
    with open(singleton_lock_path("pidrole", d)) as f:
        assert f.read().strip() == str(os.getpid())
    fh.close()


def test_lock_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    assert singleton_lock_path("envrole") == os.path.join(
        str(tmp_path), "composeio-envrole.lock")


# ── (b) exactly ONE flock call site ─────────────────────────────────────────
def test_exactly_one_flock_call_site():
    """The whole point: the flock lives in ONE shared helper. If a daemon
    re-implements it, this fails — catching the fork-divergence before it ships."""
    out = subprocess.run(
        ["grep", "-rn", "--include=*.py",
         "--exclude-dir=node_modules", "--exclude-dir=.claude",
         "fcntl.flock(", REPO],
        capture_output=True, text=True)
    # Exclude vendored node_modules, EPHEMERAL agent worktrees (.claude/worktrees/
    # — each a full repo checkout that would show its own copy of the helper), and
    # THIS test file — an invariant test must name the token it guards, and those
    # string-literal mentions are not calls.
    this_file = os.path.abspath(__file__)
    # ONE NAMED EXCLUSION, not an allow-list: python/compose_plugins/ is the
    # independent plug-in contract library (spec 95). [CXC-PC-LIBRARY] allows it
    # NO dependency beyond composebus, so it cannot import this helper, and its
    # consent-store lock is a BLOCKING LOCK_EX on its own file, not a daemon
    # singleton. This invariant's subject is the intelligence/console singleton.
    compose_plugins_lib = os.path.join(REPO, "python", "compose_plugins") + os.sep
    lines = [ln for ln in out.stdout.splitlines()
             if "/node_modules/" not in ln
             and "/.claude/" not in ln
             and not ln.startswith(this_file + ":")
             and not ln.startswith(compose_plugins_lib)]
    assert len(lines) == 1, (
        "expected exactly ONE fcntl.flock( call site (the shared helper); got:\n"
        + "\n".join(lines))
    assert lines[0].split(":", 1)[0].replace("\\", "/").endswith(
        "compose_projects/intelligence/publisher/singleton.py"), lines[0]


# ── (c) daemons YIELD when the lock is pre-held ─────────────────────────────
# A representative sample of console-global daemons. Each run() acquires the
# host lock at the top and, finding it held, returns 0 WITHOUT connecting to a
# broker. Args are dummies — run() yields before it uses them.
pytest.importorskip("paho.mqtt.client")

_YIELDERS = [
    ("deliverable_publisher", "deliverable-publisher",
     lambda m: m.run("x.lts", "mqtt://127.0.0.1:1", None, "o", 1e9)),
    ("deliverable_opener", "deliverable-opener",
     lambda m: m.run("mqtt://127.0.0.1:1", ".", "o")),
    ("meta_publisher", "meta-publisher",
     lambda m: m.run("m.json", "mqtt://127.0.0.1:1", 1e9)),
    ("config_persister", "config-persister",
     lambda m: m.run("m.json", "mqtt://127.0.0.1:1", "architect", 1e9)),
    ("away_watch_publisher", "away-watch-publisher",
     lambda m: m.run("mqtt://127.0.0.1:1", 1e9, False)),
    ("doc_viewer_launcher", "doc-viewer-launcher",
     lambda m: m.run("mqtt://127.0.0.1:1")),
]


@pytest.mark.parametrize("modname,key,invoke", _YIELDERS,
                         ids=[y[0] for y in _YIELDERS])
def test_run_yields_when_lock_preheld(modname, key, invoke, tmp_path, monkeypatch):
    # Point every daemon's lock at our temp dir, then PRE-HOLD the lock so run()
    # must lose the race and yield.
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    held = acquire_singleton(key, lock_dir=str(tmp_path))
    assert held is not None, "the test must hold the lock before invoking run()"
    mod = importlib.import_module(modname)
    rc = invoke(mod)
    assert rc == 0, f"{modname}.run() must YIELD (return 0) when the lock is held"
    held.close()


# ── (c2) RECLAIM-ON-RELEASE: a yielded PUBLISHER is bounce-safe (IC-C27) ─────
def test_reclaims_when_incumbent_releases(tmp_path):
    """[CXC-IC-SINGLETON] publisher bounce-safety (IC-C27): a console publisher
    that LOSES the flock at boot (the bounce race) must NOT go dark forever — it
    must RECLAIM the moment the incumbent releases. Simulates: incumbent holds →
    a reclaimer yields (does NOT relay while the incumbent lives) → incumbent
    dies (flock frees) → the reclaimer ACQUIRES. This is the exact
    DEFECT-CHANNEL-OFFLINE-CONSOLE-CHANNEL-BRIDGE sequence; a one-shot
    acquire_singleton yields forever and this stays dark (RED before the fix)."""
    import threading
    import time
    from singleton import acquire_singleton_reclaiming
    d = str(tmp_path)
    held = acquire_singleton("reclaim-role", lock_dir=d)
    assert held is not None, "incumbent must own the lock first"
    # a plain one-shot acquire yields (the pre-fix behavior that went dark) —
    assert acquire_singleton("reclaim-role", lock_dir=d) is None

    result = {}
    yielded = threading.Event()

    def reclaim():
        result["fh"] = acquire_singleton_reclaiming(
            "reclaim-role", lock_dir=d, poll_s=0.02,
            on_yield=yielded.set)

    t = threading.Thread(target=reclaim, daemon=True)
    t.start()
    assert yielded.wait(2.0), "the reclaimer must YIELD (wait), not relay, while held"
    time.sleep(0.1)
    assert "fh" not in result, "must NOT acquire while the incumbent still holds it"
    held.close()                                    # incumbent 'dies' — flock frees
    t.join(timeout=2.0)
    assert result.get("fh") is not None, \
        "successor must RECLAIM once the incumbent releases — not stay dark"
    result["fh"].close()


def test_reclaim_stop_signal_returns_none(tmp_path):
    """should_stop breaks the wait (a stop signal / clean shutdown) → None,
    never a busy-spin forever."""
    import threading
    from singleton import acquire_singleton_reclaiming
    d = str(tmp_path)
    held = acquire_singleton("reclaim-stop", lock_dir=d)
    assert held is not None
    stop = threading.Event()
    out = {}

    def reclaim():
        out["r"] = acquire_singleton_reclaiming(
            "reclaim-stop", lock_dir=d, poll_s=0.02, should_stop=stop.is_set)

    t = threading.Thread(target=reclaim, daemon=True)
    t.start()
    stop.set()
    t.join(timeout=2.0)
    assert "r" in out and out["r"] is None, "a stop signal must end the wait with None"
    held.close()


def test_channel_bridge_run_reclaims_not_one_shot_dark(tmp_path, monkeypatch):
    """The reported defect at the SITE: channel_bridge.run() must NOT one-shot
    yield-and-return-dark when the relay lock is pre-held (a bounced console's
    relay racing the dying predecessor). It must ENTER the reclaim wait and only
    return when told to stop. Pre-fix run(id, mqtt) returned 0 IMMEDIATELY (dark
    forever) and took no should_stop — so this is RED before / GREEN after."""
    import threading
    import channel_bridge
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    held = acquire_singleton("channel-bridge-architect", lock_dir=str(tmp_path))
    assert held is not None, "pre-hold the relay lock so run() must yield"

    rc = {}
    stop = threading.Event()

    def go():
        # mqtt url is a dud: run() must WAIT on the held lock and NEVER reach connect.
        rc["v"] = channel_bridge.run("architect", "mqtt://127.0.0.1:1",
                                     reclaim_poll_s=0.02, should_stop=stop.is_set)

    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout=0.3)
    assert t.is_alive(), \
        "run() must WAIT to reclaim (not one-shot yield-and-return dark)"
    assert "v" not in rc, "run() must not have returned while the lock is held"
    stop.set()                                      # clean-stop the reclaim wait
    t.join(timeout=2.0)
    assert rc.get("v") == 0, "run() returns 0 once the stop signal ends the wait"
    held.close()


# ── (d) SUPERSEDE variant: the launcher REAPS its predecessor (D5) ───────────
def test_supersede_reaps_predecessor_and_acquires(tmp_path):
    """[CXC-IC-SINGLETON] launcher SUPERSEDE: when an instance already holds the
    role lock, acquire_singleton_superseding SIGTERMs it and takes over — so a
    resume/restart never yields-and-fails, and never leaves two launchers. (A
    failed acquire must NOT truncate the lock file, or SUPERSEDE has no pid to
    reap — regression-guarded here.)"""
    import time
    from singleton import acquire_singleton_superseding
    d = str(tmp_path)
    child = subprocess.Popen([sys.executable, "-c",
        "import sys;sys.path.insert(0,%r);"
        "from singleton import acquire_singleton;import time;"
        "fh=acquire_singleton('test-launcher', %r);"
        "time.sleep(30) if fh else sys.exit(3)" % (HERE, d)])
    try:
        for _ in range(60):                        # wait until the child holds it
            probe = acquire_singleton('test-launcher', d)
            if probe is None:
                break                              # child owns the lock
            probe.close()                          # release so the child can grab it
            time.sleep(0.05)
        else:
            pytest.fail("child never acquired the lock")
        fh = acquire_singleton_superseding('test-launcher', d, grace_s=3.0)
        assert fh is not None, "SUPERSEDE must acquire after reaping the holder"
        time.sleep(0.2)
        assert child.poll() is not None, "SUPERSEDE must have reaped the predecessor"
    finally:
        if child.poll() is None:
            child.kill()
