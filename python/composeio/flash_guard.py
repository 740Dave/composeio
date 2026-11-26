#!/usr/bin/env python3
"""flash_guard — the ONE identity gate in front of every flash, upload or bring-up write.

Device identities are deployment config: they live in the HOST OVERLAY, never in public
code. A guard that compares a port's USB serial against a value written in the tree can
only ever match that value; for any real board it would pass, including the one it
exists to protect. So every identity is read from the environment, and every rule below
fails CLOSED.

The rules, all fail-CLOSED:

* ``FLASH_DENY_SERIALS`` (the never-flash list) must be set and non-empty. Unset or empty
  is a REFUSAL, never "nothing is denied".
* A board-specific expected-identity variable (e.g. ``R503_EXPECT_SERIAL``), when the
  flash path declares one, must be set; the port's serial must equal it.
* The target port must be explicit. Auto-detect picks "a" board and cannot be
  identity-checked before the write, so it is refused.
* The port's USB serial is read PASSIVELY (pyserial ``list_ports``: IOKit / sysfs; no port
  is opened, no reset). A port that cannot be resolved to a serial is refused.
* Only upload protocols that write THROUGH the checked serial port may proceed
  (``SERIAL_PROTOCOLS``: esptool, the only one the tree uses). Every other protocol
  (USB-ISP such as ``isp``/wchisp, ``wlink``/``wch-link``, JTAG and debug probes, OTA,
  ``custom``, unknown names, an empty name) is REFUSED: the guard cannot tie the write
  to the port it checked, so that path is NOT guarded and must be done by hand.
* Serials are compared after ONE normalisation, applied to both sides
  (``normalize_serial``): surrounding whitespace and quotes, a ``0x`` prefix and the
  ``:`` ``-`` ``.`` separators are removed, and case is folded. So ``AA:BB:..``,
  ``aabb..``, ``aa-bb-..``, ``"AA:BB:.."`` and ``0xAABB..`` are one serial.

Stdlib-only (pyserial is imported lazily, only to enumerate ports), so the PlatformIO hook
(python/scripts/pio_flash_guard.py) can load this file by path without importing the
``composeio`` package. CLI (used by firmware/r503_probe/build.sh):

    flash_guard.py check   --port PORT [--expect-var NAME]   # exit 0 allow, 3 refuse
    flash_guard.py resolve --expect-var NAME                  # print the port, after checks
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Sequence

DENY_VAR = "FLASH_DENY_SERIALS"
REFUSE_EXIT = 3

# PlatformIO targets that WRITE to a device. Anything matching is guarded.
# `__debug` is NOT here: it only builds debug firmware (IDEs request it for project
# metadata); `pio debug` loads the image through a debug probe, outside SCons, so that
# path is unguarded and documented as manual.
_FLASH_TARGET_RE = re.compile(r"(upload|erase|flash|program|bootloader|fuses)", re.I)

# ALLOWLIST: upload protocols whose write goes through the serial port the guard checks.
# esptool is the only one the tree uses over a serial port (every espressif32 env; it
# is the platform default). Anything else refuses: see check_flash.
SERIAL_PROTOCOLS = frozenset({"esptool"})

_MANUAL = ("This path is NOT guarded: flash it by hand, following the board descriptor's "
           "or the RUNBOOK's 'UNGUARDED: manual; check identity first' step.")


class FlashRefused(Exception):
    """The guard refused. ``str(exc)`` is the operator-facing reason."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    serial_number: Optional[str]


def normalize_serial(value: Optional[str]) -> str:
    """THE one serial normalisation, used on BOTH sides of every comparison (here and in
    docs/r503/probe-archive/r503probe.py): strip whitespace and quotes, drop a 0x
    prefix, remove ':' '-' '.' separators, fold case."""
    v = (value or "").strip().strip("\"'").strip()
    if v[:2].lower() == "0x":
        v = v[2:]
    return re.sub(r"[:\-.\s]", "", v).lower()


def parse_serial_list(raw: Optional[str]) -> frozenset:
    """Split a list on whitespace, ',' or ';', and normalise each entry."""
    if raw is None:
        return frozenset()
    out = (normalize_serial(t) for t in re.split(r"[\s,;]+", raw))
    return frozenset(t for t in out if t)


def is_flash_target(name: str) -> bool:
    return bool(_FLASH_TARGET_RE.search(name or ""))


def flash_targets(targets: Iterable[str]) -> list:
    return [t for t in targets if is_flash_target(t)]


def _port_key(device: str) -> str:
    """/dev/tty.X and /dev/cu.X name the same macOS device."""
    d = device.strip()
    if d.startswith("/dev/tty."):
        d = "/dev/cu." + d[len("/dev/tty."):]
    return d


def list_ports_passive() -> list:
    """Enumerate serial ports WITHOUT opening any of them."""
    try:
        from serial.tools import list_ports  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise FlashRefused(
            "refusing to flash: pyserial is not importable, so the target's USB serial "
            "cannot be read passively (%s)" % exc)
    return [PortInfo(p.device, p.serial_number) for p in list_ports.comports()]


def require_deny_list(environ: Mapping[str, str]) -> frozenset:
    deny = parse_serial_list(environ.get(DENY_VAR))
    if not deny:
        raise FlashRefused(
            "refusing to flash: %s is unset or empty. Set it in the host overlay to the "
            "USB serials that must never be written (the live controller first). An empty "
            "list is a refusal, not 'nothing is denied'." % DENY_VAR)
    return deny


def require_var(environ: Mapping[str, str], name: str, what: str) -> str:
    value = (environ.get(name) or "").strip()
    if not value:
        raise FlashRefused(
            "refusing to flash: %s is unset or empty (%s). Set it in the host overlay."
            % (name, what))
    return value


def serial_for_port(port: str, ports: Sequence[PortInfo]) -> str:
    want = _port_key(port)
    for p in ports:
        if _port_key(p.device) == want:
            if not p.serial_number:
                raise FlashRefused(
                    "refusing to flash: %s reports no USB serial number, so its identity "
                    "cannot be checked against %s" % (port, DENY_VAR))
            return p.serial_number
    if os.path.islink(port.strip()):
        raise FlashRefused(
            "refusing to flash: %s is a symlink. The guard matches the enumerated device "
            "path exactly and does not follow links, so a link's target cannot be "
            "identity-checked; pass the real /dev/cu.* path." % port)
    raise FlashRefused(
        "refusing to flash: %s is not a present serial port; cannot read its USB serial "
        "passively (ports seen: %s)" % (port, ", ".join(p.device for p in ports) or "none"))


def check_flash(port: Optional[str], environ: Mapping[str, str],
                ports: Optional[Sequence[PortInfo]] = None,
                expect_var: Optional[str] = None,
                port_var: Optional[str] = None,
                protocol: Optional[str] = None) -> str:
    """Return the target's USB serial if writing to ``port`` is allowed; else raise.

    ``protocol`` is the PlatformIO upload protocol (the hook always passes one; an empty
    string refuses). ``None`` means the caller is not PlatformIO and drives a serial tool
    itself (the CLI before a hand-run esptool, build.sh).

    Order matters: every overlay name is checked BEFORE any port is enumerated, so an
    unconfigured host refuses without touching the USB stack at all.
    """
    deny = require_deny_list(environ)
    expect = None
    if expect_var:
        expect = normalize_serial(require_var(
            environ, expect_var, "the USB serial of the one board this target may write"))
    if port_var:
        require_var(environ, port_var, "the upload port for this target")
    if protocol is not None and protocol.strip().lower() not in SERIAL_PROTOCOLS:
        raise FlashRefused(
            "refusing to flash: upload_protocol '%s' is not one whose write goes through the "
            "checked serial port (allowed: %s), so the guard cannot tie the write to an "
            "identity. %s" % (protocol.strip() or "<empty>", ", ".join(sorted(SERIAL_PROTOCOLS)), _MANUAL))
    if not port or not port.strip():
        raise FlashRefused(
            "refusing to flash: no explicit upload port. Auto-detect picks a board without "
            "an identity check; pass --upload-port (or set upload_port) so its USB serial "
            "can be checked against %s." % DENY_VAR)
    if ports is None:
        ports = list_ports_passive()
    raw = serial_for_port(port, ports)
    serial = normalize_serial(raw)
    if serial in deny:
        raise FlashRefused(
            "refusing to flash: %s has USB serial %s, which is on %s (never flash). "
            "There is no override: to reflash this board deliberately, remove its serial "
            "from %s for this one run, then put it back." % (port, serial, DENY_VAR, DENY_VAR))
    if expect is not None and serial != expect:
        raise FlashRefused(
            "refusing to flash: %s has USB serial %s, but %s names a different board."
            % (port, serial, expect_var))
    return raw


def resolve_port(environ: Mapping[str, str], expect_var: str,
                 ports: Optional[Sequence[PortInfo]] = None) -> str:
    """Find the ONE port whose serial equals ``$expect_var``, after the full check."""
    require_deny_list(environ)
    expect = normalize_serial(require_var(
        environ, expect_var, "the USB serial of the one board this target may write"))
    if ports is None:
        ports = list_ports_passive()
    hits = [p for p in ports if p.serial_number and normalize_serial(p.serial_number) == expect]
    if len(hits) != 1:
        raise FlashRefused(
            "refusing to flash: %d ports carry the serial named by %s (want exactly 1)"
            % (len(hits), expect_var))
    check_flash(hits[0].device, environ, ports=ports, expect_var=expect_var)
    return hits[0].device


def missing_env(names: Iterable[str], environ: Mapping[str, str]) -> list:
    return [n for n in names if not (environ.get(n) or "").strip()]


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="may PORT be written?")
    c.add_argument("--port", default="")
    c.add_argument("--expect-var", default=None)
    r = sub.add_parser("resolve", help="print the port of the board named by --expect-var")
    r.add_argument("--expect-var", required=True)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "check":
            serial = check_flash(args.port, os.environ, expect_var=args.expect_var)
            print("flash_guard: allow %s (USB serial %s)" % (args.port, serial), file=sys.stderr)
        else:
            print(resolve_port(os.environ, args.expect_var))
    except FlashRefused as exc:
        print(str(exc), file=sys.stderr)
        return REFUSE_EXIT
    return 0


if __name__ == "__main__":
    sys.exit(main())
