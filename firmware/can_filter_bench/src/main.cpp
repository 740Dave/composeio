/*
 * can_filter_bench — ESP32-C3 TWAI acceptance-filter capability bench
 * ===================================================================
 *
 * PURPOSE
 * -------
 * Answer, empirically and falsifiably, one question:
 *
 *   Can an ESP32-C3 TWAI acceptance filter match on DATA BYTES for EXTENDED
 *   (29-bit) frames, or only on the identifier?
 *
 * The answer decides which of two incompatible 29-bit CAN arbitration-id
 * layouts ComposeBus adopts. See docs/research/CAN-ACCEPTANCE-FILTER-RESEARCH.md
 * for the framing, the wiring, and the per-experiment protocol. This file is
 * only the instrument.
 *
 * ONE BINARY, EVERY ROLE
 * ----------------------
 * There is no transmitter build, no receiver build, and no loopback build. Every
 * board is flashed with byte-identical firmware and told what to be at runtime
 * over the serial REPL. One artefact, behaviour from configuration — and no way
 * to mix up which board has which build.
 *
 * TWO WAYS TO RUN IT
 * ------------------
 * TRACK A — ONE board, ONE jumper wire from TWAI_TX_GPIO to TWAI_RX_GPIO, no
 *   transceiver, no bus, no termination. `INIT <k> no-ack` + `SELF ON` uses the
 *   controller's Self Reception Request: the frame is transmitted, loops back
 *   through the jumper, and is received by the SAME node — THROUGH THE
 *   ACCEPTANCE FILTER. The SJA1000 family documents this explicitly: "upon self
 *   reception request a message is transmitted and simultaneously received IF
 *   THE ACCEPTANCE FILTER IS SET TO THE CORRESPONDING IDENTIFIER." Espressif's
 *   own twai_self_test example sanctions the bare TX-to-RX jumper.
 *   Track A answers every E1 question with the entire physical layer removed as
 *   a confounder. It is the primary path.
 * TRACK B — TWO boards, two transceivers, a terminated differential bus.
 *   Confirms Track A on real wire and is REQUIRED for the contention (E2) and
 *   cross-decode (E3) experiments. `ROLE tx` / `ROLE rx`, `SELF OFF`.
 *
 * OUTPUT CONTRACT (parse this, don't screen-scrape)
 * -------------------------------------------------
 * Every line begins with a stable prefix and carries space-separated key=value
 * tokens. No value ever contains a space.
 *
 *   # ...        human commentary / banner / help. Ignore when parsing.
 *   OK <CMD> ... command accepted; tokens describe what was applied.
 *   ERR <CMD> reason=<token>    command rejected. Nothing changed.
 *   EVT <KIND> ...              an asynchronous event (RX, ALERT, TRIGGERED).
 *   STAT <GROUP> ...            one line of a STATS report.
 *   ID ...                      identity / build stamp.
 *   END <CMD>                   end of a multi-line response.
 *
 * HARDWARE FACTS THIS FIRMWARE ENCODES (all verified against primary sources)
 * --------------------------------------------------------------------------
 *  - ESP32-C3 has exactly ONE TWAI controller (ESP32-C3 TRM v1.4 ch.31).
 *  - Acceptance filter, SINGLE mode (TRM v1.4 §31.4.6.1):
 *      SFF: entire 11-bit ID + RTR + data byte 1 + data byte 2
 *      EFF: entire 29-bit ID + RTR          <-- NO data bytes
 *  - Acceptance filter, DUAL mode (TRM v1.4 §31.4.6.2):
 *      SFF: entire 11-bit ID + RTR + data byte 1 (filter 1 only)
 *      EFF: the FIRST 16 BITS of the 29-bit ID (i.e. ID[28:13]) only
 *  - Acceptance mask semantics are SJA1000/AMR: a mask bit of 1 means
 *    DON'T CARE. `TWAI_FILTER_CONFIG_ACCEPT_ALL()` is code=0, mask=0xFFFFFFFF.
 *  - ESP-IDF writes the 32-bit acceptance code MSB-first into ACR0..ACR3
 *    (twai_ll_set_acc_filter byte-swaps), so bit 31 of `acceptance_code` is
 *    ACR0 bit 7. The `CALC` command below derives every literal from that.
 *  - A node that filters a message out still ACKNOWLEDGES it at the protocol
 *    level. Acceptance filtering is a reception concern, not a bus concern.
 *
 * The documentation above is what we EXPECT. The point of this rig is to try to
 * catch the silicon disagreeing with it. Nothing here short-circuits a test to
 * the documented answer.
 */

#include <Arduino.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "driver/gpio.h"
#include "driver/twai.h"
#include "esp_idf_version.h"
#include "esp_system.h"
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 0, 0)
#include "esp_chip_info.h"
#endif

#ifndef TWAI_TX_GPIO
#define TWAI_TX_GPIO 3
#endif
#ifndef TWAI_RX_GPIO
#define TWAI_RX_GPIO 10
#endif
#ifndef TRIG_GPIO
#define TRIG_GPIO 1
#endif
#ifndef FW_VERSION
#define FW_VERSION "1.0.0"
#endif

#define FW_NAME "can_filter_bench"

/* Every alert except TX_IDLE, which fires whenever the TX queue drains and adds
 * nothing diagnostic. RX_DATA and RX_QUEUE_FULL are deliberately KEPT: they are
 * how experiment E1e observes whether a frame reached the CPU at all. */
static const uint32_t ALERTS_WATCHED = TWAI_ALERT_ALL & ~TWAI_ALERT_TX_IDLE;

/* -------------------------------------------------------------------------- */
/* output                                                                     */
/* -------------------------------------------------------------------------- */

static void emit(const char *fmt, ...) {
  /* 320, not 240: the `STAT alert` line is ~210 characters with single-digit
   * counters and grows past 260 once bus_error reaches six figures — which is
   * precisely the run where you most need the line intact. */
  char buf[320];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  Serial.println(buf);
}

/* -------------------------------------------------------------------------- */
/* state                                                                      */
/* -------------------------------------------------------------------------- */

struct IdTally {
  bool used;
  bool ext;
  uint32_t id;
  uint32_t n;
};

static const int TALLY_MAX = 32;
static IdTally g_tally[TALLY_MAX];

static bool g_installed = false;
static uint32_t g_bitrate_kbps = 500;
static twai_mode_t g_mode = TWAI_MODE_NORMAL;
static const char *g_mode_name = "normal";

static uint32_t g_filter_code = 0x00000000;
static uint32_t g_filter_mask = 0xFFFFFFFF; /* accept all */
static bool g_filter_single = true;

static uint32_t g_rx_queue_len = 16;
static bool g_drain = true;     /* call twai_receive() in the service loop */
static bool g_rx_print = true;  /* emit EVT RX lines */
static bool g_single_shot = true;
static bool g_self = false; /* TWAI_MSG_FLAG_SELF — Track A loopback */
static bool g_echo = true;
static const char *g_role = "idle";
static const char *g_rxpull = "none";

/* Runtime-settable TWAI pins (default = the -D build flags).
 * ADDED 2026-08-10 during the E1 run. Reason: E1-CONTROL failed with every
 * frame tx_failed and tec rocketing to 136 from a SINGLE frame — the signature
 * of dominant bits never being read back, i.e. TX not reaching RX. With the
 * pins frozen at compile time there is no way to tell "the jumper is in the
 * wrong holes" from "the controller is broken" without reflashing per guess.
 * `PINS <tx> <rx>` (applied at next INIT) makes both a one-line test, and
 * `PINS 3 3` gives a pad-level internal loopback that needs no wire at all. */
static int g_tx_gpio = TWAI_TX_GPIO;
static int g_rx_gpio = TWAI_RX_GPIO;

static uint32_t g_tx_ok = 0, g_tx_fail = 0, g_tx_err = 0;
static uint32_t g_rx_accepted = 0, g_rx_tally_overflow = 0;

/* Alert counters. NOTE THE SEMANTICS: twai_read_alerts() latches and clears, so
 * N frames arriving between two reads set RX_DATA once. These are "number of
 * read_alerts() calls in which the flag was set", a LOWER BOUND on events, and
 * they are labelled as such in the STATS output. For exact numbers use the
 * hardware counters in `STAT hw` (rx_missed, bus_error, arb_lost, ...). */
struct AlertCounts {
  uint32_t tx_success, rx_data, below_warn, err_active, recovery, recovered;
  uint32_t arb_lost, above_warn, bus_error, tx_failed, rx_queue_full;
  uint32_t err_pass, bus_off, rx_fifo_overrun, tx_retried, periph_reset;
};
static AlertCounts g_al;

static const uint32_t ALERT_LINES_PER_SEC = 8;
static uint32_t g_alert_win_start = 0, g_alert_win_n = 0, g_alert_suppressed = 0;

/* E2 trigger / arbitration-contention primitives (see research doc §7, E2). */
static enum { TRIG_OFF, TRIG_MASTER, TRIG_SLAVE } g_trig = TRIG_OFF;
static twai_message_t g_armed;
static bool g_armed_valid = false;
static bool g_trig_prev = false;

/* -------------------------------------------------------------------------- */
/* helpers                                                                    */
/* -------------------------------------------------------------------------- */

static uint32_t crc32_str(const char *s) {
  uint32_t crc = 0xFFFFFFFFu;
  for (const char *p = s; *p; ++p) {
    crc ^= (uint8_t)*p;
    for (int i = 0; i < 8; ++i)
      crc = (crc >> 1) ^ (0xEDB88320u & (uint32_t)(-(int32_t)(crc & 1)));
  }
  return ~crc;
}

static bool parse_u32(const char *s, uint32_t *out) {
  if (!s || !*s) return false;
  char *end = nullptr;
  int base = 10;
  if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) { s += 2; base = 16; }
  else if (strpbrk(s, "abcdefABCDEF")) base = 16;
  unsigned long v = strtoul(s, &end, base);
  if (end == s || (end && *end)) return false;
  *out = (uint32_t)v;
  return true;
}

/* Always-hex parser, for fields that are hex by definition (ids, codes). */
static bool parse_hex(const char *s, uint32_t *out) {
  if (!s || !*s) return false;
  if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
  if (!*s) return false;
  char *end = nullptr;
  unsigned long v = strtoul(s, &end, 16);
  if (end == s || (end && *end)) return false;
  *out = (uint32_t)v;
  return true;
}

/* "a55a00ff" -> 4 bytes. "-" or "" -> 0 bytes. Returns -1 on malformed input. */
static int parse_bytes(const char *s, uint8_t *out, int max) {
  if (!s || !*s || (s[0] == '-' && s[1] == 0)) return 0;
  if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) s += 2;
  int len = (int)strlen(s);
  if (len & 1) return -1;
  if (len / 2 > max) return -1;
  for (int i = 0; i < len; i += 2) {
    char pair[3] = {s[i], s[i + 1], 0};
    char *end = nullptr;
    unsigned long b = strtoul(pair, &end, 16);
    if (end != pair + 2) return -1;
    out[i / 2] = (uint8_t)b;
  }
  return len / 2;
}

static void bytes_to_hex(const uint8_t *b, int n, char *out) {
  if (n <= 0) { out[0] = '-'; out[1] = 0; return; }
  for (int i = 0; i < n; ++i) sprintf(out + 2 * i, "%02x", b[i]);
  out[2 * n] = 0;
}

static const char *state_name(twai_state_t s) {
  switch (s) {
    case TWAI_STATE_STOPPED:    return "STOPPED";
    case TWAI_STATE_RUNNING:    return "RUNNING";
    case TWAI_STATE_BUS_OFF:    return "BUS_OFF";
    case TWAI_STATE_RECOVERING: return "RECOVERING";
    default:                    return "UNKNOWN";
  }
}

static void tally(bool ext, uint32_t id) {
  for (int i = 0; i < TALLY_MAX; ++i) {
    if (g_tally[i].used && g_tally[i].ext == ext && g_tally[i].id == id) {
      g_tally[i].n++;
      return;
    }
  }
  for (int i = 0; i < TALLY_MAX; ++i) {
    if (!g_tally[i].used) {
      g_tally[i] = {true, ext, id, 1};
      return;
    }
  }
  g_rx_tally_overflow++;
}

static void clear_sw_counters() {
  memset(g_tally, 0, sizeof(g_tally));
  memset(&g_al, 0, sizeof(g_al));
  g_tx_ok = g_tx_fail = g_tx_err = 0;
  g_rx_accepted = 0;
  g_rx_tally_overflow = 0;
  g_alert_suppressed = 0;
  g_alert_win_start = millis();
  g_alert_win_n = 0;
}

static bool timing_for(uint32_t kbps, twai_timing_config_t *out) {
  if (kbps == 25)   { twai_timing_config_t t = TWAI_TIMING_CONFIG_25KBITS();   *out = t; return true; }
  if (kbps == 50)   { twai_timing_config_t t = TWAI_TIMING_CONFIG_50KBITS();   *out = t; return true; }
  if (kbps == 100)  { twai_timing_config_t t = TWAI_TIMING_CONFIG_100KBITS();  *out = t; return true; }
  if (kbps == 125)  { twai_timing_config_t t = TWAI_TIMING_CONFIG_125KBITS();  *out = t; return true; }
  if (kbps == 250)  { twai_timing_config_t t = TWAI_TIMING_CONFIG_250KBITS();  *out = t; return true; }
  if (kbps == 500)  { twai_timing_config_t t = TWAI_TIMING_CONFIG_500KBITS();  *out = t; return true; }
  if (kbps == 800)  { twai_timing_config_t t = TWAI_TIMING_CONFIG_800KBITS();  *out = t; return true; }
  if (kbps == 1000) { twai_timing_config_t t = TWAI_TIMING_CONFIG_1MBITS();    *out = t; return true; }
  return false;
}

/* -------------------------------------------------------------------------- */
/* driver lifecycle                                                           */
/* -------------------------------------------------------------------------- */

static void teardown() {
  if (!g_installed) return;
  twai_stop();
  twai_driver_uninstall();
  g_installed = false;
}

/* Installs (or re-installs) the driver with the CURRENT bitrate/mode/filter.
 * Re-installing is the ONLY way to zero the hardware counters, so a re-install
 * also zeroes the software counters — a STATS report therefore always describes
 * exactly one filter configuration. That invariant is what makes the results
 * interpretable; do not "optimise" it away. */
static bool bring_up(const char *cmd) {
  teardown();

  twai_timing_config_t t;
  if (!timing_for(g_bitrate_kbps, &t)) {
    emit("ERR %s reason=unsupported_bitrate", cmd);
    return false;
  }

  twai_general_config_t g = TWAI_GENERAL_CONFIG_DEFAULT(
      (gpio_num_t)g_tx_gpio, (gpio_num_t)g_rx_gpio, g_mode);
  g.tx_queue_len = 32;
  g.rx_queue_len = g_rx_queue_len;
  g.alerts_enabled = ALERTS_WATCHED;

  twai_filter_config_t f;
  f.acceptance_code = g_filter_code;
  f.acceptance_mask = g_filter_mask;
  f.single_filter = g_filter_single;

  esp_err_t err = twai_driver_install(&g, &t, &f);
  if (err != ESP_OK) {
    emit("ERR %s reason=install_failed esp_err=0x%x", cmd, (unsigned)err);
    return false;
  }
  err = twai_start();
  if (err != ESP_OK) {
    twai_driver_uninstall();
    emit("ERR %s reason=start_failed esp_err=0x%x", cmd, (unsigned)err);
    return false;
  }
  g_installed = true;
  clear_sw_counters();
  return true;
}

/* -------------------------------------------------------------------------- */
/* service loop                                                               */
/* -------------------------------------------------------------------------- */

static void service_alerts() {
  if (!g_installed) return;
  uint32_t a = 0;
  if (twai_read_alerts(&a, 0) != ESP_OK || a == 0) return;
  if (a & TWAI_ALERT_TX_SUCCESS)           g_al.tx_success++;
  if (a & TWAI_ALERT_RX_DATA)              g_al.rx_data++;
  if (a & TWAI_ALERT_BELOW_ERR_WARN)       g_al.below_warn++;
  if (a & TWAI_ALERT_ERR_ACTIVE)           g_al.err_active++;
  if (a & TWAI_ALERT_RECOVERY_IN_PROGRESS) g_al.recovery++;
  if (a & TWAI_ALERT_BUS_RECOVERED)        g_al.recovered++;
  if (a & TWAI_ALERT_ARB_LOST)             g_al.arb_lost++;
  if (a & TWAI_ALERT_ABOVE_ERR_WARN)       g_al.above_warn++;
  if (a & TWAI_ALERT_BUS_ERROR)            g_al.bus_error++;
  if (a & TWAI_ALERT_TX_FAILED)            g_al.tx_failed++;
  if (a & TWAI_ALERT_RX_QUEUE_FULL)        g_al.rx_queue_full++;
  if (a & TWAI_ALERT_ERR_PASS)             g_al.err_pass++;
  if (a & TWAI_ALERT_BUS_OFF)              g_al.bus_off++;
#ifdef TWAI_ALERT_RX_FIFO_OVERRUN
  if (a & TWAI_ALERT_RX_FIFO_OVERRUN)      g_al.rx_fifo_overrun++;
#endif
#ifdef TWAI_ALERT_TX_RETRIED
  if (a & TWAI_ALERT_TX_RETRIED)           g_al.tx_retried++;
#endif
#ifdef TWAI_ALERT_PERIPH_RESET
  if (a & TWAI_ALERT_PERIPH_RESET)         g_al.periph_reset++;
#endif
  /* Loud on the ones that invalidate a run — but RATE LIMITED. A bus that is
   * genuinely broken (RX stuck recessive, no transceiver power, wrong bitrate)
   * produces a continuous bus-error storm, and an unthrottled EVT ALERT line per
   * read saturates the USB-CDC link and makes the REPL look HUNG. A hung port is
   * the worst possible presentation of a diagnosable fault. Suppressed lines are
   * counted and reported in STATS, so nothing is hidden — only throttled. */
  if (a & (TWAI_ALERT_BUS_OFF | TWAI_ALERT_ERR_PASS | TWAI_ALERT_BUS_ERROR |
           TWAI_ALERT_TX_FAILED | TWAI_ALERT_RX_QUEUE_FULL)) {
    uint32_t now = millis();
    if (now - g_alert_win_start >= 1000) { g_alert_win_start = now; g_alert_win_n = 0; }
    if (g_alert_win_n < ALERT_LINES_PER_SEC) {
      g_alert_win_n++;
      emit("EVT ALERT flags=0x%08x t=%lu", (unsigned)a, (unsigned long)now);
      if (g_alert_win_n == ALERT_LINES_PER_SEC)
        emit("EVT ALERT_THROTTLED note=further_alerts_this_second_suppressed_see_STATS");
    } else {
      g_alert_suppressed++;
    }
  }
}

static void service_rx() {
  if (!g_installed || !g_drain) return;
  twai_message_t m;
  for (int i = 0; i < 24; ++i) {
    if (twai_receive(&m, 0) != ESP_OK) return;
    g_rx_accepted++;
    tally(m.extd != 0, m.identifier);
    if (g_rx_print) {
      char hex[20];
      bytes_to_hex(m.data, m.data_length_code > 8 ? 8 : m.data_length_code, hex);
      emit("EVT RX ext=%d rtr=%d id=0x%08x dlc=%d data=%s t=%lu",
           m.extd ? 1 : 0, m.rtr ? 1 : 0, (unsigned)m.identifier,
           (int)m.data_length_code, hex, (unsigned long)millis());
    }
  }
}

static void service_trigger() {
  if (g_trig != TRIG_SLAVE) return;
  bool now = digitalRead((gpio_num_t)TRIG_GPIO) != 0;
  if (now && !g_trig_prev && g_armed_valid) {
    esp_err_t e = twai_transmit(&g_armed, 0);
    g_armed_valid = false;
    emit("EVT TRIGGERED queued=%d esp_err=0x%x t=%lu", e == ESP_OK ? 1 : 0,
         (unsigned)e, (unsigned long)millis());
  }
  g_trig_prev = now;
}

static void service() {
  service_alerts();
  service_rx();
  service_trigger();
}

/* -------------------------------------------------------------------------- */
/* commands                                                                   */
/* -------------------------------------------------------------------------- */

static void cmd_id() {
  esp_chip_info_t ci;
  esp_chip_info(&ci);
  uint64_t mac = ESP.getEfuseMac();
  char macs[13];
  for (int i = 0; i < 6; ++i) sprintf(macs + 2 * i, "%02x", (unsigned)((mac >> (8 * i)) & 0xFF));
  macs[12] = 0;

  static const char stampsrc[] = FW_NAME "|" FW_VERSION "|" __DATE__ "|" __TIME__;
  char built[40];
  snprintf(built, sizeof(built), "%s_%s", __DATE__, __TIME__);
  for (char *p = built; *p; ++p) if (*p == ' ') *p = '_';

  emit("ID fw=%s ver=%s stamp=%08x built=%s", FW_NAME, FW_VERSION,
       (unsigned)crc32_str(stampsrc), built);
  emit("ID chip=esp32c3 rev=%d cores=%d mac=%s idf=%d.%d.%d",
       (int)ci.revision, (int)ci.cores, macs, ESP_IDF_VERSION_MAJOR,
       ESP_IDF_VERSION_MINOR, ESP_IDF_VERSION_PATCH);
  emit("ID tx_gpio=%d rx_gpio=%d trig_gpio=%d role=%s cdc=%d", g_tx_gpio,
       g_rx_gpio, TRIG_GPIO, g_role,
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT
       1
#else
       0
#endif
  );
  emit("END ID");
}

static void cmd_stats() {
  emit("STAT cfg role=%s installed=%d bitrate_kbps=%lu mode=%s single_shot=%d "
       "self=%d drain=%d rx_print=%d",
       g_role, g_installed ? 1 : 0, (unsigned long)g_bitrate_kbps, g_mode_name,
       g_single_shot ? 1 : 0, g_self ? 1 : 0, g_drain ? 1 : 0,
       g_rx_print ? 1 : 0);
  emit("STAT filter code=0x%08x mask=0x%08x mode=%s rx_queue_len=%lu rxpull=%s",
       (unsigned)g_filter_code, (unsigned)g_filter_mask,
       g_filter_single ? "single" : "dual", (unsigned long)g_rx_queue_len,
       g_rxpull);
  emit("STAT pins tx_gpio=%d rx_gpio=%d", g_tx_gpio, g_rx_gpio);
  emit("STAT tx ok=%lu fail=%lu queue_err=%lu", (unsigned long)g_tx_ok,
       (unsigned long)g_tx_fail, (unsigned long)g_tx_err);
  emit("STAT rx accepted=%lu tally_overflow=%lu",
       (unsigned long)g_rx_accepted, (unsigned long)g_rx_tally_overflow);
  for (int i = 0; i < TALLY_MAX; ++i)
    if (g_tally[i].used)
      emit("STAT rxid ext=%d id=0x%08x n=%lu", g_tally[i].ext ? 1 : 0,
           (unsigned)g_tally[i].id, (unsigned long)g_tally[i].n);

  if (g_installed) {
    twai_status_info_t s;
    if (twai_get_status_info(&s) == ESP_OK) {
      emit("STAT hw state=%s tec=%lu rec=%lu msgs_to_tx=%lu msgs_to_rx=%lu "
           "tx_failed=%lu rx_missed=%lu rx_overrun=%lu arb_lost=%lu bus_error=%lu",
           state_name(s.state), (unsigned long)s.tx_error_counter,
           (unsigned long)s.rx_error_counter, (unsigned long)s.msgs_to_tx,
           (unsigned long)s.msgs_to_rx, (unsigned long)s.tx_failed_count,
           (unsigned long)s.rx_missed_count, (unsigned long)s.rx_overrun_count,
           (unsigned long)s.arb_lost_count, (unsigned long)s.bus_error_count);
    } else {
      emit("STAT hw state=UNAVAILABLE");
    }
  } else {
    emit("STAT hw state=NOT_INSTALLED");
  }

  emit("STAT alert tx_success=%lu rx_data=%lu below_warn=%lu err_active=%lu "
       "recovery=%lu recovered=%lu arb_lost=%lu above_warn=%lu bus_error=%lu "
       "tx_failed=%lu rx_queue_full=%lu err_pass=%lu bus_off=%lu "
       "rx_fifo_overrun=%lu tx_retried=%lu periph_reset=%lu",
       (unsigned long)g_al.tx_success, (unsigned long)g_al.rx_data,
       (unsigned long)g_al.below_warn, (unsigned long)g_al.err_active,
       (unsigned long)g_al.recovery, (unsigned long)g_al.recovered,
       (unsigned long)g_al.arb_lost, (unsigned long)g_al.above_warn,
       (unsigned long)g_al.bus_error, (unsigned long)g_al.tx_failed,
       (unsigned long)g_al.rx_queue_full, (unsigned long)g_al.err_pass,
       (unsigned long)g_al.bus_off, (unsigned long)g_al.rx_fifo_overrun,
       (unsigned long)g_al.tx_retried, (unsigned long)g_al.periph_reset);
  emit("STAT alert2 lines_suppressed=%lu", (unsigned long)g_alert_suppressed);
  emit("STAT note alert_counts=read_alerts_calls_with_flag_set_NOT_frame_counts");
  emit("END STATS");
}

static void cmd_help() {
  emit("# %s %s — TWAI acceptance-filter bench. One binary, every role.", FW_NAME, FW_VERSION);
  emit("# Track A = one board + a TX-to-RX jumper: ROLE loop; INIT <kbps> no-ack");
  emit("# Track B = two boards on a bus:           ROLE tx / ROLE rx; INIT <kbps> normal");
  emit("# HELP                                  this text");
  emit("# ID                                    chip, MAC, firmware build stamp");
  emit("# ROLE tx|rx|loop|idle                  label + defaults; 'loop' = Track A");
  emit("# SELF ON|OFF                           self-reception (Track A: needs no-ack + jumper)");
  emit("# INIT <kbps> <normal|listen-only|no-ack>  (re)install TWAI; ZEROES ALL COUNTERS");
  emit("# DEINIT                                stop + uninstall");
  emit("# FILTER <code_hex> <mask_hex> <single|dual>  set filter; re-inits; ZEROES COUNTERS");
  emit("#                                       mask bit 1 = DON'T CARE (SJA1000 AMR)");
  emit("# CALC ext <id_hex>                     code/mask for exact 29-bit single filter");
  emit("# CALC std <id_hex> <d0_hex> <d1_hex>   code/mask for 11-bit + data bytes 1..2");
  emit("# CALC dual <id1_hex> <id2_hex>         code/mask for dual-filter extended");
  emit("# SEND ext|std <id_hex> <data_hex|->    transmit one frame");
  emit("# BURST ext|std <id_hex> <data_hex|-> <count> <interval_ms>");
  emit("# RX ON|OFF                             print EVT RX lines (counting is always on)");
  emit("# DRAIN ON|OFF                          call twai_receive() at all (E1e)");
  emit("# RXQ <n>                               rx queue length, applied at next INIT (E1e)");
  emit("# SS ON|OFF                             single-shot TX (default ON; no infinite retry)");
  emit("# STATS                                 counters, per-id tallies, hw + alert state");
  emit("# CLEAR                                 zero SOFTWARE counters only");
  emit("# RECOVER                               twai_initiate_recovery() after bus-off");
  emit("# RXPULL up|down|none                   force RX pin pull — reproduces both");
  emit("#                                       'wire is wrong' signatures with no hardware");
  emit("# PINS <tx> <rx>                        retarget TWAI pins at next INIT");
  emit("#                                       PINS 3 3 = pad-level loopback, no jumper");
  emit("# PROBE <drv> <sense>                   DC continuity between two pads (DEINIT first)");
  emit("# TRIG master|slave|off                 E2 contention trigger line on GPIO%d", TRIG_GPIO);
  emit("# ARM ext|std <id_hex> <data_hex|->     preload contender frame (slave)");
  emit("# FIRE                                  master: starter frame + trigger pulse");
  emit("# ECHO ON|OFF                           echo command lines back as '# >' comments");
  emit("END HELP");
}

/* Filter-literal derivation. Kept in FIRMWARE so the bench can cross-check the
 * hex literals printed in the research document against numbers computed on the
 * silicon under test — an independent check on the document's arithmetic. */
static void cmd_calc(char **tok, int n) {
  if (n < 2) { emit("ERR CALC reason=missing_kind"); return; }
  if (!strcasecmp(tok[1], "ext")) {
    uint32_t id;
    if (n < 3 || !parse_hex(tok[2], &id) || id > 0x1FFFFFFF) {
      emit("ERR CALC reason=bad_ext_id"); return;
    }
    /* Single filter, EFF: code bits 31..3 = ID28..0, bit 2 = RTR, 1..0 unused. */
    emit("OK CALC kind=ext_single id=0x%08x code=0x%08x mask_exact=0x%08x "
         "mask_top16=0x%08x", (unsigned)id, (unsigned)(id << 3), 0x00000007u,
         0x0000FFFFu | 0x7u);
    return;
  }
  if (!strcasecmp(tok[1], "std")) {
    uint32_t id, d0, d1;
    if (n < 5 || !parse_hex(tok[2], &id) || id > 0x7FF ||
        !parse_hex(tok[3], &d0) || d0 > 0xFF || !parse_hex(tok[4], &d1) || d1 > 0xFF) {
      emit("ERR CALC reason=bad_std_args"); return;
    }
    /* Single filter, SFF: 31..21 = ID10..0, 20 = RTR, 19..16 unused,
     *                     15..8  = data byte 1, 7..0 = data byte 2. */
    uint32_t code = (id << 21) | (d0 << 8) | d1;
    emit("OK CALC kind=std_single id=0x%03x d0=0x%02x d1=0x%02x code=0x%08x "
         "mask_data_only=0x%08x mask_id_and_data=0x%08x mask_id_only=0x%08x",
         (unsigned)id, (unsigned)d0, (unsigned)d1, (unsigned)code,
         0xFFFF0000u, 0x001F0000u, 0x001FFFFFu);
    return;
  }
  if (!strcasecmp(tok[1], "dual")) {
    uint32_t id1, id2;
    if (n < 4 || !parse_hex(tok[2], &id1) || !parse_hex(tok[3], &id2) ||
        id1 > 0x1FFFFFFF || id2 > 0x1FFFFFFF) {
      emit("ERR CALC reason=bad_dual_args"); return;
    }
    /* Dual filter, EFF: filter1 = code[31:16] vs ID[28:13];
     *                   filter2 = code[15:0]  vs ID[28:13]. */
    uint32_t f1 = (id1 >> 13) & 0xFFFF, f2 = (id2 >> 13) & 0xFFFF;
    emit("OK CALC kind=dual_ext id1=0x%08x id2=0x%08x f1_top16=0x%04x "
         "f2_top16=0x%04x code=0x%08x mask_exact=0x%08x",
         (unsigned)id1, (unsigned)id2, (unsigned)f1, (unsigned)f2,
         (unsigned)((f1 << 16) | f2), 0x00000000u);
    return;
  }
  emit("ERR CALC reason=unknown_kind");
}

static bool build_frame(const char *kind, const char *ids, const char *datas,
                        twai_message_t *m, const char *cmd) {
  memset(m, 0, sizeof(*m));
  bool ext;
  if (!strcasecmp(kind, "ext")) ext = true;
  else if (!strcasecmp(kind, "std")) ext = false;
  else { emit("ERR %s reason=bad_frame_kind", cmd); return false; }

  uint32_t id;
  if (!parse_hex(ids, &id)) { emit("ERR %s reason=bad_id", cmd); return false; }
  if (ext && id > 0x1FFFFFFF) { emit("ERR %s reason=ext_id_out_of_range", cmd); return false; }
  if (!ext && id > 0x7FF) { emit("ERR %s reason=std_id_out_of_range", cmd); return false; }

  int n = parse_bytes(datas, m->data, 8);
  if (n < 0) { emit("ERR %s reason=bad_data_hex", cmd); return false; }

  m->extd = ext ? 1 : 0;
  m->identifier = id;
  m->data_length_code = (uint8_t)n;
  m->ss = g_single_shot ? 1 : 0;
  m->self = g_self ? 1 : 0;
  return true;
}

static void cmd_send(char **tok, int n) {
  if (!g_installed) { emit("ERR SEND reason=not_installed"); return; }
  if (n < 4) { emit("ERR SEND reason=usage"); return; }
  twai_message_t m;
  if (!build_frame(tok[1], tok[2], n >= 4 ? tok[3] : "-", &m, "SEND")) return;
  esp_err_t e = twai_transmit(&m, pdMS_TO_TICKS(200));
  if (e == ESP_OK) g_tx_ok++; else g_tx_err++;
  emit("OK SEND ext=%d id=0x%08x dlc=%d queued=%d esp_err=0x%x", m.extd ? 1 : 0,
       (unsigned)m.identifier, (int)m.data_length_code, e == ESP_OK ? 1 : 0,
       (unsigned)e);
}

static void cmd_burst(char **tok, int n) {
  if (!g_installed) { emit("ERR BURST reason=not_installed"); return; }
  if (n < 6) { emit("ERR BURST reason=usage"); return; }
  twai_message_t m;
  if (!build_frame(tok[1], tok[2], tok[3], &m, "BURST")) return;
  uint32_t count, interval;
  if (!parse_u32(tok[4], &count) || count == 0 || count > 10000) {
    emit("ERR BURST reason=bad_count"); return;
  }
  if (!parse_u32(tok[5], &interval) || interval > 60000) {
    emit("ERR BURST reason=bad_interval"); return;
  }
  uint32_t ok = 0, bad = 0;
  for (uint32_t i = 0; i < count; ++i) {
    esp_err_t e = twai_transmit(&m, pdMS_TO_TICKS(200));
    if (e == ESP_OK) { ok++; g_tx_ok++; } else { bad++; g_tx_err++; }
    service();
    if (interval) delay(interval);
    else delayMicroseconds(200);
  }
  emit("OK BURST ext=%d id=0x%08x dlc=%d count=%lu queued=%lu queue_err=%lu",
       m.extd ? 1 : 0, (unsigned)m.identifier, (int)m.data_length_code,
       (unsigned long)count, (unsigned long)ok, (unsigned long)bad);
}

static void cmd_fire() {
  if (!g_installed) { emit("ERR FIRE reason=not_installed"); return; }
  if (g_trig != TRIG_MASTER) { emit("ERR FIRE reason=not_trigger_master"); return; }
  if (!g_armed_valid) { emit("ERR FIRE reason=nothing_armed"); return; }
  /* A "starter" frame keeps the bus busy long enough (~130 us at 500 kbit with
   * DLC=8) for the slave to see the trigger edge and queue its contender. Both
   * contenders are then pending when the bus next goes idle, so they start
   * transmitting on the same SOF and genuinely arbitrate. Without the starter,
   * whichever node calls twai_transmit() first simply wins by being first, and
   * you measure scheduling latency instead of CAN arbitration. */
  twai_message_t starter;
  memset(&starter, 0, sizeof(starter));
  starter.extd = 1;
  starter.identifier = 0x1FFFFF00;
  starter.data_length_code = 8;
  memset(starter.data, 0x55, 8);
  starter.ss = g_single_shot ? 1 : 0;
  esp_err_t es = twai_transmit(&starter, pdMS_TO_TICKS(200));

  digitalWrite((gpio_num_t)TRIG_GPIO, HIGH);
  esp_err_t ec = twai_transmit(&g_armed, pdMS_TO_TICKS(200));
  delayMicroseconds(500);
  digitalWrite((gpio_num_t)TRIG_GPIO, LOW);
  g_armed_valid = false;
  if (ec == ESP_OK) g_tx_ok++; else g_tx_err++;
  emit("OK FIRE starter=0x%x contender=0x%x id=0x%08x", (unsigned)es,
       (unsigned)ec, (unsigned)g_armed.identifier);
}

static void handle(char *line) {
  char *tok[10];
  int n = 0;
  for (char *p = strtok(line, " \t"); p && n < 10; p = strtok(nullptr, " \t"))
    tok[n++] = p;
  if (n == 0) return;
  char *c = tok[0];

  if (!strcasecmp(c, "HELP")) { cmd_help(); return; }
  if (!strcasecmp(c, "ID"))   { cmd_id(); return; }
  if (!strcasecmp(c, "STATS")){ cmd_stats(); return; }
  if (!strcasecmp(c, "CALC")) { cmd_calc(tok, n); return; }

  if (!strcasecmp(c, "ROLE")) {
    if (n < 2) { emit("ERR ROLE reason=usage"); return; }
    /* A role is a LABEL PLUS DEFAULTS, never a hidden mode. Everything a role
     * sets is also settable individually, and STATS always reports the real
     * flags rather than the label — so a role can never make the rig behave in
     * a way the transcript does not show. */
    if (!strcasecmp(tok[1], "tx")) {
      g_role = "tx"; g_rx_print = false; g_self = false;
    } else if (!strcasecmp(tok[1], "rx")) {
      g_role = "rx"; g_rx_print = true; g_drain = true; g_self = false;
    } else if (!strcasecmp(tok[1], "loop")) {
      /* Track A: single board, TX->RX jumper, self-reception through the filter. */
      g_role = "loop"; g_rx_print = true; g_drain = true; g_self = true;
    } else if (!strcasecmp(tok[1], "idle")) {
      g_role = "idle";
    } else { emit("ERR ROLE reason=unknown_role"); return; }
    emit("OK ROLE role=%s rx_print=%d drain=%d self=%d note=%s", g_role,
         g_rx_print ? 1 : 0, g_drain ? 1 : 0, g_self ? 1 : 0,
         g_self ? "run_INIT_<kbps>_no-ack" : "run_INIT_<kbps>_normal");
    return;
  }

  if (!strcasecmp(c, "INIT")) {
    if (n < 3) { emit("ERR INIT reason=usage"); return; }
    uint32_t kbps;
    if (!parse_u32(tok[1], &kbps)) { emit("ERR INIT reason=bad_bitrate"); return; }
    twai_mode_t m;
    const char *mn;
    if (!strcasecmp(tok[2], "normal")) { m = TWAI_MODE_NORMAL; mn = "normal"; }
    else if (!strcasecmp(tok[2], "listen-only")) { m = TWAI_MODE_LISTEN_ONLY; mn = "listen-only"; }
    else if (!strcasecmp(tok[2], "no-ack")) { m = TWAI_MODE_NO_ACK; mn = "no-ack"; }
    else { emit("ERR INIT reason=bad_mode"); return; }
    uint32_t prev_k = g_bitrate_kbps; twai_mode_t prev_m = g_mode; const char *prev_n = g_mode_name;
    g_bitrate_kbps = kbps; g_mode = m; g_mode_name = mn;
    if (!bring_up("INIT")) { g_bitrate_kbps = prev_k; g_mode = prev_m; g_mode_name = prev_n; return; }
    emit("OK INIT bitrate_kbps=%lu mode=%s tx_gpio=%d rx_gpio=%d "
         "code=0x%08x mask=0x%08x filter=%s rx_queue_len=%lu counters=zeroed",
         (unsigned long)g_bitrate_kbps, g_mode_name, g_tx_gpio, g_rx_gpio,
         (unsigned)g_filter_code, (unsigned)g_filter_mask,
         g_filter_single ? "single" : "dual", (unsigned long)g_rx_queue_len);
    return;
  }

  if (!strcasecmp(c, "DEINIT")) { teardown(); emit("OK DEINIT installed=0"); return; }

  if (!strcasecmp(c, "FILTER")) {
    if (n < 4) { emit("ERR FILTER reason=usage"); return; }
    uint32_t code, mask;
    if (!parse_hex(tok[1], &code)) { emit("ERR FILTER reason=bad_code"); return; }
    if (!parse_hex(tok[2], &mask)) { emit("ERR FILTER reason=bad_mask"); return; }
    bool single;
    if (!strcasecmp(tok[3], "single")) single = true;
    else if (!strcasecmp(tok[3], "dual")) single = false;
    else { emit("ERR FILTER reason=bad_filter_mode"); return; }
    g_filter_code = code; g_filter_mask = mask; g_filter_single = single;
    int reinit = 0;
    if (g_installed) { if (!bring_up("FILTER")) return; reinit = 1; }
    emit("OK FILTER code=0x%08x mask=0x%08x mode=%s reinit=%d counters=%s",
         (unsigned)code, (unsigned)mask, single ? "single" : "dual", reinit,
         reinit ? "zeroed" : "unchanged");
    return;
  }

  if (!strcasecmp(c, "SEND"))  { cmd_send(tok, n); return; }
  if (!strcasecmp(c, "BURST")) { cmd_burst(tok, n); return; }

  if (!strcasecmp(c, "RX")) {
    if (n < 2) { emit("ERR RX reason=usage"); return; }
    if (!strcasecmp(tok[1], "on")) g_rx_print = true;
    else if (!strcasecmp(tok[1], "off")) g_rx_print = false;
    else { emit("ERR RX reason=usage"); return; }
    emit("OK RX rx_print=%d", g_rx_print ? 1 : 0);
    return;
  }

  if (!strcasecmp(c, "DRAIN")) {
    if (n < 2) { emit("ERR DRAIN reason=usage"); return; }
    if (!strcasecmp(tok[1], "on")) g_drain = true;
    else if (!strcasecmp(tok[1], "off")) g_drain = false;
    else { emit("ERR DRAIN reason=usage"); return; }
    emit("OK DRAIN drain=%d", g_drain ? 1 : 0);
    return;
  }

  if (!strcasecmp(c, "RXQ")) {
    uint32_t v;
    if (n < 2 || !parse_u32(tok[1], &v) || v == 0 || v > 200) {
      emit("ERR RXQ reason=usage"); return;
    }
    g_rx_queue_len = v;
    emit("OK RXQ rx_queue_len=%lu applies=next_INIT", (unsigned long)v);
    return;
  }

  if (!strcasecmp(c, "SS")) {
    if (n < 2) { emit("ERR SS reason=usage"); return; }
    if (!strcasecmp(tok[1], "on")) g_single_shot = true;
    else if (!strcasecmp(tok[1], "off")) g_single_shot = false;
    else { emit("ERR SS reason=usage"); return; }
    emit("OK SS single_shot=%d", g_single_shot ? 1 : 0);
    return;
  }

  if (!strcasecmp(c, "RXPULL")) {
    /* Force the TWAI RX pin's internal pull. This exists so the two "the wire is
     * wrong" signatures can be REPRODUCED DELIBERATELY, with no hardware, before
     * anyone trusts a real reading:
     *   down  -> RX reads DOMINANT forever. The controller never sees bus-idle,
     *            so a queued frame is never sent: msgs_to_tx sticks above zero
     *            while EVERY error counter stays at zero. That silence is the
     *            signature of an unpowered transceiver / missing jumper / shorted
     *            CANH-CANL, and it is easy to mistake for "nothing happened".
     *   up    -> RX reads RECESSIVE forever. The transmitter reads back the
     *            opposite of the dominant bit it drove: bus_error and tec climb
     *            and (with SS ON) tx_failed increments. That is the signature of
     *            a broken RX path / transceiver in standby (Rs high) / TX wired
     *            but RX not.
     * Set it back to `none` before any real measurement. STATS reports it. */
    if (n < 2) { emit("ERR RXPULL reason=usage"); return; }
    gpio_pull_mode_t pm;
    if (!strcasecmp(tok[1], "up")) { pm = GPIO_PULLUP_ONLY; g_rxpull = "up"; }
    else if (!strcasecmp(tok[1], "down")) { pm = GPIO_PULLDOWN_ONLY; g_rxpull = "down"; }
    else if (!strcasecmp(tok[1], "none")) { pm = GPIO_FLOATING; g_rxpull = "none"; }
    else { emit("ERR RXPULL reason=usage"); return; }
    esp_err_t e = gpio_set_pull_mode((gpio_num_t)g_rx_gpio, pm);
    emit("OK RXPULL rx_gpio=%d pull=%s esp_err=0x%x", g_rx_gpio, g_rxpull,
         (unsigned)e);
    return;
  }

  /* PINS <tx> <rx> — retarget the TWAI pins; applied at the next INIT.
   * ADDED 2026-08-10, see the comment on g_tx_gpio. `PINS 3 3` is a pad-level
   * internal loopback (the GPIO matrix can route an output signal to a pad and
   * that same pad back to an input signal), which needs no jumper at all. */
  if (!strcasecmp(c, "PINS")) {
    if (n < 3) { emit("ERR PINS reason=usage"); return; }
    long tx = strtol(tok[1], NULL, 0);
    long rx = strtol(tok[2], NULL, 0);
    if (tx < 0 || tx > 21 || rx < 0 || rx > 21) {
      emit("ERR PINS reason=out_of_range"); return;
    }
    g_tx_gpio = (int)tx;
    g_rx_gpio = (int)rx;
    emit("OK PINS tx_gpio=%d rx_gpio=%d note=applied_at_next_INIT",
         g_tx_gpio, g_rx_gpio);
    return;
  }

  /* PROBE <drv> <sense> — DC continuity test between two pads, no TWAI at all.
   * Drives `drv` high then low as a push-pull output and reads `sense` (input,
   * no pull) each time. wired=1 means sense followed drv both ways, which is
   * what a jumper does and what a floating or hard-tied pin cannot do.
   * ADDED 2026-08-10 to answer "is the wire actually where we think it is?"
   * without reflashing once per guess. Refuses to drive the USB pads. */
  if (!strcasecmp(c, "PROBE")) {
    if (n < 3) { emit("ERR PROBE reason=usage"); return; }
    long d = strtol(tok[1], NULL, 0);
    long s = strtol(tok[2], NULL, 0);
    if (d < 0 || d > 21 || s < 0 || s > 21 || d == s) {
      emit("ERR PROBE reason=out_of_range"); return;
    }
    if (d == 18 || d == 19 || s == 18 || s == 19) {
      emit("ERR PROBE reason=usb_pads_refused"); return;
    }
    if (g_installed) { emit("ERR PROBE reason=deinit_first"); return; }
    pinMode((gpio_num_t)s, INPUT);
    pinMode((gpio_num_t)d, OUTPUT);
    digitalWrite((gpio_num_t)d, HIGH);
    delay(5);
    int hi = digitalRead((gpio_num_t)s);
    digitalWrite((gpio_num_t)d, LOW);
    delay(5);
    int lo = digitalRead((gpio_num_t)s);
    /* also read the sense pad with its own pulls, drv released, to tell
     * "floating" from "tied to a rail" */
    pinMode((gpio_num_t)d, INPUT);
    pinMode((gpio_num_t)s, INPUT_PULLUP);
    delay(5);
    int f_pu = digitalRead((gpio_num_t)s);
    pinMode((gpio_num_t)s, INPUT_PULLDOWN);
    delay(5);
    int f_pd = digitalRead((gpio_num_t)s);
    pinMode((gpio_num_t)s, INPUT);
    emit("OK PROBE drv=%d sense=%d drv_hi_reads=%d drv_lo_reads=%d wired=%d "
         "free_pullup_reads=%d free_pulldown_reads=%d",
         (int)d, (int)s, hi, lo, (hi == 1 && lo == 0) ? 1 : 0, f_pu, f_pd);
    return;
  }

  if (!strcasecmp(c, "SELF")) {
    if (n < 2) { emit("ERR SELF reason=usage"); return; }
    if (!strcasecmp(tok[1], "on")) g_self = true;
    else if (!strcasecmp(tok[1], "off")) g_self = false;
    else { emit("ERR SELF reason=usage"); return; }
    emit("OK SELF self=%d note=%s", g_self ? 1 : 0,
         g_self ? "needs_no-ack_mode_and_TX-RX_jumper" : "peer_reception");
    return;
  }

  if (!strcasecmp(c, "CLEAR")) {
    clear_sw_counters();
    emit("OK CLEAR sw=zeroed hw=unchanged note=re-run_INIT_to_zero_hw_counters");
    return;
  }

  if (!strcasecmp(c, "RECOVER")) {
    if (!g_installed) { emit("ERR RECOVER reason=not_installed"); return; }
    esp_err_t e = twai_initiate_recovery();
    emit("OK RECOVER esp_err=0x%x", (unsigned)e);
    return;
  }

  if (!strcasecmp(c, "TRIG")) {
    if (n < 2) { emit("ERR TRIG reason=usage"); return; }
    if (!strcasecmp(tok[1], "master")) {
      pinMode((gpio_num_t)TRIG_GPIO, OUTPUT);
      digitalWrite((gpio_num_t)TRIG_GPIO, LOW);
      g_trig = TRIG_MASTER;
    } else if (!strcasecmp(tok[1], "slave")) {
      pinMode((gpio_num_t)TRIG_GPIO, INPUT_PULLDOWN);
      g_trig = TRIG_SLAVE;
      g_trig_prev = digitalRead((gpio_num_t)TRIG_GPIO) != 0;
    } else if (!strcasecmp(tok[1], "off")) {
      pinMode((gpio_num_t)TRIG_GPIO, INPUT_PULLDOWN);
      g_trig = TRIG_OFF;
    } else { emit("ERR TRIG reason=usage"); return; }
    emit("OK TRIG mode=%s gpio=%d",
         g_trig == TRIG_MASTER ? "master" : g_trig == TRIG_SLAVE ? "slave" : "off",
         TRIG_GPIO);
    return;
  }

  if (!strcasecmp(c, "ARM")) {
    if (n < 4) { emit("ERR ARM reason=usage"); return; }
    twai_message_t m;
    if (!build_frame(tok[1], tok[2], tok[3], &m, "ARM")) return;
    g_armed = m;
    g_armed_valid = true;
    emit("OK ARM ext=%d id=0x%08x dlc=%d", m.extd ? 1 : 0,
         (unsigned)m.identifier, (int)m.data_length_code);
    return;
  }

  if (!strcasecmp(c, "FIRE")) { cmd_fire(); return; }

  if (!strcasecmp(c, "ECHO")) {
    if (n < 2) { emit("ERR ECHO reason=usage"); return; }
    if (!strcasecmp(tok[1], "on")) g_echo = true;
    else if (!strcasecmp(tok[1], "off")) g_echo = false;
    else { emit("ERR ECHO reason=usage"); return; }
    emit("OK ECHO echo=%d", g_echo ? 1 : 0);
    return;
  }

  emit("ERR %s reason=unknown_command", c);
}

/* -------------------------------------------------------------------------- */
/* arduino entry points                                                       */
/* -------------------------------------------------------------------------- */

static char g_line[160];
static int g_len = 0;

void setup() {
  Serial.begin(115200);
#if defined(ARDUINO_USB_CDC_ON_BOOT) && ARDUINO_USB_CDC_ON_BOOT && \
    defined(ESP_ARDUINO_VERSION) && ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(2, 0, 5)
  /* 20 ms, NOT 0. With a zero timeout the CDC write is fully non-blocking and
   * SILENTLY DISCARDS bytes the moment the TX buffer fills — a 30-line HELP or a
   * long STATS report came back truncated and line-merged, which is the worst
   * possible failure for output that is supposed to be machine-parseable.
   * 20 ms is far longer than the 1 ms USB poll interval, so an attached host
   * always drains in time; and the only unsolicited output (EVT ALERT) is capped
   * at 8 lines/second, so an UNattached host costs at most ~160 ms/s of
   * blocking. Do not set this back to 0. */
  Serial.setTxTimeoutMs(20);
#endif
  delay(400);
  pinMode((gpio_num_t)TRIG_GPIO, INPUT_PULLDOWN);
  emit("# %s %s ready — type HELP. TWAI NOT installed; run INIT first.", FW_NAME,
       FW_VERSION);
  cmd_id();
}

void loop() {
  while (Serial.available()) {
    int ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      g_line[g_len] = 0;
      if (g_len) {
        if (g_echo) emit("# > %s", g_line);
        char work[160];
        memcpy(work, g_line, g_len + 1);
        handle(work);
      }
      g_len = 0;
      continue;
    }
    if (g_len < (int)sizeof(g_line) - 1) g_line[g_len++] = (char)ch;
    else { g_len = 0; emit("ERR - reason=line_too_long"); }
  }
  service();
}
