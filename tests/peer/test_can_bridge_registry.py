"""The rebuilt can_mqtt_bridge: what it now REFUSES, and what it now REPORTS.

The old bridge's filtering happened at SUBSCRIBE time, so its `filtered`
counter could not increment and `allowlist dropped 0` was an arithmetic
identity wearing the costume of a measurement. Every assertion in this file
exists because the corresponding property, if broken, would look FINE:

  * a bridge that subscribes narrowly still relays correctly — it just cannot
    say what it declined,
  * a truncated 8-byte fragment DECODES, cleanly, as a different value,
  * a hash collision resolves to *a* namespace, just the wrong one,
  * ENOBUFS logged as "CAN write error" reads exactly like a dead interface,
  * and a bridge relaying its own status blob onto CAN looks, from MQTT, like
    a bridge that is working.

`tests/peer/test_can_bridge_guards.py` still covers the pre-existing guards
(legacy allowlist, unresolvable hash, deterministic source hash). This file
covers what the 2026-08-12 rebuild added.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from composeio.config import namespace_hash

_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE = _ROOT / "services" / "can_mqtt_bridge" / "bridge.py"

#: A REAL CRC-16/CCITT-FALSE collision, found by search rather than asserted:
#: both of these hash to 0xD122. If the hash ever changes, this test fails
#: loudly instead of quietly testing nothing.
COLLIDE_A, COLLIDE_B, COLLIDE_HASH = "ns/2885", "ns/8660", 0xD122


def _load_bridge(monkeypatch=None, env: dict | None = None):
    """Load bridge.py fresh. Module-level registry state is how the service
    actually runs, so every test gets its own module object."""
    if monkeypatch is not None:
        for k in ("CAN_BRIDGE_ALLOWLIST", "CAN_BRIDGE_DENYLIST",
                  "CAN_BRIDGE_REGISTER", "CAN_BRIDGE_STATUS_S",
                  "MQTT_CLIENT_ID"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("can_bridge_rebuilt", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Bus:
    """A CAN stand-in that RECORDS, and can be told to fail like the kernel."""

    def __init__(self, raise_errno=None):
        self.sent: list[tuple[int, bytes]] = []
        self.raise_errno = raise_errno

    def send(self, arb, data, eff=False):
        if self.raise_errno is not None:
            raise OSError(self.raise_errno, "injected")
        self.sent.append((arb, data))

    def close(self):
        pass


def _deliver(bridge, ns, val, src="test", mtype="state"):
    class _M:
        payload = json.dumps({"ns": ns, "type": mtype, "val": val,
                              "src": src, "scope": "global"}).encode()
    bridge._on_mqtt_message(None, None, _M())


# --- 1. the subscription is UNCONDITIONAL ----------------------------------- #

@pytest.mark.parametrize("env", [
    {},
    {"CAN_BRIDGE_DENYLIST": "dradis/*"},
    {"CAN_BRIDGE_ALLOWLIST": "throttle/left"},
])
def test_always_subscribes_the_whole_bus(monkeypatch, env):
    """Filtering at the broker is filtering you cannot count. Whatever the
    configuration, the bridge subscribes `#` and decides in-process."""
    mod = _load_bridge(monkeypatch, env)
    bridge = mod.CANMQTTBridge(allowlist=mod.load_allowlist(),
                               denylist=mod.load_denylist())
    subs: list[tuple[str, int]] = []

    class _C:
        def subscribe(self, topic, qos=0):
            subs.append((topic, qos))

    bridge._on_mqtt_connect(_C(), None, None, 0)
    assert subs == [(f"{mod.MQTT_PREFIX}/#", 1)], (
        "every configuration must still see the whole bus in-process; "
        f"got {subs}")


# --- 2. the deny list, and a census that can actually increment ------------- #

def test_denylist_drops_and_names_what_it_dropped(monkeypatch):
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_DENYLIST": "dradis/*,shields"})
    bridge = mod.CANMQTTBridge(denylist=mod.load_denylist())
    bridge._can_bus = _Bus()

    _deliver(bridge, "throttle/left", 200)
    for _ in range(3):
        _deliver(bridge, "dradis/bodies", 1)      # fnmatch family
    _deliver(bridge, "shields", 5)                # exact
    _deliver(bridge, "warp_factor", 9)

    assert len(bridge._can_bus.sent) == 2, "only undenied namespaces cross"
    # The counter the old design could not move, moved — and BY NAME.
    assert bridge.drops["dradis/bodies"][mod.DROP_DENIED] == 3
    assert bridge.drops["shields"][mod.DROP_DENIED] == 1
    assert bridge.drop_totals()[mod.DROP_DENIED] == 4
    assert bridge.filtered == 4


def test_default_denylist_is_empty(monkeypatch):
    mod = _load_bridge(monkeypatch)
    assert mod.load_denylist() == []
    bridge = mod.CANMQTTBridge(denylist=mod.load_denylist())
    bridge._can_bus = _Bus()
    _deliver(bridge, "anything/at/all", 1)
    assert len(bridge._can_bus.sent) == 1


def test_legacy_allowlist_is_honored_and_deprecation_names_the_new_var(
        monkeypatch, caplog):
    """Silently ignoring a filter a deployment relies on would put the whole
    production bus on a 500 kbit segment. Honor it; say it is deprecated."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_ALLOWLIST": "throttle/left"})
    assert mod.allowlist_is_set() is True
    assert mod.load_allowlist() == ["throttle/left"]

    bridge = mod.CANMQTTBridge(allowlist=["throttle/left"])
    bridge._can_bus = _Bus()
    _deliver(bridge, "throttle/left", 1)
    _deliver(bridge, "dradis/bodies", 1)
    assert len(bridge._can_bus.sent) == 1

    with caplog.at_level("WARNING"):
        mod.main.__wrapped__() if hasattr(mod.main, "__wrapped__") else None
    # main() blocks, so assert on the text it would emit rather than running it.
    src = _BRIDGE.read_text()
    assert "CAN_BRIDGE_ALLOWLIST is DEPRECATED" in src
    assert "CAN_BRIDGE_DENYLIST" in src


# --- 3. the self-populating BIDIRECTIONAL registry -------------------------- #

def test_observing_on_mqtt_makes_the_hash_resolvable_on_the_way_back(monkeypatch):
    """THE key insight: the bridge never reverses a hash, it needs to have seen
    the NAME once. A namespace nobody listed anywhere must round-trip."""
    mod = _load_bridge(monkeypatch)
    ns = "game/unlisted_namespace"
    assert ns not in mod._name_to_hash
    assert namespace_hash(ns) not in mod._hash_to_name

    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    _deliver(bridge, ns, 7)

    assert mod._name_to_hash[ns] == namespace_hash(ns)
    assert mod._hash_to_name[namespace_hash(ns)] == ns
    # and now a CAN frame carrying only the hash decodes to the NAME.
    # [BP-CAN-ID] v2, hand-built rather than imported: prio(3)|type(4)|
    # ns(16)|src(4)|scope(2). `game/unlisted_namespace` matches no priority
    # rule, so it gets the DEFAULT tier 5.
    can_id = (5 << 26) | (0 << 22) | (namespace_hash(ns) << 6) | 1
    decoded = mod.message_from_can(can_id, b"\x07")
    assert decoded is not None and decoded.namespace == ns


def test_registry_is_not_a_permission_system(monkeypatch):
    """A DENIED namespace must still be LEARNED. If learning granted the right
    to forward, anything with MQTT publish access would have write access to a
    shared physical segment."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_DENYLIST": "dradis/*"})
    bridge = mod.CANMQTTBridge(denylist=mod.load_denylist())
    bridge._can_bus = _Bus()

    _deliver(bridge, "dradis/contacts", 3)

    assert bridge._can_bus.sent == [], "denied namespaces must not reach CAN"
    assert mod._hash_to_name[namespace_hash("dradis/contacts")] == "dradis/contacts", \
        "but they must still be NAMEABLE when a CAN node sends them up"
    # The two paths are genuinely separate, not one guard doing both jobs.
    assert mod.observe_ns("dradis/contacts") == mod.OBSERVE_KNOWN
    assert bridge._forwarding_decision(
        mod.BusMessage(namespace="dradis/contacts", type=mod.MessageType.STATE,
                       value=1, source="x", scope="global")) == mod.DROP_DENIED


# --- 4. representability: DROP, never truncate ------------------------------ #

def test_oversize_value_is_dropped_not_truncated(monkeypatch, caplog):
    """`transport_can.py` drops; this file used to truncate to 8 bytes and send
    the fragment. A fragment is not a smaller value — it decodes cleanly as a
    DIFFERENT one, which is worse than nothing arriving."""
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()

    big = "x" * 200                      # encodes to 1 + 200 bytes
    with caplog.at_level("WARNING"):
        for _ in range(5):
            _deliver(bridge, "dradis/bodies", big)

    assert bridge._can_bus.sent == [], "an oversize value must not reach CAN AT ALL"
    assert bridge.drops["dradis/bodies"][mod.DROP_OVERSIZE] == 5
    assert bridge.down == 0
    warns = [r for r in caplog.records if "DROPPED, not truncated" in r.message]
    assert len(warns) == 1, "warn once per namespace, then count"


def test_truncation_expression_is_gone_from_the_source():
    """The specific shape of the old bug, asserted textually so a revert is
    caught even on a different code path."""
    # The docstring NAMES the old expression so the history is legible; strip
    # that one quoted mention (the same idiom test_can_bridge_guards.py uses
    # for `_unknown_0x1234`) and assert nothing else in the file does it.
    src = _BRIDGE.read_text().replace("(`data = encoded[:8]`)", "")
    assert "encoded[:8]" not in src
    assert "value truncated" not in src


def test_exactly_eight_bytes_still_crosses(monkeypatch):
    """The gate must be about OVERSIZE, not a blanket refusal — a config chunk
    is exactly 8 bytes ([seq(2)][content(6)]) and is the whole point of CAN
    carrying config at all."""
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    _deliver(bridge, "_device/leaf/config", list(b"\x00\x01abcdef"),
             mtype="config_push")
    # a list is not bytes; use the real chunk value instead
    bridge._can_bus.sent.clear()
    from composeio.config_chunk import chunk_config, CAN_CONTENT_BYTES
    frame = chunk_config(b"hello world!", CAN_CONTENT_BYTES)[1]
    assert len(frame) == 8
    bridge._forward_to_can(mod.BusMessage(
        namespace="_device/leaf/config", type=mod.MessageType.CONFIG_PUSH,
        value=frame, source="config-server", scope="global"))
    assert len(bridge._can_bus.sent) == 1
    assert bridge._can_bus.sent[0][1] == frame, "8 bytes must cross VERBATIM"


# --- 5. collision refuses the NEWCOMER -------------------------------------- #

def test_collision_refuses_the_newcomer_and_keeps_the_incumbent(monkeypatch, caplog):
    mod = _load_bridge(monkeypatch)
    assert namespace_hash(COLLIDE_A) == namespace_hash(COLLIDE_B) == COLLIDE_HASH, \
        "the fixture collision is no longer a collision — re-derive it"

    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()

    _deliver(bridge, COLLIDE_A, 1)                      # incumbent
    assert len(bridge._can_bus.sent) == 1

    with caplog.at_level("ERROR"):
        for _ in range(4):
            _deliver(bridge, COLLIDE_B, 2)              # newcomer

    assert len(bridge._can_bus.sent) == 1, \
        "the NEWCOMER must not be forwarded — the incumbent is load-bearing"
    assert mod._hash_to_name[COLLIDE_HASH] == COLLIDE_A
    assert mod._collisions[COLLIDE_B] == COLLIDE_A
    assert bridge.drops[COLLIDE_B][mod.DROP_COLLISION] == 4
    assert any("COLLISION" in r.message for r in caplog.records)

    # and the incumbent keeps working, which is the whole reason for the rule.
    _deliver(bridge, COLLIDE_A, 3)
    assert len(bridge._can_bus.sent) == 2


def test_collision_refusal_is_permanent(monkeypatch):
    """A namespace that intermittently gets through is worse than one that
    never does: the far side would learn it exists."""
    mod = _load_bridge(monkeypatch)
    mod.register_ns(COLLIDE_A)
    for _ in range(3):
        assert mod.observe_ns(COLLIDE_B) == mod.OBSERVE_COLLISION


# --- 6. ENOBUFS is its own thing -------------------------------------------- #

def test_enobufs_is_counted_separately_from_a_write_error(monkeypatch, caplog):
    """`txqueuelen` is 10. A full queue is a CAPACITY fact about the segment;
    logging it as 'CAN write error' reads like a dead interface."""
    import errno as _errno
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus(raise_errno=_errno.ENOBUFS)

    with caplog.at_level("WARNING"):
        for _ in range(7):
            _deliver(bridge, "throttle/left", 1)

    assert bridge.enobufs == 7
    assert bridge.drops["throttle/left"][mod.DROP_ENOBUFS] == 7
    assert bridge.drop_totals()[mod.DROP_ENOBUFS] == 7
    assert bridge.down == 0
    assert len([r for r in caplog.records if "ENOBUFS" in r.message]) == 1

    # a DIFFERENT errno must not be filed as ENOBUFS.
    bridge2 = mod.CANMQTTBridge()
    bridge2._can_bus = _Bus(raise_errno=_errno.ENETDOWN)
    _deliver(bridge2, "throttle/left", 1)
    assert bridge2.enobufs == 0
    assert bridge2.drops["throttle/left"][mod.DROP_IO] == 1


# --- 7. the periodic forwarding table --------------------------------------- #

def test_status_blobs_carry_intent_reality_and_the_hash_table(monkeypatch):
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_DENYLIST": "dradis/*"})
    bridge = mod.CANMQTTBridge(denylist=mod.load_denylist())
    bridge._can_bus = _Bus()
    _deliver(bridge, "throttle/left", 200)
    _deliver(bridge, "dradis/bodies", 1)
    _deliver(bridge, "big/one", "y" * 200)

    blobs = bridge.status_blobs()
    assert set(blobs) == {mod.NS_DENYLIST, mod.NS_DROPS, mod.NS_FORWARDING}
    assert blobs[mod.NS_DENYLIST]["denylist"] == ["dradis/*"]          # INTENT
    reality = blobs[mod.NS_DROPS]
    assert reality["by_reason"][mod.DROP_DENIED] == 1                   # REALITY
    assert reality["by_reason"][mod.DROP_OVERSIZE] == 1
    assert reality["by_namespace"]["big/one"][mod.DROP_OVERSIZE] == 1
    table = blobs[mod.NS_FORWARDING]["name_to_hash"]                    # name->hash
    assert table["throttle/left"] == f"0x{namespace_hash('throttle/left'):04X}"
    assert "dradis/bodies" not in table, "only what was FORWARDED is listed"


def test_status_is_published_on_the_bridges_own_namespaces(monkeypatch):
    mod = _load_bridge(monkeypatch, {"MQTT_CLIENT_ID": "bridge-under-test"})
    bridge = mod.CANMQTTBridge()
    published: list[tuple[str, str]] = []

    class _C:
        def publish(self, topic, payload, qos=0, retain=False):
            published.append((topic, payload))

    bridge._mqtt_client = _C()
    assert bridge.publish_status() == 3
    assert bridge.status_publications == 1
    for topic, payload in published:
        assert topic.startswith(f"{mod.MQTT_PREFIX}/global/_bridge/bridge-under-test/")
        json.loads(payload)                       # must be parseable by anyone


# --- 8. THE TRAP: the bridge must not relay its own status onto CAN --------- #

def test_bridge_never_relays_its_own_status_blob_to_can(monkeypatch):
    """The bridge subscribes `#` AND publishes, so its own blobs come straight
    back. The echo guard does NOT catch them — that guard only knows
    `source == "can"` and these are sourced from the bridge itself."""
    mod = _load_bridge(monkeypatch, {"MQTT_CLIENT_ID": "bridge-under-test"})
    bridge = mod.CANMQTTBridge()          # no deny list, no allowlist: wide open
    bridge._can_bus = _Bus()

    for ns in (mod.NS_FORWARDING, mod.NS_DROPS, mod.NS_DENYLIST):
        _deliver(bridge, ns, {"a": 1}, src="bridge-under-test")

    assert bridge._can_bus.sent == [], \
        "the bridge relayed its own forwarding table onto the segment"
    assert bridge.echoes == 0, "this is NOT the echo guard's doing"
    assert bridge.drops[mod.NS_FORWARDING][mod.DROP_SELF] == 1
    assert bridge.drop_totals()[mod.DROP_SELF] == 3


def test_self_exclusion_is_by_construction_not_configuration(monkeypatch):
    """It must be impossible to configure the trap back open. An operator can
    edit a deny list; an operator cannot edit `SELF_NS_PREFIX`."""
    mod = _load_bridge(monkeypatch, {
        "MQTT_CLIENT_ID": "bridge-under-test",
        "CAN_BRIDGE_ALLOWLIST": f"_bridge/bridge-under-test/forwarding",
        "CAN_BRIDGE_DENYLIST": "",
    })
    assert mod.SELF_NS_PREFIX == "_bridge/bridge-under-test/"
    bridge = mod.CANMQTTBridge(allowlist=mod.load_allowlist(),
                               denylist=mod.load_denylist())
    bridge._can_bus = _Bus()
    # Explicitly ALLOWLISTED, deny list empty — the most permissive config that
    # can be written — and it still must not cross.
    _deliver(bridge, mod.NS_FORWARDING, {"a": 1}, src="bridge-under-test")
    assert bridge._can_bus.sent == []
    assert bridge.drops[mod.NS_FORWARDING][mod.DROP_SELF] == 1


def test_a_foreign_bridges_status_is_not_special_cased(monkeypatch):
    """The rule is about THIS bridge's own loop, and the id is what scopes it.
    Another bridge's blob is ordinary traffic; deny it if you do not want it."""
    mod = _load_bridge(monkeypatch, {"MQTT_CLIENT_ID": "bridge-a"})
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    _deliver(bridge, "_bridge/bridge-b/forwarding", 1, src="bridge-b")
    assert len(bridge._can_bus.sent) == 1
