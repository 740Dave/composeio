/*
 * real_r503_hal.h — RealR503HAL: the R503 backend behind [SPEC:CXC-FPHAL].
 *
 * Sits under the SAME FingerprintHAL contract MockFingerprintHAL satisfies,
 * so FingerprintDevice (fingerprint_device_canonical.cpp) runs UNCHANGED
 * against it — that is the whole point of the seam ([CXC-FPHAL-SEAM]).
 *
 * THE CUT ([DX-ENGINE-VS-POLICY]):
 *   r503_protocol.{h,cpp}   pure bytes.  No I/O.  Host-tested.
 *   R503Port (this file)    the ONLY thing that touches a UART.  Abstract,
 *                           so the whole driver — including the multi-packet
 *                           synctest — is host-testable against a fake port.
 *   R503Link                framing + timeouts + resync over a Port.
 *   RealR503HAL             the ten HAL operations, in R503 verbs.
 *
 * Only ArduinoR503Port is compiled behind #ifdef ESP_PLATFORM.  Everything
 * else links and runs in env:native_test.
 *
 * ⛔ MACHINE-GREEN != GLASS-GREEN.  Host tests prove this code frames,
 * sequences and reassembles correctly against a port that behaves as the
 * datasheet says.  They CANNOT prove the physical module behaves that way.
 * `r503 synctest` on real silicon is the instrument for that, and it is the
 * one thing this worktree cannot run.
 *
 * BENCH FACT, not a datasheet fact (this session, 2026-08-20): a real
 * VerifyPassword ACK came back on UART1 GPIO4(tx)/GPIO5(rx) @ 57600 8N1 —
 * EF 01 FF FF FF FF 07 00 03 00 00 0A (confirmation 0x00).  The pin choice
 * below is that observation, not a ratified decision; the ratified decision
 * ([DEC-R503-AND-FOUR-TM1637-ON-ONE-ESP32-S3]) fixes only that the R503
 * costs TX + RX + WAKEUP = 3 GPIO on the S3 SuperMini's EDGE HEADER, and
 * that WAKEUP/3.3VT must tie to 3V3 (it tracks 3.3VT, not VCC, so a 5 V
 * 3.3VT overdrives the GPIO — [CXC-R503-WAKEUP-TRACKS-3V3T]).
 */
#ifndef REAL_R503_HAL_H
#define REAL_R503_HAL_H

#include "fingerprint_hal.h"
#include "r503_protocol.h"

#include <cstdint>
#include <string>
#include <vector>

/* --- build-flag configuration (same convention as every other pin in this
 * tree: a #ifndef default, overridable from platformio.ini). ------------- */
#ifndef R503_UART_NUM
#define R503_UART_NUM 1            /* HardwareSerial(1) */
#endif
#ifndef R503_TX_PIN
#define R503_TX_PIN 4              /* ESP32-S3 GPIO4 -> module RXD */
#endif
#ifndef R503_RX_PIN
#define R503_RX_PIN 5              /* ESP32-S3 GPIO5 <- module TXD */
#endif
#ifndef R503_BAUD
#define R503_BAUD 57600            /* module default: 9600 * N, N=6 */
#endif

/* --- the port seam ------------------------------------------------------- */
class R503Port {
public:
    virtual ~R503Port() = default;
    virtual void write(const r503::Bytes& b) = 0;
    /* -1 on timeout, else 0..255.  A timeout_ms of 0 must return IMMEDIATELY
     * — that is how the link polls without blocking. */
    virtual int read_byte(uint32_t timeout_ms) = 0;
    virtual void drain_input() = 0;
    virtual uint32_t now_ms() = 0;
    /* Real time, for inter-packet pacing.  A fake port has no reason to
     * actually sleep, so the default is a no-op rather than pure virtual. */
    virtual void sleep_ms(uint32_t) {}
};

/* --- link diagnostics ----------------------------------------------------
 * An instrument that can only report success is not an instrument
 * ([CXC-VISION-EPISTEMICS]).  Every anomaly the framer swallows is counted
 * and printed by `r503 info` / `r503 synctest`, so "it worked" and "it
 * worked after discarding 41 bytes of garbage" are distinguishable. */
struct R503LinkDiag {
    uint32_t packets_ok      = 0;
    uint32_t stray_bytes     = 0;   /* pre-header garbage (the 0x55 lands here) */
    uint32_t bad_checksum    = 0;
    uint32_t bad_length      = 0;
    uint32_t timeouts        = 0;
    uint32_t bytes_rx        = 0;
    uint32_t bytes_tx        = 0;
    void reset() { *this = R503LinkDiag(); }
};

/* --- the link ------------------------------------------------------------ */
class R503Link {
public:
    R503Link(R503Port* port, uint32_t address = r503::kDefaultAddress)
        : port_(port), address_(address) {}

    void set_address(uint32_t a) { address_ = a; }
    uint32_t address() const { return address_; }
    R503LinkDiag& diag() { return diag_; }
    const R503LinkDiag& diag() const { return diag_; }

    void send(const r503::Bytes& raw);

    /* Read the next well-formed packet whose address matches (or, when
     * `any_address` is set, whose address is anything — needed exactly once,
     * during discovery, before the bound address is known). */
    bool recv_packet(r503::Packet& out, uint32_t timeout_ms,
                     bool any_address = false);

    /* Send a command packet and read exactly one ACK. */
    bool command(const r503::Bytes& cmd_packet, r503::Ack& ack,
                 uint32_t timeout_ms = 1000);

    /* Read the ack that FOLLOWS a data stream request, without consuming the
     * stream (UpChar's "ack first, then several data packets"). */
    bool recv_data_stream(r503::Bytes& out, size_t max_bytes,
                          uint32_t timeout_ms, size_t& packets_out);

    /* ⛔ THE EVIDENCE-PRESERVING REPLACEMENT FOR flush().
     * Collect whatever the module has volunteered, RAW, within `window_ms`
     * of quiet, and hand it back instead of dropping it on the floor.
     *
     * This exists because of a defect in THIS FILE, found by the bench on
     * 2026-08-20: run_sync_test() called flush() immediately after the
     * DownChar data phase.  If the module had answered that phase — with an
     * 0x01 "error when receiving package", say, or a late ack — flush()
     * ATE IT, and the run reported the far less informative "UpChar: no
     * ack".  An instrument that discards the one packet that would explain
     * the failure is not an instrument ([CXC-VISION-EPISTEMICS]); it is a
     * machine for producing confident mysteries.
     *
     * Returns the number of bytes collected.  Bytes land in `raw` AND in the
     * link's own rx_ buffer, so a subsequent recv_packet() still sees them —
     * observing must not consume. */
    size_t collect_unsolicited(r503::Bytes& raw, uint32_t window_ms);

    /* Pacing between data packets of a stream.  0 == send back to back (the
     * bench's original behaviour).  A knob rather than a baked constant
     * because the right value is a MEASUREMENT nobody has taken yet. */
    void set_inter_packet_delay_ms(uint32_t ms) { inter_packet_delay_ms_ = ms; }
    uint32_t inter_packet_delay_ms() const { return inter_packet_delay_ms_; }

    /* Send an already-built data-packet stream, pacing it, and note after
     * each packet whether the module said anything back (it should not —
     * anything it DOES say mid-stream is the error report we were losing).
     *
     * `volunteered` accumulates EVERY such byte across the whole stream.  It
     * is not optional bookkeeping: the per-packet peek necessarily REMOVES
     * those bytes from the port, so without this the post-phase listen would
     * find an empty port and the evidence would be lost a second way — a
     * subtler repeat of the very defect this replaced. */
    void send_data_stream(const std::vector<r503::Bytes>& pkts,
                          std::vector<size_t>* bytes_back_per_packet,
                          r503::Bytes* volunteered = nullptr);

    /* ⛔ DESTRUCTIVE ONLY OF PENDING INPUT — kept for the paths that
     * legitimately want a clean slate BEFORE issuing a command.  Never call
     * it where the pending bytes might be evidence. */
    void flush() { if (port_) port_->drain_input(); rx_.clear(); }

private:
    R503Port* port_ = nullptr;
    uint32_t address_ = r503::kDefaultAddress;
    r503::Bytes rx_;
    R503LinkDiag diag_;
    uint32_t inter_packet_delay_ms_ = 0;
};

/* --- per-data-packet trace ----------------------------------------------
 * So the architect can SEE where a real transfer diverges, rather than
 * receiving a final verdict and a shrug.  One row per packet actually put on
 * the wire. */
struct R503DataPacketTrace {
    size_t   index         = 0;
    uint8_t  pid           = 0;
    uint16_t length_field  = 0;
    size_t   content_bytes = 0;
    r503::Bytes head;            /* first few content bytes */
    size_t   bytes_back    = 0;  /* what the module volunteered after it */
};

/* --- `r503 extract` — THE DISCRIMINATOR ---------------------------------
 * LoadChar (0x07) a STORED template into CharBuffer1, then UpChar (0x08) it
 * out.  Zero DownChar involvement.
 *
 * WHY THIS SETTLES THE QUESTION.  The bench saw DownChar ACK 0x00 and then
 * UpChar go silent.  Two families of cause produce that exact signature and
 * they demand opposite responses:
 *   (a) the module is fine at UpChar, and our DOWNCHAR DATA PHASE left it
 *       wedged mid-receive so the UpChar bytes were eaten as data;
 *   (b) UpChar is genuinely broken on this firmware.
 * Running UpChar with no DownChar anywhere near it separates them.  If a
 * stored template comes out, (a) is true and the architecture is intact —
 * the bug is ours, in the data phase.  If this ALSO goes silent, (b) is
 * live and the push-templates-over-the-bus design needs rethinking.
 *
 * The bench handed us the fixture for free: `r503 info` reported occupancy
 * of one, slot 1 — a REAL leftover template from this module's past life.
 * No enrollment required. */
struct R503ExtractResult {
    bool     ran             = false;
    Slot     slot            = -1;
    bool     load_acked      = false;
    uint8_t  load_conf       = 0xFF;
    bool     up_acked        = false;
    uint8_t  up_conf         = 0xFF;
    bool     stream_complete = false;
    size_t   bytes           = 0;
    size_t   packets         = 0;
    size_t   expected_bytes  = 0;   /* 0 when the size is not yet known */
    r503::Bytes head, tail;
    r503::Bytes loadchar_cmd_bytes, upchar_cmd_bytes;
    r503::Bytes unsolicited;        /* anything volunteered, NOT discarded */
    std::string failure;
    R503LinkDiag diag;

    bool pass() const { return stream_complete && bytes > 0; }
};

/* --- synctest options — the bench sweep ---------------------------------- */
struct R503SyncTestOptions {
    uint8_t  buffer_id             = 1;
    r503::EndPacketStyle style     = r503::EndPacketStyle::LAST_DATA_IS_END;
    uint32_t inter_packet_delay_ms = 0;
    /* On an UpChar timeout, send a bare empty PID 0x08 and retry ONCE.  If
     * that rescues the transfer, the module was stuck waiting for more data
     * — which is a DIAGNOSIS, not merely a recovery. */
    bool     retry_with_empty_end  = true;
    /* Quiet window to listen for anything the module volunteers after the
     * data phase, instead of flushing it away. */
    uint32_t post_data_listen_ms   = 400;
};

/* --- the de-risk instrument --------------------------------------------- */
/* Everything `r503 synctest` learned, in a struct so the serial command is a
 * printer and the logic is testable. */
struct R503SyncTestResult {
    bool   ran                 = false;
    bool   sys_para_ok         = false;
    bool   prod_info_ok        = false;
    bool   downchar_acked      = false;
    bool   upchar_acked        = false;
    bool   stream_complete     = false;
    bool   byte_exact          = false;

    size_t packet_content_len  = 0;   /* from ReadSysPara, NEVER hardcoded  */
    size_t template_size       = 0;   /* from ReadProdInfo, else measured   */
    const char* size_source    = "none";
    size_t sent_len            = 0;
    size_t recv_len            = 0;
    size_t data_packets_sent   = 0;
    size_t data_packets_recv   = 0;
    size_t first_diff_index    = 0;   /* valid iff !byte_exact && lengths>0 */
    uint8_t down_conf          = 0xFF;
    uint8_t up_conf            = 0xFF;

    r503::Bytes sent_head, sent_tail, recv_head, recv_tail;
    r503::Bytes downchar_cmd_bytes, upchar_cmd_bytes, sample_data_packet;

    /* --- what the 2026-08-20 bench run could not tell us ----------------- */
    std::vector<R503DataPacketTrace> trace;   /* one row per packet sent    */
    r503::Bytes post_data_bytes;              /* volunteered after the data
                                               * phase — PRESERVED, not
                                               * flushed.  This is the fix
                                               * for the instrument defect. */
    bool     post_data_ack_parsed  = false;
    uint8_t  post_data_conf        = 0xFF;
    bool     retried_with_empty_end = false;
    bool     rescued_by_empty_end  = false;
    bool     alive_after           = false;   /* ReadSysPara still answers?  */
    r503::EndPacketStyle style     = r503::EndPacketStyle::LAST_DATA_IS_END;
    uint32_t inter_packet_delay_ms = 0;

    std::string failure;              /* empty on PASS */
    R503LinkDiag diag;

    bool pass() const { return byte_exact && sent_len > 0; }
};

/* --- RealR503HAL --------------------------------------------------------- */
class RealR503HAL final : public FingerprintHAL {
public:
    RealR503HAL(R503Port* port, uint32_t address = r503::kDefaultAddress)
        : link_(port, address) {}

    /* Handshake + capability discovery.  MUST be called before the HAL ops;
     * until it succeeds, capabilities() reports zeroes and every fallible op
     * fails on TRANSPORT rather than pretending. */
    bool begin(FingerprintHALFailure& err);
    bool ready() const { return ready_; }

    /* ---- [CXC-FPHAL-OPERATIONS] the ten operations ---- */
    Capabilities capabilities() override;
    MatchResult match() override;
    EnrollmentSession begin_enroll(Ordinal ordinal) override;
    bool store_template(const TemplateBytes& t, Slot& out_slot,
                        FingerprintHALFailure& err) override;
    bool read_template(Slot slot, TemplateBytes& out,
                       FingerprintHALFailure& err) override;
    bool empty(FingerprintHALFailure& err) override;
    std::set<Slot> read_occupancy() override;
    void write_roster_stamp(const RosterStamp& stamp) override;
    std::optional<RosterStamp> read_roster_stamp() override;
    /* raw AuraLedConfig for empirical colour characterization (design session). */
    bool aura_raw(uint8_t ctrl, uint8_t speed, uint8_t colour, uint8_t count,
                  FingerprintHALFailure& err);
    bool command_feedback(FeedbackState state, const FpProgress& progress,
                          FingerprintHALFailure& err) override;

    /* ---- the R503-NATIVE enrollment ceremony ----------------------------
     * ⛔ DO NOT drive a real enrollment through the inherited
     * EnrollmentSession::capture().  That function lives in mock_r503_hal.cpp
     * and is the MOCK's ceremony: it SYNTHESIZES template bytes and hands
     * them to store_template().  Against silicon that would DownChar
     * synthetic bytes into the sensor and Store them as if they were a
     * finger — a well-formed, entirely wrong result, which is worse than a
     * failure.  begin_enroll() below therefore returns a session that
     * records the ordinal and the capture count for the caller's bookkeeping,
     * and the REAL ceremony is these three calls:
     *
     *   enroll_capture(n, err)   GenImg (until a finger) -> Img2Tz(buffer n)
     *   enroll_combine(err)      RegModel  (CharBuffer1+2 -> a template)
     *   enroll_store(slot, err)  Store     (template -> flash page `slot`)
     *
     * Sourced from §6.2.1 "General instruction register fingerprint process"
     * (extract:1774+) and the GenChar/RegModel/Store tables. */
    /* One GenImg, reported as a predicate.  Touches ImageBuffer only — it
     * does NOT disturb CharBuffer1/2, so it is safe to poll between the two
     * captures of an enrollment to detect that the finger has been LIFTED. */
    bool finger_present();
    bool enroll_capture(int capture_index, FingerprintHALFailure& err);
    bool enroll_combine(FingerprintHALFailure& err);
    bool enroll_store(Slot slot, FingerprintHALFailure& err);

    /* ---- the de-risk instruments ---- */
    R503SyncTestResult run_sync_test(const R503SyncTestOptions& opt);
    R503SyncTestResult run_sync_test(uint8_t buffer_id = 1) {
        R503SyncTestOptions o;
        o.buffer_id = buffer_id;
        return run_sync_test(o);
    }
    /* THE DISCRIMINATOR — UpChar with no DownChar anywhere near it. */
    R503ExtractResult run_extract(Slot slot, uint8_t buffer_id = 1);

    /* Send a bare empty PID 0x08, then probe with ReadSysPara.  If the
     * module was stuck mid-DownChar this ends that phase and the probe
     * answers; if the probe still fails, it was stuck for another reason.
     * Returns true when the link answers afterwards. */
    bool unwedge(r503::Bytes& volunteered);
    /* A cheap "is the module still talking?" probe — ReadSysPara, discarded. */
    bool alive();

    /* ---- introspection for `r503 info` ---- */
    const r503::SysPara&  sys_para()  const { return sys_; }
    const r503::ProdInfo& prod_info() const { return prod_; }
    bool prod_info_valid() const { return prod_ok_; }
    R503LinkDiag& diag() { return link_.diag(); }
    R503Link& link() { return link_; }
    Slot first_free_slot();

private:
    bool cmd_ok(const r503::Bytes& pkt, r503::Ack& ack,
                FingerprintHALFailure& err, const char* what,
                uint32_t timeout_ms = 1000);
    /* Map a documented confirmation code onto the closed six-axis taxonomy
     * ([CXC-FPHAL-FAILURE-AXES]).  An UNDOCUMENTED code becomes UNKNOWN_CODE
     * carrying the raw byte — it must never be laundered into a refusal. */
    static FingerprintHALFailure axis_for(uint8_t conf, const char* what);

    /* DownChar the bytes into `buffer_id`; does NOT Store. */
    bool down_char(uint8_t buffer_id, const TemplateBytes& t,
                   FingerprintHALFailure& err);
    /* UpChar out of `buffer_id`. */
    bool up_char(uint8_t buffer_id, TemplateBytes& out,
                 FingerprintHALFailure& err);

    R503Link link_;
    r503::SysPara  sys_;
    r503::ProdInfo prod_;
    bool ready_   = false;
    bool prod_ok_ = false;
    /* Template size: ReadProdInfo if the part answers 0x3C, else the length
     * MEASURED by the first successful UpChar, else 0 == UNKNOWN.  0 is a
     * deliberate sentinel: a fabricated 1536 would be indistinguishable from
     * a measured one ([DX-SPEC-CARRIES-ITS-EVIDENCE]). */
    size_t template_size_ = 0;
    const char* template_size_source_ = "unknown";
    Ordinal enroll_ordinal_ = 0;

public:
    size_t template_size() const { return template_size_; }
    const char* template_size_source() const { return template_size_source_; }
    size_t packet_content_len() const { return sys_.packet_content_len(); }
};

/* --- the Arduino port, the ONLY silicon-touching code here -------------- */
#ifdef ESP_PLATFORM
class ArduinoR503Port final : public R503Port {
public:
    /* Opens HardwareSerial(R503_UART_NUM) at `baud` on the configured pins. */
    bool begin(uint32_t baud = R503_BAUD, int tx_pin = R503_TX_PIN,
               int rx_pin = R503_RX_PIN);
    void write(const r503::Bytes& b) override;
    int read_byte(uint32_t timeout_ms) override;
    void drain_input() override;
    uint32_t now_ms() override;
    void sleep_ms(uint32_t ms) override;
};
#endif /* ESP_PLATFORM */

#endif /* REAL_R503_HAL_H */
