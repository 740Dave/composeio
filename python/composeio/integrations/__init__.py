"""composeio.integrations — thin, reusable bridges to external services.

Each bridge is a small, dependency-light module that (a) exposes a pure
callable API and (b) offers a runner-tool factory so a render surface can
invoke it by name over the ``EngineContext.invoke`` seam (the fleetpower
pattern).  Secrets are ALWAYS resolved by reference (env / scene config),
never inlined, and never logged.
"""

from .homeassistant import (
    HAError,
    ha_call,
    get_state,
    make_invoke,
    ha_runner_tools,
    resolve_credentials,
    toggle,
    turn_off,
    turn_on,
)

__all__ = [
    "HAError",
    "ha_call",
    "get_state",
    "make_invoke",
    "ha_runner_tools",
    "resolve_credentials",
    "toggle",
    "turn_off",
    "turn_on",
]
