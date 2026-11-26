# Git history archive — R503 design work

> ⛔ This directory is NO LONGER UNDER GIT (David, 2026-08-17: Dropbox backs it up; the intent is to
> reach a state that can be checked into the main Compose\* repo). This file preserves the **reasoning**
> from 53 commits, because commit messages carried decisions and corrections that exist nowhere else.
> The design reasoning proper lives in `corpus/*.lts`; the process record lives in
> `docs/handover-2026-08-16/HANDOVER.md`. This is the third strand — why each change was made, in order.

Archived 2026-08-17. Working tree at archive time is the consolidation of three branches
(`master`, `spec/hal-contract`, `build/bringup-probe`), verified file-by-file: 57 files, 0 missing.

---

## bf8dba8 — spec(fphal): David's DSD answers — D2 and D3 CLOSED

**2026-08-17 13:07:51 -0700** · David Burnett

D2 [DD-FPHAL-CHALLENGE-MATCH-SURFACE] — CLOSED (b), DROP THE FIELD.
supports_challenge_match leaves the capabilities() schema and verify_slot() leaves the
Protocol. Seven required capability fields become SIX; twelve operations become ELEVEN.
Nothing consumed it, the verification method it came from was REJECTED, and its signature
was an inference from one clause inside that rejected alternative. Re-adding costs one
field and one method if a consumer appears.

D3 [DD-FPHAL-SCORE-SCALE] — CLOSED (a), OPAQUE.
Ordering meaningful within an implementation, value not comparable across them, and NO
CONSUMER MAY THRESHOLD ON IT. The parity suite's ban on comparing magnitudes stops being
a guard and becomes enforcement. The tell that it has been violated: any comparison of
score against a constant anywhere above the HAL.

Both carry dsd-source: docs/hal-contract/DECISIONS-hal-contract-close.md per the
attestation rule, so each decision points back at the deliberation it came from.

⚠ NOT YET APPLIED: the signature edits themselves. D2 removes a field and a method from
the Protocol block and the capabilities dataclass. Recorded first, applied next — the
ruling is the durable thing and I did not want it living only in a code block.

⛔ AND A FRAGMENTATION TO FIX: this branch holds spec 72 AND all of spec 70's updates
(the green risk, EV-25, the SuperMini answers); master holds only the handover and DSD
work; build/bringup-probe holds the probe. No branch has all of it. That is
[STRATEGY-LAND-IT-OR-IT-DOES-NOT-EXIST] — a branch is where work hides while looking
done. Consolidation proposed to David, not taken unilaterally.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/72-fingerprint-hal-contract.lts | 2 ++
 1 file changed, 2 insertions(+)
## 20f47cf — WIP on master: 8f23005 dsd: rewrite the HAL-close DSD to the ratified bar — every decision gets CONTEXT

**2026-08-17 13:07:22 -0700** · David Burnett



### files

## ffe2082 — index on master: 8f23005 dsd: rewrite the HAL-close DSD to the ratified bar — every decision gets CONTEXT

**2026-08-17 13:07:22 -0700** · David Burnett



### files

## 506f760 — untracked files on master: 8f23005 dsd: rewrite the HAL-close DSD to the ratified bar — every decision gets CONTEXT

**2026-08-17 13:07:22 -0700** · David Burnett



### files


 .../sessions/20260817T005419Z-NEGATIVE-CONTROL.jsonl  | 19 +++++++++++++++++++
 1 file changed, 19 insertions(+)
## 8f23005 — dsd: rewrite the HAL-close DSD to the ratified bar — every decision gets CONTEXT

**2026-08-17 12:40:55 -0700** · David Burnett

Re-presenting on David's ask surfaced that yesterday's version does not meet the
decision-support-doc standard, and the gap is substantive rather than cosmetic.

⛔ WHAT WAS MISSING: `What this is` and `Why it matters` on EVERY decision. Mine had
neither label on any of the six. As of 2026-08-17 the DSD viewer's guard REFUSES to
publish a document missing either field ([CXC-DSDV-CONTEXT-GUARD]); yesterday's send
succeeded only because the guard had not landed yet.

The failure that guard exists to stop is a DSD that renders perfectly and is UNDECIDABLE.
Mine was heading there: D2 asked David to rule on `supports_challenge_match` without ever
saying what a challenge match IS — a 1:1 check against one named person rather than a 1:N
search across everyone. He would have been deciding cold on a surface he last touched
months ago.

⛔ THE JUDGEMENT THAT FAILS HERE IS THE ONE THE SKILL NAMES: deciding a surface is
"familiar enough to skip" is a judgement the author makes about someone ELSE'S memory.

ALSO ADDED, per the same standard:
  - grouped into five themes, structural first, blockers marked ⛔
  - `| Option | Tradeoffs |` tables with an honest cost on every row, no strawmen
  - `My recommendation` as its own labelled part on each decision
  - a Summary at a glance so "recommendations all accepted" is a complete answer
  - ⚠ D1 flagged as WANTING A DISCUSSION, NOT A SLOT. Three shapes all satisfy the
    rulings and differ only in what they make IMPOSSIBLE; if the strongest answer is
    "neither — here's a reframe", that is the tell it was never a fork.
  - moved to the ratified retention home docs/hal-contract/DECISIONS-<topic>.md so the
    dsd-source: attestation on future decision nodes stays valid

Verified before sending: 6 decisions, 6 What-this-is, 6 Why-it-matters. Delivered via
send_dsd_for_human_review; the composeio copy is a drop-dir COPY to delete, not to land.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 docs/DSD-hal-contract-2026-08-16.md               | 201 ------------------
 docs/hal-contract/DECISIONS-hal-contract-close.md | 245 ++++++++++++++++++++++
 2 files changed, 245 insertions(+), 201 deletions(-)
## 4300fdf — dsd: six decisions that close the HAL contract, plus four to ratify

**2026-08-16 19:25:59 -0700** · David Burnett

David asked whether he needs a DSD or just to review. Both — and this is the cheaper
half, so it goes first.

⛔ THIS IS A DECISION QUEUE, NOT AN AUDIT, and the distinction is why no subagent was
dispatched. The last DSD needed an independent agent because it checked whether the
architect had represented David correctly — a thing the architect cannot check about
itself. Every item here is already declared open IN THE SPEC, with alternatives and an
invalidating_assumption recorded. Nothing needed discovering. The doc exists so he
answers six questions instead of reading 1,100 lines.

§1 DECISIONS
  D1  the stepped EnrollmentSession — the largest shape invented rather than derived,
      and it lands in every enrollment call site. Recommended KEEP: it is what makes
      "the HAL has no voice" a compile-time impossibility rather than a review item.
  D2  supports_challenge_match is a ruled field whose operation is defined NOWHERE.
      Recommended DROP — a mandatory announcement with undefined meaning is this
      document's own defect reproduced one layer down.
  D3  what a match score MEANS. A number is already on the wire with no stated meaning;
      a threshold tuned on one sensor is wrong SILENTLY and PERMISSIVELY after a swap.
  D4  four additions the agent made on its own judgement, presented as a set he can
      reject wholesale. write_template is the one without which the roster push has no
      surface to call; template_size_bytes stopped being tidiness the moment David said
      his firmware may be V1.1-era, where a template is 768 bytes and not 1536.
  D5  delete_slot idempotency — a transcription judgement, EV-04 settles it.
  D6  CONTINGENT, do not answer yet: the ring table if EV-25 finds green absent.

§2 RATIFY: the eight containment invariants, the mock-as-canonical-reference amendment,
the fault-model-survives correction, and the separate-regulator lean.

Delivered via send_dsd_for_human_review. ⚠ The viewer requires a repo file and /R503 is
its own repo, so a COPY sits in composeio/compose_projects/bsg/dsds/ — the same route the
last DSD took, and the same disposition: a file to DELETE, not to land. The authoritative
copy is this one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 docs/DSD-hal-contract-2026-08-16.md | 201 ++++++++++++++++++++++++++++++++++++
 1 file changed, 201 insertions(+)
## 9c44e0c — probe(r503): the bring-up probe — and it is shown to FAIL

**2026-08-16 17:51:50 -0700** · David Burnett

Implements [DD-FPID-NO-SERIAL-MOCK-BUILD-A-BRINGUP-PROBE]: no serial-level mock,
instrumentation against the real part. Two pieces, and the split is forced.

THE WIRE LAYER RUNS ON THE S3 (firmware/r503_probe, ESP-IDF, UART1 @ 57600 8N1).
Not the Mac with the S3 as a bridge — inter-byte timeouts, drain-before-transmit
and an INFERRED stream terminator do not survive a USB hop.
  - Length counts content PLUS the 2 checksum bytes [CXC-R503-FRAMING]
  - checksum over PID + both length bytes + content, header/address EXCLUDED
    [CXC-R503-CHECKSUM-VERIFIED] — all 7 worked examples recompute exactly
  - drain-then-transmit AND a sliding two-byte header window. BOTH.
    [CXC-R503-UNSOLICITED-0X55]. No surveyed driver has both.
  - every unknown confirmation code is DATA, never fatal [CXC-R503-CONFIRMATION-CODES]
  - conf= is emitted ONLY for an ack packet (PID 0x07); on a command packet that
    byte is the opcode, and naming it conf asserts a code that does not exist

THE HOST CLI (probe/r503probe.py) owns exploration, session logging and the human.
Resolves the S3 by USB SERIAL at use time, never by cached path; never runs
`esptool chip-id`, which resets the board.

⛔ NO BAUD-CHANGE PATH EXISTS. No SetSysPara helper, firmware or host.
   [CXC-R503-BAUD-SELF-CONTRADICTS] — no recovery from a bricked module is
   documented in any revision. EV-17 and EV-08 are OUT OF SCOPE: both need a
   sacrificial unit and none is marked.

⛔ NO UNBOUNDED WAIT. Deadline AND byte budget on the UART transaction, the drain,
   the listen window, the USB host link and the CLI read loop. FREERTOS_HZ raised
   to 1000 so the reported elapsed_ms means what it says.

NEGATIVE CONTROLS — 16 codec cases + 15 battery cases, run on the real S3
(00:00:5E:00:53:06), R503 not attached. All pass. Includes NC-0, which asserts
NC-1's own predicate against a line that is NOT silent and REQUIRES a FAIL — a
battery that has only ever printed PASS is indistinguishable from one that cannot
print anything else.

Two defects caught by running it rather than reading it: NC-4 originally accepted
both `ok` and `timeout_partial`, so it could not fail; and content[0] was reported
as a confirmation code on packets that are not acknowledgements.

⛔ build.sh is not convenience: this repo's path contains a space ("Data Sheets")
   and PlatformIO refuses outright. The build is mirrored to a space-free path.

Wiring sheet: docs/bringup-wiring-2026-08-16.md. 3.3VT TO 3.3 V, NEVER 5 V.


### files


 .gitignore                                         |   4 +
 docs/bringup-wiring-2026-08-16.md                  | 276 +++++++
 firmware/r503_probe/build.sh                       |  50 ++
 firmware/r503_probe/platformio.ini                 |  27 +
 firmware/r503_probe/sdkconfig.defaults             |  24 +
 firmware/r503_probe/src/CMakeLists.txt             |  17 +
 firmware/r503_probe/src/main.c                     | 697 ++++++++++++++++++
 firmware/r503_probe/src/r503_codec.c               | 181 +++++
 firmware/r503_probe/src/r503_codec.h               | 106 +++
 firmware/r503_probe/src/r503_link.c                | 219 ++++++
 firmware/r503_probe/src/r503_link.h                | 104 +++
 probe/README.md                                    | 163 +++++
 probe/r503probe.py                                 | 808 +++++++++++++++++++++
 .../20260817T005114Z-NEGATIVE-CONTROL.jsonl        |  19 +
 14 files changed, 2695 insertions(+)
## 62b2d8f — spec(r503): apply the held writes — the green risk, the Adafruit explanation, and EV-25

**2026-08-16 17:22:49 -0700** · David Burnett

lts-engine reconnected; the three edits staged at 1d521da are applied through the
guarded writers as intended, and PENDING-lts-writes deleted.

1. [CXC-R503-FEEDBACK-REALIZATION] — the `accept = green` row may be unimplementable.
   V1.1 documents THREE colours (Red/Blue/Purple); green 0x04 first appears in V1.2.1,
   and David reports his parts may be V1.1-era firmware. It lands on his one stated UX
   preference: `ready` in blue-or-purple survives, but he chose those "vs the obvs
   green / red", so the contrast loses a term. Recorded as a RISK, not a finding —
   three-colours is a DOCUMENTATION fact about V1.1, not a measured firmware fact.

2. [CXC-R503-BAUD-SELF-CONTRADICTS] — why Adafruit's seven forbidden constants are not
   carelessness. VERIFIED IN BOTH FILES rather than taken on report: V1.1 carries the
   range at line 121 AND the enumeration N=[1/2/4/6/12] at line 169; V1.4.1 the same
   pair at 285 and 373. Adafruit wasn't missing it — it sat 48 lines below. Reading
   order decided it: the intro table and the range are ENCOUNTERED, the enumeration is
   LOOKED UP. The constants are a faithful implementation of one sentence.
   That is CXC-FPHAL-BOUNDARY-RATIONALE's mechanism caught in the act — the vendor's own
   document, read in its natural order, produces a driver that can brick the part.

3. [CXC-R503-EV-25] — GetFwVer 0x3A, then census all seven colour indices BY EYE.
   The corpus had THREE indirect firmware-vintage discriminators (EV-15 ReadProdInfo
   length, EV-16 security level, EV-21 0x17 survival) and no slot that simply ASKS,
   via an opcode COMMAND-SET has listed all along. Three inferential routes, and the
   direct question unasked.

   ⛔ The dangerous outcome is REPEAT, not absence: if 0x04 silently renders blue, the
   ring says `ready` when it means `accept` and no error code says so.

Slot count 24 -> 25 in slots-total and both frontmatter statements, so the file does not
join the class of documents that disagree with themselves about how many of something
they contain — a defect this corpus has already caught twice.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/70-r503-peripheral.lts           | 26 +++++++++--
 corpus/PENDING-lts-writes-2026-08-16.md | 79 ---------------------------------
 2 files changed, 23 insertions(+), 82 deletions(-)
## 1d521da — corpus: HOLD three .lts writes — the engine's write surface went down mid-edit

**2026-08-16 16:37:16 -0700** · David Burnett

lts_add_property / lts_add_node became unavailable while David improves lts-engine in
parallel. The edits are staged as MARKDOWN rather than hand-written into the .lts,
because raw authoring is exactly where the six known-silent key-region loss classes bite
— and this same session the guarded writer caught an uppercase key of mine
(`...-SAFE-reading`) that my own regex could not match, having no case for consecutive
uppercase. Routing around a missing instrument to save a few minutes is how that class
lands silently.

⛔ THE FINDING THAT PROMPTED THIS — David: "my sensors are so old (years old) that their
firmware may not even support anything beyond Grow's v1.1 spec."

V1.1 documents THREE ring colours: Red 0x01, Blue 0x02, Purple 0x03. GREEN 0x04 first
appears in V1.2.1. So [CXC-R503-FEEDBACK-REALIZATION]'s `accept = green` row may be
UNIMPLEMENTABLE on the parts in hand — and it lands precisely on David's one stated UX
preference: `ready` in blue-or-purple survives, but he chose those "vs the obvs green /
red", so the contrast loses one of its two terms.

⚠ Three colours is a DOCUMENTATION fact about V1.1, not a measured firmware fact. Nobody
has sent an AuraLedConfig to any of these parts. It is a RISK, not a finding — EV-25
settles it in ten seconds.

EV-25 (staged) asks GetFwVer 0x3A and then censuses all seven colour indices BY EYE. The
dangerous outcome is not absence but REPEAT: if 0x04 silently renders blue, the ring says
`ready` when it means `accept` and no error code says so — the machine channel agreeing
with the wrong answer, which is CXC-VISION-EPISTEMICS's whole subject.

⛔ The corpus had THREE indirect firmware-vintage discriminators (EV-15 ReadProdInfo
length, EV-16 security level, EV-21 0x17 survival) and NO slot that simply ASKS via
GetFwVer 0x3A, an opcode CXC-R503-COMMAND-SET has listed all along.

Also staged: the Adafruit baud explanation. Verified in both files — V1.1 carries the
range at line 121 AND the enumeration at line 169; V1.4.1 the same pair at 285 and 373.
Adafruit was not missing it; reading order decided it. And since David reports the lib is
TESTED against real R503s, its three-colour table may be correctly MATCHED to the hardware
generation it was tested on, which makes the characterization's "stale-revision artifact"
framing unfair to it.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/PENDING-lts-writes-2026-08-16.md | 79 +++++++++++++++++++++++++++++++++
 1 file changed, 79 insertions(+)
## 8919ec1 — spec(r503): SuperMini confirmed — and a lean that may sidestep the rail question entirely

**2026-08-16 16:28:33 -0700** · David Burnett

David corrected his own typo: ESP32-S3 SuperMini, not SuperMicro. Board name now
confirmed rather than flagged-as-unverified. The chip family still rests on his word,
because the USB descriptor cannot distinguish an S3 from a C3 — that caveat stays.

ARCHITECT'S LEAN, NOT RULED: don't share the SuperMini's 3V3 LDO with the sensor. Give
the R503 its own 3.3V regulator fed from VBUS, commoning only ground. Three reasons, all
already in this spec rather than newly asserted:

  1. [CXC-R503-RAILS] — the input is UNREGULATED and "directly supplied to the image
     sensor and decoding chip", so a shared rail puts sensor noise on the MCU supply.
  2. [CXC-R503-CURRENT-UNDERSPECIFIED] — no inrush, no peak, no LED figure in ANY
     revision. A shared LDO would be sized against three numbers that do not exist.
  3. §6.5's low-power pattern HARD-SWITCHES VCC repeatedly, and switching a rail the
     MCU also runs on is a brown-out generator.

Why this is worth stating now: it makes the regulator's source capability a question we
may never need to answer, rather than one blocking the bench.

Cost stated honestly: one more part on a bench whose appeal was six wires. And cheap to
defer — nothing above the seam changes either way. This is POWER TOPOLOGY, not contract,
and EV-06/EV-07 measure the same quantities under both.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/70-r503-peripheral.lts | 3 ++-
 1 file changed, 2 insertions(+), 1 deletion(-)
## 1a7db3d — spec(r503): the bench is real — 3.3V part, S3 host, three units, and one trap dissolved

**2026-08-16 16:26:15 -0700** · David Burnett

David, 2026-08-16: "ESP32-S3 SuperMicro. The R503 I have is 3.3v power, not 5v, and
there's no serial number or part number on it. (I have two on my desk, and a third in
inventory.)"

⭐ THE ORDERING TRAP IS DISSOLVED — the best thing in this commit

[CXC-R503-PRE-WHICH-PART] named a circularity: identity needs the wire, the wire needs a
rail choice, so the paper answer must come first. Knowing the rail is 3.3V breaks it
WITHOUT naming the SKU. The part can be safely powered and then ASKED what it is via
ReadProdInfo 0x3C. The SKU stops being a precondition of bring-up and becomes its first
measurement.

⛔ AND A ROUTE THAT NODE CLAIMED IS GONE

Its `how-answered` says "David reads the packaging, the order listing and the module
silkscreen". There is no part number or serial on the module — one of the three named
routes does not exist. Only the order listing survives on paper. R503-5V is excluded;
R503 vs R503S vs R503Pro is not, and every figure in section 1 stays PROVISIONAL until
EV-03 returns a model string.

HOST

S3 is far above the CH32V203 driver-tier floor, so that question does not bind. Its IO is
3.3V and NOT 5V tolerant, which converts [CXC-R503-WAKEUP-TRACKS-3V3T-INFERRED] from a
hypothetical to a binding rule: TIE 3.3VT TO 3.3V.

⚠ Board name recorded AS HE WROTE IT. The widely-sold board of that shape is the
"SuperMini"; silently normalising a board name into a spec is how a wrong part number
propagates. And the regulator's source capability is now the ONLY real electrical
unknown — the architect has NO verified knowledge of it and will not assert one from
memory, because 20mA working with no stated LED figure is exactly where a tiny LDO bites.

⭐ THE SACRIFICIAL MODULE FINALLY EXISTS

[CXC-R503-EV-INDEX]'s sacrificial rule — EV-17 can permanently BRICK a part and EV-08
deliberately overdrives an input — named a constraint nobody could satisfy. Three units
satisfies it. ⛔ But MARK THE VICTIM PHYSICALLY FIRST: the modules carry no serial and no
part number, so they are mutually indistinguishable by inspection, and an unmarked
brick-test unit rotates back into the working set and then fails mysteriously. That is a
self-inflicted instance of the class this whole document is about, and no software step
can do the marking or verify it was done.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/70-r503-peripheral.lts | 5 +++--
 1 file changed, 3 insertions(+), 2 deletions(-)
## 7038413 — handover: the bench hardware — stable identities, and why the path is not one

**2026-08-16 16:22:04 -0700** · David Burnett

David attached an ESP32-S3 to host the device stack. Identified PASSIVELY via
system_profiler + ioreg; never esptool chip-id, which RESETS the board — the reset
being the exact harm an identity check is run to avoid.

  the S3            00:00:5E:00:53:06   -> /dev/cu.usbmodem11301
  an ESP32-C3       00:00:5E:00:53:02   -> /dev/cu.usbmodem31101
  Hosyond FTDI      <device-serial>            -> /dev/cu.usbserial-<device-serial>   ⛔ DO NOT TOUCH

⛔ THE PATH IS A LEASE. David: "It might need to be reconnected when pressing boot."
Native USB re-enumerates on BOOT, so the path moves underneath you. Bind to the full
serial, resolve the path at use time. Measured precedent on this fleet: port paths
changed five times in one session.

✅ The S3 is FREE TO FLASH — no image, no backup (David). Recorded because
preserve-before-overwrite is the correct default and would otherwise cost a round trip.
The clearance is for 00:00:5E:00:53:06 ONLY.

⚠ WHAT I DID NOT ESTABLISH: PID 0x1001 is Espressif's GENERIC native-USB descriptor and
does NOT distinguish an S3 from a C3. "S3" rests on David's word, not on an observation.
The differing OUI distinguishes the BOARD; it identifies no chip family.

⚠ And knowing the chip is not knowing the board. [CXC-R503-PRE-HOST-AND-RAIL-BUDGET]
asks what the 3.3V rail can SOURCE and whether the UART GPIO is 5V tolerant — facts of
the board and its regulator, not the die, and a USB descriptor answers neither.

⛔ The one electrical rule already binding: S3 IO is 3.3V, not 5V tolerant, so per
[CXC-R503-WAKEUP-TRACKS-3V3T-INFERRED] — TIE 3.3VT TO 3.3V. That tracking claim is a
labelled INFERENCE only [CXC-R503-EV-04] can settle.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 docs/handover-2026-08-16/HANDOVER.md | 38 ++++++++++++++++++++++++++++++++++++
 1 file changed, 38 insertions(+)
## 5152c5c — spec(fphal): containment is the PRIMARY purpose — the HAL is a quarantine, not a swap-enabler

**2026-08-16 16:16:06 -0700** · David Burnett

David, 2026-08-16: "we really want to be sure that we have a firm boundary against
anomalous behavior in the R503 libs - and our documentation sweep alone (plus my own
experience working with Chinese fingerprint sensor vendors) indicates to me that we
this is a necessary boundary."

WHAT THIS CORRECTS

Specs 70/71 justify the HAL almost entirely on DE-VENDORING — value realised once, at
a swap that may never happen. Containment pays off continuously, from the first byte.
Stating de-vendoring as primary is what set the architect up to treat the mock as a
stand-in for a second sensor rather than as a FAULT INJECTOR.

⛔ AND IT INVERTS MY OWN PREVIOUS COMMIT. 0e0e940 recorded "WHAT DIES: the mock's
~250-300 line fault-injection model". Backwards. The fault model is the SURVIVING half;
the dict-of-slots store is the disposable one. Two roles were conflated: mock as
development enabler for above-bus components (dead — hardware is six wires away) versus
mock as the only thing that can prove the boundary holds (necessary — you cannot make a
real R503 emit 0x28 on demand).

[CXC-FPHAL-CONTAINMENT-INVARIANTS] — eight guarantees, each tied to a defect already
cited in this corpus, each with a TELL a reviewer can grep for: no unbounded wait
(pyfingerprint hangs the host forever); a wire length never sizes a buffer (R503Lib's
remote stack smash); unknown codes surfaced and non-fatal (0x28 is used and documented
nowhere); desync recoverable via drain AND sliding window (no surveyed driver has both);
every checksum verified (one driver never does, and says so); no partial success
reported as success; contradictory documentation resolves to the SAFE reading (10s prose
vs 15s flowchart, same revision); no baud change ever issued (seven forbidden constants,
no documented recovery from a brick).

HYGIENE

Renamed CXC-FPHAL-CONTAINMENT-IS-THE-PRIMARY-PURPOSE -> CXC-FPHAL-BOUNDARY-RATIONALE:
the old id asserted its verdict and would have carried the conclusion into every
citation. Cited in 3 places, all mine, so free today.

⛔ LINT ATTRIBUTION — warnings read 0 -> 57 and that comparison is INVALID

The lts-engine gained validators mid-session (David confirms it is being improved in
parallel): `unminted`, `malformed_ref` and `profile_validators` are absent from every
earlier lint in this session. 45 of the 49 unminted_reference_list hits are in specs
70/71, which I never touched — pre-existing conditions newly detected, and benign:
every grounds-claim property has a matching GROUNDS triple in its Entity section.

OWED, not fixed: V10 low_density now fires on spec 72 — my two long prose nodes diluted
it. The fix is decomposition into distinctly-keyed bullets, deferred because David
reviews this document next and may reshape these nodes anyway.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/72-fingerprint-hal-contract.lts | 35 ++++++++++++++++++++++++++++++++++
 1 file changed, 35 insertions(+)
## 0e0e940 — spec(fphal): David rules the runtime home — the DEVICE owns driver + HAL, the bus is the interface

**2026-08-16 15:52:55 -0700** · David Burnett

[CXC-FPHAL-PRE-HAL-RUNTIME-HOME] ANSWERED. [DD-FPHAL-HOST-OR-VM-BINDING] CLOSED,
alternative (b). His words: "fingerprint_storage, the editor and the station are
ordinary bus clients."

The seam sits INSIDE device firmware — [CXC-R503-SEAM-RUNS-THROUGH-THE-DEVICE] read
literally rather than as a metaphor. There is no host-side HAL object and nothing
above the bus ever calls a HAL method. The twelve signatures survive unchanged;
they are now firmware structure, not a callable a service imports.

TWO DEPENDENTS DEMOTE RATHER THAN CLOSE

[DD-FPHAL-CONCURRENCY-MODEL] and [DD-FPHAL-FAILURE-RAISED-OR-RETURNED] stop being
fleet-wide signature commitments and become internal device-firmware detail. Five
open decisions become two, and neither survivor blocks a build.

THE MOCK IS LARGELY DISSOLVED — and this is a SHARPENING, not a reversal

David 2026-08-15 said "build with a mock R503"; that assumed hardware bring-up was a
slow parallel path. The premise is false when the part is six wires and a USB cable
away. Two ESP32-C3s confirmed attached (00:00:5E:00:53:02, 00:00:5E:00:53:03 — full
addresses, checked passively via system_profiler, never esptool chip-id).

⛔ WHAT IT COSTS, RECORDED SO IT IS NOT DISCOVERED LATER

[CXC-FPID-EV-14] existed to falsify the de-vendoring by running one suite over two
implementations. Against live hardware alone there is only ever one, so the
de-vendoring goes UNTESTED until a second sensor make exists — and a leaked vendor
concept then surfaces at swap time, the expensive moment.

Proposed substitute, cheaper than what it replaces: [CXC-FPID-SEAM] already states a
GREPPABLE tell. A static lint over everything above the bus for `r503`, opcode
literals, 200, 1536 and 57600 catches leakage CONTINUOUSLY where EV-14 caught it
once, needing no second sensor and no mock. Architect's proposal, NOT ruled.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/72-fingerprint-hal-contract.lts | 4 +++-
 1 file changed, 3 insertions(+), 1 deletion(-)
## 198afd2 — spec(fphal): the fingerprint HAL becomes a CALLABLE CONTRACT, not ten names

**2026-08-16 14:49:44 -0700** · David Burnett

[CXC-FPID-HAL-CONTRACT] specified this surface as one sentence listing ten
operation NAMES. Measured across specs 70 and 71: zero function signatures,
zero type declarations, zero error classes. Every downstream component was
building against a surface nobody had written down.

New file only. Specs 70 and 71 are UNTOUCHED.

  - 45 nodes, 320 edges, 480 properties — every authored property minted,
    verified per node against the authored count rather than trusted from a
    clean report.
  - The Protocol as real code: twelve typed signatures, plus the enrollment
    session, the primitives, the capabilities schema, the failure enum and
    the feedback state set.
  - capabilities() PROMOTED from `candidate-fields` to a normative typed
    schema of seven required fields, no defaults. A mandatory method with a
    candidate schema cannot be implemented consistently twice, and
    [CXC-FPID-EV-14] exists to run ONE suite against TWO implementations.
  - A CLOSED six-axis failure taxonomy with transport numerically disjoint
    from sensor refusal, and a non-fatal `unknown(code)` escape for the 0x28
    class that appears in no revision's table.
  - The forbidden auto-assign enroll mode is made UNTYPEABLE, and the HAL's
    lack of a voice is made STRUCTURAL: the enrollment session type has no
    feedback method and no reference to one.
  - `MockFingerprintHAL` named the CANONICAL REFERENCE, amending
    [CXC-FPID-MOCK-HAL] without editing it — peer-status and
    canonical-reference-status are different properties and both hold. EV-14
    restated as "R503HAL matches the mock", not "they match each other".
  - Per-implementation behaviour tables where every operation cell has an
    answer; the mock's eight-fault model; the parity test with thirteen
    named assertions.

FOUR ADDITIONS were forced by claims above the seam that the ten-name list
does not serve — `write_template`, `template_size_bytes`,
`MatchOutcome.NO_FINGER` and `FailureAxis.UNSUPPORTED`. All four are named
together at [CXC-FPHAL-THE-FOUR-ADDITIONS] so the ratify can reject them as
a set. FIVE [DD-FPHAL-*] decisions are recorded OPEN and NOT decided.

lts_lint: 234 nodes, 30 errors (all pre-existing cross-corpus dangles in 70
and 71 — this file adds ZERO), 0 warnings, key_loss.lost_bullets 0.
lts_preview: lost.total 0, unaccounted 0, coverage 100.0%, declared 45 /
minted 45, newly_dangling 0, delta CLEAN.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/72-fingerprint-hal-contract.lts | 1076 ++++++++++++++++++++++++++++++++
 1 file changed, 1076 insertions(+)
## 77b425c — handover: David reviews the spec before any implementation dispatch — a HARD GATE

**2026-08-16 14:48:14 -0700** · David Burnett

David, 2026-08-16: "I'd like to review before you dispatch for implementation."

Recorded as a gate rather than an intention, because a standing call like this is
exactly what a compact drops. It binds the HAL contract and everything downstream
of it — HAL, mock, R503HAL, fingerprint_storage, editor, enrollment station.

It is [PRINCIPLE-RATIFY-BEFORE-DISPATCH] with DAVID as the ratifier. A clean lint,
a passing preview and a confident subagent report do NOT satisfy it. The gate exists
because the architect spent this session reporting on specs it had not read; the same
failure aimed at a contract would put a builder to work against a surface nobody
checked.

What he gets is not a forwarded subagent report: the architect verifies the engine
output itself first, then brings the Protocol surface, every open [DD-FPHAL-*], and
what the authoring agent had to GUESS at despite holding both specs — the direct
measurement of whether 70 and 71 achieve their purpose.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 docs/handover-2026-08-16/HANDOVER.md | 20 ++++++++++++++++++++
 1 file changed, 20 insertions(+)
## aa8baa8 — spec(fpid): `device` was missing from the ABOVE-THE-SEAM entity list

**2026-08-16 14:08:24 -0700** · David Burnett

Found by READING the spec rather than querying its structure — which is the point.
Three counts existed for one set: frontmatter and §3 said SEVEN,
[CXC-FPID-ABOVE-THE-SEAM] listed SIX, and eight [CXC-FPID-ENTITY-*] nodes exist.

The count is not the harm. ABOVE-THE-SEAM is the node that tells a builder what is
sensor-neutral, and `device` was absent from it while being the ADDRESS every
upstream event is published under — the newest concept in the spec and the one
David's D3 answer promoted from a gap to a prerequisite. A builder reading that
node alone would not know the device model belongs above the seam.

The eighth node is deliberate and now says so: the TEMPLATE is the seam itself, so
it is neither above nor below. Listing it as sensor-neutral would assert the one
thing that is exactly false — its bytes are what a vendor swap destroys.

Same class the spec pass caught for evidence slots (frontmatter said nineteen where
the index said twenty-one) and missed one section earlier.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/71-fingerprint-identity.lts | 3 ++-
 1 file changed, 2 insertions(+), 1 deletion(-)
## 01d68df — handover: add the missing BOOT section, and retire a stale warning

**2026-08-16 13:45:52 -0700** · David Burnett

TWO DEFECTS, both mine, both found by David asking "did you complete your
bootstrapping process?"

1. HANDOVER.md had NO boot section and never mentioned the agent definition.
   Ten sections, none of them "how to start". So the post-compact instance read
   it, felt oriented, and never ran the wake sequence — there was nothing telling
   it to. Now §0: re-read the role def, run the four wake steps in order, THEN
   read the status report. With the warning that step 4 (reason through the dusk
   gifts) is the one a summary cannot carry and the one you will skip, because
   steps 1-3 produce the FEELING of being oriented and step 4 costs real work.

2. The document's most prominent warning — "the two corpus nodes were never
   committed, lts_find does NOT return them" — is STALE. The composeio repo owner
   rescued both in `0aa51c0`, now on origin/main. The work is durable.

⛔ AND THE TRAP IN THE CORRECTION

lts_find returns the node now, and that proves NOTHING about main. The engine
reads the WORKING TREE, and this checkout is on a feature branch. Measured:

  git merge-base --is-ancestor 0aa51c0 main   -> NO
  git show main:...strategies.lts | grep -c   -> 0
  git branch -a --contains 0aa51c0            -> remotes/origin/main

Safe on the remote, absent from local main, visible to queries only because of
where the checkout is standing.

This is the ORIGINAL error one layer up. Version one asked "does the file exist
on main?" and read it as "is my node on main?". The obvious follow-up asks "does
lts_find return it?" and reads it as "is it committed?" — and lts_find answers
identically either way. Ask which tree the instrument is reading before believing
what it says about a different one.

Wake steps 1-3 run this session. Step 4 not yet — stated, not glossed.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 docs/handover-2026-08-16/HANDOVER.md | 66 ++++++++++++++++++++++++++++++++++++
 1 file changed, 66 insertions(+)
## 523ad67 — handover: correct from the independent review — 11 corrections, not 7, and NO GIT REMOTE

**2026-08-16 04:07:14 -0700** · David Burnett

The independent reviewer read the raw transcript (never this handover) and returned
27 silent-failure findings across 10 classes. Corrections folded in rather than argued
with; each verified against the tree myself before landing.

THE ONE THAT THREATENS EVERYTHING ELSE

`git remote -v` is EMPTY. 34 commits, one machine, Dropbox sync only. Every artifact
of this session — both specs, the design narrative, the characterization, the survey,
the tools, the whole for-composeio/ staging set — lives in exactly one place. I never
checked this in eleven hours and the directive never mentions it. Mitigated with a
verified `git bundle --all` (backups/, gitignored, "records a complete history"); that
is not a backup. A real remote is DAVID'S CALL — outward-facing, fingerprint-adjacent.

OTHER CORRECTIONS LANDED

- 45 evidence slots, not 36. The directive said 36 in §1 and 24+21 in §4 — it
  contradicted itself by nine and nobody noticed.
- a0b68e8's message says "rename"; it carries 495 lines of spec. SECOND instance of
  the same `git add` scope error this session — the first was labelled "un-park
  retention". I committed it four hours after writing that one up, while writing the
  handover containing the write-up. History left unrewritten; the collision is the artifact.
- Three of David's late rulings landed under RENAMED ids. Searching the old ones
  returns a clean, confident zero.
- [DD-FPID-DEVICE-ANNOUNCEMENT-SHAPE] is open and DAVID HAS NEVER SEEN IT.

⛔ AND THE ELEVENTH — the error table was itself built from a subset

The five-error version quoted into P14.md omitted the 3.3VT error I owned 600 records
earlier and included a row David had attributed to a different instance. That count then
propagated as "seven". The tally of the subset-as-set failure was assembled from a subset,
inside the dusk gift whose subject IS that failure. Do NOT now write "eleven" as settled:
five, seven and eleven were each reported as a census by someone sure it was complete.
The instability of the count is the finding.

SUMMARY-by-architect.md — the control — is FORFEIT, and says so

It could only be written before reading the reviewer's result, and I read it first. The
procedure never said when to write the control. Writing it now would be an instrument that
can only report success. The file records the forfeit, the prediction it would have tested,
and the fix: write and COMMIT the control between dispatching the reviewer and opening its
result. The commit is what makes it a control — the discipline cannot be "I promise I
didn't peek."

First error this session caught BEFORE it was committed rather than after.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 .gitignore                                         |   1 +
 docs/handover-2026-08-16/COMPACT-DIRECTIVE.md      |   7 +-
 docs/handover-2026-08-16/HANDOVER.md               | 138 +++++
 docs/handover-2026-08-16/SUMMARY-by-architect.md   |  81 +++
 .../SUMMARY-by-independent-claude.md               | 675 +++++++++++++++++++++
 5 files changed, 901 insertions(+), 1 deletion(-)
## 9b5d6fc — spec(fpid): close the ring-progress ordinal gap + stage the apostrophe key-loss defect

**2026-08-16 04:00:57 -0700** · David Burnett

The spec pass folded David's five DSD answers, nine corrections and the three late
rulings into 70/71. Verified independently rather than accepted: lint re-run here
reports 189 nodes, 30 errors (all cross-corpus plt.cux/plt.cio dangles — the known
staging cost at [CXC-R503-PLACEMENT]), 0 warnings, lost_bullets 0. V10 no longer fires.

TWO THINGS THE PASS DID NOT CLOSE

1. The ring-progress vocabulary carries `n of N` but no ORDINAL. The ceremony is
   N presses x TWO templates, and the device is the ONLY ring writer under
   [DD-FPID-RING-IS-DEVICE-OWNED], so it cannot tell press 3 of 6 on the first
   finger from press 3 of 6 on the second — a twelve-press ceremony renders as an
   indistinguishable six-press loop run twice. Report shape is now
   `enroll_progress{ ordinal, capture_n, capture_of }`. Positional ordinal ONLY;
   [CXC-FPID-ORDINAL-SEMANTICS]'s no-finger-naming prohibition is unchanged.
   Architect's call, recorded as open to David's dissent. The gap was opened BY the
   ring ruling — the driver never needed this vocabulary while it held the ring.

2. An APOSTROPHE in a key region mints no property, and lts_lint AND lts_preview
   both certify the file clean (`lost.total 0`, coverage 100.0%, line bucketed
   `ignorable`). A sixth KNOWN-SILENT key-region class, absent from the documented
   five. Confirmed with a two-armed negative control: `david's-requirement` and
   `another's` vanish while `the-requirement-is-davids` — same words, no apostrophe
   — mints. The uppercase class fires loudly and is what hides it.

Staged as item G rather than filed: defects/defect-graph.lts is already modified in
the other agent's tree, which is the collision David ruled against. The fix belongs
to the PUBLIC factlattice repo — David's word alone.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>


### files


 corpus/70-r503-peripheral.lts                      |  45 +++++--
 corpus/71-fingerprint-identity.lts                 |   3 +-
 corpus/events/quality.jsonl                        |   3 +
 for-composeio/DEFECT-G-apostrophe-in-key-region.md | 129 +++++++++++++++++++++
 for-composeio/README.md                            |   8 ++
 5 files changed, 178 insertions(+), 10 deletions(-)
## a0b68e8 — handover: rename out of `session_handover/` — the NAME was the hazard

**2026-08-16 03:52:47 -0700** · David Burnett

David 2026-08-16: recreate the docs in the R503 dir, because creating them in
session_handover conflicts with the other running agent.

VERIFIED FIRST: the docs were already in R503, and nothing was ever created in
composeio's session_handover/ -- that directory is clean and was only READ from
(the two guides). So there was no file conflict.

But the concern holds on a different axis and is worth acting on: the directory
NAME was identical to composeio's. Nothing collided today; a future agent asked
to find "the handover" would have had two plausible homes, and that ambiguity
gets resolved wrongly at 2am. Renamed to docs/handover-2026-08-16/ -- plainly
local, no name a reader could mistake for the composeio one.

Self-references inside the docs updated to match, so the harvest pointer in
HANDOVER.md still resolves.


### files


 corpus/70-r503-peripheral.lts                      | 111 +++++-
 corpus/71-fingerprint-identity.lts                 | 384 +++++++++++++++------
 .../handover-2026-08-16}/COMPACT-DIRECTIVE.md      |   0
 .../handover-2026-08-16}/HANDOVER.md               |   2 +-
 .../handover-2026-08-16}/harvest/README.md         |   0
 .../harvest/ps-composeio-full.txt                  |   0
 .../harvest/queue-operation-channel-evidence.txt   |   0
 .../harvest/transport_mqtt-256-byte-guideline.txt  |   0
 8 files changed, 362 insertions(+), 135 deletions(-)
## 50773d4 — handover(session-1): directive, durable record, and the fixture harvest

**2026-08-16 03:47:56 -0700** · David Burnett

Per GUIDE-preparing-for-compact.md and its harvest addendum.

⛔ The handover LEADS with the riskiest correction, per the guide's rule that a
successor may only read the top: I told David TWICE that two corpus nodes were
"landed in composeio" and "reached at every wake". They are ON DISK ONLY and
were never committed. I had verified with `git cat-file -e main:<path>`, which
proves the FILE exists on main -- not that MY NODES are in main's copy. Right
command, wrong question, returns success either way. lts_find(prefix="DX") does
not return them.

Four artifacts, two written:
- COMPACT-DIRECTIVE.md -- instructions TO the summarizer, ordered per the guide
- HANDOVER.md -- the durable record; state, ledgers, rulings, ten traps
- harvest/ -- three LIVE fixtures copied before they rot
- SUMMARY-by-independent-claude.md -- IN FLIGHT, running against the transcript

The independent reviewer was dispatched BEFORE this handover was written, per
the addendum's binding rule: a reviewer reading the handover reads the thing
that has already stopped being checkable. Measured base rate behind that rule --
across 68 episodes, not one error over ~25 minutes old was self-caught.

The harvest copies rather than points, per the addendum: both of its recorded
near-losses were REPAIRS, not deletions -- a fix consuming its own witness. The
three taken are the ps listing (self-falsifying header), the transport_mqtt
256-byte guideline (which also records its own threshold shipping
already-violated), and the queue-operation channel census, which is the
perishable one because the transcript IS the artifact.

Explicitly NOT harvested: the collaboration wart, tagged PREFERENCE. It hinged
on David's judgment, is not re-derivable, and must never become a gift. Routed
to insights-and-warts instead.

Belief-to-catch latencies recorded where computable: 256-byte ~6 turns,
type:user ~2 (only because a subagent was told to distrust the brief),
"nodes are landed" ~40 turns. Consistent with the addendum's finding that
nothing is self-caught past ~25 minutes.


### files


 .../session_number_1/COMPACT-DIRECTIVE.md          | 128 +++++++++++++++
 session_handover/session_number_1/HANDOVER.md      | 180 +++++++++++++++++++++
 .../session_number_1/harvest/README.md             |  49 ++++++
 .../session_number_1/harvest/ps-composeio-full.txt |  45 ++++++
 .../harvest/queue-operation-channel-evidence.txt   |  23 +++
 .../harvest/transport_mqtt-256-byte-guideline.txt  |  56 +++++++
 6 files changed, 481 insertions(+)
## 680cd8e — handoff(composeio): stage everything destined for the main project

**2026-08-16 02:12:21 -0700** · David Burnett

David 2026-08-16: stop editing the composeio tree -- three agents committing
into one checkout that sits on a feature branch is how "I believed main was X"
happens, and it happened to this architect twice.

Everything bound for composeio now lives in for-composeio/ as a staged handoff
with a 452-line README the other agent can act on without archaeology. Whole new
files sit at destination-relative paths; edits to files the other agent is also
touching are FRAGMENTS with target file, insertion line and anchor text -- never
whole-file copies, which would clobber concurrent work. Each fragment was
verified by reconstruction: base + fragment reproduces the working tree
byte-identically.

Contents: the two corpus nodes (DX-INTERPRETATION-IS-AN-INFERENCE,
STRATEGY-AUDIT-THE-SPEC-AGAINST-THE-HUMANS-OWN-WORDS), P14 + its ps fixture +
the protocol section, the 25-node dusk-gift roster, the seven agent-def edits as
a patch in agents-NOT-MINE/, and the corpus-config fragment.

⛔ The README carries three things this architect got wrong and one nobody knew:
- the citation count is ~91/102, not the ~34 propagated from a subagent without
  counting -- off by 3x, and traceable to a stale property in the roster itself
- main and origin/main have DIVERGED, so the ASAP push is not a fast-forward and
  must not be forced
- "lowercase id" was imprecise; the ids are MIXED case and the parser requires
  all-uppercase
- ⛔ DEFECT 3, which neither the architect nor David knew: registering
  re-derive-via in the profile is a NO-OP today. parser.py:190 excludes it
  deliberately, and the profile route is separately dead -- index_builder never
  forwards property_edges. The profile already documents this for `documents:`
  as DECLARED BUT INERT. So [DEC-PROFILES-MAY-REGISTER-THEIR-OWN-EDGE-KEYS] is
  a sound ruling the engine does not yet honour, and the fix is in the PUBLIC
  factlattice repo -- David's word alone.

And the landing trap, with numbers: git diff --stat main 703a122 falsely shows
-1,764 lines of tonight's already-landed tools and skills, because that branch
is based on bc184c0. A merge would silently revert them. Cherry-pick only.


### files


 for-composeio/README.md                            | 452 ++++++++++++
 .../agents-NOT-MINE/703a122-claude-agents.patch    | 375 ++++++++++
 for-composeio/bootstrap/dusk-gifts/answers/P14.md  | 233 +++++++
 .../dusk-gifts/fixtures/ps-composeio-full.txt      |  45 ++
 .../dusk-gifts/registration-protocol.md.append.md  |  44 ++
 .../config/corpus-config.dusk-gifts.json.fragment  |  19 +
 ...design-excellence-composeplatform.lts.append.md |  23 +
 .../corpora/dusk-gifts/dusk-gift-roster.lts        | 373 ++++++++++
 .../intelligence-console/strategies.lts.append.md  |  31 +
 for-composeio/patches/703a122.patch                | 754 +++++++++++++++++++++
 for-composeio/patches/ca815a4.patch                | 125 ++++
 for-composeio/patches/d0a88ae.patch                | 310 +++++++++
 12 files changed, 2784 insertions(+)
## 337fe4e — design(r503): the event carries DEVICE; location is an attribute of the device

**2026-08-15 23:33:01 -0700** · David Burnett

David: "'Boomer rejected at nuke_console' should be the primary emitted message,
with 'nuke_console is in CIC' as an optional attribute to nuke_console. Location
should be secondary, not primary."

Stronger than 19.1. There the architect had location as an informational
property ADJACENT to the message; David's precision removes it from the event's
vocabulary entirely. The event carries <device_id>/user_detected and
<device_id>/access_granted; the REGISTRY carries nuke_console.location, which
may be "CIC", "Raptor_Two", or absent. A consumer wanting 'at CIC' JOINS them.
A portable device changes one registry attribute and no message shape, no
namespace and no subscription is affected.

Three findings collapse into this one:
- 19.1: a nullable field cannot be an address.
- The auditor's C1: the device registry was flagged as a missing entity the
  message design already depended on. David's answer makes it the HOME of
  location, so C1 stops being a gap to fill later and becomes a PREREQUISITE.
- 16.1: `capability` was removed from the payload because it is derivable from
  the device via the registry. `location` is the identical move on the identical
  join -- and David found the second instance rather than the architect
  generalising from the first.

⛔ The generalisable rule, and why this was worth three rounds: an EVENT carries
the minimum key needed to identify its source; everything else about that source
is an ATTRIBUTE of the source, resolved by join. Putting a source's attributes
in its events duplicates the registry onto the wire, and a duplicate is a thing
that can disagree with its source.


### files


 docs/fleet-identity-design-2026-08-15.md | 32 ++++++++++++++++++++++++++++++++
 1 file changed, 32 insertions(+)
## b4d2e58 — design(r503): location is NULLABLE — which is what settles the address question

**2026-08-15 23:28:42 -0700** · David Burnett

David: "location may be null - it's not intended to be a set of GPS coordinates.
It could be 'CIC' or 'Raptor_Two', but it may simply be unset, and that should
be legal."

⛔ A NULLABLE FIELD CANNOT BE AN ADDRESS. There is no <location>/user_detected
topic for a device with no location. So the portable tricorder is not an awkward
edge case in the namespace design -- it is the counterexample that makes DEVICE
IDENTITY the only viable address. device_id is required and addressable;
location is informational and optional.

Display consequence recorded: a consumer rendering 'Boomer rejected at CIC' must
degrade to 'Boomer rejected at nuke_console' when location is unset, so the
device id is always the fallback -- a second independent reason it is primary
rather than co-equal.

⚠ And an INTERPRETATION recorded AS one, exercising the rule landed an hour ago
([DX-INTERPRETATION-IS-AN-INFERENCE]): treating 'unset' as simply ABSENT, with
no null-vs-absent distinction. The alternative -- that a deliberate null (a
tricorder has no fixed location) differs from an unlabelled absence -- is a
distinction David did not ask for, and inventing it is the over-specification
pattern already corrected twice today. Default recorded, alternative named,
reopen only on his word.


### files


 docs/fleet-identity-design-2026-08-15.md | 25 +++++++++++++++++++++++++
 1 file changed, 25 insertions(+)
## 43df64b — dsd(r503): David's answers — all five decisions, two of them "Neither"

**2026-08-15 23:23:10 -0700** · David Burnett

Synced from the viewer copy. ⚠ NO save-close notification ever arrived; the
answers were found only because David said "I responded to the DSD, IIRC". That
notification path is broken and is its own defect -- an answered DSD that
notifies nobody is indistinguishable from an unanswered one.

D1 · working set -> NEITHER OPTION. "a cast would be universal across all
  fingerprint sensors. 1 game = 1 cast list of up to 10 live players = up to 20
  fingerprint scans. All cast templates get loaded in all fingerprint modules,
  no sub-segmentation by module."
  Neither, because option A said THIS MODULE'S cast, implying modules differ.
  The cast is scoped to the GAME, not the module, and every device gets the
  identical set. Better than A: the uniform index survives WITHOUT the
  per-roster reassignment A would have forced, the library stays unbounded, and
  the ~100-person ceiling dissolves.

D2 · retention clock -> A. Any match resets it, denied or granted.

D3 · location -> NEITHER OPTION. "location was probably not the best term...
  what I was intending was that the device emitting the semantic event was
  identifying itself clearly... device might be the better term than location,
  because the device itself could be portable (think a star-trek tricorder with
  an attached R503)."
  ⛔ Bigger than a rename: PORTABILITY kills the premise. The architect assumed
  a device sits in a room. It is <device_id>/user_detected, and location becomes
  a MUTABLE PROPERTY in the registry rather than an address.
  And this is the auditor's C1 from the other direction -- it flagged the device
  registry as a missing entity the message design depends on; David's answer says
  the message carries device identity. Same finding, two vantages.

D4 · roleless person -> A. Resolves to the empty capability set.
D5 · finger ordinal -> A. Keep the flat prohibition on branching.


### files


 docs/DSD-spec-audit-2026-08-15.md | 526 ++++++++++++++++++++++++++++++++++++++
 1 file changed, 526 insertions(+)
## 9b622f4 — docs(r503): make the audit ROUTINE, not precise — David corrects the posture

**2026-08-15 20:10:04 -0700** · David Burnett

David: 'even if it only catches 50% of real issues, that's 50% more than we
would have caught otherwise.'

That supersedes 18.3's strong/weak grading as the OPERATING question. 18.3 asked
'is this instrument good enough to keep?' -- a one-shot verdict. The right
question is 'is it cheap enough to run every time?', and it is: two scripts and
one subagent. A 50% instrument run FIVE TIMES beats a 90% instrument run once,
so effort spent perfecting the auditor is effort not spent running it again.

⛔ One guard recorded, because it is the only way a partial instrument becomes
actively harmful: its output must never read as COVERAGE. Four findings means
four findings and says nothing about the other 108 nodes. A clean-looking DSD
read as 'the spec was audited and is fine' is the exact failure this corpus
names repeatedly -- the report that LOOKS complete so the reader acts blind
while believing they are oriented ([SBR-TRUST], [LEC-CORPUS-BUILDS-NONEMPTY]).

Requirement on the artifact, cheap: every audit DSD states what it EXAMINED and
what it DID NOT, and never implies the unflagged remainder was verified. With
that guard 50% is pure gain; without it, 50% coverage read as 100% is worse than
no audit at all.


### files


 docs/fleet-identity-design-2026-08-15.md | 24 ++++++++++++++++++++++++
 1 file changed, 24 insertions(+)
## 518f1e7 — tools(r503): verify a DSD's citations — written BEFORE the DSD existed

**2026-08-15 20:08:28 -0700** · David Burnett

David: 'if it gives us a DSD full of hallucinations, that's a sign.' Correct,
and a SPECIFIC one, so this checks for it mechanically rather than by reading.

⛔ Committed BEFORE the auditing subagent reported, deliberately. A checker
authored after seeing the output it checks can be tuned -- consciously or not --
to pass that output. Same pre-registration discipline as 18.4/18.5.

⛔ THE LOCATION OF A HALLUCINATION IS DIAGNOSTIC, and the tool reports the two
halves SEPARATELY rather than averaging them, because averaging destroys the
distinction that says what to fix:

  * fabricated NODE IDS / line numbers -> the BRIEF was over-scoped. The auditor
    was asked to cross-reference 184 nodes and could not hold them. That is the
    DISPATCHER'S error; the remedy is a narrower brief.
  * fabricated HUMAN QUOTES -> far deeper. Those were ~20 short items in a file
    it was told to read. Fabricating them means the METHOD is unsound.

Why this exists at all: a hallucinating auditor is precisely the failure this
project spends its time on -- an instrument emitting confident, plausible, wrong
output -- arriving inside the tool built to catch that. Forwarding its DSD to
David unchecked would be accepting a green report on faith, which is the thing
this whole session has argued against.

SELF-TESTED WITH A NEGATIVE CONTROL before being trusted: run against a document
carrying one real quote, one fabricated quote, two real node ids and one
fabricated node id, it flagged exactly the two fabrications and passed the three
real citations, exit 1. An instrument that can only report success is not an
instrument; this one was shown to fail first.

States its own limit in the output, every run: it proves the DSD is WELL-FORMED,
never that it is RIGHT. Whether a finding is correct or worth acting on is
judgment and belongs to a reader -- [PRINCIPLE-SELF-PROVING-RECEIPT]'s boundary,
which covers only the deterministic half.


### files


 tools/verify_dsd_citations.py | 181 ++++++++++++++++++++++++++++++++++++++++++
 1 file changed, 181 insertions(+)
## 13f4f58 — docs(r503): second pre-registration — David bets on the strong criterion

**2026-08-15 20:06:56 -0700** · David Burnett

David, before the result: 'I bet it will' surface live ambiguities. That is a
bet on section 18.3's STRONG validation row, not the weak one, and it is
recorded because a prediction made after the fact is not a prediction.

The architect takes the same side and states its reasoning as its own
prediction: it caught itself interpreting three times today and ALL THREE were
caught by David, none by itself. A base rate of zero self-catches does not
suggest the remaining gaps were all surfaced.

Names six suspected live ambiguities in advance -- the derived 'etc.' tabs, the
invented `role` layer at hop 5, eight of nine LED states, the entire
capabilities() field list, whether the working set is exactly the cast, and
whether fp:// ordinals are stable across re-enrollment (which silently decides
whether re-enrolling one finger invalidates a stored template_sha256).

Naming them now makes the auditor's findings checkable against the architect's
own guesses -- and makes anything found OUTSIDE the list count for more. A
finding not on this list is the strongest possible outcome, because it means the
instrument sees past BOTH parties, which is the only result that would justify
making this standing practice rather than a one-off.


### files


 docs/fleet-identity-design-2026-08-15.md | 26 ++++++++++++++++++++++++++
 1 file changed, 26 insertions(+)
## 5f1cc19 — docs(r503): pre-register the audit's expected result, before it returns

**2026-08-15 20:05:51 -0700** · David Burnett

Written BEFORE the auditing subagent reports, so its output cannot be
retro-fitted into confirming the method. Negative-control discipline pointed at
our own process instrument.

Two independent predictions on record: the architect withheld its three known
failures from the brief as a blind set, and David predicted unprompted that the
DSD should surface clarification questions about his known-vague statements.

⛔ And a correction to the architect's own framing from one message earlier,
made BEFORE the result because making it after would be worthless: the
validation set is WEAKER than claimed. All three known failures were CORRECTED
BY DAVID IN THE TRANSCRIPT, so an auditor reading the conversation sees both the
error and the fix. Finding them proves it can READ, not that it can catch what
nobody caught.

What would actually validate: surfacing a LIVE ambiguity neither party has
named, or an INVENTED item the architect still does not know about. What would
falsify: a DSD restating things David already said, dressed as discoveries -- an
instrument that restates its input is not measuring.

Also names the one live ambiguity already known (location in the namespace vs
the payload), so that if the DSD raises it, it cannot be claimed as a discovery.


### files


 docs/fleet-identity-design-2026-08-15.md | 39 ++++++++++++++++++++++++++++++++
 1 file changed, 39 insertions(+)
## f17524a — chore(r503): gitignore bytecode — and record the shared-checkout collision

**2026-08-15 20:04:23 -0700** · David Burnett

Untracks a .pyc that a broad `git add -A` swept in, and ignores __pycache__.

⛔ But the .pyc is the symptom. The defect is the architect's: I ran `git add -A`
in a tree where a DISPATCHED AGENT WAS ACTIVELY WRITING. Two commits therefore
contain work their messages do not describe:

  c37967d "un-park retention"  -- also carries a 1,612-line spec replacement and
                                  the deletion of 71-r503-fleet-identity.lts
  29f6cd3 "extract design inputs" -- also carries 565 lines of spec churn + a .pyc

The CONTENT is correct and verified: old file absent from HEAD, new file present,
tree clean, lts_lint 0 errors / 112 nodes / 574 edges / 0 lost bullets. Nothing
was lost. But a commit message is a capture surface in this project, and a commit
claiming 'un-park retention' that actually replaces a 112-node spec misdescribes
itself to whoever reads the log later.

NOT REWRITTEN DELIBERATELY. Rewriting to make the history look clean would hide
that the collision happened, and the collision is the lesson. This commit is the
record instead.

THE RULE, learned the expensive way: never `git add -A` while a dispatch is in
flight against the same tree. Stage explicit paths, or do not commit until the
agent reports. [PRINCIPLE-WORKTREE-ISOLATION] exists for exactly this and does
not apply here only because the work is held OUTSIDE the repo, in a directory
with no worktree discipline -- so the isolation the principle would have provided
had to be supplied by hand, and was not.


### files


 .gitignore                                              |   4 ++++
 tools/__pycache__/extract_design_inputs.cpython-313.pyc | Bin 13098 -> 0 bytes
 2 files changed, 4 insertions(+)
## 7a65e9b — tools(r503): a fourth audit direction — AMBIGUOUS, and it is the preventive one

**2026-08-15 20:03:06 -0700** · David Burnett

David's addition: have the auditing subagent also flag where the architect had
to INTERPRET vague guidance -- "Dave said something vague, you interpreted it
this way, but there's other valid interpretations, and you need to ask Dave to
be more specific about X."

It is the only PREVENTIVE direction. MISSING, ALTERED and INVENTED catch an
error after it is made; AMBIGUOUS raises a question before it becomes one.

And it has a home in existing discipline that was being half-applied:
[DX-SPEC-CARRIES-ITS-EVIDENCE] requires every inference be recorded AS an
inference rather than promoted to fact. That was honoured today for DATASHEET
inferences -- the 3.3VT level claim was explicitly downgraded from fact to
inference -- and NOT for interpretations of the HUMAN'S WORDS, which are the
same epistemic class. The architect had not noticed the asymmetry.

Three measured examples written into the tool, one of them a real error:
- '10 fingerprints per R503' was read as 10 TEMPLATES; David meant 10 IDENTITIES
  x 2 fingers = 20. Arithmetic went into a decision node on the wrong reading
  before he corrected it. AMBIGUOUS catches that before it propagates.
- 'tabs for RBAC, characters, Sensors, ETC.' -- 'etc.' was filled with three
  DERIVED tabs, presented as if specified.
- location in the NAMESPACE vs the PAYLOAD -- he never said which, the architect
  decided, and the real trade-off was never put to him.

Output shape recorded: an AMBIGUOUS finding is a QUESTION, not a defect, and its
natural destination is a decision-support doc -- one pass of 'be specific about
X' rather than four interruptions.


### files


 corpus/events/quality.jsonl    |  1 +
 tools/extract_design_inputs.py | 47 +++++++++++++++++++++++++++++++++++-------
 2 files changed, 40 insertions(+), 8 deletions(-)
## 29f6cd3 — tools(r503): extract the human's design inputs from a transcript, deterministically

**2026-08-15 20:02:14 -0700** · David Burnett

David's proposal, 2026-08-15, and the second form of it is the good one: bracket
a design conversation with greppable sentinels, then hand the SPEC and the
extracted statements to a SUBAGENT that does its own grep and diffs them.

Why the subagent matters more than the automation: the architect cannot audit a
spec against its own memory of the conversation, because that memory is exactly
what failed twice today. An agent that was never in the room has no recollection
that something 'was addressed' and no investment in the spec being complete.
Extraction is deterministic and belongs in code; comparison is judgment and
belongs to a model with INDEPENDENT context.

⛔ Audits BOTH directions -- MISSING, ALTERED, and INVENTED. The third is not an
afterthought: all three of the architect's failures today were INVENTIONS (an
over-specified attempt event, per-site service replication, a device-side
retention requirement), not losses. A one-directional check would have caught
none of them.

Every filter in this script was added because it ACTUALLY LEAKED in a test run,
not because it was anticipated. First run admitted 4 false positives out of 26 --
two Skill bodies, two local-command caveats, an interrupt marker -- each of
which would have been handed to an auditor AS DAVID'S WORDS.

⛔ And the trap worth the whole exercise: a mid-turn message appears twice and
the FIRST copy can be TRUNCATED. Measured at records 708/712 -- 252 chars vs
355, and the missing tail was the load-bearing sentence of the message ('It's
actually extending the ComposeBus concepts down to the interface layer'), the
one the architect had quoted back as the key framing. Naive equality-dedup keeps
the SHORT copy and silently feeds an auditor a design input with its conclusion
amputated -- worse than the duplicate it was meant to fix. The rule is
prefix-dedup keeping the LONGER.

Promotion candidate: this is general tooling, not R503-specific. It extends the
existing transcript-query skill and belongs beside it once this work merges.


### files


 corpus/71-fingerprint-identity.lts                 | 565 ++++++++++++---------
 .../extract_design_inputs.cpython-313.pyc          | Bin 0 -> 13098 bytes
 tools/extract_design_inputs.py                     | 285 +++++++++++
 3 files changed, 611 insertions(+), 239 deletions(-)
## c37967d — design(r503): un-park retention — the objection that killed it was wrong

**2026-08-15 19:55:12 -0700** · David Burnett

David: his six-month auto-delete was tied to a design with regular emission of
identified users; he knew it was a closed loop. He dropped it because it was
'easier to drop the concept than to try to explain why my design was valid'.

He was right. The architect raised a conflict -- an optional user_detected would
let an active player age out -- and then solved that exact problem
INDEPENDENTLY four sections later at 16.1, where the device always publishes
locally and only the UPLINK is configurable. That restores precisely the closed
loop David had described. The objection was wrong when raised, and the architect
reinvented the answer already implicit in the original proposal.

RULING: retention is UN-PARKED and stands as designed -- six-month auto-delete
on fingerprint_storage only, closed because user_detected publishes on every
match INCLUDING RBAC failures. Device-side retention stays separately parked;
that half was never disputed.

Design constraint taken from David's own objection about test cost: the service
must take its clock as an INJECTABLE DEPENDENCY and never read wall time, so a
six-month expiry is a unit test rather than a six-month wait. Cheap designed in,
a rewrite bolted on.

⛔ And the PATTERN, captured as a collaboration wart because it matters more
than the instance. An objection cheap for the architect to raise and expensive
for David to refute does not function as push-back -- it is a TAX on his ideas,
paid silently by good designs being dropped rather than defended.

This INVERTS [PRINCIPLE-PUSH-BACK]. That principle guards against deference
where push-back was warranted. This is the mirror image: push-back where it was
NOT warranted, which loses the same value in the opposite direction and is
HARDER TO SEE -- a suppressed objection is at least visible to whoever suppressed
it, while an idea abandoned under objection-cost is invisible to both parties.

The tell: David concedes quickly on something he had proposed with confidence.
That is not agreement, it is rebuttal cost exceeding item value.

The remedy is not 'object less' but: before raising a conflict, check whether
the human's design already resolves it. Here the closed loop was implicit in
David's own proposal and the architect never looked. Section 16.1's method note
is the same failure in a different costume.

Flagged as a PROMOTION CANDIDATE to insights-and-warts, kind: wart. It is not
R503-specific and [SBR-SELF-AUDIT] says the observer cannot audit itself -- this
is the external light doing exactly what that node says it must.


### files


 corpus/71-fingerprint-identity.lts       | 1612 ++++++++++++++++++++++++++++++
 corpus/71-r503-fleet-identity.lts        |  873 ----------------
 docs/fleet-identity-design-2026-08-15.md |   67 +-
 3 files changed, 1678 insertions(+), 874 deletions(-)
## f2c7dc9 — design(r503): withdraw the attempt event; add `ready`; call the fixture

**2026-08-15 19:51:35 -0700** · David Burnett

⛔ WITHDRAWN: the architect's ATTEMPT EVENT {person, capability, device,
outcome}. David pointed back to his earliest design input this session and it
was recovered VERBATIM from the transcript rather than paraphrased:
user_detected: <user_id> at location: <location_id> plus access_granted:
true/false.

Simpler is better here, not merely smaller. `capability` is DERIVABLE from the
device via the Sensors registry, so carrying it in the payload duplicates what
the registry knows -- and a duplicate can DISAGREE with its source, which is the
CAN_BRIDGE_REGISTER antipattern in miniature. `location` is already the namespace
scope. So nuke_console/user_detected: boomer + access_granted: false ALREADY IS
'Boomer tried to hack the nuclear permission to fire system'; the GM's feed
derives the narrative from the registry, not from a fatter payload.

It also resolves the emit-or-not vs retention conflict BETTER than the invention
did: the device always publishes LOCALLY and what is configurable is the UPLINK
(DX-INTERNAL-BUS-UPLINK). No new message class needed.

METHOD NOTE worth keeping: the architect had this input seven hours earlier and
re-derived a more complex version anyway. It was recovered by grepping the
transcript at David's suggestion. A design input that is not in a queryable home
gets re-derived, worse, by the same instance that received it.

ADDED: a `ready` presentation state ('ready for authentication'), distinct from
`idle`. Two constraints recorded: the COLOURS belong in the R503 HAL spec as a
realization, not here; and reject_silent MUST be visually indistinguishable from
a genuine no_match -- same colour, mode and duration -- or an observant player
learns the tell and the deception is worthless.

ARCHITECT'S CALL on the serial fixture: build a BRING-UP PROBE, not a fake
serial R503. A fake device would test the driver against the architect's GUESSES
about ambiguous datasheet text -- the same blind-spot trap one layer down. The
probe tests the driver against THE PART, and converts the bench hour into a
repeatable script whose output fills the 36 empty evidence slots. House
precedent: board-bringup descriptors and the netlist-derived beep-out sheet.


### files


 docs/fleet-identity-design-2026-08-15.md | 86 ++++++++++++++++++++++++++++++++
 1 file changed, 86 insertions(+)
## 6358d14 — design(r503): two vocabularies that deliberately diverge — the Boomer case

**2026-08-15 19:37:08 -0700** · David Burnett

David, 2026-08-15, and it exposed a latent defect in the architect's LED design.

RATIFIED: the HAL is built on the GENERAL instruction set, not AutoEnroll.

⛔ The sensor's OUTCOME vocabulary and the ring's PRESENTATION vocabulary must
not share tokens. Scenario: Boomer puts her finger on the nuclear-permission
R503; it MATCHES, but fails RBAC, so the ring shows unrecognised while the bus
emits 'Boomer tried to hack the nuclear permission to fire system'.

The architect had proposed match_unknown for BOTH layers. In that scenario the
sensor reports matched(Boomer) while the ring shows unrecognised -- the same
token, opposite meanings, one layer apart. A shared token invites an implementer
to wire the sensor result straight to the ring because the names line up, and
the deception then breaks SILENTLY. Distinct vocabularies make the shortcut
unavailable rather than merely discouraged.

Sensor: matched(person) | no_match | error.
Presentation: accept | reject_silent | reject_known | retry | busy.
reject_silent is Boomer's case; reject_known is 'Access denied, Lieutenant',
which is good drama too. The game chooses per device.

⛔ HAL invariant: the HAL must NOT indicate outcome autonomously. An
implementation that helpfully lights green on a match LEAKS the deception.
Feedback is always commanded, never inferred -- independently another reason
AutoEnroll is rejected, since it mandates its own LED sequence.

⛔ Recorded as INTENT, not behaviour: a future reader seeing 'the ring shows
unrecognised when the match succeeded' would file it as a defect and fix it.
It is designed, and it is consistent with assurance: experiential -- this was
never authentication, so it is free to be theatrical.

And the upstream message is an ATTEMPT RECORD {person, capability, device,
outcome}, not a boolean. It carries person + timestamp, so it doubles as the
last_seen signal retention needs -- which dissolves the emit-or-not vs retention
conflict raised earlier: the attempt record is internal plumbing and always
emitted; the public user_detected message stays optional.


### files


 docs/fleet-identity-design-2026-08-15.md | 54 ++++++++++++++++++++++++++++++++
 1 file changed, 54 insertions(+)
## 0a36f9d — design(r503): the mock is a HAL IMPLEMENTATION, not a fake R503

**2026-08-15 19:22:36 -0700** · David Burnett

David corrected the architect's 14.1/14.2, and the difference is architectural
rather than terminological.

The architect had described a fake R503 -- something emitting 0xEF01 frames,
checksums and a stray 0x55, with the driver speaking the R503 wire protocol to a
pretend device. That tests the R503 DRIVER.

David's intent: the HAL is the vendor-neutral CONTRACT, and MockFingerprintHAL
and R503HAL are two PEER IMPLEMENTATIONS of it. The mock never speaks R503 at
all. That tests everything above the HAL, vendor-neutrally -- and it is what
makes 14.3's de-vendoring structurally real instead of aspirational, because the
layer above has no R503 concepts to unlearn.

His own framing is the right citation: this extends the ComposeBus contract
shape DOWN to the interface layer above the sensor. That is
[CXC-VISION-SEMANTIC-REALIZATION] one level below the bus, and it satisfies
[CXC-VISION-ANNOUNCE-BIND]'s design check -- a mechanism built for level N must
be reusable at N+1.

Consequence: the mock becomes TRIVIAL, an in-memory dict of slots, ~100 lines,
no protocol emulation.

The trap catalogue from 14.2 does not vanish, it RELOCATES: 0x55-after-SoftRst,
the unbounded ack stream, both 1:N readings and non-byte-stable UpChar all live
BELOW the HAL and are exercised only against real hardware. Recorded as two
fixtures at two layers -- the HAL mock is built regardless; a serial-level
fixture under R503HAL is optional and David's call, the trade being whether to
meet the 0x55 desync in a test or on the bench.

⛔ And a requirement David's version needs that the architect's did not, so it
would have been missed: the HAL MUST expose capabilities(). A sensor that cannot
do template readback breaks the identity audit, and without capability
announcement that degrades SILENTLY -- the audit stops proving anything and
nothing says so, which is an instrument that can only report success arriving by
the back door of a vendor swap. With it, the layer above degrades honestly.


### files


 docs/fleet-identity-design-2026-08-15.md | 57 ++++++++++++++++++++++++++++++++
 1 file changed, 57 insertions(+)
## 09db41c — design(r503): HAL + mock, de-vendor the taxonomy, and the LED ring

**2026-08-15 19:19:02 -0700** · David Burnett

David's fourth batch, 2026-08-15.

RULING: write a small R503 HAL against a MOCK and run hardware bring-up in a
parallel path. This DISSOLVES section 13's Blocker 2 -- 'nothing has touched
hardware' stops gating the service and the editor.

⛔ But the mock has a trap, recorded as its spec: a mock built from the same
datasheet reading as the driver AGREES WITH THE DRIVER'S MISUNDERSTANDING. So
the mock's behaviour spec is the 21 bench questions, and it is SWITCHABLE
wherever the datasheet is ambiguous -- 0x55 after SoftRst as well as power-on,
the unbounded AutoIdentify stream, 0x1F on overflow, both readings of the 1:N
timing, both ReadProdInfo lengths, and a mode where UpChar is NOT byte-stable
since the verifier now depends on that. A mock that only does the happy path
certifies a driver that only handles the happy path.

RULING: de-vendor the taxonomy; R503 survives only on the sensor side of the
HAL. The seam is THE TEMPLATE -- an opaque vendor-specific blob with a
vendor-specific ceremony and store. Above it: person, character, casting,
capability, fingerprint/cmd/*, fp:// refs, roster intent. Below: slots, ModelID,
opcodes, the 6-capture ceremony, driver: r503.

Wrinkle stated rather than left to be discovered: template_sha256 is neutral as
a mechanism but the BYTES are not portable, so swapping vendors means
re-enrolling everyone. Inherent to biometrics, not a flaw.

Flagged rename-now-or-never: 71-r503-fleet-identity.lts and its CXC-R503FI-*
ids are misnamed under this ruling; nothing outside these two files cites them
yet, so it is free today and expensive next week.

LED RING, never previously discussed: define SEMANTIC STATES, never colours --
one semantic value, many realizations. The driver maps onto 7 colours; nobody
upstream knows a colour exists. Flagged that the ring is an actuator with TWO
writers (driver during a transaction, device otherwise) and proposed that
arbitration without taking it.

RECOMMENDED: build the HAL on the GENERAL instruction set, not AutoEnroll.
Three converging arguments -- AutoEnroll mandates its own LED behaviour so we
lose the semantics; no surveyed driver implements it and its ack stream is the
most untested behaviour on the part; and the general path lets us choose the
capture count. Explicitly deferred to David where his fingerprint background
beats the architect's: a capture-QUALITY reason to prefer on-module sequencing
would outweigh all three.


### files


 docs/fleet-identity-design-2026-08-15.md | 91 ++++++++++++++++++++++++++++++++
 1 file changed, 91 insertions(+)
## 835d62d — docs(r503): the 256-byte cap is a GUIDELINE, not a constraint — third assertion-without-reading

**2026-08-15 19:15:08 -0700** · David Burnett

David asked the obvious question the architect had not: are bus messages
constrained to 256 bytes even on MQTT? They are not, and transport_mqtt.py:512
says so in as many words -- DP-CIO-18's 256 bytes is a DESIGN guideline for
constrained substrates, NOT a runtime alarm for MQTT. Two constants exist:
GUIDELINE_PAYLOAD_BYTES = 256 (logged at INFO) and LARGE_PAYLOAD_WARN_BYTES =
256 KiB (the real threshold). Kilobyte payloads are routine there; the console's
self/deliverables measures ~110 KB.

Corrected position: on MQTT a 1536-byte template is ONE ordinary message, no
chunking and no indirection needed for transport reasons. Chunking is a CAN/UART
concern where config_chunk's uint16 length field gives a hard 65,535 B ceiling.
The semantic contract -- fp://<person>/<ordinal> + template_sha256 -- is
identical either way, and how bytes move is a transport detail, which is
DP-CIO-6's position exactly.

⛔ Recorded as a PATTERN, not just a fact. This is the THIRD time in one session
the architect asserted a property without reading the bytes that decide it: the
ReadIndexTable verifier (section 4 erratum), the 3.3VT level trap stated as fact
when it was an inference, and now a remembered design principle applied as a
runtime constraint -- stated flatly, twice. Each was caught by someone else
going to the source: a spec-writer, a survey agent, and David.

Also carries forward a live hazard from the same code comment: that warn
threshold SHIPPED ALREADY-VIOLATED at 70,333 bytes and became 27,009 runaway
warnings in five days, 46% of the console log. Its own lesson is that an
absolute value is a clock, not a fix. A 20-template roster is ~30 KB if framed
as ONE payload -- so frame it per-template, not per-roster.


### files


 docs/fleet-identity-design-2026-08-15.md | 39 ++++++++++++++++++++++++++++----
 1 file changed, 34 insertions(+), 5 deletions(-)
## fe2d0ea — design(r503): withdraw two over-engineered recommendations; the verdict

**2026-08-15 19:13:39 -0700** · David Burnett

David reversed both of the architect's §11.5 recommendations, and the reasoning
is worth keeping in both cases.

1. Templates MAY flow over ComposeBus. The architect implied an out-of-band
   path; David's counter is a cost that was never weighed -- an out-of-band path
   is a SECOND PROTOCOL TO MAINTAIN, for no security gain. The posture is
   explicit and is not a gap: we are not trying to make this secure.
2. Per-site fingerprint_storage replication is PARKED as overkill until
   multi-site gameplay exists, which is expected but not present.

What survives is arithmetic, not policy: a template is 1536 bytes and a bus
message caps at 256, so templates ride ComposeBus CHUNKED via existing transfer
machinery. The architect had conflated 'which wire' with 'which message shape';
separating them dissolves the apparent conflict with the corpus rule. The kill
switch is unaffected -- fingerprint_storage is still the only thing that serves
those bytes, whatever wire they use.

RATIFIED: fp://<person>/<ordinal> + template_sha256, which closes BOTH open
decisions at once because the hash IS the verifier. Consequence flagged: whether
UpChar returns byte-stable output was a curiosity while verification was
undecided; it is now load-bearing for the whole audit path and must be answered
on hardware before that path is built.

Section 13 answers David's direct question -- implementation ready? The design
is; the build is not, for two separate reasons: one decision still gates the
software contract (the spec 27 vs 32 namespace conflict, which is composeio
work), and 36 evidence slots are empty because nothing has touched hardware.

And the useful half: FIVE pieces can start immediately with zero dependency on
hardware, on any open decision, or on the R503 at all -- record_table, theme
unification, scene-level focus, the confirm-dialog composite, and bindable
dropdown options. All composeio-repo work, all independent, all needed by the
editor and useful to the platform regardless.


### files


 docs/fleet-identity-design-2026-08-15.md | 96 ++++++++++++++++++++++++++++++++
 1 file changed, 96 insertions(+)
## 6325949 — design(r503): fingerprint_storage is NEW — the reason is a kill switch

**2026-08-15 19:06:31 -0700** · David Burnett

David's second batch of rulings, 2026-08-15.

CLOSED: fingerprint_storage is a new service, and the rationale is BLAST-RADIUS
CONTAINMENT, not tidiness -- an extension of bus_storage cannot be killed
without killing generic storage with it. He wants one process to stop that stops
every path a template can travel. That rationale then CONSTRAINS template_ref:
a generic doc:// id would let any storage client fetch a template and silently
defeat the switch.

RECOMMENDED (his call, he asked for detail not a verdict): template_ref is a
service-private locator fp://<person>/<ordinal> PLUS a template_sha256. The two
open decisions are ONE -- if the hash rides in the roster, the expensive
identity audit (UpChar the slot, hash it, compare) becomes nearly free to
implement, because the reference and the verifier are the same object. Named the
two honest costs: UpChar is a multi-packet burst no surveyed driver handles
safely, and byte-stability of a read-back template is a bench question.

NEW: two sites over VPN for remote players. This makes the kill-switch concern
the DEFAULT behaviour of a naive push. Recommendation: replicate the service per
site; the VPN carries roster INTENTIONS, each site serves bytes locally, so
templates are scope:local by construction. Flagged that this is the first real
use case for the "remote" scope value, which [CXC-VISION-SCOPE-MEMBRANE] records
as never specified -- and that specifying it is a PLATFORM call, not an R503 one.

CONFIRMED: physical boundaries are real, so 10.10's assumption holds and the
flat RBAC model is sound rather than merely convenient.

FocusManager must be a SCENE-LEVEL capability defaulting OFF -- game surfaces
outnumber data-entry ones and a focus ring on a Colonial console is a visual
regression on shipped art.

GLASS RESULT: David ran it. text_input works with a physical keyboard, so the
gate question is now a glass result rather than a code read. The virtual
keyboard overwrites on every keypress -- exactly the KNOWN LIMITATION the survey
predicted from the DSL having no string concatenation.

NOTE: amended. The first version of this message lost the word "remote" to zsh
backtick command substitution inside a double-quoted -m string. The word was
load-bearing -- "the  scope value" names nothing -- and a commit message is a
capture surface here, not packaging. Author commit messages from a quoted
heredoc, never a double-quoted -m with backticks in it.


### files


 docs/fleet-identity-design-2026-08-15.md | 112 +++++++++++++++++++++++++++++++
 1 file changed, 112 insertions(+)
## 598aeeb — design(r503): RBAC is not module-scoped — the DEVICE is the scope

**2026-08-15 18:41:57 -0700** · David Burnett

David ruled it 2026-08-15, confirming the architect's lean but with better
reasoning than the lean had. Roles are stable and global; module-scoped
permissions are unnecessary because PHYSICAL REACHABILITY already does that
work. The XO always holds nuclear authorization; a module that puts him
planetside just does not put him near the device that exercises it.

Adds a third scoping axis that costs nothing to build: what (role), who
(module + casting), WHERE (physical device placement). And it vindicates
'enroll everyone everywhere, differentiate in RBAC' -- with placement doing the
scoping there is no remaining case for per-device roster differentiation, so the
uniform index survives intact.

Names the assumption it rests on rather than leaving it implicit: that sets are
physically separated. If everyone sits in one room, 'planetside' is fiction the
hardware cannot enforce. Architect's lean is that the fiction carries it and we
build nothing. Recorded NOT as a problem but so a successor does not discover it
and reopen a settled call.

Also lands the editor reuse survey (docs/editor-reuse-survey-2026-08-15.md):
75 widgets not 18, text input exists and is deep, and three findings that shape
the build -- no data-driven collections, two competing theme systems, and focus
covering only 7 widget types.


### files


 docs/editor-reuse-survey-2026-08-15.md   | 138 +++++++++++++++++++++++++++++++
 docs/fleet-identity-design-2026-08-15.md |  35 +++++++-
 2 files changed, 172 insertions(+), 1 deletion(-)
## a59b153 — design(r503): game module — the second entity the IA exposed

**2026-08-15 18:34:30 -0700** · David Burnett

David ruled casting off the tab bar and onto a 'start a game' screen, and the
REASON introduced a new entity: different game modules offer different playable
characters, so casting is (module, person, character), not (person, character).

Second time the IA has corrected the domain model in two exchanges -- capability
in 10.6, game module here. Recorded as a METHOD, not a coincidence: asking 'what
are the screens' is a different vantage on the same model, and it found what the
entity diagram could not see about itself.

Also resolves something section 5 left dangling. The working-set push had no
natural moment and read as an admin chore someone must remember. It has one:
the cast for this module IS the working set, so 'start a game' triggers the
push as a consequence rather than a task.

That makes full resync the NORMAL rhythm rather than an occasional cost, which
strengthens the desired-state ruling: every session converges by construction
and a device that missed a push self-heals at the next game start instead of
staying silently wrong. A delta protocol would have been actively worse -- more
pushes, more chances to miss one.

New open question recorded: is RBAC module-scoped (a matrix) or does the module
only gate character availability (a simple link)? Architect leans the latter;
not decided. It touches two tabs and hop 5 of the match chain, so it is cheap
now and expensive later.


### files


 docs/fleet-identity-design-2026-08-15.md | 50 ++++++++++++++++++++++++++++++++
 1 file changed, 50 insertions(+)
## 0f26dad — design(r503): the editor IA — and the entity David's example exposed

**2026-08-15 18:31:41 -0700** · David Burnett

David gave the initial tab structure: RBAC definition, character def + RBAC
linkage, Sensors mapping R503s to the capability they gate.

His example -- 'nuclear permission to fire vs jump the ship' -- surfaced an
entity the model was missing. Those are CAPABILITIES, and they are the join
between two of his tabs: RBAC maps role -> capabilities, Sensors maps device ->
gated capability. Section 1 had RBAC as an attribute of character; it is its own
layer. The match chain is now FIVE hops, superseding the four in section 1.

Worth noting HOW it was found: the IA exposed a gap in the entity model. The tab
list was not cosmetic -- it was load-bearing on the domain model, and asking for
it produced a correction no amount of staring at the entity diagram would have.

Each tab is an entity, which makes the tab set derivable rather than a matter of
taste. Three tabs are David's; three are derived and marked as such.

Casting flagged as the odd one out -- the only session-mutable entity among
set-up-once ones, so it may be a 'start a game' screen rather than a peer tab.
Architect leans separate; NOT DECIDED.

Tab shell must be declarative, not hardcoded, or the later restyle rewrites it.


### files


 docs/fleet-identity-design-2026-08-15.md | 43 ++++++++++++++++++++++++++++++++
 1 file changed, 43 insertions(+)
## 6b6bb7d — design(r503): the editor — David ratifies the client/owner boundary

**2026-08-15 18:30:49 -0700** · David Burnett

David 2026-08-15, proactively confirming what had been the architect's proposal:
the editor is a CLIENT of fingerprint_storage, never its owner. Now his ruling,
not a proposal, and recorded as such. Consequence made explicit: every editor
operation must exist as a bus operation, or bulk import cannot be scripted,
restore cannot run headlessly, and a second surface must reimplement it.

Flagged as a PROMOTION CANDIDATE to design-excellence -- the editor/service
boundary is not R503-specific -- deferred only because the work is held outside
the repo.

Plus two directives:
- decompose maximally, reuse existing primitives (DX-NARROW-WAIST at the UI
  seam); survey the palette before authoring any widget.
- he will restyle to the Colonial Fleet aesthetic LATER, which is only cheap if
  the first build keeps style separable from structure: theme constants
  canonical, composites in .compose.yaml, no widget hardcoding a hex value.
  Otherwise 'functional first, pretty later' silently becomes 'build it twice'.

And manual delete as a service operation with an editor affordance, soft-vs-hard
left open.

Four editor questions recorded unsettled, the sharpest being whether a text
entry primitive with caret/selection/focus exists at all -- an editor requires
typing, and its absence would change the whole approach. Under survey now.


### files


 docs/fleet-identity-design-2026-08-15.md | 80 ++++++++++++++++++++++++++++++++
 1 file changed, 80 insertions(+)
## ff3c67a — docs(r503): park device-side retention; withdraw an over-read requirement

**2026-08-15 18:22:33 -0700** · David Burnett

David scoped six-month auto-delete to fingerprint_storage ONLY. The architect
had widened it into a device-side requirement (a retention deletion forcing a
roster convergence cycle) that was never asked for; withdrawn.

The residue -- whether a device's flash may keep a template for a person the
library deleted -- is recorded as PARKED so a successor can find it, and marked
do-not-reopen-without-David so finding it is not an invitation.


### files


 docs/fleet-identity-design-2026-08-15.md | 9 ++++++++-
 1 file changed, 8 insertions(+), 1 deletion(-)
## 6c7212f — docs(r503): the false verifier claim was in the design TWICE — fix the second

**2026-08-15 18:19:33 -0700** · David Burnett

The erratum in 46e98af corrected cost #3 and left the identical assertion
standing four lines above it, in the bullet that argues FOR the uniform index.
A correction that leaves the original claim intact elsewhere in the same file is
not a correction; a reader scanning the argument would have taken the wrong one.

Found by grepping for the claim rather than trusting that fixing the place I
remembered writing it was sufficient.


### files


 docs/fleet-identity-design-2026-08-15.md | 7 +++++--
 1 file changed, 5 insertions(+), 2 deletions(-)
## 46e98af — spec(r503): the fleet identity layer — and an ERRATUM on my own verifier

**2026-08-15 18:18:54 -0700** · David Burnett

Spec: 71-r503-fleet-identity.lts, 59 nodes, 12 evidence slots (4 of them
negative controls), 6 PRE questions, 3 open DD nodes. Density 29.44 against a
floor of 21.0, measured with the engine's own density_of_file and calibrated by
reproducing the sibling's 20.44 exactly. lts_preview: 59 declared / 59 minted,
873 lines at 100% coverage, 0 unaccounted. 0 orphans corpus-wide.

⛔ ERRATUM, and it is the important half of this commit.

The design narrative (e0d45ee) claimed the uniform fleet index could be verified
by comparing ReadIndexTable 0x1F against the declared roster. THAT IS FALSE.
0x1F returns 256 slots in 32 BYTES -- one bit per slot. It reports OCCUPANCY,
not IDENTITY. Two devices holding different people in the same slots produce
IDENTICAL bitmaps, so the verifier I specified would have reported 'converged'
on precisely the divergence the uniform index exists to detect.

That is an instrument that agrees with the wrong answer -- the exact failure
class [CXC-VISION-EPISTEMICS] catalogues -- authored into the design by the
architect hours after citing it. It was caught by the spec-writer going to the
manual instead of trusting the narrative, which is the external-light discipline
working as designed.

Corrected to two tiers, and the split is testimony vs evidence:
  - cheap/continuous: occupancy bitmap + the module's 512-byte power-off-
    protected NOTEPAD (WriteNotepad 0x18 / ReadNotepad 0x19) carrying the roster
    id+hash. Detects missed, partial and stale pushes. Does NOT prove identity.
    Trap: notepad pages are write-whole, 32 bytes at a time.
  - expensive/periodic: UpChar 0x08 a slot and hash it. PROVES slot N holds
    template X, at 1536 bytes a slot -- an audit, not a per-match check.

The spec raised [DD-R503FI-IDENTITY-VERIFICATION-METHOD] from this finding and
left it open with four alternatives, flagged in a provenance property as raised
by the spec rather than by the design.


### files


 corpus/71-r503-fleet-identity.lts        | 873 +++++++++++++++++++++++++++++++
 corpus/events/quality.jsonl              |   1 +
 docs/fleet-identity-design-2026-08-15.md |  31 +-
 3 files changed, 903 insertions(+), 2 deletions(-)
## e0d45ee — design(r503): the fleet identity model — library vs working set, uniform index

**2026-08-15 17:59:26 -0700** · David Burnett

David's rulings 2026-08-15, written down before a spec agent is spent on them so
the REASONING is what gets redlined, not the output.

Load-bearing calls:
- Four entities, not one: person owns the template, character owns the RBAC,
  casting binds them and is session-scoped. Re-casting a game therefore needs
  NO re-enrollment and NO template push. The device index is keyed by PERSON.
- Two storage tiers: the fingerprint_storage service is an UNBOUNDED library;
  an R503's store is a bounded working set. This is not a new pattern -- it is
  ComposeIO's 'bus storage is truth, local flash is cache' applied to templates.
  A roster push is a SELECTION, not a mirror.
- The fleet index is UNIFORM across devices (David's model, adopted over the
  architect's weaker per-device maps): one correct assignment makes divergence
  mechanically detectable by diff, where N drifting maps make a difference
  indistinguishable from a fault. Costs: the service owns slot allocation,
  AutoEnroll auto-assign is FORBIDDEN, and it needs a verifier or it is only a
  convention.
- Its precondition: enroll everyone everywhere, differentiate in RBAC, never in
  enrollment. Otherwise slot 4 is a different person on different devices. Holds
  to ~100 people, not 10.
- Roster is DESIRED STATE; 'add' is sugar. At 30 KB a full resync is affordable
  between rounds, so the delta path saves seconds and costs silent drift.
- 1 or 2 templates per person; finger_1/finger_2 are ORDINALS, not anatomy.
  Schema permits N, game design fixes 2, overflow fails loud on 0x1F.
- user_detected is the sensor's, access_granted is the DEVICE's, location is
  neither -- it goes in the namespace, not the payload.

Section 8 lists five things deliberately NOT settled, including what 'by
reference' means for a template, which blocks buildability.


### files


 docs/fleet-identity-design-2026-08-15.md | 228 +++++++++++++++++++++++++++++++
 1 file changed, 228 insertions(+)
## a0e0993 — spec(r503): close MATCH-LOCALITY sensor-side; re-home the latch decision

**2026-08-15 17:53:17 -0700** · David Burnett

David, 2026-08-15, two rulings in sequence:

1. Fail-locked/fail-open is scoped to the DEVICE THAT INTEGRATES an R503, not
   to the R503. [DD-R503-LATCH-FAILSAFE] was a boundary error on my part -- the
   sensor has no idea a hatch exists, and the corpus attaches fail-safe defaults
   to the control-class ACTUATOR. Retained as a do-not-decide-here pointer so a
   reader does not mistake its absence for it being open here.

2. Fleet population: ten identities x both index fingers = at most TWENTY
   templates per device. That collapses [DD-R503-MATCH-LOCALITY]: the blocker
   was the 1:N<10ms/Fingerprint ambiguity, worth 2s at a 200-template library,
   but only 20 x 10ms = 200ms at twenty -- noise against the ~0.7s
   capture-dominated transaction. Sensor-side wins under BOTH readings, so the
   ambiguity no longer gates the architecture. EV-13 stays worth running; it
   now tests our understanding rather than deciding anything.

Also amended the two nodes that still asserted the ambiguity 'decides the fleet
architecture' -- TRUE against 200 templates, FALSE at twenty. Amended rather
than deleted: the reasoning is what a successor re-derives from.

lts_lint: 0 errors, 16 warnings (15 = the known out-of-repo placement dangles,
recorded at [CXC-R503-PLACEMENT]; 1 = density 20.4 vs floor 21.0, incurred by
these amendments and accepted -- the alternative is deleting reasoning to hit a
metric). The status-not-a-bare-token warning is fixed in this commit.


### files


 corpus/70-r503-peripheral.lts | 15 ++++++++-------
 1 file changed, 8 insertions(+), 7 deletions(-)
## d339521 — spec(r503): the R503 peripheral spec — 72 nodes, 24 evidence slots, all EMPTY

**2026-08-15 17:48:57 -0700** · David Burnett

Authored by factlattice-editor against docs/R503-characterization-2026-08-15.md,
reusing the [CXC-PERIPH-EVIDENCE-SLOTS] pattern the TM1637 spec defined.

Measured, not asserted:
- lts_preview: 72 declared / 72 minted, 0 minted-nothing, 1137 lines at 100%
  coverage, 0 unaccounted property-shaped lines, 294 edges.
- 24 EV slots, 0 filled; 6 PRE questions, all OPEN; 5 DD nodes, all NOT MADE
  and all carrying invalidating_assumption.
- Three negative-control slots (EV-22/23/24) added beyond the characterization's
  21, per rule-negative-control-is-a-slot.

The 15 dangling refs are a STANDING PROPERTY of holding this corpus outside the
composeio repo -- every one targets a platform node that lives there. Proven,
not assumed: re-linting with the cited specs in scope yields 104 findings, zero
of them attributable to this file. Recorded in-graph at [CXC-R503-PLACEMENT] so
a later reader does not read the dangle report as a defect.

NO R503 HAS EVER BEEN CONNECTED TO ANYTHING. Every hardware-observable claim
here is unverified and says so.


### files


 corpus/70-r503-peripheral.lts | 1137 +++++++++++++++++++++++++++++++++++++++++
 corpus/events/quality.jsonl   |    1 +
 2 files changed, 1138 insertions(+)
## ebc7775 — docs(r503): device characterization from the five manual revisions + six drivers

**2026-08-15 17:28:28 -0700** · David Burnett

Documents only; no hardware touched. Every hardware-observable claim is
UNVERIFIED and labelled DATASHEET FACT / INFERENCE / DERIVED FACT per
[DX-SPEC-CARRIES-ITS-EVIDENCE].

Load-bearing findings:
- The 0x55 unsolicited byte fires after SoftRst 0x3D, not only at power-on.
  A driver handling it only at boot desyncs on its first soft reset.
- No R503 driver of the six implements AutoEnroll 0x31 / AutoIdentify 0x32,
  so the intermediate-ack stream is untested by every available reference.
- AutoIdentify with Config1=1 and Config2=0 can emit an UNBOUNDED ack stream
  (inference) -- a fixed-buffer hazard.
- The manual's only reference circuit is for the 5V R307. Built as drawn it
  puts 5V on an R503 pin 1.
- WAKEUP tracking 3.3VT is an INFERENCE, not a fact; SOURCES.md and the
  project launcher both assert it flatly. Status downgraded deliberately.
- The connector changed MX1.0--6P -> SH1.0--6P in V1.4.1 and the changelog
  does NOT record it. Pitch is a calipers question, not a document question.
- Checksum rule verified against all 21 worked examples in the document.

Section 7 is 21 falsifiable bench questions; they become the spec's
POST-INTEGRATION evidence slots.


### files


 docs/R503-characterization-2026-08-15.md | 617 +++++++++++++++++++++++++++++++
 1 file changed, 617 insertions(+)
## 49733bc — R503: baseline — vendor manuals, extracted text, provenance, launcher

**2026-08-15 17:16:09 -0700** · David Burnett

Tracks the five manual revisions (SHA-256s in SOURCES.md), the pre-extracted
layout text, and the scoped launcher. Vendored upstream driver clones and the
18MB vendor bundle are ignored: both carry provenance in SOURCES.md and are
re-fetchable, and neither is work this project generates.

Durability baseline for R503 work held outside the composeio repo per David
2026-08-15 (work stays here until it is solid enough to land).


### files


 .gitignore                                      |    7 +
 R503-5V_user_manual_V1.3.1_2023-09.pdf          |  Bin 0 -> 1852712 bytes
 R503_R503-M22_user_manual_V1.4.1_2023-09.pdf    |  Bin 0 -> 1908857 bytes
 R503_R503-M22_user_manual_V1.4_2023-07.pdf      |  Bin 0 -> 1903449 bytes
 R503_user_manual_V1.1_2019-06.pdf               |  Bin 0 -> 569164 bytes
 R503_user_manual_V1.2.1_2022-03.pdf             |  Bin 0 -> 2119412 bytes
 SOURCES.md                                      |  417 +++++
 extracted-text/R503_V1.1_pdftotext-layout.txt   | 1350 ++++++++++++++
 extracted-text/R503_V1.2.1_pdftotext-layout.txt | 2116 ++++++++++++++++++++++
 extracted-text/R503_V1.4.1_pdftotext-layout.txt | 2195 +++++++++++++++++++++++
 extracted-text/R503_V1.4_pdftotext-layout.txt   | 2168 ++++++++++++++++++++++
 launch-r503-architect.sh                        |  174 ++
 12 files changed, 8427 insertions(+)
