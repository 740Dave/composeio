# ESP-IDF component CMakeLists.txt for the main component
# PlatformIO with framework=espidf uses this to build the main/ directory

# [SPEC:CXC-CONFIG-BUS] [CXC-CONFIG-BUS-RELAY] — the headless RELAY node
# (env:esp32_can_relay, cache var CXC_RELAY=1).  CAN upstream, UART
# downstream, no display, no scene, no config of its own.  It is the ONE
# node that legitimately holds TWO transports, so it does not go through
# bus_transport() at all (see relay_main.cpp).
#
# MUST sit ABOVE every board branch and above the "not esp32 -> DSL only"
# early return: the relay is defined by its role, not by its silicon.
if(CXC_RELAY)
    idf_component_register(
        SRCS
            "relay_main.cpp"
            "config_relay.cpp"
            "config_chunk.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            # config_bus is compiled in because bus_uart/bus_can call its
            # receive hook.  On a relay it is INERT: config_bus_init() is
            # never called, so its namespace is empty and every frame falls
            # straight through to the raw sink.  Linking a stub instead would
            # be a second implementation of "is this mine?" to keep in step.
            "config_bus.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "config_fetch.cpp"      # device_id() only
            "restart.cpp"           # bus_uart/bus_can call its receive hook
            "dsl.cpp"               # restart.cpp's truthiness canon
            "bus_mqtt.cpp"
            "bus_transport_select.cpp"
            "wifi.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            mqtt
    )
    # relay_main.cpp is behind CXC_RELAY_APP.  A CMake CACHE VARIABLE
    # (CXC_RELAY, from board_build.cmake_extra_args) selects THIS BRANCH but
    # never reaches the compiler — the quirk that has already cost this tree
    # two defects — so the source guard has to be a real
    # target_compile_definitions, verified in compile_commands.json.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CXC_RELAY_APP
        CXC_DEVICE_PREFIX="relay"
        # Bench defaults for an ESP32-S3 devkit + SN65HVD230.  Only defaults:
        # NVS (can.tx/can.rx/can.bitrate, uart.tx/uart.rx) overrides them at
        # runtime, so a rig is re-pointed without a rebuild.
        CXC_CAN_TX_GPIO=5
        CXC_CAN_RX_GPIO=4
        CXC_UART_TX_GPIO=17
        CXC_UART_RX_GPIO=18)
    return()
endif()

# [SPEC:CXC-BRINGUP] [CXC-VISION-EPISTEMICS] — the rack_triple_gauge
# ON-TARGET DIAGNOSTIC (env:rack_triple_gauge_selftest, cache var
# CXC_G3_DIAG=1).  Flashed FIRST, before the real firmware, to answer "what
# can the MCU reach?".
#
# ⛔ IT IS ITS OWN BRANCH, NOT A FLAG ON CXC_RACK_TRIPLE_GAUGE, and the SRCS
# list is THREE FILES.  That minimality is the design, not laziness: the
# whole value of a diagnostic is that a failure has few possible causes, and
# every TU linked in is another suspect.  No WiFi, no MQTT, no NVS, no
# config fetch, no scene, no announcer, no transport seam — none of which
# can be a reason this build misbehaves, because none of them is present.
#
# ⛔ mcp23017.cpp IS DELIBERATELY ABSENT.  gauge_diag.cpp re-implements bus
# recovery and register access rather than linking the driver the real
# firmware uses.  A diagnostic that runs the driver under test cannot see a
# defect in that driver — it reads the answer back through the same
# assumption that wrote it, which is [CXC-VISION-EPISTEMICS]
# rule-self-verification.  Board NUMBERS are still single-sourced from
# include/rack_triple_gauge_panel.h; only the transport code is duplicated.
#
# It defines CXC_G3_DIAG_APP and NOT CXC_RACK_TRIPLE_GAUGE_APP, so
# gauge_main.cpp's app_main() cannot be pulled in alongside this one's.
#
# MUST sit ABOVE the "not esp32 -> DSL only" early return, same as its
# neighbours: esp32c3 hits that guard.
#
#   pio run -e rack_triple_gauge_selftest -t upload
if(CXC_G3_DIAG)
    idf_component_register(
        SRCS
            "gauge_diag.cpp"
            # The build stamp, and it earns its place: it is the only way to
            # know WHICH binary is on the board, and its generated header is
            # rewritten every build so it can never go stale.
            "build_stamp.cpp"
        INCLUDE_DIRS
            "."
            "../include"
        REQUIRES
            driver          # i2c_master, gpio
            esp_timer
            esp_hw_support  # esp_read_mac
    )
    # platformio.ini build_flags never reach an ESP-IDF component compile for
    # framework=espidf — silently dropped, three defects paid for already —
    # and CXC_G3_DIAG is a CMake CACHE VARIABLE that selects this branch and
    # never reaches the compiler.  The guard the source actually reads has to
    # be minted here.  Verify in
    # .pio/build/rack_triple_gauge_selftest/compile_commands.json.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_G3_DIAG_APP)
    return()
endif()

# [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW] [CXC-VISION-SEMANTIC-REALIZATION]
# — rack_triple_gauge (env:rack_triple_gauge, cache var
# CXC_RACK_TRIPLE_GAUGE=1): David's first custom PCB, and the fleet's FIRST
# NON-RASTER REALIZATION BACKEND.  Three x27.168 stepper needles behind an
# MCP23017 -> 3x L293D, three 12 V LED backlights on LEDC, ESP32-C3 Super
# Mini as a direct WiFi/MQTT peer.
#
# NO GLASS, so NO board_profile(), NO canvas, NO widget TUs.  It defines
# realization_backend() instead (include/realization.h) — the PEER seam.
# main.cpp is NOT in this build; gauge_main.cpp is.
#
# MUST sit ABOVE the "not esp32 -> DSL only" early return: esp32c3 hits that
# guard, and this build is defined by its ROLE, not by its silicon — the
# same reasoning the CXC_RELAY and CXC_CAN_BRINGUP branches give.
#
# The SRCS list is deliberately SMALL.  bus_can/bus_uart/config_bus/
# config_chunk are here NOT because this board uses them but because
# bus_transport_select.cpp registers every substrate it can select and
# config_source.cpp calls into the config-bus path — the same
# link-completeness reason the two branches above state.  They are INERT.
if(CXC_RACK_TRIPLE_GAUGE)
    idf_component_register(
        SRCS
            "gauge_main.cpp"
            "boards/rack_triple_gauge.cpp"
            "x27168.cpp"
            "mcp23017.cpp"
            "gauge_bind.cpp"
            "yaml_min.cpp"
            "announce.cpp"
            # [SPEC:CXC-FRESHNESS] the C++ LEAF FRESHNESS RECEIVER.  Three files
            # and the split is deliberate: freshness.cpp is the JUDGEMENT and
            # depends on NOTHING (so env:native_freshness can host-compile it and
            # mutate it); freshness_yaml.cpp reads the declaration out of the same
            # document the scene came from; freshness_bus.cpp is the only one that
            # touches ESP-IDF.  It goes beside announce.cpp because the two are
            # halves of one contract - announce PUBLISHES this board's heartbeat,
            # freshness RECEIVES its peers'.
            "freshness.cpp"
            "freshness_yaml.cpp"
            "freshness_bus.cpp"
            "ns_announce.cpp"
            "build_stamp.cpp"
            "bus_mqtt.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            "bus_frame.cpp"
            "bus_transport_select.cpp"
            "config_bus.cpp"
            "config_chunk.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "config_fetch.cpp"
            "restart.cpp"
            "dsl.cpp"
            "wifi.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            mqtt
            mdns
    )
    # EVERY compile-time switch is HERE.  platformio.ini build_flags never
    # reach an ESP-IDF component compile for framework=espidf — silently
    # dropped, three defects paid for already — and CXC_RACK_TRIPLE_GAUGE
    # itself is a CMake CACHE VARIABLE that selects this branch and NEVER
    # reaches the compiler, so the guard the source actually reads
    # (CXC_RACK_TRIPLE_GAUGE_APP) has to be minted separately.
    # Verify in .pio/build/rack_triple_gauge/compile_commands.json.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CXC_RACK_TRIPLE_GAUGE_APP
        # Short: device_id()'s buffer is char[16] and this id is also the
        # prefix of every `_device/<id>/...` namespace.
        CXC_DEVICE_PREFIX="gauge3"
        # A DIRECT WiFi/MQTT peer — ruled.  DP-CIO-6 applied: this part has
        # a radio, so it takes the easy substrate.  Still NVS-overridable,
        # because a rack-mounted board with no console is exactly the case
        # the transport control plane exists for.
        CXC_BUS_TRANSPORT_DEFAULT="mqtt")

    # [env:rack_triple_gauge_sweep] — the BENCH variant carrying the
    # full-scale sweep test.  Its own env, not a flag on the normal one, so
    # there is never any doubt about which binary is on the board while
    # somebody is watching the needles.
    if(CXC_G3_SWEEP)
        target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_G3_SWEEP_TEST)
    endif()
    return()
endif()

# [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW] — the ESP32-C3 Super Mini CAN
# FIRST-LIGHT node (env:esp32_c3_can_bringup, cache var CXC_CAN_BRINGUP=1).
# Headless: no glass, no scene, no config fetch, no radio.  It publishes a
# heartbeat and twai_get_status_info() upward and logs `throttle/left` on the
# way down — the instrument that makes the TWAI error state observable before
# anybody writes a recovery policy for it (see can_bringup_main.cpp).
#
# MUST sit ABOVE the "not esp32 -> DSL only" early return: esp32c3 hits that
# guard, and this build is defined by its ROLE, not by its silicon — same
# reasoning as the CXC_RELAY branch.
#
# The SRCS list is the CXC_RELAY list with the relay swapped for the bring-up
# app.  bus_transport_select/bus_mqtt/bus_uart/wifi are here NOT because this
# app uses them but because config_bus.cpp calls bus_transport(), and
# bus_can.cpp calls config_bus_on_frame() on every arrival.  config_bus is
# INERT (never initialised, empty namespace, every frame falls through) —
# linking a stub instead would be a second implementation of "is this mine?"
# to keep in step.
if(CXC_CAN_BRINGUP)
    idf_component_register(
        SRCS
            "can_bringup_main.cpp"
            "bus_can.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_mqtt.cpp"
            "bus_transport_select.cpp"
            "config_bus.cpp"
            "config_chunk.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "config_fetch.cpp"      # device_id() only
            "restart.cpp"           # bus_can calls its receive hook
            "dsl.cpp"               # restart.cpp's truthiness canon
            "wifi.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            mqtt
    )
    # EVERY compile-time switch is HERE, and none of them is a
    # platformio.ini build_flag: for framework=espidf those never reach an
    # IDF component compile — they are silently dropped, which has already
    # cost this tree three defects (a board rendering in the wrong tier, a
    # board answering to the wrong device-id prefix, and a source guard that
    # was dead while looking correct).  CXC_CAN_BRINGUP itself is a CMake
    # CACHE VARIABLE: it selects this branch and NEVER reaches the compiler,
    # so CXC_CAN_BRINGUP_APP must be minted separately.
    # Verify in .pio/build/esp32_c3_can_bringup/compile_commands.json.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CXC_CAN_BRINGUP_APP
        # Keep it SHORT — device_id()'s buffer is char[16], and this device's
        # id also becomes the `_device/<id>/…` prefix of every namespace that
        # has to be typed into CAN_BRIDGE_REGISTER by hand.
        CXC_DEVICE_PREFIX="c3can"
        # Bench wiring: SN65HVD230 on the C3 Super Mini.  Compile DEFAULTS
        # only — NVS (can.tx / can.rx / can.bitrate) overrides them at runtime
        # so the rig is re-pointed by a bus command, not a rebuild.
        #
        # CTX=5 / CRX=6 — the wiring as BUILT, confirmed against the board by
        # the owner 2026-08-12.  GPIO4 is not connected to anything.
        #
        # The first bring-up ran with CRX=4 and never received a frame, and the
        # instruments said so correctly without being able to say WHY: a pin
        # with nothing wired to it and a pin whose transceiver has no ground
        # reference produce the SAME reading.  Both faults were present at once
        # (the second was a missing ground between the breadboard and the Pi
        # HAT), which is exactly the situation the "never let two suspects be
        # live at the same time" rule exists to prevent — and here the two were
        # not sequential mistakes but simultaneous ones, so no ordering of
        # tests would have separated them without changing the hardware.
        CXC_CAN_TX_GPIO=5
        CXC_CAN_RX_GPIO=6
        CXC_CAN_BITRATE=500000)

    # [env:esp32_c3_pin_drive_test] — a BENCH-ONLY variant carrying the slow
    # square-wave pin drive test (can_bringup_main.cpp).  Its own env, not a
    # flag on the normal one, so there is never any doubt about which binary is
    # on the board while somebody is holding a meter to it.
    #
    # CXC_PIN_DRIVE is a CMake CACHE VAR and does NOT reach the compiler — the
    # same trap this branch already documents — so the guard the source
    # actually reads has to be minted here as a real compile definition.
    if(CXC_PIN_DRIVE)
        target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_PIN_DRIVE_TEST)
    endif()
    return()
endif()

# [SPEC:CXC-BRINGUP] [SPEC:CXC-BOARD-HW] — the ESP32-C3 Super Mini +
# LCDWIKI MSP3525 3.5" ST7796S 480x320 panel, fed by CAN
# (env:esp32_c3_st7796, cache var CXC_C3_ST7796=1).
#
# The SAME app shell, canvas, widgets, scene builder and capture server as
# env:cyd, with ONE substitution at the board seam:
#   boards/classic_cyd.cpp + ili9341.cpp -> boards/esp32c3_st7796.cpp
#   touch_xpt2046.cpp                    -> (none; no pins left for touch)
# Nothing above the seam changes.
#
# BAND TIER, and not by preference.  480*320*2 = 307,200 B on a part with
# ~400 KB of SRAM shared with IDF, FreeRTOS, WiFi and TWAI — a full frame is
# impossible, so there is deliberately NO CXC_BAND_HEIGHT here and the
# 16-scanline default stands (480*16*2 = 15,360 B, 20 bands/frame).
#
# MUST sit ABOVE the "not esp32 -> DSL only" early return: esp32c3 hits that
# guard, and this board DOES drive glass — the same reasoning as the
# WAVESHARE_C6 branch.
if(CXC_C3_ST7796)
    idf_component_register(
        SRCS
            "main.cpp"
            "boards/esp32c3_st7796.cpp"
            "canvas_esp32.cpp"
            "led.cpp"
            "gauge.cpp"
            "label.cpp"
            "rectangle.cpp"
            "circle.cpp"
            "button.cpp"
            "placeholder.cpp"
            "image.cpp"
            "sprite.cpp"
            "relay.cpp"
            "checkbox.cpp"
            "callout.cpp"
            "emit_button.cpp"
            "tab.cpp"
            "checklist.cpp"
            "master_detail.cpp"
            "chat_thread.cpp"
            "bus_monitor.cpp"
            "text_wrap.cpp"
            "scroll_list.cpp"
            "overlay.cpp"
            "dialog.cpp"
            "keyboard.cpp"
            "coordinate.cpp"
            "text_input.cpp"
            "capture_http.cpp"
            "wifi.cpp"
            "touch_dispatch.cpp"
            "yaml_min.cpp"
            "config_fetch.cpp"
            "compose_scene.cpp"
            "dsl.cpp"
            "dsl_selftest.cpp"
            "reactive_core_canonical.cpp"
            # The ComposeBus TRANSPORT SEAM (DP-CIO-6): the app shell depends
            # on bus_transport.h, never on a substrate.  One transport is
            # SELECTED per boot — here the compile default is "can", because
            # the throttle values this board renders arrive over CAN.
            "bus_mqtt.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            "bus_transport_select.cpp"
            # [SPEC:CXC-CONFIG-BUS] — linked for the same reason as every
            # other display env.  INERT here: config_source is `http`, so
            # config_bus is never armed.
            "config_chunk.cpp"
            "config_bus.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "restart.cpp"
            # [SPEC:CXC-BOARD-HW] workstream 0.2 — DEVICE SELF-ANNOUNCEMENT.  In the
            # SHARED app shell, never a board profile, for the same reason restart.cpp
            # is ([CXC-ER-C19]): the fleet is only as enumerable as the last board
            # somebody remembered to edit.  announce.cpp names NO substrate — it asks
            # the BusTransport seam for everything it reports (DP-CIO-6).
            # build_stamp.cpp is a separate 40-line TU on purpose: it includes the
            # per-build generated gen/build_stamp.h, so it recompiles on EVERY build,
            # and confining that to a tiny file is what keeps a never-stale build
            # fingerprint from costing a main.cpp rebuild each time (see announce.h).
            # ns_announce.cpp is the OTHER HALF of the announcement and is not
            # optional beside it: announce.cpp publishes VALUES on `_device/<id>/*`,
            # namespaces a CAN bridge CANNOT NAME (the wire id is a CRC over the
            # full name including the device id, so a `_device/*` prefix cannot be
            # expanded without already knowing every device id — the thing being
            # discovered).  ns_announce.cpp publishes the NAMES over the one
            # well-known root every bridge hardcodes, which is what makes those
            # values reachable over CAN at all ([PLAN G2]).  A board built with
            # one and not the other announces into a void.
            "announce.cpp"
            "freshness.cpp"
            "freshness_yaml.cpp"
            "freshness_bus.cpp"
            "ns_announce.cpp"
            "build_stamp.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            esp_http_server
            mqtt
    )

    # EVERY compile-time switch is HERE.  None of them can be a
    # platformio.ini build_flag: for framework=espidf those never reach an
    # IDF component compile and are silently dropped (three defects so far —
    # a board rendering in the wrong tier, a board answering to the wrong
    # device-id prefix, and a source guard that was dead while looking
    # correct).  CXC_C3_ST7796 itself is a CMake CACHE VARIABLE: it selects
    # this branch and NEVER reaches the compiler.
    # Verify in .pio/build/esp32_c3_st7796/compile_commands.json, WITH a
    # negative control — the bare cache var must appear in ZERO TUs.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        # Board identity -> device_id() == "c3lcd-c28020".  Without it the
        # board answers to "cyd-<mac3>", 404s its own scene and silently
        # falls back to another board's dashboard.  Keep it SHORT:
        # device_id()'s buffer is char[16].
        #
        # DELIBERATELY DISTINCT from the headless CAN node's "c3can" prefix,
        # which is the SAME SILICON in a different role.  Two roles on one
        # MAC must not answer to one id, or they fight over one scene.
        CXC_DEVICE_PREFIX="c3lcd"

        # THE DEFAULT SUBSTRATE IS CAN, not mqtt.  The throttle values this
        # panel exists to draw are relayed onto the CAN segment by
        # composeio-can-bridge on rpi5; MQTT would work too but would make
        # the CAN path untested, which is the point of the board.  NVS
        # `transport` still overrides at runtime.
        CXC_BUS_TRANSPORT_DEFAULT="can"

        # ⚠ LISTEN-ONLY: RECEIVE ONLY, NEVER TRANSMIT, NEVER ACK.
        #
        # A WORKAROUND FOR A BROKEN TRANSCEIVER, NOT A DESIGN, and the ONLY
        # env in this file that sets it.  Bench 2026-08-12: the SN65HVD230's
        # DRIVER is dead (the Pi's bus-errors counter unmoved across ten
        # 1.5 s dominant attempts; -0.6 mV across CANH/CANL with the driver
        # input held low; two separate modules identical) while its RECEIVER
        # is proven — 1,136 frames at 500 kbit/s, bus_err=0, twice an hour
        # apart.  In NORMAL mode this node reaches BUS-OFF in ~5 s and then
        # stops RECEIVING too, because ESP-IDF does not auto-recover and
        # bus_can.cpp never calls twai_initiate_recovery().
        #
        # It is announced at ERROR level on every boot in plain words, every
        # publish through the transport is REFUSED rather than dropped, and
        # it is reversible over the bus without a reflash:
        #     _device/c3lcd-c28020/transport = "can listen=0"
        # DELETE THIS LINE once the replacement transceivers are soldered.
        CXC_CAN_LISTEN_ONLY=1

        # Bench wiring, shared with env:esp32_c3_can_bringup and confirmed
        # against the board by the owner: CTX=GPIO5, CRX=GPIO6, 500 kbit/s.
        # GPIO4 is connected to nothing.  Compile DEFAULTS only — NVS
        # (can.tx / can.rx / can.bitrate / can.listen) overrides at runtime.
        CXC_CAN_TX_GPIO=5
        CXC_CAN_RX_GPIO=6
        CXC_CAN_BITRATE=500000

        # ---- SUB-WIDGET DAMAGE ([CXC-ER-DAMAGE]) --------------------
        #
        # OPT-IN, PER BOARD, ON PURPOSE.  The render tier is shared with
        # the Hosyond S3, the Waveshare C6, the classic CYD and the
        # Qualia; three of those are not on a bench tonight and one of
        # them (the Hosyond) is LIVE in front of the owner rendering
        # this same widget.  Under-invalidation is a silent, content-
        # dependent artifact, so the mechanism defaults OFF and a board
        # takes it only when somebody can audit it there.
        #
        # OFF, every one of those boards keeps the exact pre-existing
        # code path: Widget::damage_rows returns false, the scene falls
        # back to the band mask, and repaint_dirty_bands walks bands.
        #
        # ON, a changed widget reports the rows it actually redrew and
        # only those rows are rasterised and transferred.  Measured on
        # THIS board: a one-segment thrust change went from 14 bands /
        # 224 rows to 2 spans / ~40 rows.
        CXC_FINE_DAMAGE=1

        # The instrument, not the feature.  Per-scanline CRC of what was
        # SENT to the panel + audit_rows() to re-render and compare.
        # ~1.3 KB of RAM and a CRC per transferred row (~0.3 ms/frame),
        # which is worth paying on a board whose whole job this week is
        # proving the damage spans do not leave stale pixels.
        CXC_DAMAGE_AUDIT=1

        # The boot-time transition matrix that drives the audit.  Adds
        # ~30 scripted transitions x (repaint + full-frame audit render)
        # to boot — about 8 s — then the scene runs normally.  Comment
        # this out once the damage spans are trusted on this board; the
        # audit above can stay armed without it.
        # BOOT SELF-TEST — OFF.  It has done its job on this board: 30/30
        # transitions clean, and the owner accepted the glass on
        # 2026-08-13 ("Nope, none of those artifacts").  Turning it back
        # on costs ~8 s of boot and re-proves the damage spans after any
        # change to a widget's damage_rows.
        # CXC_DAMAGE_SELFTEST=1

        # PER-BAND TRANSFER TRACE — OFF.  It answered its question: the
        # per-transfer overhead is 20-76 us against 0.778 ms/row of wire
        # time, so a multi-band repaint is WIRE-bound and coalescing
        # transfers cannot hide the wavefront ([CXC-ER-DAMAGE]).
        # CXC_XFER_TRACE=1

        # THE NEGATIVE CONTROL — OFF, and it must stay off in anything
        # that reaches the glass.  It deliberately under-invalidates by
        # 6 rows so the audit has to report a non-zero number; it does
        # (19/30 FAIL).  Re-arm it for ONE flash whenever you need to
        # re-prove that the audit is an instrument rather than a
        # rubber stamp.
        # CXC_DAMAGE_INJECT_FAULT=1
        )

    # THE COLOUR BASELINE — DELIBERATELY OFF, AND ALREADY SPENT
    # ([CXC-BOARD-HW-SELF-CONFIRMING]; procedure in RUNBOOK.md §"Colour
    # baseline").  Uncommenting this adds five LABELLED bands of pure RGB565
    # — RED 0xF800 / GREEN 0x07E0 / BLUE 0x001F / WHITE 0xFFFF /
    # GREY 0x8410 — to the STANDARD ComposePlatform splash for six seconds,
    # then the boot continues automatically.
    #
    # RESULT FOR THIS PANEL, 2026-08-12, EYES ON THE GLASS: David reported "I
    # see the Compose logo on the screen along with the color bars which are
    # all correct."  So RGB565 byte order (vendor-default RAMCTRL, never
    # written) and MADCTL bit 3 (BGR) are BOTH confirmed correct, and this
    # instrument has done its job for this board.
    #
    # ⚠ THAT RESULT COULD NOT HAVE COME FROM ANY MACHINE SIGNAL.
    # GET /capture.bmp decodes the same band buffer with the same byte-order
    # assumption that WROTE it, so a byte-swapped panel yields a perfect
    # capture — exactly what happened on the C6 on 2026-08-11.  If the panel,
    # the init table or the canvas byte order ever changes, turn this back on
    # for ONE flash rather than trusting a capture.
    # target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_SPLASH_COLOR_BARS)
    return()
endif()

# [SPEC:CXC-EMBED-REACTIVE] R5 — the Qualia ESP32-S3 RGB666 framebuffer
# display (env:qualia_display, cache var QUALIA_DISPLAY=1).  The SAME
# reactive/scene/widget stack as the CYD build with the display tier
# swapped: canvas_qualia (PSRAM framebuffer + live transforms) +
# boards/qualia_rgb666 (NV3052C over PCA9554 + esp_lcd RGB) +
# qualia_main + the framebuffer capture server.  Excluded vs the CYD
# set: main.cpp, ili9341, canvas_esp32, capture_http, touch_xpt2046,
# classic_cyd (band/SPI-tier hardware).  ([TF7]) The CYD no longer has
# an SD card — the dev-only SD push-capture (capture.cpp/sd_card.cpp)
# was deleted; only the SD-free pull server (capture_http.cpp) remains.
if(QUALIA_DISPLAY)
    idf_component_register(
        SRCS
            "qualia_main.cpp"
            "canvas_qualia.cpp"
            "boards/qualia_rgb666.cpp"
            "capture_http_fb.cpp"
            "led.cpp"
            "gauge.cpp"
            "label.cpp"
            "rectangle.cpp"
            "circle.cpp"
            "button.cpp"
            "placeholder.cpp"
            "image.cpp"
            "sprite.cpp"
            # [CXC-PRIM-TIER-PARITY] general UI-primitive ports (readout +
            # input widgets shared with the Skia tier).
            "relay.cpp"
            "checkbox.cpp"
            "callout.cpp"
            "emit_button.cpp"
            "tab.cpp"
            # Scrollable status-list / checklist readout — first consumer of
            # the scroll_list substrate (nightwatch_checklist.py, generalized).
            "checklist.cpp"
            # Two-region master/detail nav viewer — LEFT nav is a
            # ScrollList consumer, RIGHT detail body is text_wrap-wrapped
            # (port of master_detail.py; the two-pane viewer primitive).
            "master_detail.cpp"
            # Scrolling multi-voice conversation ([SPEC:CXC-IC]
            # chat_thread.py) — flagship consumer of BOTH substrates:
            # text_wrap per bubble body + scroll_list variable-height scroll.
            "chat_thread.cpp"
            # Scrolling colour-coded bus-traffic log ([CXC-PRIM-TIER-PARITY]
            # bus_monitor.py) — a device debug panel; a ScrollList
            # uniform-row consumer (the checklist idiom).
            "bus_monitor.cpp"
            # Reusable vertical scroll + uniform-row list substrate
            # (master_detail / checklist / bus_monitor / doc_picker) —
            # pure math on the Skia scroll model, host-parity-tested
            # (env:native_scroll_test).
            "scroll_list.cpp"
            # Reusable modal-overlay substrate (scrim + centered panel +
            # hit-capture) — first consumer is dialog; enables any future
            # modal.  Pure math on the dialog.py model, host-parity-tested
            # (env:native_overlay_test).
            "overlay.cpp"
            # Modal dialog widget ([CXC-R-DIALOG] port of dialog.py) — the
            # overlay substrate's proof consumer.
            "dialog.cpp"
            # Reusable on-screen touch keyboard / keypad substrate — key
            # grid + hit-test + edit model (text_input.py / coordinate.py),
            # host-parity-tested (env:native_keyboard_test).  First consumer
            # is coordinate; enables text_input (#13).
            "keyboard.cpp"
            # Multi-digit coordinate entry + numeric keypad (port of
            # coordinate.py, #10) — the keyboard substrate's proof consumer.
            "coordinate.cpp"
            # Labeled single-line text entry + on-screen QWERTY (port of
            # text_input.py, #13) — the keyboard substrate's 2nd consumer.
            "text_input.cpp"
            "sweep.cpp"
            # [CXC-R-ENGINE-TEXT] shared soft-wrap + per-glyph measurement
            # helper (port of MultiLineEditMixin._wrap_lines) — composed by
            # text-bearing widgets, never forked per widget.
            "text_wrap.cpp"
            # DRADIS contacts overlay (docs/dradis/contacts-contract.md)
            # — framebuffer tier only (the band tier placeholders it).
            "dradis_contacts.cpp"
            "wifi.cpp"
            "touch_dispatch.cpp"
            "yaml_min.cpp"
            "config_fetch.cpp"
            "compose_scene.cpp"
            "dsl.cpp"
            # R1 reactive core compiled BY REFERENCE (one core, three
            # variants; never forked) + the ESP-IDF bus client.
            "reactive_core_canonical.cpp"
            # The ComposeBus TRANSPORT SEAM (DP-CIO-6): the app shell
            # depends on bus_transport.h, never on a substrate.  One
            # transport is SELECTED per boot (NVS, else the compile-time
            # default "mqtt") — bus_transport_select.cpp.
            "bus_mqtt.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            "bus_transport_select.cpp"
            # [SPEC:CXC-CONFIG-BUS] config delivered over ComposeBus itself,
            # so a board with NO IP can be configured.  config_chunk is the
            # substrate-independent format (sized for classic CAN's 8 bytes,
            # host-parity-tested by env:native_config_chunk); config_bus is
            # the leaf's request/reassemble/ack/announce contract;
            # config_source is the bus/http/cached strategy registry that
            # makes wifi_init() conditional.
            "config_chunk.cpp"
            "config_bus.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            # [CXC-ER-REMOTE-RESTART] shared remote-restart handler —
            # app-shell, not a board profile, so all envs inherit it.
            "restart.cpp"
            # [SPEC:CXC-BOARD-HW] workstream 0.2 — DEVICE SELF-ANNOUNCEMENT.  In the
            # SHARED app shell, never a board profile, for the same reason restart.cpp
            # is ([CXC-ER-C19]): the fleet is only as enumerable as the last board
            # somebody remembered to edit.  announce.cpp names NO substrate — it asks
            # the BusTransport seam for everything it reports (DP-CIO-6).
            # build_stamp.cpp is a separate 40-line TU on purpose: it includes the
            # per-build generated gen/build_stamp.h, so it recompiles on EVERY build,
            # and confining that to a tiny file is what keeps a never-stale build
            # fingerprint from costing a main.cpp rebuild each time (see announce.h).
            # ns_announce.cpp is the OTHER HALF of the announcement and is not
            # optional beside it: announce.cpp publishes VALUES on `_device/<id>/*`,
            # namespaces a CAN bridge CANNOT NAME (the wire id is a CRC over the
            # full name including the device id, so a `_device/*` prefix cannot be
            # expanded without already knowing every device id — the thing being
            # discovered).  ns_announce.cpp publishes the NAMES over the one
            # well-known root every bridge hardcodes, which is what makes those
            # values reachable over CAN at all ([PLAN G2]).  A board built with
            # one and not the other announces into a void.
            "announce.cpp"
            "freshness.cpp"
            "freshness_yaml.cpp"
            "freshness_bus.cpp"
            "ns_announce.cpp"
            "build_stamp.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            esp_http_server
            esp_lcd
            mqtt
            # IDF-bundled cJSON — the dradis/contacts array parser
            # (reuse, not a hand-rolled JSON scanner).
            json
    )

    # The two C++ source guards this tier depends on, applied where they
    # cannot be dropped.
    #
    # They were passed as platformio.ini build_flags — and for
    # framework=espidf those NEVER reach the IDF component compile; they
    # are silently discarded (verified against compile_commands.json,
    # 2026-08-11: only ESP_PLATFORM / IDF_VER / SOC_* survive).  The
    # Qualia on the bus today was flashed while that propagation still
    # worked, so it runs correctly; a REBUILD would have regressed it
    # silently.  This restores what the running binary already does.
    #
    #   QUALIA_DISPLAY          -> device_id() prefix "qualia-<mac3>"
    #                              (config_fetch.cpp).  Without it the
    #                              board boots as "cyd-<mac3>", 404s
    #                              displays/qualia-6c9d0c.compose.yaml and
    #                              silently falls back to the CYD
    #                              dashboard in displays/default.
    #   REACTIVE_TRANSFORM_ATTRS-> the framebuffer tier's bindable attrs:
    #                              rotation/scale/speed AND the whole
    #                              dradis_contacts contract
    #                              (source/alert/bodies/own_heading).
    #                              Without it the DRADIS scope binds
    #                              NONE of its contacts — a live-looking
    #                              but empty tactical display.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        QUALIA_DISPLAY
        REACTIVE_TRANSFORM_ATTRS)
    return()
endif()

# [CXC-BRINGUP] — the Hosyond ESP32-S3 2.8" IPS board (ES3C28P,
# env:hosyond_s3, cache var HOSYOND_S3=1).  The CYD's SPI/band tier
# moved onto an S3: the SAME app shell, canvas, widgets, scene builder
# and capture server as env:cyd, with exactly TWO substitutions at the
# board seam —
#   boards/classic_cyd.cpp + ili9341.cpp -> boards/hosyond_s3.cpp
#     (ILI9341V: different pins, power rails, gamma, inversion, colour
#      order, and no reset GPIO — a different panel, not a re-pinning)
#   touch_xpt2046.cpp                    -> touch_ft6336.cpp
#     (capacitive I2C, so no calibration — the first I2C touch in tree)
# The band height widens to a FULL FRAME via -DCXC_BAND_HEIGHT=320 in
# the env; nothing above the board seam changes.
if(HOSYOND_S3)
    idf_component_register(
        SRCS
            "main.cpp"
            "boards/hosyond_s3.cpp"
            "touch_ft6336.cpp"
            "canvas_esp32.cpp"
            "led.cpp"
            "gauge.cpp"
            "label.cpp"
            "rectangle.cpp"
            "circle.cpp"
            "button.cpp"
            "placeholder.cpp"
            "image.cpp"
            "sprite.cpp"
            "relay.cpp"
            "checkbox.cpp"
            "callout.cpp"
            "emit_button.cpp"
            "tab.cpp"
            "checklist.cpp"
            "master_detail.cpp"
            "chat_thread.cpp"
            "bus_monitor.cpp"
            "text_wrap.cpp"
            "scroll_list.cpp"
            "overlay.cpp"
            "dialog.cpp"
            "keyboard.cpp"
            "coordinate.cpp"
            "text_input.cpp"
            "capture_http.cpp"
            "wifi.cpp"
            "touch_dispatch.cpp"
            "yaml_min.cpp"
            "config_fetch.cpp"
            "compose_scene.cpp"
            "dsl.cpp"
            "dsl_selftest.cpp"
            "reactive_core_canonical.cpp"
            # The ComposeBus TRANSPORT SEAM (DP-CIO-6): the app shell
            # depends on bus_transport.h, never on a substrate.  One
            # transport is SELECTED per boot (NVS, else the compile-time
            # default "mqtt") — bus_transport_select.cpp.
            "bus_mqtt.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            "bus_transport_select.cpp"
            # [SPEC:CXC-CONFIG-BUS] config delivered over ComposeBus itself,
            # so a board with NO IP can be configured.  config_chunk is the
            # substrate-independent format (sized for classic CAN's 8 bytes,
            # host-parity-tested by env:native_config_chunk); config_bus is
            # the leaf's request/reassemble/ack/announce contract;
            # config_source is the bus/http/cached strategy registry that
            # makes wifi_init() conditional.
            "config_chunk.cpp"
            "config_bus.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "restart.cpp"
            # [SPEC:CXC-BOARD-HW] workstream 0.2 — DEVICE SELF-ANNOUNCEMENT.  In the
            # SHARED app shell, never a board profile, for the same reason restart.cpp
            # is ([CXC-ER-C19]): the fleet is only as enumerable as the last board
            # somebody remembered to edit.  announce.cpp names NO substrate — it asks
            # the BusTransport seam for everything it reports (DP-CIO-6).
            # build_stamp.cpp is a separate 40-line TU on purpose: it includes the
            # per-build generated gen/build_stamp.h, so it recompiles on EVERY build,
            # and confining that to a tiny file is what keeps a never-stale build
            # fingerprint from costing a main.cpp rebuild each time (see announce.h).
            # ns_announce.cpp is the OTHER HALF of the announcement and is not
            # optional beside it: announce.cpp publishes VALUES on `_device/<id>/*`,
            # namespaces a CAN bridge CANNOT NAME (the wire id is a CRC over the
            # full name including the device id, so a `_device/*` prefix cannot be
            # expanded without already knowing every device id — the thing being
            # discovered).  ns_announce.cpp publishes the NAMES over the one
            # well-known root every bridge hardcodes, which is what makes those
            # values reachable over CAN at all ([PLAN G2]).  A board built with
            # one and not the other announces into a void.
            "announce.cpp"
            "freshness.cpp"
            "freshness_yaml.cpp"
            "freshness_bus.cpp"
            "ns_announce.cpp"
            "build_stamp.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            esp_http_server
            mqtt
    )

    # THE RENDERING-TIER CALL, applied where it cannot be ignored.
    #
    # It must be a CMake compile definition, NOT a platformio.ini
    # build_flag: for framework=espidf, PlatformIO's build_flags do not
    # reach the IDF component compile at all — they are silently dropped,
    # and the firmware quietly renders in 16-line bands while the config
    # claims otherwise.  (Verified against compile_commands.json,
    # 2026-08-11.)  PUBLIC because kBandHeight lives in canvas_esp32.h
    # and every TU that includes it must agree.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_BAND_HEIGHT=320)

    # MEASUREMENT SWITCHES (both OFF here — never ship them ON).  They
    # restore, one at a time, the two behaviours the reactive tier was
    # sluggish for, so before/after numbers come from ONE binary under
    # the SAME traffic instead of from memory:
    #   CXC_FORCE_FULL_REPAINT  every arrival repaints the WHOLE frame
    #                           (the full-frame tier's dirty-granularity
    #                           regression, [CXC-BRINGUP] §3)
    #   CXC_LEGACY_POLL_BURST   the touch-poll burst always runs to
    #                           completion, so an arrival waits up to
    #                           100 ms before its repaint starts
    # They MUST be set here, not in platformio.ini: build_flags never
    # reach an ESP-IDF component compile (quirk pio-build-flags-dropped).
    # target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_FORCE_FULL_REPAINT CXC_LEGACY_POLL_BURST)

    # Board identity prefix -> device_id() == "hosyond-<last3 STA MAC hex>".
    # Without this the board answers to "cyd-<mac3>" and can never own a
    # per-device scene config (observed on the first live boot, which
    # 404'd on displays/cyd-d1afe8.compose.yaml).  Same reasoning as
    # above: a CMake definition, never a platformio.ini build_flag.
    # Keep it short — device_id()'s buffer is char[16].
    target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_DEVICE_PREFIX="hosyond")

    # UART transport wiring DEFAULTS ([SPEC:CXC-BOARD-HW]
    # [CXC-BOARD-HW-HOSYOND]): TXD0/RXD0 = GPIO43/44, the dedicated UART
    # JST, each through a 100R series resistor.  Only defaults — NVS
    # (uart.tx / uart.rx / uart.port / uart.baudrate) overrides them at
    # runtime so a rig is re-pointed without a rebuild, mirroring
    # transport_registry.py's `_uart(spec)`.
    #
    # HERE, not in a `#if defined(HOSYOND_S3)` in bus_uart.cpp: HOSYOND_S3
    # is a CMake CACHE VARIABLE that selects this branch and never reaches
    # the compiler, so such an #if is DEAD and takes the "unconfigured"
    # path while looking correct (checked in compile_commands.json).
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CXC_UART_TX_GPIO=43
        CXC_UART_RX_GPIO=44)
    return()
endif()

# [CXC-BRINGUP] — the Waveshare ESP32-C6-LCD-1.47 (env:waveshare_c6,
# cache var WAVESHARE_C6=1).  A BAND-tier board like the classic CYD
# (single-core RISC-V, 4 MB flash, NO PSRAM), so it keeps the default
# 16-scanline kBandHeight — no CXC_BAND_HEIGHT here.
#
# MUST sit ABOVE the "not esp32 -> DSL only" early-return below: this is
# an esp32c6 target that DOES drive glass, unlike env:esp32c3_dsl_test
# which is a conformance-only RISC-V build.
#
# Board seam substitutions vs the CYD set:
#   boards/classic_cyd.cpp + ili9341.cpp -> boards/waveshare_c6_lcd147.cpp
#   touch_xpt2046.cpp                    -> (none; this panel has no touch)
if(WAVESHARE_C6)
    idf_component_register(
        SRCS
            "main.cpp"
            "boards/waveshare_c6_lcd147.cpp"
            "canvas_esp32.cpp"
            "led.cpp"
            "gauge.cpp"
            "label.cpp"
            "rectangle.cpp"
            "circle.cpp"
            "button.cpp"
            "placeholder.cpp"
            "image.cpp"
            "sprite.cpp"
            "relay.cpp"
            "checkbox.cpp"
            "callout.cpp"
            "emit_button.cpp"
            "tab.cpp"
            "checklist.cpp"
            "master_detail.cpp"
            "chat_thread.cpp"
            "bus_monitor.cpp"
            "text_wrap.cpp"
            "scroll_list.cpp"
            "overlay.cpp"
            "dialog.cpp"
            "keyboard.cpp"
            "coordinate.cpp"
            "text_input.cpp"
            "capture_http.cpp"
            "wifi.cpp"
            "touch_dispatch.cpp"
            "yaml_min.cpp"
            "config_fetch.cpp"
            "compose_scene.cpp"
            "dsl.cpp"
            "dsl_selftest.cpp"
            "reactive_core_canonical.cpp"
            # The ComposeBus TRANSPORT SEAM (DP-CIO-6): the app shell
            # depends on bus_transport.h, never on a substrate.  One
            # transport is SELECTED per boot (NVS, else the compile-time
            # default "mqtt") — bus_transport_select.cpp.
            "bus_mqtt.cpp"
            "bus_frame.cpp"
            "bus_uart.cpp"
            "bus_can.cpp"
            "bus_transport_select.cpp"
            # [SPEC:CXC-CONFIG-BUS] config delivered over ComposeBus itself,
            # so a board with NO IP can be configured.  config_chunk is the
            # substrate-independent format (sized for classic CAN's 8 bytes,
            # host-parity-tested by env:native_config_chunk); config_bus is
            # the leaf's request/reassemble/ack/announce contract;
            # config_source is the bus/http/cached strategy registry that
            # makes wifi_init() conditional.
            "config_chunk.cpp"
            "config_bus.cpp"
            "config_source.cpp"
            # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
            # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
            # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
            # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
            "bus_binding_core.cpp"
            "bus_binding.cpp"
            "restart.cpp"
            # [SPEC:CXC-BOARD-HW] workstream 0.2 — DEVICE SELF-ANNOUNCEMENT.  In the
            # SHARED app shell, never a board profile, for the same reason restart.cpp
            # is ([CXC-ER-C19]): the fleet is only as enumerable as the last board
            # somebody remembered to edit.  announce.cpp names NO substrate — it asks
            # the BusTransport seam for everything it reports (DP-CIO-6).
            # build_stamp.cpp is a separate 40-line TU on purpose: it includes the
            # per-build generated gen/build_stamp.h, so it recompiles on EVERY build,
            # and confining that to a tiny file is what keeps a never-stale build
            # fingerprint from costing a main.cpp rebuild each time (see announce.h).
            # ns_announce.cpp is the OTHER HALF of the announcement and is not
            # optional beside it: announce.cpp publishes VALUES on `_device/<id>/*`,
            # namespaces a CAN bridge CANNOT NAME (the wire id is a CRC over the
            # full name including the device id, so a `_device/*` prefix cannot be
            # expanded without already knowing every device id — the thing being
            # discovered).  ns_announce.cpp publishes the NAMES over the one
            # well-known root every bridge hardcodes, which is what makes those
            # values reachable over CAN at all ([PLAN G2]).  A board built with
            # one and not the other announces into a void.
            "announce.cpp"
            "freshness.cpp"
            "freshness_yaml.cpp"
            "freshness_bus.cpp"
            "ns_announce.cpp"
            "build_stamp.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
            "../../firmware/reactive_core/src"
        REQUIRES
            driver
            esp_wifi
            esp_netif
            esp_event
            esp_hw_support
            esp_timer
            nvs_flash
            esp_http_client
            esp_http_server
            mqtt
    )

    # Board identity prefix -> device_id() == "wsc6-<last3 STA MAC hex>".
    # A CMake definition, never a platformio.ini build_flag — those are
    # silently dropped for framework=espidf (see the Hosyond branch).
    # Keep it short: device_id()'s buffer is char[16].
    target_compile_definitions(${COMPONENT_LIB} PUBLIC CXC_DEVICE_PREFIX="wsc6")

    # UART transport wiring DEFAULTS ([SPEC:CXC-BOARD-HW]
    # [CXC-BOARD-HW-WSC6]): the dedicated TXD/RXD pads = GPIO16/17.  Same
    # reasoning as the Hosyond branch — a compile DEFINITION, because the
    # WAVESHARE_C6 cache var never reaches the compiler.
    target_compile_definitions(${COMPONENT_LIB} PUBLIC
        CXC_UART_TX_GPIO=16
        CXC_UART_RX_GPIO=17)
    return()
endif()

# [SPEC:CXC-EMBED-REACTIVE] R1 — on a non-Xtensa target (the ESP32-C3
# RISC-V corpus re-verify, env:esp32c3_dsl_test) only the portable DSL +
# its conformance harness build; the CYD display/board sources are
# Xtensa-board-specific (ILI9341 pins > GPIO21 etc.) and stay out.
if(NOT IDF_TARGET STREQUAL "esp32")
    idf_component_register(
        SRCS
            "dsl.cpp"
            "dsl_selftest.cpp"
        INCLUDE_DIRS
            "."
            "../include"
            "../test"
        REQUIRES
            esp_timer
    )
    return()
endif()

idf_component_register(
    SRCS
        "main.cpp"
        "ili9341.cpp"
        "canvas_esp32.cpp"
        "led.cpp"
        "gauge.cpp"
        "label.cpp"
        "rectangle.cpp"
        "circle.cpp"
        "button.cpp"
        "placeholder.cpp"
        "image.cpp"
        "sprite.cpp"
        # [CXC-PRIM-TIER-PARITY] general UI-primitive ports (readout +
        # input widgets shared with the Skia tier).
        "relay.cpp"
        "checkbox.cpp"
        "callout.cpp"
        "emit_button.cpp"
        "tab.cpp"
        # Scrollable status-list / checklist readout — first consumer of
        # the scroll_list substrate (nightwatch_checklist.py, generalized).
        "checklist.cpp"
        # Two-region master/detail nav viewer — LEFT nav is a ScrollList
        # consumer, RIGHT detail body is text_wrap-wrapped (port of
        # master_detail.py; the two-pane viewer primitive).
        "master_detail.cpp"
        # Scrolling multi-voice conversation ([SPEC:CXC-IC] chat_thread.py) —
        # the flagship consumer of BOTH substrates: text_wrap per bubble body,
        # scroll_list (variable-height mode) for the thread scroll.
        "chat_thread.cpp"
        # Scrolling colour-coded bus-traffic log ([CXC-PRIM-TIER-PARITY]
        # bus_monitor.py) — a device debug panel; a ScrollList uniform-row
        # consumer (the checklist idiom).
        "bus_monitor.cpp"
        # [CXC-R-ENGINE-TEXT] shared soft-wrap + per-glyph measurement helper
        # (port of MultiLineEditMixin._wrap_lines) — composed by text-bearing
        # widgets, never forked per widget.
        "text_wrap.cpp"
        # Reusable vertical scroll + uniform-row list substrate
        # (master_detail / checklist / bus_monitor / doc_picker) — pure
        # math on the Skia scroll model, host-parity-tested
        # (env:native_scroll_test).
        "scroll_list.cpp"
        # Reusable modal-overlay substrate (scrim + centered panel +
        # hit-capture) — first consumer is dialog; enables any future
        # modal.  Pure math on the dialog.py model, host-parity-tested
        # (env:native_overlay_test).
        "overlay.cpp"
        # Modal dialog widget ([CXC-R-DIALOG] port of dialog.py) — the
        # overlay substrate's proof consumer.
        "dialog.cpp"
        # Reusable on-screen touch keyboard / keypad substrate — key grid +
        # hit-test + edit model (text_input.py / coordinate.py), host-
        # parity-tested (env:native_keyboard_test).  First consumer is
        # coordinate; enables text_input (#13).
        "keyboard.cpp"
        # Multi-digit coordinate entry + numeric keypad (port of
        # coordinate.py, #10) — the keyboard substrate's proof consumer.
        "coordinate.cpp"
        # Labeled single-line text entry + on-screen QWERTY (port of
        # text_input.py, #13) — the keyboard substrate's 2nd consumer.
        "text_input.cpp"
        "capture_http.cpp"
        "wifi.cpp"
        "touch_xpt2046.cpp"
        "touch_dispatch.cpp"
        "yaml_min.cpp"
        "config_fetch.cpp"
        "compose_scene.cpp"
        "boards/classic_cyd.cpp"
        "dsl.cpp"
        "dsl_selftest.cpp"
        # [SPEC:CXC-EMBED-REACTIVE] R3 — the R1 reactive core compiled
        # BY REFERENCE (one core, three variants; never forked) + the
        # ESP-IDF bus client.
        "reactive_core_canonical.cpp"
        # The ComposeBus TRANSPORT SEAM (DP-CIO-6) — see the notes in the
        # QUALIA_DISPLAY branch above.
        "bus_mqtt.cpp"
        "bus_frame.cpp"
        "bus_uart.cpp"
        "bus_can.cpp"
        "bus_transport_select.cpp"
        # [SPEC:CXC-CONFIG-BUS] — see the note in the QUALIA_DISPLAY branch.
        "config_chunk.cpp"
        "config_bus.cpp"
        "config_source.cpp"
        # [SPEC:CXC-REPOINT] the repoint binding — bus_binding_core.cpp is PURE
        # (host-tested in env:native_bus_binding); bus_binding.cpp is its ESP-IDF
        # sibling.  Linked wherever config_source is, since bus_mqtt/config_fetch
        # now resolve the active tier through it ([CXC-REPOINT-CLASS-ESP32]).
        "bus_binding_core.cpp"
        "bus_binding.cpp"
        # [CXC-ER-REMOTE-RESTART] shared remote-restart handler —
        # app-shell, not a board profile, so all envs inherit it.
        "restart.cpp"
        # [SPEC:CXC-BOARD-HW] workstream 0.2 — DEVICE SELF-ANNOUNCEMENT.  In the
        # SHARED app shell, never a board profile, for the same reason restart.cpp
        # is ([CXC-ER-C19]): the fleet is only as enumerable as the last board
        # somebody remembered to edit.  announce.cpp names NO substrate — it asks
        # the BusTransport seam for everything it reports (DP-CIO-6).
        # build_stamp.cpp is a separate 40-line TU on purpose: it includes the
        # per-build generated gen/build_stamp.h, so it recompiles on EVERY build,
        # and confining that to a tiny file is what keeps a never-stale build
        # fingerprint from costing a main.cpp rebuild each time (see announce.h).
        # ns_announce.cpp is the OTHER HALF of the announcement and is not
        # optional beside it: announce.cpp publishes VALUES on `_device/<id>/*`,
        # namespaces a CAN bridge CANNOT NAME (the wire id is a CRC over the
        # full name including the device id, so a `_device/*` prefix cannot be
        # expanded without already knowing every device id — the thing being
        # discovered).  ns_announce.cpp publishes the NAMES over the one
        # well-known root every bridge hardcodes, which is what makes those
        # values reachable over CAN at all ([PLAN G2]).  A board built with
        # one and not the other announces into a void.
        "announce.cpp"
        "freshness.cpp"
        "freshness_yaml.cpp"
        "freshness_bus.cpp"
        "ns_announce.cpp"
        "build_stamp.cpp"
    INCLUDE_DIRS
        "."
        "../include"
        "../test"
        "../../firmware/reactive_core/src"
    REQUIRES
        driver
        esp_wifi
        esp_netif
        esp_event
        esp_hw_support
        esp_timer
        nvs_flash
        esp_http_client
        esp_http_server
        mqtt
)
