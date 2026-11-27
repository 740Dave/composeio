"""paho 2.x construction, and a bus_client that survives a broker down at start.

w-26, from the transport review that preceded PR #16.

DEFECT 1 — paho 2.x. `pyproject.toml` allows `paho-mqtt>=1.6.0`, so 2.x installs.
paho 2.0.0 made `callback_api_version` the REQUIRED first parameter of
`Client.__init__` (read from the v2.0.0 source, client.py line 731); every site
here called `mqtt.Client(client_id=..., protocol=...)`, which on 2.0 is a
TypeError before any connection is attempted. 2.1.0 gave the parameter a
default of VERSION1, so there the same call works with a DeprecationWarning.
The fix is one helper at the paho seam, `transport_mqtt.new_paho_client`, that
passes `CallbackAPIVersion.VERSION1` when the module has the enum (so the
existing 1.x-shaped callbacks keep their signatures) and the old form when it
does not. These tests drive the helper AND every construction site against fake
modules shaped like each API, because only one paho is installed here.

DEFECT 2 — bus_client connect was one-shot. `acquire()` and `subscribe()`
called the blocking `connect()`: a broker down at start raised to the caller and
nothing retried. They now go through `transport_mqtt.connect_with_backoff`, the
same `reconnect_delay_set` + `connect_async` + `loop_start` sequence
`MQTTTransport.start()` uses since PR #16. Because the first connect is now made
by paho's loop thread, a `subscribe()` issued before CONNACK reaches no broker
(paho returns MQTT_ERR_NO_CONN when it has no socket), so the router re-issues
every filter on each CONNACK. The fake below models exactly that paho fact.
"""
from __future__ import annotations

import enum
import io
import socket
import time
import tokenize
from pathlib import Path

import pytest

paho = pytest.importorskip("paho.mqtt.client")

from composeio import bus_client as bc                      # noqa: E402
from composeio.transport_mqtt import MQTTTransport           # noqa: E402

PYTHON_ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Fakes shaped like each paho API generation
# --------------------------------------------------------------------------- #

class FakeCallbackAPIVersion(enum.Enum):
    VERSION1 = 1
    VERSION2 = 2


class _RecordingBase:
    instances: list = []

    def _record(self, client_id, protocol):
        self.client_id = client_id
        self.protocol = protocol
        self.on_connect = None
        self.on_connect_fail = None
        self.on_message = None
        self.connect_args = None
        self.reconnect_delay = None
        self.looping = False
        type(self).instances.append(self)

    def username_pw_set(self, u, p=None): pass
    def will_set(self, *a, **kw): pass
    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)
    def connect(self, host, port=1883, keepalive=60, **kw):
        self.connect_args = (host, port, keepalive, kw)
    def connect_async(self, host, port=1883, keepalive=60, **kw):
        self.connect_args = (host, port, keepalive, kw)
    def loop_start(self): self.looping = True
    def loop_stop(self): self.looping = False
    def disconnect(self): pass
    def subscribe(self, *a, **kw): return (4, None)
    def message_callback_add(self, *a): pass
    def publish(self, *a, **kw): pass


class Paho1Client(_RecordingBase):
    """paho 1.6.1's signature, copied from the installed client.py."""
    instances: list = []

    def __init__(self, client_id="", clean_session=None, userdata=None,
                 protocol=4, transport="tcp", reconnect_on_failure=True):
        self._record(client_id, protocol)
        self.callback_api_version = None


class Paho20Client(_RecordingBase):
    """paho 2.0.0's signature: `callback_api_version` is REQUIRED and first,
    and anything that is not a CallbackAPIVersion member is a ValueError
    (v2.0.0 client.py lines 729-770)."""
    instances: list = []

    def __init__(self, callback_api_version, client_id="", clean_session=None,
                 userdata=None, protocol=4, transport="tcp",
                 reconnect_on_failure=True, manual_ack=False):
        if callback_api_version not in (FakeCallbackAPIVersion.VERSION1,
                                        FakeCallbackAPIVersion.VERSION2):
            raise ValueError("Unsupported callback API version: version 2.0 "
                             "added a callback_api_version")
        self._record(client_id, protocol)
        self.callback_api_version = callback_api_version


class FakePaho1Module:
    MQTTv311 = 4
    MQTTv5 = 5
    Client = Paho1Client


class FakePaho2Module:
    MQTTv311 = 4
    MQTTv5 = 5
    CallbackAPIVersion = FakeCallbackAPIVersion
    Client = Paho20Client


@pytest.fixture(autouse=True)
def _clean():
    bc._reset_for_tests()
    Paho1Client.instances = []
    Paho20Client.instances = []
    yield
    bc._reset_for_tests()


@pytest.fixture
def paho_2(monkeypatch):
    """Make the REAL `paho.mqtt.client` module look like paho 2.0."""
    monkeypatch.setattr(paho, "Client", Paho20Client)
    monkeypatch.setattr(paho, "CallbackAPIVersion", FakeCallbackAPIVersion,
                        raising=False)
    return Paho20Client


# --------------------------------------------------------------------------- #
# 1. The helper, against both API shapes
# --------------------------------------------------------------------------- #

def test_helper_builds_a_1x_client_with_the_old_form():
    from composeio.transport_mqtt import new_paho_client
    c = new_paho_client(mqtt=FakePaho1Module, client_id="one", protocol=5)
    assert isinstance(c, Paho1Client)
    assert (c.client_id, c.protocol) == ("one", 5)


def test_helper_builds_a_2x_client_with_callback_api_version1():
    from composeio.transport_mqtt import new_paho_client
    c = new_paho_client(mqtt=FakePaho2Module, client_id="two", protocol=5)
    assert isinstance(c, Paho20Client)
    assert c.callback_api_version is FakeCallbackAPIVersion.VERSION1, \
        "VERSION1 keeps the existing (client, userdata, flags, rc, props) callbacks"
    assert (c.client_id, c.protocol) == ("two", 5)


def test_helper_requires_an_explicit_protocol():
    """The v5 grep guard reads `protocol=` at each `mqtt.Client(` call. Inside
    the helper that call is generic, so the requirement moves to the helper's
    signature: no caller can take paho's 3.1.1 default by omission."""
    from composeio.transport_mqtt import new_paho_client
    with pytest.raises(TypeError):
        new_paho_client(mqtt=FakePaho1Module, client_id="x")


def test_the_fake_2x_shape_rejects_the_old_call():
    """CONTROL (accepted as A2), a negative control of the fake itself: if the
    2.0 fake accepted the 1.x call, the site tests below would pass on the
    unfixed code and measure nothing. It passes on main by design."""
    with pytest.raises(TypeError):
        Paho20Client(client_id="x", protocol=5)


# --------------------------------------------------------------------------- #
# 1b. Every construction site, on a paho that looks like 2.0
# --------------------------------------------------------------------------- #

def test_bus_client_acquire_constructs_on_paho_2(paho_2):
    bc.acquire("mqtt://broker.invalid:1883", label="p2")
    (c,) = paho_2.instances
    assert c.callback_api_version is FakeCallbackAPIVersion.VERSION1
    assert c.protocol == paho.MQTTv5


def test_bus_client_subscribe_constructs_on_paho_2(paho_2):
    bc.subscribe("mqtt://broker.invalid:1883", "a/#", lambda c, u, m: None,
                 label="p2")
    (c,) = paho_2.instances
    assert c.callback_api_version is FakeCallbackAPIVersion.VERSION1
    assert c.protocol == paho.MQTTv5


def test_mqtt_transport_constructs_on_paho_2(paho_2):
    t = MQTTTransport(broker="broker.invalid", client_id="p2")
    t.start()
    assert t._client is not None, "start() returned without a client"
    (c,) = paho_2.instances
    assert c.callback_api_version is FakeCallbackAPIVersion.VERSION1
    assert c.client_id == "p2" and c.protocol == paho.MQTTv5


def _code_only(src: str) -> str:
    """Blank comments and strings so prose ABOUT `mqtt.Client(` is not a hit."""
    out = src.splitlines(keepends=True)
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return src
    for tok in toks:
        if tok.type not in (tokenize.COMMENT, tokenize.STRING):
            continue
        (r1, c1), (r2, c2) = tok.start, tok.end
        for r in range(r1, r2 + 1):
            line = out[r - 1]
            a = c1 if r == r1 else 0
            b = c2 if r == r2 else len(line.rstrip("\n"))
            out[r - 1] = line[:a] + " " * max(0, b - a) + line[b:]
    return "".join(out)


_CLIENT_ATTR_RE = __import__("re").compile(r"\b(?:mqtt|paho)\.Client\s*\(")


def _paho_constructions(src: str) -> int:
    """How many paho ``Client(...)`` constructions `src` makes. Two detectors, and
    the larger count wins.

    WHAT IT CATCHES:
      * (AST, following the import) ``import paho.mqtt.client as X`` then
        ``X.Client(...)``; ``from paho.mqtt import client [as X]`` then
        ``X.Client(...)``; ``from paho.mqtt.client import Client [as Y]`` or
        ``import *`` then ``Y(...)``; and the full dotted
        ``paho.mqtt.client.Client(...)`` after ``import paho...``. Imports nested
        in functions (lazy imports) count.
      * (regex, over code with comments and strings blanked) any
        ``mqtt.Client(`` or ``paho.Client(`` call, whatever the name is bound to.
        That covers a module passed in as a parameter named ``mqtt`` (the helper
        itself), and a module from importlib or ``__import__`` ONLY when it is
        bound to a name spelled ``mqtt`` or ``paho``.

    WHAT IT DOES NOT CATCH (candidate review R2-N2; the ratchet was deliberately
    not extended to these):
      * attribute aliasing: ``C = mqtt.Client`` then ``C(...)``;
      * ``getattr(mqtt, "Client")(...)``;
      * a module from importlib or ``__import__`` bound to any other name, for
        example ``m = importlib.import_module("paho.mqtt.client"); m.Client()``;
      * submodule-alias attribute chains: ``import paho.mqtt as pm`` then
        ``pm.client.Client(...)``;
      * subclassing: ``class Mine(mqtt.Client): ...`` then ``Mine(...)``.

    Candidate review note 5: the regex alone missed ``from paho.mqtt.client
    import Client`` and every alias; `test_the_detector_*` below is its
    negative-control fixture.
    """
    import ast

    regex_n = len(_CLIENT_ATTR_RE.findall(_code_only(src)))
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return regex_n
    modules, classes, dotted_root = set(), set(), False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "paho.mqtt.client" and a.asname:
                    modules.add(a.asname)
                elif a.name.startswith("paho") and not a.asname:
                    dotted_root = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "paho.mqtt":
                for a in node.names:
                    if a.name == "client":
                        modules.add(a.asname or "client")
            elif node.module == "paho.mqtt.client":
                for a in node.names:
                    if a.name in ("Client", "*"):
                        classes.add(a.asname or "Client")

    def dotted(expr) -> str:
        parts = []
        while isinstance(expr, ast.Attribute):
            parts.append(expr.attr)
            expr = expr.value
        if isinstance(expr, ast.Name):
            parts.append(expr.id)
        return ".".join(reversed(parts))

    ast_n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in classes:
            ast_n += 1
        elif isinstance(f, ast.Attribute) and f.attr == "Client":
            owner = dotted(f.value)
            if owner in modules or (dotted_root and owner == "paho.mqtt.client"):
                ast_n += 1
    return max(ast_n, regex_n)


DETECTOR_CASES = [
    # (source, constructions) -- the aliased forms are the ones the regex missed
    ("from paho.mqtt.client import Client\nc = Client()\n", 1),
    ("from paho.mqtt.client import Client as PC\nc = PC('x')\n", 1),
    ("from paho.mqtt.client import *\nc = Client()\n", 1),
    ("import paho.mqtt.client as broker\nc = broker.Client()\n", 1),
    ("from paho.mqtt import client as pm\nc = pm.Client()\n", 1),
    ("from paho.mqtt import client\nc = client.Client()\n", 1),
    ("import paho.mqtt.client\nc = paho.mqtt.client.Client()\n", 1),
    ("def f():\n    import paho.mqtt.client as mqtt\n    return mqtt.Client(protocol=5)\n", 1),
    ("def f(mqtt):\n    return mqtt.Client(protocol=5)\n", 1),          # the regex leg
    # CONTROLS: no paho construction here
    ("from composeio.paho_compat import new_paho_client\nc = new_paho_client(mqtt=m, protocol=5)\n", 0),
    ("class Client:\n    pass\nc = Client()\n", 0),
    ("# mqtt.Client() in a comment\ns = 'paho.Client()'\n", 0),
    ("from paho.mqtt.client import MQTTv5\nx = MQTTv5\n", 0),
]


KNOWN_MISSES = [
    # The forms the docstring says it does NOT catch, pinned so the docstring
    # stays true: if the detector is ever widened, this test says to update it.
    ("import paho.mqtt.client as mqtt\nC = mqtt.Client\nC()\n", "attribute aliasing"),
    ("import paho.mqtt.client as mq\ngetattr(mq, 'Client')()\n", "getattr"),
    ("import importlib\nm = importlib.import_module('paho.mqtt.client')\nm.Client()\n", "importlib, other name"),
    ("m = __import__('paho.mqtt.client', fromlist=['Client'])\nm.Client()\n", "__import__, other name"),
    ("import paho.mqtt as pm\npm.client.Client()\n", "submodule-alias chain"),
    ("import paho.mqtt.client as mq\nclass Mine(mq.Client):\n    pass\nMine()\n", "subclassing"),
]


@pytest.mark.parametrize("src,form", KNOWN_MISSES)
def test_the_documented_blind_spots_are_exactly_as_documented(src, form):
    assert _paho_constructions(src) == 0, f"{form} is now caught: update the docstring"


def test_importlib_is_caught_only_when_bound_to_mqtt_or_paho():
    src = "import importlib\nmqtt = importlib.import_module('paho.mqtt.client')\nmqtt.Client()\n"
    assert _paho_constructions(src) == 1


@pytest.mark.parametrize("src,want", DETECTOR_CASES)
def test_the_detector_counts_every_construction_form(src, want):
    assert _paho_constructions(src) == want, src


def test_the_old_regex_alone_missed_the_aliased_forms():
    """CONTROL for the widening: proves the gap note 5 named was real, so the
    cases above are not passing for a reason that was already true."""
    aliased = [s for s, n in DETECTOR_CASES[:7]]
    assert [len(_CLIENT_ATTR_RE.findall(_code_only(s))) for s in aliased] == [0] * 7


def _scan(root: Path, skip_top=None) -> tuple[dict, int]:
    this = Path(__file__).resolve()
    hits, scanned = {}, 0
    for path in root.rglob("*.py"):
        if path.resolve() == this:
            continue
        rel = path.relative_to(root)
        if (skip_top and rel.parts[0] == skip_top) or set(rel.parts) & {
                ".git", "build", "site", "node_modules", "backups", ".venv"}:
            continue
        scanned += 1
        n = _paho_constructions(path.read_text(errors="ignore"))
        if n:
            hits[str(rel)] = n
    return hits, scanned


def test_every_paho_construction_under_python_goes_through_the_helper():
    """The helper only helps if it is the ONLY way a paho client gets built.
    The two calls inside `new_paho_client` itself are the sole allowed ones."""
    hits, scanned = _scan(PYTHON_ROOT)
    assert scanned > 100, f"scan covered only {scanned} files"
    assert hits == {"composeio/paho_compat.py": 2}, (
        "paho clients built outside paho_compat.new_paho_client: "
        f"{hits}")


# --------------------------------------------------------------------------- #
# 2. bus_client against a broker that is down, then up
# --------------------------------------------------------------------------- #

class FakeBroker:
    def __init__(self):
        self.up = False


class BackoffFake:
    """paho's client surface against a FakeBroker, modelling three paho facts:
    a blocking `connect()` to a down broker raises; after `connect_async()` +
    `loop_start()` the loop thread keeps attempting (here: `pump()`); and
    `subscribe()` with no socket returns MQTT_ERR_NO_CONN and sends nothing
    (paho 1.6.1 client.py, `if self._sock is None: return (MQTT_ERR_NO_CONN,
    None)`)."""

    broker: FakeBroker = None
    instances: list = []

    def __init__(self, *a, **kw):
        self.on_connect = None
        self.on_connect_fail = None
        self.on_message = None
        self.reconnect_delay = None
        self.wants = False
        self.looping = False
        self.tcp_up = False
        self.target = None
        self.sessions: list[list] = []
        self.fails = 0
        BackoffFake.instances.append(self)

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        self.reconnect_delay = (min_delay, max_delay)

    def connect(self, host, port=1883, keepalive=60, **kw):
        if not self.broker.up:
            raise ConnectionRefusedError(61, "Connection refused")
        self.target = (host, port, keepalive)
        self.wants = True
        self.tcp_up = True
        self.sessions.append([])

    def connect_async(self, host, port=1883, keepalive=60, **kw):
        self.target = (host, port, keepalive)
        self.wants = True

    def loop_start(self):
        self.looping = True

    def loop_stop(self):
        self.looping = False

    def pump(self):
        if not (self.looping and self.wants) or self.tcp_up:
            return
        if self.broker.up:
            self.tcp_up = True
            self.sessions.append([])
            if self.on_connect:
                self.on_connect(self, None, {}, 0, None)
        else:
            self.fails += 1
            if self.on_connect_fail:
                self.on_connect_fail(self, None)

    def drop(self):
        self.tcp_up = False

    def subscribe(self, topic, qos=0, **kw):
        if not self.tcp_up:
            return (4, None)                  # MQTT_ERR_NO_CONN
        self.sessions[-1].append((topic, qos))
        return (0, 1)

    def publish(self, *a, **kw):
        pass


class _Msg:
    def __init__(self, topic):
        self.topic = topic
        self.payload = b"{}"


@pytest.fixture
def broker():
    BackoffFake.instances = []
    BackoffFake.broker = FakeBroker()
    return BackoffFake.broker


def _pump(n=3):
    for _ in range(n):
        BackoffFake.instances[-1].pump()


def test_acquire_with_the_broker_down_does_not_raise_and_connects_when_up(broker):
    c = bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    assert c.looping, "loop_start() must run even when the broker is down"
    _pump()
    assert not c.tcp_up and c.fails >= 1, "the loop thread kept trying"
    broker.up = True
    _pump()
    assert c.tcp_up, "connected in the same process, no restart"
    assert c.target == ("h", 1883, 60)


def test_subscribe_with_the_broker_down_is_live_once_the_broker_is_up(broker):
    got = []
    c = bc.subscribe("mqtt://h:1883", "composeio/+/x", lambda c, u, m:
                     got.append(m.topic), label="sub", client_factory=BackoffFake)
    _pump()
    assert c.sessions == [], "nothing reached a broker while it was down"
    broker.up = True
    _pump()
    assert ("composeio/+/x", 0) in c.sessions[-1], \
        "the filter registered while down was never sent to the broker"
    c.on_message(c, None, _Msg("composeio/global/x"))
    assert got == ["composeio/global/x"]


def test_every_filter_is_resubscribed_after_a_mid_run_drop(broker):
    broker.up = True
    c = bc.subscribe("mqtt://h:1883", "a/#", lambda *a: None, label="A",
                     client_factory=BackoffFake)
    bc.subscribe("mqtt://h:1883", "b/+", lambda *a: None, label="B", qos=1,
                 client_factory=BackoffFake)
    _pump()
    assert {("a/#", 0), ("b/+", 1)} <= set(c.sessions[-1])
    c.drop()
    broker.up = False
    _pump()
    broker.up = True
    _pump()
    assert len(c.sessions) >= 2, "no reconnect happened"
    assert {("a/#", 0), ("b/+", 1)} <= set(c.sessions[-1]), \
        "a reconnect lost subscriptions"


def test_a_publisher_first_client_gains_resubscribe_when_a_subscriber_joins(broker):
    pub = bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    bc.subscribe("mqtt://h:1883", "q/#", lambda *a: None, label="sub",
                 client_factory=BackoffFake)
    broker.up = True
    _pump()
    assert ("q/#", 0) in pub.sessions[-1]


def test_bus_client_uses_the_transports_backoff(broker):
    c = bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    assert c.reconnect_delay == (MQTTTransport.RECONNECT_MIN_DELAY_S,
                                 MQTTTransport.RECONNECT_MAX_DELAY_S)


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_real_paho_acquire_against_a_closed_port_returns_and_retries(monkeypatch):
    """Real paho, real sockets, no broker. Old code: `acquire()` raised
    ConnectionRefusedError. Now it returns, and paho's loop thread retries.
    (The negative control for "a blocking connect raises once and nothing
    retries" is `test_negative_control_blocking_connect_does_not_retry` in
    test_transport_mqtt_reconnect.py.)"""
    monkeypatch.setattr(MQTTTransport, "RECONNECT_MIN_DELAY_S", 0.05)
    monkeypatch.setattr(MQTTTransport, "RECONNECT_MAX_DELAY_S", 0.1)
    port = _closed_port()
    url = f"mqtt://127.0.0.1:{port}"
    c = bc.acquire(url, label="real")
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and bc.health(url)["connect_failures"] < 2:
            time.sleep(0.05)
        h = bc.health(url)
        assert h["connect_failures"] >= 2, "paho's loop thread did not retry"
        assert h["connected"] is False and h["started"] is True
    finally:
        c.loop_stop()


# --------------------------------------------------------------------------- #
# 3. An outage is LOUD (DEC-HOP-OLD-BROKER-OFFLINE-ON-PURPOSE-2026-11-27): the
#    old broker was taken offline on purpose so anything not repointed fails
#    loudly, so a quiet retry would hide exactly that misconfiguration.
# --------------------------------------------------------------------------- #
import logging                                                  # noqa: E402

import composeio.transport_mqtt as tm                           # noqa: E402

BC_LOGGER = "composeio.bus_client"


def _bc_records(caplog, level=None):
    return [r for r in caplog.records if r.name == BC_LOGGER
            and (level is None or r.levelno == level)]


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(tm, "_monotonic", lambda: now[0])
    return now


def test_a_down_broker_logs_ERROR_naming_the_configured_target(broker, caplog):
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    bc.acquire("mqtt://old-broker.example:1884", label="pub",
               client_factory=BackoffFake)
    _pump(1)
    errors = _bc_records(caplog, logging.ERROR)
    assert len(errors) == 1, [r.getMessage() for r in caplog.records]
    assert "old-broker.example:1884" in errors[0].getMessage()


def test_repeats_are_WARNINGs_with_a_count_and_do_not_flood(broker, caplog, clock):
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    _pump(10)                                  # 10 failures inside one second
    assert [r.levelno for r in _bc_records(caplog)] == [
        logging.ERROR, logging.WARNING, logging.WARNING], \
        "expected ERROR on attempt 1, WARNING on attempts 2 and 4, then quiet"
    warn = _bc_records(caplog, logging.WARNING)
    assert "2 failed attempts in total" in warn[0].getMessage()
    assert "4 failed attempts in total" in warn[1].getMessage()
    clock[0] += 61                              # the OLD 60 s cadence: still quiet
    _pump(1)
    assert len(_bc_records(caplog, logging.WARNING)) == 2, \
        "after attempts 2 and 4 the summary interval is 600 s, not 60 s"
    clock[0] += tm.LinkHealth.SUMMARY_EVERY_S
    _pump(1)
    warn = _bc_records(caplog, logging.WARNING)
    assert len(warn) == 3 and "12 failed attempts in total" in warn[2].getMessage()


def test_the_summary_interval_is_the_transports_one_throttle():
    assert tm.LinkHealth.SUMMARY_EVERY_S == 600
    assert MQTTTransport.FAILURE_LOG_SUMMARY_S == tm.LinkHealth.SUMMARY_EVERY_S


def test_a_refusing_broker_logs_ONE_line_per_report(broker, caplog, clock):
    """Candidate review note 2: each refused CONNACK is followed by paho's
    disconnect, and both were logged. Four refused attempts must give exactly
    the ERROR plus the attempt-2 and attempt-4 WARNINGs."""
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    c = bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    for _ in range(4):
        c.on_connect(c, None, {}, 0x87, None)
        c.on_disconnect(c, None, 0x87, None)
    assert [r.levelno for r in _bc_records(caplog)] == [
        logging.ERROR, logging.WARNING, logging.WARNING], \
        [r.getMessage() for r in _bc_records(caplog)]


def test_a_drop_after_a_good_connection_is_still_logged(broker, caplog):
    """CONTROL for the test above: only a refusal's disconnect is silenced."""
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    broker.up = True
    c = bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    _pump(1)
    c.on_disconnect(c, None, 7, None)
    assert any("unexpected disconnect" in r.getMessage()
               for r in _bc_records(caplog, logging.WARNING))


def test_health_reports_not_connected_with_a_growing_failure_count(broker):
    url = "mqtt://h:1883"
    assert bc.health(url)["started"] is False
    bc.acquire(url, label="pub", client_factory=BackoffFake)
    _pump(1)
    h1 = bc.health(url)
    _pump(2)
    h3 = bc.health(url)
    assert h1["connected"] is False and h1["started"] is True
    assert (h1["connect_failures"], h3["connect_failures"]) == (1, 3)
    assert h3["last_error"] == "connection attempt failed"
    assert h3["last_success"] is None and h3["broker"] == "h:1883"


def test_the_first_successful_connect_logs_recovery_once(broker, caplog):
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    url = "mqtt://h:1883"
    c = bc.acquire(url, label="pub", client_factory=BackoffFake)
    _pump(3)
    broker.up = True
    _pump(3)
    rec = [r for r in _bc_records(caplog, logging.INFO) if "RECOVERED" in r.getMessage()]
    assert len(rec) == 1 and "after 3 failed attempt(s)" in rec[0].getMessage()
    h = bc.health(url)
    assert h["connected"] is True and h["connect_failures"] == 0
    assert h["last_error"] is None and h["last_success"] is not None
    # a NEW outage logs its ERROR again (the throttle was cleared)
    c.drop()
    broker.up = False
    _pump(1)
    assert len(_bc_records(caplog, logging.ERROR)) == 2


def test_a_refused_connack_is_an_ERROR_with_its_reason(broker, caplog):
    caplog.set_level(logging.INFO, logger=BC_LOGGER)
    url = "mqtt://h:1883"
    c = bc.acquire(url, label="pub", client_factory=BackoffFake)
    c.on_connect(c, None, {}, 0x87, None)          # Not authorized
    (err,) = _bc_records(caplog, logging.ERROR)
    assert "h:1883" in err.getMessage() and "REFUSED" in err.getMessage()
    assert bc.health(url)["connect_failures"] == 1


def test_bus_client_and_the_transport_expose_the_same_health_structure(broker):
    """Key-set EQUALITY (candidate review note 3), for a started link and for
    a broker never started, which used to be a hand-written fallback dict."""
    bc.acquire("mqtt://h:1883", label="pub", client_factory=BackoffFake)
    t = MQTTTransport(broker="h", port=1883, client_id="probe")
    want = set(t.health())
    assert set(bc.health("mqtt://h:1883")) == want
    assert set(bc.health("mqtt://never-started:1883")) == want
    assert bc.health("mqtt://never-started:1883")["started"] is False
    assert isinstance(t._link, tm.LinkHealth), "one structure, not two"


def test_every_paho_construction_in_the_repo_goes_through_the_helper():
    """The same rule over the WHOLE repository, with NO allowed exception:
    outside python/, the launcher, the CAN bridge, the change-notify test and
    (candidate review F3) the storage bring-up collector all use the helper."""
    hits, scanned = _scan(PYTHON_ROOT.parent, skip_top="python")
    assert scanned > 100, f"scan covered only {scanned} files"
    assert hits == {}, hits
