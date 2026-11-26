"""log — log-mode namespace publishing (`publish_log`), SHED from the kernel.

`bus.py` used to own the log-entry publish path directly ([CXC-CS-EXTRACTION-
INVENTORY] extraction 5; [DEC-PHASE0B-LIFECYCLE-HOOK-SEAM-AND-DEFAULT-SHEDS]).
Emitting a `LOG_ENTRY` to a log-mode namespace is a plugin concern, so the CODE
moves here.

BEHAVIOR IS PRESERVED, AND DELIBERATELY SO OVER A PURER SHAPE. Per the governing
decision, `bus.publish_log(...)` STAYS a callable bus method — it delegates to
this default engine rather than forcing every caller (`app.py`, module log
helpers) to publish to the log namespace directly. Behavior-preservation wins
over the tidier "no log method on the kernel" shape.

Unlike `time`/`liveness` this engine is NOT periodic: it has no lifecycle hook,
it is a request-driven path invoked when a caller logs. A bare `ComposeIOBus()`
installs it at construction so `bus.publish_log(...)` works byte-for-byte as
before; `LogPlugin` is the same engine wearing the plugin contract for the
standup/registry path.
"""
from __future__ import annotations

import logging

from composebus.plugin import ComposePlugin
from composebus.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.plugins.log")


class LogEngine:
    """The log-entry publish path — a plugin peer, not the kernel."""

    def __init__(self, bus):
        self._bus = bus

    def publish_log(self, namespace: str, value, source: str = ""):
        """Publish a log entry to a log-mode namespace (BP-EXT-LOG).

        A non-log namespace falls back to a state publish (with a warning), and
        the publish whitelist (BP-EXT-ACCESS) is enforced — both exactly as the
        baked-in `bus.publish_log` did.
        """
        bus = self._bus
        ns = bus.ensure_namespace(namespace)
        if ns.mode != "log":
            logger.warning(
                f"publish_log to non-log namespace '{namespace}', treating as state")
            return bus.publish_state(namespace, value, source=source)

        # BP-EXT-ACCESS: enforce publish whitelist
        if not ns.check_publish_access(source):
            logger.warning(
                f"Publish denied: '{source}' not in publish list for '{namespace}'")
            return

        ns.publish_log(value, source=source)
        bus.send_message(BusMessage(
            namespace=namespace,
            type=MessageType.LOG_ENTRY,
            value=value,
            source=source,
            scope=bus._resolve_scope(ns),
            sequence=bus._next_seq(),
        ))


class LogPlugin(ComposePlugin):
    """Log-mode publishing wearing the plugin contract ([CXC-CS-PLUGIN-
    CONTRACT]). A default-loaded plugin ([CXC-CS-DEFAULT-PLUGINS]). Its published
    surface is DYNAMIC — any log-mode namespace a caller writes — so `publishes`
    is empty and it is the sole authority over its live surface."""

    class Config(ComposePlugin.Config):
        pass

    name = "log"
    version = "1.0.0"
    default_loaded = True
    publishes: list[str] = []  # dynamic — arbitrary log-mode namespaces

    def __init__(self, config=None):
        super().__init__(config)
        self._engine: "LogEngine | None" = None

    @property
    def engine(self) -> "LogEngine | None":
        return self._engine

    def start(self, bus) -> None:
        self._bus = bus
        self._engine = install_default(bus)


def install_default(bus) -> LogEngine:
    """Create + wire the default log engine on a bus, idempotently.

    Called by `ComposeIOBus.__init__` (so a bare bus keeps `publish_log`
    working) and by `LogPlugin.start` (the standup path). Returns the bus's
    single engine, creating it on first call."""
    engine = getattr(bus, "_log_engine", None)
    if engine is None:
        engine = LogEngine(bus)
        bus._log_engine = engine
    return engine
