"""Smoke tests for the ComposeServer PROCESS entrypoint
(``composeio.composeserver_host``) — the glue that runs a JSON manifest FILE as
a supervised host ([SPEC:CXC-CS-LOADER], [DD-CSL-MANIFEST-FORMAT] closed).

Every test states its NEGATIVE control inline ([CXC-VISION-EPISTEMICS]
rule-self-verification): a "started" log or a 200 is not evidence on its own, so
each positive is paired with the failure the mechanism must ALSO produce — an
absent key must 404, a stopped server must FREE its port, a broken manifest must
exit NON-zero. A test that can only observe success is not testing.

These run fully local against a SCRATCH data dir — nothing live is touched.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PY = _REPO / "python"
_SERVICES = _REPO / "services"
for _p in (str(_PY), str(_SERVICES)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from composeio.composeserver_host import ManifestError, load_manifest_file, main, run


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _port_is_bindable(host: str, port: int) -> bool:
    """True iff a NEW server could bind the port — the negative control for
    'stop() freed the LISTEN socket'.

    Mirrors what the real face does (``ThreadingHTTPServer.allow_reuse_address``
    is True → ``SO_REUSEADDR=1``): this is exactly the question a systemd restart
    or a cutover rollback asks — 'can ComposeServer re-bind :8420 now?'.
    SO_REUSEADDR bypasses a harmless TIME_WAIT left by a closed HTTP connection
    but, on both Linux and BSD/macOS, STILL fails against an active LISTENer — so
    an orphaned server still makes this False and the test can detect it (proven
    by the paired `assert not _port_is_bindable(...)` while serving)."""
    s = socket.socket()
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _write_manifest(tmp_path: Path, *, bus_port: int, fp_dir: Path,
                    connectors=None, bad_ref: bool = False) -> Path:
    data_dir = tmp_path / "store"
    (data_dir / "configs").mkdir(parents=True, exist_ok=True)
    bus_ref = ("composeio.plugins.bus_storage:NoSuchClass" if bad_ref
               else "composeio.plugins.bus_storage:BusStoragePlugin")
    manifest = {
        "tenant": "maker-test",
        "connectors": connectors or [],
        "plugins": [
            {"plugin": bus_ref,
             "config": {"data_dir": str(data_dir), "serve_http": True,
                        "http_host": "127.0.0.1", "http_port": bus_port}},
            {"plugin": "composeio.plugins.fingerprint_template_store:FingerprintTemplateStorePlugin",
             "config": {"data_dir": str(fp_dir), "http_enabled": False}},
            {"plugin": "composeio.plugins.time:TimePlugin", "config": {}},
            {"plugin": "print_services.plugin:PrintServicesPlugin",
             "config": {"enable_hp_ipp_backend": False,
                        "enable_elegoo_sdcp_backend": False}},
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2))
    return path


# --------------------------------------------------------------------------- #
# (a) all four plugins LOAD, not park.
# --------------------------------------------------------------------------- #
def test_all_four_plugins_load_clean(tmp_path):
    port = _free_port()
    manifest = _write_manifest(tmp_path, bus_port=port, fp_dir=tmp_path / "fp")
    srv, result = run(manifest, strict=True)
    try:
        loaded = {p.name for p in result.standup.loaded}
        assert loaded == {"bus_storage", "fingerprint_template_store",
                          "time", "print_services"}, loaded
        # NEGATIVE control: nothing parked, on EITHER park channel.
        assert result.standup.parked == {}, result.standup.parked
        assert result.config_parked == {}, result.config_parked
        assert result.is_clean()
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# (b) bus_storage HTTP face: known key 200, ABSENT key 404 (negative control).
# --------------------------------------------------------------------------- #
def test_bus_storage_http_known_200_absent_404(tmp_path):
    port = _free_port()
    manifest = _write_manifest(tmp_path, bus_port=port, fp_dir=tmp_path / "fp")
    srv, result = run(manifest, strict=True)
    base = f"http://127.0.0.1:{port}/api/v1/config"
    try:
        # health first — the face is actually up.
        _wait_http(f"http://127.0.0.1:{port}/health")

        # write a KNOWN key through the real HTTP PUT path.
        body = b"\x00\x01compiled-config-bytes\xff"
        req = urllib.request.Request(f"{base}/mod-known", data=body, method="PUT")
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status in (200, 201), r.status
        # POSITIVE: the known key round-trips 200 with the same bytes.
        with urllib.request.urlopen(f"{base}/mod-known", timeout=5) as r:
            assert r.status == 200
            assert r.read() == body

        # NEGATIVE CONTROL: an absent key is a 404, NOT a fabricated default.
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/mod-absent", timeout=5)
        assert exc.value.code == 404
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# (c) stop() FREES the port — no orphaned bind (negative control for teardown).
# --------------------------------------------------------------------------- #
def test_stop_frees_the_http_bind(tmp_path):
    port = _free_port()
    manifest = _write_manifest(tmp_path, bus_port=port, fp_dir=tmp_path / "fp")
    srv, result = run(manifest, strict=True)
    _wait_http(f"http://127.0.0.1:{port}/health")
    # While serving, the port is NOT bindable — proves the test can tell.
    assert not _port_is_bindable("127.0.0.1", port)
    srv.stop()
    # Give shutdown() a moment to release the socket.
    for _ in range(50):
        if _port_is_bindable("127.0.0.1", port):
            break
        time.sleep(0.05)
    assert _port_is_bindable("127.0.0.1", port), "stop() left the port bound"


# --------------------------------------------------------------------------- #
# (d) a broken manifest is REFUSED LOUDLY, not parked-and-green.
# --------------------------------------------------------------------------- #
def test_bad_plugin_ref_is_a_loud_nonzero_refusal(tmp_path):
    port = _free_port()
    manifest = _write_manifest(tmp_path, bus_port=port, fp_dir=tmp_path / "fp",
                               bad_ref=True)
    # run(strict) records the resolve park AND stops the server (not left green).
    srv, result = run(manifest, strict=True)
    try:
        assert result.config_parked, "a bad ref must produce a loader park"
        assert any("resolve:" in r
                   for rs in result.config_parked.values() for r in rs)
        assert not result.is_clean()
    finally:
        srv.stop()
    # main(--check) surfaces it as a NON-zero exit — the green/red the operator sees.
    rc = main(["--check", str(manifest)])
    assert rc == 3, rc


def test_missing_manifest_file_is_named_not_a_stacktrace(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest_file(tmp_path / "nope.json")
    # main() converts it to a clean exit 2, never an uncaught traceback.
    assert main(["--check", str(tmp_path / "nope.json")]) == 2


def test_non_object_manifest_rejected(tmp_path):
    bad = tmp_path / "list.json"
    bad.write_text("[1,2,3]")
    with pytest.raises(ManifestError):
        load_manifest_file(bad)


# --------------------------------------------------------------------------- #
# (e) connector to a DOWN broker degrades gracefully — plugins still load.
# --------------------------------------------------------------------------- #
def test_down_connector_does_not_block_plugin_load(tmp_path):
    port = _free_port()
    manifest = _write_manifest(
        tmp_path, bus_port=port, fp_dir=tmp_path / "fp",
        connectors=[{"type": "mqtt", "broker": "127.0.0.1", "port": 1}])
    srv, result = run(manifest, strict=True)
    try:
        assert {p.name for p in result.standup.loaded} >= {"bus_storage", "time"}
        assert result.config_parked == {}
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# (f) THE REAL SIGTERM path — a subprocess host exits 0 and frees the port.
# --------------------------------------------------------------------------- #
def test_subprocess_sigterm_clean_exit_and_port_freed(tmp_path):
    import signal
    port = _free_port()
    manifest = _write_manifest(tmp_path, bus_port=port, fp_dir=tmp_path / "fp")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(_PY), str(_SERVICES)])
    proc = subprocess.Popen(
        [sys.executable, "-m", "composeio.composeserver_host", str(manifest)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        # SIGTERM the INSTANT the face is up — the startup race window (handler
        # must be installed BEFORE run() serves, else default disposition exits
        # 143/-15). No cushion, so a regression of that ordering fails here.
        _wait_http(f"http://127.0.0.1:{port}/health", timeout=15)
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=15)
        assert rc == 0, f"SIGTERM should exit 0 (clean), got {rc}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
    for _ in range(60):
        if _port_is_bindable("127.0.0.1", port):
            break
        time.sleep(0.05)
    assert _port_is_bindable("127.0.0.1", port), "SIGTERM left the port bound"


def _wait_http(url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last = exc
        time.sleep(0.1)
    raise AssertionError(f"{url} never became healthy: {last}")
