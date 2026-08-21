/*
 * audio_cover.cpp — DY-SV5F audio cover implementation (PURE C++).
 * Port of composeio.maker.audio_cover; see audio_cover.h.
 */

#include "audio_cover.h"

namespace audio_cover {

uint8_t dysv5f_play_track(int index, uint8_t out[DYSV5F_FRAME_LEN]) {
    if (index < 1 || index > 0xFFFF) return 0;
    const uint8_t hi = (uint8_t)((index >> 8) & 0xFF);
    const uint8_t lo = (uint8_t)(index & 0xFF);
    out[0] = DYSV5F_START;
    out[1] = DYSV5F_CMD_SELECT_PLAY;
    out[2] = 0x02;                 /* LEN: two data bytes (the track index) */
    out[3] = hi;
    out[4] = lo;
    /* SM = low 8 bits of the sum of every preceding byte, incl. 0xAA. */
    uint32_t sum = 0;
    for (int i = 0; i < 5; i++) sum += out[i];
    out[5] = (uint8_t)(sum & 0xFF);
    return out[5];
}

bool contact_is_identified(const Contact& c) {
    return c.allegiance == IDENTIFIED_ALLEGIANCE;
}

/* --- small membership helpers (bounded id sets; contacts are few) -------- */
static bool has_id(const std::vector<std::string>& v, const std::string& id) {
    for (const auto& s : v) if (s == id) return true;
    return false;
}
static void add_id(std::vector<std::string>& v, const std::string& id) {
    if (!has_id(v, id)) v.push_back(id);
}
static void remove_id(std::vector<std::string>& v, const std::string& id) {
    for (size_t i = 0; i < v.size(); i++)
        if (v[i] == id) { v.erase(v.begin() + i); return; }
}

int ContactIdentificationDetector::update(const std::vector<Contact>& contacts) {
    int fired = 0;
    for (const auto& c : contacts) {
        if (c.id.empty()) continue;
        const bool ident = contact_is_identified(c);
        const bool was_ident = has_id(_identified, c.id);
        const bool first_sight = !has_id(_seen, c.id);
        if (ident && !was_ident && !first_sight) fired++;
        if (ident) add_id(_identified, c.id);
        else       remove_id(_identified, c.id);
        add_id(_seen, c.id);
    }
    return fired;
}

void ContactIdentificationDetector::reset() {
    _identified.clear();
    _seen.clear();
}

bool JumpDetector::update(const std::string& value) {
    const bool fire = (value == JUMP_FIRE_VALUE) &&
                      (!_has_prev || _prev != JUMP_FIRE_VALUE);
    _prev = value;
    _has_prev = true;
    return fire;
}

void JumpDetector::reset() {
    _prev.clear();
    _has_prev = false;
}

}  // namespace audio_cover
