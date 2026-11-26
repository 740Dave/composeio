# ComposeIO Node-RED Deployment

## Architecture

Node-RED runs on `mqtt-broker.local` as a Docker container with `network_mode: host`, co-located with the MQTT broker at port 1883. This matches the MCP server deployment pattern.

## Prerequisites

- SSH access to `mqtt-broker.local` as your deploy user. `build-and-deploy.sh` logs in as the
  invoking user (`$USER`); set `NODERED_USER` (and `NODERED_HOST`) to override.
- Docker and Docker Compose installed on mqtt-broker.local
- MQTT broker running on mqtt-broker.local:1883

## Quick Deploy

From this directory:

```bash
bash build-and-deploy.sh
```

This script:
1. Creates the remote directory structure
2. Copies the package via rsync
3. Starts the Node-RED Docker container
4. Installs the ComposeIO custom nodes
5. Restarts Node-RED to load the new nodes

## Manual Deploy

### 1. Copy files to server

```bash
rsync -avz --exclude node_modules --exclude .git --exclude test \
  ./ "$USER@mqtt-broker.local:~/composeio/services/node-red-composeio/package/"

scp docker-compose.yml "$USER@mqtt-broker.local:~/composeio/services/node-red-composeio/"
```

### 2. SSH into server and start Node-RED

```bash
ssh "$USER@mqtt-broker.local"
cd ~/composeio/services/node-red-composeio
docker compose up -d
```

### 3. Install custom nodes

```bash
CONTAINER=$(docker compose ps -q node-red)
docker cp package/. "$CONTAINER":/data/node_modules/@composeio/node-red-contrib-composeio/
docker exec "$CONTAINER" sh -c 'cd /data/node_modules/@composeio/node-red-contrib-composeio && npm install --production --omit=dev'
docker compose restart node-red
```

### 4. Verify

- Open http://mqtt-broker.local:1880
- Check palette for "ComposeIO" category with: composeio in, composeio out, state engine
- Create an mqtt-broker config node pointing to localhost:1883
- Create a composeio-config node referencing the mqtt-broker

## Updating

After making changes to the custom nodes, re-run:

```bash
bash build-and-deploy.sh
```

## Configuration

### MQTT Broker Setup in Node-RED

1. Add an `mqtt-broker` config node:
   - Server: `localhost`
   - Port: `1883`
   - Client ID: `nodered-main` (or leave blank for auto)

2. Add a `composeio-config` node:
   - MQTT Broker: select the broker from step 1
   - Client ID: `nodered-main`
   - Default Scope: `global`
   - Config Path: `/composeio/configs/compose.yaml` (optional, if mounted)

### Docker Volume

The Node-RED data is persisted in the `nodered-data` Docker volume. Flows, credentials, and installed packages survive container restarts.

To back up flows:
```bash
docker cp $(docker compose ps -q node-red):/data/flows.json ./flows-backup.json
```

## Ports

| Service | Port | Protocol |
|---------|------|----------|
| Node-RED Editor | 1880 | HTTP |
| MQTT Broker | 1883 | MQTT (accessed via localhost inside container) |
