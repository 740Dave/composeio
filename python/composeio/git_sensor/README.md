# git_sensor — the `git/*` sync-state SENSOR

A SENSOR peripheral ([SPEC:CXC-HAL-DECLARE] / [CXC-HALD-ROLE]) that instruments a
git repository's sync state onto the ComposeBus. A repo instruments ITSELF the
way a bme280 publishes `env/temperature` or the `self_sensor` publishes `self/*`:
it surfaces forgotten / uncommitted / unpushed work **visually** (a mermaid
gitGraph) and **as a bus alert** (`git/alert`).

This is a "instrument the seam" slice: it COMPUTES + EXPOSES the publish set
(`to_bus_lines()`); like `self_sensor` it does not open a live bus/MQTT link.
Only `git` (via subprocess) + the stdlib are used — no third-party deps.

## Run

```
python3 -m composeio.git_sensor                # instrument the CWD repo
python3 -m composeio.git_sensor /path/to/repo
python3 -m composeio.git_sensor --selftest     # hermetic temp-repo test
```

## The `git/*` namespaces

| namespace          | value                                                            |
| ------------------ | ---------------------------------------------------------------- |
| `git/branch`       | current branch name (short hash if detached)                     |
| `git/detached`     | bool — HEAD is detached                                          |
| `git/dirty`        | bool — tracked changes present (staged and/or modified)          |
| `git/uncommitted`  | int — tracked porcelain entries (staged + modified, not `??`)    |
| `git/staged`       | int — entries whose index column is a change (`MADRC`)           |
| `git/untracked`    | int — `??` porcelain entries                                     |
| `git/unpushed`     | int — commits ahead of upstream (0 when no upstream)             |
| `git/behind`       | int — commits behind upstream (0 when no upstream)               |
| `git/stashes`      | int — `git stash list` count                                     |
| `git/upstream`     | upstream ref name (e.g. `origin/main`) or `null`                 |
| `git/last_commit`  | `{hash, subject}` (dropped from the bus publish set — bulky)     |
| `git/alert`        | the structured out-of-sync ALERT (below)                         |
| `git/graph`        | a mermaid `gitGraph` source string for recent history           |

### `git/alert` schema — the bus alert the console/notifier consumes

```json
{
  "out_of_sync": true,
  "reasons": ["dirty", "unpushed", "stashes"],
  "message": "2 uncommitted · 1 unpushed · 1 stashed"
}
```

`out_of_sync` is `true` when ANY of: `dirty` (uncommitted > 0), `unpushed > 0`,
`behind > 0`, `detached`, or `stashes > 0`. `reasons` is an enumerable list of
machine tags (`dirty`, `unpushed`, `behind`, `detached`, `stashes`); `message`
is the human summary. A clean, fully-synced repo yields
`{out_of_sync: false, reasons: [], message: "in sync"}`.

## `git/graph` renders in the MermaidWidget

`git/graph` is a valid mermaid **`gitGraph`** source string. `gitGraph` is a
first-class diagram type in mermaid v11 (the widget vendors v11.16.0), so the
string renders **unchanged** in the already-built `MermaidWidget` — just bind a
mermaid widget to `git/graph`:

```
python/composeio/render/examples/git_graph.compose.yaml
```

The current branch's first-parent history is the trunk (`main` line); sibling
local branches whose merge-base falls inside the window are attached with
`branch`/`checkout`/`merge`-style lines. If the topology can't be mapped
cleanly, it degrades to a linear `commit` list — it never emits empty or invalid
syntax.
