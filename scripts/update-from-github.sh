#!/usr/bin/env bash
# update-from-github.sh — pull the latest code for this project from
# GitHub and, if anything changed, syntax-check it and restart the app.
#
# This is the ONLY way code gets onto the Mac mini: Muse writes code to
# the private GitHub repo base-plate-web (never directly to the Mac),
# and this script brings it down. It is safe to run any time:
#
#   * Nothing new on GitHub        -> exits quietly, changes nothing.
#   * Tracked files edited locally -> refuses to update (a pull would
#     overwrite them) and logs why. Runtime data (data/config.json,
#     the password hash, logs/) is git-ignored and is NEVER touched.
#   * New code fails a syntax check -> rolls back to the previous
#     commit and logs why, so a bad push cannot take the app down.
#   * App running under launchd    -> restarted so the new code loads.
#     App running manually in a Terminal -> the log (and this script's
#     output) says the code updated and a manual restart is needed.
#
# Run it by hand any time:   ./scripts/update-from-github.sh
# It also runs automatically every 5 minutes via the launchd job
# com.user.base-plate-web-updater (see scripts/install-mini-services.sh).

set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.." || exit 2
PROJECT="$(pwd)"
mkdir -p "$PROJECT/logs"
LOG="$PROJECT/logs/update.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] $*" | tee -a "$LOG"; }

if [ ! -d "$PROJECT/.git" ]; then
  log "ERROR: $PROJECT is not a git clone yet — do the one-time setup in README.md (\"Deploy on the Mac mini\") first."
  exit 2
fi

if ! git fetch --quiet origin main; then
  log "ERROR: git fetch failed (network down, or GitHub sign-in needs attention in a Terminal). Nothing changed."
  exit 2
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"

if [ "$LOCAL" = "$REMOTE" ]; then
  # Up to date. Stay quiet (this runs every 5 minutes).
  exit 0
fi

# A commit that already failed its syntax check once is not retried
# every 5 minutes — it is skipped quietly until a NEWER commit lands.
BAD_MARKER="$PROJECT/logs/bad-commit.txt"
if [ -f "$BAD_MARKER" ] && [ "$(cat "$BAD_MARKER")" = "$REMOTE" ]; then
  exit 0
fi

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  log "NOT UPDATING: tracked files were edited by hand on this Mac, and a pull could overwrite them:"
  git status --porcelain --untracked-files=no | tee -a "$LOG"
  log "Commit or undo those edits, then run ./scripts/update-from-github.sh again."
  exit 3
fi

log "Update available: $(git log -1 --oneline "$LOCAL") -> $(git log -1 --oneline "$REMOTE")"
git log --oneline "$LOCAL..$REMOTE" | tee -a "$LOG"

if ! git merge --ff-only --quiet "$REMOTE"; then
  log "ERROR: fast-forward pull failed. Nothing was overwritten; the previous code is still in place."
  exit 3
fi

# Syntax-check every Python file before the new code is allowed to run.
PY="$(command -v python3 || true)"
fail=0
if [ -n "$PY" ]; then
  for f in server.py base_plate_calc.py eval_db.py xml_import.py set_password.py streamlit_app.py; do
    [ -e "$f" ] || continue
    if ! "$PY" -m py_compile "$f" >>"$LOG" 2>&1; then
      log "ERROR: syntax check failed on $f"
      fail=1
    fi
  done
fi
if [ "$fail" -ne 0 ]; then
  log "Rolling back to the previous working code ($LOCAL)."
  git reset --hard --quiet "$LOCAL"
  echo "$REMOTE" > "$BAD_MARKER"
  exit 4
fi

rm -f "$BAD_MARKER" 2>/dev/null || true
log "Updated OK to $(git log -1 --oneline)."

# Restart the app if launchd manages it, so the new code actually loads.
LABEL="com.user.base-plate-web"
if launchctl list 2>/dev/null | awk -v l="$LABEL" '$3 == l {found=1} END {exit !found}'; then
  if launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>>"$LOG"; then
    log "App restarted via launchd — new code is live."
  else
    log "WARNING: code updated, but the launchd restart failed — check logs/app-error.log."
  fi
else
  log "Code updated. The app is not running under launchd, so restart it in its Terminal window (Ctrl+C, then: python3 server.py) to load the new code."
fi
