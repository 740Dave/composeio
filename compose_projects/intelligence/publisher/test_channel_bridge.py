"""Dry tests for channel_bridge._build_raw_in — the compose payload -> raw `/in` mapping.

Proves: (a) a dropped image forwards `image` to raw /in; (b) the text-only path stays
BYTE-IDENTICAL to the original {"from":"david","text":...} bridge output.

Run:  python3 compose_projects/intelligence/publisher/test_channel_bridge.py
      (or) cd compose_projects/intelligence/publisher && python3 -m pytest test_channel_bridge.py -q
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from channel_bridge import (_build_raw_in, _echo_envelope, _raw_topics,
                            _acquire_singleton, _singleton_lock_path)


def test_text_only_byte_identical():
    # This is EXACTLY what the original bridge emitted for a typed message.
    original = json.dumps({"from": "david", "text": "hello architect"})
    out = _build_raw_in({"text": "hello architect"})
    assert json.dumps(out) == original, (json.dumps(out), original)
    # a bare string commit is treated as the text, same output
    assert json.dumps(_build_raw_in("hello architect")) == original


def test_text_alt_body_keys():
    assert _build_raw_in({"content": "c"}) == {"from": "david", "text": "c"}
    assert _build_raw_in({"message": "m"}) == {"from": "david", "text": "m"}


def test_empty_commit_ignored():
    assert _build_raw_in({"text": "   "}) is None
    assert _build_raw_in("") is None
    assert _build_raw_in({}) is None


def test_dropped_image_forwarded():
    out = _build_raw_in({"from": "david", "image": "/tmp/x.png"})
    assert out == {"from": "david", "text": "", "image": "/tmp/x.png"}
    # image present -> NOT ignored even with empty text
    assert out is not None


def test_image_with_caption_text():
    out = _build_raw_in({"text": "look at this", "image": "/tmp/x.png"})
    assert out == {"from": "david", "text": "look at this", "image": "/tmp/x.png"}


def test_image_key_absent_leaves_output_unchanged():
    # No `image` key -> byte-identical text-only shape (no stray image field).
    out = _build_raw_in({"text": "t"})
    assert "image" not in out


# ── the display/relay path ([SPEC:CXC-CONSOLE-VISUAL] link health) ──────────

def test_raw_topics_includes_alive_and_keeps_in_out():
    # in/out are the FIRST two entries, unchanged; alive is ADDED third.
    assert _raw_topics("architect") == [
        "intelligence/architect/in",
        "intelligence/architect/out",
        "intelligence/architect/channel/alive",
    ]
    # throwaway id: the shape is id-parametric, no daemon needed to prove it
    assert _raw_topics("test-int-01")[2] == \
        "intelligence/test-int-01/channel/alive"


def test_alive_relays_to_composeio_scheme():
    ns = "intelligence/architect/channel/alive"
    env = _echo_envelope(ns, {"ts": 123, "id": "architect"}, 7, 1700.0)
    assert env["ns"] == ns
    assert env["val"] == {"ts": 123, "id": "architect"}
    assert env["scope"] == "global"          # target composeio/global/<ns>
    # the console subscribes composeio/+/<ns>; the bridge publishes global
    assert f"composeio/global/{env['ns']}" == \
        "composeio/global/intelligence/architect/channel/alive"


def test_in_out_envelope_byte_identical():
    # The envelope for the display path must be BYTE-IDENTICAL to the original
    # inline literal (same keys, same order) — pin the exact bytes.
    for ns in ("intelligence/architect/in", "intelligence/architect/out"):
        env = _echo_envelope(ns, {"from": "david", "text": "hi"}, 3, 1700.5)
        expected = ('{"ns": "%s", "type": "state", "src": "channel-bridge", '
                    '"ts": 1700.5, "scope": "global", "seq": 3, '
                    '"val": {"from": "david", "text": "hi"}}') % ns
        assert json.dumps(env) == expected, (json.dumps(env), expected)


def test_the_bus_envelope_matches_the_hand_built_one_key_for_key():
    """[CXC-SOB-CONTRACT-FROZEN], made EXECUTABLE for the waist conversion.

    The relay stopped hand-building `_echo_envelope` on the runtime path in
    2026-08-11's slice-7 conversion; `bus.publish_state` frames it now, through
    the canonical `BusMessage.to_json()`. The claim "the wire did not move" is
    the entire acceptance bar for [CXC-SOB-CHANNEL-LAST] — and on THIS channel
    a regression is SILENT, so it must be a test, not a reading of the diff.

    Compared key-for-key AND in ORDER (`json.dumps` preserves insertion order,
    and the pre-conversion bytes were pinned by the test above). `ts` and `seq`
    are the two deltas [CXC-SOB-WIRE-DELTAS] permits, so their VALUES are
    normalized out — their PRESENCE and POSITION are not.
    """
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "python"))
    from composeio.transport import BusMessage, MessageType

    ns, val = "intelligence/architect/in", {"from": "david", "text": "hi"}
    hand = _echo_envelope(ns, val, 3, 1700.5)
    via_bus = BusMessage(namespace=ns, type=MessageType.STATE, value=val,
                         source="channel-bridge", timestamp=1700.5,
                         scope="global", sequence=3).to_json()
    assert list(via_bus.keys()) == list(hand.keys()), (
        f"the envelope's FIELD ORDER moved: bus={list(via_bus.keys())} "
        f"hand={list(hand.keys())}")
    assert via_bus == hand, (via_bus, hand)


def test_a_composeio_topic_cannot_be_smuggled_through_the_foreign_seam():
    """The guard that keeps `publish_foreign` from becoming the next bypass.

    `channel_bridge` is the reason the foreign-channel seam exists, so it is
    the right place to pin that the seam REFUSES a ComposeBus address. Without
    this, a future relay could hand-frame `composeio/global/<ns>` through
    `publish_foreign` and rebuild the exact defect [CXC-SOB-THESIS] removed —
    while passing the paho seam-lint, because it imports no paho.
    """
    import pytest
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "python"))
    from composeio.bus import ComposeIOBus
    from composeio.transport_mqtt import MQTTTransport

    bus = ComposeIOBus(bus_id="t", global_transport=MQTTTransport(broker="x"))
    for bad in ("composeio/global/self/context", "composeio/local/x"):
        with pytest.raises(ValueError, match="not a foreign channel"):
            bus.publish_foreign(bad, {"x": 1})
        with pytest.raises(ValueError, match="not a foreign channel"):
            bus.subscribe_foreign(bad, lambda c, p: None)
    # ...and the relay's OWN channels are accepted (no over-broad refusal).
    for ok in _raw_topics("architect"):
        assert not ok.startswith("composeio/")
        MQTTTransport._reject_composeio_channel(ok)      # must not raise


# ── the SINGLETON relay guarantee ([SPEC:CXC-IC-CHANNEL] IC-C15) ────────────
# The message-doubling defect was TWO channel_bridge instances both relaying:
# each unwrapped the composer's /compose (David's message reached the Intelligence
# twice) AND echoed every raw topic (every thread bubble doubled). This proves the
# host-level lock makes the relay exactly-once: a SECOND acquire — the second
# instance, from either launch path — is DENIED, so it yields instead of doubling.

def test_singleton_second_instance_is_denied():
    import tempfile
    d = tempfile.mkdtemp(prefix="cb-singleton-")
    first = _acquire_singleton("architect", lock_dir=d)
    assert first is not None, "the first relay must acquire the singleton lock"
    # a second instance (a standalone process OR the in-process supervisor thread)
    # attempting to relay the SAME id is denied -> it must yield, not double.
    second = _acquire_singleton("architect", lock_dir=d)
    assert second is None, "a second relay acquired the lock — it would DOUBLE every message"
    # once the holder releases, a fresh relay can take over (failover, not double)
    first.close()
    third = _acquire_singleton("architect", lock_dir=d)
    assert third is not None, "after the holder exits, a new relay must be able to take over"
    third.close()


def test_singleton_distinct_ids_do_not_contend():
    # Two DIFFERENT intelligences each get their own relay — the lock is per <id>.
    import tempfile
    d = tempfile.mkdtemp(prefix="cb-singleton-")
    a = _acquire_singleton("architect", lock_dir=d)
    b = _acquire_singleton("xo", lock_dir=d)
    assert a is not None and b is not None, "per-id relays must not contend"
    assert _singleton_lock_path("architect", d) != _singleton_lock_path("xo", d)
    a.close(); b.close()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("PASS: channel_bridge._build_raw_in (image forward + text byte-identical)")
