"""The freshness RECEIVER — `unknown`, verdicts, and freshness classes.

[SPEC:CXC-FRESHNESS].  Realizes two ratified decisions:

  [DEC-FRESHNESS-IS-RECEIVER-SIDE-AND-DECLARED-IN-YAML]
      age is counted BY THE RECEIVER on a MONOTONIC clock against an interval
      the namespace DECLARED in YAML; timeout is 3x that interval floored at
      interval + 100 ms; a per-node heartbeat covers the class a per-namespace
      timeout cannot judge; expiry happens LAZILY ON READ.

  [DEC-UNKNOWN-IS-A-TYPE-TAG-NOT-A-RESERVED-VALUE]
      the state it expires INTO is `unknown` — wire tag 0xFD, zero-length
      payload — never a reserved value like -1 or 255.  Wire-unknown (an
      explicit 0xFD: "I am alive and I know that I do not know") and
      expiry-unknown (the receiver's own timeout) are DIFFERENT MECHANISMS
      CONVERGING ON ONE RECEIVER STATE, and both are needed because a dead
      publisher cannot send 0xFD.

WHY `unknown` IS A VALUE AND NOT A SIDE-CHANNEL.  The widget seam is
`on_bus_state(namespace, value)` — two arguments, implemented ~60 times.  A
freshness side-channel would need every one of those signatures changed, and
until they all were, a widget would keep rendering a stale number while the
receiver quietly knew better.  Making unknown a member of the VALUE DOMAIN is
also just what "type tag" means: 0xFD sits beside 0xFE int32 and 0xFF float32,
so its Python realization sits beside `int` and `float` — a singleton whose
identity IS the tag.  Zero signatures change and every existing consumer
receives it automatically.

⚠ WHAT THIS MODULE DELIBERATELY DOES NOT DO: render.  A widget handed UNKNOWN
gets an honest non-value, not a staleness PRESENTATION — there is no dimmed
state, no staleness marker, no `unknown` visual in the render primitives.  That
gap is real, was measured, and is scoped to [SPEC:CXC-PRIM] as its own dispatch.
See `UNKNOWN.__doc__` for what a consumer can safely do in the meantime.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Callable, Iterable, Sequence

logger = logging.getLogger("composeio.freshness")


class UnknownValueError(TypeError):
    """Raised when `unknown` is coerced to a number or ordered against one.

    Subclasses TypeError so an existing `except TypeError` around a coercion
    keeps working.

    ⛔ COERCION AND ORDERING RAISE ON PURPOSE.  `int(UNKNOWN) -> 0` would draw a
    zeroed gauge that is indistinguishable from a real zero — the exact lie this
    whole mechanism exists to stop, reintroduced one layer lower.  And
    `UNKNOWN > threshold -> False` would SUPPRESS AN ALARM: silence from a
    comparison that never ran reads identically to "the threshold was not
    crossed".  A raise is loud, local, and cannot be mistaken for a reading.
    """


class _Unknown:
    """The singleton `unknown` — wire tag 0xFD, zero-length payload.

    NOT `None`.  `None` already means "no value bytes on the wire" and is a
    legitimate resting state for a namespace nothing has ever published to;
    `unknown` means "there IS a subject here and its value is not currently
    knowable".  Collapsing the two would retire a distinction the codec already
    makes.

    WHAT IS SAFE TO DO WITH IT, for a consumer written before the presentation
    layer exists:

      `is_unknown(v)` / `v is UNKNOWN`   the check, and the only fully safe one
      `bool(v)`                          False — an unknown is not a truthy state
      `str(v)`                           "unknown"
      `v == anything`                    False (never raises; identity only)
      `v + 1`, `v * 2`, ...              UNKNOWN — arithmetic PROPAGATES

    ARITHMETIC PROPAGATES BY RATIFICATION, not convenience: D4b says a derived
    value "is unknown if ANY input is unknown", and expression-level propagation
    is that rule holding automatically wherever a binding transform does maths,
    instead of every transform having to remember it.
    """

    __slots__ = ()
    _instance: "_Unknown | None" = None

    def __new__(cls) -> "_Unknown":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # --- identity ------------------------------------------------------- #
    def __repr__(self) -> str:
        return "UNKNOWN"

    def __str__(self) -> str:
        return "unknown"

    def __bool__(self) -> bool:
        return False

    def __eq__(self, other: object) -> bool:
        return other is self

    def __ne__(self, other: object) -> bool:
        return other is not self

    def __hash__(self) -> int:
        return hash("composeio.UNKNOWN")

    def __copy__(self) -> "_Unknown":
        return self

    def __deepcopy__(self, memo: dict) -> "_Unknown":
        return self

    def __reduce__(self) -> str:
        return "UNKNOWN"

    # --- arithmetic PROPAGATES (D4b: unknown if ANY input is unknown) ---- #
    def _propagate(self, *_a: Any, **_k: Any) -> "_Unknown":
        return self

    __add__ = __radd__ = __sub__ = __rsub__ = _propagate
    __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _propagate
    __floordiv__ = __rfloordiv__ = __mod__ = __rmod__ = _propagate
    __pow__ = __rpow__ = __neg__ = __pos__ = __abs__ = _propagate
    __round__ = _propagate

    # --- coercion and ordering REFUSE ----------------------------------- #
    def _refuse(self, *_a: Any, **_k: Any):
        raise UnknownValueError(
            "value is UNKNOWN (stale, or never received) and cannot be used as "
            "a number. Check `is_unknown(value)` before coercing or comparing. "
            "Coercing would render an unknown as a real zero, and ordering it "
            "would silently suppress a threshold — both are the stale-value lie "
            "this check exists to prevent. See [SPEC:CXC-FRESHNESS].")

    __int__ = __float__ = __index__ = __complex__ = _refuse
    __lt__ = __le__ = __gt__ = __ge__ = _refuse


#: The one and only `unknown`.  Compare with `is`.
UNKNOWN = _Unknown()


def is_unknown(value: Any) -> bool:
    """True iff `value` is the unknown singleton.

    Prefer this to `value is UNKNOWN` at module boundaries — it reads as a
    question about the value rather than as a pointer comparison, and it is the
    one call a consumer must make before treating a bus value as a number.
    """
    return value is UNKNOWN


class Verdict(Enum):
    """WHY a value is (or is not) believable — the receiver's full answer.

    A bare stale/fresh boolean cannot distinguish the four different ways a
    value fails to be believable, and they call for different responses: a
    NEVER_RECEIVED namespace may just be early in boot, a STALE one has a
    publisher that died, a NODE_SILENT one has a whole board gone, and an
    UNDECLARED one was never judged at all.  Collapsing them would hand the
    glass one word where the operator needs four.
    """

    #: Received within its declared window.
    FRESH = "fresh"

    #: Declared a cadence, and nothing has arrived within 3x it.
    STALE = "stale"

    #: The publisher said 0xFD — alive, and explicitly does not know.
    WIRE_UNKNOWN = "wire_unknown"

    #: Declared a cadence; nothing has EVER arrived.  Not the same as STALE:
    #: there is no last value being disbelieved, there was never a first one.
    NEVER_RECEIVED = "never_received"

    #: Quiet-by-construction, and its NODE stopped beating.  The only way a
    #: namespace whose silence is normal can be condemned.
    NODE_SILENT = "node_silent"

    #: Nothing was declared, so no verdict was ever authorised.  ⛔ NOT a
    #: synonym for FRESH — it means the question was never asked.  Reporting it
    #: as FRESH is how a system claims confidence it never earned.
    UNDECLARED = "undeclared"


#: Verdicts under which a read must NOT hand back the stored value.
_UNBELIEVABLE = frozenset({
    Verdict.STALE, Verdict.WIRE_UNKNOWN,
    Verdict.NEVER_RECEIVED, Verdict.NODE_SILENT,
})


def verdict_is_believable(v: Verdict) -> bool:
    """Whether a verdict permits handing back the stored value."""
    return v not in _UNBELIEVABLE


# --------------------------------------------------------------------------- #
# The per-node heartbeat receiver
# --------------------------------------------------------------------------- #

#: D4's staleness convention applied to the heartbeat.  Same 3x, same reason.
HEARTBEAT_MULTIPLIER = 3.0


class HeartbeatReceiver:
    """Counts node beats and answers "is that node still there?".

    ⛔ THIS IS THE HALF THAT WAS NEVER BUILT.  `ComposeIOBus.publish_heartbeat`
    shipped and says so in its own docstring: "the receiver half — a monitor
    counting missed intervals and driving a node to degraded — does not exist
    anywhere in this repo, and spec/01 currently describes it as though it
    does."  This is that monitor.

    WHY IT IS LOAD-BEARING RATHER THAN BELT-AND-BRACES.  A per-namespace timeout
    cannot judge a namespace that is SILENT BY DESIGN.  `throttle/*` is gated on
    a sample clock AND a deadband against the last EMITTED value — "quiet-bus by
    construction" — so a stationary lever publishes nothing, indefinitely, while
    being perfectly healthy.  For that entire class the node heartbeat is the
    ONLY liveness signal that exists, which is why a quiet namespace's verdict
    is routed here instead of to a timeout that would fire the moment David
    stopped moving the lever.

    THE DECLARED CADENCE RIDES ON THE BEAT.  `publish_heartbeat` puts
    `interval_s` in the payload precisely so this receiver derives the timeout
    from what the publisher SAID rather than from a configured copy of it — the
    second copy being the thing that eventually disagrees.
    """

    def __init__(self, clock: Callable[[], float], *,
                 default_interval_s: float = 5.0) -> None:
        self._clock = clock
        self._default_interval = default_interval_s
        #: node -> (monotonic receipt, declared interval)
        self._beats: dict[str, tuple[float, float]] = {}

    def note(self, node: str, interval_s: float | None = None) -> None:
        """Record a beat from `node`, at the cadence the beat declared."""
        if not node:
            return
        iv = interval_s if (interval_s and interval_s > 0) else self._default_interval
        self._beats[node] = (self._clock(), float(iv))

    def age(self, node: str) -> float | None:
        """Seconds since `node` last beat, or None if it never has."""
        rec = self._beats.get(node)
        return None if rec is None else self._clock() - rec[0]

    def timeout(self, node: str) -> float | None:
        rec = self._beats.get(node)
        return None if rec is None else HEARTBEAT_MULTIPLIER * rec[1]

    def is_alive(self, node: str) -> bool:
        """⛔ A node never heard from is NOT alive.

        The tempting default is True — "no evidence of death" — and it is
        exactly backwards: it means every node in the fleet is certified alive
        before a single beat arrives, including ones that never boot.  Absence
        of evidence is not evidence of liveness.
        """
        rec = self._beats.get(node)
        if rec is None:
            return False
        at, interval = rec
        return (self._clock() - at) < HEARTBEAT_MULTIPLIER * interval

    def nodes(self) -> dict[str, dict[str, Any]]:
        """Every node seen, with its age, timeout and verdict — for a monitor."""
        return {
            node: {
                "age_s": self.age(node),
                "timeout_s": self.timeout(node),
                "interval_s": interval,
                "alive": self.is_alive(node),
            }
            for node, (_at, interval) in self._beats.items()
        }

    def forget(self, node: str) -> None:
        self._beats.pop(node, None)


# --------------------------------------------------------------------------- #
# Freshness classes — David's addition, and it is a design requirement
# --------------------------------------------------------------------------- #

class FreshnessClass:
    """A NAMED group of namespaces that must ALL be fresh together.

    David, ratifying D4, verbatim: "or a simple grouping of semantic messages
    that must all have a specific value within the bounding window defined as a
    class."

    THIS IS STRICTLY MORE EXPRESSIVE THAN A PER-NAMESPACE TIMEOUT, and it is the
    same rule D4b states for derived values — age is the MAX of the inputs'
    ages, and the result is unknown if ANY input is unknown — made into a
    first-class, nameable, YAML-declarable object.  A navigation solution
    computed from three sensors is not fresh because its newest input is fresh;
    it is as old as its OLDEST input, and it is not a solution at all if one
    input is missing.  Taking the max is what stops the freshest member from
    laundering the staleness of the others.
    """

    def __init__(self, name: str, members: Sequence[str], *, bus: Any) -> None:
        if not members:
            raise ValueError(
                f"freshness class {name!r} declares no members. An empty class "
                f"would report FRESH forever — a green light wired to nothing.")
        self.name = name
        self.members = list(members)
        self._bus = bus

    # --- the two ratified rules ----------------------------------------- #

    @property
    def age(self) -> float | None:
        """The MAX of the members' ages — None if any member has no age yet."""
        ages: list[float] = []
        for m in self.members:
            ns = self._bus.get_namespace(m)
            a = None if ns is None else ns.age
            if a is None:
                return None
            ages.append(a)
        return max(ages) if ages else None

    @property
    def verdict(self) -> Verdict:
        """FRESH only if EVERY member is believable; otherwise the first
        unbelievable member's own verdict, so the report names WHICH member
        broke the class rather than just that something did."""
        for m in self.members:
            ns = self._bus.get_namespace(m)
            if ns is None:
                return Verdict.NEVER_RECEIVED
            v = ns.verdict
            if not verdict_is_believable(v):
                return v
        return Verdict.FRESH

    @property
    def value(self) -> Any:
        """The members' values as a dict — or UNKNOWN if ANY member is."""
        if not verdict_is_believable(self.verdict):
            return UNKNOWN
        return {m: self._bus.get_state(m) for m in self.members}

    def failing_members(self) -> dict[str, Verdict]:
        """Which members are unbelievable, and why.  For an operator who needs
        to know WHICH sensor died, not merely that the solution is bad."""
        out: dict[str, Verdict] = {}
        for m in self.members:
            ns = self._bus.get_namespace(m)
            v = Verdict.NEVER_RECEIVED if ns is None else ns.verdict
            if not verdict_is_believable(v):
                out[m] = v
        return out

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "members": list(self.members),
            "age_s": self.age,
            "verdict": self.verdict.value,
            "failing": {k: v.value for k, v in self.failing_members().items()},
        }

    def __repr__(self) -> str:
        return f"<FreshnessClass {self.name!r} {self.verdict.value} members={len(self.members)}>"


def derived_age(ages: Iterable[float | None]) -> float | None:
    """D4b's derived-value rule, standalone: age is the MAX of the inputs'.

    None (an input with no age at all) dominates, because an input that has
    never been received makes the derivation unknown rather than merely old.
    """
    out = 0.0
    for a in ages:
        if a is None:
            return None
        out = max(out, a)
    return out
