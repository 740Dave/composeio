"""The merge queue's re-read of the PR title and body (merge-queue.yml, job `tested`, step `text`; tools/queue_leakcheck.py
--print-digest).

The defect these pin (run 45867021900, PR #7): the re-read ran with no token, the hosted runner's unauthenticated API read
failed, and the failure was reported as `same=false`, so the posted `merge-queue/tested` said the text CHANGED when it could
not be READ. The requirement (REQ-REREAD, shared leak-workflow spec): an unreadable re-read is reported as "unreadable",
apart from "changed".

The workflow tests run the step's own `run:` bytes from the YAML under `bash -e`, which is what Actions runs a step with no
`shell:` key under (`bash -e {0}`), with `python3` and `gh` replaced by stubs on PATH, so what is tested is what GitHub
runs, not a copy of it.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import urllib.error

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "merge-queue.yml"
TOOL = REPO / "tools" / "queue_leakcheck.py"
SCANNED = "a" * 64
OTHER = "b" * 64
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash is not available")


def _load_tool():
    spec = importlib.util.spec_from_file_location("_queue_leakcheck_under_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tested_job():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["tested"]


def _step(step_id=None, name_prefix=None):
    for s in _tested_job()["steps"]:
        if step_id and s.get("id") == step_id:
            return s
        if name_prefix and (s.get("name") or "").startswith(name_prefix):
            return s
    raise AssertionError(f"no step {step_id or name_prefix!r} in job `tested`")


def _run_step(step, tmp_path, env_overrides, stubs):
    """Run a step's `run:` script as Actions does with no `shell:` key (`bash -e {0}`), stubs first on PATH.
    -> (rc, outputs dict, combined stdout and stderr)."""
    assert "shell" not in step, "the step sets shell:; run it under that shell instead of bash -e"
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, body in stubs.items():
        p = bindir / name
        p.write_text("#!/bin/sh\n" + body, encoding="utf-8")
        p.chmod(0o755)
    script = tmp_path / "step.sh"
    script.write_text(step["run"], encoding="utf-8")
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    env = {"PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}", "GITHUB_OUTPUT": str(out), "HOME": str(tmp_path)}
    env.update(env_overrides)
    r = subprocess.run([BASH, "-e", str(script)], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines() if "=" in line)
    return r.returncode, outputs, r.stdout + r.stderr


def _reread(tmp_path, python3_stub, scanned=SCANNED):
    return _run_step(_step(step_id="text"), tmp_path,
                     {"NUMBER": "7", "REPO": "owner/name", "SCANNED": scanned}, {"python3": python3_stub})


# ---- the workflow's re-read step: four answers, never two --------------------------------------------------------------

@needs_bash
def test_unreadable_reread_is_unreadable_not_changed(tmp_path):
    rc, outputs, log = _reread(tmp_path, "echo '[queue-leakcheck] COULD NOT RUN' >&2; exit 3\n")
    assert rc == 0, log  # the step must not fail, or the status post is skipped
    assert outputs.get("same") == "unreadable", log


@needs_bash
def test_garbage_output_with_exit_zero_is_unreadable(tmp_path):
    rc, outputs, log = _reread(tmp_path, "echo 'not a digest'; exit 0\n")
    assert rc == 0, log
    assert outputs.get("same") == "unreadable", log


@needs_bash
def test_digest_plus_extra_line_with_exit_zero_is_unreadable(tmp_path):
    rc, outputs, log = _reread(tmp_path, f"echo {SCANNED}; echo extra; exit 0\n")
    assert rc == 0, log
    assert outputs.get("same") == "unreadable", log


@needs_bash
@pytest.mark.parametrize("scanned", [" ", "not a digest", SCANNED.upper(), SCANNED + "\nextra", SCANNED[:63]])
def test_malformed_scanned_digest_is_unreadable_not_changed(tmp_path, scanned):
    rc, outputs, log = _reread(tmp_path, f"echo {SCANNED}\n", scanned=scanned)
    assert rc == 0, log
    assert outputs.get("same") == "unreadable", log


@needs_bash
def test_changed_text_is_changed(tmp_path):
    rc, outputs, log = _reread(tmp_path, f"echo {OTHER}\n")
    assert rc == 0, log
    assert outputs.get("same") == "false", log


@needs_bash
def test_same_text_is_true(tmp_path):
    rc, outputs, log = _reread(tmp_path, f"echo {SCANNED}\n")
    assert rc == 0, log
    assert outputs.get("same") == "true", log


@needs_bash
def test_no_digest_from_leakcheck_is_not_scanned(tmp_path):
    rc, outputs, log = _reread(tmp_path, "echo SHOULD-NOT-RUN; exit 9\n", scanned="")
    assert rc == 0, log
    assert outputs.get("same") == "not-scanned", log


def test_reread_step_carries_the_token_and_the_job_stays_read_only():
    step = _step(step_id="text")
    assert step.get("env", {}).get("GH_TOKEN") == "${{ secrets.GITHUB_TOKEN }}"
    perms = _tested_job()["permissions"]
    assert perms.get("contents") == "read"
    assert perms.get("pull-requests") == "read"  # GET /repos/{repo}/pulls/{n} is the pull-requests permission's endpoint
    assert {k for k, v in perms.items() if v == "write"} == {"statuses"}


def test_leakcheck_job_references_no_token():
    job = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["leakcheck"]
    text = yaml.safe_dump(job)
    for needle in ("GH_TOKEN", "GITHUB_TOKEN", "secrets.", "github.token"):
        assert needle not in text, f"the self-hosted leakcheck job references {needle!r}"


# ---- the status post: its description names which answer -----------------------------------------------------------------

def _post(tmp_path, text_same, suite="success", leak="success"):
    stub = f'printf "%s\\n" "$@" > "{tmp_path}/gh_args"\n'
    rc, _, log = _run_step(_step(name_prefix="post merge-queue/tested"), tmp_path,
                           {"GH_TOKEN": "x", "REPO": "owner/name", "HEAD_SHA": "c" * 40, "SUITE": suite, "LEAK": leak,
                            "TEXT_SAME": text_same, "RUN_URL": "https://example.invalid/run"}, {"gh": stub})
    args = (tmp_path / "gh_args").read_text(encoding="utf-8").splitlines()
    fields = dict(a.split("=", 1) for a in args if "=" in a and a.split("=", 1)[0] in ("state", "description"))
    return rc, fields, log


@needs_bash
@pytest.mark.parametrize("text_same,must_say,must_not_say", [
    ("unreadable", "unreadable", "changed after"),
    ("false", "changed after the scan", "unreadable"),
    ("not-scanned", "not scanned", "changed after"),
])
def test_post_names_the_answer_and_fails(tmp_path, text_same, must_say, must_not_say):
    rc, fields, log = _post(tmp_path, text_same)
    assert fields["state"] == "failure", log
    assert rc != 0, log
    assert must_say in fields["description"], fields
    assert must_not_say not in fields["description"], fields
    assert len(fields["description"]) <= 140, fields  # GitHub's status description limit


@needs_bash
def test_post_success_only_when_text_unchanged(tmp_path):
    rc, fields, log = _post(tmp_path, "true")
    assert fields["state"] == "success", log
    assert "PR text: unchanged" in fields["description"], fields
    assert rc == 0, log


# ---- the tool: a read failure is a distinct exit, and nothing but a digest is ever printed -----------------------------

def test_print_digest_read_failure_exits_3_and_prints_nothing(tmp_path, capsys):
    tool = _load_tool()
    rc = tool.main(["--print-digest", "--pr-text", str(tmp_path / "missing.txt")])
    out = capsys.readouterr().out
    assert rc == tool.EXIT_UNREADABLE == 3
    assert out == ""


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_print_digest_api_failure_exits_3(monkeypatch, capsys):
    tool = _load_tool()

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "rate limited", {}, None)
    monkeypatch.setattr(tool.urllib.request, "urlopen", boom)
    assert tool.main(["--print-digest", "--pr", "7", "--repo", "owner/name"]) == 3
    assert capsys.readouterr().out == ""


def test_print_digest_malformed_reply_exits_3_without_printing_text(monkeypatch, capsys):
    tool = _load_tool()
    monkeypatch.setattr(tool.urllib.request, "urlopen", lambda req, timeout=None: _Resp(b'["SECRET-PR-BODY"]'))
    assert tool.main(["--print-digest", "--pr", "7", "--repo", "owner/name"]) == 3
    cap = capsys.readouterr()
    assert "SECRET-PR-BODY" not in cap.out + cap.err


def test_leakcheck_path_read_sends_no_token_even_with_gh_token_set(monkeypatch):
    tool = _load_tool()
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["unredirected"] = dict(req.unredirected_hdrs)
        return _Resp(json.dumps({"title": "T", "body": "B"}).encode())
    monkeypatch.setattr(tool.urllib.request, "urlopen", fake)
    monkeypatch.setenv("GH_TOKEN", "tok")
    assert tool.read_pr_text(None, "owner/name", "7") == "T\n\nB\n"
    assert seen["auth"] is None
    assert not any(k.lower() == "authorization" for k in seen["unredirected"])


def test_print_digest_sends_gh_token_and_prints_only_the_digest(monkeypatch, capsys):
    tool = _load_tool()
    seen = {}

    def fake(req, timeout=None):
        seen["auth"] = req.get_header("Authorization")
        seen["redirected_headers"] = dict(req.headers)
        return _Resp(json.dumps({"title": "T", "body": "SECRET-PR-BODY"}).encode())
    monkeypatch.setattr(tool.urllib.request, "urlopen", fake)
    monkeypatch.setenv("GH_TOKEN", "tok")
    assert tool.main(["--print-digest", "--pr", "7", "--repo", "owner/name"]) == 0
    out = capsys.readouterr().out
    assert seen["auth"] == "Bearer tok"
    # unredirected: urllib copies req.headers onto a redirected request, never req.unredirected_hdrs
    assert not any(k.lower() == "authorization" for k in seen["redirected_headers"])
    assert out.strip() == tool.digest("T\n\nSECRET-PR-BODY\n")
    assert "SECRET-PR-BODY" not in out


def test_reread_and_leakcheck_reads_make_only_get_requests(monkeypatch):
    """Both reads of the PR text (--print-digest, and read_pr_text for the leakcheck job) are GET with no body: neither
    path can write to the API, whatever token it holds."""
    tool = _load_tool()
    reqs = []

    def fake(req, timeout=None):
        reqs.append(req)
        return _Resp(json.dumps({"title": "T", "body": "B"}).encode())
    monkeypatch.setattr(tool.urllib.request, "urlopen", fake)
    monkeypatch.setenv("GH_TOKEN", "tok")
    assert tool.main(["--print-digest", "--pr", "7", "--repo", "owner/name"]) == 0
    tool.read_pr_text(None, "owner/name", "7")
    assert len(reqs) == 2
    for req in reqs:
        assert req.get_method() == "GET", req.get_method()
        assert req.data is None
