"""Test bus storage service — DP-CIO-23.

Tests run against the actual HTTP server in a subprocess.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import pytest
from pathlib import Path

# Server module path
SERVER_PATH = Path(__file__).parent.parent / "services" / "bus_storage" / "server.py"
TEST_PORT = 18420
BASE_URL = f"http://localhost:{TEST_PORT}"


@pytest.fixture(scope="module")
def storage_server(tmp_path_factory):
    """Start bus storage server in a subprocess for testing."""
    data_dir = tmp_path_factory.mktemp("bus_storage_data")
    env = os.environ.copy()
    env["COMPOSEIO_DATA_DIR"] = str(data_dir)
    env["COMPOSEIO_PORT"] = str(TEST_PORT)
    env["COMPOSEIO_HOST"] = "127.0.0.1"

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    # Wait for server to start
    for _ in range(30):
        try:
            urllib.request.urlopen(f"{BASE_URL}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("Bus storage server did not start")

    yield proc

    proc.terminate()
    proc.wait(timeout=5)


class TestConfigOperations:
    def test_push_and_get_config(self, storage_server):
        config_data = b"\xC1\x00\x01\x01\x05\x00\xAB\xCD\xEF\x01test_data"
        module_id = "test-panel-1"

        # Push
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/config/{module_id}",
            data=config_data,
            headers={"Content-Type": "application/octet-stream"},
            method="PUT",
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 201

        # Get
        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/config/{module_id}")
        assert resp.read() == config_data

    def test_config_checksum(self, storage_server):
        config_data = b"checksum_test_data"
        module_id = "checksum-test"
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/config/{module_id}",
            data=config_data,
            method="PUT",
        )
        urllib.request.urlopen(req)

        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/config/{module_id}/checksum")
        checksum = resp.read().decode()
        assert len(checksum) == 8  # CRC-32 hex string

    def test_config_not_found(self, storage_server):
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{BASE_URL}/api/v1/config/nonexistent")
        assert exc_info.value.code == 404

    def test_delete_config(self, storage_server):
        module_id = "delete-test"
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/config/{module_id}",
            data=b"delete_me",
            method="PUT",
        )
        urllib.request.urlopen(req)

        # Delete
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/config/{module_id}",
            method="DELETE",
        )
        urllib.request.urlopen(req)

        # Verify gone
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"{BASE_URL}/api/v1/config/{module_id}")


class TestNamespaceRegistry:
    def test_register_and_get(self, storage_server):
        ns_data = json.dumps({
            "engine": "binary",
            "value": "off",
            "scope": "auto",
        }).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/namespaces/ftl_arm",
            data=ns_data,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        resp = urllib.request.urlopen(req)
        assert resp.status == 201

        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/namespaces/ftl_arm")
        data = json.loads(resp.read())
        assert data["name"] == "ftl_arm"
        assert data["engine"] == "binary"

    def test_list_namespaces(self, storage_server):
        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/namespaces")
        data = json.loads(resp.read())
        assert isinstance(data, list)


class TestAssets:
    def test_upload_and_fetch(self, storage_server):
        asset_data = b"\x89PNG\r\n\x1a\nfake_image_data"
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/assets/album_art/test.png",
            data=asset_data,
            headers={"Content-Type": "image/png"},
            method="PUT",
        )
        urllib.request.urlopen(req)

        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/assets/album_art/test.png")
        assert resp.read() == asset_data


class TestAssetListing:
    def _put(self, path, data=b"x"):
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/assets/{path}",
            data=data,
            method="PUT",
        )
        urllib.request.urlopen(req)

    def _list(self, prefix=""):
        from urllib.parse import urlencode
        url = f"{BASE_URL}/api/v1/assets?{urlencode({'prefix': prefix})}"
        resp = urllib.request.urlopen(url)
        return json.loads(resp.read())

    def test_list_root_dirs_first_and_kinds(self, storage_server):
        # Seed a nested tree: two dirs (icons/, sounds/) + one root file.
        self._put("listing/icons/logo.svg", b"<svg/>")
        self._put("listing/icons/badge.png", b"\x89PNG")
        self._put("listing/sounds/beep.wav", b"RIFF")
        self._put("listing/readme.txt", b"hello world")

        body = self._list("listing")
        assert body["prefix"] == "listing/"       # trailing-slash echo
        entries = body["entries"]
        kinds = [e["kind"] for e in entries]
        # dirs first, then files
        assert kinds == ["dir", "dir", "file"]
        # dirs sorted case-insensitively by name
        assert [e["name"] for e in entries[:2]] == ["icons", "sounds"]
        # the file entry carries a size and the full slash-joined path
        f = entries[2]
        assert f["name"] == "readme.txt"
        assert f["path"] == "listing/readme.txt"
        assert f["size"] == len(b"hello world")
        # dir entries have no size and a descend-able path
        assert "size" not in entries[0]
        assert entries[0]["path"] == "listing/icons"

    def test_list_subdir(self, storage_server):
        self._put("listing2/a/one.txt", b"1")
        self._put("listing2/a/Two.txt", b"22")
        body = self._list("listing2/a")
        assert body["prefix"] == "listing2/a/"
        names = [e["name"] for e in body["entries"]]
        # both files, sorted case-insensitively (Two before... 'one' vs 'Two')
        assert all(e["kind"] == "file" for e in body["entries"])
        assert names == sorted(names, key=str.lower)
        assert {"one.txt", "Two.txt"} == set(names)

    def test_list_absent_prefix_is_empty_200(self, storage_server):
        body = self._list("does/not/exist/yet")
        assert body["entries"] == []
        assert body["prefix"] == "does/not/exist/yet/"

    def test_list_root_bare_path(self, storage_server):
        # the bare collection path (no prefix) lists the assets root.
        self._put("rootfile.bin", b"\x00\x01")
        body = self._list("")
        assert body["prefix"] == ""
        assert any(e["name"] == "rootfile.bin" and e["kind"] == "file"
                   for e in body["entries"])

    def test_list_skips_dotfiles(self, storage_server):
        self._put("dotdir/.hidden", b"secret")
        self._put("dotdir/visible.txt", b"ok")
        body = self._list("dotdir")
        names = [e["name"] for e in body["entries"]]
        assert names == ["visible.txt"]

    def test_list_traversal_is_403(self, storage_server):
        from urllib.parse import urlencode
        url = f"{BASE_URL}/api/v1/assets?{urlencode({'prefix': '../../etc'})}"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(url)
        assert exc.value.code == 403

    def test_single_asset_get_still_works(self, storage_server):
        # the new list route must not shadow the single-asset GET.
        self._put("coexist/thing.txt", b"payload")
        resp = urllib.request.urlopen(
            f"{BASE_URL}/api/v1/assets/coexist/thing.txt")
        assert resp.read() == b"payload"


class TestWorkloads:
    def test_push_and_list(self, storage_server):
        wl_data = json.dumps({
            "workload": "camera_server",
            "runtime": "python",
            "entry": "server.py",
            "restart": "always",
        }).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/api/v1/workloads/camera_server",
            data=wl_data,
            headers={"Content-Type": "application/json"},
            method="PUT",
        )
        urllib.request.urlopen(req)

        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/workloads")
        data = json.loads(resp.read())
        assert any(w["id"] == "camera_server" for w in data)


class TestStatus:
    def test_status(self, storage_server):
        resp = urllib.request.urlopen(f"{BASE_URL}/api/v1/status")
        data = json.loads(resp.read())
        assert data["service"] == "composeio-bus-storage"
        assert "configs" in data
        assert "namespaces" in data
        # P-c: a caller must be able to tell WHICH host/build answered (cutover V5).
        assert data["hostname"]
        assert data["git_sha"]
