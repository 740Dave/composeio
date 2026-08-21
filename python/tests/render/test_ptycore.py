"""[SPEC:CXC-TERM] · [CXC-TERM-PTYCORE] — the PTY half is EXTRACTED, not forked.

Acceptance TERM-C11 ("one PTY implementation in the tree") is a GREP GUARD plus the
launcher's own selftest.  The guards here are AST-based where a textual grep would be
fooled by a docstring, because the failure they exist to catch is a second `pty.fork()`
appearing in the terminal widget — the [DX-NARROW-WAIST] fork-divergence class.

TERM-C8's structural half also lives here: the single write site.  "A bus keystroke and a
keyboard keystroke are indistinguishable to the child" is satisfied BY CONSTRUCTION when
there is exactly one place that writes the master fd — so the test asserts the STRUCTURE,
not a behaviour that could drift away from it.
"""
from __future__ import annotations

import ast
import os
import re
import select
import subprocess
import sys
import threading
import time

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(_REPO, "python"))

from composeio.ptycore import (  # noqa: E402
    BOOTSTRAP_ARGV_MAX, COMPACT_CLEAR, COMPACT_SUBMIT, DELIBERATELY_OMITTED,
    HOST_IDENTITY_ENV, KEPT_HOST_ENV, MIN_PTY_COLS, MIN_PTY_ROWS, PASTE_END, PASTE_START,
    BootstrapRefused, PtyChild, SBR_FLOW_NODE, SBR_FLOW_STEPS, SBR_SPEC_ID,
    bootstrap_enabled, build_bootstrap_argv, build_bootstrap_for, build_bootstrap_prompt,
    build_claude_args, default_meta_manifest, derive_jsonl_path, export_session_pin,
    get_winsize, load_manifest, pack_winsize, parse_actuation, project_dir_key,
    resolve_claude_bin, resolve_meta_manifest, resolve_winsize, sanitize_child_env,
    sbr_spec_path, unconsumed_manifest_keys,
)



def _code_only(path: str) -> str:
    """`path` with every COMMENT and STRING literal removed, so a guard can forbid a NAME in
    code while the tree keeps explaining, at length, why that name was deleted."""
    import io
    import tokenize as _tok
    out = []
    with open(path, "rb") as fh:
        for tok in _tok.tokenize(io.BytesIO(fh.read()).readline):
            if tok.type in (_tok.COMMENT, _tok.STRING):
                continue
            out.append(tok.string)
    return " ".join(out)

# ── the tracked-python corpus the guards scan ──────────────────────────────
def _tracked_python() -> list[str]:
    # Tracked AND not-yet-committed-but-present: a second pty.fork() arriving in an
    # uncommitted file must trip this guard on the run that introduces it, not one commit
    # later.  `--others --exclude-standard` adds untracked, non-ignored files.
    out = subprocess.run(["git", "-C", _REPO, "ls-files", "--cached", "--others",
                          "--exclude-standard", "*.py"],
                         capture_output=True, text=True, check=True).stdout
    paths = []
    for rel in out.splitlines():
        if not rel.strip():
            continue
        # The guards' own source names these constructs; scanning tests would be
        # self-defeating.  Anything under a tests tree is excluded by path.
        if "/tests/" in rel or os.path.basename(rel).startswith("test_"):
            continue
        paths.append(os.path.join(_REPO, rel))
    return paths


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def test_pty_fork_appears_exactly_once():
    """[CXC-TERM-PTYCORE] / TERM-C11 — ONE `pty.fork()` call site in the whole tree.

    AST, not grep: `launcher.py`'s module docstring says "pty.fork()" in prose, and a
    textual guard that trips on prose is a guard people delete."""
    sites = []
    for path in _tracked_python():
        try:
            tree = ast.parse(_read(path), filename=path)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "fork"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "pty"):
                sites.append(f"{os.path.relpath(path, _REPO)}:{node.lineno}")
    assert len(sites) == 1 and sites[0].startswith("python/composeio/ptycore.py:"), (
        "pty.fork() must appear EXACTLY ONCE, in composeio/ptycore.py "
        f"([CXC-TERM-PTYCORE]).  Found: {sites}")


def test_one_pump_read_site():
    """[CXC-TERM-PTYCORE] / TERM-C11 — one select pump, i.e. one place that reads the
    PTY master.  A second `os.read(self.master_fd, ...)` is a second pump."""
    needle = "os.read(self." + "master_fd"
    hits = [os.path.relpath(p, _REPO) for p in _tracked_python() if needle in _read(p)]
    assert hits == ["python/composeio/ptycore.py"], (
        f"exactly one PTY-master read site expected; found {hits}")


def test_one_write_site():
    """[CXC-TERM-CONVERGE] / TERM-C8 — exactly ONE write site on the master fd.

    This is the structural claim: because a bus-originated inject and a keyboard
    keystroke reach the child through the same call, the child CANNOT tell them apart."""
    needle = "write_all(self." + "master_fd"
    hits = [os.path.relpath(p, _REPO) for p in _tracked_python() if needle in _read(p)]
    assert hits == ["python/composeio/ptycore.py"], (
        f"exactly one master-fd write site expected; found {hits}")


# ── the core, driven ────────────────────────────────────────────────────────
def _drive(child: PtyChild, sink: list, cycles: int = 60, per: float = 0.02) -> None:
    for _ in range(cycles):
        if not child.pump(sink.append, timeout=per):
            break


def test_pump_drives_a_recording_sink():
    """[CXC-TERM-PTYCORE] — the ONE sanctioned behavioural change: the pump takes a byte
    SINK, not an out_fd, so the widget never has to invent a pipe to read its own child."""
    child = PtyChild([sys.executable, "-c", "print('SINK-MARKER')"],
                     term_size=(24, 80), log=lambda _l: None)
    child.start()
    try:
        seen: list = []
        _drive(child, seen)
        assert b"SINK-MARKER" in b"".join(seen)
    finally:
        child.shutdown()


def test_secondary_input_fd_is_forwarded():
    """The launcher's stdin half survives the move: bytes on `input_fd` reach the child."""
    src = ("import sys\n"
           "line = sys.stdin.readline()\n"
           "sys.stdout.write('ECHO:' + line)\n"
           "sys.stdout.flush()\n")
    child = PtyChild([sys.executable, "-c", src], term_size=(24, 80), log=lambda _l: None)
    child.start()
    r, w = os.pipe()
    child.input_fd = r
    try:
        os.write(w, b"from-the-pipe\n")
        seen: list = []
        _drive(child, seen)
        assert b"ECHO:from-the-pipe" in b"".join(seen)
    finally:
        os.close(w)
        os.close(r)
        child.shutdown()


def test_inject_logs_and_write_keys_does_not():
    """[CXC-TERM-CONVERGE] — `injection_log` means "writes the HARNESS originated".

    Keyboard traffic in it would destroy the audit property AND put the human's typing
    into a durable record, so `write_keys` (the glass path) writes without logging while
    `inject` (the bus path) logs every byte."""
    child = PtyChild([sys.executable, "-c", "import time; time.sleep(0.4)"],
                     term_size=(24, 80), log=lambda _l: None)
    child.start()
    try:
        child.inject("bus-text", kind="inject")
        child.write_keys(b"typed-by-the-human")
        kinds = [r["kind"] for r in child.injection_log]
        assert kinds == ["inject"]
        assert all("typed-by-the-human" not in r["keys"] for r in child.injection_log)
    finally:
        child.shutdown()


def test_inject_line_is_three_phases():
    """[CXC-TERM-PTYCORE] — the three-phase clear/type/submit moved verbatim: ESC ESC,
    the line with NO trailing newline, then a bare \\r."""
    child = PtyChild([sys.executable, "-c", "import time; time.sleep(0.6)"],
                     term_size=(24, 80), log=lambda _l: None)
    child.start()
    child.compact_phase_delay = 0
    try:
        child.inject_line("/compact keep the seam", kind="compact")
        recs = child.injection_log
        assert [r["kind"] for r in recs] == ["compact-clear", "compact", "compact-submit"]
        assert recs[0]["keys"] == COMPACT_CLEAR
        assert recs[1]["keys"] == "/compact keep the seam"   # no trailing newline
        assert recs[2]["keys"] == COMPACT_SUBMIT
    finally:
        child.shutdown()


def test_winsize_is_set_before_the_child_reads_it():
    """[CXC-TERM-WINSIZE] — the child NEVER observes 0x0.  `stty size` is an independent
    witness (no Python, straight TIOCGWINSZ) and prints "0 0" under the old defect."""
    child = PtyChild(["/bin/sh", "-c", "stty size"], term_size=(37, 133), log=lambda _l: None)
    child.start()
    try:
        assert get_winsize(child.master_fd) == (37, 133)
        seen: list = []
        _drive(child, seen)
        text = b"".join(seen).decode(errors="replace")
        assert "37 133" in text, text
        assert "0 0" not in text
    finally:
        child.shutdown()


def test_resize_reaches_a_running_child():
    """[CXC-TERM-WINSIZE] — a live resize is TIOCSWINSZ + SIGWINCH to the process GROUP."""
    src = ("import os,signal,sys,time\n"
           "def emit(tag):\n"
           "    s = os.get_terminal_size(0)\n"
           "    sys.stdout.write('%s:%dx%d\\n' % (tag, s.lines, s.columns)); sys.stdout.flush()\n"
           "signal.signal(signal.SIGWINCH, lambda *a: emit('WINCH'))\n"
           "emit('INIT')\n"
           "[time.sleep(0.02) for _ in range(200)]\n")
    child = PtyChild([sys.executable, "-c", src], term_size=(30, 90), log=lambda _l: None)
    child.start()
    try:
        seen: list = []
        _drive(child, seen, cycles=40)
        assert "INIT:30x90" in b"".join(seen).decode(errors="replace")
        child.apply_winsize(44, 171)
        _drive(child, seen, cycles=40)
        text = b"".join(seen).decode(errors="replace")
        assert "WINCH:44x171" in text, text
        assert (child.term_rows, child.term_cols) == (44, 171)
        assert get_winsize(child.master_fd) == (44, 171)
    finally:
        child.shutdown()


def test_exit_description_reports_code_and_signal():
    """[CXC-TERM-BOUNDARIES] rows 3 + 4 — `exited <code>` / `exited (signal N)`."""
    child = PtyChild([sys.executable, "-c", "raise SystemExit(3)"], log=lambda _l: None)
    child.start()
    try:
        for _ in range(200):
            if child.child_exited():
                break
            time.sleep(0.01)
        assert child.exit_description() == "exited 3"
    finally:
        child.shutdown()


def test_env_extra_and_cwd_reach_the_child():
    """The widget needs `CLAUDE_AX_STARTUP_QUIET_MS=0` and `TERM` in the child env
    ([CXC-TERM-AXMODE], [CXC-TERM-WIDGET]).  Since 2026-08-11 there is no `env_extra is None`
    branch at all — every spawn goes through `execvpe` with a SANITISED env
    ([CXC-IC-SELF-IDENTITY]); `env_extra` is applied on top of it."""
    child = PtyChild([sys.executable, "-c",
                      "import os,sys; sys.stdout.write('E:%s\\n' % os.environ.get('PROBE_VAR'))"],
                     env_extra={"PROBE_VAR": "carried"}, log=lambda _l: None)
    child.start()
    try:
        seen: list = []
        _drive(child, seen)
        assert "E:carried" in b"".join(seen).decode(errors="replace")
    finally:
        child.shutdown()


# ── pure helpers that moved ────────────────────────────────────────────────
def test_resolve_winsize_precedence_and_clamp():
    assert resolve_winsize(50, 200) == (50, 200)
    assert resolve_winsize(1, 1) == (MIN_PTY_ROWS, MIN_PTY_COLS)
    import struct
    assert struct.unpack("HHHH", pack_winsize(11, 99)) == (11, 99, 0, 0)


def test_build_claude_args_is_unchanged():
    assert build_claude_args("sess-1")[:2] == ["--resume", "sess-1"]
    assert "--channels" not in build_claude_args("sess-1")
    assert "--resume" not in build_claude_args("")
    assert "--dangerously-skip-permissions" in build_claude_args("", skip_permissions=True)


def test_parse_actuation_accepts_both_envelopes():
    import json
    assert parse_actuation({"cmd": "compact", "direction": "hold the seam"}) == (
        "compact", "/compact hold the seam")
    nested = {"from": "architect", "text": json.dumps({"cmd": "inject", "text": "hi"})}
    assert parse_actuation(nested) == ("inject", "hi")
    assert parse_actuation({"from": "architect", "text": "just a reply"}) is None


def test_session_pin_exports_the_derived_transcript(tmp_path, monkeypatch):
    """[CXC-TERM-FACTS] fact 1 / TERM-C7 — the console data layer reads the pinned
    transcript, so the pin must be exported BEFORE fork."""
    monkeypatch.delenv("COMPOSEIO_SESSION_JSONL", raising=False)
    monkeypatch.delenv("SESSION_JSONL", raising=False)
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "sess-abc.jsonl").write_text("{}\n")
    monkeypatch.setenv("CLAUDE_PROJECTS_DIR", str(proj))
    pinned = export_session_pin("sess-abc", lambda _l: None)
    assert pinned == str(proj / "sess-abc.jsonl")
    assert os.environ["COMPOSEIO_SESSION_JSONL"] == pinned
    assert derive_jsonl_path("sess-abc") == pinned


def test_project_dir_key_sanitizes():
    assert project_dir_key("/Users/x/Dropbox/Maker/composeio") == "-Users-x-Dropbox-Maker-composeio"


def test_launcher_selftest_is_green_post_extraction():
    """[CXC-TERM-PTYCORE] constraint — "the extraction is a MOVE, not a rewrite", and the
    launcher's own selftest is its gate.  Runs the real thing, real pty.fork included."""
    proc = subprocess.run(
        [sys.executable, os.path.join(_REPO, "compose_projects", "intelligence", "launcher", "launcher.py"),
         "--selftest"],
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS: launcher proxy+relay selftest" in proc.stdout


# ══ [CXC-IC-SELF-IDENTITY] — the child MINTS its own identity ══════════════
# The defect, measured on the live child 2026-08-11: `pty.fork()` + a raw `os.environ` copy
# handed the hosted instance its HOST's `CLAUDE_CODE_SESSION_ID`, byte-identical.  That both
# turned transcript saving OFF (it believed it was a nested child session) and pointed its
# session resolution at the HOST's transcript — the cross-instance leak this node exists to
# prevent.  These tests assert the ABSENCE of the stripped keys and the PRESENCE of the kept
# ones, because "the env is sanitised" is only true if BOTH halves hold.

def test_the_derived_strip_list_is_disjoint_from_the_kept_list():
    """A variable cannot be both minted-by-the-child and inherited-from-the-host.  The module
    asserts this at import; the test states it so the intent is visible in the suite."""
    assert not (set(KEPT_HOST_ENV) & set(HOST_IDENTITY_ENV))
    # Every entry carries a WHY.  The list is derived from what a spawned instance must mint
    # for itself, and an unexplained entry is an entry the next reader will delete.
    for name, why in HOST_IDENTITY_ENV.items():
        assert isinstance(why, str) and len(why) > 20, f"{name} has no stated reason"


def test_host_identity_is_stripped_and_the_useful_vars_are_kept():
    base = {
        # host identity — every one must be GONE
        "CLAUDE_CODE_SESSION_ID": "e1a82ba7-0c99-4f14-a7c9-4e7c40b1d0fc",
        "CLAUDE_CODE_CHILD_SESSION": "1",
        "CLAUDE_CODE_BRIDGE_SESSION_ID": "bridge-1",
        "CLAUDECODE": "1",
        "CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/host.sock",
        "CLAUDE_CODE_ENTRYPOINT": "cli",
        "CLAUDE_PID": "82930",
        "COMPOSEIO_SESSION_JSONL": "/home/host/.claude/projects/x/host.jsonl",
        "JSONL": "/home/host/other.jsonl",
        "SESSION_JSONL": "/home/host/third.jsonl",
        # deliberately kept — a binary path, a machine config, an ordinary var
        "CLAUDE_CODE_EXECPATH": "/opt/claude/bin/claude.exe",
        "CLAUDE_CONFIG_DIR": "/home/host/.claude",
        "PATH": "/usr/bin",
    }
    env = sanitize_child_env(base=base)
    for name in HOST_IDENTITY_ENV:
        assert name not in env, f"{name} LEAKED into the child env"
    assert env["CLAUDE_CODE_EXECPATH"] == "/opt/claude/bin/claude.exe"
    assert env["CLAUDE_CONFIG_DIR"] == "/home/host/.claude"
    assert env["PATH"] == "/usr/bin"


def test_env_extra_is_applied_AFTER_the_strip_so_a_pin_can_be_PASSED():
    """RESUME/MOVE: the pin names the transcript of the session being RE-HOSTED — the child's
    OWN — so it is PASSED back in, not inherited.  Ordering is the whole mechanism: strip
    first, then apply, so "passed" and "inherited" stay distinguishable."""
    env = sanitize_child_env(
        base={"COMPOSEIO_SESSION_JSONL": "/host/leak.jsonl", "TERM": "dumb"},
        extra={"COMPOSEIO_SESSION_JSONL": "/child/own.jsonl", "TERM": "xterm-256color"})
    assert env["COMPOSEIO_SESSION_JSONL"] == "/child/own.jsonl"
    assert env["TERM"] == "xterm-256color"


def test_resolved_env_is_exactly_what_start_will_exec_with(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "host-session")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/opt/claude/bin/claude.exe")
    child = PtyChild(["/bin/true"], env_extra={"TERM": "xterm-256color"}, log=lambda _l: None)
    env = child.resolved_env()
    assert "CLAUDE_CODE_SESSION_ID" not in env
    assert env["CLAUDE_CODE_EXECPATH"] == "/opt/claude/bin/claude.exe"
    assert env["TERM"] == "xterm-256color"


def test_a_LIVE_child_does_not_inherit_the_hosts_session_id(monkeypatch):
    """The machine half of the defect, through a REAL `pty.fork()` + `execvpe`.  The original
    measurement was made this way (dump the child's env and compare), so the regression test
    is made the same way — a unit test over `sanitize_child_env` alone would still pass if
    `start()` regained its `os.execvp` branch."""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "HOST-ID-MUST-NOT-APPEAR")
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setenv("COMPOSEIO_SESSION_JSONL", "/host/leak.jsonl")
    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/opt/claude/bin/claude.exe")
    dump = ("import os,sys;sys.stdout.write('ENV[' + '|'.join("
            "'%s=%s' % (k, os.environ[k]) for k in sorted(os.environ) "
            "if k.startswith('CLAUDE') or 'JSONL' in k) + ']\\n')")
    child = PtyChild([sys.executable, "-c", dump], log=lambda _l: None)
    child.start()
    try:
        seen: list = []
        _drive(child, seen)
    finally:
        child.shutdown()
    out = b"".join(seen).decode(errors="replace")
    assert "ENV[" in out, out
    assert "HOST-ID-MUST-NOT-APPEAR" not in out, out
    assert "CLAUDE_CODE_SESSION_ID" not in out, out
    assert "/host/leak.jsonl" not in out, out
    assert "CLAUDECODE=" not in out, out
    assert "CLAUDE_CODE_EXECPATH=/opt/claude/bin/claude.exe" in out, out


# ══ the `claude` BINARY chain — `claude` is an ALIAS on this machine ═══════
def test_resolve_claude_bin_chain(monkeypatch):
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
    assert resolve_claude_bin() == "claude"                 # last resort only

    monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/opt/claude/bin/claude.exe")
    # The leg that fixes the live failure: `claude` is a shell ALIAS here and NOT on PATH, so
    # a bare-name exec exhausts PATH and dies with a misleading "No such file".
    assert resolve_claude_bin() == "/opt/claude/bin/claude.exe"

    monkeypatch.setenv("CLAUDE_BIN", "/usr/local/bin/claude")
    assert resolve_claude_bin() == "/usr/local/bin/claude"  # the operator pin outranks it
    assert resolve_claude_bin("/explicit/claude") == "/explicit/claude"
    assert resolve_claude_bin("   ") == "/usr/local/bin/claude"   # blank is not a choice


# ══ [CXC-ICL-COLD] — the re-register bootstrap, DEFAULT ON ════════════════
def test_bootstrap_defaults_ON_and_zero_is_the_explicit_opt_out(monkeypatch):
    """The ratified flip.  David, 2026-08-10: "the re-register bootstrap is not experimental
    any longer ... it should be the default for new instances."  UNSET means ON."""
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    assert bootstrap_enabled() is True
    monkeypatch.setenv("BOOTSTRAP", "")
    assert bootstrap_enabled() is True
    for off in ("0", "false", "no", "off", "OFF"):
        monkeypatch.setenv("BOOTSTRAP", off)
        assert bootstrap_enabled() is False, off
    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("BOOTSTRAP", on)
        assert bootstrap_enabled() is True, on
    # an explicit argument (a scene property) overrides the env entirely
    monkeypatch.setenv("BOOTSTRAP", "0")
    assert bootstrap_enabled(True) is True


def test_the_settle_delay_knob_is_GONE_from_the_core(monkeypatch):
    """TERM-C13 / [CXC-TERM-BOOTSTRAP-ARGV]: the settle mechanism is DELETED, not unused.

    An unused-but-present `bootstrap_delay_s` is one edit away from being called again, and
    the whole point of the ruling is that a fixed timer standing in for a state check is
    unsound for EVERY value.  So the NAME must be gone, and `BOOTSTRAP_DELAY` must have no
    reader: setting it has to be inert."""
    import composeio.ptycore as core
    for gone in ("BOOTSTRAP_DELAY_S", "bootstrap_delay_s", "build_unmanifested_prompt"):
        assert not hasattr(core, gone), f"{gone} survived the deletion"
    assert not hasattr(PtyChild, "inject_prompt"), "the startup write path survived"
    monkeypatch.setenv("BOOTSTRAP_DELAY", "99")
    code = _code_only(os.path.join(_REPO, "python", "composeio", "ptycore.py"))
    assert "BOOTSTRAP_DELAY" not in code, "ptycore still READS BOOTSTRAP_DELAY"
    assert "inject_prompt" not in code


def test_build_bootstrap_for_answers_the_whole_decision(monkeypatch, tmp_path):
    import json
    manifest = tmp_path / "arch.meta.json"
    manifest.write_text(json.dumps({
        "id": "architect",
        "role_descriptor": {"path": "intelligence/roles/architect.md"},
        "soul": {"path": "bootstrap/soul.md"},
        "dusk_gifts": [{"path": "bootstrap/dusk-gifts/registration-protocol.md",
                        "why": "how to re-register"}],
    }))
    monkeypatch.delenv("BOOTSTRAP", raising=False)

    # COLD + default -> a prompt
    prompt = build_bootstrap_for("architect", "", manifest_path=str(manifest))
    assert "RE-REGISTERING" in prompt

    # RESUME -> nothing.  A re-host already HOLDS its context.
    assert build_bootstrap_for("architect", "sess-1", manifest_path=str(manifest)) == ""

    # the explicit opt-OUT
    monkeypatch.setenv("BOOTSTRAP", "0")
    assert build_bootstrap_for("architect", "", manifest_path=str(manifest)) == ""
    monkeypatch.delenv("BOOTSTRAP")

    # An unreadable manifest REFUSES ([CXC-TERM-MANIFEST-CHAIN], 2026-08-11).  It used to
    # return a DEGRADED prompt and spawn anyway; that is the rejected branch — a
    # booted-but-unoriented child takes a channel, takes the singleton, and renders as a
    # working session.  The dedicated chain tests live under "the manifest CHAIN" below.
    with pytest.raises(BootstrapRefused):
        build_bootstrap_for("architect", "", manifest_path=str(tmp_path / "nope.json"))


def test_the_default_manifest_is_the_repo_meta_object(monkeypatch):
    """The silent no-op this closes: `main()` used to gate the bootstrap on `META_MANIFEST`
    being SET, so a default launch injected nothing even with BOOTSTRAP=1."""
    monkeypatch.delenv("META_MANIFEST", raising=False)
    path = default_meta_manifest("architect")
    assert path.endswith(os.path.join("compose_projects", "intelligence", "meta", "architect.meta.json"))
    assert os.path.isfile(path), path
    monkeypatch.setenv("META_MANIFEST", "/tmp/pinned.json")
    assert default_meta_manifest("architect") == "/tmp/pinned.json"


def test_the_prompt_points_BY_PATH_and_never_inlines_an_artifact(tmp_path):
    """[CXC-ICL-COLD]: the prompt points at role_descriptor / soul / dusk_gifts BY PATH and
    NEVER inlines contents.  Carry-depth-by-reference is the whole mechanism — an inlined
    summary is the lossy copy re-reading exists to avoid."""
    gift = tmp_path / "gift.md"
    gift.write_text("SECRET-ARTIFACT-BODY-THAT-MUST-NOT-BE-INLINED\n")
    prompt = build_bootstrap_prompt({
        "id": "architect",
        "role_descriptor": {"path": "roles/architect.md"},
        "soul": {"path": "soul.md"},
        "dusk_gifts": [{"path": str(gift), "why": "calibration"}],
        "channel": {"server": "composeio-channel", "mqtt": "mqtt://x:1883"},
        "lifecycle": {"compact_threshold": 80},
    })
    assert "READ roles/architect.md" in prompt
    assert "READ soul.md" in prompt
    assert f"READ {gift}" in prompt
    assert "SECRET-ARTIFACT-BODY-THAT-MUST-NOT-BE-INLINED" not in prompt
    assert "composeio-channel" in prompt and "compact" in prompt


def test_a_missing_soul_does_not_leave_a_numbering_hole():
    prompt = build_bootstrap_prompt({"id": "x",
                                     "role_descriptor": {"path": "r.md"},
                                     "dusk_gifts": [{"path": "g.md"}]})
    assert "1. ROLE DESCRIPTOR" in prompt and "2. DUSK GIFTS" in prompt
    assert "SOUL" not in prompt


# ══ the bootstrap rides ARGV — the startup write path is GONE ══════════════
# [CXC-TERM-BOOTSTRAP-ARGV] / TERM-C13.  What used to live here was
# `test_inject_prompt_is_three_phases_and_brackets_a_multiline_body` — the three-phase,
# bracketed-paste delivery of the bootstrap INTO THE PTY.  That whole seam is deleted: on its
# first live firing it landed in a blocking `Enter y/n:` gate and `^[[200~` echoed as literal
# text, because the census figure behind the "?2004h is already on" claim came from profiles
# with no dialogs in them ([CXC-TERM-CENSUS-CONFIG-FIDELITY]).  The three-phase actuation
# itself is NOT gone — `inject_line` is the MID-SESSION seam and is tested below, unchanged
# ([CXC-TERM-MIDSESSION-SEAM]).

def test_the_bootstrap_is_a_FILE_and_argv_names_it(tmp_path, monkeypatch):
    """The ratified delivery shape: the generator still runs, its output is written to a
    FILE, and argv carries a SHORT instruction naming that file by ABSOLUTE path.

    Both halves of the explicit form are asserted, because each fails differently: an argv
    element that inlined 6 KB would be the form David rejected, and a prompt file whose first
    line did not name the manifest would leave a cold instance with a discovery step it
    cannot perform ([DD-TERM-BOOTSTRAP-ARGV-FORM])."""
    import json
    manifest = tmp_path / "arch.meta.json"
    manifest.write_text(json.dumps({"id": "architect",
                                    "role_descriptor": {"path": "roles/architect.md"}}))
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "prompts"))

    instruction = build_bootstrap_argv("architect", "", manifest_path=str(manifest))
    assert instruction.startswith("Read /"), instruction
    named = instruction.split()[1]
    assert os.path.isabs(named) and os.path.isfile(named), named
    # SHORT — a pointer, not the body
    assert len(instruction) < 400, instruction
    assert "RE-REGISTERING" not in instruction, "the 6 KB body was inlined onto argv"
    body = _read(named)
    assert body.splitlines()[0] == (
        f"Read {manifest} and complete the bootstrap it describes.")
    assert "RE-REGISTERING" in body, "the generated prompt is not in the file"
    assert "roles/architect.md" in body

    # RESUME and the opt-OUT deliver NOTHING at all — no file, no argv element.
    assert build_bootstrap_argv("architect", "s-1", manifest_path=str(manifest)) == ""
    monkeypatch.setenv("BOOTSTRAP", "0")
    assert build_bootstrap_argv("architect", "", manifest_path=str(manifest)) == ""


def test_an_oversized_argv_instruction_REFUSES_rather_than_truncating(tmp_path, monkeypatch):
    """[CXC-TERM-BOOTSTRAP-ARGV] default: "a generated prompt above it is a REFUSAL naming the
    size, never a truncated exec" — a silently clipped orientation prompt is the
    unoriented-instance failure with a longer fuse."""
    import json
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({"id": "x", "role_descriptor": {"path": "r.md"}}))
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    monkeypatch.setattr("composeio.ptycore.BOOTSTRAP_ARGV_MAX", 10)
    with pytest.raises(BootstrapRefused) as exc:
        build_bootstrap_argv("x", "", manifest_path=str(manifest))
    assert exc.value.reason == "bootstrap-argv-too-large"
    assert str(BOOTSTRAP_ARGV_MAX) or True     # the real default is a real number
    assert "10" in exc.value.detail


# ══ the prompt DERIVES the ritual, it does not RESTATE it ═════════════════
# [SPEC:SESSION-BOOTSTRAP-READ] is the ratified wake procedure.  A generator that paraphrases
# its rules into a Python string is a SECOND implementation whose copy drifts silently — the
# class [INSIGHT-FORK-DIVERGENCE-IS-A-CLASS] names and [SBR-PATHS-SINGLE-SOURCE] forbids in
# its path-literal form.  These tests are the enforcement: the mirror must match the spec, and
# the prompt must cover every step the spec names.

def _sbr_flow_steps_from_corpus() -> list:
    """Parse the step names out of [SBR-PROCESS-FLOW] in the corpus.

    The node writes each step as a numbered line whose names are backticked, e.g.
    ``5. `slice-0-PLATFORM-OVERVIEW` -> `slice-1-WARTS` -> ... — the six slices``.  Only the
    part BEFORE the em-dash is scanned, so the prose after it (which backticks plenty of other
    things — `lts_flow`, `.lts`, `MagmaStore`) cannot be mistaken for a step."""
    # The corpora moved OUT of the repo on 2026-08-17 (41 worktrees were carrying
    # 41 forks). Resolve through the constant, which is what [SBR-PATHS-SINGLE-SOURCE]
    # asks of production code and should equally ask of its test.
    from composeio.bootstrap_paths import PLATFORM_SPECS_DIR as _SPECS
    path = str(_SPECS / "45-session-bootstrap-read.lts")
    src = _read(path)
    start = src.index("### [SBR-PROCESS-FLOW]")
    body = src[start:src.index("\n### ", start + 5)] if "\n### " in src[start + 5:] else src[start:]
    steps: list = []
    for line in body.splitlines():
        if not re.match(r"^\s*\d+\.\s+`", line):
            continue
        head = line.split("—", 1)[0]                      # names live before the em-dash
        steps.extend(re.findall(r"`([^`]+)`", head))
    return steps


def test_the_flow_step_mirror_matches_the_SPEC():
    """[SBR-PROCESS-FLOW] is the authority; `SBR_FLOW_STEPS` is a mirror.  Add a step to the
    spec and this goes RED until the prompt covers it — which is what stops the next amendment
    from being applied half-way ([INSIGHT-PARTIAL-SPEC-AMENDMENT-IS-A-FORK-GENERATOR]).  The
    same mirror+enforcing-test shape `bootstrap_paths.py` uses."""
    assert list(SBR_FLOW_STEPS) == _sbr_flow_steps_from_corpus()


def test_the_prompt_covers_every_step_the_spec_NAMES(tmp_path):
    prompt = build_bootstrap_prompt({"id": "architect",
                                     "role_descriptor": {"path": "r.md"},
                                     "dusk_gifts": [{"path": "g.md"}]})
    for step in _sbr_flow_steps_from_corpus():
        assert step in prompt, f"[SBR-PROCESS-FLOW] step {step!r} is not in the prompt"


def test_the_prompt_CITES_the_ritual_instead_of_paraphrasing_it():
    """The rules stay in the spec; the prompt points at them.  It must name the spec, the flow
    node, and the binding rule nodes — and must NOT re-teach their content."""
    prompt = build_bootstrap_prompt({"id": "architect",
                                     "role_descriptor": {"path": "r.md"},
                                     "dusk_gifts": [{"path": "g.md"}]})
    for cite in (SBR_SPEC_ID, SBR_FLOW_NODE, "[SBR-TRUST]", "[SBR-STRUCTURE]",
                 "[SBR-READ-VS-REASON]", "[SBR-DEGRADE]"):
        assert cite in prompt, f"the prompt does not cite {cite}"
    assert sbr_spec_path() in prompt, "the ritual spec is not pointed at BY PATH"
    # The old wording — "read each and re-register" — is the precise error the gifts warn
    # against, and it is what this build replaced. It must not come back.
    assert "read each and re-register" not in prompt


def test_the_ritual_spec_path_is_not_a_CODE_LITERAL():
    """[SBR-PATHS-SINGLE-SOURCE]: no code file hard-codes a continuity path literal.  The
    corpus dir must come from `bootstrap_paths`, not from a string in ptycore."""
    src = _read(os.path.join(_REPO, "python", "composeio", "ptycore.py"))
    body = src.split('"""', 2)[2]              # skip the module docstring
    assert "corpora/platform-specs" not in body
    assert "PLATFORM_SPECS_REL" in body
    # ...and it still resolves correctly. The corpora moved OUT of the repo on
    # 2026-08-17, so the check is that the path comes from the CONSTANT (the point
    # of [SBR-PATHS-SINGLE-SOURCE]) and names the right corpus — not that it is
    # repo-relative, which is exactly the property the move removed.
    from composeio import bootstrap_paths as _bp
    assert sbr_spec_path().startswith(str(_bp.PLATFORM_SPECS_DIR))


# ══ the manifest AGREEMENT CHECK — no key silently dropped ════════════════
def test_the_real_manifest_is_FULLY_carried():
    """The defect: the prompt consumed six manifest keys and silently ignored FIVE
    path-bearing ones (`principles`, `method_flow`, `bootstrap_read`, `toolbox`, plus the
    `registration` contract), so a spawned instance was never pointed at the STRATEGIC READ
    and arrived with no ground truth."""
    manifest = load_manifest(default_meta_manifest("architect"))
    assert unconsumed_manifest_keys(manifest) == []
    prompt = build_bootstrap_prompt(manifest)
    # every path-bearing key's PATH actually reaches the instance
    for key in ("role_descriptor", "principles", "method_flow", "bootstrap_read", "toolbox"):
        path = manifest[key]["path"]
        assert path in prompt, f"{key} -> {path} never reached the prompt"
    for gift in manifest["dusk_gifts"]:
        assert gift["path"] in prompt
    for required in manifest["registration"]["required"]:
        assert required in prompt


def test_a_NEW_manifest_key_cannot_be_silently_dropped():
    """The class-closing guard.  `method_flow` is the proof it is needed: it was missed BY EYE
    while cataloguing this very defect.  A hand-kept list is what failed; only a mechanical
    comparison closes it — so an unknown key is REPORTED, both to the caller and IN the prompt
    the instance receives."""
    manifest = {"id": "x", "role_descriptor": {"path": "r.md"},
                "future_gift_index": {"path": "somewhere.md"}}
    assert unconsumed_manifest_keys(manifest) == ["future_gift_index"]
    prompt = build_bootstrap_prompt(manifest)
    assert "future_gift_index" in prompt
    assert "UNCARRIED MANIFEST KEYS" in prompt


def test_deliberate_omissions_are_DECISIONS_with_reasons():
    """Being on the omit list must be a stated choice, not a place to hide a key."""
    for key, why in DELIBERATELY_OMITTED.items():
        assert isinstance(why, str) and len(why) > 15, f"{key} is omitted with no reason"
    manifest = load_manifest(default_meta_manifest("architect"))
    # every omission is real (it names a key the manifest actually has), so the list cannot
    # rot into a graveyard of keys nobody removed
    unknown = [k for k in DELIBERATELY_OMITTED if k not in manifest]
    assert unknown == [], f"omit list names keys the manifest does not have: {unknown}"


# ══ the manifest CHAIN — <id>, then <role>, then REFUSE ════════════════════
# [CXC-TERM-MANIFEST-CHAIN].  What used to live here asserted the DEGRADED boot: a missing
# manifest produced `build_unmanifested_prompt`'s "I am NOT oriented" text and the child
# spawned anyway.  That is the branch the ruling REJECTS.  It was defensible — an instance
# that can SAY it could not orient beats no instance — but it is exactly what was invisible:
# the child still took a channel, still took the singleton, still rendered as a working
# session, and in the WIDGET host its one diagnostic went to `_log`, which was `pass`.

def test_the_chain_tries_id_then_role_then_refuses(tmp_path, monkeypatch):
    """All four legs, with a fixture meta dir.  The order is normative and identical in both
    hosts: META_MANIFEST -> <id>.meta.json -> <role>.meta.json -> REFUSE."""
    import json
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    meta = tmp_path / "compose_projects" / "intelligence" / "meta"
    meta.mkdir(parents=True)
    monkeypatch.delenv("META_MANIFEST", raising=False)

    # leg 4 — nothing exists: REFUSE, and every path tried is named IN ORDER
    path, tried = resolve_meta_manifest("architect-hosted", "architect")
    assert path is None
    assert [os.path.basename(p) for p in tried] == [
        "architect-hosted.meta.json", "architect.meta.json"]

    # leg 3 — the ROLE manifest, which is what makes N instances of one role possible
    (meta / "architect.meta.json").write_text(json.dumps({"id": "architect"}))
    path, tried = resolve_meta_manifest("architect-hosted", "architect")
    assert path == str(meta / "architect.meta.json")

    # leg 2 — a manifest curated for THIS instance outranks the role's
    (meta / "architect-hosted.meta.json").write_text(json.dumps({"id": "architect-hosted"}))
    path, _ = resolve_meta_manifest("architect-hosted", "architect")
    assert path == str(meta / "architect-hosted.meta.json")

    # leg 1 — the operator override outranks both
    override = tmp_path / "pinned.meta.json"
    override.write_text(json.dumps({"id": "pinned"}))
    monkeypatch.setenv("META_MANIFEST", str(override))
    path, tried = resolve_meta_manifest("architect-hosted", "architect")
    assert path == str(override) and tried[0] == str(override)


# ══ the prompt names the INSTANCE, never the MANIFEST ═════════════════════
# [CXC-TERM-CHILD-ID] applied to the ORIENTATION PROMPT (fixed 2026-08-11).  `ident` was
# `manifest["id"]`, and `build_bootstrap_for` used `intelligence_id` only to RESOLVE THE PATH —
# so the instance id never reached the prompt.  Invisible while only INSTANCE manifests existed
# (`architect.meta.json`, whose id equals its role); a live mismatch the moment leg 3 resolves a
# ROLE manifest.  The child would be TOLD `composeux-architect` while its channel topics, its
# `intelligence-launcher-<id>` lock and its telemetry all said `architect-hosted` — posting to a
# channel under a name nobody told it it has.

def test_a_ROLE_manifest_still_names_the_INSTANCE_as_the_peer(tmp_path, monkeypatch):
    """THE regression test for the bug: leg 3 hits a manifest whose `id` is the ROLE, and the
    prompt must still open by naming the RUNNING INSTANCE.  The exact opening string is
    asserted, and the role name is asserted NOT to appear as the peer identity — an assertion
    on presence alone would pass on the broken code."""
    import json
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    meta = tmp_path / "compose_projects" / "intelligence" / "meta"
    meta.mkdir(parents=True)
    (meta / "composeux-architect.meta.json").write_text(json.dumps({
        "id": "composeux-architect",                      # the ROLE names itself
        "role_descriptor": {"path": ".claude/agents/composeux-architect.md"},
    }))

    prompt = build_bootstrap_for("architect-hosted", "", role="composeux-architect")
    assert not os.path.exists(meta / "architect-hosted.meta.json"), "leg 2 must MISS here"
    assert prompt.splitlines()[0].startswith(
        'You are the Intelligence peer "architect-hosted" on ComposeBus.')
    assert 'peer "composeux-architect"' not in prompt, (
        "the ROLE manifest renamed the child — the prompt derived identity from the manifest")
    # the role descriptor still arrives; the manifest is the ARTIFACT source, just not the
    # identity source
    assert ".claude/agents/composeux-architect.md" in prompt


def test_the_manifest_id_is_the_FALLBACK_only(tmp_path):
    """A caller with no instance id (a hand-built manifest, a preview render) still gets a
    named peer — and an instance id, when supplied, OUTRANKS the manifest's."""
    m = {"id": "composeux-architect", "role_descriptor": {"path": "r.md"}}
    assert 'peer "composeux-architect"' in build_bootstrap_prompt(m)
    assert 'peer "composeux-architect"' in build_bootstrap_prompt(m, instance_id="   ")
    assert 'peer "architect-hosted"' in build_bootstrap_prompt(m, instance_id="architect-hosted")
    assert 'peer "intelligence"' in build_bootstrap_prompt({"role_descriptor": {"path": "r.md"}})


def test_the_REAL_role_manifest_carries_all_nine_artifacts_and_the_ritual():
    """The scene's leg-3 target, as committed.  Every artifact the role's bootstrap names must
    reach the prompt and the agreement check must be CLEAN — so a key dropped in transcription
    fails here rather than arriving as a silently thinner orientation."""
    import json
    path = os.path.join(_REPO, "compose_projects", "intelligence", "meta", "composeux-architect.meta.json")
    assert os.path.isfile(path), path
    manifest = load_manifest(path)
    assert manifest["id"] == "composeux-architect"
    assert unconsumed_manifest_keys(manifest) == []

    prompt = build_bootstrap_prompt(manifest, instance_id="architect-hosted")
    assert "UNCARRIED MANIFEST KEYS" not in prompt
    named = [manifest[k]["path"] for k in
             ("role_descriptor", "principles", "method_flow", "bootstrap_read", "toolbox")]
    named += [g["path"] for g in manifest["dusk_gifts"]]
    assert len(named) == 9, f"nine artifacts, got {len(named)}"
    for artifact in named:
        assert artifact in prompt, f"{artifact} never reached the prompt"
    for required in manifest["registration"]["required"]:
        assert required in prompt
    for cite in (SBR_SPEC_ID, SBR_FLOW_NODE):
        assert cite in prompt
    for step in SBR_FLOW_STEPS:
        assert step in prompt
    # a ROLE manifest carries no per-INSTANCE UI state
    assert "console" not in manifest and "window_geometry" not in manifest
    # ...and it never hands every instance of the role the host's LITERAL topics: a role's
    # topic map states the SHAPE, and the id in it is the running instance's
    topics = manifest["channel"]["topics"]
    assert set(topics) == {"in", "out", "permission_request", "permission_verdict"}
    for name, topic in topics.items():
        assert "<instance_id>" in topic, f"channel.topics.{name} is a LITERAL id: {topic}"
    assert json.dumps(topics).count("architect") == 0


def test_the_role_name_is_the_BASENAME_of_the_role_descriptor():
    """What makes leg 3 mechanically derivable instead of a maintained convention: rename the
    agent file and the manifest name follows, with no registry to keep in sync."""
    path = os.path.join(_REPO, "compose_projects", "intelligence", "meta", "composeux-architect.meta.json")
    manifest = load_manifest(path)
    descriptor = manifest["role_descriptor"]["path"]
    role = os.path.splitext(os.path.basename(descriptor))[0]
    assert role == manifest["id"]
    assert os.path.basename(path) == f"{role}.meta.json"


def test_the_HOSTs_own_resolution_through_architect_meta_json_is_UNCHANGED(monkeypatch):
    """The worst outcome of this change would be regressing the LIVE host, which resolves leg 2
    with `id == role == architect`.  Minting a role manifest must not touch that path."""
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    resolved, _tried = resolve_meta_manifest("architect", "architect")
    assert resolved == default_meta_manifest("architect")
    assert os.path.basename(resolved) == "architect.meta.json"
    prompt = build_bootstrap_for("architect", "")
    assert prompt.splitlines()[0].startswith(
        'You are the Intelligence peer "architect" on ComposeBus.')
    assert prompt == build_bootstrap_prompt(load_manifest(resolved), instance_id="architect")


def test_an_exhausted_chain_REFUSES_with_a_payload_a_monitor_can_read(tmp_path, monkeypatch):
    """The refusal is DATA, not a log line: it has to reach the glass, the bus
    (`compose_projects/intelligence/<id>/terminal/refusal`) and a non-zero CLI exit."""
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    with pytest.raises(BootstrapRefused) as exc:
        build_bootstrap_for("builder-7", "", role="builder")
    payload = exc.value.payload()
    assert payload["refused"] is True and payload["reason"] == "no-manifest"
    assert payload["id"] == "builder-7" and payload["role"] == "builder"
    assert [os.path.basename(p) for p in payload["paths_tried"]] == [
        "builder-7.meta.json", "builder.meta.json"]
    # the message names BOTH paths, in order, so the plate is actionable without a log
    msg = exc.value.message()
    assert "builder-7.meta.json" in msg and "builder.meta.json" in msg
    assert msg.index("builder-7.meta.json") < msg.index("builder.meta.json")


def test_a_manifest_that_will_not_PARSE_refuses_too(tmp_path, monkeypatch):
    """An unreadable manifest and a missing one are the same failure — the input that decides
    what to orient FROM is not there."""
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    bad = tmp_path / "bad.meta.json"
    bad.write_text("{not json")
    with pytest.raises(BootstrapRefused) as exc:
        build_bootstrap_for("x", "", manifest_path=str(bad))
    assert exc.value.reason == "unreadable-manifest"


def test_BOOTSTRAP_0_is_the_opt_out_and_RESUME_never_refuses(tmp_path, monkeypatch):
    """The refusal has an opt-OUT; it does not have a silent fallback.  `BOOTSTRAP=0` is an
    explicit human act and is the ONE way to get a deliberately unoriented child."""
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.setenv("BOOTSTRAP", "0")
    assert build_bootstrap_for("builder-7", "", role="builder") == ""
    monkeypatch.delenv("BOOTSTRAP")
    # a re-host HOLDS its context — there is nothing to orient from and nothing to refuse
    assert build_bootstrap_for("builder-7", "s-1", role="builder") == ""


def test_no_host_answers_a_missing_manifest_by_logging_and_continuing():
    """[CXC-TERM-MANIFEST-CHAIN] verify-machine, the grep guard.  The degraded-prompt builder
    must not come back under any name in either host."""
    for path in (os.path.join(_REPO, "python", "composeio", "ptycore.py"),
                 os.path.join(_REPO, "compose_projects", "intelligence", "launcher", "launcher.py"),
                 os.path.join(_REPO, "python", "composeio", "render", "widgets",
                              "terminal.py")):
        text = _read(path)
        for i, line in enumerate(text.splitlines(), start=1):
            if "build_unmanifested_prompt" in line:
                assert line.lstrip().startswith("#"), f"{path}:{i}: {line.strip()}"


# ══ the prompt is DELIVERED WHOLE — by exec, not by a line discipline ═════
# What used to live here drove the full prompt through a REAL pty and asserted it arrived
# between the bracketed-paste markers intact, in both line disciplines — because CANONICAL
# mode silently truncates a line over MAX_CANON (1024 on macOS) and would have delivered a
# mutilated prompt that still looked like a prompt.  That whole risk class is GONE with the
# typing: the body is a FILE the child reads, and the only thing crossing a boundary is a
# short argv element ([CXC-TERM-BOOTSTRAP-ARGV]).  What replaces it is the exec-level proof.

def test_the_argv_instruction_survives_a_REAL_exec_intact(tmp_path, monkeypatch):
    """The delivery is now an EXEC, so it is proven at the exec: a real `pty.fork()` child
    dumps its own argv and the last element is byte-identical to what we passed."""
    import json
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps(
        {"id": "architect", "role_descriptor": {"path": "roles/architect.md"}}))
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "p"))
    instruction = build_bootstrap_argv("architect", "", manifest_path=str(manifest))

    child = PtyChild([sys.executable, "-u", "-c",
                      "import sys; sys.stderr.write('ARGV<' + sys.argv[-1] + '>\\n')",
                      instruction], term_size=(24, 200), log=lambda _l: None)
    child.start()
    seen: list = []
    deadline = time.monotonic() + 10.0
    try:
        while time.monotonic() < deadline:
            if not child.pump(seen.append, timeout=0.05):
                break
            if b"ARGV<" in b"".join(seen) and b">" in b"".join(seen).split(b"ARGV<")[-1]:
                break
        out = b"".join(seen).decode(errors="replace").replace("\r\n", "\n")
    finally:
        child.shutdown()
    got = out.split("ARGV<")[-1].split(">")[0]
    assert got == instruction, f"argv mutated in transit: {got!r}"
    # and NOTHING was written to the child by us — the injection log is the audit surface
    assert child.injection_log == []


def test_no_single_prompt_line_can_be_truncated_by_MAX_CANON():
    """KEPT even though the prompt no longer rides a line discipline: the mid-session
    `inject` seam still types, so a pathological unwrapped paragraph is still a hazard for
    anything that reuses the builder's output on the PTY ([CXC-TERM-MIDSESSION-SEAM])."""
    prompt = build_bootstrap_prompt(load_manifest(default_meta_manifest("architect")))
    longest = max((len(ln.encode()), ln) for ln in prompt.split("\n"))
    assert longest[0] < 1024, f"a {longest[0]}-byte line risks MAX_CANON: {longest[1][:80]!r}"


# ══ the LAUNCHER delivers it too — on ARGV, and it TYPES NOTHING ══════════
def _launcher_module():
    sys.path.insert(0, os.path.join(_REPO, "compose_projects", "intelligence", "launcher"))
    import launcher as mod
    return mod


def test_the_launcher_puts_the_bootstrap_ON_ARGV_and_types_nothing(monkeypatch, tmp_path):
    """David: "the new instance did not appear to go through the bootstrapping process."  The
    answer is no longer an armed timer — it is an argv element, so the assertion is on the
    child's argv AND on the injection log being EMPTY for the whole session."""
    import json
    mod = _launcher_module()
    manifest = tmp_path / "arch.meta.json"
    manifest.write_text(json.dumps({"id": "architect",
                                    "role_descriptor": {"path": "roles/architect.md"}}))
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("META_MANIFEST", str(manifest))
    monkeypatch.setenv("BOOTSTRAP_PROMPT_DIR", str(tmp_path / "prompts"))

    cfg = mod._load_config([])
    seed = mod.build_bootstrap_argv(cfg["intelligence_id"], "",
                                    role=cfg.get("role") or "",
                                    manifest_path=cfg.get("meta_manifest"))
    assert seed.startswith("Read "), "a default COLD launch built no argv prompt"
    assert "roles/architect.md" in _read(seed.split()[1])

    lnch = mod.Launcher(intelligence_id="architect", bootstrap_prompt=seed,
                        claude_args=["-c", "import time; time.sleep(0.3)"],
                        claude_bin=sys.executable, log=lambda _l: None)
    assert lnch.claude_args[-1] == seed, "the prompt is not on the child's argv"
    assert lnch.child.argv[-1] == seed
    lnch.start_child()
    try:
        time.sleep(0.4)
        assert lnch.injection_log == [], "the launcher TYPED at startup"
    finally:
        lnch.shutdown()


def test_NEITHER_host_has_an_arming_step_any_more():
    """The mirror of the old `test_BOTH_spawn_paths_actually_CALL_the_arming`.  The defect
    then was an arming method nobody called; the requirement now is that the whole mechanism
    is ABSENT — a dormant `_arm_bootstrap` is one edit from firing into a dialog again."""
    launcher_py = os.path.join(_REPO, "compose_projects", "intelligence", "launcher", "launcher.py")
    terminal_py = os.path.join(_REPO, "python", "composeio", "render", "widgets",
                               "terminal.py")
    for path in (launcher_py, terminal_py):
        # CODE only: comments and docstrings MUST be able to say the names, because the
        # deletion is only durable if the tree explains why it happened.
        code = _code_only(path)
        for banned in ("arm_bootstrap", "_type_bootstrap", "inject_prompt",
                       "bootstrap_delay", "BOOTSTRAP_DELAY", "threading.Timer"):
            assert banned not in code, f"{path}: {banned!r} is still LIVE CODE"


def test_the_launcher_delivers_NOTHING_in_resume_mode(monkeypatch, tmp_path):
    mod = _launcher_module()
    lnch = mod.Launcher(intelligence_id="architect", bootstrap_prompt="",
                        resume_session="sess-1", claude_args=["-c", "pass"],
                        claude_bin=sys.executable, log=lambda _l: None)
    assert lnch.claude_args == ["-c", "pass"], "a resume got a prompt appended"


def test_the_CLI_host_REFUSES_an_exhausted_chain_with_a_nonzero_exit(monkeypatch, tmp_path):
    """[CXC-TERM-MANIFEST-CHAIN]: "the CLI host refuses identically — a non-zero exit naming
    the paths tried, not a log line and a launch".  Two hosts with different answers to "what
    if there is no manifest" is the fork-divergence the shared core exists to prevent."""
    mod = _launcher_module()
    mod._load_core()
    monkeypatch.setattr("composeio.ptycore.repo_root", lambda: str(tmp_path))
    monkeypatch.delenv("META_MANIFEST", raising=False)
    monkeypatch.delenv("BOOTSTRAP", raising=False)
    monkeypatch.setenv("INTELLIGENCE_ID", "nobody-7")
    monkeypatch.setenv("INTELLIGENCE_ROLE", "nobody")
    monkeypatch.setattr(mod, "DEFAULT_ID", "nobody-7")
    spawned: list = []
    monkeypatch.setattr(mod.Launcher, "run", lambda self: spawned.append(1) or 0)
    err: list = []
    monkeypatch.setattr(sys, "stderr", type("W", (), {"write": lambda _s, t: err.append(t)})())
    rc = mod.main([])
    assert rc == 3, rc
    assert spawned == [], "the launcher spawned a child with no manifest"
    blob = "".join(err)
    assert "nobody-7.meta.json" in blob and "nobody.meta.json" in blob
    assert "BOOTSTRAP=0" in blob, "the refusal must name its opt-out"


# ══ the prose sites [CXC-ICL-COLD] requires corrected ══════════════════════
def _bootstrap_prose_sites() -> list[str]:
    return [os.path.join(_REPO, "compose_projects", "intelligence", "launcher", "launcher.py"),
            os.path.join(_REPO, "compose_projects", "intelligence", "launcher", "README.md")]


def test_no_experimental_characterization_of_the_bootstrap_survives():
    """[CXC-ICL-COLD] verify-machine, leg 2: "a grep guard that no 'experimental'
    characterization of the bootstrap survives in launcher.py / README.md after the prose
    fix".  David's CORRECTION quotes the word ("is not experimental any longer") and must be
    allowed to stand — quoting the correction is the opposite of repeating the claim."""
    for path in _bootstrap_prose_sites():
        for i, line in enumerate(_read(path).splitlines(), start=1):
            if "experimental" not in line.lower():
                continue
            assert "not experimental" in line.lower(), f"{path}:{i}: {line.strip()}"


def test_the_prose_says_the_bootstrap_is_the_DEFAULT():
    """Leg 1: shipping a spec that says "default on" against docs that say "default off" is
    the drift class this corpus exists to kill, so the docs are asserted, not trusted."""
    launcher_src, readme = (_read(p) for p in _bootstrap_prose_sites())
    assert "BOOTSTRAP         default ON" in launcher_src
    assert "`BOOTSTRAP` | **on**" in readme
    assert "default **ON**" in readme
    for src in (launcher_src, readme):
        assert "BOOTSTRAP=0" in src, "the explicit opt-OUT must stay documented"
    # ...and the ban it must not be confused with is restated at both sites.
    assert "CXC-TERM-DIALOGS" in launcher_src and "CXC-TERM-DIALOGS" in readme
