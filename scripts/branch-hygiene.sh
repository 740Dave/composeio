#!/usr/bin/env bash
# branch-hygiene — the durable branch-hygiene instrument.
#
# Thin wrapper over the NATIVE worktrunk landed oracle (rewritten 2026-08-26):
# a branch is landed IFF its tip is an ancestor of origin/main (rebase-ff) OR wt
# reports it `⊂` integrated (landed via different history). The old patch-id/
# file-diff heuristic is retired. WHY: [DEC-PUSH-IS-FREE] is falsified for push
# but not for land. Logic lives in scripts/branch_hygiene.py; this .sh is the
# stable entrypoint the wake/dusk skills call.
#
# Usage: scripts/branch-hygiene.sh [base-ref]   (default base: origin/main)
# Exits non-zero on drift; the stranded/stale worklist is the failure message.
here="$(dirname "$0")"
exec python3 "$here/branch_hygiene.py" audit "$@"
