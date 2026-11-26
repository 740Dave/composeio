"""compose_projects/intelligence/retrospective — the DETERMINISTIC perceive-half of the session-retrospective system
([SPEC:CXC-RETRO] corpora/platform-specs/39-session-retrospective.lts).

THE HARD BOUNDARY (see each module's docstring for detail): this package PERCEIVES and STAGES raw,
deterministic evidence from a session transcript. It never judges, scores, ranks by merit, or emits
findings ABOUT the human or the instance. A later human-blessed JUDGMENT pass (not built here) reasons
over what this package stages.

Modules:
  transcript_reader.py — shared JSONL streaming (factored, not forked; see its docstring for the
                         blast-radius decision re: permission_audit.py / transcript_publisher.py).
  db.py                — the SQLite metrics-store schema + connection helper (§5a).
  inject.py             — the dusk-parse tailer-injector: transcript -> SQLite rows (idempotent).
  pregenerate.py        — assembles the staged structured input for the (not-built) judgment pass.
"""
