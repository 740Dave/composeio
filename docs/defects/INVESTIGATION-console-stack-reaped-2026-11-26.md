# INVESTIGATION: the console stack exits silently within seconds of a LaunchAgent start

Defect: DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E in the registry (registered 2026-11-26;
working name DEFECT-CONSOLE-STACK-REAPED-WITH-ITS-LAUNCHAGENT-JOB)
Branch: `defect/console-stack-reaped-by-launchd`, off public main 000e9b43, in the scratch clone
Root-cause class: **under-spec**. Behind it is a false premise in a comment ("nohup & = detached"),
plus an implementation oversight in the idempotency guard, which inferred membership from
command-line text (sections 12 and 13).
Verdict on the three candidates: **not the public scrub, not the overlay: something older.**
The bug was latent from the day the LaunchAgent was installed (2026-08-04). It first showed now
because tonight is the first time the agent ran the bringup at a script path that exists.

Host names and overlay values are redacted throughout.

## 1. Symptom (as reported, then confirmed on the artifacts)

LaunchAgent `com.composeio.console-stack` ran `start-console-stack.sh` and printed "console stack
up" with console pid 14144, ribbon 14146 and app_launcher 14148. `console.log` shows 21 in-process
sources and 20 lines of "MQTT transport not started, dropping message …", with no traceback. All
three processes were gone within seconds.

## 2. Registry (step 0)

`defect_ops query` returns nothing for this symptom: the query is an exact slug match, so I also
grepped the graph directly. The graph is kept outside the repo, under the corpus root
(`COMPOSEIO_CORPORA_ROOT`, then `defects/defect-graph.lts`). That is why the public tree "lacks"
it. Nearest kin:

- DEFECT-LAUNCH-BAR-RIBBON-IS-NOT-2378F8 (2026-08-04). Its fix added this bringup and the
  LaunchAgent. It tested that the members are LAUNCHED, never that they SURVIVE.
- DEFECT-TWO-COPIES-OF-THE-INTELLIGENCE-0CE4A6 (2026-08-15). It "exonerated" the LaunchAgent on
  `runs=1, last exit 0`. That instrument reports only the one-shot bash's exit, so it could
  never show that the job's children were killed.

## 3. Root cause, with file:line

**The bringup's children never leave the LaunchAgent job's process group, so launchd reaps them
when the one-shot job exits.**

- `compose_projects/intelligence/start-console-stack.sh:105,115,124` (public main 000e9b43)
  launches each member with `nohup python3 … &`. nohup ignores SIGHUP and nothing else, so the
  children keep the script's process group.
- The plist (the template in the private deploy repo, and the installed copy in the user's
  LaunchAgents folder) is `RunAtLoad`, `KeepAlive false`, and has **no
  `AbandonProcessGroup`**. Its comment says the bringup "spawns the three surfaces detached and
  exits". That is false. launchd.plist(5): when a job exits, launchd kills the processes left in
  its process group unless AbandonProcessGroup is true.

The evidence is launchd's own log (`log show`, 2026-11-26 local time):

```
20:53:11.355  bootout initiated by launchctl <- zsh <- <agent CLI>   (installer re-load)
20:53:11.359  bootstrap … Successfully spawned bash[14130]
20:53:11.421/.437/.454  nohup.14144 / .14146 / .14148 added (the three members)
20:53:12.535  [14130] exited due to exit(0), ran for 1176ms      <- job exits after `sleep 1`
20:53:13.014  Python.14146 inactive                               <- ribbon, +0.48 s
20:53:16.565  Python.14144 inactive                               <- console, +4.0 s
```

Reproduced in the scratch clone (`compose_projects/intelligence/test_start_console_stack.py`).
Before the fix all three recorded members have pgid == the job's pgid, and one `killpg(SIGTERM)`
reaps all three.

## 4. Why the exit is SILENT (exit code, exception, singleton flock)

- **Console: a clean exit 0, with no exception to swallow.** SDL installs SIGTERM/SIGINT handlers
  that queue `SDL_QUIT`. Verified with a probe: after `SIGTERM` the process is still alive and
  `SDL_QUIT` is queued. `python/composeio/render/window.py:364` maps `SDL_QUIT` to "close", so
  `process_events()` returns False (`runtime.py:2263`). The loop ends and `main()` runs
  `sources.stop()` in its `finally`, which accounts for the 4 s (see kin DEFECT-SOURCE-STOP-
  DOES-NOT-JOIN-WITHIN-BUDGET-E7A1C4). It then returns, so the exit is 0 with no log line.
- **Surface singleton ([CXC-IC-SURFACE-SINGLETON]).** `runtime.py:2421` took the flock before the
  window opened. The kernel releases a flock when its process dies, so no stale lock is left
  behind and the next launch acquires it normally. The flock is innocent here. It only makes a
  second console yield with exit 0 and a `[surface-singleton]` stderr line, and that line does
  not appear in this log.

## 5. Why the ribbon and app_launcher die too

They share the process group, so they get the same SIGTERM. The ribbon is an SDL surface with no
in-process sources, so it takes the same `SDL_QUIT` path and exits in 0.5 s. app_launcher has no
SDL, so it dies from Python's default SIGTERM action, also silently. One cause kills all three.

## 6. Why the transport is "not started": a separate symptom, not the cause

- The broker key is `COMPOSEIO_MQTT_BROKER`, read at build time by
  `python/composeio/transport_registry.py:167`. The scene omits `broker:`
  (console.compose.yaml:21-24). The overlay key is present (checked by script, values not
  printed): `MQTT`, `COMPOSEBUS_MQTT` and `COMPOSEIO_MQTT_BROKER` exist and name the same host,
  and `COMPOSEIO_MQTT_BROKER` is a bare host, as the factory expects. The bringup sources the
  overlay at start-console-stack.sh:61. **The overlay is fine.**
- "not started" is printed only when `self._client is None` (`transport_mqtt.py:579-583`), which
  means `start()` has not run yet. `main()` starts the 21 in-process sources at `runtime.py:2437`.
  The bus starts its transport only inside `app.run()` → `start()` → `self.bus.start()` at
  `runtime.py:1408`. Every early publish in that gap is dropped, and the process was SIGTERMed
  about 1 s later. This ordering is structural, so it happens on every console start. It is
  older than the swap.
- **The coordinator's lead (a single `connect()` with no retry, transport_mqtt.py:779-784) is a
  real defect, but not this one.** `start()` assigns `_client` at :759, before `connect()`. A
  failed first connect therefore logs `ERROR MQTT connection failed` and QUEUES later sends; it
  does not drop them as "not started". Proven headlessly against a closed local port:
  send-before-start gives "not started", while start-then-failed-connect gives "connection
  failed" plus `pending=1`. The live console.log has **no** "connection failed" line.
  Recommended as a separate defect: connect_async plus loop_start (paho reconnects by itself),
  backoff, and a red health signal.

## 7. Why it appears only now

The LaunchAgent's StandardErrorPath log (`composeio-console-stack.error.log`) shows repeated
"`…/composeio/intelligence/start-console-stack.sh: No such file or directory`". The agent pointed at the pre-
`compose_projects/` path, so for a long time it never ran the bringup at all. The deploy template
still points there, and so does `install.sh`'s `CANONICAL_SCRIPT`. Tonight the live plist was
repointed to the right path, so the agent actually ran the stack for the first time in a while,
and the latent reap became visible. Interactive bounces (the skill) never showed it because the
calling shell outlives the bringup.

## 8. Fix (on the branch, one commit on 000e9b43)

Three parts. Sections 12 and 13 say why the guard ended up lock-based.

- **Members outlive their launcher, structurally.** `spawn_detached <log> <cmd…>` replaces the
  three `nohup … &` launches. It runs a `python3 -c` shim in the FOREGROUND that ALWAYS forks.
  The child calls `setsid()` and reports on a pipe, and the parent exits only after that report.
  So the function cannot return while a member is still in the script's process group. That
  does not depend on the trailing `sleep 1` or on how slowly a cold interpreter starts at login.
  If exec fails, the child reports that too and the shim exits 127. macOS has no `setsid(1)`.
- **The idempotency guard asks each member's LOCK, never the process table.** A new
  `python/composeio/stack_member_probe.py` (`--scene <scene>` | `--role <role>`) returns 0 if the
  lock is held (stdout: the holder pid), 1 if it is free, and 2 if it cannot probe. That is
  pgrep's exit contract, so `surface_absent` keeps its fail-safe shape. The probe adds no lock
  mechanism. The app id comes from the real scene parser; "is this scene a singleton" is the
  runtime's own `_surface_singleton_required`; the key is `app_singleton.surface_singleton_key`;
  and the probe is `singleton.probe_singleton_path`, which reuses the repo's single flock call
  site (non-blocking, never disturbs a holder). Paths honour `$COMPOSEIO_LOCK_DIR`.
  A scene that declares no host lock gets exit 2, so it is never "absent forever".
  **An unreadable lock is never "absent"** (Round-2 review R2-F2): the probe classifies before it
  delegates. It returns exit 1 ONLY when the lock file is missing from a usable lock dir, or is
  present and openable with its flock free. A missing or inaccessible lock dir, a dir the member
  could not create its lock in, or a lock file that exists but cannot be opened, all give exit 2.
  This matters because a member that cannot take its lock runs UNLOCKED (app_singleton degrades to
  /dev/null on purpose), so "absent" would add an unlocked duplicate on every bringup.
  `singleton.probe_singleton_path` is unchanged, since daemon_watch shares it.
- **One member's failed spawn does not stop the others** (review N7). `spawn_member` records the
  failure (a log it cannot write, or exec exiting 127) and carries on. The bringup then exits 1,
  with `console stack INCOMPLETE: failed to start: <members>` on stderr.
- **Every member has a lock.**
  - The console already held the surface flock (`design.data_sources: true`).
  - app_launcher already held the `app-launcher` role singleton. It is now the module constant
    `SINGLETON_ROLE`, and a test pins that the bringup asks that same role.
  - The ribbon held NO lock. It now declares `design.surface_singleton: true`: a new parser field,
    and `_surface_singleton_required` honours it. One ribbon per host. Viewers set neither flag,
    so they stay unconstrained, and the containment clause of [CXC-IC-SURFACE-SINGLETON] holds.
- **The guarantee, in the wording ratified after the Round-2 review: no duplicate SURFACE opens
  or stays.** A member takes its lock a moment after it starts (a few hundred ms warm: interpreter,
  runtime import and scene parse). A second bringup inside that window starts a duplicate
  PROCESS. That process acquires before any window opens and before in-process sources or a bus
  start (runtime.py:2421 vs 2437), finds the lock held, and yields with exit 0 and a stderr line.
  So no duplicate surface opens, and the incumbent is not disturbed (unless
  `COMPOSEIO_SURFACE_SUPERSEDE=1`).
- **Known limit, accepted as pre-existing (review N8): a probe can make a starting member yield.**
  `probe_singleton_path` tests "is it free?" by briefly TAKING the flock and closing it. A member
  whose own acquire lands in those microseconds sees "held" and yields silently (exit 0). The
  review measured 2,449 of 77,850 concurrent acquires losing to a tight probe loop with no real
  holder. The bringup's final status line probes about 1 s after launch, which is close to when
  a cold console reaches its lock, so the odds per bringup are small but not zero. `daemon_watch`
  already probes this way, so the race predates this change. Mitigations, not on this branch: a
  member retries its acquire once after a few ms before yielding, or the status line skips
  probing.

Tests (`compose_projects/intelligence/test_start_console_stack.py`; each uses a private
`COMPOSEIO_LOCK_DIR`, so the live stack cannot affect them):

- `…survive_the_launching_jobs_process_group_teardown`: the REAL bringup is run as a
  group leader (as launchd does), with stubbed members, then SIGHUP and SIGTERM go to the job's
  group. All three recorded members must survive.
- `…survives_a_group_SIGTERM_sent_the_instant_its_spawn_returns` (review N1): `kill -TERM 0`
  right after EACH of three `spawn_detached` calls. RED against the backgrounded shim, 3 of 3
  runs.
- `test_review_F1_…[5 cases]`: the review's duplicate table, end to end. A fake member holds the
  member's real lock, started with an absolute scene path, with `python3 -u -m`, with `./scene`,
  as a script file named `app_launcher.py`, or through an interpreter path containing a space.
  The real bringup must print `= <member> already up (pid <holder>)` and launch only the other
  two. All 5 are RED on the anchored-text script and on the base script.
- `…MENTIONS_every_member_is_not_a_member` (`/bin/sh`, review N3): a shell naming all three
  members holds no lock, so all three are launched. RED on the base.
- Guard, executed: free lock means absent; held lock means up (one per member, and free again
  after the holder dies); exit 2 or 127 means do not launch; a no-lock scene gives exit 2.
- `…every_stack_member_declares_the_lock_the_bringup_reads`: a cross-implementation pin. The
  scenes are lock-backed, the bringup asks those scenes and the `app-launcher` role, and that
  role is what app_launcher acquires. It also pins the bringup's wiring (review N4).
- `…second_bringup_launches_nothing_while_the_members_hold_their_locks`: idempotency, executed.
- `python/tests/render/test_surface_singleton.py::test_a_scene_may_DECLARE_itself_a_host_singleton_without_the_data_layer`:
  the ribbon flag reaches the gate through the real parser, and a viewer stays unconstrained.
- `test_REAL_probe_cannot_answer_when_the_lock_cannot_be_read[3 cases x scene/role]` (R2-F2):
  lock file mode 000, lock dir mode 000, and `COMPOSEIO_LOCK_DIR` naming a missing dir, each run
  through the REAL probe, must give exit 2. All 6 are RED against the previous probe (it gave 1).
  Controls `test_REAL_probe_reports_absent_only_for_a_readable_free_lock[2 cases x scene/role]`:
  a missing file in a good dir, and a stale file whose flock is free, give exit 1. The stubbed
  `member_probe` returning 2 or 127 is kept as the guard-level test.
- `test_one_members_failed_spawn_does_not_stop_the_others` (N7): the console's log path is a
  directory, so its spawn fails. The ribbon and app_launcher must still launch, and the bringup
  must exit 1, naming console. RED against the previous script, which aborted.
  `test_spawn_detached_reports_an_exec_failure_as_127` pins the shim's exec-failure report.
- Mutations: removing `os.setsid()` turns both detachment tests RED. Reverting the runtime gate
  turns the DECLARE test and the cross-implementation pin RED.

Suites: test_start_console_stack.py, test_surface_singleton.py, test_singleton_hardening.py
(single flock call site), test_launch_bar.py: 99 passed, 1 skipped. app_launcher `--selftest`: 19
checks PASS. Full `python/tests/render`: 3228 passed, 30 skipped, 6 xfailed, 1 xpassed, 0 failed.

## 9. Spec diff (proposed: the nodes live in the out-of-tree corpus, so they are the architect's to ratify)

`platform-specs/48-compose-launch-bar.lts` § `[CXC-LB-AUTOSTART]`, replace "requirement
(idempotent)" and add one requirement:

```
- requirement (idempotent, by LOCK): each stack member is launched only if its host lock is FREE:
  the surface flock for the console and the ribbon, and the `app-launcher` role singleton for
  app_launcher, read by a non-blocking probe (`stack_member_probe`). Membership is NEVER inferred
  from process command-line text, which cannot tell a member from a shell that mentions one, nor
  recognise a member started in an unanticipated way. An unanswerable probe means do NOT launch,
  and a lock that cannot be READ (dir missing or inaccessible, file present but unopenable) is
  unanswerable, never absent.
- requirement (no duplicate SURFACE opens or stays): the guarantee is the member's own lock, so
  a second launch inside a member's pre-lock window may start a duplicate PROCESS. That process
  MUST yield before opening a window, starting in-process sources or starting a bus.
- requirement (members are independent): a member whose spawn fails MUST NOT prevent the others
  from launching; the bringup reports the failed members and exits non-zero.
- requirement (members outlive their launcher): every member MUST be launched in its own session
  (setsid), and the bringup MUST NOT continue until that has happened, so a member survives the
  exit of the bringup AND a teardown of the launching process group at any moment. The boot
  LaunchAgent runs the bringup as a one-shot job, and launchd SIGTERMs the job's remaining process
  group when the job exits (launchd.plist(5) AbandonProcessGroup). A member left in that group is
  reaped about 1 s after "console stack up" and exits 0 with no traceback (SDL maps SIGTERM to
  SDL_QUIT). DEFECT-CONSOLE-STACK-CONSOLE-RIBBON-APP-A37C0E, 2026-11-26.
- scenario:
  - when: the bringup is run as a process-group leader and that group is SIGTERMed right after
    any spawn, or after the bringup exits
  - then: console, ribbon and app_launcher are all alive, and none shares the bringup's pgid
- scenario:
  - when: a member is already running, however it was started, or only a shell mentions one
  - then: the first is reported "already up" and no duplicate surface opens or stays; the second
    does not suppress a launch
- correction: "launchctl runs=1, last exit 0" (DEFECT-…-0CE4A6) measures the one-shot bash only;
  it cannot exonerate the agent with respect to its children.
```

`[CXC-IC-SURFACE-SINGLETON]`, amend its containment clause: a scene is a host singleton if it
declares `design.data_sources: true` (it owns the session) OR `design.surface_singleton: true`
(one per host, without the data layer: the Launch Bar ribbon). A scene that sets neither, which
includes every viewer, stays unconstrained.

## 10. Deploy-side follow-ups (the private deploy repo; NOT on this branch)

1. Keep `<key>AbandonProcessGroup</key><true/>` in the plist template (already added to the live
   copy). It is a second, independent guard.
2. Fix the stale `…/composeio/intelligence/start-console-stack.sh` in the plist template and in
   `install.sh` `CANONICAL_SCRIPT`: both must say `compose_projects/intelligence/`.
3. Correct the plist comment's "spawns the three surfaces detached".

## 11. Bounce the live console (architect's act)

After this branch is on public main:

```
cd <canonical composeio checkout> && git pull --ff-only \
 && launchctl kickstart gui/$(id -u)/com.composeio.console-stack \
 && sleep 15
```

Then read the last lines of the LaunchAgent's stdout log (the plist's `StandardOutPath`). The
final "console stack up (lock holders; …)" line lists each member's lock-holder pid; `-` means not
locked yet. Re-running the bringup must print "already up" three times.
**One-time transition:** a ribbon started by the OLD code holds no lock. So the first bringup after
landing, while that ribbon is still running, starts a second ribbon (which takes the lock), and
the old ribbon has to be closed once by hand. Close the old ribbon before the kickstart, or
right after it, by its pid (the pid from the pre-landing stack log).

## 12. Second finding: app_launcher is never launched (the guard matched the caller's own shell)

Reported after the live plist gained `AbandonProcessGroup`: the console and ribbon survive, but
every run prints "= app_launcher already up (pid N)" with a new N each time, and seconds later
no app_launcher exists.

- **Nothing killed it, because it was never started.** `$LOG_DIR/app_launcher.log` (the bringup's
  log dir) had mtime 20:53. Its 3 lines belong to pid 14148, the instance reaped with the job
  (section 3).
- **What matched at start-console-stack.sh:122 (public main):** `surface_absent
  "$APP_LAUNCHER_MODULE"` ran `pgrep -f intelligence.tools.app_launcher`, an unanchored search of
  EVERY process's full argv. A Claude Code Bash call runs as `zsh -c "<entire command>"`. The
  first version of the bounce command ended with a `pgrep` loop that named
  `intelligence.tools.app_launcher` literally, so the caller's own wrapper shell matched during
  the kickstart. A new Bash call means a new zsh, hence a new N. Only app_launcher was hit,
  because the console and ribbon guards were full repo paths that the loop didn't spell.
  Reproduced in scratch.
- **Not a concurrent second job, and not the kickstart racing RunAtLoad:** either would have
  started its own app_launcher and rewritten its log. **Not stdin, SIGHUP or the app_launcher
  singleton:** no process ever existed to receive them.
- **First fix, superseded:** an argv regex anchored to `^<python> -m <module>`. The candidate
  review (section 13, F1) showed it MISSED real members started another way, and a duplicate
  was started. The lesson: command-line text is the wrong instrument in BOTH directions.
  Membership is now read from the member's lock (section 8).

## 13. Candidate review on the anchored-text version: dispositions

- **F1 (FAIL, R2): real members missed, duplicates started.** Fixed by construction: the guard no
  longer reads argv. All five rows of the review's table are executed tests (section 8), RED
  before, GREEN now.
- **N1: detachment depended on timing.** Fixed structurally: the foreground shim returns only
  after the child's `setsid()`. There is a new test that sends a group SIGTERM right after each
  spawn. The first draft of that test used `trap '' TERM`, which is inherited across exec, so it
  passed vacuously against the backgrounded shim. It now uses a handler trap (reset in children),
  and it is RED 3 of 3 against the old shim.
- **N2: argv[0] trusted by name.** Gone along with the argv pattern.
- **N3: `/bin/zsh` in the test.** Now `/bin/sh`.
- **N4: wiring only pinned for app_launcher.** The cross-implementation pin now covers all three
  guards (`--scene "$CONSOLE_SCENE"`, `--scene "$RIBBON_SCENE"`, `--role "$APP_LAUNCHER_ROLE"`).
- **N5: doc counts and private names.** Counts updated. The private repo name and an internal
  commit reference were removed; the private deploy repo is now named generically.
- **N6: pgrep missing means nothing launched, exit 0.** Kept as is: the probe follows the same
  contract (exit 2 or 127 means do not launch). It was a deliberate choice
  ([CXC-IC-SURFACE-SINGLETON]).

## 14. Round-2 review (on the lock-based version): dispositions

- **R2-F2 (FAIL): an unreadable lock was reported "absent".** Fixed in `stack_member_probe` only,
  by classifying before delegating (section 8). There are three REAL-probe tests, each for scene
  and role. `probe_singleton_path` is unchanged.
- **Probe 3 / R2 wording:** ratified as "no duplicate SURFACE opens or stays". The requirement
  text in section 9 and section 8 now say so.
- **N7: an exec failure aborted the bringup.** Fixed (`spawn_member`), with an end-to-end test.
- **N8: a probe can make a starting member yield.** Accepted as pre-existing and recorded as a
  known limit in section 8.
- **N5 residue (an agent executable name in a quoted launchd line):** replaced with `<agent CLI>`.
