"""No device MAC address may be published, except RFC 7042 documentation MACs.

Every tracked text file (`git ls-files`, from the repo root) is scanned for:

  A. a 6-octet MAC written with ':' or '-' separators, and
  B. a bare 12-hex-digit token (the form esptool's `mac=` line, PlatformIO dump file
     names and native-USB serials take), and
  C. an elided MAC tail: an ellipsis then three colon-separated octets (how docs quote
     the octets a device id is derived from). Only tails that begin "00:53:" pass.

Anything outside 00:00:5E:00:53:00/24 (RFC 7042 section 2.1.2, the documentation range)
fails. Form A has no allowlist. Form B has a small reviewed allowlist of tokens that are
NOT MACs (each one named with its reason, by a hash of the token so this file does
not repeat it). A failure message names only the file, line and column: never the token, and never a
hash of it (48 bits with a known vendor prefix is cheap to brute-force), so a failing
run cannot re-leak what it found.
"""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

DOC_RANGE = re.compile(r"^00[:-]?00[:-]?5e[:-]?00[:-]?53[:-]?[0-9a-f]{2}$", re.I)

SEPARATED = re.compile(
    r"(?<![0-9A-Fa-f:-])[0-9A-Fa-f]{2}([:-])(?:[0-9A-Fa-f]{2}\1){4}[0-9A-Fa-f]{2}(?![0-9A-Fa-f:-])")
TAIL3 = re.compile(r"(?:\u2026|\.\.\.)\s?([0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2})(?![0-9A-Fa-f:])")
BARE12 = re.compile(r"(?<![0-9A-Za-z.-])[0-9A-Fa-f]{12}(?![0-9A-Za-z-])")

# Form B tokens that are reviewed NOT to be MACs: (path, sha256(lowercase token)[:16]) -> reason.
ALLOWED_BARE12 = {
    ("compose_projects/intelligence/tools/beepout_viewer_launcher.py", "ce0fc87461c4ff05"):
        "a made-up test digest checked in a self-test",
    ("docs/architecture/GIT-MODEL-git-expert-verdict-2026-08-17.md", "2707d164e67e4fb8"):
        "a git commit id (12-char abbreviation)",
    ("docs/architecture/GIT-MODEL-git-expert-verdict-2026-08-17.md", "d4a9ae85e75cfc5c"):
        "a git commit id (12-char abbreviation)",
    ("firmware/can_filter_bench/src/main.cpp", "d4c1fbf464f5b339"):
        "the hex-letter character set passed to strpbrk",
    ("python/composeio/render/engine/parts/svg/esp32-s3-supermini.fzp.xml", "1e6990b9a7c2332b"):
        "a token inside a shop URL's query string",
    ("services/fingerprint_service/tests/test_integration.py", "bde81e9384b7848e"):
        "a made-up template uuid in a test",
    ("spec/01-composeio-bus-protocol.smd", "7f3820bf849e8b65"):
        "a byte count (disk_free_bytes) in an example payload",
}
# Whole files whose 12-hex runs are reviewed generated data, never addresses.
ALLOWED_BARE12_FILES = {
    "python/composeio/render/font_atlas_data.json": "generated glyph bitmaps (bitmap_hex rows)",
}


def _h(token: str) -> str:
    return hashlib.sha256(token.lower().encode()).hexdigest()[:16]


def _text_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    names = [n for n in out.decode().split("\0") if n]
    assert len(names) > 1000, f"only {len(names)} tracked files: not the repo root?"
    for name in names:
        p = REPO / name
        if not p.is_file() or p.is_symlink():
            continue
        data = p.read_bytes()
        if b"\0" in data[:8192]:
            continue
        yield name, data.decode("utf-8", "replace")


def find_macs():
    hits, scanned = [], 0
    for name, text in _text_files():
        scanned += 1
        for ln, line in enumerate(text.splitlines(), 1):
            for m in SEPARATED.finditer(line):
                if not DOC_RANGE.match(m.group(0)):
                    hits.append(f"{name}:{ln}:{m.start() + 1}: separated MAC")
            for m in TAIL3.finditer(line):
                if not m.group(1).lower().startswith("00:53:"):
                    hits.append(f"{name}:{ln}:{m.start() + 1}: elided MAC tail")
            if name in ALLOWED_BARE12_FILES:
                continue
            for m in BARE12.finditer(line):
                tok = m.group(0)
                if DOC_RANGE.match(tok) or (name, _h(tok)) in ALLOWED_BARE12:
                    continue
                hits.append(f"{name}:{ln}:{m.start() + 1}: bare 12-hex token")
    return hits, scanned


def test_no_device_mac_outside_the_documentation_range():
    hits, scanned = find_macs()
    assert scanned > 1000, f"scanned only {scanned} text files"
    assert not hits, (
        "device MAC-shaped values found; replace each with an RFC 7042 documentation MAC "
        "(00:00:5E:00:53:xx), or, if it is truly not a MAC, add it to ALLOWED_BARE12 with a "
        "reason:\n  " + "\n  ".join(hits))


def test_allowlist_entries_are_still_present():
    """A stale allowlist entry is a hole waiting for a new token: remove it."""
    present = set()
    for name, text in _text_files():
        for m in BARE12.finditer(text):
            present.add((name, _h(m.group(0))))
    stale = sorted(k for k in ALLOWED_BARE12 if k not in present)
    assert not stale, f"allowlist entries no longer in the tree: {stale}"


def test_the_scanner_can_fail():
    """Negative control: each form is caught, documentation MACs are not."""
    octets = ["1c", "2b", "3a", "4d", "5e", "6f"]        # assembled at run time, so this
    colon, dash, bare = ":".join(octets), "-".join(octets).upper(), "".join(octets)  # file holds no MAC
    sample = f"a {colon} b {dash} c mac={bare} d board_{bare}.bin"
    assert len(TAIL3.findall("\u2026" + colon[9:] + " ..." + colon[9:])) == 2
    doc_tail = "\u2026" + ":".join(["00", "53", "03"])
    assert TAIL3.findall(doc_tail)[0].startswith("00:53:")
    assert len(SEPARATED.findall(sample)) == 2
    assert len([t for t in BARE12.findall(sample) if not DOC_RANGE.match(t)]) == 2
    ok = "00:00:5E:00:53:03 00-00-5e-00-53-02 mac=00005e005305"
    assert all(DOC_RANGE.match(m.group(0)) for m in SEPARATED.finditer(ok))
    assert all(DOC_RANGE.match(t) for t in BARE12.findall(ok))
    assert not BARE12.findall("e1a82ba7-0c99-4f14-a7c9-4e7c40b1d0fc")   # a uuid tail is not a MAC
