"""
ComposeUX Focus Manager (M1)

Spec: [CXC-R-ENGINE-FOCUS] — tap focuses a focusable widget, Tab cycles
in document order, keyboard events route to the focused widget only,
and the ENGINE draws the focus ring (consistent across platforms).

Text editing itself is M2+ (no TextInput widget exists on the contract
yet); this manager is the routing/focus scaffolding those widgets mount
onto.
"""

from __future__ import annotations

from typing import List, Optional

from ..canvas import Canvas
from ..widget import Widget

_FOCUS_RING = (0x42, 0xA5, 0xF5, 220)   # ComposeUX focus blue


class FocusManager:
    def __init__(self) -> None:
        self._focusables: List[Widget] = []
        self._focused: Optional[Widget] = None

    def collect(self, root) -> None:
        """Rebuild the focus order from the widget tree (document order)."""
        self._focusables = [w for w in root.iter_widgets()
                            if getattr(w, "accepts_focus", False)]
        if self._focused not in self._focusables:
            self._set(None)

    @property
    def focused(self) -> Optional[Widget]:
        return self._focused

    def focus(self, widget: Optional[Widget]) -> None:
        if widget is self._focused:
            return
        self._set(widget)

    def _set(self, widget: Optional[Widget]) -> None:
        if self._focused is not None:
            self._focused.focused = False
            self._focused.on_blur()
        self._focused = widget
        if widget is not None:
            widget.focused = True
            widget.on_focus()

    def focus_next(self, backwards: bool = False) -> None:
        """Tab / Shift-Tab cycling in document order."""
        if not self._focusables:
            return
        if self._focused not in self._focusables:
            self._set(self._focusables[-1 if backwards else 0])
            return
        i = self._focusables.index(self._focused)
        step = -1 if backwards else 1
        self._set(self._focusables[(i + step) % len(self._focusables)])

    def handle_key(self, key: str, modifiers: int = 0) -> bool:
        """Route a key event.  Returns True if consumed."""
        if key == "Tab":
            # Per-widget TAB CAPTURE ([CXC-TERM-INPUT], [DD-TERM-TAB-CAPTURE]).  Tab is
            # normally the engine's focus-cycling key and the focused widget never sees
            # it — but a hosted terminal must forward it to its child, because Claude
            # Code's TUI uses Tab.  The opt-out is the IDENTICAL shape the focus ring
            # already uses: a getattr-guarded attribute, default False, so no existing
            # widget's behavior changes and any widget/mock lacking the attribute keeps
            # focus cycling.  A capturing widget MUST expose a release chord, or the
            # keyboard is trapped in it (the terminal's is Ctrl-Shift-Escape).
            if self._focused is not None and getattr(self._focused, "captures_tab", False):
                self._focused.on_key(key, modifiers)
                return True
            self.focus_next(backwards=bool(modifiers & 0x0003))  # shift
            return True
        if self._focused is not None:
            self._focused.on_key(key, modifiers)
            return True
        return False

    def handle_text(self, text: str) -> bool:
        """Route committed text input to the focused widget ([CXC-R-C8])."""
        if self._focused is not None and text:
            self._focused.on_text(text)
            return True
        return False

    def draw_focus_ring(self, canvas: Canvas) -> None:
        """Engine-drawn focus ring around the focused widget."""
        w = self._focused
        if w is None or w.arranged_rect is None:
            return
        # Per-widget opt-out ([CXC-R-ENGINE-FOCUS]): a widget whose own chrome
        # signals focus (the console composer's blinking caret) sets
        # focus_ring = False and the engine skips the ring.  getattr-guarded so
        # any widget/mock lacking the attr keeps the default ring.
        if not getattr(w, "focus_ring", True):
            return
        r = w.arranged_rect
        canvas.save()
        canvas.set_stroke_color(*_FOCUS_RING)
        canvas.set_stroke_width(2)
        canvas.stroke_round_rect(r.x - 3, r.y - 3, r.w + 6, r.h + 6, 6)
        canvas.restore()
