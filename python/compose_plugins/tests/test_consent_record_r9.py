"""Spec 95 r9, [CXC-PC-CONSENT-RECORD] granted-format-r9: every record text
field obeys BOTH the blank rule and the one-line rule (QA E2: a forged second
line in the audit trail); granted holds only the r9 forms; changing
isolation_floor in EITHER direction does not widen. Each test cites r9."""
from __future__ import annotations

import json

import pytest

from compose_plugins.consent import (
    RECORD_TEXT_FIELDS, ConsentRecord, ConsentStore, HostPermission, grant_problem,
    granted_set, make_record, render_consent, widens,
)
from compose_plugins.errors import ConsentRefused
from compose_plugins.manifest import LINE_BREAK_RANGES, NO_PACKAGE, validate_manifest

from conftest import empty_section, valid_manifest

SHA = "c" * 64
#: the FULL frozen line-break set, one code point each
SEPARATORS = [chr(c) for a, b in LINE_BREAK_RANGES for c in range(a, b + 1)]
GOOD = dict(domain="hello_world", version="1.0.0", manifest_sha256=SHA, granted=(),
            decided_by="operator:dave", decided_at="2026-11-26T02:00:00Z",
            host="composeio", decision="granted")


def _vm(m=None):
    return validate_manifest(m or valid_manifest(), host="composeio",
                             section_validator=empty_section, package=NO_PACKAGE)


def test_r9_the_separator_set_is_the_full_frozen_set():
    """[r9] \\n, \\r, U+2028, U+2029, U+0085 and the rest of the frozen set."""
    assert {"\n", "\r", " ", " ", "\u0085"} <= set(SEPARATORS)
    assert len(SEPARATORS) == 10


def test_r9_record_text_fields_are_every_string_field():
    """[r9] the checked fields are every string field of the record."""
    assert set(RECORD_TEXT_FIELDS) == {"domain", "version", "manifest_sha256", "decided_by",
                                       "decided_at", "host", "decision"}


@pytest.mark.parametrize("sep", SEPARATORS, ids=[f"U+{ord(s):04X}" for s in SEPARATORS])
@pytest.mark.parametrize("field", RECORD_TEXT_FIELDS)
def test_r9_every_text_field_refuses_every_separator(field, sep):
    """[r9] a separator anywhere in any text field is refused, naming the field."""
    kw = dict(GOOD)
    kw[field] = f"{kw[field]}{sep}decided_by: HoP"
    with pytest.raises(ConsentRefused) as ei:
        ConsentRecord(**kw)
    assert field in str(ei.value)


@pytest.mark.parametrize("sep", SEPARATORS, ids=[f"U+{ord(s):04X}" for s in SEPARATORS])
def test_r9_one_line_message_for_decided_by(sep):
    """[r9] the refusal says ONE line, not only 'invalid'."""
    kw = dict(GOOD, decided_by=f"agent:x{sep}decided_by: HoP")
    with pytest.raises(ConsentRefused) as ei:
        ConsentRecord(**kw)
    print(ei.value)
    assert "decided_by must be ONE line" in str(ei.value)


@pytest.mark.parametrize("field", RECORD_TEXT_FIELDS)
@pytest.mark.parametrize("blank", ["", "  ", "​", "﻿ ⁠", "­"])
def test_r9_every_text_field_refuses_blank(field, blank):
    """[r9] the N3 blank rule on every text field."""
    kw = dict(GOOD)
    kw[field] = blank
    with pytest.raises(ConsentRefused) as ei:
        ConsentRecord(**kw)
    assert field in str(ei.value)


def test_r9_positive_control_good_record_and_inner_spaces():
    """[r9] a good record passes; a plain space or tab is not a line break."""
    ConsentRecord(**GOOD)
    ConsentRecord(**dict(GOOD, decided_by="operator: dave\tb"))


def test_r9_forged_line_never_reaches_the_store(tmp_path):
    """[r9] QA E2's exact attack: make_record refuses, so nothing is appended."""
    store = ConsentStore(tmp_path / "consent.jsonl")
    for kw in ({"decided_by": "agent:x\ndecided_by: HoP"}, {"host": "composeio gnomon"}):
        args = dict(manifest=_vm(), manifest_sha256=SHA, granted=[], decided_by="op",
                    host="composeio", decision="granted")
        args.update(kw)
        with pytest.raises(ConsentRefused) as ei:
            store.append(make_record(**args))
        print(ei.value)
    assert store.records() == []


def test_r9_a_forged_line_written_into_the_file_is_refused_on_read(tmp_path):
    """[r9] a record line edited on disk to carry a separator is refused at read."""
    path = tmp_path / "consent.jsonl"
    ConsentStore(path).append(ConsentRecord(**GOOD))
    d = json.loads(path.read_text())
    d["decided_by"] = "agent:x decided_by: HoP"
    path.write_text(json.dumps(d) + "\n")
    with pytest.raises(ConsentRefused) as ei:
        ConsentStore(path).records()
    print(ei.value)
    assert "line 1" in str(ei.value) and "ONE line" in str(ei.value)


# ---- granted forms --------------------------------------------------------- #
@pytest.mark.parametrize("grant", [
    "bus.publish:plugins/hello_world/greeting:STATE",
    "bus.subscribe:plugins/hello_world/ping:EVENT",
    "code:requests-oauthlib==2.0.0", "code:idna==3.7",
    "composeio:anything here",
])
def test_r9_granted_r9_forms_pass(grant):
    """[r9] the three forms plus <host>: are accepted."""
    assert grant_problem(grant, "composeio") is None
    ConsentRecord(**dict(GOOD, granted=(grant,)))


@pytest.mark.parametrize("grant", [
    "bus.publish:plugins/hello_world/greeting:HEARTBEAT",   # reserved type
    "bus.publish:plugins/hello_world/*:STATE",              # wildcard
    "bus.read:plugins/hello_world/x:STATE",                 # v0.2 form
    "bus.publish:_server/x:STATE",
    "code:Requests==2.0",                                   # not PEP 503 normalized
    "code:compose_plugins==0.1.0",                          # not PEP 503 normalized
    "code:idna>=3.7", "code:idna", "code:",
    "gnomon:agent:x",                                       # another host's grant
    "composeio:",                                           # empty permission
    "freeform", "",
    "composeio:x\nbus.publish:plugins/hello_world/y:STATE",  # forged second line
])
def test_r9_non_r9_grants_are_refused(grant):
    """[r9] anything else in granted is refused, naming it."""
    assert grant_problem(grant, "composeio") is not None
    with pytest.raises(ConsentRefused) as ei:
        ConsentRecord(**dict(GOOD, granted=(grant,)))
    print(ei.value)


def test_r9_granted_set_emits_only_r9_forms():
    """[r9] what the library itself derives is only the r9 forms."""
    m = valid_manifest()
    m["requirements"] = ["Requests_OAuthlib==2.0.0", "idna==3.7"]
    m["hosts"]["gnomon"] = {}
    vm = validate_manifest(m, host="gnomon", section_validator=lambda s: [], package=NO_PACKAGE)
    g = granted_set(vm, host="gnomon",
                    render_host_section=lambda s: [HostPermission("agent", "gnomon:agent:x")])
    print(g)
    assert len(g) == 6
    assert all(grant_problem(x, "gnomon") is None for x in g)
    assert {x.split(":")[0] for x in g} == {"bus.publish", "bus.subscribe", "code", "gnomon"}


# ---- isolation_floor: r12 floor rule (supersedes the r9 sentence) --------- #
def _floor_upgrade(old_floor, new_floor, old_runs_as, new_runs_as):
    m1, m2 = valid_manifest(), valid_manifest()
    m1["isolation_floor"], m2["isolation_floor"] = old_floor, new_floor
    g1 = granted_set(_vm(m1), host="composeio", render_host_section=lambda s: [])
    return render_consent(_vm(m2), host="composeio", runs_as=new_runs_as,
                          render_host_section=lambda s: [], previous_granted=g1,
                          previous_runs_as=old_runs_as)


def test_r12_raising_the_floor_never_widens():
    """[r12] raising isolation_floor never widens; runs as is shown."""
    c = _floor_upgrade("inprocess", "subprocess", "inprocess", "subprocess")
    assert c.widens is False and c.added == ()
    assert ("runs as", "subprocess") in [(ln.label, ln.value) for ln in c.lines]


def test_r12_lowering_the_floor_widens_when_the_actual_level_drops():
    """[r12] lowering the floor so the host's actual level drops WIDENS, and
    the screen marks the drop."""
    c = _floor_upgrade("subprocess", "inprocess", "subprocess", "inprocess")
    print(c.text())
    assert c.widens is True and c.added == ()
    assert [ln.text() for ln in c.lines if ln.added] == [
        "[ADDED] runs as: inprocess (was subprocess: LESS isolation than before)"]


def test_r12_lowering_the_floor_on_a_subprocess_only_host_does_not_widen():
    """[r12] a host that always runs at subprocess (Gnomon) is unaffected."""
    c = _floor_upgrade("subprocess", "inprocess", "subprocess", "subprocess")
    assert c.widens is False
