"""Consent: the content (from the manifest alone), the granted set, widening,
and the consent-record store ([CXC-PC-CONSENT-CONTENT], [CXC-PC-CONSENT-RECORD],
[CXC-PC-CONSENT-WIDENING]).

The library renders consent CONTENT; the HOST decides the GATE
([CXC-PC-CONSENT-GATES]). Nothing here grants consent: a record is written only
when a host passes in a decision and who made it
([CXC-PC-RULE-CONSENT-HOST-OWNED]).

ONE DERIVATION. The consent screen's permission lines and the record's
``granted`` strings come from the same walk over the manifest, so what is
shown and what is recorded cannot drift apart.

DECLARED PERMISSIONS (r21, [CXC-PC-PERMISSIONS]) are shown and recorded here
and NOWHERE checked: no host verifies, sandboxes or tests whether a plug-in's
behaviour matches them (never-verified). This module renders them and builds
their grant strings; that is all it does with them.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .errors import ConsentRefused
from .manifest import (
    BUS_GRANT_RE, CODE_GRANT_RE, FILESYSTEM_ACCESS, ISOLATION_LEVELS,
    NETWORK_DIRECTIONS, Manifest, check_host_name, has_line_break, is_blank, matches,
    parse_pin, permission_path_problem, permission_target_problem,
)

# --------------------------------------------------------------------------- #
# Granted strings ([CXC-PC-CONSENT-RECORD] granted-format).
# --------------------------------------------------------------------------- #


def bus_publish_grant(namespace: str, typ: str) -> str:
    return f"bus.publish:{namespace}:{typ}"


def bus_subscribe_grant(namespace: str, typ: str) -> str:
    return f"bus.subscribe:{namespace}:{typ}"


def code_grant(pin: str) -> str:
    """``code:<pep503-name>==<version>`` — new code to install is a permission
    ([CXC-PC-CONSENT-WIDENING] code-is-a-permission, r7), so an upgrade that
    adds or CHANGES a pin widens."""
    name, version = parse_pin(pin)
    return f"code:{name}=={version}"


NETWORK_GRANT_PREFIX = "permission.network:"
FILESYSTEM_GRANT_PREFIX = "permission.filesystem:"


def network_grant(direction: str, target: str) -> str:
    """``permission.network:<direction>:<target>`` (r21); the purpose is not
    part of the string, so rewording it alone never widens."""
    return f"{NETWORK_GRANT_PREFIX}{direction}:{target}"


def filesystem_grant(access: str, path: str) -> str:
    """``permission.filesystem:<access>:<path>`` (r21); purpose not included."""
    return f"{FILESYSTEM_GRANT_PREFIX}{access}:{path}"


def _covered(grant: str, old: "set[str]") -> bool:
    """[CXC-PC-CONSENT-WIDENING] permissions-widen: in the subset test a
    granted ``permission.filesystem:read-write:<path>`` covers
    ``permission.filesystem:read:<path>``; every other grant covers only
    itself."""
    if grant in old:
        return True
    read = f"{FILESYSTEM_GRANT_PREFIX}read:"
    if grant.startswith(read):
        return f"{FILESYSTEM_GRANT_PREFIX}read-write:{grant[len(read):]}" in old
    return False


@dataclass(frozen=True)
class HostPermission:
    """One permission line of a host's OWN section, as the host renders it.
    ``grant`` is the host-section granted string; it MUST start with
    ``<host>:`` (its vocabulary is the host spec's)."""

    text: str
    grant: str


#: A host's renderer for its OWN section: section JSON -> its permission lines.
HostSectionRenderer = Callable[[Any], Sequence[HostPermission]]


def _host_permissions(manifest: Manifest, host: str,
                      render_host_section: HostSectionRenderer) -> "list[HostPermission]":
    check_host_name(host)
    perms = list(render_host_section(manifest.host_section(host)))
    for p in perms:
        if not isinstance(p, HostPermission):
            raise ConsentRefused(f"host section renderer for {host!r} returned "
                                 f"{p!r}, not a HostPermission")
        prefix = f"{host}:"
        if not isinstance(p.grant, str) or not p.grant.startswith(prefix) \
                or len(p.grant) == len(prefix):
            raise ConsentRefused(f"host-section grant {p.grant!r} REFUSED: a "
                                 f"{host!r} grant must be '{prefix}<permission>'")
    return perms


def granted_set(manifest: Manifest, *, host: str,
                render_host_section: HostSectionRenderer) -> "list[str]":
    """The SORTED, DE-DUPLICATED ``granted`` list for ``manifest`` on ``host``."""
    grants = [code_grant(r) for r in manifest.data["requirements"]]
    grants += [bus_publish_grant(ns, t) for ns, t in manifest.publishes]
    grants += [bus_subscribe_grant(ns, t) for ns, t in manifest.subscribes]
    grants += [network_grant(d, t) for d, t, _ in manifest.network_permissions]
    grants += [filesystem_grant(a, p) for a, p, _ in manifest.filesystem_permissions]
    grants += [p.grant for p in _host_permissions(manifest, host, render_host_section)]
    return sorted(set(grants))


def widens(old_granted: Iterable[str], new_granted: Iterable[str]) -> bool:
    """An upgrade WIDENS iff the new set is NOT a subset of the old one
    ([CXC-PC-CONSENT-WIDENING] widening-definition), where a filesystem
    ``read-write`` grant covers ``read`` on the same path (permissions-widen)."""
    old = set(old_granted)
    return not all(_covered(g, old) for g in set(new_granted))


def level_drops(old_runs_as: "str | None", new_runs_as: str) -> bool:
    """True iff the host's ACTUAL isolation level would DROP (subprocess ->
    inprocess). [CXC-PC-CONSENT-WIDENING] widening-definition (r12): lowering
    ``isolation_floor`` widens exactly when the "runs as" level drops; raising
    it never widens."""
    if old_runs_as is None:
        return False
    return ISOLATION_LEVELS.index(new_runs_as) < ISOLATION_LEVELS.index(old_runs_as)


def upgrade_widens(old_granted: Iterable[str], new_granted: Iterable[str],
                   old_runs_as: "str | None", new_runs_as: str) -> bool:
    """The FULL widening rule: the granted set is not a subset of the old one,
    OR the actual "runs as" level drops."""
    return widens(old_granted, new_granted) or level_drops(old_runs_as, new_runs_as)


def added_grants(old_granted: Iterable[str], new_granted: Iterable[str]) -> "list[str]":
    """What the new set adds over the old one, sorted (the same covering
    reading as :func:`widens`, so what is marked added is what widens)."""
    old = set(old_granted)
    return sorted(g for g in set(new_granted) if not _covered(g, old))


# --------------------------------------------------------------------------- #
# Consent content ([CXC-PC-CONSENT-CONTENT]).
# --------------------------------------------------------------------------- #
ABSENT = "(absent — not declared in the manifest)"
NONE_DECLARED = "(none)"
#: [CXC-PC-CONSENT-CONTENT] dialog-title (r21): HoP's words; one space between
#: name and version, TWO spaces before "Manifest"
DIALOG_TITLE_SUFFIX = "  Manifest: Declared by its Developer"
#: [CXC-PC-CONSENT-CONTENT] permission-lines (r21): ONE heading, shown once and
#: only when at least one permission is declared
PERMISSIONS_HEADING = "Declared by the plug-in; not checked"


def dialog_title(manifest: Manifest) -> str:
    """``<name> <version>  Manifest: Declared by its Developer``, from the
    manifest's ``name`` and ``version``."""
    return f"{manifest.data['name']} {manifest.version}{DIALOG_TITLE_SUFFIX}"


@dataclass(frozen=True)
class ConsentLine:
    """One line of the consent screen. ``grant`` is the granted string this
    line consents to, if any; ``added`` marks a grant the previous consent did
    not hold (the re-consent screen highlights it)."""

    label: str
    value: str
    grant: "str | None" = None
    added: bool = False
    #: "field" (``label: value``), "heading" (the permissions heading, shown
    #: as its value alone) or "permission" (one declared permission, shown as
    #: its value alone, in the permission-lines format)
    kind: str = "field"

    def text(self) -> str:
        mark = "[ADDED] " if self.added else ""
        if self.kind == "field":
            return f"{mark}{self.label}: {self.value}"
        return f"{mark}{self.value}"


@dataclass(frozen=True)
class ConsentContent:
    domain: str
    version: str
    host: str
    runs_as: str
    lines: "tuple[ConsentLine, ...]"
    granted: "tuple[str, ...]"
    #: grants absent from ``previous_granted`` (empty when none was given)
    added: "tuple[str, ...]" = field(default=())
    #: True iff ``previous_granted`` was given and the new set widens it
    widens: bool = False
    #: the dialog title ([CXC-PC-CONSENT-CONTENT] dialog-title, r21)
    title: str = ""

    def text(self) -> str:
        """The title, then the manifest summary below it, one line each."""
        return "\n".join([self.title] + [line.text() for line in self.lines])


def render_consent(manifest: Manifest, *, host: str, runs_as: str,
                   render_host_section: HostSectionRenderer,
                   previous_granted: "Iterable[str] | None" = None,
                   previous_runs_as: "str | None" = None) -> ConsentContent:
    """Render the consent content from the manifest ALONE.

    ``runs_as`` is the ACTUAL isolation level the host will use; it may not be
    below the manifest's ``isolation_floor``. ``render_host_section`` renders
    THIS host's own section. With ``previous_granted`` (an upgrade), lines whose
    grant is new are marked ``added`` and ``widens`` is computed.

    Makes no network call and imports nothing from the plug-in.
    """
    if not isinstance(manifest, Manifest):
        raise TypeError("render_consent takes a validated Manifest")
    if runs_as not in ISOLATION_LEVELS:
        raise ConsentRefused(f"runs_as {runs_as!r} REFUSED: must be one of "
                             f"{list(ISOLATION_LEVELS)}")
    floor = manifest.isolation_floor
    if ISOLATION_LEVELS.index(runs_as) < ISOLATION_LEVELS.index(floor):
        raise ConsentRefused(f"runs_as {runs_as!r} REFUSED: below the plug-in's "
                             f"isolation floor {floor!r}")
    data = manifest.data
    prev = None if previous_granted is None else set(previous_granted)

    def line(label: str, value: str, grant: "str | None" = None,
             kind: str = "field") -> ConsentLine:
        return ConsentLine(label, value, grant,
                           added=bool(grant and prev is not None and not _covered(grant, prev)),
                           kind=kind)

    out: list[ConsentLine] = [
        line("name", data["name"]),
        line("version", data["version"]),
        line("description", data["description"]),
    ]
    owners = data.get("codeowners")
    if owners is None:
        out.append(line("code owners", ABSENT))
    elif not owners:
        out.append(line("code owners", NONE_DECLARED))
    else:
        out.append(line("code owners", ", ".join(owners)
                        + " (as declared by the package; not a verified publisher identity)"))
    out.append(line("documentation", data["documentation"]))
    out.append(line("run form", data["run"]["form"]))
    dropped = prev is not None and level_drops(previous_runs_as, runs_as)
    out.append(ConsentLine("runs as", runs_as if not dropped else
                           f"{runs_as} (was {previous_runs_as}: LESS isolation than before)",
                           None, added=dropped))
    reqs = data["requirements"]
    if not reqs:
        out.append(line("code this installs", NONE_DECLARED))
    else:
        out.extend(line("code this installs", r, code_grant(r)) for r in reqs)
    if "bus" not in data:
        out.append(line("publishes", ABSENT + " — no bus needed"))
        out.append(line("subscribes", ABSENT + " — no bus needed"))
    else:
        if not manifest.publishes:
            out.append(line("publishes", NONE_DECLARED))
        out.extend(line("publishes", f"{ns} {t}", bus_publish_grant(ns, t))
                   for ns, t in manifest.publishes)
        if not manifest.subscribes:
            out.append(line("subscribes", NONE_DECLARED))
        out.extend(line("subscribes", f"{ns} {t}", bus_subscribe_grant(ns, t))
                   for ns, t in manifest.subscribes)
    net, fs = manifest.network_permissions, manifest.filesystem_permissions
    if net or fs:
        out.append(line("permissions", PERMISSIONS_HEADING, kind="heading"))
        out.extend(line("permission", f"{d} {t}: {why}", network_grant(d, t), "permission")
                   for d, t, why in net)
        out.extend(line("permission", f"{a} {p}: {why}", filesystem_grant(a, p), "permission")
                   for a, p, why in fs)
    perms = _host_permissions(manifest, host, render_host_section)
    label = f"{host} permissions"
    if not perms:
        out.append(line(label, NONE_DECLARED))
    out.extend(line(label, p.text, p.grant) for p in perms)

    granted = sorted({ln.grant for ln in out if ln.grant})
    added = tuple(added_grants(prev, granted)) if prev is not None else ()
    return ConsentContent(
        domain=manifest.domain, version=manifest.version, host=host,
        runs_as=runs_as, lines=tuple(out), granted=tuple(granted), added=added,
        widens=prev is not None and upgrade_widens(prev, granted, previous_runs_as, runs_as),
        title=dialog_title(manifest))


# --------------------------------------------------------------------------- #
# The consent record and its store ([CXC-PC-CONSENT-RECORD]).
# --------------------------------------------------------------------------- #
DECISIONS = ("granted", "refused")
RECORD_KEYS = ("domain", "version", "manifest_sha256", "granted", "decided_by",
               "decided_at", "host", "decision")
_SHA_RE = re.compile(r"[0-9a-f]{64}")


#: every text field of a record ([CXC-PC-CONSENT-RECORD] granted-format-r9)
RECORD_TEXT_FIELDS = ("domain", "version", "manifest_sha256", "decided_by",
                      "decided_at", "host", "decision")


def text_field_problem(name: str, value: object) -> "str | None":
    """r9: a record text field obeys BOTH the N3 blank rule and the one-line
    rule, so no record can carry a forged second line."""
    if not isinstance(value, str):
        return f"{name} must be a string (got {value!r})"
    if is_blank(value):
        return f"{name} must be a non-empty string (got {value!r})"
    if has_line_break(value):
        return (f"{name} must be ONE line (got {value!r}: it holds a line or "
                f"paragraph separator, which could forge a line in the audit trail)")
    return None


def grant_problem(grant: object, host: str) -> "str | None":
    """r9/r21: a granted string is exactly one of bus.publish:<namespace>:<TYPE>,
    bus.subscribe:<namespace>:<TYPE>, code:<pep503-name>==<version>,
    permission.network:<direction>:<target>, permission.filesystem:<access>:<path>,
    or ``<host>:<permission>`` for THIS record's host."""
    p = text_field_problem("granted entry", grant)
    if p is not None:
        return p
    assert isinstance(grant, str)
    if grant.startswith("bus."):
        ok = BUS_GRANT_RE.fullmatch(grant) is not None
    elif grant.startswith("code:"):
        ok = CODE_GRANT_RE.fullmatch(grant) is not None
    elif grant.startswith(NETWORK_GRANT_PREFIX):
        direction, sep, target = grant[len(NETWORK_GRANT_PREFIX):].partition(":")
        ok = bool(sep) and direction in NETWORK_DIRECTIONS \
            and permission_target_problem(target) is None
    elif grant.startswith(FILESYSTEM_GRANT_PREFIX):
        access, sep, path = grant[len(FILESYSTEM_GRANT_PREFIX):].partition(":")
        ok = bool(sep) and access in FILESYSTEM_ACCESS \
            and permission_path_problem(path) is None
    elif grant.startswith("permission."):
        ok = False
    else:
        prefix = f"{host}:"
        ok = isinstance(host, str) and grant.startswith(prefix) and len(grant) > len(prefix)
    if not ok:
        return (f"granted entry {grant!r} is not one of the r9 forms "
                f"(bus.publish:<namespace>:<TYPE>, bus.subscribe:<namespace>:<TYPE>, "
                f"code:<pep503-name>==<version>, permission.network:<direction>:<target>, "
                f"permission.filesystem:<access>:<path>, {host}:<permission>)")
    return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ConsentRecord:
    domain: str
    version: str
    manifest_sha256: str
    granted: "tuple[str, ...]"
    decided_by: str
    decided_at: str
    host: str
    decision: str

    def __post_init__(self) -> None:
        problems = []
        for k in RECORD_TEXT_FIELDS:
            p = text_field_problem(k, getattr(self, k))
            if p is not None:
                problems.append(p)
        if not isinstance(self.manifest_sha256, str) or \
                _SHA_RE.fullmatch(self.manifest_sha256) is None:
            problems.append(f"manifest_sha256 must be 64 lowercase hex characters "
                            f"(got {self.manifest_sha256!r})")
        if isinstance(self.host, str) and not has_line_break(self.host) \
                and not is_blank(self.host) and not matches("host_name", self.host):
            problems.append(f"host {self.host!r} breaks the host-name rule "
                            "(^[a-z][a-z0-9_]*$, never 'bus', 'code' or 'permission')")
        if self.decision not in DECISIONS:
            problems.append(f"decision must be one of {list(DECISIONS)} "
                            f"(got {self.decision!r})")
        g = self.granted
        if not isinstance(g, tuple) or not all(isinstance(s, str) for s in g):
            problems.append(f"granted must be a tuple of strings (got {g!r})")
        else:
            if list(g) != sorted(set(g)):
                problems.append("granted must be sorted and de-duplicated")
            for entry in g:
                p = grant_problem(entry, self.host)
                if p is not None:
                    problems.append(p)
        if problems:
            raise ConsentRefused("consent record REFUSED: " + "; ".join(problems))

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in RECORD_KEYS}
        d["granted"] = list(self.granted)
        return d

    @classmethod
    def from_json(cls, d: Any) -> "ConsentRecord":
        if not isinstance(d, dict) or set(d) != set(RECORD_KEYS):
            got = sorted(d) if isinstance(d, dict) else type(d).__name__
            raise ConsentRefused(f"consent record REFUSED: keys must be exactly "
                                 f"{list(RECORD_KEYS)} (got {got})")
        g = d["granted"]
        return cls(**{**d, "granted": tuple(g) if isinstance(g, list) else g})


def make_record(*, manifest: Manifest, manifest_sha256: str, granted: Iterable[str],
                decided_by: str, host: str, decision: str,
                clock: Callable[[], datetime] = utc_now) -> ConsentRecord:
    """Build a record; ``decided_at`` is taken from ``clock`` (UTC, ISO 8601)."""
    now = clock()
    if now.tzinfo is None:
        raise ConsentRefused("consent record REFUSED: the clock returned a naive "
                             "datetime; decided_at must be timezone-aware")
    decided_at = now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return ConsentRecord(
        domain=manifest.domain, version=manifest.version,
        manifest_sha256=manifest_sha256, granted=tuple(sorted(set(granted))),
        decided_by=decided_by, decided_at=decided_at, host=host, decision=decision)


class ConsentStore:
    """An append-only JSON-lines file of consent records, in a host-managed
    location. There is NO delete or rewrite operation: a record outlives the
    plug-in's uninstall ([CXC-PC-CONSENT-RECORD] rule), and refusals are kept
    as well as grants."""

    def __init__(self, path: "str | os.PathLike[str]"):
        self.path = Path(path)

    def append(self, record: ConsentRecord) -> None:
        if not isinstance(record, ConsentRecord):
            raise TypeError("ConsentStore.append takes a ConsentRecord")
        line = (json.dumps(record.to_json(), sort_keys=True, ensure_ascii=False)
                + "\n").encode("utf-8")
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                os.write(fd, line)
                os.fsync(fd)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def records(self, *, domain: "str | None" = None) -> "list[ConsentRecord]":
        if not self.path.exists():
            return []
        out = []
        with self.path.open("r", encoding="utf-8") as fh:
            for n, raw in enumerate(fh, start=1):
                if not raw.strip():
                    continue
                try:
                    rec = ConsentRecord.from_json(json.loads(raw))
                except (ValueError, ConsentRefused) as exc:
                    raise ConsentRefused(f"consent store {self.path} line {n} is "
                                         f"unreadable: {exc}") from exc
                if domain is None or rec.domain == domain:
                    out.append(rec)
        return out


__all__ = [
    "DIALOG_TITLE_SUFFIX", "FILESYSTEM_GRANT_PREFIX", "NETWORK_GRANT_PREFIX",
    "PERMISSIONS_HEADING", "dialog_title", "filesystem_grant", "network_grant",
    "ABSENT", "ConsentContent", "ConsentLine", "ConsentRecord", "ConsentStore",
    "DECISIONS", "HostPermission", "HostSectionRenderer", "NONE_DECLARED",
    "RECORD_KEYS", "RECORD_TEXT_FIELDS", "added_grants", "bus_publish_grant", "bus_subscribe_grant",
    "code_grant",
    "grant_problem", "granted_set", "level_drops", "upgrade_widens", "make_record", "render_consent", "text_field_problem", "utc_now", "widens",
]
