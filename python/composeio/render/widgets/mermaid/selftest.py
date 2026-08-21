"""
Module-local self-test for the Mermaid Widget ([CXC-IC-CANVAS]).

Two tiers, reported separately (the media-widget two-tier verdict):

  MACHINE-GREEN — the bus-driven behavior: default namespace is
                  ``thread/diagram``; a value on it re-renders (on_bus_state
                  swaps `source`, requests redraw); the Canvas tier draws the
                  source panel through the REAL Canvas contract call sequence
                  with no exception; the web fragment embeds the source and
                  the vendored bundle exists + sets window.mermaid.

  GLASS-PENDING — the actual mermaid.js rasterization is a BROWSER workload
                  (verified by opening the standalone demo, see
                  render/examples/mermaid_widget_demo.html); this suite cannot
                  run a JS engine, so it asserts the web CONTRACT (fragment +
                  vendored asset), not pixels.

Run:  python -m composeio.render.widgets.mermaid --selftest
"""

from __future__ import annotations

import os
from typing import Any, List, Optional

from ...widget import Rect
from .widget import (
    DEFAULT_NAMESPACE,
    MERMAID_VERSION,
    MermaidWidget,
    web_asset_path,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class _RecordingCanvas:
    """Duck-typed Canvas recording the chrome + text the widget draws.

    Not a Canvas subclass (avoids implementing every abstractmethod); the
    widget only calls the handful of methods stubbed here.
    """

    def __init__(self) -> None:
        self.texts: List[str] = []

    def draw_text(self, text: str, *a: Any, **k: Any) -> None:
        self.texts.append(str(text))

    # chrome no-ops
    def save(self) -> None: ...
    def restore(self) -> None: ...
    def set_fill_color(self, *a: Any) -> None: ...
    def fill_rect(self, *a: Any) -> None: ...
    def set_stroke_color(self, *a: Any) -> None: ...
    def set_stroke_width(self, *a: Any) -> None: ...
    def stroke_rect(self, *a: Any) -> None: ...
    def set_font(self, *a: Any) -> None: ...


class _CtxSpy:
    """Minimal engine ctx: counts request_redraw (the re-render signal)."""

    def __init__(self) -> None:
        self.redraws = 0

    def request_redraw(self, rect=None) -> None:
        self.redraws += 1


FLOW_A = "graph TD\n  A[Start] --> B{Ready?}\n  B -->|yes| C[Go]"
FLOW_B = "sequenceDiagram\n  David->>Architect: emit\n  Architect-->>Widget: render"


# ---------------------------------------------------------------------------
# MACHINE-GREEN — the bus-driven behavior
# ---------------------------------------------------------------------------

def test_default_namespace() -> str:
    w = MermaidWidget("m")
    assert w.namespace == DEFAULT_NAMESPACE == "thread/diagram", w.namespace
    assert w.widget_type == "mermaid"
    return f"default namespace = {w.namespace!r}"


def test_bus_state_rerenders() -> str:
    """A value on the bound namespace swaps source + requests a redraw —
    the whole thesis: emit once, redraw is pure widget behavior."""
    w = MermaidWidget("m")
    ctx = _CtxSpy()
    w.on_mount(ctx)
    assert w.source == ""
    w.on_bus_state(DEFAULT_NAMESPACE, FLOW_A)
    assert w.source == FLOW_A
    assert ctx.redraws == 1, ctx.redraws
    # a DIFFERENT diagram re-renders again (no model round-trip)
    w.on_bus_state(DEFAULT_NAMESPACE, FLOW_B)
    assert w.source == FLOW_B
    assert ctx.redraws == 2, ctx.redraws
    # an identical value does NOT thrash a redraw
    w.on_bus_state(DEFAULT_NAMESPACE, FLOW_B)
    assert ctx.redraws == 2, ctx.redraws
    return f"3 emits -> {ctx.redraws} redraws (idempotent on no-change)"


def test_canvas_tier_draws_source_panel() -> str:
    """Canvas tier shows the bytes on the bus without running JS, through the
    real Canvas contract call sequence, and never raises."""
    w = MermaidWidget("m", source=FLOW_A)
    c = _RecordingCanvas()
    w.draw(c, Rect(0, 0, 440, 320))
    joined = "\n".join(c.texts)
    assert "mermaid" in joined
    assert DEFAULT_NAMESPACE in joined
    assert "A[Start] --> B{Ready?}" in joined     # a source line rendered
    return f"source panel drew {len(c.texts)} text runs"


def test_empty_source_placeholder() -> str:
    w = MermaidWidget("m")
    c = _RecordingCanvas()
    w.draw(c, Rect(0, 0, 440, 320))               # no source -> must not raise
    assert any("awaiting diagram" in t for t in c.texts)
    return "empty source -> awaiting-diagram placeholder"


def test_configurable_namespace() -> str:
    w = MermaidWidget("m", namespace="self/plan/diagram")
    assert w.namespace == "self/plan/diagram"
    c = _RecordingCanvas()
    w.draw(c, Rect(0, 0, 200, 120))
    assert any("self/plan/diagram" in t for t in c.texts)
    return "namespace is configurable (shown in header)"


# ---------------------------------------------------------------------------
# GLASS-PENDING — the web contract (rasterization is a browser workload)
# ---------------------------------------------------------------------------

def test_vendored_asset_present() -> str:
    path = web_asset_path()
    assert os.path.exists(path), path
    data = open(path, "rb").read()
    assert len(data) > 1_000_000, len(data)       # the real UMD bundle
    head = data[:400]
    assert b"mermaid" in head
    # the UMD bundle exposes window.mermaid (offline, no CDN)
    assert b'globalThis["mermaid"]' in data[-4000:] or b"mermaid" in data[-4000:]
    return f"vendored mermaid v{MERMAID_VERSION} ({len(data) // 1024} KiB), no CDN"


def test_web_fragment_self_mounts() -> str:
    w = MermaidWidget("m", source=FLOW_A, theme="dark")
    html = w.render_html()
    assert "mermaid-m" in html
    assert "window.mermaid.render" in html        # runs the vendored engine
    assert "graph TD" in html                      # source embedded
    assert "__composeMermaid" in html              # bus re-render hook exposed
    assert "startOnLoad: false" in html
    assert DEFAULT_NAMESPACE in html               # subscribed namespace noted
    return "web fragment self-mounts + exposes bus re-render hook"


def test_fragment_escapes_source() -> str:
    """A diagram string with quotes/newlines/</script> stays a valid JS
    literal (no fragment break-out)."""
    tricky = "graph LR\n  X['a'] --> Y[\"</script>\"]"
    w = MermaidWidget("m", source=tricky)
    html = w.render_html()
    assert "</script>\n</script>" not in html      # not broken out
    assert "<\\/" in html                           # </ was escaped
    return "source is safely escaped into the fragment"


TESTS = [
    ("default_namespace (MACHINE-GREEN)", test_default_namespace),
    ("bus_state_rerenders (MACHINE-GREEN)", test_bus_state_rerenders),
    ("canvas_tier_source_panel (MACHINE-GREEN)", test_canvas_tier_draws_source_panel),
    ("empty_source_placeholder (MACHINE-GREEN)", test_empty_source_placeholder),
    ("configurable_namespace (MACHINE-GREEN)", test_configurable_namespace),
    ("vendored_asset (GLASS-PENDING)", test_vendored_asset_present),
    ("web_fragment (GLASS-PENDING)", test_web_fragment_self_mounts),
    ("fragment_escaping (GLASS-PENDING)", test_fragment_escapes_source),
]


def _run(label: str, tests: List) -> int:
    failures = 0
    for name, fn in tests:
        try:
            msg = fn()
            print(f"  ok  {name}: {msg}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERR  {name}: {type(exc).__name__}: {exc}")
    print(f"[mermaid {label}] {len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or []
    print(f"[mermaid selftest] vendored mermaid.js v{MERMAID_VERSION} "
          f"at {web_asset_path()}")
    return _run("selftest", TESTS)
