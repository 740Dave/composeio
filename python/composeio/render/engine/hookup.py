"""
The `hookup:` block — physical half of a maker-project `.compose.yaml`.

Maker-project-flow design lock §3.  A scene's `.compose.yaml` already carries
the LOGICAL half (namespaces/bindings); `hookup:` adds the PHYSICAL half so the
one artifact is single-source for the widget, the hookup guide (S3), and the
MCU pull/read config (S4).  This module PARSES + VALIDATES that block into a
:class:`Hookup` the parser hangs on ``Composition.hookup``.

Schema (§3)::

    hookup:
      board: perfboard            # perfboard | breadboard
      parts:
        - {id: mcu,  part: esp32-c3, at: [row, col]}   # part: -> catalog (§4)
        - {id: potL, part: pot-10k,  at: [row, col]}
      wires:
        - {from: potL.wiper, to: mcu.GPIO0, publishes: throttle/left}
        - {from: mcu.GPIO3,  to: tm1.DIO,   subscribes: ship/fuel}
        - {from: potL.vcc,   to: mcu.3V3}
      must_not:                   # the NEGATIVE SPACE — see below
        - {id: no-boot-straps, why: "...", unwired: [mcu.GPIO2, mcu.GPIO8]}
      facts:                      # PROVENANCE — see below
        - {id: pullups, what: "...", value: "10 kOhm", provenance: assumed,
           source: "...", measure_before_build: true, if_different: "...",
           measured: EMPTY}

Validation is strict and additive:
  * `board` is one of {breadboard, perfboard}.
  * each part `id` is unique; each `part:` resolves in the parts catalog;
    `at:` (optional) is a [row, col] pair.
  * every wire endpoint is `<part>.<pin>` where `<part>` is a declared part id
    and `<pin>` is a real pin in THAT part's catalog pin map (a bad pin is an
    error, not a silent pass — the netlist must be sound for S3/S4).
  * `publishes:` (optional) is the slot namespace a SENSOR pin sources.
  * `subscribes:` (optional) is the slot namespace(s) an ACTUATOR pin sinks —
    str or list.  A wire carries at most ONE of the two: they are opposite
    dataflow directions and encoding the wrong one into the single-source
    artifact is how a wrong diagram gets soldered.
  * `note:` (optional) is free text for a wire that carries no namespace at all
    (a shared clock, a rail) — so a label never has to be smuggled through a
    seam field.

`must_not:` — the negative space
--------------------------------
A netlist can only express connections that EXIST.  Half of what a hookup
guide actually knows is about connections that must NOT exist, and a diagram
cannot draw an absence.  `must_not:` puts those in the SAME artifact, and —
where possible — makes them CHECKABLE rather than merely written down.

Every entry carries `id:`, `why:` and EXACTLY ONE clause.  Three clauses are
ENFORCED at parse time (a violating netlist is REFUSED, not annotated); one is
STATED, and is labelled as such everywhere it is rendered so it can never be
mistaken for a checked one:

  ``unwired: [<part>.<pin>, ...]``   ENFORCED.  No wire may touch these PADS.
      fnmatch wildcards allowed (``*.DIO-B`` = the whole B header).  Literal
      pad identity, NOT net membership — "leave DIO-B unconnected" must stay
      true while DIO itself is deliberately wired.
  ``distinct_nets: [<part>.<pin>, ...]``  ENFORCED.  No two of these may land
      on the same electrical net.  Nets are computed by union-find over the
      wire list SEEDED WITH each part's catalog ``same_net:`` groups, so a
      pass-through header (``DIO-B`` IS ``DIO``) cannot smuggle a common
      connection past the check.
  ``absent_parts: [<catalog-id>, ...]``   ENFORCED.  This part type must not
      be placed at all ("do not add pull-ups — the module carries them").
  ``stated: <text>``                      STATED.  No machine can see a
      mechanical butt-joint or a builder's habit.  Carried, rendered, and
      never claimed as checked.

A clause that resolves to NOTHING is an ERROR.  A typo'd constraint reads as
protection and provides none, which is worse than no constraint at all.

`facts:` — provenance, per [DX-SPEC-CARRIES-ITS-EVIDENCE]
---------------------------------------------------------
The motivating case is not hypothetical: the TM1637 level-shifting analysis
INVERTS if the modules ship 4.7 kOhm pull-ups instead of the assumed 10 kOhm,
and vendors vary this silently.  A `facts:` row records WHICH KIND OF TRUE a
number is — ``datasheet`` | ``computed`` | ``measured`` | ``assumed`` — and
carries an evidence slot that is EXPLICITLY ``EMPTY`` until a bench fills it
(an absent key is indistinguishable from one nobody thought to write).

  * ``datasheet``/``computed`` require a `source:` (a page, or the arithmetic).
  * ``assumed`` FORCES ``measure_before_build: true`` and requires
    ``if_different:`` — an assumption nobody intends to check is not an
    assumption.
  * ``measured`` requires ``measured:`` to be something other than ``EMPTY``.

A scene WITHOUT a `hookup:` block yields ``comp.hookup is None`` and is byte-
for-byte unchanged — this whole module is purely additive.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .parts import PartDef, PartNotFound, resolve_part

BOARDS = ("breadboard", "perfboard")

# ===========================================================================
# `label_pins:` — WHICH pads get their catalog name printed.
#
# ⛔ THE DEFAULT IS `wired`, AND THE DEFAULT IS THE DECISION (David 2026-08-15:
# "a pin nobody wires should not compete for label space").  Measured on the
# R503 + S3 guide: `esp32-s3-supermini` declares 40 pads, the scene wires 11,
# and the 29 silent ones — including the underside-only pads the ratified
# design EXCLUDES — were drawn, named, and stacked six-deep on one point,
# which is most of what made both edge columns unreadable.
#
# WHY DEFAULT-ON IS SAFE, and it turns on one distinction: the suppression
# removes the OVERLAY, never the PAD.  The Fritzing art is untouched and the
# affordance still rings every terminal, so a builder counting positions along
# a header still sees all 40 holes; only the redundant name goes.  A hookup
# guide is an instrument for "put this wire on that pad" — a pad no wire
# touches has nothing to say and is spending the scarcest resource on the
# drawing (space beside a header) to say it.
#
# `all` is the opt-in for the case this cannot serve: a guide meant to double
# as a PINOUT reference rather than a build instruction.  Say so in the scene.
# ===========================================================================
LABEL_PINS = ("wired", "all")
LABEL_PINS_DEFAULT = "wired"

#: The clause keys of a `must_not:` entry, and whether each is machine-checked.
MUST_NOT_CLAUSES: Dict[str, bool] = {
    "unwired": True,
    "distinct_nets": True,
    "absent_parts": True,
    "stated": False,
}

#: How a `facts:` row knows what it knows.
PROVENANCE = ("datasheet", "computed", "measured", "assumed")

#: The one legal placeholder for an unfilled evidence slot.  Explicit, because
#: an ABSENT key is indistinguishable from one nobody thought to write.
EMPTY = "EMPTY"


class HookupError(ValueError):
    """The `hookup:` block is malformed or references a bad part/pin."""


@dataclass(frozen=True)
class HookupPart:
    """A placed instance of a catalog part on the board."""
    id: str                     # scene-local instance id (wires reference this)
    part: str                   # catalog part id (`part:` reference)
    at: Optional[Tuple[int, int]] = None   # (row, col) placement, if given
    definition: Optional[PartDef] = None   # resolved catalog def (§4)


@dataclass(frozen=True)
class PinRef:
    """A resolved `<part>.<pin>` wire endpoint."""
    part: str                   # scene-local part instance id
    pin: str                    # canonical pin name (validated against catalog)

    def __str__(self) -> str:   # round-trips to the authored `<part>.<pin>`
        return f"{self.part}.{self.pin}"


@dataclass(frozen=True)
class HookupWire:
    """One netlist edge and, optionally, ONE HALF of the pin<->slot seam.

    `publishes` = this pin SOURCES the value (a sensor).
    `subscribes` = this pin SINKS it (an actuator).  Never both: they are
    opposite directions, and `publishes:` used for an actuator's label
    silently inverts the dataflow the whole artifact is single-source for.
    """
    frm: PinRef
    to: PinRef
    publishes: Optional[str] = None
    subscribes: Tuple[str, ...] = ()
    note: Optional[str] = None
    #: Leaf-owned signal conditioning declared on THIS wire, verbatim.
    #:
    #: ⛔ THIS FIELD EXISTS BECAUSE ITS ABSENCE WAS INVISIBLE.  The firmware
    #: (`controller_config.cpp`) reads `invert` / `deadband` / `ema` / `in_min`
    #: / `in_max` straight off the wire mapping, but this parser — the Python
    #: model of the SAME file — used to keep only from/to/publishes/subscribes/
    #: note.  Nothing failed: the keys simply evaporated on the Python side, so
    #: every Python consumer (the MCU-config emitter, the hookup guide, any
    #: future validator) reasoned about a wire that had no conditioning at all,
    #: and agreed with itself perfectly while disagreeing with the device.
    #:
    #: Carrying the dict VERBATIM rather than as named fields is deliberate:
    #: a named-field copy is a second vocabulary that must be extended in
    #: lockstep with the firmware's, and the whole defect being fixed here is
    #: that such a copy silently lags.  Unknown keys ride through untouched
    #: and reach whatever consumer understands them.
    conditioning: Dict[str, Any] = field(default_factory=dict)

    @property
    def direction(self) -> Optional[str]:
        """'publishes' | 'subscribes' | None — the seam direction, if any."""
        if self.publishes:
            return "publishes"
        if self.subscribes:
            return "subscribes"
        return None

    @property
    def slots(self) -> Tuple[str, ...]:
        """Every slot namespace this wire carries, in either direction."""
        if self.publishes:
            return (self.publishes,)
        return self.subscribes


@dataclass(frozen=True)
class MustNot:
    """One negative-space constraint — see the module docstring.

    ``enforced`` is DERIVED from the clause, never authored: an author cannot
    claim a statement is checked, and cannot opt an enforceable clause out.
    """
    id: str
    why: str
    kind: str                       # unwired | distinct_nets | absent_parts | stated
    #: the literal clause value as authored (patterns, ids, or the text)
    clause: Tuple[str, ...]
    #: for pin clauses: the concrete `<part>.<pin>` the patterns bound to
    resolved: Tuple[str, ...] = ()

    @property
    def enforced(self) -> bool:
        return MUST_NOT_CLAUSES[self.kind]

    @property
    def statement(self) -> str:
        """The human sentence, whichever clause produced it."""
        if self.kind == "stated":
            return self.clause[0]
        if self.kind == "unwired":
            return ("leave UNCONNECTED: " + ", ".join(self.resolved))
        if self.kind == "distinct_nets":
            return ("never on one net: " + ", ".join(self.resolved))
        return "never place the part: " + ", ".join(self.clause)


@dataclass(frozen=True)
class HookupFact:
    """One number, and WHAT KIND OF TRUE it is (see the module docstring)."""
    id: str
    what: str
    value: str
    provenance: str                 # datasheet | computed | measured | assumed
    source: Optional[str] = None
    measure_before_build: bool = False
    if_different: Optional[str] = None
    measured: str = EMPTY

    @property
    def is_empty(self) -> bool:
        """True while no bench has filled this slot — a QUERYABLE property."""
        return self.measured.strip() == EMPTY


@dataclass(frozen=True)
class Hookup:
    """Parsed + validated `hookup:` block — S3 reads it whole, S4 the slots."""
    board: str
    parts: Tuple[HookupPart, ...]
    wires: Tuple[HookupWire, ...]
    must_not: Tuple[MustNot, ...] = ()
    facts: Tuple[HookupFact, ...] = ()
    #: "wired" (default) | "all" — see LABEL_PINS above.
    label_pins: str = LABEL_PINS_DEFAULT

    # ---- convenience accessors (S3/S4 contracts) --------------------------

    def part(self, part_id: str) -> Optional[HookupPart]:
        for p in self.parts:
            if p.id == part_id:
                return p
        return None

    @property
    def enforced_must_not(self) -> Tuple[MustNot, ...]:
        """The constraints a violating netlist is REFUSED for."""
        return tuple(m for m in self.must_not if m.enforced)

    @property
    def stated_must_not(self) -> Tuple[MustNot, ...]:
        """The constraints only a human can check — labelled, never claimed."""
        return tuple(m for m in self.must_not if not m.enforced)

    @property
    def measure_before_build(self) -> Tuple[HookupFact, ...]:
        """The facts a meter must settle BEFORE anything is soldered."""
        return tuple(f for f in self.facts if f.measure_before_build)

    @property
    def empty_evidence(self) -> Tuple[HookupFact, ...]:
        """Slots that have never touched a bench (the EMPTY sentinel)."""
        return tuple(f for f in self.facts if f.is_empty)

    @property
    def publishing_wires(self) -> Tuple[HookupWire, ...]:
        """The wires that carry a `publishes:` slot (the seam wires)."""
        return tuple(w for w in self.wires if w.publishes)

    @property
    def subscribing_wires(self) -> Tuple[HookupWire, ...]:
        """The wires that carry a `subscribes:` slot (the ACTUATOR seam)."""
        return tuple(w for w in self.wires if w.subscribes)

    @property
    def seam_wires(self) -> Tuple[HookupWire, ...]:
        """Every wire carrying a namespace, in either direction."""
        return tuple(w for w in self.wires if w.direction)

    def pin_seam_map(self) -> Dict[str, Tuple[str, Tuple[str, ...]]]:
        """``{'<part>.<pin>': (direction, (slot, ...))}`` for the MCU side.

        The directional successor to :meth:`pin_slot_map`: an actuator pin maps
        to ``('subscribes', (...))`` and a sensor pin to ``('publishes', ...)``,
        so a consumer can never infer the wrong dataflow from the seam.
        """
        out: Dict[str, Tuple[str, Tuple[str, ...]]] = {}
        for w in self.seam_wires:
            for end in self._mcu_ends(w):
                out[str(end)] = (w.direction or "", w.slots)
        return out

    def _mcu_ends(self, w: HookupWire) -> Tuple[PinRef, ...]:
        """The endpoint(s) that are the HOST side of a seam wire.

        Prefer an ADC pin (the analog sensor case S4 was built for); else the
        endpoint whose part declares any ``adc_pins`` at all (i.e. is an MCU);
        else both, so a consumer still sees the binding.
        """
        for end in (w.frm, w.to):
            hp = self.part(end.part)
            if hp and hp.definition and hp.definition.is_adc_pin(end.pin):
                return (end,)
        for end in (w.frm, w.to):
            hp = self.part(end.part)
            if hp and hp.definition and hp.definition.adc_pins:
                return (end,)
        return (w.frm, w.to)

    def pin_slot_map(self) -> Dict[str, str]:
        """{'<part>.<pin>': slot} for the MCU side of each publishing wire.

        S4's contract: map an MCU (ADC-capable) pin to the slot it publishes.
        For a publishing wire, the MCU endpoint is the one whose resolved
        catalog def declares that pin in ``adc_pins``; if neither side is an
        ADC pin (non-analog seam), both endpoints map to the slot so S4 still
        sees the binding.
        """
        out: Dict[str, str] = {}
        for w in self.publishing_wires:
            mcu_side = None
            for end in (w.frm, w.to):
                hp = self.part(end.part)
                if hp and hp.definition and hp.definition.is_adc_pin(end.pin):
                    mcu_side = end
                    break
            if mcu_side is not None:
                out[str(mcu_side)] = w.publishes  # type: ignore[assignment]
            else:
                out[str(w.frm)] = w.publishes     # type: ignore[assignment]
                out[str(w.to)] = w.publishes      # type: ignore[assignment]
        return out

    @property
    def slots(self) -> Tuple[str, ...]:
        """Every distinct slot namespace PUBLISHED by this hookup.

        Unchanged (publish-only) so S4's sensor emitter keeps its contract;
        the directional view is :attr:`all_slots` / :meth:`pin_seam_map`.
        """
        seen: List[str] = []
        for w in self.publishing_wires:
            if w.publishes and w.publishes not in seen:
                seen.append(w.publishes)
        return tuple(seen)

    @property
    def all_slots(self) -> Tuple[str, ...]:
        """Every distinct slot namespace this hookup touches, either way."""
        seen: List[str] = []
        for w in self.seam_wires:
            for s in w.slots:
                if s not in seen:
                    seen.append(s)
        return tuple(seen)

    # ---- electrical nets (the `distinct_nets` substrate) ------------------

    def nets(self) -> Dict[str, str]:
        """``{'<part>.<pin>': <net-representative>}`` over every declared pad.

        Union-find over the wire list, SEEDED with each placed part's catalog
        ``same_net:`` groups.  Without that seed a pass-through header reads as
        two independent nodes and a butt-joint on `DIO-B` sails past a
        `distinct_nets` constraint on `DIO` — the exact defect the constraint
        exists to catch.
        """
        parent: Dict[str, str] = {}

        def find(x: str) -> str:
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for hp in self.parts:
            pins = hp.definition.pins if hp.definition else ()
            for pin in pins:
                find(f"{hp.id}.{pin}")
            for group in _same_net_groups(hp):
                anchor = f"{hp.id}.{group[0]}"
                for pin in group[1:]:
                    union(anchor, f"{hp.id}.{pin}")
        for w in self.wires:
            union(str(w.frm), str(w.to))
        return {k: find(k) for k in list(parent)}

    def same_net_reason(self, a: str, b: str) -> str:
        """Why *a* and *b* landed on one net — a wire path, or a part's own
        internal tie.  The refusal has to name the mechanism or the builder
        stares at a netlist in which the two pins are demonstrably separate."""
        for hp in self.parts:
            for group in _same_net_groups(hp):
                names = {f"{hp.id}.{p}" for p in group}
                if a in names and b in names:
                    return (f"same-net inside part '{hp.id}' "
                            f"({hp.part} declares {' = '.join(group)})")
        for hp in self.parts:
            for group in _same_net_groups(hp):
                names = {f"{hp.id}.{p}" for p in group}
                if (a in names) != (b in names):
                    return (f"same-net through part '{hp.id}''s internal tie "
                            f"({hp.part}: {' = '.join(group)}) plus the wires")
        return "joined by the wire list"


def _same_net_groups(hp: HookupPart) -> Tuple[Tuple[str, ...], ...]:
    """A part's catalog-declared internal net ties, validated against its pins.

    Catalog shape (in the part's yaml)::

        same_net:
          - [CLK, CLK-B]      # a pass-through header: ONE net, two pads
    """
    if hp.definition is None:
        return ()
    raw = hp.definition.extra.get("same_net")
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise HookupError(
            f"part '{hp.part}': catalog `same_net` must be a list of groups")
    groups: List[Tuple[str, ...]] = []
    for group in raw:
        if not isinstance(group, (list, tuple)) or len(group) < 2:
            raise HookupError(
                f"part '{hp.part}': each `same_net` group needs >= 2 pins, "
                f"got {group!r}")
        names = tuple(str(p) for p in group)
        for pin in names:
            if pin not in hp.definition.pins:
                raise HookupError(
                    f"part '{hp.part}': `same_net` names '{pin}', which is not "
                    f"a pin of this part")
        groups.append(names)
    return tuple(groups)


def _parse_at(part_id: str, raw: Any) -> Optional[Tuple[int, int]]:
    if raw is None:
        return None
    if (not isinstance(raw, (list, tuple)) or len(raw) != 2
            or not all(isinstance(v, (int, float)) for v in raw)):
        raise HookupError(
            f"part '{part_id}': `at` must be a [row, col] pair, got {raw!r}")
    return (int(raw[0]), int(raw[1]))


def _parse_endpoint(raw: Any, parts: Dict[str, HookupPart],
                    where: str) -> PinRef:
    if not isinstance(raw, str) or "." not in raw:
        raise HookupError(
            f"wire {where}: endpoint must be '<part>.<pin>', got {raw!r}")
    part_id, pin = raw.split(".", 1)
    part_id, pin = part_id.strip(), pin.strip()
    hp = parts.get(part_id)
    if hp is None:
        raise HookupError(
            f"wire {where}: endpoint '{raw}' names part '{part_id}', which is "
            f"not a declared hookup part (declared: "
            f"{', '.join(sorted(parts)) or '<none>'})")
    if hp.definition is not None and pin not in hp.definition.pins:
        raise HookupError(
            f"wire {where}: pin '{pin}' is not a pin of part '{part_id}' "
            f"(part '{hp.part}' pins: {', '.join(hp.definition.pins)})")
    return PinRef(part=part_id, pin=pin)


# ---------------------------------------------------------------------------
# `must_not:` — parse, then ENFORCE against the netlist that was just read
# ---------------------------------------------------------------------------

def _str_list(raw: Any, where: str) -> Tuple[str, ...]:
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)) and raw and all(
            isinstance(v, str) for v in raw):
        return tuple(str(v) for v in raw)
    raise HookupError(f"{where} must be a string or a non-empty list of "
                      f"strings, got {raw!r}")


#: A ComposeBus namespace — slash-separated segments, no whitespace.  Narrow on
#: purpose: the seam field is where a LABEL gets smuggled in when the schema has
#: no home for one ("-- shared CLK (host->panel only)" was found riding in a
#: real `subscribes:`), and a smuggled label is a namespace that never resolves.
_NAMESPACE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+)*$")


def _check_namespace(ns: str, where: str) -> None:
    if not _NAMESPACE.match(ns):
        raise HookupError(
            f"{where}: {ns!r} is not a bus namespace (expected e.g. "
            f"'ship/fuel').  If this is a human label for a wire that carries "
            f"no namespace, it belongs in `note:` — a seam field is not a "
            f"caption slot")


#: The wire keys this parser gives STRUCTURAL meaning to.  Everything else on a
#: wire mapping is leaf-owned signal conditioning and is carried verbatim.
_WIRE_STRUCTURAL_KEYS = frozenset({"from", "to", "publishes", "subscribes", "note"})

#: Type checks for the conditioning knobs the firmware currently reads
#: (`firmware/reactive_core/src/controller_config.cpp`).  Checked, not
#: enumerated-and-filtered: an unrecognised key still rides through.
_CONDITIONING_TYPES: Dict[str, tuple] = {
    "invert": (bool,),
    "deadband": (int,),
    "ema": (int, float),
    "in_min": (int,),
    "in_max": (int,),
}


def _parse_conditioning(entry: Dict[str, Any], i: int) -> Dict[str, Any]:
    """Every non-structural key on a wire, carried VERBATIM.

    ⛔ CARRY-BY-DEFAULT IS THE POINT.  The failure this replaces was a
    named-field allow-list that lagged the firmware: the device grew
    `invert`/`deadband`/`ema` and later `in_min`/`in_max`, and the Python model
    of the same file kept none of them — silently, because dropping a key that
    no Python code reads raises nothing and breaks no test.  An allow-list here
    would reproduce the defect the next time the firmware gains a knob, so the
    default is to CARRY and only the KNOWN keys are type-checked.

    A bad type is a hard error: `deadband: "2"` would reach the device as a
    string, and yaml_min's `get_int` would silently yield its default — the
    author's declared denoiser replaced by a different one, with no diagnostic
    anywhere.  That is worth an exception.
    """
    cond: Dict[str, Any] = {}
    for key, value in entry.items():
        if key in _WIRE_STRUCTURAL_KEYS:
            continue
        expected = _CONDITIONING_TYPES.get(key)
        if expected is not None and not isinstance(value, expected):
            # bool is an int subclass; `invert: 1` is a real authoring slip.
            raise HookupError(
                f"hookup.wires[{i}].{key} must be "
                f"{' or '.join(t.__name__ for t in expected)}, got {value!r}")
        if key in ("in_min", "in_max", "deadband") and isinstance(value, bool):
            raise HookupError(
                f"hookup.wires[{i}].{key} must be an int, got {value!r}")
        cond[str(key)] = value
    return cond


def _all_pads(parts: Sequence[HookupPart]) -> Tuple[str, ...]:
    pads: List[str] = []
    for hp in parts:
        for pin in (hp.definition.pins if hp.definition else ()):
            pads.append(f"{hp.id}.{pin}")
    return tuple(pads)


def _resolve_pin_patterns(patterns: Iterable[str], pads: Sequence[str],
                          where: str) -> Tuple[str, ...]:
    """Expand fnmatch patterns over the declared pads.

    A pattern that binds to NOTHING is an ERROR: a typo'd constraint reads as
    protection and provides none.
    """
    out: List[str] = []
    for pat in patterns:
        hits = [p for p in pads if fnmatch.fnmatchcase(p, pat)]
        if not hits:
            raise HookupError(
                f"{where}: '{pat}' matches no pin of any declared part — a "
                f"constraint that binds to nothing reads as protection and "
                f"provides none (declared pads: {len(pads)})")
        for h in hits:
            if h not in out:
                out.append(h)
    return tuple(out)


def _parse_must_not(raw_block: Any, parts: Sequence[HookupPart]) -> Tuple[MustNot, ...]:
    if raw_block in (None, []):
        return ()
    if not isinstance(raw_block, list):
        raise HookupError("hookup.must_not must be a list")
    pads = _all_pads(parts)
    seen: Dict[str, int] = {}
    out: List[MustNot] = []
    for i, entry in enumerate(raw_block):
        where = f"hookup.must_not[{i}]"
        if not isinstance(entry, dict):
            raise HookupError(f"{where} must be a mapping: {entry!r}")
        mid = entry.get("id")
        if not mid or not isinstance(mid, str):
            raise HookupError(f"{where} needs a string `id`: {entry!r}")
        if mid in seen:
            raise HookupError(
                f"{where}: duplicate must_not id '{mid}' (also at index "
                f"{seen[mid]}) — ids are how a refusal and a rendered warning "
                f"name the same constraint")
        seen[mid] = i
        why = entry.get("why")
        if not why or not isinstance(why, str):
            raise HookupError(
                f"{where} ('{mid}') needs a `why` — a constraint whose reason "
                f"is not carried gets deleted by the next person who cannot "
                f"see the harm")
        present = [k for k in MUST_NOT_CLAUSES if k in entry]
        if len(present) != 1:
            raise HookupError(
                f"{where} ('{mid}') needs exactly one clause of "
                f"{sorted(MUST_NOT_CLAUSES)}, got {present or 'none'}")
        kind = present[0]
        clause = _str_list(entry[kind], f"{where}.{kind}")
        resolved: Tuple[str, ...] = ()
        if kind == "absent_parts":
            # Anti-vacuity, same rule as a pin pattern: a part id that no
            # catalog defines can never be placed, so the constraint can never
            # fire while READING as protection.
            for catalog_id in clause:
                try:
                    resolve_part(catalog_id)
                except PartNotFound:
                    raise HookupError(
                        f"{where} ('{mid}'): `absent_parts` names "
                        f"'{catalog_id}', which no catalog defines — nothing "
                        f"can ever place it, so this constraint cannot fire.  "
                        f"Use `stated:` until the part exists") from None
        if kind in ("unwired", "distinct_nets"):
            resolved = _resolve_pin_patterns(clause, pads, f"{where}.{kind}")
            if kind == "distinct_nets" and len(resolved) < 2:
                raise HookupError(
                    f"{where} ('{mid}'): `distinct_nets` needs >= 2 pins to "
                    f"keep apart, resolved to {resolved}")
        out.append(MustNot(id=mid, why=why, kind=kind,
                           clause=clause, resolved=resolved))
    return tuple(out)


def _enforce_must_not(hookup: Hookup) -> None:
    """Check every ENFORCED clause against the netlist; refuse on any breach.

    Reports EVERY violation at once — a builder fixing one constraint at a
    time from N successive refusals is a worse experience than one list.
    """
    violations: List[str] = []
    touched = {str(w.frm) for w in hookup.wires} | {
        str(w.to) for w in hookup.wires}

    nets: Optional[Dict[str, str]] = None
    for m in hookup.must_not:
        if m.kind == "unwired":
            hit = sorted(p for p in m.resolved if p in touched)
            if hit:
                wires = [f"{w.frm} -> {w.to}" for w in hookup.wires
                         if str(w.frm) in hit or str(w.to) in hit]
                violations.append(
                    f"must_not '{m.id}': {', '.join(hit)} MUST BE UNCONNECTED "
                    f"but is wired ({'; '.join(wires)}) — {m.why}")
        elif m.kind == "distinct_nets":
            if nets is None:
                nets = hookup.nets()
            by_net: Dict[str, List[str]] = {}
            for pad in m.resolved:
                by_net.setdefault(nets.get(pad, pad), []).append(pad)
            for shared in by_net.values():
                if len(shared) > 1:
                    reason = hookup.same_net_reason(shared[0], shared[1])
                    violations.append(
                        f"must_not '{m.id}': {', '.join(shared)} are on ONE "
                        f"net and must be distinct ({reason}) — {m.why}")
        elif m.kind == "absent_parts":
            for catalog_id in m.clause:
                placed = [hp.id for hp in hookup.parts if hp.part == catalog_id]
                if placed:
                    violations.append(
                        f"must_not '{m.id}': part '{catalog_id}' MUST NOT be "
                        f"placed but is, as {', '.join(placed)} — {m.why}")
    if violations:
        raise HookupError(
            "the netlist violates its own `must_not:` block\n  - "
            + "\n  - ".join(violations))


# ---------------------------------------------------------------------------
# `facts:` — provenance ([DX-SPEC-CARRIES-ITS-EVIDENCE])
# ---------------------------------------------------------------------------

def _parse_facts(raw_block: Any) -> Tuple[HookupFact, ...]:
    if raw_block in (None, []):
        return ()
    if not isinstance(raw_block, list):
        raise HookupError("hookup.facts must be a list")
    seen: Dict[str, int] = {}
    out: List[HookupFact] = []
    for i, entry in enumerate(raw_block):
        where = f"hookup.facts[{i}]"
        if not isinstance(entry, dict):
            raise HookupError(f"{where} must be a mapping: {entry!r}")
        fid = entry.get("id")
        if not fid or not isinstance(fid, str):
            raise HookupError(f"{where} needs a string `id`: {entry!r}")
        if fid in seen:
            raise HookupError(f"{where}: duplicate fact id '{fid}'")
        seen[fid] = i
        for req in ("what", "value"):
            if not entry.get(req) or not isinstance(entry[req], (str, int, float)):
                raise HookupError(f"{where} ('{fid}') needs a `{req}`")
        prov = str(entry.get("provenance", "")).strip()
        if prov not in PROVENANCE:
            raise HookupError(
                f"{where} ('{fid}'): `provenance` must be one of {PROVENANCE}, "
                f"got {entry.get('provenance')!r} — a number whose KIND OF "
                f"TRUE is unrecorded reads as fact")
        source = entry.get("source")
        mbb = bool(entry.get("measure_before_build", False))
        if_diff = entry.get("if_different")
        if "measured" not in entry:
            raise HookupError(
                f"{where} ('{fid}'): needs an explicit `measured:` slot — use "
                f"`measured: {EMPTY}` until a bench fills it.  An ABSENT key is "
                f"indistinguishable from one nobody thought to write "
                f"([DX-SPEC-CARRIES-ITS-EVIDENCE])")
        measured = str(entry.get("measured"))
        if prov in ("datasheet", "computed") and not source:
            raise HookupError(
                f"{where} ('{fid}'): provenance '{prov}' requires a `source:` "
                f"(the page cited, or the arithmetic)")
        if prov == "assumed":
            if not mbb:
                raise HookupError(
                    f"{where} ('{fid}'): an `assumed` fact must carry "
                    f"`measure_before_build: true` — an assumption nobody "
                    f"intends to check is not an assumption")
            if not if_diff:
                raise HookupError(
                    f"{where} ('{fid}'): an `assumed` fact must carry "
                    f"`if_different:` — the reader needs to know what breaks "
                    f"when the meter disagrees")
        if prov == "measured" and measured.strip() == EMPTY:
            raise HookupError(
                f"{where} ('{fid}'): provenance 'measured' with an {EMPTY} "
                f"evidence slot — one of the two is a lie")
        out.append(HookupFact(
            id=fid, what=str(entry["what"]), value=str(entry["value"]),
            provenance=prov,
            source=(str(source) if source else None),
            measure_before_build=mbb,
            if_different=(str(if_diff) if if_diff else None),
            measured=measured))
    return tuple(out)


def parse_hookup(block: Any, *, catalog_path: Optional[str] = None) -> Hookup:
    """Parse + validate a raw `hookup:` mapping into a :class:`Hookup`.

    Raises :class:`HookupError` on any malformed field or bad part/pin ref.
    """
    if not isinstance(block, dict):
        raise HookupError(
            f"`hookup` must be a mapping, got {type(block).__name__}")

    board = str(block.get("board", "")).strip()
    if board not in BOARDS:
        raise HookupError(
            f"hookup.board must be one of {BOARDS}, got {board!r}")

    label_pins = str(block.get("label_pins", LABEL_PINS_DEFAULT)).strip()
    if label_pins not in LABEL_PINS:
        raise HookupError(
            f"hookup.label_pins must be one of {LABEL_PINS}, got {label_pins!r}")

    raw_parts = block.get("parts", []) or []
    if not isinstance(raw_parts, list):
        raise HookupError("hookup.parts must be a list")

    parts: Dict[str, HookupPart] = {}
    ordered: List[HookupPart] = []
    for entry in raw_parts:
        if not isinstance(entry, dict):
            raise HookupError(f"hookup.parts entry must be a mapping: {entry!r}")
        pid = entry.get("id")
        if not pid or not isinstance(pid, str):
            raise HookupError(f"hookup.parts entry needs a string `id`: {entry!r}")
        if pid in parts:
            raise HookupError(f"duplicate hookup part id '{pid}'")
        catalog_ref = entry.get("part")
        if not catalog_ref or not isinstance(catalog_ref, str):
            raise HookupError(
                f"part '{pid}': needs a `part:` catalog reference")
        try:
            pdef = resolve_part(catalog_ref, path=catalog_path)
        except PartNotFound as exc:
            raise HookupError(f"part '{pid}': {exc}") from None
        hp = HookupPart(
            id=pid, part=catalog_ref,
            at=_parse_at(pid, entry.get("at")), definition=pdef)
        parts[pid] = hp
        ordered.append(hp)

    raw_wires = block.get("wires", []) or []
    if not isinstance(raw_wires, list):
        raise HookupError("hookup.wires must be a list")

    wires: List[HookupWire] = []
    for i, entry in enumerate(raw_wires):
        if not isinstance(entry, dict):
            raise HookupError(f"hookup.wires[{i}] must be a mapping: {entry!r}")
        if "from" not in entry or "to" not in entry:
            raise HookupError(
                f"hookup.wires[{i}] needs both `from` and `to`: {entry!r}")
        frm = _parse_endpoint(entry.get("from"), parts, f"[{i}].from")
        to = _parse_endpoint(entry.get("to"), parts, f"[{i}].to")
        pub = entry.get("publishes")
        if pub is not None and not isinstance(pub, str):
            raise HookupError(
                f"hookup.wires[{i}].publishes must be a slot-namespace string")
        sub_raw = entry.get("subscribes")
        sub: Tuple[str, ...] = ()
        if sub_raw is not None:
            sub = _str_list(sub_raw, f"hookup.wires[{i}].subscribes")
            for ns in sub:
                _check_namespace(ns, f"hookup.wires[{i}].subscribes")
        if pub:
            _check_namespace(str(pub), f"hookup.wires[{i}].publishes")
        if pub and sub:
            raise HookupError(
                f"hookup.wires[{i}] carries BOTH `publishes:` and "
                f"`subscribes:` — a seam has ONE direction.  A pin that both "
                f"sources and sinks a value is two seams and needs two wires "
                f"(or the note belongs in `note:`)")
        note = entry.get("note")
        if note is not None and not isinstance(note, str):
            raise HookupError(f"hookup.wires[{i}].note must be a string")
        cond = _parse_conditioning(entry, i)
        wires.append(HookupWire(frm=frm, to=to,
                                publishes=(str(pub) if pub else None),
                                subscribes=sub,
                                note=(str(note) if note else None),
                                conditioning=cond))

    hookup = Hookup(board=board, parts=tuple(ordered), wires=tuple(wires),
                    must_not=_parse_must_not(block.get("must_not"), ordered),
                    facts=_parse_facts(block.get("facts")),
                    label_pins=label_pins)
    _enforce_must_not(hookup)
    return hookup


__all__ = [
    "Hookup", "HookupPart", "HookupWire", "PinRef", "HookupError",
    "MustNot", "HookupFact", "BOARDS", "MUST_NOT_CLAUSES", "PROVENANCE",
    "EMPTY", "parse_hookup", "LABEL_PINS", "LABEL_PINS_DEFAULT",
]
