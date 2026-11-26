#!/usr/bin/env python3
"""transport_registry — `type:` -> Transport, so a scene picks its substrate.

THE THESIS THIS SERVES (David, 2026-08-08): "ComposeBus might for some devices be
instantiated as MQTT, and for some it might ride on CANBUS. But in both cases the
ComposeIO/UX app shouldn't have to care how ComposeBus is instantiated — it
should just listen to / publish to ComposeBus."

Everything ABOVE the bus already honors that: widgets bind namespaces, apps call
`publish_state`, `Transport` is a proper ABC, and `transport_contract.py` already
models a CAN far-side as `FarSide.MAPPED_DEVICE_BUS` with declared degradation.

WHAT WAS BROKEN — measured 2026-08-08 by a substitution test. `runtime._build_bus`
hardcoded the one substrate:

    for t in self.comp.transports:
        if isinstance(t, dict) and t.get("type") == "mqtt":
            ...MQTTTransport(...)

so a scene could DECLARE `type: can` (or `uart`, or `synthetic`) and the runtime
would drop it on the floor, leaving `bus._global = None`. The app then ran
local-only — no exception, no log, no clue. Result:

    type: mqtt       -> MQTTTransport
    type: can        -> None, IGNORED, no error
    type: synthetic  -> None, IGNORED, no error
    type: uart       -> None, IGNORED, no error

The waist was sound and merely unreachable. That is the same silent-failure shape
as everything else this phase has been unpicking: a declaration that looks obeyed
and is not.

TWO RULES HERE, both about that failure:
  1. An UNKNOWN `type:` RAISES. A scene asking for a substrate we cannot provide
     must not quietly degrade to local-only — a mis-typed `type: mqqt` should be
     a startup error, not a mystery about missing telemetry.
  2. A KNOWN type whose backend is unavailable (no broker, no CAN interface,
     pyserial not installed) degrades LOUDLY and returns None, because a
     down broker genuinely is not a broken app. The distinction is
     "we don't know what you asked for" (bug) vs "we know, and it isn't
     reachable right now" (environment).

BRIDGES ARE COMPLEMENTARY, NOT ALTERNATIVES (David, 2026-08-08): "There is a
legitimate need to bridge between transports, e.g. MQTT <> CANBUS messages when
both are running." A Transport lets an app speak ONE substrate; a bridge
(`services/can_mqtt_bridge`) translates between TWO live substrates. Registering
CAN here does not retire that bridge and is not meant to.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# type-name -> factory(spec: dict, app_id: str) -> Transport | None
_FACTORIES: Dict[str, Callable[[dict, str], Any]] = {}


class UnknownTransportError(ValueError):
    """A scene asked for a `type:` we have no factory for.

    Deliberately fatal. The alternative — silently running local-only — is the
    defect this registry exists to remove.
    """


class TransportPolicyError(ValueError):
    """A construction-time POLICY/CONFIG refusal on a syntactically-valid,
    REACHABLE declaration — structurally the same class as
    ``UnknownTransportError``, NOT an environment fact.

    ``build()`` RE-RAISES this, never degrading it to a silent ``None``+log. The
    None+log degrade is reserved for a KNOWN type whose BACKEND is genuinely
    unreachable (broker down, no CAN iface, pyserial absent) — "we know what you
    asked for, and it isn't reachable right now." A policy refusal is the OTHER
    axis: "your config is reachable and well-formed, but we refuse it" (e.g. a
    world-facing bind with no auth). Silently degrading such a refusal to None
    would reintroduce the exact looks-obeyed-but-isn't defect this registry
    exists to remove — one layer down, in the factory rather than the dispatch.

    Subclass this for any construction-time refusal a factory raises; ``build()``
    re-raises the whole family, mirroring its ``UnknownTransportError`` rule."""


def register(type_name: str, factory: Callable[[dict, str], Any]) -> None:
    """Register `factory` for scenes declaring `type: <type_name>`."""
    _FACTORIES[str(type_name).strip().lower()] = factory


def known_types() -> List[str]:
    return sorted(_FACTORIES)


def build(spec: dict, *, app_id: str = "composeio") -> Optional[Any]:
    """Build the Transport for one `transports:` entry.

    Raises UnknownTransportError on an unrecognized `type:`. Returns None when a
    KNOWN transport's backend is simply unreachable (broker down, no CAN iface,
    pyserial absent) — that is an environment condition, and the caller logs it.
    """
    if not isinstance(spec, dict):
        raise UnknownTransportError(f"transport entry must be a mapping, got {spec!r}")
    ttype = str(spec.get("type", "")).strip().lower()
    if not ttype:
        raise UnknownTransportError(
            f"transport entry has no `type:` — {spec!r}. Known types: {known_types()}")
    factory = _FACTORIES.get(ttype)
    if factory is None:
        raise UnknownTransportError(
            f"unknown transport type {ttype!r}. Known types: {known_types()}. "
            "A scene that declares a substrate we cannot build must fail LOUDLY — "
            "silently running local-only is the defect this registry removes.")
    try:
        return factory(spec, app_id)
    except (UnknownTransportError, TransportPolicyError):
        # A policy/config refusal is NOT an environment fact — re-raise it,
        # exactly as an unknown `type:` is re-raised. Degrading it to None+log
        # would silently bypass a deliberate, loud-by-design refusal (e.g. a
        # world-facing bind with no auth, `UnsafeBindError`).
        raise
    except Exception as exc:                      # noqa: BLE001 — environment, not a bug
        logger.warning("transport %r unavailable (app runs local-only): %s", ttype, exc)
        return None


# --------------------------------------------------------------------------- #
# built-in factories — imported LAZILY so one missing optional dependency
# (paho, pyserial, python-can) never breaks the others.
# --------------------------------------------------------------------------- #
def _mqtt(spec: dict, app_id: str):
    from composeio.transport_mqtt import MQTTTransport
    # UNIQUE client-id per render process ([SPEC:CXC-IC-SINGLETON] collision
    # class): MQTTTransport defaults to a static "composeio" id, so two windows
    # with --transports would connect with the SAME id and the broker kicks each
    # in turn (rc=7 flap -> "BUS OFFLINE" toggling). Key on app_id + pid.
    #
    # `username`/`password` were NOT passed through until 2026-08-11, and that
    # gap was load-bearing: `MQTTTransport` has always accepted them, but a spec
    # could not REACH them, so the WLED adaptor kept building its own paho client
    # to get per-device credentials onto the wire ([CXC-SOB-THESIS]'s last
    # holdout in `python/composeio/`). The capability existed; only the seam was
    # missing. Secrets stay BY REFERENCE — the spec carries whatever the scene
    # resolved, this function never reads a credential store.
    #
    # `client_id` is overridable because a device adaptor legitimately wants a
    # recognisable broker-side identity; the pid suffix is retained either way so
    # two processes can never collide.
    #
    # THE AUTH KWARGS ARE PASSED ONLY WHEN THE SPEC CARRIES THEM, and that is not
    # cosmetic: this factory is routinely handed a TEST DOUBLE's constructor
    # (`test_launch_bar.py::_StubMQTT`), and passing a brand-new keyword
    # unconditionally turned an unrelated suite red with
    # `unexpected keyword argument 'username'`. An additive seam that changes the
    # call signature for every existing caller is not additive. Conditional
    # kwargs keep the credential-free path byte-for-byte what it was.
    kwargs = {}
    if spec.get("username"):
        kwargs["username"] = spec.get("username")
        kwargs["password"] = spec.get("password")
    # [DP-CIO-6] A scene does not name the transport's host. An absent `broker:`
    # falls back to the host's COMPOSEIO_MQTT_BROKER (supplied by the overlay,
    # [CXC-SCL-SCRUB-OVERLAY-PARITY]), then to the neutral local default. Read at
    # build time, not import, so the env in force when the scene starts governs.
    return MQTTTransport(
        broker=spec.get("broker") or os.environ.get("COMPOSEIO_MQTT_BROKER") or "localhost",
        port=int(spec.get("port") or os.environ.get("COMPOSEIO_MQTT_PORT") or 1883),
        client_id=f"{spec.get('client_id', app_id)}-{os.getpid()}",
        **kwargs)


def _can(spec: dict, app_id: str):
    from composeio.transport_can import CANTransport
    return CANTransport(
        channel=spec.get("channel", "loopback"),
        bitrate=int(spec.get("bitrate", 500000)),
        backend=spec.get("backend"),
        app_id=app_id)


def _uart(spec: dict, app_id: str):
    from composeio.transport_uart import UARTTransport
    # `baud=`, NOT `baudrate=`. This factory passed `baudrate=` from the day it
    # was registered, so `type: uart` NEVER constructed: it raised TypeError,
    # build() caught it as "backend unavailable", and the app ran local-only
    # while REPORTING an environment problem it did not have. That is the exact
    # failure this registry was written to eliminate — a declaration that looks
    # obeyed and is not — reintroduced one layer down, in the factory rather
    # than the dispatch. Found 2026-08-12 while building the UART bridge; the
    # scene YAML key stays `baudrate:` because that is the published spelling.
    return UARTTransport(port=spec.get("port", "/dev/null"),
                         baud=int(spec.get("baudrate", 115200)))


def _synthetic(spec: dict, app_id: str):
    from composeio.transport_synthetic import SyntheticTransport
    return SyntheticTransport(spec.get("config") or spec.get("gen"))


def _replay(spec: dict, app_id: str):
    from composeio.transport_replay import ReplayTransport
    return ReplayTransport(spec.get("capture") or spec.get("file"))


def _ws(spec: dict, app_id: str):
    # THE ONE LISTEN-ROLE FACTORY. Every sibling above DIALS a fabric that
    # already exists (broker, CAN iface, serial port); `type: ws` has no fabric
    # to dial — it OPENS A LISTEN SOCKET and each accepted browser tab becomes
    # one bus peer ([SPEC:CXC-WS] [CXC-WS-CONNECTOR-NOT-PLUGIN]). The declared-
    # config idiom is unchanged; only the socket's direction inverts. Imported
    # LAZILY like the others so a missing `websockets` never breaks the rest.
    from composeio.transport_ws import _ws as _ws_factory
    return _ws_factory(spec, app_id)


def _mcp(spec: dict, app_id: str):
    # THE AGENT-FACING LISTEN-ROLE FACTORY. Sibling of `type: ws` one layer up:
    # where `ws` makes a browser a bus peer carrying raw namespace traffic,
    # `mcp` makes an Intelligence a bus peer carrying TOOL CALLS — ONE
    # aggregating umbrella per ComposeServer ([SPEC:CXC-MCP]
    # [CXC-MCP-CONNECTOR-NOT-PLUGIN]). Imported LAZILY like the others so a
    # missing MCP SDK never breaks the rest — and, crucially, `transport_mcp`
    # itself imports the SDK only at `start()`, so even building this factory
    # (and `known_types()` reporting `mcp`) works with the SDK absent.
    from composeio.transport_mcp import _mcp as _mcp_factory
    return _mcp_factory(spec, app_id)


register("mqtt", _mqtt)
register("can", _can)
register("uart", _uart)
register("synthetic", _synthetic)
register("replay", _replay)
register("ws", _ws)
register("mcp", _mcp)


def _selftest() -> int:
    fails = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    check("mqtt" in known_types(), "(1) mqtt registered")
    check("can" in known_types(), "(2) can registered")
    check("uart" in known_types(), "(3) uart registered")
    check("synthetic" in known_types(), "(4) synthetic registered")

    # (5) THE LOAD-BEARING ONE: an unknown type must RAISE, not return None.
    for bad in ({"type": "mqqt"}, {"type": "carrier_pigeon"}, {"type": ""}, {}):
        try:
            build(bad)
            fails.append(f"(5) unknown type silently accepted: {bad!r}")
        except UnknownTransportError as exc:
            check("Known types" in str(exc), f"(5b) error should list known types: {exc}")

    # (6) a non-mapping entry is also a loud error.
    try:
        build("mqtt")  # type: ignore[arg-type]
        fails.append("(6) a non-mapping transport entry was accepted")
    except UnknownTransportError:
        pass

    # (7) a KNOWN type with an unreachable backend degrades to None, not a raise.
    register("_boom", lambda s, a: (_ for _ in ()).throw(OSError("no hardware")))
    check(build({"type": "_boom"}) is None,
          "(7) an unreachable KNOWN backend should degrade to None, not raise")
    _FACTORIES.pop("_boom", None)

    # (8) CAN builds with NO hardware — the loopback default.
    t = build({"type": "can", "channel": "loopback"})
    check(t is not None, "(8) CAN must build hardware-free on the loopback backend")
    check(type(t).__name__ == "CANTransport", f"(8b) wrong type: {type(t).__name__}")

    if fails:
        for m in fails:
            print(f"FAIL {m}")
        return 1
    print(f"PASS: transport_registry selftest (types: {', '.join(known_types())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
