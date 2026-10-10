#!/bin/bash
# Builds "Powerwall Inspector.app" in ~/Applications so the launcher shows up in Spotlight,
# Launchpad and can be dragged to the Dock. The app opens a Terminal window that runs
# launch-inspector.sh from this folder, so keep the repo where it is (or re-run this after moving it).
#
# Usage: ./make-app.sh [install-dir]   (default ~/Applications)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/launch-inspector.sh"
DEST_DIR="${1:-$HOME/Applications}"
APP="$DEST_DIR/Powerwall Inspector.app"

chmod +x "$SCRIPT"
mkdir -p "$DEST_DIR"
rm -rf "$APP"

# Quote the path for the shell inside Terminal, then escape it for the AppleScript string
SHELL_CMD="$(printf '%q' "$SCRIPT")"
AS_CMD="${SHELL_CMD//\\/\\\\}"
AS_CMD="${AS_CMD//\"/\\\"}"

osacompile -o "$APP" -e "tell application \"Terminal\"
    activate
    do script \"$AS_CMD\"
end tell"

# Let Spotlight find it right away
mdimport "$APP" 2>/dev/null || true

echo "✅ Built $APP"
echo "   Launch it from Spotlight (type 'Powerwall Inspector') or drag it to the Dock."
