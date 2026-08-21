"""
Tests for the bootstrap-manifest viewer (the Configuration tab) — the
meta-object made visible ([SPEC:CXC-CONSOLE-VISUAL] [CXC-IC-META]).

Slice acceptance: given a `self/meta` payload (the meta_publisher
projection), the widget renders the wake sequence (incl. the NEW
`principles` core element, prominent) + dusk gifts + the editable
compact-threshold + identity/channel; a wake-step tap publishes
`_app/deliverable/open` = the artifact path (the ONE Open seam, D11); a
threshold edit publishes `intelligence/architect/config/compact_threshold`
(bus-native, D13); and the Configuration tab is the 4th console tab with
the three frozen tabs + their binding SET untouched (additive).
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

# [DEC-CONSOLE-CHAT-ID-PARAM] the chat topics DERIVE from the scene's one
# chat_id line — see console_scene_ids for why hardcoding them is wrong.
from tests.render.console_scene_ids import chat_ns
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import BootstrapManifestWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.bootstrap_manifest import (
    DEFAULT_NAMESPACE, DEFAULT_OPEN_NAMESPACE, DEFAULT_CONFIG_NAMESPACE,
    THR_MIN, THR_MAX)

from .conftest import surface_to_array

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")

ROLE_DEF = ".claude/agents/composeux-architect.md"
PRINCIPLES = "docs/collaboration-and-design-principles.md"
GIFT_KEYSTONE = "bootstrap/dusk-gifts/registration-protocol.md"

META = {
    "id": "architect",
    "role": "ComposeUX / ComposeIO Architect",
    "role_descriptor": {"path": ROLE_DEF, "why": "the role I authored"},
    "soul": None,
    "soul_note": "no soul.md yet",
    "principles": {"path": PRINCIPLES, "why": "the shared principles we hold",
                   "role": "principles", "standalone_ux": True},
    "dusk_gifts": [
        {"path": GIFT_KEYSTONE, "role": "keystone", "why": "six angle-problems"},
        {"path": "bootstrap/dusk-gifts/README.md", "role": "theory",
         "why": "the premise + the one law"},
        {"path": "bootstrap/dusk-gifts/relational-registration.md",
         "role": "relationship", "why": "the thing the seam severs"},
        {"path": "bootstrap/dusk-gifts/compact-continuity-study.md",
         "role": "supporting", "why": "the measured seam"},
    ],
    "channel": {"server": "composeio-channel",
                "mqtt": "mqtt://mqtt-broker.local:1883",
                "topics": {"in": "intelligence/architect/in",
                           "out": "intelligence/architect/out"}},
    "lifecycle": {"compact_threshold": 0.90, "note": "a default hint I own"},
    "registration": {"definition": "…", "required": [ROLE_DEF]},
}

RECT = Rect(0, 0, 1256, 1524)


class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []
        self.redraws = 0

    def request_redraw(self, rect=None):
        self.redraws += 1

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


class _RecCanvas(SkiaCanvas):
    """Records drawn text (with weight + fill) — the direct probe for the
    prominent principles element + the gift role pills."""

    def __init__(self, w=1256, h=1524):
        super().__init__()
        self.create_surface(w, h)
        self._weight = "regular"
        self._fill = (0, 0, 0, 255)
        self.texts = []

    def set_font(self, family, size, weight="regular"):
        self._weight = weight
        super().set_font(family, size, weight)

    def set_fill_color(self, r, g, b, a=255):
        self._fill = (r, g, b, a)
        super().set_fill_color(r, g, b, a)

    def draw_text(self, text, x, y, align="left"):
        self.texts.append((text, self._weight, self._fill))
        super().draw_text(text, x, y, align)


def _mounted(payload=META):
    w = BootstrapManifestWidget("cfg")
    ctx = _Ctx()
    w.on_mount(ctx)
    if payload is not None:
        w.on_bus_state(DEFAULT_NAMESPACE, payload)
    w.arranged_rect = RECT
    return w, ctx


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# payload -> state (the meta_publisher contract)
# =============================================================================

def test_payload_coercion_dict_and_json_string():
    import json
    for value in (META, json.dumps(META)):
        w = BootstrapManifestWidget("cfg")
        w.on_bus_state(DEFAULT_NAMESPACE, value)
        assert w._meta["id"] == "architect"
        # threshold seeded from lifecycle.compact_threshold
        assert w._threshold == 0.90


def test_bus_update_requests_scoped_redraw_only():
    w, ctx = _mounted()
    assert ctx.redraws == 1                 # request_redraw(), nothing else
    # garbage payloads never clobber state (graceful)
    w.on_bus_state(DEFAULT_NAMESPACE, "not-json")
    w.on_bus_state(DEFAULT_NAMESPACE, 42)
    assert w._meta["id"] == "architect"


# =============================================================================
# the wake sequence — the NEW principles element is a prominent core step
# =============================================================================

def test_principles_is_a_prominent_core_wake_step():
    w, _ = _mounted()
    steps = w._wake_steps()
    by_name = {s["name"]: s for s in steps}
    # role_descriptor, principles, dusk_gifts are the three core elements
    assert "Role descriptor" in by_name
    assert "Dusk gifts" in by_name
    pr = by_name["Principles"]
    assert pr["path"] == PRINCIPLES
    assert pr["prominent"] is True          # standalone/prominent treatment
    assert pr["open"] is True               # live-linked (D11)
    # principles sits BETWEEN role_descriptor and dusk_gifts in wake order
    order = [s["name"] for s in steps]
    assert order.index("Role descriptor") < order.index("Principles") \
        < order.index("Dusk gifts")


def test_principles_and_gifts_render_in_the_wake_view():
    w, _ = _mounted()
    c = _RecCanvas()
    w.draw(c, RECT)
    drawn = [t for t, _, _ in c.texts]
    # the principles pointer path is surfaced (pointer-with-open, D12)
    assert PRINCIPLES in drawn
    assert ROLE_DEF in drawn
    # the four dusk-gift role pills render (uppercased)
    for pill in ("KEYSTONE", "THEORY", "RELATIONSHIP", "SUPPORTING"):
        assert pill in drawn
    # the principles pill renders too (its prominent role tag)
    assert "PRINCIPLES" in drawn


def test_draw_paints_and_empty_state_graceful():
    w, _ = _mounted()
    c = SkiaCanvas()
    c.create_surface(1256, 1524)
    w.draw(c, RECT)
    assert surface_to_array(c)[..., :3].std() > 3.0
    # never published, still paints (awaiting self/meta)
    e = BootstrapManifestWidget("e")
    e.on_mount(_Ctx())
    c2 = SkiaCanvas()
    c2.create_surface(1256, 1524)
    e.draw(c2, RECT)
    assert surface_to_array(c2)[..., :3].std() > 1.0


# =============================================================================
# D11 — a wake-step / gift tap publishes the ONE Open seam with the path
# =============================================================================

def _zone_center_for(w, path):
    for (x0, y0, x1, y1, p) in w._open_zones:
        if p == path:
            return (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return None


def test_wake_step_tap_publishes_open_with_artifact_path():
    w, ctx = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)                         # records the open zones
    # the principles wake step opens its artifact
    cx, cy = _zone_center_for(w, PRINCIPLES)
    w.on_tap(cx, cy)
    assert ctx.published == [(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)]
    # the role-descriptor wake step too (same ONE Open seam, D11)
    cx, cy = _zone_center_for(w, ROLE_DEF)
    w.on_tap(cx, cy)
    assert ctx.published[-1] == (DEFAULT_OPEN_NAMESPACE, ROLE_DEF)


def test_gift_tap_publishes_open_with_gift_path():
    w, ctx = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)
    cx, cy = _zone_center_for(w, GIFT_KEYSTONE)
    w.on_tap(cx, cy)
    assert ctx.published == [(DEFAULT_OPEN_NAMESPACE, GIFT_KEYSTONE)]


# =============================================================================
# D13 — the threshold edit is BUS-NATIVE (publish, never write the file)
# =============================================================================

def test_threshold_edit_publishes_config_namespace():
    w, ctx = _mounted()
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)                         # records the track rect
    tx, ty, tw, th = w._thr_track
    # tap at the track midpoint -> fraction 0.5 across [THR_MIN, THR_MAX]
    w.on_tap(tx + tw / 2.0, ty + th / 2.0)
    expect = round(THR_MIN + 0.5 * (THR_MAX - THR_MIN), 2)
    assert ctx.published == [(DEFAULT_CONFIG_NAMESPACE, expect)]
    assert w._threshold == expect           # cached locally (optimistic)


def test_threshold_edit_direct_writes_meta_file_preserving_all_keys(tmp_path):
    """[DEC-HANDOVER-MEASURED] (supersedes D13): with a meta_path, a threshold
    edit DIRECT-WRITES lifecycle.compact_threshold into the meta-object file
    (read-modify-write) — preserving EVERY other key — and does NOT publish."""
    import json
    meta_file = tmp_path / "architect.meta.json"
    meta_file.write_text(json.dumps(META, indent=2), encoding="utf-8")

    w = BootstrapManifestWidget("cfg", meta_path=str(meta_file))
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state(DEFAULT_NAMESPACE, META)
    w.arranged_rect = RECT
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)                          # records the track rect

    tx, ty, tw, th = w._thr_track
    w.on_tap(tx + tw * 0.7, ty + th / 2.0)   # 70% across [THR_MIN, THR_MAX]
    expect = round(THR_MIN + 0.7 * (THR_MAX - THR_MIN), 2)

    written = json.loads(meta_file.read_text(encoding="utf-8"))
    # the threshold changed …
    assert written["lifecycle"]["compact_threshold"] == expect
    assert w._threshold == expect
    # … and EVERY other key survived (top-level + the lifecycle note)
    assert set(written.keys()) == set(META.keys())
    assert written["lifecycle"]["note"] == META["lifecycle"]["note"]
    assert written["role_descriptor"] == META["role_descriptor"]
    assert written["dusk_gifts"] == META["dusk_gifts"]
    assert written["channel"] == META["channel"]
    # direct-write means NO bus publish on the config namespace
    assert ctx.published == []


def test_threshold_edit_falls_back_to_publish_without_meta_path():
    """With NO meta_path (a headless/test widget), the threshold edit falls
    back to the bus publish — the back-compat path stays intact."""
    w, ctx = _mounted()                      # no meta_path
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)
    tx, ty, tw, th = w._thr_track
    w.on_tap(tx + tw / 2.0, ty + th / 2.0)
    expect = round(THR_MIN + 0.5 * (THR_MAX - THR_MIN), 2)
    assert ctx.published == [(DEFAULT_CONFIG_NAMESPACE, expect)]


def test_threshold_bus_value_is_authoritative_if_newer():
    w, _ = _mounted()
    assert w._threshold == 0.90             # seeded from meta default
    # a config publish is the authoritative live value
    w.on_bus_state(DEFAULT_CONFIG_NAMESPACE, 0.82)
    assert w._threshold == 0.82 and w._config_seen is True
    # a later meta publish MUST NOT override the authoritative config value
    w.on_bus_state(DEFAULT_NAMESPACE, META)
    assert w._threshold == 0.82
    # a 0-100 config publish is normalized to a fraction (graceful)
    w.on_bus_state(DEFAULT_CONFIG_NAMESPACE, 88)
    assert w._threshold == 0.88


# =============================================================================
# wiring — parser subscribes BOTH namespaces; interactive + shadow overflow
# =============================================================================

def test_parser_wires_both_subscriptions():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "bootstrap_manifest", "id": "cfg",
         "namespace": "self/meta",
         "config_namespace": "intelligence/architect/config/compact_threshold"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed.count("self/meta") == 1
    assert subbed.count(
        "intelligence/architect/config/compact_threshold") == 1
    w = comp.subscriptions[0][1]
    assert isinstance(w, BootstrapManifestWidget)
    assert w.open_namespace == DEFAULT_OPEN_NAMESPACE
    assert w.interactive is True
    assert w.paint_overflow == ct.SHADOW_PAINT_OVERFLOW


# =============================================================================
# the console scene — Configuration is the 4th tab; frozen tabs + binding SET
# =============================================================================

def test_configuration_is_the_fourth_console_tab():
    from composeio.render.engine.parser import parse_compose_yaml
    from composeio.render.widgets import ConsoleTabWidget

    def walk(w):
        yield w
        for c in getattr(w, "children", []) or []:
            yield from walk(c)

    comp = parse_compose_yaml(SCENE)
    tabs = [w for w in walk(comp.root) if isinstance(w, ConsoleTabWidget)]
    assert len(tabs) == 1
    # Configuration is the last console tab.  The Canvas tab was RETIRED
    # ([DEC-CANVAS-TAB-RETIRED], 2026-07-24 — "basically replaced by
    # doc-viewer"), so Projects + Configuration slide up.  [CXC-ICP-S5-DASHBOARD]
    # adds a Dashboard tab between Projects and Configuration.
    assert tabs[0].labels == ["Collaboration", "Artifacts",
                              "Projects", "Dashboard", "Configuration"]
    # [CXC-IC-CONFIG-MASTERDETAIL] the Configuration page decomposes the
    # bootstrap manifest across THREE master/detail sections via the additive
    # `sections` filter: Bootstrap Manifest (`wake`), Identity
    # (`identity`/`channel`/`lifecycle`), Dusk Gifts (`dusk_gifts`).  Each is a
    # bootstrap_manifest instance subscribing self/meta — nothing dropped, the
    # content MOVED between panes.
    manifests = [w for w in walk(comp.root)
                 if isinstance(w, BootstrapManifestWidget)]
    assert len(manifests) == 3
    assert all(m.namespace == "self/meta" for m in manifests)
    by_sections = sorted((tuple(m.sections or []) for m in manifests))
    assert by_sections == sorted([
        ("wake",), ("identity", "channel", "lifecycle"), ("dusk_gifts",)])
    # the wake + identity manifests keep the direct-write threshold seam
    assert any(m.config_namespace ==
               "intelligence/architect/config/compact_threshold"
               and m.sections == ["wake"] for m in manifests)


def test_scene_declares_meta_and_config_namespaces_additively():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subbed = [ns for ns, _ in comp.subscriptions]
    # [DEC-DOCVIEWER-WINDOW] the three doc-browser OVERLAYS that each subscribed
    # self/meta are GONE.  [CXC-IC-CONFIG-MASTERDETAIL] then split the bootstrap
    # manifest into THREE master/detail sections (Bootstrap Manifest / Identity /
    # Dusk Gifts), each a bootstrap_manifest instance consuming self/meta — so the
    # ONE declared namespace now has THREE legit consumers (the split, not a leak).
    assert subbed.count("self/meta") == 3
    assert "intelligence/architect/config/compact_threshold" in subbed
    declared = [n.get("name") for n in comp.namespaces]
    assert "self/meta" in declared
    assert "intelligence/architect/config/compact_threshold" in declared
    # self/meta seeded with the FULL REAL projection so the headless render
    # shows real data (principles element present in the seed).
    meta_ns = next(n for n in comp.namespaces if n.get("name") == "self/meta")
    assert meta_ns.get("scope") == "global"
    seed = meta_ns.get("initial", {})
    assert seed.get("principles", {}).get("standalone_ux") is True
    assert len(seed.get("dusk_gifts", [])) == 4


def test_frozen_tab_binding_set_is_intact():
    """FROZEN-WIRING DISCIPLINE: adding the Configuration tab + the meta
    namespaces touches NO existing binding — the proven subscription SET of
    the three frozen tabs (and the chrome) survives unchanged."""
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(SCENE)
    subbed = set(ns for ns, _ in comp.subscriptions)
    # [DEC-CONSOLE-CHAT-ID-PARAM] the chat leg is id-PARAMETERISED now, so the
    # frozen set derives the same `$chat_id` the scene does — asserting the literal
    # would freeze WHICH intelligence the console talks to, when what is frozen is
    # the binding SHAPE.  The host-pinned telemetry legs stay literal on purpose:
    # that is the half that must NOT follow chat_id.
    # [CXC-FC-UNIT] the console declares a FLEET; the primary instance is entry 0 of
    # `instances:`, and there is no `$chat_id` variable any more ([CXC-FC-VAR-SCOPE]).
    chat = comp.instances[0]["id"]
    # the proven SUBSCRIPTION set of the three frozen tabs + chrome
    frozen_subs = {
        # Collaboration hero + composer + link health (proven MVP seam)
        # [CXC-IH-5A] per-instance feeds id-scoped to */architect/*; intent + other-daemon bare
        "thread/architect/narration", f"intelligence/{chat}/out",
        f"intelligence/{chat}/in", f"intelligence/{chat}/channel/alive",
        # rail cards
        "self/architect/context_pct", "self/architect/registered", "self/architect/subagents",
        "self/architect/rate_limit", "self/deliverables",
        # console-level tab + model picker seams
        "_app/console/tab", "_app/model_picker",
        # (Canvas tab + its thread/architect/diagram binding were RETIRED
        #  2026-07-24 — [DEC-CANVAS-TAB-RETIRED]; no longer a frozen sub.)
    }
    assert not (frozen_subs - subbed), \
        f"frozen subscriptions dropped: {frozen_subs - subbed}"
    # frozen attr-binding: the link-state label/button text+color binding
    attr_bound = set(b.namespace for b in comp.attr_bindings)
    assert "console/link/state" in attr_bound
    # frozen publish seam: the Open target namespace still declared
    declared = set(n.get("name") for n in comp.namespaces)
    assert "_app/deliverable/open" in declared
    # the frozen tab-state seam still declared, still local
    tab_ns = next(n for n in comp.namespaces
                  if n.get("name") == "_app/console/tab")
    assert tab_ns.get("scope") == "local"


# =============================================================================
# ADDITIVE `sections` filter ([CXC-IC-CONFIG-MASTERDETAIL]) — the master/detail
# right pane draws ONE card; UNSET preserves the legacy lead + two-column layout
# =============================================================================

def _texts_for(sections):
    w = BootstrapManifestWidget("cfg", sections=sections)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state(DEFAULT_NAMESPACE, META)
    w.arranged_rect = RECT
    c = _RecCanvas()
    w.draw(c, RECT)
    return w, [t for (t, _wt, _f) in c.texts]


def test_sections_unset_renders_the_full_legacy_layout():
    w, joined = _texts_for(None)
    assert w.sections is None
    j = "".join(joined)
    # the lead title + every card header is present (nothing filtered)
    assert "Bootstrap manifest" in j
    for header in ("WAKE SEQUENCE", "DUSK GIFTS", "LIFECYCLE", "IDENTITY",
                   "CHANNEL"):
        assert header in j


def test_sections_wake_only_draws_the_wake_card():
    w, joined = _texts_for(["wake"])
    j = "".join(joined)
    assert "WAKE SEQUENCE" in j
    # the identity/channel/lifecycle/gifts CARDS are not drawn — probe their
    # UNIQUE content (not the ambiguous header token, since the wake steps carry
    # an IDENTITY role pill + a "Dusk gifts" step by design).
    assert "start crossing at" not in j          # lifecycle slider caption
    assert "the warm-start bequest" not in j     # gifts card subtitle
    assert "composeio-channel" not in j          # channel card server value
    # no lead title in a filtered pane (the nav labels the section)
    assert "Bootstrap manifest" not in j
    # the threshold track (a lifecycle-only hit zone) is absent
    assert w._thr_track is None


def test_sections_identity_bundle_draws_identity_channel_lifecycle():
    w, joined = _texts_for(["identity", "channel", "lifecycle"])
    j = "".join(joined)
    for header in ("IDENTITY", "CHANNEL", "LIFECYCLE"):
        assert header in j
    assert "WAKE SEQUENCE" not in j and "DUSK GIFTS" not in j
    # the lifecycle slider is present -> its tap zone is recorded
    assert w._thr_track is not None


def test_sections_dusk_gifts_only_lists_the_gifts():
    w, joined = _texts_for(["dusk_gifts"])
    j = "".join(joined)
    assert "DUSK GIFTS" in j
    assert "WAKE SEQUENCE" not in j and "IDENTITY" not in j
    # the gift filenames render (content not dropped) -> open zones recorded
    assert len(w._open_zones) == 4


def test_parser_passes_bootstrap_sections_through():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "bootstrap_manifest", "id": "bm", "sections": ["identity"]}]})
    w = comp.subscriptions[0][1]
    assert isinstance(w, BootstrapManifestWidget)
    assert w.sections == ["identity"]


# =============================================================================
# ADDITIVE `method_flow` bootstrap element ([FLOW:MAKER-PROJECT] [MPF-BOOTSTRAP],
# the maker pivot) — a prominent wake step surfaced the way `principles` is,
# present ONLY when the meta-object declares it.
# =============================================================================

MAKER_FLOW_PATH = "docs/maker/maker-project-flow.md"


def _meta_with_flow():
    import copy
    m = copy.deepcopy(META)
    m["method_flow"] = {"path": MAKER_FLOW_PATH,
                        "why": "the six-step a-f maker flow + the pin->slot->"
                               "binding seam + the one-compose.yaml model",
                        "role": "method", "standalone_ux": True}
    return m


def test_method_flow_absent_leaves_wake_sequence_unchanged():
    """No method_flow key -> the wake sequence is byte-identical to before
    (the additive step never appears): no 'Maker-project flow' step, and Dusk
    gifts keeps its legacy '3'."""
    w, _ = _mounted()                       # module META has no method_flow
    steps = w._wake_steps()
    names = [s["name"] for s in steps]
    assert "Maker-project flow" not in names
    gifts = next(s for s in steps if s["name"] == "Dusk gifts")
    assert gifts["num"] == "3"


def test_method_flow_is_a_prominent_wake_step_ordered_after_principles():
    w, _ = _mounted(_meta_with_flow())
    steps = w._wake_steps()
    by_name = {s["name"]: s for s in steps}
    mf = by_name["Maker-project flow"]
    assert mf["path"] == MAKER_FLOW_PATH
    assert mf["prominent"] is True          # first-class, like principles
    assert mf["open"] is True               # live-linked via the ONE Open seam
    assert mf["role"] == "method"
    order = [s["name"] for s in steps]
    assert order.index("Principles") < order.index("Maker-project flow") \
        < order.index("Dusk gifts")
    # the presence of the method step pushes Dusk gifts to '4' (renumbered)
    assert by_name["Dusk gifts"]["num"] == "4"


def test_method_flow_renders_path_and_method_pill_in_the_wake_view():
    w = BootstrapManifestWidget("cfg")
    w.on_mount(_Ctx())
    w.on_bus_state(DEFAULT_NAMESPACE, _meta_with_flow())
    w.arranged_rect = RECT
    c = _RecCanvas()
    w.draw(c, RECT)
    drawn = [t for t, _, _ in c.texts]
    assert MAKER_FLOW_PATH in drawn
    assert "METHOD" in drawn                 # the method role pill (uppercased)


def test_method_flow_tap_publishes_open_with_the_flow_path():
    w = BootstrapManifestWidget("cfg")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state(DEFAULT_NAMESPACE, _meta_with_flow())
    w.arranged_rect = RECT
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)                          # records the open zones
    cx, cy = _zone_center_for(w, MAKER_FLOW_PATH)
    w.on_tap(cx, cy)
    assert ctx.published == [(DEFAULT_OPEN_NAMESPACE, MAKER_FLOW_PATH)]
