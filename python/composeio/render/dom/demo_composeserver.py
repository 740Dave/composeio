"""Launch a REAL ComposeServer (2 tenants) with a kernel-level WS listener AND
serve the DOM demo page against it — the GLASS-test harness for David.

Unlike ``server.py`` (the in-memory DemoBus stand-in), this drives the ACTUAL
``ComposeServer.attach_ws_endpoint()`` seam ([SPEC:CXC-WS]): the control frames,
retained replay, and browser->bus publishes ride the production connector.

⛔ ONE SEAM NOTE: server-side config validation (``validate_config`` ->
``config_result``) is a DOM-LAYER verb the production WS listener does NOT serve
(it answers ``{ws:"error",code:"unknown_verb"}``). For the config-form glass
row, run ``python -m composeio.render.dom.server`` instead — that harness serves
the validation leg through the SHARED ``validate_config``. Everything else
(operator list, select/bound, subscribe, retained replay, publish) is faithful
here.

Run:  python -m composeio.render.dom.demo_composeserver
Then open the printed URL; the page connects to the live ComposeServer WS port.
"""
from __future__ import annotations

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from composeio.composeserver import ComposeServer

_DOM_DIR = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    host = os.environ.get("DOM_DEMO_HOST", "127.0.0.1")
    http_port = int(os.environ.get("DOM_DEMO_HTTP_PORT", "8900"))
    ws_port = int(os.environ.get("DOM_DEMO_WS_PORT", "8430"))

    srv = ComposeServer()
    srv.add_tenant("haven")
    srv.add_tenant("bsg")
    # a couple of retained values so the browser sees replay on connect.
    srv.tenant_bus("haven").publish_state("ship/speed", 12)
    srv.tenant_bus("bsg").publish_state("ship/speed", 7)
    conn = srv.attach_ws_endpoint(host=host, port=ws_port)

    handler = partial(SimpleHTTPRequestHandler, directory=_DOM_DIR)
    httpd = ThreadingHTTPServer((host, http_port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    url = (f"http://{host}:{http_port}/demo/demo.html"
           f"?ws={conn.bound_port}&tenant=haven")
    print(f"[dom-glass] REAL ComposeServer WS listener on "
          f"ws://{host}:{conn.bound_port}  (tenants: haven, bsg)")
    print(f"[dom-glass] open  {url}")
    print("[dom-glass] Ctrl-C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
