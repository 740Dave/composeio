"""
SDL string OWNERSHIP across the ctypes boundary — the gate on the 2026-08-11 SIGABRT.

THE DEFECT (`window.clipboard_text`, the Cmd-V path of the Terminal Widget):

    raw = sdl2.SDL_GetClipboardText()      # restype c_char_p -> a Python bytes COPY
    ...
    sdl2.SDL_free(raw)                     # frees id(raw)+hdr, NOT SDL's buffer

PySDL2 declares `SDL_GetClipboardText` with `restype=c_char_p`, and ctypes converts a
`c_char_p` RESULT into a Python `bytes` copy — the SDL-malloc'd address is destroyed by
the conversion and never reaches Python.  Handing the `bytes` back to `SDL_free` (argtype
`c_void_p`) passes the INTERIOR `ob_sval` address of a live CPython object.  malloc never
returned that address, so `free()` -> `find_zone_and_free` finds no owning zone and the
macOS allocator aborts the process:

    abort <- malloc_report <- find_zone_and_free <- SDL2!SDL_free <- ffi_call_SYSV <- ceval

`abort()` does not unwind: no traceback, and the `except Exception:` around the call could
never have caught it.  It also LEAKED SDL's buffer on every paste (+94.7 MB measured over
20 000 reads of a 4 KB clipboard; +1.7 MB after the fix).

WHAT THIS FILE CAN AND CANNOT GATE
----------------------------------
The failure itself CANNOT be asserted in-process: a free of a non-heap pointer aborts the
test runner, and deliberately provoking it would scribble a crash report into the operator's
DiagnosticReports on every suite run.  So we gate the INVARIANTS that make it impossible:

  1. OWNERSHIP (a refcount assertion, not a proxy): SDL keeps an allocation counter,
     `SDL_GetNumAllocations`.  `take_sdl_string` must consume exactly one allocation and
     `clipboard_text` must be allocation-NEUTRAL.  The old code was +1 per read (the leak);
     a future double-free would abort here rather than in a human's window.
  2. THE TRAP STILL EXISTS: we assert, without freeing anything, that ctypes really does
     pass an object-interior address for a `bytes`.  If a future CPython/ctypes changed
     that, this test says so instead of the gate quietly becoming theatre.
  3. STRUCTURE: `SDL_free` and the clipboard entry points are reachable from exactly ONE
     module.  The abort existed because the read was FORKED into two copies and one of
     them got the free wrong.
"""

from __future__ import annotations

import ctypes
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

RENDER_PKG = os.path.join(PY_ROOT, "composeio", "render")

sdl2 = pytest.importorskip("sdl2")

from composeio.render import sdl_strings                       # noqa: E402


# ---------------------------------------------------------------------------
# SDL's own allocation counter — the ownership assertion
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def nalloc():
    """`SDL_GetNumAllocations` — SDL increments it in SDL_malloc and decrements it
    in SDL_free.  PySDL2 does not bind it, so bind it here (test-only)."""
    import sdl2.dll
    sdl2.SDL_Init(sdl2.SDL_INIT_EVENTS)
    try:
        fn = sdl2.dll.dll.bind_function("SDL_GetNumAllocations", None, ctypes.c_int)
    except Exception:                                    # pragma: no cover
        pytest.skip("SDL build without SDL_GetNumAllocations")
    return fn


def _sdl_strdup(data: bytes) -> int:
    """An SDL-malloc'd NUL-terminated copy of `data`; returns its ADDRESS."""
    buf = sdl2.SDL_malloc(len(data) + 1)
    ctypes.memmove(buf, data, len(data))
    ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))[len(data)] = b"\x00"
    return ctypes.cast(buf, ctypes.c_void_p).value


def test_take_sdl_string_returns_the_text_and_consumes_the_allocation(nalloc):
    addr = _sdl_strdup("héllo ✓".encode("utf-8"))
    before = nalloc()
    assert sdl_strings.take_sdl_string(addr) == "héllo ✓"
    assert nalloc() - before == -1, "the SDL buffer was not freed (the leak half)"


def test_take_sdl_string_frees_even_when_the_bytes_do_not_decode(nalloc):
    """Both error modes free.  `strict` yields "" — the drop-file path wants NO path
    rather than a mojibake one — and `replace` yields U+FFFD; neither may leak."""
    addr = _sdl_strdup(b"/tmp/bad\xff\xfe.png")
    before = nalloc()
    assert sdl_strings.take_sdl_string(addr, errors="strict") == ""
    assert nalloc() - before == -1

    addr = _sdl_strdup(b"\xff\xfe")
    before = nalloc()
    assert "�" in sdl_strings.take_sdl_string(addr, errors="replace")
    assert nalloc() - before == -1


def test_take_sdl_string_of_null_is_empty_and_frees_nothing(nalloc):
    before = nalloc()
    assert sdl_strings.take_sdl_string(0) == ""
    assert sdl_strings.take_sdl_string(None) == ""
    assert nalloc() - before == 0


def test_clipboard_text_is_allocation_neutral(nalloc):
    """The direct gate on the crash path: SDL allocates one string per read and the
    engine must give exactly that pointer back.  The pre-fix code was +1 per read.

    The pasteboard is PROCESS-GLOBAL and any app on the machine can rewrite it, so the
    round-trip is asserted once, immediately after the write, and the LOOP asserts only
    the invariant under test.  Neutrality holds whatever the clipboard contains (an empty
    clipboard still hands back a malloc'd ""), so a human copying something mid-loop
    cannot make this test lie in either direction."""
    if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:          # pragma: no cover
        pytest.skip("no SDL video driver (no display)")
    sdl_strings.clipboard_text()                             # warm any lazy init
    sdl2.SDL_SetClipboardText("round trip ✓".encode("utf-8"))
    assert sdl_strings.clipboard_text() == "round trip ✓"
    before = nalloc()
    for _ in range(50):
        sdl_strings.clipboard_text()
    assert nalloc() - before == 0, "clipboard reads leak or over-free SDL buffers"


def test_the_raw_getter_returns_a_POINTER_not_a_bytes():
    """The whole fix in one assertion: the re-prototyped entry point must have
    `restype=c_void_p`.  With `c_char_p` the address is destroyed by the conversion
    and there is nothing left that CAN be freed correctly."""
    getter = sdl_strings._clipboard_getter()
    assert getter.restype is ctypes.c_void_p
    # ...and PySDL2's shared binding is left untouched for every other caller.
    assert sdl2.SDL_GetClipboardText.restype is ctypes.c_char_p


def test_the_trap_still_exists_ctypes_passes_an_object_interior_pointer():
    """Documents WHY, and keeps this gate honest: if ctypes ever stopped passing the
    interior buffer address of a `bytes` for a `c_void_p` argument, the invariants above
    would be guarding a hazard that no longer exists — and this test would say so.

    Nothing is freed here.  `SDL_memcpy(dst, src, 0)` returns `dst`, which reveals the
    pointer ctypes actually put in the register."""
    b = b"clipboard-shaped bytes"
    scratch = ctypes.create_string_buffer(8)
    passed = sdl2.SDL_memcpy(b, scratch, 0)
    hdr = sys.getsizeof(b) - len(b) - 1           # PyBytesObject header, version-agnostic
    assert passed == id(b) + hdr, (
        "ctypes no longer passes the bytes' interior buffer — re-derive the hazard")
    # It is INSIDE a live Python object: not a pointer malloc ever returned.
    assert id(b) < passed < id(b) + sys.getsizeof(b)


# ---------------------------------------------------------------------------
# ONE owner — the structural half
# ---------------------------------------------------------------------------

def _render_sources():
    for root, _dirs, files in os.walk(RENDER_PKG):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                yield os.path.relpath(p, RENDER_PKG), open(p, encoding="utf-8").read()


def test_SDL_free_is_called_from_exactly_one_module():
    """The abort happened because the clipboard read was FORKED into two copies and the
    second one got the free wrong.  `sdl_strings` is the only module allowed to free."""
    callers = sorted({rel for rel, src in _render_sources() if "SDL_free(" in src})
    assert callers == ["sdl_strings.py"], (
        f"SDL_free escaped its owner: {callers}. Route the ADDRESS through "
        f"sdl_strings.take_sdl_string / sdl_free_ptr instead.")


def test_the_clipboard_entry_points_have_exactly_one_caller():
    users = sorted({rel for rel, src in _render_sources()
                    if "SDL_GetClipboardText" in src or "SDL_SetClipboardText" in src})
    assert users == ["sdl_strings.py"], f"a second clipboard reader appeared: {users}"


def test_every_engine_clipboard_caller_shares_the_one_reader():
    """Behavioural half of "one reader": the window re-export, the terminal widget's
    Cmd-V and the text-input paste must all land on `sdl_strings.clipboard_text`."""
    pytest.importorskip("skia")
    from composeio.render import window
    from composeio.render.widgets import text_input as ti

    # `window.clipboard_text` is a RE-EXPORT of the one reader, not a second copy.
    assert window.clipboard_text is sdl_strings.clipboard_text

    # text_input resolves the reader at CALL time (lazy, so a headless run never
    # imports sdl2) — patching the owner proves it delegates rather than duplicates.
    calls = []
    real = sdl_strings.clipboard_text
    sdl_strings.clipboard_text = lambda: (calls.append("text_input") or "PASTED")
    try:
        assert ti._clipboard_get() == "PASTED"
    finally:
        sdl_strings.clipboard_text = real

    # The terminal widget's Cmd-V reads through `window` (also at call time).
    from composeio.render.widgets.terminal import TerminalWidget
    real_w = window.clipboard_text
    window.clipboard_text = lambda: (calls.append("terminal") or "PASTED")
    try:
        assert TerminalWidget("t1").clipboard_text() == "PASTED"
    finally:
        window.clipboard_text = real_w

    assert calls == ["text_input", "terminal"]


# ---------------------------------------------------------------------------
# The drop-file path — the OTHER SDL-owned string, through the real pump_events
# ---------------------------------------------------------------------------

class _Stub:
    """The minimum `pump_events` touches.  SDL's event queue is PROCESS-GLOBAL, so the
    DROPFILE branch runs with no window — the REAL code, not a transcription of it."""
    _open = True
    _window = None
    _width = 100.0
    _height = 100.0


def _push_dropfile(path: bytes) -> None:
    ev = sdl2.SDL_Event()
    ev.type = sdl2.SDL_DROPFILE
    ev.drop.type = sdl2.SDL_DROPFILE
    ev.drop.file = ctypes.cast(ctypes.c_void_p(_sdl_strdup(path)), ctypes.c_char_p)
    sdl2.SDL_PushEvent(ctypes.byref(ev))


def _push_mouse_motion(x: int = 7, y: int = 11) -> None:
    """A foreign event, ON PURPOSE — see the docstring of the test below."""
    ev = sdl2.SDL_Event()
    ev.type = sdl2.SDL_MOUSEMOTION
    ev.motion.type = sdl2.SDL_MOUSEMOTION
    ev.motion.x, ev.motion.y = x, y
    sdl2.SDL_PushEvent(ctypes.byref(ev))


def _settle_event_queue() -> None:
    """Make the measured window immune to whatever the OS is doing.

    THE FLAKE THIS CLOSES (hit once during landing, 2026-08-11): `pump_events` drains
    SDL's PROCESS-GLOBAL queue, and once any test in this module has done
    SDL_Init(SDL_INIT_VIDEO) the Cocoa driver delivers real events — a human moving the
    physical mouse put a `PointerEvent` in the returned list and the assertion below
    tripped on `.path`.  A flaky gate is worse than a red one: green stops proving
    anything, and the ownership invariant quietly stops being enforced.

    Two exposures, two fixes.  (1) FOREIGN EVENTS IN THE RESULT — the test now filters to
    DropFileEvent and deliberately injects a mouse motion so that condition is PINNED
    rather than hoped against.  (2) FOREIGN EVENTS IN THE ALLOCATION WINDOW — SDL grows
    its event-queue block pool with SDL_malloc (blocks go to a free list and are never
    returned), so the FIRST arrival after a quiet spell can bill an allocation to our
    measurement.  Pre-warming the pool and flushing the queue immediately before the
    snapshot leaves nothing for an OS event to allocate."""
    from composeio.render.window import SkiaWindow
    for _ in range(64):                      # grow SDL's block free-list once
        _push_mouse_motion()
    sdl2.SDL_PumpEvents()
    SkiaWindow.pump_events(_Stub())
    sdl2.SDL_FlushEvents(sdl2.SDL_FIRSTEVENT, sdl2.SDL_LASTEVENT)


def test_dropfile_decodes_and_frees_through_the_real_pump_events(nalloc):
    """The other SDL-owned string, through the REAL `pump_events` DROPFILE branch.

    A foreign `SDL_MOUSEMOTION` rides along in the same drain on purpose: that is exactly
    what an OS-delivered event does to this test, so it is asserted instead of avoided.
    The invariant is unchanged — the dropped path decodes, and its SDL buffer is freed
    (-1 allocation) on BOTH the decodable and the undecodable path."""
    pytest.importorskip("skia")
    from composeio.render.window import DropFileEvent, SkiaWindow

    _settle_event_queue()

    # -- a decodable path: one DropFileEvent out, one SDL allocation consumed ------
    _push_dropfile("/tmp/some file ✓.png".encode("utf-8"))
    _push_mouse_motion()                     # the interference, deterministically
    before = nalloc()
    out = SkiaWindow.pump_events(_Stub())
    delta = nalloc() - before
    drops = [e for e in out if isinstance(e, DropFileEvent)]
    assert [e.path for e in drops] == ["/tmp/some file ✓.png"]
    assert delta == -1, f"the dropped path's SDL buffer leaked (alloc delta {delta})"
    # ...and the foreign event was carried, not swallowed: the drain is the real one.
    assert len(out) > len(drops)

    # -- an undecodable path yields NO drop event — and still frees ----------------
    _settle_event_queue()
    _push_dropfile(b"/tmp/bad\xff\xfename.png")
    _push_mouse_motion()
    before = nalloc()
    out = SkiaWindow.pump_events(_Stub())
    delta = nalloc() - before
    assert [e for e in out if isinstance(e, DropFileEvent)] == []
    assert delta == -1, f"an undecodable path leaked its buffer (alloc delta {delta})"
