# print_services v1

Bus-native ComposeBus service that puts the HP LaserJet and the Elegoo 3D
printer on the bus. Bare-Python, standalone deploy unit (like `bus_storage` /
`document_store`). DP-CIO-6: transport is an implementation detail — this
service speaks the same ComposeIO wire envelope every other peer speaks.

## Run
```
# services/ on PYTHONPATH
COMPOSEIO_MQTT_URL=mqtt://127.0.0.1:1883 PYTHONPATH=. python -m print_services
# or
python services/print_services/server.py
```

## Env config
| var | default | meaning |
|---|---|---|
| `COMPOSEIO_MQTT_URL` | `mqtt://127.0.0.1:1883` | broker (local broker for on-buildhost use) |
| `PRINT_SERVICES_PORT` | `8425` | HTTP `/api/v1/status` port (8420-8423 taken on buildhost) |
| `PRINT_SERVICES_HOST` | `0.0.0.0` | HTTP bind host |
| `PRINT_SERVICES_DAILY_CAP` | `100` | per-period page cap (print-freely, cap-safely) |
| `PRINT_SERVICES_HEARTBEAT` | `30` | registry heartbeat / status refresh seconds |
| `PRINT_SERVICES_HP_IP` | `192.0.2.77` | HP printer IP |
| `PRINT_SERVICES_ELEGOO_IP` | `192.0.2.34` | Elegoo printer IP |
| `COMPOSEIO_GIT_SHA` | (git) | build stamp for `/api/v1/status` |

## Bus surface (namespaces under `print_services/`)
- `print_services/registry/<id>` — RETAINED per backend, republished on the
  heartbeat as an age contract; tombstoned (zero-length retained) on graceful
  shutdown. `{id, kind, width, color, graphics, dpi, content_types, status_ns,
  backend, heartbeat_ts}`.
- `print_services/<id>/submit` — inbound job:
  `{job_id?, content_type: text|markdown|html|svg|pdf, content: <str|base64 pdf>,
  options?:{copies?}}`.
- `print_services/<id>/result` — `{job_id, status: submitted|done|error,
  detail?, pages?}` (transient EVENT). Last 50 kept in memory.
- `print_services/<id>/status` — RETAINED `{state: ready|busy|paper_out|offline, ts, ...}`.

On the wire a namespace maps to `composeio/{scope}/{namespace}`; STATE is
retained (QoS 1) with its payload in `val`, EVENT is transient (QoS 0) with its
payload in `data` under an `evt` type tag. This is the platform envelope
produced by `python/composeio/transport.py` (`BusMessage.to_json`) — reused
through the transport seam, not hand-copied (see `bus.py`).

## Backends
- **hp** (HP Color LaserJet MFP M476dn) — FULL path. Driverless
  IPP-Everywhere, **NO CUPS**: IPP straight to `ipp://<ip>:631/ipp/print`
  (fallback `ipps://<ip>:443/ipp/print`). Content pipeline: text -> a minimal
  zero-dependency PDF; pdf -> passthrough; markdown/html/svg -> PDF via
  weasyprint IF importable, else a loud `error` result (never a crash — text+pdf
  always work). `ipp.py` is a stdlib IPP encoder/parser (no `pyipp` dependency).
- **elegoo3d** (Elegoo Centauri Carbon) — STATUS-ONLY v1. Minimal SDCP status
  client over `ws://<ip>/websocket` (`sdcp.py`, asyncio `websockets`). Submit
  returns an honest `error`: 3D submission is v2 (needs slicing/upload).

## Dependencies on buildhost
- `composeio` (the `python/composeio` package) — REQUIRED. The bus is reached
  through `composeio.source_bus.acquire_bus` over the transport seam; this
  service imports NO `paho` itself (DP-CIO-6). `paho-mqtt` is still needed
  transitively because the MQTT transport (`composeio.transport_mqtt`) uses it —
  it is just no longer a DIRECT dependency of print_services.
- `websockets` — REQUIRED for the Elegoo SDCP status client.
- `weasyprint` — OPTIONAL; only the markdown/html/svg HP path needs it. Absent ->
  those content types return a loud `error`; text + pdf are unaffected.
- `pyipp` — NOT needed (IPP is hand-rolled in `ipp.py`, stdlib only).
- `websocket-client` — NOT needed (we use asyncio `websockets`).

## Files
- `server.py` — main: bus wiring, backends, heartbeat, `/api/v1/status`, supervision.
- `bus.py` — ComposeBus face over the transport seam (`composeio.source_bus`);
  no paho, no hand-copied envelope.
- `ipp.py` — stdlib IPP client (Get-Printer-Attributes + Print-Job).
- `sdcp.py` — minimal Elegoo SDCP status client.
- `content.py` — content-first pipeline (zero-dep text->PDF, weasyprint md/html/svg).
- `cap.py` — per-period page cap.
- `backends.py` — HpBackend, Elegoo3dBackend.
