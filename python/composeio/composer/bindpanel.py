"""BindingsPanel — the Composer's visual binding editor ([SPEC:CXC-BINDEDIT]).

The WC tab that authors the [SPEC:CXC-BIND] model with ZERO raw YAML
typed ([CXC-BE-BINDING] / DP-CXC-3):

    - bind any bindable attribute of the selected widget to a namespace
      (picker over the design's declared namespaces, or type one) —
      emits the bare-string / object binding form into the RAW spec
      ([CXC-BE-RAWSPEC]: edits go through composer/config/widget_changed
      into LayerDef.properties, never the parsed widget tree);
    - author a literal `map:` (enum -> value) — arithmetic is refused and
      captured as a DSL use-case candidate ([CXC-BE-C8]);
    - attach state-driven behaviors: trigger (is / is_exact / truthy /
      in / always) + behavior + params, with the param fields rendered
      from the ENGINE's introspection tables ([CXC-BE-INTROSPECT]) —
      nothing is hardcoded here;
    - a PREVIEW BUS section to flip trigger namespaces on the design bus
      so a configured blink/rotate is exercised live in the WW.

Everything is drawn through the Canvas contract; every edit is a bus
command ([DP-CXC-1]).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Tuple

import yaml

from ..render.canvas import Canvas, Size
from ..render.widget import Constraints, Rect, Widget
from ..render.engine.layout import Container, _contains
from ..render.engine.behaviors import (BEHAVIOR_DEFAULT_TARGET,
                                       BEHAVIOR_NAMES,
                                       BEHAVIOR_PARAM_SCHEMA, TRIGGER_KINDS)
from ..render.engine.bindings import (NAMESPACE_STRING_ATTRS,
                                      bindable_attrs_for,
                                      looks_like_expression, validate_map)

from . import chrome
from .chrome import FONT, MONO, FormInput, _baseline
from .document import LayerDef

logger = logging.getLogger("composeio.composer.bindpanel")

_CHIP_H = 22.0
_CHIP_PAD = 9.0
_CHIP_GAP = 6.0
_ROW_H = 40.0
_INPUT_H = 50.0


def _scalar(text: str) -> Any:
    """Typed scalar from a form field: YAML rules (on -> True, 3 -> int),
    falling back to the raw string.  Empty -> None."""
    t = text.strip()
    if not t:
        return None
    try:
        v = yaml.safe_load(t)
    except Exception:
        return t
    return v


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "on" if v else "off"
    return str(v)


def _chip_w(text: str) -> float:
    return len(text) * 6.3 + 2 * _CHIP_PAD


# ---------------------------------------------------------------------------
# Blocks — the panel is a scrollable stack of these
# ---------------------------------------------------------------------------

class _Block:
    rect: Optional[Rect] = None
    widget: Optional[Widget] = None          # widget-backed block

    def measure(self, width: float) -> float:
        return 24.0

    def draw(self, canvas: Canvas, rect: Rect) -> None:  # pragma: no cover
        pass

    def tap(self, x: float, y: float) -> bool:
        return False


class _Gap(_Block):
    def __init__(self, h: float = 8.0) -> None:
        self.h = h

    def measure(self, width: float) -> float:
        return self.h


class _Section(_Block):
    """Section header with an optional right-aligned action chip."""

    def __init__(self, text: str, action: str = "",
                 on_action: Optional[Callable[[], None]] = None) -> None:
        self.text = text
        self.action = action
        self.on_action = on_action

    def measure(self, width: float) -> float:
        return 26.0

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.set_fill_color(*chrome.FAINT)
        canvas.set_font(FONT, 10.5, "medium")
        canvas.draw_text(self.text, rect.x + 4,
                         _baseline(canvas, rect.y + rect.h / 2), "left")
        if self.action:
            bw = _chip_w(self.action)
            bx = rect.x + rect.w - bw - 4
            by = rect.y + (rect.h - 20) / 2
            canvas.set_fill_color(*chrome.ACCENT_SOFT)
            canvas.fill_round_rect(bx, by, bw, 20, 6)
            canvas.set_fill_color(*chrome.ACCENT)
            canvas.set_font(FONT, 11, "medium")
            canvas.draw_text(self.action, bx + bw / 2,
                             _baseline(canvas, by + 10), "center")

    def tap(self, x: float, y: float) -> bool:
        if self.action and self.on_action and self.rect is not None:
            bw = _chip_w(self.action)
            if x >= self.rect.x + self.rect.w - bw - 8:
                self.on_action()
                return True
        return False


class _Note(_Block):
    """Small informational / error line (wraps to two lines max)."""

    def __init__(self, text: str, color: tuple = None, size: float = 10.5,
                 mono: bool = False) -> None:
        self.text = text
        self.color = color or chrome.FAINT
        self.size = size
        self.mono = mono

    def _wrap(self, width: float) -> List[str]:
        per = max(10, int(width / (self.size * 0.56)))
        words, lines, cur = self.text.split(), [], ""
        for wd in words:
            if cur and len(cur) + 1 + len(wd) > per:
                lines.append(cur)
                cur = wd
            else:
                cur = f"{cur} {wd}".strip()
        if cur:
            lines.append(cur)
        return lines[:3]

    def measure(self, width: float) -> float:
        return 6 + len(self._wrap(width - 8)) * (self.size + 4)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.set_fill_color(*self.color)
        canvas.set_font(MONO if self.mono else FONT, self.size, "regular")
        y = rect.y + self.size + 2
        for line in self._wrap(rect.w - 8):
            canvas.draw_text(line, rect.x + 4, y, "left")
            y += self.size + 4


class _Row(_Block):
    """List row: main text + caption, optional edit-on-tap and × remove."""

    def __init__(self, main: str, caption: str = "",
                 on_tap: Optional[Callable[[], None]] = None,
                 on_remove: Optional[Callable[[], None]] = None,
                 accent: bool = False) -> None:
        self.main = main
        self.caption = caption
        self.on_tap = on_tap
        self.on_remove = on_remove
        self.accent = accent

    def measure(self, width: float) -> float:
        return _ROW_H if self.caption else 30.0

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(rect.x, rect.y + 2, rect.w, rect.h - 4, 7)
        canvas.set_stroke_color(*chrome.BORDER)
        canvas.set_stroke_width(1)
        canvas.stroke_round_rect(rect.x, rect.y + 2, rect.w, rect.h - 4, 7)
        canvas.set_fill_color(*(chrome.ACCENT if self.accent else chrome.TEXT))
        canvas.set_font(MONO, 11, "regular")
        ty = rect.y + 16 if self.caption else _baseline(
            canvas, rect.y + rect.h / 2)
        canvas.draw_text(self.main, rect.x + 10, ty, "left")
        if self.caption:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(MONO, 9.5, "regular")
            canvas.draw_text(self.caption, rect.x + 10, rect.y + 30, "left")
        if self.on_remove is not None:
            canvas.set_fill_color(*chrome.DIM)
            canvas.set_font(FONT, 13, "regular")
            canvas.draw_text("×", rect.x + rect.w - 14,
                             _baseline(canvas, rect.y + rect.h / 2), "center")

    def tap(self, x: float, y: float) -> bool:
        r = self.rect
        if r is None:
            return False
        if self.on_remove is not None and x >= r.x + r.w - 26:
            self.on_remove()
            return True
        if self.on_tap is not None:
            self.on_tap()
            return True
        return False


class _Chips(_Block):
    """Wrapping chip picker.  ``selected`` may be a value or a set."""

    def __init__(self, options: List[str], selected: Any,
                 on_pick: Callable[[str], None], label: str = "") -> None:
        self.options = [str(o) for o in options]
        self.selected = selected
        self.on_pick = on_pick
        self.label = label

    def _layout(self, width: float) -> List[Tuple[str, float, float, float]]:
        out, x, y = [], 0.0, (16.0 if self.label else 0.0)
        for opt in self.options:
            w = _chip_w(opt)
            if x > 0 and x + w > width:
                x, y = 0.0, y + _CHIP_H + _CHIP_GAP
            out.append((opt, x, y, w))
            x += w + _CHIP_GAP
        return out

    def measure(self, width: float) -> float:
        lay = self._layout(width - 8)
        if not lay:
            return 16.0 if self.label else 4.0
        return lay[-1][2] + _CHIP_H + 6

    def _is_sel(self, opt: str) -> bool:
        if isinstance(self.selected, (set, frozenset, list)):
            return opt in self.selected
        return opt == self.selected

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.label:
            canvas.set_fill_color(*chrome.FAINT)
            canvas.set_font(FONT, 10, "medium")
            canvas.draw_text(self.label, rect.x + 4, rect.y + 10, "left")
        canvas.set_font(MONO, 10.5, "regular")
        for opt, cx, cy, cw in self._layout(rect.w - 8):
            px, py = rect.x + 4 + cx, rect.y + cy
            sel = self._is_sel(opt)
            canvas.set_fill_color(*(chrome.ACCENT_SOFT if sel
                                    else chrome.PANEL2))
            canvas.fill_round_rect(px, py, cw, _CHIP_H, 6)
            canvas.set_stroke_color(*(chrome.ACCENT if sel
                                      else chrome.BORDER))
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(px, py, cw, _CHIP_H, 6)
            canvas.set_fill_color(*(chrome.ACCENT if sel else chrome.TEXT))
            canvas.draw_text(opt, px + cw / 2,
                             _baseline(canvas, py + _CHIP_H / 2), "center")

    def tap(self, x: float, y: float) -> bool:
        r = self.rect
        if r is None:
            return False
        for opt, cx, cy, cw in self._layout(r.w - 8):
            px, py = r.x + 4 + cx, r.y + cy
            if px <= x <= px + cw and py <= y <= py + _CHIP_H:
                self.on_pick(opt)
                return True
        return False


class _Buttons(_Block):
    """Right-aligned row of chip buttons: [(label, accent, cb), ...]."""

    def __init__(self, items: List[Tuple[str, bool, Callable[[], None]]]) -> None:
        self.items = items

    def measure(self, width: float) -> float:
        return 30.0

    def _layout(self, rect: Rect) -> List[Tuple[str, bool, Callable, Rect]]:
        out = []
        x = rect.x + rect.w - 4
        for label, accent, cb in reversed(self.items):
            w = _chip_w(label) + 8
            x -= w
            out.append((label, accent, cb, Rect(x, rect.y + 3, w, 24)))
            x -= 8
        return out

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        for label, accent, _cb, r in self._layout(rect):
            canvas.set_fill_color(*(chrome.ACCENT_SOFT if accent
                                    else chrome.PANEL2))
            canvas.fill_round_rect(r.x, r.y, r.w, r.h, 6)
            canvas.set_stroke_color(*(chrome.ACCENT if accent
                                      else chrome.BORDER_STRONG))
            canvas.set_stroke_width(1)
            canvas.stroke_round_rect(r.x, r.y, r.w, r.h, 6)
            canvas.set_fill_color(*(chrome.ACCENT if accent else chrome.TEXT))
            canvas.set_font(FONT, 11.5, "medium")
            canvas.draw_text(label, r.x + r.w / 2,
                             _baseline(canvas, r.y + r.h / 2), "center")

    def tap(self, x: float, y: float) -> bool:
        if self.rect is None:
            return False
        for _label, _accent, cb, r in self._layout(self.rect):
            if _contains(r, x, y):
                cb()
                return True
        return False


class _NsTestRow(_Block):
    """Preview-bus namespace row: name · current value · [on][off] pubs."""

    def __init__(self, ns: str, value: Any,
                 publish: Callable[[str, Any], None]) -> None:
        self.ns = ns
        self.value = value
        self.publish = publish

    def measure(self, width: float) -> float:
        return 28.0

    def _zones(self, rect: Rect) -> List[Tuple[str, Rect]]:
        zones, x = [], rect.x + rect.w - 4
        for label in ("off", "on"):
            w = _chip_w(label)
            x -= w
            zones.append((label, Rect(x, rect.y + 3, w, 20)))
            x -= 5
        return zones

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.set_fill_color(*chrome.PANEL2)
        canvas.fill_round_rect(rect.x, rect.y + 1, rect.w, rect.h - 2, 6)
        canvas.set_fill_color(*chrome.TEXT)
        canvas.set_font(MONO, 10.5, "regular")
        canvas.draw_text(self.ns, rect.x + 8,
                         _baseline(canvas, rect.y + rect.h / 2), "left")
        val = "—" if self.value is None else _fmt(self.value)
        canvas.set_fill_color(*chrome.DIM)
        canvas.set_font(MONO, 9.5, "regular")
        canvas.draw_text(val[:12], rect.x + rect.w - 86,
                         _baseline(canvas, rect.y + rect.h / 2), "right")
        for label, r in self._zones(rect):
            on = label == "on"
            canvas.set_fill_color(*(chrome.ACCENT_SOFT if on
                                    else chrome.PANEL3))
            canvas.fill_round_rect(r.x, r.y, r.w, r.h, 5)
            canvas.set_fill_color(*(chrome.ACCENT if on else chrome.DIM))
            canvas.set_font(MONO, 9.5, "medium")
            canvas.draw_text(label, r.x + r.w / 2,
                             _baseline(canvas, r.y + r.h / 2), "center")

    def tap(self, x: float, y: float) -> bool:
        if self.rect is None:
            return False
        for label, r in self._zones(self.rect):
            if _contains(r, x, y):
                self.publish(self.ns, label)
                return True
        return False


class _WidgetBlock(_Block):
    def __init__(self, widget: Widget, h: float = _INPUT_H) -> None:
        self.widget = widget
        self.h = h

    def measure(self, width: float) -> float:
        return self.h

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        pass          # the child widget draws itself (panel draw pass)


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------

class BindingsPanel(Container):
    """The WC Bindings tab: attribute bindings + behaviors, visually."""

    def __init__(self, widget_id: str, core: Any, **kwargs: Any) -> None:
        super().__init__(widget_id, "bindings_panel", **kwargs)
        self.core = core
        self.interactive = True
        self.scroll = 0.0
        self.mode = "list"                    # list | bind | behavior
        self.form_error = ""
        self.form_logged = ""                 # DSL-candidate confirmation
        # bind-form state
        self.f_attr: str = ""
        self.f_ns: str = ""
        self.f_invert = False
        self.f_map_rows = 0                   # visible map k/v input pairs
        # behavior-form state
        self.b_edit_index: Optional[int] = None
        self.b_ns: str = ""
        self.b_kind: str = "is"
        self.b_behavior: str = "blink"
        self.b_target: str = ""
        self._blocks: List[_Block] = []
        self._inputs: dict[str, FormInput] = {}
        self._content_h = 0.0

    # -- context ------------------------------------------------------------

    @property
    def doc(self):
        return self.core.document

    def _selected_layer(self) -> Optional[LayerDef]:
        wid = self.core.internal_bus.get_state("composer/selection/widget_id")
        return self.doc.get_layer(str(wid)) if wid else None

    def _ns_names(self) -> List[str]:
        return [ns.name for ns in self.doc.namespaces]

    # -- persistent inputs -----------------------------------------------------

    def _input(self, name: str, label: str, initial: Optional[str] = None,
               placeholder: str = "") -> FormInput:
        fi = self._inputs.get(name)
        if fi is None:
            fi = FormInput(f"bp_{name}", label=label, placeholder=placeholder)
            fi.on_commit = lambda _v: None    # value read at Save
            self._inputs[name] = fi
            if self._ctx is not None:
                fi.on_mount(self._ctx)
        if initial is not None:
            fi.set_text(initial)
        return fi

    def _text(self, name: str) -> str:
        fi = self._inputs.get(name)
        return fi.text.strip() if fi is not None else ""

    # -- public refresh hooks -------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        for fi in self._inputs.values():
            fi.on_mount(ctx)
        self.refresh()

    def refresh(self) -> None:
        """Doc or selection changed."""
        self._rebuild()

    def on_selection_changed(self) -> None:
        if self.mode != "list":
            self.mode = "list"                # a form is per-widget
            self.form_error = ""
        self._rebuild()

    # -- form openers ---------------------------------------------------------

    def _open_bind_form(self, attr: str = "",
                        existing: Any = None) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        self.mode = "bind"
        self.form_error = ""
        self.form_logged = ""
        self.f_attr = attr
        self.f_ns = ""
        self.f_invert = False
        self.f_map_rows = 0
        self._input("bind_ns_other", "OR TYPE A NAMESPACE", initial="")
        self._input("bind_when", "WHEN (MATCH VALUE · OPTIONAL)", initial="")
        self._input("bind_default", "DEFAULT (OPTIONAL)", initial="")
        for i in range(8):
            if f"map_k{i}" in self._inputs:
                self._inputs[f"map_k{i}"].set_text("")
                self._inputs[f"map_v{i}"].set_text("")
        if isinstance(existing, str):
            if existing in self._ns_names():
                self.f_ns = existing
            else:
                self._input("bind_ns_other", "OR TYPE A NAMESPACE",
                            initial=existing)
        elif isinstance(existing, dict):
            self.f_ns = str(existing.get("namespace", ""))
            if self.f_ns not in self._ns_names():
                self._input("bind_ns_other", "OR TYPE A NAMESPACE",
                            initial=self.f_ns)
                self.f_ns = ""
            if existing.get("when") is not None:
                self._input("bind_when", "WHEN (MATCH VALUE · OPTIONAL)",
                            initial=_fmt(existing["when"]))
            if existing.get("default") is not None:
                self._input("bind_default", "DEFAULT (OPTIONAL)",
                            initial=_fmt(existing["default"]))
            self.f_invert = bool(existing.get("invert", False))
            m = existing.get("map")
            if isinstance(m, dict):
                self.f_map_rows = len(m)
                for i, (k, v) in enumerate(list(m.items())[:8]):
                    self._input(f"map_k{i}", "STATE", initial=str(k))
                    self._input(f"map_v{i}", "VALUE", initial=_fmt(v))
        self._rebuild()

    def _open_behavior_form(self, index: Optional[int] = None) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        self.mode = "behavior"
        self.form_error = ""
        self.form_logged = ""
        self.b_edit_index = index
        self.b_ns = ""
        self.b_kind = "is"
        self.b_behavior = "blink"
        self.b_target = ""
        self._input("beh_ns_other", "OR TYPE A NAMESPACE", initial="")
        self._input("beh_value", "TRIGGER VALUE", initial="on")
        for name in self._all_param_names():
            self._input(f"param_{name}", name.upper(), initial="")
        if index is not None:
            entries = layer.properties.get("behaviors", []) or []
            if 0 <= index < len(entries) and isinstance(entries[index], dict):
                e = entries[index]
                when = e.get("when")
                if when in ("always", True) or (isinstance(when, dict)
                                                and when.get("always")):
                    self.b_kind = "always"
                elif isinstance(when, dict):
                    self.b_ns = str(when.get("namespace", ""))
                    if self.b_ns not in self._ns_names():
                        self._input("beh_ns_other", "OR TYPE A NAMESPACE",
                                    initial=self.b_ns)
                        self.b_ns = ""
                    if "is_exact" in when:
                        self.b_kind = "is_exact"
                        self._input("beh_value", "TRIGGER VALUE",
                                    initial=_fmt(when["is_exact"]))
                    elif "is" in when:
                        self.b_kind = "is"
                        self._input("beh_value", "TRIGGER VALUE",
                                    initial=_fmt(when["is"]))
                    elif "in" in when:
                        self.b_kind = "in"
                        rng = when["in"]
                        self._input("beh_value", "TRIGGER VALUE",
                                    initial=", ".join(str(v) for v in rng)
                                    if isinstance(rng, list) else _fmt(rng))
                    else:
                        self.b_kind = "truthy"
                do = e.get("do", {}) if isinstance(e.get("do"), dict) else {}
                self.b_behavior = str(do.get("behavior", "blink"))
                self.b_target = str(do.get("target", ""))
                for k, v in do.items():
                    if k in ("behavior", "target"):
                        continue
                    self._input(f"param_{k}", k.upper(), initial=_fmt(v))
        self._rebuild()

    @staticmethod
    def _all_param_names() -> List[str]:
        names: List[str] = []
        for schema in BEHAVIOR_PARAM_SCHEMA.values():
            for n in schema:
                if n not in names:
                    names.append(n)
        return names

    # -- saves ---------------------------------------------------------------

    def _resolved_ns(self, chip_ns: str, other_key: str) -> str:
        other = self._text(other_key)
        return other or chip_ns

    def _ensure_declared(self, ns: str) -> None:
        if ns and self.doc.get_namespace(ns) is None:
            # [CXC-FL-DISCOVERY]: binding to a namespace this design does
            # not publish but the BUS carries = a subscribed-EXTERNAL
            # declaration, engine from discovery when known.  A pattern is
            # always an outside-family subscription ([CXC-FL-PATTERN]).
            data: dict = {"action": "created", "name": ns,
                          "state_engine": "json"}
            disc = getattr(self.core, "discovery", None)
            from composeio.bus import is_pattern
            if is_pattern(ns):
                data["external"] = True
            elif disc is not None:
                for e in disc.snapshot():
                    if e.name == ns and e.live:
                        data["external"] = True
                        if e.engine and not e.engine.startswith(("~", "?")):
                            data["state_engine"] = e.engine
                        break
            self.core.internal_bus.publish_event(
                "composer/config/namespace_changed", "do", data=data,
                source="bindings_panel")

    def _log_dsl_need(self, need: str, context: str) -> None:
        layer = self._selected_layer()
        self.core.internal_bus.publish_event(
            "composer/dsl/candidate", "do",
            data={"need": need, "context": context,
                  "widget": layer.widget_id if layer else "",
                  "attr": self.f_attr or self.b_behavior},
            source="bindings_panel")
        self.form_logged = "logged as a DSL use-case candidate — " \
                           "the declarative layer stays literal"

    def _save_binding(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        attr = self.f_attr
        ns = self._resolved_ns(self.f_ns, "bind_ns_other")
        if not attr:
            self.form_error = "pick an attribute"
            self._rebuild()
            return
        if not ns:
            self.form_error = "pick or type a namespace"
            self._rebuild()
            return
        when = _scalar(self._text("bind_when"))
        default = _scalar(self._text("bind_default"))
        vmap: dict = {}
        for i in range(max(self.f_map_rows, 0)):
            k, v = self._text(f"map_k{i}"), self._text(f"map_v{i}")
            if not k:
                continue
            if looks_like_expression(v) or looks_like_expression(k):
                self.form_error = f"map '{k}: {v}' looks like arithmetic — " \
                                  "maps are literal lookups only"
                self._log_dsl_need(f"map {k}: {v}", "binding map")
                self._rebuild()
                return
            vmap[k] = _scalar(v)
        if vmap and validate_map(vmap, f"{layer.widget_id}.{attr}") is None:
            self.form_error = "map refused: literal lookups only"
            self._log_dsl_need(str(vmap), "binding map")
            self._rebuild()
            return
        for label, v in (("when", when), ("default", default)):
            if looks_like_expression(v):
                self.form_error = f"'{v}' looks like an expression — " \
                                  f"{label} takes a literal"
                self._log_dsl_need(str(v), f"binding {label}")
                self._rebuild()
                return
        # simplest [SPEC:CXC-BIND] form that expresses the intent
        if (attr in NAMESPACE_STRING_ATTRS and when is None
                and default is None and not self.f_invert and not vmap):
            value: Any = ns
        else:
            value = {"namespace": ns}
            if when is not None:
                value["when"] = when
            if self.f_invert:
                value["invert"] = True
            if vmap:
                value["map"] = vmap
            if default is not None:
                value["default"] = default
        self._ensure_declared(ns)
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": attr,
                  "value": value},
            source="bindings_panel")
        self.mode = "list"
        self.form_error = ""
        self._rebuild()

    def _save_behavior(self) -> None:
        layer = self._selected_layer()
        if layer is None:
            return
        kind = self.b_kind
        ns = self._resolved_ns(self.b_ns, "beh_ns_other")
        if kind != "always" and not ns:
            self.form_error = "pick or type a trigger namespace"
            self._rebuild()
            return
        raw_val = self._text("beh_value")
        if kind in ("is", "is_exact") and looks_like_expression(raw_val):
            self.form_error = f"'{raw_val}' looks like an expression — " \
                              "triggers compare literals"
            self._log_dsl_need(raw_val, "behavior trigger")
            self._rebuild()
            return
        when: Any
        if kind == "always":
            when = "always"
        elif kind == "truthy":
            when = {"namespace": ns}
        elif kind == "in":
            parts = [p.strip() for p in raw_val.split(",") if p.strip()]
            try:
                rng = [float(parts[0]), float(parts[1])]
            except (IndexError, ValueError):
                self.form_error = "range trigger needs 'lo, hi'"
                self._rebuild()
                return
            when = {"namespace": ns, "in": rng}
        else:
            when = {"namespace": ns, kind: _scalar(raw_val)}
        name = self.b_behavior
        target = self.b_target or BEHAVIOR_DEFAULT_TARGET.get(name, "opacity")
        do: dict = {"behavior": name, "target": target}
        schema = BEHAVIOR_PARAM_SCHEMA.get(name, {})
        for pname, meta in schema.items():
            txt = self._text(f"param_{pname}")
            if not txt:
                if meta.get("required"):
                    self.form_error = f"{name} needs '{pname}'"
                    self._rebuild()
                    return
                continue
            if looks_like_expression(txt):
                self.form_error = f"'{txt}' looks like arithmetic — params " \
                                  "are literals ($vars ok in Source)"
                self._log_dsl_need(txt, f"behavior param {pname}")
                self._rebuild()
                return
            if meta.get("type") == "number":
                try:
                    v: Any = float(txt)
                    v = int(v) if v == int(v) else v
                except ValueError:
                    self.form_error = f"'{pname}' must be a number"
                    self._rebuild()
                    return
            elif meta.get("type") == "enum":
                if txt not in meta.get("values", ()):
                    self.form_error = f"'{pname}' must be one of " \
                                      f"{', '.join(meta.get('values', ()))}"
                    self._rebuild()
                    return
                v = txt
            else:
                v = _scalar(txt)
            do[pname] = v
        entry = {"when": when, "do": do}
        entries = list(layer.properties.get("behaviors", []) or [])
        if self.b_edit_index is not None and \
                0 <= self.b_edit_index < len(entries):
            entries[self.b_edit_index] = entry
        else:
            entries.append(entry)
        if kind != "always":
            self._ensure_declared(ns)
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": "behaviors",
                  "value": entries},
            source="bindings_panel")
        self.mode = "list"
        self.form_error = ""
        self._rebuild()

    # -- removals -------------------------------------------------------------

    def _remove_binding(self, layer: LayerDef, attr: str) -> None:
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": attr,
                  "value": None},
            source="bindings_panel")

    def _remove_behavior(self, layer: LayerDef, index: int) -> None:
        entries = list(layer.properties.get("behaviors", []) or [])
        if 0 <= index < len(entries):
            entries.pop(index)
        self.core.internal_bus.publish_event(
            "composer/config/widget_changed", "do",
            data={"widget_id": layer.widget_id, "property": "behaviors",
                  "value": entries or None},
            source="bindings_panel")

    # -- block builders ---------------------------------------------------------

    def _rebuild(self) -> None:
        blocks: List[_Block] = []
        layer = self._selected_layer()
        if self.mode == "bind" and layer is not None:
            self._build_bind_form(blocks, layer)
        elif self.mode == "behavior" and layer is not None:
            self._build_behavior_form(blocks, layer)
        else:
            self.mode = "list"
            self._build_list(blocks, layer)
        self._blocks = blocks
        self.children = []
        for b in blocks:
            if b.widget is not None:
                self.children.append(b.widget)
            if isinstance(b, _PairBlock):
                self.children.append(b.second)
        if self._ctx is not None:
            for ch in self.children:
                if getattr(ch, "_ctx", None) is None:
                    ch.on_mount(self._ctx)
        if self.arranged_rect is not None:
            self.arrange(self.arranged_rect)
        self.request_redraw()

    def _describe_when(self, when: Any) -> str:
        if when in ("always", True) or (isinstance(when, dict)
                                        and when.get("always")):
            return "always"
        if isinstance(when, dict):
            ns = when.get("namespace", "?")
            if "is_exact" in when:
                return f"{ns} is exactly {_fmt(when['is_exact'])}"
            if "is" in when:
                return f"{ns} is {_fmt(when['is'])}"
            if "in" in when:
                return f"{ns} in {when['in']}"
            return f"{ns} truthy"
        return str(when)

    def _build_list(self, blocks: List[_Block],
                    layer: Optional[LayerDef]) -> None:
        if layer is None:
            blocks.append(_Gap(14))
            blocks.append(_Note("Select a widget on the canvas to bind "
                                "attributes and configure behaviors.",
                                size=11.5))
            self._build_ns_test(blocks)
            return
        blocks.append(_Gap(6))
        blocks.append(_Row(layer.widget_id,
                           f"{layer.widget_type} · bindable: " +
                           ", ".join(bindable_attrs_for(layer.widget_type)),
                           accent=True))
        blocks.append(_Gap(4))

        # -- attribute bindings
        blocks.append(_Section("ATTRIBUTE BINDINGS", "+ Bind",
                               lambda: self._open_bind_form()))
        rows = 0
        for attr in bindable_attrs_for(layer.widget_type):
            v = layer.properties.get(attr)
            if isinstance(v, dict) and v.get("namespace"):
                cap = []
                if v.get("when") is not None:
                    cap.append(f"when {_fmt(v['when'])}")
                if v.get("invert"):
                    cap.append("invert")
                if isinstance(v.get("map"), dict):
                    cap.append(f"map ×{len(v['map'])}")
                if v.get("default") is not None:
                    cap.append(f"default {_fmt(v['default'])}")
                blocks.append(_Row(
                    f"{attr} ← {v['namespace']}",
                    " · ".join(cap) or "direct",
                    on_tap=lambda a=attr, ex=v: self._open_bind_form(a, ex),
                    on_remove=lambda a=attr: self._remove_binding(layer, a)))
                rows += 1
            elif isinstance(v, str) and v and attr in NAMESPACE_STRING_ATTRS:
                blocks.append(_Row(
                    f"{attr} ← {v}", "direct (truthy)",
                    on_tap=lambda a=attr, ex=v: self._open_bind_form(a, ex),
                    on_remove=lambda a=attr: self._remove_binding(layer, a)))
                rows += 1
        if layer.namespace:
            blocks.append(_Row(
                f"value ← {layer.namespace}", "widget namespace (subscribe)",
                on_remove=lambda: self.core.internal_bus.publish_event(
                    "composer/config/widget_changed", "do",
                    data={"widget_id": layer.widget_id,
                          "property": "namespace", "value": ""},
                    source="bindings_panel")))
            rows += 1
        if rows == 0:
            blocks.append(_Note("none yet — + Bind wires an attribute to a "
                                "namespace, no YAML typed"))

        # -- behaviors
        blocks.append(_Gap(10))
        blocks.append(_Section("BEHAVIORS", "+ Add",
                               lambda: self._open_behavior_form()))
        entries = layer.properties.get("behaviors", []) or []
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            do = e.get("do", {}) if isinstance(e.get("do"), dict) else {}
            params = ", ".join(f"{k}={_fmt(v)}" for k, v in do.items()
                               if k not in ("behavior", "target"))
            blocks.append(_Row(
                f"{do.get('behavior', '?')} → {do.get('target', '?')}",
                f"when {self._describe_when(e.get('when'))}"
                + (f" · {params}" if params else ""),
                on_tap=lambda idx=i: self._open_behavior_form(idx),
                on_remove=lambda idx=i: self._remove_behavior(layer, idx)))
        if not entries:
            blocks.append(_Note("none yet — a behavior is engine-owned "
                                "presentation triggered by bus state"))

        self._build_ns_test(blocks)

        # rotated-selection edge hint ([CXC-BE-EDGES])
        rot = layer.properties.get("rotation")
        has_rot = (isinstance(rot, (int, float)) and float(rot) % 360 != 0) \
            or isinstance(rot, (str, dict)) \
            or any(isinstance(e, dict)
                   and isinstance(e.get("do"), dict)
                   and e["do"].get("target") == "rotation"
                   for e in entries)
        if has_rot:
            blocks.append(_Gap(8))
            blocks.append(_Note("note: a rotated widget selects/hits by its "
                                "upright (axis-aligned) bounds — shown "
                                "dashed on the canvas", size=10))

    def _build_ns_test(self, blocks: List[_Block]) -> None:
        blocks.append(_Gap(12))
        blocks.append(_Section("PREVIEW BUS — TEST STATES"))
        names = self._ns_names()
        if not names:
            blocks.append(_Note("no namespaces declared yet"))
            return

        def _pub(ns: str, value: str) -> None:
            self.core.design_bus.ensure_namespace(ns, engine_config="json")
            self.core.design_bus.publish_state(ns, value,
                                               source="bindings_panel")
            self._rebuild()

        for ns in names[:10]:
            try:
                cur = self.core.design_bus.get_state(ns)
            except Exception:
                cur = None
            blocks.append(_NsTestRow(ns, cur, _pub))
        blocks.append(_Note("publishes to the design (preview) bus — the "
                            "same bit an Intelligence or device would flip"))

    def _ns_picker(self, blocks: List[_Block], selected: str,
                   on_pick: Callable[[str], None], other_key: str) -> None:
        names = self._ns_names()
        if names:
            blocks.append(_Chips(names, selected, on_pick, "NAMESPACE"))
        else:
            blocks.append(_Note("no namespaces declared — type one below "
                                "(it will be declared on save)"))
        # [CXC-FL-DISCOVERY] browse the LIVE bus from the picker too — the
        # same registry the Bus·Live rail shows.  Picking one declares it
        # subscribed-external on save.
        disc = getattr(self.core, "discovery", None)
        if disc is not None:
            live = [e.name for e in disc.snapshot()
                    if e.live and e.name not in names][:8]
            if live:
                blocks.append(_Chips(live, selected, on_pick,
                                     "LIVE ON THE BUS"))
        blocks.append(_WidgetBlock(self._inputs[other_key]))

    def _form_footer(self, blocks: List[_Block],
                     on_save: Callable[[], None]) -> None:
        if self.form_error:
            blocks.append(_Note(self.form_error, color=chrome.CRIT, size=11))
        if self.form_logged:
            blocks.append(_Note("✓ " + self.form_logged, color=chrome.GOOD,
                                size=10.5))
        blocks.append(_Gap(4))
        blocks.append(_Buttons([
            ("Need arithmetic? Log it", False, self._dsl_prompt),
            ("Cancel", False, self._cancel_form),
            ("Save", True, on_save),
        ]))

    def _dsl_prompt(self) -> None:
        """The DSL-candidate affordance ([CXC-BE-C8]): not an inline-code
        field — a 'log this need' capture feeding the gating use-cases."""
        need = self._text("bind_when") or self._text("bind_default") \
            or self._text("beh_value") or "unspecified arithmetic need"
        self._log_dsl_need(need, f"user request from {self.mode} form")
        self._rebuild()

    def _cancel_form(self) -> None:
        self.mode = "list"
        self.form_error = ""
        self.form_logged = ""
        self._rebuild()

    def _build_bind_form(self, blocks: List[_Block], layer: LayerDef) -> None:
        blocks.append(_Gap(6))
        blocks.append(_Section(f"BIND AN ATTRIBUTE — {layer.widget_id}"))
        attrs = list(bindable_attrs_for(layer.widget_type))

        def _pick_attr(a: str) -> None:
            self.f_attr = a
            self._rebuild()

        blocks.append(_Chips(attrs, self.f_attr, _pick_attr, "ATTRIBUTE"))
        blocks.append(_Gap(6))

        def _pick_ns(ns: str) -> None:
            self.f_ns = "" if self.f_ns == ns else ns
            self._rebuild()

        self._ns_picker(blocks, self.f_ns, _pick_ns, "bind_ns_other")
        blocks.append(_Gap(4))
        blocks.append(_WidgetBlock(self._inputs["bind_when"]))
        blocks.append(_Note("canonical match: on = true = yes = 1 — "
                            "however the bit was published", size=9.5))

        def _toggle_invert(_o: str) -> None:
            self.f_invert = not self.f_invert
            self._rebuild()

        blocks.append(_Chips(["invert"], "invert" if self.f_invert else "",
                             _toggle_invert, "OPTIONS"))
        blocks.append(_WidgetBlock(self._inputs["bind_default"]))

        blocks.append(_Gap(6))
        blocks.append(_Section("VALUE MAP (STATE → VALUE · LITERALS ONLY)",
                               "+ Row", self._add_map_row))
        for i in range(self.f_map_rows):
            k = self._input(f"map_k{i}", "STATE")
            v = self._input(f"map_v{i}", "VALUE")
            blocks.append(_PairBlock(k, v))
        if self.f_map_rows == 0:
            blocks.append(_Note("optional — e.g. ok → green, crit → red; "
                                "arithmetic belongs in a bus workload",
                                size=10))
        self._form_footer(blocks, self._save_binding)

    def _add_map_row(self) -> None:
        if self.f_map_rows < 8:
            i = self.f_map_rows
            self._input(f"map_k{i}", "STATE", initial="")
            self._input(f"map_v{i}", "VALUE", initial="")
            self.f_map_rows += 1
            self._rebuild()

    def _build_behavior_form(self, blocks: List[_Block],
                             layer: LayerDef) -> None:
        blocks.append(_Gap(6))
        verb = "EDIT" if self.b_edit_index is not None else "ADD"
        blocks.append(_Section(f"{verb} BEHAVIOR — {layer.widget_id}"))

        def _pick_kind(k: str) -> None:
            self.b_kind = k
            self._rebuild()

        blocks.append(_Chips(list(TRIGGER_KINDS), self.b_kind, _pick_kind,
                             "TRIGGER CONDITION"))
        if self.b_kind != "always":
            def _pick_ns(ns: str) -> None:
                self.b_ns = "" if self.b_ns == ns else ns
                self._rebuild()

            self._ns_picker(blocks, self.b_ns, _pick_ns, "beh_ns_other")
        if self.b_kind in ("is", "is_exact"):
            blocks.append(_WidgetBlock(self._inputs["beh_value"]))
            if self.b_kind == "is":
                blocks.append(_Note("canonical: on = true = yes = 1 — pick "
                                    "is_exact to compare the raw string",
                                    size=9.5))
            else:
                blocks.append(_Note("exact raw-string compare — no "
                                    "canonicalization", size=9.5))
        elif self.b_kind == "in":
            self._input("beh_value", "RANGE — LO, HI")
            blocks.append(_WidgetBlock(self._inputs["beh_value"]))

        blocks.append(_Gap(6))

        def _pick_behavior(b: str) -> None:
            self.b_behavior = b
            self.b_target = ""            # re-derive the natural target
            self._rebuild()

        blocks.append(_Chips(sorted(BEHAVIOR_NAMES), self.b_behavior,
                             _pick_behavior, "BEHAVIOR"))

        target = self.b_target or BEHAVIOR_DEFAULT_TARGET.get(
            self.b_behavior, "opacity")

        def _pick_target(t: str) -> None:
            self.b_target = t
            self._rebuild()

        blocks.append(_Chips(list(bindable_attrs_for(layer.widget_type)),
                             target, _pick_target, "TARGET ATTRIBUTE"))

        # param fields straight from the engine table ([CXC-BE-INTROSPECT])
        schema = BEHAVIOR_PARAM_SCHEMA.get(self.b_behavior, {})
        if schema:
            blocks.append(_Gap(4))
            blocks.append(_Section("PARAMS — BLANK USES THE DEFAULT"))
        for pname, meta in schema.items():
            if meta.get("type") == "enum":
                cur = self._text(f"param_{pname}") or str(meta["default"])

                def _pick_enum(v: str, _p=pname) -> None:
                    self._input(f"param_{_p}", _p.upper(), initial=v)
                    self._rebuild()

                blocks.append(_Chips(list(meta.get("values", ())), cur,
                                     _pick_enum, pname.upper()))
            else:
                d = meta.get("default")
                label = pname.upper() + (
                    " · REQUIRED" if meta.get("required")
                    else f" · DEFAULT {_fmt(d)}" if d is not None else "")
                fi = self._input(f"param_{pname}", label)
                fi.label = label
                blocks.append(_WidgetBlock(fi))
        self._form_footer(blocks, self._save_behavior)

    # -- layout / draw / interaction ---------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        y = inner.y - self.scroll
        w = inner.w
        for b in self._blocks:
            h = b.measure(w)
            b.rect = Rect(inner.x, y, w, h)
            if b.widget is not None:
                if isinstance(b, _PairBlock):
                    b.arrange_pair(b.rect)
                else:
                    b.widget.arranged_rect = Rect(inner.x, y, w, h)
                    if isinstance(b.widget, Container):
                        b.widget.arrange(b.widget.arranged_rect)
                vis = (y + h > inner.y) and (y < inner.y + inner.h)
                b.widget.visible = vis
                if isinstance(b, _PairBlock):
                    b.second.visible = vis
            y += h
        self._content_h = (y + self.scroll) - inner.y

    def on_scroll(self, dx: float, dy: float) -> None:
        r = self.arranged_rect
        vh = r.h if r else 400.0
        max_scroll = max(0.0, self._content_h - vh + 12)
        new = max(0.0, min(max_scroll, self.scroll - dy * 26))
        if new != self.scroll:
            self.scroll = new
            if self.arranged_rect is not None:
                self.arrange(self.arranged_rect)
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        for b in self._blocks:
            if b.rect is not None and _contains(b.rect, x, y):
                if b.tap(x, y):
                    return

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, rect.h)
        for b in self._blocks:
            r = b.rect
            if r is None or r.y + r.h < rect.y or r.y > rect.y + rect.h:
                continue
            if b.widget is not None:
                if isinstance(b, _PairBlock):
                    b.draw_pair(canvas)
                elif b.widget.arranged_rect is not None:
                    b.widget.draw(canvas, b.widget.arranged_rect)
            else:
                b.draw(canvas, r)
        canvas.restore()


class _PairBlock(_Block):
    """Two FormInputs side by side (map key/value rows)."""

    def __init__(self, first: Widget, second: Widget) -> None:
        self.widget = first
        self.second = second

    def measure(self, width: float) -> float:
        return _INPUT_H

    def arrange_pair(self, rect: Rect) -> None:
        half = (rect.w - 8) / 2
        self.widget.arranged_rect = Rect(rect.x, rect.y, half, rect.h)
        self.second.arranged_rect = Rect(rect.x + half + 8, rect.y,
                                         half, rect.h)

    def draw_pair(self, canvas: Canvas) -> None:
        for w in (self.widget, self.second):
            if w.arranged_rect is not None:
                w.draw(canvas, w.arranged_rect)
