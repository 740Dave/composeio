#!/usr/bin/env python3
"""Dummy interactive child — stands in for `claude` in the launcher proxy+relay selftest.

The launcher is now a TRANSPARENT PTY PROXY: it does NOT auto-answer startup dialogs and does
NOT type a move-note; the human answers dialogs themselves and the launcher's ONLY injection is
the `/compact <direction>` relay. So this dummy no longer blocks on menu selections — it just
produces some arbitrary child output (to prove the proxy forwards it byte-for-byte) and then
echoes stdin (to prove keystrokes — including the launcher's `/compact` relay — reach the child):

  0. Echoes its own argv as `ARGV:<space-joined args>` so the selftest can assert on the
     launcher's spawn args (in RESUME/MOVE mode: `--resume <id>` + the single dev-channel flag).
  1. Prints a few lines of realistic-looking startup output — including `STARTUP_MARKER` and the
     kind of dialog text real Claude Code shows — WITHOUT blocking on any selection. The proxy
     must forward all of it to the launcher's output unmodified.
  2. Prints `READY`, then enters a RAW-MODE echo loop that faithfully models Claude Code's TUI
     input plane, so the launcher's three-phase `/compact` relay can be tested exactly:
       * it reads char-at-a-time (like a TUI in raw mode), NOT line-buffered;
       * a CARRIAGE RETURN (\r) or LINE FEED (\n) SUBMITS the accumulated line -> `ECHO:<line>`
         (the real TUI submits on \r — the launcher's relay relies on this);
       * an ESC (\x1b) CLEARS the current input buffer (models the TUI's ESC-clear, which the
         launcher's clear phase uses to wipe any pre-existing prompt text before typing /compact);
       * any other char accumulates into the buffer.
     An injected `/compact <direction>` shows up as an ECHO line, so the test can assert on it.
"""
import os
import sys
import termios
import tty


def out(s: str) -> None:
    sys.stdout.write(s)
    sys.stdout.flush()


def main() -> None:
    # ── Echo the argv we were spawned with, so the selftest can assert on the launcher's
    #    spawn args (e.g. `--resume <id>` + the dev-channel flag in RESUME/MOVE mode). ──
    out("ARGV:" + " ".join(sys.argv[1:]) + "\n")

    # ── Arbitrary startup output (NOT interactive here). The launcher no longer answers these;
    #    the human would. We just prove the proxy forwards whatever the child prints. ──
    out("STARTUP_MARKER claude booting\n")
    out("Please use --channels server:composeio-channel to enable this channel.\n")
    out("  1. I am using this for local development\n")
    out("  2. Exit\n")
    out("New MCP server found in this project: composeio-channel\n")
    out("Use this MCP server? (the human answers this, not the launcher)\n")

    # ── Ready: RAW-MODE, char-at-a-time echo loop (models the real `claude` TUI input plane). ──
    #    Real Claude Code puts its stdin into raw mode (ICRNL off -> literal \r; ICANON off ->
    #    char-at-a-time; ECHO off). We do the same so ESC-clear and \r-submit behave as they do
    #    against the real target the launcher's relay drives. Without raw mode the slave PTY is
    #    cooked (line-buffered, \r->\n translated, ESC inert) and would NOT model the TUI.
    fd = sys.stdin.fileno()
    try:
        saved = termios.tcgetattr(fd)
        tty.setraw(fd)
    except (termios.error, OSError):
        saved = None
    out("READY\n")
    # Accumulate BYTES (not chars): the launcher writes UTF-8, and a multi-byte char (e.g. an
    # em-dash) arrives as several 1-byte reads — decoding per-byte would mangle it. ESC (0x1b),
    # CR (0x0d), LF (0x0a) are ASCII and never appear inside a UTF-8 multi-byte sequence, so
    # byte-level detection of clear/submit is exact; we decode the whole line at submit time.
    buf = bytearray()
    try:
        while True:
            b = os.read(fd, 1)
            if not b:                      # EOF — parent closed the PTY
                break
            if b == b"\x1b":               # ESC -> clear the current input buffer
                buf.clear()
                continue
            if b in (b"\r", b"\n"):        # SUBMIT -> echo the accumulated line, then clear
                out("ECHO:" + buf.decode("utf-8", "replace") + "\n")
                buf.clear()
                continue
            buf += b                       # accumulate raw bytes
    finally:
        if saved is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


if __name__ == "__main__":
    main()
