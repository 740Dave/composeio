"""ComposeServer plugin LOADER — config-manifest discovery, validated BEFORE
instantiation ([SPEC:CXC-CS-LOADER], `87-composeserver-plugin-loader.lts`).

This module turns the hand-wired plugin library (``standup()`` /
``ComposeServer.add_tenant()``, which take HAND-INSTANTIATED plugin objects)
into an actual host driven by a per-tenant manifest. It is a pure CONSUMER of
the landed code in ``composeio/composeserver.py`` and ``composeio/plugin.py``:
it reuses ``ComposeServer.add_tenant()`` / ``standup()`` /
``ComposePlugin.unmet_requirements()`` / ``ComposeServer.provide_capability()``
/ ``StandupResult`` VERBATIM. It does not modify, subclass, or re-implement any
of them ([CXC-CSL-LOADER-INTERFACE] reuse-by-reference).

The net-new surface is exactly four things:
  1. class resolution from a manifest ``ref`` — a registered name OR a
     ``"module.path:ClassName"`` dotted import ([CXC-CSL-MANIFEST-SHAPE]);
  2. config validation against the plugin's pydantic ``Config`` BEFORE
     instantiation, by constructing ``cls.Config(**config)`` DIRECTLY
     ([CXC-CSL-CONFIG-VALIDATION]);
  3. the ``resolve:`` / ``config:`` park taxonomy extending the landed
     ``capability:`` / ``ready:`` prefixes ([CXC-CSL-ERROR-TAXONOMY]);
  4. surfacing those pre-instantiation parks on ``_server/plugins/<key>/status``
     and the ``_server/status`` aggregate — the one place the landed
     announce path cannot reach, because it has no instance to ``.announce()``
     ([CXC-CSL-STATUS-SURFACE]).

SCOPE (ratified forks): discovery is config-manifest (v1); hot-reload is OUT of
v1 (``load_tenant_manifest`` is one-shot per tenant); the manifest FILE format
is DEFERRED — this module takes an already-parsed ``manifest: dict`` and does no
file/bus parsing ([DD-CSL-DISCOVERY], [DD-CSL-HOT-RELOAD], [DD-CSL-MANIFEST-FORMAT]).
"""
from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import ValidationError

from composeio.plugin import ComposePlugin

if TYPE_CHECKING:  # avoid importing the server at module load; only used by type
    from composeio.composeserver import ComposeServer, StandupResult

logger = logging.getLogger("composeio.composeserver_loader")


# --------------------------------------------------------------------------- #
# The named resolution failure — never a bare ImportError/KeyError past the loader
# ([CXC-CSL-LOADER-INTERFACE], [CXC-CSL-ERROR-TAXONOMY]).
# --------------------------------------------------------------------------- #
class PluginResolutionError(Exception):
    """A manifest ``ref`` could not be resolved to a ``ComposePlugin`` subclass.

    NAMED on purpose: ``load_tenant_manifest`` catches EXACTLY this type and
    converts it into a ``resolve:`` park; no bare ``ImportError``/``AttributeError``/
    ``KeyError`` is ever allowed to escape the loader ([CXC-CSL-ERROR-TAXONOMY]).
    """

    def __init__(self, ref: str, reason: str):
        self.ref = ref
        self.reason = reason
        super().__init__(f"cannot resolve plugin ref {ref!r}: {reason}")


@dataclass
class LoaderResult:
    """What a manifest load produced — inspectable, mirroring ``StandupResult``.

    ``standup`` is the object ``ComposeServer.add_tenant()`` returned, UNCHANGED
    (requires-floor ``parked``, ``loaded``, ``subprocess_pending``). ``config_parked``
    holds the loader-native parks (``resolve:`` / ``config:``) keyed by the raw
    manifest ``ref`` (resolution failed) or the resolved class ``name`` (config
    failed) ([CXC-CSL-LOADER-INTERFACE]).
    """

    tenant_id: str
    standup: "StandupResult"
    config_parked: dict[str, list[str]] = field(default_factory=dict)

    def is_clean(self) -> bool:
        return self.standup.is_clean() and not self.config_parked


# --------------------------------------------------------------------------- #
# Class resolution ([CXC-CSL-MANIFEST-SHAPE]).
# --------------------------------------------------------------------------- #
def resolve_plugin_class(
    ref: str,
    registry: "dict[str, type[ComposePlugin]] | None" = None,
) -> "type[ComposePlugin]":
    """Resolve a manifest ``ref`` to a ``ComposePlugin`` subclass, two ways in
    order ([CXC-CSL-MANIFEST-SHAPE] ref-resolution):

      1. a REGISTERED NAME — ``ref`` is a key in ``registry``;
      2. a DOTTED IMPORT PATH — ``ref`` is ``"module.path:ClassName"``, imported
         via ``importlib.import_module`` then ``getattr``.

    A ``ref`` with no ``:`` that is ALSO absent from ``registry`` is a resolution
    failure — never silently treated as a bare module import. An ``ImportError``/
    ``AttributeError``, or a resolved object that is not a ``ComposePlugin``
    subclass, are resolution failures too. Every failure raises
    ``PluginResolutionError(ref, reason)`` — never a bare exception
    ([CXC-CSL-LOADER-INTERFACE], [CXC-CSL-ERROR-TAXONOMY]).
    """
    registry = registry or {}

    # (1) registered name — tried FIRST, so a name match wins even if it happens
    #     to contain a ':' (a caller's registry key is authoritative).
    if ref in registry:
        cls = registry[ref]
        if not (isinstance(cls, type) and issubclass(cls, ComposePlugin)):
            raise PluginResolutionError(
                ref, f"registered object {cls!r} is not a ComposePlugin subclass")
        return cls

    # (2) dotted import path — REQUIRES a ':'; a bare name absent from the
    #     registry is a failure, not a module import.
    if ":" not in ref:
        raise PluginResolutionError(
            ref, "not a registered name and not a 'module.path:ClassName' dotted path")

    module_path, _, class_name = ref.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PluginResolutionError(ref, f"import failed: {exc}") from exc
    try:
        obj = getattr(module, class_name)
    except AttributeError as exc:
        raise PluginResolutionError(
            ref, f"class {class_name!r} not found in module {module_path!r}") from exc
    if not (isinstance(obj, type) and issubclass(obj, ComposePlugin)):
        raise PluginResolutionError(
            ref, f"resolved object {obj!r} is not a ComposePlugin subclass")
    return obj


# --------------------------------------------------------------------------- #
# THE ONE config-validation path ([CXC-CSL-CONFIG-VALIDATION]).
#
# Extracted from load_tenant_manifest so there is a SINGLE evaluator: the loader
# calls it before instantiation, and the ComposeUX DOM server-side validator
# ([CXC-DOM-SERVER-VALIDATION], render/dom/validation.py) IMPORTS this exact
# function rather than re-deriving the pydantic call + the config:<field>: <msg>
# taxonomy. "A config that validates is a config that will actually run the same
# way" only holds if there is ONE evaluator — this is it.
# --------------------------------------------------------------------------- #
def _validate_config(config_model, payload):
    """Shared core: construct ``config_model(**payload)`` DIRECTLY (validate
    BEFORE instantiation) and return ``(validated_instance_or_None, parks)``.

    ``parks`` is a list of ``config:<field>: <message>`` strings
    ([CXC-CSL-ERROR-TAXONOMY]), one per invalid field, or ``[]`` when valid.
    The loader uses the returned instance to construct the plugin WITHOUT
    re-validating (single construction); ``validate_config`` exposes only the
    parks for callers that need just the verdict.
    """
    if not isinstance(payload, dict):
        return None, [
            f"config:<root>: expected an object, got {type(payload).__name__}"]
    try:
        return config_model(**payload), []
    except ValidationError as exc:
        reasons = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
            reasons.append(f"config:{loc}: {err.get('msg', 'invalid')}")
        return None, reasons


def validate_config(config_model, payload) -> "list[str]":
    """Validate *payload* against the pydantic *config_model* by constructing
    ``config_model(**payload)`` DIRECTLY — the IDENTICAL call sequence
    [CXC-CSL-CONFIG-VALIDATION] specifies (validate BEFORE instantiation).

    Returns the ``config:<field>: <message>`` parks ([CXC-CSL-ERROR-TAXONOMY]),
    ONE per invalid field, or an EMPTY list when the payload is valid. Never
    raises for a validation failure (that is the normal, expected outcome the
    caller surfaces per field); a non-``ValidationError`` bug still propagates,
    unmasked. Both ``load_tenant_manifest`` and the DOM server-side validator
    call THIS — one path, no drift.
    """
    return _validate_config(config_model, payload)[1]


# --------------------------------------------------------------------------- #
# The entry point ([CXC-CSL-LOADER-INTERFACE]).
# --------------------------------------------------------------------------- #
def load_tenant_manifest(
    server: "ComposeServer",
    tenant_id: str,
    manifest: dict,
    *,
    registry: "dict[str, type[ComposePlugin]] | None" = None,
    connectors: "list[dict] | None" = None,
    capabilities: "set[str] | None" = None,
    start: bool = False,
    plugin_host=None,
) -> LoaderResult:
    """Load one tenant's plugin manifest and hand the instances to the EXISTING
    ``ComposeServer.add_tenant()`` binder unchanged ([CXC-CSL-LOADER-INTERFACE]).

    For each entry in ``manifest.get("plugins", [])``:
      1. resolve the class (``resolve_plugin_class``) — on failure record a
         ``resolve:`` park keyed by the raw ``ref`` and CONTINUE to the next
         entry ([CXC-CSL-MANIFEST-SHAPE], [CXC-CSL-ERROR-TAXONOMY]);
      2. validate ``entry.get("config", {})`` by constructing
         ``cls.Config(**config)`` DIRECTLY (BEFORE instantiation) — on a
         ``pydantic.ValidationError`` record one ``config:<field>: <msg>`` park
         per error dict, keyed by ``cls.name``, and CONTINUE
         ([CXC-CSL-CONFIG-VALIDATION]);
      3. on success construct ``cls(config=validated)`` — passing the ALREADY
         validated ``Config`` instance, which ``_coerce_config`` short-circuits
         (``isinstance(config, cls.Config)``), so the plugin is never
         double-validated.

    A single malformed entry never crashes the loader or a sibling — every
    failure is a caught, named park and the run continues. The instantiated set
    then goes to ``server.add_tenant(...)`` UNCHANGED, which performs the
    ``requires:``-floor evaluation, the subprocess-pending seam, and the
    per-instance ``_server/*`` announce — none of which this loader
    re-implements. Finally the pre-instantiation parks are surfaced on the admin
    status surface ([CXC-CSL-STATUS-SURFACE]).

    ``plugin_host`` (spec 95 [CXC-PC-DISCOVERY], ratified decision 5): when given
    (a ``ComposeIOPluginHost``), an entry whose ``plugin`` is a bare DOMAIN (not
    in ``registry``, no ``:``) names an INSTALLED plug-in: its stored config is
    set from the entry and it is enabled through the host AFTER the tenant
    exists, at the host's isolation level. A domain with no install record
    parks with ``resolve:no install record for domain '<domain>'`` and is never
    imported. Without ``plugin_host`` the behaviour is unchanged.
    """
    config_parked: dict[str, list[str]] = {}
    instances: list[ComposePlugin] = []
    installed_entries: "list[tuple[str, dict]]" = []

    for entry in manifest.get("plugins", []):
        ref = entry.get("plugin")

        # (0) a bare domain names an INSTALLED plug-in ([CXC-PC-DISCOVERY]).
        if (plugin_host is not None and isinstance(ref, str) and ":" not in ref
                and ref not in (registry or {})):
            if plugin_host.manager.has_install(ref):
                installed_entries.append((ref, entry.get("config", {}) or {}))
            else:
                config_parked[ref] = [f"resolve:no install record for domain {ref!r}"]
                logger.warning("manifest entry %r PARKED (resolve) — no install record", ref)
            continue

        # (1) resolve the class.
        try:
            cls = resolve_plugin_class(ref, registry)
        except PluginResolutionError as exc:
            key = str(ref)
            config_parked[key] = [f"resolve:{exc.reason}"]
            logger.warning("manifest entry %r PARKED (resolve) — %s", key, exc.reason)
            continue

        # (2) validate config BEFORE instantiation — the SHARED single path
        #     ([CXC-CSL-CONFIG-VALIDATION]) constructs cls.Config DIRECTLY and
        #     hands back the validated instance for step 3 (no double-validation).
        raw_config = entry.get("config", {}) or {}
        validated, reasons = _validate_config(cls.Config, raw_config)
        if reasons:
            config_parked[cls.name] = reasons
            logger.warning(
                "plugin %r PARKED (config) — %s", cls.name, "; ".join(reasons))
            continue

        # (3) instantiate with the already-validated Config (no double-validation).
        instances.append(cls(config=validated))

    # Hand the instances to the EXISTING binder, UNCHANGED. This is where the
    # requires:-floor park, the subprocess-pending seam, per-tenant instancing,
    # and the per-instance `_server/*` announce all happen — reused verbatim.
    standup_result = server.add_tenant(
        tenant_id,
        connectors=connectors,
        plugins=instances,
        capabilities=capabilities,
        start=start,
    )

    # Surface the pre-instantiation parks via the sanctioned public seam
    # ([CXC-CSL-STATUS-SURFACE]). merge_parked records them in the tenant's
    # SEPARATE `external_parked` dict (never `StandupResult.parked`) and
    # re-announces — the per-key `_server/plugins/<key>/status` + the merged
    # `_server/status` aggregate, refreshed on every republish tick. No private
    # state is touched by the loader.
    for domain, cfg in installed_entries:
        try:
            plugin_host.manager.configure(domain, cfg)
            if plugin_host.manager.state(domain) != "enabled":
                plugin_host.manager.enable(domain)
        except Exception as exc:  # a named park, never a crash of the loader
            config_parked[domain] = [f"resolve:{type(exc).__name__}: {exc}"]

    if config_parked:
        server.merge_parked(tenant_id, config_parked)

    return LoaderResult(
        tenant_id=tenant_id, standup=standup_result, config_parked=config_parked)
