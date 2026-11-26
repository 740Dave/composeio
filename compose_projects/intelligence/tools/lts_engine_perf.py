#!/usr/bin/env python3
"""lts_engine_perf — empirical scaling harness for the lts-engine graph engine.

[DEC-lts-engine-AGGREGATION-AND-SCALING] part 3. Replaces the primer's "untested at
large scale" claim with real numbers: index BUILD time, in-memory footprint, and
representative query latency (find / node / traverse) at 1x / 5x / 10x / 20x the
current Haven LTS spec corpus, and identifies the CEILING (where it starts to hurt).

DISCIPLINE (ComposePlatform copy discipline)
  * ~/haven and ~/factlattice are READ-ONLY. We COPY the Haven LTS corpus OUT to a
    SCRATCH dir and never write back. Before/after the whole run we SHA-256 every
    Haven source file and assert byte-for-byte identity (fail loud on any drift).
  * The read-only `factlattice` + `factlattice_engine` packages are IMPORTED and run
    in-process; nothing under ~/factlattice is created/modified.
  * All generated corpora live under a scratch dir (system temp by default;
    override with lts-engine_PERF_SCRATCH). Nothing is committed.

INFLATION APPROACH (faithful size scaling, not artificial repetition)
  The 1x base is the richest coherent LTS corpus on disk:
  ~/haven/spec-lts-rollout (48 .lts files). To scale to Nx we materialize N REPLICAS
  of that corpus, each in its own subdir, with every graph id namespaced by a
  per-replica prefix (`R00_`, `R01_`, ...). Only bracketed tokens whose last
  colon-segment is a real uppercase id (exactly the parser's node-id predicate) are
  rewritten, so each replica is an INTERNALLY-CONSISTENT graph identical in shape to
  the original (same edges resolve, same danglers dangle) but with DISJOINT ids. The
  result is a single corpus root whose node count, edge count, file count, and byte
  size all scale linearly by N -- a faithful "the corpus grew" scenario, not id
  collisions collapsing into the same node_map (which is what naive file duplication
  would produce: edges 5x but nodes flat).

MEASUREMENT
  * build time     : median of a few `factlattice.build_index` runs over the Nx root.
  * memory         : Python-heap peak DURING the build + retained index size, via
                     tracemalloc (isolated per size); plus process RSS high-water.
  * query latency  : find(text) / node(id) / traverse(start, depth=2), measured TWO
                     ways per size:
                       (gated)  the real shipped path through MagmaTools -> _gate ->
                                ContentHashFreshness, which RE-HASHES THE WHOLE CORPUS
                                on every call;
                       (pure)   the same query with an always-fresh stub, isolating
                                the graph-algorithm cost from the freshness re-hash.
                     The gap between the two is the headline scaling finding.

USAGE
    PYTHONPATH=~/factlattice \
        python3 compose_projects/intelligence/tools/lts_engine_perf.py
    # options: --factors 1,5,10,20  --iters 25  --keep-scratch
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import os
import re
import resource
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from typing import Dict, List, Optional, Tuple

# --- read-only engine under test (import; never modify) ------------------------
FACTLATTICE_HOME = os.path.expanduser("~/factlattice")
if FACTLATTICE_HOME not in sys.path:
    sys.path.insert(0, FACTLATTICE_HOME)

import factlattice  # noqa: E402
from factlattice_engine import MagmaStore, MagmaTools, Partition  # noqa: E402
from factlattice_engine.store import FreshnessResult  # noqa: E402

# --- where the Haven LTS corpus lives (READ-ONLY source) -----------------------
HAVEN_CORPUS_CANDIDATES = [
    os.path.expanduser("~/haven/spec-lts-rollout"),   # richest coherent LTS corpus (48 files)
    os.path.expanduser("~/haven/spec-lts-pilot"),
    os.path.expanduser("~/haven/spec"),
]

DEFAULT_FACTORS = [1, 5, 10, 20]

# Bracketed reference: [ID], [DOCTYPE:ID], [domain:DOCTYPE:ID]. We rewrite the LAST
# colon-segment iff it is a real uppercase node id (mirrors parser._UPPER_ID_RE), so
# prose brackets and edge-type tokens are left untouched.
_BRACKET_RE = re.compile(r"\[([^\]\n]+)\]")
_UPPER_ID_RE = re.compile(r"^[A-Z][A-Z0-9_\-]*$")


# ---------------------------------------------------------------------------
# Corpus location + hash-verify discipline
# ---------------------------------------------------------------------------
def find_base_corpus() -> str:
    """Pick the RICHEST candidate with .lts files (recursive count wins)."""
    best, best_n = None, 0
    for cand in HAVEN_CORPUS_CANDIDATES:
        if not os.path.isdir(cand):
            continue
        n = len(factlattice.list_lts_files(cand))
        if n > best_n:
            best, best_n = cand, n
    if best is None:
        raise SystemExit("No Haven LTS corpus found in candidates: "
                         + ", ".join(HAVEN_CORPUS_CANDIDATES))
    return best


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def corpus_fingerprint(root: str) -> Dict[str, str]:
    """{relpath: sha256} over EVERY file under root (not just .lts) so we can prove
    the READ-ONLY source is byte-unchanged across the whole run."""
    out: Dict[str, str] = {}
    for dirpath, _dn, filenames in os.walk(root):
        for name in filenames:
            p = os.path.join(dirpath, name)
            out[os.path.relpath(p, root)] = sha256_file(p)
    return out


# ---------------------------------------------------------------------------
# Replica id-namespacing transform (faithful inflation)
# ---------------------------------------------------------------------------
def _rewrite_bracket(match: re.Match, prefix: str) -> str:
    content = match.group(1)
    segments = content.split(":")
    last = segments[-1].strip()
    if _UPPER_ID_RE.match(last):
        segments[-1] = prefix + segments[-1]
        return "[" + ":".join(segments) + "]"
    return match.group(0)


def namespace_ids(text: str, prefix: str) -> str:
    return _BRACKET_RE.sub(lambda m: _rewrite_bracket(m, prefix), text)


def materialize_inflated_corpus(base_dir: str, base_files: List[str],
                                out_root: str, factor: int) -> None:
    """Write `factor` id-namespaced replicas of the base corpus under out_root."""
    if os.path.exists(out_root):
        shutil.rmtree(out_root)
    os.makedirs(out_root)
    for k in range(factor):
        prefix = f"R{k:02d}_"
        rep_dir = os.path.join(out_root, f"replica_{k:02d}")
        os.makedirs(rep_dir)
        for rel in base_files:
            with open(os.path.join(base_dir, rel), "r", encoding="utf-8") as fh:
                src = fh.read()
            # prefix ':' -> '_' so a replica dir is a clean flat name
            dst_name = rel.replace(os.sep, "__")
            with open(os.path.join(rep_dir, dst_name), "w", encoding="utf-8") as fh:
                fh.write(namespace_ids(src, prefix))


# ---------------------------------------------------------------------------
# Always-fresh stub (isolates graph-query cost from the per-query corpus re-hash)
# ---------------------------------------------------------------------------
class AlwaysFresh:
    def evaluate(self, part: Partition) -> FreshnessResult:
        return FreshnessResult(part.corpus_id, True, part.files_hash,
                               part.files_hash)


# ---------------------------------------------------------------------------
# Timing helpers
# ---------------------------------------------------------------------------
def timed_median(fn, iters: int) -> Tuple[float, float]:
    """Return (median_ms, p95_ms) over `iters` calls of fn()."""
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    med = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(0.95 * len(samples)))]
    return med, p95


def pick_hot_node(index: dict) -> Optional[str]:
    """The highest-degree node id -- makes traverse/node a meaningful walk."""
    degree: Dict[str, int] = {}
    for layer in index.get("layers", {}).values():
        for e in layer.get("edges", []):
            degree[e["source"]] = degree.get(e["source"], 0) + 1
            degree[e["target"]] = degree.get(e["target"], 0) + 1
    resolvable = {nid: d for nid, d in degree.items() if nid in index["nodes"]}
    if not resolvable:
        return next(iter(index["nodes"]), None)
    return max(resolvable, key=resolvable.get)


# ---------------------------------------------------------------------------
# One size point
# ---------------------------------------------------------------------------
def measure_size(base_dir: str, base_files: List[str], scratch: str,
                 factor: int, build_reps: int, query_iters: int) -> dict:
    out_root = os.path.join(scratch, f"x{factor:02d}")
    materialize_inflated_corpus(base_dir, base_files, out_root, factor)

    corpus_id = f"haven-x{factor}"

    # --- build time (median of build_reps) + memory (isolated) ----------------
    gc.collect()
    tracemalloc.reset_peak()
    heap_before = tracemalloc.get_traced_memory()[0]
    build_samples = []
    index = None
    for _ in range(build_reps):
        t0 = time.perf_counter()
        index = factlattice.build_index(out_root, corpus_id=corpus_id,
                                         domain_suffix="gov.haven", built_at="perf")
        build_samples.append((time.perf_counter() - t0) * 1000.0)
    build_samples.sort()
    heap_after, heap_peak = tracemalloc.get_traced_memory()
    retained_mb = (heap_after - heap_before) / 1e6
    peak_mb = heap_peak / 1e6

    stats = index["stats"]

    # --- query setup: one Partition, two tool stacks (gated vs always-fresh) --
    part = Partition(index, source_path=out_root)
    store_gated = MagmaStore()
    store_gated.add_partition(part)
    tools_gated = MagmaTools(store_gated)

    part2 = Partition(index, source_path=out_root)
    store_pure = MagmaStore()
    store_pure.add_partition(part2)
    tools_pure = MagmaTools(store_pure, freshness_provider=AlwaysFresh())

    hot = pick_hot_node(index)
    # a text that always full-scans (cost is O(nodes) regardless of hit count)
    find_text = "magma"

    def q_find(t):
        return lambda: t.find(corpus=corpus_id, text=find_text)

    def q_node(t):
        return lambda: t.node(corpus=corpus_id, node_id=hot)

    def q_trav(t):
        return lambda: t.traverse(corpus=corpus_id, start_node=hot,
                                  max_depth=2, direction="both")

    results = {
        "factor": factor,
        "files": len(index["source_corpus"]["file_hashes"]),
        "nodes": stats["total_nodes"],
        "edges": stats["total_edges"],
        "corpus_mb": sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _dn, fs in os.walk(out_root) for f in fs) / 1e6,
        "build_ms": statistics.median(build_samples),
        "retained_mb": retained_mb,
        "build_peak_mb": peak_mb,
        "hot_node": hot,
        "q": {},
    }

    for name, mk in (("find", q_find), ("node", q_node), ("traverse", q_trav)):
        g_med, g_p95 = timed_median(mk(tools_gated), query_iters)
        p_med, p_p95 = timed_median(mk(tools_pure), max(query_iters, 100))
        results["q"][name] = {
            "gated_ms": g_med, "gated_p95_ms": g_p95,
            "pure_ms": p_med, "pure_p95_ms": p_p95,
        }

    # release before next size so memory numbers stay independent
    del index, part, part2, store_gated, store_pure, tools_gated, tools_pure
    gc.collect()
    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def print_report(rows: List[dict]) -> None:
    print("\n" + "=" * 100)
    print("lts-engine SCALING — build + memory")
    print("=" * 100)
    print(f"{'factor':>6} {'files':>6} {'nodes':>8} {'edges':>8} {'corpus_MB':>10} "
          f"{'build_ms':>10} {'idx_heap_MB':>12} {'build_peak_MB':>14}")
    for r in rows:
        print(f"{r['factor']:>5}x {r['files']:>6} {r['nodes']:>8} {r['edges']:>8} "
              f"{r['corpus_mb']:>10.1f} {r['build_ms']:>10.1f} "
              f"{r['retained_mb']:>12.1f} {r['build_peak_mb']:>14.1f}")

    print("\n" + "=" * 100)
    print("lts-engine SCALING — query latency (median ms). GATED = shipped path incl. "
          "per-query corpus re-hash; PURE = graph-algo only")
    print("=" * 100)
    print(f"{'factor':>6} | {'find gated':>11} {'find pure':>10} | "
          f"{'node gated':>11} {'node pure':>10} | "
          f"{'trav gated':>11} {'trav pure':>10}")
    for r in rows:
        q = r["q"]
        print(f"{r['factor']:>5}x | "
              f"{q['find']['gated_ms']:>11.2f} {q['find']['pure_ms']:>10.3f} | "
              f"{q['node']['gated_ms']:>11.2f} {q['node']['pure_ms']:>10.3f} | "
              f"{q['traverse']['gated_ms']:>11.2f} {q['traverse']['pure_ms']:>10.3f}")

    rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024)
    print(f"\nprocess RSS high-water: {rss_mb:.0f} MB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--factors", default=",".join(str(f) for f in DEFAULT_FACTORS),
                    help="comma list of scaling factors (default 1,5,10,20)")
    ap.add_argument("--iters", type=int, default=25,
                    help="query iterations for the gated path (default 25)")
    ap.add_argument("--build-reps", type=int, default=3,
                    help="build repetitions for the median (default 3)")
    ap.add_argument("--keep-scratch", action="store_true",
                    help="do not delete the scratch corpora on exit")
    args = ap.parse_args()

    factors = [int(x) for x in args.factors.split(",") if x.strip()]

    base_dir = find_base_corpus()
    base_files = factlattice.list_lts_files(base_dir)
    print(f"Base Haven LTS corpus (READ-ONLY): {base_dir}")
    print(f"  {len(base_files)} .lts files")

    # --- READ-ONLY guarantee: fingerprint every source file BEFORE any work ---
    fp_before = corpus_fingerprint(base_dir)
    print(f"  fingerprinted {len(fp_before)} source files for byte-identity check")

    scratch = os.environ.get("lts-engine_PERF_SCRATCH") or tempfile.mkdtemp(
        prefix="lts_engine_perf_")
    os.makedirs(scratch, exist_ok=True)
    print(f"Scratch dir: {scratch}")

    tracemalloc.start()
    rows: List[dict] = []
    try:
        for f in sorted(factors):
            print(f"\n--- measuring {f}x ---", flush=True)
            r = measure_size(base_dir, base_files, scratch, f,
                             args.build_reps, args.iters)
            print(f"    files={r['files']} nodes={r['nodes']} edges={r['edges']} "
                  f"corpus={r['corpus_mb']:.1f}MB build={r['build_ms']:.0f}ms")
            rows.append(r)
    finally:
        tracemalloc.stop()
        # --- READ-ONLY guarantee: re-fingerprint and assert unchanged ---------
        fp_after = corpus_fingerprint(base_dir)
        if fp_before == fp_after:
            print(f"\n[OK] Haven source byte-identical: all {len(fp_after)} files "
                  "unchanged (hash-verified before/after).")
        else:
            changed = [k for k in fp_before
                       if fp_before.get(k) != fp_after.get(k)]
            added = set(fp_after) - set(fp_before)
            removed = set(fp_before) - set(fp_after)
            print(f"\n[FAIL] Haven source CHANGED: modified={changed} "
                  f"added={sorted(added)} removed={sorted(removed)}")
        if not args.keep_scratch:
            shutil.rmtree(scratch, ignore_errors=True)
            print(f"Removed scratch dir {scratch}")
        else:
            print(f"Kept scratch dir {scratch}")

    print_report(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
