"""SEAM LINT — no module may CHOOSE the substrate. The substrate is CONFIGURED.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], and the sibling of
``test_no_raw_paho_outside_transport.py``. That file asks "does this module reach
paho?"; this one asks the question that actually decides the architecture:

    DOES THIS MODULE CHOOSE THE SUBSTRATE?

WHY REACHABILITY IS THE WRONG QUESTION
--------------------------------------
Transitive reach to paho IS the design. Every correct ComposeIO app reaches paho —
through `Transport`, through the registry, through the bus. A reachability check
would flag every correct app and be useless. The paho guard is narrow on purpose: it
catches only the DIRECT `import paho`, i.e. a module that has gone around the waist.

But going around the waist has FOUR doors, and the paho guard covers exactly one:

    1. `import paho`                  -> caught, repo-wide, by the sibling guard.
    2. `MQTTTransport(...)`           -> THIS FILE. Naming the concrete class binds
                                         the module to one wire just as hard as the
                                         import did, while looking like clean code:
                                         the module never says "paho", it says
                                         "Transport", and the seam lint stayed green.
    3. `connect_transport("mqtt", …)` -> THIS FILE. The substrate name as a STRING
                                         LITERAL at a dispatch site. No class, no
                                         import, same lock-in.
    4. `{"type": "mqtt", …}`          -> THIS FILE, added 2026-08-13. The substrate
                                         name as a SPEC-DICT LITERAL. It goes through
                                         the waist, so it is the weakest of the four
                                         — and it is the one the other three DRAIN
                                         INTO. See DOOR 4 below.

Doors 2, 3 and 4 are the same defect wearing different syntax, so they are one guard.

SUBSTRATE-AGNOSTIC BY CONSTRUCTION — AND IT PAID IMMEDIATELY
------------------------------------------------------------
The brief that produced this file named eight `MQTTTransport(...)` sites. Scoping the
guard to MQTT would have reproduced, one level up, the mistake the paho guard just
finished unpicking: a rule keyed on the substrate we happen to be worried about today.
A module that hardcodes CAN is wrong for exactly the same reason and by exactly the
same argument, so the guard is written over ALL concrete transports.

That generalization found a site the MQTT-shaped brief did not contain:
`simulator/app.py:122` constructs `UARTTransport(port=UART_PORT, baud=UART_BAUD)`.
It was never on anybody's list because nobody was grepping for UART.

THE GUARDED CLASS SET IS DERIVED FROM THE REPO, NOT WRITTEN DOWN
-----------------------------------------------------------------
`_concrete_transport_classes()` AST-parses every ``python/composeio/transport_*.py``
and collects each ``class X(Transport)`` it finds, keying the family off the module
name (``transport_can.py`` -> ``can``). Nothing is hand-listed, so:

  * the day somebody adds ``transport_ble.py``, ``BLETransport(...)`` is guarded in
    every module in the repo without anyone remembering to update this file;
  * the set cannot drift from what the platform actually implements, because it IS
    what the platform actually implements.

`test_the_registry_builds_only_classes_this_guard_knows_about` cross-checks that
derivation against a SECOND, independent one — the classes `transport_registry.py`'s
factories actually construct — so a silent disagreement between them is red.

WHAT IS OUT OF SCOPE (excluded, NOT exempted)
----------------------------------------------
  * ``test_*.py`` and modules under a ``tests`` directory. A pytest module
    legitimately constructs a `CANTransport` to test CAN. Its file set churns
    constantly; making the transports' own tests allow-list entries would be noise
    that never shrinks. `smoke_*` / `drive_*` / `gen_*` DRIVERS are carved back IN,
    for the reason the sibling guard states: an exemption keyed on WHERE a file lives
    stops protecting it the moment the file moves.

There are NO tree exclusions and the mechanism to add one is deliberately absent.
That is the whole lesson of 2026-08-13: an ALLOWED entry can rot, because it names a
file the scan still reads; a tree-exclusion prefix can NEVER rot, because it swallows
every file added beneath it forever. `compose_projects/bsg/` sat behind such a prefix
on a LICENSING rationale doing ARCHITECTURAL duty, and hid the platform's largest
bus-facing consumer from its own seam lint for months. This file has no `_EXCLUDED_
TREES` at all — not an empty one, none — so there is no construct here to misuse.

EXEMPTIONS ARE RULES OVER CONTENT. THE ONE LIST SAYS WHY IT COULD NOT BE A RULE
-------------------------------------------------------------------------------
RULE A — SUBSTRATE DISPATCHER. A module may construct concrete transports iff it
constructs TWO OR MORE distinct substrate families AND, for every family it
constructs, it also SELECTS ON that family's name at runtime — either
``<name> == "mqtt"`` (a comparison against a value that came from outside) or
``register("mqtt", …)`` (a factory table keyed by the type name). Such a module does
not make the choice; it IMPLEMENTS the choice somebody else made. `transport_registry
.py` and `ComposeApp.connect_transport` are the only two, and both are found by the
predicate rather than by name.

  The second half of the predicate is load-bearing and is pinned by
  `test_two_substrates_without_a_selector_is_worse_not_better`. "Constructs two
  substrates" alone describes an app that has hardcoded BOTH, which is worse than
  hardcoding one. The selector is what proves the module is a table and not a choice.

RULE B — BRIDGE. Ported verbatim in spirit from the sibling guard: a module under a
package directory named ``*_bridge/`` may hold MQTT iff it also names a SECOND,
non-MQTT substrate. A Transport lets ONE app speak ONE substrate; a bridge holds TWO
and translates. The DIRECTORY is only half the test — an address exempts nothing on
its own — so the file must PROVE it is a bridge from its content. An MQTT-only module
in a `*_bridge/` folder is a bypass wearing a bridge's name and stays RED
(`test_a_bridge_directory_alone_does_not_exempt_anything`).

THE ONE LIST — hand-run DRIVERS (`LEGITIMATE`), and why no rule could replace it.
A rule has to separate "this module's SUBJECT is the substrate" (a smoke driver that
exists to prove MQTT works end-to-end against a real broker) from "this module merely
USES the substrate" (`carousel/kiosk.py`, which is a host router that happens to
listen over MQTT). Every syntactic property they share:

  * both are `__main__` entry points;
  * both have zero importers outside their own tests (verified 2026-08-13);
  * both construct exactly one transport and drive it with the `Transport` ABC.

The difference is PURPOSE, and purpose is not a syntactic property of a file. Reach
cannot separate them either — the property that saved `bus_client.py` in the sibling
guard — because both are unreachable by import and yet one is DEPLOYED. So this is a
list, and it is made safe the only way a list can be: by leg 2, which turns an entry
RED the moment the file stops constructing a transport. David's ruling of 2026-08-10
already settled the shape for this exact file class — "if a smoke driver has a
legitimate need for direct paho, put it on the whitelist explicitly" — and a NAMED
entry survives the relocation out of ``python/tests/`` that has been discussed.

SEEDED GREEN, LIKE ITS SIBLING, AND FOR THE SAME REASON
--------------------------------------------------------
`NOT_YET_CONVERTED` carries today's TRIAGED violation set (sweep of 2026-08-13), so
this file is green the minute it lands. Its value is entirely in what it forbids
NEXT. Porting those nine sites is a separate change: this tree has four agents live
in it, and the triage is the valuable half. Each entry names its recommended
disposition, so the port is a mechanical exercise rather than a re-derivation.

DOOR 4 — THE SPEC DICT LITERAL, AND WHY IT HAD TO BE CLOSED BEFORE ANY PORT
---------------------------------------------------------------------------
Closed 2026-08-13. ``transport_registry.build({"type": "mqtt", …})`` and
``acquire_bus(transport_spec={"type": "mqtt"})`` name the substrate in source just as
completely as `MQTTTransport(...)` does. They go THROUGH the waist — the module is not
bound to the CLASS, and a spec dict is a value config can replace — so they are a
weaker defect than doors 2 and 3. They are not a weaker defect than NOTHING, which is
what they were being measured as.

THE ORDERING IS THE WHOLE POINT, and it is not a style preference. The prescribed
conversion for a door-2 site is "build it through the registry". Written carelessly
that is:

    -   transport = MQTTTransport(broker=b, port=p)          # RED, door 2
    +   transport = build({"type": "mqtt", "broker": b, "port": p})   # green, door 4

which LAUNDERS A CAUGHT VIOLATION INTO AN UNCAUGHT ONE. The module is every bit as
pinned to MQTT as it was, the guard goes quiet, and the backlog entry gets deleted by
leg 2 as "CONVERTED". Porting before guarding lets the port itself manufacture the
defect it was sent to remove, and the suite reports success. So door 4 landed FIRST,
in its own commit, with `test_the_door_2_to_door_4_laundering_rewrite_is_caught`
constructing exactly that rewrite and demanding it stay red.

THE RULE IS OVER THE VALUE, NOT OVER THE CONSUMER — this is the load-bearing choice.
The obvious implementation keys on the callee: flag `build(...)`, `acquire_bus(...)`,
`transport_spec=`. That is a LIST of function names wearing a rule's clothes, and it
has two failure modes this file will not accept. It rots the day a fourth spec
consumer is written; and it is defeated by one line of ordinary refactoring —

    spec = {"type": "mqtt", "broker": b}     # the choice is HERE...
    transport = build(spec)                  # ...and this call site is innocent

so the door would stand open to anyone who assigned to a variable first. Instead the
hit is the DICT LITERAL ITSELF: a `{<spec key>: "<substrate family>"}` mapping written
in source IS the choice, wherever it is written and whoever consumes it. Nothing about
the reader matters, so there is nothing about the reader to keep up to date.

BOTH HALVES OF IT ARE DERIVED, keeping the substrate-agnostic promise:

  * the FAMILY set is the same `_GUARDED` set doors 2 and 3 use — ``{"type": "can"}``
    is a hit for the same reason ``{"type": "mqtt"}`` is, and a `transport_ble.py`
    added tomorrow is guarded at all four doors at once with nobody editing this file;
  * the SPEC KEY is read out of `transport_registry.build` itself (`_spec_type_key`),
    because that function is the definition of what a spec dict is. If the registry
    ever renames `type:`, this guard follows it in the same commit rather than
    silently guarding a key nothing reads any more.

A NON-LITERAL VALUE IS NOT A HIT — ``{"type": t_type}``, ``{"type": os.environ[...]}``,
``{**base, "broker": b}`` all pass, exactly as `connect_transport(t_type, …)` does at
door 3. That is the shape the guard is ASKING for; flagging it would make the rule
demand the thing it is demanding. `test_a_configured_spec_dict_is_not_a_choice` pins
it, because a rule that fires on everything passes its own firing test and is worthless.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TRANSPORT_PKG = _REPO_ROOT / "python" / "composeio"
# transport.py / transport_local.py / transport_contract.py (and bus.py) MOVED to the
# composebus kernel distribution ([SPEC:CXC-COMPOSEBUS-KERNEL] §7) and are aliased back
# as composeio.<name>; the derivation reads BOTH source trees so it sees exactly the
# files it saw before the move.
_KERNEL_PKG = _REPO_ROOT / "python" / "composebus" / "composebus"
_SOURCE_PKGS = (_TRANSPORT_PKG, _KERNEL_PKG)


# --------------------------------------------------------------------------- #
# the guarded class set — DERIVED from the repo, never written down
# --------------------------------------------------------------------------- #
def _concrete_transport_classes() -> dict[str, str]:
    """`class X(Transport)` in `python/composeio/transport_*.py` -> substrate family.

    Two independent things make this the right source:

      * it is what the platform ACTUALLY implements, so it cannot drift from reality;
      * ``transport.py`` (the ABC) is excluded for free — the glob requires the
        underscore — and so is anything that subclasses `Transport` OUTSIDE the
        transport package. That last exclusion is deliberate, not incidental:
        `peer/hub.py`, `peer/link.py` and `peer/canbridge.py` subclass `Transport`,
        and `_PrintTransport` is defined inline by three dry-run publishers. None of
        those bind a module to a WIRE — a locally-defined stub is not a substrate
        choice, it is a test double with a class statement.
    """
    classes: dict[str, str] = {}
    for path in sorted(p for pkg in _SOURCE_PKGS for p in pkg.glob("transport_*.py")):
        family = path.stem[len("transport_"):]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                             filename=str(path))
        except SyntaxError:                     # pragma: no cover - not in this repo
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            if any(isinstance(b, ast.Name) and b.id == "Transport" for b in node.bases):
                classes[node.name] = family
    return classes


_CLASSES = _concrete_transport_classes()

# The dispatch functions that take a substrate NAME. A literal here is a choice.
_DISPATCH_FUNCS = ("connect_transport",)

_REGISTRY = _TRANSPORT_PKG / "transport_registry.py"


# --------------------------------------------------------------------------- #
# DOOR 4 — the spec key, DERIVED from the function that defines what a spec is
# --------------------------------------------------------------------------- #
def _spec_type_key() -> str:
    """The key `transport_registry.build` reads to decide which substrate to make.

    Not written down, for the same reason the class set is not written down. A spec
    dict is whatever `build()` says a spec dict is, so the guard asks `build()`:
    the single ``spec.get("<key>")`` in its body IS the definition of door 4, and
    the day somebody renames `type:` this guard renames with it — in that commit,
    visibly, rather than continuing to guard a key nothing reads.

    The scan is scoped to `build`'s own body deliberately. The FACTORIES below it
    read `spec.get("broker")`, `spec.get("channel")`, `spec.get("port")` and half a
    dozen more; those are a substrate's PARAMETERS, and a module naming a broker
    host has configured an address, not chosen a wire.
    """
    tree = ast.parse(_REGISTRY.read_text(encoding="utf-8"), filename=str(_REGISTRY))
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "build"):
            continue
        keys = [c.args[0].value for c in ast.walk(node)
                if isinstance(c, ast.Call) and _callee(c.func) == "get" and c.args
                and isinstance(c.args[0], ast.Constant)
                and isinstance(c.args[0].value, str)]
        if len(keys) == 1:
            return keys[0]
        raise AssertionError(                   # pragma: no cover - guarded by a test
            f"`transport_registry.build` now reads {keys!r} off the spec; door 4 can "
            f"no longer derive WHICH key names the substrate. Pick it explicitly here "
            f"and say why — do not fall back to a hardcoded 'type'.")
    raise AssertionError(                       # pragma: no cover - guarded by a test
        "`transport_registry.build` is gone; door 4's spec key cannot be derived.")


def _callee(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


#: `"type"` today — but read out of `build()`, not typed here. See `_spec_type_key`.
_SPEC_KEY = _spec_type_key()


def _declarable_families() -> set[str]:
    """A SUBSTRATE is a wire you can ASK FOR BY NAME. Derived, third pass.

    THIS NARROWING WAS FORCED BY THE GUARD'S FIRST RUN, and it is worth recording
    because the finding was real and the verdict was not. Deriving the guarded set
    from `class X(Transport)` alone flagged `python/composeio/bus.py:89`:

        self._local = local_transport or LocalTransport()

    That is not a substrate choice. `LocalTransport` is the LOCAL TIER (DP-CIO-5) —
    direct function calls, zero serialization, never leaves the process. Every
    `ComposeIOBus` has one, no scene can decline it, and "which wire?" is not a
    question you can ask of a synchronous callback. Guarding it would have demanded
    that the bus configure something that has nothing to configure.

    So the set is narrowed by a property, not by a name: a family is guarded IFF some
    module in `python/composeio/` both CONSTRUCTS its class and SELECTS ON its type
    name — i.e. somewhere, somebody can name that substrate and be handed it. That is
    exactly the set a scene's `type:` can reach, and it is exactly the set for which
    "the module chose instead of the config" is a meaningful accusation.

    Both halves are required, and the second one is why this is not just a re-read of
    the registry: `capture.py:116` contains `name == "local"` — but that is a TIER
    name, and `capture.py` constructs no transport, so it cannot make `local`
    declarable. `http_relay` IS declarable and IS guarded even though the registry
    has never heard of it, because `ComposeApp.connect_transport` will build one from
    the string "http_relay" (see `test_http_relay_is_guarded_though_unregistered`).
    """
    declarable: set[str] = set()
    for path in sorted(p for pkg in _SOURCE_PKGS for p in pkg.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"),
                             filename=str(path))
        except SyntaxError:                     # pragma: no cover - not in this repo
            continue
        built = {_CLASSES[n] for n in
                 (_callee(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call))
                 if n in _CLASSES}
        declarable |= built & _runtime_selectors(tree)
    return declarable


def substrate_choices_in(source: str, filename: str = "<scan>",
                         classes: dict[str, str] | None = None,
                         ) -> list[tuple[int, str, str]]:
    """(lineno, family, what) for every substrate CHOICE in *source*.

    AST, not grep, and the sibling guard's two reasons apply verbatim here — plus a
    third one this guard found on its first run. ``services/uart_mqtt_bridge/
    config_server.py:235`` contains the text ``CANTransport("can0")`` inside a
    DOCSTRING, as the worked example of a seam. A grep calls that a substrate choice;
    it is a sentence. (`test_a_docstring_example_is_not_a_construction` pins it.)

    An unparseable file yields nothing — it cannot construct anything either.
    """
    classes = _GUARDED if classes is None else classes
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    return _choices_in_tree(tree, classes)


def _choices_in_tree(tree: ast.AST, classes: dict[str, str]) -> list[tuple[int, str, str]]:
    families = set(classes.values())
    out: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        # DOOR 4 — the substrate name as the `type:` of a SPEC DICT LITERAL.
        # Keyed on the VALUE, not on who consumes it: `spec = {"type": "mqtt"}` on
        # one line and `build(spec)` on the next is the same choice as the inline
        # call, and a consumer-keyed rule would see neither. A NON-constant value
        # (`{"type": t_type}`) is the config-driven shape this guard is asking for
        # and is deliberately NOT a hit.
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if not (isinstance(k, ast.Constant) and k.value == _SPEC_KEY):
                    continue                    # `**spread` gives k=None; skipped here
                if not (isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    continue
                fam = v.value.strip().lower()
                if fam in families:
                    out.append((node.lineno, fam,
                                '{%r: "%s", ...}' % (_SPEC_KEY, v.value)))
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _callee(node.func)
        # DOOR 2 — constructing a concrete Transport binds the module to one wire.
        if name in classes:
            out.append((node.lineno, classes[name], f"{name}(...)"))
            continue
        # DOOR 3 — the substrate name as a string literal at a dispatch site.
        # A NON-literal first argument (`connect_transport(t_type, ...)`) is the
        # correct, config-driven call and is deliberately NOT a hit.
        if name in _DISPATCH_FUNCS and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                out.append((node.lineno, a0.value.strip().lower(),
                            f'{name}("{a0.value}", ...)'))
    return sorted(set(out))


# --------------------------------------------------------------------------- #
# RULE A — SUBSTRATE DISPATCHER. No list, nothing to rot.
# --------------------------------------------------------------------------- #
def _runtime_selectors(tree: ast.AST) -> set[str]:
    """Substrate names this module SELECTS ON rather than picks.

    Two forms, both meaning "the name came from outside this module":

      * ``<name> == "mqtt"`` / ``<name> in ("mqtt", …)`` — an if/elif dispatch on a
        parameter. `ComposeApp.connect_transport` is exactly this.
      * ``register("mqtt", _mqtt)`` — a factory table keyed by the type name.
        `transport_registry` is exactly this.

    The left-hand side must be a Name or an Attribute. ``"mqtt" == "mqtt"`` is not a
    dispatch, and neither is a literal compared to a literal.
    """
    sel: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and isinstance(node.left, (ast.Name, ast.Attribute)):
            for op, cmp in zip(node.ops, node.comparators):
                if not isinstance(op, (ast.Eq, ast.In)):
                    continue
                for c in (cmp.elts if isinstance(cmp, (ast.Tuple, ast.List, ast.Set)) else [cmp]):
                    if isinstance(c, ast.Constant) and isinstance(c.value, str):
                        sel.add(c.value.strip().lower())
        elif isinstance(node, ast.Call) and _callee(node.func) == "register" and node.args:
            a0 = node.args[0]
            if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                sel.add(a0.value.strip().lower())
    return sel


# The guarded set, at last: concrete transport classes for families that some
# dispatcher will hand out BY NAME. Nothing here is written down; all three inputs
# (the classes, the constructions, the selectors) are read out of the repo.
_DECLARABLE = _declarable_families()
_GUARDED: dict[str, str] = {c: f for c, f in _CLASSES.items() if f in _DECLARABLE}
_FAMILIES = set(_GUARDED.values())


def _dispatcher_exemption(tree: ast.AST, families: set[str]) -> str:
    """The stated reason this module is a dispatcher, or '' if it is not."""
    if len(families) < 2:
        return ""
    missing = sorted(families - _runtime_selectors(tree))
    if missing:
        return ""
    return (f"DISPATCHER: constructs {len(families)} substrate families "
            f"({'+'.join(sorted(families))}) and SELECTS ON every one of them at "
            f"runtime (an `== \"<type>\"` branch or a `register(\"<type>\", …)` "
            f"table). It does not make the choice, it implements it. Exempt by RULE.")


# --------------------------------------------------------------------------- #
# RULE B — BRIDGE. Ported from the sibling guard; same argument, same shape.
# --------------------------------------------------------------------------- #
# Tokens, not imports, and deliberately so — the strongest bridge VERIFIERS in this
# repo re-implement the far substrate from the kernel ABI (`deploy/can_mqtt_bridge/
# loopback_test.py` packs SocketCAN frames with stdlib `socket`) precisely so a
# property read back through the same assumption that wrote it cannot self-verify.
_SECOND_SUBSTRATE_TOKENS: dict[str, tuple[str, ...]] = {
    "can": ("AF_CAN", "CAN_RAW", "socketcan", "SocketCAN", "transport_can",
            "CANTransport", "python-can"),
    "uart": ("transport_uart", "UARTTransport", "pyserial", "serial.Serial",
             "termios"),
}


def _under_bridge_package(rel: str) -> bool:
    """True if any DIRECTORY on the path is named `*_bridge`.

    Directories only. Renaming a module to `mqtt_bridge.py` must not buy an
    exemption: a bridge is a PACKAGE (config server, frame codec, verifier); a
    single file that calls itself a bridge is a file that calls itself a bridge.
    """
    return any(part.endswith("_bridge") for part in rel.split("/")[:-1])


def _bridge_exemption(rel: str, source: str, families: set[str]) -> str:
    """The stated reason *rel* is exempt as a bridge, or '' if it is not."""
    if not _under_bridge_package(rel):
        return ""
    named = set(families)
    for fam, toks in _SECOND_SUBSTRATE_TOKENS.items():
        if any(t in source for t in toks):
            named.add(fam)
    if len(named - {"mqtt"}) < 1 or len(named) < 2:
        return ""
    return (f"BRIDGE ({'+'.join(sorted(named))}): under a `*_bridge/` package AND "
            f"names a second substrate, so it holds TWO live substrates by design. A "
            f"Transport lets one app speak one substrate; a bridge translates between "
            f"two. Exempt by RULE — there is no entry to go stale, and deleting the "
            f"bridge deletes the exemption with it.")


# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] class 1 — LEGITIMATE BY DESIGN. The one list, and why.
# --------------------------------------------------------------------------- #
# See the module docstring for the argument that no content rule can replace this
# one: the difference between a smoke driver and a deployed app is PURPOSE, and
# purpose is not a syntactic property. Leg 2 is what keeps the list honest.
_DRIVER_REASON = (
    "HAND-RUN DRIVER whose SUBJECT is the substrate (David's ruling, 2026-08-10). "
    "It exists to prove a specific transport works end-to-end against a real broker; "
    "the substrate is not an incidental dependency, it is the thing under test. "
    "Verifying MQTT through a config-selected transport would let the check share a "
    "blind spot with the thing it checks. NAMED rather than addressed so the "
    "exemption survives the relocation out of `python/tests/` that has been discussed."
)

LEGITIMATE: dict[str, str] = {
    f"python/tests/render/{name}": _DRIVER_REASON
    for name in (
        "smoke_bind_intelligence_mqtt.py",
        "smoke_bindedit_intelligence_mqtt.py",
        "smoke_device_announce.py",
        "smoke_flauth_mqtt.py",
        "smoke_flow_discovery_mqtt.py",
        "smoke_headless_mqtt.py",
        "smoke_hmon_mqtt.py",
        "smoke_lottie_intelligence_mqtt.py",
        "smoke_m4_intelligence_mqtt.py",
    )
}

# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] class 2 — TRIAGED VIOLATIONS. This table must only ever SHRINK.
# --------------------------------------------------------------------------- #
# Seeded 2026-08-13 with the verified sweep. Every entry is a REAL violation with a
# recommended disposition; none is a judgement call left open. They are NOT ported
# here on purpose — four agents are live in this tree and the port is a separate
# change. Read the door-4 warning in the module docstring BEFORE converting any of
# them: replacing `MQTTTransport(...)` with `build({"type": "mqtt"})` moves a caught
# violation into an uncaught one and fixes nothing.
NOT_YET_CONVERTED: dict[str, str] = {
    "compose_projects/carousel/kiosk.py":
        "TODO: convert — the carousel HOST ROUTER hardcodes MQTT in `wire_bus()`. "
        "Every call it makes on the handle is on the `Transport` ABC "
        "(subscribe/start/stop), so nothing here needs MQTT; on a CAN-only or "
        "UART-only kiosk it would subscribe `_app/*/open` into the void with no "
        "error. DISPOSITION: take the transport spec from the kiosk's own config "
        "(it already parses `--broker`/`--port`) and build it through "
        "`transport_registry.build`, defaulting `type:` from env, not from source.",
    "compose_projects/carousel/scene_swap.py":
        "TODO: convert — the CLI that publishes `_app/<t>/open`. Same shape as "
        "kiosk.py and it MUST be converted with it: the two halves of one intent "
        "channel cannot disagree about the substrate. DISPOSITION: "
        "`source_bus.acquire_bus(label='scene_swap', transport_spec=...)`, which "
        "already routes through the registry and degrades loudly.",
    "python/composeio/composer/monitor.py":
        "TODO: convert — `build_monitor_app(mqtt='host:port')` bakes the substrate "
        "into the FUNCTION SIGNATURE, which is worse than baking it into a body: "
        "every caller now spells MQTT too. DISPOSITION: rename the parameter to a "
        "transport spec (`transport='mqtt://host:port'` or a dict) and hand it to "
        "`transport_registry.build`. `--mqtt` can stay as a CLI alias that fills "
        "`{'type': 'mqtt', …}` — the CLI is allowed to name a substrate, the "
        "LIBRARY is not.",
    "python/composeio/render/engine/headless.py":
        "TODO: convert — `main()`'s `--mqtt host:port` builds an `MQTTTransport` "
        "directly. It is a CLI, so naming a substrate on the command line is fine; "
        "constructing the class in the module is not. DISPOSITION: identical to "
        "monitor.py, and land them together — they share the `host:port` parse and "
        "the `headless-*` client_id convention.",
    "services/wake_word/detector.py":
        "TODO: convert — `main()` hardcodes `MQTTTransport` AND a non-standard "
        "default port (`MQTT_PORT` defaults to 1884, while the rest of the platform "
        "uses 1883); the whole block is inside a bare `except Exception` that "
        "degrades to 'running standalone', so a wrong substrate is indistinguishable "
        "from a down broker. DISPOSITION: `acquire_bus(label='wake-word')`, which "
        "reports an unavailable transport loudly instead of swallowing it. The port "
        "discrepancy is a separate finding and should be confirmed with David, not "
        "silently normalised.",
    "simulator/app.py":
        "TODO: convert — constructs `UARTTransport(port=UART_PORT, baud=UART_BAUD)` "
        "for the physical CH32V003 link. FOUND ONLY BECAUSE THIS GUARD IS "
        "SUBSTRATE-AGNOSTIC: it appears on no MQTT grep and was on no list. Lowest "
        "severity in the table — the FTL demo genuinely does talk to one board on "
        "one wire — but the wire belongs in config, not in a constructor call, and "
        "the module already swallows the failure in a bare `except`. DISPOSITION: "
        "`transport_registry.build({'type': 'uart', 'port': UART_PORT, 'baudrate': "
        "UART_BAUD})`; note the registry spells the key `baudrate`, not `baud`.",
    "python/composeio/composer/core.py":
        "TODO: convert — DOOR 3, not door 2: `self.app.connect_transport('mqtt', "
        "broker=…)` with the substrate as a string literal. The BROKER is already "
        "env-driven (`COMPOSEUX_MQTT_BROKER`) and the SUBSTRATE is not, which is the "
        "tell — somebody made the address configurable and never asked the same "
        "question of the wire. DISPOSITION: read the type from env beside the "
        "broker; `connect_transport` already accepts a non-literal first argument "
        "(`app.py:400` calls it that way), so this is a one-line change.",
    "services/intelligence_mcp/intelligence_mcp/server.py":
        "TODO: convert — DOOR 3. `app.connect_transport('mqtt', broker=config"
        "['mqtt_broker'], port=config['mqtt_port'])`. The config dict ALREADY exists "
        "and already carries the broker and port; it simply has no `transport_type` "
        "key. DISPOSITION: add one, default it to 'mqtt' IN THE CONFIG SCHEMA (where "
        "a default is a value an operator can override) rather than in the call site "
        "(where it is not).",

    # ----------------------------------------------------------------------- #
    # DOOR 4 — the spec-dict literals, triaged 2026-08-13 when the door closed.
    # ----------------------------------------------------------------------- #
    # These six are a WEAKER defect than doors 2 and 3 and the triage says so per
    # entry: each one goes through the waist, so the fix is to source one VALUE from
    # config rather than to rewire a call. Read them in this order — `source_bus.py`
    # is upstream of three of the others, and converting it may retire them.
    "python/composeio/source_bus.py":
        "TODO: convert — DOOR 4, and the most consequential of the six because it is "
        "in the WAIST ITSELF. TWO hits, and they are one defect: `mqtt_spec(url)` "
        "(line 107) returns `{'type': 'mqtt', ...}`, and `_selftest` (line 421) "
        "asserts that exact dict back. The second is a COMPARISON, not a "
        "construction — it chooses nothing on its own and is listed only because it "
        "encodes line 107's choice as the expected answer, so it must move in the "
        "SAME commit or the selftest fails the moment the real fix lands. "
        "DISPOSITION: the substrate is already in the caller's hand and is being "
        "thrown away — `parse_mqtt_url` is handed a URL with a SCHEME. Generalise to "
        "`spec_from_url(url)` reading `type:` off that scheme (`mqtt://`, `can://`, "
        "`uart://`), keep `mqtt_spec` as a thin alias so no caller breaks, and update "
        "the selftest to drive both schemes. Note this does NOT retire the module's "
        "`_spec_key` MQTT special case (line ~130), which keys the memo on "
        "`(broker, port)` for `type: mqtt` alone — that is a separate finding, "
        "documented at length by the two BSG publishers below, and it must be "
        "re-read before any of them changes its `type:`.",
    "python/composeio/adaptors/wled/senders.py":
        "TODO: convert — DOOR 4. `_transport_spec()` hardcodes `type: mqtt` while "
        "taking `broker`, `port`, `client_id` and the credentials from config: "
        "somebody made every OTHER field of that dict configurable and never asked "
        "the same question of the wire. WEAKEST OF THE SIX, and the reason is worth "
        "stating rather than hiding — WLED's published integration really is "
        "MQTT-shaped (`<topic>/api`), so `mqtt` is close to a device FACT here. It "
        "is still not a fact this method gets to assert, because a WLED reached "
        "through a UART or CAN gateway is precisely the [CXC-SOB-THESIS] case. "
        "DISPOSITION: add `transport_type` beside `broker` in the device config and "
        "default it to 'mqtt' IN THE SCHEMA — identical shape to the "
        "`intelligence_mcp/server.py` entry above, and land them together.",
    "python/scripts/dradis_scenario.py":
        "TODO: convert — DOOR 4, and the cheapest in the table. The constructor "
        "ALREADY accepts `transport_spec=` and the literal is only the fallback when "
        "a caller passes none, so the seam exists and is unused. DISPOSITION: this "
        "is a hand-run CLI driver and a CLI is allowed to NAME a substrate — add "
        "`--transport-type` (default from `COMPOSEIO_TRANSPORT_TYPE`, not from "
        "source) and pass it into the spec it already builds. No signature changes, "
        "no new dependency.",
}

# The one table the scan is compared against. Split above purely so a reader can tell
# "permanent by design" from "triaged debt" at a glance.
ALLOWED: dict[str, str] = {**LEGITIMATE, **NOT_YET_CONVERTED}


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".pytest_cache", ".mypy_cache", ".eggs", "build", "dist",
}
_SKIP_DIR_PREFIXES = ("wt-agent-",)

# NOTE: there is deliberately no `_EXCLUDED_TREES` here, not even an empty one.
# See the module docstring. The sibling guard keeps an empty tuple as a place for the
# standard to land; this file was written after that lesson and simply has no such
# construct, so a tree exclusion cannot be added without adding the mechanism first —
# which is a visible act in a diff, and the point.

_DRIVER_PREFIXES = ("smoke_", "drive_", "gen_")


def _is_driver(rel: str) -> bool:
    return rel.split("/")[-1].startswith(_DRIVER_PREFIXES)


def _is_test_file(rel: str) -> bool:
    """A pytest module may legitimately construct a transport to TEST it.

    Drivers are explicitly NOT tests even when they share an address with them.
    """
    if _is_driver(rel):
        return False
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_")


def _in_scope(rel: str) -> bool:
    """Exactly one verdict per file — scanned or excluded, never both.

    Leg 2 only works if every ALLOWED entry is reachable by the scan; an entry for
    an out-of-scope file could never rot. So the driver carve-out lives HERE,
    upstream of both legs, rather than as a special case inside either.
    """
    return not _is_test_file(rel)


def _scan() -> tuple[dict[str, list[tuple[int, str, str]]], dict[str, str], dict[str, ast.AST]]:
    """Every in-scope .py in the repo that CHOOSES a substrate.

    Returns the hits, the SOURCE and the parsed TREE, because both exemption rules
    are predicates over content: re-reading from disk inside a predicate would let
    the scan and the rule disagree about what the file says.
    """
    found: dict[str, list[tuple[int, str, str]]] = {}
    texts: dict[str, str] = {}
    trees: dict[str, ast.AST] = {}
    # Door 4's cheap prefilter is the QUOTED spec key: a dict literal with a constant
    # `type:` key cannot exist in a file whose text lacks `"type"` or `'type'`. Both
    # quotings, because Python has two and a prefilter that guesses is a silent skip.
    prefilter = (tuple(_GUARDED) + _DISPATCH_FUNCS
                 + (f'"{_SPEC_KEY}"', f"'{_SPEC_KEY}'"))
    for dirpath, dirs, files in os.walk(_REPO_ROOT):
        dirs[:] = [d for d in dirs
                   if d not in _SKIP_DIRS and not d.startswith(_SKIP_DIR_PREFIXES)]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if not _in_scope(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if not any(tok in text for tok in prefilter):   # cheap; AST decides
                continue
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                continue
            hits = _choices_in_tree(tree, _GUARDED)
            if hits:
                found[rel] = hits
                texts[rel] = text
                trees[rel] = tree
    return found, texts, trees


_FOUND, _TEXTS, _TREES = _scan()


def _families_of(rel: str) -> set[str]:
    return {fam for _ln, fam, _what in _FOUND[rel]}


# rel -> the stated rule-reason it is exempt. DERIVED, never hand-written.
_RULE_EXEMPT: dict[str, str] = {}
for _rel in _FOUND:
    _fams = _families_of(_rel)
    _why = (_dispatcher_exemption(_TREES[_rel], _fams)
            or _bridge_exemption(_rel, _TEXTS[_rel], _fams))
    if _why:
        _RULE_EXEMPT[_rel] = _why

_FIX = (
    "The fix is never 'add it to ALLOWED'. The substrate is CONFIGURED, not chosen:\n"
    "  * a transport comes from `composeio.transport_registry.build(spec)`, where\n"
    "    `spec['type']` arrives from the scene, the env or the CLI — mqtt / can /\n"
    "    uart / synthetic / replay are all registered there, and an unknown type\n"
    "    RAISES instead of silently degrading to local-only;\n"
    "  * a standalone process uses `composeio.source_bus.acquire_bus(label=...,\n"
    "    transport_spec=...)`, which memoises one transport per spec and says so on\n"
    "    stderr when the substrate is unreachable;\n"
    "  * an in-process component takes an INJECTED bus handle and constructs nothing.\n"
    "A CLI flag may name a substrate. A library module may not."
)


# --------------------------------------------------------------------------- #
# the ratchet — leg 1: nothing NEW may choose the substrate
# --------------------------------------------------------------------------- #
def test_no_unlisted_module_chooses_the_substrate():
    offenders = {rel: hits for rel, hits in _FOUND.items()
                 if rel not in ALLOWED and rel not in _RULE_EXEMPT}
    detail = "\n".join(
        f"  {rel}\n" + "\n".join(f"      line {ln}: {what}  [{fam}]"
                                 for ln, fam, what in hits)
        for rel, hits in sorted(offenders.items())
    )
    assert not offenders, (
        f"{len(offenders)} module(s) CHOOSE THE SUBSTRATE instead of taking it from "
        f"configuration ([CXC-SOB-THESIS]):\n{detail}\n\n"
        f"Naming a concrete Transport class — or passing a substrate NAME as a "
        f"string literal to a dispatch function — hardcodes the wire just as "
        f"completely as `import paho` does, while leaving the paho seam lint green. "
        f"The module cannot ride CAN or UART, and it is invisible to scope-based "
        f"selective uplink.\n\n{_FIX}"
    )


# --------------------------------------------------------------------------- #
# the ratchet — leg 2: the list may only SHRINK
# --------------------------------------------------------------------------- #
def test_allow_list_has_no_entry_that_no_longer_chooses_a_substrate():
    """The leg that makes this a ratchet instead of a permission slip.

    An entry that outlives its reason is the same silent-skip hazard as an unlisted
    bypass, one step removed: the table stops describing reality and the next reader
    trusts it. A file that has been CONVERTED (or deleted, or renamed) fails here
    until its entry is removed — which is also the ONLY thing keeping the driver
    list honest, since no content rule can.
    """
    stale = []
    for rel in sorted(ALLOWED):
        if rel in _FOUND:
            continue
        if not (_REPO_ROOT / rel).is_file():
            stale.append(f"  {rel} — NO LONGER EXISTS (deleted or renamed)")
        else:
            stale.append(f"  {rel} — CONVERTED: still on disk, no longer constructs a "
                         f"concrete Transport and no longer names a substrate literal")
    assert not stale, (
        "ALLOWED contains entries that are no longer true:\n" + "\n".join(stale)
        + "\n\nDELETE them from ALLOWED (in `LEGITIMATE` or `NOT_YET_CONVERTED`) in "
          "the SAME commit that converted/removed the file. This list must only ever "
          "shrink — an allow-list that never loses entries has stopped being a "
          "ratchet and become the documented architecture."
    )


# --------------------------------------------------------------------------- #
# hygiene on the table itself
# --------------------------------------------------------------------------- #
def test_every_allow_list_entry_carries_a_reason():
    """An exemption is a LISTED exemption ([CXC-SOB-EXEMPT]); an unexplained entry is
    a review reject — that rule is the only thing between a list and a silent skip."""
    for rel, why in ALLOWED.items():
        assert why and len(why) > 40, f"{rel} has no real rationale ({why!r})."


def test_the_two_classes_are_disjoint():
    both = sorted(set(LEGITIMATE) & set(NOT_YET_CONVERTED))
    assert not both, f"listed in BOTH classes: {both}"


def test_every_backlog_entry_is_marked_todo():
    """`NOT_YET_CONVERTED` is a work queue. `grep 'TODO: convert'` must find all of
    it, so the backlog is discoverable from outside this file."""
    missing = [rel for rel, why in NOT_YET_CONVERTED.items() if "TODO: convert" not in why]
    assert not missing, f"backlog entries missing a `TODO: convert` marker: {missing}"


def test_every_backlog_entry_names_a_disposition():
    """A backlog entry that says only 'this is wrong' makes the next reader re-derive
    the whole triage. The triage is the valuable half of this change; it must survive
    in the file, not only in a report nobody will find."""
    missing = [rel for rel, why in NOT_YET_CONVERTED.items() if "DISPOSITION:" not in why]
    assert not missing, f"backlog entries with no recommended disposition: {missing}"


def test_no_allow_list_entry_duplicates_a_rule():
    """A named entry for something a rule already covers is exactly the stale-prone
    thing the rules exist to delete. If both could apply, the list would keep looking
    load-bearing long after the rule made it redundant."""
    dupes = sorted(set(ALLOWED) & set(_RULE_EXEMPT))
    assert not dupes, (
        f"{dupes} are exempt BY RULE and also named in ALLOWED. Drop the ALLOWED "
        f"entry — a rule that needs a list beside it is not a rule.")


def test_a_listed_file_is_actually_scanned():
    """No double coverage: a file cannot be BOTH listed and excluded.

    An entry the scan can never see is an entry that can never rot — permanent
    permission wearing an allow-list's clothes.
    """
    unreachable = sorted(rel for rel in ALLOWED if not _in_scope(rel))
    assert not unreachable, (
        f"ALLOWED entries the scan EXCLUDES upstream, so leg 2 can never fire on "
        f"them: {unreachable}. Either scan them or drop the entry.")


# --------------------------------------------------------------------------- #
# the derived class set — proving it is derived, and that it agrees with the waist
# --------------------------------------------------------------------------- #
def test_the_guarded_class_set_is_derived_and_non_empty():
    """A scanner whose target set silently emptied would pass leg 1 forever.

    THE FLOOR IS THE POINT. The set is derived from the dispatchers, so a dispatcher
    that stopped offering MQTT would silently stop guarding it — a rot vector that a
    derived set has and a hand-written list does not. This assertion is the answer to
    that: the three WIRE substrates are named here, in a test, so removing one from
    the platform is a decision somebody has to make in a diff.
    """
    assert _CLASSES, "no concrete Transport classes derived — the derivation is broken"
    # The ABC itself must never be guarded: `Transport(...)` is not constructible and
    # `transport.py` does not match the `transport_*.py` glob. Pin both halves.
    assert "Transport" not in _CLASSES
    assert {"mqtt", "can", "uart"} <= _FAMILIES, (
        f"the three wire substrates must all be guarded; got {sorted(_FAMILIES)}")


def test_the_local_tier_is_not_a_substrate():
    """The narrowing the guard's first run forced, pinned so nobody re-widens it.

    `LocalTransport` is a real `class X(Transport)` in a real `transport_*.py`, so
    the class-only derivation caught `bus.py:89`'s `local_transport or
    LocalTransport()` and called it a substrate choice. It is not one: the local tier
    is direct function calls in one process, every bus has one, and no dispatcher
    will ever hand you one by name. Guarding it would have demanded that `bus.py`
    configure a thing with nothing to configure — a guard making a FALSE demand,
    which is how guards get disabled.

    Both halves are asserted: `local` is a class this file KNOWS about (so the
    exclusion is a decision, not an oversight) and is NOT one it guards.
    """
    assert "LocalTransport" in _CLASSES, "the derivation stopped seeing transport_local.py"
    assert "local" not in _FAMILIES, (
        "`local` has become guardable — some module now constructs a LocalTransport "
        "AND selects on the name 'local'. If the local tier really did become "
        "declarable that is a design change; if not, the selector heuristic has "
        "widened and is about to make a false demand.")
    # bus.py MOVED to the composebus kernel ([SPEC:CXC-COMPOSEBUS-KERNEL] §7). Assert on
    # the path that EXISTS and is IN SCOPE, or this line passes vacuously.
    bus_rel = "python/composebus/composebus/bus.py"
    assert (_REPO_ROOT / bus_rel).is_file(), f"{bus_rel} moved again — repoint this assertion"
    assert _in_scope(bus_rel), f"{bus_rel} fell out of the scan's scope"
    assert bus_rel not in _FOUND


def test_http_relay_is_guarded_though_the_registry_never_heard_of_it():
    """The other half of the narrowing, and a live finding in its own right.

    `transport_registry` registers mqtt/can/uart/synthetic/replay. `http_relay` is
    NOT among them — yet `ComposeApp.connect_transport("http_relay")` builds one, so
    a module CAN choose it and the guard must cover it. Deriving the guarded set from
    the registry alone would have left a real wire unguarded.

    Note what this also documents: the platform has TWO transport dispatchers with
    DIFFERENT type vocabularies. A scene declaring `type: http_relay` raises
    `UnknownTransportError` while `app.connect_transport("http_relay")` succeeds.
    That is a genuine seam defect, reported not fixed (2026-08-13) — this assertion
    goes red the day somebody reconciles them, which is the right time to re-read it.
    """
    assert "http_relay" in _FAMILIES, (
        "HttpRelayTransport is no longer reachable by name from any dispatcher — if "
        "the http_relay type was retired, delete this test with it")
    reg = _REPO_ROOT / "python/composeio/transport_registry.py"
    assert 'register("http_relay"' not in reg.read_text(encoding="utf-8"), (
        "http_relay is now registered — the two dispatchers' vocabularies have been "
        "reconciled. Good; rewrite this test to pin the reconciliation instead.")


def test_the_registry_builds_only_classes_this_guard_knows_about():
    """Two INDEPENDENT derivations of 'what is a concrete transport', cross-checked.

    This guard derives the set from `class X(Transport)` in `transport_*.py`. The
    registry derives it from what its factories actually construct. If they ever
    disagree, one of them is wrong and the disagreement is silent in both directions:
    a class the registry builds but this guard does not know is an UNGUARDED
    substrate, and that is the hole this whole file exists to close.
    """
    reg = _REPO_ROOT / "python/composeio/transport_registry.py"
    tree = ast.parse(reg.read_text(encoding="utf-8"), filename=str(reg))
    built = {_callee(n.func) for n in ast.walk(tree)
             if isinstance(n, ast.Call) and _callee(n.func).endswith("Transport")}
    unknown = sorted(built - set(_CLASSES))
    assert not unknown, (
        f"`transport_registry` constructs {unknown}, which this guard does not "
        f"recognise as concrete transports. Either the class moved out of "
        f"`python/composeio/transport_*.py` or it stopped subclassing `Transport` "
        f"— either way there is now a substrate nothing guards.")


def test_the_scan_actually_found_something():
    """A scanner that silently walks an empty tree passes leg 1 forever, and would
    make leg 2 fail for the wrong reason. Pin that it is really reading the repo."""
    assert _FOUND, "the substrate-choice scan found NOTHING — the walk is broken"


# --------------------------------------------------------------------------- #
# the scanner's own contract — AST beats grep, here too
# --------------------------------------------------------------------------- #
def test_a_docstring_example_is_not_a_construction():
    """The `config_server.py` case, encoded.

    `services/uart_mqtt_bridge/config_server.py:235` contains the text
    `CANTransport("can0")` inside a docstring, as the worked example of a seam — and
    the same docstring says 'No MQTT client is constructed, imported or reachable
    from this path'. A grep-seeded list would have carried that file as a substrate
    chooser forever, and leg 2 could never have retired the entry.
    """
    src = (
        '"""A seam.\n\n'
        '    srv = ConfigServer.for_transport(CANTransport("can0"), scenes, ["l1"])\n'
        '"""\n'
        'NOTE = "we used to call MQTTTransport(broker=x) here"\n'
        '# transport = MQTTTransport(broker="x")   (removed)\n'
        'import json\n'
    )
    assert substrate_choices_in(src) == []


def test_a_lazy_nested_construction_is_a_construction():
    """Every real construction in this repo is inside a function body, most of them
    behind a lazy import so a broker-free process never pays for paho. A line-anchored
    regex would need to guess at indentation; the AST walk does not care."""
    src = (
        "def wire():\n"
        "    from composeio.transport_mqtt import MQTTTransport\n"
        "    return MQTTTransport(broker='x')\n"
        "\n"
        "class K:\n"
        "    def go(self):\n"
        "        return CANTransport(channel='can0')\n"
    )
    assert substrate_choices_in(src) == [
        (3, "mqtt", "MQTTTransport(...)"),
        (7, "can", "CANTransport(...)"),
    ]


def test_a_literal_dispatch_is_a_choice_and_a_variable_one_is_not():
    """DOOR 3, both halves.

    `connect_transport("mqtt", …)` picks the wire in source. `connect_transport(
    t_type, …)` takes it from configuration — that is the CORRECT call, it is how
    `app.py:400` already drives the same function, and flagging it would make the
    guard demand the very thing it is asking for.
    """
    src = (
        'app.connect_transport("mqtt", broker=b)\n'
        'app.connect_transport(t_type, **kw)\n'
        'app.connect_transport(cfg["transport_type"], **kw)\n'
    )
    assert substrate_choices_in(src) == [(1, "mqtt", 'connect_transport("mqtt", ...)')]


# --------------------------------------------------------------------------- #
# DOOR 4 — the spec dict literal. Both halves: it FIRES, and it is SILENT.
# --------------------------------------------------------------------------- #
def test_a_planted_spec_dict_literal_is_caught():
    """Leg 1 of the firing test. Every guarded family, not just the one we fear."""
    for fam in sorted(_FAMILIES):
        src = f'spec = {{"{_SPEC_KEY}": "{fam}", "broker": b}}\n'
        assert substrate_choices_in(src) == [
            (1, fam, '{%r: "%s", ...}' % (_SPEC_KEY, fam))], fam


def test_a_configured_spec_dict_is_not_a_choice():
    """Leg 2 of the firing test, and the half that makes leg 1 worth anything.

    A rule that fires on everything passes its own firing test. These are the shapes
    the guard is ASKING callers to write — the `type:` arriving from a parameter, an
    env var, a config dict, a scene — plus the ordinary non-transport dict literals
    this repo is full of. Every one must be silent, or the guard's own prescribed fix
    would trip it and the next author would delete the rule instead of the defect.
    """
    quiet = (
        f'spec = {{"{_SPEC_KEY}": t_type, "broker": b}}\n'
        f'spec = {{"{_SPEC_KEY}": os.environ["COMPOSEIO_TRANSPORT_TYPE"]}}\n'
        f'spec = {{"{_SPEC_KEY}": cfg["transport_type"], "port": p}}\n'
        f'spec = {{**base, "broker": b}}\n'
        f'w = {{"{_SPEC_KEY}": "gauge", "min": 0}}\n'          # a WIDGET, not a wire
        f'p = {{"{_SPEC_KEY}": "print", "instance": i}}\n'     # a dry-run test double
        f'm = {{"kind": "mqtt"}}\n'                            # not the spec key
        f'n = {{"{_SPEC_KEY}": 3}}\n'                          # not even a string
    )
    assert substrate_choices_in(quiet) == []


def test_the_door_2_to_door_4_laundering_rewrite_is_caught():
    """THE REGRESSION THIS WHOLE COMMIT EXISTS TO PREVENT.

    The prescribed conversion for a door-2 site is "build it through the registry".
    Written carelessly, that rewrite turns a CAUGHT violation into an UNCAUGHT one:
    the module stays every bit as pinned to MQTT, the guard goes quiet, and leg 2
    then DELETES the backlog entry as "CONVERTED". A green suite would be reporting
    the opposite of what happened.

    So the exact rewrite is constructed here — the real `fleet.py:109` line, before
    and after — and both forms are required to be red. Three shapes, because the
    laundering has three tempting spellings and a consumer-keyed rule would miss the
    last two entirely.
    """
    before = 'transport = MQTTTransport(broker=broker, port=port)\n'
    assert substrate_choices_in(before) == [(1, "mqtt", "MQTTTransport(...)")]

    # 1. inline at the call — the obvious laundering.
    inline = (f'transport = build({{"{_SPEC_KEY}": "mqtt", "broker": broker, '
              f'"port": port}})\n')
    assert substrate_choices_in(inline), (
        "the door-2 -> door-4 rewrite went GREEN. The module is still hardcoded to "
        "MQTT and the guard has stopped saying so.")

    # 2. via a local variable — defeats any rule keyed on the CALLEE, which is why
    #    this guard is keyed on the dict literal instead.
    via_var = (f'spec = {{"{_SPEC_KEY}": "mqtt", "broker": broker}}\n'
               f'transport = build(spec)\n')
    assert substrate_choices_in(via_var) == [
        (1, "mqtt", '{%r: "mqtt", ...}' % _SPEC_KEY)]

    # 3. through a DIFFERENT consumer nobody listed — `acquire_bus`, a kwarg, and a
    #    module-level constant. Same value, so the same verdict.
    other_consumer = (f'_SPEC = {{"{_SPEC_KEY}": "mqtt", "broker": B}}\n'
                      f'bus = acquire_bus(label="x", transport_spec=_SPEC)\n')
    assert substrate_choices_in(other_consumer) == [
        (1, "mqtt", '{%r: "mqtt", ...}' % _SPEC_KEY)]

    # ...and the HONEST conversion — the type arriving from outside — stays green.
    honest = (f'spec = {{"{_SPEC_KEY}": args.transport_type, "broker": broker}}\n'
              f'transport = build(spec)\n')
    assert substrate_choices_in(honest) == []


def test_the_spec_key_is_read_out_of_the_registry():
    """Door 4's other derived half. `_SPEC_KEY` is not typed into this file.

    Pinned both ways: it is what the registry reads TODAY (so a rename cannot leave
    the guard watching a dead key), and `build()` still reads exactly one key off the
    spec (so the derivation has not silently started guessing among several).
    """
    assert _SPEC_KEY == "type", f"derived {_SPEC_KEY!r} — the registry renamed its key"
    src = _REGISTRY.read_text(encoding="utf-8")
    assert f'spec.get("{_SPEC_KEY}"' in src, (
        "`transport_registry.build` no longer reads the key this guard derived from "
        "it — the derivation and the registry have come apart")


def test_door_4_is_not_scoped_to_mqtt():
    """The substrate-agnostic promise, at the new door.

    Scoping door 4 to `"mqtt"` would reproduce one level up the mistake the paho
    guard just finished unpicking — and it is the generalisation that found
    `simulator/app.py`'s `UARTTransport` at door 2. So the door-4 family set IS the
    door-2 family set, asserted rather than assumed.
    """
    assert {"mqtt", "can", "uart"} <= _FAMILIES
    for fam in ("can", "uart"):
        assert substrate_choices_in(f'b = build({{"{_SPEC_KEY}": "{fam}"}})\n')
    # A family nothing can be handed by name is not a substrate — same narrowing the
    # local tier forced at door 2, applied at door 4 for free.
    assert "local" not in _FAMILIES
    assert substrate_choices_in(f'b = {{"{_SPEC_KEY}": "local"}}\n') == []


def test_a_spec_dict_in_a_docstring_is_not_a_choice():
    """AST beats grep at door 4 too — and this is not hypothetical. The door-4
    backlog entries in THIS file quote `{"type": "mqtt", ...}` several times while
    explaining the rule; a grep-based door 4 would flag its own guard."""
    src = (
        '"""How to declare a transport.\n\n'
        '    transports:\n'
        '      - {"type": "mqtt", "broker": "mqtt-broker.local"}\n'
        '"""\n'
        'NOTE = "we used to write {\'type\': \'can\'} here"\n'
        '# spec = {"type": "uart", "port": "/dev/ttyUSB0"}   (removed)\n'
    )
    assert substrate_choices_in(src) == []


def test_scope_rules_exclude_tests_and_nothing_else():
    assert not _in_scope("python/tests/render/test_carousel_kiosk.py")
    assert not _in_scope("tests/peer/test_transport_contract.py")
    assert not _in_scope("python/tests/render/conftest.py")
    assert not _in_scope("compose_projects/intelligence/publisher/test_channel_bridge.py")
    # ...and do NOT over-exclude: EVERY application tree stays in scope.
    assert _in_scope("compose_projects/carousel/kiosk.py")
    assert _in_scope("simulator/app.py")
    assert _in_scope("services/wake_word/detector.py")
    assert _in_scope("python/composeio/transport_registry.py")


def test_a_hand_run_driver_is_in_scope_wherever_it_lives():
    """A driver's exemption is NAMED, not addressed, so it survives a `git mv`."""
    assert _in_scope("python/tests/render/smoke_headless_mqtt.py")
    assert _in_scope("tools/smoke_headless_mqtt.py")        # after a hypothetical move
    assert _in_scope("python/tests/render/drive_flow.py")


# --------------------------------------------------------------------------- #
# RULE A — the dispatcher rule has been SEEN to refuse
# --------------------------------------------------------------------------- #
def test_two_substrates_without_a_selector_is_worse_not_better():
    """THE test that keeps RULE A a rule instead of a looser allow-list.

    'Constructs two substrates' on its own describes an app that has hardcoded BOTH,
    which is strictly worse than hardcoding one. The runtime SELECTOR is the half of
    the predicate that proves the module is a table rather than a choice. Pinned with
    synthetic sources so the rule has been SEEN to refuse, not merely believed to.
    """
    both_hardcoded = (
        "def wire():\n"
        "    a = MQTTTransport(broker='x')\n"
        "    b = UARTTransport(port='/dev/ttyUSB0')\n"
        "    return a, b\n"
    )
    tree = ast.parse(both_hardcoded)
    assert _dispatcher_exemption(tree, {"mqtt", "uart"}) == ""

    with_selector = (
        "def wire(kind):\n"
        "    if kind == 'mqtt':\n"
        "        return MQTTTransport(broker='x')\n"
        "    elif kind == 'uart':\n"
        "        return UARTTransport(port='/dev/ttyUSB0')\n"
    )
    assert _dispatcher_exemption(ast.parse(with_selector), {"mqtt", "uart"})

    # A selector for only SOME of the families it builds is not a dispatcher — the
    # unselected one is still a hardcoded choice hiding inside a table.
    partial = (
        "def wire(kind):\n"
        "    if kind == 'mqtt':\n"
        "        return MQTTTransport(broker='x')\n"
        "    return UARTTransport(port='/dev/ttyUSB0')\n"
    )
    assert _dispatcher_exemption(ast.parse(partial), {"mqtt", "uart"}) == ""

    # ONE substrate plus a selector for it is not a dispatcher either — a table with
    # a single row is a choice with extra steps.
    single = "def wire(kind):\n    if kind == 'mqtt':\n        return MQTTTransport(broker='x')\n"
    assert _dispatcher_exemption(ast.parse(single), {"mqtt"}) == ""

    # A literal compared to a literal is not a runtime selection.
    assert _runtime_selectors(ast.parse("x = ('mqtt' == 'mqtt')\n")) == set()


def test_the_dispatcher_rule_recognises_both_real_dispatchers():
    """The two modules in this repo that legitimately name every substrate, found
    from CONTENT rather than by name — `transport_registry` via its `register(...)`
    table and `ComposeApp.connect_transport` via its `== "<type>"` branches. If a
    third appears, the rule finds it with no human in the loop; if one of these stops
    dispatching, it stops being exempt on the same run."""
    for rel in ("python/composeio/transport_registry.py", "python/composeio/app.py"):
        assert rel in _FOUND, f"{rel} no longer constructs a transport — re-triage it"
        assert rel in _RULE_EXEMPT, (
            f"{rel} is a DISPATCHER but no rule recognises it. Either it stopped "
            f"selecting on a substrate name (then it has become a chooser and should "
            f"be red), or the selector forms in `_runtime_selectors` have drifted "
            f"from how this repo spells a dispatch.")
        assert _RULE_EXEMPT[rel].startswith("DISPATCHER"), _RULE_EXEMPT[rel]


# --------------------------------------------------------------------------- #
# RULE B — the bridge rule has been SEEN to refuse
# --------------------------------------------------------------------------- #
def test_a_bridge_directory_alone_does_not_exempt_anything():
    """`*_bridge/` is an ADDRESS, and this file's whole argument is that addresses
    are unfalsifiable. So the address is only half the predicate: a module that holds
    ONLY MQTT inside a bridge package is a bypass wearing a bridge's directory name
    and it must stay RED."""
    mqtt_only = "t = MQTTTransport(broker='x')\n"
    assert _bridge_exemption("services/can_mqtt_bridge/helper.py", mqtt_only, {"mqtt"}) == ""

    both = mqtt_only + "u = UARTTransport(port='/dev/ttyUSB0')\n"
    assert _bridge_exemption("services/uart_mqtt_bridge/helper.py", both, {"mqtt", "uart"})

    # A hand-rolled far side still counts — the strongest bridge verifiers re-derive
    # the far substrate from the kernel ABI instead of importing a library.
    handrolled = mqtt_only + "s = socket.socket(socket.AF_CAN, socket.CAN_RAW)\n"
    assert _bridge_exemption("services/can_mqtt_bridge/verify.py", handrolled, {"mqtt"})

    # The directory really is required: two substrates ANYWHERE ELSE is an app that
    # has hardcoded two substrates, which is worse, not better.
    assert _bridge_exemption("compose_projects/intelligence/publisher/chat.py", both, {"mqtt", "uart"}) == ""

    # A FILE named `*_bridge.py` buys nothing — only a package directory counts.
    assert _bridge_exemption("intelligence/publisher/channel_bridge.py", both,
                             {"mqtt", "uart"}) == ""


def test_the_bridge_rule_covers_the_real_bridge_from_content():
    """`services/uart_mqtt_bridge/bridge.py` constructs BOTH a `UARTTransport` and an
    `MQTTTransport` — the two-live-substrates shape that is a bridge's job
    description. It is dispositioned from its content by RULE B and appears in no
    list, so retiring the bridge retires the exemption automatically.

    It must ALSO not be caught by RULE A: a bridge holds both substrates at once and
    never selects between them, and if the dispatcher rule started claiming bridges
    the two rules would have stopped meaning different things.
    """
    rel = "services/uart_mqtt_bridge/bridge.py"
    assert rel in _FOUND, f"{rel} moved — re-check the bridge rule against its new home"
    assert _families_of(rel) >= {"mqtt", "uart"}, _FOUND[rel]
    assert _dispatcher_exemption(_TREES[rel], _families_of(rel)) == "", (
        "the bridge is being claimed by RULE A; the two rules describe different "
        "things and must not overlap")
    assert _RULE_EXEMPT.get(rel, "").startswith("BRIDGE"), _RULE_EXEMPT.get(rel)


def test_every_rule_exemption_states_its_reason():
    """[CXC-SOB-EXEMPT]: 'an exemption is a LISTED exemption'. A rule-based exemption
    has no list to read a reason off, so the rule GENERATES one and this test makes
    the whole set legible on demand (`-s` prints it). No file is exempted silently."""
    for rel, why in sorted(_RULE_EXEMPT.items()):
        print(f"RULE-EXEMPT {rel}\n    {why}")
        assert why and len(why) > 40, f"{rel} exempted with no stated reason"
