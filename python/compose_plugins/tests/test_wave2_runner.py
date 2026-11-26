"""[CXC-PC-SUBPROCESS-RUNNER] and [CXC-PC-RUNNER-LOCAL-ONLY]: the runner over
an inherited AF_UNIX socketpair, newline-delimited JSON frames, crashes become
failed(<reason>), the host survives, the declared-publish boundary."""
from __future__ import annotations

import json
import os
import socket
import textwrap

import pytest
from composebus.bus import ComposeIOBus
from composebus.transport import BusMessage, MessageType

from compose_plugins import runner as R

from wave2_helpers import wait_for

PLUGINS = {
    "ok_plug": """
        from typing import ClassVar
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            name = "ok"
            def start(self, bus):
                self._bus = bus
                bus.subscribe_events("plugins/t/in", self._on)
                bus.publish_state("plugins/t/state", "up")
            def _on(self, ns, et, data):
                self._bus.publish_event("plugins/t/out", "echo", dict(data))
                self._bus.publish_event("plugins/t/undeclared", "x", {})
            def health_reason(self):
                return None
    """,
    "raise_plug": """
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                raise RuntimeError("boom in start")
    """,
    "exit_plug": """
        import os
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                os._exit(1)
    """,
    "degraded_plug": """
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                pass
            def health_reason(self):
                return "sensor offline"
    """,
    "raw_plug": """
        import json, socket
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                # a hostile child bypasses the child-side filter and writes raw
                for fd in range(3, 64):
                    try:
                        s = socket.socket(fileno=fd)
                    except OSError:
                        continue
                    if s.family == socket.AF_UNIX:
                        from composebus.transport import BusMessage, MessageType
                        frame = BusMessage(namespace="_server/evil", type=MessageType.STATE,
                                           value=1).to_json()
                        s.sendall((json.dumps(frame) + "\\n").encode())
                        s.detach()
                        return
                    s.detach()
    """,
    "badctl_plug": """
        import json, sys, socket
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                # find the inherited link and write an unlisted control frame
                for fd in range(3, 64):
                    try:
                        s = socket.socket(fileno=fd)
                    except OSError:
                        continue
                    if s.family == socket.AF_UNIX:
                        s.sendall(b'{"_ctl": "frobnicate"}\\n')
                        s.detach()
                        return
                    s.detach()
    """,
}


@pytest.fixture
def plugdir(tmp_path):
    for name, src in PLUGINS.items():
        (tmp_path / f"{name}.py").write_text(textwrap.dedent(src))
    return tmp_path


def _run(plugdir, module, bus=None, timeout=10.0, **kw):
    statuses = []
    sp = R.SubprocessPlugin(
        domain="t", entry=f"{module}:P", sys_path=[str(plugdir)], config={},
        bus=bus, publishes={("plugins/t/state", "STATE"), ("plugins/t/out", "EVENT")},
        subscribes={("plugins/t/in", "EVENT")}, on_status=statuses.append,
        start_timeout=timeout, **kw)
    sp.start()
    return sp, statuses


def test_socketpair_is_af_unix_and_refuses_otherwise(monkeypatch):
    a, b = R.make_socketpair()
    assert a.family == socket.AF_UNIX == b.family
    a.close()
    b.close()

    class Fake:
        family = socket.AF_INET

        def close(self):
            pass
    monkeypatch.setattr(socket, "socketpair", lambda *x, **k: (Fake(), Fake()))
    with pytest.raises(R.RunnerRefused) as ei:
        R.make_socketpair()
    print(ei.value)
    assert "never falls back to TCP" in str(ei.value)


def test_ctl_vocabulary_is_exactly_the_five():
    assert set(R.CTL_VALUES) == {"configure", "start", "stop", "health", "degraded"}
    with pytest.raises(R.RunnerRefused):
        R.ctl_frame("frobnicate")
    assert json.loads(R.ctl_frame("stop")) == {"_ctl": "stop"}


def test_bus_frame_round_trips_through_busmessage_json():
    msg = BusMessage(namespace="plugins/t/out", type=MessageType.EVENT, event_type="e",
                     data={"n": 1}, source="s")
    line = R.frame_from_message(msg)
    assert line.endswith(b"\n") and line.count(b"\n") == 1
    back = BusMessage.from_json(json.loads(line))
    assert (back.namespace, back.type, back.event_type, back.data) == \
        (msg.namespace, msg.type, msg.event_type, msg.data)


def test_round_trip_through_a_child_and_the_publish_boundary(plugdir):
    bus = ComposeIOBus(bus_id="host")
    got = []
    bus.subscribe_events("plugins/t/out", lambda ns, et, d: got.append(d))
    sp, statuses = _run(plugdir, "ok_plug", bus=bus)
    try:
        assert sp.pid != os.getpid()
        assert wait_for(lambda: bus.get_state("plugins/t/state") == "up")
        bus.publish_event("plugins/t/in", "ping", {"k": 1})
        assert wait_for(lambda: got == [{"k": 1}])
        assert bus.get_namespace("plugins/t/undeclared") is None   # child filtered it
        assert statuses[-1] == "running"
    finally:
        sp.stop()
    assert sp.status == "stopped"


def test_raise_in_start_is_failed_with_the_reason(plugdir):
    sp, statuses = _run(plugdir, "raise_plug")
    assert wait_for(lambda: sp.status.startswith("failed"))
    print(sp.status)
    assert sp.status.startswith("failed(exit code 3") and "boom in start" in sp.status


def test_os_exit_is_failed_with_the_exit_code(plugdir):
    sp, _ = _run(plugdir, "exit_plug")
    assert wait_for(lambda: sp.status.startswith("failed"))
    print(sp.status)
    assert sp.status.startswith("failed(exit code 1")


def test_kill_9_is_failed_naming_the_signal_and_a_sibling_keeps_working(plugdir):
    bus = ComposeIOBus(bus_id="host")
    got = []
    bus.subscribe_events("plugins/t/out", lambda ns, et, d: got.append(d))
    victim, _ = _run(plugdir, "degraded_plug")
    sibling, _ = _run(plugdir, "ok_plug", bus=bus)
    try:
        os.kill(victim.pid, 9)
        assert wait_for(lambda: victim.status.startswith("failed"))
        print(victim.status)
        assert victim.status == "failed(killed by signal SIGKILL)"
        bus.publish_event("plugins/t/in", "ping", {"after": "crash"})
        assert wait_for(lambda: {"after": "crash"} in got)
    finally:
        sibling.stop()


def test_degraded_arrives_as_a_ctl_frame(plugdir):
    sp, statuses = _run(plugdir, "degraded_plug")
    try:
        assert wait_for(lambda: sp.status == "degraded(sensor offline)")
    finally:
        sp.stop()


def test_an_unlisted_ctl_from_the_child_is_refused_loudly(plugdir):
    sp, _ = _run(plugdir, "badctl_plug")
    assert wait_for(lambda: sp.status.startswith("failed"))
    print(sp.status)
    assert "protocol: control frame _ctl='frobnicate' refused" in sp.status


def test_child_holds_no_inet_socket_and_host_opens_no_listener(plugdir):
    psutil = pytest.importorskip("psutil")
    me = psutil.Process(os.getpid())
    before = {c.laddr for c in me.net_connections(kind="inet") if c.status == "LISTEN"}
    sp, _ = _run(plugdir, "ok_plug", bus=ComposeIOBus(bus_id="h"))
    try:
        child = psutil.Process(sp.pid)
        assert child.net_connections(kind="inet") == []
        unix = [c for c in child.net_connections(kind="unix")]
        assert unix, "the child should hold the inherited AF_UNIX link"
        after = {c.laddr for c in me.net_connections(kind="inet") if c.status == "LISTEN"}
        assert after == before
    finally:
        sp.stop()


def test_no_start_handshake_is_failed(plugdir, tmp_path):
    (tmp_path / "hang_plug.py").write_text(textwrap.dedent("""
        import time
        from composebus.plugin import ComposePlugin
        class P(ComposePlugin):
            def start(self, bus):
                time.sleep(30)
    """))
    sp, _ = _run(tmp_path, "hang_plug", timeout=1.0)
    print(sp.status)
    assert sp.status == "failed(no start handshake within 1.0s)"


JUNK = """
import json, socket
from composebus.plugin import ComposePlugin
class P(ComposePlugin):
    def start(self, bus):
        for fd in range(3, 64):
            try:
                s = socket.socket(fileno=fd)
            except OSError:
                continue
            if s.family == socket.AF_UNIX:
                s.sendall(b'{"namespace": "x"}\\n')
                s.detach()
                return
            s.detach()
"""


def test_a_malformed_frame_from_the_child_is_refused_loudly(tmp_path):
    (tmp_path / "junk_plug.py").write_text(JUNK)
    sp, _ = _run(tmp_path, "junk_plug")
    assert wait_for(lambda: sp.status.startswith("failed"))
    print(sp.status)
    assert sp.status.startswith("failed(protocol: bad bus frame")


def test_host_drops_an_undeclared_frame_from_a_hostile_child(plugdir):
    """The host enforces the declared publishes itself, not only the child."""
    bus = ComposeIOBus(bus_id="host")
    sp, _ = _run(plugdir, "raw_plug", bus=bus)
    try:
        assert wait_for(lambda: "_server/evil:STATE" in sp.refused_frames)
        print(sp.refused_frames)
        assert bus.get_state("_server/evil") is None
    finally:
        sp.stop()
