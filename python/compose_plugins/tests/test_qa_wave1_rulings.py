"""Spec 95 r7 rulings: [CXC-PC-MANIFEST-FIELDS] qa-wave1-rulings (S1, G1, G2,
G3, N1, N3, N5) and [CXC-PC-CONSENT-WIDENING] code-is-a-permission. Each test
cites its ruling id in its docstring; each ruling has a positive and a negative
control."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata
import zipfile

import pytest

from compose_plugins.consent import code_grant, granted_set, render_consent, widens
from compose_plugins.errors import ManifestRefused, ManifestUnreadable
from compose_plugins.manifest import (
    FORMAT_RANGES, LINE_BREAK_RANGES, NO_PACKAGE, PATTERNS, SCHEMA_INEXPRESSIBLE,
    UNICODE_VERSION_FROZEN, WHITESPACE_RANGES, build_schema, render_schema_file,
    validate_manifest,
)
from compose_plugins.package import (
    MAX_MANIFEST_BYTES, MAX_NESTING_DEPTH, PackageSource, read_manifest,
)

from conftest import empty_section, valid_manifest
from test_manifest import BOUNDARY

jsonschema = pytest.importorskip("jsonschema")


def validate(m, host="composeio", package=NO_PACKAGE):
    return validate_manifest(m, host=host, section_validator=empty_section, package=package)


def refused(m, **kw):
    with pytest.raises(ManifestRefused) as ei:
        validate(m, **kw)
    print(ei.value)
    return ei.value


def _points(ranges):
    return {c for a, b in ranges for c in range(a, b + 1)}


# =========================================================================== #
# S1 \u2014 the schema agrees with the validator under ECMA-262 and Python re.
# =========================================================================== #
def _mut(path, value):
    def f(m):
        cur = m
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value
    return f


#: adversarial strings aimed at the dialect seams: a trailing newline before
#: `$`, Unicode digits for `\d`, whitespace that differs between `\s` sets,
#: Cf-only text, U+2028/U+2029, astral characters
ADVERSARIAL = [
    ("domain-trailing-nl", _mut(["domain"], "hello_world\n")),
    ("version-trailing-nl", _mut(["version"], "1.0.0\n")),
    ("version-arabic-indic-digit", _mut(["version"], "\u0661.0.0")),
    ("version-arabic-indic-digit-after-1", _mut(["version"], "1\u0661.0.0")),
    ("version-fullwidth-digit", _mut(["version"], "\uff11.0.0")),
    ("description-trailing-nl", _mut(["description"], "line\n")),
    ("description-u2028", _mut(["description"], "a\u2028b")),
    ("description-u2029", _mut(["description"], "a\u2029b")),
    ("description-nel", _mut(["description"], "a\u0085b")),
    ("description-vt", _mut(["description"], "a\u000bb")),
    ("description-fs", _mut(["description"], "a\u001cb")),
    ("description-cf-only", _mut(["description"], "\u200b\u200d\ufeff")),
    ("description-cf-and-space", _mut(["description"], " \u2060 ")),
    ("description-astral-cf-only", _mut(["description"], "\U000e0001\U000e0020")),
    ("description-astral-ok", _mut(["description"], "plug-in \U0001f600")),
    ("name-cf-only", _mut(["name"], "\u200b")),
    ("name-nbsp-only", _mut(["name"], "\u00a0\u3000")),
    ("name-x1f-only", _mut(["name"], "\u001f")),
    ("name-astral-only", _mut(["name"], "\U0001f600")),
    ("name-soft-hyphen-only", _mut(["name"], "\u00ad")),
    ("name-with-zwsp-ok", _mut(["name"], "Hello\u200bWorld")),
    ("documentation-trailing-nl", _mut(["documentation"], "https://x.invalid/d\n")),
    ("documentation-feff", _mut(["documentation"], "https://x.invalid/\ufeffd")),
    ("documentation-x1c", _mut(["documentation"], "https://x.invalid/\u001cd")),
    ("documentation-nel", _mut(["documentation"], "https://x.invalid/\u0085d")),
    ("documentation-nbsp", _mut(["documentation"], "https://x.invalid/\u00a0d")),
    ("issue-tracker-trailing-nl", _mut(["issue_tracker"], "https://x.invalid/i\n")),
    ("issue-tracker-upper-ok", _mut(["issue_tracker"], "HTTP://x.invalid/i")),
    ("codeowner-trailing-nl", _mut(["codeowners"], ["@a\n"])),
    ("pin-trailing-nl", _mut(["requirements"], ["requests==2.0\n"])),
    ("pin-arabic-digit", _mut(["requirements"], ["requests==\u0662.0"])),
    ("pin-dot-plugins", _mut(["requirements"], ["compose.plugins==0.1.0"])),
    ("pin-Compose..Bus-ok", _mut(["requirements"], ["Compose..Bus==0.1.0"])),
    ("entry-trailing-nl", _mut(["run", "entry"], "mod:Cls\n")),
    ("entry-unicode-letter", _mut(["run", "entry"], "m\u00f6d:Cls")),
    ("namespace-trailing-nl", _mut(["bus", "publishes", 0, "namespace"],
                                   "plugins/hello_world/greeting\n")),
    ("namespace-wildcard-star", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/*")),
    ("namespace-wildcard-hash", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/#")),
    ("namespace-wildcard-plus", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/+/x")),
    ("namespace-empty-segment", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world//x")),
    ("namespace-dot-segment", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/./x")),
    ("namespace-dotdot-segment", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/../x")),
    ("namespace-trailing-slash", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/x/")),
    ("namespace-leading-slash", _mut(["bus", "publishes", 0, "namespace"], "/plugins/hello_world/x")),
    ("namespace-space", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/a b")),
    ("namespace-unicode-segment", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/\u00e9")),
    ("namespace-deep-ok", _mut(["bus", "publishes", 0, "namespace"], "plugins/hello_world/a-B_9/c")),
    ("sq-trailing-nl-dotdot", _mut(["standard_questions"], "..\n")),
    ("sq-nl-inside", _mut(["standard_questions"], "a\n/../b")),
]


def _validator_accepts(m) -> bool:
    try:
        validate(m)
        return True
    except ManifestRefused:
        return False


#: boundary/adversarial cases whose refusal the schema cannot express \u2014 each
#: maps to one entry of SCHEMA_INEXPRESSIBLE
INEXPRESSIBLE_CASES = {
    "bus-namespace-other-domain": 0, "bus-namespace-domain-prefix-trick": 0,
    "hosts-own-section-invalid": 1, "standard-questions-no-package": 2,
    # shape-legal (its segment is "..\n", not ".."), refused only because no
    # package was given to check existence against
    "sq-trailing-nl-dotdot": 2,
    # r21 [CXC-PC-PERMISSIONS]: RFC 5952 canonical IPv6 (5), the 4096-byte path
    # limit (6), duplicate entries (7)
    "perm-target-ipv6-not-compressed": 5, "perm-target-ipv6-leading-zeros": 5,
    "perm-target-ipv6-tie-not-first-run": 5, "perm-target-ipv6-single-group-shortened": 5,
    "perm-target-ipv6-not-an-address": 5,
    "perm-path-4097-bytes": 6, "perm-path-over-4096-bytes-multibyte": 6,
    "perm-network-duplicate": 7, "perm-filesystem-duplicate-other-access": 7,
}


def test_s1_zero_disagreements_under_python_jsonschema():
    """[ruling S1] the adversarial set through Python jsonschema: the schema
    and the validator agree on every case except the documented inexpressible
    rules."""
    v = jsonschema.Draft202012Validator(build_schema())
    disagreements = []
    for cid, mutate in ADVERSARIAL + [(b[0], b[1]) for b in BOUNDARY] + [("valid", lambda m: None)]:
        m = valid_manifest()
        mutate(m)
        if _validator_accepts(m) != v.is_valid(m) and cid not in INEXPRESSIBLE_CASES:
            disagreements.append(cid)
    print(f"cases: {len(ADVERSARIAL) + len(BOUNDARY) + 1}; disagreements: {disagreements}")
    assert disagreements == []


def test_s1_inexpressible_cases_are_documented_in_the_schema_description():
    """[ruling S1] each rule the schema cannot express is named in its description."""
    desc = build_schema()["description"]
    for rule in SCHEMA_INEXPRESSIBLE:
        assert rule in desc
    assert set(INEXPRESSIBLE_CASES.values()) <= set(range(len(SCHEMA_INEXPRESSIBLE)))


def test_s1_negative_control_the_agreement_check_can_fail():
    """[ruling S1] negative control: a schema written with `$` and `\\d` (the
    first-wave dialect) DISAGREES on the adversarial set."""
    s = build_schema()
    s["properties"]["version"]["pattern"] = (
        r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    v = jsonschema.Draft202012Validator(s)
    bad = []
    for cid, mutate in ADVERSARIAL:
        m = valid_manifest()
        mutate(m)
        if _validator_accepts(m) != v.is_valid(m):
            bad.append(cid)
    print(bad)
    assert "version-trailing-nl" in bad and "version-arabic-indic-digit-after-1" in bad


def test_s1_patterns_use_only_the_dialect_neutral_subset():
    """[ruling S1] no `$`, `\\d`, `\\w`, bare `\\s`/`\\S` or `.` wildcard."""
    for rule, p in PATTERNS.items():
        body = p.replace(r"(?![\s\S])", "").replace(r"[\s\S]", "")
        assert "$" not in body, rule
        for esc in (r"\d", r"\w", r"\s", r"\S", r"\D", r"\W"):
            assert esc not in body, (rule, esc)
        assert not re.search(r"(?<!\\)\.(?![^\[]*\])", body.replace(r"\.", "")), (rule, p)


NODE = shutil.which("node")
NODE_SCRIPT = r"""
const data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = data.map(([p, s]) => new RegExp(p, 'u').test(s));
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="node (an ECMA-262 engine) is not installed")
def test_s1_every_pattern_means_the_same_in_ecma_262_and_python_re():
    """[ruling S1] each pattern run by node's RegExp (u flag) and by Python re
    on the same strings gives the same answer, string by string."""
    probes = ["", "a", "hello_world", "hello_world\n", "1.0.0", "1.0.0\n", "\u0661.0.0",
              "\uff11.0.0", "x\u2028y", "x\u2029y", "\u200b", "\ufeff", "\u001c", "\u0085",
              "\u00a0", "\u3000", "\U000e0001", "\U0001f600", "https://x.invalid/d",
              "https://x.invalid/d\n", "https://x.invalid/\ufeffd", "https://x.invalid/\u0085",
              "@a", "@a/b", "@a\n", "requests==2.0", "requests==2.0\n", "composebus==0.1.0",
              "compose.plugins==0.1.0", "Compose..Bus==0.1.0", "mod:Cls", "mod:Cls\n",
              "plugins/hello_world/x", "plugins/hello_world/x\n", "plugins/hello_world/*",
              "plugins/hello_world//x", "questions", "..", "a/..", "..\n", "a\n/../b",
              "C:x", "/abs", "\\abs", "line one", "line\none", " \u2060 "]
    keys = [(rule, probe) for rule in PATTERNS for probe in probes]
    pairs = [(PATTERNS[rule], probe) for rule, probe in keys]
    r = subprocess.run([NODE, "-e", NODE_SCRIPT], input=json.dumps(pairs),
                       capture_output=True, text=True, check=True)
    ecma = json.loads(r.stdout)
    assert len(ecma) == len(pairs)
    diffs = [key for key, (pat, probe), e in zip(keys, pairs, ecma)
             if (re.search(pat, probe) is not None) != e]
    print(f"pairs: {len(pairs)}; ECMA/Python disagreements: {diffs}")
    assert diffs == []


@pytest.mark.skipif(NODE is None, reason="node (an ECMA-262 engine) is not installed")
def test_s1_negative_control_node_does_see_the_dollar_difference():
    """[ruling S1] negative control: `^a$` on "a\\n" \u2014 Python re says yes,
    ECMA says no; the harness must report that."""
    r = subprocess.run([NODE, "-e", NODE_SCRIPT], input=json.dumps([["^a$", "a\n"]]),
                       capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == [False] and re.search("^a$", "a\n") is not None


def test_s1_shipped_file_is_exactly_the_generated_schema():
    """[ruling S1] define-once: the shipped bytes equal render_schema_file()."""
    from importlib import resources
    shipped = resources.files("compose_plugins.schema").joinpath(
        "manifest-0.1.schema.json").read_text(encoding="utf-8")
    assert shipped == render_schema_file()


def test_s1_frozen_unicode_sets_match_this_python_when_versions_agree():
    """[ruling S1/N3] the frozen whitespace, Cf and line-break sets equal what
    this Python computes, when its Unicode version is the frozen one."""
    if unicodedata.unidata_version != UNICODE_VERSION_FROZEN:
        pytest.skip(f"this Python has Unicode {unicodedata.unidata_version}, sets are "
                    f"frozen at {UNICODE_VERSION_FROZEN}")
    allc = range(0x110000)
    assert _points(WHITESPACE_RANGES) == {c for c in allc if chr(c).isspace()}
    assert _points(FORMAT_RANGES) == {c for c in allc if unicodedata.category(chr(c)) == "Cf"}
    assert _points(LINE_BREAK_RANGES) == {c for c in allc if len(f"a{chr(c)}b".splitlines()) > 1}


# =========================================================================== #
# G1 \u2014 finite numbers only.
# =========================================================================== #
@pytest.mark.parametrize("num", ["1e999", "-1e999", "1E400", "123456789e999"])
def test_g1_overflowing_numbers_are_refused(tmp_path, num):
    """[ruling G1] 1e999 (parses to inf) is refused by the reader."""
    m = json.dumps(valid_manifest()).replace('"type": "object"}', f'"type": "object", "maximum": {num}}}', 1)
    (tmp_path / "manifest.json").write_text(m)
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert "non-finite number" in str(ei.value) and num in str(ei.value)


def test_g1_positive_control_large_finite_numbers_pass(tmp_path):
    """[ruling G1] 1e308 and -1.5e-300 are finite and are read."""
    (tmp_path / "manifest.json").write_text('{"a": 1e308, "b": -1.5e-300, "c": 10}')
    assert read_manifest(tmp_path).data == {"a": 1e308, "b": -1.5e-300, "c": 10}


# =========================================================================== #
# G2 \u2014 case-exact path matching, directory == wheel.
# =========================================================================== #
def _sq_dir(tmp_path, ondisk):
    root = tmp_path / "pkg"
    (root / ondisk).mkdir(parents=True)
    return PackageSource(kind="directory", path=root)


def _sq_wheel(tmp_path, ondisk):
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as zf:
        zf.writestr("hw/manifest.json", "{}")
        zf.writestr(f"hw/{ondisk}/q.lts", "x")
    return PackageSource(kind="wheel", path=whl, import_package="hw")


@pytest.mark.parametrize("make", [_sq_dir, _sq_wheel], ids=["directory", "wheel"])
@pytest.mark.parametrize("ask,ondisk,ok", [("sq", "sq", True), ("SQ", "sq", False),
                                           ("Sq/Inner", "sq/inner", False),
                                           ("sq/inner", "sq/inner", True)])
def test_g2_case_exact_on_every_filesystem(tmp_path, make, ask, ondisk, ok):
    """[ruling G2] 'SQ' does not match a directory named 'sq', for a
    directory AND a wheel (one answer per package, whatever the filesystem)."""
    assert make(tmp_path, ondisk).has_directory(ask) is ok


def test_g2_manifest_name_is_case_exact(tmp_path):
    """[ruling G2] a file named MANIFEST.JSON is not manifest.json."""
    (tmp_path / "MANIFEST.JSON").write_text("{}")
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert "case-sensitively" in str(ei.value)


# =========================================================================== #
# G3 \u2014 exact-topic namespaces.
# =========================================================================== #
@pytest.mark.parametrize("ns", [
    "plugins/hello_world/*", "plugins/hello_world/#", "plugins/hello_world/+",
    "plugins/hello_world/a/*", "plugins/hello_world//x", "plugins/hello_world/.",
    "plugins/hello_world/./x", "plugins/hello_world/../x", "plugins/hello_world/x/",
    "/plugins/hello_world/x", "plugins/hello_world/a b", "plugins/hello_world/a\tb",
    "plugins/hello_world/a\x00b", "plugins/hello_world/\u00e9", "plugins/hello_world/a.b",
    "plugins/hello_world/x\n",
])
def test_g3_non_exact_topics_are_refused(ns):
    """[ruling G3] segments [A-Za-z0-9_-]+, single '/', no wildcards, no dot segments."""
    m = valid_manifest()
    m["bus"]["publishes"][0]["namespace"] = ns
    exc = refused(m)
    assert exc.fields == ["bus.publishes[0].namespace"]
    assert "exact topic" in exc.refusals[0].message


@pytest.mark.parametrize("ns", ["plugins/hello_world/a", "plugins/hello_world/A-b_9/c/D"])
def test_g3_positive_control_and_composebus_accepts_too(ns):
    """[ruling G3] exact topics pass, and composebus validate_pattern accepts them."""
    from composebus.bus import validate_pattern
    m = valid_manifest()
    m["bus"]["publishes"][0]["namespace"] = ns
    validate(m)
    validate_pattern(ns)


def test_g3_composebus_validate_pattern_is_consulted(monkeypatch):
    """[ruling G3] negative control: if composebus refused a topic, so do we."""
    import compose_plugins.manifest as mod

    def strict(p):
        raise ValueError("composebus says no")
    monkeypatch.setattr(mod, "validate_pattern", strict)
    assert refused(valid_manifest()).fields[0].startswith("bus.publishes[0].namespace")


# =========================================================================== #
# N1 \u2014 256 KiB and depth 64, both named.
# =========================================================================== #
def _exact(size: int) -> bytes:
    raw = b'{"pad": "' + b"p" * (size - 11) + b'"}'
    assert len(raw) == size
    return raw


def test_n1_exactly_256_kib_is_read_and_one_byte_more_is_refused(tmp_path):
    """[ruling N1] at most 256 KiB on disk."""
    (tmp_path / "manifest.json").write_bytes(_exact(MAX_MANIFEST_BYTES))
    read_manifest(tmp_path)
    (tmp_path / "manifest.json").write_bytes(_exact(MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert "too large" in str(ei.value) and str(MAX_MANIFEST_BYTES + 1) in str(ei.value)


def test_n1_wheel_limit_is_on_decompressed_size(tmp_path):
    """[ruling N1] a small compressed member that inflates past 256 KiB is refused."""
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("hw/manifest.json", _exact(MAX_MANIFEST_BYTES + 1))
    assert whl.stat().st_size < 10_000
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(whl)
    print(ei.value)
    assert "too large" in str(ei.value)


def test_n1_bytes_limit_applies_in_parse_too():
    """[ruling N1] parse_manifest_bytes refuses oversize bytes on its own."""
    from compose_plugins.package import parse_manifest_bytes
    with pytest.raises(ManifestUnreadable, match="too large"):
        parse_manifest_bytes(_exact(MAX_MANIFEST_BYTES + 1), location="x")
    parse_manifest_bytes(_exact(MAX_MANIFEST_BYTES), location="x")


@pytest.mark.parametrize("opener", ["[", '{"a":'])
def test_n1_depth_64_is_read_and_65_is_refused_by_name(tmp_path, opener):
    """[ruling N1] nesting deeper than 64 is a NAMED refusal, never RecursionError."""
    closer = "]" if opener == "[" else "}"
    ok = opener * MAX_NESTING_DEPTH + "1" + closer * MAX_NESTING_DEPTH
    (tmp_path / "manifest.json").write_text(ok)
    read_manifest(tmp_path)
    bad = opener * (MAX_NESTING_DEPTH + 1) + "1" + closer * (MAX_NESTING_DEPTH + 1)
    (tmp_path / "manifest.json").write_text(bad)
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    print(ei.value)
    assert "nests deeper than 64" in str(ei.value)


def test_n1_hostile_depth_inside_the_size_limit_is_named_not_recursionerror(tmp_path):
    """[ruling N1] 100k levels within 256 KiB: a named refusal."""
    (tmp_path / "manifest.json").write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(tmp_path)
    assert "nests deeper than 64" in str(ei.value)


def test_n1_brackets_inside_strings_do_not_count(tmp_path):
    """[ruling N1] positive control: '[' and '{' inside a string are not nesting."""
    (tmp_path / "manifest.json").write_text(json.dumps({"s": "[" * 500 + '\\"{' * 100}))
    read_manifest(tmp_path)


# =========================================================================== #
# N3 \u2014 blank and one-line.
# =========================================================================== #
@pytest.mark.parametrize("field", ["name", "description"])
@pytest.mark.parametrize("value", ["\u200b", "\ufeff", "\u200b \u2060", "\u00ad", "\u3000\u00a0",
                                   "\U000e0001", "\u001f"])
def test_n3_whitespace_or_cf_only_counts_as_empty(field, value):
    """[ruling N3] non-empty = at least one character that is neither
    whitespace nor Unicode format (Cf)."""
    m = valid_manifest()
    m[field] = value
    assert refused(m).fields == [field]


@pytest.mark.parametrize("sep", ["\n", "\r", "\u000b", "\u000c", "\u001c", "\u001d", "\u001e",
                                 "\u0085", "\u2028", "\u2029"])
def test_n3_one_line_forbids_every_separator(sep):
    """[ruling N3] one line forbids every line and paragraph separator."""
    m = valid_manifest()
    m["description"] = f"one{sep}two"
    exc = refused(m)
    assert exc.fields == ["description"] and "ONE line" in exc.refusals[0].message


def test_n3_positive_control():
    """[ruling N3] a visible character among Cf/whitespace is non-empty; tabs are one line."""
    m = valid_manifest()
    m["name"] = "\u200bX\u200b"
    m["description"] = "tab\tseparated, still one line"
    validate(m)


# =========================================================================== #
# N5 \u2014 a symlinked manifest.json is refused.
# =========================================================================== #
def test_n5_symlinked_manifest_in_a_directory_is_refused(tmp_path):
    """[ruling N5] manifest.json that is a symlink (even to a file inside) is refused."""
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "real.json").write_text(json.dumps(valid_manifest()))
    (root / "manifest.json").symlink_to(root / "real.json")
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(root)
    print(ei.value)
    assert "is a symlink; manifest.json must be a regular file" in str(ei.value)


def test_n5_symlink_entry_in_a_wheel_is_refused(tmp_path):
    """[ruling N5] a wheel member stored as a symlink is refused."""
    whl = tmp_path / "hw-1.0.0-py3-none-any.whl"
    info = zipfile.ZipInfo("hw/manifest.json")
    info.external_attr = (0o120777 << 16)
    with zipfile.ZipFile(whl, "w") as zf:
        # valid JSON on purpose: only the symlink guard can refuse it
        zf.writestr(info, "{}")
    with pytest.raises(ManifestUnreadable) as ei:
        read_manifest(whl)
    print(ei.value)
    assert "is a symlink entry" in str(ei.value)


def test_n5_positive_control_regular_file(tmp_path):
    """[ruling N5] a regular manifest.json is read."""
    (tmp_path / "manifest.json").write_text("{}")
    assert read_manifest(tmp_path).data == {}


# =========================================================================== #
# code-is-a-permission.
# =========================================================================== #
def _v(m):
    return validate(m)


def _g(m):
    return granted_set(_v(m), host="composeio", render_host_section=lambda s: [])


def test_code_pins_enter_granted_with_pep503_names():
    """[ruling code-is-a-permission] code:<pep503-name>==<version>."""
    m = valid_manifest()
    m["requirements"] = ["Requests_OAuthlib==2.0.0", "idna==3.7"]
    g = _g(m)
    assert "code:requests-oauthlib==2.0.0" in g and "code:idna==3.7" in g
    assert code_grant("My.Pkg__x==1.0") == "code:my-pkg-x==1.0"


@pytest.mark.parametrize("old,new,widen", [
    ([], ["idna==3.7"], True),                         # adds a pin
    (["idna==3.6"], ["idna==3.7"], True),              # changes a pin
    (["idna==3.7"], ["idna==3.7"], False),             # same
    (["idna==3.7", "six==1.16.0"], ["idna==3.7"], False),   # removes one
    (["Idna==3.7"], ["idna==3.7"], False),             # PEP 503 same name
])
def test_code_pin_changes_widen(old, new, widen):
    """[ruling code-is-a-permission] adding or changing a pin widens."""
    m1, m2 = valid_manifest(), valid_manifest()
    m1["requirements"], m2["requirements"] = old, new
    assert widens(_g(m1), _g(m2)) is widen


def test_code_changed_pin_is_marked_on_the_reconsent_screen():
    """[ruling code-is-a-permission] the re-consent screen marks the changed pin."""
    m1, m2 = valid_manifest(), valid_manifest()
    m1["requirements"], m2["requirements"] = ["idna==3.6"], ["idna==3.7"]
    c = render_consent(_v(m2), host="composeio", runs_as="inprocess",
                       render_host_section=lambda s: [], previous_granted=_g(m1))
    print(c.text())
    assert c.widens and c.added == ("code:idna==3.7",)
    assert [ln.text() for ln in c.lines if ln.added] == ["[ADDED] code this installs: idna==3.7"]


def test_code_raising_isolation_floor_does_not_widen_and_runs_as_is_shown():
    """[ruling code-is-a-permission] more isolation weakens nothing: no widening;
    the new runs-as level is on the screen."""
    m1, m2 = valid_manifest(), valid_manifest()
    m2["isolation_floor"] = "subprocess"
    c = render_consent(_v(m2), host="composeio", runs_as="subprocess",
                       render_host_section=lambda s: [], previous_granted=_g(m1))
    assert c.widens is False and c.added == ()
    assert ("runs as", "subprocess") in [(ln.label, ln.value) for ln in c.lines]


# =========================================================================== #
# Packaging: compose_plugins.schema is a declared package.
# =========================================================================== #
def test_packaging_schema_is_a_declared_package():
    """[packaging] pyproject declares compose_plugins.schema and its data."""
    tomllib = pytest.importorskip("tomllib")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "pyproject.toml"), "rb") as fh:
        cfg = tomllib.load(fh)["tool"]["setuptools"]
    assert "compose_plugins.schema" in cfg["packages"]
    assert cfg["package-data"]["compose_plugins.schema"] == ["*.json"]
    assert sys.modules.get("compose_plugins.schema") or __import__("compose_plugins.schema")
