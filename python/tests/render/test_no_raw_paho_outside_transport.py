"""SEAM LINT — no file in ComposePlatform may import `paho` unless it IS the transport.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], generalized from the source ROSTER to
the WHOLE REPO. This file is the repo-wide companion to
``test_sources_have_no_paho.py``: that one asks "does every REGISTERED console data
source stay above the waist?", this one asks "is there anything ANYWHERE that reaches
below it?". Neither subsumes the other and they are deliberately not merged — the
roster guard checks a rich pattern set (client construction, `composeio/` topic
literals, `loop_*`, `MQTTTransport(`) over a small derived file set; this one checks
ONE pattern (the paho import) over every `.py` in the repo.

WHY REPO-WIDE, AND WHY A RATCHET
--------------------------------
Phase 2a converted 17 of the 20 roster sources, but nothing stopped a file OUTSIDE
that roster from importing paho — which is exactly how the bypass surface grew to
~14 unscoped files while the roster guard stayed green. A one-time sweep would leave
the same hole open, so this is not a sweep: the ``ALLOWED`` table below is SEEDED with
today's verified bypass set (sweep of 2026-08-10), which is why this test is green the
minute it lands. Its value is entirely in what it forbids NEXT — this is the missing
gate of [DEC-BUILT-TESTED-NEVER-REGISTERED-IS-A-CLASS], applied to a substrate import
instead of to a daemon registration.

THE RATCHET RUNS IN BOTH DIRECTIONS. That is the whole design:

  * a NEW file importing paho that is not in ``ALLOWED`` -> RED. It bypasses the
    ComposeBus waist: it hardcodes MQTT, cannot ride CAN or UART, and is invisible to
    scope-based selective uplink.
  * a file IN ``ALLOWED`` that no longer imports paho -> ALSO RED. Without this leg an
    allow-list rots into permanent permission: entries accumulate, nothing ever leaves,
    and the "temporary" backlog becomes the documented architecture. A converted file
    must be REMOVED from the table in the same commit that converts it, and the only
    thing that reliably enforces that is a failing test.

WHY AST AND NOT A SUBSTRING GREP
--------------------------------
The scan parses each file with ``ast`` and walks ``Import`` / ``ImportFrom`` nodes.
Two concrete reasons, both observed in this repo:

  1. A substring grep for "paho" FALSE-POSITIVES on prose. ``compose_projects/intelligence/tools/
     spawned_windows.py`` contains the line "This keeps the tracker testable with a
     fake proc and unentangled from paho/skia." — a docstring boasting that it does
     NOT use paho. A grep-seeded allow-list would have listed it, and the reverse leg
     of this ratchet would then be permanently unsatisfiable. (This was caught seeding
     this very file; see `test_a_docstring_mention_is_not_an_import`.)
  2. Every real paho import in this repo is a LAZY, function-body import
     (``import paho.mqtt.client as mqtt`` inside a method, so a broker-free process
     never pays for it). A line-anchored ``^import paho`` regex misses all of them;
     ``ast.walk`` sees them regardless of nesting or indentation.

WHAT IS OUT OF SCOPE (excluded from the scan, NOT allow-listed)
--------------------------------------------------------------
  * ``test_*.py``, and anything else under a ``tests`` directory — a pytest module
    legitimately drives raw MQTT to VERIFY the transport, and its file set churns with
    every new test. Making the transport's own tests allow-list entries would be noise
    that never shrinks.

An exclusion and an exemption are different things and are kept different on purpose:
an exclusion says "this is not the governed surface", an exemption says "this IS the
governed surface and here is the one-line reason it is permitted anyway".

THE `compose_projects/bsg/` EXCLUSION IS GONE — WHY, AND WHAT IT COST (2026-08-13)
---------------------------------------------------------------------------------
The seeding commit excluded the whole BSG tree with the rationale "a separate project,
IP-excluded from the MIT release. It is not part of the platform surface this seam
governs."  That sentence conflates two unrelated scopes, and the conflation is the
ESCAPE ROUTE this file was written to close, reproduced inside the file itself:

  * a LICENSING scope answers "which files ship in the MIT tarball?"
  * an ARCHITECTURAL scope answers "which files must reach the bus through the waist?"

They are not the same set and there is no reason they would be. BSG is not a bystander
to the platform — it is the platform's largest CONSUMER and its proof-of-thesis app
(the DRADIS game is the reason the decentralized model exists). Excluding it meant the
single biggest body of bus-facing application code in the repo was the one body of code
the seam lint could not see. Two raw-paho publishers lived there in full view of a green
test: `world/publish.py` (an `mqtt.Client`, a `loop_start`, four hand-composed
`composeio/global/...` subscribe filters, and a hand-built `composeio/global/{ns}`
publish) and `game_hardware/gm_mcp/publisher.py` (the same egress, mirrored on purpose).
Both are now converted; neither could have been FOUND while the exclusion stood.

The general lesson, which matters more than the two files: an exclusion keyed on an
ADDRESS is unfalsifiable from inside the guard. Leg 2 can rot an ALLOWED entry, because
an entry names a file the scan still looks at. Nothing can ever rot a `_EXCLUDED_TREES`
prefix — it silently covers every file that is ever added beneath it, forever, and it
grows without anybody deciding anything. So the bar for a tree exclusion is now: it must
describe a property of the CODE (a pytest module drives raw MQTT to verify the
transport), never a property of the PROJECT the code belongs to.

EXEMPTION BY STATED RULE, NOT BY FILENAME — THE BRIDGE CLASS
------------------------------------------------------------
`services/can_mqtt_bridge/bridge.py` used to be a NAMED entry in `LEGITIMATE`. It no
longer is, and its sibling `services/uart_mqtt_bridge/bridge.py` never was — which is
the whole problem with a filename list. The two are the same KIND of thing, and only one
of them had been noticed. A hand-maintained roster of "things that are allowed" goes
stale the instant somebody writes the second one, and going stale silently is the exact
failure class this repo keeps paying for.

So the bridge exemption is now a RULE with a checked PREDICATE (`_bridge_exemption`):

    a module under a package directory named `*_bridge/` may name MQTT
    IF AND ONLY IF it also names a SECOND, non-MQTT substrate.

Why that is the right rule and not just a looser allow-list. A Transport lets ONE app
speak ONE substrate; a BRIDGE translates between TWO, and naming both is not a bypass —
it is the entire job description. But "lives in a folder ending in `_bridge`" is an
ADDRESS, and we just finished saying addresses are unfalsifiable. So the address alone
does not exempt anything: the rule also asks the file to PROVE it is a bridge by naming
a second substrate (SocketCAN / `transport_can` / `AF_CAN`, or UART / `transport_uart` /
`serial`). A module that names only MQTT inside a `*_bridge/` directory is not a bridge —
it is a bypass wearing a bridge's directory name — and it stays RED.
(`test_a_bridge_directory_alone_does_not_exempt_anything` pins that, with a synthetic
file, so the rule has been SEEN to refuse.)

The rule needs no leg-2 ratchet, and that is its point: there is nothing to delete when a
bridge is retired. Delete the bridge and the rule stops applying to it, automatically. A
list would still be sitting there, still green, still lying.

SMOKE DRIVERS ARE IN SCOPE AND NAMED, NOT EXCLUDED BY THEIR ADDRESS
------------------------------------------------------------------
David's ruling, 2026-08-10: "if a smoke driver has a legitimate need for direct paho —
independent validation — put it on the whitelist explicitly."

``python/tests/render/smoke_*.py`` are hand-run DRIVERS, not pytest modules. They live
under a ``tests`` directory only by address; several are arguably production tooling.
The first cut of this file let the directory rule cover them incidentally, which is
wrong in a specific and load-bearing way: an exclusion keyed on WHERE A FILE LIVES stops
protecting it the moment the file moves. If the drivers are ever relocated (out of
``python/tests/`` and into ``tools/`` or ``scripts/``, which has been discussed), a
directory rule would silently stop covering ~10 files and nobody would get a red test —
the exact silent-skip failure this whole file exists to prevent. A NAMED entry survives
the move: the path changes, leg 2 fires, and a human re-affirms the exemption.

So ``smoke_*`` is carved OUT of the tests exclusion by `_in_scope` and each driver that
really imports paho is a `LEGITIMATE` entry below. Two consequences worth stating:

  * NO DOUBLE COVERAGE. A file is either excluded or scanned, never both. If a listed
    file were also excluded upstream it could never appear in `_FOUND`, so its entry
    could never rot — a permanent permission wearing an allow-list's clothes. That is
    why the carve-out is in `_in_scope` rather than a special case inside leg 2.
    (`test_a_listed_smoke_driver_is_actually_scanned` pins it.)
  * ONLY ONE DRIVER QUALIFIES. Determined by the AST scanner, not by filename — and the
    filenames actively mislead here. NINE drivers are named ``smoke_*_mqtt.py`` and none
    of them import paho: they reach the broker through `connect_transport("mqtt", ...)`
    and `external_bus.publish_state(...)`, i.e. THROUGH the waist, which is exactly
    right and needs no exemption. Listing them on the strength of their names would have
    tripped leg 2 immediately.
"""
from __future__ import annotations

import ast
import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] class 1 — LEGITIMATE BY DESIGN. Permanent entries.
# --------------------------------------------------------------------------- #
# These are not debt. Something has to actually speak MQTT; the architecture's claim
# is that the set of somethings is SMALL, NAMED, and sits AT the waist rather than
# under it. Do not "fix" these — converting them is a category error.
LEGITIMATE: dict[str, str] = {
    "python/composeio/transport_mqtt.py":
        "IS the MQTT Transport — the concrete implementation behind the `Transport` "
        "ABC that `transport_registry.build({'type': 'mqtt'})` returns. If this file "
        "did not import paho there would be no MQTT substrate at all.",
    "python/composeio/bus_client.py":
        "RESIDUAL, and the rationale here is CORRECTED (2026-08-13). The previous "
        "wording claimed it 'sits AT the waist (callers hand it namespaces, never "
        "topics)'. Measured against the code, that is false: `subscribe()`'s second "
        "parameter is literally named `topic_filter`, its own selftest passes "
        "'composeio/+/self/a', and `acquire()` hands the caller a live paho client to "
        "call `.publish(topic, payload)` on. It is a CONNECTION POOL below the waist, "
        "not a seam at it. What makes it harmless today is not its shape but its "
        "REACH: `acquire()` and `subscribe()` — the only two functions that touch paho "
        "— have ZERO callers outside this module (verified repo-wide 2026-08-13). The "
        "live surface is two PURE helpers with no substrate in them at all: "
        "`source_bus` imports `parse_mqtt_url`, `transport_mqtt` imports "
        "`topic_matches`. So the paho in this file is unreachable from production, and "
        "it is the fold target named in [CXC-SOB-ENDSTATE]; when that fold lands the "
        "entry disappears WITH THE MODULE, not by conversion. Do not build on it.",
    "tools/source_capture_diff.py":
        "RECLASSIFIED from the conversion backlog on inspection (2026-08-10): it is "
        "the SOB-C2 capture INSTRUMENT, not a publisher. It imports paho solely to "
        "monkeypatch `mqtt.Client = RecordingPahoClient` process-wide, and never "
        "constructs a real client, connects, or reaches a broker. Its `.publish(` is "
        "the FAKE's own recording method. Naming the substrate is the entire point — "
        "the paho boundary IS the wire it diffs, and it is what proves a conversion "
        "changed no bytes. Converting it would destroy the verification instrument "
        "for every remaining entry below.",
    "python/tests/render/smoke_storage_viewers.py":
        "INDEPENDENT OBSERVER (David's ruling, 2026-08-10). Its `leg_live_mqtt` builds "
        "two bare paho clients to assert `_app/docstore/open` and `_app/doc/open` "
        "ACTUALLY REACHED THE REAL BROKER. Verifying the bus THROUGH the bus would "
        "make the check share the blind spot with the thing under test — the same "
        "argument that makes `source_capture_diff.py` permanent. It is a hand-run "
        "DRIVER, not a pytest module, so it is deliberately NOT covered by the tests "
        "exclusion: named here, its exemption survives a relocation "
        "([CXC-SOB-THESIS]'s guard must not be defeated by a `git mv`).",
    "compose_projects/intelligence/launcher/launcher.py":
        "PERMANENTLY EXEMPT, [CXC-SOB-EXEMPT] class 1 — RECLASSIFIED from the backlog "
        "2026-08-11, resolving the ORDERING CONFLICT the seeding commit flagged. It "
        "hand-frames its envelope to keep a paho-only, `composeio`-import-free STARTUP "
        "footprint, because it is what SPAWNS the harness: an import graph that reaches "
        "the bus would have to be healthy before anything could start, which inverts the "
        "dependency. That is a real constraint, not an oversight, and it is asserted from "
        "the other side too — `test_sources_have_no_paho.py::test_the_permanently_exempt_"
        "launcher_still_exists_and_still_hand_frames` requires `'paho' in text` and pins "
        "that no new top-level `composeio` import appears. Converting it turns that "
        "sibling test RED, and the two records now AGREE that it must not be converted. "
        "Do not 'finish the sweep' by touching this file.",
}

# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] class 2 — NOT YET CONVERTED. This table must only ever SHRINK.
# --------------------------------------------------------------------------- #
# EMPTY since 2026-08-11. The backlog seeded here on 2026-08-10 held 12 entries;
# all 12 are gone, 11 by CONVERSION and 1 (`launcher.py`) by RECLASSIFICATION to
# class 1, which closed the ORDERING CONFLICT the seeding commit flagged rather
# than leaving it for the next reader to re-litigate.
#
# Converted: chat.py, console_supervisor.py, poc_driver.py, channel_bridge.py,
# deliverable_opener.py, app_launcher.py, decisions_curator.py,
# dsd_viewer_launcher.py, wled/senders.py, dsd_review.py, dradis_scenario.py.
#
# TWO ENTRIES CLAIMED A MISSING CAPABILITY, AND BOTH CLAIMS WERE TRUE. They were
# not deferred out of laziness, and neither could be fixed inside the module:
#
#   * `channel_bridge` / `dsd_viewer_launcher` / `chat.py` address
#     `compose_projects/intelligence/<id>/in` — the composeio-channel MCP's own wire, a bare topic
#     with a bare payload. `publish_state` cannot express it without changing the
#     bytes, which [CXC-SOB-CONTRACT-FROZEN] forbids. Fixed by ADDING the
#     capability at the waist: `Transport.send_foreign` / `subscribe_foreign`,
#     surfaced as `ComposeIOBus.publish_foreign` / `subscribe_foreign`. It refuses
#     a `composeio/` address, so it cannot become the next bypass.
#   * `wled/senders.py` needed per-device credentials on a registry-built
#     transport. `MQTTTransport` always accepted `username`/`password`;
#     `transport_registry._mqtt` simply never passed them through. One seam, added.
#
# THE RULE THAT KEEPS THIS TABLE EMPTY: a new entry must name the SLICE that
# removes it, and if the reason is "the waist cannot express X", the fix is to
# extend the waist — as above — not to widen this list. An allow-list that grows
# to accommodate a missing capability has stopped being a ratchet and become the
# documented architecture.
NOT_YET_CONVERTED: dict[str, str] = {}

# The one table the scan is compared against. Split above purely so a reader can tell
# "permanent by design" from "debt with a scheduled end" at a glance.
ALLOWED: dict[str, str] = {**LEGITIMATE, **NOT_YET_CONVERTED}


# --------------------------------------------------------------------------- #
# discovery
# --------------------------------------------------------------------------- #
# Build/cache dirs and vendored trees. Nested agent worktrees (`wt-agent-*`) are
# skipped by prefix: they are full repo copies and would double-count every file.
_SKIP_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "env", "node_modules",
    ".pytest_cache", ".mypy_cache", ".eggs", "build", "dist",
}
_SKIP_DIR_PREFIXES = ("wt-agent-",)

# Out of scope — see the module docstring. Repo-relative, posix-style.
#
# DELIBERATELY EMPTY. It is kept as a named, empty tuple rather than deleted so that
# adding a tree exclusion is a VISIBLE act that lands on this comment: a prefix here
# is the one construct in this file that NOTHING can ever falsify — leg 2 can rot an
# ALLOWED entry because the entry names a file the scan still reads, but an excluded
# subtree silently swallows every file added beneath it, forever. It held
# "compose_projects/bsg/" until 2026-08-13, on a LICENSING rationale that was quietly
# doing ARCHITECTURAL duty; the module docstring records what that cost. If you are
# about to add one: the justification must be a property of the CODE, never of the
# project the code belongs to.
_EXCLUDED_TREES: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] class 3 — EXEMPT BY STATED RULE. No table, nothing to rot.
# --------------------------------------------------------------------------- #
# See the module docstring for the argument. The rule:
#
#     a module under a package directory named `*_bridge/` may name MQTT
#     IF AND ONLY IF it also names a SECOND, non-MQTT substrate.
#
# A Transport lets ONE app speak ONE substrate. A BRIDGE holds TWO live substrates and
# translates between them — naming both is its job description, not a bypass. But the
# DIRECTORY is only half the test: an address exempts nothing on its own, so the file
# must PROVE it is a bridge by actually naming the other substrate. Only then does the
# exemption apply.
#
# Tokens, not imports, and deliberately so. The strongest bridge VERIFIERS in this repo
# re-implement the far substrate from the kernel ABI rather than importing a library —
# `deploy/can_mqtt_bridge/loopback_test.py` packs SocketCAN frames with stdlib `socket`
# and re-derives CRC-16/CCITT-FALSE from the polynomial, precisely so that a property
# read back through the same assumption that wrote it cannot self-verify. An
# import-based predicate would call that file a non-bridge and be exactly backwards.
_SECOND_SUBSTRATE_TOKENS: dict[str, tuple[str, ...]] = {
    "CAN": ("AF_CAN", "CAN_RAW", "socketcan", "SocketCAN", "transport_can",
            "CANTransport", "python-can"),
    "UART": ("transport_uart", "UARTTransport", "pyserial", "serial.Serial",
             "termios"),
}


def _second_substrates(source: str) -> list[str]:
    """Which non-MQTT substrate families *source* names. Empty == not a bridge."""
    return sorted(fam for fam, toks in _SECOND_SUBSTRATE_TOKENS.items()
                  if any(t in source for t in toks))


def _under_bridge_package(rel: str) -> bool:
    """True if any DIRECTORY on the path is named `*_bridge`.

    Directories only — the filename is excluded on purpose, so renaming a module to
    `mqtt_bridge.py` cannot buy an exemption. A bridge is a PACKAGE (it has a config
    server, a frame codec, a verifier); a single file that calls itself a bridge is a
    file that calls itself a bridge.
    """
    return any(part.endswith("_bridge") for part in rel.split("/")[:-1])


def _bridge_exemption(rel: str, source: str) -> str:
    """The stated reason *rel* is exempt as a bridge, or '' if it is not.

    Returning the REASON rather than a bool is the point: [CXC-SOB-EXEMPT] says an
    exemption is a LISTED exemption, and a rule-based exemption has no list to read the
    reason off. So the rule generates it, and `test_the_bridge_rule_states_its_reasons`
    prints the whole set — a reader can still ask "what is exempt and why" and get an
    answer, without a human having maintained one.
    """
    if not _under_bridge_package(rel):
        return ""
    families = _second_substrates(source)
    if not families:
        return ""
    return (f"BRIDGE ({'+'.join(families)}<->MQTT): under a `*_bridge/` package AND "
            f"names a second substrate, so it holds TWO live substrates by design. A "
            f"Transport lets one app speak one substrate; a bridge translates between "
            f"two. Exempt by RULE, not by name — there is no entry to go stale.")


# A hand-run driver, not a pytest module. IN SCOPE wherever it lives — see the
# docstring: an exemption keyed on a directory dies at the first `git mv`.
_DRIVER_PREFIXES = ("smoke_", "drive_", "gen_")


def _is_driver(rel: str) -> bool:
    return rel.split("/")[-1].startswith(_DRIVER_PREFIXES)


def _is_test_file(rel: str) -> bool:
    """A pytest module may legitimately drive raw MQTT to verify the transport.

    Drivers are explicitly NOT tests even when they share an address with them.
    """
    if _is_driver(rel):
        return False
    parts = rel.split("/")
    return "tests" in parts[:-1] or parts[-1].startswith("test_")


def _in_scope(rel: str) -> bool:
    """Exactly one verdict per file — a file is scanned or excluded, never both.

    Leg 2 can only work if every ALLOWED entry is reachable by the scan; an entry
    for an out-of-scope file could never rot. So the driver carve-out belongs HERE,
    upstream of both legs, and not as a special case inside either of them.
    """
    if any(rel.startswith(t) for t in _EXCLUDED_TREES):
        return False
    return not _is_test_file(rel)


def paho_imports_in(source: str, filename: str = "<scan>") -> list[int]:
    """Line numbers of `import paho...` / `from paho... import ...` in *source*.

    AST-based, so a mention of "paho" in a docstring, a comment or an unrelated
    string literal is NOT a hit, and a lazy import nested inside a function body IS.
    An unparseable file yields no hits (it cannot import anything either).
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "paho" or a.name.startswith("paho.") for a in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "paho" or mod.startswith("paho."):
                lines.append(node.lineno)
    return sorted(set(lines))


def _scan() -> tuple[dict[str, list[int]], dict[str, str]]:
    """Every in-scope .py file in the repo that imports paho -> its import lines.

    Also returns the SOURCE of each hit, because the bridge rule is a predicate over
    the file's content and re-reading from disk in the predicate would let the two
    disagree about what the file says.
    """
    found: dict[str, list[int]] = {}
    texts: dict[str, str] = {}
    for dirpath, dirs, files in os.walk(_REPO_ROOT):
        dirs[:] = [
            d for d in dirs
            if d not in _SKIP_DIRS and not d.startswith(_SKIP_DIR_PREFIXES)
        ]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = Path(dirpath) / fn
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if not _in_scope(rel):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "paho" not in text:          # cheap prefilter; AST decides
                continue
            hits = paho_imports_in(text, filename=str(path))
            if hits:
                found[rel] = hits
                texts[rel] = text
    return found, texts


_FOUND, _TEXTS = _scan()

# rel -> the stated rule-reason it is exempt. Derived, never hand-written.
_RULE_EXEMPT: dict[str, str] = {
    rel: why for rel in _FOUND
    if (why := _bridge_exemption(rel, _TEXTS[rel]))
}

_FIX = (
    "The fix is never 'add it to ALLOWED'. Reach the bus through its API:\n"
    "  * a transport comes from `composeio.transport_registry.build({'type': ...})` "
    "— mqtt / can / uart / synthetic / replay are all registered there;\n"
    "  * an in-process source takes an INJECTED bus handle, a standalone one uses "
    "`composeio.source_bus.acquire_bus(...)`;\n"
    "  * every publish is `bus.publish_state(ns, value, source=...)` and every "
    "subscribe is `bus.subscribe(ns, cb)` — a NAMESPACE and a SCOPE, never a topic "
    "string and never a client."
)


# --------------------------------------------------------------------------- #
# the ratchet — leg 1: nothing NEW may reach below the waist
# --------------------------------------------------------------------------- #
def test_no_unlisted_file_imports_paho():
    offenders = {rel: lines for rel, lines in _FOUND.items()
                 if rel not in ALLOWED and rel not in _RULE_EXEMPT}
    detail = "\n".join(
        f"  {rel}:{','.join(str(n) for n in lines)}" for rel, lines in sorted(offenders.items())
    )
    assert not offenders, (
        f"{len(offenders)} file(s) import paho and BYPASS THE COMPOSEBUS WAIST "
        f"([CXC-SOB-THESIS]):\n{detail}\n\n"
        f"A file that names paho has hardcoded MQTT: on a CAN-only or UART-only "
        f"device it publishes into the void, with no error at all, and it is "
        f"invisible to scope-based selective uplink.\n\n{_FIX}"
    )


# --------------------------------------------------------------------------- #
# the ratchet — leg 2: the allow-list may only SHRINK
# --------------------------------------------------------------------------- #
def test_allow_list_has_no_entry_that_no_longer_imports_paho():
    """The leg that makes this a ratchet instead of a permission slip.

    An entry that outlives its reason is the same silent-skip hazard as an unlisted
    bypass, one step removed: the table stops describing reality, and the next reader
    trusts it. So a file that has been CONVERTED (or deleted, or renamed) must fail
    here until its entry is removed.
    """
    stale = []
    for rel, why in sorted(ALLOWED.items()):
        if rel in _FOUND:
            continue
        if not (_REPO_ROOT / rel).is_file():
            stale.append(f"  {rel} — NO LONGER EXISTS (deleted or renamed)")
        else:
            stale.append(f"  {rel} — CONVERTED: still on disk, no longer imports paho")
    assert not stale, (
        "ALLOWED contains entries that are no longer true:\n"
        + "\n".join(stale)
        + "\n\nDELETE them from ALLOWED (in `LEGITIMATE` or `NOT_YET_CONVERTED`) in "
          "the SAME commit that converted/removed the file. This list must only ever "
          "shrink — an allow-list that never loses entries has stopped being a "
          "ratchet and become the documented architecture."
    )


# --------------------------------------------------------------------------- #
# hygiene on the table itself
# --------------------------------------------------------------------------- #
def test_every_allow_list_entry_carries_a_reason():
    """An exemption is a LISTED exemption ([CXC-SOB-EXEMPT]); an unexplained entry
    is a review reject — that rule is the only thing between an allow-list and a
    silent skip."""
    for rel, why in ALLOWED.items():
        assert why and len(why) > 40, (
            f"{rel} has no real rationale ({why!r}). One line of WHY, mandatory."
        )


def test_the_two_classes_are_disjoint():
    """A file is either permanent-by-design or scheduled debt. Both is meaningless
    and would let a `LEGITIMATE` reason mask a `TODO`."""
    both = sorted(set(LEGITIMATE) & set(NOT_YET_CONVERTED))
    assert not both, f"listed in BOTH classes: {both}"


def test_every_backlog_entry_is_marked_todo():
    """`NOT_YET_CONVERTED` is a work queue. `grep 'TODO: convert'` must find all of
    it, so the backlog is discoverable from outside this file."""
    missing = [rel for rel, why in NOT_YET_CONVERTED.items() if "TODO: convert" not in why]
    assert not missing, f"backlog entries missing a `TODO: convert` marker: {missing}"


# --------------------------------------------------------------------------- #
# the scanner's own contract — proving AST beats grep, and that the exclusions bite
# --------------------------------------------------------------------------- #
def test_a_docstring_mention_is_not_an_import():
    """The `spawned_windows.py` case, encoded.

    That module's only occurrence of "paho" is a docstring line saying it is
    *unentangled* from paho. A substring-seeded allow-list would have listed it, and
    leg 2 of the ratchet would then fail forever on a file that was never a bypass.
    """
    src = (
        '"""A tracker, unentangled from paho/skia."""\n'
        "# import paho.mqtt.client as mqtt  (removed 2026-08-10)\n"
        'NOTE = "we used to import paho here"\n'
        "import json\n"
    )
    assert paho_imports_in(src) == []


def test_a_lazy_nested_import_is_an_import():
    """Every real paho import in this repo is inside a function body. A line-anchored
    `^import paho` regex would miss all of them; the AST walk does not."""
    src = (
        "def connect():\n"
        "    try:\n"
        "        import paho.mqtt.client as mqtt\n"
        "    except ImportError:\n"
        "        return None\n"
        "\n"
        "class C:\n"
        "    def go(self):\n"
        "        from paho.mqtt.client import Client\n"
        "        return Client\n"
    )
    assert paho_imports_in(src) == [3, 9]


def test_scope_rules_exclude_tests_and_nothing_else():
    assert not _in_scope("tests/peer/test_can_bridge_migrate.py")
    assert not _in_scope("python/tests/render/test_sources_have_no_paho.py")
    assert not _in_scope("compose_projects/intelligence/publisher/test_singleton_hardening.py")
    assert not _in_scope("python/tests/render/conftest.py")
    # ...and do NOT over-exclude: EVERY application tree stays in scope.
    assert _in_scope("compose_projects/carousel/app.py")
    assert _in_scope("python/composeio/transport_mqtt.py")
    assert _in_scope("compose_projects/intelligence/tools/spawned_windows.py")


def test_the_bsg_project_is_governed_like_every_other_consumer():
    """The regression that let two raw-paho publishers live in plain sight.

    This assertion is the INVERSE of the one it replaces. Until 2026-08-13 this file
    asserted `not _in_scope("compose_projects/bsg/world/publish.py")` — the guard was
    pinning its own blind spot as a requirement, which is why nobody re-derived it.

    BSG is IP-excluded from the MIT release. That is a PACKAGING fact and it has no
    bearing on whether a module may hardcode MQTT. If it is ever re-excluded, it must
    be for a property of the code.
    """
    assert _in_scope("compose_projects/bsg/world/publish.py")
    assert _in_scope("compose_projects/bsg/game_hardware/gm_mcp/publisher.py")


# --------------------------------------------------------------------------- #
# the bridge rule — a stated rule with a checked predicate, not a filename list
# --------------------------------------------------------------------------- #
def test_a_bridge_directory_alone_does_not_exempt_anything():
    """THE test that keeps the rule a rule instead of a looser allow-list.

    `*_bridge/` is an ADDRESS, and this file's whole argument is that addresses are
    unfalsifiable. So the address is only half the predicate: a module that names ONLY
    MQTT inside a bridge package is a bypass wearing a bridge's directory name, and it
    must stay RED. Pinned with synthetic sources so the rule has been SEEN to refuse
    rather than merely believed to.
    """
    mqtt_only = "import paho.mqtt.client as mqtt\nc = mqtt.Client()\nc.publish('x', 1)\n"
    assert _bridge_exemption("services/can_mqtt_bridge/helper.py", mqtt_only) == ""

    both = mqtt_only + "import socket\ns = socket.socket(socket.AF_CAN, socket.CAN_RAW)\n"
    assert _bridge_exemption("services/can_mqtt_bridge/helper.py", both)

    # ...and the directory really is required: two substrates somewhere else is an app
    # that has hardcoded two substrates, which is worse, not better.
    assert _bridge_exemption("compose_projects/intelligence/publisher/chat.py", both) == ""

    # A FILE named `*_bridge.py` buys nothing — only a package directory counts.
    assert _bridge_exemption("intelligence/publisher/channel_bridge.py", both) == ""


def test_the_bridge_rule_covers_both_bridges_not_just_the_remembered_one():
    """The failure the rule replaces, stated as an assertion.

    `services/can_mqtt_bridge/bridge.py` was a NAMED entry in `LEGITIMATE`;
    `services/uart_mqtt_bridge/bridge.py` — the same KIND of thing, written later —
    never was. Nobody noticed, because a filename list cannot notice. Both must now be
    dispositioned by the SAME rule, from their content, with no human in the loop.
    """
    for rel in ("services/can_mqtt_bridge/bridge.py",
                "services/uart_mqtt_bridge/bridge.py"):
        p = _REPO_ROOT / rel
        assert p.is_file(), f"{rel} moved — re-check the bridge rule against its new home"
        why = _bridge_exemption(rel, p.read_text(encoding="utf-8", errors="replace"))
        assert why, (
            f"{rel} is a BRIDGE but the rule does not recognise it. Either it stopped "
            f"naming its second substrate (then it is no longer a bridge and the "
            f"exemption should not apply), or `_SECOND_SUBSTRATE_TOKENS` has drifted "
            f"from how this repo spells that substrate.")


def test_no_allow_list_entry_duplicates_the_bridge_rule():
    """A named entry for something the rule already covers is the stale-prone thing
    the rule exists to delete. If both could apply, the list would keep looking
    load-bearing long after the rule made it redundant."""
    dupes = sorted(set(ALLOWED) & set(_RULE_EXEMPT))
    assert not dupes, (
        f"{dupes} are exempt BY RULE and also named in ALLOWED. Drop the ALLOWED "
        f"entry — a rule that needs a list beside it is not a rule.")


def test_the_bridge_rule_states_its_reasons():
    """[CXC-SOB-EXEMPT]: 'an exemption is a LISTED exemption'.

    A rule-based exemption has no list to read a reason off, so the rule GENERATES one
    and this test makes the whole set legible on demand (`-s` prints it). The property
    that matters is that no file is ever exempted silently.
    """
    for rel, why in sorted(_RULE_EXEMPT.items()):
        print(f"RULE-EXEMPT {rel}\n    {why}")
        assert why and len(why) > 40, f"{rel} exempted with no stated reason"


def test_a_hand_run_driver_is_in_scope_wherever_it_lives():
    """David's ruling, 2026-08-10 — a driver's exemption is NAMED, not addressed.

    A `smoke_*` / `drive_*` / `gen_*` file is a hand-run driver, not a pytest module,
    so the tests-directory exclusion must not swallow it. Pinned at BOTH addresses:
    the exemption has to survive the relocation out of `python/tests/` that has been
    discussed, which a directory rule would not.
    """
    assert _in_scope("python/tests/render/smoke_storage_viewers.py")
    assert _in_scope("tools/smoke_storage_viewers.py")          # after a hypothetical move
    assert _in_scope("python/tests/render/drive_flow.py")
    assert _in_scope("python/tests/render/gen_widget_placement_proofs.py")


def test_a_listed_smoke_driver_is_actually_scanned():
    """No double coverage: a file cannot be BOTH allow-listed and excluded.

    An entry the scan can never see is an entry that can never rot — permanent
    permission wearing an allow-list's clothes. Every ALLOWED path must be in scope,
    which is what makes leg 2 meaningful for all of them and not just for most.
    """
    unreachable = sorted(rel for rel in ALLOWED if not _in_scope(rel))
    assert not unreachable, (
        f"ALLOWED entries that the scan EXCLUDES upstream, so leg 2 can never fire "
        f"on them: {unreachable}. Either scan them or drop the entry — an exemption "
        f"for an unscanned file is not an exemption, it is a blind spot."
    )


def test_the_scan_actually_found_something():
    """A scanner that silently walks an empty tree passes leg 1 forever, and would
    make leg 2 fail for the wrong reason. Pin that it is really reading the repo."""
    assert _FOUND, "the paho scan found NOTHING — the walk is broken, not the repo clean"
