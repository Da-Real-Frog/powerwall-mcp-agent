"""Solar production forecast per array, from Open-Meteo's tilted-panel irradiance."""
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from .config import Site

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
HOURS = 48


def fetch_gti(lat: float, lon: float, tilt: float, azimuth: float, tz: str) -> tuple[list[str], list[float | None]]:
    """Returns (local hour stamps, W/m2 on the tilted panel) for today and the next two days.

    Open-Meteo takes a single tilt/azimuth per request, so each array is its own call.
    `azimuth` is in Open-Meteo's convention (0 = South, -90 = East, 90 = West).
    """
    response = httpx.get(
        OPEN_METEO_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "hourly": "global_tilted_irradiance",
            "tilt": tilt,
            "azimuth": azimuth,
            "timezone": tz,
            "forecast_days": 3,
        },
        timeout=10.0,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    return hourly["time"], hourly["global_tilted_irradiance"]


def build_forecast(site: Site, per_array: dict[str, tuple[list[str], list[float | None]]], now: datetime) -> dict:
    """Combines per-array irradiance into expected watts for the next 48 hours, starting at the current hour.

    1 kWp makes 1000 W at 1000 W/m2, so watts = W/m2 * kWp * derate. Hours with no sun are left out
    of the hourly list; `daily_kwh` sums each calendar day that the window touches.
    """
    times = None
    for name, (array_times, _) in per_array.items():
        if times is None:
            times = array_times
        elif array_times != times:
            raise ValueError(f"Open-Meteo returned a different time axis for array {name}")
    if times is None:
        raise ValueError("No arrays to forecast")

    current_hour = now.strftime("%Y-%m-%dT%H:00")
    start = next((i for i, t in enumerate(times) if t >= current_hour), len(times))
    window = range(start, min(start + HOURS, len(times)))

    hourly = []
    daily_kwh: dict[str, float] = {}
    for i in window:
        watts = {
            a.name: (per_array[a.name][1][i] or 0) * a.kwp * site.derate
            for a in site.arrays
        }
        total = sum(watts.values())
        day = times[i][:10]
        daily_kwh[day] = daily_kwh.get(day, 0.0) + total / 1000
        if round(total) > 0:
            hourly.append({
                "time": times[i],
                "expected_watts": round(total),
                "per_array_watts": {name: round(w) for name, w in watts.items()},
            })

    return {
        "derate": site.derate,
        "hourly": hourly,
        "daily_kwh": {day: round(kwh, 1) for day, kwh in daily_kwh.items()},
    }


def get_forecast(site: Site, lat: float, lon: float, tz: str) -> dict:
    per_array = {a.name: fetch_gti(lat, lon, a.tilt, a.open_meteo_azimuth, tz) for a in site.arrays}
    return build_forecast(site, per_array, datetime.now(ZoneInfo(tz)))


def rain_chance(times: list[str], chances: list[float | None], now: datetime, hours: int = 24) -> float:
    """Highest hourly chance of rain (%) over the next `hours`, starting at the current hour."""
    current_hour = now.strftime("%Y-%m-%dT%H:00")
    upcoming = [c or 0 for t, c in zip(times, chances, strict=True) if t >= current_hour][:hours]
    return max(upcoming, default=0)


def get_rain_chance(lat: float, lon: float, tz: str) -> float:
    response = httpx.get(
        OPEN_METEO_URL,
        params={"latitude": lat, "longitude": lon, "hourly": "precipitation_probability", "timezone": tz,
                "forecast_days": 2},
        timeout=10.0,
    )
    response.raise_for_status()
    hourly = response.json()["hourly"]
    return rain_chance(hourly["time"], hourly["precipitation_probability"], datetime.now(ZoneInfo(tz)))
