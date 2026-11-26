"""Demo WS + static server for the ComposeUX DOM backend GLASS test
([SPEC:CXC-DOM], [CXC-DOM-ACCEPTANCE] verify-human gates).

⚠ THIS IS A GLASS HARNESS, NOT THE PRODUCTION TRANSPORT.  The real bus transport
is the WS connector specified at [SPEC:CXC-WS] (built in parallel); this module
is a minimal stand-in so a real browser can load the demo page, connect, bind,
subscribe, publish, and — the point — submit a config form that is validated
SERVER-SIDE by the REAL pydantic evaluator (validation.py, reusing
[CXC-CSL-CONFIG-VALIDATION]).  It speaks:

  * bus traffic as the FAITHFUL BusMessage envelope ([CXC-WS-WIRE-FORMAT]):
    {ns, type, src, ts, scope, seq, val?} — retained-store + broadcast.
  * lifecycle/control frames with the CANONICAL {ws: <verb>} discriminator that
    transport_ws.py (the AUTHORITATIVE [SPEC:CXC-WS] connector) uses — reconciled
    2026-08-25: {ws:"list"}->{ws:"tenants"}, {ws:"select",tenant}->{ws:"bound"},
    {ws:"subscribe",patterns:[...]}->{ws:"subscribed"} (+ retained replay).

⚠ validate_config -> config_result is a DOM-LAYER extension, NOT a transport_ws.py
verb (the real ComposeServer WS listener returns {ws:"error",code:"unknown_verb"}
for it). It rides the SAME {ws:...} discriminator + req_id here so ws_client.js
speaks ONE vocabulary against both this demo and the real listener; only this
demo actually SERVES the config-validation leg.

Run:  python -m composeio.render.dom.server
Then open the printed http://localhost:8900/demo/demo.html in a browser.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

try:
    import websockets
except Exception as exc:  # pragma: no cover
    raise ImportError("the DOM demo server needs the 'websockets' package") from exc

from .validation import validate_config_result

_DOM_DIR = os.path.dirname(os.path.abspath(__file__))


# --- a sample plugin Config the demo form targets ([CXC-CSL-CONFIG-VALIDATION])
class DemoServiceConfig(BaseModel):
    """Stands in for a real plugin's ``Config`` — validated by the SAME
    pydantic call the loader uses."""
    name: str = Field(min_length=1)
    port: int = Field(gt=0, lt=65536)
    mode: str = "auto"          # auto | ha | manual
    replicas: Optional[int] = None
    notes: str = ""

    @model_validator(mode="after")
    def _cross_field(self):
        if self.mode not in ("auto", "ha", "manual"):
            raise ValueError("mode must be one of auto/ha/manual")
        if self.mode == "ha" and (self.replicas or 0) < 2:
            raise ValueError("mode 'ha' requires replicas >= 2")
        return self


CONFIG_REGISTRY = {"demo": DemoServiceConfig}


def _matches(pattern: str, ns: str) -> bool:
    if pattern in (ns, "*", "#"):
        return True
    if pattern.endswith("/*") or pattern.endswith("/#"):
        return ns.startswith(pattern[:-1])
    return False


# The demo serves a small cross-tenant operator list ([CXC-WS-ENTRY-OPERATOR]);
# the single shared retained store makes tenant selection cosmetic here, but the
# control exchange matches the real transport's shape exactly.
DEMO_TENANTS = ["demo", "bsg"]


class DemoBus:
    """An in-memory retained bus shared by every connected peer."""

    def __init__(self) -> None:
        self.retained: Dict[str, Any] = {}
        self.peers: "set[Peer]" = set()

    async def publish(self, msg: dict, origin: "Peer") -> None:
        ns = msg.get("ns")
        if ns is None:
            return
        self.retained[ns] = msg.get("val")
        for peer in list(self.peers):
            if peer.bound and any(_matches(p, ns) for p in peer.subs):
                await peer.send(msg)


class Peer:
    def __init__(self, ws, bus: DemoBus) -> None:
        self.ws = ws
        self.bus = bus
        self.bound = False
        self.tenant: Optional[str] = None
        self.subs: List[str] = []

    async def send(self, obj: dict) -> None:
        await self.ws.send(json.dumps(obj))

    async def handle(self, raw: str) -> None:
        try:
            d = json.loads(raw)
        except Exception:
            return
        verb = d.get("ws")
        if verb is None and isinstance(d.get("ns"), str):
            await self.bus.publish(d, self)          # a bus frame
            return
        if verb == "list":
            await self.send({"ws": "tenants", "tenants": list(DEMO_TENANTS)})
            return
        if verb in ("select", "switch"):
            self.tenant = d.get("tenant")
            self.bound = True
            await self.send({"ws": "bound", "tenant": self.tenant})
            # BOUND-entry replay of the whole declared set ([CXC-WS-RETAINED-REPLAY]).
            await self._replay(self.subs)
            return
        if verb == "subscribe":
            # the FULL declared set REPLACES the old one (transport_ws semantics).
            patterns = list(d.get("patterns", []))
            newly = [p for p in patterns if p not in self.subs]
            self.subs = patterns
            await self.send({"ws": "subscribed", "patterns": list(patterns),
                             "enforcement": "broker"})
            if self.bound and newly:
                await self._replay(newly)
            return
        if verb == "validate_config":
            # DOM-LAYER extension (NOT a transport_ws.py verb) — served here only.
            target = d.get("target")
            config = d.get("config", {})
            model = CONFIG_REGISTRY.get(target)
            if model is None:
                res = {"ok": False, "errors": [{"field": None, "msg": f"unknown target {target!r}"}], "unmet": []}
            else:
                res = validate_config_result(model, config)
            await self.send(dict(res, ws="config_result", req_id=d.get("req_id")))
            return
        # An unknown control verb is a LOUD in-band error, mirroring the real
        # transport's WSPeer ([CXC-WS-LIFECYCLE]).
        await self.send({"ws": "error", "code": "unknown_verb",
                         "message": f"unknown control verb {verb!r}"})

    async def _replay(self, patterns: List[str]) -> None:
        for ns, val in self.bus.retained.items():
            if any(_matches(p, ns) for p in patterns):
                await self.send({"ns": ns, "type": "state", "src": "server",
                                 "ts": 0, "scope": "auto", "seq": 0,
                                 "val": val, "retained": True})


async def _ws_main(host: str, port: int) -> None:
    bus = DemoBus()

    async def handler(ws):
        peer = Peer(ws, bus)
        bus.peers.add(peer)
        # operator entry: serve the cross-tenant list, stay PRE-BINDING
        # ([CXC-WS-ENTRY-OPERATOR]) — same as transport_ws.py's WSConnector.
        await peer.send({"ws": "tenants", "tenants": list(DEMO_TENANTS)})
        try:
            async for raw in ws:
                await peer.handle(raw)
        finally:
            bus.peers.discard(peer)

    async with websockets.serve(handler, host, port):
        print(f"[dom-demo] WS bus on ws://{host}:{port}")
        await asyncio.Future()   # run forever


def _serve_static(host: str, port: int) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=_DOM_DIR)
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"[dom-demo] static on http://{host}:{port}/demo/demo.html")
    httpd.serve_forever()


def main() -> int:
    host = os.environ.get("DOM_DEMO_HOST", "127.0.0.1")
    http_port = int(os.environ.get("DOM_DEMO_HTTP_PORT", "8900"))
    ws_port = int(os.environ.get("DOM_DEMO_WS_PORT", "8901"))
    t = threading.Thread(target=_serve_static, args=(host, http_port), daemon=True)
    t.start()
    print("[dom-demo] GLASS harness — NOT the production [SPEC:CXC-WS] transport.")
    print(f"[dom-demo] open  http://{host}:{http_port}/demo/demo.html  "
          f"(it connects to ws://{host}:{ws_port})")
    try:
        asyncio.run(_ws_main(host, ws_port))
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
