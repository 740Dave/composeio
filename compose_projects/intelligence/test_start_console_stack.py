#!/usr/bin/env python3
"""test_start_console_stack — the ENFORCING guard for the console-stack autostart linkage
([SPEC:CXC-LAUNCHBAR] [CXC-LB-AUTOSTART]).

WHY (DEFECT-LAUNCH-BAR-RIBBON-NOT-RUNNING, David 2026-08-04): the "ribbon autostarts WITH the
console" contract lived only as prose in the bounce-console skill runbook, so a console launched by
any path that skipped the runbook's ribbon block came up WITHOUT the ribbon. The cure is ONE
canonical bringup (`compose_projects/intelligence/start-console-stack.sh`) that starts EVERY stack member. This test
FAILS if that script is missing OR if it drops any stack member (the ribbon, app_launcher, or the
console) or its idempotency guard — so a future edit that re-forks or forgets the ribbon fails
loudly, cross-implementation, rather than silently recurring the defect.

Run:  python3 -m pytest compose_projects/intelligence/test_start_console_stack.py
      python3 compose_projects/intelligence/test_start_console_stack.py      # plain-assert selftest + counts
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BRINGUP = REPO_ROOT / "compose_projects" / "intelligence" / "start-console-stack.sh"

# The three surfaces that MUST come up together as "the console stack". Each row is
# (name, launch-token the bringup must invoke, pgrep guard token for idempotency). The ribbon +
# app_launcher rows are the exact linkage the defect dropped.
STACK = [
    ("console", "compose_projects/intelligence/console/console.compose.yaml",
     "compose_projects/intelligence/console/console.compose.yaml"),
    ("ribbon", "apps/compose_platform_launch_bar/launch_bar.compose.yaml",
     "apps/compose_platform_launch_bar/launch_bar.compose.yaml"),
    ("app_launcher", "intelligence.tools.app_launcher",
     "intelligence.tools.app_launcher"),
]


def _script_text() -> str:
    assert BRINGUP.is_file(), (
        f"canonical console-stack bringup missing: {BRINGUP} — the autostart-with-console "
        f"linkage has no single source of truth (the defect)")
    return BRINGUP.read_text()


def test_bringup_script_exists_and_executable():
    assert BRINGUP.is_file(), f"missing {BRINGUP}"
    mode = BRINGUP.stat().st_mode
    assert mode & 0o111, f"{BRINGUP} is not executable (chmod +x)"


def test_bringup_launches_every_stack_member():
    """The core guard: the ONE bringup script must launch console + ribbon + app_launcher."""
    text = _script_text()
    for name, launch_token, _guard in STACK:
        assert launch_token in text, (
            f"console-stack bringup does not launch {name!r} (missing {launch_token!r}) — "
            f"this is exactly how the ribbon went missing")


def test_bringup_is_idempotent_per_member():
    """Each member is launched behind a guard, so re-running skips a live surface."""
    text = _script_text()
    for name, _launch_token, guard in STACK:
        assert "surface_absent" in text and guard in text, (
            f"console-stack bringup has no idempotency guard for {name!r} ({guard!r})")


# ── shared harness: everything runs against a PRIVATE lock dir ────────────────
# Every member's liveness is now its host LOCK (DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E),
# and every lock path honours $COMPOSEIO_LOCK_DIR. Each test points that at its own temp dir, so
# the live stack on this host (whose locks sit in the system temp dir) can neither satisfy a
# positive check nor fail a negative one.

import os
import signal
import subprocess
import tempfile
import time
import uuid

import pytest

CONSOLE_SCENE = "compose_projects/intelligence/console/console.compose.yaml"
RIBBON_SCENE = "apps/compose_platform_launch_bar/launch_bar.compose.yaml"
APP_LAUNCHER_ROLE = "app-launcher"


def _env(lock_dir: Path, **extra) -> dict:
    Path(lock_dir).mkdir(parents=True, exist_ok=True)   # singleton.py does not create it
    env = dict(os.environ)
    env.update(COMPOSEIO_LOCK_DIR=str(lock_dir), COMPOSEIO_OVERLAY_ENV="none",
               COMPOSEIO_REPO_ROOT=str(REPO_ROOT),
               PYTHONPATH=f"{REPO_ROOT / 'python'}:{REPO_ROOT / 'compose_projects'}")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    env.pop("COMPOSEIO_SESSION_JSONL", None)
    env.update(extra)
    return env


def _shell_with_bringup_functions(body: str, override: str = "") -> str:
    """bash text that defines the bringup's REAL guard functions (definitions only; running the
    script's body would launch things), then an optional override, then *body*."""
    return (
        'set -uo pipefail\n'
        f'eval "$(sed -n \'/^member_probe()/p;/^MEMBER_PID=/p;/^surface_absent()/,/^}}/p;'
        f'/^spawn_detached()/,/^}}/p\' {BRINGUP})"\n'
        f'{override}\n{body}\n')


def _guard(lock_dir: Path, *probe_args: str, override: str = "") -> int:
    """Run the shipped surface_absent. 0 == absent (launch), nonzero == do not launch."""
    args = " ".join(f"'{a}'" for a in probe_args)
    return subprocess.run(["/bin/bash", "-c", _shell_with_bringup_functions(
        f"surface_absent {args}", override)], env=_env(lock_dir), cwd=str(REPO_ROOT),
        capture_output=True, text=True).returncode


def _wait_file(path: Path, timeout: float = 10.0) -> str:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and path.read_text().strip():
            return path.read_text()
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def _kill(pids) -> None:
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


# A fake member that holds its member's lock through the SAME acquisition code the real member
# runs (app_singleton.acquire_surface_singleton for a scene, singleton.acquire_singleton for a
# role), then reports ready and sleeps. Its argv is whatever the test chooses: that is the point.
_HOLDER = r"""
import os, sys, time
sys.path[:0] = [os.environ["HOLDER_REPO"] + "/python",
                os.environ["HOLDER_REPO"] + "/compose_projects/intelligence/publisher"]
kind, name = os.environ["HOLDER_KIND"], os.environ["HOLDER_NAME"]
if kind == "scene":
    from composeio.app_singleton import acquire_surface_singleton
    lock = acquire_surface_singleton(name)
else:
    from singleton import acquire_singleton
    lock = acquire_singleton(name)
assert lock is not None, "holder could not take the lock"
open(os.environ["HOLDER_READY"], "w").write(str(os.getpid()))
time.sleep(30)
"""


def _start_holder(tmp: Path, lock_dir: Path, kind: str, name: str, argv_tail, *,
                  interpreter: str = sys.executable, script_name: str = "holder.py",
                  as_module: str = "", cwd: Path | None = None):
    """Start a lock-holding fake member whose argv is `<interpreter> <argv_tail…>`, where
    argv_tail may reference {script} (the holder file) and {module} (the holder as a module)."""
    pkg = tmp / "holderpkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("")
    script = tmp / script_name
    script.write_text(_HOLDER)
    if as_module:
        parts = as_module.split(".")
        d = tmp
        for part in parts[:-1]:
            d = d / part
            d.mkdir(exist_ok=True)
            (d / "__init__.py").write_text("")
        (d / f"{parts[-1]}.py").write_text(_HOLDER)
    ready = tmp / f"ready-{uuid.uuid4().hex}"
    argv = [interpreter] + [a.format(script=str(script), module=as_module) for a in argv_tail]
    env = _env(lock_dir, HOLDER_REPO=str(REPO_ROOT), HOLDER_KIND=kind, HOLDER_NAME=name,
               HOLDER_READY=str(ready))
    env["PYTHONPATH"] = f"{tmp}:{env['PYTHONPATH']}"
    p = subprocess.Popen(argv, env=env, cwd=str(cwd or tmp), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        pid = int(_wait_file(ready))
    except AssertionError:
        p.kill()
        raise AssertionError(f"holder never became ready: {p.stderr.read().decode()[-400:]}")
    return p, pid


# ── the guard, EXECUTED, reading LOCKS ───────────────────────────────────────

def test_guard_says_absent_only_when_the_lock_is_affirmatively_free(tmp_path):
    for args in (("--scene", CONSOLE_SCENE), ("--scene", RIBBON_SCENE), ("--role", APP_LAUNCHER_ROLE)):
        assert _guard(tmp_path, *args) == 0, f"a free lock must mean absent for {args}"


@pytest.mark.parametrize("kind,name,args", [
    ("scene", "intelligence-console", ("--scene", CONSOLE_SCENE)),
    ("scene", "compose-launch-bar", ("--scene", RIBBON_SCENE)),
    ("role", APP_LAUNCHER_ROLE, ("--role", APP_LAUNCHER_ROLE)),
])
def test_guard_declines_to_launch_while_the_members_lock_is_held(tmp_path, kind, name, args):
    p, pid = _start_holder(tmp_path, tmp_path / "locks", kind, name, ["{script}"])
    try:
        assert _guard(tmp_path / "locks", *args) != 0, f"a HELD lock must mean up for {args}"
    finally:
        p.kill()
        p.wait(timeout=3)
    assert _guard(tmp_path / "locks", *args) == 0, "the holder died, so the lock is free again"


def test_guard_refuses_an_UNANSWERABLE_probe_instead_of_reading_it_as_absent(tmp_path):
    """An instrument that can only report ABSENCE is not an instrument. A probe that cannot
    answer (exit 2, or 127 when the interpreter is missing) must mean DO NOT LAUNCH."""
    for rc in (2, 127):
        assert _guard(tmp_path, "--role", APP_LAUNCHER_ROLE,
                      override=f"member_probe() {{ return {rc}; }}") != 0, rc


def test_probe_cannot_answer_for_a_scene_that_declares_no_lock(tmp_path):
    """A scene with no host lock must not be reported "absent" forever (a duplicate on every
    run). The probe says it cannot answer (exit 2), so the guard declines."""
    scene = tmp_path / "nolock.compose.yaml"
    scene.write_text("app:\n  id: no-lock-viewer\n  name: x\ndesign:\n  title: x\n"
                     "widgets: []\n")
    r = subprocess.run([sys.executable, "-m", "composeio.stack_member_probe", "--scene", str(scene)],
                       env=_env(tmp_path), cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert r.returncode == 2, (r.returncode, r.stderr)


def _real_probe(lock_dir: str, *args: str) -> subprocess.CompletedProcess:
    env = _env(Path(tempfile.mkdtemp()))          # a valid base env ...
    env["COMPOSEIO_LOCK_DIR"] = str(lock_dir)     # ... then the lock dir under test, as given
    return subprocess.run([sys.executable, "-m", "composeio.stack_member_probe", *args],
                          env=env, cwd=str(REPO_ROOT), capture_output=True, text=True)


_PROBED = [("--scene", CONSOLE_SCENE), ("--role", APP_LAUNCHER_ROLE)]
_LOCK_FILE = {"--scene": "composeio-surface-intelligence-console.lock",
              "--role": "composeio-app-launcher.lock"}


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root bypasses permission bits")
@pytest.mark.parametrize("args", _PROBED, ids=["scene", "role"])
@pytest.mark.parametrize("case", ["lock_file_mode_000", "lock_dir_mode_000", "lock_dir_missing"])
def test_REAL_probe_cannot_answer_when_the_lock_cannot_be_read(tmp_path, case, args):
    """Candidate review R2-F2. A lock the probe cannot read is also a lock the member cannot hold:
    it then runs UNLOCKED, so "absent" would start a fresh unlocked duplicate on every bringup.
    The REAL probe must exit 2 (cannot probe), never 1 (absent)."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    lock = lock_dir / _LOCK_FILE[args[0]]
    if case == "lock_file_mode_000":
        lock.write_text("")
        lock.chmod(0)
    elif case == "lock_dir_mode_000":
        lock.write_text("")
        lock_dir.chmod(0)
    else:
        lock_dir.rmdir()
    try:
        r = _real_probe(str(lock_dir), *args)
    finally:
        if lock_dir.exists():
            lock_dir.chmod(0o700)
        if lock.exists():
            lock.chmod(0o600)
    assert r.returncode == 2, f"{case} {args}: exit {r.returncode} (2 expected): {r.stderr}"
    assert "cannot probe" in r.stderr


@pytest.mark.parametrize("args", _PROBED, ids=["scene", "role"])
@pytest.mark.parametrize("case", ["file_absent_in_good_dir", "file_present_flock_free"])
def test_REAL_probe_reports_absent_only_for_a_readable_free_lock(tmp_path, case, args):
    """The two conditions that ARE absent (exit 1): no lock file yet in a usable dir, or a stale
    lock file whose flock nobody holds (whatever pid it records)."""
    lock_dir = tmp_path / "locks"
    lock_dir.mkdir()
    if case == "file_present_flock_free":
        (lock_dir / _LOCK_FILE[args[0]]).write_text("1\n")
    r = _real_probe(str(lock_dir), *args)
    assert r.returncode == 1, f"{case} {args}: exit {r.returncode}: {r.stderr}"


def test_spawn_detached_reports_an_exec_failure_as_127(tmp_path):
    r = subprocess.run(["/bin/bash", "-c", _shell_with_bringup_functions(
        f'spawn_detached "{tmp_path}/log" /nonexistent/cmd; echo "rc=$?"')],
        env=_env(tmp_path), cwd=str(REPO_ROOT), capture_output=True, text=True)
    assert "rc=127" in r.stdout, (r.stdout, r.stderr)
    assert "exec failed" in (tmp_path / "log").read_text()


def test_one_members_failed_spawn_does_not_stop_the_others(tmp_path):
    """Candidate review N7. The console's spawn fails (its log path is a directory, so the
    redirect fails). The ribbon and app_launcher must still be launched, and the bringup must
    exit non-zero, naming the failed member."""
    logs = tmp_path / "logs"
    (logs / "console.log").mkdir(parents=True)
    members = {}
    try:
        _, out, members, err = _run_bringup(tmp_path, tmp_path / "locks", expect_rc=1,
                                            log_dir=logs, want_stderr=True)
        launched = [m for _, (_, m) in members.items()]
        assert len(launched) == 2 and not any(CONSOLE_SCENE in m for m in launched), launched
        assert "console failed to start" in err and "failed to start: console" in err, err
    finally:
        _kill(members)


def test_every_stack_member_declares_the_lock_the_bringup_reads(tmp_path):
    """Cross-implementation pin: the bringup asks the console and ribbon SCENES and the
    app_launcher ROLE; each must actually be lock-backed, and the role must be the one
    app_launcher really acquires."""
    for scene in (CONSOLE_SCENE, RIBBON_SCENE):
        r = subprocess.run([sys.executable, "-m", "composeio.stack_member_probe", "--scene", scene],
                           env=_env(tmp_path), cwd=str(REPO_ROOT), capture_output=True, text=True)
        assert r.returncode == 1, f"{scene} must be lock-backed (free here): {r.returncode} {r.stderr}"
    text = _script_text()
    assert f'APP_LAUNCHER_ROLE="{APP_LAUNCHER_ROLE}"' in text
    assert 'surface_absent --scene "$CONSOLE_SCENE"' in text
    assert 'surface_absent --scene "$RIBBON_SCENE"' in text
    assert 'surface_absent --role "$APP_LAUNCHER_ROLE"' in text
    src = (REPO_ROOT / "compose_projects/intelligence/tools/app_launcher.py").read_text()
    assert f'SINGLETON_ROLE = "{APP_LAUNCHER_ROLE}"' in src
    assert "acquire_singleton(SINGLETON_ROLE)" in src


def test_app_launcher_is_a_watched_daemon():
    """Cross-implementation: daemon_watch already declares app_launcher a persistent daemon whose
    absence canaries — so the bringup MUST start it, or the canary fires forever. Ties the bringup
    contract to the liveness contract."""
    sys.path.insert(0, str(REPO_ROOT / "compose_projects" / "intelligence" / "publisher"))
    import daemon_watch
    watched = {s.id for s in daemon_watch.default_registry()}
    assert "app_launcher" in watched, "daemon_watch no longer watches app_launcher"
    assert "intelligence.tools.app_launcher" in _script_text(), (
        "bringup must start the app_launcher daemon that daemon_watch expects alive")



# ── the REAL bringup, end to end, with stubbed members ───────────────────────
# DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E (2026-11-26). The LaunchAgent runs this bringup
# as a one-shot job (KeepAlive false). When a launchd job exits, launchd SIGTERMs every process
# still in the job's PROCESS GROUP unless the plist sets AbandonProcessGroup (launchd.plist(5)).
# `nohup cmd &` changes SIGHUP only, so all three surfaces were reaped about 1 s after "console
# stack up". The console turned SIGTERM into SDL_QUIT and exited 0 with no traceback.

_MEMBER_MODULES = ("composeio.render.engine.runtime", "intelligence.tools.app_launcher")


def _stub_bin(tmp: Path, record: Path) -> Path:
    """A PATH prefix whose `python3` turns a stack MEMBER launch (`-m <member module>`) into
    "record pid/pgid, then sleep in place". Everything else (the spawn shim, the lock probe)
    goes to the real interpreter."""
    import stat
    b = tmp / "bin"
    b.mkdir()
    cases = "|".join(_MEMBER_MODULES)
    (b / "python3").write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "-m" ]; then case "$2" in {cases})\n'
        f'  echo "$$ $(ps -o pgid= -p $$ | tr -d \' \') $2 $3" >> "{record}"; exec sleep 30;;\n'
        "esac; fi\n"
        f'exec "{sys.executable}" "$@"\n')
    f = b / "python3"
    f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return b


def _run_bringup(tmp: Path, lock_dir: Path, *, expect_rc: int = 0, log_dir: Path | None = None,
                 want_stderr: bool = False):
    """Run the REAL bringup as a process-group leader (as launchd does). Returns
    (job_pgid, stdout, {pid: (pgid, member)}[, stderr]) once the job has exited."""
    record = tmp / "members.txt"
    env = _env(lock_dir, PATH=f"{_stub_bin(tmp, record)}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               LOG_DIR=str(log_dir or tmp / "logs"), MQTT="mqtt://localhost:1")
    job = subprocess.Popen(["/bin/bash", str(BRINGUP)], env=env, start_new_session=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = job.communicate(timeout=60)
    assert job.returncode == expect_rc, (
        f"bringup exit {job.returncode}, expected {expect_rc}: {err.decode()[-600:]}")
    rows = [ln.split(" ", 3) for ln in record.read_text().splitlines()] if record.exists() else []
    members = {int(r[0]): (int(r[1]), " ".join(r[2:])) for r in rows}
    if want_stderr:
        return job.pid, out.decode(), members, err.decode()
    return job.pid, out.decode(), members


def test_stack_members_survive_the_launching_jobs_process_group_teardown(tmp_path):
    job_pgid, out, members = _run_bringup(tmp_path, tmp_path / "locks")
    try:
        # NON-VACUOUS: all three members must actually have been launched and recorded.
        assert len(members) == 3, f"expected console + ribbon + app_launcher, got {members}\n{out}"
        try:
            os.killpg(job_pgid, signal.SIGHUP)    # a closing terminal / session teardown
            os.killpg(job_pgid, signal.SIGTERM)   # launchd's reap of an exited one-shot job
        except ProcessLookupError:
            pass                                  # nobody left in the job's group: the fix
        time.sleep(0.5)
        dead = [f"{m} (pid {p}, pgid {g}, job pgid {job_pgid})"
                for p, (g, m) in members.items() if not _alive(p)]
        assert not dead, "members were reaped with the bringup's process group: " + "; ".join(dead)
        assert not [m for p, (g, m) in members.items() if g == job_pgid]
    finally:
        _kill(members)


def test_a_member_survives_a_group_SIGTERM_sent_the_instant_its_spawn_returns(tmp_path):
    """N1 (candidate review): detachment must be STRUCTURAL, not timed. spawn_detached must not
    return while the member is still in the caller's process group, so a group SIGTERM sent
    the moment it returns (here, after EACH of three spawns) must kill nothing it launched."""
    mod = f"spawnprobe_{uuid.uuid4().hex[:10]}"
    (tmp_path / f"{mod}.py").write_text(
        "import os, sys, time\n"
        "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
        "time.sleep(30)\n")
    recs = [tmp_path / f"pid{i}" for i in range(3)]
    # A HANDLER trap keeps this shell alive through its own group SIGTERM. It must not be
    # `trap '' TERM`: an IGNORED signal is inherited across fork and exec, which would make every
    # member immune and the test vacuous (measured: it then passed against the backgrounded
    # shim). A handler is reset to the default in children, so members stay killable.
    body = "trap 'true' TERM HUP\n" + "".join(
        f'spawn_detached "{tmp_path}/log{i}" "{sys.executable}" -m {mod} "{rec}"\n'
        "kill -TERM 0\n"                          # the whole group, right now
        for i, rec in enumerate(recs))
    r = subprocess.run(["/bin/bash", "-c", _shell_with_bringup_functions(body)],
                       env=_env(tmp_path, PYTHONPATH=str(tmp_path)), cwd=str(REPO_ROOT),
                       start_new_session=True, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "command not found" not in r.stderr, r.stderr[-300:]
    pids = []
    try:
        for rec in recs:
            pids.append(int(_wait_file(rec)))
        time.sleep(0.3)
        dead = [p for p in pids if not _alive(p)]
        assert not dead, f"a group SIGTERM right after spawn_detached returned killed {dead} ({r.stderr[-300:]})"
    finally:
        _kill(pids)


# The candidate review's F1 table: REAL members (here, holders of the member's real lock)
# started in ways an argv pattern missed. With text guards each produced a DUPLICATE. With the
# lock guard the bringup must report "already up" and launch nothing for that member.
_F1_CASES = [
    ("console, absolute scene path", "scene", "intelligence-console", "console",
     lambda t: dict(argv_tail=["-m", "{module}", str(REPO_ROOT / CONSOLE_SCENE), "--transports"],
                    as_module=f"f1abs_{uuid.uuid4().hex[:8]}.runtime")),
    ("console, python3 -u -m", "scene", "intelligence-console", "console",
     lambda t: dict(argv_tail=["-u", "-m", "{module}", CONSOLE_SCENE, "--transports"],
                    as_module=f"f1u_{uuid.uuid4().hex[:8]}.runtime")),
    ("console, ./ scene path", "scene", "intelligence-console", "console",
     lambda t: dict(argv_tail=["-m", "{module}", "./" + CONSOLE_SCENE, "--transports"],
                    as_module=f"f1dot_{uuid.uuid4().hex[:8]}.runtime")),
    ("app_launcher run as a script file", "role", APP_LAUNCHER_ROLE, "app_launcher",
     lambda t: dict(argv_tail=["{script}", "--mqtt", "mqtt://localhost:1"],
                    script_name="app_launcher.py")),
    ("ribbon, interpreter path with a space", "scene", "compose-launch-bar", "launch bar ribbon",
     lambda t: dict(argv_tail=["{script}", RIBBON_SCENE], interpreter=_spaced_interpreter(t))),
]


def _spaced_interpreter(tmp: Path) -> str:
    d = tmp / "dir with space"
    d.mkdir(exist_ok=True)
    link = d / "python3"
    if not link.exists():
        link.symlink_to(sys.executable)
    return str(link)


@pytest.mark.parametrize("label,kind,name,word,shape", _F1_CASES, ids=[c[0] for c in _F1_CASES])
def test_review_F1_a_member_started_any_way_is_seen_and_not_duplicated(
        tmp_path, label, kind, name, word, shape):
    lock_dir = tmp_path / "locks"
    holder, hpid = _start_holder(tmp_path, lock_dir, kind, name, **shape(tmp_path))
    members = {}
    try:
        _, out, members = _run_bringup(tmp_path, lock_dir)
        assert f"= {word} already up (pid {hpid})" in out, f"{label}: not seen as up:\n{out}"
        launched = [m for _, (_, m) in members.items()]
        assert len(launched) == 2, f"{label}: expected only the OTHER two launched, got {launched}"
        if word == "app_launcher":
            assert not any("app_launcher" in m for m in launched), f"{label}: duplicate {launched}"
        else:
            scene = CONSOLE_SCENE if word == "console" else RIBBON_SCENE
            assert not any(scene in m for m in launched), f"{label}: duplicate {launched}"
    finally:
        _kill(members)
        holder.kill()
        holder.wait(timeout=3)


def test_a_shell_that_merely_MENTIONS_every_member_is_not_a_member(tmp_path):
    """The original impostor: the shape of a Bash call whose text names all three members.
    It holds no lock, so all three must still be launched."""
    text = (f"sleep 20; : python3 -m composeio.render.engine.runtime {CONSOLE_SCENE} "
            f"{RIBBON_SCENE} python3 -m intelligence.tools.app_launcher")
    imp = subprocess.Popen(["/bin/sh", "-c", text], start_new_session=True)
    members = {}
    try:
        time.sleep(0.3)
        _, out, members = _run_bringup(tmp_path, tmp_path / "locks")
        assert len(members) == 3, f"an impostor shell suppressed a launch:\n{out}"
    finally:
        _kill(members)
        imp.kill()
        imp.wait(timeout=3)


def test_a_second_bringup_launches_nothing_while_the_members_hold_their_locks(tmp_path):
    """Idempotency (R3), executed: with all three locks held, a re-run launches nothing."""
    lock_dir = tmp_path / "locks"
    holders = [_start_holder(tmp_path / k, lock_dir, kind, name, ["{script}"])
               for k, kind, name in (("c", "scene", "intelligence-console"),
                                     ("r", "scene", "compose-launch-bar"),
                                     ("a", "role", APP_LAUNCHER_ROLE))
               if (tmp_path / k).mkdir() is None]
    members = {}
    try:
        _, out, members = _run_bringup(tmp_path, lock_dir)
        assert members == {}, f"a re-run launched {members}:\n{out}"
        assert out.count("already up") == 3, out
    finally:
        _kill(members)
        for p, _ in holders:
            p.kill()
            p.wait(timeout=3)


def _selftest() -> int:
    """Plain-assert run of the STATIC checks; the executed tests need pytest's tmp_path."""
    checks = [
        ("script exists + executable", test_bringup_script_exists_and_executable),
        ("launches every stack member", test_bringup_launches_every_stack_member),
        ("idempotent per member", test_bringup_is_idempotent_per_member),
        ("app_launcher is a watched daemon", test_app_launcher_is_a_watched_daemon),
    ]
    fails = []
    for name, fn in checks:
        try:
            fn()
        except AssertionError as exc:
            fails.append(f"{name}: {exc}")
    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print(f"PASS: start-console-stack bringup ({len(checks)} static checks; "
          "run pytest for the executed ones)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
