"""Shapes stored readings into tool results: current strings, history series and daily energy."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Site
from .storage import BUCKET, Store


def site_layout(site: Site) -> dict:
    return {
        "panel_watts": site.panel_watts,
        "derate": site.derate,
        "arrays": [
            {"name": a.name, "panels": a.panels, "kwp": a.kwp, "tilt": a.tilt, "azimuth": a.azimuth}
            for a in site.arrays
        ],
        "inputs": [
            {"powerwall": i.powerwall, "input": i.key or None, "array": i.array,
             "panels_per_string": i.panels_per_string, "strings": i.strings,
             "panels": i.panels_per_string * i.strings}
            for i in site.inputs
        ],
        "unused_inputs": sorted(site.unused_inputs),
    }


def string_status(site: Site, latest: tuple[int, list[dict]], now: int, tz: str) -> dict:
    """The latest reading of each input, with the array and panel count from site.toml where the letter is mapped."""
    ts, rows = latest
    by_letter = {i.key: i for i in site.inputs if i.key}
    inputs = []
    for r in rows:
        entry = {"input": r["input"], "volts": r["volts"], "amps": r["amps"], "watts": r["watts"]}
        if r["state"] is not None:
            entry["state"] = r["state"]
        wired = by_letter.get(r["input"])
        if r["input"] in site.unused_inputs:
            entry["unused"] = True
        elif wired:
            panels = wired.panels_per_string * wired.strings
            entry.update(powerwall=wired.powerwall, array=wired.array, panels=panels,
                         watts_per_panel=None if r["watts"] is None else round(r["watts"] / panels, 1))
        inputs.append(entry)
    return {
        "read_at": datetime.fromtimestamp(ts, ZoneInfo(tz)).isoformat(timespec="seconds"),
        "age_seconds": now - ts,
        "inputs": inputs,
        "unmapped_inputs": sorted(
            r["input"] for r in rows if r["input"] not in by_letter and r["input"] not in site.unused_inputs),
    }


def history(store: Store, hours: float, step_minutes: int, now: int, tz: str) -> dict:
    """Averaged power and per-input series over the last `hours`, in `step_minutes` buckets."""
    step = max(BUCKET, step_minutes * 60 // BUCKET * BUCKET)
    since = now - int(hours * 3600)
    zone = ZoneInfo(tz)

    def stamp(t: int) -> str:
        return datetime.fromtimestamp(t, zone).isoformat(timespec="minutes")

    power = [{"time": stamp(r["t"]), **{k: _round(v) for k, v in r.items() if k != "t"}}
             for r in store.power_history(since, step)]
    strings: dict[str, list[dict]] = {}
    for r in store.string_history(since, step):
        strings.setdefault(r["input"], []).append(
            {"time": stamp(r["t"]), "volts": _round(r["volts"]), "amps": _round(r["amps"], 2),
             "watts": _round(r["watts"])})
    return {"step_minutes": step // 60, "power": power, "strings": strings}


def energy_summary(store: Store, days: int, now: int, tz: str) -> dict:
    """kWh per local calendar day, from the five-minute averages. `coverage` is the share of the day with data."""
    zone = ZoneInfo(tz)
    today = datetime.fromtimestamp(now, zone).replace(hour=0, minute=0, second=0, microsecond=0)
    result = []
    for back in range(days - 1, -1, -1):
        # Step by calendar date, not 24 hours, so DST days stay aligned to local midnight
        day = (today - timedelta(days=back)).replace(tzinfo=None)
        start = int(day.replace(tzinfo=zone).timestamp())
        end = int((day + timedelta(days=1)).replace(tzinfo=zone).timestamp())
        buckets = store.energy_buckets(start, min(end, now))

        def kwh(column: str, buckets=buckets) -> float:
            # Each bucket's average watts held for BUCKET seconds
            return round(sum((b[column] or 0) for b in buckets) * BUCKET / 3600 / 1000, 2)

        elapsed = max(min(end, now) - start, 1)
        result.append({
            "date": day.date().isoformat(),
            "solar_kwh": kwh("solar_w"),
            "home_kwh": kwh("home_w"),
            "grid_import_kwh": kwh("grid_import_w"),
            "grid_export_kwh": kwh("grid_export_w"),
            "battery_charge_kwh": kwh("battery_charge_w"),
            "battery_discharge_kwh": kwh("battery_discharge_w"),
            "grid_down_minutes": round(sum(
                (b["grid_down_samples"] or 0) / b["samples"] * BUCKET for b in buckets if b["samples"]) / 60),
            "coverage": round(min(len(buckets) * BUCKET / elapsed, 1.0), 2),
        })
    return {"days": result}


def _round(value, digits: int = 1):
    return None if value is None else round(value, digits)
