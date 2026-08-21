"""Home Assistant action bridge — call HA's REST service API to actuate a
smart outlet / light from a ComposeIO render surface.

This is the minimal, reusable HA cover the Skia tier lacked.  It does one
thing: POST ``/api/services/{domain}/{service}`` with ``{"entity_id": ...}``
and a bearer token, plus a GET ``/api/states/{entity_id}`` read.  The
domain is inferred from the entity_id (``switch.foo`` -> ``switch``);
``switch`` and ``light`` are the intended targets but any HA domain works.

Secrets by reference (never inlined, never logged):
    base_url  <- arg  or env ``HA_URL``
    token     <- arg  or env ``HA_TOKEN``
The token is scrubbed from every error path (``_scrub``) and is never
placed in an event ``summary`` or an exception message.

Testability: every network call goes through a ``transport`` seam —
``transport(method, url, *, headers, body, timeout) -> (status, text)`` —
so unit tests inject a MOCK and assert the exact request (URL, domain,
bearer header, body) with no live HA.

Runner tools (the fleetpower ``_ACTIONS`` pattern): ``ha_runner_tools()``
returns ``{name: (args, on_event) -> None}`` for ``homeassistant.toggle``
/ ``.turn_on`` / ``.turn_off`` / ``.status`` so a widget invokes them by
name over ``EngineContext.invoke``.  ``make_invoke(tools)`` wraps a tool
map into an off-thread ``invoke(tool, args, *, on_event)`` an app hands to
``RenderApp(invoke=...)``.
"""

from __future__ import annotations

import json
import os
import threading
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

# transport seam: (method, url, *, headers, body, timeout) -> (status, text)
Transport = Callable[..., Tuple[int, str]]


class HAError(RuntimeError):
    """A Home Assistant call failed (bad creds, HTTP error, transport)."""


# ---------------------------------------------------------------- credentials
def resolve_credentials(base_url: Optional[str] = None,
                        token: Optional[str] = None) -> Tuple[str, str]:
    """Resolve (base_url, token) from explicit args, else env HA_URL/HA_TOKEN.

    Raises HAError with a message that NEVER contains the token value.
    """
    base_url = base_url or os.environ.get("HA_URL")
    token = token or os.environ.get("HA_TOKEN")
    if not base_url:
        raise HAError("no Home Assistant base_url (set HA_URL or pass base_url)")
    if not token:
        raise HAError("no Home Assistant token (set HA_TOKEN or pass token)")
    return base_url.rstrip("/"), token


def _domain_of(entity_id: str) -> str:
    """switch.red_alert_lights -> 'switch'.  Requires a dotted entity_id."""
    if not entity_id or "." not in entity_id:
        raise HAError(f"invalid entity_id {entity_id!r} (want e.g. 'switch.foo')")
    return entity_id.split(".", 1)[0]


def _scrub(text: str, token: str) -> str:
    """Belt-and-suspenders: strip the token from any string before it is
    logged or raised.  The token should never reach here, but if a lower
    layer echoes a header we redact it."""
    return text.replace(token, "***") if token else text


# ---------------------------------------------------------------- transport
def _default_transport(method: str, url: str, *, headers: Dict[str, str],
                       body: Optional[dict], timeout: float) -> Tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        status = getattr(r, "status", None) or r.getcode()
        raw = r.read().decode()
    return int(status), raw


def _request(method: str, path: str, *, base_url: Optional[str], token: Optional[str],
             body: Optional[dict], timeout: float,
             transport: Optional[Transport]) -> Any:
    base_url, token = resolve_credentials(base_url, token)
    url = f"{base_url}{path}"
    headers = {"Authorization": f"Bearer {token}",
               "Content-Type": "application/json"}
    tx = transport or _default_transport
    try:
        status, text = tx(method, url, headers=headers, body=body, timeout=timeout)
    except HAError:
        raise
    except Exception as exc:                       # transport/urllib failure
        # `from None` drops the chained request object (which holds the auth
        # header) so the token can never surface via __cause__.
        raise HAError(_scrub(f"HA request to {path} failed: {exc}", token)) from None
    if not (200 <= status < 300):
        raise HAError(f"HA request to {path} failed: HTTP {status}")
    return json.loads(text) if text else None


# ---------------------------------------------------------------- pure API
def ha_call(entity_id: str, service: str, *, base_url: Optional[str] = None,
            token: Optional[str] = None, timeout: float = 5,
            transport: Optional[Transport] = None) -> Any:
    """POST /api/services/{domain}/{service} with {"entity_id": entity_id}.

    ``domain`` is inferred from ``entity_id``.  Returns the parsed JSON body
    (HA returns the list of changed states) or None.
    """
    domain = _domain_of(entity_id)
    return _request("POST", f"/api/services/{domain}/{service}",
                    base_url=base_url, token=token, body={"entity_id": entity_id},
                    timeout=timeout, transport=transport)


def turn_on(entity_id: str, **kw: Any) -> Any:
    return ha_call(entity_id, "turn_on", **kw)


def turn_off(entity_id: str, **kw: Any) -> Any:
    return ha_call(entity_id, "turn_off", **kw)


def toggle(entity_id: str, **kw: Any) -> Any:
    return ha_call(entity_id, "toggle", **kw)


def get_state(entity_id: str, *, base_url: Optional[str] = None,
              token: Optional[str] = None, timeout: float = 5,
              transport: Optional[Transport] = None) -> dict:
    """GET /api/states/{entity_id} -> the state dict (has 'state', etc.)."""
    if not entity_id or "." not in entity_id:
        raise HAError(f"invalid entity_id {entity_id!r} (want e.g. 'switch.foo')")
    return _request("GET", f"/api/states/{entity_id}", base_url=base_url,
                    token=token, body=None, timeout=timeout,
                    transport=transport) or {}


# ---------------------------------------------------------------- runner tools
def ha_runner_tools(*, base_url: Optional[str] = None, token: Optional[str] = None,
                    timeout: float = 5,
                    transport: Optional[Transport] = None
                    ) -> Dict[str, Callable[[dict, Callable], None]]:
    """Build the ``homeassistant.*`` runner-tool map (fleetpower _ACTIONS
    shape): each value is ``run(args, on_event) -> None`` and emits
    ``{"kind": "result", ...}`` (and a progress event).  ``args`` carries the
    target under ``entity`` | ``entity_id`` | ``target`` — so both a
    power_toggle trigger (``entity``) and a plug-style tap (``target``) work.
    """
    def _entity(args: dict) -> Optional[str]:
        return args.get("entity") or args.get("entity_id") or args.get("target")

    def _service_tool(service: str):
        def run(args: dict, on_event: Callable) -> None:
            entity = _entity(args)
            if not entity:
                on_event({"kind": "result", "ok": False,
                          "summary": "no entity_id specified"})
                return
            on_event({"kind": "progress", "pct": 30, "phase": service})
            try:
                ha_call(entity, service, base_url=base_url, token=token,
                        timeout=timeout, transport=transport)
                on_event({"kind": "result", "ok": True,
                          "summary": f"{entity} -> {service}"})
            except Exception as exc:               # HAError message is scrubbed
                on_event({"kind": "result", "ok": False,
                          "summary": f"HA {service} failed: {exc}"})
        return run

    def status(args: dict, on_event: Callable) -> None:
        entity = _entity(args)
        if not entity:
            on_event({"kind": "result", "ok": False,
                      "summary": "no entity_id specified"})
            return
        try:
            st = get_state(entity, base_url=base_url, token=token,
                           timeout=timeout, transport=transport).get("state")
            on_event({"kind": "result", "ok": True, "state": st,
                      "summary": f"{entity}: {st}"})
        except Exception as exc:
            on_event({"kind": "result", "ok": False,
                      "summary": f"HA status failed: {exc}"})

    return {
        "homeassistant.toggle": _service_tool("toggle"),
        "homeassistant.turn_on": _service_tool("turn_on"),
        "homeassistant.turn_off": _service_tool("turn_off"),
        "homeassistant.status": status,
    }


def make_invoke(tools: Dict[str, Callable[[dict, Callable], None]], *,
                threaded: bool = True) -> Callable:
    """Wrap a tool map into an ``invoke(tool, args, *, on_event)`` matching
    ``EngineContext.invoke`` — runs the tool OFF the render thread (like
    bringup.invoke_action) and REFUSES unregistered names."""
    def invoke(tool: str, args: dict, *, on_event: Callable) -> None:
        fn = tools.get(tool)
        if fn is None:
            on_event({"kind": "result", "ok": False,
                      "summary": f"REFUSED: '{tool}' not registered"})
            return
        if threaded:
            threading.Thread(target=lambda: fn(dict(args), on_event),
                             daemon=True).start()
        else:
            fn(dict(args), on_event)
    return invoke
