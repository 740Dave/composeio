"""SessionSweep — a DETERMINISTIC end-of-session "loose ends" linter.

Sibling to :mod:`composeio.self_sensor`.  Where the self-sensor answers *how
much fuel is left*, the sweep answers *what did we leave on the floor* — the
work both parties started and dropped when a tangent pulled them away.  It runs
at dusk, BEFORE the handoff / ``/compact`` is authored, so the Intelligence can
fold real findings into the bequest and raise items for the human.

Design law (from ``bootstrap/dusk-gifts/README.md``): *"the author cannot see their
own omissions from inside; an external check is load-bearing."*  This tool IS
that external check, made **deterministic** — no LLM, no network.  Like a
linter, it SURFACES candidates WITH EVIDENCE and a confidence grade; it NEVER
decides what is truly a loose end.  The Intelligence (and the human) triage.

Parsing idiom is inherited from ``self_sensor/sensor.py`` (iterate JSONL
entries, read ``entry['type']=='assistant'`` -> ``message.content`` tool_use
blocks) but the sweep extracts MORE fields: Write/Edit/MultiEdit file_paths,
Bash commands + their tool_result exit/error, Task/Agent dispatch + completion,
AskUserQuestion, user messages, and assistant text.

Confidence is HONEST:

  * **HIGH** — git-verifiable ground truth (a file is dirty *right now*; the
    tree is out of sync *right now*).  Not an inference.
  * **MED**  — a structural pattern in the transcript (a dispatched agent with
    no committed result; an error with no corrective follow-up; an ask only
    partially answered).  Strong signal, but the reader confirms intent.
  * **LOW**  — a linguistic candidate (a forward-commitment phrase with no
    later closure).  Explicitly a *candidate*: high recall, low precision.

Six detectors:

  1. uncommitted_authored (HIGH) — files this session Wrote/Edited that are
     STILL dirty / untracked in their git repo.
  2. git_out_of_sync      (HIGH) — dirty tree, staged-uncommitted, unpushed /
     behind upstream, stashes, detached HEAD, non-default branch.
  3. subagent_disposition (MED)  — a Task/Agent dispatch whose completion is
     missing, or whose reported output was never committed.
  4. unanswered_questions (MED)  — an AskUserQuestion the user's next turn did
     not address, or a user ask ("do X and Y") only partially fulfilled later.
  5. unhandled_errors     (MED)  — a Bash/tool error with no corrective action
     on the same target afterward.
  6. open_loops           (LOW)  — a forward-commitment phrase ("I'll ... next",
     "TODO", "still need") in my text with no later closure.

Each finding notes whether it lands before/after the last ``compact_boundary``.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- #
# Confidence ordering (HIGH first when ranking).
# --------------------------------------------------------------------------- #
CONFIDENCE_RANK = {"HIGH": 0, "MED": 1, "LOW": 2}

# Forward-commitment markers (detector 6).  A hit means MY text promised future
# work; the sweep then checks whether a LATER action plausibly closed it.
OPEN_LOOP_RE = re.compile(
    r"\b(i'?ll|i will|next step|next,|pending|todo|to-do|later slice|parked|"
    r"flag(?:ged)? for|not started|still need|follow[- ]up|to do|"
    r"waiting on|blocked on|need to|going to|will add|will build|will wire)\b",
    re.IGNORECASE,
)

# STRONG error signatures in a tool_result body (detector 5), used ONLY when the
# authoritative ``is_error`` flag is absent.  Deliberately narrow: a bare
# "error"/"failed" substring matches far too much benign output (log lines,
# "0 errors", grep hits), so we require unambiguous failure markers.
ERROR_RE = re.compile(
    r"(traceback \(most recent call last\)|"
    r"no such file or directory|command not found|"
    r"permission denied|fatal: |segmentation fault|"
    r"\bexit code [1-9]|\bexit status [1-9]|"
    r"modulenotfounderror|importerror|syntaxerror|assertionerror|"
    r"<tool_use_error>)",
    re.IGNORECASE,
)

# STRONG, unambiguous imperative-action verbs that mark a user clause as a
# concrete deliverable ask (detector 4b).  Deliberately narrow: weak cues
# (need/want/check/support/ensure) match too much conversational prose and turn
# a conjunction-adjacent adjective into a phantom "dropped" item.
STRONG_ACTION_RE = re.compile(
    r"\b(build|add|fix|write|update|create|implement|wire|remove|delete|"
    r"refactor|rename|deploy|make|install|set up|setup)\b",
    re.IGNORECASE,
)
_ACTION_STOP = ("build", "add", "fix", "write", "update", "create", "implement",
                "wire", "remove", "delete", "refactor", "rename", "deploy",
                "make", "install", "setup", "please", "also")

# Tokens too generic to be a "salient keyword" for recurrence/closure matching.
_STOPWORDS = frozenset("""
a an the and or but if then else for to of in on at by with from into out up
down over under this that these those it its it's you your we our i i'll i'm me
my he she they them their is are was were be been being do does did done have
has had will would should could can may might must shall not no yes so as also
just now next later still need needs want please make made get got go going
about which what when where who why how all any some more most other another
thing things stuff both one two also them here there file files code work
""".split())

_WORD_RE = re.compile(r"[A-Za-z0-9_./-]{4,}")


def _salient_tokens(text: str, extra_stop: Iterable[str] = ()) -> set[str]:
    """Lowercased content tokens (len>=4, minus stopwords) used for closure and
    recurrence heuristics.  Path-ish tokens are also reduced to their basename
    so ``config.yaml`` matches a later mention of ``config``."""
    stop = _STOPWORDS | {s.lower() for s in extra_stop}
    out: set[str] = set()
    for m in _WORD_RE.findall(text or ""):
        tok = m.lower().strip("./-")
        if not tok or tok in stop:
            continue
        out.add(tok)
        if "/" in tok:
            base = tok.rsplit("/", 1)[-1]
            if base and base not in stop:
                out.add(base)
        if "." in tok:
            stem = tok.rsplit(".", 1)[0].rsplit("/", 1)[-1]
            if len(stem) >= 4 and stem not in stop:
                out.add(stem)
    return out


def _first_sentence(text: str, marker_end: int) -> str:
    """The sentence containing a marker hit (for open-loop evidence): from the
    prior sentence boundary to the next one."""
    start = max(text.rfind(".", 0, marker_end),
                text.rfind("\n", 0, marker_end),
                text.rfind("!", 0, marker_end),
                text.rfind("?", 0, marker_end)) + 1
    end_candidates = [i for i in (text.find(".", marker_end),
                                  text.find("\n", marker_end),
                                  text.find("!", marker_end)) if i != -1]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    return text[start:end].strip()


# --------------------------------------------------------------------------- #
# Git — self-contained (deliberately overlaps a parallel git_sensor; we call
# git ourselves so there is no build-order race).  Read-only, no fetch/network.
# --------------------------------------------------------------------------- #

class GitProbe:
    """Thin, deterministic git query surface.  Every method is read-only and
    offline (no fetch/pull); upstream ahead/behind is computed against the
    already-known ``@{upstream}`` ref only."""

    def __init__(self, timeout: float = 10.0):
        self.timeout = timeout
        self._toplevel_cache: dict[str, Optional[str]] = {}

    def _run(self, cwd: str, *args: str) -> tuple[int, str]:
        try:
            p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                               text=True, timeout=self.timeout)
            return p.returncode, (p.stdout or "").strip()
        except Exception as exc:  # git missing / dir gone / timeout
            return 127, f"<git-error: {exc}>"

    def toplevel(self, path: str) -> Optional[str]:
        """Repo root enclosing ``path`` (a file or dir), or None if untracked
        territory / not a repo."""
        d = str(Path(path).parent if Path(path).suffix or not Path(path).is_dir()
                else path)
        if d in self._toplevel_cache:
            return self._toplevel_cache[d]
        rc, out = self._run(d, "rev-parse", "--show-toplevel")
        top = out if rc == 0 and out and not out.startswith("<git-error") else None
        self._toplevel_cache[d] = top
        return top

    def file_state(self, file: str) -> str:
        """One of: ``clean`` (tracked + committed), ``dirty`` (modified/staged),
        ``untracked`` (never committed), ``no_repo`` (not under any git repo)."""
        top = self.toplevel(file)
        if not top:
            return "no_repo"
        rc, out = self._run(top, "status", "--porcelain", "--", file)
        if rc != 0:
            return "no_repo"
        if not out:
            return "clean"
        code = out.splitlines()[0][:2]
        if code == "??":
            return "untracked"
        return "dirty"

    def sync_state(self, root: str) -> dict[str, Any]:
        """Whole-tree sync facts for a repo root: dirty/staged counts, branch,
        detached, ahead/behind upstream, stash count."""
        st: dict[str, Any] = {"root": root}
        rc, out = self._run(root, "status", "--porcelain")
        dirty = staged = 0
        for line in (out.splitlines() if rc == 0 else []):
            xy = line[:2]
            if xy == "??":
                dirty += 1
            else:
                if xy[0] not in " ?":
                    staged += 1
                if xy[1] not in " ":
                    dirty += 1
        st["dirty"] = dirty
        st["staged"] = staged

        rc, branch = self._run(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        st["detached"] = rc != 0
        st["branch"] = branch if rc == 0 else None

        rc, up = self._run(root, "rev-parse", "--abbrev-ref",
                           "--symbolic-full-name", "@{upstream}")
        st["upstream"] = up if rc == 0 and "<git-error" not in up else None
        if st["upstream"]:
            _, ahead = self._run(root, "rev-list", "--count", "@{upstream}..HEAD")
            _, behind = self._run(root, "rev-list", "--count", "HEAD..@{upstream}")
            st["ahead"] = int(ahead) if ahead.isdigit() else 0
            st["behind"] = int(behind) if behind.isdigit() else 0
        else:
            st["ahead"] = st["behind"] = 0

        _, stash = self._run(root, "stash", "list")
        st["stashes"] = len([l for l in stash.splitlines() if l.strip()]) \
            if "<git-error" not in stash else 0

        # default branch guess (offline): origin/HEAD symbolic ref, else main.
        rc, dh = self._run(root, "rev-parse", "--abbrev-ref", "origin/HEAD")
        st["default_branch"] = (dh.split("/", 1)[-1] if rc == 0 and "/" in dh
                                else "main")
        return st


# --------------------------------------------------------------------------- #
# The parsed transcript — a single pass extracting every field the detectors
# need, each stamped with a turn index and (when present) a timestamp.
# --------------------------------------------------------------------------- #

class _Transcript:
    """One deterministic pass over the session ``.jsonl``.

    ``turn`` is the entry's ordinal among parsed lines (stable, monotonic).
    ``tool_use_by_id`` links a Bash/tool result back to the call that made it so
    error handling can name the *target* (the file/command that failed)."""

    def __init__(self, jsonl_path: str):
        self.path = Path(jsonl_path)
        self.writes: list[dict] = []            # {file, turn, tool}
        self.bash: list[dict] = []              # {command, turn, id}
        self.tool_results: list[dict] = []      # {id, text, is_error, turn}
        self.tool_use_by_id: dict[str, dict] = {}  # id -> {name, input, turn}
        self.tasks: list[dict] = []             # {desc, type, turn, id}
        self.task_completions: list[dict] = []  # {task_id, tool_use_id, summary, status, turn}
        self.asks: list[dict] = []              # AskUserQuestion {questions, turn}
        self.assistant_text: list[dict] = []    # {text, turn}
        self.user_text: list[dict] = []         # {text, turn}
        self.compact_turns: list[int] = []
        self.max_turn = 0

    def parse(self) -> "_Transcript":
        turn = 0
        with self.path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                turn += 1
                self.max_turn = turn
                t = e.get("type")

                if t == "assistant":
                    self._assistant(e, turn)
                elif t == "user":
                    self._user(e, turn, _is_human_turn(e))
                elif t == "system":
                    if e.get("subtype") == "compact_boundary" or \
                            "compact_boundary" in json.dumps(e):
                        self.compact_turns.append(turn)
                elif t == "queue-operation":
                    self._maybe_task_notification(e.get("content"), turn)
        return self

    # -- assistant: tool_use blocks + assistant free text -------------------- #
    def _assistant(self, e: dict, turn: int) -> None:
        msg = e.get("message") or {}
        for b in (msg.get("content") or []):
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                if txt.strip():
                    self.assistant_text.append({"text": txt, "turn": turn})
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input") or {}
                bid = b.get("id")
                if bid:
                    self.tool_use_by_id[bid] = {"name": name, "input": inp,
                                                "turn": turn}
                if name in ("Write", "Edit", "MultiEdit"):
                    fp = inp.get("file_path")
                    if fp:
                        self.writes.append({"file": fp, "turn": turn, "tool": name})
                elif name == "Bash":
                    self.bash.append({"command": inp.get("command", ""),
                                      "turn": turn, "id": bid})
                elif name in ("Task", "Agent"):
                    self.tasks.append({
                        "desc": inp.get("description", ""),
                        "type": inp.get("subagent_type", "agent"),
                        "turn": turn, "id": bid})
                elif name == "AskUserQuestion":
                    self.asks.append({"questions": inp.get("questions"),
                                      "turn": turn})

    # -- user: tool_result payloads + user free text ------------------------- #
    def _user(self, e: dict, turn: int, human: bool) -> None:
        # ``human`` gates user_text: only a GENUINE human turn counts as an
        # "ask" (tool results, meta, sidechain, and injected notifications are
        # user-typed entries but not questions the human is waiting on).
        msg = e.get("message") or {}
        cont = msg.get("content")
        if isinstance(cont, str):
            if human and cont.strip():
                self.user_text.append({"text": cont, "turn": turn})
            return
        if not isinstance(cont, list):
            return
        for b in cont:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                txt = b.get("text") or ""
                if human and txt.strip():
                    self.user_text.append({"text": txt, "turn": turn})
            elif bt == "tool_result":
                self.tool_results.append({
                    "id": b.get("tool_use_id"),
                    "text": _flatten_content(b.get("content")),
                    "is_error": bool(b.get("is_error")),
                    "turn": turn})
                # a task-notification can also ride inside a tool_result string
                self._maybe_task_notification(_flatten_content(b.get("content")),
                                              turn)

    # -- <task-notification> blocks (agent + background-bash completions) ---- #
    def _maybe_task_notification(self, content: Any, turn: int) -> None:
        if not isinstance(content, str) or "<task-notification>" not in content:
            return
        for block in re.findall(r"<task-notification>.*?</task-notification>",
                                content, re.DOTALL):
            self.task_completions.append({
                "task_id": _tag(block, "task-id"),
                "tool_use_id": _tag(block, "tool-use-id"),
                "status": _tag(block, "status"),
                "summary": _tag(block, "summary"),
                "turn": turn})

    def last_compact_turn(self) -> Optional[int]:
        return self.compact_turns[-1] if self.compact_turns else None


_INJECTED_MARKERS = ("<task-notification>", "<system-reminder>",
                     "tool_use_error", "<local-command", "<command-name>",
                     "Caveat: The messages below", "[Request interrupted")


def _is_human_turn(e: dict) -> bool:
    """True iff a ``type==user`` entry is a GENUINE human message — not a tool
    result, meta/compact/sidechain entry, or injected notification.  Uses the
    Claude Code JSONL markers: a real human turn carries no ``toolUseResult``,
    is not ``isMeta``/``isCompactSummary``/``isSidechain``, and its text does
    not embed an injected block."""
    if e.get("toolUseResult") is not None:
        return False
    if e.get("isMeta") or e.get("isCompactSummary") or e.get("isSidechain"):
        return False
    msg = e.get("message") or {}
    cont = msg.get("content")
    text = cont if isinstance(cont, str) else ""
    if isinstance(cont, list):
        text = " ".join(str(b.get("text", "")) for b in cont
                        if isinstance(b, dict) and b.get("type") == "text")
    return not any(m in text for m in _INJECTED_MARKERS)


def _tag(block: str, tag: str) -> Optional[str]:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    return m.group(1).strip() if m else None


def _flatten_content(content: Any) -> str:
    """A tool_result ``content`` is a str OR a list of blocks; reduce to text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                parts.append(str(b.get("text", b)))
            else:
                parts.append(str(b))
        return "\n".join(parts)
    return "" if content is None else str(content)


# --------------------------------------------------------------------------- #
# The report.
# --------------------------------------------------------------------------- #

class SweepReport:
    """A ranked collection of findings + its readouts.

    A *finding* is::

        {category, confidence: HIGH|MED|LOW, summary,
         evidence: {turn?, file?, text?, git?, command?},
         suggested_disposition, after_compact: bool|None}
    """

    def __init__(self, findings: list[dict], meta: dict):
        # rank: HIGH first, then most-recent (highest turn) first.
        self.findings = sorted(
            findings,
            key=lambda f: (CONFIDENCE_RANK.get(f["confidence"], 9),
                           -(f.get("evidence", {}).get("turn") or 0)))
        self.meta = meta

    # -- machine-readable ---------------------------------------------------- #
    def to_json(self, indent: int = 2) -> str:
        return json.dumps({"meta": self.meta, "findings": self.findings},
                          indent=indent, default=str)

    def by_category(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self.findings:
            out[f["category"]] = out.get(f["category"], 0) + 1
        return out

    def to_bus_lines(self) -> list[tuple[str, object]]:
        """The ComposeBus publish set for the console (``self/loose_ends/*``)."""
        top = [f["summary"] for f in self.findings[:5]]
        return [
            ("self/loose_ends/count", len(self.findings)),
            ("self/loose_ends/by_category", self.by_category()),
            ("self/loose_ends/top", top),
        ]

    # -- human-readable (ASCII, mirrors self_sensor's readout) --------------- #
    def render(self) -> str:
        L: list[str] = []
        L.append("=" * 68)
        L.append("  SESSION SWEEP  ·  loose-ends readout (deterministic; surfaces,")
        L.append("  does NOT decide — the Intelligence/human triages)")
        L.append("=" * 68)
        src = self.meta.get("source", "?")
        L.append(f"  source        {src}")
        L.append(f"  turns parsed  {self.meta.get('turns', 0):,}")
        cb = self.meta.get("last_compact_turn")
        L.append(f"  last compact  {'turn ' + str(cb) if cb else 'none this session'}")
        cats = self.by_category()
        L.append(f"  findings      {len(self.findings)}  "
                 + ("  ".join(f"{k}={v}" for k, v in sorted(cats.items())) or "(clean)"))
        L.append("-" * 68)
        if not self.findings:
            L.append("  no loose ends surfaced — nothing left on the floor (that this")
            L.append("  deterministic check can see).  Author the handoff with a clear tree.")
            return "\n".join(L)
        for i, f in enumerate(self.findings, 1):
            ev = f.get("evidence", {})
            scope = ""
            if f.get("after_compact") is True:
                scope = "  [post-compact]"
            elif f.get("after_compact") is False:
                scope = "  [pre-compact]"
            L.append(f"  {i:>2}. [{f['confidence']:<4}] {f['category']}{scope}")
            L.append(f"      {f['summary']}")
            if ev.get("file"):
                L.append(f"      file:    {ev['file']}")
            if ev.get("command"):
                L.append(f"      cmd:     {_clip(ev['command'], 58)}")
            if ev.get("git"):
                L.append(f"      git:     {ev['git']}")
            if ev.get("text"):
                L.append(f"      text:    \"{_clip(ev['text'], 56)}\"")
            if ev.get("turn"):
                L.append(f"      turn:    {ev['turn']}")
            L.append(f"      -> {f['suggested_disposition']}")
        L.append("-" * 68)
        L.append("  Grades: HIGH = git-verified now · MED = transcript pattern ·")
        L.append("          LOW = linguistic candidate (high recall, triage before use)")
        return "\n".join(L)


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


# --------------------------------------------------------------------------- #
# The sweep.
# --------------------------------------------------------------------------- #

class SessionSweep:
    """Read a session ``.jsonl``, run the detectors, return a :class:`SweepReport`.

    ``repo`` scopes the whole-tree git detector (default: cwd).  The
    per-file ``uncommitted_authored`` detector locates each written file's own
    repo, so a session that touched several repos is handled correctly.
    """

    def __init__(self, jsonl_path: str, repo: Optional[str] = None,
                 git: Optional[GitProbe] = None):
        self.jsonl_path = jsonl_path
        self.repo = repo or str(Path.cwd())
        self.git = git or GitProbe()

    def run(self) -> SweepReport:
        tx = _Transcript(self.jsonl_path).parse()
        last_compact = tx.last_compact_turn()

        findings: list[dict] = []
        findings += self._d_uncommitted_authored(tx)
        findings += self._d_git_out_of_sync(tx)
        findings += self._d_subagent_disposition(tx)
        findings += self._d_unanswered_questions(tx)
        findings += self._d_unhandled_errors(tx)
        findings += self._d_open_loops(tx)

        for f in findings:
            turn = f.get("evidence", {}).get("turn")
            f["after_compact"] = (None if last_compact is None or turn is None
                                  else turn > last_compact)

        meta = {
            "source": Path(self.jsonl_path).name,
            "repo": self.repo,
            "turns": tx.max_turn,
            "last_compact_turn": last_compact,
            "compact_boundaries": len(tx.compact_turns),
            "writes": len(tx.writes),
            "bash_calls": len(tx.bash),
            "tasks": len(tx.tasks),
        }
        return SweepReport(findings, meta)

    # -- 1. uncommitted_authored (HIGH) ------------------------------------- #
    def _d_uncommitted_authored(self, tx: _Transcript) -> list[dict]:
        # last edit turn per file (recency for ranking + evidence).
        last_edit: dict[str, dict] = {}
        for w in tx.writes:
            last_edit[w["file"]] = w
        out: list[dict] = []
        for file, w in last_edit.items():
            state = self.git.file_state(file)
            if state in ("dirty", "untracked"):
                verb = ("uncommitted changes" if state == "dirty"
                        else "never committed (untracked)")
                out.append({
                    "category": "uncommitted_authored",
                    "confidence": "HIGH",
                    "summary": f"Authored file has {verb}: {Path(file).name}",
                    "evidence": {"file": file, "turn": w["turn"],
                                 "git": f"git status: {state}"},
                    "suggested_disposition":
                        "commit it, or note in the handoff why it stays dirty.",
                })
        return out

    # -- 2. git_out_of_sync (HIGH) ------------------------------------------ #
    def _d_git_out_of_sync(self, tx: _Transcript) -> list[dict]:
        top = self.git.toplevel(self.repo)
        if not top:
            return []
        st = self.git.sync_state(top)
        out: list[dict] = []

        def add(summary: str, gitline: str, dispo: str) -> None:
            out.append({"category": "git_out_of_sync", "confidence": "HIGH",
                        "summary": summary,
                        "evidence": {"git": gitline, "file": top},
                        "suggested_disposition": dispo})

        if st.get("dirty"):
            add(f"Working tree has {st['dirty']} uncommitted change(s)",
                f"{st['dirty']} dirty path(s) in {Path(top).name}",
                "commit or stash before handoff, or record why left dirty.")
        if st.get("staged"):
            add(f"{st['staged']} change(s) staged but not committed",
                f"{st['staged']} staged path(s)",
                "finish the commit you started, or unstage.")
        if st.get("ahead"):
            add(f"{st['ahead']} commit(s) ahead of upstream (unpushed)",
                f"ahead {st['ahead']} of {st.get('upstream')}",
                "push, or note the branch is intentionally local.")
        if st.get("behind"):
            add(f"{st['behind']} commit(s) behind upstream",
                f"behind {st['behind']} of {st.get('upstream')}",
                "note for the successor: pull before continuing.")
        if st.get("stashes"):
            add(f"{st['stashes']} stash entr(y/ies) present",
                f"{st['stashes']} stash(es)",
                "a stash is invisible work — pop/apply or document it.")
        if st.get("detached"):
            add("HEAD is detached (commits here are easy to lose)",
                "detached HEAD",
                "create a branch to anchor any work made here.")
        elif st.get("branch") and st.get("branch") != st.get("default_branch"):
            add(f"On non-default branch '{st['branch']}' "
                f"(default '{st.get('default_branch')}')",
                f"branch={st['branch']}",
                "confirm the successor knows which branch holds the work.")
        return out

    # -- 3. subagent_disposition (MED) -------------------------------------- #
    def _d_subagent_disposition(self, tx: _Transcript) -> list[dict]:
        # index completions by tool_use_id and by fuzzy summary match.
        by_id = {c["tool_use_id"]: c for c in tx.task_completions
                 if c.get("tool_use_id")}
        uncommitted_kw = ("uncommitt", "not committed", "still dirty",
                          "did not commit", "left uncommitted", "not pushed",
                          "un-committed", "unpushed", "needs commit",
                          "to be committed", "not yet committed")
        out: list[dict] = []
        for task in tx.tasks:
            comp = by_id.get(task["id"])
            if comp is None:
                # no completion notification recorded for this dispatch.
                out.append({
                    "category": "subagent_disposition", "confidence": "MED",
                    "summary": f"Dispatched agent has no recorded completion: "
                               f"{_clip(task['desc'], 44)}",
                    "evidence": {"turn": task["turn"],
                                 "text": f"{task['type']}: {task['desc']}"},
                    "suggested_disposition":
                        "confirm the agent finished and its output was captured.",
                })
                continue
            summary = (comp.get("summary") or "")
            low = summary.lower()
            if any(k in low for k in uncommitted_kw):
                out.append({
                    "category": "subagent_disposition", "confidence": "MED",
                    "summary": f"Agent reported UNCOMMITTED output: "
                               f"{_clip(task['desc'], 40)}",
                    "evidence": {"turn": comp["turn"],
                                 "text": _clip(summary, 80)},
                    "suggested_disposition":
                        "the architect verifies + commits the agent's work.",
                })
        return out

    # -- 4. unanswered_questions (MED) -------------------------------------- #
    def _d_unanswered_questions(self, tx: _Transcript) -> list[dict]:
        out: list[dict] = []

        # (a) AskUserQuestion whose NEXT user message doesn't address it.
        for ask in tx.asks:
            qtext = _questions_text(ask.get("questions"))
            nxt = _next_after(tx.user_text, ask["turn"])
            if nxt is None:
                out.append({
                    "category": "unanswered_questions", "confidence": "MED",
                    "summary": "AskUserQuestion posed with no user reply after it",
                    "evidence": {"turn": ask["turn"], "text": _clip(qtext, 80)},
                    "suggested_disposition":
                        "the question is still open — carry it to the human.",
                })
                continue
            keys = _salient_tokens(qtext)
            reply_tok = _salient_tokens(nxt["text"])
            if keys and not (keys & reply_tok):
                out.append({
                    "category": "unanswered_questions", "confidence": "MED",
                    "summary": "User's next turn does not address the "
                               "AskUserQuestion topic",
                    "evidence": {"turn": ask["turn"], "text": _clip(qtext, 80)},
                    "suggested_disposition":
                        "verify the question was actually answered, not skipped.",
                })

        # (b) A user ask ("do X and Y") only partially fulfilled later.
        later_actions = self._action_corpus(tx)
        for um in tx.user_text:
            clauses = _ask_clauses(um["text"])
            if len(clauses) < 2:
                continue
            corpus = _corpus_after(later_actions, um["turn"])
            dropped: list[str] = []
            recurred = 0
            for kw in clauses:
                if kw and kw in corpus:
                    recurred += 1
                elif kw:
                    dropped.append(kw)
            # partial fulfillment: some recurred, at least one dropped.
            if recurred >= 1 and dropped:
                out.append({
                    "category": "unanswered_questions", "confidence": "MED",
                    "summary": f"User asked for {recurred + len(dropped)} things; "
                               f"no later trace of: {', '.join(dropped)}",
                    "evidence": {"turn": um["turn"],
                                 "text": _clip(um["text"], 80)},
                    "suggested_disposition":
                        "check whether the dropped item(s) were done or forgotten.",
                })
        return out

    # -- 5. unhandled_errors (MED) ------------------------------------------ #
    def _d_unhandled_errors(self, tx: _Transcript) -> list[dict]:
        out: list[dict] = []
        # future bash commands + writes, for "was the target retried?" check.
        for r in tx.tool_results:
            is_err = r["is_error"] or bool(ERROR_RE.search(r["text"] or ""))
            if not is_err:
                continue
            origin = tx.tool_use_by_id.get(r["id"] or "", {})
            name = origin.get("name", "tool")
            inp = origin.get("input", {})
            target_tokens = self._error_target_tokens(name, inp)
            command = inp.get("command") if name == "Bash" else None
            # corrective action = a LATER bash/write that touches the same target.
            handled = False
            if target_tokens:
                for w in tx.writes:
                    if w["turn"] > r["turn"] and \
                            _salient_tokens(w["file"]) & target_tokens:
                        handled = True
                        break
                if not handled:
                    for b in tx.bash:
                        if b["turn"] > r["turn"] and \
                                _salient_tokens(b["command"]) & target_tokens:
                            handled = True
                            break
            if handled:
                continue
            snippet = _error_snippet(r["text"])
            out.append({
                "category": "unhandled_errors", "confidence": "MED",
                "summary": f"{name} error with no corrective follow-up on its target",
                "evidence": {"turn": r["turn"],
                             "command": command,
                             "text": snippet},
                "suggested_disposition":
                    "confirm this failure was intentional/ignorable, else fix it.",
            })
        return out

    def _error_target_tokens(self, name: str, inp: dict) -> set[str]:
        if name == "Bash":
            # Only PATH-LIKE tokens identify the target (a filename), never a
            # bare binary or a shared parent dir — so `python3 a/missing.py`
            # failing is NOT "handled" merely because a later cmd touches `a/`.
            return _path_tokens(inp.get("command", ""))
        for key in ("file_path", "path", "notebook_path"):
            if inp.get(key):
                return _salient_tokens(inp[key])
        return set()

    # -- 6. open_loops (LOW) ------------------------------------------------- #
    def _d_open_loops(self, tx: _Transcript) -> list[dict]:
        out: list[dict] = []
        corpus_by_turn = self._action_corpus(tx)
        seen: set[str] = set()
        for at in tx.assistant_text:
            text = at["text"]
            for m in OPEN_LOOP_RE.finditer(text):
                sentence = _first_sentence(text, m.end())
                if len(sentence) < 8:
                    continue
                # salient tokens of the promise, minus the marker words.
                keys = _salient_tokens(sentence, extra_stop=OPEN_LOOP_RE.pattern
                                       .replace("\\b", "").split("|"))
                if not keys:
                    continue
                later = _corpus_after(corpus_by_turn, at["turn"])
                if keys & later:
                    continue  # a later action plausibly closed this loop.
                dedup = _clip(sentence, 60).lower()
                if dedup in seen:
                    continue
                seen.add(dedup)
                out.append({
                    "category": "open_loops", "confidence": "LOW",
                    "summary": f"Forward commitment with no later closure: "
                               f"\"{_clip(sentence, 40)}\"",
                    "evidence": {"turn": at["turn"], "text": _clip(sentence, 80)},
                    "suggested_disposition":
                        "CANDIDATE — confirm this was done or fold it into the handoff.",
                })
        return out

    # -- shared: the "later actions" corpus (writes + bash + commits + text) - #
    def _action_corpus(self, tx: _Transcript) -> list[tuple[int, set[str]]]:
        rows: list[tuple[int, set[str]]] = []
        for w in tx.writes:
            rows.append((w["turn"], _salient_tokens(w["file"])))
        for b in tx.bash:
            toks = _salient_tokens(b["command"])
            # a `git commit -m "..."` message is a strong closure signal.
            rows.append((b["turn"], toks))
        for at in tx.assistant_text:
            rows.append((at["turn"], _salient_tokens(at["text"])))
        rows.sort()
        return rows

    @staticmethod
    def parse_source(source: str) -> str:
        return source


def _corpus_after(rows: list[tuple[int, set[str]]], turn: int) -> set[str]:
    out: set[str] = set()
    for t, toks in rows:
        if t > turn:
            out |= toks
    return out


def _next_after(items: list[dict], turn: int) -> Optional[dict]:
    best = None
    for it in items:
        if it["turn"] > turn and (best is None or it["turn"] < best["turn"]):
            best = it
    return best


def _questions_text(questions: Any) -> str:
    if isinstance(questions, list):
        return " ".join(str(q.get("question", q)) if isinstance(q, dict) else str(q)
                        for q in questions)
    return str(questions or "")


def _ask_clauses(text: str) -> list[str]:
    """Split a user message into request clauses and return one salient keyword
    per STRONG imperative clause (for the partial-fulfillment check).

    Gated hard for precision: slash-commands (``/compact`` &c.) are skipped
    entirely, clauses split only on conjunction/line boundaries, and a clause
    qualifies ONLY if it carries a strong action verb — so conversational
    "X, although Y" prose does not manufacture phantom asks."""
    text = (text or "").strip()
    if not text or text.startswith("/"):
        return []
    if not STRONG_ACTION_RE.search(text):
        return []
    parts = re.split(r"\n| and |;", text, flags=re.IGNORECASE)
    keys: list[str] = []
    for p in parts:
        p = p.strip()
        if not p or not STRONG_ACTION_RE.search(p):
            continue
        toks = _salient_tokens(p, extra_stop=_ACTION_STOP)
        if not toks:
            continue
        # pick the longest token as the clause's salient keyword.
        keys.append(max(toks, key=len))
    # dedup, preserve order.
    seen: set[str] = set()
    out = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _path_tokens(text: str) -> set[str]:
    """Filename-ish tokens from a shell command: only raw tokens that look like
    a path (contain ``/`` or ``.``), reduced to {full, basename, stem} so a
    later reference to the same FILE closes it, but a shared parent dir or a
    bare binary name does not."""
    out: set[str] = set()
    for m in _WORD_RE.findall(text or ""):
        raw = m.lower().strip()
        if "/" not in raw and "." not in raw:
            continue
        norm = raw.strip("./-")
        if not norm:
            continue
        out.add(norm)
        base = norm.rsplit("/", 1)[-1]
        if len(base) >= 4:
            out.add(base)
        if "." in base:
            stem = base.rsplit(".", 1)[0]
            if len(stem) >= 4:
                out.add(stem)
    return out


def _error_snippet(text: str) -> str:
    m = ERROR_RE.search(text or "")
    if not m:
        return _clip(text, 80)
    start = max(0, m.start() - 20)
    return _clip((text or "")[start:m.end() + 60], 90)
