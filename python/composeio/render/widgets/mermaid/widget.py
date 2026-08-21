"""
ComposeUX Mermaid Widget ([SPEC:CXC-IC] [CXC-IC-CANVAS]).

The composable canvas at its purest: an Intelligence emits mermaid-diagram
TEXT on a bus namespace (default ``thread/diagram``) and a subscribed widget
renders it — state on the bus, behavior in the widget, no round-trip through
the model to redraw.

Two render tiers, ONE widget (the media/svg graceful-degradation idiom)
---------------------------------------------------------------------------
mermaid.js is a browser diagram engine, so the LIVE rasterization runs on the
WEB surface (the Intelligence Console) — this widget vendors a pinned
``mermaid.min.js`` (see ``assets/``) so that surface is self-contained/offline,
never CDN-loaded.  The web tier calls :func:`web_asset_path` for the bundle and
:meth:`render_html` for a self-mounting fragment that subscribes the namespace
and re-renders on every change.

The Python Canvas tier CANNOT run mermaid.js (no JS engine on the
CYD/MinnowBoard/desktop Canvas backends), so it degrades to a SOURCE panel: it
shows the mermaid text currently on the bus — the very bytes the Console's
mermaid.js would render.  This proves the bus wiring (the value arrived and
re-rendered) on every backend and never breaks the screen.  A device with a
browser view renders the diagram; a device without one shows its source.

The state on the bus is the mermaid SOURCE (not an SVG URI like the SvgWidget,
which consumes an already-rendered diagram).  Point the SvgWidget at a
``doc://diagrams/{id}.svg`` produced by a server-side mermaid->SVG service; use
THIS widget when the raw diagram text itself is what an Intelligence emits.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from ...canvas import Canvas, Size
from ...widget import Constraints, Rect, Widget

_DIM = (0x66, 0x66, 0x78)
_INK = (0xD0, 0xD4, 0xE0)
_TEAL = (0x26, 0xA6, 0x9A)

# The default namespace an Intelligence emits mermaid text on ([CXC-IC-CANVAS];
# the architect-console prototype's `thread/diagram · mermaid` panel).
DEFAULT_NAMESPACE = "thread/diagram"

# Vendored, PINNED mermaid.js (self-contained UMD; sets window.mermaid).  NO
# CDN — the web surface must render offline (the media-widget self-containment
# rule).  Bump both when re-vendoring.
MERMAID_VERSION = "11.16.0"
_ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_MERMAID_JS = os.path.join(_ASSET_DIR, "mermaid.min.js")


def web_asset_path() -> str:
    """Absolute path to the vendored ``mermaid.min.js`` (pinned
    :data:`MERMAID_VERSION`).  The web surface serves/inlines THIS file; it is
    never fetched from a CDN."""
    return _MERMAID_JS


class MermaidWidget(Widget):
    """Live mermaid-diagram surface driven by a bus namespace.

    ``source`` is the mermaid text; a bound namespace (default
    ``thread/diagram``) swaps it reactively ([SPEC:CXC-BIND], the SvgWidget
    ``on_bus_state`` idiom).  On the Canvas tier the source is shown as text;
    on the web tier it is rendered by the vendored mermaid.js.
    """

    def __init__(self, widget_id: str, *,
                 source: str = "",
                 namespace: str = DEFAULT_NAMESPACE,
                 theme: str = "dark",
                 width: float = 440,
                 height: float = 320,
                 chrome: str = "",
                 **kwargs: Any) -> None:
        # state exists BEFORE super().__init__ so base-class kwargs that hit
        # property setters find their backing fields (the Lottie/SVG idiom).
        self._source = ""
        # the bus namespace this widget subscribes ([CXC-IC-CANVAS]); the
        # parser also appends it to comp.subscriptions so on_bus_state fires.
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.theme = str(theme or "dark")
        # [CXC-ICV-CANVAS] option B (architect-ratified): chrome="console"
        # renders the Canvas-tier source panel wearing the pre-vis panel
        # chrome (collapsible-style summary row, ns-tag, panel-3 fill, mono
        # source, srcline footer).  Default "" keeps the original panel.
        # Option A (headless mermaid -> SVG -> SvgWidget via a Node-side
        # service) is the LATER fidelity ceiling — deliberately not built
        # in this pass; recorded here per the ratification.
        self.chrome = str(chrome or "")

        super().__init__(widget_id, "mermaid", **kwargs)
        self.diagram_width = float(width)
        self.diagram_height = float(height)
        self._source = str(source or "")

    # ------------------------------------------------------------------
    # Bindable source ([SPEC:CXC-BIND]) — the state on the bus
    # ------------------------------------------------------------------

    @property
    def source(self) -> str:
        """The mermaid diagram text currently on the bus."""
        return self._source

    @source.setter
    def source(self, value: Any) -> None:
        value = "" if value is None else str(value)
        if value == self._source:
            return
        self._source = value
        self.request_redraw()

    def on_bus_state(self, namespace: str, value: Any) -> None:
        """A new value on the bound namespace re-renders the diagram.

        This is the whole thesis: the model emits mermaid text once, and every
        subsequent redraw is pure widget behavior — no model round-trip.
        """
        self.source = value

    def set_source(self, source: str) -> None:
        """Directly install diagram text (tests / embedders)."""
        self.source = source

    # ------------------------------------------------------------------
    # Web tier — self-mounting fragment that runs the vendored mermaid.js
    # ------------------------------------------------------------------

    def render_html(self, *, element_id: Optional[str] = None) -> str:
        """A self-contained HTML fragment for the web surface.

        It renders :attr:`source` via ``window.mermaid`` (the vendored bundle
        must already be on the page — see :func:`web_asset_path`) and exposes
        ``window.__composeMermaid[element_id](text)`` so the Console's bus
        subscription re-renders it on every ``namespace`` change WITHOUT a
        model round-trip.  This is the browser twin of :meth:`on_bus_state`.
        """
        eid = element_id or f"mermaid-{self.widget_id}"
        return _MERMAID_FRAGMENT.format(
            eid=eid, theme=self.theme, ns=self.namespace,
            source=_js_string(self._source))

    # ------------------------------------------------------------------
    # Canvas tier — source panel (graceful degradation; never runs JS)
    # ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(min(self.diagram_width, constraints.max_width),
                    min(self.diagram_height, constraints.max_height))

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        if self.chrome == "console":
            self._draw_console_panel(canvas, rect)
            return
        canvas.save()
        canvas.set_fill_color(0x14, 0x14, 0x1E)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)
        canvas.set_stroke_color(*_DIM)
        canvas.set_stroke_width(1)
        canvas.stroke_rect(rect.x, rect.y, rect.w, rect.h)

        # header: this is a mermaid surface bound to <namespace>
        canvas.set_fill_color(*_TEAL)
        canvas.set_font("sans-serif", 11, "bold")
        canvas.draw_text("mermaid", rect.x + 8, rect.y + 16, "left")
        canvas.set_fill_color(*_DIM)
        canvas.set_font("sans-serif", 10, "regular")
        canvas.draw_text(self.namespace, rect.x + rect.w - 8, rect.y + 16,
                         "right")

        if self._source.strip():
            # show the bytes on the bus — the very text the web tier renders.
            canvas.set_fill_color(*_INK)
            canvas.set_font("monospace", 10, "regular")
            line_h = 13.0
            y = rect.y + 34
            max_lines = max(1, int((rect.y + rect.h - 8 - y) / line_h))
            lines = self._source.splitlines() or [self._source]
            for line in lines[:max_lines]:
                canvas.draw_text(line, rect.x + 8, y, "left")
                y += line_h
            if len(lines) > max_lines:
                canvas.set_fill_color(*_DIM)
                canvas.draw_text("…", rect.x + 8, y, "left")
        else:
            canvas.set_fill_color(*_DIM)
            canvas.set_font("sans-serif", 10, "regular")
            canvas.draw_text("awaiting diagram on " + self.namespace,
                             rect.x + rect.w / 2, rect.y + rect.h / 2,
                             "center")
        canvas.restore()


    # ------------------------------------------------------------------
    # [CXC-ICV-CANVAS] option B — the console-styled source-degrade panel
    # ------------------------------------------------------------------

    def _draw_console_panel(self, canvas: Canvas, rect: Rect) -> None:
        """The pre-vis ``details.canvaspanel`` chrome on the Canvas tier:
        summary row ("CANVAS · LIVE FROM THE BUS" + ns-tag + chevron, drawn
        OPEN for the golden render), panel-3 body with the mono source (the
        bytes on the bus), and the ``srcline`` footer over a dashed rule."""
        from .. import console_theme as ct
        from .. import style
        t = ct.CONSOLE.active
        canvas.save()
        # panel-3 plate + line border (the canvaspanel strip)
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(rect.x, rect.y, rect.w, rect.h, 10)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(rect.x + 0.5, rect.y + 0.5,
                                 rect.w - 1, rect.h - 1, 10)

        # summary row
        pad = 14.0
        canvas.set_font(style.FONT_MONO, ct.LABEL_MONO, "regular")
        m = canvas.measure_text("Ag")
        base = rect.y + 9 + m.ascent
        ct.draw_tracked(canvas, "CANVAS · LIVE FROM THE BUS",
                        rect.x + pad, base, ct.TRACK_LABEL, t.dim)
        canvas.set_font(style.FONT_MONO, ct.NS_TAG, "regular")
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2],
                              ct.NS_TAG_ALPHA)
        canvas.draw_text(f"{self.namespace} · mermaid",
                         rect.x + rect.w - pad - 12, base, "right")
        # open-state chevron ("▾" is notdef in Plex -> a small vector V)
        chx = rect.x + rect.w - pad
        chy = base - m.ascent * 0.35
        canvas.set_stroke_color(*t.faint)
        canvas.set_stroke_width(1.4)
        canvas.draw_line(chx - 4, chy - 2, chx, chy + 2)
        canvas.draw_line(chx, chy + 2, chx + 4, chy - 2)
        hdr_h = 9 * 2 + m.ascent + m.descent
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.draw_line(rect.x, rect.y + hdr_h, rect.x + rect.w,
                         rect.y + hdr_h)

        # srcline footer (measured first so the body knows its space)
        foot_h = 22.0
        fy = rect.y + rect.h - foot_h
        canvas.set_stroke_color(*t.line)
        ct.draw_dashed_line(canvas, rect.x + pad, fy,
                            rect.x + rect.w - pad)
        canvas.set_font(style.FONT_MONO, 8.6, "regular")
        fm = canvas.measure_text("Ag")
        fbase = fy + 6 + fm.ascent
        canvas.set_fill_color(*t.faint)
        prefix = "rendered from "
        canvas.draw_text(prefix, rect.x + pad, fbase, "left")
        fx = rect.x + pad + canvas.measure_text(prefix).width
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(self.namespace, fx, fbase, "left")
        fx += canvas.measure_text(self.namespace).width
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(" — source panel; degrades to source off-browser",
                         fx, fbase, "left")

        # body: the mono source (the very bytes the web tier renders)
        canvas.save()
        canvas.clip_rect(rect.x + pad, rect.y + hdr_h + 6,
                         rect.w - 2 * pad, fy - rect.y - hdr_h - 10)
        if self._source.strip():
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            line_h = 13.0
            y = rect.y + hdr_h + 8
            max_lines = max(1, int((fy - 6 - y) / line_h))
            lines = self._source.splitlines() or [self._source]
            canvas.set_fill_color(*t.dim)
            for line in lines[:max_lines]:
                lm = canvas.measure_text(line)
                canvas.draw_text(line, rect.x + pad, y + lm.ascent, "left")
                y += line_h
            if len(lines) > max_lines:
                canvas.set_fill_color(*t.faint)
                canvas.draw_text("…", rect.x + pad, y + lm.ascent, "left")
        else:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(style.FONT_MONO, 9.5, "regular")
            canvas.draw_text("awaiting diagram on " + self.namespace,
                             rect.x + rect.w / 2,
                             (rect.y + hdr_h + fy) / 2, "center")
        canvas.restore()
        canvas.restore()


def _js_string(text: str) -> str:
    """Encode a Python string as a safe JS single-quoted literal."""
    out = (text.replace("\\", "\\\\").replace("'", "\\'")
               .replace("\n", "\\n").replace("\r", "")
               .replace("</", "<\\/"))
    return "'" + out + "'"


# A self-mounting fragment: inject it into a page that already loaded the
# vendored mermaid.min.js.  It renders the initial source and registers a
# re-render hook the bus subscription drives.  Idempotent per element id.
_MERMAID_FRAGMENT = """\
<div id="{eid}" class="compose-mermaid"></div>
<script>
(function () {{
  var host = document.getElementById('{eid}');
  if (!host || !window.mermaid) return;
  if (!window.__composeMermaidInit) {{
    window.mermaid.initialize({{ startOnLoad: false, theme: '{theme}',
                                 securityLevel: 'strict' }});
    window.__composeMermaidInit = true;
  }}
  window.__composeMermaid = window.__composeMermaid || {{}};
  var seq = 0;
  function render(text) {{
    seq += 1;
    var id = '{eid}-svg-' + seq;
    window.mermaid.render(id, text).then(function (r) {{
      host.innerHTML = r.svg;
    }}).catch(function (e) {{
      host.innerHTML = '<pre class="mermaid-error">' +
        String(e && e.message || e) + '</pre>';
    }});
  }}
  // namespace this widget subscribes: {ns}
  window.__composeMermaid['{eid}'] = render;
  render({source});
}})();
</script>
"""
