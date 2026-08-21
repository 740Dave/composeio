"""
ComposeUX DSD document model — parse a Decision-Support-Doc markdown into an
interactive-form structure, and write chosen options + comments BACK into the
``Your call:`` slots ([SPEC:CXC-DSDV] [CXC-DSDV-PARSE], [CXC-DSDV-SAVE]).

CANVAS-FREE by construction: this module knows nothing about rendering — it is
the pure parse + write-back the DSD-form widget (``dsd_form.py``) drives and the
machine tests exercise directly.  The DSD authoring format is owned by the
decision-support-doc skill and consumed here as a FIXED parse target:

    ## Theme … <label>              a level-2 heading grouping the decisions below
    ### D<n> · <question>            a level-3 heading whose text matches ^D<n>
    **What this is[ …]:** ...        optional decision-context gloss (shown, wraps)
    **Why it matters:** ...          optional stakes line (shown, wraps)
    | Option | Tradeoffs |           the option set (first such table)
    |---|---|
    | **Lean row** | ...  |          a bolded Option cell => recommended: true
    | Other option | ... |
    **My recommendation:** <option>  the pre-select target (matched by equality)
    **Your call:** <slot>            the write-back target (also `**Your call: X**`)
    **Comments:** <text>             (optional; re-written on re-answer)

PRE-SELECT PRECEDENCE ([CXC-DSDV-PARSE], stated so the builder makes no judgment
call): (1) the ``**My recommendation:**`` line's option, matched to a row label
by normalized-equality, wins; (2) else the BOLDED table row wins; (3) else no
option is pre-selected and the decision is flagged "no recommendation".  In real
DSDs the recommendation line is prose ("merge. it's done…"), so (2) — the skill
always bolds the lean row — is the workhorse; (1) is the exact-label case the
spec's ``when/then`` shows.

WRITE-BACK ([CXC-DSDV-SAVE]) is precise + fail-safe: each decision's recorded
slot LINE is replaced with ``**Your call:** <chosen option(s)>`` (comma-joined
for multi-select), a non-empty comment adds/replaces an immediately-following
``**Comments:** <comment>`` line, an UNANSWERED decision's slot is left exactly
as authored, and every other line is preserved byte-for-byte.  Edits apply
bottom-to-top so a comment insert never shifts an earlier decision's slot index.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ── grammar ────────────────────────────────────────────────────────────────
# A decision heading: a level-3 heading whose text starts with D<n>.
_DECISION_RE = re.compile(r"^\s*#{3}\s+(D(\d+)\b.*?)\s*$")
# Any ATX heading (decision blocks end at the next heading of any level).
_ANY_HEADING_RE = re.compile(r"^\s*#{1,6}\s+")
# A `**My recommendation:**` (or plural) line; captures the trailing option text.
_RECO_RE = re.compile(
    r"^\s*\*{0,2}\s*My\s+recommendations?\s*:?\s*\*{0,2}\s*(.*?)\s*$",
    re.IGNORECASE)
# A `Your call:` slot line — tolerant of `**Your call:** X`, `**Your call: X**`,
# and a leading `- ` bullet (the skill template renders both forms).
_YOURCALL_RE = re.compile(
    r"^\s*(?:-\s*)?\*{0,2}\s*Your\s+call\s*:\s*(.*?)\s*\*{0,2}\s*$",
    re.IGNORECASE)
# An existing `Comments:` line (re-written on re-answer, never duplicated).
_COMMENTS_RE = re.compile(
    r"^\s*(?:-\s*)?\*{0,2}\s*Comments\s*:\s*(.*?)\s*\*{0,2}\s*$",
    re.IGNORECASE)
# The answered marker (idempotent; replaced in place on re-save).
_MARKER_RE = re.compile(r"^\s*<!--\s*dsd-status:\s*answered\b.*-->\s*$",
                        re.IGNORECASE)
# [DD-DSDV-MULTISELECT-MARK] OPEN — provisional, OFF by default: a `[multi]` tag
# on the decision heading.  The multi-select CONTROL path stays gated behind the
# ratified marker; parsing it here only sets a flag the widget currently ignores
# (renders radios — the safe default per [CXC-DSDV-MULTI]).
_MULTI_TAG_RE = re.compile(r"\[multi(?:-select)?\]", re.IGNORECASE)

# [CXC-DSDV-PARSE] DECISION CONTEXT + THEME GROUPING.  The DSD skill authors,
# above the options, a `**What this is[ …]**` gloss and a `**Why it matters**`
# stakes line — the context a human decides FROM, and (ruling 2026-08-17) REQUIRED
# on every decision; see `validate_dsd` below.  Each may WRAP across several source
# lines (one soft-wrapped paragraph), terminated by a blank line, the options
# table, or the next labelled line.  They are CAPTURED here (not dropped) so the
# viewer can SHOW them ([CXC-DSDV-FORM]).
#
# THE LABEL TERMINATOR IS NOT ALWAYS A COLON — and assuming it was cost real
# content.  The grammar here used to be `What\s+this\s+is\b.*?:` , which required
# a colon SOMEWHERE on the line.  Three punctuation forms are live in the corpus:
#
#     **What this is:** text          the form the old regex was written for
#     **What this is.** text          a full stop instead
#     **What this is** — text         an em-dash, the current house style
#
# The last two DID NOT MATCH AT ALL, so an authored gloss rendered as nothing and
# the viewer showed a decision with no context — silently, with no parse error.
# Worse, the non-greedy `.*?:` would run PAST the label to the first colon in the
# PROSE, so `**Why it matters** — measured: both hosts …` captured only `both
# hosts …` and dropped `— measured`.  MEASURED on
# `docs/architecture/DECISIONS-2026-08-17-migration-and-programme.md`: 18
# decisions, 1 of 2 authored `What this is` glosses parsed and 4 of 9 `Why it
# matters` lines parsed, one of those truncated.
#
# So the terminator is an ALTERNATION, anchored right after the label (plus an
# optional parenthetical, e.g. `**What this is (blocked on your intent):**`), and
# it never scans forward into the prose.  A bare `**What this is**` with the prose
# on the NEXT line is also accepted — `_collect_paragraph` picks it up.
_LABEL_TAIL = (
    r"(?:\s*\([^)]*\))?"                       # optional parenthetical qualifier
    r"(?:"
    r"\s*[:.]\s*\*{0,2}"                       #   …is:**  /  …is.**
    r"|\s*\*{1,2}\s*[:.—–-]*"        #   …is** — /  …is**
    r"|\s*[—–-]+"                    #   …is —   (unemphasised)
    r")"
    r"\s*(.*?)\s*$"
)
_WHATIS_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\*{0,2}\s*What\s+this\s+is\b" + _LABEL_TAIL, re.IGNORECASE)
_WHY_RE = re.compile(
    r"^\s*(?:[-*]\s+)?\*{0,2}\s*Why\s+it\s+matters\b" + _LABEL_TAIL, re.IGNORECASE)
# A level-2 `## …` section heading — the `## Theme …` group divider each decision
# falls under.  `##\s+` requires whitespace after exactly two hashes, so a `###`
# decision heading never matches it.
_THEME_RE = re.compile(r"^\s*##\s+(.*?)\s*$")

# The middot the skill uses between the D<n> id and the question text.
_QUESTION_SEP = "·"          # ·
# The blocker glyph the skill stamps on a gating decision heading — preserved in
# the rendered header ([CXC-DSDV-FORM]); it must NOT break the D<n> parse.
_BLOCKER = "⛔"          # ⛔


def _strip_md(text: str) -> str:
    """Plain text of an inline-markdown cell: drop ``**``/``__`` emphasis and
    backticks, collapse internal whitespace, trim."""
    s = text.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", s).strip()


def _normalize(text: str) -> str:
    """Match key for recommendation<->option equality: lower-cased, markdown
    stripped, trailing punctuation dropped."""
    return _strip_md(text).lower().rstrip(".,;:!?").strip()


@dataclass
class DsdOption:
    """One option row: its plain LABEL, its Tradeoffs text (shown, not
    editable), whether its Option cell was BOLD (the lean row), and a stable
    per-decision id (the radio value)."""
    option_id: str
    label: str
    tradeoffs: str
    recommended_row: bool = False


@dataclass
class DsdDecision:
    """One parsed ``### D<n>`` decision."""
    id: str
    question: str
    # DECISION CONTEXT ([CXC-DSDV-PARSE]): the authored gloss + stakes shown above
    # the options, and the `## Theme …` group this decision falls under.  "" when
    # the DSD omits them (they are optional).  `marker` is the ⛔ blocker glyph.
    what_this_is: str = ""
    why_it_matters: str = ""
    theme: str = ""
    marker: str = ""
    options: List[DsdOption] = field(default_factory=list)
    # pre-select target: an option_id, or None ([CXC-DSDV-PRESELECT]).
    preselect_id: Optional[str] = None
    # "reco-line" | "bold-row" | "none" — provenance for the parse report.
    preselect_source: str = "none"
    reco_text: str = ""
    multi: bool = False               # [DD-DSDV-MULTISELECT-MARK] gated OFF
    # source line spans (0-based) for a precise write-back.
    heading_line: int = -1
    slot_line: Optional[int] = None   # None => no writable slot (read-only)
    comment_line: Optional[int] = None  # an existing `Comments:` line, if any
    notes: List[str] = field(default_factory=list)

    def option_by_id(self, option_id: str) -> Optional[DsdOption]:
        for o in self.options:
            if o.option_id == option_id:
                return o
        return None


@dataclass
class DecisionAnswer:
    """The human's answer for one decision: the chosen option LABELS (0 or 1 for
    single-choice, N for multi) + the comment text."""
    labels: List[str] = field(default_factory=list)
    comment: str = ""


class DsdDocument:
    """Parsed DSD: the source lines + the decisions, with a precise, fail-safe
    write-back of an answer map into the slots."""

    def __init__(self, text: str, path: Optional[str] = None) -> None:
        self.path = path
        self.raw = text
        self._lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        self.decisions: List[DsdDecision] = []
        self.parse_error: Optional[str] = None
        self._parse()

    # -- parse ----------------------------------------------------------------

    @property
    def ok(self) -> bool:
        """True when at least one decision parsed (else the viewer shows the
        explicit 'no decisions parsed' state, [CXC-DSDV-PARSE] fail-safe)."""
        return bool(self.decisions)

    def _parse(self) -> None:
        lines = self._lines
        n = len(lines)
        # (idx, id, heading_text, theme) — track the most recent `## Theme …`
        # level-2 heading so each decision carries the group it falls under.
        heads: List[Tuple[int, str, str, str]] = []
        cur_theme = ""
        for i, line in enumerate(lines):
            mt = _THEME_RE.match(line)
            if mt and not _DECISION_RE.match(line):
                cur_theme = _strip_md(mt.group(1))
                continue
            m = _DECISION_RE.match(line)
            if m:
                heads.append((i, "D" + m.group(2), m.group(1), cur_theme))
        if not heads:
            self.parse_error = "no D<n> decisions found"
            return
        for (idx, dec_id, htext, theme) in heads:
            end = n
            for j in range(idx + 1, n):
                if _ANY_HEADING_RE.match(lines[j]):
                    end = j
                    break
            self.decisions.append(
                self._parse_decision(dec_id, htext, idx, idx + 1, end, theme))

    def _parse_decision(self, dec_id: str, htext: str, head_idx: int,
                        start: int, end: int, theme: str = "") -> DsdDecision:
        lines = self._lines
        multi = bool(_MULTI_TAG_RE.search(htext))
        clean_h = _MULTI_TAG_RE.sub("", htext).strip()
        marker = _BLOCKER if _BLOCKER in clean_h else ""
        if _QUESTION_SEP in clean_h:
            question = clean_h.split(_QUESTION_SEP, 1)[1].strip()
        else:
            # no `·` separator: strip the `D<n>` id + any blocker glyph/punct lead.
            question = re.sub(r"^D\d+\b[\s:.\-⛔]*", "", clean_h).strip()
        dec = DsdDecision(id=dec_id, question=question, multi=multi,
                          theme=theme, marker=marker, heading_line=head_idx)

        # DECISION CONTEXT ([CXC-DSDV-PARSE]): the optional `**What this is:**`
        # gloss + `**Why it matters:**` stakes, each a soft-wrapped paragraph.
        i = start
        while i < end:
            line = lines[i]
            if (not dec.what_this_is and _WHATIS_RE.match(line)
                    and "what this is" in line.lower()):
                dec.what_this_is, i = self._collect_paragraph(
                    lines, i, end, _WHATIS_RE.match(line).group(1))
                continue
            if (not dec.why_it_matters and _WHY_RE.match(line)
                    and "why it matters" in line.lower()):
                dec.why_it_matters, i = self._collect_paragraph(
                    lines, i, end, _WHY_RE.match(line).group(1))
                continue
            i += 1

        # options table: the FIRST `| Option | Tradeoffs |` header + delimiter.
        i = start
        while i < end:
            if self._is_options_header(lines[i]) and i + 1 < end \
                    and self._is_delimiter(lines[i + 1]):
                i += 2
                while i < end and "|" in lines[i] and lines[i].strip():
                    opt = self._parse_option_row(dec_id, len(dec.options),
                                                 lines[i])
                    if opt is not None:
                        dec.options.append(opt)
                    i += 1
                break
            i += 1
        if not dec.options:
            dec.notes.append("no options table parsed")

        # recommendation line + your-call slot + an existing comments line.
        for j in range(start, end):
            mreco = _RECO_RE.match(lines[j])
            if mreco and not dec.reco_text and self._looks_like_reco(lines[j]):
                dec.reco_text = _strip_md(mreco.group(1))
                continue
            myc = _YOURCALL_RE.match(lines[j])
            if myc and dec.slot_line is None:
                dec.slot_line = j
                if j + 1 < end and _COMMENTS_RE.match(lines[j + 1]):
                    dec.comment_line = j + 1
                continue

        if dec.slot_line is None:
            dec.notes.append("no writable slot")

        self._resolve_preselect(dec)
        return dec

    @staticmethod
    def _collect_paragraph(lines: List[str], i: int, end: int,
                           first_text: str) -> Tuple[str, int]:
        """Collect a soft-wrapped context paragraph starting at *lines[i]* (whose
        label-stripped remainder is *first_text*), consuming following lines until
        a blank line, a table row, another labelled line, or a heading — the
        boundary the DSD skill authors around each `**What this is:**` /
        `**Why it matters:**` block.  Returns (markdown-stripped text, next index)."""
        parts: List[str] = [first_text] if first_text else []
        j = i + 1
        while j < end:
            s = lines[j]
            if (not s.strip() or "|" in s or _ANY_HEADING_RE.match(s)
                    or _WHATIS_RE.match(s) or _WHY_RE.match(s)
                    or _YOURCALL_RE.match(s) or _COMMENTS_RE.match(s)
                    or (_RECO_RE.match(s)
                        and DsdDocument._looks_like_reco(s))):
                break
            parts.append(s.strip())
            j += 1
        return _strip_md(" ".join(p for p in parts if p)).strip(), j

    @staticmethod
    def _looks_like_reco(line: str) -> bool:
        """Guard _RECO_RE (which is permissive) to the real recommendation
        line — the token 'recommendation' must actually be present."""
        return "recommendation" in line.lower()

    @staticmethod
    def _is_options_header(line: str) -> bool:
        cells = _split_row(line)
        if len(cells) != 2:
            return False
        return (_normalize(cells[0]) == "option"
                and _normalize(cells[1]) == "tradeoffs")

    @staticmethod
    def _is_delimiter(line: str) -> bool:
        cells = _split_row(line)
        return bool(cells) and all(re.fullmatch(r":?-{1,}:?", c.strip())
                                   for c in cells if c.strip() != "")

    @staticmethod
    def _parse_option_row(dec_id: str, idx: int,
                          line: str) -> Optional[DsdOption]:
        cells = _split_row(line)
        if len(cells) < 2:
            return None
        raw_opt = cells[0].strip()
        if raw_opt == "":
            return None
        recommended = raw_opt.startswith("**") and raw_opt.endswith("**")
        return DsdOption(option_id=f"{dec_id}o{idx}",
                         label=_strip_md(raw_opt),
                         tradeoffs=_strip_md(cells[1]),
                         recommended_row=recommended)

    def _resolve_preselect(self, dec: DsdDecision) -> None:
        # (1) recommendation line matched to an option by normalized-equality.
        if dec.reco_text:
            key = _normalize(dec.reco_text)
            for o in dec.options:
                if _normalize(o.label) == key:
                    dec.preselect_id = o.option_id
                    dec.preselect_source = "reco-line"
                    return
        # (2) the bolded (lean) row.
        for o in dec.options:
            if o.recommended_row:
                dec.preselect_id = o.option_id
                dec.preselect_source = "bold-row"
                return
        # (3) nothing — flagged; the viewer still renders the decision.
        dec.preselect_source = "none"
        if dec.options:
            dec.notes.append("no recommendation")

    # -- write-back ([CXC-DSDV-SAVE]) -----------------------------------------

    def render_answers(self, answers: Dict[str, DecisionAnswer],
                       *, mark_answered: bool = True,
                       now: Optional[float] = None) -> str:
        """Return the DSD text with *answers* written into the slots — PURE (no
        I/O), so tests re-read the result directly.  ``answers`` maps decision
        id -> :class:`DecisionAnswer`.  Only slot lines (+ their comment lines)
        change; every other line is byte-identical."""
        lines = list(self._lines)

        # apply per-decision edits BOTTOM-TO-TOP so a comment insert never
        # shifts an earlier decision's recorded slot index.
        for dec in sorted(self.decisions, key=lambda d: (d.slot_line or -1),
                          reverse=True):
            if dec.slot_line is None:
                continue                      # read-only decision: never write
            ans = answers.get(dec.id)
            if ans is None:
                continue
            labels = [l for l in ans.labels if l]
            comment = (ans.comment or "").strip()
            if not labels and not comment:
                continue                      # unanswered: leave as authored
            if labels:
                lines[dec.slot_line] = f"**Your call:** {', '.join(labels)}"
            if comment:
                comment_line = f"**Comments:** {comment}"
                if dec.comment_line is not None:
                    lines[dec.comment_line] = comment_line
                else:
                    lines.insert(dec.slot_line + 1, comment_line)

        if mark_answered:
            lines = self._apply_marker(lines, now)
        return "\n".join(lines)

    @staticmethod
    def _apply_marker(lines: List[str], now: Optional[float]) -> List[str]:
        """Insert/replace the idempotent answered marker at the top of the
        file ([CXC-DSDV-SAVE] MARK ANSWERED)."""
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                            time.gmtime(now if now is not None else time.time()))
        marker = f"<!-- dsd-status: answered {ts} -->"
        for i in range(min(4, len(lines))):
            if _MARKER_RE.match(lines[i]):
                out = list(lines)
                out[i] = marker
                return out
        return [marker] + lines

    def save(self, new_text: str) -> None:
        """Atomically rewrite the DSD at ``self.path`` (write-temp-then-rename).
        A write failure leaves the original untouched ([CXC-DSDV-SAVE] ATOMIC +
        FAIL-SAFE)."""
        if not self.path:
            raise ValueError("DsdDocument has no path to save to")
        d = os.path.dirname(os.path.abspath(self.path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".dsd-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(new_text)
            os.replace(tmp, self.path)         # atomic on POSIX
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _split_row(line: str) -> List[str]:
    """Split a markdown table row into its cell strings (drop the leading/
    trailing pipe fences).  A line with no pipe yields []."""
    s = line.strip()
    if "|" not in s:
        return []
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_dsd(text: str, path: Optional[str] = None) -> DsdDocument:
    """Parse *text* into a :class:`DsdDocument` (never raises on malformed
    input — an unparseable DSD yields ``ok == False``)."""
    return DsdDocument(text, path=path)


def load_dsd(path: str) -> DsdDocument:
    """Read a DSD markdown file (READ-ONLY, mode 'r') and parse it."""
    with open(path, "r", encoding="utf-8") as fh:
        return DsdDocument(fh.read(), path=path)


# ── the CONTEXT GUARD ([CXC-DSDV-CONTEXT-GUARD], David 2026-08-17) ─────────
#
# THE RULING: "The format for a DSD is that every line is supposed to have a
# 'What this is' and 'why it matters' line to provide me with sufficient context.
# And that's not the case … please dispatch a subagent to mod the DSD viewer so
# that it refuses to load if these attributes are blank."
#
# THE FAILURE THIS CLOSES: `docs/architecture/DECISIONS-2026-08-17-migration-and-
# programme.md` PARSED, RENDERED and DELIVERED perfectly while being UNDECIDABLE.
# Its D2 — "The Turbot images — keep, relocate, or discard?" — carried no
# `What this is`, so the reader could not tell whether "images" meant images ON
# skipper, DOCKER images, or something ComposeIO-specific. (They are dated OS ISOs
# parked in skipper's asset tree; the document never said so.) Nothing refused it,
# because nothing was WRONG in the parser's terms. That is the class this codebase
# keeps logging: an artefact that is well-formed and useless, with no instrument
# that can say so. This IS that instrument.
#
# ⛔ THE BOUND — STRUCTURAL PRESENCE AND NON-EMPTY CONTENT, NOTHING MORE.
# A field is BLANK iff, after the label grammar above has matched and
# `_collect_paragraph` has gathered the soft-wrapped body, the markdown-stripped
# text is the empty string. So:
#     - no `What this is` line at all           -> BLANK
#     - `**What this is** —` with nothing after -> BLANK (label, no prose)
#     - `**What this is** — **__**`             -> BLANK (emphasis only, no text)
#     - `**What this is** — it is a thing.`     -> PRESENT
# Boilerplate that says nothing structurally PASSES. That is deliberate. Detecting
# vacuity is a judgment call a regex loses, and the cost of losing it is a FALSE
# REFUSAL of a good document — which trains the author to route around the guard,
# and a guard that gets routed around protects nothing. The bar is the bar an
# instrument can actually hold.
_REQUIRED_CONTEXT: Tuple[Tuple[str, str], ...] = (
    ("what_this_is", "What this is"),
    ("why_it_matters", "Why it matters"),
)


@dataclass
class DsdValidation:
    """The verdict on one DSD: ``ok``, and — when not ok — WHICH decision is
    missing WHICH field, by heading, so the author never has to guess.

    ``missing`` is the machine form: one ``{decision_id, heading, field, label}``
    per (decision, missing field). ``message`` is the human form, and it names
    every offence rather than the first: an author fixing a DSD one refusal at a
    time is a worse experience than the failure this guard exists to prevent."""
    ok: bool
    message: str
    missing: List[Dict[str, str]] = field(default_factory=list)
    n_decisions: int = 0
    parse_error: str = ""


def validate_dsd(doc: DsdDocument) -> DsdValidation:
    """REFUSE a DSD that any decision leaves without context.

    The ONE predicate, shared by every gate ([CXC-DSDV-CONTEXT-GUARD]): the
    decision-ops MCP tool `send_dsd_for_human_review` (which refuses BEFORE an
    `_app/dsd/open` intent is published) and `dsd_viewer_launcher.open` (which
    refuses before a window is spawned). Two doors, one lock — the bus is
    multi-master, so a guard that lives in only one transmitter is not a guard.

    Pure: takes a parsed document, returns a verdict, touches no filesystem and
    no bus."""
    if not doc.decisions:
        err = doc.parse_error or "no D<n> decisions found"
        return DsdValidation(ok=False, message=f"not a parseable DSD: {err}",
                             n_decisions=0, parse_error=err)

    missing: List[Dict[str, str]] = []
    for dec in doc.decisions:
        heading = f"{dec.id} · {dec.question}".strip() if dec.question else dec.id
        for attr, label in _REQUIRED_CONTEXT:
            if not (getattr(dec, attr, "") or "").strip():
                missing.append({"decision_id": dec.id, "heading": heading,
                                "field": attr, "label": label})
    if not missing:
        return DsdValidation(ok=True,
                             message=f"{len(doc.decisions)} decisions, every one "
                                     f"carries What this is + Why it matters",
                             n_decisions=len(doc.decisions))

    # Group per decision so the author reads one line per offending decision.
    by_dec: Dict[str, List[str]] = {}
    headings: Dict[str, str] = {}
    for m in missing:
        by_dec.setdefault(m["decision_id"], []).append(m["label"])
        headings[m["decision_id"]] = m["heading"]
    lines = [f"  - {headings[d]} — missing: {' + '.join(labs)}"
             for d, labs in by_dec.items()]
    msg = (f"REFUSED: {len(by_dec)} of {len(doc.decisions)} decisions lack the "
           f"required context (David 2026-08-17 — every decision MUST carry both "
           f"a 'What this is' and a 'Why it matters' line):\n" + "\n".join(lines)
           + "\n  Fix: add the missing line(s) under the decision heading, above "
             "the options table. A label with no prose after it counts as blank.")
    return DsdValidation(ok=False, message=msg, missing=missing,
                         n_decisions=len(doc.decisions))


def validate_dsd_file(path: str) -> DsdValidation:
    """`validate_dsd` over a path — the form both gates call.  An unreadable file
    is a refusal, never an exception into a message loop."""
    try:
        return validate_dsd(load_dsd(path))
    except Exception as exc:                       # noqa: BLE001 — never raise
        return DsdValidation(ok=False,
                             message=f"unreadable DSD {path!r}: "
                                     f"{type(exc).__name__}: {exc}",
                             parse_error=str(exc))


def build_completion(path: str, decisions: List[DsdDecision],
                     answers: Dict[str, DecisionAnswer],
                     *, correlation_id: str = "",
                     now: Optional[float] = None) -> dict:
    """The ``_app/dsd/answered`` completion payload ([CXC-DSDV-NOTIFY]): the
    explicit, first-class save-close signal (NOT a filesystem-modified watch)."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ",
                       time.gmtime(now if now is not None else time.time()))
    out_decisions = []
    for dec in decisions:
        ans = answers.get(dec.id)
        if ans is None:
            continue
        chosen = ", ".join(l for l in ans.labels if l)
        comment = (ans.comment or "").strip()
        if not chosen and not comment:
            continue
        out_decisions.append({"id": dec.id, "chosen": chosen,
                              "comment": comment})
    return {"path": os.path.abspath(path) if path else path,
            "answered": True, "decisions": out_decisions,
            "correlation_id": correlation_id, "ts": ts}
