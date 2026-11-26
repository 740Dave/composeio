"""The manifest validator: [CXC-PC-MANIFEST-FIELDS], -RUN, -BUS-BLOCK,
-UNKNOWN-KEYS, -RESERVED-NAMES, -STANDARD-QUESTIONS, -HOST-SECTIONS.

Each boundary case is a NEGATIVE CONTROL: one mutation of a valid manifest,
asserted to be refused naming EXACTLY that field. The positive control is the
unmutated manifest, which passes; so every check here is shown both able to
pass and able to fail.
"""
from __future__ import annotations

import pytest

from compose_plugins.errors import ManifestRefused, Refusal
from compose_plugins.manifest import (
    BUS_TYPES_ALLOWED, BUS_TYPES_RESERVED, HA_OTHER_NAMES_RESERVED,
    HA_RESERVED_KEYS, NO_PACKAGE, OPTIONAL_FIELDS, REQUIRED_FIELDS,
    check_bus_type_vocabulary, message_type_names, normalize_requirement_name,
    validate_manifest,
)
from compose_plugins.package import PackageSource

from conftest import empty_section, valid_manifest


def validate(m, host="composeio", package=NO_PACKAGE, section_validator=empty_section):
    return validate_manifest(m, host=host, section_validator=section_validator,
                             package=package)


def refused_fields(m, **kw):
    with pytest.raises(ManifestRefused) as ei:
        validate(m, **kw)
    print(ei.value)
    return ei.value.fields


# --------------------------------------------------------------------------- #
# Positive controls.
# --------------------------------------------------------------------------- #
def test_valid_manifest_passes(manifest):
    v = validate(manifest)
    assert v.domain == "hello_world"
    assert v.publishes == [("plugins/hello_world/greeting", "STATE"),
                           ("plugins/hello_world/pong", "EVENT")]
    assert v.subscribes == [("plugins/hello_world/ping", "EVENT")]


def test_valid_manifest_passes_on_gnomon_too(manifest):
    validate(manifest, host="gnomon")


def test_minimal_manifest_passes():
    m = valid_manifest()
    for k in OPTIONAL_FIELDS:
        m.pop(k, None)
    assert validate(m).isolation_floor == "inprocess"


@pytest.mark.parametrize("pin", ["requests==2.32.3", "my_pkg.sub==1.0.0rc1",
                                 "A==1", "x-y_z==2024.1.post1+local"])
def test_exact_pins_pass(pin):
    m = valid_manifest()
    m["requirements"] = [pin]
    validate(m)


@pytest.mark.parametrize("ver", ["0.0.0", "1.2.3-alpha.1", "1.2.3+build.5", "10.20.30-rc.1+x"])
def test_semver_passes(ver):
    m = valid_manifest()
    m["version"] = ver
    validate(m)


def test_empty_bus_lists_pass_and_subprocess_floor_passes():
    m = valid_manifest()
    m["bus"] = {"publishes": [], "subscribes": []}
    m["isolation_floor"] = "subprocess"
    validate(m)


def test_same_namespace_with_two_types_is_not_a_duplicate():
    m = valid_manifest()
    m["bus"]["publishes"].append({"namespace": "plugins/hello_world/greeting",
                                  "type": "EVENT"})
    validate(m)


# --------------------------------------------------------------------------- #
# The boundary table ([CXC-PC-MANIFEST-FIELDS] boundary-case and neighbours).
# Each row: (id, mutation, the ONE field the refusal must name).
# --------------------------------------------------------------------------- #
def _set(path, value):
    def mut(m):
        cur = m
        for p in path[:-1]:
            cur = cur[p]
        cur[path[-1]] = value
    return mut


def _del(path):
    def mut(m):
        cur = m
        for p in path[:-1]:
            cur = cur[p]
        del cur[path[-1]]
    return mut


BOUNDARY = [
    ("domain-uppercase", _set(["domain"], "Hello"), "domain"),
    ("domain-digit-first", _set(["domain"], "1hello"), "domain"),
    ("domain-hyphen", _set(["domain"], "hello-world"), "domain"),
    ("domain-not-string", _set(["domain"], 7), "domain"),
    ("name-empty", _set(["name"], ""), "name"),
    ("version-calver-zero-padded", _set(["version"], "2026.01.05"), "version"),
    ("version-two-part", _set(["version"], "1.0"), "version"),
    ("version-leading-v", _set(["version"], "v1.0.0"), "version"),
    ("description-two-lines", _set(["description"], "one\ntwo"), "description"),
    ("description-empty", _set(["description"], ""), "description"),
    ("documentation-http", _set(["documentation"], "http://example.invalid/d"), "documentation"),
    ("documentation-not-url", _set(["documentation"], "docs/readme"), "documentation"),
    ("issue-tracker-not-url", _set(["issue_tracker"], "file:///tmp/x"), "issue_tracker"),
    ("codeowners-no-at", _set(["codeowners"], ["example-user"]), "codeowners[0]"),
    ("codeowners-three-parts", _set(["codeowners"], ["@a/b/c"]), "codeowners[0]"),
    ("codeowners-email", _set(["codeowners"], ["@a", "a@example.invalid"]), "codeowners[1]"),
    ("codeowners-not-list", _set(["codeowners"], "@a"), "codeowners"),
    ("requirements-gte", _set(["requirements"], ["requests>=2"]), "requirements[0]"),
    ("requirements-bare-name", _set(["requirements"], ["requests"]), "requirements[0]"),
    ("requirements-wildcard", _set(["requirements"], ["requests==2.*"]), "requirements[0]"),
    ("requirements-triple-eq", _set(["requirements"], ["requests===2.0"]), "requirements[0]"),
    ("requirements-composebus", _set(["requirements"], ["composebus==0.1.0"]), "requirements[0]"),
    ("requirements-compose-plugins", _set(["requirements"], ["ok==1", "compose-plugins==0.1.0"]),
     "requirements[1]"),
    ("requirements-composeio", _set(["requirements"], ["composeio==1.0.0"]), "requirements[0]"),
    ("requirements-ComposeBus-case", _set(["requirements"], ["ComposeBus==0.1.0"]), "requirements[0]"),
    ("requirements-compose_plugins-underscore", _set(["requirements"], ["Compose_Plugins==0.1.0"]),
     "requirements[0]"),
    ("requirements-compose.plugins-dot", _set(["requirements"], ["compose.plugins==0.1.0"]),
     "requirements[0]"),
    ("requirements-compose-_.plugins-mixed-run", _set(["requirements"], ["Compose-_.Plugins==0.1.0"]),
     "requirements[0]"),
    ("requirements-not-list", _set(["requirements"], "requests==2"), "requirements"),
    ("run-entry-no-colon", _set(["run", "entry"], "hello_world.plugin"), "run.entry"),
    ("run-entry-two-colons", _set(["run", "entry"], "a:b:c"), "run.entry"),
    ("run-entry-path", _set(["run", "entry"], "hello_world/plugin.py:HelloWorld"), "run.entry"),
    ("run-form-process", _set(["run", "form"], "process"), "run.form"),
    ("run-form-container", _set(["run", "form"], "container"), "run.form"),
    ("run-form-other", _set(["run", "form"], "node"), "run.form"),
    ("run-unknown-key", _set(["run", "args"], ["--x"]), "run.args"),
    ("run-missing-entry", _del(["run", "entry"]), "run.entry"),
    ("run-not-object", _set(["run"], "hello_world.plugin:HelloWorld"), "run"),
    ("isolation-floor-other", _set(["isolation_floor"], "container"), "isolation_floor"),
    ("isolation-floor-uppercase", _set(["isolation_floor"], "INPROCESS"), "isolation_floor"),
    ("hosts-empty", _set(["hosts"], {}), "hosts"),
    ("hosts-not-object", _set(["hosts"], ["composeio"]), "hosts"),
    ("hosts-own-section-invalid", _set(["hosts", "composeio"], {"x": 1}), "hosts.composeio"),
    ("config-schema-missing", _del(["config_schema"]), "config_schema"),
    ("config-schema-not-object", _set(["config_schema"], []), "config_schema"),
    ("unknown-core-key", _set(["permisions"], ["bus"]), "permisions"),
    ("health-key", _set(["health"], {"check": "x"}), "health"),
    ("upgrade-block", _set(["upgrade"], {"auto": True}), "upgrade"),
    ("bus-unknown-key", _set(["bus", "mqtt"], []), "bus.mqtt"),
    ("bus-read-reserved", _set(["bus", "read"], ["x/#"]), "bus.read"),
    ("bus-missing-subscribes", _del(["bus", "subscribes"]), "bus.subscribes"),
    ("bus-not-object", _set(["bus"], []), "bus"),
    ("bus-entry-unknown-key", _set(["bus", "publishes", 0, "retain"], True), "bus.publishes[0].retain"),
    ("bus-namespace-other-domain", _set(["bus", "publishes", 0, "namespace"], "plugins/other_domain/x"),
     "bus.publishes[0].namespace"),
    ("bus-namespace-server", _set(["bus", "subscribes", 0, "namespace"], "_server/anything"),
     "bus.subscribes[0].namespace"),
    ("bus-namespace-prefix-only", _set(["bus", "publishes", 1, "namespace"], "plugins/hello_world/"),
     "bus.publishes[1].namespace"),
    ("bus-namespace-domain-prefix-trick", _set(["bus", "publishes", 0, "namespace"],
                                               "plugins/hello_worldx/greeting"),
     "bus.publishes[0].namespace"),
    ("bus-type-unknown", _set(["bus", "publishes", 0, "type"], "FROBNICATE"), "bus.publishes[0].type"),
    ("bus-type-lowercase", _set(["bus", "publishes", 0, "type"], "state"), "bus.publishes[0].type"),
    ("bus-type-missing", _del(["bus", "subscribes", 0, "type"]), "bus.subscribes[0].type"),
    ("bus-duplicate-pair", lambda m: m["bus"]["subscribes"].append(
        {"namespace": "plugins/hello_world/ping", "type": "EVENT"}), "bus.subscribes[1]"),
    ("standard-questions-absolute", _set(["standard_questions"], "/etc"), "standard_questions"),
    ("standard-questions-dotdot", _set(["standard_questions"], "docs/../../x"), "standard_questions"),
    ("standard-questions-no-package", _set(["standard_questions"], "questions"), "standard_questions"),
] + [(f"required-missing-{k}", _del([k]), k) for k in REQUIRED_FIELDS if k != "contract"]

# --------------------------------------------------------------------------- #
# [CXC-PC-PERMISSIONS] boundary-case (r21): one row per refusal, each a
# mutation of a manifest declaring one valid network and one valid filesystem
# entry (VALID_PERMISSIONS, which installs: test_r21_permissions.py).
# --------------------------------------------------------------------------- #
VALID_PERMISSIONS = {
    "network": [{"direction": "outbound", "target": "printer.local:631",
                 "purpose": "print the greeting on the office printer (IPP)"}],
    "filesystem": [{"path": "/tmp/hello", "access": "read-write",
                    "purpose": "keep a log of greetings sent"}],
}


def _perm(edit):
    """Give the manifest VALID_PERMISSIONS, then apply ``edit`` to it."""
    def mut(m):
        import copy
        m["permissions"] = copy.deepcopy(VALID_PERMISSIONS)
        edit(m["permissions"])
    return mut


def _net(key, value):
    return _perm(lambda p: p["network"][0].__setitem__(key, value))


def _fs(key, value):
    return _perm(lambda p: p["filesystem"][0].__setitem__(key, value))


_N0, _F0 = "permissions.network[0]", "permissions.filesystem[0]"
PERMISSION_BOUNDARY = [
    ("perm-not-object", _set(["permissions"], ["network"]), "permissions"),
    ("perm-unknown-key-camera", _perm(lambda p: p.__setitem__("camera", [])), "permissions.camera"),
    ("perm-unknown-key-bus", _perm(lambda p: p.__setitem__("bus", [])), "permissions.bus"),
    ("perm-network-not-list", _perm(lambda p: p.__setitem__("network", {})), "permissions.network"),
    ("perm-filesystem-null", _perm(lambda p: p.__setitem__("filesystem", None)),
     "permissions.filesystem"),
    ("perm-network-entry-not-object", _perm(lambda p: p["network"].__setitem__(0, "printer")), _N0),
    ("perm-network-unknown-key", _net("port", 631), f"{_N0}.port"),
    ("perm-filesystem-unknown-key", _fs("recursive", True), f"{_F0}.recursive"),
    ("perm-network-missing-purpose", _perm(lambda p: p["network"][0].pop("purpose")),
     f"{_N0}.purpose"),
    ("perm-network-missing-target", _perm(lambda p: p["network"][0].pop("target")),
     f"{_N0}.target"),
    ("perm-network-missing-direction", _perm(lambda p: p["network"][0].pop("direction")),
     f"{_N0}.direction"),
    ("perm-filesystem-missing-access", _perm(lambda p: p["filesystem"][0].pop("access")),
     f"{_F0}.access"),
    ("perm-filesystem-missing-path", _perm(lambda p: p["filesystem"][0].pop("path")),
     f"{_F0}.path"),
    ("perm-direction-both", _net("direction", "both"), f"{_N0}.direction"),
    ("perm-direction-uppercase", _net("direction", "OUTBOUND"), f"{_N0}.direction"),
    ("perm-access-write", _fs("access", "write"), f"{_F0}.access"),
    ("perm-access-rw", _fs("access", "rw"), f"{_F0}.access"),
    # targets: the spec's own examples, then the grammar's edges
    ("perm-target-wildcard", _net("target", "*.example.com"), f"{_N0}.target"),
    ("perm-target-any-port", _net("target", "any:443"), f"{_N0}.target"),
    ("perm-target-uppercase", _net("target", "Printer.Local"), f"{_N0}.target"),
    ("perm-target-port-0", _net("target", "printer.local:0"), f"{_N0}.target"),
    ("perm-target-port-65536", _net("target", "printer.local:65536"), f"{_N0}.target"),
    ("perm-target-port-leading-zero", _net("target", "printer.local:0631"), f"{_N0}.target"),
    ("perm-target-empty", _net("target", ""), f"{_N0}.target"),
    ("perm-target-leading-dot", _net("target", ".example.com"), f"{_N0}.target"),
    ("perm-target-trailing-dot", _net("target", "printer.local."), f"{_N0}.target"),
    ("perm-target-double-dot", _net("target", "printer..local"), f"{_N0}.target"),
    ("perm-target-label-hyphen-start", _net("target", "-printer.local"), f"{_N0}.target"),
    ("perm-target-label-hyphen-end", _net("target", "printer-.local"), f"{_N0}.target"),
    ("perm-target-label-64", _net("target", "a" * 64 + ".local"), f"{_N0}.target"),
    ("perm-target-254-chars", _net("target", ".".join(["a" * 63] * 3 + ["a" * 62])),
     f"{_N0}.target"),
    ("perm-target-question-mark", _net("target", "printer?.local"), f"{_N0}.target"),
    ("perm-target-underscore", _net("target", "my_printer.local"), f"{_N0}.target"),
    ("perm-target-url", _net("target", "https://printer.local"), f"{_N0}.target"),
    ("perm-target-ipv6-unbracketed", _net("target", "::1"), f"{_N0}.target"),
    ("perm-target-ipv6-uppercase", _net("target", "[2001:DB8::1]"), f"{_N0}.target"),
    ("perm-target-ipv6-dotted", _net("target", "[::ffff:192.0.2.1]"), f"{_N0}.target"),
    ("perm-target-ipv6-unclosed", _net("target", "[::1"), f"{_N0}.target"),
    ("perm-target-ipv6-port-0", _net("target", "[::1]:0"), f"{_N0}.target"),
    ("perm-target-not-string", _net("target", 631), f"{_N0}.target"),
    # r22 ruling (RFC 3696 section 2): the final label is not all-numeric
    ("perm-target-ipv4-leading-zero", _net("target", "010.0.0.1"), f"{_N0}.target"),
    ("perm-target-ipv4-octet-256", _net("target", "256.1.1.1"), f"{_N0}.target"),
    ("perm-target-numeric-final-label", _net("target", "printer.123"), f"{_N0}.target"),
    # validator-only (SCHEMA_INEXPRESSIBLE): RFC 5952 canonical form
    ("perm-target-ipv6-not-compressed", _net("target", "[2001:db8:0:0:0:0:0:1]"),
     f"{_N0}.target"),
    ("perm-target-ipv6-leading-zeros", _net("target", "[2001:0db8::1]"), f"{_N0}.target"),
    ("perm-target-ipv6-tie-not-first-run", _net("target", "[2001:db8:0:0:1::1]"), f"{_N0}.target"),
    ("perm-target-ipv6-single-group-shortened", _net("target", "[2001:db8::1:1:1:1:1]"),
     f"{_N0}.target"),
    ("perm-target-ipv6-not-an-address", _net("target", "[1:2:3:4:5:6:7:8:9]"), f"{_N0}.target"),
    # paths
    ("perm-path-relative", _fs("path", "data/x"), f"{_F0}.path"),
    ("perm-path-dotdot", _fs("path", "/tmp/../etc"), f"{_F0}.path"),
    ("perm-path-dotdot-last", _fs("path", "/tmp/.."), f"{_F0}.path"),
    ("perm-path-dot", _fs("path", "/tmp/./hello"), f"{_F0}.path"),
    ("perm-path-dot-last", _fs("path", "/."), f"{_F0}.path"),
    ("perm-path-double-slash", _fs("path", "/tmp//hello"), f"{_F0}.path"),
    ("perm-path-tilde-home", _fs("path", "~/hello"), f"{_F0}.path"),
    ("perm-path-tilde-inside", _fs("path", "/home/~dave"), f"{_F0}.path"),
    ("perm-path-star", _fs("path", "/tmp/*"), f"{_F0}.path"),
    ("perm-path-question", _fs("path", "/tmp/hell?"), f"{_F0}.path"),
    ("perm-path-bracket", _fs("path", "/tmp/[ab]"), f"{_F0}.path"),
    ("perm-path-trailing-slash", _fs("path", "/tmp/hello/"), f"{_F0}.path"),
    ("perm-path-nul", _fs("path", "/tmp/a\x00b"), f"{_F0}.path"),
    ("perm-path-newline", _fs("path", "/tmp/a\nb"), f"{_F0}.path"),
    ("perm-path-del", _fs("path", "/tmp/a\x7fb"), f"{_F0}.path"),
    ("perm-path-c1-control", _fs("path", "/tmp/a\x85b"), f"{_F0}.path"),
    ("perm-path-empty", _fs("path", ""), f"{_F0}.path"),
    ("perm-path-windows", _fs("path", "C:\\tmp"), f"{_F0}.path"),
    # validator-only: the 4096-BYTE limit (maxLength would count code points)
    ("perm-path-4097-bytes", _fs("path", "/" + "a" * 4096), f"{_F0}.path"),
    ("perm-path-over-4096-bytes-multibyte", _fs("path", "/" + "\u00e9" * 2048), f"{_F0}.path"),
    # purposes
    ("perm-purpose-blank", _net("purpose", "   "), f"{_N0}.purpose"),
    ("perm-purpose-empty", _fs("purpose", ""), f"{_F0}.purpose"),
    ("perm-purpose-format-only", _net("purpose", "\u200b"), f"{_N0}.purpose"),
    ("perm-purpose-two-lines", _net("purpose", "print\nthings"), f"{_N0}.purpose"),
    ("perm-purpose-u2028", _fs("purpose", "keep\u2028log"), f"{_F0}.purpose"),
    ("perm-purpose-201", _net("purpose", "x" * 201), f"{_N0}.purpose"),
    ("perm-purpose-not-string", _fs("purpose", 7), f"{_F0}.purpose"),
    # validator-only: duplicates ([CXC-PC-PERMISSIONS] duplicates)
    ("perm-network-duplicate", _perm(lambda p: p["network"].append(
        {"direction": "outbound", "target": "printer.local:631", "purpose": "other words"})),
     "permissions.network[1]"),
    ("perm-filesystem-duplicate-other-access", _perm(lambda p: p["filesystem"].append(
        {"path": "/tmp/hello", "access": "read", "purpose": "read it back"})),
     "permissions.filesystem[1]"),
]
#: the PERMISSION_BOUNDARY rows a JSON Schema cannot refuse (SCHEMA_INEXPRESSIBLE)
PERMISSION_VALIDATOR_ONLY = {
    "perm-target-ipv6-not-compressed", "perm-target-ipv6-leading-zeros",
    "perm-target-ipv6-tie-not-first-run", "perm-target-ipv6-single-group-shortened",
    "perm-target-ipv6-not-an-address", "perm-path-4097-bytes",
    "perm-path-over-4096-bytes-multibyte", "perm-network-duplicate",
    "perm-filesystem-duplicate-other-access",
}
BOUNDARY = BOUNDARY + PERMISSION_BOUNDARY


@pytest.mark.parametrize("case_id,mutate,field", BOUNDARY, ids=[b[0] for b in BOUNDARY])
def test_boundary_case_is_refused_naming_exactly_that_field(case_id, mutate, field):
    m = valid_manifest()
    mutate(m)
    assert refused_fields(m) == [field]


# --------------------------------------------------------------------------- #
# Reserved names ([CXC-PC-RESERVED-NAMES]): one test per key.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("key", HA_RESERVED_KEYS + HA_OTHER_NAMES_RESERVED)
def test_each_reserved_ha_key_is_refused_naming_it(key):
    m = valid_manifest()
    m[key] = "x"
    with pytest.raises(ManifestRefused) as ei:
        validate(m)
    assert ei.value.fields == [key]
    assert "reserved" in ei.value.refusals[0].message


def test_reserved_lists_match_the_spec_counts():
    # ha-reserved-keys: 10 integration + 9 add-on names; ha-other-names-reserved: 12
    assert len(HA_RESERVED_KEYS) == 19 and len(set(HA_RESERVED_KEYS)) == 19
    assert len(HA_OTHER_NAMES_RESERVED) == 12


def test_unknown_key_message_differs_from_reserved():
    m = valid_manifest()
    m["permisions"] = 1
    with pytest.raises(ManifestRefused) as ei:
        validate(m)
    assert "unknown key" in ei.value.refusals[0].message


# --------------------------------------------------------------------------- #
# Bus types ([CXC-PC-RULE-REAL-TYPES], [CXC-PC-BUS-BLOCK]).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("typ", BUS_TYPES_RESERVED)
def test_each_reserved_message_type_is_refused_as_reserved(typ):
    m = valid_manifest()
    m["bus"]["publishes"][0]["type"] = typ
    with pytest.raises(ManifestRefused) as ei:
        validate(m)
    assert ei.value.fields == ["bus.publishes[0].type"]
    msg = ei.value.refusals[0].message
    print(msg)
    assert typ in msg and "reserved to kernel/host traffic" in msg


@pytest.mark.parametrize("typ", BUS_TYPES_ALLOWED)
def test_state_and_event_pass(typ):
    m = valid_manifest()
    m["bus"]["subscribes"][0]["type"] = typ
    validate(m)


def test_unknown_type_refusal_lists_names_from_installed_composebus():
    m = valid_manifest()
    m["bus"]["publishes"][0]["type"] = "FROBNICATE"
    with pytest.raises(ManifestRefused) as ei:
        validate(m)
    msg = ei.value.refusals[0].message
    for name in message_type_names():
        assert name in msg


def test_reserved_types_match_installed_message_type():
    check_bus_type_vocabulary()
    assert len(BUS_TYPES_RESERVED) == 8


def test_vocabulary_check_fails_when_composebus_renames_a_member(monkeypatch):
    """Negative control of the vocabulary check itself."""
    import compose_plugins.manifest as mod
    monkeypatch.setattr(mod, "message_type_names",
                        lambda: tuple(n if n != "ACK" else "ACKNOWLEDGE"
                                      for n in BUS_TYPES_ALLOWED + BUS_TYPES_RESERVED))
    with pytest.raises(RuntimeError) as ei:
        mod.check_bus_type_vocabulary()
    print(ei.value)
    assert "ACK" in str(ei.value) and "ACKNOWLEDGE" in str(ei.value)


# --------------------------------------------------------------------------- #
# Host sections ([CXC-PC-HOST-SECTIONS], [CXC-PC-UNKNOWN-KEYS]).
# --------------------------------------------------------------------------- #
def test_own_key_absent_is_refused_with_exact_text():
    m = valid_manifest()
    m["hosts"] = {"gnomon": {}}
    with pytest.raises(ManifestRefused) as ei:
        validate(m, host="composeio")
    assert ei.value.refusals == [Refusal("hosts", "not supported on host composeio")]


def test_other_host_section_content_and_unknown_host_keys_are_ignored():
    """Positive control paired with NC-UNKNOWN-CORE-KEY."""
    m = valid_manifest()
    m["hosts"]["gnomon"] = {"anything": [1, 2], "permisions": "typo is gnomon's business"}
    m["hosts"]["otherhost"] = "not even an object"
    validate(m, host="composeio")


def test_section_validator_is_required(manifest):
    with pytest.raises(TypeError):
        validate_manifest(manifest, host="composeio", section_validator=None,
                          package=NO_PACKAGE)


def test_section_refusal_paths_are_prefixed():
    m = valid_manifest()
    v = lambda s: [Refusal("tabs[0]", "bad tab")]  # noqa: E731
    with pytest.raises(ManifestRefused) as ei:
        validate(m, host="gnomon", section_validator=v)
    assert ei.value.fields == ["hosts.gnomon.tabs[0]"]


# --------------------------------------------------------------------------- #
# Host-provided requirements: the normalization ([CXC-PC-MANIFEST-FIELDS]).
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["composebus", "COMPOSEBUS", "compose-plugins",
                                  "compose_plugins", "Compose-Plugins", "composeio", "ComposeIO",
                                  "compose.plugins", "Compose..Plugins", "compose-_.plugins"])
def test_host_provided_names_normalize_to_the_list(name):
    from compose_plugins.manifest import HOST_PROVIDED_PACKAGES
    assert normalize_requirement_name(name) in HOST_PROVIDED_PACKAGES


@pytest.mark.parametrize("pin", ["composebus-extra==1.0", "my-composeio==1.0", "compose==1.0"])
def test_near_names_are_not_host_provided(pin):
    m = valid_manifest()
    m["requirements"] = [pin]
    validate(m)


# --------------------------------------------------------------------------- #
# standard_questions with a package ([CXC-PC-STANDARD-QUESTIONS]).
# --------------------------------------------------------------------------- #
def _dir_package(tmp_path):
    (tmp_path / "questions").mkdir()
    (tmp_path / "a_file").write_text("x")
    return PackageSource(kind="directory", path=tmp_path)


def test_standard_questions_existing_directory_passes(tmp_path):
    m = valid_manifest()
    m["standard_questions"] = "questions"
    validate(m, package=_dir_package(tmp_path))


@pytest.mark.parametrize("value", ["missing_dir", "a_file", "/abs/questions", "../questions",
                                   "questions/../../x", "C:questions", "\\abs"])
def test_standard_questions_bad_paths_are_refused_naming_the_path(tmp_path, value):
    m = valid_manifest()
    m["standard_questions"] = value
    with pytest.raises(ManifestRefused) as ei:
        validate(m, package=_dir_package(tmp_path))
    print(ei.value)
    assert ei.value.fields == ["standard_questions"]
    assert repr(value) in ei.value.refusals[0].message


def test_standard_questions_symlink_leaving_the_package_is_refused(tmp_path):
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (pkg / "questions").symlink_to(outside)
    m = valid_manifest()
    m["standard_questions"] = "questions"
    with pytest.raises(ManifestRefused):
        validate(m, package=PackageSource(kind="directory", path=pkg))


# --------------------------------------------------------------------------- #
# Several faults at once: every one is named.
# --------------------------------------------------------------------------- #
def test_all_refusals_are_reported_together():
    m = valid_manifest()
    m["domain"] = "Bad"
    m["version"] = "1"
    m["dependencies"] = []
    assert set(refused_fields(m)) == {"domain", "version", "dependencies"}


def test_non_object_manifest_is_refused():
    assert refused_fields(["not", "an", "object"]) == ["(manifest)"]
