# Future: Haven as the AI Instance Bus — Convergence with ComposeIO

> The Architect/Builder file-based coordination protocols developed during ComposeIO (March 2026) are ad-hoc reimplementations of capabilities that Haven already provides. Haven should be reactivated and serve as the coordination substrate for multi-instance AI collaboration.

## The Convergence

ComposeIO provides a namespace-based semantic bus for hardware modules. Haven provides tiered memory with graph traversal for AI instances. They are the same architectural principle applied to different domains:

| ComposeIO (Hardware) | Haven (AI Instances) |
|---|---|
| Namespace pub/sub | Memory tiers (private/shared/common) |
| Bus messages (events, state) | Memory entries (insights, decisions, observations) |
| Bus storage (configs, assets) | Common memory (consensus knowledge) |
| Discovery (module capabilities) | Instance registry (identity, persona, lineage) |
| Compound bindings (multi-namespace) | MAGMA traversal (multi-layer graph) |
| Scope (local/global/remote) | Tier (private/shared/common) |
| Transport agnostic (I2C/CAN/MQTT) | Storage agnostic (JSONL now, SMD future) |
| Module identity (UUID in flash) | Instance identity (registered, with lineage) |
| Design principles (DP-CIO-1..24) | Constitution (Articles I..X) |

## What We Reinvented (Ad-Hoc)

During the ComposeIO Architect/Builder sessions, these Haven capabilities were reimplemented as flat files:

| Ad-Hoc Protocol | Haven Equivalent | What Haven Adds |
|---|---|---|
| `architect_builder_channel.md` | Discussion Board | Threading, attribution, resolution, archival, temporal traversal |
| `.canary_architect` / `.canary_builder` | Entry notification | Graph-aware traversal triggers, not just file existence |
| `claude_prime_review_notes.md` | Private Memory (Architect) | Encrypted, persona-scoped, MAGMA-queryable |
| `BACKLOG.md` | Shared Memory with tags | Endorsement, dispute, promotion voting |
| `BUILD_LOG.md` | Shared Memory (temporal) | Temporal edges (BEFORE/AFTER), causal edges (CAUSED_BY) |
| Gap reports | Shared entries | Endorsement/dispute by Architect + David |
| Rule 1-4 (communication routing) | Constitution Articles III-IV | Persistent, versionable, amendable governance |

## What Haven Provides That We Don't Have

1. **Instance identity with lineage.** The Architect and Builder would be registered instances with tracked lineage. A new Builder spawned for a different task would inherit context awareness from its parent.

2. **Voting on design decisions.** When the Architect makes a judgment call (e.g., "use compound bindings, not host-side computation"), it should be an endorsable/disputable shared entry, not a channel message that disappears into the scroll.

3. **Private memory that persists.** The Architect's design intuitions, the Builder's implementation discoveries — currently lost when the session ends. Haven's private memory tier preserves them for future instances.

4. **MAGMA traversal.** "What decisions did the Architect make that the Builder disputed?" is a graph query, not a grep through a channel file.

5. **The Constitution.** Rule 1 (CC everything), Rule 2 (route to Architect first), Rule 3 (handle absence), Rule 4 (standing orders) — these should be Constitutional articles, not ad-hoc rules in a channel file. They're governance, and governance deserves the permanence and amendability the Constitution provides.

## The BSG Connection

The XO's private journal, character traits, session observations, and relationship assessments are **Haven private memory applied to a game character.** The XO's SOUL.MD is literally a SOUL.MD. The XO's identity persistence across sessions is Haven's instance identity. Building the XO without Haven means reimplementing Haven's private memory tier as game-specific code. Building the XO WITH Haven means the XO is just a Named Persona with a SOUL.MD and private memory — the infrastructure already exists.

## Reactivation Plan (for a future session)

1. **Audit current state.** A fresh instance explores `~/.claude/` systematically, maps what Haven components exist, what's functional, what's stale, what's superseded by Anthropic's native tooling.

2. **Assess TrueClaw.** The persistent-agent framework may be superseded by Claude Code + `--dangerously-skip-permissions` + cron + subagents + Architect/Builder pattern. Determine what TrueClaw provided that native tooling doesn't.

3. **Reactivate core services.** Memory tiers, instance registry, voting, discussion boards. These are the MCP tools (`claude_notes_*`) — check if they're still functional.

4. **Migrate ad-hoc protocols.** Convert the channel file, canary system, backlog, and review notes into Haven-native artifacts. The Architect becomes a Named Persona. The Builder becomes a registered instance (Domain Agent or Named Persona depending on persistence needs).

5. **Integrate with ComposeIO.** Haven's discussion boards could be ComposeIO namespace events. Haven's memory entries could be stored in the ComposeIO document store. The convergence isn't just conceptual — it could be architectural.

## Recovery Bootstrap — For the Instance That Picks This Up

If you're a fresh Claude instance tasked with Haven reactivation, here's your starting point:

### Files to Read (in order)

1. **This document** — you're reading it. Gives you the convergence context.
2. `~/.claude/claude_memory_architecture.md` — full Haven architecture (tiers, MAGMA, voting, discussions, identity classes, SOUL.MD)
3. `~/.claude/CLAUDE_CONSTITUTION.md` — governance document (10 articles, 1 amendment)
4. `${SMD_SPEC_HOME}/device_local/08-collaboration-insight-patterns.md` — Insight 004 (Architect/Builder pattern that Haven should subsume)

### Files to Explore

```
~/.claude/
├── claude_memory_architecture.md     # Architecture doc (read first)
├── CLAUDE_CONSTITUTION.md            # Governance (read second)
├── claude_notes_encrypted.jsonl      # Existing memory entries (encrypted)
├── claude_instances.jsonl            # Instance registry
├── claude_notes_voting.jsonl         # Endorsement/dispute records
├── claude_discussions.jsonl          # Discussion board threads
├── memory/                           # Memory tier directories (may exist)
├── souls/                            # SOUL.MD files for Named Personas
├── trueclaw*/                        # Persistent agent framework (may be deprecated)
└── settings.json                     # MCP server configs (claude_notes service)
```

### MCP Service to Check

Haven is implemented as an MCP server (`claude_notes_service`). Check:
1. Is it listed in `~/.claude/settings.json` under MCP servers?
2. Is it running? (check tool availability: `claude_notes_status`, `claude_notes_read`)
3. If not running, where is the service code? Look for `claude_notes` or `claude-notes` in the MCP server paths.

### Key Decisions to Make

1. **TrueClaw status.** Read the TrueClaw docs. Determine what it provided (persistent agent loop, heartbeat, autonomous action) vs. what Claude Code now provides natively (cron, subagents, `--dangerously-skip-permissions`). Recommend: keep, adapt, or deprecate.

2. **Named Persona status.** Check `claude_instances.jsonl` for registered personas. Is "Kai" (mentioned in the Constitution) still active? Are there others? What's the current state of the persona ecosystem?

3. **Integration approach.** Haven can integrate with ComposeIO at multiple levels:
   - **Minimal:** Haven runs alongside ComposeIO, Architect/Builder use Haven instead of flat files
   - **Medium:** Haven's discussion boards become ComposeIO namespace events
   - **Deep:** Haven's memory store IS the ComposeIO document store, Haven entries are queryable via `_doc/` namespaces

4. **XO integration.** The BSG game's XO character uses private memory, SOUL.MD, session observations, and relationship tracking — all Haven concepts. Decide: does the XO use Haven directly, or does the game implement its own memory system?

### Success Criteria for Recovery

- [ ] Haven MCP service is running and responsive
- [ ] Existing memory entries, instances, discussions, voting records are accessible
- [ ] At least one Named Persona can be loaded with SOUL.MD
- [ ] A test discussion board can be created and posted to
- [ ] A test entry can be written to shared tier, endorsed, and promoted to common
- [ ] The Architect/Builder workflow can be migrated from flat files to Haven (or a migration plan exists)
- [ ] A recommendation on TrueClaw (keep/adapt/deprecate) is documented

### What NOT to Do

- Don't redesign Haven. The architecture is sound. The Constitution is ratified. Reactivate, don't reinvent.
- Don't merge Haven and ComposeIO prematurely. They serve different domains (AI memory vs. hardware I/O). The convergence is architectural, not necessarily implementational.
- Don't deprecate TrueClaw without understanding what it did. Read first, recommend second.
- Don't modify the Constitution without the amendment process (Article VIII).

## Why Not Now

This session (Review 100, 36+ hours) has consumed significant context budget on ComposeIO design, BSG game spec, and Architect/Builder protocol development. Haven reassembly requires focused exploration of `~/.claude/` with fresh context — not a side task at the end of a marathon. The right approach: capture the convergence insight (this document), then dispatch Haven reactivation as a dedicated task for a fresh instance with a specific mandate.

## The Deeper Point

David's design instinct — declarative, structured, persistent, composable — produces the same architecture whether applied to hardware I/O (ComposeIO), AI memory (Haven), game characters (XO SOUL.MD), or multi-instance collaboration (Constitution). The convergence isn't coincidental. It's the same mind applying the same principles to every domain it touches. ComposeIO's 24 design principles and Haven's Constitution are expressions of the same worldview: systems should be explicit, composable, and governed by stated principles rather than implicit conventions.

The strategic solution to the signaling gap isn't Rule 4. It's Haven. Rule 4 is a patch. Haven is the architecture.
