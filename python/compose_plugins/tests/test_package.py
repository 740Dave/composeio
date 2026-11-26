"""manifest_sha256 and reading manifest.json from a directory or a wheel
WITHOUT executing anything ([CXC-PC-LIFECYCLE-INSTALL], [CXC-PC-MANIFEST-FILE],
[CXC-PC-NC-TAMPERED-MANIFEST] library half)."""
from __future__ import annotations

import builtins
import hashlib
import importlib
import json
import subprocess
import sys
import zipfile

import pytest

from compose_plugins.errors import (
    ManifestShaMismatch, ManifestUnreadable, PackageSourceRefused,
)
from compose_plugins.manifest import validate_manifest
from compose_plugins.package import (
    manifest_sha256, open_package, read_manifest, verify_manifest_sha256,
)

from conftest import empty_section, valid_manifest

CANARY_MODULE = "hello_world_canary_pkg"


def _manifest_bytes(indent=2) -> bytes:
    m = valid_manifest()
    m["run"]["entry"] = f"{CANARY_MODULE}.plugin:HelloWorld"
    return (json.dumps(m, indent=indent) + "\n").encode("utf-8")


def _canary_source(canary_path) -> str:
    return (f"open({str(canary_path)!r}, 'w').write('IMPORTED')\n"
            "raise SystemExit('plug-in code was executed')\n")


def make_dir_package(tmp_path, raw=None):
    root = tmp_path / "pkgdir"
    root.mkdir()
    (root / "manifest.json").write_bytes(raw if raw is not None else _manifest_bytes())
    pkg = root / CANARY_MODULE
    pkg.mkdir()
    canary = tmp_path / "CANARY"
    (pkg / "__init__.py").write_text(_canary_source(canary))
    (pkg / "plugin.py").write_text(_canary_source(canary))
    (root / "setup.py").write_text(_canary_source(canary))
    (root / "pyproject.toml").write_text("[build-system]\nrequires=[]\n"
                                         "build-backend='canary_backend'\n")
    return root, canary


def make_wheel(tmp_path, raw=None, extra=(), with_manifest=True, name="hello_world"):
    whl = tmp_path / f"{name}-1.0.0-py3-none-any.whl"
    canary = tmp_path / "CANARY"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr(f"{CANARY_MODULE}/__init__.py", _canary_source(canary))
        zf.writestr(f"{CANARY_MODULE}/plugin.py", _canary_source(canary))
        if with_manifest:
            zf.writestr(f"{CANARY_MODULE}/manifest.json",
                        raw if raw is not None else _manifest_bytes())
        zf.writestr(f"{CANARY_MODULE}/questions/q1.lts", "x")
        zf.writestr(f"{name}-1.0.0.dist-info/METADATA", "Name: hello-world\n")
        zf.writestr(f"{name}-1.0.0.dist-info/manifest.json", "{}")
        for path, data in extra:
            zf.writestr(path, data)
    return whl, canary


@pytest.fixture
def spies(monkeypatch):
    """Record every import of the plug-in module and every attempt to run a
    build backend or a subprocess."""
    calls = {"imports": [], "subprocess": [], "backend": []}
    real_import = builtins.__import__
    real_import_module = importlib.import_module

    def spy_import(name, *a, **kw):
        if name.startswith(CANARY_MODULE) or name.startswith("canary_backend"):
            calls["imports"].append(name)
        return real_import(name, *a, **kw)

    def spy_import_module(name, *a, **kw):
        calls["imports"].append(name)
        return real_import_module(name, *a, **kw)

    def no_subprocess(*a, **kw):
        calls["subprocess"].append(a)
        raise AssertionError("a subprocess was started while reading a manifest")

    monkeypatch.setattr(builtins, "__import__", spy_import)
    monkeypatch.setattr(importlib, "import_module", spy_import_module)
    for fn in ("Popen", "run", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, fn, no_subprocess)
    for mod in ("setuptools.build_meta", "pyproject_hooks", "build"):
        if mod in sys.modules:
            target = sys.modules[mod]
            for attr in ("build_wheel", "prepare_metadata_for_build_wheel",
                         "build_sdist", "get_requires_for_build_wheel"):
                if hasattr(target, attr):
                    monkeypatch.setattr(target, attr,
                                        lambda *a, _n=f"{mod}.{attr}", **k:
                                        calls["backend"].append(_n))
    return calls


# --------------------------------------------------------------------------- #
# The sha rule.
# --------------------------------------------------------------------------- #
def test_sha_is_sha256_of_the_exact_bytes():
    raw = _manifest_bytes()
    assert manifest_sha256(raw) == hashlib.sha256(raw).hexdigest()


def test_sha_matches_sha256sum_of_the_shipped_file(tmp_path):
    root, _ = make_dir_package(tmp_path)
    out = subprocess.run(["shasum", "-a", "256", str(root / "manifest.json")],
                         capture_output=True, text=True, check=True).stdout.split()[0]
    print("shasum:", out)
    assert read_manifest(root).sha256 == out


def test_reserialized_same_json_has_a_different_digest_and_is_refused():
    a, b = _manifest_bytes(indent=2), _manifest_bytes(indent=None)
    assert json.loads(a) == json.loads(b)
    assert manifest_sha256(a) != manifest_sha256(b)
    with pytest.raises(ManifestShaMismatch) as ei:
        verify_manifest_sha256(manifest_sha256(a), b)
    print(ei.value)


def test_sha_takes_bytes_only():
    with pytest.raises(TypeError):
        manifest_sha256(_manifest_bytes().decode())


def test_matching_sha_is_accepted(tmp_path):
    root, _ = make_dir_package(tmp_path)
    mf = read_manifest(root)
    verify_manifest_sha256(manifest_sha256(_manifest_bytes()), mf)


def test_nc_tampered_manifest_one_byte_changed_names_both_digests(tmp_path):
    consented = manifest_sha256(_manifest_bytes())
    raw = bytearray(_manifest_bytes())
    i = raw.index(b"Hello World")
    raw[i] = ord("J")  # "Jello World"
    root, _ = make_dir_package(tmp_path, bytes(raw))
    mf = read_manifest(root)
    with pytest.raises(ManifestShaMismatch) as ei:
        verify_manifest_sha256(consented, mf)
    print(ei.value)
    assert consented in str(ei.value) and mf.sha256 in str(ei.value)
    assert ei.value.expected == consented and ei.value.actual == mf.sha256


@pytest.mark.parametrize("bad", ["", "abc", "A" * 64, None])
def test_malformed_consented_digest_is_refused(bad):
    with pytest.raises(ManifestShaMismatch):
        verify_manifest_sha256(bad, _manifest_bytes())


# --------------------------------------------------------------------------- #
# Reading from a directory.
# --------------------------------------------------------------------------- #
def test_read_from_directory_executes_nothing(tmp_path, spies):
    root, canary = make_dir_package(tmp_path)
    mf = read_manifest(root)
    assert mf.package.kind == "directory"
    assert mf.raw == _manifest_bytes() and mf.data["domain"] == "hello_world"
    vm = validate_manifest(mf.data, host="composeio", section_validator=empty_section,
                           package=mf.package)
    assert vm.domain == "hello_world"
    assert spies == {"imports": [], "subprocess": [], "backend": []}
    assert not canary.exists()
    assert CANARY_MODULE not in sys.modules


def test_directory_without_manifest_is_refused_naming_the_path(tmp_path):
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert ei.value.path == str(tmp_path / "manifest.json")


@pytest.mark.parametrize("raw,needle", [
    (b"{not json", "not valid JSON"),
    (b'{"a": 1, "a": 2}', "duplicate JSON object key 'a'"),
    (b'{"a": NaN}', "NaN"),
    (b"\xff\xfe{}", "not UTF-8"),
    (b"\xef\xbb\xbf{}", "not valid JSON"),
])
def test_invalid_json_is_refused_naming_the_path(tmp_path, raw, needle):
    root, _ = make_dir_package(tmp_path, raw)
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(root)
    print(ei.value)
    assert needle in str(ei.value) and str(root / "manifest.json") in str(ei.value)


# --------------------------------------------------------------------------- #
# Reading from a wheel.
# --------------------------------------------------------------------------- #
def test_read_from_wheel_executes_nothing(tmp_path, spies):
    whl, canary = make_wheel(tmp_path)
    mf = read_manifest(whl)
    assert mf.package.kind == "wheel" and mf.package.import_package == CANARY_MODULE
    assert mf.raw == _manifest_bytes()
    assert mf.location.endswith(f"!{CANARY_MODULE}/manifest.json")
    assert spies == {"imports": [], "subprocess": [], "backend": []}
    assert not canary.exists()
    assert CANARY_MODULE not in sys.modules


def test_wheel_without_import_package_manifest_is_refused(tmp_path):
    whl, _ = make_wheel(tmp_path, with_manifest=False,
                        extra=[("manifest.json", _manifest_bytes())])
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(whl)
    print(ei.value)
    assert "<import-package>/manifest.json is missing" in str(ei.value)


def test_wheel_with_two_candidate_manifests_is_refused(tmp_path):
    whl, _ = make_wheel(tmp_path, extra=[("other_pkg/manifest.json", b"{}")])
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(whl)
    print(ei.value)
    assert "other_pkg/manifest.json" in str(ei.value)


def test_corrupt_wheel_is_refused(tmp_path):
    whl = tmp_path / "x-1.0.0-py3-none-any.whl"
    whl.write_bytes(b"not a zip")
    with pytest.raises(PackageSourceRefused) as ei:
        open_package(whl)
    assert ei.value.kind == "corrupt wheel"


def test_wheel_standard_questions_existence(tmp_path):
    whl, _ = make_wheel(tmp_path)
    pkg = open_package(whl)
    assert pkg.has_directory("questions") is True
    assert pkg.has_directory("missing") is False


# --------------------------------------------------------------------------- #
# Package sources ([CXC-PC-LIFECYCLE-INSTALL] package-sources).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("src,kind", [
    ("git+https://example.invalid/hello_world.git", "git URL"),
    ("git@example.invalid:org/hello_world.git", "git URL"),
    ("git://example.invalid/hello_world", "git URL"),
    ("https://example.invalid/org/hello_world.git", "git URL"),
    ("ssh://git@example.invalid/org/hello_world.git@v1", "git URL"),
    ("https://example.invalid/hello_world-1.0.0-py3-none-any.whl", "remote URL"),
    ("hello-world==1.0.0", "package-index reference"),
    ("hello_world", "package-index reference"),
])
def test_non_local_sources_are_refused_naming_the_kind(tmp_path, monkeypatch, src, kind):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(PackageSourceRefused) as ei:
        open_package(src)
    print(ei.value)
    assert ei.value.kind == kind and kind in str(ei.value)


def test_sdist_and_other_files_are_refused(tmp_path):
    sd = tmp_path / "hello_world-1.0.0.tar.gz"
    sd.write_bytes(b"x")
    with pytest.raises(PackageSourceRefused) as ei:
        open_package(sd)
    assert ei.value.kind == "source archive"
    other = tmp_path / "manifest.json"
    other.write_bytes(b"{}")
    with pytest.raises(PackageSourceRefused) as ei:
        open_package(other)
    assert ei.value.kind == "non-wheel file"


def test_missing_local_path_is_refused(tmp_path):
    with pytest.raises(PackageSourceRefused) as ei:
        open_package(str(tmp_path / "nope"))
    assert ei.value.kind == "missing path"


def test_spies_and_canary_do_fire_when_plug_in_code_is_imported(tmp_path, spies, monkeypatch):
    """Negative control of the no-execution tests: the same spies and canary
    DO record an import, so their zero above is a measurement, not a blind spot."""
    root, canary = make_dir_package(tmp_path)
    monkeypatch.syspath_prepend(str(root))
    with pytest.raises(SystemExit, match="plug-in code was executed"):
        importlib.import_module(CANARY_MODULE)
    sys.modules.pop(CANARY_MODULE, None)
    assert CANARY_MODULE in spies["imports"]
    assert canary.read_text() == "IMPORTED"
    with pytest.raises(AssertionError, match="subprocess was started"):
        subprocess.run(["true"])
