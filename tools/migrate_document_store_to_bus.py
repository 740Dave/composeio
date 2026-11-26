#!/usr/bin/env python3
"""Migrate the retired document_store into the unified bus_storage store.

[SPEC:CXC-STORE] [CXC-STORE-DATA-ABSORPTION]. document_store is KILLED
([DEC-STORAGE-CONSOLIDATE-ONE-FACE]); its catalog is absorbed into bus_storage as
`bus://` path-addressed blobs. This is the CUTOVER tool: it reads a document_store
`docstore.db` (SQLite) plus its sibling `blobs/` tree and writes every document into
the unified store:

    JSON document   collection/doc_id   ->  bus://<collection>/<doc_id>.json  (the JSON bytes)
    binary document collection/doc_id   ->  bus://<collection>/<doc_id>       (the raw bytes)

The `.json` suffix on JSON docs is the doc->blob address rule (`doc://c/id` ==
`bus://c/id.json`, [CXC-STORE-CAP-GET]); binary blobs keep their id verbatim (a
`captures/cyd/6891.bmp` id becomes `bus://captures/cyd/6891.bmp`). resolve_doc /
query_docs only consider `.json` blobs, so the two never collide.

⛔ This tool is FIXTURE-PROVEN here but RUN LIVE by the next instance at the buildhost
cutover ([CXC-STORE-EV-02]): stop the running document_store daemon, run this against
the real 46-doc / 26-blob data, then verify the unified store serves them. It writes
through the store's HTTP `bus://` face (so containment + atomic-write apply); it does
NOT touch the filesystem directly.

Collection naming caveat: document_store sanitises a collection name to its SQLite table
name (`-`/`.` -> `_`), and on a server restart it reloads collections FROM the table
names — so the live collections are already the sanitised forms. This tool reads table
names as collection names, matching what the live store serves. The real catalog's 10
collections (apps, surfaces, board_profiles, components, bsg_planets, recipes,
fleet_devices, …) carry no special characters, so the mapping is identity in practice.

Usage:
    python3 tools/migrate_document_store_to_bus.py \\
        --db /var/lib/composeio/docstore.db \\
        --blobs /var/lib/composeio/blobs \\
        --store-url http://mqtt-broker.local:8420 \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path


def _resolve_import_put():
    """Import composeio.resolve.put_bytes / resolve_bytes, adding python/ to the path
    when run from a source checkout."""
    here = Path(__file__).resolve()
    repo = here.parent.parent
    py = repo / "python"
    if py.is_dir():
        sys.path.insert(0, str(py))
    from composeio import resolve  # noqa: E402
    return resolve


def iter_json_docs(db_path: Path):
    """Yield (collection, doc_id, data_dict) for every JSON document in the db.

    Collections are the non-internal tables; each row is (doc_id, data JSON)."""
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name != '_binary_meta'")
        tables = [r[0] for r in cur.fetchall()]
        for table in tables:
            cur = conn.execute(f"SELECT doc_id, data FROM [{table}]")
            for doc_id, data in cur.fetchall():
                yield table, doc_id, json.loads(data)
    finally:
        conn.close()


def iter_binary_docs(db_path: Path, blobs_dir: Path):
    """Yield (collection, doc_id, bytes, content_type) for every binary document.

    The blob file mirrors document_store's layout: blobs/<collection>/<doc_id>."""
    if not db_path.exists():
        return
    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_binary_meta'")
        if not cur.fetchone():
            return
        cur = conn.execute("SELECT collection, doc_id, content_type FROM _binary_meta")
        rows = cur.fetchall()
    finally:
        conn.close()
    for collection, doc_id, content_type in rows:
        blob = blobs_dir / collection / doc_id
        if not blob.is_file():
            raise FileNotFoundError(
                f"binary meta {collection}/{doc_id} has no file at {blob} (corrupt store)")
        yield collection, doc_id, blob.read_bytes(), content_type


def migrate(db_path: Path, blobs_dir: Path, resolve, *, dry_run: bool = False) -> dict:
    """Write every document/blob into the unified store. Returns a summary count."""
    json_n = bin_n = 0
    for collection, doc_id, data in iter_json_docs(db_path):
        uri = f"bus://{collection}/{doc_id}.json"
        body = json.dumps(data).encode()
        print(f"  JSON  {collection}/{doc_id}  -> {uri}  ({len(body)}B)")
        if not dry_run:
            resolve.put_bytes(uri, body)
        json_n += 1
    for collection, doc_id, raw, content_type in iter_binary_docs(db_path, blobs_dir):
        uri = f"bus://{collection}/{doc_id}"
        print(f"  BLOB  {collection}/{doc_id}  -> {uri}  ({len(raw)}B, {content_type})")
        if not dry_run:
            resolve.put_bytes(uri, raw)
        bin_n += 1
    return {"json_docs": json_n, "binary_docs": bin_n}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, type=Path, help="path to docstore.db")
    ap.add_argument("--blobs", type=Path, default=None,
                    help="path to the blobs/ dir (default: <db-dir>/blobs)")
    ap.add_argument("--store-url", default=None,
                    help="unified store base URL (sets COMPOSEIO_STORAGE_URL)")
    ap.add_argument("--dry-run", action="store_true", help="list what would migrate; write nothing")
    args = ap.parse_args(argv)

    if not args.db.exists():
        ap.error(f"db not found: {args.db}")
    blobs_dir = args.blobs or (args.db.parent / "blobs")
    if args.store_url:
        os.environ["COMPOSEIO_STORAGE_URL"] = args.store_url

    resolve = _resolve_import_put()
    print(f"Migrating {args.db} (blobs: {blobs_dir}) -> "
          f"{resolve.BUS_STORAGE_URL}{'  [DRY RUN]' if args.dry_run else ''}")
    summary = migrate(args.db, blobs_dir, resolve, dry_run=args.dry_run)
    print(f"Done: {summary['json_docs']} JSON docs, {summary['binary_docs']} binary docs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
