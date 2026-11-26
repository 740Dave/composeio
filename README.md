# ComposeIO

> **There is no center.**

Building a big interactive experience — a smart home, an art installation, your own sci‑fi cockpit, an escape‑room puzzle — is the fun part. Wiring it together so it behaves the way you pictured is where it collapses, under brittle integrations, heavyweight platforms, and glue code.

ComposeIO turns the usual hub model inside out. There is no central brain that owns every integration. Instead, **every participant — a display, a sensor, a $0.10 microcontroller, even an AI — is a co‑equal peer** on a stateless bus, publishing and subscribing *semantic* messages (`throttle = 0.7`, not `pin 7 high`). Complexity flows *down* to the cheapest node that can own it; intent flows *up* the bus as meaning. Turn one knob and as many devices as you like react exactly how you configured them — in sync, for free.

**▶ [Watch the demo](./media/compose_demo.mp4)** — one input on the bus, many devices reacting live.

> ⚠️ **Initial public beta.** This is a point‑in‑time snapshot of a working system, shared so the shape of the idea is legible to others. It is **not** under active week‑to‑week development — expect the next significant update some months out, not a steady stream of commits.

## The shape of it

- **The bus is a contract, not a wire.** A peer publishes and subscribes *namespaces*; it never knows whether the message rode MQTT, CAN, UART, or WiFi. Bridges splice two live wires where a segment speaks a different one.
- **One value, many realizations.** `throttle = 0.7` is published once and drives an on‑screen gauge, a stepper needle, and a seven‑segment numeral at the same time. The widget is the *meaning*; the backend is the *realization*.
- **State on the bus, behavior in the leaf.** `red_alert = on` is published once; each screen owns its own blink. Frames never stream over the bus.
- **Even the ten‑cent floor owns its complexity.** A capable ESP32 runs the full reactive engine; a $0.10 CH32V003 still fully encapsulates its local behavior behind a clean semantic API.

## Draw it in Inkscape, render it anywhere

ComposeUX — the rendering half — defines a widget **once** against a small Canvas contract. Lay a screen out by hand in Inkscape; its elements are extracted and built into an app that renders **pixel‑identically** across very different targets — a $4 ESP32‑C3, a Raspberry Pi, and macOS — the same pixels, not a per‑platform port. Skia draws it on the desktop tier; a matching C++ engine draws the identical result on the microcontroller. Windows or Android are another backend away.

## What's in this beta

| path | what it is |
|---|---|
| `python/composeio/` | the bus + the ComposeUX render engine, transports, adaptors |
| `cyd/`, `firmware/reactive_core/` | the embedded tier — ESP32 (S3 / C3 / C6) and CH32 board firmware |
| `services/` | bus‑resident services: storage, documents, a shared clock, CAN/UART↔MQTT bridges |
| `spec/`, `docs/` | the specifications and design notes |
| `*.compose.yaml` examples | scene definitions the engine renders |

This snapshot is deliberately scoped to the platform core; some demo apps and internal tooling from the working tree are held back.

> **Network config in examples is placeholdered.** Broker addresses and hostnames appear as `mqtt-broker.local` / `*.example.local` and device MACs as IEEE documentation addresses (`00:00:5E:00:53:xx`) and device serials as `<device-serial>`. Point them at your own bus.

## Status

Beta / snapshot. APIs and wire formats may still move between the occasional major drops. Curiosity and issues are welcome; steady week‑to‑week maintenance is not promised.

## License

[MIT](./LICENSE) — do what you like, no warranty.
