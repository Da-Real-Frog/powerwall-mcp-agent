from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from server import history
from server.collector import Collector
from server.config import load_site
from server.storage import Store

TZ = "America/Chicago"
STATUS = {"battery_level_percent": 80.0, "grid_status": "UP", "grid_power_watts": -1000.0,
          "solar_power_watts": 4000.0, "home_usage_watts": 1000.0, "battery_power_watts": -2000.0}
STRINGS = {"A": {"Voltage": 204.0, "Current": 0.8, "Power": 190.0},
           "C": {"Voltage": -4.3, "Current": 0, "Power": 0}}


# Yesterday at noon: recent enough that rollup() keeps the raw rows
DAY = datetime.now(ZoneInfo(TZ)).date() - timedelta(days=1)


def noon() -> int:
    return int(datetime(DAY.year, DAY.month, DAY.day, 12, tzinfo=ZoneInfo(TZ)).timestamp())


def filled_store(tmp_path, start: int, hours: float) -> Store:
    store = Store(str(tmp_path / "pw.db"))
    for ts in range(start, start + int(hours * 3600), 30):
        store.add(ts, STATUS, STRINGS)
    store.rollup(0)
    return store


def test_latest_strings_are_mapped_to_the_site_layout(tmp_path):
    store = filled_store(tmp_path, noon(), 0.1)
    result = history.string_status(load_site(), store.latest_strings(), noon() + 400, TZ)

    a, c = result["inputs"]
    assert a["array"] == "MP3" and a["watts_per_panel"] == 31.7
    assert c["unused"] and "array" not in c
    assert result["unmapped_inputs"] == []
    assert result["age_seconds"] == 400 - 330


def test_history_averages_into_steps(tmp_path):
    store = filled_store(tmp_path, noon(), 1)
    result = history.history(store, hours=2, step_minutes=15, now=noon() + 3600, tz=TZ)

    assert result["step_minutes"] == 15
    assert len(result["power"]) == 4
    assert result["power"][0]["grid_w"] == -1000.0
    assert result["power"][0]["battery_w"] == -2000.0
    assert [p["watts"] for p in result["strings"]["A"]] == [190.0] * 4


def test_energy_summary_splits_import_from_export(tmp_path):
    store = filled_store(tmp_path, noon(), 2)
    day = history.energy_summary(store, days=1, now=noon() + 2 * 3600, tz=TZ)["days"][0]

    assert day["date"] == DAY.isoformat()
    assert day["solar_kwh"] == 8.0
    assert day["grid_export_kwh"] == 2.0 and day["grid_import_kwh"] == 0
    assert day["battery_charge_kwh"] == 4.0 and day["battery_discharge_kwh"] == 0
    assert day["grid_down_minutes"] == 0
    assert 0 < day["coverage"] < 1


def test_grid_outage_is_counted(tmp_path):
    store = Store(str(tmp_path / "pw.db"))
    for ts in range(noon(), noon() + 600, 30):
        store.add(ts, {**STATUS, "grid_status": "DOWN"}, {})
    store.rollup(0)
    day = history.energy_summary(store, days=1, now=noon() + 600, tz=TZ)["days"][0]
    assert day["grid_down_minutes"] == 10


class FakePowerwall:
    def level(self, scale=False):
        return 80.0

    def grid_status(self):
        return "UP"

    def grid(self):
        return -1000.0

    def solar(self):
        return 4000.0

    def home(self):
        return 1000.0

    def battery(self):
        return -2000.0

    def strings(self):
        return STRINGS


def test_collector_saves_a_poll(tmp_path):
    collector = Collector(Store(str(tmp_path / "pw.db")), interval=30)
    collector.poll_once(FakePowerwall())

    ts, rows = collector.store.latest_strings()
    assert ts == collector.last_success
    assert {r["input"] for r in rows} == {"A", "C"}
    assert collector.store.power_history(0, 300)  # rolled up on the first poll
