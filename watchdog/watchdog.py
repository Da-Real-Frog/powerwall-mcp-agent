"""Daily health check of percula's containers, run from the garage Pi.

Once a day (CHECK_AT, noon by default) it asks percula over SSH for the state of its containers,
checks that the expected ones are running and healthy and that their web ports answer, and sends
one ntfy message: "all good" at low priority, or the problems at high priority. Because it runs
off percula, it still reports when percula itself is down, and the daily message doubles as a
dead-man's switch for the Pi: no message at noon means the watchdog is the one in trouble.

Settings come from environment variables (see watchdog.env.example). Only the standard library
is used, so the container image stays small on a Raspberry Pi.

Usage: python watchdog.py          wait for CHECK_AT every day, forever
       python watchdog.py --once   check now, send the message, and exit
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("watchdog")

SSH_TIMEOUT = 30  # seconds for the whole SSH call
HTTP_TIMEOUT = 10


@dataclass
class Config:
    host: str
    user: str
    ntfy_url: str
    ntfy_token: str | None = None
    expected: list[str] = field(default_factory=lambda: ["mcp-server", "home-chat"])
    http_checks: list[str] = field(default_factory=list)
    check_at: str = "12:00"
    tz: str = "America/Chicago"
    ssh_key: str = "/ssh/id_ed25519"
    known_hosts: str = "/ssh/known_hosts"
    state_file: str | None = "/data/state.json"
    heartbeat_url: str | None = None


def _list(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


def config_from_env(env: dict[str, str] | None = None) -> Config:
    env = dict(os.environ if env is None else env)
    missing = [k for k in ("PERCULA_HOST", "PERCULA_USER", "NTFY_URL") if not env.get(k, "").strip()]
    if missing:
        raise SystemExit(f"Missing settings: {', '.join(missing)} (see watchdog.env.example)")
    host = env["PERCULA_HOST"].strip()
    checks = env.get("HTTP_CHECKS", "").strip()
    if checks.lower() == "none":
        http_checks = []
    elif checks:
        http_checks = _list(checks)
    else:
        # The ports the deploy workflows publish: Home Chat on 8080, the MCP server on 8000
        http_checks = [f"http://{host}:8080/", f"http://{host}:8000/mcp"]
    return Config(
        host=host,
        user=env["PERCULA_USER"].strip(),
        ntfy_url=env["NTFY_URL"].strip(),
        ntfy_token=env.get("NTFY_TOKEN", "").strip() or None,
        expected=_list(env.get("EXPECTED_CONTAINERS", "mcp-server,home-chat")),
        http_checks=http_checks,
        check_at=env.get("CHECK_AT", "12:00").strip(),
        tz=env.get("TZ", "America/Chicago").strip(),
        ssh_key=env.get("SSH_KEY", "/ssh/id_ed25519").strip(),
        known_hosts=env.get("KNOWN_HOSTS", "/ssh/known_hosts").strip(),
        state_file=env.get("STATE_FILE", "/data/state.json").strip() or None,
        heartbeat_url=env.get("HEARTBEAT_URL", "").strip() or None,
    )


# ---------------------------------------------------------------- reading percula


class Unreachable(Exception):
    """percula didn't answer over SSH, or answered with something that isn't container states."""


def fetch_containers(cfg: Config) -> list[dict]:
    """Runs the SSH key's forced command on percula and parses its one-JSON-object-per-line output."""
    cmd = [
        "ssh", "-i", cfg.ssh_key,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={cfg.known_hosts}",
        "-o", "ConnectTimeout=15",
        f"{cfg.user}@{cfg.host}",
        "container-status",  # ignored by the forced command; here for readable logs
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=SSH_TIMEOUT)
    except subprocess.TimeoutExpired as e:
        raise Unreachable(f"no answer over SSH within {SSH_TIMEOUT} seconds") from e
    if out.returncode != 0:
        lines = [ln for ln in out.stderr.strip().splitlines() if ln.strip()]
        raise Unreachable(lines[-1] if lines else f"SSH exited with code {out.returncode}")
    return parse_containers(out.stdout)


def parse_containers(text: str) -> list[dict]:
    containers = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError as e:
            raise Unreachable(f"unexpected answer from percula: {line[:80]}") from e
        c["name"] = str(c.get("name", "")).lstrip("/")
        containers.append(c)
    return containers


def probe(url: str) -> str | None:
    """Returns None when the URL answers (any status below 500), otherwise what went wrong."""
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT):
            return None
    except urllib.error.HTTPError as e:
        return None if e.code < 500 else f"answers with HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return f"doesn't answer ({reason})"


# ---------------------------------------------------------------- deciding


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)  # something is down: high priority
    notes: list[str] = field(default_factory=list)  # worth knowing, nothing down: default priority
    lines: list[str] = field(default_factory=list)  # one line per container, always shown

    @property
    def priority(self) -> str:
        return "high" if self.problems else "default" if self.notes else "low"

    @property
    def title(self) -> str:
        if self.problems:
            n = len(self.problems)
            return f"percula: {n} problem{'s' if n > 1 else ''}"
        return "percula: all good" + (", with notes" if self.notes else "")

    @property
    def tags(self) -> str:
        return "rotating_light" if self.problems else "warning" if self.notes else "white_check_mark"

    def body(self) -> str:
        parts = []
        if self.problems:
            parts.append("\n".join(f"✗ {p}" for p in self.problems))
        if self.notes:
            parts.append("\n".join(f"• {n}" for n in self.notes))
        if self.lines:
            parts.append("\n".join(self.lines))
        return "\n\n".join(parts) or "No containers found."


def parse_started(value: str | None) -> datetime | None:
    """Docker's StartedAt, e.g. 2026-11-05T17:02:11.123456789Z (nanoseconds, which Python can't parse)."""
    if not value or value.startswith("0001-"):
        return None
    try:
        whole_seconds = value.rstrip("Z").partition(".")[0]
        return datetime.fromisoformat(whole_seconds).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def describe_age(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    if hours >= 48:
        return f"{hours // 24} days"
    if hours >= 1:
        return f"{hours} hour{'s' if hours > 1 else ''}"
    return f"{max(int(delta.total_seconds() // 60), 0)} minutes"


def evaluate(containers: list[dict], expected: list[str], now: datetime, tz: ZoneInfo,
             previous_restarts: dict[str, int] | None = None) -> Report:
    report = Report()
    previous_restarts = previous_restarts or {}
    by_name = {c["name"]: c for c in containers}

    for name in expected:
        if name not in by_name:
            report.problems.append(f"{name} doesn't exist on percula")
    order = {n: i for i, n in enumerate(expected)}
    for name in sorted(by_name, key=lambda n: (order.get(n, len(order)), n)):
        c = by_name[name]
        status, health = c.get("status"), c.get("health")
        started = parse_started(c.get("started"))
        is_expected = name in expected

        line = f"{name}: {status}"
        if health:
            line += f", {health}"
        if status == "running" and started:
            line += f", up {describe_age(now - started)}"
        if status == "exited":
            line += f" (exit code {c.get('exit_code')})"
        report.lines.append(("" if is_expected else "(other) ") + line)

        if is_expected:
            if status != "running":
                report.problems.append(f"{name} is {status}" +
                                       (f" (exit code {c.get('exit_code')})" if status == "exited" else ""))
            elif health == "unhealthy":
                report.problems.append(f"{name} is running but unhealthy")
            elif started and now - started < timedelta(hours=24):
                local = started.astimezone(tz).strftime("%a %H:%M")
                report.notes.append(f"{name} (re)started {local}, a deploy or a crash")
        elif status in ("restarting", "dead") or (status == "exited" and c.get("exit_code") not in (0, None)):
            report.notes.append(f"{name} (not watched) is {status}")

        restarts = int(c.get("restarts") or 0)
        before = previous_restarts.get(name)
        if is_expected and before is not None and restarts > before:
            n = restarts - before
            report.notes.append(f"Docker restarted {name} {n} time{'s' if n > 1 else ''} since the last check")
    return report


# ---------------------------------------------------------------- sending


def send_ntfy(cfg: Config, title: str, body: str, priority: str, tags: str) -> bool:
    headers = {"Title": title, "Priority": priority, "Tags": tags}
    if cfg.ntfy_token:
        headers["Authorization"] = f"Bearer {cfg.ntfy_token}"
    for attempt in range(3):
        try:
            req = urllib.request.Request(cfg.ntfy_url, data=body.encode(), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT):
                return True
        except (urllib.error.URLError, OSError) as e:
            # Never log the URL: the topic name is the secret
            log.warning("ntfy send failed (attempt %d): %s", attempt + 1, getattr(e, "reason", e))
            time.sleep(5 * (attempt + 1))
    return False


def ping_heartbeat(cfg: Config, failed: bool) -> None:
    if not cfg.heartbeat_url:
        return
    try:
        with urllib.request.urlopen(cfg.heartbeat_url + ("/fail" if failed else ""), timeout=HTTP_TIMEOUT):
            pass
    except (urllib.error.URLError, OSError) as e:
        log.warning("heartbeat ping failed: %s", getattr(e, "reason", e))


def load_state(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: str | None, state: dict) -> None:
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, path)
    except OSError as e:
        log.warning("couldn't save state: %s", e)


def run_check(cfg: Config, now: datetime | None = None) -> Report:
    tz = ZoneInfo(cfg.tz)
    now = now or datetime.now(timezone.utc)
    state = load_state(cfg.state_file)
    try:
        containers = fetch_containers(cfg)
    except Unreachable as e:
        report = Report(problems=[f"Can't reach percula ({str(e).rstrip('.')}). It may be down, off the "
                                  "network, or its SSH may have stopped."])
    else:
        report = evaluate(containers, cfg.expected, now, tz, state.get("restarts"))
        state["restarts"] = {c["name"]: int(c.get("restarts") or 0) for c in containers}
        for url in cfg.http_checks:
            err = probe(url)
            if err:
                report.problems.append(f"{url} {err}")
    state["last_check"] = now.isoformat()
    save_state(cfg.state_file, state)
    log.info("%s\n%s", report.title, report.body())
    send_ntfy(cfg, report.title, report.body(), report.priority, report.tags)
    ping_heartbeat(cfg, failed=bool(report.problems))
    return report


# ---------------------------------------------------------------- scheduling


def next_run(now: datetime, check_at: str, tz: ZoneInfo) -> datetime:
    hour, minute = (int(x) for x in check_at.split(":"))
    local = now.astimezone(tz)
    target = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= local:
        target = (local + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return target


def wait_until(target: datetime) -> None:
    # Short sleeps, re-reading the clock each time: the Pi has no battery clock, so its time can
    # jump when it syncs after a power cut, and a single long sleep would miss the check.
    while (remaining := (target - datetime.now(timezone.utc)).total_seconds()) > 0:
        time.sleep(min(remaining, 300))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    argv = sys.argv[1:] if argv is None else argv
    cfg = config_from_env()
    tz = ZoneInfo(cfg.tz)
    if "--once" in argv:
        report = run_check(cfg)
        return 1 if report.problems else 0

    first = next_run(datetime.now(timezone.utc), cfg.check_at, tz)
    send_ntfy(cfg, "percula watchdog started",
              f"Watching {', '.join(cfg.expected)}. Next check {first.strftime('%a %H:%M')}.", "low", "eyes")
    while True:
        target = next_run(datetime.now(timezone.utc), cfg.check_at, tz)
        log.info("next check at %s", target.isoformat())
        wait_until(target)
        try:
            run_check(cfg)
        except Exception:  # keep the daily loop alive whatever happens
            log.exception("check failed")
            send_ntfy(cfg, "percula watchdog error", "The daily check crashed; see `docker logs percula-watchdog`"
                      " on the Pi.", "high", "rotating_light")
        time.sleep(61)  # step past the check minute


if __name__ == "__main__":
    sys.exit(main())
