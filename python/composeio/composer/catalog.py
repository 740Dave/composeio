"""Widget catalog for the Composer's Widgets tab ([CXC-FEAT-PALETTE]).

Every entry is a widget type the render engine parses today
(render/engine/parser.py build_leaf + containers).  ``defaults`` seed a
newly placed layer; ``size`` is the initial px footprint on the
pixel-stack canvas.

Discoverable-registry principle ([DP-CXC-8]): register() extends the
catalog at runtime without touching Composer source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CatalogEntry:
    type_key: str                      # parser type key
    name: str                          # display name
    description: str
    size: tuple = (150, 40)            # initial (w, h) px
    defaults: dict[str, Any] = field(default_factory=dict)
    id_prefix: str = ""

    @property
    def prefix(self) -> str:
        return self.id_prefix or self.type_key


_CATALOG: list[CatalogEntry] = [
    CatalogEntry("button", "Button", "Emits a command on press",
                 size=(150, 36), defaults={"label": "Button"}, id_prefix="btn"),
    CatalogEntry("image", "Image", "bus:// image · fit modes",
                 size=(240, 180), defaults={"src": "", "fit": "contain"},
                 id_prefix="image"),
    CatalogEntry("lottie", "Lottie", "Vector animation · bindable transport",
                 size=(200, 200), defaults={"src": "", "autoplay": True,
                                            "loop": True},
                 id_prefix="anim"),
    CatalogEntry("svg", "SVG", "Vector graphic · decomposed to Canvas",
                 size=(200, 200), defaults={"src": "", "fit": "contain"},
                 id_prefix="svg"),
    CatalogEntry("media", "Video", "Video / webcam / stream · pluggable "
                 "sources · state-on-bus trigger · HW decode opt-in",
                 size=(320, 240), defaults={"src": "", "fit": "contain",
                                            "autoplay": True, "loop": True},
                 id_prefix="media"),
    CatalogEntry("mermaid", "Mermaid", "Live diagram · emit mermaid text on "
                 "thread/diagram · renders via vendored mermaid.js",
                 size=(440, 320), defaults={"source": "",
                                            "namespace": "thread/diagram",
                                            "theme": "dark"},
                 id_prefix="mermaid"),
    CatalogEntry("callout", "Callout", "Data-bound annotation overlay",
                 size=(200, 120), defaults={"text": "callout",
                                            "target": [0.5, 0.5]},
                 id_prefix="callout"),
    CatalogEntry("gauge", "Gauge", "Continuous value dial",
                 size=(140, 120), defaults={"label": "Gauge", "min_val": 0,
                                            "max_val": 100}, id_prefix="gauge"),
    CatalogEntry("relay", "Relay", "Two-state toggle actuator",
                 size=(80, 80), defaults={"label": "Relay"}, id_prefix="relay"),
    CatalogEntry("label", "Label", "Static or bound text",
                 size=(160, 24), defaults={"text": "Label"}, id_prefix="label"),
    CatalogEntry("rect", "Rectangle", "Filled/stroked panel primitive",
                 size=(160, 90), defaults={"fill_color": "#28283C",
                                           "corner_radius": 6},
                 id_prefix="rect"),
    # [DEC-PROJECT-STRUCTURE]/composer-dradis-gap-analysis.md gap #5: circle + led
    # are fully implemented render-engine primitives (used by the DRADIS demos) but
    # were unreachable from the drag-drop palette — reachable only via hand-edited
    # YAML. Added so a human can compose with them directly.
    CatalogEntry("circle", "Circle", "Filled/stroked circle primitive",
                 size=(80, 80), defaults={"radius": 30, "fill_color": "#28283C",
                                          "stroke_color": "#5A5A78",
                                          "stroke_width": 1},
                 id_prefix="circle"),
    CatalogEntry("led", "LED", "Status indicator with glow",
                 size=(64, 64), defaults={"state": "off", "label": "LED"},
                 id_prefix="led"),
    CatalogEntry("checkbox", "Checkbox", "Boolean option",
                 size=(180, 28), defaults={"label": "Option"}, id_prefix="check"),
    CatalogEntry("text_input", "Text Input", "Single-line text entry",
                 size=(220, 64), defaults={"label": "Value"}, id_prefix="input"),
    CatalogEntry("coordinate", "Coordinate Entry", "Digit-entry with arm/commit",
                 size=(240, 60), defaults={"label": "Coordinates"},
                 id_prefix="coord"),
    CatalogEntry("bus_monitor", "Bus Monitor", "Live namespace traffic table",
                 size=(320, 180), defaults={"label": "Bus Monitor"},
                 id_prefix="busmon"),
    CatalogEntry("dialog", "Dialog", "Modal overlay (visible-bound)",
                 size=(0, 0), defaults={"title": "Dialog"}, id_prefix="dialog"),
    # [SPEC:CXC-FLAUTH] headless logic node — authored in Flow mode,
    # deployed to a HeadlessRuntime; geometry-free, renders nothing.
    CatalogEntry("logic", "Logic (headless)",
                 "Bus logic node · expr outputs · no render",
                 size=(0, 0),
                 defaults={"headless": {"schedule": {"mode": "continuous"}}},
                 id_prefix="logic"),
]


def entries() -> list[CatalogEntry]:
    return list(_CATALOG)


def get(type_key: str) -> Optional[CatalogEntry]:
    for e in _CATALOG:
        if e.type_key == type_key:
            return e
    return None


def register(entry: CatalogEntry) -> None:
    """Extend the palette without modifying Composer source ([DP-CXC-8])."""
    existing = get(entry.type_key)
    if existing is not None:
        _CATALOG[_CATALOG.index(existing)] = entry
    else:
        _CATALOG.append(entry)
