"""Acceptance for the storage consolidation ([SPEC:CXC-STORE]).

The ONE store (bus_storage) serves BOTH `bus://assets/<path>` AND the doc-as-blob face
`bus://<collection>/<id>.json` + a prefix-LIST; the client seams (resolve_doc / put_doc /
query_docs) round-trip against it with CLIENT-SIDE filtering; the migration tool converts a
FIXTURE docstore.db byte-identically; and the `../` traversal fix survives on the blob route.

Runs a real `services/bus_storage/server.py` on a temp data dir + free port. No broker, no
network beyond loopback. Executable directly (`python3 tests/test_storage_consolidation.py`)
or under pytest.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "services" / "bus_storage" / "server.py"
sys.path.insert(0, str(REPO / "python"))

from composeio import resolve  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """Launch bus_storage on a temp data dir; tear down on exit."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen | None = None

    def __enter__(self):
        env = dict(os.environ,
                   COMPOSEIO_HOST="127.0.0.1", COMPOSEIO_PORT=str(self.port),
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
        # Point the client seams at this instance.
        resolve.BUS_STORAGE_URL = self.base
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def _put(base: str, path: str, body: bytes) -> int:
    req = urllib.request.Request(f"{base}{path}", data=body, method="PUT")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# --------------------------------------------------------------------------- #
# 1. The unified store serves BOTH faces + a working prefix-LIST.
# --------------------------------------------------------------------------- #
def test_both_faces_and_list(tmp_path=None):
    data = _mk_tmp(tmp_path, "faces")
    with _Server(data) as srv:
        b = srv.base
        # asset face (fleet-facing)
        assert _put(b, "/api/v1/assets/displays/x.yaml", b"scene: 1") == 201
        st, got = _get(b, "/api/v1/assets/displays/x.yaml")
        assert st == 200 and got == b"scene: 1"
        # doc-as-blob face
        assert _put(b, "/api/v1/blob/apps/dradis.json", b'{"title":"DRADIS"}') == 201
        st, got = _get(b, "/api/v1/blob/apps/dradis.json")
        assert st == 200 and got == b'{"title":"DRADIS"}'
        # prefix-LIST
        _put(b, "/api/v1/blob/apps/console.json", b'{"title":"Console"}')
        st, got = _get(b, "/api/v1/blob?prefix=apps/")
        names = {e["name"] for e in json.loads(got)["entries"]}
        assert st == 200 and names == {"dradis.json", "console.json"}, names
    print("PASS both faces + prefix-LIST")


# --------------------------------------------------------------------------- #
# 2. Negative control — the f02bbc0 traversal fix survives on the blob route.
# --------------------------------------------------------------------------- #
def test_traversal_refused_on_blob(tmp_path=None):
    data = _mk_tmp(tmp_path, "trav")
    with _Server(data) as srv:
        b = srv.base
        st, _ = _get(b, "/api/v1/blob/../etc/passwd")          # relative escape
        assert st == 403, f"../ not refused: {st}"
        # absolute-path escape (Path('/root') / '/etc' == '/etc')
        req = urllib.request.Request(f"{b}/api/v1/blob//etc/passwd", data=b"x", method="PUT")
        try:
            code = urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            code = e.code
        assert code == 403, f"absolute path not refused: {code}"
    print("PASS traversal refused on blob route (403)")


# --------------------------------------------------------------------------- #
# 3. The client seams round-trip; query_docs filters CLIENT-SIDE.
# --------------------------------------------------------------------------- #
def test_client_seams_and_client_side_query(tmp_path=None):
    data = _mk_tmp(tmp_path, "seams")
    with _Server(data):
        # put_doc -> resolve_doc round-trip. doc:// is FULLY DEPRECATED (2026-09-03):
        # only the bus:// blob address resolves; a doc:// URI now HARD-BREAKS.
        resolve.put_doc("fleet_devices", "minnow-01",
                        {"device": "minnow-01", "role": "fleet", "provision": {"recipe": "deb13"}})
        resolve.put_doc("fleet_devices", "minnow-02",
                        {"device": "minnow-02", "role": "spare", "provision": {"recipe": "deb12"}})
        assert resolve.resolve_doc("bus://fleet_devices/minnow-01.json")["device"] == "minnow-01"
        assert resolve.resolve_doc("bus://fleet_devices/minnow-02.json")["role"] == "spare"
        # NEGATIVE CONTROL: a doc:// URI no longer translates — it raises.
        try:
            resolve.resolve_doc("doc://fleet_devices/minnow-01")
            raise AssertionError("doc:// must hard-break, but resolve_doc returned")
        except ValueError:
            pass
        # query_docs: where-filter runs CLIENT-SIDE, order_by + fields honoured
        rows = resolve.query_docs("fleet_devices",
                                  where={"provision.recipe": "deb13"}, order_by="device")
        assert [r["doc_id"] for r in rows] == ["minnow-01"], rows
        rows = resolve.query_docs("fleet_devices", order_by="device",
                                  fields=["device"])
        assert rows == [{"doc_id": "minnow-01", "device": "minnow-01"},
                        {"doc_id": "minnow-02", "device": "minnow-02"}], rows
        # a missing collection is an empty pick list, not an error
        assert resolve.query_docs("nope") == []
    print("PASS client seams + client-side query")


# --------------------------------------------------------------------------- #
# 4. The migration tool round-trips a FIXTURE docstore.db byte-identically.
# --------------------------------------------------------------------------- #
def _make_fixture_docstore(root: Path) -> tuple[Path, Path, dict, dict]:
    """Build a synthetic docstore.db + blobs/ mirroring document_store's schema.
    Returns (db_path, blobs_dir, json_docs, binary_docs) for assertions."""
    db = root / "docstore.db"
    blobs = root / "blobs"
    json_docs = {
        ("apps", "dradis"): {"title": "DRADIS", "surface": "doc://surfaces/dradis"},
        ("bsg_planets", "kobol"): {"name": "Kobol", "resources": {"water": 85}},
        ("bsg_planets", "ragnar"): {"name": "Ragnar", "resources": {"water": 0}},
    }
    binary_docs = {("captures", "cyd/6891.bmp"): b"BM\x00\x01\x02\x03rawpixels"}
    conn = sqlite3.connect(str(db))
    now = 1.0
    seen_tables = set()
    for (coll, doc_id), data in json_docs.items():
        if coll not in seen_tables:
            conn.execute(f"CREATE TABLE [{coll}] (doc_id TEXT PRIMARY KEY, data JSON NOT NULL, "
                         f"created_at REAL NOT NULL, updated_at REAL NOT NULL)")
            seen_tables.add(coll)
        conn.execute(f"INSERT INTO [{coll}] (doc_id, data, created_at, updated_at) VALUES (?,?,?,?)",
                     (doc_id, json.dumps(data), now, now))
    conn.execute("CREATE TABLE _binary_meta (collection TEXT NOT NULL, doc_id TEXT NOT NULL, "
                 "content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, original_filename TEXT, "
                 "created_at REAL NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY (collection, doc_id))")
    for (coll, doc_id), raw in binary_docs.items():
        conn.execute("INSERT INTO _binary_meta VALUES (?,?,?,?,?,?,?)",
                     (coll, doc_id, "image/bmp", len(raw), doc_id, now, now))
        f = blobs / coll / doc_id
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(raw)
    conn.commit()
    conn.close()
    return db, blobs, json_docs, binary_docs


def test_migration_tool_byte_identical(tmp_path=None):
    root = _mk_tmp(tmp_path, "mig")
    src = root / "src"
    src.mkdir()
    db, blobs, json_docs, binary_docs = _make_fixture_docstore(src)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "migtool", REPO / "tools" / "migrate_document_store_to_bus.py")
    mig = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mig)

    with _Server(root / "store"):
        summary = mig.migrate(db, blobs, resolve, dry_run=False)
        assert summary == {"json_docs": len(json_docs), "binary_docs": len(binary_docs)}, summary
        # JSON docs: byte-identical to canonical json bytes at bus://c/id.json
        for (coll, doc_id), data in json_docs.items():
            got = resolve.resolve_bytes(f"bus://{coll}/{doc_id}.json")
            assert got == json.dumps(data).encode(), f"{coll}/{doc_id} not byte-identical"
            assert resolve.resolve_doc(f"bus://{coll}/{doc_id}.json") == data
        # binary docs: raw bytes at bus://c/id (no .json)
        for (coll, doc_id), raw in binary_docs.items():
            assert resolve.resolve_bytes(f"bus://{coll}/{doc_id}") == raw, f"{coll}/{doc_id} blob differs"
        # the migrated JSON collection is queryable client-side
        planets = resolve.query_docs("bsg_planets", where={"resources.water": {">": 50}})
        assert [p["doc_id"] for p in planets] == ["kobol"], planets
    print("PASS migration byte-identical round-trip (JSON + binary)")


def _mk_tmp(tmp_path, name: str) -> Path:
    if tmp_path is not None:
        d = Path(tmp_path) / name
        d.mkdir(parents=True, exist_ok=True)
        return d
    import tempfile
    return Path(tempfile.mkdtemp(prefix=f"cxcstore_{name}_"))


if __name__ == "__main__":
    test_both_faces_and_list()
    test_traversal_refused_on_blob()
    test_client_seams_and_client_side_query()
    test_migration_tool_byte_identical()
    print("\nALL storage-consolidation acceptance checks PASS")
