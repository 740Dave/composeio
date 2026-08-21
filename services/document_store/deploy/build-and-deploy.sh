#!/usr/bin/env bash
# Build and deploy Document Store to mqtt-broker.local
#
# Usage:
#   ./deploy/build-and-deploy.sh              # Full build + deploy
#   ./deploy/build-and-deploy.sh --no-build   # Deploy only (skip Docker build)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
REMOTE_HOST="mqtt-broker.local"
REMOTE_DIR="/home/davidleeburnett/document-store"

NO_BUILD=false
for arg in "$@"; do
    case "$arg" in
        --no-build) NO_BUILD=true ;;
    esac
done

echo "=== Document Store Deploy ==="
echo "Project: $PROJECT_DIR"
echo "Remote:  $REMOTE_HOST:$REMOTE_DIR"
echo ""

# Step 1: Sync to remote
echo "[1/3] Syncing to $REMOTE_HOST..."
ssh "$REMOTE_HOST" "mkdir -p $REMOTE_DIR"
rsync -avz --delete \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.git' \
    --exclude='spec/' \
    --exclude='docs/spec-impl-gap-reports/' \
    "$PROJECT_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"

# Step 2: Build on remote
if [ "$NO_BUILD" = false ]; then
    echo "[2/3] Building Docker image on remote..."
    ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose -f deploy/docker-compose.yml build"
else
    echo "[2/3] Skipping build (--no-build)"
fi

# Step 3: Deploy
echo "[3/3] Starting container..."
ssh "$REMOTE_HOST" "cd $REMOTE_DIR && docker compose -f deploy/docker-compose.yml up -d"

echo ""
echo "=== Deploy complete ==="
echo "Document Store: http://$REMOTE_HOST:8421"
echo "Health check:   http://$REMOTE_HOST:8421/health"
echo "Status:         http://$REMOTE_HOST:8421/api/v1/status"
echo "Collections:    http://$REMOTE_HOST:8421/api/v1/collections"
