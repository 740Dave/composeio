"""[CXC-PC-VERSION-RULE]: same MAJOR at or below the host's MINOR is accepted;
anything else is refused naming BOTH versions. [CXC-PC-NC-CONTRACT-MISMATCH]."""
from __future__ import annotations

import pytest

from compose_plugins.errors import ContractVersionRefused, ManifestRefused
from compose_plugins.manifest import NO_PACKAGE, validate_manifest
from compose_plugins.version import HOST_CONTRACT, check_contract, parse_contract

from conftest import empty_section, valid_manifest


def test_host_contract_is_0_2():
    # contract 0.2 ([CXC-PC-V02-VERSION-0-2] rule (1)); was 0.1 through r24
    assert HOST_CONTRACT == "0.2"


# ---- positive controls ----------------------------------------------------- #
@pytest.mark.parametrize("ver", ["0.1", "0.0"])
def test_same_major_at_or_below_minor_is_accepted(ver):
    check_contract(ver, "0.1")


def test_lower_minor_on_a_later_host_is_accepted():
    check_contract("1.0", "1.3")
    check_contract("1.3", "1.3")


def test_validator_accepts_0_0_manifest_on_0_1_host():
    m = valid_manifest()
    m["contract"] = "0.0"
    validate_manifest(m, host="composeio", section_validator=empty_section,
                      package=NO_PACKAGE)


# ---- negative controls ----------------------------------------------------- #
@pytest.mark.parametrize("ver,host", [("0.2", "0.1"), ("1.0", "0.1"), ("0.1", "1.0"),
                                      ("1.4", "1.3"), ("2.0", "1.3")])
def test_newer_minor_or_other_major_is_refused_naming_both(ver, host):
    with pytest.raises(ContractVersionRefused) as ei:
        check_contract(ver, host)
    text = str(ei.value)
    print(text)
    assert f"contract {ver}" in text and f"contract {host}" in text
    assert ei.value.manifest == ver and ei.value.host == host


@pytest.mark.parametrize("bad", [0.1, 1, None, True, ["0.1"], "", "0", "0.1.0", "v0.1",
                                 "01.1", "0.01", " 0.1", "0.1 ", "0.x"])
def test_malformed_or_non_string_contract_is_refused_naming_the_value(bad):
    with pytest.raises(ContractVersionRefused) as ei:
        check_contract(bad, "0.1")
    print(ei.value)
    assert repr(bad) in str(ei.value)


def test_missing_contract_is_refused():
    with pytest.raises(ContractVersionRefused, match="missing"):
        check_contract()
    m = valid_manifest()
    del m["contract"]
    with pytest.raises(ManifestRefused) as ei:
        validate_manifest(m, host="composeio", section_validator=empty_section,
                          package=NO_PACKAGE)
    assert ei.value.fields == ["contract"]


def test_parse_contract():
    assert parse_contract("0.1") == (0, 1)
    assert parse_contract("12.30") == (12, 30)


def test_nc_contract_mismatch_through_the_validator_is_the_only_refusal():
    """A newer-MINOR manifest is never half-read: its unknown keys are NOT also
    reported, only the contract, with both versions."""
    m = valid_manifest()
    m["contract"] = "0.3"                       # one MINOR newer than a 0.2 host
    m["some_0_3_field"] = True
    with pytest.raises(ManifestRefused) as ei:
        validate_manifest(m, host="composeio", section_validator=empty_section,
                          package=NO_PACKAGE)
    print(ei.value)
    assert ei.value.fields == ["contract"]
    assert "0.3" in str(ei.value) and "0.2" in str(ei.value)
