"""A CAN-ORIGINATED NAME BECOMES RESOLVABLE — workstream G, host proof.

[PLAN G1-G4] of docs/PLAN-fleet-baseline-and-bus-native.md.

THE PROPERTY UNDER TEST, stated so it cannot be misread: a namespace that has
NEVER appeared on MQTT, whose name no configuration anywhere contains, arrives
at the bridge as a 16-bit number and is nameable AFTERWARDS because the node
that owns it said what it is called.

EVERY POSITIVE ASSERTION HERE IS PAIRED WITH A NEGATIVE ONE, and that pairing
is the point of the file. This repo has shipped four counters structurally
incapable of incrementing — the bridge's own `filtered`, and
`ConfigServer.acks_ok`/`acks_bad`, which could not fire because the ACK they
counted was physically unsendable on the substrate it was built for. The way
that happens is a test that only ever asks whether the good path works. So:

  * `test_the_gap_is_real_before_the_announcement` drives the SAME frame and
    asserts it is dropped, counted, and loudly reported. A guard that has never
    been observed to fail is not a guard.
  * every announcement failure mode (corrupt chunk, missing chunk, interleaved
    announcers, a bad name, a collision) is driven at least once and asserted
    to teach the bridge NOTHING.

WHAT THIS FILE DOES NOT PROVE, said plainly because a PASS here gets believed:
nothing has run on silicon. There is no CAN transceiver in this test, no bit
timing, no arbitration, no ESP32 emitting anything. It proves the wire FORMAT
and the bridge's LEARNING, which is exactly the layer that fails invisibly —
and exactly the layer a host test can reach.
"""

import importlib.util
import json
from pathlib import Path

import pytest

from composeio.config import namespace_hash
from composeio.config_chunk import CAN_CONTENT_BYTES
from composeio.ns_announce import (
    NS_ANNOUNCE, NS_ANNOUNCE_HASH, NS_ANNOUNCE_POLL, NS_ANNOUNCE_POLL_HASH,
    announcement_frames, decode_names, encode_names, MAX_NAME_BYTES,
    NsAnnounceError, load_registry, save_registry,
)

_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE = _ROOT / "services" / "can_mqtt_bridge" / "bridge.py"

#: The device under test. Its id is a MAC suffix — the exact shape that makes a
#: `_device/*` prefix rule impossible, because the hash is over the FULL name.
DEV = "hosyond-080739"
DEV_NAMESPACES = [f"_device/{DEV}/heartbeat", f"_device/{DEV}/build",
                  f"_device/{DEV}/transport", f"_device/{DEV}/scene",
                  f"_device/{DEV}/health"]

_ENV_KEYS = ("CAN_BRIDGE_ALLOWLIST", "CAN_BRIDGE_DENYLIST",
             "CAN_BRIDGE_REGISTER", "CAN_BRIDGE_STATUS_S", "MQTT_CLIENT_ID",
             "CAN_BRIDGE_REGISTRY_FILE", "CAN_BRIDGE_ANNOUNCE_POLL")


def _load_bridge(monkeypatch=None, env: dict | None = None):
    """Load bridge.py FRESH. Module-level registry state is how the service
    actually runs, so a re-import IS a bridge restart — which is what makes the
    persistence test a real restart and not a mock of one."""
    if monkeypatch is not None:
        for k in _ENV_KEYS:
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("can_bridge_announce", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Bus:
    def __init__(self):
        self.sent: list[tuple[int, bytes]] = []

    def send(self, arb, data, eff=False):
        self.sent.append((arb, data))

    def close(self):
        pass


def _can_id(ns_hash: int, *, type_code: int = 0, src: int = 0,
            scope: int = 1, prio: int = 5) -> int:
    """[BP-CAN-ID] v2, hand-built: prio(3)|type(4)|ns(16)|src(4)|scope(2).

    Hand-built rather than imported ON PURPOSE, the same idiom
    test_can_bridge_registry.py uses: a test that encodes with the encoder it is
    testing proves only that the encoder is self-consistent."""
    return (prio << 26) | (type_code << 22) | (ns_hash << 6) | (src << 2) | scope


def _announce_over_can(mod, names, *, src: int = 0, frames=None):
    """Play a full announcement at the bridge, frame by frame, off `can0`."""
    frames = frames if frames is not None else announcement_frames(names)
    out = []
    for f in frames:
        out.append(mod.message_from_can(
            _can_id(NS_ANNOUNCE_HASH, type_code=5, src=src), f))
    return out


# --------------------------------------------------------------------------- #
# G1 — the root itself                                                         #
# --------------------------------------------------------------------------- #

def test_the_well_known_root_is_pinned_and_matches_the_hash_function():
    """If these two numbers ever disagree, every flashed node is transmitting
    on an id nothing listens to — a fleet-wide silence, not a rename."""
    assert namespace_hash(NS_ANNOUNCE) == NS_ANNOUNCE_HASH == 0x31AE
    assert namespace_hash(NS_ANNOUNCE_POLL) == NS_ANNOUNCE_POLL_HASH == 0xC77C


def test_the_root_is_registered_at_import_not_by_configuration(monkeypatch):
    """The one entry that cannot be learned must also be one nobody can fail to
    configure. It is registered by importing the module — no main(), no env."""
    mod = _load_bridge(monkeypatch)
    assert mod._name_to_hash[NS_ANNOUNCE] == NS_ANNOUNCE_HASH
    assert mod._hash_to_name[NS_ANNOUNCE_HASH] == NS_ANNOUNCE
    assert mod._provenance[NS_ANNOUNCE] == mod.PROV_WELLKNOWN
    # and it is NOT in the editable game vocabulary
    assert NS_ANNOUNCE not in mod.BUILTIN_NAMESPACES


def test_an_announcement_fits_classic_can_frames():
    """The format is sized for the CONTROLLER, not for a setting. Eight bytes."""
    frames = announcement_frames(DEV_NAMESPACES)
    assert frames, "an announcement with names produced no frames"
    assert all(len(f) <= 8 for f in frames), \
        [len(f) for f in frames if len(f) > 8]
    assert len(frames[0]) == 8, "the header must be exactly one full frame"


# --------------------------------------------------------------------------- #
# 2. PROVE IT FAILS FIRST                                                      #
# --------------------------------------------------------------------------- #

def test_the_gap_is_real_before_the_announcement(monkeypatch, caplog):
    """THE NEGATIVE CONTROL. Same frame, same bridge, no announcement: the name
    is unresolvable, the frame is DROPPED, the drop is counted by hash, and the
    log says so. Without this assertion the positive test below could pass on a
    bridge that resolved everything by accident."""
    mod = _load_bridge(monkeypatch)
    ns = f"_device/{DEV}/heartbeat"
    h = namespace_hash(ns)

    # nothing anywhere knows this name
    assert ns not in mod._name_to_hash
    assert h not in mod._hash_to_name
    assert ns not in mod.BUILTIN_NAMESPACES

    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    published: list[str] = []
    bridge._mqtt_client = type("_C", (), {
        "publish": lambda self, t, p, qos=0, retain=False: published.append(t)})()

    with caplog.at_level("WARNING"):
        for _ in range(4):
            msg = mod.message_from_can(_can_id(h, src=0), b"\xfe\x00\x00\x00\x2a")
            assert msg is None, "an unresolvable hash must NOT decode to a name"
            bridge.count_drop(f"0x{h:04X}", mod.DROP_UNRESOLVABLE)

    assert published == [], "nothing may reach MQTT under a fabricated name"
    assert mod._unresolved_hashes[h] == 4, "the drop must be COUNTED"
    assert bridge.drops[f"0x{h:04X}"][mod.DROP_UNRESOLVABLE] == 4
    warns = [r for r in caplog.records if "UNRESOLVABLE" in r.message]
    assert len(warns) == 1, "loud once per hash, then counted"
    assert NS_ANNOUNCE in warns[0].message, \
        "the failure must name the mechanism that fixes it"

    # and it is visible on the bus, not only in a log nobody tails
    blob = bridge.status_blobs()[mod.NS_DROPS]
    assert blob["unresolvable_hashes"][f"0x{h:04X}"] == 4


def test_a_prefix_rule_could_not_have_helped():
    """The reason this design exists, asserted rather than asserted-in-prose:
    the hashes of `_device/<id>/heartbeat` for two different ids share nothing,
    so no expansion of `_device/*` produces them without the ids."""
    a = namespace_hash("_device/hosyond-080739/heartbeat")
    b = namespace_hash("_device/wsc6-07f199/heartbeat")
    assert a != b
    # and the prefix itself hashes to something unrelated to either
    p = namespace_hash("_device/")
    assert p != a and p != b


# --------------------------------------------------------------------------- #
# 1. THE ACCEPTANCE: a CAN-originated name resolves after announcement         #
# --------------------------------------------------------------------------- #

def test_a_can_originated_name_resolves_after_announcement(monkeypatch, caplog):
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    published: list[tuple[str, str]] = []
    bridge._mqtt_client = type("_C", (), {
        "publish": lambda self, t, p, qos=0, retain=False:
            published.append((t, p))})()

    ns = f"_device/{DEV}/heartbeat"
    h = namespace_hash(ns)

    # -- BEFORE: dropped, counted, unresolvable ------------------------------
    assert mod.message_from_can(_can_id(h), b"\xfe\x00\x00\x00\x2a") is None
    assert mod._unresolved_hashes[h] == 1

    # -- THE ANNOUNCEMENT, over CAN, as chunk frames on the well-known root --
    with caplog.at_level("INFO"):
        results = _announce_over_can(mod, DEV_NAMESPACES, src=3)
    assert all(r is None for r in results), \
        "announcement frames are CONSUMED, never relayed as bus values"
    assert published == [], \
        "raw chunk bytes must not reach MQTT dressed as a device state"

    # -- AFTER: every announced name is registered, in BOTH directions -------
    for name in DEV_NAMESPACES:
        assert mod._name_to_hash[name] == namespace_hash(name)
        assert mod._hash_to_name[namespace_hash(name)] == name
        assert mod._provenance[name] == mod.PROV_CAN

    # -- and the ORIGINAL frame now decodes to the NAME ----------------------
    msg = mod.message_from_can(_can_id(h, src=3), b"\xfe\x00\x00\x00\x2a")
    assert msg is not None and msg.namespace == ns
    assert msg.source == mod.CAN_SOURCE

    bridge._forward_to_mqtt(msg)
    assert len(published) == 1
    topic, payload = published[0]
    assert topic == f"{mod.MQTT_PREFIX}/global/{ns}"
    assert json.loads(payload)["ns"] == ns

    # -- the repair is REPORTED, with what the gap cost ----------------------
    assert h not in mod._unresolved_hashes, "it is no longer unresolvable"
    name, dropped = mod._resolved_after_announce[h]
    assert (name, dropped) == (ns, 1)
    drops = bridge.status_blobs()[mod.NS_DROPS]
    assert drops["resolved_after_announce"][f"0x{h:04X}"] == {
        "name": ns, "frames_dropped_before": 1}
    assert any("now RESOLVABLE" in r.message for r in caplog.records)


def test_no_hand_typed_registry_entry_is_required(monkeypatch):
    """ACCEPTANCE 4. The environment is EMPTY — no CAN_BRIDGE_REGISTER at all —
    and five `_device/<id>/*` namespaces still become nameable."""
    mod = _load_bridge(monkeypatch, {})
    assert mod.parse_namespace_list(None) == []
    import os as _os
    assert not _os.environ.get("CAN_BRIDGE_REGISTER")

    _announce_over_can(mod, DEV_NAMESPACES, src=1)
    for name in DEV_NAMESPACES:
        assert mod._hash_to_name[namespace_hash(name)] == name

    bridge = mod.CANMQTTBridge()
    reg = bridge.status_blobs()[mod.NS_FORWARDING]["registry"]
    learned = {n for n, e in reg.items() if e["via"] == mod.PROV_CAN}
    assert learned == set(DEV_NAMESPACES)
    assert bridge.status_blobs()[mod.NS_FORWARDING]["learned_from_can"] == 5


def test_the_audit_channel_shows_provenance_for_every_entry(monkeypatch):
    """G4's requirement: persisted learning can persist a mistake, so the table
    must stay visible — and a row must not be able to hide where it came from."""
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTER": "canonly/thing"})
    for ns in mod.BUILTIN_NAMESPACES:
        mod.register_ns(ns, mod.PROV_BUILTIN)
    mod.register_ns("canonly/thing", mod.PROV_ENV)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()

    # one of each provenance
    class _M:
        payload = json.dumps({"ns": "throttle/left", "type": "state", "val": 1,
                              "src": "test", "scope": "global"}).encode()
    bridge._on_mqtt_message(None, None, _M())
    _announce_over_can(mod, [f"_device/{DEV}/health"], src=2)

    reg = bridge.status_blobs()[mod.NS_FORWARDING]["registry"]
    assert reg[NS_ANNOUNCE]["via"] == mod.PROV_WELLKNOWN
    assert reg["shields"]["via"] == mod.PROV_BUILTIN
    assert reg["canonly/thing"]["via"] == mod.PROV_ENV
    assert reg["throttle/left"]["via"] == mod.PROV_MQTT
    assert reg[f"_device/{DEV}/health"]["via"] == mod.PROV_CAN
    assert all(e["hash"] == f"0x{namespace_hash(n):04X}"
               for n, e in reg.items())


# --------------------------------------------------------------------------- #
# every failure mode teaches the bridge NOTHING, and says so                   #
# --------------------------------------------------------------------------- #

def test_a_corrupted_announcement_learns_nothing(monkeypatch, caplog):
    mod = _load_bridge(monkeypatch)
    frames = list(announcement_frames(DEV_NAMESPACES))
    c = bytearray(frames[4]); c[3] ^= 0xFF; frames[4] = bytes(c)

    with caplog.at_level("ERROR"):
        _announce_over_can(mod, None, src=4, frames=frames)

    for name in DEV_NAMESPACES:
        assert name not in mod._name_to_hash, \
            "a CRC-failed announcement must not install ANY name"
    assert mod._announce_learner.counts["mismatch"] == 1
    assert any("DISCARDED, no name learned" in r.message for r in caplog.records)


def test_an_incomplete_announcement_learns_nothing(monkeypatch):
    """A LOST frame, not a corrupt one — a different fault with a different
    report. `ack_seq` distinguishes them and the census must too."""
    mod = _load_bridge(monkeypatch)
    frames = announcement_frames(DEV_NAMESPACES)
    _announce_over_can(mod, None, src=4, frames=frames[:-1])
    for name in DEV_NAMESPACES:
        assert name not in mod._name_to_hash
    assert mod._announce_learner.counts["complete"] == 0
    assert mod._announce_learner.counts["progress"] > 0


def test_a_stray_data_frame_is_reported_not_guessed(monkeypatch, caplog):
    mod = _load_bridge(monkeypatch)
    frames = announcement_frames(DEV_NAMESPACES)
    with caplog.at_level("WARNING"):
        _announce_over_can(mod, None, src=7, frames=frames[1:])   # no header
    assert mod._announce_learner.counts["stray"] > 0
    assert not any(n in mod._name_to_hash for n in DEV_NAMESPACES)
    assert any("no header" in r.message for r in caplog.records)


def test_two_unassigned_announcers_collide_LOUDLY(monkeypatch, caplog):
    """THE HONEST LIMIT, asserted rather than footnoted. `src == 0` means
    UNASSIGNED and nothing allocates node ids yet, so two announcing boards
    share one reassembler. They must not silently produce a plausible table."""
    mod = _load_bridge(monkeypatch)
    a = announcement_frames([f"_device/{DEV}/heartbeat"])
    b = announcement_frames(["_device/wsc6-07f199/heartbeat"])

    with caplog.at_level("WARNING"):
        mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=0), a[0])
        mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=0), a[1])
        for f in b:                     # the second board cuts in
            mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=0), f)

    assert mod._announce_learner.counts["interleaved"] == 1
    assert any("ABANDONED" in r.message for r in caplog.records)
    # the INTERRUPTED one taught nothing; the interrupting one completed
    assert f"_device/{DEV}/heartbeat" not in mod._name_to_hash
    assert "_device/wsc6-07f199/heartbeat" in mod._name_to_hash

    # ALLOCATED node ids make them independent — the actual fix, proven here so
    # the recommendation is not a guess.
    mod2 = _load_bridge(monkeypatch)
    for f in a:
        mod2.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=1), f)
    for f in b:
        mod2.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=2), f)
    assert f"_device/{DEV}/heartbeat" in mod2._name_to_hash
    assert "_device/wsc6-07f199/heartbeat" in mod2._name_to_hash


def test_a_malformed_name_is_refused_and_the_rest_still_learned(monkeypatch, caplog):
    """The CRC already passed, so a bad name is a SENDER defect, not corruption.
    Refuse the one name loudly; do not punish the other four."""
    mod = _load_bridge(monkeypatch)
    payload = b"\n".join([
        f"_device/{DEV}/heartbeat".encode(),
        b"has a space",                       # illegal
        b"\x01\x02control",                   # illegal
        f"_device/{DEV}/build".encode(),
    ])
    from composeio.config_chunk import chunk_config
    with caplog.at_level("ERROR"):
        _announce_over_can(mod, None, src=6,
                           frames=chunk_config(payload, CAN_CONTENT_BYTES))

    assert mod._name_to_hash.get(f"_device/{DEV}/heartbeat") is not None
    assert mod._name_to_hash.get(f"_device/{DEV}/build") is not None
    assert mod._announce_learner.names_refused == 2
    assert len([r for r in caplog.records if "REFUSED" in r.message]) == 2


def test_an_over_long_name_is_refused_at_both_ends():
    """A truncated namespace is a DIFFERENT namespace that resolves cleanly —
    the same failure class as the truncated CAN value the bridge stopped doing."""
    too_long = "_device/" + "x" * MAX_NAME_BYTES
    with pytest.raises(NsAnnounceError):
        encode_names([too_long])
    accepted, refused = decode_names(too_long.encode())
    assert accepted == [] and len(refused) == 1
    assert "REFUSED, not truncated" in refused[0][1]


# --------------------------------------------------------------------------- #
# 5. COLLISION REFUSAL STILL HOLDS FOR AN ANNOUNCED NAME                       #
# --------------------------------------------------------------------------- #

def _find_collider(target_hash: int, avoid: str) -> str:
    """A real CRC-16/CCITT-FALSE collision, FOUND rather than asserted. If the
    hash function ever changes this search simply finds a different partner —
    a hardcoded pair would quietly stop testing anything."""
    for i in range(400_000):
        cand = f"ns/{i}"
        if cand != avoid and namespace_hash(cand) == target_hash:
            return cand
    raise AssertionError("no collider found in 400k candidates")


def test_an_announced_name_that_collides_is_refused_and_the_incumbent_keeps_it(
        monkeypatch, caplog):
    """ACCEPTANCE 5. Announcement changes WHERE a name comes from; it must not
    change the integrity rule. The incumbent is load-bearing — CAN nodes have
    already learned it — and the newcomer is not, whichever substrate it
    arrived on."""
    mod = _load_bridge(monkeypatch)
    announced = f"_device/{DEV}/heartbeat"
    incumbent = _find_collider(namespace_hash(announced), announced)
    assert namespace_hash(incumbent) == namespace_hash(announced)

    mod.register_ns(incumbent, mod.PROV_ENV)      # it got there first

    with caplog.at_level("ERROR"):
        _announce_over_can(mod, [announced, f"_device/{DEV}/build"], src=5)

    assert mod._hash_to_name[namespace_hash(announced)] == incumbent, \
        "the incumbent must keep the hash"
    assert mod._collisions[announced] == incumbent
    assert announced not in mod._name_to_hash
    # the OTHER announced name is unaffected — a collision is per-name
    assert f"_device/{DEV}/build" in mod._name_to_hash
    assert any("COLLISION" in r.message for r in caplog.records)

    # and a frame carrying that hash keeps resolving to the INCUMBENT, which is
    # the whole reason for the rule.
    msg = mod.message_from_can(_can_id(namespace_hash(announced)), b"\x01")
    assert msg is not None and msg.namespace == incumbent

    blob = mod.CANMQTTBridge().status_blobs()[mod.NS_DROPS]
    assert blob["collisions"][announced]["held_by"] == incumbent


# --------------------------------------------------------------------------- #
# 3. THE LEARNED TABLE SURVIVES A RESTART                                      #
# --------------------------------------------------------------------------- #

def test_the_learned_table_survives_a_bridge_restart(monkeypatch, tmp_path):
    """A re-import IS a restart: every registry dict in bridge.py is module
    level, so a fresh module object has exactly the state a fresh process has."""
    store = tmp_path / "registry.json"

    first = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(store)})
    assert first.REGISTRY_FILE == str(store)
    _announce_over_can(first, DEV_NAMESPACES, src=3)
    assert store.exists(), "a CAN-learned name must be persisted as it is learned"

    # --- the restart ---------------------------------------------------------
    second = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(store)})
    assert second is not first
    for name in DEV_NAMESPACES:
        assert second._hash_to_name[namespace_hash(name)] == name, \
            f"{name} did not survive the restart"
        assert second._provenance[name].startswith("persisted:"), \
            "a restored row must not masquerade as an observation made this run"

    # a frame decodes with NO announcement in this process at all
    msg = second.message_from_can(
        _can_id(namespace_hash(f"_device/{DEV}/health")), b"\xfe\x00\x00\x00\x01")
    assert msg is not None and msg.namespace == f"_device/{DEV}/health"
    assert second._announce_learner.frames == 0, \
        "this process heard no announcement; the table came from the file"

    blob = second.CANMQTTBridge().status_blobs()[second.NS_FORWARDING]
    assert blob["persistence"]["file"] == str(store)
    assert blob["persistence"]["restored"] == len(DEV_NAMESPACES)
    assert blob["persistence"]["refused_on_load"] == []


def test_persistence_off_by_default_and_says_so(monkeypatch, caplog):
    """No default path. A hidden state file changes the answer at exactly the
    moment an operator restarts a misbehaving bridge."""
    with caplog.at_level("INFO"):
        mod = _load_bridge(monkeypatch)
    assert mod.REGISTRY_FILE is None
    assert any("is NOT persisted" in r.message for r in caplog.records)
    _announce_over_can(mod, DEV_NAMESPACES, src=3)
    blob = mod.CANMQTTBridge().status_blobs()[mod.NS_FORWARDING]
    assert blob["persistence"]["file"] is None
    assert blob["persistence"]["saves"] == 0


def test_only_what_was_LEARNED_is_persisted(monkeypatch, tmp_path):
    """Built-ins and env entries are re-derived every boot. Persisting them
    would let a stale file quietly override a code or unit-file change."""
    store = tmp_path / "r.json"
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(store)})
    mod.register_ns("shields", mod.PROV_BUILTIN)
    mod.register_ns("canonly/thing", mod.PROV_ENV)
    _announce_over_can(mod, [f"_device/{DEV}/scene"], src=3)

    names = {e["name"] for e in json.loads(store.read_text())["entries"]}
    assert names == {f"_device/{DEV}/scene"}
    assert "shields" not in names and NS_ANNOUNCE not in names


def test_a_persisted_entry_whose_hash_no_longer_verifies_is_REFUSED(tmp_path):
    """A PERSISTED MISTAKE, caught. Believing a file whose hashes disagree with
    this build would name CAN frames WRONG — which is worse than not naming
    them, because wrong is indistinguishable from right at the consumer."""
    store = tmp_path / "r.json"
    save_registry(store, {"good/one": "can", "bad/one": "can"})
    body = json.loads(store.read_text())
    for row in body["entries"]:
        if row["name"] == "bad/one":
            row["hash"] = "0x0000"          # a hash from another universe
    store.write_text(json.dumps(body))

    res = load_registry(store)
    assert "good/one" in res.entries
    assert "bad/one" not in res.entries
    assert not res.ok and len(res.problems) == 1
    assert "refusing 'bad/one'" in res.problems[0]
    assert "worse than not naming them" in res.problems[0]


def test_a_refused_persisted_entry_is_PUBLISHED_not_just_logged(
        monkeypatch, tmp_path):
    """The operator's mental model still says that namespace will be named. The
    only way they find out otherwise is if the bridge says so on the bus."""
    store = tmp_path / "r.json"
    save_registry(store, {"bad/one": "can"})
    body = json.loads(store.read_text())
    body["entries"][0]["hash"] = "0x0000"
    store.write_text(json.dumps(body))

    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_REGISTRY_FILE": str(store)})
    blob = mod.CANMQTTBridge().status_blobs()[mod.NS_FORWARDING]
    assert len(blob["persistence"]["refused_on_load"]) == 1
    assert "bad/one" in blob["persistence"]["refused_on_load"][0]


def test_a_persisted_file_from_another_version_is_refused_whole(tmp_path):
    store = tmp_path / "r.json"
    save_registry(store, {"a/b": "can"})
    body = json.loads(store.read_text())
    body["version"] = 99
    store.write_text(json.dumps(body))
    res = load_registry(store)
    assert res.entries == {} and len(res.problems) == 1
    assert "REFUSED WHOLE" in res.problems[0]


def test_a_persisted_collision_is_still_a_collision(tmp_path):
    """Two names, one hash, in the same file. Restoring both would install a
    mapping that a live observation would have refused."""
    store = tmp_path / "r.json"
    a = "ns/2885"
    b = _find_collider(namespace_hash(a), a)
    save_registry(store, {a: "can", b: "can"})
    res = load_registry(store)
    assert len(res.entries) == 1
    assert any("already held by" in p for p in res.problems)


# --------------------------------------------------------------------------- #
# the startup poll                                                             #
# --------------------------------------------------------------------------- #

def test_the_bridge_polls_for_announcements_on_startup(monkeypatch):
    """The bridge has just lost its table and the nodes are the only authority
    on their own names. The OUTBOUND direction never needed a registry."""
    mod = _load_bridge(monkeypatch)
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    assert bridge.poll_announcements() is True
    assert len(bridge._can_bus.sent) == 1
    arb, data = bridge._can_bus.sent[0]
    assert mod.can_id_ns_hash(arb) == NS_ANNOUNCE_POLL_HASH
    assert bridge.announce_polls == 1
    assert bridge.status_blobs()[mod.NS_FORWARDING][
        "announce_root"]["polls_sent"] == 1


def test_the_startup_poll_can_be_turned_off(monkeypatch):
    mod = _load_bridge(monkeypatch, {"CAN_BRIDGE_ANNOUNCE_POLL": "0"})
    bridge = mod.CANMQTTBridge()
    bridge._can_bus = _Bus()
    assert bridge.poll_announcements() is False
    assert bridge._can_bus.sent == []


# --------------------------------------------------------------------------- #
# THE FLEET TOOL'S OWN PATH — CANTransport, no bridge, no broker               #
# --------------------------------------------------------------------------- #
# `composeio fleet --transport type=can,channel=can0` runs on a real
# CANTransport with no MQTT anywhere. It has its OWN hash->name map, populated
# only from what it has sent or subscribed — so before this it could observe a
# board's frames and never learn what they were called, which is precisely why
# the tool printed "CAN devices cannot be NAMED here on any path".

def _can_transport():
    from composeio.transport_can import CANTransport
    return CANTransport(channel="loopback")


def test_the_fleet_tools_transport_cannot_name_a_device_before_announcement(caplog):
    """THE NEGATIVE CONTROL for the tool's own path."""
    t = _can_transport()
    seen: list = []
    t.subscribe_all(seen.append)
    ns = f"_device/{DEV}/heartbeat"

    with caplog.at_level("WARNING"):
        t._on_frame(_can_id(namespace_hash(ns)), b"\xfe\x00\x00\x00\x2a")

    assert seen == [], "an unresolvable hash must not be delivered under a name"
    assert any("unknown namespace hash" in r.message for r in caplog.records)


def test_the_fleet_tools_transport_names_a_device_after_announcement():
    """ACCEPTANCE 1 on the tool's own path: same frame, named, delivered."""
    t = _can_transport()
    seen: list = []
    t.subscribe_all(seen.append)
    ns = f"_device/{DEV}/heartbeat"

    for f in announcement_frames(DEV_NAMESPACES):
        t._on_frame(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=3), f)

    assert set(t.announced) == set(DEV_NAMESPACES)
    assert seen == [], "the announcement frames themselves are CONSUMED"

    # The value bytes come from the CANON encoder, not hand-typed: the point
    # of this assertion is the NAME, and a hand-built value that decoded to
    # something else would make the test fail for the wrong reason (it did).
    from composeio.transport_uart import encode_value
    t._on_frame(_can_id(namespace_hash(ns), src=3), encode_value(42))
    assert len(seen) == 1 and seen[0].namespace == ns
    assert seen[0].value == 42

    # and the fleet decoder makes a DEVICE out of it — the actual end goal
    from composeio.device_announce import parse_namespace
    assert parse_namespace(seen[0].namespace) == (DEV, "heartbeat")


def test_the_transport_refuses_an_announced_collision():
    """An announcement is evidence, not authority."""
    t = _can_transport()
    ns = f"_device/{DEV}/heartbeat"
    incumbent = _find_collider(namespace_hash(ns), ns)
    t.subscribe(incumbent, lambda m: None)      # it got there first

    for f in announcement_frames([ns]):
        t._on_frame(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=3), f)

    assert t._hash_to_ns[namespace_hash(ns)] == incumbent
    assert ns not in t.announced


def test_the_well_known_root_is_preregistered_in_the_transport():
    t = _can_transport()
    assert t._hash_to_ns[NS_ANNOUNCE_HASH] == NS_ANNOUNCE


# --------------------------------------------------------------------------- #
# the census can move — every counter, at least once                           #
# --------------------------------------------------------------------------- #

def test_every_announcement_counter_can_increment(monkeypatch):
    """A counter nothing has ever been observed to move may not be wired up at
    all. This repo has shipped four of those. Drive each one."""
    mod = _load_bridge(monkeypatch)
    lr = mod._announce_learner
    frames = announcement_frames(DEV_NAMESPACES)

    _announce_over_can(mod, None, src=1, frames=frames)              # started+progress+complete
    _announce_over_can(mod, None, src=1, frames=[frames[1]])         # duplicate? no: stray-free
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=2), frames[1])  # stray
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=3), b"\x00")    # malformed
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=4), frames[0])
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=4), frames[1])
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=4), frames[1])  # duplicate
    mod.message_from_can(_can_id(NS_ANNOUNCE_HASH, type_code=5, src=4), frames[0])  # interleaved

    bad = list(frames)
    c = bytearray(bad[3]); c[3] ^= 0xFF; bad[3] = bytes(c)
    _announce_over_can(mod, None, src=9, frames=bad)                 # mismatch

    for name in ("started", "progress", "complete", "stray", "malformed",
                 "duplicate", "interleaved", "mismatch"):
        assert lr.counts[name] > 0, f"{name} never incremented — is it wired?"
