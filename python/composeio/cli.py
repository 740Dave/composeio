"""ComposeIO CLI — push, status, inspect commands.

DP-CIO-7: The builder's primary tool. Validates YAML, compiles to binary,
pushes to bus storage or directly to a module.

Usage:
    composeio push <config.yaml> [--target <module_id>] [--storage <url>]
    composeio status [--storage <url>]
    composeio inspect <namespace>
    composeio validate <config.yaml>
    composeio compile <config.yaml> [-o <output.bin>]
"""

from __future__ import annotations
import argparse
import json
import socket
import sys
import time
from pathlib import Path

from composeio.config import load_config, validate_config, compile_binary, ConfigError
from composeio.storage import BusStorageClient


def cmd_push(args):
    """Push a config to bus storage (or directly to a device)."""
    config = load_config(args.config)
    errors = validate_config(config)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)

    binary = compile_binary(config)
    module_id = args.target or config.get("module", "unknown")

    print(f"Compiled {args.config} -> {len(binary)} bytes for module '{module_id}'")

    if args.storage:
        client = BusStorageClient(args.storage)
        client.push_config(module_id, binary, metadata={
            "source": str(args.config),
            "role": config.get("role"),
        })
        print(f"Pushed to bus storage at {args.storage}")
    else:
        # Default: write binary to file
        out_path = Path(args.config).with_suffix(".bin")
        out_path.write_bytes(binary)
        print(f"Wrote binary to {out_path}")


def cmd_status(args):
    """Show system status from bus storage."""
    client = BusStorageClient(args.storage)
    status = client.status()
    print(json.dumps(status, indent=2))


def cmd_inspect(args):
    """Inspect a namespace."""
    client = BusStorageClient(args.storage)
    ns = client.get_namespace(args.namespace)
    if ns:
        print(json.dumps(ns, indent=2))
    else:
        print(f"Namespace '{args.namespace}' not found", file=sys.stderr)
        sys.exit(1)


def cmd_validate(args):
    """Validate a config file."""
    config = load_config(args.config)
    errors = validate_config(config)
    if errors:
        print("Validation errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    print(f"{args.config}: valid ({config.get('role', '?')} module '{config.get('module', '?')}')")


def cmd_compile(args):
    """Compile a config to binary."""
    config = load_config(args.config)
    binary = compile_binary(config)
    if args.output:
        Path(args.output).write_bytes(binary)
        print(f"Compiled {len(binary)} bytes -> {args.output}")
    else:
        out_path = Path(args.config).with_suffix(".bin")
        out_path.write_bytes(binary)
        print(f"Compiled {len(binary)} bytes -> {out_path}")


def _parse_transport_arg(arg: str | None) -> tuple[str | None, dict]:
    """``"type=uart,port=/dev/ttyUSB0,baudrate=115200"`` -> ``("uart", {...})``.

    `k=v` pairs rather than a flag per substrate parameter, because the set of
    parameters is the REGISTRY's business and grows with it: `channel`/`bitrate`
    for CAN, `port`/`baudrate` for UART, `capture` for replay.  A flag per key
    would have to be extended here every time a transport gains one, which is
    the same "this module tracks the substrates" coupling the conversion removed.

    Values stay STRINGS; `transport_registry`'s factories already coerce
    (`int(spec.get("baudrate", 115200))`), and guessing types here would be a
    second, divergent coercion.  `None` in, `(None, {})` out — the caller then
    falls through to the environment default.
    """
    if not arg:
        return None, {}
    ttype: str | None = None
    options: dict = {}
    for part in arg.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            # A bare word is the type — `--transport can` is what people will
            # type first, and rejecting it to insist on `type=can` would be
            # pedantry with an error message.
            ttype = part.lower()
            continue
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k == "type":
            ttype = v.strip().lower()
        else:
            options[k] = v.strip()
    return ttype, options


def cmd_fleet(args):
    """Poll the bus and print every device that answers.

    [SPEC:CXC-BOARD-HW] workstream 0.5.  It READS THE BUS — it does not read
    `device.yaml`, a board descriptor or any other document.  That is the
    point: on 2026-08-12 three of five `device.yaml` records carried
    `mac: "TBD"` and one declared an id the device was not publishing under, so
    a reflash campaign driven off those records would have targeted the wrong
    hardware.  This asks the devices.

    Exit status is 0 when at least one device answered, 2 when none did — so
    `composeio fleet >/dev/null && echo up` is usable in a script, and a
    campaign can gate on it.
    """
    from composeio import fleet as fleet_mod

    # A CLI FLAG MAY NAME A SUBSTRATE; a library module may not
    # ([CXC-SOB-THESIS]).  This is the one place in the fleet path where the
    # wire is spelled out, and even here it is only parsed — the choice arrives
    # from the operator, or from `COMPOSEIO_TRANSPORT`, or from the default.
    ttype, options = _parse_transport_arg(args.transport)
    spec = fleet_mod.transport_spec(ttype, args.broker, args.port, options)
    target = fleet_mod.describe_spec(spec)

    reports, warnings = fleet_mod.poll_fleet(
        wait=args.wait, poll=not args.no_poll, spec=spec)

    if args.json:
        print(fleet_mod.report_json(reports, warnings, target=target))
    else:
        print(fleet_mod.format_report(reports, warnings, wait=args.wait,
                                      target=target))
    # A tool that exits 0 having found nothing is indistinguishable from one
    # that found a healthy fleet — the exact ambiguity announcement exists to
    # remove, so it must not be reintroduced in the exit code.
    sys.exit(0 if reports else 2)


def device_control_default_confirm() -> float:
    """The confirmation window, named ONCE in `device_control`."""
    from composeio.device_control import DEFAULT_CONFIRM_S
    return DEFAULT_CONFIRM_S


def cmd_device_restart(args):
    """Publish a restart to one or more devices — AND MEASURE WHETHER IT LANDED.

    ⛔ THIS SUBCOMMAND EXISTS BECAUSE ITS ABSENCE WAS THE DEFECT.  On 2026-08-15
    six devices were sent a restart carrying ``"type": "command"``, hand-typed
    into `mosquitto_pub` because no tool framed one.  `command` is ComposeBus
    PROSE, not one of the ten wire values [BP-TYPE-CODES] allocates, so every
    receiver refused it and the restart was a NO-OP on every substrate.  Nothing
    told the publisher.  `device_control.restart_message` frames it through the
    one validator, and the confirmation below reads the device's OWN uptime
    counter rather than reporting the publish as a success.

    Exit status: 0 iff EVERY named device produced evidence of a reboot; 2
    otherwise.  A tool that exits 0 having proved nothing is the failure mode
    this whole change is about.
    """
    from composeio import device_control, fleet as fleet_mod
    from composeio.transport_registry import build

    ttype, options = _parse_transport_arg(args.transport)
    spec = fleet_mod.transport_spec(ttype, args.broker, args.port, options)
    target = fleet_mod.describe_spec(spec)

    transport = build(spec)
    if transport is None:
        print(f"could not build the {target} transport — NOTHING WAS "
              f"PUBLISHED. This is a tool failure, not a silent fleet.",
              file=sys.stderr)
        sys.exit(2)

    devices: list = list(args.device) if args.device else [None]
    src = f"composeio-cli-{socket.gethostname().split('.')[0]}"

    transport.start()
    deadline = time.monotonic() + 5.0
    while not transport.is_connected and time.monotonic() < deadline:
        time.sleep(0.05)
    if not transport.is_connected:
        transport.stop()
        print(f"never connected to {target} — NOTHING WAS PUBLISHED.",
              file=sys.stderr)
        sys.exit(2)

    print(f"restart via {target}; baseline {args.baseline:.0f}s, "
          f"confirm {args.confirm:.0f}s")
    try:
        evidence = device_control.restart_devices(
            devices, source=src, transport=transport,
            confirm_s=args.confirm, baseline_s=args.baseline)
    finally:
        transport.stop()

    print(device_control.format_evidence(evidence))
    sys.exit(0 if all(e.rebooted for e in evidence.values()) else 2)


def main():
    parser = argparse.ArgumentParser(
        prog="composeio",
        description="ComposeIO CLI — Software-Defined I/O configuration tool",
    )
    parser.add_argument("--storage", default="http://localhost:8420", help="Bus storage URL")
    subparsers = parser.add_subparsers(dest="command")

    # push
    p_push = subparsers.add_parser("push", help="Push config to bus storage")
    p_push.add_argument("config", help="YAML config file")
    p_push.add_argument("--target", help="Override module ID")
    p_push.set_defaults(func=cmd_push)

    # status
    p_status = subparsers.add_parser("status", help="Show system status")
    p_status.set_defaults(func=cmd_status)

    # inspect
    p_inspect = subparsers.add_parser("inspect", help="Inspect a namespace")
    p_inspect.add_argument("namespace", help="Namespace name")
    p_inspect.set_defaults(func=cmd_inspect)

    # validate
    p_validate = subparsers.add_parser("validate", help="Validate a config file")
    p_validate.add_argument("config", help="YAML config file")
    p_validate.set_defaults(func=cmd_validate)

    # fleet — poll the bus for what is actually on it
    from composeio.fleet import (DEFAULT_BROKER, DEFAULT_PORT,
                                 DEFAULT_TRANSPORT_TYPE, DEFAULT_WAIT_S)

    p_fleet = subparsers.add_parser(
        "fleet",
        help="Poll the ComposeBus and list every device that announces")
    p_fleet.add_argument(
        "--transport", default=None, metavar="type=T[,k=v...]",
        help="the SUBSTRATE to poll, as `type=<t>` plus that transport's own "
             "parameters — e.g. `type=uart,port=/dev/ttyUSB0,baudrate=115200` "
             "or `type=can,channel=can0`.  Defaults to $COMPOSEIO_TRANSPORT "
             f"(currently {DEFAULT_TRANSPORT_TYPE!r}).  An unknown type is a "
             "startup error, not a silent empty report")
    p_fleet.add_argument("--broker", default=DEFAULT_BROKER,
                         help=f"MQTT broker (default {DEFAULT_BROKER}); "
                              f"meaningful only for --transport type=mqtt")
    p_fleet.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_fleet.add_argument("--wait", type=float, default=DEFAULT_WAIT_S,
                         help="seconds to listen after the poll "
                              f"(default {DEFAULT_WAIT_S:.0f}; must exceed the "
                              "5 s heartbeat to observe a cadence)")
    p_fleet.add_argument("--no-poll", action="store_true",
                         help="listen only; do not publish "
                              "_device/broadcast/announce.  Use this to check "
                              "the PERIODIC cadence, which is the actual "
                              "liveness signal — a poll answer only proves the "
                              "device was alive at that instant")
    p_fleet.add_argument("--json", action="store_true")
    p_fleet.set_defaults(func=cmd_fleet)

    # device restart — the publisher whose ABSENCE was the 2026-08-15 defect
    p_device = subparsers.add_parser(
        "device", help="Control a device over the bus (restart)")
    device_sub = p_device.add_subparsers(dest="device_command")
    p_restart = device_sub.add_parser(
        "restart",
        help="Publish _device/<id>/restart = 1 and CONFIRM the reboot")
    p_restart.add_argument(
        "device", nargs="*",
        help="device ids as `composeio fleet` reports them (e.g. "
             "cio-ctrl-db1c).  ⚠ the DEVICE ID, not a namespace fragment — a "
             "slash is refused.  With no ids, broadcasts to _device/restart")
    p_restart.add_argument(
        "--transport", default=None, metavar="type=T[,k=v...]",
        help="the SUBSTRATE, as `type=<t>` plus that transport's parameters. "
             "A restart is an ordinary bus message (DP-CIO-6)")
    p_restart.add_argument("--broker", default=DEFAULT_BROKER)
    p_restart.add_argument("--port", type=int, default=DEFAULT_PORT)
    p_restart.add_argument(
        "--baseline", type=float, default=6.0,
        help="seconds to sample uptime BEFORE publishing.  Without a baseline "
             "an uptime of 4s proves nothing")
    p_restart.add_argument(
        "--confirm", type=float, default=device_control_default_confirm(),
        help="seconds to watch for the pre-restart ack and an uptime RESET — "
             "the device's own evidence that it rebooted")
    p_restart.set_defaults(func=cmd_device_restart)

    # compile
    p_compile = subparsers.add_parser("compile", help="Compile config to binary")
    p_compile.add_argument("config", help="YAML config file")
    p_compile.add_argument("-o", "--output", help="Output binary file")
    p_compile.set_defaults(func=cmd_compile)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
