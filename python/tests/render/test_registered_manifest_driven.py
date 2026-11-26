"""
test_registered_manifest_driven — the REGISTERED sensor/widget are
MANIFEST-DRIVEN, never a hardcoded "role def + N dusk gifts" summary
([SPEC:CXC-INTELLIGENCE-CONSOLE] [CXC-IC-SELF]).

This is the enforcing regression for the vestigial-REGISTERED-sensor defect:
the console read "✓ 4/4 role def + 3 dusk gifts" — a baked-in caption that
silently misdescribed the manifest once `registration.required` grew past
role-descriptor-plus-gifts (it now carries role descriptor + bootstrap-read +
3 dusk gifts, and the meta-object declares first-class bootstrap docs).

The load-bearing property under test: ADD A MANIFEST ENTRY -> the sensor count
AND the widget caption follow, with NO code change.  The full path is exercised:
meta-object -> SelfSensor manifest -> self_publisher payload -> the caption
IntelligenceCard renders.

  T1  sensor enriches EVERY first-class artifact with a KIND (role def,
      bootstrap-read from its own role, dusk gift) — not just role_descriptor +
      dusk_gifts.  bootstrap-read's kind is present (the old code left it None).
  T2  the manifest-driven caption reads "role def + bootstrap-read + 3 dusk
      gifts" — NOT the hardcoded "role def + 4 dusk gifts".
  T3  add a 6th required artifact (toolbox) -> count -> 6/6 and the caption
      gains "toolbox", with NO code change (the anti-regression heart).
  T4  the publisher carries `kind` end-to-end so the widget can compose it.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# self_publisher lives outside the composeio package (compose_projects/intelligence/publisher);
# import it by path so this test runs from the pytest rootdir.
_REPO = Path(__file__).resolve().parents[3].parent
sys.path.insert(0, str(_REPO / "python"))
sys.path.insert(0, str(_REPO / "compose_projects" / "intelligence" / "publisher"))

from composeio.self_sensor.sensor import SelfSensor, load_registration_manifest  # noqa: E402
from composeio.render.widgets.intelligence_card import _registered_caption  # noqa: E402
from composeio.render.widgets.registered_checklist import RegisteredChecklistWidget  # noqa: E402
import self_publisher  # noqa: E402


_ROLE_DESCRIPTOR = ".claude/agents/role.md"
_BOOTSTRAP_READ = "bootstrap/session-bootstrap-read.md"
_TOOLBOX = "bootstrap/toolbox.md"
_GIFTS = [
    "bootstrap/dusk-gifts/registration-protocol.md",
    "bootstrap/dusk-gifts/README.md",
    "bootstrap/dusk-gifts/relational-registration.md",
]


def _meta(required: list[str], *, with_toolbox: bool = False) -> dict:
    obj = {
        "id": "architect",
        "role_descriptor": {"path": _ROLE_DESCRIPTOR, "why": "the role"},
        "bootstrap_read": {"path": _BOOTSTRAP_READ, "role": "bootstrap_read",
                           "why": "the wake read", "standalone_ux": True},
        "dusk_gifts": [
            {"path": _GIFTS[0], "role": "keystone", "why": "the six"},
            {"path": _GIFTS[1], "role": "theory", "why": "the premise"},
            {"path": _GIFTS[2], "role": "relationship", "why": "the seam"},
        ],
        "registration": {"required": required},
    }
    if with_toolbox:
        obj["toolbox"] = {"path": _TOOLBOX, "role": "toolbox",
                          "why": "the toolbox", "standalone_ux": True}
    return obj


def _jsonl_reading(paths: list[str], tmp: Path) -> str:
    """A session .jsonl that Reads every given path (so every row is green)."""
    lines = []
    for p in paths:
        lines.append({"type": "assistant", "message": {"usage": {},
                      "content": [{"type": "tool_use", "name": "Read",
                                   "input": {"file_path": f"/Users/you/composeio/{p}"}}]}})
    fp = tmp / "session.jsonl"
    fp.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return str(fp)


def _caption_from(manifest_path: str, jsonl: str) -> tuple[int, str, list]:
    """Drive the full path: sensor -> publisher payload -> the caption + kinds
    the IntelligenceCard would compose."""
    parsed = SelfSensor.from_jsonl(jsonl, registration_manifest=manifest_path).parse()
    lines = self_publisher.compute_lines(parsed)
    payload = lines["self/registered"]
    rows = RegisteredChecklistWidget._coerce_rows(payload)
    kinds = [r.get("kind") for r in rows]
    return payload["total"], _registered_caption(kinds), rows


def test_sensor_enriches_every_artifact_with_a_kind(tmp_path):
    required = [_ROLE_DESCRIPTOR, _BOOTSTRAP_READ, *_GIFTS]
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(_meta(required)), encoding="utf-8")
    spec = load_registration_manifest(str(mp))
    # role def
    assert spec["meta"][_ROLE_DESCRIPTOR]["kind"] == "role def"
    # T1: bootstrap-read carries a KIND derived from its own role (was None before)
    assert spec["meta"][_BOOTSTRAP_READ]["kind"] == "bootstrap-read"
    # dusk gifts
    for g in _GIFTS:
        assert spec["meta"][g]["kind"] == "dusk gift"


def test_caption_is_manifest_driven_not_hardcoded(tmp_path):
    required = [_ROLE_DESCRIPTOR, _BOOTSTRAP_READ, *_GIFTS]
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(_meta(required)), encoding="utf-8")
    jsonl = _jsonl_reading(required, tmp_path)

    total, caption, rows = _caption_from(str(mp), jsonl)
    assert total == 5
    # T2: the manifest-driven caption names bootstrap-read, NOT "role def + 4 dusk gifts"
    assert caption == "role def + bootstrap-read + 3 dusk gifts"
    assert caption != "role def + 4 dusk gifts"  # the old hardcoded string
    assert all(r["ok"] for r in rows)             # all read -> green


def test_adding_a_manifest_entry_moves_count_and_caption_no_code_change(tmp_path):
    # 6-entry manifest: role def + bootstrap-read + TOOLBOX + 3 dusk gifts.
    required = [_ROLE_DESCRIPTOR, _BOOTSTRAP_READ, _TOOLBOX, *_GIFTS]
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(_meta(required, with_toolbox=True)), encoding="utf-8")
    jsonl = _jsonl_reading(required, tmp_path)

    total, caption, _ = _caption_from(str(mp), jsonl)
    # T3: count + caption ADAPT with no code change
    assert total == 6
    assert caption == "role def + bootstrap-read + toolbox + 3 dusk gifts"


def test_publisher_carries_kind_end_to_end(tmp_path):
    # T4: the kind survives sensor -> publisher payload (so the widget can use it).
    required = [_ROLE_DESCRIPTOR, _BOOTSTRAP_READ, *_GIFTS]
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(_meta(required)), encoding="utf-8")
    jsonl = _jsonl_reading(required, tmp_path)
    parsed = SelfSensor.from_jsonl(jsonl, registration_manifest=str(mp)).parse()
    payload = self_publisher.compute_lines(parsed)["self/registered"]
    kinds = [it.get("kind") for it in payload["items"]]
    assert kinds == ["role def", "bootstrap-read", "dusk gift", "dusk gift", "dusk gift"]


def test_legacy_payload_without_kinds_is_honest_not_false(tmp_path):
    # A payload with no kinds -> honest dynamic fallback, never the false role-def claim.
    assert _registered_caption([None, None, None]) == "3 artifacts"
    assert _registered_caption([]) == ""


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
