"""SQLite store for the collector's readings.

Raw readings (one row per poll) are kept for RAW_DAYS. Five-minute averages are kept forever and
back the history and energy tools, so the database stays small: about 100 MB a year of raw rows,
pruned to a few MB of averages once they age out.
"""
import json
import sqlite3
import threading
import time

RAW_DAYS = 90
BUCKET = 300  # seconds in one average

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    ts INTEGER PRIMARY KEY,
    battery_percent REAL,
    grid_status TEXT,
    grid_w REAL,
    solar_w REAL,
    home_w REAL,
    battery_w REAL
);
CREATE TABLE IF NOT EXISTS strings (
    ts INTEGER NOT NULL,
    input TEXT NOT NULL,
    volts REAL,
    amps REAL,
    watts REAL,
    state TEXT,
    PRIMARY KEY (ts, input)
);
CREATE TABLE IF NOT EXISTS readings_5min (
    bucket INTEGER PRIMARY KEY,
    samples INTEGER,
    battery_percent REAL,
    solar_w REAL,
    home_w REAL,
    grid_import_w REAL,
    grid_export_w REAL,
    battery_discharge_w REAL,
    battery_charge_w REAL,
    grid_down_samples INTEGER
);
CREATE TABLE IF NOT EXISTS strings_5min (
    bucket INTEGER NOT NULL,
    input TEXT NOT NULL,
    samples INTEGER,
    volts REAL,
    amps REAL,
    watts REAL,
    PRIMARY KEY (bucket, input)
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    key TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject TEXT,
    message TEXT NOT NULL,
    opened_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    misses INTEGER NOT NULL DEFAULT 0,
    resolved_at INTEGER
);
CREATE INDEX IF NOT EXISTS alerts_open ON alerts (resolved_at, key);
CREATE TABLE IF NOT EXISTS reserve_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reserve_decisions (
    ts INTEGER NOT NULL,
    rule INTEGER NOT NULL,
    target REAL,
    current REAL,
    action TEXT NOT NULL,
    mode TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS reserve_decisions_ts ON reserve_decisions (ts);
"""

# Signed powers are split before averaging, so a bucket that both imported and exported keeps both.
ROLLUP_READINGS = f"""
INSERT OR REPLACE INTO readings_5min
SELECT ts / {BUCKET} * {BUCKET}, COUNT(*), AVG(battery_percent), AVG(solar_w), AVG(home_w),
       AVG(MAX(grid_w, 0)), AVG(MAX(-grid_w, 0)), AVG(MAX(battery_w, 0)), AVG(MAX(-battery_w, 0)),
       SUM(grid_status != 'UP')
FROM readings WHERE ts >= ? GROUP BY ts / {BUCKET}
"""
ROLLUP_STRINGS = f"""
INSERT OR REPLACE INTO strings_5min
SELECT ts / {BUCKET} * {BUCKET}, input, COUNT(*), AVG(volts), AVG(amps), AVG(watts)
FROM strings WHERE ts >= ? GROUP BY ts / {BUCKET}, input
"""


class Store:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)

    def add(self, ts: int, status: dict, strings: dict) -> None:
        """Saves one poll: the status from powerwall.read_status and the dict from pypowerwall's strings()."""
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO readings VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, status["battery_level_percent"], status["grid_status"], status["grid_power_watts"],
                 status["solar_power_watts"], status["home_usage_watts"], status["battery_power_watts"]),
            )
            self._db.executemany(
                "INSERT OR REPLACE INTO strings VALUES (?, ?, ?, ?, ?, ?)",
                [(ts, name, s.get("Voltage"), s.get("Current"), s.get("Power"),
                  None if s.get("State") is None else str(s["State"]))
                 for name, s in strings.items()],
            )

    def rollup(self, since: int) -> None:
        """Recomputes the five-minute averages from `since` on, then drops raw rows older than RAW_DAYS."""
        start = since // BUCKET * BUCKET
        with self._lock, self._db:
            self._db.execute(ROLLUP_READINGS, (start,))
            self._db.execute(ROLLUP_STRINGS, (start,))
            cutoff = int(time.time()) - RAW_DAYS * 86400
            self._db.execute("DELETE FROM readings WHERE ts < ?", (cutoff,))
            self._db.execute("DELETE FROM strings WHERE ts < ?", (cutoff,))

    def latest_strings(self) -> tuple[int, list[dict]] | None:
        """The most recent poll's string readings, as (timestamp, rows)."""
        with self._lock:
            row = self._db.execute("SELECT MAX(ts) FROM strings").fetchone()
            if row[0] is None:
                return None
            rows = self._db.execute(
                "SELECT input, volts, amps, watts, state FROM strings WHERE ts = ? ORDER BY input", (row[0],)
            ).fetchall()
        return row[0], [dict(r) for r in rows]

    def string_history(self, since: int, step: int) -> list[dict]:
        """Per-input averages in `step`-second buckets, from the five-minute table."""
        with self._lock:
            rows = self._db.execute(
                "SELECT bucket / ? * ? AS t, input, AVG(volts) AS volts, AVG(amps) AS amps, AVG(watts) AS watts "
                "FROM strings_5min WHERE bucket >= ? GROUP BY t, input ORDER BY t, input",
                (step, step, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def power_history(self, since: int, step: int) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT bucket / ? * ? AS t, AVG(battery_percent) AS battery_percent, AVG(solar_w) AS solar_w, "
                "AVG(home_w) AS home_w, AVG(grid_import_w - grid_export_w) AS grid_w, "
                "AVG(battery_discharge_w - battery_charge_w) AS battery_w, SUM(grid_down_samples) AS grid_down_samples "
                "FROM readings_5min WHERE bucket >= ? GROUP BY t ORDER BY t",
                (step, step, since),
            ).fetchall()
        return [dict(r) for r in rows]

    def energy_buckets(self, start: int, end: int) -> list[dict]:
        """Five-minute averages in [start, end), for summing into kWh."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM readings_5min WHERE bucket >= ? AND bucket < ? ORDER BY bucket", (start, end)
            ).fetchall()
        return [dict(r) for r in rows]

    def string_buckets(self, start: int, end: int) -> list[dict]:
        """Five-minute string averages with bucket start in [start, end)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM strings_5min WHERE bucket >= ? AND bucket < ? ORDER BY bucket, input", (start, end)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_reading(self) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM readings ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def open_alerts(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM alerts WHERE resolved_at IS NULL ORDER BY opened_at").fetchall()
        return [dict(r) for r in rows]

    def alerts_since(self, since: int) -> list[dict]:
        """Open alerts plus those resolved after `since`."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM alerts WHERE resolved_at IS NULL OR resolved_at >= ? ORDER BY opened_at", (since,)
            ).fetchall()
        return [dict(r) for r in rows]

    def open_alert(self, key: str, kind: str, subject: str | None, message: str, now: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO alerts (key, kind, subject, message, opened_at, last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                (key, kind, subject, message, now, now),
            )

    def touch_alert(self, alert_id: int, message: str, now: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "UPDATE alerts SET last_seen = ?, misses = 0, message = ? WHERE id = ?", (now, message, alert_id)
            )

    def miss_alert(self, alert_id: int) -> int:
        with self._lock, self._db:
            self._db.execute("UPDATE alerts SET misses = misses + 1 WHERE id = ?", (alert_id,))
            return self._db.execute("SELECT misses FROM alerts WHERE id = ?", (alert_id,)).fetchone()[0]

    def resolve_alert(self, alert_id: int, now: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE alerts SET resolved_at = ? WHERE id = ?", (now, alert_id))

    def reserve_state(self) -> dict:
        with self._lock:
            row = self._db.execute("SELECT state FROM reserve_state WHERE id = 1").fetchone()
        return json.loads(row[0]) if row else {}

    def save_reserve_state(self, state: dict) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO reserve_state VALUES (1, ?)", (json.dumps(state),))

    def log_reserve_decision(self, ts: int, rule: int, target: float | None, current: float | None,
                             action: str, mode: str, details: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT INTO reserve_decisions VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (ts, rule, target, current, action, mode, details))

    def last_reserve_decision(self) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM reserve_decisions ORDER BY ts DESC, rowid DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def reserve_decisions(self, since: int) -> list[dict]:
        """Decisions since `since`, keeping only those that differ from the one before (rule, target or action)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM reserve_decisions WHERE ts >= ? ORDER BY ts, rowid", (since,)).fetchall()
        changes, last = [], None
        for r in map(dict, rows):
            key = (r["rule"], r["target"], r["action"])
            if key != last:
                changes.append(r)
                last = key
        return changes
