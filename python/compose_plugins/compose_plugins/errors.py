"""Refusals: every refusal in this library is loud and names what it refuses.

[SPEC:CXC-PLUGIN-CONTRACT] says each refusal at install names the field
([CXC-PC-MANIFEST-FIELDS] boundary-case) and that "loudly" means a named,
human-readable reason ([CXC-PC-NEGATIVE-CONTROLS]). So no refusal here is a
bare ``ValueError``: each one carries the field path (or the source, or both
digests) as data, and its text says the same thing for a human.
"""
from __future__ import annotations

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
