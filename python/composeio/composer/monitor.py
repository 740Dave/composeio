"""
ComposeUX Headless Monitor — the Diagnosis viewer ([SPEC:CXC-HMON]).

Headless widgets are invisible; this makes them legible.  ONE define-once
ComposeUX surface ([CXC-HMON-VIEW]) subscribing `_bus/headless/*`:

    fleet rail        runtimes (heartbeats) -> widgets, health at a glance
    annotated hero    the output expr with THIS tick's live values
                      substituted, the taken branch lit / dead branch
                      dimmed -> result + published/suppressed pill
                      ([CXC-HMON-C7])
    tick timeline     recent ticks (t, inputs, output, verdict); click a
                      tick to REPLAY it into the hero ([CXC-HMON-C8])
    internal state    $vars, behavior timers, schedule state
    health            rate sparkline, uptime, ticks, last error

Two hosts, one surface ([CXC-HMON-HOSTS] / [CXC-HMON-C9]):

    Flow pop-out      launched from a logic node's "Inspect ▸"
                      (flowpanel.py), scoped to one widget:
                          python -m composeio.composer.monitor \
                              --widget fuel_pump --mqtt broker
    standalone ops    the fleet rail + drill-down:
                          python -m composeio.composer.monitor --mqtt broker

Discipline: READ-ONLY observation of a running widget.  The Monitor never
pauses or mutates execution or the design — its only writes are trace
ENABLE requests published to `_bus/headless/<id>/trace/ctl`
([CXC-HMON-C6]: opt-in is a bus publish, not a code change).  Everything
it renders arrives on ordinary bus namespaces, so the raw Bus Monitor and
an Intelligence read the identical trace ([CXC-HMON-C10]).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..render.canvas import Canvas, Size
from ..render.widget import Constraints, Rect
from ..render.engine.layout import Container, _contains
from ..render.engine.parser import Composition
from ..render.engine.headless import (HEADLESS_BUS_PREFIX,
                                      trace_ctl_namespace)

from . import chrome
from .chrome import FONT, MONO, _baseline

logger = logging.getLogger("composeio.composer.monitor")

# pre-vis palette additions (headless-monitor-previs.v1.html)
LOGIC = (0xB9, 0x8C, 0xFF)
LOGIC_SOFT = (0xB9, 0x8C, 0xFF, 0x1C)
LIVE = (0x4B, 0xD6, 0xA0)
GOOD_SOFT = (0x4B, 0xD6, 0xA0, 0x26)
WARN_SOFT = (0xE0, 0xA6, 0x3A, 0x1F)

MAX_TICKS_KEPT = 256        # merged-timeline cap per widget (viewer-side)


# ---------------------------------------------------------------------------
# Annotation ([CXC-HMON-C7]) — pure function of (source, trace steps)
# ---------------------------------------------------------------------------

def annotate(source: str, steps: List[dict]) -> List[dict]:
    """Segment *source* for the annotated hero, from ONE output's trace
    steps (dsl.Expression.evaluate_traced): value chips after each
    ns()/$var/dt/t read, the taken ternary branch lit, dead branches
    (ternary or short-circuit) dimmed.

    Returns segments in source order:
        {"text": str, "role": "normal"|"taken"|"dead"}
    with chip entries interleaved where a read completed:
        {"chip": display_value, "role": ...}

    Pure + JSON-in/JSON-out: usable by this pane, the inline expr
    editor's future "explain", or an Intelligence."""
    n = len(source)
    taken: List[Tuple[int, int]] = []
    dead: List[Tuple[int, int]] = []
    chips: List[Tuple[int, str]] = []       # (position, display)
    for s in steps:
        kind = s.get("kind")
        span = s.get("span")
        if kind in ("ns", "var", "clock") and span:
            chips.append((int(span[1]), _display(s.get("value"))))
        if kind == "ternary":
            if s.get("taken_span"):
                taken.append((int(s["taken_span"][0]),
                              int(s["taken_span"][1])))
            if s.get("dead_span"):
                dead.append((int(s["dead_span"][0]),
                             int(s["dead_span"][1])))
        if kind == "logic" and s.get("short_circuit") and s.get("dead_span"):
            dead.append((int(s["dead_span"][0]), int(s["dead_span"][1])))

    def role_at(i: int) -> str:
        # dead wins (a taken outer region may contain a dead inner one)
        for a, b in dead:
            if a <= i < b:
                return "dead"
        for a, b in taken:
            if a <= i < b:
                return "taken"
        return "normal"

    cuts = {0, n}
    for a, b in taken + dead:
        cuts.add(max(0, min(a, n)))
        cuts.add(max(0, min(b, n)))
    for p, _txt in chips:
        cuts.add(max(0, min(p, n)))
    order = sorted(cuts)
    chips_at: Dict[int, List[str]] = {}
    for p, txt in sorted(chips):
        chips_at.setdefault(max(0, min(p, n)), []).append(txt)

    out: List[dict] = []
    for i, a in enumerate(order):
        for txt in chips_at.get(a, []):
            out.append({"chip": txt, "role": role_at(max(0, a - 1))})
        if i + 1 < len(order):
            b = order[i + 1]
            if b > a:
                out.append({"text": source[a:b], "role": role_at(a)})
    return out


def _display(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


# ---------------------------------------------------------------------------
# The model — pure ingest of the `_bus/headless/*` family
# ---------------------------------------------------------------------------

class MonitorModel:
    """Holds everything the surface renders, fed one bus publish at a
    time.  No bus, no canvas — directly testable, and exactly what an
    Intelligence would reconstruct from the same namespaces
    ([CXC-HMON-C10])."""

    def __init__(self) -> None:
        self.runtimes: Dict[str, dict] = {}     # rid -> {payload, seen}
        self.widget_runtime: Dict[str, str] = {}
        self.traces: Dict[str, Dict[float, dict]] = {}   # wid -> t -> rec
        self.state: Dict[str, dict] = {}
        self.health: Dict[str, dict] = {}
        self.selected: Optional[str] = None
        self.hero_t: Optional[float] = None     # None = newest (live)

    # -- ingest ---------------------------------------------------------

    def ingest(self, namespace: str, value: Any) -> bool:
        """Feed one `_bus/headless/...` publish.  Returns True when the
        model changed (the pane redraws on True)."""
        prefix = HEADLESS_BUS_PREFIX + "/"
        if not namespace.startswith(prefix) or not isinstance(value, dict):
            return False
        rest = namespace[len(prefix):]
        if rest.endswith("/trace/ctl"):
            return False                        # our own control plane
        wid, _, kind = rest.rpartition("/")
        if kind == "health":
            if "widget_ids" in value or "rate_hz" not in value:
                # the Pass-3 RUNTIME heartbeat (runtime_id + widget_ids)
                rid = str(value.get("runtime_id", wid))
                self.runtimes[rid] = {"payload": value,
                                      "seen": time.time()}
                for w in value.get("widget_ids", []) or []:
                    self.widget_runtime.setdefault(str(w), rid)
                return True
            self.health[wid] = value            # per-widget ([CXC-HMON-C5])
            return True
        if kind == "state":
            self.state[wid] = value
            return True
        if kind == "trace":
            ring = self.traces.setdefault(wid, {})
            for rec in value.get("ticks", []) or []:
                if isinstance(rec, dict) and "t" in rec:
                    ring[float(rec["t"])] = rec
            if value.get("runtime_id"):
                self.widget_runtime[wid] = str(value["runtime_id"])
            # cap the merged timeline
            if len(ring) > MAX_TICKS_KEPT:
                for k in sorted(ring)[:len(ring) - MAX_TICKS_KEPT]:
                    del ring[k]
            return True
        return False

    # -- queries ----------------------------------------------------------

    def widgets_of(self, rid: str) -> List[str]:
        got = self.runtimes.get(rid)
        ids = list((got or {}).get("payload", {}).get("widget_ids", []) or [])
        for w, r in self.widget_runtime.items():
            if r == rid and w not in ids:
                ids.append(w)
        return ids

    def ticks(self, wid: str) -> List[dict]:
        """All known ticks, newest first ([CXC-HMON-VIEW] timeline)."""
        ring = self.traces.get(wid, {})
        return [ring[t] for t in sorted(ring, reverse=True)]

    def hero_record(self) -> Optional[dict]:
        """The record the hero annotates: the replay selection when set
        ([CXC-HMON-C8]), else the newest tick."""
        if self.selected is None:
            return None
        ring = self.traces.get(self.selected, {})
        if not ring:
            return None
        if self.hero_t is not None and self.hero_t in ring:
            return ring[self.hero_t]
        return ring[max(ring)]

    def replay(self, t: Optional[float]) -> None:
        """Select a timeline tick for the hero (None -> follow newest)."""
        self.hero_t = t

    def select(self, wid: Optional[str]) -> None:
        if wid != self.selected:
            self.selected = wid
            self.hero_t = None


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------

class MonitorPane(Container):
    """The Headless Monitor as ONE engine widget (the FlowPane idiom):
    fleet rail + diagnosis column, drawn through the Canvas contract,
    zones for taps.  Scoped mode (scope_widget set — the Flow pop-out
    frame) hides the rail and pins the selection; fleet mode (the
    standalone ops frame) shows the rail and drills down.  Same surface,
    two frames ([CXC-HMON-C9])."""

    RAIL_W = 230.0

    def __init__(self, widget_id: str, *,
                 scope_widget: Optional[str] = None,
                 runtime_id: Optional[str] = None,
                 bus: Any = None, **kwargs: Any) -> None:
        super().__init__(widget_id, "monitor_pane", **kwargs)
        self.interactive = True
        self.model = MonitorModel()
        self.scope_widget = scope_widget
        self.runtime_id = runtime_id
        self._bus_override = bus
        self._bus: Any = None
        self._ctx: Any = None
        self._zones: List[Tuple[Rect, Callable[[], None]]] = []
        self._enabled: set = set()      # widgets whose trace WE enabled
        self._sel_output = 0            # hero output index within a record
        self.scroll = 0.0
        if scope_widget:
            self.model.select(scope_widget)
            if runtime_id:
                self.model.widget_runtime[scope_widget] = runtime_id

    # -- mount / bus ------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        self._ctx = ctx
        self._bus = self._bus_override or (ctx.bus if ctx else None)
        if self._bus is None:
            return

        def _cb(ns: str, new_val: Any, old_val: Any) -> None:
            # transport thread -> render thread ([CXC-R-ENGINE] M2 rule)
            if self._ctx is not None:
                self._ctx.schedule_update(
                    lambda n=ns, v=new_val: self._ingest(n, v))
            else:
                self._ingest(ns, new_val)

        self._pattern_cb = _cb
        self._bus.subscribe_pattern(HEADLESS_BUS_PREFIX + "/*", _cb)
        if self.scope_widget:
            self.request_trace(self.scope_widget)

    def on_unmount(self) -> None:
        # leave the fleet as we found it: disable every trace WE enabled
        for wid in list(self._enabled):
            self._publish_ctl(wid, {"enabled": False})
        self._enabled.clear()
        if self._bus is not None and getattr(self, "_pattern_cb", None):
            try:
                self._bus.unsubscribe_pattern(HEADLESS_BUS_PREFIX + "/*",
                                              self._pattern_cb)
            except Exception:
                pass
        super().on_unmount()

    def _ingest(self, ns: str, value: Any) -> None:
        if self.model.ingest(ns, value):
            self.request_redraw()

    # -- the ONE write: the trace-enable request ([CXC-HMON-C6]) ---------

    def _publish_ctl(self, wid: str, payload: dict) -> None:
        if self._bus is None:
            return
        try:
            ns = trace_ctl_namespace(wid)
            self._bus.ensure_namespace(ns, engine_config="json")
            self._bus.publish_state(ns, payload, source="headless-monitor")
        except Exception:
            logger.exception("trace ctl publish failed for %s", wid)

    def request_trace(self, wid: str) -> None:
        self._enabled.add(wid)
        self._publish_ctl(wid, {"enabled": True, "snapshot": time.time()})

    # -- selection ---------------------------------------------------------

    def select(self, wid: str) -> None:
        self.model.select(wid)
        self._sel_output = 0
        if wid:
            self.request_trace(wid)
        self.request_redraw()

    def replay(self, t: Optional[float]) -> None:
        """[CXC-HMON-C8]: re-annotate the hero from a past tick."""
        self.model.replay(t)
        self.request_redraw()

    # -- layout ------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        pass

    def on_scroll(self, dx: float, dy: float) -> None:
        self.scroll = max(0.0, self.scroll - dy * 24)
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        for rect, fn in self._zones:
            if _contains(rect, x, y):
                fn()
                return

    # -- drawing helpers -----------------------------------------------------

    def _chip(self, canvas: Canvas, x: float, y: float, text: str,
              color, soft, h: float = 18.0) -> float:
        w = canvas.measure_text(text).width + 12
        canvas.set_fill_color(*soft)
        canvas.fill_round_rect(x, y, w, h, 5)
        canvas.set_stroke_color(*color, 0x66)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, y, w, h, 5)
        canvas.set_fill_color(*color)
        canvas.draw_text(text, x + w / 2, _baseline(canvas, y + h / 2),
                         "center")
        return w

    def _card(self, canvas: Canvas, x: float, y: float, w: float,
              h: float, label: str = "", border=None) -> None:
        canvas.set_fill_color(*chrome.PANEL)
        canvas.fill_round_rect(x, y, w, h, 10)
        canvas.set_stroke_color(*(border or chrome.BORDER))
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(x, y, w, h, 10)
        if label:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 9.5, "medium")
            canvas.draw_text(label.upper(), x + 12, y + 18, "left")

    # -- drawing ----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_fill_color(*chrome.BG)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        self._zones = []
        pad = 12.0
        x = rect.x + pad
        if not self.scope_widget:
            self._draw_rail(canvas, Rect(x, rect.y + pad, self.RAIL_W,
                                         rect.h - 2 * pad))
            x += self.RAIL_W + pad
        main = Rect(x, rect.y + pad, rect.x + rect.w - pad - x,
                    rect.h - 2 * pad)
        self._draw_diag(canvas, main)
        canvas.restore()
        super().draw(canvas, rect)      # any child overlays

    # -- the fleet rail (the standalone frame) ----------------------------

    def _draw_rail(self, canvas: Canvas, r: Rect) -> None:
        self._card(canvas, r.x, r.y, r.w, r.h)
        canvas.set_font(MONO, 9.5, "medium")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.draw_text("FLEET", r.x + 12, r.y + 20, "left")
        n_run = len(self.model.runtimes)
        canvas.draw_text(f"{n_run} running", r.x + r.w - 12, r.y + 20,
                         "right")
        y = r.y + 34
        for rid in sorted(self.model.runtimes):
            info = self.model.runtimes[rid]
            age = time.time() - info["seen"]
            fresh = age <= 5.0
            canvas.set_fill_color(*(LIVE if fresh else chrome.FAINT))
            canvas.fill_circle(r.x + 16, y + 7, 3)
            canvas.set_fill_color(*chrome.TEXT)
            canvas.set_font(MONO, 11, "regular")
            canvas.draw_text(rid[:22], r.x + 26, y + 11, "left")
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(MONO, 8.5, "regular")
            canvas.draw_text(f"{int(age * 1000)} ms" if fresh
                             else f"{int(age)}s", r.x + r.w - 12, y + 11,
                             "right")
            y += 20
            for wid in self.model.widgets_of(rid):
                sel = wid == self.model.selected
                row = Rect(r.x + 8, y, r.w - 16, 22)
                if sel:
                    canvas.set_fill_color(*LOGIC_SOFT)
                    canvas.fill_round_rect(row.x, row.y, row.w, row.h, 6)
                    canvas.set_stroke_color(*LOGIC, 0x88)
                    canvas.set_stroke_width(1)
                    canvas.stroke_round_rect(row.x, row.y, row.w, row.h, 6)
                h = self.model.health.get(wid)
                err = bool(h and h.get("last_error"))
                canvas.set_fill_color(*(chrome.CRIT if err else LIVE))
                canvas.fill_circle(row.x + 10, y + 11, 3.5)
                canvas.set_fill_color(*(chrome.TEXT if sel else chrome.DIM))
                canvas.set_font(MONO, 10.5, "regular")
                canvas.draw_text(wid[:20], row.x + 20, y + 15, "left")
                st = self.model.state.get(wid)
                hz = ""
                if st:
                    m = st.get("schedule", {}).get("mode", "")
                    hz = {"continuous": "10 Hz", "periodic": "timer",
                          "bus_triggered": "trig"}.get(m, m)
                canvas.set_fill_color(*chrome.FAINT)
                canvas.set_font(MONO, 8.5, "regular")
                canvas.draw_text(hz, row.x + row.w - 8, y + 15, "right")
                self._zones.append((row, lambda w=wid: self.select(w)))
                y += 24
            y += 8
        if not self.model.runtimes:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 10.5, "regular")
            canvas.draw_text("no runtime heartbeats yet —", r.x + 12,
                             y + 10, "left")
            canvas.draw_text("start one with --heartbeat", r.x + 12,
                             y + 26, "left")

    # -- the diagnosis column ------------------------------------------------

    def _draw_diag(self, canvas: Canvas, r: Rect) -> None:
        wid = self.model.selected
        if not wid:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 12, "regular")
            canvas.draw_text("Select a widget in the fleet rail.",
                             r.x + 12, r.y + 30, "left")
            return
        y = r.y - self.scroll
        y = self._draw_header(canvas, r, y, wid)
        y = self._draw_hero(canvas, r, y, wid)
        y = self._draw_timeline(canvas, r, y, wid)
        half = (r.w - 12) / 2
        y2 = self._draw_state(canvas, Rect(r.x, y, half, 0), wid)
        y3 = self._draw_health(canvas, Rect(r.x + half + 12, y, half, 0),
                               wid)
        _ = max(y2, y3)

    def _draw_header(self, canvas: Canvas, r: Rect, y: float,
                     wid: str) -> float:
        self._card(canvas, r.x, y, r.w, 48)
        canvas.set_fill_color(*LOGIC_SOFT)
        canvas.fill_round_rect(r.x + 12, y + 12, 24, 24, 7)
        canvas.set_stroke_color(*LOGIC, 0x66)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(r.x + 12, y + 12, 24, 24, 7)
        canvas.set_fill_color(*LOGIC)
        canvas.set_font(MONO, 12, "medium")
        canvas.draw_text("fx", r.x + 24, _baseline(canvas, y + 24), "center")
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 14, "medium")
        canvas.draw_text(wid[:28], r.x + 46, y + 22, "left")
        st = self.model.state.get(wid)
        rid = self.model.widget_runtime.get(wid, self.runtime_id or "?")
        meta = rid
        if st:
            sched = st.get("schedule", {})
            meta = f"{sched.get('mode', '?')} · {rid}"
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 9, "regular")
        canvas.draw_text(meta[:52], r.x + 46, y + 37, "left")
        h = self.model.health.get(wid)
        if h:
            up = float(h.get("uptime_s", 0.0))
            errs = h.get("last_error")
            txt = f"running · {int(up // 60)}m {int(up % 60):02d}s · " \
                  f"{'1+ errors' if errs else '0 errors'}"
            canvas.set_font(MONO, 10, "regular")
            canvas.set_fill_color(*(chrome.CRIT if errs else LIVE))
            canvas.fill_circle(r.x + r.w - 12 -
                               canvas.measure_text(txt).width - 12,
                               y + 24, 3.5)
            canvas.draw_text(txt, r.x + r.w - 12,
                             _baseline(canvas, y + 24), "right")
        return y + 60

    def _hero_output(self, wid: str) -> Optional[dict]:
        rec = self.model.hero_record()
        if rec is None:
            return None
        outs = rec.get("outputs", [])
        if not outs:
            return None
        return outs[min(self._sel_output, len(outs) - 1)]

    def _draw_hero(self, canvas: Canvas, r: Rect, y: float,
                   wid: str) -> float:
        """[CXC-HMON-C7] the annotated tick."""
        h = 118.0
        self._card(canvas, r.x, y, r.w, h, border=LOGIC)
        rec = self.model.hero_record()
        out = self._hero_output(wid)
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 9, "medium")
        if out is not None:
            canvas.draw_text(f"OUTPUT · {out.get('namespace', '?')} = "
                             f"{'expr' if out.get('expr') else out.get('attr')}",
                             r.x + 12, y + 18, "left")
        else:
            canvas.draw_text("OUTPUT", r.x + 12, y + 18, "left")
        if rec is not None:
            live = self.model.hero_t is None
            canvas.set_fill_color(*(chrome.ACCENT if live else WARN_SOFT[:3]))
            canvas.draw_text(
                f"tick t = {rec.get('t')}s" + ("" if live else " · replay"),
                r.x + r.w - 12, y + 18, "right")
        if rec is None or out is None:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 11, "regular")
            canvas.draw_text("waiting for a traced tick…", r.x + 12,
                             y + 48, "left")
            return y + h + 12
        # output selector chips when the widget has several outputs
        outs = rec.get("outputs", [])
        if len(outs) > 1:
            cx = r.x + 12
            for i, o in enumerate(outs):
                label = str(o.get("namespace", "?"))[-18:]
                on = i == min(self._sel_output, len(outs) - 1)
                canvas.set_font(MONO, 8.5, "regular")
                w = self._chip(canvas, cx, y + 24, label,
                               chrome.ACCENT if on else chrome.DIM,
                               chrome.ACCENT_SOFT if on
                               else (0, 0, 0, 0), h=16)
                self._zones.append((Rect(cx, y + 24, w, 16),
                                    lambda idx=i: self._pick_output(idx)))
                cx += w + 6
            expr_y = y + 46
        else:
            expr_y = y + 30
        # the expression strip
        strip = Rect(r.x + 12, expr_y, r.w - 24, 34)
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(strip.x, strip.y, strip.w, strip.h, 8)
        canvas.save()
        canvas.clip_rect(strip.x, strip.y, strip.w, strip.h)
        tx = strip.x + 10
        ty = strip.y + strip.h / 2
        canvas.set_font(MONO, 12, "regular")
        if out.get("expr"):
            segments = annotate(str(out["expr"]), out.get("trace", []))
            for seg in segments:
                if "chip" in seg:
                    canvas.set_font(MONO, 9.5, "regular")
                    tx += self._chip(canvas, tx + 2, ty - 8, seg["chip"],
                                     chrome.ACCENT
                                     if seg["role"] != "dead" else chrome.FAINT,
                                     chrome.ACCENT_SOFT, h=16) + 4
                    canvas.set_font(MONO, 12, "regular")
                    continue
                role = seg["role"]
                text = seg["text"]
                tw = canvas.measure_text(text).width
                if role == "taken":
                    canvas.set_fill_color(*GOOD_SOFT)
                    canvas.fill_round_rect(tx - 1, ty - 10, tw + 2, 20, 4)
                    canvas.set_fill_color(*chrome.TEXT)
                elif role == "dead":
                    canvas.set_fill_color(*chrome.FAINT, 0x77)
                else:
                    canvas.set_fill_color(*chrome.DIM)
                canvas.draw_text(text, tx, _baseline(canvas, ty), "left")
                if role == "dead":      # strike the never-evaluated branch
                    canvas.set_stroke_color(*chrome.FAINT, 0x99)
                    canvas.set_stroke_width(1)
                    canvas.draw_line(tx, ty, tx + tw, ty)
                tx += tw
        else:
            canvas.set_fill_color(*chrome.DIM)
            canvas.draw_text(f"widget attr '{out.get('attr')}'",
                             tx, _baseline(canvas, ty), "left")
        canvas.restore()
        # result row + pill
        ry = expr_y + 44
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 10, "regular")
        canvas.draw_text("evaluates to", r.x + 12, ry + 6, "left")
        canvas.set_fill_color(*chrome.ACCENT)
        canvas.set_font(MONO, 13, "medium")
        canvas.draw_text(f"→ {_display(out.get('value'))}", r.x + 92,
                         ry + 7, "left")
        pill, ok = self._verdict(out)
        canvas.set_font(MONO, 9, "regular")
        pw = canvas.measure_text(pill).width + 18
        px = r.x + r.w - 12 - pw
        canvas.set_fill_color(*(GOOD_SOFT if ok else chrome.PANEL2))
        canvas.fill_round_rect(px, ry - 6, pw, 20, 10)
        canvas.set_stroke_color(*(LIVE if ok else chrome.BORDER), 0x88)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(px, ry - 6, pw, 20, 10)
        canvas.set_fill_color(*(LIVE if ok else chrome.FAINT))
        canvas.draw_text(pill, px + pw / 2, _baseline(canvas, ry + 4),
                         "center")
        return y + h + 12

    def _pick_output(self, idx: int) -> None:
        self._sel_output = idx
        self.request_redraw()

    @staticmethod
    def _verdict(out: dict) -> Tuple[str, bool]:
        if out.get("error"):
            return (f"error: {str(out['error'])[:36]}", False)
        if out.get("published"):
            prev = out.get("previous")
            return (f"published ({_display(out.get('value'))} ← "
                    f"{_display(prev)})", True)
        return ("suppressed (unchanged)", False)

    def _draw_timeline(self, canvas: Canvas, r: Rect, y: float,
                       wid: str) -> float:
        ticks = self.model.ticks(wid)[:10]
        h = 40.0 + 20.0 * max(1, len(ticks))
        self._card(canvas, r.x, y, r.w, h, "Tick trace")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 8.5, "regular")
        canvas.draw_text("click a tick to replay it above · newest first",
                         r.x + r.w - 12, y + 18, "right")
        ry = y + 30
        if not ticks:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 10.5, "regular")
            canvas.draw_text("no ticks traced yet", r.x + 12, ry + 12,
                             "left")
        hero = self.model.hero_record()
        for rec in ticks:
            row = Rect(r.x + 8, ry, r.w - 16, 19)
            is_hero = hero is not None and rec.get("t") == hero.get("t")
            if is_hero:
                canvas.set_fill_color(*chrome.PANEL3)
                canvas.fill_round_rect(row.x, row.y, row.w, row.h, 5)
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(MONO, 9.5, "regular")
            canvas.draw_text(f"{rec.get('t')}s", row.x + 4, ry + 13, "left")
            inp = " · ".join(
                f"{k.rsplit('/', 1)[-1]} {_display(v)}"
                for k, v in list(rec.get("inputs", {}).items())[:2])
            canvas.set_fill_color(*chrome.DIM)
            canvas.draw_text(inp[:44], row.x + 64, ry + 13, "left")
            outs = rec.get("outputs", [])
            o = outs[min(self._sel_output, len(outs) - 1)] if outs else {}
            canvas.set_fill_color(*chrome.ACCENT)
            canvas.draw_text(_display(o.get("value", "—")),
                             row.x + row.w - 78, ry + 13, "right")
            if o.get("error"):
                verdict, color = "error", chrome.CRIT
            elif o.get("published"):
                verdict, color = "published", LIVE
            else:
                verdict, color = "suppressed", chrome.FAINT
            canvas.set_fill_color(*color)
            canvas.set_font(MONO, 8, "regular")
            canvas.draw_text(verdict, row.x + row.w - 4, ry + 13, "right")
            self._zones.append(
                (row, lambda t=rec.get("t"): self.replay(float(t))))
            ry += 20
        return y + h + 12

    def _draw_state(self, canvas: Canvas, r: Rect, wid: str) -> float:
        st = self.model.state.get(wid) or {}
        rows: List[Tuple[str, str]] = []
        for k, v in (st.get("vars") or {}).items():
            rows.append((f"${k}", _display(v)))
        sched = st.get("schedule") or {}
        if sched:
            desc = str(sched.get("mode", "?"))
            if sched.get("next_in_s") is not None:
                desc += f" · next +{sched['next_in_s']:.2f}s"
            elif sched.get("last_trigger"):
                lt = sched["last_trigger"]
                desc += f" · trig {lt.get('namespace', '?')}"
            rows.append(("schedule", desc))
        for b in st.get("behaviors") or []:
            v = "active" if b.get("active") else "idle"
            if b.get("elapsed_s") is not None:
                v += f" {b['elapsed_s']:.1f}s"
            rows.append((f"{b.get('behavior')}→{b.get('target')}", v))
        lic = st.get("last_input_change")
        if lic:
            rows.append(("last input chg",
                         f"{lic.get('namespace', '?').rsplit('/', 1)[-1]} "
                         f"{lic.get('age_s', 0):.1f}s ago"))
        h = 40.0 + 17.0 * max(1, len(rows))
        self._card(canvas, r.x, r.y, r.w, h, "Internal state")
        yy = r.y + 32
        canvas.set_font(MONO, 10, "regular")
        if not rows:
            canvas.set_fill_color(*chrome.DIM)
            canvas.draw_text("no state published yet", r.x + 12, yy + 8,
                             "left")
        for k, v in rows:
            canvas.set_fill_color(*chrome.DIM)
            canvas.draw_text(k[:20], r.x + 12, yy + 8, "left")
            canvas.set_fill_color(*chrome.TEXT)
            canvas.draw_text(str(v)[:30], r.x + r.w - 12, yy + 8, "right")
            yy += 17
        return r.y + h

    def _draw_health(self, canvas: Canvas, r: Rect, wid: str) -> float:
        h = 118.0
        self._card(canvas, r.x, r.y, r.w, h, "Health")
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(MONO, 8.5, "regular")
        canvas.draw_text("tick rate · 30s", r.x + r.w - 12, r.y + 18,
                         "right")
        hp = self.model.health.get(wid) or {}
        # sparkline ([CXC-HMON-VIEW]): 30 1-second buckets
        hist = hp.get("rate_history") or []
        sp = Rect(r.x + 12, r.y + 26, r.w - 24, 30)
        if hist:
            peak = max(max(hist), 1)
            bw = sp.w / max(len(hist), 1)
            canvas.set_fill_color(*chrome.ACCENT, 0xCC)
            for i, v in enumerate(hist):
                bh = sp.h * (v / peak)
                canvas.fill_rect(sp.x + i * bw, sp.y + sp.h - bh,
                                 max(1.0, bw - 2), bh)
        else:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 9.5, "regular")
            canvas.draw_text("—", sp.x, sp.y + 18, "left")
        yy = r.y + 64
        canvas.set_font(MONO, 10, "regular")
        for k, v in (("rate", f"{hp.get('rate_hz', '—')} Hz"),
                     ("ticks", f"{hp.get('ticks', '—')}"),
                     ("last error",
                      str(hp.get("last_error") or "—")[:30])):
            canvas.set_fill_color(*chrome.DIM)
            canvas.draw_text(k, r.x + 12, yy + 8, "left")
            canvas.set_fill_color(*(chrome.CRIT if k == "last error"
                                    and hp.get("last_error") else
                                    chrome.TEXT))
            canvas.draw_text(v, r.x + r.w - 12, yy + 8, "right")
            yy += 17
        return r.y + h


# ---------------------------------------------------------------------------
# Hosts ([CXC-HMON-HOSTS]): one surface, two frames
# ---------------------------------------------------------------------------

def build_monitor_composition(*, scope_widget: Optional[str] = None,
                              runtime_id: Optional[str] = None,
                              bus: Any = None,
                              width: int = 1240,
                              height: int = 760) -> Composition:
    """The Monitor as an ordinary engine Composition — scoped (Flow
    pop-out frame, no rail) or fleet (standalone ops frame)."""
    if scope_widget:
        width, height = min(width, 900), min(height, 700)
    pane = MonitorPane("monitor", scope_widget=scope_widget,
                       runtime_id=runtime_id, bus=bus)
    title = "Headless Monitor — " + (scope_widget or "fleet")
    comp = Composition(root=pane, title=title, width=width, height=height,
                       background=chrome.BG,
                       app_id="composeux-headless-monitor")
    return comp


def build_monitor_app(*, bus: Any = None, mqtt: Optional[str] = None,
                      scope_widget: Optional[str] = None,
                      runtime_id: Optional[str] = None,
                      windowed: bool = True,
                      width: int = 1240, height: int = 760) -> Any:
    """A runnable Monitor RenderApp.  *bus* shares an in-process bus
    (tests / embedding); *mqtt* carries it to a real fleet."""
    from ..render.engine.runtime import RenderApp
    from composeio.bus import ComposeIOBus
    if bus is None and mqtt:
        from composeio.transport_mqtt import MQTTTransport
        broker, _, port = mqtt.partition(":")
        import os
        # distinct client_id: two peers sharing one get rc=7-kicked by
        # the broker and silently drop events (the BINDEDIT ops finding)
        bus = ComposeIOBus(
            bus_id=f"headless-monitor-{os.getpid()}",
            global_transport=MQTTTransport(
                broker=broker, port=int(port or 1883),
                client_id=f"headless-monitor-{os.getpid()}"))
        # WE made this bus, so WE start it (RenderApp only starts buses
        # it owns) — the transport must be up before the pane's mount
        # subscribes `_bus/headless/*` and requests tracing.
        bus.start()
        time.sleep(2.0)     # post-connect settle (the FLAUTH gotcha)
    comp = build_monitor_composition(scope_widget=scope_widget,
                                     runtime_id=runtime_id,
                                     width=width, height=height)
    return RenderApp(comp, windowed=windowed, bus=bus)


def main() -> None:
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    ap = argparse.ArgumentParser(
        description="ComposeUX Headless Monitor ([SPEC:CXC-HMON]) — "
                    "Diagnosis for headless widgets")
    ap.add_argument("--widget", default=None,
                    help="scope to ONE widget (the Flow pop-out frame)")
    ap.add_argument("--runtime", default=None,
                    help="runtime id hint for the scoped header")
    ap.add_argument("--mqtt", metavar="BROKER[:PORT]",
                    help="observe a fleet over MQTT (default port 1883)")
    ap.add_argument("--width", type=int, default=1240)
    ap.add_argument("--height", type=int, default=760)
    args = ap.parse_args()
    app = build_monitor_app(mqtt=args.mqtt, scope_widget=args.widget,
                            runtime_id=args.runtime,
                            width=args.width, height=args.height)
    app.run()


if __name__ == "__main__":
    main()
