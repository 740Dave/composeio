"""
ComposeUX CommandWidget — a button that INVOKES an mcp_action and frames
its result.

M2 port of ux_composer CommandRenderer (spec 13-mcp-action-binding):
a trigger + a result frame holding NO application state (MCPA-3).
Activation resolves the `mcp_action` binding — ${ns} refs resolve
against live bus state at invoke time (MCPA-2) — and calls the
host-provided runner (EngineContext.invoke), which runs the tool OFF
the render thread and streams back progress + a summarized result.
State authority is external: the widget shows *pending* from invocation
until the tool confirms — never an optimistic green.

Confirm gate divergence from CTk: the engine owns every pixel, so there
is no OS message box.  Destructive (`confirm: true`) commands use an
ARM-CONFIRM pattern instead: first tap arms the button ("tap again to
confirm", 3 s window), the second tap fires.  Same human gate (MCPA-5),
engine-native.
"""

from __future__ import annotations

import re
import time
from typing import Any, Optional

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import style

# Runtime namespace reference: ${ns/path} (port of command.resolve_ns_refs)
_NS_REF = re.compile(r"\$\{([^}]+)\}")

_CONFIRM_WINDOW_S = 3.0


def resolve_ns_refs(node: Any, get_state) -> Any:
    """Recursively replace ${ns} refs with current bus values.

    Exact-token "${ns}" -> the native value (type preserved);
    embedded -> stringified.
    """
    if isinstance(node, str):
        m = _NS_REF.fullmatch(node.strip())
        if m:
            return get_state(m.group(1))
        return _NS_REF.sub(lambda mm: str(get_state(mm.group(1))), node)
    if isinstance(node, dict):
        return {k: resolve_ns_refs(v, get_state) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_ns_refs(v, get_state) for v in node]
    return node


class CommandWidget(Widget):
    """A button that invokes an mcp_action tool and frames the outcome."""

    def __init__(self, widget_id: str, *,
                 label: str = "",
                 binding: Optional[dict] = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "command", **kwargs)
        self.binding = dict(binding or {})
        self.tool = self.binding.get("tool", "")
        self.args = dict(self.binding.get("args", {}) or {})
        self.confirm = bool(self.binding.get("confirm", False))
        self.progress_ns = self.binding.get("progress_namespace")
        self.result_ns = self.binding.get("result_namespace")
        self.label = label or self.tool or "Command"
        self.interactive = True

        self._status_key = "idle"
        self._status_text = "idle"
        self._result_text = self.tool
        self._result_color = style.TEXT_DIM
        self._busy = False
        self._armed_at: Optional[float] = None    # confirm arm timestamp
        self._now = time.monotonic

    # -- host runner availability (resolved at mount) -------------------------

    @property
    def _has_runner(self) -> bool:
        return self._ctx is not None and callable(getattr(self._ctx, "invoke", None))

    @property
    def _runnable(self) -> bool:
        return self._has_runner and bool(self.tool) and not self._busy

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        self._status_text = ("idle" + ("  ⚠ confirm" if self.confirm else "")
                             if self._has_runner else "no runner")

    # -- interaction --------------------------------------------------------------

    def _set_status(self, key: str, text: str) -> None:
        self._status_key = key
        self._status_text = text
        self.request_redraw()

    def _args_preview(self) -> str:
        inner = ", ".join(f"{k}={v}" for k, v in self.args.items())
        return f"{self.tool}({inner})"

    def on_tap(self, x: float, y: float) -> None:
        if not self._runnable:
            return
        if self.confirm:
            now = self._now()
            if self._armed_at is None or now - self._armed_at > _CONFIRM_WINDOW_S:
                self._armed_at = now          # first tap: arm
                self._set_status("blocked", "tap again to confirm ⚠")
                return
            self._armed_at = None             # second tap inside window: fire
        self._fire()

    def _fire(self) -> None:
        self._busy = True
        self._set_status("invoking", "invoking…")
        self._result_text = self._args_preview()
        self._result_color = style.TEXT_STATUS
        try:
            # Resolve ${ns} refs at invoke time (MCPA-2).  The preview shows
            # the UNRESOLVED template so a resolved secret hash is never
            # rendered into the UI.
            args = resolve_ns_refs(dict(self.args), self._ctx.bus.get_state)
            self._ctx.invoke(self.tool, args, on_event=self._on_event)
        except Exception as exc:              # runner refused synchronously
            self._on_event({"kind": "result", "ok": False,
                            "summary": f"runner error: {exc}"})

    def _on_event(self, ev: dict) -> None:
        """Runner callback — may arrive from any thread.  Marshal."""
        self._ctx.schedule_update(lambda e=dict(ev): self._apply_event(e))

    def _apply_event(self, ev: dict) -> None:
        kind = ev.get("kind")
        if kind == "progress":
            pct = ev.get("pct")
            phase = ev.get("phase")
            self._set_status("running", f"running {pct}%" if pct is not None
                             else f"running {phase or ''}".strip())
            if self.progress_ns is not None and pct is not None:
                self.publish(self.progress_ns, pct)
        elif kind == "result":
            ok = bool(ev.get("ok"))
            blocked = bool(ev.get("blocked"))
            summary = ev.get("summary") or self.tool
            state = "blocked" if blocked else ("ok" if ok else "fail")
            self._set_status(state, {"blocked": "gated ⛔", "ok": "done ✓",
                                     "fail": "failed ✗"}[state])
            self._result_text = summary
            self._result_color = ((0xCC, 0xCC, 0xCC) if ok else
                                  (0xE6, 0xB4, 0x50) if blocked else
                                  (0xE0, 0x90, 0x8F))
            # Republish a SUMMARIZED result (stop_light) for siblings (MCPA-2)
            if self.result_ns is not None:
                self.publish(self.result_ns,
                             "green" if ok else ("yellow" if blocked else "red"))
            self._busy = False

    # -- measure / draw --------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(280.0, constraints.max_width),
                    min(86.0, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        x = rect.x + 8
        y = rect.y + 4

        # title
        canvas.set_fill_color(*style.TEXT)
        canvas.set_font(style.FONT_UI, 13, "bold")
        m = canvas.measure_text(self.label)
        canvas.draw_text(self.label, x, y + m.ascent, "left")
        y += 20

        # row: status dot + Run button + status text
        cy = y + 14
        dot_color = style.STATUS_COLORS.get(self._status_key, style.STATUS_COLORS["idle"])
        if self._armed_at is not None and self._now() - self._armed_at > _CONFIRM_WINDOW_S:
            self._armed_at = None
            self._status_key, self._status_text = "idle", "idle  ⚠ confirm"
        style.draw_dot(canvas, x + 6, cy, 5, dot_color)

        armed = self._has_runner and bool(self.tool)
        btn_label = "Run" if armed else "—"
        if self._armed_at is not None:
            btn_label = "Confirm?"
        style.draw_button(canvas, x + 18, cy - 14, 88, 28, btn_label,
                          fill=style.BTN_WARM if self.confirm else style.BTN_FILL,
                          disabled=not armed or self._busy)

        canvas.set_fill_color(*style.TEXT_STATUS)
        canvas.set_font(style.FONT_UI, 11, "regular")
        canvas.draw_text(self._status_text, x + 114,
                         style.baseline_for(canvas, cy), "left")
        y += 32

        # result / arg preview line (monospace, clipped to widget)
        canvas.save()
        canvas.clip_rect(rect.x, y, rect.w, rect.h - (y - rect.y))
        canvas.set_fill_color(*self._result_color)
        canvas.set_font(style.FONT_MONO, 10, "regular")
        rm = canvas.measure_text(self._result_text)
        canvas.draw_text(self._result_text, x, y + rm.ascent, "left")
        canvas.restore()
        canvas.restore()

        if self._armed_at is not None:
            self.request_redraw()     # keep ticking so the arm window expires
