"""Dynamic namespace selectors.

DP-CIO-21: A module's output namespace can be dynamic — determined at runtime
by a selector namespace's current value.

Example: `namespace: ${active_camera}_ptz` with `selector: camera_select`
When camera_select value is "camera_2", publishes to "camera_2_ptz".
"""

from __future__ import annotations
import re
from typing import Any


# Pattern matches ${selector_namespace} in a namespace string
_SELECTOR_PATTERN = re.compile(r"\$\{([^}]+)\}")


def is_dynamic(namespace: str) -> bool:
    """Check if a namespace string contains a dynamic selector."""
    return bool(_SELECTOR_PATTERN.search(namespace))


def extract_selector_names(namespace: str) -> list[str]:
    """Extract all selector namespace names from a dynamic namespace string."""
    return _SELECTOR_PATTERN.findall(namespace)


def resolve(namespace_template: str, selector_values: dict[str, Any]) -> str:
    """Resolve a dynamic namespace template using current selector values.

    Args:
        namespace_template: String with ${...} placeholders, e.g. "${active_camera}_ptz"
        selector_values: Dict mapping selector namespace names to their current values

    Returns:
        Resolved namespace string, e.g. "camera_2_ptz"

    Raises:
        KeyError: If a selector namespace has no current value
    """
    def replace_match(m):
        selector_name = m.group(1)
        if selector_name not in selector_values:
            raise KeyError(f"Selector '{selector_name}' has no current value")
        return str(selector_values[selector_name])

    return _SELECTOR_PATTERN.sub(replace_match, namespace_template)
