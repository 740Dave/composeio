"""composeio.defect_ops.core — the deterministic report/attempt/resolve/query CORE
over a defect-graph `.lts` (`defects/defect-graph.lts`).

THE POINT ([SPEC:CIO-DEFECT-REGISTRY]): a fix-resistant defect loses its history
between defect-investigator dispatches, so each fresh investigator re-digs from
scratch and misses the pattern. This CORE gives a defect a persistent home keyed
to a STABLE SYMPTOM IDENTITY (`profile.symptom_slug`) so a recurrence dedups onto
the SAME node and ACCUMULATES: prior attempts + their root-cause hypotheses + why
they didn't hold + related kin. `query()` is the ANTI-RE-DIG gate.

REUSE-BY-REFERENCE, NOT A FORK. This module does NOT re-implement `.lts`
parsing, node splicing, or the dangle/collision rollback guard — it CONSUMES the
decision_ops CORE primitives verbatim ([DEC-ONE-FACTLATTICE-PARSER], the one
FactLattice engine; decision_ops is the sibling single-writer this mirrors):

    from ..decision_ops.core import (
        render_node, find_node_span, _apply_with_guard, _split, _join,
        _set_fields_in_span, _node_header_and_fields, _FIELD_LINE_RE,
        _format_value, DangleRefusedError, NodeNotFoundError, ...)

Same guarantees as decision_ops: append-only (never deletes — resolve ANNOTATES),
lint-before/lint-after with a content-keyed rollback on any NEW forward dangle /
collision / parse error (so an attempt's `investigates:`/`supersedes:`/`reopens:`
edge that resolves nowhere is REFUSED, never written), and idempotent no-ops push
no change. The engine is refreshed on write by the MCP server (lts_refresh,
reused from decision_ops) — this module never touches the engine.

GRAPH SHAPE (the graph/SQLite seam — relationships + per-attempt narrative live
HERE; counts/aggregation live in the retrospective SQLite):

    ### [DEFECT-<slug>] <symptom, truncated>
    - lifecycle-position: reported|investigating|fix-attempted|verified|recurred
    - first-reported: <YYYY-MM-DD>
    - last-activity: <YYYY-MM-DD>
    - report-count: <n>
    - attempt-count: <n>
    - symptom: <the original human symptom text>
    - area: <optional file/area>
    - related-to: [DEFECT-<kin>]        # optional; kin defects in the same area

    ### [DEFECT-<slug>-A<n>] attempt <n>: <root-cause hypothesis, truncated>
    - lifecycle-position: fix-attempted|recurred
    - attempted-in-session: <YYYY-MM-DD>
    - investigates: [DEFECT-<slug>]     # parent link (walkable from the defect)
    - branch: <branch>
    - commit: <sha>
    - root-cause-hypothesis: <prose>
    - what-changed: <prose>
    - outcome: held|recurred
    - why-it-didnt-hold: <prose>        # when outcome=recurred
    - supersedes: [DEFECT-<slug>-A<n-1>]  # the recurrence chain (n>1)
    - reopens: [DEFECT-<slug>]          # when outcome=recurred (a recurrence reopens)
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import factlattice

# REUSE the decision_ops CORE primitives verbatim (one engine, one splice/guard).
from ..decision_ops.core import (  # noqa: F401
    DangleRefusedError,
    DecisionOpsError,
    NodeExistsError,
    NodeNotFoundError,
    _FIELD_LINE_RE,
    _apply_with_guard,
    _format_value,
    graph_receipt,
    guard_graph_write,
    _join,
    _node_header_and_fields,
    _set_fields_in_span,
    _split,
    find_node_span,
    render_node,
)

from .profile import (
    attempt_node_id,
    default_profile_path,
    symptom_slug,
)


class DefectOpsError(DecisionOpsError):
    """Base class for defect_ops failures (subclasses the shared decision_ops
    error so a caller catching DecisionOpsError catches ours too)."""


def _today() -> str:
    return datetime.date.today().isoformat()


def _truncate(text: str, n: int = 90) -> str:
    """Collapse whitespace and cap at `n` — for TITLES ONLY.

    A node HEADING is a one-line label, so capping it is right. A NOTE is the
    CONTENT and must never be capped — see `_note_text`.
    """
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _note_text(text) -> str:
    """A defect NOTE, whole. Whitespace-collapsed to stay one `.lts` bullet, and
    NOT length-capped.

    WHY THIS EXISTS (2026-08-08, David: "defect_ops should not truncate at ~200
    characters!"). Notes used to go through `_truncate(note, 200)` at all four
    annotation sites, so any note longer than 200 chars was SILENTLY stored with
    a trailing `…` and the rest discarded. Caught when a filed defect's full
    rationale — the reasoning, not the headline — turned out to be missing from
    the graph; it was only preserved because the same text happened to be in a
    commit message.

    The irony is worth keeping in the record: this is a silent knowledge-loss bug
    in the very registry we use to record silent-failure bugs. The truncation
    threw away exactly the part that makes a defect entry useful later — WHY,
    not WHAT — and did it without a warning, so nobody could notice.

    There is no format reason to cap. A `.lts` bullet is a single line of any
    length; the sibling decision-graph routinely carries 3000+ char rationales on
    one line and lints clean. Titles still cap via `_truncate` — that is a
    heading, and capping a heading is correct.
    """
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------- #
# text-level helpers (built on the reused decision_ops splice primitives)
# --------------------------------------------------------------------------- #
def _append_node(text: str, node_id: str, title: str,
                 fields: Dict[str, Any]) -> str:
    """Append a new `### [id] title` block in the graph's byte format (via the
    reused `render_node`), separated by exactly one blank line — mirrors
    decision_ops.add()'s append."""
    block = render_node(node_id, title,
                        {k: v for k, v in fields.items() if v is not None})
    base = text.rstrip("\n")
    return (base + "\n\n" + block) if base else block


def _set_fields(text: str, node_id: str, fields: Dict[str, Any]) -> str:
    """Set/insert bullet fields on an existing node, preserving every other byte
    (reused `_set_fields_in_span`). No-op-safe: returns `text` unchanged if the
    node is absent (callers ensure presence first)."""
    span = find_node_span(text, node_id)
    if span is None:
        return text
    lines = _split(text)
    start, end = span
    formatted = {k: _format_value(v) for k, v in fields.items() if v is not None}
    lines, _end, _changed = _set_fields_in_span(lines, start, end, formatted)
    return _join(lines)


def _append_annotation(text: str, node_id: str, annotation: str) -> str:
    """Append a free-text annotation line after a node's last field bullet
    (mirrors decision_ops.resolve()'s annotation insert)."""
    span = find_node_span(text, node_id)
    if span is None:
        return text
    lines = _split(text)
    start, end = span
    last_field_idx = start
    for i in range(start + 1, end):
        if _FIELD_LINE_RE.match(lines[i]):
            last_field_idx = i
    lines.insert(last_field_idx + 1, annotation)
    return _join(lines)


def _int_field(fields: Dict[str, str], key: str, default: int = 0) -> int:
    try:
        return int(str(fields.get(key, default)).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# result
# --------------------------------------------------------------------------- #
@dataclass
class DefectResult:
    ok: bool
    defect_id: str
    changed: bool
    message: str
    node_id: Optional[str] = None          # the node actually written (attempt id on attempt())
    lint_clean: Optional[bool] = None
    lint_summary: Optional[dict] = None
    # --- the self-proving receipt (ADDED 2026-08-08; nothing above changed) -----
    # WHERE the record landed, absolute + resolved. THIS is the field whose absence
    # let two dispatched subagents honestly report "filed the defect" while the
    # record sat on a worktree copy that was live on no graph at all. A claim that
    # costs a human a `grep` is not a receipt.
    graph_path: Optional[str] = None
    graph_is_canonical: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "defect_id": self.defect_id, "changed": self.changed,
            "message": self.message, "node_id": self.node_id,
            "lint_clean": self.lint_clean, "lint_summary": self.lint_summary,
            "graph_path": self.graph_path,
            "graph_is_canonical": self.graph_is_canonical,
        }


def _result(defect_id: str, changed: bool, message: str, after,
            node_id: Optional[str] = None,
            graph_path: Optional[str] = None) -> DefectResult:
    receipt = graph_receipt(graph_path)
    return DefectResult(
        ok=True, defect_id=defect_id, changed=changed, message=message,
        node_id=node_id,
        lint_clean=(not after.has_errors) if after is not None else None,
        lint_summary=after.summary() if after is not None else None,
        graph_path=receipt["graph_path"],
        graph_is_canonical=receipt["graph_is_canonical"],
    )


# --------------------------------------------------------------------------- #
# reading (the anti-re-dig gate)
# --------------------------------------------------------------------------- #
def _resolve_defect_id(selector: str) -> str:
    """Accept EITHER an existing DEFECT id OR a raw symptom, returning the stable
    defect id. A `[DEFECT-...]`/`DEFECT-...` token is taken verbatim (its own
    id); anything else is treated as a symptom and hashed to its stable slug."""
    s = str(selector or "").strip().strip("[]")
    if s.upper().startswith("DEFECT-"):
        return s
    return symptom_slug(selector)


def _read_defect(graph_path: str, defect_id: str, *, profile=None) -> dict:
    """Assemble ONE defect's full accumulated history from the graph: the defect
    node + every attempt child (in attempt order) + its kin (related-to). This is
    the payload `query()` returns — the history a fresh investigator reads so it
    stops re-digging blind."""
    corpus = factlattice.parse(graph_path, profile=profile)
    by_id = {n.id: n for n in corpus.nodes}
    node = by_id.get(defect_id)
    if node is None:
        return {"found": False, "defect_id": defect_id, "defect": None,
                "attempts": [], "kin": []}

    p = node.properties
    defect = {
        "id": node.id, "title": node.title,
        "lifecycle": p.get("lifecycle-position"),
        "symptom": p.get("symptom"),
        "area": p.get("area"),
        "first_reported": p.get("first-reported"),
        "last_activity": p.get("last-activity"),
        "report_count": _int_field(p, "report-count", 0),
        "attempt_count": _int_field(p, "attempt-count", 0),
        "related_to": p.get("see-also"),
    }
    attempts: List[dict] = []
    prefix = defect_id + "-A"
    for n in corpus.nodes:
        if not n.id.startswith(prefix):
            continue
        ap = n.properties
        try:
            num = int(n.id[len(prefix):])
        except ValueError:
            num = 0
        attempts.append({
            "id": n.id, "n": num, "title": n.title,
            "lifecycle": ap.get("lifecycle-position"),
            "attempted_in_session": ap.get("attempted-in-session"),
            "branch": ap.get("branch"), "commit": ap.get("commit"),
            "root_cause_hypothesis": ap.get("root-cause-hypothesis"),
            "what_changed": ap.get("what-changed"),
            "outcome": ap.get("outcome"),
            "why_it_didnt_hold": ap.get("why-it-didnt-hold"),
            "supersedes": ap.get("supersedes"),
        })
    attempts.sort(key=lambda a: a["n"])

    kin: List[str] = []
    rel = p.get("see-also")
    if rel:
        kin = [t.strip().strip("[]") for t in str(rel).split(",") if t.strip()]

    return {"found": True, "defect_id": defect_id, "defect": defect,
            "attempts": attempts, "kin": kin}


def query(graph_path: str, selector: str, *, profile=None) -> dict:
    """THE ANTI-RE-DIG GATE ([SPEC:CIO-DEFECT-REGISTRY]). Given a symptom OR a
    defect id, return the accumulated history: the defect + prior fix attempts +
    each attempt's root-cause hypothesis + WHY IT DIDN'T HOLD + related kin. A
    fresh defect-investigator MUST run this FIRST — "5 attempts all touched X ->
    the real cause is elsewhere" is exactly the pattern a blind re-dig misses.

    Read-only; never triggers an engine refresh."""
    prof = profile if profile is not None else default_profile_path()
    defect_id = _resolve_defect_id(selector)
    return _read_defect(graph_path, defect_id, profile=prof)


def list_defects(graph_path: str, *, profile=None) -> List[dict]:
    """Every DEFECT node (not attempt children), most-fix-resistant first
    (attempt-count desc). The seam the metrics publisher reads for the
    fix-resistant leaders."""
    prof = profile if profile is not None else default_profile_path()
    corpus = factlattice.parse(graph_path, profile=prof)
    out: List[dict] = []
    for n in corpus.nodes:
        if not n.id.startswith("DEFECT-"):
            continue
        if "-A" in n.id and n.id.rsplit("-A", 1)[1].isdigit():
            continue  # an attempt child, not a defect
        p = n.properties
        # only a REAL defect node carries report-count — this excludes the
        # corpus header node ([SYSTEM:DEFECT-CORPUS], whose id also starts DEFECT-).
        if "report-count" not in p:
            continue
        out.append({
            "id": n.id, "title": n.title,
            "symptom": p.get("symptom"),
            "lifecycle": p.get("lifecycle-position"),
            "report_count": _int_field(p, "report-count", 0),
            "attempt_count": _int_field(p, "attempt-count", 0),
            "area": p.get("area"),
        })
    out.sort(key=lambda d: (-d["attempt_count"], -d["report_count"], d["id"]))
    return out


# --------------------------------------------------------------------------- #
# 1. report() — register a NEW defect OR append a report to an existing one
# --------------------------------------------------------------------------- #
def report(graph_path: str, *, symptom: str, area: str = "",
           note: str = "", related_to: Optional[str] = None,
           profile=None, today: Optional[str] = None) -> DefectResult:
    """Register a NEW defect OR append a report to an existing one (dedup by
    STABLE symptom identity — the query-first gate, like the DSD gate). A new
    defect is created with report-count 1, lifecycle `reported`; a repeat report
    bumps report-count + last-activity, and a report arriving on a `verified`
    defect is a RECURRENCE (lifecycle -> `recurred`, annotated).

    `related_to` (an existing DEFECT id) links kin in the same area; it MUST
    resolve (else the no-forward-dangle guard refuses the write)."""
    if not str(symptom or "").strip():
        raise ValueError("report() requires a non-empty symptom")
    prof = profile if profile is not None else default_profile_path()
    date = today or _today()
    defect_id = symptom_slug(symptom)
    rel = None
    if related_to:
        rel = related_to if str(related_to).startswith("[") else f"[{str(related_to).strip()}]"

    def _mutate(text: str) -> str:
        span = find_node_span(text, defect_id)
        if span is None:
            fields = {
                "lifecycle-position": "reported",
                "first-reported": date,
                "last-activity": date,
                "report-count": 1,
                "attempt-count": 0,
                "symptom": " ".join(str(symptom).split()),
            }
            if area:
                fields["area"] = area
            if rel:
                # `see-also` is the CORE-recognized RELATED_TO verb, so a kin link
                # is a REAL, dangle-CHECKED edge — a related-to target that resolves
                # nowhere is REFUSED by the guard (the "related-to" edge kind maps
                # onto core see-also; [SPEC:CIO-DEFECT-REGISTRY]).
                fields["see-also"] = rel
            new = _append_node(text, defect_id, _truncate(symptom), fields)
            if note:
                new = _append_annotation(
                    new, defect_id, f"- **REPORTED ({date}):** {_note_text(note)}")
            return new
        # existing defect -> append a report event (bump count + dates)
        _title, cur = _node_header_and_fields(text, span)
        count = _int_field(cur, "report-count", 0) + 1
        updates: Dict[str, Any] = {"report-count": count, "last-activity": date}
        recurrence = cur.get("lifecycle-position") == "verified"
        if recurrence:
            updates["lifecycle-position"] = "recurred"
        if area and not cur.get("area"):
            updates["area"] = area
        if rel and not cur.get("see-also"):
            updates["see-also"] = rel
        new = _set_fields(text, defect_id, updates)
        tag = "RE-REPORTED (recurrence)" if recurrence else "RE-REPORTED"
        ann = f"- **{tag} #{count} ({date}):**" + (f" {_note_text(note)}" if note else "")
        new = _append_annotation(new, defect_id, ann)
        return new

    _new, _before, after, _problems, changed = _apply_with_guard(
        graph_path, _mutate, profile=prof)
    msg = "reported (new defect)" if changed and find_node_span(_new or "", defect_id) \
        else ("report appended" if changed else "no-op")
    return _result(defect_id, changed, msg, after, node_id=defect_id,
                   graph_path=graph_path)


# --------------------------------------------------------------------------- #
# 2. attempt() — append a fix attempt (supersedes the prior; recurrence reopens)
# --------------------------------------------------------------------------- #
def attempt(graph_path: str, *, symptom: str,
            root_cause_hypothesis: str = "", what_changed: str = "",
            outcome: str = "held", branch: str = "", commit: str = "",
            why_it_didnt_hold: str = "", area: str = "",
            profile=None, today: Optional[str] = None) -> DefectResult:
    """Append a fix ATTEMPT to a defect (creating the defect if this is the first
    thing filed for the symptom, so the attempt always has a parent — no forward
    dangle on `investigates:`). The new attempt `SUPERSEDES` the prior attempt
    (the recurrence chain); an `outcome=recurred` attempt also `REOPENS` the
    defect (lifecycle -> `recurred`).

    `symptom` may be the raw symptom or an existing DEFECT id. Every field of the
    attempt narrative — root-cause hypothesis, what changed, outcome, why it
    didn't hold — is persisted on the attempt node so the NEXT investigator reads
    it instead of re-deriving it."""
    prof = profile if profile is not None else default_profile_path()
    date = today or _today()
    defect_id = _resolve_defect_id(symptom)
    outcome = str(outcome or "held").strip().lower()
    recurred = outcome == "recurred"
    written_id: Dict[str, str] = {}

    def _mutate(text: str) -> str:
        # ensure the parent defect exists (first-filing-is-an-attempt path)
        span = find_node_span(text, defect_id)
        if span is None:
            base_fields = {
                "lifecycle-position": "investigating",
                "first-reported": date, "last-activity": date,
                "report-count": 1, "attempt-count": 0,
                "symptom": " ".join(str(symptom).split()) if not str(symptom).upper().startswith("DEFECT-") else defect_id,
            }
            if area:
                base_fields["area"] = area
            text = _append_node(text, defect_id, _truncate(symptom), base_fields)
            span = find_node_span(text, defect_id)

        _title, cur = _node_header_and_fields(text, span)
        n = _int_field(cur, "attempt-count", 0) + 1
        aid = attempt_node_id(defect_id, n)
        written_id["id"] = aid

        # `part-of` is the CORE-recognized edge verb (PART_OF) — so the attempt's
        # parent link is a REAL, dangle-CHECKED edge (the "investigates" edge kind
        # in the defect profile maps onto core PART_OF; see [SPEC:CIO-DEFECT-REGISTRY]
        # on the domain-name -> core-verb mapping). Attempts are walkable from the
        # defect via this edge.
        afields: Dict[str, Any] = {
            "lifecycle-position": "recurred" if recurred else "fix-attempted",
            "attempted-in-session": date,
            "part-of": f"[{defect_id}]",
        }
        if branch:
            afields["branch"] = branch
        if commit:
            afields["commit"] = commit
        if root_cause_hypothesis:
            afields["root-cause-hypothesis"] = " ".join(str(root_cause_hypothesis).split())
        if what_changed:
            afields["what-changed"] = " ".join(str(what_changed).split())
        afields["outcome"] = outcome
        if recurred and why_it_didnt_hold:
            afields["why-it-didnt-hold"] = " ".join(str(why_it_didnt_hold).split())
        if n > 1:
            afields["supersedes"] = f"[{attempt_node_id(defect_id, n - 1)}]"
        if recurred:
            afields["reopens"] = f"[{defect_id}]"

        title = f"attempt {n}: " + _truncate(root_cause_hypothesis or what_changed or outcome, 80)
        text = _append_node(text, aid, title, afields)

        # advance the defect: bump attempt-count, last-activity, lifecycle
        defect_updates: Dict[str, Any] = {
            "attempt-count": n, "last-activity": date,
            "lifecycle-position": "recurred" if recurred else "fix-attempted",
        }
        text = _set_fields(text, defect_id, defect_updates)
        return text

    _new, _before, after, _problems, changed = _apply_with_guard(
        graph_path, _mutate, profile=prof)
    aid = written_id.get("id")
    msg = (f"attempt appended ({'recurred -> reopened' if recurred else 'fix-attempted'})"
           if changed else "no-op")
    return _result(defect_id, changed, msg, after, node_id=aid,
                   graph_path=graph_path)


# --------------------------------------------------------------------------- #
# 3. resolve() — verified-fixed OR reopened (ANNOTATE, never delete)
# --------------------------------------------------------------------------- #
def resolve(graph_path: str, *, symptom: str, status: str = "verified",
            note: str = "", profile=None, today: Optional[str] = None) -> DefectResult:
    """Close (`status=verified`) or reopen (`status=reopened`) a defect by
    ANNOTATION — never deletes (mirrors decision_ops.resolve). `verified` sets
    lifecycle-position=verified; `reopened` sets it to `recurred`. Idempotent on
    the field state (a repeat verified is a no-op once the lifecycle is already
    verified and the annotation is present)."""
    prof = profile if profile is not None else default_profile_path()
    date = today or _today()
    defect_id = _resolve_defect_id(symptom)
    status = str(status or "verified").strip().lower()
    if status not in ("verified", "reopened"):
        raise ValueError("resolve() status must be 'verified' or 'reopened'")
    target_lifecycle = "verified" if status == "verified" else "recurred"
    ann_prefix = f"- **{'VERIFIED-FIXED' if status == 'verified' else 'REOPENED'} ("

    def _mutate(text: str) -> str:
        span = find_node_span(text, defect_id)
        if span is None:
            raise NodeNotFoundError(
                f"resolve(): defect {defect_id!r} not found in {graph_path!r}")
        _title, cur = _node_header_and_fields(text, span)
        lines = _split(text)
        start, end = span
        already = any(lines[i].startswith(ann_prefix) for i in range(start + 1, end))
        fields_set = cur.get("lifecycle-position") == target_lifecycle
        if already and fields_set:
            return text  # idempotent no-op
        updates = {"lifecycle-position": target_lifecycle, "last-activity": date}
        text = _set_fields(text, defect_id, updates)
        if not already:
            ann = f"{ann_prefix}{date}):**" + (f" {_note_text(note)}" if note else "")
            text = _append_annotation(text, defect_id, ann)
        return text

    _new, _before, after, _problems, changed = _apply_with_guard(
        graph_path, _mutate, profile=prof)
    msg = (f"annotated {status}" if changed else "no-op (already at this status)")
    return _result(defect_id, changed, msg, after, node_id=defect_id,
                   graph_path=graph_path)


__all__ = [
    "report", "attempt", "resolve", "query", "list_defects",
    "DefectResult", "DefectOpsError",
    "DangleRefusedError", "NodeNotFoundError", "NodeExistsError",
]
