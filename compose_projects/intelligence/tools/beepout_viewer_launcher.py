#!/usr/bin/env python3
"""beepout_viewer_launcher — open a BEEP-OUT SHEET in the composeio doc viewer,
one window per sheet, on `_app/beepout/open`.

THE CONSUMER HALF of `composeio.decision_ops.beepout_review`.  It lands in the
SAME CHANGE as that endpoint's publish leg, which is the whole point:
`[CXC-BPO-MCP]`'s `scope-boundary` note held the publish MUTE precisely because
no party consumed the namespace, and publishing into a namespace nobody
subscribes reports success while doing nothing — the defect the DSD notify leg
already paid for at `_app/dsd/notice`.

⛔ THIS MODULE CONTAINS NO VIEWER.  David ruled it (2026-08-15): *"the viewer can
be the composeio doc viewer."*  A beep-out sheet is printable markdown, and
`markdown_viewer` ([CXC-MDV]) already renders exactly the block set the sheet
uses — ATX headings, pipe tables, bullets, blockquotes, rules.  So this launcher
REUSES `doc_viewer_launcher.DocViewerLauncher` WHOLESALE ([DX-NARROW-WAIST],
reuse by reference): the per-open scene build, the `spawned_windows` same-path
dedupe, the `[DEC-LAUNCH-PLACEMENT]` bbox placement, and the detached `runtime`
spawn are all ITS code, called, not copied.  Forking any of them would fork the
doc window's geometry key, its dedupe policy and its placement contract three
ways, which is the divergence `spawned_windows` was de-forked to end.

WHAT IS BEEP-OUT-SPECIFIC, AND IT IS ONLY THIS:

  THE NAMESPACE.  `_app/beepout/open`, a peer of `_app/doc/open` rather than a
  mode of it — the seam `[CXC-BPO-SEAM]` argues for the endpoint, held one layer
  down.  The endpoint's receipt is about a NETLIST; a generic doc-open intent
  has nowhere to carry that, and a consumer keyed on the sheet's namespace can
  say `beep-out sheet` in its log line where a doc consumer can only say `.md`.

  THE PROVENANCE PASSENGERS.  The intent carries `netlist`, `sha256` and the row
  counts.  They ride so a future consumer can use them WITHOUT re-reading the
  sheet; this launcher only logs them, and that is deliberate — it does not
  re-validate, because the endpoint is the gate and a second opinion sharing no
  code with the first is a fork wearing a gate's clothes.

⚠ TWO HONEST LIMITS OF THE DOC VIEWER ON THIS ARTIFACT, NAMED NOT HIDDEN:

  ROWS CANNOT BE TICKED ON GLASS.  `markdown_viewer` is READ-ONLY BY
  CONSTRUCTION — no caret, no text entry, no write path; the only interaction is
  vertical scroll.  The sheet's `☐` column and its `____________` ACTUAL column
  are for a PEN.  This is the same limit that made the read-only viewer
  unsuitable for DSD fill-in slots, and it is accepted here rather than worked
  around, because a bench human has a probe in each hand and no third hand for a
  mouse.

  THE WINDOW CANNOT PRINT.  The ComposeUX runtime has no print path, so the glass
  is for READING and REVIEW; paper comes from the `.md` by any ordinary route.
  Printability is a property of the SHEET, which is markdown, and it is not lost
  by this window existing.

Run:  python3 beepout_viewer_launcher.py --mqtt mqtt://mqtt-broker.local:1883
      python3 beepout_viewer_launcher.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))
# tools dir on sys.path so the sibling import resolves in EVERY entry mode
# (run-as-script, bare import from data_sources, exec_module-by-path) — the
# doc/hookup launcher idiom, mirrored because the failure it prevents is silent.
sys.path.insert(0, str(Path(__file__).resolve().parent))

# ⛔ THE VIEWER, IMPORTED — NOT REIMPLEMENTED. Every window behaviour below is
# doc_viewer_launcher's; this module supplies a namespace and a payload shape.
import doc_viewer_launcher as dvl  # noqa: E402

from composeio import source_bus                              # noqa: E402
from composeio.app_topics import console_bbox_ns              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

INTENT_NS = "_app/beepout/open"
SRC = "beepout-viewer-launcher"    # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
# [CXC-FC-S4-BBOX] DERIVED, never a literal: the console's bbox topic is built
# from its scene's `app: id:`, so a launcher hardcoding the old literal would
# subscribe a topic NOBODY publishes to — a silent placement regression.
BBOX_NS = console_bbox_ns()

# open intents that mean "close" — the window model has nothing to close.
_FALSY = {"", "off", "none", "false", "0", "close"}


# ---------------------------------------------------------------------------
# intent parsing (the doc/dsd/hookup family shape + the provenance passengers)
# ---------------------------------------------------------------------------
def parse_intent(payload: Any) -> Optional[Dict[str, Any]]:
    """The `{path, netlist, sha256, correlation_id, ...}` in an
    `_app/beepout/open` intent, or None.

    Accepts the bare envelope, a nested ``val`` (the bus wraps a bare-string
    publish as ``{val: "<path>"}``), a bare string, or the full dict — the same
    four shapes its three sibling launchers accept, so a HAND-PUBLISHED intent
    (the way a human tests this leg) does not have to know the record shape."""
    d = payload
    if isinstance(d, dict) and "val" in d and isinstance(d.get("val"), (dict, str)):
        d = d["val"]
    path: Optional[str] = None
    extra: Dict[str, Any] = {}
    if isinstance(d, str):
        path = d
    elif isinstance(d, dict):
        p = d.get("path") or d.get("sheet") or d.get("doc")
        path = str(p) if p else None
        extra = {
            "correlation_id": str(d.get("correlation_id") or d.get("corr") or ""),
            "netlist": str(d.get("netlist") or ""),
            "sha256": str(d.get("sha256") or d.get("netlist_sha256") or ""),
            "board": str(d.get("board") or ""),
            "rows_total": _int_or_zero(d.get("rows_total")),
        }
    if path is None:
        return None
    text = path.strip()
    if text == "" or text.lower() in _FALSY:
        return None
    out: Dict[str, Any] = {"path": text, "correlation_id": "", "netlist": "",
                           "sha256": "", "board": "", "rows_total": 0}
    out.update(extra)
    return out


def _int_or_zero(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def describe(intent: Dict[str, Any]) -> str:
    """A one-line provenance tail for the log — the sheet is only meaningful
    together with the board it is true of, so the log line carries both."""
    bits: List[str] = []
    if intent.get("board"):
        bits.append(str(intent["board"]))
    if intent.get("rows_total"):
        bits.append(f"{intent['rows_total']} rows")
    if intent.get("sha256"):
        bits.append(f"netlist {str(intent['sha256'])[:12]}…")
    if intent.get("correlation_id"):
        bits.append(str(intent["correlation_id"]))
    return ("  [" + " · ".join(bits) + "]") if bits else ""


# ---------------------------------------------------------------------------
# the bus leg — a subscriber on `_app/*`
# ---------------------------------------------------------------------------
def run(mqtt_url: str, *, bus=None, stop=None) -> int:
    """[SPEC:CXC-SOURCES-ON-BUS] — a subscribing source, no publish leg.

    REPEAT DELIVERY IS REQUIRED, so the subscription is `subscribe_pattern`
    ([CXC-SOB-SUBSCRIBE]) and NOT `bus.subscribe(INTENT_NS, ...)`: re-sending the
    SAME sheet after a board revision is the NORMAL case here — that is exactly
    what the staleness gate makes people do — and `Namespace.publish_state`
    notifies EXACT subscribers only when `new != old`, so an exact subscribe
    would swallow the second send with no error at all."""
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("beepout-viewer-launcher")
    if lock is None:
        sys.stderr.write("[beepout_viewer_launcher] another instance holds the "
                         "singleton; yielding\n")
        return 0

    b = source_bus.acquire_bus(mqtt_url, label=SRC, bus=bus)
    for _ns in (INTENT_NS, BBOX_NS):
        source_bus.declare(b, _ns, scope=Scope.GLOBAL, source=SRC,
                           label="app intent")
    source_bus.manifest(SRC)

    # THE DOC VIEWER, INSTANTIATED — not a subclass, not a copy.
    launcher = dvl.DocViewerLauncher()

    def _on_app(ns: str, value, _old) -> None:
        if ns == BBOX_NS:
            launcher.set_console_bbox(dvl.parse_bbox(value))
            return
        if ns != INTENT_NS:
            return                     # another `_app/*` member; not ours
        intent = parse_intent(value)
        if not intent:
            return
        sys.stderr.write("[beepout_viewer_launcher] open -> "
                         + launcher.open(intent["path"])
                         + describe(intent) + "\n")
        sys.stderr.flush()

    b.subscribe_pattern("_app/*", _on_app)
    sys.stderr.write(f"[beepout_viewer_launcher] listening _app/* (filtering "
                     f"{INTENT_NS} + {BBOX_NS})\n")
    sys.stderr.flush()
    source_bus.park(stop)
    return 0


# ---------------------------------------------------------------------------
# selftest — logic only, no broker, no window, mocked spawn
# ---------------------------------------------------------------------------
def _selftest() -> int:
    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    # ---- intent parsing: all four accepted shapes -------------------------
    i = parse_intent("docs/board-bringup/lego_dots-BEEPOUT.md")
    check(i and i["path"] == "docs/board-bringup/lego_dots-BEEPOUT.md",
          "(1) bare string")
    i = parse_intent({"path": "a.md", "correlation_id": "c1", "netlist": "/b.kicad_pcb",
                      "sha256": "deadbeef" * 8, "board": "lego_dots",
                      "rows_total": 131})
    check(i and i["correlation_id"] == "c1" and i["netlist"] == "/b.kicad_pcb"
          and i["board"] == "lego_dots" and i["rows_total"] == 131,
          "(2) full dict carries the provenance passengers")
    check((parse_intent({"val": {"path": "b.md"}}) or {}).get("path") == "b.md",
          "(3) nested val envelope (the bus's bare-string wrapper)")
    check((parse_intent({"sheet": "c.md"}) or {}).get("path") == "c.md",
          "(4) `sheet` alias")
    check(parse_intent("") is None and parse_intent("close") is None
          and parse_intent({"nope": 1}) is None and parse_intent(None) is None,
          "(5) empty/close/no-path -> None")
    check((parse_intent({"path": "a.md", "netlist_sha256": "ab"}) or {})["sha256"] == "ab",
          "(6) `netlist_sha256` alias (the receipt's own key name)")
    check((parse_intent({"path": "a.md", "rows_total": "oops"}) or {})["rows_total"] == 0,
          "(7) a non-numeric rows_total degrades to 0, never raises")

    # ---- the path guard IS the doc viewer's, not a second one -------------
    check(dvl.resolve_doc("docs/board-bringup/RUNBOOK.md") is not None,
          "(8) a real repo doc resolves")
    check(dvl.resolve_doc("/etc/passwd") is None, "(9) outside-repo refused")
    check(dvl.resolve_doc("../../../../etc/hosts") is None, "(10) traversal refused")
    check(dvl.resolve_doc("docs/board-bringup/NOPE-BEEPOUT.md") is None,
          "(11) non-existent sheet refused")

    # ---- NEGATIVE CONTROL: the launcher must NOT act on a bad path --------
    # An instrument that can only report success is not one ([CXC-VISION-EPISTEMICS]).
    class FakeProc:
        def __init__(self):
            self.pid = 4242
            self._alive = True

        def poll(self):
            return None if self._alive else 0

    spawned: List[str] = []
    lau = dvl.DocViewerLauncher(spawn=lambda s: (spawned.append(s), FakeProc())[1])

    real = "docs/board-bringup/RUNBOOK.md"
    r_bad = lau.open("docs/board-bringup/DOES-NOT-EXIST-BEEPOUT.md")
    check(r_bad.startswith("refused") and len(spawned) == 0,
          "(12) NC: non-existent sheet -> refused, ZERO spawns")
    r_out = lau.open("/etc/passwd")
    check(r_out.startswith("refused") and len(spawned) == 0,
          "(13) NC: outside-repo path -> refused, ZERO spawns")
    r_trav = lau.open("../../../../etc/hosts")
    check(r_trav.startswith("refused") and len(spawned) == 0,
          "(14) NC: traversal -> refused, ZERO spawns")

    # ---- POSITIVE: a real sheet DOES spawn, exactly once ------------------
    r_ok = lau.open(real)
    check(r_ok.startswith("opened") and len(spawned) == 1,
          "(15) POS: a real repo doc spawns exactly one window")
    r_dup = lau.open(real)
    check("already open" in r_dup and len(spawned) == 1,
          "(16) same-path dedupe (the doc viewer's own policy, not a copy)")
    lau._windows.live[dvl.resolve_doc(real)]._alive = False
    r_re = lau.open(real)
    check(r_re.startswith("opened") and len(spawned) == 2,
          "(17) reopen after close spawns fresh")

    # ---- the window it spawns really is the DOC VIEWER --------------------
    import yaml
    written = yaml.safe_load(Path(spawned[0]).read_text(encoding="utf-8"))
    check(written.get("app", {}).get("id") == "doc-viewer",
          "(18) the spawned scene IS the doc viewer app, not a fork")
    check(any(w.get("type") == "stack" for w in written.get("widgets", [])),
          "(19) the canonical docviewer frame survived the variable bake")
    check(written.get("variables", {}).get("doc_path", "").endswith("RUNBOOK.md"),
          "(20) the sheet path is baked into THIS window's variables")

    # ---- the log tail carries provenance ----------------------------------
    tail = describe({"board": "lego_dots", "rows_total": 131,
                     "sha256": "abcdef0123456789", "correlation_id": "c9"})
    check("lego_dots" in tail and "131 rows" in tail and "abcdef012345" in tail
          and "c9" in tail, "(21) log tail names board, rows, netlist and corr")
    check(describe({"path": "x.md"}) == "",
          "(22) a bare intent logs no empty provenance brackets")

    # ---- the namespace is the beep-out one, and it is NOT the doc one -----
    check(INTENT_NS == "_app/beepout/open", "(23) intent namespace")
    check(INTENT_NS != dvl.INTENT_NS,
          "(24) a PEER of _app/doc/open, never a mode of it")

    if fails:
        for f in fails:
            print("FAIL:", f)
        return 1
    print("PASS: beepout_viewer_launcher selftest (24/24)")
    return 0


def main(argv=None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(
        description="Open a beep-out sheet in the composeio doc viewer per "
                    "_app/beepout/open intent.")
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(a.mqtt, bus=bus, stop=stop)


if __name__ == "__main__":
    raise SystemExit(main())
