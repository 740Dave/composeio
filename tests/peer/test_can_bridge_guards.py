"""The two guards that had to exist before can_mqtt_bridge met a real segment.

Both defects were found by reading the code on 2026-08-12, the day the bridge
was first deployed to display-node.local against a live 500 kbit CAN HAT and the live
production MQTT broker. Neither would have shown up as an exception, a traceback
or a failed test — the firehose "works", and a fabricated namespace publishes
cleanly. That is precisely why they get assertions.

  1. ALLOWLIST — the MQTT side is the whole bus; the CAN side is 8 bytes per
     frame on a shared segment.
  2. UNRESOLVABLE HASH — a namespace hash is one-way, so a hash with no
     registration is not a namespace and must not be invented into one.

Plus a third, on the encode path: the 9-bit source hash used Python's salted
`hash()`, so arbitration ids moved every process restart.

THE IDS IN THIS FILE ARE HAND-BUILT FROM THE LAYOUT, ON PURPOSE. They restate
[BP-CAN-ID] v2's shifts rather than importing them, so a test that "passes"
because the encoder and the decoder moved together is not possible here. That is
the one place in this repo where restating the layout is correct — everywhere
else it is how [BP-CAN-ID-DIVERGENCE] happened.

    29-bit extended id:  prio(3) | type(4) | ns_id(16) | src(4) | scope(2)
    bit positions:       28..26  | 25..22  | 21..6     | 5..2   | 1..0
"""

#: Hand-written shifts — see the module docstring. NOT imported.
_PRIO_SHIFT, _TYPE_SHIFT, _NS_SHIFT, _SRC_SHIFT, _SCOPE_SHIFT = 26, 22, 6, 2, 0
_SCOPE_GLOBAL = 1


def _v2_id(ns_hash, type_code=0, prio=0, src=0, scope=_SCOPE_GLOBAL):
    """Build a [BP-CAN-ID] v2 id by hand. See the module docstring."""
    return ((prio << _PRIO_SHIFT) | (type_code << _TYPE_SHIFT)
            | (ns_hash << _NS_SHIFT) | (src << _SRC_SHIFT)
            | (scope << _SCOPE_SHIFT))

import importlib
import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE = _ROOT / "services" / "can_mqtt_bridge" / "bridge.py"


def _load_bridge(monkeypatch=None, env: dict | None = None):
    """Load bridge.py fresh, optionally under a given environment.

    Reloaded per test because the allowlist and the namespace registry are
    module-level — which is exactly how the service runs."""
    if monkeypatch is not None:
        for k in ("CAN_BRIDGE_ALLOWLIST", "CAN_BRIDGE_REGISTER"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("can_bridge_under_test", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --- 1. the allowlist -------------------------------------------------------

def test_unset_allowlist_relays_everything_but_says_so(monkeypatch, caplog):
    """Unset must NOT silently change behaviour for existing callers — but the
    firehose has to announce itself. Backward compatibility is not a licence
    to be quiet."""
    mod = _load_bridge(monkeypatch)
    assert mod.load_allowlist() is None          # unchanged: everything
    assert mod.allowlist_is_default() is True
    assert mod.DEFAULT_ALLOWLIST == mod.ALLOW_ALL  # the default is stated ONCE

    bridge = mod.CANMQTTBridge(allowlist=None)
    with caplog.at_level("WARNING"):
        bridge._describe_allowlist()
    assert any(r.levelname == "WARNING" and "allowlist is NOT SET" in r.message
               for r in caplog.records), "an unbounded bridge must warn"


def test_explicit_star_is_the_firehose_without_the_warning(monkeypatch):
    """A deliberate firehose is a different thing from an unconfigured one."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_ALLOWLIST": "*"})
    assert mod.load_allowlist() is None
    assert mod.allowlist_is_default() is False


@pytest.mark.parametrize("raw,expect", [
    ("throttle/left,throttle/right", ["throttle/left", "throttle/right"]),
    ("throttle/left throttle/right", ["throttle/left", "throttle/right"]),
    ("a, a ,b", ["a", "b"]),                      # deduped, order preserved
    ("", []),
    (None, []),
])
def test_allowlist_parsing(raw, expect):
    mod = _load_bridge()
    assert mod.parse_namespace_list(raw) == expect


def test_allowlisted_bridge_drops_everything_else(monkeypatch):
    """The handler-side check, not just the broker-side subscribe. A reconnect
    that re-installs a wildcard filter must not put the whole bus on CAN."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_ALLOWLIST": "throttle/left"})
    mod.register_ns("throttle/left")
    bridge = mod.CANMQTTBridge(allowlist=["throttle/left"])

    sent: list[tuple[int, bytes]] = []

    class _Bus:
        def send(self, arb, data, eff=False):
            sent.append((arb, data))
    bridge._can_bus = _Bus()

    def deliver(ns, val, src="test"):
        import json

        class _M:
            payload = json.dumps({"ns": ns, "type": "state", "val": val,
                                  "src": src, "scope": "global"}).encode()
        bridge._on_mqtt_message(None, None, _M())

    deliver("throttle/left", 200)
    deliver("dradis/bodies", "a-huge-json-blob")   # the real firehose offender
    deliver("_app/console/bbox", 1)

    assert len(sent) == 1, "only the allowlisted namespace may reach CAN"
    assert bridge.filtered == 2
    assert bridge.down == 1


def test_echo_guard_drops_messages_sourced_from_can(monkeypatch):
    """The loop that would be destructive on a 500 kbit segment."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_ALLOWLIST": "shields"})
    bridge = mod.CANMQTTBridge(allowlist=["shields"])
    sent = []

    class _Bus:
        def send(self, arb, data, eff=False):
            sent.append(arb)
    bridge._can_bus = _Bus()

    import json

    class _M:
        payload = json.dumps({"ns": "shields", "type": "state", "val": 5,
                              "src": mod.CAN_SOURCE, "scope": "global"}).encode()
    bridge._on_mqtt_message(None, None, _M())

    assert sent == [], "a message that came FROM can must never go back to can"
    assert bridge.echoes == 1


# --- 2. the unresolvable hash ----------------------------------------------

def test_unknown_namespace_hash_is_dropped_not_fabricated(caplog):
    """It used to publish `_unknown_0x1234` onto the real MQTT bus."""
    mod = _load_bridge()
    unknown_ns_hash = 0xBEEF
    assert unknown_ns_hash not in mod._hash_to_name
    can_id = _v2_id(unknown_ns_hash, type_code=0, src=1)  # type 0 IS allocated

    with caplog.at_level("WARNING"):
        decoded = mod.message_from_can(can_id, b"\x2a")

    assert decoded is None, "an unnameable frame must be DROPPED"
    text = " ".join(r.message for r in caplog.records)
    assert "UNRESOLVABLE" in text
    assert "0xBEEF" in text, "the warning must identify the hash that failed"
    assert mod._unresolved_hashes[unknown_ns_hash] == 1


def test_no_fabricated_namespace_string_survives_in_source():
    """The specific shape of the old bug, asserted textually so a revert is
    caught even if someone reintroduces it on a different code path."""
    src = _BRIDGE.read_text()
    assert "_unknown_" not in src.replace("`_unknown_0x1234`", "")


def test_repeated_unknown_hash_warns_once_and_counts(caplog):
    """A rogue talker on a live segment must not fill the journal — but it must
    still be counted, because 'quiet' is how the first bug survived."""
    mod = _load_bridge()
    can_id = _v2_id(0x1234)
    with caplog.at_level("WARNING"):
        for _ in range(50):
            assert mod.message_from_can(can_id, b"\x01") is None
    warnings = [r for r in caplog.records if "UNRESOLVABLE" in r.message]
    assert len(warnings) == 1
    assert mod._unresolved_hashes[0x1234] == 50


def test_registered_namespace_still_decodes():
    """The drop must be about UNKNOWN hashes only — not a blanket refusal."""
    mod = _load_bridge()
    mod.register_ns("throttle/left")
    from composeio.config import namespace_hash
    # throttle/* is tier 1 (live control) by the [BP-WIRE-PRIORITY] rule.
    can_id = _v2_id(namespace_hash("throttle/left"), prio=1)
    msg = mod.message_from_can(can_id, b"\xc8")
    assert msg is not None
    assert msg.namespace == "throttle/left"
    assert msg.value == 200
    assert msg.source == mod.CAN_SOURCE      # feeds the echo guard


# --- 3. the source hash on the encode path ---------------------------------

def test_source_hash_is_deterministic_across_processes():
    """`hash()` on a str is salted per process (PYTHONHASHSEED), so the same
    message used to get a DIFFERENT arbitration id — hence a different bus
    PRIORITY — after every restart, and no firmware could reproduce it."""
    import subprocess
    from composeio.transport import BusMessage, MessageType

    mod = _load_bridge()
    mod.register_ns("shields")
    local = mod.can_id_from_message(BusMessage(
        namespace="shields", type=MessageType.STATE, value=1,
        source="cio-ctrl-db1c", scope="global"))

    prog = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('b', {str(_BRIDGE)!r});"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "m.register_ns('shields');"
        "from composeio.transport import BusMessage, MessageType;"
        "print(m.can_id_from_message(BusMessage(namespace='shields',"
        "type=MessageType.STATE,value=1,source='cio-ctrl-db1c',scope='global')))"
    )
    env_runs = set()
    for seed in ("1", "12345"):
        out = subprocess.run(
            [sys.executable, "-c", prog], capture_output=True, text=True,
            env={"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin",
                 "PYTHONPATH": str(_ROOT / "python")}, cwd=str(_ROOT))
        assert out.returncode == 0, out.stderr
        env_runs.add(int(out.stdout.strip()))

    assert len(env_runs) == 1, (
        f"arbitration id varies with PYTHONHASHSEED: {env_runs}")
    assert env_runs == {local}


# --- 4. a drop must be able to NAME ITS AUTHOR ------------------------------
# 2026-08-15. Six restarts were refused by this handler for carrying
# `"type": "command"` — a word [BP-TYPE-CODES] never allocated. The warning
# named the TOPIC and the enum error and NOTHING ELSE, so identifying the
# publisher meant reconstructing it by hand from six timestamps in a journal.
#
# `src` was in the payload the whole time. The envelope failed on ONE field and
# the entire message — including the only field that says WHO SENT IT — was
# discarded with it. A receiver that can see the author and does not print it
# is choosing to make the defect anonymous.

def test_an_unreadable_envelope_still_names_its_src(monkeypatch, caplog):
    import json as _json

    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge(allowlist=None, denylist=[])

    class _Msg:
        topic = "composeio/global/_device/cio-ctrl-db1c/restart"
        payload = _json.dumps({
            "ns": "_device/cio-ctrl-db1c/restart",
            "type": "command",              # ⛔ THE WORD. Not a wire type.
            "val": 1, "src": "nightly-poke", "scope": "global",
        }).encode()

    with caplog.at_level("WARNING"):
        bridge._on_mqtt_message(None, None, _Msg())

    text = "\n".join(r.message for r in caplog.records)
    assert "nightly-poke" in text, (
        "the drop names the topic but not the PUBLISHER — which is how six "
        "identical failures stayed anonymous for a whole evening")
    # and the refusal carries the remedy, not just 'invalid MessageType'
    assert "'state'" in text and "PROSE" in text
    # counted, not merely logged
    assert bridge.drops[f"<unparseable>{_Msg.topic}"][mod.DROP_ENCODE] == 1


def test_a_payload_that_is_not_json_at_all_still_drops_cleanly(monkeypatch, caplog):
    """NEGATIVE CONTROL for the change above.

    `payload` is read by the new src-naming branch, and `json.loads` raising
    leaves it unbound unless it is bound first. A NameError inside an exception
    handler on the hot path would take the bridge down on the first non-JSON
    message on a shared prefix — which is traffic the bridge sees NORMALLY.
    """
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge(allowlist=None, denylist=[])

    class _Msg:
        topic = "composeio/global/junk"
        payload = b"not json at all"

    with caplog.at_level("WARNING"):
        bridge._on_mqtt_message(None, None, _Msg())      # must not raise

    assert bridge.drops[f"<unparseable>{_Msg.topic}"][mod.DROP_ENCODE] == 1
    assert "src=" not in "\n".join(r.message for r in caplog.records)


# --- 5. the persisted table must not DECAY across restarts ------------------
# Measured on rpi5 2026-08-15: two ordinary service restarts took the learned
# table from 71 entries to 10. `_load_persisted_registry` re-registers a
# restored row as `persisted:mqtt`, and the shutdown filter tested `prov in
# PERSISTABLE` — which that string fails. So each shutdown wrote only what the
# ending run had seen for itself and silently dropped everything it had
# restored. Both startups logged `restored 71`, so the instrument reported
# success in the run where the loss happened.

def test_a_restored_row_is_written_back_on_the_next_shutdown(monkeypatch, tmp_path):
    reg = tmp_path / "registry.json"
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(reg)})

    # Run 1: observe two names on MQTT, then shut down.
    mod.observe_ns("game/threat", mod.PROV_MQTT)
    mod.observe_ns("sys/cpu", mod.PROV_MQTT)
    assert mod._persist_registry("run-1") == 2

    # Run 2: a fresh process restores them, sees ONE new name, and shuts down.
    mod2 = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(reg)})
    assert mod2._registry_loaded == 2, "the restore itself must work"
    mod2.observe_ns("throttle/left", mod2.PROV_MQTT)
    written = mod2._persist_registry("run-2")

    assert written == 3, (
        f"a restart wrote {written} of 3 — the table DECAYS to whatever the "
        f"last run happened to observe, while the startup log says 'restored N'")

    import json as _json
    body = _json.loads(reg.read_text())
    vias = {e["name"]: e["via"] for e in body["entries"]}
    assert set(vias) == {"game/threat", "sys/cpu", "throttle/left"}
    assert set(vias.values()) == {mod2.PROV_MQTT}, (
        f"the FILE must store the underlying kind, not 'persisted:mqtt' — "
        f"otherwise it grows a prefix per restart: {vias}")

    # Run 3: the round trip is now stable rather than merely one hop longer.
    mod3 = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(reg)})
    assert mod3._registry_loaded == 3
    assert mod3._persist_registry("run-3") == 3


def test_unpersistable_provenance_is_still_refused(monkeypatch, tmp_path):
    """NEGATIVE CONTROL. Loosening the filter must not start persisting the
    kinds that are re-derived from code and from the unit file every boot — a
    stale file must never be able to override a change made there."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE":
                                     str(tmp_path / "r.json")})
    for kind in (mod.PROV_WELLKNOWN, mod.PROV_BUILTIN, mod.PROV_ENV):
        assert mod.persistable_provenance(kind) is None
        assert mod.persistable_provenance(f"persisted:{kind}") is None
    assert mod.persistable_provenance(mod.PROV_MQTT) == mod.PROV_MQTT
    assert mod.persistable_provenance("persisted:can") == mod.PROV_CAN
    assert mod.persistable_provenance(None) is None
    assert mod.persistable_provenance("") is None
