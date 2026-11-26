# composeio-channel — the Intelligence Console transport

The pure, platform-level transport that bridges a running Claude Code session to
**ComposeBus**. It is the foundation of the Intelligence Console
(`docs/intelligence-console/DESIGN-HANDOFF.md`): a Claude instance becomes a bus peer whose
duplex "channel" maps to `intelligence/<id>/{in,out}`.

It is a Claude Code **channel** — an MCP stdio server that PUSHES bus messages into the
session as `<channel source="composeio-channel" …>` events, and exposes a `reply` tool that
publishes back onto the bus. Optional permission relay lets tool-approval prompts be
answered from another bus peer.

This is Haven-independent. Haven's `haven-dispatch-channel` was mined for SHAPE only
(MCP-stdio + lifecycle + meta sanitization); the HTTP-registry routing is replaced with
ComposeBus pub/sub, so an Intelligence is addressed by a bus topic instead of a listener
port.

## Topic map

With `INTELLIGENCE_ID=<id>` (default `architect`):

| Direction | Topic | Purpose |
| --------- | ----- | ------- |
| inbound   | `intelligence/<id>/in` | bus message → `<channel>` event in the session |
| outbound  | `intelligence/<id>/out` | `reply` tool → published to the bus |
| outbound  | `intelligence/<id>/permission/request` | relayed permission prompt |
| inbound   | `intelligence/<id>/permission/verdict` | `{request_id, behavior}` → applied verdict |

## Message shapes

**Inbound** (`…/in`) — a JSON object or a plain string. For an object, the body comes from
`text` / `content` / `body` / `message`; every other scalar field becomes a `<channel>`
attribute (keys sanitized to `[A-Za-z0-9_]`). The attribute `id` (the Intelligence id) is
always present.

```
{ "text": "status?", "sender-name": "david", "severity": "high" }
```
arrives as:
```
<channel source="composeio-channel" id="architect" sender_name="david" severity="high">status?</channel>
```

**Outbound** (`…/out`) — the `reply` tool publishes:
```
{ "from": "architect", "text": "on it", "ts": 1720000000000, "to": "…?", "thread": "…?" }
```

**Permission** — `…/permission/request` carries `{request_id, tool_name, description,
input_preview}`; publish `{request_id, behavior: "allow"|"deny"}` to `…/permission/verdict`
to answer.

## Capabilities declared

```jsonc
capabilities: {
  experimental: {
    "claude/channel": {},             // register the notification listener
    "claude/channel/permission": {},  // opt in to remote permission relay
  },
  tools: {},                          // two-way: the reply tool
}
```

## reply tool schema

```jsonc
{
  "name": "reply",
  "description": "Publish a reply from this Intelligence back onto ComposeBus (intelligence/<id>/out).",
  "inputSchema": {
    "type": "object",
    "properties": {
      "text":   { "type": "string", "description": "The message body to publish." },
      "to":     { "type": "string", "description": "Optional addressee (recipient intelligence id)." },
      "thread": { "type": "string", "description": "Optional thread / correlation id." }
    },
    "required": ["text"]
  }
}
```

## Config (env)

| Var | Default | Meaning |
| --- | ------- | ------- |
| `INTELLIGENCE_ID` | `architect` | this Intelligence's id (topics + `meta.id`) |
| `COMPOSEBUS_MQTT` | `mqtt://192.0.2.75:1883` | ComposeBus MQTT broker |

No secrets. The MCP server name is always `composeio-channel` (→ `<channel source=…>`); the
Intelligence id is carried separately in meta and in every topic.

## Register with a session

Add to your project `.mcp.json` (absolute path so it resolves from any project):

```json
{
  "mcpServers": {
    "composeio-channel": {
      "command": "node",
      "args": ["${COMPOSEIO_HOME}/intelligence/channel/channel.mjs"],
      "env": {
        "INTELLIGENCE_ID": "architect",
        "COMPOSEBUS_MQTT": "mqtt://192.0.2.75:1883"
      }
    }
  }
}
```

Then launch the session with the channel opted in. During the research preview a custom channel
isn't on the allowlist, so it must be loaded with the **development-channels** flag:

```bash
claude --dangerously-load-development-channels server:composeio-channel
```

> **⚠ Do NOT also pass `--channels server:composeio-channel`.** Naming the same server in **both**
> `--channels` and `--dangerously-load-development-channels` **double-registers** it and **breaks
> the channel notification listener** — inbound bus messages stop arriving as `<channel>` events.
> This was found on the glass and is bug #1 in the launcher rebuild; see the `CHANNEL_FLAGS`
> comment in [`../launcher/launcher.py`](../launcher/launcher.py). `--channels` is for
> *allowlisted* channels only and must never also name a dev channel.

In practice you rarely type this yourself — [`../launcher/transport.sh`](../launcher/transport.sh)
re-hosts a session under the launcher, which spawns `claude` with exactly the single correct flag.

## Selftest (no broker, no network)

```bash
node channel.mjs --selftest
# or: npm run selftest
```

Mocks the MQTT client (in-memory pub/sub) and spies on `mcp.notification`, then asserts the
three contract paths: (a) an inbound `…/in` message yields exactly one
`notifications/claude/channel` with sanitized meta + `id`; (b) the `reply` tool publishes to
`…/out`; (c) a `permission_request` publishes to `…/permission/request` and an inbound
`…/permission/verdict` emits `notifications/claude/channel/permission` with the matching
`request_id` + `behavior`.

Expected output:

```
PASS: composeio-channel selftest (3/3 contract paths)
  (a) inbound bus message → one channel notification, meta sanitized, id present
  (b) reply tool → published to intelligence/test-int-01/out
  (c) permission_request → intelligence/test-int-01/permission/request; verdict → notifications/claude/channel/permission
```
