/*
 * audio_cover.h — the DY-SV5F audio cover, PURE C++ (host-testable).
 * [SPEC:CXC-EMBED-REACTIVE] [CXC-ER-BOARDS] (dual-throttle S6 actuator).
 *
 * A faithful C++ port of composeio.maker.audio_cover (the executable
 * reference model + COVER_HOOK).  The SAME ESP32-C3 that reads the two
 * throttle pots ALSO drives a DY-SV5F voice module: it SUBSCRIBES existing
 * game state (dradis/contacts + game/jump), derives two edges AT THE LEAF,
 * and writes a "play track" UART frame.  It NEVER publishes — input from the
 * bus, output to the module, full stop.
 *
 * PURE C++ (no Arduino, no ArduinoJson): the controller shell decodes the
 * bus message and feeds DECODED inputs here (the contact list as
 * {id, allegiance} pairs, the jump value as a string).  This keeps JSON out
 * of the reference logic so it host-compiles for env:native_test exactly
 * like the reactive core + the DSL — reuse-by-reference of the Python
 * contract, verified against the same edge semantics + the same UART frames.
 */

#ifndef REACTIVE_AUDIO_COVER_H
#define REACTIVE_AUDIO_COVER_H

#include <cstdint>
#include <string>
#include <vector>

namespace audio_cover {

/* --- DY-SV5F UART protocol (9600-8N1) -------------------------------------
 * Frame = [ 0xAA | 0x07 | 0x02 | idx_hi | idx_lo | SM ], SM = sum of all
 * preceding bytes (incl. 0xAA) & 0xFF.  Mirrors
 * composeio.maker.audio_cover.dysv5f_play_track (verified: track 8 ->
 * AA 07 02 00 08 BB, track 9 -> AA 07 02 00 09 BC). */
constexpr uint8_t DYSV5F_START = 0xAA;
constexpr uint8_t DYSV5F_CMD_SELECT_PLAY = 0x07;
constexpr int DYSV5F_FRAME_LEN = 6;
constexpr int DYSV5F_BAUD = 9600;

/* The two leaf edges + their default on-flash track indices (CUE_TRACKS). */
constexpr int TRACK_CONDITION_ONE = 8;   /* contact unknown -> hostile */
constexpr int TRACK_JUMP = 9;            /* game/jump -> "jumping"      */

/* The allegiance that means a contact is IDENTIFIED (the fog-of-war reveal),
 * and the game/jump value that is the FTL moment. */
constexpr char IDENTIFIED_ALLEGIANCE[] = "hostile";
constexpr char JUMP_FIRE_VALUE[] = "jumping";

/* Build the 6-byte DY-SV5F "select track & play" frame into out[6].
 * index is the 1..65535 on-flash song number.  Returns the checksum byte
 * (also stored in out[5]); returns 0 and leaves out untouched when index is
 * out of range. */
uint8_t dysv5f_play_track(int index, uint8_t out[DYSV5F_FRAME_LEN]);

/* One decoded contact — only the fields the identify edge keys on. */
struct Contact {
    std::string id;
    std::string allegiance;
};

/* A contact is IDENTIFIED iff its allegiance is "hostile"
 * (composeio.maker.audio_cover.contact_is_identified). */
bool contact_is_identified(const Contact& c);

/* Per-id edge detector over successive dradis/contacts frames.  update()
 * returns the count of ids that flipped unknown -> identified THIS frame
 * (each == one CUE_CONDITION_ONE).  Baseline-guarded: an edge needs two
 * frames, so a contact already hostile on first sight is recorded WITHOUT a
 * cue (no observed reveal) — same rule the dradis uses to derive heading. */
class ContactIdentificationDetector {
public:
    int update(const std::vector<Contact>& contacts);
    void reset();

private:
    std::vector<std::string> _identified;   /* ids currently hostile */
    std::vector<std::string> _seen;          /* ids ever observed     */
};

/* Edge detector over successive game/jump values.  update() returns true on
 * the transition INTO "jumping" and false otherwise (jumping->jumping does
 * not re-fire; jumping->idle->jumping fires again). */
class JumpDetector {
public:
    bool update(const std::string& value);
    void reset();

private:
    std::string _prev;
    bool _has_prev = false;
};

}  // namespace audio_cover

#endif /* REACTIVE_AUDIO_COVER_H */
