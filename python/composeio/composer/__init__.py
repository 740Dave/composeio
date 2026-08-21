"""ComposeIO Composer on the render engine ([CXC-RM-M4]).

The redesigned Composer — Widget Workshop / tabbed Widget Configuration /
telemetry bar per specs/ux/composer-workshop-previs.v1.html — rebuilt as
a render-engine application.  Bus-native and Intelligence-drivable: every
builder action is a bus message on the same `composer/*` namespaces the
CTk Composer and the intelligence_mcp bridge already speak.

Launch:  python -m composeio.composer [design.compose.yaml]
"""

from .document import CompositeDocument, LayerDef, NamespaceDef

__all__ = ["CompositeDocument", "LayerDef", "NamespaceDef"]
