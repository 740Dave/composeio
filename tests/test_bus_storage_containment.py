"""Bus storage — path containment + request-error boundary. [SPEC:CXC-BUSSTORE]

Enforces [CXC-BUSSTORE-CONTAINMENT] and [CXC-BUSSTORE-ERROR-BOUNDARY].

WHY THIS FILE EXISTS, AND WHY IT IS PARAMETRIZED ACROSS ROUTES
--------------------------------------------------------------
The containment guard was written ONCE, in 2026-07 (30255a0), as a local detail
inside `_handle_asset_list` when that route was added. It was never lifted to a
shared primitive, so the FIVE sibling handlers that also turn a request-supplied
segment into a filesystem path never got it: asset GET, asset PUT, config GET,
config PUT, config DELETE, workload PUT. The result was unauthenticated
arbitrary file read AND write in a service the whole fleet fetches config from.

So the containment cases below are parametrized OVER THE ROUTES, not written
once per route by hand. A route added next month that builds `ROOT / segment`
directly is added to `_ESCAPE_ROUTES` and fails immediately — which is the
property the original guard lacked. Enumerating by hand is how the hole was
inherited five times; the parametrization is the de-fork.

Each attack form defeats a DIFFERENT naive guard, so each is its own case:
  * `../`      — the obvious one.
  * `%2e%2e/`  — defeats any check that runs BEFORE `unquote`. `_route`
                 unquotes the path first, so a raw-string check on the wire
                 form never sees the `..` at all.
  * `//abs`    — defeats a `..`-substring check entirely. Python-specific trap:
                 `Path("/root") / "/etc/hosts"` == `Path("/etc/hosts")`. The
                 base is DISCARDED. There is no `..` anywhere in the request.

POSITIVE CONTROLS ARE MANDATORY, NOT OPTIONAL
---------------------------------------------
`bus://assets/<path>` resolution and the boot-time config fetch are what the
fleet depends on. A guard that also rejects valid nested asset paths trades a
vulnerability for a fleet outage. `TestLegitimatePathsStillResolve` is the
paired positive control and must stay green.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import http.client
from pathlib import Path

import pytest

SERVER_PATH = Path(__file__).parent.parent / "services" / "bus_storage" / "server.py"
TEST_PORT = 18999
HOST = "127.0.0.1"
BASE_URL = f"http://{HOST}:{TEST_PORT}"

# The canary lives OUTSIDE the assets/config/workload roots but inside the
# scratch tree, so a successful escape is unambiguous and nothing real is
# exposed even if this suite is run somewhere unexpected.
CANARY_NAME = "SECRET.txt"
CANARY_BODY = b"TOP-SECRET-CANARY-IF-YOU-CAN-READ-THIS-CONTAINMENT-FAILED\n"


@pytest.fixture(scope="module")
def scratch_server(tmp_path_factory):
    """A SCRATCH bus_storage on a scratch port + scratch data dir.

    Never production. `mqtt-broker.local:8420` is a live service the whole
    fleet fetches config from; probing it in the write form is how a security
    test becomes an outage.
    """
    scratch = tmp_path_factory.mktemp("busstore_containment")
    data_dir = scratch / "data"
    data_dir.mkdir()
    (scratch / CANARY_NAME).write_bytes(CANARY_BODY)
    (data_dir / CANARY_NAME).write_bytes(CANARY_BODY)

    env = os.environ.copy()
    env["COMPOSEIO_DATA_DIR"] = str(data_dir)
    env["COMPOSEIO_PORT"] = str(TEST_PORT)
    env["COMPOSEIO_HOST"] = HOST

    proc = subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for _ in range(50):
        try:
            urllib.request.urlopen(f"{BASE_URL}/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("scratch bus_storage did not start")

    yield {"proc": proc, "scratch": scratch, "data_dir": data_dir}

    proc.terminate()
    proc.wait(timeout=5)


def raw_request(method, path, body=None):
    """Issue a request WITHOUT urllib's normalization, and tolerate no response.

    urllib collapses `..` in a URL client-side, which would silently neuter
    every traversal case here — the request would never leave as typed. It also
    raises on a closed connection in a way that hides whether a status line was
    ever sent. `http.client` with a literal path avoids both.

    Returns (status:int|None, body:bytes). status is None when the server sent
    NO HTTP RESPONSE AT ALL — the connection-closed failure this suite also
    pins, which sends an operator to the wrong layer for hours.
    """
    conn = http.client.HTTPConnection(HOST, TEST_PORT, timeout=5)
    try:
        conn.request(method, path, body=body)
        resp = conn.getresponse()
        return resp.status, resp.read()
    except (http.client.RemoteDisconnected, http.client.BadStatusLine,
            ConnectionResetError):
        return None, b""
    finally:
        conn.close()


# (label, method, url-template with {seg}, body) for every route that turns a
# request-supplied segment into a filesystem path. ADD NEW ROUTES HERE.
_ESCAPE_ROUTES = [
    ("asset-get",    "GET",    "/api/v1/assets/{seg}",    None),
    ("asset-put",    "PUT",    "/api/v1/assets/{seg}",    b"pwned"),
    ("config-get",   "GET",    "/api/v1/config/{seg}",    None),
    ("config-put",   "PUT",    "/api/v1/config/{seg}",    b"pwned"),
    ("config-del",   "DELETE", "/api/v1/config/{seg}",    None),
    ("workload-put", "PUT",    "/api/v1/workloads/{seg}", b'{"x":1}'),
]

# Each form defeats a different naive guard — see the module docstring.
_ESCAPE_FORMS = [
    ("dotdot",       f"../{CANARY_NAME}"),
    ("dotdot-deep",  f"../../{CANARY_NAME}"),
    ("pct-encoded",  f"%2e%2e/{CANARY_NAME}"),
    ("pct-slash",    f"..%2f{CANARY_NAME}"),
    ("absolute",     f"/{CANARY_NAME}"),
    ("absolute-etc", "/etc/hosts"),
]


@pytest.mark.parametrize("route", _ESCAPE_ROUTES, ids=lambda r: r[0])
@pytest.mark.parametrize("form", _ESCAPE_FORMS, ids=lambda f: f[0])
def test_escaping_segment_is_refused_on_every_route(scratch_server, route, form):
    """No route may resolve a request-supplied segment outside its family root.

    The assertion is on the STATUS, not on whether the canary leaked, because a
    refusal is the contract — a 404 that happens to miss is not containment, it
    is luck with a suffix. 403 is the escape refusal; 400 is a malformed
    segment. Anything 2xx is a successful escape.
    """
    _, method, template, body = route
    _, seg = form
    status, payload = raw_request(method, template.format(seg=seg), body)

    assert status is not None, (
        f"{method} {template.format(seg=seg)} returned NO HTTP RESPONSE — "
        "the connection closed with no status line"
    )
    assert status in (400, 403), (
        f"{method} {template.format(seg=seg)} -> {status}; expected 400/403 "
        f"(refusal). Body: {payload[:200]!r}"
    )
    assert CANARY_BODY not in payload, "the canary leaked in the response body"


def test_escape_write_creates_no_file_outside_the_root(scratch_server):
    """The write half, asserted on the FILESYSTEM — a status is not proof.

    A 403 with the file written anyway would pass the status assertions above
    and still be a full compromise, so the side effect is checked directly.
    """
    scratch = scratch_server["scratch"]
    for label, method, template, body in _ESCAPE_ROUTES:
        if method not in ("PUT", "DELETE"):
            continue
        raw_request(method, template.format(seg="../PWNED"), body)
        raw_request(method, template.format(seg="../../PWNED"), body)
        raw_request(method, template.format(seg="/PWNED"), body)

    stray = [p.name for p in scratch.iterdir()
             if p.name.startswith("PWNED")]
    assert stray == [], f"files written OUTSIDE the storage roots: {stray}"
    # and the canary must still be intact — DELETE escapes are as bad as writes
    assert (scratch / CANARY_NAME).read_bytes() == CANARY_BODY


def test_asset_list_route_still_refuses_escape(scratch_server):
    """The one route that ALREADY had the guard must keep it after the de-fork.

    This is the regression control on the refactor: the fix moves this handler
    onto the shared primitive, and moving a working guard is exactly where a
    de-fork can silently drop behaviour.
    """
    status, _ = raw_request("GET", "/api/v1/assets?prefix=../../etc")
    assert status == 403


class TestLegitimatePathsStillResolve:
    """POSITIVE CONTROL — required, not optional.

    The fleet's boot-time config fetch and every `bus://assets/<path>`
    resolution run through these routes. A containment guard that also rejects
    valid nested paths trades a vulnerability for a fleet-wide outage.
    """

    def test_nested_asset_put_then_get(self, scratch_server):
        status, _ = raw_request("PUT", "/api/v1/assets/album_art/current.jpg",
                                b"JPEGDATA")
        assert status == 201
        status, body = raw_request("GET", "/api/v1/assets/album_art/current.jpg")
        assert status == 200 and body == b"JPEGDATA"

    def test_deeply_nested_asset(self, scratch_server):
        status, _ = raw_request("PUT", "/api/v1/assets/a/b/c/d/deep.bin", b"ok")
        assert status == 201
        status, body = raw_request("GET", "/api/v1/assets/a/b/c/d/deep.bin")
        assert status == 200 and body == b"ok"

    def test_asset_path_containing_dots_but_not_traversal(self, scratch_server):
        """`..` inside a NAME is legitimate — the guard resolves, not greps."""
        status, _ = raw_request("PUT", "/api/v1/assets/fw/v1..2/image.bin", b"fw")
        assert status == 201
        status, body = raw_request("GET", "/api/v1/assets/fw/v1..2/image.bin")
        assert status == 200 and body == b"fw"

    def test_boot_config_fetch_roundtrip(self, scratch_server):
        """The fleet's boot path: config PUT, GET, checksum."""
        status, _ = raw_request("PUT", "/api/v1/config/panel-1", b"\xc1\x00binary")
        assert status == 201
        status, body = raw_request("GET", "/api/v1/config/panel-1")
        assert status == 200 and body == b"\xc1\x00binary"
        status, body = raw_request("GET", "/api/v1/config/panel-1/checksum")
        assert status == 200 and len(body) == 8

    def test_nested_listing_still_works(self, scratch_server):
        raw_request("PUT", "/api/v1/assets/legit/one.txt", b"1")
        status, body = raw_request("GET", "/api/v1/assets?prefix=legit")
        assert status == 200
        parsed = json.loads(body)
        assert parsed["prefix"] == "legit/"
        assert any(e["name"] == "one.txt" for e in parsed["entries"])

    def test_workload_put_and_list(self, scratch_server):
        status, _ = raw_request("PUT", "/api/v1/workloads/camera_frame_server",
                                json.dumps({"runtime": "python"}).encode())
        assert status == 201
        status, body = raw_request("GET", "/api/v1/workloads")
        assert status == 200
        assert any(w.get("id") == "camera_frame_server" for w in json.loads(body))


class TestRequestErrorBoundary:
    """[CXC-BUSSTORE-ERROR-BOUNDARY] — a request ALWAYS gets a status line.

    A dropped connection with no status line sends an operator to the network
    layer to debug what is a 400. The cost is hours, and the operator is the
    only one who pays it.
    """

    @pytest.mark.parametrize("path", [
        "/api/v1/namespaces/broken",
        "/api/v1/workloads/broken",
    ])
    def test_malformed_json_body_is_400_not_a_dropped_connection(
            self, scratch_server, path):
        status, body = raw_request("PUT", path, b"{not json at all")
        assert status is not None, (
            f"PUT {path} with a malformed body returned NO HTTP RESPONSE — "
            "the connection closed with no status line (RemoteDisconnected)"
        )
        assert status == 400, f"expected 400, got {status}: {body[:200]!r}"

    def test_one_corrupt_workload_does_not_deny_the_collection(
            self, scratch_server):
        """A single bad row must not take down the whole collection.

        The endpoint, not the service, is what dies today: `json.loads` inside
        the listing loop raises on the first corrupt file and the response never
        starts. One unreadable file denies every workload in the store.
        """
        data_dir = scratch_server["data_dir"]
        workloads = data_dir / "workloads"
        workloads.mkdir(parents=True, exist_ok=True)
        (workloads / "good_one.json").write_text('{"id": "good_one"}')

        status, body = raw_request("GET", "/api/v1/workloads")
        assert status == 200
        assert any(w.get("id") == "good_one" for w in json.loads(body))

        (workloads / "corrupt.json").write_text("{ this is not json")
        try:
            status, body = raw_request("GET", "/api/v1/workloads")
            assert status is not None, (
                "one corrupt file in workloads/ returned NO HTTP RESPONSE for "
                "the ENTIRE collection"
            )
            assert status == 200, (
                f"one corrupt file denied the whole collection: {status}")
            ids = [w.get("id") for w in json.loads(body)]
            assert "good_one" in ids, (
                "the readable workloads must still be served; one bad row is "
                f"not a collection failure. Got: {ids}")
        finally:
            (workloads / "corrupt.json").unlink()

    def test_skipped_rows_are_reported_not_silently_dropped(self, scratch_server):
        """Surviving a corrupt row must not make the loss INVISIBLE.

        Serving 9 of 10 with no signal is a silent-loss class: the caller
        cannot tell a partial listing from a complete one. The bare-array body
        is a specified contract ([CXC-BUSSTORE-API-WORKLOAD]), so the count
        rides on a header instead of changing the body shape.
        """
        data_dir = scratch_server["data_dir"]
        workloads = data_dir / "workloads"
        workloads.mkdir(parents=True, exist_ok=True)
        (workloads / "corrupt2.json").write_text("}{ nope")
        try:
            conn = http.client.HTTPConnection(HOST, TEST_PORT, timeout=5)
            conn.request("GET", "/api/v1/workloads")
            resp = conn.getresponse()
            resp.read()
            skipped = resp.getheader("X-ComposeIO-Skipped-Entries")
            conn.close()
            assert skipped is not None and int(skipped) >= 1, (
                "a skipped corrupt entry must be reported on the response, "
                "not silently dropped")
        finally:
            (workloads / "corrupt2.json").unlink()

    def test_unknown_route_is_still_404(self, scratch_server):
        """The boundary must not swallow the ordinary 404."""
        status, _ = raw_request("GET", "/api/v1/nonexistent")
        assert status == 404

    def test_health_still_ok(self, scratch_server):
        status, body = raw_request("GET", "/health")
        assert status == 200 and body == b"ok"
