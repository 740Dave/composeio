"""ComposeUX widget implementations on the Canvas contract.

Phase 1 primitives (gauge/led/label/rect/circle) + the M2 production
widget parity set ([CXC-RM-M2]): Button, Relay, Command, TextInput,
Checkbox, PowerToggle, DocPicker, BusMonitor, Tab, Coordinate, Image,
Callout.  [SPEC:CXC-LOTTIE] adds LottieWidget (vector animation);
[SPEC:CXC-FLAUTH] adds LogicWidget (headless logic node, no render).
"""

from .gauge import GaugeWidget
from .led import LEDWidget
from .label import LabelWidget
from .rectangle import RectangleWidget
from .circle import CircleWidget
from .placeholder import PlaceholderWidget
from .image import ImageWidget
from .carousel import CarouselWidget
# Generic selector widgets promoted OUT of the BSG project into engine core
# (project-neutral names; BSG tokens kept as descriptor aliases in parser.py).
# The BSG-specific ``colonial_console`` shell stays project-local and imports
# SegmentedTabRowWidget from here.
from .radio_button_group import RadioButtonGroupWidget
from .segmented_tab_row import (FILLED_BACKGROUND, PILL_OVERLAY,
                                SegmentedTabRowWidget)
from .seven_segment import SevenSegmentDisplay

from .button import ButtonWidget
from .emit_button import EmitButtonWidget
from .relay import RelayWidget
from .command import CommandWidget
from .text_input import TextInputWidget
from .checkbox import CheckboxWidget
from .power_toggle import PowerToggleWidget
from .doc_picker import DocPickerWidget
from .store_browser import StoreBrowserWidget
from .bus_monitor import BusMonitorWidget
from .chat_thread import ChatThreadWidget
from .activity import ActivityWidget
from .subagent_ledger import SubagentLedgerWidget
from .registered_checklist import RegisteredChecklistWidget
from .nightwatch_checklist import NightWatchChecklistWidget
from .nightwatch_artifacts import NightWatchArtifactsWidget
from .decisions_graph import DecisionsGraphWidget
from .decisions_tab import DecisionsTabWidget, GraphViewerWidget
from .decisions_summary import DecisionsSummaryWidget
from .defects_summary import DefectsSummaryWidget
from .lts_engine_rail import LtsEngineRailWidget
from .self_ledger import SelfLedgerWidget
from .deliverable_card import DeliverableCardWidget
from .intelligence_card import IntelligenceCardWidget
from .account_card import AccountCardWidget
from .model_picker import ModelPickerWidget
from .deliverable_alert import DeliverableAlertWidget
from .deliverable_history import DeliverableHistoryWidget
from .bootstrap_manifest import BootstrapManifestWidget
from .console_tabs import ConsoleTabWidget
from .avatar import AvatarWidget
from .theme_toggle import ThemeToggleWidget
from .tab import TabWidget
from .coordinate import CoordinateWidget
from .callout import CalloutWidget
from .dialog import DialogWidget
from .lottie import LottieWidget
from .svg import SvgWidget
from .launch_tile import LaunchTileWidget
from .media import MediaWidget
from .mermaid import MermaidWidget
from .logic import LogicWidget
# [SPEC:CXC-TERM] the PTY-backed terminal.  Imported lazily-safe: the module
# guards its own `pyte` import, so a missing emulator degrades to a plate
# rather than breaking the whole widget package.
from .terminal import TerminalWidget
from .link_health import LinkHealthWidget
from .touch_viz import TouchVizWidget
from .markdown_viewer import MarkdownViewerWidget
from .hookup import HookupWidget
from .doc_browser import DocBrowserWidget
from .config_settings import ConfigSettingsWidget
from .master_detail import MasterDetailWidget
from .agents_skills import AgentsSkillsWidget
# [SPEC:CXC-CONSOLE-PROD] app-level surfaces S4/S5 + the render-deep Config
# selectors: the Appearance selector (theme/font/dark-custom), the Collaboration
# widget-selection editor, and the Dashboard placement grid.
from .console_prefs import ConsolePrefsWidget
from .widget_select import WidgetSelectWidget
from .dashboard import DashboardWidget
from .projects_view import ProjectsViewWidget
from .away_watch_view import AwayWatchViewWidget
from .dsd_form import DsdFormWidget
# reusable soft-wrap / grow-then-scroll / vertical-caret-nav / scrollbar
# substrate ([CXC-R-ENGINE-TEXT]) — a behavior mixin (NOT a widget type, so no
# parser registration); composed by TextInputWidget, available to any
# multi-line editable widget.
from .multiline_text import MultiLineEditMixin

__all__ = [
    "GaugeWidget",
    "LEDWidget",
    "LabelWidget",
    "RectangleWidget",
    "CircleWidget",
    "PlaceholderWidget",
    "ImageWidget",
    "CarouselWidget",
    "RadioButtonGroupWidget",
    "SegmentedTabRowWidget",
    "SevenSegmentDisplay",
    "FILLED_BACKGROUND",
    "PILL_OVERLAY",
    "ButtonWidget",
    "EmitButtonWidget",
    "RelayWidget",
    "CommandWidget",
    "TextInputWidget",
    "CheckboxWidget",
    "PowerToggleWidget",
    "DocPickerWidget",
    "StoreBrowserWidget",
    "BusMonitorWidget",
    "ChatThreadWidget",
    "ActivityWidget",
    "SubagentLedgerWidget",
    "RegisteredChecklistWidget",
    "NightWatchChecklistWidget",
    "NightWatchArtifactsWidget",
    "DecisionsGraphWidget",
    "DecisionsSummaryWidget",
    "DefectsSummaryWidget",
    "DecisionsTabWidget",
    "GraphViewerWidget",
    "LtsEngineRailWidget",
    "SelfLedgerWidget",
    "DeliverableCardWidget",
    "IntelligenceCardWidget",
    "AccountCardWidget",
    "ModelPickerWidget",
    "DeliverableAlertWidget",
    "DeliverableHistoryWidget",
    "BootstrapManifestWidget",
    "ConsoleTabWidget",
    "AvatarWidget",
    "ThemeToggleWidget",
    "TabWidget",
    "CoordinateWidget",
    "CalloutWidget",
    "DialogWidget",
    "LottieWidget",
    "SvgWidget",
    "LaunchTileWidget",
    "MediaWidget",
    "MermaidWidget",
    "LogicWidget",
    "TerminalWidget",
    "LinkHealthWidget",
    "TouchVizWidget",
    "MarkdownViewerWidget",
    "DocBrowserWidget",
    "ConfigSettingsWidget",
    "MasterDetailWidget",
    "AgentsSkillsWidget",
    "ConsolePrefsWidget",
    "WidgetSelectWidget",
    "DashboardWidget",
    "ProjectsViewWidget",
    "AwayWatchViewWidget",
    "DsdFormWidget",
    "MultiLineEditMixin",
]
