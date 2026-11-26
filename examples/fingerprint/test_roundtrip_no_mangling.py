"""Host unit tests for roundtrip_no_mangling.py — PURE LOGIC ONLY.

⛔ Runs no hardware and touches no broker/store.  It exercises the frame
scraper, the base64+sha256 decode-and-verify, and the byte comparison against a
SYNTHETIC frame shaped exactly the way the firmware's `r503 dump` emits one, and
proves that a corrupt-sha frame RAISES rather than silently passing bad bytes.

Run:  pytest examples/fingerprint/test_roundtrip_no_mangling.py
"""
import base64
import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import roundtrip_no_mangling as rt  # noqa: E402


def _emit_frame(raw: bytes, slot: int, *, wrap: int = 76,
                sha_override: str | None = None,
                noise: bool = False) -> str:
    """Reproduce the firmware's dump frame from raw bytes.

    Mirrors r503_cmd_dump: sha256 over the RAW bytes, base64 wrapped at 76 cols,
    framed by the BEGIN/END sentinels.  `sha_override` forges a wrong digest for
    the corruption case; `noise` interleaves an unrelated console line to prove
    the scraper tolerates it.
    """
    sha = sha_override or hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(raw).decode()
    lines = [b64[i:i + wrap] for i in range(0, len(b64), wrap)]
    body = "\n".join(lines)
    if noise:
        # a stray log line the way the CDC console might interleave one
        body = body + "\n[tick] uptime=3s heartbeat=ready"
    return (
        f"-----R503-DUMP-BEGIN slot={slot} bytes={len(raw)} sha256={sha}-----\n"
        f"{body}\n"
        f"-----R503-DUMP-END-----\n"
    )


def test_roundtrip_synthetic_frame_parses_and_matches():
    raw = bytes((i * 7 + 3) & 0xFF for i in range(1536))  # R503-sized template
    frame_text = _emit_frame(raw, slot=1)
    # scrape + decode + verify
    decoded = rt.scrape_and_decode(frame_text)
    assert decoded == raw
    # a second dump of the same bytes must compare byte-exact
    b = rt.scrape_and_decode(_emit_frame(raw, slot=0))
    equal, idx = rt.compare(decoded, b)
    assert equal is True
    assert idx is None


def test_scraper_reads_header_fields():
    raw = b"hello-template-bytes"
    frame = rt.scrape_frame(_emit_frame(raw, slot=3))
    assert frame.slot == 3
    assert frame.nbytes == len(raw)
    assert frame.sha256 == hashlib.sha256(raw).hexdigest()


def test_scraper_tolerates_interleaved_console_noise():
    raw = bytes(range(200))
    text = "[boot] some earlier log\n" + _emit_frame(raw, slot=2, noise=True)
    assert rt.scrape_and_decode(text) == raw


def test_corrupt_sha_raises():
    raw = b"the-real-bytes-abcdef"
    bad = _emit_frame(raw, slot=1, sha_override="0" * 64)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        rt.scrape_and_decode(bad)


def test_declared_length_mismatch_raises():
    # a frame whose BEGIN line lies about the byte count must be refused
    raw = b"twelve-bytes"
    sha = hashlib.sha256(raw).hexdigest()
    b64 = base64.b64encode(raw).decode()
    bad = (
        f"-----R503-DUMP-BEGIN slot=1 bytes={len(raw) + 5} sha256={sha}-----\n"
        f"{b64}\n-----R503-DUMP-END-----\n"
    )
    with pytest.raises(ValueError, match="length mismatch"):
        rt.scrape_and_decode(bad)


def test_missing_frame_raises():
    with pytest.raises(ValueError, match="no R503-DUMP-BEGIN"):
        rt.scrape_and_decode("nothing to see here\n[tick] uptime=1s\n")


def test_unterminated_frame_raises():
    raw = b"abc"
    text = (
        f"-----R503-DUMP-BEGIN slot=1 bytes=3 sha256={hashlib.sha256(raw).hexdigest()}-----\n"
        f"{base64.b64encode(raw).decode()}\n"  # no END marker
    )
    with pytest.raises(ValueError, match="no matching"):
        rt.scrape_and_decode(text)


def test_first_diff_and_compare():
    assert rt.first_diff(b"abc", b"abc") is None
    assert rt.first_diff(b"abc", b"abd") == 2
    assert rt.first_diff(b"abc", b"abcd") == 3      # length divergence
    assert rt.first_diff(b"abcd", b"abc") == 3
    equal, idx = rt.compare(b"xy", b"xz")
    assert equal is False and idx == 1


def test_payload_shape_matches_firmware_decoder():
    p = rt.build_template_set_payload(
        template_set_uuid="uuid-123", ref="fingerprint://abc")
    assert p["evt"] == "template_set"
    data = p["data"]
    assert data["template_set_uuid"] == "uuid-123"
    entry = data["entries"][0]
    # the fields onMqttMessage actually reads — and NOT {slot,tmpl,role}
    assert set(entry) == {"person", "ordinal", "character", "rbac", "template"}
    assert entry["template"] == "fingerprint://abc"
    assert "slot" not in entry
    assert "tmpl" not in entry
    assert data["role_capability_table"] == {"crew": ["open_hatch"]}


def test_base64_matches_python_stdlib_for_binary_with_nulls():
    # embedded nulls + high bytes: exactly what a real 1536B template carries
    raw = bytes([0, 255, 0, 1, 254, 128, 0, 0, 7]) * 40
    decoded = rt.scrape_and_decode(_emit_frame(raw, slot=1))
    assert decoded == raw


def test_format_failure_axis_sensor_with_code():
    # SENSOR axis carries the exact R503 confirmation byte, rendered as hex
    s = rt.format_failure_axis(
        {"axis": "sensor_refusal", "sensor_code": 0x0C,
         "detail": "Store 0x0C", "template_set_uuid": "u"})
    assert "axis=sensor_refusal" in s
    assert "sensor_code=0x0C" in s
    assert "Store 0x0C" in s


def test_format_failure_axis_transport_has_no_code():
    # TRANSPORT axis quotes no sensor_code -> reported 'absent', never a fake 0x00
    s = rt.format_failure_axis(
        {"axis": "transport",
         "detail": "GET http://buildhost.local:8423/... -> HTTP 404"})
    assert "axis=transport" in s
    assert "sensor_code=absent" in s
    assert "404" in s


def test_format_failure_axis_missing_fields_are_named_not_crashed():
    s = rt.format_failure_axis({})
    assert "<no axis field>" in s
    assert "<no detail field>" in s
