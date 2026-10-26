#!/usr/bin/env bash
# install-mini-services.sh — one-time install of the two launchd jobs
# on the Mac mini, run AFTER the one-time git-clone setup in README.md:
#
#   1. com.user.base-plate-web          the app itself (always-on,
#                                       restarts on crash, starts at login)
#   2. com.user.base-plate-web-updater  every 5 minutes, pulls new
#                                       code from GitHub and restarts
#                                       the app when code changed
#
# It fills this repo's actual folder path into both plist templates
# (so launchd points at wherever you actually cloned the repo, not a
# hardcoded ~/base-plate-web), copies them to ~/Library/LaunchAgents,
# and (re)loads them. Safe to re-run — e.g. after a plist template
# changes in the repo, or after moving the repo folder.
#
# BEFORE running this: stop any app copy running manually in a Terminal
# (Ctrl+C in its window), or two servers will fight over port 8512.

set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$(dirname "$0")/.."
PROJECT="$(pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"

if [ ! -d "$PROJECT/.git" ]; then
  echo "ERROR: $PROJECT is not a git clone yet — do the one-time setup in README.md first."
  exit 2
fi

mkdir -p "$PROJECT/logs" "$AGENTS_DIR"
chmod +x "$PROJECT/scripts/update-from-github.sh" "$PROJECT/scripts/install-mini-services.sh" || true

install_one() {
  local template="$1" label="$2"
  local dest="$AGENTS_DIR/$label.plist"
  # | delimiter so a path containing / works; the repo path has no |
  # in practice. This is what makes a moved repo folder work.
  sed "s|__PROJECT_DIR__|$PROJECT|g" "$PROJECT/$template" > "$dest"
  if grep -q "__PROJECT_DIR__" "$dest"; then
    echo "ERROR: $template still contains an unfilled __PROJECT_DIR__ placeholder — not installing $label."
    exit 2
  fi
  echo "Installed $dest"
  launchctl unload "$dest" 2>/dev/null || true
  launchctl load "$dest"
  echo "Loaded $label"
}

install_one "com.user.base-plate-web.plist" "com.user.base-plate-web"
install_one "com.user.base-plate-web-updater.plist" "com.user.base-plate-web-updater"

echo
echo "Done. Check status with:"
echo "  launchctl list | grep base-plate-web"
echo "App log:    $PROJECT/logs/app.log"
echo "Update log: $PROJECT/logs/update.log"
echo "Test an update any time with:  $PROJECT/scripts/update-from-github.sh"
