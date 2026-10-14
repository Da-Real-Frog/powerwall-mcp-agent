#!/usr/bin/env bash
# Rejoins the gateway's Wi-Fi (TEG-xxx) if the bridge has dropped off it.
#
# NetworkManager gives up on a Wi-Fi connection after a failure it reports as
# "no-secrets", which is what it logs when the gateway's Wi-Fi vanishes for a
# moment (a gateway restart or update). autoconnect-retries 0 doesn't cover
# that, so the bridge stays off TEG-xxx until someone runs nmcli by hand.
# setup-bridge.sh installs this with a systemd timer that runs it every minute.
#
# Usage: teg-watchdog.sh <teg-interface>
# Holds no passwords: it reuses the connection NetworkManager already saved.
set -euo pipefail

TEG_IF=${1:?usage: teg-watchdog.sh <teg-interface>}

state=$(nmcli -g GENERAL.STATE device show "$TEG_IF" 2>/dev/null || true)
if [[ "$state" == 100* ]]; then
  exit 0
fi

echo "$TEG_IF is '${state:-unknown}', reconnecting"
# A user-requested activation clears the block NetworkManager put on autoconnect.
# It picks the device's saved Wi-Fi connection, so no network name is needed here.
nmcli radio wifi on
if nmcli --wait 45 device connect "$TEG_IF"; then
  echo "$TEG_IF reconnected"
else
  echo "$TEG_IF reconnect failed; will try again next run" >&2
  exit 1
fi
