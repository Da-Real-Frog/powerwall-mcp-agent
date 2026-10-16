from datetime import datetime, timedelta, timezone

import pytest

from server import reserve
from server.config import ReserveSettings, load_site
from server.reserve import Controller, Inputs, decide
from server.storage import Store

S = ReserveSettings()
NOW = 1_800_000_000
TZ = "America/Chicago"


def inputs(**kw) -> Inputs:
    base = {"grid_status": "UP", "current_reserve": 20.0, "tomorrow_solar_kwh": 60.0, "daytime_use_kwh": 15.0}
    return Inputs(**{**base, **kw})


@pytest.mark.parametrize("kw, rule, target", [
    ({"grid_status": "DOWN"}, 1, None),
    ({"grid_status": "DOWN", "warnings": ["Tornado Warning"]}, 1, None),
    ({"warnings": ["Tornado Warning"], "watches": ["Tornado Watch"]}, 2, 100),
    ({"watches": ["Winter Storm Watch"]}, 3, 80),
    ({"tomorrow_solar_kwh": 10.0}, 4, 60),
    ({"tomorrow_solar_kwh": 30.0}, 5, 40),
    ({"tomorrow_solar_kwh": 42.0}, 6, 20),
    ({"tomorrow_solar_kwh": None}, 0, None),
    ({"errors": ["NWS alerts: timeout"]}, 0, None),
])
def test_rules_in_order(kw, rule, target):
    decision = decide(inputs(**kw), S)
    assert (decision.rule, decision.target) == (rule, target)


def test_only_severe_events_count_and_warnings_are_held():
    ends = datetime.fromtimestamp(NOW + 3600, timezone.utc).isoformat()
    alerts = [{"event": "Severe Thunderstorm Warning", "ends": ends}, {"event": "Tornado Watch"},
              {"event": "Dense Fog Advisory"}, {"event": "Flood Warning"}]
    hold: dict[str, int] = {}
    warnings, watches = reserve.classify_alerts(alerts, hold, NOW, 6)
    assert warnings == ["Severe Thunderstorm Warning"] and watches == ["Tornado Watch"]

    # Gone from the feed, but still within 6 hours of its end
    assert reserve.classify_alerts([], hold, NOW + 3600 + 5 * 3600, 6) == (["Severe Thunderstorm Warning"], [])
    assert reserve.classify_alerts([], hold, NOW + 3600 + 6 * 3600 + 1, 6) == ([], [])
    assert hold == {}


def test_wind_advisory_counts_as_a_watch():
    alerts = [{"event": "Wind Advisory"}, {"event": "Lake Wind Advisory"}]
    assert reserve.classify_alerts(alerts, {}, NOW, 6) == ([], ["Wind Advisory"])


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, level):
        self.calls.append(level)

    def send(self, title, message, priority="default", tags=""):
        self.calls.append(title)


def controller(tmp_path, live=False):
    writer, notifier = Recorder(), Recorder()
    c = Controller(Store(str(tmp_path / "pw.db")), S, live=live, tz=TZ, writer=writer, notifier=notifier)
    return c, writer, notifier


def test_dry_run_announces_once_and_never_writes(tmp_path):
    c, writer, notifier = controller(tmp_path)
    storm = inputs(warnings=["Tornado Warning"])
    assert c.step(storm, NOW)["action"] == "would_set"
    assert c.step(storm, NOW + 900)["action"] == "would_set"
    assert writer.calls == []
    assert notifier.calls == ["Would set reserve to 100%"]
    assert len(c.store.reserve_decisions(0)) == 1  # repeats are folded


def test_live_writes_then_waits_an_hour(tmp_path):
    c, writer, _ = controller(tmp_path, live=True)
    assert c.step(inputs(watches=["Tornado Watch"]), NOW)["action"] == "set"
    assert writer.calls == [80]
    # Half an hour later the warning arrives, but the last change was too recent
    assert c.step(inputs(current_reserve=80.0, warnings=["Tornado Warning"]), NOW + 1800)["action"] == "waiting"
    assert c.step(inputs(current_reserve=80.0, warnings=["Tornado Warning"]), NOW + 3600)["action"] == "set"
    assert writer.calls == [80, 100]


def test_small_steps_are_skipped(tmp_path):
    c, writer, _ = controller(tmp_path, live=True)
    assert c.step(inputs(current_reserve=23.0), NOW)["action"] == "none"
    assert writer.calls == []


def test_manual_change_pauses_for_a_day(tmp_path):
    c, writer, _ = controller(tmp_path, live=True)
    c.step(inputs(current_reserve=20.0), NOW)  # at target, nothing to do
    # Someone sets 50% in the Tesla app; the forecast still says 20%
    assert c.step(inputs(current_reserve=50.0), NOW + 900)["action"] == "paused"
    assert c.step(inputs(current_reserve=50.0), NOW + 23 * 3600)["action"] == "paused"
    assert c.step(inputs(current_reserve=50.0), NOW + 25 * 3600)["action"] == "set"
    assert writer.calls == [20]


def test_own_change_is_not_mistaken_for_a_manual_one(tmp_path):
    c, writer, _ = controller(tmp_path, live=True)
    c.step(inputs(tomorrow_solar_kwh=10.0), NOW)  # sets 60
    assert c.step(inputs(current_reserve=60.0, tomorrow_solar_kwh=10.0), NOW + 900)["action"] == "none"


def test_failed_write_is_recorded(tmp_path):
    def broken(level):
        raise RuntimeError("403")

    c = Controller(Store(str(tmp_path / "pw.db")), S, live=True, tz=TZ, writer=broken)
    result = c.step(inputs(tomorrow_solar_kwh=10.0), NOW)
    assert result["action"] == "failed" and "403" in result["note"]
    assert c.store.reserve_state().get("last_change") is None  # can retry on the next run


def test_writer_converts_to_the_gateway_scale_and_falls_back_to_cloud():
    class Gateway:
        def __init__(self, result):
            self.result, self.levels = result, []

        def set_reserve(self, level):
            self.levels.append(level)
            return self.result

    local, cloud = Gateway({"ok": 1}), Gateway({"ok": 1})
    reserve.make_writer(lambda: local, lambda: cloud)(20)
    assert local.levels == [24.0] and cloud.levels == []

    local, cloud = Gateway(None), Gateway({"ok": 1})
    reserve.make_writer(lambda: local, lambda: cloud)(20)
    assert cloud.levels == [20]

    with pytest.raises(RuntimeError, match="local: .*; cloud: "):
        reserve.make_writer(lambda: Gateway(None), lambda: Gateway(None))(20)


def test_home_use_from_two_weeks_of_history(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    status = {"battery_level_percent": 50.0, "grid_status": "UP", "grid_power_watts": 0.0,
              "solar_power_watts": 0.0, "battery_power_watts": 0.0}
    from zoneinfo import ZoneInfo
    zone = ZoneInfo(TZ)
    today = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    start = int((today - timedelta(days=14)).timestamp())
    end = int(today.timestamp())
    assert reserve.home_use(store, end + 3600, TZ) is None
    for ts in range(start, end, 300):
        hour = datetime.fromtimestamp(ts, zone).hour
        store.add(ts, {**status, "home_usage_watts": 1000.0 if 7 <= hour < 19 else 500.0}, {})
    store.rollup(0)
    day, night = reserve.home_use(store, end + 3600, TZ)
    assert day == pytest.approx(12.0) and night == pytest.approx(6.0)


def test_site_toml_reserve_settings():
    assert load_site().reserve == ReserveSettings()
