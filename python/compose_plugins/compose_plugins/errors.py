"""Refusals: every refusal in this library is loud and names what it refuses.

[SPEC:CXC-PLUGIN-CONTRACT] says each refusal at install names the field
([CXC-PC-MANIFEST-FIELDS] boundary-case) and that "loudly" means a named,
human-readable reason ([CXC-PC-NEGATIVE-CONTROLS]). So no refusal here is a
bare ``ValueError``: each one carries the field path (or the source, or both
digests) as data, and its text says the same thing for a human.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


class PluginContractRefusal(Exception):
    """Base class of every refusal this library raises."""


@dataclass(frozen=True)
class Refusal:
    """One refused field. ``field`` is a path such as ``domain``,
    ``requirements[2]`` or ``bus.publishes[0].type``."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


class ManifestRefused(PluginContractRefusal):
    """The manifest failed validation. ``refusals`` lists every refused field."""

    def __init__(self, refusals: "list[Refusal]"):
        if not refusals:
            raise ValueError("ManifestRefused needs at least one Refusal")
        self.refusals = list(refusals)
        lines = "\n".join(f"  - {r}" for r in self.refusals)
        super().__init__(f"manifest REFUSED ({len(self.refusals)} field(s)):\n{lines}")

    @property
    def fields(self) -> "list[str]":
        return [r.field for r in self.refusals]


class ContractVersionRefused(PluginContractRefusal):
    """The manifest's ``contract`` cannot be accepted by this host
    ([CXC-PC-VERSION-RULE]). Carries both versions (``manifest`` may be the
    raw, malformed value)."""

    def __init__(self, message: str, *, manifest: object, host: str):
        self.manifest = manifest
        self.host = host
        super().__init__(message)


class PackageSourceRefused(PluginContractRefusal):
    """The package source is not a kind v0.1 accepts, or cannot be read
    ([CXC-PC-LIFECYCLE-INSTALL] package-sources). ``kind`` names the source
    kind (for example ``git URL``)."""

    def __init__(self, message: str, *, source: str, kind: str):
        self.source = source
        self.kind = kind
        super().__init__(message)


class ManifestUnreadable(PluginContractRefusal):
    """``manifest.json`` is missing, not UTF-8, or not valid JSON
    ([CXC-PC-MANIFEST-FILE] boundary-case). ``path`` names the failing path."""

    def __init__(self, message: str, *, path: str):
        self.path = path
        super().__init__(message)


class ManifestShaMismatch(PluginContractRefusal):
    """The fetched manifest is not the one shown at consent
    ([CXC-PC-NC-TAMPERED-MANIFEST]). Carries both digests."""

    def __init__(self, *, expected: str, actual: str, location: str):
        self.expected = expected
        self.actual = actual
        self.location = location
        super().__init__(
            f"install REFUSED: manifest_sha256 mismatch for {location}: "
            f"expected (consented) {expected}, actual (fetched) {actual}")


class HostNameRefused(PluginContractRefusal):
    """A host's OWN name breaks [CXC-PC-HOST-SECTIONS] host-name-rule (r10):
    it must match ^[a-z][a-z0-9_]*$ and is never ``bus``, ``code`` or
    ``permission`` (``permission`` since r21, with the permission grant forms)."""

    def __init__(self, name: object):
        self.name = name
        super().__init__(f"host name {name!r} REFUSED: a host name matches "
                         f"^[a-z][a-z0-9_]*$ and is never 'bus', 'code' or "
                         f"'permission' (the prefixes of the built-in grant forms)")


class ConsentRefused(PluginContractRefusal):
    """A consent-layer input is malformed (for example a host grant without
    its ``<host>:`` prefix, or an isolation level below the plug-in's floor)."""


# --------------------------------------------------------------------------- #
# Contract 0.2 ([DELTA:CXC-PLUGIN-CONTRACT-V0-2-DELTA]).
# --------------------------------------------------------------------------- #
class EulaShaMismatch(PluginContractRefusal):
    """The EULA file is not the one shown at consent, or the caller's
    ``consented_eula_sha256`` is missing, malformed or given for a manifest
    without ``eula`` ([CXC-PC-V02-EULA-HASH-RECORD] bound-to-the-shown-text).
    Carries both digests (``expected`` is what the caller consented to)."""

    def __init__(self, message: str, *, expected: object, actual: "str | None"):
        self.expected = expected
        self.actual = actual
        super().__init__(message)


class PromptNotPrepared(PluginContractRefusal):
    """``record_decline`` was given a prompt this manager did not prepare
    ([CXC-PC-V02-RECORD-DECLINE-PROMPT-VALIDITY] prepared)."""

    def __init__(self) -> None:
        super().__init__("record_decline REFUSED: the prompt was not prepared by this manager")


class PromptStale(PluginContractRefusal):
    """``record_decline`` was given a prompt whose subject changed since it was
    prepared, or that was already declined ([CXC-PC-V02-RECORD-DECLINE-PROMPT-VALIDITY])."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"record_decline REFUSED: the prompt is stale: {reason}")


_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
#: the token recorded for a class whose name is not an identifier (candidate
#: review A1, ruled: type("x y", (Exception,), {}) makes one)
NON_IDENTIFIER_CLASS = "NonIdentifierException"


def safe_class_name(exc: BaseException) -> str:
    """The exception's class name for install-log text
    ([CXC-PC-V02-INSTALL-LOG-FAILURE-TEXT]): ``type(exc).__name__`` when it is an
    identifier (cut to 200 characters), else ``NonIdentifierException``, so
    no other text of the class can reach the log, a finding or a message,
    and an abort event can always be written."""
    name = type(exc).__name__
    if not isinstance(name, str) or _IDENTIFIER_RE.fullmatch(name) is None:
        return NON_IDENTIFIER_CLASS
    return name[:200]


class InstallLogRefused(PluginContractRefusal):
    """An event could not be appended to ``installs.jsonl``
    ([CXC-PC-V02-INSTALL-LOG-WRITE-ORDER] failure-of-the-append). The message
    names the log path and the CLASS of the underlying error, never its text
    ([CXC-PC-V02-INSTALL-LOG-FAILURE-TEXT])."""

    def __init__(self, path: object, error_class: str):
        self.path = str(path)
        self.error_class = error_class
        super().__init__(f"install log REFUSED: an event could not be appended to "
                         f"{self.path} ({error_class}); nothing changed")
