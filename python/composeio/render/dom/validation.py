"""Server-side config validation for the ComposeUX DOM backend
([CXC-DOM-SERVER-VALIDATION]).

The form's buffered ``{field: value}`` submission is validated HERE, on the
server, against the SAME pydantic ``Config`` model the ComposeServer plugin
loader validates against ([CXC-CSL-CONFIG-VALIDATION]) — reused, NOT a second,
independent validation code path, and NEVER re-implemented in client-side JS.
The whole point ([CXC-CS-RULE-CONFIG-UX] "validation is the real evaluator") is
that a config that validates is a config that will actually run the same way,
which is only true if there is ONE evaluator.

That one evaluator is ``composeio.composeserver_loader.validate_config`` — the
LANDED loader's own function. This module IMPORTS it (it does not re-derive the
``config_model(**payload)`` call or the ``config:<field>: <message>`` taxonomy)
and layers only the wire-shaping the DOM form consumes. When the loader changes
its validation, this changes with it — no drift.
"""

from __future__ import annotations

from typing import Any, Dict, List

# THE ONE validation path ([CXC-CSL-CONFIG-VALIDATION]) — imported from the
# landed loader, never re-implemented here ([CXC-DOM-SERVER-VALIDATION]).
from composeio.composeserver_loader import validate_config

__all__ = ["validate_config", "validate_config_result"]


def validate_config_result(config_model: Any,
                           payload: Dict[str, Any]) -> Dict[str, Any]:
    """The wire-shaped result the DOM form consumes ([CXC-DOM-SERVER-VALIDATION]):
    ``{"ok": bool, "errors": [{"field", "msg"}], "unmet": ["config:<field>: ..."]}``.

    ``errors`` carries the field/message split the browser displays inline;
    ``unmet`` carries the raw taxonomy strings straight from the shared
    ``validate_config`` ([CXC-CSL-ERROR-TAXONOMY]), so a peer that already parses
    the loader's ``config:`` prefix reads them unchanged.
    """
    unmet = validate_config(config_model, payload)
    errors: List[Dict[str, str]] = []
    for item in unmet:
        # split "config:<field>: <message>"
        body = item[len("config:"):] if item.startswith("config:") else item
        field, _, msg = body.partition(": ")
        errors.append({"field": field, "msg": msg})
    return {"ok": not unmet, "errors": errors, "unmet": unmet}
