#!/usr/bin/env python3
"""project_validator — a DETERMINISTIC (no model) validator of David's projects
against the required ComposeIO/ComposePlatform project structure
([DEC-PROJECT-STRUCTURE], `decisions/decision-graph-composeux-architect.lts`).

WHY DETERMINISTIC (mirrors `permission_audit.py` / `memory_monitor.py`): "does
this project have a decision file, a git remote, a specs dir" is mechanical
filesystem inspection + a couple of read-only `git` queries — it must NEVER
burn a reasoning model. This tool does the whole check for ZERO model tokens;
the Projects tab renders the flags, David/the architect make the judgment
calls (fix the gap? it's a legitimate WIP project?).

WHAT "REQUIRED STRUCTURE" MEANS ([DEC-PROJECT-STRUCTURE], quoted):

  REQUIRED to register: a root dir + a `projects.lts` record
    {name, slug, TYPE, root, status, SUMMARY, git}.

  STANDARD (flagged-if-absent, NEVER an error — a new project legitimately
  has gaps): README.md - decisions/decision-graph-<slug>.lts (flag "no
  decision-LTS yet"; point-forward per D2) - an artifact ledger
  (shared/filtered-by-root per D3, or local deliverables/) - a specs/ dir -
  .claude/ (scope-B) - a git remote.

  TYPE-SPECIFIC: ComposeApp -> >=1 *.compose.yaml + a launch entry;
  ComposeBus Service -> a service module + >=1 declared bus namespace;
  Reactive Widget -> a *.compose.yaml widget def + a board/deploy descriptor
  (docs/board-bringup/* or a deploy target).

  git indicator (3 states): own-repo (a git remote) - monorepo (a dir inside
  a shared repo, e.g. Visual Composer inside 740Dave/composeio) - no-git.
  Computed deterministically (`git -C <root> remote get-url origin` +
  walking up for an enclosing repo).

"Missing" items are FLAGS, not errors — `compliant` is a convenience bool
(true only when nothing is missing) for the Projects-tab badge; the STANDARD
items being absent is expected/benign for a young project (David's own
language: "never an error").

THE REGISTRY (source of truth): `decisions/projects.lts`, per
`[DEC-CONFIG-PROJECTS-CALLS-SETTLED]` D1 ("the project registry lives in
`decisions/projects.lts` (repo/FactLattice, linkable)") and
`docs/intelligence-console/DESIGN-configuration-and-projects.md` §3. A
deliberately restricted, self-parseable FactLattice subset — the SAME
`### [ID] title` + flat `- key: value` node shape
`deliverable_sweep/ledger.py` uses for the deliverable ledger, not a general
FactLattice parser. If the file does not exist, this tool SEEDS a minimal one
from the known projects (this app / Visual Composer / Headless Monitor /
provisionIO / the FactLattice parser / bus_storage / document_store / the
Dradis widget) with **best-guess paths FLAGGED for David to confirm** — the
membership and paths are his to decide, this tool only seeds a starting
point. An EXISTING registry is NEVER overwritten.

Pointer-field resolution (decision_lts / artifact_lts / specs_dir /
settings), since projects nest inside a monorepo OR live in their own repo:
  - decision_lts, artifact_lts resolve relative to the POINTER BASE — the
    repo root for a project nested inside it (matches the shared-ledger /
    point-forward convention, D2/D3), or the project's own root for a
    standalone/own-repo project (e.g. provisionIO).
  - specs_dir, settings resolve relative to the PROJECT'S OWN ROOT (the
    design doc literally writes these as `<root>/...`), and MAY use `..`
    to reach a shared dir outside the project's own subtree.
An absolute value in any pointer field is used as-is.

Publishing: `self/projects` (scope global) -> topic `composeio/global/self/projects`,
the same `self/*` mapping `self_publisher`/`meta_publisher`/`memory_monitor` use.
Payload: {"projects": [...], "ts": <iso8601 utc>}.

Run:   python3 project_validator.py --mqtt mqtt://mqtt-broker.local:1883 [--interval 60]
       python3 project_validator.py --once      # compute + print; publish if broker reachable
       python3 project_validator.py --selftest  # compute-logic only, no broker, no network, no
                                                  # writes to the real registry (temp dirs only)
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# repo-root import shim (sibling daemons do the same: compose_projects/intelligence/tools/X.py -> <root>/python)
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "python"))

NS = "self/projects"

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[3]
# The project registry (projects.lts) now lives in the intelligence-console
# decision corpus; resolve its repo-relative location from bootstrap_paths.
from composeio.bootstrap_paths import INTELLIGENCE_CONSOLE_CORPUS_REL as _IC_CORPUS_REL
DEFAULT_REGISTRY_PATH = _IC_CORPUS_REL + "projects.lts"
DEFAULT_INTERVAL_S = 60.0   # project structure changes slowly; no need to poll fast

# directories a bounded structure-walk never descends into (mirrors
# deliverable_sweep's do-not-track-dirs — the repo's existing convention for
# "never walk this")
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pio", ".pytest_cache", ".mypy_cache",
    ".venv", "venv", "env", "build", "dist", ".eggs", "site", ".ray", "_generated",
}

_LAUNCH_ENTRY_NAMES = {"__main__.py", "launcher.compose.yaml", "app.py", "shell.py", "main.py"}
_NAMESPACE_RE = re.compile(r'\bnamespace[s]?\s*[:=]', re.IGNORECASE)


# ---------------------------------------------------------------------------
# the seed — best-guess projects, FLAGGED for David to confirm (never used if
# decisions/projects.lts already exists)
# ---------------------------------------------------------------------------
def seed_registry_projects() -> List[Dict[str, Any]]:
    """The known-projects list from the task brief, as best-guess registry
    records. `seed_confidence` is NOT part of [DEC-PROJECT-STRUCTURE]'s
    schema — an honest extra field this tool adds so the seed visibly marks
    which entries are solid (a real, inspected directory) vs. a guess vs.
    genuinely unlocated (no directory found at all)."""
    return [
        {
            "slug": "intelligence-console", "name": "Intelligence Console",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": ".", "status": "active", "parent": "",
            "decision_lts": "corpora/intelligence-console/decision-graph-composeux-architect.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "corpora/platform-specs",
            "settings": ".claude",
            "summary": ("The Intelligence Console + ComposePlatform-architect build "
                        "project itself (console scene, daemons, publishers, tools). "
                        "Root = the composeio repo root."),
            "seed_confidence": "confirmed",
        },
        {
            "slug": "visual-composer", "name": "Visual Composer",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": "python/composeio/composer", "status": "active", "parent": "",
            "decision_lts": "corpora/visual-composer/decision-graph-visual-composer.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "../../../corpora/platform-specs",
            "settings": ".claude",
            "summary": ("The M4 Composer — a RenderApp on the Skia render engine, so "
                        "the same engine that renders deployed widgets also runs the "
                        "tool that builds them (the dogfooding gate). Root repointed "
                        "2026-08-08: it was `ux_composer/src/composer`, the CTk-shell "
                        "ComposerApp this SUPERSEDED, which David retired and which is "
                        "now DELETED — a registry entry pointing at it would have made "
                        "the Projects tab validate a root that no longer exists."),
            "seed_confidence": "confirmed",
        },
        {
            "slug": "headless-monitor", "name": "Headless Monitor",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": "apps/headless-monitor", "status": "active", "parent": "",
            "decision_lts": "corpora/headless-monitor/decision-graph-headless-monitor.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "specs",
            "settings": ".claude",
            "summary": ("UNLOCATED: only a spec (corpora/platform-specs/28-composeux-headless-"
                        "monitor.lts) and a previs (corpora/platform-specs/ux/headless-monitor-"
                        "previs.v1.html) were found — no standalone implementation "
                        "directory exists under this repo yet. `root` is a PLACEHOLDER; "
                        "David please confirm/supply the real path (or that it doesn't "
                        "exist yet as a project)."),
            "seed_confidence": "unlocated",
        },
        {
            "slug": "provisionio", "name": "provisionIO",
            "family": "ComposeApps", "type": "ComposeApp",
            "root": os.path.join(os.environ.get("MAKER_HOME", os.path.expanduser("~/maker")), "provisionIO"),
            "status": "active", "parent": "",
            "decision_lts": "corpora/provisionio/decision-graph-provisionio.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "specs",
            "settings": ".claude",
            "summary": ("Fleet-provisioning package, own GitHub repo (740Dave/"
                        "provisionIO), lives OUTSIDE the composeio tree at "
                        "${MAKER_HOME}/provisionIO."),
            "seed_confidence": "confirmed",
        },
        {
            "slug": "factlattice-parser", "name": "LTS / FactLattice Parser",
            "family": "ComposeBus Services", "type": "ComposeBus Service",
            "root": "docs/factlattice-linter", "status": "active", "parent": "",
            "decision_lts": "corpora/factlattice-parser/decision-graph-factlattice-parser.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "specs",
            "settings": ".claude",
            "summary": ("UNCERTAIN: no standalone parser/linter implementation directory "
                        "was found; `docs/factlattice-linter` holds only "
                        "PARSER-LINTER-ALIGNMENT.md. This may actually live in MAGMA "
                        "(a Haven service, external to this repo) or be un-extracted "
                        "from `python/composeio/decision_extractor` / `session_sweep`. "
                        "`root` is a PLACEHOLDER; David please confirm."),
            "seed_confidence": "unlocated",
        },
        {
            "slug": "bus-storage", "name": "bus_storage",
            "family": "ComposeBus Services", "type": "ComposeBus Service",
            "root": "services/bus_storage", "status": "active", "parent": "",
            "decision_lts": "corpora/bus-storage/decision-graph-bus-storage.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "specs",
            "settings": ".claude",
            "summary": ("The bus:// asset-storage service (server.py), hosted on "
                        "mqtt-broker.local:8420."),
            "seed_confidence": "confirmed",
        },
        # document_store RETIRED ([SPEC:CXC-STORE]) — consolidated into bus_storage (the ONE
        # store). The doc catalog is now bus:// path-addressed blobs; no separate project.
        {
            "slug": "dradis-widget", "name": "BSG DRADIS Widget",
            "family": "Reactive Widgets", "type": "Reactive Widget",
            "root": "cyd", "status": "active", "parent": "",
            "decision_lts": "corpora/dradis-widget/decision-graph-dradis-widget.lts",
            "artifact_lts": "corpora/deliverables/deliverable-ledger.lts",
            "specs_dir": "spec",
            "settings": ".claude",
            "summary": ("BEST-GUESS root: the DRADIS Contacts widget "
                        "(cyd/configs/cyd_dradis.compose.yaml, src/dradis_contacts.cpp) "
                        "deployed to the Qualia display (docs/board-bringup/qualia.md) "
                        "lives INSIDE the shared `cyd` firmware project, which hosts "
                        "several other widgets too — root points at the whole cyd tree, "
                        "not a Dradis-only subdir. Confirm whether a narrower root, or "
                        "the flat-with-parent-reference subproject shape, is wanted."),
            "seed_confidence": "best-guess",
        },
    ]


# ---------------------------------------------------------------------------
# registry I/O — the restricted FactLattice-subset parser/writer
# ---------------------------------------------------------------------------
# Accept BOTH id forms: the FactLattice-SPEC-compliant house style
# `[PROJECT-UPPERCASE-SLUG]` (node ids MUST be [A-Z][A-Z0-9_-]+, so the registry
# was migrated hyphen+uppercase in 80dc990 to become lts_find-queryable) AND the
# legacy `[PROJECT:lowercase-slug]` this generator historically emitted. The slug
# is normalized to lowercase-kebab in either case, so downstream (self/projects,
# the Projects widget's slug row-zones) is convention-stable. Closes the
# "generator still emits the old form -> code follow-up" flagged in 80dc990.
_NODE_RE = re.compile(r'^### \[PROJECT[:-]([A-Za-z0-9_-]+)\]\s*(.*)$')
_FIELD_RE = re.compile(r'^-\s+([A-Za-z0-9_-]+):\s?(.*)$')


def parse_registry(path: Union[str, Path]) -> Optional[List[Dict[str, Any]]]:
    """Read `decisions/projects.lts` -> a list of project dicts. Returns None
    if the file does not exist (the caller decides whether to seed). Never
    writes anything — purely a reader."""
    p = Path(path)
    if not p.is_file():
        return None
    projects: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    for line in p.read_text(encoding="utf-8").splitlines():
        m = _NODE_RE.match(line)
        if m:
            if cur is not None:
                projects.append(cur)
            cur = {"slug": m.group(1).lower(), "name": m.group(2).strip()}
            continue
        if cur is None:
            continue
        fm = _FIELD_RE.match(line)
        if fm:
            key = fm.group(1).replace("-", "_")
            val = fm.group(2).strip()
            cur[key] = val if val else ""
    if cur is not None:
        projects.append(cur)
    return projects


_REGISTRY_FIELD_ORDER = [
    "family", "type", "root", "status", "parent",
    "decision_lts", "artifact_lts", "specs_dir", "settings",
    "summary", "seed_confidence",
    # ADDITIVE maker-project fields ([FLOW:MAKER-PROJECT] S1): carried by a maker
    # project instance so it flows a->f visibly.  Rendered only when present, so
    # non-maker projects are byte-identical.  `baseline_stack_rev`/`current_step`
    # are the task's `baseline-stack-rev`/`current-step` (the parser maps '-'->'_').
    "mcu", "baseline_stack_rev", "widget", "slots", "current_step",
]


def render_registry_lts(projects: List[Dict[str, Any]], seeded: bool = True) -> str:
    """Render project records into the restricted FactLattice-subset shape
    (mirrors `deliverable-ledger.lts`'s `### [ID] title` + flat `- key:
    value` nodes)."""
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        "---",
        "machine-maintained: false",
        "domain: PLT.CUX",
        "confidentiality: internal-only",
        "artifact-readiness: seed" if seeded else "artifact-readiness: reviewed",
        f"created: {today}",
        "---",
        "# Project Registry [REGISTRY:PROJECTS]",
        "",
    ]
    if seeded:
        lines += [
            "> SEEDED by `compose_projects/intelligence/tools/project_validator.py` from the known-projects "
            "list in [DEC-PROJECT-STRUCTURE] / [DEC-CONFIG-PROJECTS-CALLS-SETTLED] D1. "
            "**FLAGGED for David**: the paths + membership below are the tool's BEST GUESS "
            "and are UNCONFIRMED except where `seed_confidence: confirmed`. Review and "
            "correct (edit this file directly, or hand corrections to the architect), "
            "then the `seed_confidence` annotations can be dropped. Point-forward per D2 — "
            "this file only REGISTERS projects; each project's own decision-LTS / specs / "
            "artifact ledger stay wherever they already live (or get created as the "
            "project matures).",
            "",
        ]
    lines += ["---", "", "## Projects", ""]
    for p in projects:
        # FactLattice-SPEC-compliant node id: [PROJECT-UPPERCASE-SLUG] (matches
        # the migrated house style, lts_find-queryable). The parser lowercases
        # the slug back on read, so the round-trip is convention-stable.
        slug = str(p.get('slug', 'unknown'))
        lines.append(f"### [PROJECT-{slug.upper()}] {p.get('name', slug)}")
        for key in _REGISTRY_FIELD_ORDER:
            if key in p:
                lines.append(f"- {key}: {p[key]}")
        # any extra/unknown fields, schema-tolerant pass-through (sorted, deterministic)
        for key in sorted(p):
            if key in ("slug", "name") or key in _REGISTRY_FIELD_ORDER:
                continue
            lines.append(f"- {key}: {p[key]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_seed_registry_if_absent(path: Union[str, Path],
                                   projects: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Write the seed registry ONLY if `path` does not already exist. Returns
    True if it wrote (seeded), False if a registry was already present (left
    untouched — "do not overwrite an existing registry", per the brief).
    Atomic write (temp file + os.replace), matching the ledger's convention."""
    p = Path(path)
    if p.is_file():
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    text = render_registry_lts(projects if projects is not None else seed_registry_projects())
    fd, tmp = None, None
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".projects-", suffix=".lts.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return True


def load_or_seed_registry(path: Union[str, Path]) -> Tuple[List[Dict[str, Any]], bool]:
    """The orchestration helper (has the one legitimate side effect: seeding
    a missing file). `validate()` itself stays pure/read-only. Returns
    (projects, was_seeded)."""
    existing = parse_registry(path)
    if existing is not None:
        return existing, False
    write_seed_registry_if_absent(path)
    seeded = parse_registry(path)
    return (seeded or []), True


# ---------------------------------------------------------------------------
# git indicator — read-only, deterministic (own-repo / monorepo / no-git)
# ---------------------------------------------------------------------------
def _git_remote_url(path: Path) -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            url = out.stdout.strip()
            return url or None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def git_indicator(root: Path) -> Dict[str, Any]:
    """3-state git indicator, computed READ-ONLY:
      own-repo  -- `root` itself is a git worktree (has `.git`), remote read.
      monorepo  -- `root` is a plain dir inside an ENCLOSING git repo (walk
                   up); the enclosing repo's remote is reported.
      no-git    -- neither (or `root` does not exist)."""
    if not root.exists():
        return {"state": "no-git", "remote": None}
    if (root / ".git").exists():
        return {"state": "own-repo", "remote": _git_remote_url(root)}
    cur = root.resolve().parent
    for _ in range(64):
        if (cur / ".git").exists():
            return {"state": "monorepo", "remote": _git_remote_url(cur),
                    "monorepo_root": str(cur)}
        if cur.parent == cur:
            break
        cur = cur.parent
    return {"state": "no-git", "remote": None}


# ---------------------------------------------------------------------------
# bounded filesystem walk (never descends into build/vendor/vcs dirs)
# ---------------------------------------------------------------------------
def _walk_bounded(root: Path, max_depth: int):
    root = root.resolve()
    base_depth = len(root.parts)
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).parts) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        yield dirpath, dirnames, filenames


def _find_files(root: Path, pattern: str, max_depth: int = 4) -> List[Path]:
    if not root.is_dir():
        return []
    found = []
    for dirpath, _dirnames, filenames in _walk_bounded(root, max_depth):
        for fn in filenames:
            if fnmatch.fnmatch(fn, pattern):
                found.append(Path(dirpath) / fn)
    return found


def _find_any_named(root: Path, names: set, max_depth: int = 4) -> bool:
    if not root.is_dir():
        return False
    for _dirpath, _dirnames, filenames in _walk_bounded(root, max_depth):
        if names & set(filenames):
            return True
    return False


def _has_top_level_py(root: Path) -> bool:
    if not root.is_dir():
        return False
    try:
        return any(f.endswith(".py") for f in os.listdir(root)
                   if (root / f).is_file())
    except OSError:
        return False


def _grep_for_namespace(root: Path, max_depth: int = 2) -> bool:
    if not root.is_dir():
        return False
    for dirpath, _dirnames, filenames in _walk_bounded(root, max_depth):
        for fn in filenames:
            if fn.endswith((".py", ".yaml", ".yml")):
                fp = Path(dirpath) / fn
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if _NAMESPACE_RE.search(text):
                    return True
    return False


# ---------------------------------------------------------------------------
# the structure checks
# ---------------------------------------------------------------------------
def _pointer_base(root: Path, repo_root: Path) -> Path:
    """decision_lts / artifact_lts resolve relative to this base: repo_root
    for a project nested inside it (the shared-ledger / point-forward
    convention), or the project's own root for a standalone/own-repo
    project."""
    try:
        root.resolve().relative_to(repo_root.resolve())
        return repo_root
    except ValueError:
        return root


def resolve_pointers(p: Dict[str, Any], root: Path, repo_root: Path) -> Dict[str, Any]:
    base = _pointer_base(root, repo_root)

    def _resolve(value: str, relative_to: Path) -> Path:
        vp = Path(value)
        return vp if vp.is_absolute() else (relative_to / vp)

    decision_lts_rel = p.get("decision_lts") or f"corpora/{p.get('slug', 'project')}/decision-graph-{p.get('slug', 'project')}.lts"
    artifact_lts_rel = p.get("artifact_lts") or "corpora/deliverables/deliverable-ledger.lts"
    specs_dir_rel = p.get("specs_dir") or "specs"
    settings_rel = p.get("settings") or ".claude"

    return {
        "decision_lts_path": _resolve(decision_lts_rel, base),
        "decision_lts_rel": decision_lts_rel,
        "artifact_lts_path": _resolve(artifact_lts_rel, base),
        "artifact_lts_rel": artifact_lts_rel,
        "specs_dir_path": _resolve(specs_dir_rel, root),
        "specs_dir_rel": specs_dir_rel,
        "settings_path": _resolve(settings_rel, root),
        "settings_rel": settings_rel,
    }


def _check_standard(root: Path, resolved: Dict[str, Any]) -> Tuple[List[str], bool]:
    missing: List[str] = []
    if not (root / "README.md").is_file():
        missing.append("README.md")

    has_decision = resolved["decision_lts_path"].is_file()
    if not has_decision:
        missing.append(f"decision-LTS: {resolved['decision_lts_rel']} (no decision-LTS yet)")

    has_ledger = resolved["artifact_lts_path"].is_file() or (root / "deliverables").is_dir()
    if not has_ledger:
        missing.append("artifact ledger (shared ledger not found, no local deliverables/ either)")

    if not resolved["specs_dir_path"].is_dir():
        missing.append(f"specs/ dir ({resolved['specs_dir_rel']})")

    if not resolved["settings_path"].is_dir():
        missing.append(".claude/ (scope-B)")

    return missing, has_decision


def _check_type_specific(ptype: str, root: Path) -> List[str]:
    missing: List[str] = []
    compose_files = _find_files(root, "*.compose.yaml", max_depth=4)

    if ptype == "ComposeApp":
        if not compose_files:
            missing.append("ComposeApp: no *.compose.yaml found")
        if not _find_any_named(root, _LAUNCH_ENTRY_NAMES, max_depth=4):
            missing.append("ComposeApp: no launch entry found "
                           "(__main__.py / app.py / shell.py / launcher.compose.yaml)")
    elif ptype == "ComposeBus Service":
        if not _has_top_level_py(root):
            missing.append("ComposeBus Service: no top-level service module (*.py) found")
        if not _grep_for_namespace(root):
            missing.append("ComposeBus Service: no declared bus namespace found")
    elif ptype == "Reactive Widget":
        if not compose_files:
            missing.append("Reactive Widget: no *.compose.yaml widget def found")
        has_deploy = (root / "platformio.ini").is_file() or (root / "deploy").is_dir()
        if not has_deploy:
            missing.append("Reactive Widget: no board/deploy descriptor found "
                           "(platformio.ini or deploy/)")
    else:
        missing.append(f"unknown project type '{ptype}' — no type-specific check defined")
    return missing


_REQUIRED_REGISTRY_FIELDS = ("name", "slug", "type", "root", "status")


def _resolve_root(raw_root: str, repo_root: Path) -> Path:
    rp = Path(raw_root or ".")
    if rp.is_absolute():
        return rp
    return (repo_root / rp).resolve()


def _validate_one(p: Dict[str, Any], repo_root: Path) -> Dict[str, Any]:
    slug = p.get("slug", "")
    name = p.get("name", slug)
    ptype = p.get("type", "")
    family = p.get("family", "")
    status = p.get("status", "")
    parent = p.get("parent") or None
    seed_confidence = p.get("seed_confidence") or None
    raw_root = p.get("root", ".")
    root = _resolve_root(raw_root, repo_root)

    reg_missing = [f"registry:{f} (required field absent from the projects.lts record)"
                   for f in _REQUIRED_REGISTRY_FIELDS if not p.get(f)]

    base = {
        "slug": slug, "name": name, "family": family, "type": ptype,
        "root": str(root), "status": status, "parent": parent,
        "seed_confidence": seed_confidence,
    }

    if not root.is_dir():
        base.update({
            "git": {"state": "no-git", "remote": None},
            "has_git": False,
            "has_decision_file": False,
            "decision_lts": None,
            "missing": [f"root path not found: {root}"] + reg_missing,
            "compliant": False,
            "root_exists": False,
        })
        return base

    resolved = resolve_pointers(p, root, repo_root)
    std_missing, has_decision = _check_standard(root, resolved)
    type_missing = _check_type_specific(ptype, root)
    git = git_indicator(root)
    missing = reg_missing + std_missing + type_missing

    base.update({
        "git": git,
        "has_git": git["state"] != "no-git",
        "has_decision_file": has_decision,
        "decision_lts": str(resolved["decision_lts_path"]),
        "missing": missing,
        "compliant": len(missing) == 0,
        "root_exists": True,
    })
    return base


def validate(projects: List[Dict[str, Any]],
             repo_root: Union[str, Path] = REPO_ROOT_DEFAULT) -> Dict[str, Any]:
    """The pure library entry point — importable, NO side effects (no writes;
    the only I/O is read-only: `os.stat`/`os.walk`/file reads under each
    project's own root, plus a read-only `git remote get-url` subprocess).
    A path that doesn't exist is reported as a validation finding, never a
    crash. Returns {"projects": [...], "ts": <iso8601 utc>}."""
    repo_root = Path(repo_root).resolve()
    results = [_validate_one(p, repo_root) for p in projects]
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {"projects": results, "ts": ts}


# ---------------------------------------------------------------------------
# publish (bus-native, self/projects — mirrors memory_monitor.py's transport)
# ---------------------------------------------------------------------------
def _parse_mqtt_url(url: str) -> Tuple[str, int]:
    s = url
    for scheme in ("mqtt://", "tcp://", "mqtts://"):
        if s.startswith(scheme):
            s = s[len(scheme):]
            break
    host, _, port = s.partition(":")
    return host or "localhost", int(port) if port else 1883


# [SPEC:CXC-SOURCES-ON-BUS] slice 3 — the bus seam. This module used to build an
# `MQTTTransport` itself. That touched no paho and was STILL a substrate name: a
# module that imports ONE transport class is exactly as dark on a CAN-only
# device as one that hand-rolls a topic string. It now states a NAMESPACE and a
# SCOPE and lets `transport_registry` choose.
from composeio import source_bus                              # noqa: E402
from composeio.namespace import Scope                         # noqa: E402

SRC = "project-validator"          # FROZEN wire field ([CXC-SOB-CONTRACT-FROZEN])
LABEL = "project-validator"


def _make_bus(mqtt_url: str, bus=None):
    """The bus this daemon publishes on ([CXC-SOB-HANDLE])."""
    return source_bus.acquire_bus(mqtt_url, label=LABEL, bus=bus)


def _publish(bus, payload: Dict[str, Any], seq: int) -> None:
    """`seq` accepted and IGNORED — the bus owns sequencing
    ([DD-SOB-SEQ-SEMANTICS]); the parameter stays for the `--once` call site."""
    bus.publish_state(NS, payload, source=SRC)


def run(registry_path: str, repo_root: Path, mqtt_url: str, interval: float,
        _loops: Optional[int] = None, *, bus=None, stop=None) -> None:
    """Poll loop: re-validate + publish every `interval` seconds. `_loops`
    bounds the loop for tests (None = forever).

    REPEAT DELIVERY ([CXC-SOB-SUBSCRIBE]): does not subscribe. A stable repo
    republishes an identical verdict; in-process subscribers dedup it, the wire
    does not.
    """
    # SINGLETON ([SPEC:CXC-IC-SINGLETON]): console-global daemon (the --interval
    # poll loop). --selftest never reaches run(); a second instance yields.
    try:
        from singleton import acquire_singleton
    except ImportError:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "publisher"))
        from singleton import acquire_singleton
    lock = acquire_singleton("project-validator")
    if lock is None:
        sys.stderr.write("[project_validator] another project-validator holds the singleton; yielding\n")
        return                         # the ONE legitimate early return
    b = _make_bus(mqtt_url, bus)
    source_bus.declare(b, NS, scope=Scope.GLOBAL, source=SRC,
                       label="project compliance")
    source_bus.manifest(SRC)
    projects, seeded = load_or_seed_registry(registry_path)
    if seeded:
        sys.stderr.write(f"[project_validator] SEEDED {registry_path} with "
                         f"{len(projects)} best-guess project(s) — flagged for David\n")
    sys.stderr.write(f"[project_validator] {len(projects)} project(s) from "
                     f"{registry_path} -> {NS} every {interval}s\n")
    sys.stderr.flush()

    def _tick() -> None:
        try:
            # re-read the registry each pass (David/the architect may edit it live)
            cur_projects = parse_registry(registry_path) or projects
            payload = validate(cur_projects, repo_root)
            _publish(b, payload, 0)
            n_ok = sum(1 for r in payload["projects"] if r["compliant"])
            sys.stderr.write(f"[project_validator] {n_ok}/{len(payload['projects'])} "
                             f"compliant\n")
            sys.stderr.flush()
        except Exception as exc:  # a bad read must never kill the daemon
            sys.stderr.write(f"[project_validator] read error (continuing): {exc!r}\n")
            sys.stderr.flush()

    if _loops is None:
        _tick()                        # publish NOW, then park — see slice 2
        source_bus.park(stop, poll_s=interval, on_tick=_tick)
    else:
        for _i in range(_loops):       # bounded, for tests only
            _tick()
            if _i + 1 < _loops:
                time.sleep(interval)


# ---------------------------------------------------------------------------
# selftest — COMPUTE LOGIC ONLY, no broker, no network, no touching the real
# registry ([CXC-DS] --selftest discipline, matching deliverable_watcher /
# memory_monitor)
# ---------------------------------------------------------------------------
def _selftest() -> int:
    import tempfile

    fails: List[str] = []

    def check(cond, msg):
        if not cond:
            fails.append(msg)

    with tempfile.TemporaryDirectory() as d:
        repo_root = Path(d)

        # shared repo-root plumbing every "monorepo" project below sits inside
        (repo_root / ".git").mkdir()
        (repo_root / "decisions").mkdir()
        # ratified layout: shared ledger lives in its own corpus dir
        (repo_root / "corpora" / "deliverables" / "deliverable-ledger.lts").parent.mkdir(parents=True)
        (repo_root / "corpora" / "deliverables" / "deliverable-ledger.lts").write_text("# ledger\n")

        # ---- (a) a FULLY COMPLIANT ComposeApp project ------------------------------
        p_ok = repo_root / "proj-ok"
        p_ok.mkdir()
        (p_ok / "README.md").write_text("hi\n")
        (p_ok / "specs").mkdir()
        (p_ok / ".claude").mkdir()
        (p_ok / "app.compose.yaml").write_text("x: 1\n")
        (p_ok / "app.py").write_text("# entry\n")
        # ratified layout: per-project decision graph lives in corpora/<slug>/
        (repo_root / "corpora" / "proj-ok").mkdir(parents=True)
        (repo_root / "corpora" / "proj-ok" / "decision-graph-proj-ok.lts").write_text("# decisions\n")

        reg_ok = {"slug": "proj-ok", "name": "Proj OK", "family": "ComposeApps",
                  "type": "ComposeApp", "root": "proj-ok", "status": "active"}
        res = validate([reg_ok], repo_root)["projects"][0]
        check(res["compliant"] is True, f"(a) fully-structured project should be compliant, "
              f"got missing={res['missing']}")
        check(res["has_decision_file"] is True, "(a) decision-LTS should be found")
        check(res["has_git"] is True and res["git"]["state"] == "monorepo",
              f"(a) nested-in-repo project should read as monorepo git state, got {res['git']}")
        check(res["root_exists"] is True, "(a) root_exists should be True")

        # ---- (b) a project with NO git anywhere (no repo_root .git either) --------
        with tempfile.TemporaryDirectory() as d2:
            lonely_root = Path(d2)
            p_nogit = lonely_root / "proj-nogit"
            p_nogit.mkdir()
            reg_nogit = {"slug": "proj-nogit", "name": "No Git", "family": "ComposeApps",
                        "type": "ComposeApp", "root": "proj-nogit", "status": "active"}
            res_nogit = validate([reg_nogit], lonely_root)["projects"][0]
            check(res_nogit["git"]["state"] == "no-git" and res_nogit["has_git"] is False,
                  f"(b) a dir with no enclosing .git anywhere should be no-git, got {res_nogit['git']}")

        # ---- (c) a project with NO decision file -> flagged, NOT compliant --------
        p_nodecision = repo_root / "proj-nodecision"
        p_nodecision.mkdir()
        (p_nodecision / "README.md").write_text("hi\n")
        (p_nodecision / "specs").mkdir()
        (p_nodecision / ".claude").mkdir()
        (p_nodecision / "app.compose.yaml").write_text("x: 1\n")
        (p_nodecision / "app.py").write_text("# entry\n")
        # deliberately NO decisions/decision-graph-proj-nodecision.lts
        reg_nodec = {"slug": "proj-nodecision", "name": "No Decision", "family": "ComposeApps",
                    "type": "ComposeApp", "root": "proj-nodecision", "status": "active"}
        res_nodec = validate([reg_nodec], repo_root)["projects"][0]
        check(res_nodec["has_decision_file"] is False,
              "(c) missing decision-LTS must be flagged has_decision_file=False")
        check(res_nodec["compliant"] is False,
              "(c) a project missing its decision-LTS must NOT be compliant")
        check(any("decision-LTS" in m for m in res_nodec["missing"]),
              f"(c) missing[] must name the decision-LTS gap, got {res_nodec['missing']}")

        # ---- (d) a project missing REQUIRED dirs/files (bare empty dir) -----------
        p_bare = repo_root / "proj-bare"
        p_bare.mkdir()
        reg_bare = {"slug": "proj-bare", "name": "Bare", "family": "ComposeApps",
                   "type": "ComposeApp", "root": "proj-bare", "status": "active"}
        res_bare = validate([reg_bare], repo_root)["projects"][0]
        check(res_bare["compliant"] is False, "(d) a bare dir must not be compliant")
        for expect in ("README.md",):
            check(any(expect in m for m in res_bare["missing"]),
                  f"(d) missing[] must flag {expect}, got {res_bare['missing']}")
        check(any("specs/" in m for m in res_bare["missing"]), "(d) must flag missing specs/ dir")
        check(any(".claude/" in m for m in res_bare["missing"]), "(d) must flag missing .claude/")
        check(any("compose.yaml" in m for m in res_bare["missing"]),
              "(d) ComposeApp type-specific: must flag missing *.compose.yaml")
        check(any("launch entry" in m for m in res_bare["missing"]),
              "(d) ComposeApp type-specific: must flag missing launch entry")

        # ---- (e) a ROOT PATH THAT DOES NOT EXIST -> a finding, never a crash ------
        reg_ghost = {"slug": "ghost", "name": "Ghost", "family": "ComposeApps",
                    "type": "ComposeApp", "root": "does/not/exist", "status": "active"}
        res_ghost = validate([reg_ghost], repo_root)["projects"][0]
        check(res_ghost["root_exists"] is False, "(e) a missing root must set root_exists=False")
        check(res_ghost["compliant"] is False, "(e) a missing root must not be compliant")
        check(any("root path not found" in m for m in res_ghost["missing"]),
              f"(e) missing[] must say the root wasn't found, got {res_ghost['missing']}")
        check(res_ghost["has_git"] is False and res_ghost["has_decision_file"] is False,
              "(e) a missing root degrades has_git/has_decision_file to False, never raises")

        # ---- (f) ComposeBus Service type-specific: module + declared namespace ----
        p_svc = repo_root / "proj-svc"
        p_svc.mkdir()
        (p_svc / "server.py").write_text(
            '# declares bus namespace: self/thing\nNS = "self/thing"\n')  # module + namespace
        (p_svc / "README.md").write_text("hi\n")
        (p_svc / "spec").mkdir()
        (p_svc / ".claude").mkdir()
        (repo_root / "corpora" / "proj-svc").mkdir(parents=True)
        (repo_root / "corpora" / "proj-svc" / "decision-graph-proj-svc.lts").write_text("# d\n")
        reg_svc = {"slug": "proj-svc", "name": "Svc", "family": "ComposeBus Services",
                  "type": "ComposeBus Service", "root": "proj-svc", "status": "active",
                  "specs_dir": "spec"}
        res_svc = validate([reg_svc], repo_root)["projects"][0]
        check(not any("service module" in m for m in res_svc["missing"]),
              f"(f) a top-level .py module must satisfy the service-module check, "
              f"got {res_svc['missing']}")
        check(not any("declared bus namespace" in m for m in res_svc["missing"]),
              f"(f) `NS = \"...\"` must satisfy the declared-namespace check, "
              f"got {res_svc['missing']}")

        p_svc_bad = repo_root / "proj-svc-bad"
        p_svc_bad.mkdir()
        reg_svc_bad = {"slug": "proj-svc-bad", "name": "Svc Bad", "family": "ComposeBus Services",
                      "type": "ComposeBus Service", "root": "proj-svc-bad", "status": "active"}
        res_svc_bad = validate([reg_svc_bad], repo_root)["projects"][0]
        check(any("service module" in m for m in res_svc_bad["missing"]),
              "(f) a service dir with no .py must flag the missing service module")
        check(any("declared bus namespace" in m for m in res_svc_bad["missing"]),
              "(f) a service dir with no namespace text must flag it")

        # ---- (g) Reactive Widget type-specific: compose.yaml + deploy descriptor --
        p_widget = repo_root / "proj-widget"
        p_widget.mkdir()
        (p_widget / "widget.compose.yaml").write_text("x: 1\n")
        (p_widget / "platformio.ini").write_text("[env]\n")
        reg_widget = {"slug": "proj-widget", "name": "Widget", "family": "Reactive Widgets",
                     "type": "Reactive Widget", "root": "proj-widget", "status": "active"}
        res_widget = validate([reg_widget], repo_root)["projects"][0]
        check(not any("widget def" in m for m in res_widget["missing"]),
              f"(g) a *.compose.yaml must satisfy the widget-def check, got {res_widget['missing']}")
        check(not any("deploy descriptor" in m for m in res_widget["missing"]),
              f"(g) platformio.ini must satisfy the deploy-descriptor check, "
              f"got {res_widget['missing']}")

        # ---- (h) an own-repo project (its own .git, OUTSIDE the shared repo_root) -
        with tempfile.TemporaryDirectory() as d3:
            own_root = Path(d3) / "own-proj"
            own_root.mkdir()
            (own_root / ".git").mkdir()
            reg_own = {"slug": "own-proj", "name": "Own", "family": "ComposeApps",
                      "type": "ComposeApp", "root": str(own_root), "status": "active"}
            res_own = validate([reg_own], repo_root)["projects"][0]
            check(res_own["git"]["state"] == "own-repo" and res_own["has_git"] is True,
                  f"(h) a project with its OWN .git must read as own-repo, got {res_own['git']}")
            # pointer_base for an own-repo project is ITS OWN root, not repo_root
            check(res_own["decision_lts"] == str(own_root / "corpora" / "own-proj" /
                                                 "decision-graph-own-proj.lts"),
                  f"(h) own-repo pointer_base must be the project's own root, "
                  f"got {res_own['decision_lts']}")

        # ---- (i) required-registry-field gap surfaces even when root exists -------
        reg_incomplete = {"slug": "proj-ok", "root": "proj-ok"}  # name/type/status absent
        res_incomplete = validate([reg_incomplete], repo_root)["projects"][0]
        check(any(m.startswith("registry:") for m in res_incomplete["missing"]),
              f"(i) an incomplete registry record must surface a registry:<field> gap, "
              f"got {res_incomplete['missing']}")

        # ---- (j) registry round-trip: render -> parse -> validate matches direct --
        rendered = render_registry_lts([reg_ok, reg_svc])
        reg_file = repo_root / "roundtrip.lts"
        reg_file.write_text(rendered)
        parsed = parse_registry(reg_file)
        check(parsed is not None and len(parsed) == 2,
              f"(j) round-tripped registry must parse back 2 projects, got {parsed}")
        check(parsed[0]["slug"] == "proj-ok" and parsed[0]["root"] == "proj-ok",
              f"(j) round-tripped fields must match, got {parsed[0] if parsed else None}")
        rt_payload = validate(parsed, repo_root)
        check(rt_payload["projects"][0]["compliant"] is True,
              "(j) a round-tripped registry must validate identically to the in-memory one")

        # ---- (k) parse_registry(missing file) -> None, never raises ---------------
        check(parse_registry(repo_root / "does-not-exist.lts") is None,
              "(k) parsing a missing registry file must return None, not raise")

        # ---- (l) write_seed_registry_if_absent: seeds once, never overwrites ------
        seed_path = repo_root / "seed-test.lts"
        wrote1 = write_seed_registry_if_absent(seed_path, [reg_ok])
        check(wrote1 is True and seed_path.is_file(), "(l) first call must seed the file")
        original_text = seed_path.read_text()
        wrote2 = write_seed_registry_if_absent(seed_path, [reg_svc])  # different content offered
        check(wrote2 is False, "(l) a second call must NOT overwrite an existing registry")
        check(seed_path.read_text() == original_text,
              "(l) an existing registry's bytes must be untouched by a re-seed attempt")

        # ---- (m) load_or_seed_registry: seeds, then subsequent loads read it back -
        load_path = repo_root / "load-test.lts"
        projs1, seeded1 = load_or_seed_registry(load_path)
        check(seeded1 is True and len(projs1) == len(seed_registry_projects()),
              f"(m) first load must seed all {len(seed_registry_projects())} default "
              f"projects, got seeded={seeded1} n={len(projs1)}")
        projs2, seeded2 = load_or_seed_registry(load_path)
        check(seeded2 is False and len(projs2) == len(projs1),
              "(m) a second load must read the already-seeded file, not re-seed")

        # ---- (n) JSON round-trip / determinism of validate()'s payload shape ------
        payload = validate([reg_ok], repo_root)
        check(set(payload) == {"projects", "ts"}, f"(n) payload shape wrong: {set(payload)}")
        check(json.loads(json.dumps(payload)) == payload,
              "(n) payload must be JSON round-trippable")
        expected_keys = {"slug", "name", "family", "type", "root", "status", "parent",
                         "seed_confidence", "git", "has_git", "has_decision_file",
                         "decision_lts", "missing", "compliant", "root_exists"}
        check(set(payload["projects"][0]) == expected_keys,
              f"(n) per-project keys wrong: {set(payload['projects'][0])}")

        # ---- (o) emit envelope shape matches the sibling daemons -------------------
        # Post-[SPEC:CXC-SOURCES-ON-BUS] this goes through the BUS, so the check
        # moved down one layer: a real ComposeIOBus over a recording transport.
        # Same contract, and it now also pins the SCOPE RESOLUTION
        # (Scope.GLOBAL -> "global"), which the hand-built BusMessage could not
        # get wrong and the bus path can.
        class _FakeTransport:
            def __init__(self):
                self.sent = []
            def start(self): pass
            def stop(self): pass
            def send(self, msg):
                self.sent.append(msg)
            def subscribe(self, ns, cb): pass
            def subscribe_all(self, cb): pass
            def subscribe_pattern(self, p): pass
            @property
            def is_connected(self): return True

        from composeio.bus import ComposeIOBus
        ft = _FakeTransport()
        probe_bus = ComposeIOBus(bus_id="pv-selftest", global_transport=ft)
        probe_bus.start(announce_time=False)
        source_bus.declare(probe_bus, NS, scope=Scope.GLOBAL, source=SRC)
        _publish(probe_bus, {"projects": [], "ts": "x"}, 1)
        check(len(ft.sent) == 1, f"(o0) exactly one message on the wire: {len(ft.sent)}")
        msg = ft.sent[0]
        d = msg.to_json() if hasattr(msg, "to_json") else None
        check(d is not None and d["ns"] == NS and d["scope"] == "global"
              and d["src"] == SRC,
              f"(o) published BusMessage must carry ns={NS} scope=global "
              f"src={SRC}, got {d}")
        check(d is not None and list(d) == ["ns", "type", "src", "ts", "scope",
                                            "seq", "val"],
              f"(o2) wire key order changed: {list(d) if d else d}")

        # ---- (p) FactLattice house-style ids + additive maker fields --------------
        # The migrated registry uses [PROJECT-UPPERCASE-SLUG] (SPEC-compliant,
        # lts_find-queryable); the parser must read it AND lowercase the slug, so
        # both id forms coexist and downstream slugs stay convention-stable.
        house_style = (
            "## Projects\n\n"
            "### [PROJECT-INTELLIGENCE-CONSOLE] Intelligence Console\n"
            "- type: ComposeApp\n- root: .\n- status: active\n\n"
            "### [PROJECT:legacy-colon] Legacy Colon\n"
            "- type: ComposeApp\n- root: .\n- status: active\n")
        hp = repo_root / "house-style.lts"
        hp.write_text(house_style)
        parsed_hs = parse_registry(hp)
        slugs = {p["slug"] for p in (parsed_hs or [])}
        check(slugs == {"intelligence-console", "legacy-colon"},
              f"(p) both id forms must parse to lowercase-kebab slugs, got {slugs}")

        # a MAKER project instance carries the additive fields; they survive
        # parse (schema-tolerant) and render (round-trip), and NEVER reach the
        # validate() payload (its key set is frozen — additive to the registry
        # only, so the Projects widget is untouched).
        maker = {"slug": "galactica-throttle", "name": "Galactica Throttle",
                 "family": "ComposeApps", "type": "ComposeApp",
                 "root": "does/not/exist/yet", "status": "planned",
                 "mcu": "esp32-c3", "baseline_stack_rev": "v0",
                 "widget": "dual-throttle.compose.yaml",
                 "slots": "throttle/left, throttle/right", "current_step": "a"}
        rendered_maker = render_registry_lts([maker])
        check("### [PROJECT-GALACTICA-THROTTLE]" in rendered_maker,
              "(p) a maker project renders as a house-style [PROJECT-UPPERCASE] id")
        mp = repo_root / "maker.lts"
        mp.write_text(rendered_maker)
        parsed_maker = parse_registry(mp)[0]
        check(parsed_maker["slug"] == "galactica-throttle",
              f"(p) maker slug lowercased on parse, got {parsed_maker['slug']}")
        for f in ("mcu", "baseline_stack_rev", "widget", "slots", "current_step"):
            check(parsed_maker.get(f) == maker[f],
                  f"(p) maker field {f} must round-trip, got {parsed_maker.get(f)!r}")
        maker_payload = validate([parsed_maker], repo_root)["projects"][0]
        check(set(maker_payload) == expected_keys,
              f"(p) maker fields must NOT leak into the frozen validate() payload, "
              f"got extra {set(maker_payload) - expected_keys}")
        check(maker_payload["root_exists"] is False,
              "(p) the planned maker project (root not yet created) reads root_exists=False")

    if fails:
        for m in fails:
            sys.stderr.write("FAIL " + m + "\n")
        return 1
    sys.stderr.write("PASS: project_validator selftest (16/16 groups)\n")
    return 0


def main(argv: Optional[List[str]] = None, *, bus=None, stop=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--registry", default=DEFAULT_REGISTRY_PATH,
                    help=f"path to the project registry (default: {DEFAULT_REGISTRY_PATH})")
    ap.add_argument("--repo-root", default=str(REPO_ROOT_DEFAULT))
    ap.add_argument("--mqtt", default=os.environ.get("COMPOSEBUS_MQTT", "mqtt://mqtt-broker.local:1883"))
    ap.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_S)
    ap.add_argument("--once", action="store_true",
                    help="compute + print one payload; publish it too if the broker is reachable")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)

    if a.selftest:
        return _selftest()

    repo_root = Path(a.repo_root).resolve()

    if a.once:
        projects, seeded = load_or_seed_registry(a.registry)
        if seeded:
            sys.stderr.write(f"[project_validator] SEEDED {a.registry} with "
                             f"{len(projects)} best-guess project(s) — flagged for David\n")
        payload = validate(projects, repo_root)
        print(json.dumps(payload, indent=2))
        try:
            once_bus = _make_bus(a.mqtt)
            transport = once_bus.global_transport
            deadline = time.time() + 2.0
            while transport is not None and not transport.is_connected \
                    and time.time() < deadline:
                time.sleep(0.1)
            if transport is not None and transport.is_connected:
                _publish(once_bus, payload, 1)
                time.sleep(0.3)  # give paho's loop thread a beat to flush the publish
                sys.stderr.write(f"[project_validator] published to composeio/global/{NS} "
                                 f"@ {a.mqtt}\n")
            else:
                sys.stderr.write(f"[project_validator] broker {a.mqtt} not reachable "
                                 f"within 2s — printed only, not published\n")
            transport.stop()
        except Exception as exc:
            sys.stderr.write(f"[project_validator] publish attempt failed (printed only): {exc!r}\n")
        return 0

    run(a.registry, repo_root, a.mqtt, a.interval, bus=bus, stop=stop)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
