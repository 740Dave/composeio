#!/usr/bin/env python3
"""[CXC-IC-SELF-IDENTITY] Cross-implementation recurrence guard: ONE session owner — a console
instruments its OWN transcript by a PASSED id (never an `ls -t` guess), and the retired standalone
daemons REFUSE to co-run beside an in-process console.

The defect (live 2026-07-24, a RECURRENCE): the architect console rendered provisionIO's messages
AND showed provisionIO's ~75% context on the architect's ~25% meter (both derive from the SAME
mis-selected transcript). Root: [DEC-INPROCESS-SOURCES] moved the data layer in-process on
2026-07-22 to kill the `ls -t | head -1` wrong-session pick, but the RETIRED standalone
`start-console-daemons.sh` set was still run ALONGSIDE the console (the fossil `console-up` skill).
The per-<id> singleton flock ([CXC-IC-SINGLETON]) stops the DOUBLING but not wrong-session: whoever
wins the lock FIRST serves the bus, and the standalone winner selected its transcript by newest-wins
among the two sessions coexisting in the shared home project dir — the twin-fork of
[DEC-DOUBLING-IS-TRANSCRIPT-FORK]. The 2026-07-22 fix also built the CAPABILITY to PIN a session but
wired the PASSING on only ONE launch path (the spawn, IntelligenceHost) and not the root re-host
(transport.sh/launcher.py) — a forked invariant living only in code ([INSIGHT-FORK-DIVERGENCE-IS-A-CLASS]).

Parametrized over EVERY transcript-selection / ownership site, so any launch path that reverts to a
newest-wins guess — or lets the retired daemons co-run — fails loudly:

  A. launcher.py  — ROOT re-host: `_export_session_pin` exports COMPOSEIO_SESSION_JSONL from the
                    PASSED RESUME_SESSION id, NOT the newest .jsonl.
  B. host.py      — SPAWN path: `IntelligenceHost._build_env` carries the per-id pin into the child.
  C. data_sources — the IN-PROCESS console: `resolve_session_jsonl` takes the pin, uses a single
                    unambiguous .jsonl, and REFUSES (returns None + shouts) rather than guess among
                    multiple unpinned transcripts.
  D. start-console-daemons.sh — the HEADLESS fallback: honors the pin, refuses to guess among
                    multiple unpinned .jsonl, and REFUSES (exit!=0) when a --transports console
                    already owns the session (FORCE=1 overrides).

Run:  python3 compose_projects/intelligence/launcher/test_session_identity.py --selftest
      pytest compose_projects/intelligence/launcher/test_session_identity.py
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time

_HERE = os.path.dirname(os.path.abspath(__file__))                    # compose_projects/intelligence/launcher
_INTEL = os.path.dirname(_HERE)                                       # intelligence
_REPO = os.path.dirname(os.path.dirname(_INTEL))                              # repo root (compose_projects is in between)
_HOST_DIR = os.path.join(_INTEL, "host")
for _d in (_HERE, _HOST_DIR):
    if _d not in sys.path:
        sys.path.insert(0, _d)

SCRIPT = os.path.join(_INTEL, "start-console-daemons.sh")
_DATA_SOURCES = os.path.join(_INTEL, "console", "data_sources.py")

# The exact vars each selection site reads — cleared around every case so a stray env from the
# real session (this test runs INSIDE a live instance) can never mask a regression.
_PIN_VARS = ("COMPOSEIO_SESSION_JSONL", "JSONL", "SESSION_JSONL", "CLAUDE_PROJECTS_DIR",
             "CLAUDE_CODE_SESSION_ID")


def _load_data_sources():
    """Import compose_projects/intelligence/console/data_sources.py BY FILE PATH (as runtime.py does — the module
    is not importable as a package)."""
    name = "_ds_under_test"
    spec = importlib.util.spec_from_file_location(name, _DATA_SOURCES)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # register BEFORE exec so the module's @dataclass resolves
    spec.loader.exec_module(mod)     # type: ignore[union-attr]
    return mod


def _two_sessions(dirpath: str) -> tuple[str, str, str, str]:
    """Create a shared project dir with TWO session .jsonl — `own` (older) and `sibling` (NEWER),
    reproducing the live collision. The sibling is written LAST + own is back-dated, so an
    `ls -t | head -1` newest-wins guess would pick the SIBLING — the exact wrong pick the invariant
    forbids. Returns (own_id, own_path, sibling_id, sibling_path)."""
    own_id = "aaaaaaaa-0000-0000-0000-000000000001"      # the architect (this instance)
    sib_id = "bbbbbbbb-1111-1111-1111-111111111111"      # provisionIO (the concurrent sibling)
    own = os.path.join(dirpath, f"{own_id}.jsonl")
    sib = os.path.join(dirpath, f"{sib_id}.jsonl")
    for p in (own, sib):
        with open(p, "w") as fh:
            fh.write('{"type":"assistant"}\n')
    old = time.time() - 3600
    os.utime(own, (old, old))                            # own is OLDER -> never the newest-wins pick
    return own_id, own, sib_id, sib


class _EnvGuard:
    def __enter__(self):
        self._saved = {k: os.environ.get(k) for k in _PIN_VARS}
        for k in _PIN_VARS:
            os.environ.pop(k, None)
        return self

    def __exit__(self, *a):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── A. launcher.py root re-host: passed RESUME_SESSION id wins over newest ─────────────────────
def check_launcher_pins_passed_id(check) -> None:
    import launcher  # noqa: WPS433
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        own_id, own_path, sib_id, sib_path = _two_sessions(d)
        os.environ["CLAUDE_PROJECTS_DIR"] = d
        newest = max((own_path, sib_path), key=os.path.getmtime)
        check(newest == sib_path, "(A0) fixture invalid: sibling is not the newest .jsonl")

        lch = launcher.Launcher(intelligence_id="architect", resume_session=own_id,
                                claude_args=[], log=lambda _l: None)
        lch._export_session_pin()
        pinned = os.environ.get("COMPOSEIO_SESSION_JSONL")
        check(pinned == own_path,
              f"(A) launcher must pin the PASSED session ({own_path}), got {pinned!r} "
              f"(newest-wins would have given the SIBLING {sib_path})")
        check(pinned != sib_path, "(A) launcher must NEVER select the sibling's transcript")

    # An explicit pre-set pin is honored, never overridden by derivation.
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        own_id, own_path, sib_id, sib_path = _two_sessions(d)
        os.environ["CLAUDE_PROJECTS_DIR"] = d
        os.environ["COMPOSEIO_SESSION_JSONL"] = sib_path       # a caller pinned the sibling on purpose
        lch = launcher.Launcher(intelligence_id="architect", resume_session=own_id,
                                claude_args=[], log=lambda _l: None)
        lch._export_session_pin()
        check(os.environ.get("COMPOSEIO_SESSION_JSONL") == sib_path,
              "(A2) an explicit COMPOSEIO_SESSION_JSONL must be honored, not overridden")


# ── B. host.py spawn path: per-id resolver carries the pin into the child env ──────────────────
def check_host_carries_pin(check) -> None:
    import host  # noqa: WPS433
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        own_id, own_path, sib_id, sib_path = _two_sessions(d)
        mapping = {"architect": own_path, "provisionio": sib_path}
        h = host.IntelligenceHost(session_jsonl_for=lambda i: mapping.get(i), log=lambda _l: None)
        spec = host.IntelligenceSpec(id="architect", mode="headless", task="x", resume_session=own_id)
        _argv, env = h.build_command(spec)
        check(env.get("COMPOSEIO_SESSION_JSONL") == own_path,
              f"(B) host spawn must carry the per-id pin ({own_path}), got "
              f"{env.get('COMPOSEIO_SESSION_JSONL')!r}")
        check(env.get("INTELLIGENCE_ID") == "architect",
              "(B) host must carry the id that scopes self/<id>/*")


# ── C. data_sources.resolve_session_jsonl: pin wins; ambiguity REFUSED; single is used ─────────
def check_inprocess_resolver(check) -> None:
    ds = _load_data_sources()
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        own_id, own_path, sib_id, sib_path = _two_sessions(d)
        ds._PROJ_DIR = d                                    # point the resolver at the collision dir
        import io

        # (C1) two unpinned .jsonl -> REFUSE (None), never newest-wins the sibling.
        buf = io.StringIO()
        got = ds.resolve_session_jsonl(None, log=buf)
        check(got is None,
              f"(C1) resolver must REFUSE (None) among 2 unpinned .jsonl, got {got!r} "
              f"(newest-wins would have given the SIBLING {sib_path})")
        check("REFUSING" in buf.getvalue(),
              f"(C1) refusal must be LOUD. log: {buf.getvalue()!r}")

        # (C2) an explicit pin is taken verbatim, even with the sibling present + newer.
        buf2 = io.StringIO()
        check(ds.resolve_session_jsonl(own_path, log=buf2) == own_path,
              "(C2) resolver must take the PASSED pin")
        os.environ["COMPOSEIO_SESSION_JSONL"] = own_path
        check(ds.resolve_session_jsonl(None, log=io.StringIO()) == own_path,
              "(C2) resolver must take the env COMPOSEIO_SESSION_JSONL pin")
        os.environ.pop("COMPOSEIO_SESSION_JSONL", None)

    # (C3) a single unambiguous .jsonl (none pinned) is usable — no false refusal.
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        only = os.path.join(d, "cccccccc-2222-2222-2222-222222222222.jsonl")
        with open(only, "w") as fh:
            fh.write('{"type":"assistant"}\n')
        ds._PROJ_DIR = d
        import io
        check(ds.resolve_session_jsonl(None, log=io.StringIO()) == only,
              "(C3) a single unambiguous .jsonl must be used")

    # (C4) CLAUDE_CODE_SESSION_ID resolves THIS session amid a concurrent sibling —
    # the durable pin-by-construction leg ([DEC-TRANSCRIPT-SOURCES-BREAK-AT-HANDOVER-SEAM]):
    # an UNPINNED console launch with >1 transcript no longer goes dark, it resolves
    # its OWN transcript by the harness-set unique id. Deterministic, NOT ls -t.
    with tempfile.TemporaryDirectory() as d, _EnvGuard():
        import io
        own_id = "dddddddd-3333-3333-3333-333333333333"
        own_slug = os.path.join(d, "-Users-you-proj")
        os.makedirs(own_slug)
        own = os.path.join(own_slug, f"{own_id}.jsonl")
        with open(own, "w") as fh:
            fh.write('{"type":"assistant"}\n')
        sib_slug = os.path.join(d, "-Users-you-other")
        os.makedirs(sib_slug)
        with open(os.path.join(sib_slug, "eeeeeeee-4444-4444-4444-444444444444.jsonl"),
                  "w") as fh:
            fh.write('{"type":"assistant"}\n')
        _saved_root, _saved_proj = ds._PROJECTS_ROOT, ds._PROJ_DIR
        ds._PROJECTS_ROOT = d
        ds._PROJ_DIR = d                                    # top-level has no .jsonl
        os.environ["CLAUDE_CODE_SESSION_ID"] = own_id
        try:
            check(ds.resolve_session_jsonl(None, log=io.StringIO()) == own,
                  "(C4) CLAUDE_CODE_SESSION_ID resolves OWN transcript amid a sibling")
            # (C5) an id matching NOTHING must still fall through to refuse, never ls -t.
            os.environ["CLAUDE_CODE_SESSION_ID"] = "ffffffff-5555-5555-5555-555555555555"
            ds._PROJ_DIR = own_slug  # holds exactly one -> single-unambiguous path, still fine
            # point the refuse-scan at a dir with >1 to prove no newest-wins guess
            ds._PROJ_DIR = d
            with open(os.path.join(d, "gggggggg-6666-6666-6666-666666666666.jsonl"),
                      "w") as fh:
                fh.write("{}")
            with open(os.path.join(d, "hhhhhhhh-7777-7777-7777-777777777777.jsonl"),
                      "w") as fh:
                fh.write("{}")
            buf5 = io.StringIO()
            check(ds.resolve_session_jsonl(None, log=buf5) is None
                  and "REFUSING" in buf5.getvalue(),
                  "(C5) unmatched id + >1 unpinned still REFUSES (no ls -t)")
        finally:
            ds._PROJECTS_ROOT, ds._PROJ_DIR = _saved_root, _saved_proj


# ── D. start-console-daemons.sh: honor pin, refuse ambiguity, refuse co-run beside a console ───
def _run_script(env: dict, *, capture: bool = True) -> subprocess.CompletedProcess:
    # DAEMON_CAPTURE short-circuits each launch() to emit a DAEMON_SPEC line instead of spawning a
    # real python daemon, so the script runs offline; the ownership/selection logic under test runs
    # BEFORE any launch(). It ALSO (by design) skips the co-run guard — introspection never
    # launches a rival — so the SELECTION checks (which set capture=True) are isolated from the
    # guard and immune to a real console being up during this test run. The co-run-guard check sets
    # capture=False so the guard actually evaluates; it exits BEFORE any launch(), so no real daemon
    # is ever spawned when the guard fires (a bogus MQTT is passed as belt-and-suspenders).
    full = dict(os.environ)
    for k in _PIN_VARS:
        full.pop(k, None)
    full.update(env)
    if capture:
        full["DAEMON_CAPTURE"] = "1"
    else:
        full.setdefault("MQTT", "mqtt://127.0.0.1:1")   # unreachable — a leaked launch can't connect
    return subprocess.run(["bash", SCRIPT], env=full, capture_output=True, text=True, timeout=60)


def check_script_selection_and_coRun_guard(check) -> None:
    with tempfile.TemporaryDirectory() as home:
        proj = os.path.join(home, ".claude", "projects", home.replace(os.sep, "-"))   # the script's HOME-derived slug
        os.makedirs(proj)
        own_id, own_path, sib_id, sib_path = _two_sessions(proj)

        # (D1) two unpinned .jsonl -> LOUD REFUSAL (exit != 0), never a newest-wins pick.
        r = _run_script({"HOME": home, "FORCE": "1"})
        check(r.returncode != 0,
              f"(D1) script must REFUSE (exit!=0) to guess among 2 unpinned .jsonl; rc={r.returncode}")
        check("REFUSING to guess" in r.stderr,
              f"(D1) refusal must be LOUD + named. stderr tail: {r.stderr[-300:]!r}")
        check(sib_path not in r.stdout, "(D1) script must not have selected the sibling transcript")

        # (D2) pinned -> honored + announced, even with the sibling present + newer.
        r2 = _run_script({"HOME": home, "FORCE": "1", "COMPOSEIO_SESSION_JSONL": own_path})
        check(r2.returncode == 0, f"(D2) pinned run must succeed; rc={r2.returncode} err={r2.stderr[-200:]!r}")
        check(own_path in r2.stdout and sib_path not in r2.stdout,
              f"(D2) script must instrument the PINNED transcript only. stdout: {r2.stdout[-300:]!r}")

    # (D3) an UNAMBIGUOUS single .jsonl is usable (no false refusal).
    with tempfile.TemporaryDirectory() as home:
        proj = os.path.join(home, ".claude", "projects", home.replace(os.sep, "-"))   # the script's HOME-derived slug
        os.makedirs(proj)
        only = os.path.join(proj, "cccccccc-2222-2222-2222-222222222222.jsonl")
        with open(only, "w") as fh:
            fh.write('{"type":"assistant"}\n')
        r3 = _run_script({"HOME": home, "FORCE": "1"})
        check(r3.returncode == 0, f"(D3) single unambiguous .jsonl usable; rc={r3.returncode} err={r3.stderr[-200:]!r}")
        check(only in r3.stdout, "(D3) the lone transcript must be selected")

    # (D4/D5) a --transports console already up: the co-run guard + its interaction with FORCE and
    # DAEMON_CAPTURE (introspection must NOT be blocked, else restart-daemon.sh breaks).
    fake = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)",
         "composeio.render.engine.runtime", "some_scene", "--transports"])
    try:
        time.sleep(0.4)                                     # let it show up to pgrep
        with tempfile.TemporaryDirectory() as home:
            proj = os.path.join(home, ".claude", "projects", home.replace(os.sep, "-"))   # the script's HOME-derived slug
            os.makedirs(proj)
            only = os.path.join(proj, "dddddddd-3333-3333-3333-333333333333.jsonl")
            with open(only, "w") as fh:
                fh.write('{"type":"assistant"}\n')
            # (D4) pin set so the ONLY reason to refuse is the co-run guard; NO DAEMON_CAPTURE so the
            # guard actually evaluates. It exits before any launch(), so no real daemon spawns.
            r = _run_script({"HOME": home, "COMPOSEIO_SESSION_JSONL": only}, capture=False)
            check(r.returncode != 0,
                  f"(D4) script must REFUSE to co-run beside a --transports console; rc={r.returncode}")
            check("in-process console" in r.stderr,
                  f"(D4) co-run refusal must name the in-process console. stderr: {r.stderr[-300:]!r}")
            # (D4b) FORCE=1 overrides the guard (deliberate headless run); DAEMON_CAPTURE keeps it offline.
            r_forced = _run_script({"HOME": home, "COMPOSEIO_SESSION_JSONL": only, "FORCE": "1"})
            check(r_forced.returncode == 0,
                  f"(D4b) FORCE=1 must override the co-run guard; rc={r_forced.returncode}")
            # (D5) DAEMON_CAPTURE introspection must NOT be blocked by the guard even with a console up
            # (restart-daemon.sh reads the canonical roster this way); it emits DAEMON_SPEC lines.
            r_cap = _run_script({"HOME": home, "COMPOSEIO_SESSION_JSONL": only})   # DAEMON_CAPTURE on
            check(r_cap.returncode == 0,
                  f"(D5) DAEMON_CAPTURE introspection must not be blocked by the guard; rc={r_cap.returncode}")
            check("DAEMON_SPEC" in r_cap.stdout,
                  f"(D5) introspection must emit the canonical daemon roster. stdout: {r_cap.stdout[-200:]!r}")
    finally:
        fake.terminate()
        try:
            fake.wait(timeout=5)
        except Exception:
            fake.kill()


_CASES = (
    ("launcher-root-rehost", check_launcher_pins_passed_id),
    ("host-spawn", check_host_carries_pin),
    ("data_sources-inprocess-resolver", check_inprocess_resolver),
    ("start-console-daemons.sh selection + co-run guard", check_script_selection_and_coRun_guard),
)


# ── pytest entry points (parametrized over the launch paths) ───────────────────────────────────
def test_launcher_pins_passed_id():
    _assert_case(check_launcher_pins_passed_id)


def test_host_carries_pin():
    _assert_case(check_host_carries_pin)


def test_inprocess_resolver_refuses_guess():
    _assert_case(check_inprocess_resolver)


def test_script_selection_and_coRun_guard():
    _assert_case(check_script_selection_and_coRun_guard)


def _assert_case(fn):
    fails: list[str] = []
    fn(lambda cond, msg: None if cond else fails.append(msg))
    assert not fails, "\n".join(fails)


# ── selftest entry point (repo idiom) ──────────────────────────────────────────────────────────
def _selftest() -> int:
    fails: list[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    for name, fn in _CASES:
        fn(check)
        sys.stderr.write(f"  ran {name}\n")

    if fails:
        sys.stderr.write(f"\n{len(fails)} FAILURE(S)\n")
        return 1
    print(f"PASS: one-owner + identity-passed-not-guessed across {len(_CASES)} sites")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
