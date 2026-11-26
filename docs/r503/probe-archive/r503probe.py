#!/usr/bin/env python3
"""R503 bring-up probe — host side.

Drives the S3-side firmware (firmware/r503_probe) over its line protocol and
records what it observes in a session log that can fill an evidence slot.

WHAT THIS IS: exploration, session logging, and the human interface.
WHAT THIS IS NOT: the wire layer. Framing, checksums, drain, resync and every
byte-level timeout live on the S3, because the R503's protocol is timing-coupled
at the byte level and none of that survives a USB hop.

⛔ NOT the HAL. [SPEC:CXC-FPHAL] is UNRATIFIED and under review. Nothing here
   implements it, imports its types, or pre-empts it.

⛔ NO BAUD CHANGE EXISTS IN THIS TOOL. There is no SetSysPara helper and no
   subcommand that emits one. [CXC-R503-BAUD-SELF-CONTRADICTS]: every revision
   contradicts itself on legal rates and NO RECOVERY FROM A BRICKED MODULE IS
   DOCUMENTED ANYWHERE. 57600 is bound in firmware and never touched.

⛔ NO unbounded wait anywhere. Every read has a deadline and every deadline is
   reported. A shipped driver in this space hangs its host forever because its
   timeout returns empty and the caller continues with no counter.
"""

import argparse
import datetime
import json
import pathlib
import sys
import time

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    sys.stderr.write("pyserial is required:  python3 -m pip install pyserial\n")
    raise SystemExit(2)

# ⛔ The ONE board this tool may talk to: the ESP32-S3 SuperMini whose USB serial is
#    R503_EXPECT_SERIAL in the host overlay. Every other board is OFF LIMITS, and any
#    serial on FLASH_DENY_SERIALS is refused even if named with --serial. Public code
#    names no device: unset = refuse (main()), never a made-up default.
import os  # noqa: E402
DEFAULT_SERIAL = os.environ.get("R503_EXPECT_SERIAL", "").strip()

REPO = pathlib.Path(__file__).resolve().parent.parent
SESSIONS = REPO / "probe" / "sessions"

# AuraLedConfig control codes and colour indices. [CXC-R503-AURA-LED]
LED_ALWAYS_ON = 0x03
LED_ALWAYS_OFF = 0x04
COLOUR_NAMES_PER_MANUAL = {
    0x01: "Red",
    0x02: "Blue",
    0x03: "Purple",
    0x04: "Green",
    0x05: "Yellow",
    0x06: "Cyan",
    0x07: "White",
}
# ⚠ V1.1 documented only THREE colours: Red 0x01, Blue 0x02, Purple 0x03.
#   Green 0x04 first appears in V1.2.1. [CXC-R503-REVISION-DRIFT] `drift-led`.
V1_1_COLOURS = {0x01, 0x02, 0x03}


# ---------------------------------------------------------------------------
# Identity — a device path is a LEASE, not an identity
# ---------------------------------------------------------------------------

def resolve_port(want_serial):
    """Resolve a USB serial number to a device path, AT USE TIME.

    ⛔ The path is never cached. This board has native USB and re-enumerates when
       BOOT is pressed; on this fleet paths have changed five times in one session.
    ⛔ `esptool chip-id` is NOT an acceptable identity check — it RESETS the board,
       which is the exact harm an identity check is run to avoid. This reads the USB
       descriptor, which has no side effects at all.
    """
    matches = [p for p in serial.tools.list_ports.comports()
               if p.serial_number == want_serial]
    if not matches:
        seen = [(p.device, p.serial_number) for p in serial.tools.list_ports.comports()]
        raise SystemExit(
            f"no port with USB serial {want_serial!r}.\nports seen: {seen}\n"
            "If the board was just re-plugged or BOOT was pressed, re-run.")
    if len(matches) > 1:
        raise SystemExit(f"ambiguous: {[m.device for m in matches]}")
    return matches[0].device


# ---------------------------------------------------------------------------
# Link to the probe firmware
# ---------------------------------------------------------------------------

class ProbeLink:
    def __init__(self, port, log=None, verbose=False):
        self.port = port
        self.log = log
        self.verbose = verbose
        self.seq = 0
        # ⛔ PROVENANCE COUNTER. A session log full of human colour names with ZERO
        #    bytes ever received from a module is exactly the artifact that could
        #    fill an evidence slot with a measurement nobody made. Counted here so
        #    the guard cannot be forgotten by a caller.
        self.frames_ok = 0
        self.bytes_rx = 0
        # Construct unopened so DTR/RTS are set BEFORE the port comes up: on an
        # ESP32-S3 native-USB port, DTR/RTS transitions emulate the reset/boot
        # strapping. Resting both low avoids resetting the board just by connecting.
        self.ser = serial.Serial()
        self.ser.port = port
        self.ser.baudrate = 115200      # irrelevant for native USB CDC; pyserial wants it
        self.ser.timeout = 0.05         # bounded read, always
        self.ser.dtr = False
        self.ser.rts = False
        self.ser.open()
        time.sleep(0.15)
        self.ser.reset_input_buffer()

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def command(self, verb, *args, deadline=6.0):
        """Send one verb, return (status, fields dict, diagnostics list).

        ⛔ BOUNDED. `deadline` is a hard wall-clock ceiling. If the firmware never
           answers, this returns status 'HOST_TIMEOUT' with the elapsed time. It
           does not spin, it does not retry silently, and it never blocks forever.
        """
        self.seq += 1
        seq = str(self.seq)
        line = "> " + seq + " " + verb
        if args:
            line += " " + " ".join(str(a) for a in args)
        if self.verbose:
            print("  TX " + line)
        self.ser.write((line + "\n").encode())
        self.ser.flush()

        diags = []
        buf = b""
        t0 = time.monotonic()
        while True:
            elapsed = time.monotonic() - t0
            if elapsed > deadline:
                return "HOST_TIMEOUT", {"host_elapsed_s": round(elapsed, 3)}, diags
            chunk = self.ser.read(256)
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                if self.verbose:
                    print("  RX " + text)
                # SELF-SYNCHRONISING BY SENTINEL: anything not starting with '<'
                # is not a response. A boot banner, a panic dump or a stray byte is
                # therefore harmless rather than a desync — the same lesson as the
                # sliding two-byte header window, one layer up.
                if text.startswith("#"):
                    diags.append(text[1:].strip())
                    continue
                if not text.startswith("<"):
                    diags.append("UNFRAMED: " + text)
                    continue
                parts = text[1:].strip().split()
                if len(parts) < 2 or parts[0] != seq:
                    diags.append("STALE-OR-MISMATCHED: " + text)
                    continue
                status = parts[1]
                fields = {}
                for kv in parts[2:]:
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        fields[k] = v
                fields["host_elapsed_s"] = round(time.monotonic() - t0, 3)
                try:
                    self.bytes_rx += int(fields.get("rx_total", 0))
                except ValueError:
                    pass
                if status == "ok" and "pid" in fields:
                    self.frames_ok += 1
                return status, fields, diags


# ---------------------------------------------------------------------------
# Session log — shaped so a record can fill an evidence slot verbatim
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, name, observer):
        SESSIONS.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.path = SESSIONS / f"{stamp}-{name}.jsonl"
        self.observer = observer
        self.fh = self.path.open("w")
        self.write("session_start", {"tool": "r503probe.py", "subcommand": name,
                                     "observer": observer})

    def write(self, kind, payload):
        rec = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "kind": kind,
            "observer": self.observer,
        }
        rec.update(payload)
        self.fh.write(json.dumps(rec) + "\n")
        self.fh.flush()

    def observation(self, slot, question, expected, observed, machine=None):
        """The evidence-slot shape: question / expected / observed / who / when.

        An unfilled field carries the explicit sentinel 'EMPTY' rather than being
        absent — an absent key is indistinguishable from one nobody thought to write.
        """
        self.write("observation", {
            "slot": slot,
            "question": question,
            "expected": expected,
            "observed": observed if observed not in (None, "") else "EMPTY",
            "machine": machine if machine is not None else "EMPTY",
        })

    def close(self):
        self.write("session_end", {})
        self.fh.close()


def provenance_guard(sess, link, slots):
    """⛔ Say plainly whether ANY hardware answered during this session.

    An evidence slot filled from a session in which no module ever replied is a
    measurement nobody made. This writes the fact into the log AND prints it, so
    the log is self-identifying rather than relying on someone remembering.
    """
    responded = link.frames_ok > 0
    sess.write("provenance", {
        "frames_parsed_from_hardware": link.frames_ok,
        "bytes_received_total": link.bytes_rx,
        "hardware_responded": responded,
        "slots_touched": slots,
        "warning": "EMPTY" if responded else
                   "NO MODULE EVER ANSWERED. Every human answer in this log was "
                   "given against a silent line. ⛔ DO NOT USE THIS SESSION TO FILL "
                   "AN EVIDENCE SLOT.",
    })
    print()
    if responded:
        print(f"  PROVENANCE: {link.frames_ok} frame(s) parsed from hardware, "
              f"{link.bytes_rx} bytes received. This session touched a real module.")
    else:
        print("  " + "=" * 74)
        print("  ⛔ PROVENANCE: NO MODULE EVER ANSWERED — zero frames parsed.")
        print("     Any answers recorded above were given against a SILENT LINE.")
        print("     ⛔ DO NOT USE THIS SESSION TO FILL AN EVIDENCE SLOT.")
        print("  " + "=" * 74)


def banner(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def show(status, fields, diags, label=""):
    if label:
        print(f"  {label}")
    print(f"    status : {status}")
    for k in sorted(fields):
        print(f"    {k:<16}: {fields[k]}")
    for d in diags:
        print(f"    # {d}")
    return fields


def ask_nonempty(prompt):
    """⛔ Free text, and it will not accept a blank.

    NOT a yes/no. [CXC-R503-EV-25] `why-a-human`: the observer must NAME the colour
    seen, never judge whether it looks right. A confirmation code of 0x00 means the
    command was ACCEPTED, never that the intended colour APPEARED.
    """
    while True:
        try:
            v = input(prompt).strip()
        except EOFError:
            return "EMPTY (no tty)"
        if v:
            return v
        print("    (a blank answer records nothing — please type what you actually see)")


def open_link(args, log=None):
    port = resolve_port(args.serial)
    print(f"resolved USB serial {args.serial} -> {port}")
    return ProbeLink(port, log=log, verbose=args.verbose)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_port(args):
    print(resolve_port(args.serial))


def cmd_ping(args):
    link = open_link(args)
    try:
        show(*link.command("ping"), label="ping")
    finally:
        link.close()


def cmd_selftest(args):
    """Codec self-test, run ON THE REAL SILICON. Needs no R503 attached."""
    link = open_link(args)
    try:
        banner("CODEC SELF-TEST (no R503 required)")
        status, fields, diags = link.command("selftest", deadline=15.0)
        for d in diags:
            print("  " + d)
        print(f"\n  RESULT: {status}  pass={fields.get('pass')} fail={fields.get('fail')}")
        return 0 if status == "ok" else 1
    finally:
        link.close()


def cmd_ev25(args):
    """[CXC-R503-EV-25] — GetFwVer, then census the ring colours actually driven.

    Two observations on one powered part in one sitting.
    """
    observer = args.observer or ask_nonempty("Who is running this (name for the record)? ")
    sess = Session("EV-25", observer)
    link = open_link(args, log=sess)
    try:
        banner("EV-25 (a) — GetFwVer 0x3A, recorded VERBATIM")
        show(*link.command("ping"), label="probe identity")

        # Drain first and REPORT what was there. [CXC-R503-UNSOLICITED-0X55]:
        # a bare 0x55 arrives at power-on and after every SoftRst.
        st, f, d = link.command("drain", 200)
        show(st, f, d, label="pre-session drain (a 0x55 here is the power-on byte)")
        sess.write("drain", {"count": f.get("count"), "hex": f.get("hex")})

        st, f, d = link.command("fwver", deadline=8.0)
        show(st, f, d, label="GetFwVer 0x3A")
        fw_ascii = "EMPTY"
        for line in d:
            if line.startswith("fwver_ascii="):
                fw_ascii = line
        sess.observation(
            "CXC-R503-EV-25(a)",
            "Send GetFwVer 0x3A and record the returned string VERBATIM.",
            "a version string",
            observed=f.get("content", "EMPTY") if st == "ok" else f"NO STRING ({st})",
            machine={"status": st, **{k: v for k, v in f.items()}, "ascii": fw_ascii},
        )
        if st != "ok":
            print("\n  ⛔ GetFwVer did not return a frame. Status above is the finding.")
            print("     The colour census below is still worth running if the ring lights.")

        banner("EV-25 (b) — ring colour census, indices 0x01..0x07, always-on")
        print("""
  ⛔ READ THIS BEFORE ANSWERING.
     You will be shown seven colour indices, one at a time. You are DELIBERATELY
     NOT TOLD what each index is supposed to be, because an observer told to expect
     green will see green. Name the colour you actually see, in your own words.
     'none' / 'dark' / 'off' are real answers and are recorded as such.

     ⛔ THE DANGEROUS OUTCOME IS A REPEAT, NOT AN ABSENCE. If index 4 silently
        renders as blue, the module returns 0x00 and NOTHING says the colour is
        wrong. That is what this census exists to catch.
""")
        results = {}
        for idx in range(0x01, 0x08):
            st, f, d = link.command("led", LED_ALWAYS_ON, 0x00, idx, 0x00, deadline=8.0)
            conf = f.get("conf", "n/a")
            conf_name = f.get("conf_name", "n/a")
            print(f"\n  --- index {idx} of 7 sent."
                  f"  transport={st}  conf={conf} ({conf_name})")
            if st != "ok":
                print("      ⛔ no acknowledgement. Recording the transport failure "
                      "and still asking what the ring shows.")
            seen = ask_nonempty("      What colour is the ring showing RIGHT NOW? ")
            results[idx] = {"seen": seen, "status": st, "conf": conf,
                            "conf_name": conf_name, "fields": f}
            sess.observation(
                "CXC-R503-EV-25(b)",
                f"AuraLedConfig 0x35, always-on mode 0x03, colour index 0x{idx:02X}. "
                "Name the colour the ring actually shows.",
                f"per V1.2.1+: {COLOUR_NAMES_PER_MANUAL[idx]}. "
                f"per V1.1: only 0x01-0x03 exist; 0x04-0x07 give no light, an error, "
                f"or A REPEAT of one of the first three.",
                observed=seen,
                machine={"status": st, "conf": conf, "conf_name": conf_name},
            )

        # Leave the ring off. The probe must not leave the part in a state it set.
        link.command("led", LED_ALWAYS_OFF, 0x00, 0x01, 0x00, deadline=8.0)

        banner("EV-25 — VERDICT")
        print(f"  {'idx':<5}{'commanded (V1.2.1+)':<22}{'OBSERVED':<24}{'conf'}")
        for idx in sorted(results):
            r = results[idx]
            print(f"  0x{idx:02X} {COLOUR_NAMES_PER_MANUAL[idx]:<22}"
                  f"{r['seen']:<24}{r['conf']}")

        # ⛔ REPEAT DETECTION — the dangerous outcome, computed rather than eyeballed.
        norm = {}
        for idx, r in results.items():
            key = r["seen"].strip().lower()
            norm.setdefault(key, []).append(idx)
        repeats = {k: v for k, v in norm.items() if len(v) > 1}
        dark = [k for k in norm if k in ("none", "dark", "off", "no light", "nothing")]

        print()
        if repeats:
            print("  ⛔ REPEATS FOUND — this is the DANGEROUS outcome, not the absence:")
            for name, idxs in repeats.items():
                print(f"     '{name}' returned by indices {['0x%02X' % i for i in idxs]}")
            print("     A driver written against V1.4.1 will show one semantic state "
                  "while meaning another, and NO CONFIRMATION CODE SAYS SO.")
        else:
            print("  No repeats: every index produced a distinct answer.")

        green_seen = results.get(0x04, {}).get("seen", "").strip().lower()
        print()
        print(f"  0x04 GREEN — observed as: {green_seen or 'EMPTY'}")
        print("  ⛔ [CXC-R503-FEEDBACK-REALIZATION] `rule-accept-is-green` is "
              "implementable ONLY if that reads as green.")
        print("     If it does not, the table needs a RULING (a game-design call), "
              "not a workaround — every remaining option collides with `ready`.")

        v11_shape = all(
            results.get(i, {}).get("seen", "").strip().lower() in
            ("none", "dark", "off", "no light", "nothing")
            or results.get(i, {}).get("seen", "").strip().lower() in
            [results.get(j, {}).get("seen", "").strip().lower() for j in V1_1_COLOURS]
            for i in (0x04, 0x05, 0x06, 0x07))
        print()
        print(f"  Firmware-vintage shape: "
              f"{'consistent with V1.1-era (only 3 colours)' if v11_shape else 'consistent with V1.2.1 or later (7 distinct colours)'}")
        if v11_shape:
            print("  ⛔ SECOND-ORDER: if the firmware is V1.1-era, "
                  "[CXC-R503-REVISION-DRIFT]'s other silent changes become LIVE RISKS —")
            print("     template size 768 rather than 1536 bytes (which HALVES the "
                  "full-resync cost arithmetic), 0x31/0x32 possibly ABSENT, and a "
                  "different default security level.")

        provenance_guard(sess, link, ["CXC-R503-EV-25"])
        sess.write("verdict", {"results": {f"0x{k:02X}": v["seen"] for k, v in results.items()},
                               "repeats": {k: [f"0x{i:02X}" for i in v] for k, v in repeats.items()},
                               "dark_answers": dark,
                               "v1_1_shape": bool(v11_shape)})
        print(f"\n  session log: {sess.path}")
    finally:
        link.close()
        sess.close()


def cmd_ev03(args):
    """[CXC-R503-EV-03] — ReadProdInfo 0x3C, and [CXC-R503-EV-15] for free."""
    observer = args.observer or ask_nonempty("Who is running this (name for the record)? ")
    sess = Session("EV-03", observer)
    link = open_link(args, log=sess)
    try:
        banner("EV-03 / EV-15 — ReadProdInfo 0x3C")
        show(*link.command("ping"), label="probe identity")
        st, f, d = link.command("drain", 200)
        show(st, f, d, label="pre-session drain")

        st, f, d = link.command("prodinfo", deadline=8.0)
        show(st, f, d, label="ReadProdInfo 0x3C")

        model = "EMPTY"
        len_field = f.get("len_field", "EMPTY")
        prodinfo_bytes = "EMPTY"
        verdict = "EMPTY"
        for line in d:
            if line.startswith("prodinfo "):
                for kv in line.split():
                    if kv.startswith("prodinfo_bytes="):
                        prodinfo_bytes = kv.split("=", 1)[1]
                    if kv.startswith("ev15_verdict="):
                        verdict = kv.split("=", 1)[1]
                if 'model="' in line:
                    model = line.split('model="', 1)[1].rstrip('"')

        sess.observation(
            "CXC-R503-EV-03",
            "Issue ReadProdInfo 0x3C and read PARAM_FPM_MODEL — 16 ASCII bytes. "
            "What model string does the module report?",
            "a string identifying an R503 or R503-M22",
            observed=model,
            machine={"status": st, **f},
        )
        sess.observation(
            "CXC-R503-EV-15",
            "Count the bytes of the ReadProdInfo acknowledgement exactly. Is the "
            "Length field 49 (46 ProdInfo bytes) or 53 (50 ProdInfo bytes)?",
            "one of exactly two values — the Length field implies 46, the column "
            "header and summed field rows imply 50",
            observed=f"len_field={len_field} prodinfo_bytes={prodinfo_bytes} "
                     f"verdict={verdict}",
            machine={"status": st, **f},
        )

        banner("EV-03 / EV-15 — VERDICT")
        print(f"  PARAM_FPM_MODEL   : {model}")
        print(f"  Length field      : {len_field}")
        print(f"  ProdInfo bytes    : {prodinfo_bytes}   (counted ON THE WIRE)")
        print(f"  EV-15 verdict     : {verdict}")
        print()
        print("  ⛔ If the model string is NOT an R503/R503-M22, every rail figure in")
        print("     [SPEC:CXC-R503] section 1 is provisional for THIS part and pin 1")
        print("     may want 5 V. Stop and re-read section 1 before wiring anything else.")
        provenance_guard(sess, link, ["CXC-R503-EV-03", "CXC-R503-EV-15"])
        print(f"\n  session log: {sess.path}")
    finally:
        link.close()
        sess.close()


def cmd_raw(args):
    """Escape hatch: send arbitrary command content bytes, get the ack verbatim."""
    link = open_link(args)
    try:
        verb = "badsum" if args.badsum else "cmd"
        st, f, d = link.command(verb, args.hex, args.deadline_ms, args.interbyte_ms,
                                deadline=(args.deadline_ms / 1000.0) + 5.0)
        show(st, f, d, label=f"{verb} {args.hex}")
    finally:
        link.close()


def cmd_rawtx(args):
    """Escape hatch, one level below `raw`: transmit exactly these bytes.

    ⛔ No header is added, no Length is computed, no checksum is appended. This is
       how a deliberately malformed stream is put on real hardware.
    """
    link = open_link(args)
    try:
        st, f, d = link.command("rawtx", args.hex, args.deadline_ms, args.interbyte_ms,
                                deadline=(args.deadline_ms / 1000.0) + 5.0)
        show(st, f, d, label=f"rawtx {args.hex}")
    finally:
        link.close()


def cmd_led(args):
    link = open_link(args)
    try:
        show(*link.command("led", args.ctrl, args.speed, args.colour, args.times),
             label="AuraLedConfig 0x35")
    finally:
        link.close()


def cmd_listen(args):
    """Capture unsolicited traffic WITHOUT transmitting. [CXC-R503-UNSOLICITED-0X55]"""
    link = open_link(args)
    try:
        st, f, d = link.command("listen", args.ms, deadline=(args.ms / 1000.0) + 6.0)
        show(st, f, d, label=f"listen {args.ms} ms (no transmit)")
        if f.get("count", "0") != "0":
            print("\n  Bytes arrived with nothing sent. A single 0x55 is the documented")
            print("  power-on / post-SoftRst handshake byte.")
    finally:
        link.close()


def cmd_negctl(args):
    """⛔ THE NEGATIVE CONTROL BATTERY — prove this probe can FAIL.

    An instrument that can only report success is not an instrument. Run this with
    NO R503 ATTACHED. Every result is printed verbatim.
    """
    observer = args.observer or "unattended"
    sess = Session("NEGATIVE-CONTROL", observer)
    link = open_link(args, log=sess)
    failures = []

    def record(name, ok, detail):
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}\n         {detail}")
        sess.write("negative_control", {"name": name, "verdict": mark, "detail": detail})
        if not ok:
            failures.append(name)

    try:
        banner("NEGATIVE CONTROL — run this with NO R503 ATTACHED")
        st, f, d = link.command("ping")
        show(st, f, d, label="probe identity")
        sess.write("probe_identity", f)

        # --------------------------------------------------------------
        banner("NC-1  every command reports a CLEAN TIMEOUT with no module")
        for verb, argv in (("fwver", ()), ("prodinfo", ()),
                           ("led", (LED_ALWAYS_ON, 0, 1, 0)), ("cmd", ("3A",))):
            t0 = time.monotonic()
            st, f, d = link.command(verb, *argv, deadline=8.0)
            wall = time.monotonic() - t0
            ok = (st == "timeout_no_bytes") and (wall < 6.0)
            record(f"NC-1 {verb}",
                   ok,
                   f"status={st} rx_total={f.get('rx_total')} "
                   f"elapsed_ms={f.get('elapsed_ms')} host_wall_s={wall:.3f} "
                   f"(want status=timeout_no_bytes, and it must RETURN)")

        # --------------------------------------------------------------
        banner("NC-2  a deliberately corrupted checksum is REJECTED")
        print("  (a) parser level — the firmware's own codec self-test")
        st, f, d = link.command("selftest", deadline=20.0)
        neg_lines = [x for x in d if x.startswith("SELFTEST") and "neg_" in x]
        for line in neg_lines:
            print("      " + line)
        record("NC-2a codec selftest",
               st == "ok" and f.get("fail") == "0",
               f"status={st} pass={f.get('pass')} fail={f.get('fail')}; "
               f"{len(neg_lines)} of those cases are explicit negative controls")

        print("\n  (b) THROUGH THE REAL UART, using internal loopback.")
        print("      This is also the POSITIVE control: a clean timeout only means")
        print("      something if the SAME code path succeeds when bytes DO arrive.")
        st, f, d = link.command("loopback", "on")
        show(st, f, d, label="loopback on")

        st, f, d = link.command("cmd", "3A", 500, 60, deadline=8.0)
        show(st, f, d, label="loopback + GOOD checksum (positive control)")
        record("NC-2b-pos loopback good checksum parses",
               st == "ok",
               f"status={st} sum_rx={f.get('sum_rx')} sum_calc={f.get('sum_calc')} "
               f"rx_total={f.get('rx_total')} (want ok — proves the path works)")

        st, f, d = link.command("badsum", "3A", 500, 60, deadline=8.0)
        show(st, f, d, label="loopback + CORRUPTED checksum (negative control)")
        record("NC-2b-neg loopback bad checksum rejected",
               st == "bad_checksum",
               f"status={st} sum_rx={f.get('sum_rx')} sum_calc={f.get('sum_calc')} "
               f"(want bad_checksum, and the two sums must DIFFER)")

        link.command("loopback", "off")

        # --------------------------------------------------------------
        banner("NC-3  NO UNBOUNDED WAIT — deadlines are honoured, both sides")
        for ms in (100, 250, 1000):
            t0 = time.monotonic()
            st, f, d = link.command("cmd", "3A", ms, 60, deadline=(ms / 1000.0) + 5.0)
            wall = time.monotonic() - t0
            reported = int(f.get("elapsed_ms", -1))
            # The firmware's own deadline must bound its report, and the host's wall
            # clock must be consistent with it. Slack is generous because USB CDC
            # scheduling is not the thing under test.
            ok = (st == "timeout_no_bytes") and (ms <= reported <= ms + 150) and wall < (ms / 1000.0) + 2.0
            record(f"NC-3 firmware deadline {ms} ms",
                   ok,
                   f"status={st} firmware elapsed_ms={reported} host_wall_s={wall:.3f} "
                   f"(want elapsed_ms in [{ms},{ms + 150}])")

        print("\n  Host-side deadline: send a line the firmware will NEVER answer")
        print("  (no '>' sentinel), and confirm the HOST returns rather than hanging.")
        t0 = time.monotonic()
        link.ser.write(b"this line has no sentinel and gets no response\n")
        link.ser.flush()
        st, f, d = link.command("ping", deadline=3.0)
        wall = time.monotonic() - t0
        record("NC-3 host does not desync on an unanswered line",
               st == "ok" and wall < 4.0,
               f"status={st} host_wall_s={wall:.3f} diagnostics={d} "
               f"(the '#' reply is ignored by the sentinel rule; the next command "
               f"still correlates)")

        # --------------------------------------------------------------
        banner("NC-4  a TRUNCATED frame is TIMEOUT_PARTIAL, never a false success")
        print("  Put a deliberately truncated frame on the real UART via loopback:")
        print("  header + address + PID + Length promising 3 more bytes, then NOTHING.")
        print("  The probe must report timeout_partial with rx_total=9 — distinguishing")
        print("  'bytes arrived and stopped' from 'nothing ever arrived'.")
        link.command("loopback", "on")
        st, f, d = link.command("rawtx", "EF01FFFFFFFF070003", 400, 60, deadline=8.0)
        show(st, f, d, label="rawtx truncated frame (9 bytes of a 12-byte frame)")
        record("NC-4 truncated frame -> timeout_partial",
               st == "timeout_partial" and f.get("rx_total") == "9",
               f"status={st} rx_total={f.get('rx_total')} elapsed_ms={f.get('elapsed_ms')} "
               f"(want status=timeout_partial AND rx_total=9 — anything else, "
               f"including 'ok', is a FAIL)")

        # ⛔ The two timeout kinds must be DISTINGUISHABLE, not merely both non-fatal.
        st2, f2, _ = link.command("cmd", "3A", 400, 60, deadline=8.0)
        # loopback is on, so this one SUCCEEDS; the contrast is the point.
        record("NC-4 partial is distinct from no-bytes and from ok",
               st == "timeout_partial" and st2 == "ok",
               f"truncated -> {st}, complete -> {st2}; and with no module attached "
               f"NC-1 gave timeout_no_bytes. Three outcomes, three names.")

        banner("NC-5  the unsolicited 0x55 is ABSORBED ON THE WIRE, not just in a test")
        print("  [CXC-R503-UNSOLICITED-0X55]: a bare unframed 0x55 arrives at power-on")
        print("  AND after every SoftRst. Inject one in front of a valid acknowledge")
        print("  packet through real UART hardware and confirm the sliding two-byte")
        print("  header window costs exactly one skipped byte and does NOT phase-shift.")
        st, f, d = link.command("rawtx", "55EF01FFFFFFFF07000300000A", 400, 60,
                                deadline=8.0)
        show(st, f, d, label="rawtx: 0x55 + a well-formed ack")
        record("NC-5 0x55 absorbed, frame still parses",
               st == "ok" and f.get("skipped") == "1" and f.get("pid") == "0x07"
               and f.get("conf") == "0x00",
               f"status={st} skipped={f.get('skipped')} skipped_hex={f.get('skipped_hex')} "
               f"pid={f.get('pid')} conf={f.get('conf')} "
               f"(want ok, skipped=1, skipped_hex=55, pid=0x07, conf=0x00)")

        print("\n  And a FALSE header (EF EF 01) must cost exactly 3 skipped bytes:")
        st, f, d = link.command("rawtx", "55AAEFEF01FFFFFFFF07000300000A", 400, 60,
                                deadline=8.0)
        show(st, f, d, label="rawtx: 55 AA EF | EF 01 + a well-formed ack")
        record("NC-5 resync past a false header",
               st == "ok" and f.get("skipped") == "3",
               f"status={st} skipped={f.get('skipped')} "
               f"skipped_hex={f.get('skipped_hex')} (want ok and skipped=3)")

        link.command("loopback", "off")

        # --------------------------------------------------------------
        # ⛔ PROVE THE BATTERY CAN REPORT FAIL.
        # Everything above PASSED. A battery that has only ever printed PASS is
        # indistinguishable from a battery that cannot print anything else — which
        # is the exact failure this whole exercise exists to prevent. So run one
        # case ENGINEERED to fail: assert NC-1's condition ("nothing on the line")
        # while loopback is ON, so bytes demonstrably ARE on the line.
        banner("NC-0  falsification check — this case MUST report FAIL")
        link.command("loopback", "on")
        st, f, d = link.command("fwver", deadline=8.0)
        show(st, f, d, label="fwver WITH loopback on (bytes ARE on the line)")
        would_pass = (st == "timeout_no_bytes")
        print(f"  NC-1's assertion applied here gives: "
              f"{'PASS' if would_pass else 'FAIL'}  (status={st})")
        link.command("loopback", "off")
        record("NC-0 the battery can report FAIL",
               not would_pass,
               f"NC-1's own predicate returned {'PASS' if would_pass else 'FAIL'} "
               f"against a line that is NOT silent (status={st}). It must return "
               f"FAIL. If this ever reads PASS, every NC-1 result above is worthless.")

        banner("NEGATIVE CONTROL — SUMMARY")
        if failures:
            print(f"  ⛔ {len(failures)} FAILED: {failures}")
        else:
            print("  All negative controls behaved as required.")
        print(f"\n  session log: {sess.path}")
        return 1 if failures else 0
    finally:
        link.close()
        sess.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", default=DEFAULT_SERIAL,
                    help="USB serial number of the S3 (NOT a device path); "
                         "default $R503_EXPECT_SERIAL")
    ap.add_argument("--observer", default=None, help="name recorded in the session log")
    ap.add_argument("-v", "--verbose", action="store_true", help="echo the line protocol")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("port", help="resolve the S3's device path from its USB serial").set_defaults(fn=cmd_port)
    sub.add_parser("ping", help="probe identity + build stamp + bound GPIOs").set_defaults(fn=cmd_ping)
    sub.add_parser("selftest", help="codec self-test on the real silicon; no R503 needed").set_defaults(fn=cmd_selftest)
    sub.add_parser("ev25", help="[CXC-R503-EV-25] GetFwVer + ring colour census (asks a human)").set_defaults(fn=cmd_ev25)
    sub.add_parser("ev03", help="[CXC-R503-EV-03]+[EV-15] ReadProdInfo, counted on the wire").set_defaults(fn=cmd_ev03)
    sub.add_parser("negctl", help="⛔ negative-control battery — prove the probe can FAIL").set_defaults(fn=cmd_negctl)

    p = sub.add_parser("raw", help="escape hatch: send arbitrary command content bytes")
    p.add_argument("hex", help="command content as hex, e.g. 3A (opcode + params)")
    p.add_argument("--deadline-ms", type=int, default=1000)
    p.add_argument("--interbyte-ms", type=int, default=60)
    p.add_argument("--badsum", action="store_true",
                   help="transmit with a DELIBERATELY WRONG checksum")
    p.set_defaults(fn=cmd_raw)

    p = sub.add_parser("rawtx", help="⛔ put VERBATIM bytes on the UART (no framing added)")
    p.add_argument("hex", help="exact bytes to transmit, e.g. EF01FFFFFFFF070003")
    p.add_argument("--deadline-ms", type=int, default=1000)
    p.add_argument("--interbyte-ms", type=int, default=60)
    p.set_defaults(fn=cmd_rawtx)

    p = sub.add_parser("led", help="AuraLedConfig 0x35 (no colour claim is made)")
    p.add_argument("ctrl", type=lambda s: int(s, 0))
    p.add_argument("speed", type=lambda s: int(s, 0))
    p.add_argument("colour", type=lambda s: int(s, 0))
    p.add_argument("times", type=lambda s: int(s, 0))
    p.set_defaults(fn=cmd_led)

    p = sub.add_parser("listen", help="capture unsolicited bytes without transmitting")
    p.add_argument("ms", nargs="?", type=int, default=3000)
    p.set_defaults(fn=cmd_listen)

    args = ap.parse_args()
    deny = {t.strip().upper() for t in
            os.environ.get("FLASH_DENY_SERIALS", "").replace(",", " ").split() if t.strip()}
    if not args.serial:
        sys.stderr.write("refusing: R503_EXPECT_SERIAL is unset and no --serial was given "
                         "(set it in the host overlay)\n")
        raise SystemExit(2)
    if not deny:
        sys.stderr.write("refusing: FLASH_DENY_SERIALS is unset or empty "
                         "(set it in the host overlay)\n")
        raise SystemExit(2)
    if args.serial.strip().upper() in deny:
        sys.stderr.write("refusing: --serial is on FLASH_DENY_SERIALS\n")
        raise SystemExit(2)
    rc = args.fn(args)
    raise SystemExit(rc or 0)


if __name__ == "__main__":
    main()
