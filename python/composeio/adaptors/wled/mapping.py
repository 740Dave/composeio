"""Declarative semantic -> WLED state mapping for the WLED DRIVEN adaptor.

[CXC-HAL-ACTUATE] (spec 27, node [SPEC:CXC-HAL]): the WLED adaptor subscribes
SEMANTIC namespaces and drives WLED via its JSON API (POST /json/state) + MQTT
topics -- ZERO firmware. This module owns the mapping from a namespace value to
a WLED JSON *state fragment*. The mapping is declarative and overridable, never
hardcoded to one install: the same `scene/mood = "storm"` may be a preset id on
one strip and an effect+palette on another.

WLED JSON state fragments this module emits (all partial state, merged
statefully by WLED itself -- one namespace change -> one fragment):
  {"on": bool}                                    -- power
  {"bri": 0..255}                                 -- master brightness
  {"seg": [{"id": n, "col": [[r, g, b]]}]}        -- segment primary color
  {"ps": id} / {"seg": [{"fx": .., "pal": ..}]}   -- preset / effect (scene)

Value engines (mirrors the HAL value-map/engine idea, [CXC-HAL-PLATFORM]):
  on          bool-ish (True/False/1/0/"on"/"off")           -> {"on": ...}
  brightness  0..1 float (fraction) OR 0..255 int (raw)      -> {"bri": ...}
  color       "#rrggbb" / "rrggbb" / [r,g,b] / named color   -> seg col
  scene       string keyed into a table of WLED fragments    -> preset/effect
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger("composeio.adaptors.wled")

# A small named-color table so `light/color = "red"` works out of the box.
# Overridable per-install by editing the mapping rules, never load-bearing.
_NAMED_COLORS: dict[str, list[int]] = {
    "black": [0, 0, 0],
    "white": [255, 255, 255],
    "red": [255, 0, 0],
    "green": [0, 255, 0],
    "blue": [0, 0, 255],
    "yellow": [255, 255, 0],
    "cyan": [0, 255, 255],
    "magenta": [255, 0, 255],
    "orange": [255, 128, 0],
    "purple": [128, 0, 128],
    "warm": [255, 180, 107],
    "amber": [255, 191, 0],
}


# --- value coercion -------------------------------------------------------

def coerce_bool(value: Any) -> bool:
    """Coerce a bus value to a WLED on/off boolean."""
    if isinstance(value, str):
        return value.strip().lower() in ("on", "true", "1", "yes")
    return bool(value)


def to_bri(value: Any) -> int:
    """Normalize a brightness value to WLED's 0..255 `bri`.

    Disambiguation (documented, deterministic): a float in [0.0, 1.0] is a
    FRACTION (0.5 -> 128, 1.0 -> 255); anything else is a RAW 0..255 level,
    clamped. So `0.5` -> 128 and `128` -> 128; `1` (int) -> 1, `1.0` -> 255.
    """
    if isinstance(value, bool):  # bool is an int subclass -- treat as on/off
        return 255 if value else 0
    if isinstance(value, float) and 0.0 <= value <= 1.0:
        n = round(value * 255)
    else:
        n = round(float(value))
    return max(0, min(255, n))


def parse_color(value: Any) -> list[int]:
    """Parse a color value into an [r, g, b] triple (0..255 ints)."""
    if isinstance(value, (list, tuple)):
        if len(value) < 3:
            raise ValueError(f"color list needs 3 components, got {value!r}")
        return [max(0, min(255, int(c))) for c in value[:3]]
    if isinstance(value, int):  # packed 0xRRGGBB
        return [(value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
    if isinstance(value, str):
        s = value.strip().lower()
        if s in _NAMED_COLORS:
            return list(_NAMED_COLORS[s])
        h = s[1:] if s.startswith("#") else s
        if len(h) == 3:  # shorthand #rgb
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return [int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)]
    raise ValueError(f"cannot parse color: {value!r}")


# --- kind handlers: (value, spec) -> WLED state fragment ------------------

def _kind_on(value: Any, spec: dict) -> dict:
    return {"on": coerce_bool(value)}


def _kind_brightness(value: Any, spec: dict) -> dict:
    return {"bri": to_bri(value)}


def _kind_color(value: Any, spec: dict) -> dict:
    rgb = parse_color(value)
    seg_id = spec.get("seg", 0)
    return {"seg": [{"id": seg_id, "col": [rgb]}]}


def _kind_scene(value: Any, spec: dict) -> dict:
    table: dict[str, dict] = spec.get("table", {})
    key = str(value)
    if key in table:
        # deep-ish copy so callers never mutate the mapping's template
        import copy
        return copy.deepcopy(table[key])
    default = spec.get("default")
    if default is not None:
        import copy
        return copy.deepcopy(default)
    logger.warning("scene value %r has no mapping and no default", value)
    return {}


# kind registry -- extensible: register_kind() adds custom engines per install
_KINDS: dict[str, Callable[[Any, dict], dict]] = {
    "on": _kind_on,
    "brightness": _kind_brightness,
    "color": _kind_color,
    "scene": _kind_scene,
}


def register_kind(name: str, handler: Callable[[Any, dict], dict]) -> None:
    """Register a custom mapping kind (value, spec) -> WLED fragment."""
    _KINDS[name] = handler


# --- default declarative mapping ------------------------------------------
# Generic, platform-pure, overridable. NO game/BSG specifics. Keys are the
# SEMANTIC namespaces the adaptor subscribes; each spec names a `kind` engine.
DEFAULT_MAPPING: dict[str, dict] = {
    "light/on": {"kind": "on"},
    "light/brightness": {"kind": "brightness"},
    "light/color": {"kind": "color", "seg": 0},
    "scene/mood": {
        "kind": "scene",
        "default": {},
        "table": {
            # a scene may be a preset id ...
            "storm": {"ps": 5},
            # ... or an effect + palette + brightness combo (still declarative)
            "calm": {"on": True, "bri": 90, "seg": [{"fx": 2, "sx": 40, "pal": 3}]},
            "alert": {"on": True, "bri": 255, "seg": [{"fx": 1, "col": [[255, 0, 0]]}]},
            "party": {"on": True, "bri": 200, "seg": [{"fx": 89, "sx": 200, "pal": 11}]},
            "off": {"on": False},
        },
    },
}


class WLEDMapping:
    """A declarative semantic-namespace -> WLED-state-fragment map.

    Construct with `WLEDMapping()` for the generic defaults, or pass/override
    rules for a specific install. `namespaces()` tells the adaptor what to
    subscribe; `render(ns, value)` turns a bus value into a WLED fragment.
    """

    def __init__(self, rules: dict[str, dict] | None = None):
        self.rules: dict[str, dict] = (
            {k: dict(v) for k, v in DEFAULT_MAPPING.items()}
            if rules is None
            else {k: dict(v) for k, v in rules.items()}
        )

    def namespaces(self) -> list[str]:
        """The semantic namespaces this mapping covers (adaptor subscribes these)."""
        return list(self.rules.keys())

    def override(self, namespace: str, spec: dict) -> "WLEDMapping":
        """Add or replace one namespace rule (per-install override). Chainable."""
        self.rules[namespace] = dict(spec)
        return self

    def render(self, namespace: str, value: Any) -> dict:
        """Map a namespace value to a WLED JSON state fragment ({} if unmapped)."""
        spec = self.rules.get(namespace)
        if spec is None:
            return {}
        kind = spec.get("kind")
        handler = _KINDS.get(kind)
        if handler is None:
            logger.warning("no handler for mapping kind %r (ns %s)", kind, namespace)
            return {}
        try:
            return handler(value, spec)
        except Exception as exc:  # a bad value must not kill the bus dispatch
            logger.warning("mapping %s=%r failed: %s", namespace, value, exc)
            return {}

    @classmethod
    def from_config(cls, config: dict | None) -> "WLEDMapping":
        """Build from a plain dict (e.g. loaded YAML): {"mapping": {ns: spec}}."""
        if not config:
            return cls()
        rules = config.get("mapping", config)
        return cls(rules=rules)
