"""composeio.decision_ops.dsd_review — the DSD *is* a decision-materials artifact,
so its two decision-ops seams live here, on the ONE decision-ops surface
([SPEC:CXC-DSDV], [DD-DSDV-MCP-HOME] RESOLVED — D5).

Two functions, both consumed by the decision-ops MCP server (``mcp_server.py``)
and the DSD viewer, never a second server:

  send_dsd_for_human_review(dsd_path)   [D5, CXC-DSDV-MCP]
      Validate ``dsd_path`` resolves to a real, parseable DSD under the repo
      root, PUBLISH an ``_app/dsd/open {path, correlation_id}`` intent on the
      global bus (the in-process ``dsd_viewer_launcher`` spawns the window), and
      return a correlatable status.  RE-HOMED verbatim from
      ``compose_projects/intelligence/tools/dsd_review_mcp.py`` (now a thin compat shim) — same
      fixed signature + behavior (validate, publish, return a status), just
      living on the decision-ops surface where a decision-materials artifact
      belongs.

  mint_decision_nodes(graph, dsd_path, decisions, answers)   [D6, CXC-DSDV-SAVE]
      On the human's save-close, mint one ``[DEC-*]`` node per ANSWERED decision
      through the guarded decision-ops write path (``core.add`` — same
      dangle/collision refusal + lint guard the MCP tools use), each carrying the
      answer AS THE HUMAN WORDED IT (chosen option label(s) + verbatim comment,
      and whether it deviates from the recommendation), a ``dsd-source`` pointer
      back at the DSD, and a read-only lock marker so the auto-minted node is
      never hand-edited.  This kills the manual transcription the architect did
      by hand after every DSD.

Both are pure/injectable at the edges (an injectable ``publish`` sink; an
injectable ``add_fn``) so validate+publish and mint+guard are testable without a
broker or the real graph.
"""
from __future__ import annotations

import datetime
import hashlib
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .core import add as _core_add

# Repo root, CANONICAL: `git rev-parse --git-common-dir` walks back to the main
# checkout even when this module is a linked worktree's copy of itself (the
# parents[3] form silently answered "the worktree" — see graph_home). A
# COMPOSEIO_ROOT override still wins, and parents[3] survives only for a checkout
# that is not a git repo at all.
#
# NOTE the deliberate asymmetry with the graph WRITE guard: a DSD is a working
# document, so `resolve_dsd()` below accepts one under EITHER the canonical root or
# the worktree you are actually standing in. Reviewing the DSD in your own worktree
# is legitimate; filing a permanent RECORD there is not.
from .graph_home import canonical_repo_root as _canonical_repo_root

REPO_ROOT = Path(os.environ.get("COMPOSEIO_ROOT")
                 or _canonical_repo_root(__file__)
                 or Path(__file__).resolve().parents[3])

# EXTRA DSD roots, `os.pathsep`-separated, in the shape of COMPOSEIO_ROOT above.
# `os.pathsep` and not whitespace ON PURPOSE: the motivating path contains a
# space (`${DATASHEETS_HOME}/R503`), and a space-separated list would
# split it into two roots that do not exist.  See `configured_dsd_roots`.
DSD_ROOTS_ENV = "COMPOSEIO_DSD_ROOTS"

# ---- D5: send_dsd_for_human_review -----------------------------------------
INTENT_NS = "_app/dsd/open"
DEFAULT_MQTT = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")

# ---- D6: minted-node conventions -------------------------------------------
# The read-only lock marker: an auto-minted node is a permanent record of what
# the human decided at save-close; this bullet tells a later reader (human or
# Intelligence) NOT to hand-edit it (the DSD save-close is its sole writer).
DSD_LOCK_MARKER = "auto-minted from DSD save-close — do not hand-edit"
_DEC_ID_PREFIX = "DEC-DSD"
_MINTED_LIFECYCLE = "decided"
_MINTED_IMPORTANCE = "medium"


# ===========================================================================
# D5 — send_dsd_for_human_review (re-homed; behavior identical)
# ===========================================================================
def own_checkout_root() -> Path:
    """The root of the checkout THIS MODULE was loaded from.

    Not `REPO_ROOT` — that is deliberately the CANONICAL checkout, so that a
    permanent record has one home. This is the other question: *which tree is
    the code I am running actually made of?* They differ whenever the module is
    imported from a linked worktree, and code that must measure ITSELF (a
    `_selftest`, a fixture load) has to ask THIS one. Reading a fixture from the
    canonical tree while executing a worktree's code is a split-brain: it can
    fail against correct code, and — worse — PASS against broken code because
    the canonical fixtures happened to be good. See [CXC-HKR-SELFTEST-ROOT]."""
    from .graph_home import containing_worktree_root
    wt = containing_worktree_root(Path(__file__).resolve())
    return (wt or Path(__file__).resolve().parents[3]).resolve()


def configured_dsd_roots(raw: Optional[str] = None):
    """Parse ``COMPOSEIO_DSD_ROOTS`` into (accepted roots, rejected entries).

    ⭐ THE ROOT SET IS CONFIGURATION, NOT A CONSTANT ([CXC-DSDV-EXTRA-ROOTS],
    requested 2026-08-17 by the R503 composeux-architect).  The three computed
    roots all say "a DSD lives in composeio", which was true when written and is
    no longer: a design project staged OUTSIDE the checkout (by David's standing
    ruling, and not under git at all) had no DSD-delivery path, and the
    workaround in use was COPYING the DSD into someone else's tree purely to
    satisfy the check.  Routing around a guard by moving the artefact is the
    symptom of a root list that is too narrow, not of a guard that is too strict.

    ⛔ THIS WIDENS THE ROOT SET; IT DOES NOT WEAKEN CONTAINMENT.  Every path is
    still checked for containment under SOME root — `/etc/passwd` is refused
    with this configured exactly as it is without it.  The two entry shapes that
    would quietly turn the guard OFF are therefore REFUSED rather than honoured:

      * a RELATIVE entry.  `Path("docs").resolve()` silently means "under
        whatever cwd this process happens to have", so the admitted set would
        change with the caller's directory — a containment root that moves is
        not a containment root.  (An empty entry is the worst case of this: it
        resolves to cwd itself.)
      * the FILESYSTEM ROOT.  `COMPOSEIO_DSD_ROOTS=/` contains every path on the
        machine, so honouring it would make `resolve_dsd` a no-op while still
        LOOKING like a guard — the exact failure mode the request named.

    `~` is expanded first (an operator writes `~/...`), and the
    separator is `os.pathsep` — NOT whitespace — because the motivating path
    contains a space (`${DATASHEETS_HOME}/R503`).

    REJECTIONS ARE RETURNED, NOT SWALLOWED.  A silently-dropped root would
    present as "not a repo file" for a path the operator believes they admitted,
    with nothing anywhere saying why; `resolve_dsd`'s caller folds these into its
    refusal message so a typo diagnoses itself.  (An instrument that can only
    report success is not an instrument — [CXC-VISION-EPISTEMICS].)"""
    if raw is None:
        raw = os.environ.get(DSD_ROOTS_ENV, "")
    accepted: List[Path] = []
    rejected: List[tuple] = []
    for entry in (raw or "").split(os.pathsep):
        entry = entry.strip()
        if not entry:
            continue
        p = Path(entry).expanduser()
        if not p.is_absolute():
            rejected.append((entry, "not an absolute path"))
            continue
        resolved = p.resolve()
        if len(resolved.parts) <= 1:
            rejected.append((entry, "the filesystem root is not a containment "
                                    "root — it would admit every path"))
            continue
        accepted.append(resolved)
    return accepted, rejected


def _allowed_dsd_roots() -> List[Path]:
    """The roots a DSD may legitimately live under: the CANONICAL checkout, the
    worktree the caller is actually standing in, the checkout THIS CODE was
    loaded from, and any root the operator has declared in
    ``COMPOSEIO_DSD_ROOTS``.

    A DSD is a working document — reviewing the one in your own worktree is the
    normal case — so widening here costs nothing and keeps the containment guard
    intact. (Contrast the graph WRITE guard, which refuses a worktree path outright:
    a permanent RECORD has exactly one home.)

    The third root is what lets a module address its OWN tree by absolute path
    regardless of where the process was launched from — without it, a selftest
    that correctly refuses to read the canonical tree's fixtures would find its
    own fixtures refused as out-of-repo.

    The fourth SOURCE is configuration, and it is the only one that is not a
    repo root — see `configured_dsd_roots` for why that does not cost the
    containment property, and for the two entry shapes it refuses because they
    would. The containment property is unchanged in both directions: a path
    under no root is still refused, `/etc/passwd` included.

    Read at CALL time, never memoised: the root set is configuration, and a
    process that cached it at import would answer with the environment it was
    born in rather than the one it is running in."""
    roots = [REPO_ROOT.resolve()]

    def _add(p: Optional[Path]) -> None:
        if p is not None and p.resolve() not in roots:
            roots.append(p.resolve())

    from .graph_home import containing_worktree_root
    try:
        _add(containing_worktree_root(Path.cwd()))
    except OSError:
        pass
    _add(own_checkout_root())
    # Configured roots go LAST so a repo root always wins the relative-path
    # candidate race below — an in-repo `docs/x.md` resolves exactly as it did
    # before this existed.
    for extra in configured_dsd_roots()[0]:
        _add(extra)
    return roots


def resolve_dsd(path: str) -> Optional[str]:
    """Resolve *path* under an allowed root, refusing an escape (the launcher's
    guard mirrored).  Returns the absolute path iff it is a real file inside one
    of those roots, else None.

    ⭐ THE SHARED RESOLVER — `hookup_review` and `beepout_review` IMPORT this
    (as `resolve_repo_file` / `_resolve_repo_file`) rather than carrying a copy,
    so the three review surfaces cannot drift and a widening reaches all three
    at once.  Do not fork it.

    Which roots are allowed is `_allowed_dsd_roots()`'s question and is now
    partly CONFIGURATION; whether an admitted document is FIT TO PUBLISH is a
    separate, downstream question that this function does not touch."""
    if not path:
        return None
    roots = _allowed_dsd_roots()
    candidates: List[Path] = []
    p = Path(path)
    if p.is_absolute():
        candidates.append(p)
    else:
        candidates.extend(root / p for root in roots)
    for cand in candidates:
        try:
            resolved = cand.resolve()
        except OSError:
            continue
        contained = False
        for root in roots:
            try:
                resolved.relative_to(root)
            except ValueError:
                continue
            contained = True
            break
        if contained and resolved.is_file():
            return str(resolved)
    return None


def _display_path(abspath: str) -> str:
    """The path AS A HUMAN SHOULD READ IT in a status message.

    Repo-relative while the DSD is inside REPO_ROOT (unchanged for every DSD
    that could be sent before configured roots existed), ABSOLUTE otherwise.
    A configured root lives elsewhere on the filesystem, so the old unconditional
    `os.path.relpath(abspath, REPO_ROOT)` answered
    `../../Data Sheets/R503/docs/...` — a string that LOOKS repo-relative, is
    resolvable from nowhere in particular, and names the wrong project. The
    `ValueError` arm is the cross-volume case, where relpath cannot answer at
    all."""
    try:
        rel = os.path.relpath(abspath, REPO_ROOT)
    except ValueError:
        return abspath
    return abspath if rel.startswith(os.pardir + os.sep) or rel == os.pardir \
        else rel


def _not_a_repo_file(path: str) -> Dict[str, Any]:
    """The containment refusal — and, when the operator configured a root that
    was REFUSED, the reason.  Without this a typo'd `COMPOSEIO_DSD_ROOTS` is
    indistinguishable from no configuration at all: same message, no clue."""
    msg = f"not a repo file: {path!r}"
    _, rejected = configured_dsd_roots()
    if rejected:
        msg += (f" — and {DSD_ROOTS_ENV} entr"
                f"{'ies were' if len(rejected) > 1 else 'y was'} REFUSED: "
                + "; ".join(f"{e!r} ({why})" for e, why in rejected))
    return {"status": "error", "path": path, "correlation_id": "",
            "message": msg}


def _is_a_dsd(abspath: str) -> bool:
    """True iff the file PARSES as a DSD (>= 1 ``### D<n>`` decision) — so the
    endpoint never launches the viewer against a non-DSD markdown.  The render
    import is LAZY (kept off the module-import path)."""
    try:
        from composeio.render.widgets.dsd_document import load_dsd
        return load_dsd(abspath).ok
    except Exception:
        return False


def _context_verdict(abspath: str):
    """The CONTEXT GUARD verdict ([CXC-DSDV-CONTEXT-GUARD], David 2026-08-17):
    every decision must carry both a `What this is` and a `Why it matters` line.

    THE SHARED PREDICATE, imported — never re-implemented here.  It lives with the
    DSD grammar (`render.widgets.dsd_document.validate_dsd`) because "what a
    well-formed DSD is" is the parser's business; this module only decides WHEN to
    ask.  A second copy of the predicate would drift from the first, and the drift
    would show up as a document the MCP tool accepts and the viewer refuses.

    WHY THE GATE IS *HERE* AND NOT ONLY IN THE VIEWER: this is the earliest point
    that already has a refusal contract, and refusing here means NO
    `_app/dsd/open` intent is ever published.  A viewer-only check would let a
    defective document sit on the bus as a live intent — retained, replayable, and
    openable by any consumer.  Refuse before you transmit.  (The launcher carries
    the same check as a BACKSTOP, because the bus is multi-master: anyone may
    publish the intent without going through this tool.)

    The render import is LAZY, matching `_is_a_dsd` — the decision-ops surface
    must not drag the render package onto its module-import path.  An import
    failure returns None ("cannot judge"), which is NOT a hole: `_is_a_dsd` runs
    first and ALREADY returns False when that same import fails, so a missing
    renderer refuses the send outright before this is ever reached.
    """
    try:
        from composeio.render.widgets.dsd_document import validate_dsd_file
    except Exception:                              # noqa: BLE001
        return None
    return validate_dsd_file(abspath)


def _publish_intent_mqtt(mqtt_url: str, payload: dict) -> None:
    """One-shot publish of the open intent, THROUGH THE COMPOSEBUS WAIST.

    HISTORY, KEPT BECAUSE IT IS THE ARGUMENT FOR THE SEAM. This used to publish a
    bare ``json.dumps({"val": payload})`` — no ``ns``, ``type``, ``src``, ``ts``,
    ``scope`` or ``seq``. It happened to work only because ``dsd_viewer_launcher``
    unwraps ``d["val"]`` directly; the CANONICAL decoder rejects it outright
    (``BusMessage.from_json`` raises ``KeyError: 'ns'``). That was a message no
    standard consumer could read — a FOURTH envelope dialect on a wire that
    already had three. The 2026-08-08 fix routed it through ``bus_envelope``'s
    framer, which made the bytes right but left the framing decision in this
    file; 2026-08-11 finished the job by handing the framing to the bus, which is
    the only place that can be authoritative about it. A hand-framed envelope
    cannot drift from the canonical one if there is no hand-framing.

    The function name keeps its ``_mqtt`` suffix ON PURPOSE: it is the DEFAULT
    ``publish`` sink and callers pass their own; renaming it would be a silent
    API change for a caller that names it explicitly. What it does is no longer
    MQTT-specific — the substrate comes from ``transport_registry``.

    Wire-compatible: ``bus.publish_state`` emits the same
    ``{ns,type,src,ts,scope,seq,val}`` envelope on the same topic, so ``d["val"]``
    unwrapping in the launcher is unchanged.

    ONE-SHOT, AND THAT NEEDS THE FLUSH. This runs inside an MCP server process
    with no bus of its own and no event loop to keep it alive: it publishes and
    the process may exit immediately. The old code called
    ``info.wait_for_publish``; ``bus.publish_state`` returns as soon as the
    transport has queued the frame, so the transport is stopped explicitly —
    ``MQTTTransport.stop`` runs ``loop_stop`` + ``disconnect``, which flushes
    paho's outbound queue. Dropping that would make a QoS-1 intent vanish on a
    fast exit, silently, which is the failure mode this file has already had once.
    """
    from composeio import source_bus
    from composeio.namespace import Scope
    bus = source_bus.acquire_bus(mqtt_url, label=f"dsd-review-{os.getpid()}")
    source_bus.declare(bus, INTENT_NS, scope=Scope.GLOBAL, source="dsd-review",
                       label="DSD open intent")
    bus.publish_state(INTENT_NS, payload, source="dsd-review")
    bus.stop()
    source_bus.reset_for_tests()   # drop the memo: this process keeps no bus


def send_dsd_for_human_review(
        dsd_path: str, *,
        mqtt_url: str = DEFAULT_MQTT,
        correlation_id: Optional[str] = None,
        publish: Optional[Callable[[dict], None]] = None) -> Dict[str, Any]:
    """The MCP tool ([CXC-DSDV-MCP]).  Validate *dsd_path* (repo-scoped, real DSD),
    publish an ``_app/dsd/open`` intent, and return a correlatable status.

    ``publish`` is injectable (the intent dict is handed to it) so the
    validate+publish behavior is testable without a broker; the default publishes
    to MQTT.  Returns a status dict:

        {"status": "sent"|"error", "path": <abspath>, "correlation_id": <id>,
         "message": <human text>}

    An unresolvable / non-repo / non-DSD path returns ``status == "error"`` and
    publishes NO intent ([CXC-DSDV-MCP] VALIDATION).  So does a DSD any decision
    of which lacks a ``What this is`` or ``Why it matters`` line
    ([CXC-DSDV-CONTEXT-GUARD], David 2026-08-17) — the returned ``message`` NAMES
    every offending decision and the field it is missing, and ``missing`` carries
    the machine form."""
    abspath = resolve_dsd(dsd_path)
    if abspath is None:
        return _not_a_repo_file(dsd_path)
    if not _is_a_dsd(abspath):
        return {"status": "error", "path": abspath, "correlation_id": "",
                "message": f"not a parseable DSD (no ### D<n> decisions): "
                           f"{_display_path(abspath)}"}
    # [CXC-DSDV-CONTEXT-GUARD] the refusal happens BEFORE anything is published.
    verdict = _context_verdict(abspath)
    if verdict is not None and not verdict.ok:
        return {"status": "error", "path": abspath, "correlation_id": "",
                "missing": list(verdict.missing),
                "message": f"{os.path.relpath(abspath, REPO_ROOT)}\n"
                           f"{verdict.message}"}
    corr = correlation_id or uuid.uuid4().hex[:12]
    intent = {"path": abspath, "correlation_id": corr}
    sink = publish or (lambda p: _publish_intent_mqtt(mqtt_url, p))
    try:
        sink(intent)
    except Exception as exc:
        return {"status": "error", "path": abspath, "correlation_id": corr,
                "message": f"publish failed: {exc}"}
    rel = _display_path(abspath)
    return {"status": "sent", "path": abspath, "correlation_id": corr,
            "message": f"sent for review: {rel} (correlation {corr}) — the "
                       "human's answers will notify you on save-close"}


# ===========================================================================
# D6 — mint [DEC-*] nodes from DSD answers, through the guarded write path
# ===========================================================================
def _today() -> str:
    return datetime.date.today().isoformat()


def _dsd_hash(abspath: str) -> str:
    """A short, stable, id-safe (uppercase hex) tag for a DSD path — the middle
    segment of a minted node id, so ids are deterministic per (DSD, decision)."""
    return hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:8].upper()


def minted_node_id(dsd_abspath: str, decision_id: str) -> str:
    """The deterministic ``[DEC-DSD-<hash>-<Dn>]`` id for one DSD decision.
    Matches ``core.NODE_ID_RE`` (uppercase, ``-``-joined ``[A-Z0-9]+`` segments)."""
    dec_seg = str(decision_id).upper().replace("-", "")
    return f"{_DEC_ID_PREFIX}-{_dsd_hash(dsd_abspath)}-{dec_seg}"


def _reco_label(dec: Any) -> Optional[str]:
    pid = getattr(dec, "preselect_id", None)
    if not pid:
        return None
    getter = getattr(dec, "option_by_id", None)
    opt = getter(pid) if callable(getter) else None
    return getattr(opt, "label", None) if opt is not None else None


def _relpath(abspath: str) -> str:
    try:
        return os.path.relpath(abspath, REPO_ROOT)
    except ValueError:
        return abspath


def build_minted_node(dsd_abspath: str, dec: Any, ans: Any,
                      *, today: Optional[str] = None,
                      session: Optional[str] = None) -> Optional[Dict[str, object]]:
    """The ``core.add`` node dict for one answered decision, or None if the
    decision was left unanswered (no chosen option and no comment).

    Records the answer AS WORDED: the chosen option LABEL(s) verbatim, the human's
    verbatim comment, and — when the pick differs from the architect's
    recommendation — an explicit ``deviates-from-recommendation: yes`` plus the
    deviation carried in the rationale in the human's own words.  Adds a
    ``dsd-source`` pointer and the read-only lock marker."""
    labels = [l for l in (getattr(ans, "labels", None) or []) if l]
    comment = (getattr(ans, "comment", "") or "").strip()
    if not labels and not comment:
        return None  # unanswered — nothing to mint (matches build_completion)

    chosen = ", ".join(labels)
    reco = _reco_label(dec)
    deviated = bool(reco) and reco not in labels

    # rationale: the human's call in their own words (the as-worded record).
    parts = [f"Human's call on {getattr(dec, 'id', '?')}: {chosen or '(comment only)'}"]
    if deviated:
        parts.append(f"deviates from recommendation ({reco})")
    if comment:
        parts.append(f"comment: {comment}")
    rationale = " — ".join(parts)

    node: Dict[str, object] = {
        "id": minted_node_id(dsd_abspath, getattr(dec, "id", "")),
        "title": (f"DSD call · {getattr(dec, 'id', '?')} · "
                  f"{getattr(dec, 'question', '') or 'decision'}").strip(),
        "lifecycle-position": _MINTED_LIFECYCLE,
        "decided-in-session": session or today or _today(),
        "importance": _MINTED_IMPORTANCE,
        "rationale": rationale,
        "answer": chosen or "(comment only)",
        "deviates-from-recommendation": "yes" if deviated else "no",
        "dsd-source": f"{_relpath(dsd_abspath)}#{getattr(dec, 'id', '')}",
        "read-only-lock": DSD_LOCK_MARKER,
    }
    if comment:
        # the deviation/answer in the human's exact words, as its own bullet too.
        node["comment"] = comment
    return node


def mint_decision_nodes(graph_path: str, dsd_path: str,
                        decisions: Sequence[Any], answers: Dict[str, Any],
                        *, add_fn: Optional[Callable[..., Any]] = None,
                        profile: Optional[str] = None,
                        today: Optional[str] = None,
                        session: Optional[str] = None) -> List[Dict[str, Any]]:
    """Mint one ``[DEC-*]`` node per ANSWERED decision into *graph_path*, through
    the guarded decision-ops write path.

    Returns a per-decision list of result dicts
    ``{decision_id, node_id, status, changed, message}`` where ``status`` is one
    of ``minted`` / ``idempotent`` / ``exists-different`` / ``refused`` /
    ``error`` / ``skipped``.  BEST-EFFORT + FAIL-SAFE: a per-node failure (a
    refused dangle, a collision, any exception) is recorded and the next decision
    still gets its turn — the DSD save-close must never be broken by the
    decision-graph write.

    ``add_fn`` defaults to the guarded ``composeio.decision_ops.core.add`` (the
    SAME write path the MCP tools use, so the forward-dangle refusal is intact);
    it is injectable for tests.  Nothing is written when *graph_path* is falsy."""
    add = add_fn or _core_add
    out: List[Dict[str, Any]] = []
    if not graph_path:
        return out
    abspath = os.path.abspath(dsd_path) if dsd_path else dsd_path
    for dec in decisions:
        dec_id = getattr(dec, "id", "")
        ans = answers.get(dec_id)
        if ans is None:
            continue
        node = build_minted_node(abspath, dec, ans, today=today, session=session)
        if node is None:
            out.append({"decision_id": dec_id, "node_id": None,
                        "status": "skipped", "changed": False,
                        "message": "unanswered"})
            continue
        node_id = node["id"]
        try:
            res = add(graph_path, node, profile=profile)
            changed = bool(getattr(res, "changed", False))
            out.append({"decision_id": dec_id, "node_id": node_id,
                        "status": "minted" if changed else "idempotent",
                        "changed": changed,
                        "message": getattr(res, "message", "")})
        except Exception as exc:  # noqa: BLE001 — best-effort, never break save
            name = type(exc).__name__
            status = ("exists-different" if name == "NodeExistsError"
                      else "refused" if name == "DangleRefusedError"
                      else "error")
            out.append({"decision_id": dec_id, "node_id": node_id,
                        "status": status, "changed": False,
                        "message": f"{name}: {exc}"})
    return out


# ===========================================================================
# selftest — logic only, no broker, injected publish / add (shared by the CLI)
# ===========================================================================
def _selftest() -> int:
    """Fixtures come from THIS MODULE'S OWN CHECKOUT — see [CXC-HKR-SELFTEST-ROOT].

    The same split-brain the hookup sibling hit on 2026-08-15: a repo-RELATIVE
    fixture path binds to the CANONICAL checkout first, so a copy of this module
    imported from a linked worktree measures a DIFFERENT TREE'S data than the
    code it is testing. Fixed here at the same time because it is one defect
    with two instances, not two defects."""
    fails: List[str] = []
    root = own_checkout_root()

    def fixture(rel: str) -> str:
        """A fixture path, absolute, in the tree this code is made of."""
        return str(root / rel)

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    published: List[dict] = []

    def fake_publish(intent):
        published.append(intent)

    # -- D5 send_dsd_for_human_review -----------------------------------------
    # The happy-path fixture is a CONTEXT-COMPLETE DSD.  It used to be a retained
    # historical DSD (`docs/intelligence-console/DECISIONS-2026-07-23-morning-
    # batch.md`), which [CXC-DSDV-CONTEXT-GUARD] now correctly REFUSES — most of
    # the retained corpus predates the ruling — so it can no longer stand in for
    # "a valid send".
    real_dsd = fixture("python/tests/render/fixtures/dsd/complete.md")
    if resolve_dsd(real_dsd) is None:
        print("SKIP: fixture DSD missing:", real_dsd)
    else:
        r = send_dsd_for_human_review(real_dsd, publish=fake_publish)
        check(r["status"] == "sent", "(1) valid DSD -> sent")
        check(len(published) == 1, "(2) exactly one intent published")
        check(published[0]["path"] == resolve_dsd(real_dsd),
              "(3) intent carries the abspath")
        check(r["correlation_id"] and published[0]["correlation_id"]
              == r["correlation_id"], "(4) correlation id returned + on intent")

    published.clear()
    r2 = send_dsd_for_human_review("/etc/passwd", publish=fake_publish)
    check(r2["status"] == "error" and not published, "(5) outside-repo -> error, no publish")
    r3 = send_dsd_for_human_review(fixture("docs/nope.md"), publish=fake_publish)
    check(r3["status"] == "error" and not published, "(6) missing -> error, no publish")

    non_dsd = fixture("compose_projects/intelligence/console/dsdviewer.compose.yaml")
    if resolve_dsd(non_dsd) is not None:
        r4 = send_dsd_for_human_review(non_dsd, publish=fake_publish)
        check(r4["status"] == "error" and not published,
              "(7) non-DSD repo file -> error, no publish")

    published.clear()
    if resolve_dsd(real_dsd) is not None:
        r5 = send_dsd_for_human_review(real_dsd, correlation_id="fixed-1",
                                       publish=fake_publish)
        check(r5["correlation_id"] == "fixed-1"
              and published[0]["correlation_id"] == "fixed-1",
              "(8) explicit correlation id honored")

    # -- CONFIGURED EXTRA ROOTS, BOTH DIRECTIONS ------------------------------
    # [CXC-DSDV-EXTRA-ROOTS].  The widening arm and its NEGATIVE CONTROL run
    # against the SAME configuration, in this order, because a root list observed
    # only admitting things has not been shown to refuse anything
    # ([CXC-VISION-EPISTEMICS] rule-negative-control).  The tmp root carries a
    # SPACE, which is the separator regression this feature can actually have.
    import shutil
    import tempfile
    published.clear()
    _tmp = tempfile.mkdtemp(prefix="dsd roots ")      # a space, on purpose
    try:
        _outside = Path(_tmp) / "OUTSIDE.md"
        _outside.write_text(
            "# DSD\n\n### D1 · Outside the checkout\n"
            "**What this is:** a DSD staged outside composeio.\n"
            "**Why it matters:** it is the case this root list exists for.\n\n"
            "| Option | Tradeoffs |\n|---|---|\n| A | a |\n| B | b |\n\n"
            "**Your call:** _______\n", encoding="utf-8")
        check(resolve_dsd(str(_outside)) is None,
              "(9) BEFORE: an outside-the-checkout DSD is refused")
        _prev = os.environ.get(DSD_ROOTS_ENV)
        os.environ[DSD_ROOTS_ENV] = _tmp
        try:
            r8 = send_dsd_for_human_review(str(_outside), publish=fake_publish)
            check(r8["status"] == "sent" and len(published) == 1,
                  "(10) with the root configured, the outside DSD is SENT")
            check(r8["message"].count("..") == 0,
                  "(11) the message names it absolutely, not as ../../")
            published.clear()
            r9 = send_dsd_for_human_review("/etc/passwd", publish=fake_publish)
            check(r9["status"] == "error" and not published,
                  "(12) NEGATIVE CONTROL: /etc/passwd is STILL refused with "
                  "the same configuration active")
            check(resolve_dsd(str(REPO_ROOT / "README.md")) is not None
                  or not (REPO_ROOT / "README.md").is_file(),
                  "(13) an in-repo path resolves exactly as before")
        finally:
            if _prev is None:
                os.environ.pop(DSD_ROOTS_ENV, None)
            else:
                os.environ[DSD_ROOTS_ENV] = _prev
        check(resolve_dsd(str(_outside)) is None,
              "(14) AFTER: the widening is scoped to the configuration")
    finally:
        shutil.rmtree(_tmp, ignore_errors=True)

    # a relative / filesystem-root entry is REFUSED, with a reason, not honoured.
    _acc, _rej = configured_dsd_roots(os.pathsep.join(["relative/dir", "/", ""]))
    check(_acc == [], "(15) neither a relative entry nor `/` becomes a root")
    check({e for e, _ in _rej} == {"relative/dir", "/"},
          "(16) both refusals are REPORTED, so a typo diagnoses itself")
    # -- [CXC-DSDV-CONTEXT-GUARD] both directions, per the negative-control rule
    # ([CXC-VISION-EPISTEMICS]).  Fixture 1 is THE REAL ARTEFACT that reached the
    # human's glass undecidable on 2026-08-17 — the strongest test available, and
    # it costs nothing.
    published.clear()
    bad = fixture("docs/architecture/"
                  "DECISIONS-2026-08-17-migration-and-programme.md")
    if resolve_dsd(bad) is not None:
        r6 = send_dsd_for_human_review(bad, publish=fake_publish)
        check(r6["status"] == "error" and not published,
              "(17) context-incomplete DSD -> error, NOTHING published")
        check(any(m["decision_id"] == "D2"
                  and m["field"] == "what_this_is" for m in r6.get("missing", [])),
              "(18) the refusal names D2 (the Turbot images) + the missing field")
        check("What this is" in r6["message"] and "Turbot" in r6["message"],
              "(19) the human-readable message names the decision by heading")

    published.clear()
    good = fixture("python/tests/render/fixtures/dsd/complete.md")
    if resolve_dsd(good) is not None:
        r7 = send_dsd_for_human_review(good, publish=fake_publish)
        check(r7["status"] == "sent" and len(published) == 1,
              "(20) NEGATIVE CONTROL: a context-complete DSD is still SENT")

    published.clear()
    blank = fixture("python/tests/render/fixtures/dsd/blank_label.md")
    if resolve_dsd(blank) is not None:
        r8 = send_dsd_for_human_review(blank, publish=fake_publish)
        miss = {(m["decision_id"], m["field"]) for m in r8.get("missing", [])}
        check(r8["status"] == "error" and not published,
              "(21) a PRESENT-but-empty label is BLANK -> refused")
        check(miss == {("D2", "what_this_is"), ("D3", "why_it_matters")},
              "(22) exactly the empty fields are named — D1 (complete) is NOT")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: dsd_review selftest (22/22)")
    return 0


__all__ = [
    "send_dsd_for_human_review", "resolve_dsd", "configured_dsd_roots",
    "DSD_ROOTS_ENV", "INTENT_NS", "DEFAULT_MQTT",
    "mint_decision_nodes", "build_minted_node", "minted_node_id",
    "DSD_LOCK_MARKER",
]
