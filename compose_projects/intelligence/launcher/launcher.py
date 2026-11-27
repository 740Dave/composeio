#!/usr/bin/env python3
"""ComposeIO Intelligence launcher — a THIN, ROBUST transparent PTY proxy + /compact relay
that makes an Intelligence's `/compact` self-directed ([SPEC:CXC-INTELLIGENCE-CONSOLE] · node
[CXC-IC-LIFECYCLE]).

Design (rebuilt after a live shakedown taught us the auto-driver was fragile):

  * TRANSPARENT PTY PROXY. The launcher spawns `claude` under a PTY and then gets OUT OF THE
    WAY: it forwards the human's terminal <-> the child byte-for-byte. The human sees the real
    Claude Code UI — including every startup dialog — and answers those dialogs THEMSELVES,
    exactly as if they had run `claude` directly. The launcher does NOT watch for dialogs, does
    NOT inject menu keystrokes, and does NOT type a move-note. (The old auto-answer-by-regex +
    typed-move-note approach collided with the human on the glass and hung.)

  * /compact RELAY — the ONE special thing. The launcher owns the child's PTY, so it is the
    natural actuator for the input-plane `/compact` command (a channel event can't press it).
    It subscribes to `compose_projects/intelligence/<id>/out`; WHEN THE INSTANCE ITSELF emits a well-formed
    compact directive out its channel, the launcher types `/compact <direction>` into the
    child. It DECIDES NOTHING — when / whether / what-direction are all the Intelligence's.
    Every injected keystroke is LOGGED (auditable, no silent action). This is the only
    launcher-originated write to the child.

That is the whole job: be an invisible wire in both directions, and type `/compact <direction>`
on the Intelligence's own instruction. Nothing else.

Lifecycle modes ([CXC-IC-LIFECYCLE]):
  RESUME / MOVE (RESUME_SESSION set): spawn `claude --resume <id>` to RE-HOST an already-running
     session INTO the console with its context intact. This crosses NO seam, so NOTHING is
     injected at startup — the instance already holds its context. The human answers whatever
     startup dialogs appear; the /compact relay runs as normal.
  COLD START (default): spawn a FRESH `claude`, WITH the re-register bootstrap — the DEFAULT
     since 2026-08-11 ([CXC-ICL-COLD], ratified). David, 2026-08-10:
     "The re-register bootstrap is not experimental any longer, it works quite well — it
     should be the default for new instances."
     A cold successor holds no context, so `build_bootstrap_prompt` builds an orientation
     prompt from the meta-object manifest — pointing at the role_descriptor / soul /
     dusk_gifts BY PATH, never inlining them. It is written to a FILE and the child's
     POSITIONAL PROMPT ARGUMENT names that file ([CXC-TERM-BOOTSTRAP-ARGV], ratified
     2026-08-11). NOTHING IS EVER TYPED AT STARTUP — the settle-delay injection
     (`BOOTSTRAP_DELAY`, `arm_bootstrap`) is DELETED, so [CXC-TERM-DIALOGS] holds by
     construction rather than by timing. `BOOTSTRAP=0` is the explicit opt-OUT.
     The manifest is resolved through the CHAIN ([CXC-TERM-MANIFEST-CHAIN]) — META_MANIFEST,
     then `<id>.meta.json`, then `<role>.meta.json` — and an EXHAUSTED chain REFUSES the
     launch with a non-zero exit naming every path tried, rather than booting an instance
     that looks oriented and is not.

PTY mechanism: stdlib `pty.fork()` + `select` on [own-stdin, pty-master] — and as of 2026-08-11 it
lives in ONE place, `composeio.ptycore` ([SPEC:CXC-TERM] · [CXC-TERM-PTYCORE]), which this launcher
and the ComposeUX terminal widget both drive. This module keeps the PROXY half (raw mode, the
blocking loop, signal handling, `run()`); the core owns spawn/pump/write/inject/teardown. The
extraction is a MOVE: behaviour is unchanged and `--selftest` is its gate. The only third-party
runtime dependency is still `paho-mqtt` (the core is stdlib-only); importing it does pull in the
`composeio` package __init__, which is same-repo and dependency-light. No pexpect.

Config (env or a JSON --config file):
  INTELLIGENCE_ID   default "architect"          the bus id (topics + prompt)
  COMPOSEBUS_MQTT   default "mqtt://mqtt-broker.local:1883"
  CLAUDE_BIN        unset → $CLAUDE_CODE_EXECPATH → "claude"    the binary to spawn. The
                                                 EXECPATH leg matters: `claude` is a shell
                                                 ALIAS on some machines and NOT on PATH, so a
                                                 bare-name exec dies with a misleading
                                                 "No such file" (ptycore.resolve_claude_bin)
  META_MANIFEST     default = the CHAIN: <id>.meta.json, then <role>.meta.json, then REFUSE
                                                 ([CXC-TERM-MANIFEST-CHAIN])
  INTELLIGENCE_ROLE default = INTELLIGENCE_ID    the ROLE whose manifest is chain leg 3
  RESUME_SESSION    default ""                   a Claude Code session-id; when non-empty,
                                                 run in RESUME/MOVE mode (see above)
  BOOTSTRAP         default ON                   deliver the cold-start re-register bootstrap
                                                 on ARGV (cold mode only). "0" is the explicit
                                                 opt-OUT and the ONE way to get an instance
                                                 that is deliberately unoriented
  BOOTSTRAP_PROMPT_DIR  default $TMPDIR/composeio-bootstrap   where the generated prompt file
                                                 is written; argv names it by absolute path
  SKIP_PERMISSIONS  default ON  (David 2026-07-21)  "0" → OMIT --dangerously-skip-permissions from the
                                                 child argv (may reduce, but not eliminate, the
                                                 startup dialogs)
  PTY_ROWS/PTY_COLS default = the launcher's own terminal, else 24x80. Pins the child's terminal
                                                 geometry (TIOCSWINSZ). Clamped to 10x40.
                                                 See [CXC-TERM-WINSIZE].

Run:   python3 launcher.py            # proxy + relay a real `claude`
       python3 launcher.py --selftest # headless: dummy child + mock MQTT, no broker
"""
from __future__ import annotations

import json
import os
import select
import signal
import sys
import threading
import time
from datetime import datetime, timezone

# ── The SHARED PTY core, bound LAZILY ([CXC-TERM-PTYCORE] + [CXC-SOB-EXEMPT]) ────────────────
# `composeio.ptycore` itself is stdlib-only, but importing it executes the `composeio` package
# __init__, which reaches the bus and the transports. This launcher's paho exemption protects its
# STARTUP FOOTPRINT — the import graph reachable at MODULE LOAD — so the core is bound on FIRST
# USE, exactly like the two composeio imports this module already had (`bootstrap_paths`,
# `self_sensor.context_source`). Module load stays composeio-free; a Launcher() (or an
# `from launcher import project_dir_key`) binds the core.
_PY_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))), "python")
_CORE = None
# Names the core owns and this module re-exports. `_write_all` / `_derive_jsonl_path` keep their
# historical private spellings because callers (the census tool, the selftest) already use them.
_CORE_NAMES = (
    "CHANNEL_FLAGS", "COMPACT_CLEAR", "COMPACT_CMD", "COMPACT_PHASE_DELAY_S", "COMPACT_SUBMIT",
    "DEFAULT_PTY_COLS", "DEFAULT_PTY_ROWS", "INJECT_CMD", "MIN_PTY_COLS", "MIN_PTY_ROWS",
    "PtyChild", "build_claude_args", "derive_jsonl_path", "export_session_pin", "get_winsize",
    "pack_winsize", "parse_actuation", "project_dir_key", "resolve_winsize", "set_winsize",
    "write_all",
    # THE ONE PROJECT-DIR SEAM ([CXC-FC-DEFORK]).  Re-exported here so `IntelligenceHost` —
    # which reaches the core through THIS module — derives a child's cwd from the same
    # function the Terminal Widget's spawn path does.  Two implementations of one invariant
    # is what produced the 2026-08-12 shared-memory defect ([CXC-FC-CWD-FORK]).
    "RoleRefused", "project_dir_for", "role_memory_dir",
    # The child-env sanitisation ([CXC-IC-SELF-IDENTITY]) and the binary chain — shared
    # because BOTH hosts fork the same child and must hand it the same identity-free env.
    "HOST_IDENTITY_ENV", "KEPT_HOST_ENV", "resolve_claude_bin", "sanitize_child_env",
    # The COLD-START bootstrap ([CXC-ICL-COLD]) — moved out of this file so the terminal
    # widget inherits the behaviour rather than re-implementing it ([CXC-TERM-DIALOGS]).
    # There is NO `BOOTSTRAP_DELAY_S` / `bootstrap_delay_s` any more: the prompt rides argv
    # and the settle mechanism is DELETED ([CXC-TERM-BOOTSTRAP-ARGV]).
    "BOOTSTRAP_ARGV_MAX", "BootstrapRefused", "bootstrap_enabled", "build_bootstrap_argv",
    "build_bootstrap_for", "build_bootstrap_prompt", "default_meta_manifest",
    "host_intelligence_id", "load_manifest", "resolve_meta_manifest",
    # The harness-monitor factory — ONE construction site for BOTH hosts
    # ([CXC-TERM-CHILD-TELEMETRY]).  `build_monitor` below is a thin launcher-shaped wrapper.
    "DEFAULT_CONTEXT_THRESHOLDS", "HOSTED_CHILD_THRESHOLDS", "build_context_reader",
    "env_thresholds", "load_heartbeat_cadence", "resolve_child_jsonl",
    # The context QUERY, shared for the same reason the actuation parser is.
    "CONTEXT_CMD", "CONTEXT_CMD_ALIASES", "parse_context_query",
)
_CORE_ALIASES = {"_write_all": "write_all", "_derive_jsonl_path": "derive_jsonl_path"}


def _load_core():
    """Import the shared PTY core and BIND its names into this module's globals (once).

    Binding into globals rather than returning a module keeps every call site below reading
    exactly as it did before the extraction — and PEP-562 `__getattr__` covers an outside
    importer that reaches for a core name before any Launcher exists."""
    global _CORE
    if _CORE is None:
        if _PY_ROOT not in sys.path:
            sys.path.insert(0, _PY_ROOT)
        import composeio.ptycore as _ptycore     # lazy on purpose — see the note above
        _CORE = _ptycore
        g = globals()
        for _name in _CORE_NAMES:
            g[_name] = getattr(_ptycore, _name)
        for _alias, _target in _CORE_ALIASES.items():
            g[_alias] = getattr(_ptycore, _target)
    return _CORE


def __getattr__(name):          # PEP 562 — module-level attribute hook
    """`from launcher import project_dir_key` (the census tool) still works, and still does
    not cost a composeio import at module load."""
    if name in _CORE_NAMES or name in _CORE_ALIASES:
        _load_core()
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# [CXC-SCL-SCRUB-OVERLAY-PARITY] Apply the host overlay BEFORE the module-level env reads below.
# This entry point keeps its load free of the composeio package, so it runs the stdlib-only loader by path
# (no package import); it fills only unset names and never raises.
def _apply_host_overlay() -> None:
    import importlib.util
    try:
        _p = os.path.join(os.path.dirname(_PY_ROOT), "python", "composeio", "_overlay.py")
        _spec = importlib.util.spec_from_file_location("_composeio_overlay_entry", _p)
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.apply()
    except Exception:  # noqa: BLE001 — no overlay problem may stop this entry point
        pass


_apply_host_overlay()


# ── Config defaults ─────────────────────────────────────────
DEFAULT_ID = os.environ.get("INTELLIGENCE_ID", "architect")
DEFAULT_MQTT = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")
# EMPTY, not "claude": the fallback chain lives in `ptycore.resolve_claude_bin`
# (explicit -> $CLAUDE_BIN -> $CLAUDE_CODE_EXECPATH -> "claude") and is applied in
# Launcher.__init__, AFTER the core is bound.  Baking "claude" in here would consume the
# fallback at module-import time and the EXECPATH leg could never run.  Module load must
# stay composeio-free ([CXC-SOB-EXEMPT]), which is why it is not resolved here.
DEFAULT_CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "")
# A Claude Code session-id. When non-empty the launcher runs in RESUME/MOVE mode (re-host an
# already-running session); when empty it runs in the default COLD-START mode. [CXC-IC-LIFECYCLE]
DEFAULT_RESUME = os.environ.get("RESUME_SESSION", "")

# CHANNEL_FLAGS + build_claude_args moved to composeio.ptycore ([CXC-TERM-PTYCORE]) — the widget
# spawns the same child with the same argv, so the argv builder cannot live in a CLI program.


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# `_envflag` (default-FALSE flag reader) was DELETED with its last caller: it gated
# BOOTSTRAP, which now defaults ON via `ptycore.bootstrap_enabled` ([CXC-ICL-COLD]). The
# remaining default-TRUE reader is `_envflag_default`, below. Leaving a default-FALSE helper
# lying around next to a default-ON flag is how the flip gets silently undone.


# ── Actuation-directive schema ──────────────────────────────
# The Intelligence emits a directive out `compose_projects/intelligence/<id>/out`. The channel's `reply` tool
# publishes `{from, text, ts, to?, thread?}`, so the directive rides in `text` as a JSON object;
# a peer may also publish it at the TOP LEVEL of the out-message. The launcher owns the child's
# PTY, so it is the actuator that ENTERS text into the running session as if the human typed it.
# Two commands ride this seam:
#
#     {"cmd": "compact", "direction": "<the full custom /compact direction text>"}
#         -> the leaf types + submits  `/compact <direction>`   (the lifecycle self-fire)
#     {"cmd": "inject",  "text":      "<an arbitrary message>"}
#         -> the leaf types + submits  the message VERBATIM      (a general 'enter this on my
#            behalf, as if the human typed it' actuation — the primitive the ComposeApp
#            collaboration model builds on; /compact is just its structured special case)
#
# Anything else on `.../out` (an ordinary reply with no `cmd`) is IGNORED — no injection.
# COMPACT_CMD / INJECT_CMD / parse_actuation moved to composeio.ptycore ([CXC-TERM-PTYCORE]):
# the widget must actuate IDENTICALLY, so the directive schema is shared, not re-parsed.
# A NON-actuating request: the Intelligence asks the harness for its current context utilization.
# It does NOT press a key on the child — the harness answers by publishing a report to
# `compose_projects/intelligence/<id>/in` (a <channel from="harness"> event), so it rides the bus, not the PTY.
# CONTEXT_CMD / CONTEXT_CMD_ALIASES / parse_context_query MOVED to composeio.ptycore
# ([CXC-TERM-CHILD-TELEMETRY]): the terminal widget answers the same query for its hosted
# child, and a second parser would be a second answer to "is this a query or a keystroke".

# ── /compact injection mechanics (the leaf owns the actuation — Problem 1) ──────────────────
# The launcher owns the child's PTY, so it owns the FULL input-plane mechanism of firing
# `/compact`, not just the text. Two live-verified facts (2026-07-19) shaped this:
#
#   * SUBMIT is a CARRIAGE RETURN (\r, 0x0D — what the physical Return key emits in raw mode),
#     NOT a line feed (\n, 0x0A). Claude Code's TUI inserts \n as a newline; it submits on \r.
#     The old bulk write `/compact <direction>\n` left the command sitting in the prompt, fully
#     typed, HUNG waiting for the human to press Return. \r fixes it — and it is sent as a
#     SEPARATE write AFTER the command so a paste-buffering TUI finishes ingesting the command
#     before the submit arrives (this is what makes an ANY-LENGTH direction self-fire).
#   * The prompt must be CLEARED first: there was pre-existing text in the entry area when the
#     leaf injected, and it collided with the command (the human had to ESC-ESC it clear by hand).
#     Two ESCs dismiss any half-typed text / stuck paste state — David verified ESC-ESC clears
#     the prompt in this build; that clearing is the leaf's job, not the human's.
# COMPACT_CLEAR / COMPACT_SUBMIT / COMPACT_PHASE_DELAY_S and the three-phase inject_line moved to
# composeio.ptycore ([CXC-TERM-PTYCORE]) — the widget needs the IDENTICAL actuation.


# ── Bootstrap-prompt construction — MOVED to composeio.ptycore ([CXC-TERM-PTYCORE]) ───────
# `build_bootstrap_prompt` / `load_manifest` / `default_meta_manifest` / `bootstrap_enabled` /
# `resolve_meta_manifest` / `build_bootstrap_for` / `build_bootstrap_argv` now live in the
# shared core and are re-exported here under their historical names (see _CORE_NAMES).
#
# WHY they moved: [CXC-ICL-COLD] made the cold-start re-register bootstrap the DEFAULT, and
# [CXC-TERM-DIALOGS] says the terminal widget "inherits that behaviour unchanged". A widget
# cannot import this module ([CXC-TERM-PTYCORE] known-hazard: `Launcher.run()` is a `main()`,
# not a component), so a shared behaviour that stayed here would have been re-implemented —
# and a second copy of the ORIENTATION PROMPT is the worst possible thing to fork.
#
# The carry-depth-by-reference discipline moved with it, intact: the prompt POINTS at
# role_descriptor / soul / dusk_gifts BY PATH and never inlines their contents.


# ── Context perception — the SHARED source reader ([CXC-IC-MONITOR], the de-fork) ─────────────
# The launcher itself cannot see Claude Code's authoritative `used_percentage` (that is piped to
# the STATUSLINE command, not to us). Perception is a MULTI-source read, and it is the SAME reader
# the console publisher uses — `composeio.self_sensor.context_source.read_context` — so the gauge
# and the 65/80/90/95 crossings can never perceive different levels. Precedence (freshest
# AUTHORITATIVE source wins, by timestamp):
#   1. hud-cache — haven-hud's per-session snapshot of the native `used_percentage`, persisted
#      every ~300ms INDEPENDENT of the CLI redraw. Typically the freshest AND the fix for the
#      2026-07-26 staleness defect: the monitor no longer depends on the redraw-gated state file.
#   2. statusline-file — the `self_sensor --emit-state` state file (authoritative but FREEZES
#      between CLI redraws — the root of the stale reading).
#   3. jsonl-estimate — over-reading estimate; only when NO authoritative source exists.
# Was a launcher-LOCAL two-source reader (state-file then jsonl) that could NOT see the hud-cache,
# so the monitor read a frozen figure while the console read the fresh one — a fork-divergence.
# The reader itself now lives in the core (`build_context_reader`), re-exported here.

# ── The monitor factory — MOVED to composeio.ptycore ([CXC-TERM-CHILD-TELEMETRY]) ────────────
# `build_context_reader`, `default_state_file`, `load_heartbeat_cadence` and the ONE
# `HarnessMonitor(...)` construction site now live in the shared core and are re-exported here
# under their historical names (see _CORE_NAMES).  WHY they moved: the terminal widget hosts a
# child that needs the SAME monitor, and it structurally cannot import this module
# (`Launcher.run()` installs signal handlers and takes a superseding flock — a `main()`, not a
# component, [CXC-TERM-PTYCORE] known-hazard).  A widget-local copy of the wiring would fork the
# hysteresis, the arming state machine, the echo grace and the heartbeat cadence, and the fork
# would drift on the first change to either.  The function below is a thin LAUNCHER-SHAPED
# wrapper over the core factory so this module's own call site and selftest are unchanged.


def _repo_root() -> str:
    # This file is compose_projects/intelligence/<sub>/<me>.py, so the repo root is FOUR
    # dirnames up. Three stops at compose_projects/ -- see the doubled-metrics.db defect.
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def _default_meta_path(intelligence_id: str) -> str:
    """The durable config store the Config tab writes through (config_persister.py):
    compose_projects/intelligence/meta/<id>.meta.json. META_MANIFEST overrides; else the repo default.

    Delegates to the core so the HEARTBEAT-CADENCE reader and the BOOTSTRAP prompt builder
    resolve the SAME manifest — a second spelling of this path is how a Config-tab edit lands
    in one consumer and not the other."""
    _load_core()
    return default_meta_manifest(intelligence_id)


def build_monitor(launcher: "Launcher", resume_session: str = ""):
    """Construct a HarnessMonitor for `launcher`, or None if disabled.  A WRAPPER over
    `ptycore.build_monitor` — it supplies the launcher's id, its `publish_in`, its
    publish-current cadence hook and its log, and nothing else.  Env knobs are read by the
    core (HARNESS_MONITOR, CONTEXT_POLL_S, CONTEXT_THRESHOLDS, HEARTBEAT_*, SELF_STATE_FILE,
    SESSION_JSONL).  The CLI launcher's own threshold default is UNCHANGED (65,80,90,95); the
    hosted child passes (90,95) explicitly ([CXC-TERM-CHILD-TELEMETRY])."""
    _load_core()
    from composeio.ptycore import build_monitor as _core_build_monitor
    return _core_build_monitor(
        intelligence_id=launcher.id,
        publish_in=launcher.publish_in,
        resume_session=resume_session,
        publish_cadence=lambda d: launcher.publish_state(
            f"intelligence/{launcher.id}/config/heartbeat/current", d),
        log=launcher._log,
    )


def _envflag_default(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


# ── PTY window size, winsize resolution and the low-level write helper ─────────────────────
# All of it moved to composeio.ptycore ([CXC-TERM-PTYCORE] / [CXC-TERM-WINSIZE]): the terminal
# widget's arranged rect IS a terminal geometry, so one implementation serves both hosts.
# pack_winsize / set_winsize / get_winsize / resolve_winsize / write_all are imported at the top
# of this module and re-exported under their old names for existing callers.


# ── The launcher ────────────────────────────────────────────
class Launcher:
    def __init__(
        self,
        intelligence_id: str = DEFAULT_ID,
        mqtt_url: str = DEFAULT_MQTT,
        claude_bin: str = DEFAULT_CLAUDE_BIN,
        claude_args: list[str] | None = None,
        bootstrap_prompt: str = "",
        resume_session: str = "",
        mqtt_client=None,
        log=None,
        monitor=None,
        term_size: tuple[int, int] | None = None,
    ):
        _load_core()      # bind the shared PTY core's names ([CXC-TERM-PTYCORE])
        self.id = intelligence_id
        self.mqtt_url = mqtt_url
        # explicit -> $CLAUDE_BIN -> $CLAUDE_CODE_EXECPATH -> "claude" ([CXC-TERM-WIDGET]).
        self.claude_bin = resolve_claude_bin(claude_bin)
        self.resume_session = resume_session
        # Default args = dev-channel flag (COLD) or `--resume <id>` + dev-channel flag
        # (RESUME/MOVE); a caller (or the selftest) may override entirely. [CXC-IC-LIFECYCLE]
        self.claude_args = (
            list(claude_args) if claude_args is not None
            else build_claude_args(
                resume_session,
                skip_permissions=_envflag_default("SKIP_PERMISSIONS", True))
        )
        # The COLD-START re-register prompt ([CXC-ICL-COLD], default ON). Empty in resume mode
        # (a re-host already holds its context) and whenever BOOTSTRAP=0. When set it is the
        # child's POSITIONAL PROMPT ARGUMENT — appended to argv HERE, at construction, so it
        # is exec'd rather than typed ([CXC-TERM-BOOTSTRAP-ARGV]). `main()` fills it via
        # `build_bootstrap_argv`, which owns that whole decision for BOTH hosts.
        #
        # WHY IT IS APPENDED AND NOT TYPED: a fixed settle delay is a state check that cannot
        # fail loudly — when it is wrong it does not error, it types into whatever happens to
        # be reading, and on the first live firing that was a blocking `Enter y/n:` gate.
        self.bootstrap_prompt = bootstrap_prompt
        if bootstrap_prompt:
            self.claude_args = list(self.claude_args) + [bootstrap_prompt]
        self.out_topic = f"intelligence/{self.id}/out"
        # The harness answers the Intelligence (context alerts / heartbeat / query reports) by
        # PUBLISHING to its inbound topic, so the message surfaces as a <channel from="harness">
        # event — the same path David's messages ride, NON-interrupting (unlike the PTY inject).
        self.in_topic = f"intelligence/{self.id}/in"
        # OPTIONAL baseline monitor (HarnessMonitor). None disables the whole capability; run()
        # builds a real one unless HARNESS_MONITOR is off. Its publish_in is wired to publish_in().
        self.monitor = monitor

        self._mqtt = mqtt_client  # injectable; real paho created lazily in connect_mqtt()
        self._log = log or (lambda line: sys.stderr.write(line + "\n"))

        # THE PTY HALF, by reference ([CXC-TERM-PTYCORE]). Spawn, the select pump, the single
        # locked write path, the three-phase inject, the winsize plumbing and teardown all live
        # in the shared core; this class keeps the PROXY half around it. Every attribute the old
        # in-class implementation exposed (pid / master_fd / injection_log / term_rows /
        # term_cols / _stdin_fd / _write_lock / compact_phase_delay) is a property onto the core,
        # so callers and the selftest are unchanged.
        self.child = PtyChild([self.claude_bin] + self.claude_args,
                              term_size=term_size, log=self._log)
        self._stop = False

    # ── The core, surfaced under the launcher's historical attribute names ──
    @property
    def pid(self) -> int | None:
        return self.child.pid

    @property
    def master_fd(self) -> int | None:
        return self.child.master_fd

    @property
    def injection_log(self) -> list:
        return self.child.injection_log

    @property
    def term_rows(self) -> int | None:
        return self.child.term_rows

    @property
    def term_cols(self) -> int | None:
        return self.child.term_cols

    @property
    def compact_phase_delay(self) -> float:
        return self.child.compact_phase_delay

    @compact_phase_delay.setter
    def compact_phase_delay(self, value: float) -> None:
        self.child.compact_phase_delay = value

    @property
    def _stdin_fd(self) -> int | None:
        """The human's stdin, forwarded to the child while the proxy loop runs. It is the core's
        OPTIONAL secondary input fd — a windowed host leaves it None and writes keystrokes."""
        return self.child.input_fd

    @_stdin_fd.setter
    def _stdin_fd(self, fd: int | None) -> None:
        self.child.input_fd = fd

    @property
    def _write_lock(self):
        return self.child._write_lock

    # ── The launcher-side wrappers over the shared core ([CXC-TERM-PTYCORE]) ──
    def _export_session_pin(self) -> None:
        """PASS the session transcript identity to the child, never let it GUESS
        ([CXC-IC-SELF-IDENTITY]).  Delegates to the core so the widget pins identically."""
        export_session_pin(self.resume_session, self._log)

    def current_winsize(self) -> tuple[int, int]:
        """The (rows, cols) the child SHOULD have right now — re-resolved each call so a SIGWINCH
        picks up the human's new terminal size.  The core resolves explicit > env > our own tty."""
        return self.child.current_winsize()

    def apply_winsize(self, rows: int | None = None, cols: int | None = None):
        """Set the child's terminal geometry on the PTY MASTER + SIGWINCH the child
        ([CXC-TERM-WINSIZE]).  Returns the applied (rows, cols), or None."""
        return self.child.apply_winsize(rows, cols)

    def _on_sigwinch(self, signum=None, frame=None) -> None:  # noqa: ANN001
        """The human resized the launcher's terminal → re-resolve and forward to the child. Wrapped
        whole: a resize must never be able to take the proxy down."""
        try:
            self.apply_winsize()
        except Exception:
            pass

    # ── Spawn under a PTY ───────────────────────────────────
    def start_child(self) -> None:
        """Spawn `claude` under a PTY via the shared core — the tree's ONE `pty.fork()`."""
        self.child.argv = [self.claude_bin] + self.claude_args
        # RE-SUPPLY the session pin explicitly ([CXC-IC-SELF-IDENTITY]). The core strips every
        # host-identity variable from the child env — INCLUDING the transcript pin, because an
        # INHERITED pin names the HOST's transcript. In RESUME/MOVE mode the pin
        # `_export_session_pin` derived names the session we are RE-HOSTING, i.e. the child's
        # own, so it is PASSED back in through env_extra. In COLD mode nothing is passed and
        # the fresh instance resolves by its own rules — which is the whole point.
        pin = os.environ.get("COMPOSEIO_SESSION_JSONL", "").strip()
        if pin and self.resume_session:
            extra = dict(self.child.env_extra or {})
            extra["COMPOSEIO_SESSION_JSONL"] = pin
            self.child.env_extra = extra
        self.child.start()

    # ── Inject keystrokes into the child (the ONLY launcher-originated write; always logged) ──
    def inject(self, keys: str, kind: str = "keystroke") -> None:
        self.child.inject(keys, kind=kind)

    # ── Type + SUBMIT a line on the instance's behalf (the general actuation) ──
    def inject_line(self, line: str, kind: str | None = None) -> None:
        """Enter `line` into the running session AS IF THE HUMAN TYPED IT, in THREE phases
        (ESC-ESC clear → the line verbatim → a \r submit).  The mechanism lives in the core so
        the terminal widget actuates with the IDENTICAL bytes ([CXC-TERM-CONVERGE]).

        `kind=None` means the core's own default ("inject") — the constant is NOT re-spelled
        here, because a second spelling of a shared constant is the fork this whole extraction
        exists to prevent."""
        if kind is None:
            self.child.inject_line(line)
        else:
            self.child.inject_line(line, kind=kind)

    # ── Publish a harness message to the Intelligence's inbound topic ──
    def publish_in(self, text: str, kind: str = "harness", extra: dict | None = None) -> None:
        """Publish a harness-originated message to `compose_projects/intelligence/<id>/in`. The channel maps
        `text` -> the <channel> body and every OTHER key -> a <channel> attribute, so `from` and
        `kind` (+ any `extra`) become attributes the Intelligence can read. This is how the
        monitor delivers context alerts / heartbeat / query answers WITHOUT pressing a key on the
        child — it rides the bus (non-interrupting), not the PTY (ESC-ESC, reserved for /compact)."""
        if self._mqtt is None:
            self._log(f"[launcher] {_iso_now()} publish_in DROPPED (no bus): {kind}: {text[:60]!r}")
            return
        msg = {"from": "harness", "kind": kind, "text": str(text), "ts": int(time.time() * 1000)}
        if extra:
            for k, v in extra.items():
                if k not in msg:  # never let extra clobber from/kind/text/ts
                    msg[k] = v
        self._log(f"[launcher] {_iso_now()} PUBLISH in {kind}: {text[:70]!r}")
        self._mqtt.publish(self.in_topic, json.dumps(msg), qos=1)

    # ── Publish a STATE namespace onto ComposeBus (a co-equal bus peer) ──
    def publish_state(self, namespace: str, value, scope: str = "global") -> None:
        """Publish a STATE message to a ComposeBus namespace the console binds — the harness
        acting as a co-equal bus PEER, not just a channel producer ([CXC-IC-HEARTBEAT-TWO-SOURCES],
        David 2026-07-26). Frames the composeio BusMessage wire envelope by hand (the launcher
        keeps its paho-only, composeio-import-free footprint) and publishes to
        `composeio/{scope}/{namespace}` — the same envelope the console's transport decodes
        (it reads `val`). Used for publish-current of the heartbeat cadence (the set-vs-current
        delta). Retained so a late-joining console immediately shows the effective cadence."""
        if self._mqtt is None:
            self._log(f"[launcher] {_iso_now()} publish_state DROPPED (no bus): {namespace}={value!r}")
            return
        envelope = {"ns": namespace, "type": "state", "src": f"harness:{self.id}",
                    "ts": time.time(), "scope": scope, "seq": 0, "val": value}
        topic = f"composeio/{scope}/{namespace}"
        self._mqtt.publish(topic, json.dumps(envelope), qos=1, retain=True)
        self._log(f"[launcher] {_iso_now()} PUBLISH state {namespace}={value!r}")

    # ── Handle an out-message from the bus ──────────────────
    def on_out_message(self, raw: str) -> None:
        """Parse an `compose_projects/intelligence/<id>/out` message. If it is a well-formed actuation directive
        ({"cmd":"compact","direction":...} or {"cmd":"inject","text":...}), type + SUBMIT the
        corresponding line into the child via the three-phase `inject_line` (clear → type →
        \\r-submit); otherwise ignore (ordinary replies are not commands). This is the ONLY
        thing that turns a bus message into a keystroke."""
        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._log(f"[launcher] {_iso_now()} SKIP out (non-JSON): {raw[:120]!r}")
            return
        # A context query presses NO key — the harness answers on the `in` topic. Handle it before
        # actuation so {"cmd":"context"} is never mistaken for a keystroke.
        if parse_context_query(payload):
            self._log(f"[launcher] {_iso_now()} CONTEXT QUERY")
            if self.monitor is not None:
                self.monitor.handle_query()
            else:
                self.publish_in("harness: the context monitor is not enabled this session.",
                                kind="context-report", extra={"source": "none"})
            return
        actuation = parse_actuation(payload)
        if actuation is None:
            self._log(f"[launcher] {_iso_now()} SKIP out (not an actuation directive)")
            return
        kind, line = actuation
        self._log(f"[launcher] {_iso_now()} ACTUATE {kind}: {line[:80]!r}")
        self.inject_line(line, kind=kind)

    # ── MQTT (paho, matching transport_mqtt.py idiom) ───────
    def _paho_on_message(self, client, userdata, msg) -> None:
        try:
            raw = msg.payload.decode()
        except Exception:
            raw = str(msg.payload)
        self.on_out_message(raw)

    def connect_mqtt(self):
        if self._mqtt is not None:
            # Injected client (selftest). Register the handler and subscribe.
            self._mqtt.on_message = self._paho_on_message
            self._mqtt.subscribe(self.out_topic, qos=1)
            return self._mqtt
        import paho.mqtt.client as mqtt

        host, port = _parse_mqtt_url(self.mqtt_url)
        # Stable (non-PID) client-id: broker-level backstop to the SUPERSEDE host
        # lock ([CXC-IC-SINGLETON]). A superseded predecessor is reaped at run()
        # start; if one lingers a beat, the shared client-id also makes the broker
        # drop it. Unique per <id> so distinct intelligences never collide.
        # MQTT 5, EXPLICITLY — see [DEC-IMPLEMENT-MQTT-RETAIN-WITH-AN-AGE-CONTRACT].
        # paho's default is MQTTv311 and an omitted `protocol=` is invisible at
        # the call site.
        #
        # Built at the paho seam so paho 2.x constructs too (it requires a
        # CallbackAPIVersion). Lazy, like every composeio import here.
        if _PY_ROOT not in sys.path:
            sys.path.insert(0, _PY_ROOT)
        from composeio.paho_compat import new_paho_client
        client = new_paho_client(mqtt=mqtt,
                                 client_id=f"intelligence-launcher-{self.id}",
                                 protocol=mqtt.MQTTv5)
        client.on_message = self._paho_on_message

        # `properties` DEFAULTS so BOTH arities work: MQTT 5 passes a 5th
        # argument, 3.1.1 does not. Without it this raises a TypeError inside
        # paho's network thread, where it is SWALLOWED — the launcher would
        # never mark itself connected and the channel would be silently dead.
        def _on_connect(c, u, flags, rc, properties=None):  # noqa: ANN001
            if rc == 0:
                c.subscribe(self.out_topic, qos=1)
                self._log(f"[launcher] {_iso_now()} bus connected {self.mqtt_url}; sub {self.out_topic}")
            else:
                self._log(f"[launcher] {_iso_now()} bus connect failed rc={rc}")

        client.on_connect = _on_connect
        client.connect(host, port, keepalive=60)
        client.loop_start()
        self._mqtt = client
        return client

    # ── Transparent proxy core ──────────────────────────────
    def _sink(self, out_fd: int):
        """The byte SINK this launcher hands the core's pump ([CXC-TERM-PTYCORE]): mirror the
        child's output to the human's terminal, byte-for-byte, and note the activity.

        This is the ONE sanctioned behavioural change of the extraction — the core pumps into a
        callable instead of an fd, so the terminal widget can enqueue for its emulator instead of
        inventing a pipe. What the launcher does with the bytes is IDENTICAL to before."""
        def sink(data: bytes) -> None:
            # Child output == the Intelligence generating: mark activity so the heartbeat
            # tracks quiescence from the last time it actually produced something.
            if self.monitor is not None:
                self.monitor.note_activity()
            write_all(out_fd, data)
        return sink

    def _pump(self, out_fd: int, timeout: float = 0.2) -> bool:
        """ONE select cycle of the transparent proxy (the core's), forwarding byte-for-byte:
             child PTY master  -> out_fd   (the human sees the real claude UI, unmodified)
             self._stdin_fd    -> master   (the human's keystrokes reach claude, so THEY answer
                                            dialogs; the launcher itself never writes here)
        Returns False when the child's PTY has EOF'd (session over), True otherwise."""
        return self.child.pump(self._sink(out_fd), timeout=timeout)

    def proxy_loop(self, in_fd: int, out_fd: int, timeout: float = 0.2) -> None:
        """Run the transparent proxy until the child exits / EOFs or we are asked to stop.
        `in_fd` is the human's stdin (forwarded to the child); `out_fd` is where the child's
        output is mirrored (the human's stdout)."""
        self._stdin_fd = in_fd
        while not self._stop:
            if not self._pump(out_fd, timeout=timeout):
                break
            if self._child_exited():
                self._drain_final(out_fd)
                break

    def _drain_final(self, out_fd: int) -> None:
        """Flush any output the child left in the PTY buffer after it exited."""
        self.child.drain_final(self._sink(out_fd))

    # ── Full run (production) ───────────────────────────────
    def run(self) -> int:
        # SUPERSEDE singleton ([SPEC:CXC-IC-SINGLETON] launcher case, D5 2026-07-23):
        # become THE launcher for this <id> BEFORE spawning a rival `claude`. Two
        # launchers would both relay `.../out` and DOUBLE-inject every actuation.
        # A plain yield-if-held flock would make a RESUME start exit and SILENTLY
        # fail the restart, so we SUPERSEDE: reap any predecessor (this fresh
        # re-host is meant to replace it), then hold the lock for our lifetime
        # (flock releases on death, so a crash never blocks a restart). Best-effort:
        # if a predecessor genuinely can't be reaped we still launch — a restart
        # must not be blocked — but that is the rare not-ours-to-signal case.
        try:
            sys.path.insert(0, os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "publisher"))
            from singleton import acquire_singleton_superseding
            self._singleton_lock = acquire_singleton_superseding(
                f"intelligence-launcher-{self.id}")
            if self._singleton_lock is None:
                self._log(f"[launcher] {_iso_now()} singleton: predecessor not "
                          f"reapable; launching anyway (restart must not block)")
        except Exception as exc:                  # never let the guard block a re-host
            self._log(f"[launcher] {_iso_now()} singleton acquire skipped: {exc!r}")
            self._singleton_lock = None
        self._install_signals()
        # [CXC-IC-SELF-IDENTITY] PASS the session transcript identity to the child, never let it
        # GUESS. The launcher KNOWS the session (RESUME_SESSION) and already derives its .jsonl for
        # the harness monitor's context reader; export that SAME derived path as
        # COMPOSEIO_SESSION_JSONL BEFORE fork/exec so the child `claude` — and the console's
        # in-process data layer (data_sources.resolve_session_jsonl) + any manual
        # start-console-daemons.sh it inherits — pins THIS session instead of `ls -t` newest-wins
        # (the cross-instance transcript leak, recurred 2026-07-24). Single-sourced via
        # _derive_jsonl_path (same derivation the monitor uses — no fork); an explicit pre-set env
        # is respected (never overridden).
        self._export_session_pin()
        self.start_child()
        # Connect the bus first so the /compact relay is live for the whole session.
        self.connect_mqtt()

        # Baseline harness monitor: context-threshold alerts + quiescence heartbeat + on-demand
        # context report (all delivered via publish_in on the `in` topic). Built from env unless
        # one was injected (tests) or HARNESS_MONITOR is off. [CXC-IC-LIFECYCLE]
        if self.monitor is None:
            try:
                self.monitor = build_monitor(self, self.resume_session)
            except Exception as exc:  # a monitor build failure must never block the proxy
                self._log(f"[launcher] {_iso_now()} monitor build failed (proxy continues): {exc!r}")
                self.monitor = None
        if self.monitor is not None:
            self.monitor.start_thread()
            self._log(f"[launcher] {_iso_now()} harness monitor started")

        in_fd = sys.stdin.fileno()
        out_fd = sys.stdout.fileno()
        restore = self._enter_raw(in_fd)
        try:
            # NO bootstrap step here — the prompt was exec'd with the child ([CXC-TERM-BOOTSTRAP-ARGV]).
            self.proxy_loop(in_fd, out_fd)
        finally:
            if restore is not None:
                restore()
        self.shutdown()
        return 0

    # ── Raw-mode helpers (restored on every exit) ───────────
    def _enter_raw(self, fd: int):
        """Put `fd` (the human's terminal) into raw mode so keystrokes pass through untouched to
        the child — the human interacts with claude exactly as if launched directly. Returns a
        restore() callback, or None if `fd` is not a tty (e.g. the selftest / piped stdin)."""
        if not os.isatty(fd):
            return None
        import termios
        import tty

        try:
            old = termios.tcgetattr(fd)
        except termios.error:
            return None
        tty.setraw(fd)

        def restore() -> None:
            try:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
            except (termios.error, OSError):
                pass

        return restore

    # ── Lifecycle helpers ───────────────────────────────────
    def _child_exited(self) -> bool:
        return self.child.child_exited()

    # ⛔ THERE IS NO `arm_bootstrap` ([CXC-TERM-BOOTSTRAP-ARGV], DELETED 2026-08-11).
    # The cold-start prompt is on the child's ARGV (appended in `__init__`), so there is no
    # timer to arm, no window between spawn and delivery, and NO byte written to a starting
    # child.  Do not reintroduce it: the mechanism was unsound for every value of the delay,
    # because the startup it raced contains an UNBOUNDED number of human-gated blocking reads
    # ([CXC-TERM-BOOTSTRAP-SETTLE-RETIRED] keeps the evidence and the three observations that
    # retired it).  If a positional prompt ever turns out to be swallowed by a gate, the
    # fallback is a harness-level structured input or an explicit human "orient" affordance —
    # NOT this timer.

    def _install_signals(self) -> None:
        def _handler(signum, frame):  # noqa: ANN001
            self._stop = True
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
        # SIGWINCH: the human resized the terminal the launcher is proxying into. Forward the new
        # geometry to the child ([CXC-TERM-WINSIZE]). Installed only on the run() path — an
        # in-process host (the terminal widget) owns its own signals and calls apply_winsize()
        # directly from its arranged rect.
        try:
            signal.signal(signal.SIGWINCH, self._on_sigwinch)
        except (ValueError, AttributeError, OSError):
            pass  # no SIGWINCH on this platform / not the main thread — proxy continues

    def shutdown(self) -> None:
        self._log(f"[launcher] {_iso_now()} SHUTDOWN")
        if self.monitor is not None:
            try:
                self.monitor.stop()
            except Exception:
                pass
        if self._mqtt is not None:
            for fn in ("loop_stop", "disconnect"):
                try:
                    getattr(self._mqtt, fn)()
                except Exception:
                    pass
        self.child.shutdown()   # SIGTERM the child + close the master fd ([CXC-TERM-PTYCORE])


def _parse_mqtt_url(url: str) -> tuple[str, int]:
    """'mqtt://host:port' → (host, port). Bare 'host' → (host, 1883)."""
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


# ── CLI entrypoint ──────────────────────────────────────────
def _load_config(argv: list[str]) -> dict:
    cfg = {
        "intelligence_id": DEFAULT_ID,
        # The ROLE is chain leg 3 ([CXC-TERM-MANIFEST-CHAIN]).  It defaults to the id, which
        # makes the chain a no-op for a hand-curated singleton like `architect` and the whole
        # point for `architect-review`, `architect-2`, … — N instances of ONE role.
        "role": os.environ.get("INTELLIGENCE_ROLE", "") or DEFAULT_ID,
        "mqtt_url": DEFAULT_MQTT,
        "claude_bin": DEFAULT_CLAUDE_BIN,
        "meta_manifest": os.environ.get("META_MANIFEST"),
        "resume_session": DEFAULT_RESUME,
    }
    if "--config" in argv:
        path = argv[argv.index("--config") + 1]
        with open(path, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
    return cfg


def main(argv: list[str]) -> int:
    _load_core()
    cfg = _load_config(argv)
    resume_session = cfg.get("resume_session") or ""
    # Default ON (David 2026-07-21: "--dangerously-skip-permissions is needed"; blanket allow is
    # fine — "you have root access to every machine I own"). Settings-level bypassPermissions +
    # allow rules cannot lift Claude Code's `.claude/` write guard; the launch flag can. Set
    # SKIP_PERMISSIONS=0 to opt a launch back OUT.
    skip_permissions = _envflag_default("SKIP_PERMISSIONS", True)

    # A COLD start is bootstrapped BY DEFAULT ([CXC-ICL-COLD]); `BOOTSTRAP=0` opts out, and a
    # resume never bootstraps (a re-host holds its context). The whole decision — resume?
    # opted out? which manifest? — belongs to the core so the widget answers it identically.
    # NOTE the silent no-op this fixes: the old gate also required `cfg["meta_manifest"]`,
    # which is `os.environ.get("META_MANIFEST")` and is None unless an operator set it — so
    # even an explicit BOOTSTRAP=1 injected NOTHING on a default launch.
    #
    # AN EXHAUSTED MANIFEST CHAIN REFUSES THE LAUNCH ([CXC-TERM-MANIFEST-CHAIN]) — a non-zero
    # exit naming every path tried, not a log line and a launch. Two hosts with different
    # answers to "what if there is no manifest" is exactly the fork-divergence the shared core
    # exists to prevent, and the divergent one is always the one nobody reads.
    try:
        seed_prompt = build_bootstrap_argv(
            cfg["intelligence_id"], resume_session,
            role=cfg.get("role") or "",
            manifest_path=cfg.get("meta_manifest"),
            log=lambda line: sys.stderr.write(line + "\n"))
    except BootstrapRefused as refusal:
        sys.stderr.write(f"[launcher] {_iso_now()} {refusal.message()}\n")
        sys.stderr.write("[launcher] no child was spawned. Author a manifest for this id or "
                         "its role, or set BOOTSTRAP=0 to launch deliberately unoriented.\n")
        return 3

    launcher = Launcher(
        intelligence_id=cfg["intelligence_id"],
        mqtt_url=cfg["mqtt_url"],
        claude_bin=cfg["claude_bin"],
        claude_args=build_claude_args(resume_session, skip_permissions),
        bootstrap_prompt=seed_prompt,
        resume_session=resume_session,
    )
    return launcher.run()


# ── Selftest (no real claude, no real broker) ───────────────
class _FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload.encode() if isinstance(payload, str) else payload


class _FakeMqtt:
    """In-memory MQTT stand-in matching the slice of the paho surface the launcher uses."""

    def __init__(self):
        self.on_message = None
        self.on_connect = None
        self._subs = set()
        self.published = []

    def subscribe(self, topic, qos=0):
        self._subs.add(topic)

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))
        if topic in self._subs and self.on_message is not None:
            self.on_message(self, None, _FakeMsg(topic, payload))

    def loop_stop(self):
        pass

    def disconnect(self):
        pass


def _proxy_harness(launcher: "Launcher"):
    """Wire the launcher's transparent proxy to a pair of pipes so the selftest can drive it
    WITHOUT a controlling terminal:
        in_w  → (in_r == launcher._stdin_fd)   feed the launcher's 'human stdin'
        (out_w passed to _pump) → out_r        collect what the launcher mirrors out
    Returns (in_w, box, pump) where box['text'] accumulates the mirrored child output and
    pump(cycles) runs that many proxy cycles, draining output after each."""
    in_r, in_w = os.pipe()
    out_r, out_w = os.pipe()
    os.set_blocking(out_r, False)
    launcher._stdin_fd = in_r
    box = {"text": "", "_fds": (in_r, in_w, out_r, out_w)}

    def drain() -> None:
        while True:
            try:
                r, _, _ = select.select([out_r], [], [], 0)
            except (OSError, ValueError):
                return
            if not r:
                return
            try:
                chunk = os.read(out_r, 65536)
            except (BlockingIOError, OSError):
                return
            if not chunk:
                return
            box["text"] += chunk.decode(errors="replace")

    def pump(cycles: int = 40, per: float = 0.03) -> None:
        for _ in range(cycles):
            launcher._pump(out_w, timeout=per)
            drain()

    return in_w, box, pump


def _selftest() -> int:
    _load_core()
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    here = os.path.dirname(os.path.abspath(__file__))
    dummy = os.path.join(here, "dummy_child.py")

    # ══ COLD MODE: transparent proxy + /compact relay ══════════════════════════════════════
    fake = _FakeMqtt()
    launcher = Launcher(
        intelligence_id="test-int-01",
        claude_bin=sys.executable,
        claude_args=[dummy],            # dummy stands in for `claude` (real PTY, real fork)
        bootstrap_prompt="",             # default: NOTHING injected at startup
        mqtt_client=fake,
        log=lambda line: None,           # quiet during the run; we print our own PASS lines
    )
    launcher.start_child()
    launcher.connect_mqtt()  # wire the fake MQTT (registers on_message + subscribes .../out)
    in_w, box, pump = _proxy_harness(launcher)

    # (a) child stdout is transparently forwarded to the proxied output.
    pump(40)
    check("STARTUP_MARKER" in box["text"],
          f"(a) child stdout not forwarded to proxied output. saw: {box['text']!r}")
    check("READY" in box["text"],
          f"(a) child READY marker not forwarded. saw: {box['text']!r}")
    # COLD argv (the dummy echoes it) carries NO --resume.
    check("--resume" not in box["text"],
          f"(a) COLD start spawned with --resume in argv (should be absent). saw: {box['text']!r}")

    # (b) bytes written to the launcher's stdin are forwarded to the child (dummy echoes them).
    os.write(in_w, b"hello-from-stdin\n")
    pump(40)
    check("ECHO:hello-from-stdin" in box["text"],
          f"(b) stdin not forwarded to child (no echo). saw tail: {box['text'][-200:]!r}")

    # (c) a compact directive types + SUBMITS `/compact <direction>` in THREE phases
    #     (clear → type → \r-submit), clearing any pre-existing prompt text first. Zero the
    #     inter-phase delay so the run is fast + deterministic (the dummy reads char-at-a-time).
    launcher.compact_phase_delay = 0
    direction = "Preserve the launcher-actuator invariant and the meta-object index; drop scratch."
    # Pre-load STALE text into the child's prompt (NO newline → it just sits there un-submitted),
    # reproducing the live bug: text in the entry area when the leaf fires /compact. The ESC-ESC
    # CLEAR must wipe it so it does not collide with (or concatenate onto) the injected command.
    os.write(in_w, b"stale half-typed text")
    pump(20)
    fake.publish(
        launcher.out_topic,
        json.dumps({"from": "test-int-01",
                    "text": json.dumps({"cmd": "compact", "direction": direction}), "ts": 1}),
    )
    pump(60)
    # The COMMAND record (kind=="compact") is the audit surface: EXACTLY `/compact <direction>`
    # with NO trailing newline (the newline was the bug — \n inserts, it does not submit).
    compact_injections = [r for r in launcher.injection_log if r["kind"] == "compact"]
    check(len(compact_injections) == 1,
          f"(c) expected exactly 1 compact COMMAND injection, got {len(compact_injections)}")
    if compact_injections:
        check(compact_injections[0]["keys"] == f"/compact {direction}",
              f"(c) compact command wrong (must have NO trailing newline): "
              f"{compact_injections[0]['keys']!r}")
    # The envelope: a CLEAR (ESC ESC) before and a SUBMIT (\r) after, each its own write, in order.
    kinds = [r["kind"] for r in launcher.injection_log]
    check(kinds[-3:] == ["compact-clear", "compact", "compact-submit"],
          f"(c) three-phase injection order wrong: {kinds[-3:]!r}")
    clear_rec = [r for r in launcher.injection_log if r["kind"] == "compact-clear"]
    submit_rec = [r for r in launcher.injection_log if r["kind"] == "compact-submit"]
    check(bool(clear_rec) and clear_rec[0]["keys"] == COMPACT_CLEAR,
          f"(c) clear phase did not send ESC ESC: {clear_rec!r}")
    check(bool(submit_rec) and submit_rec[0]["keys"] == COMPACT_SUBMIT,
          f"(c) submit phase did not send a carriage return: {submit_rec!r}")
    # The child SUBMITTED exactly the command — the stale text was CLEARED, not concatenated.
    check(f"ECHO:/compact {direction}" in box["text"],
          f"(c) dummy did not echo the SUBMITTED /compact line. saw tail: {box['text'][-200:]!r}")
    check("stale half-typed text" not in box["text"],
          f"(c) stale prompt text was NOT cleared before the command. saw tail: {box['text'][-200:]!r}")

    # (f) a PLAIN inject directive enters an ARBITRARY message on the instance's behalf — the
    #     general 'type this as if the human typed it' actuator (/compact is its special case).
    #     The line is entered VERBATIM (no /compact prefix), via the same three-phase mechanism.
    inject_msg = "hello — entered on my behalf, not typed by the human"
    fake.publish(
        launcher.out_topic,
        json.dumps({"from": "test-int-01",
                    "text": json.dumps({"cmd": "inject", "text": inject_msg}), "ts": 3}),
    )
    pump(60)
    inject_recs = [r for r in launcher.injection_log if r["kind"] == "inject"]
    check(len(inject_recs) == 1,
          f"(f) expected exactly 1 inject line, got {len(inject_recs)}")
    if inject_recs:
        check(inject_recs[0]["keys"] == inject_msg,
              f"(f) inject line wrong (must be the raw message — no /compact prefix, no newline): "
              f"{inject_recs[0]['keys']!r}")
    ikinds = [r["kind"] for r in launcher.injection_log]
    check(ikinds[-3:] == ["inject-clear", "inject", "inject-submit"],
          f"(f) three-phase inject order wrong: {ikinds[-3:]!r}")
    check(f"ECHO:{inject_msg}" in box["text"],
          f"(f) dummy did not echo the SUBMITTED inject line. saw tail: {box['text'][-200:]!r}")

    # (d) a NON-directive out-message injects NOTHING.
    before = len(launcher.injection_log)
    fake.publish(
        launcher.out_topic,
        json.dumps({"from": "test-int-01", "text": "just a normal reply, not a command", "ts": 2}),
    )
    pump(20)
    check(len(launcher.injection_log) == before,
          "(d) a non-directive out-message caused an injection")

    # (g) HARNESS MONITOR integration: a context QUERY presses NO key and is ANSWERED on the `in`
    #     topic (not the PTY). Wire a real HarnessMonitor over a scripted reader; assert the query
    #     round-trips to a publish on compose_projects/intelligence/<id>/in with the from="harness" envelope.
    #     Built through the SHARED FACTORY, not by constructing the class here: there is
    #     exactly ONE `HarnessMonitor(` construction site in the tree outside monitor.py's own
    #     selftest ([CXC-TERM-CHILD-TELEMETRY]), and a selftest that quietly opened a second
    #     one would be the fork the extraction exists to prevent.
    from monitor import KIND_QUERY  # sibling module
    launcher.monitor = build_monitor(launcher)
    launcher.monitor.read_context = lambda: {
        "pct": 47.5, "level": 0.475, "tokens": 475000,
        "window": 1_000_000, "source": "statusline-file"}
    inj_before = len(launcher.injection_log)
    pub_before = len(fake.published)
    # The query rides the reply-tool envelope (nested-in-text), exactly like a real reply.
    fake.publish(
        launcher.out_topic,
        json.dumps({"from": "test-int-01", "text": json.dumps({"cmd": "context"}), "ts": 9}),
    )
    pump(20)
    check(len(launcher.injection_log) == inj_before,
          "(g) a context query pressed a key on the child (must press NONE)")
    in_pubs = [(t, p) for (t, p) in fake.published[pub_before:] if t == launcher.in_topic]
    check(len(in_pubs) == 1,
          f"(g) context query did not publish exactly one answer to {launcher.in_topic}: "
          f"{fake.published[pub_before:]!r}")
    if in_pubs:
        ans = json.loads(in_pubs[0][1])
        check(ans.get("from") == "harness" and ans.get("kind") == KIND_QUERY,
              f"(g) query answer envelope wrong (from/kind): {ans!r}")
        check("47.5" in ans.get("text", "") and "statusline-file" in ans.get("text", ""),
              f"(g) query answer did not carry the %/source: {ans!r}")

    launcher.shutdown()

    # ══ RESUME / MOVE MODE: --resume + single dev-flag, NO startup injection ═════════════════
    RESUME_ID = "sess-2f9c1a-resume-01"

    # build_claude_args: RESUME prepends `--resume <id>` and keeps ONLY the single dev-flag
    # (no `--channels` — the double-registration bug); COLD carries no --resume.
    ca_resume = build_claude_args(RESUME_ID)
    check(ca_resume[:2] == ["--resume", RESUME_ID],
          f"(e) resume argv did not prepend --resume <id>: {ca_resume!r}")
    check("--dangerously-load-development-channels" in ca_resume,
          f"(e) resume argv missing the dev-channel flag: {ca_resume!r}")
    check("--channels" not in ca_resume,
          f"(e) resume argv wrongly includes --channels (double-registration bug): {ca_resume!r}")
    check(ca_resume.count("server:composeio-channel") == 1,
          f"(e) dev channel registered more than once: {ca_resume!r}")
    check("--resume" not in build_claude_args(""),
          "(e) COLD build_claude_args() should contain no --resume")
    # SKIP_PERMISSIONS adds the flag; default does not.
    check("--dangerously-skip-permissions" in build_claude_args("", skip_permissions=True),
          "(e) SKIP_PERMISSIONS did not add --dangerously-skip-permissions")
    check("--dangerously-skip-permissions" not in build_claude_args(""),
          "(e) --dangerously-skip-permissions present without SKIP_PERMISSIONS")

    fake2 = _FakeMqtt()
    resume_args = [dummy] + ca_resume  # dummy stands in for `claude`; gets --resume + dev-flag
    launcher2 = Launcher(
        intelligence_id="test-int-02",
        claude_bin=sys.executable,
        claude_args=resume_args,
        bootstrap_prompt="",             # RESUME injects NOTHING at startup
        resume_session=RESUME_ID,
        mqtt_client=fake2,
        log=lambda line: None,
    )
    launcher2.start_child()
    launcher2.connect_mqtt()
    _in_w2, box2, pump2 = _proxy_harness(launcher2)

    pump2(40)
    # The dummy echoed the spawn argv on its `ARGV:` line: --resume <id> AND the single dev-flag,
    # and NOT --channels. Assert against that line ONLY (later dummy output mentions --channels as
    # prose, which must not be mistaken for a spawn flag).
    argv_line = ""
    for ln in box2["text"].splitlines():
        if ln.startswith("ARGV:"):
            argv_line = ln
            break
    check(argv_line != "", "(e) dummy did not echo its argv in resume mode")
    check("--resume" in argv_line and RESUME_ID in argv_line,
          f"(e) spawned argv missing --resume <id>. saw: {argv_line!r}")
    check("--dangerously-load-development-channels" in argv_line,
          f"(e) spawned argv missing the dev-channel flag. saw: {argv_line!r}")
    check("--channels" not in argv_line,
          f"(e) spawned argv wrongly includes --channels (double-registration bug). saw: {argv_line!r}")
    # The load-bearing invariant: NOTHING was injected at startup in resume mode.
    check(len(launcher2.injection_log) == 0,
          f"(e) resume mode injected at startup (must be nothing): {launcher2.injection_log!r}")

    launcher2.shutdown()

    # ══ (h) HEARTBEAT CADENCE from the persisted meta-object (BUG 3 wiring) ══════════════════
    # load_heartbeat_cadence reads lifecycle.heartbeat (MINUTES) from the meta-object the
    # Config tab persists to, converts to seconds, and lets env (SECONDS) override.
    import tempfile as _tempfile
    with _tempfile.TemporaryDirectory() as _d:
        _meta = os.path.join(_d, "architect.meta.json")
        with open(_meta, "w", encoding="utf-8") as _fh:
            json.dump({"id": "architect", "lifecycle": {
                "compact_threshold": 0.93,
                "heartbeat": {"base": 10, "backoff_after": 60, "then": 60}}}, _fh)
        base_s, backoff_s, then_s = load_heartbeat_cadence("architect", _meta)
        check((base_s, backoff_s, then_s) == (600.0, 3600.0, 3600.0),
              f"(h) meta minutes not read as seconds: {(base_s, backoff_s, then_s)!r}")
        # a different persisted cadence (David sets 15/90/90 in the UI) round-trips.
        with open(_meta, "w", encoding="utf-8") as _fh:
            json.dump({"id": "architect", "lifecycle": {
                "heartbeat": {"base": 15, "backoff_after": 90, "then": 90}}}, _fh)
        check(load_heartbeat_cadence("architect", _meta) == (900.0, 5400.0, 5400.0),
              "(h) a persisted 15/90/90 cadence did not take effect")
        # env override (seconds) wins over the meta-object.
        os.environ["HEARTBEAT_BASE_S"] = "42"
        try:
            b, _bk, _th = load_heartbeat_cadence("architect", _meta)
            check(b == 42.0, f"(h) HEARTBEAT_BASE_S env override ignored: {b!r}")
        finally:
            del os.environ["HEARTBEAT_BASE_S"]
        # legacy HEARTBEAT_IDLE_S still maps to base.
        os.environ["HEARTBEAT_IDLE_S"] = "123"
        try:
            b2, _, _ = load_heartbeat_cadence("architect", _meta)
            check(b2 == 123.0, f"(h) legacy HEARTBEAT_IDLE_S did not map to base: {b2!r}")
        finally:
            del os.environ["HEARTBEAT_IDLE_S"]
        # a meta-object with no heartbeat block falls back to the coded defaults.
        with open(_meta, "w", encoding="utf-8") as _fh:
            json.dump({"id": "architect", "lifecycle": {}}, _fh)
        check(load_heartbeat_cadence("architect", _meta) == (600.0, 3600.0, 3600.0),
              "(h) missing heartbeat block did not fall back to 10/60/60 defaults")

    # ══ (i) PTY WINDOW SIZE — the CHILD reports it ([CXC-TERM-WINSIZE]) ══════════════════════
    # The acceptance signal is the CHILD's own view of its terminal, not "the ioctl was called".
    # Spawn through the SAME start_child() path and read what the child prints back.
    #
    # Deliberately odd, non-default numbers so a pass cannot be a coincidence: neither 24x80 (the
    # coded floor) nor whatever terminal happens to be running the selftest.
    PROBE_ROWS, PROBE_COLS = 37, 133
    RESIZE_ROWS, RESIZE_COLS = 44, 171

    # (i1) `stty size` — an INDEPENDENT witness (no Python, straight TIOCGWINSZ) that prints
    #      "<rows> <cols>". Under 0x0 it prints "0 0", which is the defect this proves gone.
    stty_l = Launcher(intelligence_id="test-int-winsize", claude_bin="/bin/sh",
                      claude_args=["-c", "stty size"], mqtt_client=_FakeMqtt(),
                      term_size=(PROBE_ROWS, PROBE_COLS), log=lambda line: None)
    stty_l.start_child()
    _in_w3, box3, pump3 = _proxy_harness(stty_l)
    pump3(40)
    check(f"{PROBE_ROWS} {PROBE_COLS}" in box3["text"],
          f"(i1) child `stty size` did not report {PROBE_ROWS}x{PROBE_COLS} — "
          f"saw: {box3['text']!r}")
    check("0 0" not in box3["text"],
          f"(i1) child still sees a 0x0 terminal: {box3['text']!r}")
    stty_l.shutdown()

    # (i2) a live RESIZE reaches the child: it re-reads its size on SIGWINCH and prints it.
    resize_src = (
        "import os,signal,sys,time\n"
        "def emit(tag):\n"
        "    s = os.get_terminal_size(0)\n"
        "    sys.stdout.write('%s:%dx%d\\n' % (tag, s.lines, s.columns)); sys.stdout.flush()\n"
        "signal.signal(signal.SIGWINCH, lambda *a: emit('WINCH'))\n"
        "emit('INIT')\n"
        "[time.sleep(0.05) for _ in range(200)]\n"
    )
    rz = Launcher(intelligence_id="test-int-winsize-2", claude_bin=sys.executable,
                  claude_args=["-c", resize_src], mqtt_client=_FakeMqtt(),
                  term_size=(PROBE_ROWS, PROBE_COLS), log=lambda line: None)
    rz.start_child()
    _in_w4, box4, pump4 = _proxy_harness(rz)
    pump4(60)
    check(f"INIT:{PROBE_ROWS}x{PROBE_COLS}" in box4["text"],
          f"(i2) child's INITIAL os.get_terminal_size was not {PROBE_ROWS}x{PROBE_COLS} — "
          f"saw: {box4['text']!r}")
    # The read-back on the master must agree with what the child sees (one source of truth).
    check(get_winsize(rz.master_fd) == (PROBE_ROWS, PROBE_COLS),
          f"(i2) master-side TIOCGWINSZ disagrees with the child: {get_winsize(rz.master_fd)!r}")
    rz.apply_winsize(RESIZE_ROWS, RESIZE_COLS)
    pump4(60)
    check(f"WINCH:{RESIZE_ROWS}x{RESIZE_COLS}" in box4["text"],
          f"(i2) resize did not reach the child (no SIGWINCH / stale size) — "
          f"saw tail: {box4['text'][-200:]!r}")
    check((rz.term_rows, rz.term_cols) == (RESIZE_ROWS, RESIZE_COLS),
          f"(i2) launcher did not record the applied size: {(rz.term_rows, rz.term_cols)!r}")
    rz.shutdown()

    # (i3) resolution precedence + the clamp, with no child involved.
    check(resolve_winsize(50, 200) == (50, 200), "(i3) explicit (rows, cols) not honoured")
    check(resolve_winsize() == (DEFAULT_PTY_ROWS, DEFAULT_PTY_COLS),
          "(i3) no explicit/env/tty input did not fall back to 24x80")
    check(resolve_winsize(1, 1) == (MIN_PTY_ROWS, MIN_PTY_COLS),
          "(i3) a degenerate size was not clamped to the 10x40 floor")
    os.environ["PTY_ROWS"], os.environ["PTY_COLS"] = "31", "121"
    try:
        check(resolve_winsize() == (31, 121), "(i3) PTY_ROWS/PTY_COLS env pin ignored")
        check(resolve_winsize(50, 200) == (50, 200),
              "(i3) env wrongly overrode an EXPLICIT size")
    finally:
        del os.environ["PTY_ROWS"], os.environ["PTY_COLS"]
    # rows-then-cols in the struct — the classic transposition bug, caught by asymmetric values.
    import struct as _struct
    check(_struct.unpack("HHHH", pack_winsize(11, 99)) == (11, 99, 0, 0),
          "(i3) pack_winsize field order is not (rows, cols, xpixel, ypixel)")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: launcher proxy+relay selftest (9/9 groups)\n")
    sys.stdout.write("  (a) child stdout is transparently forwarded to the proxied output; COLD argv has no --resume\n")
    sys.stdout.write("  (b) bytes written to the launcher's stdin are forwarded to the child (dummy echoed them)\n")
    sys.stdout.write("  (c) a compact directive typed + SUBMITTED '/compact <direction>' in 3 phases (ESC-ESC clear -> command -> \\r), clearing stale prompt text\n")
    sys.stdout.write("  (f) a plain inject directive entered an ARBITRARY message VERBATIM on the instance's behalf (same 3-phase actuator; /compact is its special case)\n")
    sys.stdout.write("  (d) a non-directive out-message caused NO injection\n")
    sys.stdout.write("  (g) a context query pressed NO key and was ANSWERED on the `in` topic (from=\"harness\" envelope)\n")
    sys.stdout.write("  (e) resume mode adds '--resume <id>' + the SINGLE dev-flag (no --channels) and injects NOTHING at startup\n")
    sys.stdout.write("  (h) the two-tier heartbeat cadence is READ from the persisted meta-object (minutes->seconds), env overrides win, legacy HEARTBEAT_IDLE_S maps to base (BUG 3 wiring)\n")
    sys.stdout.write("  (i) the CHILD reports the window size we set — `stty size` prints 37 133 (never '0 0'), os.get_terminal_size agrees, a live apply_winsize() SIGWINCHes the child to 44x171, and resolve_winsize's precedence/clamp/field-order hold [CXC-TERM-WINSIZE]\n")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    sys.exit(main(sys.argv))
