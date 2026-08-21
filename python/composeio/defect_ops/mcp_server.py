"""composeio.defect_ops.mcp_server — a thin stdio MCP server over the deterministic
report/query/attempt/resolve CORE (`core.py`), with an lts-engine **refresh on write**
and the graph<->SQLite cross-link write.

    python3 -m composeio.defect_ops.mcp_server            # speak MCP over stdio
    python3 -m composeio.defect_ops.mcp_server --selftest

HOUSE PATTERN. Byte-for-byte the decision_ops.mcp_server shape (which itself
mirrors `factlattice_engine/server.py`): newline-delimited JSON-RPC 2.0 over stdio,
STDLIB ONLY on the transport, and a transport-agnostic
`DefectOpsMcp.dispatch(name, arguments) -> dict` seam the stdio shell wraps (so a
future ComposeBus request/announcement handler swaps in — the credo: an
Intelligence drives this identically to a human).

THE FOUR TOOLS:
  * defect_query   — history by symptom/id (THE ANTI-RE-DIG GATE: prior attempts +
                     hypotheses + why-they-failed + related kin). Read-only.
  * defect_report  — register a NEW defect OR append a report to an existing one
                     (dedup by stable symptom identity; bump report-count + dates).
  * defect_attempt — append a fix attempt (supersedes the prior; a recurrence
                     reopens the defect) AND write the SQLite analytics row.
  * defect_resolve — verified-fixed OR reopened.

THE WRITE PATH (report / attempt / resolve):
  1. call the core op (line-level splice + re-verify via the reused decision_ops
     guard, with content-keyed dangle/collision rollback -> DangleRefusedError);
  2. IFF the core reports changed=True, push an lts-engine refresh (reused
     decision_ops.lts_refresh.LtsRefresher, area="defects") so the new node is
     immediately queryable with no stale window;
  3. on attempt/resolve ALSO write the retrospective SQLite `defect_investigations`
     row (the graph/SQLite seam: the graph holds the narrative + recurrence edges,
     SQLite holds the counts/aggregation histogram). Best-effort — a SQLite write
     failure NEVER fails the graph write (which already committed).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Seat the engine path (reused decision_ops seat) BEFORE importing the core.
from ..decision_ops._enginepath import ensure_engine_on_path

ensure_engine_on_path()

from ..decision_ops.graph_home import canonical_repo_root, graph_receipt
from ..decision_ops.lts_refresh import LtsRefresher  # reuse the refresh machinery
from ..decision_ops.graph_home import WorktreeGraphRefused
from .core import (
    DangleRefusedError,
    DefectOpsError,
    NodeNotFoundError,
    attempt,
    query,
    report,
    resolve,
)
from .profile import default_profile_path, symptom_slug

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "composeio-defect-ops", "version": "0.1.0"}

_DEFAULT_GRAPH_REL = os.path.join("defects", "defect-graph.lts")


def _repo_root() -> str:
    """The CANONICAL repo root — the MAIN checkout, even when this very module is
    a linked worktree's copy of itself.

    THE DEFECT THIS REPLACES (2026-08-08, twice in one session):

        _REPO_ROOT = str(Path(__file__).resolve().parents[3])      # WRONG

    derived the registry's home from WHERE THIS CODE LIVES. Inside a linked
    worktree that is the worktree's copy of this file, so the "repo" defect graph
    resolved to the WORKTREE's copy — and two defects filed by dispatched subagents
    (DEFECT-UART-FRAME-MESSAGE-TYPE-DOES-D62D7F,
    DEFECT-DOMAIN-RELATIONSHIP-VERBS-DISCARDED-SILENTLY-D542D5) landed on a graph
    that was live nowhere. Both agents honestly reported "filed".

    `git rev-parse --git-common-dir` un-does the worktree from the inside (see
    `decision_ops.graph_home`). parents[3] survives ONLY as the last resort for a
    checkout that is not a git repo at all (a tarball / site-packages install),
    where there is no worktree to be confused by."""
    root = canonical_repo_root(__file__)
    if root is not None:
        return str(root)
    return str(Path(__file__).resolve().parents[3])


# Resolved ONCE at import: the canonical root cannot change under a running
# process, and this also keeps `sys.path` seeding (below) pointed at the canonical
# `intelligence.retrospective` so the SQLite analytics row lands beside the graph.
_REPO_ROOT = _repo_root()


def _default_graph() -> Optional[str]:
    g = os.environ.get("DEFECT_OPS_GRAPH")
    if g:
        return g
    # ⛔ THE DEFECT GRAPH LEFT THE REPO 2026-08-17, with the rest of the corpora
    #    (41 worktrees were each carrying a fork). This default resolved
    #    `<repo>/defects/defect-graph.lts`, and when that stopped existing the
    #    server did NOT say "the graph moved" — it said "no graph given (pass
    #    'graph' or set $DEFECT_OPS_GRAPH)", which reads as a CONFIGURATION
    #    omission and sent the reader looking in the wrong place. Resolve through
    #    the one seam instead, so a future relocation is a one-line edit there.
    try:
        from ..bootstrap_paths import DEFECT_GRAPH
        if DEFECT_GRAPH.is_file():
            return str(DEFECT_GRAPH)
    except Exception:  # noqa: BLE001 — never let path resolution crash the server
        pass
    cand = os.path.join(_REPO_ROOT, _DEFAULT_GRAPH_REL)   # legacy in-repo layout
    return cand if os.path.isfile(cand) else None


# --- SQLite analytics seam (reuse-by-reference; best-effort) ------------------
def _record_investigation(**kwargs) -> Optional[int]:
    """Write ONE `defect_investigations` row via the EXISTING retrospective API
    (reuse, never rebuild — [SPEC:CXC-DEFECT-STORE]). Best-effort: a failure
    (module absent, DB unwritable) returns None and never propagates, because the
    graph write it accompanies already committed."""
    try:
        if _REPO_ROOT not in sys.path:
            sys.path.insert(0, _REPO_ROOT)
        # `intelligence/` moved to compose_projects/ (f391600); the parent of
        # `intelligence/` must be on sys.path for `import intelligence.*` to resolve
        # as a standalone entry point — the same seam compose_projects/conftest.py and
        # away_watch_publisher.py apply. (Rename of the 117 sites is David's deferred call.)
        _projects = os.path.join(_REPO_ROOT, "compose_projects")
        if _projects not in sys.path:
            sys.path.insert(0, _projects)
        from intelligence.retrospective import db as _db
        return _db.record_defect_investigation(**kwargs)
    except Exception as exc:  # noqa: BLE001 — analytics is best-effort
        sys.stderr.write(f"[defect_ops] SQLite analytics row skipped: {exc!r}\n")
        return None


# --- MCP tool schemas ---------------------------------------------------------
_STR = {"type": "string"}
_BOOL = {"type": "boolean"}
_GRAPH = {**_STR, "description": "path to the defect-graph .lts. OMIT IT unless you "
                                 "mean a scratch graph — the default resolves to the "
                                 "CANONICAL checkout's defects/defect-graph.lts "
                                 "($DEFECT_OPS_GRAPH wins) and is correct from inside "
                                 "any git worktree. A path inside a linked worktree is "
                                 "REFUSED: a record written there is live on no graph."}
TOOLS = [
    {
        "name": "defect_query",
        "description": ("THE ANTI-RE-DIG GATE — run this FIRST on any defect. Given a "
                        "SYMPTOM or a DEFECT id, return the accumulated history: the defect "
                        "+ every prior fix attempt + each attempt's root-cause hypothesis + "
                        "WHY IT DIDN'T HOLD + related kin. Read-only; no refresh."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "symptom": {**_STR, "description": "the symptom text OR a DEFECT-... id"}},
            "required": ["symptom"]},
    },
    {
        "name": "defect_report",
        "description": ("Register a NEW defect OR append a report to an existing one "
                        "(dedup by STABLE symptom identity — query-first). Bumps report-count "
                        "+ dates; a report on a verified defect is a recurrence. Refreshes the "
                        "lts-engine on a real write."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "symptom": {**_STR, "description": "the symptom (stable identity key)"},
            "area": _STR,
            "note": _STR,
            "related_to": {**_STR, "description": "an EXISTING DEFECT id to link as kin "
                           "(must resolve, else the write is refused)"}},
            "required": ["symptom"]},
    },
    {
        "name": "defect_attempt",
        "description": ("Append a fix ATTEMPT (supersedes the prior; a recurred outcome "
                        "reopens the defect) AND write the SQLite analytics row. Creates the "
                        "defect if this is the first filing for the symptom. Refreshes the "
                        "lts-engine on a real write."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "symptom": {**_STR, "description": "the symptom text OR a DEFECT-... id"},
            "root_cause_hypothesis": _STR, "what_changed": _STR,
            "outcome": {**_STR, "description": "held | recurred"},
            "branch": _STR, "commit": _STR, "why_it_didnt_hold": _STR, "area": _STR,
            # analytics-row fields (the defect-investigator deliverable -> SQLite)
            "root_cause_class": {**_STR, "description": "under-spec|oversight|"
                                 "fork-divergence|spec-drift|other"},
            "prior_fix_ref": _STR, "under_spec": _BOOL, "fork": _BOOL,
            "spec_added": _STR, "test_added": _BOOL},
            "required": ["symptom"]},
    },
    {
        "name": "defect_resolve",
        "description": ("Close (status=verified) or reopen (status=reopened) a defect by "
                        "ANNOTATION (never deletes). Writes a SQLite analytics row + refreshes "
                        "the lts-engine on a real write."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "symptom": {**_STR, "description": "the symptom text OR a DEFECT-... id"},
            "status": {**_STR, "description": "verified | reopened (default verified)"},
            "note": _STR,
            "root_cause_class": _STR, "test_added": _BOOL},
            "required": ["symptom"]},
    },
]


class DefectOpsMcp:
    """Transport-agnostic op layer: `dispatch(name, arguments) -> dict`. ZERO
    MCP/stdio knowledge. Owns the one `LtsRefresher` for this process (reused
    verbatim from decision_ops)."""

    def __init__(self, *, default_graph: Optional[str] = None,
                 profile: Optional[str] = None,
                 refresher: Optional[LtsRefresher] = None,
                 record_investigation=_record_investigation) -> None:
        self.default_graph = default_graph if default_graph is not None else _default_graph()
        self.profile = profile if profile is not None else default_profile_path()
        self.refresher = refresher if refresher is not None else LtsRefresher()
        self._record = record_investigation      # injectable for tests

    def _graph(self, a: dict) -> str:
        g = a.get("graph") or self.default_graph
        if not g:
            raise ValueError("no graph given (pass 'graph' or set $DEFECT_OPS_GRAPH)")
        return g

    def _refresh_after_write(self, graph: str, res) -> Optional[dict]:
        if not getattr(res, "changed", False):
            return None
        return self.refresher.refresh(graph, area="defects").to_dict()

    # --- the tools ------------------------------------------------------------
    def _query(self, a: dict) -> dict:
        graph = self._graph(a)
        result = query(graph, a["symptom"], profile=self.profile)
        # The receipt rides the READ too: "I queried the registry and found nothing"
        # is only trustworthy if the reader can see WHICH file was read.
        return {"success": True, **result, **graph_receipt(graph)}

    def _report(self, a: dict) -> dict:
        graph = self._graph(a)
        res = report(graph, symptom=a["symptom"], area=a.get("area", ""),
                     note=a.get("note", ""), related_to=a.get("related_to"),
                     profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        return {"success": True, **out}

    def _attempt(self, a: dict) -> dict:
        graph = self._graph(a)
        outcome = str(a.get("outcome", "held")).strip().lower()
        res = attempt(graph, symptom=a["symptom"],
                      root_cause_hypothesis=a.get("root_cause_hypothesis", ""),
                      what_changed=a.get("what_changed", ""),
                      outcome=outcome, branch=a.get("branch", ""),
                      commit=a.get("commit", ""),
                      why_it_didnt_hold=a.get("why_it_didnt_hold", ""),
                      area=a.get("area", ""), profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        # graph/SQLite seam: EVERY attempt writes an analytics row, cross-linked
        # to the graph defect id ([SPEC:CIO-DEFECT-REGISTRY] [DR-SEAM]/[DR-SQLITE]).
        # This is composed HERE, in the ONE dispatch the CLI + stdio + any future
        # bus handler all reuse by reference ([DR-CLI]) — so no entry point can
        # compose a partial op that drops the SQLite row.
        if res.changed:
            # class fallback: a CLI investigator passes the taxonomy as the
            # `--root-cause` (root_cause_hypothesis) per the agent def; use it as
            # the analytics class when no explicit `root_cause_class` was given, so
            # the histogram + structural gauge populate from the SAME call.
            rc_class = (a.get("root_cause_class")
                        or a.get("root_cause_hypothesis") or "other")
            norm = str(rc_class).strip().lower()
            out["analytics_row_id"] = self._record(
                symptom=a["symptom"], area=a.get("area", ""),
                root_cause_class=rc_class,
                recurrence=(outcome == "recurred"),
                prior_fix_ref=a.get("prior_fix_ref", ""),
                # structural flags derive from the class when not explicitly set,
                # so the under-spec/fork gauge is fed even by a minimal CLI call.
                under_spec=bool(a.get("under_spec", False)) or norm == "under-spec",
                fork=bool(a.get("fork", False)) or norm == "fork-divergence",
                spec_added=a.get("spec_added", ""),
                test_added=bool(a.get("test_added", False)),
                resolution=a.get("what_changed", ""),
                branch=a.get("branch", ""),
                defect_id=res.defect_id)
        return {"success": True, **out}

    def _resolve(self, a: dict) -> dict:
        graph = self._graph(a)
        status = str(a.get("status", "verified")).strip().lower()
        res = resolve(graph, symptom=a["symptom"], status=status,
                      note=a.get("note", ""), profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        if res.changed:
            out["analytics_row_id"] = self._record(
                symptom=a["symptom"],
                root_cause_class=a.get("root_cause_class", "other"),
                recurrence=(status == "reopened"),
                test_added=bool(a.get("test_added", False)),
                resolution=a.get("note", "") or status,
                defect_id=res.defect_id)
        return {"success": True, **out}

    def dispatch(self, name: str, arguments: dict) -> dict:
        a = arguments or {}
        handlers = {
            "defect_query": self._query, "defect_report": self._report,
            "defect_attempt": self._attempt, "defect_resolve": self._resolve,
        }
        h = handlers.get(name)
        if h is None:
            return {"success": False, "error": f"unknown defect-ops tool: {name}"}
        try:
            return h(a)
        except WorktreeGraphRefused as exc:
            # Structural refusal, surfaced as DATA (not just prose) so a caller can
            # retry against `canonical_path` without re-parsing the message.
            return {"success": False, "error": str(exc), "refused": True,
                    "refused_worktree_graph": True,
                    "given_path": exc.given_path,
                    "canonical_path": exc.canonical_path,
                    "engine_refresh": None}
        except DangleRefusedError as exc:
            return {"success": False, "error": str(exc), "refused": True,
                    "problems": exc.problems, "engine_refresh": None}
        except (NodeNotFoundError, DefectOpsError) as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}",
                    "engine_refresh": None}
        except KeyError as exc:
            return {"success": False, "error": f"missing required argument: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# stdio MCP shell (mirrors decision_ops.mcp_server / factlattice_engine.server)
# --------------------------------------------------------------------------- #
class Server:
    def __init__(self, ops: DefectOpsMcp):
        self.ops = ops

    def handle(self, req: dict) -> Optional[dict]:
        method = req.get("method")
        rid = req.get("id")
        if method and method.startswith("notifications/"):
            return None
        if method == "initialize":
            return self._ok(rid, {"protocolVersion": PROTOCOL_VERSION,
                                  "capabilities": {"tools": {}},
                                  "serverInfo": SERVER_INFO})
        if method == "tools/list":
            return self._ok(rid, {"tools": TOOLS})
        if method == "tools/call":
            params = req.get("params") or {}
            result = self.ops.dispatch(params.get("name"), params.get("arguments") or {})
            return self._ok(rid, {"content": [
                {"type": "text", "text": json.dumps(result, indent=2)}],
                "isError": not result.get("success", False)})
        if method == "ping":
            return self._ok(rid, {})
        return self._err(rid, -32601, f"method not found: {method}")

    @staticmethod
    def _ok(rid: Any, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @staticmethod
    def _err(rid: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(DefectOpsMcp())
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = server.handle(req)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--selftest" in argv:
        from .selftest import run
        return run()
    serve()
    return 0


if __name__ == "__main__":
    sys.exit(main())
