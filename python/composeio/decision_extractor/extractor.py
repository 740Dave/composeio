"""DecisionExtractor — a DETERMINISTIC (no LLM, no network) draft-authoring tool.

Sibling to :mod:`composeio.session_sweep`.  Where the sweep answers *what got
left on the floor*, the extractor answers *what did we DECIDE* — it reads a
Claude Code session ``.jsonl`` and emits a **DRAFT** LTS decision-provenance
document (spec-35 shaped: ``corpora/platform-specs/35-intelligence-console-decisions.lts``)
pre-populated from the decisions found in the transcript.  So at end-of-session
the architect REFINES a draft instead of authoring the ledger from scratch.

Design law (``bootstrap/dusk-gifts/README.md`` — "Externalize structure; make the
summarizer carry the LEAST"): rationale is the ONE channel a ``/compact``
summarizer cannot carry, and LTS is purpose-built to.  A decision-provenance
ledger is worth authoring whenever a session settles forks whose *why* the
summary will flatten to a bare conclusion.  This tool does the mechanical first
pass — SURFACE the decisions with their evidence; the architect triages the
``TODO`` gaps and lints.  It never invents rationale that is not in the bytes.

Three decision sources, all DETERMINISTIC:

  1. **explicit markers** — a shared convention both David and the Intelligence
     type inline.  A line ``load-bearing decision: <title>`` (optionally after a
     ``- ``/``* `` bullet or ``**`` markdown), enriched by immediately-following
     ``chose:`` / ``over:`` / ``because:`` / ``flips-if:`` / ``tag:`` /
     ``status:`` lines, OR a compact single-line
     ``load-bearing decision(TAG): title — chose X over Y because Z; flips if W``.
  2. **channel-reply markers** — the SAME marker grammar, but scanned inside a
     channel ``reply`` tool_use (canonically ``mcp__composeio-channel__reply``).
     An Intelligence resumed into the Intelligence Console speaks over the bus
     via that tool, so decisions it plants there would otherwise be MISSED by
     the text scan.  Nodes are tagged ``channel-marker`` so a reader knows the
     decision came over the bus.
  3. **AskUserQuestion forks** — AUTOMATIC, no marker.  Every ``AskUserQuestion``
     tool_use is a fork the human resolved; each question becomes a node with the
     question as the decision, the option labels as alternatives, and the
     recorded answer as the chosen option.

The parsing idiom (single-pass over the JSONL, ``_Transcript``) is IMPORTED from
``session_sweep.sensor`` — not duplicated.  Every MISSING schema field is a
literal ``TODO`` in the draft so the architect sees exactly what to fill.

The marker grammar is a CONVENTION (see ``README.md``); the architect registers
it in ``spec/99-composeio-conventions.smd``.
"""
from __future__ import annotations

import datetime
import re
import time
from pathlib import Path
from typing import Any, Optional

# REUSE the session_sweep single-pass JSONL parser (import, do not duplicate).
from ..session_sweep.sensor import _Transcript

# --------------------------------------------------------------------------- #
# The draft's stable identifiers (a DRAFT spec id, distinct from the real
# ledger's [SPEC:CXC-IC-DECISIONS] so the two never collide before triage).
# --------------------------------------------------------------------------- #
DRAFT_SPEC_ID = "CXC-IC-DECISIONS-DRAFT"
NODE_PREFIX = "CXC-ICD-D"
TODO = "TODO"
DEFAULT_TAG = "UNTRIAGED"
DEFAULT_STATUS = "draft"
_VALID_TAGS = {"DERIVABLE", "CONTESTED", "PREFERENCE"}

# --------------------------------------------------------------------------- #
# Marker grammar.
# --------------------------------------------------------------------------- #
# The primary trigger.  Optional leading bullet (``- ``/``* ``) or markdown
# emphasis (``**``); an OPTIONAL ``(TAG)`` right after "decision" carries the
# recoverability tag for the compact single-line form.
_TRIGGER_RE = re.compile(
    r"""^\s*
        (?:[-*]\s+)?          # optional list bullet
        (?:\*{1,2}\s*)?       # optional markdown emphasis
        load[- ]bearing\s+decision
        \s*(?:\(\s*(?P<tag>[^)]*?)\s*\))?   # optional (TAG)
        \s*:\s*
        (?P<rest>.*)$
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A leading bullet/emphasis stripper for tail lines.
_BULLET_RE = re.compile(r"^\s*(?:[-*]\s+)?(?:\*{1,2}\s*)?")

# The four "invalidating assumption" spellings (flips-if and aliases).
_FLIPS_KW = r"flips?[- ]if|invalidated\s+if|would\s+flip\s+if"

# Inline keywords that mark ``rest`` as a COMPACT single-line marker (title +
# tail on one line) rather than a bare title with tails on following lines.
_COMPACT_HINT_RE = re.compile(rf"\b(?:chose|because|{_FLIPS_KW})\b", re.IGNORECASE)

# A channel-reply tool_use: an Intelligence resumed into the Intelligence
# Console publishes over the bus via the channel's ``reply`` tool (canonically
# ``mcp__composeio-channel__reply``).  Match GENERALLY so a differently-named
# channel still matches: the tool name ends with ``__reply`` AND contains
# ``channel``.  Its ``input.text`` is the message published to the bus, which we
# scan for the SAME markers as assistant/user turn text.
_CHANNEL_REPLY_RE = re.compile(r"__reply$", re.IGNORECASE)


def _is_channel_reply(name: Optional[str]) -> bool:
    return bool(name) and bool(_CHANNEL_REPLY_RE.search(name)) and "channel" in name.lower()


def _today() -> str:
    return datetime.date.today().isoformat()


def _clip(s: str, n: int) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _norm_tag(raw: Optional[str]) -> str:
    """A marker's ``tag`` / ``(TAG)`` IS the recoverability tag.  Uppercase and
    validate against the spec-35 set; anything else is preserved verbatim (the
    architect triages) but an empty tag falls back to the documented default."""
    if not raw or not raw.strip():
        return DEFAULT_TAG
    up = raw.strip().upper()
    return up if up in _VALID_TAGS else raw.strip()


def _split_on(s: str, kw_pattern: str) -> tuple[str, Optional[str]]:
    """Split ``s`` at the first occurrence of a keyword; return
    ``(head, tail_after_keyword)`` with surrounding punctuation trimmed.  Tail is
    ``None`` when the keyword is absent."""
    m = re.search(kw_pattern, s, re.IGNORECASE)
    if not m:
        return s, None
    head = s[: m.start()].strip(" —-;:,")
    tail = s[m.end():].strip(" —-;:,")
    return head, (tail or None)


def _split_chose_over(chose_over: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """A ``chose`` value may be ``X over Y``; split into (chosen, alternative)."""
    if not chose_over:
        return None, None
    m = re.search(r"\bover\b", chose_over, re.IGNORECASE)
    if m:
        return (chose_over[: m.start()].strip(" —-;:,") or None,
                chose_over[m.end():].strip(" —-;:,") or None)
    return chose_over.strip() or None, None


# --------------------------------------------------------------------------- #
# A decision record (source-neutral); rendered into one spec-35 node.
# --------------------------------------------------------------------------- #
class Decision:
    """One extracted decision.  ``source`` is ``marker``, ``channel-marker``, or
    ``askuserquestion``.  Any field left ``None`` renders as the literal ``TODO``
    placeholder (except ``tag``/``status`` which carry documented defaults)."""

    def __init__(self, source: str, turn: int, seq: int = 0):
        self.source = source
        self.turn = turn
        self.seq = seq                       # tie-break within a turn
        self.decision: Optional[str] = None  # the fork (heading + decision:)
        self.chosen: Optional[str] = None
        self.alternatives: Optional[str] = None
        self.rationale: Optional[str] = None
        self.invalidating: Optional[str] = None
        self.tag: str = DEFAULT_TAG          # -> recoverability:
        self.status: str = DEFAULT_STATUS

    @property
    def order(self) -> tuple[int, int]:
        return (self.turn, self.seq)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source, "turn": self.turn,
            "decision": self.decision, "chosen": self.chosen,
            "alternatives": self.alternatives, "rationale": self.rationale,
            "invalidating_assumption": self.invalidating,
            "recoverability": self.tag, "status": self.status,
        }


# --------------------------------------------------------------------------- #
# The extractor.
# --------------------------------------------------------------------------- #
class DecisionExtractor:
    """Read a session ``.jsonl``, extract decisions from BOTH sources, and
    render a draft spec-35 ``.lts``.

        DecisionExtractor(path).extract()        -> list[Decision]  (turn order)
        DecisionExtractor(path).to_lts()         -> str             (the draft)
    """

    def __init__(self, jsonl_path: str):
        self.jsonl_path = jsonl_path

    # -- top level ---------------------------------------------------------- #
    def extract(self) -> list[Decision]:
        tx = _Transcript(self.jsonl_path).parse()
        decisions = (self._from_markers(tx) + self._from_channel(tx)
                     + self._from_asks(tx))
        decisions.sort(key=lambda d: d.order)
        return decisions

    def to_lts(self, today: Optional[str] = None) -> str:
        return render_lts(self.extract(), source=Path(self.jsonl_path).name,
                          today=today)

    # -- Source 1: explicit inline markers ---------------------------------- #
    def _flat_lines(self, tx: _Transcript) -> list[tuple[int, str]]:
        """Assistant + genuine-user text, merged in turn order, split to lines.
        A tail line "immediately follows" a trigger when it is the next entry in
        THIS flat sequence — which is true whether the tail is on the next line
        of the same text block or in the next turn."""
        merged = sorted(list(tx.assistant_text) + list(tx.user_text),
                        key=lambda e: e["turn"])
        out: list[tuple[int, str]] = []
        for e in merged:
            for line in (e["text"] or "").split("\n"):
                out.append((e["turn"], line))
        return out

    def _from_markers(self, tx: _Transcript) -> list[Decision]:
        return self._scan_marker_lines(self._flat_lines(tx), source="marker")

    def _scan_marker_lines(self, lines: list[tuple[int, str]],
                           source: str) -> list[Decision]:
        """Scan a flat (turn, line) sequence for ``load-bearing decision:``
        markers (compact + multi-line forms) and emit one Decision each, tagged
        with ``source``.  This IS the shared marker parser — the text-source and
        channel-source both flow through it (grammar is not forked)."""
        out: list[Decision] = []
        i = 0
        while i < len(lines):
            turn, line = lines[i]
            m = _TRIGGER_RE.match(line)
            if not m:
                i += 1
                continue
            tag = m.group("tag")
            rest = (m.group("rest") or "").strip()
            if _COMPACT_HINT_RE.search(rest):
                out.append(self._parse_compact(rest, tag, turn, source))
                i += 1
            else:
                d, i = self._parse_multiline(rest, tag, turn, lines, i + 1,
                                             source)
                out.append(d)
        return out

    # -- Source 2: channel-reply tool_use markers --------------------------- #
    def _from_channel(self, tx: _Transcript) -> list[Decision]:
        """An Intelligence resumed into the Intelligence Console speaks over the
        bus via the channel's ``reply`` tool; it drops the SAME
        ``load-bearing decision:`` markers inside those tool calls.  Scan each
        channel-reply's ``input.text`` with the shared marker parser and tag the
        nodes ``channel-marker`` so a reader knows the decision came over the
        bus.  Each reply's text is scanned independently so a trailing marker's
        tail block cannot bleed across replies."""
        out: list[Decision] = []
        for u in sorted(tx.tool_use_by_id.values(),
                        key=lambda x: x.get("turn", 0)):
            if not _is_channel_reply(u.get("name")):
                continue
            turn = u.get("turn", 0)
            text = ((u.get("input") or {}).get("text")) or ""
            lines = [(turn, ln) for ln in text.split("\n")]
            out.extend(self._scan_marker_lines(lines, source="channel-marker"))
        return out

    def _parse_compact(self, rest: str, tag: Optional[str], turn: int,
                       source: str = "marker") -> Decision:
        """``title — chose X over Y because Z; flips if W`` on one line."""
        d = Decision(source, turn)
        d.tag = _norm_tag(tag)
        head, invalid = _split_on(rest, rf"\b(?:{_FLIPS_KW})\b")
        head, because = _split_on(head, r"\bbecause\b")
        head, chose_over = _split_on(head, r"\bchose\b")
        d.invalidating = invalid
        d.rationale = because
        d.chosen, d.alternatives = _split_chose_over(chose_over)
        d.decision = head.strip(" —-:;,") or None
        return d

    def _parse_multiline(self, rest: str, tag: Optional[str], turn: int,
                         lines: list[tuple[int, str]], j: int,
                         source: str = "marker") -> tuple[Decision, int]:
        """Bare title on the trigger line; enrich from following tail lines."""
        d = Decision(source, turn)
        d.decision = rest.strip(" —-:;,") or None
        if tag:
            d.tag = _norm_tag(tag)
        while j < len(lines):
            if self._apply_tail(d, lines[j][1]):
                j += 1
            else:
                break
        return d, j

    def _apply_tail(self, d: Decision, raw_line: str) -> bool:
        """Match ONE enrichment tail line onto ``d``.  Returns True if consumed.
        Tails are keyword-led and order-independent; an unmatched line ends the
        block."""
        line = _BULLET_RE.sub("", raw_line).strip()
        if not line:
            return False

        def val(kw: str) -> Optional[str]:
            m = re.match(rf"^{kw}\s*:?\s*(.*)$", line, re.IGNORECASE)
            return m.group(1).strip() if m else None

        v = val("chose")
        if v is not None:
            d.chosen, alt = _split_chose_over(v)
            if alt and not d.alternatives:
                d.alternatives = alt
            return True
        v = val("over")
        if v is not None:
            d.alternatives = v or d.alternatives
            return True
        v = val("because")
        if v is not None:
            d.rationale = v or d.rationale
            return True
        v = val(f"(?:{_FLIPS_KW})")
        if v is not None:
            d.invalidating = v or d.invalidating
            return True
        v = val("tag")
        if v is not None:
            d.tag = _norm_tag(v)
            return True
        v = val("status")
        if v is not None:
            d.status = v or d.status
            return True
        return False

    # -- Source 3: AskUserQuestion forks (automatic) ------------------------ #
    def _from_asks(self, tx: _Transcript) -> list[Decision]:
        # AskUserQuestion tool_use lives in tool_use_by_id (all tool_use blocks
        # are recorded there with id + input + turn); pair to its answer via the
        # tool_result carrying the same id.
        results_by_id = {r["id"]: r["text"] for r in tx.tool_results if r.get("id")}
        # index the assistant's recommendation text (last text block BEFORE the
        # ask) for a best-effort rationale seed.
        texts = sorted(tx.assistant_text, key=lambda e: e["turn"])
        out: list[Decision] = []
        for uid, u in tx.tool_use_by_id.items():
            if u.get("name") != "AskUserQuestion":
                continue
            inp = u.get("input") or {}
            turn = u.get("turn", 0)
            questions = inp.get("questions") or []
            answers = _parse_ask_answers(results_by_id.get(uid) or "", questions)
            preceding = self._preceding_text(texts, turn)
            for qi, q in enumerate(questions if isinstance(questions, list) else []):
                out.append(self._ask_node(q, answers, preceding, turn, qi))
        return out

    @staticmethod
    def _preceding_text(texts: list[dict], turn: int) -> Optional[str]:
        best = None
        for e in texts:
            if e["turn"] < turn and (best is None or e["turn"] > best["turn"]):
                best = e
        return best["text"] if best else None

    def _ask_node(self, q: Any, answers: dict[str, str],
                  preceding: Optional[str], turn: int, seq: int) -> Decision:
        d = Decision("askuserquestion", turn, seq)
        if not isinstance(q, dict):
            d.decision = str(q)
            d.chosen = TODO
            d.alternatives = TODO
            d.rationale = "TODO: confirm rationale"
            d.tag = DEFAULT_TAG
            return d
        qtext = q.get("question") or q.get("header") or ""
        d.decision = qtext or None
        opts = q.get("options") or []
        labels = [o.get("label", str(o)) if isinstance(o, dict) else str(o)
                  for o in opts]
        d.alternatives = "; ".join(labels) if labels else None
        d.chosen = answers.get(qtext)
        # rationale seed: a "(Recommended)" option marker and/or the assistant's
        # recommendation text just before the ask.  Never invented — best-effort
        # seed + an explicit TODO for the architect to confirm.
        seeds: list[str] = []
        for o in opts:
            if isinstance(o, dict):
                blob = f"{o.get('label','')} {o.get('description','')}"
                if re.search(r"recommend", blob, re.IGNORECASE):
                    seeds.append(f"recommended option: {o.get('label','')}")
        if preceding and re.search(r"recommend|suggest|i'?d\s+go\s+with",
                                   preceding, re.IGNORECASE):
            seeds.append(f"pre-ask note: \"{_clip(preceding, 140)}\"")
        seed = "; ".join(seeds)
        d.rationale = (f"{seed} — TODO: confirm rationale" if seed
                       else "TODO: confirm rationale")
        d.tag = DEFAULT_TAG   # recoverability UNTRIAGED (TODO) for auto forks
        return d


# --------------------------------------------------------------------------- #
# AskUserQuestion answer parsing.
# --------------------------------------------------------------------------- #
def _parse_ask_answers(result_text: str, questions: Any) -> dict[str, str]:
    """Map each question text -> its recorded answer.

    Claude Code records the answer as a single string::

        Your questions have been answered: "<Q1>"="<A1>", "<Q2>"="<A2>".
        You can now continue with these answers in mind.

    We anchor on the KNOWN question texts (from the tool_use input), so an answer
    body that itself contains commas or quotes does not break the parse: each
    answer runs from its own ``"<Qi>"="`` marker to the NEXT question's marker
    (or the trailing "You can now continue" sentinel)."""
    out: dict[str, str] = {}
    if not isinstance(questions, list) or not result_text:
        return out
    qtexts = [q.get("question") for q in questions
              if isinstance(q, dict) and q.get("question")]
    trail = result_text.rfind(". You can now continue")
    hard_end = trail if trail != -1 else len(result_text)
    for i, qtext in enumerate(qtexts):
        key = f'"{qtext}"="'
        idx = result_text.find(key)
        if idx == -1:
            continue
        start = idx + len(key)
        end = hard_end
        for nxt in qtexts[i + 1:]:
            ni = result_text.find(f', "{nxt}"="', start)
            if ni == -1:
                ni = result_text.find(f'"{nxt}"="', start)
            if ni != -1:
                end = min(end, ni)
                break
        ans = result_text[start:end].strip()
        # Variant shape: a single-select answer is recorded as
        # ``<label>" selected preview:<multiline preview>`` — keep just the
        # chosen LABEL (the preview is the option's own description, not the
        # decision).
        m = re.match(r'^(.*?)"\s*selected preview:', ans, re.DOTALL)
        if m:
            ans = m.group(1).strip()
        else:
            ans = ans.rstrip().rstrip(",").rstrip().rstrip('"')
        out[qtext] = ans
    return out


# --------------------------------------------------------------------------- #
# Rendering — a DRAFT spec-35-shaped .lts string.
# --------------------------------------------------------------------------- #
def _field(name: str, value: Optional[str]) -> str:
    return f"- {name}: {value if value not in (None, '') else TODO}"


def _node(n: int, d: Decision) -> str:
    heading = _clip(d.decision or f"(untitled {d.source} decision)", 90)
    L = [
        f"### [{NODE_PREFIX}{n}] {heading}",
        f"<!-- source: {d.source} (turn {d.turn}) -->",
        "",
        _field("decision", d.decision),
        _field("chosen", d.chosen),
        _field("alternatives", d.alternatives),
        _field("rationale", d.rationale),
        _field("invalidating_assumption", d.invalidating),
        _field("recoverability", d.tag),
        _field("status", d.status),
        f"- part-of: [SPEC:{DRAFT_SPEC_ID}]",
    ]
    return "\n".join(L)


def render_lts(decisions: list[Decision], source: str = "session.jsonl",
               today: Optional[str] = None) -> str:
    date = today or _today()
    n_marker = sum(1 for d in decisions if d.source == "marker")
    n_channel = sum(1 for d in decisions if d.source == "channel-marker")
    n_ask = sum(1 for d in decisions if d.source == "askuserquestion")

    front = [
        "---",
        'scope: "DRAFT decision-provenance ledger, machine-extracted from a '
        "Claude Code session transcript by composeio.decision_extractor "
        "(deterministic; no LLM, no network). Pre-populates the spec-35 shape "
        "(corpora/platform-specs/35-intelligence-console-decisions.lts) from three "
        "sources — explicit inline `load-bearing decision:` markers, the same "
        "markers dropped inside channel-reply tool calls (over the bus), and "
        "AskUserQuestion forks — so the architect REFINES a draft rather than "
        'authoring the ledger cold. Every unknown field is a literal TODO."',
        'audience: "the ComposeUX architect — triages the TODOs, walks each '
        'fork, assigns recoverability, lints, then promotes to the real ledger"',
        'boundary: "IN: decisions detectable in the transcript bytes (inline + '
        "channel-reply markers + AskUserQuestion answers). OUT: rationale not "
        "present in the transcript "
        "(never invented — left TODO); relationship edges; lint compliance "
        '(the architect adds these)."',
        f"depends-on: [SPEC:CXC-INTELLIGENCE-CONSOLE]",
        "domain: PLT.CUX",
        "confidentiality: internal-only",
        "artifact-readiness: design-review",
        f'status: "DRAFT — auto-extracted {date} from {source}, architect '
        f'to triage ({len(decisions)} node(s): {n_marker} marker, {n_channel} '
        f'channel-marker, {n_ask} askuserquestion)."',
        f"created: {date}",
        "---",
    ]

    head = [
        f"# Intelligence Console — Decision Provenance (DRAFT) [SPEC:{DRAFT_SPEC_ID}]",
        "",
        "> MACHINE DRAFT — do not read cold as truth. This ledger was "
        "auto-extracted from a session transcript by "
        "`composeio.decision_extractor`: it captures the FORKS it could see "
        "(inline + channel-reply `load-bearing decision:` markers + "
        "AskUserQuestion answers) and "
        "leaves every field it could not derive from the bytes as a literal "
        "`TODO`. It never invents rationale. The architect refines each node — "
        "walks the fork, writes the real `rationale`/`invalidating_assumption`, "
        "assigns an epistemic-recoverability tag (DERIVABLE | CONTESTED | "
        "PREFERENCE), adds the Relationships edges — then promotes it to the "
        "living ledger ([SPEC:CXC-IC-DECISIONS], spec 35).",
        "",
        "---",
    ]

    body: list[str] = []
    if not decisions:
        body.append("_No decisions extracted from this transcript. (No "
                    "`load-bearing decision:` markers — inline or in channel "
                    "replies — and no AskUserQuestion forks were found.)_")
    else:
        for i, d in enumerate(decisions, 1):
            body.append(_node(i, d))
            body.append("")
            body.append("---")

    rel = [
        "## Relationships",
        "",
        f"[SPEC:{DRAFT_SPEC_ID}] DOCUMENTS [SPEC:CXC-INTELLIGENCE-CONSOLE]",
        "",
        "_(TODO: architect adds per-node RELATED_TO / EXEMPLIFIES edges during "
        "triage — the extractor cannot infer graph relationships from the "
        "transcript.)_",
        "",
        "---",
    ]

    footer = [
        f"**Status**: DRAFT, auto-extracted {date} from `{source}` by "
        f"`composeio.decision_extractor`. {len(decisions)} candidate "
        f"decision(s): {n_marker} from inline markers, {n_channel} from "
        f"channel-reply markers, {n_ask} from "
        "AskUserQuestion forks. Each node is a starting point, not a "
        "conclusion — the architect triages every `TODO`, assigns "
        "recoverability, and promotes the refined nodes into the living "
        "ledger [SPEC:CXC-IC-DECISIONS] (spec 35). This draft is itself an "
        "application of the externalize-to-LTS principle "
        "([CXC-ICD-D7]): mechanize the first pass so the rationale channel the "
        "`/compact` summarizer drops gets captured before the seam.",
    ]

    parts = (front + [""] + head + [""] + body + [""] + rel + [""] + footer)
    return "\n".join(parts) + "\n"


# --------------------------------------------------------------------------- #
# --tail: watch the file, re-emit the draft as decisions accrue.  Poll-based
# and deliberately simple; the end-of-session full pass is the PRIMARY path.
# This exists to seed a future live console "decisions" panel.
# --------------------------------------------------------------------------- #
def tail(jsonl_path: str, out: Optional[str] = None, interval: float = 3.0,
         _max_iters: Optional[int] = None) -> int:
    """Poll ``jsonl_path``; whenever the extracted decision COUNT grows, re-emit
    the full draft (to ``out`` or stdout).  Ctrl-C to stop.  ``_max_iters`` caps
    the loop for tests."""
    import sys
    last_count = -1
    iters = 0
    print(f"[decision_extractor --tail] watching {jsonl_path} "
          f"(every {interval:.0f}s; Ctrl-C to stop)\n", file=sys.stderr)
    try:
        while True:
            if Path(jsonl_path).exists():
                ex = DecisionExtractor(jsonl_path)
                decisions = ex.extract()
                if len(decisions) != last_count:
                    last_count = len(decisions)
                    draft = render_lts(decisions,
                                       source=Path(jsonl_path).name)
                    if out:
                        Path(out).write_text(draft, encoding="utf-8")
                        print(f"[tail] {last_count} decision(s) -> {out}",
                              file=sys.stderr)
                    else:
                        print(draft, flush=True)
            iters += 1
            if _max_iters is not None and iters >= _max_iters:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n[tail] stopped.", file=sys.stderr)
        return 0
