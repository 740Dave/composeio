#!/usr/bin/env python3
"""The escape-sequence census — [SPEC:CXC-TERM] · [CXC-TERM-CENSUS], build task #1.

This is a MEASUREMENT TOOL, not a probe. It spawns a real `claude` under a PTY in each
candidate profile, drives it to STEADY STATE, and reports what it actually emits, so that
[DD-TERM-TARGET-MODE] (which mode V1 targets) and [CXC-TERM-SEQTABLE] (what V1 supports)
are DERIVED FROM EVIDENCE rather than from the help text.

Two prior attempts were VOID ([CXC-TERM-CENSUS-PRIOR]). Both of their named defects are
fixed HERE, structurally:

  A. THE TRUST GATE. A child sitting on "Is this a project you created or one you trust?"
     emits a gate, not a TUI — identically in every mode. `_clear_gates` recognizes the
     gate, answers it with a LOGGED keystroke, and ABORTS on any gate it does not
     recognize rather than guessing. And the round trip is verified OUT OF BAND: the
     session transcript `.jsonl` must contain an assistant message ([CXC-TERM-FACTS]
     fact 1 — the transcript is written in all modes), which no amount of TUI-byte
     pattern-matching could establish.
  B. THE 0x0 PTY. Nothing in this repo set a window size until 2026-08-10, and a TUI that
     cannot learn its dimensions degrades to minimal line output in EVERY mode. The fix
     landed in `launcher.py` ([CXC-TERM-WINSIZE]); this tool spawns THROUGH that same
     `Launcher.start_child`, so there is exactly one pty.fork and one winsize
     implementation in play — no second copy to drift.
  C. `--bare`. Not used. It changes the auth model and would confound the measurement.

DISCRIMINATION IS A GATE, NOT A FOOTNOTE. If two profiles come out byte-identical or
class-identical, the run is a PROBE FAILURE: the tool refuses to print a census and says
why. That is [CXC-TERM-CENSUS]'s third validity condition and it is the only defence
against a third void measurement.

CONFIGURATION FIDELITY ([CXC-TERM-CENSUS-CONFIG-FIDELITY], 2026-08-11). The five profiles
below are CLEAN-ROOM: they exclude MCP, the dev-channels flag and the permission prompts. That
is right for what they measure (redraw cost, sequence classes at steady state and on resize)
and WRONG for anything about STARTUP — a startup engineered to contain no dialogs is a
measurement of a system nobody runs. `ptycore` cited a startup figure from these profiles to
claim `?2004h` was already set, and the first live spawn refuted it. The `as-deployed` profile
exists for exactly that: real `.mcp.json`, dev channels, prompts NOT suppressed. NO startup
claim may cite a clean-room profile.

SAFETY — this spawns real `claude` processes, alongside a live Intelligence Console:
  * NO ComposeBus, in the five clean-room profiles. `--strict-mcp-config --mcp-config
    '{"mcpServers":{}}'` (no MCP servers at all) and NO
    `--dangerously-load-development-channels`, so the child has no channel and cannot publish
    `self/*` or `channel/*`. The `as-deployed` profile is the ONE exception — it joins the bus
    by design, is NOT in the default profile set, and joins it under a throwaway id pinned in
    a GENERATED per-instance MCP config ([CXC-IC-CHANNEL-INSTANCE]) so it can never announce
    itself as the live Architect. The `INTELLIGENCE_ID` env pin alone would NOT have achieved
    that: an `env` block in `.mcp.json` overrides the inherited environment.
  * NO HOOKS, NO STATUSLINE. `--setting-sources local` in a scratch cwd loads neither the
    user's settings nor the project's, so the SessionStart/SessionEnd hooks — which
    rotate logs and delete the LIVE session's canary — never run. Verified: the hook log
    does not grow during a census.
  * NOT the architect. A throwaway `--session-id` and a scratch cwd, so the singleton
    flock is never taken and `self/<id>/*` is never touched.
  * ONLY OUR OWN CHILDREN ARE REAPED, by the pid this tool forked. Nothing is ever
    matched by name; no `pkill`. Each profile has a hard deadline.

Run:
    python3 compose_projects/intelligence/tools/terminal_census.py                 # all four profiles
    python3 compose_projects/intelligence/tools/terminal_census.py --profiles default,ax
    python3 compose_projects/intelligence/tools/terminal_census.py --selftest      # no claude, no network
"""
from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import signal
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
# _HERE is <repo>/compose_projects/intelligence/tools — THREE levels down, not two.
# 2026-08-17: intelligence/ moved under compose_projects/, and a depth count carries no
# path text, so the stale `dirname(dirname(...))` kept resolving and silently named
# <repo>/compose_projects as the repo root: the as-deployed profile then looked for
# `<repo>/compose_projects/.mcp.json` and REFUSED TO SPAWN.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (os.path.join(_REPO, "compose_projects", "intelligence", "launcher"), os.path.join(_REPO, "python")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from composeio.term_sequences import (  # noqa: E402
    FEATURES, K_CSI, K_DCS, K_ESC, K_OSC, K_STR, K_TEXT, Token, classify, shape, tokenize,
)
from composeio.ptycore import (  # noqa: E402  (the as-deployed profile's flag + its config)
    CHANNEL_FLAGS, write_child_mcp_config,
)
from launcher import Launcher, project_dir_key  # noqa: E402

# ── Measurement constants (every one stated, per [CXC-TERM-CENSUS]) ─────────────────────
CENSUS_STEADY_S = 20.0        # the steady-state window, from the first byte after submit
CENSUS_RESIZE_S = 5.0         # per resize burst; two resizes are issued
CENSUS_STARTUP_MAX_S = 45.0   # give up waiting for the prompt after this
CENSUS_QUIET_S = 1.5          # "output has settled" = this long with no bytes
GATE_ROUNDS = 6               # how many recognized gates we will answer before aborting
PROFILE_DEADLINE_S = 150.0    # hard per-profile watchdog — nothing is ever orphaned
BURST_GAP_S = 0.05            # reads separated by more than this start a new burst

CENSUS_ROWS, CENSUS_COLS = 40, 120        # the geometry the child is given
RESIZE_ROWS, RESIZE_COLS = 30, 90         # the geometry it is resized to, mid-capture

# A prompt that reliably produces a multi-second STREAMING response and uses no tools, so
# the measurement is of the TUI and not of tool-call rendering. Committed with the tool so
# the census is repeatable ([CXC-TERM-CENSUS] default).
FIXTURE_PROMPT = (
    "Answer from your own knowledge only. Do not use any tools, do not read any files, "
    "and do not run any commands. In roughly 250 words of plain prose — no lists, no "
    "headings, no code blocks — explain what a carriage return did on a mechanical "
    "teleprinter and why terminals still distinguish it from a line feed."
)

# Gates we will answer, and the EXACT key we send. Anything not on this list aborts the
# run with the screen text, rather than being guessed at ([CXC-TERM-DIALOGS]'s lesson:
# keystroke injection into startup dialogs is how the last attempt hung).
KNOWN_GATES: tuple[tuple[str, str, str], ...] = (
    ("Is this a project you created or one you trust",
     "\r", "trust gate — option 1 'Yes, I trust this folder' is preselected"),
    ("Do you trust the files in this folder",
     "\r", "legacy trust gate — option 1 preselected"),
)
PROMPT_MARKERS = ("for shortcuts", "Bypassing Permissions", "esc to interrupt")


PROFILES: dict[str, dict] = {
    "fullscreen": {
        "label": "tui: fullscreen",
        "settings": {"tui": "fullscreen"},
        "args": [],
        "env": {},
    },
    "default": {
        "label": "tui: default",
        "settings": {"tui": "default"},
        "args": [],
        "env": {},
    },
    "ax": {
        "label": "--ax-screen-reader",
        "settings": {},
        "args": ["--ax-screen-reader"],
        "env": {"CLAUDE_AX_STARTUP_QUIET_MS": "0"},
    },
    "ax+default": {
        "label": "--ax-screen-reader + tui: default",
        "settings": {"tui": "default"},
        "args": ["--ax-screen-reader"],
        "env": {"CLAUDE_AX_STARTUP_QUIET_MS": "0"},
    },
    # A FIFTH profile the spec does not name, added because the four it names cannot
    # answer the question [CXC-TERM-AXMODE] actually asks. Every profile here runs with
    # `--setting-sources local`, so the `ax` profile inherits the BUILT-IN tui default, not
    # David's global `fullscreen`. `ax` ≈ `ax+default` therefore only shows that the
    # built-in default IS `default` — it does NOT show that the flag beats `fullscreen`.
    # This profile is the decisive one: if it looks like `ax`, the FLAG DOMINATES the
    # setting and [CXC-TERM-PIN]'s whole settings-composition requirement disappears; if it
    # looks like `fullscreen`, the widget must pin `tui` no matter which target is chosen.
    "ax+fullscreen": {
        "label": "--ax-screen-reader + tui: fullscreen",
        "settings": {"tui": "fullscreen"},
        "args": ["--ax-screen-reader"],
        "env": {"CLAUDE_AX_STARTUP_QUIET_MS": "0"},
    },
    # ── THE AS-DEPLOYED PROFILE ([CXC-TERM-CENSUS-CONFIG-FIDELITY], added 2026-08-11) ──
    # The five profiles above all ran `--strict-mcp-config --mcp-config '{"mcpServers":{}}'`
    # with `skipDangerousModePermissionPrompt: true` and no dev-channels flag, and the report
    # records `gates answered: none` for every one of them.  Those exclusions were deliberate
    # and individually well-reasoned — see the SAFETY block at the top of this file — and they
    # remain the clean-room comparison that makes the sequence sets DISCRIMINABLE.  The defect
    # was drawing a STARTUP conclusion from a startup that had been engineered to have no
    # dialogs in it: `ptycore.py` cited `bracketed_paste` in the startup segment to claim a
    # child has `?2004h` on "by the time anything is typed", and on the first live spawn the
    # TUI had not started, nothing had set `?2004h`, and `^[[200~` echoed as literal text into
    # a blocking `Enter y/n:` read.  A measurement is admissible about a system only over the
    # configuration that system is actually RUN in; excluding the awkward parts produces a
    # clean measurement of a DIFFERENT system.
    #
    # This profile launches what the WIDGET launches: the real `.mcp.json` (channel server
    # included), `--dangerously-load-development-channels`, and permission prompts NOT
    # suppressed — so the STARTUP segment contains the dialogs the human actually answers.
    #
    # ⚠️ IT JOINS THE BUS under a THROWAWAY id — and the id comes from the GENERATED
    # PER-INSTANCE MCP CONFIG, not from this `env` block ([CXC-IC-CHANNEL-INSTANCE]).  The env
    # pin below is kept (it is what `write_child_mcp_config` is handed, and it names the
    # throwaway in one place) but on its own it is INERT: an `env` block in `.mcp.json`
    # OVERRIDES the inherited environment, so the previous as-deployed argv — the real
    # `.mcp.json`, unmodified — would have announced this census child as the LIVE ARCHITECT
    # and displaced it, exactly the outage the pin was written to prevent.  Never remove
    # either half.
    "as-deployed": {
        "label": "AS DEPLOYED (real .mcp.json, dev channels, prompts NOT suppressed)",
        "settings": {},
        "args": [],
        "env": {"CLAUDE_AX_STARTUP_QUIET_MS": "0",
                "INTELLIGENCE_ID": "census-as-deployed"},
        "as_deployed": True,
    },
}


def profile_argv(key: str, session_id: str, repo: str = _REPO) -> list:
    """The argv tail for `key`.  PUBLIC so the CONFIGURATION FIDELITY of a profile is
    assertable without spawning anything ([CXC-TERM-CENSUS-CONFIG-FIDELITY] verify-machine:
    the as-deployed profile carries the real server set with the child's own channel identity
    and does NOT carry an empty `mcpServers`, `--dangerously-skip-permissions` or
    `skipDangerousModePermissionPrompt`).

    Side-effect-free EXCEPT for the as-deployed profile, which WRITES its per-instance MCP
    config — deliberately, because "the same startup the widget runs" now includes the
    generated config, and a profile that measured the repo's `.mcp.json` instead would be
    measuring a startup nobody runs.  That is the fidelity failure this profile exists to
    end, so it may not be reintroduced to keep this function pure."""
    prof = PROFILES[key]
    settings = dict(prof["settings"])
    if prof.get("as_deployed"):
        # NO permission suppression: the dialogs ARE the measurement.  `--setting-sources
        # local` is KEPT — the hook/statusline isolation it buys is a SAFETY property, not a
        # fidelity exclusion (this tool must not rotate the live session's logs or delete its
        # canary), and nothing about it removes a dialog.
        #
        # The MCP flags are the WIDGET'S, byte for byte ([CXC-IC-CHANNEL-INSTANCE]): the real
        # server set, derived from `<repo>/.mcp.json`, with ONE key rebound to the throwaway
        # id, plus `--strict-mcp-config` so the child cannot also load the repo's config and
        # re-acquire the Architect's channel.  `--strict-mcp-config` here therefore means the
        # OPPOSITE of what it means in the clean-room profiles below (there: no servers at
        # all; here: exactly the deployed servers and no others).
        mcp_config = write_child_mcp_config(prof["env"]["INTELLIGENCE_ID"],
                                            os.path.join(repo, ".mcp.json"))
        args = [
            "--setting-sources", "local",
            "--strict-mcp-config",
            "--mcp-config", mcp_config,
            "--session-id", session_id,
        ] + list(CHANNEL_FLAGS) + list(prof["args"])
        if settings:
            args = ["--settings", json.dumps(settings)] + args
        return args
    settings["skipDangerousModePermissionPrompt"] = True
    return [
        "--setting-sources", "local",          # NO user settings -> no hooks, no statusline
        "--settings", json.dumps(settings),
        "--strict-mcp-config",                 # NO MCP servers at all -> not on ComposeBus
        "--mcp-config", json.dumps({"mcpServers": {}}),
        "--dangerously-skip-permissions",
        "--session-id", session_id,
    ] + list(prof["args"])


class CensusAbort(RuntimeError):
    """A validity condition failed. NO profile is produced — a gate-only or zero-byte
    capture is never reported as a finding ([CXC-TERM-CENSUS])."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(msg: str) -> None:
    sys.stderr.write(f"[census] {_now()} {msg}\n")
    sys.stderr.flush()


# ── Capture ─────────────────────────────────────────────────
class Capture:
    """A timestamped byte log, sliced into named SEGMENTS. Segments are what get reported;
    the raw stream is kept whole so a later question can be re-asked of the same bytes."""

    def __init__(self) -> None:
        self.chunks: list[tuple[float, bytes]] = []
        self.marks: dict[str, float] = {}

    def add(self, t: float, data: bytes) -> None:
        self.chunks.append((t, data))

    def mark(self, name: str) -> None:
        self.marks[name] = time.time()

    def segment(self, t0: float, t1: float) -> list[tuple[float, bytes]]:
        return [(t, d) for (t, d) in self.chunks if t0 <= t < t1]

    @property
    def total_bytes(self) -> int:
        return sum(len(d) for _t, d in self.chunks)


def _drain(fd: int, cap: Capture, until: float, quiet_s: float | None = None) -> bytes:
    """Read `fd` until `until`, or until `quiet_s` elapses with no bytes. Returns
    everything read in this call."""
    got = bytearray()
    last = time.time()
    while time.time() < until:
        try:
            r, _, _ = select.select([fd], [], [], 0.05)
        except (OSError, ValueError):
            break
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            cap.add(time.time(), data)
            got += data
            last = time.time()
        elif quiet_s is not None and (time.time() - last) >= quiet_s:
            break
    return bytes(got)


def plain_text(data: bytes) -> str:
    """The printable payload with every escape sequence removed — what the human would
    read. Gate detection runs on THIS, never on the raw bytes."""
    toks, _tail = tokenize(data)
    return "".join(t.raw.decode("utf-8", "replace") for t in toks if t.kind == K_TEXT)


_SQUASH = re.compile(r"[^a-z0-9]+")


def squash(text: str) -> str:
    """Lowercase, alphanumerics only.

    THIS IS LOAD-BEARING, and it is a real finding about the TUI: Claude Code does not
    emit spaces between words — it emits each word followed by an absolute COLUMN move
    (`Quick\\x1b[8Gsafety\\x1b[15Gcheck:`). Strip the escapes and the words run together,
    so a plain `"is this a project you trust" in screen` test NEVER matches and the tool
    walks straight past the gate it exists to detect. Matching on the squashed form is
    what makes gate detection work at all."""
    return _SQUASH.sub("", text.lower())


# ── One profile ─────────────────────────────────────────────
def run_profile(key: str, cwd: str, claude_bin: str, keep_raw: str | None = None) -> dict:
    prof = PROFILES[key]
    session_id = str(uuid.uuid4())
    # The clean-room profiles suppress the dangerous-mode confirm (with `--setting-sources
    # local` the user's own setting is not loaded, and it is a gate we refuse to guess at);
    # the AS-DEPLOYED profile deliberately does not, because those dialogs are what it exists
    # to measure ([CXC-TERM-CENSUS-CONFIG-FIDELITY]).
    args = profile_argv(key, session_id)

    for k, v in prof["env"].items():
        os.environ[k] = v
    # THE TRANSCRIPT WITNESS ONLY EXISTS IF THE TRANSCRIPT IS WRITTEN. When this tool is
    # itself run from inside a Claude Code session, `CLAUDE_CODE_CHILD_SESSION` is in the
    # environment and is INHERITED by the census child, which then reports "Transcript
    # saving is off" and writes no `.jsonl` — the witness fails even though the session
    # worked perfectly. Drop the marker and force persistence, so the child is a session in
    # its own right and [CXC-TERM-FACTS] fact 1 holds for it.
    os.environ.pop("CLAUDE_CODE_CHILD_SESSION", None)
    os.environ["CLAUDE_CODE_FORCE_SESSION_PERSISTENCE"] = "1"
    os.chdir(cwd)

    lch = Launcher(
        intelligence_id=f"census-{key}",       # a THROWAWAY id; run() is never called, so
        claude_bin=claude_bin,                 # no singleton flock and no MQTT connection
        claude_args=args,
        mqtt_client=None,
        term_size=(CENSUS_ROWS, CENSUS_COLS),
        log=lambda line: None,
    )
    cap = Capture()
    deadline = time.time() + PROFILE_DEADLINE_S
    gates_answered: list[str] = []
    _log(f"profile {key!r} ({prof['label']}) — spawning, session {session_id}")
    lch.start_child()
    fd = lch.master_fd
    try:
        # ── 1. STARTUP: settle, clearing any gate we recognize ──────────────────
        round_bytes = b""
        for _round in range(GATE_ROUNDS + 1):
            # Scan ONLY what THIS round produced. Scanning the whole accumulated capture
            # would keep re-finding a gate that has already been answered and scrolled
            # away — it would answer it forever and then abort as "unresolved".
            round_bytes = _drain(fd, cap, min(time.time() + CENSUS_STARTUP_MAX_S, deadline),
                                 quiet_s=CENSUS_QUIET_S)
            screen = squash(plain_text(round_bytes))
            hit = next(((m, keys, why) for (m, keys, why) in KNOWN_GATES
                        if squash(m) in screen), None)
            if hit is None:
                break
            _log(f"  gate: {hit[2]} -> sending {hit[1]!r}")
            gates_answered.append(hit[2])
            lch.inject(hit[1], kind="census-gate")
            time.sleep(0.8)
        else:
            raise CensusAbort(f"{key}: still on a gate after {GATE_ROUNDS} answers")
        if cap.total_bytes < 200:
            raise CensusAbort(f"{key}: only {cap.total_bytes} startup bytes — the child "
                              f"never rendered anything")
        if squash("Transcript saving is off") in squash(plain_text(round_bytes)):
            raise CensusAbort(f"{key}: the child reports 'Transcript saving is off' — the "
                              f"out-of-band witness cannot exist. Clear CLAUDE_CODE_CHILD_SESSION.")
        cap.mark("startup_end")
        seen = [m for m in PROMPT_MARKERS if squash(m) in squash(plain_text(round_bytes))]
        _log(f"  startup settled: {cap.total_bytes} bytes"
             + (f" (prompt markers: {','.join(seen)})" if seen else
                " (no known prompt marker — the transcript is the witness)"))

        # ── 2. STEADY STATE: submit the fixture prompt, capture the response ─────
        lch.compact_phase_delay = 0.25
        lch.inject_line(FIXTURE_PROMPT, kind="census-prompt")
        cap.mark("steady_start")
        _drain(fd, cap, min(time.time() + CENSUS_STEADY_S, deadline))
        cap.mark("steady_end")

        # ── 3. RESIZE: the other expensive behaviour ([CXC-TERM-CENSUS] cond. 3) ─
        cap.mark("resize_start")
        lch.apply_winsize(RESIZE_ROWS, RESIZE_COLS)
        _drain(fd, cap, min(time.time() + CENSUS_RESIZE_S, deadline))
        lch.apply_winsize(CENSUS_ROWS, CENSUS_COLS)
        _drain(fd, cap, min(time.time() + CENSUS_RESIZE_S, deadline))
        cap.mark("resize_end")
    finally:
        _reap(lch)
        # Keep the raw capture even on an ABORT — a void run's bytes are the evidence for
        # WHY it was void, and throwing them away is how the first two attempts ended up
        # with a verdict and no diagnosis.
        if keep_raw:
            with open(os.path.join(keep_raw, f"{key}.raw"), "wb") as fh:
                fh.write(b"".join(d for _t, d in cap.chunks))
            # The TIMESTAMPED capture + the segment marks. This is what makes `--reanalyse`
            # faithful rather than approximate: a question asked later (a feature added to
            # the table, a different burst threshold) is re-answered against the SAME bytes
            # AND the same timing, with no second measurement and no second API spend.
            with open(os.path.join(keep_raw, f"{key}.capture.json"), "w", encoding="utf-8") as fh:
                json.dump({"marks": cap.marks,
                           "chunks": [[t, d.hex()] for t, d in cap.chunks]}, fh)

    # ── 4. The OUT-OF-BAND witness: did the session actually answer? ─────────────
    transcript = os.path.join(os.path.expanduser("~"), ".claude", "projects",
                              project_dir_key(cwd), f"{session_id}.jsonl")
    assistant_turns, transcript_found = _transcript_witness(transcript)
    if not transcript_found:
        raise CensusAbort(f"{key}: no session transcript at {transcript} — the child never "
                          f"reached a usable session")
    if assistant_turns < 1:
        raise CensusAbort(f"{key}: transcript has {assistant_turns} assistant turns — the "
                          f"prompt was never answered, so this is a gate capture, not a census")
    _log(f"  transcript witness OK: {assistant_turns} assistant turn(s)")

    return {
        "profile": key,
        "label": prof["label"],
        "session_id": session_id,
        "argv_tail": args,
        "env": prof["env"],
        "gates_answered": gates_answered,
        "assistant_turns": assistant_turns,
        "geometry": {"spawn": [CENSUS_ROWS, CENSUS_COLS], "resized": [RESIZE_ROWS, RESIZE_COLS]},
        "segments": {
            "startup": analyse(cap.segment(0, cap.marks["startup_end"])),
            "steady": analyse(cap.segment(cap.marks["steady_start"], cap.marks["steady_end"])),
            "resize": analyse(cap.segment(cap.marks["resize_start"], cap.marks["resize_end"])),
        },
    }


def _reap(lch: Launcher) -> None:
    """Terminate ONLY the child this tool forked, by pid. Never by name, never a pkill —
    a live Intelligence Console and a live architect are running alongside this."""
    pid = lch.pid
    if pid is None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, OSError):
            break
        for _ in range(20):
            try:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    _log(f"  reaped census child pid={pid} with {sig.name}")
                    lch.pid = None
                    return
            except ChildProcessError:
                lch.pid = None
                return
            time.sleep(0.1)
    lch.pid = None


def _transcript_witness(path: str) -> tuple[int, bool]:
    """(assistant turn count, transcript exists). The independent proof that the session
    was USABLE — no TUI-byte pattern can establish this ([CXC-TERM-FACTS] fact 1)."""
    if not os.path.isfile(path):
        return 0, False
    turns = 0
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("type") == "assistant":
                turns += 1
    return turns, True


# ── Analysis ────────────────────────────────────────────────
def analyse(chunks: list[tuple[float, bytes]]) -> dict:
    """Everything [CXC-TERM-CENSUS] says to record, for one segment."""
    data = b"".join(d for _t, d in chunks)
    toks: list[Token] = []
    tail = b""
    for _t, chunk in chunks:
        got, tail = tokenize(tail + chunk)
        toks.extend(got)
    if tail:                       # a genuinely truncated trailing sequence
        got, _ = tokenize(tail)
        toks.extend(got)

    kinds = Counter(t.kind for t in toks)
    shapes = Counter(shape(t) for t in toks if t.kind not in (K_TEXT, "c0"))
    features = Counter()
    for t in toks:
        for name in classify(t):
            features[name] += 1
    text_bytes = sum(len(t.raw) for t in toks if t.kind == K_TEXT)
    esc_bytes = sum(len(t.raw) for t in toks
                    if t.kind in (K_CSI, K_OSC, K_DCS, K_ESC, K_STR))

    # Burst structure — the input to the coalescing cadence ([CXC-TERM-DAMAGE]).
    bursts: list[int] = []
    gaps: list[float] = []
    cur = 0
    prev_t = None
    for t, chunk in chunks:
        if prev_t is not None and (t - prev_t) > BURST_GAP_S:
            bursts.append(cur)
            gaps.append(t - prev_t)
            cur = 0
        cur += len(chunk)
        prev_t = t
    if cur:
        bursts.append(cur)

    return {
        "bytes": len(data),
        "text_bytes": text_bytes,
        "escape_bytes": esc_bytes,
        "escape_ratio": round(esc_bytes / len(data), 4) if data else 0.0,
        "reads": len(chunks),
        "token_kinds": dict(kinds),
        "distinct_shapes": len(shapes),
        "shapes": dict(shapes.most_common()),
        "features": dict(features.most_common()),
        "bursts": {"count": len(bursts), "max_bytes": max(bursts) if bursts else 0,
                   "mean_bytes": round(sum(bursts) / len(bursts), 1) if bursts else 0.0,
                   "max_gap_s": round(max(gaps), 3) if gaps else 0.0,
                   "mean_gap_s": round(sum(gaps) / len(gaps), 3) if gaps else 0.0},
    }


# ── Discrimination gate ([CXC-TERM-CENSUS] validity condition 3) ────────────────────────
def discrimination(results: list[dict]) -> dict:
    """Do the profiles differ MEASURABLY? Byte-identical or class-identical output across
    modes is a PROBE FAILURE, not a finding."""
    pairs = []
    ok = True
    for i in range(len(results)):
        for j in range(i + 1, len(results)):
            a, b = results[i], results[j]
            sa, sb = a["segments"]["steady"], b["segments"]["steady"]
            same_bytes = sa["bytes"] == sb["bytes"]
            same_shapes = set(sa["shapes"]) == set(sb["shapes"])
            same_counts = sa["shapes"] == sb["shapes"]
            identical = same_bytes and same_counts
            class_identical = same_shapes and same_bytes
            if identical or class_identical:
                ok = False
            pairs.append({
                "a": a["profile"], "b": b["profile"],
                "bytes_a": sa["bytes"], "bytes_b": sb["bytes"],
                "shape_jaccard": round(_jaccard(set(sa["shapes"]), set(sb["shapes"])), 3),
                "only_a": sorted(set(sa["shapes"]) - set(sb["shapes"])),
                "only_b": sorted(set(sb["shapes"]) - set(sa["shapes"])),
                "verdict": "IDENTICAL — PROBE FAILURE" if (identical or class_identical) else "differs",
            })
    return {"ok": ok, "pairs": pairs}


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if (a or b) else 1.0


# ── pyte coverage — measured, not asserted ──────────────────
def pyte_coverage(shapes: list[str], samples: dict[str, bytes]) -> dict:
    """For each observed sequence, feed it to a REAL `pyte` screen and observe whether
    anything changes. Three outcomes:

      EFFECTIVE      — display / cursor / title changed: pyte implements it.
      RECORDED_ONLY  — only `screen.mode` changed: pyte remembers the mode but implements
                       no behaviour for it (this is where `?1049h` lands — pyte 0.8.2 has
                       NO alternate screen buffer, despite the mode being accepted).
      DROPPED        — nothing changed at all. pyte SILENTLY discarded it. This is the set
                       [CXC-TERM-SEQTABLE]'s upstream pre-scan exists to catch.

    Measured by driving the library, so it cannot go stale against a pyte upgrade the way
    a hand-written coverage list would."""
    try:
        import pyte
    except ImportError:
        return {"available": False}

    # The private/ANSI mode numbers pyte 0.8.2 gives BEHAVIOUR to (pyte.modes). Every other
    # mode number is accepted into `screen.mode` and then does nothing — which is exactly
    # how `?1049h` (alt screen) and `?2004h` (bracketed paste) behave: REMEMBERED, NOT
    # IMPLEMENTED. A set-membership test is the only honest way to tell those apart from a
    # mode whose effect happens to be a no-op from the probe's starting state (`?25h` when
    # the cursor is already visible), which a naive state-diff scores as DROPPED.
    PYTE_PRIVATE_MODES = {3, 5, 6, 7, 25}       # DECCOLM DECSCNM DECOM DECAWM DECTCEM
    PYTE_ANSI_MODES = {4, 20}                   # IRM LNM

    def state(scr):
        return (tuple(scr.display), scr.cursor.x, scr.cursor.y, scr.cursor.hidden,
                tuple(scr.cursor.attrs), frozenset(scr.mode), scr.title, scr.icon_name,
                tuple(scr.margins) if scr.margins else None,
                len(getattr(scr, "savepoints", ())), getattr(scr, "saved_columns", None))

    def mode_verdict(sh: str) -> str | None:
        m = re.fullmatch(r"CSI (\?)?([0-9;]*) ([hl])", sh)
        if not m:
            return None
        private, params = bool(m.group(1)), [p for p in m.group(2).split(";") if p]
        known = PYTE_PRIVATE_MODES if private else PYTE_ANSI_MODES
        return "EFFECTIVE" if any(int(p) in known for p in params) else "RECORDED_ONLY"

    out: dict[str, str] = {}
    for sh in shapes:
        raw = samples.get(sh)
        if raw is None:
            continue
        verdict = mode_verdict(sh)
        if verdict is not None:
            out[sh] = verdict
            continue
        scr = pyte.Screen(20, 5)
        stream = pyte.Stream(scr)
        # A deliberately NON-DEFAULT starting state: text on screen, the cursor off-origin,
        # and non-default attributes. Without this, a reset-shaped sequence (`CSI 0 m`,
        # `CSI r`, `ESC 7`) is a genuine no-op from the default state and scores DROPPED —
        # a probe artifact that would libel pyte's SGR support.
        stream.feed("abcdefghij\nklmnopqrst\n\x1b[1;4;31;44m")
        scr.cursor.x, scr.cursor.y = 4, 1
        before = state(scr)
        try:
            stream.feed(raw.decode("latin-1"))
        except Exception as exc:
            # The most severe outcome and the one no reading of pyte's source would have
            # predicted: the library RAISES. Recorded with the exception text, because a
            # crash in the widget's byte path is a different defect class from a drop.
            out[sh] = f"RAISES {type(exc).__name__}: {exc}"
            continue
        out[sh] = "DROPPED" if state(scr) == before else "EFFECTIVE"
    # MISAPPLIED — worse than DROPPED, and only visible because the probe drove the real
    # library. pyte 0.8.2 recognizes the `?` private marker for h/l ONLY; for every other
    # private introducer (`<`, `>`, `=`, `!`) it strips the marker and dispatches the final
    # byte as if the sequence were the plain ANSI one. So `CSI < u` (kitty keyboard POP)
    # dispatches to `restore_cursor` and TELEPORTS THE CURSOR. A sequence pyte drops is a
    # missing effect; a sequence pyte misapplies is a WRONG render, and the pre-scan has to
    # catch these too.
    #
    # The second misapplication family is OSC: pyte takes the FIRST CHARACTER of the OSC
    # code as the code, so `OSC 133;A` is parsed as OSC **1** (set icon name) with the
    # param "3;A". Any multi-digit OSC that scores EFFECTIVE is that misparse.
    misapplied = sorted(
        sh for sh, v in out.items()
        if v == "EFFECTIVE" and (re.match(r"CSI [<>=!]", sh)
                                 or re.fullmatch(r"OSC \d{2,}", sh)))
    tally = Counter(out.values())
    return {"available": True, "pyte_version": _pyte_version(), "per_shape": out,
            "exemplars": {sh: samples[sh].decode("latin-1") for sh in out if sh in samples},
            "tally": dict(tally), "misapplied": misapplied,
            "legend": {
                "EFFECTIVE": "pyte implements it — observable state changed (or the mode is in pyte.modes)",
                "RECORDED_ONLY": "pyte accepts the mode into screen.mode and implements NO behaviour for it",
                "DROPPED": "pyte silently discarded it — nothing changed. This is the set the "
                           "[CXC-TERM-SEQTABLE] pre-scan must catch UPSTREAM of the emulator",
            }}


def _pyte_version() -> str:
    try:
        from importlib.metadata import version
        return version("pyte")
    except Exception:
        return "unknown"


# ── Report ──────────────────────────────────────────────────
def render_report(results: list[dict], disc: dict, cov: dict) -> str:
    L: list[str] = []
    L.append(f"# Escape-sequence census — [SPEC:CXC-TERM] · [CXC-TERM-CENSUS]\n")
    L.append(f"Measured {_now()} · geometry {CENSUS_ROWS}x{CENSUS_COLS} "
             f"(resized to {RESIZE_ROWS}x{RESIZE_COLS} mid-capture) · "
             f"steady window {CENSUS_STEADY_S:.0f}s\n")
    L.append("## Validity\n")
    L.append("| Profile | gates answered | assistant turns (transcript witness) | startup B | steady B | resize B |")
    L.append("|---|---|---|---|---|---|")
    for r in results:
        L.append(f"| `{r['label']}` | {', '.join(r['gates_answered']) or 'none'} | "
                 f"{r['assistant_turns']} | {r['segments']['startup']['bytes']} | "
                 f"{r['segments']['steady']['bytes']} | {r['segments']['resize']['bytes']} |")
    L.append("")
    L.append(f"**Discrimination gate: {'PASS' if disc['ok'] else 'FAIL — PROBE FAILURE'}**\n")
    L.append("| A | B | bytes A | bytes B | shape Jaccard | verdict |")
    L.append("|---|---|---|---|---|---|")
    for p in disc["pairs"]:
        L.append(f"| {p['a']} | {p['b']} | {p['bytes_a']} | {p['bytes_b']} | "
                 f"{p['shape_jaccard']} | {p['verdict']} |")
    L.append("")
    for seg in ("steady", "startup", "resize"):
        L.append(f"## Segment: {seg}\n")
        L.append("| Profile | bytes | esc bytes | esc ratio | distinct seqs | reads | max burst | mean gap |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in results:
            s = r["segments"][seg]
            L.append(f"| `{r['label']}` | {s['bytes']} | {s['escape_bytes']} | "
                     f"{s['escape_ratio']} | {s['distinct_shapes']} | {s['reads']} | "
                     f"{s['bursts']['max_bytes']} | {s['bursts']['mean_gap_s']} |")
        L.append("")
    # WHERE a decisive sequence appears matters as much as WHETHER. `?1049h` at startup is
    # a mode the widget must degrade on ONCE; `?1049h` at steady state means V1 needs a
    # screen buffer. A steady-state-only table cannot tell those apart.
    L.append("## Decisive features, by profile x segment\n")
    decisive = ["alt_screen_1049", "alt_screen_47", "alt_screen_1047", "abs_cursor_addr",
                "scroll_region", "clear_screen", "clear_scrollback", "scroll_up",
                "bracketed_paste", "sync_output", "mouse_normal", "mouse_button",
                "mouse_any", "mouse_sgr", "focus_events", "dcs", "da1_query",
                "osc_color_query", "xtversion_query", "dsr_query", "dsr_query_ansi",
                "kitty_keyboard", "osc_hyperlink"]
    L.append("| Feature | V1 | " + " | ".join(
        f"{r['profile']}/{s}" for r in results for s in ("startup", "steady", "resize")) + " |")
    L.append("|---" * (2 + 3 * len(results)) + "|")
    sup0 = {f.name: f.supported for f in FEATURES}
    for n in decisive:
        cells = [str(r["segments"][s]["features"].get(n, 0))
                 for r in results for s in ("startup", "steady", "resize")]
        if not any(c != "0" for c in cells):
            continue
        L.append(f"| `{n}` | {'yes' if sup0.get(n) else 'NO'} | " + " | ".join(cells) + " |")
    L.append("")
    L.append("## Capability matrix (steady state)\n")
    names = sorted({n for r in results for n in r["segments"]["steady"]["features"]})
    L.append("| Feature | supported in V1 | " + " | ".join(r["profile"] for r in results) + " |")
    L.append("|---" * (len(results) + 2) + "|")
    sup = {f.name: f.supported for f in FEATURES}
    for n in names:
        cells = " | ".join(str(r["segments"]["steady"]["features"].get(n, 0)) for r in results)
        L.append(f"| `{n}` | {'yes' if sup.get(n) else 'NO'} | {cells} |")
    L.append("")
    if cov.get("available"):
        L.append(f"## pyte coverage (pyte {cov['pyte_version']}, measured by driving the library)\n")
        L.append(f"Tally: {cov['tally']}\n")
        if cov.get("misapplied"):
            L.append("**MISAPPLIED by pyte (a WRONG render, not a missing one): "
                     + ", ".join(f"`{s}`" for s in cov["misapplied"]) + "**\n")
        L.append("| Sequence | exemplar (the actual bytes measured) | pyte |")
        L.append("|---|---|---|")
        ex = cov.get("exemplars", {})
        for sh, verdict in sorted(cov["per_shape"].items(), key=lambda kv: (kv[1], kv[0])):
            raw = ex.get(sh, "")
            pretty = raw.replace("\x1b", "ESC ").replace("\x07", " BEL").replace("|", "\\|")
            flag = " **MISAPPLIED**" if sh in cov.get("misapplied", ()) else ""
            L.append(f"| `{sh}` | `{pretty}` | {verdict}{flag} |")
    return "\n".join(L) + "\n"


# ── Entry ───────────────────────────────────────────────────
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # `as-deployed` is NOT in the default set, and that is a SAFETY call, not a demotion: it
    # is the only profile that joins ComposeBus (real `.mcp.json` + the dev-channels flag), so
    # it must be an explicit, attended act — `--profiles as-deployed`.  It is still REQUIRED
    # for any startup claim ([CXC-TERM-CENSUS] validity condition 4).
    ap.add_argument("--profiles", default="fullscreen,default,ax,ax+default,ax+fullscreen")
    ap.add_argument("--out", default=os.path.join(_HERE, "terminal_census_report"))
    ap.add_argument("--cwd", default=os.path.join(os.path.expanduser("~"), ".cache",
                                                  "composeio-terminal-census"))
    ap.add_argument("--claude-bin", default=os.environ.get(
        "CLAUDE_BIN", os.path.join(os.path.expanduser("~"), ".claude", "local", "claude")))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--reanalyse", action="store_true",
                    help="rebuild the report from the KEPT artifacts — no claude is spawned. "
                         "The analysis is re-runnable against the same bytes, so a later "
                         "question does not cost another measurement.")
    args = ap.parse_args(argv[1:])
    if args.selftest:
        return _selftest()

    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.cwd, exist_ok=True)
    results, aborts = [], []
    if args.reanalyse:
        with open(os.path.join(args.out, "census.json"), "r", encoding="utf-8") as fh:
            results = json.load(fh)["profiles"]
        for r in results:
            path = os.path.join(args.out, f"{r['profile']}.capture.json")
            if not os.path.isfile(path):
                _log(f"  {r['profile']}: no capture.json — keeping the stored counts "
                     f"(they predate any table change made since)")
                continue
            with open(path, "r", encoding="utf-8") as fh:
                capf = json.load(fh)
            chunks = [(t, bytes.fromhex(h)) for t, h in capf["chunks"]]
            marks = capf["marks"]
            r["segments"] = {
                "startup": analyse([(t, d) for (t, d) in chunks if t < marks["startup_end"]]),
                "steady": analyse([(t, d) for (t, d) in chunks
                                   if marks["steady_start"] <= t < marks["steady_end"]]),
                "resize": analyse([(t, d) for (t, d) in chunks
                                   if marks["resize_start"] <= t < marks["resize_end"]]),
            }
        _log(f"reanalyse: {len(results)} profile(s) re-derived from the kept captures")
    else:
        for key in [p.strip() for p in args.profiles.split(",") if p.strip()]:
            if key not in PROFILES:
                _log(f"unknown profile {key!r} — skipping")
                continue
            try:
                results.append(run_profile(key, args.cwd, args.claude_bin, keep_raw=args.out))
            except CensusAbort as exc:
                _log(f"ABORT {exc}")
                aborts.append(str(exc))
    if not results:
        _log("NO PROFILE PRODUCED — the census is VOID. " + "; ".join(aborts))
        return 2

    disc = discrimination(results)
    samples = _shape_samples(args.out, results)
    # The UNION across every segment, not just steady: a sequence that only appears at
    # startup or on resize still reaches the widget's emulator, and `CSI < u` (which pyte
    # MISAPPLIES) is a startup-only sequence — a steady-only coverage pass would miss it.
    all_shapes = sorted({s for r in results for seg in r["segments"].values()
                         for s in seg["shapes"]})
    cov = pyte_coverage(all_shapes, samples)
    payload = {"measured_at": _now(), "constants": {
        "CENSUS_STEADY_S": CENSUS_STEADY_S, "CENSUS_RESIZE_S": CENSUS_RESIZE_S,
        "rows": CENSUS_ROWS, "cols": CENSUS_COLS,
        "resize_rows": RESIZE_ROWS, "resize_cols": RESIZE_COLS},
        "fixture_prompt": FIXTURE_PROMPT, "profiles": results,
        "discrimination": disc, "pyte": cov, "aborts": aborts}
    with open(os.path.join(args.out, "census.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=False)
    report = render_report(results, disc, cov)
    with open(os.path.join(args.out, "census.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    sys.stdout.write(report)

    if not disc["ok"]:
        _log("PROBE FAILURE — profiles did not discriminate. No target mode may be derived "
             "from this run ([CXC-TERM-CENSUS-PRIOR]).")
        return 3
    return 0


def _shape_samples(out_dir: str, results: list[dict]) -> dict[str, bytes]:
    """One raw exemplar per observed shape, taken from the kept raw captures — so the pyte
    probe feeds pyte the ACTUAL bytes rather than a reconstruction."""
    samples: dict[str, bytes] = {}
    for r in results:
        path = os.path.join(out_dir, f"{r['profile']}.raw")
        if not os.path.isfile(path):
            continue
        with open(path, "rb") as fh:
            toks, _ = tokenize(fh.read())
        for t in toks:
            if t.kind == K_TEXT:
                continue
            samples.setdefault(shape(t), t.raw)
    return samples


# ── Selftest ([CXC-TERM-CENSUS] verify-machine) — no claude, no network ────────────────
def _selftest() -> int:
    fails: list[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    # (1) the tokenizer is chunk-boundary safe: splitting a sequence at EVERY offset must
    #     still yield exactly one token ([CXC-TERM-SEQTABLE]).
    seq = b"\x1b[?1049h"
    for i in range(1, len(seq)):
        head, rest = seq[:i], seq[i:]
        toks, tail = tokenize(head)
        toks2, tail2 = tokenize(tail + rest)
        got = [t for t in toks + toks2 if t.kind == K_CSI]
        check(len(got) == 1 and got[0].raw == seq,
              f"(1) split at {i} produced {got!r}, expected exactly one {seq!r}")
    # a split OSC too (its terminator is two bytes)
    osc = b"\x1b]8;id=x;https://example.com\x07"
    toks, tail = tokenize(osc[:12])
    toks2, _ = tokenize(tail + osc[12:])
    check(len([t for t in toks + toks2 if t.kind == K_OSC]) == 1, "(1) split OSC not matched once")

    # (2) shapes collapse parameters but NOT mode numbers.
    check(shape(Token(K_CSI, b"\x1b[12G")) == shape(Token(K_CSI, b"\x1b[44G")),
          "(2) CSI n G did not collapse to one family")
    check(shape(Token(K_CSI, b"\x1b[?1049h")) != shape(Token(K_CSI, b"\x1b[?25h")),
          "(2) mode numbers were wrongly collapsed — ?1049h and ?25h are not one capability")

    # (3) the feature table classifies the load-bearing sequences correctly.
    for raw, want in ((b"\x1b[?1049h", "alt_screen_1049"), (b"\x1b[?2004h", "bracketed_paste"),
                      (b"\x1b[?2026h", "sync_output"), (b"\x1b[3;7H", "abs_cursor_addr"),
                      (b"\x1b[2;30r", "scroll_region"), (b"\x1b[2J", "clear_screen"),
                      (b"\x1b[?1000h", "mouse_normal"), (b"\x1b[?25l", "cursor_hide")):
        check(want in classify(Token(K_CSI, raw)), f"(3) {raw!r} not classified as {want}")
    check("abs_cursor_addr" not in classify(Token(K_CSI, b"\x1b[H")),
          "(3) bare CSI H (home) wrongly counted as absolute addressing")
    check("scroll_region" not in classify(Token(K_CSI, b"\x1b[r")),
          "(3) bare CSI r (region RESET) wrongly counted as a scroll region")

    # (4) the discrimination gate FAILS LOUDLY on two identical fixture profiles — the
    #     exact outcome that voided attempt 1 ([CXC-TERM-CENSUS-PRIOR]).
    def fixture(name, shapes, nbytes):
        return {"profile": name, "label": name,
                "segments": {"steady": {"bytes": nbytes, "shapes": shapes}}}
    same = discrimination([fixture("a", {"CSI {n} G": 5}, 1436),
                           fixture("b", {"CSI {n} G": 5}, 1436)])
    check(not same["ok"], "(4) two byte-identical profiles were NOT flagged as a probe failure")
    diff = discrimination([fixture("a", {"CSI {n} G": 5}, 1436),
                           fixture("b", {"CSI {n} G": 9, "CSI ?1049 h": 1}, 2200)])
    check(diff["ok"], "(4) two materially different profiles were wrongly flagged")

    # (5) a capture that never got past a gate ABORTS rather than producing a profile.
    #     The fixture is the REAL shape of the gate: words separated by absolute COLUMN
    #     moves, not spaces — the exact reason a naive substring test walked past it.
    gate_bytes = (b"\x1b[2GQuick\x1b[8Gsafety\x1b[15Gcheck:\x1b[22GIs\x1b[25Gthis\x1b[30Ga"
                  b"\x1b[32Gproject\x1b[40Gyou\x1b[44Gcreated\x1b[52Gor\x1b[55Gone\x1b[59Gyou"
                  b"\x1b[63Gtrust?\x1b[39m")
    gate_screen = squash(plain_text(gate_bytes))
    check(any(squash(m) in gate_screen for m, _k, _w in KNOWN_GATES),
          "(5) the trust gate is not detected in real gate screen text (column-positioned words)")
    check("Is this a project you created" not in plain_text(gate_bytes),
          "(5) the fixture no longer reproduces the column-positioning that broke naive matching")
    check(plain_text(b"\x1b[?2004h\x1b[38;2;1;2;3mhi\x1b[39m") == "hi",
          "(5) plain_text did not strip escape sequences")

    if fails:
        sys.stderr.write(f"\n{len(fails)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: terminal_census selftest (5/5 groups)\n"
                     "  (1) the tokenizer matches a CSI/OSC split at EVERY byte offset exactly once\n"
                     "  (2) shapes collapse numeric params but preserve MODE numbers\n"
                     "  (3) the feature table classifies alt-screen/paste/sync/absolute-addressing/DECSTBM, and does NOT mistake bare CSI H or bare CSI r for them\n"
                     "  (4) the discrimination gate FAILS on byte-identical profiles (attempt 1's outcome) and passes on different ones\n"
                     "  (5) the trust gate is recognized in stripped screen text (attempt 2's diagnosis)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
