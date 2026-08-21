"""
Maker-project tooling — the derived consumers of a maker `.compose.yaml`'s
`hookup:` block (design lock `docs/maker/maker-project-flow.md`).

One artifact (the widget's `.compose.yaml`, carrying a `hookup:` block), N
consumers. The hookup netlist (S2, `render/engine/hookup.py` + `render/engine/parts/`)
is the single source of truth for a maker project's PHYSICAL half; this package
holds the code that reads `comp.hookup` (an S2
:class:`composeio.render.engine.hookup.Hookup`) and turns it into something a maker uses:

  * S3 (`hookup_svg`) — renders the hookup as an SVG wiring guide, two substrate
    renderers (breadboard | perfboard) off the ONE netlist spec, wired into the
    console doc-viewer (SVG lowered through the Canvas contract).
  * S4 (`mcu_config`) — the MCU config-emitter: the pin->slot map falls out as the
    ESP32 baseline stack's read/publish (sensor) config — step (e).

A future KiCad interpreter would join here as a fourth reader of the same netlist.
"""

from __future__ import annotations

from .hookup_svg import (
    # ⛔ render_hookup is the FULL result — the SVG *and* the crossing verdict.
    # A delivery path must use it and gate on `report.ok`; `hookup_to_svg`
    # returns the picture alone and is how 7 un-bridged crossings shipped.
    render_hookup,
    CrossingReport,
    hookup_to_svg,
    hookup_svg_to_file,
    hookup_svg_to_png,
    svgdom_rasterize,
    canvas_rasterize,
    hopless_rasterize,
    layout_hookup,
    wire_segments,
)
from .mcu_config import (
    McuConfig,
    SensorSpec,
    Normalize,
    ActuatorSpec,
    AudioCue,
    emit_mcu_config,
    ADC_FULL_SCALE,
    ENGINE_DOMAINS,
    DEFAULT_SAMPLE_HZ,
    DEFAULT_DEBOUNCE_MS,
)
from .throttle_thrust import (
    NS_THROTTLE_LEFT,
    NS_THROTTLE_RIGHT,
    NS_OWN_HEADING,
    THRUST_FULL_SCALE,
    DEFAULT_MAX_RATE_DPS,
    DEFAULT_DEADBAND,
    DEFAULT_MAX_SPEED,
    SIGN_CONVENTION,
    normalized_thrust,
    differential_thrust,
    mean_thrust,
    heading_rate,
    ThrustHeadingModel,
    ThrottleThrustBridge,
    WORLD_APP_HOOK,
)
from .audio_cover import (
    NS_CONTACTS,
    NS_JUMP,
    JUMP_FIRE_VALUE,
    IDENTIFIED_ALLEGIANCE,
    CUE_CONDITION_ONE,
    CUE_JUMP,
    CUE_TRACKS,
    track_filename,
    cue_filename,
    DYSV5F_BAUD,
    DYSV5F_START,
    DYSV5F_CMD_SELECT_PLAY,
    DYSV5F_CMD_PLAY,
    dysv5f_checksum,
    dysv5f_play_track,
    dysv5f_frame_hex,
    contact_is_identified,
    ContactIdentificationDetector,
    JumpDetector,
    AudioCover,
    COVER_HOOK,
)

__all__ = [
    "render_hookup",
    "CrossingReport",
    "hookup_to_svg",
    "hookup_svg_to_file",
    "hookup_svg_to_png",
    "svgdom_rasterize",
    "canvas_rasterize",
    "hopless_rasterize",
    "layout_hookup",
    "wire_segments",
    "McuConfig",
    "SensorSpec",
    "Normalize",
    "ActuatorSpec",
    "AudioCue",
    "emit_mcu_config",
    "ADC_FULL_SCALE",
    "ENGINE_DOMAINS",
    "DEFAULT_SAMPLE_HZ",
    "DEFAULT_DEBOUNCE_MS",
    # S5 — dual-throttle -> Galactica-heading contract
    "NS_THROTTLE_LEFT",
    "NS_THROTTLE_RIGHT",
    "NS_OWN_HEADING",
    "THRUST_FULL_SCALE",
    "DEFAULT_MAX_RATE_DPS",
    "DEFAULT_DEADBAND",
    "DEFAULT_MAX_SPEED",
    "SIGN_CONVENTION",
    "normalized_thrust",
    "differential_thrust",
    "mean_thrust",
    "heading_rate",
    "ThrustHeadingModel",
    "ThrottleThrustBridge",
    "WORLD_APP_HOOK",
    # S6 — the DY-SV5F audio cover (a pure bus SUBSCRIBER)
    "NS_CONTACTS",
    "NS_JUMP",
    "JUMP_FIRE_VALUE",
    "IDENTIFIED_ALLEGIANCE",
    "CUE_CONDITION_ONE",
    "CUE_JUMP",
    "CUE_TRACKS",
    "track_filename",
    "cue_filename",
    "DYSV5F_BAUD",
    "DYSV5F_START",
    "DYSV5F_CMD_SELECT_PLAY",
    "DYSV5F_CMD_PLAY",
    "dysv5f_checksum",
    "dysv5f_play_track",
    "dysv5f_frame_hex",
    "contact_is_identified",
    "ContactIdentificationDetector",
    "JumpDetector",
    "AudioCover",
    "COVER_HOOK",
]
