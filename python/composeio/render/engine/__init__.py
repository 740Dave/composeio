"""
ComposeUX Layer 2 Engine — platform-independent (M1).

Spec: [CXC-R-ENGINE] — layout, hit-testing/pointer dispatch, focus,
animation, asset manager, and the .compose.yaml -> widget-tree parser.
Everything here talks only to the abstract Canvas contract and the
ComposeIO bus; nothing imports a platform library.

parser/RenderApp are exposed via lazy module attributes: the parser
imports the widget catalog, and container-flavored widgets (TabWidget)
import engine.layout — eager re-export here would make that a cycle.
"""

from .layout import (Container, FlexContainer, GridContainer,
                     PixelStackContainer)
from .animation import Animator, EASING
from .assets import AssetManager
from .focus import FocusManager

__all__ = [
    "Container", "FlexContainer", "GridContainer", "PixelStackContainer",
    "Animator", "EASING", "AssetManager", "FocusManager",
    "parse_compose_yaml", "parse_widget_tree", "RenderApp",
]

_LAZY = {
    "parse_compose_yaml": ("composeio.render.engine.parser", "parse_compose_yaml"),
    "parse_widget_tree": ("composeio.render.engine.parser", "parse_widget_tree"),
    "RenderApp": ("composeio.render.engine.runtime", "RenderApp"),
}


def __getattr__(name):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    import importlib
    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value
