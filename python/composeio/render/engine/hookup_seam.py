"""
The two-halves cross-check — does the PHYSICAL half agree with the LOGICAL one?

A maker `.compose.yaml` is single-source by DESIGN: the netlist (`hookup:`) and
the device/logical config (`namespaces:`, `audio_cover:`, `tm1637_panel:`, ...)
describe one machine.  Until now nothing ASSERTED that, and both boards that
needed an actuator seam said so in a COMMENT instead::

    audio_cover:
      uart:
        tx_pin: GPIO5      # C3 UART TX -> DY-SV5F RXD/IO1 (must match the hookup wire)

"must match" is not a check.  The two halves drift, and the drift surfaces as a
wrong joint on a bench — the most expensive place to find it.

This module reads BOTH halves of an already-parsed scene and reports where they
disagree.  It is GENERIC, not a registry of known blocks: any top-level mapping
that is not a known logical key is treated as a DEVICE HALF, and the rules key
off the SHAPE of what it carries (a `*_pin`, a `*_ns`, a part reference, a
subscribe list).  A third actuator block invented tomorrow is checked the day
it is written, with no edit here — the failure mode this whole exercise is
about is a seam nobody remembered to register.

Rules
-----
SEAM-PIN-NOT-WIRED       error    the device half names a pin no wire touches.
SEAM-NS-MISSING          error    a pin paired with a namespace in the device
                                  half, whose wire carries no namespace at all.
SEAM-NS-MISMATCH         error    that wire carries a DIFFERENT namespace.
SEAM-DIRECTION-INVERTED  error    the wire says `publishes:` for what the device
                                  half consumes — ⛔ `publishes:` for an
                                  actuator inverts the dataflow silently.
SEAM-PART-NS-MISMATCH    error    a block naming a hookup part and a subscribe
                                  list disagrees with the wires to that part.
SEAM-NS-UNDECLARED       warning  a namespace in neither half's `namespaces:`.
                                  Only a warning: a leaf legitimately subscribes
                                  state that ANOTHER app publishes.

``seam_errors()`` is the error-severity subset; the parser refuses a scene on
any of those and lets warnings through.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .hookup import Hookup, HookupWire

#: Top-level keys that are the LOGICAL/render half, not a device half.
_NOT_DEVICE_BLOCKS = {
    "app", "design", "namespaces", "widgets", "bindings", "hookup", "theme",
    "assets", "fonts", "styles", "components", "layout", "meta", "intents",
    "flows", "state", "imports", "params", "window", "scenes",
}

#: A pin-valued key in a device half (`clk_pin`, `dio_pin`, `tx_pin`, ...).
_PIN_KEY = re.compile(r"(^|_)pin$")

#: A namespace-valued key in a device half (`value_ns`, `namespace`, ...).
_NS_KEY = re.compile(r"(^|_)(ns|namespace)$")

#: A key whose LIST value is a set of subscribed namespaces.
_SUB_LIST_KEYS = ("subscribes", "subscribe")
_PUB_LIST_KEYS = ("publishes", "publish")

#: What a bare pin name looks like, so a `*_pin` holding something else (a
#: boolean, a duration) is skipped rather than reported as unwired.
_PIN_VALUE = re.compile(r"^[A-Za-z][A-Za-z0-9_/+.-]*$")


@dataclass(frozen=True)
class SeamFinding:
    code: str
    severity: str        # "error" | "warning"
    where: str           # dotted path into the .compose.yaml
    message: str

    def __str__(self) -> str:
        return f"{self.code} [{self.severity}] {self.where}: {self.message}"


# ---------------------------------------------------------------------------
# walking a device half
# ---------------------------------------------------------------------------

def _device_blocks(config: Dict[str, Any]) -> List[Tuple[str, Any]]:
    return [(k, v) for k, v in config.items()
            if k not in _NOT_DEVICE_BLOCKS and isinstance(v, (dict, list))]


def _mappings(node: Any, path: str) -> Iterable[Tuple[str, Dict[str, Any]]]:
    """Every mapping inside *node*, with its dotted path."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            sub = f"{path}.{k}" if path else str(k)
            yield from _mappings(v, sub)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _mappings(v, f"{path}[{i}]")


def _declared_namespaces(config: Dict[str, Any]) -> Tuple[str, ...]:
    out: List[str] = []
    for entry in (config.get("namespaces") or []):
        if isinstance(entry, dict) and entry.get("name"):
            out.append(str(entry["name"]))
        elif isinstance(entry, str):
            out.append(entry)
    return tuple(out)


def _wires_touching_pin(hookup: Hookup, pin: str) -> Tuple[HookupWire, ...]:
    """Every wire with an endpoint whose PIN NAME is *pin* on an MCU part.

    A device half names bare pins (`GPIO3`) because it is the MCU's own config;
    the netlist names `<part>.<pin>`.  Matching on the MCU side is what makes
    the two comparable at all.
    """
    hits: List[HookupWire] = []
    for w in hookup.wires:
        for end in (w.frm, w.to):
            hp = hookup.part(end.part)
            is_mcu = bool(hp and hp.definition and hp.definition.adc_pins)
            if end.pin == pin and is_mcu:
                hits.append(w)
                break
    return tuple(hits)


def _wires_touching_part(hookup: Hookup, part_id: str) -> Tuple[HookupWire, ...]:
    return tuple(w for w in hookup.wires
                 if w.frm.part == part_id or w.to.part == part_id)


# ---------------------------------------------------------------------------
# the check
# ---------------------------------------------------------------------------

def crosscheck_seam(config: Dict[str, Any],
                    hookup: Optional[Hookup]) -> Tuple[SeamFinding, ...]:
    """Report every disagreement between the two halves of one scene."""
    if hookup is None or not isinstance(config, dict):
        return ()
    out: List[SeamFinding] = []
    declared = set(_declared_namespaces(config))
    part_ids = {hp.id for hp in hookup.parts}

    for block_key, block in _device_blocks(config):
        for path, mapping in _mappings(block, block_key):
            pin_keys = [k for k in mapping if _PIN_KEY.search(str(k))
                        and isinstance(mapping[k], str)
                        and _PIN_VALUE.match(mapping[k])]
            ns_keys = [k for k in mapping if _NS_KEY.search(str(k))
                       and isinstance(mapping[k], str)]

            for pk in pin_keys:
                pin = mapping[pk]
                wires = _wires_touching_pin(hookup, pin)
                if not wires:
                    out.append(SeamFinding(
                        "SEAM-PIN-NOT-WIRED", "error", f"{path}.{pk}",
                        f"the device half drives pin '{pin}' but NO hookup "
                        f"wire touches that pin on any MCU part — one half of "
                        f"the artifact is describing a machine the other half "
                        f"is not building"))
                    continue
                # pin + namespace in the SAME mapping = a declared seam.  The
                # wire on that pin must carry that namespace, that direction.
                for nk in ns_keys:
                    ns = mapping[nk]
                    carried = {s for w in wires for s in w.slots}
                    if not carried:
                        out.append(SeamFinding(
                            "SEAM-NS-MISSING", "error", f"{path}.{nk}",
                            f"the device half binds pin '{pin}' to namespace "
                            f"'{ns}', but the wire on that pin carries no "
                            f"`subscribes:`/`publishes:` — the netlist cannot "
                            f"be single-source for a seam it does not name"))
                    elif ns not in carried:
                        out.append(SeamFinding(
                            "SEAM-NS-MISMATCH", "error", f"{path}.{nk}",
                            f"the device half binds pin '{pin}' to '{ns}'; the "
                            f"wire on that pin carries "
                            f"{sorted(carried)} — they cannot both be true"))
                    else:
                        for w in wires:
                            if ns in w.slots and w.direction == "publishes":
                                out.append(SeamFinding(
                                    "SEAM-DIRECTION-INVERTED", "error",
                                    f"{path}.{nk}",
                                    f"pin '{pin}' SINKS '{ns}' in the device "
                                    f"half (an actuator) but the wire declares "
                                    f"`publishes:` — that inverts the dataflow "
                                    f"in the one artifact both halves are read "
                                    f"from.  Use `subscribes:`"))

            # a block that names a hookup part AND a subscribe/publish list
            # must agree with the wires that reach that part.
            out.extend(_check_part_block(hookup, path, mapping, part_ids))

    for ns in _seam_namespaces(hookup):
        if ns not in declared:
            out.append(SeamFinding(
                "SEAM-NS-UNDECLARED", "warning", "hookup.wires",
                f"'{ns}' rides the seam but is not in this scene's "
                f"`namespaces:` — legitimate when another app publishes it, "
                f"a typo otherwise"))
    return tuple(out)


def _check_part_block(hookup: Hookup, path: str, mapping: Dict[str, Any],
                      part_ids: set) -> List[SeamFinding]:
    named = [v for v in mapping.values()
             if isinstance(v, str) and v in part_ids]
    if not named:
        return []
    out: List[SeamFinding] = []
    for keys, direction in ((_SUB_LIST_KEYS, "subscribes"),
                            (_PUB_LIST_KEYS, "publishes")):
        for key in keys:
            listed = mapping.get(key)
            if not isinstance(listed, list) or not listed:
                continue
            want = {str(x) for x in listed if isinstance(x, str)}
            for part_id in named:
                wires = _wires_touching_part(hookup, part_id)
                got = {s for w in wires if w.direction == direction
                       for s in w.slots}
                if got != want:
                    out.append(SeamFinding(
                        "SEAM-PART-NS-MISMATCH", "error", f"{path}.{key}",
                        f"the device half says part '{part_id}' {direction} "
                        f"{sorted(want)}; the wires to '{part_id}' carry "
                        f"{sorted(got) or 'nothing'} — the 'must match the "
                        f"hookup wire' comment made machine-checkable"))
    return out


def _seam_namespaces(hookup: Hookup) -> Tuple[str, ...]:
    seen: List[str] = []
    for w in hookup.seam_wires:
        for s in w.slots:
            if s not in seen:
                seen.append(s)
    return tuple(seen)


def seam_errors(config: Dict[str, Any],
                hookup: Optional[Hookup]) -> Tuple[SeamFinding, ...]:
    """Only the findings a scene is REFUSED for."""
    return tuple(f for f in crosscheck_seam(config, hookup)
                 if f.severity == "error")


__all__ = ["SeamFinding", "crosscheck_seam", "seam_errors"]
