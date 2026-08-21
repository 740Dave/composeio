"""`composeio fleet` polls whatever ComposeBus it is pointed at — including a
bus with no broker in it.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS]. The sibling of
``python/tests/render/test_substrate_choice_at_the_waist.py``, which forbids the
defect repo-wide; this file proves the FIX WORKS for the one module that had it.

THE DEFECT THIS PINS SHUT, because the irony is the useful part. `fleet.py`'s own
docstring has said since it was written that the announce poll "works identically
on MQTT, UART and CAN because it is an ordinary bus message" — and thirty lines
below that sentence it constructed ``MQTTTransport(broker=…, port=…)``. The claim
was TRUE: every transport call the module makes (``subscribe_pattern``,
``subscribe_all``, ``send``, ``start``, ``stop``, ``is_connected``) is declared on
the `Transport` ABC. Only the construction was wrong. So the tool an operator
reaches for when a device has gone missing was structurally incapable of seeing a
CAN or UART device — exactly the population ``_device/<id>/*`` was designed
substrate-agnostic to reach, and exactly the ones least likely to be reachable by
any other means.

The paho seam lint was green throughout, because this module never imported paho.
That is why door 2 exists.

WHY THE ASSERTIONS ARE SHAPED LIKE THIS
---------------------------------------
Three levels, and the weakest is deliberately still here:

  * a REAL non-MQTT transport, built by the registry from a spec, driven end to
    end with no broker reachable and nothing listening — proves the wiring;
  * a FAKE transport that implements only the `Transport` ABC and delivers an
    announcement — proves the REPORT is produced from ABC calls alone, which is
    the thing "works identically on MQTT, UART and CAN" actually claims. A CAN
    round-trip cannot prove this today: the CAN wire id is a 16-bit hash of the
    full namespace, so ``_device/<id>/*`` names do not survive the trip
    (workstream G). Using CAN as the proof would have quietly tested nothing;
  * a SOURCE assertion that the concrete class name is gone, which is the one an
    accidental revert trips first.

NO HARDWARE, NO BROKER, NO NETWORK. The CAN transport is built on the `loopback`
backend (the same hardware-free path `transport_registry._selftest` check (8)
uses) and the fake wire is a python object. `mqtt-broker.local` is a LIVE PRODUCTION
broker and nothing here may ever touch it.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from composeio import device_announce as da
from composeio import fleet
from composeio.transport import BusMessage, MessageType, Transport
from composeio.transport_registry import UnknownTransportError

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FLEET_PY = _REPO_ROOT / "python" / "composeio" / "fleet.py"


# --------------------------------------------------------------------------- #
# a wire that is ONLY the ABC
# --------------------------------------------------------------------------- #
class _FakeWire(Transport):
    """Implements the `Transport` ABC and nothing else — no broker, no socket.

    It is not a mock of MQTT; it is a substrate that happens to be a list. If
    `fleet` can produce a full report over this, it is using the ABC and only the
    ABC, which is the entire claim under test.
    """

    def __init__(self, replies: list[BusMessage] | None = None):
        self.sent: list[BusMessage] = []
        self.patterns: list[str] = []
        self.started = False
        self.stopped = False
        self._all: list = []
        self._replies = replies or []

    # --- the parts fleet uses ------------------------------------------- #
    def subscribe_pattern(self, pattern: str):
        self.patterns.append(pattern)

    def subscribe_all(self, callback):
        self._all.append(callback)

    def send(self, message: BusMessage):
        self.sent.append(message)
        # A real bus answers a broadcast poll. So does this one.
        if message.namespace == da.POLL_BROADCAST:
            for reply in self._replies:
                for cb in self._all:
                    cb(reply)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    @property
    def is_connected(self) -> bool:
        return self.started and not self.stopped

    # --- the rest of the ABC, unused by fleet --------------------------- #
    def subscribe(self, namespace: str, callback):
        self._all.append(callback)

    def unsubscribe(self, namespace: str, callback):
        pass


def _announcement(device_id: str, leaf: str, value) -> BusMessage:
    return BusMessage(namespace=da.announce_namespace(device_id, leaf),
                      value=value, type=MessageType.STATE, source=device_id,
                      scope="global")


# --------------------------------------------------------------------------- #
# the report is produced from ABC calls alone
# --------------------------------------------------------------------------- #
def test_a_device_announcing_over_a_non_mqtt_wire_appears_in_the_report():
    """The claim in `fleet.py`'s docstring, finally executable.

    Nothing here is MQTT-shaped: no broker, no topic, no client id, no paho. A
    device announces over a wire that is a python list, and `composeio fleet`
    reports it — which is what "an ordinary bus message" has to mean if it means
    anything.
    """
    wire = _FakeWire(replies=[
        _announcement("bench-can-1", "heartbeat", 41),
        _announcement("bench-can-1", "build", 0x1234ABCD),
        _announcement("bench-can-1", "scene", 0x00FF),
    ])
    reports, warnings = fleet.poll_fleet(transport=wire, wait=0.0)

    assert warnings == [], warnings
    assert set(reports) == {"bench-can-1"}, reports
    rep = reports["bench-can-1"]
    assert rep.uptime_s == 41
    assert rep.build == 0x1234ABCD
    assert rep.scene == 0x00FF

    # ...and it was driven the way the ABC says, in the order the module's own
    # comment insists on: the wildcard subscription is registered BEFORE start(),
    # so no device can answer into a socket nobody is reading yet.
    assert wire.patterns == ["_device/*"]
    assert wire.started and wire.stopped
    assert [m.namespace for m in wire.sent] == [da.POLL_BROADCAST]

    # The rendered report names the DEVICE, not a substrate.
    text = fleet.format_report(reports, warnings, {}, wait=0.0, target="fake-wire")
    assert "bench-can-1" in text
    assert "1 device(s) announced." in text


def test_an_injected_transport_means_this_module_constructs_nothing():
    """The `--no-poll` path too, since it is the one an operator uses to watch
    the PERIODIC heartbeat — the actual liveness signal."""
    wire = _FakeWire()
    reports, warnings = fleet.poll_fleet(transport=wire, wait=0.0, poll=False)
    assert reports == {} and warnings == []
    assert wire.sent == [], "a --no-poll run must publish NOTHING"
    assert wire.started and wire.stopped


# --------------------------------------------------------------------------- #
# a REAL non-MQTT transport, built from a spec, with no broker anywhere
# --------------------------------------------------------------------------- #
def test_it_runs_end_to_end_on_a_real_can_transport_with_no_broker():
    """Built by `transport_registry` from `type: can` on the `loopback` backend —
    the same hardware-free path the registry's own selftest uses.

    This is the wiring proof rather than the reporting proof: no device answers,
    because nothing is on the bus. What it establishes is that the whole path
    (build -> subscribe -> start -> poll -> listen -> stop) completes over a
    substrate with no broker in it, which is precisely what the module could not
    do before.
    """
    t0 = time.monotonic()
    reports, warnings = fleet.poll_fleet(
        spec={"type": "can", "channel": "loopback"},
        wait=0.05, connect_timeout=1.0)
    assert time.monotonic() - t0 < 10.0, "the CAN poll should not hang"
    assert reports == {}
    # No warning: the transport built and reported itself connected. A warning
    # here would mean the loopback backend regressed, which is worth knowing.
    assert warnings == [], warnings


def test_no_broker_and_no_paho_are_reachable_on_the_can_path():
    """Run in a SUBPROCESS, because import state is global and a sibling test
    that touched MQTT would make an in-process check pass for the wrong reason.

    This is the assertion the old code could not have passed under any argument:
    `import composeio.fleet` used to import `transport_mqtt` at module scope.
    """
    src = (
        "import sys\n"
        "from composeio import fleet\n"
        "assert 'composeio.transport_mqtt' not in sys.modules, 'imported at module scope'\n"
        "fleet.poll_fleet(spec={'type': 'can', 'channel': 'loopback'},\n"
        "                 wait=0.05, connect_timeout=1.0)\n"
        "assert 'paho' not in sys.modules, sorted(m for m in sys.modules if 'paho' in m)\n"
        "assert 'composeio.transport_mqtt' not in sys.modules\n"
        "print('CLEAN')\n"
    )
    env_path = str(_REPO_ROOT / "python")
    proc = subprocess.run([sys.executable, "-c", src], capture_output=True,
                          text=True, timeout=120,
                          env={"PATH": "/usr/bin:/bin", "PYTHONPATH": env_path,
                               "HOME": str(Path.home())})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CLEAN" in proc.stdout


# --------------------------------------------------------------------------- #
# the substrate arrives as a VALUE
# --------------------------------------------------------------------------- #
def test_the_spec_builder_takes_the_type_from_its_caller():
    assert fleet.transport_spec("uart", options={"port": "/dev/ttyUSB0",
                                                 "baudrate": "115200"}) == {
        "type": "uart", "client_id": fleet._client_id_stem(),
        "port": "/dev/ttyUSB0", "baudrate": "115200"}

    # `--broker`/`--port` are MQTT-shaped and must not be smuggled into a CAN
    # spec, where `port` means a serial device and would be a silent nonsense.
    can = fleet.transport_spec("can", broker="mqtt-broker.local", port=1883,
                               options={"channel": "can0"})
    assert can["type"] == "can" and can["channel"] == "can0"
    assert "broker" not in can and "port" not in can

    mqtt = fleet.transport_spec("mqtt", broker="bench.local", port=1884)
    assert mqtt["broker"] == "bench.local" and mqtt["port"] == 1884


def test_the_default_type_comes_from_the_environment(monkeypatch):
    """`COMPOSEIO_TRANSPORT` is read at import, beside `COMPOSEIO_MQTT_BROKER`.

    Asserted through the module attribute rather than by re-importing, because
    the point is that the value is a VARIABLE an operator can move — not that
    `importlib.reload` works.
    """
    monkeypatch.setattr(fleet, "DEFAULT_TRANSPORT_TYPE", "can")
    assert fleet.transport_spec()["type"] == "can"
    assert fleet.transport_spec("uart")["type"] == "uart", "an explicit type wins"


def test_the_client_id_survived_the_conversion_byte_for_byte():
    """`transport_registry._mqtt` appends `-<pid>` itself, so the spec must carry
    the STEM. Passing the already-suffixed id would yield `…-<pid>-<pid>`, and two
    MQTT peers sharing an id kick each other off the broker (rc=7) — the failure
    `_client_id`'s docstring exists to prevent, on a LIVE PRODUCTION broker.
    """
    import os
    assert fleet._client_id() == f"{fleet._client_id_stem()}-{os.getpid()}"
    spec = fleet.transport_spec("mqtt")
    assert spec["client_id"] == fleet._client_id_stem()
    assert not spec["client_id"].endswith(str(os.getpid()))


def test_an_unknown_substrate_raises_instead_of_polling_into_the_void():
    """The registry's rule 1, reachable from the fleet tool.

    A typo'd `--transport type=mqqt` must be a startup error. The alternative —
    an empty report from a poll nobody could hear — is indistinguishable from a
    dead fleet, which is the exact ambiguity this whole workstream removes.
    """
    with pytest.raises(UnknownTransportError):
        fleet.poll_fleet(spec={"type": "mqqt", "broker": "x"}, wait=0.0)


def test_a_known_substrate_with_a_dead_backend_is_a_LOUD_empty_report():
    """The registry's rule 2. `build()` returns None, and fleet must say WHY.

    `/dev/null` is not a serial port, so `type: uart` builds and then fails to
    configure. The report is empty either way; the difference between "the tool
    could not listen" and "the fleet is silent" is the whole product.
    """
    reports, warnings = fleet.poll_fleet(
        spec={"type": "uart", "port": "/dev/null", "baudrate": 115200},
        wait=0.0, connect_timeout=0.3)
    assert reports == {}
    assert len(warnings) == 1
    assert "EMPTY BECAUSE OF THE TOOL" in warnings[0]
    assert "uart" in warnings[0], (
        "the warning must name the WIRE that failed; it used to print "
        "`broker:port` unconditionally, which on a UART poll names a host "
        "nobody contacted")


# --------------------------------------------------------------------------- #
# the report describes the wire it actually listened to
# --------------------------------------------------------------------------- #
def test_the_header_names_the_wire_not_a_broker_it_never_contacted():
    assert fleet.describe_spec({"type": "mqtt", "broker": "h", "port": 1883}) \
        == "mqtt://h:1883"
    assert fleet.describe_spec({"type": "can", "channel": "can0",
                                "client_id": "x"}) == "can(channel=can0)"

    text = fleet.format_report({}, [], {}, wait=1.0, target="can(channel=can0)")
    assert "can(channel=can0)" in text.splitlines()[0]
    assert "mqtt-broker.local" not in text

    # `broker=` still works for every pre-existing caller, and is the fallback.
    assert "bench.local" in fleet.format_report({}, [], {}, wait=1.0,
                                                broker="bench.local")


def test_the_json_report_records_which_wire_it_polled():
    """An inventory that does not say which substrate it came from cannot be
    compared with one taken on another, which is the point of being able to poll
    more than one."""
    import json
    out = json.loads(fleet.report_json({}, [], {}, target="can(channel=can0)"))
    assert out["target"] == "can(channel=can0)"
    # ...and it is ABSENT rather than guessed when the caller did not say.
    assert "target" not in json.loads(fleet.report_json({}, [], {}))


# --------------------------------------------------------------------------- #
# the CLI is the one layer allowed to NAME a substrate
# --------------------------------------------------------------------------- #
def test_the_cli_parses_a_substrate_and_its_parameters():
    from composeio.cli import _parse_transport_arg

    assert _parse_transport_arg(None) == (None, {})
    assert _parse_transport_arg("") == (None, {})
    assert _parse_transport_arg("can") == ("can", {})            # bare word
    assert _parse_transport_arg("type=can") == ("can", {})
    assert _parse_transport_arg(
        "type=uart,port=/dev/ttyUSB0,baudrate=115200") == (
            "uart", {"port": "/dev/ttyUSB0", "baudrate": "115200"})
    # values stay STRINGS — the registry factories already coerce, and a second
    # divergent coercion here is how `baud=`/`baudrate=` bugs get made.
    assert _parse_transport_arg("type=can,bitrate=250000")[1] == {"bitrate": "250000"}
    # a `=` in the VALUE survives (device paths and URLs contain them).
    assert _parse_transport_arg("type=replay,capture=a=b")[1] == {"capture": "a=b"}


# --------------------------------------------------------------------------- #
# the source assertion — the one an accidental revert trips first
# --------------------------------------------------------------------------- #
def test_fleet_constructs_no_concrete_transport_class():
    """The local canary — the first thing to go red if a merge reverts the wiring.

    AST, NOT GREP, and this file learned that the hard way rather than inheriting
    it. The first draft asserted ``"MQTTTransport(" not in src`` and went RED
    immediately — on `fleet.py`'s own docstring, which now NARRATES the old
    construction in order to explain why it was wrong. A grep-shaped canary would
    have forced the fix to be "stop describing the defect", i.e. delete the
    explanation to satisfy the check. Same finding the sibling guard records
    against `config_server.py:235`, reproduced here within an hour of writing it.

    The repo-wide version lives in `test_substrate_choice_at_the_waist.py`; this
    stays because that guard is a whole-repo walk with an allow-list, and a local
    assertion on one file cannot be silenced by adding a line to a table.
    """
    import ast

    tree = ast.parse(_FLEET_PY.read_text(encoding="utf-8"), filename=str(_FLEET_PY))
    constructed = {
        f.id if isinstance(f, ast.Name) else f.attr
        for f in (n.func for n in ast.walk(tree) if isinstance(n, ast.Call))
        if isinstance(f, (ast.Name, ast.Attribute))
    }
    named = sorted(c for c in constructed
                   if c.endswith("Transport") and c != "Transport")
    assert not named, f"fleet.py constructs {named} again"

    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import)
                for a in n.names}
    imported |= {n.module or "" for n in ast.walk(tree)
                 if isinstance(n, ast.ImportFrom)}
    assert not any(m.split(".")[0] == "paho" for m in imported), sorted(imported)
    assert not any(m.startswith("composeio.transport_") and m != "composeio.transport_registry"
                   for m in imported), (
        f"fleet.py imports a concrete transport module again: {sorted(imported)}")
    assert "composeio.transport_registry" in imported, (
        "fleet.py no longer reaches the registry — if the transport now arrives "
        "some other way, re-read this whole file before deleting the assertion")
