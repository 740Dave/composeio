"""MCP tool definitions and handlers for the ComposeIO Intelligence API.

Implements MCP-TOOLS from spec 09. Each tool maps to one or more bus messages.
Tool names use the composeio_ prefix. All tools tag messages with the agent_id
as source (MCP-C2).

Tool categories:
    Observation: get_state, get_layout, validate, get_namespaces
    Structure:   add_module, remove_module, add_widget, remove_widget, add_namespace
    Layout:      move_widget, resize_widget
    Config:      set_property
    Binding:     create_binding, modify_binding, remove_binding
    File:        save, load, export
    Batch:       batch_begin, batch_end
    Intent:      declare_intent, suggest
    Monitor:     bus_status, publish_raw
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("composeio.mcp.tools")


# ---------------------------------------------------------------------------
# Tool definitions (returned by tools/list)
# ---------------------------------------------------------------------------

def get_tool_definitions() -> list[dict]:
    """Return the complete list of MCP tool definitions.

    Each tool has name, description, and inputSchema (JSON Schema).
    """
    return [
        # --- Observation Tools (MCP-TOOLS-OBSERVE) ---
        {
            "name": "composeio_get_state",
            "description": (
                "Request a full design state snapshot from ComposeUX. "
                "Returns the DesignDocument as JSON, current namespace values, "
                "connection status, and selection state. Blocks up to 5 seconds."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_get_layout",
            "description": (
                "Request spatial layout of the current design (pixel positions, "
                "overlap detection). Returns widget bounds and reading order."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["preview", "canvas"],
                        "default": "preview",
                        "description": "Which view to get layout for.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_validate",
            "description": (
                "Request design validation. Returns errors (missing publishers, "
                "broken bindings) and warnings (unused namespaces). Blocks up to 5s."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_get_namespaces",
            "description": (
                "List all namespaces on the design bus with current values. "
                "This is a local query on the MCP server -- no round-trip to ComposeUX."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },

        # --- Structure Tools (MCP-TOOLS-STRUCTURE) ---
        {
            "name": "composeio_add_module",
            "description": "Add a module to the design.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module_id": {
                        "type": "string",
                        "description": "Unique module identifier (e.g., 'ftl_panel').",
                    },
                    "role": {
                        "type": "string",
                        "description": "Module role (e.g., 'button-matrix', 'gauge-cluster').",
                    },
                    "grid_row": {
                        "type": "integer",
                        "default": 0,
                        "description": "Row position in the design grid.",
                    },
                    "grid_column": {
                        "type": "integer",
                        "default": 0,
                        "description": "Column position in the design grid.",
                    },
                },
                "required": ["module_id", "role"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_remove_module",
            "description": "Remove a module from the design.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "module_id": {
                        "type": "string",
                        "description": "ID of the module to remove.",
                    },
                },
                "required": ["module_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_add_widget",
            "description": "Add a widget to a module.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "widget_id": {
                        "type": "string",
                        "description": "Unique widget identifier (e.g., 'btn_arm').",
                    },
                    "widget_type": {
                        "type": "string",
                        "description": "Widget class (e.g., 'ButtonWidget', 'GaugeWidget').",
                    },
                    "module_id": {
                        "type": "string",
                        "description": "Parent module ID.",
                    },
                    "namespace": {
                        "type": "string",
                        "description": "Namespace this widget is bound to.",
                    },
                    "properties": {
                        "type": "object",
                        "description": "Widget-specific properties.",
                        "default": {},
                    },
                    "row": {
                        "type": "integer",
                        "default": 0,
                        "description": "Row position within the module grid.",
                    },
                    "column": {
                        "type": "integer",
                        "default": 0,
                        "description": "Column position within the module grid.",
                    },
                },
                "required": ["widget_id", "widget_type", "module_id", "namespace"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_remove_widget",
            "description": "Remove a widget from the design.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "widget_id": {
                        "type": "string",
                        "description": "ID of the widget to remove.",
                    },
                },
                "required": ["widget_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_add_namespace",
            "description": "Register a namespace in the design.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Namespace identifier (e.g., 'ftl_arm').",
                    },
                    "label": {
                        "type": "string",
                        "description": "Human-readable label.",
                    },
                    "state_engine": {
                        "description": (
                            "State engine config. String ('binary', 'tri_level') "
                            "or object ({\"type\": \"enum\", \"values\": [...]})."
                        ),
                    },
                    "scope": {
                        "type": "string",
                        "default": "auto",
                        "description": "Namespace scope: 'auto', 'local', 'global'.",
                    },
                    "authority": {
                        "type": "string",
                        "default": "local",
                        "description": "Authority: 'local' or 'external'.",
                    },
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },

        # --- Layout Tools (MCP-TOOLS-LAYOUT) ---
        {
            "name": "composeio_move_widget",
            "description": "Move a widget to a new grid position within its module.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "widget_id": {
                        "type": "string",
                        "description": "ID of the widget to move.",
                    },
                    "row": {
                        "type": "integer",
                        "description": "New row position.",
                    },
                    "column": {
                        "type": "integer",
                        "description": "New column position.",
                    },
                },
                "required": ["widget_id", "row", "column"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_resize_widget",
            "description": "Resize a widget's grid span.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "widget_id": {
                        "type": "string",
                        "description": "ID of the widget to resize.",
                    },
                    "rowspan": {
                        "type": "integer",
                        "description": "New row span.",
                    },
                    "columnspan": {
                        "type": "integer",
                        "description": "New column span.",
                    },
                },
                "required": ["widget_id", "rowspan", "columnspan"],
                "additionalProperties": False,
            },
        },

        # --- Config Tools (MCP-TOOLS-CONFIG) ---
        {
            "name": "composeio_set_property",
            "description": (
                "Change a widget, module, or namespace property. "
                "Publishes to the appropriate config namespace."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target_type": {
                        "type": "string",
                        "enum": ["widget", "module", "namespace"],
                        "description": "Type of object to configure.",
                    },
                    "target_id": {
                        "type": "string",
                        "description": "ID of the target (widget_id, module_id, or namespace name).",
                    },
                    "property": {
                        "type": "string",
                        "description": "Property name to change.",
                    },
                    "value": {
                        "description": "New property value.",
                    },
                },
                "required": ["target_type", "target_id", "property", "value"],
                "additionalProperties": False,
            },
        },

        # --- Binding Tools (MCP-TOOLS-BINDING) ---
        {
            "name": "composeio_create_binding",
            "description": (
                "Create a binding between namespaces. Supports simple, compound, "
                "and event_triggered types."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "binding_id": {
                        "type": "string",
                        "description": "Unique binding identifier.",
                    },
                    "binding_type": {
                        "type": "string",
                        "enum": ["simple", "compound", "event_triggered"],
                        "description": "Type of binding.",
                    },
                    "config": {
                        "type": "object",
                        "description": (
                            "Binding configuration. For simple: {input, output, transform}. "
                            "For compound: {output, requires, conditions}. "
                            "For event_triggered: {trigger, sequence, side_effects}."
                        ),
                    },
                },
                "required": ["binding_id", "binding_type", "config"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_modify_binding",
            "description": "Modify an existing binding's configuration.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "binding_id": {
                        "type": "string",
                        "description": "ID of the binding to modify.",
                    },
                    "config": {
                        "type": "object",
                        "description": "Updated binding configuration.",
                    },
                },
                "required": ["binding_id", "config"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_remove_binding",
            "description": "Remove a binding.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "binding_id": {
                        "type": "string",
                        "description": "ID of the binding to remove.",
                    },
                },
                "required": ["binding_id"],
                "additionalProperties": False,
            },
        },

        # --- File Tools (MCP-TOOLS-FILE) ---
        {
            "name": "composeio_save",
            "description": "Save the current design to a .compose.yaml file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path on the machine running ComposeUX.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_load",
            "description": "Load a design from a .compose.yaml file.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to load.",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_export",
            "description": "Export the design to ComposeIO module YAML files.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "output_dir": {
                        "type": "string",
                        "description": "Output directory for exported YAML files.",
                    },
                },
                "required": ["output_dir"],
                "additionalProperties": False,
            },
        },

        # --- Batch Tools (MCP-TOOLS-BATCH) ---
        {
            "name": "composeio_batch_begin",
            "description": (
                "Start a batch operation. All subsequent structure/layout/config/binding "
                "tools are tagged with the batch ID until batch_end. The History module "
                "treats the batch as a single undoable unit."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What this batch does (shown to human).",
                    },
                    "estimated_commands": {
                        "type": "integer",
                        "description": "Estimated number of commands for progress indication.",
                    },
                },
                "required": ["description"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_batch_end",
            "description": (
                "Complete the current batch. Finalizes batch_total for undo grouping."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },

        # --- Intent and Suggestion Tools (MCP-TOOLS-INTENT) ---
        {
            "name": "composeio_declare_intent",
            "description": (
                "Declare what the Intelligence is about to do. "
                "Shown to the human as a toast notification."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "What the Intelligence is about to do.",
                    },
                    "affected_modules": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Module IDs that will be affected.",
                    },
                },
                "required": ["description"],
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_suggest",
            "description": (
                "Make a non-imperative suggestion to the human. "
                "Does not modify the design."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "suggestion": {
                        "type": "string",
                        "description": "The suggestion text.",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Why this suggestion is being made.",
                    },
                    "affected_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Widget/module/binding IDs the suggestion relates to.",
                    },
                },
                "required": ["suggestion"],
                "additionalProperties": False,
            },
        },

        # --- Bus Monitor Tools (MCP-TOOLS-MONITOR) ---
        {
            "name": "composeio_bus_status",
            "description": (
                "Get current bus status -- connected transports, namespace count, "
                "active modules."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "composeio_publish_raw",
            "description": (
                "Escape hatch: publish a raw bus message. For advanced use when "
                "no specific tool covers the operation. Cannot publish to _bus/ "
                "infrastructure namespaces."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "namespace": {
                        "type": "string",
                        "description": "Target namespace.",
                    },
                    "type": {
                        "type": "string",
                        "enum": ["state", "event"],
                        "description": "Message type.",
                    },
                    "value": {
                        "description": "Value for state messages.",
                    },
                    "event_type": {
                        "type": "string",
                        "description": "Event type for event messages.",
                    },
                    "data": {
                        "type": "object",
                        "description": "Data payload for event messages.",
                    },
                },
                "required": ["namespace", "type"],
                "additionalProperties": False,
            },
        },
    ]


# ---------------------------------------------------------------------------
# Tool execution handlers
# ---------------------------------------------------------------------------

class ToolExecutor:
    """Executes MCP tool calls by publishing bus messages.

    Each tool method:
    1. Tags all messages with source=agent_id (MCP-C2)
    2. Includes batch metadata if in batch mode (MCP-C5)
    3. Uses correlation tracker for request/response patterns (MCP-C4)
    """

    def __init__(self, session: Any, external_bus: Any, app: Any):
        """
        Args:
            session: IntelligenceSession instance.
            external_bus: ComposeApp's external bus.
            app: ComposeApp instance.
        """
        self._session = session
        self._bus = external_bus
        self._app = app

    def _publish_command(self, target_namespace: str, do_data: dict, undo_data: dict, description: str = "") -> dict:
        """Publish an undoable command through the proper command protocol.

        All design-mutating tools publish to ``composer/command/execute``
        so that the DesignCommandDispatcher can record the command for
        undo/redo.
        """
        import uuid

        command_id = f"mcp_{uuid.uuid4().hex[:8]}"

        command = {
            "command_id": command_id,
            "type": target_namespace,
            "do": do_data,
            "undo": undo_data,
            "description": description,
            "source": self._session.agent_id,
            "timestamp": time.time(),
        }

        batch_meta = self._session.get_batch_metadata()
        if batch_meta:
            command.update(batch_meta)

        self._bus.publish_event(
            "composer/command/execute",
            "execute",
            data=command,
            source=self._session.agent_id,
        )

        logger.info(f"Command: {target_namespace} from {self._session.agent_id}")
        return {"status": "published", "command_id": command_id, "type": target_namespace}

    def _publish_direct(self, namespace: str, event_type: str, data: dict) -> dict:
        """Publish a non-undoable message directly (file ops, etc.)."""
        data["source"] = self._session.agent_id
        data["timestamp"] = time.time()

        self._bus.publish_event(
            namespace,
            event_type,
            data=data,
            source=self._session.agent_id,
        )

        logger.info(f"Direct publish: {namespace}/{event_type} from {self._session.agent_id}")
        return {"status": "published", "namespace": namespace, "data": data}

    async def execute(self, tool_name: str, arguments: dict) -> list[dict]:
        """Execute a tool call and return MCP content blocks.

        Returns a list of content blocks per MCP protocol:
        [{"type": "text", "text": "..."}]
        """
        # Ensure Intelligence has joined the bus
        await self._session.ensure_joined()

        # Update active task for heartbeat
        self._session.update_active_task(f"calling {tool_name}")

        handler = getattr(self, f"_tool_{tool_name}", None)
        if handler is None:
            return [{"type": "text", "text": json.dumps({"error": f"Unknown tool: {tool_name}"})}]

        try:
            result = await handler(arguments)
            return [{"type": "text", "text": json.dumps(result, default=str)}]
        except TimeoutError as e:
            return [{"type": "text", "text": json.dumps({"error": str(e)})}]
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}", exc_info=True)
            return [{"type": "text", "text": json.dumps({"error": str(e)})}]

    # --- Observation Tools ---

    async def _tool_composeio_get_state(self, args: dict) -> dict:
        """Request full design state snapshot. Blocks until response (5s timeout)."""
        return await self._session.correlation.request(
            request_namespace="composer/intelligence/request_state",
            response_namespace="composer/intelligence/state_snapshot",
            data={"agent_id": self._session.agent_id},
        )

    async def _tool_composeio_get_layout(self, args: dict) -> dict:
        """Request spatial layout. Blocks until response (5s timeout)."""
        target = args.get("target", "preview")
        return await self._session.correlation.request(
            request_namespace="composer/intelligence/request_layout",
            response_namespace="composer/intelligence/layout_snapshot",
            data={"agent_id": self._session.agent_id, "target": target},
        )

    async def _tool_composeio_validate(self, args: dict) -> dict:
        """Request design validation. Blocks until response (5s timeout)."""
        return await self._session.correlation.request(
            request_namespace="composer/intelligence/validate",
            response_namespace="composer/intelligence/validation_result",
            data={"agent_id": self._session.agent_id},
        )

    async def _tool_composeio_get_namespaces(self, args: dict) -> dict:
        """List all namespaces. Local query -- no round-trip to ComposeUX."""
        namespaces = self._bus.namespaces
        return {
            "namespaces": {
                name: ns.to_dict() for name, ns in namespaces.items()
            },
            "count": len(namespaces),
        }

    # --- Structure Tools ---

    async def _tool_composeio_add_module(self, args: dict) -> dict:
        """Add a module to the design."""
        module_id = args["module_id"]
        do_data = {
            "module_id": module_id,
            "role": args["role"],
            "layout": {
                "grid_row": args.get("grid_row", 0),
                "grid_column": args.get("grid_column", 0),
            },
        }
        undo_data = {"module_id": module_id}
        return self._publish_command(
            "composer/canvas/module_added",
            do_data,
            undo_data,
            description=f"Add module '{module_id}'",
        )

    async def _tool_composeio_remove_module(self, args: dict) -> dict:
        """Remove a module from the design."""
        module_id = args["module_id"]
        do_data = {"module_id": module_id}
        # Undo placeholder -- dispatcher captures full module state on forward exec
        undo_data = {"module_id": module_id}
        return self._publish_command(
            "composer/canvas/module_removed",
            do_data,
            undo_data,
            description=f"Remove module '{module_id}'",
        )

    async def _tool_composeio_add_widget(self, args: dict) -> dict:
        """Add a widget to a module."""
        widget_id = args["widget_id"]
        module_id = args["module_id"]
        do_data = {
            "widget_id": widget_id,
            "widget_type": args["widget_type"],
            "module_id": module_id,
            "namespace": args["namespace"],
            "properties": args.get("properties", {}),
            "row": args.get("row", 0),
            "column": args.get("column", 0),
        }
        undo_data = {"widget_id": widget_id, "module_id": module_id}
        return self._publish_command(
            "composer/canvas/widget_added",
            do_data,
            undo_data,
            description=f"Add widget '{widget_id}' to '{module_id}'",
        )

    async def _tool_composeio_remove_widget(self, args: dict) -> dict:
        """Remove a widget from the design."""
        widget_id = args["widget_id"]
        do_data = {"widget_id": widget_id}
        # Undo placeholder -- dispatcher captures widget state on forward exec
        undo_data = {"widget_id": widget_id}
        return self._publish_command(
            "composer/canvas/widget_removed",
            do_data,
            undo_data,
            description=f"Remove widget '{widget_id}'",
        )

    async def _tool_composeio_add_namespace(self, args: dict) -> dict:
        """Register a namespace in the design."""
        name = args["name"]
        do_data = {
            "action": "created",
            "name": name,
            "label": args.get("label", ""),
            "state_engine": args.get("state_engine", "binary"),
            "scope": args.get("scope", "auto"),
            "authority": args.get("authority", "local"),
        }
        undo_data = {"name": name}
        return self._publish_command(
            "composer/config/namespace_changed",
            do_data,
            undo_data,
            description=f"Add namespace '{name}'",
        )

    # --- Layout Tools ---

    async def _tool_composeio_move_widget(self, args: dict) -> dict:
        """Move a widget to a new grid position."""
        widget_id = args["widget_id"]
        do_data = {
            "widget_id": widget_id,
            "row": args["row"],
            "column": args["column"],
        }
        # Undo placeholder -- old position unknown; dispatcher captures on forward exec
        undo_data = {"widget_id": widget_id}
        return self._publish_command(
            "composer/canvas/widget_moved",
            do_data,
            undo_data,
            description=f"Move widget '{widget_id}' to ({args['row']}, {args['column']})",
        )

    async def _tool_composeio_resize_widget(self, args: dict) -> dict:
        """Resize a widget's grid span."""
        widget_id = args["widget_id"]
        do_data = {
            "widget_id": widget_id,
            "rowspan": args["rowspan"],
            "columnspan": args["columnspan"],
        }
        # Undo placeholder -- old size unknown; dispatcher captures on forward exec
        undo_data = {"widget_id": widget_id}
        return self._publish_command(
            "composer/canvas/widget_resized",
            do_data,
            undo_data,
            description=f"Resize widget '{widget_id}' to {args['rowspan']}x{args['columnspan']}",
        )

    # --- Config Tools ---

    async def _tool_composeio_set_property(self, args: dict) -> dict:
        """Change a widget, module, or namespace property."""
        target_type = args["target_type"]
        target_id = args["target_id"]
        prop = args["property"]
        value = args["value"]

        namespace_map = {
            "widget": "composer/config/widget_changed",
            "module": "composer/config/module_changed",
            "namespace": "composer/config/namespace_changed",
        }
        ns = namespace_map.get(target_type)
        if not ns:
            return {"error": f"Invalid target_type: {target_type}"}

        do_data: dict[str, Any] = {
            "target_id": target_id,
            "property_name": prop,
            "value": value,
        }
        # Include the type-specific id key as well for dispatcher compatibility
        if target_type == "widget":
            do_data["widget_id"] = target_id
        elif target_type == "module":
            do_data["module_id"] = target_id
        elif target_type == "namespace":
            do_data["action"] = "modified"
            do_data["name"] = target_id

        # Undo placeholder -- old value unknown; dispatcher captures on forward exec
        undo_data: dict[str, Any] = {
            "target_id": target_id,
            "property_name": prop,
        }

        return self._publish_command(
            ns,
            do_data,
            undo_data,
            description=f"Set {target_type} '{target_id}' property '{prop}'",
        )

    # --- Binding Tools ---

    async def _tool_composeio_create_binding(self, args: dict) -> dict:
        """Create a binding between namespaces."""
        binding_id = args["binding_id"]
        do_data = {
            "binding_id": binding_id,
            "binding_type": args["binding_type"],
            "config": args["config"],
        }
        undo_data = {"binding_id": binding_id}
        return self._publish_command(
            "composer/binding/created",
            do_data,
            undo_data,
            description=f"Create binding '{binding_id}'",
        )

    async def _tool_composeio_modify_binding(self, args: dict) -> dict:
        """Modify an existing binding."""
        binding_id = args["binding_id"]
        do_data = {
            "binding_id": binding_id,
            "config": args["config"],
        }
        # Undo placeholder -- old config unknown; dispatcher captures on forward exec
        undo_data = {"binding_id": binding_id}
        return self._publish_command(
            "composer/binding/modified",
            do_data,
            undo_data,
            description=f"Modify binding '{binding_id}'",
        )

    async def _tool_composeio_remove_binding(self, args: dict) -> dict:
        """Remove a binding."""
        binding_id = args["binding_id"]
        do_data = {"binding_id": binding_id}
        # Undo placeholder -- full binding config unknown; dispatcher captures on forward exec
        undo_data = {"binding_id": binding_id}
        return self._publish_command(
            "composer/binding/removed",
            do_data,
            undo_data,
            description=f"Remove binding '{binding_id}'",
        )

    # --- File Tools ---

    async def _tool_composeio_save(self, args: dict) -> dict:
        """Save the current design to a file."""
        return self._publish_direct(
            "composer/file/saved",
            "save",
            {"path": args["path"]},
        )

    async def _tool_composeio_load(self, args: dict) -> dict:
        """Load a design from a file."""
        return self._publish_direct(
            "composer/file/loaded",
            "load",
            {"path": args["path"]},
        )

    async def _tool_composeio_export(self, args: dict) -> dict:
        """Export the design to ComposeIO module YAML files."""
        return self._publish_direct(
            "composer/file/exported",
            "export",
            {"output_dir": args["output_dir"]},
        )

    # --- Batch Tools ---

    async def _tool_composeio_batch_begin(self, args: dict) -> dict:
        """Start a batch operation."""
        description = args["description"]
        estimated = args.get("estimated_commands")

        batch_id = self._session.begin_batch(description, estimated)

        # Publish intent for the batch
        self._bus.publish_event(
            "composer/intelligence/intent",
            "intent",
            data={
                "agent_id": self._session.agent_id,
                "action": description,
                "estimated_commands": estimated,
                "batch_id": batch_id,
                "source": self._session.agent_id,
                "timestamp": time.time(),
            },
            source=self._session.agent_id,
        )

        return {
            "status": "batch_started",
            "batch_id": batch_id,
            "description": description,
            "estimated_commands": estimated,
        }

    async def _tool_composeio_batch_end(self, args: dict) -> dict:
        """Complete the current batch."""
        summary = self._session.end_batch()
        return {"status": "batch_ended", **summary}

    # --- Intent and Suggestion Tools ---

    async def _tool_composeio_declare_intent(self, args: dict) -> dict:
        """Declare what the Intelligence is about to do."""
        data: dict[str, Any] = {
            "agent_id": self._session.agent_id,
            "action": args["description"],
            "source": self._session.agent_id,
            "timestamp": time.time(),
        }
        if "affected_modules" in args:
            data["affected_modules"] = args["affected_modules"]

        self._bus.publish_event(
            "composer/intelligence/intent",
            "intent",
            data=data,
            source=self._session.agent_id,
        )

        return {"status": "intent_declared", "description": args["description"]}

    async def _tool_composeio_suggest(self, args: dict) -> dict:
        """Make a non-imperative suggestion to the human."""
        data: dict[str, Any] = {
            "agent_id": self._session.agent_id,
            "suggestion": args["suggestion"],
            "source": self._session.agent_id,
            "timestamp": time.time(),
        }
        if "rationale" in args:
            data["rationale"] = args["rationale"]
        if "affected_ids" in args:
            data["affected_ids"] = args["affected_ids"]

        self._bus.publish_event(
            "composer/intelligence/suggestion",
            "suggestion",
            data=data,
            source=self._session.agent_id,
        )

        return {"status": "suggestion_published", "suggestion": args["suggestion"]}

    # --- Bus Monitor Tools ---

    async def _tool_composeio_bus_status(self, args: dict) -> dict:
        """Get current bus status."""
        bus_status = self._bus.status()
        app_status = self._app.status()
        return {
            "bus": bus_status,
            "app": {
                "app_id": app_status.get("app_id"),
                "started": app_status.get("started"),
                "exports": app_status.get("exports"),
                "imports": app_status.get("imports"),
                "transports": app_status.get("transports"),
            },
            "session": self._session.status(),
        }

    async def _tool_composeio_publish_raw(self, args: dict) -> dict:
        """Publish a raw bus message. Validates no _bus/ prefix (MCP-C6)."""
        namespace = args["namespace"]
        msg_type = args["type"]

        # MCP-C6: Refuse to publish to _bus/ infrastructure namespaces
        if namespace.startswith("_bus/"):
            return {
                "error": (
                    "Cannot publish to _bus/ infrastructure namespaces. "
                    "These are reserved for the bus master."
                ),
            }

        if msg_type == "state":
            value = args.get("value")
            self._bus.publish_state(
                namespace, value, source=self._session.agent_id
            )
            return {"status": "published", "namespace": namespace, "type": "state", "value": value}
        elif msg_type == "event":
            event_type = args.get("event_type", "custom")
            data = args.get("data", {})
            data["source"] = self._session.agent_id
            data["timestamp"] = time.time()

            self._bus.publish_event(
                namespace,
                event_type,
                data=data,
                source=self._session.agent_id,
            )
            return {
                "status": "published",
                "namespace": namespace,
                "type": "event",
                "event_type": event_type,
            }
        else:
            return {"error": f"Invalid message type: {msg_type}. Use 'state' or 'event'."}
