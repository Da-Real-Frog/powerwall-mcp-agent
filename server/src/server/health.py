"""Deterministic health checks over the collector's readings, and the alerts they raise.

Every five minutes the collector calls Monitor.run(). Each check returns findings; a new finding opens
an alert and sends an ntfy notification, and an alert closes once its finding has been absent for
CLEAR_AFTER evaluations in a row, so one noisy reading neither opens nor closes anything.
"""
import logging
import os
import statistics
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import Site
from .storage import BUCKET, Store

log = logging.getLogger(__name__)

WINDOW_BUCKETS = 3  # a string fault must hold for 15 minutes of five-minute averages
MIN_PEER_W_PER_PANEL = 20.0  # below this the sun is too weak to judge a string
DEAD_W_PER_PANEL = 2.0
LOW_FRACTION = 0.5  # producing under half of its neighbours, per panel
STALE_SECONDS = 300
CLEAR_AFTER = 3


@dataclass(frozen=True)
class Finding:
    kind: str  # string_dead, string_low, input_missing, strings_stale, grid_down, collector_stale
    subject: str | None  # the input name ("PW2-A"), or None for site-wide findings
    severity: str  # "high" or "default", used as the ntfy priority
    message: str

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.subject or ''}"


def check_strings(site: Site, buckets: list[dict]) -> list[Finding]:
    """Compares each mapped input with the other inputs on the same array, in watts per panel.

    Uses the average over the window, so a passing cloud does not count. An array with only one input
    visible has nothing to compare against and is skipped (its forecast-based check comes later).
    """
    watts: dict[str, list[float]] = {}
    for b in buckets:
        if b["watts"] is not None:
            watts.setdefault(b["input"], []).append(b["watts"])
    per_panel = {}
    for i in site.inputs:
        samples = watts.get(i.key) if i.key else None
        if samples and len(samples) >= WINDOW_BUCKETS:
            per_panel[i.key] = (i, sum(samples) / len(samples) / (i.panels_per_string * i.strings))

    findings = []
    for name, (wired, w) in per_panel.items():
        peers = [pw for other, (oi, pw) in per_panel.items() if other != name and oi.array == wired.array]
        if not peers:
            continue
        reference = statistics.median(peers)
        if reference < MIN_PEER_W_PER_PANEL:
            continue
        where = f"input {name} ({wired.array}, {wired.panels_per_string * wired.strings} panels)"
        if w < DEAD_W_PER_PANEL:
            findings.append(Finding("string_dead", name, "high",
                                    f"{where} is producing nothing while its neighbours make "
                                    f"{reference:.0f} W per panel"))
        elif w < LOW_FRACTION * reference:
            findings.append(Finding("string_low", name, "default",
                                    f"{where} makes {w:.0f} W per panel, under half of its neighbours' "
                                    f"{reference:.0f} W"))
    return findings


def check_inputs_present(site: Site, latest_inputs: set[str]) -> list[Finding]:
    """A mapped input missing from the latest poll usually means its inverter has dropped off."""
    return [
        Finding("input_missing", i.key, "high",
                f"Input {i.key} ({i.array}) is missing from the gateway's readings; its inverter may be offline")
        for i in site.inputs if i.key and i.key not in latest_inputs
    ]


def check_grid(reading: dict | None) -> list[Finding]:
    if reading and reading["grid_status"] and reading["grid_status"] != "UP":
        return [Finding("grid_down", None, "high",
                        f"Grid is {reading['grid_status']}; the home is on battery at "
                        f"{reading['battery_percent']:.0f}%")]
    return []


def check_collector(last_success: int | None, started: int, now: int) -> list[Finding]:
    since = last_success or started
    if now - since > STALE_SECONDS:
        return [Finding("collector_stale", None, "high",
                        f"No reading from the gateway for {(now - since) // 60} minutes")]
    return []


def evaluate(site: Site, store: Store, last_success: int | None, started: int, now: int) -> list[Finding]:
    findings = check_collector(last_success, started, now)
    if findings:
        return findings  # the stored readings are stale, so nothing else can be judged
    current = now // BUCKET * BUCKET  # the bucket still filling is left out
    findings += check_strings(site, store.string_buckets(current - WINDOW_BUCKETS * BUCKET, current))
    latest = store.latest_strings()
    if latest and now - latest[0] > STALE_SECONDS:
        findings.append(Finding("strings_stale", None, "high",
                                f"No string readings for {(now - latest[0]) // 60} minutes; the gateway is answering, "
                                "so the TEDAPI bridge (192.168.91.1) is probably down"))
    elif latest:
        findings += check_inputs_present(site, {r["input"] for r in latest[1]})
    findings += check_grid(store.latest_reading())
    return findings


class Notifier:
    """Sends to ntfy (NTFY_URL is the full topic URL, NTFY_TOKEN optional). Does nothing when unset."""

    def __init__(self, url: str | None, token: str | None = None):
        self.url = url
        self.token = token

    def send(self, title: str, message: str, priority: str = "default", tags: str = "") -> None:
        if not self.url:
            return
        headers = {"Title": title, "Priority": priority}
        if tags:
            headers["Tags"] = tags
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            httpx.post(self.url, content=message.encode(), headers=headers, timeout=10.0).raise_for_status()
        except httpx.HTTPError as e:
            log.warning("ntfy notification failed: %s", e)


class Monitor:
    def __init__(self, site: Site, store: Store, notifier: Notifier, tz: str, heartbeat_url: str | None = None):
        self.site = site
        self.store = store
        self.notifier = notifier
        self.tz = tz
        self.heartbeat_url = heartbeat_url

    def run(self, last_success: int | None, started: int, now: int) -> list[Finding]:
        findings = evaluate(self.site, self.store, last_success, started, now)
        self.apply(findings, now)
        self._heartbeat(failed=any(f.kind == "collector_stale" for f in findings))
        return findings

    def apply(self, findings: list[Finding], now: int) -> None:
        """Opens alerts for new findings, refreshes ongoing ones, and resolves those gone long enough."""
        current = {f.key: f for f in findings}
        open_by_key = {a["key"]: a for a in self.store.open_alerts()}
        for key, f in current.items():
            if key in open_by_key:
                self.store.touch_alert(open_by_key[key]["id"], f.message, now)
            else:
                self.store.open_alert(key, f.kind, f.subject, f.message, now)
                self.notifier.send(f"Solar alert: {f.kind.replace('_', ' ')}", f.message, f.severity, "warning")
        for key, alert in open_by_key.items():
            if key in current:
                continue
            if self.store.miss_alert(alert["id"]) >= CLEAR_AFTER:
                self.store.resolve_alert(alert["id"], now)
                since = datetime.fromtimestamp(alert["opened_at"], ZoneInfo(self.tz)).strftime("%a %H:%M")
                self.notifier.send(f"Resolved: {alert['kind'].replace('_', ' ')}",
                                   f"{alert['message']} (open since {since})", "low", "white_check_mark")

    def _heartbeat(self, failed: bool) -> None:
        """Pings a dead-man service (e.g. healthchecks.io) so silence from this server raises its own alert."""
        if not self.heartbeat_url:
            return
        try:
            httpx.get(self.heartbeat_url + ("/fail" if failed else ""), timeout=10.0)
        except httpx.HTTPError as e:
            log.warning("Heartbeat ping failed: %s", e)


def notifier_from_env() -> Notifier:
    return Notifier(os.getenv("NTFY_URL", "").strip() or None, os.getenv("NTFY_TOKEN", "").strip() or None)
