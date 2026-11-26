#!/usr/bin/env python3
"""db.py — the SQLite METRICS store for the session-retrospective's DETERMINISTIC capture-types
([SPEC:CXC-RETRO] §5a: "SQLite — the METRICS/analytics store (quantitative, tabular, time-series)").

This is the RAW/TALLIED side of the split, not the DECISIONS side: every table here holds counts,
timestamps, and verbatim evidence quotes — never a score, a verdict, or a judgment about the human or
the instance (the hard boundary this whole package builds to; see `pregenerate.py`'s docstring). The
judgment capture-types (decisions, corrections' axis-clustering, dusk-gift fodder) live in the
project decision-LTS via the MCP write surface (§5, not built here).

## Schema (CREATE TABLE statements — also printed by `python -m intelligence.retrospective.db --schema`)

`sessions` — one row per transcript, upserted on (re)injection:
  session_id                      PK. From the record's `sessionId`, else the filename stem.
  transcript_path                 The JSONL path last injected from (informational).
  start_ts / end_ts               Epoch seconds of the first/last record with a parseable timestamp.
  record_count                    Total JSON records parsed (idempotency/sanity signal).
  handover_start_ts / handover_end_ts / handover_cost_pct
                                   Mirror the LAST (most recent) HANDOVER_START/END pair found — the
                                   crossing that actually matters for "how much did THIS wrap cost."
                                   ALL pairs found are also kept in `handover_events` below.
  context_pct_at_handover_start / context_pct_at_handover_end
                                   The context-% reading nearest each marker, when available. FED BY:
                                   the statusline/hud snapshot (self_publisher.py's `_read_hud_cache`
                                   reads Claude Code's real `used_percentage`, which is delivered ONLY
                                   to the statusline — never the JSONL, per [SPEC:CXC-RETRO] §5b), or
                                   a caller-supplied sample (`context_samples` — see inject.py) for a
                                   future periodic sampler. THIS TRAJECTORY IS THIN BY DESIGN: today
                                   there is at most one live snapshot (the current reading), not a
                                   logged history, so a marker whose moment wasn't sampled stays NULL
                                   rather than guessing. handover_cost_pct is NULL until both ends of
                                   a pair have a real reading.
  context_trajectory_source       'sample-provided' | 'unavailable' — which of the above fed the pair
                                   mirrored onto this row (documents the "thin" provenance per row).
  injected_at                     Epoch seconds of the last (re)injection (bookkeeping only).

`handover_events` — every HANDOVER_START/END pair found in a session (deterministic marker match on
  the transcript's Bash tool_use/tool_result text, per the `handover` skill's stamped markers):
  session_id, seq (1-based order) PK. start_ts, end_ts, cost_pct, context_pct_start, context_pct_end,
  context_source (per-pair provenance, same vocabulary as `sessions.context_trajectory_source`).

`file_reads` — capture-type 5 (read-frequency), deliberate-vs-incidental per §3:
  session_id, path, deliberate (1/0) PK. count. `deliberate=1` = an explicit `Read` tool_use on that
  path. `deliberate=0` = the path appeared in a `Grep`/`Glob` tool_result (incidental — surfaced by a
  search, not opened on purpose). Both are raw tallies; no ranking/weighting happens here (that is
  §3.1's empirical-bootstrap SELECT, a downstream consumer, not this store).

`repetitive_commands` — capture-type 2 (repetitive-task frequency):
  session_id, tool, signature PK. count. `signature` is the tool's representative arg VERBATIM
  (Bash -> exact `command` string; other tools -> their file/path arg, else the tool name) — an
  EXACT-MATCH tally, not fuzzy clustering (fuzzy "same task, different flags" grouping is a judgment
  call about intent, left to a future refinement / the act-half, not built here).

`magma_queries` — capture-type 6 (Magma-query frequency + NULL-result corpus-gaps, §2a):
  session_id, tool, query PK. call_count, null_count. `null_count` > 0 is the raw corpus-gap FLAG
  (the spec's "negative-space signal") — this store never decides what's missing or drafts the fix,
  it only tallies that a query came back empty.

`token_spikes` — capture-type 7 (token-spikes), one row per detected spike event:
  session_id, event_hash PK (sha256 of session_id+turn+ts+tokens — content-derived, so re-injection
  never dupes). turn, ts, tokens, prev_tokens, reason ('absolute' the turn alone crossed the
  threshold, or 'delta' the jump vs the previous turn did). Thresholds are tunable per §7's config
  surface (inject.py CLI flags here; a config-surface wire-up is future work).

`correction_markers` — capture-type 3, THE RAW SLICE ONLY (see pregenerate.py's docstring for the
  tiering nuance — the spec calls corrections a judgment capture-type; what's staged here is only the
  deterministic PATTERN MATCH + verbatim quote, never the axis-clustering or the feedback-memory
  routing, which stay judgment and are NOT built here):
  session_id, event_hash PK. turn, ts, speaker ('user'|'assistant'), marker (pattern label), quote
  (verbatim, truncated).

`hedging_markers` — capture-type 8 (hedging-markers, spec tier "deterministic-ish"):
  session_id, event_hash PK. turn, ts, marker (pattern label), quote (verbatim, truncated).

`away_watch` — the AWAY-WATCH history (the overnight/away build-watch sessions the Architect pops up
  as a "Night Watch" window; David 2026-07-22 "maintain a list of Away Watch entries ... scroll through
  prior night watches, clicking on one pulls up the window"). Purely a RAW TALLY of a completed watch —
  counts + timestamps + artifact pointers, no judgment (consistent with this store's hard boundary):
  id                              PK (autoincrement).
  session_id                      The originating session, when known (informational).
  label                           Human label ("Overnight 2026-07-22"). UNIQUE — re-recording the same
                                   label UPSERTS (idempotent seeding), never dupes.
  started_ts / ended_ts           Epoch seconds bounding the watch (either may be NULL).
  commits / subagents / tests     A legacy tally (retained; still projected). The Night Watch window no
                                   longer surfaces these — David 2026-07-22 swapped in decisions/tokens/
                                   elapsed — but they stay ADDITIVELY so nothing that read them breaks.
  decisions                       The night's decision-node count (harvest nodes + stubs) — a window metric.
  tokens                          Tokens consumed across the watch (raw count; the window formats "23.7M").
  tasks                           JSON list of {label, status} where status ∈ {done|partial|deferred} — the
                                   session's task checklist the Night Watch window renders with distinct
                                   visual states. An unknown status normalizes to `deferred` (conservative:
                                   "not confirmed done"), never dropped.
  git_state                       A short human summary of the working tree at the watch's close ("clean",
                                   or "13 modified · 1 stash · 89 ahead of origin"). Computed LIVE at seed
                                   time via `git_state_summary()` (git status --porcelain); degrades to "" if
                                   the repo/git is unavailable.
  artifacts                       JSON list of {title, path} — the build log etc., opened READ-ONLY in
                                   the console doc-browser.
  scene_path                      The .compose.yaml to relaunch as the Night Watch window (the click seam).
  recorded_at                     Epoch seconds this row was (re)recorded (bookkeeping).
  Recorder/query API: `record_away_watch(...)` / `list_away_watches()` (most-recent first — each row also
  carries a DERIVED `elapsed` in seconds when both timestamps are present, so start/end is the single source
  of truth for elapsed, not a stored duplicate). The console's away_watch_publisher projects
  `list_away_watches()` onto the bus namespace `self/away_watches`.

  Schema evolution: the four columns decisions/tokens/tasks/git_state were added ADDITIVELY (David 2026-07-22).
  A pre-existing DB is migrated in `connect()` via `ALTER TABLE ADD COLUMN` (guarded by PRAGMA table_info),
  so the columns land with NULL defaults on old rows and no data is lost.

  Idle-tracking ([SPEC:CXC-IDLE-TRACKING], David 2026-07-23) added TWO more columns under the SAME additive
  discipline: `build_complete_ts REAL` (stamped by `mark_build_complete(label, ts)` when the away-build queue
  goes dry and the Architect enters holding) and `resumed_ts REAL` (stamped by `mark_resumed(label, ts)` on
  the first human inbound after completion — idempotent + guarded: only when resumed is unset AND
  build_complete is set). `idle_seconds` is DERIVED in `list_away_watches()` (`resumed_ts - build_complete_ts`,
  NULL until both ends exist), never a stored column — exactly like `elapsed`. `record_away_watch` gains
  matching kwargs (default None) that are UPSERT-PRESERVING (COALESCE — a metrics re-record never clobbers a
  stamped boundary). The away_watch_publisher (on main) reads these via the projection and is unaffected.

  Run->session SCOPE ([CXC-GV-SCOPE], David 2026-08-06) added TWO more columns under the SAME additive
  discipline, plus one new table:
    `sessions TEXT`               JSON list of the VERBATIM `decided-in-session` tokens this run OBSERVED
                                   (never normalized, never derived — see `observe_watch_sessions()`).
                                   Projected onto each row by `list_away_watches()` as a real list (`[]`
                                   when the column is NULL), which is exactly what the Night Watch window's
                                   "View Decisions" button needs to scope the Graph Viewer to THIS run.
    `decision_baseline_ts REAL`   When the OPEN-OF-WINDOW decision-corpus snapshot was taken. NULL means
                                   NO snapshot exists, which means the run CANNOT honestly name its
                                   sessions — the observation window is gone and nothing is recorded.
                                   This column is what distinguishes "baseline absent" (unscopeable) from
                                   "baseline taken and the corpus was empty" (legitimately zero new nodes);
                                   without it an absent baseline would make EVERY node look new and the run
                                   would claim the whole corpus (the exact fabrication this design forbids).
  `away_watch_decision_baseline`  (label, node_id) PK — the decision-node id set present at watch OPEN.
                                   The close-of-watch diff against this set IS the observation; the tokens
                                   of the nodes that are new are the run's `sessions`. Written by
                                   `snapshot_decision_baseline()`, read by `decision_baseline()`. The
                                   OBSERVER that drives both (corpus scan + diff) lives one layer up in
                                   `compose_projects/intelligence/retrospective/watch_sessions.py` — this store never
                                   parses `.lts` and never infers a mapping.

`defect_investigations` — the DEFECT-INVESTIGATION history ([DEC-DEFECT-TRACKING-STORE], [SPEC:CXC-DEFECT-STORE]):
  the SQLite-PRIMARY aggregate signal, one row per defect-investigator run, complementing the LTS `[DEFECT-*]`
  recurrence GRAPH (the spec defines that convention). Columns mirror the agent's final-text deliverable:
  id (PK autoincrement), ts, symptom (short), area (area/file), root_cause_class (normalized to
  under-spec|oversight|fork-divergence|spec-drift|other), recurrence (0/1) + prior_fix_ref, under_spec (0/1),
  fork (0/1), spec_added (a spec ref), test_added (0/1), resolution, branch. Purely a RAW TALLY — no verdict,
  consistent with this store's hard boundary. API: `record_defect_investigation(...)` (append; returns row id),
  `list_defect_investigations(since=None)`, and `defects_by_root_cause(since=None)` — the {class: count}
  histogram (count desc) that IS the process-improvement signal.

## DB file path (config-overridable; NOT committed)
Default: `compose_projects/intelligence/retrospective/data/metrics.db` (repo-relative, this package's own `data/` dir).
Override: the `COMPOSEIO_RETRO_DB` environment variable (an absolute path). The default directory is
created on first connect. **The `.db` file is gitignored** (`compose_projects/intelligence/retrospective/data/` — see
the repo `.gitignore`) — it is a regenerable derived artifact (re-inject the transcripts to rebuild),
never a source-of-truth file, so it must never be committed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

# the four columns added ADDITIVELY to away_watch (David 2026-07-22) — kept in
# one place so the CREATE (SCHEMA) and the ALTER-migration can never drift.
_AWAY_WATCH_ADDED_COLUMNS = (
    ("decisions", "INTEGER"),
    ("tokens", "INTEGER"),
    ("tasks", "TEXT"),
    ("git_state", "TEXT"),
    # idle-tracking ([SPEC:CXC-IDLE-TRACKING], David 2026-07-23) — added under the
    # SAME additive discipline as the four above: two REAL timestamps; idle_seconds
    # is DERIVED in list_away_watches() (never a stored column, mirrors `elapsed`).
    ("build_complete_ts", "REAL"),
    ("resumed_ts", "REAL"),
    # run->session scope ([CXC-GV-SCOPE], David 2026-08-06) — same additive
    # discipline again. `sessions` is a JSON list of the VERBATIM
    # `decided-in-session` tokens this run OBSERVED on decision nodes that
    # appeared inside its window; `decision_baseline_ts` stamps WHEN the
    # open-of-window corpus snapshot was taken (NULL = never taken = this run
    # cannot honestly name its sessions; see `decision_baseline()`).
    ("sessions", "TEXT"),
    ("decision_baseline_ts", "REAL"),
    # the run's DECLARED identity (the slug the start script establishes and
    # exports as the ambient session token). NOT the run's scope — `sessions` is,
    # and it stays purely OBSERVED. Storing the declaration separately is what
    # lets the close report an honest MISMATCH ("declared X, observed [Y]").
    ("run_slug", "TEXT"),
)

_TASK_STATUSES = ("done", "partial", "deferred")

# defect_investigations columns added ADDITIVELY after the table shipped
# ([SPEC:CIO-DEFECT-REGISTRY] — the graph<->SQLite cross-link). Same additive
# discipline as _AWAY_WATCH_ADDED_COLUMNS: `CREATE TABLE IF NOT EXISTS` never
# alters an existing table, so a DB created before this build needs an explicit
# `ALTER TABLE ADD COLUMN` for each. `defect_id` carries the graph DEFECT node id
# so an aggregate row cross-links to its recurrence-graph node.
_DEFECT_INVESTIGATIONS_ADDED_COLUMNS = (
    ("defect_id", "TEXT"),
)

# defect-investigation root-cause taxonomy ([DEC-DEFECT-TRACKING-STORE]) — the
# by-root-cause rollup over these IS the process-improvement signal. An unknown /
# absent class normalizes to "other" (conservative — never silently dropped);
# the defect-investigator agent's "implementation-oversight" collapses to
# "oversight" so the histogram has one bucket per root cause.
_ROOT_CAUSE_CLASSES = ("under-spec", "oversight", "fork-divergence", "spec-drift", "other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id                      TEXT PRIMARY KEY,
    transcript_path                 TEXT,
    start_ts                        REAL,
    end_ts                          REAL,
    record_count                    INTEGER,
    handover_start_ts               REAL,
    handover_end_ts                 REAL,
    handover_cost_pct               REAL,
    context_pct_at_handover_start   REAL,
    context_pct_at_handover_end     REAL,
    context_trajectory_source       TEXT,
    injected_at                     REAL
);

CREATE TABLE IF NOT EXISTS handover_events (
    session_id          TEXT,
    seq                 INTEGER,
    start_ts             REAL,
    end_ts               REAL,
    cost_pct             REAL,
    context_pct_start    REAL,
    context_pct_end      REAL,
    context_source       TEXT,
    PRIMARY KEY (session_id, seq)
);

CREATE TABLE IF NOT EXISTS file_reads (
    session_id    TEXT,
    path          TEXT,
    deliberate    INTEGER,
    count         INTEGER,
    PRIMARY KEY (session_id, path, deliberate)
);

CREATE TABLE IF NOT EXISTS repetitive_commands (
    session_id    TEXT,
    tool          TEXT,
    signature     TEXT,
    count         INTEGER,
    PRIMARY KEY (session_id, tool, signature)
);

CREATE TABLE IF NOT EXISTS magma_queries (
    session_id    TEXT,
    tool          TEXT,
    query         TEXT,
    call_count    INTEGER,
    null_count    INTEGER,
    PRIMARY KEY (session_id, tool, query)
);

CREATE TABLE IF NOT EXISTS token_spikes (
    session_id    TEXT,
    event_hash    TEXT,
    turn          INTEGER,
    ts            REAL,
    tokens        INTEGER,
    prev_tokens   INTEGER,
    reason        TEXT,
    PRIMARY KEY (session_id, event_hash)
);

CREATE TABLE IF NOT EXISTS correction_markers (
    session_id    TEXT,
    event_hash    TEXT,
    turn          INTEGER,
    ts            REAL,
    speaker       TEXT,
    marker        TEXT,
    quote         TEXT,
    PRIMARY KEY (session_id, event_hash)
);

CREATE TABLE IF NOT EXISTS hedging_markers (
    session_id    TEXT,
    event_hash    TEXT,
    turn          INTEGER,
    ts            REAL,
    marker        TEXT,
    quote         TEXT,
    PRIMARY KEY (session_id, event_hash)
);

CREATE TABLE IF NOT EXISTS away_watch (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT,
    label         TEXT UNIQUE,
    started_ts    REAL,
    ended_ts      REAL,
    commits       INTEGER,
    subagents     INTEGER,
    tests         INTEGER,
    artifacts     TEXT,
    scene_path    TEXT,
    recorded_at   REAL,
    decisions     INTEGER,
    tokens        INTEGER,
    tasks         TEXT,
    git_state     TEXT,
    build_complete_ts REAL,
    resumed_ts        REAL,
    sessions          TEXT,
    decision_baseline_ts REAL,
    run_slug          TEXT
);

CREATE TABLE IF NOT EXISTS away_watch_decision_baseline (
    label         TEXT,
    node_id       TEXT,
    PRIMARY KEY (label, node_id)
);

CREATE TABLE IF NOT EXISTS defect_investigations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts                REAL,
    symptom           TEXT,
    area              TEXT,
    root_cause_class  TEXT,
    recurrence        INTEGER,
    prior_fix_ref     TEXT,
    under_spec        INTEGER,
    fork              INTEGER,
    spec_added        TEXT,
    test_added        INTEGER,
    resolution        TEXT,
    branch            TEXT,
    defect_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_magma_null ON magma_queries (session_id, null_count);
CREATE INDEX IF NOT EXISTS idx_token_spikes_session ON token_spikes (session_id);
CREATE INDEX IF NOT EXISTS idx_away_watch_started ON away_watch (started_ts);
CREATE INDEX IF NOT EXISTS idx_defect_root_cause ON defect_investigations (root_cause_class);
CREATE INDEX IF NOT EXISTS idx_defect_ts ON defect_investigations (ts);
"""


def _repo_root() -> str:
    # This file is compose_projects/intelligence/<sub>/<me>.py, so the repo root is FOUR
    # dirnames up. Three stops at compose_projects/ -- see the doubled-metrics.db defect.
    return os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))


def default_db_path() -> str:
    return os.path.join(_repo_root(), "compose_projects", "intelligence", "retrospective", "data", "metrics.db")


def db_path() -> str:
    """The active DB path: `COMPOSEIO_RETRO_DB` env override, else the package-local default."""
    override = os.environ.get("COMPOSEIO_RETRO_DB", "").strip()
    return override if override else default_db_path()


def connect(path: str | None = None) -> sqlite3.Connection:
    """Open (creating + schema-initializing if needed) the metrics DB. Idempotent: safe to call every
    run — `CREATE TABLE IF NOT EXISTS` never disturbs existing data."""
    p = path or db_path()
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate_away_watch(conn)
    _migrate_defect_investigations(conn)
    conn.commit()
    return conn


def _migrate_away_watch(conn: sqlite3.Connection) -> None:
    """Additively bring an EXISTING away_watch table up to the current column
    set (David 2026-07-22 added decisions/tokens/tasks/git_state). `CREATE TABLE
    IF NOT EXISTS` never alters an existing table, so a DB created before the
    change needs an explicit `ALTER TABLE ADD COLUMN` per missing column — the
    safe path: it lands the column with NULL on old rows and touches nothing
    else. Idempotent (guarded by PRAGMA table_info); safe to call every connect."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(away_watch)")}
    if not have:                                   # table absent (fresh DB path)
        return
    for name, sqltype in _AWAY_WATCH_ADDED_COLUMNS:
        if name not in have:
            conn.execute(f"ALTER TABLE away_watch ADD COLUMN {name} {sqltype}")


def _migrate_defect_investigations(conn: sqlite3.Connection) -> None:
    """Additively bring an EXISTING defect_investigations table up to the current
    column set ([SPEC:CIO-DEFECT-REGISTRY] added `defect_id` for the graph<->SQLite
    cross-link). Same safe path as _migrate_away_watch: `ALTER TABLE ADD COLUMN`
    lands the column with NULL on old rows and touches nothing else. Idempotent
    (guarded by PRAGMA table_info); safe to call every connect."""
    have = {row[1] for row in conn.execute("PRAGMA table_info(defect_investigations)")}
    if not have:                                   # table absent (fresh DB path)
        return
    for name, sqltype in _DEFECT_INVESTIGATIONS_ADDED_COLUMNS:
        if name not in have:
            conn.execute(
                f"ALTER TABLE defect_investigations ADD COLUMN {name} {sqltype}")


# ---------------------------------------------------------------------------
# away_watch recorder + query API (the Away-Watch history feature)
# ---------------------------------------------------------------------------

def _normalize_artifacts(artifacts: Any) -> List[Dict[str, str]]:
    """Coerce an artifacts arg into a clean [{title, path}] list (drops junk)."""
    out: List[Dict[str, str]] = []
    for a in artifacts or []:
        if isinstance(a, dict) and a.get("path"):
            out.append({"title": str(a.get("title") or os.path.basename(str(a["path"]))),
                        "path": str(a["path"])})
    return out


def _normalize_tasks(tasks: Any) -> List[Dict[str, str]]:
    """Coerce a tasks arg into a clean [{label, status}] list (status ∈
    {done|partial|deferred}). A row without a label is dropped; an unknown/absent
    status normalizes to `deferred` (conservative — "not confirmed done", never a
    fake `done`). Accepts dicts or a bare "label" string (status -> deferred)."""
    out: List[Dict[str, str]] = []
    for it in tasks or []:
        if isinstance(it, dict):
            label = str(it.get("label") or it.get("name") or "").strip()
            status = str(it.get("status", "")).strip().lower()
        elif isinstance(it, str):
            label, status = it.strip(), ""
        else:
            continue
        if not label:
            continue
        if status not in _TASK_STATUSES:
            status = "deferred"
        out.append({"label": label, "status": status})
    return out


def _normalize_sessions(sessions: Any) -> List[str]:
    """Coerce a sessions arg into a clean list of session TOKENS, VERBATIM.

    [CXC-GV-SCOPE] The ONLY transformations allowed here are whitespace-strip,
    drop-empty, and de-dup (first occurrence wins, order preserved). The tokens
    are matched CHARACTER-FOR-CHARACTER against each decision node's
    `decided-in-session` property, and that property is HETEROGENEOUS in the real
    corpus — session UUIDs (`59f1b46e-3cde-...`), bare dates (`2026-07-22`), and
    date-slugs (`2026-08-05-cic-composite-esp32-fan-continuation`) all occur. So
    this must NOT canonicalize a date, lower-case a uuid, or collapse a slug to
    its date prefix: any such "tidying" turns a match into a miss. A run that
    genuinely spans tokens of MIXED shape records them all, as they appear."""
    out: List[str] = []
    seen: set = set()
    if isinstance(sessions, (str, bytes)):        # a bare token, not a sequence
        sessions = [sessions]
    for s in sessions or []:
        if s is None:                             # a missing token is not "None"
            continue
        tok = s.decode() if isinstance(s, bytes) else str(s)
        tok = tok.strip()
        if not tok or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def git_state_summary(repo_root: Optional[str] = None) -> str:
    """A short, human summary of the working tree — "clean", or a compact
    "N modified · M untracked · K stash · A ahead of origin" (only the non-zero
    parts). Computed LIVE from `git status --porcelain` (+ stash/ahead counts).
    Never raises: any git/OS failure (not a repo, git absent) returns "" so a
    missing capability reads as blank, not a fake state."""
    root = repo_root or _repo_root()

    def _git(*args: str) -> Optional[str]:
        try:
            out = subprocess.run(("git", "-C", root, *args),
                                 capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    porcelain = _git("status", "--porcelain")
    if porcelain is None:
        return ""
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    modified = sum(1 for ln in lines if not ln.startswith("??"))
    untracked = sum(1 for ln in lines if ln.startswith("??"))
    parts: List[str] = []
    if modified:
        parts.append(f"{modified} uncommitted")
    if untracked:
        parts.append(f"{untracked} untracked")
    stash = _git("stash", "list")
    n_stash = len([ln for ln in stash.splitlines() if ln.strip()]) if stash else 0
    if n_stash:
        parts.append(f"{n_stash} stash")
    ahead = _git("rev-list", "--count", "@{u}..HEAD")
    try:
        n_ahead = int((ahead or "").strip())
    except ValueError:
        n_ahead = 0
    if n_ahead:
        parts.append(f"{n_ahead} ahead of origin")
    return " · ".join(parts) if parts else "clean"


def record_away_watch(conn: Optional[sqlite3.Connection] = None, *,
                      label: str,
                      session_id: Optional[str] = None,
                      started_ts: Optional[float] = None,
                      ended_ts: Optional[float] = None,
                      commits: int = 0,
                      subagents: int = 0,
                      tests: int = 0,
                      artifacts: Optional[List[Dict[str, str]]] = None,
                      scene_path: str = "",
                      recorded_at: Optional[float] = None,
                      decisions: int = 0,
                      tokens: int = 0,
                      tasks: Optional[List[Dict[str, str]]] = None,
                      git_state: str = "",
                      build_complete_ts: Optional[float] = None,
                      resumed_ts: Optional[float] = None,
                      sessions: Optional[List[str]] = None,
                      run_slug: Optional[str] = None) -> int:
    """Record ONE Away-Watch entry (upsert on `label` — idempotent seeding).

    Returns the row id. Opens/owns a connection when `conn` is None (and closes
    it); with a caller-supplied `conn` the caller owns commit/close lifetime.

    The idle-tracking timestamps (`build_complete_ts`, `resumed_ts`,
    [SPEC:CXC-IDLE-TRACKING]) are UPSERT-PRESERVING: a re-record that leaves them
    None does NOT wipe a value already stamped by `mark_build_complete` /
    `mark_resumed` (COALESCE keeps the existing value), so a metrics refresh
    can't clobber an idle boundary the Architect already marked.

    `sessions` ([CXC-GV-SCOPE]) follows the SAME upsert-preserving rule: a
    metrics re-record that omits it keeps whatever `observe_watch_sessions()`
    has already accumulated. Passing an explicit list REPLACES the stored list
    (an explicit caller assertion); passing None never clears it. Tokens are
    stored VERBATIM (`_normalize_sessions` only strips/de-dups).

    `run_slug` is the run's DECLARED identity (the token its start script
    exported), also upsert-preserving. It is deliberately NOT the run's scope: a
    declaration is a statement about decisions that may never be written, so it
    can never be handed to the viewer as if it had been observed. It exists so
    the close can compare declared-vs-observed and report a mismatch."""
    own = conn is None
    c = conn or connect()
    try:
        c.execute(
            """INSERT INTO away_watch
                 (session_id, label, started_ts, ended_ts, commits, subagents,
                  tests, artifacts, scene_path, recorded_at,
                  decisions, tokens, tasks, git_state,
                  build_complete_ts, resumed_ts, sessions, run_slug)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(label) DO UPDATE SET
                 session_id=excluded.session_id, started_ts=excluded.started_ts,
                 ended_ts=excluded.ended_ts, commits=excluded.commits,
                 subagents=excluded.subagents, tests=excluded.tests,
                 artifacts=excluded.artifacts, scene_path=excluded.scene_path,
                 recorded_at=excluded.recorded_at, decisions=excluded.decisions,
                 tokens=excluded.tokens, tasks=excluded.tasks,
                 git_state=excluded.git_state,
                 build_complete_ts=COALESCE(excluded.build_complete_ts,
                                            away_watch.build_complete_ts),
                 resumed_ts=COALESCE(excluded.resumed_ts,
                                     away_watch.resumed_ts),
                 sessions=COALESCE(excluded.sessions, away_watch.sessions),
                 run_slug=COALESCE(excluded.run_slug, away_watch.run_slug)""",
            (session_id, label, started_ts, ended_ts, int(commits or 0),
             int(subagents or 0), int(tests or 0),
             json.dumps(_normalize_artifacts(artifacts)), scene_path,
             recorded_at if recorded_at is not None else time.time(),
             int(decisions or 0), int(tokens or 0),
             json.dumps(_normalize_tasks(tasks)), str(git_state or ""),
             build_complete_ts, resumed_ts,
             (json.dumps(_normalize_sessions(sessions))
              if sessions is not None else None),
             (str(run_slug).strip() or None) if run_slug is not None else None))
        c.commit()
        row = c.execute("SELECT id FROM away_watch WHERE label = ?",
                        (label,)).fetchone()
        return int(row[0]) if row else -1
    finally:
        if own:
            c.close()


def list_away_watches(conn: Optional[sqlite3.Connection] = None
                      ) -> List[Dict[str, Any]]:
    """Every Away-Watch entry, MOST-RECENT FIRST (started_ts desc, id desc as
    tie-break). `artifacts` is deserialized back to a [{title, path}] list.

    Each row also carries the DERIVED `idle_seconds` ([SPEC:CXC-IDLE-TRACKING]):
    `resumed_ts - build_complete_ts` when both are real and resumed >= complete,
    else None — the single-source-of-truth idle span (never a stored column,
    mirrors `elapsed`).

    `sessions` ([CXC-GV-SCOPE]) comes back as a real LIST of verbatim session
    tokens — `[]` for any row that never observed one (every row recorded before
    this column existed, and any run whose observation window was missed). `[]`
    is the HONEST "this run cannot name its sessions" answer; the consumer
    (`away_watch_publisher.watch_sessions`) treats a non-empty list as the scope
    and degrades LOUDLY otherwise. Nothing here ever back-fills a guess."""
    own = conn is None
    c = conn or connect()
    try:
        cur = c.execute(
            """SELECT id, session_id, label, started_ts, ended_ts, commits,
                      subagents, tests, artifacts, scene_path, recorded_at,
                      decisions, tokens, tasks, git_state,
                      build_complete_ts, resumed_ts, sessions,
                      decision_baseline_ts, run_slug
                 FROM away_watch
                ORDER BY (started_ts IS NULL), started_ts DESC, id DESC""")
        cols = [d[0] for d in cur.description]
        out: List[Dict[str, Any]] = []
        for r in cur.fetchall():
            row = dict(zip(cols, r))
            for jkey in ("artifacts", "tasks", "sessions"):
                try:
                    row[jkey] = json.loads(row.get(jkey) or "[]")
                except (ValueError, TypeError):
                    row[jkey] = []
            # a legacy/garbled `sessions` payload reads as NO observation ([]),
            # never as a half-parsed scope the viewer would trust.
            row["sessions"] = _normalize_sessions(row.get("sessions")) \
                if isinstance(row.get("sessions"), (list, tuple)) else []
            # elapsed DERIVED from start/end (one source of truth, never a stored
            # duplicate) — seconds, only when both timestamps are real.
            st, en = row.get("started_ts"), row.get("ended_ts")
            row["elapsed"] = (float(en) - float(st)) if (
                isinstance(st, (int, float)) and isinstance(en, (int, float))
                and en >= st) else None
            # idle_seconds DERIVED from the two idle boundaries (single source of
            # truth, never stored) — NULL until BOTH ends exist and resumed >= complete.
            bc, rs = row.get("build_complete_ts"), row.get("resumed_ts")
            row["idle_seconds"] = (float(rs) - float(bc)) if (
                isinstance(bc, (int, float)) and isinstance(rs, (int, float))
                and rs >= bc) else None
            out.append(row)
        return out
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------------
# run->session SCOPE ([CXC-GV-SCOPE], David 2026-08-06) — the two ends of the
# OBSERVATION window, and the append-only token ledger it feeds.
#
# THE RULE THIS SECTION EXISTS TO ENFORCE: a watch run may record ONLY session
# tokens it genuinely OBSERVED. It may never derive a mapping from its date, its
# label, or its `session_id`. The measured reason: `away_watch.session_id` is the
# Claude-Code session uuid, which is NOT 1:1 with a run (4 of the 5 real rows
# share ONE uuid, and the 5th's id matches ZERO decisions), while the decision
# corpus records `decided-in-session` mostly as DATES. Both directions of the
# link are unreliable, so the only trustworthy key is what the run WATCHED
# happen. Nothing in this file parses `.lts` or decides what "probably" belongs
# to a run — it stores an observation the layer above made.
# ---------------------------------------------------------------------------

def snapshot_decision_baseline(label: str, node_ids: Any,
                               ts: Optional[float] = None,
                               conn: Optional[sqlite3.Connection] = None) -> int:
    """OPEN the observation window for the away_watch row `label`: record the
    decision-node id set present in the corpus RIGHT NOW, and stamp
    `decision_baseline_ts`.

    This snapshot is the whole basis of the run->session link: at close, the
    nodes that are in the corpus but NOT in this set are the ones that appeared
    DURING the run, and their `decided-in-session` values are the tokens the run
    honestly observed. Replaces any prior baseline for the label (re-opening a
    window is legitimate — e.g. the preflight row was created before the corpus
    was reachable) and returns the number of ids stored.

    Called from the away-watch PREFLIGHT (see the checklist), right after
    `record_away_watch` creates the row. Returns 0 rows stored is FINE and
    meaningful — an empty corpus is a real baseline; "no baseline" is the
    separate NULL-`decision_baseline_ts` state, which is why the stamp exists."""
    own = conn is None
    c = conn or connect()
    try:
        ids = sorted({str(n).strip() for n in (node_ids or []) if str(n or "").strip()})
        c.execute("DELETE FROM away_watch_decision_baseline WHERE label = ?", (label,))
        c.executemany(
            "INSERT OR IGNORE INTO away_watch_decision_baseline (label, node_id) "
            "VALUES (?,?)", [(label, n) for n in ids])
        c.execute("UPDATE away_watch SET decision_baseline_ts=? WHERE label=?",
                  (float(ts) if ts is not None else time.time(), label))
        c.commit()
        return len(ids)
    finally:
        if own:
            c.close()


def decision_baseline(label: str,
                      conn: Optional[sqlite3.Connection] = None
                      ) -> Optional[set]:
    """The open-of-window decision-node id set for `label`, or None when NO
    baseline was ever taken.

    None vs set() is THE load-bearing distinction: an empty SET means "the window
    was opened and the corpus held nothing", so every node found at close is
    genuinely new; None means "there is no observation window", so the run cannot
    know which nodes are its own and MUST record nothing. Collapsing the two
    would make an un-baselined run claim the entire corpus — the exact
    fabrication [CXC-GV-SCOPE] forbids."""
    own = conn is None
    c = conn or connect()
    try:
        row = c.execute("SELECT decision_baseline_ts FROM away_watch WHERE label=?",
                        (label,)).fetchone()
        if row is None or row[0] is None:
            return None
        return {r[0] for r in c.execute(
            "SELECT node_id FROM away_watch_decision_baseline WHERE label=?",
            (label,))}
    finally:
        if own:
            c.close()


def observe_watch_sessions(label: str, tokens: Any,
                           conn: Optional[sqlite3.Connection] = None
                           ) -> Optional[List[str]]:
    """APPEND observed session `tokens` to the away_watch row `label`; return the
    row's full token list afterwards, or None if no such row exists (a LOUD
    no-op — the caller reports it rather than creating a row from nothing).

    APPEND-ONLY + de-duplicating, so it is safe to call repeatedly mid-watch: a
    long run can sync its scope every time the corpus grows, and a re-sync never
    dupes or reorders. Tokens are stored VERBATIM (`_normalize_sessions`) because
    they are matched character-for-character against `decided-in-session`, whose
    real values are a MIX of uuids, dates, and date-slugs — a run that spans
    several is expected to carry several, of several shapes.

    This function stores an OBSERVATION. It has no opinion about which sessions a
    run "should" have; passing it a guess would defeat the entire design."""
    own = conn is None
    c = conn or connect()
    try:
        row = c.execute("SELECT sessions FROM away_watch WHERE label=?",
                        (label,)).fetchone()
        if row is None:
            return None
        try:
            existing = json.loads(row[0] or "[]")
        except (ValueError, TypeError):
            existing = []
        if not isinstance(existing, list):
            existing = []
        merged = _normalize_sessions(list(existing) + _normalize_sessions(tokens))
        c.execute("UPDATE away_watch SET sessions=? WHERE label=?",
                  (json.dumps(merged), label))
        c.commit()
        return merged
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------------
# idle-tracking stamps ([SPEC:CXC-IDLE-TRACKING]) — the two ends of the idle span
# ---------------------------------------------------------------------------

def mark_build_complete(label: str, ts: float,
                        conn: Optional[sqlite3.Connection] = None) -> bool:
    """Stamp `build_complete_ts` on the away_watch row `label` — the moment the
    away-build QUEUE GOES DRY and the Architect enters holding (§CXC-IT-STAMP).
    ONE explicit call by the Architect; latest stamp wins (the boundary between
    productive and idle is whatever the Architect last declared). Returns True if
    a row matched. No-op (False) if the label doesn't exist yet."""
    own = conn is None
    c = conn or connect()
    try:
        cur = c.execute("UPDATE away_watch SET build_complete_ts=? WHERE label=?",
                        (float(ts), label))
        c.commit()
        return cur.rowcount > 0
    finally:
        if own:
            c.close()


def mark_resumed(label: str, ts: float,
                 conn: Optional[sqlite3.Connection] = None) -> bool:
    """Stamp `resumed_ts` on the away_watch row `label` when DAVID RETURNS — the
    first inbound human message after the queue went dry (§CXC-IT-STAMP).

    IDEMPOTENT + guarded: only stamps when `resumed_ts` is still NULL AND
    `build_complete_ts` is already set — so (a) a second inbound message doesn't
    move the return time, and (b) a mid-build message (before build-complete)
    never counts as a return. Returns True iff this call actually stamped it."""
    own = conn is None
    c = conn or connect()
    try:
        cur = c.execute(
            "UPDATE away_watch SET resumed_ts=? "
            "WHERE label=? AND resumed_ts IS NULL AND build_complete_ts IS NOT NULL",
            (float(ts), label))
        c.commit()
        return cur.rowcount > 0
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------------
# defect-investigation store ([DEC-DEFECT-TRACKING-STORE], [SPEC:CXC-DEFECT-STORE])
# — the SQLite-PRIMARY aggregate signal: one row per defect-investigator run, and
# `defects_by_root_cause()` is the histogram that IS the process-improvement signal
# (the recurrence GRAPH is the LTS-COMPLEMENT [DEFECT-*] nodes; see the spec).
# ---------------------------------------------------------------------------

def _norm_root_cause(value: Any) -> str:
    """Coerce a root-cause label into the fixed taxonomy (`_ROOT_CAUSE_CLASSES`).
    The defect-investigator agent's `implementation-oversight` collapses to
    `oversight`; anything unrecognized -> `other` (conservative, never dropped)."""
    s = str(value or "").strip().lower()
    if s in ("implementation-oversight", "oversight"):
        return "oversight"
    return s if s in _ROOT_CAUSE_CLASSES else "other"


def _b(value: Any) -> int:
    """Normalize a truthy flag to a stored 0/1 integer."""
    return 1 if value else 0


def record_defect_investigation(conn: Optional[sqlite3.Connection] = None, *,
                                symptom: str,
                                area: str = "",
                                root_cause_class: str = "other",
                                recurrence: bool = False,
                                prior_fix_ref: str = "",
                                under_spec: bool = False,
                                fork: bool = False,
                                spec_added: str = "",
                                test_added: bool = False,
                                resolution: str = "",
                                branch: str = "",
                                defect_id: str = "",
                                ts: Optional[float] = None) -> int:
    """Record ONE defect investigation (append — each investigation is a distinct
    event, like `token_spikes`, so this INSERTs and returns the new row id).

    Fields mirror the defect-investigator agent's final-text deliverable: the
    short `symptom`, the `area`/file, the `root_cause_class` (normalized to the
    taxonomy), the recurrence flag + `prior_fix_ref`, the `under_spec` / `fork`
    structural flags, the `spec_added` reference + `test_added` flag (did the fix
    close the CLASS, not just the instance), the `resolution`, and the `branch`
    the proposal lives on. Opens/owns a connection when `conn` is None."""
    own = conn is None
    c = conn or connect()
    try:
        cur = c.execute(
            """INSERT INTO defect_investigations
                 (ts, symptom, area, root_cause_class, recurrence, prior_fix_ref,
                  under_spec, fork, spec_added, test_added, resolution, branch,
                  defect_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (ts if ts is not None else time.time(), str(symptom),
             str(area or ""), _norm_root_cause(root_cause_class),
             _b(recurrence), str(prior_fix_ref or ""), _b(under_spec), _b(fork),
             str(spec_added or ""), _b(test_added), str(resolution or ""),
             str(branch or ""), str(defect_id or "")))
        c.commit()
        return int(cur.lastrowid)
    finally:
        if own:
            c.close()


def list_defect_investigations(conn: Optional[sqlite3.Connection] = None,
                               since: Optional[float] = None
                               ) -> List[Dict[str, Any]]:
    """Every defect investigation, MOST-RECENT FIRST (ts desc, id desc). `since`
    (epoch seconds) filters to `ts >= since` when given. Bool columns come back
    as their stored 0/1 integers (raw tally; no interpretation here)."""
    own = conn is None
    c = conn or connect()
    try:
        if since is not None:
            cur = c.execute(
                "SELECT * FROM defect_investigations WHERE ts >= ? "
                "ORDER BY ts DESC, id DESC", (float(since),))
        else:
            cur = c.execute(
                "SELECT * FROM defect_investigations ORDER BY ts DESC, id DESC")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        if own:
            c.close()


def defects_by_root_cause(since: Optional[float] = None,
                          conn: Optional[sqlite3.Connection] = None
                          ) -> Dict[str, int]:
    """The by-root-cause HISTOGRAM — {root_cause_class: count} — over all defect
    investigations (or those with `ts >= since`). THIS ordered dict (count desc,
    then class name) IS the process-improvement signal: a tall `under-spec` bar
    says "spec-first harder"; a tall `fork-divergence` bar says "de-fork".
    Only classes actually present appear (an empty store -> {})."""
    own = conn is None
    c = conn or connect()
    try:
        if since is not None:
            cur = c.execute(
                "SELECT root_cause_class, COUNT(*) FROM defect_investigations "
                "WHERE ts >= ? GROUP BY root_cause_class", (float(since),))
        else:
            cur = c.execute(
                "SELECT root_cause_class, COUNT(*) FROM defect_investigations "
                "GROUP BY root_cause_class")
        rows = cur.fetchall()
        rows.sort(key=lambda kv: (-int(kv[1]), str(kv[0] or "")))
        return {str(k): int(n) for k, n in rows}
    finally:
        if own:
            c.close()


# the seeded watch's real task checklist (drawn from the overnight build log
# BUILD-LOG-2026-07-22-overnight.md) — mixed statuses so the window's three
# visual states are all exercised on the real entry.
_SEED_TASKS = [
    {"label": "Config tab — remaining sections", "status": "done"},
    {"label": "Projects tab (group-by-type)", "status": "done"},
    {"label": "Memory gauge on Intelligence card", "status": "done"},
    {"label": "Retrospective perceive-half + dogfood", "status": "done"},
    {"label": "DRADIS gap analysis", "status": "done"},
    {"label": "Decision-ops CLI core", "status": "done"},
    {"label": "Drop files onto the prompt", "status": "deferred"},
    {"label": "Agent-registry reload", "status": "partial"},
]


def seed_default_away_watch(conn: Optional[sqlite3.Connection] = None) -> int:
    """Seed the ONE real overnight entry for 2026-07-22 (idempotent via the
    label upsert). The real overnight numbers + the two artifacts David named +
    (2026-07-22) the decisions/tokens metrics, the task checklist, and the LIVE
    git-tree summary the redesigned Night Watch window surfaces.

    Timestamps: start 2026-07-21 23:50 local, end 2026-07-22 07:42 local — a
    stable, readable morning close (7h 52m) rather than `now()`, so the window a
    row re-opens shows a meaningful elapsed instead of ~0. git_state is computed
    LIVE at seed time (real working tree)."""
    started = time.mktime((2026, 7, 21, 23, 50, 0, 0, 0, -1))
    ended = time.mktime((2026, 7, 22, 7, 42, 0, 0, 0, -1))
    return record_away_watch(
        conn,
        label="Overnight 2026-07-22",
        session_id=os.environ.get("CLAUDE_SESSION_ID") or "overnight-2026-07-22",
        started_ts=started,
        ended_ts=ended,
        commits=52, subagents=14, tests=1334,
        decisions=12, tokens=23_700_000,
        tasks=_SEED_TASKS,
        git_state=git_state_summary(),
        artifacts=[
            {"title": "Build log",
             "path": "docs/intelligence-console/BUILD-LOG-2026-07-22-overnight.md"},
            {"title": "DRADIS gap analysis",
             "path": "docs/composer-dradis-gap-analysis.md"},
        ],
        scene_path="compose_projects/intelligence/console/nightwatch.compose.yaml")


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if "--seed" in argv:
        rid = seed_default_away_watch()
        print(f"seeded away_watch id={rid} into {db_path()}")
        return 0
    if "--schema" in argv:
        print(SCHEMA.strip())
        return 0
    if "--path" in argv:
        print(db_path())
        return 0
    conn = connect()
    conn.close()
    print(f"metrics DB ready at {db_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
