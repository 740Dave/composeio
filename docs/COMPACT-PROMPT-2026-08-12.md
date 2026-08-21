# /compact prompt for this session

Paste the block below as the argument to `/compact`. It is written to preserve the things a
default summary reliably loses from a session like this: live hardware state, invariants stated
once in conversation, corrections David made, and the distinction between what was *proven* and
what merely *looked* proven.

---

Summarize this session with the following priorities, in this order.

**1. PHYSICAL AND OPERATIONAL STATE — preserve exactly, these are facts about the world.**
Which boards exist, their device ids, their current transport and config mode, which are
reachable and which are deliberately unreachable, which serial/FTDI ports map to which board,
what processes are running that other things depend on, and the state of the Raspberry Pi's CAN
interfaces. Include the two bus commands that return the Hosyond to WiFi. If a board is
unreachable BY DESIGN, say so — otherwise the next instance will treat it as broken.

**2. DAVID'S STATED INVARIANTS AND CORRECTIONS — verbatim where possible.**
He corrected me several times and the corrections carry more signal than my original framing.
Preserve at minimum: the bus_storage singleton as a PROHIBITION (not a convention) and its
logical-not-per-process scope; his rejection of slicing done for narratability; his rejection of
configured hop depth as a transport-specific leak; his insistence that a no-wireless chain is a
first-class configuration. Preserve the correction itself, not just the conclusion — the fact
that I had it stricter or looser is the useful part.

**3. THE VERIFICATION DISCIPLINE, WITH ITS EVIDENCE.**
This session's through-line is that machine signals repeatedly AGREED WITH THE WRONG ANSWER —
capture.bmp rendering perfectly over a byte-swapped panel, every timing number improving while
the display degraded, a CAN controller reporting successful init with its interrupt on the wrong
pin, a build tag stale across three boards. Keep several concrete instances, not the
generalization alone; the generalization without examples does not transfer. Keep the diagnostic
heuristic about asking for SPECIFIC colours.

**4. WHAT IS PROVEN vs WHAT IS ASSUMED — do not let these blur.**
Proven on hardware: two board bring-ups accepted on the glass; UART transport end-to-end; a
board with no IP configuring itself over the bus; the Pi's CAN loopback. NOT proven: CAN has
never carried a live message, the relay has never run on silicon, the C6 has never run the
transport work, and no one has seen the panel under `config: bus`. Preserve that asymmetry
precisely — the temptation to round it up to "CAN works" must be resisted.

**5. TRAPS WITH THEIR MECHANISM, not just their name.**
build_flags and CMake cache vars never reaching ESP-IDF compiles; port paths not being stable
identities and `esptool chip-id` being a destructive way to check; UART crossing TX/RX while CAN
does not; `.lts` multi-word keys not minting properties. Each needs its mechanism, because the
name alone will not stop a repeat — I repeated the `.lts` key mistake three times after
diagnosing it.

**6. OPEN DECISIONS AND WHOSE THEY ARE.**
Distinguish what awaits David (the glass check; CAN arbitration priority; placement ratification
for the two corpus files) from what is merely unfinished work (C6 reflash, CAN hardware, the
can_mqtt_bridge firehose fix).

**Drop aggressively:** tool-call mechanics, build output, my intermediate reasoning toward
conclusions already recorded in the corpus or the runbook, and anything already durably captured
in `docs/HANDOFF-2026-08-12-bringup-and-transport.md`,
`corpora/platform-specs/60-board-hardware-inventory.lts`, `61-config-over-bus.lts`, or
`docs/board-bringup/RUNBOOK.md` — point at those files instead of restating them.

**Preserve in full:** anything David said that has not been written to a file yet. That is the
only category which cannot be recovered by reading the repo.
