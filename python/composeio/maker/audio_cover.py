"""
[FLOW:MAKER-PROJECT] S6 — the DY-SV5F audio cover (a PURE bus SUBSCRIBER).

This is the **composeio side** of the Galactica dual-throttle controller's audio
extension (David 2026-07-26).  The SAME ESP32-C3 that reads the two throttle pots
ALSO drives a **DY-SV5F voice module** — an MP3 player it commands over UART to
play a track in reaction to game events **already on ComposeBus**.

control-is-a-peer, mirrored on the OUTPUT side
----------------------------------------------
``throttle_thrust`` is the C3's INPUT peer (it reads ADCs and PUBLISHES two thrust
slots).  This cover is the C3's OUTPUT peer: it **SUBSCRIBES** existing published
game state and drives a peripheral.  Critically it is::

    INPUT-only  from the bus  (subscribes dradis/contacts + game/jump)
    OUTPUT-only to the module  (emits DY-SV5F UART "play track" frames)
    NEVER publishes to the bus

The DY-SV5F is a pure actuator — a bus event makes noise, nothing more.  There is
NO game change: both triggers are DERIVED AT THE LEAF from state the world already
publishes.  Same principle as the dradis deriving a contact's heading from
successive positions: the identity VALUE stays engine-published (fog-of-war ground
truth the leaf can't invent), and the cover derives only the EDGE ("this id just
changed") from successive frames it already holds.

The two triggers (the decomposition)
------------------------------------
1. **condition_one -> 00008.mp3** — subscribe ``dradis/contacts``; track each
   contact by its stable ``id``; fire when a contact flips unknown -> IDENTIFIED.
   The identify edge is ``allegiance`` transitioning ``neutral`` -> ``hostile``
   (equivalently ``type`` going ``*_unknown`` -> a resolved true type).  This is
   the fog-of-war reveal moment (see ``~/maker/bsg/world/sensor.py::sense`` — the
   0.5 LS resolve threshold that flips a contact to hostile lives THERE).
2. **jump -> 00009.mp3** — subscribe ``game/jump``; fire on the transition to the
   value ``"jumping"`` (the actual FTL moment; the namespace publishes
   ``idle|spooling|recalling|jumping`` on change).

Both edges are computed from successive frames the cover holds; neither requires a
new publisher on the game side.

The wire contracts (grounded against the ACTUAL bsg code, 2026-07-26)
--------------------------------------------------------------------
* ``dradis/contacts`` (``bsg/world/publish.py::NS_CONTACTS``) carries a LIST of
  contact dicts built by ``bsg/world/contract.py::contact(...)``::

      {"id", "type", "bearing", "range", "allegiance", "label"}

  ``allegiance`` is one of ``neutral`` | ``hostile`` | ``friendly``; ``type`` is
  ``small_unknown`` | ``large_unknown`` before resolve, a real type after.
* ``game/jump`` (``bsg/world/publish.py::NS_JUMP``) carries a STRING; the FTL
  moment is the value ``"jumping"``.

WHERE THE COVER RUNS (the C3-side firmware — documented, see COVER_HOOK)
-----------------------------------------------------------------------
The cover runs on the ESP32-C3 itself: the reactive-core stack subscribes the two
namespaces off the local bus (bridged from the global bus), runs the edge
detectors below, and writes the "play track" UART frame to the DY-SV5F.  The
reference model (:class:`AudioCover`) is the executable statement of that firmware
behaviour; ``COVER_HOOK`` is the literal drop-in description mirroring it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# The namespaces — the S6 subscribe contract (single source of truth).
# These are the EXACT bsg namespaces (drift guard in the tests).
# ---------------------------------------------------------------------------

#: The fog-of-war contact list (bsg NS_CONTACTS).  Value = list[contact dict].
NS_CONTACTS = "dradis/contacts"

#: The FTL-jump state string (bsg NS_JUMP): idle | spooling | recalling | jumping.
NS_JUMP = "game/jump"

#: The one game/jump value that is the actual FTL moment.
JUMP_FIRE_VALUE = "jumping"

#: The allegiance that means a contact is IDENTIFIED (the reveal).  A contact is
#: unknown/neutral until the sensor resolves it, then becomes hostile.
IDENTIFIED_ALLEGIANCE = "hostile"


# ---------------------------------------------------------------------------
# The cue -> track map — CONFIG, not code (the SDR credo: push specificity out
# of firmware into configuration).  A cue is a semantic event name; a track is a
# DY-SV5F on-flash song INDEX rendered as a 5-digit NNNNN.mp3 file name.
# ---------------------------------------------------------------------------

CUE_CONDITION_ONE = "condition_one"
CUE_JUMP = "jump"

#: cue -> DY-SV5F track index (1..65535).  The audio designer edits THIS, not the
#: firmware.  Kept ordered so a doc/table renders deterministically.
CUE_TRACKS: Dict[str, int] = {
    CUE_CONDITION_ONE: 8,   # -> 00008.mp3
    CUE_JUMP: 9,            # -> 00009.mp3
}


def track_filename(index: int) -> str:
    """A DY-SV5F track index -> its 5-digit ``NNNNN.mp3`` file name.

    The DY-SV series addresses a track by its on-flash INDEX.  The conventional
    way to pin index == the number you mean is to name the files ``00001.mp3``,
    ``00002.mp3`` ... in copy order (the module orders by physical write order,
    like the DFPlayer family), so ``track 8`` is the file ``00008.mp3``.
    """
    idx = int(index)
    if not 1 <= idx <= 0xFFFF:
        raise ValueError(f"DY-SV5F track index out of range (1..65535): {idx}")
    return f"{idx:05d}.mp3"


def cue_filename(cue: str, tracks: Optional[Dict[str, int]] = None) -> str:
    """A cue name -> the ``NNNNN.mp3`` file the DY-SV5F plays for it."""
    tbl = CUE_TRACKS if tracks is None else tracks
    return track_filename(tbl[cue])


# ---------------------------------------------------------------------------
# The DY-SV5F UART "play track" command builder.
#
# Protocol (DY-SV5F / DY-SV5W voice-module family; UART mode, 9600-8N1):
#   Frame = [ START | CMD | LEN | DATA... | SM ]
#     START = 0xAA
#     CMD   = 0x07  "select the specified track and play"
#     LEN   = number of DATA bytes
#     DATA  = the track index as a BIG-ENDIAN uint16 (high byte, low byte),
#             so LEN = 0x02 for a track select
#     SM    = sum-check = (sum of ALL preceding bytes, INCLUDING the 0xAA
#             start byte) & 0xFF   (low 8 bits)
#
# Datasheet cite: DY-SV5W Voice Playback Module datasheet / user guide
#   (Envistia Mall / grobotronics), UART command table.  The formula is verified
#   against the documented single-byte commands, e.g. PLAY = AA 02 00 AC
#   (0xAA + 0x02 + 0x00 = 0xAC), STOP = AA 04 00 AE, NEXT = AA 06 00 B0 — every
#   published example includes the 0xAA byte in the sum.
# ---------------------------------------------------------------------------

DYSV5F_BAUD = 9600
DYSV5F_START = 0xAA
DYSV5F_CMD_SELECT_PLAY = 0x07   # "select the specified track and play"
DYSV5F_CMD_PLAY = 0x02          # bare PLAY (used only to verify the checksum)


def dysv5f_checksum(frame_without_sm: bytes) -> int:
    """The DY-SV5F sum-check (SM): low 8 bits of the sum of every preceding byte,
    INCLUDING the 0xAA start byte."""
    return sum(frame_without_sm) & 0xFF


def dysv5f_play_track(index: int) -> bytes:
    """Build the DY-SV5F "select track ``index`` and play" UART frame.

    ``index`` is the 1..65535 on-flash song number (== the ``NNNNN`` in
    ``NNNNN.mp3``).  Returns the full 6-byte frame the C3 writes to the module's
    RXD line: ``AA 07 02 <hi> <lo> <SM>``.
    """
    idx = int(index)
    if not 1 <= idx <= 0xFFFF:
        raise ValueError(f"DY-SV5F track index out of range (1..65535): {idx}")
    hi = (idx >> 8) & 0xFF
    lo = idx & 0xFF
    body = bytes([DYSV5F_START, DYSV5F_CMD_SELECT_PLAY, 0x02, hi, lo])
    return body + bytes([dysv5f_checksum(body)])


def dysv5f_frame_hex(frame: bytes) -> str:
    """Space-separated uppercase hex, e.g. ``AA 07 02 00 08 BB`` — for docs/logs."""
    return " ".join(f"{b:02X}" for b in frame)


# ---------------------------------------------------------------------------
# Edge detector 1 — the contact IDENTIFICATION edge (unknown -> hostile).
# ---------------------------------------------------------------------------

def contact_is_identified(contact: Any) -> bool:
    """A contact dict is IDENTIFIED iff its ``allegiance`` is hostile — the
    reveal the world's SensorModel performs at the 0.5 LS resolve threshold.
    (Equivalently ``type`` is no longer ``*_unknown``; ``allegiance`` is the
    authoritative flag the world publishes, so we key on it.)"""
    return isinstance(contact, dict) and contact.get("allegiance") == IDENTIFIED_ALLEGIANCE


@dataclass
class ContactIdentificationDetector:
    """Per-id edge detector over successive ``dradis/contacts`` frames.

    Holds the last-seen identified-state of every contact ``id`` it has observed.
    ``update(contacts)`` returns one :data:`CUE_CONDITION_ONE` for EACH id that
    flipped unknown -> identified THIS frame.

    Semantics (the tested contract):
      * fires ONCE per id on its neutral -> hostile transition;
      * does NOT fire on a repeat (hostile -> hostile);
      * does NOT fire for a contact that is friendly/neutral the whole time;
      * FIRST observation establishes a baseline and does NOT fire (an edge needs
        two frames — same rule the dradis uses to derive heading from motion).  A
        contact that is already hostile on first sight is recorded as identified
        with no cue (there was no OBSERVED reveal).
    """

    _identified: Set[Any] = field(default_factory=set)   # ids currently hostile
    _seen: Set[Any] = field(default_factory=set)         # ids ever observed

    def update(self, contacts: Any) -> List[str]:
        fired: List[str] = []
        for c in contacts or []:
            if not isinstance(c, dict):
                continue
            cid = c.get("id")
            if cid is None:
                continue
            ident = contact_is_identified(c)
            was_ident = cid in self._identified
            first_sight = cid not in self._seen
            if ident and not was_ident and not first_sight:
                fired.append(CUE_CONDITION_ONE)
            if ident:
                self._identified.add(cid)
            else:
                self._identified.discard(cid)
            self._seen.add(cid)
        return fired


# ---------------------------------------------------------------------------
# Edge detector 2 — the FTL JUMP edge (-> "jumping").
# ---------------------------------------------------------------------------

@dataclass
class JumpDetector:
    """Edge detector over successive ``game/jump`` values.

    ``update(value)`` returns a single :data:`CUE_JUMP` on the transition INTO
    ``"jumping"`` and nothing otherwise:
      * idle -> spooling -> jumping  fires once (on the ->jumping step);
      * jumping -> jumping           does not fire;
      * jumping -> idle -> jumping   fires again (a fresh transition).
    """

    _prev: Optional[str] = None

    def update(self, value: Any) -> List[str]:
        v = value if isinstance(value, str) else None
        fired: List[str] = []
        if v == JUMP_FIRE_VALUE and self._prev != JUMP_FIRE_VALUE:
            fired.append(CUE_JUMP)
        self._prev = v
        return fired


# ---------------------------------------------------------------------------
# The reference cover model — the EXECUTABLE statement of the C3 firmware hook.
# ---------------------------------------------------------------------------

@dataclass
class AudioCover:
    """Reference DY-SV5F audio cover: exactly what the C3 firmware runs.

    Wire it with an ``emit`` sink that writes bytes to the DY-SV5F UART (the C3's
    ``uart_write``).  Feed it decoded bus state via :meth:`on_message` — the SAME
    ``(namespace, value)`` bus-callback shape ``ThrottleThrustBridge`` uses.  For
    each edge it detects it builds the module's play-track frame and writes it to
    ``emit``; it returns the frames it emitted (for tests/logging).  It NEVER
    publishes to the bus — input from the bus, output to the module, full stop.
    """

    emit: Callable[[bytes], None]
    tracks: Dict[str, int] = field(default_factory=lambda: dict(CUE_TRACKS))
    contacts: ContactIdentificationDetector = field(
        default_factory=ContactIdentificationDetector)
    jump: JumpDetector = field(default_factory=JumpDetector)

    #: The namespaces this cover subscribes — the firmware's subscribe list.
    SUBSCRIPTIONS = (NS_CONTACTS, NS_JUMP)

    def cues_for(self, namespace: str, value: Any) -> List[str]:
        """Pure edge-detection: decoded (namespace, value) -> the cue names it
        fires this frame (no side effects; used by :meth:`on_message`)."""
        if namespace == NS_CONTACTS:
            return self.contacts.update(value)
        if namespace == NS_JUMP:
            return self.jump.update(value)
        return []

    def on_message(self, namespace: str, value: Any) -> List[bytes]:
        """Feed one decoded bus state message.  Emits a DY-SV5F play-track frame
        to ``emit`` for every cue detected; returns the frames emitted."""
        frames: List[bytes] = []
        for cue in self.cues_for(namespace, value):
            idx = self.tracks.get(cue)
            if idx is None:
                continue
            frame = dysv5f_play_track(idx)
            frames.append(frame)
            self.emit(frame)
        return frames


# ---------------------------------------------------------------------------
# COVER_HOOK — the C3-side firmware behaviour (documented, like WORLD_APP_HOOK).
# The cover runs on the SAME ESP32-C3 as the throttle read loop.  NOT applied
# here; it describes the reactive-core stack's audio-actuator wiring.
# ---------------------------------------------------------------------------
COVER_HOOK = r'''
# === C3 firmware — the DY-SV5F audio cover (reactive-core stack) ===========
# The cover is an OUTPUT peer on the SAME C3 that reads the throttle pots.  It
# adds NO publish; it only SUBSCRIBES two existing game namespaces and drives a
# UART.  Mirrors composeio.maker.audio_cover.AudioCover field-for-field.
#
# 1. UART: bring up a hardware UART on the DY-SV5F TX pin at 9600-8N1.
#      uart_init(tx=GPIO5, rx=none, baud=9600)     # GPIO5 -> DY-SV5F RXD/IO1
#      (optional) gpio_input(GPIO6)                # <- DY-SV5F BUSY (HIGH=playing)
#
# 2. Subscribe the two bridged namespaces off the local bus:
#      bus_subscribe("dradis/contacts")    # list[contact dict], fog-of-war
#      bus_subscribe("game/jump")          # "idle|spooling|recalling|jumping"
#
# 3. Hold the two edge detectors as standing state (successive-frame edges):
#      contact_identified_ids : set        # ids currently allegiance==hostile
#      contact_seen_ids       : set        # ids ever observed (baseline guard)
#      prev_jump              : str
#
# 4. On a dradis/contacts frame (list of contacts):
#      for c in contacts:
#          ident = (c["allegiance"] == "hostile")
#          if ident and c["id"] not in contact_identified_ids \
#                   and c["id"] in contact_seen_ids:
#              dysv5f_play(8)               # condition_one -> 00008.mp3
#          contact_identified_ids.{add|discard}(c["id"]);  contact_seen_ids.add(c["id"])
#
# 5. On a game/jump frame (string value):
#      if value == "jumping" and prev_jump != "jumping":
#          dysv5f_play(9)                   # jump -> 00009.mp3
#      prev_jump = value
#
# 6. dysv5f_play(index): write the 6-byte frame to the UART:
#      AA 07 02 <index_hi> <index_lo> <SM>       SM = sum(all bytes) & 0xFF
#      (composeio.maker.audio_cover.dysv5f_play_track(index) builds it exactly.)
#
# The cover NEVER calls bus_publish(): input from the bus, output to the module.
# The cue->track map (condition_one->8, jump->9) is CONFIG pulled from the device
# .compose.yaml `audio_cover:` block, not hard-coded (the SDR credo).
'''


__all__ = [
    # namespaces + contract constants
    "NS_CONTACTS", "NS_JUMP", "JUMP_FIRE_VALUE", "IDENTIFIED_ALLEGIANCE",
    # cue -> track config
    "CUE_CONDITION_ONE", "CUE_JUMP", "CUE_TRACKS",
    "track_filename", "cue_filename",
    # DY-SV5F UART protocol
    "DYSV5F_BAUD", "DYSV5F_START", "DYSV5F_CMD_SELECT_PLAY", "DYSV5F_CMD_PLAY",
    "dysv5f_checksum", "dysv5f_play_track", "dysv5f_frame_hex",
    # edge detectors + reference cover
    "contact_is_identified", "ContactIdentificationDetector", "JumpDetector",
    "AudioCover", "COVER_HOOK",
]
