# ComposeIO Intelligence MCP Server

Bridges MCP tool calls from Claude Code (or any MCP-capable agent) to the ComposeIO bus. The Intelligence never touches MQTT directly -- this server is the bridge.

**Spec**: `spec/09-composeio-intelligence-mcp-server.smd`

## Setup

### 1. Install composeio library

```bash
pip install -e /path/to/composeio/python/composebus -e /path/to/composeio/python
```

`composeio` depends on the ComposeBus kernel distribution `composebus==0.1.2`, which
lives beside it at `python/composebus`; the one command installs both.
Non-editable equivalent:

```bash
pip install /path/to/composeio/python/composebus /path/to/composeio/python
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Run

```bash
# Default: 0.0.0.0:8430, MQTT on localhost:1883
python -m intelligence_mcp

# With custom config
python -m intelligence_mcp --host 0.0.0.0 --port 8430 --mqtt-broker 198.51.100.17

# Or via environment variables
MQTT_BROKER=198.51.100.17 MCP_PORT=8430 python -m intelligence_mcp
```

### 4. Docker

```bash
docker compose up -d
```

### 5. Configure Claude Code

Add to your Claude Code MCP config (`~/.claude/settings.json` or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "composeio": {
      "type": "sse",
      "url": "http://mqtt-broker.local:8430/sse"
    }
  }
}
```

## Architecture

```
Claude Code (Mac)  --SSE/HTTP-->  MCP Server (mqtt-broker.local:8430)
                                      |
                                      +-- MQTT client (localhost:1883)
                                      |
                                      +-- Intelligence lifecycle (join/heartbeat/leave)
                                      |
                                      +-- Tool <-> bus message mapping
```

The MCP server IS a ComposeApp with dual-bus architecture:
- **Internal bus**: in-process tool state and correlations
- **External bus**: MQTT to localhost:1883 for Intelligence namespace traffic

## Tools

24 tools organized by workflow phase:

| Category | Tools |
|----------|-------|
| Observation | `get_state`, `get_layout`, `validate`, `get_namespaces` |
| Structure | `add_module`, `remove_module`, `add_widget`, `remove_widget`, `add_namespace` |
| Layout | `move_widget`, `resize_widget` |
| Config | `set_property` |
| Binding | `create_binding`, `modify_binding`, `remove_binding` |
| File | `save`, `load`, `export` |
| Batch | `batch_begin`, `batch_end` |
| Intent | `declare_intent`, `suggest` |
| Monitor | `bus_status`, `publish_raw` |

All tools use the `composeio_` prefix (e.g., `composeio_add_widget`).
