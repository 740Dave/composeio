"""
ComposeUX AgentsSkillsWidget — the Configuration tab's "what's LOADED with
this session" NESTED two-pane viewer ([CXC-IC-CONFIG-LOADED]).

LEFT: a one-line list of items (agents or skills) + an **(Open)** button that
reveals the SOURCE DIRECTORY in the OS file browser.
RIGHT: the selected item's detail, rendered through the READ-ONLY, scrollable
``markdown_viewer`` ([CXC-MDV]) as real markdown, in labelled sections:
  * **Description** — the front-matter `description` (from the bus payload).
  * **Tools** (agents only) — the agent's permitted tools, parsed from the
    file front-matter `tools:` field.
  * **Implementation** — the FULL markdown body of the item's file (front-matter
    stripped): `.claude/agents/<name>.md` or `.claude/skills/<name>/SKILL.md`.

MECHANISM — file-load-for-body (not bus-transmitted): the bus payload stays
small (`{name, description, path, loaded}`); the large .md BODIES are NOT put on
the bus (that would blow past the payload-size guideline).  The detail widget
LOADS the selected item's file directly (the Identity-role-def pattern) and
composes one markdown document for the viewer.

Namespaces (subscribed):
  * DATA (id-scoped): ``self/<id>/agents`` | ``self/<id>/skills`` — a JSON ARRAY
    of ``{name, description, path, loaded}`` from ``agents_skills_publisher``.
  * SELECTION: ``_app/config/agent`` | ``_app/config/skill`` — the per-item
    selection STATE (a list tap publishes the name; an Intelligence drives it).

Reveal seam (the (Open) button): publishes the source DIRECTORY on
``_app/fs/open`` — the additive reveal actuation on the existing
``deliverable_opener`` daemon (the one-Open-seam discipline, [DP-CIO]).  No
widget shells out; the opener runs the OS reveal.

Damage discipline ([CXC-R-DAMAGE]): bus updates + selection call ONLY
``self.request_redraw()``.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect
from ..engine.layout import Container, draw_child
from . import console_theme as ct
from . import style
from .deliverable_card import _coerce
from .markdown_viewer import MarkdownViewerWidget

# id-scoped data-namespace defaults; the scene points these at self/<id>/*.
DEFAULT_AGENTS_NS = "self/architect/agents"
DEFAULT_SKILLS_NS = "self/architect/skills"
# Local Memory ([DEC-LOCAL-MEMORY-CONFIG-SECTION]) — a THIRD mode on this same
# nested master/detail machinery.  NB the plural `memories`: `self/<id>/memory`
# is ALREADY owned by the automemory-footprint gauge ([DEC-MEMORY-BLOAT-MONITOR],
# a {pct,status,...} dict the intelligence_card ingests), so the LIST projection
# rides `self/<id>/memories` to avoid clobbering that gauge.
DEFAULT_MEMORY_NS = "self/architect/memories"
# per-item selection-namespace defaults (Intelligence-drivable seam).
DEFAULT_AGENT_SELECT = "_app/config/agent"
DEFAULT_SKILL_SELECT = "_app/config/skill"
DEFAULT_MEMORY_SELECT = "_app/config/memory"
# the (Open)-button reveal seam (deliverable_opener actuates it).
DEFAULT_OPEN_NS = "_app/fs/open"
DEFAULT_AGENT_DIR = ".claude/agents"
DEFAULT_SKILL_DIR = ".claude/skills"
# Memory files live in the instance's Claude project dir (~/.claude/projects/
# <slug>/memory), OUTSIDE the repo — so open_dir is INJECTED by the scene/parser
# (the publisher derives it per-instance); no repo-relative default exists.
DEFAULT_MEMORY_DIR = ""

NAV_W = 300.0          # the left list column width
NAV_GAP = 18.0
_ROW_H = 34.0          # one-line list entry
_OPEN_H = 30.0         # the (Open) button row height


class AgentsSkillsWidget(Container):
    """A nested master/detail loaded-set viewer (agents OR skills), hosting a
    read-only markdown_viewer for the detail pane."""

    def __init__(self, widget_id: str, *,
                 mode: str = "agents",
                 namespace: Optional[str] = None,
                 select_namespace: Optional[str] = None,
                 open_namespace: Optional[str] = None,
                 open_dir: Optional[str] = None,
                 title: Optional[str] = None,
                 nav_width: float = NAV_W,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "agents_skills", **kwargs)
        # the cards' ambient drop shadows paint past their rects ([CXC-R-DAMAGE]).
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        _m = str(mode).lower()
        if _m.startswith("skill"):
            self.mode = "skills"
        elif _m.startswith("mem"):
            self.mode = "memory"
        else:
            self.mode = "agents"
        _ns_default = {"skills": DEFAULT_SKILLS_NS, "memory": DEFAULT_MEMORY_NS}\
            .get(self.mode, DEFAULT_AGENTS_NS)
        _sel_default = {"skills": DEFAULT_SKILL_SELECT,
                        "memory": DEFAULT_MEMORY_SELECT}\
            .get(self.mode, DEFAULT_AGENT_SELECT)
        _dir_default = {"skills": DEFAULT_SKILL_DIR, "memory": DEFAULT_MEMORY_DIR}\
            .get(self.mode, DEFAULT_AGENT_DIR)
        self.namespace = str(namespace or _ns_default)
        self.select_namespace = str(select_namespace or _sel_default)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NS)
        self.open_dir = str(open_dir or _dir_default)
        self.title = str(title or self.mode)
        self.nav_width = float(nav_width or NAV_W)
        self.interactive = True
        # [{name, description, path, loaded}] in arrival order.
        self._items: List[Dict[str, Any]] = []
        self._selected: int = 0
        self._selected_name: Optional[str] = None   # stable across data updates
        # hit geometry recorded by draw().
        self._nav_bounds: List[Tuple[float, float]] = []
        self._open_btn: Optional[Tuple[float, float, float, float]] = None
        # the detail pane IS a read-only markdown viewer (a real child, so the
        # engine routes wheel-scroll to it natively) — [CXC-MDV].
        self._viewer = MarkdownViewerWidget(widget_id + "_md", font_size=13.0,
                                            pad=14.0)
        self.children = [self._viewer]
        self._detail_key: Optional[str] = None      # last-composed cache key

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        if namespace == self.select_namespace:
            self._select_by(_coerce(value))
            return
        if namespace != self.namespace:
            return
        items = self._coerce_items(_coerce(value))
        if items is None:
            return
        self._items = items
        self._reconcile_selection()          # keep selection stable by NAME
        self._refresh_detail()
        self.request_redraw()

    def _select_by(self, value: Any) -> None:
        if isinstance(value, bool):
            return
        idx: Optional[int] = None
        if isinstance(value, (int, float)):
            i = int(value)
            if 0 <= i < len(self._items):
                idx = i
        else:
            name = str(value or "").strip().lower()
            for i, it in enumerate(self._items):
                if str(it.get("name", "")).lower() == name:
                    idx = i
                    break
        if idx is not None and idx != self._selected:
            self._selected = idx
            self._selected_name = str(self._items[idx].get("name", ""))
            self._refresh_detail()
            self.request_redraw()

    def _reconcile_selection(self) -> None:
        if not self._items:
            self._selected = 0
            return
        if self._selected_name:
            for i, it in enumerate(self._items):
                if str(it.get("name", "")) == self._selected_name:
                    self._selected = i
                    return
        self._selected = max(0, min(self._selected, len(self._items) - 1))
        self._selected_name = str(self._items[self._selected].get("name", ""))

    @staticmethod
    def _coerce_items(value: Any) -> Optional[List[Dict[str, Any]]]:
        if isinstance(value, dict):
            for k in ("items", "agents", "skills", "memories"):
                if isinstance(value.get(k), list):
                    value = value[k]
                    break
        if not isinstance(value, list):
            return None
        out: List[Dict[str, Any]] = []
        for it in value:
            if isinstance(it, dict):
                # `hook` is the memory publisher's field for the one-line index
                # gloss ([DEC-LOCAL-MEMORY-CONFIG-SECTION]); it maps onto the same
                # `description` slot agents/skills use, so the detail/rows render
                # identically with no memory-specific branch here.
                desc = it.get("description")
                if desc in (None, ""):
                    desc = it.get("hook", "")
                out.append({
                    "name": str(it.get("name", it.get("id", "?"))),
                    "description": str(desc or ""),
                    "path": str(it.get("path", "") or ""),
                    "loaded": bool(it.get("loaded", True)),
                })
            elif it is not None:
                out.append({"name": str(it), "description": "", "path": "",
                            "loaded": True})
        return out

    # -- detail composition (file-load-for-body) -------------------------------

    def _refresh_detail(self) -> None:
        it = self._items[self._selected] \
            if 0 <= self._selected < len(self._items) else None
        if it is None:
            if self._detail_key != "":
                self._detail_key = ""
                self._viewer.set_content("_Select an item to view its detail._")
            return
        key = it.get("path") or it.get("name") or ""
        if key == self._detail_key:
            return                            # unchanged — avoid re-reading
        self._detail_key = key
        self._viewer.set_content(self._compose(it))

    def _conventional_path(self, name: str) -> str:
        if self.mode == "skills":
            return f"{DEFAULT_SKILL_DIR}/{name}/SKILL.md"
        if self.mode == "memory":
            return ""      # memory entries always carry an explicit path
        return f"{DEFAULT_AGENT_DIR}/{name}.md"

    @staticmethod
    def _under_claude(rel: str) -> bool:
        """Guard: only load files under `.claude/` (no arbitrary-path read from a
        stray bus publisher)."""
        rel = (rel or "").replace("\\", "/")
        return rel.startswith(".claude/") and ".." not in rel.split("/")

    def _path_allowed(self, rel_path: str) -> bool:
        """Body-load safety guard.  Agents/skills load repo-relative files under
        `.claude/`.  Memory files live OUTSIDE the repo (~/.claude/projects/<slug>/
        memory), so the equivalent trust bound is: the path must be one the trusted
        publisher ENUMERATED in the current payload, be a `.md`, and hold no `..`
        component — a stray bus publisher can't coax an arbitrary-path read."""
        if self.mode == "memory":
            p = (rel_path or "").replace("\\", "/")
            if not p or not p.endswith(".md") or ".." in p.split("/"):
                return False
            return rel_path in {str(it.get("path", "")) for it in self._items}
        return self._under_claude(rel_path)

    def _read_body_tools(self, rel_path: str) -> Tuple[str, List[str]]:
        """Return (body-minus-front-matter, tools-list) for a .md path, read
        READ-ONLY (agents/skills: relative to cwd; memory: an absolute path in the
        instance's Claude project dir). A missing or unsafe path yields an empty
        body + no tools — never raises."""
        if not self._path_allowed(rel_path):
            return ("", [])
        try:
            with open(rel_path, "r", encoding="utf-8") as fh:   # READ-ONLY
                text = fh.read()
        except OSError:
            return ("", [])
        m = re.match(r"^---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
        if m:
            fm_text, body = m.group(1), text[m.end():]
        else:
            fm_text, body = "", text
        return (body, self._parse_tools(fm_text))

    @staticmethod
    def _parse_tools(fm_text: str) -> List[str]:
        """Parse the front-matter `tools:` (or `allowed-tools:`) into a clean
        list. Tolerates a comma string ("Read, Grep") or a YAML list."""
        try:
            fm = yaml.safe_load(fm_text) if fm_text.strip() else None
        except yaml.YAMLError:
            fm = None
        raw = None
        if isinstance(fm, dict):
            raw = fm.get("tools", fm.get("allowed-tools"))
        if isinstance(raw, str):
            return [s.strip() for s in raw.split(",") if s.strip()]
        if isinstance(raw, list):
            return [str(s).strip() for s in raw if str(s).strip()]
        return []

    def _compose(self, item: Dict[str, Any]) -> str:
        """Compose the detail markdown: Description + Tools (agents) +
        Implementation (the file body)."""
        name = str(item.get("name", "?"))
        desc = str(item.get("description", "")).strip() or "_(no description)_"
        rel = item.get("path") or self._conventional_path(name)
        body, tools = self._read_body_tools(rel)
        parts = [f"# {name}", "## Description", desc]
        if self.mode == "agents":
            parts += ["## Tools",
                      ", ".join(tools) if tools else "_(none declared)_"]
        # the body section label tracks the mode: agents/skills expose an
        # "Implementation" (the definition file), a memory note is its "Note".
        body_label = "Note" if self.mode == "memory" else "Implementation"
        parts += [f"## {body_label}",
                  body.strip() or f"_(no {body_label.lower()} body found at "
                  f"`{rel}`)_"]
        return "\n\n".join(parts)

    # -- lifecycle / layout ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)             # mounts the viewer child too
        ct.theme_from_ctx(ctx)
        self._refresh_detail()            # seed the placeholder / first item

    def _nav_w(self, rect_w: float) -> float:
        return min(self.nav_width, rect_w * 0.45)

    def _detail_rect(self, r: Rect) -> Rect:
        nav_w = self._nav_w(r.w)
        return Rect(r.x + nav_w + NAV_GAP, r.y,
                    max(20.0, r.w - nav_w - NAV_GAP), r.h)

    def _arrange_children(self, inner: Rect) -> None:
        self._viewer.arranged_rect = self._detail_rect(inner)

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        nav_w = self._nav_w(rect.w)
        self._draw_list(canvas, rect.x, rect.y, nav_w, rect.h, t)
        # the detail pane: the read-only markdown viewer child (draws its own
        # card + scrollbar).  Keep its rect fresh even if arrange lagged.
        self._viewer.arranged_rect = self._detail_rect(rect)
        draw_child(canvas, self._viewer, self._viewer.arranged_rect)
        canvas.restore()

    def _draw_list(self, canvas: Canvas, x: float, y: float, w: float,
                   h: float, t) -> None:
        ct.draw_card(canvas, x, y, w, h, ct.RADIUS_CARD, t)
        n_loaded = sum(1 for it in self._items if it.get("loaded"))
        total = len(self._items)
        body_y = ct.draw_card_header(
            canvas, x, y, w, self.title, ns_tag="loaded",
            count_text=f"{n_loaded}/{total}" if total else "",
            count_color=t.ok if total and n_loaded == total else t.faint, t=t)

        pad = ct.PAD_CARD_BODY
        # the (Open) button row: reveals the source directory in the OS browser.
        self._open_btn = self._draw_open_button(canvas, x, body_y + 6.0, w, pad, t)

        self._nav_bounds = []
        canvas.save()
        canvas.clip_round_rect(x, y, w, h, ct.RADIUS_CARD)
        ry = body_y + 6.0 + _OPEN_H + 8.0
        for i, it in enumerate(self._items):
            if ry + _ROW_H > y + h - 4:
                break
            selected = (i == self._selected)
            row_y0, row_y1 = ry, ry + _ROW_H
            if selected:
                canvas.set_fill_color(*t.accent_dim)
                canvas.fill_round_rect(x + 5.0, row_y0 + 1.0, w - 10.0,
                                       _ROW_H - 2.0, 8.0)
                canvas.set_fill_color(*t.accent)
                canvas.fill_round_rect(x + 5.0, row_y0 + 6.0, 2.5,
                                       _ROW_H - 12.0, 1.25)
            cy = row_y0 + _ROW_H / 2.0
            loaded = bool(it.get("loaded"))
            ct.draw_status_dot(canvas, x + pad + 4.0, cy, 3.5,
                               t.ok if loaded else t.faint,
                               halo=t.ok_dim if loaded else None)
            canvas.set_font(style.FONT_MONO, 11.5,
                            "medium" if selected else "regular")
            base = style.baseline_for(canvas, cy)
            canvas.set_fill_color(*(t.accent if selected else t.dim))
            canvas.draw_text(ct.ellipsize(canvas, str(it.get("name", "?")),
                                          w - pad - 20.0),
                             x + pad + 16.0, base, "left")
            self._nav_bounds.append((row_y0, row_y1))
            ry = row_y1 + 2.0
        canvas.restore()

        if not self._items:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            canvas.draw_text(f"no {self.mode}", x + w / 2,
                             body_y + 6.0 + _OPEN_H + 30.0, "center")

    def _draw_open_button(self, canvas: Canvas, x: float, y: float, w: float,
                          pad: float, t) -> Tuple[float, float, float, float]:
        """A full-width '(Open) <dir>' pill; taps publish the dir on the reveal
        seam.  Returns its hit rect."""
        bx, by = x + pad, y
        bw, bh = w - 2 * pad, _OPEN_H
        canvas.set_fill_color(*t.accent_dim)
        canvas.fill_round_rect(bx, by, bw, bh, 8.0)
        canvas.set_stroke_color(*t.accent_line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(bx + 0.5, by + 0.5, bw - 1, bh - 1, 8.0)
        cy = by + bh / 2.0
        m = canvas.measure_text("Ag")
        base = cy + (m.ascent - m.descent) / 2.0
        canvas.set_font(ct.FONT_MONO, 11.0, "medium")
        canvas.set_fill_color(*t.accent)
        canvas.draw_text("(Open)", bx + 10.0, base, "left")
        ow = canvas.measure_text("(Open)").width
        canvas.set_font(ct.FONT_MONO, 10.5, "regular")
        canvas.set_fill_color(*t.dim)
        label = ct.ellipsize(canvas, f"{self.open_dir}/  ▸",
                             bw - ow - 26.0)
        canvas.draw_text(label, bx + 10.0 + ow + 8.0, base, "left")
        return (bx, by, bx + bw, by + bh)

    # -- interaction -----------------------------------------------------------

    def on_tap(self, x: float, y: float) -> None:
        # the (Open) button: reveal the source dir via the bus seam.
        b = self._open_btn
        if b and b[0] <= x <= b[2] and b[1] <= y <= b[3]:
            self.publish(self.open_namespace, self.open_dir)
            return
        # a list row: select + publish selection (the declared seam).
        for i, (y0, y1) in enumerate(self._nav_bounds):
            if y0 <= y <= y1 and i < len(self._items):
                self._selected = i
                self._selected_name = str(self._items[i].get("name", ""))
                self._refresh_detail()
                self.publish(self.select_namespace,
                             self._items[i].get("name", ""))
                self.request_redraw()
                return
