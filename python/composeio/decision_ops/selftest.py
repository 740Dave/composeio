"""End-to-end self-test for `composeio.decision_ops`.

Operates ENTIRELY on a synthetic fixture graph written to a TEMPORARY directory
-- NEVER on `decisions/decision-graph-composeux-architect.lts` or any other real
project graph. Proves the full round-trip the build brief's acceptance bar asks
for: add -> query returns it -> update its lifecycle-position -> resolve it,
staying lint-clean (no NEW forward dangles) after every op, each op idempotent,
and a dangle-introducing add() refused + rolled back.

Run::  python3 -m composeio.decision_ops --selftest
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from factlattice_engine.lint import lint
from .core import DangleRefusedError, add, query, resolve, update

FIXTURE_GRAPH = """\
---
domain: PLT.CUX
confidentiality: internal-only
artifact-readiness: system-design
created: 2026-07-01
---
# Fixture Decision Graph [SPEC:FIXTURE-DECISIONS]

> Synthetic fixture for `composeio.decision_ops` tests. Never the real graph.

### [DEC-FIXTURE-ROOT] A root fixture decision, referenced so nothing here starts as a dangle
- lifecycle-position: committed
- decided-in-session: 2026-07-01
- importance: medium
- rationale: exists purely so update()/resolve() targets have something to point at without inventing dangling refs.

### [DEC-FIXTURE-SECOND] A second fixture decision that depends on the root
- lifecycle-position: decided
- decided-in-session: 2026-07-01
- importance: low
- rationale: a second node to prove query() filtering and multi-node behavior.
- depends-on: [DEC-FIXTURE-ROOT]
"""


class _Checker:
    """Tiny PASS/FAIL harness (same shape as lts_lint/decision_extractor)."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)


def run() -> int:
    c = _Checker()
    print("composeio.decision_ops selftest (temp-copy fixture only)")

    with tempfile.TemporaryDirectory(prefix="decision-ops-selftest-") as tmp:
        path = Path(tmp) / "fixture-decisions.lts"
        path.write_text(FIXTURE_GRAPH, encoding="utf-8")
        graph = str(path)

        baseline = lint(graph, dangle=True)
        c.check("fixture starts lint-clean (no forward dangle / collision errors)",
                not baseline.has_errors, baseline.render_text())

        # -- 1. add() ---------------------------------------------------------
        print("\n1 -- add()")
        node = {
            "id": "DEC-FIXTURE-NEW", "title": "A newly added fixture decision",
            "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
            "importance": "medium",
            "rationale": "added by the decision_ops selftest, round-trip proof.",
            "depends-on": "[DEC-FIXTURE-ROOT]",
        }
        res1 = add(graph, node)
        c.check("add() reports changed=True", res1.changed)
        c.check("add() reports lint_clean=True", res1.lint_clean is True, res1.lint_summary)
        after_add = lint(graph, dangle=True)
        c.check("no forward-dangle errors on disk after add()", not after_add.has_errors)

        res1b = add(graph, node)
        c.check("add() is idempotent -- identical content -> changed=False",
                res1b.changed is False)
        c.check("idempotent add() did not duplicate the node",
                path.read_text(encoding="utf-8").count("### [DEC-FIXTURE-NEW]") == 1)

        # -- 2. query() returns it ---------------------------------------------
        print("\n2 -- query()")
        exact = query(graph, "DEC-FIXTURE-NEW")
        c.check("query(exact id) returns exactly one match", len(exact) == 1,
                f"got {len(exact)}")
        if exact:
            c.check("result carries lifecycle-position", exact[0]["lifecycle_position"] == "raised")
            c.check("result carries file:line location",
                    bool(exact[0]["source_file"]) and exact[0]["line"] is not None)
            c.check("result carries the rationale", "round-trip proof" in (exact[0]["rationale"] or ""))

        by_prefix = query(graph, "prefix:DEC")
        c.check("prefix query returns all 3 DEC-* nodes", len(by_prefix) == 3,
                f"got {len(by_prefix)}: {[r['id'] for r in by_prefix]}")

        by_text = query(graph, "text:round-trip")
        c.check("free-text query finds the new node via its rationale",
                any(r["id"] == "DEC-FIXTURE-NEW" for r in by_text))

        missing = query(graph, "id:DEC-DOES-NOT-EXIST")
        c.check("query for an absent id returns an empty list (not an error)",
                missing == [])

        # -- 3. update() lifecycle-position raised -> decided -> committed -----
        print("\n3 -- update()")
        res2 = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "decided"})
        c.check("update() reports changed=True", res2.changed)
        c.check("update() reports lint_clean=True", res2.lint_clean is True)
        r = query(graph, "id:DEC-FIXTURE-NEW")[0]
        c.check("lifecycle-position advanced to decided", r["lifecycle_position"] == "decided")

        res2b = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "decided"})
        c.check("update() is idempotent -- same value -> changed=False", res2b.changed is False)

        res2c = update(graph, "DEC-FIXTURE-NEW", {"lifecycle-position": "committed"})
        c.check("update() advances decided -> committed", res2c.changed)
        r = query(graph, "id:DEC-FIXTURE-NEW")[0]
        c.check("lifecycle-position now committed", r["lifecycle_position"] == "committed")

        c.check("update() preserved the node's other fields (rationale untouched)",
                "round-trip proof" in (query(graph, "id:DEC-FIXTURE-NEW")[0]["rationale"] or ""))

        after_update = lint(graph, dangle=True)
        c.check("no forward-dangle errors on disk after update()", not after_update.has_errors)

        # -- 4. resolve() -- annotate, never delete -----------------------------
        print("\n4 -- resolve()")
        before_ids = {row["id"] for row in query(graph, "prefix:DEC")}
        res3 = resolve(graph, "DEC-FIXTURE-SECOND", superseded_by="DEC-FIXTURE-NEW",
                       note="fixture supersession", today="2026-07-22")
        c.check("resolve() reports changed=True", res3.changed)
        c.check("resolve() reports lint_clean=True", res3.lint_clean is True)

        after_ids = {row["id"] for row in query(graph, "prefix:DEC")}
        c.check("resolve() did NOT delete the node (still present in query results)",
                "DEC-FIXTURE-SECOND" in after_ids and before_ids == after_ids)

        resolved = query(graph, "id:DEC-FIXTURE-SECOND")[0]
        c.check("resolved node's lifecycle-position -> superseded",
                resolved["lifecycle_position"] == "superseded")

        text_after = path.read_text(encoding="utf-8")
        c.check("annotation line present, naming the superseding node + date",
                "RESOLVED" in text_after and "DEC-FIXTURE-NEW" in text_after
                and "2026-07-22" in text_after)
        c.check("original title text still present verbatim (nothing deleted)",
                "A second fixture decision that depends on the root" in text_after)

        after_resolve = lint(graph, dangle=True)
        c.check("no forward-dangle errors on disk after resolve()", not after_resolve.has_errors)

        res3b = resolve(graph, "DEC-FIXTURE-SECOND", superseded_by="DEC-FIXTURE-NEW",
                        note="fixture supersession", today="2026-07-22")
        c.check("resolve() is idempotent -- repeat call -> changed=False", res3b.changed is False)
        c.check("idempotent resolve() did not duplicate the annotation",
                path.read_text(encoding="utf-8").count("RESOLVED — superseded by [DEC-FIXTURE-NEW]") == 1)

        # -- 5. add() refusal on a NEW forward dangle ---------------------------
        print("\n5 -- add() refuses a NEW forward dangle")
        before_bytes = path.read_text(encoding="utf-8")
        bad_node = {
            "id": "DEC-FIXTURE-BAD", "title": "A bad fixture decision that points nowhere",
            "lifecycle-position": "raised", "decided-in-session": "2026-07-22",
            "importance": "low",
            "rationale": "deliberately depends on a node that does not exist, "
                        "to prove the refusal + rollback path.",
            "depends-on": "[DEC-DOES-NOT-EXIST-ANYWHERE]",
        }
        refused = False
        try:
            add(graph, bad_node)
        except DangleRefusedError as e:
            refused = True
            c.check("DangleRefusedError names the new forward dangle",
                    "forward dangle" in str(e), str(e))
        c.check("add() that introduces a forward dangle is REFUSED", refused)

        after_bytes = path.read_text(encoding="utf-8")
        c.check("refused add() rolled back -- file bytes unchanged",
                after_bytes == before_bytes)
        c.check("the bad node is NOT queryable (it was never persisted)",
                query(graph, "id:DEC-FIXTURE-BAD") == [])
        after_refusal = lint(graph, dangle=True)
        c.check("no forward-dangle errors on disk after the refused add()",
                not after_refusal.has_errors)

        # -- 6. update()/resolve() against a missing node ------------------------
        print("\n6 -- missing-node errors")
        from .core import NodeNotFoundError
        raised_update = raised_resolve = False
        try:
            update(graph, "DEC-NOPE", {"importance": "high"})
        except NodeNotFoundError:
            raised_update = True
        try:
            resolve(graph, "DEC-NOPE", status="parked")
        except NodeNotFoundError:
            raised_resolve = True
        c.check("update() on a missing node raises NodeNotFoundError", raised_update)
        c.check("resolve() on a missing node raises NodeNotFoundError", raised_resolve)

    print()
    if c.failures:
        print(f"SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
