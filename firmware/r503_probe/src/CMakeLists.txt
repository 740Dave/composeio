idf_component_register(
    SRCS "main.c" "r503_codec.c" "r503_link.c"
    INCLUDE_DIRS "."
    REQUIRES driver esp_timer esp_driver_uart esp_driver_usb_serial_jtag esp_hw_support
)

# ⛔ COMPILE-TIME SWITCHES GO HERE, NOT IN platformio.ini.
#    `build_flags` in platformio.ini are SILENTLY DROPPED for ESP-IDF component
#    compiles. This has produced two real defects on this fleet — a board rendering
#    in the wrong tier while every test passed, and a board answering to the wrong
#    device-id prefix. Verify with:
#        grep -o 'R503_TX_GPIO=[0-9]*' .pio/build/supermini_s3/compile_commands.json
#
# The GPIO defaults live in main.c and can also be overridden here. They are ALSO
# retargetable at runtime via the `pins` verb, which is the cheap escape from the
# fact that this firmware does not hold the SuperMini's silkscreen.
# target_compile_definitions(${COMPONENT_LIB} PUBLIC R503_TX_GPIO=4 R503_RX_GPIO=5)
