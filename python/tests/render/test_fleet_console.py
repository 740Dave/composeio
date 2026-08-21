"""[SPEC:CXC-FLEET] — the instancing unit, the data-layer singletons and the no-second-path guards.

Covers acceptance FC-C1, FC-C2, FC-C3, FC-C8, FC-C10, FC-C12, FC-C13, FC-C15.
(FC-C4..FC-C7 and FC-C18..FC-C20 live in `test_fleet_memory_scoping.py`; FC-C11 in
`test_launch_placement.py`; FC-C17 in `test_dusk_gift_tiers.py`.)

THE HEADLINE IS FC-C1: two panes in one scene, distinct ids, each bound to its own
Intelligence, neither leaking into the other — from ONE composite, with no engine widget
class added.  Everything else in this file exists so that a green FC-C1 cannot be reached
by a shortcut: FC-C2 guards that no widget class appeared, FC-C3 that the binding moved to
param scope with no second source left behind, FC-C15 that no second discovery path,
slicer or transport table was introduced along the way.

⚠ MACHINE-GREEN IS NOT GLASS-GREEN.  FC-C1 and FC-C13 have HUMAN legs that no assertion
here can discharge: a test asserting a string is present cannot see that it is illegible,
truncated, or attached to the wrong pane.  Those legs are David's and they are mandatory
([PRINCIPLE-MACHINE-GREEN-IS-NOT-GLASS-GREEN]).
"""
from __future__ import annotations

import ast
import os
import sys

import pytest
import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
for _d in (os.path.join(_REPO, "python"),
           os.path.join(_REPO, "compose_projects", "intelligence", "console"),
           os.path.join(_REPO, "compose_projects", "intelligence", "publisher")):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from composeio.render.engine.instances import parse_instances  # noqa: E402
from composeio.render.engine.parser import parse_compose_yaml  # noqa: E402

PANE_SCENE = os.path.join(_REPO, "python", "composeio", "render", "examples",
                          "components", "intelligence_pane.compose.yaml")
CONSOLE_SCENE = os.path.join(_REPO, "compose_projects", "intelligence", "console", "console.compose.yaml")
DAEMON_SH = os.path.join(_REPO, "compose_projects", "intelligence", "start-console-daemons.sh")


def _all_ids(comp):
    out = []

    def walk(w):
        out.append(w.widget_id)
        for ch in getattr(w, "children", []) or []:
            walk(ch)
    walk(comp.root)
    return out


# ═════ FC-C1 — two Intelligences render in one scene from ONE composite ═════════
def test_FC_C1_two_panes_expand_with_complete_non_colliding_bindings():
    comp = parse_compose_yaml(PANE_SCENE)
    assert [e["id"] for e in comp.instances] == ["architect-hosted", "hardware"]

    subs = {ns for ns, _ in comp.subscriptions}
    a = {s for s in subs if "architect-hosted" in s}
    b = {s for s in subs if "hardware" in s}

    # COMPLETE: each pane subscribes its own out/in/heartbeat/send — four apiece.
    for iid, group in (("architect-hosted", a), ("hardware", b)):
        for suffix in (f"intelligence/{iid}/out", f"intelligence/{iid}/in",
                       f"intelligence/{iid}/channel/alive",
                       f"_app/{iid}/composer_send"):
            assert suffix in group, f"{iid} missing {suffix}"
    # DISJOINT: no topic belongs to both panes.
    assert not (a & b)


def test_FC_C1_zero_duplicate_widget_ids_across_two_panes():
    """The collision problem `segment_bar.compose.yaml:52` solved with `id_prefix`,
    applied to a whole pane.  A duplicate id is not cosmetic: two widgets answering to
    one id is how a bus publish lands on the wrong pane."""
    ids = _all_ids(parse_compose_yaml(PANE_SCENE))
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate widget ids across panes: {dupes}"
    assert len(ids) > 40, "the two panes did not fully expand"


def test_FC_C1_the_two_panes_come_from_ONE_component_definition():
    """Not two similar subtrees that happen to match — literally one `components:`
    entry, stamped twice."""
    with open(PANE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    assert list(cfg["components"]) == ["intelligence_pane"]

    # ONE generator, not two hand-written call sites.  Writing them out made
    # `instances:` and the widget tree two declarations of one fact: repoint an id in
    # the list and the pane stayed on the old topics ([CXC-FC-VAR-SCOPE]).
    gens = [w for w in cfg["widgets"][0]["children"] if "for_each_instance" in w]
    assert len(gens) == 1
    assert gens[0]["for_each_instance"] == "intelligence_pane"

    # ...and each DECLARED instance carries its ROLE, because role is not derivable
    # from the id and the generator passes it straight through ([CXC-FC-UNIT]).
    declared = parse_instances(cfg)
    assert len(declared) == 2
    assert all(d.get("role") for d in declared)
    assert {d["id"] for d in declared} == {"architect-hosted", "hardware"}


def test_FC_C1_each_panes_widgets_are_rooted_at_its_own_id_prefix():
    ids = _all_ids(parse_compose_yaml(PANE_SCENE))
    assert any(i.startswith("pane1_") for i in ids)
    assert any(i.startswith("pane2_") for i in ids)
    # no pane1 widget carries pane2's instance id, or the panes are crossed
    assert not [i for i in ids if i.startswith("pane1_") and "hardware" in i]
    assert not [i for i in ids if i.startswith("pane2_") and "architect-hosted" in i]


# ═════ FC-C2 — no rasterising widget class was added for the pane ═══════════════
def test_FC_C2_no_engine_widget_class_was_added_for_the_pane():
    """[CXC-FC-COMPOSITE]: a pane widget class is a spec violation, not a shortcut —
    the mechanism it would duplicate is already in the tree and already tested."""
    widgets_dir = os.path.join(_REPO, "python", "composeio", "render", "widgets")
    offenders = []
    for fn in sorted(os.listdir(widgets_dir)):
        if not fn.endswith(".py"):
            continue
        tree = ast.parse(open(os.path.join(widgets_dir, fn), encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and "pane" in node.name.lower():
                offenders.append(f"{fn}:{node.name}")
    assert not offenders, f"a pane widget CLASS was added: {offenders}"


def test_FC_C2_the_panes_definition_is_a_compose_yaml_component():
    assert os.path.isfile(PANE_SCENE)
    with open(PANE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    pane = cfg["components"]["intelligence_pane"]
    assert set(pane["params"]) == {"instance_id", "role"}
    # `role` has NO default — an undeclared role must refuse, not guess, because it
    # keys the MEMORY STORE ([CXC-FC-PROJECT-DIR]).
    assert "role" not in pane.get("defaults", {})
    assert "id_prefix" in pane["defaults"]           # the segment_bar precedent


# ═════ FC-C3 — the instance binding lives at PARAM scope, in ONE place ══════════
def test_FC_C3_the_console_declares_no_document_level_chat_id_variable():
    """The literal acceptance grep.  Two live sources for one binding is the fork class
    this corpus keeps paying for ([CXC-FC-VAR-SCOPE])."""
    with open(CONSOLE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    assert "chat_id" not in (cfg.get("variables") or {})


def test_FC_C3_the_console_declares_an_instance_LIST_instead():
    with open(CONSOLE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    declared = parse_instances(cfg)
    assert declared, "the console must declare its fleet"
    assert declared[0]["id"] and declared[0]["role"], \
        "every declared instance is an (id, role) PAIR ([CXC-FC-UNIT])"


def test_FC_C3_every_console_channel_site_still_derives_from_the_declared_instance():
    """The FLIP, restated on the list: change the declared id and every channel site
    follows.  A site left hardcoded shows up here as a topic naming the old id."""
    with open(CONSOLE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    cfg["instances"] = [{"id": "provisionio", "role": "some-role"}]
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree(cfg, base_dir=os.path.dirname(CONSOLE_SCENE))

    # DECLARATIONS follow (stamped from `instance_namespaces:`)...
    declared = {n.get("name") for n in comp.namespaces}
    for suffix in ("out", "in", "compose", "channel/alive"):
        assert f"intelligence/provisionio/{suffix}" in declared, suffix
    # ...and so do the WIDGETS (generated by `for_each_instance:`).  Both halves matter:
    # declarations without panes is a scene with topics nobody binds, and panes without
    # declarations is a scene binding topics nobody declared.
    subs = {ns for ns, _ in comp.subscriptions}
    assert "intelligence/provisionio/out" in subs
    assert "_app/provisionio/composer_send" in subs

    # NOTHING is left on the old ids — the flip is COMPLETE, which is the property that
    # makes the instance list a single source rather than a hopeful label.
    assert not [s for s in subs if "architect-hosted" in s]
    assert not [n for n in declared if n and "architect-hosted" in n]

    # ...and the console's HOST telemetry did NOT move: `self/architect/*` is produced by
    # THIS console's transcript-tailing sources, pinned to the host's session, and
    # repointing it would have the console claim another instance's gauges ([CXC-IH-5A]).
    assert any(s.startswith("self/architect/") for s in subs)
    assert not any(s.startswith("self/provisionio/") for s in subs)


def test_FC_C3_the_headless_script_reads_the_declared_instance_list():
    """[CXC-FC-VAR-SCOPE]'s constraint: the headless fallback read the SAME variable out
    of the SAME file.  Moving the widgets and leaving that reader behind would keep the
    headless path on a one-Intelligence view of a fleet scene."""
    sh = open(DAEMON_SH, encoding="utf-8").read()
    assert "scene_instances" in sh
    assert "for _iid in $INSTANCE_IDS" in sh, \
        "the headless path must launch one bridge PER declared instance"
    # the transcript-tailing daemons still carry the HOST id
    assert 'self_publisher.py --jsonl "$JSONL" --id "$ID"' in sh
    assert 'transcript_publisher.py --jsonl "$JSONL" --id "$ID"' in sh


def test_FC_C3_the_instance_list_is_the_ONLY_source_and_seeds_no_variable():
    """No `$chat_id` ANYWHERE — not declared, and no longer DERIVED into `variables:`.

    The first cut of this build seeded `$chat_id` from `instances[0]`, on the reasoning
    that a derivation cannot disagree with its source.  That is true and beside the
    point: [CXC-FC-VAR-SCOPE] asks for the binding to live at PARAM scope per pane, and
    a scene keeping ONE document-level `$chat_id` alive can only ever name ONE
    Intelligence however honestly that name is computed."""
    comp = parse_compose_yaml(CONSOLE_SCENE)
    assert "chat_id" not in comp.variables
    assert len(comp.instances) >= 2, "the console must declare a FLEET, not one instance"


@pytest.mark.parametrize("raw,expected", [
    ([{"id": "a", "role": "r"}], [{"id": "a", "role": "r"}]),
    ([{"id": "a", "role": "r"}, {"id": "a", "role": "r"}],
     [{"id": "a", "role": "r"}]),                                 # dupes DROPPED
    ([{"role": "r"}], []),                                        # no id -> no topics
    ("not-a-list", []),
])
def test_FC_C3_instance_list_parsing_is_strict(raw, expected):
    assert parse_instances({"instances": raw}) == expected


@pytest.mark.parametrize("raw", [
    ["bare-id"],                     # the string shorthand carries no role
    [{"id": "a"}],                   # role omitted
    [{"id": "a", "role": ""}],       # role present but empty
    [{"id": "a", "role": "   "}],    # role present but blank
])
def test_FC_C3_a_roleless_instance_is_REFUSED_at_the_scene_layer(raw):
    """[CXC-FC-UNIT]: an absent role MUST refuse, not guess.

    The SPAWN path already refused (`project_dir_for` raises `RoleRefused`), so nothing
    was ever mis-keyed — but a roleless instance still reached the GLASS, where the pane
    rendered `~/compose-instances//memory/`: a surface asserting a memory location that
    does not exist. "The spawn would have caught it" is no defence when the human is
    reading the pane rather than the traceback.
    """
    with pytest.raises(ValueError, match=r"CXC-FC-UNIT"):
        parse_instances({"instances": raw})


def test_the_console_app_id_constant_agrees_with_the_scene():
    """[CXC-FC-S4-BBOX] — the one hand-restated string this build introduced.

    `app_topics._DEFAULT_CONSOLE_APP_ID` re-spells `console.compose.yaml`'s `app: id:` so
    five out-of-process launchers can name the console's bbox topic WITHOUT parsing a
    scene.  That is a defensible engineering trade (a consumer needs the string, not the
    scene) and an INDEFENSIBLE one to leave unguarded: it is a second source of truth,
    introduced by the build whose whole thesis is that there are none.  If they ever
    disagree the console publishes its geometry where nobody listens, and window
    placement degrades silently — which is precisely the class [CXC-FC-S4-BBOX] closed.
    """
    from composeio.app_topics import app_bbox_ns, console_app_id
    scene_app_id = parse_compose_yaml(CONSOLE_SCENE).app_id
    assert console_app_id() == scene_app_id, (
        f"app_topics says the console app id is {console_app_id()!r} but "
        f"{CONSOLE_SCENE} declares {scene_app_id!r}. The launchers would subscribe a "
        f"bbox topic the console never publishes to.")
    # ...and the derived topic is the one the console will actually publish on.
    from composeio.render.engine.runtime import CONSOLE_BBOX_NS
    assert CONSOLE_BBOX_NS == app_bbox_ns(scene_app_id)


def test_FC_C3_no_scene_in_the_tree_renders_an_empty_role_path():
    """The consequence, checked where it shows: no pane anywhere names a collapsed
    `~/compose-instances//` path."""
    import glob
    bad = []
    for f in glob.glob(os.path.join(_REPO, "**", "*.compose.yaml"), recursive=True):
        with open(f, encoding="utf-8") as fh:
            src = fh.read()
        if "compose-instances//" in src:
            bad.append(f)
    assert not bad, f"scene(s) render an empty-role memory path: {bad}"


# ═════ FC-C8 — N bridges, and no instance loses its voice ══════════════════════
def _argv_of(sources, name):
    src = next(s for s in sources if s.name == name)
    return list(src.run.args[0])


def _build(**kw):
    import data_sources                                            # type: ignore
    base = dict(session_jsonl="/tmp/x.jsonl", intelligence_id="architect",
                mqtt_url="mqtt://x:1883", ledger="l.lts", session="2026-08-12")
    base.update(kw)
    return data_sources.build_console_sources(**base)


def test_FC_C8_two_declared_instances_start_two_bridges():
    """This is why the HOST lost its bus voice on 2026-08-12 when the console's single
    bridge was pointed at the hosted child: not a pane change, a TRANSPORT change.
    There was one bridge and it could only be in one place."""
    sources = _build(instances=[{"id": "architect-hosted", "role": "composeux-architect"},
                                {"id": "hardware", "role": "composeio-hardware-architect"}])
    names = [s.name for s in sources]
    assert "channel_bridge:architect-hosted" in names
    assert "channel_bridge:hardware" in names
    assert _argv_of(sources, "channel_bridge:hardware")[:2] == ["--id", "hardware"]


def test_FC_C8_the_host_keeps_its_own_bridge_when_a_child_is_added():
    """The regression that started this: adding an Intelligence must not take another
    one's voice away."""
    sources = _build(instances=[{"id": "architect-hosted", "role": "composeux-architect"}])
    names = [s.name for s in sources]
    assert "channel_bridge:architect-hosted" in names
    assert "channel_bridge:architect" in names       # the host is still on the air


def test_FC_C8_the_bridges_take_distinct_flocks_by_construction():
    """The mechanism was already correct — `channel-bridge-<id>` — it was simply never
    instantiated more than once."""
    import channel_bridge                                          # type: ignore
    src = open(channel_bridge.__file__, encoding="utf-8").read()
    assert 'f"channel-bridge-{intelligence_id}"' in src


def test_FC_C8_no_declared_list_reproduces_the_single_bridge_wiring():
    """Byte-for-byte the pre-fleet argv when no fleet is declared."""
    sources = _build(session_jsonl=None)
    assert _argv_of(sources, "channel_bridge:architect") == \
        ["--id", "architect", "--mqtt", "mqtt://x:1883"]


# ═════ FC-C9 — telemetry is per-instance ═══════════════════════════════════════
def test_FC_C9_each_instance_with_a_known_transcript_gets_its_OWN_telemetry_set():
    sources = _build(instances=[{"id": "hardware", "role": "composeio-hardware-architect"}],
                     session_jsonl_by_id={"hardware": "/tmp/hw.jsonl"})
    argv = _argv_of(sources, "self_publisher:hardware")
    assert argv[argv.index("--id") + 1] == "hardware"
    assert "--namespace-by-id" in argv          # disjoint self/<id>/* per instance
    # ...and the HOST's set is untouched and still on the host's transcript.
    host = _argv_of(sources, "self_publisher")
    assert host[host.index("--id") + 1] == "architect"


def test_FC_C9_an_instance_with_no_known_transcript_gets_NO_set_rather_than_a_wrong_one():
    """A gauge attached to the wrong Intelligence is worse than a missing one, because
    the surface reports a state the system is not in ([CXC-FC-S2-TELEMETRY])."""
    sources = _build(instances=[{"id": "hardware", "role": "r"}])
    assert "self_publisher:hardware" not in [s.name for s in sources]


# ═════ FC-C10 — ONE roster computation, not one per pane ═══════════════════════
def test_FC_C10_the_console_starts_exactly_one_roster_source():
    sources = _build(instances=[{"id": "a", "role": "r1"}, {"id": "b", "role": "r2"}])
    assert [s.name for s in sources].count("fleet_roster") == 1


def test_FC_C10_the_roster_is_handed_every_declared_pair():
    sources = _build(instances=[{"id": "a", "role": "r1"}, {"id": "b", "role": "r2"}])
    argv = _argv_of(sources, "fleet_roster")
    assert "a:r1" in argv and "b:r2" in argv


def test_FC_C10_an_independently_launched_peer_appears_in_the_roster():
    """The dividend: the console need not have SPAWNED something in order to instrument
    it, because the heartbeat carries the session uuid.  Hosted children and independent
    peers become the same code path — which is what actually closes
    [INSIGHT-SELF-IS-NOT-YET-A-PEER]."""
    import fleet_roster                                            # type: ignore
    r = fleet_roster.RosterComputation(hosted_ids=["architect-hosted"],
                                       role_resolver=lambda i: "unknown")
    r.ingest("intelligence/somebody-elses-agent/channel/alive",
             b'{"pid": 9, "instance": "uuid-1"}')
    entry = r.roster()[0]
    assert entry["id"] == "somebody-elses-agent"
    assert entry["hosted"] is False              # a peer, not a child — and it is SAID
    assert entry["instance"] == "uuid-1"         # the key to its transcript


def test_FC_C10_role_and_hosted_are_UNKNOWN_when_unresolvable_never_guessed():
    """Measured 2026-08-12: `role` and `hosted` are NOT on the heartbeat.  A guessed
    role keys nothing (and would mis-key a memory store); a guessed `hosted` mislabels a
    peer as somebody's child."""
    import fleet_roster                                            # type: ignore
    r = fleet_roster.RosterComputation(role_resolver=lambda i: "unknown")
    r.ingest("intelligence/mystery/channel/alive", b"{}")
    entry = r.roster()[0]
    assert entry["role"] == "unknown" and entry["hosted"] == "unknown"


def test_FC_C10_the_roster_resolves_a_real_role_through_the_manifest_chain():
    """`architect.meta.json` carries `"role": "ComposeUX / ComposeIO Architect"` — a
    human-readable TITLE.  The STORE name is the role-descriptor basename."""
    import fleet_roster                                            # type: ignore
    assert fleet_roster.resolve_role("architect") == "composeux-architect"
    assert fleet_roster.resolve_role("no-such-instance") == "unknown"


# ═════ FC-C12 — the console has no privileged self ═════════════════════════════
def test_FC_C12_the_pane_composite_does_not_branch_on_the_host_id():
    """[CXC-FC-HOST-AS-PANE]: a privileged self is a second code path for one concept,
    and it is the path that produced the 2026-08-12 chimera — the chat pane showing the
    CHILD while every gauge showed the HOST.  Checked against the PARSED component body
    rather than the file text, so the surrounding prose that explains the rule cannot
    trip the rule."""
    with open(PANE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    body = yaml.safe_dump(cfg["components"]["intelligence_pane"]["widget"])
    # `chrome: console` is a STYLE token (the console theme), not an identity — hence
    # the banned set names TOPICS and IDS, not the substring "console".
    for literal in ("architect-hosted", "hardware", "composeux-architect",
                    "_app/console/", "self/console", "intelligence/architect"):
        assert literal not in body, (
            f"the composite must be id-GENERIC — the literal {literal!r} inside it is a "
            f"privileged self ([CXC-FC-HOST-AS-PANE])")


def test_FC_C12_the_telemetry_loop_has_no_is_this_the_host_branch():
    """[CXC-FC-HOST-AS-PANE] forbids "is this the host" BY NAME.

    ⚠ THIS ASSERTION REPLACES A FITTED ONE.  The previous version counted host-id
    comparisons and asserted `<= 2` — a bound chosen to match what the code happened to
    do, which is not a guard at all: it would have passed for any implementation that
    branched at most twice, including the `if iid == intelligence_id: continue` that
    [CXC-FC-HOST-AS-PANE] names as the violation. A guard fitted to the code under test
    certifies the code under test.

    The property is now structural: the telemetry loop resolves every instance through
    ONE map lookup, so there is no host arm to drift.
    """
    import data_sources                                            # type: ignore
    src = open(data_sources.__file__, encoding="utf-8").read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_console_sources")

    # No `continue`/`return` GUARDED BY a host-id comparison anywhere in the builder.
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        tests = [n for n in ast.walk(node.test)
                 if isinstance(n, ast.Name) and n.id == "intelligence_id"]
        if not tests:
            continue
        body_kinds = {type(b) for b in node.body}
        assert not (body_kinds & {ast.Continue, ast.Return}), (
            "a host-id comparison guards control flow in build_console_sources — that "
            "is 'is this the host', which [CXC-FC-HOST-AS-PANE] forbids by name")


def test_FC_C12_the_host_gets_its_telemetry_through_the_SAME_loop_as_everyone():
    """The positive form: the host is an ORDINARY entry, not a hardcoded set."""
    sources = _build(instances=[{"id": "architect-hosted", "role": "composeux-architect"},
                                {"id": "architect", "role": "composeux-architect"}],
                     session_jsonl_by_id={"architect-hosted": "/tmp/child.jsonl"})
    names = [s.name for s in sources]
    # the host keeps its historical unsuffixed names (argv/roster continuity)...
    assert "self_publisher" in names and "transcript_publisher" in names
    # ...and the child's set comes from the same loop, on its own transcript.
    assert "self_publisher:architect-hosted" in names
    host = _argv_of(sources, "self_publisher")
    child = _argv_of(sources, "self_publisher:architect-hosted")
    assert host[host.index("--id") + 1] == "architect"
    assert child[child.index("--id") + 1] == "architect-hosted"
    # identical argv SHAPE — the proof that ONE loop built both. Compare the flag
    # skeleton (values differ by construction: different id, different transcript).
    def _flags(argv):
        return [a for a in argv if a.startswith("--")]
    assert _flags(host) == _flags(child)


# The console-self widgets that still bind the HOST's topics from OUTSIDE any pane.
# Enumerated, not counted: a number would drift silently, and the point of the xfail is
# that a future builder can pick up an exact list.  Measured 2026-08-12.
_PRIVILEGED_SELF_WIDGETS = {
    # rail + chrome — genuinely per-instance readings in console-level furniture
    "intelligence_panel": "self/architect/{context_pct,subagents,registered,effort,...}",
    "presence":           "self/architect/avatar/expression",
    "live_dot":           "intelligence/architect/channel/alive",
    # config tab — the FC-C14 overlap (see the de-scope xfail below)
    "config_agents": "self/architect/agents", "config_skills": "self/architect/skills",
    "config_memory": "self/architect/memories",
    "config_identity": "intelligence/architect/config/compact_threshold",
    "config_manifest": "intelligence/architect/config/compact_threshold",
    "config_gifts": "intelligence/architect/config/compact_threshold",
    "config_escalation": "intelligence/architect/config/{heartbeat,retention}",
    "config_heartbeat": "intelligence/architect/config/{heartbeat,retention}",
    "config_permissions": "intelligence/architect/config/{heartbeat,retention}",
    "config_retention": "intelligence/architect/config/{heartbeat,retention}",
}

# CONSOLE-SELF dials that merely PERSIST under the host's config namespace.  Listed
# separately because the split rule gives them the opposite answer: "could its correct
# value differ between two Intelligences?"  Appearance, which panes are visible, the
# dashboard layout and the ACCOUNT's rate limit are properties of the console or of the
# account, shared by every Intelligence, so [CXC-FC-CONFIG-TAB] KEEPS them in the tab.
# What is wrong about them is narrower and cosmetic: their TOPIC carries `architect`
# where it should carry a console/account scope. Recorded so the next pass re-scopes the
# topic rather than mistakenly relocating the dial into a pane.
_CONSOLE_SELF_ON_HOST_TOPIC = {
    "account_panel":         "self/architect/rate_limit (the ACCOUNT's, not an instance's)",
    "config_appearance":     "intelligence/architect/config/console/{theme,font_scale,...}",
    "config_collab_widgets": "intelligence/architect/config/console/tabs",
    "console_dashboard":     "intelligence/architect/config/console/dashboard",
}


def test_FC_C12_the_privileged_self_widget_set_has_not_GROWN():
    """The measured truth, pinned so the deferral below cannot quietly get worse.

    These widgets are not BRANCHES — none of them asks "is this the host" — but each is a
    per-instance reading rendered in console-level furniture, which is the same privileged
    self one layer up.  A NEW one appearing is a regression even while these remain.
    """
    comp = parse_compose_yaml(CONSOLE_SCENE)
    pane_ids = set()

    def walk(w, inside):
        wid = getattr(w, "widget_id", "")
        inside = inside or (wid.endswith("_root") and wid.startswith("pane"))
        if inside:
            pane_ids.add(wid)
        for ch in getattr(w, "children", []) or []:
            walk(ch, inside)
    walk(comp.root, False)

    found = set()
    for ns, w in comp.subscriptions:
        wid = getattr(w, "widget_id", "?")
        parts = ns.split("/")
        if (wid not in pane_ids and len(parts) > 1 and parts[1] == "architect"
                and parts[0] in ("self", "thread", "intelligence")):
            found.add(wid)
    new = found - set(_PRIVILEGED_SELF_WIDGETS) - set(_CONSOLE_SELF_ON_HOST_TOPIC)
    assert not new, (
        f"NEW console-self widget(s) bound to the host's topics outside a pane: "
        f"{sorted(new)}. [CXC-FC-HOST-AS-PANE] — move it into the pane, or into a "
        f"console-self topic that names no instance.")


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-HOST-AS-PANE] widget half DEFERRED. The DATA-LAYER half is fixed (one "
    "telemetry loop, no host branch), but 17 console-self widgets outside any pane still "
    "bind `architect`'s topics — the rail (intelligence_panel, presence, account_panel), "
    "the header live_dot, and the whole config tab. Moving them is the FC-C14 de-scope "
    "plus a rail migration: the pane's CONFIGURE mode renders read-only labels today, so "
    "relocating LIVE dials into it is a new build, not a re-binding. Named individually "
    "in `_PRIVILEGED_SELF_WIDGETS` so the next pass has a work list, not a count."))
def test_FC_C12_no_widget_outside_a_pane_binds_a_literal_instance_id():
    comp = parse_compose_yaml(CONSOLE_SCENE)
    pane_ids = set()

    def walk(w, inside):
        wid = getattr(w, "widget_id", "")
        inside = inside or (wid.endswith("_root") and wid.startswith("pane"))
        if inside:
            pane_ids.add(wid)
        for ch in getattr(w, "children", []) or []:
            walk(ch, inside)
    walk(comp.root, False)

    declared_ids = {e["id"] for e in comp.instances}
    offenders = sorted({getattr(w, "widget_id", "?") for ns, w in comp.subscriptions
                        if getattr(w, "widget_id", "?") not in pane_ids
                        and ns.split("/")[1:2] and ns.split("/")[1] in declared_ids
                        and ns.split("/")[0] in ("self", "thread", "intelligence")})
    assert not offenders, f"console-self widgets bound to an instance id: {offenders}"


# ═════ FC-C13 — identity is on the glass ═══════════════════════════════════════
def test_FC_C13_every_pane_renders_its_instance_id_AND_its_role():
    """The MACHINE leg only.  The GLASS leg — David looks at a two-pane console and
    knows which Intelligence he is talking to WITHOUT clicking — is mandatory and
    non-delegable, because this assertion cannot see that a string is illegible,
    truncated, or attached to the wrong pane."""
    comp = parse_compose_yaml(PANE_SCENE)
    texts = {}

    def walk(w):
        t = getattr(w, "text", None)
        if isinstance(t, str) and t:
            texts[w.widget_id] = t
        for ch in getattr(w, "children", []) or []:
            walk(ch)
    walk(comp.root)

    for prefix, iid, role in (("pane1", "architect-hosted", "composeux-architect"),
                              ("pane2", "hardware", "composeio-hardware-architect")):
        assert texts.get(f"{prefix}_{iid}_id") == iid, f"{prefix}: id not on the glass"
        assert role in texts.get(f"{prefix}_{iid}_role", ""), \
            f"{prefix}: role not on the glass"


def test_FC_C13_the_identity_is_visible_in_BOTH_modes():
    """Identity must not be a thing you lose by switching to CONFIGURE — the header sits
    outside both mode-gated subtrees."""
    with open(PANE_SCENE, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    header = cfg["components"]["intelligence_pane"]["widget"]["children"][0]
    assert header["id"].endswith("_identity")
    assert "visible" not in header, "the identity header must never be mode-gated"


# ═════ FC-C14 — the pane toggles, and the tab de-scopes ════════════════════════
#
# ⚠ TWO TESTS HERE USED TO BE NAMED `test_FC_C14_*` AND ASSERT THINGS FC-C14 NEVER ASKS
# — that `_app/<id>/pane_mode` is declared, and that a label contains a path string.
# Both are real properties and both still hold; neither is the criterion.  FC-C14 asks
# that "no config-tab widget binds a literal instance id", and the tab had not de-scoped
# at all.  A mis-named passing test is strictly worse than an absent one: an absent test
# leaves a criterion unclaimed, a mis-named one CLAIMS it.  They are renamed to what they
# actually verify, and the criterion itself is asserted (and xfailed) below.
def test_pane_mode_state_is_per_pane_never_global():
    """Two panes may be in different modes at once, because the reason to configure one
    Intelligence is rarely a reason to configure the other ([CXC-FC-PANE-MODES])."""
    comp = parse_compose_yaml(PANE_SCENE)
    declared = {n.get("name") for n in comp.namespaces}
    assert "_app/architect-hosted/pane_mode" in declared
    assert "_app/hardware/pane_mode" in declared
    assert "_app/console/pane_mode" not in declared


def test_configure_mode_names_this_panes_own_memory_scope_on_the_glass():
    """[CXC-FC-SPLIT-RULE]: any dial whose correct value could DIFFER between two
    Intelligences belongs to the PANE."""
    comp = parse_compose_yaml(PANE_SCENE)
    texts = []

    def walk(w):
        t = getattr(w, "text", None)
        if isinstance(t, str):
            texts.append(t)
        for ch in getattr(w, "children", []) or []:
            walk(ch)
    walk(comp.root)
    joined = "\n".join(texts)
    # the pane NAMES its own memory scope — which is how a human can SEE that two
    # instances of one role share a store on purpose ([CXC-FC-PROJECT-DIR]).
    assert "~/compose-instances/composeux-architect/memory/" in joined
    assert "~/compose-instances/composeio-hardware-architect/memory/" in joined
    assert "intelligence/architect-hosted/*" in joined
    assert "intelligence/hardware/*" in joined


# ═════ FC-C15 — no second discovery path, slicer or transport table ════════════
def _py_files(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in (".git", "node_modules", "__pycache__",
                                                ".cache", "captures", "reference_images")]
        for fn in sorted(files):
            if fn.endswith(".py"):
                yield os.path.join(base, fn)


def test_FC_C15_exactly_one_wildcard_alive_subscription_exists():
    """A count of 0 FAILS as loudly as a count of 2, so a renamed site cannot pass by
    disappearing ([CXC-FC-ANTI])."""
    # By AST, not by grep: this filter is DOCUMENTED where it is defined (that is the
    # point of naming it once), and a text guard would fail on the prose explaining why
    # the guard exists. A STRING CONSTANT in the code is a subscription; a docstring is
    # not — `ast` distinguishes them for free.
    hits = []
    for p in _py_files(_REPO):
        if os.path.basename(p).startswith("test_"):
            continue
        try:
            tree = ast.parse(open(p, encoding="utf-8").read(), filename=p)
        except (SyntaxError, UnicodeDecodeError):
            continue
        docstrings = {id(ast.get_docstring(n, clean=False))
                      for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef))}
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "compose_projects/intelligence/+/channel/alive" in node.value
                    and id(node.value) not in docstrings):
                hits.append(f"{p}:{node.lineno}")
    assert len(hits) == 1, f"expected exactly ONE wildcard-alive site, found {hits}"
    assert "fleet_roster.py" in hits[0]


def test_FC_C15_exactly_one_transcript_slicer_implementation():
    """`resolve_child_jsonl` is the one session/transcript slicer; a second would
    disagree about which transcript belongs to whom, far from its cause."""
    defs = []
    for p in _py_files(os.path.join(_REPO, "python", "composeio")):
        tree = ast.parse(open(p, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "resolve_child_jsonl":
                defs.append(f"{p}:{node.lineno}")
    assert len(defs) == 1, f"expected ONE transcript slicer, found {defs}"


def test_FC_C15_exactly_one_transport_table():
    """`composeio.transport_registry` is the one place a transport SPEC becomes a
    transport.  Guarded as a REGISTRY count, not a function-name count: the table is the
    `_FACTORIES` map, and a second table would show up as a second module registering
    transport types rather than as a second function called `build_transport`."""
    from composeio import transport_registry
    assert callable(transport_registry.build)
    assert set(transport_registry.known_types()) >= {"mqtt"}
    modules = []
    for p in _py_files(os.path.join(_REPO, "python", "composeio")):
        src = open(p, encoding="utf-8").read()
        if "def register(" in src and "type_name" in src and "factory" in src:
            modules.append(p)
    assert len(modules) == 1, f"expected ONE transport registry, found {modules}"
    assert "transport_registry.py" in modules[0]


def test_FC_C15_exactly_one_project_dir_derivation():
    """[CXC-FC-DEFORK] restated as a count: `project_dir_for` has one definition site,
    and both spawn paths call it."""
    defs = []
    for p in _py_files(os.path.join(_REPO, "python", "composeio")):
        tree = ast.parse(open(p, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "project_dir_for":
                defs.append(f"{p}:{node.lineno}")
    assert len(defs) == 1, f"expected ONE project-dir derivation, found {defs}"
    assert "instance_home.py" in defs[0]

# ── FC-C14's ACTUAL criterion: "no config-tab widget binds a literal instance id" ──
#
# The config tab did NOT de-scope in this build.  Every dial below is PANE-TIER by
# [CXC-FC-SPLIT-RULE] — name a second Intelligence and ask whether the value could
# differ; for each of these it could — and each therefore owes a move into the pane's
# CONFIGURE mode.  Enumerated so the next pass inherits a work list.
_CONFIG_TAB_PANE_TIER_SITES = {
    "config_identity":    "intelligence/architect/config/compact_threshold",
    "config_manifest":    "intelligence/architect/config/compact_threshold",
    "config_gifts":       "intelligence/architect/config/compact_threshold",
    "config_escalation":  "intelligence/architect/config/{heartbeat,heartbeat/current,retention} + notify/rules",
    "config_heartbeat":   "intelligence/architect/config/{heartbeat,heartbeat/current,retention} + notify/rules",
    "config_permissions": "intelligence/architect/config/{heartbeat,heartbeat/current,retention} + notify/rules",
    "config_retention":   "intelligence/architect/config/{heartbeat,heartbeat/current,retention} + notify/rules",
    "config_agents":      "self/architect/agents",
    "config_skills":      "self/architect/skills",
    "config_memory":      "self/architect/memories",
}


def _config_tab_widgets(comp):
    """Every widget under the Configuration tab, by id prefix (`config_*`)."""
    out = {}
    for ns, w in comp.subscriptions:
        wid = getattr(w, "widget_id", "")
        if wid.startswith("config_") or wid == "console_dashboard":
            out.setdefault(wid, set()).add(ns)
    return out


def test_FC_C14_the_pane_tier_config_site_list_has_not_GROWN():
    """The measured truth, pinned.  A NEW pane-tier dial appearing in the console tab is
    a regression even while these ten remain."""
    comp = parse_compose_yaml(CONSOLE_SCENE)
    declared = {e["id"] for e in comp.instances}
    offenders = {wid for wid, nss in _config_tab_widgets(comp).items()
                 if any(n.split("/")[1:2] and n.split("/")[1] in declared for n in nss)}
    new = offenders - set(_CONFIG_TAB_PANE_TIER_SITES) - set(_CONSOLE_SELF_ON_HOST_TOPIC)
    assert not new, (
        f"NEW config-tab widget(s) binding a literal instance id: {sorted(new)}. "
        f"[CXC-FC-SPLIT-RULE]: name a second Intelligence and ask whether the value "
        f"could differ — if it could, it is a PANE dial.")


@pytest.mark.xfail(strict=False, reason=(
    "[CXC-FC-CONFIG-TAB] DEFERRED — the tab did not de-scope in this build. Ten "
    "pane-tier dials still bind `intelligence/architect/config/*` and `self/architect/*` "
    "from the console tab (named in _CONFIG_TAB_PANE_TIER_SITES): compact_threshold x3, "
    "heartbeat/retention/notify x4, agents, skills, memories. Moving them is not a "
    "re-binding: the pane's CONFIGURE mode renders READ-ONLY labels today, so each dial "
    "needs a live control built in the pane first. The topics are already per-id "
    "([CXC-FC-BINDINGS]), so the move is unblocked — it is unbuilt, not blocked."))
def test_FC_C14_no_config_tab_widget_binds_a_literal_instance_id():
    """The criterion as [CXC-FC-ACCEPT-GUARDS] states it."""
    comp = parse_compose_yaml(CONSOLE_SCENE)
    declared = {e["id"] for e in comp.instances}
    offenders = {}
    for wid, nss in _config_tab_widgets(comp).items():
        bad = {n for n in nss if n.split("/")[1:2] and n.split("/")[1] in declared}
        if bad:
            offenders[wid] = sorted(bad)
    assert not offenders, (
        f"config-tab widgets bound to a literal instance id: {offenders}")


def test_FC_C14_the_pane_carries_its_own_per_instance_config_scope():
    """The half that IS built: each pane's CONFIGURE mode names ITS OWN scope, so the
    dials have a home to move INTO."""
    comp = parse_compose_yaml(CONSOLE_SCENE)
    texts = []

    def walk(w):
        t = getattr(w, "text", None)
        if isinstance(t, str):
            texts.append(t)
        for ch in getattr(w, "children", []) or []:
            walk(ch)
    walk(comp.root)
    joined = "\n".join(texts)
    for iid in (e["id"] for e in comp.instances):
        assert f"intelligence/{iid}/*" in joined, f"{iid}: no config scope on the glass"

# ── [CXC-FC-PANE-MODES] the SELECTED-STATE clause (ratified 2026-08-12) ────────────
def test_a_mode_control_renders_its_selected_state_distinctly():
    """The clause adversarial QA earned, asserted where it can actually fail.

    The affordance was UNFALSIFIABLE before this: the pane satisfied every other word of
    [CXC-FC-PANE-MODES] with two identical labels, because `background: {expr: ...}` was
    DISCARDED IN SILENCE ([CXC-FC-BIND-NO-SILENT-DROP]).  Asserting that a mode NAMESPACE
    is declared — which is what the old `test_FC_C14_*` did — cannot catch that; only
    asserting that the two controls DIFFER in a rendered attribute can.
    """
    comp = parse_compose_yaml(CONSOLE_SCENE)
    by_widget = {}
    for b in comp.attr_bindings:
        wid = getattr(getattr(b, "widget", None), "widget_id", "")
        if "_mode_engage" in wid or "_mode_configure" in wid:
            by_widget.setdefault(wid, set()).add(b.attr)

    assert by_widget, "no mode control carries ANY binding — the toggle cannot show state"
    for wid, attrs in by_widget.items():
        assert attrs, f"{wid}: mode control has no bound rendered attribute"

    # ...and the two controls must resolve to DIFFERENT values for the same state, or
    # they are two chips that light together.
    for iid in (e["id"] for e in comp.instances):
        exprs = {}
        for b in comp.attr_bindings:
            wid = getattr(getattr(b, "widget", None), "widget_id", "")
            if wid.endswith(f"_{iid}_mode_engage") or wid.endswith(f"_{iid}_mode_configure"):
                src = getattr(getattr(b, "expr", None), "source", None)
                if src:
                    exprs[wid] = src
        assert len(exprs) == 2, f"{iid}: expected two bound mode controls, got {sorted(exprs)}"
        assert len(set(exprs.values())) == 2, (
            f"{iid}: both mode controls bind the SAME expression {set(exprs.values())} — "
            f"they would light together, so neither shows a selected state")


def test_the_engine_REFUSES_a_binding_it_cannot_apply():
    """[CXC-FC-BIND-NO-SILENT-DROP] — the platform half, which outlives this build.

    This is the finding's real value: `background` is fixed, but the CLASS is what
    recurs. An attribute the engine cannot bind must raise, not fall through.
    """
    from composeio.render.engine.bindings import extract_bindings
    with pytest.raises(ValueError, match=r"CXC-BIND-NO-SILENT-DROP"):
        extract_bindings({"type": "stack", "corner_radius": {"expr": "1 + 1"}}, "probe")

    # ...and an attribute whose binding-shaped dict is consumed ELSEWHERE is exempt by
    # NAME, not by pattern — an unregistered attribute stays the loud case.
    from composeio.render.engine.bindings import BOUND_ELSEWHERE_ATTRS
    assert "drag_value" in BOUND_ELSEWHERE_ATTRS
    extract_bindings({"type": "pixel_stack",
                      "drag_value": {"namespace": "demo/x"}}, "probe")   # no raise


def test_background_is_bindable_and_coerces_as_a_colour():
    """The specific fix, kept separate from the class so neither hides the other."""
    from composeio.render.engine.bindings import BINDABLE_ATTRS, COLOR_ATTRS
    assert "background" in BINDABLE_ATTRS and "background" in COLOR_ATTRS
