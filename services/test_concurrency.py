#!/usr/bin/env python3
"""Concurrency regression tests for the three ComposeIO HTTP services.

Each service was converted from a single-threaded http.server.HTTPServer to
ThreadingHTTPServer with a thread-safe storage backend. These tests launch each
service as a subprocess on a loopback-only test port with a temp data dir, then:

  1. Fire N parallel requests and assert they do NOT serialize.
  2. THE KEY REGRESSION: open one deliberately WEDGED request (announces a large
     Content-Length then never sends the body) and assert OTHER requests still
     complete promptly in parallel. Before the fix this hung the whole service.
  3. Per-service data-integrity checks under concurrency (no torn reads, no
     SQLite cross-thread errors, unique ids).

Stdlib only. Run:  python3 services/test_concurrency.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOST = "127.0.0.1"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_for_port(port: int, timeout: float = 15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"server never came up on {port}")


class HttpError(Exception):
    def __init__(self, status, body):
        super().__init__(f"HTTP {status}: {body[:200]!r}")
        self.status = status
        self.body = body


def _req(method: str, url: str, body: bytes | None = None, headers: dict | None = None,
         timeout: float = 5.0):
    """Minimal raw-socket HTTP/1.1 client.

    Sends the ENTIRE request (line + headers + body) in a single TCP segment with
    TCP_NODELAY set, avoiding the urllib/loopback Nagle + delayed-ACK stall that
    would otherwise dominate timings and hide real server concurrency behavior.
    """
    assert url.startswith("http://")
    hostport, _, path = url[len("http://"):].partition("/")
    path = "/" + path
    host, _, port_s = hostport.partition(":")
    port = int(port_s)
    hdrs = dict(headers or {})
    hdrs.setdefault("Host", host)
    hdrs["Connection"] = "close"
    hdrs["Content-Length"] = str(len(body) if body is not None else 0)
    raw = f"{method} {path} HTTP/1.1\r\n"
    raw += "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
    raw += "\r\n"
    data = raw.encode() + (body or b"")

    s = socket.create_connection((host, port), timeout=timeout)
    try:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        s.settimeout(timeout)
        s.sendall(data)
        buf = bytearray()
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
    finally:
        s.close()

    head, _, resp_body = bytes(buf).partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    status = int(status_line.split(" ", 2)[1])
    if status >= 400:
        raise HttpError(status, resp_body)
    return status, resp_body


def _open_wedged_connection(port: int, request_line_and_headers: bytes) -> socket.socket:
    """Open a socket, send request line + headers announcing a big body, send NO
    body, and hold the connection open. Occupies exactly one request thread."""
    s = socket.create_connection((HOST, port), timeout=5)
    s.sendall(request_line_and_headers)
    # Send a single byte of the promised body then stall forever (until closed).
    s.sendall(b"x")
    return s


class Service:
    def __init__(self, name, script, env, port):
        self.name = name
        self.script = script
        self.env = env
        self.port = port
        self.proc = None

    def __enter__(self):
        env = dict(os.environ)
        env.update(self.env)
        # Log to a real file, NOT subprocess.PIPE: these services log one line per
        # request, and thousands of them would fill the 64KB pipe buffer and block
        # the server on its next write (a test artifact, not a service bug).
        self._log = tempfile.NamedTemporaryFile(
            mode="w+", prefix=f"{self.name}_", suffix=".log", delete=False)
        self.proc = subprocess.Popen(
            [sys.executable, str(self.script)],
            env=env, stdout=self._log, stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_port(self.port)
        except Exception:
            self.dump_log()
            raise
        return self

    def dump_log(self):
        try:
            self._log.flush()
            with open(self._log.name) as f:
                tail = f.read()[-2000:]
            print(f"--- {self.name} server log tail ---\n{tail}")
        except Exception:  # noqa
            pass

    def __exit__(self, exc_type, *a):
        if exc_type is not None:
            self.dump_log()
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def _wedged_test(name, port, wedge_bytes, probe_fn, n=20):
    """Open a wedged connection, then fire n parallel probes; assert all complete
    quickly. probe_fn(i) performs one normal request and returns on success."""
    wedged = _open_wedged_connection(port, wedge_bytes)
    try:
        t0 = time.time()
        errors = []
        with ThreadPoolExecutor(max_workers=n) as ex:
            futs = [ex.submit(probe_fn, i) for i in range(n)]
            for f in as_completed(futs, timeout=10):
                try:
                    f.result()
                except Exception as e:  # noqa
                    errors.append(repr(e))
        elapsed = time.time() - t0
        assert not errors, f"{name}: probes failed while a request was wedged: {errors[:3]}"
        # n fast probes behind a wedged connection must finish in well under the
        # stall time. Single-threaded, they would have hung until the test timed out.
        assert elapsed < 5.0, f"{name}: {n} probes took {elapsed:.1f}s behind a wedged request (serialized?)"
        print(f"  [{name}] WEDGED-REQUEST PROOF: {n} parallel probes done in "
              f"{elapsed:.2f}s while one request was deliberately wedged  -> PASS")
    finally:
        wedged.close()


# ---------------------------------------------------------------------------

def test_bus_storage():
    port = _free_port()
    data = tempfile.mkdtemp(prefix="bus_")
    script = HERE / "bus_storage" / "server.py"
    env = {"COMPOSEIO_HOST": HOST, "COMPOSEIO_PORT": str(port), "COMPOSEIO_DATA_DIR": data}
    with Service("bus_storage", script, env, port):
        base = f"http://{HOST}:{port}"

        # --- parallel throughput (no serialization) ---
        def put_get(i):
            p = f"{base}/api/v1/assets/pool/asset_{i}.bin"
            _req("PUT", p, body=f"body-{i}".encode())
            st, b = _req("GET", p)
            assert st == 200 and b == f"body-{i}".encode()
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(put_get, range(20)))
        print(f"  [bus_storage] 20 parallel PUT+GET on distinct paths in {time.time()-t0:.2f}s -> PASS")

        # --- torn-read test: concurrent PUTs to SAME path + concurrent GETs ---
        target = f"{base}/api/v1/assets/shared/same.bin"
        # two distinct whole-file versions of very different sizes
        va = b"A" * 200_000
        vb = b"B" * 350_000
        _req("PUT", target, body=va)  # seed
        stop = threading.Event()
        torn = []
        rw_errors = []
        reads_done = [0]

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    _req("PUT", target, body=(va if i % 2 == 0 else vb), timeout=10)
                except Exception as e:  # noqa
                    rw_errors.append(repr(e))
                i += 1

        def reader():
            while not stop.is_set():
                try:
                    _, b = _req("GET", target, timeout=10)
                except Exception as e:  # noqa
                    rw_errors.append(repr(e))
                    continue
                reads_done[0] += 1
                # every read must be one COMPLETE prior/new version, never torn
                if b not in (va, vb):
                    torn.append(len(b))
        ths = [threading.Thread(target=writer) for _ in range(3)] + \
              [threading.Thread(target=reader) for _ in range(5)]
        for th in ths:
            th.start()
        time.sleep(2.0)
        stop.set()
        for th in ths:
            th.join()
        assert not rw_errors, f"bus_storage: request errors under load: {rw_errors[:3]}"
        assert not torn, f"bus_storage: {len(torn)} TORN reads observed, sizes={torn[:5]}"
        print(f"  [bus_storage] concurrent same-path PUT + {reads_done[0]} GETs: "
              f"0 torn reads -> PASS")

        # --- wedged request proof ---
        wedge = (b"PUT /api/v1/assets/wedge/stall.bin HTTP/1.1\r\n"
                 b"Host: x\r\nContent-Length: 100000000\r\n\r\n")
        _wedged_test("bus_storage", port, wedge,
                     lambda i: _req("GET", f"{base}/api/v1/status"))


def test_coordination_relay():
    port = _free_port()
    script = HERE / "coordination_relay" / "server.py"
    env = {"RELAY_HOST": HOST, "RELAY_PORT": str(port)}
    with Service("coordination_relay", script, env, port):
        base = f"http://{HOST}:{port}"

        # --- concurrent POSTs: unique ids, correct count ---
        N = 60

        def post(i):
            st, b = _req("POST", f"{base}/messages",
                         body=json.dumps({"from": f"u{i}", "text": f"m{i}",
                                          "channel": "c"}).encode(),
                         headers={"Content-Type": "application/json"})
            return json.loads(b)["id"]
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=20) as ex:
            ids = list(ex.map(post, range(N)))
        elapsed = time.time() - t0
        assert len(set(ids)) == N, f"relay: duplicate ids under concurrency: {N-len(set(ids))} dupes"
        st, b = _req("GET", f"{base}/messages")
        assert json.loads(b)["count"] == N, "relay: message count mismatch"
        print(f"  [coordination_relay] {N} concurrent POSTs, {len(set(ids))} unique ids, "
              f"count=={N} in {elapsed:.2f}s -> PASS")

        # --- wedged request proof ---
        wedge = (b"POST /messages HTTP/1.1\r\n"
                 b"Host: x\r\nContent-Type: application/json\r\n"
                 b"Content-Length: 100000000\r\n\r\n")
        _wedged_test("coordination_relay", port, wedge,
                     lambda i: _req("GET", f"{base}/health"))


def test_bus_storage_blob():
    """The UNIFIED-store doc-as-blob face ([SPEC:CXC-STORE]) under concurrency —
    the regression value the retired document_store concurrency test carried, moved
    onto the surviving store's `bus://<collection>/<id>.json` blob route."""
    port = _free_port()
    data = tempfile.mkdtemp(prefix="blob_")
    script = HERE / "bus_storage" / "server.py"
    env = {"COMPOSEIO_HOST": HOST, "COMPOSEIO_PORT": str(port), "COMPOSEIO_DATA_DIR": data}
    with Service("bus_storage", script, env, port):
        base = f"http://{HOST}:{port}"

        # --- concurrent writers to DIFFERENT doc-blobs + a concurrent reader ---
        N = 40

        def writer(i):
            p = f"{base}/api/v1/blob/things/doc_{i}.json"
            st, b = _req("PUT", p, body=json.dumps({"n": i, "v": i * 10}).encode())
            assert st == 201
        errors = []

        def reader():
            for _ in range(50):
                try:
                    _req("GET", f"{base}/api/v1/status")
                    _req("GET", f"{base}/api/v1/blob?prefix=things/")
                except Exception as e:  # noqa
                    errors.append(repr(e))
        t0 = time.time()
        rt = threading.Thread(target=reader)
        rt.start()
        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(writer, range(N)))
        rt.join()
        elapsed = time.time() - t0
        assert not errors, f"bus_storage blob: reader errors: {errors[:3]}"
        # verify every doc-blob landed and is readable + correct
        for i in range(N):
            st, b = _req("GET", f"{base}/api/v1/blob/things/doc_{i}.json")
            d = json.loads(b)
            assert st == 200 and d["n"] == i and d["v"] == i * 10, f"doc_{i} corrupt: {d}"
        print(f"  [bus_storage/blob] {N} concurrent writers (distinct blobs) + reader, "
              f"all correct in {elapsed:.2f}s -> PASS")

        # --- prefix-LIST returns every id, and the negative control still refuses ../ ---
        st, b = _req("GET", f"{base}/api/v1/blob?prefix=things/")
        names = {e["name"] for e in json.loads(b)["entries"]}
        assert st == 200 and names == {f"doc_{i}.json" for i in range(N)}, \
            f"blob LIST mismatch: {len(names)} entries"
        try:
            _req("GET", f"{base}/api/v1/blob/../etc/passwd")
            raise AssertionError("traversal not refused on blob route")
        except HttpError as e:
            assert e.status == 403, f"traversal returned {e.status}, expected 403"
        print("  [bus_storage/blob] prefix-LIST complete + ../ refused (403) -> PASS")

        # --- wedged request proof ---
        wedge = (b"PUT /api/v1/blob/things/wedge.json HTTP/1.1\r\n"
                 b"Host: x\r\nContent-Length: 100000000\r\n\r\n")
        _wedged_test("bus_storage", port, wedge,
                     lambda i: _req("GET", f"{base}/api/v1/status"))


def test_service_hub():
    port = _free_port()
    hub_dir = tempfile.mkdtemp(prefix="hub_")
    apps_dir = Path(hub_dir) / "apps"
    script = HERE / "service_hub" / "hub.py"
    env = {
        "COMPOSEIO_HUB_HOST": HOST,
        "COMPOSEIO_HUB_PORT": str(port),
        "COMPOSEIO_HUB_DIR": hub_dir,
        # Point bus storage at a dead port so the hub's startup fetch fails fast
        # (connection refused) and falls back to local dirs — no external dep.
        "COMPOSEIO_STORAGE_URL": "http://127.0.0.1:9",
    }
    with Service("service_hub", script, env, port):
        base = f"http://{HOST}:{port}"

        # --- parallel throughput (no serialization) on the status endpoint ---
        def get_status(i):
            st, b = _req("GET", f"{base}/api/v1/status")
            assert st == 200
            d = json.loads(b)
            assert d["service"] == "composeio-service-hub"
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(get_status, range(20)))
        print(f"  [service_hub] 20 parallel GET /api/v1/status in {time.time()-t0:.2f}s -> PASS")

        # --- registry integrity: concurrent POST auto-discover (register) while
        #     concurrent GETs iterate the apps registry. This is the exact
        #     read-modify-write race the per-app + registry locks protect:
        #     an insert into self.apps concurrent with dict iteration used to be
        #     able to raise "dictionary changed size during iteration".
        N = 25
        # Create the app directories AFTER startup so they are NOT auto-loaded at
        # boot — each is registered only when its first POST auto-discovers it.
        for i in range(N):
            d = apps_dir / f"cfgapp_{i}"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"cfgapp_{i}.compose.yaml").write_text(
                f"app:\n  id: cfgapp_{i}\ndocker:\n  image: busybox:latest\nnamespaces: []\n"
            )
        errors = []
        stop_iter = threading.Event()

        def registrar(i):
            # 'stop' action returns 200 regardless of docker availability (docker
            # missing/daemon-down -> success False, still HTTP 200); it registers
            # the app via auto-discover, which is what we're stressing.
            st, b = _req("POST", f"{base}/api/v1/apps/cfgapp_{i}/stop", timeout=25)
            assert st == 200, f"register cfgapp_{i} -> HTTP {st}"

        def lister():
            while not stop_iter.is_set():
                try:
                    _req("GET", f"{base}/api/v1/apps", timeout=10)
                except Exception as e:  # noqa
                    errors.append(repr(e))
        lt = [threading.Thread(target=lister) for _ in range(4)]
        for th in lt:
            th.start()
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=20) as ex:
            list(ex.map(registrar, range(N)))
        stop_iter.set()
        for th in lt:
            th.join()
        elapsed = time.time() - t0
        assert not errors, f"service_hub: concurrent apps-listing errors (dict race?): {errors[:3]}"
        st, b = _req("GET", f"{base}/api/v1/apps")
        registered = json.loads(b)
        for i in range(N):
            assert f"cfgapp_{i}" in registered, f"cfgapp_{i} missing from registry after concurrent register"
        assert len(registered) == N, f"expected {N} apps, got {len(registered)}"
        print(f"  [service_hub] {N} concurrent auto-register POSTs + 4 concurrent GET listers: "
              f"all {N} registered, 0 iteration errors in {elapsed:.2f}s -> PASS")

        # --- wedged request proof ---
        # The hub's handlers never read the request BODY (actions are path-based),
        # so a body-stall wedge wouldn't occupy a thread. Instead send an
        # INCOMPLETE request (request line + headers, NO terminating blank line)
        # so the server blocks parsing headers — that occupies one worker thread.
        # Single-threaded, this hung the whole API; threaded, probes fly past it.
        wedge = (b"POST /api/v1/apps/wedge/deploy HTTP/1.1\r\n"
                 b"Host: x\r\nContent-Length: 100000000\r\n")
        _wedged_test("service_hub", port, wedge,
                     lambda i: _req("GET", f"{base}/api/v1/status"))


def main():
    print("=== ComposeIO concurrency regression tests ===")
    for fn in (test_bus_storage, test_bus_storage_blob, test_coordination_relay,
               test_service_hub):
        print(f"\n-- {fn.__name__} --")
        fn()
    print("\nALL CONCURRENCY TESTS PASSED")


if __name__ == "__main__":
    main()
