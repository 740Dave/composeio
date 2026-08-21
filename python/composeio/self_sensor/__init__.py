"""composeio.self_sensor — the Intelligence self-instrumentation SENSOR.

In HAL terms ([SPEC:CXC-HAL-DECLARE] / [CXC-HALD-ROLE]) this is a SENSOR
peripheral whose namespaces are ``self/*``: an Intelligence instruments ITSELF
onto the ComposeBus like any other peer, so the Intelligence Console can read
its own context level, registration green-light, subagent ledger, cost and
rate-limit headroom.

    SelfSensor      the sensor: statusline (stdin) PRIMARY, session .jsonl
                    FALLBACK — the native ``used_percentage`` wins over the
                    JSONL estimate; adds self/cost, self/rate_limit, self/effort
    SessionSensor   the preserved JSONL engine (the original prototype)
    REGISTRATION_MARKERS   files whose Read flips self/registered true

This slice COMPUTES + EXPOSES the publish set (``to_bus_lines``); it does not
open a live bus/MQTT link.  Readable readout::

    python3 -m composeio.self_sensor              # auto: stdin or JSONL default
    some-statusline-json | python3 -m composeio.self_sensor
    python3 -m composeio.self_sensor path/to/session.jsonl
    python3 -m composeio.self_sensor --selftest
"""
from __future__ import annotations

from .sensor import (
    DEFAULT_CONTEXT_WINDOW,
    REGISTRATION_MARKERS,
    SelfSensor,
    SessionSensor,
    load_registration_manifest,
)

__all__ = [
    "SelfSensor",
    "SessionSensor",
    "REGISTRATION_MARKERS",
    "DEFAULT_CONTEXT_WINDOW",
    "load_registration_manifest",
]
