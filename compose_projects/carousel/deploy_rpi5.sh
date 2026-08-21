#!/usr/bin/env bash
# deploy_rpi5.sh — COMPLETE, idempotent, reproducible deploy of the carousel
# kiosk to pi.example.local, driven by the committed deploy.manifest (the declared
# asset closure).  Replaces the ad-hoc, out-of-band file copies that once
# shipped the python package but not the compose_projects/ authoring tree and
# blanked the CIC nav (defect 2026-07-31).
#
# WHAT IT GUARANTEES
#   • Everything the running scenes reference ships — declared in deploy.manifest
#     and rsynced with path structure preserved.  Re-running is safe (rsync is
#     idempotent; unchanged files are skipped).
#   • A timestamped backup of the existing hand-edited trees is taken BEFORE any
#     overwrite (reversible).
#   • The kiosk is restarted the SAFE way: publish scene-swap INTENTS on the bus
#     so the supervisor (kiosk.py) kills+respawns the render child itself.  NOTHING
#     is signalled/killed directly — the mistake that took the kiosk down twice
#     this session (a SIGTERM to the render child cascaded to kiosk.py).
#
# USAGE
#   ./deploy_rpi5.sh                 # backup → sync (mirror) → verify → restart
#   ./deploy_rpi5.sh --dry-run       # show exactly what rsync WOULD change
#   ./deploy_rpi5.sh --sync-only     # backup → sync → verify, no restart
#   ./deploy_rpi5.sh --restart-only  # just the safe bus scene-swap restart
#   ./deploy_rpi5.sh --grim          # sync/restart, then pull a grim readback PNG
#   ./deploy_rpi5.sh --mirror        # OPT-IN --delete (mirror; prune rpi5 extras)
#
# SYNC IS ADD/UPDATE BY DEFAULT (no --delete).  rpi5's python/ has legitimately
# diverged from the mac tree (pip editable-install composeio.egg-info/, a
# python/services dir, Pi-only modules); a blind mirror would delete those.
# --mirror is opt-in and should only be run after reviewing a --dry-run.
#
# ENV OVERRIDES
#   RPI_HOST (davidleeburnett@pi.example.local)  RPI_DIR (maker/composeio)
#   BROKER   (mqtt-broker.local)      RESTART_TARGETS ("launcher bsg")
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
MANIFEST="${HERE}/deploy.manifest"

RPI_HOST="${RPI_HOST:-davidleeburnett@pi.example.local}"
RPI_DIR="${RPI_DIR:-maker/composeio}"          # relative to the ssh login home
BROKER="${BROKER:-mqtt-broker.local}"
RESTART_TARGETS="${RESTART_TARGETS:-launcher bsg}"   # full round-trip = fresh child
BACKUP_KEEP=5
# grim runs from an ssh session (no session env); supply the wayfire seat.
GRIM_ENV="${GRIM_ENV:-XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-1}"

RSYNC_EXCLUDES=(
    --exclude '__pycache__/' --exclude '*.pyc' --exclude '.git'
    --exclude '.DS_Store'   --exclude '*.bak-*' --exclude 'proofs/'
    # The launcher tile icons are AUTHORING sources served from the bus store
    # (bus://assets/icons/carousel/…, scripts/publish_carousel_assets.py) — the
    # kiosk resolves them by reference, so they are NOT shipped.  Anchored to the
    # transfer root (leading /) so it only touches carousel/resources/, never a
    # PNG elsewhere in the closure (e.g. under python/).
    --exclude '/compose_projects/carousel/resources/*.png'
)

log()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[err]\033[0m %s\n' "$*" >&2; exit 1; }

# ── argument parsing ───────────────────────────────────────────────────────
DRY_RUN=0 SYNC_ONLY=0 RESTART_ONLY=0 GRIM=0 MIRROR=0
for arg in "$@"; do
    case "$arg" in
        --dry-run)      DRY_RUN=1 ;;
        --sync-only)    SYNC_ONLY=1 ;;
        --restart-only) RESTART_ONLY=1 ;;
        --grim)         GRIM=1 ;;
        --mirror)       MIRROR=1 ;;
        -h|--help)      grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)              die "unknown arg: $arg" ;;
    esac
done

[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"

# ── manifest → path list (strip comments/blank lines) ──────────────────────
# portable (bash 3.2 on stock macOS has no `mapfile`).
ENTRIES=()
while IFS= read -r _line; do
    ENTRIES+=("$_line")
done < <(grep -vE '^[[:space:]]*(#|$)' "$MANIFEST")
[ "${#ENTRIES[@]}" -gt 0 ] || die "manifest declares no paths"

# ── preflight ──────────────────────────────────────────────────────────────
preflight() {
    log "preflight: ${RPI_HOST}"
    ssh -o ConnectTimeout=6 "$RPI_HOST" \
        "test -d ~/${RPI_DIR} && test -x ~/${RPI_DIR}/.venv/bin/python3" \
        || die "rpi5 not reachable, or ~/${RPI_DIR}/.venv missing"
    for e in "${ENTRIES[@]}"; do
        [ -e "${REPO_ROOT}/${e}" ] || die "manifest path missing locally: ${e}"
    done
    log "manifest closure (${#ENTRIES[@]} entries): ${ENTRIES[*]}"
}

# ── backup (timestamped, reversible) ───────────────────────────────────────
backup() {
    local ts; ts="$(date +%Y%m%d-%H%M%S)"
    log "backup existing hand-edited trees → ~/maker/backups/composeio-${ts}.tar.gz"
    ssh "$RPI_HOST" "mkdir -p ~/maker/backups && \
        tar czf ~/maker/backups/composeio-${ts}.tar.gz \
            -C ~/${RPI_DIR} compose_projects fonts 2>/dev/null && \
        ls -1t ~/maker/backups/composeio-*.tar.gz | tail -n +$((BACKUP_KEEP+1)) \
            | xargs -r rm -f" \
        || warn "backup step reported an issue (continuing)"
}

# ── clean stale prior manual backups the old ad-hoc copies left behind ─────
clean_stale() {
    log "removing stale *.bak-* left by prior out-of-band copies"
    ssh "$RPI_HOST" \
        "find ~/${RPI_DIR}/compose_projects -name '*.bak-*' -type f -print -delete" \
        || warn "stale cleanup reported an issue (continuing)"
}

# ── sync (rsync -aR dot-marker per manifest entry) ─────────────────────────
sync_tree() {
    local rflags=(-a -R --human-readable "${RSYNC_EXCLUDES[@]}")
    local mode="add/update"
    [ "$MIRROR" -eq 1 ] && { rflags+=(--delete); mode="MIRROR/--delete"; }
    [ "$DRY_RUN" -eq 1 ] && { rflags+=(-n -v); mode="${mode} (dry-run)"; }
    log "rsync [${mode}] → ${RPI_HOST}:${RPI_DIR}/"
    local e src
    for e in "${ENTRIES[@]}"; do
        # /./ marks where the preserved relative path begins (rsync -R).
        src="${REPO_ROOT}/./${e}"
        rsync "${rflags[@]}" "$src" "${RPI_HOST}:${RPI_DIR}/"
    done
}

# ── verify (sha256 of the load-bearing files must match) ───────────────────
verify() {
    log "verify: checksum load-bearing files (mac vs rpi5)"
    # CIC art (chrome/EXT/nav) is served from the bus store, NOT deployed — so it
    # is not checksummed here.  The bundled OFFLINE fallback + the project-local
    # widget cluster now ride under compose_projects/bsg/widgets/ ([CXC-PROJECT-
    # WIDGETS]); menu.py is the load-bearing nav parser the 2-up imports.
    local files=(
        "compose_projects/carousel/kiosk.py"
        "compose_projects/carousel/launcher.compose.yaml"
        "compose_projects/carousel/bsg_app.compose.yaml"
        "compose_projects/bsg/widgets/cic/menu.py"
    )
    local ok=1 f local_sum remote_sum
    for f in "${files[@]}"; do
        local_sum="$(shasum -a 256 "${REPO_ROOT}/${f}" | awk '{print $1}')"
        remote_sum="$(ssh "$RPI_HOST" "sha256sum ~/${RPI_DIR}/${f} 2>/dev/null | awk '{print \$1}'")"
        if [ "$local_sum" = "$remote_sum" ] && [ -n "$remote_sum" ]; then
            printf '    \033[1;32mok\033[0m   %s\n' "$f"
        else
            printf '    \033[1;31mMISS\033[0m %s (local=%s remote=%s)\n' \
                "$f" "${local_sum:0:12}" "${remote_sum:0:12}"; ok=0
        fi
    done
    [ "$ok" -eq 1 ] || die "verification failed — some files did not land"
}

# ── restart (SAFE: bus scene-swap, never a kill) ───────────────────────────
restart() {
    log "restart via bus scene-swap intents: ${RESTART_TARGETS} (no kill, supervisor owns the child)"
    # Run scene_swap on the Pi with the deployed venv+package so broker
    # reachability is guaranteed and the just-synced code is what publishes.
    # shellcheck disable=SC2029
    ssh "$RPI_HOST" \
        "cd ~/${RPI_DIR} && PYTHONPATH=~/${RPI_DIR}/python \
         ~/${RPI_DIR}/.venv/bin/python3 \
         compose_projects/carousel/scene_swap.py --broker ${BROKER} ${RESTART_TARGETS}" \
        || die "scene-swap restart failed"
    log "supervisor health:"
    ssh "$RPI_HOST" "pgrep -af 'compose_projects/carousel/kiosk.py' || echo '  (kiosk.py NOT running!)'"
}

# ── grim readback ──────────────────────────────────────────────────────────
grim_readback() {
    local ts; ts="$(date +%Y%m%d-%H%M%S)"
    local remote="/tmp/kiosk_verify-${ts}.png"
    local local_out="${HERE}/proofs/deploy_verify-${ts}.png"
    log "grim readback → ${local_out}"
    sleep 3   # let the respawned scene render
    ssh "$RPI_HOST" "${GRIM_ENV} grim ${remote}" || { warn "grim failed"; return 0; }
    mkdir -p "${HERE}/proofs"
    scp -q "${RPI_HOST}:${remote}" "${local_out}" \
        && log "pulled ${local_out}" || warn "scp of grim PNG failed"
    ssh "$RPI_HOST" "rm -f ${remote}" || true
}

# ── orchestration ──────────────────────────────────────────────────────────
main() {
    preflight
    if [ "$RESTART_ONLY" -eq 1 ]; then
        restart
        [ "$GRIM" -eq 1 ] && grim_readback
        log "restart-only complete."
        return 0
    fi
    if [ "$DRY_RUN" -eq 1 ]; then
        sync_tree
        log "dry-run complete (no changes made, no backup taken)."
        return 0
    fi
    backup
    clean_stale
    sync_tree
    verify
    if [ "$SYNC_ONLY" -eq 1 ]; then
        log "sync-only complete (kiosk NOT restarted — run --restart-only to apply)."
        return 0
    fi
    restart
    [ "$GRIM" -eq 1 ] && grim_readback
    log "deploy complete."
}

main
