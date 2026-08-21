#!/usr/bin/env python3
"""Per-APP bus topics — the `_app/<app_id>/…` family ([SPEC:CXC-FLEET] [CXC-FC-S4-BBOX]).

THE SINGLETON THIS REMOVES.  `runtime.py` carried `CONSOLE_BBOX_NS = "_app/console/bbox"` as a
literal, so EVERY scene the render engine launched published its window geometry to a topic
named `console`.  Measured 2026-08-12: FOUR apps clobbering one topic at 2 Hz, and
[DEC-LAUNCH-PLACEMENT]'s consumer reads whichever published last — so a launched window's
placement depended on which app happened to tick most recently.

WHY THIS IS A SMALL CHANGE.  The seam already existed: the runtime already read a
`COMPOSEIO_CONSOLE_BBOX_NS` override and already gated publication on a launched-child sentinel.
Only the DEFAULT was a constant.  The fix is to DERIVE the default from the running app's id
(`Composition.app_id`, which every scene already declares under `app: id:`) — not to build a new
mechanism.

WHY THE CONSUMERS IMPORT FROM HERE.  Five out-of-process launchers each spelled
`BBOX_NS = "_app/console/bbox"` for themselves.  Deriving the producer's topic while leaving five
hardcoded consumers would have moved the publish somewhere nobody listens — a silent placement
regression.  One function, one console-app-id constant, every party importing them: that is
[CXC-FC-ANTI]'s reuse-by-reference applied to a topic name.
"""
from __future__ import annotations

import os

__all__ = ["app_bbox_ns", "console_app_id", "console_bbox_ns", "APP_TOPIC_PREFIX"]

APP_TOPIC_PREFIX = "_app"

# The console scene's declared `app: id:` (`compose_projects/intelligence/console/console.compose.yaml`).  Named
# HERE rather than re-read from YAML by five launchers: a consumer needs the string, not the
# scene.  `COMPOSEIO_CONSOLE_APP_ID` overrides for an operator running a renamed console.
_DEFAULT_CONSOLE_APP_ID = "intelligence-console"


def console_app_id() -> str:
    """The app id whose bbox the window launchers follow."""
    return os.environ.get("COMPOSEIO_CONSOLE_APP_ID", _DEFAULT_CONSOLE_APP_ID).strip() \
        or _DEFAULT_CONSOLE_APP_ID


def app_bbox_ns(app_id: str) -> str:
    """`_app/<app_id>/bbox` — where the app called `app_id` publishes its screen geometry.

    An empty/blank app id falls back to `"app"` rather than to `"console"`: a nameless scene
    must not be able to claim the console's topic, which is the collision this node closes.
    """
    aid = str(app_id or "").strip() or "app"
    return f"{APP_TOPIC_PREFIX}/{aid}/bbox"


def console_bbox_ns() -> str:
    """The console's own bbox topic — what a launcher subscribes."""
    return app_bbox_ns(console_app_id())
