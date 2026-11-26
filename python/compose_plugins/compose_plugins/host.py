"""The ``Host`` protocol each host implements ([CXC-PC-LIBRARY-CONTENTS]).

Members: the consent GATE, the AUTH hook ([CXC-PC-UI-HOST-HOOKS]), the install
root, the chosen isolation level (>= the plug-in's floor,
[CXC-PC-HOST-ISOLATION-CHOICE]), the bus the plug-ins run on (optional,
[CXC-PC-RULE-BUS-OPTIONAL]), the status sink, its own section's validator and
renderer ([CXC-PC-HOST-SECTIONS]) and ``extra_panels``. Run/stop go through the
library's runtimes (in-process, or the subprocess runner) at the level the host
chose. The host's name is checked against the host-name rule at construction.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from .consent import ConsentContent, HostPermission
from .errors import PluginContractRefusal, Refusal
from .manifest import ISOLATION_LEVELS, check_host_name
from .version import HOST_CONTRACT


@dataclass(frozen=True)
class ConsentRequest:
    """What the gate decides on. ``accepted`` and ``operator`` carry the
    operator's affirmative act (a UI click or a CLI ``--consent``); a gate that
    waits for a person (Gnomon) ignores them and asks."""

    kind: str                      # "install" | "upgrade"
    domain: str
    version: str
    manifest_sha256: str
    content: ConsentContent
    operator: "str | None"
    accepted: bool


@dataclass(frozen=True)
class ConsentDecision:
    decision: str                  # "granted" | "refused"
    decided_by: str


@dataclass(frozen=True)
class Panel:
    """An extra Plugin Manager panel: rendered under the SAME prefix and auth.
    ``render(environ, identity)`` returns an HTML fragment."""

    slug: str
    title: str
    render: Callable[[dict, str], str]


class Host:
    """Base class of a host. Subclasses override the protocol members."""

    contract = HOST_CONTRACT
    #: the auth hook: environ -> operator identity, or None to deny. A host
    #: that leaves it None cannot construct the Plugin Manager UI.
    auth_hook: "Callable[[dict], str | None] | None" = None
    #: the isolation levels this host supports ([CXC-PC-RUN]
    #: inprocess-collision-details, r19): a set drawn from {inprocess,
    #: subprocess}. Without subprocess, an in-process collision is refused;
    #: without inprocess, every plug-in runs at subprocess.
    isolation_levels: "frozenset[str]" = frozenset(ISOLATION_LEVELS)

    def __init__(self, name: str, *, state_root: "str | Path",
                 minimum_isolation: str = "inprocess",
                 isolation_levels: "Iterable[str] | None" = None):
        self.name = check_host_name(name)
        if minimum_isolation not in ISOLATION_LEVELS:
            raise PluginContractRefusal(f"minimum_isolation {minimum_isolation!r} must "
                                        f"be one of {list(ISOLATION_LEVELS)}")
        if isolation_levels is not None:
            levels = frozenset(isolation_levels)
            if not levels or not levels <= set(ISOLATION_LEVELS):
                raise PluginContractRefusal(
                    f"isolation_levels {sorted(levels)!r} must be a non-empty subset of "
                    f"{list(ISOLATION_LEVELS)}")
            self.isolation_levels = levels
        self.minimum_isolation = minimum_isolation
        self.state_root = Path(state_root)
        self.install_root = self.state_root / "installed"
        self.install_root.mkdir(parents=True, exist_ok=True)

    # ---- the protocol ----------------------------------------------------- #
    def consent_gate(self, request: ConsentRequest) -> ConsentDecision:
        raise NotImplementedError("a Host must implement consent_gate")

    def validate_section(self, section: Any) -> "list[Refusal]":
        raise NotImplementedError("a Host must validate its own section")

    def render_section(self, section: Any) -> "Sequence[HostPermission]":
        raise NotImplementedError("a Host must render its own section")

    def isolation_level(self, floor: str) -> str:
        """The ACTUAL level: never below the floor ([CXC-CS-ISOLATION-FLOOR]),
        never a level this host does not support. A host without inprocess
        runs everything at subprocess; a plug-in whose floor is a level the host
        lacks is refused."""
        level = ISOLATION_LEVELS[max(ISOLATION_LEVELS.index(floor),
                                     ISOLATION_LEVELS.index(self.minimum_isolation))]
        if level == "inprocess" and "inprocess" not in self.isolation_levels:
            level = "subprocess"
        if level not in self.isolation_levels:
            raise PluginContractRefusal(
                f"host {self.name!r} supports only {sorted(self.isolation_levels)}; it "
                f"cannot run a plug-in at {level!r} (isolation floor {floor!r})")
        return level

    def bus(self):
        """The ComposeBus the host's plug-ins run on, or None (no bus attached)."""
        return None

    def publish_status(self, domain: str, status: str) -> None:
        """The status sink. Default: nothing beyond the manager's own record."""

    def extra_panels(self) -> "list[Panel]":
        return []
