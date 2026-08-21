"""Tests for `scripts/pre-commit-tests.sh` — THE GATE MUST BE INVISIBLE WHILE IT PASSES.

WHY THIS FILE EXISTS (2026-08-12). The hook runs the render suite inside `git commit`, and it did
not export `SDL_VIDEODRIVER`. The display-guarded render tests therefore created REAL `SkiaWindow`s
on the operator's desktop: windows appearing and vanishing during a routine commit, which at a
glance is indistinguishable from a crash loop. A test gate that a human has to interpret is a test
gate that costs more than it saves.

THE FIX AND ITS CONSTRAINT, both pinned here:
  * the hook DEFAULTS `SDL_VIDEODRIVER=dummy`, so nothing is drawn to a screen;
  * it NEVER CLOBBERS a caller who set it deliberately (a renderer being debugged against a real
    window, a CI image pinning its own driver). Overriding a stated requirement silently is the
    same defect with better manners.

⚠️ THE PROOF IS BEHAVIOURAL, NOT TEXTUAL. Asserting that the line is in the file proves the line
is in the file. The tests below run the REAL script against a hermetic throwaway repo with a fake
`$PYTHON` that records the environment it was launched with — so what is asserted is that the
variable reached THE PYTEST CHILD, which is the only thing that was ever wrong.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "pre-commit-tests.sh")

_GIT = shutil.which("git")
_BASH = shutil.which("bash")
needs_tools = pytest.mark.skipif(
    not (_GIT and _BASH), reason="git and bash are required to run the real hook hermetically")


def _read_script() -> str:
    with open(SCRIPT, "r", encoding="utf-8") as fh:
        return fh.read()


def _hermetic_repo(tmp_path):
    """A throwaway git repo carrying a COPY of the real hook and one python change to gate.

    The hook derives REPO_ROOT from its own BASH_SOURCE, so a copy placed in `<tmp>/scripts/`
    gates <tmp> — nothing here can touch the real repository, and the range is ours to control.
    """
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    (root / "python").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / "pre-commit-tests.sh")
    os.chmod(root / "scripts" / "pre-commit-tests.sh", 0o755)
    # Arm 0 (the .lts property gate) REFUSES to run if its script is absent — correctly, and
    # loudly. Carry it across so this test exercises the hook as it really is rather than a
    # crippled variant of it.
    lts_gate = os.path.join(REPO_ROOT, "scripts", "lts_property_gate.py")
    if os.path.isfile(lts_gate):
        shutil.copy2(lts_gate, root / "scripts" / "lts_property_gate.py")

    env = dict(os.environ)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
                "HOME": str(tmp_path)})
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)

    def git(*args):
        subprocess.run([_GIT, "-C", str(root), *args], check=True, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    git("init", "-q")
    git("config", "user.email", "gate@example.invalid")
    git("config", "user.name", "gate")
    (root / "README").write_text("seed\n", encoding="utf-8")
    git("add", "README")
    git("commit", "-q", "-m", "seed", "--no-verify")
    (root / "python" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    git("add", "python/mod.py")
    git("commit", "-q", "-m", "a python change", "--no-verify")
    return root, env


def _fake_python(tmp_path, record):
    """A stand-in for `$PYTHON` that records the SDL_VIDEODRIVER of every PYTEST launch.

    Records only for `-m pytest`: the hook also runs `"$PY" <lts_property_gate>` as arm 0, which
    draws nothing and legitimately precedes the headless default. The claim under test is
    narrower and exact — the variable reached THE PYTEST CHILD.

    `UNSET` is written when the variable is absent, so the pre-fix condition is DISTINGUISHABLE
    from every post-fix value rather than merely 'not dummy'.
    """
    fake = tmp_path / "fake-python"
    fake.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-m" ] && [ "$2" = "pytest" ]; then\n'
        f'  printf "%s\\n" "${{SDL_VIDEODRIVER-UNSET}}" >> "{record}"\n'
        "fi\n"
        "exit 0\n",
        encoding="utf-8")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake


def _run_gate(tmp_path, sdl_value):
    """Run the REAL hook over a python-touching range; return (stdout, [values the child saw])."""
    root, env = _hermetic_repo(tmp_path)
    record = tmp_path / "seen.txt"
    env["PYTHON"] = str(_fake_python(tmp_path, record))
    env.pop("SDL_VIDEODRIVER", None)
    if sdl_value is not None:
        env["SDL_VIDEODRIVER"] = sdl_value
    proc = subprocess.run(
        [_BASH, str(root / "scripts" / "pre-commit-tests.sh"), "--range", "HEAD~1..HEAD"],
        env=env, capture_output=True, text=True, cwd=str(tmp_path))
    seen = record.read_text(encoding="utf-8").split() if record.exists() else []
    assert proc.returncode == 0, (
        f"the hermetic gate run failed (rc={proc.returncode}).\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    assert seen, (
        "the fake $PYTHON was never launched, so this test measured NOTHING. The hook's python "
        f"arm did not fire for the range.\nstdout:\n{proc.stdout}")
    return proc.stdout, seen


# ══ 1. BEHAVIOURAL — the variable reaches the pytest child ═════════════════

@needs_tools
def test_the_pytest_child_is_launched_HEADLESS_when_the_caller_said_nothing(tmp_path):
    """The defect, exactly: with SDL_VIDEODRIVER unset in the caller's environment, the child
    the hook forks must still be headless. Before the fix the child saw UNSET and opened real
    windows on the human's desktop mid-commit."""
    out, seen = _run_gate(tmp_path, None)
    assert set(seen) == {"dummy"}, (
        f"the pytest child saw SDL_VIDEODRIVER={seen} — a real window would open during a "
        f"routine `git commit`.\nhook output:\n{out}")
    assert "SDL_VIDEODRIVER=dummy" in out, (
        "the hook did not ANNOUNCE its headless decision; the run's own output must carry the "
        "evidence, or every future check of this is an archaeology exercise")


@needs_tools
def test_a_DELIBERATE_driver_is_never_clobbered(tmp_path):
    """A caller who exported a driver is stating a requirement. Overriding it would be the same
    class of defect — the environment saying one thing and the process doing another."""
    out, seen = _run_gate(tmp_path, "x11")
    assert set(seen) == {"x11"}, (
        f"the hook overrode a caller-set SDL_VIDEODRIVER: the child saw {seen}, not x11.\n"
        f"hook output:\n{out}")
    assert "SDL_VIDEODRIVER=x11" in out and "INHERITED" in out, (
        f"the hook must say WHOSE value it used, so 'no window opened' and 'the gate forced it' "
        f"are never confused.\nhook output:\n{out}")


# ══ 2. STRUCTURAL — the export cannot drift below the tests it protects ════

def test_the_headless_default_is_set_BEFORE_every_pytest_invocation():
    """Ordering, not presence. An export that lands after a pytest arm protects nothing, and the
    diff that moves it would look harmless."""
    text = _read_script()
    export_at = text.find("export SDL_VIDEODRIVER=dummy")
    assert export_at != -1, "the hook no longer defaults SDL_VIDEODRIVER — windows will return"
    firsts = [text.find(needle) for needle in ('"$PY" -m pytest', '"$PY" -m "$mod"')]
    firsts = [i for i in firsts if i != -1]
    assert firsts, (
        "no `\"$PY\" -m ...` invocation was found; the script's shape changed and this test can "
        "no longer read it — it must fail here rather than pass over nothing")
    assert export_at < min(firsts), (
        "SDL_VIDEODRIVER is exported AFTER a test child is launched — the arms above it still "
        "open real windows")


def test_the_default_uses_the_NO_CLOBBER_form():
    text = _read_script()
    assert '[ -z "${SDL_VIDEODRIVER:-}" ]' in text, (
        "the hook must default SDL_VIDEODRIVER only when UNSET; an unconditional assignment "
        "silently overrides a caller who asked for a real driver")
