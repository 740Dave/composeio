"""composeio.decision_ops.core — the deterministic add/update/query/resolve CORE
over a project decision-graph `.lts` file (e.g.
`decisions/decision-graph-composeux-architect.lts`).

REUSE, do not reimplement: node/edge PARSING and the corpus-level dangle/collision
verdict come from `factlattice` ([DEC-ONE-FACTLATTICE-PARSER]) via `composeio.lts_lint`
(`lint()` / `find_dangles`), exactly per this build's brief. This module does not
parse `.lts` structure itself for `query()` — it hands the file to
`factlattice.parse()` and reads `ParsedCorpus.nodes`/`.properties`.

`add()`/`update()`/`resolve()` DO edit `.lts` TEXT directly (line-level splice, not
a re-derived parser) because `factlattice` is read-only (no `.lts` SERIALIZER back
out of the AST — verified: no such export in `factlattice/__init__.py`). Line-level
editing that (a) matches the corpus's own observed byte format exactly and (b) is
verified afterward by re-running the REAL parser/linter is the smallest correct
surface for a mutating tool; it is not a second parser.

--------------------------------------------------------------------------------
Node format (learned from `decisions/decision-graph-composeux-architect.lts`,
NEVER guessed): one node is exactly

    ### [PREFIX-NAME] Title text
    - lifecycle-position: <value>
    - decided-in-session: <YYYY-MM-DD>
    - importance: <high|medium|low>
    - rationale: <one-line prose>
    - <optional fields: born-from, stage-evidence, re-derive-via, depends-on,
       relates-to, supersedes, governs, note, superseded-by, ...>

separated from its siblings by exactly ONE blank line, with NO leading blank line
between the heading and its first `- key: value` bullet. `add()` reproduces this
byte-for-byte; `update()`/`resolve()` edit only the bullets they touch and leave
every other byte of the node (including free-text annotation lines such as
`- **RESOLVED — ... :** ...` or `- **DANGLE (forward):** ...`, which are a
markdown convention, not a parsed property) untouched.

--------------------------------------------------------------------------------
Four operations:

    add(graph_path, node)                     -- append a new node (idempotent;
                                                  refuses a duplicate id with
                                                  DIFFERENT content; refuses/rolls
                                                  back if it would introduce a NEW
                                                  forward dangle, collision, or
                                                  parse error)
    update(graph_path, node_id, fields)       -- update/insert bullet fields on an
                                                  existing node, preserving
                                                  everything else (idempotent)
    query(graph_path, selector)               -- id / prefix / free-text lookup,
                                                  via factlattice's parse + index;
                                                  returns structured results
                                                  (id, title, lifecycle-position,
                                                  rationale, file:line) -- the
                                                  anti-re-ask gate for
                                                  [FIX-DSD-IS-A-QUERY]
    resolve(graph_path, node_id, ...)         -- ANNOTATE resolved/superseded,
                                                  never delete (idempotent)

All four are guarded the same way: lint the file BEFORE a real (non-no-op) edit,
write, lint AFTER, and roll back to the pre-edit bytes if the AFTER lint shows a
forward dangle / id collision / parse error that was not already present BEFORE
(see `_apply_with_guard`). Pre-existing findings (this repo's real graph already
carries 2 known forward dangles and ~50 orphan warnings — orphans are an accepted
log-style pattern, see module docstring below) are never treated as new failures.

--------------------------------------------------------------------------------
FLAGGED, NOT BUILT HERE (see the build brief + this package's `__init__.py` for
the full notes; do not re-derive any of this from this module alone):
  * MCP-server wrapper   -- a follow-up server layer calls this exact library.
  * Nexus-Magma sync     -- the external Nexus "Magma" service (a DIFFERENT system
    from the local lts-engine, still magma_*-named); out of scope.
  * Live-cutover to sole writer -- the hand-append path stays; this is not wired
    as the only writer anywhere. That switch is David's glass call.
"""
from __future__ import annotations

import datetime
import os
import re
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

import factlattice

from factlattice_engine.lint import DIR_FORWARD, lint as _lts_lint

# WHERE a graph lives is decided by what the operation is ABOUT (the canonical
# checkout), never by where this module's file happens to sit — see graph_home's
# module docstring for the two records lost to that mistake on 2026-08-08.
from .graph_home import (  # noqa: F401  (re-exported for defect_ops + callers)
    WorktreeGraphRefused,
    graph_receipt,
    guard_graph_write,
)

# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class DecisionOpsError(Exception):
    """Base class for all decision_ops failures."""


class NodeNotFoundError(DecisionOpsError):
    pass


class NodeExistsError(DecisionOpsError):
    """`add()` targeted an id that already exists with DIFFERENT content.
    (Identical content is idempotent -- see `add()` -- and is not an error.)"""


class DangleRefusedError(DecisionOpsError):
    """A mutation was rejected and rolled back because it would have introduced a
    NEW forward dangle / id collision / parse error not present before the edit."""

    def __init__(self, message: str, problems: List[str]):
        super().__init__(message)
        self.problems = problems


# --------------------------------------------------------------------------- #
# The node format, as literal regexes over `.lts` TEXT (not a general .lts
# parser -- narrowly the shape this module needs to locate/splice ONE node's
# bullets; whole-corpus semantics are always re-verified via `factlattice`).
# --------------------------------------------------------------------------- #
NODE_ID_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$")
_HEADER_RE = re.compile(r"^(#{2,4})[ \t]+\[([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*)\][ \t]*(.*?)[ \t]*$")
_ANY_HEADING_RE = re.compile(r"^#{1,6}[ \t]")
_FIELD_LINE_RE = re.compile(r"^-[ \t]+([a-z][a-z0-9_\-]*)[ \t]*:[ \t]*(.*)$")

# Canonical bullet order, learned from the real graph's dominant shape. Any key
# outside this set is appended after these, in the order the caller supplied it.
CANONICAL_FIELD_ORDER: Tuple[str, ...] = (
    "lifecycle-position", "decided-in-session", "importance", "rationale",
    "born-from", "stage-evidence", "re-derive-via", "depends-on", "relates-to",
    "supersedes", "resolves", "governs", "note", "superseded-by",
)

# The 4 required-on-every-real-node fields per the build brief's node-format read.
REQUIRED_FIELDS: Tuple[str, ...] = (
    "lifecycle-position", "decided-in-session", "importance", "rationale",
)

# The observed lifecycle vocabulary (informational only -- update()/resolve() do
# NOT hard-reject an unrecognized value; the graph is allowed to grow the set).
KNOWN_LIFECYCLE_VALUES: frozenset = frozenset({
    "raised", "spec", "decided", "committed", "built", "parked", "superseded",
})


def _today() -> str:
    return datetime.date.today().isoformat()


# --------------------------------------------------------------------------- #
# The AMBIENT SESSION TOKEN ([CXC-GV-SCOPE], David 2026-08-06)
#
# `decided-in-session` is the corpus's session key, and it is supplied by the
# CALLER on every `add()`. In practice callers type the DATE, which makes the key
# day-grained: two runs on one night are indistinguishable in the graph, so any
# view scoped by the token merges them.
#
# The seam that fixes that WITHOUT this module learning anything about away
# watches, night watches, or runs: `add()` will fill an OMITTED
# `decided-in-session` from an AMBIENT session token in the process environment.
# Whoever sets that variable decides what a "session" means here; this module
# only reads it. That keeps `decision_ops` generic (it does not import, know
# about, or depend on the retrospective/watch subsystem at all — it reads one
# ambient string), while letting an orchestrator establish a run-grained token
# once, at start, for everything it launches.
#
# THREE PROPERTIES THIS DESIGN MUST HAVE, and how it gets them:
#   OPT-IN BY PRESENCE — with the variable unset, behaviour is EXACTLY as before:
#     an omitted `decided-in-session` still raises the same ValueError. Nothing
#     is defaulted, nothing is invented. An EXPLICIT value always wins, verbatim,
#     so every existing caller is byte-identical.
#   NEVER INVENTED — this resolves a value someone SET; it never derives one from
#     the date, the cwd, the graph, or anything else.
#   HONEST UNDER CONCURRENCY — the token lives in the PROCESS ENVIRONMENT, so it
#     is inherited only by processes the orchestrator started. A second, unrelated
#     instance writing a decision at the same moment has an unset variable and is
#     completely unaffected; there is no shared file or global state to leak
#     across processes.
# --------------------------------------------------------------------------- #
SESSION_ENV = "COMPOSEIO_DECISION_SESSION"


def ambient_session() -> Optional[str]:
    """The ambient session token for this PROCESS (`$COMPOSEIO_DECISION_SESSION`),
    or None when unset/blank.

    Returned VERBATIM (whitespace-stripped only). The corpus's session keys are
    heterogeneous by design — uuids, `2026-07-22`, and run slugs like
    `2026-08-06-away-watch-scope` all coexist and are matched character-for-
    character by consumers — so this never reshapes the value it was given."""
    tok = os.environ.get(SESSION_ENV, "")
    tok = tok.strip() if isinstance(tok, str) else ""
    return tok or None


def _format_value(v: object) -> str:
    if isinstance(v, (list, tuple, set)):
        s = ", ".join(str(x) for x in v)
    else:
        s = str(v)
    # Property values are single-line (`- key: value`); collapse any embedded
    # newlines rather than silently corrupting the node's bullet structure.
    return " ".join(s.split())


# --------------------------------------------------------------------------- #
# Text-level node location (append/splice target, NOT a corpus parse)
# --------------------------------------------------------------------------- #
def _split(text: str) -> List[str]:
    return text.split("\n")


def _join(lines: List[str]) -> str:
    return "\n".join(lines)


def find_node_span(text: str, node_id: str) -> Optional[Tuple[int, int]]:
    """Locate `node_id`'s `### [ID] Title` heading line and its content span
    `(start, end)` (0-indexed into `text.split("\\n")`, end EXCLUSIVE), where the
    span runs to the line before the NEXT heading of any level (mirrors
    `factlattice.parser`'s own node-boundary rule) or EOF. Returns None if the id
    has no `##`-`####` bracketed-id heading in `text`."""
    lines = _split(text)
    start = None
    for i, ln in enumerate(lines):
        m = _HEADER_RE.match(ln)
        if m and m.group(2) == node_id:
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if _ANY_HEADING_RE.match(lines[j]):
            end = j
            break
    return start, end


def _all_node_ids(text: str) -> Set[str]:
    return {m.group(2) for ln in _split(text) if (m := _HEADER_RE.match(ln))}


def _node_header_and_fields(text: str, span: Tuple[int, int]) -> Tuple[str, Dict[str, str]]:
    """`(title, {field: value})` for the node occupying `span` in `text`."""
    lines = _split(text)
    start, end = span
    m = _HEADER_RE.match(lines[start])
    title = m.group(3).strip() if m else ""
    fields: Dict[str, str] = {}
    for i in range(start + 1, end):
        fm = _FIELD_LINE_RE.match(lines[i])
        if fm:
            fields[fm.group(1)] = fm.group(2).strip()
    return title, fields


def _set_fields_in_span(lines: List[str], start: int, end: int,
                        fields: Dict[str, str]) -> Tuple[List[str], int, bool]:
    """Set/insert `- key: value` bullets for `fields` inside `lines[start:end]`.
    Existing bullets are edited IN PLACE (value only); missing keys are inserted
    as new bullets right after the LAST existing field line (so free-text
    annotation lines and the node's trailing blank separator are left where they
    are). Returns `(lines, new_end, changed)`."""
    changed = False
    for key, val in fields.items():
        idx = None
        for i in range(start + 1, end):
            fm = _FIELD_LINE_RE.match(lines[i])
            if fm and fm.group(1) == key:
                idx = i
                break
        newline = f"- {key}: {val}"
        if idx is not None:
            if lines[idx] != newline:
                lines[idx] = newline
                changed = True
        else:
            last_field_idx = start
            for i in range(start + 1, end):
                if _FIELD_LINE_RE.match(lines[i]):
                    last_field_idx = i
            lines.insert(last_field_idx + 1, newline)
            end += 1
            changed = True
    return lines, end, changed


def render_node(node_id: str, title: str, fields: Dict[str, object]) -> str:
    """Render one `### [ID] Title` node block, byte-compatible with the real
    graph's format (canonical field order; unknown keys appended in call order;
    trailing newline, no leading blank line)."""
    out = [f"### [{node_id}] {title}".rstrip()]
    ordered = [k for k in CANONICAL_FIELD_ORDER if k in fields]
    ordered += [k for k in fields if k not in CANONICAL_FIELD_ORDER]
    for k in ordered:
        v = fields[k]
        if v is None:
            continue
        out.append(f"- {k}: {_format_value(v)}")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".decision_ops-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------- #
# The lint guard (`factlattice`/`composeio.lts_lint` CONSUME, never re-derive)
# --------------------------------------------------------------------------- #
def _lint(path: str, profile=None):
    return _lts_lint(path, profile=profile, structure=True, dangle=True)


def _forward_sigs(result) -> Set[Tuple[Optional[str], Optional[str], Optional[str]]]:
    """(source_node, edge_type, missing_target) triples -- content-keyed (not
    line-keyed), so a line-count shift elsewhere in the file (an insertion above
    an unrelated pre-existing dangle) never reads as a spurious NEW dangle."""
    sigs = set()
    for f in result.findings:
        if f.origin == "dangle" and f.kind == DIR_FORWARD:
            edge = f.detail or {}
            sigs.add((edge.get("source"), edge.get("type"), f.node_id))
    return sigs


def _collision_sigs(result) -> Set[str]:
    return {f.node_id for f in result.findings
            if f.origin == "structure" and f.kind == "collisions"}


def _parse_error_sigs(result) -> Set[str]:
    return {f.source_file for f in result.findings
            if f.origin == "structure" and f.kind == "parse_error"}


def _guard_problems(before, after) -> List[str]:
    problems = []
    new_fwd = _forward_sigs(after) - _forward_sigs(before)
    if new_fwd:
        problems.append(f"{len(new_fwd)} new forward dangle(s): {sorted(new_fwd)}")
    new_coll = _collision_sigs(after) - _collision_sigs(before)
    if new_coll:
        problems.append(f"{len(new_coll)} new id collision(s): {sorted(new_coll)}")
    new_perr = _parse_error_sigs(after) - _parse_error_sigs(before)
    if new_perr:
        problems.append(f"{len(new_perr)} new parse error(s): {sorted(new_perr)}")
    return problems


def _apply_with_guard(graph_path: str, mutate, *, profile=None):
    """Read -> mutate (pure function `text -> text`) -> (no-op if unchanged) ->
    write -> lint -> roll back + raise on any NEW forward dangle / collision /
    parse error. Returns `(new_text, before, after, problems, changed)`; `before`/
    `after` are None when `mutate` was a no-op (nothing was ever written, so
    nothing needed re-linting).

    THE WORKTREE GUARD FIRES FIRST ([graph_home.guard_graph_write]). This is the
    ONE write choke point for BOTH decision_ops and defect_ops (defect_ops/core.py
    imports this function verbatim rather than forking it), so installing the guard
    here makes it STRUCTURALLY IMPOSSIBLE for any entry point — CLI, MCP dispatch,
    stdio server, a direct `from ...core import add` — to file a record into a
    linked worktree's copy of a graph. A guard on the MCP layer alone would have
    left the decision_ops CLI (which calls the core directly) wide open.

    It fires BEFORE the read, so even a would-be idempotent NO-OP against a
    worktree graph refuses: the caller INTENDED to write there and must be told."""
    guard_graph_write(graph_path)
    original = _read(graph_path)
    new_text = mutate(original)
    if new_text == original:
        return original, None, None, [], False

    before = _lint(graph_path, profile=profile)
    _atomic_write(graph_path, new_text)
    after = _lint(graph_path, profile=profile)
    problems = _guard_problems(before, after)
    if problems:
        _atomic_write(graph_path, original)  # rollback
        raise DangleRefusedError(
            f"refused: mutation to {graph_path!r} would introduce " + "; ".join(problems),
            problems,
        )
    return new_text, before, after, problems, True


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #
@dataclass
class OpResult:
    ok: bool
    node_id: str
    changed: bool
    message: str
    lint_clean: Optional[bool] = None
    lint_summary: Optional[dict] = None
    # --- the self-proving receipt (ADDED 2026-08-08; nothing above changed) -----
    # WHERE the write landed, absolute + resolved. Without it, a dispatched agent's
    # "recorded the decision" was a CLAIM a human had to `grep` to verify — which is
    # exactly how two defect records were reported as filed while living on no graph.
    graph_path: Optional[str] = None
    graph_is_canonical: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "node_id": self.node_id, "changed": self.changed,
            "message": self.message, "lint_clean": self.lint_clean,
            "lint_summary": self.lint_summary,
            "graph_path": self.graph_path,
            "graph_is_canonical": self.graph_is_canonical,
        }


def _result(node_id: str, changed: bool, message: str, after,
            graph_path: Optional[str] = None) -> OpResult:
    receipt = graph_receipt(graph_path)
    return OpResult(
        ok=True, node_id=node_id, changed=changed, message=message,
        lint_clean=(not after.has_errors) if after is not None else None,
        lint_summary=after.summary() if after is not None else None,
        graph_path=receipt["graph_path"],
        graph_is_canonical=receipt["graph_is_canonical"],
    )


# --------------------------------------------------------------------------- #
# 1. add()
# --------------------------------------------------------------------------- #
def add(graph_path: str, node: Dict[str, object], *, profile=None) -> OpResult:
    """Append a new `[PREFIX-NAME]` node in the exact existing format.

    `node` = {"id": "DEC-FOO-BAR", "title": "...", "lifecycle-position": "...",
              "decided-in-session": "...", "importance": "...", "rationale": "...",
              ...any other bullet fields (re-derive-via, relates-to, depends-on,
              born-from, stage-evidence, supersedes, governs, note, ...)}.

    Idempotent: calling `add()` again with an id + fields that already match the
    node on disk EXACTLY is a no-op (`changed=False`), not an error. A different
    id that collides with existing content raises `NodeExistsError` -- use
    `update()` to change an existing node on purpose.

    `decided-in-session` may be OMITTED, in which case it inherits the AMBIENT
    session token (`$COMPOSEIO_DECISION_SESSION`, see `ambient_session()`) if one
    is set. An explicit value always wins and is written verbatim; with no
    ambient token and no explicit value the call raises, exactly as before.

    Refuses (and rolls back to the pre-call bytes) if appending the node would
    introduce a NEW forward dangle (e.g. a `depends-on:` target that resolves
    nowhere), a NEW id collision, or a NEW parse error -- raises
    `DangleRefusedError`. Pre-existing dangles elsewhere in the graph (the
    orphan log-style pattern, or this repo's 2 known pre-existing forward
    dangles) never trigger a refusal.
    """
    node_id = str(node.get("id") or node.get("node_id") or "")
    title = node.get("title")
    if not NODE_ID_RE.match(node_id):
        raise ValueError(f"invalid or missing node id (want [PREFIX-NAME] shape): {node_id!r}")
    if not title or not str(title).strip():
        raise ValueError("node['title'] is required")
    title = " ".join(str(title).split())

    fields = {k: v for k, v in node.items() if k not in ("id", "node_id", "title")}
    # [CXC-GV-SCOPE] An OMITTED `decided-in-session` inherits the ambient session
    # token when one is set (see `ambient_session`). An EXPLICIT value is never
    # touched, and with no ambient token the field stays missing and the
    # required-fields check below raises exactly as it always has.
    if not fields.get("decided-in-session"):
        _ambient = ambient_session()
        if _ambient:
            fields["decided-in-session"] = _ambient
    missing = [k for k in REQUIRED_FIELDS if not fields.get(k)]
    if missing:
        raise ValueError(f"add({node_id!r}) missing required field(s): {missing}")
    formatted_fields = {k: _format_value(v) for k, v in fields.items() if v is not None}

    def _mutate(original: str) -> str:
        span = find_node_span(original, node_id)
        if span is not None:
            cur_title, cur_fields = _node_header_and_fields(original, span)
            if cur_title == title and cur_fields == formatted_fields:
                return original  # idempotent no-op
            raise NodeExistsError(
                f"add(): node {node_id!r} already exists with different content "
                f"-- use update() to change it on purpose")
        block = render_node(node_id, title, formatted_fields)
        base = original.rstrip("\n")
        sep = "\n\n" if base else ""
        return (base + sep + block) if base else block

    new_text, before, after, problems, changed = _apply_with_guard(graph_path, _mutate, profile=profile)
    msg = ("appended" if changed else "no-op (identical node already present)")
    return _result(node_id, changed, msg, after, graph_path=graph_path)


# --------------------------------------------------------------------------- #
# 2. update()
# --------------------------------------------------------------------------- #
def update(graph_path: str, node_id: str, fields: Dict[str, object], *, profile=None) -> OpResult:
    """Update (or add) bullet fields on an existing node -- e.g.
    `update(path, "DEC-FOO", {"lifecycle-position": "committed"})` for the
    raised -> decided -> committed progression. Preserves everything else in the
    node (title, other fields, free-text annotation lines) byte-for-byte.
    Idempotent: setting a field to its current value is a no-op.
    """
    if not fields:
        raise ValueError("update() needs at least one field to set")
    formatted = {k: _format_value(v) for k, v in fields.items() if v is not None}

    def _mutate(original: str) -> str:
        span = find_node_span(original, node_id)
        if span is None:
            raise NodeNotFoundError(f"update(): node {node_id!r} not found in {graph_path!r}")
        lines = _split(original)
        start, end = span
        lines, end, changed = _set_fields_in_span(lines, start, end, formatted)
        return _join(lines) if changed else original

    new_text, before, after, problems, changed = _apply_with_guard(graph_path, _mutate, profile=profile)
    msg = ("updated" if changed else "no-op (fields already at requested values)")
    return _result(node_id, changed, msg, after, graph_path=graph_path)


# --------------------------------------------------------------------------- #
# 3. query()
# --------------------------------------------------------------------------- #
Selector = Union[str, Dict[str, object], None]


def _parse_selector(selector: Selector) -> Dict[str, Optional[str]]:
    if selector is None:
        return {}
    if isinstance(selector, dict):
        return {
            "id": selector.get("id"),
            "prefix": selector.get("prefix"),
            "text": selector.get("text"),
        }
    if isinstance(selector, str):
        s = selector.strip()
        if s.startswith("id:"):
            return {"id": s[3:].strip()}
        if s.startswith("prefix:"):
            return {"prefix": s[7:].strip()}
        if s.startswith("text:"):
            return {"text": s[5:].strip()}
        if NODE_ID_RE.match(s):
            return {"id": s}
        return {"text": s}
    raise TypeError(f"query() selector must be a str or dict, got {type(selector)!r}")


def query(graph_path: str, selector: Selector = None, *, profile=None,
         limit: Optional[int] = None) -> List[dict]:
    """Return matching nodes by id / prefix / free-text -- THE anti-re-ask gate
    ([FIX-DSD-IS-A-QUERY]): a DSD/question-generation candidate must be run
    through this before being asked, and dropped if a committed node already
    answers it.

    `selector` forms (all case-sensitive on id/prefix, case-insensitive on text):
        "DEC-FOO-BAR"        -- exact id (auto-detected: looks like [PREFIX-NAME])
        "id:DEC-FOO-BAR"     -- exact id, explicit
        "prefix:DEC"         -- every node whose id's PREFIX segment is "DEC"
        "text:context level" -- free-text substring over id/title/rationale/all
                                 properties
        {"id": ..., "prefix": ..., "text": ...}  -- any combination, AND'd

    Consumes `factlattice.parse()` (never a bespoke parser) for the corpus/graph;
    reads `ParsedCorpus.nodes` directly -- no re-derivation of the .lts grammar.

    Returns a list of dicts: {id, title, type_prefix, lifecycle_position,
    importance, decided_in_session, rationale, source_file, line}, in file order.
    """
    sel = _parse_selector(selector)
    corpus = factlattice.parse(graph_path, profile=profile)
    out: List[dict] = []
    for node in corpus.nodes:
        if sel.get("id") and node.id != sel["id"]:
            continue
        if sel.get("prefix") and node.type_prefix != sel["prefix"]:
            continue
        if sel.get("text"):
            needle = sel["text"].lower()
            haystack_parts = [node.id, node.title]
            for k, v in node.properties.items():
                haystack_parts.append(k)
                haystack_parts.append(_format_value(v))
            haystack = " ".join(haystack_parts).lower()
            if needle not in haystack:
                continue
        out.append({
            "id": node.id,
            "title": node.title,
            "type_prefix": node.type_prefix,
            "lifecycle_position": node.properties.get("lifecycle-position"),
            "importance": node.properties.get("importance"),
            "decided_in_session": node.properties.get("decided-in-session"),
            "rationale": node.properties.get("rationale"),
            "source_file": node.source_file,
            "line": node.line_range[0] if node.line_range else None,
        })
    if limit is not None:
        out = out[:limit]
    return out


# --------------------------------------------------------------------------- #
# 4. resolve()
# --------------------------------------------------------------------------- #
def resolve(graph_path: str, node_id: str, *, status: Optional[str] = None,
           superseded_by: Optional[str] = None, note: Optional[str] = None,
           profile=None, today: Optional[str] = None) -> OpResult:
    """Mark a node resolved/superseded by ANNOTATION -- never deletes, per the
    decision-lifecycle convention (`- **RESOLVED — ... :** ...`, matching the
    real graph's existing `- **RESOLVED — base/shared ...:**` convention).

    Exactly one of `status=` (e.g. "resolved", "parked") or `superseded_by=`
    (a node id -- sets `lifecycle-position: superseded` + a `superseded-by:`
    bullet on THIS node; does NOT touch the superseding node) must be given.
    `note=` is optional free text appended to the annotation.

    Idempotent: a repeat call with the same `status`/`superseded_by` is a no-op
    once both the field state AND the annotation are already present (the
    annotation's date is not re-diffed byte-for-byte across days -- its LABEL
    prefix is the idempotency key).
    """
    if not status and not superseded_by:
        raise ValueError("resolve() needs status= and/or superseded_by=")
    date = today or _today()
    label = f"superseded by [{superseded_by}]" if superseded_by else str(status)
    target_lifecycle = "superseded" if superseded_by else str(status)
    annotation_prefix = f"- **RESOLVED — {label} ("

    def _mutate(original: str) -> str:
        span = find_node_span(original, node_id)
        if span is None:
            raise NodeNotFoundError(f"resolve(): node {node_id!r} not found in {graph_path!r}")
        lines = _split(original)
        start, end = span
        _, cur_fields = _node_header_and_fields(original, span)

        already_annotated = any(
            lines[i].startswith(annotation_prefix) for i in range(start + 1, end))
        field_updates = {"lifecycle-position": target_lifecycle}
        if superseded_by:
            field_updates["superseded-by"] = f"[{superseded_by}]"
        fields_already_set = all(
            cur_fields.get(k) == v for k, v in field_updates.items())

        if already_annotated and fields_already_set:
            return original  # fully idempotent no-op

        lines, end, fields_changed = _set_fields_in_span(lines, start, end, field_updates)

        if not already_annotated:
            note_txt = " ".join((note or "").split())
            ann = f"{annotation_prefix}{date}):**" + (f" {note_txt}" if note_txt else "")
            last_field_idx = start
            for i in range(start + 1, end):
                if _FIELD_LINE_RE.match(lines[i]):
                    last_field_idx = i
            lines.insert(last_field_idx + 1, ann)

        return _join(lines)

    new_text, before, after, problems, changed = _apply_with_guard(graph_path, _mutate, profile=profile)
    msg = ("annotated resolved/superseded" if changed
           else "no-op (already resolved with this same status)")
    return _result(node_id, changed, msg, after, graph_path=graph_path)


__all__ = [
    "add", "update", "query", "resolve",
    "OpResult", "DecisionOpsError", "NodeNotFoundError", "NodeExistsError",
    "DangleRefusedError",
    "render_node", "find_node_span",
    "NODE_ID_RE", "CANONICAL_FIELD_ORDER", "REQUIRED_FIELDS", "KNOWN_LIFECYCLE_VALUES",
]
