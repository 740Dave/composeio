"""The v0.1 manifest validator ([CXC-PC-MANIFEST-FIELDS] and its neighbours).

This validator is AUTHORITATIVE. The JSON Schema shipped beside it
(``schema/manifest-0.1.schema.json``, see :func:`schema`) expresses every rule
a JSON Schema CAN express; the rules below need data the schema cannot see and
are enforced HERE ONLY:

  * the own-namespace rule: a ``bus`` namespace sits under
    ``plugins/<domain>/`` for THIS manifest's ``domain`` ([CXC-PC-BUS-BLOCK]);
  * the installing host's own key in ``hosts`` (``not supported on host
    <name>``) and that host's validation of its own section
    ([CXC-PC-HOST-SECTIONS]);
  * ``standard_questions`` naming a directory that exists inside the package
    ([CXC-PC-STANDARD-QUESTIONS]);
  * which refusal TEXT a refused key gets (reserved vs unknown);
  * the bus ``type`` list being computed from the installed composebus's
    ``MessageType`` ([CXC-PC-RULE-REAL-TYPES]);
  * three ``permissions`` rules ([CXC-PC-PERMISSIONS], r21): a bracketed
    IPv6 ``target`` in RFC 5952 canonical form, a ``path`` of at most 4096
    UTF-8 BYTES, and no duplicate entry.

``permissions`` is DECLARATION-ONLY ([CXC-PC-PERMISSIONS] never-verified):
this module checks that a declaration is well formed, and nothing anywhere in
the library checks, sandboxes or tests whether a plug-in's behaviour matches
it. The only readers of ``permissions`` are this validator, the consent
renderer and the grant builder (:mod:`compose_plugins.consent`).

Order: the ``contract`` version is checked FIRST and alone. A manifest the host
cannot read is never half-read ([CXC-PC-VERSION-RULE] which-side-breaks-first),
so a newer-MINOR manifest gets ONE refusal, naming both versions, and is not
also refused for keys a newer contract may define. Every other check then runs
and ALL refusals are reported together, each naming its field.

The validator reads JSON data only. It imports and runs nothing from the
plug-in ([CXC-PC-MANIFEST-FILE] rule).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, Callable, Iterable

from composebus.bus import validate_pattern
from composebus.transport import MessageType

from .errors import ContractVersionRefused, HostNameRefused, ManifestRefused, Refusal
from .package import eula_path_problem, read_eula
from .version import HOST_CONTRACT, check_contract, parse_contract

# --------------------------------------------------------------------------- #
# The field set ([CXC-PC-MANIFEST-FIELDS]).
# --------------------------------------------------------------------------- #
REQUIRED_FIELDS = ("contract", "domain", "name", "version", "description",
                   "documentation", "requirements", "run", "hosts")
OPTIONAL_FIELDS = ("issue_tracker", "codeowners", "isolation_floor", "bus",
                   "standard_questions", "permissions", "license", "eula")
#: [CXC-PC-V02-VERSION-0-2] rule (3): fields defined from contract 0.2. A
#: manifest is read by the contract it STATES, so one that states an earlier
#: contract and carries any of these is refused naming the key.
FIELDS_FROM_0_2 = ("license", "eula")
#: [CXC-PC-V02-LICENSE-FIELD] length: 1 to 256 ASCII characters
LICENSE_MAX_CHARS = 256
GENERATED_FIELDS = ("config_schema",)
CORE_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS + GENERATED_FIELDS

RUN_FIELDS = ("form", "entry")
BUS_FIELDS = ("publishes", "subscribes")
BUS_ENTRY_FIELDS = ("namespace", "type")

ISOLATION_LEVELS = ("inprocess", "subprocess")

# --------------------------------------------------------------------------- #
# Declared permissions ([CXC-PC-PERMISSIONS], r21): declaration-only.
# --------------------------------------------------------------------------- #
PERMISSIONS_FIELDS = ("network", "filesystem")
NETWORK_ENTRY_FIELDS = ("direction", "target", "purpose")
FILESYSTEM_ENTRY_FIELDS = ("path", "access", "purpose")
NETWORK_DIRECTIONS = ("outbound", "inbound")
FILESYSTEM_ACCESS = ("read", "read-write")
PURPOSE_MAX_CODE_POINTS = 200
PATH_MAX_UTF8_BYTES = 4096

# --------------------------------------------------------------------------- #
# Reserved names ([CXC-PC-RESERVED-NAMES]); each is refused like an unknown key.
# --------------------------------------------------------------------------- #
HA_RESERVED_KEYS = (
    "dependencies", "after_dependencies", "iot_class", "integration_type",
    "categories", "quality_scale", "loggers", "import_executor",
    "preview_features", "disabled", "arch", "stage", "url", "image", "ports",
    "watchdog", "map", "devices", "privileged",
)
HA_OTHER_NAMES_RESERVED = (
    "config_flow", "single_config_entry", "mqtt", "zeroconf", "ssdp", "homekit",
    "bluetooth", "dhcp", "usb", "lorawan", "supported_by", "iot_standards",
)
#: other-reserved items that are KEYS at the top level
OTHER_RESERVED_KEYS = ("upgrade",)
#: other-reserved items that are KEYS inside ``bus`` (the selector-scoped form)
BUS_RESERVED_KEYS = ("read", "publish")
#: other-reserved VALUES of ``run.form``
RUN_FORMS_RESERVED = ("process", "container")
RUN_FORM = "python"

# --------------------------------------------------------------------------- #
# Bus types ([CXC-PC-RULE-REAL-TYPES], [CXC-PC-BUS-BLOCK] type-vocabulary).
# --------------------------------------------------------------------------- #
BUS_TYPES_ALLOWED = ("STATE", "EVENT")
#: the eight composebus-v0.1.0 MessageType members reserved to kernel/host traffic
BUS_TYPES_RESERVED = ("CONFIG_PUSH", "CONFIG_REQUEST", "HEARTBEAT", "DISCOVERY",
                      "TIME_SYNC", "BINDING", "ACK", "LOG_ENTRY")


def message_type_names() -> "tuple[str, ...]":
    """The member names of the INSTALLED composebus ``MessageType``."""
    return tuple(m.name for m in MessageType)


def check_bus_type_vocabulary() -> None:
    """Fail unless the installed ``MessageType`` is exactly STATE, EVENT and the
    eight reserved names ([CXC-PC-RULE-REAL-TYPES] verify-machine: a renamed
    member fails the check)."""
    installed = set(message_type_names())
    expected = set(BUS_TYPES_ALLOWED) | set(BUS_TYPES_RESERVED)
    if installed != expected:
        raise RuntimeError(
            "composebus MessageType does not match contract 0.1's bus type "
            f"vocabulary: missing from composebus {sorted(expected - installed)}, "
            f"not known to the contract {sorted(installed - expected)}")


# --------------------------------------------------------------------------- #
# Value rules.
# --------------------------------------------------------------------------- #
# ONE dialect-neutral pattern per string rule, DEFINED ONCE: the validator
# searches with these exact strings (``re.search``) and the shipped JSON Schema
# is BUILT from them (:func:`build_schema`), so the two cannot drift
# ([CXC-PC-MANIFEST-FIELDS] qa-wave1-rulings S1). They are written in the
# subset where ECMA-262 (with the ``u`` flag, as Draft 2020-12 validators use)
# and Python ``re`` agree: ``^`` + ``(?![\s\S])`` instead of ``$`` (Python's
# ``$`` also matches before a trailing newline), ``[0-9]`` instead of ``\d``
# (Unicode digits in Python), explicit classes instead of ``\s``/``\S``/``\w``
# (their sets differ between dialects), and ``[\s\S]`` instead of ``.``.
_END = r"(?![\s\S])"


def _cls(ranges: "tuple[tuple[int, int], ...]") -> str:
    r"""The inside of a character class for code point ranges. BMP points are
    written ``\uXXXX``; astral points as literal characters (``\u{...}`` is
    ECMA-only and ``\U...`` Python-only)."""
    def one(c: int) -> str:
        return f"\\u{c:04x}" if c <= 0xFFFF else chr(c)
    return "".join(one(a) if a == b else f"{one(a)}-{one(b)}" for a, b in ranges)


#: Python ``str.isspace()`` code points (frozen; Unicode 15.1)
WHITESPACE_RANGES = ((0x9, 0xd), (0x1c, 0x20), (0x85, 0x85), (0xa0, 0xa0),
                     (0x1680, 0x1680), (0x2000, 0x200a), (0x2028, 0x2029),
                     (0x202f, 0x202f), (0x205f, 0x205f), (0x3000, 0x3000))
#: Unicode general category Cf (format) code points (frozen; Unicode 15.1)
FORMAT_RANGES = ((0xad, 0xad), (0x600, 0x605), (0x61c, 0x61c), (0x6dd, 0x6dd),
                 (0x70f, 0x70f), (0x890, 0x891), (0x8e2, 0x8e2), (0x180e, 0x180e),
                 (0x200b, 0x200f), (0x202a, 0x202e), (0x2060, 0x2064), (0x2066, 0x206f),
                 (0xfeff, 0xfeff), (0xfff9, 0xfffb), (0x110bd, 0x110bd), (0x110cd, 0x110cd),
                 (0x13430, 0x1343f), (0x1bca0, 0x1bca3), (0x1d173, 0x1d17a),
                 (0xe0001, 0xe0001), (0xe0020, 0xe007f))
#: every line and paragraph separator (``str.splitlines`` boundaries)
LINE_BREAK_RANGES = ((0xa, 0xd), (0x1c, 0x1e), (0x85, 0x85), (0x2028, 0x2029))
UNICODE_VERSION_FROZEN = "15.1.0"

_WS = _cls(WHITESPACE_RANGES)
_BLANK = _WS + _cls(FORMAT_RANGES)          # "empty" = only whitespace and Cf
_LB = _cls(LINE_BREAK_RANGES)
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_SEMVER = (r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
           r"(?:-(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*)"
           r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[a-zA-Z-][0-9a-zA-Z-]*))*)?"
           r"(?:\+[0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*)?")
_PIN_NAME = r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?"
# permissions.network[].target ([CXC-PC-PERMISSIONS] network-entry). A host name
# is lowercase LDH labels of 1 to 63, no '-' at either end, single '.', at most
# 253 characters (the lookahead), no trailing '.', and (architect ruling for
# r22, RFC 3696 section 2) a FINAL label that is not all-numeric: so a target of
# digits and dots is accepted only as a canonical dotted-quad IPv4 (010.0.0.1,
# 256.1.1.1 and printer.123 are refused). The bracketed IPv6 form is
# matched LOOSELY here (lowercase hex and ':'); RFC 5952 canonicality needs a
# parse, so the validator alone checks it (SCHEMA_INEXPRESSIBLE).
_LDH_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = (rf"(?=[a-z0-9.-]{{1,253}}(?::|{_END}))(?:{_LDH_LABEL}\.)*"
             rf"(?=[0-9]*[a-z-]){_LDH_LABEL}")
_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])"
_IPV4 = rf"{_OCTET}(?:\.{_OCTET}){{3}}"
_IPV6_BRACKETED_LOOSE = r"\[[0-9a-f:]{2,39}\]"
_PORT = (r"(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}"
         r"|655[0-2][0-9]|6553[0-5])")
#: Unicode general category Cc (control): C0, DEL and C1
CONTROL_RANGES = ((0x0, 0x1f), (0x7f, 0x9f))
_CC = _cls(CONTROL_RANGES)
_FS_SEGMENT = rf"(?!\.\.?(?:/|{_END}))[^/~*?\[{_CC}]+"
_EULA_SEG = rf"(?!\.\.?(?:/|{_END}))[^/\\{_CC}]+"
_PIN_VERSION = r"[A-Za-z0-9][A-Za-z0-9.+!_-]*"
# PEP 503 spellings of the host-provided names (case-insensitive by hand)
_HOST_PROVIDED_LOOKAHEAD = (
    r"(?!(?:[Cc][Oo][Mm][Pp][Oo][Ss][Ee][Bb][Uu][Ss]"
    r"|[Cc][Oo][Mm][Pp][Oo][Ss][Ee][-_.]+[Pp][Ll][Uu][Gg][Ii][Nn][Ss]"
    r"|[Cc][Oo][Mm][Pp][Oo][Ss][Ee][Ii][Oo])==)")

PATTERNS = {
    "domain": rf"^[a-z][a-z0-9_]*{_END}",
    # host-name-rule (r10): never the built-in grant prefixes bus / code
    "host_name": rf"^(?!(?:bus|code|permission){_END})[a-z][a-z0-9_]*{_END}",
    "name": rf"[^{_BLANK}]",
    "version": rf"^{_SEMVER}{_END}",
    "description": rf"^[^{_LB}]*[^{_BLANK}][^{_LB}]*{_END}",
    "documentation": rf"^https://[^{_WS}/?#]+[^{_WS}]*{_END}",
    "issue_tracker": rf"^[Hh][Tt][Tt][Pp][Ss]?://[^{_WS}/?#]+[^{_WS}]*{_END}",
    "codeowner": rf"^@[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?{_END}",
    "pin": rf"^{_PIN_NAME}=={_PIN_VERSION}{_END}",
    "requirement": rf"^{_HOST_PROVIDED_LOOKAHEAD}{_PIN_NAME}=={_PIN_VERSION}{_END}",
    "entry": rf"^{_IDENT}(?:\.{_IDENT})*:{_IDENT}(?:\.{_IDENT})*{_END}",
    # exact topics: single-/ separated segments of [A-Za-z0-9_-]+, no wildcards
    # (qa-wave1-rulings G3); the domain segment is checked by the validator
    "namespace": rf"^plugins/[a-z][a-z0-9_]*(?:/[A-Za-z0-9_-]+)+{_END}",
    # [CXC-PC-PERMISSIONS] (r21): `any` stands alone; otherwise a host name,
    # an IPv4 address or a bracketed IPv6 address, then an optional port
    "permission_target": (rf"^(?:any|(?!any:)(?:{_HOSTNAME}|{_IPV4}|{_IPV6_BRACKETED_LOOSE})"
                          rf"(?::{_PORT})?){_END}"),
    # an absolute POSIX path; no empty, '.' or '..' segment; no trailing '/'
    # except '/' itself; no '~', '*', '?', '[' or control character
    "permission_path": rf"^/(?:{_FS_SEGMENT}(?:/{_FS_SEGMENT})*)?{_END}",
    # non-empty and one line, exactly as `description` (readings (12), N3)
    "permission_purpose": rf"^[^{_LB}]*[^{_BLANK}][^{_LB}]*{_END}",
    # [CXC-PC-V02-EULA-FIELD] path-rule: single-'/' separated segments, none
    # empty, '.' or '..'; no leading '/', drive prefix, backslash, control
    # character or trailing '/' (the 255-byte limit is the validator's alone)
    "eula": rf"^(?![A-Za-z]:)(?:{_EULA_SEG}/)*{_EULA_SEG}{_END}",
    "standard_questions": (r"^(?![/\\])(?![A-Za-z]:)"
                           r"(?!(?:[\s\S]*[/\\])?\.\.(?:[/\\]|(?![\s\S])))[\s\S]+" + _END),
}
NAMESPACE_SEGMENT_RE = re.compile(r"[A-Za-z0-9_-]+", re.ASCII)
_PIN_PARTS_RE = re.compile(rf"(?P<name>{_PIN_NAME})==(?P<version>{_PIN_VERSION})")


def matches(rule: str, value: object) -> bool:
    """True iff ``value`` is a string the named pattern accepts (``re.search``,
    which is JSON Schema's ``pattern`` semantics)."""
    return isinstance(value, str) and re.search(PATTERNS[rule], value) is not None


_LINE_BREAK_RE = re.compile(f"[{_LB}]")


def has_line_break(value: str) -> bool:
    """N3 one-line rule: True iff ``value`` holds ANY character of the frozen
    line-break set (\\n, \\r, VT, FF, FS, GS, RS, U+0085, U+2028, U+2029)."""
    return _LINE_BREAK_RE.search(value) is not None


#: the code-grant body: a PEP 503 NORMALIZED name, "==", a pin version
CODE_GRANT_RE = re.compile(rf"code:[a-z0-9]+(?:-[a-z0-9]+)*=={_PIN_VERSION}", re.ASCII)
#: the bus-grant forms, namespace grammar as in PATTERNS["namespace"]
BUS_GRANT_RE = re.compile(
    r"bus\.(?:publish|subscribe):plugins/[a-z][a-z0-9_]*(?:/[A-Za-z0-9_-]+)+:(?:STATE|EVENT)",
    re.ASCII)


RESERVED_HOST_NAMES = ("bus", "code", "permission")


def check_host_name(name: object) -> str:
    """[CXC-PC-HOST-SECTIONS] host-name-rule (r10; ``permission`` refused
    since r21, with the ``permission.`` grant forms), applied to the HOST'S OWN
    name wherever the library takes one. Raises :class:`HostNameRefused`."""
    if not matches("host_name", name):
        raise HostNameRefused(name)
    return name  # type: ignore[return-value]


def is_blank(value: str) -> bool:
    """Reading (4) with N3: no character that is neither whitespace nor Cf."""
    return re.search(PATTERNS["name"], value) is None


#: packages the HOST provides; a manifest's requirements may not name them
HOST_PROVIDED_PACKAGES = ("composebus", "compose-plugins", "composeio")


_PEP503_SEP_RE = re.compile(r"[-_.]+")


def normalize_requirement_name(name: str) -> str:
    """FULL PEP 503 normalization: lowercase, every run of ``-``, ``_`` and
    ``.`` collapsed to one ``-`` ([CXC-PC-MANIFEST-FIELDS]
    host-provided-normalization, r6). This is how pip names a distribution,
    so ``compose.plugins`` IS ``compose-plugins``."""
    return _PEP503_SEP_RE.sub("-", name).lower()


def parse_pin(pin: str) -> "tuple[str, str]":
    """``name==version`` -> (PEP 503 name, version). The pin must already
    match ``PATTERNS['pin']``."""
    m = _PIN_PARTS_RE.fullmatch(pin)
    if m is None:
        raise ValueError(f"not an exact pin: {pin!r}")
    return normalize_requirement_name(m.group("name")), m.group("version")


def _sq_path_problem(value: str) -> "str | None":
    """Why a ``standard_questions`` path is refused by SHAPE, or None. Agrees
    with ``PATTERNS['standard_questions']`` (tested)."""
    if value == "":
        return "is empty"
    if value.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", value):
        return "is absolute (it must be a relative path inside the package)"
    if ".." in re.split(r"[/\\]", value):
        return "contains '..' (it must stay inside the package)"
    return None


# --------------------------------------------------------------------------- #
# `license` ([CXC-PC-V02-LICENSE-FIELD], contract 0.2): an SPDX license
# expression, SHAPE-checked only. No id is looked up in the SPDX License List
# (a copy goes stale and would refuse a valid new id); the field is
# informational: shown at consent, never verified, never part of a grant.
# --------------------------------------------------------------------------- #
_LICENSE_ID_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.-")
#: an idstring equal to one of these is refused (NONE and NOASSERTION describe
#: an absent declaration, which is written by leaving the field out)
LICENSE_RESERVED_WORDS = ("AND", "OR", "WITH", "NONE", "NOASSERTION")


class _LicenseShape(Exception):
    pass


class _LicenseParser:
    """expression = term ((" AND " | " OR ") term)*; term = "(" expression ")"
    | simple [" WITH " idstring]; simple = idstring ["+"] | "LicenseRef-"
    idstring | "DocumentRef-" idstring ":LicenseRef-" idstring."""

    def __init__(self, text: str):
        self.t, self.i = text, 0

    def parse(self) -> None:
        self.expression()
        if self.i != len(self.t):
            raise _LicenseShape(f"unexpected text at character {self.i + 1}")

    def _eat(self, token: str) -> bool:
        if self.t.startswith(token, self.i):
            self.i += len(token)
            return True
        return False

    def expression(self) -> None:
        self.term()
        while self._eat(" AND ") or self._eat(" OR "):
            self.term()

    def term(self) -> None:
        if self._eat("("):
            self.expression()
            if not self._eat(")"):
                raise _LicenseShape("unbalanced parentheses (a '(' is not closed)")
            return
        self.simple()
        if self._eat(" WITH "):
            self.idstring("the exception id after WITH")

    def idstring(self, what: str) -> str:
        start = self.i
        while self.i < len(self.t) and self.t[self.i] in _LICENSE_ID_CHARS:
            self.i += 1
        word = self.t[start:self.i]
        if not word:
            raise _LicenseShape(f"{what} is missing at character {start + 1}")
        if word in LICENSE_RESERVED_WORDS:
            raise _LicenseShape(f"{word!r} is an operator or a reserved word, not an id")
        return word

    def simple(self) -> None:
        word = self.idstring("a license id")
        if word.startswith("DocumentRef-") and self._eat(":LicenseRef-"):
            self.idstring("the LicenseRef id after ':LicenseRef-'")
            return
        self._eat("+")


def license_problem(value: object) -> "str | None":
    """Why a ``license`` value is refused, or None ([CXC-PC-V02-LICENSE-FIELD])."""
    if not isinstance(value, str):
        return f"{value!r} must be a string (an SPDX license expression)"
    if value == "":
        return "'' is empty (leave the field out to declare no licence)"
    if len(value) > LICENSE_MAX_CHARS:
        return f"is {len(value)} characters; at most {LICENSE_MAX_CHARS} are allowed"
    if not value.isascii():
        return f"{value!r} is not ASCII"
    try:
        _LicenseParser(value).parse()
    except _LicenseShape as exc:
        return (f"{value!r} is not an SPDX license expression: {exc} (operators are "
                "AND, OR and WITH, upper-case, with one space on each side)")
    except RecursionError:
        return f"{value!r} nests parentheses too deeply"
    return None


def _describe_unknown_key(key: str, where: str) -> str:
    if where == "" and key in HA_RESERVED_KEYS:
        return ("reserved name (Home Assistant) — not in contract 0.1; "
                "a manifest carrying it is refused")
    if where == "" and key in HA_OTHER_NAMES_RESERVED:
        return ("reserved name (Home Assistant integration manifest) — not in "
                "contract 0.1; a manifest carrying it is refused")
    if where == "" and key in OTHER_RESERVED_KEYS:
        return ("reserved for v0.2 (the upgrade block) — not in contract 0.1; "
                "a manifest carrying it is refused")
    if where == "bus" and key in BUS_RESERVED_KEYS:
        return ("reserved for v0.2 (the selector-scoped bus permission form) — "
                "not in contract 0.1; refused")
    return "unknown key — contract 0.1 does not define it; refused"


# --------------------------------------------------------------------------- #
# The host-facing inputs.
# --------------------------------------------------------------------------- #
#: A host's validator for its OWN section: takes the section's JSON value and
#: returns the refusals for it, with ``field`` RELATIVE to the section ("" for
#: the section itself). The library prefixes ``hosts.<host>``.
SectionValidator = Callable[[Any], Iterable[Refusal]]


class _NoPackage:
    def __repr__(self) -> str:
        return "<no package>"


NO_PACKAGE: Any = _NoPackage()


@dataclass(frozen=True)
class Manifest:
    """A manifest that passed :func:`validate_manifest`. ``data`` is the parsed
    JSON, unchanged."""

    data: dict

    @property
    def domain(self) -> str:
        return self.data["domain"]

    @property
    def version(self) -> str:
        return self.data["version"]

    @property
    def contract(self) -> str:
        return self.data["contract"]

    @property
    def isolation_floor(self) -> str:
        return self.data.get("isolation_floor", "inprocess")

    @property
    def publishes(self) -> "list[tuple[str, str]]":
        return [(e["namespace"], e["type"])
                for e in self.data.get("bus", {}).get("publishes", [])]

    @property
    def subscribes(self) -> "list[tuple[str, str]]":
        return [(e["namespace"], e["type"])
                for e in self.data.get("bus", {}).get("subscribes", [])]

    @property
    def network_permissions(self) -> "list[tuple[str, str, str]]":
        """Declared (direction, target, purpose); [] when none declared."""
        return [(e["direction"], e["target"], e["purpose"])
                for e in self.data.get("permissions", {}).get("network", [])]

    @property
    def filesystem_permissions(self) -> "list[tuple[str, str, str]]":
        """Declared (access, path, purpose); [] when none declared."""
        return [(e["access"], e["path"], e["purpose"])
                for e in self.data.get("permissions", {}).get("filesystem", [])]

    def host_section(self, host: str) -> Any:
        return self.data["hosts"][host]


def validate_manifest(manifest: Any, *, host: str,
                      section_validator: SectionValidator,
                      package: Any,
                      host_contract: str = HOST_CONTRACT) -> Manifest:
    """Validate a parsed manifest for installation on ``host``.

    ``section_validator`` validates ``hosts.<host>``; it is REQUIRED (each host
    owns its own section's schema). ``package`` is the
    :class:`~compose_plugins.package.PackageSource` the manifest came from, used
    to check that ``standard_questions`` exists inside it. Pass
    ``NO_PACKAGE`` to validate without a package: a manifest that then carries
    ``standard_questions`` is REFUSED, because its existence cannot be checked
    (never passed unchecked).

    Raises :class:`ManifestRefused` naming every refused field.
    """
    check_host_name(host)
    if not callable(section_validator):
        raise TypeError("validate_manifest: section_validator is required "
                        "(each host validates its own section)")
    if not isinstance(manifest, dict):
        raise ManifestRefused([Refusal(
            "(manifest)", f"must be a JSON object, got {type(manifest).__name__}")])

    # 1. The contract version, FIRST and alone.
    try:
        if "contract" in manifest:
            check_contract(manifest["contract"], host_contract)
        else:
            check_contract(host_contract=host_contract)
    except ContractVersionRefused as exc:
        raise ManifestRefused([Refusal("contract", str(exc).removeprefix("contract: "))]) from exc

    out: list[Refusal] = []

    def refuse(field: str, message: str) -> None:
        out.append(Refusal(field, message))

    # 1b. [CXC-PC-V02-VERSION-0-2] rule (3): a manifest is read by the contract
    # it STATES, so a 0.2 key in a manifest that states an earlier contract is
    # refused naming the key (its value is then not checked).
    stated = manifest.get("contract")
    from_0_2 = parse_contract(stated) >= (0, 2) if stated is not None else False
    for key in FIELDS_FROM_0_2:
        if key in manifest and not from_0_2:
            refuse(key, f"defined from contract 0.2; this manifest states contract "
                        f"{stated!r}, which does not define it (a manifest is read by "
                        "the contract it states)")

    # 2. Unknown / reserved top-level keys.
    for key in manifest:
        if key not in CORE_FIELDS:
            refuse(key, _describe_unknown_key(key, ""))

    # 3. Missing required fields.
    for key in REQUIRED_FIELDS:
        if key not in manifest:
            refuse(key, "required field missing")
    if "config_schema" not in manifest:
        refuse("config_schema", "missing — the manifest is not built "
               "(run `compose-plugin build`; config_schema is always present "
               "in a built manifest)")

    # 4. Field values.
    domain_ok = False
    if "domain" in manifest:
        v = manifest["domain"]
        if not matches("domain", v):
            refuse("domain", f"{v!r} must be a string matching ^[a-z][a-z0-9_]*$")
        else:
            domain_ok = True

    if "name" in manifest:
        v = manifest["name"]
        if not isinstance(v, str) or is_blank(v):
            refuse("name", f"{v!r} must be a non-empty string (at least one character "
                   "that is neither whitespace nor a Unicode format character)")

    if "version" in manifest:
        v = manifest["version"]
        if not matches("version", v):
            refuse("version", f"{v!r} is not a SemVer version (contract 0.1 "
                   "accepts SemVer only, for example 1.0.0)")

    if "description" in manifest:
        v = manifest["description"]
        if not isinstance(v, str) or is_blank(v):
            refuse("description", f"{v!r} must be a non-empty string (at least one "
                   "character that is neither whitespace nor a Unicode format character)")
        elif not matches("description", v):
            refuse("description", f"{v!r} must be ONE line (it contains a line or "
                   "paragraph separator)")

    if "documentation" in manifest:
        v = manifest["documentation"]
        if not matches("documentation", v):
            refuse("documentation", f"{v!r} must be an https:// URL "
                   "(contract 0.1 accepts https only)")

    if "issue_tracker" in manifest:
        v = manifest["issue_tracker"]
        if not matches("issue_tracker", v):
            refuse("issue_tracker", f"{v!r} must be a URL (http:// or https://)")

    if "codeowners" in manifest:
        v = manifest["codeowners"]
        if not isinstance(v, list):
            refuse("codeowners", f"{v!r} must be a list of '@user' or '@org/team' strings")
        else:
            for i, item in enumerate(v):
                if not matches("codeowner", item):
                    refuse(f"codeowners[{i}]", f"{item!r} is not '@user' or '@org/team' "
                           "(must match ^@[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)?$)")

    if "requirements" in manifest:
        v = manifest["requirements"]
        if not isinstance(v, list):
            refuse("requirements", f"{v!r} must be a list of exact pins "
                   "('name==version'; may be [])")
        else:
            for i, item in enumerate(v):
                if not matches("pin", item):
                    refuse(f"requirements[{i}]", f"{item!r} is not an exact pin "
                           "'name==version' (contract 0.1 accepts == only)")
                    continue
                raw_name = item.split("==", 1)[0]
                norm, _ = parse_pin(item)
                if norm in HOST_PROVIDED_PACKAGES:
                    refuse(f"requirements[{i}]", f"{item!r} names {raw_name!r} "
                           f"(PEP 503: {norm!r}), "
                           f"a package the HOST provides ({', '.join(HOST_PROVIDED_PACKAGES)}); "
                           "a plug-in may not require it")

    if "run" in manifest:
        v = manifest["run"]
        if not isinstance(v, dict):
            refuse("run", f"{v!r} must be an object {{form, entry}}")
        else:
            for key in v:
                if key not in RUN_FIELDS:
                    refuse(f"run.{key}", _describe_unknown_key(key, "run"))
            for key in RUN_FIELDS:
                if key not in v:
                    refuse(f"run.{key}", "required field missing")
            if "form" in v:
                form = v["form"]
                if form in RUN_FORMS_RESERVED:
                    refuse("run.form", f"{form!r} is reserved for v0.2; only "
                           f"'python' is valid in contract 0.1")
                elif form != RUN_FORM:
                    refuse("run.form", f"{form!r} is not valid; only 'python' "
                           "is valid in contract 0.1")
            if "entry" in v:
                entry = v["entry"]
                if not matches("entry", entry):
                    refuse("run.entry", f"{entry!r} is not of the form 'module:object' "
                           "(a Python entry-point object reference, for example "
                           "'hello_world.plugin:HelloWorld')")

    if "isolation_floor" in manifest:
        v = manifest["isolation_floor"]
        if v not in ISOLATION_LEVELS:
            refuse("isolation_floor", f"{v!r} must be one of {list(ISOLATION_LEVELS)}")

    if "config_schema" in manifest:
        v = manifest["config_schema"]
        if not isinstance(v, dict):
            refuse("config_schema", f"must be a JSON Schema object, got "
                   f"{type(v).__name__}")

    if "bus" in manifest:
        _validate_bus(manifest["bus"], manifest.get("domain") if domain_ok else None,
                      refuse)

    if "standard_questions" in manifest:
        v = manifest["standard_questions"]
        if not isinstance(v, str):
            refuse("standard_questions", f"{v!r} must be a string path")
        else:
            problem = _sq_path_problem(v)
            if problem is not None:
                refuse("standard_questions", f"{v!r} {problem}")
            elif package is NO_PACKAGE:
                refuse("standard_questions", f"{v!r} cannot be checked without "
                       "the package (validate with the PackageSource it came from)")
            elif not package.has_directory(v):
                refuse("standard_questions", f"{v!r} does not exist as a directory "
                       "inside the package")

    if "permissions" in manifest:
        _validate_permissions(manifest["permissions"], refuse)

    if "license" in manifest and from_0_2:
        problem = license_problem(manifest["license"])
        if problem is not None:
            refuse("license", problem)

    if "eula" in manifest and from_0_2:
        v = manifest["eula"]
        problem = eula_path_problem(v)
        if problem is not None:
            refuse("eula", problem)
        elif package is NO_PACKAGE:
            refuse("eula", f"{v!r} cannot be checked without the package (validate with "
                   "the PackageSource it came from)")
        else:
            try:
                read_eula(package, v)
            except ManifestRefused as exc:
                out.extend(exc.refusals)

    if "hosts" in manifest:
        v = manifest["hosts"]
        if not isinstance(v, dict):
            refuse("hosts", f"{v!r} must be an object with at least one host key")
        elif not v:
            refuse("hosts", "must have at least one host key (it is empty)")
        elif any(not matches("host_name", k) for k in v):
            for k in v:
                if not matches("host_name", k):
                    refuse(f"hosts.{k}", f"host name {k!r} is refused: a host name "
                           "matches ^[a-z][a-z0-9_]*$ and is never 'bus', 'code' or "
                           "'permission' (the prefixes of the built-in grant forms)")
        elif host not in v:
            refuse("hosts", f"not supported on host {host}")
        else:
            for r in section_validator(v[host]):
                field = f"hosts.{host}" + (f".{r.field}" if r.field else "")
                refuse(field, r.message)
            # every other host's section, and unknown host keys: IGNORED

    if out:
        raise ManifestRefused(out)
    return Manifest(data=manifest)


def _is_exact_topic(ns: str) -> bool:
    """[CXC-PC-MANIFEST-FIELDS] qa-wave1-rulings G3: the namespace grammar, and
    composebus's own ``validate_pattern`` must accept it too."""
    if not matches("namespace", ns):
        return False
    if not all(NAMESPACE_SEGMENT_RE.fullmatch(seg) for seg in ns.split("/")):
        return False
    try:
        validate_pattern(ns)
    except ValueError:
        return False
    return True


def _validate_bus(bus: Any, domain: "str | None", refuse) -> None:
    if not isinstance(bus, dict):
        refuse("bus", f"{bus!r} must be an object {{publishes, subscribes}}")
        return
    for key in bus:
        if key not in BUS_FIELDS:
            refuse(f"bus.{key}", _describe_unknown_key(key, "bus"))
    installed_types = message_type_names()
    for list_key in BUS_FIELDS:
        if list_key not in bus:
            refuse(f"bus.{list_key}", "required field missing (use [] for none)")
            continue
        entries = bus[list_key]
        if not isinstance(entries, list):
            refuse(f"bus.{list_key}", f"{entries!r} must be a list of {{namespace, type}}")
            continue
        seen: set = set()
        for i, entry in enumerate(entries):
            path = f"bus.{list_key}[{i}]"
            if not isinstance(entry, dict):
                refuse(path, f"{entry!r} must be an object {{namespace, type}}")
                continue
            for key in entry:
                if key not in BUS_ENTRY_FIELDS:
                    refuse(f"{path}.{key}", _describe_unknown_key(key, "bus-entry"))
            ns, typ = entry.get("namespace"), entry.get("type")
            ok = True
            if "namespace" not in entry:
                refuse(f"{path}.namespace", "required field missing")
                ok = False
            elif not isinstance(ns, str):
                refuse(f"{path}.namespace", f"{ns!r} must be a string")
                ok = False
            elif not _is_exact_topic(ns):
                refuse(f"{path}.namespace", f"{ns!r} is not an exact topic: contract 0.1 "
                       "namespaces are plugins/<domain>/<segment>[/<segment>...], each "
                       "segment [A-Za-z0-9_-]+, single '/' separators, no wildcards "
                       "(*, #, +), no '.' or '..' segments")
                ok = False
            elif domain is not None and ns.split("/")[1] != domain:
                refuse(f"{path}.namespace", f"{ns!r} is outside this plug-in's "
                       f"own namespace plugins/{domain}/ (contract 0.1 allows only "
                       f"namespaces under plugins/<domain>/)")
                ok = False
            if "type" not in entry:
                refuse(f"{path}.type", "required field missing")
                ok = False
            elif typ in BUS_TYPES_ALLOWED:
                pass
            elif isinstance(typ, str) and typ in installed_types:
                refuse(f"{path}.type", f"{typ!r} is a composebus MessageType reserved "
                       "to kernel/host traffic; a plug-in may declare STATE or EVENT only")
                ok = False
            else:
                refuse(f"{path}.type", f"{typ!r} is not a composebus MessageType "
                       f"member (valid names: {', '.join(installed_types)}; a "
                       "plug-in may declare STATE or EVENT only)")
                ok = False
            if ok:
                pair = (ns, typ)
                if pair in seen:
                    refuse(path, f"duplicate {{namespace: {ns!r}, type: {typ!r}}} "
                           f"within bus.{list_key}")
                seen.add(pair)


def ipv6_rfc5952(text: str) -> "str | None":
    """The RFC 5952 section 4 canonical text of the IPv6 address ``text``, or
    None if ``text`` is not an IPv6 address. Computed HERE rather than taken
    from ``str(ipaddress.IPv6Address)``, whose output for some addresses
    differs between Python versions: leading zeros dropped, lowercase hex,
    the LONGEST run of two or more zero groups (the first on a tie) shortened
    to ``::``, a single zero group never shortened."""
    import ipaddress
    try:
        addr = ipaddress.IPv6Address(text)
    except ValueError:
        return None
    if "%" in text:
        return None                      # a scope id is not an address
    n = int(addr)
    groups = [(n >> (16 * (7 - i))) & 0xFFFF for i in range(8)]
    best_at, best_len, i = -1, 0, 0
    while i < 8:
        if groups[i] == 0:
            j = i
            while j < 8 and groups[j] == 0:
                j += 1
            if j - i > best_len:
                best_at, best_len = i, j - i
            i = j
        else:
            i += 1
    hexes = [f"{g:x}" for g in groups]
    if best_len < 2:
        return ":".join(hexes)
    return (":".join(hexes[:best_at]) + "::" + ":".join(hexes[best_at + best_len:]))


def permission_target_problem(target: object) -> "str | None":
    """Why ``target`` is not a valid ``permissions.network[].target``, or None
    ([CXC-PC-PERMISSIONS] network-entry). The pattern first (shared with the
    schema), then the one rule the schema cannot carry: a bracketed IPv6
    address must be RFC 5952 canonical, because grant strings are compared
    exactly for widening."""
    if not matches("permission_target", target):
        return (f"{target!r} is not a valid target: exactly 'any' alone, or a "
                "lowercase host name (LDH labels of 1 to 63 characters, at most 253 "
                "characters, no wildcard, no trailing '.', a final label that is not "
                "all-numeric), an IPv4 address (octets 0 to 255, no leading zeros), or a bracketed RFC 5952 canonical IPv6 address, each "
                "optionally followed by ':<port>' (1 to 65535, no leading zeros)")
    assert isinstance(target, str)
    if target.startswith("["):
        inner = target[1:target.index("]")]
        canon = ipv6_rfc5952(inner)
        if canon is None:
            return f"{target!r} is not a valid target: {inner!r} is not an IPv6 address"
        if canon != inner:
            return (f"{target!r} is not a valid target: the IPv6 address must be in "
                    f"RFC 5952 canonical form, [{canon}]")
    return None


def permission_path_problem(path: object) -> "str | None":
    """Why ``path`` is not a valid ``permissions.filesystem[].path``, or None
    ([CXC-PC-PERMISSIONS] filesystem-entry). The pattern first (shared with the
    schema), then the 4096-BYTE UTF-8 limit, which a JSON Schema cannot carry
    (``maxLength`` counts code points). The path is compared exactly; it is
    never resolved and never looked at on disk."""
    if not matches("permission_path", path):
        return (f"{path!r} is not a valid path: an absolute POSIX path starting "
                "with '/', with no empty, '.' or '..' segment, no trailing '/' "
                "(except '/' itself), and no '~', wildcard ('*', '?', '[') or "
                "control character")
    assert isinstance(path, str)
    try:
        size = len(path.encode("utf-8"))
    except UnicodeEncodeError:
        return f"{path!r} is not a valid path: it is not encodable as UTF-8"
    if size > PATH_MAX_UTF8_BYTES:
        return (f"path of {size} bytes is not a valid path: at most "
                f"{PATH_MAX_UTF8_BYTES} bytes in UTF-8")
    return None


def _purpose_problem(purpose: object) -> "str | None":
    if not isinstance(purpose, str) or is_blank(purpose):
        return (f"{purpose!r} must be a non-empty string (at least one character "
                "that is neither whitespace nor a Unicode format character)")
    if not matches("permission_purpose", purpose):
        return f"{purpose!r} must be ONE line (it contains a line or paragraph separator)"
    if len(purpose) > PURPOSE_MAX_CODE_POINTS:
        return (f"purpose of {len(purpose)} characters is too long: at most "
                f"{PURPOSE_MAX_CODE_POINTS}")
    return None


def _validate_permissions(perms: Any, refuse) -> None:
    """[CXC-PC-PERMISSIONS] shape, network-entry, filesystem-entry,
    purpose-rule, duplicates. Checks the DECLARATION only (never-verified)."""
    if not isinstance(perms, dict):
        refuse("permissions", f"{perms!r} must be an object {{network, filesystem}}")
        return
    for key in perms:
        if key not in PERMISSIONS_FIELDS:
            refuse(f"permissions.{key}", "unknown key — contract 0.1 permissions "
                   "declare only 'network' and 'filesystem'; refused")
    specs = (("network", NETWORK_ENTRY_FIELDS), ("filesystem", FILESYSTEM_ENTRY_FIELDS))
    for list_key, entry_fields in specs:
        if list_key not in perms:
            continue                     # absent means none declared
        entries = perms[list_key]
        if not isinstance(entries, list):
            refuse(f"permissions.{list_key}", f"{entries!r} must be a list "
                   f"of {{{', '.join(entry_fields)}}} (may be [])")
            continue
        seen: set = set()
        for i, entry in enumerate(entries):
            path = f"permissions.{list_key}[{i}]"
            if not isinstance(entry, dict):
                refuse(path, f"{entry!r} must be an object {{{', '.join(entry_fields)}}}")
                continue
            for key in entry:
                if key not in entry_fields:
                    refuse(f"{path}.{key}", "unknown key — contract 0.1 does not "
                           "define it; refused")
            ok = True
            for key in entry_fields:
                if key not in entry:
                    refuse(f"{path}.{key}", "required field missing")
                    ok = False
            if list_key == "network":
                if "direction" in entry and entry["direction"] not in NETWORK_DIRECTIONS:
                    refuse(f"{path}.direction", f"{entry['direction']!r} must be one "
                           f"of {list(NETWORK_DIRECTIONS)}")
                    ok = False
                if "target" in entry:
                    problem = permission_target_problem(entry["target"])
                    if problem is not None:
                        refuse(f"{path}.target", problem)
                        ok = False
                key_of = ("direction", "target")
            else:
                if "access" in entry and entry["access"] not in FILESYSTEM_ACCESS:
                    refuse(f"{path}.access", f"{entry['access']!r} must be one "
                           f"of {list(FILESYSTEM_ACCESS)}")
                    ok = False
                if "path" in entry:
                    problem = permission_path_problem(entry["path"])
                    if problem is not None:
                        refuse(f"{path}.path", problem)
                        ok = False
                key_of = ("path",)       # whatever its access
            if "purpose" in entry:
                problem = _purpose_problem(entry["purpose"])
                if problem is not None:
                    refuse(f"{path}.purpose", problem)
            if ok:
                ident = tuple(entry[k] for k in key_of)
                if ident in seen:
                    what = ", ".join(f"{k}: {entry[k]!r}" for k in key_of)
                    refuse(path, f"duplicate entry {{{what}}} within permissions.{list_key}")
                seen.add(ident)


# --------------------------------------------------------------------------- #
# The shipped JSON Schema, BUILT from the same constants as the validator.
# --------------------------------------------------------------------------- #
#: one shipped schema per contract, chosen by a manifest's OWN ``contract``
#: (a manifest is read by the contract it states). The 0.1 file is
#: byte-identical to compose-plugins 0.1.0's.
SCHEMA_RESOURCES = {"0.1": "manifest-0.1.schema.json", "0.2": "manifest-0.2.schema.json"}
#: the schema of the contract this library implements
SCHEMA_RESOURCE = SCHEMA_RESOURCES[HOST_CONTRACT]
#: the fields each shipped schema carries beyond contract 0.1's
_SCHEMA_FIELDS_FROM = {"0.1": (), "0.2": FIELDS_FROM_0_2}

#: the rules a JSON Schema cannot express; the validator alone enforces them
SCHEMA_INEXPRESSIBLE = (
    "bus namespaces under plugins/<THIS manifest's domain>/ (needs the domain value)",
    "the installing host's own key in hosts, and that host's validation of its "
    "own section (needs the installing host)",
    "standard_questions naming a directory that exists inside the package, "
    "case-exactly and without leaving it through a symlink (needs the package)",
    "which refusal text a refused key gets (reserved vs unknown)",
    "reader rules applied before JSON exists: UTF-8, no duplicate keys, finite "
    "numbers only, at most 256 KiB, nesting at most 64 levels, manifest.json "
    "not a symlink",
    "a bracketed IPv6 permissions.network target in RFC 5952 canonical form "
    "(needs an address parse; the schema checks lowercase hex and ':' only)",
    "a permissions.filesystem path of at most 4096 bytes in UTF-8 (maxLength "
    "counts code points)",
    "no duplicate permissions entry (the same direction and target within "
    "network; the same path, whatever its access, within filesystem)",
    # ---- (0.2) the three rules below are in the 0.2 schema only ----
    "license and eula are defined from contract 0.2: a manifest that states an "
    "earlier contract and carries either is refused naming the key (a cross-field "
    "rule)",
    "license holding an SPDX license expression of ASCII characters (the "
    "expression grammar is a function, not one pattern; ids are not looked up in "
    "the SPDX License List)",
    "eula naming, in at most 255 bytes of UTF-8, a regular file inside the package, "
    "not a symlink and not reached through one, holding 1 to 262144 bytes of UTF-8 "
    "text with no byte-order mark, no control character other than tab, line feed "
    "and carriage return, no bidirectional control character, and not only "
    "whitespace (needs the package)",
)


#: how many SCHEMA_INEXPRESSIBLE entries each contract's schema lists
_INEXPRESSIBLE_COUNT = {"0.1": 8, "0.2": len(SCHEMA_INEXPRESSIBLE)}


def build_schema(contract: str = HOST_CONTRACT) -> dict:
    """The manifest JSON Schema of ``contract`` ("0.1" or "0.2"), from
    :data:`PATTERNS` and the field tuples. The 0.1 schema is exactly the one
    compose-plugins 0.1.0 shipped."""
    if contract not in SCHEMA_RESOURCES:
        raise ValueError(f"no shipped schema for contract {contract!r} "
                         f"(shipped: {sorted(SCHEMA_RESOURCES)})")
    inexpressible = SCHEMA_INEXPRESSIBLE[:_INEXPRESSIBLE_COUNT[contract]]
    extra_fields = _SCHEMA_FIELDS_FROM[contract]

    def pat(rule: str, **extra) -> dict:
        return {"type": "string", "pattern": PATTERNS[rule], **extra}

    bus_list = {
        "type": "array", "uniqueItems": True,
        "items": {"type": "object", "additionalProperties": False,
                  "required": list(BUS_ENTRY_FIELDS),
                  "properties": {"namespace": pat("namespace"),
                                 "type": {"enum": list(BUS_TYPES_ALLOWED)}}},
    }
    purpose = pat("permission_purpose", maxLength=PURPOSE_MAX_CODE_POINTS)
    network_entry = {"type": "object", "additionalProperties": False,
                     "required": list(NETWORK_ENTRY_FIELDS),
                     "properties": {"direction": {"enum": list(NETWORK_DIRECTIONS)},
                                    "target": pat("permission_target"),
                                    "purpose": purpose}}
    filesystem_entry = {"type": "object", "additionalProperties": False,
                        "required": list(FILESYSTEM_ENTRY_FIELDS),
                        "properties": {"path": pat("permission_path"),
                                       "access": {"enum": list(FILESYSTEM_ACCESS)},
                                       "purpose": purpose}}
    major, minor = (int(x) for x in contract.split("."))
    out = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"compose-plugins manifest.json, contract {contract}",
        "description": (
            "The static, host-neutral plug-in manifest of [SPEC:CXC-PLUGIN-CONTRACT] "
            f"v{contract}. GENERATED from compose_plugins.manifest (build_schema); "
            "the validator there is AUTHORITATIVE and uses the same patterns. Patterns "
            "are written to mean the same under ECMA-262 (with the u flag) and Python "
            f"re; whitespace and Unicode format (Cf) sets are frozen at Unicode "
            f"{UNICODE_VERSION_FROZEN}. Rules this schema cannot express: "
            + "; ".join(f"({i}) {r}" for i, r in enumerate(inexpressible, 1)) + "."),
        "x-compose-plugins-contract": contract,
        "type": "object",
        "additionalProperties": False,
        "required": list(REQUIRED_FIELDS + GENERATED_FIELDS),
        "properties": {
            "contract": {"type": "string",
                         "enum": [f"{major}.{m}" for m in range(minor + 1)]},
            "domain": pat("domain"),
            "name": pat("name"),
            "version": pat("version"),
            "description": pat("description"),
            "documentation": pat("documentation"),
            "issue_tracker": pat("issue_tracker"),
            "codeowners": {"type": "array", "items": pat("codeowner")},
            "requirements": {"type": "array", "items": pat("requirement")},
            "run": {"type": "object", "additionalProperties": False,
                    "required": list(RUN_FIELDS),
                    "properties": {"form": {"const": RUN_FORM},
                                   "entry": pat("entry")}},
            "isolation_floor": {"enum": list(ISOLATION_LEVELS)},
            "config_schema": {"type": "object"},
            "bus": {"type": "object", "additionalProperties": False,
                    "required": list(BUS_FIELDS),
                    "properties": {"publishes": {"$ref": "#/$defs/busList"},
                                   "subscribes": {"$ref": "#/$defs/busList"}}},
            "standard_questions": pat("standard_questions"),
            "permissions": {"type": "object", "additionalProperties": False,
                            "properties": {
                                "network": {"type": "array",
                                            "items": {"$ref": "#/$defs/networkPermission"}},
                                "filesystem": {"type": "array",
                                               "items": {"$ref": "#/$defs/filesystemPermission"}}}},
            "hosts": {"type": "object", "minProperties": 1,
                      "propertyNames": {"pattern": PATTERNS["host_name"]}},
        },
        "$defs": {"busList": bus_list, "networkPermission": network_entry,
                  "filesystemPermission": filesystem_entry},
    }
    new = {"license": {"type": "string", "minLength": 1, "maxLength": LICENSE_MAX_CHARS},
           "eula": pat("eula")}
    if extra_fields:
        # after standard_questions, as the 0.2 schema has always placed them
        props = {}
        for k, v in out["properties"].items():
            props[k] = v
            if k == "standard_questions":
                props.update({f: new[f] for f in extra_fields})
        out["properties"] = props
    return out


def render_schema_file(contract: str = HOST_CONTRACT) -> str:
    """The exact text of the shipped schema file of ``contract``."""
    return json.dumps(build_schema(contract), indent=2, ensure_ascii=True) + "\n"


def schema(contract: str = HOST_CONTRACT) -> dict:
    """The shipped manifest JSON Schema of ``contract`` ([CXC-PC-MANIFEST-FILE]);
    by default the one of the contract this library implements."""
    if contract not in SCHEMA_RESOURCES:
        raise ValueError(f"no shipped schema for contract {contract!r} "
                         f"(shipped: {sorted(SCHEMA_RESOURCES)})")
    text = resources.files("compose_plugins.schema").joinpath(SCHEMA_RESOURCES[contract]) \
        .read_text(encoding="utf-8")
    return json.loads(text)


def schema_contract(manifest: Any) -> str:
    """The shipped contract whose schema reads ``manifest``: the manifest's
    OWN ``contract`` (a manifest is read by the contract it states); a
    same-MAJOR lower MINOR with no file of its own ("0.0") reads with the
    lowest shipped schema of that MAJOR. A missing, malformed or unshipped
    contract gets this library's own schema, whose contract enum refuses it."""
    c = manifest.get("contract") if isinstance(manifest, dict) else None
    if isinstance(c, str) and c in SCHEMA_RESOURCES:
        return c
    try:
        major, minor = parse_contract(c)
    except Exception:              # noqa: BLE001 - malformed: the host's schema refuses it
        return HOST_CONTRACT
    host_major, host_minor = parse_contract(HOST_CONTRACT)
    if major == host_major and minor <= host_minor:
        same = sorted((parse_contract(k), k) for k in SCHEMA_RESOURCES
                      if parse_contract(k)[0] == major and parse_contract(k)[1] >= minor)
        if same:
            return same[0][1]
    return HOST_CONTRACT


def schema_for(manifest: Any) -> dict:
    """The shipped schema selected by the manifest's own ``contract``."""
    return schema(schema_contract(manifest))


__all__ = [
    "BUS_TYPES_ALLOWED", "BUS_TYPES_RESERVED", "CORE_FIELDS", "GENERATED_FIELDS",
    "HA_OTHER_NAMES_RESERVED", "HA_RESERVED_KEYS", "HOST_PROVIDED_PACKAGES",
    "FIELDS_FROM_0_2", "LICENSE_MAX_CHARS", "LICENSE_RESERVED_WORDS", "license_problem",
    "Manifest", "NO_PACKAGE", "OPTIONAL_FIELDS", "OTHER_RESERVED_KEYS",
    "PATTERNS", "REQUIRED_FIELDS", "SCHEMA_INEXPRESSIBLE", "SectionValidator",
    "BUS_GRANT_RE", "CODE_GRANT_RE", "RESERVED_HOST_NAMES", "build_schema",
    "SCHEMA_RESOURCES", "schema_contract", "schema_for",
    "CONTROL_RANGES", "FILESYSTEM_ACCESS", "FILESYSTEM_ENTRY_FIELDS",
    "NETWORK_DIRECTIONS", "NETWORK_ENTRY_FIELDS", "PATH_MAX_UTF8_BYTES",
    "PERMISSIONS_FIELDS", "PURPOSE_MAX_CODE_POINTS", "ipv6_rfc5952",
    "permission_path_problem", "permission_target_problem",
    "check_bus_type_vocabulary", "check_host_name",
    "has_line_break", "is_blank", "matches", "parse_pin",
    "render_schema_file",
    "message_type_names", "normalize_requirement_name", "schema",
    "validate_manifest",
]
