"""The eleven validator readings, ratified in spec 95 r6
([CXC-PC-MANIFEST-FIELDS] validator-readings). Every test here cites its
reading as ``[reading N]`` in its docstring, and
``test_every_reading_has_a_test`` fails if a reading has no test. Each reading
gets a positive control and a negative control, so each check is shown both
able to pass and able to fail."""
from __future__ import annotations

import json
import re
import sys
import zipfile

import pytest

from compose_plugins.consent import ConsentStore, HostPermission, make_record, render_consent
from compose_plugins.errors import ManifestRefused, ManifestUnreadable
from compose_plugins.manifest import NO_PACKAGE, validate_manifest
from compose_plugins.package import PackageSource, open_package, read_manifest

from conftest import empty_section, valid_manifest

READINGS = range(1, 12)


def validate(m, host="composeio", package=NO_PACKAGE, section_validator=empty_section):
    return validate_manifest(m, host=host, section_validator=section_validator, package=package)


def refused(m, **kw):
    with pytest.raises(ManifestRefused) as ei:
        validate(m, **kw)
    print(ei.value)
    return ei.value


# --------------------------------------------------------------------------- #
def test_r1_bus_carries_both_lists():
    """[reading 1] `bus` carries BOTH publishes and subscribes; [] for none."""
    m = valid_manifest()
    m["bus"] = {"publishes": [], "subscribes": []}
    validate(m)
    for missing in ("publishes", "subscribes"):
        m = valid_manifest()
        del m["bus"][missing]
        assert refused(m).fields == [f"bus.{missing}"]


def test_r2_unknown_key_inside_a_bus_entry_is_refused():
    """[reading 2] Unknown keys inside a bus entry are refused."""
    m = valid_manifest()
    m["bus"]["subscribes"][0]["qos"] = 1
    assert refused(m).fields == ["bus.subscribes[0].qos"]


def test_r3_issue_tracker_http_or_https_documentation_https_only():
    """[reading 3] issue_tracker is http or https; documentation stays https."""
    for url in ("http://example.invalid/issues", "https://example.invalid/issues"):
        m = valid_manifest()
        m["issue_tracker"] = url
        validate(m)
    for url in ("ftp://example.invalid/issues", "example.invalid/issues", "mailto:a@b.invalid"):
        m = valid_manifest()
        m["issue_tracker"] = url
        assert refused(m).fields == ["issue_tracker"]
    m = valid_manifest()
    m["documentation"] = "http://example.invalid/docs"
    assert refused(m).fields == ["documentation"]


@pytest.mark.parametrize("field", ["name", "description"])
@pytest.mark.parametrize("value", ["", "   "])
def test_r4_name_and_description_are_non_empty(field, value):
    """[reading 4] name and description are non-empty."""
    m = valid_manifest()
    m[field] = value
    assert refused(m).fields == [field]


@pytest.mark.parametrize("pin", [
    "requests == 2.32.3",                      # spaces
    "requests ==2.32.3",
    "requests[security]==2.32.3",              # extras
    'requests==2.32.3; python_version>"3.8"',  # markers
    "requests==2.*",                           # wildcard
    "requests==2.32.*",
])
def test_r5_requirement_is_exactly_name_eq_eq_version(pin):
    """[reading 5] exactly name==version: no spaces, extras, markers or wildcards."""
    m = valid_manifest()
    m["requirements"] = [pin]
    assert refused(m).fields == ["requirements[0]"]


def test_r5_positive_control():
    """[reading 5] a plain exact pin passes."""
    m = valid_manifest()
    m["requirements"] = ["requests==2.32.3"]
    validate(m)


@pytest.mark.parametrize("entry", ["plügin:HelloWorld", "hello_world.plugin:Héllo",
                                   "hello-world:HelloWorld", "1mod:HelloWorld"])
def test_r6_run_entry_ascii_identifiers_only(entry):
    """[reading 6] run.entry is module:attr with ASCII Python identifiers only."""
    m = valid_manifest()
    m["run"]["entry"] = entry
    assert refused(m).fields == ["run.entry"]


def test_r6_positive_control():
    """[reading 6] dotted ASCII module and attribute pass."""
    m = valid_manifest()
    m["run"]["entry"] = "hello_world.plugin:HelloWorld.Inner"
    validate(m)


# ---- reading 7 ------------------------------------------------------------- #
def _pkg(tmp_path):
    root = tmp_path / "pkg"
    (root / "docs" / "questions").mkdir(parents=True)
    (root / "a_file").write_text("x")
    (tmp_path / "outside" / "questions").mkdir(parents=True)
    return root


def _sq(value, root):
    m = valid_manifest()
    m["standard_questions"] = value
    return m, PackageSource(kind="directory", path=root)


def test_r7_relative_to_the_manifest_directory_and_must_be_a_directory(tmp_path):
    """[reading 7] relative to the directory holding manifest.json; a directory."""
    root = _pkg(tmp_path)
    m, pkg = _sq("docs/questions", root)
    validate(m, package=pkg)
    m, pkg = _sq("a_file", root)
    assert refused(m, package=pkg).fields == ["standard_questions"]
    m, pkg = _sq("questions", root)   # exists only one level down, not at the root
    assert refused(m, package=pkg).fields == ["standard_questions"]


def test_r7_symlink_that_leaves_the_package_is_refused(tmp_path):
    """[reading 7] the path may not BE a symlink that leaves the package."""
    root = _pkg(tmp_path)
    (root / "q_link").symlink_to(tmp_path / "outside" / "questions")
    m, pkg = _sq("q_link", root)
    assert refused(m, package=pkg).fields == ["standard_questions"]


def test_r7_passing_through_a_leaving_symlink_is_refused_even_if_it_comes_back(tmp_path):
    """[reading 7] the path may not PASS THROUGH a symlink that leaves the
    package, even when the final target resolves back inside it."""
    root = _pkg(tmp_path)
    (tmp_path / "outside" / "back").symlink_to(root / "docs" / "questions")
    (root / "via").symlink_to(tmp_path / "outside")
    m, pkg = _sq("via/back", root)
    assert (root / "via" / "back").resolve() == (root / "docs" / "questions").resolve()
    assert refused(m, package=pkg).fields == ["standard_questions"]


def test_r7_symlink_staying_inside_the_package_passes(tmp_path):
    """[reading 7] positive control: an internal symlink is inside the package."""
    root = _pkg(tmp_path)
    (root / "q_inside").symlink_to(root / "docs" / "questions")
    m, pkg = _sq("q_inside", root)
    validate(m, package=pkg)


def test_r7_in_a_wheel_the_path_is_relative_to_the_import_package(tmp_path):
    """[reading 7] in a wheel, relative to <import-package>/ (where manifest.json is)."""
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("hw/manifest.json", "{}")
        zf.writestr("hw/questions/q.lts", "x")
        zf.writestr("questions_at_root/q.lts", "x")
    pkg = open_package(whl)
    m = valid_manifest()
    m["standard_questions"] = "questions"
    validate(m, package=pkg)
    m["standard_questions"] = "questions_at_root"
    assert refused(m, package=pkg).fields == ["standard_questions"]


# ---- reading 8 ------------------------------------------------------------- #
@pytest.mark.parametrize("raw,needle", [
    (b'{"domain": "a", "domain": "b"}', "duplicate JSON object key 'domain'"),
    (b'{"x": {"y": 1, "y": 2}}', "duplicate JSON object key 'y'"),
    (b'{"x": NaN}', "NaN"),
    (b'{"x": Infinity}', "Infinity"),
    (b'{"x": -Infinity}', "-Infinity"),
])
def test_r8_reader_refuses_duplicate_keys_nan_infinity(tmp_path, raw, needle):
    """[reading 8] the reader refuses duplicate JSON keys and NaN or Infinity."""
    (tmp_path / "manifest.json").write_bytes(raw)
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert needle in str(ei.value)


def test_r8_positive_control(tmp_path):
    """[reading 8] ordinary JSON with large finite numbers is read."""
    (tmp_path / "manifest.json").write_bytes(b'{"x": 1e308, "y": {"z": 1}}')
    assert read_manifest(tmp_path).data == {"x": 1e308, "y": {"z": 1}}


# ---- reading 9 ------------------------------------------------------------- #
def _wheel(tmp_path, names):
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        for n in names:
            zf.writestr(n, "{}")
    return whl


def test_r9_single_top_level_package_is_the_import_package(tmp_path):
    """[reading 9] the single top-level dir (not *.dist-info / *.data) with a manifest."""
    whl = _wheel(tmp_path, ["hw/manifest.json", "hw-1.0.0.dist-info/manifest.json",
                            "hw-1.0.0.data/manifest.json", "hw/sub/manifest.json"])
    assert open_package(whl).import_package == "hw"


@pytest.mark.parametrize("names,needle", [
    (["manifest.json", "hw-1.0.0.dist-info/manifest.json", "hw/sub/manifest.json"], "is missing"),
    (["a/manifest.json", "b/manifest.json"], "exactly one"),
])
def test_r9_none_or_more_than_one_is_refused(tmp_path, names, needle):
    """[reading 9] none or more than one candidate is refused."""
    with pytest.raises(ManifestUnreadable) as ei:
        open_package(_wheel(tmp_path, names))
    print(ei.value)
    assert needle in str(ei.value)


# ---- reading 10 ------------------------------------------------------------ #
def test_r10_consent_renders_only_the_installing_hosts_section():
    """[reading 10] only the INSTALLING host's own section is rendered."""
    m = valid_manifest()
    m["hosts"]["gnomon"] = {"agents": ["zq_agent_token"]}
    seen = []

    def composeio_renderer(section):
        seen.append(section)
        return [HostPermission("composeio thing", "composeio:thing")]

    vm = validate(m)
    c = render_consent(vm, host="composeio", runs_as="inprocess",
                       render_host_section=composeio_renderer)
    print(c.text())
    assert seen == [{}]                                  # composeio's section only
    assert "zq_agent_token" not in c.text() and "gnomon" not in c.text()
    assert [g for g in c.granted if not g.startswith("bus.")] == ["composeio:thing"]


# ---- reading 11 ------------------------------------------------------------ #
def test_r11_store_is_append_only_jsonl_under_an_exclusive_lock(tmp_path, monkeypatch):
    """[reading 11] append-only JSON lines under an exclusive file lock."""
    import fcntl
    calls = []
    real = fcntl.flock
    monkeypatch.setattr(fcntl, "flock", lambda fd, op: (calls.append(op), real(fd, op))[1])
    path = tmp_path / "consent.jsonl"
    store = ConsentStore(path)
    vm = validate(valid_manifest())
    rec = lambda d: make_record(manifest=vm, manifest_sha256="a" * 64, granted=[],  # noqa: E731
                                decided_by="op", host="composeio", decision=d)
    store.append(rec("granted"))
    first = path.read_bytes()
    store.append(rec("refused"))
    after = path.read_bytes()
    assert after.startswith(first)                       # append, never rewrite
    lines = after.decode().splitlines()
    assert len(lines) == 2 and all(isinstance(json.loads(x), dict) for x in lines)
    assert calls == [fcntl.LOCK_EX, fcntl.LOCK_UN] * 2
    assert sys.platform in ("darwin", "linux")


# --------------------------------------------------------------------------- #
def test_every_reading_has_a_test():
    """Meta: each of readings 1..11 is cited by at least one test above."""
    mod = sys.modules[__name__]
    cited = set()
    for name in dir(mod):
        fn = getattr(mod, name)
        if name.startswith("test_") and name != "test_every_reading_has_a_test":
            cited |= {int(n) for n in re.findall(r"\[reading (\d+)\]", fn.__doc__ or "")}
    assert cited == set(READINGS), f"readings without a test: {set(READINGS) - cited}"
