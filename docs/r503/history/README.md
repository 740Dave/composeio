# R503 archive — git history bundle (pointer)

The R503 design work was originally its own git repository (external dir
`${DATASHEETS_HOME}/R503/`, now deleted). That repo's **complete git history**
was captured as a `git bundle` before the directory was removed. The bundle is the
ONLY surviving copy of that history.

An 11 MB binary would permanently bloat this code repo (git can never gc it, and the
repo is slated for open-source release). The corpora home
(`~/.claude/factlattice_corpora/composeio/`) is itself a git repo, so parking there
would just relocate the bloat. The bundle + a full tree snapshot therefore live in a
**durable, Dropbox-synced, NON-repo** archive:

    ${MAKER_HOME}/composeio-archives/r503-history/

| file | size | sha256 |
|------|------|--------|
| `r503-final-history-8f23005.bundle` | 4.0 MB | `698873f7da1ee2860d16ea269f5845dacb225372d99a2e0829297a45bf34bfcf` |
| `r503-tree-snapshot-2026-08-17-post-handover.tar.gz` | 7.0 MB | `dfab9aa244c98cd54c536ab65da48c6a9f09e99e90aa14fed62a634be757b8b0` |

- The `.bundle` is the git history (HEAD `8f23005`; `git bundle verify` reports a
  complete history). Recover with:  `git clone <path>/r503-final-history-8f23005.bundle r503-recovered`
- The `.tar.gz` is a full working-tree snapshot taken post-handover 2026-08-17
  (superset of what was landed here — kept for belt-and-suspenders provenance).

If that archive location ever moves, update this pointer.
