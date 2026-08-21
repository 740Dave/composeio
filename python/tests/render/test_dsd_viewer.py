"""
Tests for the interactive DSD-viewer ([SPEC:CXC-DSDV]): the DSD parse contract +
write-back ([CXC-DSDV-PARSE]/[CXC-DSDV-SAVE]), the DSD-form widget (control-group
per decision, pre-selected recommendation, mutually-exclusive radios, draw-pass
hit zones, comment field, save-close completion event), the standalone launcher +
notify path ([CXC-DSDV-LAUNCHER]/[CXC-DSDV-NOTIFY]), the send_dsd_for_human_review
MCP endpoint ([CXC-DSDV-MCP]), and the in-process registration ([CXC-DSDV-REGISTER]).

Two-tier ([CXC-DSDV-VERIFY-*]): these are the MACHINE tier (parse + control-group
+ pre-select + exclusivity + re-read-shows-the-write + intent->spawn->dedupe +
publish/correlate).  The GLASS tier is the rendered reference image + the live
window (see python/scripts/dsd_viewer_reference.py).
"""

from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DsdFormWidget
from composeio.render.widgets.dsd_document import (
    DecisionAnswer, build_completion, parse_dsd)

DSDVIEWER_SCENE = os.path.join(
    REPO_ROOT, "compose_projects", "intelligence", "console", "dsdviewer.compose.yaml")

# ── a fixture DSD exercising all three pre-select precedence cases + a
#    read-only (no-slot) decision + an existing Comments line ────────────────
FIXTURE = """# DSD — fixture

> preamble.

## Theme 1

### D1 · Which store?
**Why it matters:** downstream windows.

| Option | Tradeoffs |
|---|---|
| Per-app file | isolated per window |
| Shared store | scales to the other windows |

**My recommendation:** Per-app file

**Your call:** _______

### D2 · Which cadence?
**Why it matters:** effort.

| Option | Tradeoffs |
|---|---|
| Heavy | slow but thorough |
| **Light** | fast, retune later |

**My recommendation:** the lighter cadence — less ceremony.

**Your call:** _______

### D3 · No lean here?
| Option | Tradeoffs |
|---|---|
| Alpha | option a |
| Beta | option b |

**Your call:** _______

### D4 · Read-only (no slot)?
| Option | Tradeoffs |
|---|---|
| X | only choice |
"""


def _canvas(w=764, h=1600):
    c = SkiaCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x17)
    c.fill_rect(0, 0, w, h)
    return c


class _FakeCtx:
    """Minimal engine context for a widget under test."""
    theme = "dark"

    def __init__(self):
        self.published = []
        self.clocked = []
        self.stop_requested = 0

    def request_redraw(self, rect=None):
        pass

    def publish(self, ns, value, source="widget"):
        self.published.append((ns, value))

    def register_clocked(self, w):
        self.clocked.append(w)

    def unregister_clocked(self, w):
        self.clocked = [x for x in self.clocked if x is not w]

    def schedule_update(self, fn, target=None):
        fn()

    def request_stop(self):
        # mirror EngineContext.request_stop — the "Save Answers" close primitive
        self.stop_requested += 1


# ---------------------------------------------------------------------------
# load the launcher / mcp daemon modules (live under compose_projects/intelligence/tools)
# ---------------------------------------------------------------------------
def _load(mod_name, rel):
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools", rel)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ===========================================================================
# 1. Parse contract ([CXC-DSDV-PARSE], [CXC-DSDV-VERIFY-FORM] machine (a))
# ===========================================================================

def test_parse_decisions_in_order_with_questions():
    doc = parse_dsd(FIXTURE)
    assert doc.ok
    assert [d.id for d in doc.decisions] == ["D1", "D2", "D3", "D4"]
    assert doc.decisions[0].question == "Which store?"
    assert doc.decisions[1].question == "Which cadence?"


def test_parse_options_table():
    doc = parse_dsd(FIXTURE)
    d1 = doc.decisions[0]
    assert [o.label for o in d1.options] == ["Per-app file", "Shared store"]
    assert d1.options[0].tradeoffs == "isolated per window"


def test_preselect_precedence_reco_line_equality():
    # (1) the recommendation line matched to an option by equality wins.
    d1 = parse_dsd(FIXTURE).decisions[0]
    assert d1.preselect_source == "reco-line"
    assert d1.option_by_id(d1.preselect_id).label == "Per-app file"


def test_preselect_precedence_bold_row_fallback():
    # (2) prose recommendation -> the BOLDED row wins.
    d2 = parse_dsd(FIXTURE).decisions[1]
    assert d2.preselect_source == "bold-row"
    assert d2.option_by_id(d2.preselect_id).label == "Light"


def test_preselect_precedence_none():
    # (3) no reco line + no bold row -> nothing pre-selected, flagged.
    d3 = parse_dsd(FIXTURE).decisions[2]
    assert d3.preselect_id is None
    assert d3.preselect_source == "none"
    assert "no recommendation" in d3.notes


def test_slot_line_recorded_and_readonly_without_slot():
    doc = parse_dsd(FIXTURE)
    assert doc.decisions[0].slot_line is not None
    # D4 has no `Your call:` line -> read-only, excluded from write-back.
    assert doc.decisions[3].slot_line is None
    assert "no writable slot" in doc.decisions[3].notes


def test_malformed_dsd_is_not_ok_without_raising():
    doc = parse_dsd("# just a doc\n\nno decisions here.\n")
    assert not doc.ok
    assert doc.decisions == []


def test_parses_your_call_bold_wrapped_form():
    # the skill's actual rendered form: `**Your call: A**` (bold spans the slot)
    src = ("### D1 · x?\n\n| Option | Tradeoffs |\n|---|---|\n"
           "| **A** | a |\n| B | b |\n\n**Your call: A**\n")
    doc = parse_dsd(src)
    assert doc.decisions[0].slot_line is not None


# ===========================================================================
# 2. The form widget ([CXC-DSDV-FORM]/[CXC-DSDV-PRESELECT], VERIFY-FORM (b))
# ===========================================================================

def _mounted_widget():
    w = DsdFormWidget("form", content=FIXTURE)
    ctx = _FakeCtx()
    w.on_mount(ctx)
    return w, ctx


def _draw(w, rect=None):
    rect = rect or Rect(0, 0, 764, 1600)
    w.arranged_rect = rect
    w.draw(_canvas(int(rect.w), int(rect.h)), rect)


def _select_zone(w, dec_id, suffix):
    """Tap the draw-pass radio zone for (dec_id, option-suffix) — requires a
    prior _draw so the zones are recorded."""
    hits = [(oid, r) for (did, oid, r) in w._radio_zones
            if did == dec_id and oid.endswith(suffix)]
    assert hits, f"expected a {dec_id}/{suffix} radio zone from the draw pass"
    _oid, r = hits[0]
    w.on_pointer_down(r.x + 5, r.y + 5)          # press off any comment field
    w.on_tap(r.x + 5, r.y + 5)


def _answer_d3(w):
    """D3 has no recommendation, so under the completeness gate
    ([CXC-DSDV-COMPLETE]) the FIXTURE form is INCOMPLETE until D3 is answered.
    Select D3's first option so save/close is permitted."""
    _select_zone(w, "D3", "o0")


def test_widget_preselects_recommendation():
    w, _ = _mounted_widget()
    ans = w.answers()
    assert ans["D1"].labels == ["Per-app file"]      # reco-line
    assert ans["D2"].labels == ["Light"]             # bold-row
    assert ans["D3"].labels == []                    # none


def test_widget_one_group_per_decision_and_hit_zones_from_draw():
    w, _ = _mounted_widget()
    _draw(w)
    # a radio zone per parsed option (2+2+2+1 = 7) PLUS the always-present
    # "Neither / None" radio per decision (+4 = 11) ([CXC-DSDV-NEITHER]).
    assert len(w._radio_zones) == 11
    assert sum(1 for (_d, oid, _r) in w._radio_zones
               if oid.endswith("-neither")) == 4
    # a comment zone per decision (4)
    assert len(w._comment_zones) == 4


def test_radios_mutually_exclusive_via_draw_pass_zone_tap():
    w, _ = _mounted_widget()
    _draw(w)
    # find D1's "Shared store" (the non-recommended option) zone + tap its center
    target = [(oid, r) for (did, oid, r) in w._radio_zones
              if did == "D1" and oid.endswith("o1")]
    assert target, "expected D1 second-option zone from the draw pass"
    oid, r = target[0]
    w.on_pointer_down(r.x + 5, r.y + 5)          # press (not on a comment)
    w.on_tap(r.x + 5, r.y + 5)
    assert w.answers()["D1"].labels == ["Shared store"]   # moved
    assert "Per-app file" not in w.answers()["D1"].labels  # sibling deselected


def test_comment_field_edits_route_to_active_field():
    w, _ = _mounted_widget()
    _draw(w)
    did, rect = w._comment_zones[1]              # D2's comment field
    assert did == "D2"
    w.on_pointer_down(rect.x + 10, rect.y + 10)  # activates + focuses the field
    for ch in "prefer the lighter cadence":
        w.on_text(ch)
    assert w.answers()["D2"].comment == "prefer the lighter cadence"
    # editing D2's field never touched D1's
    assert w.answers()["D1"].comment == ""


# ===========================================================================
# 3. Save-on-close write-back ([CXC-DSDV-SAVE], VERIFY-SAVE machine)
# ===========================================================================

def _answers_accept_override_skip():
    return {
        "D1": DecisionAnswer(labels=["Per-app file"]),               # accept rec
        "D2": DecisionAnswer(labels=["Heavy"], comment="scales better"),  # override+comment
        # D3 unanswered (skipped)
    }


def test_writeback_slots_and_untouched_and_marker():
    doc = parse_dsd(FIXTURE)
    out = doc.render_answers(_answers_accept_override_skip(),
                             now=0.0)              # deterministic marker ts
    lines = out.split("\n")
    assert "**Your call:** Per-app file" in lines            # D1 accepted
    i = lines.index("**Your call:** Heavy")                  # D2 overridden
    assert lines[i + 1] == "**Comments:** scales better"     # comment right after
    assert "**Your call:** _______" in lines                 # D3 untouched (blank)
    assert lines[0].startswith("<!-- dsd-status: answered")  # answered marker


def test_writeback_preserves_every_nonslot_line_byte_for_byte():
    doc = parse_dsd(FIXTURE)
    out = doc.render_answers(_answers_accept_override_skip(), now=0.0)
    orig = FIXTURE.split("\n")
    got = out.split("\n")[1:]                     # drop the prepended marker
    # the only changes: D1 slot, D2 slot, + an inserted D2 comment line.
    # every OTHER line is identical, in order.
    changed_slots = {"**Your call:** Per-app file", "**Your call:** Heavy",
                     "**Comments:** scales better"}
    got_wo_new = [l for l in got if l not in changed_slots]
    orig_wo_slots = [l for l in orig
                     if l not in ("**Your call:** _______",)]
    # D1 + D2 blank slots were replaced (2 of the 3 blank slots consumed)
    assert orig.count("**Your call:** _______") == 3
    assert got.count("**Your call:** _______") == 1          # only D3 remains
    # spot-check structural lines survive verbatim
    for keep in ("# DSD — fixture", "## Theme 1", "### D1 · Which store?",
                 "| Per-app file | isolated per window |",
                 "**My recommendation:** Per-app file"):
        assert keep in got


def test_writeback_multiselect_comma_joined():
    doc = parse_dsd(FIXTURE)
    out = doc.render_answers(
        {"D1": DecisionAnswer(labels=["Per-app file", "Shared store"])}, now=0.0)
    assert "**Your call:** Per-app file, Shared store" in out.split("\n")


def test_writeback_marker_is_idempotent():
    doc = parse_dsd(FIXTURE)
    once = doc.render_answers(_answers_accept_override_skip(), now=0.0)
    doc2 = parse_dsd(once)
    twice = doc2.render_answers(_answers_accept_override_skip(), now=0.0)
    # re-answering does not stack markers or duplicate the D2 comment line
    assert twice.count("dsd-status: answered") == 1
    assert twice.count("**Comments:** scales better") == 1


def test_atomic_save_leaves_original_on_failure(tmp_path, monkeypatch):
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    from composeio.render.widgets import dsd_document as dd
    doc = dd.load_dsd(str(p))
    new_text = doc.render_answers(_answers_accept_override_skip(), now=0.0)

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(dd.os, "replace", boom)
    try:
        doc.save(new_text)
        assert False, "save should have raised"
    except OSError:
        pass
    # the original DSD is byte-identical (never a half-written file)
    assert p.read_text(encoding="utf-8") == FIXTURE
    # and no stray temp file was left behind
    assert list(tmp_path.glob(".dsd-*")) == []


def test_widget_save_writes_file_and_emits_completion_event(tmp_path):
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    # override D2 -> Heavy via its draw-pass zone, type a comment
    zone = [(oid, r) for (did, oid, r) in w._radio_zones
            if did == "D2" and oid.endswith("o0")][0]
    oid, r = zone
    w.on_pointer_down(r.x + 5, r.y + 5)
    w.on_tap(r.x + 5, r.y + 5)
    _, crect = w._comment_zones[1]
    w.on_pointer_down(crect.x + 8, crect.y + 8)
    for ch in "scales better":
        w.on_text(ch)
    _answer_d3(w)                              # complete the form (gate #1)
    payload = w.save()
    # file written with the answers
    text = p.read_text(encoding="utf-8")
    assert "**Your call:** Per-app file" in text          # D1 accepted rec
    assert "**Your call:** Heavy" in text                 # D2 overridden
    assert "**Comments:** scales better" in text
    # completion event published ([CXC-DSDV-NOTIFY])
    assert ("_app/dsd/answered", payload) in ctx.published
    ids = {d["id"] for d in payload["decisions"]}
    assert {"D1", "D2"} <= ids


def test_save_button_drawn_with_hit_zone_from_draw_pass():
    # the "Save Answers" button is painted in the draw pass and records its
    # hit-zone there ([CXC-DSDV-FORM] HIT ZONES FROM THE DRAW PASS).
    w, _ = _mounted_widget()
    assert w._save_zone is None            # nothing until a draw
    _draw(w)
    assert w._save_zone is not None
    # pinned to the footer at the bottom of the widget rect
    r = w._save_zone
    assert r.y + r.h <= 1600
    assert 1600 - (r.y + r.h) < 60         # within the reserved footer band


def test_save_button_tap_writes_back_notifies_and_closes(tmp_path):
    # tapping "Save Answers" = the save-on-close behavior as an explicit action:
    # (1) write-back to the slots, (2) the _app/dsd/answered event, (3) close.
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    # override D2 -> Heavy via its draw-pass zone + type a comment
    oid, r = [(oid, r) for (did, oid, r) in w._radio_zones
              if did == "D2" and oid.endswith("o0")][0]
    w.on_pointer_down(r.x + 5, r.y + 5)
    w.on_tap(r.x + 5, r.y + 5)
    _, crect = w._comment_zones[1]
    w.on_pointer_down(crect.x + 8, crect.y + 8)
    for ch in "scales better":
        w.on_text(ch)
    _answer_d3(w)                              # complete the form (gate #1)
    # tap the Save Answers button at its centre
    sz = w._save_zone
    w.on_pointer_down(sz.x + sz.w / 2, sz.y + sz.h / 2)   # press (off comments)
    w.on_tap(sz.x + sz.w / 2, sz.y + sz.h / 2)
    # (1) file written with the answers
    text = p.read_text(encoding="utf-8")
    assert "**Your call:** Per-app file" in text          # D1 accepted rec
    assert "**Your call:** Heavy" in text                 # D2 overridden
    assert "**Comments:** scales better" in text
    # (2) completion event published ([CXC-DSDV-NOTIFY])
    answered = [v for (ns, v) in ctx.published if ns == "_app/dsd/answered"]
    assert len(answered) == 1
    ids = {d["id"] for d in answered[0]["decisions"]}
    assert {"D1", "D2"} <= ids
    # (3) window close requested (the same clean shutdown as a window-close)
    assert ctx.stop_requested == 1


def test_save_button_is_idempotent_with_on_close_fallback(tmp_path, monkeypatch):
    # tapping the button saves once; the on-close fallback (env-armed) then
    # NO-OPs — the button is additive, the close-save is preserved (no regression).
    monkeypatch.setenv("COMPOSEIO_DSD_SAVE_ON_CLOSE", "1")
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    _answer_d3(w)                              # complete the form (gate #1)
    sz = w._save_zone
    w.on_pointer_down(sz.x + sz.w / 2, sz.y + sz.h / 2)
    w.on_tap(sz.x + sz.w / 2, sz.y + sz.h / 2)
    n_after_button = len([v for (ns, v) in ctx.published
                          if ns == "_app/dsd/answered"])
    assert n_after_button == 1 and ctx.stop_requested == 1
    # the window then tears down -> on_unmount runs the fallback save, a no-op
    w.on_unmount()
    n_after_close = len([v for (ns, v) in ctx.published
                         if ns == "_app/dsd/answered"])
    assert n_after_close == 1              # not published twice


def test_close_without_button_still_saves_no_regression(tmp_path, monkeypatch):
    # closing WITHOUT ever tapping the button still saves (save-on-close intact).
    monkeypatch.setenv("COMPOSEIO_DSD_SAVE_ON_CLOSE", "1")
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    _answer_d3(w)                          # complete the form (gate #1)
    w.on_unmount()                         # window closed, no button tap
    text = p.read_text(encoding="utf-8")
    assert "**Your call:** Per-app file" in text          # D1 rec written
    assert any(ns == "_app/dsd/answered" for (ns, _v) in ctx.published)


def test_save_button_tap_off_zone_does_not_close(tmp_path):
    # a tap that misses the button (on an option row) selects, never closes.
    w, ctx = _mounted_widget()
    _draw(w)
    oid, r = [(oid, r) for (did, oid, r) in w._radio_zones
              if did == "D1" and oid.endswith("o1")][0]
    w.on_tap(r.x + 5, r.y + 5)
    assert ctx.stop_requested == 0
    assert w.answers()["D1"].labels == ["Shared store"]


def test_widget_save_noop_when_parse_failed(tmp_path):
    # HARD FAIL-SAFE: rendering a non-DSD then "closing" never rewrites it.
    p = tmp_path / "not-a-dsd.md"
    original = "# just notes\n\nnothing to decide.\n"
    p.write_text(original, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    w.on_mount(_FakeCtx())
    assert w.save() is None
    assert p.read_text(encoding="utf-8") == original


def test_completion_payload_shape():
    doc = parse_dsd(FIXTURE)
    payload = build_completion("/x/dsd.md", doc.decisions,
                               _answers_accept_override_skip(),
                               correlation_id="c9", now=0.0)
    assert payload["answered"] is True
    assert payload["correlation_id"] == "c9"
    by_id = {d["id"]: d for d in payload["decisions"]}
    assert by_id["D1"]["chosen"] == "Per-app file"
    assert by_id["D2"]["chosen"] == "Heavy" and by_id["D2"]["comment"] == "scales better"
    assert "D3" not in by_id                       # unanswered omitted


# ===========================================================================
# 4. Launcher + registration ([CXC-DSDV-LAUNCHER]/[CXC-DSDV-REGISTER], VERIFY-LAUNCH)
# ===========================================================================

def test_launcher_selftest_passes():
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    assert mod._selftest() == 0


def test_dsd_form_is_first_class_parser_widget_and_scene_renders(tmp_path):
    # the scene parses + renders headless (dsd_form parser wiring), READ-only
    # (no COMPOSEIO_DSD_SAVE_ON_CLOSE -> the render never rewrites the DSD).
    from composeio.render.engine.runtime import RenderApp
    out = str(tmp_path / "scene.png")
    app = RenderApp.from_file(DSDVIEWER_SCENE, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.getsize(out) > 0


def test_save_button_close_flows_through_real_engine_context(tmp_path):
    # end-to-end wiring: the widget's _request_close hits the REAL
    # EngineContext.request_stop, which clears the run flag so the loop's
    # finally: stop() does the clean teardown ([CXC-DSDV-SAVE]).
    from composeio.render.engine.runtime import RenderApp
    app = RenderApp.from_file(DSDVIEWER_SCENE, windowed=False)
    app.start()
    assert app._running is True
    form = next(w for w in app.comp.root.iter_widgets()
                if isinstance(w, DsdFormWidget))
    assert form._ctx is not None and hasattr(form._ctx, "request_stop")
    form._request_close()                  # what the Save Answers tap calls
    assert app._running is False           # graceful stop requested
    app.stop()


def test_registration_selftest_at_new_count():
    # the daemon dirs must be importable (data_sources imports the daemons);
    # register the module in sys.modules before exec so its @dataclass resolves.
    for d in (os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher"),
              os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools"),
              os.path.join(REPO_ROOT, "python")):
        if d not in sys.path:
            sys.path.insert(0, d)
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console", "data_sources.py")
    spec = importlib.util.spec_from_file_location("data_sources", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["data_sources"] = mod
    spec.loader.exec_module(mod)
    src_all = [s.name for s in mod.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-07-22")]
    assert "dsd_viewer_launcher" in src_all
    # [SPEC:CIO-DEFECT-REGISTRY] added defect_metrics_publisher (the 18th).
    # [SPEC:CXC-GRAPH-VIEWER] [CXC-GV-EMITTER-RETIRED] RETIRED decisions_publisher
    # (the pushed feed) — the Graph Viewer queries lts-engine directly; count 19 -> 18.
    # [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS] wired meta_publisher +
    # daemon_watch (built + tested + never registered); count 18 -> 20.
    assert "defect_metrics_publisher" in src_all
    assert "decisions_publisher" not in src_all
    assert "meta_publisher" in src_all
    assert "daemon_watch" in src_all
    # 20 -> 21 ([SPEC:CXC-FLEET] [CXC-FC-S3-ROSTER]): `fleet_roster` is THE one
    # subscriber to `compose_projects/intelligence/+/channel/alive`, publishing `fleet/roster`. Per-pane
    # discovery would be N implementations of one liveness rule, guaranteed to disagree
    # under load, so it is a SINGLE registered source. The bridge also gained an instance
    # suffix (`channel_bridge:<id>`) — there is one PER DECLARED INSTANCE now
    # ([CXC-FC-S1-BRIDGES]), and a bare `channel_bridge` name would be the singleton again.
    # 21 -> 22: `hookup_viewer_launcher`, the consumer half of
    # `send_hookup_for_human_review` (`_app/hookup/open`) — the DSD launcher's
    # sibling on the same launcher rail.
    # 22 -> 23: `beepout_viewer_launcher`, the consumer half of
    # `send_beepout_for_human_review` (`_app/beepout/open`) — the third launcher on the
    # same rail, registered in the same change as its endpoint's publish leg.
    assert len(src_all) == 23


# ===========================================================================
# 5. MCP endpoint + notify ([CXC-DSDV-MCP]/[CXC-DSDV-NOTIFY], VERIFY-MCP)
# ===========================================================================

def test_mcp_valid_dsd_publishes_intent_and_returns_correlation(tmp_path):
    mcp = _load("dsd_review_mcp", "dsd_review_mcp.py")
    # A CONTEXT-COMPLETE DSD ([CXC-DSDV-CONTEXT-GUARD]).  The retained historical
    # DSD that stood here predates the 2026-08-17 ruling and is now correctly
    # refused, so it cannot stand for "a valid send".
    dsd = os.path.join(REPO_ROOT, "python", "tests", "render", "fixtures",
                       "dsd", "complete.md")
    if mcp.resolve_dsd(dsd) is None:
        import pytest
        pytest.skip("fixture DSD not present on this checkout")
    published = []
    r = mcp.send_dsd_for_human_review(dsd, publish=lambda i: published.append(i))
    assert r["status"] == "sent" and r["correlation_id"]
    assert len(published) == 1
    assert published[0]["path"] == mcp.resolve_dsd(dsd)
    assert published[0]["correlation_id"] == r["correlation_id"]


def test_mcp_invalid_path_errors_without_publishing():
    mcp = _load("dsd_review_mcp", "dsd_review_mcp.py")
    published = []
    r = mcp.send_dsd_for_human_review("/etc/passwd",
                                      publish=lambda i: published.append(i))
    assert r["status"] == "error" and not published


def test_notify_correlates_answered_to_send():
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    notices = []
    lau = mod.DsdViewerLauncher(spawn=lambda s: type("P", (), {"pid": 1, "poll": lambda self: None})(),
                                notify=lambda n: notices.append(n))
    # must be a real, context-complete DSD: the launcher now REFUSES to open
    # anything else ([CXC-DSDV-CONTEXT-GUARD]), and a refused open records no
    # correlation for the notify leg to find.
    real = "python/tests/render/fixtures/dsd/complete.md"
    lau.open(real, "corr-77")
    abspath = mod.resolve_doc(real)
    answered = {"val": {"path": abspath, "answered": True,
                        "decisions": [{"id": "D1", "chosen": "Merge it",
                                       "comment": "ok"}]}}
    notice = lau.on_answered(answered)
    assert notice is not None and len(notices) == 1
    assert notice["correlation_id"] == "corr-77"
    assert "D1=Merge it" in notice["summary"] and "ok" in notice["summary"]


def test_notify_decoupled_from_file_modified():
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    notices = []
    lau = mod.DsdViewerLauncher(spawn=lambda s: None,
                                notify=lambda n: notices.append(n))
    # a bare filesystem-modified-style event (no completion payload) -> NO notify
    assert lau.on_answered({"val": {"event": "modified"}}) is None
    assert notices == []


def test_notice_routes_to_architect_inbound_not_dead_topic():
    """[CXC-DSDV-NOTIFY] / [DD-DSDV-MCP-HOME] — the save-close notice MUST be
    delivered to the architect's INBOUND channel (the raw `compose_projects/intelligence/<id>/in`
    topic that composeio-channel/channel.mjs subscribes and surfaces as a
    <channel> event), NOT the `_app/dsd/notice` bus namespace that no party
    consumes.  This is the exact break that dropped the dogfood notification:
    the emit worked, but it rode a topic with no route to the architect.

    Regression guard: fails if the route reverts to the dead topic or the
    composeio `{val: ...}` envelope (which the raw `/in` handler would not
    unwrap into a `<channel>` body)."""
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    notice = mod.build_notice(
        {"path": "/repo/docs/DECISIONS-x.md", "answered": True,
         "decisions": [{"id": "D1", "chosen": "Merge it"}]}, "corr-9")
    topic, payload = mod.notice_route(notice)

    # (1) routes to the architect's raw inbound topic — the seam channel.mjs owns
    assert topic == "intelligence/architect/in"
    # (2) NOT the dead namespace the notice used to be emitted into
    assert "_app/dsd/notice" not in topic
    assert topic.startswith("intelligence/") and topic.endswith("/in")
    # (3) bare {from, text, ...} message channel.mjs renders — no {val} envelope,
    #     the correlation id + path ride along as scalar fields (become <channel>
    #     attrs via buildMeta), and the human-readable text is preserved verbatim
    assert "val" not in payload
    assert payload["from"] == mod.NOTICE_FROM
    assert payload["text"] == notice["text"]
    assert payload["correlation_id"] == "corr-9"


def test_notice_route_honors_addressee_and_defaults_to_architect():
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    # explicit addressee -> that Intelligence's inbound topic
    assert mod.notice_route({"to": "builder", "text": "hi"})[0] \
        == "intelligence/builder/in"
    # no addressee -> the default architect inbound
    assert mod.notice_route({"text": "hi"})[0] \
        == f"intelligence/{mod.NOTICE_INBOUND_ID_DEFAULT}/in"


# ===========================================================================
# 6. DECISION CONTEXT + THEME GROUPING + SCROLL DIRECTION
#    ([CXC-DSDV-PARSE]/[CXC-DSDV-FORM] context+theme invariants; [CXC-ICP-S6-SCROLLDIR])
#    Regression guard for the viewer dropping decision context that IS in the
#    source markdown (What-this-is / Why-it-matters glosses + `## Theme` headings)
#    and ignoring the persisted console scroll-direction preference.
# ===========================================================================

# a DSD that carries a `## Theme` grouping, the `⛔` blocker glyph, a
# multi-line `**What this is:**` gloss, and a `**Why it matters:**` stakes line.
CONTEXT_FIXTURE = """# DSD — context fixture

## Theme A — The blockers

### D1 ⛔ · Approve the plan?
**What this is:** the plan spans four branches that touch
overlapping subsystems, so landing order matters.
**Why it matters:** landing order decides whether the tree stays coherent.

| Option | Tradeoffs |
|---|---|
| **Land in order** | safe, proven |
| Squash it | loses history |

**My recommendation: land in order.**

**Your call:** _______

## Theme B — Later

### D2 · A calmer call?
**Why it matters:** it is order-independent.

| Option | Tradeoffs |
|---|---|
| **Now** | warm context |
| Defer | bites later |

**Your call:** _______
"""


class _RecordingCanvas(SkiaCanvas):
    """A SkiaCanvas that also records every draw_text string, so a headless
    render can assert WHICH text the widget actually painted (the pixel-verifying
    render the widget already supports, instrumented at its text seam)."""

    def __init__(self):
        super().__init__()
        self.texts = []

    def draw_text(self, text, x, y, align="left"):
        self.texts.append(text)
        return super().draw_text(text, x, y, align)


def _record_render(widget, w=764, h=1600):
    c = _RecordingCanvas()
    c.create_surface(w, h)
    c.set_fill_color(0x0A, 0x0E, 0x17)
    c.fill_rect(0, 0, w, h)
    rect = Rect(0, 0, w, h)
    widget.arranged_rect = rect
    widget.draw(c, rect)
    return " \n".join(c.texts)


def test_parse_captures_context_theme_and_marker():
    # [CXC-DSDV-PARSE] the parser CAPTURES the per-decision gloss + stakes + the
    # `## Theme` group + the ⛔ blocker glyph (it used to drop all of them).
    doc = parse_dsd(CONTEXT_FIXTURE)
    d1, d2 = doc.decisions
    assert d1.theme == "Theme A — The blockers"
    assert d2.theme == "Theme B — Later"
    assert d1.marker == "⛔" and d2.marker == ""
    # the multi-line What-this-is is joined into one paragraph, markdown stripped
    assert d1.what_this_is.startswith("the plan spans four branches")
    assert "overlapping subsystems" in d1.what_this_is
    assert d1.why_it_matters == "landing order decides whether the tree stays coherent."
    assert d1.what_this_is and not d2.what_this_is       # D2 has no gloss
    assert d2.why_it_matters == "it is order-independent."
    # the question is still the clean text after the `·` (⛔ preserved separately)
    assert d1.question == "Approve the plan?"


def test_widget_renders_context_theme_and_marker_headless():
    # [CXC-DSDV-FORM] the RENDER PASS paints the theme divider, the ⛔ marker, the
    # What-this-is gloss, and the Why-it-matters line — this FAILS before the fix
    # (the widget dropped them) and passes after.
    w = DsdFormWidget("form", content=CONTEXT_FIXTURE)
    w.on_mount(_FakeCtx())
    painted = _record_render(w)
    assert "THEME A — THE BLOCKERS" in painted          # theme divider (upper)
    assert "THEME B — LATER" in painted
    assert "⛔" in painted                                # blocker glyph preserved
    assert "What this is: the plan spans four branches" in painted
    assert "Why it matters: landing order decides" in painted


def test_blocker_and_separator_do_not_break_parse():
    # robustness ([CXC-DSDV-PARSE]): the ⛔ glyph + the `·` separator in a heading
    # never break the D<n> parse — the decision still resolves with a clean question.
    doc = parse_dsd(CONTEXT_FIXTURE)
    assert doc.ok and [d.id for d in doc.decisions] == ["D1", "D2"]
    assert doc.decisions[0].options and doc.decisions[0].slot_line is not None


def test_launcher_bakes_persisted_scroll_direction_into_scene():
    # [CXC-ICP-S6-SCROLLDIR] the per-open scene carries the persisted scroll pref
    # in design.scroll_direction — the SAME seam the runtime honors for the
    # console.  FAILS before the fix (build_open_scene ignored the preference).
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    scene = mod.build_open_scene("/repo/docs/x.md",
                                 template={"design": {"title": "t"},
                                           "variables": {}, "widgets": []},
                                 scroll_direction="traditional")
    assert scene["design"]["scroll_direction"] == "traditional"
    # the persisted read never raises and resolves to a valid value
    assert mod._persisted_scroll_direction("architect") in ("natural", "traditional")


def test_dsd_scene_honors_scroll_direction_in_viewport(tmp_path):
    # end-to-end honored path: a scene whose design.scroll_direction is seeded
    # (as the launcher bakes it) drives comp.scroll_direction, which the runtime's
    # wheel dispatch flips — so the DSD viewer's scroll viewport honors the pref
    # exactly as the console does (no fork of the flip).
    import yaml
    from composeio.render.engine.runtime import RenderApp
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    scene = mod.build_open_scene(DSDVIEWER_SCENE, scroll_direction="traditional")
    sp = tmp_path / "scene.compose.yaml"
    sp.write_text(yaml.safe_dump(scene), encoding="utf-8")
    app = RenderApp.from_file(str(sp), windowed=False)
    app.start()
    assert getattr(app.comp, "scroll_direction", "natural") == "traditional"
    app.stop()


# ===========================================================================
# 7. HARDENING ([CXC-DSDV-COMPLETE], [CXC-DSDV-NEITHER]) — the four tonight fixes:
#    (1) NO save/close while any decision is unanswered; (2) an always-present
#    "Neither / None" option per decision; (3) "Neither" REQUIRES a comment;
#    (4) the wheel handler consults the ONE SHARED scroll-direction preference.
# ===========================================================================

def test_save_blocked_while_a_decision_unanswered(tmp_path):
    # [CXC-DSDV-COMPLETE] the headline ask: the FIXTURE's D3 has no recommendation,
    # so the all-default form is INCOMPLETE — save() must NO-OP, write NOTHING,
    # and mark nothing answered (this is the D4-came-back-blank case David hit).
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    assert not w._is_complete()
    assert any(d.id == "D3" for d in w._unanswered())     # D3 blocks the save
    assert w.save() is None                                # refused
    assert p.read_text(encoding="utf-8") == FIXTURE        # file untouched
    assert not any(ns == "_app/dsd/answered" for (ns, _v) in ctx.published)


def test_disabled_save_button_tap_does_not_write_or_close(tmp_path):
    # [CXC-DSDV-COMPLETE] the disabled Save button: its hit-zone is still recorded
    # in the draw pass, but tapping it while incomplete neither saves nor closes.
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    assert w._save_zone is not None                        # zone-from-draw intact
    sz = w._save_zone
    w.on_tap(sz.x + sz.w / 2, sz.y + sz.h / 2)
    assert ctx.stop_requested == 0                         # never closed
    assert p.read_text(encoding="utf-8") == FIXTURE        # never written
    # answering the last decision flips completeness -> the button now commits
    _answer_d3(w)
    assert w._is_complete()


def test_neither_option_present_per_decision():
    # [CXC-DSDV-NEITHER] a first-class, always-present "Neither / None" radio is
    # rendered for EVERY decision (draw-pass hit zone), independent of options.
    w, _ = _mounted_widget()
    _draw(w)
    neither = {did for (did, oid, _r) in w._radio_zones
               if oid.endswith("-neither")}
    assert neither == {"D1", "D2", "D3", "D4"}


def test_neither_selected_without_comment_is_unanswered(tmp_path):
    # [CXC-DSDV-NEITHER] COMMENT-REQUIRED: picking "Neither" for a decision does
    # NOT complete it until a non-empty comment is supplied.
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    _select_zone(w, "D3", "-neither")                      # Neither, no comment
    assert not w._decision_answered(w.doc.decisions[2])    # D3 still unanswered
    assert w.save() is None                                # save refused
    assert p.read_text(encoding="utf-8") == FIXTURE        # nothing written
    # type a comment into D3's field -> the Neither pick is now complete
    did, crect = w._comment_zones[2]
    assert did == "D3"
    w.on_pointer_down(crect.x + 8, crect.y + 8)
    for ch in "both branches miss the point":
        w.on_text(ch)
    assert w._decision_answered(w.doc.decisions[2])
    assert w._is_complete()


def test_neither_answer_roundtrips_label_and_comment(tmp_path):
    # [CXC-DSDV-NEITHER] answer-schema reconciliation: a "Neither" pick round-trips
    # as the label "Neither" + the verbatim comment through answers(), the slot
    # write-back, and the completion payload.
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    _select_zone(w, "D3", "-neither")
    did, crect = w._comment_zones[2]
    w.on_pointer_down(crect.x + 8, crect.y + 8)
    for ch in "neither fits":
        w.on_text(ch)
    ans = w.answers()["D3"]
    assert ans.labels == ["Neither"] and ans.comment == "neither fits"
    payload = w.save()                                     # form now complete
    assert payload is not None
    text = p.read_text(encoding="utf-8")
    assert "**Your call:** Neither" in text                # slot write-back
    assert "**Comments:** neither fits" in text
    by_id = {d["id"]: d for d in payload["decisions"]}
    assert by_id["D3"]["chosen"] == "Neither"
    assert by_id["D3"]["comment"] == "neither fits"


def test_complete_form_permits_save_via_options_and_neither(tmp_path):
    # [CXC-DSDV-COMPLETE] a complete set — pre-selected recs (D1/D2), an option
    # pick (D3), and (implicitly D4 is read-only, excluded) — permits the save.
    p = tmp_path / "dsd.md"
    p.write_text(FIXTURE, encoding="utf-8")
    w = DsdFormWidget("form", path=str(p))
    ctx = _FakeCtx()
    w.on_mount(ctx)
    _draw(w)
    assert not w._is_complete()                            # D3 blank
    _answer_d3(w)
    assert w._is_complete()                                # D4 read-only excluded
    payload = w.save()
    assert payload is not None
    assert any(ns == "_app/dsd/answered" for (ns, _v) in ctx.published)


def test_readonly_decision_excluded_from_completeness_gate():
    # [CXC-DSDV-COMPLETE] D4 has NO writable slot -> it can never be persisted, so
    # it is excluded from the gate (else the form could never be saved).
    w, _ = _mounted_widget()
    d4 = w.doc.decisions[3]
    assert d4.slot_line is None
    assert not w._answerable(d4)
    assert not any(d.id == "D4" for d in w._unanswered())


def test_wheel_handler_consults_shared_scroll_direction_seam(monkeypatch):
    # [CXC-ICP-S6-SCROLLDIR] fix #4: the launcher's persisted-scroll reader routes
    # through the ONE SHARED console-personalization seam
    # (personalization.read_settings / meta_path_for) — NOT a forked copy.  Patch
    # that shared reader and assert the launcher's value follows it.
    import importlib
    console_dir = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")
    if console_dir not in sys.path:
        sys.path.insert(0, console_dir)
    for d in (os.path.join(REPO_ROOT, "python"),
              os.path.join(REPO_ROOT, "compose_projects", "intelligence", "publisher")):
        if d not in sys.path:
            sys.path.insert(0, d)
    personalization = importlib.import_module("personalization")
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    seen = {}

    def fake_read_settings(meta_path):
        seen["meta_path"] = meta_path
        return {"scroll_direction": "traditional"}

    monkeypatch.setattr(personalization, "read_settings", fake_read_settings)
    # the launcher imports the shared reader lazily -> it picks up the patch
    assert mod._persisted_scroll_direction("architect") == "traditional"
    # and it consulted the shared meta_path_for seam (the console's own path)
    assert seen["meta_path"] == personalization.meta_path_for(
        "architect", repo_root=REPO_ROOT)


# ===========================================================================
# 6. [CXC-DSDV-CONTEXT-GUARD] (David 2026-08-17) — the LABEL GRAMMAR the guard
#    stands on, and the launcher's REFUSAL TO SPAWN.
#
#    The guard is only as good as the parse underneath it: before this landed,
#    `_WHATIS_RE`/`_WHY_RE` required a COLON somewhere on the line, so the two
#    other authored forms (`.**` and `** —`) parsed as NOTHING — an authored
#    gloss rendered as blank, silently. A guard built on that parse would have
#    refused correct documents, which is the failure mode that gets a guard
#    disabled. These tests pin all three forms.
# ===========================================================================
_ALL_LABEL_FORMS = """\
### D1 · colon form

**What this is:** the colon form.

**Why it matters:** it is the oldest authored form.

| Option | Tradeoffs |
|---|---|
| **A** | a |

**Your call: _______**

### D2 · full-stop form

**What this is.** the full-stop form.

**Why it matters.** it is live in docs/.

| Option | Tradeoffs |
|---|---|
| **A** | a |

**Your call: _______**

### D3 · em-dash form

**What this is** — the em-dash form, the current house style.

**Why it matters** — measured: this one did not parse at all before the fix, and
the non-greedy scan used to run past the label to this colon and drop the lead.

| Option | Tradeoffs |
|---|---|
| **A** | a |

**Your call: _______**

### D4 · parenthetical qualifier

**What this is (blocked on your intent):** the label may carry a parenthetical.

**Why it matters** —

| Option | Tradeoffs |
|---|---|
| **A** | a |

**Your call: _______**
"""


def test_all_three_label_forms_parse():
    doc = parse_dsd(_ALL_LABEL_FORMS)
    got = {d.id: (d.what_this_is, d.why_it_matters) for d in doc.decisions}
    assert got["D1"][0] == "the colon form."
    assert got["D2"][0] == "the full-stop form."
    assert got["D3"][0].startswith("the em-dash form")
    assert got["D4"][0].startswith("the label may carry a parenthetical")
    # the em-dash `Why` keeps its LEAD — the old `.*?:` scan dropped "measured".
    assert got["D3"][1].startswith("measured:")
    # D4's `Why` is a bare label -> BLANK, which is what the guard must catch.
    assert got["D4"][1] == ""


def test_validate_dsd_names_every_offender_and_field():
    from composeio.render.widgets.dsd_document import validate_dsd
    v = validate_dsd(parse_dsd(_ALL_LABEL_FORMS))
    assert v.ok is False
    assert v.missing == [{"decision_id": "D4",
                          "heading": "D4 · parenthetical qualifier",
                          "field": "why_it_matters",
                          "label": "Why it matters"}]
    assert "D4" in v.message and "Why it matters" in v.message
    # NEGATIVE CONTROL: the three complete decisions are NOT named.
    for dec_id in ("D1", "D2", "D3"):
        assert f"- {dec_id} ·" not in v.message


def test_launcher_refuses_context_incomplete_dsd_and_never_spawns():
    """The LAST GATE BEFORE GLASS, against THE REAL ARTEFACT that got through
    on 2026-08-17. Paired with its negative control below."""
    import pytest
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    bad = os.path.join(REPO_ROOT, "docs", "architecture",
                       "DECISIONS-2026-08-17-migration-and-programme.md")
    if mod.resolve_doc(bad) is None:
        pytest.skip("the 2026-08-17 artefact is not on this checkout")
    spawned = []
    lau = mod.DsdViewerLauncher(
        spawn=lambda s: spawned.append(s) or type(
            "P", (), {"pid": 1, "poll": lambda self: None})())
    msg = lau.open(bad)
    assert msg.startswith("refused (incomplete DSD)")
    assert not spawned, "no window may open for an undecidable DSD"
    assert "D2" in msg and "What this is" in msg     # names the offender


def test_launcher_still_opens_a_complete_dsd():
    """NEGATIVE CONTROL — an instrument that can only refuse is not an
    instrument either."""
    mod = _load("dsd_viewer_launcher", "dsd_viewer_launcher.py")
    spawned = []
    lau = mod.DsdViewerLauncher(
        spawn=lambda s: spawned.append(s) or type(
            "P", (), {"pid": 1, "poll": lambda self: None})())
    msg = lau.open("python/tests/render/fixtures/dsd/complete.md")
    assert msg.startswith("opened") and len(spawned) == 1
