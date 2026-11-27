"""No console data source may name a SUBSTRATE below the waist.

[SPEC:CXC-SOURCES-ON-BUS] [CXC-SOB-THESIS], acceptance criterion SOB-C7.

THE THESIS THIS ENFORCES (David, 2026-08-08): "ComposeBus might for some devices
be instantiated as MQTT, and for some it might ride on CANBUS. But in both cases
the ComposeIO/UX app shouldn't have to care how ComposeBus is instantiated."  A
source that writes `client.publish(f"composeio/global/{ns}", ...)` has hardcoded
MQTT: on a CAN-only device it publishes into the void, and — this is the part
that matters — with no error at all.

WHY A GREP TEST AND NOT A CONVENTION. Reorg-proofing is a red test, not a
comment ([SBR-PATHS-SINGLE-SOURCE]'s shape). The bypass this arc removes reached
SEVENTEEN call sites because nothing failed when the eighteenth was added. This
file is what makes the nineteenth impossible: the allow-list SHRINKS as the
slices land, and a source that is neither converted nor explicitly exempt is a
red build.

AN EXEMPTION IS A *LISTED* EXEMPTION. Every entry below carries a one-line
rationale. An unexplained entry is a review reject — that rule is the only thing
standing between an allow-list and a silent skip.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PUBLISHER = _REPO_ROOT / "compose_projects" / "intelligence" / "publisher"
_TOOLS = _REPO_ROOT / "compose_projects" / "intelligence" / "tools"


# --------------------------------------------------------------------------- #
# The registered source set — read from `data_sources.py`, never re-listed.
# --------------------------------------------------------------------------- #
# [DX-NARROW-WAIST]: a hand-maintained copy of the source list here would drift
# the moment a source is registered or retired, and would drift SILENTLY (this
# test would keep passing while checking a stale set). So the list is parsed out
# of the ONE place that defines it.
def _registered_source_modules() -> list[str]:
    text = (_REPO_ROOT / "compose_projects" / "intelligence" / "console" / "data_sources.py").read_text()
    return sorted(set(re.findall(r'_src\(\s*"([a-z_]+)"', text)))


def _module_path(name: str) -> Path:
    for d in (_PUBLISHER, _TOOLS):
        p = d / f"{name}.py"
        if p.is_file():
            return p
    raise AssertionError(f"registered source {name!r} has no module on disk")


# --------------------------------------------------------------------------- #
# [CXC-SOB-EXEMPT] — the allow-list. One line of WHY per entry, mandatory.
# --------------------------------------------------------------------------- #
# Class 2 of [CXC-SOB-EXEMPT] ("out of scope, not exempt in principle"): these
# are still on raw paho because their conversion SLICE has not landed. Each entry
# names the slice that removes it. This dict must only ever SHRINK.
#
# EMPTY since 2026-08-11 — Phase 2a is COMPLETE: EVERY registered console data
# source reaches the bus through `source_bus`, and NONE is exempt. It has stayed
# empty through each source added since (most recently `hookup_viewer_launcher`),
# which is what a ratchet at rest looks like.
#
# Slices 2, 3, 4, 5a and 5b landed earlier and took their rationale constants
# (`_S2`..`_S5`) with them. The last three went in this order, per
# [CXC-SOB-SLICES]: `deliverable_opener` (slice 6, the first `loop_forever`
# source, proving [CXC-SOB-RUN-BLOCKS] parking on the lower-stakes blocker),
# `dsd_viewer_launcher` (slice 7), then `channel_bridge` (slice 7,
# [CXC-SOB-CHANNEL-LAST], alone).
#
# The last two were blocked on the SAME missing capability, not on effort: both
# address `compose_projects/intelligence/<id>/in` — a bare topic with a bare payload, the
# composeio-channel MCP's own wire — which `publish_state` cannot express
# without changing the bytes. The unblock was a new FOREIGN-CHANNEL seam at the
# waist (`Transport.send_foreign` / `ComposeIOBus.publish_foreign`), so the
# module names a peer-protocol ADDRESS and the transport still owns the
# substrate. See `channel_bridge.run`'s docstring.
#
# THIS DICT MUST STAY EMPTY. It is not "done" — it is a ratchet at rest. A new
# source that needs an exemption adds an entry WITH a rationale and a named
# slice that removes it; anything else is a red build, which is the point.
NOT_YET_CONVERTED: dict[str, str] = {}

# Class 1 of [CXC-SOB-EXEMPT] — PERMANENT. Not a data source, listed so a future
# reader does not "fix" it.
PERMANENTLY_EXEMPT: dict[str, str] = {
    "compose_projects/intelligence/launcher/launcher.py":
        "hand-frames its envelope to keep a paho-only, composeio-import-free "
        "STARTUP footprint. That is a real constraint, not an oversight.",
}

# The literals that mean "this file knows it is talking to MQTT".
_BANNED = [
    (re.compile(r"^\s*import\s+paho", re.M), "imports paho"),
    (re.compile(r"^\s*from\s+paho", re.M), "imports paho"),
    (re.compile(r"mqtt\.Client\s*\("), "constructs an mqtt.Client"),
    (re.compile(r'"composeio/|\'composeio/|f"composeio/|f\'composeio/'),
     "composes a `composeio/...` topic string"),
    (re.compile(r"\.loop_forever\s*\("), "calls loop_forever"),
    (re.compile(r"\.loop_start\s*\("), "calls loop_start"),
    # NOT in the spec's literal list, and it should be. `memory_monitor`,
    # `project_validator` and `daemon_watch` do not touch paho at all — they
    # import and construct `MQTTTransport` directly and hand it `BusMessage`s.
    # That is a step UP from raw paho and still fails [CXC-SOB-THESIS] in
    # exactly the way that matters: the module names ONE substrate by class, so
    # on a CAN-only device it is just as dark. Naming the transport class is
    # naming the substrate.
    (re.compile(r"\bMQTTTransport\s*\("), "constructs an MQTTTransport directly"),
    (re.compile(r"transport_mqtt\b"), "imports the MQTT transport module"),
]


def _strip_comments_and_docstrings(text: str) -> str:
    """Only CODE counts. A module docstring is allowed — and encouraged — to
    explain that it used to speak MQTT and no longer does."""
    import io
    import tokenize
    out = []
    try:
        toks = tokenize.generate_tokens(io.StringIO(text).readline)
        prev_type = tokenize.INDENT
        for tok in toks:
            if tok.type == tokenize.COMMENT:
                continue
            if tok.type == tokenize.STRING and prev_type in (
                    tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE,
                    tokenize.NL, tokenize.ENCODING):
                continue                          # a docstring
            out.append(tok.string)
            if tok.type not in (tokenize.NL, tokenize.COMMENT):
                prev_type = tok.type
    except (tokenize.TokenError, IndentationError):
        return text
    return "\n".join(out)


def _without_selftest(text: str) -> str:
    """Everything ABOVE `def _selftest`.

    A module's own `--selftest` legitimately builds a `ComposeIOBus` over a
    recording transport — that is how it pins its wire shape without a broker,
    and it is the OPPOSITE of the defect this file guards (it proves the source
    goes through the bus). Only the RUNTIME path is checked for a hand-built bus.
    Deliberately NOT applied to the substrate patterns above: a `--selftest` that
    needs paho is not hermetic and should fail.
    """
    m = re.search(r"^def _selftest\b", text, re.M)
    return text[:m.start()] if m else text


@pytest.mark.parametrize("name", _registered_source_modules())
def test_registered_source_names_no_substrate(name):
    path = _module_path(name)
    code = _strip_comments_and_docstrings(path.read_text())
    hits = [why for pattern, why in _BANNED if pattern.search(code)]

    if name in NOT_YET_CONVERTED:
        assert hits, (
            f"{name} is on the NOT_YET_CONVERTED allow-list but no longer names "
            f"a substrate — it has been converted. DELETE its entry from "
            f"NOT_YET_CONVERTED; the list must only shrink, and an entry that "
            f"outlives its reason is how an allow-list becomes a silent skip.")
        return

    assert not hits, (
        f"{name} ({path.relative_to(_REPO_ROOT)}) {', '.join(hits)}.\n"
        f"A registered console data source must reach the bus through "
        f"`composeio.source_bus` — namespace + scope, never a topic string and "
        f"never a client. If this is deliberate, it needs an entry in "
        f"[CXC-SOB-EXEMPT] WITH a rationale, not a silent skip.")


def test_the_allow_list_only_names_real_registered_sources():
    """A stale entry is worse than none: it silently exempts nothing while
    looking like it exempts something."""
    registered = set(_registered_source_modules())
    stale = set(NOT_YET_CONVERTED) - registered
    assert not stale, (
        f"NOT_YET_CONVERTED names sources that are not registered: {sorted(stale)}")


def test_every_exemption_carries_a_rationale():
    """[CXC-SOB-EXEMPT]: 'an exemption is a LISTED exemption'."""
    for table in (NOT_YET_CONVERTED, PERMANENTLY_EXEMPT):
        for key, why in table.items():
            assert why and len(why) > 40, (
                f"{key} has no real rationale ({why!r}). An unexplained "
                f"exemption is a review reject.")


def test_the_permanently_exempt_launcher_still_exists_and_still_hand_frames():
    """If the launcher ever stops hand-framing, its exemption should be RETIRED,
    not left standing as a documented path back around the bus."""
    p = _REPO_ROOT / "compose_projects" / "intelligence" / "launcher" / "launcher.py"
    assert p.is_file()
    text = p.read_text()
    assert "paho" in text, (
        "launcher.py no longer speaks paho — retire its [CXC-SOB-EXEMPT] entry")
    # [CXC-SOB-ENDSTATE]'s constraint says the fold "MUST NOT create an import
    # from the launcher into `composeio`". Measured against the code, that is
    # stated too broadly: launcher.py ALREADY imports `composeio.bootstrap_paths`
    # and `composeio.self_sensor.context_source` — both LAZILY, inside functions,
    # and neither pulls in paho, the bus, or a transport. What the exemption
    # actually protects is the STARTUP footprint: the import graph reachable at
    # module load. So that is what is pinned here.
    lazy_ok = {
        "composeio.bootstrap_paths",
        "composeio.self_sensor.context_source",
        # [SPEC:CXC-TERM] · [CXC-TERM-PTYCORE], 2026-08-11: the PTY half was EXTRACTED so the
        # launcher and the terminal widget drive ONE pty.fork.  ptycore is stdlib-only and
        # reaches no bus and no transport; it is bound on first use (_load_core), so module
        # LOAD stays composeio-free — the property this exemption actually protects.
        "composeio.ptycore",
        # w-26, 2026-11-27: paho 2.x requires a CallbackAPIVersion at Client(), so the
        # launcher builds its client through the shared constructor. `paho_compat` is
        # import-free (the caller passes its own paho module) and reaches no bus and
        # no transport; it is imported lazily inside connect_mqtt(). The launcher still
        # hand-frames its own paho traffic, which is what this exemption is about.
        "composeio.paho_compat",
    }
    for m in re.finditer(r"^(?:from|import)\s+(composeio[\w.]*)", text, re.M):
        raise AssertionError(
            f"launcher.py gained a TOP-LEVEL composeio import ({m.group(1)}) — "
            f"that is the startup footprint its [CXC-SOB-EXEMPT] entry protects")
    # BOTH import forms, at ANY indentation.  This used to match only `from X import`, so a
    # lazy `import composeio.<anything>` inside a function was invisible to the guard — and
    # silence, not the dependency, is the failure mode this file exists to prevent.
    for m in re.finditer(r"(?:from|import)\s+(composeio[\w.]*)", text):
        assert m.group(1) in lazy_ok, (
            f"launcher.py gained a new composeio dependency ({m.group(1)}). "
            f"Lazy imports of pure helpers are fine; anything that reaches the "
            f"bus or a transport defeats the exemption.")


def test_source_bus_is_the_only_place_a_source_builds_a_bus():
    """The seam is only a waist if nothing goes around it.

    A source that constructs `ComposeIOBus(...)` itself has re-forked the
    transport choice — the same defect as a hardcoded topic string, one level up.
    """
    offenders = []
    for name in _registered_source_modules():
        code = _strip_comments_and_docstrings(
            _without_selftest(_module_path(name).read_text()))
        if re.search(r"ComposeIOBus\s*\(", code):
            offenders.append(name)
    assert not offenders, (
        f"{offenders} construct a ComposeIOBus directly — use "
        f"`source_bus.acquire_bus(...)`, which goes through "
        f"`transport_registry` and memoizes the self-create path.")
