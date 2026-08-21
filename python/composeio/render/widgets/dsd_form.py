"""
ComposeUX DsdFormWidget — a Decision-Support-Doc rendered as an INTERACTIVE
FORM ([SPEC:CXC-DSDV] [CXC-DSDV-FORM], [CXC-DSDV-PRESELECT], [CXC-DSDV-COMMENTS],
[CXC-DSDV-SAVE]).

One control-group per parsed ``### D<n>`` decision: the question as the group
label, a SINGLE-CHOICE radio per option (its label + tradeoffs beside it) with
the architect's recommendation PRE-SELECTED and tagged "recommended", and a
per-decision editable "Additional Comments" field (>= 3 rows).  On SAVE-ON-CLOSE
the widget writes each chosen option + comment BACK into that decision's
``Your call:`` slot and publishes the ``_app/dsd/answered`` completion event.

EXTENDS the input plane, forks nothing ([CXC-DSDV-CONTRACT]):
  * the RADIO is the ``CheckboxWidget`` idiom generalized to one-of-N — the
    group binds one answer whose value is the selected option id (a checkbox is
    binary; a radio group is one-of-N over the same select+redraw machinery),
    drawn here as a filled dot;
  * the COMMENT field is a real ``TextInputWidget`` in its multi-line console
    chrome (``chrome="console"``, ``min_lines=3``) — engine-native editing
    (focus, caret, keyboard, selection/clipboard), not a read label.  This
    widget is the engine-focused leaf; it ROUTES on_key/on_text/on_pan to the
    active comment field (which never appears in the engine's focus order), and
    each field's own caret-blink clock drives its sliver damage.

ONE MEASUREMENT PATH ([CXC-R-DRAW-FONT]): ``_build_layout`` wraps + measures
every group ONCE (cached by rev/width/theme); ``draw`` paints exactly those
positions and, in the SAME pass, records the radio / comment HIT ZONES the
pointer path reads — so a control is clickable precisely where it is drawn
([CXC-DSDV-FORM] HIT ZONES FROM THE DRAW PASS).  Damage on any interaction stays
this widget's rect ([CXC-R-DAMAGE]): interactions call ``self.request_redraw()``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field as dfield
from typing import Any, Dict, List, Optional, Tuple

from ..canvas import Canvas, Size
from ..widget import Constraints, Rect, Widget
from . import console_theme as ct
from . import style
from .text_input import TextInputWidget
from .dsd_document import (DecisionAnswer, DsdDecision, DsdDocument,
                           build_completion, parse_dsd)

# The bus namespace the completion event rides ([CXC-DSDV-NOTIFY] working
# default; the exact topic is FLAGGED open in [DD-DSDV-MCP-HOME]).
ANSWERED_NS = "_app/dsd/answered"

# layout metrics ------------------------------------------------------------
_PAD = 22.0                 # form outer padding
_GROUP_GAP = 18.0           # between control-groups
_GROUP_PAD = 16.0           # inside a group card
_Q_SIZE = 15.0              # question header
_OPT_SIZE = 13.0            # option label
_TRADE_SIZE = 12.0          # tradeoffs prose
_CTX_SIZE = 12.5            # What-this-is / Why-it-matters context prose
_CTX_GAP = 5.0              # between context lines/blocks
_THEME_SIZE = 12.0         # `## Theme …` section-divider label
_THEME_ABOVE = 32.0        # band reserved ABOVE a themed group's card
_RADIO = 18.0               # radio diameter
_RADIO_GAP = 12.0           # radio -> label
_OPT_GAP = 10.0             # between option rows
_LINE = 1.45                # line-height multiple
_COMMENT_ROWS = 3
_COMMENT_H = 2 * 7.0 + _COMMENT_ROWS * 18.0    # 3-row console field (68px)
_COMMENT_LABEL_H = 20.0
_CHIP_TEXT = "recommended"
# "Save Answers" footer ([CXC-DSDV-SAVE] SAVE ANSWERS BUTTON) — a pinned
# primary action bar so the save-close is one clear tap, always in view
# regardless of scroll.  The scroll viewport is shrunk by _FOOTER_H so content
# never hides behind it.
_FOOTER_H = 60.0
_BTN_H = 36.0
_BTN_PAD_X = 22.0
_BTN_FONT = 14.0
_SAVE_LABEL = "Save Answers"

# The always-present "Neither / None — explain" escape hatch ([CXC-DSDV-NEITHER],
# the Problem-7 reject-BOTH-branches option).  Rendered as one extra single-choice
# radio BELOW a decision's parsed options; it is a first-class, per-decision
# choice.  Selecting it makes that decision's comment REQUIRED ([CXC-DSDV-NEITHER]
# COMMENT-REQUIRED) and, on save-close, writes back the label "Neither" + the
# verbatim comment (the answer-schema reconciliation, [CXC-DSDV-SAVE]).
_NEITHER_SUFFIX = "-neither"
_NEITHER_LABEL = "Neither"
_NEITHER_ROW_TEXT = "Neither / None — explain (requires a comment)"


def _neither_id(dec_id: str) -> str:
    """The synthetic per-decision option id for the always-present Neither radio
    ([CXC-DSDV-NEITHER]).  Never collides with a parsed option id (those are
    ``<Dn>o<idx>``)."""
    return f"{dec_id}{_NEITHER_SUFFIX}"


@dataclass
class _OptRow:
    dec_idx: int
    opt_idx: int
    y: float = 0.0
    height: float = 0.0
    label_lines: List[str] = dfield(default_factory=list)
    trade_lines: List[str] = dfield(default_factory=list)
    is_reco: bool = False


@dataclass
class _Group:
    dec_idx: int
    y: float = 0.0
    height: float = 0.0
    q_lines: List[str] = dfield(default_factory=list)
    opt_rows: List[_OptRow] = dfield(default_factory=list)
    comment_y: float = 0.0             # content-relative
    slot_present: bool = True
    read_only: bool = False
    # DECISION CONTEXT ([CXC-DSDV-FORM]): the `## Theme …` divider drawn ABOVE the
    # card (or "" for none), and the What-this-is / Why-it-matters lines drawn
    # inside the card ABOVE the options.  *_y are content-relative to the group
    # inner top (the same basis as opt_rows / comment_y).
    theme_label: str = ""
    whatis_lines: List[str] = dfield(default_factory=list)
    why_lines: List[str] = dfield(default_factory=list)
    whatis_y: float = 0.0
    why_y: float = 0.0
    # the always-present "Neither / None" radio row ([CXC-DSDV-NEITHER]), drawn
    # BELOW the parsed options; content-relative like opt_rows / comment_y.
    neither_y: float = 0.0
    neither_height: float = 0.0
    neither_lines: List[str] = dfield(default_factory=list)


@dataclass
class _FormLayout:
    groups: List[_Group]
    content_height: float


def _wrap(canvas: Canvas, text: str, avail: float, font: str,
          size: float) -> List[str]:
    """Greedy word-wrap *text* to *avail* px on the current face — the SAME
    canvas.measure_text advances draw uses, so wrap and paint agree."""
    canvas.set_font(font, size, "regular")
    if not text:
        return [""]
    words = text.split()
    lines: List[str] = []
    cur = ""
    for w in words:
        trial = w if not cur else cur + " " + w
        if canvas.measure_text(trial).width <= avail or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class DsdFormWidget(Widget):
    """Interactive DSD form: radios (pre-selected recommendations) + per-decision
    comment fields, with save-on-close write-back ([SPEC:CXC-DSDV])."""

    def __init__(self, widget_id: str, *,
                 content: Optional[str] = None,
                 path: Optional[str] = None,
                 correlation_id: str = "",
                 **kwargs: Any) -> None:
        super().__init__(widget_id, "dsd_form", **kwargs)
        self.interactive = True
        self.accepts_focus = True
        self.focus_ring = False
        self.dsd_path: Optional[str] = None
        self.correlation_id = str(correlation_id
                                  or os.environ.get("COMPOSEIO_DSD_CORRELATION",
                                                    ""))
        self.doc: Optional[DsdDocument] = None
        # answer state — the single source of what save-close writes:
        #   _selected[dec_id] = set(option_id)  (single-choice => 0 or 1)
        #   _touched[dec_id]   = the human changed this decision (chip clears)
        self._selected: Dict[str, set] = {}
        self._touched: set = set()
        self._comment_fields: Dict[str, TextInputWidget] = {}
        self._active_field: Optional[TextInputWidget] = None
        # scroll (TOP-anchored) ------------------------------------------------
        self._scroll = 0.0
        self._max_scroll = 0.0
        # layout cache ---------------------------------------------------------
        self._layout: Optional[_FormLayout] = None
        self._layout_key: Optional[Tuple[int, float, str]] = None
        self._rev = 0
        # draw-pass hit zones ([CXC-DSDV-FORM]) --------------------------------
        self._radio_zones: List[Tuple[str, str, Rect]] = []   # (dec_id, opt_id, rect)
        self._comment_zones: List[Tuple[str, Rect]] = []      # (dec_id, rect)
        # the "Save Answers" button hit-zone, recorded in the SAME draw pass
        # that paints it ([CXC-DSDV-FORM] HIT ZONES FROM THE DRAW PASS).
        self._save_zone: Optional[Rect] = None
        self._saved = False
        self._status = ""

        if content is not None:
            self.set_document(parse_dsd(content, path=path))
        elif path is not None:
            self.load_path(path)

    # -- source ---------------------------------------------------------------

    def load_path(self, path: str) -> None:
        """Read + parse the DSD at *path* (READ-ONLY open, [CXC-DSDV-PARSE])."""
        self.dsd_path = str(path)
        try:
            with open(self.dsd_path, "r", encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            self.set_document(parse_dsd(f"(could not read {path}: {exc})",
                                        path=path))
            return
        self.set_document(parse_dsd(text, path=self.dsd_path))

    def set_document(self, doc: DsdDocument) -> None:
        self.doc = doc
        self.dsd_path = doc.path or self.dsd_path
        self._selected.clear()
        self._touched.clear()
        self._comment_fields.clear()
        self._active_field = None
        for dec in doc.decisions:
            # PRE-SELECT the recommendation ([CXC-DSDV-PRESELECT]).
            self._selected[dec.id] = ({dec.preselect_id}
                                      if dec.preselect_id else set())
            f = TextInputWidget(f"{self.widget_id}-c-{dec.id}",
                                chrome="console", min_lines=_COMMENT_ROWS,
                                max_lines=6,
                                placeholder="Additional comments…")
            f._ctx = self._ctx
            self._comment_fields[dec.id] = f
        self._rev += 1
        self._layout = None
        self._scroll = 0.0
        self.request_redraw()

    # -- lifecycle ------------------------------------------------------------

    def on_mount(self, ctx: Any = None) -> None:
        super().on_mount(ctx)
        ct.theme_from_ctx(ctx)
        for f in self._comment_fields.values():
            f._ctx = ctx
            f.on_mount(ctx)

    def on_unmount(self) -> None:
        # SAVE-ON-CLOSE ([CXC-DSDV-SAVE]): closing the WINDOW is the save.  The
        # launcher-spawned window sets COMPOSEIO_DSD_SAVE_ON_CLOSE=1; a headless
        # / pixel-verification render (which also tears the tree down via
        # app.stop()) does NOT, so merely RENDERING a DSD never rewrites it.
        # (save() is also hard-guarded to no-op on a failed load/parse.)
        if os.environ.get("COMPOSEIO_DSD_SAVE_ON_CLOSE", "").lower() in (
                "1", "true", "yes", "on"):
            self.save()

    # -- answer state ---------------------------------------------------------

    def answers(self) -> Dict[str, DecisionAnswer]:
        """The current answer map (option LABELS + comment text), the single
        source of write-back ([CXC-DSDV-FORM] SELECTION STATE IS THE ANSWER)."""
        out: Dict[str, DecisionAnswer] = {}
        if self.doc is None:
            return out
        for dec in self.doc.decisions:
            comment = ""
            f = self._comment_fields.get(dec.id)
            if f is not None:
                comment = f.text.strip()
            labels: List[str] = []
            if _neither_id(dec.id) in self._selected.get(dec.id, set()):
                # NEITHER answer-schema reconciliation ([CXC-DSDV-NEITHER]): the
                # reject-both pick round-trips as the label "Neither" + the
                # verbatim comment (write-back, completion, and minted node).
                labels = [_NEITHER_LABEL]
            else:
                for oid in self._ordered_selection(dec):
                    opt = dec.option_by_id(oid)
                    if opt is not None:
                        labels.append(opt.label)
            out[dec.id] = DecisionAnswer(labels=labels, comment=comment)
        return out

    def _ordered_selection(self, dec: DsdDecision) -> List[str]:
        sel = self._selected.get(dec.id, set())
        return [o.option_id for o in dec.options if o.option_id in sel]

    def _select(self, dec_id: str, opt_id: str) -> None:
        # single-choice: replace the set (mutually exclusive, [CXC-DSDV-FORM]).
        self._selected[dec_id] = {opt_id}
        self._touched.add(dec_id)
        self._blur_active()
        self.request_redraw()

    # -- completeness gate ([CXC-DSDV-COMPLETE]) ------------------------------

    def _answerable(self, dec: DsdDecision) -> bool:
        """A decision the human MUST answer before save/close — one with a
        writable ``Your call:`` slot.  A read-only decision (no slot) cannot be
        persisted, so it is excluded from the gate ([CXC-DSDV-COMPLETE])."""
        return dec.slot_line is not None

    def _decision_answered(self, dec: DsdDecision) -> bool:
        """True iff *dec* has a complete answer: a selected option, OR the
        always-present "Neither" WITH a non-empty comment ([CXC-DSDV-NEITHER]
        COMMENT-REQUIRED — a Neither without a comment is NOT answered)."""
        sel = self._selected.get(dec.id, set())
        if not sel:
            return False
        if _neither_id(dec.id) in sel:
            f = self._comment_fields.get(dec.id)
            return bool(f and f.text.strip())
        return True

    def _unanswered(self) -> List[DsdDecision]:
        """The answerable decisions still missing a complete answer."""
        if self.doc is None or not self.doc.ok:
            return []
        return [d for d in self.doc.decisions
                if self._answerable(d) and not self._decision_answered(d)]

    def _is_complete(self) -> bool:
        """The completeness invariant ([CXC-DSDV-COMPLETE]): EVERY answerable
        decision has an answer — the precondition for save/close."""
        return (self.doc is not None and self.doc.ok
                and not self._unanswered())

    def save(self) -> Optional[dict]:
        """Write the answer state back into the DSD slots + mark answered, then
        return the completion payload (also published on the bus).  Idempotent —
        a second call (Cmd+S then close) is a no-op after the first write.

        HARD-GUARDED FAIL-SAFE: a no-op unless a DSD actually PARSED (``doc.ok``)
        — a failed load ("could not read …") or a malformed DSD (0 decisions)
        NEVER rewrites the file, so rendering a non-DSD can't clobber it."""
        if (self._saved or self.doc is None or not self.doc.ok
                or not self.dsd_path):
            return None
        # commit any in-progress comment edit into field state.
        self._blur_active()
        # COMPLETENESS GATE ([CXC-DSDV-COMPLETE]): refuse the save/close while ANY
        # answerable decision is unanswered (including a "Neither" pick that still
        # lacks its required comment).  This is the SAME gate the disabled Save
        # button + the "N unanswered" footer affordance surface — closing the
        # window can NEVER persist a partial answer or mark it answered.
        if not self._is_complete():
            n = len(self._unanswered())
            self._status = (f"{n} unanswered — answer every decision "
                            "(or pick Neither + a comment) to save")
            self.request_redraw()
            return None
        ans = self.answers()
        try:
            new_text = self.doc.render_answers(ans)
            self.doc.save(new_text)
        except Exception as exc:          # fail-safe: original untouched
            self._status = f"save failed: {exc}"
            self.request_redraw()
            return None
        self._saved = True
        payload = build_completion(self.dsd_path, self.doc.decisions, ans,
                                   correlation_id=self.correlation_id)
        self._status = "answered — saved"
        # COMPLETION EVENT ([CXC-DSDV-NOTIFY]) — explicit, not a file-watch.
        if self._ctx is not None:
            try:
                self.publish(ANSWERED_NS, payload)
            except Exception:
                pass
        # AUTO-WRITE decision-graph nodes ([CXC-DSDV-SAVE], D6): mint one
        # [DEC-*] node per answered decision through the guarded decision-ops
        # write path — killing the architect's manual post-DSD transcription.
        # Best-effort + graph-gated: never breaks the save above.
        self._mint_decision_nodes(ans)
        return payload

    def _decision_graph_path(self) -> Optional[str]:
        """The decision-graph .lts the minted [DEC-*] nodes are written to, or
        None to DISABLE the auto-write (the safe default, so a headless render or
        an unconfigured launch never writes a graph).  A DSD-specific override
        wins; else the shared decision-ops graph env is honored — so the
        architect enables production minting by pointing an env var at the live
        graph at console-spawn time (a deliberate, glass-gated cutover), and
        tests point it at a fixture."""
        return (os.environ.get("COMPOSEIO_DSD_DECISION_GRAPH")
                or os.environ.get("DECISION_OPS_GRAPH") or None)

    def _mint_decision_nodes(self, answers: Dict[str, DecisionAnswer]) -> list:
        """Mint [DEC-*] nodes from *answers* into the configured decision graph
        (D6).  Best-effort: any failure is recorded in status, never raised —
        the DSD save-close already committed and must not report as failed.
        The heavy decision-ops import is LAZY (only when a graph is configured)."""
        graph = self._decision_graph_path()
        if not graph or self.doc is None or not self.dsd_path:
            return []
        try:
            from composeio.decision_ops.dsd_review import mint_decision_nodes
            results = mint_decision_nodes(graph, self.dsd_path,
                                          self.doc.decisions, answers)
        except Exception as exc:  # noqa: BLE001 — never break the save
            self._status = f"answered — saved (decision-node write skipped: {exc})"
            self.request_redraw()
            return []
        minted = [r for r in results if r.get("changed")]
        if minted:
            self._status = f"answered — saved ({len(minted)} decision node(s) minted)"
            self.request_redraw()
        return results

    def _save_and_close(self) -> None:
        """The "Save Answers" button action ([CXC-DSDV-SAVE] SAVE ANSWERS
        BUTTON): make the save-on-close behavior an EXPLICIT user action.  It
        runs the SAME write-back + completion-event path the close-handler
        uses (``save()`` — reused, not duplicated), then requests the SAME
        clean window shutdown a window-close triggers.  The on-unmount save
        stays a fallback (closing without the button still saves); ``save()``
        is idempotent, so the teardown's re-save is a guaranteed no-op."""
        self.save()
        self._request_close()

    def _request_close(self) -> None:
        """Ask the host to close the window (deferred, graceful) via the
        engine-context primitive; a no-op when unhosted (tests / headless)."""
        ctx = self._ctx
        stop = getattr(ctx, "request_stop", None) if ctx is not None else None
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    # -- keyboard / editing routing (to the active comment field) -------------

    def on_key(self, key: str, modifiers: Any = None) -> None:
        mod = int(modifiers or 0)
        primary = bool(mod & (0x00C0 | 0x0C00))       # Ctrl | Cmd
        if primary and len(key) == 1 and key.upper() == "S":
            self.save()                                # Cmd/Ctrl+S saves now
            return
        if self._active_field is not None:
            self._active_field.on_key(key, modifiers)
            self.request_redraw()

    def on_text(self, text: str) -> None:
        if self._active_field is not None:
            self._active_field.on_text(text)
            self.request_redraw()

    def on_focus(self) -> None:
        pass

    def on_blur(self) -> None:
        # engine focus truly left this widget: stop editing the comment field.
        self._blur_active()

    def _activate_comment(self, dec_id: str) -> None:
        f = self._comment_fields.get(dec_id)
        if f is None or f is self._active_field:
            return
        self._blur_active()
        self._active_field = f
        f.focused = True
        f.on_focus()

    def _blur_active(self) -> None:
        if self._active_field is not None:
            self._active_field.focused = False
            self._active_field.on_blur()
            self._active_field = None

    # -- pointer --------------------------------------------------------------

    def on_pointer_down(self, x: float, y: float) -> None:
        for dec_id, rect in self._comment_zones:
            if _in(rect, x, y):
                self._activate_comment(dec_id)
                f = self._active_field
                if f is not None:
                    f.on_pointer_down(x, y)
                self.request_redraw()
                return
        # a press off any comment field ends editing.
        self._blur_active()
        self.request_redraw()

    def on_tap(self, x: float, y: float) -> None:
        # the "Save Answers" primary action wins (footer is reserved, so it
        # never overlaps an option row) — save + close in one tap.  DISABLED
        # while the form is incomplete ([CXC-DSDV-COMPLETE]): a tap on the
        # greyed button surfaces the "N unanswered" reason and does NOT save.
        if self._save_zone is not None and _in(self._save_zone, x, y):
            if self._is_complete():
                self._save_and_close()
            else:
                n = len(self._unanswered())
                self._status = (f"{n} unanswered — answer every decision "
                                "(or pick Neither + a comment) to save")
                self.request_redraw()
            return
        for dec_id, opt_id, rect in self._radio_zones:
            if _in(rect, x, y):
                self._select(dec_id, opt_id)
                return

    def on_pan(self, dx: float, dy: float) -> None:
        if self._active_field is not None:
            self._active_field.on_pan(dx, dy)
            self.request_redraw()

    def on_scroll(self, dx: float, dy: float) -> None:
        if not dy:
            return
        before = self._scroll
        self._scroll = max(0.0, min(self._max_scroll, self._scroll - dy * 48.0))
        if self._scroll != before:
            self.request_redraw()

    def hit_test(self, x: float, y: float) -> bool:
        r = self.arranged_rect
        return (r is not None and r.x <= x <= r.x + r.w
                and r.y <= y <= r.y + r.h)

    # -- measure / layout -----------------------------------------------------

    def measure(self, constraints: Constraints) -> Size:
        return Size(constraints.max_width, constraints.max_height)

    def _build_layout(self, canvas: Canvas, content_w: float) -> _FormLayout:
        groups: List[_Group] = []
        y = 0.0
        if self.doc is None or not self.doc.ok:
            return _FormLayout(groups=[], content_height=0.0)
        inner_w = content_w - 2 * _GROUP_PAD
        prev_theme: Optional[str] = None
        for idx, dec in enumerate(self.doc.decisions):
            g = _Group(dec_idx=idx)
            # THEME DIVIDER ([CXC-DSDV-FORM]): a `## Theme …` section header drawn
            # above the FIRST card of each group so decisions read grouped as
            # authored, not as a flat list.  Reserve a band above this card.
            if dec.theme and dec.theme != prev_theme:
                g.theme_label = dec.theme
                y += _THEME_ABOVE
                prev_theme = dec.theme
            gy = 0.0
            # question header (preserving the ⛔ blocker glyph when present)
            mk = f" {dec.marker}" if dec.marker else ""
            if dec.question:
                q = f"{dec.id}{mk} · {dec.question}"
            else:
                q = f"{dec.id}{mk}"
            g.q_lines = _wrap(canvas, q, inner_w, style.FONT_UI, _Q_SIZE)
            gy += len(g.q_lines) * _Q_SIZE * _LINE + 10.0
            # DECISION CONTEXT ([CXC-DSDV-FORM]): What-this-is gloss + Why-it-matters
            # stakes, ABOVE the options — the context a human decides FROM.
            if dec.what_this_is:
                g.whatis_lines = _wrap(canvas, f"What this is: {dec.what_this_is}",
                                       inner_w, style.FONT_UI, _CTX_SIZE)
                g.whatis_y = gy
                gy += len(g.whatis_lines) * _CTX_SIZE * _LINE + _CTX_GAP
            if dec.why_it_matters:
                g.why_lines = _wrap(canvas, f"Why it matters: {dec.why_it_matters}",
                                    inner_w, style.FONT_UI, _CTX_SIZE)
                g.why_y = gy
                gy += len(g.why_lines) * _CTX_SIZE * _LINE + _CTX_GAP
            if dec.what_this_is or dec.why_it_matters:
                gy += 6.0
            # options
            radio_col = _RADIO + _RADIO_GAP
            opt_avail = inner_w - radio_col
            for oi, opt in enumerate(dec.options):
                row = _OptRow(dec_idx=idx, opt_idx=oi,
                              is_reco=(opt.option_id == dec.preselect_id))
                row.label_lines = _wrap(canvas, opt.label, opt_avail,
                                        style.FONT_UI, _OPT_SIZE)
                row.trade_lines = _wrap(canvas, opt.tradeoffs, opt_avail,
                                        style.FONT_UI, _TRADE_SIZE)
                lh = (len(row.label_lines) * _OPT_SIZE * _LINE
                      + len(row.trade_lines) * _TRADE_SIZE * _LINE + 6.0)
                row.height = max(lh, _RADIO + 6.0)
                row.y = gy
                gy += row.height + _OPT_GAP
                g.opt_rows.append(row)
            if not dec.options:
                gy += 20.0            # "no options parsed" note room
            # ALWAYS-PRESENT "Neither / None" escape hatch ([CXC-DSDV-NEITHER]):
            # one extra single-choice radio BELOW the parsed options — reject
            # BOTH branches.  Measured with the SAME wrap the options use.
            g.neither_lines = _wrap(canvas, _NEITHER_ROW_TEXT, opt_avail,
                                    style.FONT_UI, _OPT_SIZE)
            g.neither_y = gy
            g.neither_height = max(len(g.neither_lines) * _OPT_SIZE * _LINE + 6.0,
                                   _RADIO + 6.0)
            gy += g.neither_height + _OPT_GAP
            # comment field
            gy += 6.0
            g.comment_y = gy + _COMMENT_LABEL_H
            gy += _COMMENT_LABEL_H + _COMMENT_H + 4.0
            g.slot_present = dec.slot_line is not None
            g.read_only = dec.slot_line is None
            g.height = gy + 2 * _GROUP_PAD
            g.y = y
            y += g.height + _GROUP_GAP
            groups.append(g)
        return _FormLayout(groups=groups, content_height=max(0.0, y))

    def _ensure_layout(self, canvas: Canvas, content_w: float) -> _FormLayout:
        key = (self._rev, round(content_w, 1), ct.CONSOLE.active.name)
        if self._layout is None or self._layout_key != key:
            self._layout = self._build_layout(canvas, content_w)
            self._layout_key = key
        return self._layout

    # -- draw -----------------------------------------------------------------

    def draw(self, canvas: Canvas, rect: Rect) -> None:
        t = ct.CONSOLE.active
        canvas.save()
        canvas.set_fill_color(*t.panel)
        canvas.fill_rect(rect.x, rect.y, rect.w, rect.h)

        content_w = max(120.0, rect.w - 2 * _PAD)
        content_x = rect.x + _PAD
        top = rect.y + _PAD

        self._radio_zones = []
        self._comment_zones = []
        self._save_zone = None

        if self.doc is None or not self.doc.ok:
            self._draw_empty(canvas, rect, t)
            canvas.restore()
            return

        # Reserve a pinned footer for the "Save Answers" action; the scroll
        # viewport (and its clip) stop above it so nothing hides behind it.
        region_h = max(0.0, rect.h - _FOOTER_H)
        avail = max(0.0, region_h - 2 * _PAD)

        layout = self._ensure_layout(canvas, content_w)
        self._max_scroll = max(0.0, layout.content_height - avail)
        self._scroll = max(0.0, min(self._max_scroll, self._scroll))

        canvas.save()
        canvas.clip_rect(rect.x, rect.y, rect.w, region_h)
        origin_y = top - self._scroll
        for g in layout.groups:
            gy = origin_y + g.y
            # cull against the FULL group extent, including any theme band drawn
            # above the card, so a divider is never orphaned off-screen.
            top_extent = gy - (_THEME_ABOVE if g.theme_label else 0.0)
            if gy + g.height < rect.y or top_extent > rect.y + region_h:
                continue
            self._draw_group(canvas, g, content_x, gy, content_w, t)
        canvas.restore()

        self._draw_scrollbar(canvas, rect, _PAD, region_h, avail,
                             layout.content_height, t)
        self._draw_footer(canvas, rect, t)
        canvas.restore()

    def _draw_group(self, canvas: Canvas, g: _Group, x: float, y: float,
                    content_w: float, t) -> None:
        dec = self.doc.decisions[g.dec_idx]
        # THEME DIVIDER ([CXC-DSDV-FORM]): the section header for the first card
        # of a `## Theme …` group, painted in the reserved band above the card.
        if g.theme_label:
            canvas.set_font(style.FONT_UI, _THEME_SIZE, "regular")
            canvas.set_fill_color(*t.accent)
            canvas.draw_text(
                g.theme_label.upper(), x,
                style.baseline_for(canvas, y - _THEME_ABOVE + _THEME_ABOVE / 2),
                "left")
            canvas.set_fill_color(*t.line)
            canvas.fill_rect(x, y - 8.0, content_w, 1.0)
        # group card
        canvas.set_fill_color(*t.panel3)
        canvas.fill_round_rect(x, y, content_w, g.height, ct.RADIUS_INNER)
        canvas.set_stroke_color(*t.line)
        canvas.set_stroke_width(1.0)
        canvas.stroke_round_rect(x + 0.5, y + 0.5, content_w - 1,
                                 g.height - 1, ct.RADIUS_INNER)
        ix = x + _GROUP_PAD
        iy = y + _GROUP_PAD
        inner_w = content_w - 2 * _GROUP_PAD
        # question
        canvas.set_font(style.FONT_UI, _Q_SIZE, "regular")
        for ln in g.q_lines:
            base = style.baseline_for(canvas, iy + _Q_SIZE * _LINE / 2)
            canvas.set_fill_color(*t.ink)
            canvas.draw_text(ln, ix, base, "left")
            iy += _Q_SIZE * _LINE
        iy += 10.0
        # DECISION CONTEXT ([CXC-DSDV-FORM]): the What-this-is gloss + the
        # Why-it-matters stakes, ABOVE the options.  Positioned by the *_y offsets
        # the layout pass measured (same basis as the option rows).
        canvas.set_font(style.FONT_UI, _CTX_SIZE, "regular")
        for lines, y0 in ((g.whatis_lines, g.whatis_y),
                          (g.why_lines, g.why_y)):
            if not lines:
                continue
            ly = y + _GROUP_PAD + y0
            for ln in lines:
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(ln, ix,
                                 style.baseline_for(canvas,
                                                    ly + _CTX_SIZE * _LINE / 2),
                                 "left")
                ly += _CTX_SIZE * _LINE
            canvas.set_font(style.FONT_UI, _CTX_SIZE, "regular")
        # options — row.y is content-relative to the group inner top, which is
        # absolute (y + _GROUP_PAD); it already includes the question offset.
        sel = self._selected.get(dec.id, set())
        chip_ok = dec.id not in self._touched
        for row in g.opt_rows:
            opt = dec.options[row.opt_idx]
            ry = y + _GROUP_PAD + row.y
            selected = opt.option_id in sel
            # radio
            cx = ix + _RADIO / 2
            cy = ry + _RADIO / 2 + 1.0
            canvas.set_stroke_color(*(t.accent if selected else t.line2))
            canvas.set_stroke_width(2.0)
            canvas.stroke_circle(cx, cy, _RADIO / 2 - 1.0)
            if selected:
                canvas.set_fill_color(*t.accent)
                canvas.fill_circle(cx, cy, _RADIO / 2 - 4.0)
            # clickable zone: the whole option row
            zone = Rect(ix, ry, inner_w, row.height)
            self._radio_zones.append((dec.id, opt.option_id, zone))
            # label + optional recommended chip
            lx = ix + _RADIO + _RADIO_GAP
            ly = ry
            canvas.set_font(style.FONT_UI, _OPT_SIZE, "regular")
            for k, ln in enumerate(row.label_lines):
                base = style.baseline_for(canvas, ly + _OPT_SIZE * _LINE / 2)
                canvas.set_fill_color(*(t.ink if selected else t.ink))
                canvas.draw_text(ln, lx, base, "left")
                if k == 0 and row.is_reco and chip_ok:
                    lw = canvas.measure_text(ln).width
                    self._draw_chip(canvas, lx + lw + 10.0,
                                    ly + _OPT_SIZE * _LINE / 2, t)
                ly += _OPT_SIZE * _LINE
            canvas.set_font(style.FONT_UI, _TRADE_SIZE, "regular")
            for ln in row.trade_lines:
                base = style.baseline_for(canvas, ly + _TRADE_SIZE * _LINE / 2)
                canvas.set_fill_color(*t.dim)
                canvas.draw_text(ln, lx, base, "left")
                ly += _TRADE_SIZE * _LINE
        if not dec.options:
            canvas.set_font(style.FONT_UI, _TRADE_SIZE, "regular")
            canvas.set_fill_color(*t.faint)
            oy = (y + _GROUP_PAD
                  + len(g.q_lines) * _Q_SIZE * _LINE + 10.0)
            canvas.draw_text("(no options parsed for this decision)",
                             ix, style.baseline_for(canvas, oy + 8), "left")
        # ALWAYS-PRESENT "Neither / None" radio ([CXC-DSDV-NEITHER]): a first-class
        # per-decision choice below the parsed options, registering its hit-zone in
        # the SAME draw pass.  A Neither still MISSING its required comment reads in
        # warn, so the human sees why the decision is not yet answered.
        nid = _neither_id(dec.id)
        neither_sel = nid in sel
        cfield = self._comment_fields.get(dec.id)
        comment_has = bool(cfield and cfield.text.strip())
        needs_comment = neither_sel and not comment_has
        nry = y + _GROUP_PAD + g.neither_y
        ncx = ix + _RADIO / 2
        ncy = nry + _RADIO / 2 + 1.0
        canvas.set_stroke_color(*(t.accent if neither_sel else t.line2))
        canvas.set_stroke_width(2.0)
        canvas.stroke_circle(ncx, ncy, _RADIO / 2 - 1.0)
        if neither_sel:
            canvas.set_fill_color(*t.accent)
            canvas.fill_circle(ncx, ncy, _RADIO / 2 - 4.0)
        self._radio_zones.append((dec.id, nid, Rect(ix, nry, inner_w,
                                                    g.neither_height)))
        nlx = ix + _RADIO + _RADIO_GAP
        nly = nry
        canvas.set_font(style.FONT_UI, _OPT_SIZE, "regular")
        for ln in g.neither_lines:
            base = style.baseline_for(canvas, nly + _OPT_SIZE * _LINE / 2)
            canvas.set_fill_color(*(t.warn if needs_comment else t.ink))
            canvas.draw_text(ln, nlx, base, "left")
            nly += _OPT_SIZE * _LINE
        # comment field — when Neither is picked the comment is REQUIRED, so the
        # label says so and reads in warn until a comment is typed.
        cy0 = y + _GROUP_PAD + g.comment_y
        clabel = "Additional comments (required)" if neither_sel \
            else "Additional comments"
        canvas.set_font(style.FONT_UI, 11.0, "regular")
        canvas.set_fill_color(*(t.warn if needs_comment else t.faint))
        canvas.draw_text(clabel,
                         ix, style.baseline_for(canvas, cy0 - _COMMENT_LABEL_H
                                                + _COMMENT_LABEL_H / 2), "left")
        field = self._comment_fields.get(dec.id)
        if field is not None:
            frect = Rect(ix, cy0, inner_w, _COMMENT_H)
            field.arranged_rect = frect
            field.draw(canvas, frect)
            self._comment_zones.append((dec.id, frect))
        if g.read_only:
            canvas.set_font(style.FONT_UI, 10.0, "regular")
            canvas.set_fill_color(*t.faint)
            canvas.draw_text("no writable slot — read-only",
                             x + content_w - _GROUP_PAD,
                             style.baseline_for(canvas, y + _GROUP_PAD + 6),
                             "right")

    def _draw_chip(self, canvas: Canvas, x: float, y_center: float, t) -> None:
        canvas.set_font(style.FONT_UI, 10.0, "regular")
        w = canvas.measure_text(_CHIP_TEXT).width + 14.0
        h = 16.0
        cy = y_center - h / 2
        canvas.set_fill_color(*t.accent_dim)
        canvas.fill_round_rect(x, cy, w, h, 8.0)
        canvas.set_fill_color(*t.accent)
        canvas.draw_text(_CHIP_TEXT, x + 7.0,
                         style.baseline_for(canvas, y_center), "left")

    def _draw_empty(self, canvas: Canvas, rect: Rect, t) -> None:
        name = os.path.basename(self.dsd_path) if self.dsd_path else "DSD"
        canvas.set_font(style.FONT_UI, 13.0, "regular")
        canvas.set_fill_color(*t.faint)
        canvas.draw_text(f"no decisions parsed from {name}",
                         rect.x + rect.w / 2, rect.y + rect.h / 2, "center")

    def _draw_footer(self, canvas: Canvas, rect: Rect, t) -> None:
        """Pinned footer: a status line + the "Save Answers" primary button,
        painted in this SAME pass and recording the button hit-zone
        ([CXC-DSDV-FORM] HIT ZONES FROM THE DRAW PASS)."""
        fy = rect.y + rect.h - _FOOTER_H
        # bar ground + a 1px top divider (fill_rect, no line primitive needed)
        canvas.set_fill_color(*t.panel)
        canvas.fill_rect(rect.x, fy, rect.w, _FOOTER_H)
        canvas.set_fill_color(*t.line)
        canvas.fill_rect(rect.x, fy, rect.w, 1.0)
        # the primary "Save Answers" button: accent fill + dark send-ink label
        # when the form is COMPLETE; a greyed, non-committing button while any
        # decision is unanswered ([CXC-DSDV-COMPLETE]).  The hit-zone is recorded
        # in EITHER state (draw-pass invariant); the tap is gated in on_tap.
        complete = self._is_complete()
        canvas.set_font(style.FONT_UI, _BTN_FONT, "regular")
        bw = canvas.measure_text(_SAVE_LABEL).width + 2 * _BTN_PAD_X
        bx = rect.x + rect.w - _PAD - bw
        by = fy + (_FOOTER_H - _BTN_H) / 2
        canvas.set_fill_color(*(t.accent if complete else t.panel3))
        canvas.fill_round_rect(bx, by, bw, _BTN_H, ct.RADIUS_INNER)
        if not complete:
            canvas.set_stroke_color(*t.line2)
            canvas.set_stroke_width(1.0)
            canvas.stroke_round_rect(bx + 0.5, by + 0.5, bw - 1, _BTN_H - 1,
                                     ct.RADIUS_INNER)
        canvas.set_fill_color(*(t.send_ink if complete else t.faint))
        canvas.draw_text(_SAVE_LABEL, bx + bw / 2,
                         style.baseline_for(canvas, by + _BTN_H / 2), "center")
        self._save_zone = Rect(bx, by, bw, _BTN_H)
        # status to the left: while incomplete, the "N unanswered" blocking reason
        # in warn; else the saved status or the resting hint.
        if not complete:
            n = len(self._unanswered())
            msg = (f"{n} unanswered — answer every decision "
                   "(or pick Neither + a comment) to save")
            color = t.warn
        else:
            msg = self._status or "your picks + comments are written on save"
            color = t.accent if self._status else t.faint
        canvas.set_font(style.FONT_UI, 11.0, "regular")
        canvas.set_fill_color(*color)
        canvas.draw_text(msg, rect.x + _PAD,
                         style.baseline_for(canvas, by + _BTN_H / 2), "left")

    def _draw_scrollbar(self, canvas: Canvas, rect: Rect, pad: float,
                        region_h: float, avail: float, content_h: float,
                        t) -> None:
        if content_h <= avail or avail <= 0.0:
            return
        track_w = 4.0
        tx = rect.x + rect.w - 10.0
        ty = rect.y + pad
        th = region_h - 2 * pad
        canvas.set_fill_color(*t.line)
        canvas.fill_round_rect(tx, ty, track_w, th, track_w / 2)
        thumb_h = max(24.0, th * (avail / content_h))
        travel = max(0.0, th - thumb_h)
        frac = 0.0 if self._max_scroll <= 0.0 else self._scroll / self._max_scroll
        canvas.set_fill_color(*t.faint)
        canvas.fill_round_rect(tx, ty + travel * frac, track_w, thumb_h,
                               track_w / 2)


def _in(r: Rect, x: float, y: float) -> bool:
    return r.x <= x <= r.x + r.w and r.y <= y <= r.y + r.h
