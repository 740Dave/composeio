#!/usr/bin/env python3
"""Role homes, role memory stores and the DERIVED memory index
([SPEC:CXC-FLEET] · [CXC-FC-PROJECT-DIR], [CXC-FC-ROLE-MEMORY], [CXC-FC-STORE-ACCESS],
[CXC-FC-MEMORY-INDEX-DERIVED], [CXC-FC-DEFORK]).

THE ONE SEAM.  Every spawn path derives an Intelligence's working directory from
`project_dir_for(role)` HERE and nowhere else.  There were two spawn paths on
2026-08-12 — `IntelligenceHost.spawn` (which set cwd from a declared home) and
`ptycore.PtyChild` under the Terminal Widget (which defaulted `cwd=None` -> the
host's cwd -> THE REPO) — and the invariant lived in exactly one of them.  That is
[INSIGHT-FORK-DIVERGENCE-IS-A-CLASS]: the fix was not forgotten, it was implemented
in one fork.  This module exists so there is one definition to call and a
cross-implementation test to point at ([CXC-FC-DEFORK]).

THE THREE AXES ([CXC-FC-THREE-AXES]) — nothing here may key off more than one:

    ROLE         memory, bootstrap tier, agent registration   `composeux-architect`
    INSTANCE ID  channel topics, telemetry, locks, voice      `architect-hosted`
    HOST         where it renders                             (carries no identity)

cwd keys the harness's project dir, and the project dir keys memory + transcripts +
agent-def registration TOGETHER.  cwd can therefore key exactly ONE axis, and David
ruled 2026-08-12 that it keys the ROLE ([DD-FC-MEMORY-GRAIN], [DD-FC-PROJECT-DIR-LAYOUT]).
Instance identity rides `INTELLIGENCE_ID` and the manifest — NEVER the cwd.

ONE STORE, TWO ACCESS PATHS ([CXC-FC-STORE-ACCESS]).  A top-level Intelligence gets
its store automatically because its cwd IS the role home.  A DISPATCHED SUBAGENT runs
in an ephemeral `~/.cache/claude-worktrees/...` worktree, so cwd cannot be the
mechanism for it — a prune would destroy the store
([INSIGHT-WORKTREE-IS-NOT-THE-ENVIRONMENT] instance (4) applied to memory).  It
addresses `role_memory_dir(role)` BY PATH instead.  Same bytes, same location, one
store; only the loading mechanism differs by caller.  Two stores would diverge
invisibly because each caller only ever sees its own.

THE INDEX IS DERIVED, NOT MAINTAINED ([CXC-FC-MEMORY-INDEX-DERIVED]).  Per-role
memory makes two live writers the NORMAL case, and memory is one file per fact, so
the only real contention is `MEMORY.md`.  Regenerating it by scanning `memory/*.md`
frontmatter means concurrent writers touch disjoint files and the index is COMPUTED —
it cannot be corrupted by interleaving, and a deleted index is a recoverable state
rather than data loss.  Shipping per-role memory WITHOUT this ships the defect.
"""
from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional, Tuple

__all__ = [
    "RoleRefused",
    "repo_root",
    "compose_instances_root",
    "normalize_role",
    "project_dir_for",
    "role_memory_dir",
    "project_dir_key",
    "agent_descriptor_dirs",
    "known_roles",
    "read_memory_frontmatter",
    "render_memory_index",
    "regenerate_memory_index",
    "MEMORY_INDEX_FILENAME",
    "MEMORY_INDEX_HEADER",
]


class RoleRefused(ValueError):
    """A role was absent, blank, or not a legal store name.

    A REFUSAL, never a fallback ([CXC-FC-UNIT] default, [CXC-FC-PROJECT-DIR]).  A
    guessed role silently mis-keys the project dir, which puts an Intelligence's
    memory in somebody else's pile — the exact defect this module closes.  Refusing
    is loud; guessing is silent and wrong.
    """


# ── Locations ────────────────────────────────────────────────────────────────────
def repo_root() -> str:
    """`<repo>`, from this file's own location (`<repo>/python/composeio/instance_home.py`).

    `ptycore.repo_root` DELEGATES here rather than re-spelling the dirname walk — one
    definition, per [CXC-FC-ANTI]'s no-second-implementation rule.
    """
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def compose_instances_root() -> str:
    """`~/compose-instances` — the fleet's home root, OUTSIDE the repo
    ([DD-FC-PROJECT-DIR-LAYOUT]).

    Outside the repo so the project key is stable under checkout moves and so a fleet
    does not pollute the working tree.  `COMPOSE_INSTANCES_ROOT` overrides — that
    override is what lets every test in this area run against a tmpdir instead of the
    human's live store.
    """
    return os.path.abspath(os.path.expanduser(
        os.environ.get("COMPOSE_INSTANCES_ROOT", "~/compose-instances")))


_ROLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def normalize_role(role: Optional[str]) -> str:
    """The role, validated. Raises `RoleRefused` on absent/blank/illegal.

    A role becomes a DIRECTORY NAME, so a value carrying a path separator or `..`
    would escape `~/compose-instances/` and key some unrelated directory.  That is a
    refusal, not a sanitisation: silently rewriting a role name would make two
    different roles collapse into one store.
    """
    r = str(role or "").strip()
    if not r:
        raise RoleRefused(
            "[CXC-FC-PROJECT-DIR] no role — a managed Intelligence's project dir is "
            "keyed by ROLE, and an absent role would fall back to the repo, putting "
            "this instance's memory in the shared pile. Declare `role:` in the scene "
            "(the pane's params are the (instance_id, role) PAIR — [CXC-FC-UNIT]).")
    if not _ROLE_RE.match(r):
        raise RoleRefused(
            f"[CXC-FC-PROJECT-DIR] illegal role {r!r} — a role becomes the directory "
            f"name under {compose_instances_root()}/, so it must match "
            f"{_ROLE_RE.pattern} (no separators, no '..').")
    return r


def project_dir_for(role: Optional[str]) -> str:
    """`~/compose-instances/<role>/` — THE cwd for every instance of `role`.

    THE SINGLE DEFINITION SITE ([CXC-FC-DEFORK]).  `IntelligenceHost.spawn` and the
    Terminal Widget's `PtyChild` spawn both resolve their child's cwd through this
    function, so the same `(instance_id, role)` yields the same project key from
    either path.  A per-path implementation is what produced the 2026-08-12 defect,
    and a per-path TEST would have passed on the fixed path and proved nothing about
    the other.

    Keyed by ROLE, never by instance id: `architect` and `architect-hosted` are two
    INSTANCES of `composeux-architect` and share one store BY DESIGN
    ([DD-FC-MEMORY-GRAIN]).  It is never the repo's key.
    """
    return os.path.join(compose_instances_root(), normalize_role(role))


def role_memory_dir(role: Optional[str]) -> str:
    """`~/compose-instances/<role>/memory/` — the ONE store for `role`.

    This is the path a DISPATCHED SUBAGENT states in its role descriptor and reaches
    with ordinary file tools ([CXC-FC-STORE-ACCESS] access path 2).  It resolves to
    the same directory the top-level Intelligence loads by cwd (access path 1), and
    it is outside every worktree, so a routine prune destroys nothing.
    """
    return os.path.join(project_dir_for(role), "memory")


def project_dir_key(cwd: str) -> str:
    """Model the harness's `cwd -> ~/.claude/projects/<key>/` sanitisation.

    Kept here BESIDE the derivation it constrains; `ptycore.project_dir_key` is this
    function (re-exported), so the two cannot drift.  Distinct cwds -> distinct
    project keys -> transcripts + memory that never co-locate; a SHARED cwd collapses
    N instances into one key ([INSIGHT-CWD-IS-THE-SCOPE-LEVER]).
    """
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(os.path.expanduser(cwd)))


# ── The role roster — DERIVED from descriptors, never a hand list ────────────────
def agent_descriptor_dirs() -> List[str]:
    """Every directory that holds role descriptors, most-local first.

    Today: the repo's `.claude/agents/` and the FactLattice agent pool
    (`~/factlattice-internal-dev/agents/`, `FACTLATTICE_AGENTS_DIR` overrides).  A
    missing directory contributes nothing and is not an error — a machine without the
    FactLattice deployment simply has fewer roles.
    """
    dirs = [os.path.join(repo_root(), ".claude", "agents")]
    fl = os.environ.get("FACTLATTICE_AGENTS_DIR")
    dirs.append(os.path.abspath(os.path.expanduser(
        fl if fl else "~/factlattice-internal-dev/agents")))
    extra = os.environ.get("COMPOSEIO_EXTRA_AGENTS_DIRS", "").strip()
    if extra:
        dirs.extend(os.path.abspath(os.path.expanduser(p))
                    for p in extra.split(os.pathsep) if p.strip())
    return dirs


def _declares_frontmatter_name(path: str) -> bool:
    """True when `path` opens with a YAML frontmatter block declaring `name:`.

    THE DISCRIMINATOR, and the reason the roster needs no hand-maintained list
    ([CXC-FC-ROLE-MEMORY] prohibition).  An agent-pool directory also holds prose
    (`QUALITY-EVENT-PROTOCOL.md`) and editor leftovers; a role descriptor is
    recognisable by its own contract rather than by a curated allow-list that goes
    stale the first time a role is added.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return False
            for _ in range(40):
                line = fh.readline()
                if not line or line.strip() == "---":
                    return False
                if re.match(r"^name\s*:", line):
                    return True
    except OSError:
        return False
    return False


def known_roles(dirs: Optional[Iterable[str]] = None) -> List[str]:
    """Every role in the roster — the BASENAME of each role descriptor, sorted.

    "Role" means EVERY role, not only top-level Intelligences ([CXC-FC-ROLE-MEMORY]):
    `composeux-render-engineer`, `composeio-expert`, `git-expert` and
    `defect-investigator` each get a store exactly as `composeux-architect` does.
    That is the payoff that makes per-role the RIGHT grain rather than merely the
    cheaper one — a dispatched role gains continuity across dispatches, and briefs
    shrink as the store grows.

    DERIVED, never chosen: adding a descriptor is the ONLY act needed to give a role
    a store.  Stores are created LAZILY on first write, so a never-used role has no
    directory and an empty store and an unused role are the same observable state.
    """
    out: set = set()
    for d in (dirs if dirs is not None else agent_descriptor_dirs()):
        try:
            entries = sorted(os.listdir(d))
        except OSError:
            continue
        for name in entries:
            if not name.endswith(".md"):
                continue
            path = os.path.join(d, name)
            if os.path.isfile(path) and _declares_frontmatter_name(path):
                out.add(name[:-3])
    return sorted(out)


# ── The DERIVED memory index ([CXC-FC-MEMORY-INDEX-DERIVED]) ─────────────────────
MEMORY_INDEX_FILENAME = "MEMORY.md"
MEMORY_INDEX_HEADER = (
    "<!-- DERIVED FILE — regenerated from memory/*.md frontmatter by\n"
    "     composeio.instance_home.regenerate_memory_index ([CXC-FC-MEMORY-INDEX-DERIVED]).\n"
    "     Do NOT hand-edit: two live instances of one role write this store, and a\n"
    "     hand-maintained index loses a pointer under interleaving — silently. -->\n")


def read_memory_frontmatter(path: str) -> Tuple[str, str, str]:
    """`(name, description, group)` from one memory file's YAML frontmatter.

    Parsed with a deliberately small reader rather than PyYAML: this runs in the
    console's process and in a bare subagent's, and an index that cannot be
    regenerated because an optional dependency is absent is an index that is
    hand-maintained again.  `name` falls back to the basename, which is what the
    index links anyway; `group` comes from `metadata.group` when an author declares
    one and is otherwise empty (the flat section).
    """
    basename = os.path.basename(path)
    name = basename[:-3] if basename.endswith(".md") else basename
    description = ""
    group = ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            if fh.readline().strip() != "---":
                return (name, "", "")
            in_metadata = False
            for _ in range(200):
                line = fh.readline()
                if not line or line.strip() == "---":
                    break
                stripped = line.strip()
                indented = line[:1] in (" ", "\t")
                if not indented:
                    in_metadata = stripped.startswith("metadata:")
                    m = re.match(r"^(name|description)\s*:\s*(.*)$", stripped)
                    if m:
                        val = m.group(2).strip().strip("'\"")
                        if m.group(1) == "name" and val:
                            name = val
                        elif m.group(1) == "description":
                            description = val
                elif in_metadata:
                    m = re.match(r"^group\s*:\s*(.*)$", stripped)
                    if m:
                        group = m.group(1).strip().strip("'\"")
    except OSError:
        pass
    return (name, description, group)


def render_memory_index(memory_dir: str, *, title: str = "Memory Index") -> str:
    """The index TEXT computed from `memory_dir/*.md` — pure, no writes.

    Deterministic and IDEMPOTENT by construction: files are sorted, groups are
    sorted, and nothing is carried over from a previous index.  Two regenerations
    from the same inputs are byte-identical, which is what makes "regenerate" a safe
    thing for any writer to do at any time.
    """
    entries: List[Tuple[str, str, str, str]] = []   # (group, filename, name, description)
    try:
        names = sorted(os.listdir(memory_dir))
    except OSError:
        names = []
    for fn in names:
        if not fn.endswith(".md") or fn == MEMORY_INDEX_FILENAME:
            continue
        path = os.path.join(memory_dir, fn)
        if not os.path.isfile(path):
            continue
        name, description, group = read_memory_frontmatter(path)
        entries.append((group, fn, name, description))

    lines = [MEMORY_INDEX_HEADER, f"# {title}\n"]
    ungrouped = [e for e in entries if not e[0]]
    grouped = sorted({e[0] for e in entries if e[0]})
    if ungrouped:
        for _g, fn, name, desc in sorted(ungrouped, key=lambda e: e[1]):
            lines.append(f"- [{name}]({fn})" + (f" — {desc}" if desc else ""))
        lines.append("")
    for g in grouped:
        lines.append(f"## {g}\n")
        for _g, fn, name, desc in sorted((e for e in entries if e[0] == g),
                                         key=lambda e: e[1]):
            lines.append(f"- [{name}]({fn})" + (f" — {desc}" if desc else ""))
        lines.append("")
    if not entries:
        lines.append("_(no facts yet)_\n")
    return "\n".join(lines).rstrip("\n") + "\n"


def regenerate_memory_index(memory_dir: str, *, title: str = "Memory Index") -> str:
    """Write `memory_dir/MEMORY.md` from the facts on disk and return its path.

    The whole point of a DERIVED index: any writer may call this at any moment, and
    two writers adding disjoint facts concurrently both survive, because neither one
    edits the index — each writes its own file and the index is recomputed from the
    filesystem.  A `MEMORY.md` that is missing or corrupt is therefore a recoverable
    state, not data loss.
    """
    import threading
    os.makedirs(memory_dir, exist_ok=True)
    path = os.path.join(memory_dir, MEMORY_INDEX_FILENAME)
    text = render_memory_index(memory_dir, title=title)
    # The scratch file is keyed by WRITER, not by target.  A shared `MEMORY.md.tmp` is itself a
    # concurrent-write defect: two regenerating writers race, one's `os.replace` finds the other
    # already consumed the scratch file, and it raises FileNotFoundError — i.e. the exact class
    # this whole node exists to remove, reintroduced by the mitigation. Found by the concurrency
    # test rather than reasoned about, which is why that test writes from two real threads.
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)      # atomic — a reader never sees a half-written index
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return path
