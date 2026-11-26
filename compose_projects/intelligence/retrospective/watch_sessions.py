#!/usr/bin/env python3
"""watch_sessions — the OBSERVER that gives an Away-Watch run an honest
run->session(s) key ([CXC-GV-SCOPE], David 2026-08-06).

WHY THIS EXISTS (the measured gap, verified 2026-08-06 against the real
`metrics.db` + `corpora/intelligence-console/`):

  * `away_watch.session_id` is the Claude-Code SESSION uuid. Four of the five
    real watch rows carry the SAME uuid (`59f1b46e-3cde-4c0f-8021-3b3e7bbe00f3`)
    because the session was resumed across nights — scoping by it merges four
    runs into one view.
  * The fifth row's id (`overnight-2026-07-22`) matches ZERO decision nodes.
  * The decision corpus records `decided-in-session` mostly as DATES:
    2026-07-20 x30, 2026-07-22 x25, 2026-07-24 x21 ...; the uuid accounts for
    only 12 of 167 tokened nodes.

So the link is unreliable in BOTH directions, and no amount of cleverness over
the stored fields can repair it — the information was never captured. The fix is
therefore not a smarter derivation but a NEW OBSERVATION: watch the corpus across
the run window and record, verbatim, the tokens carried by the decision nodes
that appeared inside it.

THE MECHANISM — two stamps, one diff:

  OPEN  (away-watch preflight, right after `record_away_watch` creates the row)
        `open_watch(label)` snapshots the decision-node id set present in the
        corpus and stamps `decision_baseline_ts`. That snapshot IS the window's
        left edge.
  CLOSE (post-watch, and as often as you like mid-watch)
        `close_watch(label)` re-scans, diffs against the baseline, and APPENDS
        the `decided-in-session` values of the nodes that are NEW to the row's
        `sessions` list. Append-only + de-duplicating, so mid-watch syncs are
        safe and idempotent.

WHAT THIS DELIBERATELY DOES NOT DO — the honesty gate:

  * It NEVER derives a session from a run's date, label, or `session_id`. A
    plausible-but-wrong scope is worse than no scope, because the human reads a
    filtered view as authoritative.
  * With NO baseline (`decision_baseline_ts` NULL) it records NOTHING and says
    so. It does not fall back to "diff against the empty set", which would make
    every node in the corpus look new and let the run claim the whole graph.
  * It never normalizes a token. `decided-in-session` is genuinely heterogeneous
    (uuids, `2026-07-22`, `2026-08-05-cic-composite-esp32-fan-continuation`) and
    the viewer matches character-for-character, so tidying a token breaks it.
  * If the corpus cannot be read (factlattice/engine absent), it records NOTHING
    and reports `corpus-unavailable` — an unreadable corpus is not an empty one.

HONEST LIMITS, stated so no one over-reads the scope:

  1. OBSERVED-IN-WINDOW is not AUTHORED-BY-THIS-RUN. If another instance writes a
     decision while the watch is open, its token is observed too. This is a
     scoping aid, not an attribution of authorship — and it is still strictly an
     observation, never a guess.
  2. The token space is COARSER than a run. When the corpus stamps a bare date,
     every run that night shares that token, so the viewer's scope is
     day-grained for those nodes. That is the corpus's granularity showing
     through, not this module inventing anything. The route to run-grained scope
     is a run-grained token at decision-WRITE time (see DAVID'S GLASS, below).
  3. RETRO-FIT: the five existing rows cannot be repaired. Their observation
     windows are gone, and any backfill would be exactly the guess this design
     forbids. They stay UNSCOPED and the viewer degrades loudly. That is correct.

Run:
    python3 -m intelligence.retrospective.watch_sessions --open  "<label>"
    python3 -m intelligence.retrospective.watch_sessions --close "<label>"
    python3 -m intelligence.retrospective.watch_sessions --show  "<label>"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
# _HERE is <repo>/compose_projects/intelligence/retrospective — THREE up to the repo
# root, not two (2026-08-17 reorg). The `intelligence.*` package root moved WITH it:
# the dir that must be importable is now <repo>/compose_projects, not <repo>.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _p in (os.path.join(REPO_ROOT, "python"),
           os.path.join(REPO_ROOT, "compose_projects"), REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from intelligence.retrospective import db as _db   # noqa: E402

# The corpus the Graph Viewer serves (`decisions_publisher.DEFAULT_CORPUS`) —
# the SAME node universe the viewer filters, so a token observed here is a token
# the viewer can match. Overridable for tests / a second corpus.
DEFAULT_CORPUS = "intelligence-console"
CORPUS_ENV = "COMPOSEIO_DECISION_CORPUS_DIR"

# the property every decision node carries the session token in — the join key
SESSION_PROPERTY = "decided-in-session"

# The environment variable a run's slug is exported as — the AMBIENT SESSION
# TOKEN `composeio.decision_ops.core.ambient_session()` reads. Named here as a
# plain string, deliberately NOT imported from decision_ops: the coupling runs
# one way only (an orchestrator sets an ambient value; the generic decision tool
# reads it), and this module must not drag the LTS writer into the metrics store.
RUN_SLUG_ENV = "COMPOSEIO_DECISION_SESSION"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_EMBEDDED_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def run_slug_for(label: str, started_ts: Optional[float] = None) -> str:
    """The RUN-GRAINED session slug for a watch labelled *label* — David's
    2026-08-06 convention, e.g. `2026-08-06-away-watch-scope`.

    Shape: `<YYYY-MM-DD>-<slugified label>`, date ALWAYS first (the convention's
    readable form, and it sorts). A date already inside the label is moved to the
    front rather than duplicated, so "Overnight 2026-07-22" yields
    `2026-07-22-overnight`, not `2026-07-22-overnight-2026-07-22`.

    UNIQUENESS comes from the data, not from luck: `away_watch.label` is UNIQUE,
    so one label is one run, so one slug is one run. That is what makes the scope
    run-grained instead of day-grained — two runs on one night get two slugs,
    where today they would both stamp the same bare date.

    This slug is a DECLARATION of identity, not a claim about any decision. It
    becomes a run's scope only where it is actually OBSERVED on a node."""
    body = _SLUG_STRIP.sub("-", str(label or "").strip().lower()).strip("-")
    if not body:
        raise ValueError("run_slug_for() needs a non-empty label")
    embedded = _EMBEDDED_DATE.search(body)
    if embedded:
        day = embedded.group(0)
        body = _SLUG_STRIP.sub("-", body.replace(day, " ")).strip("-")
    else:
        day = time.strftime("%Y-%m-%d", time.localtime(
            started_ts if isinstance(started_ts, (int, float)) and started_ts > 0
            else time.time()))
    return f"{day}-{body}" if body else day


def corpus_dir(corpus: str = DEFAULT_CORPUS) -> str:
    """The decision-corpus directory scanned for nodes: the `COMPOSEIO_DECISION_
    CORPUS_DIR` override, else `bootstrap_paths.CORPORA_DIR / <corpus>` (the dir
    `decisions_publisher._build_engine` serves the viewer from).

    ⛔ 2026-08-17: this was a FOURTH fork of the `<repo>/corpora/<id>` layout literal —
    the same fork `decisions_publisher._build_engine` names in its own comment. The
    corpora moved OUT of the repo, so a re-derived `<repo>/corpora/...` now points at
    nothing and this scan would report an EMPTY corpus against a healthy engine.
    `bootstrap_paths.CORPORA_DIR` is the one place the layout is known."""
    override = os.environ.get(CORPUS_ENV, "").strip()
    if override:
        return override
    from composeio.bootstrap_paths import CORPORA_DIR   # noqa: PLC0415 (see sys.path above)
    return str(CORPORA_DIR / corpus)


class CorpusUnavailable(RuntimeError):
    """The decision corpus could not be READ (parser/engine absent, path gone).
    Raised — never swallowed into an empty scan — because "unreadable" and
    "empty" must not produce the same observation."""


def scan_decision_tokens(path: Optional[str] = None) -> Dict[str, Optional[str]]:
    """`{node_id: decided-in-session}` for every node in the decision corpus,
    tokens VERBATIM (None for a node that carries no token).

    REUSE, do not reimplement ([DEC-ONE-FACTLATTICE-PARSER]): the scan is
    `composeio.decision_ops.core.query()` over the corpus dir, which is itself
    `factlattice.parse()`. This module owns no `.lts` grammar.

    Raises CorpusUnavailable if the corpus cannot be parsed at all."""
    target = path or corpus_dir()
    try:
        from composeio.decision_ops import core as _core
        rows = _core.query(target)
    except Exception as exc:                       # import OR parse failure
        raise CorpusUnavailable(f"{target}: {exc!r}") from exc
    out: Dict[str, Optional[str]] = {}
    for r in rows:
        nid = str(r.get("id") or "").strip()
        if not nid:
            continue
        tok = r.get("decided_in_session")
        tok = str(tok).strip() if tok is not None and str(tok).strip() else None
        out[nid] = tok
    return out


def open_watch(label: str, path: Optional[str] = None,
               ts: Optional[float] = None,
               run_slug: Optional[str] = None) -> Dict[str, Any]:
    """OPEN the observation window for the away_watch row `label` (preflight) and
    establish the run's DECLARED identity.

    Two things happen here because they are the same event — "the run starts":
      1. the corpus's current decision-node ids are snapshotted as the baseline
         (the left edge of the observation window), and
      2. the run's SLUG is derived and stored (`run_slug_for`, or an explicit
         `run_slug`), so the caller can export it as the ambient session token.

    The row must already exist (`record_away_watch` first) — this never creates
    one. The slug is stored as a DECLARATION only: it is never written into
    `sessions`, because at this moment not a single decision carries it, and a
    scope must name tokens that were OBSERVED, not tokens that were intended.

    Returns `{status, label, baseline, run_slug, corpus_dir}` where status is
      `opened`            — baseline + slug stored; the run is scopeable
      `no-row`            — no away_watch row with that label (nothing done)
      `corpus-unavailable`— corpus unreadable; NO baseline stamped, so a later
                            close will correctly refuse rather than over-claim."""
    report: Dict[str, Any] = {"label": label, "corpus_dir": path or corpus_dir(),
                              "baseline": None, "run_slug": None}
    conn = _db.connect()
    try:
        row = conn.execute("SELECT started_ts FROM away_watch WHERE label=?",
                           (label,)).fetchone()
        if row is None:
            report["status"] = "no-row"
            report["message"] = (f"no away_watch row labelled {label!r} — record "
                                 f"the watch first, then open its window")
            return report
        try:
            tokens = scan_decision_tokens(path)
        except CorpusUnavailable as exc:
            report["status"] = "corpus-unavailable"
            report["message"] = (f"decision corpus unreadable ({exc}); NO baseline "
                                 f"stamped — this run will stay UNSCOPED rather "
                                 f"than claim decisions it never watched")
            return report
        n = _db.snapshot_decision_baseline(label, tokens.keys(), ts=ts, conn=conn)
        slug = (str(run_slug).strip() if run_slug
                else run_slug_for(label, row[0]))
        conn.execute("UPDATE away_watch SET run_slug=? WHERE label=?",
                     (slug, label))
        conn.commit()
        report["status"] = "opened"
        report["baseline"] = n
        report["run_slug"] = slug
        report["message"] = (f"observation window OPEN for {label!r}: {n} decision "
                             f"node(s) already present are excluded from this run; "
                             f"run slug {slug!r} (export {RUN_SLUG_ENV}={slug})")
        return report
    finally:
        conn.close()


def close_watch(label: str, path: Optional[str] = None) -> Dict[str, Any]:
    """CLOSE (or mid-watch SYNC) the observation window for `label`: diff the
    corpus against the run's baseline and APPEND the verbatim session tokens of
    the nodes that appeared inside the window.

    Idempotent and repeatable — the baseline is not consumed, and
    `observe_watch_sessions` de-dupes, so calling this hourly during a long watch
    keeps the scope current without duplicating or reordering tokens.

    Returns a report: `{status, label, new_nodes, tokens, sessions, ...}`:
      `recorded`          — tokens observed and appended (`sessions` = the row's
                            full list afterwards)
      `no-new-decisions`  — the window is real but nothing appeared in it; NOTHING
                            recorded (an honest empty is an empty)
      `untokened-only`    — new nodes appeared but none carries a
                            `decided-in-session`; NOTHING recorded
      `no-baseline`       — the window was never opened, so this run CANNOT know
                            which nodes are its own; NOTHING recorded and the run
                            stays UNSCOPED (the viewer degrades loudly). This is
                            the state EVERY pre-2026-08-06 row is in, permanently.
      `no-row` / `corpus-unavailable` — as in `open_watch`."""
    report: Dict[str, Any] = {"label": label, "corpus_dir": path or corpus_dir(),
                              "new_nodes": [], "tokens": [], "sessions": []}
    conn = _db.connect()
    try:
        row = conn.execute("SELECT sessions, run_slug FROM away_watch WHERE label=?",
                           (label,)).fetchone()
        if row is None:
            report["status"] = "no-row"
            report["message"] = f"no away_watch row labelled {label!r}"
            return report
        declared = row[1]
        report["run_slug"] = declared
        baseline = _db.decision_baseline(label, conn=conn)
        if baseline is None:
            report["status"] = "no-baseline"
            report["message"] = (
                f"{label!r} has NO observation window (no baseline was taken at "
                f"open), so its decisions cannot be identified. Recording nothing "
                f"— this run stays UNSCOPED and the Graph Viewer will say so. "
                f"Guessing here (e.g. 'everything decided that night') would be a "
                f"plausible-but-wrong scope.")
            return report
        try:
            current = scan_decision_tokens(path)
        except CorpusUnavailable as exc:
            report["status"] = "corpus-unavailable"
            report["message"] = (f"decision corpus unreadable ({exc}); recording "
                                 f"nothing")
            return report
        new_ids = sorted(set(current) - set(baseline))
        report["new_nodes"] = new_ids
        report["baseline"] = len(baseline)
        report["corpus_nodes"] = len(current)
        if not new_ids:
            report["status"] = "no-new-decisions"
            report["message"] = (f"{label!r}: no decision node appeared inside the "
                                 f"observation window; nothing recorded")
            return report
        tokens = [current[n] for n in new_ids if current.get(n)]
        report["tokens"] = _db._normalize_sessions(tokens)
        if not report["tokens"]:
            report["status"] = "untokened-only"
            report["message"] = (f"{label!r}: {len(new_ids)} new decision node(s) "
                                 f"but none carries a {SESSION_PROPERTY}; nothing "
                                 f"recorded")
            return report
        merged = _db.observe_watch_sessions(label, report["tokens"], conn=conn)
        report["sessions"] = merged or []
        report["status"] = "recorded"
        report["message"] = (f"{label!r}: observed {len(new_ids)} new decision "
                             f"node(s) carrying {len(report['tokens'])} distinct "
                             f"session token(s) -> {report['sessions']}")
        # DECLARED vs OBSERVED ([CXC-GV-SCOPE]). The slug only makes the scope
        # run-grained if the decisions actually CARRY it; if they came out
        # date-stamped instead, the ambient token never reached the writer and
        # the scope has quietly stayed day-grained. Say so. The scope is honest
        # either way — it is exactly what was observed — but the operator needs
        # to know the run-grained isolation did not happen.
        if declared and declared not in report["sessions"]:
            report["slug_missing"] = True
            report["message"] += (
                f"  WARNING: this run declared slug {declared!r} but NO decision "
                f"carries it — the ambient token ({RUN_SLUG_ENV}) did not reach "
                f"the decision writer, so the scope is only as fine-grained as the "
                f"tokens above.")
        return report
    finally:
        conn.close()


def show_watch(label: str) -> Dict[str, Any]:
    """The current scope state for `label` — what the Graph Viewer would get."""
    baseline = _db.decision_baseline(label)
    for w in _db.list_away_watches():
        if str(w.get("label")) == str(label):
            return {"label": label, "status": "ok",
                    "session_id": w.get("session_id"),
                    "run_slug": w.get("run_slug"),
                    "sessions": w.get("sessions") or [],
                    "baseline": None if baseline is None else len(baseline),
                    "scoped": bool(w.get("sessions")),
                    "run_grained": bool(w.get("run_slug")
                                        and w.get("run_slug") in
                                        (w.get("sessions") or []))}
    return {"label": label, "status": "no-row"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Open/close an Away-Watch run's decision OBSERVATION window "
                    "so the run can honestly name its session token(s).")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--open", metavar="LABEL",
                   help="preflight: snapshot the corpus as this run's baseline")
    g.add_argument("--close", metavar="LABEL",
                   help="post-watch (or mid-watch sync): diff + record observed "
                        "session tokens")
    g.add_argument("--show", metavar="LABEL", help="print the run's scope state")
    ap.add_argument("--corpus", default=None,
                    help="decision-corpus dir (default: corpora/"
                         f"{DEFAULT_CORPUS}/, or ${CORPUS_ENV})")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    a = ap.parse_args(argv)

    if a.open:
        rep = open_watch(a.open, path=a.corpus)
    elif a.close:
        rep = close_watch(a.close, path=a.corpus)
    else:
        rep = show_watch(a.show)
    if a.json:
        print(json.dumps(rep, indent=2))
    else:
        print(f"[{rep.get('status')}] {rep.get('message') or rep}")
    return 0 if rep.get("status") in ("opened", "recorded", "ok",
                                      "no-new-decisions", "untokened-only") else 1


if __name__ == "__main__":
    raise SystemExit(main())
