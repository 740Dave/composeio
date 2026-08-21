"""
ComposeUX Render Engine - Live Windowed Skia Backend (M0)

Spec: [CXC-R-DRAW-CONTRACT] surface section; roadmap [CXC-RM-M0].

Presents the *raster* Skia surface (the pixel reference) in a live SDL2
window.  Design decision: the window shows the exact bytes the reference
renderer produces — present() blits the raster framebuffer to an SDL
streaming texture.  This keeps the on-screen pixels byte-identical to the
PNG the pixel-verification harness diffs against, and it runs unmodified
on macOS (Cocoa) and Raspberry Pi / Linux (KMSDRM fullscreen, Wayland,
or X11) because SDL2 abstracts the display server.

The GPU (GL/Metal) surface path is deliberately NOT used here: raster is
the golden reference, and a GPU rasterizer's AA output differs subtly.
When perf demands it, a GPU surface can be added behind the same class
without touching the Canvas contract.

Event capture: SDL events are converted to platform-independent engine
events (PointerEvent / KeyEvent / WindowEvent) and handed to whatever
callback the engine registers.  The backend knows nothing about widgets.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

import skia

# pysdl2-dll emits a UserWarning on import about using bundled binaries;
# harmless.  On Linux without pysdl2-dll it falls back to the system SDL2.
import sdl2

from .canvas import Size
from .sdl_strings import clipboard_text, take_sdl_string  # noqa: F401 (re-export)
from .skia_backend import SkiaCanvas


# ---------------------------------------------------------------------------
# [CXC-ICP-S6-SCROLLDIR] wheel-delta direction normalization
# ---------------------------------------------------------------------------

def normalize_wheel_delta(x: float, y: float, direction: int) -> Tuple[float, float]:
    """Normalize a raw SDL mouse-wheel delta to ONE canonical, OS-respecting
    orientation by honoring ``wheel.direction`` (SDL 2.0.4+).

    On macOS the OS bakes its "natural scrolling" preference INTO the wheel
    delta before SDL sees it, and flags that with
    ``SDL_MOUSEWHEEL_FLIPPED`` — a flag that tracks the OS checkbox live.  SDL
    documents that a FLIPPED delta is the OPPOSITE of its NORMAL convention.
    The scrollable widgets' scroll/pan handlers were authored against the
    NORMAL orientation (the RPi / mouse-wheel path, which is always NORMAL), so
    a raw FLIPPED delta ran through them BACKWARDS — the reported inversion.

    Negating on FLIPPED delivers the SAME canonical orientation on every
    platform.  This is NOT a hardcoded sign flip: it is conditional on the
    OS-provided flag, so toggling the macOS natural-scroll checkbox flips the
    flag and the delta follows the OS.  The per-instance ``scroll_direction``
    preference is applied ON TOP of this, in the runtime's scroll dispatch."""
    if direction == sdl2.SDL_MOUSEWHEEL_FLIPPED:
        return -x, -y
    return x, y


# ---------------------------------------------------------------------------
# [CXC-TERM-INPUT] The system clipboard — read-only, and deliberately tiny
#
# `clipboard_text` is RE-EXPORTED from `sdl_strings` (see the import above), not
# defined here.  It lives in a skia-free module because `widgets/text_input.py`
# needs the same reader in a headless run, and a second copy of the SDL string-
# ownership idiom is exactly what produced the 2026-08-11 SIGABRT: freeing a
# ctypes-converted `bytes` instead of SDL's pointer aborts the process with no
# traceback.  ONE reader, ONE free.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Platform-independent engine events (Layer 1 -> Layer 2 boundary)
# ---------------------------------------------------------------------------

@dataclass
class PointerEvent:
    """Engine pointer event per [CXC-R-ENGINE-HIT]."""
    type: str          # down | move | up | cancel | scroll
    x: float
    y: float
    pointer_id: int = 0
    timestamp: float = 0.0
    dx: float = 0.0    # scroll deltas
    dy: float = 0.0
    button: int = 1


@dataclass
class KeyEvent:
    """Engine keyboard event."""
    type: str          # down | up | text
    key: str           # SDL key name, e.g. "Tab", "A", "Return"
    text: str = ""     # for text input events
    modifiers: int = 0


@dataclass
class WindowEvent:
    """Window lifecycle events (close, resize, move, expose).

    ``resize`` carries the new client ``width``/``height``; ``move`` carries
    the new window origin in ``x``/``y`` (screen px).  Both feed the
    standalone-viewer geometry persistence ([CXC-DVL-GEOMETRY])."""
    type: str          # close | resize | move | expose
    width: int = 0
    height: int = 0
    x: int = 0
    y: int = 0


@dataclass
class DropFileEvent:
    """A file dropped onto the window (SDL_DROPFILE).  `path` is the
    absolute local path SDL reports for the dropped file.  Surfaced like
    every other engine event so the runtime consumes it uniformly (the
    drag-drop-to-Intelligence seam).

    `x`, `y` are the drop position in logical surface px (5b,
    [DEC-THREAD-MARKDOWN-HEADERS] companion): SDL2's SDL_DropEvent carries no
    coordinates, so the window reads the CURRENT mouse position at drop time
    (the same SDL_GetMouseState idiom the wheel event already uses) — good
    enough to route a drop onto the widget under the pointer (the message
    prompt vs. the thread).  ``-1.0`` = position unknown -> the runtime keeps
    the historical whole-window behavior (publish to the thread)."""
    path: str
    x: float = -1.0
    y: float = -1.0


EngineEvent = PointerEvent | KeyEvent | WindowEvent | DropFileEvent


class SkiaWindow(SkiaCanvas):
    """Skia raster canvas presented in a live SDL2 window.

    Usage:
        win = SkiaWindow(title="ComposeUX")
        win.create_surface(960, 600)
        ... draw via the Canvas contract ...
        win.present()                  # blits the raster surface to screen
        events = win.pump_events()     # engine events since last pump

    ``fullscreen=True`` requests a borderless-fullscreen surface — the
    RPi kiosk mode.  Under Linux, SDL selects KMSDRM automatically when
    no display server is running (or set SDL_VIDEODRIVER=kmsdrm/wayland).

    ``pos=(x, y)`` places the window at that screen origin instead of the
    default OS-centered placement ([DEC-LAUNCH-PLACEMENT], David 2026-07-22:
    a launched window opens 10px right of the console, top-aligned — not
    centered-behind).  ``None`` keeps SDL_WINDOWPOS_CENTERED.  ``raise_to_front``
    calls SDL_RaiseWindow after creation so the new window is above the
    console rather than behind it.  Both are ignored under ``fullscreen``
    (a kiosk surface owns the whole display).
    """

    def __init__(self, title: str = "ComposeUX", fullscreen: bool = False,
                 scale: float = 1.0,
                 pos: Optional[Tuple[int, int]] = None,
                 raise_to_front: bool = False,
                 resizable: bool = False,
                 min_size: Optional[Tuple[int, int]] = None) -> None:
        super().__init__()
        self._title = title
        self._fullscreen = fullscreen
        self._window: Optional[int] = None
        self._renderer: Optional[int] = None
        self._texture: Optional[int] = None
        self._open = False
        self._scale = scale
        self._pos = pos
        self._raise_to_front = raise_to_front
        # [CXC-DVL-WINDOW] the standalone viewer opens RESIZABLE with a
        # sensible floor; the console/other scenes keep the fixed default.
        self._resizable = resizable
        self._min_size = min_size
        # [CXC-R-ENGINE-HOVER] system-cursor cache: name -> SDL_Cursor*.  The
        # engine hands us a SEMANTIC cursor ("pointer"/"default"); we create
        # each backing SDL system cursor ONCE (creating one per motion event
        # would leak) and re-set the cached handle.  Freed in close().
        self._cursor_cache: dict = {}

    # -- Surface (windowed) ---------------------------------------------------

    def create_surface(self, width: int, height: int) -> None:
        # Build the raster surface exactly as the reference backend does.
        super().create_surface(width, height)

        if sdl2.SDL_WasInit(sdl2.SDL_INIT_VIDEO) == 0:
            if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
                raise RuntimeError(
                    f"SDL_Init failed: {sdl2.SDL_GetError().decode()}")

        flags = sdl2.SDL_WINDOW_SHOWN | sdl2.SDL_WINDOW_ALLOW_HIGHDPI
        if self._fullscreen:
            flags |= sdl2.SDL_WINDOW_FULLSCREEN_DESKTOP
        # [CXC-DVL-WINDOW] resizable standalone window: the user drags the
        # edges/corners and the runtime re-lays-out to the new client size.
        if self._resizable and not self._fullscreen:
            flags |= sdl2.SDL_WINDOW_RESIZABLE

        # Explicit launch placement ([DEC-LAUNCH-PLACEMENT]): a launcher passes
        # a screen origin so the window opens beside the console instead of
        # OS-centered on top of it.  Fullscreen ignores placement (it owns the
        # whole display).  SDL takes real x,y pixels here in place of the
        # CENTERED sentinel.
        if self._pos is not None and not self._fullscreen:
            win_x, win_y = int(self._pos[0]), int(self._pos[1])
        else:
            win_x = win_y = sdl2.SDL_WINDOWPOS_CENTERED

        self._window = sdl2.SDL_CreateWindow(
            self._title.encode(),
            win_x, win_y,
            int(width * self._scale), int(height * self._scale), flags)
        if not self._window:
            raise RuntimeError(
                f"SDL_CreateWindow failed: {sdl2.SDL_GetError().decode()}")

        # Raise/focus the new window ([DEC-LAUNCH-PLACEMENT]): a launched
        # window must come to the FRONT, not spawn behind the console.  SDL
        # raises within this process; cross-application focus stealing is
        # ultimately the window manager's policy (a glass-verify item).
        if self._raise_to_front and not self._fullscreen:
            sdl2.SDL_RaiseWindow(self._window)

        # [CXC-DVL-WINDOW] minimum size: a resizable window cannot be dragged
        # to a degenerate strip (below the floor the graph pane still renders
        # its empty state legibly).
        if (self._resizable and self._min_size is not None
                and not self._fullscreen):
            sdl2.SDL_SetWindowMinimumSize(
                self._window, int(self._min_size[0]), int(self._min_size[1]))

        # No PRESENTVSYNC: the engine run loop paces frames itself; adding a
        # vsync block on top of that double-throttles below 60fps.
        # On macOS the default Metal renderer's RenderPresent blocks ~8ms per
        # call (command-queue flush) even without vsync; the GL renderer
        # presents in <1ms.  Preference only — SDL falls back if unavailable.
        if sys.platform == "darwin":
            sdl2.SDL_SetHint(sdl2.SDL_HINT_RENDER_DRIVER, b"opengl")
        self._renderer = sdl2.SDL_CreateRenderer(
            self._window, -1, sdl2.SDL_RENDERER_ACCELERATED)
        if not self._renderer:
            # Fall back to software renderer (headless-ish X, odd drivers)
            self._renderer = sdl2.SDL_CreateRenderer(
                self._window, -1, sdl2.SDL_RENDERER_SOFTWARE)
        if not self._renderer:
            raise RuntimeError(
                f"SDL_CreateRenderer failed: {sdl2.SDL_GetError().decode()}")

        # Streaming texture matching Skia's native N32 byte layout, which
        # differs per build: RGBA on the macOS wheel, BGRA on Linux/aarch64.
        # (SDL's ARGB8888/ABGR8888 names are packed-int order == reversed
        # byte order on little-endian.)
        n32 = skia.ImageInfo.MakeN32Premul(1, 1).colorType()
        sdl_format = (sdl2.SDL_PIXELFORMAT_ABGR8888
                      if n32 == skia.ColorType.kRGBA_8888_ColorType
                      else sdl2.SDL_PIXELFORMAT_ARGB8888)
        self._texture = sdl2.SDL_CreateTexture(
            self._renderer, sdl_format,
            sdl2.SDL_TEXTUREACCESS_STREAMING, width, height)
        if not self._texture:
            raise RuntimeError(
                f"SDL_CreateTexture failed: {sdl2.SDL_GetError().decode()}")

        # Reusable readback buffer (avoid a 4*w*h allocation per frame)
        self._blit_buf = bytearray(width * height * 4)
        self._blit_info = skia.ImageInfo.MakeN32Premul(width, height)

        # Text input events (SDL_TEXTINPUT) for TextInput widgets ([CXC-R-C8]).
        sdl2.SDL_StartTextInput()
        # Drag-and-drop file events (SDL_DROPFILE): dropping an image onto the
        # Intelligence Console sends it to the Intelligence.  Enable explicitly
        # so the platform delivers the drop regardless of SDL's default state.
        sdl2.SDL_EventState(sdl2.SDL_DROPFILE, sdl2.SDL_ENABLE)
        self._open = True

    def present(self) -> None:
        """Blit the raster surface to the window.  One call per frame."""
        if not self._open:
            return
        row_bytes = self._width * 4
        buf = self._blit_buf
        if not self._surface.readPixels(self._blit_info, buf, row_bytes, 0, 0):
            return

        c_buf = (ctypes.c_char * len(buf)).from_buffer(buf)
        sdl2.SDL_UpdateTexture(self._texture, None, c_buf, row_bytes)
        sdl2.SDL_RenderClear(self._renderer)
        sdl2.SDL_RenderCopy(self._renderer, self._texture, None, None)
        sdl2.SDL_RenderPresent(self._renderer)

    def present_partial(self, rects) -> None:
        """Blit ONLY the damaged sub-rects to the window ([CXC-R-DRAW-DAMAGE]).

        The engine's dirty-region path hands us the regions it actually
        redrew; we upload only those texture sub-rectangles instead of the
        whole 1280x1300 surface.  This is the desktop analog of the win the
        embedded backends get: on ESP32 the same damage rect becomes the SPI
        address-window push — the single biggest refresh cost there.

        A hardware renderer still RenderCopies the full texture to the
        back-buffer (cheap, GPU-side), but the per-frame CPU->GPU texture
        UPLOAD shrinks from the whole surface to just the damage.  Falls back
        to a full present() when there is nothing to scope."""
        if not self._open:
            return
        if not rects:
            return self.present()
        row_bytes = self._width * 4
        for (x, y, w, h) in rects:
            ix, iy = int(x), int(y)
            iw, ih = int(w), int(h)
            if iw <= 0 or ih <= 0:
                continue
            ix = max(0, min(ix, self._width - 1))
            iy = max(0, min(iy, self._height - 1))
            iw = min(iw, self._width - ix)
            ih = min(ih, self._height - iy)
            # Read just this sub-rect out of the raster surface.
            sub_info = skia.ImageInfo.MakeN32Premul(iw, ih)
            sub_row = iw * 4
            sub_buf = bytearray(sub_row * ih)
            if not self._surface.readPixels(sub_info, sub_buf, sub_row, ix, iy):
                return self.present()          # fall back on any readback miss
            c_sub = (ctypes.c_char * len(sub_buf)).from_buffer(sub_buf)
            sdl_rect = sdl2.SDL_Rect(ix, iy, iw, ih)
            sdl2.SDL_UpdateTexture(self._texture, sdl_rect, c_sub, sub_row)
        sdl2.SDL_RenderClear(self._renderer)
        sdl2.SDL_RenderCopy(self._renderer, self._texture, None, None)
        sdl2.SDL_RenderPresent(self._renderer)

    # -- Event pump -------------------------------------------------------------

    def pump_events(self) -> List[EngineEvent]:
        """Drain the SDL event queue, returning engine events.

        Mouse coordinates are mapped from window space to logical surface
        space (relevant when fullscreen scales the texture).
        """
        events: List[EngineEvent] = []
        if not self._open:
            return events

        # window -> logical mapping
        ww, wh = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetWindowSize(self._window, ctypes.byref(ww), ctypes.byref(wh))
        sx = self._width / ww.value if ww.value else 1.0
        sy = self._height / wh.value if wh.value else 1.0

        ev = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(ev)) != 0:
            t = ev.type
            if t == sdl2.SDL_QUIT:
                events.append(WindowEvent("close"))
            elif t == sdl2.SDL_MOUSEBUTTONDOWN:
                events.append(PointerEvent(
                    "down", ev.button.x * sx, ev.button.y * sy,
                    pointer_id=0, timestamp=ev.button.timestamp / 1000.0,
                    button=ev.button.button))
            elif t == sdl2.SDL_MOUSEBUTTONUP:
                events.append(PointerEvent(
                    "up", ev.button.x * sx, ev.button.y * sy,
                    pointer_id=0, timestamp=ev.button.timestamp / 1000.0,
                    button=ev.button.button))
            elif t == sdl2.SDL_MOUSEMOTION:
                events.append(PointerEvent(
                    "move", ev.motion.x * sx, ev.motion.y * sy,
                    pointer_id=0, timestamp=ev.motion.timestamp / 1000.0))
            elif t == sdl2.SDL_MOUSEWHEEL:
                mx, my = ctypes.c_int(), ctypes.c_int()
                sdl2.SDL_GetMouseState(ctypes.byref(mx), ctypes.byref(my))
                # [CXC-ICP-S6-SCROLLDIR] honor SDL_MOUSEWHEEL_FLIPPED so the
                # delta is in one canonical, OS-respecting orientation on every
                # platform (see normalize_wheel_delta).
                dx, dy = normalize_wheel_delta(
                    float(ev.wheel.x), float(ev.wheel.y),
                    getattr(ev.wheel, "direction", 0))
                events.append(PointerEvent(
                    "scroll", mx.value * sx, my.value * sy,
                    dx=dx, dy=dy,
                    timestamp=ev.wheel.timestamp / 1000.0))
            elif t == sdl2.SDL_FINGERDOWN or t == sdl2.SDL_FINGERUP or t == sdl2.SDL_FINGERMOTION:
                # Touch (RPi touchscreen) — normalized coords -> logical px
                kind = {sdl2.SDL_FINGERDOWN: "down",
                        sdl2.SDL_FINGERUP: "up",
                        sdl2.SDL_FINGERMOTION: "move"}[t]
                events.append(PointerEvent(
                    kind, ev.tfinger.x * self._width, ev.tfinger.y * self._height,
                    pointer_id=int(ev.tfinger.fingerId),
                    timestamp=ev.tfinger.timestamp / 1000.0))
            elif t == sdl2.SDL_KEYDOWN:
                name = sdl2.SDL_GetKeyName(ev.key.keysym.sym).decode()
                events.append(KeyEvent("down", name,
                                       modifiers=ev.key.keysym.mod))
            elif t == sdl2.SDL_KEYUP:
                name = sdl2.SDL_GetKeyName(ev.key.keysym.sym).decode()
                events.append(KeyEvent("up", name,
                                       modifiers=ev.key.keysym.mod))
            elif t == sdl2.SDL_TEXTINPUT:
                events.append(KeyEvent("text", "", text=ev.text.text.decode()))
            elif t == sdl2.SDL_DROPFILE:
                # ev.drop.file is a C char* SDL malloc'd for us; SDL documents
                # that WE must SDL_free it.  Reading the c_char_p field copies
                # it to a Python bytes and loses the raw pointer, so recover the
                # original address out of the struct memory (by field offset) and
                # hand it to the ONE owner-transfer helper — freeing the bytes
                # buffer instead aborts the process (see `sdl_strings`).
                addr = ctypes.cast(
                    ctypes.byref(ev.drop, sdl2.SDL_DropEvent.file.offset),
                    ctypes.POINTER(ctypes.c_void_p)).contents.value
                # strict: an undecodable path is NO path, not a mojibake one.
                path = take_sdl_string(addr, errors="strict")
                if path:
                    # SDL_DropEvent has no x/y — read the pointer position at
                    # drop time (same idiom as the wheel event above) so the
                    # runtime can route the drop to the widget under the cursor.
                    dmx, dmy = ctypes.c_int(), ctypes.c_int()
                    sdl2.SDL_GetMouseState(ctypes.byref(dmx), ctypes.byref(dmy))
                    events.append(DropFileEvent(
                        path, dmx.value * sx, dmy.value * sy))
            elif t == sdl2.SDL_WINDOWEVENT:
                we = ev.window.event
                if we == sdl2.SDL_WINDOWEVENT_EXPOSED:
                    events.append(WindowEvent("expose"))
                elif we == sdl2.SDL_WINDOWEVENT_SIZE_CHANGED:
                    # [CXC-DVL-WINDOW] the client size changed (user drag or a
                    # programmatic resize): report the new client size so the
                    # runtime rebuilds the surface + re-lays-out.  data1/data2
                    # carry the new width/height.
                    events.append(WindowEvent(
                        "resize", width=int(ev.window.data1),
                        height=int(ev.window.data2)))
                elif we == sdl2.SDL_WINDOWEVENT_MOVED:
                    # [CXC-DVL-GEOMETRY] the window origin moved — data1/data2
                    # carry the new x/y so geometry persistence records it.
                    events.append(WindowEvent(
                        "move", x=int(ev.window.data1), y=int(ev.window.data2)))
        return events

    def get_bounds(self) -> Optional[Tuple[int, int, int, int]]:
        """This window's on-screen bounding box ``(x, y, w, h)`` in screen
        pixels, or None if not open ([DEC-LAUNCH-PLACEMENT]).  The console
        publishes this so a launcher knows where to place a new window
        (top-aligned, 10px right of the console's right edge).  x,y are the
        window ORIGIN (SDL_GetWindowPosition); w,h the window SIZE
        (SDL_GetWindowSize, logical window units — matches the origin's
        coordinate space, which is what a sibling window's placement needs)."""
        if not self._open or not self._window:
            return None
        x, y = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetWindowPosition(self._window,
                                   ctypes.byref(x), ctypes.byref(y))
        w, h = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetWindowSize(self._window,
                               ctypes.byref(w), ctypes.byref(h))
        return (x.value, y.value, w.value, h.value)

    def resize_surface(self, width: int, height: int) -> None:
        """Rebuild the raster surface + SDL streaming texture at a new client
        size ([CXC-DVL-WINDOW] resize re-layout).  The window + renderer are
        kept; only the pixel buffers grow/shrink so the next frame draws at the
        new size and present() blits it.  A no-op for a degenerate/no-change
        size."""
        if not self._open:
            return
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            return
        if width == self._width and height == self._height:
            return
        # Rebuild the Skia raster surface (the reference backend's own path).
        SkiaCanvas.create_surface(self, width, height)
        # Recreate the streaming texture at the new size (same N32 format).
        if self._texture:
            sdl2.SDL_DestroyTexture(self._texture)
            self._texture = None
        n32 = skia.ImageInfo.MakeN32Premul(1, 1).colorType()
        sdl_format = (sdl2.SDL_PIXELFORMAT_ABGR8888
                      if n32 == skia.ColorType.kRGBA_8888_ColorType
                      else sdl2.SDL_PIXELFORMAT_ARGB8888)
        self._texture = sdl2.SDL_CreateTexture(
            self._renderer, sdl_format,
            sdl2.SDL_TEXTUREACCESS_STREAMING, width, height)
        self._blit_buf = bytearray(width * height * 4)
        self._blit_info = skia.ImageInfo.MakeN32Premul(width, height)

    # -- hover cursor ([CXC-R-ENGINE-HOVER]) ----------------------------------

    _SYSTEM_CURSORS = {
        "pointer": sdl2.SDL_SYSTEM_CURSOR_HAND,   # the "pointing hand" over clickables
        "default": sdl2.SDL_SYSTEM_CURSOR_ARROW,
        "text": sdl2.SDL_SYSTEM_CURSOR_IBEAM,
    }

    def set_cursor(self, name: str) -> None:
        """Lower a SEMANTIC hover cursor to the OS pointer ([CXC-R-ENGINE-HOVER]
        backend lowering).  ``name`` comes from the engine's hover resolver —
        "pointer" -> the system hand (clickable), "text" -> the I-beam,
        anything else -> the arrow.  Backing SDL system cursors are created
        once and cached; SDL owns the active-cursor lifetime.  A no-op before
        the window is open.

        This is the desktop leg of a backend-agnostic affordance: the browser
        backend lowers the same name to CSS ``cursor: pointer``; the embedded/
        CYD tier has no OS cursor and simply omits ``set_cursor`` (the runtime's
        duck-typed call then no-ops).  No per-widget cursor code ever forks into
        a backend — the widget only names the semantic; the backend maps it."""
        if not self._open:
            return
        cur = self._cursor_cache.get(name)
        if cur is None:
            sysid = self._SYSTEM_CURSORS.get(name,
                                             sdl2.SDL_SYSTEM_CURSOR_ARROW)
            cur = sdl2.SDL_CreateSystemCursor(sysid)
            if not cur:
                return
            self._cursor_cache[name] = cur
        sdl2.SDL_SetCursor(cur)

    def set_position(self, x: int, y: int) -> None:
        """Move the window origin to ``(x, y)`` screen px ([CXC-DVL-GEOMETRY]
        off-screen clamp re-placement)."""
        if self._open and self._window:
            sdl2.SDL_SetWindowPosition(self._window, int(x), int(y))

    def get_display_bounds(self) -> List[Tuple[int, int, int, int]]:
        """The bounds ``(x, y, w, h)`` of every connected display, in screen
        px ([CXC-DVL-GEOMETRY] off-screen clamp).  Empty list if SDL cannot
        report them (the clamp then simply trusts the persisted position)."""
        out: List[Tuple[int, int, int, int]] = []
        try:
            n = sdl2.SDL_GetNumVideoDisplays()
        except Exception:
            return out
        for i in range(max(0, int(n))):
            rect = sdl2.SDL_Rect()
            if sdl2.SDL_GetDisplayBounds(i, ctypes.byref(rect)) == 0:
                out.append((rect.x, rect.y, rect.w, rect.h))
        return out

    def get_border_size(self) -> Optional[Tuple[int, int, int, int]]:
        """This window's decoration thickness ``(top, left, bottom, right)`` in
        screen px, or None if the WM cannot report it ([CXC-DVL-GEOMETRY]
        first-launch placement).

        WHY this exists: SDL positions and reports the window's CONTENT rect,
        but a placement policy expressed as "20 px from the top-left of the
        screen" is about what the user SEES — the window FRAME, title bar
        included.  On macOS the two differ by the ~28 px title bar, and the WM
        additionally refuses to put a title bar under the menu bar, so a naive
        content-origin placement silently lands somewhere else.  The runtime
        adds this offset so the FRAME lands where the policy says.  None ->
        the caller keeps the content-origin placement (a WM without borders,
        e.g. the RPi kiosk, needs no offset anyway)."""
        if not self._open or not self._window:
            return None
        fn = getattr(sdl2, "SDL_GetWindowBordersSize", None)
        if fn is None:
            return None
        top, left, bottom, right = (ctypes.c_int() for _ in range(4))
        try:
            if fn(self._window, ctypes.byref(top), ctypes.byref(left),
                  ctypes.byref(bottom), ctypes.byref(right)) != 0:
                return None
        except Exception:
            return None
        return (top.value, left.value, bottom.value, right.value)

    def get_display_usable_bounds(self) -> List[Tuple[int, int, int, int]]:
        """Instance-side alias of :func:`display_usable_bounds` — the
        duck-typed backend affordance the runtime looks for (same shape as
        ``get_display_bounds``, but the WORK AREA)."""
        return display_usable_bounds()

    def read_presented_pixels(self) -> Optional[bytes]:
        """Read back the pixels the renderer actually presented (RGBA bytes).

        This is the ground truth for what is on screen — it goes through
        the same texture/format path as the display, so a channel-order
        bug in the blit shows up here even though save_png (which reads
        the Skia surface, not the renderer) would look correct.
        """
        if not self._open:
            return None
        ww, wh = ctypes.c_int(), ctypes.c_int()
        sdl2.SDL_GetRendererOutputSize(self._renderer,
                                       ctypes.byref(ww), ctypes.byref(wh))
        buf = (ctypes.c_ubyte * (ww.value * wh.value * 4))()
        if sdl2.SDL_RenderReadPixels(self._renderer, None,
                                     sdl2.SDL_PIXELFORMAT_ABGR8888,
                                     buf, ww.value * 4) != 0:
            return None
        self._presented_size = (ww.value, wh.value)
        return bytes(buf)

    # -- Test support -----------------------------------------------------------

    def inject_pointer(self, type_: str, x: float, y: float) -> None:
        """Push a synthetic mouse event into the SDL queue (smoke tests)."""
        ev = sdl2.SDL_Event()
        if type_ in ("down", "up"):
            ev.type = (sdl2.SDL_MOUSEBUTTONDOWN if type_ == "down"
                       else sdl2.SDL_MOUSEBUTTONUP)
            ev.button.x = int(x)
            ev.button.y = int(y)
            ev.button.button = 1
            ev.button.timestamp = sdl2.SDL_GetTicks()
        else:
            ev.type = sdl2.SDL_MOUSEMOTION
            ev.motion.x = int(x)
            ev.motion.y = int(y)
            ev.motion.timestamp = sdl2.SDL_GetTicks()
        sdl2.SDL_PushEvent(ctypes.byref(ev))

    def inject_text(self, text: str) -> None:
        """Push a synthetic SDL_TEXTINPUT event (smoke tests, [CXC-R-C8])."""
        ev = sdl2.SDL_Event()
        ev.type = sdl2.SDL_TEXTINPUT
        # ctypes c_char arrays accept direct bytes assignment (NUL-padded)
        ev.text.text = text.encode("utf-8")[:31]
        sdl2.SDL_PushEvent(ctypes.byref(ev))

    def inject_drop(self, path: str) -> None:
        """Push a synthetic SDL_DROPFILE event (smoke tests).  The path
        string is SDL_malloc'd so pump_events' SDL_free frees the correct
        pointer, exercising the real drop path end-to-end."""
        data = path.encode("utf-8")
        buf = sdl2.SDL_malloc(len(data) + 1)
        ctypes.memmove(buf, data, len(data))
        ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))[len(data)] = b"\x00"
        ev = sdl2.SDL_Event()
        ev.type = sdl2.SDL_DROPFILE
        ev.drop.type = sdl2.SDL_DROPFILE
        ev.drop.file = ctypes.cast(buf, ctypes.c_char_p)
        sdl2.SDL_PushEvent(ctypes.byref(ev))

    def inject_key(self, key_name: str) -> None:
        """Push a synthetic key-down event by SDL key name (smoke tests)."""
        ev = sdl2.SDL_Event()
        ev.type = sdl2.SDL_KEYDOWN
        ev.key.keysym.sym = sdl2.SDL_GetKeyFromName(key_name.encode())
        ev.key.keysym.mod = 0
        sdl2.SDL_PushEvent(ctypes.byref(ev))

    # -- Teardown ----------------------------------------------------------------

    def close(self) -> None:
        # [CXC-R-ENGINE-HOVER] free the cached system cursors we created.
        for cur in self._cursor_cache.values():
            if cur:
                sdl2.SDL_FreeCursor(cur)
        self._cursor_cache.clear()
        if self._texture:
            sdl2.SDL_DestroyTexture(self._texture)
            self._texture = None
        if self._renderer:
            sdl2.SDL_DestroyRenderer(self._renderer)
            self._renderer = None
        if self._window:
            sdl2.SDL_DestroyWindow(self._window)
            self._window = None
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open


# ---------------------------------------------------------------------------
# Display work area ([CXC-DVL-GEOMETRY] first-launch placement)
# ---------------------------------------------------------------------------

def display_usable_bounds() -> List[Tuple[int, int, int, int]]:
    """The VISIBLE WORK AREA ``(x, y, w, h)`` of every connected display, in
    screen px ([CXC-DVL-GEOMETRY] first-launch placement).

    Work area = the display MINUS the OS chrome that permanently owns part of
    it — on macOS the menu bar (a ~25 px band at the top) and the Dock; on
    Linux/Wayland the panels/docks the WM reserves.  This is what a "20 px
    from the top-left of the screen" placement policy MUST measure against:
    the raw display origin (``get_display_bounds``) would tuck the window
    UNDER the menu bar.

    Module-level (not only a window method) because the runtime needs it
    BEFORE the window exists, to choose the creation origin.  SDL video is
    initialized on demand — the same guarded init ``create_surface`` does, so
    calling this first costs nothing and changes nothing.  Any failure (no
    SDL, a display-less session, an SDL build without
    ``SDL_GetDisplayUsableBounds``) degrades to an EMPTY list and the caller
    keeps the OS default placement — never a crash."""
    out: List[Tuple[int, int, int, int]] = []
    try:
        if sdl2.SDL_WasInit(sdl2.SDL_INIT_VIDEO) == 0:
            if sdl2.SDL_Init(sdl2.SDL_INIT_VIDEO) != 0:
                return out
        usable = getattr(sdl2, "SDL_GetDisplayUsableBounds", None)
        if usable is None:                      # very old SDL2 -> raw bounds
            usable = sdl2.SDL_GetDisplayBounds
        n = int(sdl2.SDL_GetNumVideoDisplays())
    except Exception:
        return out
    for i in range(max(0, n)):
        try:
            rect = sdl2.SDL_Rect()
            if usable(i, ctypes.byref(rect)) == 0 and rect.w > 0 and rect.h > 0:
                out.append((rect.x, rect.y, rect.w, rect.h))
        except Exception:
            continue
    return out
