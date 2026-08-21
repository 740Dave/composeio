"""The GLASS POLICY — headless by DEFAULT, glass by ANNOUNCED OPT-IN.

⛔ WHY THIS EXISTS, MEASURED 2026-08-16.  For weeks the Intelligence Console
"randomly" popped two windows onto David's desktop which then died shortly
after.  Three suspects were chased and wrongly accused — a login LaunchAgent, a
bus-commanded `console_supervisor`, and retained MQTT commands.  The real cause:

    `SDL_VIDEODRIVER=dummy` was set by `scripts/pre-commit-tests.sh` AND BY NO
    `conftest.py` ANYWHERE.

So a commit was headless and a BARE `pytest` was not.  Every agent in the fleet
is told to run the suites to verify its work, and each of those runs reached out
and drew on the developer's live desktop.  "Random" was simply "whenever anybody
verified anything", from several concurrent sessions.

⭐ THE GENERALISATION, and it is the same shape as two other defects found the
same night: A GUARD THAT LIVES IN ONE INVOCATION PATH IS NOT A GUARD.  It is a
property of that path.  bus_storage's path-containment lived inside one handler
and six siblings could not reach it; the hookup crossing-report was computed and
discarded at its only call site.  Lift the guard to the seam every caller
crosses — here, `conftest.py`, which pytest loads however it is invoked.

⛔ AND THE POINT THAT MAKES THIS SUBTLE (David, 2026-08-16):

    "there are times when tests *must* pop content to the screen.  It should
     just be coordinated and notified in advance."

He is right, and it is not a caveat — it is the project's own
`[PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]`: for any visual output human eyes
are LOAD-BEARING, and a fully green machine tier can sit on a dead board.  A
policy of "always headless" would quietly delete the only check that catches a
byte-swapped panel or an illegible wiring diagram.  So the answer is not
suppression, it is CONSENT: glass tests are opt-in, and when they run they
ANNOUNCE THEMSELVES FIRST, so a window that appears is one somebody asked for.

HOW TO USE IT
-------------
Default (any invocation, any suite) — headless, no windows::

    pytest tests
    pytest python/tests -k render

A test that genuinely needs pixels on real glass::

    @pytest.mark.needs_glass("renders the console scene at 1280x1300 for a "
                             "colour-order read; ~4s, one window")
    def test_panel_colour_order():
        ...

Such a test is SKIPPED unless glass is explicitly enabled::

    COMPOSEIO_GLASS=1 pytest tests -m needs_glass

⛔ NEVER CLOBBER AN EXPLICIT CHOICE.  If the caller already exported
`SDL_VIDEODRIVER` (debugging a real window, or forcing a different backend), it
is left exactly as found and the origin is reported.  This mirrors the existing
discipline in `scripts/pre-commit-tests.sh`, which carries the same rule with
the same warning — the policy is now shared rather than duplicated.
"""

from __future__ import annotations

import os
import sys

#: The env var the caller sets to consent to windows appearing.
GLASS_ENV = "COMPOSEIO_GLASS"

#: The marker a test carries when it genuinely needs real glass.
GLASS_MARKER = "needs_glass"


def glass_enabled() -> bool:
    """True when the caller has explicitly consented to on-screen windows."""
    return os.environ.get(GLASS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def apply_headless_default() -> tuple[str, str]:
    """Default SDL to the dummy video driver, never clobbering an explicit value.

    Returns ``(driver, origin)`` where origin is one of ``inherited`` (the caller
    chose it), ``glass-consented`` (glass mode, left alone deliberately) or
    ``defaulted`` (we set it).
    """
    explicit = os.environ.get("SDL_VIDEODRIVER")
    if explicit:
        return explicit, "inherited"
    if glass_enabled():
        # Glass consented: do NOT force dummy, or the opt-in could not work.
        return os.environ.get("SDL_VIDEODRIVER", "<platform default>"), "glass-consented"
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    return "dummy", "defaulted"


def register(config) -> None:
    """Register the marker and apply the headless default. Call from conftest."""
    config.addinivalue_line(
        "markers",
        f"{GLASS_MARKER}(reason): this test draws on REAL GLASS. Skipped unless "
        f"{GLASS_ENV}=1. The reason string is announced before the run so a "
        f"window that appears is one somebody asked for.",
    )
    driver, origin = apply_headless_default()
    # Announce on stderr: visible however pytest is invoked, including -q.
    if origin == "defaulted":
        print(f"[glass-policy] headless: SDL_VIDEODRIVER=dummy (defaulted) — "
              f"no window will open. Set {GLASS_ENV}=1 to allow glass tests.",
              file=sys.stderr)
    elif origin == "inherited":
        print(f"[glass-policy] SDL_VIDEODRIVER={driver} (INHERITED — your explicit "
              f"choice, not clobbered)", file=sys.stderr)
    else:
        print(f"[glass-policy] ⛔ GLASS ENABLED ({GLASS_ENV}=1) — tests marked "
              f"{GLASS_MARKER} MAY OPEN WINDOWS ON YOUR DESKTOP.", file=sys.stderr)


def announce_and_gate(config, items) -> None:
    """Skip glass tests unless consented; ANNOUNCE the ones that will run.

    ⛔ The announcement is the half David asked for and the half a plain skip
    marker does not give you: when glass IS enabled, every test that will draw
    says so, with its reason, BEFORE the first window appears.
    """
    import pytest

    glass_items = [it for it in items if it.get_closest_marker(GLASS_MARKER)]
    if not glass_items:
        return

    if not glass_enabled():
        skip = pytest.mark.skip(
            reason=f"needs real glass; set {GLASS_ENV}=1 to run (headless by default)"
        )
        for it in glass_items:
            it.add_marker(skip)
        print(f"[glass-policy] {len(glass_items)} glass test(s) SKIPPED — headless. "
              f"{GLASS_ENV}=1 to run them.", file=sys.stderr)
        return

    # Consented: announce IN ADVANCE exactly what is about to appear.
    print(f"\n[glass-policy] ⛔ {len(glass_items)} test(s) WILL DRAW ON YOUR SCREEN:",
          file=sys.stderr)
    for it in glass_items:
        marker = it.get_closest_marker(GLASS_MARKER)
        reason = (marker.args[0] if marker.args else "no reason given — "
                  "⚠ add one; an unannounced window is the defect this policy exists to end")
        print(f"[glass-policy]   · {it.nodeid}\n[glass-policy]       {reason}",
              file=sys.stderr)
    print("[glass-policy] (nothing else in this run may open a window)\n", file=sys.stderr)
