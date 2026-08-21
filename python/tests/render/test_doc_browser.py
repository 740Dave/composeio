"""
Tests for the in-app DOC-BROWSER overlay ([DEC-INAPP-MARKDOWN-VIEWER]): a
modal popup with a LEFT doc list + a RIGHT read-only markdown viewer, opened by
a doc-path publish on the OPEN seam.

Slice acceptance: idle it draws nothing + hit_test is False (taps fall
through); a self/meta projection builds the left list (role descriptor ·
principles · each dusk gift); an open publish shows it + loads the doc
read-only; a row tap re-selects; the close ✕ and a falsy publish close it.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets import DocBrowserWidget, MarkdownViewerWidget
from composeio.render.widgets import console_theme as ct
from composeio.render.widgets.doc_browser import (
    DEFAULT_OPEN_NAMESPACE, DEFAULT_DOCS_NAMESPACE)

from .conftest import surface_to_array

PRINCIPLES = "docs/collaboration-and-design-principles.md"
ROLE_DEF = ".claude/agents/composeux-architect.md"
GIFT = "bootstrap/dusk-gifts/registration-protocol.md"

META = {
    "id": "architect",
    "role_descriptor": {"path": ROLE_DEF, "why": "the role"},
    "principles": {"path": PRINCIPLES, "why": "the shared principles"},
    "dusk_gifts": [
        {"path": GIFT, "role": "keystone", "why": "the six problems"},
        {"path": "bootstrap/dusk-gifts/README.md", "role": "theory", "why": "premise"},
    ],
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


def _mounted():
    w = DocBrowserWidget("db", doc_root=REPO_ROOT)
    ctx = _Ctx()
    w.on_mount(ctx)
    w.arranged_rect = RECT
    return w, ctx


def _draw(w):
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    w.draw(c, RECT)
    return c


def teardown_function(_fn):
    ct.set_theme("dark")


# =============================================================================
# idle: draws nothing, falls through
# =============================================================================

def test_idle_draws_nothing_and_falls_through():
    w, _ = _mounted()
    assert w.visible is False
    c = _draw(w)
    # nothing painted -> flat black surface
    assert surface_to_array(c)[..., :3].std() < 1.0
    # a tap anywhere falls through (hit_test False -> not the overlay's tap)
    assert w.hit_test(600, 700) is False
    assert w._panel_rect is None


# =============================================================================
# meta builds the left-pane doc list
# =============================================================================

def test_meta_builds_the_doc_list():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_DOCS_NAMESPACE, META)
    paths = [d["path"] for d in w._docs]
    assert paths == [ROLE_DEF, PRINCIPLES, GIFT, "bootstrap/dusk-gifts/README.md"]
    labels = [d["label"] for d in w._docs]
    assert labels[0] == "Role descriptor" and labels[1] == "Principles"


# =============================================================================
# open shows + loads read-only; close hides
# =============================================================================

def test_open_shows_and_loads_the_doc():
    w, ctx = _mounted()
    w.on_bus_state(DEFAULT_DOCS_NAMESPACE, META)
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    assert w.visible is True
    assert w._selected == PRINCIPLES
    # the RIGHT pane is a REAL read-only viewer that actually loaded the file
    assert isinstance(w._viewer, MarkdownViewerWidget)
    assert w._viewer.path == os.path.join(REPO_ROOT, PRINCIPLES)
    assert len(w._viewer.content) > 100
    # draw records panel + rows + the close target + renders viewer content
    c = _draw(w)
    assert surface_to_array(c)[..., :3].std() > 3.0
    assert w._panel_rect is not None
    assert len(w._row_zones) == 4
    assert w._viewer.content_height > 0.0
    # modal capture while open
    p = w._panel_rect
    assert w.hit_test(p.x + 5, p.y + 5) is True


def test_open_a_path_not_in_manifest_prepends_it():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_DOCS_NAMESPACE, META)
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, "README.md")
    assert w._selected == "README.md"
    assert w._docs[0]["path"] == "README.md"     # prepended


def test_falsy_open_closes():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    assert w.visible is True
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, "")
    assert w.visible is False


# =============================================================================
# interaction: row select, close button, scrim dismiss
# =============================================================================

def test_row_tap_selects_and_loads():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_DOCS_NAMESPACE, META)
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    _draw(w)
    # tap the ROLE_DEF row
    zone = next(z for z in w._row_zones if z[4] == ROLE_DEF)
    x0, y0, x1, y1, _ = zone
    w.on_tap((x0 + x1) / 2.0, (y0 + y1) / 2.0)
    assert w._selected == ROLE_DEF
    assert w._viewer.path == os.path.join(REPO_ROOT, ROLE_DEF)


def test_close_button_dismisses():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    _draw(w)
    r = w._close_rect
    w.on_tap(r.x + r.w / 2.0, r.y + r.h / 2.0)
    assert w.visible is False


def test_scrim_tap_dismisses():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    _draw(w)
    # a tap well outside the centred panel (top-left corner) dismisses
    w.on_tap(3.0, 3.0)
    assert w.visible is False


def test_read_only_no_write_path():
    """Enforce 'I read, you edit': the hosted viewer is read-only (no caret,
    no editable text entry) — the whole reason this replaces the OS editor."""
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    v = w._viewer
    assert getattr(v, "focus_ring", None) is False
    assert not hasattr(v, "insert_text") and not hasattr(v, "commit")


# =============================================================================
# parser wiring — open seam + meta namespace
# =============================================================================

def test_parser_wires_open_and_meta():
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({"widgets": [{"type": "doc_browser", "id": "db"}]})
    subbed = [ns for ns, _ in comp.subscriptions]
    assert subbed.count(DEFAULT_OPEN_NAMESPACE) == 1
    assert subbed.count(DEFAULT_DOCS_NAMESPACE) == 1
    w = comp.subscriptions[0][1]
    assert isinstance(w, DocBrowserWidget)
    assert w.interactive is True


# =============================================================================
# seeded full render (glass-checkable PNG)
# =============================================================================

def test_seeded_doc_browser_render_png():
    w, _ = _mounted()
    w.on_bus_state(DEFAULT_DOCS_NAMESPACE, META)
    w.on_bus_state(DEFAULT_OPEN_NAMESPACE, PRINCIPLES)
    c = SkiaCanvas(); c.create_surface(1256, 1524)
    # a dim ground so the scrim/panel read as an overlay in the capture
    c.set_fill_color(0x07, 0x0A, 0x0F, 255)
    c.fill_rect(0, 0, 1256, 1524)
    w.draw(c, RECT)
    cap_dir = os.path.join(HERE, "captures")
    os.makedirs(cap_dir, exist_ok=True)
    out = os.path.join(cap_dir, "doc_browser.png")
    c.save_png(out)
    assert os.path.exists(out) and os.path.getsize(out) > 5000
