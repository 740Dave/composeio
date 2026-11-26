"""DOM-V9 — validation is SERVER-side, reusing the loader's REAL pydantic
evaluator ([CXC-DOM-SERVER-VALIDATION]); no client-side JS re-derivation.

Proves the property the JS tests can only stub: a value that only the SERVER'S
pydantic model can reject (a type error, a cross-field constraint, an enum
membership the DOM control could let through) is rejected here, in the SAME
``config:<field>: <message>`` taxonomy [CXC-CSL-ERROR-TAXONOMY] defines, one
item per invalid field — exactly as [CXC-CSL-CONFIG-VALIDATION]'s CSL-V1 row
requires of the loader.
"""

from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel, model_validator

from composeio.render.dom.validation import (validate_config,
                                             validate_config_result)


class SampleConfig(BaseModel):
    """Stands in for a plugin's ``Config`` model — the SAME kind of pydantic
    model the loader validates ([CXC-CSL-CONFIG-VALIDATION])."""
    name: str
    port: int
    mode: str = "auto"
    replicas: Optional[int] = None

    @model_validator(mode="after")
    def _cross_field(self):
        # a CROSS-FIELD constraint the DOM control set cannot express — the
        # case [CXC-DOM-SERVER-VALIDATION]'s verify-machine row names.
        if self.mode == "ha" and (self.replicas or 0) < 2:
            raise ValueError("mode 'ha' requires replicas >= 2")
        return self


def test_valid_config_passes():
    assert validate_config(SampleConfig, {"name": "svc", "port": 8080}) == []
    res = validate_config_result(SampleConfig, {"name": "svc", "port": 8080})
    assert res["ok"] is True
    assert res["errors"] == []


def test_single_type_error_is_one_config_field_item():
    # port fed a non-integer string the browser number hint could not have
    # caught if the field were rendered as text.
    unmet = validate_config(SampleConfig, {"name": "svc", "port": "not-an-int"})
    assert len(unmet) == 1
    assert unmet[0].startswith("config:port: ")


def test_two_field_failure_yields_two_items():
    unmet = validate_config(SampleConfig, {"port": "x"})  # missing name + bad port
    fields = sorted(item.split(":", 2)[1] for item in unmet)
    assert fields == ["name", "port"]
    assert len(unmet) == 2


def test_cross_field_constraint_rejected_server_side():
    # a config every FIELD-level DOM control would accept, rejected only by the
    # model's cross-field rule — proving the server check is authoritative.
    res = validate_config_result(SampleConfig,
                                 {"name": "svc", "port": 1, "mode": "ha", "replicas": 1})
    assert res["ok"] is False
    assert any("replicas >= 2" in e["msg"] for e in res["errors"])


def test_result_shape_splits_field_and_message():
    res = validate_config_result(SampleConfig, {"name": "svc", "port": "x"})
    assert res["ok"] is False
    assert res["errors"][0]["field"] == "port"
    assert res["errors"][0]["msg"]
    # raw taxonomy string is also carried for a config:-prefix-parsing peer
    assert res["unmet"][0].startswith("config:port: ")


def test_no_client_reimpl_marker():
    """Structural guard for the 'never re-implement pydantic in JS' rule
    ([CXC-DOM-SERVER-VALIDATION]): the JS runtime ships no validation of field
    types.  A grep over the static JS must find no pydantic/ValidationError
    re-derivation.  (The presentation-layer min/max HINTS are allowed.)"""
    import os
    import re
    static = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                          "composeio", "render", "dom", "static")

    def strip(src: str) -> str:
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
        src = re.sub(r"(?<!:)//.*", "", src)
        return src.lower()

    hits = []
    for fn in os.listdir(static):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(static, fn), encoding="utf-8") as f:
            src = strip(f.read())
        # actual validation-logic re-derivation in CODE (not the comments that
        # document the "never re-implement pydantic in JS" rule).
        for needle in ("validationerror", "pydantic"):
            if needle in src:
                hits.append(f"{fn}:{needle}")
    assert hits == [], f"JS appears to re-derive validation logic: {hits}"
