"""A minimal test Host for the wave-2 tests (library level, no ComposeIO)."""
from __future__ import annotations

import time

from composebus.bus import ComposeIOBus

from compose_plugins.host import ConsentDecision, Host
from compose_plugins.lifecycle import PluginManager

PING, PONG = "plugins/hello_world/ping", "plugins/hello_world/pong"


class TestHost(Host):
    __test__ = False

    def __init__(self, root, level="inprocess", auth=None, panels=()):
        super().__init__("testhost", state_root=root, minimum_isolation=level)
        self._bus = ComposeIOBus(bus_id="test")
        self.auth_hook = auth
        self.gate_calls = []
        self._panels = list(panels)
        self.statuses = []

    def consent_gate(self, request):
        self.gate_calls.append(request)
        op = request.operator or "nobody"
        return ConsentDecision("granted" if request.accepted else "refused", op)

    def validate_section(self, section):
        return []

    def render_section(self, section):
        return []

    def bus(self):
        return self._bus

    def publish_status(self, domain, status):
        self.statuses.append((domain, status))

    def extra_panels(self):
        return self._panels


def manifest_for_testhost(data: dict) -> dict:
    data["hosts"]["testhost"] = {}
    return data


#: every manager a test made; tests/conftest.py releases them after each test,
#: as the end of a host process would (one host per process in production)
MADE: list = []


def make(tmp_path, level="inprocess", **kw):
    host = TestHost(tmp_path / f"state-{level}", level, **kw)
    pm = PluginManager(host)
    MADE.append(pm)
    return host, pm


def install(pm, src):
    p = pm.prepare_install(src)
    pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    return p


def wait_for(pred, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def pongs(bus, count=1, expect=1):
    got = []
    bus.subscribe_events(PONG, lambda ns, et, d: got.append(dict(d)))
    for _ in range(count):
        bus.publish_event(PING, "ping", {})
    wait_for(lambda: len(got) >= expect)
    time.sleep(0.1)
    return got


def fixture_copy(tmp_path, version="v1", edit=None, name=None):
    """A copy of hello-world (declaring hosts.testhost too), optionally edited."""
    import json
    import shutil

    from compose_plugins.fixtures import package_dir
    dst = tmp_path / (name or f"pkg-{version}")
    shutil.copytree(package_dir(version), dst,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    mp = dst / "manifest.json"
    data = manifest_for_testhost(json.loads(mp.read_text()))
    if edit is not None:
        edit(data)
    mp.write_text(json.dumps(data, indent=2) + "\n")
    return dst
