"""Spec 95 r21: declaration-only `permissions` ([CXC-PC-PERMISSIONS],
[CXC-PC-NC-MALFORMED-PERMISSION]), the consent dialog title and permission
lines ([CXC-PC-CONSENT-CONTENT] dialog-title, permission-lines), the two
`permission.` grant strings ([CXC-PC-CONSENT-RECORD] granted-format), their
widening ([CXC-PC-CONSENT-WIDENING] permissions-widen), the unreserving of
network and filesystem permissions ([CXC-PC-RESERVED-NAMES] r21-unreserved),
and the never-verified rule: NO host verifies, sandboxes or tests a declared
permission, so the library exposes no API that could.

The per-field refusals are rows of test_manifest.PERMISSION_BOUNDARY (so the
schema-agreement test in test_schema_file.py runs them too)."""
from __future__ import annotations

import ast
import copy
import io
import json
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import compose_plugins
from compose_plugins import conformance, consent as consent_mod, manifest as manifest_mod
from compose_plugins.consent import (
    PERMISSIONS_HEADING, ConsentRecord, added_grants, dialog_title, grant_problem,
    granted_set, render_consent, upgrade_widens, widens,
)
from compose_plugins.errors import ManifestRefused
from compose_plugins.fixtures import package_dir
from compose_plugins.manifest import (
    HA_OTHER_NAMES_RESERVED, HA_RESERVED_KEYS, NO_PACKAGE, OPTIONAL_FIELDS,
    OTHER_RESERVED_KEYS, PATTERNS, build_schema, ipv6_rfc5952, validate_manifest,
)

from conftest import empty_section, valid_manifest
from test_manifest import VALID_PERMISSIONS
import wave2_helpers as h

jsonschema = pytest.importorskip("jsonschema")

LIB = Path(compose_plugins.__file__).parent
TITLE_SUFFIX = "  Manifest: Declared by its Developer"


def v(m, host="composeio"):
    return validate_manifest(m, host=host, section_validator=empty_section, package=NO_PACKAGE)


def with_perms(perms=None):
    m = valid_manifest()
    m["permissions"] = copy.deepcopy(VALID_PERMISSIONS if perms is None else perms)
    return m


def no_perms(section):
    return []


def schema_ok(m) -> bool:
    return jsonschema.Draft202012Validator(build_schema()).is_valid(m)


def validator_ok(m) -> bool:
    try:
        v(m)
        return True
    except ManifestRefused:
        return False


# =========================================================================== #
# Positive controls: what installs.
# =========================================================================== #
@pytest.mark.parametrize("perms", [
    None, {"network": [], "filesystem": []}, {"network": []}, {"filesystem": []}, {},
], ids=["absent", "both-empty", "network-only-empty", "filesystem-only-empty", "empty-object"])
def test_no_permissions_or_empty_lists_install(perms):
    """[CXC-PC-PERMISSIONS] default + boundary-case: absent, or both lists
    empty, installs (validator and schema agree)."""
    m = valid_manifest()
    if perms is not None:
        m["permissions"] = perms
    assert validator_ok(m) and schema_ok(m)
    assert v(m).network_permissions == [] and v(m).filesystem_permissions == []


def test_permissions_is_an_optional_core_field():
    assert "permissions" in OPTIONAL_FIELDS
    assert "permissions" in build_schema()["properties"]
    assert "permissions" not in build_schema()["required"]


GOOD_TARGETS = [
    "any", "printer.local", "printer.local:631", "localhost", "a", "a-b.c-d",
    "printer.local:1", "printer.local:65535", "mqtt-broker.local", "mqtt-broker.local:1883",
    "0.0.0.0", "255.255.255.255", "[::1]", "[::1]:8080", "[::]", "[2001:db8::1]",
    "[fe80::]", "[2001:db8:0:1:1:1:1:1]", "[2001:db8::1:0:0:1]", "[::ffff:c000:201]",
    "a" * 63 + ".local", ".".join(["a" * 63] * 3 + ["a" * 61]),
    "anything.local", "any.local:80",
]
BAD_TARGETS_BOTH = [
    "*.example.com", "any:443", "Printer.Local", "printer.local:0", "printer.local:65536",
    "printer.local:0631", "", ".example.com", "printer.local.", "printer..local",
    "-printer.local", "printer-.local", "a" * 64, ".".join(["a" * 63] * 3 + ["a" * 62]),
    "?", "my_printer", "::1", "[2001:DB8::1]", "[::ffff:192.0.2.1]", "[::1", "[::1]:0",
    "printer.local:", "any:", " printer.local", "printer.local\n", "ANY",
]
#: refused by the validator only: RFC 5952 canonicality (SCHEMA_INEXPRESSIBLE)
BAD_TARGETS_VALIDATOR_ONLY = [
    "[2001:db8:0:0:0:0:0:1]", "[2001:0db8::1]", "[2001:db8:0:0:1::1]",
    "[2001:db8::1:1:1:1:1]", "[1:2:3:4:5:6:7:8:9]", "[:::]",
]


def _net_manifest(target):
    return with_perms({"network": [{"direction": "inbound", "target": target,
                                    "purpose": "listen"}]})


@pytest.mark.parametrize("target", GOOD_TARGETS)
def test_valid_targets_install_in_validator_and_schema(target):
    m = _net_manifest(target)
    assert validator_ok(m), target
    assert schema_ok(m), target


@pytest.mark.parametrize("target", BAD_TARGETS_BOTH)
def test_invalid_targets_refused_by_validator_and_schema(target):
    m = _net_manifest(target)
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert ei.value.fields == ["permissions.network[0].target"]
    assert not schema_ok(m), target


@pytest.mark.parametrize("target", BAD_TARGETS_VALIDATOR_ONLY)
def test_non_canonical_ipv6_refused_by_validator_only(target):
    m = _net_manifest(target)
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert ei.value.fields == ["permissions.network[0].target"]
    assert schema_ok(m), "schema now catches it: move it out of SCHEMA_INEXPRESSIBLE"


@pytest.mark.parametrize("target,ok", [
    ("010.0.0.1", False), ("256.1.1.1", False), ("printer.123", False),
    ("printer.local", True), ("198.51.100.10", True),
    # around the rule
    ("1", False), ("1.2.3", False), ("1.2.3.4.5", False), ("198.51.100.10:8080", True),
    ("010.0.0.1:80", False), ("printer.123:631", False), ("123.printer", True),
    ("printer.1a", True), ("printer.a1", True), ("printer.1-2", True),
    ("203.0.113.01", False), ("255.255.255.256", False),
])
def test_r22_final_label_not_all_numeric(target, ok):
    """Architect ruling recorded in r22 (RFC 3696 section 2): a host name's
    FINAL label is not all-numeric, so digits-and-dots is accepted only as a
    canonical dotted-quad IPv4. Validator and schema agree on every case."""
    m = _net_manifest(target)
    assert validator_ok(m) is ok, target
    assert schema_ok(m) is ok, target


@pytest.mark.parametrize("addr,canon", [
    ("::1", "::1"), ("0:0:0:0:0:0:0:1", "::1"), ("2001:0db8:0000:0000:0000:0000:0000:0001",
                                                 "2001:db8::1"),
    ("2001:db8:0:0:1:0:0:1", "2001:db8::1:0:0:1"), ("2001:db8:0:1:1:1:1:1",
                                                    "2001:db8:0:1:1:1:1:1"),
    ("::", "::"), ("fe80::", "fe80::"), ("::ffff:192.0.2.1", "::ffff:c000:201"),
    ("not-an-address", None), ("fe80::1%en0", None),
])
def test_rfc5952_canonicalizer(addr, canon):
    assert ipv6_rfc5952(addr) == canon


GOOD_PATHS = ["/", "/tmp/hello", "/a b/c", "/a..b", "/.hidden", "/a]b", "/tmp/a.b",
              "/" + "a" * 4095, "/" + "\u00e9" * 2047, "/Users/you/My Files"]
BAD_PATHS_BOTH = ["data/x", "/tmp/../etc", "/..", "/.", "/tmp/./x", "//", "/tmp//x",
                  "~/x", "/~", "/tmp/*", "/tmp/?", "/tmp/[", "/tmp/x/", "", "/a\x00",
                  "/a\x1f", "/a\x7f", "/a\x9f", "/a\nb", "C:\\tmp"]
BAD_PATHS_VALIDATOR_ONLY = ["/" + "a" * 4096, "/" + "\u00e9" * 2048]


def _fs_manifest(path, access="read"):
    return with_perms({"filesystem": [{"path": path, "access": access, "purpose": "x"}]})


@pytest.mark.parametrize("path", GOOD_PATHS)
def test_valid_paths_install(path):
    m = _fs_manifest(path)
    assert validator_ok(m) and schema_ok(m)


@pytest.mark.parametrize("path", BAD_PATHS_BOTH)
def test_invalid_paths_refused_by_validator_and_schema(path):
    m = _fs_manifest(path)
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert ei.value.fields == ["permissions.filesystem[0].path"]
    assert not schema_ok(m)


@pytest.mark.parametrize("path", BAD_PATHS_VALIDATOR_ONLY)
def test_path_over_4096_utf8_bytes_refused_by_validator_only(path):
    m = _fs_manifest(path)
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert "4096 bytes" in ei.value.refusals[0].message
    assert schema_ok(m)


def test_purpose_of_200_code_points_installs_201_does_not():
    ok = with_perms({"network": [{"direction": "outbound", "target": "any",
                                  "purpose": "\u00e9" * 200}]})
    assert validator_ok(ok) and schema_ok(ok)
    bad = copy.deepcopy(ok)
    bad["permissions"]["network"][0]["purpose"] = "\u00e9" * 201
    assert not validator_ok(bad) and not schema_ok(bad)


def test_same_target_with_other_direction_is_not_a_duplicate():
    m = with_perms({"network": [
        {"direction": "outbound", "target": "printer.local", "purpose": "a"},
        {"direction": "inbound", "target": "printer.local", "purpose": "b"}]})
    assert validator_ok(m)


def test_all_permission_refusals_are_reported_together():
    m = with_perms({"camera": [], "network": [{"direction": "both", "target": "*",
                                               "purpose": ""}]})
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert sorted(ei.value.fields) == sorted([
        "permissions.camera", "permissions.network[0].direction",
        "permissions.network[0].target", "permissions.network[0].purpose"])


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node (an ECMA-262 engine) is not installed")
@pytest.mark.parametrize("rule,samples", [
    ("permission_target", GOOD_TARGETS + BAD_TARGETS_BOTH + BAD_TARGETS_VALIDATOR_ONLY
     + ["010.0.0.1", "256.1.1.1", "printer.123", "198.51.100.10", "1", "printer.1a"]),
    ("permission_path", GOOD_PATHS + BAD_PATHS_BOTH),
    ("permission_purpose", ["x", " ", "", "a\nb", "a\u2028b", "\u200b", "ok words"]),
])
def test_r21_patterns_mean_the_same_in_ecma_262(rule, samples):
    """[validator-readings (15)] each new pattern gives the same answer under
    node's RegExp(u) and Python re."""
    pat = PATTERNS[rule]
    script = ("const d=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(d.s.map(x=>new RegExp(d.p,'u').test(x))))")
    r = subprocess.run([NODE, "-e", script], input=json.dumps({"p": pat, "s": samples}),
                       capture_output=True, text=True, check=True)
    assert json.loads(r.stdout) == [re.search(pat, x) is not None for x in samples]


# =========================================================================== #
# Reserved names: network and filesystem permissions are no longer reserved.
# =========================================================================== #
def test_r21_permissions_left_the_reserved_names():
    reserved = set(HA_RESERVED_KEYS) | set(HA_OTHER_NAMES_RESERVED) | set(OTHER_RESERVED_KEYS)
    assert not {"permissions", "network", "filesystem"} & reserved
    assert validator_ok(with_perms())


@pytest.mark.parametrize("key", ["network", "filesystem"])
def test_top_level_network_or_filesystem_is_an_unknown_key_not_a_reserved_one(key):
    m = valid_manifest()
    m[key] = []
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert ei.value.fields == [key]
    msg = ei.value.refusals[0].message
    assert "unknown key" in msg and "reserved" not in msg


def test_other_reserved_items_stay_reserved():
    """D2: upgrade, process/container run forms and the bus selector form stay."""
    for edit, field in ((lambda m: m.update(upgrade={}), "upgrade"),
                        (lambda m: m["run"].update(form="process"), "run.form"),
                        (lambda m: m["run"].update(form="container"), "run.form"),
                        (lambda m: m["bus"].update(read=[]), "bus.read")):
        m = valid_manifest()
        edit(m)
        with pytest.raises(ManifestRefused) as ei:
            v(m)
        assert ei.value.fields == [field] and "reserved" in ei.value.refusals[0].message


# =========================================================================== #
# Consent content: the dialog title and the permission lines.
# =========================================================================== #
def _fixture_manifest(version="v1"):
    data = json.loads((package_dir(version) / "manifest.json").read_text())
    return v(data)


@pytest.mark.parametrize("version,expected", [
    ("v1", "Hello World 1.0.0  Manifest: Declared by its Developer"),
    ("v2", "Hello World 2.0.0  Manifest: Declared by its Developer"),
])
def test_dialog_title_from_the_hello_world_manifest(version, expected):
    """[CXC-PC-CONSENT-CONTENT] dialog-title + verify-machine: built from the
    manifest's name and version; TWO spaces before 'Manifest'."""
    m = _fixture_manifest(version)
    c = render_consent(m, host="composeio", runs_as="inprocess", render_host_section=no_perms)
    assert c.title == expected == dialog_title(m)
    assert c.title == f"{m.data['name']} {m.data['version']}{TITLE_SUFFIX}"
    assert c.text().splitlines()[0] == expected


def test_dialog_title_follows_the_manifest_not_a_constant():
    m = valid_manifest()
    m["name"], m["version"] = "Print Services", "0.3.1-rc.1"
    c = render_consent(v(m), host="composeio", runs_as="inprocess", render_host_section=no_perms)
    assert c.title == "Print Services 0.3.1-rc.1  Manifest: Declared by its Developer"


def test_hello_world_v1_shows_no_permissions_heading_and_no_permission_line():
    c = render_consent(_fixture_manifest("v1"), host="composeio", runs_as="inprocess",
                       render_host_section=no_perms)
    assert PERMISSIONS_HEADING not in c.text()
    assert [ln for ln in c.lines if ln.kind != "field"] == []
    assert not any(g.startswith("permission.") for g in c.granted)


def test_one_network_and_one_filesystem_entry_render_under_one_heading():
    """[CXC-PC-CONSENT-CONTENT] verify-machine: the heading exactly once, then
    exactly two permission lines in the permission-lines format, no per-line mark."""
    c = render_consent(v(with_perms()), host="composeio", runs_as="inprocess",
                       render_host_section=no_perms)
    text = c.text()
    print(text)
    assert text.count(PERMISSIONS_HEADING) == 1
    kinds = [ln.kind for ln in c.lines]
    i = kinds.index("heading")
    assert kinds.count("heading") == 1
    assert kinds[i + 1:i + 3] == ["permission", "permission"]
    assert kinds.count("permission") == 2
    perm_text = [ln.text() for ln in c.lines if ln.kind == "permission"]
    assert perm_text == [
        "outbound printer.local:631: print the greeting on the office printer (IPP)",
        "read-write /tmp/hello: keep a log of greetings sent",
    ]
    for t in perm_text:
        assert "not enforced" not in t and "[ADDED]" not in t and "declared" not in t
    assert c.lines[i].text() == "Declared by the plug-in; not checked"
    # placed after the bus lines, before the host-section permissions
    labels = [ln.label for ln in c.lines]
    assert labels.index("subscribes") < i < labels.index("composeio permissions")


def test_only_network_or_only_filesystem_still_one_heading():
    for perms in ({"network": VALID_PERMISSIONS["network"]},
                  {"filesystem": VALID_PERMISSIONS["filesystem"], "network": []}):
        c = render_consent(v(with_perms(perms)), host="composeio", runs_as="inprocess",
                           render_host_section=no_perms)
        assert c.text().count(PERMISSIONS_HEADING) == 1
        assert [ln.kind for ln in c.lines].count("permission") == 1


def test_rendering_permissions_makes_no_network_call_and_imports_nothing():
    sys.addaudithook(conformance._audit)
    before = set(sys.modules)
    conformance._NET_EVENTS.clear()
    conformance._NET_WATCH[0] = True
    try:
        render_consent(v(with_perms()), host="composeio", runs_as="inprocess",
                       render_host_section=no_perms)
    finally:
        conformance._NET_WATCH[0] = False
    assert conformance._NET_EVENTS == []
    assert not any(n.startswith("hello_world") for n in set(sys.modules) - before)


# =========================================================================== #
# Grant strings and the consent record.
# =========================================================================== #
def test_granted_holds_the_two_permission_strings_and_no_purpose():
    g = granted_set(v(with_perms()), host="composeio", render_host_section=no_perms)
    assert "permission.network:outbound:printer.local:631" in g
    assert "permission.filesystem:read-write:/tmp/hello" in g
    assert not any("greeting" in s and s.startswith("permission.") for s in g)
    assert g == sorted(set(g))


def test_r21_verify_machine_install_shows_lines_and_records_grants(tmp_path):
    """[CXC-PC-PERMISSIONS] verify-machine: the valid manifest installs, its
    consent content shows both lines with their purposes, and the consent
    record's granted holds both permission strings."""
    host, pm = h.make(tmp_path)
    src = h.fixture_copy(tmp_path, edit=lambda d: d.update(permissions=copy.deepcopy(
        VALID_PERMISSIONS)))
    p = pm.prepare_install(src)
    text = p.content.text()
    assert "outbound printer.local:631: print the greeting on the office printer (IPP)" in text
    assert "read-write /tmp/hello: keep a log of greetings sent" in text
    pm.install(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert pm.state("hello_world") == "installed"
    rec = pm.consent.records()[-1]
    assert rec.decision == "granted"
    assert "permission.network:outbound:printer.local:631" in rec.granted
    assert "permission.filesystem:read-write:/tmp/hello" in rec.granted
    # the stored record re-reads (grant_problem accepts both forms)
    assert ConsentRecord.from_json(rec.to_json()) == rec


@pytest.mark.parametrize("grant", [
    "permission.network:outbound:printer.local:631", "permission.network:inbound:any",
    "permission.network:outbound:[::1]:8080", "permission.filesystem:read:/",
    "permission.filesystem:read-write:/tmp/a:b",
])
def test_grant_problem_accepts_the_permission_forms(grant):
    assert grant_problem(grant, "composeio") is None


@pytest.mark.parametrize("grant", [
    "permission.network:both:any", "permission.network:outbound:*.example.com",
    "permission.network:outbound", "permission.network:", "permission.filesystem:write:/tmp",
    "permission.filesystem:read:tmp", "permission.filesystem:read", "permission.camera:on",
    "permission:x", "permission.network:outbound:[2001:db8:0:0:0:0:0:1]",
])
def test_grant_problem_refuses_malformed_permission_grants(grant):
    assert grant_problem(grant, "composeio") is not None


def test_host_named_permission_is_refused_in_a_manifest():
    m = valid_manifest()
    m["hosts"]["permission"] = {}
    with pytest.raises(ManifestRefused) as ei:
        v(m)
    assert ei.value.fields == ["hosts.permission"]
    assert not schema_ok(m)


# =========================================================================== #
# Widening ([CXC-PC-CONSENT-WIDENING] permissions-widen + verify-machine).
# =========================================================================== #
N631 = {"direction": "outbound", "target": "printer.local:631", "purpose": "print via IPP"}
N9100 = {"direction": "outbound", "target": "printer.local:9100", "purpose": "raw printing"}
FS_R = {"path": "/tmp/hello", "access": "read", "purpose": "read the log"}
FS_RW = {"path": "/tmp/hello", "access": "read-write", "purpose": "keep the log"}

WIDEN_CASES = [
    # id, v1 permissions, v2 permissions, re-consent?, exactly-added
    ("adds-outbound-9100", {"network": [N631]}, {"network": [N631, N9100]}, True,
     ["permission.network:outbound:printer.local:9100"]),
    ("purpose-only-reworded", {"network": [N631]},
     {"network": [dict(N631, purpose="print the greeting, now in other words")]}, False, []),
    ("read-to-read-write", {"filesystem": [FS_R]}, {"filesystem": [FS_RW]}, True,
     ["permission.filesystem:read-write:/tmp/hello"]),
    ("read-write-to-read", {"filesystem": [FS_RW]}, {"filesystem": [FS_R]}, False, []),
    ("removes-an-entry", {"network": [N631, N9100]}, {"network": [N631]}, False, []),
    ("new-direction", {"network": [N631]},
     {"network": [dict(N631, direction="inbound")]}, True,
     ["permission.network:inbound:printer.local:631"]),
    ("new-path", {"filesystem": [FS_R]}, {"filesystem": [dict(FS_R, path="/tmp/other")]}, True,
     ["permission.filesystem:read:/tmp/other"]),
    ("adds-first-permission", None, {"filesystem": [FS_R]}, True,
     ["permission.filesystem:read:/tmp/hello"]),
]


def _grants(perms):
    m = valid_manifest()
    if perms is not None:
        m["permissions"] = copy.deepcopy(perms)
    return granted_set(v(m), host="composeio", render_host_section=no_perms)


@pytest.mark.parametrize("cid,old,new,reconsent,added", WIDEN_CASES,
                         ids=[c[0] for c in WIDEN_CASES])
def test_widening_rule_on_grant_sets(cid, old, new, reconsent, added):
    g_old, g_new = _grants(old), _grants(new)
    assert widens(g_old, g_new) is reconsent
    assert upgrade_widens(g_old, g_new, "inprocess", "inprocess") is reconsent
    assert added_grants(g_old, g_new) == added


def test_read_does_not_cover_read_write_and_covering_is_per_path():
    rw, r = ["permission.filesystem:read-write:/a"], ["permission.filesystem:read:/a"]
    assert widens(r, rw) and not widens(rw, r)
    assert widens(rw, ["permission.filesystem:read:/b"])
    assert widens(["permission.filesystem:read-write:/a/b"], r)   # no prefix covering


@pytest.mark.parametrize("cid,old,new,reconsent,added", WIDEN_CASES,
                         ids=[c[0] for c in WIDEN_CASES])
def test_upgrade_presents_reconsent_exactly_when_it_widens(tmp_path, cid, old, new,
                                                            reconsent, added):
    """The verify-machine cases through PluginManager: re-consent means the
    host's consent gate is asked, showing exactly the added grants."""
    def edit(perms, version):
        def e(d):
            d["version"] = version
            if perms is not None:
                d["permissions"] = copy.deepcopy(perms)
        return e
    host, pm = h.make(tmp_path)
    h.install(pm, h.fixture_copy(tmp_path, edit=edit(old, "1.0.0"), name="pkg-old"))
    calls = len(host.gate_calls)
    src = h.fixture_copy(tmp_path, edit=edit(new, "1.1.0"), name="pkg-new")
    p = pm.prepare_upgrade(src)
    assert p.content.widens is reconsent
    assert list(p.content.added) == added
    assert [ln.grant for ln in p.content.lines if ln.added] == added
    pm.upgrade(src, consented_sha256=p.manifest_sha256, operator="op", accepted=True)
    assert (len(host.gate_calls) > calls) is reconsent
    rec = pm.record("hello_world")
    assert rec["version"] == "1.1.0"
    assert rec["granted"] == _grants(new)        # a narrowing records the narrower set
    if cid == "purpose-only-reworded":
        assert "now in other words" in p.content.text()   # the new purpose is shown


def test_refused_permission_widening_keeps_v1(tmp_path):
    host, pm = h.make(tmp_path)
    h.install(pm, h.fixture_copy(tmp_path, edit=lambda d: d.update(
        permissions={"network": [N631]}), name="pkg-old"))
    src = h.fixture_copy(tmp_path, edit=lambda d: d.update(
        version="1.1.0", permissions={"network": [N631, N9100]}), name="pkg-new")
    p = pm.prepare_upgrade(src)
    from compose_plugins.lifecycle import ConsentDenied
    with pytest.raises(ConsentDenied):
        pm.upgrade(src, consented_sha256=p.manifest_sha256, operator="op", accepted=False)
    assert pm.record("hello_world")["version"] == "1.0.0"
    assert pm.consent.records()[-1].decision == "refused"


# =========================================================================== #
# never-verified: no host verifies, sandboxes or tests a declared permission.
# =========================================================================== #
_CHECKER_WORDS = ("verif", "enforce", "sandbox", "allow", "permit", "deny", "denied",
                  "restrict", "police", "guard", "authori", "may_", "can_", "is_granted",
                  "check", "test", "block", "intercept", "firewall", "seccomp")
_PERMISSION_WORDS = ("permission", "network", "filesystem", "socket", "access", "path")


def _public_names():
    """Every public name of every library module, and every public method of
    every public class the library defines."""
    import importlib
    import pkgutil
    found = []
    for info in pkgutil.iter_modules(compose_plugins.__path__):
        if info.name.startswith("_") or info.name == "conformance":
            continue
        mod = importlib.import_module(f"compose_plugins.{info.name}")
        for name, obj in vars(mod).items():
            if name.startswith("_"):
                continue
            found.append(f"{info.name}.{name}")
            if isinstance(obj, type) and obj.__module__ == mod.__name__:
                found += [f"{info.name}.{name}.{a}" for a in vars(obj) if not a.startswith("_")]
    return found


def _permission_checkers(names):
    out = []
    for full in names:
        leaf = full.rsplit(".", 1)[-1].lower()
        if any(w in leaf for w in _PERMISSION_WORDS) and any(w in leaf for w in _CHECKER_WORDS):
            out.append(full)
    return out


def test_library_exposes_no_permission_checking_api():
    """[CXC-PC-PERMISSIONS] never-verified: no public function, class or method
    of the library checks, verifies, enforces or sandboxes a permission. The
    declaration-shape validators (permission_target_problem, ...) only judge
    whether a DECLARATION is well formed and are allowed by name below."""
    names = _public_names()
    assert len(names) > 100, "the scan found too little to be a scan"
    shape_only = {"manifest.permission_path_problem", "manifest.permission_target_problem"}
    hits = [n for n in _permission_checkers(names) if n not in shape_only]
    assert hits == [], f"permission-checking API found: {hits}"


def test_permission_checker_scan_can_fail(monkeypatch):
    """Negative control of the scan: a planted checker is found."""
    monkeypatch.setattr(consent_mod, "enforce_network_permission", lambda *a: None,
                        raising=False)
    assert "consent.enforce_network_permission" in _permission_checkers(_public_names())


def _modules_reading_permissions():
    """Library modules whose source reads the manifest's `permissions` or its
    accessors (network_permissions / filesystem_permissions)."""
    readers = set()
    for py in sorted(LIB.glob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "permissions":
                readers.add(py.name)
            if isinstance(node, ast.Attribute) and node.attr in (
                    "network_permissions", "filesystem_permissions"):
                readers.add(py.name)
    return readers


def test_only_the_validator_consent_renderer_and_grant_builder_read_permissions():
    """[CXC-PC-PERMISSIONS] verify-machine: no host code path reads
    `permissions` except the validator (manifest.py) and the consent renderer
    and grant builder (consent.py). The runner, lifecycle, web UI and host
    protocol never see it."""
    assert _modules_reading_permissions() == {"manifest.py", "consent.py"}


def test_permissions_reader_scan_can_fail(tmp_path, monkeypatch):
    """Negative control: a planted reader in another module is found."""
    planted = tmp_path / "lib"
    shutil.copytree(LIB, planted, ignore=shutil.ignore_patterns("__pycache__", "fixtures"))
    (planted / "runner.py").write_text((planted / "runner.py").read_text()
                                       + "\n_x = {}.get('permissions')\n")
    monkeypatch.setattr(sys.modules[__name__], "LIB", planted)
    assert "runner.py" in _modules_reading_permissions()


_NET_PLUGIN_START = '''    def start(self, bus):
        import os, socket
        port = int(open(os.environ["CP_R21_PORT_FILE"]).read())
        s = socket.create_connection(("127.0.0.1", port), timeout=5)
        s.sendall(b"undeclared")
        s.close()
'''


@pytest.mark.parametrize("level", ["inprocess", "subprocess"])
def test_undeclared_network_use_is_neither_refused_nor_parked(tmp_path, monkeypatch, level):
    """[CXC-PC-PERMISSIONS] verify-machine + never-verified: a plug-in that
    declares NO permissions and uses the network anyway runs. No host checks
    behaviour against a declaration."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(15)
    port_file = tmp_path / "port"
    port_file.write_text(str(listener.getsockname()[1]))
    monkeypatch.setenv("CP_R21_PORT_FILE", str(port_file))
    got: list = []

    def accept():
        try:
            conn, _ = listener.accept()
            got.append(conn.recv(64))
            conn.close()
        except OSError as exc:
            got.append(exc)
    t = threading.Thread(target=accept, daemon=True)
    t.start()

    src = h.fixture_copy(tmp_path)
    data = json.loads((src / "manifest.json").read_text())
    assert "permissions" not in data
    plugin = src / "hello_world" / "plugin.py"
    text = plugin.read_text()
    assert "    def start(self, bus):\n" in text
    plugin.write_text(text.replace("    def start(self, bus):\n", _NET_PLUGIN_START, 1))

    host, pm = h.make(tmp_path, level)
    h.install(pm, src)
    status = pm.enable("hello_world")
    t.join(15)
    listener.close()
    h.wait_for(lambda: (pm.tick() or True) and pm.status("hello_world") == "running")
    print(level, status, pm.status("hello_world"), got)
    assert got == [b"undeclared"], "the plug-in did not reach the network"
    assert pm.state("hello_world") == "enabled"
    assert pm.status("hello_world") == "running"
    assert not pm.status("hello_world").startswith(("parked", "failed"))


# =========================================================================== #
# r22 delta-audit rows ([CXC-PC-PERMISSIONS] network-entry, as fixed in r22).
# =========================================================================== #
R22_AUDIT_ROWS = [
    # target, accepted?, refused by the schema too? (None when accepted)
    ("[fe80::1%eth0]", False, True),        # zone id
    ("[::]", True, None), ("[::1]", True, None), ("0.0.0.0", True, None),
    ("[2001:db8::1:0:0:1]", True, None),    # tie: the FIRST longest run compressed
    ("[2001:db8:0:0:1::1]", False, False),  # tie: the second run compressed
    ("1.2.3", False, True),
    ("prïnter.local", False, True),    # non-ASCII host
    ("xn--prnter-5ua.local", True, None),   # its ASCII (punycode) spelling
    ("0x7f", True, None),                   # all-numeric means [0-9]+ (a known limit)
    ("[::ffff:c000:201]", True, None), ("[::ffff:192.0.2.1]", False, True),
]


@pytest.mark.parametrize("target,ok,schema_refuses", R22_AUDIT_ROWS,
                         ids=[r[0] for r in R22_AUDIT_ROWS])
def test_r22_audit_rows(target, ok, schema_refuses):
    m = _net_manifest(target)
    assert validator_ok(m) is ok, target
    if ok:
        assert schema_ok(m), target
    else:
        assert schema_ok(m) is (not schema_refuses), target


def test_r22_canonical_check_is_its_own_function_not_ipaddress_compressed():
    """The audit's warning: ``ipaddress.IPv6Address(s).compressed == s`` accepts
    a zone id and refuses ::ffff:c000:201 on some Pythons. ipv6_rfc5952 is a
    standalone canonicalizer and gives the ruled answers whatever
    ``compressed`` says."""
    import inspect
    src = inspect.getsource(ipv6_rfc5952)
    assert ".compressed" not in src and "str(addr)" not in src
    assert ipv6_rfc5952("fe80::1%eth0") is None
    assert ipv6_rfc5952("::ffff:c000:201") == "::ffff:c000:201"
    assert ipv6_rfc5952("2001:db8:0:0:1:0:0:1") == "2001:db8::1:0:0:1"
    assert any("RFC 5952" in r for r in manifest_mod.SCHEMA_INEXPRESSIBLE)
    assert any("RFC 5952" in r for r in [build_schema()["description"]])
