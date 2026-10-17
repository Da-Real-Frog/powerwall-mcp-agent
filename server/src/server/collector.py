"""Background poller: reads the gateway every PW_POLL_SECONDS and saves the readings to SQLite."""
import logging
import os
import threading
import time

from . import ercot, forecast, health, powerwall, reserve, weather
from .config import Site, load_site, location, timezone
from .storage import BUCKET, Store

log = logging.getLogger(__name__)

ROLLUP_EVERY = 300  # seconds between recomputing the five-minute averages
CHECK_EVERY = 300  # seconds between health checks
RESERVE_EVERY = 900  # seconds between reserve decisions


def db_path() -> str:
    return os.getenv("PW_DB_PATH") or os.path.join(os.getenv("PW_CACHE_DIR", "."), "powerwall.db")


class Collector:
    def __init__(self, store: Store, interval: float, monitor: health.Monitor | None = None,
                 controller: reserve.Controller | None = None, site: Site | None = None):
        self.store = store
        self.site = site or load_site()
        self.interval = interval
        self.monitor = monitor
        self.controller = controller
        self._last_reserve = 0.0
        self.started = int(time.time())
        self.last_success: int | None = None
        self.last_error: str | None = None
        self.strings_error: str | None = None
        self._last_rollup = 0.0
        self._last_check = 0.0

    def poll_once(self, pw) -> None:
        now = int(time.time())
        status = powerwall.read_status(pw)
        try:
            strings = powerwall.read_strings(pw, self.site)
            self.strings_error = None
        except Exception as e:  # keep the battery and grid readings; the health check flags stale strings
            strings = {}
            self.strings_error = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}: {e}"
            log.warning("Reading the strings failed: %s", e)
        self.store.add(now, status, strings)
        self.last_success = now
        self.last_error = None
        if now - self._last_rollup >= ROLLUP_EVERY:
            # Two buckets back, so the bucket that was still filling at the last rollup gets finished
            self.store.rollup(now - 2 * BUCKET)
            self._last_rollup = now

    def check(self) -> None:
        """Runs the health checks every CHECK_EVERY seconds, whether or not the last poll worked."""
        now = int(time.time())
        if self.monitor and now - self._last_check >= CHECK_EVERY:
            self._last_check = now
            self.monitor.run(self.last_success, self.started, now)

    def adjust_reserve(self) -> None:
        """Runs the reserve controller every RESERVE_EVERY seconds, once readings are coming in."""
        now = int(time.time())
        if self.controller and self.last_success and now - self._last_reserve >= RESERVE_EVERY:
            self._last_reserve = now
            self.controller.step(gather_reserve_inputs(self.store, self.controller.settings, now), now)

    def run(self) -> None:
        while True:
            try:
                self.poll_once(powerwall.get_connection())
            except Exception as e:  # keep polling; the gateway or network may come back
                self.last_error = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}: {e}"
                log.warning("Collector poll failed: %s", e)
            try:
                self.check()
            except Exception:
                log.exception("Health check failed")
            try:
                self.adjust_reserve()
            except Exception:
                log.exception("Reserve controller failed")
            time.sleep(self.interval)

    def info(self) -> dict:
        return {
            "poll_seconds": self.interval,
            "last_success": self.last_success,
            "last_error": self.last_error,
            "strings_source": "tedapi" if powerwall.tedapi_enabled() else "local_api",
            "strings_error": self.strings_error,
        }


_collector: Collector | None = None
_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        os.makedirs(os.path.dirname(db_path()) or ".", exist_ok=True)
        _store = Store(db_path())
    return _store


def start() -> Collector:
    """Starts polling in a daemon thread. Called once from main()."""
    global _collector
    site = load_site()
    # Readings from before inputs were named per Powerwall all came from the local API's inverter
    get_store().prefix_unnamed_inputs(f"PW{site.local_api_powerwall}-")
    notifier = health.notifier_from_env()
    monitor = health.Monitor(site, get_store(), notifier, timezone(), os.getenv("HEARTBEAT_URL", "").strip() or None)
    controller = reserve.Controller(
        get_store(), site.reserve, live=reserve_mode() == "live", tz=timezone(), notifier=notifier,
        writer=reserve.make_writer(powerwall.get_connection, powerwall.get_cloud_connection))
    _collector = Collector(get_store(), float(os.getenv("PW_POLL_SECONDS", "30")), monitor, controller, site)
    threading.Thread(target=_collector.run, name="collector", daemon=True).start()
    return _collector


def info() -> dict | None:
    return _collector.info() if _collector else None


def get_collector() -> Collector | None:
    return _collector


def reserve_mode() -> str:
    """"dry-run" (the default) records and announces decisions; "live" also writes them to the Powerwall."""
    return "live" if os.getenv("RESERVE_MODE", "").strip().lower() == "live" else "dry-run"


def gather_reserve_inputs(store: Store, settings, now: int) -> reserve.Inputs:
    tz = timezone()

    def alerts():
        lat, lon = location()
        return weather.get_alerts(lat, lon, os.getenv("PW_EMAIL", "home-energy-agent@local"))

    def solar():
        lat, lon = location()
        return forecast.get_forecast(load_site(), lat, lon, tz)

    def rain():
        lat, lon = location()
        return forecast.get_rain_chance(lat, lon, tz)

    return reserve.gather(powerwall.get_connection, store, settings, alerts, solar, now, tz,
                          get_grid=ercot.get_condition, get_rain=rain)
