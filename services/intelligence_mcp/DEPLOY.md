# Deploying ComposeIO Intelligence MCP Server to mqtt-broker.local

## Prerequisites

- SSH access to `mqtt-broker.local` as `davidleeburnett` (passwordless sudo)
- Docker running on the target (verified: Docker 29.2.1)
- Mosquitto MQTT broker on port 1883 (systemd service, configured with `allow_anonymous true` on `0.0.0.0`)

## Port Assignment

The MCP server runs on **port 8433**. This is set via the `MCP_PORT` environment variable in `docker-compose.yml`.

Ports 8420-8425 are used by other ComposeIO services (bus_storage, document_store, etc.).
Ports 8430-8432 are used by Haven services.
Port 8433 is the first free port in the ComposeIO range.

If port 8433 is taken, check available ports:
```bash
ssh mqtt-broker.local "ss -tlnp | grep '0.0.0.0' | awk '{print \$4}' | sed 's/.*://' | sort -n"
```

## Deployment Steps

### 1. Copy source files to target

```bash
# From the project root (where this file lives)
rsync -av --exclude='__pycache__' --exclude='*.pyc' \
  . mqtt-broker.local:~/composeio/services/intelligence_mcp/

# Copy the composeio Python library (dependency, not on PyPI)
rsync -av --exclude='__pycache__' --exclude='*.pyc' \
  ../../python/ mqtt-broker.local:~/composeio/python/
```

### 2. Build the Docker image on the target

```bash
ssh mqtt-broker.local "cd ~/composeio/services/intelligence_mcp && bash build.sh"
```

`build.sh` copies the composeio library into the Docker build context, builds the image as `composeio-intelligence-mcp:latest`, then cleans up.

### 3. Deploy

```bash
ssh mqtt-broker.local "cd ~/composeio/services/intelligence_mcp && docker compose up -d"
```

### 4. Verify

```bash
# Check container is running
ssh mqtt-broker.local "docker ps --filter name=composeio-intelligence-mcp"

# Check logs (should show "MCP server listening on http://0.0.0.0:8433/sse")
ssh mqtt-broker.local "docker logs composeio-intelligence-mcp 2>&1 | tail -5"

# Test health endpoint (fallback mode only)
curl -s http://mqtt-broker.local:8433/health

# Test SSE endpoint
curl -s -N http://mqtt-broker.local:8433/sse &
# Should receive an "endpoint" event with a session-specific messages URL
```

### 5. Configure Claude Code

Add to `~/.claude/settings.json` (or project `.claude/settings.json`):

```json
{
  "mcpServers": {
    "composeio": {
      "type": "sse",
      "url": "http://mqtt-broker.local:8433/sse"
    }
  }
}
```

## Rebuild After Code Changes

```bash
# Re-copy changed files
rsync -av --exclude='__pycache__' --exclude='*.pyc' \
  . mqtt-broker.local:~/composeio/services/intelligence_mcp/
rsync -av --exclude='__pycache__' --exclude='*.pyc' \
  ../../python/ mqtt-broker.local:~/composeio/python/

# Rebuild and redeploy
ssh mqtt-broker.local "cd ~/composeio/services/intelligence_mcp && \
  bash build.sh && \
  docker compose down && \
  docker compose up -d"
```

## Troubleshooting

**Port already in use**: Check `ss -tlnp | grep 8433` on the target. Change `MCP_PORT` in `docker-compose.yml` if needed.

**MQTT not connecting**: Verify Mosquitto is running (`systemctl status mosquitto`) and bound to `0.0.0.0:1883` (check `/etc/mosquitto/conf.d/composeio.conf`).

**Container crash loop**: `docker logs composeio-intelligence-mcp 2>&1 | tail -30` for the traceback.

**MCP SDK mode vs fallback**: The container doesn't include the `mcp` Python SDK. It runs in fallback aiohttp mode. This is functionally equivalent — same SSE/JSON-RPC 2.0 interface. To enable SDK mode, add `mcp>=1.0 starlette uvicorn` to `requirements.txt` and rebuild.
