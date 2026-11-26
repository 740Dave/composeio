"""Reading ``manifest.json`` WITHOUT executing anything, and the sha256 rule.

[CXC-PC-LIFECYCLE-INSTALL]:
  * package-sources: a LOCAL DIRECTORY or a WHEEL FILE only. A git URL or a
    package-index reference is refused naming the source kind.
  * manifest-read-location: ``manifest.json`` at the directory root, or inside
    the wheel zip at ``<import-package>/manifest.json``, read WITHOUT running
    any build backend or plug-in code.
  * manifest-sha256-definition: the SHA-256 of the EXACT BYTES of the shipped
    ``manifest.json``, with NO canonicalization.

This module only uses ``pathlib``, ``zipfile``, ``hashlib`` and ``json``. It
never imports, builds or runs anything from the package it reads.

``<import-package>`` in a wheel is found by listing the zip: the ONE top-level
directory (not ``*.dist-info`` or ``*.data``) that holds a ``manifest.json``.
Zero such entries refuses as missing; more than one refuses as ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import ManifestShaMismatch, ManifestUnreadable, PackageSourceRefused

MANIFEST_NAME = "manifest.json"
#: qa-wave1-rulings N1: manifest.json is at most 256 KiB (on disk or
#: decompressed) and nests at most 64 levels
MAX_MANIFEST_BYTES = 256 * 1024
MAX_NESTING_DEPTH = 64

_WHEEL_MANIFEST_RE = re.compile(r"([^/]+)/manifest\.json")
_INDEX_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(\[[^\]]*\])?\s*([<>=!~]=?.*)?")
_SDIST_SUFFIXES = (".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar", ".zip")


def manifest_sha256(manifest_bytes: bytes) -> str:
    """SHA-256 of the exact bytes, lowercase hex. No canonicalization."""
    if not isinstance(manifest_bytes, (bytes, bytearray)):
        raise TypeError("manifest_sha256 takes the manifest's exact BYTES, "
                        f"not {type(manifest_bytes).__name__}")
    return hashlib.sha256(bytes(manifest_bytes)).hexdigest()


@dataclass(frozen=True)
class PackageSource:
    """An opened package: a directory or a wheel. Holds no plug-in code."""

    kind: str          # "directory" or "wheel"
    path: Path
    #: the wheel's ``<import-package>`` (None for a directory)
    import_package: "str | None" = None

    @property
    def manifest_location(self) -> str:
        if self.kind == "directory":
            return str(self.path / MANIFEST_NAME)
        return f"{self.path}!{self.import_package}/{MANIFEST_NAME}"

    def has_directory(self, relpath: str) -> bool:
        """True iff ``relpath`` (relative to the directory holding
        ``manifest.json``) names a directory INSIDE the package."""
        parts = [p for p in re.split(r"[/\\]", relpath) if p not in ("", ".")]
        if self.kind == "directory":
            # Walk component by component: the path may not BE, or PASS
            # THROUGH, a symlink whose target leaves the package
            # ([CXC-PC-MANIFEST-FIELDS] validator-readings (7)).
            # Each name is compared EXACTLY against the directory listing, so
            # 'SQ' never matches 'sq' even on a case-insensitive filesystem
            # (qa-wave1-rulings G2): a directory and a wheel give one answer.
            root = self.path.resolve()
            cur = self.path
            for part in parts:
                try:
                    if part not in os.listdir(cur):
                        return False
                except (NotADirectoryError, FileNotFoundError):
                    return False
                cur = cur / part
                if cur.is_symlink() or not cur.exists():
                    try:
                        cur.resolve().relative_to(root)
                    except ValueError:
                        return False
            return cur.resolve().is_dir()
        prefix = "/".join([self.import_package or "", *parts]) + "/"
        with zipfile.ZipFile(self.path) as zf:
            return any(n.startswith(prefix) for n in zf.namelist())


@dataclass(frozen=True)
class ManifestFile:
    """The manifest as read: exact bytes, their digest, the parsed JSON."""

    raw: bytes
    sha256: str
    data: Any
    package: PackageSource

    @property
    def location(self) -> str:
        return self.package.manifest_location


def _classify_string_source(source: str) -> "str | None":
    """Return a refused source kind for a URL-like string, else None."""
    s = source.strip()
    low = s.lower()
    if low.startswith(("git+", "git@", "git://")):
        return "git URL"
    if "://" in low and re.search(r"\.git([#@/]|$)", low):
        return "git URL"
    if "://" in s:
        return "remote URL"
    return None


def open_package(source: "str | os.PathLike[str]") -> PackageSource:
    """Open a v0.1 package source: a local directory or a ``.whl`` file.

    Everything else is refused naming the source kind."""
    src = os.fspath(source)
    if isinstance(source, str):
        kind = _classify_string_source(source)
        if kind is not None:
            raise PackageSourceRefused(
                f"package source REFUSED: {source!r} is a {kind}; contract 0.1 "
                f"accepts a local directory or a wheel file only "
                f"(git URLs and package indexes are reserved for v0.2)",
                source=src, kind=kind)
    path = Path(src)
    if path.is_dir():
        return PackageSource(kind="directory", path=path)
    if path.is_file():
        name = path.name.lower()
        if name.endswith(".whl"):
            return PackageSource(kind="wheel", path=path,
                                 import_package=_find_wheel_import_package(path))
        if name.endswith(_SDIST_SUFFIXES):
            raise PackageSourceRefused(
                f"package source REFUSED: {src!r} is a source archive (sdist); "
                f"reading it would need a build backend. Contract 0.1 accepts a "
                f"local directory or a wheel file only",
                source=src, kind="source archive")
        raise PackageSourceRefused(
            f"package source REFUSED: {src!r} is a file that is not a wheel "
            f"(.whl); contract 0.1 accepts a local directory or a wheel file only",
            source=src, kind="non-wheel file")
    if isinstance(source, str) and os.sep not in src and "/" not in src \
            and _INDEX_REF_RE.fullmatch(src.strip()):
        raise PackageSourceRefused(
            f"package source REFUSED: {src!r} is not a local path and reads as a "
            f"package-index reference; contract 0.1 accepts a local directory or "
            f"a wheel file only (package indexes are reserved for v0.2)",
            source=src, kind="package-index reference")
    raise PackageSourceRefused(
        f"package source REFUSED: {src!r} does not exist",
        source=src, kind="missing path")


def _find_wheel_import_package(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
    except zipfile.BadZipFile as exc:
        raise PackageSourceRefused(
            f"package source REFUSED: {str(path)!r} is not a readable wheel "
            f"(zip): {exc}", source=str(path), kind="corrupt wheel") from exc
    found = []
    for n in names:
        m = _WHEEL_MANIFEST_RE.fullmatch(n)
        if m and not m.group(1).endswith((".dist-info", ".data")):
            found.append(m.group(1))
    if not found:
        raise ManifestUnreadable(
            f"install REFUSED: {path}!<import-package>/{MANIFEST_NAME} is missing "
            f"(no top-level package directory in the wheel holds a {MANIFEST_NAME})",
            path=f"{path}!<import-package>/{MANIFEST_NAME}")
    if len(found) > 1:
        raise ManifestUnreadable(
            f"install REFUSED: {path} holds {len(found)} candidate manifests "
            f"({', '.join(f'{p}/{MANIFEST_NAME}' for p in sorted(found))}); "
            f"exactly one <import-package>/{MANIFEST_NAME} is required",
            path=str(path))
    return found[0]


def _reject_duplicate_keys(pairs: "list[tuple[str, Any]]") -> dict:
    seen: dict = {}
    for k, v in pairs:
        if k in seen:
            raise ValueError(f"duplicate JSON object key {k!r}")
        seen[k] = v
    return seen


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _finite_float(text: str) -> float:
    """qa-wave1-rulings G1: a number that overflows to infinity (``1e999``) is
    refused, not silently turned into ``inf``."""
    value = float(text)
    if not math.isfinite(value):
        raise ValueError(f"non-finite number {text} (it parses to {value})")
    return value


def _nesting_depth_exceeds(text: str, limit: int) -> bool:
    """True iff arrays/objects nest deeper than ``limit`` (strings skipped).
    Runs BEFORE ``json.loads``, so deep input is a named refusal and never a
    RecursionError."""
    depth = 0
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
            if depth > limit:
                return True
        elif ch in "]}":
            depth -= 1
    return False


def _too_large(location: str, size: "int | str") -> ManifestUnreadable:
    return ManifestUnreadable(
        f"install REFUSED: {location} is too large ({size} bytes; contract 0.1 "
        f"allows at most {MAX_MANIFEST_BYTES} bytes, 256 KiB)", path=location)


def parse_manifest_bytes(raw: bytes, *, location: str) -> Any:
    """Strict JSON parse of the manifest bytes: at most 256 KiB, UTF-8, no
    duplicate keys, finite numbers only (no NaN, Infinity or overflow), nesting
    at most 64 levels. Refuses naming the location."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _too_large(location, len(raw))
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestUnreadable(
            f"install REFUSED: {location} is not UTF-8: {exc}", path=location) from exc
    if _nesting_depth_exceeds(text, MAX_NESTING_DEPTH):
        raise ManifestUnreadable(
            f"install REFUSED: {location} nests deeper than {MAX_NESTING_DEPTH} "
            "levels (contract 0.1 limit)", path=location)
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_constant, parse_float=_finite_float)
    except ValueError as exc:  # JSONDecodeError is a ValueError
        raise ManifestUnreadable(
            f"install REFUSED: {location} is not valid JSON: {exc}",
            path=location) from exc
    except RecursionError as exc:  # unreachable after the depth pre-scan; loud anyway
        raise ManifestUnreadable(
            f"install REFUSED: {location} nests too deeply to parse", path=location) from exc


def _is_zip_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK(info.external_attr >> 16)


def read_manifest(source: "str | os.PathLike[str] | PackageSource") -> ManifestFile:
    """Read and parse ``manifest.json`` from a directory or a wheel.

    Executes nothing from the package. Does NOT validate the manifest against
    the contract; see :func:`compose_plugins.manifest.validate_manifest`.
    The file name is matched EXACTLY (G2), a symlinked manifest.json is
    refused (N5), and at most 256 KiB is ever read (N1)."""
    pkg = source if isinstance(source, PackageSource) else open_package(source)
    location = pkg.manifest_location
    if pkg.kind == "directory":
        mpath = pkg.path / MANIFEST_NAME
        if MANIFEST_NAME not in os.listdir(pkg.path):
            raise ManifestUnreadable(
                f"install REFUSED: {mpath} is missing (names are matched "
                f"exactly, case-sensitively)", path=str(mpath))
        st = os.lstat(mpath)
        if stat.S_ISLNK(st.st_mode):
            raise ManifestUnreadable(
                f"install REFUSED: {mpath} is a symlink; manifest.json must be a "
                f"regular file inside the package", path=str(mpath))
        if not stat.S_ISREG(st.st_mode):
            raise ManifestUnreadable(
                f"install REFUSED: {mpath} is not a regular file", path=str(mpath))
        if st.st_size > MAX_MANIFEST_BYTES:
            raise _too_large(location, st.st_size)
        with open(mpath, "rb") as fh:
            raw = fh.read(MAX_MANIFEST_BYTES + 1)
    else:
        with zipfile.ZipFile(pkg.path) as zf:
            info = zf.getinfo(f"{pkg.import_package}/{MANIFEST_NAME}")
            if _is_zip_symlink(info):
                raise ManifestUnreadable(
                    f"install REFUSED: {location} is a symlink entry; manifest.json "
                    f"must be a regular file inside the package", path=location)
            if info.file_size > MAX_MANIFEST_BYTES:
                raise _too_large(location, info.file_size)
            with zf.open(info) as fh:   # the header may lie: cap the read too
                raw = fh.read(MAX_MANIFEST_BYTES + 1)
    if len(raw) > MAX_MANIFEST_BYTES:
        raise _too_large(location, f"more than {MAX_MANIFEST_BYTES}")
    data = parse_manifest_bytes(raw, location=location)
    return ManifestFile(raw=raw, sha256=manifest_sha256(raw), data=data, package=pkg)


def verify_manifest_sha256(consented_sha256: str, fetched: "ManifestFile | bytes",
                           *, location: str = "manifest.json") -> None:
    """Refuse unless the fetched manifest is byte-identical to the consented
    one ([CXC-PC-LIFECYCLE-INSTALL]); the refusal names both digests."""
    if isinstance(fetched, ManifestFile):
        actual, location = fetched.sha256, fetched.location
    else:
        actual = manifest_sha256(fetched)
    if not isinstance(consented_sha256, str) or \
            re.fullmatch(r"[0-9a-f]{64}", consented_sha256) is None:
        raise ManifestShaMismatch(expected=repr(consented_sha256), actual=actual,
                                  location=location)
    if actual != consented_sha256:
        raise ManifestShaMismatch(expected=consented_sha256, actual=actual,
                                  location=location)


__all__ = [
    "MANIFEST_NAME", "MAX_MANIFEST_BYTES", "MAX_NESTING_DEPTH", "ManifestFile", "PackageSource", "manifest_sha256",
    "open_package", "parse_manifest_bytes", "read_manifest",
    "verify_manifest_sha256",
]
