#!/usr/bin/env python3
"""Host tests for the daemon HEARTBEAT/CANARY watcher (compose_projects/intelligence/publisher/daemon_watch.py).

Deterministic: MOCK flocks (real temp lock files + real fcntl holders), MOCK pids
(scripted probe results), MOCK clock (epoch floats passed to the tracker). No
broker, no network, no touch of the real defect-graph.lts.

Acceptance mapping (DSD D5, [DEC-CAROUSEL-PLATFORM-CALLS-SETTLED]):
  * a daemon whose flock goes stale > 30s -> canary raised .......... test_stale_flock_raises_canary
  * a fresh flock -> no canary ..................................... test_fresh_flock_no_canary
  * two live pids for one id -> duplicate alert .................... test_duplicate_flagged
  * a recovered daemon clears ..................................... test_recovery_clears_canary
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "compose_projects" / "intelligence" / "publisher"))
sys.path.insert(0, str(REPO_ROOT / "python"))

import daemon_watch as dw  # noqa: E402
from singleton import singleton_lock_path, _flock_exclusive_nb  # noqa: E402


# --------------------------------------------------------------------------- #
# helpers: a real held / released / absent flock on disk (the "mock flock")
# --------------------------------------------------------------------------- #
class _HeldFlock:
    """A real LOCK_EX holder on a temp lock file — a live daemon's flock. Holds via
    the ONE shared flock primitive (`singleton._flock_exclusive_nb`), NOT a raw
    `fcntl.flock` here, so the single-call-site invariant holds even in test code."""

    def __init__(self, path, pid=None):
        self.path = path
        with open(path, "w") as w:
            w.write(f"{pid if pid is not None else os.getpid()}\n")
        self.fh = open(path, "r+")
        assert _flock_exclusive_nb(self.fh), "the mock holder must win the flock"

    def release(self):
        self.fh.close()                    # closing frees the flock (no LOCK_UN call)


# --------------------------------------------------------------------------- #
# probe_flock against real held / released / absent locks
# --------------------------------------------------------------------------- #
def test_probe_flock_held_is_up(tmp_path):
    path = str(tmp_path / "composeio-x.lock")
    holder = _HeldFlock(path)
    try:
        held, pid = dw.probe_flock(path)
        assert held is True
        assert pid == os.getpid()
    finally:
        holder.release()


def test_probe_flock_released_is_down(tmp_path):
    path = str(tmp_path / "composeio-x.lock")
    holder = _HeldFlock(path)
    holder.release()                       # process died -> flock released, file lingers
    held, pid = dw.probe_flock(path)
    assert held is False                   # stale file, nobody holds it -> down


def test_probe_flock_absent_is_down(tmp_path):
    held, pid = dw.probe_flock(str(tmp_path / "nope.lock"))
    assert held is False and pid is None


# --------------------------------------------------------------------------- #
# real_probe unions flock + process-scan into up/down/duplicate
# --------------------------------------------------------------------------- #
def test_real_probe_inprocess_flock_only_is_up(tmp_path, monkeypatch):
    """The in-process console case: flock held, NO separate process -> up."""
    lock_dir = str(tmp_path)
    path = singleton_lock_path("inproc", lock_dir)
    holder = _HeldFlock(path)
    monkeypatch.setattr(dw, "scan_processes", lambda pattern, excl: [])
    try:
        spec = dw.DaemonSpec("inproc", "inproc", match="no-such-proc")
        pr = dw.real_probe(spec, "architect", lock_dir=lock_dir, do_health=False)
        assert pr["status"] == "up"
        assert pr["flock_pid"] == os.getpid()
        assert pr["process_pids"] == []
    finally:
        holder.release()


def test_real_probe_flockless_duplicate(monkeypatch):
    """decision_ops.mcp_server has NO flock; 3 live processes -> duplicate."""
    monkeypatch.setattr(dw, "scan_processes", lambda pattern, excl: [31853, 86837, 97067])
    spec = dw.DaemonSpec("decision_ops_mcp", None, "decision_ops.mcp_server")
    pr = dw.real_probe(spec, "architect", do_health=False)
    assert pr["status"] == "duplicate"
    assert sorted(pr["pids"]) == [31853, 86837, 97067]


def test_real_probe_standalone_no_double_count(tmp_path, monkeypatch):
    """A standalone daemon: flock pid == the process-scan pid -> union dedups -> up
    (NOT a false duplicate)."""
    lock_dir = str(tmp_path)
    path = singleton_lock_path("standalone", lock_dir)
    holder = _HeldFlock(path)                      # flock records THIS pid
    monkeypatch.setattr(dw, "scan_processes", lambda pattern, excl: [os.getpid()])
    try:
        spec = dw.DaemonSpec("standalone", "standalone", match="standalone")
        pr = dw.real_probe(spec, "architect", lock_dir=lock_dir, do_health=False)
        assert pr["status"] == "up"               # deduped, not "duplicate"
    finally:
        holder.release()


def test_real_probe_absent_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(dw, "scan_processes", lambda pattern, excl: [])
    spec = dw.DaemonSpec("gone", "gone", match="gone")
    pr = dw.real_probe(spec, "architect", lock_dir=str(tmp_path), do_health=False)
    assert pr["status"] == "down"


# --------------------------------------------------------------------------- #
# the state machine (mock clock) — the four acceptance cases
# --------------------------------------------------------------------------- #
def test_stale_flock_raises_canary():
    tr = dw.LivenessTracker(threshold_s=30.0)
    assert tr.observe("d", "down", now=0.0) == []          # first down: baseline
    assert tr.observe("d", "down", now=30.0) == []         # exactly 30s: not yet (strict >)
    assert tr.observe("d", "down", now=30.5) == [("canary_raised", "d")]
    assert tr.observe("d", "down", now=60.0) == []         # no repeat


def test_fresh_flock_no_canary():
    tr = dw.LivenessTracker(threshold_s=30.0)
    events = []
    for t in range(0, 200, 5):
        events += tr.observe("fresh", "up", now=float(t))
    assert events == []
    assert tr.state["fresh"].canary_open is False


def test_duplicate_flagged():
    tr = dw.LivenessTracker(threshold_s=30.0)
    assert tr.observe("mcp", "duplicate", now=0.0) == [("duplicate_raised", "mcp")]
    assert tr.observe("mcp", "duplicate", now=5.0) == []   # no repeat
    assert tr.observe("mcp", "up", now=10.0) == [("duplicate_cleared", "mcp")]


def test_recovery_clears_canary():
    tr = dw.LivenessTracker(threshold_s=30.0)
    tr.observe("d", "down", now=0.0)
    tr.observe("d", "down", now=40.0)                      # canary raised
    assert tr.state["d"].canary_open is True
    assert tr.observe("d", "up", now=45.0) == [("recovered", "d")]
    assert tr.state["d"].canary_open is False
    assert tr.state["d"].last_up == 45.0


def test_duplicate_then_down_clears_then_canaries():
    tr = dw.LivenessTracker(threshold_s=30.0)
    tr.observe("dd", "duplicate", now=0.0)
    assert tr.observe("dd", "down", now=1.0) == [("duplicate_cleared", "dd")]
    assert tr.observe("dd", "down", now=40.0) == [("canary_raised", "dd")]


# --------------------------------------------------------------------------- #
# build_liveness_map end-to-end: probe -> tracker -> sink -> payload
# --------------------------------------------------------------------------- #
def test_build_map_dispatches_to_sink_and_shapes_payload():
    reg = [dw.DaemonSpec("alpha", "alpha"),
           dw.DaemonSpec("beta", "beta"),
           dw.DaemonSpec("mcp", None, "mcp")]
    script = {"alpha": "up", "beta": "down", "mcp": "duplicate"}

    def probe(spec):
        st = script[spec.id]
        pids = {"up": [111], "down": [], "duplicate": [201, 202, 203]}[st]
        return {"status": st, "pids": pids, "flock_pid": pids[0] if pids else None,
                "flock_held": st != "down", "process_pids": pids, "health_ok": None}

    tr = dw.LivenessTracker(30.0)
    sink = dw.RecordingSink()

    payload, events = dw.build_liveness_map(reg, probe, tr, sink, now=1000.0,
                                            detail_of=dw._detail_of)
    assert payload["summary"] == {"up": 1, "down": 1, "duplicate": 1}
    assert payload["duplicates"] == ["mcp"]
    assert payload["canaries"] == []                          # beta down < 30s
    assert ("duplicate_raised", "mcp") in events
    assert any(k == "duplicate_raised" and d == "mcp" for k, d, _ in sink.events)
    # the sink got a non-empty detail (pids) for the mcp duplicate
    dup_detail = [detail for k, d, detail in sink.events if d == "mcp"][0]
    assert "201" in dup_detail

    # advance past threshold -> beta canaries via the same builder + sink
    payload2, events2 = dw.build_liveness_map(reg, probe, tr, sink, now=1031.0,
                                              detail_of=dw._detail_of)
    assert payload2["canaries"] == ["beta"]
    assert any(k == "canary_raised" and d == "beta" for k, d, _ in sink.events)


def test_defect_registry_sink_stable_symptom(tmp_path):
    """The canary REUSES the defect-registry: a stale daemon becomes a defect via
    defect_ops.report on a STABLE symptom (dedups on recurrence), resolve on
    recovery. Runs against a TEMP graph copy — never the real corpus."""
    defect_ops = pytest.importorskip("composeio.defect_ops")
    graph = tmp_path / "defect-graph.lts"
    graph.write_text("---\ndomain: defect\n---\n# Fixture [SYSTEM:DEFECT-CORPUS]\n")

    sink = dw.DefectRegistrySink(str(graph), threshold_s=30.0)
    sink.canary_raised("memory_monitor", detail="status=down")
    sink.canary_raised("memory_monitor", detail="status=down")   # recurrence -> dedup, no 2nd node

    defects = defect_ops.list_defects(str(graph))
    daemon_defects = [d for d in defects if "memory_monitor" in (d.get("area") or "")
                      or "memory_monitor" in (d.get("symptom") or "")]
    assert len(daemon_defects) == 1, f"stable symptom must dedup, got {daemon_defects}"
    assert daemon_defects[0]["report_count"] == 2                # both reports on one node

    # recovery annotates verified (does not raise)
    sink.recovered("memory_monitor")
    q = defect_ops.query(str(graph), daemon_defects[0]["id"])
    assert q["defect"]["lifecycle"] == "verified"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
