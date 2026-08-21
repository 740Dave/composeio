"""The ONE PTY implementation in this tree — [SPEC:CXC-TERM] · [CXC-TERM-PTYCORE].

TWO consumers, ONE `pty.fork()`:

  * `compose_projects/intelligence/launcher/launcher.py` — the transparent CLI proxy + `/compact` relay.
  * `composeio.render.widgets.terminal` — the ComposeUX terminal widget, which hosts the
    same child inside a window instead of a terminal.

This module is an EXTRACTION, not a rewrite. Every function below was moved out of
`launcher.py` with its behaviour intact; `launcher.py` now imports them and delegates.
The ONE behavioural change the spec sanctions is the pump's output half: it takes an
`on_output: Callable[[bytes], None]` SINK instead of an out_fd, so the widget does not
have to invent a pipe just to read its own child. The launcher passes a sink that does
`write_all(out_fd, data)` — byte-identical to what it did before.

WHY A CORE AND NOT `from launcher import Launcher` ([CXC-TERM-PTYCORE] known-hazard):
`Launcher.run()` installs signal handlers, takes a superseding flock and blocks. It is a
`main()`, not a component. Importing it whole would couple a render widget to a CLI
program's lifecycle. The table in [CXC-TERM-PTYCORE] is normative about what moved:
`start_child` / the pump / the write path / `inject` / `inject_line` / teardown / the
session pin / `build_claude_args` moved HERE; `proxy_loop`, raw-mode termios,
`_install_signals` and `run()` stayed in the launcher, because a windowed app has no
controlling terminal and the console owns its own signals.

THE SINGLE WRITE SITE ([CXC-TERM-CONVERGE], acceptance TERM-C8). Every byte that reaches
the child — a bus-originated `inject`, a `/compact` actuation, a human keystroke typed on
the glass — passes through `PtyChild._write()`, under `_write_lock`. `os.write(self.master_fd`
appears exactly once in this file and nowhere else in the tree, which is what makes "the
child cannot distinguish a bus keystroke from a keyboard keystroke" true BY CONSTRUCTION
rather than by assertion.

THE AUDIT SURFACE ([CXC-TERM-CONVERGE]). `inject()` LOGS; `write_keys()` does NOT.
`injection_log`'s meaning is "writes the harness originated" — polluting it with keyboard
traffic would destroy the audit property and leak the human's typing into a durable record.
Two entry points, one write path, two different logging contracts, on purpose.

THE CHILD MINTS ITS OWN IDENTITY ([CXC-IC-SELF-IDENTITY], defect found on the glass
2026-08-11). `start()` hands the child a SANITISED environment, never a raw copy of ours —
see `HOST_IDENTITY_ENV` for the derivation and the per-variable reasons.
"""
from __future__ import annotations

import json
import os
import re
import select
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

# ── Claude Code argv ────────────────────────────────────────
# The channel flag. ONLY the dev-channel flag — NOT `--channels` as well.
#
# WHY (a real bug found on the glass): passing BOTH `--channels server:composeio-channel` AND
# `--dangerously-load-development-channels server:composeio-channel` DOUBLE-REGISTERS the server
# and breaks the channel notification listener. A custom (non-allowlisted) dev channel must be
# loaded with ONLY `--dangerously-load-development-channels`; `--channels` is for allowlisted
# channels and must not also name a dev channel.
CHANNEL_FLAGS = [
    "--dangerously-load-development-channels", "server:composeio-channel",
]


def build_claude_args(
    resume_session: str | None = None,
    skip_permissions: bool = False,
    mcp_config: str = "",
) -> list[str]:
    """Assemble the argv tail passed to `claude` (everything after CLAUDE_BIN).

      COLD START (resume_session empty): just the single dev-channel flag.
      RESUME / MOVE (resume_session set): PREPEND `--resume <session-id>` so `claude` RE-HOSTS an
        already-running session with its context intact; the dev-channel flag still applies so
        the re-hosted instance lands on composeio-channel.
      SKIP_PERMISSIONS: append `--dangerously-skip-permissions` (may reduce but not eliminate the
        startup dialogs).
      MCP_CONFIG (the CHILD'S OWN IDENTITY, [CXC-IC-CHANNEL-INSTANCE]): a path written by
        `write_child_mcp_config` — a copy of the repo's `.mcp.json` with ONE key changed.
        Emits `--strict-mcp-config --mcp-config <path>`; empty emits neither, which is the
        CLI launcher's unchanged behaviour (it IS the primary instance, so the repo config's
        identity is already its own).

    ⚠️ POSITION IS LOAD-BEARING. `--mcp-config <configs...>` is VARIADIC on this build
    (`claude --help`: "Load MCP servers from JSON files or strings (space-separated)"), so it
    swallows every following bare word up to the next `--flag`.  The bootstrap prompt rides
    argv as a POSITIONAL ([CXC-TERM-BOOTSTRAP-ARGV]) — putting `--mcp-config` last would feed
    the 200-character wake instruction to the MCP loader as a config path.  It is therefore
    emitted BEFORE `CHANNEL_FLAGS`, which is a `--flag` and terminates the variadic.
    """
    args: list[str] = []
    if resume_session:
        args += ["--resume", resume_session]
    if mcp_config:
        args += ["--strict-mcp-config", "--mcp-config", str(mcp_config)]
    args += list(CHANNEL_FLAGS)
    if skip_permissions:
        args += ["--dangerously-skip-permissions"]
    return args


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── The claude BINARY ([CXC-TERM-WIDGET]; PATH defect found live 2026-08-11) ─────────────────
def resolve_claude_bin(explicit: str | None = None) -> str:
    """Which `claude` to exec.  Chain, highest first:

        1. `explicit` — a caller / scene property that names the binary outright.
        2. `$CLAUDE_BIN` — the operator pin the launcher has always honoured.
        3. `$CLAUDE_CODE_EXECPATH` — set by the Claude Code harness itself and naming THE
           BINARY OF THE RUNNING HOST (verified: `.../@anthropic-ai/claude-code/bin/claude.exe`).
        4. the bare name `"claude"`, resolved by `execvp` through PATH.

    WHY 3 EXISTS (a real failure, first live spawn of the terminal widget): on this machine
    `claude` is a SHELL ALIAS and is NOT on PATH, so the old `"claude"` default made `execvp`
    exhaust PATH and die with a misleading "No such file" naming a stale plugin directory.  A
    windowed host has no shell, so an alias is invisible to it; `CLAUDE_CODE_EXECPATH` is an
    absolute path the harness already handed us and is strictly MORE correct than a PATH search.
    It is therefore also one of the variables deliberately KEPT in the child env
    (`KEPT_HOST_ENV`) — it names a binary, not a session."""
    for candidate in (explicit, os.environ.get("CLAUDE_BIN"),
                      os.environ.get("CLAUDE_CODE_EXECPATH")):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    return "claude"


# ── The child MINTS its own identity ([CXC-IC-SELF-IDENTITY]) ────────────────────────────────
# `pty.fork()` + `execvpe` hands the child a COPY of our environment.  When the host is ITSELF
# a Claude Code session — which is the terminal widget's INTENDED deployment ("the console
# hosts the Intelligence"), not an artifact of how it happened to be launched — that copy
# carries the HOST'S IDENTITY.  Measured on the live child, 2026-08-11:
#
#     child : CLAUDE_CODE_SESSION_ID=e1a82ba7-...   host : CLAUDE_CODE_SESSION_ID=e1a82ba7-...
#
# Two consequences, the second worse:
#   1. the child believes it is a NESTED child session, so TRANSCRIPT SAVING IS OFF;
#   2. [CXC-IC-SELF-IDENTITY] resolves a session pin FROM `CLAUDE_CODE_SESSION_ID`
#      (`data_sources.resolve_session_jsonl` leg 2), so a hosted instance carrying its host's
#      id resolves to the HOST'S TRANSCRIPT — the exact cross-instance leak that fix exists to
#      prevent (the `ls -t` newest-wins defect, recurred 2026-07-24).
#
# THE LIST IS DERIVED FROM WHAT A SPAWNED INSTANCE MUST MINT FOR ITSELF, not from a dump of
# what happened to be set: its identity, its parentage, the am-I-inside-Claude flag, its IPC
# endpoint, its entrypoint, its pid, and its transcript pin.  Anything describing the MACHINE
# or the BINARY rather than the RUNNING SESSION is kept (`KEPT_HOST_ENV`).
HOST_IDENTITY_ENV: dict = {
    # ── identity ──
    "INTELLIGENCE_ID":
        "the HOST's BUS id — the id `channel.mjs`, the launcher and the console all key on. "
        "A hosted child is a PEER wearing its own `instance_id` ([CXC-TERM-CHILD-ID]); "
        "inheriting this is how a child came to bind `intelligence/architect/in|out` and "
        "DISPLACE the live host. NOTE it is the SECOND of two independent reasons the child "
        "cannot be the host: the channel binding is actually decided by the generated "
        "per-instance MCP config (`write_child_mcp_config`), because an `env` block in "
        "`.mcp.json` OVERRIDES the inherited environment. Both, on purpose.",
    "CLAUDE_CODE_SESSION_ID":
        "the HOST session's id.  The child's `claude` mints its own on startup, and this is "
        "the key leg 2 of resolve_session_jsonl globs on — inheriting it IS the leak.",
    # ── parentage (why transcript saving was off) ──
    "CLAUDE_CODE_CHILD_SESSION":
        "'I am a nested child of a running session'.  A hosted instance is a PEER, not a "
        "sub-session; carrying this is what turned transcript saving OFF in the child.",
    "CLAUDE_CODE_BRIDGE_SESSION_ID":
        "the host's bridge/parent session id — the same nesting claim by another name.",
    # ── the in-harness flag ──
    "CLAUDECODE":
        "'this process is running INSIDE Claude Code' (i.e. I am a tool Claude invoked).  A "
        "top-level `claude` we spawn under a PTY is not; it sets this for its OWN children.",
    # ── IPC endpoints that belong to the host ──
    "CLAUDE_CODE_MESSAGING_SOCKET":
        "names the HOST's message socket.  A child writing there talks on the host's wire.",
    # ── how the process was started ──
    "CLAUDE_CODE_ENTRYPOINT":
        "describes how THIS process was started ('cli').  The child's entrypoint is our "
        "pty.fork; the harness sets it itself.",
    "CLAUDE_PID":
        "the HOST claude's pid.  A child that reports it is reporting its parent.",
    # ── the transcript pin (the SAME leak by a second route) ──
    # These are leg 1 of resolve_session_jsonl (`data_sources._SESSION_JSONL_ENV`) plus
    # ptycore's own SESSION_JSONL knob.  The console bring-up (start-console-stack.sh) exports
    # COMPOSEIO_SESSION_JSONL from the HOST's CLAUDE_CODE_SESSION_ID, so an unsanitised child
    # inherits an absolute path to the host's transcript and every transcript-backed source in
    # it instruments the WRONG session.  A pin that legitimately names the CHILD's own
    # transcript (RESUME/MOVE mode) is passed back in through `env_extra`, which is applied
    # AFTER this strip — a PASSED pin, never an INHERITED one.
    "COMPOSEIO_SESSION_JSONL": "leg-1 transcript pin belonging to whoever set it.",
    "JSONL": "leg-1 transcript pin alias (data_sources._SESSION_JSONL_ENV).",
    "SESSION_JSONL": "ptycore's own derive_jsonl_path override — read in the PARENT, before "
                     "the fork, so stripping it from the child costs nothing.",
}

# DELIBERATELY KEPT, stated so a future reader does not "tidy" them into the strip list.
KEPT_HOST_ENV = (
    # names the BINARY, not the session — and it is the fix for the PATH/alias defect above.
    "CLAUDE_CODE_EXECPATH",
    # the widget SETS this itself in child_env() ([CXC-TERM-AXMODE]); deliberate, not inherited.
    "CLAUDE_AX_STARTUP_QUIET_MS",
    # a machine-level config location and a model preference — neither is a session identity.
    "CLAUDE_CONFIG_DIR", "CLAUDE_EFFORT",
)
assert not (set(KEPT_HOST_ENV) & set(HOST_IDENTITY_ENV)), "a var cannot be both kept and stripped"


def sanitize_child_env(base: Optional[dict] = None,
                       extra: Optional[dict] = None) -> dict:
    """The environment a spawned instance gets: `base` (default `os.environ`) MINUS every
    `HOST_IDENTITY_ENV` key, PLUS `extra`.

    `extra` is applied LAST on purpose: it is how a caller re-supplies a value deliberately —
    a RESUME/MOVE pin naming the child's OWN transcript, the widget's `TERM` and
    `CLAUDE_AX_STARTUP_QUIET_MS` — so "passed" and "inherited" stay distinguishable."""
    src = os.environ if base is None else base
    env = {str(k): str(v) for k, v in src.items()}
    for name in HOST_IDENTITY_ENV:
        env.pop(name, None)
    if extra:
        env.update({str(k): str(v) for k, v in extra.items()})
    return env


# ── The child's OWN MCP CONFIG ([CXC-IC-CHANNEL-INSTANCE], the other half of the defect) ─────
# [CXC-TERM-CHILD-ID] renamed everything THE WIDGET controls — the singleton lock, the bus
# subscribe, the telemetry namespace, the refusal publish — and the ONE surface it does not
# control is the one that caused the original outage.  Measured 2026-08-11:
#
#     .mcp.json  composeio-channel  env: {"INTELLIGENCE_ID": "architect", ...}
#     child_argv():  no --mcp-config, no --strict-mcp-config
#     cwd: None -> the host's cwd -> THE REPO
#
# So a widget-hosted child loads the REPO's `.mcp.json` and its `channel.mjs` binds
# `intelligence/architect/in|out|permission/*` — THE HOST'S TOPICS — no matter what
# `instance_id` the scene declared.  Symptom 1 of the first live spawn ("the child's
# channel.mjs displaced the host's and the host lost its voice") was still live on the
# child's side; the two halves of the fix disagreed with each other.
#
# ⚠️ WHY ENV IS NOT THE FIX.  An `env` block in `.mcp.json` OVERRIDES the inherited
# environment for that server, so neither SETTING nor STRIPPING `INTELLIGENCE_ID` in the
# child's environment can move the binding.  The config file is the only lever.  Stripping it
# (`HOST_IDENTITY_ENV`) is still done — two independent reasons, not one with a spare.
#
# WHY DERIVED AND NOT HAND-WRITTEN.  The first live spawn's wake ritual ran `lts_selfcheck`,
# `lts_find` x9 and `lts_aggregate`: a config carrying ONLY the channel would break the
# bootstrap the child is spawned to perform.  We deep-copy the REAL file and override exactly
# one key, so every server the repo grows arrives in the child for free.
CHANNEL_SERVER = "composeio-channel"
INTELLIGENCE_ID_KEY = "INTELLIGENCE_ID"


def repo_mcp_config_path() -> str:
    """The config we DERIVE FROM: `<repo>/.mcp.json`.  `COMPOSEIO_MCP_CONFIG` overrides for a
    child that must be given a different server set (the derivation is unchanged: copy it,
    rename the channel)."""
    return os.environ.get("COMPOSEIO_MCP_CONFIG",
                          os.path.join(repo_root(), ".mcp.json"))


def child_mcp_config_dir() -> str:
    """Where a generated per-instance config lives.  NOT under `.claude/` and NOT in the repo
    (it must never be mistaken for the tracked `.mcp.json`, and a child may be spawned outside
    the tree).  `CHILD_MCP_CONFIG_DIR` overrides."""
    import tempfile
    return os.environ.get("CHILD_MCP_CONFIG_DIR",
                          os.path.join(tempfile.gettempdir(), "composeio-mcp"))


def build_child_mcp_config(instance_id: str, source_path: str = "") -> dict:
    """The child's MCP config OBJECT: the repo's, deep-copied, with
    `mcpServers[composeio-channel].env.INTELLIGENCE_ID` set to `instance_id` — and NOTHING
    else touched.  Pure: reads a file, returns a dict, writes nothing.

    Raises `BootstrapRefused` when the source is missing/unparseable, or when it carries no
    channel server.  Both are REFUSALS rather than degradations: a child spawned with a
    config we could not rebind is a child on the host's topics (the outage), and a child
    spawned with no channel at all is off the bus SILENTLY — which is the failure class this
    whole area exists to end."""
    import copy
    src = source_path or repo_mcp_config_path()
    try:
        with open(src, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
    except Exception as exc:
        raise BootstrapRefused(
            "mcp-config-unreadable", intelligence_id=instance_id, paths_tried=[src],
            detail=f"{type(exc).__name__}: {exc} — cannot derive a per-instance channel "
                   f"identity, and spawning without one binds the HOST's topics")
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict) or CHANNEL_SERVER not in servers:
        raise BootstrapRefused(
            "mcp-config-no-channel-server", intelligence_id=instance_id, paths_tried=[src],
            detail=f"{src} declares no {CHANNEL_SERVER!r} server, so there is nothing to "
                   f"rebind and the child would come up off the bus")
    out = copy.deepcopy(cfg)
    channel = out["mcpServers"][CHANNEL_SERVER]
    env = channel.get("env")
    if not isinstance(env, dict):
        env = {}
        channel["env"] = env
    env[INTELLIGENCE_ID_KEY] = str(instance_id)
    return out


def _child_mcp_config_prefix(instance_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(instance_id or "unnamed"))
    return f"mcp-{safe}-"


def write_child_mcp_config(instance_id: str, source_path: str = "",
                           log: Optional[Callable[[str], None]] = None) -> str:
    """Write the per-instance config and return its ABSOLUTE path.

    Any EARLIER config for the same `instance_id` is deleted first: the file names an
    identity, and a stale one is a stale identity — the exact hazard the tracked `.mcp.json`
    already demonstrated.  Mode 0600; it carries no secret (it is a copy of a tracked file)
    but it is a spawn-scoped artifact, not shared state."""
    say = log or (lambda _line: None)
    cfg = build_child_mcp_config(instance_id, source_path)
    d = child_mcp_config_dir()
    os.makedirs(d, exist_ok=True)
    prefix = _child_mcp_config_prefix(instance_id)
    for stale in os.listdir(d):
        if stale.startswith(prefix) and stale.endswith(".json"):
            try:
                os.unlink(os.path.join(d, stale))
            except OSError:
                pass
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = os.path.abspath(os.path.join(d, f"{prefix}{os.getpid()}-{stamp}.json"))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=False)
        fh.write("\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    say(f"[ptycore] {iso_now()} MCP config for {instance_id!r} -> {path} "
        f"({len(cfg.get('mcpServers', {}))} servers, channel identity rebound)")
    return path


def remove_child_mcp_config(path: str) -> None:
    """Delete a generated config.  Best-effort by design: teardown must never raise, and a
    leftover file is re-deleted by the next `write_child_mcp_config` for the same id."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


# ── /compact injection mechanics (the leaf owns the actuation) ──────────────────────────────
# Two live-verified facts (2026-07-19) shaped this:
#
#   * SUBMIT is a CARRIAGE RETURN (\r, 0x0D — what the physical Return key emits in raw mode),
#     NOT a line feed (\n, 0x0A). Claude Code's TUI inserts \n as a newline; it submits on \r.
#     The old bulk write `/compact <direction>\n` left the command sitting in the prompt, fully
#     typed, HUNG waiting for the human to press Return. \r fixes it — and it is sent as a
#     SEPARATE write AFTER the command so a paste-buffering TUI finishes ingesting the command
#     before the submit arrives (this is what makes an ANY-LENGTH direction self-fire).
#   * The prompt must be CLEARED first: there was pre-existing text in the entry area when the
#     leaf injected, and it collided with the command (the human had to ESC-ESC it clear by hand).
COMPACT_CLEAR = "\x1b\x1b"   # ESC ESC — wipe any pre-existing prompt text before typing
COMPACT_SUBMIT = "\r"        # carriage return — the key the TUI reads as "submit"
COMPACT_PHASE_DELAY_S = 0.2
INJECT_CMD = "inject"

# BRACKETED PASTE ([CXC-TERM-PASTE]).  A MULTI-LINE body delivered RAW submits on every
# embedded newline, so a multi-character payload the HUMAN pastes is wrapped in these markers.
# One spelling, imported by the widget — a second copy is the fork this core exists to prevent.
#
# ⚠️ WHAT THESE MARKERS DO **NOT** LICENCE ([CXC-TERM-CENSUS-CONFIG-FIDELITY], measured
# 2026-08-11).  This constant used to carry the claim "the census measured `bracketed_paste`
# in the STARTUP segment of all five profiles, so a `claude` child has `?2004h` on by the time
# anything is typed into it".  That figure came from the CLEAN-ROOM profiles
# (`--strict-mcp-config`, an EMPTY `mcpServers`, `skipDangerousModePermissionPrompt: true`,
# no dev-channels flag) — a configuration with NO dialogs in it, which is not the configuration
# the widget deploys.  On the first live spawn (real `.mcp.json`, dev channels, permission
# prompts live) the TUI had not started, nothing had set `?2004h`, and `^[[200~` ECHOED AS
# LITERAL TEXT into a blocking `Enter y/n:` read.  No startup claim may be made from a
# clean-room profile; only the AS-DEPLOYED profile measures startup ([CXC-TERM-CENSUS] validity
# condition 4).  Startup typing is now impossible anyway — the bootstrap rides argv
# ([CXC-TERM-BOOTSTRAP-ARGV]) — so these markers serve the HUMAN's paste and the mid-session
# seam only, both of which face a TUI that is definitely already up.
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"


# ── Session-transcript identity ([CXC-IC-SELF-IDENTITY], [CXC-TERM-FACTS] fact 1) ───────────
# project_dir_key + project_dir_for + role_memory_dir are IMPORTED from
# `composeio.instance_home`, not re-spelled here ([CXC-FC-DEFORK]).  They stay importable from
# this module (the import binds them) so `from composeio.ptycore import project_dir_key` and
# `from launcher import project_dir_key` both keep working — one definition, three call paths.
#
# Model of the harness's sanitization, for a reader who lands here first: the harness keys a
# session's project dir — where its transcript `.jsonl` AND its memory store live — off the
# ABSOLUTE cwd, so `~/compose-instances/composeux-architect` ->
# `-Users-x-compose-instances-composeux-architect`.  This is THE lever
# ([INSIGHT-CWD-IS-THE-SCOPE-LEVER]): distinct cwds -> distinct project keys; a SHARED cwd
# collapses N instances into one key — which is deliberate for two instances of ONE ROLE
# ([DD-FC-MEMORY-GRAIN]) and a defect for anything else.
from composeio.instance_home import (RoleRefused, project_dir_for,  # noqa: E402,F401
                                     project_dir_key, role_memory_dir)


def derive_jsonl_path(resume_session: str) -> str:
    """Best-effort session .jsonl path. SESSION_JSONL wins; else derive from the resumed
    session-id under CLAUDE_PROJECTS_DIR, whose default project dir is derived from the CURRENT
    cwd (`project_dir_key`) — so an instance cold-started / re-hosted from its OWN home derives
    its transcript from ITS project dir, not the shared pile ([CXC-IH-HOMES])."""
    explicit = os.environ.get("SESSION_JSONL", "").strip()
    if explicit:
        return explicit
    if not resume_session:
        return ""
    proj = os.environ.get(
        "CLAUDE_PROJECTS_DIR",
        os.path.join(os.path.expanduser("~"), ".claude", "projects", project_dir_key(os.getcwd())),
    )
    return os.path.join(proj, f"{resume_session}.jsonl")


def export_session_pin(resume_session: str, log: Callable[[str], None]) -> str:
    """Set `COMPOSEIO_SESSION_JSONL` in THIS process env (inherited by the pty.fork+execvp child
    and everything it spawns) so the console's data layer instruments the RIGHT transcript by a
    PASSED value, never an `ls -t` guess ([CXC-IC-SELF-IDENTITY], acceptance TERM-C7).

    Returns the pinned path ("" when nothing was pinned). No-op unless we are re-hosting a known
    session whose .jsonl exists on disk; an explicit pre-set env always wins."""
    already = os.environ.get("COMPOSEIO_SESSION_JSONL", "").strip()
    if already:
        log(f"[ptycore] {iso_now()} session pin: COMPOSEIO_SESSION_JSONL already set — honoring it")
        return already
    if not resume_session:
        return ""
    jsonl = derive_jsonl_path(resume_session)
    if jsonl and os.path.isfile(jsonl):
        os.environ["COMPOSEIO_SESSION_JSONL"] = jsonl
        log(f"[ptycore] {iso_now()} session PINNED for child -> {jsonl}")
        return jsonl
    log(f"[ptycore] {iso_now()} session pin: derived .jsonl not found ({jsonl!r}); "
        f"child will resolve by its own rules")
    return ""


# ── PTY window size — TIOCSWINSZ + SIGWINCH ([CXC-TERM-WINSIZE]) ─────────────────────────────
# NOTHING in this repository ever set a pty window size before 2026-08-10 (a grep for TIOCSWINSZ /
# winsize / SIGWINCH returned zero matches). A pty from `pty.fork()` starts at 0x0, and a TUI that
# cannot learn its dimensions degrades to minimal line-oriented output — IDENTICALLY in every mode.
# Two escape-sequence censuses were voided by exactly that ([CXC-TERM-CENSUS-PRIOR] diagnosis B).
#
# The size is applied in TWO places on purpose:
#   * in the CHILD, on fd 0 (the slave pty), BEFORE `execvp` — so the spawned program can never
#     observe 0x0. This closes the fork/exec race that a parent-only ioctl leaves open.
#   * in the PARENT, on the master fd, right after fork — establishes the master-side path that
#     every later resize uses, and covers the (unexpected) case of the child-side ioctl failing.
DEFAULT_PTY_ROWS = 24          # the safe floor; a real terminal's size overrides it
DEFAULT_PTY_COLS = 80
MIN_PTY_ROWS = 10              # matches [CXC-TERM-GRID]'s MIN_ROWS
MIN_PTY_COLS = 40              # matches [CXC-TERM-GRID]'s MIN_COLS — the terminal widget's floor


def pack_winsize(rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> bytes:
    """The `struct winsize` payload: ws_row, ws_col, ws_xpixel, ws_ypixel — four unsigned shorts,
    in THAT order (rows first; the reversed order is the classic bug)."""
    import struct
    return struct.pack("HHHH", int(rows), int(cols), int(xpixel), int(ypixel))


def set_winsize(fd: int, rows: int, cols: int, xpixel: int = 0, ypixel: int = 0) -> bool:
    """ioctl(fd, TIOCSWINSZ, winsize). Works on either end of the pty (master or slave). Returns
    True on success; False (never raising) when `fd` is not a tty / already closed — a size that
    cannot be set must not take the host down."""
    try:
        import fcntl
        import termios
        fcntl.ioctl(fd, termios.TIOCSWINSZ, pack_winsize(rows, cols, xpixel, ypixel))
        return True
    except (OSError, ValueError, ImportError, AttributeError):
        return False


def get_winsize(fd: int) -> tuple[int, int] | None:
    """(rows, cols) currently set on `fd` via TIOCGWINSZ, or None if it is not a tty. The read-back
    that makes a winsize assertion an OBSERVATION rather than a claim."""
    try:
        import fcntl
        import struct
        import termios
        raw = fcntl.ioctl(fd, termios.TIOCGWINSZ, b"\0" * 8)
        rows, cols, _xp, _yp = struct.unpack("HHHH", raw)
        return int(rows), int(cols)
    except Exception:
        return None


def _envint(name: str) -> int | None:
    v = os.environ.get(name)
    if v is None or not v.strip():
        return None
    try:
        return int(v)
    except ValueError:
        return None


def resolve_winsize(rows: int | None = None, cols: int | None = None,
                    tty_fd: int | None = None) -> tuple[int, int]:
    """Decide the (rows, cols) to give the child. Precedence, highest first:

      1. EXPLICIT arguments — a caller (the census tool, a test, the terminal widget whose
         arranged rect IS the geometry) that knows the size wins outright.
      2. `PTY_ROWS` / `PTY_COLS` env — the operator pin.
      3. The caller's OWN controlling terminal (`tty_fd`). The right default for a TRANSPARENT
         proxy: the child should be exactly as big as the human's terminal.
      4. DEFAULT_PTY_ROWS x DEFAULT_PTY_COLS (24x80) — the safe floor.

    The result is clamped to MIN_PTY_ROWS/MIN_PTY_COLS so a bogus env value or a hilariously small
    terminal can never hand the child a degenerate geometry."""
    if rows is None or cols is None:
        env_r, env_c = _envint("PTY_ROWS"), _envint("PTY_COLS")
        rows = rows if rows is not None else env_r
        cols = cols if cols is not None else env_c
    if (rows is None or cols is None) and tty_fd is not None:
        try:
            size = os.get_terminal_size(tty_fd)
            rows = rows if rows is not None else size.lines
            cols = cols if cols is not None else size.columns
        except (OSError, ValueError):
            pass
    rows = DEFAULT_PTY_ROWS if rows is None else int(rows)
    cols = DEFAULT_PTY_COLS if cols is None else int(cols)
    return max(rows, MIN_PTY_ROWS), max(cols, MIN_PTY_COLS)


def probe_tty_fd() -> int | None:
    """The caller's own terminal, for inheriting its geometry. stdout first, then stdin —
    stdout is what a human resizes; stdin may be a pipe even in an interactive run. Returns
    None in a windowed app, which has no controlling terminal at all."""
    for stream in (sys.stdout, sys.stdin):
        try:
            fd = stream.fileno()
        except (OSError, ValueError, AttributeError):
            continue
        try:
            if os.isatty(fd):
                return fd
        except (OSError, ValueError):
            continue
    return None


# ── Low-level fd write helper ───────────────────────────────
def write_all(fd: int, data: bytes) -> None:
    """Write all of `data` to `fd`, tolerating short writes. Best-effort: on OSError (peer
    closed) it stops silently — the pump notices EOF/exit on the next cycle."""
    while data:
        try:
            n = os.write(fd, data)
        except OSError:
            return
        if n <= 0:
            return
        data = data[n:]


class PtyChild:
    """A `claude` (or any) child under a PTY: spawn, one select cycle, one write path, teardown.

    Deliberately structured as a single testable `pump()` cycle over plain fds — that structure
    is what lets the launcher's selftest drive it with pipes (no controlling terminal required)
    AND lets the terminal widget drive it from a reader thread. It is the property that made this
    extraction possible at all ([CXC-TERM-PTYCORE])."""

    def __init__(self, argv: list[str], *,
                 term_size: Optional[tuple[int, int]] = None,
                 env_extra: Optional[dict] = None,
                 cwd: Optional[str] = None,
                 log: Optional[Callable[[str], None]] = None) -> None:
        self.argv = list(argv)
        self.env_extra = dict(env_extra) if env_extra else None
        self.cwd = cwd
        self._log = log or (lambda line: sys.stderr.write(line + "\n"))

        self.pid: int | None = None
        self.master_fd: int | None = None
        # Every HARNESS-ORIGINATED keystroke (auditable). Keyboard input never lands here —
        # see write_keys() and [CXC-TERM-CONVERGE].
        self.injection_log: list[dict] = []
        self.compact_phase_delay = COMPACT_PHASE_DELAY_S
        self._write_lock = threading.Lock()
        # An OPTIONAL second input fd the pump forwards to the child (the launcher's stdin).
        # A windowed host leaves it None and writes keystrokes through write_keys().
        self.input_fd: int | None = None
        # PTY GEOMETRY ([CXC-TERM-WINSIZE]). `term_rows`/`term_cols` hold what was ACTUALLY
        # applied, so a caller can assert against it rather than against its intent.
        self._term_size_override = tuple(term_size) if term_size else None
        self.term_rows: int | None = None
        self.term_cols: int | None = None
        self.exit_status: int | None = None

    # -- geometry ---------------------------------------------------------
    def set_term_size(self, rows: int, cols: int) -> None:
        self._term_size_override = (int(rows), int(cols))

    def current_winsize(self) -> tuple[int, int]:
        """The (rows, cols) the child SHOULD have right now — re-resolved each call so a SIGWINCH
        picks up the human's new terminal size."""
        rows = cols = None
        if self._term_size_override:
            rows, cols = self._term_size_override
        return resolve_winsize(rows, cols, tty_fd=probe_tty_fd())

    def apply_winsize(self, rows: int | None = None, cols: int | None = None,
                      xpixel: int = 0, ypixel: int = 0):
        """Set the child's terminal geometry on the PTY MASTER and make sure the child hears about
        it. Returns the applied (rows, cols), or None if there is no child / the ioctl failed.

        The kernel raises SIGWINCH on the slave's foreground process group only when the size
        actually CHANGES, so an explicit `killpg(SIGWINCH)` follows — it makes a re-assert of the
        same size still notify, and it is harmless (SIGWINCH's default disposition is ignore)."""
        if self.master_fd is None:
            return None
        if rows is None or cols is None:
            r, c = self.current_winsize()
            rows = r if rows is None else rows
            cols = c if cols is None else cols
        if not set_winsize(self.master_fd, rows, cols, xpixel, ypixel):
            self._log(f"[ptycore] {iso_now()} winsize ioctl FAILED ({rows}x{cols}) — "
                      f"child may render at 0x0")
            return None
        self.term_rows, self.term_cols = rows, cols
        if self.pid is not None:
            try:
                os.killpg(os.getpgid(self.pid), signal.SIGWINCH)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        self._log(f"[ptycore] {iso_now()} WINSIZE {rows}x{cols} (rows x cols) -> child pid={self.pid}")
        return (rows, cols)

    # -- spawn ------------------------------------------------------------
    def resolved_env(self) -> dict:
        """EXACTLY the environment `start()` will exec with — our own, stripped of the host's
        identity, plus `env_extra` ([CXC-IC-SELF-IDENTITY]).

        Public and side-effect-free so the sanitisation is assertable WITHOUT forking: the
        interesting failure ("the child inherited the host's session id") is otherwise only
        observable by spawning something that dumps its own env."""
        return sanitize_child_env(extra=self.env_extra)

    def start(self) -> None:
        """`pty.fork()` + `execvpe`. THE ONLY pty.fork in this tree ([CXC-TERM-PTYCORE],
        acceptance TERM-C11)."""
        import pty

        argv = list(self.argv)
        # Resolve BEFORE the fork so both sides apply the same numbers ([CXC-TERM-WINSIZE]),
        # and build the child env BEFORE it too — this is ordinary parent-side work, and doing
        # it between fork and exec would run allocator-heavy code in a forked child.
        rows, cols = self.current_winsize()
        env = self.resolved_env()
        pid, fd = pty.fork()
        if pid == 0:
            # Child: stdio is the slave PTY. Set the window size on fd 0 HERE, before `execvpe` —
            # this is what guarantees the spawned program can never read 0x0, with no dependence on
            # the parent winning a race. Exec the target. On failure, exit loudly.
            set_winsize(0, rows, cols)
            try:
                if self.cwd:
                    os.chdir(self.cwd)
                # ALWAYS execvpe with the SANITISED env — never the bare `execvp` that inherits
                # os.environ wholesale. That branch is what let the hosted instance come up
                # wearing its host's session id ([CXC-IC-SELF-IDENTITY], HOST_IDENTITY_ENV).
                os.execvpe(argv[0], argv, env)
            except Exception as exc:  # pragma: no cover - child path
                sys.stderr.write(f"ptycore: exec failed: {exc}\n")
                os._exit(127)
        # Parent
        self.pid = pid
        self.master_fd = fd
        # Master-side apply: establishes the fd every later resize uses, and is the backstop if the
        # child-side ioctl failed. Idempotent when the child already set the same size.
        self.apply_winsize(rows, cols)
        self._log(f"[ptycore] {iso_now()} SPAWN pid={pid} argv={argv!r}")

    # -- the ONE write path ([CXC-TERM-CONVERGE]) -------------------------
    def _write(self, data: bytes) -> None:
        """THE single write site. Both the bus actuation and the human's keyboard converge here,
        under one lock, because the actuation relay writes from a bus thread while the keyboard
        writes from the render thread — an unlocked write interleaves a keystroke into the middle
        of an injected `/compact` line."""
        if self.master_fd is None:
            raise RuntimeError("cannot write before start()")
        with self._write_lock:
            write_all(self.master_fd, data)

    def inject(self, keys: str, kind: str = "keystroke") -> None:
        """A HARNESS-ORIGINATED write — LOGGED (auditable, no silent action)."""
        if self.master_fd is None:
            raise RuntimeError("cannot inject before start()")
        rec = {"ts": iso_now(), "kind": kind, "keys": keys}
        self.injection_log.append(rec)
        self._log(f"[ptycore] {rec['ts']} INJECT {kind}: {keys!r}")
        self._write(keys.encode())

    def write_keys(self, data: bytes) -> None:
        """A HUMAN keystroke from the glass — the SAME write path, deliberately NOT logged.

        `injection_log`'s meaning is "writes the harness originated"; keyboard traffic in it would
        both destroy that audit property and put the human's typing into a durable record
        ([CXC-TERM-CONVERGE])."""
        if not data:
            return
        self._write(data)

    def inject_line(self, line: str, kind: str = INJECT_CMD) -> None:
        """Enter `line` into the running session AS IF THE HUMAN TYPED IT, in THREE phases —
        each a SEPARATE write with a beat between:

          1. CLEAR   — ESC ESC, dismissing any pre-existing prompt text so it can't collide.
          2. LINE    — the text to enter, VERBATIM, with NO trailing newline.
          3. SUBMIT  — a carriage return (\\r), the key the TUI reads as 'submit'.

        Why three phases (both live-verified 2026-07-19): a bulk write with a trailing \\n did NOT
        submit (\\n inserts a newline; the TUI submits on \\r), and pre-existing prompt text
        collided with the line."""
        self.inject(COMPACT_CLEAR, kind=f"{kind}-clear")
        time.sleep(self.compact_phase_delay)
        self.inject(line, kind=kind)
        time.sleep(self.compact_phase_delay)
        self.inject(COMPACT_SUBMIT, kind=f"{kind}-submit")

    # ⛔ THERE IS NO `inject_prompt` ([CXC-TERM-BOOTSTRAP-ARGV], DELETED 2026-08-11).
    # It existed for exactly one caller — the cold-start bootstrap, typed into the child after
    # a fixed settle delay — and that whole seam is gone: the prompt rides the child's ARGV, so
    # NOTHING is written to a starting child, ever.  `inject_line` (mid-session actuation, a
    # LIVE TUI with a reader that is definitely ready) is untouched and is a DIFFERENT seam
    # ([CXC-TERM-MIDSESSION-SEAM]).  If you are here to re-add a startup write, read
    # [CXC-TERM-BOOTSTRAP-SETTLE-RETIRED] first: the mechanism was unsound for EVERY value of
    # the delay, not mis-tuned.

    # -- the pump ---------------------------------------------------------
    def pump(self, on_output: Callable[[bytes], None], timeout: float = 0.2) -> bool:
        """ONE select cycle. Forwards, byte-for-byte:
             child PTY master -> on_output(data)   (the sink: a terminal fd, or a widget queue)
             self.input_fd    -> master            (an OPTIONAL second input fd; the launcher's
                                                    stdin. A windowed host leaves it None.)
        Returns False when the child's PTY has EOF'd (session over), True otherwise.

        [CXC-TERM-PTYCORE]'s ONE sanctioned behavioural change lives in this signature: the sink
        replaces an out_fd, so a widget does not have to invent a pipe to read its own child."""
        if self.master_fd is None:
            return False
        rfds = [self.master_fd]
        if self.input_fd is not None:
            rfds.append(self.input_fd)
        try:
            r, _, _ = select.select(rfds, [], [], timeout)
        except (OSError, ValueError):
            return True
        for fd in r:
            if fd == self.master_fd:
                try:
                    data = os.read(self.master_fd, 65536)
                except OSError:
                    return False
                if not data:
                    return False  # child EOF → session over
                on_output(data)
            else:  # secondary input → child
                try:
                    data = os.read(self.input_fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    # input EOF: stop forwarding it but keep the child running.
                    self.input_fd = None
                else:
                    self._write(data)
        return True

    def drain_final(self, on_output: Callable[[bytes], None]) -> None:
        """Flush any output the child left in the PTY buffer after it exited."""
        if self.master_fd is None:
            return
        try:
            os.set_blocking(self.master_fd, False)
        except (OSError, ValueError):
            return
        while True:
            try:
                data = os.read(self.master_fd, 65536)
            except (BlockingIOError, OSError):
                break
            if not data:
                break
            on_output(data)

    # -- lifecycle --------------------------------------------------------
    def child_exited(self) -> bool:
        if self.pid is None:
            return True
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            return True
        if wpid == self.pid:
            self.exit_status = status
            return True
        return False

    def exit_description(self) -> str:
        """`exited <code>` / `exited (signal N)` for the status strip ([CXC-TERM-BOUNDARIES]
        rows 3 and 4). Returns "" while the child is still running."""
        st = self.exit_status
        if st is None:
            return ""
        if os.WIFSIGNALED(st):
            return f"exited (signal {os.WTERMSIG(st)})"
        if os.WIFEXITED(st):
            return f"exited {os.WEXITSTATUS(st)}"
        return f"exited (status {st})"

    def shutdown(self, reap_s: float = 0.5) -> None:
        """SIGTERM the child, REAP it, close the master fd. Idempotent.

        The reap matters for an in-process host: a windowed console tears children down
        on every tab switch, and an unreaped child stays a ZOMBIE — which still answers
        `kill(pid, 0)`, so "did the teardown work?" cannot even be asked honestly without
        it. The CLI launcher exits immediately after shutdown() so init reaped for it."""
        if self.pid is not None:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
            deadline = time.monotonic() + reap_s
            while time.monotonic() < deadline:
                if self.child_exited():
                    break
                time.sleep(0.01)
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


# ── Actuation-directive schema (shared by both hosts) ───────────────────────────────────────
# The Intelligence emits a directive out `compose_projects/intelligence/<id>/out`. Two commands ride this seam:
#
#     {"cmd": "compact", "direction": "<the full custom /compact direction text>"}
#         -> the host types + submits  `/compact <direction>`   (the lifecycle self-fire)
#     {"cmd": "inject",  "text":      "<an arbitrary message>"}
#         -> the host types + submits  the message VERBATIM
#
# Anything else on `.../out` (an ordinary reply with no `cmd`) is IGNORED — no injection.
COMPACT_CMD = "compact"


def _actuation_from_dict(d) -> tuple[str, str] | None:
    if not isinstance(d, dict):
        return None
    if d.get("cmd") == COMPACT_CMD:
        direction = d.get("direction")
        if isinstance(direction, str) and direction.strip():
            return (COMPACT_CMD, f"/compact {direction}")
    elif d.get("cmd") == INJECT_CMD:
        text = d.get("text")
        if isinstance(text, str) and text.strip():
            return (INJECT_CMD, text)
    return None


# ── The CONTEXT QUERY — a directive that presses NO key ─────────────────────────────────────
# `{"cmd":"context"}` (aliases `status` / `context_query`) is answered by the monitor on the
# `in` topic, never by a keystroke.  It lives HERE, beside `parse_actuation`, because BOTH hosts
# must answer it identically: a widget-hosted child that got silence would be asking a question
# no one had told it was unanswerable ([CXC-TERM-CHILD-TELEMETRY]).
CONTEXT_CMD = "context"
CONTEXT_CMD_ALIASES = (CONTEXT_CMD, "status", "context_query")


def _is_context_query_dict(d) -> bool:
    return isinstance(d, dict) and d.get("cmd") in CONTEXT_CMD_ALIASES


def parse_context_query(payload) -> bool:
    """True iff `payload` is a well-formed context-query directive, at the top level OR nested
    inside the reply-tool `text` envelope.  A query is NOT an actuation: it presses no key."""
    if _is_context_query_dict(payload):
        return True
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            try:
                inner = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                inner = None
            if _is_context_query_dict(inner):
                return True
    return False


def parse_actuation(payload) -> tuple[str, str] | None:
    """Return (kind, line) if `payload` is a well-formed actuation directive, else None. Accepts
    the marker at the top level OR nested inside the out-message `text` field (how the channel's
    `reply` tool carries it). `line` is exactly what the host types into the prompt + submits."""
    hit = _actuation_from_dict(payload)
    if hit is not None:
        return hit
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str):
            try:
                inner = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                inner = None
            hit = _actuation_from_dict(inner)
            if hit is not None:
                return hit
    return None


# ── The COLD-START re-register bootstrap ([CXC-ICL-COLD], RATIFIED) ──────────────────────────
# MOVED here from `launcher.py` for the same reason everything else in this file moved: BOTH
# hosts must do it IDENTICALLY.  [CXC-TERM-DIALOGS] says in terms that "the widget inherits
# that behaviour unchanged", and a second copy of the prompt builder is exactly the
# fork-divergence [CXC-TERM-PTYCORE] exists to prevent.  `launcher.py` re-exports these names.
#
# ⛔ NOTHING IS EVER TYPED AT STARTUP ([CXC-TERM-BOOTSTRAP-ARGV], ratified 2026-08-11).  The
# prompt rides the child's POSITIONAL ARGV — `claude [options] [command] [prompt]` — so
# [CXC-TERM-DIALOGS] ("no auto-answered dialogs, ever") holds BY CONSTRUCTION rather than by
# timing.  The settle-delay mechanism (`BOOTSTRAP_DELAY_S`, `bootstrap_delay_s`,
# `arm_bootstrap`, `_type_bootstrap`, `PtyChild.inject_prompt`) is DELETED, not deprecated:
# on its first live firing it landed in a blocking `Enter y/n:` gate and echoed `^[[200~` as
# literal text.  A fixed timer standing in for a state check is unsound for EVERY value, so
# there was nothing to tune ([CXC-TERM-BOOTSTRAP-SETTLE-RETIRED] keeps the evidence).
#
# THE DELIVERY SHAPE (David's ratified form, and the ONLY one with live evidence — he typed it
# by hand and the child read all nine artifacts and ran `lts_selfcheck`): the generator still
# runs, its 6 KB output is written to a FILE, and argv carries a SHORT instruction naming that
# file by ABSOLUTE path.  Explicit, self-contained, no discovery step, and small enough that no
# exec limit is anywhere near ([DD-TERM-BOOTSTRAP-ARGV-FORM]'s "explicit form" argument applied
# one level out: the argv names the prompt file, and the prompt file's own first line names the
# manifest).

# The argv element ceiling.  A prompt that cannot be delivered is a REFUSAL naming the size,
# never a truncated exec — a silently clipped orientation prompt is the unoriented-instance
# failure with a longer fuse ([CXC-TERM-MANIFEST-CHAIN]).
BOOTSTRAP_ARGV_MAX = 131072


def bootstrap_enabled(explicit: Optional[bool] = None) -> bool:
    """Is the cold-start re-register bootstrap ON?  DEFAULT: **YES** ([CXC-ICL-COLD]).

    David, 2026-08-10: "The re-register bootstrap is not experimental any longer, it works
    quite well — it should be the default for new instances."  `BOOTSTRAP=0` (or `false`/`no`/
    `off`) is the explicit opt-OUT and is the ONLY way to get the pre-2026-08-11 behaviour;
    an UNSET or blank `BOOTSTRAP` means ON.  `explicit` (a scene property / constructor arg)
    overrides the env entirely."""
    if explicit is not None:
        return bool(explicit)
    v = os.environ.get("BOOTSTRAP")
    if v is None or not v.strip():
        return True
    return v.strip().lower() not in ("0", "false", "no", "off")


def repo_root() -> str:
    """<repo> — DELEGATED to `composeio.instance_home.repo_root` so the dirname walk has one
    definition rather than two that agree until someone moves a file ([CXC-FC-ANTI])."""
    from composeio.instance_home import repo_root as _repo_root
    return _repo_root()


def default_meta_manifest(intelligence_id: str) -> str:
    """The durable meta-object the Config tab writes through: `compose_projects/intelligence/meta/<id>.meta.json`.
    `META_MANIFEST` overrides.  Defaulting it HERE closes a silent no-op: `main()` used to gate
    the bootstrap on `cfg.get("meta_manifest")`, which is `os.environ.get("META_MANIFEST")` and
    is therefore None unless an operator set it — so even `BOOTSTRAP=1` injected nothing."""
    return os.environ.get(
        "META_MANIFEST",
        os.path.join(repo_root(), "compose_projects", "intelligence", "meta", f"{intelligence_id}.meta.json"))


def meta_manifest_for(intelligence_id: str, role: str = "") -> str:
    """`compose_projects/intelligence/meta/<id>.meta.json` for a bare id — NO `META_MANIFEST` read, so a chain
    leg can be spelled without the operator override swallowing it."""
    del role
    return os.path.join(repo_root(), "compose_projects", "intelligence", "meta",
                        f"{intelligence_id}.meta.json")


def resolve_meta_manifest(intelligence_id: str, role: str = "") -> tuple:
    """The manifest CHAIN ([CXC-TERM-MANIFEST-CHAIN]).  Returns `(path_or_None, tried)`.

    The order is NORMATIVE and identical in BOTH hosts:

      1. `META_MANIFEST`, when explicitly set — the operator override, unchanged;
      2. `compose_projects/intelligence/meta/<id>.meta.json` — a manifest curated for THIS instance;
      3. `compose_projects/intelligence/meta/<role>.meta.json` — the ROLE's manifest, which is what makes N
         instances of one role possible at all;
      4. exhausted -> `(None, tried)` and the CALLER REFUSES.  No fork, no degraded boot, no
         silent orientation gap.

    WHY A CHAIN AT ALL (it is required BY the distinct-id ruling, not a nicety beside it):
    give every hosted child an id distinct from its host's ([CXC-TERM-CHILD-ID]) and leg 2
    MISSES for every child that is not hand-curated.  Manifest-per-id cannot scale to N
    instances of one role, so without leg 3 the identity fix would have manufactured a fleet
    of unoriented instances.

    First READABLE leg wins: an explicit `META_MANIFEST` naming a file that does not exist
    does not silently shadow a curated one, and it still appears in `tried` so the refusal
    names it."""
    tried: list = []
    override = os.environ.get("META_MANIFEST", "").strip()
    if override:
        tried.append(override)
    if intelligence_id:
        tried.append(meta_manifest_for(intelligence_id))
    if role and role != intelligence_id:
        tried.append(meta_manifest_for(role))
    for path in tried:
        if os.path.isfile(path):
            return (path, tried)
    return (None, tried)


class BootstrapRefused(Exception):
    """The spawn is REFUSED before `pty.fork()` ([CXC-TERM-MANIFEST-CHAIN]).

    Carries the whole diagnosis as data, because it has to reach THREE surfaces: the glass
    (a refusal plate), the bus (`compose_projects/intelligence/<id>/terminal/refusal`) and a non-zero CLI
    exit.  A refusal that only reaches a log is the failure this exception exists to end —
    `terminal.py::_log` was literally `pass`, so "degrades and logs" meant DISCARDS."""

    def __init__(self, reason: str, *, intelligence_id: str = "", role: str = "",
                 paths_tried: Optional[list] = None, detail: str = "") -> None:
        self.reason = reason
        self.intelligence_id = intelligence_id
        self.role = role
        self.paths_tried = list(paths_tried or [])
        self.detail = detail
        super().__init__(self.message())

    def message(self) -> str:
        bits = [f"REFUSED TO SPAWN — {self.reason}"]
        if self.intelligence_id or self.role:
            bits.append(f"id={self.intelligence_id!r} role={self.role!r}")
        if self.paths_tried:
            bits.append("tried, in order: " + " -> ".join(self.paths_tried))
        if self.detail:
            bits.append(self.detail)
        return " · ".join(bits)

    def payload(self) -> dict:
        """The `compose_projects/intelligence/<id>/terminal/refusal` body — the same shape a headless test and
        a monitor read ([CXC-TERM-DEGRADE]'s bus-visible rule, applied to a refusal)."""
        return {"refused": True, "reason": self.reason, "id": self.intelligence_id,
                "role": self.role, "paths_tried": list(self.paths_tried),
                "detail": self.detail, "ts": iso_now()}


def host_intelligence_id() -> str:
    """The id THIS process is already wearing, if any — the value a hosted child must NOT be
    given ([CXC-TERM-CHILD-ID]).  `INTELLIGENCE_ID` is what the console, the launcher and
    `channel.mjs` all key on."""
    return os.environ.get("INTELLIGENCE_ID", "").strip()


def load_manifest(path: str) -> dict:
    """Load the meta-object manifest JSON. NOTE: this reads the MANIFEST file itself (given
    via META_MANIFEST), not any artifact it references. Callers must not point META_MANIFEST
    at a `.claude/` path (the harness must not read under `.claude/`)."""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _compact_summaries_rel() -> str:
    """The repo-relative compact-summaries dir, from the ONE source of truth
    (`composeio.bootstrap_paths`)."""
    from composeio.bootstrap_paths import COMPACT_SUMMARIES_REL
    return COMPACT_SUMMARIES_REL


def _platform_specs_rel() -> str:
    """The repo-relative platform-specs corpus dir, from the ONE source of truth.
    [SBR-PATHS-SINGLE-SOURCE]: "no code file hard-codes a continuity path literal"."""
    from composeio.bootstrap_paths import PLATFORM_SPECS_REL
    return PLATFORM_SPECS_REL


# ── The wake ritual is SPECIFIED, not restated here ([SPEC:SESSION-BOOTSTRAP-READ]) ──────────
# The prompt below REFERENCES the ritual; it does not paraphrase it.  That is not style — a
# generator that restates ratified rules in a Python string is a SECOND implementation of the
# wake procedure whose copy drifts silently from the spec ([INSIGHT-FORK-DIVERGENCE-IS-A-CLASS],
# and [SBR-PATHS-SINGLE-SOURCE] forbids the path-literal version of the same mistake).  So:
#
#   * the RULES live in `45-session-bootstrap-read.lts` — [SBR-TRUST] (never silently omit),
#     [SBR-STRUCTURE] (slice order), [SBR-READ-VS-REASON] (wisdom is reasoned, not read),
#     [SBR-DEGRADE] (degrade loudly; STALE is halt-and-fix, not a degrade);
#   * the prompt CITES them by node id and points at the readable twin BY PATH, exactly as it
#     already does for the role descriptor and the gifts (carry-depth-by-reference);
#   * the STEP NAMES below mirror [SBR-PROCESS-FLOW] so the instance can report against them —
#     names are identifiers, like node ids, not rule content.
#
# SBR_FLOW_STEPS is a MIRROR with an enforcing test, the same pattern `bootstrap_paths.py` uses
# ([SBR-PATHS-SINGLE-SOURCE] `enforced-by`): `test_ptycore.py` parses [SBR-PROCESS-FLOW] out of
# the corpus and FAILS if this tuple drifts from it.  Add a step to the spec and the build goes
# red until the prompt covers it — which is what stops the next amendment from being applied
# half-way ([INSIGHT-PARTIAL-SPEC-AMENDMENT-IS-A-FORK-GENERATOR]).
SBR_SPEC_ID = "[SPEC:SESSION-BOOTSTRAP-READ]"
SBR_SPEC_FILE = "45-session-bootstrap-read.lts"
SBR_FLOW_NODE = "[SBR-PROCESS-FLOW]"
SBR_FLOW_STEPS = (
    "cold-start",
    "read-the-ritual",
    "resolve-mcp-surface",
    "freshness-gate",
    "slice-0-PLATFORM-OVERVIEW",
    "slice-1-WARTS",
    "slice-2-OPEN",
    "slice-3-IN-FLIGHT",
    "slice-4-SETTLED+FRAME",
    "slice-5-DUSK-GIFT",
    "read-vs-reason",
    "report-coverage",
    "answer-part-C",
    "begin-work-oriented",
)


def sbr_spec_path() -> str:
    """The walkable ritual spec, repo-relative, composed from the single-source corpus dir."""
    return _platform_specs_rel() + SBR_SPEC_FILE


# ── The manifest CONSUMPTION CONTRACT ([CXC-IC-META], the agreement check) ───────────────────
# The prompt used to consume SIX manifest keys and silently ignore FIVE path-bearing ones —
# `principles`, `method_flow`, `bootstrap_read`, `toolbox` (plus the `registration` contract).
# So a spawned instance was never pointed at `bootstrap/session-bootstrap-read.md`, the
# STRATEGIC READ that loads CURRENT state, and arrived with a role and a warm model and NO
# ground truth: the discover-as-you-go the boot read exists to end.
#
# The fix that closes the CLASS, not just the instance: sections are declared as DATA below,
# and `unconsumed_manifest_keys()` reports any key that is neither consumed nor explicitly
# omitted. A future manifest key therefore cannot go missing the way these five did — the
# prompt NAMES it at runtime (degrade LOUDLY) and a test fails the build.
#
# `method_flow` is the proof this is needed: it is a first-class BOOTSTRAP document (David
# 2026-07-26, the maker pivot — "post-pivot the whole build focus is maker projects") and it
# was missed BY EYE even while cataloguing exactly this defect. A hand-kept list is the thing
# that failed; only a mechanical comparison closes it.

# Keys that legitimately carry NO bootstrap instruction, each with the reason it is skipped.
# Being on this list is a DECISION, not an oversight — that is the whole distinction.
DELIBERATELY_OMITTED: dict = {
    "$schema": "a JSON-schema pointer, not content",
    "id": "the FALLBACK peer name for the opening line, used only when no running instance "
          "id is supplied; the INSTANCE id wins ([CXC-TERM-CHILD-ID]) — never a section",
    "role": "a display string ('ComposeUX / ComposeIO Architect'); the role_descriptor "
            "artifact carries the substance",
    "description": "describes the meta-object itself, for a human reading the JSON",
    "soul_note": "explains WHY `soul` is null; the instance needs the artifact, not the note",
    "console": "console UI layout (tabs/widgets/placements) — the console reads it, not the "
               "Intelligence",
    "window_geometry": "window placement, read by the window manager",
}

# The ORDER is the wake ritual's own ([SBR-PROCESS-FLOW], `bootstrap/session-bootstrap-read.md`
# §6): identity, then the shared frame, then the tools you will need to RESOLVE the MCP
# surface, then the STRATEGIC READ (state orients), and only then the dusk gifts (wisdom
# registers). bootstrap_read BEFORE the gifts is load-bearing, not cosmetic.
_SECTIONS = (
    # (manifest key, HEADING, the one-line instruction appended after the path)
    ("role_descriptor", "ROLE DESCRIPTOR", "who you are, this session"),
    ("soul", "SOUL", "your stable identity across seams"),
    ("principles", "PRINCIPLES", "how we work together and how we build — the shared frame, "
                                 "not the operational role"),
    ("method_flow", "METHOD FLOW", "HOW a maker project flows (solder -> slot -> widget); the "
                                   "pin->slot->binding seam is load-bearing"),
    ("toolbox", "TOOLBOX", "what you can reach for, and the HALT-AND-FIX vs DEGRADE-AND-FLAG "
                           "call when one is not working — read it BEFORE the strategic read, "
                           "because that read runs against these tools"),
    ("bootstrap_read", "THE STRATEGIC READ", "the wake procedure itself: query slices 0-4 for "
                                             "CURRENT state, in descending re-trip cost. This "
                                             "is where you get GROUND TRUTH instead of "
                                             "discovering as you go"),
)


def unconsumed_manifest_keys(manifest: dict) -> list:
    """Manifest keys this prompt neither CONSUMES nor DELIBERATELY OMITS — sorted, so the
    report is stable.

    The agreement check ([SBR-TRUST] applied to the prompt generator itself): a boot input
    that is silently partial is worse than one that is loudly incomplete, because it looks
    complete. Callers surface this; they never swallow it."""
    consumed = {k for k, _h, _w in _SECTIONS} | {
        "id", "dusk_gifts", "channel", "lifecycle", "registration"}
    known = consumed | set(DELIBERATELY_OMITTED)
    return sorted(k for k in manifest if k not in known)


def build_bootstrap_prompt(manifest: dict, *, instance_id: str = "") -> str:
    """Build a cold-start re-register prompt from a meta-object manifest ([CXC-IC-META]).

    THE MANIFEST IS THE ARTIFACT SOURCE; THE INSTANCE ID IS THE IDENTITY ([CXC-TERM-CHILD-ID],
    fixed 2026-08-11).  These are two different questions and this function used to answer both
    from the manifest: `ident` was `manifest["id"]`, so the prompt named whatever the MANIFEST
    was called.  That was invisible while only INSTANCE manifests existed (`architect.meta.json`,
    whose id happens to equal its role) and becomes a live identity mismatch the moment chain
    leg 3 resolves a ROLE manifest ([CXC-TERM-MANIFEST-CHAIN]): a child spawned as
    `architect-hosted`, orienting from `composeux-architect.meta.json`, would be TOLD it is
    `composeux-architect` while its channel topics, its `intelligence-launcher-<id>` lock, its
    `self/<id>/*` telemetry and its refusal publish all say `architect-hosted`.  It would post
    to a channel under a name nobody told it it has — precisely the class the distinct-id ruling
    exists to close.  So the RUNNING INSTANCE's id wins; `manifest["id"]` is the fallback for a
    caller that has no instance id to give (a hand-built manifest, a preview render).

    CARRY-DEPTH-BY-REFERENCE, and this discipline is load-bearing: the prompt POINTS to every
    artifact BY PATH and instructs the instance to READ them and RE-REGISTER off them.  It
    NEVER inlines artifact contents — this function does NOT open any artifact file (so it
    also never touches a `.claude/` path; such paths are carried through as plain text for the
    running instance to read itself).  The load-bearing state lives in the committed
    artifacts; only re-reading them rebuilds it.

    THE RITUAL IS CITED, NOT RESTATED.  Everything about HOW to boot — that wisdom is reasoned
    rather than read, what the slices are and in what order, what coverage must be declared —
    is already ratified in [SPEC:SESSION-BOOTSTRAP-READ].  This function points at it and names
    its steps; it does NOT paraphrase the rules, because a paraphrase in a Python string is a
    second implementation that drifts ([INSIGHT-FORK-DIVERGENCE-IS-A-CLASS]).  If a rule seems
    missing from the prompt, the fix is to amend the SPEC and let this cite the amendment."""
    ident = str(instance_id or "").strip() or manifest.get("id") or "intelligence"
    gifts = manifest.get("dusk_gifts") or []
    channel = manifest.get("channel") or {}
    lifecycle = manifest.get("lifecycle") or {}
    registration = manifest.get("registration") or {}

    lines: list[str] = []
    lines.append(
        f"You are the Intelligence peer \"{ident}\" on ComposeBus. Before you do ANY work, "
        f"orient yourself by READING the artifacts referenced below and RE-REGISTERING off "
        f"them. These are pointers, not contents — the load-bearing state lives in the "
        f"committed artifacts, and only re-reading them (not this prompt, not a lossy "
        f"summary) rebuilds it. Work through the sections in order; the order is the wake "
        f"ritual's own."
    )
    lines.append("")
    summaries_dir = lifecycle.get("compact_summaries_dir") or _compact_summaries_rel()
    lines.append(
        f"0. FIRST, before reading anything below: if a compaction summary was handed to you at "
        f"session start, WRITE IT OUT VERBATIM to {summaries_dir}"
        f"compact-summary-<session-id-short>-<YYYY-MM-DD>.md — the seam's own record (that "
        f"directory's README explains why). It is the one artifact of this crossing that is "
        f"otherwise never persisted; capture it before it is gone, then continue with orientation."
    )
    lines.append("")

    n = 0  # dynamic numbering — any section may be absent from a given manifest
    for key, heading, why_read in _SECTIONS:
        entry = manifest.get(key)
        path = entry.get("path") if isinstance(entry, dict) else None
        if not path:
            continue                      # absent or null (e.g. `soul` today) — skip cleanly
        n += 1
        lines.append(f"{n}. {heading} ({why_read}): READ {path}")

    if gifts:
        n += 1
        lines.append("")
        lines.append(
            f"{n}. DUSK GIFTS — slice 5 of the ritual. This slice is an ENGAGEMENT, not a read: "
            f"apply [SBR-READ-VS-REASON] to each artifact below, and follow the reasoning "
            f"discipline registration-protocol.md states in its own Part B heading and "
            f"README.md. Do not substitute reading for it:")
        for i, gift in enumerate(gifts, start=1):
            path = gift.get("path", "") if isinstance(gift, dict) else str(gift)
            why = gift.get("why", "") if isinstance(gift, dict) else ""
            suffix = f"  — {why}" if why else ""
            lines.append(f"   {n}.{i}. READ {path}{suffix}")

    required = registration.get("required") or []
    if required:
        n += 1
        lines.append("")
        lines.append(
            f"{n}. REGISTRATION CONTRACT — `self/registered` is TRUE for this session IFF every "
            f"artifact below was actually Read this session. The sensor reports it PER ARTIFACT, "
            f"so a partial read shows as a red row, not as a green whole:")
        for path in required:
            lines.append(f"   - {path}")

    # The agreement check, surfaced IN the prompt: a key this builder does not know how to
    # carry is NAMED so the instance reads it itself, rather than vanishing.
    missing = unconsumed_manifest_keys(manifest)
    if missing:
        n += 1
        lines.append("")
        lines.append(
            f"{n}. UNCARRIED MANIFEST KEYS — this prompt does not know how to carry "
            f"{', '.join(missing)}. That is a GAP IN THE PROMPT BUILDER, not in you: open the "
            f"meta-object yourself, read what those keys point at, and REPORT the gap so it is "
            f"fixed at the source.")

    lines.append("")
    lines.append(
        f"RUN THE RITUAL. The wake procedure is SPECIFIED — do not improvise it and do not "
        f"take this prompt as its summary. {SBR_SPEC_ID} is normative "
        f"({sbr_spec_path()}); the readable twin is the STRATEGIC READ artifact above. Follow "
        f"{SBR_FLOW_NODE} — every step, in its order:")
    lines.append("   " + " -> ".join(SBR_FLOW_STEPS))
    lines.append(
        "Its rules bind you as written, not as remembered: [SBR-TRUST] (a read NEVER silently "
        "omits), [SBR-STRUCTURE] (the slices, in descending re-trip-cost order), "
        "[SBR-READ-VS-REASON] (state is read to orient; wisdom is reasoned to own), and "
        "[SBR-DEGRADE] (degrade LOUDLY on an UNAVAILABLE surface — and a STALE one is "
        "halt-and-fix, never a fallback). Read those nodes; do not rely on this paraphrase of "
        "their names.")
    lines.append("")
    lines.append(
        f"THEN REPORT, BEFORE ANY OTHER WORK. Steps `report-coverage` and `answer-part-C` are "
        f"already steps of {SBR_FLOW_NODE}; post their output where it can be READ rather than "
        f"assumed, because an unoriented instance and an oriented one look identical from "
        f"outside. In the report, in the vocabulary those nodes define:")
    lines.append(
        "   (a) name INDIVIDUALLY every artifact above you read, and every one you could NOT "
        "(missing file, unreadable path, tool unavailable) — [SBR-TRUST] makes the could-not "
        "list a REQUIRED output, so a count alone does not satisfy it;")
    lines.append(
        "   (b) declare per-slice coverage and the totals, per `report-coverage`/[SBR-DEGRADE];")
    lines.append(
        "   (c) write out your `answer-part-C` answers, so the registration is auditable;")
    lines.append(
        f"   (d) state plainly whether you completed every step of {SBR_FLOW_NODE} through "
        f"`begin-work-oriented` — and if not, name the step you are blocked at and what would "
        f"unblock it. Blocked-and-named is a good outcome; a cheerful ready with no (a)-(c) "
        f"behind it is worthless.")
    lines.append(
        "Only then begin work. Confirm re-registration (the self/registered signal should flip "
        "true) as part of the report.")

    if channel.get("server"):
        mqtt = channel.get("mqtt", "")
        lines.append("")
        lines.append(
            f"Your channel is \"{channel['server']}\"{(' on ' + mqtt) if mqtt else ''}; "
            f"messages arrive as <channel> events and you answer with the `reply` tool. Post "
            f"the self-report there."
        )
    thr = lifecycle.get("compact_threshold")
    if thr is not None:
        # WHO TYPES IT ([CXC-TERM-CHILD-TELEMETRY]): this string used to say "The launcher
        # will type it", which is FALSE for a widget-hosted child — the terminal widget owns
        # that PTY, not `launcher.py`.  ONE string is emitted by BOTH hosts, so it names the
        # actuator generically.  Naming a specific component in shared text is the fork this
        # file's builder keeps having to close ([CXC-TERM-BOOTSTRAP-DERIVED]).
        lines.append(
            f"Lifecycle: watch self/context_level; at your chosen threshold (~{thr}) PAUSE & "
            f"REFLECT — refine your dusk gifts, write the handoff, and AUTHOR a custom "
            f"/compact direction, then emit it out your channel as "
            f"{{\"cmd\":\"compact\",\"direction\":\"...\"}}. The HOST that owns this session's "
            f"PTY will type it; the decision — when, whether, and the direction — is yours "
            f"alone."
        )
    return "\n".join(lines)


# ⛔ THERE IS NO `build_unmanifested_prompt` ([CXC-TERM-MANIFEST-CHAIN], DELETED 2026-08-11).
# It built a DEGRADED prompt for a child whose manifest could not be read, and shipping it was
# the defensible-but-rejected branch: a booted-but-unoriented child still takes a channel, still
# takes the launcher singleton, still renders as a working session, and its one diagnostic went
# to a sink that discarded it (`terminal.py::_log` was `pass`).  Refusing costs one keypress to
# retry; degrading costs a session that LOOKS right and is not.  `BOOTSTRAP=0` remains the ONE
# way to get a deliberately unoriented child, and it is an explicit human act.


def build_bootstrap_for(intelligence_id: str, resume_session: str = "", *,
                        role: str = "",
                        manifest_path: Optional[str] = None,
                        enabled: Optional[bool] = None,
                        log: Optional[Callable[[str], None]] = None) -> str:
    """The prompt a COLD start should be given, or "" when there is nothing to deliver.

    THE decision point, in one place, so both hosts answer it identically:
      * RESUME/MOVE (`resume_session` set) -> "".  A re-host already HOLDS its context; there
        is no seam to cross and nothing to re-register from.
      * `BOOTSTRAP=0` (or `enabled=False`) -> "".  The explicit opt-OUT ([CXC-ICL-COLD] (c)),
        and the ONLY way to get a deliberately unoriented child.
      * otherwise -> the manifest-derived prompt, resolved through the CHAIN
        ([CXC-TERM-MANIFEST-CHAIN]): explicit `manifest_path` / `META_MANIFEST`, then
        `<id>.meta.json`, then `<role>.meta.json`.
      * the chain EXHAUSTED, or a manifest that will not parse -> **`BootstrapRefused`**.
        Not a degraded prompt, not "" and a log line: the caller refuses BEFORE `pty.fork()`
        and puts the refusal on the glass and on the bus."""
    say = log or (lambda _line: None)
    if resume_session:
        return ""
    if not bootstrap_enabled(enabled):
        say(f"[ptycore] {iso_now()} bootstrap OPTED OUT (BOOTSTRAP=0) — spawning unoriented, "
            f"deliberately")
        return ""
    if manifest_path:
        path, tried = (manifest_path, [manifest_path])
        if not os.path.isfile(path):
            path = None
    else:
        path, tried = resolve_meta_manifest(intelligence_id, role)
    if path is None:
        raise BootstrapRefused("no-manifest", intelligence_id=intelligence_id, role=role,
                               paths_tried=tried,
                               detail="set BOOTSTRAP=0 to spawn deliberately unoriented")
    try:
        manifest = load_manifest(path)
    except (OSError, ValueError) as exc:
        raise BootstrapRefused("unreadable-manifest", intelligence_id=intelligence_id,
                               role=role, paths_tried=tried,
                               detail=f"{path}: {exc!r}") from exc
    missing = unconsumed_manifest_keys(manifest)
    if missing:
        say(f"[ptycore] {iso_now()} bootstrap AGREEMENT GAP — manifest {path!r} declares "
            f"{missing!r} which the prompt builder cannot carry; NAMED in the prompt")
    say(f"[ptycore] {iso_now()} bootstrap manifest RESOLVED -> {path}")
    # The INSTANCE id is threaded through explicitly.  `intelligence_id` used to be consumed
    # ONLY to resolve the path, and the identity the prompt announced came back out of the
    # manifest — so a ROLE manifest renamed the child ([CXC-TERM-CHILD-ID]).
    return build_bootstrap_prompt(manifest, instance_id=intelligence_id)


# ── Bootstrap DELIVERY: a file on disk + a short argv instruction ([CXC-TERM-BOOTSTRAP-ARGV])
def bootstrap_prompt_dir() -> str:
    """Where the generated bootstrap prompt is written.  NOT under `.claude/` (the harness
    must not read there) and NOT in the repo (a child may be spawned outside it).
    `BOOTSTRAP_PROMPT_DIR` overrides for an operator who wants them kept somewhere durable."""
    import tempfile
    return os.environ.get("BOOTSTRAP_PROMPT_DIR",
                          os.path.join(tempfile.gettempdir(), "composeio-bootstrap"))


def write_bootstrap_prompt(intelligence_id: str, prompt: str, *,
                           manifest_path: str = "") -> str:
    """Write `prompt` to a per-spawn file and return its ABSOLUTE path.

    The file's FIRST LINE names the manifest by absolute path — [DD-TERM-BOOTSTRAP-ARGV-FORM]'s
    EXPLICIT form, preserved verbatim inside the artifact the child actually reads: a cold
    instance has not read the corpus, does not know which id it is wearing, and cannot resolve
    "the bootstrap" to a file, so nothing here may assume context."""
    d = bootstrap_prompt_dir()
    os.makedirs(d, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    path = os.path.join(d, f"bootstrap-{intelligence_id or 'unnamed'}-{stamp}.md")
    head = ""
    if manifest_path:
        head = (f"Read {manifest_path} and complete the bootstrap it describes.\n\n")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + prompt + "\n")
    return os.path.abspath(path)


def argv_bootstrap_instruction(prompt_file: str) -> str:
    """The SHORT instruction argv carries — it names the prompt file by ABSOLUTE path.

    NOT the 6 KB body inline and NOT a bare "complete bootstrap": this exact shape is the ONLY
    one with live evidence.  David typed it by hand on 2026-08-11 and the child read all nine
    artifacts and ran `lts_selfcheck` — a step that exists only in the CITED spec, which is how
    we know the pointer was actually followed rather than paraphrased."""
    return (f"Read {prompt_file} and complete the bootstrap it describes. "
            f"Follow it exactly, including every artifact it names.")


def build_bootstrap_argv(intelligence_id: str, resume_session: str = "", *,
                         role: str = "",
                         manifest_path: Optional[str] = None,
                         enabled: Optional[bool] = None,
                         log: Optional[Callable[[str], None]] = None) -> str:
    """The POSITIONAL PROMPT ARGUMENT for a cold spawn, or "" when there is none.

    The whole cold-start delivery, in ONE place both hosts call ([CXC-TERM-BOOTSTRAP-ARGV]):
    resolve the manifest chain, generate the prompt ([CXC-TERM-BOOTSTRAP-DERIVED] unchanged),
    WRITE it to a file, and return the short instruction that names that file.  Raises
    `BootstrapRefused` when the chain is exhausted or the instruction would exceed
    `BOOTSTRAP_ARGV_MAX` — never a truncated exec."""
    say = log or (lambda _line: None)
    if not manifest_path:
        resolved, _tried = resolve_meta_manifest(intelligence_id, role)
    else:
        resolved = manifest_path
    prompt = build_bootstrap_for(intelligence_id, resume_session, role=role,
                                 manifest_path=manifest_path, enabled=enabled, log=log)
    if not prompt:
        return ""
    prompt_file = write_bootstrap_prompt(intelligence_id, prompt,
                                         manifest_path=resolved or "")
    instruction = argv_bootstrap_instruction(prompt_file)
    if len(instruction.encode("utf-8")) > BOOTSTRAP_ARGV_MAX:
        raise BootstrapRefused(
            "bootstrap-argv-too-large", intelligence_id=intelligence_id, role=role,
            detail=f"{len(instruction.encode('utf-8'))} bytes > {BOOTSTRAP_ARGV_MAX}")
    say(f"[ptycore] {iso_now()} bootstrap ON ARGV -> {prompt_file} "
        f"({len(prompt)} chars in the file, {len(instruction)} on argv); "
        f"NOTHING is typed at startup")
    return instruction


# ── The HARNESS MONITOR factory — ONE construction site, BOTH hosts ─────────────────────────
# [CXC-TERM-CHILD-TELEMETRY].  `HarnessMonitor` was instantiated ONLY by `launcher.py`
# (`build_monitor`, line 389; started on a thread at line 782) and `terminal.py` referenced it
# NOWHERE — so a widget-hosted child had no threshold alerts, no quiescence heartbeat and no
# `{"cmd":"context"}` fuel gauge, while the bootstrap prompt told it to watch
# `self/context_level`, a permanently silent namespace.  The capability already existed, was
# already tested, and already drew the FYI-vs-GUIDANCE distinction that makes 95 actionable;
# the ONLY gap was that its single construction site lived in a module a widget structurally
# cannot import (`Launcher.run()` installs signal handlers and takes a superseding flock —
# it is a `main()`, not a component).  So this is an EXTRACTION, not a feature: the factory
# moves HERE and both hosts call it.  A second monitor implementation is a review reject.
DEFAULT_CONTEXT_THRESHOLDS = (65, 80, 90, 95)     # the CLI launcher's default, UNCHANGED
HOSTED_CHILD_THRESHOLDS = (90, 95)                # David, 2026-08-11: 65 and 80 were not useful


def _harness_monitor_cls():
    """`HarnessMonitor`, imported from `compose_projects/intelligence/launcher/monitor.py`.

    A path insert rather than a package import because `compose_projects/intelligence/` is not a package on
    `sys.path`; the same shape `terminal.py` already uses for `singleton.py`.  `monitor.py`
    itself imports nothing from the launcher, so this pulls in no CLI lifecycle."""
    mon_dir = os.path.join(repo_root(), "compose_projects", "intelligence", "launcher")
    if mon_dir not in sys.path:
        sys.path.insert(0, mon_dir)
    from monitor import HarnessMonitor
    return HarnessMonitor


def default_state_file(intelligence_id: str) -> str:
    """The statusline tee (`~/.claude/composeio/self-<id>.json`) the context reader prefers.
    `SELF_STATE_FILE` overrides."""
    return os.environ.get(
        "SELF_STATE_FILE",
        os.path.join(os.path.expanduser("~"), ".claude", "composeio",
                     f"self-{intelligence_id}.json"))


def build_context_reader(state_file: Optional[str], jsonl_path):
    """A `read_context()` for the monitor, over the ONE shared source reader
    (`composeio.self_sensor.context_source.read_context`) so the monitor and the console gauge
    never fork the precedence.

    `jsonl_path` may be a STRING or a CALLABLE.  The callable form is what a widget-hosted
    COLD child needs: its transcript does not exist at fork time (the child mints its own
    session), so the path is resolved on the monitor thread, late, and re-resolved until it
    lands.  A read/import failure degrades to None (the monitor's tick already swallows a
    None reading), never crashes the host."""
    def read():
        py = os.path.join(repo_root(), "python")
        if py not in sys.path:
            sys.path.insert(0, py)
        try:
            from composeio.self_sensor.context_source import read_context
        except Exception:
            return None
        path = jsonl_path() if callable(jsonl_path) else jsonl_path
        try:
            return read_context(jsonl_path=path, state_file=state_file)
        except Exception:
            return None
    return read


def _envnum(name: str):
    v = os.environ.get(name)
    if v is None or not v.strip():
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_heartbeat_cadence(intelligence_id: str,
                           meta_path: Optional[str] = None) -> tuple:
    """The two-tier heartbeat cadence (base_s, backoff_after_s, then_s).  Priority: explicit
    env (SECONDS) > the persisted meta-object `lifecycle.heartbeat` (MINUTES — where the Config
    tab's edits land) > coded defaults (10/60/60 min).  Legacy `HEARTBEAT_IDLE_S` is honoured
    as a `base` override.  MOVED from `launcher.py` with the factory, unchanged."""
    base_min = backoff_min = then_min = None
    path = meta_path or default_meta_manifest(intelligence_id)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            hb = (json.load(fh).get("lifecycle") or {}).get("heartbeat") or {}
        if isinstance(hb, dict):
            for key in ("base", "backoff_after", "then"):
                v = hb.get(key)
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                    if key == "base":
                        base_min = float(v)
                    elif key == "backoff_after":
                        backoff_min = float(v)
                    else:
                        then_min = float(v)
    except (OSError, ValueError):
        pass
    base_s = base_min * 60.0 if base_min is not None else 600.0
    backoff_s = backoff_min * 60.0 if backoff_min is not None else 3600.0
    then_s = then_min * 60.0 if then_min is not None else 3600.0
    env_base = _envnum("HEARTBEAT_BASE_S")
    if env_base is None:
        env_base = _envnum("HEARTBEAT_IDLE_S")       # legacy alias
    if env_base is not None:
        base_s = env_base
    env_backoff = _envnum("HEARTBEAT_BACKOFF_AFTER_S")
    if env_backoff is not None:
        backoff_s = env_backoff
    env_then = _envnum("HEARTBEAT_THEN_S")
    if env_then is not None:
        then_s = env_then
    return base_s, backoff_s, then_s


def env_thresholds(default=DEFAULT_CONTEXT_THRESHOLDS) -> tuple:
    """`CONTEXT_THRESHOLDS` as a tuple, falling back to `default`.  An empty / unparseable
    value must NOT silently drop the 95 crossing — the one message that says what to DO."""
    raw = os.environ.get("CONTEXT_THRESHOLDS", "")
    try:
        parsed = tuple(int(x) for x in raw.split(",") if x.strip())
    except ValueError:
        parsed = ()
    return parsed or tuple(default)


def build_monitor(*, intelligence_id: str,
                  publish_in: Callable[..., None],
                  resume_session: str = "",
                  jsonl_path=None,
                  thresholds=None,
                  state_file: Optional[str] = None,
                  publish_cadence: Optional[Callable[[dict], None]] = None,
                  log: Optional[Callable[[str], None]] = None,
                  enabled: Optional[bool] = None):
    """THE `HarnessMonitor` construction site — the only one outside the selftests.

    Both hosts call this: `launcher.build_monitor(launcher, resume)` wraps it for the CLI,
    and `TerminalWidget._start_monitor` calls it for a hosted child with
    `thresholds=HOSTED_CHILD_THRESHOLDS` and a `jsonl_path` resolving the CHILD's transcript.
    Returns None when `HARNESS_MONITOR` is off (or `enabled=False`).

    `thresholds=None` means "the env, else the CLI default" — the hosted child passes (90, 95)
    EXPLICITLY, as a construction argument, so `HarnessMonitor`'s own declared default is
    untouched ([CXC-TERM-CHILD-TELEMETRY] default)."""
    say = log or (lambda _line: None)
    if enabled is None:
        v = os.environ.get("HARNESS_MONITOR")
        enabled = True if v is None else v.strip().lower() in ("1", "true", "yes", "on")
    if not enabled:
        return None
    HarnessMonitor = _harness_monitor_cls()
    if jsonl_path is None:
        jsonl_path = derive_jsonl_path(resume_session)
    base_s, backoff_s, then_s = load_heartbeat_cadence(intelligence_id)
    say(f"[ptycore] {iso_now()} monitor for {intelligence_id!r}: thresholds="
        f"{tuple(thresholds) if thresholds else env_thresholds()} heartbeat base="
        f"{base_s/60:.0f}m backoff-after={backoff_s/60:.0f}m then={then_s/60:.0f}m")
    return HarnessMonitor(
        read_context=build_context_reader(
            state_file if state_file is not None else default_state_file(intelligence_id),
            jsonl_path),
        publish_in=publish_in,
        thresholds=tuple(thresholds) if thresholds else env_thresholds(),
        heartbeat_base_s=base_s,
        heartbeat_backoff_after_s=backoff_s,
        heartbeat_then_s=then_s,
        cadence_provider=lambda: load_heartbeat_cadence(intelligence_id),
        publish_cadence=publish_cadence,
        echo_grace_s=float(os.environ.get("HEARTBEAT_ECHO_GRACE_S", "90")),
        poll_s=float(os.environ.get("CONTEXT_POLL_S", "20")),
        log=say,
    )


def resolve_child_jsonl(cwd: Optional[str], since_ts: float,
                        exclude: tuple = ()) -> str:
    """The transcript of a child we forked at `since_ts`, or "" while it cannot be told.

    A hosted COLD child MINTS its own session id ([CXC-TERM-CHILD-IDENTITY]) and we never learn
    it directly, so the monitor's `read_context` has to find the transcript late.  This is a
    BOUNDED discovery, not the `ls -t` newest-wins guess that leaked a transcript in 2026-07-24:
    it looks only in the CHILD's own project dir (keyed on the child's cwd — distinct cwd,
    distinct project key, [INSIGHT-CWD-IS-THE-SCOPE-LEVER]), only at files first written AFTER
    our fork, and it EXCLUDES any path we already know belongs to someone else (the host's pin).
    When nothing qualifies it returns "" — the monitor then reads no context and says so, which
    is the honest answer."""
    proj = os.environ.get("CLAUDE_PROJECTS_DIR")
    if not proj:
        base = os.path.join(os.path.expanduser("~"), ".claude", "projects")
        proj = os.path.join(base, project_dir_key(cwd or os.getcwd()))
    try:
        names = os.listdir(proj)
    except OSError:
        return ""
    best, best_mtime = "", 0.0
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        path = os.path.join(proj, name)
        if path in exclude:
            continue
        try:
            st = os.stat(path)
        except OSError:
            continue
        if st.st_mtime < since_ts:
            continue                      # predates our fork: not our child's
        if st.st_mtime > best_mtime:
            best, best_mtime = path, st.st_mtime
    return best
