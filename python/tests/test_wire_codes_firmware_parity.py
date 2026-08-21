"""The firmware's copy of THE ONE TABLE cannot go stale unnoticed.

[SPEC:CIO-BUS-PROTOCOL] [BP-TYPE-CODES] [BP-UART-FRAME]

`MESSAGE_TYPE_WIRE_CODES` (composeio/transport.py) is one table because the
per-transport copies drifted — transport_uart had 8 of 10 entries,
transport_can 2, services/can_mqtt_bridge 5 — and every copy ended in
`.get(msg.type, 0)`, so an unlisted type silently went on the wire as STATE.
An `ack` came back from a UART round-trip as a spurious state change.

The ESP32 firmware cannot import Python, so it necessarily keeps a C++ mirror
(cyd/src/bus_wire_codes.h) checked by a host harness (`pio run -e
native_wire_codes`) against a header GENERATED from this table. That closes
half the loop: change the C++ and the harness fails. This test closes the other
half — change the PYTHON and forget to regenerate, and the harness would keep
comparing against a stale bake and keep passing.

So: regenerate into a temp dir and byte-compare with what is committed. A
failure here means run the generator, not edit the header.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CASES = [
    ("python/scripts/gen_wire_codes_header.py",
     "cyd/test/wire_codes_generated.h"),
    ("python/scripts/gen_uart_frame_corpus_header.py",
     "cyd/test/uart_frame_corpus_generated.h"),
    # [SPEC:CXC-BOARD-HW] workstream 0.2 — device self-announcement. Same
    # two-implementation shape, and here the drift is SILENT rather than loud:
    # a health word shifted 16 on one tier and 15 on the other decodes to a
    # plausible number, so `composeio fleet` would name the wrong firmware or
    # show a bus-off board as healthy with nothing anywhere reporting an error.
    ("python/scripts/gen_device_announce_header.py",
     "cyd/test/device_announce_generated.h"),
]


@pytest.mark.parametrize("script,header", CASES)
def test_generated_header_is_current(script: str, header: str) -> None:
    src = REPO / script
    baked = REPO / header
    assert src.exists(), f"{script} is missing"
    assert baked.exists(), (
        f"{header} is missing — run: python3 {script}")

    before = baked.read_bytes()
    subprocess.run([sys.executable, str(src)], check=True,
                   capture_output=True, cwd=REPO)
    after = baked.read_bytes()

    # Restore, so a failing run does not silently "fix" the tree and hide
    # the drift from the next reader.
    if after != before:
        baked.write_bytes(before)

    assert after == before, (
        f"{header} is STALE: regenerating it from the live Python table "
        f"produces different bytes. The firmware's wire-code / frame parity "
        f"harness is therefore checking the C++ against an OUT-OF-DATE bake "
        f"and would pass while the two tiers disagree on the wire. "
        f"Fix: python3 {script} — then re-run "
        f"`pio run -e native_wire_codes && pio run -e native_frame` in cyd/ "
        f"and update cyd/src/bus_wire_codes.h if THEY now fail.")
