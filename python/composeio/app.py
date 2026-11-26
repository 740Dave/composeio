"""ComposeIO Application Framework — bus-native apps in any Python program.

Extracts the internal bus pattern from ComposeUX into a reusable library.
Any Python CLI app can become a first-class ComposeIO bus client by:

1. Subclassing ComposeApp (or using ComposeApp.from_config())
2. Defining namespaces for its state
3. Connecting a transport (MQTT, UART, CAN) for external visibility

The bus dissolves the local/remote boundary — a dashboard designed in ComposeUX
on machine Y subscribes to namespaces published by a CLI app on machine X.
Same protocol, same messages, zero glue code.

Cross-platform semantics:
    This Python framework is one instantiation of the ComposeApp pattern.
    The same design (.compose.yaml) runs on:
    - Python CLI (this library) — MQTT or UART to external bus
    - CH32V003 firmware — I2C to daughterboards, CAN/UART to external bus
    - ESP32 — WiFi/MQTT + CAN bridge
    Same config, same namespace contracts, same state engines.
    Transport is an implementation detail (DP-CIO-6).

Architecture:
    ComposeApp creates two buses:
    - Internal bus: module-to-module communication within the app (never leaves process)
    - External bus: namespaces visible to ComposeIO ecosystem

    Namespaces are explicitly exported from internal → external via export().
    External → internal imports bring remote commands into the app.
    Non-exported internal namespaces are invisible to the outside world.

    On a CH32V003, the "internal bus" is the I2C register protocol to
    daughterboards and the "external bus" is CAN or UART. On a Python CLI,
    the "internal bus" is in-process and the "external bus" is MQTT. The
    crossover rules (export/import) are defined in config — not in code,
    not in firmware, not in transport-specific logic.

Transport configuration:
    The external bus accepts any Transport instance. The framework provides
    convenience methods for common setups (MQTT, UART), but the transport
    is pluggable. connect_transport() is additive — multiple transports
    can be active simultaneously (e.g., MQTT + UART).

Config-driven setup:
    app = ComposeApp.from_config("my_dashboard.compose.yaml",
                                 transport="mqtt", broker="198.51.100.17")
    app.start()

Programmatic setup:
    class MyApp(ComposeApp):
        APP_ID = "biometric-tester"

        def setup(self):
            self.register("_app/mode", engine="enum",
                          values=["idle", "setup", "running", "review"])
            self.register("test_status", engine="enum",
                          values=["idle", "running", "complete", "error"],
                          export=True)
            self.register("test_progress", engine="continuous_0_255", export=True)

        def on_ready(self):
            self.publish("test_status", "idle")

    app = MyApp()
    app.connect_transport("mqtt", broker="198.51.100.17")
    app.start()
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from composeio.bus import ComposeIOBus
from composeio.namespace import Scope
from composeio.transport import BusMessage, MessageType

logger = logging.getLogger("composeio.app")


class AppModule:
    """Base class for decomposed modules within a ComposeApp.

    Like ComposeUX's ComposerModule but with zero UI dependency.
    Modules communicate via the internal bus. Selected state is
    exported to the external bus by the app framework.

    Subclass this to decompose a complex CLI into bus-connected modules:

        class TestRunner(AppModule):
            MODULE_NAME = "test_runner"
            MODULE_MANIFEST = {
                "publishes": ["test_status", "test_progress"],
                "subscribes": ["_app/mode"],
            }

            def on_ready(self):
                self.subscribe("_app/mode", self._on_mode_change)

            def _on_mode_change(self, ns, new_val, old_val):
                if new_val == "running":
                    self._run_tests()
    """

    MODULE_NAME: str = "unnamed"
    MODULE_MANIFEST: dict[str, Any] = {
        "publishes": [],
        "subscribes": [],
    }

    def __init__(self):
        self._internal_bus: ComposeIOBus | None = None
        self._external_bus: ComposeIOBus | None = None
        self._subscriptions: list[tuple[str, Any]] = []

    def _attach(self, internal_bus: ComposeIOBus, external_bus: ComposeIOBus | None):
        """Called by ComposeApp when registering the module."""
        self._internal_bus = internal_bus
        self._external_bus = external_bus

    # --- Bus helpers ---

    def subscribe(self, namespace: str, callback: Callable):
        """Subscribe to state changes on the internal bus."""
        self._internal_bus.subscribe(namespace, callback)
        self._subscriptions.append((namespace, callback))

    def subscribe_events(self, namespace: str, callback: Callable):
        """Subscribe to events on the internal bus."""
        self._internal_bus.subscribe_events(namespace, callback)
        self._subscriptions.append((namespace, callback))

    def publish_state(self, namespace: str, value: Any):
        """Publish state to the internal bus."""
        self._internal_bus.publish_state(namespace, value, source=self.MODULE_NAME)

    def publish_event(self, namespace: str, event_type: str, data: dict | None = None):
        """Publish an event to the internal bus."""
        evt_data = dict(data) if data else {}
        evt_data.setdefault("source", self.MODULE_NAME)
        evt_data.setdefault("timestamp", time.time())
        self._internal_bus.publish_event(
            namespace, event_type, data=evt_data, source=self.MODULE_NAME
        )

    def publish_log(self, namespace: str, value: Any) -> None:
        """Publish a log entry to a log-mode namespace."""
        self._internal_bus.publish_log(namespace, value, source=self.MODULE_NAME)

    def get_state(self, namespace: str) -> Any:
        """Get current value of an internal namespace."""
        return self._internal_bus.get_state(namespace)

    # --- External bus access (for modules that explicitly bridge) ---

    def subscribe_external(self, namespace: str, callback: Callable):
        """Subscribe to state changes on the external bus.

        Use this for responding to commands from dashboards or hardware.
        Most modules should only use internal bus methods.
        """
        if self._external_bus is None:
            logger.warning(f"[{self.MODULE_NAME}] No external bus — subscribe_external ignored")
            return
        self._external_bus.subscribe(namespace, callback)

    def publish_external(self, namespace: str, value: Any):
        """Publish state directly to the external bus.

        Use this sparingly — most external publishing should go through
        the app's export mechanism, not direct module access.
        """
        if self._external_bus is None:
            logger.warning(f"[{self.MODULE_NAME}] No external bus — publish_external ignored")
            return
        self._external_bus.publish_state(namespace, value, source=self.MODULE_NAME)

    # --- Lifecycle ---

    def on_ready(self):
        """Called after app start, all modules registered, buses running.

        Override to set up subscriptions and initial state.
        """
        pass

    def on_stop(self):
        """Called before app shutdown. Override for cleanup."""
        pass

    def destroy(self):
        """Clean up subscriptions."""
        for ns, cb in self._subscriptions:
            try:
                ns_obj = self._internal_bus.get_namespace(ns)
                if ns_obj:
                    ns_obj.unsubscribe(cb)
            except (AttributeError, ValueError):
                pass
        self._subscriptions.clear()


class ComposeApp:
    """Base class for bus-native Python applications.

    Creates dual buses (internal + external) and manages the crossover
    between them. Use programmatically (subclass + override setup()) or
    from config (ComposeApp.from_config()).

    Dual-bus architecture:
        Internal bus — module-to-module communication, never leaves process.
            Namespaces prefixed with _app/ have an enforced default-deny
            visibility boundary: they CANNOT be exported to the external bus
            unless explicitly bridged via route_external(). This is a firewall
            model — deny all _app/ traffic to external, then whitelist via
            routing rules.
            Any non-_app/ namespace can be internal-only (just don't export it).

        External bus — connected to the ComposeIO ecosystem via transports.
            Only namespaces explicitly exported from the internal bus appear here.
            External → internal imports bring remote commands into the app.
            Transport is pluggable: MQTT, UART, CAN, or any Transport subclass.

    Crossover rules:
        export(internal_ns, external_ns=None)
            Internal state changes are forwarded to external bus.
            external_ns defaults to internal_ns (same name on both buses).
            Refuses _app/ namespaces (logs WARN, does not forward).

        route_external(internal_ns, route_as)
            Bridges an _app/ namespace to the external bus under a non-_app/
            name. This is the only way to make _app/ state visible externally.
            Logs WARN on every call — the bridge is intentional but auditable.

        import_namespace(external_ns, internal_ns=None)
            External state changes (from dashboards, hardware) are forwarded
            to the internal bus. internal_ns defaults to external_ns.

        The crossover is one-way per declaration. For bidirectional,
        call both export() and import_namespace().

    CH32V003 / firmware equivalence:
        On a CH32V003 mainboard, the same pattern applies:
        - Internal bus = I2C register protocol to daughterboards
        - External bus = CAN or UART to network
        - Modules = daughterboards (each responds to its own namespaces)
        - Crossover = mainboard bridges I2C ↔ CAN
        - Config = same .compose.yaml, compiled to binary via config.compile_binary()
        The Python ComposeApp and the C firmware runtime implement the same
        bus contract from the same config artifact.
    """

    APP_ID: str = "compose-app"

    def __init__(self, app_id: str | None = None):
        self._app_id = app_id or self.APP_ID
        self._modules: dict[str, AppModule] = {}

        # Internal bus — always in-process, never leaves
        self._internal_bus = ComposeIOBus(bus_id=f"{self._app_id}-internal")

        # External bus — connects to ComposeIO ecosystem
        self._external_bus = ComposeIOBus(bus_id=f"{self._app_id}-external")

        # Named transports on the external bus
        self._transports: dict[str, Any] = {}

        # Crossover tables
        self._exports: dict[str, str] = {}       # internal_ns → external_ns
        self._imports: dict[str, str] = {}        # external_ns → internal_ns
        self._routes: dict[str, str] = {}         # _app/ internal_ns → external route_as name
        self._export_subs: list[tuple[str, Any]] = []
        self._import_subs: list[tuple[str, Any]] = []
        self._route_subs: list[tuple[str, Any]] = []

        # Feedback-loop guard for bidirectional crossover (APP-C7).
        # When a crossover forwarder is actively publishing, this set
        # contains the namespace being forwarded, preventing re-entrant
        # forwarding in the reverse direction.
        self._crossover_active: set[str] = set()

        # Config source (if loaded from file)
        self._config: dict | None = None
        self._config_path: str | None = None

        # Lifecycle state
        self._started = False

    @classmethod
    def from_config(
        cls,
        config_path: str,
        app_id: str | None = None,
        **transport_kwargs,
    ) -> ComposeApp:
        """Create a ComposeApp from a ComposeIO YAML config file.

        The config defines namespaces, state engines, and bindings.
        All namespaces from the config are registered on the internal bus
        and exported to the external bus (the config describes what's
        visible to the ecosystem).

        Args:
            config_path: Path to .compose.yaml or ComposeIO module YAML
            app_id: Override app ID (defaults to config's app.id or module field)
            **transport_kwargs: Passed to connect_transport() after setup.
                Example: from_config("dash.yaml", transport="mqtt", broker="198.51.100.10")

        Returns:
            A configured ComposeApp instance (not yet started).
        """
        from composeio.config import load_config, apply_config, _detect_config_type, ConfigError

        config = load_config(config_path)
        config_type = _detect_config_type(config)

        # Extract app/module ID based on config type
        if config_type == "app":
            app_block = config.get("app", {})
            config_id = app_block.get("id", "compose-app") if isinstance(app_block, dict) else "compose-app"
        else:
            config_id = config.get("module", "compose-app")

        app = cls(app_id=app_id or config_id)
        app._config = config
        app._config_path = config_path

        # Apply config to internal bus (registers namespaces, bindings)
        apply_config(app._internal_bus, config)

        # Determine config format: app-style (has namespace dicts with export/import)
        # vs traditional module config (has 'role' key → export all non-_ namespaces)
        is_module_config = "role" in config

        if is_module_config:
            # Legacy behavior: export all non-_ namespaces
            for ns_name in app._internal_bus.namespaces:
                if not ns_name.startswith("_"):  # Don't export infrastructure
                    app.export(ns_name)
        else:
            # App-format config: respect per-namespace export/import/route_external
            # and bus protocol extension fields (mode, retention, access)
            ns_list = config.get("namespaces", [])
            for ns_def in ns_list:
                if isinstance(ns_def, dict):
                    name = ns_def.get("name", "")

                    # Parse bus extension fields (BP-EXT-LOG, BP-EXT-ACCESS)
                    ns_mode = ns_def.get("mode", "state")
                    ns_retention = ns_def.get("retention", 100)
                    access_block = ns_def.get("access", {})
                    ns_access_publish = None
                    ns_access_subscribe = None
                    if isinstance(access_block, dict):
                        ap = access_block.get("publish")
                        if ap is not None:
                            ns_access_publish = ap if isinstance(ap, list) else None
                        asub = access_block.get("subscribe")
                        if asub is not None and asub != "any":
                            ns_access_subscribe = asub if isinstance(asub, list) else None

                    # Re-register on internal bus with extension fields
                    # (apply_config already registered the namespace with engine;
                    #  update mode/retention/access on the existing namespace)
                    int_ns_obj = app._internal_bus.get_namespace(name)
                    if int_ns_obj:
                        int_ns_obj.mode = ns_mode
                        int_ns_obj.retention = ns_retention
                        int_ns_obj.access_publish = ns_access_publish
                        int_ns_obj.access_subscribe = ns_access_subscribe
                        if ns_mode == "log":
                            import collections
                            int_ns_obj._log_entries = collections.deque(maxlen=ns_retention)

                    if ns_def.get("export"):
                        ext_name = ns_def.get("export_as")
                        app.export(name, ext_name)
                    if ns_def.get("import"):
                        import_from = ns_def.get("import_from")
                        app.import_namespace(import_from or name, name if import_from else None)
                    if name.startswith("_app/") and ns_def.get("route_external"):
                        route_as = ns_def.get("route_as")
                        if route_as:
                            app.route_external(name, route_as)
                        else:
                            raise ConfigError(
                                f"route_as is required when routing an _app/ namespace "
                                f"to external bus: '{name}'"
                            )

            # P1-3: Connect transports from app.transports[] in the YAML
            app_block = config.get("app", {})
            if isinstance(app_block, dict):
                for transport_entry in app_block.get("transports", []):
                    if isinstance(transport_entry, dict):
                        t_type = transport_entry.get("type")
                        if t_type:
                            t_kwargs = {k: v for k, v in transport_entry.items() if k != "type"}
                            app.connect_transport(t_type, **t_kwargs)

        # Connect transport if specified via kwargs (legacy single-transport shortcut)
        transport_type = transport_kwargs.pop("transport", None)
        if transport_type:
            app.connect_transport(transport_type, **transport_kwargs)

        return app

    # --- Namespace registration ---

    def register(
        self,
        namespace: str,
        engine: str | dict = "binary",
        export: bool = False,
        external_name: str | None = None,
        label: str | None = None,
        mode: str = "state",
        retention: int = 100,
        access_publish: list[str] | None = None,
        access_subscribe: list[str] | None = None,
        **engine_kwargs,
    ) -> None:
        """Register a namespace on the internal bus.

        Args:
            namespace: Namespace identifier (e.g., "test_status", "_app/mode")
            engine: State engine config — string ("binary", "tri_level",
                    "continuous_0_255") or dict ({"type": "enum", "values": [...]})
            export: If True, automatically export to external bus
            external_name: Name on external bus (defaults to namespace)
            label: Human-readable display label
            mode: Namespace mode — "state", "event_only", or "log" (BP-EXT-LOG)
            retention: Max log entries retained (log mode only). Default: 100.
            access_publish: Publisher whitelist (None = any source allowed)
            access_subscribe: Subscriber whitelist (None = any client allowed)
            **engine_kwargs: If engine is a string type, these are passed
                            to construct a dict config. Example:
                            register("x", engine="enum", values=["a","b"])
        """
        # Enforce _app/ default-deny boundary
        if namespace.startswith("_app/") and export:
            raise ValueError(
                "_app/ namespaces cannot be exported directly. "
                "Use route_external() to bridge explicitly."
            )

        # Build engine config from kwargs if needed
        if isinstance(engine, str) and engine_kwargs:
            engine_config = {"type": engine, **engine_kwargs}
        else:
            engine_config = engine

        self._internal_bus.register_namespace(
            namespace, engine_config, label=label,
            mode=mode, retention=retention,
            access_publish=access_publish, access_subscribe=access_subscribe,
        )

        if export:
            self.export(namespace, external_name)

    def export(self, internal_ns: str, external_ns: str | None = None) -> None:
        """Export an internal namespace to the external bus.

        When the internal namespace's state changes, the new value is
        forwarded to the external bus under external_ns (defaults to
        same name). This makes the namespace visible to ComposeUX
        dashboards, other bus clients, and hardware via MQTT.

        Refuses _app/ namespaces — use route_external() instead.
        """
        # Enforce _app/ default-deny boundary at runtime
        if internal_ns.startswith("_app/"):
            logger.warning(
                f"Refusing to export _app/ namespace '{internal_ns}' "
                f"— use route_external() to bridge explicitly"
            )
            return

        external_ns = external_ns or internal_ns
        self._exports[internal_ns] = external_ns

        # Register on external bus with same engine config, mode, and access policy
        int_ns = self._internal_bus.get_namespace(internal_ns)
        if int_ns:
            self._external_bus.register_namespace(
                external_ns,
                int_ns.state_engine.to_dict(),
                label=int_ns.label,
                mode=int_ns.mode,
                retention=int_ns.retention,
                access_publish=int_ns.access_publish,
                access_subscribe=int_ns.access_subscribe,
            )

    def import_namespace(self, external_ns: str, internal_ns: str | None = None) -> None:
        """Import an external namespace into the internal bus.

        When the external namespace's state changes (e.g., a dashboard
        button press via MQTT), the new value is forwarded to the
        internal bus under internal_ns (defaults to same name).
        """
        internal_ns = internal_ns or external_ns
        self._imports[external_ns] = internal_ns

        # Ensure internal namespace exists
        self._internal_bus.ensure_namespace(internal_ns)

        # Pre-register external namespace with same engine config as internal
        # (mirrors the pattern in export()). Without this, the external bus
        # namespace is lazily created with default 'binary' engine on first
        # message arrival, which may lose type information.
        int_ns_obj = self._internal_bus.get_namespace(internal_ns)
        if int_ns_obj:
            self._external_bus.register_namespace(
                external_ns,
                int_ns_obj.state_engine.to_dict(),
                label=int_ns_obj.label,
                mode=int_ns_obj.mode,
                retention=int_ns_obj.retention,
                access_publish=int_ns_obj.access_publish,
                access_subscribe=int_ns_obj.access_subscribe,
            )

    def route_external(self, internal_ns: str, route_as: str) -> None:
        """Bridge an _app/ namespace to the external bus under a different name.

        This is the only way to make _app/ namespaces visible externally.
        The _app/ prefix enforces a default-deny visibility boundary;
        route_external() is the explicit whitelist mechanism.

        Args:
            internal_ns: Must start with '_app/'. The private namespace to bridge.
            route_as: REQUIRED. The name on the external bus. Must NOT start
                      with '_app/' (the private prefix must not leak).

        Raises:
            ValueError: If internal_ns does not start with '_app/' (use export()),
                        or if route_as starts with '_app/'.
        """
        if not internal_ns.startswith("_app/"):
            raise ValueError(
                "route_external() is only for _app/ namespaces. "
                "Use export() instead."
            )
        if route_as.startswith("_app/"):
            raise ValueError(
                "route_as must not start with _app/ — "
                "the external name must be outside the private boundary."
            )

        logger.warning(
            f"_app/ namespace '{internal_ns}' bridged to external bus "
            f"as '{route_as}' — verify this is intentional"
        )

        # Register the external namespace with the same engine config, mode, and access
        int_ns = self._internal_bus.get_namespace(internal_ns)
        if int_ns:
            self._external_bus.register_namespace(
                route_as,
                int_ns.state_engine.to_dict(),
                label=int_ns.label,
                mode=int_ns.mode,
                retention=int_ns.retention,
                access_publish=int_ns.access_publish,
                access_subscribe=int_ns.access_subscribe,
            )

        self._routes[internal_ns] = route_as

    # --- Module management ---

    def add_module(self, module: AppModule) -> None:
        """Register a module with the app."""
        self._modules[module.MODULE_NAME] = module
        module._attach(self._internal_bus, self._external_bus)

    # --- Publishing (convenience for apps without modules) ---

    def publish(self, namespace: str, value: Any, source: str = "") -> None:
        """Publish state to the internal bus.

        If the namespace is exported, the value is automatically
        forwarded to the external bus.
        """
        src = source or self._app_id
        self._internal_bus.publish_state(namespace, value, source=src)

    def publish_event(self, namespace: str, event_type: str, data: dict | None = None) -> None:
        """Publish an event to the internal bus."""
        self._internal_bus.publish_event(
            namespace, event_type, data=data, source=self._app_id
        )

    def publish_log(self, namespace: str, value: Any, source: str = "") -> None:
        """Publish a log entry to a log-mode namespace on the internal bus."""
        src = source or self._app_id
        self._internal_bus.publish_log(namespace, value, source=src)

    def get_state(self, namespace: str) -> Any:
        """Get current value of an internal namespace."""
        return self._internal_bus.get_state(namespace)

    def subscribe(self, namespace: str, callback: Callable) -> None:
        """Subscribe to state changes on the internal bus."""
        self._internal_bus.subscribe(namespace, callback)

    # --- Transport management ---

    def connect_transport(self, transport_type: str, **kwargs) -> None:
        """Connect a transport to the external bus.

        Transports are additive — connecting MQTT doesn't replace UART.
        Multiple transports can be active simultaneously.

        Args:
            transport_type: One of "mqtt", "uart", or a Transport instance key.
            **kwargs: Transport-specific configuration.

        MQTT kwargs: broker (str), port (int, default 1883), client_id (str)
        UART kwargs: port (str, e.g. "/dev/ttyUSB0"), baudrate (int, default 115200)

        Examples:
            app.connect_transport("mqtt", broker="198.51.100.17")
            app.connect_transport("uart", port="/dev/ttyUSB0", baudrate=115200)
        """
        if transport_type == "mqtt":
            transport = self._create_mqtt_transport(**kwargs)
        elif transport_type == "uart":
            transport = self._create_uart_transport(**kwargs)
        elif transport_type == "http_relay":
            transport = self._create_http_relay_transport(**kwargs)
        else:
            raise ValueError(f"Unknown transport type: {transport_type}. Use 'mqtt', 'uart', or 'http_relay'.")

        if transport is None:
            return  # Creation failed (logged already)

        self._add_transport(transport_type, transport)

    def connect_transport_instance(self, name: str, transport: Any) -> None:
        """Connect a pre-configured Transport instance to the external bus.

        Use this for custom transports or when you need fine-grained control.
        The transport must implement the composeio.transport.Transport interface.
        """
        self._add_transport(name, transport)

    def disconnect_transport(self, name: str) -> None:
        """Disconnect a named transport from the external bus."""
        if name not in self._transports:
            return
        transport = self._transports.pop(name)
        transport.stop()
        # Clear from bus transport slots
        if self._external_bus._global is transport:
            self._external_bus._global = None
        if self._external_bus._remote is transport:
            self._external_bus._remote = None
        logger.info(f"Transport '{name}' disconnected")

    def _add_transport(self, name: str, transport: Any) -> None:
        """Add a transport to the external bus."""
        if name in self._transports:
            self.disconnect_transport(name)

        # Assign to first available slot (global, then remote)
        if self._external_bus._global is None:
            self._external_bus._global = transport
        elif self._external_bus._remote is None:
            self._external_bus._remote = transport
        else:
            logger.warning(
                f"Both transport slots full. Replacing global with '{name}'. "
                f"Consider using a transport multiplexer for 3+ transports."
            )
            self._external_bus._global = transport

        self._transports[name] = transport
        if self._started:
            self._external_bus._wire_transport_incoming(transport)
            # Re-subscribe the transport to all namespaces that have bus-level
            # subscribers (imports, exports). Without this, MQTT won't receive
            # messages for namespaces that were wired before the transport existed.
            for ns_name in self._external_bus.namespaces:
                transport.subscribe(ns_name, lambda msg: None)
            # Pattern subscriptions made before this transport existed widen
            # it too ([CXC-FL-PATTERN]).
            sub_pat = getattr(transport, "subscribe_pattern", None)
            if sub_pat is not None:
                for pattern, _cb in self._external_bus._pattern_subs:
                    sub_pat(pattern)
            transport.start()
        logger.info(f"Transport '{name}' connected to external bus")

    def _create_mqtt_transport(self, **kwargs) -> Any:
        try:
            from composeio.transport_mqtt import MQTTTransport
        except ImportError:
            logger.error("paho-mqtt not installed. Run: pip install paho-mqtt")
            return None
        return MQTTTransport(
            broker=kwargs.get("broker", "localhost"),
            port=kwargs.get("port", 1883),
            client_id=kwargs.get("client_id", self._app_id),
        )

    def _create_uart_transport(self, **kwargs) -> Any:
        try:
            from composeio.transport_uart import UARTTransport
        except ImportError:
            logger.error("pyserial not installed. Run: pip install pyserial")
            return None
        return UARTTransport(
            port=kwargs.get("port", "/dev/ttyUSB0"),
            baudrate=kwargs.get("baudrate", 115200),
        )

    def _create_http_relay_transport(self, **kwargs) -> Any:
        try:
            from composeio.transport_http_relay import HttpRelayTransport
        except ImportError:
            logger.error("composeio.transport_http_relay not available")
            return None
        return HttpRelayTransport(
            endpoint=kwargs.get("endpoint", "http://localhost:8422/messages"),
            channel=kwargs.get("channel", self._app_id),
            direction=kwargs.get("direction", "outbound"),
            filter_namespaces=kwargs.get("filter"),
            batch_interval_ms=kwargs.get("batch_interval_ms", 0),
        )

    # --- Bus access (for advanced use) ---

    @property
    def internal_bus(self) -> ComposeIOBus:
        """Direct access to the internal bus. Use for advanced patterns."""
        return self._internal_bus

    @property
    def external_bus(self) -> ComposeIOBus:
        """Direct access to the external bus. Use sparingly."""
        return self._external_bus

    # --- Lifecycle ---

    def setup(self):
        """Override to define namespaces, modules, and exports.

        Called before buses start. Use self.register(), self.export(),
        self.import_namespace(), and self.add_module() here.

        Not called when using from_config() — config drives setup instead.
        """
        pass

    def on_ready(self):
        """Override for post-start initialization.

        Called after buses are running, transports are connected,
        and all crossover bridges are active.
        """
        pass

    def on_stop(self):
        """Override for pre-shutdown cleanup."""
        pass

    def start(self) -> None:
        """Start the app: setup, start buses, connect transports, wire crossovers."""
        if self._started:
            return

        # 1. Let subclass define namespaces and modules (skip if from_config)
        if self._config is None:
            self.setup()

        # 2. Start buses.
        #
        # ⛔ ONLY THE EXTERNAL BUS ANNOUNCES A CLOCK. A ComposeApp holds TWO
        # buses, and both used to call `start()` with the default
        # `announce_time=True` — which was two one-shot ticks and looked
        # harmless. Now that `_bus/time` is a REAL 1 Hz signal
        # ([DEC-BUS-TIME-IS-A-REAL-1HZ-SIGNAL]) that same line would run TWO
        # clocks and TWO heartbeats in one process, forever.
        #
        # The internal bus is the app's PRIVATE in-process fabric — the same
        # "leaf, not master" case `source_bus.py` already passes
        # `announce_time=False` for, and for the identical reason: a clock is a
        # bus MASTER's announcement, and two masters in one process is a clock
        # argument, not a feature. The app's liveness and time belong to the bus
        # it actually shares with the fleet.
        self._internal_bus.start(announce_time=False)
        self._external_bus.start()

        # 3. Start any pre-connected transports
        for name, transport in self._transports.items():
            if not transport.is_connected:
                transport.start()

        # 4. Wire crossover bridges
        self._wire_exports()
        self._wire_imports()

        # 4b. Publish discovery on external bus (APP-C6)
        self._publish_discovery()

        # 5. Notify modules
        for module in self._modules.values():
            module.on_ready()

        # 6. App-level ready
        self.on_ready()

        self._started = True

        transport_names = list(self._transports.keys())
        routes_info = f", {len(self._routes)} routes" if self._routes else ""
        logger.info(
            f"App '{self._app_id}' started — "
            f"{len(self._internal_bus.namespaces)} internal namespaces, "
            f"{len(self._exports)} exports, "
            f"{len(self._imports)} imports"
            f"{routes_info}"
            f"{', transports: ' + ', '.join(transport_names) if transport_names else ''}"
        )

    def stop(self) -> None:
        """Stop the app: notify modules, disconnect transports, stop buses."""
        if not self._started:
            return

        self.on_stop()

        for module in self._modules.values():
            module.on_stop()
            module.destroy()

        self._teardown_crossovers()

        for name in list(self._transports):
            self.disconnect_transport(name)

        self._external_bus.stop()
        self._internal_bus.stop()

        self._started = False
        logger.info(f"App '{self._app_id}' stopped")

    def run_forever(self) -> None:
        """Start and block until KeyboardInterrupt. Convenience for CLI apps."""
        self.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    # --- Status / introspection ---

    def status(self) -> dict:
        """App status for diagnostics.

        Routes (_app/ bridges) are shown separately from exports with
        a BRIDGED marker to distinguish intentional _app/ exceptions.
        """
        # Build namespace mode summary for richer diagnostics
        namespace_modes = {}
        for ns_name, ns_obj in self._internal_bus.namespaces.items():
            info = {"mode": ns_obj.mode}
            if ns_obj.mode == "log":
                info["retention"] = ns_obj.retention
                info["log_entries"] = len(ns_obj._log_entries)
            if ns_obj.access_publish:
                info["access_publish"] = ns_obj.access_publish
            if ns_obj.access_subscribe:
                info["access_subscribe"] = ns_obj.access_subscribe
            namespace_modes[ns_name] = info

        return {
            "app_id": self._app_id,
            "started": self._started,
            "config_path": self._config_path,
            "internal_bus": self._internal_bus.status(),
            "external_bus": self._external_bus.status(),
            "namespace_modes": namespace_modes,
            "modules": list(self._modules.keys()),
            "exports": dict(self._exports),
            "imports": dict(self._imports),
            "routes": {
                k: f"BRIDGED -> {v}" for k, v in self._routes.items()
            },
            "transports": {
                name: {"connected": t.is_connected}
                for name, t in self._transports.items()
            },
        }

    # --- Internal: crossover wiring ---

    def _wire_exports(self):
        """Subscribe to internal bus namespaces and forward to external bus.

        All crossover-forwarded messages use source='crossover' (APP-C7).
        A feedback-loop guard prevents re-entrant forwarding when the
        same namespace is both exported and imported (bidirectional crossover).
        """
        for internal_ns, external_ns in self._exports.items():
            # Forward state changes
            def _make_state_forwarder(int_ns, ext_ns):
                def _forward(ns_name, new_val, old_val):
                    # Feedback-loop guard (APP-C7): skip if this namespace
                    # is already being forwarded by the reverse direction
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._external_bus.publish_state(
                            ext_ns, new_val, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            forwarder = _make_state_forwarder(internal_ns, external_ns)
            self._internal_bus.subscribe(internal_ns, forwarder)
            self._export_subs.append((internal_ns, forwarder))

            # Forward events
            def _make_event_forwarder(int_ns, ext_ns):
                def _forward(ns_name, event_type, data):
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._external_bus.publish_event(
                            ext_ns, event_type, data=data, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            evt_forwarder = _make_event_forwarder(internal_ns, external_ns)
            self._internal_bus.subscribe_events(internal_ns, evt_forwarder)
            self._export_subs.append((internal_ns, evt_forwarder))

            # Forward log entries for log-mode namespaces (BP-EXT-LOG)
            int_ns_obj = self._internal_bus.get_namespace(internal_ns)
            if int_ns_obj and int_ns_obj.mode == "log":
                def _make_log_forwarder(int_ns, ext_ns):
                    def _forward(ns_name, event_type, data):
                        if event_type != "log_entry":
                            return
                        if ext_ns in self._crossover_active:
                            return
                        self._crossover_active.add(ext_ns)
                        try:
                            self._external_bus.publish_log(
                                ext_ns, data, source="crossover"
                            )
                        finally:
                            self._crossover_active.discard(ext_ns)
                    return _forward

                log_forwarder = _make_log_forwarder(internal_ns, external_ns)
                self._internal_bus.subscribe_events(internal_ns, log_forwarder)
                self._export_subs.append((internal_ns, log_forwarder))

        # Wire _app/ routes (same forwarding pattern as exports, same source tag)
        for internal_ns, route_as in self._routes.items():
            def _make_route_state_forwarder(int_ns, ext_ns):
                def _forward(ns_name, new_val, old_val):
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._external_bus.publish_state(
                            ext_ns, new_val, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            forwarder = _make_route_state_forwarder(internal_ns, route_as)
            self._internal_bus.subscribe(internal_ns, forwarder)
            self._route_subs.append((internal_ns, forwarder))

            def _make_route_event_forwarder(int_ns, ext_ns):
                def _forward(ns_name, event_type, data):
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._external_bus.publish_event(
                            ext_ns, event_type, data=data, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            evt_forwarder = _make_route_event_forwarder(internal_ns, route_as)
            self._internal_bus.subscribe_events(internal_ns, evt_forwarder)
            self._route_subs.append((internal_ns, evt_forwarder))

    def _wire_imports(self):
        """Subscribe to external bus namespaces and forward to internal bus.

        All crossover-forwarded messages use source='crossover' (APP-C7).
        A feedback-loop guard prevents re-entrant forwarding when the
        same namespace is both exported and imported (bidirectional crossover).

        Forwards both state changes AND events (same pattern as _wire_exports).
        """
        for external_ns, internal_ns in self._imports.items():
            # Forward state changes
            def _make_import_state_forwarder(ext_ns, int_ns):
                def _forward(ns_name, new_val, old_val):
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._internal_bus.publish_state(
                            int_ns, new_val, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            forwarder = _make_import_state_forwarder(external_ns, internal_ns)
            self._external_bus.subscribe(external_ns, forwarder)
            self._import_subs.append((external_ns, forwarder))

            # Forward events
            def _make_import_event_forwarder(ext_ns, int_ns):
                def _forward(ns_name, event_type, data):
                    if ext_ns in self._crossover_active:
                        return
                    self._crossover_active.add(ext_ns)
                    try:
                        self._internal_bus.publish_event(
                            int_ns, event_type, data=data, source="crossover"
                        )
                    finally:
                        self._crossover_active.discard(ext_ns)
                return _forward

            evt_forwarder = _make_import_event_forwarder(external_ns, internal_ns)
            self._external_bus.subscribe_events(external_ns, evt_forwarder)
            self._import_subs.append((external_ns, evt_forwarder))

    def _publish_discovery(self):
        """Publish discovery message on external bus listing exported namespaces (APP-C6).

        This allows the bus master to register the app's exported namespaces
        in the namespace registry (DP-CIO-23). Called at startup after
        crossover wiring, and can be re-called on transport reconnect.
        """
        exported_namespaces = list(self._exports.values())
        routed_namespaces = list(self._routes.values())
        all_external = exported_namespaces + routed_namespaces

        if all_external:
            # Engine types per exported namespace (ADDITIVE field, 2026-07-07
            # [CXC-FL-ENGINE]): lets a discovering peer show what SHAPE an
            # external signal has instead of inferring from values.
            engines: dict[str, str] = {}
            for int_ns, ext_ns in {**self._exports,
                                   **self._routes}.items():
                ns_obj = self._internal_bus.get_namespace(int_ns)
                if ns_obj is not None:
                    engines[ext_ns] = ns_obj.state_engine.engine_type
            self._external_bus.publish_event(
                "_bus/discovery",
                "discovery",
                data={
                    "app_id": self._app_id,
                    "namespaces": all_external,
                    "engines": engines,
                    "exports": dict(self._exports),
                    "routes": {k: f"BRIDGED -> {v}" for k, v in self._routes.items()},
                },
                source=self._app_id,
            )
            logger.debug(
                f"Discovery published on external bus: {len(all_external)} namespaces"
            )

    def _teardown_crossovers(self):
        """Remove crossover subscriptions (exports, imports, and _app/ routes)."""
        for ns, cb in self._export_subs:
            ns_obj = self._internal_bus.get_namespace(ns)
            if ns_obj:
                try:
                    ns_obj.unsubscribe(cb)
                except ValueError:
                    pass
        for ns, cb in self._import_subs:
            ns_obj = self._external_bus.get_namespace(ns)
            if ns_obj:
                try:
                    ns_obj.unsubscribe(cb)
                except ValueError:
                    pass
        for ns, cb in self._route_subs:
            ns_obj = self._internal_bus.get_namespace(ns)
            if ns_obj:
                try:
                    ns_obj.unsubscribe(cb)
                except ValueError:
                    pass
        self._export_subs.clear()
        self._import_subs.clear()
        self._route_subs.clear()
