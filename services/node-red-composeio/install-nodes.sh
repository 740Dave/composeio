#!/usr/bin/env bash
# Run this script ON mqtt-broker.local to install ComposeIO nodes into Node-RED
set -euo pipefail

cd ~/composeio/services/node-red-composeio

CONTAINER=$(docker compose ps -q node-red)
echo "Container: $CONTAINER"

# Create dir and copy package into the container
docker exec "$CONTAINER" mkdir -p /data/node_modules/@composeio/node-red-contrib-composeio
docker cp package/package.json "$CONTAINER":/data/node_modules/@composeio/node-red-contrib-composeio/
docker cp package/nodes "$CONTAINER":/data/node_modules/@composeio/node-red-contrib-composeio/
docker cp package/lib "$CONTAINER":/data/node_modules/@composeio/node-red-contrib-composeio/

# Install js-yaml dependency inside the container
docker exec "$CONTAINER" sh -c 'cd /data/node_modules/@composeio/node-red-contrib-composeio && npm install --production --omit=dev'

# Restart to load new nodes
docker compose restart node-red

echo "Waiting for Node-RED to start..."
sleep 8

# Verify
if curl -sf http://localhost:1880 > /dev/null 2>&1; then
    echo "SUCCESS: Node-RED is running at http://mqtt-broker.local:1880"
else
    echo "WARNING: Node-RED may still be starting. Check: curl http://localhost:1880"
fi
