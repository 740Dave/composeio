"""composeio.decision_ops — the deterministic CORE (add / update / query / resolve)
over a project decision-graph `.lts` file, matching the sibling-tool layout
(`decision_extractor/`, `session_sweep/`, `lts_lint/`: `__init__` + `__main__` +
core-logic module + `selftest.py`).

This is the WRITE side of the graph that `decision_extractor` DRAFTS from a
session transcript; where the extractor produces a draft for the architect to
triage by hand, `decision_ops` is the library an architect (and, in a FOLLOW-UP,
an MCP server) calls to actually mutate the ledger in place, with the same
byte-format discipline the hand-authored graph already has.

    from composeio.decision_ops import add, update, query, resolve

    add(graph_path, {"id": "DEC-FOO-BAR", "title": "...",
                     "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
                     "importance": "medium", "rationale": "..."})
    update(graph_path, "DEC-FOO-BAR", {"lifecycle-position": "committed"})
    query(graph_path, "prefix:DEC")              # -> list[dict]
    resolve(graph_path, "DEC-FOO-BAR", superseded_by="DEC-NEWER", note="...")

CLI::

    python3 -m composeio.decision_ops add <graph.lts> --id DEC-FOO --title "..." \\
        --lifecycle-position raised --decided-in-session 2026-07-22 \\
        --importance medium --rationale "..." [--field key=value ...]
    python3 -m composeio.decision_ops update <graph.lts> DEC-FOO --set key=value ...
    python3 -m composeio.decision_ops query <graph.lts> "prefix:DEC"
    python3 -m composeio.decision_ops resolve <graph.lts> DEC-FOO --superseded-by DEC-NEWER
    python3 -m composeio.decision_ops --selftest

WHERE IT WRITES (`graph_home`, added 2026-08-08). `<graph.lts>` inside a LINKED GIT
WORKTREE is REFUSED, loudly, naming the canonical path — a record written to a
worktree copy is live on NO graph, which silently cost this project two defect
records. The guard sits on `_apply_with_guard`, the ONE write choke point this
package and `defect_ops` share, so the CLI (which calls the core directly), the MCP
dispatch and a bare `from composeio.decision_ops import add` are all covered by the
same gate. Escape hatch for hermetic tests: `COMPOSEIO_ALLOW_WORKTREE_GRAPH=1`.

EVERY WRITE IS SELF-PROVING: `OpResult.to_dict()` carries `graph_path` (absolute,
resolved) and `graph_is_canonical`. Quote them when reporting — a claim that costs
the reader a `grep` is not a receipt.

--------------------------------------------------------------------------------
FLAGGED — explicitly OUT of scope for this build, do NOT re-derive any of it from
this module; each is a real follow-up with its own owner/gate:

  1. MCP-SERVER WRAPPER. This package is the CORE an MCP server will call
     ([DEC-SESSION-RETROSPECTIVE] §5a decision-LTS write lifecycle,
     [FIX-DSD-IS-A-QUERY]'s literal `query_project_decision` call). No MCP
     tool/server code lives here — building the wrapper is the follow-up task
     that imports THIS library, the same "one core, N consumers" pattern
     `lts_lint` and `decision_extractor` already use.

  2. HAVEN-MAGMA SYNC. The external Haven "Magma" knowledge-graph service
     (`haven_magma_*` MCP tools) is a DIFFERENT system from the local lts-engine
     this package's MCP server refreshes on write — and it genuinely still carries
     the magma_* naming. Keeping this `.lts` file and Haven-Magma's index in sync
     needs Haven and is its own follow-up. For now, `query()`'s local `factlattice`
     parse/index is the complete answer to "does the graph already have this
     decision" — sufficient for the anti-re-ask gate today; Haven-Magma sync only
     matters once something OTHER than this repo's own tools needs to traverse the graph.

  3. LIVE-CUTOVER TO SOLE WRITER. The hand-append workflow (an architect editing
     the `.lts` directly, as every node in the real graph today was written) is
     UNCHANGED and untouched by this build. `decision_ops` is not wired as the
     only writer anywhere, and this module does not retire or discourage the
     hand-append path. Per David's D10 (eventual immediate-cutover to a single
     writer), promoting this from "a tool available" to "the only writer" is a
     load-bearing call that needs his glass-confirmation — not something a
     build task should silently do by being the more convenient path.
"""
from __future__ import annotations

# MUST run before `.core`, which imports `factlattice_engine.lint` at module level.
from ._enginepath import ensure_engine_on_path

ensure_engine_on_path()

from .core import (  # noqa: E402 — deliberate: the engine path must be seated first
    CANONICAL_FIELD_ORDER,
    DangleRefusedError,
    DecisionOpsError,
    KNOWN_LIFECYCLE_VALUES,
    NODE_ID_RE,
    NodeExistsError,
    NodeNotFoundError,
    OpResult,
    REQUIRED_FIELDS,
    add,
    find_node_span,
    query,
    render_node,
    resolve,
    update,
)

# DSD-as-decision-materials seam (D5 + D6): the send-for-review endpoint and the
# save-close [DEC-*] minter, on the ONE decision-ops surface ([DD-DSDV-MCP-HOME]).
from .dsd_review import (  # noqa: E402
    DSD_LOCK_MARKER,
    build_minted_node,
    mint_decision_nodes,
    minted_node_id,
    send_dsd_for_human_review,
)

# The hookup guide is a decision-materials artifact too — and the one with the
# HIGHEST cost of being misread (a wrong diagram becomes a wrong JOINT), so its
# send-for-review endpoint lives on the SAME one decision-ops surface, with a
# render-and-label-gate its DSD sibling does not need.
from .hookup_review import send_hookup_for_human_review  # noqa: E402

__all__ = [
    "add", "update", "query", "resolve",
    "OpResult", "DecisionOpsError", "NodeNotFoundError", "NodeExistsError",
    "DangleRefusedError",
    "render_node", "find_node_span",
    "NODE_ID_RE", "CANONICAL_FIELD_ORDER", "REQUIRED_FIELDS", "KNOWN_LIFECYCLE_VALUES",
    "send_dsd_for_human_review", "send_hookup_for_human_review",
    "mint_decision_nodes", "build_minted_node",
    "minted_node_id", "DSD_LOCK_MARKER",
]
