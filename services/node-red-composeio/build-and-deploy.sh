#!/usr/bin/env bash
set -euo pipefail

# ComposeIO Node-RED deployment script
# Copies the custom nodes package to mqtt-broker.local, installs it,
# and restarts the Node-RED container.

REMOTE_USER="davidleeburnett"
REMOTE_HOST="mqtt-broker.local"
REMOTE_DIR="~/composeio/services/node-red-composeio"
PACKAGE_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=== ComposeIO Node-RED Deploy ==="
echo "Package: ${PACKAGE_DIR}"
echo "Target:  ${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}"
echo ""

# Step 1: Ensure remote directory exists
echo "[1/5] Creating remote directory..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/package"

# Step 2: Copy the package files (excluding node_modules, test, .git)
echo "[2/5] Copying package to remote..."
rsync -avz --delete \
    --exclude 'node_modules' \
    --exclude '.git' \
    --exclude 'test' \
    "${PACKAGE_DIR}/" \
    "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/package/"

# Step 3: Copy docker-compose.yml to the project root
echo "[3/5] Ensuring docker-compose.yml is in place..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" "cp ${REMOTE_DIR}/package/docker-compose.yml ${REMOTE_DIR}/docker-compose.yml"

# Step 4: Start or restart Node-RED container
echo "[4/5] Starting Node-RED container..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" "cd ${REMOTE_DIR} && docker compose up -d"

# Step 5: Install the package into the container's /data directory
echo "[5/5] Installing ComposeIO nodes into Node-RED..."
ssh "${REMOTE_USER}@${REMOTE_HOST}" << 'ENDSSH'
cd ~/composeio/services/node-red-composeio

# Get the container name
CONTAINER=$(docker compose ps -q node-red)
if [ -z "$CONTAINER" ]; then
    echo "ERROR: Node-RED container not found"
    exit 1
fi

# Copy the package into the container's /data directory
docker cp package/. "$CONTAINER":/data/node_modules/@composeio/node-red-contrib-composeio/

# Install dependencies inside the container
docker exec "$CONTAINER" sh -c 'cd /data/node_modules/@composeio/node-red-contrib-composeio && npm install --production --omit=dev'

# Restart to load new nodes
docker compose restart node-red

echo ""
echo "Waiting for Node-RED to start..."
sleep 5

# Verify Node-RED is running
if curl -sf http://localhost:1880 > /dev/null 2>&1; then
    echo "Node-RED is running at http://mqtt-broker.local:1880"
else
    echo "WARNING: Node-RED may not be fully started yet. Check: curl http://mqtt-broker.local:1880"
fi
ENDSSH

echo ""
echo "=== Deploy complete ==="
echo "Access Node-RED at: http://mqtt-broker.local:1880"
echo "ComposeIO nodes should appear in the palette under 'ComposeIO' category."
