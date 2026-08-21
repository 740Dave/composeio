/*
 * r503_protocol.cpp — implementation of the pure R503 wire protocol.
 * [SPEC:CXC-R503] [CXC-R503-FRAMING] [CXC-R503-CHECKSUM]
 *
 * PURE.  Nothing in this translation unit performs I/O, sleeps, or touches
 * a peripheral, so all of it links and runs in env:native_test.
 */
#include "r503_protocol.h"

#include <cstdio>

namespace r503 {

/* --- small endian helpers ------------------------------------------------ */
static inline void put_u16(Bytes& b, uint16_t v) {
    b.push_back(static_cast<char>((v >> 8) & 0xFF));
    b.push_back(static_cast<char>(v & 0xFF));
}
static inline void put_u32(Bytes& b, uint32_t v) {
    b.push_back(static_cast<char>((v >> 24) & 0xFF));
    b.push_back(static_cast<char>((v >> 16) & 0xFF));
    b.push_back(static_cast<char>((v >> 8) & 0xFF));
    b.push_back(static_cast<char>(v & 0xFF));
}
static inline uint8_t at(const Bytes& b, size_t i) {
    return static_cast<uint8_t>(b[i]);
}
static inline uint16_t u16_at(const Bytes& b, size_t i) {
    return static_cast<uint16_t>((at(b, i) << 8) | at(b, i + 1));
}
static inline uint32_t u32_at(const Bytes& b, size_t i) {
    return (static_cast<uint32_t>(at(b, i)) << 24) |
           (static_cast<uint32_t>(at(b, i + 1)) << 16) |
           (static_cast<uint32_t>(at(b, i + 2)) << 8) |
           static_cast<uint32_t>(at(b, i + 3));
}

/* --- checksum ------------------------------------------------------------ */
uint16_t checksum(uint8_t pid, uint16_t length_field, const Bytes& content) {
    uint32_t sum = pid;
    sum += (length_field >> 8) & 0xFF;
    sum += length_field & 0xFF;
    for (size_t i = 0; i < content.size(); i++) sum += at(content, i);
    return static_cast<uint16_t>(sum & 0xFFFF);
}

Bytes build_packet(uint32_t address, uint8_t pid, const Bytes& content) {
    const uint16_t len = static_cast<uint16_t>(content.size() + 2);
    Bytes out;
    out.reserve(9 + content.size() + 2);
    out.push_back(static_cast<char>(kHeaderHi));
    out.push_back(static_cast<char>(kHeaderLo));
    put_u32(out, address);
    out.push_back(static_cast<char>(pid));
    put_u16(out, len);
    out += content;
    put_u16(out, checksum(pid, len, content));
    return out;
}

/* --- the scanner ---------------------------------------------------------
 * Resync discipline: find 0xEF followed by 0x01.  Everything before it is
 * garbage and is REPORTED (skipped), never silently dropped — that is how
 * the power-on 0x55 becomes visible instead of becoming a mystery.        */
ScanOutcome scan_packet(const Bytes& buf) {
    ScanOutcome r;

    /* 1. locate the header */
    size_t start = 0;
    while (true) {
        if (start >= buf.size()) {
            /* nothing but garbage: drop it all, ask for more */
            r.kind = ScanOutcome::Kind::NEED_MORE;
            r.skipped = buf.size();
            r.consumed = buf.size();
            return r;
        }
        if (at(buf, start) != kHeaderHi) { start++; continue; }
        if (start + 1 >= buf.size()) {
            /* a trailing lone 0xEF might be the start of a real header —
             * keep it, drop only what precedes it. */
            r.kind = ScanOutcome::Kind::NEED_MORE;
            r.skipped = start;
            r.consumed = start;
            return r;
        }
        if (at(buf, start + 1) == kHeaderLo) break;
        start++;   /* 0xEF not followed by 0x01: not a header */
    }
    r.skipped = start;

    /* 2. need at least header(2)+addr(4)+pid(1)+len(2) = 9 bytes */
    if (buf.size() - start < 9) {
        r.kind = ScanOutcome::Kind::NEED_MORE;
        r.consumed = start;
        return r;
    }

    const uint32_t address = u32_at(buf, start + 2);
    const uint8_t  pid     = at(buf, start + 6);
    const uint16_t len     = u16_at(buf, start + 7);

    /* 3. a length field must at minimum cover its own checksum, and must
     *    not exceed the largest legal packet.  Out of range == garbage:
     *    skip the two header bytes and resync rather than block forever. */
    if (len < 2 || len > kMaxLengthField) {
        r.kind = ScanOutcome::Kind::BAD_LENGTH;
        r.consumed = start + 2;
        return r;
    }

    const size_t total = 9 + static_cast<size_t>(len);   /* through checksum */
    if (buf.size() - start < total) {
        r.kind = ScanOutcome::Kind::NEED_MORE;
        r.consumed = start;
        return r;
    }

    const size_t content_n = static_cast<size_t>(len) - 2;
    Bytes content = buf.substr(start + 9, content_n);
    const uint16_t got = u16_at(buf, start + 9 + content_n);
    const uint16_t want = checksum(pid, len, content);

    r.consumed = start + total;
    if (got != want) {
        r.kind = ScanOutcome::Kind::BAD_CHECKSUM;
        return r;
    }

    r.kind = ScanOutcome::Kind::PACKET;
    r.packet.address = address;
    r.packet.pid     = pid;
    r.packet.content = content;
    return r;
}

bool parse_ack(const Packet& p, Ack& out) {
    if (p.pid != PID_ACK) return false;
    if (p.content.empty()) return false;
    out.confirmation = at(p.content, 0);
    out.params = p.content.substr(1);
    return true;
}

/* --- command builders ---------------------------------------------------- */
static Bytes cmd0(uint32_t addr, uint8_t code) {
    Bytes c;
    c.push_back(static_cast<char>(code));
    return build_packet(addr, PID_COMMAND, c);
}
static Bytes cmd1(uint32_t addr, uint8_t code, uint8_t a) {
    Bytes c;
    c.push_back(static_cast<char>(code));
    c.push_back(static_cast<char>(a));
    return build_packet(addr, PID_COMMAND, c);
}

Bytes cmd_verify_password(uint32_t address, uint32_t password) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_VFY_PWD));
    put_u32(c, password);
    return build_packet(address, PID_COMMAND, c);
}
Bytes cmd_read_sys_para(uint32_t a)   { return cmd0(a, CMD_READ_SYS_PARA); }
Bytes cmd_read_prod_info(uint32_t a)  { return cmd0(a, CMD_READ_PROD_INFO); }
Bytes cmd_template_num(uint32_t a)    { return cmd0(a, CMD_TEMPLATE_NUM); }
Bytes cmd_gen_img(uint32_t a)         { return cmd0(a, CMD_GEN_IMG); }
Bytes cmd_get_image_ex(uint32_t a)    { return cmd0(a, CMD_GET_IMAGE_EX); }
Bytes cmd_reg_model(uint32_t a)       { return cmd0(a, CMD_REG_MODEL); }
Bytes cmd_empty(uint32_t a)           { return cmd0(a, CMD_EMPTY); }

Bytes cmd_read_index_table(uint32_t a, uint8_t page) {
    return cmd1(a, CMD_READ_INDEX_TABLE, page);
}
Bytes cmd_img2tz(uint32_t a, uint8_t buffer_id) {
    return cmd1(a, CMD_IMG2TZ, buffer_id);
}
Bytes cmd_up_char(uint32_t a, uint8_t buffer_id) {
    return cmd1(a, CMD_UP_CHAR, buffer_id);
}
Bytes cmd_down_char(uint32_t a, uint8_t buffer_id) {
    return cmd1(a, CMD_DOWN_CHAR, buffer_id);
}

Bytes cmd_store(uint32_t a, uint8_t buffer_id, uint16_t page_id) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_STORE));
    c.push_back(static_cast<char>(buffer_id));
    put_u16(c, page_id);
    return build_packet(a, PID_COMMAND, c);
}
Bytes cmd_load_char(uint32_t a, uint8_t buffer_id, uint16_t page_id) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_LOAD_CHAR));
    c.push_back(static_cast<char>(buffer_id));
    put_u16(c, page_id);
    return build_packet(a, PID_COMMAND, c);
}
Bytes cmd_search(uint32_t a, uint8_t buffer_id, uint16_t start_id, uint16_t num) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_SEARCH));
    c.push_back(static_cast<char>(buffer_id));
    put_u16(c, start_id);
    put_u16(c, num);
    return build_packet(a, PID_COMMAND, c);
}
Bytes cmd_delete_char(uint32_t a, uint16_t start_id, uint16_t num) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_DELETE_CHAR));
    put_u16(c, start_id);
    put_u16(c, num);
    return build_packet(a, PID_COMMAND, c);
}
Bytes cmd_write_notepad(uint32_t a, uint8_t page, const Bytes& data) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_WRITE_NOTEPAD));
    c.push_back(static_cast<char>(page));
    Bytes payload = data.substr(0, kNotepadPageSize);
    payload.resize(kNotepadPageSize, '\0');   /* fixed 32 -> length 0x0024 */
    c += payload;
    return build_packet(a, PID_COMMAND, c);
}
Bytes cmd_read_notepad(uint32_t a, uint8_t page) {
    return cmd1(a, CMD_READ_NOTEPAD, page);
}
Bytes cmd_aura_led(uint32_t a, uint8_t ctrl, uint8_t speed, uint8_t colour,
                   uint8_t count) {
    Bytes c;
    c.push_back(static_cast<char>(CMD_AURA_LED));
    c.push_back(static_cast<char>(ctrl));
    c.push_back(static_cast<char>(speed));
    c.push_back(static_cast<char>(colour));
    c.push_back(static_cast<char>(count));
    return build_packet(a, PID_COMMAND, c);
}

/* --- the multi-packet transfer ------------------------------------------
 * Resolution A1 (short last packet) and A2 (no trailing empty packet) are
 * implemented here; see the header for why each was decided that way. */
Bytes build_empty_end_packet(uint32_t address) {
    return build_packet(address, PID_END, Bytes());
}

std::vector<Bytes> build_data_packets(uint32_t address, const Bytes& data,
                                      size_t content_len,
                                      EndPacketStyle style) {
    std::vector<Bytes> out;
    if (data.empty() || content_len == 0) return out;
    if (content_len > kMaxContentBytes) content_len = kMaxContentBytes;

    const bool separate_end = (style == EndPacketStyle::SEPARATE_EMPTY_END);
    size_t off = 0;
    while (off < data.size()) {
        const size_t n = (data.size() - off < content_len)
                             ? (data.size() - off)
                             : content_len;
        const bool last = (off + n >= data.size());
        /* Under SEPARATE_EMPTY_END every payload packet is 0x02 and the
         * terminator is appended below; under the manual's framing the last
         * payload packet IS the terminator. */
        out.push_back(build_packet(
            address, (last && !separate_end) ? PID_END : PID_DATA,
            data.substr(off, n)));
        off += n;
    }
    if (separate_end) out.push_back(build_empty_end_packet(address));
    return out;
}

DataReassembler::Feed DataReassembler::feed(const Packet& p) {
    if (complete_) return Feed::ALREADY_COMPLETE;
    if (p.pid != PID_DATA && p.pid != PID_END) return Feed::WRONG_PID;
    if (max_bytes_ && data_.size() + p.content.size() > max_bytes_)
        return Feed::TOO_LARGE;

    /* Resolution A2, receive half: an EMPTY PID_END packet is tolerated and
     * simply terminates the stream contributing zero bytes. */
    data_ += p.content;
    packets_++;
    if (p.pid == PID_END) {
        complete_ = true;
        return Feed::COMPLETE;
    }
    return Feed::ACCEPTED;
}

/* --- ReadSysPara / ReadProdInfo decode ----------------------------------- */
bool parse_sys_para(const Bytes& ack_params, SysPara& out) {
    if (ack_params.size() < 16) return false;
    out.status_register     = u16_at(ack_params, 0);
    out.system_identifier   = u16_at(ack_params, 2);
    out.finger_library_size = u16_at(ack_params, 4);
    out.security_level      = u16_at(ack_params, 6);
    out.device_address      = u32_at(ack_params, 8);
    out.packet_size_code    = u16_at(ack_params, 12);
    out.baud_n              = u16_at(ack_params, 14);
    return true;
}

/* ASCII field, 0x00-padded (extract:836-838: "For a string, the
 * insufficient part is 0x00"). */
static std::string ascii_field(const Bytes& b, size_t off, size_t n) {
    std::string s;
    for (size_t i = 0; i < n && off + i < b.size(); i++) {
        char c = b[off + i];
        if (c == '\0') break;
        s.push_back(c);
    }
    return s;
}

bool parse_prod_info(const Bytes& ack_params, ProdInfo& out) {
    if (ack_params.size() < 46) return false;   /* 46 defined + 4 reserved */
    out.module_model   = ascii_field(ack_params, 0, 16);
    out.batch_number   = ascii_field(ack_params, 16, 4);
    out.serial_number  = ascii_field(ack_params, 20, 8);
    out.hw_major       = at(ack_params, 28);
    out.hw_minor       = at(ack_params, 29);
    out.sensor_model   = ascii_field(ack_params, 30, 8);
    out.sensor_width   = u16_at(ack_params, 38);
    out.sensor_height  = u16_at(ack_params, 40);
    out.template_size  = u16_at(ack_params, 42);
    out.template_total = u16_at(ack_params, 44);
    return true;
}

bool parse_search_ack(const Ack& a, uint16_t& page_id, uint16_t& score) {
    if (a.params.size() < 4) return false;
    page_id = u16_at(a.params, 0);
    score   = u16_at(a.params, 2);
    return true;
}

bool parse_template_num_ack(const Ack& a, uint16_t& count) {
    if (a.params.size() < 2) return false;
    count = u16_at(a.params, 0);
    return true;
}

bool parse_index_table_ack(const Ack& a, uint16_t page, std::vector<int>& slots) {
    if (a.params.size() < 32) return false;
    slots.clear();
    for (size_t i = 0; i < 32; i++) {
        const uint8_t byte = at(a.params, i);
        for (int bit = 0; bit < 8; bit++) {
            if (byte & (1u << bit))
                slots.push_back(static_cast<int>(page) * 256 +
                                static_cast<int>(i) * 8 + bit);
        }
    }
    return true;
}

/* --- hex ----------------------------------------------------------------- */
std::string hex(const Bytes& b, size_t max_bytes) {
    const size_t n = (max_bytes && max_bytes < b.size()) ? max_bytes : b.size();
    std::string s;
    s.reserve(n * 3);
    char tmp[4];
    for (size_t i = 0; i < n; i++) {
        std::snprintf(tmp, sizeof tmp, "%02X", at(b, i));
        if (i) s.push_back(' ');
        s += tmp;
    }
    if (n < b.size()) s += " ...";
    return s;
}

}  /* namespace r503 */
