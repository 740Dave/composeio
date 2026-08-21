"""The scene's DECLARED INSTANCE LIST ([SPEC:CXC-FLEET] [CXC-FC-UNIT], [CXC-FC-VAR-SCOPE]).

A fleet scene declares which Intelligences it hosts, ONCE, at document level::

    instances:
      - {id: architect-hosted, role: composeux-architect}
      - {id: hardware,         role: composeio-hardware-architect}

THE UNIT IS A PAIR, NOT A SCALAR ([CXC-FC-UNIT]).  `role` is NOT derivable from `instance_id`:
`ptycore.resolve_meta_manifest(intelligence_id, role)` takes role as a PARAMETER precisely because
chain leg 2 (`compose_projects/intelligence/<id>.meta.json`) MISSES for every child that is not hand-curated, so
there is no per-id manifest to read the role out of.  Deriving role from a naming convention on
the id (`architect-hosted` -> `architect`) is FORBIDDEN: it is a second, undeclared identity
mechanism that works until the first id that does not follow the convention, and it silently
produces a WRONG project dir rather than a refusal ([CXC-FC-THREE-AXES] prohibition).

WHAT THIS REPLACES.  `console.compose.yaml` carried `variables: {chat_id: architect-hosted}`, and
`start-console-daemons.sh` shelled out to `data_sources.scene_chat_id()` to read THE SAME LINE out
of THE SAME FILE — one Intelligence, spelled once, read by two paths.  The list is the single
source now, and `$chat_id` is DERIVED from its first entry rather than declared beside it:
[CXC-FC-VAR-SCOPE] refuses "two live sources for one binding", which is the fork class this corpus
keeps paying for.  A scene that declares no instances gets no derived variables and behaves
exactly as it did before, so nothing that never had a fleet notices this exists.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

# The doc variables a declared instance list SEEDS. Derived, never declared beside the list.
FIRST_ID_VAR = "chat_id"
FIRST_ROLE_VAR = "chat_role"


def parse_instances(config: dict) -> List[Dict[str, Any]]:
    """The declared `(instance_id, role)` pairs, normalised and order-preserving.

    Order is meaning: the FIRST entry is the scene's primary Intelligence, and it is the one
    `$chat_id` derives from, so a single-pane scene reads exactly as it did before the list
    existed.  A malformed entry is DROPPED with its id absent rather than half-registered — an
    instance with no id has no topics, and a pane bound to nothing renders a lie.
    """
    raw = config.get("instances")
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in raw:
        if isinstance(entry, str):
            entry = {"id": entry}
        if not isinstance(entry, dict):
            continue
        iid = str(entry.get("id") or "").strip()
        if not iid or iid in seen:
            # A DUPLICATE id is dropped, not merged: two panes on one id would take one
            # channel-bridge flock and one telemetry namespace between them, and the second
            # would be silently mute ([CXC-TERM-CHILD-ID]'s sibling-collision rule, at scene
            # scope).
            continue
        role = str(entry.get("role") or "").strip()
        if not role:
            # ⚠ AN ABSENT ROLE REFUSES AT THE SCENE LAYER TOO ([CXC-FC-UNIT]).
            #
            # The SPAWN path already refuses (`project_dir_for` raises `RoleRefused`), so
            # nothing was ever mis-keyed — but a roleless instance still reached the GLASS,
            # where the pane rendered `~/compose-instances//memory/`: an empty role
            # collapses the path and the surface asserts a memory location that does not
            # exist. A surface stating a false fact is the failure class this spec exists
            # to close, and "the spawn would have caught it" is no defence when the human
            # is reading the pane, not the traceback.
            raise ValueError(
                f"[CXC-FC-UNIT] instance {iid!r} declares no `role`. An Intelligence is "
                f"declared by an (instance_id, role) PAIR: role keys memory, the "
                f"bootstrap tier and agent registration ([CXC-FC-THREE-AXES] axis 1), "
                f"and it is NOT derivable from the id — deriving it by naming convention "
                f"is forbidden and produces a WRONG project dir rather than a refusal. "
                f"Add `role:` to this entry.")
        seen.add(iid)
        rec = {"id": iid, "role": role}
        for k, v in entry.items():
            if k not in ("id", "role"):
                rec[k] = v
        out.append(rec)
    return out


def derived_variables(instances: List[Dict[str, Any]]) -> Dict[str, str]:
    """RETIRED — returns nothing, deliberately.

    ⚠ THIS FUNCTION USED TO SEED `$chat_id` FROM `instances[0]`, AND THAT WAS THE OLD
    MECHANISM STILL BREATHING.  The reasoning at the time was that deriving the variable was
    better than declaring it, because a derivation cannot disagree with its source.  True as far
    as it goes — and beside the point.  [CXC-FC-VAR-SCOPE] does not ask for `chat_id` to have
    ONE source; it asks for the binding to live at PARAM SCOPE, per pane.  A scene keeping a
    single document-level `$chat_id` alive is a scene that can only ever name ONE Intelligence,
    however honestly that one name is computed — and the console proved it by keeping its
    bespoke single-pane subtree working right up until it was migrated.

    Kept as a named no-op rather than deleted so the retirement is VISIBLE to a reader who goes
    looking for the seam, and so the two callers below need no signature change.
    """
    del instances
    return {}


INSTANCE_NS_KEY = "instance_namespaces"
_NS_TOKEN = re.compile(r"\{\{\s*(instance_id|role)\s*\}\}")


def expand_instance_namespaces(config: dict,
                               instances: List[Dict[str, Any]]) -> List[dict]:
    """Stamp `instance_namespaces:` once PER DECLARED INSTANCE into `namespaces:`.

        instance_namespaces:
          - {name: "intelligence/{{instance_id}}/out", state_engine: json, export: true}

    WHY THIS EXISTS.  `expand_components()` rewrites WIDGETS; it never touches `namespaces:`.
    So when the console moved to N panes, its ten channel declarations had to be written out as
    literals, one block per instance — and that quietly destroyed the property the whole
    parameterisation was for: adding or repointing an Intelligence stopped being ONE edit and
    became "edit the instance list, then remember to edit five namespace lines that look
    nothing like it".  A declaration that must be kept in sync by hand with a list two hundred
    lines above it is the same fork class as two sources for one binding
    ([CXC-FC-VAR-SCOPE]) — it just fails later, as a topic nobody declared rather than a topic
    nobody publishes.

    Only `{{instance_id}}` and `{{role}}` substitute, and only inside strings.  This is
    deliberately NOT a second component engine: it is the instance list stamping its own
    per-instance topics, with the same two tokens the pane composite takes as params
    ([CXC-FC-UNIT]).  Anything richer belongs in `components:`.

    Literal `namespaces:` entries are KEPT and come first — a scene still declares its
    console-self topics (`console/link/state`, `fleet/roster`) literally, because those are
    exactly the ones that must NOT multiply by instance ([CXC-FC-SPLIT-RULE]).
    """
    template = config.get(INSTANCE_NS_KEY)
    existing = list(config.get("namespaces") or [])
    if not isinstance(template, list) or not template:
        return existing

    def _subst(node, params):
        if isinstance(node, str):
            return _NS_TOKEN.sub(lambda m: str(params.get(m.group(1), m.group(0))), node)
        if isinstance(node, dict):
            return {k: _subst(v, params) for k, v in node.items()}
        if isinstance(node, list):
            return [_subst(v, params) for v in node]
        return node

    seen = {n.get("name") for n in existing if isinstance(n, dict)}
    out = list(existing)
    for inst in instances:
        params = {"instance_id": inst["id"], "role": inst.get("role", "")}
        for entry in template:
            if not isinstance(entry, dict):
                continue
            stamped = _subst(dict(entry), params)
            name = stamped.get("name")
            if not name or name in seen:
                # A literal declaration WINS over a stamped one, same rule as components:
                # it is how a scene gives one instance a special engine or scope without
                # forking the template.
                continue
            seen.add(name)
            out.append(stamped)
    return out


FOR_EACH_KEY = "for_each_instance"
# The nested widget-list keys this pass descends into — the same set `components.py`
# uses, for the same reason: the console's whole tree hangs off `tabs:`.
_CHILD_LIST_KEYS = ("children", "tabs", "widgets")


def _expand_for_each(node, instances: List[Dict[str, Any]]):
    """Rewrite every `{for_each_instance: <component>, ...}` entry into N `{use: ...}` sites.

        - for_each_instance: intelligence_pane
          id_prefix_base: pane
          params: {pane_grow: 1}

    becomes one `{use: intelligence_pane, instance_id: <id>, role: <role>,
    id_prefix: pane1|pane2|..., pane_grow: 1}` per DECLARED instance, in declaration order.

    WHY THIS EXISTS — and it is the second half of the same defect the namespace template
    closes.  Writing the `use:` sites out by hand made `instances:` and the widget tree TWO
    declarations of one fact: the list said which Intelligences exist, the call sites said
    which get panes, and nothing made them agree.  Repointing an instance in the list left
    the pane bound to the old id — a console that renders two panes over topics nobody
    declared, which is [CXC-FC-VAR-SCOPE]'s "two live sources for one binding" wearing a
    different hat.  Panes stay DECLARED ([DD-FC-PANE-SOURCE]) — they are declared ONCE.

    `id_prefix` is generated 1-based (`pane1`, `pane2`) rather than from the instance id,
    because the id may contain characters that are fine in a topic and awkward in a widget
    id, and because a stable positional prefix is what makes a golden capture stable.
    """
    if isinstance(node, list):
        out = []
        for item in node:
            if isinstance(item, dict) and FOR_EACH_KEY in item:
                component = item[FOR_EACH_KEY]
                base = str(item.get("id_prefix_base", "pane"))
                extra = item.get("params") or {}
                for i, inst in enumerate(instances, start=1):
                    site = {"use": component,
                            "instance_id": inst["id"],
                            "role": inst.get("role", ""),
                            "id_prefix": f"{base}{i}"}
                    site.update(extra)
                    # explicit layout keys on the generator apply to every copy
                    for k, v in item.items():
                        if k not in (FOR_EACH_KEY, "id_prefix_base", "params"):
                            site[k] = v
                    out.append(site)
                continue
            out.append(_expand_for_each(item, instances))
        return out
    if isinstance(node, dict):
        out = dict(node)
        for key in _CHILD_LIST_KEYS:
            if isinstance(out.get(key), list):
                out[key] = _expand_for_each(out[key], instances)
        return out
    return node


def seed_config_variables(config: dict) -> List[Dict[str, Any]]:
    """Parse the declared instance list, stamp everything derived from it, and return it.

    TWO derivations, both keyed off the ONE list, both running BEFORE component stamping:
      * `instance_namespaces:` -> one topic set per instance;
      * `for_each_instance:`   -> one `{use: ...}` pane site per instance.

    Seeds NOTHING into `variables:` — see `derived_variables`.  A scene with no `instances:`
    and neither key is returned untouched, so every non-fleet scene parses byte-for-byte as
    before.
    """
    instances = parse_instances(config)
    if config.get(INSTANCE_NS_KEY):
        config["namespaces"] = expand_instance_namespaces(config, instances)
        config.pop(INSTANCE_NS_KEY, None)     # consumed, like `variables:`
    for key in ("widgets", "modules"):
        if isinstance(config.get(key), list):
            config[key] = _expand_for_each(config[key], instances)
    return instances
