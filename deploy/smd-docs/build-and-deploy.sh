#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REMOTE_USER="davidleeburnett"
REMOTE_HOST="mqtt-broker.local"
REMOTE_DIR="~/smd-docs"

echo "=== SMD Docs: Build & Deploy ==="

# Step 1: Build MkDocs site locally
echo ""
echo "[1/4] Building MkDocs site..."
cd "$PROJECT_ROOT"
pip install -e tools/mkdocs-smd-plugin --quiet 2>/dev/null
mkdocs build
echo "  Site built at $PROJECT_ROOT/site/"

# Step 2: Check if port 80 is taken on the server
echo ""
echo "[2/4] Checking port availability..."
PORT=80
PORT_CHECK=$(ssh "$REMOTE_USER@$REMOTE_HOST" "docker ps --format '{{.Ports}} {{.Names}}' | grep ':80' || true")
if [ -n "$PORT_CHECK" ]; then
    echo "  WARNING: Port 80 is in use:"
    echo "  $PORT_CHECK"
    echo "  Falling back to port 8080"
    PORT=8080
else
    echo "  Port 80 is available"
fi

# Step 3: rsync site and config files to server
echo ""
echo "[3/4] Syncing files to $REMOTE_HOST..."
ssh "$REMOTE_USER@$REMOTE_HOST" "mkdir -p $REMOTE_DIR"

rsync -az --delete "$PROJECT_ROOT/site/" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/site/"
echo "  Synced site/ directory"

# Generate docker-compose with correct port (may differ from template if 80 is taken)
if [ "$PORT" -ne 80 ]; then
    echo "  Adjusting docker-compose.yml for port $PORT..."
    sed "s/\"80:80\"/\"${PORT}:80\"/" "$SCRIPT_DIR/docker-compose.yml" | \
        ssh "$REMOTE_USER@$REMOTE_HOST" "cat > $REMOTE_DIR/docker-compose.yml"
else
    rsync -az "$SCRIPT_DIR/docker-compose.yml" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/docker-compose.yml"
fi

rsync -az "$SCRIPT_DIR/Dockerfile" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/Dockerfile"
rsync -az "$SCRIPT_DIR/nginx.conf" "$REMOTE_USER@$REMOTE_HOST:$REMOTE_DIR/nginx.conf"
echo "  Synced deployment configs"

# Step 4: Build and start the container on the server
echo ""
echo "[4/4] Building and starting container on $REMOTE_HOST..."
ssh "$REMOTE_USER@$REMOTE_HOST" "cd $REMOTE_DIR && docker compose up -d --build"

echo ""
echo "=== Deploy complete ==="
echo "SMD Docs available at: http://$REMOTE_HOST:$PORT"
