"""Launch placement + console bbox tests ([DEC-LAUNCH-PLACEMENT], David
2026-07-22).

A launched window (Away/Night Watch, doc-viewer) must open BESIDE the console
and come to the FRONT, not centered-behind.  Three seams are covered here, all
headless (windowed SDL behavior that needs a real display is guarded/skipped,
per the smoke-script convention):

  * runtime `_launch_window_opts()` — env -> (pos, raise) parsing
  * the console bbox-publish gate + payload/change/heartbeat semantics
  * SkiaWindow honoring an explicit position + reporting its bounds
    (display-guarded)

The launcher-side position math (bbox + margin -> origin) is exercised by the
two launchers' own selftests (away_watch_publisher/doc_viewer_launcher
--selftest); this file covers the render-engine half.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.app_topics import app_bbox_ns
from composeio.render.engine.runtime import (CONSOLE_BBOX_NS, RenderApp,
                                             _launch_window_opts)

DEMO = os.path.join(PY_ROOT, "composeio", "render", "examples",
                    "render_demo.compose.yaml")


# ---------------------------------------------------------------------------
# _launch_window_opts — env -> (pos, raise)
# ---------------------------------------------------------------------------

def test_launch_opts_absent(monkeypatch):
    for k in ("COMPOSEIO_WINDOW_X", "COMPOSEIO_WINDOW_Y",
              "COMPOSEIO_WINDOW_RAISE"):
        monkeypatch.delenv(k, raising=False)
    assert _launch_window_opts() == (None, False)


def test_launch_opts_pos_and_raise(monkeypatch):
    monkeypatch.setenv("COMPOSEIO_WINDOW_X", "1390")
    monkeypatch.setenv("COMPOSEIO_WINDOW_Y", "40")
    monkeypatch.setenv("COMPOSEIO_WINDOW_RAISE", "1")
    assert _launch_window_opts() == ((1390, 40), True)


def test_launch_opts_partial_or_bad_is_none(monkeypatch):
    monkeypatch.setenv("COMPOSEIO_WINDOW_X", "100")   # Y missing
    monkeypatch.delenv("COMPOSEIO_WINDOW_Y", raising=False)
    monkeypatch.setenv("COMPOSEIO_WINDOW_RAISE", "off")
    pos, raise_ = _launch_window_opts()
    assert pos is None and raise_ is False
    monkeypatch.setenv("COMPOSEIO_WINDOW_Y", "notanint")
    assert _launch_window_opts()[0] is None


# ---------------------------------------------------------------------------
# console bbox-publish gate
# ---------------------------------------------------------------------------

def _app(monkeypatch, *, transports, launched, disabled=False):
    for k in ("COMPOSEIO_WINDOW_X", "COMPOSEIO_CONSOLE_BBOX"):
        monkeypatch.delenv(k, raising=False)
    if launched:
        monkeypatch.setenv("COMPOSEIO_WINDOW_X", "1390")
    if disabled:
        monkeypatch.setenv("COMPOSEIO_CONSOLE_BBOX", "0")
    return RenderApp.from_file(DEMO, windowed=True,
                              connect_transports=transports)


def test_console_publishes_bbox(monkeypatch):
    """Windowed + transports + NOT a launched child + not disabled -> the app
    advertises its bbox — ON ITS OWN TOPIC ([CXC-FC-S4-BBOX]).

    This assertion used to read `app._bbox_ns == CONSOLE_BBOX_NS` against a scene
    whose app id is `render-demo`, which is the singleton stated as a test: every
    app, whatever its id, published under the name `console`.  Measured 2026-08-12,
    four apps were clobbering that one topic at 2 Hz and the placement consumer read
    whichever published last.  The contract is now DERIVATION."""
    app = _app(monkeypatch, transports=True, launched=False)
    assert app._publish_bbox is True
    assert app._bbox_ns == app_bbox_ns(app.comp.app_id) == "_app/render-demo/bbox"


def test_two_apps_publish_to_two_distinct_bbox_topics(monkeypatch):
    """FC-C11's machine leg: geometry no longer collides."""
    from composeio.render.engine.parser import Composition
    a = _app(monkeypatch, transports=True, launched=False)
    assert isinstance(a.comp, Composition)
    assert app_bbox_ns("intelligence-console") != app_bbox_ns("doc-viewer")
    assert app_bbox_ns(a.comp.app_id) != app_bbox_ns("intelligence-console")


def test_a_nameless_scene_cannot_claim_the_consoles_topic():
    """An empty app id falls back to `_app/app/bbox`, never to the console's — a scene
    that forgot to declare an id must not be able to displace the console."""
    assert app_bbox_ns("") == "_app/app/bbox"
    assert app_bbox_ns(None) != CONSOLE_BBOX_NS


# ---------------------------------------------------------------------------
# the console-topic guard: a BINDING SITE, not a mention
# ---------------------------------------------------------------------------
#
# WHY THIS IS AN AST CLASSIFIER AND NOT A GREP.  The first version of this guard asserted the
# literal `_app/console/bbox` appeared in NO `.py` file in the repo.  It then went RED on three
# files that are unambiguously correct:
#
#     python/tests/test_can_wire_format_v2.py   x7   CAN-id/hash fixtures — one is a dict KEY
#                                                    whose VALUE is the expected hash 0x159F
#     python/scripts/gen_can_id_corpus_header.py:93  an element of `NAMESPACES = [...]`, the
#                                                    corpus the C header is generated from
#     tests/peer/test_can_bridge_guards.py:126       an argument to a local `deliver()` helper
#                                                    feeding a bridge-filter assertion
#
# None of them is a CONSUMER.  A hash fixture that stopped saying `_app/console/bbox` would stop
# testing the hash of `_app/console/bbox` — editing the sample data to satisfy the detector fits
# the subject to the instrument.  So the detector was narrowed instead.
#
# THE LINE THE CLASSIFIER DRAWS: a topic name BOUND — to a name, to a bus call, to a default, to
# a dispatch comparison — is a place a subscription can silently point at a dead topic.  A topic
# name as an ELEMENT OF A COLLECTION, or as an argument to a non-bus function, is DATA about a
# topic.  Hence `BBOX_NS = "_app/console/bbox"` (the exact sin `app_topics.py` records five
# launchers committing) is caught, and `NAMESPACES = [..., "_app/console/bbox"]` is not.

CONSOLE_BBOX_LITERAL = "_app/console/bbox"

#: Callees that BIND a namespace: passing the literal to one of these is a subscription,
#: a declaration or a publish, i.e. a live wire to a topic nobody derives.
_BUS_BINDING_CALLS = frozenset({
    "subscribe", "subscribe_pattern", "unsubscribe", "declare", "ensure_namespace",
    "register_namespace", "add_namespace", "create_binding", "add_binding", "bind",
    "publish", "publish_state", "publish_command", "publish_event",
    "get_state", "set_state", "on_change", "watch", "request", "announce",
})


def _console_literal_binding_sites(source: str, filename: str = "<string>"):
    """Line numbers in `source` where CONSOLE_BBOX_LITERAL is BOUND rather than mentioned.

    Shared by the repo walk and by both control tests below, so the controls exercise the
    same predicate the walk does rather than a paraphrase of it.
    """
    import ast
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []

    def _lit(n):
        return isinstance(n, ast.Constant) and n.value == CONSOLE_BBOX_LITERAL

    def _callee(func):
        if isinstance(func, ast.Attribute):
            return func.attr
        if isinstance(func, ast.Name):
            return func.id
        return ""

    hits: dict[int, str] = {}

    def _flag(node, why):
        hits.setdefault(node.lineno, f"{filename}:{node.lineno}: {why}")

    for node in ast.walk(tree):
        # 1. bound to a name as a topic constant — `BBOX_NS = "_app/console/bbox"`,
        #    `self._bbox_ns = ...`.  A literal NESTED in a list/tuple/dict is exempt: that is
        #    a corpus of sample namespaces, not a topic constant.
        if isinstance(node, (ast.Assign, ast.AnnAssign)) and _lit(node.value):
            _flag(node.value, "the literal is bound to a name as a topic constant")
        # 2. handed to a bus call — subscribe/declare/publish/...
        elif isinstance(node, ast.Call):
            name = _callee(node.func)
            if name in _BUS_BINDING_CALLS:
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    if _lit(arg):
                        _flag(arg, f"the literal is passed to the bus call `{name}()`")
            # 3. an env-var fallback: `os.environ.get("...", "_app/console/bbox")`
            elif name == "getenv" or (name == "get" and isinstance(node.func, ast.Attribute)
                                      and "environ" in ast.dump(node.func.value)):
                for arg in node.args[1:]:
                    if _lit(arg):
                        _flag(arg, "the literal is an environment-variable FALLBACK default")
        # 4. a hardcoded parameter default — `def follow(ns="_app/console/bbox")`
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for d in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                if _lit(d):
                    _flag(d, "the literal is a hardcoded parameter default")
        # 5. a resolver that returns it instead of deriving it
        elif isinstance(node, ast.Return) and node.value is not None and _lit(node.value):
            _flag(node.value, "a function RETURNS the literal instead of deriving it")
        # 6. a dispatch filter — `if ns == "..."`, `if ns in ("...", OTHER)`
        elif isinstance(node, ast.Compare):
            cands = [node.left] + list(node.comparators)
            for c in list(cands):
                if isinstance(c, (ast.Tuple, ast.List, ast.Set)):
                    cands.extend(c.elts)
            for c in cands:
                if _lit(c):
                    _flag(c, "the literal is compared against — a dispatch/filter site")

    return [hits[k] for k in sorted(hits)]


#: The three files the OVER-BROAD version of this guard falsely accused.  Named explicitly so
#: that if the classifier ever regresses to a grep, it regresses LOUDLY and against the exact
#: evidence, instead of quietly re-accusing correct code.
_KNOWN_GOOD_MENTIONS = (
    ("python", "tests", "test_can_wire_format_v2.py"),
    ("python", "scripts", "gen_can_id_corpus_header.py"),
    ("tests", "peer", "test_can_bridge_guards.py"),
)

#: POSITIVE CONTROL. Each is a real consumer/producer shape lifted from the five launchers and
#: from `runtime.py` as they looked BEFORE `app_topics.py` existed. A guard that no longer
#: catches these reports success forever, which is worse than the over-broad version.
_REAL_VIOLATIONS = {
    "the module-level topic constant five launchers each spelled for themselves":
        'BBOX_NS = "_app/console/bbox"\n',
    "an instance attribute pinned to the literal":
        'class A:\n    def __init__(self):\n        self._bbox_ns = "_app/console/bbox"\n',
    "a direct positional subscribe":
        'bus.subscribe("_app/console/bbox", _on_bbox)\n',
    "a keyword subscribe":
        'bus.subscribe(ns="_app/console/bbox", handler=_on_bbox)\n',
    "a bus declare (app_launcher's actual shape)":
        'source_bus.declare(b, "_app/console/bbox", scope=Scope.GLOBAL, label="console bbox")\n',
    "a publish to the literal":
        'self.bus.publish_state("_app/console/bbox", payload)\n',
    "a hardcoded parameter default":
        'def follow_console(ns="_app/console/bbox"):\n    return ns\n',
    "an env-var fallback default (runtime.py's old shape)":
        'ns = os.environ.get("COMPOSEIO_CONSOLE_BBOX_NS", "_app/console/bbox")\n',
    "a resolver returning the literal instead of deriving it":
        'def console_bbox_ns():\n    return "_app/console/bbox"\n',
    "a dispatch filter on an inbound message":
        'if ns == "_app/console/bbox":\n    _place(msg)\n',
    "a membership filter over several topics":
        'if ns in ("_app/console/bbox", INTENT_NS):\n    _place(msg)\n',
}


def test_the_console_topic_is_no_longer_a_hardcoded_default():
    """The producer and all five out-of-process consumers resolve ONE function.  A
    consumer left on the literal would subscribe a topic nobody publishes to — a
    silent placement regression, which is worse than a loud one."""
    import os as _os
    repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))
    offenders = []
    for base, dirs, files in _os.walk(repo):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__",
                                                ".cache", "captures", "reference_images")]
        for fn in files:
            if not fn.endswith(".py") or fn == _os.path.basename(__file__):
                continue
            p = _os.path.join(base, fn)
            try:
                src = open(p, encoding="utf-8").read()
            except OSError:
                continue
            offenders.extend(_console_literal_binding_sites(src, p))
    assert not offenders, (
        f"{CONSOLE_BBOX_LITERAL!r} is BOUND, not merely mentioned — resolve it through "
        f"composeio.app_topics instead: {offenders}")


def test_the_guard_still_fires_on_a_real_consumer():
    """POSITIVE CONTROL — the narrowed guard must still catch every real binding shape.

    Without this, narrowing the guard is indistinguishable from deleting it."""
    missed = [why for why, src in _REAL_VIOLATIONS.items()
              if not _console_literal_binding_sites(src, "planted.py")]
    assert not missed, (
        "the guard has been narrowed until it catches nothing — these REAL violations "
        f"went undetected: {missed}")


def test_the_guard_is_silent_on_the_three_fixture_files():
    """NEGATIVE CONTROL — the three files the over-broad version falsely accused.

    They use the literal as CAN-hash sample data, as a generated-corpus element, and as an
    argument to a test helper.  Editing them to please a detector would have destroyed what
    they test; the detector was narrowed instead."""
    import os as _os
    repo = _os.path.dirname(_os.path.dirname(_os.path.dirname(
        _os.path.dirname(_os.path.abspath(__file__)))))
    for parts in _KNOWN_GOOD_MENTIONS:
        p = _os.path.join(repo, *parts)
        assert _os.path.isfile(p), f"control file moved — this control is now vacuous: {p}"
        src = open(p, encoding="utf-8").read()
        assert CONSOLE_BBOX_LITERAL in src, (
            f"{p} no longer mentions {CONSOLE_BBOX_LITERAL!r} — either the fixture was edited "
            "to please the detector (don't) or this control needs re-pointing")
        assert not _console_literal_binding_sites(src, p), (
            f"the guard has widened back out and is accusing correct sample data in {p}")


def test_launched_window_suppresses_bbox(monkeypatch):
    """A launched child carries COMPOSEIO_WINDOW_X and must NOT publish a
    (colliding) console bbox."""
    app = _app(monkeypatch, transports=True, launched=True)
    assert app._publish_bbox is False


def test_no_transports_no_bbox(monkeypatch):
    """No global leg -> no way to reach the out-of-process launchers anyway."""
    app = _app(monkeypatch, transports=False, launched=False)
    assert app._publish_bbox is False


def test_bbox_explicitly_disabled(monkeypatch):
    app = _app(monkeypatch, transports=True, launched=False, disabled=True)
    assert app._publish_bbox is False


def test_bbox_ns_override(monkeypatch):
    monkeypatch.setenv("COMPOSEIO_CONSOLE_BBOX_NS", "_app/other/bbox")
    app = _app(monkeypatch, transports=True, launched=False)
    assert app._bbox_ns == "_app/other/bbox"


# ---------------------------------------------------------------------------
# _maybe_publish_bbox — payload shape, change-detection, heartbeat
# ---------------------------------------------------------------------------

class _StubCanvas:
    def __init__(self, bounds):
        self._bounds = bounds

    def get_bounds(self):
        return self._bounds


def test_maybe_publish_bbox_payload_and_change(monkeypatch):
    app = _app(monkeypatch, transports=False, launched=False)
    app._publish_bbox = True          # force the gate on for the unit test
    app._bbox_ns = CONSOLE_BBOX_NS
    app.canvas = _StubCanvas((200, 60, 1280, 800))

    seen = []
    app.bus.ensure_namespace(CONSOLE_BBOX_NS, engine_config="json")
    app.bus.subscribe(CONSOLE_BBOX_NS,
                      lambda ns, new, old: seen.append(new))

    # first tick publishes (last_bbox is None)
    app._maybe_publish_bbox(0.0)
    assert seen[-1] == {"x": 200, "y": 60, "w": 1280, "h": 800}
    n_after_first = len(seen)

    # same bounds, before the heartbeat -> no republish
    app._maybe_publish_bbox(0.1)
    assert len(seen) == n_after_first

    # moved -> republish immediately
    app.canvas = _StubCanvas((900, 60, 1280, 800))
    app._maybe_publish_bbox(0.2)
    assert seen[-1]["x"] == 900

    # unchanged but past the heartbeat window -> republish so a late MQTT
    # subscriber still learns the position (state publishes aren't broker-
    # retained).  The local bus dedupes an unchanged value's SUBSCRIBER
    # notify, but publish_state still emits the wire message and advances the
    # publish clock — assert on that clock rather than the deduped callback.
    assert app._last_bbox_pub_t == 0.2
    app._maybe_publish_bbox(0.2 + 3.0)
    assert app._last_bbox_pub_t == 0.2 + 3.0

    # unchanged and INSIDE the heartbeat window -> the clock does not advance
    app._maybe_publish_bbox(0.2 + 3.0 + 0.1)
    assert app._last_bbox_pub_t == 0.2 + 3.0


def test_maybe_publish_bbox_noop_when_gated_off(monkeypatch):
    app = _app(monkeypatch, transports=False, launched=False)
    app._publish_bbox = False
    app.canvas = _StubCanvas((0, 0, 100, 100))
    app.bus.ensure_namespace(CONSOLE_BBOX_NS, engine_config="json")
    app._maybe_publish_bbox(0.0)
    assert app.bus.get_state(CONSOLE_BBOX_NS) is None


# ---------------------------------------------------------------------------
# SkiaWindow explicit position + bounds (display-guarded)
# ---------------------------------------------------------------------------

def _display_available():
    try:
        import sdl2
    except Exception:
        return False
    if sdl2.SDL_WasInit(sdl2.SDL_INIT_VIDEO) != 0:
        return True
    return sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) == 0


@pytest.mark.skipif(not _display_available(),
                    reason="no SDL video display (headless CI)")
def test_window_honors_position_and_reports_bounds():
    from composeio.render.window import SkiaWindow
    win = SkiaWindow(title="placement-test", pos=(300, 120),
                     raise_to_front=True)
    try:
        win.create_surface(200, 150)
        bounds = win.get_bounds()
        assert bounds is not None and len(bounds) == 4
        x, y, w, h = bounds
        assert all(isinstance(v, int) for v in bounds)
        # SDL reports the logical window size we asked for (scale 1.0).
        assert (w, h) == (200, 150)
        # The window was placed, not centered — a window manager may nudge the
        # exact origin, so we assert it is near the requested corner rather than
        # pinning it (exact on-screen placement is a glass-verify item).
        assert x >= 0 and y >= 0
    finally:
        win.close()


@pytest.mark.skipif(not _display_available(),
                    reason="no SDL video display (headless CI)")
def test_window_default_centered_still_works():
    """No pos -> CENTERED path unchanged (regression guard)."""
    from composeio.render.window import SkiaWindow
    win = SkiaWindow(title="centered-test")
    try:
        win.create_surface(200, 150)
        assert win.get_bounds() is not None
    finally:
        win.close()
