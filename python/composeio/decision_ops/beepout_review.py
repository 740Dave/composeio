"""``send_beepout_for_human_review`` — publish a bare-board beep-out sheet for a human.

WHY THIS IS A NEW ENDPOINT AND NOT A MODE OF ``send_hookup_for_human_review``
============================================================================

Argued on the seam, not on convenience:

1. **The refusal predicates are disjoint, over different artifact classes.** The hookup
   gate is ``hookup_svg.label_ink_report`` — a SAME-RASTERIZER DIFFERENTIAL proving a
   PICTURE's labels became ink. A beep-out sheet is a printable text checklist; it has no
   raster whose labels could go mute, so that gate is not merely unnecessary, it is
   inapplicable. Its gate is ROW PROVENANCE: were these rows derived from the netlist, and
   does the netlist still hash to what the sheet claims? One endpoint carrying two disjoint
   predicates selected by a flag is ``[DX-ENGINE-VS-POLICY]``'s exact tell — the generic
   core importing the policy.

2. **The input contracts contradict each other.** The hookup endpoint takes a
   ``.compose.yaml`` and REFUSES pre-rendered images, with a message that names
   ``.compose.yaml`` as the remedy — a message pinned by test H2. A beep-out sheet is a
   markdown document derived from a ``.kicad_pcb``. A merged endpoint would have to
   dispatch on extension and would give the wrong remedy half the time.

3. **The receipts have different shapes.** Hookup returns
   ``labels: {in_svg, ink_px, ratio, sparse}``. Beep-out must return rows-by-category and
   the netlist SHA. A union-typed receipt whose keys depend on a mode flag merges two
   peers' identities, which ``[DX-COLLAPSE-TRANSPORT]`` names as the thing never to
   collapse.

4. **Leaf ownership** (``[DX-LEAF-OWNS-BEHAVIOR]``): a beep-out endpoint can FULLY own
   "are these rows netlist-derived?". The hookup endpoint could only own it by importing
   netlist knowledge it has no other reason to have — pushing leaf behaviour up into a
   coordinator.

WHAT IS REUSED BY REFERENCE, NEVER FORKED
-----------------------------------------
* ``dsd_review.resolve_dsd`` — the repo-containment guard, imported exactly as
  ``hookup_review`` imports it. There is one containment rule and this is not a second one.
* ``composeio.maker.beepout`` — the derivation engine. This module CONTAINS NO DERIVATION
  LOGIC. It re-runs the engine and compares; if it re-implemented any part of the
  derivation it would be checking the sheet against a second opinion that shares no code,
  which is a fork wearing a gate's clothes.

THE INTENT LEG IS NOW LIVE, AND IT LANDED WITH ITS CONSUMER — see ``INTENT_NS``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Reuse by reference: ONE containment guard for the whole decision-ops surface.
from .dsd_review import REPO_ROOT, resolve_dsd as _resolve_repo_file

__all__ = [
    "send_beepout_for_human_review",
    "resolve_sheet",
    "read_provenance",
    "PROVENANCE_RE",
    "INTENT_NS",
    "_selftest",
]

SRC = "beepout-review"

#: PUBLISHED — AND THE CONSUMER LANDED IN THE SAME CHANGE, WHICH IS THE WHOLE RULE.
#:
#: This namespace was DECLARED AND DELIBERATELY MUTE until 2026-08-15, because no party
#: consumed it, and publishing to a namespace no party consumes is a FAILURE THIS
#: CODEBASE HAS ALREADY PAID FOR: the DSD save-close notice originally went to
#: ``_app/dsd/notice``, which nothing subscribed, so every notice went into the void
#: while the publisher reported success. A silent no-op that reports "sent" is strictly
#: worse than an absent feature.
#:
#: The refusal is now DISCHARGED rather than merely lifted. ``compose_projects/intelligence/tools/
#: beepout_viewer_launcher.py`` subscribes this namespace and is registered in
#: ``compose_projects/intelligence/console/data_sources.py``; it opens the sheet in the composeio DOC
#: VIEWER (David 2026-08-15: *"the viewer can be the composeio doc viewer"*), reusing
#: ``doc_viewer_launcher`` by reference rather than forking a second viewer.
#:
#: ⚠ THE SHEET IS STILL A PRINTABLE DOCUMENT AND THE WINDOW DOES NOT CHANGE THAT. The
#: doc viewer is READ-ONLY by construction ([CXC-MDV]) — the ``☐`` column and the
#: ``____________`` ACTUAL column are for a pen at a bench, not for a mouse. The window
#: is for reading and review; paper is still how this artifact is used.
INTENT_NS = "_app/beepout/open"
INTENT_PUBLISHED = True
DEFAULT_MQTT = os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883")

PROVENANCE_RE = re.compile(
    r"<!--\s*BEGIN BEEPOUT-PROVENANCE[^>]*-->\s*(\{.*?\})\s*<!--\s*END BEEPOUT-PROVENANCE\s*-->",
    re.S,
)


def _err(message: str, **extra: Any) -> Dict[str, Any]:
    return {"status": "error", "message": message, "correlation_id": "", **extra}


def _publish_intent_bus(mqtt_url: str, payload: dict) -> None:
    """One-shot publish of the open intent THROUGH THE COMPOSEBUS WAIST — the same
    framing seam ``hookup_review._publish_intent_bus`` and ``dsd_review`` use, for the
    same reason: a hand-framed envelope is a dialect no canonical decoder can read.

    ONE-SHOT, SO IT FLUSHES: this runs inside an MCP server process with no bus and no
    event loop, and may exit immediately after; ``bus.stop()`` runs the transport's
    ``loop_stop`` + ``disconnect``, which flushes paho's outbound queue. Without it a
    QoS-1 intent vanishes silently on a fast exit — which would reproduce the exact
    published-into-the-void symptom this namespace stayed mute to avoid."""
    from composeio import source_bus
    from composeio.namespace import Scope
    bus = source_bus.acquire_bus(mqtt_url, label=f"{SRC}-{os.getpid()}")
    source_bus.declare(bus, INTENT_NS, scope=Scope.GLOBAL, source=SRC,
                       label="beepout open intent")
    bus.publish_state(INTENT_NS, payload, source=SRC)
    bus.stop()
    source_bus.reset_for_tests()   # drop the memo: this process keeps no bus


def resolve_sheet(path: str) -> Optional[str]:
    """Repo containment, delegated. Returns an abspath or ``None``."""
    return _resolve_repo_file(path)


def read_provenance(text: str) -> Optional[Dict[str, Any]]:
    """Extract the machine-readable provenance block a generated sheet carries."""
    m = PROVENANCE_RE.search(text)
    if not m:
        return None
    try:
        payload = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def send_beepout_for_human_review(
    sheet_path: str,
    *,
    correlation_id: Optional[str] = None,
    mqtt_url: str = DEFAULT_MQTT,
    publish: Optional[Callable[[dict], None]] = None,
) -> Dict[str, Any]:
    """Validate a beep-out sheet and return a SELF-PROVING receipt.

    Every failure below returns cleanly and **publishes nothing**. The receipt names the
    absolute path, the netlist it is true of, that netlist's sha256, and the row counts by
    category and by derivation state — so accepting it never requires going and running a
    command (``[PRINCIPLE-SELF-PROVING-RECEIPT]``).
    """
    corr = correlation_id or f"beepout-{os.getpid()}"

    # 1. containment
    abspath = resolve_sheet(sheet_path)
    if not abspath:
        return _err(
            f"not a repo file: {sheet_path!r}. A beep-out sheet must live inside the "
            f"repository (root {REPO_ROOT}); the .kicad_pcb it is derived from is an "
            "external read-only source and is named by the sheet, not passed here."
        )
    if not os.path.isfile(abspath):
        return _err(f"no such file: {abspath}")

    text = Path(abspath).read_text(encoding="utf-8", errors="replace")

    # 2. is it a beep-out sheet at all?
    prov = read_provenance(text)
    if prov is None:
        return _err(
            f"{abspath} carries no BEEPOUT-PROVENANCE block, so it is not a generated "
            "beep-out sheet. ⛔ A hand-authored sheet is exactly what this endpoint "
            "refuses: a hand-typed pad pair is a claim, and only a netlist-derived one is "
            "a measurement. Generate it with "
            "`python -m composeio.maker.beepout.cli BOARD.kicad_pcb -o <sheet>.md`.",
            refused_not_derived=True,
        )

    netlist = prov.get("derived_from") or ""
    recorded_sha = prov.get("derived_from_sha256") or ""
    if not netlist or not recorded_sha:
        return _err(
            f"{abspath}'s provenance block is missing derived_from / derived_from_sha256.",
            refused_not_derived=True,
        )

    # 3. zero rows is not a sheet
    rows_total = int(prov.get("rows_total") or 0)
    if rows_total <= 0:
        return _err(
            f"{abspath} declares {rows_total} probe rows. ⛔ A sheet with no rows is not a "
            "sheet — it is a document that cannot fail, which is the one thing an "
            "instrument must be able to do.",
            refused_no_rows=True,
        )

    # 4. does the netlist still exist, and does it still hash to what the sheet claims?
    #    This ENFORCES the sync-contract that [PCB-CORPUS-PLACEMENT] records as a mere
    #    convention with its residual risk stated: "nothing runs shasum on a schedule
    #    today. An edit to the source will not announce itself." Here it does.
    if not os.path.isfile(netlist):
        return _err(
            f"the netlist this sheet names does not exist: {netlist}. The sheet cannot be "
            "shown to be true of any board.",
            refused_stale=True, netlist=netlist,
        )

    from composeio.maker.beepout import derive_sheet, parse_board  # lazy: keeps MCP bootable

    board = parse_board(netlist)
    if board.source_sha256 != recorded_sha:
        return _err(
            f"⛔ STALE SHEET. {abspath} was derived from {netlist} at sha256 "
            f"{recorded_sha[:16]}…, but that file now hashes to "
            f"{board.source_sha256[:16]}…. The board was revised and this sheet describes "
            "a different board. Regenerate it.",
            refused_stale=True,
            netlist=netlist,
            recorded_sha256=recorded_sha,
            actual_sha256=board.source_sha256,
        )

    # 5. re-derive and compare. A sheet whose row counts do not match a fresh derivation
    #    has been hand-edited, and a hand-edited row is precisely the claim this instrument
    #    exists to replace.
    fresh = derive_sheet(board)
    fresh_counts = fresh.by_category()
    recorded_counts = prov.get("rows_by_category") or {}
    if fresh_counts != recorded_counts:
        return _err(
            f"⛔ ROWS ARE NOT NETLIST-DERIVED. Re-deriving {netlist} yields "
            f"{fresh_counts}, but {abspath} records {recorded_counts}. Either the sheet "
            "was hand-edited or the generator changed; in both cases the rows on the page "
            "are no longer what the board file says.",
            refused_not_derived=True,
            netlist=netlist,
            recorded_rows_by_category=recorded_counts,
            derived_rows_by_category=fresh_counts,
        )

    # 6. PUBLISH THE OPEN INTENT. Every refusal above returned before this line, so the
    #    only thing that ever reaches the namespace is a sheet proven netlist-derived,
    #    non-empty and current. The provenance rides WITH the intent so a consumer can
    #    show what the sheet is true of without re-reading the sheet.
    board_name = str(prov.get("board") or "")
    intent = {
        # `path` is the doc-family alias, so the composeio DOC VIEWER opens this
        # unchanged; the rest are passengers a beep-out consumer can use.
        "path": abspath,
        "sheet": abspath,
        "netlist": netlist,
        "sha256": board.source_sha256,
        "netlist_sha256": board.source_sha256,
        "board": board_name,
        "rows_total": len(fresh.rows),
        "rows_by_category": fresh_counts,
        "rows_by_state": fresh.by_state(),
        "correlation_id": corr,
    }
    sink = publish or (lambda p: _publish_intent_bus(mqtt_url, p))
    receipt = {
        "path": abspath,
        "correlation_id": corr,
        "netlist": netlist,
        "netlist_sha256": board.source_sha256,
        "rows_total": len(fresh.rows),
        "rows_by_category": fresh_counts,
        "rows_by_state": fresh.by_state(),
    }
    try:
        sink(intent)
    except Exception as exc:                                   # noqa: BLE001
        # ⛔ THE SHEET IS VALID BUT IT WAS NOT DELIVERED, AND "sent" MAY NOT SAY
        # OTHERWISE. The receipt fields are kept so nothing verified is lost; the
        # status is honest so nothing unverified is claimed.
        return {"status": "error", **receipt, "intent_published": False,
                "message": (f"sheet VERIFIED netlist-derived, but the "
                            f"{INTENT_NS} publish FAILED: {exc}. The sheet at "
                            f"{abspath} is good; the viewer was not opened.")}

    return {
        "status": "sent",
        **receipt,
        "intent_published": True,
        "intent_ns": INTENT_NS,
        "message": (
            f"beep-out sheet VERIFIED netlist-derived and SENT: {abspath}\n"
            f"  derived from : {netlist}\n"
            f"  sha256       : {board.source_sha256}\n"
            f"  rows         : {len(fresh.rows)} {fresh_counts}\n"
            f"  states       : {fresh.by_state()}\n"
            f"  intent       : {INTENT_NS} (correlation {corr}) — the beep-out viewer "
            "launcher opens it in the composeio doc viewer.\n"
            "  ⚠ THE GLASS IS FOR READING. The doc viewer is READ-ONLY, so the ☐ column "
            "and the ACTUAL column are for a PEN — print the path above and take paper "
            "to the bench."
        ),
    }


def _selftest() -> int:
    """Fault-injected self-test. Returns 0 on success."""
    import tempfile

    fails = 0

    def check(cond: bool, label: str) -> None:
        nonlocal fails
        if not cond:
            fails += 1
            print(f"  FAIL {label}")

    # outside the repo publishes nothing
    sent: list = []
    r = send_beepout_for_human_review("/etc/hosts", publish=sent.append)
    check(r["status"] == "error", "outside-repo refused")
    check(not sent, "outside-repo published nothing")

    # a repo file with no provenance block is refused
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir=str(REPO_ROOT),
                                     delete=False) as fh:
        fh.write("# not a beep-out sheet\n")
        bogus = fh.name
    try:
        sent2: list = []
        r2 = send_beepout_for_human_review(bogus, publish=sent2.append)
        check(r2["status"] == "error", "no-provenance refused")
        check(r2.get("refused_not_derived") is True, "no-provenance flagged")
        check(not sent2, "no-provenance published nothing")
    finally:
        os.unlink(bogus)

    # a zero-row sheet publishes nothing (a document that cannot fail is not an
    # instrument, and it must not reach a bench)
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir=str(REPO_ROOT),
                                     delete=False) as fh:
        fh.write("<!-- BEGIN BEEPOUT-PROVENANCE -->\n"
                 '{"derived_from": "/nope.kicad_pcb", "derived_from_sha256": "ab",\n'
                 ' "rows_total": 0}\n'
                 "<!-- END BEEPOUT-PROVENANCE -->\n")
        empty = fh.name
    try:
        sent3: list = []
        r3 = send_beepout_for_human_review(empty, publish=sent3.append)
        check(r3["status"] == "error", "zero-row refused")
        check(r3.get("refused_no_rows") is True, "zero-row flagged")
        check(not sent3, "zero-row published nothing")
    finally:
        os.unlink(empty)

    # a sheet naming a netlist that does not exist publishes nothing
    with tempfile.NamedTemporaryFile("w", suffix=".md", dir=str(REPO_ROOT),
                                     delete=False) as fh:
        fh.write("<!-- BEGIN BEEPOUT-PROVENANCE -->\n"
                 '{"derived_from": "/definitely/not/here.kicad_pcb",\n'
                 ' "derived_from_sha256": "ab", "rows_total": 5}\n'
                 "<!-- END BEEPOUT-PROVENANCE -->\n")
        missing_nl = fh.name
    try:
        sent4: list = []
        r4 = send_beepout_for_human_review(missing_nl, publish=sent4.append)
        check(r4["status"] == "error", "missing-netlist refused")
        check(r4.get("refused_stale") is True, "missing-netlist flagged stale")
        check(not sent4, "missing-netlist published nothing")
    finally:
        os.unlink(missing_nl)

    # ⛔ THE POSITIVE CONTROL — a launcher/endpoint observed only REFUSING is as
    # unfalsifiable as one observed only succeeding. A real generated sheet whose
    # netlist is present and unmoved must publish EXACTLY ONE intent, and that intent
    # must carry the doc-family `path` alias the viewer opens on.
    #    ⛔ THE FIXTURE COMES FROM THIS MODULE'S OWN CHECKOUT ([CXC-HKR-SELFTEST-ROOT]),
    #    NOT from REPO_ROOT. REPO_ROOT is deliberately the CANONICAL checkout so a
    #    permanent record has one home; a selftest asks the OTHER question — which tree
    #    is the code I am running made of? Measured while writing this test: using
    #    REPO_ROOT made the positive control SKIP silently on a worktree whose sheets
    #    were present, and a skipped positive control is indistinguishable from a
    #    passing one in the output. `_allowed_dsd_roots` already admits this root, so
    #    containment is unchanged.
    from .dsd_review import own_checkout_root
    real_sheet = (own_checkout_root() / "docs" / "board-bringup"
                  / "rack_triple_gauge-BEEPOUT.md")
    if real_sheet.is_file() and os.path.isfile(
            (read_provenance(real_sheet.read_text(encoding="utf-8",
                                                  errors="replace")) or {})
            .get("derived_from") or ""):
        sent5: list = []
        r5 = send_beepout_for_human_review(str(real_sheet), publish=sent5.append)
        check(r5["status"] == "sent", f"real sheet sent (got {r5.get('message')!r})")
        check(len(sent5) == 1, "EXACTLY ONE intent published")
        if sent5:
            it = sent5[0]
            check(it.get("path") == str(real_sheet.resolve()),
                  "intent carries the doc-family `path` alias the viewer opens on")
            check(it.get("sha256") and it.get("netlist"),
                  "intent carries netlist + sha provenance")
            check(int(it.get("rows_total") or 0) > 0, "intent carries a row count")
        check(r5.get("intent_published") is True, "receipt reports the publish HAPPENED")
        check(r5.get("intent_ns") == INTENT_NS, "receipt names the namespace")
    else:
        print("  SKIP positive control: source netlist not present on this machine")

    # a publish that RAISES must never be reported as sent
    if real_sheet.is_file():
        def _boom(_p):
            raise RuntimeError("broker down")
        r6 = send_beepout_for_human_review(str(real_sheet), publish=_boom)
        if r6.get("rows_total"):      # only meaningful if the gates passed
            check(r6["status"] == "error", "publish failure is NOT reported as sent")
            check(r6.get("intent_published") is False,
                  "publish failure reports intent_published=False")

    print("beepout_review selftest:", "OK" if fails == 0 else f"{fails} FAILURES")
    return 1 if fails else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_selftest())
