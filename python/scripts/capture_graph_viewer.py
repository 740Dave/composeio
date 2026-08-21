#!/usr/bin/env python3
"""Headless visual-proof capture for the Graph Viewer ([SPEC:CXC-GRAPH-VIEWER]).

Renders the GraphViewerWidget headless (no live window) against a REAL in-process
GraphProjector — a DIRECT lts-engine query ([CXC-GV-DATA-PATH]), no bus feed — for
several corpora + the corpus-dropdown-open state, and saves PNGs to
captures/graph_viewer/.  These are the glass proofs the human eyes verify
([CXC-GV-VERIFY-DATA-PATH] / -SELECTOR / -STYLE verify.human).

Run:  python composeio/render/../scripts/capture_graph_viewer.py     (from python/)
      python -m scripts.capture_graph_viewer
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.dirname(HERE)
sys.path.insert(0, PY_ROOT)

from composeio.render.skia_backend import SkiaCanvas                # noqa: E402
from composeio.render.widget import Rect                           # noqa: E402
from composeio.render.widgets import console_theme as ct           # noqa: E402
from composeio.render.widgets.decisions_tab import GraphViewerWidget  # noqa: E402
from composeio.decision_ops.graph_projection import (              # noqa: E402
    GraphProjector, load_style_profiles)

STYLES = os.path.join(PY_ROOT, "composeio", "render", "examples",
                      "graph_viewer.styles.yaml")
REPO_ROOT = os.path.dirname(PY_ROOT)
OUT_DIR = os.path.join(REPO_ROOT, "captures", "graph_viewer")
W, H = 1440, 1000


class _Ctx:
    theme = "dark"
    reduced_motion = True

    def request_redraw(self, rect=None):
        pass

    def publish(self, namespace, value, source=None):
        pass


def _mk_widget(projector):
    w = GraphViewerWidget("graph", projector=projector, direct_query=True)
    w.on_mount(_Ctx())
    return w


def _render(w, name):
    ct.set_theme("dark")
    c = SkiaCanvas()
    c.create_surface(W, H)
    c.set_fill_color(0x1E, 0x1E, 0x2E, 0xFF)
    c.fill_rect(0, 0, W, H)
    w.draw(c, Rect(0, 0, W, H))
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    c.save_png(path)
    print(f"  wrote {path}  ({len(w._nodes)} nodes)")


def main() -> int:
    profs, dflt = load_style_profiles(STYLES)
    proj = GraphProjector(style_profiles=profs, default_profile=dflt)
    print(f"engine_available={proj.engine_available}  "
          f"corpora={proj.list_corpora()}")

    # one FULL-graph proof per corpus (decisions / defects / insights)
    for corpus, fname in (
            ("intelligence-console", "decisions_full.png"),
            ("defect-graph", "defects_full.png"),
            ("insights-and-warts", "insights_full.png")):
        w = GraphViewerWidget("graph", corpus=corpus, projector=proj,
                              direct_query=True)
        w.on_mount(_Ctx())
        _render(w, fname)

    # the corpus DROPDOWN open ([CXC-GV-CORPUS-SELECTOR] glass proof)
    w = GraphViewerWidget("graph", corpus="intelligence-console",
                          projector=proj, direct_query=True)
    w.on_mount(_Ctx())
    w._corpus_dropdown_open = True
    _render(w, "decisions_dropdown.png")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
