"""The contract version rule ([CXC-PC-VERSION-RULE]).

A host implements ONE contract version. It accepts a manifest with the same
MAJOR and a MINOR at or below its own. A newer MINOR, or a different MAJOR, is
REFUSED with BOTH versions named: the new plug-in breaks first, and the host
never half-reads a manifest it does not fully understand.
"""
from __future__ import annotations

import re

from .errors import ContractVersionRefused

#: The ONE contract version this library (and so a host built on it) implements.
HOST_CONTRACT = "0.1"

_CONTRACT_RE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")

_MISSING = object()


def parse_contract(value: object) -> "tuple[int, int]":
    """``"MAJOR.MINOR"`` -> ``(major, minor)``; anything else is refused
    naming the value ([CXC-PC-VERSION-RULE] boundary-case)."""
    if value is _MISSING:
        raise ContractVersionRefused(
            "contract: REFUSED — the required field is missing "
            '(expected a string "MAJOR.MINOR")',
            manifest=None, host=HOST_CONTRACT)
    if not isinstance(value, str):
        raise ContractVersionRefused(
            f"contract: REFUSED — {value!r} is not a string "
            '(expected "MAJOR.MINOR")',
            manifest=value, host=HOST_CONTRACT)
    m = _CONTRACT_RE.fullmatch(value)
    if m is None:
        raise ContractVersionRefused(
            f"contract: REFUSED — {value!r} is malformed "
            '(expected "MAJOR.MINOR", for example "0.1")',
            manifest=value, host=HOST_CONTRACT)
    return int(m.group(1)), int(m.group(2))


def check_contract(manifest_contract: object = _MISSING,
                   host_contract: str = HOST_CONTRACT) -> None:
    """Accept iff same MAJOR and MINOR <= the host's; else refuse naming both."""
    host_major, host_minor = parse_contract(host_contract)
    major, minor = parse_contract(manifest_contract)
    if major != host_major:
        raise ContractVersionRefused(
            f"contract: REFUSED — the manifest is written against contract "
            f"{manifest_contract}, this host implements contract {host_contract} "
            f"(different MAJOR)",
            manifest=manifest_contract, host=host_contract)
    if minor > host_minor:
        raise ContractVersionRefused(
            f"contract: REFUSED — the manifest is written against contract "
            f"{manifest_contract}, this host implements contract {host_contract} "
            f"(newer MINOR; this host cannot read it)",
            manifest=manifest_contract, host=host_contract)
