# shellcheck shell=bash
# The host overlay for shell consumers ([CXC-SCL-SCRUB-OVERLAY-PARITY]); SOURCE this, do not run it.
# The same rules as python/composeio/_overlay.py: the file is $COMPOSEIO_OVERLAY_ENV, else
# ~/.config/composeio/live.env; `none` disables it; a missing or non-regular file is a no-op;
# KEY=VALUE lines, `#` comments, optional `export ` prefix, one pair of surrounding quotes
# stripped, NO shell expansion (the file is read, never sourced); only names NOT already set are
# exported, so an explicit environment always wins and the FIRST occurrence of a name wins; a
# malformed line, a key over 256 or a value over 65536 characters is skipped. (bash cannot hold a
# NUL, and this helper does not check UTF-8: the Python loader skips such lines, bash reads bytes.)
# Locals are prefixed so an overlay name such as `key` or `line` is never shadowed.
# Safe under `set -euo pipefail`.
_composeio_overlay_apply() {
  local _cio_f="${COMPOSEIO_OVERLAY_ENV:-$HOME/.config/composeio/live.env}" _cio_l _cio_k _cio_v
  case "$(printf '%s' "$_cio_f" | tr '[:upper:]' '[:lower:]')" in none) return 0 ;; esac
  case "$_cio_f" in "~/"*) _cio_f="$HOME/${_cio_f#\~/}" ;; esac
  [ -f "$_cio_f" ] && [ -r "$_cio_f" ] || return 0
  while IFS= read -r _cio_l || [ -n "$_cio_l" ]; do
    _cio_l="${_cio_l#"${_cio_l%%[![:space:]]*}"}"; _cio_l="${_cio_l%"${_cio_l##*[![:space:]]}"}"
    case "$_cio_l" in ''|'#'*) continue ;; 'export '*) _cio_l="${_cio_l#export }" ;; esac
    case "$_cio_l" in *=*) ;; *) continue ;; esac
    _cio_k="${_cio_l%%=*}"; _cio_v="${_cio_l#*=}"
    _cio_k="${_cio_k%"${_cio_k##*[![:space:]]}"}"; _cio_v="${_cio_v#"${_cio_v%%[![:space:]]*}"}"
    [[ "$_cio_k" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    [ "${#_cio_k}" -le 256 ] || continue
    if [ "${#_cio_v}" -ge 2 ]; then
      case "$_cio_v" in \"*\"|\'*\') _cio_v="${_cio_v:1:${#_cio_v}-2}" ;; esac
    fi
    [ "${#_cio_v}" -le 65536 ] || continue
    [ -n "${!_cio_k+x}" ] && continue
    export "$_cio_k=$_cio_v"
  done < "$_cio_f"
  return 0
}
_composeio_overlay_apply
unset -f _composeio_overlay_apply
