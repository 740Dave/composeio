# office_devices — headless relay-fan controllers (MIT)

Generic home/office ComposeIO devices — **MIT-publishable**, permissive deps only
(PlatformIO + Arduino-ESP32 + PubSubClient + ArduinoJson). NOT BSG game content.

Two devices this pass, **identical** ESP32-WROOM-32E integrated 4-relay boards:

| device | namespace (enum) | asset id |
|---|---|---|
| `office_fan`  | `office_fan/speed`  | `office_fan`  |
| `office_hepa` | `office_hepa/speed` | `office_hepa` |

Each is a **headless, config-fetched** leaf: it subscribes ONE semantic enum
namespace (`off | low | medium | high`) and actuates a 3-speed AC tap fan through
the on-board relay bank, with **break-before-make + ~80 ms dead-time** (motor
safety — two speed relays are NEVER closed at once). It holds no logic beyond the
actuator; a bus peer (human or Intelligence) publishes the speed.

The device runs the platform **`reactive_core`** firmware unchanged. At boot it
GETs its `.compose.yaml` from the bus asset store by device id and self-configures
its relay HAL from the `relay_actuator:` block — no baked pins. This project adds
only the **config** (the two `.compose.yaml` + `device.yaml`); the firmware relay
actuator lives in `firmware/reactive_core/` (`relay_actuator.{h,cpp}` +
`controller_config` lowering + the `controller_main` shell wiring).

## Wiring (verified, locked)

- Powered externally at **12 V DC** (browns out on FTDI serial power alone).
- Relays, **active-HIGH** (energize on GPIO HIGH):
  `RY1=GPIO32` (spare) · `RY2=GPIO33` · `RY3=GPIO25` · `RY4=GPIO26`.
- Fan speed tap: **low=RY2 · medium=RY3 · high=RY4**. `off` = all relays open.
  No master relay (a speed-tap fan does not need one).

## 1. Publish each config to the bus asset store (do this BEFORE boot)

The bus master asset store is `$COMPOSEIO_FW_STORAGE_URL` (`mqtt-broker.local`).

```sh
# office_fan
curl -X PUT --data-binary \
  @compose_projects/office_devices/office_fan/office_fan.compose.yaml \
  $COMPOSEIO_FW_STORAGE_URL/api/v1/assets/displays/office_fan.compose.yaml

# office_hepa
curl -X PUT --data-binary \
  @compose_projects/office_devices/office_hepa/office_hepa.compose.yaml \
  $COMPOSEIO_FW_STORAGE_URL/api/v1/assets/displays/office_hepa.compose.yaml
```

## 2. Flash the firmware (physical board — architect/David runs this)

`reactive_core` is the platform firmware, built from `composeio/firmware/reactive_core`.
The board has **no native USB** — flash over an **external FTDI** (TX/RX crossed) on
`$OFFICE_RELAY_SERIAL_PORT` (host overlay; the flash guard refuses when it is unset), in **MANUAL download mode** (tie `IO0`→`GND`, tap `EN`),
because the FTDI auto-reset is unreliable. Flash the two boards **one at a time** on
the shared FTDI cable.

```sh
cd composeio/firmware/reactive_core

# office_fan  (env bakes -DDISPLAY_DEVICE_ID="office_fan")
pio run -e office_fan -t upload    # port = $OFFICE_RELAY_SERIAL_PORT (host overlay)
pio device monitor --port "$OFFICE_RELAY_SERIAL_PORT" -b 115200

# office_hepa (env bakes -DDISPLAY_DEVICE_ID="office_hepa")
pio run -e office_hepa -t upload    # port = $OFFICE_RELAY_SERIAL_PORT (host overlay)
```

WiFi creds: drop a `firmware/reactive_core/src/wifi_config.h` (gitignored) with
`#define WIFI_SSID/WIFI_PASS`, or pass them via `PLATFORMIO_BUILD_FLAGS`. The env
already sets `MQTT_PORT=1883` and `BUS_STORAGE_URL=$COMPOSEIO_FW_STORAGE_URL`.

## 3. Drive it (any bus peer — human or Intelligence)

Publish a speed as a ComposeBus state message (global scope):

```sh
mosquitto_pub -h mqtt-broker.local \
  -t 'composeio/global/office_fan/speed' \
  -m '{"ns":"office_fan/speed","type":"state","val":"medium","scope":"global"}'
```

`val` ∈ `off | low | medium | high`. An unknown value is withheld (relays untouched).

## 4. Live re-fetch / reconfigure (no power-cycle)

Edit a `.compose.yaml`, re-PUT it (step 1), then publish a **truthy** command to the
device's reload namespace. The device acks and re-runs its boot config-fetch,
re-lowering the relay HAL — no reflash, no power-cycle.

```sh
# per-device
mosquitto_pub -h mqtt-broker.local \
  -t 'composeio/global/_device/office_fan/restart' \
  -m '{"ns":"_device/office_fan/restart","type":"state","val":true,"scope":"global"}'

# broadcast to every reactive_core device
mosquitto_pub -h mqtt-broker.local \
  -t 'composeio/global/_device/restart' \
  -m '{"ns":"_device/restart","type":"state","val":true,"scope":"global"}'
```

Ack: `_device/<id>/status` → `"restarting"`. A 3 s post-boot guard drops a
re-delivered/retained command so the device cannot restart-loop.

## Deferred (clean seams left; NOT built)

- **Reactive sensor rules** (e.g. HEPA speed derived from a particulate namespace):
  a future local `ReactiveCore` binding would compute and publish the SAME
  `<device>/speed` enum this actuator already subscribes — no change to the
  actuator. Seam noted in each `.compose.yaml`.

## Tests (broker-free, host-runnable)

- Firmware: `cd firmware/reactive_core && pio run -e native_test && .pio/build/native_test/program`
  (relay break-before-make + dead-time + config lowering + reload handler).
- Python: `cd python && PYTHONPATH=. python3 -m pytest tests/render/test_relay_actuator.py`
  (the `relay_actuator:` parser + the two real configs).
