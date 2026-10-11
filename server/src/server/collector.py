"""Background poller: reads the gateway every PW_POLL_SECONDS and saves the readings to SQLite."""
import logging
import os
import threading
import time

from . import powerwall
from .storage import BUCKET, Store

log = logging.getLogger(__name__)

ROLLUP_EVERY = 300  # seconds between recomputing the five-minute averages


def db_path() -> str:
    return os.getenv("PW_DB_PATH") or os.path.join(os.getenv("PW_CACHE_DIR", "."), "powerwall.db")


class Collector:
    def __init__(self, store: Store, interval: float):
        self.store = store
        self.interval = interval
        self.last_success: int | None = None
        self.last_error: str | None = None
        self._last_rollup = 0.0

    def poll_once(self, pw) -> None:
        now = int(time.time())
        status = powerwall.read_status(pw)
        strings = pw.strings() or {}
        self.store.add(now, status, strings)
        self.last_success = now
        self.last_error = None
        if now - self._last_rollup >= ROLLUP_EVERY:
            # Two buckets back, so the bucket that was still filling at the last rollup gets finished
            self.store.rollup(now - 2 * BUCKET)
            self._last_rollup = now

    def run(self) -> None:
        while True:
            try:
                self.poll_once(powerwall.get_connection())
            except Exception as e:  # keep polling; the gateway or network may come back
                self.last_error = f"{time.strftime('%Y-%m-%dT%H:%M:%S')}: {e}"
                log.warning("Collector poll failed: %s", e)
            time.sleep(self.interval)

    def info(self) -> dict:
        return {
            "poll_seconds": self.interval,
            "last_success": self.last_success,
            "last_error": self.last_error,
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
    _collector = Collector(get_store(), float(os.getenv("PW_POLL_SECONDS", "30")))
    threading.Thread(target=_collector.run, name="collector", daemon=True).start()
    return _collector


def info() -> dict | None:
    return _collector.info() if _collector else None
