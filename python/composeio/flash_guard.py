#!/usr/bin/env python3
"""flash_guard — the ONE identity gate in front of every flash, upload or bring-up write.

WHY THIS EXISTS (slice 7d, 2026-11-26). The public scrub replaced the real device
identities in the "never flash the live controller" prose with IEEE documentation-range
MACs and serial placeholders. A guard that compares a port's USB serial
against a made-up value never matches, so it PASSES for the real controller it exists to
protect. Identity is deployment config: it lives in the HOST OVERLAY, never in public code.

The rules, all fail-CLOSED:

* ``FLASH_DENY_SERIALS`` (the never-flash list) must be set and non-empty. Unset or empty
  is a REFUSAL, never "nothing is denied".
* A board-specific expected-identity variable (e.g. ``R503_EXPECT_SERIAL``), when the
  flash path declares one, must be set; the port's serial must equal it.
* The target port must be explicit. Auto-detect picks "a" board and cannot be
  identity-checked before the write, so it is refused.
* The port's USB serial is read PASSIVELY (pyserial ``list_ports``: IOKit / sysfs; no port
  is opened, no reset). A port that cannot be resolved to a serial is refused.
* A port-less upload protocol (USB-ISP / debug probe) cannot be identity-checked here and
  is refused; flash it by hand per its board descriptor.

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
_FLASH_TARGET_RE = re.compile(r"(upload|erase|flash|program|bootloader|fuses|debug)", re.I)

# Upload protocols that do not go through a serial port: the target cannot be resolved
# to a USB serial here, so the guard refuses rather than waving them through.
PORTLESS_PROTOCOLS = frozenset({
    "wchisp", "wch-link", "wchlink", "minichlink", "stlink", "jlink", "cmsis-dap",
    "esp-prog", "esp-builtin", "ftdi", "olimex-arm-usb-ocd", "dfu", "picotool",
    "blackmagic", "atmel-ice", "mbed",
})


class FlashRefused(Exception):
    """The guard refused. ``str(exc)`` is the operator-facing reason."""


@dataclass(frozen=True)
class PortInfo:
    device: str
    serial_number: Optional[str]


def normalize_serial(value: str) -> str:
    return value.strip().upper()


def parse_serial_list(raw: Optional[str]) -> frozenset:
    if raw is None:
        return frozenset()
    return frozenset(normalize_serial(t) for t in re.split(r"[\s,;]+", raw) if t.strip())


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
    raise FlashRefused(
        "refusing to flash: %s is not a present serial port; cannot read its USB serial "
        "passively (ports seen: %s)" % (port, ", ".join(p.device for p in ports) or "none"))


def check_flash(port: Optional[str], environ: Mapping[str, str],
                ports: Optional[Sequence[PortInfo]] = None,
                expect_var: Optional[str] = None,
                port_var: Optional[str] = None,
                protocol: Optional[str] = None) -> str:
    """Return the target's USB serial if writing to ``port`` is allowed; else raise.

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
    if protocol and protocol.strip().lower() in PORTLESS_PROTOCOLS:
        raise FlashRefused(
            "refusing to flash: upload_protocol '%s' does not go through a serial port, so "
            "the target cannot be identity-checked against %s. Flash it by hand per its "
            "board descriptor." % (protocol, DENY_VAR))
    if not port or not port.strip():
        raise FlashRefused(
            "refusing to flash: no explicit upload port. Auto-detect picks a board without "
            "an identity check; pass --upload-port (or set upload_port) so its USB serial "
            "can be checked against %s." % DENY_VAR)
    if ports is None:
        ports = list_ports_passive()
    serial = normalize_serial(serial_for_port(port, ports))
    if serial in deny:
        raise FlashRefused(
            "refusing to flash: %s has USB serial %s, which is on %s (never flash)."
            % (port, serial, DENY_VAR))
    if expect is not None and serial != expect:
        raise FlashRefused(
            "refusing to flash: %s has USB serial %s, but %s names a different board."
            % (port, serial, expect_var))
    return serial


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
