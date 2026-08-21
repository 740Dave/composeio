"""ComposeIO Intelligence MCP Server -- main entry point.

Implements MCP-ARCH from spec 09. Creates an SSE server that bridges MCP tool
calls from Claude Code (or any MCP-capable agent) to the ComposeIO bus.

The server IS a ComposeApp (MCP-ARCH-APP) with dual-bus architecture:
- Internal bus: manages tool state, correlations, heartbeat, connections
- External bus: MQTT to localhost:1883 for all Intelligence namespace traffic

Two modes:
1. MCP SDK mode (if `mcp` package installed): uses Server + SseServerTransport
2. Fallback mode: raw aiohttp SSE + JSON-RPC 2.0 message handling

Usage:
    python -m intelligence_mcp.server
    python -m intelligence_mcp.server --host 0.0.0.0 --port 8430
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import logging
import os
import sys
import time
import uuid
from typing import Any

# ComposeIO imports
from composeio.app import ComposeApp

from .intelligence import IntelligenceSession
from .tools import ToolExecutor, get_tool_definitions

logger = logging.getLogger("composeio.mcp.server")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8430
DEFAULT_MQTT_BROKER = "localhost"
DEFAULT_MQTT_PORT = 1883


def get_config() -> dict:
    """Read configuration from environment variables."""
    return {
        "host": os.environ.get("MCP_HOST", DEFAULT_HOST),
        "port": int(os.environ.get("MCP_PORT", str(DEFAULT_PORT))),
        "mqtt_broker": os.environ.get("MQTT_BROKER", DEFAULT_MQTT_BROKER),
        "mqtt_port": int(os.environ.get("MQTT_PORT", str(DEFAULT_MQTT_PORT))),
    }


# ---------------------------------------------------------------------------
# ComposeApp setup for the MCP server
# ---------------------------------------------------------------------------

def create_compose_app(config: dict) -> ComposeApp:
    """Create and configure the ComposeApp instance for the MCP server.

    Exported namespaces (internal -> external):
        composer/intelligence/joined
        composer/intelligence/left
        composer/intelligence/heartbeat
        composer/intelligence/intent
        composer/intelligence/request_state
        composer/intelligence/request_layout
        composer/intelligence/validate
        composer/intelligence/suggestion

    Imported namespaces (external -> internal):
        composer/intelligence/error
        composer/intelligence/state_snapshot
        composer/intelligence/layout_snapshot
        composer/intelligence/validation_result
        composer/intelligence/cancel
    """
    app = ComposeApp(app_id="composeio-intelligence-mcp")

    # Register exported namespaces (Intelligence publishes these)
    exported = [
        "composer/intelligence/joined",
        "composer/intelligence/left",
        "composer/intelligence/heartbeat",
        "composer/intelligence/intent",
        "composer/intelligence/request_state",
        "composer/intelligence/request_layout",
        "composer/intelligence/validate",
        "composer/intelligence/suggestion",
    ]
    for ns in exported:
        app.register(ns, engine="binary", export=True)

    # Register imported namespaces (ComposeUX publishes these, we listen)
    imported = [
        "composer/intelligence/error",
        "composer/intelligence/state_snapshot",
        "composer/intelligence/layout_snapshot",
        "composer/intelligence/validation_result",
        "composer/intelligence/cancel",
    ]
    for ns in imported:
        app.import_namespace(ns)

    # Connect MQTT transport
    app.connect_transport(
        "mqtt",
        broker=config["mqtt_broker"],
        port=config["mqtt_port"],
        client_id="composeio-intelligence-mcp",
    )

    return app


# ---------------------------------------------------------------------------
# MCP SDK mode
# ---------------------------------------------------------------------------

def _try_mcp_sdk_server(app: ComposeApp, config: dict) -> bool:
    """Attempt to run using the MCP Python SDK. Returns False if not available."""
    try:
        from mcp.server import Server
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route
        import uvicorn
    except ImportError:
        return False

    mcp_server = Server("composeio-intelligence")
    sessions: dict[str, tuple[IntelligenceSession, ToolExecutor]] = {}
    _current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
        "mcp_session_id", default=None
    )

    def _get_session() -> tuple[IntelligenceSession, ToolExecutor]:
        """Get or create session for the current connection."""
        session_id = _current_session_id.get() or "default"
        if session_id not in sessions:
            session = IntelligenceSession(app.external_bus)
            executor = ToolExecutor(session, app.external_bus, app)
            sessions[session_id] = (session, executor)
        return sessions[session_id]

    @mcp_server.list_tools()
    async def list_tools():
        from mcp.types import Tool
        definitions = get_tool_definitions()
        return [
            Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["inputSchema"],
            )
            for d in definitions
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict):
        from mcp.types import TextContent
        session, executor = _get_session()
        results = await executor.execute(name, arguments)
        return [TextContent(type="text", text=r["text"]) for r in results]

    # Create SSE transport and Starlette app
    sse = SseServerTransport("/messages/")

    async def handle_sse(request):
        session_id = str(uuid.uuid4())
        _current_session_id.set(session_id)
        _get_session()  # Ensure session is created for this connection
        try:
            async with sse.connect_sse(
                request.scope, request.receive, request._send
            ) as streams:
                await mcp_server.run(
                    streams[0], streams[1], mcp_server.create_initialization_options()
                )
        finally:
            if session_id in sessions:
                intel_session, _ = sessions.pop(session_id)
                await intel_session.leave()

    async def handle_messages(request):
        await sse.handle_post_message(request.scope, request.receive, request._send)

    starlette_app = Starlette(
        routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages/", endpoint=handle_messages, methods=["POST"]),
        ],
    )

    logger.info(f"Starting MCP server (SDK mode) on {config['host']}:{config['port']}")
    uvicorn.run(starlette_app, host=config["host"], port=config["port"])
    return True


# ---------------------------------------------------------------------------
# Fallback mode: aiohttp SSE + JSON-RPC 2.0
# ---------------------------------------------------------------------------

class MCPSession:
    """An active MCP client session over SSE."""

    def __init__(self, session_id: str, app: ComposeApp):
        self.session_id = session_id
        self.intelligence = IntelligenceSession(app.external_bus)
        self.executor = ToolExecutor(self.intelligence, app.external_bus, app)
        self.message_queue: asyncio.Queue = asyncio.Queue()
        self.messages_endpoint = f"/messages/{session_id}"
        self.created_at = time.time()


async def run_fallback_server(app: ComposeApp, config: dict) -> None:
    """Run the MCP server using aiohttp with SSE transport and JSON-RPC 2.0."""
    from aiohttp import web

    sessions: dict[str, MCPSession] = {}

    async def handle_sse(request: web.Request) -> web.StreamResponse:
        """SSE endpoint -- establishes a long-lived event stream."""
        session_id = str(uuid.uuid4())
        session = MCPSession(session_id, app)
        sessions[session_id] = session

        response = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "Access-Control-Allow-Origin": "*",
            },
        )
        await response.prepare(request)

        # Send the endpoint event so the client knows where to POST
        endpoint_data = json.dumps({"endpoint": session.messages_endpoint})
        await response.write(
            f"event: endpoint\ndata: {session.messages_endpoint}\n\n".encode()
        )

        logger.info(f"SSE client connected: {session_id}")

        try:
            while True:
                try:
                    # Wait for messages from the tool handler to stream back
                    message = await asyncio.wait_for(
                        session.message_queue.get(), timeout=30
                    )
                    await response.write(
                        f"event: message\ndata: {json.dumps(message)}\n\n".encode()
                    )
                except asyncio.TimeoutError:
                    # Send keepalive comment
                    await response.write(b": keepalive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            # Auto-leave on disconnect (MCP-C7)
            await session.intelligence.leave()
            sessions.pop(session_id, None)
            logger.info(f"SSE client disconnected: {session_id}")

        return response

    async def handle_messages(request: web.Request) -> web.Response:
        """Handle JSON-RPC 2.0 messages from the MCP client."""
        # Extract session ID from URL path
        session_id = request.match_info.get("session_id")
        session = sessions.get(session_id)
        if session is None:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Invalid session"}},
                status=404,
            )

        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}},
                status=400,
            )

        # Handle JSON-RPC request
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")

        response = await _handle_jsonrpc(session, method, params, req_id)

        # Queue the response for SSE streaming (skip for notifications)
        if response is not None:
            await session.message_queue.put(response)

        return web.json_response({"status": "ok"})

    async def handle_health(request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({
            "status": "healthy",
            "app_id": "composeio-intelligence-mcp",
            "sessions": len(sessions),
            "uptime": time.time() - start_time,
        })

    start_time = time.time()

    web_app = web.Application()
    web_app.router.add_get("/sse", handle_sse)
    web_app.router.add_post("/messages/{session_id}", handle_messages)
    web_app.router.add_get("/health", handle_health)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, config["host"], config["port"])
    await site.start()

    logger.info(
        f"MCP server (fallback mode) listening on "
        f"http://{config['host']}:{config['port']}/sse"
    )

    # Run forever
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


async def _handle_jsonrpc(
    session: MCPSession, method: str, params: dict, req_id: Any
) -> dict:
    """Handle a JSON-RPC 2.0 request and return a response."""

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "composeio-intelligence",
                    "version": "0.1.0",
                },
            },
        }

    if method == "notifications/initialized":
        # Notifications must not generate a response per JSON-RPC 2.0
        return None

    if method == "tools/list":
        tools = get_tool_definitions()
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools},
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        content = await session.executor.execute(tool_name, arguments)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": content, "isError": False},
        }

    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }

    # Unknown method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    """Main entry point for the MCP server."""
    parser = argparse.ArgumentParser(description="ComposeIO Intelligence MCP Server")
    parser.add_argument("--host", default=None, help="Host to bind to")
    parser.add_argument("--port", type=int, default=None, help="Port to listen on")
    parser.add_argument("--mqtt-broker", default=None, help="MQTT broker hostname")
    parser.add_argument("--mqtt-port", type=int, default=None, help="MQTT broker port")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    args = parser.parse_args()

    # Configure logging (MCP-S3: log all tool calls at INFO)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    # Build config from env vars + CLI overrides
    config = get_config()
    if args.host:
        config["host"] = args.host
    if args.port:
        config["port"] = args.port
    if args.mqtt_broker:
        config["mqtt_broker"] = args.mqtt_broker
    if args.mqtt_port:
        config["mqtt_port"] = args.mqtt_port

    logger.info(f"Configuration: {config}")

    # Create the ComposeApp
    compose_app = create_compose_app(config)
    compose_app.start()
    logger.info("ComposeApp started (MQTT transport connected)")

    # Try MCP SDK first, fall back to aiohttp
    if not _try_mcp_sdk_server(compose_app, config):
        logger.info("MCP SDK not available, using fallback aiohttp SSE server")
        try:
            asyncio.run(run_fallback_server(compose_app, config))
        except KeyboardInterrupt:
            pass
        finally:
            compose_app.stop()
            logger.info("Server stopped")


if __name__ == "__main__":
    main()
