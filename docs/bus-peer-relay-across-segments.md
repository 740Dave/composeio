---
scope: "Design input + constraints for a ComposeBus peer/relay. Driving case: letting an isolated network segment (e.g., Nexus) reach the bus across a firewall boundary WITHOUT weakening isolation. Generalizes to any transport/trust seam — MQTT<->CANbus bridges, WebSocket client edges, etc."
audience: "The Claude architecting the Compose* stack — bus protocol, nexus-bridge, coordination_relay."
origin: "Home-network segmentation work (fw1 / OPNsense on the VeloCloud 540). Network-architect handoff, 2026-07-20."
status: "Design INPUT — not a ratified spec. Fold into 04-nexus-bridge.smd or a new bus-peer amendment as you see fit."
relates-to:
  - spec/01-composeio-bus-protocol.smd
  - spec/01-amendment-bus-extensions.smd
  - spec/01-amendment-capture-replay.smd
  - spec/01-amendment-critical-ack.smd
  - spec/04-nexus-bridge.smd
  - services/coordination_relay
  - "~/network_configure/NETWORK_DESIGN_v1.md"
  - "~/machine_configure/velocloud_540/per_vlan_matrix_and_state.md"
---

# ComposeBus Peer/Relay Across Network Segments — Design Handoff

## Why this exists (what's changing under you)
The home LAN is being segmented into firewalled VLAN zones behind **fw1** (OPNsense). Two consequences land directly on Compose:

- **The bus stops being co-located with its consumers.** Today the whole surface — MQTT event bus (`:1884`), `bus_storage` (`bus://assets`, `:8420`), `document_store` (`doc://`, `:8421`), and `coordination_relay` (Architect/Builder messaging, `:8422`) — sits on `mqtt-broker.local` on one flat LAN. In the target design those move to the **Servers zone (VLAN30, 192.0.2.0/24)**.
- **Nexus becomes an isolated island.** Nexus lands on **VLAN35 (192.0.2.0/24)** — **default-deny inbound from every LAN zone, reachable only via Tailscale.**

This breaks an assumption baked into `04-nexus-bridge.smd`, which runs the bridge **on mqtt-broker**, subscribing to MQTT locally. Once Nexus is across a firewall boundary, "subscribe to `mqtt-broker:1884`" is a cross-segment call the firewall drops by default — and *should*, because opening Nexus up to satisfy it defeats the isolation. Same for a Nexus-side Claude reaching `coordination_relay`.

So Compose needs a first-class answer to: **how does a consumer on an isolated segment participate in the bus without punching inbound holes in that segment?**

## The one principle that makes it clean
The bus is **client/server, and the isolated consumer is the client** (`composeio.resolve`, the nexus-bridge subscriber). So **make the isolated side always the initiator (pull).** Firewalls gate on *who opens the connection*, not which way *data* flows — so a single **outbound** allow from the isolated zone lets the consumer both **read from and write to** the bus with **zero inbound holes** into Nexus.

**Never push from the master into the isolated zone.** Push needs an inbound listener inside Nexus's wall — the one thing the design forbids. The master never reaches in; the isolated peer always reaches out.

## Recommended shape
- **Interim (works today, zero new code):** the existing client connects **directly** to the bus at its VLAN30 address; fw1 gets one stateful **outbound** rule `VLAN35 → 10.0.30.x : {1884,8420,8421,8422}`. Correct and inbound-safe — but **couples Nexus to the master's reachability/latency** across the boundary.
- **Target — a bus PEER on the isolated segment:** Nexus's apps/bridge talk to a **local peer** on VLAN35; the peer **pulls/syncs** the needed namespaces/objects from the master. Same firewall posture (peer initiates outbound), but you gain **decoupling** (Nexus keeps working off local state when the boundary or master is down), **local latency**, and it is the **decomposed bus-peer model the stack exists to enable** — the segment boundary becomes a clean *sync seam*, not an obstacle.

## Design constraints (please honor these)
1. **Outbound-initiated only from the isolated side.** No component in the isolated zone may *listen* for a connection originating from the master. (This is what preserves default-deny-inbound.)
2. **Multiplex read + write over the isolated-initiated session.** A Nexus persona both consumes and emits bus events; both must ride the single outbound link, never a return connection into Nexus.
3. **Fixed, enumerable port set — ideally collapse to ONE sync port.** Keep the cross-boundary surface small and known so the firewall rule / Tailscale ACL stays narrow. Today's spread (1884/8420/8421/8422) is four rules; a single peer-sync port is one.
4. **Tolerate the boundary being down.** Local cache / last-known-good, reconnect with backoff, eventual consistency on reconnect. This is **capture-replay across a link** — reuse `01-amendment-capture-replay` semantics, don't reinvent.
5. **Preserve bus semantics across the relay** — ordering, and especially **critical-ack** (`01-amendment-critical-ack`): an ack must still mean *delivered* across the peer hop, or degrade explicitly.
6. **Authenticate + encrypt at the app layer.** The two sides are *different trust zones* — do not rely on network trust. **Best option: run the sync over Tailscale** (Nexus is already a tailnet node) → **no LAN pinhole at all**, WireGuard-encrypted, scoped by a Tailscale ACL (`tag:nexus → tag:bus`). The LAN pinhole is the fallback if you don't want the overlay in the data path.

## Beyond the firewall boundary — peer/relay is a ComposeBus primitive
The segment-crossing case above is one instance of a general pattern: a **peer/relay is any point where ComposeBus meets a different transport or trust domain and has to carry messages across.** It's the HAL/adaptor seam (cf. WLED, Car Thing). Expect several:

- **Network-segment relay** (this doc's driving case): MQTT ↔ MQTT across a firewall/segment. Pure reachability + trust bridging; semantics preserved 1:1.
- **Transport bridge — e.g. MQTT ↔ ComposeBus-on-CANbus.** A peer speaking MQTT on one side and CAN on the other. Same core shape, but the adaptor must **map semantics that don't exist on both sides**: CAN has 8-byte frames (fragmentation/reassembly), arbitration-ID addressing (namespace ↔ CAN-ID mapping), and **no QoS/retained/LWT** — so `critical-ack` and retained state must be *synthesized* CAN-side or the bridge must **degrade explicitly** (per `01-amendment-critical-ack`), plus bounded rate/MTU. Same applies to serial/BLE/LoRa adaptors.
- **Client-edge transport — WebSockets vs native.** How a *client frames MQTT*, which is a different axis from *reachability*:
  - **Native client (laptop app, a service):** raw-TCP MQTT (`:1884`), reached over **Tailscale**. Simplest, full semantics, nothing exposed — **WebSockets adds nothing here.**
  - **Browser client (a ComposeUX web UI):** browsers can't open raw TCP, so they **must** use **MQTT-over-WebSockets** (broker WS listener, `wss`). Still MQTT — same QoS/retained/critical-ack, just WS-framed. Reachability still best over **Tailscale** (`wss://` to the broker's tailnet address) so the endpoint isn't public.
  - **Off-tailnet browser / shared link:** a **publicly-exposed `wss://`** (Tailscale Funnel or a reverse proxy with real certs) — the one case that reintroduces an inbound attack surface to auth + harden. Prefer tailnet-scoped WS unless a client genuinely can't join the tailnet.

  **"WebSockets vs Tailscale" is a false choice:** WS is an alternative to *raw-TCP framing* (needed for browsers); Tailscale is an alternative to *a public endpoint* (needed for reachability). They compose → browser + tailnet = **MQTT-over-WS over Tailscale**. A standalone WS **gateway** (translating a *non-MQTT* WS protocol ↔ MQTT) is warranted only when the client can't speak MQTT-over-WS — and that gateway is itself just another instance of this relay primitive.

**Which constraints carry across all seams:** the **initiator/pull** rule and **"authenticate independent of network trust"** apply everywhere; **"tolerate the link down + capture-replay"** applies wherever the far side can vanish (CAN device asleep, browser tab closed, boundary down); **"preserve-or-degrade-explicitly"** becomes *load-bearing* the moment a transport lacks a semantic (CAN has no QoS).

## Open questions for you (I don't own the bus internals)
- Does `bus_storage` / `document_store` already support **peer replication / incremental sync**, or is that net-new? Decides "config vs. build."
- Does the `04-nexus-bridge` bridge **move to the Nexus side** now (subscribing to a *local peer*), or stay master-side and itself become the remote thing?
- Consistency model for `bus://assets` + `doc://` across a peer — full replica, lazy fetch-on-resolve, or subscription-scoped mirror?
- `coordination_relay` (:8422) hits the **same** boundary for any Claude embodying a Nexus persona on the isolated segment — fold it into the same peer, or a shared relay-over-Tailscale story?

## Acceptance (what "done" looks like)
- A consumer on VLAN35 reads **and** writes the bus while the isolated zone accepts **no inbound LAN connections** (verify: `fw1` shows only an outbound allow — or nothing on the LAN if it's over Tailscale).
- Severing the VLAN30↔VLAN35 path leaves Nexus-side apps functional on local state; on restore they converge with **no lost critical-acked messages**.
- The cross-boundary link is authenticated/encrypted independent of network trust.

---
**Network-side pointers:** zone design `~/network_configure/NETWORK_DESIGN_v1.md`; per-zone firewall/DHCP matrix `~/machine_configure/velocloud_540/per_vlan_matrix_and_state.md` (VLAN35 Nexus = default-deny inbound, Tailscale-only; VLAN30 Servers). Firewall rules on the fw1 side are mine to write once you tell me the final port(s) and whether the sync rides Tailscale or a LAN pinhole.
