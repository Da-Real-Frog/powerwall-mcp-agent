#!/bin/bash
# Starts MCP Inspector already pointed at the Powerwall MCP server and opens it in the browser.
# Press Ctrl-C in this window to stop it. Holds no credentials: the Inspector makes a fresh
# session token on every run and puts it in the URL it opens.
#
# Overrides (optional):
#   MCP_SERVER_URL     server to inspect (default http://percula.local:8000/mcp)
#   INSPECTOR_VERSION  Inspector release to run (default 2.10.1, the one this was tested with)

set -euo pipefail

SERVER_URL="${MCP_SERVER_URL:-http://percula.local:8000/mcp}"
INSPECTOR_VERSION="${INSPECTOR_VERSION:-2.10.1}"
UI_PORT="${CLIENT_PORT:-6274}"

# Apps launched from Spotlight or the Dock get a bare PATH, so add the usual Node locations
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.volta/bin:$PATH"
if [ -s "$HOME/.nvm/nvm.sh" ] && ! command -v npx >/dev/null 2>&1; then
    # shellcheck disable=SC1091
    . "$HOME/.nvm/nvm.sh"
fi
if ! command -v npx >/dev/null 2>&1; then
    echo "❌ npx not found. Install Node.js 22.19 or newer (e.g. 'brew install node') and try again."
    exit 1
fi

# An Inspector left running from an earlier launch holds the port; stop it so this one starts clean
OLD_PIDS="$(lsof -ti "tcp:$UI_PORT" -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$OLD_PIDS" ]; then
    if ps -o command= -p $OLD_PIDS | grep -qi inspector; then
        echo "Stopping the Inspector that was already running on port $UI_PORT..."
        kill $OLD_PIDS
        sleep 1
    else
        echo "❌ Port $UI_PORT is used by something other than the Inspector. Set CLIENT_PORT to another port."
        exit 1
    fi
fi

# Warn early if the server is down, but start anyway so the Inspector can retry
if ! curl -s -o /dev/null --max-time 3 "$SERVER_URL"; then
    echo "⚠️  $SERVER_URL is not answering right now; the Inspector will start but won't connect until it does."
fi

echo "Inspecting $SERVER_URL (streamable HTTP) with MCP Inspector $INSPECTOR_VERSION"
exec npx -y "@modelcontextprotocol/inspector@$INSPECTOR_VERSION" --web \
    --transport http --server-url "$SERVER_URL"
