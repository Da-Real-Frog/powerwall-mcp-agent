#!/usr/bin/env bash
# Builds the watchdog image on the Pi and (re)starts its container. Safe to re-run after a
# `git pull`. Expects the files set up in README.md under ~/watchdog: watchdog.env and ssh/.
#
# Usage: ./deploy-pi.sh            build, then restart the daily watchdog
#        ./deploy-pi.sh --once     build, then run one check now and show its result
set -euo pipefail

DIR=$HOME/watchdog
for f in "$DIR/watchdog.env" "$DIR/ssh/id_ed25519" "$DIR/ssh/known_hosts"; do
  if [[ ! -f "$f" ]]; then
    echo "Missing $f (see watchdog/README.md)." >&2
    exit 1
  fi
done
mkdir -p "$DIR/data"

docker build -t percula-watchdog "$(dirname "$(readlink -f "$0")")"

# --network host: Docker on the Pi runs with its firewall handling off (README step 1), so the
# container shares the Pi's network instead of a Docker bridge network.
args=(--network host --env-file "$DIR/watchdog.env" -v "$DIR/ssh:/ssh:ro" -v "$DIR/data:/data")

if [[ "${1:-}" == "--once" ]]; then
  exec docker run --rm "${args[@]}" percula-watchdog python watchdog.py --once
fi

docker rm -f percula-watchdog >/dev/null 2>&1 || true
docker run -d --name percula-watchdog --restart unless-stopped "${args[@]}" percula-watchdog
echo "Started. 'docker logs percula-watchdog' shows when the next check runs."
