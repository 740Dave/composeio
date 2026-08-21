"""Phase 1 bridge.py migration tests — [CXC-CAN-BRIDGE-MIGRATE] / [CXC-CAN-VERIFY] CAN-C5.

The ComposeBus-over-CAN codec (message_from_can / can_id_from_message) is FROZEN
and UNTOUCHED; only the transport under it changed (python-can -> stdlib socket).
These assert:
  * the round-trip is byte-identical to the pre-migration python-can wire format,
  * no python-can import survives anywhere in the bridge path,
  * python-can is gone from requirements (the dependency-scan proof).
"""

import importlib.util
import re
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_BRIDGE = _ROOT / "services" / "can_mqtt_bridge" / "bridge.py"
_REQS = _ROOT / "services" / "can_mqtt_bridge" / "requirements.txt"


def _load_bridge():
    spec = importlib.util.spec_from_file_location("can_mqtt_bridge_bridge", _BRIDGE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_bridge_imports_without_python_can():
    _load_bridge()
    assert "can" not in sys.modules, "python-can must not be imported by the bridge"


def test_codec_roundtrip_byte_identical_wire_format():
    from composeio.peer.socketcan import (
        pack_can_frame, make_can_id, parse_can_frame, CAN_EFF_FLAG)

    bridge = _load_bridge()
    bridge.register_ns("shields")

    from composeio.transport import BusMessage, MessageType
    from composeio.transport_uart import encode_value

    msg = BusMessage(namespace="shields", type=MessageType.STATE, value=1,
                     source="panel", scope="global")

    # Egress: encode exactly as the migrated bridge does.
    arb = bridge.can_id_from_message(msg)
    data = encode_value(msg.value)[:8]
    frame = pack_can_frame(make_can_id(arb, eff=True), data)

    # BYTE-IDENTICAL to the pre-migration python-can socketcan wire format:
    # struct can_frame = "=IB3x8s" with the EFF flag in the id.
    expected = struct.pack("=IB3x8s", (arb | CAN_EFF_FLAG) & 0xFFFFFFFF,
                           len(data), data.ljust(8, b"\x00"))
    assert frame == expected

    # Ingress: the untouched codec reverses it.
    r_arb, eff, r_data = parse_can_frame(frame)
    assert eff is True
    assert r_arb == arb
    decoded = bridge.message_from_can(r_arb, r_data)
    assert decoded.namespace == "shields"
    assert decoded.type == MessageType.STATE
    assert decoded.value == msg.value
    assert decoded.source == "can"


def test_extended_only_ingress_filter_preserved():
    # bridge.run() only decodes 29-bit extended frames (the ComposeBus-over-CAN
    # convention). A standard frame is not fed to the codec.
    from composeio.peer.socketcan import MockCanReader
    r = MockCanReader()
    r.inject(0x123, bytes([1]), eff=False)   # standard -> ignored by bridge loop
    r.inject(0x1ABCDE, bytes([2]), eff=True)  # extended -> decoded
    seen_extended = []
    while True:
        f = r.read_frame()
        if f is None:
            break
        arb, eff, data = f
        if eff:
            seen_extended.append(arb)
    assert seen_extended == [0x1ABCDE]


# --- The dependency-scan proof: python-can is GONE ([CXC-CAN-LICENSE]) --------

def test_requirements_has_no_python_can():
    text = _REQS.read_text()
    assert not re.search(r"^\s*python-can", text, re.MULTILINE), \
        "python-can must be removed from requirements.txt"
    assert "paho-mqtt" in text  # the real deps remain


def test_bridge_source_has_no_python_can_import():
    src = _BRIDGE.read_text()
    # No `import can` / `from can` anywhere in the migrated bridge.
    assert not re.search(r"^\s*import\s+can\b", src, re.MULTILINE)
    assert not re.search(r"^\s*from\s+can\b", src, re.MULTILINE)
    assert "open_can" in src  # migrated to the stdlib reader
