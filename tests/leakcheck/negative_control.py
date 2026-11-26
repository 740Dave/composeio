#!/usr/bin/env python3
"""Negative control of the leak check: plant what each gate must catch, and fail when a gate stays quiet.

    negative_control.py --private-dir DIR     # the real vocabulary (the merge queue's self-hosted job)
    negative_control.py --synthetic           # a throwaway vocabulary of made-up rules (hosted CI: proves the mechanism only)
    [--no-gitleaks]                           # skip the two gitleaks cases, announced (never the default)
        exit 0 every case gave its expected verdict; 1 a case did not (a gate stopped firing, or fired on clean input); 2 could not run

The cases are the rows of fixtures/cases.tsv. A row the runner does not implement, or a case it implements that the table does
not name, is a failure: the table and the runner cannot drift apart silently.

Why this exists: a green leak check means "none of these N patterns appeared". If a rule, the diff builder, the commit-message
scan or the PUBLISH-EXCLUDE refusal stops working, the check stays green on real PRs and looks exactly like a clean one. This
run turns that into a red queue build. The planted strings are each rule's OWN control (read from the vocabulary at run time),
so nothing private is written into this repository, and the scratch repositories live under the system temp folder.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import secrets
import string
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
TOOLS = REPO / "tools"
CASES = HERE / "fixtures" / "cases.tsv"
SYNTHETIC_VOCAB = (
    "# synthetic vocabulary for the hosted negative control: made-up terms, no real ones\n"
    "\\bexample-private-host\\.invalid\\b\texample-private-host.invalid\tsynthetic private host\tsynthetic-host\n"
    "(?i)/srv/example-private-share\\b\t/srv/example-private-share/x\tsynthetic private path\tsynthetic-path\n"
    "^compose_projects/example-withheld/[^\\s#]+$\tcompose_projects/example-withheld/x.py\tsynthetic path-anchored rule\tsynthetic-path-anchored\n"
)
SYNTHETIC_DENY = "# synthetic denylist\n(?i)\\bexample corp\\b\tExample Corp\tsynthetic organisation\tsynthetic-org\n"


def load_guard():
    spec = importlib.util.spec_from_file_location("_composeio_disclosure_guard", TOOLS / "disclosure_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rules_of(guard, pdir):
    """-> [(name, control)] for every rule the guard will load: its builtins, then both private files."""
    out = [(r.name, r.control) for r in guard.BUILTIN_RULES]
    for fn in ("publish-vocabulary.tsv", "disclosure-denylist.tsv"):
        p = pdir / fn
        if not p.is_file():
            raise SystemExit(f"[negative-control] COULD NOT RUN: {fn} is missing from the private dir")
        rules, problems, _rows = guard._parse_rows(p, p.read_text(encoding="utf-8"))
        if problems:
            raise SystemExit(f"[negative-control] COULD NOT RUN: {fn} has {len(problems)} refused row(s); run the guard's --selftest")
        out += [(r.name, r.control) for r in rules]
    return out


def sh(cwd, *args, env=None):
    return subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=env)


def g(repo, *args):
    p = sh(repo, "git", *args)
    if p.returncode != 0:
        raise SystemExit(f"[negative-control] COULD NOT RUN: git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout.strip()


def commit(repo, files, message, remove=()):
    for rel, text in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")
        g(repo, "add", rel)
    for rel in remove:
        g(repo, "rm", "-q", rel)
    g(repo, "-c", "user.name=negative-control", "-c", "user.email=negative-control@example.invalid",
      "commit", "-q", "--no-verify", "-m", message)
    return g(repo, "rev-parse", "HEAD")


def new_repo(root, name):
    repo = root / name
    repo.mkdir()
    g(repo, "init", "-q", "-b", "main")
    base = commit(repo, {"README.md": "scratch repository of the leak-check negative control\n",
                         "PUBLISH-EXCLUDE": "held/   # the negative control's excluded prefix\n"}, "base")
    return repo, base


def leakcheck(repo, base, pdir, work, tag, pr_text=None):
    report = work / f"{tag}.json"
    cmd = [sys.executable, "-I", str(TOOLS / "leakcheck.py"), "diff", "--repo", str(repo), "--base", base,
           "--private-dir", str(pdir), "--report", str(report)]
    if pr_text is not None:
        t = work / f"{tag}-pr.txt"
        t.write_text(pr_text, encoding="utf-8")
        cmd += ["--pr-text", str(t)]
    p = sh(REPO, *cmd)
    try:
        doc = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        doc = {"result": "no-report", "guard": {"findings": []}, "publish_exclude_findings": []}
    findings = doc.get("guard", {}).get("findings", []) + doc.get("publish_exclude_findings", [])
    return p.returncode, doc.get("result"), findings, p.stdout


def cleanup(repo, base, scope):
    p = sh(REPO, sys.executable, "-I", str(TOOLS / "cleanup_check.py"), "--repo", str(repo), "--base", base, "--scope", *scope)
    return p.returncode, p.stdout


def fake_key():
    alphabet = string.ascii_letters + string.digits
    return "sk-ant-api03-" + "".join(secrets.choice(alphabet) for _ in range(93)) + "AA"


def gitleaks(repo, base, history):
    cmd = [sys.executable, "-I", str(TOOLS / "ci_gitleaks.py"), "--tree", str(repo)]
    if history:
        cmd += ["--history-base", base, "--require-history"]
    env = dict(os.environ)
    env.pop("BASE_REF", None)
    p = sh(REPO, *cmd, env=env)
    return p.returncode, p.stdout


def main(argv=None):
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--private-dir")
    src.add_argument("--synthetic", action="store_true")
    ap.add_argument("--no-gitleaks", action="store_true")
    a = ap.parse_args(argv)

    listed = [ln.split("\t")[0] for ln in CASES.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    work = pathlib.Path(tempfile.mkdtemp(prefix="leakcheck-negctl-"))
    if a.synthetic:
        pdir = work / "private"
        pdir.mkdir()
        (pdir / "publish-vocabulary.tsv").write_text(SYNTHETIC_VOCAB, encoding="utf-8")
        (pdir / "disclosure-denylist.tsv").write_text(SYNTHETIC_DENY, encoding="utf-8")
    else:
        pdir = pathlib.Path(a.private_dir).expanduser().resolve()
    guard = load_guard()
    rules = rules_of(guard, pdir)
    names = sorted({n for n, _c in rules})
    probe = next(c for n, c in rules if n not in {r.name for r in guard.BUILTIN_RULES})
    empty = work / "empty-private"
    empty.mkdir()

    results = {}

    def case(cid, ok, detail):
        results[cid] = (ok, detail)

    # red-every-rule and refused-no-vocabulary share one range
    repo, base = new_repo(work, "terms")
    commit(repo, {"planted/controls.txt": "".join(c + "\n" for _n, c in rules)}, "add planted controls")
    rc, res, f, _out = leakcheck(repo, base, pdir, work, "every-rule")
    fired = {x["rule"] for x in f}
    missing = [n for n in names if n not in fired]
    case("red-every-rule", rc == 1 and res == "red" and not missing,
         f"exit {rc}, {res}, {len(f)} finding(s), {len(names) - len(missing)}/{len(names)} rule(s) fired"
         + (f"; MUTE: {missing}" if missing else ""))
    rc, res, f, _out = leakcheck(repo, base, empty, work, "no-vocab")
    case("refused-no-vocabulary", rc == 1 and res == "refused", f"exit {rc}, {res}")

    repo, base = new_repo(work, "message")
    commit(repo, {"notes.txt": "a clean line\n"}, f"a message that names {probe}")
    rc, res, f, _out = leakcheck(repo, base, pdir, work, "message")
    hit = [x for x in f if x["path"].endswith("commit-messages.txt")]
    case("red-commit-message", rc == 1 and res == "red" and hit, f"exit {rc}, {res}, {len(hit)} finding(s) in commit-messages.txt")

    repo, base = new_repo(work, "prtext")
    commit(repo, {"notes.txt": "a clean line\n"}, "a clean message")
    rc, res, f, _out = leakcheck(repo, base, pdir, work, "prtext", pr_text=f"PR title\n\nbody naming {probe}\n")
    hit = [x for x in f if x["path"].endswith("pr-text.txt")]
    case("red-pr-text", rc == 1 and res == "red" and hit, f"exit {rc}, {res}, {len(hit)} finding(s) in pr-text.txt")

    repo, base = new_repo(work, "typechange")
    commit(repo, {"docs/link": "a clean line\n"}, "add a file")
    base = g(repo, "rev-parse", "HEAD")
    (repo / "docs" / "link").unlink()
    home_rule = next(r for r in guard.BUILTIN_RULES if r.name == "absolute-home-path")
    target = home_rule.rx.search(home_rule.control).group(0) + "checkout"  # built from the guard's own control: no literal path here
    os.symlink(target, repo / "docs" / "link")
    g(repo, "add", "docs/link")
    commit(repo, {}, "replace the file with a link")
    rc, res, f, _out = leakcheck(repo, base, pdir, work, "typechange")
    hit = [x for x in f if x["path"] == "docs/link"]
    case("red-typechange-symlink", rc == 1 and res == "red" and hit, f"exit {rc}, {res}, {len(hit)} finding(s) at the type-changed path")

    repo, base = new_repo(work, "excluded")
    commit(repo, {"held/clean.txt": "a clean line\n"}, "a clean message")
    rc, res, f, _out = leakcheck(repo, base, pdir, work, "excluded")
    hit = [x for x in f if x["rule"] == "publish-exclude-path"]
    case("red-excluded-path", rc == 1 and res == "red" and len(hit) == 1, f"exit {rc}, {res}, {len(hit)} publish-exclude-path finding(s)")

    repo, base = new_repo(work, "cleanup")
    commit(repo, {"src/__pycache__/m.cpython-313.pyc": "x\n", "src/m.py.bak1": "x\n", "python/tests/render/captures/w.png": "x\n",
                  "held/x.txt": "a clean line\n", "other/y.txt": "a clean line\n"}, "a change carrying scratch files")
    rc, out = cleanup(repo, base, ["src/*", "python/*", "held/*"])
    n = sum(1 for ln in out.splitlines() if ln.startswith("  ") and "[" in ln)
    case("red-cleanup", rc == 1 and n == 5, f"exit {rc}, {n} violation(s)")
    repo, base = new_repo(work, "cleanup-ok")
    commit(repo, {"src/m.py": "VALUE = 1\n"}, "a clean change")
    rc, out = cleanup(repo, base, ["src/*"])
    case("green-cleanup", rc == 0, f"exit {rc}")

    clean, cbase = new_repo(work, "clean")
    commit(clean, {"app/config.py": 'BROKER = "mqtt-broker.local"  # placeholder; the deployment supplies the real host\n'},
           "config: name the broker by its placeholder")
    rc, res, f, _out = leakcheck(clean, cbase, pdir, work, "clean", pr_text="A clean PR\n\nNothing private here.\n")
    case("green-clean", rc == 0 and res == "green" and not f, f"exit {rc}, {res}, {len(f)} finding(s)")

    if a.no_gitleaks:
        print("[negative-control] NOTE: --no-gitleaks: the gitleaks cases were NOT run")
        for cid in ("gitleaks-red-tree", "gitleaks-red-history", "gitleaks-green"):
            results[cid] = (None, "skipped by --no-gitleaks")
    else:
        key = fake_key()
        repo, base = new_repo(work, "key-tree")
        commit(repo, {"settings.py": f'API_KEY = "{key}"\n'}, "add settings")
        rc, _out = gitleaks(repo, base, history=False)
        case("gitleaks-red-tree", rc == 1, f"exit {rc}")
        repo, base = new_repo(work, "key-history")
        commit(repo, {"settings.py": f'API_KEY = "{key}"\n'}, "add settings")
        commit(repo, {}, "remove settings", remove=["settings.py"])
        rc, _out = gitleaks(repo, base, history=True)
        case("gitleaks-red-history", rc == 1, f"exit {rc}")
        rc, _out = gitleaks(clean, cbase, history=True)
        case("gitleaks-green", rc == 0, f"exit {rc}")

    bad = 0
    for cid in listed:
        if cid not in results:
            print(f"  FAIL  {cid}: listed in cases.tsv, not implemented by this runner")
            bad += 1
    for cid, (ok, detail) in results.items():
        if cid not in listed:
            print(f"  FAIL  {cid}: implemented here, not listed in cases.tsv")
            bad += 1
        elif ok is None:
            print(f"  SKIP  {cid}: {detail}")
        elif ok:
            print(f"  ok    {cid}: {detail}")
        else:
            print(f"  FAIL  {cid}: {detail}")
            bad += 1
    mode = "synthetic vocabulary" if a.synthetic else "private vocabulary"
    print(f"[negative-control] {len(results)} case(s), {bad} failure(s), {len(names)} rule(s) ({mode}); scratch: {work}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
