"""Backup reserve controller: picks the reserve with fixed, ordered rules.

The first rule that matches wins (see RULES in the README). It runs in dry-run by default: it records
and announces what it would set without touching the Powerwall. RESERVE_MODE=live lets it write.
"""
import json
import logging
import statistics
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import ReserveSettings
from .storage import BUCKET, Store

log = logging.getLogger(__name__)

SEVERE_EVENTS = (
    "Tornado", "Severe Thunderstorm", "Hurricane", "Tropical Storm", "Winter Storm", "Ice Storm", "Blizzard",
    "Extreme Cold", "Extreme Heat", "Excessive Heat", "High Wind",
)
# Advisories that count as a watch: a Wind Advisory is what usually sits behind a CPS Energy high wind alert
WATCH_ADVISORIES = ("Wind Advisory",)
DAY_START_HOUR = 7  # "daytime use" is home use from 07:00 to 19:00 local time
DAY_END_HOUR = 19
HISTORY_DAYS = 14
MIN_COVERAGE = 0.9


@dataclass
class Inputs:
    """Everything a decision depends on, so each one can be explained and replayed."""
    grid_status: str | None
    current_reserve: float | None
    warnings: list[str] = field(default_factory=list)  # severe NWS warnings in effect or within the hold
    watches: list[str] = field(default_factory=list)
    tomorrow_solar_kwh: float | None = None
    daytime_use_kwh: float = 0.0
    overnight_use_kwh: float = 0.0
    use_source: str = "estimate"  # "history" once HISTORY_DAYS of readings exist
    errors: list[str] = field(default_factory=list)  # inputs that could not be fetched


@dataclass
class Decision:
    rule: int
    target: float | None  # None means leave the reserve as it is
    reason: str


def decide(inputs: Inputs, settings: ReserveSettings) -> Decision:
    """The ordered rules. Pure: same inputs, same decision."""
    if inputs.grid_status and inputs.grid_status != "UP":
        return Decision(1, None, f"Grid is {inputs.grid_status}: the Powerwalls are running the house, leave it")
    if inputs.errors:
        # Missing weather data could hide a storm, so never lower the reserve on partial information
        return Decision(0, None, f"Inputs unavailable ({'; '.join(inputs.errors)}), so no change")
    if inputs.warnings:
        return Decision(2, settings.warning, f"Severe weather warning: {', '.join(inputs.warnings)}")
    if inputs.watches:
        return Decision(3, settings.watch, f"Severe weather watch: {', '.join(inputs.watches)}")
    if inputs.tomorrow_solar_kwh is None:
        return Decision(0, None, "No solar forecast for tomorrow, so no change")
    solar, day = inputs.tomorrow_solar_kwh, inputs.daytime_use_kwh
    if solar < day:
        return Decision(4, settings.low_solar,
                        f"Tomorrow's solar ({solar:.1f} kWh) is below daytime use ({day:.1f} kWh): "
                        "the battery will not refill")
    if solar < day + settings.battery_kwh:
        return Decision(5, settings.partial,
                        f"Tomorrow's solar ({solar:.1f} kWh) covers daytime use ({day:.1f} kWh) but not a full "
                        f"recharge ({settings.battery_kwh:.0f} kWh)")
    return Decision(6, settings.default,
                    f"Tomorrow's solar ({solar:.1f} kWh) covers daytime use and a full recharge")


def classify_alerts(alerts: list[dict], hold_until: dict[str, int], now: int, hold_hours: float):
    """Splits NWS alerts into severe warnings and watches (a Wind Advisory counts as a watch).

    A warning keeps counting for `hold_hours` after it ends: `hold_until` maps each warning event to the
    time its hold runs out, and is updated in place so the hold survives the alert leaving the feed.
    """
    warnings, watches = set(), set()
    for a in alerts:
        event = a.get("event") or ""
        if event in WATCH_ADVISORIES:
            watches.add(event)
            continue
        if not any(event.startswith(name) for name in SEVERE_EVENTS):
            continue
        if event.endswith("Warning"):
            ends = _parse_time(a.get("ends")) or now
            hold_until[event] = max(hold_until.get(event, 0), ends + int(hold_hours * 3600))
        elif event.endswith("Watch"):
            watches.add(event)
    for event, until in list(hold_until.items()):
        if until > now:
            warnings.add(event)
        else:
            del hold_until[event]
    return sorted(warnings), sorted(watches)


def home_use(store: Store, now: int, tz: str) -> tuple[float, float] | None:
    """Median daytime and overnight home use (kWh) over the last HISTORY_DAYS full days.

    None until every one of those days has at least MIN_COVERAGE of its five-minute averages.
    """
    zone = ZoneInfo(tz)
    today = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    days, nights = [], []
    for back in range(1, HISTORY_DAYS + 1):
        start = today - timedelta(days=back)
        t0 = int(start.replace(tzinfo=zone).timestamp())
        t1 = int((start + timedelta(days=1)).replace(tzinfo=zone).timestamp())
        buckets = store.energy_buckets(t0, t1)
        if len(buckets) * BUCKET < MIN_COVERAGE * (t1 - t0):
            return None
        day = night = 0.0
        for b in buckets:
            kwh = (b["home_w"] or 0) * BUCKET / 3600 / 1000
            hour = datetime.fromtimestamp(b["bucket"], zone).hour
            if DAY_START_HOUR <= hour < DAY_END_HOUR:
                day += kwh
            else:
                night += kwh
        days.append(day)
        nights.append(night)
    return statistics.median(days), statistics.median(nights)


def tomorrow_solar(forecast: dict, now: int, tz: str) -> float | None:
    tomorrow = (datetime.fromtimestamp(now, ZoneInfo(tz)) + timedelta(days=1)).date().isoformat()
    # daily_kwh covers the 48 hours from now, so tomorrow is always complete in it
    return forecast.get("daily_kwh", {}).get(tomorrow)


def _parse_time(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return None


class Controller:
    """Applies the decision with the safeguards: at most one change per min_interval, only steps of at
    least min_change points, and a pause after a change someone made in the Tesla app."""

    def __init__(self, store: Store, settings: ReserveSettings, live: bool, tz: str, writer=None, notifier=None):
        self.store = store
        self.settings = settings
        self.live = live
        self.tz = tz
        self.writer = writer  # callable(level) -> None, used only in live mode
        self.notifier = notifier

    def step(self, inputs: Inputs, now: int) -> dict:
        state = self.store.reserve_state()
        decision = decide(inputs, self.settings)
        outcome = self._outcome(decision, inputs, state, now)

        if inputs.current_reserve is not None:
            state["last_observed"] = inputs.current_reserve
        if outcome["action"] == "set":
            try:
                self.writer(decision.target)
                state["last_change"] = now
                state["last_written"] = decision.target
                state["last_observed"] = decision.target
            except Exception as e:
                log.warning("Setting the reserve failed: %s", e)
                outcome = {"action": "failed", "note": f"Setting the reserve failed: {e}"}
        self.store.save_reserve_state(state)

        previous = self.store.last_reserve_decision()
        self.store.log_reserve_decision(now, decision.rule, decision.target, inputs.current_reserve,
                                        outcome["action"], "live" if self.live else "dry-run",
                                        json.dumps({"reason": decision.reason, "note": outcome["note"],
                                                    "inputs": asdict(inputs)}))
        if self.notifier and outcome["action"] in ("set", "would_set") and (
                not previous or previous["target"] != decision.target or previous["action"] != outcome["action"]):
            verb = "Set" if outcome["action"] == "set" else "Would set"
            self.notifier.send(f"{verb} reserve to {decision.target:.0f}%",
                               f"Rule {decision.rule}: {decision.reason}", "low", "battery")
        return {"decision": asdict(decision), **outcome}

    def preview(self, inputs: Inputs, now: int) -> dict:
        """What step() would do with these inputs, without saving, writing or notifying."""
        decision = decide(inputs, self.settings)
        return {"decision": asdict(decision), **self._outcome(decision, inputs, self.store.reserve_state(), now)}

    def _outcome(self, decision: Decision, inputs: Inputs, state: dict, now: int) -> dict:
        s = self.settings
        current = inputs.current_reserve
        if decision.target is None:
            return {"action": "none", "note": "Rule leaves the reserve unchanged"}
        if current is None:
            return {"action": "none", "note": "Could not read the current reserve"}

        # A change we did not make since the last look means someone changed it in the Tesla app
        observed, written = state.get("last_observed"), state.get("last_written")
        if observed is not None and abs(current - observed) >= 1 and (written is None or abs(current - written) >= 1):
            state["paused_until"] = now + int(s.manual_pause_hours * 3600)
        if state.get("paused_until", 0) > now:
            until = datetime.fromtimestamp(state["paused_until"], ZoneInfo(self.tz)).strftime("%a %H:%M")
            return {"action": "paused", "note": f"Reserve was changed by hand; paused until {until}"}

        if abs(decision.target - current) < s.min_change:
            return {"action": "none", "note": f"Already at {current:.0f}%, within {s.min_change:.0f} points"}
        if now - state.get("last_change", 0) < s.min_interval_minutes * 60:
            return {"action": "waiting", "note": f"Changed less than {s.min_interval_minutes:.0f} minutes ago"}
        if not self.live:
            return {"action": "would_set", "note": f"Dry-run: would change {current:.0f}% to {decision.target:.0f}%"}
        return {"action": "set", "note": f"Changing {current:.0f}% to {decision.target:.0f}%"}


def gather(get_pw, store: Store, settings: ReserveSettings, get_alerts, get_forecast, now: int, tz: str) -> Inputs:
    """Collects the decision inputs. `get_pw()` returns the gateway connection; `get_alerts()` and
    `get_forecast()` are the NWS and Open-Meteo calls."""
    reading = store.latest_reading()
    inputs = Inputs(grid_status=reading["grid_status"] if reading else None, current_reserve=None,
                    daytime_use_kwh=settings.daytime_use_kwh, overnight_use_kwh=settings.overnight_use_kwh)
    try:
        inputs.current_reserve = get_pw().get_reserve(scale=True)
    except Exception as e:
        log.warning("Reading the reserve failed: %s", e)

    state = store.reserve_state()
    hold = state.get("hold_until", {})
    try:
        inputs.warnings, inputs.watches = classify_alerts(get_alerts(), hold, now, settings.warning_hold_hours)
    except Exception as e:
        inputs.errors.append(f"NWS alerts: {e}")
        inputs.warnings = sorted(event for event, until in hold.items() if until > now)
    state["hold_until"] = hold
    store.save_reserve_state(state)

    try:
        inputs.tomorrow_solar_kwh = tomorrow_solar(get_forecast(), now, tz)
    except Exception as e:
        inputs.errors.append(f"solar forecast: {e}")

    use = home_use(store, now, tz)
    if use:
        inputs.daytime_use_kwh, inputs.overnight_use_kwh = round(use[0], 1), round(use[1], 1)
        inputs.use_source = "history"
    return inputs


def make_writer(get_local, get_cloud):
    """Writes through the local gateway first and falls back to Tesla's cloud.

    The local /api/operation takes the gateway's raw scale (app % * 0.95 + 5); the cloud takes the app's.
    `get_local()` and `get_cloud()` return pypowerwall connections.
    """
    def write(level: float) -> None:
        try:
            if get_local().set_reserve(round(level * 0.95 + 5, 1)) is not None:
                return
            local_error = "the gateway refused the change"
        except Exception as e:
            local_error = str(e)
        log.warning("Local reserve write failed (%s); trying the cloud", local_error)
        try:
            if get_cloud().set_reserve(level) is not None:
                return
            cloud_error = "the cloud refused the change"
        except Exception as e:
            cloud_error = str(e)
        raise RuntimeError(f"local: {local_error}; cloud: {cloud_error}")
    return write
