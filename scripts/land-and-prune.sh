#!/usr/bin/env bash
# land-and-prune — delete a branch whose content has landed on origin/main.
#
# Seats prune-on-land as the STANDARD close of a landing: a branch (especially a
# wt-agent-* dispatch branch) whose commit just landed is deleted (local + its
# origin/ ref) in the SAME operation, not left for a later sweep. That is what
# stops the ~200-ref pile from re-forming.
#
# It REFUSES unless the branch is LANDED by the native oracle — its tip is an
# ancestor of the base (rebase-ff) OR wt reports it `⊂` integrated — so it can
# never delete unlanded work. Uses `git branch -D` on purpose: an integrated-via-
# different-history branch is not SHA-merged, so `-d` would refuse — the landed
# guard is the real safety, not `-d`.
#
# Usage: scripts/land-and-prune.sh <branch> [base-ref]   (default base: origin/main)
here="$(dirname "$0")"
exec python3 "$here/branch_hygiene.py" land-and-prune "$@"
