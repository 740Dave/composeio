"""Minimal Elegoo SDCP status client — reimplemented THIN from the Haven
reference (`~/haven/print_3d_service/sdcp_client.py`). Status + discovery only;
NO print submission (that is v2 — it needs slicing + file upload).

Protocol (SDCP v3):
  * discovery : UDP broadcast 'M99999' to :3000 (optional; we have the IP).
  * commands  : WebSocket ws://{ip}/websocket, JSON messages.
  * status    : Cmd 0.

Elegoo firmware varies in envelope shape and some units PUSH a status frame on
connect without a request. This client is deliberately TOLERANT: it sends the
Cmd-0 request AND accepts the first status-looking frame, and reports the raw
frame so v1 is honest about whatever the printer actually said.

Uses the asyncio `websockets` library (installed); exposes a synchronous
`get_status()` for the service's threaded supervisor via asyncio.run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import uuid

logger = logging.getLogger("print_services.sdcp")

CMD_STATUS = 0

# FDM + resin state codes (superset from the reference).
PRINTER_STATES = {
    0: "idle", 1: "printing", 2: "paused", 3: "stopping",
    4: "error", 5: "heating", 6: "cooling", 7: "filament_change",
}


class SDCPError(Exception):
    """SDCP connect/read failed."""


def _now() -> float:
    import time as _t
    return _t.time()


def _has_status_payload(msg: dict) -> bool:
    """True only for a frame that actually CARRIES status (temps / CurrentStatus),
    not a bare command Ack. The Centauri pushes {"Status":{"CurrentStatus":[..],
    "TempOfNozzle":..}}; we require that substance so we do not return the echo."""
    if not isinstance(msg, dict):
        return False
    status = msg.get("Status") or msg.get("status")
    if isinstance(status, dict) and ("CurrentStatus" in status or "PrintStatus" in status
                                     or "TempOfNozzle" in status):
        return True
    data = msg.get("Data") or msg.get("data") or {}
    if isinstance(data, dict):
        inner = data.get("Status") or data.get("status")
        if isinstance(inner, dict) and ("CurrentStatus" in inner or "TempOfNozzle" in inner):
            return True
    return False


def _looks_like_status(msg: dict) -> bool:
    if not isinstance(msg, dict):
        return False
    if "Status" in msg or "status" in msg:
        return True
    data = msg.get("Data") or msg.get("data") or {}
    if isinstance(data, dict):
        if "Status" in data or "status" in data:
            return True
        # reference envelope: {cmd:0, data:{state:..}}
        if msg.get("cmd") == CMD_STATUS or data.get("Cmd") == CMD_STATUS:
            return True
        if "state" in data or "CurrentStatus" in data:
            return True
    topic = str(msg.get("Topic", "")).lower()
    return "status" in topic


def _extract_state(msg: dict) -> tuple[int | None, str]:
    """Best-effort pull of a numeric state code + name from a heterogeneous
    SDCP status frame. Returns (code_or_None, name)."""
    data = msg.get("Data") or msg.get("data") or msg
    status = data.get("Status") if isinstance(data, dict) else None
    candidates = []
    if isinstance(status, dict):
        candidates += [status.get("CurrentStatus"), status.get("PrintStatus"),
                       status.get("state")]
    if isinstance(data, dict):
        candidates += [data.get("state"), data.get("CurrentStatus")]
    for c in candidates:
        if isinstance(c, list) and c:
            c = c[0]
        if isinstance(c, int):
            return c, PRINTER_STATES.get(c, f"unknown({c})")
    return None, "unknown"


async def _get_status_async(ip: str, timeout: float) -> dict:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover
        raise SDCPError("websockets library not installed") from exc

    ws_url = f"ws://{ip}/websocket"
    try:
        async with websockets.connect(ws_url, ping_interval=None,
                                       open_timeout=timeout,
                                       close_timeout=2) as ws:
            # SDCP-proper request (what the Centauri Carbon firmware answers).
            # We also send the reference's lowercase shape for firmware variants
            # that speak it; a printer ignores the one it does not understand.
            request_id = uuid.uuid4().hex
            proper = {"Id": "", "Data": {"Cmd": CMD_STATUS, "Data": {},
                                          "RequestID": request_id, "MainboardID": "",
                                          "TimeStamp": int(_now()), "From": 0},
                      "Topic": "sdcp/request/"}
            legacy = {"cmd": CMD_STATUS, "data": {}, "requestID": request_id}
            for req in (proper, legacy):
                try:
                    await ws.send(json.dumps(req))
                except Exception:  # noqa: BLE001 — some units only push
                    pass

            best = None  # a bare Ack response is a fallback; a Status push wins.
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, remaining))
                except asyncio.TimeoutError:
                    break
                if raw in ("pong", "ping"):
                    continue
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if _has_status_payload(msg):
                    code, name = _extract_state(msg)
                    return {"ok": True, "state": code, "state_name": name,
                            "raw": msg, "ws_url": ws_url}
                if _looks_like_status(msg) and best is None:
                    best = msg  # ack/echo — keep as fallback, keep waiting for a push
            if best is not None:
                code, name = _extract_state(best)
                return {"ok": True, "state": code, "state_name": name,
                        "raw": best, "ws_url": ws_url}
            raise SDCPError(f"no status frame within {timeout}s")
    except SDCPError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise SDCPError(f"SDCP connect/read failed: {exc}") from exc


def get_status(ip: str, timeout: float = 10.0) -> dict:
    """Synchronous one-shot status read. Raises SDCPError on failure."""
    return asyncio.run(_get_status_async(ip, timeout))


def discover(timeout: float = 3.0) -> list[dict]:
    """UDP broadcast 'M99999' to :3000; collect JSON responses. Optional — the
    service is configured with a static IP, this is a convenience/verification."""
    printers = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.5)
    try:
        sock.sendto(b"M99999", ("255.255.255.255", 3000))
        import time as _t
        deadline = _t.time() + timeout
        while _t.time() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            try:
                info = json.loads(data.decode("utf-8"))
                info["ip"] = addr[0]
                printers.append(info)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
    finally:
        sock.close()
    return printers
