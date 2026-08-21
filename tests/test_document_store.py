"""Test document store — CRUD, query, compact query, seeded data."""

import json
import os
import subprocess
import sys
import time
import urllib.request
import pytest
from pathlib import Path

SERVER_PATH = Path(__file__).parent.parent / "services" / "document_store" / "server.py"
TEST_PORT = 18421
BASE_URL = f"http://localhost:{TEST_PORT}"


@pytest.fixture(scope="module")
def docstore_db_dir(tmp_path_factory):
    """The document store's data dir, exposed separately so tests can assert
    filesystem side effects (or their absence) without reaching into the
    server subprocess."""
    return tmp_path_factory.mktemp("docstore_test")


@pytest.fixture(scope="module")
def docstore_server(docstore_db_dir):
    """Start document store server in subprocess."""
    db_dir = docstore_db_dir
    env = os.environ.copy()
    env["COMPOSEIO_DOCSTORE_DB"] = str(db_dir / "test.db")
    env["COMPOSEIO_DOCSTORE_PORT"] = str(TEST_PORT)
    env["COMPOSEIO_DOCSTORE_HOST"] = "127.0.0.1"

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{BASE_URL}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Document store did not start")

    yield proc
    proc.terminate()
    proc.wait(timeout=5)


def _get(path):
    return json.loads(urllib.request.urlopen(f"{BASE_URL}{path}").read())


def _put(path, data):
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="PUT",
    )
    return json.loads(urllib.request.urlopen(req).read())


def _post(path, data):
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    return json.loads(urllib.request.urlopen(req).read())


def _delete(path):
    req = urllib.request.Request(f"{BASE_URL}{path}", method="DELETE")
    return json.loads(urllib.request.urlopen(req).read())


def _put_binary(path, data, content_type="application/octet-stream"):
    """PUT raw bytes (not JSON) — exercises the binary write path, i.e.
    DocumentStore.put_binary / _blob_path."""
    req = urllib.request.Request(
        f"{BASE_URL}{path}", data=data,
        headers={"Content-Type": content_type}, method="PUT",
    )
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read()), resp.status


def _get_binary(path):
    resp = urllib.request.urlopen(f"{BASE_URL}{path}")
    return resp.read(), resp.headers.get("Content-Type")


class TestCRUD:
    def test_put_and_get(self, docstore_server):
        _put("/api/v1/collections/test/doc1", {"name": "Alpha", "value": 42})
        doc = _get("/api/v1/collections/test/doc1")
        assert doc["name"] == "Alpha"
        assert doc["value"] == 42
        assert doc["_id"] == "doc1"

    def test_update(self, docstore_server):
        _put("/api/v1/collections/test/doc1", {"name": "Alpha Updated", "value": 99})
        doc = _get("/api/v1/collections/test/doc1")
        assert doc["name"] == "Alpha Updated"
        assert doc["value"] == 99

    def test_delete(self, docstore_server):
        _put("/api/v1/collections/test/to_delete", {"name": "Temporary"})
        result = _delete("/api/v1/collections/test/to_delete")
        assert result["deleted"] == "to_delete"
        with pytest.raises(urllib.error.HTTPError):
            _get("/api/v1/collections/test/to_delete")

    def test_list_docs(self, docstore_server):
        """[DS-BINARY-C5] — a collection listing carries `{id, type}` RECORDS.

        This test asserted `"a" in docs` against a flat list of ids, which was the
        shape BEFORE the binary enhancement. `[DS-BINARY-API]` now specifies:

            {"collection": "captures",
             "documents": [{"id": "1234.bmp", "type": "binary", ...},
                           {"id": "config",   "type": "json"}]}

        Against that dict, `"a" in docs` tests the top-level KEYS — which are
        `collection` and `documents` — so it failed while both documents were present
        and correct. The SERVICE is conformant to the spec; the test was never updated
        when the enhancement landed, and asserted the superseded contract.
        """
        _put("/api/v1/collections/list_test/a", {"x": 1})
        _put("/api/v1/collections/list_test/b", {"x": 2})
        docs = _get("/api/v1/collections/list_test")

        assert docs["collection"] == "list_test"
        by_id = {d["id"]: d for d in docs["documents"]}
        assert set(by_id) == {"a", "b"}, (
            "exactly the two documents just written, no more — a listing that returns "
            f"extra ids is leaking across collections. got: {sorted(by_id)}")
        # the `type` marker is the POINT of DS-BINARY-C5: a consumer distinguishes a
        # JSON doc from a binary blob without a second request.
        assert by_id["a"]["type"] == "json"
        assert by_id["b"]["type"] == "json"

    def test_not_found(self, docstore_server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _get("/api/v1/collections/test/nonexistent")
        assert exc_info.value.code == 404


class TestQuery:
    def test_where_equals(self, docstore_server):
        _put("/api/v1/collections/q/x", {"color": "red", "size": 10})
        _put("/api/v1/collections/q/y", {"color": "blue", "size": 20})
        _put("/api/v1/collections/q/z", {"color": "red", "size": 30})
        result = _post("/api/v1/collections/q/query", {"where": {"color": "red"}})
        assert result["count"] == 2
        ids = {r["doc_id"] for r in result["results"]}
        assert ids == {"x", "z"}

    def test_where_greater_than(self, docstore_server):
        result = _post("/api/v1/collections/q/query", {"where": {"size": {">": 15}}})
        assert result["count"] == 2

    def test_order_by(self, docstore_server):
        result = _post("/api/v1/collections/q/query", {
            "order_by": "size", "order": "desc",
        })
        sizes = [r["size"] for r in result["results"]]
        assert sizes == sorted(sizes, reverse=True)

    def test_limit(self, docstore_server):
        result = _post("/api/v1/collections/q/query", {"limit": 1})
        assert result["count"] == 1

    def test_field_selection(self, docstore_server):
        result = _post("/api/v1/collections/q/query", {
            "fields": ["color"],
            "where": {"color": "red"},
        })
        for r in result["results"]:
            assert "color" in r
            assert "size" not in r


class TestNestedQuery:
    def test_nested_field(self, docstore_server):
        """Query on nested fields like resources.water."""
        # BSG planets are seeded automatically
        result = _post("/api/v1/collections/bsg_planets/query", {
            "where": {"resources.water": {">": 50}},
        })
        names = {r["name"] for r in result["results"]}
        assert "Kobol" in names
        assert "Algae Planet" in names
        assert "Ragnar Anchorage" not in names


class TestCompactQuery:
    def test_compact_ids(self, docstore_server):
        """Compact query returns comma-separated doc IDs."""
        result = _post("/api/v1/collections/bsg_planets/query", {
            "compact": "type=terrestrial",
        })
        ids = result["result"].split(",")
        assert "kobol" in ids
        assert "new_caprica" in ids

    def test_compact_with_comparison(self, docstore_server):
        result = _post("/api/v1/collections/bsg_planets/query", {
            "compact": "resources.water>50",
        })
        ids = result["result"].split(",")
        assert "kobol" in ids

    def test_compact_with_field_selector(self, docstore_server):
        result = _post("/api/v1/collections/bsg_planets/query", {
            "compact": "resources.water>50@name",
        })
        names = result["result"].split(",")
        assert "Kobol" in names


class TestSeededData:
    def test_bsg_planets_seeded(self, docstore_server):
        collections = _get("/api/v1/collections")
        names = {c["name"] for c in collections}
        assert "bsg_planets" in names

    def test_kobol_exists(self, docstore_server):
        doc = _get("/api/v1/collections/bsg_planets/kobol")
        assert doc["name"] == "Kobol"
        assert doc["resources"]["water"] == 85

    def test_cylon_colony(self, docstore_server):
        doc = _get("/api/v1/collections/bsg_planets/the_colony")
        assert doc["cylon_controlled"] is True


class TestBinaryPathEscape:
    """[DEFECT-DOCUMENT-STORE-WRITE-PATH-PUT-D8CCDE] — DocumentStore._blob_path had no
    traversal containment: a PUT binary with a `../`-laden doc_id escaped the
    `blobs/` directory and wrote to an arbitrary filesystem path, BEFORE any DB
    gate. This is the negative control: a guard whose test never fails is not a
    guard, so each escape attempt must be refused with 4xx AND must leave nothing
    on disk outside `blobs/`.
    """

    @staticmethod
    def _naive_target(db_dir: Path, collection: str, doc_id: str) -> Path:
        """Reproduce the PRE-FIX `_blob_path` computation (bare os.path.join, no
        containment) so the test can assert nothing ever landed there."""
        return Path(os.path.normpath(
            os.path.join(str(db_dir), "blobs", collection, doc_id)))

    def test_dotdot_escape_is_refused_and_writes_nothing(self, docstore_server, docstore_db_dir):
        marker = f"docstore_escape_probe_{os.getpid()}"
        doc_id = "/".join([".."] * 12 + ["tmp", marker])
        naive_target = self._naive_target(docstore_db_dir, "escape_test", doc_id)
        assert not naive_target.exists(), "probe target must not pre-exist"

        with pytest.raises(urllib.error.HTTPError) as exc:
            _put_binary(f"/api/v1/collections/escape_test/{doc_id}", b"pwned")
        assert 400 <= exc.value.code < 500, (
            f"escape attempt must be refused 4xx, got {exc.value.code}")
        assert not naive_target.exists(), (
            f"traversal escaped blobs/ and wrote to {naive_target}")

    def test_percent_encoded_dotdot_escape_is_refused_and_writes_nothing(
        self, docstore_server, docstore_db_dir,
    ):
        marker = f"docstore_escape_probe_enc_{os.getpid()}"
        doc_id = "/".join([".."] * 12 + ["tmp", marker])  # decoded form, for the target check
        encoded_doc_id = "/".join(["%2e%2e"] * 12 + ["tmp", marker])
        naive_target = self._naive_target(docstore_db_dir, "escape_test", doc_id)
        assert not naive_target.exists(), "probe target must not pre-exist"

        with pytest.raises(urllib.error.HTTPError) as exc:
            _put_binary(f"/api/v1/collections/escape_test/{encoded_doc_id}", b"pwned")
        assert 400 <= exc.value.code < 500, (
            f"encoded escape attempt must be refused 4xx, got {exc.value.code}")
        assert not naive_target.exists(), (
            f"encoded traversal escaped blobs/ and wrote to {naive_target}")

    def test_nested_doc_id_still_works(self, docstore_server):
        """Negative control for the negative control: doc_id LEGITIMATELY contains
        `/` (nested ids). The guard must reject escapes WITHOUT breaking a normal
        nested id — the containment is computed against the `blobs/` ROOT, not
        per-collection."""
        payload = b"\x89PNG-not-really-just-bytes"
        body, status = _put_binary(
            "/api/v1/collections/escape_test/nested/a/b.bin", payload,
            content_type="application/octet-stream",
        )
        assert status == 201
        assert body["doc_id"] == "nested/a/b.bin"

        data, ct = _get_binary("/api/v1/collections/escape_test/nested/a/b.bin")
        assert data == payload
        assert ct == "application/octet-stream"


class TestStatus:
    def test_status(self, docstore_server):
        status = _get("/api/v1/status")
        assert status["service"] == "composeio-document-store"
        assert status["collections"] >= 1
        assert status["total_documents"] >= 5
        # P-c: a caller must be able to tell WHICH host/build answered (cutover V5).
        assert status["hostname"]
        assert status["git_sha"]
