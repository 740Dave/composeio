"""ComposeUX TerminalWidget — the console HOSTS the Intelligence itself.

[SPEC:CXC-TERM].  A PTY-backed interactive `claude` session inside a ComposeApp: no
Terminal.app, no `osascript`, no macOS-only API.  The load-bearing consequence is
[CXC-TERM-CONVERGE]: because the widget owns the PTY master, a keystroke arriving from
the BUS (the launcher's `inject` actuation) and a keystroke arriving from the KEYBOARD
converge on the same fd through the same lock — control-is-a-peer, completed at the
input plane.

V1 TARGET MODE = `--ax-screen-reader` ([DD-TERM-TARGET-MODE], ratified by David
2026-08-11 from the census at `compose_projects/intelligence/tools/terminal_census_report/census.md`).
That census is why this file can be a SCROLLING LINE RENDERER rather than a screen-buffer
emulator.  Measured, at steady state, over a 20 s streaming response:

    profile          esc ratio  distinct seqs  alt-screen  abs cursor  DECSTBM  ED 2
    tui: fullscreen     0.68         17            0          118        1       0
    tui: default        0.50         17            0            0        0       0
    --ax-screen-reader  0.21          7            0            0        0       0

`--ax-screen-reader` emits SEVEN distinct sequences at steady state (cursor up, CHA,
EL, OSC title, and friends) and — decisively — the `ax + tui: fullscreen` profile looks
like `ax`, not like `fullscreen`: the FLAG DOMINATES the setting, so [CXC-TERM-PIN]'s
entire settings-composition requirement disappears.  Nothing is written to the user's
`~/.claude/settings.json` on this path, and David's global `tui: fullscreen` is untouched.

THREE MEASURED pyte hazards drive the shape of the byte path ([CXC-TERM-EMULATOR],
requirements.txt's pin note).  pyte 0.8.2:
  1. RAISES on `\x1b[?6n` (DECDSR) — `report_device_status() got an unexpected keyword
     argument 'private'` — and Claude Code emits it at startup in EVERY profile, so an
     unguarded `stream.feed()` crashes on the first burst;
  2. MISAPPLIES private-marker sequences: `CSI < u` (kitty keyboard pop) dispatches to
     restore_cursor and MOVES THE CURSOR, and `OSC 133;A` is read as OSC **1**;
  3. DROPS silently what it does not implement — including `?1049h` and `?2004h`.
That is why [CXC-TERM-SEQTABLE]'s pre-scan sits UPSTREAM of the emulator: the widget
tokenizes each chunk itself, counts every unsupported sequence, WITHHOLDS the hazardous
ones from pyte, and only then feeds what is left.  An emulator that silently drops is
exactly the subtly-wrong render [CXC-TERM-DEGRADE] forbids.

QUERIES ARE NOT ANSWERED, deliberately.  The child emits DA1 (`CSI c`), XTVERSION and
DECDSR at startup and proceeds without a reply: all five CLEAN-ROOM census profiles reached
a usable prompt and streamed a full response while the census tool answered nothing.  That
citation names its profile ON PURPOSE — the clean-room profiles ran with an empty MCP config
and permission prompts suppressed, so they are admissible about "does it need a reply to
proceed" and NOT about anything the startup DIALOGS touch ([CXC-TERM-CENSUS-CONFIG-FIDELITY];
the as-deployed profile is where a startup claim becomes admissible).  Replying would put
widget-originated bytes on the master fd before the human's first keypress, which TERM-C5 and
TERM-C13 forbid unconditionally.  So they are COUNTED and NAMED in the status strip instead
of answered — visible, not silent, and not a violation.

NOTHING IS TYPED AT STARTUP, AT ALL ([CXC-TERM-BOOTSTRAP-ARGV], ratified 2026-08-11).  The
cold-start bootstrap rides the child's positional prompt argument; there is no settle timer,
no `_arm_bootstrap`, and no `_type_bootstrap` in this class.  [CXC-TERM-DIALOGS] therefore
holds by CONSTRUCTION rather than by timing.  Mid-session `{"cmd":"compact"}` /
`{"cmd":"inject"}` actuation is a DIFFERENT seam against a live TUI and is untouched
([CXC-TERM-MIDSESSION-SEAM]).
"""
from __future__ import annotations

import os
import threading
import time
from collections import Counter, deque
from typing import Any, List, Optional, Tuple

from ...ptycore import (
    HOSTED_CHILD_THRESHOLDS, PASTE_END, PASTE_START, BootstrapRefused, host_intelligence_id,
    resolve_claude_bin,
)
from ...term_sequences import FEATURES, K_CSI, K_OSC, scan_unsupported, tokenize
from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

try:                                  # [CXC-TERM-BOUNDARIES] row 12
    import pyte
except Exception:                     # pragma: no cover - exercised by the refusal test
    pyte = None

# ── Defaults — every one stated, per [CXC-TERM-WIDGET] ─────────────────────
TARGET_MODE_AX = "ax-screen-reader"       # V1's target ([DD-TERM-TARGET-MODE], ratified)
TARGET_MODE_TUI_DEFAULT = "tui-default"   # measured, NOT V1's target -> degraded banner
TARGET_MODE_TUI_FULLSCREEN = "tui-fullscreen"   # explicitly out of scope -> degraded banner
SUPPORTED_TARGET_MODES = (TARGET_MODE_AX,)
DEFAULT_TARGET_MODE = TARGET_MODE_AX

AX_STARTUP_QUIET_MS = 0        # [CXC-TERM-AXMODE]: the speech hold is pure latency here
CHILD_TERM = "xterm-256color"

SCROLLBACK_LINES = 2000
TERM_FONT_PX = 12.0
CELL_H = 15.0                  # matches bus_monitor._LINE_H so the two log surfaces align
PAD = 8.0
MIN_COLS = 40
MIN_ROWS = 10
# The drain cap is a TIME budget with a byte ceiling, not a byte count
# ([CXC-TERM-DAMAGE], re-derived from measurement 2026-08-11).
#
# A byte count cannot bound a frame, because the cost of a byte is not a constant:
# pyte measured 437-662 B/ms on this grid, so the old flat 262 144 B ceiling admitted
# a ~390 ms emulator run INSIDE ONE FRAME — the window stops painting and stops
# answering the keyboard for a third of a second, which is exactly what "laggy" means.
# A budget in milliseconds bounds the stall directly and adapts to whatever the
# sequence mix actually costs.
#
# 8 ms leaves room for the frame's ~5 ms draw inside a 16.7 ms frame.  MAX_BYTES_PER_TICK
# survives as an absolute ceiling (a monotonic clock that does not advance must not turn
# into an unbounded drain), and DRAIN_SLICE_BYTES is the granularity at which the budget
# is checked — feed() carries an incomplete escape sequence across slices in `self._tail`,
# so a slice may fall anywhere in the stream.
MAX_BYTES_PER_TICK = 262144
DRAIN_BUDGET_S = 0.008
DRAIN_SLICE_BYTES = 2048
RESIZE_DEBOUNCE_S = 0.15
DIRTY_ROW_FULL_FRAC = 0.5
CURSOR_BLINK_HZ = 1.0
STRIP_H = 16.0
BANNER_H = 18.0
RELEASE_CHORD_LABEL = "Ctrl-Shift-Esc"

# Sequences whose arrival means V1's LINE-ORIENTED MODEL IS WRONG for what the child is
# doing ([CXC-TERM-DEGRADE]).  Not "unsupported" — model-breaking: the render cannot be
# trusted at all, so the banner is sticky and says so.
MODEL_BREAKING = frozenset({
    "alt_screen_1049", "alt_screen_47", "alt_screen_1047",
    "scroll_region", "clear_screen", "clear_scrollback",
})

# Sequences WITHHELD from pyte even though the table calls them supported, because pyte
# 0.8.2 gets them WRONG rather than merely missing them (census "MISAPPLIED" rows).  A
# withheld-but-supported sequence is NOT counted as degradation — pyte's own answer would
# have been to drop it too; the difference is that here it cannot corrupt the cursor.
_FEATURE_BY_NAME = {f.name: f for f in FEATURES}
_MISAPPLIED_NAMES = ("kitty_keyboard", "modify_other_keys")
# The input-plane mode families this widget TRACKS (never renders), derived from the
# shared table by name so a new mouse mode added there is tracked here for free.
_PASTE_MODES = tuple(n for n in _FEATURE_BY_NAME if n.startswith("bracketed_paste"))
_MOUSE_MODES = tuple(n for n in _FEATURE_BY_NAME if n.startswith("mouse_"))
# OSC codes pyte reads correctly.  It parses only the FIRST CHARACTER of an OSC code, so
# `OSC 133;A` becomes OSC 1 (set icon name) — anything outside this set is withheld.
_OSC_SAFE = (b"0", b"1", b"2")

# ── The keymap ([CXC-TERM-INPUT]) ──────────────────────────────────────────
# `on_key` sends ONLY named / control keys.  Printable characters come from `on_text`
# and ONLY from there — a widget that sends from both channels types everything twice.
NAMED_KEYS = {
    "Return": b"\r",            # NOT \n — live-verified: the TUI inserts \n, submits on \r
    "Enter": b"\r",
    "Keypad Enter": b"\r",
    "Escape": b"\x1b",
    "Backspace": b"\x7f",
    "Tab": b"\t",
    "Up": b"\x1b[A",
    "Down": b"\x1b[B",
    "Right": b"\x1b[C",
    "Left": b"\x1b[D",
    "Home": b"\x1b[H",
    "End": b"\x1b[F",
    "PageUp": b"\x1b[5~",
    "Page Up": b"\x1b[5~",
    "PageDown": b"\x1b[6~",
    "Page Down": b"\x1b[6~",
    "Delete": b"\x1b[3~",
    "Insert": b"\x1b[2~",
    "F1": b"\x1bOP", "F2": b"\x1bOQ", "F3": b"\x1bOR", "F4": b"\x1bOS",
    "F5": b"\x1b[15~", "F6": b"\x1b[17~", "F7": b"\x1b[18~", "F8": b"\x1b[19~",
    "F9": b"\x1b[20~", "F10": b"\x1b[21~", "F11": b"\x1b[23~", "F12": b"\x1b[24~",
}
SHIFT_TAB = b"\x1b[Z"
# PASTE_START / PASTE_END are IMPORTED from `composeio.ptycore`, not re-spelled here: the
# cold-start bootstrap ([CXC-ICL-COLD]) is wrapped in the same markers from the core, and two
# spellings of a wire constant is the fork the core exists to prevent ([CXC-TERM-PTYCORE]).
# They stay importable from this module (the import above binds them here) so existing
# callers and tests are unchanged.

_KMOD_SHIFT = 0x0003
_KMOD_CTRL = 0x00C0
_KMOD_ALT = 0x0300
_KMOD_GUI = 0x0C00

# ── ONE id, ONE instance, IN THIS PROCESS ([CXC-TERM-CHILD-ID], [DD-TERM-CHILD-ID-SCHEME]) ──
# The ratified scheme is scene-DECLARED: the scene carries a `role` plus an explicit
# `instance_id`, so ids stay human-readable and PREDICTABLE (`intelligence/architect-review/in`
# is a topic David can type and a peer can bind) and chain leg 2 can actually hit.  Its known
# weakness is that a copy-pasted scene re-collides — which is exactly what happened — so the
# refusal is WIDENED past the host's own id to cover SIBLING widgets in the same process.
# Registered on MOUNT and released on UNMOUNT: two terminals in one scene are both mounted, so
# the second one refuses before it can fork, while a test that merely constructs widgets never
# trips it.
_MOUNTED_IDS: dict = {}
_MOUNTED_IDS_LOCK = threading.Lock()


def claim_instance_id(instance_id: str, widget_id: str) -> None:
    if not instance_id:
        return
    with _MOUNTED_IDS_LOCK:
        _MOUNTED_IDS.setdefault(instance_id, widget_id)


def release_instance_id(instance_id: str, widget_id: str) -> None:
    with _MOUNTED_IDS_LOCK:
        if _MOUNTED_IDS.get(instance_id) == widget_id:
            _MOUNTED_IDS.pop(instance_id, None)


def sibling_holding(instance_id: str, widget_id: str) -> str:
    """The widget_id of ANOTHER mounted terminal already wearing `instance_id`, or ""."""
    with _MOUNTED_IDS_LOCK:
        holder = _MOUNTED_IDS.get(instance_id, "")
    return holder if holder and holder != widget_id else ""


def key_to_bytes(key: str, modifiers: int = 0) -> Optional[bytes]:
    """(SDL key name, KMOD bitmask) -> the bytes a terminal sends, or None when this key
    is NOT ours to send ([CXC-TERM-INPUT]).

    None is the load-bearing return: a bare printable key returns None here because it
    arrives again through `on_text` as committed text, and sending from both channels is
    how a terminal widget types every character twice."""
    mod = int(modifiers or 0)
    shift, ctrl, alt = bool(mod & _KMOD_SHIFT), bool(mod & _KMOD_CTRL), bool(mod & _KMOD_ALT)

    if key == "Tab" and shift:
        return SHIFT_TAB
    base = NAMED_KEYS.get(key)
    if base is not None:
        return (b"\x1b" + base) if alt else base
    if len(key) == 1:
        if ctrl:
            ch = key.upper()
            if "@" <= ch <= "_":                    # Ctrl-A..Ctrl-Z, Ctrl-[ ... Ctrl-_
                return bytes([ord(ch) - 0x40])
            if ch == "?":
                return b"\x7f"
            return None
        if alt:
            return b"\x1b" + key.encode("utf-8")
        return None                                 # printable -> on_text owns it
    return None


def _rgba(hexstr: str) -> Tuple[int, int, int, int]:
    return (int(hexstr[0:2], 16), int(hexstr[2:4], 16), int(hexstr[4:6], 16), 255)


# The 16 basic ANSI colours, mapped through the CONSOLE THEME's semantic tokens where a
# token exists, so the terminal reads as part of the console rather than a pasted-in
# xterm ([CXC-TERM-GRID]).  Colours 16-255 arrive from pyte pre-resolved as hex.
def _ansi_table(t) -> dict:
    return {
        "black": t.bg,
        "red": t.crit,
        "green": t.ok,
        "brown": t.warn,
        "blue": t.accent,
        "magenta": (0xC5, 0x8A, 0xF0, 255),
        "cyan": (0x4E, 0xD0, 0xE0, 255),
        "white": t.ink,
        "brightblack": t.faint,
        "brightred": t.crit,
        "brightgreen": t.ok,
        "brightbrown": t.warn,
        "brightblue": t.accent,
        "brightmagenta": (0xDA, 0xB0, 0xFF, 255),
        "brightcyan": (0x8A, 0xE8, 0xF5, 255),
        "brightwhite": (0xFF, 0xFF, 0xFF, 255),
    }


class TerminalWidget(Widget):
    """A `rows x cols` character grid on the Canvas text primitives, fed by a real PTY.

    Threading ([CXC-TERM-DAMAGE]): the PTY is read on a READER THREAD which only appends
    bytes to a queue; the emulator is fed on the RENDER THREAD via
    `EngineContext.schedule_update(fn, target=self)`, whose `target` argument exists
    precisely to scope the resulting damage to one widget.  The widget never touches
    emulator state from the reader thread and never calls mark_dirty / add_full — a
    terminal that marks full damage per chunk would hold the 1280x1300 console at ~19 fps
    forever ([DEC-DAMAGE-FANIN-SCOPE], which David caught on FEEL while the machine tests
    were green)."""

    def __init__(self, widget_id: str, *,
                 role: str = "",
                 instance_id: str = "",
                 claude_bin: Optional[str] = None,
                 claude_args: Optional[List[str]] = None,
                 target_mode: str = DEFAULT_TARGET_MODE,
                 resume_session: str = "",
                 autostart: bool = False,
                 cwd: Optional[str] = None,
                 bootstrap: Optional[bool] = None,
                 meta_manifest: Optional[str] = None,
                 thresholds: Optional[Tuple[int, ...]] = None,
                 scrollback_lines: int = SCROLLBACK_LINES,
                 font_px: float = TERM_FONT_PX,
                 cell_h: float = CELL_H,
                 pad: float = PAD,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "terminal", **kwargs)
        # THE CHILD'S ID IS NOT THE HOST'S ([CXC-TERM-CHILD-ID]).  There is no
        # `intelligence_id` scalar any more: the scene carries the ROLE (which manifest to
        # orient from, chain leg 3) and an explicit distinct INSTANCE_ID (the id the child
        # WEARS — its channel topics, its singleton lock, its telemetry namespace, its
        # refusal publish, all of them).  The old single scalar handed a hosted child its
        # host's id and produced all three symptoms at once: the child's `channel.mjs`
        # displaced the host's and the host lost its voice; the console showed the HOST's
        # telemetry under the child's name; and the console showed the HOST's transcript
        # under a label that fitted both.
        # `role` has NO SAFE DEFAULT ([CXC-FC-UNIT]).  It used to default to "architect", which
        # was harmless while one Intelligence existed and becomes a silent MIS-KEYING of the
        # project dir now that role keys the memory store ([CXC-FC-PROJECT-DIR]): an undeclared
        # role would quietly put this instance's memory in the ComposeUX architect's pile.  An
        # absent role now REFUSES (see `refusal()`), it does not guess.
        self.role = str(role or "").strip()
        self.instance_id = str(instance_id or "").strip()
        # explicit -> $CLAUDE_BIN -> $CLAUDE_CODE_EXECPATH -> "claude".  The EXECPATH leg is
        # not decoration: on this machine `claude` is a shell ALIAS and is NOT on PATH, and a
        # windowed host has no shell to expand it — the first live spawn died in `execvp` with
        # a misleading "No such file" naming a stale plugin dir ([ptycore.resolve_claude_bin]).
        self.claude_bin = resolve_claude_bin(claude_bin)
        self._claude_args_override = list(claude_args) if claude_args is not None else None
        # TARGET_MODE.  The default is the census-derived, ratified value; an operator may
        # override per launch.  An UNSET value is REFUSED rather than guessed (TERM-C6);
        # an out-of-scope value is accepted, applied, and immediately degraded — the
        # honest behaviour, not a refusal to launch ([CXC-TERM-PIN] default).
        self.target_mode = (target_mode or "").strip()
        self.resume_session = str(resume_session or "")
        self.autostart = bool(autostart)
        # THE CWD IS DERIVED FROM THE ROLE ([CXC-FC-CWD-FORK], [CXC-FC-DEFORK]).  This was
        # `cwd = cwd` — i.e. `None` unless a scene said otherwise — and `PtyChild` documents
        # what None means at `ptycore.py:233`: "cwd: None -> the host's cwd -> THE REPO".  So
        # the child spawned here on 2026-08-12 inherited the repo as its project key and wrote
        # the SAME `MEMORY.md` as its host, live.  The invariant already existed in the OTHER
        # spawn path (`IntelligenceHost.spawn`); it was never taught to this one — which is
        # [INSIGHT-FORK-DIVERGENCE-IS-A-CLASS], not an oversight.  Both paths now resolve
        # through `project_dir_for(role)`, one function, one definition site.
        #
        # An explicit `cwd:` in the scene still wins (a probe, a test); an absent role leaves it
        # None AND `refusal()` refuses the spawn, so None can no longer reach `pty.fork()` for a
        # managed instance.
        self.cwd = cwd if cwd else self._derive_cwd()
        # COLD-START ORIENTATION ([CXC-ICL-COLD], which [CXC-TERM-SUPERSEDES-55] carries into
        # this host UNCHANGED).  `bootstrap=None` means "the env decides", and the env's
        # default is ON — `BOOTSTRAP=0` is the opt-OUT.  A scene may pin it either way.
        self.bootstrap = bootstrap
        self.meta_manifest = meta_manifest
        # The hosted child's context thresholds ([CXC-TERM-CHILD-TELEMETRY]): 90 and 95 ONLY
        # (David, 2026-08-11 — 65 and 80 were not useful).  A CONSTRUCTION ARGUMENT, not an
        # edit to HarnessMonitor's declared default, precisely so re-deciding the values is a
        # value change rather than a redesign.
        self.thresholds = tuple(thresholds) if thresholds else HOSTED_CHILD_THRESHOLDS
        self.scrollback_lines = int(scrollback_lines)
        self.font_px = float(font_px)
        self.cell_h = float(cell_h)
        self.pad = float(pad)

        # Engine affordances ([CXC-TERM-WIDGET] defaults)
        self.accepts_focus = True
        self.interactive = True
        self.captures_tab = True        # [CXC-TERM-INPUT] — the engine opt-out
        self.focus_ring = False         # our own border + block cursor signal focus

        # Emulator + byte path
        self._screen = None
        self._stream = None
        self._queue = bytearray()
        self._queue_lock = threading.Lock()
        self._drain_pending = False
        self._tail = b""                # [CXC-TERM-SEQTABLE] chunk-boundary carry
        self._counts: Counter = Counter()
        self._degraded = False
        self._degrade_reason = ""
        self._bracketed_paste = False
        self._mouse_tracking = False
        self._title = ""

        # Child + lifecycle
        self._child = None
        self._reader: Optional[threading.Thread] = None
        self._reader_stop = threading.Event()
        self._singleton_lock = None
        self._state = "idle"
        self._plate: str = ""
        self._disconnected = False
        self._refusal: Optional[BootstrapRefused] = None
        self._mounted_claim = False
        # THE LOG IS NOT A SINK ANY MORE ([CXC-TERM-MANIFEST-CHAIN]).  This was literally
        # `def _log(self, line): pass`, so every core diagnostic — including "manifest
        # unreadable" — was DISCARDED at the call site, and an unoriented instance looked
        # identical to an oriented one on the glass.  A bounded ring keeps the last lines
        # readable (`log_lines`) without turning the widget into a log viewer; the refusal
        # itself does not rely on it — it reaches the PLATE and the BUS.
        self._log_lines: deque = deque(maxlen=200)
        # The hosted child's telemetry ([CXC-TERM-CHILD-TELEMETRY]): the launcher's EXISTING
        # HarnessMonitor, constructed through the SHARED factory, on an IN-PROCESS thread
        # whose lifetime is exactly the child's ([DD-TERM-CHILD-TELEMETRY-HOST], ratified
        # 2026-08-11: in-process, so there is no orphan to reap).
        self._monitor = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._child_jsonl: str = ""
        self._spawned_at: float = 0.0
        # The child's OWN MCP config ([CXC-IC-CHANNEL-INSTANCE]) — generated at spawn,
        # deleted at teardown.  See `_ensure_mcp_config`.
        self._mcp_config: str = ""
        # Injectable for tests — the real read is SDL's clipboard, imported lazily so this
        # module stays importable headless ([CXC-TERM-INPUT] ⌘V).
        self._clipboard_reader = None

        # Geometry / metrics
        self._cell_w: Optional[float] = None
        self._metrics_key: Optional[tuple] = None
        self._ascent = 0.0
        self._cols: Optional[int] = None
        self._rows: Optional[int] = None
        self._last_resize_at = 0.0
        self._pending_resize: Optional[Tuple[int, int]] = None
        self._scroll_offset = 0
        self._cursor_on = True
        self._blink_phase = 0.0

    # ── identity ([CXC-TERM-CHILD-ID]) ──────────────────────────────────
    @property
    def intelligence_id(self) -> str:
        """The id the CHILD wears — every namespace keys on this and on nothing else.

        Read-only on purpose.  It was a settable scalar defaulting to `"architect"`, and that
        default is precisely how a hosted child came to share its host's identity.  A mixed
        keying (some namespaces on the child's id, some on the scene's) is symptom 2 of
        [CXC-TERM-CHILD-ID] in a new costume, so there is ONE accessor."""
        return self.instance_id

    def host_id(self) -> str:
        return host_intelligence_id()

    def log_lines(self) -> List[str]:
        return list(self._log_lines)

    # ── geometry ────────────────────────────────────────────────────────
    def _chrome_h(self) -> float:
        return STRIP_H + (BANNER_H if self._degraded else 0.0)

    def grid_size(self, rect: Optional[Rect] = None) -> Optional[Tuple[int, int]]:
        """(cols, rows) for `rect`, or None while the cell metrics are unmeasured.

        `CELL_W` is `measure_text("M")` at the chosen font/size, NOT a constant: hardcoding
        a cell width makes the grid drift the moment the bundled font changes
        ([CXC-TERM-GRID])."""
        r = rect if rect is not None else self.arranged_rect
        if r is None or self._cell_w is None or self._cell_w <= 0:
            return None
        cols = int((r.w - 2 * self.pad) // self._cell_w)
        rows = int((r.h - 2 * self.pad - self._chrome_h()) // self.cell_h)
        return (max(cols, 0), max(rows, 0))

    def _fits(self, size: Optional[Tuple[int, int]]) -> bool:
        return bool(size and size[0] >= MIN_COLS and size[1] >= MIN_ROWS)

    def set_cell_metrics(self, cell_w: float, ascent: float = 10.0) -> None:
        """Inject measured metrics (the headless/test path; draw() measures for real)."""
        self._cell_w = float(cell_w)
        self._ascent = float(ascent)

    def _ensure_metrics(self, canvas: Canvas) -> None:
        key = (ct.FONT_MONO, self.font_px, ct.CONSOLE.active.name)
        if self._metrics_key == key and self._cell_w:
            return
        canvas.set_font(ct.FONT_MONO, self.font_px, "regular")
        m = canvas.measure_text("M")
        self._cell_w = float(m.width) or 1.0
        self._ascent = float(getattr(m, "ascent", self.font_px * 0.8))
        self._metrics_key = key

    # ── emulator ────────────────────────────────────────────────────────
    def _ensure_screen(self, cols: int, rows: int) -> bool:
        if pyte is None:
            return False
        if self._screen is None:
            self._screen = pyte.HistoryScreen(cols, rows, history=self.scrollback_lines,
                                              ratio=0.5)
            self._stream = pyte.ByteStream(self._screen)     # incremental UTF-8 decoder
        elif (self._screen.columns, self._screen.lines) != (cols, rows):
            self._screen.resize(rows, cols)
        return True

    def attach_emulator(self, cols: int, rows: int) -> bool:
        """Create the emulator with NO child — the slice-2 path ([CXC-TERM-SLICES]):
        everything visual becomes testable against a RECORDED byte stream, deterministically,
        before a real `claude` is involved."""
        self._cols, self._rows = cols, rows
        return self._ensure_screen(cols, rows)

    # ── the byte path ───────────────────────────────────────────────────
    def _on_child_bytes(self, data: bytes) -> None:
        """READER THREAD.  Append only — never touch emulator state here.

        The ONE thing done besides enqueueing is `note_activity`, exactly as the launcher's
        sink does it ([CXC-TERM-CHILD-TELEMETRY]): child output IS the Intelligence
        generating, so the quiescence heartbeat measures idle from the last time it actually
        produced something.  Without it the hosted child would be nudged while it works —
        the monitor's own echo-grace logic then handles the reply-to-our-nudge case."""
        mon = self._monitor
        if mon is not None:
            try:
                mon.note_activity()
            except Exception:
                pass
        with self._queue_lock:
            self._queue += data
            pending = self._drain_pending
            self._drain_pending = True
        if pending:
            return                       # a drain is already scheduled: coalesce
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "schedule_update"):
            ctx.schedule_update(self.drain_once, target=self)
        else:                            # no engine (tests): drain inline
            self.drain_once()

    def drain_once(self) -> int:
        """RENDER THREAD.  Feed the emulator for AT MOST `DRAIN_BUDGET_S` (ceiling
        `MAX_BYTES_PER_TICK`) and damage the dirty rows.  The remainder carries to the
        next tick — an uncapped drain turns a 1 MiB burst into a visible freeze
        ([CXC-TERM-DAMAGE]), and so did the old flat byte cap: see the constants.

        Returns the bytes consumed this tick."""
        deadline = time.monotonic() + DRAIN_BUDGET_S
        consumed = 0
        while True:
            with self._queue_lock:
                self._drain_pending = False
                room = min(DRAIN_SLICE_BYTES, MAX_BYTES_PER_TICK - consumed)
                chunk = bytes(self._queue[:room]) if room > 0 else b""
                del self._queue[:len(chunk)]
                leftover = len(self._queue)
            if not chunk:
                break
            self.feed(chunk)
            consumed += len(chunk)
            if not leftover or consumed >= MAX_BYTES_PER_TICK:
                break
            if time.monotonic() >= deadline:
                break
        if not consumed:
            return 0
        if leftover:
            # More waiting: schedule another drain rather than exceeding the frame cap.
            with self._queue_lock:
                self._drain_pending = True
            ctx = self._ctx
            if ctx is not None and hasattr(ctx, "schedule_update"):
                ctx.schedule_update(self.drain_once, target=self)
        return consumed

    def feed(self, data: bytes) -> None:
        """The pre-scan + filtered feed.  PUBLIC so a recorded byte stream can drive the
        whole render path with no live child ([CXC-TERM-SLICES] slice 2)."""
        buf = self._tail + data
        tokens, self._tail = tokenize(buf)

        # 1. COUNT every unsupported sequence, BEFORE the emulator sees anything
        #    ([CXC-TERM-SEQTABLE]).  The tail carry is what makes a sequence split across
        #    two os.read chunks count exactly once.
        newly_breaking = None
        for name, _raw in scan_unsupported(tokens):
            self._counts[name] += 1
            # The FIRST model-breaking sequence in the chunk names the banner: it is the
            # one that broke the model, and a later one overwriting it would report the
            # consequence instead of the cause.
            if name in MODEL_BREAKING and not self._degraded and newly_breaking is None:
                newly_breaking = name

        # 2. TRACK the input-plane modes we must honour but never render.  Set and reset
        #    are separate rows of the SHARED table, so the widget never re-parses a mode
        #    number itself ([CXC-TERM-SEQTABLE]).
        for tok in tokens:
            for name in _PASTE_MODES:
                if _FEATURE_BY_NAME[name].match(tok):
                    self._bracketed_paste = not name.endswith("_off")
            for name in _MOUSE_MODES:
                if _FEATURE_BY_NAME[name].match(tok):
                    self._mouse_tracking = not name.endswith("_off")
            if tok.kind == K_OSC and tok.raw[2:].split(b";", 1)[0] in (b"0", b"2"):
                body = tok.raw[2:]
                if b";" in body:
                    self._title = body.split(b";", 1)[1].rstrip(b"\x07\x1b\\").decode(
                        "utf-8", "replace")

        if newly_breaking is not None:
            self._enter_degraded(
                f"{newly_breaking} — V1 renders a scrolling line model; this session is "
                f"driving a screen buffer, so the render above is NOT trustworthy")

        # 3. FEED what is left.  Everything unsupported or misapplied is withheld: pyte
        #    would either crash (DECDSR), corrupt the cursor (CSI < u), or drop it anyway.
        if self._screen is None:
            return
        keep = bytearray()
        for tok in tokens:
            if self._withhold(tok):
                continue
            keep += tok.raw
        if keep:
            try:
                self._stream.feed(bytes(keep))
            except Exception as exc:      # an emulator crash must not take the console down
                self._enter_degraded(f"emulator error: {exc!r}")
                return
        self._damage_dirty()

    def _withhold(self, tok) -> bool:
        for f in FEATURES:
            if f.match(tok):
                if not f.supported:
                    return True
                if f.name in _MISAPPLIED_NAMES:
                    return True
        if tok.kind == K_OSC and tok.raw[2:].split(b";", 1)[0] not in _OSC_SAFE:
            return True                    # pyte reads only the FIRST char of an OSC code
        if tok.kind == K_CSI and tok.raw[2:3] in (b"<", b"=", b"!"):
            return True                    # pyte only recognizes `?` as a private marker
        return False

    def _damage_dirty(self) -> None:
        """Damage the UNION of the dirty rows' bands — not the widget rect and NEVER the
        scene.  A one-line update damages ~15 px ([CXC-TERM-DAMAGE])."""
        scr, ctx, rect = self._screen, self._ctx, self.arranged_rect
        if scr is None:
            return
        dirty = sorted(scr.dirty)
        scr.dirty.clear()
        if not dirty or ctx is None or rect is None or self._rows is None:
            return
        rows = max(self._rows, 1)
        if len(dirty) > DIRTY_ROW_FULL_FRAC * rows:
            ctx.request_redraw(rect)       # one rect is cheaper than N overlapping ones
            return
        top = rect.y + self.pad + (BANNER_H if self._degraded else 0.0)
        x, w = rect.x + self.pad, rect.w - 2 * self.pad
        for y in dirty:
            if 0 <= y < rows:
                ctx.request_redraw(Rect(x, top + y * self.cell_h, w, self.cell_h))

    # ── degradation ([CXC-TERM-DEGRADE]) ────────────────────────────────
    def _enter_degraded(self, reason: str) -> None:
        if self._degraded:
            return                        # sticky: a flickering failure banner is a lie
        self._degraded = True
        self._degrade_reason = reason
        self._publish_degraded()
        self.request_redraw()

    def _publish_degraded(self) -> None:
        """Bus-visible, not just pixel-visible: a condition visible only in pixels cannot
        be regression-tested ([CXC-TERM-DEGRADE])."""
        top = self._counts.most_common(1)
        payload = {"degraded": self._degraded,
                   "counts": dict(self._counts),
                   "top": top[0][0] if top else ""}
        try:
            self.publish(f"intelligence/{self.intelligence_id}/terminal/degraded", payload)
        except Exception:
            pass                          # bus down: rendering + typing are UNAFFECTED

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def unsupported_counts(self) -> dict:
        return dict(self._counts)

    # ── lifecycle ───────────────────────────────────────────────────────
    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        # CLAIM the instance id for as long as this widget is mounted, so a SIBLING terminal
        # in the same scene refuses instead of forking a second child under one id
        # ([DD-TERM-CHILD-ID-SCHEME]: the declared scheme's known weakness is the copy-paste,
        # and this is the widening that answers it).
        if self.instance_id and not sibling_holding(self.instance_id, self.widget_id):
            claim_instance_id(self.instance_id, self.widget_id)
            self._mounted_claim = True
        bus = getattr(ctx, "bus", None) if ctx else None
        if bus is not None and hasattr(bus, "subscribe"):
            try:                          # the /compact relay ([CXC-TERM-CONVERGE])
                bus.subscribe(f"intelligence/{self.intelligence_id}/out",
                              lambda _ns, value, _old=None: self.on_out_message(value))
            except Exception:
                pass
        if self.autostart:
            self.start_session()

    def on_unmount(self) -> None:
        """[CXC-TERM-WIDGET]: stop the pump, release the singleton, close the fd, SIGTERM
        the child.  A tab switch that leaves an orphaned `claude` holding the singleton is
        a defect that only shows up an hour later."""
        self.stop_session()
        if self._mounted_claim:
            release_instance_id(self.instance_id, self.widget_id)
            self._mounted_claim = False
        super().on_unmount()

    def _headless(self) -> bool:
        """A headless render must NOT spawn ([CXC-TERM-WIDGET], boundary row 11): the
        capture suite would otherwise fork a `claude` per reference image."""
        ctx = self._ctx
        return bool(getattr(ctx, "headless", False)) if ctx is not None else False

    def _derive_cwd(self) -> Optional[str]:
        """`~/compose-instances/<role>/` for this widget's declared role, or None.

        Calls the ONE definition site ([CXC-FC-DEFORK]) rather than composing the path here —
        a second spelling of this derivation is the fork the seam exists to prevent.  Returns
        None (rather than raising) for an absent/illegal role so a widget can still be
        CONSTRUCTED in a test or a headless parse; the refusal lands at `refusal()`, which is
        the gate `start_session()` actually passes through.
        """
        if not self.role:
            return None
        try:
            from ...instance_home import project_dir_for
            return project_dir_for(self.role)
        except Exception:                     # RoleRefused / import failure -> refuse at spawn
            return None

    def refusal(self) -> str:
        """Why `start_session()` would refuse right now ("" == it would spawn).  The
        refusals are the honest half of TERM-C6 and boundary rows 1 and 12.

        The IDENTITY refusals come FIRST and are checked BEFORE `pty.fork()`, because all
        three symptoms of a shared id are invisible FROM INSIDE the child — it sees a working
        channel and a healthy transcript; only the displaced host and the human at the console
        see the damage, and by then the host is already off the air ([CXC-TERM-CHILD-ID])."""
        if not self.instance_id:
            return ("no instance_id — the scene must carry `role` PLUS a distinct "
                    "`instance_id` ([CXC-TERM-CHILD-ID]); refusing to guess one")
        if not self.role and not self.cwd:
            # [CXC-FC-UNIT] / [CXC-FC-PROJECT-DIR]: an absent role has no safe default now that
            # role keys the memory store.  Spawning anyway would inherit the host's cwd — the
            # repo — and put this instance's memory in the shared pile.  A REFUSAL is the whole
            # point: guessing is silent and lands in somebody else's store.
            return ("no role — the pane declares the (instance_id, role) PAIR "
                    "([CXC-FC-UNIT]); without a role the child's project dir would fall back "
                    "to the repo and its memory would join the shared pile "
                    "([CXC-FC-PROJECT-DIR]); refusing to guess one")
        host = self.host_id()
        if host and self.instance_id == host:
            return (f"ID COLLISION — instance_id {self.instance_id!r} is the HOST's own id. "
                    f"Spawning it would displace the host on the channel "
                    f"([CXC-TERM-CHILD-ID]); no fork.")
        sib = sibling_holding(self.instance_id, self.widget_id)
        if sib:
            return (f"ID COLLISION — widget {sib!r} in this process already hosts "
                    f"{self.instance_id!r}. One id, one instance ([CXC-TERM-CHILD-ID]).")
        if pyte is None:
            return "terminal emulator unavailable — pip install -r requirements.txt"
        if not self.target_mode:
            # TERM-C6: refuse rather than guess.  The census decides the target; a widget
            # that picked one itself would be rendering an unmeasured mode.
            return ("TARGET_MODE unset — refusing to spawn rather than guess "
                    "([DD-TERM-TARGET-MODE])")
        size = self.grid_size()
        if size is None:
            return "not laid out yet — no grid geometry"
        if not self._fits(size):
            return f"TERMINAL TOO SMALL — {size[0]}x{size[1]}, needs {MIN_COLS}x{MIN_ROWS}"
        return ""

    # ── the child's OWN channel identity ([CXC-IC-CHANNEL-INSTANCE]) ────
    def _ensure_mcp_config(self) -> str:
        """The path to THIS child's MCP config, generated once per session.

        [CXC-TERM-CHILD-ID] renamed the lock, the subscribe, the telemetry and the refusal
        publish — every surface the WIDGET owns — and left the one surface it does not own:
        with no `--mcp-config`, the child loads the REPO's `.mcp.json`, whose channel server
        pins `INTELLIGENCE_ID=architect`, and binds THE HOST'S TOPICS regardless of
        `instance_id`.  That is symptom 1 of the first live spawn (the host loses its voice),
        still live on the child's side.

        Env cannot fix it — an `env` block in `.mcp.json` OVERRIDES the inherited
        environment — so the child gets a DERIVED config: the real file, deep-copied, one key
        changed, `--strict-mcp-config` so it cannot also load the repo's and re-acquire the
        host's id.  Raises `BootstrapRefused` if the source config cannot be derived from;
        `start_session` turns that into a plate + a bus publish and no fork."""
        if self._mcp_config:
            return self._mcp_config
        from ...ptycore import write_child_mcp_config
        self._mcp_config = write_child_mcp_config(self.instance_id, log=self._log)
        return self._mcp_config

    def _release_mcp_config(self) -> None:
        """A generated config outlives nothing: it is deleted when the session stops.  A
        stale file is a stale identity."""
        if self._mcp_config:
            from ...ptycore import remove_child_mcp_config
            remove_child_mcp_config(self._mcp_config)
            self._mcp_config = ""

    @property
    def mcp_config_path(self) -> str:
        """The generated config's path while a session is up ("" otherwise) — readable so the
        binding is assertable without parsing argv."""
        return self._mcp_config

    def child_argv(self, bootstrap_prompt: str = "") -> List[str]:
        """The child's argv.  An explicit `claude_args` means the CALLER OWNS THE WHOLE
        TAIL (the launcher's existing contract) — no mode flag is injected into it, since
        a flag prepended to someone else's argv is a spawn failure, not a mode.  That
        contract covers the MCP flags too: a caller who spells its own tail spells its own
        config, and we do not generate one behind its back.

        `bootstrap_prompt` is appended LAST as the POSITIONAL PROMPT ARGUMENT
        ([CXC-TERM-BOOTSTRAP-ARGV]): `claude [options] [command] [prompt]`.  This is the whole
        cold-start delivery — there is no timer, no settle, and no byte written to a starting
        child, so [CXC-TERM-DIALOGS] holds BY CONSTRUCTION.  `--mcp-config` is VARIADIC, so it
        is emitted upstream of `CHANNEL_FLAGS` and can never swallow this positional
        (`ptycore.build_claude_args`)."""
        from ...ptycore import build_claude_args
        if self._claude_args_override is not None:
            argv = [self.claude_bin] + list(self._claude_args_override)
        else:
            skip = os.environ.get("SKIP_PERMISSIONS", "1").strip().lower() not in (
                "0", "false", "no", "off")
            args = build_claude_args(self.resume_session, skip_permissions=skip,
                                     mcp_config=self._ensure_mcp_config())
            if self.target_mode == TARGET_MODE_AX:
                args = ["--ax-screen-reader"] + args
            argv = [self.claude_bin] + args
        if bootstrap_prompt:
            argv.append(bootstrap_prompt)
        return argv

    def child_env(self, session_pin: str = "") -> dict:
        """The env the widget ADDS to the child's.  Everything here is DELIBERATE — the core
        strips the host's identity out of the inherited environment and then applies this on
        top ([CXC-IC-SELF-IDENTITY], `ptycore.sanitize_child_env`), so a value that appears in
        the child either was chosen here or describes the machine, never the host session.

        `session_pin` is the RESUME/MOVE case: the pin names the transcript of the session we
        are RE-HOSTING — the child's OWN — so it is PASSED, not inherited.  A cold start passes
        nothing and the fresh instance resolves by its own rules, which is the entire point."""
        env = {"TERM": CHILD_TERM}
        if self.target_mode == TARGET_MODE_AX:
            env["CLAUDE_AX_STARTUP_QUIET_MS"] = str(AX_STARTUP_QUIET_MS)
        if session_pin and self.resume_session:
            env["COMPOSEIO_SESSION_JSONL"] = session_pin
        return env

    def start_session(self) -> bool:
        """Spawn the child.  Returns True when a child is running.

        ZERO bytes are written between here and the human's first keypress — UNCONDITIONALLY
        ([CXC-TERM-DIALOGS], TERM-C5, TERM-C13).  The startup dialogs are RENDERED and the
        human answers them in the widget, with the keyboard.  Auto-answering them collided
        with the human and hung once; it is out of scope permanently, not deferred.

        There is NO LONGER an exception to that rule.  The cold-start bootstrap used to be
        typed after a fixed settle delay; it now rides the child's POSITIONAL PROMPT ARGUMENT
        ([CXC-TERM-BOOTSTRAP-ARGV]), so the widget writes nothing at startup at all and the
        old carve-out ("the bootstrap timer excepted") is deleted with the timer.

        FOUR things are decided BEFORE `pty.fork()` and any one of them REFUSES the spawn:
        the id (it must not be the host's or a sibling's, [CXC-TERM-CHILD-ID]); the manifest
        chain (`<id>` -> `<role>` -> refuse, [CXC-TERM-MANIFEST-CHAIN]); the MCP config the
        child's channel identity comes from ([CXC-IC-CHANNEL-INSTANCE]); and the target mode."""
        if self._child is not None:
            return True
        if self._headless():
            self._plate = "headless — no session"
            return False
        why = self.refusal()
        if why:
            self._plate = why
            self.request_redraw()
            return False

        # THE MANIFEST CHAIN AND THE CHANNEL IDENTITY, BEFORE THE FORK
        # ([CXC-TERM-MANIFEST-CHAIN], [CXC-IC-CHANNEL-INSTANCE]).  Neither degrades and
        # neither logs-and-continues: they refuse, on the glass AND on the bus, and the
        # widget stays immediately re-launchable.  The whole argv — including the generated
        # `--mcp-config` — is built HERE, before the singleton is taken, so a refusal leaves
        # no lock and no half-spawn behind.
        try:
            bootstrap_prompt = self._bootstrap_argv()
            argv = self.child_argv(bootstrap_prompt)
        except BootstrapRefused as refusal:
            self._enter_refused(refusal)
            self._release_mcp_config()
            return False

        cols, rows = self.grid_size()
        if self.target_mode not in SUPPORTED_TARGET_MODES:
            # Accepted, applied, and immediately degraded ([CXC-TERM-PIN] default,
            # TERM-C6): an out-of-scope mode is REFUSED HONESTLY, not rendered badly.
            self._enter_degraded(
                f"target mode {self.target_mode!r} is outside V1's scope — V1 targets "
                f"{TARGET_MODE_AX} (measured); this render is NOT trustworthy")

        self._acquire_singleton()
        from ...ptycore import PtyChild, export_session_pin
        pin = export_session_pin(self.resume_session, self._log)    # TERM-C7, BEFORE fork
        self._ensure_screen(cols, rows)
        self._cols, self._rows = cols, rows
        self._refusal = None
        self._spawned_at = time.time()
        # The role home must EXIST before the child `os.chdir`s into it, or exec dies with a
        # misleading 127.  Created here (at spawn) rather than at construction: a widget is
        # constructed in headless parses and tests that must not touch the filesystem.  The
        # MEMORY store inside it stays lazy — an unused role still has no `memory/` dir
        # ([CXC-FC-ROLE-MEMORY] default).
        if self.cwd:
            try:
                os.makedirs(self.cwd, exist_ok=True)
            except OSError as exc:
                self._log(f"[terminal] could not create role home {self.cwd!r}: {exc}")
        self._child = PtyChild(argv, term_size=(rows, cols),
                               env_extra=self.child_env(pin), cwd=self.cwd, log=self._log)
        self._child.start()
        self._state = f"running pid {self._child.pid}"
        self._disconnected = False
        self._reader_stop.clear()
        self._reader = threading.Thread(target=self._read_loop, name="terminal-pty",
                                        daemon=True)
        self._reader.start()
        self._start_monitor(pin)
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "register_clocked"):
            ctx.register_clocked(self)
        self.request_redraw()
        return True

    def _log(self, line: str) -> None:
        """The core's diagnostics, KEPT.  This was `pass` — a discarding sink — which is why
        "the manifest could not be read" was invisible on the glass ([CXC-TERM-MANIFEST-CHAIN]
        evidence).  Bounded, so a chatty session cannot grow it without limit."""
        self._log_lines.append(str(line))

    # ── the cold-start bootstrap, ON ARGV ([CXC-TERM-BOOTSTRAP-ARGV]) ───
    def _bootstrap_argv(self) -> str:
        """The positional prompt argument for this spawn, or "" (resume / `BOOTSTRAP=0`).

        ⛔ NOTHING IS TYPED.  There is no `_arm_bootstrap` and no `_type_bootstrap` in this
        class any more; there is no `threading.Timer`; and `PtyChild` has no `inject_prompt`.
        The prompt the generator produces is written to a FILE and argv carries a short
        instruction naming it by absolute path — the shape David typed by hand on 2026-08-11,
        after which the child read all nine artifacts and ran `lts_selfcheck`.

        Raises `BootstrapRefused` when the manifest chain is exhausted."""
        from ...ptycore import build_bootstrap_argv
        return build_bootstrap_argv(self.intelligence_id, self.resume_session,
                                    role=self.role, manifest_path=self.meta_manifest,
                                    enabled=self.bootstrap, log=self._log)

    # ── the REFUSAL — glass AND bus ([CXC-TERM-MANIFEST-CHAIN]) ─────────
    def _enter_refused(self, refusal: BootstrapRefused) -> None:
        """No fork happened.  Say so on the PLATE, publish it, and stay re-launchable.

        Both surfaces, deliberately: a condition visible only in pixels cannot be
        regression-tested, and one visible only in a log is what made an unoriented instance
        indistinguishable from an oriented one ([SBR-TRUST] one layer up)."""
        self._refusal = refusal
        self._plate = refusal.message()
        self._log(f"[terminal] {refusal.message()}")
        try:
            self.publish(f"intelligence/{self.intelligence_id}/terminal/refusal",
                         refusal.payload())
        except Exception:
            pass                          # bus down: the PLATE still says it
        self.request_redraw()

    @property
    def refused(self) -> Optional[BootstrapRefused]:
        return self._refusal

    # ── the hosted child's OWN telemetry ([CXC-TERM-CHILD-TELEMETRY]) ───
    def _child_jsonl_path(self) -> str:
        """The CHILD's transcript, resolved LATE and cached.

        A cold child MINTS its own session id, so its `.jsonl` does not exist at fork time.
        `resolve_child_jsonl` looks only in the child's own project dir, only at files written
        AFTER our fork, and never at the host's pin — a bounded discovery, not the `ls -t`
        newest-wins guess that leaked a transcript in 2026-07-24.  A monitor wired to the
        HOST's transcript would report the host's context to the child, which is symptom 2 of
        [CXC-TERM-CHILD-ID] rebuilt on purpose."""
        if self._child_jsonl:
            return self._child_jsonl
        from ...ptycore import resolve_child_jsonl
        host_pin = os.environ.get("COMPOSEIO_SESSION_JSONL", "").strip()
        found = resolve_child_jsonl(self.cwd, self._spawned_at,
                                    exclude=(host_pin,) if host_pin else ())
        if found:
            self._child_jsonl = found
        return found

    def _publish_to_child(self, text: str = "", kind: str = "", extra=None) -> None:
        """Deliver a harness message to the CHILD's inbound channel topic.

        The RAW topic (`compose_projects/intelligence/<child-id>/in`) through the bus's foreign-channel seam,
        because that is the address `channel.mjs` subscribes — the same envelope the launcher
        publishes, so the child cannot tell which host it came from."""
        import json as _json
        msg = {"from": "harness", "kind": kind, "text": str(text),
               "ts": int(time.time() * 1000)}
        if extra:
            for k, v in extra.items():
                if k not in msg:
                    msg[k] = v
        topic = f"intelligence/{self.intelligence_id}/in"
        ctx = self._ctx
        bus = getattr(ctx, "bus", None) if ctx is not None else None
        pub = getattr(bus, "publish_foreign", None) if bus is not None else None
        try:
            if pub is not None:
                pub(topic, _json.dumps(msg))
            else:
                self.publish(topic, msg)
        except Exception:
            self._log(f"[terminal] harness message DROPPED (bus down): {kind}")

    def _start_monitor(self, session_pin: str) -> None:
        """Run the launcher's EXISTING `HarnessMonitor` for this child, through the SHARED
        factory ([CXC-TERM-CHILD-TELEMETRY]).  Thresholds 90 and 95 ONLY; the FYI-vs-GUIDANCE
        split `monitor.py` already draws is preserved exactly, because this constructs that
        class rather than reimplementing it.

        IN-PROCESS ([DD-TERM-CHILD-TELEMETRY-HOST], ratified): the thread's lifetime is
        exactly the child's — `start_session` starts it, `stop_session` stops it — so there is
        no orphan to reap and no second flock."""
        from ...ptycore import build_monitor
        try:
            self._monitor = build_monitor(
                intelligence_id=self.intelligence_id,
                publish_in=self._publish_to_child,
                jsonl_path=(lambda: session_pin or self._child_jsonl_path()),
                thresholds=self.thresholds,
                log=self._log,
            )
        except Exception as exc:          # a monitor build failure must never block a session
            self._log(f"[terminal] monitor build failed (session continues): {exc!r}")
            self._monitor = None
            return
        if self._monitor is not None:
            self._monitor_thread = self._monitor.start_thread()

    def _stop_monitor(self) -> None:
        if self._monitor is not None:
            try:
                self._monitor.stop()
            except Exception:
                pass
        self._monitor = None
        self._monitor_thread = None

    @property
    def monitor(self):
        return self._monitor

    def _acquire_singleton(self) -> None:
        """[CXC-TERM-SINGLETON]: the console IS a launcher now, and must take the SAME
        superseding lock — otherwise a human running `transport.sh architect` in a
        terminal silently supersedes and then reaps the console's live child."""
        try:
            import sys
            here = os.path.dirname(os.path.abspath(__file__))
            repo = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(here))))
            pub = os.path.join(repo, "compose_projects", "intelligence", "publisher")
            if pub not in sys.path:
                sys.path.insert(0, pub)
            from singleton import acquire_singleton_superseding
            self._singleton_lock = acquire_singleton_superseding(
                f"intelligence-launcher-{self.intelligence_id}")
        except Exception:
            self._singleton_lock = None   # a lock we cannot take must not block a launch

    def note_superseded(self) -> None:
        """A successor took our singleton.  Unlike a CLI launcher the console cannot exit:
        it stops relaying, tears the child down, and SAYS SO on the glass — a half-dead
        child plus a second relay is the double-injection this lock exists to prevent."""
        self._enter_degraded(
            f"superseded by another launcher for {self.intelligence_id}")
        self.stop_session()

    def _read_loop(self) -> None:
        child = self._child
        while not self._reader_stop.is_set() and child is not None:
            try:
                alive = child.pump(self._on_child_bytes, timeout=0.05)
            except Exception:
                break
            if not alive:
                # EOF on the master.  Two DIFFERENT conditions share this exit
                # ([CXC-TERM-BOUNDARIES] rows 3-5), and reporting them as one is how a
                # normal `exit 0` gets displayed as a fault: flush whatever the child
                # left, then give it a beat to be reaped so `exited <code>` is
                # available.  Only if it is genuinely still running is this a
                # `disconnected`.
                child.drain_final(self._on_child_bytes)
                deadline = time.monotonic() + 0.5
                while time.monotonic() < deadline and not child.child_exited():
                    time.sleep(0.01)
                self._disconnected = child.exit_status is None
                break
            if child.child_exited():
                child.drain_final(self._on_child_bytes)
                break
        self._state = ("disconnected" if self._disconnected else
                       (child.exit_description() if child is not None else "")
                       or self._state)
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "schedule_update"):
            ctx.schedule_update(lambda: self.request_redraw(), target=self)

    def stop_session(self) -> None:
        self._reader_stop.set()
        # THERE IS NO BOOTSTRAP TIMER TO CANCEL ([CXC-TERM-BOOTSTRAP-ARGV]).  The old teardown
        # had to cancel one, because a tab switch a second after spawn could otherwise fire a
        # prompt into a child about to be SIGTERMed — or, worse, into the NEXT one's fd.  With
        # the prompt on argv that whole class of race does not exist.
        self._stop_monitor()
        self._child_jsonl = ""
        # The generated MCP config dies with the session ([CXC-IC-CHANNEL-INSTANCE]): it
        # names an identity, so leaving it on disk leaves a stale identity lying around.
        self._release_mcp_config()
        if self._reader is not None and self._reader.is_alive():
            self._reader.join(timeout=0.5)
        self._reader = None
        if self._child is not None:
            self._child.shutdown()
            self._child = None
        if self._singleton_lock is not None:
            try:
                self._singleton_lock.close()
            except Exception:
                pass
            self._singleton_lock = None
        ctx = self._ctx
        if ctx is not None and hasattr(ctx, "unregister_clocked"):
            ctx.unregister_clocked(self)

    @property
    def child(self):
        return self._child

    @property
    def state(self) -> str:
        return self._state

    # ── the /compact relay ([CXC-TERM-CONVERGE], TERM-C3) ───────────────
    def on_out_message(self, raw: Any) -> None:
        """An `compose_projects/intelligence/<id>/out` message.  A well-formed actuation directive is typed
        into the child through the CORE's three-phase clear/type/submit — the identical
        bytes the CLI launcher sends, because it is the identical code.

        ⛔ THIS SEAM IS UNTOUCHED by the startup deletion ([CXC-TERM-MIDSESSION-SEAM]).  Both
        seams end in bytes on the same fd through the same lock, so a builder deleting "the
        injection path" can plausibly delete both — what distinguishes them is the STATE they
        target.  Mid-session has a reader that is definitely ready, so no timing assumption is
        being made and there is nothing unsound to remove.  `/compact` self-direction is the
        entire reason the harness exists."""
        import json
        from ...ptycore import parse_actuation, parse_context_query
        payload = raw
        if isinstance(raw, (bytes, str)):
            try:
                payload = json.loads(raw)
            except (json.JSONDecodeError, ValueError, TypeError):
                return
        # A context query presses NO key — answered on the `in` topic, before actuation so
        # `{"cmd":"context"}` is never mistaken for a keystroke.  With no monitor the answer
        # is the EXPLICIT "not enabled this session" the launcher already gives; silence would
        # leave the instance waiting on a namespace nothing publishes
        # ([CXC-TERM-CHILD-TELEMETRY]).
        if parse_context_query(payload):
            if self._monitor is not None:
                self._monitor.handle_query()
            else:
                self._publish_to_child(
                    text="harness: the context monitor is not enabled this session.",
                    kind="context-report", extra={"source": "none"})
            return
        actuation = parse_actuation(payload)
        if actuation is None or self._child is None:
            return
        kind, line = actuation
        self._child.inject_line(line, kind=kind)

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if str(namespace).endswith("/out"):
            self.on_out_message(value)

    # ── input ([CXC-TERM-INPUT], [CXC-TERM-PASTE]) ──────────────────────
    def _write(self, data: bytes) -> None:
        """The keyboard path: the core's SINGLE write path, deliberately UNLOGGED."""
        if self._child is not None and data:
            self._child.write_keys(data)

    def on_key(self, key: str, modifiers: Any = None) -> None:
        mod = int(modifiers or 0)
        if key == "Escape" and (mod & _KMOD_CTRL) and (mod & _KMOD_SHIFT):
            # THE RELEASE CHORD, mandatory ([CXC-TERM-INPUT]): with captures_tab true and
            # Escape forwarded to the child, a human could otherwise become unable to
            # leave this widget by keyboard.  It writes ZERO bytes and is rendered in the
            # status strip, so it is discoverable rather than remembered.
            self.release_focus()
            return
        if (mod & _KMOD_GUI) and len(key) == 1:
            # ⌘V — ONE BRANCH ABOVE THE SWALLOW ([CXC-TERM-INPUT]).  The clipboard is routed
            # through the EXISTING `on_text` path, so bracketed-paste wrapping, the length
            # rule and the bottom-stick follow all apply unchanged.  A second write path for
            # pasted text would fork the one thing [CXC-TERM-PASTE] exists to guarantee.
            # An EMPTY or unavailable clipboard writes ZERO bytes: a bracketed-paste wrapper
            # around nothing is a submit the human did not ask for.
            #
            # ⌘C is deliberately NOT here: copy-out needs a SELECTION MODEL, which neither the
            # widget nor the grid has.  It lands WITH selection or not at all.
            if key.lower() == "v" and not (mod & (_KMOD_CTRL | _KMOD_ALT)):
                text = self.clipboard_text()
                if text:
                    self.on_text(text)
                return
            return                        # every other Cmd-chord belongs to the app
        if self._child is None:
            # The START affordance ([CXC-TERM-BOUNDARIES] row 3: an exited session leaves
            # the widget RE-LAUNCHABLE).  A session begins on an EXPLICIT act, never by
            # the widget appearing in a scene.
            if key in ("Return", "Enter", "Keypad Enter"):
                self.start_session()
            return
        if key in ("PageUp", "Page Up", "PageDown", "Page Down") and (mod & _KMOD_SHIFT):
            self.scroll_lines(+5 if key.startswith("Page") and "Up" in key else -5)
            return
        data = key_to_bytes(key, mod)
        if data:
            self._follow_bottom()
            self._write(data)

    def clipboard_text(self) -> str:
        """The system clipboard as text, or "" when there is nothing / SDL is unavailable.

        Lazy import: this module must stay importable in a headless test run, where `window`
        (skia + sdl2) may not be loadable at all.  `_clipboard_reader` is the injection point
        tests use, so the ⌘V path is exercised without a window."""
        if self._clipboard_reader is not None:
            try:
                return str(self._clipboard_reader() or "")
            except Exception:
                return ""
        try:
            from ..window import clipboard_text
            return clipboard_text()
        except Exception:
            return ""

    def on_text(self, text: str) -> None:
        if not text:
            return
        payload = text.encode("utf-8")
        if len(text) > 1 and self._bracketed_paste:
            # [CXC-TERM-PASTE]: a multi-line paste delivered RAW submits on every embedded
            # newline — the documented historical failure of the re-register bootstrap.
            # Five lines of wrapping prevent an already-paid-for defect.
            payload = PASTE_START + payload + PASTE_END
        self._follow_bottom()
        self._write(payload)

    def release_focus(self) -> None:
        ctx = self._ctx
        fm = getattr(ctx, "focus", None) if ctx is not None else None
        if fm is not None and hasattr(fm, "focus"):
            fm.focus(None)
        else:
            self.focused = False
            self.on_blur()
        self.request_redraw()

    # ── scrolling ([CXC-TERM-GRID] bottom-stick) ────────────────────────
    def _follow_bottom(self) -> None:
        if self._scroll_offset:
            self._scroll_offset = 0
            self.request_redraw()

    def scroll_lines(self, delta: int) -> None:
        hist = len(self._screen.history.top) if self._screen is not None else 0
        self._scroll_offset = max(0, min(hist, self._scroll_offset + delta))
        self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        if dy:
            self.scroll_lines(int(dy) if abs(dy) >= 1 else (1 if dy > 0 else -1))

    @property
    def sticking_to_bottom(self) -> bool:
        return self._scroll_offset == 0

    # ── resize ([CXC-TERM-WINSIZE]) ─────────────────────────────────────
    def apply_grid(self, cols: int, rows: int, now: Optional[float] = None) -> bool:
        """Re-issue TIOCSWINSZ + SIGWINCH and resize the emulator IN THE SAME OPERATION,
        so the emulator's dimensions and the child's belief about them can never disagree.
        Debounced: a drag-resize produces a resize per frame, and an un-debounced storm is
        indistinguishable from a hang."""
        now = time.monotonic() if now is None else now
        if (cols, rows) == (self._cols, self._rows):
            return False
        # Never send 0 — a rect below the minimum clamps, it does not kill the child
        # ([CXC-TERM-BOUNDARIES] row 10).
        cols, rows = max(cols, MIN_COLS), max(rows, MIN_ROWS)
        if now - self._last_resize_at < RESIZE_DEBOUNCE_S:
            self._pending_resize = (cols, rows)
            return False
        self._last_resize_at = now
        self._pending_resize = None
        self._cols, self._rows = cols, rows
        # RESIZE an existing emulator only.  Creating one here would replace the idle
        # plate with an empty grid the moment the widget is laid out — a session that
        # looks live when nothing was ever spawned.
        if self._screen is not None:
            self._ensure_screen(cols, rows)
        if self._child is not None:
            self._child.set_term_size(rows, cols)
            self._child.apply_winsize(rows, cols)
        return True

    def flush_pending_resize(self, now: Optional[float] = None) -> bool:
        """The final issue after motion stops."""
        if self._pending_resize is None:
            return False
        now = time.monotonic() if now is None else now
        if now - self._last_resize_at < RESIZE_DEBOUNCE_S:
            return False
        cols, rows = self._pending_resize
        self._pending_resize = None
        self._cols = self._rows = None       # force apply_grid past its equality guard
        return self.apply_grid(cols, rows, now=now)

    # ── clock ───────────────────────────────────────────────────────────
    def on_clock_tick(self, now: float) -> bool:
        self.flush_pending_resize()
        if self._child is None:
            return False
        if ct.reduced_motion(self._ctx):
            self._cursor_on = True
            return False
        phase = int(now * CURSOR_BLINK_HZ * 2) % 2 == 0
        if phase != self._cursor_on:
            self._cursor_on = phase
            return True
        return True

    def clock_damage_rect(self) -> Optional[Rect]:
        """Blink the CURSOR CELL, not the widget: the sliver, not the card."""
        r = self.arranged_rect
        if r is None or self._screen is None or self._cell_w is None:
            return r
        cx = r.x + self.pad + self._screen.cursor.x * self._cell_w
        cy = (r.y + self.pad + (BANNER_H if self._degraded else 0.0)
              + self._screen.cursor.y * self.cell_h)
        return Rect(cx, cy, self._cell_w + 1, self.cell_h)

    # ── render ──────────────────────────────────────────────────────────
    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def status_line(self) -> str:
        """[CXC-TERM-DEGRADE]: always the geometry, the child's state and the release
        chord; the unsupported counter appends itself when it is non-zero, and mouse
        tracking is a NAMED gap rather than a silent one."""
        size = self.grid_size()
        geom = f"{size[0]}x{size[1]}" if size else "--x--"
        bits = [geom, self._state, f"release {RELEASE_CHORD_LABEL}"]
        if self._refusal is not None:
            bits.insert(1, f"REFUSED ({self._refusal.reason})")
        elif self._child is not None and not self.bootstrap_on() and not self.resume_session:
            bits.insert(1, "unoriented (BOOTSTRAP=0)")
        total = sum(self._counts.values())
        if total:
            top = self._counts.most_common(1)[0][0]
            bits.append(f"{total} unsupported ({top})")
        if self._mouse_tracking:
            bits.append("mouse tracking not forwarded")
        if self._scroll_offset:
            bits.append(f"scrolled +{self._scroll_offset}")
        return " · ".join(b for b in bits if b)

    def visible_rows(self) -> List[Any]:
        """The rows to draw: the live buffer, or a window into scrollback while the human
        has scrolled up ([CXC-TERM-GRID] bottom-stick)."""
        scr = self._screen
        if scr is None:
            return []
        live = [scr.buffer[y] for y in range(scr.lines)]
        if not self._scroll_offset:
            return live
        hist = list(scr.history.top)
        take = min(self._scroll_offset, len(hist))
        if not take:
            return live
        return (hist[len(hist) - take:] + live)[:scr.lines]

    def _runs(self, row, cols: int):
        """Group a row into (text, fg, bg, bold) runs so one draw_text covers a span."""
        out, cur, start = [], None, 0
        text = []
        for x in range(cols):
            ch = row[x]
            key = (ch.fg, ch.bg, ch.bold, ch.reverse)
            if cur is None:
                cur, start = key, x
            elif key != cur:
                out.append(("".join(text), cur, start))
                text, cur, start = [], key, x
            text.append(ch.data or " ")
        if cur is not None:
            out.append(("".join(text), cur, start))
        return out

    def _color(self, name: str, t, default) -> Tuple[int, int, int, int]:
        if not name or name == "default":
            return default
        table = _ansi_table(t)
        if name in table:
            return table[name]
        if len(name) == 6:
            try:
                return _rgba(name)
            except ValueError:
                return default
        return default

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._ensure_metrics(canvas)
        canvas.save()
        # Default background = the theme's panel ground, NOT black ([CXC-TERM-GRID]).
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_INNER)
        canvas.set_stroke_color(*(t.crit_line if self._degraded else t.line))
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(rect.x, rect.y, rect.w, rect.h, ct.RADIUS_INNER)

        size = self.grid_size(rect)
        cols, rows = size if size else (0, 0)
        if self._fits(size) and (cols, rows) != (self._cols, self._rows):
            self.apply_grid(cols, rows)

        y = rect.y + self.pad
        if self._degraded:
            canvas.set_fill_color(*t.crit)
            canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "bold")
            canvas.draw_text("DEGRADED — " + self._degrade_reason,
                             rect.x + self.pad, y + BANNER_H - 5, "left")
            y += BANNER_H

        body_h = rect.h - 2 * self.pad - self._chrome_h()
        if not self._fits(size):
            self._draw_plate(canvas, rect, t,
                             f"TERMINAL TOO SMALL — {cols}x{rows}, "
                             f"needs {MIN_COLS}x{MIN_ROWS}")
        elif self._screen is None:
            self._draw_plate(canvas, rect, t, self._plate or self._idle_plate())
        else:
            canvas.save()
            canvas.clip_rect(rect.x + self.pad, y, rect.w - 2 * self.pad, max(body_h, 0))
            canvas.set_font(ct.FONT_MONO, self.font_px, "regular")
            if self._degraded:
                canvas.set_opacity(0.55)      # dimmed: no claim that this render is right
            self._draw_grid(canvas, rect, t, y, cols, rows)
            if self._degraded:
                canvas.set_opacity(1.0)
            canvas.restore()

        canvas.set_font(ct.FONT_MONO, ct.LABEL_MONO, "regular")
        canvas.set_fill_color(*(t.crit if self._degraded else t.faint))
        canvas.draw_text(self.status_line(), rect.x + self.pad,
                         rect.y + rect.h - self.pad + 2, "left")
        canvas.restore()

    def _idle_plate(self) -> str:
        # THE REFUSAL PLATE ([CXC-TERM-MANIFEST-CHAIN]) outranks everything: it names the id,
        # the role and EVERY path tried, in order, so the human can act on it without a log.
        if self._refusal is not None:
            return self._refusal.message()
        why = self.refusal()
        if why:
            return why
        if not self.bootstrap_on():
            return (f"no session — {self.intelligence_id} (role {self.role}) · "
                    f"unoriented (BOOTSTRAP=0) · click, then press Return to start")
        verb = "start a new session" if self._state.startswith("exited") else "start"
        return (f"no session — {self.intelligence_id} (role {self.role}) · "
                f"target {self.target_mode} · click, then press Return to {verb}")

    def bootstrap_on(self) -> bool:
        """Whether this spawn would be ORIENTED.  `BOOTSTRAP=0` is the ONE way to get a
        deliberately unoriented child, and the strip says so out loud — a deliberate choice
        must still be VISIBLE ([CXC-TERM-MANIFEST-CHAIN] default)."""
        from ...ptycore import bootstrap_enabled
        return bool(self.resume_session) or bootstrap_enabled(self.bootstrap)

    def _draw_plate(self, canvas: Canvas, rect: Rect, t, message: str) -> None:
        canvas.set_font(ct.FONT_MONO, ct.TELEMETRY, "regular")
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(message, rect.x + rect.w / 2,
                         rect.y + rect.h / 2, "center")

    def _draw_grid(self, canvas: Canvas, rect: Rect, t, top: float,
                   cols: int, rows: int) -> None:
        scr = self._screen
        x0 = rect.x + self.pad
        ink, ground = t.ink, t.panel3
        for ry, row in enumerate(self.visible_rows()[:rows]):
            ly = top + ry * self.cell_h
            for text, key, sx in self._runs(row, min(cols, scr.columns)):
                fg_name, bg_name, bold, reverse = key
                fg = self._color(fg_name, t, ink)
                bg = self._color(bg_name, t, ground)
                if reverse:
                    fg, bg = bg, fg
                if bg != ground:
                    canvas.set_fill_color(*bg)
                    canvas.fill_rect(x0 + sx * self._cell_w, ly,
                                     len(text) * self._cell_w, self.cell_h)
                if text.strip():
                    canvas.set_font(ct.FONT_MONO, self.font_px,
                                    "bold" if bold else "regular")
                    canvas.set_fill_color(*fg)
                    canvas.draw_text(text, x0 + sx * self._cell_w,
                                     ly + self._ascent, "left")
        # The cursor comes FROM THE EMULATOR (position and hidden), so `?25l` is honoured
        # for free and there is never a second cursor model ([CXC-TERM-GRID]).
        if (not scr.cursor.hidden and not self._scroll_offset
                and (self._cursor_on or not self.focused)
                and 0 <= scr.cursor.y < rows):
            canvas.set_fill_color(*(t.accent if self.focused else t.faint))
            canvas.fill_rect(x0 + scr.cursor.x * self._cell_w,
                             top + scr.cursor.y * self.cell_h,
                             self._cell_w, self.cell_h)
