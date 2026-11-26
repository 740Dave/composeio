# worktrunk audit — cutover plan vs. the documented tool, 2026-08-17

> Audit of `docs/architecture/CUTOVER-EVIDENCE-worktrunk-sweeps-2026-08-17.md` (`f6e0b26`) against the
> full worktrunk documentation set, the Rust source at upstream `main`, and behaviour measured in a
> scratch lab. **Nothing in this audit was run against the primary checkout.** Every behavioural claim
> below is either (a) quoted from docs with a URL, (b) read from source with a file:line, or
> (c) measured in a throwaway repo, with both arms shown. Those three are kept separate on purpose.

## Provenance of this audit

| item | value |
|---|---|
| docs read | all 16 pages of `docs/content/` in the upstream repo (the source of worktrunk.dev), plus `README.md`, `CLAUDE.md`, `plugins/worktrunk/CLAUDE.md`, both shipped skills, `skills/wt-switch-create/rationale.md` |
| source read | upstream `main` @ `d73ce69` — *"refactor(codex-cloud): replace the Taskfile and mise with a shell script (#3839)"*, committed **2026-08-17 11:13 -0700**, i.e. today. Upstream pushed while this audit ran, exactly as the role definition warns. |
| binary exercised | **v0.74.0** (released 2026-08-14), `worktrunk-aarch64-apple-darwin`, downloaded into the session scratchpad and invoked by absolute path. **Not installed.** `wt` is still not on David's `PATH` — nothing has been deployed. |
| lab | 16 throwaway repos under the session scratchpad, with `HOME` redirected to a scratch dir so no user config was read or written |
| issues checked | #2939, #2940, #3769, #3770, #2753, plus the full commit history of `plugins/worktrunk/hooks/hooks.json` via the GitHub API |

### Measured state of composeio, re-taken 2026-08-17

| measurement | 08-17 earlier | **now** |
|---|---|---|
| worktrees registered | 41 | **42** |
| local branches | 77 | **81** |
| under `/private/tmp` | 29 | **29** |
| under `~/.cache/claude-worktrees` | 9 | **8** |
| under `~/worktrees` | 3 | **3** |
| inside the repo tree | 2 | **1** (`wt-ctl`) |
| **detached-HEAD worktrees** | not measured | **4** ⛔ live #3769 exposure |
| **locked worktrees** | not measured | **0** |
| `.git` | 1.3 GiB | **1.3 GiB** |

Still trending up. Zero mechanisms deployed.

---

# Q1 — The dispatch model

## Q1a — Two different paths, and yes, the plugin is enforcement

They are two different paths, documented on two different pages.

**Path A — the handoff pattern** ([tips-patterns#agent-handoffs](https://worktrunk.dev/tips-patterns/#agent-handoffs)):

> "Spawn a worktree with an agent CLI running in the background. … **tmux** (new detached session):
> `tmux new-session -d -s fix-auth-bug "wt switch --create fix-auth-bug -x claude -- '…'"` …
> This lets one agent session hand off work to another that runs in the background. **Hooks run inside
> the multiplexer session/pane.**"

This is **guidance**. It is a shell recipe a human or an agent types. Nothing enforces it. The shipped
skill (`skills/worktrunk/SKILL.md:130-165`) gates it behind three conditions — *"User explicitly
requests spawning/handoff · User is in a supported multiplexer (tmux or Zellij) · The user's project
instructions … authorize this pattern"* — and adds *"**Do not use this pattern** for normal worktree
operations."*

**Path B — the plugin** ([claude-code#worktree-isolation](https://worktrunk.dev/claude-code/#worktree-isolation-claude-code-only)):

> "Claude Code agents can run in isolated worktrees (`isolation: "worktree"`). By default, Claude Code
> creates these with `git worktree add`. The plugin's `WorktreeCreate` and `WorktreeRemove` hooks route
> this through `wt switch --create` and `wt remove` instead, so worktrees created by agents get
> worktrunk's naming conventions, hooks, and lifecycle management."

This is **enforcement**, and it needs no agent cooperation. The hook bodies at upstream `main`
(`plugins/worktrunk/hooks/hooks.json`) are:

```
WorktreeCreate: bash -c 'set -o pipefail; name=$(jq -er .name) || exit 1;
  cd "${CLAUDE_PROJECT_DIR:-.}" || exit 1;
  bash "$CLAUDE_PLUGIN_ROOT/hooks/wt.sh" switch --create "$name" --no-cd --format=json | jq -er .path'

WorktreeRemove: bash -c 'p=$(jq -er .worktree_path) || exit 1; [ -e "$p/.git" ] || exit 0;
  bash "$CLAUDE_PLUGIN_ROOT/hooks/wt.sh" -C "$p" remove --foreground "$p"'
```

The `WorktreeCreate` hook consumes `.name` from the harness's JSON. Our own falsification test already
measured that Claude Code fires `WorktreeCreate` with `{"name":"agent-a0f7455d07c3fb9df"}` for an
`Agent(isolation: worktree)` dispatch. **The shapes match, so routing works with no agent
cooperation** — this is an inference from two measured facts, labelled as such.

### ⭐ FINDING 1 — Upstream's own skill says DO NOT use `Agent(isolation: "worktree")` for our case

`skills/worktrunk/SKILL.md:161-176`, section *"Parallel sub-Agents (single Claude Code session)"*:

> "To spawn multiple sub-Agents that each work in their own worktree from one Claude Code session — no
> terminal multiplexer, no human in the other pane — pre-start each worktree from the parent and pass
> the path into the sub-Agent prompt:
>
> ```bash
> wt switch --create <branch> --no-cd --no-hooks
> ```
>
> Then call the `Agent` tool **without** `isolation: "worktree"`, naming the path in the prompt:
>
> ```
> You are working in `/abs/path/to/worktrunk.<branch>` on branch `<branch>`.
> All edits must stay in that worktree.
> ```
>
> … **Do not** use `Agent { isolation: "worktree" }` for this. Claude Code passes its internal agent ID
> as `name` to the `WorktreeCreate` hook, so `wt` creates the worktree as `worktrunk.agent-<id>` on a
> throwaway branch. If the sub-Agent then creates a feature branch on top, you end up with
> **non-canonical paths, orphan branches, and post-start hooks fired against the wrong branch**.
> Pre-creating with `wt switch --create` keeps path, branch, and hook target aligned."

That is a verbatim description of our measured pathology — `wt-agent-a42f272cb5f989539-…`,
`composeio-agent-<id>`, orphan branches, 81 local branches. **Upstream diagnosed our disease
independently and its documented remedy is the opposite of what the cutover plan assumes.** The
cutover plan's "to-be state" treats plugin-routed `isolation: worktree` as the enforcement mechanism.
Upstream says to stop using `isolation: worktree` entirely and pre-create instead.

This is the single most consequential correction in this audit.

## Q1b — The multiplexer is NOT load-bearing. `-x` needs no TTY and survives the parent.

**Docs:** silent on TTY. Both documented handoff examples use a multiplexer; there is no documented
plain-`&` variant, and no documented supervision/attach story without one. The `-x` help text
(`wt switch --help`, rendered at [switch.md](https://worktrunk.dev/switch/)) says only:

> "**Replaces the wt process with the command after switching, giving it full terminal control.**
> Useful for launching editors, AI agents, or other interactive tools."

**Source:** `src/output/global.rs:485-513` — on Unix `-x` is `exec()`, with
`.stdin(inherit).stdout(inherit).stderr(inherit)`. The comment says *"This gives the command full TTY
access … enabling interactive programs like `claude` to work properly."* There is **no TTY check and
no refusal path**.

**Measured (lab):**

```
$ ( wt switch --create bg-agent --yes -x ./agentsim.sh -- 'do the thing' >log 2>&1 </dev/null & )
$ cat proof
AGENT RAN at 1787006801 cwd=…/lab/rx.bg-agent tty=NO args=do the thing
```

`tty=NO`, ran fine. Backgrounded with a plain `&`, `</dev/null`, no multiplexer.

Survival across parent exit:

```
$ bash -c "cd …/rx; wt switch --create orphan-test --yes -x ./longagent.sh -- out.txt >/dev/null 2>&1 </dev/null & disown; exit 0"
$ ps -o pid,ppid,stat,command -p 46541
  PID  PPID STAT COMMAND
46541     1 S    /bin/bash …/longagent.sh …
```

Reparented to PID 1 and still running. **The multiplexer buys attach/observability, not survival.**

### ⛔ FINDING 2 — `--no-cd` (or `[switch] cd = false`) makes `-x` run in the WRONG DIRECTORY while reporting the right one. Unreported upstream.

This is a new bug, found in the lab, not in any doc or issue. It is the exact "isolation flag reports
success anyway" shape our own corpus records at `~/.claude/agents/other-architect.md:278-280`.

**Control arm** — default (`cd` enabled), no shell integration installed:

```
$ wt switch --create probe-ctl --yes -x ./probe.sh
◎ Executing (--execute) @ …/lab/rx.probe-ctl:
cwd=…/lab/rx.probe-ctl  toplevel=…/lab/rx.probe-ctl        ← CORRECT
```

**Failing arm** — `--no-cd` flag:

```
$ wt switch --create probe-nocd --no-cd --yes -x ./probe.sh
◎ Executing (--execute) @ …/lab/rx.probe-nocd:               ← the banner LIES
cwd=…/lab/rx            toplevel=…/lab/rx                    ← the INVOKING dir
```

**Failing arm — config form** (higher blast radius, `--config-set 'switch.cd=false'`): identical.

**Worst arm** — `--no-cd` invoked from a non-repo directory with `-C`:

```
$ cd …/lab/elsewhere && wt -C …/lab/rx switch --create probe-elsewhere --no-cd --yes -x ./probe.sh
◎ Executing (--execute) @ …/lab/rx.probe-elsewhere:
cwd=…/lab/elsewhere  toplevel=fatal: not a git repository (or any of the parent directories): .git
```

The agent runs **outside any repository** while `wt` prints that it is executing in the worktree.

**Root cause, from source.** `src/output/global.rs:394-399` — `target_dir` is populated *only* by
`change_directory()`. `--no-cd` / `[switch] cd = false` suppresses that call, so
`src/output/global.rs:486` falls through to `let exec_dir = target_dir.unwrap_or_else(|| Path::new("."))`
— the invoking cwd. `src/output/global.rs:502` then also skips `scrub_git_discovery_env_vars`, so an
inherited `GIT_DIR` is forwarded as well. The `◎ Executing (--execute) @ <path>` banner is printed on
a separate code path and is not conditioned on any of this.

Search of upstream issues found only #2753 (*"Aliases template vars reference the current worktree
instead of the target one (with `--no-cd`)"*, CLOSED 2026-05-13) — related in spirit, different
mechanism, and it does not cover cwd. **This appears to be unreported. Recommend filing it.**

Not currently in our path: the plugin's `WorktreeCreate` hook uses `--no-cd` but **not** `-x`, and the
upstream parallel-sub-Agent recipe uses `--no-cd --no-hooks` with **no** `-x`. Both are safe. The trap
is only for someone composing `--no-cd -x`, which is the natural instinct for headless dispatch — i.e.
exactly what we would have written.

## Q1c — Residual gap: raw `git worktree add` is uncovered at creation, but recoverable after

**Nothing in worktrunk intercepts `git worktree add`.** There is no git hook for it, and the FAQ is
explicit that worktrunk installs *"No global git hooks"*
([faq#what-worktrunk-does-not-create](https://worktrunk.dev/faq/#what-files-does-worktrunk-create)).
An agent typing `git worktree add /tmp/x -b y` into a Bash tool call is invisible to worktrunk at
creation time. **That is 29 of our 42 worktrees.**

But — and this is the load-bearing answer to the role's crux question — **`wt` manages worktrees it did
not create.** Measured, in full:

```
$ git worktree add -b handrolled /tmp/lab-handrolled-wt     # raw git, off-template path
$ wt list
+ handrolled     ⚑_    ../../../../../../lab-handrolled-wt
```

`⚑` is documented at [list.md](https://worktrunk.dev/list/) as `worktree.state "branch_worktree_mismatch"`
— *"Worktree isn't at the path its branch implies."* So they are **seen and flagged**, not ignored.

- `wt merge` from inside a hand-rolled worktree: **works** (squashed, landed, worktree removed, exit 0).
- `wt remove <path>` on a hand-rolled worktree: **works**.
- `wt step relocate` moves them onto the template path, **preserving dirty content**:

```
$ wt step relocate --dry-run
○ 2 worktrees would be relocated:
  strayA: /private/tmp/lab-strayA → …/lab/r10.strayA
  strayB: /private/tmp/lab-strayB → …/lab/r10.strayB
$ wt step relocate
✓ Relocated 2 worktrees
$ find … -name wip.txt
…/lab/r10.strayB/wip.txt          ← the dirty file survived the move
```

**So the residual gap is narrower than the cutover plan assumes.** worktrunk does not prevent
hand-rolled creation; it makes hand-rolled worktrees *visible, relocatable, mergeable and removable
after the fact*. `wt step relocate` is a directly usable one-shot remedy for our 29 strays. The plan
should say so — it currently does not mention `relocate` at all.

---

# Q2 — What the plugin actually installs

**`wt config plugins claude install` is exactly two `claude` CLI calls.** From source,
`src/commands/config/plugins.rs:36-45`:

```rust
super::run_plugin_cli("claude", &["plugin", "marketplace", "add", "max-sixty/worktrunk"])?;
super::run_plugin_cli("claude", &["plugin", "install", "worktrunk@worktrunk"])?;
```

It writes no worktrunk config, touches no `settings.json`, and **does not install the statusline** —
that is a manual `settings.json` edit the docs show separately. The marketplace source is the GitHub
repo at its **default branch (`main`)**, not a release tag, so the plugin a user gets is upstream HEAD.

| piece | what it does | guidance or enforcement |
|---|---|---|
| skill `worktrunk` | ~16 reference docs mirrored from worktrunk.dev + workflow guidance for config, hooks, LLM commits, the agent-handoff pattern, and the parallel-sub-Agent pattern | **guidance** |
| skill `wt-switch-create` (the `/wt-switch-create` command) | create-and-enter flow: `EnterWorktree({name})` → falls back to `wt -C <repo> switch --create <branch> --no-cd --format=json` + `EnterWorktree({path})` | **guidance** (a skill the model chooses to follow) |
| hooks `UserPromptSubmit` / `Notification` / `PreToolUse(AskUserQuestion)` / `PermissionRequest` / `Stop` | `wt config state marker set 🤖\|💬`, all suffixed `\|\| true` | **enforcement**, but cosmetic — writes a git-config marker for `wt list` |
| hook `SessionEnd` | `wt config state marker clear \|\| true` | **enforcement**, cosmetic |
| hook `WorktreeCreate` | routes harness worktree creation through `wt switch --create <name> --no-cd --format=json`, returns `.path` | ⭐ **enforcement** — the real one |
| hook `WorktreeRemove` | `wt -C "$p" remove --foreground "$p"`, guarded by `[ -e "$p/.git" ] \|\| exit 0` | ⭐ **enforcement** |
| statusline | **not installed by the command**; documented as a manual `settings.json` edit | n/a |

## ⛔ Issue #2939 — verified fixed in the build a user actually gets

The `WorktreeRemove` hook at upstream `main` (`d73ce69`, today) contains **no `-D`**. Verified three ways:

1. Direct read of `plugins/worktrunk/hooks/hooks.json` at `d73ce69` — quoted in full under Q1a above.
2. Full file history via the GitHub API: `eaa230bc`, 2026-05-30, *"fix(plugin): drop -D from
   WorktreeRemove hook to preserve unmerged branches (#2940)"*. Fourteen commits have touched that file
   since; none reintroduced it.
3. [Issue #2939](https://github.com/max-sixty/worktrunk/issues/2939) is CLOSED, closed 2026-05-30. The
   maintainer's own comment: *"I've worked really hard to try and ensure there's no chance of data
   loss, but I guess I was predominantly auditing the rust code and didn't think so much of the hooks
   that themselves call into `wt`."* The reporter recovered via a dangling ref.

Because the marketplace tracks the repo's default branch, a user installing today gets the fixed hook.
**Note the class, though:** the plugin is versionless by design (`plugins/worktrunk/CLAUDE.md`: *"The
manifest deliberately carries no `version` field: installs pin the marketplace git SHA"*). There is no
way to pin a reviewed plugin revision. A future regression in `hooks.json` arrives on the next
`claude plugin update` with no changelog gate. **That is a standing risk, not a resolved one.**

### ⛔ FINDING 3 — installing the plugin collides with our existing user-scope hook

`~/.claude/settings.json` registers `WorktreeCreate → bash ~/.claude/hooks/create-worktree.sh`, at USER
scope for all repos. That script emits a path on stdout (`create-worktree.sh:105`,
`printf '%s\n' "$dest"`) — the same contract as the plugin's hook. Installing the plugin gives Claude
Code **two `WorktreeCreate` hooks each claiming to create the worktree and each returning a path**,
with different naming conventions (`wt-${name}-${stamp}` in `~/.cache/claude-worktrees` vs `${name}` at
the `worktree-path` template). We have **no** `WorktreeRemove` registered, so the plugin would be the
sole provider of that half.

**This must be resolved as part of the same change, not after.** The falsification section of the
cutover evidence doc correctly established that *removing* the hook fixes 0% of the sprawl — that
finding stands. But *keeping it alongside the plugin* is a new, different failure. Retire
`create-worktree.sh` **at the moment the plugin is installed**, or not at all.

---

# Q3 — The closest documented pattern, and what it costs

## The pattern: **"Local CI"** from [merge.md](https://worktrunk.dev/merge/#local-ci)

> "For personal projects, pre-merge hooks open up the possibility of a workflow with much faster
> iteration — an order of magnitude more small changes instead of fewer large ones.
>
> Historically, ensuring tests ran before merging was difficult to enforce locally. Remote CI was
> valuable for the process as much as the checks: **it guaranteed validation happened.** `wt merge`
> brings that guarantee local.
>
> The full workflow: **start an agent (one of many) on a task, work elsewhere, return when it's ready.
> Review the diff, run `wt merge`, move on.** Pre-merge hooks validate before merging — if they pass,
> the branch goes to the default branch and the worktree cleans up.
>
> ```toml
> [[pre-merge]]
> test = "cargo test"
> lint = "cargo clippy"
> ```"

Named, quoted, and it is *literally addressed to us*: solo, personal project, many parallel agents, no
PR review, no second human, trunk-based. Paired with
[**Progressive validation**](https://worktrunk.dev/tips-patterns/#progressive-validation) —
*"`pre-commit` runs during `wt merge`, before the squash commit; `pre-merge` runs once per merge after
the rebase, so it's the right place for the slow tests"* — and the **parallel sub-Agent** recipe from
Q1a, that is the whole adoption.

## What adopting it as-documented costs us

1. **We stop using `Agent(isolation: "worktree")`.** Upstream's explicit instruction. Every dispatch
   becomes: parent runs `wt switch --create <branch> --no-cd --no-hooks`, then dispatches `Agent`
   *without* isolation, naming the absolute path in the prompt. That moves branch naming from the
   harness to us — which is the point, and which fixes the orphan-branch half of our 81 branches.
   Cost: the dispatching agent must be disciplined about the path, and our `dispatch-subagent-build`
   skill has to carry it. This is a real regression in enforcement (the harness no longer forces
   isolation) traded for a real gain in naming and lifecycle.
2. **`wt merge` squashes.** Discussed under Q4/collisions below.
3. **Review-the-diff is a step we do not currently have.** The documented loop is *"Review the diff,
   run `wt merge`"*. Our loop is architect-accepts-then-lands. These are compatible; the point is that
   upstream does not imagine an unattended `wt merge`, and our `[[pre-merge]]` gate is what substitutes
   for the human. Say so explicitly in the ruling.
4. **Project hooks need approval.** [hook.md#security](https://worktrunk.dev/hook/#security): approvals
   land in `~/.config/worktrunk/approvals.toml`, *"If a command changes, new approval is required."*
   The skill is emphatic that an agent must not `--yes` past this: *"**When invoked as an agent, stop
   and escalate to the user.** … Do not reach for either `--yes` on the user's behalf."* So every edit
   to our `[[pre-merge]]` body requires David to re-approve interactively, or `--yes` in the runner.
   That is a per-change human touch we do not have today.

## Our three candidate deviations, adjudicated

### 1. `[commit] stage = "tracked"` — **KEEP, but understand what it converts the problem into**

Not covered by a default; the documented default is `all`
([config.md#commit](https://worktrunk.dev/config/), `stage = "all"`).

**Source:** `src/config/user/sections.rs:40-46` — `StageMode::All => ["add", "-A"]`,
`Tracked => ["add", "-u"]`, `None => nothing`. Q4(a) confirmed exactly.

**Measured, `stage = "tracked"`:** the stray `capture.png` was not staged, did not land, and stayed on
disk. **But then the post-merge removal refused:**

```
✓ Merged to main (1 commit, 1 file, +1)
✗ Cannot remove worktree after merge: st3 has uncommitted changes
  ?? capture.png
FIRST-RUN EXIT=1
```

### ⛔ FINDING 4 — `wt merge` exits 1 when the merge SUCCEEDED and only the removal failed

Measured above: `main` advanced to `fd4aad1`, and `wt merge` returned **1**. Any automation shaped like
`wt merge && record_provenance` **silently skips provenance for a successful landing**. This is the
`[CXC-VISION-EPISTEMICS]` `rule-negative-control` failure in its exit-code form. Our merge receipt must
be driven from a `post-merge` hook (which does fire — see Q2 below) or from `--format=json`, never from
the exit status.

So `stage = "tracked"` is the right call, and its consequence is that **generated-noise worktrees stop
being swept into commits and start being retained instead**. That is strictly safer and strictly worse
for sprawl. It must be paired with fixing the generated-PNG noise (`captures/*.png`, rebuilt by the
pre-commit hook by construction) — otherwise every build worktree survives forever. The cutover
evidence doc flagged this as "open and now load-bearing"; it is now **measured and confirmed
load-bearing**.

### 2. `[[pre-merge]]` running our suite plus a QA verdict file — **KEEP; it is the documented pattern verbatim**

This is not a deviation at all. `[[pre-merge]]` reading a committed file is fully supported — see Q5
for both arms. Two additions the plan is missing:

- **`--no-hooks` is a total bypass.** Measured: gate `exit 7` → `wt merge` exit **7**, `main` unmoved;
  `wt merge --no-hooks` → exit **0**, `main` advanced, gate never ran. Documented at
  [hook.md](https://worktrunk.dev/hook/#security) (*"Use `--no-hooks` to skip hooks"*). This is the
  successor to `--no-verify`, and it is a flag, not an env var, so it is at least visible in a
  transcript. `[DEC-LANDING-GATE-MUST-BE-BASELINE-RELATIVE]` should name it.
- **The gate runs AFTER the squash.** See Finding 6.

### 3. `git worktree lock` on the primary and deployment checkouts — **KEEP, but it is only HALF the protection you think**

**Docs** ([faq#what-can-worktrunk-delete](https://worktrunk.dev/faq/#what-can-worktrunk-delete)):

> "To protect a worktree from removal entirely (say it holds a local database), lock it … Locked
> worktrees show `⊞` in `wt list`. **Neither `git worktree remove` nor `wt remove` (even with
> `--force`) will delete them.**"

**Measured — removal arm (works):**

```
$ git worktree lock --reason "deployment checkout: 11 live processes" …/r6.locked
$ wt remove locked --yes --foreground
✗ Cannot remove locked, worktree is locked (deployment checkout: 11 live processes)
EXIT=1
```

**Measured — content arm (does NOT work):**

```
$ git worktree lock --reason "live: 11 processes read scenes/*.yaml" …/r7-deploy
$ cat …/r7-deploy/scenes/live.yaml        →  v1
$ wt merge --yes -C …/r7.feat              →  ✓ Merged to main
$ cat …/r7-deploy/scenes/live.yaml        →  v2 from branch      ⛔ REWRITTEN UNDER THE LOCK
```

The lock stops deletion. It does **not** stop `wt merge` from checking the merged commits out into the
locked worktree. `[DEC-DEPLOYMENT-CHECKOUT-NOT-THE-DEV-TREE]` must say both halves.

`wt step prune` also honours the lock — [step.md](https://worktrunk.dev/step/#wt-step-prune):
*"Locked worktrees and the main worktree are always skipped."* Locking is still worth doing; it is just
not sufficient.

## The deviation you are MISSING

### ⛔ FINDING 5 — forbid worktrees inside the repo tree, or `--stage=all` will land a gitlink on main

We have one linked worktree inside the repo today (`wt-ctl`). Measured:

```
$ git -C …/r12.ctlbranch status --porcelain
 M a
?? wt-ctl/
$ wt merge --yes
▲ Auto-staging 1 untracked path:
  wt-ctl/
✓ Merged to main (1 commit, 2 files, +2)
$ git cat-file -p HEAD^{tree}
100644 blob 58f424c…    a
160000 commit 40664c3…  wt-ctl        ⛔ a GITLINK on main
```

`git add -A` on a directory that is itself a worktree records a **`160000` submodule gitlink** pointing
at a commit with no `.gitmodules` entry. That lands on trunk and breaks every fresh clone. `stage =
"tracked"` prevents *this* instance, but the right fix is the invariant: **no worktree inside the repo
tree.** The default `worktree-path` template already gives us this for free —
[config.md](https://worktrunk.dev/config/): *"Default — sibling directory:
`worktree-path = "{{ repo_path }}/../{{ repo }}.{{ branch | sanitize }}"`."* Adopt the default; do not
configure `.worktrees/`.

---

# Q4 — The things that would break us

## (a) `--stage=all` runs `git add -A` — **CONFIRMED, from source and measurement**

`src/config/user/sections.rs:42`: `StageMode::All => Some(&["add", "-A"])`. Default per
[merge.md](https://worktrunk.dev/merge/) *"`--stage <STAGE>` … [default: all]"*.

**Mitigation the cutover doc does not record:** it warns, per file, on stderr, before doing it.
`src/commands/repository_ext.rs:622-639`, `warn_about_untracked_files`, is unconditional and lists
every path:

```
▲ Auto-staging 1 untracked path:
  untracked-note.md
```

It warns and proceeds — no prompt, no cap observed. So the sweep is loud but not blocking.

## (b) Gitignored files invisible to every safety check and absent from `refs/wt-backup/` — **CONFIRMED, all three legs**

Measured in one run. A hand-rolled worktree with a modified tracked file, an untracked file, a
gitignored file, and a gitignored directory:

```
$ git status --porcelain              $ git status --porcelain --ignored
 M tracked.txt                         M tracked.txt
?? untracked-note.md                   ?? untracked-note.md
                                       !! secret.env
                                       !! session_handover/
$ wt merge --yes
▲ Auto-staging 1 untracked path:
  untracked-note.md                    ← secret.env and session_handover/ never mentioned
↳ Backup created @ c691068
✓ Merged to main
$ git ls-tree -r refs/wt-backup/handrolled
.gitignore
tracked.txt
untracked-note.md                      ← the ignored paths are NOT in the backup
$ ls /tmp/lab-handrolled-wt
ls: No such file or directory          ← ~2 s later, gone
```

`refs/wt-backup/<branch>` is a `git stash --include-untracked`-shaped commit (its own subject is
`On handrolled: --include-untracked`). Ignored paths are structurally excluded.

**Correction to the cutover doc:** it treats `.git/wt/trash/` as a safety net with 24-hour retention.
It is not. [remove.md#background-removal](https://worktrunk.dev/remove/#background-removal): *"The
worktree is renamed into `.git/wt/trash/` (instant same-filesystem rename) … and a **detached `rm -rf`
finishes cleanup**. … After each `wt remove`, entries in `.git/wt/trash/` older than 24 hours are
swept — eventual cleanup for **directories orphaned when a previous background removal was
interrupted**."* The 24 hours is for *leftovers of failed removals*, not a recovery window. Measured:
the trash was empty within 2 seconds of a normal removal, and empty immediately after a `--foreground`
one.

**And it is worse than "merge destroys them" — `wt step prune` destroys them silently too.** Both arms:

```
$ git -C …/r9.landedign status --porcelain    →  (empty — looks clean)
$ wt list
+ unlanded       ↑   ↑1  +1     ← has unlanded commits
+ landedign      _              ← integrated, "clean"
$ wt step prune --min-age=0s --foreground --yes
◎ Removing landedign worktree...
✓ Removed landedign worktree & branch (same commit as main, _) (4 files · 214 B)
✓ Pruned 1 worktree & branch
EXIT=0
$ ls …/r9.landedign/corpora-local/decision.lts
ls: No such file or directory                 ⛔ the only copy, gone, exit 0, no warning
$ git branch
* main
+ unlanded                                    ← negative arm: correctly SPARED
```

Positive control and negative control both shown. **`.lts` corpora must not live gitignored inside a
worktree under any circumstance.** The plan's move to `~/.claude/factlattice_corpora/` is correct and
necessary; the alternative under consideration ("written in place by agents, checked in by a hook") is
disqualified by this measurement.

For contrast, the *untracked-but-not-ignored* arm behaves perfectly:

```
$ wt remove dirty --yes --foreground
✗ Cannot remove worktree: dirty has uncommitted changes
  ?? wip.txt
↳ Commit or stash changes first, or to lose uncommitted changes, run wt remove --force dirty
EXIT=1
```

The asymmetry is the whole finding: **worktrunk's safety net is `git status --porcelain`, and
gitignored content is by definition outside it.**

## (c) Issue #3769 — **OPEN and reproducing on v0.74.0, with an important refinement**

[Issue #3769](https://github.com/max-sixty/worktrunk/issues/3769) is **OPEN** as of today. The proposed
fix, [PR #3770](https://github.com/max-sixty/worktrunk/pull/3770) — *"fix(remove): refuse to strand a
detached worktree when removing by branch"* — was **CLOSED WITHOUT MERGE on 2026-08-09**. The
reproduction test named in the bot comment (`test_remove_branch_with_detached_worktree_refuses`) is not
present at upstream `main`.

**Arm 1 — exact repro (branch content-landed), v0.74.0:**

```
$ wt remove feature --yes --foreground
○ No worktree found for branch feature
✓ Removed branch feature (same commit as main, _)
REMOVE_EXIT=0
$ git worktree list
…/r4.feature  4e1aadc (detached HEAD)        ⛔ stranded, and the command said ✓
```

**Arm 2 — the refinement the cutover doc does not have: an UNMERGED branch is protected.**

```
$ wt remove feature --yes --foreground        # branch holds a commit not on main
○ No worktree found for branch feature
○ Branch feature retained; has unmerged changes
↳ To delete the unmerged branch, run wt remove -D feature
REMOVE_EXIT=0
$ git branch
  feature                                     ← branch SURVIVED
* main
```

**Arm 3 — control: by path, it works correctly:**

```
$ wt remove …/r4.feature --yes --foreground
✓ Removed worktree @ …/r4.feature (detached HEAD, no branch to delete)
```

So #3769 is a **stranding** bug, not a data-loss bug — unless `-D` is passed. The cutover doc's
"address by PATH, never branch name" rule is correct and should stay; the severity framing should be
corrected. **We have 4 detached worktrees right now, so this is live exposure, not theoretical.**

## (d) `[[pre-merge]]` and `GIT_DIR` / `GIT_INDEX_FILE` — **worktrunk scrubs them. Our scrubbing is harmless, not load-bearing, for hooks — but IS load-bearing for `wt` itself.**

**Source is explicit.** `src/shell_exec.rs:594-641`, `scrub_git_discovery_env_vars`, doc comment:

> "Scrub the git-discovery path vars … from a child `Command`, so the spawned process discovers its
> repository from its working directory rather than a `GIT_DIR`/`GIT_WORK_TREE` that `wt` inherited.
> … **`wt` relocated a user command into a worktree it selected** — **hooks (run in the operation's
> worktree)**, `wt step for-each` … the cwd carries `wt`'s intent, so the inherited context is
> scrubbed … **git itself exports discovery vars (e.g. `GIT_INDEX_FILE`) to the hooks it spawns.**
> Forwarding those into a relocated command misdirects every `git` call in it … See issue #3373."

The scrub list is `INHERITED_GIT_PATH_VARS` = `GIT_DIR, GIT_WORK_TREE, GIT_COMMON_DIR, GIT_INDEX_FILE,
GIT_OBJECT_DIRECTORY` (`src/shell_exec.rs:239-245`). Hooks are wired to it at
`src/commands/command_executor.rs:543`: `let scrub_git_discovery = fg_step.announce.is_hook();`

**Measured, with the parent env deliberately poisoned:**

```
$ export GIT_DIR=…/r2/.git GIT_INDEX_FILE=…/r2/.git/index GIT_WORK_TREE=…/r2
$ wt merge --yes            # from …/r2.arm-negative
--- pre-merge hook env probe ---
cwd=…/r2.arm-negative
GIT_DIR=[<unset>]  GIT_INDEX_FILE=[<unset>]  GIT_WORK_TREE=[<unset>]  GIT_COMMON_DIR=[<unset>]
git rev-parse --show-toplevel = …/r2.arm-negative
```

All four scrubbed. **Answer: our `_clean_env()` / `pre-commit-tests.sh:190-193` scrubbing is redundant
inside a `[[pre-merge]]` hook. Harmless — keep it, it costs nothing and protects the git-hook path
which still exists — but it is not what makes the gate correct.**

### ⛔ FINDING 6 (adjacent, and worse) — `wt` itself HONOURS an inherited `GIT_WORK_TREE`, and will operate on the wrong worktree

The same source comment states this deliberately: *"**`wt`'s own git plumbing** ([`Cmd`] via
`Repository::run_command`) **keeps the inherited context on purpose** (relative values absolutized, see
issue #1914): `wt` honoring the context it was handed is the point of running `wt` under `git`."*

**Measured.** Same poisoned env, `wt merge` invoked from inside `…/r2.arm-negative`:

```
◎ Running pre-merge project:qa-verdict @ …/r2         ← it resolved to the PRIMARY, not the branch
git rev-parse HEAD = cc8f8de                          ← main's tip, not the branch's 01bf3cc
```

`wt` operated on `main`'s worktree while being invoked from a feature worktree, purely because
`GIT_WORK_TREE` was exported in the parent. **Any `wt` invocation made from inside a git hook, or from
any process that exported `GIT_DIR`/`GIT_WORK_TREE`, targets the wrong worktree.** Our `.githooks/`
tree and `scripts/pre-commit-tests.sh` are exactly such contexts.

**Recommendation:** any wrapper that shells out to `wt` must scrub `INHERITED_GIT_PATH_VARS` *before*
the call. This inverts the current rule — we scrub for git, and we must now also scrub for `wt`, for
the opposite reason.

## (e) The ~11 live processes — **the docs state the hazard precisely, and it reproduces**

[faq#what-can-worktrunk-delete → Other cleanup](https://worktrunk.dev/faq/#what-can-worktrunk-delete):

> "`wt merge` / `wt step push` — **the target branch's checked-out worktree is updated to the merged
> commits**, so a file those commits delete disappears from it, and **an ignored file at a path they
> track is overwritten** — the same result a `git merge` run in that worktree would produce.
> **Uncommitted changes at paths the merge doesn't touch stay in place, staged or not; one at a path it
> does touch refuses the merge upfront, naming the file.**"

**Measured — content is rewritten (new inode):**

```
BEFORE: scene: v1                        inode=224650402
$ wt merge --yes -C …/r6.f1              ✓ Merged to main
AFTER:  scene: v2 CHANGED BY THE BRANCH  inode=224650496
```

**Measured — negative arm, it refuses on a conflicting uncommitted change:**

```
$ git status --porcelain      →   M scenes/live.yaml
$ wt merge --yes -C …/r6.f2
✗ Can't push to local main branch: conflicting uncommitted changes
  scenes/live.yaml
↳ Commit or stash these changes in …/r6 first
```

So: **every `wt merge` that touches a scene YAML rewrites that file under the running processes**, with
a fresh inode — a process holding an open fd keeps the old content; a process that re-reads by path
gets the new. This is not a `wt` defect; it is what `git merge` in that worktree would do. But it means
the cutover doc's framing is right and should be sharpened: **`wt merge` into `main` is a deployment
event for `~/worktrees/composeio-main`**, and `git worktree lock` does not change that (Finding, Q3.3).

Also relevant: `wt remove --reap` exists ([remove.md#reaping-processes](https://worktrunk.dev/remove/#reaping-processes),
marked experimental) and would `SIGTERM`/`SIGKILL` processes whose cwd is under a removed worktree —
but *"Interactive processes are spared"* and *"Discovery is by working directory only"*, and it applies
to removal, not merge. Do not enable `--reap` anywhere near the deployment checkout.

---

# Q5 — The two non-git problems

## Does worktrunk interfere with untracked / gitignored paths in the tree?

**Yes, decisively, and in the direction that hurts.** Summarised from the measurements above:

| path class | seen by `wt merge`'s stage? | seen by `wt remove`'s safety check? | in `refs/wt-backup/`? | survives worktree removal? |
|---|---|---|---|---|
| tracked, modified | yes (`add -u` / `add -A`) | yes — refuses | yes | in the commit |
| untracked, not ignored | `all` yes (warns); `tracked` no | **yes — refuses, names the file** | yes | yes if `stage=tracked` (worktree retained) |
| **gitignored** | **no** | ⛔ **no — invisible** | ⛔ **no** | ⛔ **no — deleted, exit 0, silently** |

There is one documented mechanism aimed at this, and it is a *copy*, not a protection:
[`wt step copy-ignored`](https://worktrunk.dev/step/#wt-step-copy-ignored), configured as
`[post-start] copy = "wt step copy-ignored"`, with a `.worktreeinclude` allowlist —
[tips-patterns#eliminate-cold-starts](https://worktrunk.dev/tips-patterns/#eliminate-cold-starts):
*"All gitignored files are copied by default. To limit what gets copied, create `.worktreeinclude` with
patterns — files must be both gitignored and listed."*

That would fix the `.mcp.json` / 20-skipped-tests problem (`python/tests/conftest.py:1-37`) cleanly and
as-documented. **It does not protect anything** — it makes more copies, which for a
single-source-of-truth corpus is the disease, not the cure.

**The docs say nothing about partially-tracked repositories.** No pattern, no caveat, no
recommendation. That is a genuine gap, and the honest answer is: worktrunk assumes gitignored content
is regenerable. Our `.lts` corpus is not. The move to `~/.claude/factlattice_corpora/` is therefore not
merely convenient — it is the only configuration in which worktrunk's safety model is sound for us.

## Can a `[[pre-merge]]` hook read a file and fail on it?

**Yes. Both arms, measured.**

Project config `.config/wt.toml`:

```toml
[[pre-merge]]
qa-verdict = """
test -f qa/verdict.json || { echo "REFUSE: no qa/verdict.json committed on this branch"; exit 1; }
v=$(python3 -c 'import json;print(json.load(open("qa/verdict.json"))["verdict"])')
[ "$v" = "PASS" ] || { echo "REFUSE: verdict is $v"; exit 3; }
"""
```

**Negative arm — no verdict file:**

```
--- verdict gate ---
REFUSE: no qa/verdict.json committed on this branch
✗ pre-merge command failed: qa-verdict: exit status: 1
↳ To skip pre-merge hooks, re-run with --no-hooks
MERGE_EXIT=1
$ git log --oneline -3 main
cc8f8de init                    ← main UNMOVED
$ git worktree list
…/r2.arm-negative  01bf3cc [arm-negative]    ← worktree and branch SURVIVE
```

**Positive arm — `{"verdict":"PASS"}` committed on the branch:**

```
--- verdict gate ---
verdict read from file = PASS
ACCEPT
✓ Merged to main (1 commit, 2 files, +2)
◎ Removing arm-negative worktree & branch in background
$ git log --oneline main
9f8f23f chore: squashed by lab                ← and qa/verdict.json is IN that commit
```

Answering the role's integration question 4 precisely:

- **Working directory:** the worktree being merged (verified `pwd` and `git rev-parse --show-toplevel`).
- **Environment:** `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_COMMON_DIR` scrubbed (Q4d). All
  template variables are also delivered as JSON on stdin —
  [hook.md#json-context](https://worktrunk.dev/hook/#json-context).
- **What constitutes failure:** non-zero exit. The code propagates — a gate exiting 7 made `wt merge`
  exit 7.
- **Does failure abort cleanly:** yes for the merge itself. **With one caveat — see Finding 7.**
- **post-merge / pre-remove hooks:** both exist. Documented order
  ([merge.md#pipeline](https://worktrunk.dev/merge/#pipeline)): commit → squash → rebase → **pre-merge**
  → merge → **pre-remove** → cleanup → **post-remove + post-merge**.

### ⭐ FINDING 7 — a REFUSED merge is not a no-op: the squash has already happened

Because `pre-merge` runs at step 4, after squash (2) and rebase (3), a refusal leaves the branch
**already rewritten**. Measured — a branch with one commit `feat: work with NO qa verdict`, after two
refused merges:

```
$ git log --oneline arm-negative
42cb43d chore: squashed by lab      ← the original commit message is GONE
cc8f8de init
$ git for-each-ref refs/wt-backup
refs/wt-backup/arm-negative 1ce9cf8…
```

Recoverable via `refs/wt-backup/` and the reflog, but the per-commit history is destroyed by a *failed*
landing. Anyone writing "the gate refuses and nothing changes" into a ruling would be wrong.

### ⭐ FINDING 8 — the durable provenance anchor is `post-merge`, and the JSON receipt does NOT carry the SHA

This answers the role's integration question 2 directly. `wt merge --format=json` emits:

```json
{ "branch": "prov", "committed": false, "rebased": false, "removed": true, "squashed": false, "target": "main" }
```

**No SHA.** So the receipt alone cannot anchor provenance.

But a `post-merge` hook can, and it runs in the target's worktree after cleanup
([hook.md](https://worktrunk.dev/hook/): *"`post-merge` … Runs in the target branch worktree if it
exists, otherwise the primary worktree"*). Measured:

```toml
[post-merge]
provenance = """
{ echo "branch={{ branch }} target={{ target }} commit={{ commit }}"
  echo "trunk tip now = $(git rev-parse {{ target }})"
} >> "{{ primary_worktree_path }}/PROVENANCE.log"
"""
```

```
branch=prov2 target=main commit=c5ac3ae9f8ff755d2d89d0323c49a635a1b0ee32 short=c5ac3ae
trunk tip now = c5ac3ae9f8ff755d2d89d0323c49a635a1b0ee32
trunk subject  = feat: thing two
$ git log --oneline -2 main
c5ac3ae feat: thing two             ← identical. {{ commit }} IS the trunk-side SHA.
```

Under the default fast-forward merge, `{{ commit }}` **equals the trunk tip**, because the
fast-forward makes them the same object. So:

- **`{{ commit }}` in a `post-merge` hook is a durable, permanently-reachable, trunk-side SHA.** That is
  the honest replacement `[DEC-GAP-ANALYZER-GETS-BASH-AND-RUNS-GIT-ITSELF]` needs, and it is strictly
  better than the branch-side SHA that ruling assumed.
- ⚠ **Two caveats.** With `--no-ff` the trunk tip is a *merge commit* and `{{ commit }}` is not it —
  use `$(git rev-parse {{ target }})` instead, which is correct under both. And `post-merge` runs
  **in the background**, so a failure there is logged, not raised: `wt config state logs get
  --hook=project:post-merge:provenance`. A provenance write that fails silently is exactly the loss
  class we are closing, so it needs its own check.
- ⭐ **The verdict file lands on trunk as part of the squash** (measured: `qa/verdict.json` is in
  `9f8f23f`). So the QA verdict becomes permanent, addressable, trunk-side evidence — which is the
  cleanest fix available for "subagent findings evaporate as chat text."

**Where in the pipeline provenance gets written: `post-merge`, not the builder, not the architect.**

---

# Corrections to `CUTOVER-EVIDENCE-worktrunk-sweeps-2026-08-17.md`

| doc says | correction | evidence |
|---|---|---|
| "Worktrees are addressed by PATH, never branch name (#3769: … deletes the branch …)" | Rule is right; severity is overstated. An **unmerged** branch is protected — `○ Branch feature retained; has unmerged changes`. Only content-landed branches are deleted. It is a **stranding** bug. | Lab arms 1–3, v0.74.0 |
| Sweep 4 item 6: "Gitignored content destroyed by `wt merge`" | True, and understated: **`wt step prune` destroys it too, exit 0, no warning**, and `.git/wt/trash/` is **not** a 24-hour recovery window — it is emptied within seconds. | Lab; [remove.md](https://worktrunk.dev/remove/#background-removal) |
| "trash safety net available? **yes**" | ⛔ **No.** The 24-hour sweep is for directories orphaned by *interrupted* removals. A normal removal `rm -rf`s immediately. | Docs + lab |
| "Could not determine: whether `wt`'s `[[pre-merge]]` inherits `GIT_DIR`/`GIT_INDEX_FILE`" | **Determined: it does not — worktrunk scrubs all five vars for hooks.** Our scrubbing is harmless-redundant there. But `wt` **itself** honours them and will target the wrong worktree. | `src/shell_exec.rs:594-641`, `src/commands/command_executor.rs:543`; lab |
| implicit: `[[pre-merge]]` gate failure is a no-op | ⛔ The squash has already run. A refused merge leaves the branch squashed. | Lab, Finding 7 |
| implicit: `wt merge` exit status is the landing verdict | ⛔ Exit 1 with a **successful** merge when only the removal refused. | Lab, Finding 4 |
| the to-be state routes `Agent(isolation: worktree)` through the plugin | ⛔ Upstream's own skill says **do not** — pre-create instead. | `skills/worktrunk/SKILL.md:161-176` |
| `~/.claude/settings.json` "register a remove hook, or delete create and let `wt` own it" | Sharpen: installing the plugin while `create-worktree.sh` stays registered gives **two competing `WorktreeCreate` hooks**. Must be simultaneous. | `~/.claude/settings.json`; plugin `hooks.json` |
| nothing about relocating the 29 strays | `wt step relocate` moves off-template worktrees onto the template path, preserving dirty content, with `--dry-run`. Add it to the plan. | Lab; [step.md](https://worktrunk.dev/step/#wt-step-relocate) |
| Sweep 4: `wt-ctl/` "would be swept at `git add -A`" | Confirmed, and worse: it lands as a **`160000` gitlink** on `main`. | Lab, Finding 5 |

---

# Collisions with ratified decisions

| id | collision | recommendation |
|---|---|---|
| `[DEC-DISPATCHED-BUILDS-COMMIT-TO-BRANCH]` — *"never by squash (loses per-defect history + enforcing-test provenance for bisect)"* | `wt merge` squashes by default. **But `--no-squash` / `[merge] squash = false` genuinely preserves per-commit history on trunk** — measured: `step 1: broken intermediate` and `step 2: fixed` both landed, linearly. So the ruling is *satisfiable*, not contradicted. | ⭐ **Supersede anyway, and say why.** The stated ground for the ban is bisect. Under `--no-squash` our *builder's intermediate, non-building commits land on `main`* — `git bisect` then lands on commits that never passed a gate, which is the failure the ruling was trying to prevent. Under squash, every commit on `main` is a complete, `[[pre-merge]]`-gated unit — which is what bisect actually needs. **Recommend squash.** And note the ruling's own premise was abandoned when the project adopted `merge-tree --write-tree`, *"insensitive to provenance."* |
| `[PRINCIPLE-WORKTREE-ISOLATION]` — *"`git-expert` lands it — assert base-ancestry, then cherry-pick"* | The mechanism is replaced. | Amend `principle:` to *"…and `wt merge` lands it."* Everything else on the node (`boundary:`, `cost:`, `operating-rule:`, `generalization:`) is **still true and reinforced** by Finding 6 — an isolation mechanism that isolates exactly one axis is precisely what `GIT_WORK_TREE` defeats. |
| `[DEC-DEPLOYMENT-CHECKOUT-NOT-THE-DEV-TREE]` | Must state the exemption **and** that `git worktree lock` protects it from removal only, not from `wt merge` rewriting its files. | Amend with both halves + the FAQ quote. |
| `[LEC-CORPUS-SEAT]` / `[PCB-CORPUS-PLACEMENT]` | Untouched by worktrunk itself, but Q5's measurement — prune silently destroying gitignored corpus content — is decisive **for** the move and **against** any "gitignored `.lts` in the tree" variant. | Cite the prune measurement in the amendment as the falsifier. |
| `[DEC-LANDING-GATE-MUST-BE-BASELINE-RELATIVE]` (`proposed`, 2026-08-11) | Still the closest thing to a `[[pre-merge]]` spec. Add: the escape hatch is now `--no-hooks` (a flag, visible), and the gate runs *after* the squash. | ⭐ Ratify into the hook spec. |
| `[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST]` | The instrument half dies (all three named instruments take a live branch ref). | Replace with the `post-merge` receipt (Finding 8). Keep the `generalisation:` verbatim. |
| `[DEC-GAP-ANALYZER-GETS-BASH-AND-RUNS-GIT-ITSELF]` (David personally overruled the architect) | Both load-bearing premises assumed a branch-side SHA. | ⭐ **Answered by Finding 8**: `$(git rev-parse {{ target }})` in `post-merge` is a trunk SHA — still a SHA, permanently reachable, strictly better than what the ruling assumed. Record it as a *strengthening*, not a loss. |
| `[DEC-COMPOSEIO-IS-TRUNK-BASED]` `do-not-prune-rule` count | 23 → **42**. | Update. |

---

# Recommended changelist, in dependency order

1. **Move `.lts` corpora out of every worktree** — prerequisite for everything else. Q5's prune
   measurement is the falsifier that justifies it.
2. **Fix the generated-noise class** (`captures/*.png` rebuilt by construction) — otherwise
   `stage = "tracked"` converts sweep-into-commit into worktree-retained-forever.
3. **`wt step relocate`** the 29 strays onto the template path (`--dry-run` first).
4. **Adopt the default sibling `worktree-path`**; forbid worktrees inside the repo tree; deal with
   `wt-ctl` before any `--stage=all` runs.
5. **Retire `~/.claude/hooks/create-worktree.sh` in the same change that installs the plugin.**
6. **Switch dispatch to the documented parallel-sub-Agent pattern**: `wt switch --create <branch>
   --no-cd --no-hooks`, then `Agent` **without** `isolation: "worktree"`, path in the prompt.
7. **Project config** `.config/wt.toml`: `[[pre-commit]]` fast checks, `[[pre-merge]]` suite + verdict
   gate, `[post-merge]` provenance receipt. User config: `[commit] stage = "tracked"`.
8. **`git worktree lock`** the deployment checkout — knowing it stops removal only.
9. **Scrub `GIT_DIR`/`GIT_WORK_TREE`/`GIT_INDEX_FILE`/`GIT_COMMON_DIR`/`GIT_OBJECT_DIRECTORY` before
   any programmatic `wt` invocation** (Finding 6).
10. **Never gate automation on `wt merge`'s exit status** (Finding 4).

## Upstream reports worth filing

- **`--no-cd` / `[switch] cd = false` makes `-x` execute in the invoking directory** while printing the
  worktree path. Repro in Finding 2; root cause at `src/output/global.rs:394-399` + `:486` + `:502`.
  Appears unreported. Highest value of the three.
- **#3769 is still open** and PR #3770 was closed unmerged; a comment noting we hit it with 4 detached
  worktrees may help it get re-prioritised.
- **`wt merge` exit 1 on a successful merge with a failed removal** — arguably intended, but worth a
  question, since it silently breaks `wt merge && <record>`.

---

# ⛔ What I could not determine

1. **Whether the composeio test suite is green enough for a `[[pre-merge]]` gate.** I did not run it —
   the sweep's `~2 timing failures on a pristine checkout` figure is inferred, not measured, and I did
   not re-measure. A gate over a red suite is `--no-hooks` waiting to happen.
2. **What Claude Code actually does when two `WorktreeCreate` hooks are registered** — runs both? takes
   the first path? the last? errors? I read both hook definitions but did not test the harness
   behaviour, and Claude Code's docs are not authoritative here. **Experiment that would settle it:**
   register a second no-op `WorktreeCreate` hook that logs and emits a bogus path in a scratch repo,
   fire one `isolation: worktree` dispatch, read `hook-fires.log` and `git worktree list`.
3. **Whether `wt config plugins claude install` pins a marketplace SHA at install time or floats.**
   `plugins/worktrunk/CLAUDE.md` says *"installs pin the marketplace git SHA"*, but I did not verify
   what `claude plugin update` then does. **Experiment:** install in a scratch `CLAUDE_CONFIG_DIR`,
   record the pinned SHA, run `claude plugin update`, diff.
4. **Whether `wt`'s `.git/wt/cache` + `.git/wt/logs` write volume is material against 1.3 GiB of `.git`
   inside Dropbox.** The FAQ enumerates ~10 cache/log locations written by `wt list`, `wt merge`,
   `wt remove` and hooks. I observed dozens of small JSON files per command in the lab but did not
   measure rate or size at our scale. **Experiment:** `wt list` in a loop for a minute against a clone,
   count files and bytes under `.git/wt/`.
5. **What happens to the ~11 live processes' open file descriptors specifically.** I measured that the
   file is replaced with a new inode; I did not measure whether any of our consumers re-read by path,
   hold an fd, or use a watcher. That is a composeio question, not a worktrunk one, and it decides
   whether the cutover is a scheduled outage or a non-event.
6. **`wt merge` behaviour under a named gate** (the `spec/bus-protocol-corrections-2026-08-14`
   standing exception). I found no documented mechanism for "refuse to land this branch." The nearest
   is a `[[pre-merge]]` hook that reads `{{ branch }}` and refuses by name — untested.
7. **Whether `--reap` would touch anything under `~/worktrees/composeio-main`.** Documented as
   working-directory-based with interactive processes spared; I did not test it at all, and I
   recommend not testing it anywhere near live hardware.
</content>
</invoke>
