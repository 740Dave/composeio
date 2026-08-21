/*
 * real_r503_hal.cpp — RealR503HAL implementation.  [SPEC:CXC-FPHAL] [CXC-R503]
 *
 * Everything except ArduinoR503Port is portable C++17 and links into
 * env:native_test, so the link framing, the DownChar/UpChar sequencing and
 * the whole synctest are exercised on the host against a fake port.
 */
#include "real_r503_hal.h"

#include <cstdio>

using r503::Ack;
using r503::Bytes;
using r503::Packet;

/* ======================================================================== */
/*  R503Link                                                                */
/* ======================================================================== */

void R503Link::send(const Bytes& raw) {
    if (!port_) return;
    diag_.bytes_tx += static_cast<uint32_t>(raw.size());
    port_->write(raw);
}

bool R503Link::recv_packet(Packet& out, uint32_t timeout_ms, bool any_address) {
    if (!port_) return false;
    const uint32_t deadline = port_->now_ms() + timeout_ms;

    for (;;) {
        /* 1. try to frame something out of what we already hold */
        while (!rx_.empty()) {
            r503::ScanOutcome s = r503::scan_packet(rx_);
            diag_.stray_bytes += static_cast<uint32_t>(s.skipped);
            if (s.kind == r503::ScanOutcome::Kind::NEED_MORE) {
                if (s.consumed) rx_.erase(0, s.consumed);
                break;                       /* need more bytes */
            }
            rx_.erase(0, s.consumed);
            if (s.kind == r503::ScanOutcome::Kind::BAD_CHECKSUM) {
                diag_.bad_checksum++;
                continue;                    /* resync and keep looking */
            }
            if (s.kind == r503::ScanOutcome::Kind::BAD_LENGTH) {
                diag_.bad_length++;
                continue;
            }
            /* PACKET */
            if (!any_address && s.packet.address != address_) {
                /* [CXC-R503-FRAMING]: "MUST reject a packet whose address
                 * does not match the bound device address."  Counted as a
                 * framing anomaly rather than dropped in silence. */
                diag_.bad_length++;
                continue;
            }
            diag_.packets_ok++;
            out = s.packet;
            return true;
        }

        /* 2. pull another byte */
        const uint32_t now = port_->now_ms();
        if (now >= deadline) { diag_.timeouts++; return false; }
        const int c = port_->read_byte(deadline - now);
        if (c < 0) { diag_.timeouts++; return false; }
        rx_.push_back(static_cast<char>(c));
        diag_.bytes_rx++;
    }
}

size_t R503Link::collect_unsolicited(Bytes& raw, uint32_t window_ms) {
    raw.clear();
    if (!port_) return 0;
    /* Quiet-window collection: keep reading while bytes keep arriving, stop
     * once `window_ms` passes with nothing.  Everything collected is ALSO
     * appended to rx_, so a later recv_packet() still sees it — observing
     * must never consume the evidence. */
    for (;;) {
        const int c = port_->read_byte(window_ms);
        if (c < 0) break;
        raw.push_back(static_cast<char>(c));
        diag_.bytes_rx++;
    }
    rx_ += raw;
    return raw.size();
}

void R503Link::send_data_stream(const std::vector<Bytes>& pkts,
                                std::vector<size_t>* bytes_back_per_packet,
                                Bytes* volunteered) {
    if (bytes_back_per_packet) bytes_back_per_packet->clear();
    if (volunteered) volunteered->clear();
    for (size_t i = 0; i < pkts.size(); i++) {
        send(pkts[i]);
        /* Pacing.  0 means back-to-back, which is what the 2026-08-20 bench
         * run did; the knob exists so the next run can find out whether that
         * mattered instead of guessing a constant. */
        if (inter_packet_delay_ms_ && port_) port_->sleep_ms(inter_packet_delay_ms_);
        if (bytes_back_per_packet || volunteered) {
            /* Non-blocking peek.  The module should say NOTHING during a
             * DownChar data phase; anything it does say is the error report
             * the old flush()-based code was throwing away. */
            Bytes back;
            collect_unsolicited(back, 0);
            if (bytes_back_per_packet) bytes_back_per_packet->push_back(back.size());
            if (volunteered) *volunteered += back;
        }
    }
}

bool R503Link::command(const Bytes& cmd_packet, Ack& ack, uint32_t timeout_ms) {
    send(cmd_packet);
    Packet p;
    if (!recv_packet(p, timeout_ms)) return false;
    return r503::parse_ack(p, ack);
}

bool R503Link::recv_data_stream(Bytes& out, size_t max_bytes,
                                uint32_t timeout_ms, size_t& packets_out) {
    r503::DataReassembler re(max_bytes);
    packets_out = 0;
    if (!port_) return false;
    const uint32_t deadline = port_->now_ms() + timeout_ms;

    while (!re.complete()) {
        const uint32_t now = port_->now_ms();
        if (now >= deadline) { diag_.timeouts++; break; }
        Packet p;
        if (!recv_packet(p, deadline - now)) break;
        const r503::DataReassembler::Feed f = re.feed(p);
        if (f == r503::DataReassembler::Feed::WRONG_PID ||
            f == r503::DataReassembler::Feed::TOO_LARGE) {
            /* A non-data packet inside a data stream is a PHASE SHIFT, not a
             * data byte — return what we have and let the caller call it
             * PROTOCOL_DESYNC. */
            break;
        }
    }
    out = re.data();
    packets_out = re.packet_count();
    return re.complete();
}

/* ======================================================================== */
/*  failure mapping — [CXC-FPHAL-FAILURE-AXES]                              */
/* ======================================================================== */

FingerprintHALFailure RealR503HAL::axis_for(uint8_t conf, const char* what) {
    char detail[96];
    std::snprintf(detail, sizeof detail, "%s: confirmation 0x%02X", what, conf);

    switch (conf) {
        /* the stream is phase-shifted / the module could not parse us */
        case r503::CONF_PACKET_RECV_ERROR:
            return fp_make_failure(FailureAxis::PROTOCOL_DESYNC, detail, conf);

        /* store full, slot out of range, payload wrong size */
        case r503::CONF_BAD_LOCATION:
        case r503::CONF_LIBRARY_FULL:
            return fp_make_failure(FailureAxis::CAPACITY, detail, conf);

        /* the part told us it cannot do this at all */
        case r503::CONF_UNSUPPORTED_CMD:
            return fp_make_failure(FailureAxis::UNSUPPORTED, detail, conf);

        /* documented refusals — the part answered, and it was a NO */
        case r503::CONF_NO_FINGER:
        case r503::CONF_IMAGE_FAIL:
        case r503::CONF_IMAGE_MESSY:
        case r503::CONF_FEATURE_FAIL:
        case r503::CONF_NO_MATCH:
        case r503::CONF_NOT_FOUND:
        case r503::CONF_ENROLL_MISMATCH:
        case r503::CONF_INVALID_TEMPLATE:
        case r503::CONF_UPLOAD_FAIL:
        case r503::CONF_CANNOT_RECEIVE:
        case r503::CONF_UPLOAD_IMG_FAIL:
        case r503::CONF_DELETE_FAIL:
        case r503::CONF_CLEAR_FAIL:
        case r503::CONF_WRONG_PASSWORD:
        case r503::CONF_FLASH_WRITE_ERR:
        case r503::CONF_BAD_ADDRESS:
        case r503::CONF_MUST_VERIFY_PWD:
        case r503::CONF_TEMPLATE_EMPTY:
        case r503::CONF_LIBRARY_EMPTY:
        case r503::CONF_TIMEOUT:
        case r503::CONF_SENSOR_HW_ERROR:
        case r503::CONF_HARDWARE_ERROR:
        case r503::CONF_EXEC_FAILURE:
            return fp_make_failure(FailureAxis::SENSOR_REFUSAL, detail, conf);

        default:
            /* ⛔ [CXC-FPHAL-UNKNOWN-CODE]: a code no revision documents must
             * stay visible as UNKNOWN_CODE carrying the raw byte.  Laundering
             * it into SENSOR_REFUSAL would make an undocumented failure look
             * like a documented one — the exact class of lie this taxonomy
             * exists to prevent.  0x28 is the known-undocumented example. */
            return fp_make_failure(FailureAxis::UNKNOWN_CODE, detail, conf);
    }
}

bool RealR503HAL::cmd_ok(const Bytes& pkt, Ack& ack, FingerprintHALFailure& err,
                         const char* what, uint32_t timeout_ms) {
    if (!link_.command(pkt, ack, timeout_ms)) {
        char detail[96];
        std::snprintf(detail, sizeof detail, "%s: no ack (timeout/framing)", what);
        err = fp_make_failure(FailureAxis::TRANSPORT, detail);
        return false;
    }
    if (ack.confirmation != r503::CONF_OK) {
        err = axis_for(ack.confirmation, what);
        return false;
    }
    return true;
}

/* ======================================================================== */
/*  begin() — handshake + capability DISCOVERY (nothing hardcoded)          */
/* ======================================================================== */

bool RealR503HAL::begin(FingerprintHALFailure& err) {
    ready_ = false;
    prod_ok_ = false;
    link_.flush();

    /* 1. VfyPwd.  Bench-proven this session; also the command whose ack
     *    proves TX, RX, ground and baud all at once, with no state changed. */
    {
        link_.send(r503::cmd_verify_password(link_.address(),
                                             r503::kDefaultPassword));
        Packet p;
        /* any_address on FIRST contact only: we do not yet know the bound
         * address, and filtering on a guess would discard the one reply that
         * could tell us what it is. */
        if (!link_.recv_packet(p, 1000, /*any_address=*/true)) {
            err = fp_make_failure(FailureAxis::TRANSPORT,
                                  "VfyPwd: no ack (check TX/RX, ground, 57600 8N1)");
            return false;
        }
        Ack ack;
        if (!r503::parse_ack(p, ack)) {
            err = fp_make_failure(FailureAxis::PROTOCOL_DESYNC,
                                  "VfyPwd: reply was not an ACK packet");
            return false;
        }
        if (ack.confirmation != r503::CONF_OK) {
            err = axis_for(ack.confirmation, "VfyPwd");
            return false;
        }
        link_.set_address(p.address);   /* bind to whoever actually answered */
    }

    /* 2. ReadSysPara -> finger library size + PACKET CONTENT LENGTH.
     *    The packet length is the framing parameter the whole multi-packet
     *    transfer depends on; it is a SETTING, never a constant. */
    {
        Ack ack;
        if (!cmd_ok(r503::cmd_read_sys_para(link_.address()), ack, err,
                    "ReadSysPara"))
            return false;
        if (!r503::parse_sys_para(ack.params, sys_)) {
            err = fp_make_failure(FailureAxis::PROTOCOL_DESYNC,
                                  "ReadSysPara: ack carried < 16 parameter bytes");
            return false;
        }
    }

    /* 3. ReadProdInfo -> TEMPLATE SIZE.
     *    ⛔ ReadSysPara does NOT carry a template size (extract:700-708 lists
     *    status/ident/library-size/security/address/packet-size/baud, and
     *    nothing else).  PARAM_TMPL_SIZE lives in ReadProdInfo (0x3C).  A
     *    part that does not implement 0x3C answers 0xFC; that is NOT fatal —
     *    the size is then MEASURED by the first UpChar, and until it is, it
     *    stays 0 == UNKNOWN rather than becoming a plausible fiction. */
    {
        Ack ack;
        FingerprintHALFailure ignored;
        if (cmd_ok(r503::cmd_read_prod_info(link_.address()), ack, ignored,
                   "ReadProdInfo") &&
            r503::parse_prod_info(ack.params, prod_)) {
            prod_ok_ = true;
            if (prod_.template_size > 0) {
                template_size_ = prod_.template_size;
                template_size_source_ = "ReadProdInfo(0x3C) PARAM_TMPL_SIZE";
            }
        }
    }

    ready_ = true;
    return true;
}

/* ======================================================================== */
/*  capabilities()                                                          */
/* ======================================================================== */

Capabilities RealR503HAL::capabilities() {
    Capabilities c;
    if (!ready_) return c;               /* all-zero: honestly "not known yet" */
    /* slot_count: prefer ReadProdInfo's PARAM_TMPL_TOTAL when present, else
     * ReadSysPara's finger library size.  Both are ANNOUNCED figures
     * ([CXC-FPID-POPULATION-HEADROOM]: the ceiling is announced, never
     * hard-coded). */
    c.slot_count = prod_ok_ && prod_.template_total
                       ? static_cast<int>(prod_.template_total)
                       : static_cast<int>(sys_.finger_library_size);
    /* supports_readback: the R503 documents UpChar, so the CLAIM is yes.
     * ⛔ That claim is a datasheet claim, not an observation — `r503 synctest`
     * is what turns it into one. */
    c.supports_readback = true;
    c.feedback_channels = static_cast<uint32_t>(FeedbackChannel::AURA_LED);
    /* captures_per_template: RegModel combines CharBuffer1 + CharBuffer2
     * (extract:988-995) — two captures. */
    c.captures_per_template = 2;
    c.template_size_bytes = static_cast<int>(template_size_);
    return c;
}

/* ======================================================================== */
/*  match() — GenImg -> Img2Tz -> Search                                     */
/* ======================================================================== */

MatchResult RealR503HAL::match() {
    if (!ready_) return MatchResult::no_finger();

    Ack ack;
    /* GetImg: 0x02 "can't detect finger" is the NORMAL idle answer, not a
     * failure — match() has no failure channel by design, so an inability to
     * see a finger is NO_FINGER and nothing else. */
    if (!link_.command(r503::cmd_gen_img(link_.address()), ack, 1000))
        return MatchResult::no_finger();
    if (ack.confirmation != r503::CONF_OK)
        return MatchResult::no_finger();

    if (!link_.command(r503::cmd_img2tz(link_.address(), 1), ack, 1500))
        return MatchResult::no_finger();
    if (ack.confirmation != r503::CONF_OK)
        return MatchResult::no_finger();   /* messy/too-small image */

    const uint16_t span = static_cast<uint16_t>(
        capabilities().slot_count > 0 ? capabilities().slot_count : 200);
    if (!link_.command(r503::cmd_search(link_.address(), 1, 0, span), ack, 2000))
        return MatchResult::no_finger();

    if (ack.confirmation == r503::CONF_NOT_FOUND)
        return MatchResult::no_match();
    if (ack.confirmation != r503::CONF_OK)
        return MatchResult::no_finger();

    uint16_t page = 0, score = 0;
    if (!r503::parse_search_ack(ack, page, score))
        return MatchResult::no_finger();
    return MatchResult::matched(static_cast<Slot>(page),
                                static_cast<Score>(score));
}

/* ======================================================================== */
/*  enrollment                                                              */
/* ======================================================================== */

EnrollmentSession RealR503HAL::begin_enroll(Ordinal ordinal) {
    enroll_ordinal_ = ordinal;
    EnrollmentSession s;
    s.hal = this;
    s.ordinal = ordinal;
    s.required = capabilities().captures_per_template;
    if (s.required <= 0) s.required = 2;
    s.done = 0;
    s.complete_ = false;
    return s;
}

bool RealR503HAL::alive() {
    Ack ack;
    if (!link_.command(r503::cmd_read_sys_para(link_.address()), ack, 1000))
        return false;
    return ack.confirmation == r503::CONF_OK;
}

bool RealR503HAL::unwedge(Bytes& volunteered) {
    /* A module stuck mid-DownChar is waiting for one thing: an end marker.
     * Give it one, in band, then see whether it starts answering commands
     * again.  Anything it emits on the way out is KEPT, not flushed — a
     * late 0x01 here would name the exact packet it choked on. */
    link_.send(r503::build_empty_end_packet(link_.address()));
    link_.collect_unsolicited(volunteered, 300);
    link_.flush();
    return alive();
}

R503ExtractResult RealR503HAL::run_extract(Slot slot, uint8_t buffer_id) {
    R503ExtractResult r;
    r.ran = true;
    r.slot = slot;
    if (!ready_) { r.failure = "link not ready — run begin() first"; return r; }

    const int n = capabilities().slot_count;
    if (slot < 0 || (n > 0 && slot >= n)) {
        r.failure = "slot out of range";
        return r;
    }
    r.expected_bytes = template_size_;
    r.loadchar_cmd_bytes =
        r503::cmd_load_char(link_.address(), 1, static_cast<uint16_t>(slot));
    r.upchar_cmd_bytes = r503::cmd_up_char(link_.address(), buffer_id);

    /* Clean slate BEFORE the first command is legitimate — nothing pending
     * here can be evidence about this run, which has not started. */
    link_.flush();

    /* 1. LoadChar: flash slot -> CharBuffer1. */
    {
        Ack ack;
        if (!link_.command(r.loadchar_cmd_bytes, ack, 2000)) {
            r.failure = "LoadChar: no ack — the module is not answering "
                        "commands at all (is it wedged from a previous run? "
                        "try 'r503 unwedge')";
            r.diag = link_.diag();
            return r;
        }
        r.load_conf = ack.confirmation;
        if (ack.confirmation != r503::CONF_OK) {
            r.failure =
                ack.confirmation == r503::CONF_INVALID_TEMPLATE
                    ? "LoadChar refused 0x0C — no valid template in that slot"
                    : (ack.confirmation == r503::CONF_BAD_LOCATION
                           ? "LoadChar refused 0x0B — slot beyond the library"
                           : "LoadChar refused");
            r.diag = link_.diag();
            return r;
        }
        r.load_acked = true;
    }

    /* 2. UpChar: CharBuffer1 -> host.  NO DownChar anywhere in this path. */
    {
        Ack ack;
        if (!link_.command(r.upchar_cmd_bytes, ack, 1500)) {
            r.failure = "UpChar: NO ACK, with no DownChar involved anywhere in "
                        "this run — this is evidence that UpChar itself is "
                        "unsupported or broken on this firmware, not that our "
                        "DownChar data phase wedged it";
            link_.collect_unsolicited(r.unsolicited, 300);
            r.diag = link_.diag();
            return r;
        }
        r.up_conf = ack.confirmation;
        if (ack.confirmation != r503::CONF_OK) {
            r.failure = ack.confirmation == r503::CONF_UPLOAD_FAIL
                            ? "UpChar refused 0x0D 'error when uploading "
                              "template' — a REFUSAL, which is an answer: the "
                              "command exists and declined"
                            : "UpChar refused";
            r.diag = link_.diag();
            return r;
        }
        r.up_acked = true;

        const size_t ceiling = template_size_ ? template_size_ + 512 : 4096;
        Bytes out;
        r.stream_complete =
            link_.recv_data_stream(out, ceiling, 5000, r.packets);
        r.bytes = out.size();
        r.head = out.substr(0, out.size() < 8 ? out.size() : 8);
        r.tail = out.substr(out.size() >= 8 ? out.size() - 8 : 0);
        if (!r.stream_complete)
            r.failure = r.bytes ? "UpChar stream never terminated (no PID 0x08)"
                                : "UpChar acked OK but sent no data at all";
        else if (!template_size_ && r.bytes) {
            template_size_ = r.bytes;
            template_size_source_ = "MEASURED by r503 extract";
        }
    }
    r.diag = link_.diag();
    return r;
}

bool RealR503HAL::finger_present() {
    if (!ready_) return false;
    Ack ack;
    if (!link_.command(r503::cmd_gen_img(link_.address()), ack, 1000))
        return false;
    return ack.confirmation == r503::CONF_OK;
}

bool RealR503HAL::enroll_capture(int capture_index, FingerprintHALFailure& err) {
    if (!ready_) {
        err = fp_make_failure(FailureAxis::TRANSPORT, "enroll_capture: link not ready");
        return false;
    }
    /* CharBuffer numbering is 1-based and RegModel combines 1 and 2. */
    const uint8_t buf = static_cast<uint8_t>(capture_index <= 1 ? 1 : 2);
    Ack ack;
    if (!cmd_ok(r503::cmd_gen_img(link_.address()), ack, err, "GetImg"))
        return false;
    return cmd_ok(r503::cmd_img2tz(link_.address(), buf), ack, err, "GenChar", 1500);
}

bool RealR503HAL::enroll_combine(FingerprintHALFailure& err) {
    Ack ack;
    return cmd_ok(r503::cmd_reg_model(link_.address()), ack, err, "RegModel", 2000);
}

bool RealR503HAL::enroll_store(Slot slot, FingerprintHALFailure& err) {
    const int n = capabilities().slot_count;
    if (slot < 0 || (n > 0 && slot >= n)) {
        err = fp_make_failure(FailureAxis::CAPACITY, "Store: slot out of range");
        return false;
    }
    Ack ack;
    return cmd_ok(r503::cmd_store(link_.address(), 1, static_cast<uint16_t>(slot)),
                  ack, err, "Store", 2000);
}

/* ======================================================================== */
/*  the multi-packet halves                                                 */
/* ======================================================================== */

bool RealR503HAL::down_char(uint8_t buffer_id, const TemplateBytes& t,
                            FingerprintHALFailure& err) {
    if (t.empty()) {
        err = fp_make_failure(FailureAxis::CAPACITY, "DownChar: empty payload");
        return false;
    }
    link_.flush();

    Ack ack;
    if (!cmd_ok(r503::cmd_down_char(link_.address(), buffer_id), ack, err,
                "DownChar", 1000))
        return false;

    /* Ambiguity A4, resolved: the module has ACKed "ready to transfer the
     * following data packet"; the HOST now sends them. */
    const std::vector<Bytes> pkts =
        r503::build_data_packets(link_.address(), t, sys_.packet_content_len());
    if (pkts.empty()) {
        err = fp_make_failure(FailureAxis::CAPACITY,
                              "DownChar: nothing to send (packet length 0?)");
        return false;
    }
    for (size_t i = 0; i < pkts.size(); i++) link_.send(pkts[i]);

    /* The manual specifies NO trailing ack after a DownChar data stream, so
     * absence of one is SUCCESS-SHAPED and cannot be used as proof.  That is
     * precisely why synctest reads the bytes BACK rather than trusting this
     * step's silence ([CXC-VISION-EPISTEMICS]). */
    return true;
}

bool RealR503HAL::up_char(uint8_t buffer_id, TemplateBytes& out,
                          FingerprintHALFailure& err) {
    link_.flush();
    Ack ack;
    if (!cmd_ok(r503::cmd_up_char(link_.address(), buffer_id), ack, err,
                "UpChar", 1000))
        return false;

    /* Ceiling: the known template size when we have one, else a bounded
     * guess big enough for any documented template.  A ceiling is required —
     * an unbounded reassembler on a desynced stream is a heap exhaustion. */
    const size_t ceiling = template_size_ ? template_size_ + 64 : 4096;
    size_t packets = 0;
    const bool complete = link_.recv_data_stream(out, ceiling, 4000, packets);
    if (!complete) {
        char d[128];
        std::snprintf(d, sizeof d,
                      "UpChar: stream incomplete after %u packets / %u bytes",
                      static_cast<unsigned>(packets),
                      static_cast<unsigned>(out.size()));
        err = fp_make_failure(FailureAxis::PROTOCOL_DESYNC, d);
        return false;
    }
    if (!template_size_ && !out.empty()) {
        template_size_ = out.size();
        template_size_source_ = "MEASURED by first successful UpChar";
    }
    return true;
}

/* ======================================================================== */
/*  the HAL storage face                                                    */
/* ======================================================================== */

Slot RealR503HAL::first_free_slot() {
    const std::set<Slot> occ = read_occupancy();
    const int n = capabilities().slot_count;
    for (int i = 0; i < n; i++)
        if (occ.find(i) == occ.end()) return i;
    return -1;
}

bool RealR503HAL::store_template(const TemplateBytes& t, Slot& out_slot,
                                 FingerprintHALFailure& err) {
    if (!ready_) {
        err = fp_make_failure(FailureAxis::TRANSPORT, "store_template: link not ready");
        return false;
    }
    /* [CXC-FPBUS-REQ-TEMPLATE-SIZE-GUARD] — guarded at BOTH ends.  Only
     * enforced once the size is KNOWN; an unknown size must not manufacture
     * a bound out of nothing. */
    if (template_size_ && t.size() != template_size_) {
        char d[128];
        std::snprintf(d, sizeof d,
                      "store_template: payload %u bytes, device template_size %u",
                      static_cast<unsigned>(t.size()),
                      static_cast<unsigned>(template_size_));
        err = fp_make_failure(FailureAxis::CAPACITY, d);
        return false;
    }
    const Slot slot = first_free_slot();
    if (slot < 0) {
        err = fp_make_failure(FailureAxis::CAPACITY, "store_template: library full");
        return false;
    }
    if (!down_char(1, t, err)) return false;

    Ack ack;
    if (!cmd_ok(r503::cmd_store(link_.address(), 1, static_cast<uint16_t>(slot)),
                ack, err, "Store", 2000))
        return false;
    out_slot = slot;
    return true;
}

bool RealR503HAL::read_template(Slot slot, TemplateBytes& out,
                                FingerprintHALFailure& err) {
    if (!ready_) {
        err = fp_make_failure(FailureAxis::TRANSPORT, "read_template: link not ready");
        return false;
    }
    const int n = capabilities().slot_count;
    if (slot < 0 || (n > 0 && slot >= n)) {
        err = fp_make_failure(FailureAxis::CAPACITY, "read_template: slot out of range");
        return false;
    }
    Ack ack;
    if (!cmd_ok(r503::cmd_load_char(link_.address(), 1, static_cast<uint16_t>(slot)),
                ack, err, "LoadChar", 2000))
        return false;
    return up_char(1, out, err);
}

bool RealR503HAL::empty(FingerprintHALFailure& err) {
    if (!ready_) {
        err = fp_make_failure(FailureAxis::TRANSPORT, "empty: link not ready");
        return false;
    }
    Ack ack;
    return cmd_ok(r503::cmd_empty(link_.address()), ack, err, "Empty", 3000);
}

std::set<Slot> RealR503HAL::read_occupancy() {
    std::set<Slot> out;
    if (!ready_) return out;
    const int n = capabilities().slot_count;
    const int pages = (n + 255) / 256;
    for (int page = 0; page < pages && page < 4; page++) {
        Ack ack;
        if (!link_.command(r503::cmd_read_index_table(
                               link_.address(), static_cast<uint8_t>(page)),
                           ack, 1500))
            break;
        if (ack.confirmation != r503::CONF_OK) break;
        std::vector<int> slots;
        if (!r503::parse_index_table_ack(ack, static_cast<uint16_t>(page), slots))
            break;
        for (size_t i = 0; i < slots.size(); i++)
            if (n <= 0 || slots[i] < n) out.insert(slots[i]);
    }
    return out;
}

/* ======================================================================== */
/*  roster stamp — the sensor's own notepad                                 */
/* ======================================================================== */
/* [CXC-FPID-ROSTER-STAMP]: the stamp is testimony about WHAT THIS DEVICE
 * HOLDS.  It therefore belongs with the templates, in the module's 512-byte
 * notepad (extract:325-328), NOT in the host's NVS — a stamp in host flash
 * would survive a sensor swap and then testify about templates that are no
 * longer there.
 *
 * Layout, pages 0..2 (96 bytes): 'R','S', len(1), then `len` payload bytes,
 * zero-padded.  Capacity 93, comfortably over a 36-char UUID. */
static constexpr uint8_t kStampPage0   = 0;
static constexpr int     kStampPages   = 3;
static constexpr size_t  kStampMaxLen  = 3 * r503::kNotepadPageSize - 3;

void RealR503HAL::write_roster_stamp(const RosterStamp& stamp) {
    if (!ready_) return;
    Bytes blob;
    blob.push_back('R');
    blob.push_back('S');
    const size_t n = stamp.size() > kStampMaxLen ? kStampMaxLen : stamp.size();
    blob.push_back(static_cast<char>(n));
    blob += stamp.substr(0, n);
    blob.resize(kStampPages * r503::kNotepadPageSize, '\0');

    for (int p = 0; p < kStampPages; p++) {
        Ack ack;
        link_.command(r503::cmd_write_notepad(
                          link_.address(),
                          static_cast<uint8_t>(kStampPage0 + p),
                          blob.substr(p * r503::kNotepadPageSize,
                                      r503::kNotepadPageSize)),
                      ack, 2000);
        /* write_roster_stamp has no failure channel in the contract; a failed
         * page leaves the stamp unreadable, which read_roster_stamp reports
         * as "no stamp" — the honest outcome ([CXC-FPBUS-SCEN-PARTIAL-LOAD-
         * HOLDS-STAMP]: a partially-loaded device must NOT claim a stamp). */
    }
}

std::optional<RosterStamp> RealR503HAL::read_roster_stamp() {
    if (!ready_) return std::nullopt;
    Bytes blob;
    for (int p = 0; p < kStampPages; p++) {
        Ack ack;
        if (!link_.command(r503::cmd_read_notepad(
                               link_.address(),
                               static_cast<uint8_t>(kStampPage0 + p)),
                           ack, 1500))
            return std::nullopt;
        if (ack.confirmation != r503::CONF_OK) return std::nullopt;
        if (ack.params.size() < r503::kNotepadPageSize) return std::nullopt;
        blob += ack.params.substr(0, r503::kNotepadPageSize);
    }
    if (blob.size() < 3) return std::nullopt;
    if (blob[0] != 'R' || blob[1] != 'S') return std::nullopt;
    const size_t n = static_cast<uint8_t>(blob[2]);
    if (n == 0 || n > kStampMaxLen || 3 + n > blob.size()) return std::nullopt;
    return blob.substr(3, n);
}

/* ======================================================================== */
/*  feedback — the Aura ring                                                */
/* ======================================================================== */
/* ⛔ [CXC-R503-AURALED]: the animated modes have a FIVE-SECOND MINIMUM cycle
 * (extract:1400).  "Flash twice" is therefore not a quick blink — it is a
 * ten-second animation.  Anything wanting sub-second feedback must use the
 * non-animated ON/OFF modes, which is why ACCEPT/REJECT below are solid, not
 * flashing.
 *
 * ⛔ [CXC-FPID-RING-HIDES-DENIAL]: REJECT_SILENT and REJECT_KNOWN are the
 * SAME red on purpose.  The ring is not a mirror of the bus event — making
 * "unrecognized" visually distinct from "denied" would leak the roster to
 * anyone with a finger. */
bool RealR503HAL::command_feedback(FeedbackState state, const FpProgress& progress,
                                   FingerprintHALFailure& err) {
    (void)progress;
    if (!ready_) {
        err = fp_make_failure(FailureAxis::TRANSPORT, "command_feedback: link not ready");
        return false;
    }
    uint8_t ctrl = r503::AURA_OFF, speed = 0x60, colour = r503::AURA_BLUE, count = 0;

    switch (state) {
        case FeedbackState::IDLE:
            ctrl = r503::AURA_OFF;  colour = r503::AURA_BLUE;  count = 0; break;
        case FeedbackState::READY:
            ctrl = r503::AURA_BREATHING; colour = r503::AURA_BLUE;
            speed = 0xC0; count = 0; break;
        case FeedbackState::AWAITING_FINGER:
            ctrl = r503::AURA_BREATHING; colour = r503::AURA_CYAN;
            speed = 0x60; count = 0; break;
        case FeedbackState::CAPTURE_ACCEPTED:
            ctrl = r503::AURA_ON;   colour = r503::AURA_GREEN;  count = 0; break;
        case FeedbackState::CAPTURE_RETRY:
        case FeedbackState::RETRY:
            ctrl = r503::AURA_ON;   colour = r503::AURA_YELLOW; count = 0; break;
        case FeedbackState::ENROLL_PROGRESS:
            ctrl = r503::AURA_BREATHING; colour = r503::AURA_PURPLE;
            speed = 0x40; count = 0; break;
        case FeedbackState::BUSY:
            ctrl = r503::AURA_BREATHING; colour = r503::AURA_WHITE;
            speed = 0x30; count = 0; break;
        case FeedbackState::FAULT:
            ctrl = r503::AURA_FLASHING; colour = r503::AURA_RED;
            speed = 0x40; count = 0; break;
        case FeedbackState::ACCEPT:
            ctrl = r503::AURA_ON;   colour = r503::AURA_GREEN;  count = 0; break;
        case FeedbackState::REJECT_SILENT:
        case FeedbackState::REJECT_KNOWN:
            ctrl = r503::AURA_ON;   colour = r503::AURA_RED;    count = 0; break;
    }
    Ack ack;
    return cmd_ok(r503::cmd_aura_led(link_.address(), ctrl, speed, colour, count),
                  ack, err, "AuraLedConfig", 1000);
}

bool RealR503HAL::aura_raw(uint8_t ctrl, uint8_t speed, uint8_t colour,
                           uint8_t count, FingerprintHALFailure& err) {
    if (!ready_) { err = fp_make_failure(FailureAxis::TRANSPORT, "aura_raw: link not ready"); return false; }
    Ack ack;
    return cmd_ok(r503::cmd_aura_led(link_.address(), ctrl, speed, colour, count),
                  ack, err, "AuraLedConfig", 1000);
}

/* ======================================================================== */
/*  run_sync_test — THE de-risk instrument                                  */
/* ======================================================================== */
/* Answers, on real silicon and WITHOUT a finger: does this module's firmware
 * actually honour template UPLOAD (DownChar 0x09) and DOWNLOAD (UpChar 0x08)?
 *
 * Sequence, and why each step is where it is:
 *   1. ReadSysPara      -> the module's ACTUAL packet content length.  Every
 *                          data packet is framed to it.  Never assumed.
 *   2. ReadProdInfo     -> PARAM_TMPL_SIZE, the module's ACTUAL template
 *                          size.  (Not in ReadSysPara — see begin().)
 *   3. UpChar #1        -> if the size is still unknown, MEASURE it by
 *                          reading whatever is in the buffer.  This also
 *                          exercises the download direction on its own,
 *                          BEFORE anything has been written — so a failure
 *                          here cannot be blamed on our upload.
 *   4. DownChar a RAMP  -> a deterministic pattern (i*7+3 mod 256, a
 *                          bijection over a byte, so a truncation, a repeat
 *                          and a swap are all separately visible; a constant
 *                          fill would hide every one of them).
 *   5. UpChar #2        -> byte-compare.
 *
 * NON-DESTRUCTIVE: char buffers only.  No Store, no Empty, no flash write.
 *
 * ⛔ WHAT A PASS DOES AND DOES NOT PROVE.  A pass proves DownChar-then-UpChar
 * round-trips through the module's char buffer.  It does NOT prove the bytes
 * are a valid template, nor that a template written this way will MATCH a
 * finger — that needs enroll -> UpChar -> DownChar -> Search on glass.
 * A FAILURE, though, is decisive: if the ramp does not come back, the whole
 * push-templates-over-the-bus architecture needs a different mechanism. */
R503SyncTestResult RealR503HAL::run_sync_test(const R503SyncTestOptions& opt) {
    R503SyncTestResult r;
    r.ran = true;
    const uint8_t buffer_id = opt.buffer_id;
    r.style = opt.style;
    r.inter_packet_delay_ms = opt.inter_packet_delay_ms;
    link_.set_inter_packet_delay_ms(opt.inter_packet_delay_ms);

    if (!ready_) { r.failure = "link not ready — run begin() first"; return r; }

    r.sys_para_ok = true;                         /* begin() already read it */
    r.packet_content_len = sys_.packet_content_len();
    r.prod_info_ok = prod_ok_;

    /* ---- step 3: establish the length, by reading rather than assuming --- */
    size_t want = template_size_;
    r.size_source = template_size_source_;
    if (want == 0) {
        TemplateBytes probe;
        FingerprintHALFailure e;
        if (up_char(buffer_id, probe, e) && !probe.empty()) {
            want = probe.size();
            r.size_source = "MEASURED by a probe UpChar";
        } else {
            r.failure = std::string("template size unknown: ReadProdInfo did not "
                                    "supply PARAM_TMPL_SIZE and the probe UpChar "
                                    "failed (") + e.detail + ")";
            r.diag = link_.diag();
            return r;
        }
    }
    r.template_size = want;

    /* ---- step 4: build the ramp and DownChar it ------------------------- */
    TemplateBytes ramp;
    ramp.reserve(want);
    for (size_t i = 0; i < want; i++)
        ramp.push_back(static_cast<char>((i * 7 + 3) & 0xFF));
    r.sent_len = ramp.size();
    r.sent_head = ramp.substr(0, 8);
    r.sent_tail = ramp.substr(ramp.size() >= 8 ? ramp.size() - 8 : 0);

    r.downchar_cmd_bytes = r503::cmd_down_char(link_.address(), buffer_id);
    r.upchar_cmd_bytes   = r503::cmd_up_char(link_.address(), buffer_id);
    const std::vector<Bytes> pkts = r503::build_data_packets(
        link_.address(), ramp, r.packet_content_len, opt.style);
    r.data_packets_sent = pkts.size();
    if (!pkts.empty()) {
        /* the FIRST data packet, header-and-checksum included, clipped for
         * printing — the row the architect eyeballs against the manual. */
        r.sample_data_packet = pkts[0].substr(0, 9 + 8);
    }

    {
        link_.flush();   /* legitimate: nothing pending here is evidence yet */
        Ack ack;
        if (!link_.command(r.downchar_cmd_bytes, ack, 1000)) {
            r.failure = "DownChar: no ack";
            r.diag = link_.diag();
            return r;
        }
        r.down_conf = ack.confirmation;
        if (ack.confirmation != r503::CONF_OK) {
            r.failure = ack.confirmation == r503::CONF_CANNOT_RECEIVE
                            ? "DownChar refused with 0x0E 'can not receive the "
                              "following data packet' — this firmware does NOT "
                              "accept template upload"
                            : "DownChar refused";
            r.diag = link_.diag();
            return r;
        }
        r.downchar_acked = true;

        /* THE DATA PHASE, TRACED.  Ambiguity A4 resolved: the module has
         * ACKed "ready to transfer", so the HOST sends.  Each packet is
         * recorded, and anything the module volunteers between packets is
         * captured rather than discarded. */
        std::vector<size_t> back;
        link_.send_data_stream(pkts, &back, &r.post_data_bytes);
        for (size_t i = 0; i < pkts.size(); i++) {
            R503DataPacketTrace t;
            t.index = i;
            t.pid = static_cast<uint8_t>(pkts[i][6]);
            t.length_field = static_cast<uint16_t>(
                (static_cast<uint8_t>(pkts[i][7]) << 8) |
                static_cast<uint8_t>(pkts[i][8]));
            t.content_bytes = pkts[i].size() - 11;
            t.head = pkts[i].substr(9, t.content_bytes < 4 ? t.content_bytes : 4);
            t.bytes_back = i < back.size() ? back[i] : 0;
            r.trace.push_back(t);
        }
    }

    /* ---- step 4b: LISTEN, do not flush ----------------------------------
     * ⛔ THE INSTRUMENT FIX.  The previous revision called link_.flush()
     * here, which DISCARDED anything the module said about the data phase
     * and left the 2026-08-20 bench run reporting the uninformative "UpChar:
     * no ack".  Now the window is collected and reported.  A late 0x01
     * "error when receiving package" arriving here names the failure
     * outright; silence here is itself a datum. */
    {
        /* APPEND, never overwrite: the per-packet peeks above have already
         * pulled some of this off the port, and clobbering them here would
         * lose the evidence a second way. */
        Bytes more;
        link_.collect_unsolicited(more, opt.post_data_listen_ms);
        r.post_data_bytes += more;
        if (!r.post_data_bytes.empty()) {
            r503::ScanOutcome s = r503::scan_packet(r.post_data_bytes);
            Ack a;
            if (s.kind == r503::ScanOutcome::Kind::PACKET &&
                r503::parse_ack(s.packet, a)) {
                r.post_data_ack_parsed = true;
                r.post_data_conf = a.confirmation;
            }
        }
    }

    /* ---- step 5: UpChar it back and compare ----------------------------- */
    {
        /* Drop only what we have already RECORDED above, so the UpChar ack
         * is framed against a clean buffer without losing the evidence. */
        link_.flush();
        Ack ack;
        bool acked = link_.command(r.upchar_cmd_bytes, ack, 1000);

        /* ⛔ THE DIAGNOSIS ATTEMPT, and it is a diagnosis rather than a mere
         * retry.  Total silence from UpChar (as opposed to a 0x0D refusal)
         * is what a module still sitting in its DownChar receive phase looks
         * like: it consumed our 13 command bytes as data.  Sending a bare
         * empty PID 0x08 ends that phase.  If UpChar then answers, the cause
         * was OUR DATA PHASE FAILING TO TERMINATE — a bug we own and can
         * fix — and NOT an unsupported UpChar. */
        if (!acked && opt.retry_with_empty_end) {
            r.retried_with_empty_end = true;
            Bytes vol;
            link_.send(r503::build_empty_end_packet(link_.address()));
            link_.collect_unsolicited(vol, 300);
            r.post_data_bytes += vol;
            link_.flush();
            acked = link_.command(r.upchar_cmd_bytes, ack, 1500);
            if (acked) r.rescued_by_empty_end = true;
        }

        if (!acked) {
            r.failure =
                "UpChar: NO ACK (not a refusal — total silence).  The module "
                "did not parse the command, and an empty end-packet did not "
                "revive it.  Run 'r503 extract 1' next: it exercises UpChar "
                "with NO DownChar involved and separates 'our data phase "
                "wedged it' from 'UpChar is unsupported'";
            r.alive_after = alive();
            r.diag = link_.diag();
            return r;
        }
        r.up_conf = ack.confirmation;
        if (ack.confirmation != r503::CONF_OK) {
            r.failure = ack.confirmation == r503::CONF_UPLOAD_FAIL
                            ? "UpChar refused with 0x0D 'error when uploading "
                              "template' — this firmware does NOT allow template "
                              "download"
                            : "UpChar refused";
            r.diag = link_.diag();
            return r;
        }
        r.upchar_acked = true;

        TemplateBytes back;
        size_t packets = 0;
        r.stream_complete =
            link_.recv_data_stream(back, want + 512, 5000, packets);
        r.data_packets_recv = packets;
        r.recv_len = back.size();
        r.recv_head = back.substr(0, back.size() < 8 ? back.size() : 8);
        r.recv_tail = back.substr(back.size() >= 8 ? back.size() - 8 : 0);

        if (!r.stream_complete) {
            r.failure = r.recv_len
                ? "UpChar stream never terminated (no PID 0x08 end packet)"
                : "UpChar acked OK but sent NO DATA — the command exists and "
                  "was accepted, yet nothing followed";
            r.alive_after = alive();
            r.diag = link_.diag();
            return r;
        }
        if (back.size() != ramp.size()) {
            r.failure = "length mismatch: what came back is not what went out";
            r.diag = link_.diag();
            return r;
        }
        for (size_t i = 0; i < ramp.size(); i++) {
            if (ramp[i] != back[i]) {
                r.first_diff_index = i;
                r.failure = "byte mismatch";
                r.diag = link_.diag();
                return r;
            }
        }
        r.byte_exact = true;
    }
    /* Whatever the verdict, say whether the part is still answering.  A
     * synctest that leaves the module wedged and does not SAY SO poisons
     * every command the architect runs next. */
    r.alive_after = alive();
    r.diag = link_.diag();
    return r;
}

/* ======================================================================== */
/*  ArduinoR503Port — the ONLY silicon-touching code in this file           */
/* ======================================================================== */
#ifdef ESP_PLATFORM
#include <Arduino.h>
#include <HardwareSerial.h>

static HardwareSerial g_r503_uart(R503_UART_NUM);

bool ArduinoR503Port::begin(uint32_t baud, int tx_pin, int rx_pin) {
    /* SERIAL_8N1, RX then TX in the Arduino-ESP32 signature. */
    g_r503_uart.begin(baud, SERIAL_8N1, rx_pin, tx_pin);
    g_r503_uart.setTimeout(50);
    delay(20);
    /* [CXC-R503-UNSOLICITED-0X55]: the module emits a bare 0x55 at power-on.
     * Drain whatever is already sitting in the FIFO so the first real reply
     * is not framed against it.  scan_packet() would resync anyway — this
     * just keeps the diagnostic counters honest about the LINK rather than
     * about boot order. */
    while (g_r503_uart.available() > 0) (void)g_r503_uart.read();
    return true;
}

void ArduinoR503Port::write(const r503::Bytes& b) {
    g_r503_uart.write(reinterpret_cast<const uint8_t*>(b.data()), b.size());
    g_r503_uart.flush();
}

int ArduinoR503Port::read_byte(uint32_t timeout_ms) {
    const uint32_t deadline = millis() + timeout_ms;
    for (;;) {
        if (g_r503_uart.available() > 0) return g_r503_uart.read();
        if (static_cast<int32_t>(millis() - deadline) >= 0) return -1;
        delay(1);   /* yields; a tight spin starves the WiFi/MQTT tasks */
    }
}

void ArduinoR503Port::drain_input() {
    while (g_r503_uart.available() > 0) (void)g_r503_uart.read();
}

uint32_t ArduinoR503Port::now_ms() { return millis(); }

void ArduinoR503Port::sleep_ms(uint32_t ms) { delay(ms); }
#endif /* ESP_PLATFORM */
