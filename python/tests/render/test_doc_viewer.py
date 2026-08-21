"""
Tests for the STANDALONE doc-viewer WINDOW refactor ([DEC-DOCVIEWER-WINDOW],
David 2026-07-22): the cramped in-console doc-browser OVERLAY is replaced by a
per-doc read-only window (``compose_projects/intelligence/console/docviewer.compose.yaml``) that
``compose_projects/intelligence/tools/doc_viewer_launcher.py`` spawns off the ``_app/doc/open``
intent — several openable at once, each its own doc.

Coverage:
  * the ``markdown_viewer`` widget is a first-class scene widget (parser wiring),
    READ-ONLY;
  * the docviewer scene parses + renders a real doc read-only (headless PNG);
  * PER-WINDOW CONTENT: the launcher bakes each doc into the scene's variables so
    two opens produce two distinct scenes (no shared namespace);
  * the launcher intent parsing + repo-scoped path guard + intent->spawn +
    same-path dedupe (subprocess mocked);
  * artifact-open restored: the Away-Watch card publishes ``_app/doc/open`` with
    an artifact path on an artifact sub-row tap.
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

from composeio.render.widgets import MarkdownViewerWidget

DOCVIEWER_SCENE = os.path.join(
    REPO_ROOT, "compose_projects", "intelligence", "console", "docviewer.compose.yaml")
REAL_DOC = "bootstrap/collaboration-and-design-principles.md"


# ---------------------------------------------------------------------------
# load the launcher daemon module (lives under compose_projects/intelligence/tools, not a pkg)
# ---------------------------------------------------------------------------
def _load_launcher():
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "tools",
                        "doc_viewer_launcher.py")
    spec = importlib.util.spec_from_file_location("doc_viewer_launcher", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# markdown_viewer — first-class scene widget, read-only
# ---------------------------------------------------------------------------
def test_parser_registers_markdown_viewer():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [
        {"type": "markdown_viewer", "id": "v", "content": "# Hi\n\ntext"}]})
    widgets = list(comp.root.iter_widgets())
    v = next(w for w in widgets if isinstance(w, MarkdownViewerWidget))
    assert v.interactive is True
    assert v.content == "# Hi\n\ntext"
    # READ-ONLY by construction: no caret / no write path exposed.
    assert not hasattr(v, "insert_text") and not hasattr(v, "set_caret")


def test_markdown_viewer_load_path_is_read_only(tmp_path):
    p = tmp_path / "d.md"
    p.write_text("# Title\n\nbody", encoding="utf-8")
    before = p.read_text(encoding="utf-8")
    v = MarkdownViewerWidget("v", path=str(p))
    assert "# Title" in v.content
    # loading never writes the file back
    assert p.read_text(encoding="utf-8") == before


# ---------------------------------------------------------------------------
# docviewer scene — parses + renders a doc read-only (headless PNG proof)
# ---------------------------------------------------------------------------
def test_docviewer_scene_parses_with_markdown_viewer():
    from composeio.render.engine.parser import parse_compose_yaml
    comp = parse_compose_yaml(DOCVIEWER_SCENE)
    assert comp.width == 720 and comp.height == 900
    viewers = [w for w in comp.root.iter_widgets()
               if isinstance(w, MarkdownViewerWidget)]
    assert len(viewers) == 1
    # the default variable baked the real doc into the viewer's path
    assert viewers[0].path and viewers[0].path.endswith(
        "collaboration-and-design-principles.md")


def test_docviewer_scene_renders_headless(tmp_path):
    from composeio.render.engine.runtime import RenderApp
    out = str(tmp_path / "docviewer.png")
    app = RenderApp.from_file(DOCVIEWER_SCENE, windowed=False)
    app.start()
    app.render_headless(out)
    app.stop()
    assert os.path.exists(out) and os.path.getsize(out) > 5000


# ---------------------------------------------------------------------------
# per-window content — each window its own doc
# ---------------------------------------------------------------------------
def test_build_open_scene_bakes_distinct_docs():
    L = _load_launcher()
    tmpl = {"variables": {"doc_path": "x", "doc_name": "x"},
            "design": {"title": "Doc · $doc_name"}, "widgets": []}
    a = L.build_open_scene("/repo/docs/a.md", template=tmpl)
    b = L.build_open_scene("/repo/docs/b.md", template=tmpl)
    assert a["variables"]["doc_path"] == "/repo/docs/a.md"
    assert a["variables"]["doc_name"] == "a.md"
    assert b["variables"]["doc_path"] == "/repo/docs/b.md"
    # two opens -> two distinct scenes (no shared namespace collapses them)
    assert a["variables"] != b["variables"]
    # the template is never mutated (deep copy)
    assert tmpl["variables"]["doc_path"] == "x"


# ---------------------------------------------------------------------------
# launcher — intent parse, path guard, spawn + dedupe (subprocess mocked)
# ---------------------------------------------------------------------------
def test_launcher_intent_parsing():
    L = _load_launcher()
    assert L.parse_intent("docs/a.md") == "docs/a.md"
    assert L.parse_intent({"path": "docs/b.md"}) == "docs/b.md"
    assert L.parse_intent({"val": "docs/c.md"}) == "docs/c.md"   # bus envelope
    assert L.parse_intent({"val": {"path": "docs/d.md"}}) == "docs/d.md"
    assert L.parse_intent("") is None
    assert L.parse_intent("off") is None and L.parse_intent({"val": "none"}) is None
    assert L.parse_intent(None) is None


def test_launcher_path_guard():
    L = _load_launcher()
    assert L.resolve_doc(REAL_DOC) is not None
    assert L.resolve_doc("/etc/passwd") is None
    assert L.resolve_doc("../../../../etc/hosts") is None
    assert L.resolve_doc("docs/does-not-exist-xyz.md") is None


def test_launcher_spawn_and_dedupe():
    L = _load_launcher()

    class FakeProc:
        def __init__(self):
            self.pid = 999
            self.alive = True

        def poll(self):
            return None if self.alive else 0

    spawned = []

    def fake_spawn(scene_file):
        spawned.append(scene_file)
        return FakeProc()

    lau = L.DocViewerLauncher(spawn=fake_spawn)
    r1 = lau.open(REAL_DOC)
    assert r1.startswith("opened") and len(spawned) == 1
    # the per-open scene was written and carries THIS doc
    assert os.path.isfile(spawned[0])
    # same path while the window is alive -> deduped
    r2 = lau.open(REAL_DOC)
    assert "already open" in r2 and len(spawned) == 1
    # window closes -> reopen spawns fresh (reach through the shared tracker)
    lau._windows.live[L.resolve_doc(REAL_DOC)].alive = False
    r3 = lau.open(REAL_DOC)
    assert r3.startswith("opened") and len(spawned) == 2
    # an out-of-repo path is refused, never spawned
    assert "refused" in lau.open("/etc/passwd")
    assert len(spawned) == 2


def test_launcher_selftest_passes():
    L = _load_launcher()
    assert L._selftest() == 0


# ---------------------------------------------------------------------------
# artifact-open restored on the Away-Watch card ([DEC-DOCVIEWER-WINDOW])
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self):
        self.theme = "dark"
        self.reduced_motion = False
        self.published = []

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        self.published.append((namespace, value))


def _away_watch_payload():
    return {"ts": "now", "watches": [{
        "label": "Overnight 2026-07-22",
        "scene_path": "compose_projects/intelligence/console/nightwatch.compose.yaml",
        "started_ts": 0, "decisions": 12, "tokens": 23_700_000,
        "elapsed": 28320,
        "artifacts": [
            {"title": "Build log", "path": "docs/a.md"},
            {"title": "DRADIS gap analysis", "path": "docs/b.md"},
        ]}]}


def test_away_watch_card_publishes_doc_open_for_artifacts():
    from composeio.render.skia_backend import SkiaCanvas
    from composeio.render.widget import Rect
    from composeio.render.widgets.away_watch_view import (
        AwayWatchViewWidget, DEFAULT_OPEN_NAMESPACE, DEFAULT_LAUNCH_NAMESPACE)

    w = AwayWatchViewWidget("aw")
    ctx = _Ctx()
    w.on_mount(ctx)
    w.on_bus_state("self/away_watches", _away_watch_payload())
    c = SkiaCanvas(); c.create_surface(332, 372)
    rect = Rect(0, 0, 332, 372)
    w.arranged_rect = rect
    w.draw(c, rect)                       # records row + artifact hit zones

    # two artifact sub-rows recorded, in payload order
    assert [p for _, p in w._art_zones] == ["docs/a.md", "docs/b.md"]

    # tapping an artifact sub-row publishes _app/doc/open = that path
    r, path = w._art_zones[1]
    w.on_tap(r.x + 2, r.y + r.h / 2)
    assert (DEFAULT_OPEN_NAMESPACE, "docs/b.md") in ctx.published

    # tapping the watch row still publishes the window-relaunch intent
    ctx.published.clear()
    wr, _ = w._row_zones[0]
    w.on_tap(wr.x + 2, wr.y + wr.h / 2)
    assert any(ns == DEFAULT_LAUNCH_NAMESPACE for ns, _ in ctx.published)
