"""bus_storage device-firmware-baseline caps 3 + 4 — [SPEC:CXC-DEVICE-BASELINE].

Acceptance surface (read from `corpora/platform-specs/93-device-firmware-baseline.lts`,
nodes [CXC-DEVICE-BASELINE-CAP-HASH-ON-GET] and [CXC-DEVICE-BASELINE-CAP-BLOB-NOTIFY]):

  CAP 3 — content-hash on GET
    C3.1  asset  GET carries the server-provided content hash (X-Content-Hash + ETag)
          and it EQUALS the sha256 of the served bytes.
    C3.2  blob   GET carries the same headers, equal to the sha256 of the served bytes.
    C3.3  a device-style consumer SELF-SELECTS purely by string-comparing the header
          value against a `held` hash — it NEVER computes sha256 itself.
    C3.4  (negative control) an asset the client re-PUTs with DIFFERENT bytes serves a
          DIFFERENT header; a legacy file that never went through the new PUT path
          carries NO hash header (proving the header is stored-at-PUT, not the transport
          inventing one — and that GET does not recompute).

  CAP 4 — blob-route change-notify emit
    C4.1  a blob PUT of NEW bytes fires exactly ONE {old->new} change event through the
          SAME notifier the asset PUT uses (by reference, not a second producer).
    C4.2  (negative control) an IDENTICAL re-PUT of a blob emits NO second event — the
          notifier is content-discriminating.
    C4.3  a create (no prior blob) emits one event with a null `old`; a later change
          emits {old, new} with both real hashes.
    C4.4  (negative control) the deny-list still refuses `configs/` and
          `assets/fleet/private/` on the blob route (403), and NO change event escapes
          for a refused write.

CAP 4 is observed HERMETICALLY: the real `services/bus_storage/server.py` runs
IN-PROCESS with a real `AssetChangeNotifier` wired to a RECORDING transport (the same
double `tests/test_bus_storage_change_notify.py` uses), so emissions are captured with
no broker. This proves the SERVER WIRING plus the real notifier's dedup logic together.

`tests/conftest.py` puts `python/` on sys.path so `composeio` imports directly.
"""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SERVER = _REPO / "services" / "bus_storage" / "server.py"
_CHANGE_NOTIFY = _REPO / "services" / "bus_storage" / "change_notify.py"
HOST = "127.0.0.1"
NS = "bus_storage/asset/changed"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind((HOST, 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- #
# recording transport — the hermetic wire, no broker (mirrors change_notify test)
# --------------------------------------------------------------------------- #
class _RecordingTransport:
    def __init__(self):
        self.sent = []
        self._connected = True

    def start(self):
        pass

    def stop(self):
        pass

    def send(self, message):
        self.sent.append(message)

    def subscribe_all(self, callback):
        pass

    @property
    def is_connected(self):
        return self._connected


def _load_change_notify():
    spec = importlib.util.spec_from_file_location("bus_storage_change_notify_dbl", _CHANGE_NOTIFY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_server(data_dir: Path):
    """Import the REAL server.py as a fresh module against a scratch data dir.

    server.py reads COMPOSEIO_DATA_DIR at import time, so it is set FIRST. The server's
    own dir goes on sys.path so its top-level `from change_notify import content_hash`
    resolves to the sibling, exactly as it does when run as a script."""
    os.environ["COMPOSEIO_DATA_DIR"] = str(data_dir)
    srvdir = str(_SERVER.parent)
    if srvdir not in sys.path:
        sys.path.insert(0, srvdir)
    modname = f"bus_storage_server_uut_{data_dir.name}"
    spec = importlib.util.spec_from_file_location(modname, _SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _InProcServer:
    """The real bus_storage HTTP server, in-process, with a RECORDING notifier so blob
    change events are captured without a broker."""

    def __init__(self, tmp_path: Path):
        self.data_dir = tmp_path
        self.mod = _load_server(tmp_path)
        self.mod.ensure_dirs()
        # wire a real AssetChangeNotifier onto a recording transport
        cn = _load_change_notify()
        from composeio.bus import ComposeIOBus
        from composeio.namespace import Scope

        self.transport = _RecordingTransport()
        self.bus = ComposeIOBus(bus_id=f"uut-{tmp_path.name}", global_transport=self.transport)
        self.bus.start(announce_time=False)
        self.bus.register_namespace(NS, engine_config="json", scope=Scope.GLOBAL)
        self.mod._NOTIFIER = cn.AssetChangeNotifier(bus=self.bus, namespace=NS)

        self.port = _free_port()
        self.base = f"http://{HOST}:{self.port}"
        self.httpd = self.mod._ThreadingServer((HOST, self.port), self.mod.BusStorageHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            try:
                st, _b, _h = self.raw("GET", "/health")
                if st == 200:
                    break
            except Exception:
                pass
            time.sleep(0.02)
        else:
            raise RuntimeError("in-process bus_storage did not come up")
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.bus.stop()

    def raw(self, method: str, path: str, body: bytes | None = None):
        """LITERAL-path request (http.client, so `..` is not collapsed client-side).
        Returns (status, body_bytes, headers_dict)."""
        conn = http.client.HTTPConnection(HOST, self.port, timeout=5)
        try:
            conn.request(method, path, body=body)
            resp = conn.getresponse()
            data = resp.read()
            return resp.status, data, dict(resp.getheaders())
        finally:
            conn.close()

    # -- notifier observation ---------------------------------------------------
    def change_events(self):
        return [m for m in self.transport.sent if m.namespace == NS]


# --------------------------------------------------------------------------- #
# CAP 3 — content-hash on GET
# --------------------------------------------------------------------------- #
def test_C3_1_asset_get_carries_server_hash_equal_to_sha256(tmp_path):
    with _InProcServer(tmp_path) as srv:
        body = b"scene-v1-bytes-for-hash"
        st, _b, _h = srv.raw("PUT", "/api/v1/assets/flip/scene.yaml", body)
        assert st == 201
        st, got, headers = srv.raw("GET", "/api/v1/assets/flip/scene.yaml")
        assert st == 200 and got == body
        expect = _sha256(body)
        assert headers.get("X-Content-Hash") == expect, headers
        assert headers.get("ETag") == f'"{expect}"', headers
        print(f"C3.1 asset GET X-Content-Hash={headers.get('X-Content-Hash')} == sha256(bytes)")


def test_C3_2_blob_get_carries_server_hash_equal_to_sha256(tmp_path):
    with _InProcServer(tmp_path) as srv:
        body = b'{"board_profile":"velo","v":1}'
        st, _b, _h = srv.raw("PUT", "/api/v1/blob/board_profiles/velo.json", body)
        assert st == 201
        st, got, headers = srv.raw("GET", "/api/v1/blob/board_profiles/velo.json")
        assert st == 200 and got == body
        expect = _sha256(body)
        assert headers.get("X-Content-Hash") == expect, headers
        assert headers.get("ETag") == f'"{expect}"', headers
        print(f"C3.2 blob  GET X-Content-Hash={headers.get('X-Content-Hash')} == sha256(bytes)")


def test_C3_3_device_style_consumer_self_selects_by_header_never_hashes(tmp_path):
    """The device holds a server-provided hash string and self-selects by STRING
    COMPARISON only — [CXC-DEVICE-BASELINE-CAP-ASSET-RELOAD]'s rule. It NEVER runs
    sha256. Modelled here as a consumer that has no hashing function at all."""
    with _InProcServer(tmp_path) as srv:
        # boot: fetch the asset, read `held` off the SAME response (no device hashing).
        v1 = b"config-generation-1"
        srv.raw("PUT", "/api/v1/assets/dev/cfg.bin", v1)
        _st, _got, h1 = srv.raw("GET", "/api/v1/assets/dev/cfg.bin")
        held = h1["X-Content-Hash"]
        assert held == _sha256(v1)

        def device_decides(event_old, event_new, held_hash):
            # the firmware's pure string-compare self-select rule — no hashing
            if event_new == held_hash:
                return "ignore"          # already latest (retained-replay case)
            if event_old == held_hash:
                return "reload"          # the exact bytes I hold just changed — mine
            return "not-mine"            # ambiguous, carries no path -> do nothing

        # a change happens server-side; the device is told {old->new} by the event.
        v2 = b"config-generation-2-different"
        srv.raw("PUT", "/api/v1/assets/dev/cfg.bin", v2)
        _st, _got, h2 = srv.raw("GET", "/api/v1/assets/dev/cfg.bin")
        new_hash = h2["X-Content-Hash"]
        assert new_hash == _sha256(v2)

        # holding v1's hash, event {old=v1,new=v2} -> reload; then held becomes v2's.
        assert device_decides(held, new_hash, held) == "reload"
        assert device_decides(held, new_hash, new_hash) == "ignore"      # retained replay
        assert device_decides("deadbeef", "cafef00d", held) == "not-mine"
        print(f"C3.3 device self-selected reload on {held[:12]}..->{new_hash[:12]}.. w/o hashing")


def test_C3_4_neg_control_stored_at_put_not_recomputed_on_get(tmp_path):
    """Negative controls for cap 3:
      (a) DIFFERENT bytes on re-PUT -> a DIFFERENT stored header (not a stale one);
      (b) a file that NEVER went through the PUT handler (dropped straight on disk)
          carries NO hash header — proving the header is the STORED-at-PUT value and
          GET does not recompute one on the fly."""
    with _InProcServer(tmp_path) as srv:
        # (a) re-PUT with different bytes updates the served hash
        srv.raw("PUT", "/api/v1/assets/d/x.bin", b"AAAA")
        _s, _b, ha = srv.raw("GET", "/api/v1/assets/d/x.bin")
        srv.raw("PUT", "/api/v1/assets/d/x.bin", b"BBBBBB")
        _s, _b, hb = srv.raw("GET", "/api/v1/assets/d/x.bin")
        assert ha["X-Content-Hash"] == _sha256(b"AAAA")
        assert hb["X-Content-Hash"] == _sha256(b"BBBBBB")
        assert ha["X-Content-Hash"] != hb["X-Content-Hash"]

        # (b) a legacy asset written straight to disk (no PUT, no sidecar) -> no header
        legacy = srv.data_dir / "assets" / "legacy" / "old.bin"
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_bytes(b"pre-existing-content")
        st, got, headers = srv.raw("GET", "/api/v1/assets/legacy/old.bin")
        assert st == 200 and got == b"pre-existing-content"
        assert "X-Content-Hash" not in headers and "ETag" not in headers, headers
        print("C3.4 neg-control: re-PUT updates hash; legacy no-sidecar GET carries no header")


# --------------------------------------------------------------------------- #
# CAP 4 — blob-route change-notify emit
# --------------------------------------------------------------------------- #
def test_C4_1_blob_put_emits_one_old_new_event(tmp_path):
    with _InProcServer(tmp_path) as srv:
        srv.raw("PUT", "/api/v1/blob/apps/surface.json", b'{"v":1}')     # create
        srv.raw("PUT", "/api/v1/blob/apps/surface.json", b'{"v":2}')     # change
        evs = srv.change_events()
        assert len(evs) == 2, f"expected create+change = 2 events, got {len(evs)}"
        last = evs[-1].value
        assert set(last.keys()) == {"old", "new"}
        assert last["old"] == _sha256(b'{"v":1}')
        assert last["new"] == _sha256(b'{"v":2}')
        print(f"C4.1 blob change emitted {{old->new}} = {last['old'][:12]}..->{last['new'][:12]}..")


def test_C4_2_neg_control_identical_reput_emits_no_second_event(tmp_path):
    with _InProcServer(tmp_path) as srv:
        same = b'{"board":"velo","rev":7}'
        srv.raw("PUT", "/api/v1/blob/board_profiles/velo.json", same)    # 1 event (create)
        n_after_create = len(srv.change_events())
        srv.raw("PUT", "/api/v1/blob/board_profiles/velo.json", same)    # identical -> none
        srv.raw("PUT", "/api/v1/blob/board_profiles/velo.json", same)    # identical -> none
        n_final = len(srv.change_events())
        assert n_after_create == 1, f"create should emit exactly 1, got {n_after_create}"
        assert n_final == 1, f"identical re-PUTs must emit NO further events, got {n_final}"
        print("C4.2 neg-control: identical re-PUT emitted no second event (content-discriminating)")


def test_C4_3_create_has_null_old_then_change_carries_both(tmp_path):
    with _InProcServer(tmp_path) as srv:
        srv.raw("PUT", "/api/v1/blob/recipes/r.json", b"first")
        srv.raw("PUT", "/api/v1/blob/recipes/r.json", b"second")
        evs = srv.change_events()
        assert evs[0].value["old"] is None
        assert evs[0].value["new"] == _sha256(b"first")
        assert evs[1].value["old"] == _sha256(b"first")
        assert evs[1].value["new"] == _sha256(b"second")
        print("C4.3 create -> null old; change -> {old,new} both real hashes")


def test_C4_4_neg_control_denylist_refuses_and_emits_nothing(tmp_path):
    """The deny-list must still refuse the two protected trees on the blob route (403),
    a legit blob still round-trips (positive control), and a REFUSED write must NOT
    escape a change event."""
    with _InProcServer(tmp_path) as srv:
        # seed a config through the DEDICATED route + a private creds file on disk
        assert srv.raw("PUT", "/api/v1/config/velo-panel", b"\xc1\x00cfg")[0] == 201
        priv = srv.data_dir / "assets" / "fleet" / "private"
        priv.mkdir(parents=True, exist_ok=True)
        (priv / "velo.creds").write_bytes(b"SUPER-SECRET-CREDS")

        events_before = len(srv.change_events())

        # deny cases (protected trees) — every method refused 403
        assert srv.raw("GET", "/api/v1/blob/configs/velo-panel.bin")[0] == 403
        assert srv.raw("PUT", "/api/v1/blob/configs/velo-panel.bin", b"pwn")[0] == 403
        assert srv.raw("DELETE", "/api/v1/blob/configs/velo-panel.bin")[0] == 403
        assert srv.raw("GET", "/api/v1/blob/assets/fleet/private/velo.creds")[0] == 403
        assert srv.raw("PUT", "/api/v1/blob/assets/fleet/private/x.creds", b"pwn")[0] == 403
        assert srv.raw("DELETE", "/api/v1/blob/assets/fleet/private/velo.creds")[0] == 403
        # a ../ escape that resolves inside BLOB_ROOT then lands in a denied tree
        assert srv.raw("PUT", "/api/v1/blob/apps/../configs/velo-panel.bin", b"pwn")[0] == 403

        # a refused write emits NO change event
        assert len(srv.change_events()) == events_before, "a denied blob write leaked an event"

        # positive control: a legit blob round-trips and DOES emit
        assert srv.raw("PUT", "/api/v1/blob/apps/ok.json", b'{"ok":1}')[0] == 201
        assert len(srv.change_events()) == events_before + 1

        # the protected secret is untouched, and the config still holds its sidecar
        assert (priv / "velo.creds").read_bytes() == b"SUPER-SECRET-CREDS"
        assert (srv.data_dir / "configs" / "velo-panel.bin").exists()
        print("C4.4 neg-control: deny-list refuses 3 methods x 2 trees + ../ escape, no event leaked")



# --------------------------------------------------------------------------- #
# PRE-SECRETS security fix — the deny-list must guard the ASSET route too
# --------------------------------------------------------------------------- #
def test_C5_pre_secrets_asset_route_denylist_refuses_credentials(tmp_path):
    """REPRODUCTION + FIX. The deny-list was wired onto /api/v1/blob/ ONLY, so an
    unauthenticated GET /api/v1/assets/fleet/private/velo.creds returned real
    credentials over the LAN. The guard is a property of the storage root, not one
    route: `_handle_asset_get`/`_handle_asset_put` now call `_blob_denied` too.

    Negative controls: GET + PUT into the credential tree via the ASSET route -> 403;
    a ../ escape into it -> 403; a legit asset still 200 (positive control, so a 403
    that fires because the whole route broke is caught, not mistaken for a pass)."""
    with _InProcServer(tmp_path) as srv:
        # seed a real credential file on disk exactly where the live host holds it
        priv = srv.data_dir / "assets" / "fleet" / "private"
        priv.mkdir(parents=True, exist_ok=True)
        (priv / "velo.creds").write_bytes(b"SUPER-SECRET-CREDS")

        codes = {}
        def check(label, method, path, want, body=None):
            st, data, _h = srv.raw(method, path, body)
            codes[label] = st
            assert st == want, f"{label}: {method} {path} -> {st}, expected {want}"
            return data

        # THE REPRODUCTION: the exact request the reviewer flagged, now refused.
        got = check("repro asset GET creds", "GET",
                    "/api/v1/assets/fleet/private/velo.creds", 403)
        assert b"SUPER-SECRET-CREDS" not in got, "credentials leaked in the 403 body"
        # write into the credential tree via the asset route -> refused
        check("asset PUT creds", "PUT",
              "/api/v1/assets/fleet/private/x.creds", 403, b"pwn")
        # a ../ escape that resolves inside DATA_DIR/assets then lands in the tree
        check("asset ../ escape", "GET",
              "/api/v1/assets/displays/../fleet/private/velo.creds", 403)

        # positive control: a legit asset still round-trips 200 (route not broken)
        check("legit asset PUT", "PUT",
              "/api/v1/assets/displays/foo.compose.yaml", 201, b"widgets: []\n")
        legit = check("legit asset GET", "GET",
                      "/api/v1/assets/displays/foo.compose.yaml", 200)
        assert legit == b"widgets: []\n"

        # cross-check the fix landed on the asset route, not just the blob route:
        # the SAME protected path is refused on BOTH faces now.
        assert srv.raw("GET", "/api/v1/blob/assets/fleet/private/velo.creds")[0] == 403
        assert srv.raw("GET", "/api/v1/assets/fleet/private/velo.creds")[0] == 403

        # the secret file itself is untouched by any of the refused writes
        assert (priv / "velo.creds").read_bytes() == b"SUPER-SECRET-CREDS"
        print("C5 PRE-SECRETS asset-route deny-list — live status codes:")
        for label, st in codes.items():
            print(f"  {st}  {label}")


if __name__ == "__main__":
    import tempfile

    for fn in [
        test_C3_1_asset_get_carries_server_hash_equal_to_sha256,
        test_C3_2_blob_get_carries_server_hash_equal_to_sha256,
        test_C3_3_device_style_consumer_self_selects_by_header_never_hashes,
        test_C3_4_neg_control_stored_at_put_not_recomputed_on_get,
        test_C4_1_blob_put_emits_one_old_new_event,
        test_C4_2_neg_control_identical_reput_emits_no_second_event,
        test_C4_3_create_has_null_old_then_change_carries_both,
        test_C4_4_neg_control_denylist_refuses_and_emits_nothing,
        test_C5_pre_secrets_asset_route_denylist_refuses_credentials,
    ]:
        d = Path(tempfile.mkdtemp(prefix="cxc_dbl_"))
        fn(d)
    print("\nALL device-baseline cap3+cap4 checks PASS")
