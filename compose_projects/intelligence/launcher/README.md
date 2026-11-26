# intelligence launcher — transparent PTY proxy + actuation relay + harness monitor

The hinge that makes an Intelligence's `/compact` **self-directed**
([SPEC:CXC-INTELLIGENCE-CONSOLE] · node [CXC-IC-LIFECYCLE]).

It spawns a Claude Code instance under a PTY and then **gets out of the way**. It is a
**transparent proxy**: it forwards the human's terminal ⟷ the child byte-for-byte, so the human
sees the **real** Claude Code UI — including every startup dialog — and answers those dialogs
**themselves**, exactly as if they had run `claude` directly.

On top of that wire it does three things, and only three:

1. **Actuation relay** — when the instance emits a directive out its channel, the launcher types
   it into the child (`/compact <direction>`, or an arbitrary line).
2. **Harness monitor** — context-threshold alerts and a two-tier quiescence heartbeat, delivered
   over the **bus**, never the keyboard.
3. **Session pin** — exports `COMPOSEIO_SESSION_JSONL` so the child and everything it spawns
   instrument the *right* transcript by a passed value, never an `ls -t` guess.

It **decides nothing** about the first of those. *When* to compact, *whether* to compact, and the
*direction* text are all the Intelligence's. The launcher is a decision-free keystroke actuator that
**logs every keystroke it injects** — auditable, no silent action. This is Problem 4b ("control is a
bus peer, not a center") applied to the Intelligence's own death and rebirth: even `/compact` is a
bus-driven leaf action.

`/compact` is an **input-plane** command; a channel event cannot press it (channels push *content*
the instance reads, not control keys). The launcher owns the instance's PTY, so it is the natural
actuator: it hears the instance's authored direction on `.../out` and writes the keystrokes.

## Why this design (a live shakedown)

An earlier version tried to **auto-drive** the startup by watching PTY output and injecting menu
keystrokes, plus typing a "move note." In a real run it **failed**: blindly injecting keystrokes
into Claude Code's full-screen TUI startup dialogs **collided with the human and hung**. Four
real bugs were found on the glass; this rebuild removes every fragile part:

1. **Single channel flag.** Passing `--channels server:composeio-channel` **and**
   `--dangerously-load-development-channels server:composeio-channel` **double-registers** the
   server and breaks the channel notification listener. A custom (non-allowlisted) dev channel
   must be loaded with **only** `--dangerously-load-development-channels`.
2. **No auto-answered dialogs.** PTY keystroke injection into the TUI dialogs is fragile and
   unreliable — the **human** answers them, through the transparent proxy.
3. **No typed move-note.** A multi-line note submits early on embedded newlines, and a **resumed**
   instance already has its context and needs no note at all — so nothing is typed at startup.
4. **Cwd-scoped resume.** `claude --resume <id>` is project-scoped **by cwd**; the channel config
   (`.mcp.json`) must be reachable from the cwd the session is resumed in — so `transport.sh`
   ensures a `.mcp.json` with **absolute** paths in the current directory and does **not** `cd`.

## PTY mechanism

Stdlib **`pty.fork()` + `select`** on `[own-stdin, pty-master]`. No `pexpect`, no interaction
DSL, no dialog-matching. Each cycle forwards, byte-for-byte:

- **child PTY master → stdout** — the human sees the real claude UI, unmodified (dialogs and all);
- **stdin → child PTY master** — the human's keystrokes reach claude, so **they** answer dialogs.

The launcher's own stdin is put into **raw mode** (`termios`/`tty.setraw`) for the duration and
**restored on every exit**, so keys pass through untouched. The one forwarding cycle (`_pump`) is
structured over plain fds so the selftest drives it with pipes — no controlling terminal needed.
The only third-party dependency is **`paho-mqtt`**, matching
[`python/composeio/transport_mqtt.py`](../../../python/composeio/transport_mqtt.py).

## Launch flags

The child is spawned as `CLAUDE_BIN <flags>`:

```
--dangerously-load-development-channels server:composeio-channel
--dangerously-skip-permissions                        # present by default — see below
```

The dev-channel flag is required during the channels research preview — a custom channel isn't on
the allowlist. **Do not** also pass `--channels …` for the same channel (bug #1). The MCP server
name `composeio-channel` is what surfaces as `<channel source="composeio-channel" …>`.

`--dangerously-skip-permissions` is **ON by default** (David, 2026-07-21: settings-level
`bypassPermissions` and allow-rules cannot lift Claude Code's `.claude/` write guard, but the launch
flag can). Set **`SKIP_PERMISSIONS=0`** to opt a launch back out. The flag may **reduce** — but does
**not eliminate** — the startup dialogs; the human still answers whatever remains.

## Resume / Move mode (the primary path)

Setting **`RESUME_SESSION=<claude-code-session-id>`** spawns

```
claude --resume <session-id> --dangerously-load-development-channels server:composeio-channel …
```

`--resume <id>` is **prepended** to the argv; the other flags are kept. This **re-hosts an
ALREADY-RUNNING session INTO the console with its context INTACT** — a **re-host, not a seam
crossing.** Because the instance already holds its context, the launcher injects **NOTHING** at
startup — no move-note, no bootstrap. The human answers whatever startup dialogs appear; the
actuation relay runs as normal.

`claude --resume` is **project-scoped by cwd**: run `transport.sh` (below) from the **same
directory the session was originally started in** (its project dir).

## Cold start & the re-register bootstrap (default **ON**)

A **cold start** (no `RESUME_SESSION`) comes up **ORIENTED**. A cold successor holds no context,
so the re-register bootstrap is typed in — **on by default** since 2026-08-11
([`CXC-ICL-COLD`], ratified). David, 2026-08-10:
*"The re-register bootstrap is not experimental any longer, it works quite well — it should be the default for new instances."*
The prompt is built from
the meta-object manifest by `ptycore.build_bootstrap_prompt`, which points to the
`role_descriptor` / `soul` / `dusk_gifts` artifacts **by path** and **never inlines their
contents** — carry-depth-by-reference, and the reason a re-registered instance is calibrated
rather than merely told about itself.

**`BOOTSTRAP=0` is the explicit opt-OUT** (`false`/`no`/`off` too); unset or blank means ON.
There is **no `BOOTSTRAP_DELAY`** — the knob was deleted with the mechanism.

**Delivery is on ARGV, and nothing is ever typed at startup** ([`CXC-TERM-BOOTSTRAP-ARGV`],
ratified 2026-08-11). The generated prompt is written to a file
(`$TMPDIR/composeio-bootstrap/bootstrap-<id>-<ts>.md`, override with `BOOTSTRAP_PROMPT_DIR`)
and the child's **positional prompt argument** — `claude [options] [command] [prompt]` — is a
short instruction naming that file by absolute path. The file's own first line names the
manifest by absolute path, so a cold instance needs no discovery step.

> ⛔ **This is not dialog auto-answering — and it can no longer become it.** The old path typed
> the prompt after a fixed `BOOTSTRAP_DELAY` settle. On its first live firing it landed in a
> blocking `Enter y/n:` gate and `^[[200~` echoed as literal text. A fixed timer standing in
> for a state check is unsound for **every** value, because the startup it races contains an
> unbounded number of human-gated blocking reads. So the whole startup-write seam is
> **deleted** ([`CXC-TERM-BOOTSTRAP-SETTLE-RETIRED`]): `BOOTSTRAP_DELAY`,
> `bootstrap_delay_s`, `Launcher.arm_bootstrap`, the widget's `_arm_bootstrap`/`_type_bootstrap`
> and `PtyChild.inject_prompt` are all gone. Auto-answering Claude Code's startup dialogs by
> keystroke injection collided with the human and hung — **bug #2** below — and it is out of
> scope **permanently** ([`CXC-TERM-DIALOGS`]), which now holds by construction.
>
> **Mid-session actuation is a DIFFERENT seam and is untouched** ([`CXC-TERM-MIDSESSION-SEAM`]):
> `{"cmd":"compact"}` / `{"cmd":"inject"}` still type into a **live** TUI through the
> three-phase clear → type → `\r`-submit.

| | cold start (default) | resume / move (`RESUME_SESSION` set) |
| --- | --- | --- |
| seam | crossed — successor holds no context | none — context is intact |
| argv | dev-channel flag (+ skip-perms) | `--resume <id>` + the same flags |
| bootstrap | the **re-register bootstrap**, on the child's **argv** (`BOOTSTRAP=0` ⇒ none) | **none** |
| startup injection | **none, ever** — zero bytes reach the child before the human's first keypress | **none** |
| dialogs | answered by the **human** through the proxy | answered by the **human** through the proxy |
| session pin | none — the child resolves by its own rules | `COMPOSEIO_SESSION_JSONL` **passed** into the child env |

## The child mints its own identity

The child is exec'd with a **sanitised** environment, never a raw copy of the launcher's
(`ptycore.HOST_IDENTITY_ENV` / `sanitize_child_env`). When the host is itself a Claude Code
session — the terminal widget's *intended* deployment — an unsanitised copy hands the child the
**host's** `CLAUDE_CODE_SESSION_ID`, which (a) makes it believe it is a nested child session, so
**transcript saving is off**, and (b) resolves its session pin to the **host's transcript**: the
cross-instance leak [`CXC-IC-SELF-IDENTITY`] exists to prevent. Identity, parentage, the
in-harness flag, the messaging socket, the entrypoint, the host pid and the transcript pin are
all stripped; `CLAUDE_CODE_EXECPATH` (a **binary** path, not a session) is kept. A pin that
legitimately names the *child's own* transcript is **passed** in through `env_extra`, so
"passed" and "inherited" stay distinguishable.

## Directives on `.../out`

The Intelligence emits directives out `intelligence/<id>/out`. The channel's `reply` tool publishes
`{from, text, ts, to?, thread?}`, so a directive rides inside `text` as a JSON object; a peer may
also publish it at the **top level** of the out-message. Both forms are accepted.

Anything on `.../out` **without** a recognized `cmd` is an ordinary reply and is **ignored** — no
injection, no side effect.

| directive | kind | effect |
| --- | --- | --- |
| `{"cmd":"compact","direction":"<text>"}` | actuation | types + submits `/compact <direction>` |
| `{"cmd":"inject","text":"<text>"}` | actuation | types + submits the line **verbatim**, as if the human typed it |
| `{"cmd":"context"}` — aliases `status`, `context_query` | **query** | presses **no** key; the harness answers on `intelligence/<id>/in` |

`inject` is the general "enter this on my behalf" primitive — the one the ComposeApp collaboration
model builds on. `/compact` is just its structured special case.

Both nested and top-level forms of a compact directive:

```json
{ "from": "architect", "ts": 1720000000000,
  "text": "{\"cmd\":\"compact\",\"direction\":\"Preserve the meta-object index; drop scratch.\"}" }
```
```json
{ "cmd": "compact", "direction": "Preserve the meta-object index; drop scratch." }
```

### How injection actually works (three phases, not one write)

An actuation is **three separate writes** with a beat between each
(`COMPACT_PHASE_DELAY_S`, 0.2s; the selftest zeroes it):

1. **CLEAR** — `ESC ESC`, wiping any half-typed text already sitting in the prompt;
2. **LINE** — the text, **with no trailing newline**;
3. **SUBMIT** — a **carriage return** (`\r`).

Two facts, both verified live on 2026-07-19, forced this:

- **Submit is `\r`, not `\n`.** The TUI *inserts* `\n` as a newline and submits on `\r`. The old
  bulk write of `/compact <direction>\n` left the command sitting fully typed in the prompt,
  **hung** waiting for the human to press Return. Sending `\r` as a separate write **after** the
  command also lets a paste-buffering TUI finish ingesting before the submit lands — which is what
  makes an **any-length** direction self-fire.
- **The prompt must be cleared first.** Pre-existing text in the entry area collided with the
  injected command; the human had to ESC-ESC it clear by hand. That clearing is the leaf's job.

Every write is appended to `injection_log` and stderr-logged with an ISO-8601 timestamp. The
command record carries the **line itself** (the audit surface); the envelope writes log as
`<kind>-clear` / `<kind>-submit`.

## Harness monitor

Built at `run()` unless `HARNESS_MONITOR` is off. It never presses a key — everything it produces is
**published to `intelligence/<id>/in`**, so it surfaces as a `<channel from="harness">` event, the
same non-interrupting path David's messages ride.

**Context perception is a shared multi-source read** ([CXC-IC-MONITOR], the de-fork). The launcher
cannot see Claude Code's authoritative `used_percentage` (that goes to the statusline command), so it
delegates to `composeio.self_sensor.context_source.read_context` — **the same reader the console
publisher uses**, so the gauge and the threshold crossings can never perceive different levels.
Precedence, freshest authoritative source first:

1. **hud-cache** — haven-hud's per-session snapshot, persisted every ~300ms independent of the CLI
   redraw (typically freshest; the fix for the 2026-07-26 staleness defect);
2. **statusline-file** — authoritative but **freezes** between CLI redraws;
3. **jsonl-estimate** — over-reading estimate, only when no authoritative source exists.

**Thresholds** default to `65,80,90,95`; each fires once (armed/disarmed), primed on the first read.

**Heartbeat** is two-tier: `base` until `backoff_after` of quiescence, then `then`. Cadence comes
from the persisted meta-object `lifecycle.heartbeat` (in **minutes** — where the Console's Config
tab writes via `config_persister`), overridable by env (in **seconds**). It is **re-read every
check** (`cadence_provider`), so a cadence edit takes effect within one poll with **no restart**.
The cadence actually in force is published to `intelligence/<id>/config/heartbeat/current` so the
Config tab can render set-vs-current.

Child output within `HEARTBEAT_ECHO_GRACE_S` of a harness self-emit is treated as the Intelligence
replying to our own nudge, **not** genuine activity — otherwise the two-tier cadence never escalates.

## Singleton

`run()` takes a **superseding** singleton (`intelligence-launcher-<id>`) **before** spawning a rival
`claude`. Two launchers would both relay `.../out` and **double-inject** every actuation. A plain
yield-if-held flock would make a resume start exit and silently fail the restart, so it
**supersedes**: reap any predecessor, then hold the lock for our lifetime (flock releases on death,
so a crash never blocks a restart). Best-effort — if a predecessor genuinely can't be reaped it
still launches, because a restart must not be blocked. The MQTT client-id is stable (non-PID) as a
broker-level backstop.

## Session pin

Before fork/exec, `_export_session_pin()` sets `COMPOSEIO_SESSION_JSONL` in this process's env —
inherited by the child and **everything it spawns**, including the console's in-process data layer.
This is [CXC-IC-SELF-IDENTITY]: the transcript is **passed**, never guessed. It is a no-op on cold
start (no prior session id) and an explicit pre-set value always wins. Path derivation is
single-sourced through `_derive_jsonl_path`, the same function the monitor's fallback reader uses.

> **Note:** the pin reaches the *child*. A console that is **already running** keeps whatever pin it
> resolved at its own boot — `resolve_session_jsonl` runs once. Bounce the console from inside the
> re-hosted session to re-point it. See [`../QUICKSTART-launcher-harness.md`](../QUICKSTART-launcher-harness.md).

## transport.sh

Re-hosts a running session into the console:

```bash
# Run this from the SAME directory the session was originally started in (its project dir).
./transport.sh <session-id-to-resume>
```

It **does not `cd`** anywhere (unless `INSTANCE_HOME` is set — below). It ensures a `.mcp.json`
exists **in the current directory** registering **three** servers, all by **absolute** path:

| server | what it is |
| --- | --- |
| `composeio-channel` | the bus channel (`compose_projects/intelligence/channel/channel.mjs`) |
| `lts-engine` | the FactLattice query engine, from the private deployment (`~/factlattice-internal-dev`) |
| `decision-ops` | the decision-graph MCP that pushes a MAGMA refresh on write |

Absolute paths mean they load no matter which project dir the session is resumed in (a relative path
would only resolve from the repo root, which we are not in). `.mcp.json` is gitignored, so **that
block is the durable source of truth** — a fresh machine regenerates the full manifest. The check is
keyed on `"lts-engine"`, so an older channel-only file gets upgraded to all three.

It then `exec`s the launcher with `RESUME_SESSION`, `COMPOSEIO_REPO_ROOT` (pins the repo by absolute
path so anything launched inside the session resolves repo-relative files regardless of the resumed
cwd), `INTELLIGENCE_ID`, and `COMPOSEBUS_MQTT`.

Exit the target session first — a session can't be resumed while it's still active; Claude Code
prints the resume id on `/exit`.

**`INSTANCE_HOME`** (optional, [DEC-INSTANCE-KEYED-HOMES], David 2026-07-23): set it to
`~/compose-instances/<role>/` to `cd` there before launching, so the harness keys memory / statusline
/ agents to the **instance** rather than the project — an instance works across many Compose\*
projects, so keying by project re-fragments it the moment it moves between them. Opt-in and safe:
empty (the default) is the exact prior behavior. Note the resume constraint — `--resume` re-pins to
the project dir the session was **created** in, so this re-keys only a session **cold-started** in
the instance home. Reach the Compose\* projects via `additionalDirectories` in
`$INSTANCE_HOME/.claude/settings*`.

## Config

Env, or a JSON file via `--config <path>`.

**Identity + spawn**

| var | default | meaning |
| --- | --- | --- |
| `INTELLIGENCE_ID` | `architect` | bus id (topics + prompt) |
| `COMPOSEBUS_MQTT` | `mqtt://192.0.2.75:1883` | ComposeBus MQTT broker |
| `CLAUDE_BIN` | `claude` | the binary to spawn |
| `RESUME_SESSION` | `` (empty) | a Claude Code session-id; non-empty ⇒ **resume / move mode** |
| `SKIP_PERMISSIONS` | **on** | `0` ⇒ omit `--dangerously-skip-permissions` |
| `INSTANCE_HOME` | `` (empty) | `transport.sh` only — `cd` here first (instance-keyed homes) |

**Session pin / transcript**

| var | default | meaning |
| --- | --- | --- |
| `COMPOSEIO_SESSION_JSONL` | derived from `RESUME_SESSION` | the pin **passed** to the child in RESUME mode; a pre-set value always wins. NOT inherited — it is stripped and re-supplied, see "The child mints its own identity" |
| `SESSION_JSONL` | — | explicit override for the monitor's fallback reader (read in the **parent**; stripped from the child env) |
| `CLAUDE_PROJECTS_DIR` | `~/.claude/projects/<cwd-key>` | where session `.jsonl` files are looked up |
| `SELF_STATE_FILE` | `~/.claude/composeio/self-<id>.json` | the statusline-emitted state file |

**Harness monitor**

| var | default | meaning |
| --- | --- | --- |
| `HARNESS_MONITOR` | on | `0` disables thresholds + heartbeat entirely |
| `CONTEXT_POLL_S` | `20` | seconds between context reads |
| `CONTEXT_THRESHOLDS` | `65,80,90,95` | comma-separated alert crossings |
| `HEARTBEAT_BASE_S` | meta `lifecycle.heartbeat.base` × 60, else 600 | first-tier cadence (**seconds**) |
| `HEARTBEAT_BACKOFF_AFTER_S` | meta `…backoff_after` × 60, else 3600 | quiescence before escalating |
| `HEARTBEAT_THEN_S` | meta `…then` × 60, else 3600 | second-tier cadence |
| `HEARTBEAT_IDLE_S` | — | **legacy** alias for `HEARTBEAT_BASE_S` |
| `HEARTBEAT_ECHO_GRACE_S` | `90` | child output this soon after a self-emit is our echo, not activity |

**Bootstrap (cold start only)**

| var | default | meaning |
| --- | --- | --- |
| `META_MANIFEST` | leg 1 of the **chain** | explicit operator override. Read **every launch** — for the heartbeat cadence, and for the bootstrap prompt |
| `INTELLIGENCE_ROLE` | `= INTELLIGENCE_ID` | leg 3 of the chain: `intelligence/meta/<role>.meta.json`, which is what makes N instances of one role possible |
| `BOOTSTRAP` | **on** | `0`/`false`/`no`/`off` ⇒ opt OUT of the cold-start re-register bootstrap. The **only** way to get a deliberately unoriented instance |
| `BOOTSTRAP_PROMPT_DIR` | `$TMPDIR/composeio-bootstrap` | where the generated prompt file is written; argv names it by absolute path |

**The manifest chain** ([`CXC-TERM-MANIFEST-CHAIN`]) — `META_MANIFEST`, then
`intelligence/meta/<id>.meta.json`, then `intelligence/meta/<role>.meta.json`, then **REFUSE**.
An exhausted chain **exits 3 naming every path tried** and spawns nothing. It does not log and
launch: a booted-but-unoriented child still takes a channel, still takes the singleton, and
still renders as a working session — it just is not oriented, and nothing on the glass says so.

`SIGTERM`/`SIGINT` trigger graceful shutdown: stop the monitor, stop the MQTT loop + disconnect,
`SIGTERM` the child, close the PTY, and restore the terminal from raw mode.

## Run

```bash
# Proxy + relay a real `claude`
python3 launcher.py

# Headless selftest — dummy child + mock MQTT, NO real claude, NO broker
python3 launcher.py --selftest
```

## Selftest

`python3 launcher.py --selftest` spawns [`dummy_child.py`](./dummy_child.py) (a tiny script that
echoes its argv as `ARGV:…`, prints startup output including `STARTUP_MARKER`, then echoes stdin
lines as `ECHO:<line>`) under a **real PTY via `pty.fork()`**, and drives the launcher's transparent
proxy through **pipes** (no controlling terminal required) with an in-memory mock MQTT.

Expected output:

```
PASS: launcher proxy+relay selftest (8/8 groups)
  (a) child stdout is transparently forwarded to the proxied output; COLD argv has no --resume
  (b) bytes written to the launcher's stdin are forwarded to the child (dummy echoed them)
  (c) a compact directive typed + SUBMITTED '/compact <direction>' in 3 phases (ESC-ESC clear -> command -> \r), clearing stale prompt text
  (f) a plain inject directive entered an ARBITRARY message VERBATIM on the instance's behalf (same 3-phase actuator; /compact is its special case)
  (d) a non-directive out-message caused NO injection
  (g) a context query pressed NO key and was ANSWERED on the `in` topic (from="harness" envelope)
  (e) resume mode adds '--resume <id>' + the SINGLE dev-flag (no --channels) and injects NOTHING at startup
  (h) the two-tier heartbeat cadence is READ from the persisted meta-object (minutes->seconds), env overrides win, legacy HEARTBEAT_IDLE_S maps to base (BUG 3 wiring)
```

Group (c) is the load-bearing one: it pre-loads **stale half-typed text** into the child's prompt
(no newline, so it just sits there), reproducing the live bug, then asserts the ESC-ESC clear wiped
it, the command carries **no trailing newline**, the three phases land in order, and the dummy
echoed the **submitted** line.
