"""Config validated BEFORE instantiation ([CXC-PC-LIFECYCLE-CONFIG]).

The same call and the same ``config:<field>: <message>`` park taxonomy as spec
87's ``validate_config`` ([CXC-CSL-CONFIG-VALIDATION]): construct
``Config(**payload)`` directly. Kept here because compose_plugins must not
depend on composeio; ``tests/test_wave2_*`` checks the two produce identical
reasons.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError


def config_errors(config_model, payload: Any) -> "list[str]":
    if not isinstance(payload, dict):
        return [f"config:<root>: expected an object, got {type(payload).__name__}"]
    try:
        config_model(**payload)
        return []
    except ValidationError as exc:
        out = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            out.append(f"config:{loc}: {err.get('msg', 'invalid')}")
        return out
