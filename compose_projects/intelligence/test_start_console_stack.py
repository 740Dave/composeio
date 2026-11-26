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


# ── the guard, EXECUTED — not string-matched ──────────────────────────────────
# DEFECT-TWO-CONSOLES-SPAWNED-THEN-KILLED, David 2026-08-15. The test above asserts
# a TOKEN IS PRESENT IN A FILE; it cannot fail for any behaviour the guard actually
# has, which is how a guard that reads "I could not tell" as "nothing is running"
# shipped and put a third console on top of two. These RUN it.

def _surface_absent(pattern: str, extra_flags: str = "") -> int:
    """Source the bringup's REAL guard and invoke it. Returns the shell exit status:
    0 == "absent, launch", nonzero == "do not launch".

    `extra_flags` is injected into the pgrep the guard calls, which is how the
    unanswerable-probe case is reproduced without editing the shipped script."""
    import subprocess
    script = (
        'set -uo pipefail\n'
        # the function DEFINITION only — running the script's body would launch things
        f'eval "$(sed -n \'/^surface_absent()/,/^}}/p\' {BRINGUP})"\n'
        f'pgrep() {{ command pgrep {extra_flags} "$@"; }}\n'
        f'surface_absent "{pattern}"\n')
    return subprocess.run(["/bin/bash", "-c", script],
                          capture_output=True, text=True).returncode


def test_guard_says_absent_only_when_pgrep_affirmatively_says_so():
    """Exit 1 from pgrep — and ONLY exit 1 — means 'nothing is running'."""
    import uuid
    assert _surface_absent(f"definitely-not-running-{uuid.uuid4().hex}") == 0, (
        "an affirmatively-absent surface must be launched")


def test_guard_declines_to_launch_on_a_LIVE_surface():
    import subprocess
    import sys as _sys
    import time
    import uuid
    marker = f"console-stack-guard-test-{uuid.uuid4().hex}"
    p = subprocess.Popen([_sys.executable, "-c", "import sys,time; time.sleep(6)", marker],
                         start_new_session=True)
    try:
        time.sleep(1.0)
        assert _surface_absent(marker) != 0, "a LIVE surface must not be relaunched"
    finally:
        p.kill()
        p.wait(timeout=3)


def test_guard_refuses_an_UNANSWERABLE_probe_instead_of_reading_it_as_absent():
    """THE DEFECT, on the bench. `pgrep -c` is a Linux flag; macOS pgrep exits 2 with
    a usage message. The old `if ! pgrep …` read that exit as "nothing is running"
    and launched a duplicate — David measured exactly this on 2026-08-15, reporting
    zero consoles while two were live. An instrument that can only report SUCCESS is
    not an instrument, and neither is one that can only report ABSENCE."""
    import uuid
    marker = f"unanswerable-{uuid.uuid4().hex}"
    rc = _surface_absent(marker, extra_flags="-c")      # the broken probe, verbatim
    assert rc != 0, (
        "an UNANSWERABLE probe must NOT be read as 'nothing is running' — that read "
        "is what launched a third console on top of two")


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


def _selftest() -> int:
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
    print(f"PASS: start-console-stack bringup ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
