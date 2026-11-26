#!/usr/bin/env python3
"""dsd_review_mcp — THIN COMPAT SHIM ([DD-DSDV-MCP-HOME] RESOLVED, D5).

The ``send_dsd_for_human_review`` endpoint has been RE-HOMED onto the one
decision-ops surface: it now lives in
``composeio.decision_ops.dsd_review`` and is registered as a tool on the
decision-ops MCP server (``composeio.decision_ops.mcp_server``), alongside
``decision_add`` / ``decision_update`` / ``decision_query`` / ``decision_resolve``.
Rationale (David, D5): a DSD *is* a decision-materials artifact — one coherent
surface, no extra server.

This module remains only as a thin compat shim so the historical CLI + any
by-path importer (e.g. the DSD-viewer test suite, which ``exec_module``s this
file and calls ``send_dsd_for_human_review`` / ``resolve_dsd``) keep working
unchanged.  It adds NO behavior — it re-exports the re-homed implementation.

Run:  python3 dsd_review_mcp.py docs/…/DECISIONS-foo.md --mqtt mqtt://host:1883
      python3 dsd_review_mcp.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "python"))
sys.path.insert(0, str(REPO_ROOT))

# Re-export the re-homed endpoint + its helpers so this shim is a drop-in for
# every historical caller (module attributes must stay: send_dsd_for_human_review,
# resolve_dsd — the test suite reads both off this module).
# `_parse_mqtt_url` is NO LONGER re-exported (2026-08-11): the re-homed module
# stopped carrying its own broker-URL parser when it moved onto the ComposeBus
# waist — `source_bus.acquire_bus` resolves the URL through the ONE
# implementation now ([DX-NARROW-WAIST]). Re-exporting a parser this shim's
# callers never used would only re-create the fork one indirection out.
from composeio.decision_ops.dsd_review import (  # noqa: E402,F401
    DEFAULT_MQTT,
    INTENT_NS,
    _is_a_dsd,
    _publish_intent_mqtt,
    _selftest,
    resolve_dsd,
    send_dsd_for_human_review,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="send_dsd_for_human_review — publish an _app/dsd/open intent "
                    "for a repo DSD so the human answers it in the DSD viewer. "
                    "(Re-homed onto the decision-ops MCP server; this is a shim.)")
    ap.add_argument("dsd_path", nargs="?", help="the DSD markdown path (repo-relative or abs)")
    ap.add_argument("--mqtt", default=DEFAULT_MQTT)
    ap.add_argument("--correlation-id", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.dsd_path:
        ap.error("dsd_path is required (or use --selftest)")
    result = send_dsd_for_human_review(a.dsd_path, mqtt_url=a.mqtt,
                                       correlation_id=a.correlation_id)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "sent" else 1


if __name__ == "__main__":
    raise SystemExit(main())
