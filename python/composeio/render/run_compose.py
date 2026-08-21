"""
Launcher: run a .compose.yaml on the ComposeUX render engine.

    python -m composeio.render.run_compose <config.compose.yaml> [--fullscreen]
    python -m composeio.render.run_compose <config> --headless out.png
    python -m composeio.render.run_compose <config> --transports   # connect MQTT

(Kept outside the engine package so `python -m` doesn't re-execute a
module already imported by the package __init__.)
"""

from composeio.render.engine.runtime import main

if __name__ == "__main__":
    main()
