"""composeio.git_sensor — a git repo sync-state SENSOR (the ``git/*`` family).

In HAL terms ([SPEC:CXC-HAL-DECLARE] / [CXC-HALD-ROLE]) this is a SENSOR
peripheral whose namespaces are ``git/*``: a repository instruments its OWN sync
state onto the ComposeBus like any other peer, so a subscriber (the Intelligence
Console, a notifier, or the mermaid widget) can see forgotten / uncommitted /
unpushed work — visually AND as a bus alert.

    GitSensor       the sensor: reads branch, dirty/staged/untracked counts,
                    unpushed/behind vs upstream, stashes, last commit; emits
                    ``git/alert`` (out_of_sync + reasons + human message) and a
                    mermaid ``gitGraph`` on ``git/graph``.

The ``git/graph`` string is valid mermaid ``gitGraph`` source — a first-class
diagram type in mermaid v11 — so it renders UNCHANGED in the already-built
MermaidWidget (bind a mermaid widget to ``git/graph``).

This slice COMPUTES + EXPOSES the publish set (``to_bus_lines``); it does not
open a live bus/MQTT link.  Readable readout::

    python3 -m composeio.git_sensor                    # instrument the CWD repo
    python3 -m composeio.git_sensor /path/to/repo
    python3 -m composeio.git_sensor --selftest
"""
from __future__ import annotations

from .sensor import DEFAULT_MAX_COMMITS, GitSensor

__all__ = ["GitSensor", "DEFAULT_MAX_COMMITS"]
