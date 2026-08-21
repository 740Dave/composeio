"""A converted source's `run()` MUST park for the lifetime of the source.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-RUN-BLOCKS], acceptance criterion SOB-C5.
This is a CLASS guard: it parametrizes over EVERY converted source that
`build_console_sources` returns, so a source added or converted later inherits
the check without anyone remembering to add it.

THE HAZARD, STATED PRECISELY, BECAUSE IT DOES NOT LOOK LIKE A BUG
------------------------------------------------------------------
`DataSourceSupervisor._guarded` treats a CLEAN RETURN from a source's `run()` as
"this source yielded its host singleton" and RE-INVOKES it after
`reclaim_poll_s` — the reclaim-on-release behaviour from [SPEC:CXC-IC-SINGLETON]
IC-C27, generalized to the source class after
[DEFECT-IN-PROCESS-SINGLETON-SOURCE-YIELDS]. That is correct and load-bearing for
a source that genuinely lost the flock race during a `/bounce-console`.

It is a trap for a CONVERTED source. Today two sources stay alive by blocking in
`client.loop_forever()`. Once they publish and subscribe through the bus there is
no `loop_forever` to block on, and a source that simply returns does not error —
it enters a reclaim RETRY LOOP that re-subscribes every five seconds forever. The
failure is a slow leak of duplicate routes plus a log line the operator learns to
ignore. There is no crash, no red gauge, and no test that would have caught it,
which is why this file exists.

BOTH LEGS ARE REQUIRED. A source that returns early fails leg 1. A source that
blocks forever and cannot be stopped fails leg 3 — that is the OTHER half of the
same defect: `stop()` would hang the console's shutdown and `/bounce-console`
would leave an orphan holding the flock, which is precisely the yield-race the
reclaim loop exists to survive.

THE SINGLETON IS DELIBERATELY ACQUIRABLE HERE. `acquire_singleton` returning None
is the ONE legitimate early return, and if the live console held the flock every
source would yield and leg 1 would measure the yield path instead of the parking
path — passing for entirely the wrong reason. So each case runs in its own
`COMPOSEIO_LOCK_DIR`.
"""
from __future__ import annotations

import importlib.util
import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[2]
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio import source_bus                              # noqa: E402
from composeio.bus import ComposeIOBus                        # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DS_PATH = _REPO_ROOT / "compose_projects" / "intelligence" / "console" / "data_sources.py"

ALIVE_WINDOW_S = 0.25
JOIN_BUDGET_S = 2.0


def _load_data_sources():
    name = "ds_run_blocks_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _DS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class _RecordingTransport:
    """Transport-shaped and inert. A converted source must be able to publish and
    subscribe without a broker, or this test would be an integration test."""

    def __init__(self):
        self.sent = []
        self.subscribed = []

    def start(self): pass
    def stop(self): pass
    def send(self, message): self.sent.append(message)
    def subscribe(self, namespace, callback): self.subscribed.append(namespace)
    def subscribe_all(self, callback): pass
    def subscribe_pattern(self, pattern): self.subscribed.append(pattern)

    @property
    def is_connected(self): return True


class _ThreadSafeStderr(io.StringIO):
    """The supervisor and the sources both `sys.stderr.write` from threads."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()

    def write(self, s):                                       # type: ignore[override]
        with self._lock:
            return super().write(s)

    def snapshot(self) -> str:
        with self._lock:
            return self.getvalue()


def _converted_sources(tmp_path):
    """Every source `build_console_sources` returns that took a `stop` kwarg.

    Taking `stop` IS the definition of "converted" for this test's purposes:
    `_accepts` binds it exactly where the signature accepts it. The complementary
    guard — that a source which is NOT converted is on an explicit, shrinking,
    rationale-bearing allow-list — lives in `test_sources_have_no_paho.py`. Neither
    file alone closes the class; together they do.

    A SESSION IS PINNED, and that is not cosmetic. `build_console_sources` omits
    the three TRANSCRIPT-based sources entirely when `session_jsonl` is None, so
    this file ran for two slices without ever measuring `self_publisher`,
    `transcript_publisher` or `deliverable_monitor` — the three whose parking is
    LEAST obvious, because none of them parks on `source_bus.park`: two ride
    `follow_lines`' idle hook and raise to unwind it. A class guard that silently
    excludes the hard cases is the shape of guard this arc exists to remove, so
    the empty transcript is created here and passed in ([CXC-SOB-RUN-BLOCKS]
    "parametrizes over EVERY source `build_console_sources` returns").
    """
    ds = _load_data_sources()
    bus = ComposeIOBus(bus_id="run-blocks-probe",
                       global_transport=_RecordingTransport())
    bus.start(announce_time=False)
    session_jsonl = tmp_path / "session.jsonl"
    session_jsonl.write_text("")          # tailed, never replayed: empty is fine
    sources = ds.build_console_sources(
        session_jsonl=str(session_jsonl),
        intelligence_id="architect",
        mqtt_url="mqtt://127.0.0.1:1883",
        ledger=str(tmp_path / "ledger.lts"),
        session="2026-08-08",
        repo_root=str(_REPO_ROOT),
        bus=bus)
    return ds, [s for s in sources if s.stop is not None]


def _converted_names() -> list[str]:
    """Collection-time parametrization needs the NAMES before any fixture runs."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        _ds, srcs = _converted_sources(Path(d))
        return [s.name for s in srcs]


@pytest.fixture
def isolated_locks(tmp_path, monkeypatch):
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(lock_dir))
    # CWD isolation, and it is not cosmetic. Several sources resolve a default
    # path RELATIVE TO CWD, and at least one WRITES there: `project_validator`'s
    # `load_or_seed_registry` SEEDS `corpora/intelligence-console/projects.lts`
    # when none exists, so running this test from `python/` created a bogus
    # `python/corpora/...` registry inside the repo. A test that mutates the
    # working tree is a test that will eventually be committed by accident.
    # (It also isolates `deliverable_watcher`, whose ledger snapshot scans the
    # ledger's `project-roots: [.]` against CWD, not against --repo-root.)
    workdir = tmp_path / "cwd"
    workdir.mkdir()
    monkeypatch.chdir(workdir)

    # `daemon_watch` is ARMED in production (`--no-registry` was deliberately
    # dropped 2026-08-08), so driving it here FILED A REAL DEFECT into
    # `defects/defect-graph.lts` — and a bogus one: the isolated lock dir means
    # its own two probe instances look like a DUPLICATE. A test that files
    # defects is a test that poisons the registry it is supposed to be
    # protecting, and "a watcher that cries wolf is worse than no watcher" is
    # the exact reasoning that armed it in the first place. Redirect the graph
    # to scratch; the module already exposes the env seam.
    monkeypatch.setenv("DAEMON_WATCH_GRAPH", str(tmp_path / "defect-graph.lts"))

    source_bus.reset_for_tests()
    yield lock_dir
    source_bus.reset_for_tests()


@pytest.mark.parametrize("name", _converted_names())
def test_converted_source_blocks_and_then_stops(name, tmp_path, isolated_locks,
                                                monkeypatch):
    ds, sources = _converted_sources(tmp_path)
    src = next(s for s in sources if s.name == name)

    err = _ThreadSafeStderr()
    monkeypatch.setattr(sys, "stderr", err)

    sup = ds.DataSourceSupervisor([src], reclaim_poll_s=0.05)
    try:
        sup.start()

        # ---- leg 1: still ALIVE after the window ------------------------- #
        time.sleep(ALIVE_WINDOW_S)
        alive = sup.alive_names()
        log = err.snapshot()
        assert alive == [name], (
            f"{name}: run() returned within {ALIVE_WINDOW_S}s. Under "
            f"DataSourceSupervisor that is read as 'yielded my singleton' and "
            f"starts a reclaim RETRY LOOP — no error, no crash, just a slow leak "
            f"of duplicate routes. Park on `stop` via source_bus.park().\n"
            f"--- source log ---\n{log}")
        assert src.error is None, f"{name}: run() raised: {src.error!r}"

        # ---- leg 2: the supervisor did NOT log a reclaim ------------------ #
        # A source that yields and is re-invoked would pass leg 1 by accident:
        # the thread is alive, just alive for the wrong reason.
        #
        # Scoped to THIS source's name, deliberately. A bare
        # `"yielded its singleton" not in log` also matched a LEAKED daemon
        # thread from `test_data_sources_supervisor.py` (its double is literally
        # named "fine") writing into the monkeypatched stderr — a cross-file
        # coupling that made this test fail only in a full-suite run. The
        # narrower assertion is also the more correct one: another source's
        # reclaim says nothing about this one.
        assert f"~ {name} yielded its singleton" not in log, (
            f"{name}: the supervisor logged a RECLAIM inside the alive window — "
            f"the thread is alive because it is being restarted, not because it "
            f"is parked.\n--- source log ---\n{log}")

        # ...and the reason it did not yield is that it HELD the flock, not that
        # it never tried. Otherwise this test would silently stop measuring the
        # thing it claims to measure the day the lock dir stopped being isolated.
        assert "holds the singleton; yielding" not in log, (
            f"{name}: the source YIELDED its singleton, so leg 1 measured the "
            f"yield path, not the parking path.\n--- source log ---\n{log}")

        # ---- leg 2b: the SOB-C3 startup manifest actually landed ---------- #
        # It is emitted to `sys.stderr` resolved AT CALL TIME, so it must appear
        # in a redirected stream too. A `log=sys.stderr` DEFAULT ARGUMENT binds
        # the stream that existed at import time, and the manifest silently
        # vanished from this capture until that was fixed — which is exactly how
        # it would vanish from a console that captures its children's output,
        # taking SOB-C3's only mechanism with it.
        assert "] declared:" in log, (
            f"{name}: no startup manifest line ([CXC-SOB-DECLARE]) reached the "
            f"log. SOB-C3's whole mechanism is that this is readable in the boot "
            f"log.\n--- source log ---\n{log}")
        assert "declared: NOTHING" not in log, (
            f"{name}: published without declaring any namespace")
    finally:
        # ---- leg 3: stop() releases it, promptly ------------------------- #
        t0 = time.time()
        sup.stop(timeout=JOIN_BUDGET_S)
        took = time.time() - t0
        monkeypatch.undo()

    assert sup.alive_names() == [], (
        f"{name}: did not join within {JOIN_BUDGET_S}s of stop() (took "
        f"{took:.2f}s). A source that blocks forever and cannot be stopped is "
        f"the other half of the [CXC-SOB-RUN-BLOCKS] defect: it hangs console "
        f"shutdown and leaves an orphan holding the flock across a bounce.")


def test_at_least_one_source_is_converted():
    """Guards the guard. A parametrize over an EMPTY list is a green test that
    checks nothing, and it would stay green through every future slice."""
    assert _converted_names(), (
        "no converted sources found — either the conversion regressed or "
        "`_accepts`/`DataSource.stop` stopped being wired")


def test_the_singleton_yield_path_is_still_the_one_legitimate_early_return(tmp_path,
                                                                           monkeypatch):
    """[CXC-SOB-RUN-BLOCKS] constraint: `acquire_singleton` returning None MUST
    still return from `run()`, so the supervisor's reclaim loop can do its job.

    Converting a source must not "fix" the early return by parking through the
    yield — that would make the incumbent's flock permanent and turn a survivable
    bounce race into a dark surface until the next restart.
    """
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(lock_dir))
    source_bus.reset_for_tests()

    sys.path.insert(0, str(_REPO_ROOT / "compose_projects" / "intelligence" / "publisher"))
    sys.path.insert(0, str(_REPO_ROOT / "compose_projects" / "intelligence" / "tools"))
    from singleton import acquire_singleton
    import lts_engine_status

    # Hold the flock from this thread; the source must yield to it.
    incumbent = acquire_singleton(lts_engine_status.LABEL)
    assert incumbent is not None, "could not take the flock to simulate an incumbent"
    try:
        bus = ComposeIOBus(bus_id="probe", global_transport=_RecordingTransport())
        bus.start(announce_time=False)
        stop = threading.Event()
        rc = lts_engine_status.main(["--mqtt", "mqtt://127.0.0.1:1883"],
                                    bus=bus, stop=stop)
        assert rc == 0, "a yielding source returns 0, not an error"
        assert not stop.is_set(), (
            "a yielding source must return WITHOUT waiting on stop — otherwise "
            "the supervisor's reclaim loop never re-invokes it")
    finally:
        incumbent.close()
        source_bus.reset_for_tests()
