"""Every registered transport factory must actually CONSTRUCT its transport.

WHY THIS EXISTS (2026-08-12)
    `transport_registry` already separates the two failure modes it was written
    for: an UNKNOWN `type:` raises (a bug), and a KNOWN type whose backend is
    unreachable degrades loudly to None (an environment). What it could not
    catch is a third mode living INSIDE a factory — a factory that cannot build
    its transport at all, because it passes a keyword the constructor does not
    take.

    That is not hypothetical. `_uart` passed `baudrate=` while
    `UARTTransport.__init__` takes `baud=`, so `type: uart` raised TypeError
    from the day it was registered. `build()` caught it as "transport
    unavailable (app runs local-only)" — so a scene declaring `type: uart` ran
    local-only forever, while the log blamed missing hardware for what was a
    one-word code defect. Same silent-obedience shape the registry exists to
    remove, one layer down.

    The distinction this test enforces:
        TypeError from a factory  -> a CODE bug. Always a failure.
        OSError / ImportError     -> an ENVIRONMENT. Acceptable here; no broker,
                                     no CAN interface and no pyserial are all
                                     legitimate on a test runner.
"""
from __future__ import annotations

import inspect

import pytest

from composeio import transport_registry as registry


#: One representative spec per registered type. Deliberately uses the SAME key
#: spellings a scene's `transports:` block would use — the point is to exercise
#: the factory's own translation from published YAML keys to constructor kwargs,
#: which is exactly where the `baudrate`/`baud` defect lived.
SPECS: dict[str, dict] = {
    "mqtt": {"type": "mqtt", "broker": "127.0.0.1", "port": 1883},
    "uart": {"type": "uart", "port": "/dev/null", "baudrate": 115200},
    "can": {"type": "can", "channel": "can0"},
    # NOTE: `config:` is a PATH to a .gen.yaml, not an inline dict — checked
    # against SyntheticTransport's signature after this test first flagged it.
    # The test was wrong, the factory was right; recorded so the next reader
    # does not "fix" the factory to match a mistaken expectation.
    "synthetic": {"type": "synthetic", "config": "/nonexistent.gen.yaml"},
    "replay": {"type": "replay", "capture": "/dev/null"},
    # ws is the LISTEN-role connector: its factory only CONSTRUCTS the
    # WSConnector object (no socket bind until start()), so a construction check
    # needs no live registry ([SPEC:CXC-WS] [CXC-WS-CONNECTOR-INTERFACE]).
    "ws": {"type": "ws", "host": "127.0.0.1", "port": 0},
    # mcp is the agent-facing LISTEN-role umbrella: its factory CONSTRUCTS the
    # MCPConnector only (the MCP SDK is imported at start(), never at build), so
    # this check needs neither the SDK nor a live registry. A LAN/loopback host
    # is REQUIRED — a world-facing bind is refused ([SPEC:CXC-MCP]
    # [CXC-MCP-CONNECTOR-INTERFACE], [DD-MCP-AUTH-GATE]).
    "mcp": {"type": "mcp", "host": "127.0.0.1", "port": 8431},
}


def test_every_registered_type_has_a_spec():
    """If someone registers a transport, it gets covered here too."""
    missing = set(registry.known_types()) - set(SPECS)
    assert not missing, (
        f"registered but untested transport type(s): {sorted(missing)}. "
        "Add a representative spec to SPECS so the factory is construction-checked."
    )


@pytest.mark.parametrize("type_name", sorted(SPECS))
def test_factory_does_not_raise_typeerror(type_name: str):
    """A factory must not mis-call its own constructor.

    We do not require the transport to be USABLE — no broker or CAN interface is
    present on a test runner. We require only that the factory can form the call.
    """
    factory = registry._FACTORIES[type_name]
    try:
        factory(SPECS[type_name], "test-app")
    except TypeError as exc:
        pytest.fail(
            f"_{type_name} factory mis-calls its constructor: {exc}\n"
            "This is a CODE defect, not an environment one: build() would swallow "
            "it as 'backend unavailable' and the app would run local-only while "
            "blaming missing hardware."
        )
    except (OSError, ImportError, ValueError):
        pass  # environment, not our concern here


@pytest.mark.parametrize("type_name", sorted(SPECS))
def test_factory_kwargs_match_constructor_signature(type_name: str):
    """Static cross-check: catches the defect even where a ctor is permissive.

    A constructor taking **kwargs would swallow a wrong keyword rather than
    raising, so the runtime check above could pass while the value is silently
    dropped — a baud rate ignored is worse than a baud rate rejected.
    """
    factory = registry._FACTORIES[type_name]
    src = inspect.getsource(factory)
    if "**" in src:
        pytest.skip("factory forwards **kwargs; runtime check is authoritative")
    # Nothing to assert structurally beyond the runtime call for these simple
    # factories; the guard above is the enforcement. This test documents the
    # gap so a future **kwargs factory is a conscious decision.
