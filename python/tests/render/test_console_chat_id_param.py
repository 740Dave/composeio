"""SUPERSEDED — kept as the BACK-COMPAT half of [CXC-FC-VAR-SCOPE].

[DEC-CONSOLE-CHAT-ID-PARAM] (2026-08-11) made "which Intelligence the console chats
with" a document-level `variables: {chat_id: ...}`, and this file was its enforcing
test: the acceptance was THE FLIP — parse the scene, change the one line, assert all
four channel sites follow.

[CXC-FC-VAR-SCOPE] (2026-08-12) SUPERSEDES that decision.  The binding moved to the
scene's DECLARED INSTANCE LIST (`instances: [{id, role}]`) because the console now hosts
N Intelligences, and a scalar cannot name N.  `$chat_id` still exists and every site
still derives from it — but it is now DERIVED from `instances[0]`, not declared beside
it.  The requirement was explicit that the old declaration must be REMOVED rather than
left alongside: "two live sources for one binding is the fork class this corpus keeps
paying for."

WHY THIS FILE SURVIVES RATHER THAN BEING DELETED.  The FLIP property is still true and
still worth guarding — a channel site left hardcoded is still the silent half-flip that
made a composer publish to a topic no bridge unwraps.  Only the thing you FLIP changed:
a `variables:` key became an `instances:` entry.  So every test here now flips the LIST,
and the assertions about what must NOT move (the host telemetry) are untouched, because
that half of the decision was not superseded at all.

The fleet-side acceptance (FC-C1..FC-C3, two panes from one composite) lives in
`test_fleet_console.py`; this file is deliberately the narrow back-compat guard.
"""


from __future__ import annotations

import copy
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
REPO_ROOT = os.path.abspath(os.path.join(PY_ROOT, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

import pytest
import yaml

from composeio.render.engine.parser import parse_compose_yaml, parse_widget_tree

SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                     "console.compose.yaml")
DAEMON_SH = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "start-console-daemons.sh")

# The telemetry topics that must stay on the HOST id no matter what chat_id says.
# `thread/architect/narration` is NO LONGER here.  It was host telemetry rendered inside
# the CHAT surface — the one leg of the old thread that wore a different id from the other
# two, which is the 2026-08-12 chimera in a single widget.  In a pane the narration leg is
# `thread/{{instance_id}}/narration` and cannot disagree with out/in.  The rail's gauges
# below are still genuine host telemetry and still must not move.
HOST_TELEMETRY = ("self/architect/context_pct",
                  "self/architect/subagents", "self/architect/rate_limit")


def _parsed(chat_id=None):
    """The console composition, optionally with the DECLARED INSTANCE flipped.

    The flip is a one-entry edit of the `instances:` list — the same single line a human
    would edit ([CXC-FC-UNIT]).  It used to be a one-key edit of `variables:`; the
    property under test is unchanged, only the source of truth moved."""
    with open(SCENE, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    if chat_id is not None:
        cfg = copy.deepcopy(cfg)
        role = (cfg.get("instances") or [{}])[0].get("role", "composeux-architect")
        # ONE entry: these tests assert the SINGLE-surface property (every site of ONE
        # instance moves together). The N-pane property is [CXC-FC-ACCEPT-INSTANCING]'s
        # and lives in test_fleet_console.py — asserted there rather than duplicated.
        cfg["instances"] = [{"id": chat_id, "role": role}]
    return parse_widget_tree(cfg, base_dir=os.path.dirname(SCENE))


def _sites(comp, chat):
    """The four scene-side sites, as resolved topics.

    The composer field and the pane's link dot are found by their DERIVED per-instance
    ids (`<prefix>_<instance_id>_...`), because the console's collaboration surface is
    now N `intelligence_pane` instantiations rather than one bespoke subtree. The
    header's `live_dot` is deliberately NOT used here any more: it is CONSOLE-SELF
    chrome bound to the console's own Intelligence, not to the chat surface under test
    ([CXC-FC-SPLIT-RULE]).
    """
    declared = {n.get("name") for n in comp.namespaces}
    subbed = {ns for ns, _ in comp.subscriptions}
    widgets = list(comp.root.iter_widgets())

    def _by_suffix(suffix):
        return next(w for w in widgets
                    if w.widget_id.endswith(f"_{chat}_{suffix}"))

    field = _by_suffix("composer_field")
    dot = _by_suffix("link")
    return declared, subbed, field.bound_namespace, dot.namespace


# =============================================================================
# the single source
# =============================================================================

def test_scene_declares_its_intelligence_in_ONE_place():
    """The single-source property, restated on the list.  `chat_id` is still resolvable
    at every site — it is DERIVED from `instances[0]`, so there is nowhere to write a
    second value that could disagree ([CXC-FC-VAR-SCOPE])."""
    comp = parse_compose_yaml(SCENE)
    assert comp.instances, "the console scene must declare its fleet"
    # and TODAY the first is the hosted child — the whole reason this file exists.
    assert comp.instances[0]["id"] == "architect-hosted"

    # THE SUPERSEDED MECHANISM IS GONE, BOTH HALVES (FC-C3).  Not merely absent from
    # `variables:` — no longer SEEDED there either.  `instances.py::derived_variables`
    # used to inject `$chat_id` from entry 0, and a derived-but-still-document-scoped
    # variable is the old mechanism breathing: a scene carrying one can only ever name
    # ONE Intelligence, however honestly that name is computed ([CXC-FC-VAR-SCOPE]).
    with open(SCENE, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    assert "chat_id" not in (raw.get("variables") or {})
    assert "chat_id" not in comp.variables


@pytest.mark.parametrize("chat", ["architect-hosted", "architect", "provisionio"])
def test_all_four_sites_derive_from_the_one_variable(chat):
    """THE FLIP.  Every channel site follows chat_id; a site left hardcoded shows
    up here as a topic still naming the OLD id."""
    comp = _parsed(chat)
    declared, subbed, compose_ns, alive_ns = _sites(comp, chat)

    # 1. declarations
    for suffix in ("out", "in", "compose", "channel/alive"):
        assert f"intelligence/{chat}/{suffix}" in declared, suffix
    # 2. the chat thread's two channel legs
    assert f"intelligence/{chat}/out" in subbed
    assert f"intelligence/{chat}/in" in subbed
    # 3. the composer's send leg + the link dot's heartbeat
    assert compose_ns == f"intelligence/{chat}/compose"
    assert alive_ns == f"intelligence/{chat}/channel/alive"
    assert f"intelligence/{chat}/channel/alive" in subbed

    # NOTHING is left on another intelligence's channel: no channel topic
    # anywhere in the declared/subscribed set names a DIFFERENT id.  (config/*
    # and notify/* are the console's own persisted settings, which live under the
    # HOST's id by design — they are not the chat surface.)
    def _stray(topics):
        # `intelligence/architect/channel/alive` is EXEMPT: it is the console header's own
        # link dot, which is CONSOLE-SELF chrome bound to the console's own Intelligence
        # by an explicit id ([CXC-FC-SPLIT-RULE]).  It used to follow `$chat_id`, which
        # made a per-INSTANCE reading masquerade as a console-level one — the thing the
        # split rule exists to catch.  Per-instance link health lives in each PANE.
        return {t for t in topics
                if t.startswith("intelligence/")
                and t.split("/")[1] != chat
                and t != "intelligence/architect/channel/alive"
                and t.split("/")[2] not in ("config", "notify", "permission")}
    assert not _stray(declared), _stray(declared)
    assert not _stray(subbed), _stray(subbed)


@pytest.mark.parametrize("chat", ["architect-hosted", "architect"])
def test_host_telemetry_never_follows_chat_id(chat):
    """The half that must NOT move: the transcript-tailing feeds stay on the host
    whose session this console instruments, at EVERY chat_id."""
    comp = _parsed(chat)
    _declared, subbed, _c, _a = _sites(comp, chat)
    for ns in HOST_TELEMETRY:
        assert ns in subbed, ns
    # `thread/<id>/narration` is deliberately NOT in this assertion any more: it is a
    # PANE leg now, so a pane for the child SHOULD subscribe the child's narration.  What
    # must never move is the RAIL's `self/architect/*` gauges — those are produced by this
    # console's transcript-tailing sources, pinned to the HOST's session, and repointing
    # them would have the console claim the child's numbers while tailing the host's
    # transcript ([CXC-IH-5A]).
    assert not any(ns.startswith("self/architect-hosted/") for ns in subbed), \
        "rail telemetry was repointed at the child — the console would claim the " \
        "child's gauges while tailing the HOST's transcript ([CXC-IH-5A])"


def test_flip_is_one_line_and_round_trips():
    """Flip to `architect`, assert it took, flip back — the operational claim."""
    live = parse_compose_yaml(SCENE).instances[0]["id"]
    flipped = _sites(_parsed("architect"), "architect")
    assert flipped[2] == "intelligence/architect/compose"
    back = _sites(_parsed(live), live)
    assert back[2] == f"intelligence/{live}/compose"
    # the ON-DISK scene is untouched by the flip (it happened in memory)
    assert parse_compose_yaml(SCENE).instances[0]["id"] == live


# =============================================================================
# site 4 — the channel_bridge (the WRITE path), off the SAME variable
# =============================================================================

def _data_sources():
    path = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console")
    if path not in sys.path:
        sys.path.insert(0, path)
    import importlib
    import data_sources                                     # type: ignore
    return importlib.reload(data_sources)


def _argv_of(sources, name):
    src = next(s for s in sources if s.name == name)
    return list(src.run.args[0])            # functools.partial(main, argv, ...)


def test_scene_instance_reader_agrees_with_the_parser():
    """Both launch paths read the SAME declared list — the headless script shells out to
    `scene_instances`, the runtime hands over `comp.instances`."""
    ds = _data_sources()
    parsed = parse_compose_yaml(SCENE)
    assert [e["id"] for e in ds.scene_instances()] == [e["id"] for e in parsed.instances]
    assert ds.scene_chat_id() == parsed.instances[0]["id"]


def test_channel_bridge_takes_chat_id_and_telemetry_takes_the_host_id():
    ds = _data_sources()
    sources = ds.build_console_sources(
        session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
        chat_id="architect-hosted", mqtt_url="mqtt://x:1883",
        ledger="l.lts", session="2026-08-11")

    bridge = _argv_of(sources, "channel_bridge:architect-hosted")
    assert bridge[bridge.index("--id") + 1] == "architect-hosted"
    # ...and NOTHING else moved: every id-carrying telemetry source stays host.
    for name in ("self_publisher", "transcript_publisher",
                 "defect_metrics_publisher", "daemon_watch"):
        argv = _argv_of(sources, name)
        assert argv[argv.index("--id") + 1] == "architect", name


def test_omitted_chat_id_reproduces_the_old_wiring():
    """Byte-for-byte the pre-parameterisation argv when no chat_id is given."""
    ds = _data_sources()
    sources = ds.build_console_sources(
        session_jsonl=None, intelligence_id="architect",
        mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-08-11")
    argv = _argv_of(sources, "channel_bridge:architect")
    assert argv == ["--id", "architect", "--mqtt", "mqtt://x:1883"]


def test_headless_fallback_script_uses_the_declared_list_for_the_bridges_only():
    """start-console-daemons.sh is the OTHER launch path; a bridge on $ID there while
    the widgets are on the declared instance is the same silent mismatch — and with a
    fleet it is worse, because ONE bridge for TWO panes looks identical to a working
    console until the second Intelligence's messages go nowhere."""
    sh = open(DAEMON_SH, "r", encoding="utf-8").read()
    assert 'scene_instances' in sh, "the script must READ the scene's declared list"
    assert 'channel_bridge.py --id "$_iid"' in sh
    # the transcript-tailing daemons still carry the HOST id
    assert 'self_publisher.py --jsonl "$JSONL" --id "$ID"' in sh
    assert 'transcript_publisher.py --jsonl "$JSONL" --id "$ID"' in sh


# =============================================================================
# the runtime hand-off (live scene -> data layer)
# =============================================================================

class _Comp:
    def __init__(self, variables, instances=None):
        self.variables = variables
        self.instances = instances or []
        self.data_sources = True
        self.app_id = "intelligence-console"


class _App:
    def __init__(self, variables, instances=None):
        self.comp = _Comp(variables, instances)
        self.bus = object()


def _stub_repo(tmp_path, body):
    d = tmp_path / "compose_projects" / "intelligence" / "console"
    d.mkdir(parents=True)
    (d / "data_sources.py").write_text(body)
    return str(tmp_path)


def _run_with_stub(tmp_path, monkeypatch, body):
    from composeio.render.engine import runtime
    monkeypatch.setenv("COMPOSEIO_REPO_ROOT", _stub_repo(tmp_path, body))
    return runtime._maybe_start_inprocess_sources(
        _App({"chat_id": "architect-hosted"},
             [{"id": "architect-hosted", "role": "composeux-architect"}]))


def test_runtime_hands_the_live_scenes_instances_to_the_data_layer(tmp_path,
                                                                   monkeypatch):
    got = _run_with_stub(
        tmp_path, monkeypatch,
        "def start_console_data_layer(*, bus=None, chat_id=None, instances=None):\n"
        "    return {'bus': bus is not None, 'chat_id': chat_id,\n"
        "            'ids': [e['id'] for e in (instances or [])]}\n")
    assert got == {"bus": True, "chat_id": "architect-hosted",
                   "ids": ["architect-hosted"]}


def test_runtime_degrades_loudly_not_fatally_on_an_older_data_layer(tmp_path,
                                                                    monkeypatch,
                                                                    caplog):
    """A data_sources.py without the kwarg must still bring the layer UP (it
    resolves chat_id from the scene itself) — passing blind would TypeError into
    the broad except and take the whole data layer down silently."""
    got = _run_with_stub(tmp_path, monkeypatch,
                         "def start_console_data_layer(*, bus=None):\n"
                         "    return 'started'\n")
    assert got == "started"
