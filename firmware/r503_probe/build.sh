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
#                              ⛔ flashes ONLY the board whose USB serial equals
#                                 $R503_EXPECT_SERIAL (set in the host overlay), and
#                                 never one on $FLASH_DENY_SERIALS. The port is resolved
#                                 from that serial AT USE TIME. It is never cached.
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SRC_DIR}/../.." && pwd)"
GUARD="${REPO_ROOT}/python/composeio/flash_guard.py"
HOOK="${REPO_ROOT}/python/scripts/pio_flash_guard.py"
# The mirror PRESERVES the repo-relative layout (firmware/r503_probe + the guard files)
# so platformio.ini's `pre:../../python/scripts/pio_flash_guard.py` resolves in it too.
MIRROR_ROOT="${HOME}/.cache/composeio/r503_probe_build"
BUILD_DIR="${MIRROR_ROOT}/firmware/r503_probe"

TARGET="${1:-}"

# ⛔ IDENTITY GATE — runs BEFORE anything is copied, built or enumerated.
#    The one board this firmware may write is deployment config (host overlay:
#    R503_EXPECT_SERIAL); the never-flash list is FLASH_DENY_SERIALS. Unset = refuse.
#    Public code names no device: a pseudonymous serial here would never match the real
#    board, so a guard comparing against it would PASS for the board it exists to protect.
if [ "${TARGET}" = "upload" ]; then
  if [ -z "${FLASH_DENY_SERIALS:-}" ]; then
    echo "refusing to flash: set FLASH_DENY_SERIALS (host overlay) to the USB serials that must never be written" >&2
    exit 2
  fi
  if [ -z "${R503_EXPECT_SERIAL:-}" ]; then
    echo "refusing to flash: set R503_EXPECT_SERIAL to the USB serial of the one board this firmware may write" >&2
    exit 2
  fi
  # ⛔ Resolve the port FROM THE SERIAL, now, through the shared guard (passive: no
  #    port is opened). A device path is a lease: this board has native USB and
  #    re-enumerates when BOOT is pressed.
  PORT="$(python3 "${GUARD}" resolve --expect-var R503_EXPECT_SERIAL)"
  echo "resolved R503_EXPECT_SERIAL -> ${PORT}"
fi

mkdir -p "${BUILD_DIR}" "${MIRROR_ROOT}/python/composeio" "${MIRROR_ROOT}/python/scripts"
rsync -a --delete \
  --exclude '.pio/' \
  "${SRC_DIR}/" "${BUILD_DIR}/"
cp "${GUARD}" "${MIRROR_ROOT}/python/composeio/flash_guard.py"
cp "${HOOK}" "${MIRROR_ROOT}/python/scripts/pio_flash_guard.py"

if [ "${TARGET}" = "upload" ]; then
  # The PlatformIO hook re-checks the same identity at upload time (belt and braces).
  cd "${BUILD_DIR}" && pio run -t upload --upload-port "${PORT}"
else
  cd "${BUILD_DIR}" && pio run
fi

echo
echo "build dir: ${BUILD_DIR}"
