# shellcheck shell=bash
# The host overlay for shell consumers ([CXC-SCL-SCRUB-OVERLAY-PARITY]); SOURCE this, do not run it.
# The same rules as python/composeio/_overlay.py: the file is $COMPOSEIO_OVERLAY_ENV, else
# ~/.config/composeio/live.env; `none` disables it; a missing file is a no-op; KEY=VALUE lines,
# `#` comments, optional `export ` prefix, one pair of surrounding quotes stripped, NO shell
# expansion (the file is read, never sourced); only names NOT already set are exported, so an
# explicit environment always wins; a malformed line is skipped. Safe under `set -euo pipefail`.
_composeio_overlay_apply() {
  local f="${COMPOSEIO_OVERLAY_ENV:-$HOME/.config/composeio/live.env}" line key value
  case "$(printf '%s' "$f" | tr '[:upper:]' '[:lower:]')" in none) return 0 ;; esac
  case "$f" in "~/"*) f="$HOME/${f#\~/}" ;; esac
  [ -f "$f" ] && [ -r "$f" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line#"${line%%[![:space:]]*}"}"; line="${line%"${line##*[![:space:]]}"}"
    case "$line" in ''|'#'*) continue ;; 'export '*) line="${line#export }" ;; esac
    case "$line" in *=*) ;; *) continue ;; esac
    key="${line%%=*}"; value="${line#*=}"
    key="${key%"${key##*[![:space:]]}"}"; value="${value#"${value%%[![:space:]]*}"}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    if [ "${#value}" -ge 2 ]; then
      case "$value" in \"*\"|\'*\') value="${value:1:${#value}-2}" ;; esac
    fi
    [ -n "${!key+x}" ] && continue
    export "$key=$value"
  done < "$f"
  return 0
}
_composeio_overlay_apply
unset -f _composeio_overlay_apply
