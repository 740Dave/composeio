"""composeio.decision_ops.mcp_server — a thin stdio MCP server over the deterministic
add/update/query/resolve CORE (`core.py`), with an lts-engine **refresh on write**.

    python3 -m composeio.decision_ops.mcp_server         # speak MCP over stdio
    python3 -m composeio.decision_ops.mcp_server --selftest

HOUSE PATTERN. Built to match `factlattice_engine/server.py`: newline-delimited
JSON-RPC 2.0 over stdio (the MCP stdio framing), STDLIB ONLY, and a transport-agnostic
`DecisionOpsMcp.dispatch(name, arguments) -> dict` seam the stdio shell wraps (so a
future ComposeBus request/announcement handler swaps in without touching op logic).
The only non-stdlib imports are this repo's own `composeio.decision_ops.core` (the
single-writer) and, on the refresh path, the read-only `factlattice_engine`.

THE WRITE PATH (add / update / resolve):
  1. call the core op (line-level splice + re-verify via composeio.lts_lint, with the
     content-keyed dangle/collision rollback that raises DangleRefusedError);
  2. IFF the core reports `changed=True`, push an lts-engine refresh so the new/updated node
     is immediately queryable with no stale window (see lts_refresh.py for why
     rebuild-from-corpus is the design the content-hash freshness kernel supports);
  3. on DangleRefusedError (or any core error) the op RAISED before returning — no
     refresh is pushed, because nothing changed on disk (the graph is byte-identical).
  A `changed=False` idempotent no-op also pushes NO refresh (nothing to make visible).

THE READ PATH (query, lts_query): never triggers a refresh.

FROZEN: this server does NOT touch core.py's write semantics — it wraps them. It also
does NOT flip the hand-append path to sole-writer (David's glass call, per the package
__init__ FLAGGED item 3).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from typing import Any, Dict, Optional

from .core import (
    SESSION_ENV,
    DangleRefusedError,
    DecisionOpsError,
    NodeExistsError,
    NodeNotFoundError,
    add,
    ambient_session,
    query,
    resolve,
    update,
)
from .dsd_review import send_dsd_for_human_review
from .hookup_review import send_hookup_for_human_review
from .beepout_review import send_beepout_for_human_review
from .graph_home import WorktreeGraphRefused, graph_receipt
from .lts_refresh import LtsRefresher, corpus_id_for_graph

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "composeio-decision-ops", "version": "0.1.0"}

# The freshness kernel's "root read as empty" signature: combined_files_hash([]) over a
# vanished/retired root == sha256(b"") ([LEC-READ-PATH-VANISHED-ROOT]).
_EMPTY_SHA = "sha256:" + hashlib.sha256(b"").hexdigest()  # e3b0c442...b855


def is_vanished_root(res: dict) -> bool:
    """True when an lts-engine read is terminal-stale because the served ROOT is gone.

    The engine has signalled this two ways, and both must be read:
      * live_hash == sha256("") — the content re-hash of a root that reads as empty (the
        original [LEC-READ-PATH-VANISHED-ROOT] signature);
      * reason == "root-set-changed" with live_hash None — since engine 85c5834 (2026-10-28)
        `Partition.freshness()` checks the registered root SET before any content hash, so a
        vanished root now reports that documented reason and never reaches the re-hash.
    Reading only the first signature silently dropped the accurate remedy for every
    vanished root after that engine change."""
    if not res.get("stale"):
        return False
    return res.get("live_hash") == _EMPTY_SHA or res.get("reason") == "root-set-changed"

_COMPOSEIO_ROOT = os.environ.get(
    "COMPOSEIO_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
# The domain profile now lives with the platform-specs corpus; resolve its
# repo-relative location from the single source of truth (bootstrap_paths).
from composeio.bootstrap_paths import COMPOSEIO_PROFILE_REL as _DEFAULT_PROFILE_REL


def _default_profile() -> Optional[str]:
    env = os.environ.get("DECISION_OPS_PROFILE")
    if env:
        return env if os.path.isfile(env) else None
    for base in (os.getcwd(), _COMPOSEIO_ROOT):
        cand = os.path.join(base, _DEFAULT_PROFILE_REL)
        if os.path.isfile(cand):
            return cand
    return None


def _default_graph() -> Optional[str]:
    g = os.environ.get("DECISION_OPS_GRAPH")
    return g or None


class CorpusPathError(RuntimeError):
    """A configured DEFAULT corpus/graph path is missing or empty — a HARD config fault
    ([LEC-CORPUS-PATH-MUST-EXIST]). Raised so the server REFUSES to serve rather than let a
    missing file masquerade as an empty corpus: a previously-built partition then diverges
    from live_hash=sha256("") and every read answers TERMINAL stale that no rebuild can clear
    (the file the self-heal would rebuild from does not exist)."""


def validate_default_graph(path: Optional[str]) -> None:
    """Guard the freshness kernel's file-is-truth assumption at STARTUP.

    If a default graph is configured (`DECISION_OPS_GRAPH`), it MUST exist and be non-empty.
    A missing path is config drift — e.g. a corpus reorg the gitignored `.mcp.json` could not
    follow — and MUST fail LOUD here, never silently reconcile to sha256("") -> permanent stale
    ([LEC-CORPUS-PATH-MUST-EXIST]). A `None` default (no env) is allowed: callers then pass an
    explicit `graph` per op, so there is no default path to validate."""
    if not path:
        return
    if not os.path.isfile(path):
        raise CorpusPathError(
            f"DECISION_OPS_GRAPH points at a MISSING corpus file: {path!r}. A missing corpus "
            "path is a config fault, not an empty corpus ([LEC-CORPUS-PATH-MUST-EXIST]); "
            "refusing to serve. If the corpus was reorganized, repoint DECISION_OPS_GRAPH "
            "(e.g. in .mcp.json) at the file's current location.")
    if os.path.getsize(path) == 0:
        raise CorpusPathError(
            f"DECISION_OPS_GRAPH points at an EMPTY (0-byte) corpus file: {path!r}. Refusing to "
            "serve rather than reconcile to sha256('') -> terminal stale "
            "([LEC-CORPUS-PATH-MUST-EXIST]).")
    # [LEC-CORPUS-BUILDS-NONEMPTY]: bytes present is not content present. A file that exists
    # and is non-empty but BUILDS to zero nodes is path-to-wrong-file / parser / area drift —
    # it would otherwise sail past the byte guards above and serve every query as an empty
    # corpus (the 2026-07-31 recurrence band). Build it; refuse loud if it yields nothing.
    # Engine-absent (build cannot run) degrades gracefully: file-is-truth, never block startup
    # on a missing engine (RefreshResult.ok=False on an engine ImportError/degrade).
    res = LtsRefresher().refresh(path)
    if res.ok and (res.total_nodes or 0) == 0:
        raise CorpusPathError(
            f"DECISION_OPS_GRAPH points at a non-empty corpus that BUILDS TO ZERO NODES: {path!r} "
            "([LEC-CORPUS-BUILDS-NONEMPTY]). The bytes are present but the engine extracted no "
            "nodes — most likely the path drifted to the WRONG file, a parser/schema regression, "
            "or an 'area' filter that no longer matches this graph's node tags. Refusing to serve "
            "an empty view. Repoint DECISION_OPS_GRAPH at the intended graph and verify it parses.")


# --- MCP tool schemas ---------------------------------------------------------
_STR = {"type": "string"}
_OBJ = {"type": "object"}
_GRAPH = {**_STR, "description": "path to the decision-graph .lts (defaults to "
                                 "$DECISION_OPS_GRAPH if set). A path inside a LINKED "
                                 "GIT WORKTREE is REFUSED — a record written there is "
                                 "live on no graph; the refusal names the canonical "
                                 "path to re-run against."}
TOOLS = [
    {
        "name": "decision_add",
        "description": ("Append a NEW decision node to a project decision-graph .lts, in "
                        "the graph's exact byte format; refuses+rolls back a NEW forward "
                        "dangle/collision (DangleRefusedError). On a real write, refreshes "
                        "the lts-engine index so the node is immediately queryable. Idempotent. "
                        "OMIT decided_in_session to inherit this server's AMBIENT session "
                        "token when one is set (check it with decision_session_token); an "
                        "explicit value always wins, and with neither the call errors as usual."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "id": {**_STR, "description": "node id, [PREFIX-NAME] shape e.g. DEC-FOO-BAR"},
            "title": _STR,
            "lifecycle_position": _STR, "decided_in_session": _STR,
            "importance": _STR, "rationale": _STR,
            "fields": {**_OBJ, "description": "any extra bullet fields, e.g. "
                       "{'depends-on': '[DEC-X]', 'relates-to': '[DEC-Y]'}"}},
            "required": ["id", "title"]},
    },
    {
        "name": "decision_update",
        "description": ("Update/insert bullet fields on an existing node (e.g. advance "
                        "lifecycle-position raised->decided->committed), preserving every "
                        "other byte. Refreshes the lts-engine index on a real write. Idempotent."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH, "node_id": _STR,
            "fields": {**_OBJ, "description": "{key: value} bullets to set"}},
            "required": ["node_id", "fields"]},
    },
    {
        "name": "decision_resolve",
        "description": ("Annotate a node resolved/superseded (never deletes). Give status= "
                        "OR superseded_by=. Refreshes the lts-engine index on a real write. Idempotent."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH, "node_id": _STR,
            "status": _STR, "superseded_by": _STR, "note": _STR},
            "required": ["node_id"]},
    },
    {
        "name": "decision_query",
        "description": ("Find nodes by id / prefix / free-text over the local factlattice "
                        "parse of the graph (the anti-re-ask gate). Read-only; no refresh."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "selector": {**_STR, "description": "'DEC-FOO', 'prefix:DEC', 'text:foo'"},
            "limit": {"type": "integer"}}},
    },
    {
        "name": "decision_lts_query",
        "description": ("Query the lts-engine view this server keeps fresh on write (proves the "
                        "no-stale-window guarantee end to end). Dispatches an lts-engine read tool "
                        "against the refreshed in-process partition. Read-only."),
        "inputSchema": {"type": "object", "properties": {
            "graph": _GRAPH,
            "tool": {**_STR, "description": "lts_find|lts_node|lts_traverse|"
                     "lts_capabilities|lts_list_corpora (default lts_find)"},
            "arguments": {**_OBJ, "description": "arguments forwarded to the lts-engine tool "
                          "(corpus is filled in automatically)"}}},
    },
    {
        "name": "send_dsd_for_human_review",
        "description": ("THE PRIMARY interactive path for delivering a Decision-Support-Doc to "
                        "the human ([SPEC:CXC-DSDV] [CXC-DSDV-MCP]): validate that dsd_path is a "
                        "real, parseable DSD under the repo, publish an `_app/dsd/open` intent "
                        "(the in-process launcher spawns the interactive viewer), and return a "
                        "correlatable status. The DSD is a decision-materials artifact, so it "
                        "lives on this one decision-ops surface (D5). Validation-only failure "
                        "publishes nothing. This is the trusted primary path — the "
                        "`open`-the-markdown editable route is a LOUD, announced degradation only "
                        "(never a silent fallback); if this tool is missing from the live surface "
                        "that is a HALT-AND-FIX (reconnect the MCP client), not a reason to route "
                        "around it ([SBR-TRUST]). "
                        "⛔ THE CONTEXT GUARD ([CXC-DSDV-CONTEXT-GUARD], David 2026-08-17): a DSD "
                        "in which ANY decision lacks a `What this is` or a `Why it matters` line is "
                        "REFUSED — status `error`, NOTHING published — and the reply names every "
                        "offending decision by heading in `message` plus `missing: [{decision_id, "
                        "heading, field, label}]`. Blankness is STRUCTURAL: the label must be "
                        "present AND followed by prose that is non-empty once markdown is stripped. "
                        "A refusal is a real outcome: fix the document and re-send. Do NOT fall "
                        "back to `open`-ing the markdown to route around it — that delivers the "
                        "exact defect the guard exists to stop (a DSD that renders perfectly and "
                        "is undecidable)."),
        "inputSchema": {"type": "object", "properties": {
            "dsd_path": {**_STR, "description":
                         "the DSD markdown path (repo-relative or abs). The DSD must live under "
                         "an ALLOWED ROOT: the composeio checkout, your worktree, or any absolute "
                         "root listed in the `COMPOSEIO_DSD_ROOTS` environment variable "
                         "(os.pathsep-separated — `:` on posix — so a root containing a SPACE "
                         "works). That variable is how a project staged OUTSIDE composeio gets a "
                         "DSD-delivery path; copying the DSD into composeio to satisfy the check "
                         "is the wrong fix ([CXC-DSDV-EXTRA-ROOTS]). Anything under no root is "
                         "refused as `not a repo file` and publishes nothing — and if you "
                         "configured a root that was itself refused (relative, or `/`), the "
                         "refusal message names it and says why."},
            "correlation_id": {**_STR, "description": "optional; echoed back and carried on the "
                               "intent so the save-close notify can be correlated"},
            "mqtt_url": {**_STR, "description": "optional broker override "
                         "(default mqtt://mqtt-broker.local:1883)"}},
            "required": ["dsd_path"]},
    },
    {
        "name": "send_beepout_for_human_review",
        "description": (
            "Validate and deliver a BARE-BOARD BEEP-OUT SHEET — the sibling of "
            "send_hookup_for_human_review, gating a DIFFERENT artifact class on a "
            "DIFFERENT predicate. A hookup guide says 'connect these'; a beep-out sheet "
            "says 'probe these two pads and expect THIS', and it is a falsification "
            "instrument for a board with no parts on it — bare is the only time some "
            "faults are visible, because once parts are on, a fab defect and a solder "
            "defect are indistinguishable. PASS THE GENERATED SHEET (a repo .md); the "
            "`.kicad_pcb` it derives from is an external read-only source NAMED BY the "
            "sheet, not passed here. ⛔ THE GATE IS PROVENANCE, NOT PIXELS: the sheet must "
            "carry a BEEPOUT-PROVENANCE block (a hand-authored sheet is REFUSED — a "
            "hand-typed pad pair is a claim, only a netlist-derived one is a "
            "measurement), the netlist must still hash to what the sheet records (a "
            "revised board makes the sheet STALE and it is refused, which ENFORCES the "
            "sync-contract [PCB-CORPUS-PLACEMENT] leaves as a convention), the rows must "
            "match a FRESH re-derivation (a hand-edited row is refused), and a sheet with "
            "ZERO rows is refused because a document that cannot fail is not an "
            "instrument. ANY validation failure publishes NOTHING and returns a clean "
            "success=false. Returns a self-proving receipt: absolute path written, the "
            "netlist and its sha256, and row counts by category and by derivation state "
            "(DERIVED / UNVERIFIED / BLOCKED)."),
        "inputSchema": {"type": "object", "properties": {
            "sheet_path": {**_STR, "description": "the generated beep-out sheet .md "
                           "(repo-relative or abs). Generate one with `python -m "
                           "composeio.maker.beepout.cli BOARD.kicad_pcb -o SHEET.md`"},
            "correlation_id": {**_STR, "description": "optional; echoed back"}},
            "required": ["sheet_path"]},
    },
    {
        "name": "send_hookup_for_human_review",
        "description": (
            "THE interactive path for delivering a WIRING DIAGRAM (hookup guide) to the "
            "human — the sibling of send_dsd_for_human_review, and held to a HIGHER bar. "
            "A misread decision-doc costs a wrong ANSWER; a misread wiring diagram costs a "
            "wrong JOINT, executed with a soldering iron on boards that may already be "
            "panelized. PASS THE `.compose.yaml` SCENE, NOT AN IMAGE: the diagram is "
            "rendered HERE from the `hookup:` netlist, so the picture provably matches the "
            "netlist (a stale PNG is indistinguishable from a fresh one). The netlist is "
            "validated strictly (a wire to a nonexistent pin is an error), the rendered "
            "labels are VERIFIED to have survived rasterization (the shipped skia SVGDOM "
            "path silently drops every label and returns a valid PNG — an unlabelled "
            "diagram is REFUSED, never merely flagged), and an `_app/hookup/open` intent "
            "is published carrying the SOURCE scene, both rendered halves, and the "
            "negative-space warnings. ANY validation failure publishes NOTHING. "
            "⛔ A CROSSING THAT COULD NOT BE BRIDGED IS A HARD FAILURE: where two "
            "conductors cross, the horizontal one is drawn with a small bridge so the "
            "crossing cannot be read as a junction — and an un-bridged crossing renders "
            "as a plain intersection, which on a wiring guide READS AS A SOLDER JOINT "
            "that must not exist. The router reports the crossings it could not bridge "
            "(reasons: pad / corner / crowded); if there are ANY, this REFUSES with "
            "`refused_unhopped_crossing: true` and returns `crossings.rows` naming each "
            "one by both conductors, its coordinate and its reason, plus how to fix it. "
            "THE FIX IS TO RE-ROUTE THE LAYOUT — usually to spread a congested column — "
            "then call again; there is no override flag. The bridges are ALSO verified "
            "to have reached the pixels, the same way the labels are."),
        "inputSchema": {"type": "object", "properties": {
            "hookup_path": {**_STR, "description": "the maker scene `.compose.yaml` "
                            "carrying the `hookup:` block (repo-relative or abs). An "
                            "already-rendered .png/.svg is REFUSED"},
            "correlation_id": {**_STR, "description": "optional; echoed back and carried "
                               "on the intent"},
            "warnings": {"type": "array", "items": _STR,
                         "description": "NEGATIVE SPACE — the connections that must NOT "
                         "exist ('do not butt the modules together, it commons DIO'; "
                         "'leave the -B header unconnected'; 'never use GPIO2/8/9'). A "
                         "netlist can only express connections that DO exist and a drawing "
                         "cannot draw an absence, so these ride the intent and the viewer "
                         "shows them beside the picture. A scene may also carry them in a "
                         "`hookup: do_not:` block"},
            "mqtt_url": {**_STR, "description": "optional broker override "
                         "(default mqtt://mqtt-broker.local:1883)"}},
            "required": ["hookup_path"]},
    },
    {
        "name": "decision_session_token",
        "description": ("Report the AMBIENT session token THIS SERVER PROCESS sees "
                        "($COMPOSEIO_DECISION_SESSION) — the value an omitted "
                        "decided_in_session would inherit on decision_add. Read-only; "
                        "writes nothing. This exists because the token is inherited from "
                        "the process environment at server START, so it is the only way to "
                        "confirm from INSIDE the server that an orchestrator's token actually "
                        "propagated; a shell check answers for a different process. `null` "
                        "means no ambient token: decision_add behaves exactly as it always "
                        "has and decided_in_session must be supplied explicitly."),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


# Graph-consuming tools: these read/write the DEFAULT corpus when no explicit `graph` arg is
# given, so a default-corpus config fault must short-circuit them ([LEC-DECISION-OPS-BOOTS-AND-
# REPORTS]). `send_dsd_for_human_review` and `send_hookup_for_human_review` are
# graph-independent and are deliberately excluded.
_GRAPH_TOOLS = frozenset({
    "decision_add", "decision_update", "decision_resolve",
    "decision_query", "decision_lts_query",
})


class DecisionOpsMcp:
    """Transport-agnostic op layer: `dispatch(name, arguments) -> dict`. ZERO MCP/stdio
    knowledge. Owns the one `LtsRefresher` for this process."""

    def __init__(self, *, default_graph: Optional[str] = None,
                 profile: Optional[str] = None,
                 refresher: Optional[LtsRefresher] = None) -> None:
        self.default_graph = default_graph if default_graph is not None else _default_graph()
        self.profile = profile if profile is not None else _default_profile()
        self.refresher = refresher if refresher is not None else LtsRefresher()
        # [LEC-DECISION-OPS-BOOTS-AND-REPORTS]: a bad DEFAULT corpus is a HARD config fault, but
        # this is a stdio MCP server — it MUST stay alive to REPORT the fault per tool call, never
        # process-exit on it (a dead server surfaces only an opaque -32000, never this diagnostic).
        # We DETECT loud at construction ([LEC-CORPUS-PATH-MUST-EXIST]) and CARRY the message.
        try:
            validate_default_graph(self.default_graph)
            self.default_graph_fault: Optional[str] = None
        except CorpusPathError as exc:
            self.default_graph_fault = str(exc)

    # --- helpers --------------------------------------------------------------
    def _graph(self, arguments: dict) -> str:
        g = arguments.get("graph") or self.default_graph
        if not g:
            raise ValueError("no graph given (pass 'graph' or set $DECISION_OPS_GRAPH)")
        return g

    def _refresh_after_write(self, graph: str, res) -> Optional[dict]:
        """Push an lts-engine refresh IFF a real edit landed. Returns the refresh dict (or
        None on an idempotent no-op). Refresh failures are reported, not raised — the
        write already committed and must not be reported as failed."""
        if not getattr(res, "changed", False):
            return None
        return self.refresher.refresh(graph).to_dict()

    # --- the tools ------------------------------------------------------------
    def _add(self, a: dict) -> dict:
        graph = self._graph(a)
        node: Dict[str, object] = {"id": a["id"], "title": a["title"]}
        for arg_key, field_key in (("lifecycle_position", "lifecycle-position"),
                                    ("decided_in_session", "decided-in-session"),
                                    ("importance", "importance"),
                                    ("rationale", "rationale")):
            if a.get(arg_key) is not None:
                node[field_key] = a[arg_key]
        node.update(a.get("fields") or {})
        res = add(graph, node, profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        return {"success": True, **out}

    def _session_token(self, a: dict) -> dict:
        """The ambient session token THIS process inherited — the propagation
        check for [CXC-GV-SCOPE]. Read-only: no graph is touched, no refresh is
        pushed, and a `null` token is a normal, correct answer (not an error)."""
        tok = ambient_session()
        return {"success": True, "env_var": SESSION_ENV, "session_token": tok,
                "ambient": tok is not None,
                "note": ("decision_add will inherit this when decided_in_session is "
                         "omitted" if tok else
                         "no ambient token — decided_in_session must be supplied "
                         "explicitly, exactly as before")}

    def _update(self, a: dict) -> dict:
        graph = self._graph(a)
        res = update(graph, a["node_id"], a.get("fields") or {}, profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        return {"success": True, **out}

    def _resolve(self, a: dict) -> dict:
        graph = self._graph(a)
        res = resolve(graph, a["node_id"], status=a.get("status"),
                      superseded_by=a.get("superseded_by"), note=a.get("note"),
                      profile=self.profile)
        out = res.to_dict()
        out["engine_refresh"] = self._refresh_after_write(graph, res)
        return {"success": True, **out}

    def _query(self, a: dict) -> dict:
        graph = self._graph(a)
        results = query(graph, a.get("selector"), profile=self.profile,
                        limit=a.get("limit"))
        # The receipt rides the READ too: "I queried and found nothing" is only
        # trustworthy if the reader can see WHICH file was read.
        return {"success": True, "count": len(results), "results": results,
                **graph_receipt(graph)}

    def _lts_query(self, a: dict) -> dict:
        graph = a.get("graph") or self.default_graph
        cid = corpus_id_for_graph(graph) if graph else None
        tool = a.get("tool") or "lts_find"
        args = a.get("arguments") or {}
        res = self.refresher.query(corpus_id=cid, graph_path=graph, tool=tool, **args)
        return self._heal_vanished_root(res, graph, cid, tool, args)

    def _heal_vanished_root(self, res: dict, graph: Optional[str], cid: Optional[str],
                            tool: str, args: dict) -> dict:
        """[LEC-READ-PATH-VANISHED-ROOT]: a terminal-stale read on a vanished root (see
        `is_vanished_root`: live_hash == sha256(""), or reason root-set-changed) means the served partition's ROOT vanished (a moved/renamed
        corpus, or a cached view built from a since-retired path) — NOT an empty corpus. Try ONE
        rebuild from the CURRENTLY-configured path (self-heal without a manual restart); if it is
        still terminal-stale, attach the ACCURATE decision-ops remedy instead of the engine's
        misleading `factlattice_engine.build` hint (which cannot fix an in-process view whose root
        BINDING is wrong)."""
        if not (graph and is_vanished_root(res)):
            return res
        # (1) self-heal: if the configured file is present + non-empty, drop the stale cached
        # view and rebuild from the live path, then re-query.
        if os.path.isfile(graph) and os.path.getsize(graph) > 0:
            self.refresher.refresh(graph)
            res = self.refresher.query(corpus_id=cid, graph_path=graph, tool=tool, **args)
            if not is_vanished_root(res):
                return res
        # (2) still terminal-stale (or the configured path itself is gone): accurate remedy.
        return {**res, "remedy": (
            "VANISHED CORPUS ROOT, not an empty corpus ([LEC-READ-PATH-VANISHED-ROOT]): the "
            "in-process view was built from a path that no longer holds this graph. Repoint "
            f"DECISION_OPS_GRAPH at the graph's CURRENT location (configured: {graph!r}) and "
            "relaunch/reconnect the decision-ops MCP server. Do NOT run "
            "`python -m factlattice_engine.build` — that rebuilds the standalone engine's on-disk "
            "index and cannot fix an in-process view whose root binding is wrong.")}

    def _send_dsd_for_human_review(self, a: dict) -> dict:
        """D5 ([CXC-DSDV-MCP]): validate + publish `_app/dsd/open` for a DSD.
        Wraps the re-homed endpoint (identical validate+publish behavior) in the
        server's `{success: ...}` envelope; a validation failure is a clean
        `success=False`, not an exception."""
        kwargs: Dict[str, Any] = {}
        if a.get("correlation_id"):
            kwargs["correlation_id"] = a["correlation_id"]
        if a.get("mqtt_url"):
            kwargs["mqtt_url"] = a["mqtt_url"]
        res = send_dsd_for_human_review(str(a.get("dsd_path") or ""), **kwargs)
        return {"success": res.get("status") == "sent", **res}

    def _send_beepout_for_human_review(self, a: dict) -> dict:
        """Validate a beep-out sheet is netlist-derived, current, and non-empty.

        Same envelope as its two siblings: a refusal is a clean `success=False`
        carrying the diagnostic, never an exception, and it publishes nothing.
        ⛔ This method contains NO derivation logic — it re-runs
        `composeio.maker.beepout` and compares. Re-implementing any part of the
        derivation here would make the gate a second opinion sharing no code with
        the thing it checks, which is a fork wearing a gate's clothes."""
        kwargs: Dict[str, Any] = {}
        if a.get("correlation_id"):
            kwargs["correlation_id"] = a["correlation_id"]
        res = send_beepout_for_human_review(str(a.get("sheet_path") or ""), **kwargs)
        return {"success": res.get("status") == "sent", **res}

    def _send_hookup_for_human_review(self, a: dict) -> dict:
        """Validate + render + LABEL-GATE + publish `_app/hookup/open` for a maker
        scene.  Same envelope shape as its DSD sibling: a validation failure (or a
        label-gate refusal) is a clean `success=False` carrying the diagnostic, not
        an exception — and publishes nothing."""
        kwargs: Dict[str, Any] = {}
        if a.get("correlation_id"):
            kwargs["correlation_id"] = a["correlation_id"]
        if a.get("mqtt_url"):
            kwargs["mqtt_url"] = a["mqtt_url"]
        if a.get("warnings"):
            kwargs["warnings"] = [str(w) for w in a["warnings"]]
        res = send_hookup_for_human_review(str(a.get("hookup_path") or ""), **kwargs)
        return {"success": res.get("status") == "sent", **res}

    def dispatch(self, name: str, arguments: dict) -> dict:
        a = arguments or {}
        # [LEC-DECISION-OPS-BOOTS-AND-REPORTS]: if the configured DEFAULT corpus is a hard config
        # fault, answer every graph tool call with the actionable diagnostic (unless the caller
        # passed an explicit `graph` that overrides the default) — the server stayed alive so this
        # reaches the operator instead of dying into an opaque -32000.
        if (self.default_graph_fault and name in _GRAPH_TOOLS and not a.get("graph")):
            return {"success": False, "config_fault": True,
                    "error": self.default_graph_fault}
        handlers = {
            "decision_add": self._add, "decision_update": self._update,
            "decision_resolve": self._resolve, "decision_query": self._query,
            "decision_lts_query": self._lts_query,
            "send_dsd_for_human_review": self._send_dsd_for_human_review,
            "send_hookup_for_human_review": self._send_hookup_for_human_review,
            "send_beepout_for_human_review": self._send_beepout_for_human_review,
            "decision_session_token": self._session_token,
        }
        h = handlers.get(name)
        if h is None:
            return {"success": False, "error": f"unknown decision-ops tool: {name}"}
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
        except (NodeExistsError, NodeNotFoundError, DecisionOpsError) as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}",
                    "engine_refresh": None}
        except KeyError as exc:
            return {"success": False, "error": f"missing required argument: {exc}"}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------- #
# stdio MCP shell (mirrors factlattice_engine/server.py)
# --------------------------------------------------------------------------- #
class Server:
    def __init__(self, ops: DecisionOpsMcp):
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


def serve(stdin=None, stdout=None, ops: Optional["DecisionOpsMcp"] = None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(ops or DecisionOpsMcp())
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
        from .mcp_selftest import run
        return run()
    # [LEC-DECISION-OPS-BOOTS-AND-REPORTS]: a bad DEFAULT corpus path is a HARD config fault
    # ([LEC-CORPUS-PATH-MUST-EXIST]), but this is a stdio MCP server — exiting on it surfaces only
    # an opaque -32000 to the client and the diagnostic below is never seen. BOOT ANYWAY (exit 0,
    # stay alive) and report the actionable fault PER TOOL CALL. Constructing the op layer DETECTS
    # the fault (loud) and CARRIES it; we log it to stderr as DEGRADED, never FATAL, never exit.
    ops = DecisionOpsMcp()
    if ops.default_graph_fault:
        print(f"[decision-ops] config fault — serving DEGRADED (every graph tool call returns this "
              f"until fixed; the server stays UP so the error reaches you): {ops.default_graph_fault}",
              file=sys.stderr, flush=True)
    serve(ops=ops)
    return 0


if __name__ == "__main__":
    sys.exit(main())
