"""[BPR-B-CANMAP] / [SCEN-BPR-B-MASK] — CAN-ID<->namespace mapping.

Thresholds realized here (mock/virtual CAN, no hardware):
  * a trailing-glob prefix subscription lowers to ONE CAN acceptance mask that
    admits exactly the matching 29-bit ID range and rejects everything else;
  * namespace->CAN-ID->namespace is identity for mapped namespaces;
  * an unmapped namespace raises an explicit mapping-miss (never a fabricated ID).
"""

import pytest

from composeio.peer.canmap import (
    CanIdRegistry, MappingMiss, admits, ACK_FLAG, CID_MASK, SLOT_SHIFT,
)


def _registry():
    reg = CanIdRegistry()
    # Two hierarchical prefixes get aligned blocks; several namespaces each.
    reg.register_prefix("jumpdrive", block_bits=4)   # 16-slot block
    reg.register_prefix("reactor", block_bits=4)
    for ns in ("jumpdrive/arm", "jumpdrive/throttle", "jumpdrive/status"):
        reg.register(ns)
    for ns in ("reactor/coolant", "reactor/scram"):
        reg.register(ns)
    return reg


def test_roundtrip_identity_for_mapped_namespaces():
    reg = _registry()
    for ns in ("jumpdrive/arm", "jumpdrive/status", "reactor/scram"):
        can_id = reg.namespace_to_id(ns)
        assert reg.id_to_namespace(can_id) == ns
        # cid rides the low 12 bits and does NOT disturb the namespace identity.
        crit_id = reg.namespace_to_id(ns, cid=1234)
        assert reg.id_to_namespace(crit_id) == ns
        assert reg.cid_of(crit_id) == 1234


def test_unmapped_namespace_raises_mapping_miss_not_a_fabricated_id():
    reg = _registry()
    with pytest.raises(MappingMiss):
        reg.namespace_to_id("weapons/fire")          # never allocated
    with pytest.raises(MappingMiss):
        reg.prefix_to_mask("weapons/*")


def test_prefix_subscription_lowers_to_one_acceptance_mask():
    # [SCEN-BPR-B-MASK]: "jumpdrive/*" -> ONE (filter, mask) admitting every
    # jumpdrive/... ID (any cid) and rejecting every non-jumpdrive ID + acks.
    reg = _registry()
    filter_, mask = reg.prefix_to_mask("jumpdrive/*")

    # Admits every jumpdrive namespace, with and without a cid.
    for ns in ("jumpdrive/arm", "jumpdrive/throttle", "jumpdrive/status"):
        assert admits(filter_, mask, reg.namespace_to_id(ns))
        assert admits(filter_, mask, reg.namespace_to_id(ns, cid=4095))

    # Rejects the sibling prefix's IDs.
    for ns in ("reactor/coolant", "reactor/scram"):
        assert not admits(filter_, mask, reg.namespace_to_id(ns))

    # Rejects type-8 ack frames (they ride a separate ack filter, not the data
    # subscription) — so the residue is dropped before the CPU.
    ack_id = reg.ack_id("jumpdrive/arm", cid=7)
    assert reg.is_ack(ack_id)
    assert not admits(filter_, mask, ack_id)


def test_mask_admits_the_whole_contiguous_block_and_nothing_above():
    # The prefix owns an aligned block; the mask must admit the ENTIRE block
    # (even slots not yet registered) and reject the adjacent block — proving it
    # is a contiguous ID range, not an enumeration of registered IDs.
    reg = _registry()
    filter_, mask = reg.prefix_to_mask("jumpdrive/*")
    base_slot = reg.namespace_to_id("jumpdrive/arm") >> SLOT_SHIFT
    block_base = base_slot  # arm is the first registered -> block base
    # Every slot in the 16-slot block, any cid, is admitted.
    for offset in range(16):
        for cid in (0, 1, CID_MASK):
            can_id = ((block_base + offset) << SLOT_SHIFT) | cid
            assert admits(filter_, mask, can_id)
    # The slot immediately past the block belongs to the next prefix -> rejected.
    past = ((block_base + 16) << SLOT_SHIFT)
    assert not admits(filter_, mask, past)


def test_star_matches_all_data_frames_but_not_acks():
    reg = _registry()
    filter_, mask = reg.prefix_to_mask("*")
    assert admits(filter_, mask, reg.namespace_to_id("jumpdrive/arm"))
    assert admits(filter_, mask, reg.namespace_to_id("reactor/scram", cid=9))
    assert not admits(filter_, mask, reg.ack_id("jumpdrive/arm", cid=1))


def test_exact_namespace_pattern_pins_one_namespace_any_cid():
    reg = _registry()
    filter_, mask = reg.prefix_to_mask("jumpdrive/arm")
    assert admits(filter_, mask, reg.namespace_to_id("jumpdrive/arm"))
    assert admits(filter_, mask, reg.namespace_to_id("jumpdrive/arm", cid=42))
    assert not admits(filter_, mask, reg.namespace_to_id("jumpdrive/status"))
