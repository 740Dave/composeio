"""THE ACCEPTANCE INSTRUMENT FORKED FROM THE THING IT ACCEPTS.

DEFECT-FLOAT32-PUBLISHED-FROM-CAN-ARRIVES-5AA80B, second head. The registered
defect named `services/can_mqtt_bridge/bridge.py::message_from_can` and its
private value decoder. That head was cut off on 2026-08-13 by [BP-VALUE-CODEC]
(the 0xFE/0xFF type tags) plus deleting the copy, and
`test_value_codec_type_ambiguity.py` pins it shut. The bridge is clean.

THE SAME DECODER IS STILL IN THE REPO, in `deploy/can_mqtt_bridge/
loopback_test.py`, and its own docstring said what it was: "Mirror of the
bridge's inline value decode". It mirrors a decoder that NO LONGER EXISTS —
byte for byte the deleted defect, including `int.from_bytes(data, "little")`
and no float branch at all. It never saw the 0xFE/0xFF tags because it was
written before them.

WHY THAT IS WORSE THAN THE ORIGINAL, NOT A LEFTOVER. This file is the
ACCEPTANCE TEST for the bridge deployment on rpi5 — the instrument that says
PASS or FAIL for the coordinated flag day (wire format v2 + binary ACK +
ns_announce). A forked instrument does not merely fail to catch a defect; it
MANUFACTURES one. Run against a CORRECT bridge:

  * `mqtt-to-can` computes its expected arbitration id from [BP-CAN-ID] **v1**
    (`ns<<13 | type<<9 | src9`). The bridge speaks v2
    (`prio(3)|type(4)|ns(16)|src(4)|scope(2)`). No id can ever match, so the
    verdict is "FAIL: no frame with the expected arbitration id" — loud, and
    a lie about a working bridge.
  * `can-to-mqtt` transmits an UNTAGGED 4-byte int32 (`struct.pack("<i", v)`)
    for any value over 255. The canon reads an untagged 4-byte value as opaque
    bytes ON PURPOSE ("visibly undecodable rather than invisibly wrong"), so
    the frame is correctly refused and the verdict is again a FAIL against a
    correct bridge.
  * `capture` and `describe` print `value=` from the forked decoder, so a human
    reading a 60-second live capture — the acceptance evidence — reads
    1109393408 where the wire says 42.0. That one is SILENT.

AN INDEPENDENT ORACLE IS NOT THE DEFECT — AN UNPINNED ONE IS.
`loopback_test.py` deliberately does not import composeio, and that rationale
is CORRECT and is kept: "a property read back through the same assumption that
wrote it cannot be self-verified". A verifier that called `namespace_hash`
would agree with a broken `namespace_hash`.

But a deliberate second implementation is only an oracle while it AGREES. The
moment it drifts it is an ordinary fork with a good excuse, and nothing in this
repo could tell the difference — the drift above sat on `main` undetected
because no test ever compared the two. So the reuse rule for a deliberate
reimplementation is not "delete it", it is:

    THE AGREEMENT IS THE THING THAT MUST BE SHARED, AND IT MUST BE ASSERTED.

This file is that assertion. It imports the canon and loads the oracle, and
pins them field by field. The oracle stays hand-written; it can no longer drift
in silence.
"""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pytest

from composeio.config import namespace_hash
from composeio.transport import MessageType, wire_code_for_type
from composeio.transport_can import (
    arbitration_priority, decode_arbitration_id, encode_arbitration_id,
)
from composeio.transport_uart import decode_value, encode_value

_REPO = Path(__file__).resolve().parents[2]
_ORACLE_PATH = _REPO / "deploy" / "can_mqtt_bridge" / "loopback_test.py"


def _load_oracle():
    """Load the deploy-side loopback harness as a module.

    It is a standalone script on the Pi (not a package, not importable by
    name), and it must STAY standalone — `deploy.sh` copies this one file to
    `/opt/composeio-can-bridge/` where `composeio` is not on the path. Loading
    it by file spec is how a test reaches a file whose whole point is that it
    has no import relationship with the code it verifies.
    """
    assert _ORACLE_PATH.exists(), f"{_ORACLE_PATH} is gone — the deploy-side " \
        "acceptance harness is what this file pins"
    spec = importlib.util.spec_from_file_location(
        "_can_loopback_oracle", _ORACLE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


oracle = _load_oracle()


# --- the value codec -------------------------------------------------------- #
# The registered defect's own worked example is first. 42.0 is chosen over 0.0
# for the reason the defect record gives: float32 `0.0` is four zero bytes and
# decodes to 0 under BOTH codecs, so a test written with 0.0 passes against the
# broken decoder and proves nothing. Distinct bytes, or no evidence.
FLOATS = [42.0, 3.14, -1.5, 1e6, 0.5]
BIG_INTS = [256, 1000, 70000, -1, -5, -70000]
SMALL = [0, 1, 200, 255]
STRINGS = ["ahead flank", "on", "warp_5"]


@pytest.mark.parametrize("value", FLOATS)
def test_oracle_decodes_float32_as_a_float(value):
    """42.0 must not read as 1109393408. THE registered defect, on this copy."""
    wire = encode_value(value)
    got = oracle.decode_value(wire)
    assert isinstance(got, float), (
        f"the deploy oracle read {wire.hex(' ')} as {got!r} "
        f"({type(got).__name__}) — a float32 on the wire is being reported as "
        f"an integer to whoever reads the acceptance output")
    assert got == pytest.approx(value)


@pytest.mark.parametrize("value", BIG_INTS)
def test_oracle_decodes_tagged_int32(value):
    """[BP-VALUE-CODEC]'s 0xFE tag. Negatives were wrong in EVERY untagged
    decoder, which is why converging the old copies was never the fix."""
    got = oracle.decode_value(encode_value(value))
    assert isinstance(got, int) and got == value


@pytest.mark.parametrize("value", SMALL + STRINGS)
def test_oracle_still_decodes_what_it_already_decoded(value):
    """The regression floor — the values that DID cross correctly, and are most
    of what has ever crossed this bridge, must keep crossing correctly."""
    assert oracle.decode_value(encode_value(value)) == value


def test_oracle_decode_agrees_with_canon_on_everything():
    """Field-by-field is nice; the whole corpus in one assertion is the pin."""
    disagree = []
    for value in FLOATS + BIG_INTS + SMALL + STRINGS + [None]:
        wire = encode_value(value)
        canon, orc = decode_value(wire), oracle.decode_value(wire)
        if canon != orc:
            disagree.append((value, wire.hex(" "), canon, orc))
    assert not disagree, (
        "the deploy oracle and the canonical codec read the same bytes "
        "differently:\n" + "\n".join(
            f"  {v!r:14} wire={w:20} canon={c!r:14} oracle={o!r}"
            for v, w, c, o in disagree))


def test_oracle_encodes_what_the_bridge_can_read():
    """`can-to-mqtt` INJECTS a value; if it encodes untagged the bridge is
    right to refuse it and the harness blames the bridge."""
    assert hasattr(oracle, "encode_value"), (
        "the harness hand-packs its injected value inline (`struct.pack(\"<i\", "
        "v)`), which is an untagged int32 — unreadable by a correct bridge")
    for value in FLOATS + BIG_INTS + SMALL + STRINGS:
        assert oracle.encode_value(value) == encode_value(value), (
            f"oracle encodes {value!r} differently from the canon")


def test_an_untagged_four_byte_value_is_not_guessed():
    """The canon refuses to guess a bare 4-byte width; the oracle must too, or
    a pre-flag-day peer's frame reads as a plausible number instead of as
    undecodable."""
    bare = b"\xe8\x03\x00\x00"
    assert decode_value(bare) == bare
    assert oracle.decode_value(bare) == bare, (
        "the oracle guessed a bare 4-byte value — that guess is the defect "
        "class itself, and it is what makes a stale peer look healthy")


# --- the arbitration id ----------------------------------------------------- #
ID_CASES = [
    ("throttle/left", MessageType.STATE, "global", 0),
    ("throttle/right", MessageType.STATE, "local", 7),
    ("shields", MessageType.EVENT, "global", 15),
    ("_device/announce", MessageType.DISCOVERY, "global", 3),
    ("alert_status", MessageType.ACK, "remote", 1),
]


@pytest.mark.parametrize("ns,mtype,scope,node", ID_CASES)
def test_oracle_builds_the_v2_arbitration_id(ns, mtype, scope, node):
    """[BP-CAN-ID] v2. The oracle shipped with v1 (`ns<<13|type<<9|src9`), so
    every expected id it computed was unmatchable against a correct bridge."""
    canon = encode_arbitration_id(ns, mtype, scope, node)
    got = oracle.expected_can_id(ns, mtype.value if hasattr(mtype, "value")
                                 else str(mtype), scope, node)
    # PRIORITY IS EXCLUDED BY DESIGN, both here and in `decode_arbitration_id`:
    # it is the sender's own opinion of its own urgency, it has already done its
    # only job by the time a receiver sees the frame, and an oracle that
    # restated the priority RULE TABLE would be forking a second table to check
    # the first. Compare the fields a receiver actually routes on.
    core = (1 << 26) - 1
    assert got & core == canon & core, (
        f"oracle id 0x{got:08X} vs canon 0x{canon:08X} for "
        f"{ns}/{mtype}/{scope}/node{node}")


@pytest.mark.parametrize("ns,mtype,scope,node", ID_CASES)
def test_oracle_splits_the_v2_arbitration_id(ns, mtype, scope, node):
    """The read direction — `capture` and `echo-check` both route on it."""
    arb = encode_arbitration_id(ns, mtype, scope, node)
    ns_h, tcode, src, sc = oracle.split_can_id(arb)
    c_ns, c_type, c_scope, c_src = decode_arbitration_id(arb)
    assert ns_h == c_ns == (namespace_hash(ns) & 0xFFFF)
    assert tcode == wire_code_for_type(mtype)
    assert src == c_src == node
    assert sc == c_scope == scope


def test_oracle_scope_is_not_dropped():
    """`scope` had no field in v1, so a `local` CAN message arrived relabelled
    `global` — the leak the mothercard trunk cannot tolerate. An oracle that
    cannot see the field cannot witness the fix."""
    local = encode_arbitration_id("throttle/left", MessageType.STATE, "local", 2)
    glob = encode_arbitration_id("throttle/left", MessageType.STATE, "global", 2)
    assert local != glob
    assert oracle.split_can_id(local)[3] == "local"
    assert oracle.split_can_id(glob)[3] == "global"


# --- the tables the oracle restates ---------------------------------------- #
def test_oracle_wire_codes_match_the_canonical_table():
    """[BP-TYPE-CODES]. Restated on purpose; therefore pinned on purpose."""
    for name, code in oracle.WIRE_CODES.items():
        assert wire_code_for_type(MessageType(name)) == code, (
            f"oracle wire code for {name} is {code}")


def test_oracle_crc_matches_the_namespace_hash():
    """The one restatement that was always correct. Kept, and now checked."""
    for ns in ("throttle/left", "throttle/right", "shields", "_device/announce",
               "composeio/status", "a", ""):
        assert oracle.crc16_ccitt_false(ns) == (namespace_hash(ns) & 0xFFFF), ns
    assert oracle.crc16_ccitt_false("123456789") == 0x29B1


# --- the class, not the instance ------------------------------------------- #
def test_no_int_guessing_value_decoder_survives_anywhere():
    """DEFECT-FLOAT32-...-5AA80B was an `int.from_bytes(data, "little")` in a
    value decoder. It was in the bridge; then it was in the deploy harness. This
    asks the whole shipping surface, so a THIRD site cannot appear quietly.

    ⛔ MATCHED ON THE AST, NOT ON THE TEXT. The first draft of this guard was a
    substring search and it fired on its OWN DOCSTRING — and on the comment in
    `loopback_test.py` that records what was removed. A text guard therefore
    punishes writing down the defect, which is the one habit this repo most
    wants to keep. `ast.walk` sees CALLS; prose, comments and docstrings are
    invisible to it, so the fix can be described in full and still pass.

    `peer/candecode.py` is EXCLUDED, and not as an exception waved through: it
    extracts DBC/J1939 SIGNAL BIT-FIELDS from a raw 8-byte payload — a foreign
    vehicle-bus format with no tag byte and no relationship to the ComposeIO
    value codec. Its `int.from_bytes` is the correct primitive for that job.
    """
    roots = [_REPO / "services", _REPO / "deploy", _REPO / "python" / "composeio"]
    offenders = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if path.name == "candecode.py":
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8",
                                                errors="replace"))
            except SyntaxError:            # not ours to police
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr == "from_bytes"
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "int"):
                    offenders.append(
                        f"{path.relative_to(_REPO)}:{node.lineno}")
    assert not offenders, (
        "an int-guessing byte decoder is back at:\n  " + "\n  ".join(offenders))


def test_the_bridge_itself_still_has_no_private_decoder():
    """The first head, kept cut. Duplicates part of
    `test_value_codec_type_ambiguity.py` deliberately: that file asserts the
    bridge imports the canon, this one asserts the whole CAN surface agrees,
    and the defect recurred precisely in the gap between two such checks."""
    src = (_REPO / "services" / "can_mqtt_bridge" / "bridge.py").read_text()
    assert "from composeio.transport_uart import decode_value" in src
    assert "struct.unpack" not in src, (
        "the bridge is unpacking wire values inline again")
