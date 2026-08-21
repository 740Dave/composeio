"""
ComposeUX ProjectsViewWidget — the Projects-tab surface (the STYLE GUIDE
previs ``docs/intelligence-console/previs/config-and-projects-full.html``,
the ``#prj`` page): a grouped project roster (LEFT) whose select swaps a
per-project structure detail (RIGHT).

  * LEFT — the roster GROUPED BY FAMILY (``ComposeApps`` · ``ComposeBus
    Services`` · ``Reactive Widgets`` — the registry ``family`` field, order
    fixed).  Each row carries: a status dot (active/parked/planned), the
    project NAME, the 3-state **git indicator** (own-repo · monorepo ·
    no-git — visually distinct by both colour AND shape: a solid disc for a
    real repo, a hollow ring for no-git; azure=own, violet=monorepo), a
    **flag** when the decision-LTS is missing (loud warn triangle) or the
    project is otherwise non-compliant (a fainter structure-gap dot), and the
    root path (mono, faint).

  * RIGHT — the select→swap detail card: the selected project's record
    (summary · type/family · root · status · git state + remote · specs ·
    decisions · artifacts · readme) with each STANDARD item shown
    present/shared (ok) or **flagged missing** (warn) exactly as the
    validator reports it, then the raw ``missing[]`` gap list.  The
    decision-LTS is a FIRST-CLASS signal: when present, an **Open ▸** chip
    publishes ``open_namespace`` (the in-app doc-browser overlay opens it
    READ-ONLY — reuse-by-reference, [DEC-INAPP-MARKDOWN-VIEWER]); when
    absent, it is flagged, never a dead link.

DATA-DRIVEN off ONE namespace: ``self/projects`` — the ``project_validator``
daemon's projection ({name, type, family, path/root, git{state,remote},
has_decision_file, missing[], compliant, root_exists, …}).  The widget renders
that payload verbatim and NEVER re-computes structure (the split of §II.8:
deterministic code owns the perceive, this widget owns the present).

Damage discipline ([CXC-R-DAMAGE]): the bus update and every select/scroll
interaction call ``self.request_redraw()`` ONLY — damage stays this widget's
own rect.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Close, LineTo, MoveTo, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from .chat_thread import wrap_text
from .deliverable_card import _coerce, _open_tri

DEFAULT_NAMESPACE = "self/projects"
DEFAULT_OPEN_NAMESPACE = "_app/doc/open"

# the registry `family` groups, in fixed presentation order, each with the
# previs sub-line.
FAMILY_ORDER: List[Tuple[str, str]] = [
    ("ComposeApps", "GUI apps on the render engine"),
    ("ComposeBus Services", "bus-native peers / services"),
    ("Reactive Widgets", "deployed to hardware / displays"),
]

# the previs `--violet` (git-monorepo hue) — not a semantic STATE token, so it
# lives here, not in the theme table.
VIOLET = (0x9A, 0x86, 0xFF, 255)

# ── geometry (px) ──
PAD_X = 4.0
COL_GAP = 20.0
HEAD_H = 34.0               # tab head: dot · title · sub-line
GROUP_H = 30.0             # family header (name + sub-line)
ROW_H = 46.0               # project row (name line + path line)
ROW_GAP = 7.0
GROUP_GAP = 14.0
_SCROLL_STEP = 40.0
CELL_PAD = 12.0


def _warn_tri(canvas: Canvas, cx: float, cy: float, s: float, color) -> None:
    """A small filled warning triangle (Plex has no U+26A0 — draw the path,
    [CXC-R-DP-1] no font-fallback forks)."""
    canvas.set_fill_color(*color)
    canvas.fill_path([MoveTo(cx, cy - s), LineTo(cx - s * 0.9, cy + s * 0.72),
                      LineTo(cx + s * 0.9, cy + s * 0.72), Close()])


class ProjectsViewWidget(Widget):
    """The Projects-tab roster + select→swap detail (the previs `#prj`
    page)."""

    def __init__(self, widget_id: str, *,
                 namespace: str = DEFAULT_NAMESPACE,
                 open_namespace: str = DEFAULT_OPEN_NAMESPACE,
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "projects_view", **kwargs)
        self.namespace = str(namespace or DEFAULT_NAMESPACE)
        self.open_namespace = str(open_namespace or DEFAULT_OPEN_NAMESPACE)
        self.interactive = True
        self.paint_overflow = ct.SHADOW_PAINT_OVERFLOW
        # payload state (the validator contract)
        self._projects: List[Dict[str, Any]] = []
        self._ts: str = ""
        # view state
        self._selected: Optional[str] = None      # slug
        self._scroll = 0.0
        self._max_scroll = 0.0
        # hit geometry recorded at draw
        self._row_zones: List[Tuple[Rect, str]] = []
        self._open_rect: Optional[Rect] = None
        self._open_path: str = ""
        self._list_rect: Optional[Rect] = None

    # -- bus wiring ------------------------------------------------------------

    def on_bus_state(self, namespace: str, value: Any) -> None:
        payload = _coerce(value)
        if not isinstance(payload, dict):
            return
        rows = payload.get("projects")
        if isinstance(rows, list):
            self._projects = [r for r in rows if isinstance(r, dict)]
        self._ts = str(payload.get("ts", self._ts))
        # keep a valid selection (default to the first project)
        slugs = [str(p.get("slug", "")) for p in self._projects]
        if self._selected not in slugs:
            self._selected = slugs[0] if slugs else None
        self.request_redraw()                     # [CXC-R-DAMAGE] scoped only

    # -- derivation (pure) -----------------------------------------------------

    def grouped(self) -> List[Tuple[str, str, List[Dict[str, Any]]]]:
        """[(family, subtitle, [projects])] in FAMILY_ORDER, then any extra
        families present in the data (schema-tolerant, appended)."""
        out: List[Tuple[str, str, List[Dict[str, Any]]]] = []
        seen = set()
        for fam, sub in FAMILY_ORDER:
            members = [p for p in self._projects if p.get("family") == fam]
            out.append((fam, sub, members))
            seen.add(fam)
        extras = sorted({str(p.get("family", "") or "—")
                         for p in self._projects
                         if p.get("family") not in seen})
        for fam in extras:
            out.append((fam, "",
                        [p for p in self._projects
                         if str(p.get("family", "") or "—") == fam]))
        return out

    def selected_project(self) -> Optional[Dict[str, Any]]:
        for p in self._projects:
            if str(p.get("slug", "")) == self._selected:
                return p
        return None

    @staticmethod
    def _status_color(status: str, t) -> Tuple[int, int, int, int]:
        s = str(status or "").lower()
        if s == "active":
            return t.ok
        if s in ("parked", "paused", "blocked"):
            return t.warn
        return t.faint             # planned / unknown

    @staticmethod
    def _git_style(state: str, t) -> Tuple[str, Tuple[int, int, int, int], bool]:
        """(label, colour, filled) for the 3-state git indicator — colour AND
        shape distinguish (a hollow ring reads no-git even in greyscale)."""
        s = str(state or "").lower()
        if s == "own-repo":
            return "own-repo", t.accent, True
        if s == "monorepo":
            return "monorepo", VIOLET, True
        return "no git", t.faint, False       # no-git (hollow)

    # -- layout / lifecycle ----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)

    # -- interaction -----------------------------------------------------------

    def on_scroll(self, dx: float, dy: float) -> None:
        if self._list_rect is None:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll,
                                    self._scroll - dy * _SCROLL_STEP))
        if self._scroll != before:
            self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        def _in(r: Rect) -> bool:
            return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h

        # Open decision-LTS ▸ (the doc-browser overlay seam)
        if self._open_rect and self._open_path and _in(self._open_rect):
            self.publish(self.open_namespace, self._open_path)
            return
        for r, slug in self._row_zones:
            if _in(r):
                if slug != self._selected:
                    self._selected = slug
                    self.request_redraw()
                return

    # -- draw ------------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        x0, y0, W = rect.x + PAD_X, rect.y, rect.w - 2 * PAD_X

        # ── head: dot · Projects · sub-line ──
        cy = y0 + HEAD_H / 2.0 - 2.0
        ct.draw_status_dot(canvas, x0 + 5, cy, 4.5, t.accent,
                           halo=t.accent_dim, halo_w=4.0)
        canvas.set_font(ct.FONT_UI, 17.0, "bold")
        m = canvas.measure_text("Ag")
        base = cy + (m.ascent - m.descent) / 2.0
        canvas.set_fill_color(*t.accent)
        canvas.draw_text("Projects", x0 + 18, base, "left")
        hx = x0 + 18 + canvas.measure_text("Projects").width
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text("THE COMPOSEPLATFORM TREE · GROUPED BY FAMILY · "
                         "SELF/PROJECTS", hx + 14, base - 1.0, "left")

        # ── two columns ──
        col_y = y0 + HEAD_H + 8.0
        col_h = max(120.0, rect.h - (col_y - y0) - 6.0)
        left_w = W * 0.46
        right_x = x0 + left_w + COL_GAP
        right_w = W - left_w - COL_GAP
        self._list_rect = Rect(x0, col_y, left_w, col_h)

        self._draw_list(canvas, x0, col_y, left_w, col_h, t)
        self._draw_detail(canvas, right_x, col_y, right_w, col_h, t)

        canvas.restore()

    # -- LEFT: the grouped roster ---------------------------------------------

    def _draw_list(self, canvas: Canvas, x: float, y: float, w: float,
                   h: float, t) -> None:
        self._row_zones = []
        canvas.save()
        canvas.clip_rect(x, y, w, h)

        if not self._projects:
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_MONO, 10.5, "regular")
            canvas.draw_text("awaiting self/projects…", x + w / 2, y + 40,
                             "center")
            canvas.restore()
            self._max_scroll = 0.0
            return

        # measure total content height first (for scroll clamp)
        content_h = 0.0
        for _fam, sub, members in self.grouped():
            if not members:
                continue
            content_h += GROUP_H + len(members) * (ROW_H + ROW_GAP) + GROUP_GAP
        self._max_scroll = max(0.0, content_h - h)
        self._scroll = max(0.0, min(self._scroll, self._max_scroll))

        ry = y - self._scroll
        for fam, sub, members in self.grouped():
            if not members:
                continue
            # group header — azure mono uppercase + faint sub-line
            if ry + GROUP_H > y and ry < y + h:
                canvas.set_font(ct.FONT_MONO, 10.5, "regular")
                gm = canvas.measure_text("Ag")
                gb = ry + GROUP_H / 2.0 + (gm.ascent - gm.descent) / 2.0 + 2.0
                gw = ct.draw_tracked(canvas, fam.upper(), x, gb,
                                     ct.TRACK_LABEL, t.accent)
                if sub:
                    canvas.set_fill_color(*t.faint)
                    canvas.set_font(ct.FONT_MONO, 9.5, "regular")
                    canvas.draw_text("— " + sub, x + gw + 12.0, gb, "left")
            ry += GROUP_H
            for p in members:
                if ry + ROW_H > y and ry < y + h:
                    self._draw_row(canvas, p, x, ry, w, ROW_H, t)
                self._row_zones.append(
                    (Rect(x, ry, w, ROW_H), str(p.get("slug", ""))))
                ry += ROW_H + ROW_GAP
            ry += GROUP_GAP
        canvas.restore()

        # scrollbar (chat_thread idiom)
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

    def _draw_row(self, canvas: Canvas, p: Dict[str, Any], x: float, y: float,
                  w: float, hgt: float, t) -> None:
        selected = str(p.get("slug", "")) == self._selected
        # card chrome (selected = accent-tinted + accent border)
        if selected:
            canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 18)
            canvas.fill_round_rect(x, y, w, hgt, 9.0)
            canvas.set_stroke_color(*t.accent_line)
        else:
            canvas.set_fill_color(*t.panel3)
            canvas.fill_round_rect(x, y, w, hgt, 9.0)
            canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, w - 1, hgt - 1, 9.0)

        # ── line 1: status dot · name (+ flag) · git badge (right) ──
        dot_x = x + 14.0
        line1 = y + 17.0
        ct.draw_status_dot(canvas, dot_x, line1, 3.5,
                           self._status_color(p.get("status", ""), t))

        # git badge (right-aligned)
        state = str((p.get("git") or {}).get("state", "no-git"))
        g_label, g_col, g_filled = self._git_style(state, t)
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        gm = canvas.measure_text(g_label)
        badge_w = 13.0 + gm.width
        bx = x + w - 12.0 - badge_w
        gcy = line1
        if g_filled:
            canvas.set_fill_color(*g_col)
            canvas.fill_circle(bx + 4.0, gcy, 3.2)
        else:
            canvas.set_stroke_color(*g_col)
            canvas.set_stroke_width(1.2)
            canvas.stroke_circle(bx + 4.0, gcy, 3.0)
        canvas.set_fill_color(*g_col)
        canvas.draw_text(g_label, bx + 12.0,
                         gcy + (gm.ascent - gm.descent) / 2.0, "left")

        # name (ink; accent when selected) — flag sits right after it
        name_x = dot_x + 12.0
        canvas.set_font(ct.FONT_UI, 13.0, "bold")
        nm = canvas.measure_text("Ag")
        name = ct.ellipsize(canvas, str(p.get("name", "?")),
                            bx - name_x - 24.0)
        canvas.set_fill_color(*(t.accent if selected else t.ink))
        canvas.draw_text(name, name_x,
                         line1 + (nm.ascent - nm.descent) / 2.0, "left")
        flag_x = name_x + canvas.measure_text(name).width + 9.0

        # the flag: missing decision-LTS is the LOUD nag (warn triangle); a
        # merely-non-compliant project gets a fainter structure-gap dot.
        if not p.get("has_decision_file", False):
            _warn_tri(canvas, flag_x + 4.0, line1, 5.0, t.warn)
        elif not p.get("compliant", True):
            canvas.set_fill_color(t.warn[0], t.warn[1], t.warn[2], 150)
            canvas.fill_circle(flag_x + 4.0, line1, 3.0)

        # ── line 2: root path (mono faint) ──
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        pm = canvas.measure_text("Ag")
        path = ct.ellipsize(canvas, str(p.get("root", "")), w - (name_x - x) - 16.0)
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(path, name_x, y + 34.0 + pm.ascent - 3.0, "left")

    # -- RIGHT: the select→swap detail ----------------------------------------

    def _draw_detail(self, canvas: Canvas, x: float, y: float, w: float,
                     h: float, t) -> None:
        self._open_rect = None
        self._open_path = ""
        p = self.selected_project()

        if p is None:
            ct.draw_card(canvas, x, y, w, min(h, 76.0), 12.0, t)
            canvas.set_fill_color(*t.faint)
            canvas.set_font(ct.FONT_UI, 13.0, "regular")
            canvas.draw_text("Select a project.", x + w / 2, y + 44, "center")
            return

        # two passes over the SAME layout code (paint=False measures the
        # content bottom so the card fits its content — the previs shows a snug
        # card, not a full-height panel), then paint the card + content.
        content_bottom = self._detail_content(canvas, p, x, y, w, t, paint=False)
        card_h = min(h, (content_bottom - y) + 14.0)
        ct.draw_card(canvas, x, y, w, card_h, 12.0, t)
        canvas.save()
        canvas.clip_round_rect(x, y, w, card_h, 12.0)
        self._detail_content(canvas, p, x, y, w, t, paint=True)
        canvas.restore()

    def _detail_content(self, canvas: Canvas, p: Dict[str, Any], x: float,
                        y: float, w: float, t, *, paint: bool) -> float:
        """Draw (paint=True) or measure (paint=False) the detail card's
        content; returns the content bottom y.  The ry math depends ONLY on
        font metrics + wrap_text, so both passes agree exactly."""
        pad = 16.0
        ix = x + pad
        iw = w - 2 * pad

        # header: name · status chip · git badge
        canvas.set_font(ct.FONT_UI, 16.0, "bold")
        hm = canvas.measure_text("Ag")
        hy = y + 16.0
        hbase = hy + hm.ascent
        name = ct.ellipsize(canvas, str(p.get("name", "?")), iw * 0.56)
        if paint:
            canvas.set_fill_color(*t.ink)
            canvas.draw_text(name, ix, hbase, "left")
        cx = ix + canvas.measure_text(name).width + 12.0

        status = str(p.get("status", "") or "—")
        st_col = self._status_color(status, t)
        canvas.set_font(ct.FONT_MONO, 9.0, "regular")
        sm = canvas.measure_text(status.lower())
        if paint:
            chip_w = sm.width + 18.0
            chip_h = 18.0
            chip_y = hy + (hm.ascent + hm.descent - chip_h) / 2.0
            canvas.set_fill_color(st_col[0], st_col[1], st_col[2], 30)
            canvas.fill_round_rect(cx, chip_y, chip_w, chip_h, 9.0)
            canvas.set_stroke_color(st_col[0], st_col[1], st_col[2], 90)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(cx + 0.5, chip_y + 0.5, chip_w - 1,
                                     chip_h - 1, 9.0)
            canvas.set_fill_color(*st_col)
            canvas.draw_text(status.lower(), cx + 9.0,
                             chip_y + chip_h / 2.0 + (sm.ascent - sm.descent) / 2.0,
                             "left")

        # git badge (top-right of header)
        state = str((p.get("git") or {}).get("state", "no-git"))
        g_label, g_col, g_filled = self._git_style(state, t)
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        gm = canvas.measure_text(g_label)
        if paint:
            gbx = x + w - pad - gm.width - 15.0
            if g_filled:
                canvas.set_fill_color(*g_col)
                canvas.fill_circle(gbx + 4.0, hbase - hm.ascent / 2.0 + 1.0, 3.4)
            else:
                canvas.set_stroke_color(*g_col)
                canvas.set_stroke_width(1.3)
                canvas.stroke_circle(gbx + 4.0, hbase - hm.ascent / 2.0 + 1.0, 3.2)
            canvas.set_fill_color(*g_col)
            canvas.draw_text(g_label, gbx + 13.0, hbase - 2.0, "left")

        divider = hy + hm.ascent + hm.descent + 12.0
        if paint:
            canvas.set_stroke_color(*t.line)
            canvas.set_stroke_width(1.0)
            canvas.draw_line(x, divider, x + w, divider)

        # ── body rows ──
        ry = divider + 12.0
        git = p.get("git") or {}
        remote = git.get("remote")
        missing = p.get("missing") or []
        missing_txt = " ".join(str(m) for m in missing)
        has_decision = bool(p.get("has_decision_file", False))
        compliant = bool(p.get("compliant", False))

        def _missing_named(token: str) -> bool:
            return token in missing_txt

        ry = self._detail_row(canvas, "summary", ix, ry, iw, t, paint=paint,
                              wrap=True, value=str(p.get("summary", "") or "—"))
        ry = self._detail_kv(canvas, "type", ix, ry, iw, t, paint=paint,
                             value=f"{p.get('type', '—')}  ·  {p.get('family', '—')}")
        ry = self._detail_kv(canvas, "root", ix, ry, iw, t, paint=paint,
                             value=str(p.get("root", "—")), mono=True,
                             color=(t.crit if not p.get("root_exists", True)
                                    else t.accent))
        if p.get("parent"):
            ry = self._detail_kv(canvas, "parent", ix, ry, iw, t, paint=paint,
                                 value=str(p.get("parent")), mono=True)
        gtxt = state + (("  ·  " + str(remote)) if remote else "  ·  no remote")
        ry = self._detail_kv(canvas, "git", ix, ry, iw, t, paint=paint,
                             value=gtxt, mono=True, color=g_col)

        ry = self._flag_row(canvas, "specs", ix, ry, iw, t, paint=paint,
                            present=not _missing_named("specs/"), ok_word="present")
        ry = self._decisions_row(canvas, ix, ry, iw, t, p, has_decision,
                                 paint=paint)
        ry = self._flag_row(canvas, "artifacts", ix, ry, iw, t, paint=paint,
                            present=not _missing_named("artifact ledger"),
                            ok_word="shared")
        ry = self._flag_row(canvas, "readme", ix, ry, iw, t, paint=paint,
                            present=not _missing_named("README.md"),
                            ok_word="present")

        # compliance summary + the raw gap list
        ry += 6.0
        if paint:
            canvas.set_stroke_color(t.line[0], t.line[1], t.line[2], 160)
            canvas.set_stroke_width(1.0)
            canvas.draw_line(ix, ry, ix + iw, ry)
        ry += 12.0
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        cm = canvas.measure_text("Ag")
        cbase = ry + cm.ascent
        if compliant:
            if paint:
                ct.draw_status_dot(canvas, ix + 4, ry + cm.ascent / 2.0, 3.0, t.ok)
                canvas.set_fill_color(*t.ok)
                canvas.draw_text("structure complete — no gaps", ix + 14, cbase,
                                 "left")
            ry += 20.0
        else:
            if paint:
                _warn_tri(canvas, ix + 5, ry + cm.ascent / 2.0, 4.5, t.warn)
                canvas.set_fill_color(*t.warn)
                canvas.draw_text(f"{len(missing)} structure gap(s) — flagged, "
                                 f"not errors:", ix + 14, cbase, "left")
            ry += 20.0
            canvas.set_font(ct.FONT_MONO, 9.5, "regular")
            for mline in missing:
                lm = canvas.measure_text("Ag")
                for wl in wrap_text(canvas, "· " + str(mline), iw - 12.0):
                    if paint:
                        canvas.set_fill_color(*t.dim)
                        canvas.draw_text(wl, ix + 10, ry + lm.ascent, "left")
                    ry += lm.ascent + lm.descent + 3.0
                ry += 1.0
        return ry

    # -- detail-row helpers ----------------------------------------------------

    def _kk(self, canvas: Canvas, label: str, x: float, ry: float, t,
            paint: bool) -> None:
        canvas.set_font(ct.FONT_MONO, 9.5, "regular")
        km = canvas.measure_text("Ag")
        if paint:
            canvas.set_fill_color(*t.faint)
            canvas.draw_text(label.upper(), x, ry + km.ascent, "left")

    _KK_W = 96.0

    def _detail_kv(self, canvas: Canvas, label: str, x: float, ry: float,
                   w: float, t, *, value: str, mono: bool = False,
                   color=None, paint: bool = True) -> float:
        self._kk(canvas, label, x, ry, t, paint)
        vx = x + self._KK_W
        vw = w - self._KK_W
        canvas.set_font(ct.FONT_MONO if mono else ct.FONT_UI,
                        11.0 if mono else 12.5, "regular")
        vm = canvas.measure_text("Ag")
        if paint:
            canvas.set_fill_color(*(color or t.dim))
            canvas.draw_text(ct.ellipsize(canvas, value, vw), vx,
                             ry + vm.ascent, "left")
        return ry + vm.ascent + vm.descent + 9.0

    def _detail_row(self, canvas: Canvas, label: str, x: float, ry: float,
                    w: float, t, *, value: str, wrap: bool = False,
                    paint: bool = True) -> float:
        self._kk(canvas, label, x, ry, t, paint)
        vx = x + self._KK_W
        vw = w - self._KK_W
        canvas.set_font(ct.FONT_UI, 12.5, "regular")
        vm = canvas.measure_text("Ag")
        lines = wrap_text(canvas, value, vw) if wrap else [value]
        yy = ry + vm.ascent
        if paint:
            canvas.set_fill_color(*t.dim)
        for ln in lines[:4]:
            if paint:
                canvas.draw_text(ln, vx, yy, "left")
            yy += vm.ascent + vm.descent + 2.0
        return yy + 4.0

    def _flag_row(self, canvas: Canvas, label: str, x: float, ry: float,
                  w: float, t, *, present: bool, ok_word: str,
                  paint: bool = True) -> float:
        self._kk(canvas, label, x, ry, t, paint)
        vx = x + self._KK_W
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        vm = canvas.measure_text("Ag")
        vbase = ry + vm.ascent
        if paint:
            if present:
                canvas.set_fill_color(*t.ok)
                canvas.draw_text(ok_word.upper(), vx, vbase, "left")
            else:
                _warn_tri(canvas, vx + 4.0, ry + vm.ascent / 2.0 + 1.0, 4.0, t.warn)
                canvas.set_fill_color(*t.warn)
                canvas.draw_text("— missing —", vx + 13.0, vbase, "left")
        return ry + vm.ascent + vm.descent + 9.0

    def _decisions_row(self, canvas: Canvas, x: float, ry: float, w: float,
                       t, p: Dict[str, Any], has_decision: bool, *,
                       paint: bool = True) -> float:
        self._kk(canvas, "decisions", x, ry, t, paint)
        vx = x + self._KK_W
        canvas.set_font(ct.FONT_MONO, 10.0, "regular")
        vm = canvas.measure_text("Ag")
        vbase = ry + vm.ascent
        if has_decision:
            path = str(p.get("decision_lts", "") or "")
            label = "decision-LTS"
            op = "Open"
            om = canvas.measure_text(op)          # (mono 10 already set)
            tri_w, tri_gap = 4.5, 5.0
            ow = om.width + tri_gap + tri_w + 22.0
            oh = 22.0
            ox = x + w - ow
            oy = ry - 2.0
            if paint:
                canvas.set_fill_color(*t.accent)
                canvas.draw_text(label, vx, vbase, "left")
                canvas.set_fill_color(*t.ok)
                canvas.draw_text("PRESENT", vx + canvas.measure_text(label).width
                                 + 10.0, vbase, "left")
                ocy = oy + oh / 2.0
                canvas.set_fill_color(t.accent[0], t.accent[1], t.accent[2], 18)
                canvas.fill_round_rect(ox, oy, ow, oh, 7.0)
                canvas.set_stroke_color(t.accent[0], t.accent[1], t.accent[2], 71)
                canvas.set_stroke_width(1.0)
                canvas.stroke_round_rect(ox + 0.5, oy + 0.5, ow - 1, oh - 1, 7.0)
                canvas.set_fill_color(*t.accent)
                canvas.draw_text(op, ox + 11.0,
                                 ocy + (om.ascent - om.descent) / 2.0, "left")
                _open_tri(canvas, ox + 11.0 + om.width + tri_gap, ocy, tri_w,
                          7.0, t.accent)
                # hit target recorded only on the real paint pass
                self._open_rect = Rect(ox - 4, oy - 4, ow + 8, oh + 8)
                self._open_path = path
        else:
            flag_txt = "— no decision-LTS yet —"
            if paint:
                _warn_tri(canvas, vx + 4.0, ry + vm.ascent / 2.0 + 1.0, 4.0, t.warn)
                canvas.set_fill_color(*t.warn)
                canvas.draw_text(flag_txt, vx + 13.0, vbase, "left")
                # measure the flag at ITS OWN 10px font before shrinking, else
                # the hint overlaps it (glass defect caught 2026-07-22).
                flag_w = canvas.measure_text(flag_txt).width
                canvas.set_fill_color(*t.faint)
                canvas.set_font(ct.FONT_MONO, 9.0, "regular")
                canvas.draw_text("point-forward: created on first decision",
                                 vx + 13.0 + flag_w + 12.0, vbase, "left")
        return ry + vm.ascent + vm.descent + 11.0
