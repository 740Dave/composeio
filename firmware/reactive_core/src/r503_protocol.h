/*
 * r503_protocol.h — the R503 UART wire protocol, as PURE FUNCTIONS.
 * [SPEC:CXC-R503] [CXC-R503-FRAMING] [CXC-R503-CHECKSUM]
 *
 * Every builder and every parser in this file is pure: no Arduino, no
 * ESP-IDF, no I/O, no globals.  That is deliberate — the multi-packet
 * DownChar/UpChar transfer is the riskiest thing in the whole driver, and
 * it is the one part that CAN be proven on a host before any silicon is
 * involved.  See r503_protocol_tests() in src/hosttest/.
 *
 * ⛔ MACHINE-GREEN IS NOT GLASS-GREEN.  Everything here proves that THIS
 * CODE frames and reassembles correctly.  Whether the PHYSICAL module's
 * firmware honours DownChar/UpChar at all is a question only the bench can
 * answer — that is what `r503 synctest` exists to close.
 *
 * SOURCE OF EVERY FIELD, cited per builder below:
 *   R503_R503-M22_user_manual_V1.4.1_2023-09.pdf
 *   (plaintext extract: Data Sheets/R503/extracted-text/
 *    R503_V1.4.1_pdftotext-layout.txt — line numbers cited as "extract:NNNN")
 *   §V Module Instruction System, pp. 10-27.
 * No field in this file was invented.
 */
#ifndef R503_PROTOCOL_H
#define R503_PROTOCOL_H

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace r503 {

using Bytes = std::string;   /* byte container; matches TemplateBytes */

/* --- [CXC-R503-FRAMING] the frame ---------------------------------------
 *   header(2) | address(4) | PID(1) | length(2) | content(N) | checksum(2)
 * Big-endian throughout.  Length COUNTS THE CHECKSUM: length == N + 2.
 * (extract:580-596, the VfyPwd command/ack tables, and every table after.) */
constexpr uint8_t  kHeaderHi = 0xEF;
constexpr uint8_t  kHeaderLo = 0x01;
constexpr uint32_t kDefaultAddress  = 0xFFFFFFFFu;  /* factory default */
constexpr uint32_t kDefaultPassword = 0x00000000u;  /* factory default */

/* Package identifiers (extract:1038-1047 "Data package format"). */
constexpr uint8_t PID_COMMAND = 0x01;
constexpr uint8_t PID_DATA    = 0x02;   /* "have following packet"          */
constexpr uint8_t PID_ACK     = 0x07;
constexpr uint8_t PID_END     = 0x08;   /* "end packet" — LAST data packet  */

/* Instruction codes — each cited to its section in the V1.4.1 manual. */
constexpr uint8_t CMD_GEN_IMG          = 0x01; /* GetImg      extract:861  */
constexpr uint8_t CMD_IMG2TZ           = 0x02; /* GenChar     extract:957  */
constexpr uint8_t CMD_MATCH_1TO1       = 0x03; /* Match       extract:1194 */
constexpr uint8_t CMD_SEARCH           = 0x04; /* Search      extract:1215 */
constexpr uint8_t CMD_REG_MODEL        = 0x05; /* RegModel    extract:988  */
constexpr uint8_t CMD_STORE            = 0x06; /* Store       extract:1087 */
constexpr uint8_t CMD_LOAD_CHAR        = 0x07; /* LoadChar    extract:1116 */
constexpr uint8_t CMD_UP_CHAR          = 0x08; /* UpChar      extract:1011 */
constexpr uint8_t CMD_DOWN_CHAR        = 0x09; /* DownChar    extract:1049 */
constexpr uint8_t CMD_DELETE_CHAR      = 0x0C; /* DeletChar   extract:1143 */
constexpr uint8_t CMD_EMPTY            = 0x0D; /* Empty       extract:1168 */
constexpr uint8_t CMD_SET_SYS_PARA     = 0x0E; /* SetSysPara  extract:643  */
constexpr uint8_t CMD_READ_SYS_PARA    = 0x0F; /* ReadSysPara extract:679  */
constexpr uint8_t CMD_VFY_PWD          = 0x13; /* VfyPwd      extract:580  */
constexpr uint8_t CMD_TEMPLATE_NUM     = 0x1D; /* TempleteNum extract:711  */
constexpr uint8_t CMD_READ_INDEX_TABLE = 0x1F; /* ReadIndexTable extract:734 */
constexpr uint8_t CMD_GET_IMAGE_EX     = 0x28; /* GetImageEx  extract:1245 */
constexpr uint8_t CMD_CANCEL           = 0x30; /* Cancel      extract:1279 */
constexpr uint8_t CMD_AURA_LED         = 0x35; /* AuraLedConfig extract:1381 */
constexpr uint8_t CMD_WRITE_NOTEPAD    = 0x18; /* WriteNotepad extract:1719 */
constexpr uint8_t CMD_READ_NOTEPAD     = 0x19; /* ReadNotepad  extract:1746 */
constexpr uint8_t CMD_READ_PROD_INFO   = 0x3C; /* ReadProdInfo extract:820  */

/* Notepad geometry (extract:325-328): "The system sets aside a 512-bytes
 * memory (16 pages * 32 bytes) for user's notepad".  This is where the
 * roster stamp lives — see [CXC-FPID-ROSTER-STAMP] and the note on
 * RealR503HAL::write_roster_stamp. */
constexpr uint8_t kNotepadPages    = 16;
constexpr size_t  kNotepadPageSize = 32;

/* Confirmation codes (extract:525-570, the master list). Only the ones the
 * driver branches on are named; the rest ride through as raw codes so an
 * undocumented value stays visible ([CXC-FPHAL-UNKNOWN-CODE]). */
constexpr uint8_t CONF_OK                 = 0x00;
constexpr uint8_t CONF_PACKET_RECV_ERROR  = 0x01;
constexpr uint8_t CONF_NO_FINGER          = 0x02;
constexpr uint8_t CONF_IMAGE_FAIL         = 0x03;
constexpr uint8_t CONF_IMAGE_MESSY        = 0x06;
constexpr uint8_t CONF_FEATURE_FAIL       = 0x07;
constexpr uint8_t CONF_NO_MATCH           = 0x08;
constexpr uint8_t CONF_NOT_FOUND          = 0x09;
constexpr uint8_t CONF_ENROLL_MISMATCH    = 0x0A;
constexpr uint8_t CONF_BAD_LOCATION       = 0x0B;
constexpr uint8_t CONF_INVALID_TEMPLATE   = 0x0C;
constexpr uint8_t CONF_UPLOAD_FAIL        = 0x0D;  /* UpChar refused        */
constexpr uint8_t CONF_CANNOT_RECEIVE     = 0x0E;  /* DownChar refused      */
constexpr uint8_t CONF_UPLOAD_IMG_FAIL    = 0x0F;
constexpr uint8_t CONF_DELETE_FAIL        = 0x10;
constexpr uint8_t CONF_CLEAR_FAIL         = 0x11;
constexpr uint8_t CONF_WRONG_PASSWORD     = 0x13;
constexpr uint8_t CONF_FLASH_WRITE_ERR    = 0x18;
constexpr uint8_t CONF_BAD_ADDRESS        = 0x20;
constexpr uint8_t CONF_MUST_VERIFY_PWD    = 0x21;
constexpr uint8_t CONF_TEMPLATE_EMPTY     = 0x22;
constexpr uint8_t CONF_LIBRARY_EMPTY      = 0x24;
constexpr uint8_t CONF_TIMEOUT            = 0x26;
constexpr uint8_t CONF_LIBRARY_FULL       = 0x1F;
constexpr uint8_t CONF_SENSOR_HW_ERROR    = 0x29;
constexpr uint8_t CONF_UNSUPPORTED_CMD    = 0xFC;
constexpr uint8_t CONF_HARDWARE_ERROR     = 0xFD;
constexpr uint8_t CONF_EXEC_FAILURE       = 0xFE;

/* ⛔ [CXC-R503-UNSOLICITED-0X55] the module emits a BARE UNFRAMED 0x55 at
 * power-on and after every soft reset.  It is not a packet and has no
 * checksum; a parser that does not resync on the 0xEF 0x01 header will
 * mis-frame the packet that follows it.  scan_packet() below skips it (and
 * any other pre-header garbage) and REPORTS how many bytes it discarded —
 * an instrument that silently swallows noise cannot report a real desync. */
constexpr uint8_t kStrayPowerOnByte = 0x55;

/* --- packet-length bounds -----------------------------------------------
 * The largest legal content is a 256-byte data packet (SetSysPara parameter
 * 6, extract:667-676: codes 0/1/2/3 -> 32/64/128/256).  Anything past that
 * is garbage, not a long packet, and must trigger a resync rather than a
 * multi-kilobyte wait. */
constexpr size_t kMaxContentBytes = 256;
constexpr size_t kMaxLengthField  = kMaxContentBytes + 2;

/* --- checksum ------------------------------------------------------------
 * [CXC-R503-CHECKSUM]: the arithmetic sum of the package identifier, BOTH
 * package-length bytes, and every content byte, modulo 2^16.  The header and
 * the address are NOT included.
 *
 * Verified against four checksums the manual states literally:
 *   ReadSysPara  0x01+0x00+0x03+0x0F              = 0x0013 (extract:685)
 *   TempleteNum  0x01+0x00+0x03+0x1D              = 0x0021 (extract:720)
 *   RegModel     0x01+0x00+0x03+0x05              = 0x0009 (extract:1065)
 *   ReadProdInfo 0x01+0x00+0x03+0x3C              = 0x0040 (extract:829)
 * and against the manual's worked AuraLED example (extract:1409):
 *   EF01 FFFFFFFF 01 0007 35 01 50 06 00  -> 0x0094 */
uint16_t checksum(uint8_t pid, uint16_t length_field, const Bytes& content);

/* --- one packet ---------------------------------------------------------- */
struct Packet {
    uint32_t address = 0;
    uint8_t  pid     = 0;
    Bytes    content;            /* WITHOUT the two checksum bytes */
};

/* Serialize header|address|pid|length|content|checksum. */
Bytes build_packet(uint32_t address, uint8_t pid, const Bytes& content);

/* --- the scanner ---------------------------------------------------------
 * A byte-stream framer.  `consumed` is always the number of bytes the caller
 * should erase from the FRONT of `buf`, whatever the outcome — so a caller
 * that honours it can never spin. */
struct ScanOutcome {
    enum class Kind {
        PACKET,        /* a well-formed, checksum-valid packet             */
        NEED_MORE,     /* prefix is a valid partial packet; feed more      */
        BAD_CHECKSUM,  /* fully framed but the checksum did not match      */
        BAD_LENGTH     /* length field out of range -> treated as garbage  */
    };
    Kind   kind     = Kind::NEED_MORE;
    size_t consumed = 0;   /* bytes to drop from the front of buf          */
    size_t skipped  = 0;   /* pre-header garbage discarded (0x55 shows up here) */
    Packet packet;         /* valid iff kind == PACKET                     */
};
ScanOutcome scan_packet(const Bytes& buf);

/* --- acknowledge ---------------------------------------------------------
 * Every ack's content is confirmation(1) + zero or more parameter bytes.
 * (extract:589-596 and every ack table thereafter.) */
struct Ack {
    uint8_t confirmation = 0xFF;
    Bytes   params;
};
bool parse_ack(const Packet& p, Ack& out);

/* --- command builders ---------------------------------------------------
 * Each is one datasheet table, transcribed.  The length field in each
 * comment is the value the manual states, and the test suite asserts the
 * built bytes carry exactly it. */

/* VfyPwd 0x13 — content 0x13 + password(4), length 0x0007. extract:580-592 */
Bytes cmd_verify_password(uint32_t address, uint32_t password);
/* ReadSysPara 0x0F — content 0x0F, length 0x0003, cksum 0x0013. extract:679 */
Bytes cmd_read_sys_para(uint32_t address);
/* ReadProdInfo 0x3C — content 0x3C, length 0x0003, cksum 0x0040. extract:820  */
Bytes cmd_read_prod_info(uint32_t address);
/* TempleteNum 0x1D — content 0x1D, length 0x0003, cksum 0x0021. extract:711 */
Bytes cmd_template_num(uint32_t address);
/* ReadIndexTable 0x1F — content 0x1F + page, length 0x0004. extract:734-748 */
Bytes cmd_read_index_table(uint32_t address, uint8_t page);
/* GetImg 0x01 — content 0x01, length 0x0003, cksum 0x0005. extract:861  */
Bytes cmd_gen_img(uint32_t address);
/* GetImageEx 0x28 — content 0x28, length 0x0003, cksum 0x002C. extract:1245 */
Bytes cmd_get_image_ex(uint32_t address);
/* GenChar 0x02 — content 0x02 + bufferID, length 0x0004. extract:957-975 */
Bytes cmd_img2tz(uint32_t address, uint8_t buffer_id);
/* RegModel 0x05 — content 0x05, length 0x0003, cksum 0x0009. extract:988  */
Bytes cmd_reg_model(uint32_t address);
/* Store 0x06 — content 0x06 + buf + page(2), length 0x0006. extract:1087 */
Bytes cmd_store(uint32_t address, uint8_t buffer_id, uint16_t page_id);
/* LoadChar 0x07 — content 0x07 + buf + page(2), length 0x0006. extract:1116 */
Bytes cmd_load_char(uint32_t address, uint8_t buffer_id, uint16_t page_id);
/* Search 0x04 — content 0x04 + buf + start(2) + num(2), length 0x0008.
 * extract:1215-1232 */
Bytes cmd_search(uint32_t address, uint8_t buffer_id, uint16_t start_id,
                 uint16_t num);
/* DeletChar 0x0C — content 0x0C + start(2) + num(2), length 0x0007.
 * extract:1143-1158 */
Bytes cmd_delete_char(uint32_t address, uint16_t start_id, uint16_t num);
/* Empty 0x0D — content 0x0D, length 0x0003, cksum 0x0011. extract:1168 */
Bytes cmd_empty(uint32_t address);
/* UpChar 0x08 — content 0x08 + bufferID, length 0x0004. extract:1011-1030 */
Bytes cmd_up_char(uint32_t address, uint8_t buffer_id);
/* DownChar 0x09 — content 0x09 + bufferID, length 0x0004. extract:1049-1080 */
Bytes cmd_down_char(uint32_t address, uint8_t buffer_id);
/* AuraLedConfig 0x35 — content 0x35 + ctrl + speed + colour + count,
 * length 0x0007.  extract:1381-1404 */
/* WriteNotepad 0x18 — content 0x18 + page(1) + EXACTLY 32 data bytes,
 * length 0x0024 (extract:1719-1738).  `data` is right-padded with 0x00 and
 * truncated to 32 so the length field is invariant. */
Bytes cmd_write_notepad(uint32_t address, uint8_t page, const Bytes& data);
/* ReadNotepad 0x19 — content 0x19 + page(1), length 0x0004; the ack carries
 * conf(1) + 32 bytes, length 0x0023 (extract:1746-1766). */
Bytes cmd_read_notepad(uint32_t address, uint8_t page);
Bytes cmd_aura_led(uint32_t address, uint8_t ctrl, uint8_t speed,
                   uint8_t colour, uint8_t count);

/* AuraLED control codes and colours (extract:1394-1404). */
constexpr uint8_t AURA_BREATHING   = 0x01;
constexpr uint8_t AURA_FLASHING    = 0x02;
constexpr uint8_t AURA_ON          = 0x03;
constexpr uint8_t AURA_OFF         = 0x04;
constexpr uint8_t AURA_GRADUAL_ON  = 0x05;
constexpr uint8_t AURA_GRADUAL_OFF = 0x06;
constexpr uint8_t AURA_RED    = 0x01;
constexpr uint8_t AURA_BLUE   = 0x02;
constexpr uint8_t AURA_PURPLE = 0x03;
constexpr uint8_t AURA_GREEN  = 0x04;
constexpr uint8_t AURA_YELLOW = 0x05;
constexpr uint8_t AURA_CYAN   = 0x06;
constexpr uint8_t AURA_WHITE  = 0x07;

/* --- THE MULTI-PACKET TRANSFER — the load-bearing part ------------------
 *
 * Datasheet, DownChar §"Data package format" (extract:1073-1082) and UpChar
 * §"Data package format" (extract:1038-1047), identical tables:
 *
 *     header(2) | address(4) | PID(1) | length(2) | content(N) | checksum(2)
 *     PID = 0x02  "have following packet"
 *     PID = 0x08  "end packet"
 *     length      = N + 2
 *
 * N ("Packet Bytes") is the module's configured packet content length —
 * ReadSysPara word 6, SetSysPara parameter 6 — NOT a constant.  The manual
 * says "The value is 128 Bytes before delivery" (extract:1035, 1078), i.e.
 * the factory default is 128, and it is a SETTING.  ⛔ Do not hardcode it;
 * read it (see SysPara::packet_content_len below).
 *
 * ⛔ FOUR AMBIGUITIES IN THIS TABLE, each resolved explicitly here rather
 * than papered over.  Every one is a place the physical part could disagree
 * with this code, so each is called out in the synctest report too.
 *
 * A1. LAST-PACKET SIZE.  The manual never says whether the final packet may
 *     be SHORT.  RESOLVED: it may.  build_data_packets() emits ceil(L/N)
 *     packets, the last carrying L mod N bytes (or N when L divides evenly),
 *     and its length field reflects the ACTUAL content it carries.  Reason:
 *     the length field exists precisely so the receiver need not assume, and
 *     a template length is not guaranteed to be a multiple of a settable N.
 *
 * A2. EXACT-MULTIPLE TERMINATION.  When L is an exact multiple of N, is
 *     there a trailing EMPTY 0x08 packet?  RESOLVED, asymmetrically:
 *     the BUILDER never emits one (the last data-bearing packet carries PID
 *     0x08); the REASSEMBLER TOLERATES one on receive (it contributes zero
 *     bytes and terminates the stream).  Reason: the manual shows no empty
 *     packet anywhere, so emitting one is unjustified — but being strict on
 *     receive would turn a harmless vendor quirk into a hard transfer
 *     failure, and this is a stream we cannot re-request cheaply.
 *
 * A3. CHECKSUM SCOPE ON A DATA PACKET.  The data-packet table names a
 *     "Checksum" column but the manual states the summation rule only once,
 *     for command packets (§4.1).  RESOLVED: identical rule — PID + both
 *     length bytes + content, mod 2^16, header and address excluded.
 *     Reason: the frame is the same frame; [CXC-R503-CHECKSUM] recomputed
 *     that rule against 21 worked examples across packet types and it held
 *     on all 21.
 *
 * A4. WHO SENDS THE DATA ON DownChar.  DownChar's Note 2 (extract:1079)
 *     reads "the module sends the acknowledge packet first, and then sends
 *     several data packet" — copied verbatim from UpChar and WRONG here.
 *     RESOLVED: on DownChar the module ACKs, then the HOST sends the data
 *     packets.  Reason: DownChar's own description is "upper computer
 *     download template to module buffer", and its failure code 0x0E is
 *     "can not receive the following data packet" — the module is the
 *     RECEIVER.  Corroborated by the ack note "00H: ready to transfer the
 *     following data packet".
 *
 * A5 (bonus, and the one synctest actually falsifies).  UpChar's description
 *     says it uploads "the data in the template buffer ModelBuffer", while
 *     its parameter is a CharBufferID that both UpChar and DownChar declare
 *     IGNORED ("This command don't need to use the CharBufferID, so the
 *     CharBufferID can be any value between 1 and 6", extract:1023, 1054).
 *     So the manual does NOT establish that DownChar writes the same buffer
 *     UpChar reads.  That is not resolvable from the document — it is
 *     exactly the hardware question `r503 synctest` answers.
 */

/* ⛔ A2 IS NOW A LIVE HARDWARE QUESTION, NOT A THEORETICAL ONE.
 * Bench, 2026-08-20: DownChar (0x09) was ACKed 0x00 by the real module, all
 * 1536 bytes were sent as 12 packets of 128 with the final packet carrying
 * PID 0x08 — and the UpChar that followed drew TOTAL SILENCE.  Not 0x0D, not
 * 0xFC: no acknowledge packet at all.
 *
 * A refusal would have been an ANSWER.  Silence means the module never
 * parsed those 13 UpChar bytes AS A COMMAND, and the overwhelmingly likely
 * reason is that it was still in its DownChar receive phase and consumed
 * them as data.  Which means the data phase did not TERMINATE for the
 * module — and the only thing that terminates it is the end marker.
 *
 * So the end-packet style stops being an authoring choice and becomes a
 * PARAMETER TO SWEEP ON THE BENCH.  Both styles are built here, both are
 * proven byte-exact through the real scanner in the host suite, and the
 * serial command selects between them — because the document cannot settle
 * this and only the part can. */
enum class EndPacketStyle {
    /* The manual's own framing: the final DATA-BEARING packet carries PID
     * 0x08.  ceil(L/N) packets total.  This is what the bench ran. */
    LAST_DATA_IS_END,
    /* Every data-bearing packet carries PID 0x02, followed by a SEPARATE,
     * EMPTY PID 0x08 packet (content 0, length field 0x0002).  ceil(L/N)+1
     * packets total.  The manual shows no such packet — but a receiver
     * implemented as "absorb 0x02 until a 0x08 arrives" would hang forever
     * on a stream whose last payload packet is itself the 0x08 only if it
     * checks the PID BEFORE appending, and that is exactly the class of
     * off-by-one no document reveals. */
    SEPARATE_EMPTY_END
};

/* Split `data` into a DownChar/UpChar-shaped data-packet stream.
 * Returns an empty vector for empty input or content_len == 0 (a caller
 * must not attempt a zero-length transfer).
 * The default preserves the manual's framing exactly. */
std::vector<Bytes> build_data_packets(
    uint32_t address, const Bytes& data, size_t content_len,
    EndPacketStyle style = EndPacketStyle::LAST_DATA_IS_END);

/* A bare, EMPTY end packet: header|addr|0x08|0x0002|checksum.  Sent on its
 * own it is the ONLY in-band way to tell a module that is still waiting for
 * data that the stream is over — the unwedge primitive, and the thing that
 * distinguishes "stuck mid-receive" from "stuck for some other reason". */
Bytes build_empty_end_packet(uint32_t address);

inline const char* to_string(EndPacketStyle s) {
    return s == EndPacketStyle::SEPARATE_EMPTY_END ? "separate-empty-end"
                                                   : "last-data-is-end";
}

/* Reassemble an UpChar data stream.  Fed Packets (from scan_packet), it
 * accumulates content until it sees PID_END. */
class DataReassembler {
public:
    enum class Feed {
        ACCEPTED,         /* PID_DATA absorbed, more expected              */
        COMPLETE,         /* PID_END absorbed, stream finished             */
        WRONG_PID,        /* not a data packet — a desync, not a data byte */
        ALREADY_COMPLETE, /* fed after termination                          */
        TOO_LARGE         /* would exceed the caller's declared ceiling    */
    };
    explicit DataReassembler(size_t max_bytes) : max_bytes_(max_bytes) {}

    Feed feed(const Packet& p);
    bool complete() const { return complete_; }
    const Bytes& data() const { return data_; }
    size_t packet_count() const { return packets_; }
    void reset() { data_.clear(); packets_ = 0; complete_ = false; }

private:
    Bytes  data_;
    size_t max_bytes_ = 0;
    size_t packets_   = 0;
    bool   complete_  = false;
};

/* --- ReadSysPara decode --------------------------------------------------
 * 16 parameter bytes, big-endian, word-addressed (extract:700-708):
 *   word 0 (2B) status register
 *   word 1 (2B) system identifier, fixed 0x0000
 *   word 2 (2B) finger library size      <- slot_count
 *   word 3 (2B) security level (1..5)
 *   word 4 (4B) device address
 *   word 6 (2B) data packet size CODE (0..3)  <- 32/64/128/256
 *   word 7 (2B) baud setting N (baud = 9600*N)
 *
 * ⛔ NOTE WHAT IS NOT HERE: there is NO template-size field in ReadSysPara.
 * The template size lives in ReadProdInfo (0x3C) as PARAM_TMPL_SIZE — see
 * ProdInfo below.  A driver that expects ReadSysPara to announce a template
 * size is reading a table that does not contain one. */
struct SysPara {
    uint16_t status_register    = 0;
    uint16_t system_identifier  = 0;
    uint16_t finger_library_size = 0;
    uint16_t security_level     = 0;
    uint32_t device_address     = 0;
    uint16_t packet_size_code   = 0;
    uint16_t baud_n             = 0;

    /* SetSysPara parameter 6 (extract:667-676): code 0/1/2/3 -> 32/64/128/256.
     * An out-of-range code falls back to the factory default of 128 rather
     * than to 32: guessing SMALL silently multiplies packet count, and a
     * wrong-but-plausible transfer is worse than a loud one. */
    size_t packet_content_len() const {
        switch (packet_size_code) {
            case 0: return 32;
            case 1: return 64;
            case 2: return 128;
            case 3: return 256;
            default: return 128;
        }
    }
    bool packet_size_code_known() const { return packet_size_code <= 3; }
};
bool parse_sys_para(const Bytes& ack_params, SysPara& out);

/* --- ReadProdInfo decode -------------------------------------------------
 * 50 bytes, big-endian numbers, 0x00-padded ASCII strings (extract:838-852):
 *   [ 0..15] PARAM_FPM_MODEL   16  module type, ASCII
 *   [16..19] PARAM_BN           4  batch number, ASCII
 *   [20..27] PARAM_SN           8  serial number, ASCII
 *   [28..29] PARAM_HW_VER       2  main.sub
 *   [30..37] PARAM_FPS_MODEL    8  sensor type, ASCII
 *   [38..39] PARAM_FPS_WIDTH    2
 *   [40..41] PARAM_FPS_HEIGHT   2
 *   [42..43] PARAM_TMPL_SIZE    2  <- THE template size, in bytes
 *   [44..45] PARAM_TMPL_TOTAL   2  <- fingerprint database size
 *   [46..49] reserved           4
 * The V1.4.1 spec table (extract:186) states 1536 bytes for this part; that
 * figure is a CROSS-CHECK for the value read at runtime, never a default. */
struct ProdInfo {
    std::string module_model;
    std::string batch_number;
    std::string serial_number;
    uint8_t  hw_major = 0;
    uint8_t  hw_minor = 0;
    std::string sensor_model;
    uint16_t sensor_width  = 0;
    uint16_t sensor_height = 0;
    uint16_t template_size = 0;
    uint16_t template_total = 0;
};
bool parse_prod_info(const Bytes& ack_params, ProdInfo& out);

/* --- small ack payload decoders ----------------------------------------- */
/* Search ack: conf(1) + pageID(2) + score(2), length 0x0007 (extract:1237). */
bool parse_search_ack(const Ack& a, uint16_t& page_id, uint16_t& score);
/* TempleteNum ack: conf(1) + N(2), length 0x0005 (extract:723). */
bool parse_template_num_ack(const Ack& a, uint16_t& count);
/* ReadIndexTable ack: conf(1) + 32 bytes bitmap (extract:751).  Bit b of
 * byte i means template (page*256 + i*8 + b) is present — LSB first within
 * the byte, which is the convention every working driver uses and which the
 * manual's own index table (extract:756+) lays out. */
bool parse_index_table_ack(const Ack& a, uint16_t page, std::vector<int>& slots);

/* --- debug helper: hex, for the eyeball rows the architect must check --- */
std::string hex(const Bytes& b, size_t max_bytes = 0);

}  /* namespace r503 */

#endif /* R503_PROTOCOL_H */
