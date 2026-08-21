#!/usr/bin/env python3
"""The SURFACE singleton — exactly one session-owning window per app id, by construction.

[SPEC:CXC-IC-SINGLETON] [CXC-IC-SURFACE-SINGLETON], [SPEC:CXC-LAUNCHBAR] [CXC-LB-AUTOSTART].

DEFECT-TWO-CONSOLES-SPAWNED-THEN-KILLED (David 2026-08-15: "some process is, on some
random basis, firing up two copies of the IC and then killing them shortly thereafter").

WHY THIS FILE EXISTS.  [CXC-IC-SINGLETON] gave every console DAEMON a host flock and
said, in its own reasoning, "singleton-ness must be a PROPERTY of the daemon, not a
matter of remembering to run one copy."  The console SURFACE — the `runtime <scene>
--transports` process that owns the window and hosts the data layer — was never in
that set.  Its only guard was [CXC-LB-AUTOSTART]'s "pgrep guard" in
`compose_projects/intelligence/start-console-stack.sh`.

A pgrep guard is ADVISORY: it constrains the ONE launch path that runs it, and it
answers a question it cannot actually answer.  Both failures are reproduced below as
DETERMINISTIC negative controls over real processes, then closed by the fix:

  (a) `test_pgrep_guard_cannot_distinguish_absent_from_unknown`
      `if ! pgrep …` reads ANY nonzero exit as "nothing is running" — including the
      exit that means "I could not tell."  Measured by David 2026-08-15: `pgrep -fc`
      ( `-c` is a Linux flag that macOS pgrep does not have) reported nothing while
      consoles were running, and a third console was launched on top of two.
  (b) `test_an_unguarded_launch_path_doubles`
      `console_supervisor.ConsoleSupervisor.start()` is a SECOND launch path whose
      `is_running()` inspects only its OWN child, so it spawns a rival surface
      unconditionally — and its `run()` ends in `finally: sup.stop()`, which is the
      "…and killing them shortly thereafter" half of the report.

The positive test is PARAMETRIZED over both of those launch paths: a fix that only
repaired the bringup would leave (b) open, which is how this class recurs
([INSIGHT-FORK-DIVERGENCE-IS-A-CLASS]).

Without `composeio.app_singleton` every positive test ERRORS at import — it cannot
report success against a missing fix.

SAFETY: every child here is `python3 <stub> <uuid-marker> <seconds>` with a per-run
UUID in its argv.  Nothing in this file can match, signal or spawn a real console,
ribbon, viewer or daemon, and $COMPOSEIO_LOCK_DIR is a pytest tmp_path so a test can
never contend with the live host locks.
"""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
import uuid

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
PY = os.path.join(REPO, "python")
if PY not in sys.path:
    sys.path.insert(0, PY)

CHILD_LIFETIME_S = 8.0
SETTLE_S = 1.5                      # generous: fork+exec+interpreter start


# ── stub surfaces: one guarded by the host singleton, one not ──────────────────

_PLAIN_SURFACE = textwrap.dedent("""
    import sys, time
    time.sleep(float(sys.argv[2]))              # argv[1] is the marker
""")

_SINGLETON_SURFACE = textwrap.dedent("""
    import sys, time
    sys.path.insert(0, sys.argv[3])
    from composeio.app_singleton import acquire_surface_singleton
    lock = acquire_surface_singleton(sys.argv[1])
    if lock is None:
        raise SystemExit(0)                     # a second surface opens NO window
    time.sleep(float(sys.argv[2]))
""")


class _Surfaces:
    """A per-test fleet of harmless stub surfaces, reaped on teardown."""

    def __init__(self, tmp_path, guarded: bool):
        self.marker = f"composeio-surface-test-{uuid.uuid4().hex}"
        self.lock_dir = str(tmp_path / "locks")
        os.makedirs(self.lock_dir, exist_ok=True)
        body = _SINGLETON_SURFACE if guarded else _PLAIN_SURFACE
        self.stub = str(tmp_path / "surface_stub.py")
        with open(self.stub, "w") as fh:
            fh.write(body)
        self.procs: list[subprocess.Popen] = []

    # the raw launch — what EVERY launch path ultimately does
    def launch(self) -> subprocess.Popen:
        p = subprocess.Popen(
            [sys.executable, self.stub, self.marker, str(CHILD_LIFETIME_S), PY],
            env=dict(os.environ, COMPOSEIO_LOCK_DIR=self.lock_dir),
            start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def alive(self) -> int:
        time.sleep(SETTLE_S)
        return sum(1 for p in self.procs if p.poll() is None)

    def close(self) -> None:
        for p in self.procs:
            try:
                p.kill()
                p.wait(timeout=3)
            except Exception:
                pass


@pytest.fixture
def surfaces(tmp_path):
    made: list[_Surfaces] = []

    def _make(guarded: bool) -> _Surfaces:
        s = _Surfaces(tmp_path, guarded)
        made.append(s)
        return s
    yield _make
    for s in made:
        s.close()


# ── the two LAUNCH PATHS, in the shape the repo actually ships them ────────────

def _bringup_leg(s: _Surfaces, pgrep_args: list[str]) -> None:
    """`compose_projects/intelligence/start-console-stack.sh`'s console leg:

        if ! pgrep <args> "<scene>" >/dev/null 2>&1; then <launch>; fi

    The guard is REAL `pgrep`; only the pattern is the harmless marker.  `pgrep_args`
    is what makes the negative control honest — it is the ONLY difference between the
    guard working and the guard silently answering "nothing is running".
    """
    rc = subprocess.run(["pgrep", *pgrep_args, s.marker],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
    if rc != 0:                                  # `if !` — ANY nonzero means "launch"
        s.launch()


def _supervisor_leg(s: _Surfaces) -> None:
    """`console_supervisor.ConsoleSupervisor.start()`: guarded ONLY by `self.proc`,
    which is None in a freshly constructed supervisor — so it always launches,
    however many surfaces another path already has up."""
    own_child = None                             # a fresh supervisor knows of none
    if own_child is None or own_child.poll() is not None:
        s.launch()


LAUNCH_PATHS = {
    "bringup(start-console-stack.sh)": lambda s: _bringup_leg(s, ["-f"]),
    "supervisor(console_supervisor.start)": _supervisor_leg,
    "hand-launch(runtime --transports)": lambda s: s.launch(),
}


# ── (a) NEGATIVE CONTROL — the guard cannot distinguish absent from unknown ────

def test_pgrep_guard_cannot_distinguish_absent_from_unknown(surfaces):
    """David's measured 2026-08-15 preflight, on the bench.

    `pgrep -c` is a LINUX flag; macOS pgrep exits 2 with a usage message.  Under
    `if ! pgrep …` that is indistinguishable from "no console is running", so the
    guard launches a duplicate on top of a live surface.  An instrument that can
    only report SUCCESS is not an instrument.
    """
    s = surfaces(guarded=False)
    s.launch()                                    # an established surface, alive
    assert s.alive() == 1, "the established surface must be up before we probe"

    probe = subprocess.run(["pgrep", "-fc", s.marker], capture_output=True, text=True)
    assert probe.returncode != 0, (
        "this control depends on macOS pgrep REJECTING -c; if this platform accepts "
        "it, the control no longer reproduces David's measurement")
    assert not probe.stdout.strip().isdigit() or probe.stdout.strip() == "0", probe.stdout

    _bringup_leg(s, ["-fc"])                      # the guard, fed the broken probe
    assert s.alive() == 2, (
        "the pgrep guard launched a duplicate on top of a live surface — this is the "
        "defect, and it is what the surface singleton has to make impossible")


# ── (b) NEGATIVE CONTROL — the guard is ADVISORY, so a second path doubles ─────

@pytest.mark.parametrize("path_name", sorted(LAUNCH_PATHS))
def test_an_unguarded_launch_path_doubles(surfaces, path_name):
    """The bringup's guard binds the bringup and nothing else.  Every other launch
    path — the supervisor's `start()`, a hand `runtime --transports`, the bounce's
    relaunch after a kill that did not take — spawns a second surface beside a live
    one.  Even the bringup itself doubles when its probe cannot answer (case (a)).
    """
    s = surfaces(guarded=False)
    s.launch()
    assert s.alive() == 1
    LAUNCH_PATHS[path_name](s)
    if path_name.startswith("bringup"):
        pytest.skip("the working pgrep guard does bind ITS OWN path — case (a) is "
                    "where that path fails; this control is about the others")
    assert s.alive() == 2, f"{path_name} spawned a rival surface beside a live one"


# ── (c) THE FIX — every launch path converges on ONE surface ───────────────────

@pytest.mark.parametrize("path_name", sorted(LAUNCH_PATHS))
def test_surface_singleton_holds_across_every_launch_path(surfaces, path_name):
    """CROSS-LAUNCH-PATH.  The invariant is enforced in the SURFACE, so it cannot be
    dropped by a launch path that forgets to check — including one added later.
    Parametrized deliberately: a fix that only repaired the bringup is the
    fork-divergence that makes this class recur."""
    import composeio.app_singleton  # noqa: F401 — missing fix ERRORS, never passes
    s = surfaces(guarded=True)
    s.launch()
    assert s.alive() == 1, "the first surface must acquire the singleton and run"
    LAUNCH_PATHS[path_name](s)
    assert s.alive() == 1, (
        f"{path_name} must find the singleton held and YIELD without opening a "
        f"window; saw more than one live surface")


def test_singleton_holds_when_the_pgrep_probe_cannot_answer(surfaces):
    """The exact defect David measured, now closed.

    Case (a) again — the bringup's guard fed a probe that exits nonzero for a reason
    that is NOT absence, so the guard decides to launch.  With the surface singleton
    the launch still yields, because the decision no longer depends on the guard
    having been able to answer.

    NOTE why this test is here and not folded into the parametrized one above: with
    a WORKING probe the bringup leg declines to launch on its own, so that
    parametrization passes even against a broken singleton.  This one does not — it
    is the bringup path's real negative control.
    """
    import composeio.app_singleton  # noqa: F401
    s = surfaces(guarded=True)
    s.launch()
    assert s.alive() == 1
    _bringup_leg(s, ["-fc"])                      # guard blinded, decides to launch
    assert len(s.procs) == 2, "the blinded guard must in fact have launched"
    assert s.alive() == 1, (
        "the second surface must have yielded to the host singleton even though the "
        "pgrep guard told it to launch")


def test_surface_singleton_survives_a_concurrent_race(surfaces):
    """The flock decides atomically, so simultaneity is not a special case — the
    property a check-then-act guard can never have however small its window."""
    import composeio.app_singleton  # noqa: F401
    s = surfaces(guarded=True)
    for _ in range(6):
        s.launch()
    assert s.alive() == 1, "a 6-way simultaneous launch must leave exactly ONE surface"


# ── (d) shape, containment, and the de-fork constraint ────────────────────────

def test_surface_key_is_derived_from_the_scene_app_id():
    """Reuse-by-reference, mirroring `app_topics.app_bbox_ns`: the key comes from the
    scene's declared `app: id:`, so console and ribbon can never contend."""
    from composeio.app_singleton import surface_singleton_key
    assert surface_singleton_key("intelligence-console") != \
        surface_singleton_key("compose-launch-bar")
    assert "intelligence-console" in surface_singleton_key("intelligence-console")
    # a nameless scene must not be able to claim the console's key
    assert surface_singleton_key("") != surface_singleton_key("intelligence-console")
    assert surface_singleton_key(None) == surface_singleton_key("  ")


def test_only_a_session_owning_scene_is_constrained():
    """CONTAINMENT.  Gate on the SAME discriminator the in-process data layer uses
    (`design.data_sources: true`) — never the agent id, never the mere presence of
    `--transports`.  Several doc viewers, several hookup viewers and the ribbon must
    all still run; making a second viewer window impossible would be a worse
    regression than the defect."""
    from composeio.render.engine.runtime import _surface_singleton_required

    class _Comp:
        def __init__(self, app_id, data_sources):
            self.app_id, self.data_sources = app_id, data_sources

    class _App:
        def __init__(self, comp):
            self.comp = comp

    assert _surface_singleton_required(_App(_Comp("intelligence-console", True)))
    assert not _surface_singleton_required(_App(_Comp("compose-launch-bar", False)))
    assert not _surface_singleton_required(_App(_Comp("doc-viewer", False)))
    assert not _surface_singleton_required(_App(None))


def test_the_runtime_actually_consults_the_gate_before_running():
    """WIRING.  Every other test here can pass while `main()` never calls the gate —
    a fix that exists and is not invoked is the quietest way for this defect to come
    back.  Asserted against the compiled code object, not the source text, and
    ordered: the gate must be consulted BEFORE `run`."""
    from composeio.render.engine import runtime
    names = list(runtime.main.__code__.co_names)
    assert "_acquire_surface_singleton" in names, (
        "runtime.main() must consult the surface singleton gate")
    assert names.index("_acquire_surface_singleton") < names.index("run"), (
        "the gate must be consulted BEFORE the render loop starts — a window that "
        "has already opened has already doubled")
    # …and the headless one-frame path must NOT take the lock: a screenshot is not
    # a surface, and gating it behind a live console would break every capture test
    # in this repo.  The headless branch returns first, so its call comes earlier.
    assert names.index("render_headless") < names.index("_acquire_surface_singleton"), (
        "the headless capture path must return BEFORE the surface gate")


def test_no_second_flock_call_site():
    """The fix must reuse `compose_projects/intelligence/publisher/singleton.py` BY REFERENCE.  A copy
    of the flock in the render tree is exactly the fork-divergence that made this
    class recur.  (`test_singleton_hardening.test_exactly_one_flock_call_site` is the
    repo-wide guard; this states the constraint where the fix lives.)"""
    src = open(os.path.join(PY, "composeio", "app_singleton.py")).read()
    assert "import fcntl" not in src, (
        "app_singleton must not implement a lock — it delegates to the ONE shared "
        "acquire_singleton in compose_projects/intelligence/publisher/singleton.py")
    # …and the REPO-WIDE invariant must still hold with this module in the tree.
    # Delegated to the ONE existing guard rather than re-greped here: a second copy
    # of the check is the same fork this whole file is about.
    import importlib.util
    guard = os.path.join(REPO, "compose_projects", "intelligence", "publisher",
                         "test_singleton_hardening.py")
    spec = importlib.util.spec_from_file_location("_singleton_guard", guard)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_singleton_guard"] = mod
    spec.loader.exec_module(mod)
    mod.test_exactly_one_flock_call_site()


def test_supersede_is_opt_in_not_the_default():
    """A DELIBERATE bounce wants the fresh console to win; a login autostart and a
    stray second bringup must YIELD.  Defaulting to supersede would turn every
    accidental double-launch into a kill of the console David is using — strictly
    worse than the defect it fixes."""
    import inspect
    from composeio.app_singleton import acquire_surface_singleton
    sig = inspect.signature(acquire_surface_singleton)
    assert sig.parameters["supersede"].default is False


def test_supersede_reaps_the_incumbent_when_asked(surfaces):
    """…and when a bounce DOES ask, the fresh surface wins by reaping the recorded
    holder PID — never by an unscoped `pgrep -f console.compose.yaml | kill -9`,
    which cannot tell one operator's console from another's."""
    import composeio.app_singleton  # noqa: F401
    s = surfaces(guarded=True)
    first = s.launch()
    assert s.alive() == 1

    from composeio.app_singleton import acquire_surface_singleton
    lock = acquire_surface_singleton(s.marker, supersede=True, lock_dir=s.lock_dir)
    try:
        assert lock is not None, "supersede must acquire the lock"
        first.wait(timeout=8)
        assert first.poll() is not None, "the incumbent surface must have been reaped"
    finally:
        if lock is not None:
            lock.close()
