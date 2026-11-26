"""time_service, worn as a ComposeServer plugin ([SPEC:CXC-COMPOSESERVER]
[CXC-CS-PLUGIN-CONTRACT]).

This is the WAVE-2b repoint: the extant :class:`~time_service.service.TimeService`
— a headless publisher of ``time/base`` at a configurable tick — becomes a
self-contained :class:`~composeio.plugin.ComposePlugin`. The behaviour is
UNCHANGED; only the SUBSTRATE changes. Standalone, ``TimeService`` connected its
OWN transport (``__main__`` built one via ``transport_registry.build``); INSIDE
ComposeServer it is handed its tenant's bus and publishes onto THAT — no separate
broker, no transport named here. That is the whole of the repoint (DP-CIO-6:
transport is an implementation detail, not an architectural decision).

ONE DECLARATION -> FOUR DERIVED ARTIFACTS ([CXC-CS-PLUGIN-CONTRACT]). The plugin
hand-writes only its :class:`Config` (the config manifest the loader validates
BEFORE instantiation, [CXC-CSL-CONFIG-VALIDATION]), its ``publishes`` surface,
and its ``mcp_tools`` list. ComposeServer DERIVES the config form, the announce
payload, the registry entry, and the MCP tool descriptors from those.

DISCOVERY / PLACEMENT ([CXC-CSL-MANIFEST-SHAPE]). The loader resolves a plugin
from a manifest ``ref`` that is either a registered name or a
``"module.path:ClassName"`` dotted import. This module ships INSIDE the
``time_service`` package it wraps, so a tenant manifest names it self-contained
as ``time_service.plugin:TimeBasePublisherPlugin`` — no central registry edit,
the plugin travels with the behaviour it wraps.

THE MCP SURFACE is minimal for a publisher: a single retained READ of
``time/base`` ([CXC-MCP-TOOL-DECLARATION], [CXC-MCP-INVOKE-READ] RETAINED). Its
name, params and description are written to be UNAMBIGUOUS TO AN INTELLIGENCE
reading the aggregated tool list, verbose over terse
([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)

from . import topics
from .profile import TimeProfile
from .service import TimeService


# --------------------------------------------------------------------------- #
# MCP tool argument model — DERIVES the tool's JSON-schema
# ([CXC-MCP-TOOL-DECLARATION] input-schema-is-the-single-derivation-source).
# --------------------------------------------------------------------------- #
class GetCurrentBusTimeBaseArguments(BaseModel):
    """Arguments for the ``time/base`` retained read — deliberately NONE.

    ``time/base`` is a single retained namespace; the read is a pure snapshot
    and takes no parameters. ``extra='forbid'`` so an Intelligence that sends a
    stray field gets a loud validation error rather than a silently-ignored
    argument that might make it believe it was steering the clock.
    """

    model_config = ConfigDict(extra="forbid")


class TimeBasePublisherPlugin(ComposePlugin):
    """The platform base clock (``time/base``) wearing the plugin contract.

    Wraps :class:`~time_service.service.TimeService` VERBATIM: ``start(bus)``
    builds the same profile-driven service, attaches it to the tenant bus, and
    starts its tick loop (an immediate retained snapshot, then the configured
    cadence). ``stop()`` halts and joins that loop. Nothing about the service's
    value shape, retention, idle-body realization, or game/idle mode changes.
    """

    # ---- the ONE typed declaration (the loader's config manifest) -------- #
    class Config(ComposePlugin.Config):
        """time_service's config, as the plugin's derived config form.

        Mirrors what ``TimeProfile`` reads today ([DD-TIME-PROFILE-SHAPE],
        ruled minimal-and-open): the publish cadence, the base mode, an optional
        override of the idle body table, and this publisher's bus ``source`` id.
        Validated by the loader BEFORE instantiation, so a bad value PARKS the
        plugin loudly rather than raising inside ``start()``
        ([CXC-CSL-CONFIG-VALIDATION], [CXC-CSL-ERROR-TAXONOMY]).
        """

        model_config = ConfigDict(extra="forbid")

        tick_hz: float = Field(
            default=1.0, gt=0.0,
            description="time/base publish cadence in Hz (must be > 0; default 1 Hz).",
        )
        base_mode: Literal["idle", "game"] = Field(
            default="idle",
            description="idle = UTC wall-clock epoch; game = elapsed seconds from start.",
        )
        bodies: dict[str, str] | None = Field(
            default=None,
            description="optional {slug: IANA-tz} idle-body override; None = the "
                        "default idle locale table.",
        )
        source: str = Field(
            default="time_service",
            description="the bus `source` id this publisher stamps on its messages.",
        )

    # ---- identity + declared surface ------------------------------------ #
    name = "time_base_publisher"
    version = "1.0.0"
    # A SERVICE plugin, not a kernel default — an operator opts it into a tenant
    # via the manifest, it does not ship installed with every bus.
    default_loaded = False
    publishes = [topics.BASE]

    # ---- ARTIFACT 4/4: the MCP tool surface ----------------------------- #
    mcp_tools = [
        McpToolDecl(
            name="get_current_bus_time_base_epoch_seconds",
            description=(
                "Return the current retained value of the ComposeBus `time/base` "
                "namespace: the platform base clock, as INTEGER EPOCH SECONDS. In "
                "idle base_mode this is UTC wall-clock seconds since 1970; in game "
                "base_mode it is elapsed seconds since the game clock started. This "
                "is a READ-ONLY snapshot of the most recently published tick — it "
                "takes NO arguments and does NOT advance, reset, or reconfigure the "
                "clock. If the publisher has not ticked yet, the result reports "
                "present=false rather than a fabricated default value."
            ),
            input_model=GetCurrentBusTimeBaseArguments,
            direction=ToolDirection.READ,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(subscribes=[topics.BASE]),
            idempotent=True,
        ),
    ]

    def __init__(self, config=None):
        super().__init__(config)
        self._service: "TimeService | None" = None

    @property
    def service(self) -> "TimeService | None":
        """The wrapped :class:`TimeService`, or None before ``start``/after
        ``stop``. Exposed for inspection/tests, never a second control path."""
        return self._service

    # ---- the profile the wrapped service runs on ------------------------ #
    def _profile(self) -> TimeProfile:
        kwargs: dict = {
            "base_mode": self.config.base_mode,
            "tick_hz": self.config.tick_hz,
        }
        if self.config.bodies is not None:
            kwargs["bodies"] = dict(self.config.bodies)
        return TimeProfile(**kwargs)

    # ---- lifecycle ------------------------------------------------------ #
    def start(self, bus) -> None:
        """Attach the wrapped ``TimeService`` to the tenant bus and start it.

        The service publishes an immediate retained ``time/base`` snapshot at
        ``start()`` (so the retained-read tool and any late joiner see a value
        at once) and then ticks at the configured cadence in its own daemon
        thread — byte-identical to the standalone service, only the bus is now
        ComposeServer's tenant bus."""
        self._bus = bus
        self._service = TimeService(profile=self._profile(), source=self.config.source)
        self._service.attach(bus)
        self._service.start()

    def stop(self) -> None:
        if self._service is not None:
            self._service.stop()
        self._service = None
        super().stop()

    # ---- the current surface (authoritative; reflects realized bodies) --- #
    def current_publishes(self) -> list[str]:
        """The live published surface: ``time/base`` plus every idle body that
        REALIZES in the running base mode ([CXC-CS-DYNAMIC-SURFACE]).

        Before ``start`` (and in game mode, where no idle offset-body realizes)
        this is just ``time/base``. Once attached in idle mode it additionally
        names each ``time/<body>`` the service actually publishes, so the
        announce reflects the true surface rather than the class-level minimum.
        """
        surface = [topics.BASE]
        if self._service is not None:
            surface.extend(
                topics.body(b.name)
                for b in self._service.bodies
                if b.realizes_in(self._service.profile.base_mode)
            )
        return surface
