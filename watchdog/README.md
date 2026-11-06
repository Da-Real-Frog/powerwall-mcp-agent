# percula watchdog: a daily check from the garage Pi

Every day at noon, a small container on the garage Pi (`garage-bridge`) asks percula which
containers it is running and in what state, checks that the MCP server and Home Chat answer on
their ports, and sends one ntfy message to the same topic as the server's alerts:

- **percula: all good** (low priority, no sound), with one line per container.
- **percula: all good, with notes** (normal priority) when something is worth knowing but nothing
  is down: a container restarted in the last 24 hours (a deploy or a crash), Docker restarted one
  since the last check, or a container it doesn't watch has failed.
- **percula: N problems** (high priority, with sound) when a watched container is missing,
  stopped or unhealthy, a port doesn't answer, or percula doesn't answer at all.

It runs on the Pi so it still reports when percula is down. The daily message is also the
Pi's own dead-man's switch: **no message at noon means the watchdog itself is in trouble.** For an
alert in that case too, set `HEARTBEAT_URL` (below).

```
garage Pi ── SSH, one locked command ──▶ percula: container-status (docker inspect, names and states only)
    │        HTTP :8080 and :8000    ──▶ Home Chat, MCP server
    └── ntfy ──▶ your phone
```

## Files

- [`watchdog.py`](watchdog.py): the check and the daily schedule. Standard library only.
- [`container-status.sh`](container-status.sh): installed on percula as the only command the
  watchdog's SSH key can run. It prints each container's name, state, health, restart count and
  start time. It never prints container settings, which is where the env files' secrets live.
- [`Dockerfile`](Dockerfile), [`deploy-pi.sh`](deploy-pi.sh): the container and the script that builds
  and starts it on the Pi.
- [`watchdog.env.example`](watchdog.env.example): the settings, copied to `~/watchdog/watchdog.env` on the Pi.

No secret goes in this repo. The ntfy topic lives only in `~/watchdog/watchdog.env` on the Pi
(mode 600), and the SSH private key only in `~/watchdog/ssh/` on the Pi.

## Setup

`<pi-user>` is your account on the Pi, `<percula-user>` your account on percula (the one that
runs the deploys and can use `docker`), and `<percula-ip>` percula's fixed address on the home
network. Use the IP, not `percula.local`: `.local` names don't resolve inside a container. If
percula has no DHCP reservation yet, give it one in the router the same way as the Pi
([bridge step 8](../bridge/README.md#8-give-the-pi-a-fixed-address)).

### 1. Install Docker on the Pi, with its firewall handling off

The Pi is also the TEDAPI bridge, and Docker normally rewrites the firewall when it starts (it
sets the forwarding policy to drop). Turning that off before installing keeps the bridge's rules
exactly as `setup-bridge.sh` left them. The watchdog uses the Pi's own network (`--network host`),
so it doesn't need Docker's networking.

On the Pi:
```
sudo mkdir -p /etc/docker
echo '{ "iptables": false, "ip6tables": false }' | sudo tee /etc/docker/daemon.json
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```
Log out and back in (for the `docker` group), then check `docker run --rm hello-world` works.

Check the bridge still works, from percula:
```
curl -sk -m 5 -o /dev/null -w '%{http_code}\n' https://192.168.91.1/
```
It should still print `200`.

### 2. Make the watchdog's SSH key (on the Pi)

```
mkdir -p ~/watchdog/ssh && chmod 700 ~/watchdog
ssh-keygen -t ed25519 -N '' -C percula-watchdog -f ~/watchdog/ssh/id_ed25519
ssh-keyscan -t ed25519 <percula-ip> > ~/watchdog/ssh/known_hosts
ssh-keygen -lf ~/watchdog/ssh/known_hosts
cat ~/watchdog/ssh/id_ed25519.pub
```
The key has no passphrase because it is used unattended; step 3 limits it to one read-only
command. Compare the fingerprint `ssh-keygen -lf` printed with the one percula prints for
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`: they should match. Keep the last line (the
public key, starting `ssh-ed25519`) for step 3. A public key isn't secret.

### 3. Let that key run only `container-status` (on percula)

From a copy of this repo on percula (`git clone https://github.com/Da-Real-Frog/powewall-mcp-agent.git powerwall-mcp-agent && cd powerwall-mcp-agent` if it has none):
```
sudo install -m 755 watchdog/container-status.sh /usr/local/bin/container-status
container-status
```
`container-status` should print one line per container, such as
`{"name":"/mcp-server","status":"running",...}`.

Then add the key to `~/.ssh/authorized_keys` with the command it is locked to, on one line,
pasting the public key from step 2 at the end:
```
echo 'command="/usr/local/bin/container-status",restrict ssh-ed25519 AAAA... percula-watchdog' >> ~/.ssh/authorized_keys
```
`restrict` turns off shells, port forwarding and everything else, and `command=` runs
`container-status` whatever the caller asks for.

Check from the Pi:
```
ssh -i ~/watchdog/ssh/id_ed25519 -o UserKnownHostsFile=$HOME/watchdog/ssh/known_hosts <percula-user>@<percula-ip>
ssh -i ~/watchdog/ssh/id_ed25519 -o UserKnownHostsFile=$HOME/watchdog/ssh/known_hosts <percula-user>@<percula-ip> ls /
```
Both should print the same container lines and return straight away: the second proves the key
can't run anything else.

### 4. Settings (on the Pi)

```
cp ~/powerwall-mcp-agent/watchdog/watchdog.env.example ~/watchdog/watchdog.env
chmod 600 ~/watchdog/watchdog.env
nano ~/watchdog/watchdog.env
```
Fill in `PERCULA_HOST`, `PERCULA_USER` and `NTFY_URL`. Copy `NTFY_URL` (and `NTFY_TOKEN`, if
any) by hand from `~/mcp-server.env` on percula; don't paste it anywhere else. The rest have
sensible defaults.

(`~/powerwall-mcp-agent` is the clone from [bridge step 7](../bridge/README.md#7-turn-on-forwarding).
Run `git -C ~/powerwall-mcp-agent pull` first so it has this folder.)

### 5. Try it, then start it

```
~/powerwall-mcp-agent/watchdog/deploy-pi.sh --once
```
This builds the image (a few minutes the first time on a Pi 3) and runs one check now. Your
phone should get "percula: all good" with a line per container, and the terminal shows the same
text.

Then start the daily watchdog:
```
~/powerwall-mcp-agent/watchdog/deploy-pi.sh
```
It sends "percula watchdog started" with the time of the next check, and comes back by itself
after a reboot or a power cut (with another "started" message, which is a handy sign the Pi
rebooted).

### Optional: an alert when the Pi goes quiet

The missing noon message only helps if you notice it. For an actual alert, make a free check at
[healthchecks.io](https://healthchecks.io) with a period of 1 day and a grace time of 1 hour, set its
notifications to your phone, and put its ping URL in `HEARTBEAT_URL`. The watchdog pings it after
every check (on the `/fail` endpoint when there is a problem). The ping URL is a secret like the
ntfy topic: it only goes in `watchdog.env`. Then `./deploy-pi.sh` again.

## Day to day

- `docker logs percula-watchdog` on the Pi shows each check and when the next one runs.
- To change a setting, edit `~/watchdog/watchdog.env` and run `deploy-pi.sh` again.
- To update after a change here: `git -C ~/powerwall-mcp-agent pull` and run `deploy-pi.sh` again.
  This isn't automatic: the deploy runner lives on percula, and the point is not to depend on it.
- If a container is added on percula and should be watched, add it to `EXPECTED_CONTAINERS`.
- If you ever revoke it, delete the `command="/usr/local/bin/container-status"` line from
  `~/.ssh/authorized_keys` on percula.

## Development

```
uv sync
uv run ruff check .
uv run pytest -q
```
