"""
Compliance tests — the two storage-viewer navigation shells (the D7 slice of
[SPEC:CXC-LAUNCHBAR]): the Document Storage viewer ([CXC-DOCNAV-*]) and the Bus
Storage viewer ([CXC-BUSNAV-*]).

Covers the machine leg of LB-C7/C8/C9:

  * LB-C7/C8 — a fake store lists in the browse pane; selecting a LEAF publishes
    `_app/doc/open` with a REPO-SCOPED path the doc-viewer launcher accepts; an
    unreachable store renders the explicit "unavailable" state (never blank).
  * LB-C9 — read/view only: neither the widget nor the scenes expose a
    write/delete/upload affordance.

Plus the wiring guards: both scenes parse, carry the resize opt-in, and declare
`_app/doc/open` so it REACHES the global (MQTT) transport (the out-of-process
doc_viewer_launcher consumes it) — never pinned scope: local.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

_PKG_ROOT = Path(__file__).resolve().parents[2]          # .../python
_REPO_ROOT = _PKG_ROOT.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from composeio.render.engine.parser import parse_compose_yaml
from composeio.render.skia_backend import SkiaCanvas
from composeio.render.widget import Rect
from composeio.render.widgets.store_browser import (
    StoreBrowserWidget, StoreEntry, BrowseListing,
    DocStoreAdapter, BusStoreAdapter, _CACHE_ROOT)

DOCSTORE_SCENE = _REPO_ROOT / "apps/compose_platform_launch_bar" / "docstore_viewer.compose.yaml"
BUSSTORE_SCENE = _REPO_ROOT / "apps/compose_platform_launch_bar" / "busstore_viewer.compose.yaml"


# ---------------------------------------------------------------------------
# fakes — the adapter duck-type + a recording ctx (no network, no thread)
# ---------------------------------------------------------------------------
class _RecordCtx:
    """Records publishes; NO schedule_update, so the widget's _post runs inline
    (deterministic — the background worker's result applies synchronously)."""

    def __init__(self):
        self.published = []

    def publish(self, namespace, value, source="widget"):
        self.published.append((namespace, value, source))

    def request_redraw(self, *_a, **_k):
        pass


class _FakeAdapter:
    """A store adapter with an in-memory tree; resolve_for_view writes a REAL
    repo-scoped cache file (so the launcher path guard can be exercised)."""

    def __init__(self, root_label, tree, reachable=True):
        self.root_label = root_label
        self._tree = tree          # {(): [StoreEntry...], (seg,): [...]}
        self.reachable = reachable
        self.resolved = []

    def probe(self):
        return self.reachable

    def list(self, segments):
        if not self.reachable:
            return BrowseListing(available=False, note="unreachable (fake)")
        return BrowseListing(entries=list(self._tree.get(tuple(segments), [])))

    def resolve_for_view(self, entry):
        out = _CACHE_ROOT / "test" / ("_".join(entry.segments) + ".yaml")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("payload: " + entry.label + "\n", encoding="utf-8")
        self.resolved.append(entry)
        return str(out)


def _docstore_fake(reachable=True):
    tree = {
        (): [StoreEntry("bsg_planets", "dir", ("bsg_planets",),
                        "doc://bsg_planets", "collection"),
             StoreEntry("configs", "dir", ("configs",),
                        "doc://configs", "collection")],
        ("bsg_planets",): [
            StoreEntry("kobol", "leaf", ("bsg_planets", "kobol"),
                       "doc://bsg_planets/kobol", "bsg_planets"),
            StoreEntry("caprica", "leaf", ("bsg_planets", "caprica"),
                       "doc://bsg_planets/caprica", "bsg_planets")],
    }
    return _FakeAdapter("doc://", tree, reachable=reachable)


def _busstore_fake(reachable=True):
    tree = {
        (): [StoreEntry("cyd-45302c.compose.yaml", "leaf",
                        ("displays/cyd-45302c.compose.yaml",),
                        "bus://assets/displays/cyd-45302c.compose.yaml",
                        "displays"),
             StoreEntry("compose_console.svg", "leaf",
                        ("icons/compose_console.svg",),
                        "bus://assets/icons/compose_console.svg", "icons")],
    }
    return _FakeAdapter("bus://assets", tree, reachable=reachable)


def _widget(store, adapter):
    w = StoreBrowserWidget("browser", store=store, adapter=adapter)
    w._ctx = _RecordCtx()
    return w


class _RecordingCanvas(SkiaCanvas):
    """A real SkiaCanvas that also records every draw_text string, so a headless
    render can assert WHICH text the widget actually painted."""

    def __init__(self, w=420, h=460):
        super().__init__()
        self.texts = []
        self.create_surface(w, h)

    def draw_text(self, text, x, y, align="left"):
        self.texts.append(text)
        return super().draw_text(text, x, y, align)


def _probe_canvas(w=420, h=460):
    return _RecordingCanvas(w, h)


# ---------------------------------------------------------------------------
# LB-C7 — Document Storage viewer: browse -> select -> _app/doc/open
# ---------------------------------------------------------------------------
def test_docstore_lists_collections():
    w = _widget("docstore", _docstore_fake())
    w.list_now()
    labels = [e.label for e in w._entries]
    assert labels == ["bsg_planets", "configs"]
    assert all(e.kind == "dir" for e in w._entries)
    assert w._available is True


def test_docstore_drill_into_collection_lists_entries():
    w = _widget("docstore", _docstore_fake())
    w.list_now()
    w._drill(w._entries[0])            # bsg_planets
    w.list_now()                       # refresh() spawns a thread; drive sync
    assert w._path == ("bsg_planets",)
    labels = sorted(e.label for e in w._entries)
    assert labels == ["caprica", "kobol"]
    assert all(e.kind == "leaf" for e in w._entries)


def test_docstore_select_entry_publishes_app_doc_open():
    adapter = _docstore_fake()
    w = _widget("docstore", adapter)
    w._path = ("bsg_planets",)
    w.list_now()
    leaf = next(e for e in w._entries if e.label == "kobol")
    w._view_worker(leaf)               # synchronous (ctx has no schedule_update)
    pubs = w._ctx.published
    assert len(pubs) == 1
    ns, path, _src = pubs[0]
    assert ns == "_app/doc/open"
    # the published path is a REPO-SCOPED file the doc-viewer launcher accepts.
    tools = _REPO_ROOT / "compose_projects" / "intelligence" / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    import doc_viewer_launcher as dvl
    assert dvl.resolve_doc(path) is not None, f"launcher would refuse {path}"


def test_docstore_unreachable_shows_unavailable_state():
    w = _widget("docstore", _docstore_fake(reachable=False))
    w.list_now()
    assert w._available is False
    assert w._entries == []
    assert w._status == "store unavailable"
    assert "unreachable" in w._note


# ---------------------------------------------------------------------------
# LB-C8 — Bus Storage viewer: same shell, same intent
# ---------------------------------------------------------------------------
def test_busstore_lists_assets():
    w = _widget("busstore", _busstore_fake())
    w.list_now()
    assert [e.label for e in w._entries] == \
        ["cyd-45302c.compose.yaml", "compose_console.svg"]
    assert w._available is True


def test_busstore_select_asset_publishes_app_doc_open():
    w = _widget("busstore", _busstore_fake())
    w.list_now()
    leaf = w._entries[0]
    w._view_worker(leaf)
    pubs = w._ctx.published
    assert len(pubs) == 1 and pubs[0][0] == "_app/doc/open"
    assert Path(pubs[0][1]).is_file()


def test_busstore_unreachable_shows_unavailable_state():
    w = _widget("busstore", _busstore_fake(reachable=False))
    w.list_now()
    assert w._available is False and w._status == "store unavailable"


# ---------------------------------------------------------------------------
# LB-C9 — read/view only: no write/delete/upload affordance
# ---------------------------------------------------------------------------
def test_widget_exposes_no_write_or_delete():
    w = StoreBrowserWidget("b", store="docstore", adapter=_docstore_fake())
    for banned in ("delete", "put", "upload", "save", "write", "remove",
                   "on_write", "on_delete"):
        assert not hasattr(w, banned), f"read-only widget exposes {banned!r}"


def test_real_adapters_never_call_a_write_method():
    # the read-only adapters use ONLY get/list/status client methods; assert the
    # write client methods are never referenced in the adapter source.
    import composeio.render.widgets.store_browser as mod
    src = Path(mod.__file__).read_text(encoding="utf-8")
    for banned in ("put(", "delete(", "put_asset", "put_binary",
                   "register_namespace", "push_config"):
        assert banned not in src, f"adapter references a write method: {banned}"


@pytest.mark.parametrize("scene", [DOCSTORE_SCENE, BUSSTORE_SCENE])
def test_scenes_have_no_write_words(scene):
    txt = scene.read_text(encoding="utf-8").lower()
    # the widget spec keys are browse-only; there is no write/delete/upload key.
    for banned in ("select_action: launch", "delete:", "upload:", "writable"):
        assert banned not in txt


# ---------------------------------------------------------------------------
# wiring guards — scenes parse, resize opt-in, intent reaches GLOBAL
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("scene,wkey", [
    (DOCSTORE_SCENE, "docstore-viewer"),
    (BUSSTORE_SCENE, "busstore-viewer"),
])
def test_scene_parses_with_resize_optin(scene, wkey):
    comp = parse_compose_yaml(str(scene))
    assert comp.resizable is True
    assert comp.min_width == 640 and comp.min_height == 480
    assert comp.persist_geometry is True
    assert comp.window_key == wkey


@pytest.mark.parametrize("scene", [DOCSTORE_SCENE, BUSSTORE_SCENE])
def test_app_doc_open_reaches_global(scene):
    # [CXC-RETRO-AWAYWATCH-OPEN-REACHES-LAUNCHER]: the out-of-process launcher
    # subscribes composeio/global/_app/doc/open — the intent must NOT be local.
    raw = yaml.safe_load(scene.read_text(encoding="utf-8"))
    decls = {n["name"]: n for n in raw.get("namespaces", [])
             if isinstance(n, dict)}
    assert "_app/doc/open" in decls
    assert decls["_app/doc/open"].get("scope") == "global"


@pytest.mark.parametrize("scene", [DOCSTORE_SCENE, BUSSTORE_SCENE])
def test_scene_declares_mqtt_transport(scene):
    raw = yaml.safe_load(scene.read_text(encoding="utf-8"))
    transports = raw.get("app", {}).get("transports", [])
    assert any(t.get("type") == "mqtt" for t in transports), \
        "scene needs an mqtt transport so --transports connects the global leg"


def test_scene_renders_headless():
    # a full standalone render (unreachable store in CI -> the unavailable
    # state, never a crash) proves the widget draws end-to-end.
    from composeio.render.engine.runtime import RenderApp
    out = str(_CACHE_ROOT / "test" / "docstore_headless.png")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    app = RenderApp.from_file(str(DOCSTORE_SCENE), windowed=False)
    app.render_headless(out)
    assert os.path.getsize(out) > 0


# ---------------------------------------------------------------------------
# the real BusStoreAdapter — the store-API gap is handled gracefully
# ---------------------------------------------------------------------------
class _FakeBusClient:
    """A BusStorageClient stand-in.  `listing` maps a prefix ("" for root) to a
    server-shaped entries list; a prefix absent from the map -> list_assets None
    (an old server without the endpoint), driving the curated-roots fallback."""

    def __init__(self, listing=None, version=""):
        self._listing = listing        # None => always None (no endpoint)
        self._version = version

    def status(self):
        st = {"service": "composeio-bus-storage"}
        if self._version:
            st["version"] = self._version
        return st

    def list_assets(self, prefix=""):
        if self._listing is None:
            return None
        return self._listing.get(prefix)   # None if prefix not present

    def get_asset(self, path):
        return b"asset:" + path.encode()


def test_real_bus_adapter_seeds_roots_when_no_endpoint():
    # An old/un-restarted server: list_assets returns None -> curated roots +
    # the preserve-and-flag note (the graceful fallback path).
    a = BusStoreAdapter(roots=["bus://assets/icons/x.svg", "fonts/y.ttf"],
                        client=_FakeBusClient(listing=None))
    listing = a.list(())
    assert [e.address for e in listing.entries] == \
        ["bus://assets/icons/x.svg", "bus://assets/fonts/y.ttf"]
    # The rows are a SUBSTITUTE and the listing must say so — this flag is what
    # stops the widget rendering them as the store's contents
    # (DEFECT-BUS-STORAGE-VIEWER-SHOWS-ONLY-F993A4).
    assert listing.degraded is True
    assert "curated" in listing.note and "listing API" in listing.note
    assert "redeploy" in listing.note


def test_real_bus_adapter_no_endpoint_note_names_the_stale_version():
    # The note must NAME the stale build, so the operator knows what to redeploy
    # rather than merely that "something is wrong".
    a = BusStoreAdapter(roots=["fonts/y.ttf"],
                        client=_FakeBusClient(listing=None, version="0.1.0"))
    listing = a.list(())
    assert listing.degraded is True
    assert "v0.1.0" in listing.note


def test_real_bus_adapter_no_endpoint_without_roots_still_degraded():
    # No curated roots configured: zero rows, but the note must still blame the
    # missing API rather than let the pane read as an EMPTY store.
    a = BusStoreAdapter(client=_FakeBusClient(listing=None, version="0.1.0"))
    listing = a.list(())
    assert listing.entries == [] and listing.degraded is True
    assert "no listing API" in listing.note and "v0.1.0" in listing.note


def test_real_bus_adapter_deep_no_endpoint_is_degraded_not_leaf():
    # Deeper than the root the old code emitted note="leaf" + an empty listing,
    # which reads as "this folder is empty".  It is NOT empty — it is unknowable.
    a = BusStoreAdapter(client=_FakeBusClient(listing=None, version="0.1.0"))
    listing = a.list(("displays",))
    assert listing.entries == []
    assert listing.degraded is True
    assert "cannot enumerate" in listing.note and "v0.1.0" in listing.note


def test_real_bus_adapter_live_listing_builds_entries():
    # The endpoint is present: list_assets returns dirs-first server entries and
    # the adapter mirrors DocStoreAdapter's dir-vs-leaf convention.
    listing_map = {
        "": [
            {"name": "displays", "path": "displays", "kind": "dir"},
            {"name": "icons", "path": "icons", "kind": "dir"},
            {"name": "readme.txt", "path": "readme.txt", "kind": "file",
             "size": 11},
        ],
        "displays": [
            {"name": "cyd.compose.yaml", "path": "displays/cyd.compose.yaml",
             "kind": "file", "size": 42},
        ],
    }
    a = BusStoreAdapter(client=_FakeBusClient(listing=listing_map))
    a.probe = lambda: True

    root = a.list(())
    assert [(e.label, e.kind) for e in root.entries] == [
        ("displays", "dir"), ("icons", "dir"), ("readme.txt", "leaf")]
    # a dir carries descend-segments (passed back to list()); no leaf address
    d = root.entries[0]
    assert d.segments == ("displays",) and d.address == ""
    # a leaf carries the full path in segments[0] + a bus://assets/<path> address
    leaf = root.entries[2]
    assert leaf.segments == ("readme.txt",)
    assert leaf.address == "bus://assets/readme.txt"

    # descend into the dir: its segments join into the ?prefix=
    sub = a.list(d.segments)
    assert len(sub.entries) == 1
    sleaf = sub.entries[0]
    assert sleaf.kind == "leaf"
    assert sleaf.segments == ("displays/cyd.compose.yaml",)
    assert sleaf.address == "bus://assets/displays/cyd.compose.yaml"
    # and the existing resolve_for_view fetches via segments[0]
    assert a.resolve_for_view(sleaf) is not None


def test_real_bus_adapter_live_empty_dir_is_not_gap():
    # A real EMPTY dir returns [] (not None) -> an empty listing, NOT the
    # curated-roots fallback, and NOT degraded: [] here IS the truth.
    a = BusStoreAdapter(roots=["fonts/y.ttf"],
                        client=_FakeBusClient(listing={"": []}))
    a.probe = lambda: True
    listing = a.list(())
    assert listing.entries == []
    assert listing.degraded is False
    assert "listing API" not in listing.note


def test_real_bus_adapter_live_listing_is_not_degraded():
    a = BusStoreAdapter(roots=["fonts/y.ttf"], client=_FakeBusClient(
        listing={"": [{"name": "icons", "path": "icons", "kind": "dir"}]}))
    a.probe = lambda: True
    assert a.list(()).degraded is False


# ---------------------------------------------------------------------------
# DEFECT-BUS-STORAGE-VIEWER-SHOWS-ONLY-F993A4 — the DEGRADED listing must
# announce itself as loudly as an unreachable store.  A server that ANSWERS but
# cannot be enumerated used to render its curated fallback rows as if they were
# the store's contents, with a plain "4 items" status underneath.  That is the
# sentence that misled a human into believing a 1.5 GB store held four things.
# ---------------------------------------------------------------------------
def _degraded_widget():
    """A bus viewer over an old (no-listing-API) server + curated roots."""
    a = BusStoreAdapter(
        roots=["displays/cyd-45302c.compose.yaml", "icons/compose_console.svg",
               "fonts/inter.ttf", "m31/scene.yaml"],
        client=_FakeBusClient(listing=None, version="0.1.0"))
    w = StoreBrowserWidget("browser", store="busstore", adapter=a)
    w._ctx = _RecordCtx()
    return w


def test_degraded_listing_flags_the_widget():
    w = _degraded_widget()
    w.list_now()
    # the rows ARE shown (graceful fallback preserved) ...
    assert len(w._entries) == 4
    # ... but the widget knows they are not data.
    assert w._available is True          # the store answered — not unreachable
    assert w._degraded is True


def test_degraded_status_line_never_reads_as_an_item_count():
    """The regression guard: '4 items' under substitute rows is the lie."""
    w = _degraded_widget()
    w.list_now()
    assert "4 items" not in w._status
    assert w._status == w._note
    assert "curated" in w._status
    assert "listing API" in w._status
    assert "v0.1.0" in w._status
    assert "redeploy" in w._status


def test_degraded_is_as_loud_as_unreachable():
    """Both failure modes must surface a non-empty operator note; neither may
    present itself as an ordinary successful listing."""
    degraded = _degraded_widget()
    degraded.list_now()
    unreachable = _widget("busstore", _busstore_fake(reachable=False))
    unreachable.list_now()
    for w in (degraded, unreachable):
        assert w._note, "a failure state must carry an operator note"
        n = len(w._entries)
        assert w._status != f"{n} " + ("item" if n == 1 else "items")


def test_healthy_listing_keeps_the_plain_item_count():
    """The loud path must not leak into the normal one."""
    w = _widget("busstore", _busstore_fake())
    w.list_now()
    assert w._degraded is False
    assert w._status == "2 items"
    assert w._note == ""


def test_degraded_banner_draws_and_shrinks_the_list():
    """The banner is real pixels above the rows, not just a status string."""
    from composeio.render.widgets.store_browser import BANNER_H

    plain = _widget("busstore", _busstore_fake())
    plain.list_now()
    degraded = _degraded_widget()
    degraded.list_now()

    rect = Rect(0, 0, 420, 460)
    for w in (plain, degraded):
        c = _probe_canvas()
        w._row_zones, w._crumb_zones = [], []
        w.draw(c, rect)

    assert plain._list_rect is not None and degraded._list_rect is not None
    # the banner pushes the list down and takes its height out of the rows
    assert degraded._list_rect.y == pytest.approx(
        plain._list_rect.y + BANNER_H + 4.0)
    assert degraded._list_rect.h < plain._list_rect.h


def test_degraded_banner_paints_the_not_live_tag():
    w = _degraded_widget()
    w.list_now()
    c = _probe_canvas()
    w.draw(c, Rect(0, 0, 420, 460))
    # draw_tracked emits one draw_text per glyph, so join with "" to recover the
    # tracked label; the note is drawn as a single string.
    painted = "".join(c.texts)
    assert "NOT LIVE" in painted
    assert "curated" in painted and "redeploy" in painted


def test_real_bus_adapter_unreachable():
    a = BusStoreAdapter(client=_FakeBusClient())
    a.probe = lambda: False
    listing = a.list(())
    assert listing.available is False
