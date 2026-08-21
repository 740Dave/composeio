/*
 * r503_protocol_tests.cpp — host tests for the R503 wire protocol and the
 * RealR503HAL driver.  [SPEC:CXC-R503] [SPEC:CXC-FPHAL]
 *
 * THE HEADLINE TEST IS THE MULTI-PACKET ROUND TRIP.  Section 5 builds a
 * DownChar data stream from a known byte array, re-frames it through the
 * real scanner, reassembles it as if it were an UpChar stream, and asserts
 * BYTE-EXACT equality — across four packet lengths, a short last packet, an
 * exact multiple, a single-packet payload, and a stream with the module's
 * power-on 0x55 injected between packets.
 *
 * ⛔ AND IT CARRIES ITS OWN NEGATIVE CONTROLS.  An instrument that can only
 * report success is not an instrument ([CXC-VISION-EPISTEMICS]).  Section 8
 * therefore drives run_sync_test() against fake modules that REFUSE
 * DownChar, refuse UpChar, corrupt one byte, truncate the stream, and drop
 * the end packet — and asserts the instrument reports FAIL in each case,
 * naming the right reason.  Without those, "synctest passed" would be
 * unfalsifiable.
 *
 * ⛔ MACHINE-GREEN != GLASS-GREEN.  Every check here runs against a fake
 * module built from the datasheet.  It proves OUR code is right; it cannot
 * prove the physical part behaves as documented.
 */
#include "../real_r503_hal.h"
#include "../r503_protocol.h"

#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <string>
#include <vector>

using r503::Ack;
using r503::Bytes;
using r503::Packet;

static int t_checks = 0;
static int t_fail = 0;

#define TCHECK(cond, msg) do {                                            \
    t_checks++;                                                           \
    if (!(cond)) { t_fail++; printf("FAIL [r503] %s (line %d)\n", msg, __LINE__); } \
} while (0)

static Bytes B(std::initializer_list<int> v) {
    Bytes b;
    for (int x : v) b.push_back(static_cast<char>(x & 0xFF));
    return b;
}

/* ======================================================================== */
/*  A fake R503 module, built strictly from the V1.4.1 manual               */
/* ======================================================================== */
class FakeR503 : public R503Port {   /* not final: 10h subclasses it */
public:
    /* fault injection — the negative controls */
    bool     refuse_downchar   = false;   /* answer 0x0E                    */
    bool     refuse_upchar     = false;   /* answer 0x0D                    */
    bool     no_prod_info      = false;   /* answer 0xFC to 0x3C            */
    bool     drop_end_packet   = false;   /* never send PID 0x08            */
    bool     truncate_stream   = false;   /* drop the last data packet      */
    long     corrupt_at        = -1;      /* flip one byte of the readback  */
    bool     emit_stray_0x55   = false;   /* the power-on byte, mid-stream  */
    bool     separate_buffers  = false;   /* DownChar and UpChar disagree   */

    /* ⛔ THE 2026-08-20 BENCH SIGNATURE, MADE REPRODUCIBLE.
     * strict_separate_end: the receive phase ends ONLY on an EMPTY PID 0x08.
     * A 0x08 that CARRIES payload is treated as ordinary data — so a stream
     * framed the manual's way (last data packet IS the end packet) never
     * terminates, the module stays in receive mode, and the UpChar command
     * that follows is swallowed as data.  Observable result: DownChar ACKs
     * 0x00, all bytes go out, UpChar draws TOTAL SILENCE.  Exactly what the
     * bench saw, from a module that supports UpChar perfectly well. */
    bool     strict_separate_end = false;
    /* wedge_forever: once receiving, nothing ever ends it — the harsher
     * cousin, where even an empty end packet does not rescue. */
    bool     wedge_forever     = false;

    uint32_t address       = r503::kDefaultAddress;
    uint16_t library_size  = 200;
    uint16_t packet_code   = 2;           /* 128 bytes, the factory default */
    uint16_t template_size = 1536;

    size_t   data_packets_received = 0;

    FakeR503() { notepad_.assign(r503::kNotepadPages * r503::kNotepadPageSize, '\0'); }

    /* what a UpChar will hand back */
    void preload_buffer(const Bytes& b) { char_buffer_ = b; }
    const Bytes& char_buffer() const { return char_buffer_; }
    /* The bench's free fixture: this physical module arrived with a REAL
     * leftover template in slot 1 from a past life.  Seed the same. */
    void preload_slot(int slot, const Bytes& b) { flash_[slot] = b; }
    bool receiving() const { return receiving_; }

    /* --- R503Port ---------------------------------------------------- */
    void write(const Bytes& b) override { rx_ += b; pump(); }
    int read_byte(uint32_t) override {
        if (tx_.empty()) return -1;
        const int c = static_cast<uint8_t>(tx_.front());
        tx_.pop_front();
        return c;
    }
    void drain_input() override { tx_.clear(); }
    uint32_t now_ms() override { return clock_++; }

private:
    void emit(const Bytes& b) { for (char c : b) tx_.push_back(c); }
    void emit_ack(uint8_t conf, const Bytes& params = Bytes()) {
        Bytes c;
        c.push_back(static_cast<char>(conf));
        c += params;
        emit(r503::build_packet(address, r503::PID_ACK, c));
    }

    void pump() {
        for (;;) {
            r503::ScanOutcome s = r503::scan_packet(rx_);
            if (s.kind == r503::ScanOutcome::Kind::NEED_MORE) {
                if (s.consumed) rx_.erase(0, s.consumed);
                return;
            }
            rx_.erase(0, s.consumed);
            if (s.kind != r503::ScanOutcome::Kind::PACKET) continue;
            handle(s.packet);
        }
    }

    void handle(const Packet& p) {
        /* --- THE DOWNCHAR RECEIVE PHASE ---------------------------------
         * While receiving, a real module is consuming a byte stream, not
         * dispatching commands.  EVERYTHING is absorbed until the end
         * marker — which is precisely why an UpChar sent into an
         * unterminated receive phase produces silence rather than a
         * refusal. */
        if (receiving_) {
            if (p.pid == r503::PID_DATA || p.pid == r503::PID_END)
                data_packets_received++;

            bool terminates = false;
            if (p.pid == r503::PID_END)
                terminates = strict_separate_end ? p.content.empty() : true;
            if (wedge_forever) terminates = false;

            if (p.pid == r503::PID_DATA || p.pid == r503::PID_END)
                incoming_ += p.content;
            /* a COMMAND arriving mid-receive is eaten as data and answered
             * with nothing at all — the bench signature */

            if (terminates) {
                receiving_ = false;
                if (separate_buffers) {
                    /* Ambiguity A5 made real: DownChar wrote a buffer UpChar
                     * does not read.  The manual does not rule this out, so
                     * the test suite must cover it. */
                    other_buffer_ = incoming_;
                } else {
                    char_buffer_ = incoming_;
                }
                incoming_.clear();
            }
            return;
        }
        /* a stray data packet outside a receive phase is ignored */
        if (p.pid == r503::PID_DATA || p.pid == r503::PID_END) {
            data_packets_received++;
            return;
        }
        if (p.pid != r503::PID_COMMAND || p.content.empty()) return;
        const uint8_t code = static_cast<uint8_t>(p.content[0]);

        switch (code) {
            case r503::CMD_VFY_PWD:
                emit_ack(r503::CONF_OK);
                break;

            case r503::CMD_READ_SYS_PARA: {
                Bytes q(16, '\0');
                auto w16 = [&](size_t off, uint16_t v) {
                    q[off]     = static_cast<char>((v >> 8) & 0xFF);
                    q[off + 1] = static_cast<char>(v & 0xFF);
                };
                w16(0, 0x0000);          /* status register            */
                w16(2, 0x0000);          /* system identifier          */
                w16(4, library_size);    /* finger library size        */
                w16(6, 3);               /* security level             */
                w16(8, static_cast<uint16_t>(address >> 16));
                w16(10, static_cast<uint16_t>(address & 0xFFFF));
                w16(12, packet_code);    /* packet size CODE           */
                w16(14, 6);              /* baud N -> 57600            */
                emit_ack(r503::CONF_OK, q);
                break;
            }

            case r503::CMD_READ_PROD_INFO: {
                if (no_prod_info) { emit_ack(r503::CONF_UNSUPPORTED_CMD); break; }
                Bytes q(50, '\0');
                std::memcpy(&q[0], "R503", 4);
                std::memcpy(&q[16], "2301", 4);
                std::memcpy(&q[20], "SN000001", 8);
                q[28] = 1; q[29] = 4;
                std::memcpy(&q[30], "FPS0", 4);
                q[38] = 0; q[39] = static_cast<char>(192);
                q[40] = 0; q[41] = static_cast<char>(192);
                q[42] = static_cast<char>((template_size >> 8) & 0xFF);
                q[43] = static_cast<char>(template_size & 0xFF);
                q[44] = static_cast<char>((library_size >> 8) & 0xFF);
                q[45] = static_cast<char>(library_size & 0xFF);
                emit_ack(r503::CONF_OK, q);
                break;
            }

            case r503::CMD_DOWN_CHAR:
                if (refuse_downchar) { emit_ack(r503::CONF_CANNOT_RECEIVE); break; }
                emit_ack(r503::CONF_OK);
                receiving_ = true;
                incoming_.clear();
                break;

            case r503::CMD_UP_CHAR: {
                if (refuse_upchar) { emit_ack(r503::CONF_UPLOAD_FAIL); break; }
                emit_ack(r503::CONF_OK);
                Bytes payload = char_buffer_;
                if (payload.empty()) break;
                if (corrupt_at >= 0 &&
                    static_cast<size_t>(corrupt_at) < payload.size())
                    payload[corrupt_at] = static_cast<char>(
                        payload[corrupt_at] ^ 0xFF);
                std::vector<Bytes> pkts = r503::build_data_packets(
                    address, payload, content_len());
                if (truncate_stream && !pkts.empty()) pkts.pop_back();
                for (size_t i = 0; i < pkts.size(); i++) {
                    if (drop_end_packet && i + 1 == pkts.size()) break;
                    if (emit_stray_0x55 && i == 1)
                        tx_.push_back(static_cast<char>(r503::kStrayPowerOnByte));
                    emit(pkts[i]);
                }
                break;
            }

            case r503::CMD_READ_INDEX_TABLE: {
                Bytes q(32, '\0');
                for (std::map<int, Bytes>::const_iterator it = flash_.begin();
                     it != flash_.end(); ++it) {
                    const int slot = it->first;
                    if (slot < 0 || slot >= 256) continue;
                    q[slot / 8] = static_cast<char>(
                        static_cast<uint8_t>(q[slot / 8]) | (1u << (slot % 8)));
                }
                emit_ack(r503::CONF_OK, q);
                break;
            }

            case r503::CMD_WRITE_NOTEPAD: {
                if (p.content.size() < 2 + r503::kNotepadPageSize) {
                    emit_ack(r503::CONF_PACKET_RECV_ERROR);
                    break;
                }
                const uint8_t page = static_cast<uint8_t>(p.content[1]);
                if (page >= r503::kNotepadPages) {
                    emit_ack(0x1C);   /* wrong notepad page number */
                    break;
                }
                notepad_.replace(page * r503::kNotepadPageSize,
                                 r503::kNotepadPageSize,
                                 p.content.substr(2, r503::kNotepadPageSize));
                emit_ack(r503::CONF_OK);
                break;
            }
            case r503::CMD_READ_NOTEPAD: {
                if (p.content.size() < 2) { emit_ack(r503::CONF_PACKET_RECV_ERROR); break; }
                const uint8_t page = static_cast<uint8_t>(p.content[1]);
                if (page >= r503::kNotepadPages) { emit_ack(0x1C); break; }
                emit_ack(r503::CONF_OK,
                         notepad_.substr(page * r503::kNotepadPageSize,
                                         r503::kNotepadPageSize));
                break;
            }

            case r503::CMD_GEN_IMG:
                emit_ack(finger_present ? r503::CONF_OK : r503::CONF_NO_FINGER);
                break;
            case r503::CMD_IMG2TZ:
                emit_ack(r503::CONF_OK);
                break;
            case r503::CMD_SEARCH: {
                if (!match_slot_valid) { emit_ack(r503::CONF_NOT_FOUND); break; }
                Bytes q;
                q.push_back(static_cast<char>((match_slot >> 8) & 0xFF));
                q.push_back(static_cast<char>(match_slot & 0xFF));
                q.push_back(static_cast<char>((match_score >> 8) & 0xFF));
                q.push_back(static_cast<char>(match_score & 0xFF));
                emit_ack(r503::CONF_OK, q);
                break;
            }
            case r503::CMD_STORE: {
                if (p.content.size() < 4) { emit_ack(r503::CONF_PACKET_RECV_ERROR); break; }
                const int page = (static_cast<uint8_t>(p.content[2]) << 8) |
                                 static_cast<uint8_t>(p.content[3]);
                flash_[page] = char_buffer_;
                emit_ack(r503::CONF_OK);
                break;
            }
            /* LoadChar is REAL here, not a rubber stamp: it moves a stored
             * template into the char buffer, or refuses 0x0C when the slot
             * holds nothing.  `r503 extract` is only a discriminator if this
             * step can actually fail. */
            case r503::CMD_LOAD_CHAR: {
                if (p.content.size() < 4) { emit_ack(r503::CONF_PACKET_RECV_ERROR); break; }
                const int page = (static_cast<uint8_t>(p.content[2]) << 8) |
                                 static_cast<uint8_t>(p.content[3]);
                std::map<int, Bytes>::const_iterator it = flash_.find(page);
                if (it == flash_.end()) { emit_ack(r503::CONF_INVALID_TEMPLATE); break; }
                char_buffer_ = it->second;
                emit_ack(r503::CONF_OK);
                break;
            }
            case r503::CMD_REG_MODEL:
            case r503::CMD_AURA_LED:
                emit_ack(r503::CONF_OK);
                break;
            case r503::CMD_EMPTY:
                flash_.clear();
                char_buffer_.clear();
                emit_ack(r503::CONF_OK);
                break;
            default:
                emit_ack(r503::CONF_UNSUPPORTED_CMD);
                break;
        }
    }

    size_t content_len() const {
        switch (packet_code) {
            case 0: return 32;
            case 1: return 64;
            case 2: return 128;
            case 3: return 256;
            default: return 128;
        }
    }

public:
    bool     finger_present   = false;
    bool     match_slot_valid = false;
    uint16_t match_slot       = 0;
    uint16_t match_score      = 0;

private:
    std::map<int, Bytes> flash_;    /* the on-module template library */
    Bytes rx_, char_buffer_, other_buffer_, incoming_, notepad_;
    std::deque<char> tx_;
    bool receiving_ = false;
    uint32_t clock_ = 1000;
};

/* ======================================================================== */
/*  the suite                                                               */
/* ======================================================================== */
int r503_protocol_tests(int* out_checks) {
    t_checks = 0;
    t_fail = 0;
    const uint32_t A = r503::kDefaultAddress;

    printf("\n--- R503 PROTOCOL (pure) + RealR503HAL driver ---\n");

    /* =================================================================
     * 1. CHECKSUM — against every value the V1.4.1 manual states literally
     * ================================================================= */
    {
        /* ReadSysPara: PID 0x01 + len 0x0003 + 0x0F = 0x0013 (extract:685) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x0F})) == 0x0013,
               "checksum: ReadSysPara == datasheet 0x0013");
        /* TempleteNum -> 0x0021 (extract:723) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x1D})) == 0x0021,
               "checksum: TempleteNum == datasheet 0x0021");
        /* RegModel -> 0x0009 (extract:995) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x05})) == 0x0009,
               "checksum: RegModel == datasheet 0x0009");
        /* ReadProdInfo -> 0x0040 (extract:829) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x3C})) == 0x0040,
               "checksum: ReadProdInfo == datasheet 0x0040");
        /* Empty -> 0x0011 (extract:1178) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x0D})) == 0x0011,
               "checksum: Empty == datasheet 0x0011");
        /* GetImg -> 0x0005 (extract:875) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x01})) == 0x0005,
               "checksum: GetImg == datasheet 0x0005");
        /* Match -> 0x0007 (extract:1205) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x03})) == 0x0007,
               "checksum: Match == datasheet 0x0007");
        /* GetImageEx -> 0x002C (extract:1268) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x28})) == 0x002C,
               "checksum: GetImageEx == datasheet 0x002C");
        /* Cancel -> 0x0034 (extract:1293) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x30})) == 0x0034,
               "checksum: Cancel == datasheet 0x0034");
        /* DownImage -> 0x000F (extract:937) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x0B})) == 0x000F,
               "checksum: DownImage == datasheet 0x000F");
        /* UpImage -> 0x000E (extract:901) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x0A})) == 0x000E,
               "checksum: UpImage == datasheet 0x000E");
        /* GetRandomCode -> 0x0018 (extract:1671) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x14})) == 0x0018,
               "checksum: GetRandomCode == datasheet 0x0018");
        /* ReadInfPage -> 0x001A (extract:1697) */
        TCHECK(r503::checksum(0x01, 0x0003, B({0x16})) == 0x001A,
               "checksum: ReadInfPage == datasheet 0x001A");

        /* THE WORKED EXAMPLE the manual prints in full (extract:1409):
         * "Cyan breathing light:  Send: EF 01 FF FF FF FF 01 00 07
         *  35 01 50 06 00   00 94"  — this pins the checksum SCOPE (header
         * and address excluded) rather than merely its arithmetic. */
        const Bytes aura = r503::cmd_aura_led(A, r503::AURA_BREATHING, 0x50,
                                              r503::AURA_CYAN, 0x00);
        const Bytes want = B({0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x00,
                              0x07, 0x35, 0x01, 0x50, 0x06, 0x00, 0x00, 0x94});
        TCHECK(aura == want,
               "AuraLED cyan-breathing == the manual's worked example, byte for byte");
    }

    /* =================================================================
     * 2. THE FRAME — and the REAL BENCH ACK captured this session
     * ================================================================= */
    {
        /* Bench capture, 2026-08-20, UART1 GPIO4/GPIO5 @57600 8N1: a real
         * VerifyPassword acknowledge from the physical module. */
        const Bytes bench = B({0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF,
                               0x07, 0x00, 0x03, 0x00, 0x00, 0x0A});
        r503::ScanOutcome s = r503::scan_packet(bench);
        TCHECK(s.kind == r503::ScanOutcome::Kind::PACKET,
               "BENCH ACK: the real VfyPwd reply frames as a valid packet");
        TCHECK(s.consumed == bench.size(), "BENCH ACK: consumed the whole frame");
        TCHECK(s.packet.pid == r503::PID_ACK, "BENCH ACK: PID is 0x07");
        TCHECK(s.packet.address == 0xFFFFFFFFu, "BENCH ACK: default address");
        Ack a;
        TCHECK(r503::parse_ack(s.packet, a), "BENCH ACK: parses as an ACK");
        TCHECK(a.confirmation == 0x00, "BENCH ACK: confirmation 0x00 (correct password)");
        TCHECK(a.params.empty(), "BENCH ACK: no parameter bytes");

        /* length counts the checksum: content 1 byte -> length field 0x0003 */
        const Bytes rt = r503::build_packet(A, r503::PID_ACK, B({0x00}));
        TCHECK(rt == bench, "build_packet reproduces the bench ACK exactly");
    }

    /* =================================================================
     * 3. COMMAND BUILDERS — each against its datasheet table
     * ================================================================= */
    {
        auto len_of = [](const Bytes& p) -> uint16_t {
            return static_cast<uint16_t>((static_cast<uint8_t>(p[7]) << 8) |
                                         static_cast<uint8_t>(p[8]));
        };
        TCHECK(len_of(r503::cmd_verify_password(A, 0)) == 0x0007,
               "VfyPwd length field == 0x0007");
        TCHECK(len_of(r503::cmd_read_sys_para(A)) == 0x0003,
               "ReadSysPara length field == 0x0003");
        TCHECK(len_of(r503::cmd_read_prod_info(A)) == 0x0003,
               "ReadProdInfo length field == 0x0003");
        TCHECK(len_of(r503::cmd_template_num(A)) == 0x0003,
               "TempleteNum length field == 0x0003");
        TCHECK(len_of(r503::cmd_read_index_table(A, 0)) == 0x0004,
               "ReadIndexTable length field == 0x0004");
        TCHECK(len_of(r503::cmd_img2tz(A, 1)) == 0x0004,
               "GenChar length field == 0x0004");
        TCHECK(len_of(r503::cmd_up_char(A, 1)) == 0x0004,
               "UpChar length field == 0x0004");
        TCHECK(len_of(r503::cmd_down_char(A, 1)) == 0x0004,
               "DownChar length field == 0x0004");
        TCHECK(len_of(r503::cmd_store(A, 1, 0)) == 0x0006,
               "Store length field == 0x0006");
        TCHECK(len_of(r503::cmd_load_char(A, 1, 0)) == 0x0006,
               "LoadChar length field == 0x0006");
        TCHECK(len_of(r503::cmd_delete_char(A, 0, 1)) == 0x0007,
               "DeletChar length field == 0x0007");
        TCHECK(len_of(r503::cmd_search(A, 1, 0, 200)) == 0x0008,
               "Search length field == 0x0008");
        TCHECK(len_of(r503::cmd_aura_led(A, 1, 0, 1, 0)) == 0x0007,
               "AuraLedConfig length field == 0x0007");
        TCHECK(len_of(r503::cmd_write_notepad(A, 0, Bytes())) == 0x0024,
               "WriteNotepad length field == 0x0024 (32 data bytes, padded)");
        TCHECK(len_of(r503::cmd_read_notepad(A, 0)) == 0x0004,
               "ReadNotepad length field == 0x0004");

        /* THE TWO PACKETS THE ARCHITECT EYEBALLS.  Transcribed from the
         * DownChar (extract:1049-1068) and UpChar (extract:1011-1030)
         * command tables: 0xEF01 | addr | 0x01 | 0x0004 | code | bufID | sum */
        const Bytes dc = r503::cmd_down_char(A, 1);
        const Bytes uc = r503::cmd_up_char(A, 1);
        TCHECK(dc == B({0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x00, 0x04,
                        0x09, 0x01, 0x00, 0x0F}),
               "DownChar command bytes == the datasheet table");
        TCHECK(uc == B({0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x00, 0x04,
                        0x08, 0x01, 0x00, 0x0E}),
               "UpChar command bytes == the datasheet table");
        printf("   DownChar cmd : %s\n", r503::hex(dc).c_str());
        printf("   UpChar   cmd : %s\n", r503::hex(uc).c_str());
    }

    /* =================================================================
     * 4. THE SCANNER — resync, partial frames, and the power-on 0x55
     * ================================================================= */
    {
        const Bytes good = r503::build_packet(A, r503::PID_ACK, B({0x00}));

        /* [CXC-R503-UNSOLICITED-0X55]: a bare 0x55 in front of a real packet */
        Bytes with55 = B({0x55}) + good;
        r503::ScanOutcome s = r503::scan_packet(with55);
        TCHECK(s.kind == r503::ScanOutcome::Kind::PACKET,
               "0x55: a stray power-on byte does not break framing");
        TCHECK(s.skipped == 1, "0x55: the stray byte is REPORTED, not swallowed");
        TCHECK(s.consumed == with55.size(), "0x55: consumed stray + packet");

        /* arbitrary garbage, including a lone 0xEF not followed by 0x01 */
        Bytes junk = B({0x55, 0x00, 0xEF, 0x7F, 0xAA}) + good;
        s = r503::scan_packet(junk);
        TCHECK(s.kind == r503::ScanOutcome::Kind::PACKET, "resync past garbage");
        TCHECK(s.skipped == 5, "garbage byte count reported exactly");

        /* a partial packet must ask for more and consume nothing of it */
        s = r503::scan_packet(good.substr(0, good.size() - 1));
        TCHECK(s.kind == r503::ScanOutcome::Kind::NEED_MORE, "partial -> NEED_MORE");
        TCHECK(s.consumed == 0, "partial: nothing consumed");

        /* a trailing lone 0xEF is kept (it may begin the next header) */
        s = r503::scan_packet(B({0x55, 0xEF}));
        TCHECK(s.kind == r503::ScanOutcome::Kind::NEED_MORE &&
               s.consumed == 1 && s.skipped == 1,
               "a trailing lone 0xEF is preserved as a possible header start");

        /* a corrupted checksum is DETECTED, and the framer resyncs past it */
        Bytes bad = good;
        bad[bad.size() - 1] = static_cast<char>(bad[bad.size() - 1] ^ 0xFF);
        s = r503::scan_packet(bad);
        TCHECK(s.kind == r503::ScanOutcome::Kind::BAD_CHECKSUM,
               "a wrong checksum is reported, never accepted");
        TCHECK(s.consumed == bad.size(), "bad checksum: whole frame skipped");

        /* an absurd length field is garbage, not a long packet */
        Bytes badlen = good;
        badlen[7] = static_cast<char>(0xFF);
        badlen[8] = static_cast<char>(0xFF);
        s = r503::scan_packet(badlen);
        TCHECK(s.kind == r503::ScanOutcome::Kind::BAD_LENGTH,
               "an out-of-range length field resyncs instead of blocking");
        TCHECK(s.consumed == 2, "bad length: skip the header and resync");

        /* the scanner never spins: every outcome consumes or asks for bytes */
        Bytes stream = B({0x55, 0x55}) + good + B({0x55}) + good;
        int found = 0;
        while (!stream.empty()) {
            r503::ScanOutcome o = r503::scan_packet(stream);
            if (o.kind == r503::ScanOutcome::Kind::PACKET) found++;
            if (o.consumed == 0) break;
            stream.erase(0, o.consumed);
        }
        TCHECK(found == 2, "two packets recovered from a 0x55-littered stream");
    }

    /* =================================================================
     * 5. ⭐ THE HEADLINE: DownChar/UpChar MULTI-PACKET FRAMING + REASSEMBLY
     *    Build a data stream from known bytes, re-frame it through the real
     *    scanner, reassemble it, assert BYTE-EXACT.  This is the test that
     *    answers the de-risk question at the protocol-correctness level.
     * ================================================================= */
    {
        auto ramp = [](size_t n) {
            Bytes b;
            b.reserve(n);
            for (size_t i = 0; i < n; i++)
                b.push_back(static_cast<char>((i * 7 + 3) & 0xFF));
            return b;
        };
        /* Round-trip a payload of `len` at packet content length `n`:
         * build -> concatenate -> scan -> reassemble -> compare. */
        auto roundtrip = [&](size_t len, size_t n, size_t expect_packets,
                             const char* what) {
            const Bytes src = ramp(len);
            std::vector<Bytes> pkts = r503::build_data_packets(A, src, n);
            char msg[160];

            std::snprintf(msg, sizeof msg, "%s: packet COUNT == ceil(L/N)", what);
            TCHECK(pkts.size() == expect_packets, msg);

            /* every packet but the last is PID 0x02; the last is PID 0x08 */
            bool pids_ok = !pkts.empty();
            for (size_t i = 0; i + 1 < pkts.size(); i++)
                if (static_cast<uint8_t>(pkts[i][6]) != r503::PID_DATA) pids_ok = false;
            if (!pkts.empty() &&
                static_cast<uint8_t>(pkts.back()[6]) != r503::PID_END) pids_ok = false;
            std::snprintf(msg, sizeof msg, "%s: PIDs are 0x02... then 0x08", what);
            TCHECK(pids_ok, msg);

            /* every length field == its own content + 2 (Length counts the
             * checksum — [CXC-R503-FRAMING]) */
            bool lens_ok = true;
            for (const Bytes& p : pkts) {
                const uint16_t lf =
                    static_cast<uint16_t>((static_cast<uint8_t>(p[7]) << 8) |
                                          static_cast<uint8_t>(p[8]));
                if (lf != p.size() - 9) lens_ok = false;      /* total-9 == N+2 */
                if (lf < 2 || lf > r503::kMaxLengthField) lens_ok = false;
            }
            std::snprintf(msg, sizeof msg,
                          "%s: every length field == content + 2", what);
            TCHECK(lens_ok, msg);

            /* concatenate into one wire stream and re-frame it for real */
            Bytes wire;
            for (const Bytes& p : pkts) wire += p;
            r503::DataReassembler re(len + 1024);
            size_t framed = 0;
            while (!wire.empty()) {
                r503::ScanOutcome o = r503::scan_packet(wire);
                if (o.consumed == 0) break;
                wire.erase(0, o.consumed);
                if (o.kind == r503::ScanOutcome::Kind::PACKET) {
                    framed++;
                    re.feed(o.packet);
                }
            }
            std::snprintf(msg, sizeof msg, "%s: every packet re-frames", what);
            TCHECK(framed == pkts.size(), msg);
            std::snprintf(msg, sizeof msg, "%s: stream TERMINATES on PID 0x08", what);
            TCHECK(re.complete(), msg);
            std::snprintf(msg, sizeof msg, "%s: ⭐ BYTE-EXACT round trip", what);
            TCHECK(re.data() == src, msg);
            std::snprintf(msg, sizeof msg, "%s: length preserved exactly", what);
            TCHECK(re.data().size() == len, msg);
        };

        /* the real case: a 1536-byte R503 template at the factory default 128 */
        roundtrip(1536, 128, 12, "1536B @ N=128 (the R503 default)");
        /* a SHORT last packet — ambiguity A1 */
        roundtrip(1500, 128, 12, "1500B @ N=128 (short last packet)");
        roundtrip(1537, 128, 13, "1537B @ N=128 (1-byte last packet)");
        /* every other legal packet length (SetSysPara parameter 6) */
        roundtrip(1536, 32,  48, "1536B @ N=32");
        roundtrip(1536, 64,  24, "1536B @ N=64");
        roundtrip(1536, 256,  6, "1536B @ N=256");
        /* a payload smaller than one packet: a lone PID 0x08 */
        roundtrip(10, 128, 1, "10B @ N=128 (single end packet)");
        roundtrip(128, 128, 1, "128B @ N=128 (exact single packet)");

        /* Ambiguity A2, both halves.  The BUILDER emits no trailing empty
         * packet on an exact multiple... */
        {
            const Bytes src = ramp(256);
            std::vector<Bytes> p = r503::build_data_packets(A, src, 128);
            TCHECK(p.size() == 2, "A2: exact multiple -> NO trailing empty packet");
            TCHECK(static_cast<uint8_t>(p[1][6]) == r503::PID_END,
                   "A2: the last DATA-BEARING packet carries PID 0x08");
        }
        /* ...and the REASSEMBLER tolerates one if a module sends it. */
        {
            const Bytes src = ramp(256);
            std::vector<Bytes> p = r503::build_data_packets(A, src, 128);
            /* rewrite packet 1 as PID 0x02 and append an empty PID 0x08 */
            std::vector<Bytes> alt;
            alt.push_back(r503::build_packet(A, r503::PID_DATA, src.substr(0, 128)));
            alt.push_back(r503::build_packet(A, r503::PID_DATA, src.substr(128, 128)));
            alt.push_back(r503::build_packet(A, r503::PID_END, Bytes()));
            r503::DataReassembler re(4096);
            for (const Bytes& b : alt) {
                r503::ScanOutcome o = r503::scan_packet(b);
                TCHECK(o.kind == r503::ScanOutcome::Kind::PACKET,
                       "A2: an EMPTY end packet is still a legal frame");
                re.feed(o.packet);
            }
            TCHECK(re.complete(), "A2: an empty 0x08 terminates the stream");
            TCHECK(re.data() == src,
                   "A2: a trailing empty 0x08 contributes zero bytes (tolerated)");
        }

        /* A stray 0x55 BETWEEN data packets must not corrupt the payload —
         * this is the [CXC-R503-UNSOLICITED-0X55] trap inside a transfer. */
        {
            const Bytes src = ramp(1536);
            std::vector<Bytes> pkts = r503::build_data_packets(A, src, 128);
            Bytes wire;
            for (size_t i = 0; i < pkts.size(); i++) {
                if (i == 3 || i == 7) wire.push_back(static_cast<char>(0x55));
                wire += pkts[i];
            }
            r503::DataReassembler re(4096);
            size_t skipped = 0;
            while (!wire.empty()) {
                r503::ScanOutcome o = r503::scan_packet(wire);
                if (o.consumed == 0) break;
                skipped += o.skipped;
                wire.erase(0, o.consumed);
                if (o.kind == r503::ScanOutcome::Kind::PACKET) re.feed(o.packet);
            }
            TCHECK(skipped == 2, "stray bytes mid-transfer are counted");
            TCHECK(re.complete() && re.data() == src,
                   "⭐ BYTE-EXACT round trip DESPITE two stray 0x55 mid-stream");
        }

        /* The reassembler's own negative controls. */
        {
            r503::DataReassembler re(4096);
            Packet cmd; cmd.pid = r503::PID_ACK; cmd.content = B({0x00});
            TCHECK(re.feed(cmd) == r503::DataReassembler::Feed::WRONG_PID,
                   "NEGATIVE: an ACK inside a data stream is a DESYNC, not data");
            Packet d; d.pid = r503::PID_END; d.content = Bytes(64, 'x');
            TCHECK(re.feed(d) == r503::DataReassembler::Feed::COMPLETE,
                   "PID 0x08 completes the stream");
            TCHECK(re.feed(d) == r503::DataReassembler::Feed::ALREADY_COMPLETE,
                   "NEGATIVE: feeding a completed stream is reported, not absorbed");
            r503::DataReassembler small(16);
            Packet big; big.pid = r503::PID_DATA; big.content = Bytes(32, 'y');
            TCHECK(small.feed(big) == r503::DataReassembler::Feed::TOO_LARGE,
                   "NEGATIVE: a stream past its ceiling is refused, not grown");
        }

        /* the builder refuses degenerate input rather than emitting nonsense */
        TCHECK(r503::build_data_packets(A, Bytes(), 128).empty(),
               "NEGATIVE: an empty payload builds no packets");
        TCHECK(r503::build_data_packets(A, ramp(10), 0).empty(),
               "NEGATIVE: a zero packet length builds no packets");
    }

    /* =================================================================
     * 6. ReadSysPara / ReadProdInfo / index-table decode
     * ================================================================= */
    {
        Bytes q(16, '\0');
        q[4] = 0x00; q[5] = static_cast<char>(200);     /* library size 200  */
        q[6] = 0x00; q[7] = 0x03;                       /* security level 3  */
        q[8] = static_cast<char>(0xFF); q[9] = static_cast<char>(0xFF);
        q[10] = static_cast<char>(0xFF); q[11] = static_cast<char>(0xFF);
        q[12] = 0x00; q[13] = 0x02;                     /* packet code 2     */
        q[14] = 0x00; q[15] = 0x06;                     /* baud N = 6        */
        r503::SysPara sp;
        TCHECK(r503::parse_sys_para(q, sp), "ReadSysPara: 16 bytes decode");
        TCHECK(sp.finger_library_size == 200, "SysPara: library size 200");
        TCHECK(sp.security_level == 3, "SysPara: security level 3");
        TCHECK(sp.device_address == 0xFFFFFFFFu, "SysPara: device address");
        TCHECK(sp.packet_content_len() == 128,
               "SysPara: packet code 2 -> 128 bytes (SetSysPara param 6 table)");
        TCHECK(sp.baud_n * 9600 == 57600, "SysPara: N=6 -> 57600 baud");
        r503::SysPara z;
        z.packet_size_code = 0; TCHECK(z.packet_content_len() == 32, "code 0 -> 32");
        z.packet_size_code = 1; TCHECK(z.packet_content_len() == 64, "code 1 -> 64");
        z.packet_size_code = 3; TCHECK(z.packet_content_len() == 256, "code 3 -> 256");
        z.packet_size_code = 9;
        TCHECK(z.packet_content_len() == 128 && !z.packet_size_code_known(),
               "an unknown packet-size code falls back LOUD to 128, not to 32");
        TCHECK(!r503::parse_sys_para(Bytes(15, '\0'), sp),
               "NEGATIVE: a short ReadSysPara ack is refused, not padded");

        Bytes pi(50, '\0');
        std::memcpy(&pi[0], "R503", 4);
        pi[42] = 0x06; pi[43] = 0x00;                   /* 1536 template     */
        pi[44] = 0x00; pi[45] = static_cast<char>(200); /* 200 templates     */
        r503::ProdInfo info;
        TCHECK(r503::parse_prod_info(pi, info), "ReadProdInfo: 50 bytes decode");
        TCHECK(info.module_model == "R503", "ProdInfo: ASCII stops at the 0x00 pad");
        TCHECK(info.template_size == 1536,
               "ProdInfo: PARAM_TMPL_SIZE == 1536 (matches the spec table, "
               "extract:186) — READ, not assumed");
        TCHECK(info.template_total == 200, "ProdInfo: PARAM_TMPL_TOTAL == 200");
        TCHECK(!r503::parse_prod_info(Bytes(20, '\0'), info),
               "NEGATIVE: a short ReadProdInfo ack is refused");

        Ack ia;
        ia.confirmation = 0x00;
        ia.params.assign(32, '\0');
        ia.params[0] = static_cast<char>(0b00001001);   /* slots 0 and 3     */
        ia.params[1] = static_cast<char>(0b00000010);   /* slot 9            */
        std::vector<int> slots;
        TCHECK(r503::parse_index_table_ack(ia, 0, slots), "index table decodes");
        TCHECK(slots.size() == 3 && slots[0] == 0 && slots[1] == 3 && slots[2] == 9,
               "index table: LSB-first bit order gives slots {0,3,9}");
        TCHECK(r503::parse_index_table_ack(ia, 1, slots) && slots[0] == 256,
               "index table: page 1 offsets by 256");

        Ack sa;
        sa.confirmation = 0x00;
        sa.params = B({0x00, 0x2A, 0x01, 0x2C});
        uint16_t page = 0, score = 0;
        TCHECK(r503::parse_search_ack(sa, page, score) && page == 42 && score == 300,
               "Search ack: pageID 42, score 300");
    }

    /* =================================================================
     * 7. THE DRIVER against a datasheet-shaped fake module
     * ================================================================= */
    {
        FakeR503 fake;
        RealR503HAL hal(&fake);
        FingerprintHALFailure err;

        TCHECK(hal.begin(err), "begin(): VfyPwd + ReadSysPara + ReadProdInfo");
        TCHECK(hal.ready(), "begin(): the link reports ready");

        Capabilities c = hal.capabilities();
        TCHECK(c.slot_count == 200, "capabilities: slot_count DISCOVERED == 200");
        TCHECK(c.template_size_bytes == 1536,
               "capabilities: template_size_bytes READ from ReadProdInfo");
        TCHECK(std::string(hal.template_size_source()).find("ReadProdInfo") !=
                   std::string::npos,
               "capabilities: the template size names its SOURCE (not hardcoded)");
        TCHECK(c.captures_per_template == 2, "capabilities: two captures (RegModel)");
        TCHECK(c.has_feedback_channel(FeedbackChannel::AURA_LED),
               "capabilities: the Aura ring is announced");
        TCHECK(c.supports_readback, "capabilities: readback CLAIMED (UpChar exists)");
        TCHECK(hal.packet_content_len() == 128,
               "the packet length comes from ReadSysPara, never a constant");

        /* store_template -> DownChar + Store, then read it back */
        Bytes t(1536, '\0');
        for (size_t i = 0; i < t.size(); i++)
            t[i] = static_cast<char>((i * 31 + 11) & 0xFF);
        Slot slot = -1;
        TCHECK(hal.store_template(t, slot, err),
               "store_template: DownChar + Store succeeds");
        TCHECK(slot == 0, "store_template: first free slot allocated");
        TCHECK(fake.char_buffer() == t,
               "⭐ store_template: the module received the payload BYTE-EXACT");

        std::set<Slot> occ = hal.read_occupancy();
        TCHECK(occ.size() == 1 && *occ.begin() == 0,
               "read_occupancy: ReadIndexTable reports the stored slot");

        Bytes back;
        TCHECK(hal.read_template(0, back, err),
               "read_template: LoadChar + UpChar succeeds");
        TCHECK(back == t, "⭐ read_template: BYTE-EXACT readback");

        /* size guard, both ends ([CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD]) */
        Bytes wrong(1000, 'z');
        Slot s2 = -1;
        TCHECK(!hal.store_template(wrong, s2, err),
               "NEGATIVE: a wrong-size template is REFUSED before it hits the wire");
        TCHECK(err.axis == FailureAxis::CAPACITY,
               "wrong-size template fails on the CAPACITY axis");

        /* match() */
        MatchResult m = hal.match();
        TCHECK(m.outcome == MatchOutcome::NO_FINGER,
               "match(): no finger -> NO_FINGER (not a failure)");
        fake.finger_present = true;
        m = hal.match();
        TCHECK(m.outcome == MatchOutcome::NO_MATCH,
               "match(): finger, no library hit -> NO_MATCH");
        TCHECK(!m.slot.has_value() && !m.score.has_value(),
               "match(): NO_MATCH carries neither slot nor score");
        fake.match_slot_valid = true;
        fake.match_slot = 7;
        fake.match_score = 210;
        m = hal.match();
        TCHECK(m.outcome == MatchOutcome::MATCHED && m.slot.value() == 7 &&
                   m.score.value() == 210,
               "match(): GenImg -> Img2Tz -> Search -> MATCHED(slot 7, score 210)");
        TCHECK(fp_match_result_valid(m.outcome, m.slot.has_value(),
                                     m.score.has_value()),
               "match(): slot and score together or not at all");

        /* roster stamp, in the module's own notepad */
        TCHECK(!hal.read_roster_stamp().has_value(),
               "roster stamp: a blank notepad reports NO stamp");
        const std::string uuid = "6f2b1c44-9f0e-4a3d-9d21-0f7a5b8c1e33";
        hal.write_roster_stamp(uuid);
        std::optional<RosterStamp> got = hal.read_roster_stamp();
        TCHECK(got.has_value() && got.value() == uuid,
               "roster stamp: a 36-char UUID round-trips through the notepad");

        /* feedback */
        TCHECK(hal.command_feedback(FeedbackState::READY, FpProgress{}, err),
               "command_feedback: READY drives the Aura ring");
        TCHECK(hal.command_feedback(FeedbackState::REJECT_SILENT, FpProgress{}, err),
               "command_feedback: REJECT_SILENT is accepted");

        /* empty() */
        TCHECK(hal.empty(err), "empty(): the library clears");
        TCHECK(hal.read_occupancy().empty(), "empty(): occupancy is now {}");
    }

    /* =================================================================
     * 8. ⭐ run_sync_test() — AND ITS NEGATIVE CONTROLS
     *    A synctest that can only report PASS would be worthless on the
     *    bench.  Each fault below must produce a FAIL, for the right reason.
     * ================================================================= */
    {
        /* --- 8a. the happy path -------------------------------------- */
        FakeR503 fake;
        RealR503HAL hal(&fake);
        FingerprintHALFailure err;
        TCHECK(hal.begin(err), "synctest: begin() ok");
        R503SyncTestResult r = hal.run_sync_test(1);
        TCHECK(r.pass(), "⭐ synctest PASSES against a datasheet-conformant module");
        TCHECK(r.sent_len == 1536 && r.recv_len == 1536,
               "synctest: 1536 bytes out, 1536 back");
        TCHECK(r.data_packets_sent == 12 && r.data_packets_recv == 12,
               "synctest: 12 data packets each way at N=128");
        TCHECK(r.downchar_acked && r.upchar_acked && r.stream_complete,
               "synctest: both directions acked and the stream terminated");
        TCHECK(r.packet_content_len == 128,
               "synctest: the packet length came from ReadSysPara");
        TCHECK(std::string(r.size_source).find("ReadProdInfo") != std::string::npos,
               "synctest: the template size names its source");
        TCHECK(r.failure.empty(), "synctest: a pass carries no failure string");

        /* --- 8b. NEGATIVE: the module refuses upload (0x0E) ----------- */
        {
            FakeR503 f2; f2.refuse_downchar = true;
            RealR503HAL h2(&f2);
            FingerprintHALFailure e2;
            TCHECK(h2.begin(e2), "synctest/refuse-down: begin ok");
            R503SyncTestResult x = h2.run_sync_test(1);
            TCHECK(!x.pass(), "NEGATIVE: a DownChar refusal FAILS the synctest");
            TCHECK(x.down_conf == r503::CONF_CANNOT_RECEIVE,
                   "NEGATIVE: the 0x0E refusal code is reported verbatim");
            TCHECK(x.failure.find("does NOT accept template upload") !=
                       std::string::npos,
                   "NEGATIVE: the failure NAMES the architectural consequence");
        }
        /* --- 8c. NEGATIVE: the module refuses download (0x0D) --------- */
        {
            FakeR503 f3; f3.refuse_upchar = true;
            RealR503HAL h3(&f3);
            FingerprintHALFailure e3;
            TCHECK(h3.begin(e3), "synctest/refuse-up: begin ok");
            R503SyncTestResult x = h3.run_sync_test(1);
            TCHECK(!x.pass(), "NEGATIVE: an UpChar refusal FAILS the synctest");
            TCHECK(x.up_conf == r503::CONF_UPLOAD_FAIL,
                   "NEGATIVE: the 0x0D refusal code is reported verbatim");
        }
        /* --- 8d. NEGATIVE: one corrupted byte ------------------------- */
        {
            FakeR503 f4; f4.corrupt_at = 900;
            RealR503HAL h4(&f4);
            FingerprintHALFailure e4;
            TCHECK(h4.begin(e4), "synctest/corrupt: begin ok");
            R503SyncTestResult x = h4.run_sync_test(1);
            TCHECK(!x.pass(), "NEGATIVE: ONE flipped byte FAILS the synctest");
            TCHECK(x.first_diff_index == 900,
                   "NEGATIVE: the synctest names the EXACT diverging index");
            TCHECK(x.recv_len == x.sent_len,
                   "NEGATIVE: a corruption is length-preserving — only a "
                   "byte compare could have caught it");
        }
        /* --- 8e. NEGATIVE: the stream never terminates ---------------- */
        {
            FakeR503 f5; f5.drop_end_packet = true;
            RealR503HAL h5(&f5);
            FingerprintHALFailure e5;
            TCHECK(h5.begin(e5), "synctest/no-end: begin ok");
            R503SyncTestResult x = h5.run_sync_test(1);
            TCHECK(!x.pass() && !x.stream_complete,
                   "NEGATIVE: a missing PID 0x08 FAILS the synctest");
            TCHECK(x.failure.find("never terminated") != std::string::npos,
                   "NEGATIVE: the failure says the stream never terminated");
        }
        /* --- 8f. NEGATIVE: a short stream ----------------------------- */
        {
            FakeR503 f6; f6.truncate_stream = true;
            RealR503HAL h6(&f6);
            FingerprintHALFailure e6;
            TCHECK(h6.begin(e6), "synctest/truncated: begin ok");
            R503SyncTestResult x = h6.run_sync_test(1);
            TCHECK(!x.pass(), "NEGATIVE: a truncated stream FAILS the synctest");
        }
        /* --- 8g. NEGATIVE: ambiguity A5 made real — DownChar and UpChar
         * address DIFFERENT buffers.  The manual cannot rule this out; the
         * synctest must catch it, because it is exactly the shape in which
         * the hardware would betray the architecture. */
        {
            FakeR503 f7; f7.separate_buffers = true;
            RealR503HAL h7(&f7);
            FingerprintHALFailure e7;
            TCHECK(h7.begin(e7), "synctest/split-buffer: begin ok");
            R503SyncTestResult x = h7.run_sync_test(1);
            TCHECK(!x.pass(),
                   "NEGATIVE: DownChar and UpChar using DIFFERENT buffers "
                   "FAILS the synctest (ambiguity A5 is falsifiable)");
        }
        /* --- 8h. the part that does not implement ReadProdInfo:
         * the size must be MEASURED, never invented. */
        {
            FakeR503 f8;
            f8.no_prod_info = true;
            f8.preload_buffer(Bytes(1536, 'q'));
            RealR503HAL h8(&f8);
            FingerprintHALFailure e8;
            TCHECK(h8.begin(e8), "synctest/no-0x3C: begin still succeeds");
            TCHECK(h8.capabilities().template_size_bytes == 0,
                   "no ReadProdInfo -> template_size_bytes is 0 == UNKNOWN, "
                   "NOT a fabricated 1536");
            R503SyncTestResult x = h8.run_sync_test(1);
            TCHECK(x.pass(), "synctest recovers by MEASURING the template size");
            TCHECK(x.template_size == 1536 &&
                       std::string(x.size_source).find("MEASURED") != std::string::npos,
                   "the measured size names itself as measured, not as read");
        }
        /* --- 8i. a module with a non-default packet length ------------ */
        {
            FakeR503 f9; f9.packet_code = 0;         /* 32-byte packets */
            RealR503HAL h9(&f9);
            FingerprintHALFailure e9;
            TCHECK(h9.begin(e9), "synctest/N=32: begin ok");
            R503SyncTestResult x = h9.run_sync_test(1);
            TCHECK(x.pass(), "synctest passes at N=32 (packet length is a SETTING)");
            TCHECK(x.packet_content_len == 32 && x.data_packets_sent == 48,
                   "synctest/N=32: 48 packets, framed to the module's own setting");
        }
        /* --- 8j. a module with a non-default TEMPLATE size ------------ */
        {
            FakeR503 fa; fa.template_size = 512;
            RealR503HAL ha(&fa);
            FingerprintHALFailure ea;
            TCHECK(ha.begin(ea), "synctest/512B template: begin ok");
            R503SyncTestResult x = ha.run_sync_test(1);
            TCHECK(x.pass() && x.sent_len == 512,
                   "synctest follows the module's announced template size, "
                   "not a hardcoded 1536");
        }
        /* --- 8k. NEGATIVE: a dead link ------------------------------- */
        {
            class DeadPort final : public R503Port {
            public:
                void write(const Bytes&) override {}
                int read_byte(uint32_t) override { return -1; }
                void drain_input() override {}
                uint32_t now_ms() override { return clock_++; }
            private: uint32_t clock_ = 0;
            };
            DeadPort dead;
            RealR503HAL hd(&dead);
            FingerprintHALFailure ed;
            TCHECK(!hd.begin(ed), "NEGATIVE: a silent module fails begin()");
            TCHECK(ed.axis == FailureAxis::TRANSPORT,
                   "NEGATIVE: silence is TRANSPORT, never a sensor refusal");
            TCHECK(!ed.sensor_code.has_value(),
                   "NEGATIVE: a transport failure quotes NO sensor code");
            TCHECK(fp_failure_valid(ed.axis, ed.sensor_code),
                   "NEGATIVE: the failure is taxonomy-valid");
            TCHECK(hd.capabilities().slot_count == 0,
                   "NEGATIVE: an un-begun HAL announces zeroes, not the mock's 200");
            R503SyncTestResult x = hd.run_sync_test(1);
            TCHECK(!x.pass() && !x.failure.empty(),
                   "NEGATIVE: synctest on a dead link fails with a reason");
        }
        /* --- 8l. NEGATIVE: an UNDOCUMENTED confirmation code ----------
         * It must surface as UNKNOWN_CODE carrying the raw byte, never be
         * laundered into a documented refusal ([CXC-FPHAL-UNKNOWN-CODE]). */
        {
            class OddPort final : public R503Port {
            public:
                void write(const Bytes& b) override {
                    r503::ScanOutcome s = r503::scan_packet(b);
                    if (s.kind != r503::ScanOutcome::Kind::PACKET) return;
                    const uint8_t code = static_cast<uint8_t>(s.packet.content[0]);
                    /* 0x28 is the known-UNDOCUMENTED code the HAL names */
                    const uint8_t conf =
                        (code == r503::CMD_VFY_PWD || code == r503::CMD_READ_SYS_PARA)
                            ? 0x00 : 0x9A;
                    Bytes c; c.push_back(static_cast<char>(conf));
                    if (code == r503::CMD_READ_SYS_PARA) c += Bytes(16, '\0');
                    Bytes p = r503::build_packet(r503::kDefaultAddress,
                                                 r503::PID_ACK, c);
                    for (char ch : p) tx_.push_back(ch);
                }
                int read_byte(uint32_t) override {
                    if (tx_.empty()) return -1;
                    int c = static_cast<uint8_t>(tx_.front()); tx_.pop_front(); return c;
                }
                void drain_input() override { tx_.clear(); }
                uint32_t now_ms() override { return clock_++; }
            private:
                std::deque<char> tx_; uint32_t clock_ = 0;
            };
            OddPort odd;
            RealR503HAL ho(&odd);
            FingerprintHALFailure eo;
            TCHECK(ho.begin(eo), "odd-code: begin survives (VfyPwd/SysPara ok)");
            FingerprintHALFailure e2;
            TCHECK(!ho.empty(e2), "odd-code: Empty fails");
            TCHECK(e2.axis == FailureAxis::UNKNOWN_CODE,
                   "NEGATIVE: an undocumented 0x9A is UNKNOWN_CODE, not a refusal");
            TCHECK(e2.sensor_code.has_value() && e2.sensor_code.value() == 0x9A,
                   "NEGATIVE: the raw undocumented byte is carried through");
            TCHECK(fp_failure_valid(e2.axis, e2.sensor_code),
                   "NEGATIVE: UNKNOWN_CODE carries a code, as the taxonomy requires");
        }
    }

    /* =================================================================
     * 10. ⭐ THE 2026-08-20 BENCH FAILURE, REPRODUCED AND DIAGNOSED
     *
     * Observed on the physical part: DownChar ACKed 0x00, all 1536 bytes
     * went out as 12 packets of 128 with the last carrying PID 0x08, and
     * the UpChar that followed drew TOTAL SILENCE — no ack, no data.
     *
     * The distinction that matters: a REFUSAL (0x0D / 0xFC) would mean
     * "UpChar exists and declined".  SILENCE means the command was never
     * parsed as a command.  The leading hypothesis is therefore that the
     * module was still in its DownChar receive phase and ate those 13
     * bytes as data — i.e. our data phase never TERMINATED for it.
     *
     * These tests build a module for which that is literally true, and
     * assert that the instrument (a) reproduces the exact signature, and
     * (b) tells the two causes apart.  Without this, "UpChar unsupported"
     * and "our framing is wrong" are indistinguishable from the console.
     * ================================================================= */
    {
        auto ramp = [](size_t n) {
            Bytes b;
            b.reserve(n);
            for (size_t i = 0; i < n; i++)
                b.push_back(static_cast<char>((i * 7 + 3) & 0xFF));
            return b;
        };

        /* --- 10a. the SEPARATE_EMPTY_END framing is itself correct ----- */
        {
            const Bytes src = ramp(1536);
            std::vector<Bytes> p = r503::build_data_packets(
                A, src, 128, r503::EndPacketStyle::SEPARATE_EMPTY_END);
            TCHECK(p.size() == 13,
                   "A2/separate-end: ceil(L/N) payload packets PLUS a terminator");
            bool all_data = true;
            for (size_t i = 0; i + 1 < p.size(); i++)
                if (static_cast<uint8_t>(p[i][6]) != r503::PID_DATA) all_data = false;
            TCHECK(all_data,
                   "A2/separate-end: every PAYLOAD packet carries PID 0x02");
            TCHECK(static_cast<uint8_t>(p.back()[6]) == r503::PID_END,
                   "A2/separate-end: the terminator carries PID 0x08");
            TCHECK(p.back().size() == 11,
                   "A2/separate-end: the terminator is EMPTY (9 header + 2 cksum)");
            const uint16_t lf =
                static_cast<uint16_t>((static_cast<uint8_t>(p.back()[7]) << 8) |
                                      static_cast<uint8_t>(p.back()[8]));
            TCHECK(lf == 0x0002,
                   "A2/separate-end: the terminator's length field is 0x0002 "
                   "(checksum only — Length COUNTS the checksum)");
            /* and it still round-trips byte-exact through the real scanner */
            Bytes wire;
            for (const Bytes& b : p) wire += b;
            r503::DataReassembler re(4096);
            while (!wire.empty()) {
                r503::ScanOutcome o = r503::scan_packet(wire);
                if (o.consumed == 0) break;
                wire.erase(0, o.consumed);
                if (o.kind == r503::ScanOutcome::Kind::PACKET) re.feed(o.packet);
            }
            TCHECK(re.complete() && re.data() == src,
                   "⭐ A2/separate-end: BYTE-EXACT round trip, same as the "
                   "manual's framing");
            /* the standalone unwedge primitive */
            const Bytes e = r503::build_empty_end_packet(A);
            TCHECK(e == B({0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x08, 0x00, 0x02,
                           0x00, 0x0A}),
                   "the bare empty end packet is header|addr|0x08|0x0002|cksum");
        }

        /* --- 10b. ⭐ THE BENCH SIGNATURE, REPRODUCED -------------------
         * A module that only terminates on an EMPTY 0x08.  It supports
         * UpChar perfectly — but the manual's framing never ends its
         * receive phase, so UpChar is swallowed. */
        {
            FakeR503 f; f.strict_separate_end = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "bench-repro: begin() ok");

            R503SyncTestOptions o;
            o.retry_with_empty_end = false;   /* raw signature, no rescue */
            R503SyncTestResult x = h.run_sync_test(o);

            TCHECK(x.downchar_acked && x.down_conf == r503::CONF_OK,
                   "⭐ bench-repro: DownChar ACKs 0x00 — exactly as observed");
            TCHECK(x.data_packets_sent == 12,
                   "⭐ bench-repro: 12 data packets sent — exactly as observed");
            TCHECK(!x.pass() && !x.upchar_acked && x.recv_len == 0,
                   "⭐ bench-repro: UpChar returns NOTHING — exactly as observed");
            TCHECK(x.up_conf == 0xFF,
                   "⭐ bench-repro: SILENCE, not a refusal code — the whole "
                   "point.  A 0x0D would have meant 'UpChar declined'");
            TCHECK(x.failure.find("extract") != std::string::npos,
                   "bench-repro: the failure ROUTES the architect to "
                   "'r503 extract', the command that separates the causes");
        }

        /* --- 10c. ⭐ THE DIAGNOSIS: an empty end packet RESCUES it ------
         * Same module, retry enabled.  If a bare PID 0x08 revives the
         * transfer, the cause was OUR DATA PHASE FAILING TO TERMINATE —
         * a bug we own — and NOT an unsupported UpChar. */
        {
            FakeR503 f; f.strict_separate_end = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "rescue: begin() ok");
            R503SyncTestResult x = h.run_sync_test(1);   /* retry ON by default */
            TCHECK(x.retried_with_empty_end,
                   "rescue: the instrument TRIED the empty end packet");
            TCHECK(x.rescued_by_empty_end,
                   "⭐ rescue: an empty PID 0x08 revived the module — that is a "
                   "DIAGNOSIS (our framing), not merely a recovery");
            TCHECK(x.pass() && x.byte_exact,
                   "⭐ rescue: and the round trip then completes BYTE-EXACT");
        }

        /* --- 10d. ⭐ AND THE FRAMING SWEEP FIXES IT OUTRIGHT ------------ */
        {
            FakeR503 f; f.strict_separate_end = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "sweep: begin() ok");
            R503SyncTestOptions o;
            o.style = r503::EndPacketStyle::SEPARATE_EMPTY_END;
            R503SyncTestResult x = h.run_sync_test(o);
            TCHECK(x.pass(),
                   "⭐ sweep: the SEPARATE_EMPTY_END framing PASSES on the very "
                   "module the manual's framing hangs — this is why the style "
                   "is a bench parameter and not an authoring opinion");
            TCHECK(x.data_packets_sent == 13,
                   "sweep: 13 packets under separate-end (12 payload + terminator)");
            TCHECK(!x.retried_with_empty_end,
                   "sweep: no rescue was needed");
        }

        /* --- 10e. NEGATIVE: a module that wedges beyond rescue ---------- */
        {
            FakeR503 f; f.wedge_forever = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "wedged: begin() ok");
            R503SyncTestResult x = h.run_sync_test(1);
            TCHECK(!x.pass(), "NEGATIVE/wedged: synctest FAILS");
            TCHECK(x.retried_with_empty_end && !x.rescued_by_empty_end,
                   "NEGATIVE/wedged: the rescue was tried and REPORTED as failed "
                   "— a rescue that silently did nothing would be a lie");
            TCHECK(!x.alive_after,
                   "NEGATIVE/wedged: the instrument SAYS the part is no longer "
                   "answering, so the next command's failure is not a mystery");
        }

        /* --- 10f. THE INSTRUMENT FIX: evidence is no longer flushed -----
         * A module that answers the data phase with a late 0x01 "error when
         * receiving package".  The previous revision called flush() here and
         * DESTROYED exactly this packet. */
        {
            class LateErrorPort final : public R503Port {
            public:
                void write(const Bytes& b) override {
                    rx_ += b;
                    for (;;) {
                        r503::ScanOutcome s = r503::scan_packet(rx_);
                        if (s.kind == r503::ScanOutcome::Kind::NEED_MORE) {
                            if (s.consumed) rx_.erase(0, s.consumed);
                            return;
                        }
                        rx_.erase(0, s.consumed);
                        if (s.kind != r503::ScanOutcome::Kind::PACKET) continue;
                        const Packet& p = s.packet;
                        if (p.pid == r503::PID_COMMAND && !p.content.empty()) {
                            const uint8_t c = static_cast<uint8_t>(p.content[0]);
                            if (c == r503::CMD_VFY_PWD) { ack(0x00); }
                            else if (c == r503::CMD_READ_SYS_PARA) {
                                Bytes q(16, '\0');
                                q[5] = static_cast<char>(200);
                                q[13] = 2; q[15] = 6;
                                ack(0x00, q);
                            } else if (c == r503::CMD_READ_PROD_INFO) {
                                Bytes q(50, '\0');
                                q[42] = 0x06; q[43] = 0x00;
                                q[45] = static_cast<char>(200);
                                ack(0x00, q);
                            } else if (c == r503::CMD_DOWN_CHAR) { ack(0x00); }
                            else { /* UpChar and everything after: silence */ }
                        } else if (p.pid == r503::PID_END) {
                            /* the late complaint the old code threw away */
                            ack(r503::CONF_PACKET_RECV_ERROR);
                        }
                    }
                }
                int read_byte(uint32_t) override {
                    if (tx_.empty()) return -1;
                    int c = static_cast<uint8_t>(tx_.front()); tx_.pop_front(); return c;
                }
                void drain_input() override { tx_.clear(); }
                uint32_t now_ms() override { return clock_++; }
            private:
                void ack(uint8_t conf, const Bytes& params = Bytes()) {
                    Bytes c; c.push_back(static_cast<char>(conf)); c += params;
                    Bytes p = r503::build_packet(r503::kDefaultAddress,
                                                 r503::PID_ACK, c);
                    for (char ch : p) tx_.push_back(ch);
                }
                Bytes rx_; std::deque<char> tx_; uint32_t clock_ = 0;
            };
            LateErrorPort lp;
            RealR503HAL h(&lp);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "late-error: begin() ok");
            R503SyncTestOptions o;
            o.retry_with_empty_end = false;
            R503SyncTestResult x = h.run_sync_test(o);
            TCHECK(!x.pass(), "late-error: synctest fails, as it must");
            TCHECK(!x.post_data_bytes.empty(),
                   "⭐ INSTRUMENT FIX: the module's post-data-phase reply is "
                   "PRESERVED (the previous revision flush()ed it away)");
            TCHECK(x.post_data_ack_parsed &&
                       x.post_data_conf == r503::CONF_PACKET_RECV_ERROR,
                   "⭐ INSTRUMENT FIX: and it is DECODED as 0x01 'error when "
                   "receiving package' — the failure names itself instead of "
                   "degrading to 'UpChar: no ack'");
        }

        /* --- 10g. per-packet TRACE ------------------------------------- */
        {
            FakeR503 f;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "trace: begin() ok");
            R503SyncTestResult x = h.run_sync_test(1);
            TCHECK(x.trace.size() == 12, "trace: one row per packet sent");
            TCHECK(x.trace[0].pid == r503::PID_DATA &&
                       x.trace[0].length_field == 0x0082 &&
                       x.trace[0].content_bytes == 128,
                   "trace: row 0 reports PID 0x02, length 0x0082, 128 content bytes");
            TCHECK(x.trace.back().pid == r503::PID_END,
                   "trace: the final row reports PID 0x08");
            bool quiet = true;
            for (const R503DataPacketTrace& t : x.trace)
                if (t.bytes_back) quiet = false;
            TCHECK(quiet,
                   "trace: a healthy module says NOTHING during the data phase "
                   "— so any non-zero bytes_back on the bench is the finding");
            TCHECK(x.alive_after,
                   "trace: the part is still answering after a clean synctest");
        }

        /* --- 10h. inter-packet pacing is a real, observable knob -------- */
        {
            class CountingPort final : public FakeR503 {
            public:
                size_t sleeps = 0;
                uint32_t slept_ms = 0;
                void sleep_ms(uint32_t ms) override { sleeps++; slept_ms += ms; }
            };
            CountingPort cp;
            RealR503HAL h(&cp);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "pacing: begin() ok");
            R503SyncTestOptions o;
            o.inter_packet_delay_ms = 5;
            R503SyncTestResult x = h.run_sync_test(o);
            TCHECK(x.pass(), "pacing: the transfer still passes with a delay");
            TCHECK(cp.sleeps == 12 && cp.slept_ms == 60,
                   "pacing: the delay is REALLY applied between every packet "
                   "(12 x 5ms), not silently ignored");
            TCHECK(x.inter_packet_delay_ms == 5,
                   "pacing: the result records the delay it ran with");
            CountingPort cp0;
            RealR503HAL h0(&cp0);
            FingerprintHALFailure e0;
            TCHECK(h0.begin(e0), "pacing/0: begin ok");
            (void)h0.run_sync_test(1);
            TCHECK(cp0.sleeps == 0,
                   "pacing: delay 0 means NO sleep at all — the bench's "
                   "original back-to-back behaviour, unchanged by default");
        }

        /* --- 10i. ⭐ r503 extract — THE DISCRIMINATOR ------------------- */
        {
            /* The bench's own fixture: a real leftover template in slot 1. */
            FakeR503 f;
            const Bytes stored = ramp(1536);
            f.preload_slot(1, stored);
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "extract: begin() ok");

            R503ExtractResult x = h.run_extract(1);
            TCHECK(x.pass(), "⭐ extract: LoadChar + UpChar returns the template");
            TCHECK(x.load_acked && x.load_conf == r503::CONF_OK,
                   "extract: LoadChar acked 0x00");
            TCHECK(x.up_acked && x.up_conf == r503::CONF_OK,
                   "extract: UpChar acked 0x00");
            TCHECK(x.bytes == 1536 && x.packets == 12,
                   "extract: 1536 bytes across 12 packets");
            TCHECK(x.stream_complete, "extract: the stream terminated");
            TCHECK(x.head == stored.substr(0, 8),
                   "extract: head-8 matches the stored template");
            TCHECK(x.tail == stored.substr(stored.size() - 8),
                   "extract: tail-8 matches the stored template");

            /* an empty slot must REFUSE, not return garbage */
            R503ExtractResult y = h.run_extract(7);
            TCHECK(!y.pass() && y.load_conf == r503::CONF_INVALID_TEMPLATE,
                   "NEGATIVE/extract: an empty slot refuses LoadChar with 0x0C");
            TCHECK(y.failure.find("no valid template") != std::string::npos,
                   "NEGATIVE/extract: and the failure says so plainly");
            R503ExtractResult z = h.run_extract(9999);
            TCHECK(!z.pass() && z.failure.find("out of range") != std::string::npos,
                   "NEGATIVE/extract: a slot beyond the library is refused "
                   "before it reaches the wire");
        }

        /* --- 10j. ⭐ extract's DECISIVE-NEGATIVE reading ----------------
         * If UpChar is silent with NO DownChar anywhere in the run, that is
         * real evidence against UpChar itself — and the failure string has
         * to SAY that, because it is the reading that would change the
         * architecture. */
        {
            FakeR503 f;
            f.preload_slot(1, ramp(1536));
            f.refuse_upchar = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "extract/refused: begin() ok");
            R503ExtractResult x = h.run_extract(1);
            TCHECK(!x.pass() && x.up_conf == r503::CONF_UPLOAD_FAIL,
                   "extract: an UpChar REFUSAL is captured verbatim");
            TCHECK(x.failure.find("REFUSAL, which is an answer") != std::string::npos,
                   "⭐ extract: a refusal is explicitly distinguished from "
                   "silence — they mean different things and the report says so");
        }

        /* --- 10k. collect_unsolicited OBSERVES without CONSUMING -------- */
        {
            FakeR503 f;
            R503Link link(&f);
            /* provoke a reply, then peek at it twice */
            link.send(r503::cmd_verify_password(A, 0));
            Bytes seen;
            const size_t n = link.collect_unsolicited(seen, 5);
            TCHECK(n == 12, "collect_unsolicited: captured the whole 12-byte ack");
            TCHECK(seen[0] == static_cast<char>(0xEF),
                   "collect_unsolicited: raw bytes, header first");
            /* the bytes must STILL be framable — observing is not consuming */
            Packet p;
            TCHECK(link.recv_packet(p, 5) && p.pid == r503::PID_ACK,
                   "⭐ collect_unsolicited: the packet is STILL there afterwards "
                   "— evidence is observed, never eaten");
        }

        /* --- 10l. unwedge ---------------------------------------------- */
        {
            FakeR503 f; f.strict_separate_end = true;
            RealR503HAL h(&f);
            FingerprintHALFailure e;
            TCHECK(h.begin(e), "unwedge: begin() ok");
            R503SyncTestOptions o;
            o.retry_with_empty_end = false;
            (void)h.run_sync_test(o);          /* leaves the module wedged */
            TCHECK(f.receiving(), "unwedge: the module really is stuck receiving");
            Bytes vol;
            TCHECK(h.unwedge(vol), "⭐ unwedge: an empty end packet frees it");
            TCHECK(!f.receiving(), "unwedge: the receive phase actually ended");
            TCHECK(h.alive(), "unwedge: the module answers commands again");
        }
    }

    /* =================================================================
     * 9. THE EYEBALL ROWS — printed for the architect to check against
     *    the manual before any of this touches silicon.
     * ================================================================= */
    {
        printf("\n   --- BYTES THE ARCHITECT MUST CHECK AGAINST THE MANUAL ---\n");
        printf("   DownChar cmd  (V1.4.1 p.20, extract:1049-1068):\n     %s\n",
               r503::hex(r503::cmd_down_char(A, 1)).c_str());
        printf("   UpChar   cmd  (V1.4.1 p.20, extract:1011-1030):\n     %s\n",
               r503::hex(r503::cmd_up_char(A, 1)).c_str());
        Bytes ramp;
        for (size_t i = 0; i < 1536; i++)
            ramp.push_back(static_cast<char>((i * 7 + 3) & 0xFF));
        std::vector<Bytes> pkts = r503::build_data_packets(A, ramp, 128);
        printf("   DATA packet #0 of %u (PID 0x02, N=128, length 0x0082):\n"
               "     %s\n",
               static_cast<unsigned>(pkts.size()),
               r503::hex(pkts[0], 16).c_str());
        printf("   LAST packet #%u (PID 0x08 'end packet'):\n     %s\n",
               static_cast<unsigned>(pkts.size() - 1),
               r503::hex(pkts.back(), 16).c_str());
        printf("   ReadSysPara   : %s\n", r503::hex(r503::cmd_read_sys_para(A)).c_str());
        printf("   ReadProdInfo  : %s\n", r503::hex(r503::cmd_read_prod_info(A)).c_str());
        printf("   AuraLED cyan  : %s   (== the manual's worked example)\n",
               r503::hex(r503::cmd_aura_led(A, r503::AURA_BREATHING, 0x50,
                                            r503::AURA_CYAN, 0)).c_str());
        /* the length field of a full data packet, spelled out */
        const uint16_t lf =
            static_cast<uint16_t>((static_cast<uint8_t>(pkts[0][7]) << 8) |
                                  static_cast<uint8_t>(pkts[0][8]));
        printf("   data packet length field = 0x%04X == N(128) + 2  [Length "
               "COUNTS the checksum]\n", lf);
        TCHECK(lf == 0x0082, "a full 128-byte data packet's length field is 0x0082");
    }

    printf("\n   [r503] %d checks, %d failed%s\n", t_checks, t_fail,
           t_fail == 0 ? "  — MACHINE-GREEN (host). GLASS still PENDING." : "");

    if (out_checks) *out_checks = t_checks;
    return t_fail;
}
