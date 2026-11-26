"""composebus — the ComposeBus kernel ([SPEC:CXC-COMPOSEBUS-KERNEL] §7).

The bare, transport-agnostic bus: ``composebus.bus.ComposeIOBus`` plus the
namespace, state-engine, freshness, clock, selector, transform, envelope,
transport-contract and plugin-contract modules, and the five default plugins a
bare bus installs (``composebus.plugins``). Depends on the standard library and
pydantic only.

Importing this package imports nothing else; import the module you need
(e.g. ``from composebus.bus import ComposeIOBus``).
"""

__version__ = "0.1.2"
