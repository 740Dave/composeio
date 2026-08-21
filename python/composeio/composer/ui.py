"""ComposerUI — assembles the Composer chrome into an engine widget tree.

Realizes the three-zone layout of composer-workshop-previs.v1.html:

    ┌────────────────────────────────────────────────┐
    │ topbar: Composer · <doc name> · save/export    │
    ├───────────────────────────┬────────────────────┤
    │ Widget Workshop (2/3)     │ WC tabs (1/3)      │
    │ rulers + pixel-stack +    │ Overview/Layers/   │
    │ selection handles         │ Widgets            │
    ├───────────────────────────┴────────────────────┤
    │ telemetry: Selected · Bus · MQTT · UART        │
    └────────────────────────────────────────────────┘

The 2/3 + 1/3 split dogfoods the M4 flex-grow addition; the Bus-Monitor
pop-out dogfoods the M4 DialogWidget (in-window modal on the internal
bus; a real separate window via subprocess when an MQTT broker bridges
the buses).

All state flows through the bus: the UI publishes commands and reacts to
"executed" notifications — it never mutates the document ([DP-CXC-1]).
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from typing import Any, Optional

from ..render.engine.layout import (Container, FlexContainer,
                                    PixelStackContainer)
from ..render.engine.parser import Composition
from ..render.widgets import BusMonitorWidget, DialogWidget

from ..render.canvas import Size
from ..render.widget import Constraints, Rect

from . import chrome
from .bindpanel import BindingsPanel
from .chrome import (ActionButton, BusTabWidget, CatalogPanel, ChromeLabel,
                     LayersPanel, LiveInput, OverviewPanel, StatusBarWidget,
                     WorkshopWidget)
from .core import CANVAS_NAMESPACES, ComposerCore
from .flowpanel import FlowInspectorPanel, FlowPane
from .source_panel import SourcePanel

logger = logging.getLogger("composeio.composer.ui")

WIN_W, WIN_H = 1440, 900


class _LensHost(Container):
    """Hosts the WW's two lenses ([CXC-FL-C3]): Layout (WorkshopWidget)
    and Flow (FlowPane) occupy the same slot; visibility is bus-driven.
    Both children always get the full rect — Container.draw/hit_test skip
    the invisible one."""

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _arrange_children(self, inner: Rect) -> None:
        for ch in self.children:
            ch.measure(Constraints(inner.w, inner.h))
            if isinstance(ch, Container):
                ch.arrange(Rect(inner.x, inner.y, inner.w, inner.h))
            else:
                ch.arranged_rect = Rect(inner.x, inner.y, inner.w, inner.h)


class ComposerUI:
    """Builds the chrome tree and keeps it in sync with the bus."""

    def __init__(self, core: ComposerCore) -> None:
        self.core = core
        self.render: Any = None                     # set by app after RenderApp init

        doc = core.document

        # -- workshop (WW) + Flow lens ([SPEC:CXC-FLOW]) ---------------------
        self.workshop = WorkshopWidget("workshop", core)
        self.flow = FlowPane("flow", core)
        self.flow.visible = False
        self.lens_host = _LensHost("ww_lenses", "lens_host")
        self.lens_host.add(self.workshop)
        self.lens_host.add(self.flow)
        self.lens_host.layout.grow = 2.0

        # -- WC tabs ----------------------------------------------------------
        self.overview = OverviewPanel("wc_overview", core)
        self.layers = LayersPanel("wc_layers", core)
        self.catalog = CatalogPanel("wc_catalog", core)
        self.bindings = BindingsPanel("wc_bindings", core)
        self.source = SourcePanel("wc_source", core,
                                  on_popout=self._open_source_popout)
        self.flow_inspector = FlowInspectorPanel("wc_flow", core)
        self.catalog_search = LiveInput(
            "catalog_search", label="", placeholder="search the catalog…",
            on_change=self.catalog.set_filter)
        self.catalog_search.layout.height = 44
        self.tabs = BusTabWidget(
            "wc_tabs", core,
            labels=["Overview", "Layers", "Widgets", "Bindings", "Source",
                    "Flow"])
        for panel in (self.overview, self.layers, self.catalog,
                      self.bindings, self.source, self.flow_inspector):
            page = FlexContainer(f"page_{panel.widget_id}",
                                 direction="column", align="stretch")
            page.padding = (6, 4, 6, 4)
            panel.layout.grow = 1.0
            if panel is self.catalog:
                page.add(self.catalog_search)      # [CXC-BE-POLISH] search
            page.add(panel)
            self.tabs.add(page)
        for i, ch in enumerate(self.tabs.children):
            ch.visible = (i == 0)

        wc = FlexContainer("wc", direction="column", align="stretch")
        wc.background = chrome.PANEL
        wc.layout.grow = 1.0
        self.tabs.layout.grow = 1.0
        wc.add(self.tabs)

        # -- topbar ----------------------------------------------------------
        self.brand = ChromeLabel("brand", text="Composer", font_size=13,
                                 weight="medium", color=chrome.ACCENT)
        self.doc_label = ChromeLabel("doc_name", text=doc.name,
                                     font_size=13, weight="medium",
                                     color=chrome.TEXT)
        self.chip = ChromeLabel("chip", text="pixel-stack", font_size=10,
                                color=chrome.DIM, mono=True)
        spacer = ChromeLabel("tb_spacer", text="")
        spacer.layout.grow = 1.0
        # Layout <-> Flow lens toggle ([CXC-FL-C3]) — mode is BUS STATE, so
        # an Intelligence flips the lens exactly like this button.
        self.btn_layout = ActionButton(
            "btn_mode_layout", label="Layout", accent=True,
            on_press=lambda: core.internal_bus.publish_state(
                "composer/ww/mode", "layout", source="ui"))
        self.btn_flow = ActionButton(
            "btn_mode_flow", label="Flow",
            on_press=lambda: core.internal_bus.publish_state(
                "composer/ww/mode", "flow", source="ui"))
        self.btn_undo = ActionButton(
            "btn_undo", label="↶ Undo",
            on_press=lambda: core.internal_bus.publish_event(
                "composer/history/undo", "do", data={}, source="ui"))
        self.btn_redo = ActionButton(
            "btn_redo", label="↷ Redo",
            on_press=lambda: core.internal_bus.publish_event(
                "composer/history/redo", "do", data={}, source="ui"))
        self.btn_save = ActionButton("btn_save", label="Save",
                                     on_press=self._save)
        self.btn_export = ActionButton("btn_export", label="Export",
                                       on_press=self._export, accent=True)
        topbar = FlexContainer("topbar", direction="row", align="center")
        topbar.background = chrome.PANEL
        topbar.border_color = chrome.BORDER
        topbar.padding = (7, 12, 7, 12)
        topbar.gap = 10
        topbar.layout.height = 40
        for wgt in (self.brand, self.doc_label, self.chip, spacer,
                    self.btn_layout, self.btn_flow,
                    self.btn_undo, self.btn_redo,
                    self.btn_save, self.btn_export):
            topbar.add(wgt)

        # -- status / telemetry bar ---------------------------------------------
        self.status = StatusBarWidget("status_bar", core,
                                      on_bus_click=self._open_bus_monitor)
        self.status.layout.height = 34

        # -- work row (2/3 · 1/3 via flex grow) ------------------------------
        work = FlexContainer("work", direction="row", align="stretch")
        work.layout.grow = 1.0
        work.add(self.lens_host)
        work.add(wc)

        main = FlexContainer("main", direction="column", align="stretch")
        main.background = chrome.BG
        main.add(topbar)
        main.add(work)
        main.add(self.status)

        # -- root: pixel stack so the Bus-Monitor dialog overlays z-top -------
        root = PixelStackContainer("root", canvas_width=WIN_W,
                                   canvas_height=WIN_H)
        main.stack_x, main.stack_y = 0, 0
        main.stack_w, main.stack_h = "100%", "100%"
        root.add(main)

        self.bus_dialog = DialogWidget(
            "busmon_dialog", title="Bus Monitor — composer internal bus",
            panel_width=640, panel_height=420, dismissible=True)
        self.bus_monitor = BusMonitorWidget("busmon", label="Bus Monitor",
                                            max_lines=200)
        self.bus_monitor.layout.height = 340
        self.bus_dialog.add(self.bus_monitor)
        self.bus_dialog.stack_x = 0
        self.bus_dialog.stack_y = 0
        self.bus_dialog.stack_w = "100%"
        self.bus_dialog.stack_h = "100%"
        root.add(self.bus_dialog)

        # Source pop-out ([CXC-BE-C6]): full-width own window over the
        # Composer — the same DialogWidget mechanism the Bus Monitor uses.
        # A SECOND SourcePanel over the same document: both projections
        # stay in lockstep through the document bus.
        self.source_dialog = DialogWidget(
            "source_dialog", title="Source — full width",
            panel_width=WIN_W - 60, panel_height=WIN_H - 80,
            dismissible=True)
        self.source_big = SourcePanel("wc_source_big", core)
        self.source_big.layout.height = WIN_H - 80 - 36 - 28
        self.source_dialog.add(self.source_big)
        self.source_dialog.stack_x = 0
        self.source_dialog.stack_y = 0
        self.source_dialog.stack_w = "100%"
        self.source_dialog.stack_h = "100%"
        root.add(self.source_dialog)

        # Headless Monitor pop-out ([SPEC:CXC-HMON] [CXC-HMON-C9] host a):
        # subprocess own-window when the MQTT bridge is up (the Bus-Monitor
        # convention); this engine Dialog hosts the SAME MonitorPane
        # surface as the in-window fallback.  The pane observes the DESIGN
        # bus (where a bridged runtime's `_bus/headless/*` traffic lands).
        from .monitor import MonitorPane
        self.monitor_dialog = DialogWidget(
            "monitor_dialog", title="Headless Monitor — Diagnosis",
            panel_width=920, panel_height=640, dismissible=True)
        self.monitor_pane = MonitorPane("monitor_pane",
                                        bus=core.design_bus)
        self.monitor_pane.layout.height = 560
        self.monitor_dialog.add(self.monitor_pane)
        self.monitor_dialog.stack_x = 0
        self.monitor_dialog.stack_y = 0
        self.monitor_dialog.stack_w = "100%"
        self.monitor_dialog.stack_h = "100%"
        root.add(self.monitor_dialog)

        self.root = root

    # -- composition for RenderApp ------------------------------------------

    def composition(self) -> Composition:
        comp = Composition(root=self.root, title="ComposeIO Composer",
                           width=WIN_W, height=WIN_H,
                           background=chrome.BG,
                           app_id="composeux-composer")
        return comp

    # -- bus wiring (called once the RenderApp exists) ---------------------

    def wire(self, render: Any) -> None:
        self.render = render
        ib = self.core.internal_bus

        # document changed -> rebuild views (render-thread hop)
        self.core.on_change(self._on_doc_changed)

        # selection state -> workshop + bindings + source + status repaint
        def _sel(ns, new_val, old_val):
            def apply():
                wid = str(new_val or "")
                self.workshop.selected_id = wid
                self.bindings.on_selection_changed()
                self.source.on_canvas_selection(wid)
                self.source_big.on_canvas_selection(wid)
                render.mark_dirty()
            render.schedule_update(apply)
        ib.subscribe("composer/selection/widget_id", _sel)

        # Source projections start from the loaded document
        self.source.refresh_from_doc()
        self.source_big.refresh_from_doc()

        # WC tab as bus state (Intelligence tab-switch)
        def _tab(ns, new_val, old_val):
            render.schedule_update(
                lambda: self.tabs.set_active_key(str(new_val or "")))
        ib.subscribe("composer/wc/active_tab", _tab)

        # Layout <-> Flow lens as bus state ([CXC-FL-C3]/[CXC-FL-C9])
        def _mode(ns, new_val, old_val):
            render.schedule_update(
                lambda: self.set_mode(str(new_val or "layout")))
        ib.subscribe("composer/ww/mode", _mode)

        # inspector repaint + logic-card reseed when the selection changes
        def _flow_sel(ns, new_val, old_val):
            def apply():
                self.flow_inspector.refresh()
                render.mark_dirty()
            render.schedule_update(apply)
        ib.subscribe("composer/flow/selected", _flow_sel)

        # file loads replace the document wholesale
        ib.subscribe_events("composer/file/loaded",
                            lambda ns, et, d: self._on_doc_changed(ns, d))

        # [SPEC:CXC-HMON] "Inspect ▸" -> the Headless Monitor host.  A bus
        # command, so an Intelligence opens it exactly like the button.
        ib.subscribe_events("composer/monitor/open",
                            lambda ns, et, d: self._open_monitor(d or {}))

    def set_mode(self, mode: str) -> None:
        """Swap the WW lens ([CXC-FL-C3]): same design, two projections."""
        flow = (mode == "flow")
        self.workshop.visible = not flow
        self.flow.visible = flow
        self.btn_layout.accent = not flow
        self.btn_flow.accent = flow
        if flow:
            self.flow.refresh()
            # the WC follows the lens: the Flow tab is the inspector
            self.core.internal_bus.publish_state(
                "composer/wc/active_tab", "flow", source="ui")
        elif self.core.internal_bus.get_state(
                "composer/wc/active_tab") == "flow":
            self.core.internal_bus.publish_state(
                "composer/wc/active_tab", "overview", source="ui")
        if self.render is not None:
            self.render.mark_dirty()

    def _on_doc_changed(self, namespace: str, data: dict) -> None:
        if self.render is None:
            return

        def apply() -> None:
            self.workshop.rebuild()
            self.overview.refresh()
            self.bindings.refresh()
            self.flow.refresh()
            self.flow_inspector.refresh()
            self.source.refresh_from_doc()
            self.source_big.refresh_from_doc()
            self.doc_label.text = self.core.document.name
            # a freshly added widget becomes the selection
            if namespace.endswith("widget_added") and data.get("widget_id"):
                self.workshop.select(str(data["widget_id"]))
            if namespace.endswith("widget_removed"):
                wid = str(data.get("widget_id", ""))
                if wid and self.workshop.selected_id == wid:
                    self.workshop.select("")
            self.render.mark_dirty()

        self.render.schedule_update(apply)

    # -- actions ----------------------------------------------------------

    def _default_path(self) -> str:
        return self.core.file_path or os.path.join(
            os.getcwd(), self.core.document.slug + ".compose.yaml")

    def _save(self) -> None:
        self.core.internal_bus.publish_event(
            "composer/file/save", "do", data={"path": self._default_path()},
            source="ui")

    def _export(self) -> None:
        self.core.internal_bus.publish_event(
            "composer/file/export", "do", data={"path": self._default_path()},
            source="ui")

    def _open_source_popout(self) -> None:
        """[CXC-BE-C6]: Source full-width, reusing the Bus-Monitor pop-out
        mechanism (the engine DialogWidget overlay)."""
        self.source_big.refresh_from_doc()
        wid = self.core.internal_bus.get_state("composer/selection/widget_id")
        if wid:
            self.source_big.on_canvas_selection(str(wid))
        self.source_dialog.open()
        self.core.internal_bus.publish_state(
            "composer/window/open", {"window": "source"}, source="ui")
        if self.render is not None:
            self.render.mark_dirty()

    def _open_monitor(self, data: dict) -> None:
        """[SPEC:CXC-HMON] [CXC-HMON-C9] host (a), the Flow pop-out:
        launch the Headless Monitor scoped to ONE widget.  Its own OS
        window (subprocess, the Bus-Monitor pop-out convention) when the
        MQTT bridge is up — the Monitor then hears the fleet's
        `_bus/headless/*` directly; otherwise the engine Dialog overlay
        hosting the same MonitorPane on the design bus."""
        wid = str(data.get("widget_id", ""))
        rid = str(data.get("runtime_id", "") or "")
        broker = os.environ.get("COMPOSEUX_MQTT_BROKER")
        if broker and getattr(self.core, "mqtt_connected", False):
            try:
                args = [sys.executable, "-m", "composeio.composer.monitor",
                        "--mqtt", broker]
                if wid:
                    args += ["--widget", wid]
                if rid:
                    args += ["--runtime", rid]
                subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except Exception:
                logger.exception("monitor pop-out failed; dialog fallback")
        pane = self.monitor_pane
        pane.scope_widget = wid or None
        pane.runtime_id = rid or None
        if wid:
            pane.model.widget_runtime.setdefault(wid, rid or "?")
            pane.select(wid)
        self.monitor_dialog.open()
        if self.render is not None:
            self.render.mark_dirty()

    def _open_bus_monitor(self) -> None:
        """Bus telemetry click: its own OS window when the MQTT bridge is
        up (a standalone engine app on the same broker); otherwise the
        engine Dialog overlay on the internal bus."""
        broker = os.environ.get("COMPOSEUX_MQTT_BROKER")
        if broker and getattr(self.core, "mqtt_connected", False):
            try:
                self._spawn_bus_monitor_window(broker)
                return
            except Exception:
                logger.exception("bus monitor pop-out failed; dialog fallback")
        self.bus_dialog.open()
        if self.render is not None:
            self.render.mark_dirty()

    def _spawn_bus_monitor_window(self, broker: str) -> None:
        cfg = f"""# generated by Composer — Bus Monitor pop-out
app:
  id: composer-bus-monitor
  transports:
    - {{type: mqtt, broker: {broker}}}
design:
  title: "Bus Monitor — {broker}"
  width: 720
  height: 460
widgets:
  - type: stack
    direction: column
    align: stretch
    children:
      - type: bus_monitor
        id: busmon
        label: "Bus Monitor"
        height: 430
"""
        path = os.path.join(tempfile.gettempdir(),
                            "composer_bus_monitor.compose.yaml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(cfg)
        subprocess.Popen(
            [sys.executable, "-m", "composeio.render.run_compose", path,
             "--transports"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
