"""The conformance script ([CXC-PC-CONFORMANCE-SCRIPT]) driven by a STUB Host that
implements only the protocol members ([CXC-PC-LIBRARY-CONTENTS] verify-machine),
at both isolation levels; exit 0 only when every step passes."""
from __future__ import annotations

import io

import pytest
from composebus.bus import ComposeIOBus

from compose_plugins import conformance, lifecycle
from compose_plugins.host import ConsentDecision, Host
from compose_plugins.lifecycle import PluginManager


class StubHost(Host):
    def __init__(self, root, level):
        super().__init__("composeio", state_root=root, minimum_isolation=level)
        self._bus = ComposeIOBus(bus_id="stub")

    def consent_gate(self, r):
        return ConsentDecision("granted" if r.accepted and r.operator else "refused",
                               r.operator or "stub:no-operator")

    def validate_section(self, s):
        return []

    def render_section(self, s):
        return []

    def bus(self):
        return self._bus


class Target:
    foreign_host = "gnomon"   # r24 foreign-host: REQUIRED of every target

    def __init__(self, isolation, workdir):
        self.host = StubHost(workdir, isolation)
        self.manager = PluginManager(self.host)
        self.bus = self.host.bus()
        self.isolation = isolation
        self.operator = "stub-operator"

    def close(self):
        self.manager.close()


@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_stub_host_passes_the_full_script(tmp_path, level):
    out = io.StringIO()
    rc = conformance.run(Target, level, out=out, workdir=tmp_path)
    text = out.getvalue()
    print(text[-600:])
    assert rc == 0, text
    assert "0 failed" in text and "FAIL" not in text


def test_the_script_exits_1_when_a_guard_is_removed(tmp_path, monkeypatch):
    """Negative control of the script: without the v0.1 requirements refusal,
    R15 must FAIL and the run must exit 1."""
    monkeypatch.setattr(lifecycle, "refuse_requirements", lambda m: None)
    out = io.StringIO()
    rc = conformance.run(Target, "inprocess", out=out, workdir=tmp_path)
    text = out.getvalue()
    assert rc == 1
    assert "STEP R15 requirements-refused-in-v0.1" in text
    assert text.split("STEP R15")[1].split("STEP")[0].count("-> FAIL") == 1


def test_cli_requires_target_and_isolation():
    with pytest.raises(SystemExit) as ei:
        conformance.main([])
    assert ei.value.code == 2


def test_network_watcher_does_see_a_lookup():
    """Negative control of NC7's instrument: the audit hook records a real
    getaddrinfo, so NC7's empty list is a measurement, not a blind spot."""
    import socket
    import sys
    sys.addaudithook(conformance._audit)
    conformance._NET_EVENTS.clear()
    conformance._NET_WATCH[0] = True
    try:
        try:
            socket.getaddrinfo("localhost", 9)
        except OSError:
            pass
    finally:
        conformance._NET_WATCH[0] = False
    assert "socket.getaddrinfo" in conformance._NET_EVENTS


def test_inet_probe_does_see_an_inet_socket():
    """Negative control of the local-only instrument: a TCP listener in this
    process IS reported by the same probe the conformance script uses."""
    import os
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        assert conformance._inet_fds(os.getpid()) != []
        assert conformance._listen_sockets() != set()
    finally:
        s.close()


def test_nc8_fails_when_the_permissions_guard_is_removed(tmp_path, monkeypatch):
    """[CXC-PC-NEGATIVE-CONTROLS] mutation check for NC8 (r21): without the
    permissions validation, NC8 must FAIL and the run must exit 1."""
    from compose_plugins import manifest
    monkeypatch.setattr(manifest, "_validate_permissions", lambda perms, refuse: None)
    out = io.StringIO()
    rc = conformance.run(Target, "inprocess", out=out, workdir=tmp_path)
    text = out.getvalue()
    assert rc == 1
    assert "STEP NC8 malformed-permission" in text
    assert text.split("STEP NC8")[1].split("STEP")[0].count("-> FAIL") == 1
