"""Contract 0.2: one shipped JSON Schema per contract, chosen by the manifest's
own ``contract`` (coordinator ruling on mismatch (5) of the 0.2 build: a
schema file named 0.1 must not report 0.2).

``schema/manifest-0.1.schema.json`` stays byte-identical to compose-plugins
0.1.0, so 0.1 manifests still validate against it; ``manifest-0.2.schema.json``
is the 0.2 schema (with ``license`` and ``eula``).
"""
from __future__ import annotations

import hashlib
import json
from importlib import resources

import pytest

jsonschema = pytest.importorskip("jsonschema")

import compose_plugins.manifest as M  # noqa: E402

from conftest import valid_manifest  # noqa: E402

#: sha256 of schema/manifest-0.1.schema.json as shipped in compose-plugins
#: 0.1.0 (main ad5e168)
V010_SCHEMA_SHA256 = "39a3bd09eab9f0d6dad2854ea5798bb60533dd8a98ef8243dd4a271cd6abd31a"


def _file(name: str) -> bytes:
    return resources.files("compose_plugins.schema").joinpath(name).read_bytes()


def _errors(schema: dict, manifest: dict) -> list:
    jsonschema.Draft202012Validator.check_schema(schema)
    return list(jsonschema.Draft202012Validator(schema).iter_errors(manifest))


def _v02(**extra) -> dict:
    m = valid_manifest()
    m["contract"] = "0.2"
    m.update(extra)
    return m


def test_the_0_1_schema_file_is_byte_identical_to_0_1_0():
    raw = _file("manifest-0.1.schema.json")
    assert hashlib.sha256(raw).hexdigest() == V010_SCHEMA_SHA256
    s = json.loads(raw)
    assert s["x-compose-plugins-contract"] == "0.1"
    assert "license" not in s["properties"] and "eula" not in s["properties"]


def test_the_0_2_schema_file_is_shipped_and_reports_0_2():
    s = json.loads(_file("manifest-0.2.schema.json"))
    assert s["x-compose-plugins-contract"] == "0.2"
    assert s["properties"]["contract"]["enum"] == ["0.0", "0.1", "0.2"]
    assert "license" in s["properties"] and "eula" in s["properties"]


@pytest.mark.parametrize("contract", ["0.1", "0.2"])
def test_each_shipped_file_is_exactly_its_generated_schema(contract):
    """define-once ([ruling S1]) for every shipped contract."""
    assert _file(M.SCHEMA_RESOURCES[contract]).decode("utf-8") == \
        M.render_schema_file(contract)


def test_a_0_1_manifest_validates_against_the_0_1_file():
    m = valid_manifest()
    assert m["contract"] == "0.1"
    assert _errors(M.schema("0.1"), m) == []
    assert M.schema_for(m)["x-compose-plugins-contract"] == "0.1"
    assert _errors(M.schema_for(m), m) == []


@pytest.mark.parametrize("extra", [{"license": "MIT"}, {"eula": "EULA.txt"},
                                   {"license": "Apache-2.0", "eula": "legal/EULA.txt"}],
                         ids=["license", "eula", "both"])
def test_a_0_2_manifest_with_license_or_eula_validates_against_0_2_and_fails_against_0_1(extra):
    m = _v02(**extra)
    assert _errors(M.schema("0.2"), m) == []
    assert M.schema_for(m)["x-compose-plugins-contract"] == "0.2"
    assert _errors(M.schema_for(m), m) == []
    errs = _errors(M.schema("0.1"), m)
    assert errs, "the 0.1 schema accepted a 0.2 manifest"
    print([e.message for e in errs])


def test_selection_is_by_the_manifests_own_contract():
    assert M.schema_for({"contract": "0.0"})["x-compose-plugins-contract"] == "0.1"
    assert M.schema_for({"contract": "0.1"})["x-compose-plugins-contract"] == "0.1"
    assert M.schema_for({"contract": "0.2"})["x-compose-plugins-contract"] == "0.2"
    # no contract, or one this library does not ship: the host's own schema,
    # whose contract enum then refuses it
    for m in ({}, {"contract": "0.3"}, {"contract": 2}):
        s = M.schema_for(m)
        assert s["x-compose-plugins-contract"] == M.HOST_CONTRACT
    assert M.schema() == M.schema(M.HOST_CONTRACT)
    with pytest.raises(ValueError):
        M.schema("0.3")
