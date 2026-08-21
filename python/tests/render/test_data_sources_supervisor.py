"""Tests for the in-process console data-source supervisor
(`compose_projects/intelligence/console/data_sources.py`).

[DEC-INPROCESS-SOURCES] The console runs its session-owned data-source daemons on
daemon threads it owns, pinned to ITS session, instead of standalone subprocesses.
These tests prove: N threads start, each run-loop is invoked, stop() joins cleanly,
the session is taken from the EXPLICIT arg (never ls -t), the disable flag works,
a crash is isolated, and the built source set faithfully mirrors
start-console-daemons.sh (same modules, same argv => same wire). No broker, no
network — MQTT is never touched (run-loops are replaced by fakes; the real build
only BINDS argv, it does not run it).
"""
from __future__ import annotations

import importlib.util
import io
import os
import threading
import time
from pathlib import Path

import pytest

# Load the module by file path — the render engine package does not depend on the
# intelligence tree, exactly as runtime.main() loads it in production.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DS_PATH = _REPO_ROOT / "compose_projects" / "intelligence" / "console" / "data_sources.py"


@pytest.fixture(scope="module")
def ds():
    import sys
    name = "ds_under_test"
    spec = importlib.util.spec_from_file_location(name, _DS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # @dataclass resolves via sys.modules
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _hermetic_session_env(ds, monkeypatch, tmp_path_factory):
    """Isolate EVERY test from the ambient session env, deterministically.

    The harness this suite runs in exports the DEV session's pins —
    COMPOSEIO_SESSION_JSONL / JSONL / CLAUDE_CODE_SESSION_ID — into the real
    environment. resolve_session_jsonl reads all three (leg 1 path-pins, leg 2 the
    id glob), so an uncleared var makes resolution return the LIVE transcript and the
    pin/refuse/id legs under test never run — tests that pass-or-fail on whether an
    ambient session happens to be present (the phantom-red leak).

    We make the suite hermetic BY CONSTRUCTION and in BOTH ambient states:
      1. POLLUTE — set every var in ds._ALL_SESSION_ENV to a sentinel, so the env is
         identical whether or not a real session is present (kills the flakiness).
      2. CLEAR — delete every var in ds._ALL_SESSION_ENV. Driving both loops off the
         resolver's OWN single source of truth means a var added to a resolution leg
         (and thus to _ALL_SESSION_ENV) is auto-neutralized here — no third fork to
         forget. Any var the resolver reads but is NOT cleared would survive the
         pollute step and break the id-leg tests loudly.
    """
    leak = tmp_path_factory.mktemp("ambient") / "LEAK-dev-session.jsonl"
    leak.write_text('{"type":"assistant"}\n')
    for name in ds._ALL_SESSION_ENV:
        monkeypatch.setenv(
            name, str(leak) if name in ds._SESSION_JSONL_ENV
            else "ffffffff-dead-beef-dead-ffffffffffff")
    for name in ds._ALL_SESSION_ENV:
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- #
# Supervisor lifecycle
# --------------------------------------------------------------------------- #
def test_starts_n_threads_and_invokes_each_runloop(ds):
    started = {}
    stop_evt = threading.Event()

    def make(name):
        def _run():
            started[name] = True
            stop_evt.wait(5.0)
        return ds.DataSource(name=name, run=_run)

    sup = ds.DataSourceSupervisor([make("a"), make("b"), make("c")])
    sup.start()
    time.sleep(0.15)
    assert sorted(started) == ["a", "b", "c"], "each run-loop invoked"
    assert len(sup.alive_names()) == 3
    assert sup.started is True
    stop_evt.set()
    sup.stop(timeout=2.0)
    assert sup.alive_names() == [], "stop() joined all threads cleanly"


def test_threads_are_daemon_and_named(ds):
    stop_evt = threading.Event()
    src = ds.DataSource(name="x", run=lambda: stop_evt.wait(5.0))
    sup = ds.DataSourceSupervisor([src])
    sup.start()
    time.sleep(0.05)
    assert src.thread.daemon is True, "must not block process exit / render loop"
    assert src.thread.name == "ds:x"
    stop_evt.set()
    sup.stop()


def test_double_start_is_idempotent(ds):
    calls = []
    stop_evt = threading.Event()
    src = ds.DataSource(name="once", run=lambda: (calls.append(1),
                                                  stop_evt.wait(5.0)))
    sup = ds.DataSourceSupervisor([src])
    sup.start()
    sup.start()
    time.sleep(0.1)
    assert len(calls) == 1, "second start() is a no-op"
    stop_evt.set()
    sup.stop()


def test_crash_is_isolated(ds):
    ok = {}
    done = threading.Event()

    def boom():
        raise RuntimeError("nope")

    def fine():
        ok.setdefault("fine", True)
        done.wait(5.0)                       # block so the supervisor doesn't restart-loop it

    sup = ds.DataSourceSupervisor([
        ds.DataSource("boom", boom),
        ds.DataSource("fine", fine),
    ])
    sup.start()
    time.sleep(0.15)
    assert ok.get("fine") is True, "sibling ran despite crash"
    assert sup.sources[0].error is not None, "crash captured, not raised"
    done.set()
    sup.stop()


def test_crash_dies_alone_no_restart(ds):
    """A source that RAISES must NOT be restart-looped — a crash dies alone (the
    isolation boundary), distinct from a clean RETURN which reclaims. Guards the
    line the reclaim seam must not cross: a persistently-crashing source in a tight
    restart loop would spin the CPU."""
    calls = []
    sup = ds.DataSourceSupervisor(
        [ds.DataSource("crasher", lambda: (calls.append(1),
                                           (_ for _ in ()).throw(RuntimeError("x"))))],
        reclaim_poll_s=0.01)
    sup.start()
    time.sleep(0.2)
    assert calls == [1], "a crashing source runs ONCE and dies alone, never restarts"
    sup.stop()


# --------------------------------------------------------------------------- #
# RECLAIM-ON-RELEASE at the SOURCE class ([SPEC:CXC-IC-SINGLETON] IC-C27) — the seam
# --------------------------------------------------------------------------- #
def test_supervisor_reclaims_one_shot_yielding_source(ds, tmp_path, monkeypatch):
    """The GENERALIZED defect (DEFECT-IN-PROCESS-SINGLETON-SOURCE-YIELDS): an
    in-process source that ONE-SHOT yields its host singleton — it lost the boot race
    to a dying predecessor's still-held flock, exactly the `/bounce-console` race that
    darkened `self/decisions` after the channel-bridge fix — must NOT stay dark. The
    shared supervisor RECLAIMS it (re-invokes run()) the moment the incumbent releases.

    RED before the fix: the old `_guarded` ran run() ONCE; a yielded source returned 0
    and was never re-invoked, so `published` never fires and this times out.
    GREEN after: supervise+reclaim re-runs it, it acquires after the incumbent dies,
    and publishes. Uses the REAL `acquire_singleton` + the REAL supervisor — the seam,
    not a channel-specific path, so ANY current-or-future source is covered."""
    import sys as _sys
    pub = str(_REPO_ROOT / "compose_projects" / "intelligence" / "publisher")
    if pub not in _sys.path:
        _sys.path.insert(0, pub)
    from singleton import acquire_singleton
    monkeypatch.setenv("COMPOSEIO_LOCK_DIR", str(tmp_path))
    key = "reclaim-source-demo"

    # the INCUMBENT (the dying old console's thread) still owns the flock at boot
    incumbent = acquire_singleton(key, lock_dir=str(tmp_path))
    assert incumbent is not None

    published = threading.Event()
    stop_evt = threading.Event()

    def source_run():
        # the REAL source pattern: one-shot acquire; if held -> YIELD (return 0)
        lock = acquire_singleton(key, lock_dir=str(tmp_path))
        if lock is None:
            return 0                          # pre-fix: went dark HERE, forever
        published.set()                       # acquired -> we "publish"
        stop_evt.wait(5.0)
        return 0

    sup = ds.DataSourceSupervisor([ds.DataSource("demo", source_run)],
                                  reclaim_poll_s=0.02)
    sup.start()
    try:
        assert not published.wait(0.3), \
            "must NOT publish while the incumbent holds the singleton"
        incumbent.close()                     # predecessor 'dies' -> flock frees
        assert published.wait(2.0), \
            "source must RECLAIM + publish once the incumbent releases — not stay dark"
    finally:
        stop_evt.set()
        sup.stop()


# --------------------------------------------------------------------------- #
# Session pinning — the real fix for the wrong-session bug
# --------------------------------------------------------------------------- #
def test_session_pin_prefers_explicit_arg(ds, tmp_path):
    s = tmp_path / "mysession.jsonl"
    s.write_text("{}")
    buf = io.StringIO()
    assert ds.resolve_session_jsonl(str(s), log=buf) == str(s)
    assert "PINNED" in buf.getvalue()


def test_session_pin_env_over_lst(ds, tmp_path, monkeypatch):
    s = tmp_path / "envsession.jsonl"
    s.write_text("{}")
    monkeypatch.setenv("COMPOSEIO_SESSION_JSONL", str(s))
    buf = io.StringIO()
    # Even with no explicit arg, the ENV pin wins — never ls -t.
    assert ds.resolve_session_jsonl(None, log=buf) == str(s)


def test_session_missing_pin_is_loud_not_silent(ds, tmp_path):
    buf = io.StringIO()
    ds.resolve_session_jsonl(str(tmp_path / "nope.jsonl"), log=buf)
    assert "not found" in buf.getvalue(), "a bad pin must be warned, not silent"


# --------------------------------------------------------------------------- #
# CLAUDE_CODE_SESSION_ID self-resolution — the durable pin-by-construction leg
# ([CXC-IC-SELF-IDENTITY], [DEC-TRANSCRIPT-SOURCES-BREAK-AT-HANDOVER-SEAM]).
# This leg is what makes an UNPINNED console launch (manual bounce / /bounce-console)
# resolve ITS OWN transcript deterministically instead of going dark-by-refusal when
# >1 transcript coexist — the defect. It must NEVER re-introduce ls -t newest-wins.
# --------------------------------------------------------------------------- #
def _make_projects_root(tmp_path, own_id, *, sibling_id=None):
    """A ~/.claude/projects-shaped tree: own transcript in one slug, an optional
    sibling in ANOTHER slug (so a bare newest-wins/listdir would be ambiguous)."""
    root = tmp_path / "projects"
    own_slug = root / "-Users-x-proj"
    own_slug.mkdir(parents=True)
    own = own_slug / f"{own_id}.jsonl"
    own.write_text('{"type":"assistant"}\n')
    if sibling_id:
        sib_slug = root / "-Users-x-other"
        sib_slug.mkdir(parents=True)
        (sib_slug / f"{sibling_id}.jsonl").write_text('{"type":"assistant"}\n')
    return root, own


def test_session_resolves_by_claude_code_session_id_amid_sibling(ds, tmp_path,
                                                                 monkeypatch):
    """FAILS before the fix (unset pin + >1 transcript -> None). With the id leg the
    console resolves its OWN transcript regardless of the project-dir slug, even with
    a concurrent sibling present — deterministic, not newest-wins."""
    own_id = "aaaaaaaa-1111-1111-1111-111111111111"
    root, own = _make_projects_root(tmp_path, own_id,
                                    sibling_id="bbbbbbbb-2222-2222-2222-222222222222")
    monkeypatch.setattr(ds, "_PROJECTS_ROOT", str(root))
    # also make the flat refuse-scan dir ambiguous so the ONLY thing that can resolve
    # a single transcript is the id leg (not the single-unambiguous fallback).
    monkeypatch.setattr(ds, "_PROJ_DIR", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", own_id)
    buf = io.StringIO()
    assert ds.resolve_session_jsonl(None, log=buf) == str(own)
    assert "CLAUDE_CODE_SESSION_ID" in buf.getvalue()


def test_session_id_leg_never_ls_t_when_unmatched(ds, tmp_path, monkeypatch):
    """An id that matches NO transcript, with >1 coexisting, must still REFUSE — the
    id leg falls through to the loud refusal, never an ls -t newest-wins guess."""
    root, _ = _make_projects_root(tmp_path, "aaaaaaaa-1111-1111-1111-111111111111",
                                  sibling_id="bbbbbbbb-2222-2222-2222-222222222222")
    monkeypatch.setattr(ds, "_PROJECTS_ROOT", str(root))
    # the flat refuse-scan dir holds >1 transcript -> ambiguous -> must refuse
    flat = tmp_path / "flat"
    flat.mkdir()
    (flat / "cccccccc-3333-3333-3333-333333333333.jsonl").write_text("{}")
    (flat / "dddddddd-4444-4444-4444-444444444444.jsonl").write_text("{}")
    monkeypatch.setattr(ds, "_PROJ_DIR", str(flat))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID",
                       "99999999-9999-9999-9999-999999999999")  # matches nothing
    buf = io.StringIO()
    assert ds.resolve_session_jsonl(None, log=buf) is None
    assert "REFUSING" in buf.getvalue()


def test_explicit_pin_still_wins_over_session_id(ds, tmp_path, monkeypatch):
    """The id leg must not shadow an explicit pin — order is pin -> id -> refuse."""
    own_id = "aaaaaaaa-1111-1111-1111-111111111111"
    root, own = _make_projects_root(tmp_path, own_id)
    monkeypatch.setattr(ds, "_PROJECTS_ROOT", str(root))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", own_id)
    explicit = tmp_path / "explicit.jsonl"
    explicit.write_text("{}")
    buf = io.StringIO()
    assert ds.resolve_session_jsonl(str(explicit), log=buf) == str(explicit)


# --------------------------------------------------------------------------- #
# Hermetic-isolation guards — the env-leak defect. resolve_session_jsonl reads a
# SET of ambient env vars (_ALL_SESSION_ENV); the two neutralizers (this suite's
# autouse fixture AND the module's own _selftest) must clear that EXACT set. The
# fork-bug cleared only CLAUDE_CODE_SESSION_ID, so an ambient COMPOSEIO_SESSION_JSONL
# / JSONL leaked the DEV transcript into leg 1 (PINNED) and the tests passed-or-failed
# on whether a live session was present. These guards pin BOTH neutralizers to the
# shared source of truth so neither can drift again.
# --------------------------------------------------------------------------- #
def test_autouse_fixture_clears_every_resolution_env_var(ds):
    """The autouse hermetic fixture must leave NO var the resolver reads in the env —
    if it clears a subset, an ambient pin leaks into leg 1/2. Would have caught the
    fork: the old fixture cleared only the id, leaving COMPOSEIO_SESSION_JSONL to pin
    the live transcript. Deterministic (the fixture pollutes then clears)."""
    for name in ds._ALL_SESSION_ENV:
        assert name not in os.environ, (
            f"ambient session var {name!r} leaked past hermetic isolation")


def test_selftest_is_hermetic_under_polluted_ambient_session_env(ds, tmp_path,
                                                                 monkeypatch):
    """Cross-neutralizer guard: the module's OWN _selftest had the SAME fork-bug
    (cleared only the id). Pollute EVERY resolution env var with a live-looking pin,
    then run _selftest: it must neutralize _ALL_SESSION_ENV so its id-resolution leg
    resolves its fixture, not the leg-1 pollutant. FAILS before the fix (the leaked
    COMPOSEIO_SESSION_JSONL pins in -> selftest returns 1); passes after. Deterministic
    — pollutes itself, independent of any real ambient session."""
    leak = tmp_path / "LEAK-dev-session.jsonl"
    leak.write_text('{"type":"assistant"}\n')
    for name in ds._SESSION_JSONL_ENV:
        monkeypatch.setenv(name, str(leak))
    monkeypatch.setenv(ds._SESSION_ID_ENV, "ffffffff-dead-beef-dead-ffffffffffff")
    assert ds._selftest() == 0, "_selftest must be hermetic w.r.t. the ambient env"


# --------------------------------------------------------------------------- #
# Disable flag
# --------------------------------------------------------------------------- #
def test_disable_flag(ds, monkeypatch):
    monkeypatch.setenv("COMPOSEIO_INPROCESS_SOURCES", "0")
    assert ds.sources_enabled() is False
    assert ds.start_console_data_layer() is None, "disabled -> no supervisor"


def test_enabled_by_default(ds, monkeypatch):
    monkeypatch.delenv("COMPOSEIO_INPROCESS_SOURCES", raising=False)
    assert ds.sources_enabled() is True


# --------------------------------------------------------------------------- #
# The built source set faithfully mirrors start-console-daemons.sh
# --------------------------------------------------------------------------- #
def test_full_set_is_twenty_with_session(ds):
    names = [s.name for s in ds.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://mqtt-broker.local:1883", ledger="l.lts",
        session="2026-07-22")]
    # [CXC-DVL-REGISTER] added decisions_viewer_launcher + lts_engine_status;
    # [SPEC:CXC-DSDV] [CXC-DSDV-REGISTER] added dsd_viewer_launcher (the 17th);
    # [SPEC:CIO-DEFECT-REGISTRY] added defect_metrics_publisher (the 18th).
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED] RETIRED decisions_publisher
    # (the pushed `self/decisions` feed) — the Graph Viewer queries lts-engine
    # directly, so the source-count drops by one (19 -> 18).
    # [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS] (David 2026-08-08) wired
    # meta_publisher (the ONLY publisher of self/meta, whose bootstrap_manifest
    # widget had been rendering a hardcoded seed) and daemon_watch (the liveness
    # watcher, in DRY-RUN `--no-registry` while tentative) — both built + tested
    # + never registered.  18 -> 20.
    # 20 -> 21 ([SPEC:CXC-FLEET] [CXC-FC-S3-ROSTER]): `fleet_roster` is THE one
    # subscriber to `compose_projects/intelligence/+/channel/alive`, publishing `fleet/roster`. Per-pane
    # discovery would be N implementations of one liveness rule, guaranteed to disagree
    # under load, so it is a SINGLE registered source. The bridge also gained an instance
    # suffix (`channel_bridge:<id>`) — there is one PER DECLARED INSTANCE now
    # ([CXC-FC-S1-BRIDGES]), and a bare `channel_bridge` name would be the singleton again.
    # 21 -> 22: `hookup_viewer_launcher`, the CONSUMER half of
    # `send_hookup_for_human_review` (`_app/hookup/open`). Registered in the same
    # change as the endpoint — an intent nobody subscribes is an intent published
    # into the void, and built-tested-never-registered is a known class here.
    # 22 -> 23: `beepout_viewer_launcher`, the CONSUMER half of
    # `send_beepout_for_human_review` (`_app/beepout/open`). Landed in the SAME change
    # that turned that endpoint's publish leg on — the namespace had been DECLARED and
    # deliberately mute precisely because nothing consumed it ([CXC-BPO-LAUNCHER]).
    assert len(names) == 23
    assert "decisions_publisher" not in names
    assert set(names) == {
        "self_publisher", "transcript_publisher", "channel_bridge:architect",
        "fleet_roster",
        "deliverable_publisher", "deliverable_monitor", "deliverable_opener",
        "deliverable_watcher", "memory_monitor", "away_watch_publisher",
        "project_validator", "doc_viewer_launcher", "config_persister",
        "agents_skills_publisher", "memory_publisher",
        "decisions_viewer_launcher", "dsd_viewer_launcher",
        "hookup_viewer_launcher", "beepout_viewer_launcher", "lts_engine_status",
        "defect_metrics_publisher", "meta_publisher", "daemon_watch"}


def test_no_session_omits_transcript_sources(ds):
    names = [s.name for s in ds.build_console_sources(
        session_jsonl=None, intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")]
    # transcript-based sources cannot run without a transcript -> omitted
    assert "self_publisher" not in names
    assert "transcript_publisher" not in names
    assert "deliverable_monitor" not in names
    # session-independent sources still come up
    assert "project_validator" in names
    assert "memory_monitor" in names
    assert "memory_publisher" in names
    # [CXC-DVL-REGISTER] the launcher rail's two sources are session-independent
    assert "decisions_viewer_launcher" in names
    assert "lts_engine_status" in names
    # [SPEC:CXC-DSDV] [CXC-DSDV-REGISTER] the DSD launcher is session-independent
    assert "dsd_viewer_launcher" in names


def test_argv_binding_pins_session_into_transcript_sources(ds):
    """The pinned transcript flows into the argv the module's main() receives —
    proving session pinning reaches the wire, not just resolution."""
    srcs = {s.name: s for s in ds.build_console_sources(
        session_jsonl="/pin/session.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://mqtt-broker.local:1883", ledger="l.lts",
        session="2026-07-22")}
    # run is functools.partial(module.main, argv) — inspect the bound argv
    self_argv = srcs["self_publisher"].run.args[0]
    assert "--jsonl" in self_argv
    assert self_argv[self_argv.index("--jsonl") + 1] == "/pin/session.jsonl"
    tp_argv = srcs["transcript_publisher"].run.args[0]
    assert tp_argv[tp_argv.index("--jsonl") + 1] == "/pin/session.jsonl"
    dm_argv = srcs["deliverable_monitor"].run.args[0]
    assert dm_argv[dm_argv.index("--jsonl") + 1] == "/pin/session.jsonl"


def test_argv_carries_mqtt_and_ledger(ds):
    srcs = {s.name: s for s in ds.build_console_sources(
        session_jsonl="/pin/s.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://broker:1883", ledger="myledger.lts",
        session="2026-07-22")}
    dp_argv = srcs["deliverable_publisher"].run.args[0]
    assert dp_argv[dp_argv.index("--ledger") + 1] == "myledger.lts"
    assert dp_argv[dp_argv.index("--mqtt") + 1] == "mqtt://broker:1883"
    pv_argv = srcs["project_validator"].run.args[0]
    assert pv_argv[pv_argv.index("--interval") + 1] == "60"
    dw_argv = srcs["deliverable_watcher"].run.args[0]
    assert dw_argv[dw_argv.index("--interval") + 1] == "3"
