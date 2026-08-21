#!/usr/bin/env python3
"""dsd_viewer_reference — reproducible GLASS proof of the interactive DSD viewer
([SPEC:CXC-DSDV] [CXC-DSDV-VERIFY-FORM]/[CXC-DSDV-VERIFY-SAVE]).

Renders the standalone dsdviewer scene in two states and prints a save-close
write-back round-trip, so the human-tier gates are inspectable as artifacts:

  1. dsd_viewer_default.png  — every decision's recommendation PRE-SELECTED and
     tagged "recommended" (accept-all = just close).
  2. dsd_viewer_answered.png — the human OVERRIDES a decision, selects one where
     there was no lean, and TYPES a per-decision comment (chip clears on the
     touched decision).
  3. a write-back round-trip on a TEMP copy of the DSD: the chosen options +
     comment land in the correct `Your call:` slots; the untouched decision's
     blank slot is preserved; an answered marker is added.

Usage:  PYTHONPATH=python python3 python/scripts/dsd_viewer_reference.py [OUT_DIR]
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.runtime import RenderApp
from composeio.render.widgets import DsdFormWidget

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "dsdviewer.compose.yaml")


def _find_form(app) -> DsdFormWidget:
    for w in app.comp.root.iter_widgets():
        if isinstance(w, DsdFormWidget):
            return w
    raise SystemExit("no DsdFormWidget in the scene")


def main(out_dir: str) -> int:
    os.makedirs(out_dir, exist_ok=True)

    # --- 1. default: recommendations pre-selected --------------------------
    app = RenderApp.from_file(SCENE, windowed=False)
    app.start()
    form = _find_form(app)
    default_png = os.path.join(out_dir, "dsd_viewer_default.png")
    app.render_headless(default_png)
    print(f"[1] default (recs pre-selected) -> {default_png}")
    for dec in form.doc.decisions[:4]:
        sel = form.answers()[dec.id].labels
        print(f"      {dec.id}: pre-selected={sel} "
              f"(source={dec.preselect_source})")

    # --- 1b. the "Save Answers" button (pinned footer) ---------------------
    # a focused artifact of the primary action ([CXC-DSDV-SAVE] SAVE ANSWERS
    # BUTTON): the button is drawn in every state, but this names the glass
    # gate — the button reads clearly and is where a tap saves + closes.
    button_png = os.path.join(out_dir, "dsd_viewer_save_button.png")
    app.render_headless(button_png)
    zone = form._save_zone
    print(f"[1b] Save Answers button -> {button_png}")
    print(f"      button hit-zone: {zone}")

    # --- 2. human overrides + comments -------------------------------------
    # override D2 to its FIRST option (not the lean), choose D3 (had no lean),
    # and type a comment on D2.
    d2, d3 = form.doc.decisions[1], form.doc.decisions[2]
    form._select(d2.id, d2.options[0].option_id)             # override the lean
    form._select(d3.id, d3.options[0].option_id)             # pick where none
    form._comment_fields[d2.id].text = "prefer the lighter cadence in practice"
    form._rev += 1                                           # invalidate layout
    form._layout = None
    answered_png = os.path.join(out_dir, "dsd_viewer_answered.png")
    app.render_headless(answered_png)
    print(f"[2] overridden + comment -> {answered_png}")
    app.stop()

    # --- 3. write-back round-trip on a temp DSD ----------------------------
    src = form.doc.path
    tmp = tempfile.mkdtemp(prefix="dsd-ref-")
    copy = os.path.join(tmp, os.path.basename(src))
    shutil.copy2(src, copy)
    from composeio.render.widgets.dsd_document import load_dsd
    w = DsdFormWidget("form", path=copy)

    class _Ctx:
        theme = "dark"
        def request_redraw(self, *_a, **_k): pass
        def publish(self, ns, v, source="w"): self.evt = (ns, v)
        def register_clocked(self, *_a): pass
        def unregister_clocked(self, *_a): pass
        def schedule_update(self, fn, target=None): fn()

    ctx = _Ctx()
    w.on_mount(ctx)
    # accept D1's rec, override D2, comment on D2, leave D3 blank
    w._select(w.doc.decisions[1].id, w.doc.decisions[1].options[0].option_id)
    w._comment_fields[w.doc.decisions[1].id].text = "scales better for us"
    os.environ["COMPOSEIO_DSD_SAVE_ON_CLOSE"] = "1"
    payload = w.save()
    print(f"[3] write-back round-trip on a temp copy -> {copy}")
    print("      completion event:", getattr(ctx, "evt", None) is not None,
          "->", ("_app/dsd/answered" if getattr(ctx, "evt", None) else None))
    reread = load_dsd(copy)
    for dec in reread.decisions:
        # show the raw slot line + any comment line
        if dec.slot_line is not None:
            line = copy_lines(copy)[dec.slot_line]
            print(f"      {dec.id} slot -> {line!r}")
    marker = copy_lines(copy)[0]
    print(f"      marker  -> {marker!r}")
    return 0


def copy_lines(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read().split("\n")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO_ROOT,
                                                             "captures", "dsd")
    raise SystemExit(main(out))
