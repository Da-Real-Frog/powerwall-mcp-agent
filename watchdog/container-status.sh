#!/bin/sh
# Runs on percula as the forced command of the watchdog's SSH key (see README.md).
# Prints one JSON line per container with its name, state, health, restart count and
# start time, and nothing else. Container settings (which hold the env files' secrets)
# are never printed, and whatever command the caller asked for is ignored.
set -eu

ids=$(docker ps -aq)
if [ -z "$ids" ]; then
  exit 0
fi

# shellcheck disable=SC2086  # one argument per container id
exec docker inspect --format '{"name":{{json .Name}},"status":{{json .State.Status}},"health":{{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}},"restarts":{{.RestartCount}},"started":{{json .State.StartedAt}},"exit_code":{{.State.ExitCode}}}' $ids
