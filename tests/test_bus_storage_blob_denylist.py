"""Bus storage — blob-route deny-list. [SPEC:CXC-STORE] (David ruled 2026-09-03)

The unified store's generic blob route (`/api/v1/blob/<path>`) is rooted at
`BLOB_ROOT == DATA_DIR`, so WITHOUT a deny-list it reaches the WHOLE data dir —
including two trees that have dedicated, narrower routes and must NOT be a second
door through the generic bytes-by-path face:

  * `configs/`              — writable ONLY through the dedicated /api/v1/config
                              route, which maintains the `.meta.json` checksum
                              sidecar. A blob PUT to `configs/<id>.bin` would write
                              a device config BYPASSING that checksum.
  * `assets/fleet/private/` — the fleet's private credential tree (a real
                              `velo.creds` exists on the live host).

THE DENY-LIST IS THE GUARD; THE NEGATIVE CONTROLS ARE THE PROOF.
A guard that can only report success is not a guard. Every deny case below is
paired with the corresponding ALLOW (a legit blob op still round-trips; the
dedicated config + asset routes still work), so a deny that fires because the
whole route broke is caught, not mistaken for a pass.

Runs a REAL `services/bus_storage/server.py` on 127.0.0.1 + a scratch data dir +
a free port. Never :8420, never the live host — a security test that probes the
live store in the write form is how a test becomes an outage. Executable directly
(`python3 tests/test_bus_storage_blob_denylist.py`) or under pytest.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "services" / "bus_storage" / "server.py"
HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


class _Server:
    """Launch bus_storage on a scratch data dir + free port; tear down on exit."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.port = _free_port()
        self.base = f"http://{HOST}:{self.port}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        env = dict(os.environ, COMPOSEIO_HOST=HOST, COMPOSEIO_PORT=str(self.port),
                   COMPOSEIO_DATA_DIR=str(self.data_dir))
        self.proc = subprocess.Popen([sys.executable, str(SERVER)], env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{self.base}/health", timeout=1) as r:
                    if r.read() == b"ok":
                        break
            except Exception:
                time.sleep(0.05)
        else:
            raise RuntimeError("bus_storage did not come up")
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()

    def raw(self, method: str, path: str, body: bytes | None = None):
        """Issue a request via http.client with a LITERAL path so `..` is not
        collapsed client-side (urllib would neuter the traversal case)."""
        conn = http.client.HTTPConnection(HOST, self.port, timeout=5)
        try:
            conn.request(method, path, body=body)
            resp = conn.getresponse()
            return resp.status, resp.read()
        except (http.client.RemoteDisconnected, http.client.BadStatusLine,
                ConnectionResetError):
            return None, b""
        finally:
            conn.close()


def _mk_tmp(tmp_path, name: str) -> Path:
    if tmp_path is not None:
        d = Path(tmp_path) / name
        d.mkdir(parents=True, exist_ok=True)
        return d
    import tempfile
    return Path(tempfile.mkdtemp(prefix=f"cxcdeny_{name}_"))


# The load-bearing report: (label, method, blob-path, expect-status).
_DENY = "must be REFUSED 403 on the blob route (protected tree)"


def test_blob_denylist(tmp_path=None):
    data = _mk_tmp(tmp_path, "deny")
    with _Server(data) as srv:
        results: list[tuple[str, int | None]] = []

        def check(label, method, path, want, body=None):
            st, _ = srv.raw(method, path, body)
            results.append((f"{label:<34} {method:<6} {path}", st))
            assert st == want, f"{label}: {method} {path} -> {st}, expected {want}"

        # --- seed a real config through the DEDICATED route (with sidecar) ------
        st, _ = srv.raw("PUT", "/api/v1/config/velo-panel", b"\xc1\x00cfg")
        assert st == 201
        # --- seed a private creds file directly on disk (simulating velo.creds) --
        priv = data / "assets" / "fleet" / "private"
        priv.mkdir(parents=True, exist_ok=True)
        (priv / "velo.creds").write_bytes(b"SUPER-SECRET-CREDS")

        # (a) blob route may not GET/PUT/DELETE under configs/ -------------------
        check("(a) blob configs GET", "GET", "/api/v1/blob/configs/velo-panel.bin", 403)
        check("(a) blob configs PUT", "PUT", "/api/v1/blob/configs/velo-panel.bin", 403, b"pwn")
        check("(a) blob configs DELETE", "DELETE", "/api/v1/blob/configs/velo-panel.bin", 403)

        # (b) blob route may not GET/PUT/DELETE under assets/fleet/private/ ------
        check("(b) blob private GET", "GET",
              "/api/v1/blob/assets/fleet/private/velo.creds", 403)
        check("(b) blob private PUT", "PUT",
              "/api/v1/blob/assets/fleet/private/x.creds", 403, b"pwn")
        check("(b) blob private DELETE", "DELETE",
              "/api/v1/blob/assets/fleet/private/velo.creds", 403)

        # (c) a ../ escape from an ALLOWED prefix into configs/ is caught too ----
        #     resolves inside BLOB_ROOT (containment PASSES) then the deny-list fires.
        check("(c) ../ escape into configs", "GET",
              "/api/v1/blob/apps/../configs/velo-panel.bin", 403)
        check("(c) ../ escape into private", "PUT",
              "/api/v1/blob/apps/../assets/fleet/private/x.creds", 403, b"pwn")

        # (d) a LEGIT blob op round-trips (positive control) --------------------
        check("(d) legit blob PUT", "PUT", "/api/v1/blob/apps/y.json", 201, b'{"ok":1}')
        check("(d) legit blob GET", "GET", "/api/v1/blob/apps/y.json", 200)
        st, body = srv.raw("GET", "/api/v1/blob/apps/y.json")
        assert body == b'{"ok":1}', body
        check("(d) legit blob DELETE", "DELETE", "/api/v1/blob/apps/y.json", 200)

        # (e) the DEDICATED config route STILL writes configs/ WITH its sidecar --
        conn = http.client.HTTPConnection(HOST, srv.port, timeout=5)
        conn.request("PUT", "/api/v1/config/velo-panel", body=b"\xc1\x00cfg2",
                     headers={"X-ComposeIO-Metadata": '{"crc":"deadbeef"}'})
        assert conn.getresponse().status == 201
        conn.close()
        check("(e) config route GET", "GET", "/api/v1/config/velo-panel", 200)
        check("(e) config route checksum", "GET", "/api/v1/config/velo-panel/checksum", 200)
        assert (data / "configs" / "velo-panel.bin").exists()
        assert (data / "configs" / "velo-panel.meta.json").read_text() == '{"crc":"deadbeef"}', \
            "the dedicated config route must still write its .meta.json checksum sidecar"

        # (f) the ASSET route STILL serves assets/... (fleet-facing path intact) -
        check("(f) asset route PUT", "PUT", "/api/v1/assets/album/x.jpg", 201, b"JPEG")
        check("(f) asset route GET", "GET", "/api/v1/assets/album/x.jpg", 200)

        # LIST: a prefix INTO a protected tree is refused; protected children are
        # filtered from a root-level blob listing so it never even enumerates them.
        check("(list) blob configs prefix", "GET", "/api/v1/blob?prefix=configs", 403)
        srv.raw("PUT", "/api/v1/blob/apps/z.json", b"{}")
        st, body = srv.raw("GET", "/api/v1/blob?prefix=")
        names = {e["name"] for e in json.loads(body)["entries"]}
        results.append(("(list) root blob listing names", st))
        assert st == 200 and "configs" not in names, \
            f"configs/ must be filtered from the root blob listing; got {names}"
        # ...but the private creds file must never leak via a deep prefix either.
        st, body = srv.raw("GET", "/api/v1/blob?prefix=assets/fleet/private")
        assert st == 403, f"private prefix listing -> {st}, expected 403"

        # The creds file the deny-list protects must be UNTOUCHED after all writes.
        assert (priv / "velo.creds").read_bytes() == b"SUPER-SECRET-CREDS", \
            "a blob write escaped into the private tree"

        print("\nBLOB DENY-LIST — live status codes (127.0.0.1 scratch store):")
        for label, st in results:
            print(f"  {st}  {label}")
        print("PASS blob deny-list negative controls + positive controls")


if __name__ == "__main__":
    test_blob_denylist()
    print("\nALL blob-deny-list checks PASS")
