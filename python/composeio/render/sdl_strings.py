"""
SDL-allocated C strings and their OWNERSHIP — the one place the engine crosses
that boundary ([CXC-R-DRAW-CONTRACT] backend seam; [CXC-TERM-INPUT] paste).

WHY THIS MODULE EXISTS — the 2026-08-11 SIGABRT
-----------------------------------------------
Several SDL entry points hand the caller a heap pointer it must give back with
``SDL_free``:  ``SDL_GetClipboardText``, ``SDL_DropEvent.file``, ``SDL_GetBasePath``.
PySDL2 declares those as ``restype=c_char_p`` / ``c_char_p`` struct fields, and
**ctypes destroys the pointer during the conversion**: a ``c_char_p`` result is
turned into a Python ``bytes`` COPY, and the address SDL malloc'd is never seen
by Python at all.

Handing that ``bytes`` object back to ``SDL_free`` is not a free of SDL's buffer —
``SDL_free``'s argtype is ``c_void_p``, and ctypes passes the interior ``ob_sval``
address of a LIVE CPython object (``id(b) + 32`` on CPython 3.13).  malloc never
returned that address, so ``free()`` -> ``find_zone_and_free`` finds no owning zone
and the macOS allocator ABORTS THE PROCESS:

    abort  <- malloc_report  <- find_zone_and_free  <- SDL2!SDL_free
           <- ffi_call_SYSV  <- _ctypes  <- _PyEval_EvalFrameDefault

``abort()`` does not unwind, so no Python traceback and no ``except Exception:``
guard can catch it — the window simply vanishes.  It is also a LEAK: SDL's buffer
is never freed, because its address was already lost.

THE RULE, and it has no exceptions: recover the raw ADDRESS, copy from the
address, and free the ADDRESS.  Never free a decoded Python value.

This module is deliberately skia-free and lazy about ``import sdl2`` so headless
callers (``widgets/text_input.py``) can share it without dragging in a renderer.
"""

from __future__ import annotations

import ctypes
from typing import Optional

__all__ = ["take_sdl_string", "clipboard_text", "set_clipboard_text",
           "sdl_free_ptr"]


# ---------------------------------------------------------------------------
# The raw-pointer view of SDL_GetClipboardText
# ---------------------------------------------------------------------------

_get_clipboard_ptr = None          # cached CFUNCTYPE view, built on first use


def _clipboard_getter():
    """``SDL_GetClipboardText`` re-prototyped to return a RAW pointer.

    PySDL2's own binding has ``restype=c_char_p``, which loses the address (see
    the module docstring).  We re-prototype the SAME entry point with
    ``restype=c_void_p`` rather than mutating ``sdl2.SDL_GetClipboardText.restype``
    in place: that binding is a process-global object shared with every other
    PySDL2 user, and changing its restype under them is action at a distance."""
    global _get_clipboard_ptr
    if _get_clipboard_ptr is None:
        import sdl2
        entry = ctypes.cast(sdl2.SDL_GetClipboardText, ctypes.c_void_p).value
        _get_clipboard_ptr = ctypes.CFUNCTYPE(ctypes.c_void_p)(entry)
    return _get_clipboard_ptr


def sdl_free_ptr(addr: Optional[int]) -> None:
    """``SDL_free`` an ADDRESS.  The ``c_void_p`` wrapper is load-bearing: it is
    what stops a caller from accidentally passing a Python ``bytes``."""
    if addr:
        import sdl2
        sdl2.SDL_free(ctypes.c_void_p(int(addr)))


def take_sdl_string(addr: Optional[int], errors: str = "replace") -> str:
    """Take ownership of the SDL-malloc'd C string at ``addr``: copy it into a
    Python ``str`` and ``SDL_free`` the ORIGINAL pointer.

    ``errors="strict"`` returns "" for an undecodable string (the drop-file path
    wants "no usable path" rather than a mojibake one).  The buffer is freed on
    every exit, decode failure included — that is what the ``finally`` is for.

    NEVER call this with ``id(bytes)``-derived arithmetic or with a value that
    came out of a ``c_char_p`` conversion; see the module docstring."""
    if not addr:
        return ""
    try:
        raw = ctypes.string_at(int(addr))
        try:
            return raw.decode("utf-8", errors)
        except UnicodeDecodeError:
            return ""
    finally:
        sdl_free_ptr(addr)


# ---------------------------------------------------------------------------
# The clipboard — the engine's ONE reader/writer
# ---------------------------------------------------------------------------

def clipboard_text() -> str:
    """The system clipboard as text, or "" when it is empty or unavailable.

    SDL's clipboard is process-global, so this is a module function rather than a
    Window method: the terminal widget's Cmd-V branch needs the clipboard and has no
    reason to reach through a window handle for it.  Never raises — an unavailable
    clipboard writes ZERO bytes to the child, which is the honest behaviour (a
    bracketed-paste wrapper around nothing would be a submit the human did not ask
    for).  The SDL buffer is freed on EVERY path, including the empty one: SDL hands
    back a malloc'd "" even when ``SDL_HasClipboardText`` is false."""
    try:
        import sdl2                          # noqa: F401  (presence check)
        return take_sdl_string(_clipboard_getter()())
    except Exception:
        return ""


def set_clipboard_text(text: str) -> None:
    """Write the OS clipboard.  Failures are ignored (no clipboard, no SDL)."""
    try:
        import sdl2
        sdl2.SDL_SetClipboardText(str(text).encode("utf-8"))
    except Exception:
        pass
