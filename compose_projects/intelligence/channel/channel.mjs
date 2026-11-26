#!/usr/bin/env node
/**
 * ComposeIO Intelligence Channel — the pure, platform-level transport that bridges a
 * running Claude Code session to ComposeBus. It is the foundation of the Intelligence
 * Console (see docs/intelligence-console/DESIGN-HANDOFF.md): a Claude instance becomes a
 * bus peer whose duplex "channel" maps to `compose_projects/intelligence/<id>/{in,out}`.
 *
 * Claude Code channel server (MCP stdio subprocess) that:
 *   1. Subscribes to MQTT topic `compose_projects/intelligence/<id>/in`   → pushes each message into the
 *      session as a <channel source="composeio-channel" ...> event.
 *   2. Exposes a two-way `reply` tool → publishes to `compose_projects/intelligence/<id>/out`.
 *   3. Relays permission prompts: forwards `permission_request` to
 *      `compose_projects/intelligence/<id>/permission/request` and applies verdicts arriving on
 *      `compose_projects/intelligence/<id>/permission/verdict`.
 *
 * This is the PURE transport seam. It carries NO application content (no BSG, no Haven
 * runtime); it is Type-Agnostic Transport — a bus peer that happens to be a mind.
 *
 * Prior art mined (SHAPE only, not a dependency): Haven's haven-dispatch-channel
 * (~/haven/haven-dispatch-channel/channel.mjs) — its MCP-stdio +
 * lifecycle + meta-sanitization ([A-Za-z0-9_]) + graceful-shutdown structure. Haven routes
 * pushes through an HTTP registry; here that model is replaced with ComposeBus pub/sub, so
 * the Intelligence is addressed by a bus topic instead of a registered listener port.
 *
 * Topic map (id = INTELLIGENCE_ID):
 *   compose_projects/intelligence/<id>/in                  inbound  → <channel> event
 *   compose_projects/intelligence/<id>/out                 outbound ← reply tool
 *   compose_projects/intelligence/<id>/permission/request  outbound ← permission_request relay
 *   compose_projects/intelligence/<id>/permission/verdict  inbound  → permission verdict
 *
 * Config (env): INTELLIGENCE_ID (default "architect"),
 *               COMPOSEBUS_MQTT (default "mqtt://mqtt-broker.local:1883").
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { ListToolsRequestSchema, CallToolRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import { z } from "zod";
import { appendFileSync } from "node:fs";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

// ── Config ──────────────────────────────────────────────────

const SERVER_NAME = "composeio-channel";     // stable MCP name → <channel source="composeio-channel">
const INTELLIGENCE_ID = process.env.INTELLIGENCE_ID || "architect";
const MQTT_URL = process.env.COMPOSEBUS_MQTT || "mqtt://mqtt-broker.local:1883";

// Observability: append lifecycle + inbound events to a file (Claude Code doesn't surface
// MCP-server stderr). Opt-out with CHANNEL_LOG=off. Default /tmp/composeio-channel-<id>.log.
const CHANNEL_LOG = process.env.CHANNEL_LOG || `/tmp/composeio-channel-${INTELLIGENCE_ID}.log`;
function logf(...a) {
  if (CHANNEL_LOG === "off") return;
  try { appendFileSync(CHANNEL_LOG, `[${new Date().toISOString()}] ${a.join(" ")}\n`); } catch { /* ignore */ }
}

// Topic map for this Intelligence. The id is carried in meta AND in every topic so a
// single broker can host many Intelligences without collision.
function topicsFor(id) {
  return {
    in: `compose_projects/intelligence/${id}/in`,
    out: `compose_projects/intelligence/${id}/out`,
    permissionRequest: `compose_projects/intelligence/${id}/permission/request`,
    permissionVerdict: `compose_projects/intelligence/${id}/permission/verdict`,
    // Liveness HEARTBEAT ([SPEC:CXC-CONSOLE-VISUAL] link health): a periodic
    // beat published here proves channel.mjs is alive and delivering. The
    // channel_bridge relays it onto the composeio scheme so the Intelligence
    // Console's live-dot can tell "channel down" from "bus down" — the fix for
    // a send that LOOKS delivered (echoed to the thread) but never reached the
    // mind because the channel MCP was down.
    alive: `compose_projects/intelligence/${id}/channel/alive`,
    // CONTESTED ([CXC-IC-CHANNEL-INSTANCE]): a DIFFERENT instance arrived under a LIVE
    // instance's id and REFUSED the channel rather than evicting it. Published so the
    // console and any monitor can render the contest — a silent refusal would merely
    // move the defect rather than close it.
    contested: `compose_projects/intelligence/${id}/channel/contested`,
  };
}

// Heartbeat cadence (ms). ~5s; the console calls the channel stale after ~12s
// (it tolerates one dropped beat). Override with CHANNEL_HEARTBEAT_MS.
const HEARTBEAT_MS = Number(process.env.CHANNEL_HEARTBEAT_MS) || 5000;

// Delivery-liveness ([CXC-IC-CHANNEL] IC-C17): the channels contract gives NO receipt that
// a `notifications/claude/channel` we wrote to stdout was actually SURFACED to the session
// (a client-side listener lost after a /compact or resume churn silently drops it, yet our
// write succeeds and we log INJECTED). We detect that heuristically: after this many injects
// with no downstream ACK (any client->server call — `reply`/permission — proves the session
// is receiving+acting), the heartbeat reports delivery "degraded" so the console's link dot
// goes RED (a detected break) instead of false-green (a silent one). Override with
// CHANNEL_DELIVERY_DEGRADED.
const DELIVERY_DEGRADED_THRESHOLD = Number(process.env.CHANNEL_DELIVERY_DEGRADED) || 3;

// ── The INSTANCE identity ([CXC-IC-CHANNEL-INSTANCE]) ───────
// What tells "the same instance restarting" from "a different instance wearing the same id".
// It is minted from THE SESSION's own identity — never from the pid (a restart changes the
// pid, and then every restart would look like a stranger) and never from the id (which is
// the very thing being disambiguated). `CLAUDE_CODE_SESSION_ID` survives an `/exit`+`--resume`
// of the same session, which is exactly the stability the rule needs.
//
// When it cannot be determined we fall back to "" and the LEGACY displacement rule applies
// unchanged — a bridge that cannot prove it is a different instance must not be able to
// refuse a live peer's identity on a guess, and must not become un-restartable either.
const INSTANCE_IDENTITY =
  process.env.CHANNEL_INSTANCE ||
  process.env.CLAUDE_CODE_SESSION_ID ||
  "";

// ── Meta sanitization (mined from haven-dispatch) ───────────
// Channel <channel> tag attribute keys must be identifiers: [A-Za-z0-9_]. Claude Code
// silently DROPS keys with other characters, so we rewrite them to underscore to keep the
// information rather than lose it. `id` is always present so the session knows which
// Intelligence a message belongs to.
function buildMeta(id, payload) {
  const meta = { id: String(id) };
  if (payload && typeof payload === "object") {
    for (const [key, value] of Object.entries(payload)) {
      // The body fields become `content`, not meta attributes.
      if (["text", "content", "body", "message"].includes(key)) continue;
      if (value == null) continue;
      if (typeof value === "object") continue; // meta values are scalars only
      const safeKey = key.replace(/[^A-Za-z0-9_]/g, "_");
      meta[safeKey] = String(value);
    }
  }
  return meta;
}

// Map a raw bus payload (JSON object OR plain string) to the <channel> body text.
function bodyOf(payload) {
  if (payload && typeof payload === "object") {
    return String(payload.text ?? payload.content ?? payload.body ?? payload.message ?? "");
  }
  return String(payload ?? "");
}

// Best-effort JSON parse; a non-JSON message is carried as a plain string.
function parsePayload(raw) {
  const s = typeof raw === "string" ? raw : raw?.toString?.("utf8") ?? "";
  try {
    return JSON.parse(s);
  } catch {
    return s;
  }
}

// ── Channel factory ─────────────────────────────────────────
// Everything is wired here so the selftest can drive the exact same code paths with a
// mocked MQTT client and without a live transport. `deps.mqttClient` is injected in tests;
// in production we lazily `mqtt.connect(MQTT_URL)`.

export function createChannel({
  id = INTELLIGENCE_ID,
  mqttUrl = MQTT_URL,
  mqttClient = null,
  startTs = Date.now(),
  pid = process.pid,
  instance = INSTANCE_IDENTITY,
  onDisplaced = null,
  onContested = null,
} = {}) {
  const topics = topicsFor(id);

  // Single-instance identity ([CXC-IC-CHANNEL], last-writer-wins). This bridge's claim to
  // compose_projects/intelligence/<id> is stamped by (MY_START, MY_PID). A liveness beat from ANOTHER bridge
  // for the SAME id that is NEWER (later start; tie-break higher pid) means this bridge is
  // the stale one — it yields the identity via onDisplaced (in production: shutdown + exit),
  // so a /exit+resume or a duplicate concurrent session collapses to exactly one live owner.
  //
  // ⚠️ THAT RULE HAS AN UNSTATED PREMISE: **same id implies same instance**
  // ([CXC-IC-CHANNEL-INSTANCE], amended 2026-08-11). It was designed for a RESTART, where the
  // older bridge is a corpse holding a name and yielding is required. A widget-hosted child
  // handed its host's id is a genuinely DIFFERENT instance, so the premise is false and the
  // rule EVICTS A LIVE PEER — measured: the host Architect's bridge logged "displaced by a
  // newer bridge", exited, and David's console lost its voice, with nothing on the glass
  // saying why. So the two cases are now distinguished by MY_INSTANCE (below) and answered
  // differently: same instance -> DISPLACE, unchanged; different instance -> REFUSE + ANNOUNCE.
  const MY_START = Number(startTs) || 0;
  const MY_PID = Number(pid) || 0;
  const MY_INSTANCE = String(instance || "");
  let displaced = false;
  let refused = false;

  const mcp = new Server(
    { name: SERVER_NAME, version: "1.0.0" },
    {
      capabilities: {
        experimental: {
          "claude/channel": {},              // register the notification listener
          "claude/channel/permission": {},   // opt in to remote permission relay
        },
        tools: {},                            // two-way: the reply tool
      },
      instructions: [
        `Messages from ComposeBus arrive as <channel source="${SERVER_NAME}" id="${id}" ...>.`,
        "The `id` attribute is this Intelligence's id on the bus; other attributes are the",
        "message's structured fields. To answer, call the `reply` tool with `text` (and an",
        "optional `to`/`thread` for addressing/correlation); it publishes to the bus.",
        "Permission prompts may be approved or denied remotely over this channel.",
      ].join(" "),
    }
  );

  // The mqtt client. Production: real `mqtt`. Test: injected in-memory fake with the same
  // surface (.on/.subscribe/.publish/.end). Created lazily so importing this module for the
  // selftest never dials the broker.
  let mqtt = mqttClient;

  // ── Liveness heartbeat ([SPEC:CXC-CONSOLE-VISUAL] link health) ──────────
  // A periodic beat on topics.alive, reusing THIS mqtt client. If channel.mjs
  // is down, no beat -> the console's live-dot goes CHANNEL OFFLINE.
  let heartbeatTimer = null;

  // ── Delivery-liveness ([CXC-IC-CHANNEL] IC-C17) ─────────────
  // Count injects not yet ACKed by downstream client->server activity. An ACK (a `reply` tool
  // call or a permission_request from the session) proves the notification path is live end to
  // end; a pile of unacked injects means our notifications are being written but not surfaced
  // (a client-side listener lost after churn), so the heartbeat flags delivery "degraded".
  let unackedInjects = 0;
  let degradedLogged = false;
  function markInjected() { unackedInjects += 1; }
  function ackDelivery() { unackedInjects = 0; degradedLogged = false; }
  function deliveryState() {
    return unackedInjects >= DELIVERY_DEGRADED_THRESHOLD ? "degraded" : "ok";
  }

  function beat() {
    if (mqtt && (mqtt.connected === undefined ? true : mqtt.connected)) {
      const delivery = deliveryState();
      if (delivery === "degraded" && !degradedLogged) {
        degradedLogged = true;
        logf("DELIVERY DEGRADED", "id=" + id, unackedInjects + " injects unacked >= " +
          DELIVERY_DEGRADED_THRESHOLD + " — notifications written but not surfaced; RECONNECT NEEDED");
        try {
          process.stderr.write(`[${SERVER_NAME}] delivery DEGRADED for "${id}" — ` +
            `${unackedInjects} injects unacked; run /mcp reconnect ${SERVER_NAME}\n`);
        } catch { /* stderr may be gone */ }
      }
      try {
        mqtt.publish(
          topics.alive,
          // `instance` rides every beat: it is what lets a peer tell a RESTART of this
          // instance from a DIFFERENT instance wearing this id ([CXC-IC-CHANNEL-INSTANCE]).
          JSON.stringify({ ts: Date.now(), id, pid: MY_PID, start: MY_START,
                           instance: MY_INSTANCE, delivery, unacked: unackedInjects }),
          { qos: 0 }
        );
      } catch { /* a transient publish failure is itself a missed beat */ }
    }
  }

  function startHeartbeat() {
    if (heartbeatTimer) return;
    beat();                                  // beat once immediately (fast green)
    heartbeatTimer = setInterval(beat, HEARTBEAT_MS);
    if (heartbeatTimer.unref) heartbeatTimer.unref();  // never hold the loop open
  }

  function stopHeartbeat() {
    if (heartbeatTimer) {
      clearInterval(heartbeatTimer);
      heartbeatTimer = null;
    }
  }

  // mcp.notification() is async and returns a promise; a rejected notification (e.g. the
  // stdio transport failed mid-write) would otherwise escape the synchronous try/catch in
  // onMqttMessage and surface only as an unhandledRejection. Attach a .catch so a
  // notification failure is logged to the CHANNEL_LOG and the channel stays up.
  function notify(n) {
    try {
      const p = mcp.notification(n);
      if (p && typeof p.then === "function") {
        p.catch((e) => logf("NOTIFY ERROR", n.method, (e && (e.stack || e.message)) || String(e)));
      }
    } catch (e) {
      logf("NOTIFY ERROR", n.method, (e && (e.stack || e.message)) || String(e));
    }
  }

  // ── Inbound: bus → <channel> event ──────────────────────
  function handleInbound(rawPayload) {
    const payload = parsePayload(rawPayload);
    const content = bodyOf(payload);
    logf("INBOUND", topics.in, "->", JSON.stringify(String(content)).slice(0, 200));
    notify({
      method: "notifications/claude/channel",
      params: { content, meta: buildMeta(id, payload) },
    });
    logf("INJECTED notifications/claude/channel");
    // Delivery-liveness ([CXC-IC-CHANNEL] IC-C17): count this inject as unacked until a
    // downstream client->server call proves the session actually surfaced our notifications.
    markInjected();
  }

  // ── Inbound: permission verdict → apply to Claude Code ──
  function handleVerdict(rawPayload) {
    const payload = parsePayload(rawPayload);
    if (!payload || typeof payload !== "object") return;
    const { request_id, behavior } = payload;
    if (!request_id || (behavior !== "allow" && behavior !== "deny")) return;
    notify({
      method: "notifications/claude/channel/permission",
      params: { request_id: String(request_id), behavior },
    });
  }

  // ── Inbound: peer liveness beat → single-instance displacement ──
  // A beat on topics.alive from ANOTHER bridge for the SAME id carries that bridge's
  // (start, pid). Last-writer-wins: the earlier-started bridge yields the identity to the
  // later one (tie-break: higher pid yields). This collapses a /exit+resume leak or a
  // duplicate concurrent session to exactly ONE live owner — no reconnect-war (the loser
  // exits rather than reconnecting to fight), no inbound fan-out, no heartbeat masking.
  function handleAlive(rawPayload) {
    const p = parsePayload(rawPayload);
    if (!p || typeof p !== "object" || p.id !== id) return;
    const otherPid = Number(p.pid);
    if (!otherPid || otherPid === MY_PID) return;          // ignore our own beat
    const otherStart = Number(p.start) || 0;
    const otherInstance = String(p.instance || "");
    const iAmOlder = otherStart > MY_START || (otherStart === MY_START && otherPid > MY_PID);

    // CASE 2 — a DIFFERENT instance under this id ([CXC-IC-CHANNEL-INSTANCE]). Both sides can
    // tell (both carry an instance identity and they differ), so the ARRIVING bridge — the one
    // that started LATER, i.e. NOT `iAmOlder` — declines the channel and says so. The incumbent
    // is untouched and never learns it was challenged beyond the log. Rarity was never the
    // problem; SILENCE was: a rare failure that announces itself costs one log line, a rare
    // failure that silently kills a live peer costs the session that noticed.
    if (MY_INSTANCE && otherInstance && otherInstance !== MY_INSTANCE) {
      if (!iAmOlder && !refused) {
        refused = true;
        logf("CONTESTED", "id=" + id, "incumbent-instance=" + otherInstance,
             "arriving-instance=" + MY_INSTANCE, "— REFUSING the channel, incumbent keeps it");
        try {
          process.stderr.write(
            `[${SERVER_NAME}] REFUSED the channel for "${id}": a DIFFERENT live instance ` +
            `(${otherInstance}) already holds it. This session runs UNCHANNELLED — relaunch ` +
            `it under a distinct id ([CXC-TERM-CHILD-ID]).\n`);
        } catch { /* stderr may be gone */ }
        const body = { contested: true, id, incumbent: otherInstance,
                       arriving: MY_INSTANCE, ts: Date.now() };
        try {
          if (mqtt) mqtt.publish(topics.contested, JSON.stringify(body), { qos: 1 });
        } catch { /* the log and the session notice still fired */ }
        // (c) tell our OWN session, so the instance KNOWS it has no channel rather than
        // wondering why nobody answers. An instance that cannot take the channel must SAY so.
        notify({
          method: "notifications/claude/channel",
          params: {
            content:
              `This session could NOT take the channel for "${id}": a different live instance ` +
              `already holds it, and evicting a live peer is forbidden ` +
              `([CXC-IC-CHANNEL-INSTANCE]). You are running UNCHANNELLED — everything else ` +
              `(PTY, transcript, self/*) still works. Relaunch under a distinct instance id.`,
            meta: buildMeta(id, { kind: "channel-contested", incumbent: otherInstance }),
          },
        });
        stopHeartbeat();          // stop claiming an identity we just declined
        if (typeof onContested === "function") onContested(body);
      }
      return;                     // NEVER fall through to displacement on a different instance
    }

    // CASE 1 — the SAME instance restarting (or an identity we cannot tell apart): the
    // original last-writer-wins rule, kept VERBATIM, because for a restart the older bridge
    // is a corpse holding a name and yielding is not merely safe but required.
    if (iAmOlder && !displaced) {
      displaced = true;
      logf("DISPLACED", "id=" + id, "by pid=" + otherPid, "start=" + otherStart, "— yielding identity");
      if (typeof onDisplaced === "function") onDisplaced();
    }
  }

  // ── MQTT message router ─────────────────────────────────
  // Catch-and-continue: a bad inbound payload, a publish failure, or a notification failure
  // must NOT take down the whole channel. We log the error to the CHANNEL_LOG and keep
  // running so one poison message can't sever the console<->Intelligence link.
  function onMqttMessage(topic, payload) {
    try {
      if (topic === topics.in) handleInbound(payload);
      else if (topic === topics.permissionVerdict) handleVerdict(payload);
      else if (topic === topics.alive) handleAlive(payload);
    } catch (e) {
      logf("HANDLER ERROR", topic, (e && (e.stack || e.message)) || String(e));
      // swallow — server stays up
    }
  }

  // ── Outbound: reply tool → bus ──────────────────────────
  mcp.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: "reply",
        description:
          `Publish a reply from this Intelligence back onto ComposeBus (${topics.out}).`,
        inputSchema: {
          type: "object",
          properties: {
            text: { type: "string", description: "The message body to publish." },
            to: { type: "string", description: "Optional addressee (recipient intelligence id)." },
            thread: { type: "string", description: "Optional thread / correlation id." },
          },
          required: ["text"],
        },
      },
    ],
  }));

  mcp.setRequestHandler(CallToolRequestSchema, async (req) => {
    // Any client->server tool call is proof the session is live and surfacing our channel
    // notifications — clear the delivery-degraded state ([CXC-IC-CHANNEL] IC-C17).
    ackDelivery();
    if (req.params.name === "reply") {
      const { text, to, thread } = req.params.arguments ?? {};
      const message = { from: id, text: String(text ?? ""), ts: Date.now() };
      if (to != null) message.to = String(to);
      if (thread != null) message.thread = String(thread);
      if (mqtt) {
        try {
          mqtt.publish(topics.out, JSON.stringify(message));
        } catch (e) {
          // A publish failure must not throw out of the tool and destabilize the channel.
          logf("REPLY PUBLISH ERROR", (e && (e.stack || e.message)) || String(e));
          return { content: [{ type: "text", text: "error: publish failed" }], isError: true };
        }
      }
      return { content: [{ type: "text", text: "sent" }] };
    }
    throw new Error(`unknown tool: ${req.params.name}`);
  });

  // ── Outbound: permission_request relay → bus ────────────
  const PermissionRequestSchema = z.object({
    method: z.literal("notifications/claude/channel/permission_request"),
    params: z.object({
      request_id: z.string(),
      tool_name: z.string(),
      description: z.string(),
      input_preview: z.string(),
    }),
  });

  mcp.setNotificationHandler(PermissionRequestSchema, async ({ params }) => {
    // A permission_request is client->server traffic — proof the session is live (IC-C17 ack).
    ackDelivery();
    const relay = {
      request_id: params.request_id,
      tool_name: params.tool_name,
      description: params.description,
      input_preview: params.input_preview,
    };
    if (mqtt) {
      try {
        mqtt.publish(topics.permissionRequest, JSON.stringify(relay));
      } catch (e) {
        // Never let a relay publish failure escape the notification handler.
        logf("PERMISSION RELAY ERROR", (e && (e.stack || e.message)) || String(e));
      }
    }
  });

  // ── Bus connect / subscribe ─────────────────────────────
  // Re-subscribe on EVERY (re)connection. mqtt.js auto-reconnects (default reconnectPeriod
  // 1000ms) and re-emits 'connect' on each successful (re)connect, so wiring the subscribes
  // in the 'connect' handler makes a bus blip self-heal: reconnect → resubscribe → deliveries
  // resume, and the heartbeat (interval-driven, on the same client) continues once
  // mqtt.connected flips back true. All transitions are logged so a blip is visible.
  function resubscribe() {
    mqtt.subscribe(topics.in, { qos: 1 });
    mqtt.subscribe(topics.permissionVerdict, { qos: 1 });
    // Hear peer bridges' liveness beats so a newer bridge can displace this one
    // ([CXC-IC-CHANNEL] single-instance identity).
    mqtt.subscribe(topics.alive, { qos: 0 });
  }

  async function connectBus() {
    if (!mqtt) {
      const mqttLib = await import("mqtt");
      mqtt = mqttLib.default.connect(mqttUrl, { clientId: `${SERVER_NAME}-${id}-${process.pid}` });
    }
    mqtt.on("message", onMqttMessage);
    mqtt.on("connect", () => {
      resubscribe();
      process.stderr.write(`[${SERVER_NAME}] bus connected ${mqttUrl}; subscribed ${topics.in}\n`);
      logf("BUS CONNECTED", mqttUrl, "subscribed", topics.in, "+", topics.permissionVerdict);
    });
    mqtt.on("reconnect", () => {
      logf("BUS RECONNECT", "attempting", mqttUrl);
    });
    mqtt.on("offline", () => {
      logf("BUS OFFLINE", mqttUrl, "— will auto-reconnect; heartbeat pauses while offline");
    });
    mqtt.on("close", () => {
      logf("BUS CLOSE", mqttUrl);
    });
    mqtt.on("error", (e) => {
      process.stderr.write(`[${SERVER_NAME}] mqtt error: ${e.message}\n`);
      logf("MQTT ERROR", (e && (e.stack || e.message)) || String(e));
    });
    // A fake client used in tests may already be "connected"; subscribe eagerly too so the
    // router is live even without a 'connect' event.
    if (typeof mqtt.subscribe === "function") {
      mqtt.subscribe(topics.in, { qos: 1 });
      mqtt.subscribe(topics.permissionVerdict, { qos: 1 });
      mqtt.subscribe(topics.alive, { qos: 0 });
    }
    // Start beating now that we have a client (reuses the same connection).
    startHeartbeat();
    return mqtt;
  }

  function isBusConnected() {
    return !!(mqtt && (mqtt.connected === undefined ? true : mqtt.connected));
  }

  async function shutdown() {
    stopHeartbeat();
    if (mqtt) {
      await new Promise((resolve) => {
        try {
          mqtt.end(false, {}, resolve);
        } catch {
          resolve();
        }
      });
    }
  }

  return {
    mcp,
    topics,
    connectBus,
    shutdown,
    isBusConnected,
    // Exposed for the selftest so it drives the real handler code paths.
    _handlers: { handleInbound, handleVerdict, onMqttMessage },
    _beat: beat,
    get _deliveryState() {
      return deliveryState();
    },
    get _mqtt() {
      return mqtt;
    },
  };
}

// ── Runtime entrypoint ──────────────────────────────────────

async function main() {
  logf("STARTUP", SERVER_NAME, "id=" + INTELLIGENCE_ID, "mqtt=" + MQTT_URL, "pid=" + process.pid);

  // Resilience: a stray throw/rejection ANYWHERE must not silently vanish. Log it to the
  // CHANNEL_LOG (not just stderr, which Claude Code doesn't surface) so the next death is
  // diagnosable. We do NOT exit here — the channel keeps running; only truly unrecoverable
  // transport EOF (handled by the MCP transport / signal handlers) ends the process.
  process.on("uncaughtException", (e) => {
    logf("UNCAUGHT_EXCEPTION", (e && (e.stack || e.message)) || String(e));
    process.stderr.write(`[${SERVER_NAME}] uncaughtException: ${(e && (e.stack || e.message)) || e}\n`);
  });
  process.on("unhandledRejection", (reason) => {
    const r = reason instanceof Error ? (reason.stack || reason.message) : String(reason);
    logf("UNHANDLED_REJECTION", r);
    process.stderr.write(`[${SERVER_NAME}] unhandledRejection: ${r}\n`);
  });

  const channel = createChannel({
    // Single-instance identity ([CXC-IC-CHANNEL]): if a NEWER bridge for this same id
    // announces itself, THIS (older) bridge yields the identity and exits — so a
    // /exit+resume or a duplicate concurrent session leaves exactly one live owner.
    onDisplaced: () => {
      logf("SINGLE-INSTANCE", "newer bridge for id=" + INTELLIGENCE_ID + " took over — exiting");
      process.stderr.write(`[${SERVER_NAME}] displaced by a newer bridge for "${INTELLIGENCE_ID}" — exiting\n`);
      stop().catch(() => process.exit(0));
    },
  });
  await channel.connectBus();
  const transport = new StdioServerTransport();

  // Truly unrecoverable: the MCP stdio transport closed (the Claude Code session went away /
  // stdin EOF). Nothing to bridge to anymore. Log-before-exit so the reason is in the
  // CHANNEL_LOG rather than an unexplained silent death.
  const priorOnClose = transport.onclose;
  transport.onclose = () => {
    try { if (typeof priorOnClose === "function") priorOnClose(); } catch { /* ignore */ }
    logf("TRANSPORT CLOSED", "MCP stdio EOF — session gone; shutting down");
    stop().catch(() => process.exit(0));
  };

  await channel.mcp.connect(transport);
  process.stderr.write(
    `[${SERVER_NAME}] ready as intelligence "${INTELLIGENCE_ID}" on ${MQTT_URL}\n`
  );

  // Client-disconnect reaping ([CXC-IC-CHANNEL] IC-C16): the MCP SDK's stdio transport only
  // listens for stdin 'data'/'error' — it does NOT fire onclose on stdin EOF ('end'/'close')
  // nor on a broken stdout (EPIPE). So a Claude session that goes away without a clean
  // transport close ORPHANS this bridge (it keeps holding the bus identity — the resume leak).
  // Watch the pipes directly so an orphaned bridge REAPS ITSELF — no manual kill, ever.
  let reaped = false;
  const reap = (why) => {
    if (reaped) return;
    reaped = true;
    logf("REAP", why, "— MCP client gone; shutting down");
    process.stderr.write(`[${SERVER_NAME}] client gone (${why}) — exiting\n`);
    stop().catch(() => process.exit(0));
  };
  process.stdin.on("end", () => reap("stdin EOF"));
  process.stdin.on("close", () => reap("stdin close"));
  process.stdout.on("error", (e) => reap("stdout error " + ((e && e.code) || e)));

  async function stop() {
    await channel.shutdown();
    process.exit(0);
  }
  process.on("SIGTERM", stop);
  process.on("SIGINT", stop);
}

// ── Selftest (no broker, no network) ────────────────────────
// Mocks the MQTT client (in-memory pub/sub) and spies on mcp.notification, then asserts the
// three contract paths: inbound event, reply publish, permission relay + verdict.

function makeFakeMqtt() {
  const subs = new Set();
  const handlers = { message: [], connect: [], error: [] };
  const published = []; // [{topic, payload}]
  return {
    connected: true,
    published,
    on(event, cb) {
      (handlers[event] ||= []).push(cb);
      return this;
    },
    subscribe(topic) {
      subs.add(topic);
    },
    // publish loops back to message handlers, simulating a broker round-trip so an inbound
    // topic delivers to onMqttMessage exactly as a real broker would.
    publish(topic, payload) {
      published.push({ topic, payload });
      if (subs.has(topic)) for (const cb of handlers.message) cb(topic, payload);
    },
    // test helper: inject a message as if it arrived from the broker (bypasses sub-check)
    inject(topic, payload) {
      for (const cb of handlers.message) cb(topic, payload);
    },
    end(_force, _opts, cb) {
      if (cb) cb();
    },
    _fire(event, ...args) {
      for (const cb of handlers[event] || []) cb(...args);
    },
  };
}

async function selftest() {
  const assert = (cond, msg) => {
    if (!cond) {
      process.stderr.write(`FAIL: ${msg}\n`);
      process.exit(1);
    }
  };

  const id = "test-int-01";
  const fake = makeFakeMqtt();
  const ch = createChannel({ id, mqttClient: fake });

  // Spy on channel notifications pushed into the (mocked) session.
  const notifications = [];
  ch.mcp.notification = async (n) => {
    notifications.push(n);
  };

  await ch.connectBus();
  const t = ch.topics;

  // (a) inbound bus message → exactly one channel notification, content + sanitized meta.
  fake.inject(
    t.in,
    JSON.stringify({ text: "hello architect", "sender-name": "david", severity: "high" })
  );
  const inbound = notifications.filter((n) => n.method === "notifications/claude/channel");
  assert(inbound.length === 1, `expected 1 channel notification, got ${inbound.length}`);
  assert(inbound[0].params.content === "hello architect", "inbound content mismatch");
  assert(inbound[0].params.meta.id === id, "inbound meta.id missing/mismatch");
  assert(
    inbound[0].params.meta.sender_name === "david",
    "hyphen key not sanitized to underscore"
  );
  assert(!("sender-name" in inbound[0].params.meta), "raw hyphen key leaked into meta");
  assert(inbound[0].params.meta.severity === "high", "structured field lost from meta");

  // (b) reply tool → publish to compose_projects/intelligence/<id>/out.
  const callToolHandler = ch.mcp._requestHandlers.get("tools/call");
  const replyRes = await callToolHandler(
    { method: "tools/call", params: { name: "reply", arguments: { text: "on it", thread: "T7" } } },
    {}
  );
  assert(replyRes.content[0].text === "sent", "reply tool did not return 'sent'");
  const outPub = fake.published.find((p) => p.topic === t.out);
  assert(!!outPub, `nothing published to ${t.out}`);
  const outBody = JSON.parse(outPub.payload);
  assert(outBody.text === "on it", "reply text not published");
  assert(outBody.thread === "T7", "reply thread not carried");
  assert(outBody.from === id, "reply.from not stamped");

  // (c) permission_request → publish to .../permission/request; verdict → notification.
  const permHandler = ch.mcp._notificationHandlers.get(
    "notifications/claude/channel/permission_request"
  );
  await permHandler({
    method: "notifications/claude/channel/permission_request",
    params: {
      request_id: "abcde",
      tool_name: "Bash",
      description: "Run shell command",
      input_preview: "ls -la",
    },
  });
  const reqPub = fake.published.find((p) => p.topic === t.permissionRequest);
  assert(!!reqPub, `nothing published to ${t.permissionRequest}`);
  assert(JSON.parse(reqPub.payload).request_id === "abcde", "permission request_id not relayed");

  fake.inject(t.permissionVerdict, JSON.stringify({ request_id: "abcde", behavior: "allow" }));
  const verdict = notifications.find(
    (n) => n.method === "notifications/claude/channel/permission"
  );
  assert(!!verdict, "no permission verdict notification emitted");
  assert(verdict.params.request_id === "abcde", "verdict request_id mismatch");
  assert(verdict.params.behavior === "allow", "verdict behavior mismatch");

  // (d) liveness heartbeat → published to compose_projects/intelligence/<id>/channel/alive on
  // connect (reusing this mqtt client), carrying {ts, id}.
  const alivePub = fake.published.find((p) => p.topic === t.alive);
  assert(!!alivePub, `no heartbeat published to ${t.alive}`);
  const aliveBody = JSON.parse(alivePub.payload);
  assert(aliveBody.id === id, "heartbeat id mismatch");
  assert(typeof aliveBody.ts === "number", "heartbeat ts missing/not a number");

  // (e) RESILIENCE: a message-handler error must be caught (logged, not thrown) and the
  // channel must KEEP RUNNING — one poison message can't sever the link. Force handleInbound
  // to throw by making the notification sink throw, route a message through the SAME
  // onMqttMessage the broker uses, and assert it does NOT propagate. Then prove the channel
  // still delivers a subsequent good message.
  const beforeErr = notifications.length;
  ch.mcp.notification = async () => { throw new Error("simulated notification failure"); };
  let threw = false;
  try {
    ch._handlers.onMqttMessage(t.in, JSON.stringify({ text: "poison" }));
  } catch {
    threw = true;
  }
  assert(!threw, "handler error escaped onMqttMessage — a bad message can kill the channel");
  assert(notifications.length === beforeErr, "poison message should not have appended a notification");
  // Restore a working sink and prove the channel survived and still delivers.
  ch.mcp.notification = async (n) => { notifications.push(n); };
  ch._handlers.onMqttMessage(t.in, JSON.stringify({ text: "still alive" }));
  const survivor = notifications.filter(
    (n) => n.method === "notifications/claude/channel" && n.params.content === "still alive"
  );
  assert(survivor.length === 1, "channel did not keep running after a handler error");

  // (f) SINGLE-INSTANCE IDENTITY ([CXC-IC-CHANNEL] / IC-C15): last-writer-wins displacement.
  // A NEWER bridge's liveness beat displaces this (older) bridge exactly once; an OLDER
  // peer's beat and our OWN beat never do — so a /exit+resume or a duplicate concurrent
  // session collapses to one owner with no reconnect-war.
  let displacedCount = 0;
  const chS = createChannel({
    id: "solo",
    mqttClient: makeFakeMqtt(),
    startTs: 1000,
    pid: 4242,
    onDisplaced: () => { displacedCount++; },
  });
  chS.mcp.notification = async () => {};
  await chS.connectBus();
  const ts = chS.topics;
  // an OLDER peer beats (start 500 < 1000) → we KEEP the identity (that peer must yield, not us)
  chS._handlers.onMqttMessage(ts.alive, JSON.stringify({ id: "solo", pid: 999999, start: 500 }));
  assert(displacedCount === 0, "an older peer must NOT displace the newer bridge");
  // our OWN beat (same pid) → ignored
  chS._handlers.onMqttMessage(ts.alive, JSON.stringify({ id: "solo", pid: 4242, start: 1000 }));
  assert(displacedCount === 0, "a self-beat must NOT trigger displacement");
  // a beat for a DIFFERENT id → ignored
  chS._handlers.onMqttMessage(ts.alive, JSON.stringify({ id: "other", pid: 111111, start: 9999 }));
  assert(displacedCount === 0, "a beat for a different id must NOT displace this bridge");
  // a NEWER peer beats (start 2000 > 1000) → we YIELD exactly once (idempotent on repeat beats)
  chS._handlers.onMqttMessage(ts.alive, JSON.stringify({ id: "solo", pid: 111111, start: 2000 }));
  chS._handlers.onMqttMessage(ts.alive, JSON.stringify({ id: "solo", pid: 111111, start: 2000 }));
  assert(displacedCount === 1, `newer peer must displace exactly once, got ${displacedCount}`);

  // (f2) SAME ID, DIFFERENT INSTANCE ([CXC-IC-CHANNEL-INSTANCE], 2026-08-11): the arriving
  // bridge REFUSES and ANNOUNCES; it never evicts a live peer. Three sub-cases, because the
  // rule is exactly a discrimination between them and a test of only one would pass a bridge
  // that refuses everything (or nothing).
  //   1. SAME instance restarting  -> DISPLACE, unchanged (the guarded regression)
  //   2. DIFFERENT instance, we started LATER -> WE refuse, publish contested, tell our session
  //   3. DIFFERENT instance, we started EARLIER (we ARE the incumbent) -> we keep the channel
  let dis1 = 0;
  const chSame = createChannel({
    id: "twin", mqttClient: makeFakeMqtt(), startTs: 1000, pid: 10,
    instance: "sess-A", onDisplaced: () => { dis1++; },
  });
  chSame.mcp.notification = async () => {};
  await chSame.connectBus();
  chSame._handlers.onMqttMessage(chSame.topics.alive, JSON.stringify(
    { id: "twin", pid: 11, start: 2000, instance: "sess-A" }));
  assert(dis1 === 1, "the SAME instance restarting must still DISPLACE (the guarded path)");

  let contested = null, dis2 = 0;
  const chArriving = createChannel({
    id: "twin", mqttClient: makeFakeMqtt(), startTs: 2000, pid: 12,
    instance: "sess-CHILD",
    onDisplaced: () => { dis2++; },
    onContested: (b) => { contested = b; },
  });
  const arrivingNotices = [];
  chArriving.mcp.notification = async (n) => { arrivingNotices.push(n); };
  await chArriving.connectBus();
  chArriving._mqtt.published.length = 0;
  chArriving._handlers.onMqttMessage(chArriving.topics.alive, JSON.stringify(
    { id: "twin", pid: 13, start: 1000, instance: "sess-HOST" }));
  assert(dis2 === 0, "a DIFFERENT instance must never be displaced by us — no eviction");
  assert(contested && contested.incumbent === "sess-HOST" && contested.arriving === "sess-CHILD",
    "the arriving bridge must REFUSE and report both instance identities");
  const cPub = chArriving._mqtt.published.find((p) => p.topic === chArriving.topics.contested);
  assert(!!cPub && JSON.parse(cPub.payload).contested === true,
    `no channel/contested publish on ${chArriving.topics.contested}`);
  assert(arrivingNotices.some((n) => /could NOT take the channel/.test(n.params?.content || "")),
    "the refusing bridge must TELL ITS OWN SESSION it has no channel");
  // idempotent: a second beat from the same live incumbent must not re-announce
  chArriving._mqtt.published.length = 0;
  chArriving._handlers.onMqttMessage(chArriving.topics.alive, JSON.stringify(
    { id: "twin", pid: 13, start: 1000, instance: "sess-HOST" }));
  assert(!chArriving._mqtt.published.some((p) => p.topic === chArriving.topics.contested),
    "the refusal must announce ONCE, not once per beat");

  let dis3 = 0, contested3 = null;
  const chIncumbent = createChannel({
    id: "twin", mqttClient: makeFakeMqtt(), startTs: 1000, pid: 14,
    instance: "sess-HOST",
    onDisplaced: () => { dis3++; },
    onContested: (b) => { contested3 = b; },
  });
  chIncumbent.mcp.notification = async () => {};
  await chIncumbent.connectBus();
  chIncumbent._handlers.onMqttMessage(chIncumbent.topics.alive, JSON.stringify(
    { id: "twin", pid: 15, start: 2000, instance: "sess-CHILD" }));
  assert(dis3 === 0 && contested3 === null,
    "the INCUMBENT keeps the channel and is not evicted by a different arriving instance");

  // (g) DELIVERY-LIVENESS ([CXC-IC-CHANNEL] / IC-C17): unacked injects past the threshold flag
  // the heartbeat delivery "degraded" (console dot RED, not false-green); a client->server ACK
  // (a reply call) clears it — turning a silent non-delivery into a detected, self-correcting one.
  const chD = createChannel({ id: "deliv", mqttClient: makeFakeMqtt() });
  chD.mcp.notification = async () => {};
  await chD.connectBus();
  const td = chD.topics;
  assert(chD._deliveryState === "ok", "a fresh channel must be delivery-ok");
  // inject DELIVERY_DEGRADED_THRESHOLD (default 3) messages, none acked
  for (let i = 0; i < 3; i++) chD._handlers.onMqttMessage(td.in, JSON.stringify({ text: "m" + i }));
  assert(chD._deliveryState === "degraded", "unacked injects past threshold must degrade delivery");
  // the heartbeat must carry delivery:"degraded" so the console can flip the link dot to RED
  chD._mqtt.published.length = 0;
  chD._beat();
  const degBeat = chD._mqtt.published.find((p) => p.topic === td.alive);
  assert(
    degBeat && JSON.parse(degBeat.payload).delivery === "degraded",
    "heartbeat must report delivery degraded so the link dot goes red, not false-green"
  );
  // a reply (client->server ACK) proves the session is surfacing our notifications → self-heal
  const dCall = chD.mcp._requestHandlers.get("tools/call");
  await dCall({ method: "tools/call", params: { name: "reply", arguments: { text: "ack" } } }, {});
  assert(chD._deliveryState === "ok", "a client->server ack (reply) must clear delivery-degraded");

  // (h) CLIENT-DISCONNECT REAPING ([CXC-IC-CHANNEL] / IC-C16): a bridge whose stdin hits EOF
  // must reap ITSELF (the SDK stdio transport does NOT fire onclose on EOF). Spawn the bridge
  // in main mode against a dead broker, let it boot + wire the reaper, then close its stdin and
  // assert it exits(0) on its own — no manual kill.
  const selfPath = fileURLToPath(import.meta.url);
  const reapExit = await new Promise((resolve) => {
    const child = spawn(process.execPath, [selfPath], {
      env: {
        ...process.env,
        INTELLIGENCE_ID: "reap-selftest",
        COMPOSEBUS_MQTT: "mqtt://127.0.0.1:1",   // refused → bridge stays up until stdin EOF
        CHANNEL_LOG: "off",
      },
      stdio: ["pipe", "ignore", "ignore"],
    });
    const killer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch { /* already gone */ }
      resolve("TIMEOUT");
    }, 4000);
    child.on("exit", (code) => { clearTimeout(killer); resolve(code === null ? "SIGNAL" : code); });
    // Let it boot + wire the reaper, then simulate the Claude session going away.
    setTimeout(() => { try { child.stdin.end(); } catch { /* already closed */ } }, 600);
  });
  assert(reapExit === 0, `bridge must exit(0) on stdin EOF (self-reap), got ${reapExit}`);

  process.stdout.write("PASS: composeio-channel selftest (9 contract + resilience + self-heal paths)\n");
  process.stdout.write("  (a) inbound bus message → one channel notification, meta sanitized, id present\n");
  process.stdout.write(`  (b) reply tool → published to ${t.out}\n`);
  process.stdout.write(`  (c) permission_request → ${t.permissionRequest}; verdict → notifications/claude/channel/permission\n`);
  process.stdout.write(`  (d) liveness heartbeat → published to ${t.alive} ({ts, id})\n`);
  process.stdout.write("  (e) handler error caught (not thrown) + channel keeps delivering\n");
  process.stdout.write("  (f) single-instance identity: a newer bridge displaces the older one; older/self/other-id beats do not\n");
  process.stdout.write("  (f2) same id, DIFFERENT instance: the ARRIVING bridge REFUSES, publishes channel/contested and tells its own session; the incumbent is never evicted; a restart of the SAME instance still displaces\n");
  process.stdout.write("  (g) delivery-liveness: unacked injects past threshold -> heartbeat delivery=degraded; a reply ack clears it\n");
  process.stdout.write("  (h) client-disconnect reaping: a bridge exits(0) on stdin EOF (self-reap; no manual kill)\n");
  process.exit(0);
}

if (process.argv.includes("--selftest")) {
  selftest().catch((e) => {
    process.stderr.write(`FAIL: ${e.stack || e.message}\n`);
    process.exit(1);
  });
} else {
  main().catch((e) => {
    // Log the reason to the CHANNEL_LOG BEFORE exiting — a fatal that only hit stderr is
    // invisible in the channel log and left the last death undiagnosable.
    logf("FATAL", (e && (e.stack || e.message)) || String(e));
    process.stderr.write(`[${SERVER_NAME}] fatal: ${(e && (e.stack || e.message)) || e}\n`);
    process.exit(1);
  });
}
