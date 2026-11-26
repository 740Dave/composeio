#!/usr/bin/env bash
# Build (and optionally flash) the R503 probe firmware.
#
# ⛔ WHY THIS SCRIPT EXISTS AT ALL — it is not convenience.
#    This repo lives at "${DATASHEETS_HOME:-$HOME/datasheets}/R503".
#    "Data Sheets" CONTAINS A SPACE. PlatformIO refuses outright:
#        Error: Detected a whitespace character in project paths.
#    ESP-IDF's CMake/ninja toolchain cannot build from such a path. This is a
#    property of the REPO'S LOCATION, not of the firmware, and it cannot be fixed
#    from inside the firmware.
#
#    So the source of truth stays in the repo and the BUILD happens in a mirrored
#    space-free directory. The mirror is one-way and clobbering: edit in the repo,
#    never in the mirror.
#
# Usage:
#   ./build.sh                 build only
#   ./build.sh upload          build + flash
#                              ⛔ flashes ONLY the board whose USB serial number is
#                                 00:00:5E:00:53:06. The port is resolved from that
#                                 serial AT USE TIME. It is never cached.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SRC_DIR}/../.." && pwd)"
BUILD_DIR="${HOME}/.cache/composeio/r503_probe_build"

# ⛔ The ONE board this firmware may ever be written to. David's ESP32-S3 SuperMini.
#    The ESP32-C3 (00:00:5E:00:53:02) and the FTDI bridge (<device-serial>) are OFF LIMITS.
# The board identity is deployment config (composeio-deploy: r503_probe.env). Unset = refuse to flash.
EXPECT_SERIAL="${R503_EXPECT_SERIAL:-}"
if [[ "${1:-}" == "upload" && -z "${EXPECT_SERIAL}" ]]; then
  echo "refusing to flash: set R503_EXPECT_SERIAL to the USB serial of the one board this firmware may write" >&2
  exit 2
fi

mkdir -p "${BUILD_DIR}"
rsync -a --delete \
  --exclude '.pio/' \
  "${SRC_DIR}/" "${BUILD_DIR}/"

TARGET="${1:-}"

if [ "${TARGET}" = "upload" ]; then
  # ⛔ Resolve the port FROM THE SERIAL, now. A device path is a lease: this board
  #    has native USB and re-enumerates when BOOT is pressed.
  PORT="$(python3 "${REPO_ROOT}/probe/r503probe.py" --serial "${EXPECT_SERIAL}" port)"
  echo "resolved ${EXPECT_SERIAL} -> ${PORT}"
  cd "${BUILD_DIR}" && pio run -t upload --upload-port "${PORT}"
else
  cd "${BUILD_DIR}" && pio run
fi

echo
echo "build dir: ${BUILD_DIR}"
