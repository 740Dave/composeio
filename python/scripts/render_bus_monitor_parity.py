#!/usr/bin/env python3
"""
render_bus_monitor_parity.py — render the DESKTOP bus_monitor reference from the
CYD parity config ([CXC-PRIM-TIER-PARITY]) and print the formatted log lines.

The engine parser wires bus_monitor to the LIVE bus (bus.on_message); it does
not read a static `lines:` list — that is the CYD-tier render default the
embedded parser consumes.  So this script reads the SAME `lines:` the CYD reads
and injects them into the desktop widget's line ring the SAME way
_on_bus_message formats + colours them (stamp used verbatim, show_heartbeats
filter honoured), then renders it with the Skia backend.  Both tiers therefore
draw the SAME log content with the SAME per-type colours; when the log FITS its
box (the top monitor) the block is pixel-identical.

    python3 python/scripts/render_bus_monitor_parity.py [config.yaml] [out.png]
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
from composeio.render.widgets.bus_monitor import (            # noqa: E402
    BusMonitorWidget, _TYPE_COLORS, _OTHER,
)

DEFAULT_CFG = os.path.join(
    REPO, "cyd", "test", "configs", "bus_monitor_parity.compose.yaml")
DEFAULT_OUT = "/tmp/bus_monitor_parity_ref.png"

_GRAY_TYPES = ("heartbeat", "time_sync")


def _rgb(v, default):
    if isinstance(v, (list, tuple)) and len(v) >= 3:
        return (int(v[0]), int(v[1]), int(v[2]))
    if isinstance(v, str) and v.startswith("#") and len(v) in (7, 9):
        return (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
    return default


def _inject(widget: BusMonitorWidget, lines: list) -> None:
    """Format each static config entry into a (text, color) line the SAME way
    bus_monitor._on_bus_message does — stamp used verbatim (no live receipt-time
    auto-stamp, for a deterministic reference), show_heartbeats filter applied."""
    for m in lines:
        type_str = str(m.get("type", "")).strip()
        if not widget.show_heartbeats and type_str in _GRAY_TYPES:
            continue
        if m.get("text") is not None:
            line = str(m["text"])
        else:
            stamp = str(m.get("stamp") or m.get("timestamp") or "")
            ns = str(m.get("namespace", ""))
            value = m.get("value")
            value_str = str(value) if value is not None else ""
            source = str(m.get("source", "") or "")
            line = f"[{stamp}] {ns}: {type_str} {value_str}"
            if source:
                line += f" ({source})"
        color = _TYPE_COLORS.get(type_str, _OTHER)
        widget._lines.append((line, color))
        if len(widget._lines) > widget.max_lines:
            del widget._lines[:len(widget._lines) - widget.max_lines]


def main() -> int:
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_CFG
    out_png = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT

    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    design = cfg.get("design", {})
    width = int(design.get("width", 240))
    height = int(design.get("height", 320))
    bg = _rgb(design.get("background"), (30, 30, 46))

    canvas = SkiaCanvas()
    canvas.create_surface(width, height)
    canvas.set_fill_color(*bg)
    canvas.fill_rect(0, 0, width, height)

    print(f"rendering {out_png}  ({width}x{height})\n")
    for spec in cfg.get("widgets", []):
        if spec.get("type") not in ("bus_monitor", "busmonitor"):
            continue
        x = float(spec.get("x", 0))
        y = float(spec.get("y", 0))
        ww = float(spec.get("w", spec.get("width", 320)))
        wh = float(spec.get("h", spec.get("height", 240)))
        widget = BusMonitorWidget(
            str(spec.get("id", "bus_monitor")),
            label=str(spec.get("label", "Bus Monitor")),
            max_lines=int(spec.get("max_lines", 150)),
            show_heartbeats=bool(spec.get("show_heartbeats", False)))
        _inject(widget, spec.get("lines", spec.get("messages", [])) or [])
        widget.on_mount(None)
        widget.draw(canvas, Rect(x, y, ww, wh))
        print(f"[{widget.widget_id}] label={widget.label!r}  "
              f"show_heartbeats={widget.show_heartbeats}  "
              f"{len(widget._lines)} line(s):")
        for text, color in widget._lines:
            print(f"        #{color[0]:02X}{color[1]:02X}{color[2]:02X}  |{text}")
        print()

    canvas.save_png(out_png)
    print(f"saved {out_png}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
