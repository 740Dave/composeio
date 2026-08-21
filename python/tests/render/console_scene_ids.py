"""The console scene's IDs, derived — never re-typed — for the tests.

[CXC-FC-UNIT] (2026-08-12): the console hosts N Intelligences, declared as `(id, role)`
PAIRS in the scene's `instances:` block, and each one's whole collaboration surface is a
`{use: intelligence_pane}` instantiation.  Every per-instance widget id is therefore
rooted at `{{id_prefix}}_{{instance_id}}_` — `pane1_architect-hosted_composer_field`,
not `composer_field`.

A test that hardcodes `composer_field` or `intelligence/architect/out` freezes the WRONG
thing: it freezes WHICH Intelligence and HOW MANY, when what the frozen-wiring discipline
protects is the binding SHAPE — a chat_thread on {out, in}, a composer on /compose, a
link dot on /channel/alive, one set per declared instance.  So the scene-coupled tests
derive their expected ids and topics from the same declaration the widgets do, and adding
or repointing an instance moves the scene and the tests together.  That is the property
that makes the fleet safe to change at all.

⚠ THIS MODULE IS WHY THE MIGRATION WAS AFFORDABLE.  Converting the console from a bespoke
single-pane subtree to two composite instantiations renamed every widget in that subtree.
Thirty-six tests referenced those names.  Routing them through one derivation turned
thirty-six edits-that-could-each-be-wrong into one seam — and the seam is checkable: if it
resolved to the wrong pane, every one of them would fail at once rather than one of them
silently asserting nothing.

PRIMARY vs SECONDARY.  `primary_*` is the FIRST declared instance — the one a
single-surface test means when it says "the composer".  Tests that care about the fleet
property (two panes, disjoint bindings) ask for `instance_ids()` and assert over all of
them.

DELIBERATELY NOT HERE: `self/architect/*` and `thread/architect/narration`.  Those are
HOST telemetry, published by the console's transcript-tailing sources pinned to the host's
session; they do NOT follow an instance declaration and the tests keep asserting them
literally.  Two ids, two roles ([CXC-IH-5A]).
"""

from __future__ import annotations

import functools
import os
import sys
from typing import List

_HERE = os.path.dirname(os.path.abspath(__file__))
_PY_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir, os.pardir))
if _PY_ROOT not in sys.path:
    sys.path.insert(0, _PY_ROOT)

REPO_ROOT = os.path.abspath(os.path.join(_PY_ROOT, os.pardir))
CONSOLE_SCENE = os.path.join(REPO_ROOT, "compose_projects", "intelligence", "console",
                             "console.compose.yaml")

# The `id_prefix` each console pane is instantiated with, in declaration order.  Read from
# the scene rather than assumed, so a re-ordered or renamed pane cannot silently point the
# whole test suite at the wrong Intelligence.
_PANE_PREFIX_KEY = "id_prefix"


@functools.lru_cache(maxsize=8)
def _scene(scene: str = CONSOLE_SCENE):
    import yaml
    with open(scene, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.lru_cache(maxsize=8)
def _pane_uses(scene: str = CONSOLE_SCENE) -> tuple:
    """Every pane call site, in scene order, as `{instance_id, role, id_prefix}` dicts.

    Handles BOTH authoring forms, because the scene has used each in turn and a test seam
    that only understood one would fail with "the console instantiates no pane" — a message
    that points at the scene when the fault is here:

      * `for_each_instance: intelligence_pane` — the CURRENT form.  ONE generator, N panes,
        generated from the `instances:` list so the list is the single source
        ([CXC-FC-VAR-SCOPE]).  `id_prefix` is positional and 1-based, matching
        `instances.py::_expand_for_each`.
      * explicit `{use: intelligence_pane, instance_id: ...}` sites — still valid authoring,
        and what a scene writes when two panes need genuinely different parameters.
    """
    found: List[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("use") == "intelligence_pane":
                found.append({"instance_id": node.get("instance_id"),
                              "role": node.get("role"),
                              _PANE_PREFIX_KEY: node.get(_PANE_PREFIX_KEY, "pane")})
            elif node.get("for_each_instance") == "intelligence_pane":
                base = str(node.get("id_prefix_base", "pane"))
                for i, inst in enumerate(
                        (_scene(scene).get("instances") or []), start=1):
                    if isinstance(inst, dict) and inst.get("id"):
                        found.append({"instance_id": inst["id"],
                                      "role": inst.get("role", ""),
                                      _PANE_PREFIX_KEY: f"{base}{i}"})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(_scene(scene).get("widgets"))
    return tuple(found)


def instance_ids(scene: str = CONSOLE_SCENE) -> List[str]:
    """Every DECLARED instance id, in declaration order ([CXC-FC-UNIT])."""
    return [str(e["id"]) for e in (_scene(scene).get("instances") or [])
            if isinstance(e, dict) and e.get("id")]


def chat_id(scene: str = CONSOLE_SCENE) -> str:
    """The FIRST declared instance's id.

    Kept under its historical name because that is what a single-surface test means, and
    renaming it would have been a second edit to thirty-six call sites for no behaviour.
    """
    ids = instance_ids(scene)
    assert ids, "the console scene must declare at least one instance"
    return ids[0]


def chat_ns(suffix: str, scene: str = CONSOLE_SCENE) -> str:
    """`compose_projects/intelligence/<primary id>/<suffix>` — the channel topic the primary pane binds."""
    return f"intelligence/{chat_id(scene)}/{suffix.lstrip('/')}"


def pane_prefix(index: int = 0, scene: str = CONSOLE_SCENE) -> str:
    """The `id_prefix` of the pane at `index` (e.g. `pane1`)."""
    uses = _pane_uses(scene)
    assert uses, "the console scene must instantiate intelligence_pane"
    return str(uses[index].get(_PANE_PREFIX_KEY, "pane"))


def pane_widget(name: str, index: int = 0, scene: str = CONSOLE_SCENE) -> str:
    """The full widget id of `name` inside the pane at `index`.

    `pane_widget("composer_field")` -> `pane1_architect-hosted_composer_field`.
    """
    uses = _pane_uses(scene)
    assert uses, "the console scene must instantiate intelligence_pane"
    use = uses[index]
    return f"{use.get(_PANE_PREFIX_KEY, 'pane')}_{use['instance_id']}_{name}"


# ── The names a single-surface test used to hardcode, now derived ────────────────────
def composer_field(scene: str = CONSOLE_SCENE) -> str:
    return pane_widget("composer_field", 0, scene)


def composer(scene: str = CONSOLE_SCENE) -> str:
    """The composer STACK (field + SEND), as opposed to the field itself."""
    return pane_widget("composer", 0, scene)


def composer_send(scene: str = CONSOLE_SCENE) -> str:
    return pane_widget("send", 0, scene)


def composer_send_label(scene: str = CONSOLE_SCENE) -> str:
    return pane_widget("send_label", 0, scene)


def thread(scene: str = CONSOLE_SCENE) -> str:
    return pane_widget("thread", 0, scene)


def send_trigger_ns(scene: str = CONSOLE_SCENE) -> str:
    """`_app/<id>/composer_send` — the commit trigger, now PER PANE.

    It was the console-scoped literal `_app/console/composer_send`, which is a
    per-instance binding wearing a per-console name: two panes on that topic would fire
    each other's SEND button ([CXC-FC-BINDINGS]).
    """
    return f"_app/{chat_id(scene)}/composer_send"


def pane_mode_ns(index: int = 0, scene: str = CONSOLE_SCENE) -> str:
    """`_app/<id>/pane_mode` — per-pane, never global ([CXC-FC-PANE-MODES])."""
    return f"_app/{instance_ids(scene)[index]}/pane_mode"

def narration_ns(scene: str = CONSOLE_SCENE) -> str:
    """`thread/<primary id>/narration` — the pane's narration voice.

    PER-INSTANCE, and that is the fix rather than the accident.  The console's bespoke
    thread subscribed `thread/architect/narration` (the HOST) beside
    `compose_projects/intelligence/<child>/{out,in}` — one widget showing one Intelligence's narration next
    to another's conversation.  In a pane the legs are one parameter and cannot disagree.
    """
    return f"thread/{chat_id(scene)}/narration"
