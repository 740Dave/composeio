"""
ComposeUX StoreBrowserWidget — the list-left NAVIGATION SHELL over a storage
service ([SPEC:CXC-LAUNCHBAR] [CXC-DOCNAV-*] / [CXC-BUSNAV-*]).

This is the shared browse widget behind BOTH the Document Storage viewer
([CXC-DOCNAV-APP], over the doc catalog / `bus://<collection>/<id>.json`) and the
Bus Storage viewer ([CXC-BUSNAV-APP], over bus_storage / `bus://assets`) — both now
faces of the ONE unified store ([SPEC:CXC-STORE]).  It is a NAVIGATION
SHELL, not a renderer: it lists a store's tree/entries, and on selecting a leaf
it hands the entry to the EXISTING doc-viewer by publishing `_app/doc/open`
([CXC-DOCNAV-VIEW] / [CXC-BUSNAV-VIEW]).  It adds NO document-rendering path of
its own — yaml/svg/png/md all render in the doc-viewer window.

Reuse-by-reference (R3 / the narrow waist):

  * the LIST idiom + tokens come from the console family (`console_theme`,
    the `projects_view`/`doc_picker` list-left rows) — NOT a new look;
  * the VIEW step is the EXISTING doc-viewer via the `_app/doc/open` intent
    the console's live-links already publish — NOT a re-implemented renderer;
  * store I/O rides the EXISTING clients (`DocStore`, `BusStorageClient`)
    read-only — NOT a new REST layer.

The store I/O is abstracted behind a small `StoreAdapter` duck-type so the two
viewers differ ONLY in their adapter (which store + address scheme), and a test
injects a fake adapter with no network (LB-C7/C8).  A blocking store call NEVER
runs on the render thread — it runs on a background thread and marshals back via
the engine update queue (the `doc_picker` idiom).

VIEW resolution ([CXC-DOCNAV-VIEW] repo-scoped guard): the doc-viewer launcher
(`compose_projects/intelligence/tools/doc_viewer_launcher.py`) refuses to open a file outside the
repo, so a selected `bus://` entry is FETCHED and cached to a
repo-scoped temp (`<repo>/tmp/storeview/…`) before its path is published.

Read-only ([CXC-DOCNAV-READONLY] / [CXC-BUSNAV-READONLY]): there is no write /
delete / upload affordance in this build — only browse + select-to-view.

Damage discipline ([CXC-R-DAMAGE]): every bus update / drill / select / scroll
calls ONLY `self.request_redraw()` — never `mark_dirty` / `add_full` / a rect.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct

DEFAULT_OPEN_NAMESPACE = "_app/doc/open"

# repo root = python/composeio/render/widgets/ -> parents[4].  The doc-viewer
# launcher computes the SAME root (compose_projects/intelligence/tools -> parents[2]); the cache
# MUST live under it so the launcher's repo-scoped path guard accepts the file.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CACHE_ROOT = _REPO_ROOT / "tmp" / "storeview"

# geometry (px)
PAD_X = 4.0
HEAD_H = 34.0
CRUMB_H = 26.0
ROW_H = 44.0
ROW_GAP = 6.0
STATUS_H = 20.0
BANNER_H = 30.0              # the degraded ("these rows are not data") banner
_SCROLL_STEP = 40.0


@dataclass
class StoreEntry:
    """One browsable thing: a drillable folder (`dir`) or a viewable leaf."""

    label: str
    kind: str                    # "dir" | "leaf"
    segments: Tuple[str, ...]    # location within the store
    address: str = ""            # bus://<coll>/<id>.json | bus://assets/<path>
    hint: str = ""               # subtitle (collection / content-type / id)


@dataclass
class BrowseListing:
    """The result of listing one location: entries + reachability.

    Three distinct states, and the distinction is load-bearing:

      * `available=True,  degraded=False` — `entries` ARE the store's contents.
      * `available=False`                 — the store could not be reached; the
        widget draws the STORE UNAVAILABLE card and shows NO rows.
      * `available=True,  degraded=True`  — the store ANSWERED but could not be
        enumerated (e.g. a server without the listing API), so `entries` are a
        CURATED SUBSTITUTE, not data.  This state is the one that used to lie:
        four seeded roots rendered exactly like four real assets.  It is now
        flagged as loudly as unreachable — banner + note-bearing status line —
        per [INSIGHT-TRUST-IS-A-CONSTRUCTED-PROPERTY] (loud refusal beats a
        plausible wrong answer)."""

    entries: List[StoreEntry] = field(default_factory=list)
    available: bool = True       # False => store unreachable (not "empty")
    note: str = ""               # an explicit operator note (e.g. an API gap)
    degraded: bool = False       # True => `entries` are a substitute, not data


# ---------------------------------------------------------------------------
# store adapters — the ONLY thing that differs between the two viewers
# ---------------------------------------------------------------------------
def _safe_name(seg: str) -> str:
    keep = [c if (c.isalnum() or c in "-_.") else "_" for c in str(seg)]
    return "".join(keep)[:80] or "_"


def _host_of(url: str) -> str:
    """`host` of an http URL, for the unreachable note only ("" when unparseable)."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


class DocStoreAdapter:
    """Browse the document catalog (`bus://<collection>/<id>.json`) read-only.

    document_store is RETIRED ([SPEC:CXC-STORE]); the catalog is now path-addressed
    JSON blobs on the ONE store (bus_storage :8420), reached through `DocStore`.
    Two levels: collections -> entries.  Reachability is probed via `status()` so an
    UNREACHABLE store is distinguished from an EMPTY one (both yield no rows through
    the swallow-and-return-None client, [CXC-DOCNAV-BROWSE] preserve-and-flag)."""

    root_label = "bus://"

    # host=None (the default) means "this process's storage URL" (COMPOSEIO_STORAGE_URL via
    # composeio.resolve). A hard-coded default host here repointed the WHOLE process's
    # storage URL on construction, because DocStore(host=...) rewrites the resolve global
    # (H1, [CXC-SCL-SCRUB-OVERLAY-PARITY]). Only a scene's explicit `host:` names a host.
    def __init__(self, host: Optional[str] = None, port: int = 8420,
                 client: Any = None, cache_root: Path = _CACHE_ROOT) -> None:
        if client is None:
            from composeio.docstore import DocStore
            client = DocStore(host=host) if host else DocStore()
        self.client = client
        self.host = host or _host_of(getattr(client, "base_url", ""))
        self.cache_root = Path(cache_root)

    def probe(self) -> bool:
        try:
            return "error" not in (self.client.status() or {"error": "x"})
        except Exception:
            return False

    def list(self, segments: Tuple[str, ...]) -> BrowseListing:
        if not self.probe():
            return BrowseListing(available=False,
                                 note=f"store unreachable ({self.host}:8420)")
        if len(segments) == 0:
            rows = self.client.list_collections() or []
            entries: List[StoreEntry] = []
            for r in rows:
                name = r.get("name") or r.get("collection") or r.get("id") \
                    if isinstance(r, dict) else str(r)
                if not name:
                    continue
                count = r.get("count") if isinstance(r, dict) else None
                entries.append(StoreEntry(
                    label=str(name), kind="dir", segments=(str(name),),
                    address=f"bus://{name}",
                    hint=(f"{count} docs" if count is not None else "collection")))
            return BrowseListing(entries=sorted(entries, key=lambda e: e.label))
        coll = segments[0]
        ids = self.client.list_docs(coll) or []
        entries = []
        for it in ids:
            did = it.get("doc_id") or it.get("id") or it.get("name") \
                if isinstance(it, dict) else str(it)
            if not did:
                continue
            entries.append(StoreEntry(
                label=str(did), kind="leaf", segments=(coll, str(did)),
                address=f"bus://{coll}/{did}.json", hint=coll))
        return BrowseListing(entries=sorted(entries, key=lambda e: e.label))

    def resolve_for_view(self, entry: StoreEntry) -> Optional[str]:
        """Fetch the doc and cache it repo-scoped as YAML for the doc-viewer.
        Returns an absolute path under the repo, or None on failure."""
        import yaml
        coll, did = entry.segments[0], entry.segments[1]
        doc = self.client.get(coll, did)
        if doc is None:
            return None
        out = self.cache_root / "docstore" / _safe_name(coll) / \
            (_safe_name(did) + ".yaml")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True)
        return str(out)


class BusStoreAdapter:
    """Browse bus_storage (`bus://assets/<path>`) read-only.

    STORE-API GAP (now CLOSED): the bus_storage server (`services/bus_storage/
    server.py`) grew `GET /api/v1/assets?prefix=<path>`, which enumerates one
    level (subdirs + files) under a prefix — so the live asset tree is now
    walkable directly from the API (`BusStorageClient.list_assets`).  The curated
    `roots:` path is RETAINED as the graceful fallback for a server without the
    endpoint (an older or not-yet-restarted bus_storage): `list_assets` returns
    None there, and `list()` reverts to the seeded roots — flagged
    `degraded=True` so the widget announces them as a substitute rather than
    rendering them as the store's contents.  A `None` result means "no endpoint
    / unreachable"; an empty list means a real empty dir."""

    root_label = "bus://assets"

    def __init__(self, host: Optional[str] = None, port: int = 8420,
                 client: Any = None, roots: Optional[List[str]] = None,
                 cache_root: Path = _CACHE_ROOT) -> None:
        if client is None:
            from composeio.storage import BusStorageClient
            if host:
                base_url = f"http://{host}:{port}"
            else:  # no host named: the process's storage URL, as DocStoreAdapter
                from composeio import resolve as _resolve
                base_url = _resolve.BUS_STORAGE_URL
            client = BusStorageClient(base_url=base_url)
        self.client = client
        self.host = host or _host_of(getattr(client, "base_url", ""))
        self.roots = list(roots or [])
        self.cache_root = Path(cache_root)
        # last version string the server reported, so the no-listing-API note
        # can NAME the stale build ("v0.1.0") instead of vaguely blaming "the
        # server" — a version in the note is what makes the redeploy actionable.
        self._server_version = ""

    def probe(self) -> bool:
        try:
            st = self.client.status() or {"error": "x"}
        except Exception:
            self._server_version = ""
            return False
        if "error" in st:
            self._server_version = ""
            return False
        self._server_version = str(st.get("version") or "")
        return True

    def _seed_entries(self) -> List[StoreEntry]:
        """The curated top-level assets (the server has no listing API)."""
        entries: List[StoreEntry] = []
        for r in self.roots:
            addr = r if r.startswith("bus://assets/") else f"bus://assets/{r}"
            path = addr[len("bus://assets/"):]
            entries.append(StoreEntry(
                label=os.path.basename(path.rstrip("/")) or path,
                kind="leaf", segments=(path,), address=addr,
                hint=os.path.dirname(path) or "asset"))
        return entries

    def list(self, segments: Tuple[str, ...]) -> BrowseListing:
        if not self.probe():
            return BrowseListing(available=False,
                                 note=f"bus_storage unreachable ({self.host}:8420)")
        rows = self.client.list_assets("/".join(segments))
        if rows is None:
            # The server ANSWERED status() but has no listing endpoint (an older
            # / un-restarted bus_storage).  Fall back to the curated roots — but
            # mark the listing DEGRADED so the widget cannot present a handful of
            # seeded entries as if they were the store's real contents.  This is
            # the exact failure that produced DEFECT-BUS-STORAGE-VIEWER-SHOWS-
            # ONLY-F993A4: four curated roots read as "the store has 4 things".
            ver = f" (v{self._server_version})" if self._server_version else ""
            if len(segments) == 0:
                note = (f"showing curated entries — this server has no listing "
                        f"API{ver}, needs redeploy" if self.roots else
                        f"this bus_storage has no listing API{ver} — redeploy "
                        f"it, or configure `roots:` to browse")
                return BrowseListing(entries=self._seed_entries(), note=note,
                                     degraded=True)
            return BrowseListing(
                entries=[], degraded=True,
                note=(f"cannot enumerate — this server has no listing API{ver}, "
                      f"needs redeploy"))
        # Live listing — build StoreEntry rows mirroring DocStoreAdapter's
        # dir-vs-leaf convention.  The server already returns dirs-first, each
        # group sorted case-insensitively, so its order is preserved here.
        entries: List[StoreEntry] = []
        for it in rows:
            if not isinstance(it, dict):
                continue
            name = it.get("name")
            if not name:
                continue
            path = it.get("path") or (
                f"{'/'.join(segments)}/{name}" if segments else str(name))
            if it.get("kind") == "dir":
                # descend-segments: the tuple the widget passes back to list()
                # (joined into the ?prefix=) to drill into this folder.
                entries.append(StoreEntry(
                    label=str(name), kind="dir",
                    segments=tuple(segments) + (str(name),),
                    hint="folder"))
            else:
                # leaf: segments[0] MUST be the full asset path so the existing
                # resolve_for_view (get_asset(entry.segments[0])) fetches it.
                entries.append(StoreEntry(
                    label=str(name), kind="leaf", segments=(str(path),),
                    address=f"bus://assets/{path}",
                    hint=os.path.dirname(path) or "asset"))
        return BrowseListing(entries=entries)

    def resolve_for_view(self, entry: StoreEntry) -> Optional[str]:
        """Fetch the asset bytes and cache them repo-scoped (extension
        preserved) for the doc-viewer.  Absolute path under the repo, or None."""
        path = entry.segments[0]
        data = self.client.get_asset(path)
        if data is None:
            return None
        safe = "/".join(_safe_name(s) for s in path.split("/"))
        out = self.cache_root / "busstore" / safe
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            fh.write(data)
        return str(out)


def make_adapter(store: str, **kw: Any) -> Any:
    if store in ("docstore", "doc", "document"):
        return DocStoreAdapter(**{k: v for k, v in kw.items()
                                  if k in ("host", "port", "client", "cache_root")})
    if store in ("busstore", "bus", "assets"):
        return BusStoreAdapter(**{k: v for k, v in kw.items()
                                  if k in ("host", "port", "client", "roots",
                                           "cache_root")})
    raise ValueError(f"unknown store kind: {store!r}")


# ---------------------------------------------------------------------------
# the widget
# ---------------------------------------------------------------------------
class StoreBrowserWidget(Widget):
    """The list-left navigation shell over a storage service; select-to-view
    hands off to the doc-viewer ([CXC-DOCNAV-APP] / [CXC-BUSNAV-APP])."""

    def __init__(self, widget_id: str, *,
                 store: str = "docstore",
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 title: str = "",
                 host: Optional[str] = None,
                 port: int = 0,
                 roots: Optional[List[str]] = None,
                 default_path: str = "",
                 adapter: Any = None,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "store_browser", **kwargs)
        self.store = str(store)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.title = title or ("Document Storage" if self.store == "docstore"
                               else "Bus Storage")
        self.interactive = True
        # adapter (injectable for tests / captures; else built from store kind)
        if adapter is None:
            kw: dict = {"host": host}
            if port:
                kw["port"] = port
            if roots:
                kw["roots"] = roots
            adapter = make_adapter(self.store, **kw)
        self._adapter = adapter
        # navigation state
        self._path: Tuple[str, ...] = tuple(
            s for s in str(default_path).split("/") if s)
        self._entries: List[StoreEntry] = []
        self._available = True
        self._degraded = False
        self._note = ""
        self._status = ""
        self._scroll = 0.0
        self._max_scroll = 0.0
        # hit geometry recorded at draw
        self._row_zones: List[Tuple[Rect, int]] = []
        self._crumb_zones: List[Tuple[Rect, int]] = []
        self._list_rect: Optional[Rect] = None

    # -- lifecycle -------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        self.refresh()

    # -- store I/O (background thread; results marshal via update queue) --------

    def _post(self, fn) -> None:
        if self._ctx is not None and hasattr(self._ctx, "schedule_update"):
            self._ctx.schedule_update(fn)
        else:
            fn()

    def refresh(self) -> None:
        self._status = "listing…"
        self.request_redraw()
        threading.Thread(target=self._list_worker,
                         args=(self._path,), daemon=True).start()

    def _list_worker(self, path: Tuple[str, ...]) -> None:
        try:
            listing = self._adapter.list(path)
        except Exception as exc:      # a store hiccup is unavailable, not a crash
            listing = BrowseListing(available=False, note=f"list failed: {exc}")
        # ignore a stale worker if the user drilled elsewhere meanwhile
        self._post(lambda: self._apply_listing(path, listing))

    def _apply_listing(self, path: Tuple[str, ...],
                       listing: BrowseListing) -> None:
        if path != self._path:
            return
        self._available = bool(listing.available)
        self._degraded = bool(getattr(listing, "degraded", False))
        self._note = listing.note or ""
        self._entries = list(listing.entries)
        self._scroll = 0.0
        if not self._available:
            self._status = "store unavailable"
        elif self._degraded:
            # NEVER "N items" here — an item count on substitute entries is the
            # sentence that misleads.  The status line carries the CAUSE instead.
            self._status = self._note or "cannot enumerate — substitute entries"
        elif not self._entries:
            self._status = "empty"
        else:
            n = len(self._entries)
            self._status = f"{n} " + ("item" if n == 1 else "items")
        self.request_redraw()

    def list_now(self) -> None:
        """Synchronous list (deterministic captures / tests): no thread."""
        self._apply_listing(self._path, self._adapter.list(self._path))

    # -- navigation ------------------------------------------------------------

    def _drill(self, entry: StoreEntry) -> None:
        self._path = tuple(entry.segments)
        self.refresh()

    def _crumb_to(self, level: int) -> None:
        new = self._path[:level]
        if new != self._path:
            self._path = new
            self.refresh()

    def _select_leaf(self, entry: StoreEntry) -> None:
        self._status = f"opening '{entry.label}'…"
        self.request_redraw()
        threading.Thread(target=self._view_worker, args=(entry,),
                         daemon=True).start()

    def _view_worker(self, entry: StoreEntry) -> None:
        try:
            abspath = self._adapter.resolve_for_view(entry)
        except Exception as exc:
            self._post(lambda: self._set_status(f"open failed: {exc}"))
            return
        if not abspath:
            self._post(lambda: self._set_status(
                f"could not fetch '{entry.label}'"))
            return
        self._post(lambda: self._publish_open(entry, abspath))

    def _publish_open(self, entry: StoreEntry, abspath: str) -> None:
        # hand off to the EXISTING doc-viewer ([CXC-DOCNAV-VIEW]); no local render
        self.publish(self.open_namespace, abspath)
        self._set_status(f"opened '{entry.label}' in doc-viewer")

    def _set_status(self, text: str) -> None:
        self._status = text
        self.request_redraw()

    # -- interaction -----------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        def _in(r: Rect) -> bool:
            return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

        for r, level in self._crumb_zones:
            if _in(r):
                self._crumb_to(level)
                return
        for r, idx in self._row_zones:
            if _in(r) and 0 <= idx < len(self._entries):
                entry = self._entries[idx]
                if entry.kind == "dir":
                    self._drill(entry)
                else:
                    self._select_leaf(entry)
                return

    # -- draw ------------------------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        self._row_zones = []
        self._crumb_zones = []
        canvas.save()
        x0, y0, W = rect.x + PAD_X, rect.y, rect.w - 2 * PAD_X

        # ── head: title + READ-ONLY badge ──
        cy = y0 + HEAD_H / 2.0 - 2.0
        canvas.set_font(ct.FONT_UI, 16.0, "bold")
        m = canvas.measure_text("Ag")
        base = cy + (m.ascent - m.descent) / 2.0
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(self.title, x0 + 4, base, "left")
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        ro = f"{self._adapter.root_label.upper()} · READ-ONLY"
        rw = ct.measure_tracked(canvas, ro, ct.TRACK_LABEL)
        ct.draw_tracked(canvas, ro, x0 + W - rw, base - 1.0, ct.TRACK_LABEL,
                        t.faint)

        # ── breadcrumb ──
        crumb_y = y0 + HEAD_H
        self._draw_breadcrumb(canvas, x0, crumb_y, W, t)

        # ── degraded banner ──
        #   drawn ABOVE the rows and stealing their space, so substitute entries
        #   can never be read as the store's contents ([INSIGHT-TRUST-IS-A-
        #   CONSTRUCTED-PROPERTY]: as loud as STORE UNAVAILABLE).
        list_y = crumb_y + CRUMB_H + 4.0
        if self._available and self._degraded:
            self._draw_degraded_banner(canvas, x0, list_y, W, t)
            list_y += BANNER_H + 4.0

        # ── list / unavailable ──
        list_h = max(80.0, rect.h - (list_y - y0) - STATUS_H - 6.0)
        self._list_rect = Rect(x0, list_y, W, list_h)
        if not self._available:
            self._draw_unavailable(canvas, x0, list_y, W, list_h, t)
        else:
            self._draw_list(canvas, x0, list_y, W, list_h, t)

        # ── status line ──
        if self._status:
            canvas.set_font(ct.FONT_MONO, 10.0, "regular")
            sm = canvas.measure_text("Ag")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(self._status, x0 + 4,
                             list_y + list_h + 4 + sm.ascent, "left")
        canvas.restore()

    def _draw_breadcrumb(self, canvas: Canvas, x: float, y: float, w: float,
                         t) -> None:
        canvas.set_font(ct.FONT_MONO, 11.0, "regular")
        bm = canvas.measure_text("Ag")
        baseline = y + CRUMB_H / 2.0 + (bm.ascent - bm.descent) / 2.0
        cx = x + 4
        # root crumb (level 0)
        root = self._adapter.root_label
        rw = canvas.measure_text(root).width
        canvas.set_fill_color(*(t.accent if self._path else t.faint))
        canvas.draw_text(root, cx, baseline, "left")
        self._crumb_zones.append((Rect(cx - 3, y, rw + 8, CRUMB_H), 0))
        cx += rw
        for level, seg in enumerate(self._path, start=1):
            canvas.set_fill_color(*t.dim)
            canvas.draw_text(" › ", cx, baseline, "left")
            cx += canvas.measure_text(" › ").width
            last = (level == len(self._path))
            sw = canvas.measure_text(seg).width
            canvas.set_fill_color(*(t.ink if last else t.accent))
            canvas.draw_text(seg, cx, baseline, "left")
            self._crumb_zones.append((Rect(cx - 3, y, sw + 6, CRUMB_H), level))
            cx += sw

    def _draw_list(self, canvas: Canvas, x: float, y: float, w: float,
                   h: float, t) -> None:
        canvas.save()
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, w, h, 8.0)
        canvas.clip_round_rect(x, y, w, h, 8.0)

        if not self._entries:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            msg = self._note or "empty"
            canvas.draw_text(msg, x + w / 2, y + 44, "center")
            canvas.restore()
            self._max_scroll = 0.0
            return

        content_h = len(self._entries) * (ROW_H + ROW_GAP)
        self._max_scroll = max(0.0, content_h - h + 8.0)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        ry = y + 4.0 - self._scroll
        for idx, entry in enumerate(self._entries):
            if ry + ROW_H > y and ry < y + h:
                self._draw_row(canvas, entry, x + 4, ry, w - 8, t)
            self._row_zones.append((Rect(x + 4, ry, w - 8, ROW_H), idx))
            ry += ROW_H + ROW_GAP
        canvas.restore()

        if content_h > h:
            track_w = 4.0
            sx = x + w - 6.0
            sy = y + 4.0
            sh = h - 8.0
            canvas.set_fill_color(*t.line)
            canvas.fill_round_rect(sx, sy, track_w, sh, 2.0)
            thumb_h = max(24.0, sh * (h / content_h))
            travel = max(0.0, sh - thumb_h)
            frac = 0.0 if self._max_scroll <= 0 else self._scroll / self._max_scroll
            canvas.set_fill_color(*t.faint)
            canvas.fill_round_rect(sx, sy + travel * frac, track_w, thumb_h, 2.0)

    def _draw_row(self, canvas: Canvas, entry: StoreEntry, x: float, y: float,
                  w: float, t) -> None:
        is_dir = entry.kind == "dir"
        canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 12)
        canvas.fill_round_rect(x, y, w, ROW_H, 8.0)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, ROW_H - 1, 8.0)

        # glyph: a folder chevron for a dir, a doc bullet for a leaf (drawn, no
        # font-fallback fork — [CXC-R-DP-1]).
        gx = x + 16.0
        gy = y + ROW_H / 2.0
        if is_dir:
            canvas.set_fill_color(*t.accent)
            canvas.fill_round_rect(gx - 6, gy - 5, 12, 10, 2.0)
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(gx - 4, gy - 3, 8, 6, 1.0)
        else:
            canvas.set_stroke_color(*t.faint)
            canvas.set_stroke_width(1.2)
            canvas.stroke_round_rect(gx - 5, gy - 6, 10, 12, 2.0)

        # label (ink) + hint (faint mono)
        tx = x + 34.0
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        nm = canvas.measure_text("Ag")
        canvas.set_fill_color(*t.ink)
        canvas.draw_text(ct.ellipsize(canvas, entry.label, w - 48.0),
                         tx, y + 17.0 + (nm.ascent - nm.descent) / 2.0, "left")
        if entry.hint:
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            hm = canvas.measure_text("Ag")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(ct.ellipsize(canvas, entry.hint, w - 48.0),
                             tx, y + 32.0 + hm.ascent - 3.0, "left")

        # right-edge affordance: "›" for drill, "view" chip for a leaf
        if is_dir:
            canvas.set_font(ct.FONT_MONO, 14.0, "regular")
            cm = canvas.measure_text("›")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text("›", x + w - 20.0,
                             gy + (cm.ascent - cm.descent) / 2.0, "left")
        else:
            canvas.set_font(ct.FONT_MONO, 9.0, "regular")
            label = "VIEW"
            lw = ct.measure_tracked(canvas, label, ct.TRACK_LABEL)
            chip_w = lw + 16.0
            chip_x = x + w - chip_w - 8.0
            canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 22)
            canvas.fill_round_rect(chip_x, gy - 9, chip_w, 18, 7.0)
            ct.draw_tracked(canvas, label, chip_x + 8.0, gy + 3.0,
                            ct.TRACK_LABEL, t.accent)

    def _draw_degraded_banner(self, canvas: Canvas, x: float, y: float,
                              w: float, t) -> None:
        """'NOT LIVE' + the reason, in warn colour, above the rows.

        The store answered but could not be enumerated, so the rows below are a
        curated SUBSTITUTE.  Before this banner existed the substitute rendered
        identically to real data — which is precisely how a 4-entry seeded
        fallback was read as "the store holds 4 things"
        (DEFECT-BUS-STORAGE-VIEWER-SHOWS-ONLY-F993A4)."""
        canvas.set_fill_color(t.warn[0], t.warn[1], t.warn[2], 28)
        canvas.fill_round_rect(x, y, w, BANNER_H, 8.0)
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        tag = "NOT LIVE"
        tw = ct.measure_tracked(canvas, tag, ct.TRACK_LABEL)
        ct.draw_tracked(canvas, tag, x + 10.0, y + BANNER_H / 2.0 + 3.5,
                        ct.TRACK_LABEL, t.warn)
        canvas.set_font(ct.FONT_UI, 11.0, "regular")
        msg = self._note or "these entries are a substitute, not the store"
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(
            ct.ellipsize(canvas, msg, w - (tw + 34.0)),
            x + tw + 22.0, y + BANNER_H / 2.0 + 4.0, "left")

    def _draw_unavailable(self, canvas: Canvas, x: float, y: float, w: float,
                          h: float, t) -> None:
        """The explicit 'store unavailable' state ([CXC-DOCNAV-BROWSE] /
        [CXC-BUSNAV-BROWSE] preserve-and-flag) — NEVER a blank pane read as
        'no documents'."""
        ct.draw_card(canvas, x, y, w, min(h, 130.0), 10.0, t)
        cx = x + w / 2.0
        canvas.set_font(ct.FONT_MONO, 12.0, "regular")
        hw = ct.measure_tracked(canvas, "STORE UNAVAILABLE", ct.TRACK_LABEL)
        ct.draw_tracked(canvas, "STORE UNAVAILABLE", cx - hw / 2.0, y + 46.0,
                        ct.TRACK_LABEL, t.warn)
        canvas.set_font(ct.FONT_UI, 12.0, "regular")
        note = self._note or "the storage service could not be reached"
        canvas.set_fill_color(*t.dim)
        canvas.draw_text(ct.ellipsize(canvas, note, w - 40.0), cx, y + 74.0,
                         "center")
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("retry: reopen this viewer", cx, y + 98.0, "center")
