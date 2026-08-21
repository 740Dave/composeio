#!/usr/bin/env python3
"""
render_chat_thread_parity.py — render the DESKTOP chat_thread reference from the
CYD parity config ([SPEC:CXC-IC]) and print the wrapped bubble lines.

The engine parser wires chat_thread to a LIST of bus namespaces (turns arrive
live via on_bus_state); it does not read the static `messages:` list — that is
the CYD-tier render default the embedded parser consumes.  So this script reads
the SAME `messages:` the CYD reads and injects them into the desktop widget,
then renders it with the Skia backend.  Both tiers therefore draw the SAME
thread content, and — the load-bearing claim — the per-bubble word-wrap is
identical (proven independently by env:native_chat_wrap; this script prints the
desktop wrap so it can be eyeballed against the corpus).

    python3 python/scripts/render_chat_thread_parity.py [config.yaml] [out.png]
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "python"))

import yaml  # noqa: E402

from composeio.render.skia_backend import SkiaCanvas          # noqa: E402
from composeio.render.widget import Rect                      # noqa: E402
from composeio.render.widgets.chat_thread import (            # noqa: E402
    ChatThreadWidget, _Message,
)

DEFAULT_CFG = os.path.join(
    REPO, "python", "composeio", "render", "examples",
    "chat_thread_parity.compose.yaml")
DEFAULT_OUT = "/tmp/chat_thread_parity_ref.png"


def _rgb(v, default):
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (int(v[0]), int(v[1]), int(v[2]))
    return default


def _inject(widget: ChatThreadWidget, messages: list) -> None:
    """Resolve each static config turn to a desktop _Message the SAME way
    on_bus_state does (voice -> colour/align), without the live receipt-time
    auto-stamp (deterministic reference)."""
    for m in messages:
        voice = str(m.get("voice", "david")).lower()
        sender = str(m.get("sender") or m.get("from") or "").strip()
        text = str(m.get("text", ""))
        payload = {"from": sender, "text": text}
        if m.get("kind"):
            payload["kind"] = str(m["kind"])
        if voice in ("reply",):
            msg = _Message(sender=sender or "architect", text=text,
                           color=widget.accent, align="left",
                           dim_sender=False, voice="reply", payload=payload)
        elif voice in ("narration", "narrate"):
            msg = _Message(sender=sender or "architect", text=text,
                           color=widget.dim, align="left",
                           dim_sender=True, voice="narration", payload=payload)
        elif voice == "harness":
            msg = _Message(sender="harness", text=text, color=widget.harness,
                           align="center", dim_sender=False, voice="harness",
                           kind=str(m.get("kind") or ""), payload=payload)
        else:
            msg = _Message(sender=sender or "david", text=text,
                           color=widget.neutral, align="left",
                           dim_sender=False, voice="david", payload=payload)
        widget._messages.append(msg)


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CFG
    out_png = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    design = cfg.get("design", {})
    width = int(design.get("width", 360))
    height = int(design.get("height", 420))
    bg = _rgb(design.get("background"), (7, 10, 15))

    spec = None
    for w in cfg.get("widgets", []):
        if w.get("type") in ("chat_thread", "chatthread"):
            spec = w
            break
    if spec is None:
        print("no chat_thread widget in config")
        return 1

    x = float(spec.get("x", 0))
    y = float(spec.get("y", 0))
    ww = float(spec.get("width", 320))
    wh = float(spec.get("height", 380))
    font_size = float(spec.get("font_size", 12))
    messages = spec.get("messages", []) or []

    widget = ChatThreadWidget(
        str(spec.get("id", "console")),
        label=str(spec.get("label", "Intelligence Console")),
        max_messages=int(spec.get("max_messages", 200)),
        font_size=font_size)
    _inject(widget, messages)

    canvas = SkiaCanvas()
    canvas.create_surface(width, height)
    canvas.set_fill_color(*bg)
    canvas.fill_rect(0, 0, width, height)
    widget.on_mount(None)
    widget.draw(canvas, Rect(x, y, ww, wh))
    canvas.save_png(out_png)

    # -- print the per-bubble wrap (the parity artifact) --------------------
    content_w = max(60.0, ww - 2.0 * max(18.0, min(40.0, ww * 0.04)))
    cap = min(content_w * 0.78, 560.0)
    body_w = cap - 2 * 14.0
    print(f"rendered {out_png}  ({width}x{height})")
    print(f"chat_thread width={ww:g}  content_w={content_w:g}  "
          f"bubble body_w={body_w:g}px (wrap width)\n")
    for i, m in enumerate(widget._messages):
        if m.voice == "harness":
            print(f"[{i}] harness ({m.kind or 'packet'}) — packet path")
            continue
        vlines = widget._layout_body(canvas, m.text, body_w, font_size)
        lines = ["".join(r.text for r in vl.runs).rstrip(" ") for vl in vlines]
        print(f"[{i}] {m.voice:9s} {m.sender!r} wraps to {len(lines)} line(s):")
        for ln in lines:
            print(f"        |{ln}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
