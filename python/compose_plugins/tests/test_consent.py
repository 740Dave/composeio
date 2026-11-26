"""Consent: content from the manifest alone, granted strings, widening, and the
consent-record store ([CXC-PC-CONSENT-CONTENT], -RECORD, -WIDENING)."""
from __future__ import annotations

import builtins
import importlib
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from compose_plugins.consent import (
    ABSENT, ConsentRecord, ConsentStore, HostPermission, RECORD_KEYS,
    added_grants, granted_set, make_record, render_consent, widens,
)
from compose_plugins.errors import ConsentRefused
from compose_plugins.manifest import NO_PACKAGE, validate_manifest

from conftest import empty_section, valid_manifest

SHA = "a" * 64


def no_perms(section):
    return []


def v(m, host="composeio"):
    return validate_manifest(m, host=host, section_validator=empty_section, package=NO_PACKAGE)


def v2_manifest():
    """The test manifest widened the way hello-world v2 widens it."""
    m = valid_manifest()
    m["version"] = "2.0.0"
    m["bus"]["subscribes"].append({"namespace": "plugins/hello_world/name", "type": "EVENT"})
    return m


V1_GRANTED = [
    "bus.publish:plugins/hello_world/greeting:STATE",
    "bus.publish:plugins/hello_world/pong:EVENT",
    "bus.subscribe:plugins/hello_world/ping:EVENT",
]


# --------------------------------------------------------------------------- #
# Consent content.
# --------------------------------------------------------------------------- #
def test_v1_content_is_exactly_the_expected_list():
    c = render_consent(v(valid_manifest()), host="composeio", runs_as="subprocess",
                       render_host_section=no_perms)
    print(c.text())
    assert [(ln.label, ln.value) for ln in c.lines] == [
        ("name", "Hello World"),
        ("version", "1.0.0"),
        ("description", "Greets on the bus and answers pings with pongs."),
        ("code owners", "@example-user, @example-org/plugins-team (as declared by the "
                        "package; not a verified publisher identity)"),
        ("documentation", "https://example.invalid/hello_world"),
        ("run form", "python"),
        ("runs as", "subprocess"),
        ("code this installs", "(none)"),
        ("publishes", "plugins/hello_world/greeting STATE"),
        ("publishes", "plugins/hello_world/pong EVENT"),
        ("subscribes", "plugins/hello_world/ping EVENT"),
        ("composeio permissions", "(none)"),
    ]
    assert list(c.granted) == V1_GRANTED
    assert c.widens is False and c.added == ()


def test_absent_codeowners_and_absent_bus_are_shown_as_absent():
    m = valid_manifest()
    del m["codeowners"], m["bus"]
    c = render_consent(v(m), host="composeio", runs_as="inprocess", render_host_section=no_perms)
    labels = {ln.label: ln.value for ln in c.lines}
    assert labels["code owners"] == ABSENT
    assert labels["publishes"].startswith(ABSENT) and labels["subscribes"].startswith(ABSENT)
    assert c.granted == ()


def test_requirements_are_listed_one_per_line():
    m = valid_manifest()
    m["requirements"] = ["requests==2.32.3", "idna==3.7"]
    c = render_consent(v(m), host="composeio", runs_as="inprocess", render_host_section=no_perms)
    assert [ln.value for ln in c.lines if ln.label == "code this installs"] == \
        ["requests==2.32.3", "idna==3.7"]


def test_standard_questions_does_not_change_consent_content(tmp_path):
    from compose_plugins.package import PackageSource
    (tmp_path / "q").mkdir()
    pkg = PackageSource(kind="directory", path=tmp_path)
    m1 = valid_manifest()
    m2 = valid_manifest()
    m2["standard_questions"] = "q"
    kw = dict(host="composeio", runs_as="inprocess", render_host_section=no_perms)
    c1 = render_consent(validate_manifest(m1, host="composeio", section_validator=empty_section,
                                          package=pkg), **kw)
    c2 = render_consent(validate_manifest(m2, host="composeio", section_validator=empty_section,
                                          package=pkg), **kw)
    assert c1 == c2


def test_runs_as_below_the_floor_is_refused():
    m = valid_manifest()
    m["isolation_floor"] = "subprocess"
    with pytest.raises(ConsentRefused) as ei:
        render_consent(v(m), host="composeio", runs_as="inprocess", render_host_section=no_perms)
    print(ei.value)
    assert "below" in str(ei.value)
    c = render_consent(v(m), host="composeio", runs_as="subprocess", render_host_section=no_perms)
    assert ("runs as", "subprocess") in [(ln.label, ln.value) for ln in c.lines]


def test_runs_as_must_be_a_known_level():
    with pytest.raises(ConsentRefused):
        render_consent(v(valid_manifest()), host="composeio", runs_as="container",
                       render_host_section=no_perms)


def test_host_section_permissions_render_through_the_hosts_renderer():
    m = valid_manifest()
    m["hosts"]["gnomon"] = {"agents": ["scribe"]}

    def gnomon_renderer(section):
        return [HostPermission(f"agent may call: {a}", f"gnomon:agent:{a}")
                for a in section["agents"]]

    vm = validate_manifest(m, host="gnomon", section_validator=lambda s: [], package=NO_PACKAGE)
    c = render_consent(vm, host="gnomon", runs_as="subprocess", render_host_section=gnomon_renderer)
    assert ("gnomon permissions", "agent may call: scribe") in [(ln.label, ln.value) for ln in c.lines]
    assert "gnomon:agent:scribe" in c.granted
    assert list(c.granted) == granted_set(vm, host="gnomon", render_host_section=gnomon_renderer)


@pytest.mark.parametrize("grant", ["agent:scribe", "composeio:x", "gnomon:"])
def test_host_grant_without_its_host_prefix_is_refused(grant):
    m = valid_manifest()
    vm = validate_manifest(m, host="gnomon", section_validator=lambda s: [], package=NO_PACKAGE)
    with pytest.raises(ConsentRefused) as ei:
        granted_set(vm, host="gnomon",
                    render_host_section=lambda s: [HostPermission("x", grant)])
    print(ei.value)
    assert repr(grant) in str(ei.value)


def test_render_imports_nothing_and_opens_no_socket(monkeypatch):
    m = v(valid_manifest())
    imported = []
    real_import = builtins.__import__

    def spy_import(name, *a, **kw):
        imported.append(name)
        return real_import(name, *a, **kw)

    def no_socket(*a, **kw):
        raise AssertionError("consent rendering opened a socket")

    monkeypatch.setattr(builtins, "__import__", spy_import)
    monkeypatch.setattr(importlib, "import_module",
                        lambda *a, **k: imported.append(a[0]))
    monkeypatch.setattr(socket, "socket", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    render_consent(m, host="composeio", runs_as="inprocess", render_host_section=no_perms)
    assert not [n for n in imported if n.startswith("hello_world")]


# --------------------------------------------------------------------------- #
# Granted strings and widening.
# --------------------------------------------------------------------------- #
def test_granted_strings_are_exactly_as_specified():
    assert granted_set(v(valid_manifest()), host="composeio", render_host_section=no_perms) \
        == V1_GRANTED


def test_granted_is_sorted_and_deduplicated():
    m = valid_manifest()
    vm = v(m)
    g = granted_set(vm, host="composeio",
                    render_host_section=lambda s: [HostPermission("b", "composeio:b"),
                                                   HostPermission("a", "composeio:a"),
                                                   HostPermission("a2", "composeio:a")])
    assert g == sorted(set(g)) and g.count("composeio:a") == 1


def test_v1_to_v2_widens_with_exactly_the_added_subscribe():
    old = granted_set(v(valid_manifest()), host="composeio", render_host_section=no_perms)
    c = render_consent(v(v2_manifest()), host="composeio", runs_as="inprocess",
                       render_host_section=no_perms, previous_granted=old)
    print(c.text())
    assert c.widens is True
    assert c.added == ("bus.subscribe:plugins/hello_world/name:EVENT",)
    assert [ln.value for ln in c.lines if ln.added] == ["plugins/hello_world/name EVENT"]


def test_same_or_narrower_set_does_not_widen():
    """Negative control of the widening rule: it can say NO."""
    old = V1_GRANTED
    assert widens(old, old) is False
    assert widens(old, old[:1]) is False
    assert widens(old, []) is False
    assert added_grants(old, old[:2]) == []


def test_widening_is_not_a_subset_not_a_size_comparison():
    old = ["bus.publish:plugins/d/a:STATE", "bus.publish:plugins/d/b:STATE"]
    new = ["bus.publish:plugins/d/c:STATE"]  # SMALLER, but not a subset
    assert widens(old, new) is True
    assert added_grants(old, new) == new


# --------------------------------------------------------------------------- #
# Consent records and the store.
# --------------------------------------------------------------------------- #
FIXED = datetime(2026, 11, 26, 1, 30, 0, tzinfo=timezone.utc)


def _record(decision="granted", manifest=None, **kw):
    vm = v(manifest or valid_manifest())
    return make_record(manifest=vm, manifest_sha256=kw.pop("sha", SHA),
                       granted=kw.pop("granted", granted_set(vm, host="composeio",
                                                             render_host_section=no_perms)),
                       decided_by=kw.pop("decided_by", "operator:test"), host="composeio",
                       decision=decision, clock=kw.pop("clock", lambda: FIXED))


def test_record_has_exactly_the_specified_keys_and_clock_time():
    r = _record()
    d = r.to_json()
    print(json.dumps(d, indent=1))
    assert tuple(d) == RECORD_KEYS
    assert d["decided_at"] == "2026-11-26T01:30:00Z"
    assert d["granted"] == V1_GRANTED and d["decision"] == "granted"


def test_record_time_comes_from_the_clock_and_is_utc():
    other = datetime(2026, 11, 25, 17, 0, tzinfo=timezone(timedelta(hours=-8)))
    assert _record(clock=lambda: other).decided_at == "2026-11-26T01:00:00Z"
    with pytest.raises(ConsentRefused, match="naive"):
        _record(clock=lambda: datetime(2026, 1, 1))


@pytest.mark.parametrize("kw,needle", [
    ({"decided_by": ""}, "decided_by"),
    ({"sha": "ABC"}, "manifest_sha256"),
    ({"sha": "A" * 64}, "manifest_sha256"),
])
def test_malformed_records_are_refused(kw, needle):
    with pytest.raises(ConsentRefused) as ei:
        _record(**kw)
    print(ei.value)
    assert needle in str(ei.value)


def test_bad_decision_is_refused():
    with pytest.raises(ConsentRefused, match="decision"):
        _record(decision="maybe")


def test_unsorted_granted_tuple_is_refused_at_construction():
    with pytest.raises(ConsentRefused, match="sorted"):
        ConsentRecord(domain="d", version="1.0.0", manifest_sha256=SHA,
                      granted=("b", "a"), decided_by="x", decided_at="t", host="h",
                      decision="granted")


def test_store_keeps_grants_and_refusals_across_reopen(tmp_path):
    path = tmp_path / "consent.jsonl"
    s = ConsentStore(path)
    r1 = _record()
    r2 = _record(decision="refused", manifest=v2_manifest(), granted=V1_GRANTED + [
        "bus.subscribe:plugins/hello_world/name:EVENT"])
    s.append(r1)
    s.append(r2)
    again = ConsentStore(path)
    assert again.records() == [r1, r2]
    assert [r.decision for r in again.records(domain="hello_world")] == ["granted", "refused"]
    assert again.records(domain="other") == []


def test_store_has_no_delete_or_rewrite_operation():
    public = {n for n in dir(ConsentStore) if not n.startswith("_")}
    assert public == {"append", "records"}


def test_store_refuses_a_corrupt_line_naming_it(tmp_path):
    path = tmp_path / "consent.jsonl"
    s = ConsentStore(path)
    s.append(_record())
    with path.open("a") as fh:
        fh.write('{"domain": "x"}\n')
    with pytest.raises(ConsentRefused) as ei:
        s.records()
    print(ei.value)
    assert "line 2" in str(ei.value)


def test_missing_store_has_no_records(tmp_path):
    assert ConsentStore(tmp_path / "none.jsonl").records() == []
