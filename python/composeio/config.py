"""YAML config parser, validator, and binary compiler.

DP-CIO-7: Configuration is the product. YAML is the builder's primary artifact.
This module parses ComposeIO YAML configs, validates them against the schema,
and compiles them to compact binary format for edge MCU flash.
"""

from __future__ import annotations
import hashlib
import struct
from pathlib import Path
from typing import Any

import yaml

from composeio.bus import ComposeIOBus
from composeio.namespace import Scope, Authority
from composeio.state_engine import StateEngine
from composeio.transforms import TransformChain


# --- YAML Parsing ---

def load_config(path: str | Path) -> dict:
    """Load and parse a ComposeIO YAML config file."""
    with open(path) as f:
        return yaml.safe_load(f)


def load_config_string(yaml_string: str) -> dict:
    """Parse a ComposeIO YAML config from a string."""
    return yaml.safe_load(yaml_string)


# --- Validation ---

_VALID_ROLES = {
    "button-matrix", "relay-controller", "stepper-controller",
    "pwm-bank", "fingerprint-gate", "composite-device", "sensor-hub",
}

_VALID_INPUT_TYPES = {"momentary", "toggle", "hold_ramp", "continuous", "encoder"}

_VALID_LED_BEHAVIORS = {
    "solid", "off", "follow_state", "while_pressed",
    "blink", "pulse", "proportional", "chase",
}

_VALID_BUILTIN_ENGINES = {"binary", "tri_level", "stop_light", "continuous_0_255",
                          "continuous_0_100", "json"}

_VALID_NAMESPACE_MODES = {"state", "event_only", "log"}


class ConfigError(Exception):
    """Raised when a config file has validation errors."""


def _detect_config_type(config: dict) -> str:
    """Detect config type from root keys.

    Returns one of: 'app', 'module', 'composite_device', 'adapter',
    'workload', 'bindings', or 'unknown'.
    """
    for key in ("app", "module", "composite_device", "adapter", "workload", "bindings"):
        if key in config:
            return key
    return "unknown"


def validate_config(config: dict) -> list[str]:
    """Validate a config dict. Returns list of error strings (empty = valid).

    Detects config type by root key and dispatches to the appropriate
    validator. Supports both module-format (module/role) and app-format
    (app.id/namespaces) configs.
    """
    config_type = _detect_config_type(config)

    if config_type == "app":
        return _validate_app_config(config)
    else:
        return _validate_module_config(config)


def _validate_app_config(config: dict) -> list[str]:
    """Validate an app-format config (CS-APP-V1 through CS-APP-V9)."""
    errors = []

    app_block = config.get("app", {})
    if not isinstance(app_block, dict):
        errors.append("'app' must be a dict with at least 'id' field")
        return errors

    # CS-APP-ID: app.id is required
    if "id" not in app_block:
        errors.append("Missing required field: app.id")

    # Validate namespaces
    ns_list = config.get("namespaces", [])
    for i, ns_def in enumerate(ns_list):
        if not isinstance(ns_def, dict):
            continue
        prefix = f"namespaces[{i}]"
        name = ns_def.get("name", "")
        if not name:
            errors.append(f"{prefix}: missing 'name'")
            continue

        is_app_ns = name.startswith("_app/")
        has_export = ns_def.get("export", False)
        has_import = ns_def.get("import", False)
        has_export_as = "export_as" in ns_def
        has_import_from = "import_from" in ns_def
        has_route_external = ns_def.get("route_external", False)
        has_route_as = "route_as" in ns_def

        # CS-APP-V1: exported namespace must have state_engine
        if has_export and "state_engine" not in ns_def:
            errors.append(
                f"{prefix} ('{name}'): exported namespace must have a state_engine defined"
            )

        # CS-APP-V2: imported namespace must have state_engine
        if has_import and "state_engine" not in ns_def:
            errors.append(
                f"{prefix} ('{name}'): imported namespace must have a state_engine defined"
            )

        # CS-APP-V3: export_as only valid when export is true
        if has_export_as and not has_export:
            errors.append(
                f"{prefix} ('{name}'): export_as is only valid when export is true"
            )

        # CS-APP-V4: import_from only valid when import is true
        if has_import_from and not has_import:
            errors.append(
                f"{prefix} ('{name}'): import_from is only valid when import is true"
            )

        # CS-APP-V5: bidirectional must not have different export_as and import_from
        if has_export and has_import and has_export_as and has_import_from:
            if ns_def.get("export_as") != ns_def.get("import_from"):
                errors.append(
                    f"{prefix} ('{name}'): bidirectional namespace cannot have "
                    f"different export_as and import_from values"
                )

        # CS-APP-V6: _app/ namespaces must not have export: true
        if is_app_ns and has_export:
            errors.append(
                f"{prefix} ('{name}'): _app/ namespaces cannot be exported directly. "
                f"Use route_external with route_as to bridge explicitly."
            )

        # CS-APP-V7: _app/ with route_external must have route_as
        if is_app_ns and has_route_external and not has_route_as:
            errors.append(
                f"{prefix} ('{name}'): route_as is required when routing an "
                f"_app/ namespace to external bus."
            )

        # CS-APP-V8: route_as must not start with _app/
        if has_route_as:
            route_as_val = ns_def.get("route_as", "")
            if route_as_val.startswith("_app/"):
                errors.append(
                    f"{prefix} ('{name}'): route_as must not start with _app/ "
                    f"— the external name must be outside the private boundary."
                )

        # CS-APP-V9: route_external/route_as only valid on _app/ namespaces
        if not is_app_ns and (has_route_external or has_route_as):
            errors.append(
                f"{prefix} ('{name}'): route_external is only valid on _app/ namespaces. "
                f"Use export instead."
            )

        # Validate state engine if present
        if "state_engine" in ns_def:
            se = ns_def["state_engine"]
            if isinstance(se, str) and se not in _VALID_BUILTIN_ENGINES:
                errors.append(f"{prefix} ('{name}'): unknown state engine '{se}'")
            elif isinstance(se, dict):
                if "type" not in se:
                    errors.append(f"{prefix} ('{name}'): state_engine dict missing 'type'")
                elif se["type"] == "enum" and "values" not in se:
                    errors.append(f"{prefix} ('{name}'): enum state engine missing 'values'")

        # BP-EXT-LOG: Validate mode field
        ns_mode = ns_def.get("mode", "state")
        if ns_mode not in _VALID_NAMESPACE_MODES:
            errors.append(f"{prefix} ('{name}'): invalid mode '{ns_mode}'. Valid: {_VALID_NAMESPACE_MODES}")

        # BP-EXT-LOG: Validate retention field
        if "retention" in ns_def:
            ret = ns_def["retention"]
            if not isinstance(ret, int) or ret < 0:
                errors.append(f"{prefix} ('{name}'): retention must be int >= 0")
            if ns_mode != "log":
                errors.append(f"{prefix} ('{name}'): retention is only valid when mode='log'")

        # BP-EXT-ACCESS: Validate access policy
        access = ns_def.get("access", {})
        if isinstance(access, dict):
            if "publish" in access:
                pub = access["publish"]
                if not isinstance(pub, list) or not all(isinstance(s, str) for s in pub):
                    errors.append(f"{prefix} ('{name}'): access.publish must be a list of strings")
            if "subscribe" in access:
                sub = access["subscribe"]
                if sub != "any" and (not isinstance(sub, list) or not all(isinstance(s, str) for s in sub)):
                    errors.append(f"{prefix} ('{name}'): access.subscribe must be 'any' or a list of strings")

    # Validate bindings
    for i, binding in enumerate(config.get("bindings", [])):
        prefix = f"bindings[{i}]"
        if "source" not in binding:
            errors.append(f"{prefix}: missing 'source'")
        if "target" not in binding:
            errors.append(f"{prefix}: missing 'target'")

    return errors


def _validate_module_config(config: dict) -> list[str]:
    """Validate a module-format config (original module/role validation)."""
    errors = []

    if "module" not in config:
        errors.append("Missing required field: module")
    if "role" not in config:
        errors.append("Missing required field: role")
    elif config["role"] not in _VALID_ROLES:
        errors.append(f"Unknown role: {config['role']}. Valid: {_VALID_ROLES}")

    # Validate buttons
    for i, btn in enumerate(config.get("buttons", [])):
        prefix = f"buttons[{i}]"
        if "namespace" not in btn:
            errors.append(f"{prefix}: missing 'namespace'")
        if "input" in btn and btn["input"] not in _VALID_INPUT_TYPES:
            errors.append(f"{prefix}: unknown input type '{btn['input']}'")
        if "state_engine" in btn:
            se = btn["state_engine"]
            if isinstance(se, str) and se not in _VALID_BUILTIN_ENGINES:
                errors.append(f"{prefix}: unknown state engine '{se}'")
            elif isinstance(se, dict):
                if "type" not in se:
                    errors.append(f"{prefix}: state_engine dict missing 'type'")
                elif se["type"] == "enum" and "values" not in se:
                    errors.append(f"{prefix}: enum state engine missing 'values'")
        # Validate LED behavior
        if "led" in btn:
            led = btn["led"]
            if isinstance(led, str) and led not in _VALID_LED_BEHAVIORS:
                # Check for parameterized forms like "blink_500ms"
                if not any(led.startswith(b) for b in ("blink_", "pulse_")):
                    errors.append(f"{prefix}: unknown LED behavior '{led}'")

    # Validate channels
    for i, ch in enumerate(config.get("channels", [])):
        prefix = f"channels[{i}]"
        if "namespace" not in ch:
            errors.append(f"{prefix}: missing 'namespace'")
        if "channel" not in ch:
            errors.append(f"{prefix}: missing 'channel'")

    # Validate bindings
    for i, binding in enumerate(config.get("bindings", [])):
        prefix = f"bindings[{i}]"
        if "source" not in binding:
            errors.append(f"{prefix}: missing 'source'")
        if "target" not in binding:
            errors.append(f"{prefix}: missing 'target'")

    return errors


# --- Apply Config to Bus ---

def apply_config(bus: ComposeIOBus, config: dict):
    """Apply a parsed config to a bus instance.

    Registers namespaces, creates bindings, sets up modules.
    Handles both module-format and app-format configs.
    """
    errors = validate_config(config)
    if errors:
        raise ConfigError(f"Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    config_type = _detect_config_type(config)

    if config_type == "app":
        _apply_app_config(bus, config)
    else:
        _apply_module_config(bus, config)


def _apply_app_config(bus: ComposeIOBus, config: dict):
    """Apply an app-format config to a bus instance.

    Registers namespaces from the top-level 'namespaces' list and
    bindings from the top-level 'bindings' list.
    """
    default_scope = Scope(config.get("scope", "auto"))

    # Register namespaces from the namespaces list
    for ns_def in config.get("namespaces", []):
        if not isinstance(ns_def, dict):
            continue
        name = ns_def.get("name", "")
        if not name:
            continue
        engine_config = ns_def.get("state_engine", "binary")
        scope = Scope(ns_def.get("scope", default_scope.value))
        label = ns_def.get("label")
        mode = ns_def.get("mode", "state")
        retention = ns_def.get("retention", 100)
        access = ns_def.get("access", {})
        access_publish = access.get("publish") if isinstance(access, dict) else None
        access_subscribe_raw = access.get("subscribe") if isinstance(access, dict) else None
        # "any" means no restriction (None)
        access_subscribe = None if access_subscribe_raw == "any" else access_subscribe_raw
        # [D4] THE FRESHNESS CONTRACT, DECLARED IN YAML. David's ratification
        # made this a design requirement rather than a config note: "This should
        # be configurable in YAML, ideally specifying the refresh interval, max
        # age, timeout, etc."
        #
        #   namespaces:
        #     - name: _bus/time
        #       freshness: {refresh_interval: 1.0}
        #     - name: throttle/left
        #       freshness: {quiet_by_construction: true}
        #     - name: slow/thing
        #       freshness: {refresh_interval: 5.0, timeout: 60.0}
        #
        # THE CLASS FORM NOW SHIPS TOO — see `freshness_classes:` below. It was
        # deferred here on the reading that David offered it as a FALLBACK ("or
        # if that's too difficult then a simple grouping ... defined as a
        # class") whose precondition never fired. That reading was too narrow:
        # D4b independently requires that a DERIVED value's age be the MAX of
        # its inputs' and that it be unknown if ANY input is unknown, and a
        # per-namespace field cannot express a conjunction across namespaces at
        # all. The class is not a weaker substitute for the per-namespace form,
        # it is the only form that can state a multi-message contract — so both
        # ship, and the class is the home of the derived-value rule.
        freshness = ns_def.get("freshness") or {}
        if not isinstance(freshness, dict):
            raise ValueError(
                f"namespace {name!r}: 'freshness' must be a mapping, got "
                f"{type(freshness).__name__}")
        bus.register_namespace(
            name, engine_config, scope=scope, label=label,
            mode=mode, retention=retention,
            access_publish=access_publish, access_subscribe=access_subscribe,
            refresh_interval=freshness.get("refresh_interval"),
            timeout=freshness.get("timeout"),
            quiet_by_construction=bool(freshness.get("quiet_by_construction")),
        )

    # [D4] FRESHNESS CLASSES — a NAMED group that must ALL be fresh together.
    #
    #   freshness_classes:
    #     - name: nav_solution
    #       members: [gps/fix, imu/heading, log/speed]
    #
    # Age is the MAX of the members' ages and the class is UNKNOWN if ANY member
    # is — D4b's derived-value rule, made a first-class nameable object. A
    # navigation solution is not fresh because its newest input is fresh; it is
    # as old as its OLDEST input, and it is not a solution at all if one input
    # is missing.
    for cls_def in config.get("freshness_classes", []):
        cls_name = cls_def.get("name")
        members = cls_def.get("members") or []
        if not cls_name:
            raise ValueError(
                "freshness class entry has no 'name'. An unnamed class cannot "
                "be looked up, so it could never be read — declaring one is "
                "always a mistake rather than a shorthand.")
        if not isinstance(members, list) or not members:
            raise ValueError(
                f"freshness class {cls_name!r}: 'members' must be a non-empty "
                f"list. An empty class reports FRESH forever — a green light "
                f"wired to nothing, which is worse than no light at all.")
        unknown_members = [m for m in members if bus.get_namespace(m) is None]
        if unknown_members:
            # ⛔ REFUSE, do not warn. A class silently missing a member is a
            # freshness contract that does not cover what its name says it
            # covers, and it fails OPEN — reporting FRESH while the namespace
            # nobody registered is the one that died.
            raise ValueError(
                f"freshness class {cls_name!r} names namespaces that are not "
                f"registered: {unknown_members}. Declare them (with their own "
                f"`freshness:` block) before grouping them.")
        bus.register_freshness_class(cls_name, members)

    # Register bindings (DP-CIO-10)
    for binding in config.get("bindings", []):
        bus.add_binding(
            source_ns=binding["source"],
            target_ns=binding["target"],
            transform_config=binding.get("transform"),
        )


def _apply_module_config(bus: ComposeIOBus, config: dict):
    """Apply a module-format config to a bus instance (original behavior)."""
    module_id = config["module"]
    default_scope = Scope(config.get("scope", "auto"))

    # Register button namespaces
    for btn in config.get("buttons", []):
        ns_name = btn["namespace"]
        engine_config = btn.get("state_engine", "binary")
        scope = Scope(btn.get("scope", default_scope.value))
        authority = Authority(btn.get("authority", "local"))
        bus.register_namespace(ns_name, engine_config, scope=scope, authority=authority)

    # Register channel namespaces
    for ch in config.get("channels", []):
        ns_name = ch["namespace"]
        engine_config = ch.get("state_engine", "binary")
        scope = Scope(ch.get("scope", default_scope.value))
        bus.register_namespace(ns_name, engine_config, scope=scope)

    # Register gauge namespaces
    for gauge in config.get("gauges", []):
        ns_name = gauge["namespace"]
        engine_config = gauge.get("state_engine", "continuous_0_255")
        scope = Scope(gauge.get("scope", default_scope.value))
        bus.register_namespace(ns_name, engine_config, scope=scope)

    # Register bindings (DP-CIO-10)
    for binding in config.get("bindings", []):
        bus.add_binding(
            source_ns=binding["source"],
            target_ns=binding["target"],
            transform_config=binding.get("transform"),
        )

    # Register adapter entities
    if "entities" in config:
        for entity in config["entities"]:
            ns_name = entity["namespace"]
            bus.register_namespace(ns_name, "binary", authority=Authority.EXTERNAL)


# --- Binary Compilation (for edge MCU flash) ---

def namespace_hash(name: str) -> int:
    """Compute 16-bit namespace hash (CRC-16/CCITT-FALSE)."""
    crc = 0xFFFF
    for byte in name.encode("ascii"):
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc &= 0xFFFF
    return crc


_INPUT_TYPE_IDS = {
    "momentary": 0, "toggle": 1, "hold_ramp": 2, "continuous": 3, "encoder": 4,
}

_ENGINE_TYPE_IDS = {
    "binary": 0, "tri_level": 1, "stop_light": 2, "continuous_0_255": 3, "enum": 4, "json": 5,
}

_LED_BEHAVIOR_IDS = {
    "off": 0, "solid": 1, "follow_state": 2, "while_pressed": 3,
    "blink": 4, "pulse": 5, "proportional": 6, "chase": 7,
}

# Binary config header: magic(2) + version(1) + role_id(1) + config_size(2) + crc32(4) = 10 bytes
_HEADER_MAGIC = b"\xC1\x00"  # ComposeIO v0
_HEADER_VERSION = 1

_ROLE_IDS = {
    "button-matrix": 0x01,
    "relay-controller": 0x02,
    "stepper-controller": 0x03,
    "pwm-bank": 0x04,
    "fingerprint-gate": 0x05,
    "composite-device": 0x06,
    "sensor-hub": 0x07,
}


def compile_binary(config: dict) -> bytes:
    """Compile a YAML config to compact binary for edge MCU flash.

    Binary layout:
    - Header: magic(2) + version(1) + role_id(1) + body_size(2) + crc32(4) = 10 bytes
    - Body: role-specific packed data

    For button-matrix:
    - button_count(1) + per-button: ns_hash(2) + input_type(1) + engine_type(1) + led(1) = 5 bytes each
    - 16 buttons = 1 + 80 = 81 bytes body → 91 bytes total. Fits CH32V003 128-byte config region.

    All multi-byte values are little-endian (RISC-V native).
    """
    errors = validate_config(config)
    if errors:
        raise ConfigError(f"Cannot compile invalid config:\n" + "\n".join(f"  - {e}" for e in errors))

    role = config["role"]
    role_id = _ROLE_IDS.get(role, 0xFF)

    # Compile body based on role
    if role == "button-matrix":
        body = _compile_button_matrix(config)
    elif role == "relay-controller":
        body = _compile_relay_controller(config)
    elif role == "stepper-controller":
        body = _compile_stepper_controller(config)
    elif role == "pwm-bank":
        body = _compile_pwm_bank(config)
    else:
        body = b""  # Placeholder for roles not yet implemented

    # Build header
    body_size = len(body)
    crc = _crc32(body)
    header = struct.pack("<2sBBH I", _HEADER_MAGIC, _HEADER_VERSION, role_id, body_size, crc)
    return header + body


def _compile_button_matrix(config: dict) -> bytes:
    """Compile button-matrix role config body."""
    buttons = config.get("buttons", [])
    data = struct.pack("<B", len(buttons))
    for btn in buttons:
        ns_h = namespace_hash(btn["namespace"])
        input_id = _INPUT_TYPE_IDS.get(btn.get("input", "toggle"), 1)
        engine = btn.get("state_engine", "binary")
        engine_id = _ENGINE_TYPE_IDS.get(engine if isinstance(engine, str) else engine.get("type", "binary"), 0)
        led = btn.get("led", "off")
        led_id = _LED_BEHAVIOR_IDS.get(led if isinstance(led, str) else "follow_state", 0)
        data += struct.pack("<HBBB", ns_h, input_id, engine_id, led_id)
    return data


def _compile_relay_controller(config: dict) -> bytes:
    """Compile relay-controller role config body."""
    channels = config.get("channels", [])
    data = struct.pack("<B", len(channels))
    for ch in channels:
        ns_h = namespace_hash(ch["namespace"])
        channel_num = ch.get("channel", 0)
        default = 1 if ch.get("default", "off") == "on" else 0
        data += struct.pack("<HBB", ns_h, channel_num, default)
    return data


def _compile_stepper_controller(config: dict) -> bytes:
    """Compile stepper-controller role config body."""
    gauges = config.get("gauges", [])
    data = struct.pack("<B", len(gauges))
    for g in gauges:
        ns_h = namespace_hash(g["namespace"])
        steps = g.get("steps_per_rev", 945)
        max_speed = g.get("max_speed", 300)
        data += struct.pack("<HHH", ns_h, steps, max_speed)
    return data


def _compile_pwm_bank(config: dict) -> bytes:
    """Compile pwm-bank role config body."""
    channels = config.get("channels", [])
    data = struct.pack("<B", len(channels))
    for ch in channels:
        ns_h = namespace_hash(ch["namespace"])
        freq = ch.get("frequency", 1000)
        default_duty = ch.get("default_duty", 0)
        data += struct.pack("<HHB", ns_h, freq, default_duty)
    return data


def _crc32(data: bytes) -> int:
    """CRC-32 checksum."""
    import binascii
    return binascii.crc32(data) & 0xFFFFFFFF
