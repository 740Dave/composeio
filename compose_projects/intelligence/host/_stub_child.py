#!/usr/bin/env python3
"""_stub_child — a trivial autonomous child for IntelligenceHost PoC proofs ([CXC-IH-POC]).

Stands in for a real `claude -p` builder where launching real claude is too heavy/unsafe in the
test env. It carries its identity from the env exactly as a real child would: it reads
`INTELLIGENCE_ID` (set by IntelligenceHost.spawn), does ONE observable thing, then idles until the
Host stops it (SIGTERM). This is what lets the PoC prove the id-carry + spawn/list/stop lifecycle
on the wire without a heavyweight process.

Modes:
  --touch <file>   write the carried INTELLIGENCE_ID to <file>, then idle (the no-broker lifecycle
                   proof reads the file back to confirm the env was carried).
  (default)        just print the carried id and idle.

The real-broker no-clobber proof does NOT use this stub — it spawns self_publisher --namespace-by-id
directly (the REAL 5a code path), so `self/<id>/*` lands on the actual bus."""
import os
import sys
import time


def main(argv: list[str]) -> int:
    iid = os.environ.get("INTELLIGENCE_ID", "unknown")
    touch = None
    if "--touch" in argv:
        touch = argv[argv.index("--touch") + 1]
    sys.stderr.write(f"[stub_child] INTELLIGENCE_ID={iid} pid={os.getpid()}\n")
    if touch:
        with open(touch, "w", encoding="utf-8") as fh:
            fh.write(iid)
    # Idle until the Host terminates us (SIGTERM). A real builder would be doing its scoped slice.
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
