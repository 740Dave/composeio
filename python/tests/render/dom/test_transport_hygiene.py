"""DOM-V12 — this backend rides the WS connector; the legacy SSE bridge stays
read-only and unextended ([CXC-DOM-TRANSPORT]).

The static/dependency scan [CXC-DOM-TRANSPORT]'s verify-machine row asks for:
this backend's JS source contains no ``EventSource`` reference and no fetch/POST
standing in for a publish — every publish goes over the open WS connection.
"""

from __future__ import annotations

import os
import re

_STATIC = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                       "composeio", "render", "dom", "static")


def _strip_comments(src: str) -> str:
    """Remove JS comments so a hygiene scan matches CODE, not the prose that
    documents each prohibition (this file's own backend names the forbidden
    APIs in comments precisely to forbid them)."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)     # block comments
    src = re.sub(r"(?<!:)//.*", "", src)                     # line comments (keep ://)
    return src


def _sources():
    for fn in sorted(os.listdir(_STATIC)):
        if fn.endswith(".js"):
            with open(os.path.join(_STATIC, fn), encoding="utf-8") as f:
                yield fn, _strip_comments(f.read())


def test_no_eventsource_or_sse_route():
    hits = []
    for fn, src in _sources():
        for needle in ("EventSource", "/events", "text/event-stream"):
            if needle in src:
                hits.append(f"{fn}:{needle}")
    assert hits == [], f"DOM backend must not reference the SSE bridge: {hits}"


def test_publishes_go_over_ws_not_http():
    """No fetch()/XMLHttpRequest/POST side-channel standing in for a publish —
    the write leg is the WS connection ([CXC-DOM-TRANSPORT] why-ws-not-sse)."""
    hits = []
    for fn, src in _sources():
        for needle in ("fetch(", "XMLHttpRequest", "navigator.sendBeacon"):
            if needle in src:
                hits.append(f"{fn}:{needle}")
    assert hits == [], f"DOM backend must publish over WS, not HTTP: {hits}"
