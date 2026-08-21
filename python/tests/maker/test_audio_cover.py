"""
[FLOW:MAKER-PROJECT] S6 — the DY-SV5F audio cover (a PURE bus SUBSCRIBER).

Proves the additive audio peer the SAME C3 drives alongside the throttle read:

  A. the DY-SV5F UART "play track" frame + checksum (protocol correctness);
  B. the contact IDENTIFICATION edge (unknown -> hostile), derived at the leaf
     from successive dradis/contacts frames — fires ONCE per id, NOT on repeats;
  C. the FTL JUMP edge (-> "jumping"), fires on the transition only;
  D. the cue -> NNNNN.mp3 track mapping;
  E. the AudioCover reference model: input from the bus, output to the module,
     NEVER a publish;
  F. the S4 mcu-config ACTUATOR emission (the OUTPUT dual falls out of the YAML);
  G. drift guards: the namespaces + contact-dict shape are the EXACT bsg wire
     contracts (grounded against ~/maker/bsg/world/{publish,sensor,contract}.py).
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PY_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
if PY_ROOT not in sys.path:
    sys.path.insert(0, PY_ROOT)

from composeio.render.engine.parser import parse_compose_yaml
from composeio.maker import (
    emit_mcu_config,
    NS_CONTACTS, NS_JUMP, JUMP_FIRE_VALUE, IDENTIFIED_ALLEGIANCE,
    CUE_CONDITION_ONE, CUE_JUMP, CUE_TRACKS,
    track_filename, cue_filename,
    DYSV5F_BAUD, dysv5f_checksum, dysv5f_play_track, dysv5f_frame_hex,
    contact_is_identified, ContactIdentificationDetector, JumpDetector,
    AudioCover, COVER_HOOK,
)

EXAMPLES = os.path.join(PY_ROOT, "composeio", "render", "examples")
DUAL_THROTTLE = os.path.join(EXAMPLES, "dual_throttle.compose.yaml")


# ---------------------------------------------------------------------------
# helpers — synthetic contact dicts in the EXACT bsg wire shape
# (bsg/world/contract.py::contact -> {id, type, bearing, range, allegiance, label})
# ---------------------------------------------------------------------------

def _contact(cid, *, allegiance, type_="small_unknown", label="UNKNOWN"):
    return {"id": cid, "type": type_, "bearing": 42.0, "range": 5.0,
            "allegiance": allegiance, "label": label}


def _unknown(cid):
    return _contact(cid, allegiance="neutral", type_="small_unknown", label="UNKNOWN")


def _hostile(cid, type_="raider"):
    return _contact(cid, allegiance="hostile", type_=type_, label="DRADIS-1")


def _friendly(cid, type_="viper"):
    return _contact(cid, allegiance="friendly", type_=type_, label="VIPER-1")


# ===========================================================================
# A. the DY-SV5F UART "play track" frame + checksum
# ===========================================================================

def test_checksum_formula_includes_the_aa_header():
    # The documented single-byte PLAY command is AA 02 00 AC — its checksum
    # (0xAA + 0x02 + 0x00 = 0xAC) PROVES the sum-check includes the 0xAA header.
    assert dysv5f_checksum(bytes([0xAA, 0x02, 0x00])) == 0xAC
    assert dysv5f_checksum(bytes([0xAA, 0x04, 0x00])) == 0xAE   # STOP
    assert dysv5f_checksum(bytes([0xAA, 0x06, 0x00])) == 0xB0   # NEXT


def test_play_track_frame_shape_and_checksum():
    # Track 8 -> AA 07 02 00 08 BB   (0xAA+7+2+0+8 = 187 = 0xBB)
    assert dysv5f_play_track(8) == bytes([0xAA, 0x07, 0x02, 0x00, 0x08, 0xBB])
    # Track 9 -> AA 07 02 00 09 BC
    assert dysv5f_play_track(9) == bytes([0xAA, 0x07, 0x02, 0x00, 0x09, 0xBC])
    # Track 123 (a two-nibble low byte) -> AA 07 02 00 7B 2E
    assert dysv5f_play_track(123) == bytes([0xAA, 0x07, 0x02, 0x00, 0x7B, 0x2E])
    # a high-byte track (300 = 0x012C) exercises the big-endian split
    f = dysv5f_play_track(300)
    assert f[3] == 0x01 and f[4] == 0x2C
    assert f[-1] == (0xAA + 0x07 + 0x02 + 0x01 + 0x2C) & 0xFF


def test_play_track_range_guard():
    for bad in (0, -1, 0x10000):
        with pytest.raises(ValueError):
            dysv5f_play_track(bad)


def test_frame_hex_is_readable():
    assert dysv5f_frame_hex(dysv5f_play_track(8)) == "AA 07 02 00 08 BB"


def test_baud_is_9600():
    assert DYSV5F_BAUD == 9600


# ===========================================================================
# B. the contact IDENTIFICATION edge (unknown -> hostile)
# ===========================================================================

def test_identify_helper():
    assert contact_is_identified(_hostile("c1")) is True
    assert contact_is_identified(_unknown("c1")) is False
    assert contact_is_identified(_friendly("c1")) is False
    assert contact_is_identified("not a dict") is False


def test_fires_once_on_unknown_to_hostile():
    d = ContactIdentificationDetector()
    # frame 1: baseline (unknown) — establishes the "seen" state, no fire
    assert d.update([_unknown("c1")]) == []
    # frame 2: the reveal (neutral -> hostile) — fires ONCE
    assert d.update([_hostile("c1")]) == [CUE_CONDITION_ONE]


def test_does_not_fire_on_repeat_hostile():
    d = ContactIdentificationDetector()
    d.update([_unknown("c1")])
    d.update([_hostile("c1")])                 # the reveal (fired)
    assert d.update([_hostile("c1")]) == []    # hostile -> hostile: NO fire
    assert d.update([_hostile("c1")]) == []    # still no fire


def test_never_identified_never_fires():
    d = ContactIdentificationDetector()
    assert d.update([_unknown("c1")]) == []
    assert d.update([_unknown("c1")]) == []    # neutral -> neutral: no fire
    assert d.update([_friendly("f1")]) == []   # a friendly is not a reveal
    assert d.update([_friendly("f1")]) == []


def test_first_sight_hostile_does_not_fire():
    # An edge needs two frames (same rule the dradis uses to derive heading).  A
    # contact first OBSERVED already hostile has no observed reveal -> baseline.
    d = ContactIdentificationDetector()
    assert d.update([_hostile("c1")]) == []
    assert d.update([_hostile("c1")]) == []


def test_per_id_independent_and_multiple_reveals_in_one_frame():
    d = ContactIdentificationDetector()
    d.update([_unknown("c1"), _unknown("c2")])          # baseline both
    # both flip in the same frame -> two cues (one per id)
    assert d.update([_hostile("c1"), _hostile("c2")]) == \
        [CUE_CONDITION_ONE, CUE_CONDITION_ONE]
    # a third, newly-seen unknown does not disturb the identified two
    assert d.update([_hostile("c1"), _hostile("c2"), _unknown("c3")]) == []
    assert d.update([_hostile("c1"), _hostile("c2"), _hostile("c3")]) == \
        [CUE_CONDITION_ONE]


def test_reidentify_after_dropping_to_neutral_fires_again():
    d = ContactIdentificationDetector()
    d.update([_unknown("c1")])
    assert d.update([_hostile("c1")]) == [CUE_CONDITION_ONE]
    assert d.update([_unknown("c1")]) == []            # lost the ID
    assert d.update([_hostile("c1")]) == [CUE_CONDITION_ONE]   # re-reveal fires


def test_empty_and_missing_id_frames_are_safe():
    d = ContactIdentificationDetector()
    assert d.update([]) == []
    assert d.update(None) == []
    assert d.update([{"type": "x", "allegiance": "hostile"}]) == []  # no id -> skipped


# ===========================================================================
# C. the FTL JUMP edge (-> "jumping")
# ===========================================================================

def test_jump_fires_on_transition_into_jumping():
    j = JumpDetector()
    assert j.update("idle") == []
    assert j.update("spooling") == []
    assert j.update(JUMP_FIRE_VALUE) == [CUE_JUMP]     # the FTL moment


def test_jump_does_not_repeat():
    j = JumpDetector()
    j.update("spooling")
    assert j.update("jumping") == [CUE_JUMP]
    assert j.update("jumping") == []                   # still jumping: no re-fire


def test_jump_refires_after_leaving_jumping():
    j = JumpDetector()
    j.update("jumping")                                # first (from None) fires
    j.update("idle")
    assert j.update("jumping") == [CUE_JUMP]           # a fresh jump fires again


def test_jump_first_value_jumping_fires():
    # game/jump is retained state; a late subscriber whose first frame is already
    # "jumping" SHOULD get the cue (prev None != "jumping").
    assert JumpDetector().update("jumping") == [CUE_JUMP]


def test_jump_ignores_non_jumping_values():
    j = JumpDetector()
    assert j.update("recalling") == []
    assert j.update(None) == []
    assert j.update(123) == []


# ===========================================================================
# D. the cue -> NNNNN.mp3 track mapping
# ===========================================================================

def test_track_filename_is_five_digit():
    assert track_filename(8) == "00008.mp3"
    assert track_filename(9) == "00009.mp3"
    assert track_filename(123) == "00123.mp3"
    assert track_filename(65535) == "65535.mp3"


def test_cue_track_map_and_filenames():
    assert CUE_TRACKS == {CUE_CONDITION_ONE: 8, CUE_JUMP: 9}
    assert cue_filename(CUE_CONDITION_ONE) == "00008.mp3"
    assert cue_filename(CUE_JUMP) == "00009.mp3"


# ===========================================================================
# E. the AudioCover reference model — input from bus, output to module, no publish
# ===========================================================================

def test_cover_emits_correct_frame_per_cue():
    frames = []
    cover = AudioCover(emit=frames.append)
    # baseline contacts frame — no cue yet
    assert cover.on_message(NS_CONTACTS, [_unknown("c1")]) == []
    # the reveal -> the condition_one track (00008.mp3) frame
    out = cover.on_message(NS_CONTACTS, [_hostile("c1")])
    assert out == [dysv5f_play_track(8)]
    # the jump -> the jump track (00009.mp3) frame
    cover.on_message(NS_JUMP, "spooling")
    out = cover.on_message(NS_JUMP, "jumping")
    assert out == [dysv5f_play_track(9)]
    # everything the cover produced went to the UART sink, nothing else
    assert frames == [dysv5f_play_track(8), dysv5f_play_track(9)]


def test_cover_ignores_unrelated_namespaces():
    cover = AudioCover(emit=lambda f: None)
    assert cover.on_message("throttle/left", 200) == []
    assert cover.on_message("game/own_heading", 90) == []


def test_cover_subscriptions_are_the_two_namespaces():
    assert AudioCover.SUBSCRIPTIONS == (NS_CONTACTS, NS_JUMP)


def test_cover_never_publishes():
    # The cover is constructed with a UART sink only; there is no bus-publish
    # affordance on it at all (input from bus, output to module).  Assert the
    # emit sink is the ONLY output path and it carries UART frames (bytes).
    published = []
    frames = []

    def publish(*a, **k):            # a stand-in bus.publish the cover must never call
        published.append(a)

    cover = AudioCover(emit=frames.append)
    assert not hasattr(cover, "publish")
    cover.on_message(NS_CONTACTS, [_unknown("c1")])
    cover.on_message(NS_CONTACTS, [_hostile("c1")])
    cover.on_message(NS_JUMP, "jumping")
    assert published == []                        # never published
    assert all(isinstance(f, bytes) for f in frames)


def test_cover_hook_documents_the_firmware():
    assert "dradis/contacts" in COVER_HOOK and "game/jump" in COVER_HOOK
    assert "bus_publish" in COVER_HOOK            # named only to say it is NEVER called
    assert "00008.mp3" in COVER_HOOK and "00009.mp3" in COVER_HOOK


# ===========================================================================
# F. the S4 mcu-config ACTUATOR emission (the OUTPUT dual falls out of the YAML)
# ===========================================================================

def test_mcu_config_emits_audio_actuator():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)

    # the sensor path is UNTOUCHED (additive)
    assert {s.publish for s in cfg.sensors} == {"throttle/left", "throttle/right"}

    assert len(cfg.actuators) == 1
    a = cfg.actuators[0]
    assert a.kind == "audio"
    assert a.module == "dy-sv5f"                  # hookup part id -> catalog part
    assert a.driver == "dy-sv5f-uart"
    assert a.subscribes == ("dradis/contacts", "game/jump")
    assert a.uart["tx_pin"] == "GPIO5"            # the free UART TX GPIO
    assert a.uart["baud"] == 9600
    assert a.uart["busy_pin"] == "GPIO6"

    # track vs file: TWO INDEPENDENT FACTS (decision D8.2, the 2026-07-29
    # ears-verified bring-up — "play track N" indexes by the card's NUMERIC FILE
    # ORDER, not by the digits in the name, so on this 2-file card 00008.mp3 IS
    # track 1).  The pre-D8 assertions here were `track == 8` / frame `...08 BB`;
    # 7866f90 moved the scene to 1/2 and this suite was never run by the gate, so
    # they sat red for 13 days.  The play ORDINAL (and therefore the UART frame)
    # comes from `track:`; the on-card NAME comes from `file:`.
    by_cue = {c.cue: c for c in a.cues}
    assert set(by_cue) == {"condition_one", "jump"}
    assert by_cue["condition_one"].track == 1                    # 1st file on the card
    assert by_cue["condition_one"].file == "00008.mp3"           # ...which is this one
    assert by_cue["condition_one"].source == "dradis/contacts"
    assert by_cue["condition_one"].uart_frame == "AA 07 02 00 01 B4"
    assert by_cue["jump"].track == 2
    assert by_cue["jump"].file == "00009.mp3"
    assert by_cue["jump"].uart_frame == "AA 07 02 00 02 B5"

    assert cfg.warnings == ()


def test_actuator_serializes_as_pure_subscriber():
    comp = parse_compose_yaml(DUAL_THROTTLE)
    cfg = emit_mcu_config(comp)
    doc = cfg.to_dict()
    assert "actuators" in doc
    act = doc["actuators"][0]
    # the contract is explicit in the emitted config: a pure subscriber
    assert act["publishes"] == []
    assert act["subscribes"] == ["dradis/contacts", "game/jump"]
    assert [c["file"] for c in act["cues"]] == ["00008.mp3", "00009.mp3"]


def _audio_scene(cues):
    """A minimal hookup+audio_cover scene, so the cue lowering can be exercised
    without dragging in the whole dual_throttle example."""
    from composeio.render.engine.parser import parse_widget_tree
    return parse_widget_tree({
        "app": {"id": "audio-only"},
        "design": {"title": "x", "width": 100, "height": 100},
        "namespaces": [],
        "hookup": {
            "board": "perfboard",
            "parts": [{"id": "mcu", "part": "esp32-c3"},
                      {"id": "dysv5f", "part": "dy-sv5f"}],
            # The UART wire is no longer optional scaffolding: hookup_seam.py
            # asserts the device half (`audio_cover:` below) and the netlist
            # describe ONE machine, so a fixture that declares a module wired
            # to nothing is now the drift it is meant to catch.
            "wires": [{"from": "mcu.GPIO5", "to": "dysv5f.RXD/IO1",
                       "subscribes": ["game/jump"]}],
        },
        "audio_cover": {"module": "dysv5f", "uart": {"tx_pin": "GPIO5"},
                        "subscribes": ["game/jump"], "cues": cues},
        "widgets": [],
    })


def test_on_card_file_name_is_declared_not_derived():
    """[D8.2] The DY-SV5F indexes by the card's NUMERIC FILE ORDER, so the file
    NAME is not a function of the play ordinal.  `file:` is carried through from
    config verbatim — emitting a name the card does not contain would be a lie in
    the one field a human reads while loading the card."""
    cfg = emit_mcu_config(_audio_scene([
        {"cue": "jump", "source": "game/jump", "edge": "value == jumping",
         "track": 2, "file": "00009.mp3"},
    ]))
    cue = cfg.actuators[0].cues[0]
    assert cue.track == 2                            # the play ordinal
    assert cue.file == "00009.mp3"                   # NOT track_filename(2)
    assert cue.file != track_filename(cue.track)     # the point of the fix
    assert cue.uart_frame == "AA 07 02 00 02 B5"     # the frame follows the ORDINAL
    assert cfg.warnings == ()


def test_undeclared_file_falls_back_LOUDLY():
    """An undeclared `file:` still emits the index-order convention (shape is
    stable) but is NEVER silent — the derivation is unsound post-D8.2 and the
    warning says so.  Silence here is exactly how 00001.mp3 got emitted for a
    card holding 00008.mp3."""
    cfg = emit_mcu_config(_audio_scene([
        {"cue": "jump", "source": "game/jump", "edge": "value == jumping",
         "track": 2},
    ]))
    cue = cfg.actuators[0].cues[0]
    assert cue.file == "00002.mp3"                   # the legacy convention
    assert len(cfg.warnings) == 1
    w = cfg.warnings[0]
    assert "jump" in w and "file:" in w and "D8.2" in w


def test_scene_without_audio_cover_has_no_actuators():
    # additive: a scene with a hookup but no audio_cover block emits no actuators
    # and the serialized shape omits the `actuators:` key entirely.
    from composeio.render.engine.parser import parse_widget_tree
    comp = parse_widget_tree({
        "app": {"id": "hookup-only"},
        "design": {"title": "x", "width": 100, "height": 100},
        "namespaces": [{"name": "throttle/left", "state_engine": "continuous_0_255"}],
        "hookup": {
            "board": "perfboard",
            "parts": [{"id": "mcu", "part": "esp32-c3"},
                      {"id": "potL", "part": "pot-10k"}],
            "wires": [{"from": "potL.wiper", "to": "mcu.GPIO0",
                       "publishes": "throttle/left"}],
        },
        "widgets": [],
    })
    cfg = emit_mcu_config(comp)
    assert cfg.actuators == ()
    assert "actuators" not in cfg.to_dict()


# ===========================================================================
# G. drift guards — the EXACT bsg wire contracts
# ===========================================================================

def test_namespaces_match_bsg():
    # bsg/world/publish.py: NS_CONTACTS = "dradis/contacts"; NS_JUMP = "game/jump"
    assert NS_CONTACTS == "dradis/contacts"
    assert NS_JUMP == "game/jump"
    # bsg/world/publish.py NS_JUMP publishes idle|spooling|recalling|jumping
    assert JUMP_FIRE_VALUE == "jumping"
    # bsg/world/sensor.py: the resolved contact becomes allegiance "hostile"
    assert IDENTIFIED_ALLEGIANCE == "hostile"


def test_contact_shape_is_the_contract():
    # bsg/world/contract.py::contact -> {id, type, bearing, range, allegiance, label}
    c = _hostile("c1")
    assert set(c) >= {"id", "type", "bearing", "range", "allegiance", "label"}
    # the detector keys on `id` (stable) + `allegiance` (the reveal flag)
    d = ContactIdentificationDetector()
    d.update([_unknown("c1")])
    assert d.update([_hostile("c1")]) == [CUE_CONDITION_ONE]
