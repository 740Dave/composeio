"""
ComposeUX Mermaid Widget ([SPEC:CXC-IC] [CXC-IC-CANVAS]) — the composable
canvas at its purest: emit mermaid text on ``thread/diagram`` and a subscribed
widget renders it, no model round-trip.

One `MermaidWidget` with two render tiers over ONE bus namespace:

    web tier    the vendored, pinned mermaid.min.js rasterizes the diagram
                live in the Console (self-contained/offline, never a CDN).
    Canvas tier degrades to a SOURCE panel (the bytes on the bus) on the
                CYD/MinnowBoard/desktop Canvas backends — the media/svg
                graceful-degradation idiom; the screen is never broken.

The vendored bundle (``assets/mermaid.min.js``, mermaid v{version}) is reached
via :func:`~composeio.render.widgets.mermaid.widget.web_asset_path`.

NOTE: this module does NOT self-register with the global widget registry
(module-local by design, mirroring the media package).  The parser maps widget
type "mermaid" -> MermaidWidget (render/engine/parser.py build_leaf).

Run the self-test:  python -m composeio.render.widgets.mermaid --selftest
"""

from __future__ import annotations

from .widget import (
    DEFAULT_NAMESPACE,
    MERMAID_VERSION,
    MermaidWidget,
    web_asset_path,
)

__doc__ = __doc__.format(version=MERMAID_VERSION)

__all__ = [
    "MermaidWidget",
    "DEFAULT_NAMESPACE",
    "MERMAID_VERSION",
    "web_asset_path",
]


if __name__ == "__main__":  # pragma: no cover
    import sys
    from .selftest import main
    sys.exit(main(sys.argv[1:]))
