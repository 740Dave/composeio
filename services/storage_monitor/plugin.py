"""storage_monitor as a ComposeServer PLUGIN — the Wave-2b adaptation.

The extant :class:`storage_monitor.service.StorageMonitor` is a headless disk-space
SENSOR: it polls a filesystem and publishes ONE retained semantic fact,
``buildhost/storage/free_bytes`` (an integer byte count), at a configurable cadence
([DX-STATE-ON-BUS] — publish the fact, let leaves react). This module wraps that
BEHAVIOUR, unchanged, in the frozen ComposePlugin contract ([SPEC:CXC-COMPOSESERVER]
[CXC-CS-PLUGIN-CONTRACT]) so it is discovered + stood up by the ComposeServer plugin
loader ([SPEC:CXC-CS-LOADER]) instead of run as its own ``python -m`` process.

WHAT CHANGES vs. the standalone service — ONLY the transport standup:
  * the standalone ``__main__`` builds an MQTT transport and constructs its OWN bus;
  * the plugin holds NO transport, imports no paho, and publishes onto the bus the
    server INJECTS at ``start(bus)`` — ComposeServer's per-tenant bus (DP-CIO-6:
    transport is an implementation detail, not an architectural decision). No separate
    broker; the plugin never learns whether it is one of N per-tenant instances.

WHAT IS PRESERVED, VERBATIM:
  * the MEASUREMENT — ``StorageMonitor`` is reused as-is, so free bytes still come from
    ``shutil.disk_usage(path).free``, a REAL statvfs of the live filesystem. This is the
    fleet lesson made structural: never trust a service's self-reported free bytes — the
    number is a real syscall of the actual mount, not a value some daemon volunteers. The
    plugin adds NO cheaper "estimate" path.
  * the FROZEN CONTRACT NAMESPACE ``buildhost/storage/free_bytes``
    (:data:`storage_monitor.topics.FREE_BYTES`), the ``json`` engine, the retained STATE
    publish, and the immediate-snapshot-then-tick cadence — all inherited unchanged
    because the plugin drives the SAME ``StorageMonitor`` object.

THE MCP SURFACE — one retained read ([SPEC:CXC-MCP] [CXC-MCP-TOOL-DECLARATION],
[CXC-MCP-INVOKE-READ]). ``get_current_free_bytes_on_monitored_filesystem`` decodes the
retained value of ``buildhost/storage/free_bytes`` directly — no round-trip, no fabricated
default (a namespace with nothing published yet returns an explicit "no value yet"). The
name is deliberately verbose and agent-unambiguous per David's ruling
([DEC-MCP-SURFACES-NAMED-FOR-INTELLIGENCE-2026-08-25]): an Intelligence reading the tool
list must know, from the name alone, that it returns the CURRENT number of FREE BYTES on
the MONITORED FILESYSTEM — not a percentage, not a total, not a threshold verdict.
"""
from __future__ import annotations

from typing import Any, Callable

from pydantic import BaseModel, Field

from composeio.plugin import (
    ComposePlugin,
    McpToolDecl,
    NamespaceMapping,
    ReadMode,
    ToolDirection,
)

from . import config, topics
from .service import StorageMonitor


# --------------------------------------------------------------------------- #
# THE MCP TOOL ARGUMENT MODEL — a pure retained read takes no arguments.
# The JSON-schema an MCP client sees is DERIVED from this empty model
# ([CXC-MCP-TOOL-DECLARATION] input-schema-is-the-single-derivation-source).
# --------------------------------------------------------------------------- #
class ReadFreeBytesArgs(BaseModel):
    """No arguments: the tool reads the single monitored filesystem's retained
    free-bytes value. WHICH filesystem is monitored is plugin CONFIG (``path``),
    not a per-call argument — a caller cannot ask this plugin about an arbitrary
    path, only about the one it was configured to watch."""


class StorageMonitorPlugin(ComposePlugin):
    """The buildhost disk-space sensor, wearing the ComposePlugin contract.

    ONE declaration -> the derived config form, announce payload, registry entry,
    AND MCP surface. The author writes only the ``Config``, the class-level
    ``publishes``, and the ``mcp_tools`` list; everything else derives.
    """

    # ---- ARTIFACT 1: the ONE typed declaration (the config MANIFEST) ------ #
    class Config(ComposePlugin.Config):
        """storage_monitor's per-tenant config manifest ([CXC-CSL-CONFIG-VALIDATION]).

        Validated by the loader BEFORE instantiation via ``Config(**config)`` — a
        bad value is a ``config:<field>`` park, never a crash. Defaults follow the
        extant service's env defaults (:mod:`storage_monitor.config`) so a manifest
        that omits both fields behaves identically to the standalone service."""

        path: str = Field(
            default=config.DEFAULT_PATH,
            description="Filesystem to measure free space on (the mount whose "
                        "df is reported). Re-pointable to a future spinny-disk "
                        "mount with no code change.",
        )
        interval_s: float = Field(
            default=config.DEFAULT_INTERVAL_S,
            gt=0.0,
            description="Seconds between measurements. Disk space changes slowly, "
                        "so the default (30 s) is plenty; a 1 Hz sensor here would "
                        "be wasteful.",
        )

    # ---- identity + declared bus surface --------------------------------- #
    name = "storage_monitor"
    version = "1.0.0"
    #: NOT a kernel shed — a tenant service discovered via manifest ref, never a
    #: default-loaded plugin ([CXC-CS-DEFAULT-PLUGINS]).
    default_loaded = False
    #: the one frozen fact this sensor originates.
    publishes = [topics.FREE_BYTES]

    # ---- ARTIFACT 4: the MCP tool surface (DERIVED) ---------------------- #
    mcp_tools = [
        McpToolDecl(
            name="get_current_free_bytes_on_monitored_filesystem",
            description=(
                "Return the CURRENT number of free bytes on the filesystem this "
                "storage_monitor is configured to watch, as a bare integer — the "
                "latest retained measurement of shutil.disk_usage(path).free (a "
                "real statvfs of the live mount), NOT a percentage, a total, a "
                "used count, or a threshold verdict. If the sensor has not yet "
                "published a measurement, returns present=false rather than a "
                "fabricated zero."
            ),
            input_model=ReadFreeBytesArgs,
            direction=ToolDirection.READ,
            read_mode=ReadMode.RETAINED,
            namespace_mapping=NamespaceMapping(subscribes=[topics.FREE_BYTES]),
            idempotent=True,
        )
    ]

    def __init__(
        self,
        config: "BaseModel | dict | None" = None,
        *,
        measure: "Callable[[str], int] | None" = None,
    ) -> None:
        """``measure`` is an OPTIONAL injectable measurement hook, mirroring
        :class:`StorageMonitor`. The loader never passes it (it calls
        ``cls(config=validated)``), so production always uses the real
        ``shutil.disk_usage`` path; a test injects a deterministic byte count to
        assert the publish/read path without depending on the host's real disk."""
        super().__init__(config)
        self._measure = measure
        self._service: "StorageMonitor | None" = None

    @property
    def service(self) -> "StorageMonitor | None":
        """The wrapped sensor, or None before ``start``. Exposed for inspection
        and test assertions, not as a control surface."""
        return self._service

    # ---- lifecycle ------------------------------------------------------- #
    def start(self, bus: Any) -> None:
        """Attach the wrapped ``StorageMonitor`` to the server-injected bus and
        begin publishing. The plugin sits on the data path DIRECTLY; ComposeServer
        supervises lifecycle, it never mediates ([CXC-CS-MICROKERNEL])."""
        self._bus = bus
        self._service = StorageMonitor(
            path=self.config.path,
            interval_s=self.config.interval_s,
            measure=self._measure,
            source=config.SOURCE,
        )
        self._service.attach(bus)
        # Immediate snapshot then periodic ticks — a subscriber (or an MCP
        # retained read) present at start reads the current value at once.
        self._service.start()

    def stop(self) -> None:
        """Stop the measurement loop and join its thread. Idempotent by contract."""
        if self._service is not None:
            self._service.stop()
        self._service = None
        super().stop()
