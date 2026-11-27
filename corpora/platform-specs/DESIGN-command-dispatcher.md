# DesignCommandDispatcher — Undo/Redo Replay Handler

## Problem

22 sites in canvas.py (7) and binding_editor.py (15) mutate DesignDocument directly
and publish commands to history. Forward operations work. But when HistoryModule
replays an undo, it publishes an "undo" event to the command's target namespace —
and nobody subscribes. The reverse mutation never happens.

## Solution

A new non-visual module, `DesignCommandDispatcher`, that:
1. Subscribes to ALL undo/redo replay events from HistoryModule
2. Applies the reverse mutation (undo) or forward mutation (redo) to DesignDocument
3. Publishes a bus event so visual modules (canvas, binding_editor) can update their UI

The forward path is UNCHANGED. The 22 existing mutation sites keep working as-is.
Only the undo/redo path gains a handler.

## Architecture

```
Forward (existing, no changes):
  Canvas._place_widget()
    → self.design.add_widget(widget_def)     # direct mutation (stays)
    → self.publish_command("widget_add",      # recorded in history (stays)
        do_data={widget_id, module_id, widget},
        undo_data={widget_id, module_id})

Undo (new):
  User presses Ctrl+Z
    → Toolbar publishes composer/command/undo
    → HistoryModule pops command, publishes:
        namespace: "composer/canvas/widget_added"
        event_type: "undo"
        data: {widget_id, module_id}            # the undo_data from the command
    → DesignCommandDispatcher receives event:
        → self.design.remove_widget(data["widget_id"])
        → publishes "composer/canvas/widget_removed" event
        → Canvas subscribes, removes widget from visual canvas

Redo (new):
  User presses Ctrl+Shift+Z
    → Same flow but event_type is "redo" and data is do_data
    → Dispatcher applies forward mutation
```

## Namespace Subscriptions

The dispatcher subscribes to undo/redo events on these namespaces
(matching HistoryModule._command_type_to_namespace):

| Namespace | Undo Action | Redo Action |
|-----------|-------------|-------------|
| composer/canvas/widget_added | design.remove_widget(widget_id) | design.add_widget(module_id, WidgetDef.from_dict(widget)) |
| composer/canvas/widget_removed | design.add_widget(module_id, WidgetDef.from_dict(widget)) | design.remove_widget(widget_id) |
| composer/canvas/widget_moved | restore widget.layout from undo_data | apply widget.layout from do_data |
| composer/canvas/widget_resized | restore widget.layout from undo_data | apply widget.layout from do_data |
| composer/canvas/module_added | design.remove_module(module_id) | design.add_module(ModuleDef.from_dict(module)) |
| composer/canvas/module_removed | design.add_module(ModuleDef.from_dict(module)) | design.remove_module(module_id) |
| composer/canvas/layout_changed | restore layout from undo_data | apply layout from do_data |
| composer/binding/created | design.remove_binding(binding_id) | design.add_binding(BindingDef.from_dict(binding)) |
| composer/binding/removed | design.add_binding(BindingDef.from_dict(binding)) | design.remove_binding(binding_id) |
| composer/binding/modified | restore binding.config from undo_data["old_config"] | apply binding.config from do_data["new_config"] |
| composer/config/widget_changed | restore widget property from undo_data | apply widget property from do_data |
| composer/config/module_changed | restore module property from undo_data | apply module property from do_data |
| composer/config/namespace_changed | restore namespace from undo_data | apply namespace from do_data |

## Event Data Contract

The dispatcher relies on the undo_data/do_data dicts already published by
canvas.py and binding_editor.py. These MUST contain enough information to
reverse/replay the mutation. Required fields per command type:

### Canvas commands (canvas.py) — actual shapes from code

**widget_add**:
- do_data: {widget_id, module_id, widget_type, x, y}
- undo_data: {widget_id, module_id}
- Undo action: design.remove_widget(widget_id)
- Redo action: reconstruct WidgetDef from do_data fields, design.add_widget()

**widget_remove**:
- do_data: {widget_id}
- undo_data: {widget_id, module_id, widget_type, namespace, x, y, w, h}
- Undo action: reconstruct WidgetDef from undo_data flat fields, design.add_widget()
- Redo action: design.remove_widget(widget_id)

**module_add**:
- do_data: {module_id, x, y}
- undo_data: {module_id}
- Undo action: design.remove_module(module_id)
- Redo action: reconstruct ModuleDef from do_data, design.add_module()

**module_remove**:
- do_data: {module_id}
- undo_data: {module_id, role, x, y, w, h}
- Undo action: reconstruct ModuleDef from undo_data, design.add_module()
- Redo action: design.remove_module(module_id)

**widget_move**:
- do_data: {widget_id, x, y}
- undo_data: {widget_id, x, y}  (old position)
- Undo action: find widget in design, update layout x/y from undo_data
- Redo action: find widget in design, update layout x/y from do_data

**module_move**:
- do_data: {module_id, x, y}
- undo_data: {module_id, x, y}
- Same pattern as widget_move

**widget_resize / module_resize** (cmd_type varies):
- do_data: {widget_id|module_id, x, y, w, h}
- undo_data: {widget_id|module_id, x, y, w, h}
- Undo/redo: restore x/y/w/h from the respective data dict

**binding_create**:
- do_data: {binding_id, source_widget_id, target_widget_id, binding_type, config}
- undo_data: {binding_id}
- Undo action: design.remove_binding(binding_id)
- Redo action: reconstruct BindingDef from do_data, design.add_binding()

**binding_remove**:
- do_data: {binding_id}
- undo_data: {binding_id, binding_type, config}
- Undo action: reconstruct BindingDef from undo_data, design.add_binding()
- Redo action: design.remove_binding(binding_id)

### Binding editor commands (binding_editor.py)

**binding_modify / binding_config_change / binding_property_change**:
- do_data: {binding_id, field, value} or {binding_id, new_config: dict}
- undo_data: {binding_id, field, value} or {binding_id, old_config: dict}

For config-level changes (conditions, side_effects, transforms):
- do_data: {binding_id, new_config: full config snapshot}
- undo_data: {binding_id, old_config: full config snapshot}

The config-snapshot approach is simpler and more robust than tracking
individual field changes — the dispatcher just replaces the entire config.

### Property editor commands

**property_change / widget_property_change**:
- do_data: {widget_id, property, value}
- undo_data: {widget_id, property, old_value}

## Implementation Notes

1. **File**: `src/composer/modules/design_dispatcher.py`
2. **Class**: `DesignCommandDispatcher(ComposerModule)` — non-visual, like HistoryModule
3. **Registration**: Add to MODULE_LAYOUT in app.py with height=0
4. **No UI**: pack_forget() in __init__, same pattern as HistoryModule
5. **After applying the mutation, publish a notification event** so visual modules
   can update. Use the SAME namespace with event_type "replayed" — canvas can
   subscribe to "replayed" events on its own namespaces to refresh.

## What the Subagent Does NOT Change

- The 22 existing mutation sites stay exactly as they are
- The publish_command() calls stay as they are
- HistoryModule stays as it is
- The only change to existing files: add DesignCommandDispatcher to app.py MODULE_LAYOUT

## Verification

After implementation, this sequence should work:
1. Place a widget on canvas (forward: widget appears, history records command)
2. Ctrl+Z (undo: widget disappears, design.widgets shrinks by 1)
3. Ctrl+Shift+Z (redo: widget reappears, design.widgets grows by 1)

And for binding_editor:
1. Create a binding (forward: binding appears, history records)
2. Modify the binding's transform (forward: config updated, history records)
3. Ctrl+Z (undo: transform reverted to previous config)
4. Ctrl+Z (undo: binding removed)

## Data Integrity

The dispatcher MUST verify that the mutation target exists before applying:
- undo widget_add: verify widget_id exists in design before removing
- undo widget_remove: verify widget_id does NOT exist before adding
- undo binding_modify: verify binding_id exists before restoring config

Log WARN and skip if precondition fails — don't crash on stale undo data.
