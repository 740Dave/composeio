// R503 bring-up probe — S3-side firmware.
//
// WHAT THIS IS: the wire layer. It owns framing, checksums, the drain, the resync
// and every timeout. It returns PARSED RESULTS to the host over a line protocol of
// its own. It does NOT bridge raw R503 bytes to USB, because the R503's protocol is
// timing-coupled at the byte level and none of that survives a USB hop.
//
// WHAT THIS IS NOT: not the HAL, not R503HAL, not a driver, not fingerprint_storage.
// [SPEC:CXC-FPHAL] is UNRATIFIED and under review; nothing here implements it.
//
// HOST LINK PROTOCOL — line oriented, self-synchronising by sentinel:
//   host -> s3 :  "> <seq> <verb> [args...]\n"
//   s3 -> host :  "< <seq> <status> key=value ...\n"
//   s3 -> host :  "# <free text>\n"        diagnostics; the host IGNORES these
// Anything the host receives that does not begin with '<' is not a response. That
// makes a boot banner, a panic dump or a stray byte harmless instead of a desync —
// the same lesson as the sliding header window, applied one layer up.
//
// ⛔ NO BAUD CHANGE. There is no SetSysPara helper in this firmware and no verb that
//   emits one. [CXC-R503-BAUD-SELF-CONTRADICTS] — no recovery from a bricked module
//   is documented anywhere, in any revision.

#include <ctype.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "esp_chip_info.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "r503_codec.h"
#include "r503_link.h"

// ⛔ GPIO defaults. David MUST confirm these against the SuperMini's silkscreen.
// They are chosen to be broken out on essentially every S3 mini board and to avoid
// every hazard class: not strapping pins (0, 3, 45, 46), not USB (19, 20), not
// SPI flash/PSRAM (26-32), not the UART0 pads (43, 44).
// If the silkscreen disagrees, the `pins` verb retargets them AT RUNTIME with no
// reflash — which is the cheap escape from a fact this firmware does not hold.
#ifndef R503_TX_GPIO
#define R503_TX_GPIO 4  // S3 GPIO4  -> R503 pin 4 RXD
#endif
#ifndef R503_RX_GPIO
#define R503_RX_GPIO 5  // S3 GPIO5  <- R503 pin 3 TXD
#endif

#define PROBE_LINE_MAX 512
#define LINE_STALL_MS 3000  // a partial command line that stops arriving is dropped

static char s_out[2048];

// Compiler-written build stamp, space-free so it survives a whitespace-delimited
// key=value parser. ⛔ NEVER a hand-maintained tag: on this fleet a hand-bumped
// build string read the same value through ~10 flashes across three boards,
// including one that did not exist when the string was written — the instrument
// built to detect staleness had itself gone stale.
static char s_build[48];

static void build_stamp_init(void) {
    snprintf(s_build, sizeof(s_build), "%s_%s", __DATE__, __TIME__);
    for (char *p = s_build; *p; p++) {
        if (*p == ' ') *p = '-';
    }
}

static void emit(const char *s) {
    size_t n = strlen(s);
    // Bounded write. Even the host link has a deadline: a host that stops reading
    // must not be able to wedge the probe.
    usb_serial_jtag_write_bytes(s, n, pdMS_TO_TICKS(500));
}

static void emitf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(s_out, sizeof(s_out), fmt, ap);
    va_end(ap);
    emit(s_out);
}

static void hexcat(char *dst, size_t cap, const uint8_t *b, uint32_t n) {
    size_t used = strlen(dst);
    for (uint32_t i = 0; i < n && used + 3 < cap; i++) {
        snprintf(dst + used, cap - used, "%02X", b[i]);
        used += 2;
    }
}

// ---------------------------------------------------------------------------
// Confirmation codes. [CXC-R503-CONFIRMATION-CODES]
// ⛔ An UNKNOWN code is DATA, never a fatal error. 0x28 is used by the firmware's
//   own worked example (§6.2.1) and appears in NO revision's table; a driver that
//   treats an unlisted code as a protocol error fails on the vendor's own example.
// ---------------------------------------------------------------------------
static const char *conf_name(uint8_t c) {
    switch (c) {
        case 0x00: return "complete";
        case 0x01: return "packet_receive_error";
        case 0x02: return "no_finger";
        case 0x03: return "enroll_fail";
        case 0x06: return "image_too_disorderly";
        case 0x07: return "too_few_feature_points";
        case 0x08: return "no_match";
        case 0x09: return "no_matching_finger_found";
        case 0x0A: return "combine_fail";
        case 0x0B: return "pageid_beyond_library";
        case 0x0C: return "template_read_error";
        case 0x0D: return "template_upload_error";
        case 0x0E: return "cannot_receive_following_packets";
        case 0x0F: return "image_upload_error";
        case 0x10: return "delete_template_fail";
        case 0x11: return "clear_library_fail";
        case 0x13: return "wrong_password";
        case 0x15: return "no_valid_primary_image";
        case 0x18: return "flash_write_error";
        case 0x19: return "no_definition_error";
        case 0x1A: return "invalid_register_number";
        case 0x1B: return "register_config_incorrect";
        case 0x1C: return "wrong_notepad_page";
        case 0x1D: return "comm_port_operation_fail";
        case 0x1F: return "library_full";
        case 0x20: return "address_code_incorrect";
        case 0x21: return "password_must_be_verified";
        case 0x22: return "template_empty";
        case 0x24: return "library_empty";
        case 0x26: return "timeout";
        case 0x27: return "fingerprint_already_exists";
        case 0x29: return "sensor_hardware_error";
        case 0xFC: return "unsupported_command";
        case 0xFD: return "hardware_error";
        case 0xFE: return "execution_failure";
        default: return NULL;  // ⛔ UNKNOWN IS NOT FATAL. It is a finding.
    }
}

static const char *txn_status(r503_txn_result_t r) {
    switch (r) {
        case R503_TXN_OK: return "ok";
        case R503_TXN_TIMEOUT_NO_BYTES: return "timeout_no_bytes";
        case R503_TXN_TIMEOUT_PARTIAL: return "timeout_partial";
        case R503_TXN_BAD_CHECKSUM: return "bad_checksum";
        case R503_TXN_LENGTH_ABSURD: return "length_absurd";
        case R503_TXN_TX_FAIL: return "tx_fail";
        default: return "unknown";
    }
}

// Render one transaction result as a single response line.
static void report_txn(const char *seq, r503_txn_result_t r, const r503_packet_t *pkt,
                       const r503_txn_stats_t *st) {
    char pre[80] = "";
    hexcat(pre, sizeof(pre), st->pre_drain, st->pre_drain_kept);
    char skip[80] = "";
    hexcat(skip, sizeof(skip), st->skipped, st->skipped_kept);

    char content[2 * R503_MAX_CONTENT + 8] = "";
    uint16_t clen = 0;
    if (r == R503_TXN_OK || r == R503_TXN_BAD_CHECKSUM) {
        clen = pkt->content_len;
        hexcat(content, sizeof(content), pkt->content, clen);
    }

    // pre_drain and skipped are reported on EVERY transaction, success or not.
    // [CXC-R503-UNSOLICITED-0X55] — the 0x55 shows up in exactly these two fields,
    // and a probe that swallowed them would have thrown away the measurement.
    int n = snprintf(s_out, sizeof(s_out),
                     "< %s %s pre_drain=%lu pre_drain_hex=%s skipped=%lu skipped_hex=%s "
                     "rx_total=%lu elapsed_ms=%lu first_byte_ms=%lu",
                     seq, txn_status(r), (unsigned long)st->pre_drain_count, pre,
                     (unsigned long)st->skipped_count, skip,
                     (unsigned long)st->rx_total, (unsigned long)st->elapsed_ms,
                     (unsigned long)st->first_byte_ms);

    if (r == R503_TXN_OK || r == R503_TXN_BAD_CHECKSUM) {
        // ⛔ content[0] IS A CONFIRMATION CODE ONLY ON AN ACKNOWLEDGE PACKET (PID
        //   0x07). On a command packet (PID 0x01 — which is what internal loopback
        //   returns) it is the OPCODE, and reporting it as `conf=` would assert a
        //   confirmation code where none exists. Naming the wrong field confidently
        //   is the same failure as agreeing with a wrong answer.
        if (pkt->pid == R503_PID_ACK && clen > 0) {
            uint8_t conf = pkt->content[0];
            const char *cn = conf_name(conf);
            snprintf(s_out + n, sizeof(s_out) - n,
                     " pid=0x%02X len_field=%u content_len=%u sum_rx=0x%04X"
                     " sum_calc=0x%04X conf=0x%02X conf_known=%d conf_name=%s"
                     " content=%s\n",
                     pkt->pid, pkt->length_field, pkt->content_len, pkt->checksum_rx,
                     pkt->checksum_calc, conf, cn ? 1 : 0, cn ? cn : "UNDOCUMENTED",
                     content);
        } else {
            snprintf(s_out + n, sizeof(s_out) - n,
                     " pid=0x%02X len_field=%u content_len=%u sum_rx=0x%04X"
                     " sum_calc=0x%04X conf=n/a conf_known=n/a"
                     " conf_name=not_an_ack_packet content=%s\n",
                     pkt->pid, pkt->length_field, pkt->content_len, pkt->checksum_rx,
                     pkt->checksum_calc, content);
        }
    } else {
        snprintf(s_out + n, sizeof(s_out) - n, "\n");
    }
    emit(s_out);
}

// ---------------------------------------------------------------------------
// Codec self-test — the negative control that needs no hardware at all.
// Runs on the real silicon, on demand, and PRINTS EVERY CASE so a pass is
// countable rather than asserted.
// ---------------------------------------------------------------------------
static int st_pass, st_fail;

static void st_check(const char *name, bool ok, const char *detail) {
    if (ok) {
        st_pass++;
        emitf("# SELFTEST PASS %-28s %s\n", name, detail);
    } else {
        st_fail++;
        emitf("# SELFTEST FAIL %-28s %s\n", name, detail);
    }
}

// Encode a known command and compare the whole frame against the manual's worked
// example, byte for byte.
static void st_worked(const char *name, const uint8_t *content, uint16_t clen,
                      uint16_t expect_sum) {
    uint8_t f[64];
    int n = r503_encode(f, sizeof(f), R503_DEFAULT_ADDRESS, R503_PID_COMMAND, content,
                        clen, false);
    uint16_t got = (n >= 2) ? (uint16_t)((f[n - 2] << 8) | f[n - 1]) : 0xFFFFu;
    char d[96];
    snprintf(d, sizeof(d), "expect sum=0x%04X got=0x%04X len_field=0x%04X", expect_sum,
             got, (uint16_t)(clen + 2));
    st_check(name, n > 0 && got == expect_sum, d);
}

// Feed a byte stream through the parser and report the terminal result.
static r503_parse_result_t st_feed(const uint8_t *buf, uint32_t n, r503_packet_t *pkt,
                                   uint32_t *skipped) {
    r503_parser_t p;
    r503_parser_reset(&p);
    r503_parse_result_t r = R503_PARSE_NEED_MORE;
    for (uint32_t i = 0; i < n; i++) {
        r = r503_parse_byte(&p, buf[i], pkt);
        if (r != R503_PARSE_NEED_MORE) break;
    }
    if (skipped) *skipped = p.skipped_count;
    return r;
}

static void run_selftest(const char *seq) {
    st_pass = st_fail = 0;

    // --- Encoder against the manual's worked examples. [CXC-R503-CHECKSUM-VERIFIED]
    { const uint8_t c[] = {0x01}; st_worked("enc_getimg_0x01", c, 1, 0x0005); }
    { const uint8_t c[] = {0x0A}; st_worked("enc_upimage_0x0A", c, 1, 0x000E); }
    { const uint8_t c[] = {0x39}; st_worked("enc_getalgver_0x39", c, 1, 0x003D); }
    { const uint8_t c[] = {0x3A}; st_worked("enc_getfwver_0x3A", c, 1, 0x003E); }
    { const uint8_t c[] = {0x3C}; st_worked("enc_readprodinfo_0x3C", c, 1, 0x0040); }
    { const uint8_t c[] = {0x35, 0x01, 0x50, 0x06, 0x00};
      st_worked("enc_auraled_0x35", c, 5, 0x0094); }
    { const uint8_t c[] = {0x31, 0xC8, 0x00, 0x01, 0x01, 0x01};
      st_worked("enc_autoenroll_0x31", c, 6, 0x0105); }

    // --- Full-frame byte equality against the AuraLedConfig trace in the manual.
    {
        const uint8_t expect[] = {0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x01, 0x00,
                                  0x07, 0x35, 0x01, 0x50, 0x06, 0x00, 0x00, 0x94};
        const uint8_t c[] = {0x35, 0x01, 0x50, 0x06, 0x00};
        uint8_t f[32];
        int n = r503_encode(f, sizeof(f), R503_DEFAULT_ADDRESS, R503_PID_COMMAND, c, 5,
                            false);
        bool ok = (n == (int)sizeof(expect)) && memcmp(f, expect, sizeof(expect)) == 0;
        st_check("enc_frame_byte_equal", ok, "whole 16-byte frame vs manual p.27 trace");
    }

    // --- ⛔ THE LENGTH TRAP. [CXC-R503-FRAMING]
    // Length 0x0003 must yield ONE content byte, not three. A parser that reads
    // Length as the content length overruns by exactly two bytes on every packet,
    // and the overrun looks like a plausible short frame.
    {
        const uint8_t ack[] = {0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x07,
                               0x00, 0x03, 0x00, 0x00, 0x0A};
        r503_packet_t pkt;
        uint32_t skipped = 0;
        r503_parse_result_t r = st_feed(ack, sizeof(ack), &pkt, &skipped);
        char d[128];
        snprintf(d, sizeof(d), "len_field=3 -> content_len=%u (must be 1) result=%d",
                 pkt.content_len, (int)r);
        st_check("parse_length_counts_cksum", r == R503_PARSE_OK && pkt.content_len == 1,
                 d);
    }

    // --- ⛔ THE UNSOLICITED 0x55. [CXC-R503-UNSOLICITED-0X55]
    // A bare 0x55 immediately in front of a valid ack must cost exactly one skipped
    // byte and must NOT phase-shift the frame.
    {
        const uint8_t s[] = {0x55, 0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x07,
                             0x00, 0x03, 0x00, 0x00, 0x0A};
        r503_packet_t pkt;
        uint32_t skipped = 0;
        r503_parse_result_t r = st_feed(s, sizeof(s), &pkt, &skipped);
        char d[128];
        snprintf(d, sizeof(d), "skipped=%lu (must be 1) conf=0x%02X result=%d",
                 (unsigned long)skipped, pkt.content_len ? pkt.content[0] : 0xFF,
                 (int)r);
        st_check("parse_absorbs_0x55", r == R503_PARSE_OK && skipped == 1, d);
    }

    // --- Resync from garbage, including a false partial header (EF EF 01 ...).
    {
        const uint8_t s[] = {0x55, 0xAA, 0xEF, 0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF,
                             0x07, 0x00, 0x03, 0x00, 0x00, 0x0A};
        r503_packet_t pkt;
        uint32_t skipped = 0;
        r503_parse_result_t r = st_feed(s, sizeof(s), &pkt, &skipped);
        char d[128];
        snprintf(d, sizeof(d), "skipped=%lu (must be 3) result=%d",
                 (unsigned long)skipped, (int)r);
        st_check("parse_resync_false_header", r == R503_PARSE_OK && skipped == 3, d);
    }

    // --- ⛔ NEGATIVE CONTROL: a corrupted checksum MUST be rejected, and the
    //     corrupt frame must still surrender its contents as evidence.
    {
        uint8_t s[] = {0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x07,
                       0x00, 0x03, 0x00, 0x00, 0x0B};  // 0x0A corrupted to 0x0B
        r503_packet_t pkt;
        r503_parse_result_t r = st_feed(s, sizeof(s), &pkt, NULL);
        char d[128];
        snprintf(d, sizeof(d), "sum_rx=0x%04X sum_calc=0x%04X result=%d (must be %d)",
                 pkt.checksum_rx, pkt.checksum_calc, (int)r, (int)R503_PARSE_BAD_CHECKSUM);
        st_check("neg_bad_checksum_rejected", r == R503_PARSE_BAD_CHECKSUM, d);
    }

    // --- ⛔ NEGATIVE CONTROL: single-bit corruption ANYWHERE in the content is
    //     caught. Walked over every content byte of a 16-byte-content ack.
    {
        uint8_t content[16];
        for (int i = 0; i < 16; i++) content[i] = (uint8_t)(0x10 + i);
        uint8_t f[64];
        int n = r503_encode(f, sizeof(f), R503_DEFAULT_ADDRESS, R503_PID_ACK, content, 16,
                            false);
        int caught = 0, tried = 0;
        for (int i = 9; i < n - 2; i++) {  // content bytes only
            uint8_t save = f[i];
            f[i] = (uint8_t)(save ^ 0x01);
            r503_packet_t pkt;
            r503_parse_result_t r = st_feed(f, (uint32_t)n, &pkt, NULL);
            tried++;
            if (r == R503_PARSE_BAD_CHECKSUM) caught++;
            f[i] = save;
        }
        char d[96];
        snprintf(d, sizeof(d), "caught %d of %d single-bit content corruptions", caught,
                 tried);
        st_check("neg_bitflip_walk", tried > 0 && caught == tried, d);
    }

    // --- ⛔ NEGATIVE CONTROL: the deliberate transmit-side fault actually faults.
    //     The `badsum` verb is only an instrument if it really corrupts.
    {
        const uint8_t c[] = {0x3A};
        uint8_t good[32], bad[32];
        int ng = r503_encode(good, sizeof(good), R503_DEFAULT_ADDRESS, R503_PID_COMMAND,
                             c, 1, false);
        int nb = r503_encode(bad, sizeof(bad), R503_DEFAULT_ADDRESS, R503_PID_COMMAND, c,
                             1, true);
        bool ok = (ng == nb) && memcmp(good, bad, (size_t)ng) != 0;
        char d[96];
        snprintf(d, sizeof(d), "good sum=0x%02X%02X bad sum=0x%02X%02X",
                 good[ng - 2], good[ng - 1], bad[nb - 2], bad[nb - 1]);
        st_check("neg_tx_fault_really_faults", ok, d);
    }

    // --- An absurd Length is reported, not trusted.
    {
        const uint8_t s[] = {0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x07, 0x00, 0x00};
        r503_packet_t pkt;
        r503_parse_result_t r = st_feed(s, sizeof(s), &pkt, NULL);
        char d[64];
        snprintf(d, sizeof(d), "len_field=0 result=%d", (int)r);
        st_check("parse_length_absurd", r == R503_PARSE_LENGTH_ABSURD, d);
    }

    // --- An incomplete frame NEVER completes. (No unbounded wait is possible here:
    //     the parser is fed a finite buffer, and the LINK enforces the deadline.)
    {
        const uint8_t s[] = {0xEF, 0x01, 0xFF, 0xFF, 0xFF, 0xFF, 0x07, 0x00, 0x03, 0x00};
        r503_packet_t pkt;
        r503_parse_result_t r = st_feed(s, sizeof(s), &pkt, NULL);
        st_check("parse_truncated_never_ok", r == R503_PARSE_NEED_MORE,
                 "truncated ack must stay NEED_MORE");
    }

    emitf("< %s %s pass=%d fail=%d\n", seq, st_fail == 0 ? "ok" : "fail", st_pass,
          st_fail);
}

// ---------------------------------------------------------------------------
// Verb dispatch
// ---------------------------------------------------------------------------

static bool hex_to_bytes(const char *s, uint8_t *out, uint16_t cap, uint16_t *n) {
    size_t len = strlen(s);
    if (len == 0 || (len % 2) != 0 || (len / 2) > cap) return false;
    for (size_t i = 0; i < len; i += 2) {
        char b[3] = {s[i], s[i + 1], 0};
        if (!isxdigit((unsigned char)b[0]) || !isxdigit((unsigned char)b[1])) return false;
        out[i / 2] = (uint8_t)strtol(b, NULL, 16);
    }
    *n = (uint16_t)(len / 2);
    return true;
}

static uint32_t arg_u32(char **tok, int ntok, int idx, uint32_t dflt) {
    if (idx >= ntok || tok[idx] == NULL) return dflt;
    return (uint32_t)strtoul(tok[idx], NULL, 0);
}

static void do_transaction(const char *seq, const uint8_t *content, uint16_t clen,
                           bool fault, uint32_t deadline, uint32_t interbyte) {
    r503_packet_t pkt;
    r503_txn_stats_t st;
    r503_txn_result_t r =
        r503_link_txn(content, clen, fault, deadline, interbyte, &pkt, &st);
    report_txn(seq, r, &pkt, &st);
}

static void handle(char *line) {
    char *tok[16];
    int ntok = 0;
    char *save = NULL;
    for (char *t = strtok_r(line, " \t\r\n", &save); t && ntok < 16;
         t = strtok_r(NULL, " \t\r\n", &save)) {
        tok[ntok++] = t;
    }
    if (ntok == 0) return;
    if (strcmp(tok[0], ">") != 0) {
        emitf("# ignored (no '>' sentinel): %s\n", tok[0]);
        return;
    }
    if (ntok < 3) {
        emitf("< %s err reason=short_line\n", ntok >= 2 ? tok[1] : "?");
        return;
    }
    const char *seq = tok[1];
    const char *verb = tok[2];
    char **a = &tok[3];
    int na = ntok - 3;

    if (strcmp(verb, "ping") == 0) {
        esp_chip_info_t ci;
        esp_chip_info(&ci);
        emitf("< %s ok fw=r503_probe build=%s chip_model=%d chip_rev=%d "
              "cores=%d tx_gpio=%d rx_gpio=%d baud=%d loopback=%d uptime_ms=%lu\n",
              seq, s_build, (int)ci.model, (int)ci.revision, (int)ci.cores,
              r503_link_tx_gpio(), r503_link_rx_gpio(), R503_BAUD,
              r503_link_loopback_is_on() ? 1 : 0,
              (unsigned long)(esp_timer_get_time() / 1000));
        return;
    }

    if (strcmp(verb, "pins") == 0) {
        if (na < 2) {
            emitf("< %s err reason=usage_pins_tx_rx\n", seq);
            return;
        }
        int tx = (int)strtol(a[0], NULL, 0);
        int rx = (int)strtol(a[1], NULL, 0);
        bool ok = r503_link_init(tx, rx);
        emitf("< %s %s tx_gpio=%d rx_gpio=%d\n", seq, ok ? "ok" : "err",
              r503_link_tx_gpio(), r503_link_rx_gpio());
        return;
    }

    if (strcmp(verb, "loopback") == 0) {
        bool on = (na >= 1) && (strcmp(a[0], "on") == 0 || strcmp(a[0], "1") == 0);
        bool ok = r503_link_set_loopback(on);
        emitf("< %s %s loopback=%d\n", seq, ok ? "ok" : "err",
              r503_link_loopback_is_on() ? 1 : 0);
        return;
    }

    if (strcmp(verb, "drain") == 0) {
        uint32_t ms = arg_u32(a, na, 0, 50);
        uint8_t keep[64];
        uint8_t kept = 0;
        uint32_t n = r503_link_drain(ms, keep, sizeof(keep), &kept);
        char hex[140] = "";
        hexcat(hex, sizeof(hex), keep, kept);
        emitf("< %s ok count=%lu hex=%s window_ms=%lu\n", seq, (unsigned long)n, hex,
              (unsigned long)ms);
        return;
    }

    if (strcmp(verb, "listen") == 0) {
        // Capture unsolicited traffic WITHOUT transmitting. This is how the power-on
        // and post-SoftRst 0x55 becomes a timestamped measurement.
        uint32_t ms = arg_u32(a, na, 0, 2000);
        static uint8_t buf[256];
        uint32_t first = 0;
        uint32_t n = r503_link_listen(ms, buf, sizeof(buf), &first);
        char hex[540] = "";
        hexcat(hex, sizeof(hex), buf, n < 256 ? n : 256);
        emitf("< %s ok count=%lu first_byte_ms=%lu hex=%s window_ms=%lu\n", seq,
              (unsigned long)n, (unsigned long)first, hex, (unsigned long)ms);
        return;
    }

    if (strcmp(verb, "selftest") == 0) {
        run_selftest(seq);
        return;
    }

    if (strcmp(verb, "cmd") == 0 || strcmp(verb, "badsum") == 0) {
        // The raw escape hatch. `badsum` transmits with a deliberately wrong
        // checksum — the transmit-side half of the negative control.
        if (na < 1) {
            emitf("< %s err reason=usage_cmd_hex\n", seq);
            return;
        }
        uint8_t content[R503_MAX_CONTENT];
        uint16_t clen = 0;
        if (!hex_to_bytes(a[0], content, sizeof(content), &clen)) {
            emitf("< %s err reason=bad_hex\n", seq);
            return;
        }
        uint32_t deadline = arg_u32(a, na, 1, 1000);
        uint32_t interbyte = arg_u32(a, na, 2, 60);
        do_transaction(seq, content, clen, strcmp(verb, "badsum") == 0, deadline,
                       interbyte);
        return;
    }

    if (strcmp(verb, "rawtx") == 0) {
        // ⛔ Put DELIBERATELY MALFORMED bytes on real UART hardware, verbatim: no
        //   header added, no length computed, no checksum appended. This is what
        //   makes the partial-frame timeout, the 0x55 desync and the false header
        //   TESTABLE ON THE WIRE instead of only in a unit test. A branch that
        //   cannot be exercised is a branch that gets reported as working forever.
        if (na < 1) {
            emitf("< %s err reason=usage_rawtx_hex\n", seq);
            return;
        }
        uint8_t bytes[R503_MAX_CONTENT];
        uint16_t blen = 0;
        if (!hex_to_bytes(a[0], bytes, sizeof(bytes), &blen)) {
            emitf("< %s err reason=bad_hex\n", seq);
            return;
        }
        r503_packet_t pkt;
        r503_txn_stats_t st;
        r503_txn_result_t r = r503_link_rawtx(bytes, blen, arg_u32(a, na, 1, 1000),
                                              arg_u32(a, na, 2, 60), &pkt, &st);
        report_txn(seq, r, &pkt, &st);
        return;
    }

    if (strcmp(verb, "fwver") == 0) {
        // [CXC-R503-EV-25](a) — GetFwVer 0x3A. Ack: len_field 0x0023 = 1 conf + 32
        // string + 2 checksum. The string is reported VERBATIM as hex AND as ASCII;
        // the hex is authoritative, because a terminal that renders a control byte
        // as nothing is one more instrument that can only report success.
        const uint8_t c[] = {0x3A};
        r503_packet_t pkt;
        r503_txn_stats_t st;
        r503_txn_result_t r =
            r503_link_txn(c, 1, false, arg_u32(a, na, 0, 1000), arg_u32(a, na, 1, 60),
                          &pkt, &st);
        report_txn(seq, r, &pkt, &st);
        if (r == R503_TXN_OK && pkt.content_len > 1) {
            char ascii[R503_MAX_CONTENT + 1];
            uint16_t k = 0;
            for (uint16_t i = 1; i < pkt.content_len && k < sizeof(ascii) - 1; i++) {
                uint8_t ch = pkt.content[i];
                ascii[k++] = (ch >= 0x20 && ch < 0x7F) ? (char)ch : '.';
            }
            ascii[k] = 0;
            emitf("# fwver_ascii=\"%s\" len_field=%u string_bytes=%u\n", ascii,
                  pkt.length_field, (unsigned)(pkt.content_len - 1));
        }
        return;
    }

    if (strcmp(verb, "prodinfo") == 0) {
        // [CXC-R503-EV-03] + [CXC-R503-EV-15].
        // ⚠ The response length is SELF-CONTRADICTORY in the manual by exactly 4
        //   bytes: the Length field says 0x0031 (49 = 1 conf + 46 ProdInfo + 2 sum)
        //   while the column header and the summed field rows say 50 ProdInfo bytes
        //   (which would be Length 0x0035). V1.4 changed the column and added a
        //   4-byte reserved row WITHOUT touching the Length field.
        // ⛔ SO WE COUNT THE BYTES ACTUALLY ON THE WIRE and report BOTH the declared
        //   length field and the real content byte count. Whichever the firmware
        //   emits, the driver must be told.
        const uint8_t c[] = {0x3C};
        r503_packet_t pkt;
        r503_txn_stats_t st;
        r503_txn_result_t r =
            r503_link_txn(c, 1, false, arg_u32(a, na, 0, 1500), arg_u32(a, na, 1, 60),
                          &pkt, &st);
        report_txn(seq, r, &pkt, &st);
        if (r == R503_TXN_OK && pkt.content_len >= 17) {
            char model[17];
            for (int i = 0; i < 16; i++) {
                uint8_t ch = pkt.content[1 + i];
                model[i] = (ch >= 0x20 && ch < 0x7F) ? (char)ch : (ch == 0 ? ' ' : '.');
            }
            model[16] = 0;
            // Trim trailing pad. "For a string, the insufficient part is 0x00."
            for (int i = 15; i >= 0 && model[i] == ' '; i--) model[i] = 0;
            uint16_t prodinfo_bytes = (uint16_t)(pkt.content_len - 1);
            emitf("# prodinfo len_field=0x%04X prodinfo_bytes=%u ev15_verdict=%s "
                  "model=\"%s\"\n",
                  pkt.length_field, prodinfo_bytes,
                  prodinfo_bytes == 46 ? "46_LENGTH_FIELD_WINS"
                                       : (prodinfo_bytes == 50 ? "50_COLUMN_WINS"
                                                               : "NEITHER_REPORT_IT"),
                  model);
        }
        return;
    }

    if (strcmp(verb, "led") == 0) {
        // [CXC-R503-EV-25](b) — AuraLedConfig 0x35, payload [0x35][Ctrl][Speed][Color][Times].
        // ⛔ A confirmation code of 0x00 means the command was ACCEPTED. It NEVER
        //   means the intended colour appeared. The colour half of EV-25 cannot be
        //   machine-checked and this firmware does not pretend otherwise — the host
        //   CLI stops and asks a human to NAME what they see.
        if (na < 4) {
            emitf("< %s err reason=usage_led_ctrl_speed_color_times\n", seq);
            return;
        }
        uint8_t c[5] = {0x35, (uint8_t)strtol(a[0], NULL, 0),
                        (uint8_t)strtol(a[1], NULL, 0), (uint8_t)strtol(a[2], NULL, 0),
                        (uint8_t)strtol(a[3], NULL, 0)};
        do_transaction(seq, c, 5, false, arg_u32(a, na, 4, 1000), arg_u32(a, na, 5, 60));
        return;
    }

    emitf("< %s err reason=unknown_verb verb=%s\n", seq, verb);
}

void app_main(void) {
    build_stamp_init();
    usb_serial_jtag_driver_config_t ucfg = USB_SERIAL_JTAG_DRIVER_CONFIG_DEFAULT();
    ucfg.rx_buffer_size = 1024;
    ucfg.tx_buffer_size = 2048;
    usb_serial_jtag_driver_install(&ucfg);
    usb_serial_jtag_vfs_use_driver();

    bool ok = r503_link_init(R503_TX_GPIO, R503_RX_GPIO);

    // Banner is a '#' line: the host ignores it, a human reads it.
    emitf("# r503_probe build=%s uart_ok=%d tx_gpio=%d rx_gpio=%d baud=%d\n", s_build,
          ok ? 1 : 0, R503_TX_GPIO, R503_RX_GPIO, R503_BAUD);
    emit("# ready. protocol: \"> <seq> <verb> [args]\" -> \"< <seq> <status> k=v ...\"\n");

    char line[PROBE_LINE_MAX];
    size_t len = 0;
    uint32_t last_byte_ms = 0;
    uint8_t b;

    for (;;) {
        // ⛔ BOUNDED read even here. A host that opens the port and then stops must
        //   not be able to wedge the probe, and a half-delivered command line must
        //   not sit in the buffer forever waiting for a newline that never comes.
        int n = usb_serial_jtag_read_bytes(&b, 1, pdMS_TO_TICKS(50));
        uint32_t t = (uint32_t)(esp_timer_get_time() / 1000);

        if (n <= 0) {
            if (len > 0 && (t - last_byte_ms) > LINE_STALL_MS) {
                emitf("# discarded stalled partial line (%u bytes)\n", (unsigned)len);
                len = 0;
            }
            continue;
        }
        last_byte_ms = t;

        if (b == '\n' || b == '\r') {
            if (len == 0) continue;
            line[len] = 0;
            handle(line);
            len = 0;
            continue;
        }
        if (len < PROBE_LINE_MAX - 1) {
            line[len++] = (char)b;
        } else {
            emit("# line too long, discarded\n");
            len = 0;
        }
    }
}
