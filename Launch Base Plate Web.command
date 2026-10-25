#!/bin/bash
# Launch Base Plate Web — double-click this file in Finder on the Mac mini.
#
# What it does:
#   * If the app is already running (e.g. via the launchd service),
#     it just opens the app in your browser — it never starts a second
#     copy, so there is no fight over port 8512.
#   * If the app is not running, it starts it in this Terminal window
#     and opens the app in your browser after 2 seconds. Closing this
#     Terminal window stops the app. (For always-on, use the launchd
#     service instead — see README.md, "Deploy on the Mac mini".)
#
# No password or other secret is in this file. The app itself asks for
# its password on its login/setup page.

cd "$(dirname "$0")" || {
  echo "Could not find the app folder. Move this file back next to server.py and try again."
  exit 1
}

PORT="${TAPP_PORT:-8512}"
URL="http://127.0.0.1:${PORT}"

open_app() {
  # `open` here is macOS's opener; on anything else just print the URL.
  if [[ "$OSTYPE" == darwin* ]] && command -v open >/dev/null 2>&1; then
    open "$URL"
  else
    echo "Open in your browser: $URL"
  fi
}

if curl -fsS --max-time 2 "$URL/api/state" >/dev/null 2>&1; then
  echo "Base Plate app is already running — opening it in your browser."
  open_app
  exit 0
fi

echo "Starting the Base Plate app on $URL ..."
echo "(Keep this Terminal window open while you use the app; closing it stops the app.)"
if [[ "$OSTYPE" == darwin* ]] && command -v open >/dev/null 2>&1; then
  ( sleep 2; open "$URL" ) &
fi
exec python3 server.py
