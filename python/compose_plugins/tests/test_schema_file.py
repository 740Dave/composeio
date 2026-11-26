"""The shipped JSON Schema ([CXC-PC-MANIFEST-FILE]): valid Draft 2020-12, it
accepts the valid manifest, it agrees with the validator on every boundary case
a JSON Schema can express, and every field it declares is exercised
([CXC-PC-RULE-SIZE] schema-coverage check)."""
from __future__ import annotations

import pytest

jsonschema = pytest.importorskip("jsonschema")

from compose_plugins.manifest import (  # noqa: E402
    BUS_FIELDS, CORE_FIELDS, OPTIONAL_FIELDS, REQUIRED_FIELDS, GENERATED_FIELDS,
    RUN_FIELDS, schema,
)
from compose_plugins.version import HOST_CONTRACT  # noqa: E402

from conftest import valid_manifest  # noqa: E402
from test_manifest import BOUNDARY, PERMISSION_VALIDATOR_ONLY  # noqa: E402

#: boundary cases the schema CANNOT express; the validator alone refuses them
VALIDATOR_ONLY = {
    "bus-namespace-other-domain",       # own-namespace needs this manifest's domain
    "bus-namespace-domain-prefix-trick",
    "hosts-own-section-invalid",        # the installing host validates its section
    "standard-questions-no-package",    # existence needs the package
} | PERMISSION_VALIDATOR_ONLY           # r21: RFC 5952, 4096 bytes, duplicates


def validator():
    s = schema()
    jsonschema.Draft202012Validator.check_schema(s)
    return jsonschema.Draft202012Validator(s)


def test_schema_is_valid_draft_2020_12_and_accepts_the_valid_manifest():
    errors = list(validator().iter_errors(valid_manifest()))
    assert errors == []


def test_schema_carries_the_contract_version():
    s = schema()
    assert s["x-compose-plugins-contract"] == HOST_CONTRACT
    assert s["properties"]["contract"]["enum"] == ["0.0", "0.1"]


def test_schema_field_set_equals_the_validators():
    s = schema()
    assert set(s["properties"]) == set(CORE_FIELDS)
    assert set(s["required"]) == set(REQUIRED_FIELDS) | set(GENERATED_FIELDS)
    assert set(s["properties"]["run"]["properties"]) == set(RUN_FIELDS)
    assert set(s["properties"]["bus"]["properties"]) == set(BUS_FIELDS)
    assert s["additionalProperties"] is False


@pytest.mark.parametrize("case_id,mutate,field",
                         [b for b in BOUNDARY if b[0] not in VALIDATOR_ONLY],
                         ids=[b[0] for b in BOUNDARY if b[0] not in VALIDATOR_ONLY])
def test_schema_also_rejects_each_expressible_boundary_case(case_id, mutate, field):
    m = valid_manifest()
    mutate(m)
    errors = list(validator().iter_errors(m))
    assert errors, f"schema ACCEPTED boundary case {case_id}"


@pytest.mark.parametrize("case_id", sorted(VALIDATOR_ONLY))
def test_validator_only_cases_really_pass_the_schema(case_id):
    """Keeps VALIDATOR_ONLY honest: if the schema starts catching one, move it."""
    mutate = {b[0]: b[1] for b in BOUNDARY}[case_id]
    m = valid_manifest()
    mutate(m)
    assert list(validator().iter_errors(m)) == []


def test_every_schema_field_is_exercised():
    """[CXC-PC-RULE-SIZE]: each schema field appears in the valid manifest or
    in a boundary case; standard_questions by its own boundary tests."""
    exercised = set(valid_manifest())
    for case_id, mutate, field in BOUNDARY:
        exercised.add(field.split(".")[0].split("[")[0])
    missing = set(schema()["properties"]) - exercised
    assert missing == set(), f"schema fields exercised by nothing: {missing}"
    assert "standard_questions" in exercised and "standard_questions" in OPTIONAL_FIELDS


def test_schema_coverage_check_can_fail(monkeypatch):
    """Negative control of the coverage check: a schema field nothing
    exercises is reported."""
    s = schema()
    s["properties"]["frobnicate"] = {"type": "string"}
    exercised = set(valid_manifest()) | {b[2].split(".")[0].split("[")[0] for b in BOUNDARY}
    assert set(s["properties"]) - exercised == {"frobnicate"}
