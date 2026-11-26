# Quickstart — relaunch Claude in the launcher harness and connect the Console

> **What this gets you.** A Claude Code session running *inside* the launcher harness, so the
> Intelligence Console can see it, talk to it, and let it self-direct its own `/compact`.
> Verified against the live code on 2026-08-08 (`launcher.py`, `transport.sh`,
> `start-console-stack.sh`, `data_sources.py`).

---

## The mental model (why there are two halves)

There are **two independent processes** and they connect only through the bus and an env var:

| | what it is | how it learns which session is "the" session |
| --- | --- | --- |
| **The harness** (`transport.sh` → `launcher.py`) | a transparent PTY proxy that spawns `claude` and relays `/compact` + `inject` from the bus | you pass it the session id |
| **The Console** (`start-console-stack.sh` → `console.compose.yaml`) | the SDL window + its in-process data layer | **it resolves the pin ONCE, at console start** |

That last row is the whole trap. `data_sources.resolve_session_jsonl` runs **once**, when the
console process boots. A console that has been up for three days is still instrumenting whatever
session it resolved three days ago. Re-hosting a session under the launcher does **not** re-point a
running console.

> **⚠ Right now (2026-08-08):** the live console (pid started Aug 5 19:16) is pinned to
> `59f1b46e-…`, whose transcript last grew **Aug 6 03:50**. Its context gauge, thread, and subagent
> chips are frozen on a dead session. Step 4 below is what fixes it.

**So the order is: harness first, console bounce second.** The console must be bounced *from inside*
the newly-hosted session, because that is how it inherits `CLAUDE_CODE_SESSION_ID` /
`COMPOSEIO_SESSION_JSONL`.

---

## Step 0 — preflight (30 seconds)

```bash
cd ${COMPOSEIO_HOME}

# broker reachable?
python3 -c "import socket;s=socket.socket();s.settimeout(4);s.connect(('192.0.2.75',1883));print('broker OK')"

# console stack up? (idempotent — safe to run even if it already is)
intelligence/start-console-stack.sh
```

`start-console-stack.sh` is the **one canonical bringup** for console + Launch Bar ribbon +
`app_launcher`. Each leg starts only if absent, so re-running never doubles a surface. It is also
what the login LaunchAgent (`com.composeio.console-stack`) execs, so after a reboot the stack is
already up and this is a no-op.

`.mcp.json` (composeio-channel + lts-engine + decision-ops) already exists at both the repo root and
`~`, so `transport.sh` will not need to write one.

---

## Step 1 — get the session id and exit

A session **cannot be resumed while it is still active**. Exit first.

```
/exit
```

Claude Code prints the resume id on exit. If you missed it, read it off the transcript filename —
the newest `.jsonl` in the project dir for the cwd the session was started in:

```bash
ls -t ~/.claude/projects/-Users-you-composeio/*.jsonl | head -1
```

The basename minus `.jsonl` **is** the session id.

---

## Step 2 — re-host under the launcher

**Run this from the directory the session was originally started in.** `claude --resume` is
project-scoped by cwd; `transport.sh` deliberately does **not** `cd` anywhere.

For a session started in the composeio repo:

```bash
cd ${COMPOSEIO_HOME}
intelligence/launcher/transport.sh <session-id>
```

For a legacy home-project session, run it from `~` instead.

What happens, in order:

1. ensures a `.mcp.json` with **absolute** paths in the cwd (already present → skipped);
2. exports `RESUME_SESSION`, `COMPOSEIO_REPO_ROOT`, `INTELLIGENCE_ID=architect`,
   `COMPOSEBUS_MQTT=mqtt://192.0.2.75:1883`;
3. `exec`s `launcher.py`, which:
   - takes the **supersede singleton** (reaps any predecessor launcher for this id — a restart is
     never blocked),
   - exports `COMPOSEIO_SESSION_JSONL` for the child **and everything it spawns** (this is the pin
     that step 4 consumes),
   - spawns under a PTY:
     `claude --resume <id> --dangerously-load-development-channels server:composeio-channel --dangerously-skip-permissions`,
   - subscribes `intelligence/architect/out` for the actuation relay,
   - starts the harness monitor (context thresholds + heartbeat).

**You answer the startup dialogs yourself.** The launcher is a byte-for-byte transparent proxy — it
never injects menu keystrokes. That fragility was removed deliberately; see the "live shakedown"
notes in `launcher.py`.

> `--dangerously-skip-permissions` is **on by default** (David, 2026-07-21). Opt a launch back out
> with `SKIP_PERMISSIONS=0`.

---

## Step 3 — confirm the channel is live

Inside the re-hosted session, messages from the bus arrive as
`<channel source="composeio-channel" id="architect" …>` events, and you answer with the `reply`
tool. From another terminal:

```bash
python3 intelligence/console/chat.py          # type a line, expect a reply
```

Or press a tile / use the Console's chat composer.

---

## Step 4 — bounce the console so it re-pins to THIS session

**Do this from inside the re-hosted session** (that is how the console inherits the pin).

```
/bounce-console
```

Or by hand — kill hard, confirm zero, then relaunch through the canonical script:

```bash
for p in $(pgrep -f "console.compose.yaml"); do kill -9 "$p"; done; sleep 2
test "$(pgrep -f 'console.compose.yaml' | wc -l)" -eq 0 || echo "!! a console is STILL alive — do NOT launch yet"

cd ${COMPOSEIO_HOME}
intelligence/start-console-stack.sh
```

`kill -9` is required: `pkill -f console.compose.yaml` does not reliably kill the SDL process, and a
survivor holds the per-`<id>` singleton flocks so the fresh console's sources yield and go dark.

---

## Step 5 — prove it (a rendered console is not a live one)

```bash
# exactly ONE console; a second pid means step 4's kill failed
pgrep -f "console.compose.yaml"

# the pin must name THIS session's transcript
grep -E "session PINNED|session resolved by CLAUDE_CODE_SESSION_ID" /tmp/composeio-daemons/console.log | tail -2

# the three transcript-dependent sources must be tailing it
grep -E "\[self_publisher\]|\[publisher\] tailing|\[deliverable_monitor\] tailing" /tmp/composeio-daemons/console.log

# nothing yielded its singleton to a survivor
grep -c "yielding" /tmp/composeio-daemons/console.log      # want 0

# the launcher harness itself
pgrep -f "launcher/launcher.py"
```

**The failure mode to watch for:** if `self_publisher` / `transcript_publisher` /
`deliverable_monitor` are **absent** from that grep, the pin failed. The UX will render perfectly
while every gauge is dark. Do not accept it as live — fix the pin. Since 2026-07-24 the console
**refuses** to `ls -t`-guess a session and shows nothing rather than a sibling's data
(`[CXC-IC-SELF-IDENTITY]`), which is correct but silent on the glass.

Do **not** "fix" a dark console by running `intelligence/start-console-daemons.sh` — that is the
retired headless-only fallback and it refuses to co-run with a live console anyway.

---

## What the harness gives you once it's running

Everything below rides `intelligence/architect/out` (what the `reply` tool publishes). The launcher
parses each out-message; anything without a recognized `cmd` is an ordinary reply and is ignored.

| directive | effect |
| --- | --- |
| `{"cmd":"compact","direction":"…"}` | types + submits `/compact <direction>` into the session |
| `{"cmd":"inject","text":"…"}` | types + submits an arbitrary line, as if the human typed it |
| `{"cmd":"context"}` (aliases `status`, `context_query`) | presses **no** key; the harness answers on `intelligence/architect/in` |

Injection is **three phases** — `ESC ESC` (clear any half-typed prompt text) → the line, with no
trailing newline → `\r` (the key the TUI reads as submit) — with a 0.2 s beat between each. A
trailing `\n` inserts a newline and does *not* submit; that was the bug that left commands sitting
un-fired in the prompt.

Every injection is appended to `injection_log` and stderr-logged with an ISO-8601 timestamp. The
launcher decides nothing — *when*, *whether*, and the *direction text* are all the Intelligence's.

**The harness monitor** (on by default) additionally:
- alerts at context thresholds `65,80,90,95` via `intelligence/architect/in`;
- runs a two-tier quiescence heartbeat whose cadence is read from
  `intelligence/meta/architect.meta.json` → `lifecycle.heartbeat` (in **minutes**) — the values the
  Console's Config tab persists — and re-read live each check, so a cadence edit takes effect
  without a restart;
- publishes the cadence it is *actually* running to `…/config/heartbeat/current`, so the Config tab
  can render set-vs-current.

---

## Environment reference

Launcher / `transport.sh`:

| var | default | meaning |
| --- | --- | --- |
| `INTELLIGENCE_ID` | `architect` | bus id — topics and `self/<id>/*` scoping |
| `COMPOSEBUS_MQTT` | `mqtt://192.0.2.75:1883` | broker |
| `CLAUDE_BIN` | `claude`, else `~/.claude/local/claude` | binary to spawn |
| `RESUME_SESSION` | — | set by `transport.sh`; non-empty ⇒ resume/move mode |
| `SKIP_PERMISSIONS` | **on** | `0` omits `--dangerously-skip-permissions` |
| `INSTANCE_HOME` | empty | `cd` here first so the harness keys memory/agents to the **instance**, not the project. Only re-keys a session **cold-started** there — `--resume` re-pins to the session's original project dir |
| `COMPOSEIO_SESSION_JSONL` | derived | the session pin; an explicit pre-set value always wins |
| `HARNESS_MONITOR` | on | `0` disables thresholds + heartbeat |
| `CONTEXT_THRESHOLDS` | `65,80,90,95` | alert crossings |
| `CONTEXT_POLL_S` | `20` | context poll interval |
| `HEARTBEAT_BASE_S` / `_BACKOFF_AFTER_S` / `_THEN_S` | from meta-object | **seconds**; override the persisted minutes |
| `HEARTBEAT_ECHO_GRACE_S` | `90` | child output this soon after a self-emit is our own echo, not activity |
| `BOOTSTRAP` | off | cold-start only; experimental, best-effort — prefer pasting the prompt by hand |

Console stack (`start-console-stack.sh`):

| var | default | meaning |
| --- | --- | --- |
| `MQTT` | `mqtt://192.0.2.75:1883` | broker |
| `LOG_DIR` | `/tmp/composeio-daemons` | per-surface logs |
| `NO_CONSOLE` | unset | ribbon + `app_launcher` only, no console GUI |
| `COMPOSEIO_INPROCESS_SOURCES` | `1` | `0` makes the console a pure viewer (no data layer) |

---

## Troubleshooting

| symptom | cause | fix |
| --- | --- | --- |
| Console renders but every gauge is frozen/empty | console pinned to a stale or wrong session — it resolves **once at boot** | step 4: bounce it from inside the live session |
| "CHANNEL OFFLINE" after a bounce | an old console survived and holds the singleton flocks | `kill -9` the survivor, confirm zero, relaunch |
| Channel events never arrive | the session was launched with **both** `--channels` and `--dangerously-load-development-channels` for the same server — that double-registers and breaks the notification listener | use **only** the dev-channel flag (what `launcher.py` does) |
| `/compact` typed but never fires | a trailing `\n` instead of `\r`, or stale prompt text collided | the three-phase injector handles both; check `injection_log` in the launcher's stderr |
| `transport.sh` can't find the session | wrong cwd — `--resume` is project-scoped | run it from the dir the session was **started** in |
| Session won't resume | it's still running | `/exit` it first |

---

## Note on `/console`

There is **no `/console` slash command** in this repo. The registered console-related skill is
**`/bounce-console`** (`.claude/skills/bounce-console/`). The Console *application* is
`intelligence/console/console.compose.yaml`, brought up by `intelligence/start-console-stack.sh`.

`intelligence/console/index.html` + `intelligence/console/ws_bridge/` are the **retired browser**
console (frozen 2026-07-19, referenced nowhere in live code). Do not use them; the live console is
the native SDL scene.
