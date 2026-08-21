"""End-to-end self-test for the DETERMINISTIC session-sweep loose-ends linter.

Proves every detector against a PLANTED fixture with matched CLEAN controls, so
each detector must catch the planted loose end AND stay silent on the control.
The git detectors run against a REAL, HERMETIC git repo created in a tempdir —
no network, no shared state, git calls are genuine.

Planted vs. control, per detector:

  uncommitted_authored / git_out_of_sync
      loose_file.py   authored + left UNTRACKED   -> flagged (HIGH)
      committed.py    authored + committed        -> clean (control)
  subagent_disposition
      agent A completion summary says "left uncommitted" -> flagged (MED)
      agent B completion summary "all committed"         -> clean (control)
  unanswered_questions
      user "build the widget AND update the readme" — only widget recurs
          later                                          -> flagged (MED)
      user "add the button AND add the slider" — both recur -> clean (control)
  unhandled_errors
      `python3 .../missing_mod.py` fails, never retried  -> flagged (MED)
      `cat .../notes.txt` fails, then written            -> clean (control)
  open_loops
      "I'll wire the zigbee peripheral next." never closed -> flagged (LOW)
      "I'll write the config next." + later Write config   -> clean (control)

Run::  python3 -m composeio.session_sweep --selftest
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .sensor import SessionSweep


class _Checker:
    """Tiny PASS/FAIL harness (same shape as self_sensor.selftest)."""

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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def _build_repo(repo: Path) -> dict[str, str]:
    """A real git repo: an initial commit, one committed authored file (clean
    control), one authored file left UNTRACKED (the planted loose end)."""
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True, text=True)
    _git(repo, "config", "user.email", "sweep@test.local")
    _git(repo, "config", "user.name", "Sweep Test")
    (repo / "README.md").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "seed")

    committed = repo / "committed.py"
    committed.write_text("print('committed and clean')\n", encoding="utf-8")
    _git(repo, "add", "committed.py")
    _git(repo, "commit", "-m", "add committed.py")

    loose = repo / "loose_file.py"
    loose.write_text("print('authored but never committed')\n", encoding="utf-8")
    # deliberately NOT added -> untracked, the planted uncommitted_authored find.

    # a later-written file that closes the "config" open-loop control.
    (repo / "config.yaml").write_text("k: v\n", encoding="utf-8")
    _git(repo, "add", "config.yaml")
    _git(repo, "commit", "-m", "add config")

    return {"committed": str(committed), "loose": str(loose),
            "config": str(repo / "config.yaml")}


def _fixture_lines(repo: Path, paths: dict[str, str]) -> list[dict]:
    """The planted session .jsonl, in turn order."""
    missing = f"{repo}/missing_mod.py"
    notes = f"{repo}/notes.txt"
    widget = f"{repo}/widget.py"      # referenced but never created -> clean
    button = f"{repo}/button.py"
    slider = f"{repo}/slider.py"

    return [
        # -- authored files: one committed (clean), one left untracked --------
        {"type": "assistant", "message": {"usage": {"input_tokens": 10}, "content": [
            {"type": "tool_use", "id": "tu_w1", "name": "Write",
             "input": {"file_path": paths["committed"]}},
            {"type": "tool_use", "id": "tu_w2", "name": "Write",
             "input": {"file_path": paths["loose"]}},
        ]}},

        # -- subagent A: dispatched, completion says LEFT UNCOMMITTED ---------
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_taskA", "name": "Task",
             "input": {"subagent_type": "general-purpose",
                       "description": "scope the corpus state"}},
        ]}},
        # -- subagent B: dispatched, completion says all committed (control) --
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_taskB", "name": "Task",
             "input": {"subagent_type": "smd-linter",
                       "description": "lint the migrated corpus"}},
        ]}},
        {"type": "queue-operation", "content":
            "<task-notification><task-id>a1</task-id>"
            "<tool-use-id>tu_taskA</tool-use-id><status>completed</status>"
            "<summary>Scoped corpus. Wrote 3 files but left them uncommitted "
            "for the architect to verify.</summary></task-notification>"},
        {"type": "queue-operation", "content":
            "<task-notification><task-id>b1</task-id>"
            "<tool-use-id>tu_taskB</tool-use-id><status>completed</status>"
            "<summary>Lint clean. All changes committed and pushed."
            "</summary></task-notification>"},

        # -- user CONTROL ask: two things, BOTH fulfilled later --------------
        {"type": "user", "message": {"content":
            "Also add the button and add the slider now please."}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_wb", "name": "Write",
             "input": {"file_path": button}},
            {"type": "tool_use", "id": "tu_ws", "name": "Write",
             "input": {"file_path": slider}},
        ]}},

        # -- user PLANTED ask: two things, only ONE recurs later -------------
        {"type": "user", "message": {"content":
            "Please build the widget and update the readme docs."}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Done: I built the widget component."},
            {"type": "tool_use", "id": "tu_wwid", "name": "Write",
             "input": {"file_path": widget}},
        ]}},

        # -- unhandled error PLANTED: missing_mod.py fails, never retried -----
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_berr", "name": "Bash",
             "input": {"command": f"python3 {missing}",
                       "description": "run the module"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_berr", "is_error": True,
             "content": f"python3: can't open file '{missing}': "
                        f"[Errno 2] No such file or directory"},
        ]}},

        # -- unhandled error CONTROL: notes.txt fails, then IS written -------
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_bh", "name": "Bash",
             "input": {"command": f"cat {notes}", "description": "read notes"}},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "tu_bh", "is_error": True,
             "content": f"cat: {notes}: No such file or directory"},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_bfix", "name": "Bash",
             "input": {"command": f"echo hello > {notes}",
                       "description": "create notes"}},
        ]}},

        # -- open loop CONTROL: promise + later closure ----------------------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll write the config next."},
        ]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "tu_wc", "name": "Write",
             "input": {"file_path": paths["config"]}},
        ]}},

        # -- open loop PLANTED: promise never closed (unique token) ----------
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll wire the zigbee peripheral next."},
        ]}},

        # -- a compact boundary near the end (scoping check) -----------------
        {"type": "system", "subtype": "compact_boundary"},
    ]


def run() -> int:
    c = _Checker()
    print("ComposeIO session-sweep self-test (deterministic loose-ends linter)\n")

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td) / "repo"
        repo.mkdir()
        paths = _build_repo(repo)

        fixture = repo / "session.jsonl"
        fixture.write_text(
            "\n".join(json.dumps(e) for e in _fixture_lines(repo, paths)) + "\n",
            encoding="utf-8")

        report = SessionSweep(str(fixture), repo=str(repo)).run()
        cats: dict[str, list] = {}
        for f in report.findings:
            cats.setdefault(f["category"], []).append(f)

        def summaries(cat: str) -> str:
            return " | ".join(f["summary"] for f in cats.get(cat, []))

        # -- 1. uncommitted_authored ----------------------------------------
        print("D1 — uncommitted_authored (HIGH, git-verified):")
        ua = cats.get("uncommitted_authored", [])
        ua_files = [f["evidence"]["file"] for f in ua]
        c.check("the UNTRACKED authored file is flagged",
                any(f.endswith("loose_file.py") for f in ua_files),
                f"{len(ua)} finding(s)")
        c.check("the COMMITTED authored file is NOT flagged (control)",
                not any(f.endswith("committed.py") for f in ua_files),
                "committed.py absent")
        c.check("all uncommitted_authored findings are HIGH",
                all(f["confidence"] == "HIGH" for f in ua))

        # -- 2. git_out_of_sync ---------------------------------------------
        print("\nD2 — git_out_of_sync (HIGH, live git):")
        gs = cats.get("git_out_of_sync", [])
        c.check("dirty working tree surfaced (the untracked loose file)",
                any("uncommitted change" in f["summary"] for f in gs),
                summaries("git_out_of_sync")[:70])
        c.check("all git_out_of_sync findings are HIGH",
                all(f["confidence"] == "HIGH" for f in gs))

        # -- 3. subagent_disposition ----------------------------------------
        print("\nD3 — subagent_disposition (MED):")
        sd = cats.get("subagent_disposition", [])
        c.check("agent A (reported UNCOMMITTED output) is flagged",
                any("scope the corpus" in f["summary"] for f in sd),
                f"{len(sd)} finding(s)")
        c.check("agent B (all committed) is NOT flagged (control)",
                not any("lint the migrated" in f["summary"] for f in sd),
                "clean agent absent")

        # -- 4. unanswered_questions ----------------------------------------
        print("\nD4 — unanswered_questions (MED, partial fulfillment):")
        uq = cats.get("unanswered_questions", [])
        c.check("the ASK where only 'widget' recurred (readme dropped) is flagged",
                any("readme" in f["summary"] for f in uq),
                summaries("unanswered_questions")[:70])
        c.check("the ASK where BOTH button+slider recurred is NOT flagged (control)",
                not any("button" in f["summary"] or "slider" in f["summary"]
                        for f in uq),
                "fully-answered ask absent")

        # -- 5. unhandled_errors --------------------------------------------
        print("\nD5 — unhandled_errors (MED):")
        ue = cats.get("unhandled_errors", [])
        ue_cmds = [(f["evidence"].get("command") or "") for f in ue]
        c.check("the error with NO corrective follow-up is flagged",
                any("missing_mod.py" in cmd for cmd in ue_cmds),
                f"{len(ue)} finding(s)")
        c.check("the error that WAS handled (notes.txt written) is NOT flagged",
                not any("notes.txt" in cmd for cmd in ue_cmds),
                "handled error absent")

        # -- 6. open_loops --------------------------------------------------
        print("\nD6 — open_loops (LOW, candidates):")
        ol = cats.get("open_loops", [])
        ol_txt = " || ".join(f["evidence"].get("text", "") for f in ol)
        c.check("the UNCLOSED 'wire the zigbee' promise is flagged",
                "zigbee" in ol_txt, f"{len(ol)} candidate(s)")
        c.check("the CLOSED 'write the config' promise is NOT flagged (control)",
                "config" not in ol_txt, "closed loop suppressed")
        c.check("all open_loops findings are LOW (candidates)",
                all(f["confidence"] == "LOW" for f in ol))

        # -- 7. report surfaces: ranking, scoping, bus shape ----------------
        print("\nD7 — report surfaces (ranking / scoping / bus):")
        confs = [f["confidence"] for f in report.findings]
        c.check("findings ranked HIGH first",
                confs == sorted(confs, key=lambda x: {"HIGH": 0, "MED": 1, "LOW": 2}[x]),
                f"order={confs}")
        c.check("compact boundary recorded + findings carry after_compact scope",
                report.meta["last_compact_turn"] is not None
                and all("after_compact" in f for f in report.findings),
                f"last_compact_turn={report.meta['last_compact_turn']}")
        bus = dict(report.to_bus_lines())
        c.check("bus shape: self/loose_ends/count + by_category + top",
                bus.get("self/loose_ends/count") == len(report.findings)
                and isinstance(bus.get("self/loose_ends/by_category"), dict)
                and isinstance(bus.get("self/loose_ends/top"), list),
                f"count={bus.get('self/loose_ends/count')}")
        c.check("to_json round-trips",
                isinstance(json.loads(report.to_json()).get("findings"), list))

        # print the per-category tally for the record.
        print("\n  per-category finding counts on the fixture:")
        for cat in sorted(cats):
            print(f"    {cat:<24} {len(cats[cat])}")

    print()
    if c.failures:
        print(f"SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
