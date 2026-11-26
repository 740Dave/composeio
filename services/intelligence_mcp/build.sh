#!/bin/bash
# Build the ComposeIO Intelligence MCP Server Docker image.
#
# The composeio library is a local package (not on PyPI), so we copy it
# into the build context before running docker build.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSEIO_LIB="$SCRIPT_DIR/../../python"

echo "==> Copying composeio library into build context..."
rm -rf "$SCRIPT_DIR/composeio_lib"
mkdir -p "$SCRIPT_DIR/composeio_lib"
cp -r "$COMPOSEIO_LIB/composeio" "$SCRIPT_DIR/composeio_lib/composeio"
cp "$COMPOSEIO_LIB/pyproject.toml" "$SCRIPT_DIR/composeio_lib/"
# composeio depends on the ComposeBus kernel distribution (composebus==0.1.2,
# [SPEC:CXC-COMPOSEBUS-KERNEL] §7); ship it beside composeio so the Dockerfile's
# single pip command resolves both locally.
mkdir -p "$SCRIPT_DIR/composeio_lib/composebus"
cp -r "$COMPOSEIO_LIB/composebus/composebus" "$SCRIPT_DIR/composeio_lib/composebus/composebus"
cp "$COMPOSEIO_LIB/composebus/pyproject.toml" "$COMPOSEIO_LIB/composebus/README.md" "$COMPOSEIO_LIB/composebus/LICENSE" "$SCRIPT_DIR/composeio_lib/composebus/"

echo "==> Building Docker image..."
docker build -t composeio-intelligence-mcp:latest "$SCRIPT_DIR"

echo "==> Cleaning up build context..."
rm -rf "$SCRIPT_DIR/composeio_lib"

echo "==> Done. Run with:"
echo "    docker compose up -d"
echo "    # or push to local registry:"
echo "    docker tag composeio-intelligence-mcp:latest mqtt-broker.local:5000/composeio-intelligence-mcp:latest"
echo "    docker push mqtt-broker.local:5000/composeio-intelligence-mcp:latest"
