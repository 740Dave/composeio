#!/usr/bin/env bash
# deploy.sh — reproducible deploy of services/can_mqtt_bridge to a Linux host
# with a SocketCAN interface (built for pi.example.local + Waveshare 2-CH CAN HAT).
#
# WHY THIS FILE EXISTS
#   A bridge that only exists on one Pi is a bridge nobody can rebuild. Every
#   artifact the service needs — the code closure, the venv, the systemd unit
#   and the environment file — is produced from this repo by this script, and
#   nothing is hand-edited on the host. Re-running is safe and idempotent.
#
# WHAT IT INSTALLS
#   /opt/composeio-can-bridge/python/composeio/     the package (rsynced)
#   /opt/composeio-can-bridge/services/can_mqtt_bridge/
#   /opt/composeio-can-bridge/venv/                 paho-mqtt + pyyaml
#   /opt/composeio-can-bridge/loopback_test.py      the independent verifier
#   /etc/default/composeio-can-bridge               tunables (allowlist!)
#   /etc/systemd/system/composeio-can-bridge.service
#   a `composeio` system user (no shell, no home) that owns the process
#
#   The on-host layout deliberately mirrors the repo's `<root>/python` +
#   `<root>/services/...` shape, because bridge.py locates the package with a
#   `parent.parent.parent / "python"` sys.path insert. Flatten it and the
#   import breaks in a way that only shows up at runtime.
#
# WHAT IT DOES NOT TOUCH — and must not
#   /boot/firmware/config.txt, the mcp251xfd overlays, can0.service,
#   can1.service. Those were corrected by hand (can0 interrupt 23, can1
#   interrupt 25, 16 MHz oscillator) with a backup at config.txt.bak-preCAN.
#   This script reads the interface state and refuses to proceed if it is
#   wrong; it never repairs it.
#
# USAGE
#   ./deploy.sh                    # verify host -> sync -> venv -> unit -> start
#   ./deploy.sh --dry-run          # show what rsync would change, change nothing
#   ./deploy.sh --no-restart       # install everything, leave the service alone
#   ./deploy.sh --status           # unit state + interface state + recent log
#   ./deploy.sh --logs             # follow the journal
#   ./deploy.sh --uninstall        # stop, disable, remove unit + /opt tree
#
# ENV OVERRIDES
#   TARGET_HOST   davidleeburnett@pi.example.local
#   EXPECT_HOST   rpi5            expected `hostname` — identity is CHECKED,
#                                 never assumed; a hostname is not a machine.
#   REMOTE_ROOT   /opt/composeio-can-bridge
#   SERVICE_USER  composeio
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"

TARGET_HOST="${TARGET_HOST:-davidleeburnett@pi.example.local}"
EXPECT_HOST="${EXPECT_HOST:-rpi5}"
REMOTE_ROOT="${REMOTE_ROOT:-/opt/composeio-can-bridge}"
SERVICE_USER="${SERVICE_USER:-composeio}"
UNIT_NAME="composeio-can-bridge.service"
ENV_PATH="/etc/default/composeio-can-bridge"

SSH=(ssh -o ConnectTimeout=10 "${TARGET_HOST}")

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mXXX\033[0m %s\n' "$*" >&2; exit 1; }

MODE="deploy"
RSYNC_EXTRA=()
case "${1:-}" in
    --dry-run)   MODE="dryrun"; RSYNC_EXTRA=(--dry-run --itemize-changes) ;;
    --no-restart) MODE="norestart" ;;
    --status)    MODE="status" ;;
    --logs)      MODE="logs" ;;
    --uninstall) MODE="uninstall" ;;
    "")          ;;
    *)           die "unknown option: $1 (see the header of this file)" ;;
esac

# --- identity ---------------------------------------------------------------
# "Verify machine identity before acting; do not assume a hostname resolves to
# what you expect." mDNS names move. A deploy that installs a CAN service on
# the wrong machine is not obviously wrong until something transmits.
verify_host() {
    local got
    got="$("${SSH[@]}" 'echo "$(hostname)|$(cat /proc/device-tree/model 2>/dev/null | tr -d "\0")|$(uname -m)"')"
    local hn="${got%%|*}"
    say "target ${TARGET_HOST} is: ${got//|/ / }"
    [[ "${hn}" == "${EXPECT_HOST}" ]] \
        || die "hostname is '${hn}', expected '${EXPECT_HOST}'. Refusing. Set EXPECT_HOST to override deliberately."
}

# --- preflight: the CAN interface -------------------------------------------
verify_can() {
    local iface
    iface="$(grep -E '^CAN_INTERFACE=' "${HERE}/composeio-can-bridge.env" | cut -d= -f2)"
    say "checking SocketCAN interface '${iface}'"
    local state
    state="$("${SSH[@]}" "ip -details link show ${iface} 2>/dev/null || true")"
    [[ -n "${state}" ]] || die "${iface} does not exist on ${EXPECT_HOST}. This script does NOT create it — that is can${iface#can}.service's job and the overlays are hand-corrected."
    grep -qw "UP" <<<"${state}" || die "${iface} exists but is not UP. Bring it up with 'sudo systemctl start can${iface#can}.service' and re-run."
    grep -q "ERROR-ACTIVE" <<<"${state}" \
        || warn "${iface} is not ERROR-ACTIVE — check termination and that a second node is present before trusting any transmit."
    sed -n 's/.*bitrate \([0-9]*\).*/    bitrate: \1/p' <<<"${state}" | head -1
}

# --- the code closure -------------------------------------------------------
# Only what the service imports. Explicitly NOT the whole repo: a deploy that
# ships the tree ships the tree's churn.
sync_code() {
    say "syncing code closure -> ${REMOTE_ROOT}"
    # A --dry-run must change NOTHING on the target, including the directory it
    # would sync into. rsync honours that; a bare `mkdir` over ssh does not.
    if [[ "${MODE}" != "dryrun" ]]; then
        # rsync will not create more than the last path component; make the
        # whole tree and hand it to the login user for the duration of the sync
        # (make_user takes it back to root:root afterwards).
        "${SSH[@]}" "sudo mkdir -p ${REMOTE_ROOT}/python/composeio ${REMOTE_ROOT}/services/can_mqtt_bridge \
                     && sudo chown -R \$(id -un):\$(id -gn) ${REMOTE_ROOT}"
    fi
    local excludes=(--exclude '__pycache__/' --exclude '*.pyc'
                    --exclude '.DS_Store' --exclude '*.egg-info/')
    rsync -a --delete "${excludes[@]}" ${RSYNC_EXTRA[@]+"${RSYNC_EXTRA[@]}"} \
        "${REPO_ROOT}/python/composeio/" "${TARGET_HOST}:${REMOTE_ROOT}/python/composeio/"
    rsync -a --delete "${excludes[@]}" ${RSYNC_EXTRA[@]+"${RSYNC_EXTRA[@]}"} \
        "${REPO_ROOT}/services/can_mqtt_bridge/" "${TARGET_HOST}:${REMOTE_ROOT}/services/can_mqtt_bridge/"
    # The verifier ships WITH the deployment. A proof you have to re-copy by
    # hand is a proof nobody re-runs, and this one is the only thing on the box
    # that can contradict the bridge (it re-derives the CRC and the frame
    # layout rather than importing them).
    rsync -a ${RSYNC_EXTRA[@]+"${RSYNC_EXTRA[@]}"} \
        "${HERE}/loopback_test.py" "${TARGET_HOST}:${REMOTE_ROOT}/loopback_test.py"
}

# --- the venv ---------------------------------------------------------------
# Its own venv, not the host's site-packages and not another project's. The Pi
# already carries ~/cxc-venv for the carousel kiosk; sharing it would couple
# this service's uptime to that project's next dependency bump.
build_venv() {
    say "building venv (paho-mqtt, pyyaml)"
    "${SSH[@]}" "set -e
        test -x ${REMOTE_ROOT}/venv/bin/python || python3 -m venv ${REMOTE_ROOT}/venv
        ${REMOTE_ROOT}/venv/bin/pip -q install --upgrade pip
        ${REMOTE_ROOT}/venv/bin/pip -q install -r ${REMOTE_ROOT}/services/can_mqtt_bridge/requirements.txt
        ${REMOTE_ROOT}/venv/bin/python - <<'PY'
import paho.mqtt, sys
sys.path.insert(0, '${REMOTE_ROOT}/python')
import composeio.transport, composeio.peer.socketcan
print('    venv OK: paho', paho.mqtt.__version__, '| composeio importable')
PY"
}

# --- the service user -------------------------------------------------------
make_user() {
    say "ensuring system user '${SERVICE_USER}'"
    "${SSH[@]}" "id -u ${SERVICE_USER} >/dev/null 2>&1 || \
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin ${SERVICE_USER}
        sudo chown -R root:root ${REMOTE_ROOT}
        sudo chmod -R a+rX ${REMOTE_ROOT}"
}

# --- unit + env -------------------------------------------------------------
install_unit() {
    say "installing ${ENV_PATH} and ${UNIT_NAME}"
    # Staged through the login user's home because the target dirs are root-owned
    # and rsync-over-ssh has no sudo.
    rsync -a "${HERE}/composeio-can-bridge.env" "${TARGET_HOST}:/tmp/ccb.env"
    rsync -a "${HERE}/${UNIT_NAME}"             "${TARGET_HOST}:/tmp/ccb.service"
    "${SSH[@]}" "set -e
        sudo install -o root -g root -m 0644 /tmp/ccb.env ${ENV_PATH}
        sudo install -o root -g root -m 0644 /tmp/ccb.service /etc/systemd/system/${UNIT_NAME}
        rm -f /tmp/ccb.env /tmp/ccb.service
        sudo systemctl daemon-reload
        sudo systemctl enable ${UNIT_NAME} >/dev/null"
}

restart_service() {
    say "restarting ${UNIT_NAME}"
    "${SSH[@]}" "sudo systemctl restart ${UNIT_NAME}"
    sleep 3
    show_status
    # `active` on its own proves systemd forked something, not that the bridge
    # connected. The startup log lines are the actual evidence.
    "${SSH[@]}" "systemctl is-active --quiet ${UNIT_NAME}" \
        || die "service is NOT active — see the journal above"
}

show_status() {
    "${SSH[@]}" "set +e
        echo '--- unit ---'
        systemctl is-active ${UNIT_NAME}; systemctl is-enabled ${UNIT_NAME}
        echo '--- interfaces ---'
        ip -brief link show type can
        echo '--- last 25 log lines ---'
        journalctl -u ${UNIT_NAME} -n 25 --no-pager -o short-iso"
}

case "${MODE}" in
    status) verify_host; show_status; exit 0 ;;
    logs)   verify_host; exec "${SSH[@]}" "journalctl -u ${UNIT_NAME} -f -o short-iso" ;;
    uninstall)
        verify_host
        say "uninstalling"
        "${SSH[@]}" "set +e
            sudo systemctl disable --now ${UNIT_NAME}
            sudo rm -f /etc/systemd/system/${UNIT_NAME} ${ENV_PATH}
            sudo systemctl daemon-reload
            sudo rm -rf ${REMOTE_ROOT}"
        say "done. can0/can1, their units and config.txt are untouched."
        exit 0 ;;
esac

verify_host
verify_can
sync_code
if [[ "${MODE}" == "dryrun" ]]; then
    say "dry run complete — nothing was changed on ${EXPECT_HOST}"
    exit 0
fi
build_venv
make_user
install_unit
if [[ "${MODE}" == "norestart" ]]; then
    say "installed; service NOT restarted (--no-restart)"
else
    restart_service
fi
say "deployed. Follow it with: $0 --logs"
