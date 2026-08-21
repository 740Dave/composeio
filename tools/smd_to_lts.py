#!/usr/bin/env python3
"""SMD -> FactLattice (.lts) corpus migration.

A *migration*, not a rewrite: per FactLattice EXEC-SUMMARY, migrating an SMD corpus
is (1) rename .smd -> .lts, (2) add the `domain:` frontmatter binding the doc to its
domain profile, (3) rewrite intra-corpus cross-references to the new extension.
Content/notation is NOT rewritten (that is a separate enrichment pass).

Surgical ref rule: only references whose basename matches a file *in this corpus* are
rewritten. Cross-corpus references (e.g. ../spec/*composeio*.smd, the SMD spec itself)
are left untouched because those corpora are not being migrated.

Usage:  python3 smd_to_lts.py <specs_dir> <domain> [--apply]
        (default is dry-run; --apply performs the migration)
"""
import os, sys, glob, re

def main():
    specs_dir = sys.argv[1]
    domain = sys.argv[2]
    apply = "--apply" in sys.argv
    # Where converted sources go. Sibling of the corpus, not inside it, so the archived
    # .smd never lands under a corpus root and gets parsed as content.
    archive_dir = os.path.join(os.path.dirname(os.path.abspath(specs_dir.rstrip("/"))),
                               os.path.basename(specs_dir.rstrip("/")) + "-archived-smd")

    # The corpus = real .smd files in this dir (exclude symlinks -> other corpora).
    files = sorted(f for f in glob.glob(os.path.join(specs_dir, "*.smd"))
                   if not os.path.islink(f))
    sibling_smd = {os.path.basename(f) for f in files}          # {"00-....smd", ...}
    rewrite = {n: n[:-4] + ".lts" for n in sibling_smd}          # .smd -> .lts

    print(f"Corpus: {len(files)} files in {specs_dir}")
    print(f"Domain: {domain}\n")

    total_ref_rewrites = 0
    for f in files:
        content = open(f, encoding="utf-8").read()
        base = os.path.basename(f)

        # (2) inject domain into frontmatter (before closing --- of the leading block)
        domain_added = False
        if content.startswith("---") and "\ndomain:" not in content.split("\n---", 1)[0]:
            lines = content.split("\n")
            for i in range(1, len(lines)):
                if lines[i].strip() == "---":
                    lines.insert(i, f"domain: {domain}")
                    domain_added = True
                    break
            content = "\n".join(lines)

        # (3) surgical ref rewrite: only sibling basenames
        refs_here = 0
        for smd_name, lts_name in rewrite.items():
            n = content.count(smd_name)
            if n:
                content = content.replace(smd_name, lts_name)
                refs_here += n
        total_ref_rewrites += refs_here

        target = f[:-4] + ".lts"
        print(f"  {base:42s} -> {os.path.basename(target):42s} "
              f"[domain:{'+' if domain_added else '-'}] refs:{refs_here}")

        if apply:
            open(target, "w", encoding="utf-8").write(content)

    if apply:
        # ⛔ THE SOURCE IS ARCHIVED, NEVER DELETED. David 2026-08-16, ruling on this
        # exact line: deleting the .smd is "a grave design error ... I could see moving
        # them to some other dir for archiving, but not deletion."
        #
        # WHY THIS IS CODE AND NOT A NOTE IN THE RUNBOOK: the porting process document
        # already prohibited `--apply`, and a prohibition that lives in a DOCUMENT is
        # the weakest guard there is. Three measured instances the same night of the
        # stronger version failing -- bus_storage's containment living inside one
        # handler six siblings could not reach; the hookup crossing report computed and
        # discarded at its only call site; SDL_VIDEODRIVER set in one wrapper and in no
        # conftest. A guard that lives in one invocation path is a property of that
        # path, not a guard. So the capability is REMOVED rather than discouraged: this
        # tool can no longer destroy a source file, whatever it is asked to do.
        #
        # A port is also not proven at the moment of conversion -- gap-analysis, edge
        # assessment and landing all come after it, and each can send a file BACK.
        # The source must outlive every one of them.
        os.makedirs(archive_dir, exist_ok=True)
        archived, collisions = 0, []
        for f in files:
            dest = os.path.join(archive_dir, os.path.basename(f))
            if os.path.exists(dest):           # never clobber a prior archive
                collisions.append(os.path.basename(f))
                continue
            os.rename(f, dest)
            archived += 1
        print(f"\nAPPLIED. Wrote {len(files)} .lts, ARCHIVED {archived} .smd -> "
              f"{archive_dir} (deleted 0), rewrote {total_ref_rewrites} intra-corpus refs.")
        if collisions:
            print(f"  ⛔ {len(collisions)} source(s) LEFT IN PLACE -- an archived copy "
                  f"already exists and was not overwritten: {', '.join(collisions)}")
    else:
        print(f"\nDRY RUN. Would write {len(files)} .lts, ARCHIVE {len(files)} .smd -> "
              f"{archive_dir} (never delete), rewrite {total_ref_rewrites} intra-corpus "
              f"refs. Re-run with --apply.")

if __name__ == "__main__":
    main()
