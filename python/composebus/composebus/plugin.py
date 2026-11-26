"""ComposePlugin — THE FROZEN PLUGIN CONTRACT.

This is the narrow waist Phase 1 imports. Every ComposeServer capability —
including the kernel's own admin surface — is a plugin with this exact shape
([SPEC:CXC-COMPOSESERVER] [CXC-CS-PLUGIN-CONTRACT]).

ONE DECLARATION -> THREE ARTIFACTS. A plugin is declared as ONE typed pydantic
``Config`` model plus its class-level ``publishes``/``subscribes``. From that
single declaration ComposeServer DERIVES the plugin's config FORM, its ANNOUNCE
payload, and its REGISTRY entry — the author hand-writes none of the three, so
they can never drift ([CXC-CS-PLUGIN-CONTRACT] announce-is-mandatory-and-derived).

THE TWO FLOORS.
  * ``isolation_floor`` — a plugin MAY pin ``subprocess`` as a HARD requirement
    (David's Q1 ruling 2026-08-23): if it declares it must run out-of-process,
    ComposeServer MUST honor it. A plugin may NEVER force ``inprocess`` — and it
    structurally cannot, because the floor is a MINIMUM and the server sets the
    actual level ``>= floor`` ([CXC-CS-ISOLATION-FLOOR]). There is deliberately
    no ceiling field: "force inprocess" is unrepresentable by construction.
  * ``requires`` — an execution floor of announced CAPABILITIES plus a ``ready:``
    predicate. Unmet, the plugin is PARKED LOUDLY (never silently degraded) and
    the admin UX names the specific unmet item ([CXC-CS-REQUIRES-FLOOR]).

The contract is BUS-AGNOSTIC. A plugin holds no transport, imports no paho,
and never learns whether it is one of N per-tenant instances — that is pure
kernel orchestration ([DEC-COMPOSESERVER-BUS-STANDUP-2026-08-23] multi-tenancy).
"""
from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ConfigDict, model_validator

if TYPE_CHECKING:  # avoid a kernel<-plugin import cycle at module load
    from composebus.bus import ComposeIOBus


class Isolation(enum.IntEnum):
    """Ordered isolation levels ([CXC-CS-ISOLATION-FLOOR]).

    ORDERED ON PURPOSE. A plugin declares a FLOOR (a minimum); ComposeServer
    sets the actual level to ``>= floor``. Because higher is stronger and the
    floor is a lower bound, declaring ``INPROCESS`` cannot FORCE in-process
    execution — the server is always free to isolate more. Pinning
    ``SUBPROCESS`` is the one HARD demand a plugin may make.
    """

    INPROCESS = 0
    SUBPROCESS = 1


class RequiresFloor(BaseModel):
    """The ``requires:`` execution floor ([CXC-CS-REQUIRES-FLOOR]).

    ``capabilities`` are announced strings drawn from a registered vocabulary
    (unversioned in v1). ``ready`` is a ``ready:`` DSL readiness predicate whose
    GRAMMAR is defined elsewhere ([SPEC:CXC-DSL], out of scope for the freeze) —
    here it is carried as an opaque expression the binder evaluates. When the
    floor is unmet the plugin is PARKED and the admin UX names the unmet item;
    it is never silently degraded.
    """

    capabilities: list[str] = []
    ready: str | None = None


# --------------------------------------------------------------------------- #
# THE MCP TOOL-DECLARATION CONTRACT — the load-bearing deliverable of
# [SPEC:CXC-MCP] §6 ([CXC-MCP-TOOL-DECLARATION]).
#
# A plugin declares its MCP surface as a class-level `mcp_tools` list on the
# SAME typed pydantic manifest that already derives the config form, the
# announce payload, and the registry entry — a FOURTH derived artifact, never a
# second hand-authored registration path. The tool's MCP JSON-schema is DERIVED
# from each tool's own `input_model`; the author writes none of it by hand.
# --------------------------------------------------------------------------- #
class ToolDirection(str, enum.Enum):
    """Which invocation path a tool uses ([CXC-MCP-TOOL-DECLARATION] schema).

    * ``WRITE`` — a table-driven publish onto ``namespace_mapping.publishes``,
      val = the validated ``input_model`` args ([CXC-MCP-INVOKE-WRITE]).
    * ``READ`` — a decode from a retained namespace value OR a
      [SPEC:CXC-BUS-QUERY] round-trip, per ``read_mode`` ([CXC-MCP-INVOKE-READ]).
    * ``READ_WRITE`` — composes both, an UNCORRELATED publish-then-separate-read.
    * ``COMMAND`` — a MUTATING QUERY ROUND-TRIP: is_write AND is_read, realized
      over the [SPEC:CXC-BUS-QUERY] idiom (subscribe-before-publish, correlate by
      ``request_id``) whose SINGLE correlated reply carries the authoritative
      outcome ([DEC-MCP-COMMAND-DIRECTION-2026-08-25]). DISTINCT from
      ``READ_WRITE``: a mutation whose result is the responder's correlated
      answer, never an uncorrelated re-read of some namespace. It therefore
      REQUIRES ``read_mode=QUERY`` (enforced at declaration). A direction-keyed
      policy/audit gate sees it as a WRITE (``includes_write()`` true), so a
      mutation can no longer masquerade as a read.
    """

    WRITE = "write"
    READ = "read"
    READ_WRITE = "read-write"
    COMMAND = "command"

    def includes_read(self) -> bool:
        return self in (ToolDirection.READ, ToolDirection.READ_WRITE,
                        ToolDirection.COMMAND)

    def includes_write(self) -> bool:
        return self in (ToolDirection.WRITE, ToolDirection.READ_WRITE,
                        ToolDirection.COMMAND)


class ReadMode(str, enum.Enum):
    """How a ``read`` tool resolves ([CXC-MCP-INVOKE-READ]).

    * ``RETAINED`` — return the namespace's current value directly (no
      round-trip); a namespace with no retained value yet returns an explicit
      "no value yet" result, never a fabricated default.
    * ``QUERY`` — issue a [SPEC:CXC-BUS-QUERY] round-trip and return its result
      verbatim.
    """

    RETAINED = "retained"
    QUERY = "query"


class NamespaceMapping(BaseModel):
    """The bus namespace(s) a tool touches ([CXC-MCP-TOOL-DECLARATION] schema).

    ``publishes`` — the namespace(s) a ``write`` tool publishes to.
    ``subscribes`` — the namespace(s) a ``read`` tool resolves from (for
    ``query`` mode the ``.../query`` request namespace it publishes the query
    to; the responder answers on ``.../result/{src}``, [CXC-BUS-QUERY-IDIOM]).
    """

    publishes: list[str] = []
    subscribes: list[str] = []


class McpToolDecl(BaseModel):
    """ONE tool of a plugin's MCP surface ([CXC-MCP-TOOL-DECLARATION] §6 table).

    The author writes ONLY this: a name, a description, an ``input_model``
    (pydantic), a ``direction``, and a ``namespace_mapping``. The MCP
    JSON-schema an MCP client sees is DERIVED from ``input_model`` — never
    hand-written separately — and the exposed name is ``{plugin_name}.{name}``,
    applied structurally by the router, not by the author.
    """

    # `input_model` is a live pydantic class, not JSON-serialisable data.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    #: plugin-LOCAL tool name (unqualified — e.g. ``enroll``, not
    #: ``fingerprint.enroll``; the plugin-name prefix is the router's job).
    name: str
    #: human/agent-readable purpose, surfaced verbatim in the tool-list.
    description: str = ""
    #: the tool's typed arguments; the JSON-schema is DERIVED from this.
    input_model: type[BaseModel]
    direction: ToolDirection
    namespace_mapping: NamespaceMapping = NamespaceMapping()
    #: required when ``direction`` includes ``read``.
    read_mode: ReadMode | None = None
    #: whether re-invoking with identical args is safe to retry — informational
    #: to the client, never enforced by the router.
    idempotent: bool = False

    @model_validator(mode="after")
    def _check(self) -> "McpToolDecl":
        if not (isinstance(self.input_model, type)
                and issubclass(self.input_model, BaseModel)):
            raise ValueError(
                f"tool {self.name!r}: input_model must be a pydantic BaseModel "
                f"subclass, got {self.input_model!r}")
        if self.direction.includes_read() and self.read_mode is None:
            raise ValueError(
                f"tool {self.name!r}: read_mode is required when direction "
                f"includes 'read' ([CXC-MCP-TOOL-DECLARATION] schema)")
        if (self.direction is ToolDirection.COMMAND
                and self.read_mode is not ReadMode.QUERY):
            # A `command` is a MUTATING QUERY ROUND-TRIP — its authoritative
            # outcome is the responder's correlated reply, so it can ONLY be
            # realized over [SPEC:CXC-BUS-QUERY]. A `command` declared with any
            # other read_mode (including RETAINED) is a declaration error
            # ([DEC-MCP-COMMAND-DIRECTION-2026-08-25]).
            raise ValueError(
                f"tool {self.name!r}: a 'command' tool REQUIRES read_mode=query "
                f"(got {self.read_mode!r}) — its authoritative outcome is the "
                f"correlated reply of a [SPEC:CXC-BUS-QUERY] round-trip "
                f"([DEC-MCP-COMMAND-DIRECTION-2026-08-25])")
        if self.direction is ToolDirection.WRITE and self.read_mode is not None:
            raise ValueError(
                f"tool {self.name!r}: read_mode is meaningless for a pure "
                f"'write' tool")
        return self

    def input_schema(self) -> dict:
        """The JSON-schema an MCP client sees — DERIVED from ``input_model``,
        never hand-maintained ([CXC-MCP-TOOL-DECLARATION]
        input-schema-is-the-single-derivation-source)."""
        return self.input_model.model_json_schema()

    def exposed_name(self, plugin_name: str) -> str:
        """``{plugin_name}.{name}`` — the dot-namespaced name the client sees.
        Structural, never a convention the author applies by hand
        ([CXC-MCP-TOOL-DECLARATION] naming)."""
        return f"{plugin_name}.{self.name}"

    def derived_descriptor(self, plugin_name: str) -> dict:
        """A fully JSON-serialisable descriptor for the derived-announce payload
        ([CXC-MCP-ROUTER-MECHANICS] reads THIS off the announce). Carries the
        derived JSON-schema, not the live class — the class stays with the
        plugin instance, resolved at invoke time for faithful validation."""
        return {
            "name": self.exposed_name(plugin_name),
            "local_name": self.name,
            "description": self.description,
            "input_schema": self.input_schema(),
            "direction": self.direction.value,
            "namespace_mapping": self.namespace_mapping.model_dump(),
            "read_mode": self.read_mode.value if self.read_mode else None,
            "idempotent": self.idempotent,
        }


class PluginAnnounce(BaseModel):
    """The DERIVED announce payload ([CXC-CS-PLUGIN-CONTRACT]).

    Generated from the plugin's declaration; never hand-authored, so it can
    never drift from the config form or the registry entry it is derived
    alongside. Re-emitted on every surface change ([CXC-CS-DYNAMIC-SURFACE]).

    ``mcp_tools`` / ``mcp_endpoint`` are the FOURTH derived artifact
    ([CXC-MCP-TOOL-DECLARATION]): the MCP umbrella router reads the tool
    descriptors off THIS announce, never a second announce spine
    ([CXC-MCP-ROUTER-MECHANICS]).
    """

    plugin: str
    version: str
    publishes: list[str]
    subscribes: list[str]
    config_schema: dict
    requires: dict
    isolation_floor: str
    #: derived MCP tool descriptors ({plugin}.{tool} + derived input_schema).
    mcp_tools: list[dict] = []
    #: ``"own"`` opts this plugin OUT of the umbrella ([CXC-MCP-FORK-EXCEPTION]);
    #: ``None``/absent IS the umbrella path (the default).
    mcp_endpoint: str | None = None


class ParkedError(RuntimeError):
    """Raised (or recorded) when a plugin's ``requires:`` floor is unmet.

    Carries the specific unmet item so the admin UX can name it rather than
    reporting a generic failure ([CXC-CS-REQUIRES-FLOOR] park-loud)."""

    def __init__(self, plugin: str, unmet: list[str]):
        self.plugin = plugin
        self.unmet = unmet
        super().__init__(
            f"plugin {plugin!r} PARKED — unmet requires: floor: {', '.join(unmet)}")


class ComposePlugin:
    """Base class for every ComposeServer capability.

    A subclass declares (all class-level):
        class Config(BaseModel): ...   # the ONE typed declaration
        name / version                 # identity
        publishes / subscribes         # the (initial) bus surface
        requires                       # the execution floor
        isolation_floor / isolation_prefer

    and implements ``start(bus)`` / ``stop()``. Everything else — the config
    form, the announce payload, the registry entry — is DERIVED here from that
    declaration and never written by hand.
    """

    # ---- the ONE typed declaration -------------------------------------- #
    class Config(BaseModel):
        """Per-plugin config. Override in a subclass; its schema DERIVES the
        config form, the announce payload and the registry entry."""

    # ---- identity + declared surface ------------------------------------ #
    name: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    #: default-loaded plugins ship with the standard ComposeServer build and are
    #: installed unless a deployment opts out ([CXC-CS-DEFAULT-PLUGINS]).
    default_loaded: ClassVar[bool] = False
    publishes: ClassVar[list[str]] = []
    subscribes: ClassVar[list[str]] = []

    # ---- ARTIFACT 4/4: the MCP tool surface (DERIVED) ------------------- #
    #: the plugin's declared MCP tools ([CXC-MCP-TOOL-DECLARATION]). ONE typed
    #: declaration per tool; the MCP JSON-schema derives from each tool's
    #: ``input_model``. Empty = the plugin exposes no MCP surface.
    mcp_tools: ClassVar[list[McpToolDecl]] = []
    #: ``"own"`` opts this plugin OUT of the umbrella entirely
    #: ([CXC-MCP-FORK-EXCEPTION]); ``None`` (absence) IS the umbrella path.
    mcp_endpoint: ClassVar[str | None] = None

    # ---- the two floors ------------------------------------------------- #
    requires: ClassVar[RequiresFloor] = RequiresFloor()
    #: the HARD minimum isolation level; server sets actual >= this.
    isolation_floor: ClassVar[Isolation] = Isolation.INPROCESS
    #: a NON-BINDING preference the server MAY honor for efficiency. It can only
    #: ever ask for LESS isolation than the floor forbids; it never lowers the
    #: floor another plugin relies on ComposeServer to set ([CXC-CS-ISOLATION-FLOOR]).
    isolation_prefer: ClassVar[Isolation] = Isolation.INPROCESS

    def __init__(self, config: "BaseModel | dict | None" = None):
        self.config = self._coerce_config(config)
        self._bus: "ComposeIOBus | None" = None

    @classmethod
    def _coerce_config(cls, config: "BaseModel | dict | None") -> BaseModel:
        if config is None:
            return cls.Config()
        if isinstance(config, cls.Config):
            return config
        if isinstance(config, BaseModel):
            return cls.Config(**config.model_dump())
        return cls.Config(**dict(config))

    # ---- lifecycle ------------------------------------------------------ #
    def start(self, bus: "ComposeIOBus") -> None:
        """Attach the plugin to *bus* and begin publishing/subscribing.

        The plugin publishes and subscribes on the bus DIRECTLY — ComposeServer
        supervises lifecycle, it never sits on the data path ([CXC-CS-MICROKERNEL]
        supervisor-not-mediator)."""
        raise NotImplementedError

    def stop(self) -> None:
        """Release bus subscriptions / timers. Idempotent by contract."""
        self._bus = None

    # ---- the current surface (authoritative; may change at runtime) ----- #
    def current_publishes(self) -> list[str]:
        """The plugin's live published surface — it is the SOLE authority, and
        re-announces on any change ([CXC-CS-DYNAMIC-SURFACE])."""
        return list(self.publishes)

    def current_subscribes(self) -> list[str]:
        return list(self.subscribes)

    # ---- ARTIFACT 1/3: the config form (DERIVED) ------------------------ #
    @classmethod
    def config_schema(cls) -> dict:
        return cls.Config.model_json_schema()

    @classmethod
    def config_form(cls) -> dict:
        """A UI form descriptor derived from the Config JSON schema.

        One field per Config property, carrying type, title, default and
        required-ness — enough to render the config surface without the author
        writing any form code."""
        schema = cls.config_schema()
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = []
        for key, spec in props.items():
            fields.append({
                "name": key,
                "type": spec.get("type", spec.get("anyOf", "any")),
                "title": spec.get("title", key),
                "default": spec.get("default"),
                "required": key in required,
            })
        return {"plugin": cls.name, "title": schema.get("title", cls.name),
                "fields": fields}

    # ---- the MCP tool descriptors (DERIVED) ----------------------------- #
    @classmethod
    def mcp_tool_descriptors(cls) -> list[dict]:
        """The plugin's derived MCP tool descriptors ([CXC-MCP-TOOL-DECLARATION]).

        One entry per declared ``McpToolDecl``, each carrying the exposed
        ``{plugin}.{tool}`` name and the JSON-schema DERIVED from its
        ``input_model``. Read off the announce by the umbrella router
        ([CXC-MCP-ROUTER-MECHANICS]); the author writes none of this."""
        return [t.derived_descriptor(cls.name) for t in cls.mcp_tools]

    # ---- ARTIFACT 2/4: the announce payload (DERIVED) ------------------- #
    def announce(self) -> PluginAnnounce:
        return PluginAnnounce(
            plugin=self.name,
            version=self.version,
            publishes=self.current_publishes(),
            subscribes=self.current_subscribes(),
            config_schema=self.config_schema(),
            requires=self.requires.model_dump(),
            isolation_floor=self.isolation_floor.name.lower(),
            mcp_tools=self.mcp_tool_descriptors(),
            mcp_endpoint=self.mcp_endpoint,
        )

    # ---- ARTIFACT 3/4: the registry entry (DERIVED) -------------------- #
    @classmethod
    def registry_entry(cls) -> dict:
        return {
            "name": cls.name,
            "version": cls.version,
            "default_loaded": cls.default_loaded,
            "publishes": list(cls.publishes),
            "subscribes": list(cls.subscribes),
            "config_schema": cls.config_schema(),
            "requires": cls.requires.model_dump(),
            "isolation_floor": cls.isolation_floor.name.lower(),
            "isolation_prefer": cls.isolation_prefer.name.lower(),
            "mcp_tools": cls.mcp_tool_descriptors(),
            "mcp_endpoint": cls.mcp_endpoint,
        }

    # ---- the requires: floor (the BINDER evaluates; the plugin declares) - #
    def unmet_requirements(
        self, available_capabilities: "set[str] | None" = None,
        ready_evaluator: "Any" = None,
    ) -> list[str]:
        """The specific unmet items of this plugin's ``requires:`` floor.

        Empty list => the floor is met. ComposeServer is the BINDER: it calls
        this continuously, PARKS the plugin while the list is non-empty, and
        UNPARKS it the moment the list empties — no restart ([CXC-CS-REQUIRES-FLOOR]
        dynamic-rebinding). The ``ready:`` predicate's grammar lives in the DSL
        spec; absent an evaluator a declared predicate is treated as UNMET
        (fail-closed) so a park is never mistaken for readiness.
        """
        available = available_capabilities or set()
        unmet = [f"capability:{c}" for c in self.requires.capabilities
                 if c not in available]
        if self.requires.ready is not None:
            if ready_evaluator is None:
                unmet.append(f"ready:{self.requires.ready} (no evaluator)")
            elif not ready_evaluator(self.requires.ready):
                unmet.append(f"ready:{self.requires.ready}")
        return unmet

    def resolved_isolation(self, server_minimum: "Isolation | None" = None) -> Isolation:
        """The actual isolation level: max(floor, server_minimum) — never below
        the floor ([CXC-CS-ISOLATION-FLOOR] level >= floor)."""
        floor = self.isolation_floor
        if server_minimum is None:
            return floor
        return Isolation(max(int(floor), int(server_minimum)))

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<{type(self).__name__} name={self.name!r} v{self.version}>"
