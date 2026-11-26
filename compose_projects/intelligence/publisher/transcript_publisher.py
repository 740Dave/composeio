#!/usr/bin/env python3
"""TranscriptPublisher — turn the Intelligence's JSONL transcript into a stream of SEMANTIC bus
events ([SPEC:CXC-INTELLIGENCE-CONSOLE] · [CXC-IC-CANVAS]).

David's insight (2026-07-19): the `reply` tool is a NARROW MANUAL GATE — the thread only gets what
the Intelligence remembers to push through it, while its richest narration lives in main-loop text
that never crosses. And tool calls / subagent lifecycle are semantic events a sub-widget could
render live. The transcript (the session `.jsonl`) is the AUTHORITATIVE record of everything the
Intelligence says and does. So: TAIL it and re-publish it as distinct semantic namespaces, each
bound by its own sub-widget (Problem 4 — state on the bus, behavior in the widget; and [CXC-IC-CANVAS]
made concrete). The Intelligence no longer has to remember to `reply` for narration to be seen.

STREAMS (namespace ← transcript event):
  * `thread/narration`   ← assistant TEXT blocks. The muted "thinking-aloud" voice. A thread client
                           (chat.py, the ComposeApp widget) SUBSCRIBES to this namespace to render it.
  * `self/activity`      ← tool_use / tool_result. {tool, target, phase:start|done, id, dur_s}.
                           A "what's it doing now" sub-widget lights up on these.
  * `self/subagents`     ← Task/Agent lifecycle. {type, desc, status:dispatched|done, tokens,
                           dur_s}. The subagent-ledger sub-widget animates on these. ASYNC-DISPATCH
                           ([SPEC:CXC-IC-STREAMS]): a tool_use opens a RUNNING chip; the tool_result
                           that immediately follows is an async-launch ACK (`toolUseResult.isAsync`)
                           and is NOT a completion — the chip resolves to `done` only on the later
                           `<task-notification>` bearing the original tool-use-id + a terminal
                           status (or on a synchronous non-async tool_result completion).

SAFETY — narration NEVER rides the actuation channel. We deliberately do NOT echo narration onto
`compose_projects/intelligence/<id>/out` (the topic the launcher reads for `{"cmd":...}` directives). If a narration
text block were itself a JSON command blob (e.g. an example `{"cmd":"inject","text":"/exit"}` the
Intelligence typed into its own prose), an echo onto `.../out` would let the launcher PARSE AND
ACTUATE it — a self-injection loop. Keeping narration on its own `thread/narration` namespace makes
that impossible by construction; subscribers render it, the launcher never sees it. (Defense-in-depth
follow-up: the launcher's actuation parser should also authenticate directives, so the actuation
channel can't be spoofed regardless.)

THE BOUNDARY (load-bearing, David + architect agree): mirror what the Intelligence SAYS and DOES,
NEVER what it THINKS or the tool plumbing GUTS. So `thinking` blocks are DROPPED; tool_result
CONTENT is NOT republished (only that a tool finished); nothing under the hood leaks to the thread.

MECHANISM: a standalone READ-ONLY tailer (needs only the JSONL path + an MQTT client) — NO launcher
change, NO relaunch. It seeks to the END of the file (the 57MB history is never re-read or
re-published) and follows appended lines, publishing each new event. Restart resumes from the end
(events during downtime are missed — acceptable for v1; a durable offset is a later refinement).

Run:   python3 transcript_publisher.py --jsonl <path> [--id architect] [--mqtt mqtt://host:port]
                                       [--namespace-by-id]
       python3 transcript_publisher.py --selftest   # no broker, synthetic entries

[CXC-IH-5A] carried-identity: with `--namespace-by-id` the per-instance feeds are rewritten
`<family>/<rest>` → `<family>/<id>/<rest>` (thread/narration → thread/<id>/narration; self/activity
→ self/<id>/activity; …) so two intelligences on the shared broker never MERGE onto the same topics.
The rewrite is the ONE shared helper `self_publisher.scope_namespace` (families self+thread) — never
a second copy — so this publisher and self_publisher can never drift on the scheme. Default OFF
preserves the legacy bare topics.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime

# [CXC-IH-5A] the ONE carried-identity rewrite, reused by reference (never re-implemented). Both
# publishers live in compose_projects/intelligence/publisher/; add our own dir to sys.path so the import resolves
# whether we run as a script or are imported by data_sources.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from self_publisher import scope_namespace  # noqa: E402

# [Phase 2a step 1] the ONE envelope seam — see composeio/bus_envelope.py.
_BE_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))), "python")
if _BE_PY not in sys.path:
    sys.path.insert(0, _BE_PY)
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "transcript"                 # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])


class _Stopped(Exception):
    """Internal: the supervisor set our `stop` Event while we were parked inside
    `follow_lines`.

    `follow_lines` is an INFINITE generator with no cancellation channel, and on a
    quiet transcript the ONLY code that runs is its `on_idle` hook. So the idle
    hook raises this to unwind the generator, rather than the loop body checking a
    flag it may not reach for hours. Caught immediately below the loop; never
    escapes `run()`. [CXC-SOB-RUN-BLOCKS] leg 3.
    """


# ── Namespaces ──────────────────────────────────────────────
NS_NARRATION = "thread/narration"
NS_ACTIVITY = "self/activity"
NS_SUBAGENTS = "self/subagents"
NS_AVATAR = "self/avatar/expression"

# Subagent tool names (a Task/Agent tool_use dispatches a subagent).
SUBAGENT_TOOLS = ("Task", "Agent")

# ── Async-dispatch subagent lifecycle ([SPEC:CXC-IC-STREAMS], async-dispatch invariant) ──
# The current Claude Code harness dispatches subagents ASYNCHRONOUSLY. The tool_result that
# IMMEDIATELY follows an Agent/Task tool_use is an async-launch ACK (entry-level
# `toolUseResult.isAsync` / `status == "async_launched"`), NOT the subagent's completion — closing
# the chip on it flips a just-dispatched subagent to `done` in ~0.5s, after which the linger-prune
# ([CXC-IC-SUBAGENT-LIFECYCLE]) sweeps it, so the console reads "NO SUBAGENTS" while subagents run.
# The TRUE completion arrives LATER as a `type=user` `<task-notification>` record carrying the
# ORIGINAL dispatch `<tool-use-id>` and a TERMINAL `<status>`. (A still-synchronous, non-async
# `tool_result` completion is still honored — both harness modes are covered by one reducer.)
_TASK_NOTIF_TAG = "<task-notification>"
_TERMINAL_TASK_STATUSES = frozenset({
    "completed", "failed", "killed", "stopped", "cancelled", "canceled",
    "error", "aborted", "timeout", "timed_out",
})


def _xml_tag(text: str, name: str) -> str | None:
    """Extract the inner text of `<name>…</name>` from a harness notification blob, or None."""
    m = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, re.S)
    return m.group(1).strip() if m else None

# ── Narration meta-filter ([SPEC:CXC-IC-STREAMS] IC-C16) ─────────────────────
# thread/narration carries what the Intelligence actually SAID. Some assistant
# text blocks are not speech but harness/plumbing PLACEHOLDERS produced by the
# turn machinery — the interrupted-turn marker "No response requested.", an empty
# "(no content)" sentinel, a "[Request interrupted ...]" harness notice. These
# leaked into the thread as ARCHITECT (Narration) bubbles. Publishing them as
# narration leaks the GUTS the boundary forbids, so the tailer DROPS them.
#
# The filter is deliberately TIGHT (normalized-exact / known-prefix on a small
# set) so real narration is never suppressed: strip + drop a trailing period +
# lowercase, then match. A message that merely CONTAINS one of these phrases mid-
# sentence is real speech and is mirrored unchanged.
_META_PLACEHOLDERS_EXACT = frozenset({
    "no response requested",
    "(no content)",
    "no content",
})
_META_PLACEHOLDER_PREFIXES = (
    "[request interrupted",     # "[Request interrupted by user]" / "...for tool use"
)


def _is_meta_placeholder(text: str) -> bool:
    """True when an assistant text block is a harness/plumbing placeholder that
    must NEVER be published as narration (IC-C16), False for real speech."""
    norm = " ".join(text.strip().split()).rstrip(".").lower()
    if norm in _META_PLACEHOLDERS_EXACT:
        return True
    return any(norm.startswith(p) for p in _META_PLACEHOLDER_PREFIXES)

# ── Inferred avatar-expression classifier ───────────────────
# The console's AvatarWidget renders whatever `self/avatar/expression` arrives but NOTHING publishes
# it, so the face is frozen on the scene seed. This is the INFERRED publisher: it reads the same
# JSONL tail and infers the Intelligence's expression from its RECENT activity so the avatar reacts
# to what the architect is actually doing.
#
# The DRAWN enum is fixed by render/widgets/avatar.py — exactly three faces carry distinct paths:
#   grounded | focused | contemplative   (any other/unknown value there renders `grounded`, never
#   blank). This classifier only ever emits those three, so no nearest-mapping is needed downstream.
#
# FOLLOW-UP (not built here): Spec 34 [CXC-IC-CANVAS] / [CXC-ICD-D20] D20 defines a TWO-publisher
# model — an EXPLICIT publisher (the Intelligence publishing its own expression) arbitrated ABOVE
# this inferred fallback by a bus-side PriorityCommandSource (explicit > inferred). Only the inferred
# publisher is in scope for this task; the explicit publisher + arbitration are a documented TODO.
AVATAR_ENUM = ("grounded", "focused", "contemplative")
AVATAR_WINDOW_S = 90.0     # the recent-activity window the classifier reasons over
AVATAR_POLL_S = 5.0        # idle re-check cadence, so `focused` decays to `grounded` when work stops


def _iso_to_epoch(ts) -> float | None:
    """Parse a JSONL entry `timestamp` (ISO-8601, often Z-suffixed) to epoch seconds, or None."""
    if not ts or not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _target_of(tool_name: str, tool_input: dict) -> str:
    """A short human label for what a tool acted on — the sub-widget's caption."""
    if not isinstance(tool_input, dict):
        return tool_name
    if tool_name in ("Read", "Edit", "Write", "NotebookEdit"):
        fp = tool_input.get("file_path", "")
        return os.path.basename(fp) if fp else tool_name
    if tool_name == "Bash":
        return str(tool_input.get("description") or tool_input.get("command", ""))[:60]
    if tool_name in ("Grep", "Glob"):
        return str(tool_input.get("pattern", ""))[:60]
    if tool_name in SUBAGENT_TOOLS:
        return str(tool_input.get("description", ""))[:60]
    if tool_name.startswith("mcp__"):
        return tool_name.split("__", 2)[-1]
    # generic: first stringy input value, else the tool name
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return v[:60]
    return tool_name


def _extract_subagent_tokens(entry: dict) -> int | None:
    """Pull a subagent token count out of a completion entry, if present (mirrors the self_sensor's
    tolerant scan of the tool_result/notification text)."""
    txt = json.dumps(entry)
    if "subagent_tokens" not in txt:
        return None
    try:
        tail = txt.split("subagent_tokens", 1)[1].lstrip('":> ')
        num = tail.split("<")[0].split(",")[0].split("}")[0].strip()
        return int(num)
    except (ValueError, IndexError):
        return None


class TranscriptPublisher:
    """Pure transcript→events logic. `process_entry(entry)` returns a list of (namespace, payload)
    to publish; the tailer or the selftest does the actual publishing. State pairs a tool_use with
    its later tool_result by `tool_use_id` (for phase:done + duration)."""

    def __init__(self):
        self._pending_tools: dict[str, dict] = {}      # id -> {tool, target, ts_start}
        self._pending_subagents: dict[str, dict] = {}  # id -> {type, desc, ts_start}
        # agentId -> the ORIGINAL dispatch tool-use-id (learned from the async-launch ACK), so a
        # later task_status record (keyed by taskId == agentId) addresses the SAME chip the
        # tool_use path opened — no double chip. ([SPEC:CXC-IC-STREAMS] task_status recovery, IC-C21)
        self._agent_to_tool: dict[str, str] = {}
        self._turn = 0
        # Inferred-avatar state: a bounded rolling window of (epoch_ts, category) recent activity,
        # and the LAST published expression (the change-gate — we publish only on transition).
        self._recent: list[tuple[float, str]] = []
        self._avatar_expr: str | None = None

    def process_entry(self, entry: dict) -> list[tuple[str, dict]]:
        if not isinstance(entry, dict):
            return []
        t = entry.get("type")
        ts = _iso_to_epoch(entry.get("timestamp"))
        # Observe every entry for the avatar window (a pure side-effect — does NOT change the
        # narration/activity/subagents events returned below; those stay byte-for-byte identical).
        self._observe(entry, ts)
        if t == "assistant":
            return self._process_assistant(entry, ts)
        if t in ("user", "tool_result"):
            return self._process_result_bearing(entry, ts)
        if t == "attachment":
            return self._process_attachment(entry, ts)
        return []

    # -- inferred avatar expression -------------------------------------------
    #
    # Classifier (deterministic, no LLM), over the recent-activity window:
    #   focused        — a tool_use / tool_result in the window: the architect is actively building.
    #   contemplative  — reflection/narration-heavy with NO tool calls, or a compact boundary is
    #                    near (mid-thought): the Intelligence is thinking, not acting.
    #   grounded       — nothing recent: at rest / idle / stable after registration.
    # The window decay (AVATAR_WINDOW_S) gives the natural progression working -> focused, then
    # (as tool events age out but reflection remains) -> contemplative, then (all quiet) -> grounded.

    def _observe(self, entry: dict, ts: float | None) -> None:
        """Categorize an entry and append it to the bounded recent-activity window."""
        when = ts if ts is not None else time.time()
        t = entry.get("type")
        cat: str | None = None
        if entry.get("isCompactSummary"):
            cat = "compact"
        elif t == "assistant":
            msg = entry.get("message") or {}
            content = msg.get("content") or []
            if isinstance(content, list):
                has_tool = any(isinstance(b, dict) and b.get("type") == "tool_use"
                               for b in content)
                has_text = any(isinstance(b, dict) and b.get("type") == "text"
                               and (b.get("text") or "").strip() for b in content)
                if has_tool:
                    cat = "tool"           # the Intelligence is acting -> working
                elif has_text:
                    cat = "reflection"     # thinking-aloud narration, no action
        elif t in ("user", "tool_result"):
            # Only a tool_result counts as the Intelligence's activity (a tool actively completing);
            # a plain user message is the human speaking, not the Intelligence working.
            msg = entry.get("message") or {}
            content = msg.get("content")
            if isinstance(content, list) and any(
                    isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                cat = "tool"
        if cat is not None:
            self._recent.append((when, cat))
            self._prune(when)              # bound memory relative to the newest ts seen

    def _prune(self, ref: float) -> None:
        self._recent = [(t, c) for (t, c) in self._recent if t >= ref - AVATAR_WINDOW_S]

    def classify(self, now: float) -> str:
        """The inferred expression at time `now`, from the recent window. One of AVATAR_ENUM."""
        cats = {c for (t, c) in self._recent if t >= now - AVATAR_WINDOW_S}
        if "tool" in cats:
            return "focused"
        if "compact" in cats or "reflection" in cats:
            return "contemplative"
        return "grounded"

    def avatar_event(self, now: float) -> tuple[str, dict] | None:
        """Change-gated: returns the (namespace, payload) to publish only when the classified
        expression TRANSITIONS; otherwise None (no churn — we never re-publish the same value)."""
        expr = self.classify(now)
        if expr == self._avatar_expr:
            return None
        self._avatar_expr = expr
        return (NS_AVATAR, {"expression": expr, "ts": _now_ms(now)})

    def _process_assistant(self, entry: dict, ts: float | None) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        msg = entry.get("message") or {}
        content = msg.get("content") or []
        if not isinstance(content, list):
            return events
        self._turn += 1
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                text = (block.get("text") or "").strip()
                if not text:
                    continue
                # IC-C16: harness/plumbing placeholders are not speech — drop them
                # so they never render as an ARCHITECT (Narration) bubble.
                if _is_meta_placeholder(text):
                    continue
                payload = {"from": "architect", "text": text, "turn": self._turn,
                           "ts": _now_ms(ts)}
                events.append((NS_NARRATION, payload))
            elif bt == "tool_use":
                events += self._on_tool_use(block, ts)
            # bt == "thinking" (or anything else): DROPPED — never mirror thinking/plumbing.
        return events

    def _on_tool_use(self, block: dict, ts: float | None) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        tid = block.get("id") or ""
        name = block.get("name", "?")
        tool_input = block.get("input") or {}
        target = _target_of(name, tool_input)
        events.append((NS_ACTIVITY, {"tool": name, "target": target, "phase": "start",
                                     "id": tid, "ts": _now_ms(ts)}))
        if tid:
            self._pending_tools[tid] = {"tool": name, "target": target, "ts_start": ts}
        if name in SUBAGENT_TOOLS:
            sub_type = tool_input.get("subagent_type", "agent")
            model = tool_input.get("model")
            desc = tool_input.get("description", "")
            sa = {"type": sub_type, "desc": desc, "status": "dispatched", "id": tid,
                  "ts": _now_ms(ts)}
            if model:
                sa["model"] = model
            events.append((NS_SUBAGENTS, sa))
            if tid:
                self._pending_subagents[tid] = {"type": sub_type, "desc": desc, "ts_start": ts}
        return events

    def _process_result_bearing(self, entry: dict, ts: float | None) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []
        msg = entry.get("message") or {}
        content = msg.get("content")

        # [CXC-IC-STREAMS async-dispatch] async subagent COMPLETION: the true end of an
        # async-dispatched subagent is a `type=user` `<task-notification>` record (string or
        # text-block content) carrying the ORIGINAL dispatch `<tool-use-id>` + a terminal
        # `<status>`. Reduce it to `status:"done"` for that chip. (The immediate async-launch
        # ACK handled below is NOT this — it must never close the chip.)
        notif_text = content if isinstance(content, str) else None
        if notif_text is None and isinstance(content, list):
            notif_text = "".join(
                b.get("text", "") for b in content
                if isinstance(b, dict) and b.get("type") == "text")
        if notif_text and _TASK_NOTIF_TAG in notif_text:
            events += self._on_task_notification(notif_text, ts)

        blocks = content if isinstance(content, list) else []
        tokens = _extract_subagent_tokens(entry)
        # An async-launch ACK closes NO subagent — the sub is still RUNNING; its chip must stay
        # `dispatched` until the `<task-notification>` above. A SYNCHRONOUS (non-async)
        # `tool_result` completion (`toolUseResult.status == "completed"`, no `isAsync`) still
        # resolves normally below, so both harness modes are covered.
        tur = entry.get("toolUseResult")
        async_ack = isinstance(tur, dict) and (
            bool(tur.get("isAsync")) or tur.get("status") == "async_launched")
        for block in blocks:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = block.get("tool_use_id") or ""
            # tool activity done (we publish only THAT it finished, never the result CONTENT).
            pend = self._pending_tools.pop(tid, None)
            if pend is not None:
                dur = _dur(pend.get("ts_start"), ts)
                events.append((NS_ACTIVITY, {"tool": pend["tool"], "target": pend["target"],
                                             "phase": "done", "id": tid, "dur_s": dur,
                                             "ts": _now_ms(ts)}))
            if async_ack:
                # Learn agentId -> ORIGINAL tool-use-id so a later task_status record (keyed by
                # taskId == agentId) resolves to THIS chip, never a duplicate ([CXC-IC-STREAMS]
                # task_status recovery, IC-C21). The subagent is still RUNNING — never close its
                # chip on the ACK.
                agent_id = tur.get("agentId") if isinstance(tur, dict) else None
                if agent_id and tid:
                    self._agent_to_tool[str(agent_id)] = tid
                continue
            # subagent completion (synchronous tool_result path).
            sub = self._pending_subagents.pop(tid, None)
            if sub is not None:
                dur = _dur(sub.get("ts_start"), ts)
                sa = {"type": sub["type"], "desc": sub["desc"], "status": "done", "id": tid,
                      "dur_s": dur, "ts": _now_ms(ts)}
                if tokens is not None:
                    sa["tokens"] = tokens
                events.append((NS_SUBAGENTS, sa))
        return events

    def _process_attachment(self, entry: dict, ts: float | None) -> list[tuple[str, dict]]:
        """[SPEC:CXC-IC-STREAMS] task_status RECOVERY leg (IC-C21). The harness emits a
        `type:"attachment"` entry whose `attachment.type == "task_status"` carries the AUTHORITATIVE
        running/terminal state of every background (`taskType:"local_agent"`) subagent —
        `{taskId, taskType, description, status, deltaSummary}`, `taskId` == the async-launch ACK's
        `agentId`. This is the ONLY running-state signal for a subagent whose dispatch the tailer
        never WITNESSED (it seeks to END, so any relaunch mid-run misses the `Agent` tool_use), so
        consuming it makes the chip SELF-HEALING.

        Idempotent with the tool_use path: an agent already tracked (chip opened by its witnessed
        dispatch, correlated via `_agent_to_tool`) emits NOTHING, so the no-restart case is
        byte-for-byte unchanged. A `running` status for an UNTRACKED agent OPENS a running chip
        keyed by `taskId`; a TERMINAL status resolves the chip to `done`. A terminal for an agent we
        never opened emits nothing (no phantom `done`)."""
        att = entry.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "task_status":
            return []
        task_id = str(att.get("taskId") or "").strip()
        if not task_id:
            return []
        status = str(att.get("status") or "").strip().lower()
        desc = str(att.get("description") or "").strip()
        sub_type = str(att.get("taskType") or "agent").strip() or "agent"
        # Address the SAME chip the tool_use path opened when we witnessed the async-launch ACK;
        # otherwise (unwitnessed dispatch) key by taskId — the recovery path.
        chip_id = self._agent_to_tool.get(task_id, task_id)
        if status in _TERMINAL_TASK_STATUSES:
            sub = self._pending_subagents.pop(chip_id, None)
            self._agent_to_tool.pop(task_id, None)
            if sub is None:
                return []   # never opened a chip for this agent -> no phantom done
            dur = _dur(sub.get("ts_start"), ts)
            sa = {"type": sub.get("type", sub_type), "desc": sub.get("desc", desc),
                  "status": "done", "id": chip_id, "ts": _now_ms(ts)}
            if dur is not None:
                sa["dur_s"] = dur
            return [(NS_SUBAGENTS, sa)]
        # non-terminal (running): RECOVERY. Only OPEN a chip when none is tracked — the tool_use
        # dispatch path owns a witnessed agent, so this is a no-op in the common (no-restart) case.
        if chip_id in self._pending_subagents:
            return []
        self._pending_subagents[chip_id] = {"type": sub_type, "desc": desc, "ts_start": ts}
        sa = {"type": sub_type, "desc": desc, "status": "dispatched", "id": chip_id,
              "ts": _now_ms(ts)}
        return [(NS_SUBAGENTS, sa)]

    def _on_task_notification(self, text: str, ts: float | None) -> list[tuple[str, dict]]:
        """Reduce a harness `<task-notification>` — the async-subagent completion signal — to a
        `self/subagents` done event, keyed by the ORIGINAL dispatch `<tool-use-id>`
        ([SPEC:CXC-IC-STREAMS] async-dispatch invariant). A TERMINAL `<status>`
        (completed|failed|killed|stopped|…) closes the chip; a present-but-non-terminal status is
        ignored (the subagent is still running). An unknown/absent tool-use-id yields NOTHING (no
        phantom chip). The dispatch→notification span is the chip's real running duration."""
        tid = _xml_tag(text, "tool-use-id") or ""
        status = (_xml_tag(text, "status") or "").strip().lower()
        if not tid or (status and status not in _TERMINAL_TASK_STATUSES):
            return []
        sub = self._pending_subagents.pop(tid, None)
        if sub is None:
            return []
        dur = _dur(sub.get("ts_start"), ts)
        sa = {"type": sub["type"], "desc": sub["desc"], "status": "done", "id": tid,
              "dur_s": dur, "ts": _now_ms(ts)}
        return [(NS_SUBAGENTS, sa)]


def _now_ms(ts: float | None) -> int:
    return int((ts if ts is not None else time.time()) * 1000)


def _dur(ts_start: float | None, ts_end: float | None) -> float | None:
    if ts_start is None or ts_end is None:
        return None
    return round(ts_end - ts_start, 1)


# ── The live tailer (production) ────────────────────────────
def _repo_root() -> str:
    # This file is compose_projects/intelligence/<sub>/<me>.py, so the repo root is FOUR
    # dirnames up. Three stops at compose_projects/ -- see the doubled-metrics.db defect.
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def _parse_mqtt_url(url: str) -> tuple[str, int]:
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


# [CXC-IH-5A] the families this publisher's feeds live under: thread/narration + thread/diagram
# (thread) and self/activity + self/subagents + self/avatar/expression (self).
_ID_FAMILIES = ("self", "thread")


def run(jsonl_path: str, intelligence_id: str, mqtt_url: str, from_start: bool = False,
        namespace_by_id: bool = False, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] slice 4 — publish-only, TRANSCRIPT-based.

    Publishes through `ComposeIOBus` ([CXC-SOB-THESIS]). The two reasons this
    used raw paho are both gone: the topic-level mismatch was a TOPIC concern and
    is now the transport's business (`MQTTTransport` maps ns+scope to
    `composeio/{scope}/{ns}`, which is the scheme the console subscribes), and
    the BINARY auto-register that rejected a float/dict was fixed on 2026-08-08
    ([CXC-SOB-BYPASS-EXPIRED]) — this source declares explicitly regardless. The
    unique client-id survives: `transport_registry` keys it on app_id + pid, so
    the rc=7 collision is structurally impossible rather than merely avoided.

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]) — checked, not assumed. This source
    does not subscribe, but `Namespace.publish_state` notifies local subscribers
    only when `new != old`, so an identical repeat would be ABSORBED. That would
    matter here: selftest check (o) pins that a GENUINELY REPEATED narration line
    must narrate BOTH times ("exactly-once is the singleton, never a content
    dedup that would suppress legitimate repeats").

    It is safe because the narration payload is
    `{"from", "text", "turn", "ts"}` — the per-turn counter and the millisecond
    timestamp make two identical TEXTS two distinct VALUES. That is the
    load-bearing fact: an edit that trimmed the payload down to `{"from",
    "text"}` for tidiness would silently start swallowing the second of two
    identical lines, on the glass, with no error. Do not trim it.

    Where a repeat IS absorbed — `self/avatar/expression` re-classifying to the
    SAME mood — that is correct: the widget renders the mood, not the event of
    deciding it.
    """
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from tail_follow import follow_lines  # the ONE partial-line-safe tail reader (de-forked)

    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): per-<id> — one transcript relay per
    # Intelligence. Drop the PID suffix (the enabler that let two coexist); a
    # second instance from any launch path yields without connecting.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    _key = f"transcript-publisher-{intelligence_id}"
    lock = acquire_singleton(_key)
    if lock is None:
        sys.stderr.write(f"[transcript_publisher] another {_key} holds the singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=_key, bus=bus)
    pub = TranscriptPublisher()
    _declared: set[str] = set()

    #: DYNAMIC NAMESPACE SET, declared LAZILY ([CXC-SOB-DECLARE]) — see
    #: `self_publisher` for the full reasoning. A transcript can legitimately
    #: produce a namespace for the first time an hour into a session, so a
    #: hardcoded startup list would be a lie that passes the SOB-C3 audit.
    #: `manifest(only_if_grown=True)` logs a line the moment one first appears.
    def emit(namespace: str, payload) -> None:
        # [CXC-IH-5A] carried-identity rewrite (opt-in). `<family>/<rest>` -> `<family>/<id>/<rest>`
        # for the self+thread families; a no-op in legacy mode so the live topics are byte-for-byte
        # unchanged. The envelope `ns` and the MQTT topic BOTH carry the scoped name so a subscriber
        # bound to `self/<id>/*` / `thread/<id>/*` receives it and the wire is self-describing.
        if namespace_by_id:
            namespace = scope_namespace(namespace, intelligence_id, _ID_FAMILIES)
        if namespace not in _declared:
            source_bus.declare(b, namespace, scope=Scope.GLOBAL, source=SRC)
            _declared.add(namespace)
            source_bus.manifest(SRC, only_if_grown=True)
        b.publish_state(namespace, payload, source=SRC)

    _feeds = ("thread/narration, self/activity, self/subagents, self/avatar/expression"
              if not namespace_by_id else
              f"thread/{intelligence_id}/narration, self/{intelligence_id}/activity, "
              f"self/{intelligence_id}/subagents, self/{intelligence_id}/avatar/expression")
    sys.stderr.write(f"[publisher] tailing {jsonl_path}\n[publisher] -> "
                     f"{{{_feeds}}}\n")
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
        if not from_start:
            fh.seek(0, os.SEEK_END)  # only NEW turns — never re-publish the 57MB history
        # Establish the avatar baseline immediately so the console leaves the frozen scene seed.
        av = pub.avatar_event(time.time())
        if av:
            emit(*av)
        last_avatar = [time.time()]  # boxed so the idle callback can update it

        def _idle() -> None:
            # No new complete line: re-classify on a slow cadence so a finished burst of work
            # decays focused -> contemplative -> grounded even though no new lines arrive.
            # ALSO the stop check ([CXC-SOB-RUN-BLOCKS] leg 3): `follow_lines` is an
            # infinite generator and this idle hook is the ONLY code that runs on a
            # quiet transcript, so it is the only place a stop signal can be noticed
            # without waiting for the next line — which may never come.
            if stop is not None and stop.is_set():
                raise _Stopped()
            now = time.time()
            if now - last_avatar[0] >= AVATAR_POLL_S:
                last_avatar[0] = now
                av = pub.avatar_event(now)
                if av:
                    emit(*av)

        # follow_lines yields only COMPLETE lines — a partial mid-write line (e.g. a large Agent
        # dispatch/completion still being flushed) is BUFFERED, never dropped as a fragment.
        try:
            for line in follow_lines(fh, poll_s=0.5, on_idle=_idle):
                if stop is not None and stop.is_set():
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                for namespace, payload in pub.process_entry(entry):
                    emit(namespace, payload)
                # New activity just landed — re-classify against it and publish on change.
                now = time.time()
                last_avatar[0] = now
                av = pub.avatar_event(now)
                if av:
                    emit(*av)
        except _Stopped:
            pass                       # the supervisor asked us to stand down
    return 0


# ── Selftest (synthetic entries; no broker) ─────────────────
def _selftest() -> int:
    failures: list[str] = []

    def check(cond, msg):
        if not cond:
            failures.append(msg)
            sys.stderr.write(f"FAIL: {msg}\n")

    pub = TranscriptPublisher()

    # (a) assistant TEXT -> narration (+ .../out echo); THINKING is dropped; tool_use -> activity.
    ev = pub.process_entry({
        "type": "assistant", "timestamp": "2026-07-19T17:00:00Z",
        "message": {"content": [
            {"type": "thinking", "thinking": "SECRET private reasoning — must NOT leak"},
            {"type": "text", "text": "Both surface the new reality from this session."},
            {"type": "tool_use", "id": "t1", "name": "Read",
             "input": {"file_path": "/x/corpora/platform-specs/34-composeux-intelligence-console.lts"}},
        ]},
    })
    ns = [n for n, _ in ev]
    check(NS_NARRATION in ns, f"(a) text block did not produce narration: {ns}")
    check("__out__" not in ns and "intelligence/architect/out" not in ns,
          f"(a) narration MUST NOT ride the actuation channel (.../out): {ns}")
    check(NS_ACTIVITY in ns, f"(a) tool_use did not produce activity: {ns}")
    check(not any("SECRET" in json.dumps(p) for _, p in ev),
          "(a) THINKING block LEAKED into the published events (must be dropped)")
    nar = [p for n, p in ev if n == NS_NARRATION][0]
    check("Both surface" in nar["text"] and nar.get("from") == "architect",
          f"(a) narration payload wrong: {nar}")
    act = [p for n, p in ev if n == NS_ACTIVITY][0]
    check(act["tool"] == "Read" and act["target"] == "34-composeux-intelligence-console.lts" and act["phase"] == "start",
          f"(a) activity event wrong (tool/target/phase): {act}")

    # (b) tool_result closes the activity with phase:done + duration (NOT the result content).
    ev2 = pub.process_entry({
        "type": "user", "timestamp": "2026-07-19T17:00:03Z",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "big file contents here"},
        ]},
    })
    done = [p for n, p in ev2 if n == NS_ACTIVITY]
    check(len(done) == 1 and done[0]["phase"] == "done" and done[0]["dur_s"] == 3.0,
          f"(b) tool_result did not close activity with duration: {ev2}")
    check(not any("big file contents" in json.dumps(p) for _, p in ev2),
          "(b) tool_result CONTENT leaked (only 'done' should publish, never the result body)")

    # (c) Task tool_use -> subagent dispatched (with type/model/desc).
    ev3 = pub.process_entry({
        "type": "assistant", "timestamp": "2026-07-19T17:01:00Z",
        "message": {"content": [
            {"type": "tool_use", "id": "s1", "name": "Task",
             "input": {"subagent_type": "general-purpose", "model": "opus",
                       "description": "Pre-vis Instrument"}},
        ]},
    })
    sa = [p for n, p in ev3 if n == NS_SUBAGENTS]
    check(len(sa) == 1 and sa[0]["status"] == "dispatched" and sa[0]["type"] == "general-purpose"
          and sa[0].get("model") == "opus" and sa[0]["desc"] == "Pre-vis Instrument",
          f"(c) subagent dispatch event wrong: {ev3}")

    # (d) subagent completion -> status:done + duration + tokens.
    ev4 = pub.process_entry({
        "type": "user", "timestamp": "2026-07-19T17:05:48Z",
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "s1",
             "content": "result <subagent_tokens>71990</subagent_tokens>"},
        ]},
    })
    sad = [p for n, p in ev4 if n == NS_SUBAGENTS]
    check(len(sad) == 1 and sad[0]["status"] == "done" and sad[0]["dur_s"] == 288.0
          and sad[0].get("tokens") == 71990,
          f"(d) subagent completion event wrong: {ev4}")

    # (e) a text-free assistant turn (pure tool_use) emits NO narration.
    ev5 = pub.process_entry({
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash",
                                 "input": {"description": "run tests"}}]},
    })
    check(not any(n == NS_NARRATION for n, _ in ev5),
          f"(e) a tool-only turn wrongly produced narration: {ev5}")
    check(any(n == NS_ACTIVITY and p["target"] == "run tests" for n, p in ev5),
          f"(e) Bash activity target should be the description: {ev5}")

    # ── Inferred avatar-expression classifier ──────────────────────────────
    T0 = 1_000_000.0  # a fixed synthetic epoch base (seconds)

    # (f) active tool_use in the recent window -> focused.
    pf = TranscriptPublisher()
    pf._observe({"type": "assistant",
                 "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                                          "input": {"file_path": "/a/b.py"}}]}}, T0)
    check(pf.classify(T0 + 10) == "focused",
          f"(f) recent tool_use should classify focused, got {pf.classify(T0 + 10)}")
    # ... and it decays to grounded once the tool event ages out of the window.
    check(pf.classify(T0 + AVATAR_WINDOW_S + 1) == "grounded",
          f"(f) tool activity should decay to grounded past the window, "
          f"got {pf.classify(T0 + AVATAR_WINDOW_S + 1)}")

    # (g) reflection/narration with NO tool calls -> contemplative.
    pg = TranscriptPublisher()
    pg._observe({"type": "assistant",
                 "message": {"content": [{"type": "text", "text": "weighing the trade-off here"}]}},
                T0)
    check(pg.classify(T0 + 5) == "contemplative",
          f"(g) reflection-only window should be contemplative, got {pg.classify(T0 + 5)}")

    # (h) nothing recent -> grounded (idle / at rest).
    ph = TranscriptPublisher()
    check(ph.classify(T0) == "grounded",
          f"(h) empty window should be grounded, got {ph.classify(T0)}")

    # (i) a compact-summary boundary -> contemplative (mid-thought), even without narration text.
    pi = TranscriptPublisher()
    pi._observe({"type": "user", "isCompactSummary": True, "message": {"content": []}}, T0)
    check(pi.classify(T0 + 5) == "contemplative",
          f"(i) compact boundary should be contemplative, got {pi.classify(T0 + 5)}")

    # (j) tool DOMINATES reflection in the same window (working beats thinking-aloud).
    pj = TranscriptPublisher()
    pj._observe({"type": "assistant",
                 "message": {"content": [{"type": "text", "text": "let me check"}]}}, T0)
    pj._observe({"type": "assistant",
                 "message": {"content": [{"type": "tool_use", "id": "x", "name": "Bash",
                                          "input": {"description": "go"}}]}}, T0 + 1)
    check(pj.classify(T0 + 2) == "focused",
          f"(j) tool activity should dominate reflection -> focused, got {pj.classify(T0 + 2)}")

    # (k) change-gating: only TRANSITIONS publish; the enum is exactly the drawn faces.
    pk = TranscriptPublisher()
    e_start = pk.avatar_event(T0)                       # None->grounded : first transition publishes
    check(e_start == (NS_AVATAR, {"expression": "grounded", "ts": _now_ms(T0)}),
          f"(k) first classification should publish grounded baseline, got {e_start}")
    check(pk.avatar_event(T0 + 1) is None,
          "(k) an unchanged expression must NOT re-publish (no churn)")
    pk._observe({"type": "assistant",
                 "message": {"content": [{"type": "tool_use", "id": "x", "name": "Read",
                                          "input": {"file_path": "/a/b.py"}}]}}, T0 + 2)
    e_focus = pk.avatar_event(T0 + 3)
    check(e_focus is not None and e_focus[0] == NS_AVATAR
          and e_focus[1]["expression"] == "focused",
          f"(k) transition to focused should publish, got {e_focus}")
    check(all(pk.classify(T0 + d) in AVATAR_ENUM for d in (0, 3, 200)),
          "(k) classifier must only ever emit the drawn enum (grounded|focused|contemplative)")

    # ── (l) narration meta-filter ([SPEC:CXC-IC-STREAMS] IC-C16) ────────────
    # A harness/plumbing PLACEHOLDER text block must NEVER become narration;
    # real speech (even if it MENTIONS such a phrase) always is. This is the
    # enforcing guard for the 'No response requested.' leak defect.
    pl = TranscriptPublisher()
    for placeholder in ("No response requested.", "no response requested",
                        "(no content)", "[Request interrupted by user]"):
        evx = pl.process_entry({
            "type": "assistant", "timestamp": "2026-07-23T00:00:00Z",
            "message": {"content": [{"type": "text", "text": placeholder}]},
        })
        check(not any(n == NS_NARRATION for n, _ in evx),
              f"(l) meta placeholder leaked into narration: {placeholder!r} -> {evx}")
    # real speech that merely CONTAINS the phrase is still narrated (tight filter)
    ev_real = pl.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T00:00:01Z",
        "message": {"content": [{"type": "text",
                                 "text": "No response requested here means the turn was interrupted; here's why it matters."}]},
    })
    check(any(n == NS_NARRATION for n, _ in ev_real),
          f"(l) tight filter wrongly suppressed real narration: {ev_real}")
    # pure-helper unit check
    check(_is_meta_placeholder("No response requested.") and
          not _is_meta_placeholder("Both surface the new reality."),
          "(l) _is_meta_placeholder classifies placeholder vs speech")

    # ── (m) [CXC-IH-5A] carried-identity namespace rewrite of the EMITTED feeds ────────────
    # Every per-instance feed this publisher emits must scope `<family>/<rest>` -> `<family>/<id>/
    # <rest>` when namespace-by-id is on, so two intelligences never MERGE. We drive the SAME
    # scope_namespace the live emit() uses (families self+thread), proving the on-the-wire topic.
    for src_ns, want in (
        ("thread/narration", "thread/architect/narration"),
        ("thread/diagram", "thread/architect/diagram"),
        ("self/activity", "self/architect/activity"),
        ("self/subagents", "self/architect/subagents"),
        ("self/avatar/expression", "self/architect/avatar/expression"),
    ):
        got = scope_namespace(src_ns, "architect", _ID_FAMILIES)
        check(got == want, f"(m) feed {src_ns!r} must scope to {want!r}, got {got!r}")
    # a different carried id lands on a DISTINCT topic (the no-clobber / fleet property).
    check(scope_namespace(NS_NARRATION, "builder-1", _ID_FAMILIES) == "thread/builder-1/narration",
          "(m) a spawned id scopes to its OWN topic (coexists with the architect's)")
    check(scope_namespace(NS_NARRATION, "architect", _ID_FAMILIES)
          != scope_namespace(NS_NARRATION, "builder-1", _ID_FAMILIES),
          "(m) architect and builder-1 narration are DISTINCT topics (no merge)")

    # ── (n) a channel-echo USER record NEVER narrates ([SPEC:CXC-IC-STREAMS] IC-C18) ──
    # The "architect messages render TWICE" hypothesis was that the Intelligence's own
    # output round-trips into its transcript as a role=user record (the composeio-channel
    # echoes sent messages back into context) and is re-emitted as from=architect
    # narration. It CANNOT: narration derives ONLY from assistant TEXT blocks; a
    # type=user record is routed to the result-bearing path, which emits solely from
    # tool_result. (Forensics on the live JSONL: 0/3570 assistant texts ever appeared
    # inside a later user record.) This guard pins that BY CONSTRUCTION so a future
    # dedup/ignore-echo "fix" is never reached for.
    pn = TranscriptPublisher()
    own = "Both vectors found — the leaked relay doubled every message."
    ev_say = pn.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T19:47:33Z",
        "message": {"content": [{"type": "text", "text": own}]},
    })
    check(sum(1 for _n, _ in ev_say if _n == NS_NARRATION) == 1,
          f"(n) an assistant utterance must narrate EXACTLY ONCE: {ev_say}")
    # the channel echoes it back as a role=user record — string content (the real shape)
    ev_echo_str = pn.process_entry({
        "type": "user", "timestamp": "2026-07-23T19:48:00Z",
        "message": {"role": "user",
                    "content": ('<channel source="composeio-channel" id="architect" '
                                f'from="architect">{own}</channel>')},
    })
    check(not any(_n == NS_NARRATION for _n, _ in ev_echo_str),
          f"(n) a channel-echo user record (string) MUST NOT narrate: {ev_echo_str}")
    # ... and even if the echo arrives as a user TEXT BLOCK, still no narration
    ev_echo_block = pn.process_entry({
        "type": "user", "timestamp": "2026-07-23T19:48:01Z",
        "message": {"role": "user", "content": [{"type": "text", "text": own}]},
    })
    check(not any(_n == NS_NARRATION for _n, _ in ev_echo_block),
          f"(n) a channel-echo user text-block MUST NOT narrate: {ev_echo_block}")

    # ── (o) genuinely-repeated narration is NEVER dropped ([SPEC:CXC-IC-STREAMS] IC-C18) ──
    # The Intelligence legitimately re-replies the SAME short text to each periodic
    # harness heartbeat (observed live: "(Holding for David. No action.)" ~every 680s,
    # 30+ DISTINCT turns). Each is a real, distinct utterance that MUST render. Exactly-
    # once is enforced by the per-<id> SINGLETON (IC-C17, test_singleton_hardening.py) —
    # one emit per utterance per instance — NOT by content dedup, which would SUPPRESS
    # these legitimate repeats. This guard forecloses a dedup-shaped "fix".
    po = TranscriptPublisher()
    same = "(Holding for David. No action.)"
    ev_r1 = po.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T18:00:00Z",
        "message": {"content": [{"type": "text", "text": same}]},
    })
    ev_r2 = po.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T18:11:20Z",   # ~680s later, a NEW turn
        "message": {"content": [{"type": "text", "text": same}]},
    })
    n1 = sum(1 for _n, _ in ev_r1 if _n == NS_NARRATION)
    n2 = sum(1 for _n, _ in ev_r2 if _n == NS_NARRATION)
    check(n1 == 1 and n2 == 1,
          f"(o) a genuinely-repeated line must narrate BOTH times (no dedup): {n1},{n2}")

    # ── (p) async subagent lifecycle ([SPEC:CXC-IC-STREAMS] async-dispatch invariant, IC-C19) ──
    # The 3rd-recurrence subagent-chip defect: the current harness dispatches subagents
    # ASYNCHRONOUSLY. An Agent/Task tool_use opens a RUNNING chip, but the tool_result that
    # IMMEDIATELY follows is an async-launch ACK (entry-level toolUseResult.isAsync /
    # status "async_launched") — NOT completion. Reducing that ACK to done flipped the chip done
    # ~0.5s after dispatch, then the linger-prune swept it, so the card read "NO SUBAGENTS" even
    # while subagents ran. The TRUE completion is a later <task-notification> user record bearing
    # the ORIGINAL <tool-use-id> + a terminal <status>. This group is the enforcing guard.
    pp = TranscriptPublisher()
    ev_disp = pp.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T20:45:13Z",
        "message": {"content": [
            {"type": "tool_use", "id": "toolu_A", "name": "Agent",
             "input": {"subagent_type": "defect-investigator",
                       "description": "Investigate subagent chips",
                       "isolation": "worktree", "prompt": "…large sub-prompt…"}},
        ]},
    })
    disp = [p for n, p in ev_disp if n == NS_SUBAGENTS]
    check(len(disp) == 1 and disp[0]["status"] == "dispatched"
          and disp[0]["type"] == "defect-investigator" and disp[0]["id"] == "toolu_A",
          f"(p) an Agent tool_use must open a RUNNING chip (status:dispatched): {ev_disp}")
    # the immediate async-launch ACK must NOT close the chip — the subagent is still running.
    ev_ack = pp.process_entry({
        "type": "user", "timestamp": "2026-07-23T20:45:14Z",
        "toolUseResult": {"isAsync": True, "status": "async_launched",
                          "agentId": "abce24518295bcb27"},
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_A",
             "content": [{"type": "text",
                          "text": "Async agent launched successfully. agentId: abce…"}]},
        ]},
    })
    check(not any(n == NS_SUBAGENTS for n, _ in ev_ack),
          f"(p) the async-launch ACK MUST NOT emit a subagent event (chip stays running): {ev_ack}")
    # the TRUE completion: a <task-notification> user record with the ORIGINAL tool-use-id.
    notif = ("<task-notification>\n<task-id>abce24518295bcb27</task-id>\n"
             "<tool-use-id>toolu_A</tool-use-id>\n"
             "<output-file>/tmp/x.output</output-file>\n<status>completed</status>\n"
             "<summary>Agent \"Investigate subagent chips\" completed.</summary>\n"
             "</task-notification>")
    ev_done = pp.process_entry({
        "type": "user", "timestamp": "2026-07-23T20:52:00Z",
        "message": {"role": "user", "content": notif},
    })
    sdone = [p for n, p in ev_done if n == NS_SUBAGENTS]
    check(len(sdone) == 1 and sdone[0]["status"] == "done" and sdone[0]["id"] == "toolu_A"
          and sdone[0]["dur_s"] == 407.0,
          f"(p) <task-notification> must resolve the chip to done with the real duration: {ev_done}")
    # a task-notification for an UNKNOWN tool-use-id yields nothing (no phantom chip).
    pp2 = TranscriptPublisher()
    ev_orphan = pp2.process_entry({
        "type": "user", "timestamp": "2026-07-23T20:52:00Z",
        "message": {"role": "user",
                    "content": ("<task-notification><tool-use-id>toolu_UNKNOWN</tool-use-id>"
                                "<status>completed</status></task-notification>")},
    })
    check(not any(n == NS_SUBAGENTS for n, _ in ev_orphan),
          f"(p) a task-notification for an unknown dispatch must emit nothing: {ev_orphan}")
    # a SYNCHRONOUS (non-async) tool_result completion still resolves normally (both modes covered).
    ps = TranscriptPublisher()
    ps.process_entry({
        "type": "assistant", "timestamp": "2026-07-23T10:00:00Z",
        "message": {"content": [
            {"type": "tool_use", "id": "toolu_S", "name": "Task",
             "input": {"subagent_type": "lint", "description": "Lint specs"}},
        ]},
    })
    ev_sync = ps.process_entry({
        "type": "user", "timestamp": "2026-07-23T10:03:00Z",
        "toolUseResult": {"status": "completed"},
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_S", "content": "done"},
        ]},
    })
    sync_done = [p for n, p in ev_sync if n == NS_SUBAGENTS]
    check(len(sync_done) == 1 and sync_done[0]["status"] == "done"
          and sync_done[0]["dur_s"] == 180.0,
          f"(p) a synchronous (non-async) tool_result completion must still resolve done: {ev_sync}")

    # ── (q) task_status RECOVERY leg ([SPEC:CXC-IC-STREAMS] task_status recovery, IC-C21) ──
    # The 4th-recurrence subagent-chip defect: the tailer seeks to END and never re-reads history,
    # so a subagent ALREADY RUNNING when the publisher (re)starts is never seen dispatched and can
    # never open a chip — the console reads "NO SUBAGENTS" while it runs. The harness emits an
    # authoritative running/terminal state record (type:"attachment" / attachment.type:"task_status"
    # for background local_agent subagents) that MUST recover the chip. This is the enforcing guard.

    # (q1) RECOVERY: a fresh publisher that NEVER saw the dispatch opens a running chip from a
    # `running` task_status, keyed by taskId.
    pq = TranscriptPublisher()
    ev_ts_run = pq.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:40:05Z",
        "attachment": {"type": "task_status", "taskId": "a8044be58b46125b5",
                       "taskType": "local_agent", "description": "Build the 3-tab widget viewer",
                       "status": "running", "deltaSummary": "Reading Widget base class contract"},
    })
    rec = [p for n, p in ev_ts_run if n == NS_SUBAGENTS]
    check(len(rec) == 1 and rec[0]["status"] == "dispatched"
          and rec[0]["id"] == "a8044be58b46125b5"
          and rec[0]["desc"] == "Build the 3-tab widget viewer",
          f"(q1) a running task_status for an unwitnessed agent must OPEN a running chip: {ev_ts_run}")
    # (q2) a REPEAT running task_status for the now-tracked agent emits nothing (idempotent — no
    # churn, no second chip).
    ev_ts_run2 = pq.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:45:05Z",
        "attachment": {"type": "task_status", "taskId": "a8044be58b46125b5",
                       "taskType": "local_agent", "description": "Build the 3-tab widget viewer",
                       "status": "running", "deltaSummary": "Wiring the tab bar"},
    })
    check(not any(n == NS_SUBAGENTS for n, _ in ev_ts_run2),
          f"(q2) a repeat running task_status must be idempotent (no second chip): {ev_ts_run2}")
    # (q3) a TERMINAL task_status resolves the recovered chip to done (same taskId key).
    ev_ts_done = pq.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:50:05Z",
        "attachment": {"type": "task_status", "taskId": "a8044be58b46125b5",
                       "taskType": "local_agent", "description": "Build the 3-tab widget viewer",
                       "status": "completed", "deltaSummary": None},
    })
    fin = [p for n, p in ev_ts_done if n == NS_SUBAGENTS]
    check(len(fin) == 1 and fin[0]["status"] == "done" and fin[0]["id"] == "a8044be58b46125b5",
          f"(q3) a terminal task_status must resolve the recovered chip to done: {ev_ts_done}")

    # (q4) IDEMPOTENT with the tool_use path: when the dispatch WAS witnessed, the async-launch ACK
    # maps agentId->tool-use-id, so a `running` task_status for that agent addresses the SAME chip
    # and emits NOTHING (no double chip); a terminal task_status then resolves the witnessed chip.
    pq2 = TranscriptPublisher()
    pq2.process_entry({
        "type": "assistant", "timestamp": "2026-07-27T04:35:19Z",
        "message": {"content": [
            {"type": "tool_use", "id": "toolu_LIVE", "name": "Agent",
             "input": {"subagent_type": "render-engineer",
                       "description": "Build the 3-tab widget viewer"}},
        ]},
    })
    pq2.process_entry({   # async-launch ACK carrying agentId -> learns the mapping
        "type": "user", "timestamp": "2026-07-27T04:35:20Z",
        "toolUseResult": {"isAsync": True, "status": "async_launched",
                          "agentId": "a8044be58b46125b5"},
        "message": {"content": [
            {"type": "tool_result", "tool_use_id": "toolu_LIVE",
             "content": [{"type": "text", "text": "Async agent launched. agentId: a8044be5…"}]},
        ]},
    })
    ev_dup = pq2.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:40:05Z",
        "attachment": {"type": "task_status", "taskId": "a8044be58b46125b5",
                       "taskType": "local_agent", "description": "Build the 3-tab widget viewer",
                       "status": "running", "deltaSummary": "Reading Widget base class contract"},
    })
    check(not any(n == NS_SUBAGENTS for n, _ in ev_dup),
          f"(q4) a task_status for a WITNESSED agent must emit nothing (no double chip): {ev_dup}")
    ev_dup_done = pq2.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:52:00Z",
        "attachment": {"type": "task_status", "taskId": "a8044be58b46125b5",
                       "taskType": "local_agent", "description": "Build the 3-tab widget viewer",
                       "status": "completed", "deltaSummary": None},
    })
    dd = [p for n, p in ev_dup_done if n == NS_SUBAGENTS]
    check(len(dd) == 1 and dd[0]["status"] == "done" and dd[0]["id"] == "toolu_LIVE",
          f"(q4) a terminal task_status must resolve the WITNESSED chip (keyed by tool-use-id): {ev_dup_done}")

    # (q5) a TERMINAL task_status for an agent we NEVER opened emits nothing (no phantom done).
    pq3 = TranscriptPublisher()
    ev_orphan_done = pq3.process_entry({
        "type": "attachment", "timestamp": "2026-07-27T04:52:00Z",
        "attachment": {"type": "task_status", "taskId": "aUNSEEN", "taskType": "local_agent",
                       "description": "never shown", "status": "completed", "deltaSummary": None},
    })
    check(not any(n == NS_SUBAGENTS for n, _ in ev_orphan_done),
          f"(q5) a terminal task_status for a never-opened agent must emit nothing: {ev_orphan_done}")

    if failures:
        sys.stderr.write(f"\n{len(failures)} FAILURE(S)\n")
        return 1
    sys.stdout.write("PASS: TranscriptPublisher selftest (17/17 groups)\n")
    for line in (
        "  (a) assistant TEXT -> thread/narration (NEVER the .../out actuation channel); THINKING dropped; tool_use -> self/activity",
        "  (b) tool_result closes activity (phase:done + duration); result CONTENT never republished",
        "  (c) Task/Agent tool_use -> self/subagents dispatched (type/model/desc)",
        "  (d) subagent completion -> self/subagents done (+ duration + tokens)",
        "  (e) a tool-only turn emits no narration; Bash activity target = its description",
        "  (f) recent tool_use -> self/avatar/expression=focused; decays to grounded past the window",
        "  (g) reflection/narration, no tools -> contemplative",
        "  (h) empty/idle window -> grounded",
        "  (i) compact-summary boundary -> contemplative",
        "  (j) tool activity dominates reflection -> focused",
        "  (k) avatar publishes only on CHANGE; enum is exactly the drawn faces",
        "  (l) narration meta-filter: harness placeholders ('No response requested.') never narrate; real speech always does",
        "  (m) [CXC-IH-5A] --namespace-by-id scopes every feed <family>/<rest> -> <family>/<id>/<rest> (self+thread); distinct ids never merge",
        "  (n) a channel-echo user record (the Intelligence's own output round-tripped) NEVER narrates — narration is assistant-text-only, by construction",
        "  (o) a genuinely-repeated line narrates BOTH times — exactly-once is the singleton (IC-C17), NEVER a content dedup that would suppress legitimate repeats",
        "  (p) [CXC-IC-STREAMS async-dispatch / IC-C19] a subagent chip stays RUNNING through the async-launch ACK and resolves to done only on the <task-notification> (or a synchronous tool_result) — never flips done ~0.5s after dispatch",
        "  (q) [CXC-IC-STREAMS task_status recovery / IC-C21] a task_status attachment RECOVERS a running chip for a subagent whose dispatch the tailer never witnessed (seek-to-end relaunch); idempotent with the tool_use path (no double chip), no phantom done",
    ):
        sys.stdout.write(line + "\n")
    return 0


def _classify_file(jsonl_path: str) -> int:
    """Feed a whole JSONL through the classifier and report the expression it would publish at the
    moment of the last recorded activity — a dry run that touches NO broker (never disrupts a
    running daemon)."""
    pub = TranscriptPublisher()
    last_ts: float | None = None
    n = 0
    with open(jsonl_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            pub.process_entry(entry)
            n += 1
            ts = _iso_to_epoch(entry.get("timestamp"))
            if ts is not None:
                last_ts = ts
    now = last_ts if last_ts is not None else time.time()
    expr = pub.classify(now)
    window = [c for (t, c) in pub._recent if t >= now - AVATAR_WINDOW_S]
    sys.stdout.write(
        f"classified {n} entries; last activity ts={now}\n"
        f"recent-window categories (<= {AVATAR_WINDOW_S:.0f}s): {window}\n"
        f"-> would publish self/avatar/expression = {expr!r}\n")
    return 0


def main(argv: list[str], *, bus=None, stop=None) -> int:
    if "--selftest" in argv:
        return _selftest()
    # arg parsing (minimal)
    def opt(flag, default=None):
        return argv[argv.index(flag) + 1] if flag in argv else default

    if "--classify" in argv:
        path = opt("--classify")
        if not path:
            sys.stderr.write("usage: transcript_publisher.py --classify <jsonl>\n")
            return 2
        return _classify_file(path)

    jsonl = opt("--jsonl")
    if not jsonl:
        sys.stderr.write("usage: transcript_publisher.py --jsonl <path> "
                         "[--id architect] [--mqtt mqtt://host:port] [--from-start] "
                         "[--namespace-by-id]\n")
        return 2
    intelligence_id = opt("--id", os.environ.get("INTELLIGENCE_ID", "architect"))
    mqtt_url = opt("--mqtt", os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    # [CXC-IH-5A] carried-identity: default OFF (legacy bare topics — a non-migrated console is
    # untouched); `--namespace-by-id` opts this publisher into `<family>/<id>/*`, id from --id /
    # $INTELLIGENCE_ID (CARRIED, never guessed) — mirrors self_publisher EXACTLY.
    namespace_by_id = "--namespace-by-id" in argv
    return run(jsonl, intelligence_id, mqtt_url, from_start="--from-start" in argv,
               namespace_by_id=namespace_by_id, bus=bus, stop=stop)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
