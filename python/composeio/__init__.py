"""ComposeIO — Software-Defined I/O for Hardware Projects.

Bus-oriented composition model using namespace-based pub/sub.
Behavior defined in YAML configuration, not firmware.
"""

__version__ = "0.1.0"

# --- the host overlay: FIRST, before any submodule ---------------------------- #
# [CXC-SCL-SCRUB-OVERLAY-PARITY]. Submodules read the environment at their own
# import time (fleet, the decision_ops review tools, composeserver_plugins), so
# the per-host overlay must fill unset names before any of them load. It sets
# only names the environment lacks and never raises; see composeio/_overlay.py.
from composeio import _overlay as _overlay_mod
_overlay_mod.apply()
del _overlay_mod

# --- the composebus kernel: seat + identity aliases --------------------------- #
# [SPEC:CXC-COMPOSEBUS-KERNEL] §7. The bare-bus kernel modules MOVED to the
# `composebus` distribution (python/composebus/). Every moved `composeio.<name>`
# resolves to the IDENTICAL module object `composebus.<name>`
# ([CXC-CBK-PKG-COMPAT-IDENTITY]): `from composeio.bus import ComposeIOBus`,
# `mock.patch("composeio.bus.x")` and isinstance checks keep working and agree
# across both names. The alias is LAZY — `composeio.<name>` imports
# `composebus.<name>` only when it is itself imported — so the import footprint
# of every entrypoint is unchanged. `composeio.plugins` stays a REAL package
# (three non-moving plugins live in it); only its five moved default-plugin
# SUBMODULES are aliased (amendment-plugins-package).
def _seat_composebus() -> None:
    """TRANSITIONAL monorepo resolution ([CXC-CBK-PKG-DISTRIBUTION]
    monorepo-resolution). An installed composebus always wins; otherwise the
    sibling source directory python/composebus — located relative to this file,
    never an absolute path — goes to the front of sys.path.

    "Not importable" includes a NAMESPACE-package hit: with python/ on sys.path
    (every `cd python && ...` entrypoint) the bare directory python/composebus/
    satisfies `import composebus` as an EMPTY namespace package instead of
    raising ImportError, so an ImportError-only check would never fall back.
    Removal condition: composeio declares a dependency on a published composebus.
    """
    import importlib
    import importlib.util
    import os
    import sys

    loaded = sys.modules.get("composebus")
    if loaded is not None and getattr(loaded, "__file__", None):
        return
    if loaded is None:
        try:
            spec = importlib.util.find_spec("composebus")
        except (ImportError, ValueError):
            spec = None
        if spec is not None and spec.origin is not None:
            return
    sibling = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "composebus")
    if sibling not in sys.path:
        sys.path.insert(0, sibling)
    sys.modules.pop("composebus", None)
    importlib.invalidate_caches()


def _install_composebus_aliases() -> None:
    import importlib
    import importlib.abc
    import importlib.machinery
    import importlib.util
    import sys

    moved = (
        "bus", "bus_clock", "bus_envelope", "freshness", "module", "namespace",
        "selector", "state_engine", "transport", "transport_contract",
        "transport_local", "plugin", "transforms",
    )
    moved_plugins = ("state_manager", "discovery", "time", "liveness", "log")
    alias = {f"composeio.{n}": f"composebus.{n}" for n in moved}
    alias.update({f"composeio.plugins.{n}": f"composebus.plugins.{n}"
                  for n in moved_plugins})

    def _real_spec(target):
        """The composebus file's OWN spec, found fresh by the path finder (never
        via sys.modules, whose entry may be the very object being repaired)."""
        parent = sys.modules[target.rpartition(".")[0]]
        return importlib.machinery.PathFinder.find_spec(target, parent.__path__)

    class _ComposebusAlias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        """`composeio.<moved>` -> the composebus module object itself."""

        def find_spec(self, fullname, path=None, target=None):
            if fullname not in alias:
                return None
            return importlib.util.spec_from_loader(fullname, self)

        def create_module(self, spec):
            # The composebus module is returned AS the composeio module; the
            # import system keeps its own __name__/__spec__ (it never
            # overrides attributes a module already carries), so both names
            # bind one object.
            return importlib.import_module(alias[spec.name])

        def exec_module(self, module):
            # The import system has just stamped the ALIAS spec (name
            # composeio.<x>, this loader, origin None) onto the shared module
            # object: `__spec__` is the one attribute it sets unconditionally.
            # Left there, `importlib.reload` re-finds the alias and re-executes
            # NOTHING. Restore the real composebus spec, so a reload through
            # either name re-executes the composebus file.
            module.__spec__ = _real_spec(alias[module.__spec__.name])

        # `python -m composeio.bus_envelope` (runpy) asks the loader for the
        # module's CODE, not for a module object; hand over the composebus
        # file's own code so `-m` entrypoints keep running unchanged.
        def get_code(self, fullname):
            spec = importlib.util.find_spec(alias[fullname])
            return spec.loader.get_code(alias[fullname])

        def is_package(self, fullname):
            return False

    if not any(type(f).__name__ == "_ComposebusAlias" for f in sys.meta_path):
        sys.meta_path.insert(0, _ComposebusAlias())


_seat_composebus()
_install_composebus_aliases()
del _seat_composebus, _install_composebus_aliases
# ----------------------------------------------------------------------------- #

from composeio.bus import ComposeIOBus
from composeio.namespace import Namespace
from composeio.state_engine import (
    StateEngine,
    BinaryEngine,
    TriLevelEngine,
    StopLightEngine,
    ContinuousEngine,
    EnumEngine,
    JsonEngine,
)
from composeio.module import Module
from composeio.app import ComposeApp, AppModule
from composeio.web import WebBusModule
from composeio.transport_replay import ReplayTransport
from composeio.transport_synthetic import SyntheticTransport
from composeio.capture import BusCapture
from composeio.generate import generate_trace

__all__ = [
    "ComposeIOBus",
    "ComposeApp",
    "AppModule",
    "WebBusModule",
    "Namespace",
    "StateEngine",
    "BinaryEngine",
    "TriLevelEngine",
    "StopLightEngine",
    "ContinuousEngine",
    "EnumEngine",
    "JsonEngine",
    "Module",
    "ReplayTransport",
    "SyntheticTransport",
    "BusCapture",
    "generate_trace",
]
