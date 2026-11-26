/*
 * bus_binding_board.h — per-board constants for the repoint escape hatch and
 * the captive-portal rescue capability.  [SPEC:CXC-REPOINT]
 * [DEC-BUILDHOST-ESCAPE-HATCH-WHERE-GPIO-DECLARED] [DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]
 *
 * These are COMPILE-TIME per-board facts, in the ESPHome mould: exactly one
 * board is linked into a firmware and it declares whether it has a reachable
 * BOOT/GPIO0 escape-hatch button and whether it has the flash/RAM budget to
 * vend a SoftAP captive portal.
 *
 * ⛔ THEY MUST BE SET THROUGH CMake `target_compile_definitions`, NEVER a
 * platformio.ini `build_flag`: for framework=espidf a build_flag never reaches
 * the component compile and is SILENTLY DROPPED — three prior defects in this
 * tree (a board in the wrong render tier, a board answering to the wrong
 * device-id prefix, a dead-but-correct-looking source guard).  Verify any
 * override landed in `.pio/build/<env>/compile_commands.json`.
 *
 * DEFAULTS ARE THE HONEST SENTINELS.  Absent an override, a board has NO
 * escape hatch (pin -1) and NO captive-portal capability (0).  Announcing
 * "no hatch here" (flags bit 0) is correct until someone has verified on the
 * bench that a board's GPIO0 is actually free at reset ([DD-REPOINT-BUTTON-
 * SCOPE] invalidating_assumption: a GPIO0 carrying a peripheral, or an
 * enclosure covering the button, silently has no hatch).  A pin asserted here
 * without that bench check would make the flag LIE — a worse failure than an
 * honestly-reported gap.  Set a real pin ONLY after the bench verifies it.
 */

#ifndef BUS_BINDING_BOARD_H
#define BUS_BINDING_BOARD_H

/* GPIO used as the two-stage escape hatch, or -1 when the board has none.
 * When -1, the hatch is skipped entirely (no boot delay) and its absence is
 * announced via ANNOUNCE flags bit 0. */
#ifndef CXC_ESCAPE_HATCH_PIN
#define CXC_ESCAPE_HATCH_PIN (-1)
#endif

/* Active level of the escape-hatch button.  The ESP32 BOOT/GPIO0 strap idles
 * HIGH and reads LOW while pressed, so the default is active-low (0). */
#ifndef CXC_ESCAPE_HATCH_ACTIVE_LEVEL
#define CXC_ESCAPE_HATCH_ACTIVE_LEVEL (0)
#endif

/* Two-stage hold durations (ms).  Tunable board constants, NOT load-bearing to
 * the two-tier design ([DEC-REPOINT-ESCAPE-HATCH-IS-TWO-STAGE]).  A hold below
 * SHORT is a tap and retains its download-mode meaning. */
#ifndef CXC_ESCAPE_HATCH_SHORT_MS
#define CXC_ESCAPE_HATCH_SHORT_MS (3000)
#endif
#ifndef CXC_ESCAPE_HATCH_LONG_MS
#define CXC_ESCAPE_HATCH_LONG_MS (10000)
#endif

/* 1 when this board has the budget to vend a SoftAP captive-portal rescue
 * ([CXC-REPOINT-CAPTIVE-PORTAL]); 0 otherwise.  The portal itself is a
 * follow-on and is NOT built here — this flag only drives the ANNOUNCE
 * capability bit (flags bit 1) so a fleet report says which boards can be
 * rescued without a reflash and which honestly cannot. */
#ifndef CXC_HAS_CAPTIVE_PORTAL
#define CXC_HAS_CAPTIVE_PORTAL (0)
#endif

#endif /* BUS_BINDING_BOARD_H */
