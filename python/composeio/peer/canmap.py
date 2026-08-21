"""CAN-ID <-> namespace mapping + prefix->acceptance-mask lowering ([BPR-B-CANMAP]).

The CAN bridge is a Slice B CONSUMER of the Slice A core: the far side speaks
CAN, which addresses by 29-bit extended arbitration ID, not by namespace. This
module is the addressing seam — it maps a ComposeBus namespace to a contiguous
29-bit ID and lowers a trailing-glob prefix subscription ([SPEC:CXC-PATSUB]) to
ONE CAN acceptance filter+mask ([SCEN-BPR-B-MASK]).

29-bit extended ID bit-field layout (precedent: J1939 PGNs, CANopen — the
namespace HIERARCHY lives in the arbitration-ID bit-fields so a prefix is a
contiguous ID RANGE):

    bit  28        : ACK flag  (1 = a [BP-ACK-CAN] type-8 application-ack frame)
    bits 27..12    : 16-bit namespace SLOT (hierarchically allocated per prefix)
    bits 11..0     : 12-bit correlation id (``cid``, 0 for non-critical)

The 12-bit cid width is not incidental: [BP-ACK-CAN-CRITICAL] rolls the cid at
4095 precisely so it fits a CAN arbitration-ID field (see the 12-bit counter in
``critical.py``). Because the cid rides the LOW bits, a prefix acceptance mask
that covers the namespace-block bits and leaves the cid bits DON'T-CARE admits
every cid for the prefix's namespaces from ONE (filter, mask) pair.

Allocation is registry-backed: a prefix owns a power-of-two-aligned block of
slots, so the block's high bits are a clean mask. An unmapped namespace on
egress is an explicit ``MappingMiss`` — NEVER a fabricated ID ([BPR-B-CANMAP]).
"""

from __future__ import annotations

import threading

from composeio.bus import is_pattern

# --- 29-bit extended-ID field geometry ---------------------------------------
CAN_EXT_MASK = 0x1FFFFFFF          # 29 bits
ACK_FLAG = 1 << 28                 # bit 28: type-8 application-ack marker
SLOT_SHIFT = 12
SLOT_BITS = 16                     # bits 27..12
CID_BITS = 12                      # bits 11..0
SLOT_MASK = (1 << SLOT_BITS) - 1   # 0xFFFF
CID_MASK = (1 << CID_BITS) - 1     # 0xFFF
SLOT_FIELD_MASK = SLOT_MASK << SLOT_SHIFT

DEFAULT_BLOCK_BITS = 8             # 256 namespaces per top-level prefix by default


class MappingMiss(Exception):
    """An egress namespace (or ingress ID) has no registry mapping. Surfaced as
    an explicit fault by the bridge — never a fabricated ID/namespace."""


def _align_up(value: int, alignment: int) -> int:
    rem = value % alignment
    return value if rem == 0 else value + (alignment - rem)


def admits(filter_: int, mask: int, can_id: int) -> bool:
    """Standard CAN acceptance test: a frame is admitted iff its ID matches the
    filter under the mask. This is the software emulation of a hardware
    acceptance filter — the residue is dropped before it reaches the CPU
    ([SCEN-BPR-B-MASK])."""
    return (can_id & mask) == (filter_ & mask)


class CanIdRegistry:
    """Namespace<->29-bit-ID allocation registry ([BPR-B-CANMAP]).

    A top-level prefix (the first namespace segment) owns a power-of-two-aligned
    block of slots; each registered namespace under it gets the next slot in the
    block. Alignment is what makes a prefix's block expressible as ONE mask.
    """

    def __init__(self):
        self._lock = threading.RLock()
        # prefix -> [base_slot, block_size, next_offset]
        self._blocks: dict[str, list[int]] = {}
        self._ns_slot: dict[str, int] = {}
        self._slot_ns: dict[int, str] = {}
        self._next_base = 0

    # --- allocation --------------------------------------------------------

    def register_prefix(self, prefix: str, block_bits: int = DEFAULT_BLOCK_BITS) -> int:
        """Reserve an aligned block of ``2**block_bits`` slots for ``prefix``."""
        with self._lock:
            if prefix in self._blocks:
                return self._blocks[prefix][0]
            size = 1 << block_bits
            base = _align_up(self._next_base, size)
            if base + size > SLOT_MASK + 1:
                raise ValueError(f"slot space exhausted allocating prefix {prefix!r}")
            self._blocks[prefix] = [base, size, 0]
            self._next_base = base + size
            return base

    def register(self, namespace: str) -> int:
        """Allocate (or return) the slot for a full namespace. The top-level
        segment auto-registers a default block if not pre-declared."""
        with self._lock:
            if namespace in self._ns_slot:
                return self._ns_slot[namespace]
            prefix = namespace.split("/", 1)[0]
            if prefix not in self._blocks:
                self.register_prefix(prefix)
            base, size, off = self._blocks[prefix]
            if off >= size:
                raise ValueError(f"prefix block {prefix!r} full ({size} slots)")
            slot = base + off
            self._blocks[prefix][2] = off + 1
            self._ns_slot[namespace] = slot
            self._slot_ns[slot] = namespace
            return slot

    # --- namespace <-> id --------------------------------------------------

    def namespace_to_id(self, namespace: str, cid: int = 0) -> int:
        """Map a namespace (+ optional cid) to a 29-bit data-frame ID. Raises
        ``MappingMiss`` for an unregistered namespace — never fabricates an ID."""
        with self._lock:
            slot = self._ns_slot.get(namespace)
        if slot is None:
            raise MappingMiss(f"no CAN-ID mapping for namespace {namespace!r}")
        return (slot << SLOT_SHIFT) | (cid & CID_MASK)

    def id_to_namespace(self, can_id: int) -> str:
        """Recover the namespace from a 29-bit ID (cid/ack bits masked off).
        Raises ``MappingMiss`` for an unallocated slot."""
        slot = (can_id >> SLOT_SHIFT) & SLOT_MASK
        with self._lock:
            ns = self._slot_ns.get(slot)
        if ns is None:
            raise MappingMiss(f"no namespace for CAN slot {slot} (id=0x{can_id:08X})")
        return ns

    @staticmethod
    def cid_of(can_id: int) -> int:
        return can_id & CID_MASK

    @staticmethod
    def is_ack(can_id: int) -> bool:
        return bool(can_id & ACK_FLAG)

    def ack_id(self, namespace: str, cid: int) -> int:
        """The type-8 application-ack frame ID for a namespace+cid."""
        return ACK_FLAG | self.namespace_to_id(namespace, cid)

    # --- prefix -> ONE acceptance mask ([SCEN-BPR-B-MASK]) -----------------

    def prefix_to_mask(self, pattern: str) -> tuple[int, int]:
        """Lower a trailing-glob prefix subscription to ONE (filter, mask) CAN
        acceptance pair that admits exactly the matching data-ID range and
        rejects everything else (including type-8 ack frames — those ride a
        separate ack filter).

          * ``"*"``          -> all data frames
          * ``"prefix/*"``   -> the prefix's whole aligned slot block, any cid
          * ``"prefix"``     -> exactly that namespace's data frames, any cid
        """
        if pattern == "*":
            # Admit all DATA frames (ack bit must be 0).
            return (0, ACK_FLAG)
        with self._lock:
            if is_pattern(pattern):  # "prefix/*"
                prefix = pattern[:-2]
                block = self._blocks.get(prefix)
                if block is None:
                    raise MappingMiss(f"no CAN block for prefix {prefix!r}")
                base, size, _ = block
                block_hi = SLOT_MASK & ~(size - 1)          # slot bits that pin the block
                mask = ACK_FLAG | (block_hi << SLOT_SHIFT)  # ack=0 + block bits; cid don't-care
                filter_ = base << SLOT_SHIFT
                return (filter_, mask)
            # Exact namespace: pin the full slot, leave cid don't-care.
            slot = self._ns_slot.get(pattern)
            if slot is None:
                raise MappingMiss(f"no CAN-ID mapping for namespace {pattern!r}")
            mask = ACK_FLAG | SLOT_FIELD_MASK
            return (slot << SLOT_SHIFT, mask)

    # --- introspection -----------------------------------------------------

    def mapped_namespaces(self) -> list[str]:
        with self._lock:
            return sorted(self._ns_slot)
