"""Spec 95 r10, [CXC-PC-HOST-SECTIONS] host-name-rule: a host name matches
^[a-z][a-z0-9_]*$ and is never `bus`, `code` or (since r21) `permission`. A manifest whose hosts holds
such a name is refused naming it (validator AND generated schema agree), and
the host's OWN name is checked wherever the library takes it. Each test
cites r10."""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from compose_plugins.consent import ConsentRecord, granted_set, render_consent
from compose_plugins.errors import ConsentRefused, HostNameRefused, ManifestRefused
from compose_plugins.manifest import (
    NO_PACKAGE, PATTERNS, RESERVED_HOST_NAMES, build_schema, check_host_name,
    validate_manifest,
)

from conftest import empty_section, valid_manifest

jsonschema = pytest.importorskip("jsonschema")

GOOD_NAMES = ["composeio", "gnomon", "otherhost", "a", "a_1", "busy", "codex", "bus_x",
              "code2", "x_bus", "permissions", "permission_x", "permissio"]
BAD_NAMES = ["bus", "code", "Composeio", "GNOMON", "1host", "_host", "ho-st", "ho st",
             "ho.st", "", "host\n", "hé", "ｈost", "ho:st", "bus\n", "code ", "permission",
             "permission\n", "Permission"]


def _with_extra_host(name):
    m = valid_manifest()
    m["hosts"][name] = {}
    return m


def _validator_accepts(m, host="composeio"):
    try:
        validate_manifest(m, host=host, section_validator=empty_section, package=NO_PACKAGE)
        return True
    except ManifestRefused:
        return False


def test_r21_reserved_names_are_bus_code_and_permission():
    """[r10, r21] the reserved names are the built-in grant prefixes; r21 adds
    `permission`, the prefix of permission.network: and permission.filesystem:."""
    assert RESERVED_HOST_NAMES == ("bus", "code", "permission")


@pytest.mark.parametrize("name", GOOD_NAMES)
def test_r10_good_host_keys_pass(name):
    """[r10] positive control: lowercase identifiers, incl. ones starting bus/code."""
    assert _validator_accepts(_with_extra_host(name))
    assert check_host_name(name) == name


@pytest.mark.parametrize("name", BAD_NAMES)
def test_r10_bad_host_key_is_refused_naming_it_even_for_another_host(name):
    """[r10] a hosts key breaking the rule is refused naming it, even though
    it is not the installing host (whose sections are otherwise ignored)."""
    with pytest.raises(ManifestRefused) as ei:
        validate_manifest(_with_extra_host(name), host="composeio",
                          section_validator=empty_section, package=NO_PACKAGE)
    print(ei.value)
    assert ei.value.fields == [f"hosts.{name}"]
    assert repr(name) in ei.value.refusals[0].message


@pytest.mark.parametrize("name", GOOD_NAMES + BAD_NAMES)
def test_r10_generated_schema_agrees_with_the_validator(name):
    """[r10] the generated schema's hosts.propertyNames gives the same answer."""
    v = jsonschema.Draft202012Validator(build_schema())
    m = _with_extra_host(name)
    assert v.is_valid(m) == _validator_accepts(m)


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node (an ECMA-262 engine) is not installed")
def test_r10_host_name_pattern_means_the_same_in_ecma_262():
    """[r10] the host-name pattern in node's RegExp(u) equals Python re."""
    pat = PATTERNS["host_name"]
    script = ("const d=JSON.parse(require('fs').readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(d.names.map(s=>new RegExp(d.p,'u').test(s))))")
    names = GOOD_NAMES + BAD_NAMES
    r = subprocess.run([NODE, "-e", script], input=json.dumps({"p": pat, "names": names}),
                       capture_output=True, text=True, check=True)
    ecma = json.loads(r.stdout)
    py = [re.search(pat, s) is not None for s in names]
    assert ecma == py
    assert py == [True] * len(GOOD_NAMES) + [False] * len(BAD_NAMES)


# ---- the host's OWN name --------------------------------------------------- #
@pytest.mark.parametrize("name", ["bus", "code", "permission", "Composeio", "", "composeio\n",
                                  None])
def test_r10_own_name_checked_by_validate_manifest(name):
    """[r10] the installing host's own name is checked before anything else."""
    with pytest.raises(HostNameRefused) as ei:
        validate_manifest(valid_manifest(), host=name, section_validator=empty_section,
                          package=NO_PACKAGE)
    print(ei.value)
    assert repr(name) in str(ei.value)


@pytest.mark.parametrize("name", ["bus", "code", "permission"])
def test_r10_own_name_checked_by_consent(name):
    """[r10] granted_set and render_consent check the host's own name."""
    m = valid_manifest()
    m["hosts"]["busy"] = {}
    vm = validate_manifest(m, host="composeio", section_validator=empty_section,
                           package=NO_PACKAGE)
    with pytest.raises(HostNameRefused):
        granted_set(vm, host=name, render_host_section=lambda s: [])
    with pytest.raises(HostNameRefused):
        render_consent(vm, host=name, runs_as="inprocess", render_host_section=lambda s: [])


@pytest.mark.parametrize("name", ["bus", "code", "permission", "Composeio", "com-poseio"])
def test_r10_record_host_follows_the_rule(name):
    """[r10] a consent record's host follows the same rule."""
    with pytest.raises(ConsentRefused) as ei:
        ConsentRecord(domain="hello_world", version="1.0.0", manifest_sha256="c" * 64,
                      granted=(), decided_by="op", decided_at="2026-11-26T02:00:00Z",
                      host=name, decision="granted")
    print(ei.value)
    assert "host-name rule" in str(ei.value)


def test_r10_positive_control_own_name():
    """[r10] a good own name passes every entry point."""
    vm = validate_manifest(valid_manifest(), host="gnomon", section_validator=empty_section,
                           package=NO_PACKAGE)
    granted_set(vm, host="gnomon", render_host_section=lambda s: [])
    ConsentRecord(domain="hello_world", version="1.0.0", manifest_sha256="c" * 64,
                  granted=(), decided_by="op", decided_at="2026-11-26T02:00:00Z",
                  host="gnomon", decision="granted")


@pytest.mark.parametrize("name", ["bus", "code", "permission", "Bad-Name"])
def test_r21_host_name_refusal_message_names_every_reserved_name(name):
    """The refusal TEXT states the whole rule, not only the r10 half: every
    reserved name (read from the rule's own list, so the message cannot drift
    from it again) appears in the message."""
    from compose_plugins.manifest import RESERVED_HOST_NAMES, check_host_name
    with pytest.raises(HostNameRefused) as ei:
        check_host_name(name)
    text = str(ei.value)
    assert repr(name) in text
    assert RESERVED_HOST_NAMES == ("bus", "code", "permission")
    for reserved in RESERVED_HOST_NAMES:
        assert repr(reserved) in text, (reserved, text)
