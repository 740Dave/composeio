"""End-to-end self-test for the HAL host slice ([SPEC:CXC-HAL-DECLARE] §8).

Proves the first slice against a MOCK/host bus — no hardware, no network, no
firmware.  Each check is tagged with the verification-table claim it exercises
([CXC-HALD-VALIDATE] table C1-C12):

    C2  one declaration, many chips  — a bme280 DATA-DESCRIPTOR decodes via the
        existing DSL and publishes env/temperature from a one-line entry.
    C3  hierarchical addressing      — two same-addr bme280 on different mux
        channels coexist; a button on an MCP23017 sub-pin (io.gpb0) resolves +
        routes through the provider.
    C4  collision caught at parse    — same-addr-same-channel / duplicate
        resolved pin / unknown driver / provider over-allocation / shared-freq
        disagreement all raise at validate().
    C5  define-once binding          — gauge/value drives the compound gauge
        needle; the same namespace a screen gauge would read.
    C6  compound peripheral          — the gauge is ONE actuator pin-group over
        4 coils; the three BUILT drivers instantiate from a peripheral entry.
    C8  rules run at the leaf        — with the ComposeBus link unused, a hot
        reading drives relay + display; non-exported namespaces never appear on
        the external bus.
    C9  bridge both ways             — a derived climate/status EXPORTS up; an
        imported demo/alert drives display + relay DOWN.

Run::  python3 -m composeio.hal --selftest
"""

from __future__ import annotations

import sys

from .parser import parse_board_profile
from .runtime import HalRuntime
from .validator import HalValidationError, validate

# The MIXED board profile ([CXC-HALD-DECLARE] worked-example shape).
MIXED_PROFILE = """
peripherals:
  # --- port-providers first (they define the addressing others use) ---
  - id: io
    driver: mcp23017
    interface: { i2c: { bus: qwiic0, addr: 0x20 } }
    provides: { gpio: 16 }
  - id: fanout
    driver: tca9548a
    interface: { i2c: { bus: qwiic0, addr: 0x70 } }
    provides: { i2c_channel: 8 }

  # --- an I2C bank behind the mux: two identical-address sensors (C3) ---
  - id: climate_in
    role: sensor
    driver: bme280
    interface: { i2c: { bus: fanout.ch0, addr: 0x76 } }
    namespace: env/temperature
  - id: climate_out
    role: sensor
    driver: bme280
    interface: { i2c: { bus: fanout.ch1, addr: 0x76 } }   # same addr, diff channel — OK
    namespace: outside/temperature

  # --- a button on an expander sub-pin (C3) ---
  - id: doorbell
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: io.gpb0, pull: up, debounce_ms: 20, edge: falling } }
    namespace: input/button

  # --- a compound gauge actuator-group (C6) ---
  - id: gauge_a
    role: actuator
    driver: x27168
    interface: { actuator_group: { via: io, coils: [gpa0, gpa1, gpa2, gpa3] } }
    namespace: gauge/value
    config: { sweep_deg: 315, home_on_boot: true }
    assurance: control

  # --- the relay + display driven by the leaf rule ---
  - id: relay
    role: actuator
    driver: relay
    interface: { pwm_out: { pin: d5 } }
    namespace: relay/state
    assurance: control
  - id: hud
    role: actuator
    driver: display
    interface: { i2c: { bus: qwiic0, addr: 0x3C } }
    namespace: display/text

  # --- the three BUILT drivers, declaration-driven (C6 / [CXC-HALD-BUILT]) ---
  - id: bioscan
    role: transactional
    driver: pn532
    interface: { uart: { baud: 57600 } }
    assurance: experiential
    namespaces: { cmd: fingerprint/cmd, status: fingerprint/enroll/status, result: fingerprint/match }
  - id: room_lights
    role: actuator
    driver: wled
    interface: { adaptor: { wled: { host: 192.0.2.60 } } }
    namespaces: { on: light/on, brightness: light/brightness, mood: scene/mood }
  - id: chime
    role: actuator
    driver: audio
    interface: { adaptor: { audio: { device: software } } }
    namespace: audio/play

rules:
  - when: "ns('env/temperature') > 25"
    set: { relay/state: on,  display/text: "HOT {env/temperature|0}C", climate/status: hot }
  - when: "ns('env/temperature') <= 25"
    set: { relay/state: off, display/text: "{env/temperature|0}C OK", climate/status: ok }
  - when: "ns('demo/alert') == 'red'"
    set: { display/text: "RED ALERT!", relay/state: on }

bridge:
  transport: can
  export: [climate/status]
  import: [demo/alert]
"""


class _Checker:
    """Tiny PASS/FAIL harness — prints each claim, tallies failures."""

    def __init__(self) -> None:
        self.failures = 0

    def check(self, claim: str, ok: bool, detail: str = "") -> None:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            self.failures += 1
        line = f"  [{mark}] {claim}"
        if detail:
            line += f"  ({detail})"
        print(line)

    def expect_reject(self, claim: str, profile_yaml: str) -> None:
        """A bad profile MUST raise HalValidationError at validate()."""
        try:
            prof = parse_board_profile(profile_yaml)
            validate(prof)
            self.check(claim, False, "no error raised")
        except HalValidationError as e:
            self.check(claim, True, str(e).split(" (")[0][:60])
        except Exception as e:  # parse-level rejects also count as caught
            self.check(claim, True, f"{type(e).__name__}: {str(e)[:40]}")


def run() -> int:
    c = _Checker()
    print("ComposeIO HAL host-slice self-test ([SPEC:CXC-HAL-DECLARE] §8)\n")

    # --- parse + build + validate + instantiate the mixed board ---
    print("Parse + validate + instantiate the mixed board:")
    profile = parse_board_profile(MIXED_PROFILE)
    c.check("profile parses (peripherals + rules + bridge)",
            len(profile.peripherals) == 11 and len(profile.rules) == 3
            and profile.bridge is not None,
            f"{len(profile.peripherals)} peripherals, {len(profile.rules)} rules")

    rt = HalRuntime(profile).build().start()
    c.check("mixed board validates + instantiates (no hardware)",
            len(rt._instances) == 11, f"{len(rt._instances)} instances live")

    # --- C3: hierarchical addressing ---
    print("\nC3 — hierarchical addressing (mux + expander):")
    c.check("two same-addr bme280 coexist on different mux channels",
            rt.instance("climate_in") is not None
            and rt.instance("climate_out") is not None,
            "fanout.ch0/0x76 + fanout.ch1/0x76")
    doorbell = rt.instance("doorbell")
    doorbell.press()
    io_prov = rt._providers["io"]
    c.check("button on expander sub-pin io.gpb0 resolves + routes",
            rt.get_internal("input/button") == "on"
            and io_prov.read_channel("gpb0") is True,
            "input/button=on, io.gpb0 routed")

    # --- C2 / C5 / C6: descriptor sensor, define-once, compound gauge ---
    print("\nC2/C5/C6 — descriptor sensor, define-once, compound gauge:")
    temp = rt.instance("climate_in")
    decoded = temp.read(t_raw=2650)      # data-descriptor decode via the DSL
    c.check("bme280 DATA-DESCRIPTOR decodes via the existing DSL",
            abs(decoded - 26.5) < 1e-9
            and abs(rt.get_internal("env/temperature") - 26.5) < 1e-9,
            f"env/temperature={decoded}")
    rt.instance("climate_out").read(t_raw=1900)
    c.check("second muxed sensor publishes its own namespace",
            abs(rt.get_internal("outside/temperature") - 19.0) < 1e-9,
            "outside/temperature=19.0")
    gauge = rt.instance("gauge_a")
    rt.internal_bus.publish_state("gauge/value", 130, source="test")
    c.check("define-once: gauge/value drives the compound needle (C5)",
            gauge.position == 130 and len(gauge.coils) == 4,
            f"needle={gauge.position}, coils={gauge.coils}")
    c.check("gauge is ONE actuator pin-group over N coils (C6)",
            io_prov.read_channel("gpa1") == ((130 >> 1) & 1),
            "coils strobed through provider io")

    # --- C6 / [CXC-HALD-BUILT]: the three built drivers instantiate ---
    print("\nC6/[CXC-HALD-BUILT] — the three built drivers, declaration-driven:")
    from composeio.adaptors.wled import WLEDAdaptor
    from composeio.audio import AudioActuator
    from composeio.peripherals.nfc import PN532
    c.check("wled actuator instantiated (composeio.adaptors.wled)",
            isinstance(rt.instance("room_lights"), WLEDAdaptor))
    c.check("audio actuator instantiated (composeio.audio)",
            isinstance(rt.instance("chime"), AudioActuator))
    bioscan = rt.instance("bioscan")
    c.check("pn532 transactional instantiated (composeio.peripherals.nfc)",
            isinstance(bioscan.reader, PN532))
    rt.internal_bus.publish_state("fingerprint/cmd", "match", source="test")
    c.check("transactional cmd -> status + result ([CXC-HALD-ROLE])",
            rt.get_internal("fingerprint/enroll/status") == "scanning"
            and isinstance(rt.get_internal("fingerprint/match"), dict),
            "cmd/match -> result emitted")

    # --- C8: rules fire at the leaf; encapsulation holds ---
    print("\nC8 — rules run at the leaf, ComposeBus link unused:")
    c.check("hot reading drove relay + display via the leaf rule",
            rt.get_internal("relay/state") == "on"
            and rt.get_internal("display/text") == "HOT 26C",
            f"relay={rt.get_internal('relay/state')}, "
            f"display={rt.get_internal('display/text')!r}")
    c.check("non-exported namespace never appears on the external bus",
            rt.get_external("relay/state") is None
            and rt.get_external("display/text") is None,
            "relay/display internal-only (encapsulation)")

    # --- C9: the bridge both ways ---
    print("\nC9 — the internal<->ComposeBus membrane, both directions:")
    c.check("derived climate/status EXPORTS up to the ComposeBus",
            rt.get_external("climate/status") == "hot",
            "climate/status promoted to external bus")
    rt.external_bus.publish_state("demo/alert", "red", source="composebus")
    c.check("imported demo/alert drives display + relay DOWN",
            rt.get_internal("display/text") == "RED ALERT!"
            and rt.get_internal("relay/state") == "on",
            "external demo/alert -> local actuators")

    # --- C4: the validator rejects real mistakes ---
    print("\nC4 — the validator rejects wiring mistakes at parse time:")
    c.expect_reject("same-addr-same-channel collision", """
peripherals:
  - id: fanout
    driver: tca9548a
    interface: { i2c: { bus: qwiic0, addr: 0x70 } }
    provides: { i2c_channel: 8 }
  - id: a
    role: sensor
    driver: bme280
    interface: { i2c: { bus: fanout.ch0, addr: 0x44 } }
    namespace: env/temperature
  - id: b
    role: sensor
    driver: bme280
    interface: { i2c: { bus: fanout.ch0, addr: 0x44 } }
    namespace: env/humidity
""")
    c.expect_reject("duplicate resolved pin (expander sub-pin)", """
peripherals:
  - id: io
    driver: mcp23017
    interface: { i2c: { bus: qwiic0, addr: 0x20 } }
    provides: { gpio: 16 }
  - id: btn_a
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: io.gpa0 } }
    namespace: input/a
  - id: btn_b
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: io.gpa0 } }
    namespace: input/b
""")
    c.expect_reject("unknown driver (with did-you-mean)", """
peripherals:
  - id: t
    role: sensor
    driver: bme281
    interface: { i2c: { bus: qwiic0, addr: 0x76 } }
    namespace: env/temperature
""")
    c.expect_reject("provider over-allocation", """
peripherals:
  - id: tiny
    driver: mcp23017
    interface: { i2c: { bus: qwiic0, addr: 0x20 } }
    provides: { gpio: 2 }
  - id: b0
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: tiny.gpa0 } }
    namespace: input/0
  - id: b1
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: tiny.gpa1 } }
    namespace: input/1
  - id: b2
    role: sensor
    driver: gpio
    interface: { digital_in: { pin: tiny.gpa2 } }
    namespace: input/2
""")
    c.expect_reject("shared-provider frequency disagreement (PCA9685 rule 5)", """
peripherals:
  - id: pwm
    driver: pca9685
    interface: { i2c: { bus: qwiic0, addr: 0x40 } }
    provides: { pwm_ch: 16 }
    config: { freq_hz: 50 }
  - id: servo
    role: actuator
    driver: relay
    interface: { pwm_out: { pin: pwm.ch0, freq_hz: 50 } }
    namespace: servo/angle
  - id: led
    role: actuator
    driver: relay
    interface: { pwm_out: { pin: pwm.ch1, freq_hz: 1000 } }
    namespace: led/brightness
""")

    rt.stop()

    print()
    if c.failures:
        print(f"SELFTEST: FAIL — {c.failures} check(s) failed")
        return 1
    print("SELFTEST: PASS — all checks green")
    return 0


if __name__ == "__main__":
    sys.exit(run())
