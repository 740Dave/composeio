"""Generate a beep-out sheet from a ``.kicad_pcb``.

    python -m composeio.maker.beepout.cli BOARD.kicad_pcb -o docs/board-bringup/x-BEEPOUT.md

⛔ The input is the FABRICATED BOARD FILE. Pointing this at ``generate_pcb.py`` is not
supported and never will be: on ``rack_triple_gauge`` that script's net-id space does not
match the shipped board at all, and its trace emitter draws SDA out of a GND pin.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .derive import derive_sheet
from .netlist import parse_board
from .render import render_markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="composeio.maker.beepout")
    ap.add_argument("board", help="path to the .kicad_pcb")
    ap.add_argument("-o", "--out", help="write markdown here (default: stdout)")
    ap.add_argument("--summary", action="store_true",
                    help="print the receipt to stderr")
    args = ap.parse_args(argv)

    board = parse_board(args.board)
    sheet = derive_sheet(board)

    if not sheet.rows:
        print("REFUSED: derivation produced ZERO probe rows — a sheet with no rows is not "
              "a sheet.", file=sys.stderr)
        return 2

    md = render_markdown(sheet)
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(md, encoding="utf-8")
        target = str(p.resolve())
    else:
        sys.stdout.write(md)
        target = "<stdout>"

    if args.summary or args.out:
        print(f"wrote            {target}", file=sys.stderr)
        print(f"derived-from     {board.source_path}", file=sys.stderr)
        print(f"sha256           {board.source_sha256}", file=sys.stderr)
        print(f"rows             {len(sheet.rows)}  {sheet.by_category()}", file=sys.stderr)
        print(f"states           {sheet.by_state()}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
