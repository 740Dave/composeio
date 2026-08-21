"""The ONE terminal escape-sequence table — [SPEC:CXC-TERM] · [CXC-TERM-SEQTABLE].

TWO consumers, ONE table:

  * `compose_projects/intelligence/tools/terminal_census.py` — the measurement that produced the
    `supported` column ([CXC-TERM-CENSUS]).
  * the terminal widget's runtime degradation detector — which PRE-SCANS every byte
    chunk against the UNSUPPORTED patterns BEFORE feeding the emulator, because
    `pyte`'s own answer to a sequence it does not implement is to DROP IT SILENTLY
    ([CXC-TERM-EMULATOR]). Unsupported-sequence detection therefore has to sit
    UPSTREAM of the emulator; that is what makes degradation VISIBLE instead of a
    subtly-wrong render.

Two copies of this table is the fork-divergence class this corpus fails builds over
([DX-NARROW-WAIST]), and the specific defect it produces is a widget that believes it
supports a sequence the census says it does not.

Contents:
  * `tokenize` — a chunk-boundary-safe ANSI tokenizer. Returns (tokens, tail); feed the
    tail back in with the next chunk so a sequence SPLIT ACROSS TWO `os.read` calls is
    matched exactly ONCE ([CXC-TERM-SEQTABLE] chunk-boundary safety).
  * `shape` — the canonical family name of a sequence (numeric params collapsed), so a
    census counts `CSI <n> G` once rather than eighty times. Mode numbers are PRESERVED,
    because `?1049h` and `?25l` are different capabilities, not the same one.
  * `FEATURES` — the named capability table, each row a matcher over a token.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable, NamedTuple

# ── Token kinds ─────────────────────────────────────────────
K_TEXT = "text"      # printable payload
K_C0 = "c0"          # a C0 control byte (BEL, BS, HT, LF, CR, ...) or DEL
K_CSI = "csi"        # ESC [ params intermediates final
K_OSC = "osc"        # ESC ] ... (BEL | ST)
K_DCS = "dcs"        # ESC P ... ST
K_STR = "str"        # ESC _ (APC) / ESC ^ (PM) / ESC X (SOS) ... ST
K_ESC = "esc"        # a two/three-byte escape: ESC 7, ESC =, ESC ( B, ...

ESC = 0x1B
BEL = 0x07
_CSI_FINAL = range(0x40, 0x7F)
_CSI_PARAM = range(0x30, 0x40)
_CSI_INTER = range(0x20, 0x30)
# The longest incomplete tail worth carrying between chunks. [CXC-TERM-SEQTABLE] names
# SEQ_TAIL_MAX = 64; an OSC 8 hyperlink runs longer than that, so the tokenizer carries a
# genuinely incomplete string sequence in full rather than truncating it into a false match.
SEQ_TAIL_MAX = 64


class Token(NamedTuple):
    kind: str
    raw: bytes

    @property
    def text(self) -> str:
        return self.raw.decode("latin-1")


def tokenize(data: bytes) -> tuple[list[Token], bytes]:
    """Split `data` into tokens. Returns (tokens, tail) where `tail` is a TRAILING,
    INCOMPLETE sequence: prepend it to the next chunk. A caller that ignores the tail
    will double-count or miss a split sequence — the bug this signature forecloses."""
    tokens: list[Token] = []
    i, n = 0, len(data)
    run = bytearray()

    def flush_text() -> None:
        if run:
            tokens.append(Token(K_TEXT, bytes(run)))
            run.clear()

    while i < n:
        b = data[i]
        if b == ESC:
            start = i
            j = i + 1
            if j >= n:
                flush_text()
                return tokens, data[start:]
            nxt = data[j]
            if nxt == 0x5B:                      # ESC [ -> CSI
                j += 1
                while j < n and data[j] in _CSI_PARAM:
                    j += 1
                while j < n and data[j] in _CSI_INTER:
                    j += 1
                if j >= n:
                    flush_text()
                    return tokens, data[start:]
                if data[j] in _CSI_FINAL:
                    flush_text()
                    tokens.append(Token(K_CSI, data[start:j + 1]))
                    i = j + 1
                    continue
                # Malformed: fall through and treat the ESC as literal text.
            elif nxt in (0x5D, 0x50, 0x5F, 0x5E, 0x58):   # OSC / DCS / APC / PM / SOS
                kind = {0x5D: K_OSC, 0x50: K_DCS}.get(nxt, K_STR)
                j += 1
                end = -1
                while j < n:
                    if data[j] == BEL:
                        end = j + 1
                        break
                    if data[j] == ESC and j + 1 < n and data[j + 1] == 0x5C:  # ST
                        end = j + 2
                        break
                    j += 1
                if end < 0:
                    flush_text()
                    return tokens, data[start:]
                flush_text()
                tokens.append(Token(kind, data[start:end]))
                i = end
                continue
            elif nxt in (0x28, 0x29, 0x2A, 0x2B, 0x2D, 0x2E, 0x2F, 0x20, 0x23, 0x25):
                # Charset designation / ESC SP F / ESC # 8 — one more byte follows.
                if j + 1 >= n:
                    flush_text()
                    return tokens, data[start:]
                flush_text()
                tokens.append(Token(K_ESC, data[start:j + 2]))
                i = j + 2
                continue
            elif 0x30 <= nxt <= 0x7E:            # ESC 7, ESC 8, ESC =, ESC >, ESC M, ...
                flush_text()
                tokens.append(Token(K_ESC, data[start:j + 1]))
                i = j + 1
                continue
        if b < 0x20 or b == 0x7F:
            flush_text()
            tokens.append(Token(K_C0, data[i:i + 1]))
            i += 1
            continue
        run.append(b)
        i += 1
    flush_text()
    return tokens, b""


_NUM = re.compile(rb"\d+")


def shape(tok: Token) -> str:
    """The canonical FAMILY name of a token — what a census counts.

    `CSI 12 G` and `CSI 44 G` are the same capability (absolute column addressing) and
    collapse to `CSI {n} G`. Mode sequences do NOT collapse: `CSI ? 1049 h` keeps its
    number, because the number IS the capability. SGR keeps its parameters shape but not
    its colour values."""
    raw = tok.raw
    if tok.kind == K_TEXT:
        return "<text>"
    if tok.kind == K_C0:
        return {b"\x07": "BEL", b"\x08": "BS", b"\t": "HT", b"\n": "LF", b"\x0b": "VT",
                b"\x0c": "FF", b"\r": "CR", b"\x0e": "SO", b"\x0f": "SI",
                b"\x7f": "DEL"}.get(raw, "C0<0x%02x>" % raw[0])
    if tok.kind == K_CSI:
        body = raw[2:]
        final = body[-1:].decode("latin-1")
        params = body[:-1]
        private = ""
        if params[:1] in (b"?", b">", b"<", b"=", b"!"):
            private, params = params[:1].decode("latin-1"), params[1:]
        if final in ("h", "l") or private in (">", "<", "="):
            return f"CSI {private}{params.decode('latin-1')} {final}".replace("  ", " ")
        collapsed = _NUM.sub(b"{n}", params).decode("latin-1")
        return f"CSI {private}{collapsed} {final}".replace("  ", " ")
    if tok.kind == K_OSC:
        body = raw[2:]
        num = body.split(b";", 1)[0].decode("latin-1", "replace")
        return f"OSC {num}"
    if tok.kind == K_DCS:
        return "DCS"
    if tok.kind == K_STR:
        return "APC/PM/SOS"
    return f"ESC {raw[1:].decode('latin-1', 'replace')}"


# ── The capability table ────────────────────────────────────
class Feature(NamedTuple):
    name: str
    match: Callable[[Token], bool]
    supported: bool          # can the V1 line-oriented renderer honour it?
    note: str


def _csi(final: str, private: str = "", param: str | None = None) -> Callable[[Token], bool]:
    def m(tok: Token) -> bool:
        if tok.kind != K_CSI:
            return False
        body = tok.raw[2:].decode("latin-1")
        if not body.endswith(final):
            return False
        params = body[:-1]
        if private:
            if not params.startswith(private):
                return False
            params = params[1:]
        elif params[:1] in ("?", ">", "<", "=", "!"):
            return False
        if param is None:
            return True
        return param in params.split(";")
    return m


def _mode(num: str, setter: bool = True) -> Callable[[Token], bool]:
    return _csi("h" if setter else "l", private="?", param=num)


def _osc(num: str) -> Callable[[Token], bool]:
    def m(tok: Token) -> bool:
        return tok.kind == K_OSC and tok.raw[2:].split(b";", 1)[0] == num.encode()
    return m


def _abs_cursor(tok: Token) -> bool:
    """CSI <r>;<c> H / f — absolute cursor addressing with an explicit ROW. `CSI H`
    (home) and `CSI {n} G` (column-only) are NOT this; only a row+column move forces a
    screen-buffer model."""
    if tok.kind != K_CSI:
        return False
    body = tok.raw[2:].decode("latin-1")
    if body[-1] not in "Hf" or body[:1] in ("?", ">", "<"):
        return False
    return ";" in body[:-1]


def _decstbm(tok: Token) -> bool:
    """CSI <t>;<b> r — set scrolling region. `CSI r` with NO parameters is the RESET
    (full-screen region) and is materially different: it does not create a region."""
    if tok.kind != K_CSI:
        return False
    body = tok.raw[2:].decode("latin-1")
    return body.endswith("r") and body[:1] not in ("?", ">", "<") and len(body) > 1


# `supported` = can V1's SCROLLING, LINE-ORIENTED renderer ([CXC-TERM-GRID]) honour it
# without a screen buffer? It is a measured-and-decided column, not an aspiration.
FEATURES: tuple[Feature, ...] = (
    # ── the four that decide whether V1 needs a screen buffer ──
    Feature("alt_screen_1049", _mode("1049"), False, "alt screen buffer — forces a screen-buffer model"),
    Feature("alt_screen_47", _mode("47"), False, "legacy alt screen"),
    Feature("alt_screen_1047", _mode("1047"), False, "legacy alt screen"),
    Feature("abs_cursor_addr", _abs_cursor, False, "CUP with a row — random access to the screen"),
    Feature("scroll_region", _decstbm, False, "DECSTBM — a real scrolling region"),
    Feature("clear_screen", _csi("J", param="2"), False, "ED 2 — erase the whole display"),
    Feature("clear_scrollback", _csi("J", param="3"), False, "ED 3 — erase scrollback"),
    # ── input-plane modes the widget must TRACK, not render ──
    # Each carries its RESET row too.  A mode the widget tracks but whose `l` form it
    # cannot recognize would latch ON forever — the terminal widget would keep wrapping
    # pastes in \x1b[200~ after the child turned bracketed paste off, i.e. leak visible
    # garbage into the prompt.  Set and reset are ONE capability, spelled twice.
    Feature("bracketed_paste", _mode("2004"), True, "tracked; drives [CXC-TERM-PASTE] wrapping"),
    Feature("bracketed_paste_off", _mode("2004", setter=False), True, "the reset half"),
    Feature("mouse_x10", _mode("9"), True, "no mouse forwarding in V1 — tracked and ignored"),
    Feature("mouse_x10_off", _mode("9", setter=False), True, "the reset half"),
    Feature("mouse_normal", _mode("1000"), True, "tracked and ignored"),
    Feature("mouse_normal_off", _mode("1000", setter=False), True, "the reset half"),
    Feature("mouse_button", _mode("1002"), True, "tracked and ignored"),
    Feature("mouse_button_off", _mode("1002", setter=False), True, "the reset half"),
    Feature("mouse_any", _mode("1003"), True, "tracked and ignored"),
    Feature("mouse_any_off", _mode("1003", setter=False), True, "the reset half"),
    Feature("mouse_sgr", _mode("1006"), True, "tracked and ignored"),
    Feature("mouse_sgr_off", _mode("1006", setter=False), True, "the reset half"),
    Feature("mouse_urxvt", _mode("1015"), True, "tracked and ignored"),
    Feature("mouse_urxvt_off", _mode("1015", setter=False), True, "the reset half"),
    Feature("focus_events", _mode("1004"), True, "tracked; the widget can answer from its own focus"),
    Feature("kitty_keyboard", _csi("u", private=">"), True, "progressive keyboard flags — reply not required"),
    Feature("modify_other_keys", _csi("m", private=">"), True, "xterm modifyOtherKeys — input-plane only"),
    # ── cheap render-plane sequences a line renderer handles ──
    Feature("sync_output", _mode("2026"), True, "synchronized output — becomes a frame-coalescing hint"),
    Feature("cursor_hide", _mode("25", setter=False), True, "honoured via screen.cursor.hidden"),
    Feature("cursor_show", _mode("25"), True, "honoured via screen.cursor.hidden"),
    Feature("autowrap", _mode("7"), True, "DECAWM — pyte implements it"),
    Feature("sgr", _csi("m"), True, "colour/attributes, incl. 256 and truecolour"),
    Feature("cursor_col", _csi("G"), True, "CHA — column-only, safe in a line model"),
    Feature("cursor_up", _csi("A"), True, "CUU"),
    Feature("cursor_down", _csi("B"), True, "CUD"),
    Feature("cursor_fwd", _csi("C"), True, "CUF"),
    Feature("cursor_back", _csi("D"), True, "CUB"),
    Feature("cursor_home", _csi("H"), True, "CUP with no row — home"),
    Feature("erase_line", _csi("K"), True, "EL"),
    Feature("erase_display_end", _csi("J", param="0"), True, "ED 0 — to end of display"),
    Feature("insert_line", _csi("L"), True, "IL"),
    Feature("delete_line", _csi("M"), True, "DL"),
    Feature("delete_char", _csi("P"), True, "DCH"),
    Feature("erase_char", _csi("X"), True, "ECH"),
    Feature("save_cursor", lambda t: t.kind == K_ESC and t.raw == b"\x1b7", True, "DECSC"),
    Feature("restore_cursor", lambda t: t.kind == K_ESC and t.raw == b"\x1b8", True, "DECRC"),
    Feature("scroll_region_reset", lambda t: t.kind == K_CSI and t.raw == b"\x1b[r", True,
            "DECSTBM reset to full screen — a no-op for a line renderer"),
    # ── things that need an ANSWER or are otherwise special ──
    Feature("da1_query", lambda t: t.kind == K_CSI and t.raw == b"\x1b[c", False,
            "Device Attributes QUERY — the child WAITS for a reply the widget must send"),
    Feature("dsr_query", _csi("n", private="?"), False,
            "DECDSR (e.g. \\x1b[?6n, cursor-position report). MEASURED HAZARD: pyte 0.8.2 "
            "RAISES TypeError('report_device_status() got an unexpected keyword argument "
            "private') on this, and Claude Code emits it at STARTUP in every profile — so "
            "an unfiltered stream.feed() crashes on the first burst. It MUST be intercepted "
            "by the upstream pre-scan and ANSWERED, never passed to the emulator"),
    Feature("dsr_query_ansi", _csi("n"), False,
            "DSR — the child waits for a reply"),
    Feature("xtversion_query", _csi("q", private=">"), False,
            "XTVERSION query — expects a DCS reply"),
    Feature("cursor_style", _csi("q", private=" "), True, "DECSCUSR — cosmetic"),
    Feature("theme_notify", _mode("2031"), True, "theme-change notifications — tracked and ignored"),
    Feature("osc_title", lambda t: _osc("0")(t) or _osc("2")(t), True, "window title — surfaced in the strip"),
    Feature("osc_hyperlink", _osc("8"), True, "OSC 8 — render as text in V1"),
    Feature("osc_color_query", lambda t: _osc("10")(t) or _osc("11")(t), False,
            "foreground/background QUERY — the child waits for a reply"),
    Feature("dcs", lambda t: t.kind == K_DCS, False, "DCS — not parsed by V1"),
    Feature("scroll_up", _csi("S"), False, "SU — pyte 0.8.2 does not implement it"),
    Feature("scroll_down", _csi("T"), False, "SD — pyte 0.8.2 does not implement it"),
    Feature("window_ops", _csi("t"), True, "XTWINOPS — ignorable"),
    Feature("back_tab", _csi("Z"), False, "CBT — pyte 0.8.2 does not implement it"),
    Feature("repeat_char", _csi("b"), False, "REP — pyte 0.8.2 does not implement it"),
)

UNSUPPORTED = tuple(f for f in FEATURES if not f.supported)


def classify(tok: Token) -> list[str]:
    """Every feature name matching `tok` (usually zero or one)."""
    return [f.name for f in FEATURES if f.match(tok)]


def scan_unsupported(tokens: Iterable[Token]) -> list[tuple[str, bytes]]:
    """(feature_name, raw) for every token matching an UNSUPPORTED feature. This is the
    widget's pre-scan — run it BEFORE feeding the emulator ([CXC-TERM-SEQTABLE])."""
    hits: list[tuple[str, bytes]] = []
    for tok in tokens:
        for f in UNSUPPORTED:
            if f.match(tok):
                hits.append((f.name, tok.raw))
    return hits
