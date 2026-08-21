"""ComposeIO Intelligence MCP Server.

Bridges MCP tool calls from Claude Code (or any MCP-capable agent) to the
ComposeIO bus. The Intelligence never touches MQTT directly -- this server
is the bridge.

See: spec/09-composeio-intelligence-mcp-server.smd
"""

__version__ = "0.1.0"
