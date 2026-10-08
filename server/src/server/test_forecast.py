"""Quick check of the Open-Meteo forecast for the sum of the three solar arrays.

Run from this directory:  uv run test_forecast.py
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

LAT = os.getenv("SOLAR_LAT", "0.0")
LON = os.getenv("SOLAR_LON", "0.0")

# (tilt in degrees, azimuth in degrees with 0 = South / -90 = East / 90 = West, size in kWp)
ARRAYS = [
    (float(os.getenv(f"SOLAR_DEC_{n}", "0")), float(os.getenv(f"SOLAR_AZ_{n}", "0")), float(os.getenv(f"SOLAR_KWP_{n}", "0")))
    for n in (1, 2, 3)
]


def fetch_gti(tilt, azimuth):
    """Returns (times, W/m2 on the tilted panel) for the next 48 hours."""
    # Open-Meteo takes a single tilt/azimuth per request, so each array is its own call
    response = httpx.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": LAT,
            "longitude": LON,
            "hourly": "global_tilted_irradiance",
            "tilt": tilt,
            "azimuth": azimuth,
            "timezone": "America/Chicago",
            "forecast_days": 2,
        },
        timeout=10.0,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Open-Meteo {response.status_code}: {response.text}")
    hourly = response.json()["hourly"]
    return hourly["time"], hourly["global_tilted_irradiance"]


times = None
per_array_watts = []
for tilt, azimuth, kwp in ARRAYS:
    times, gti = fetch_gti(tilt, azimuth)
    # 1 kWp produces 1000 W at 1000 W/m2, so watts = W/m2 * kWp
    per_array_watts.append([(g or 0) * kwp for g in gti])

print(f"{'time':<18}{'array 1':>9}{'array 2':>9}{'array 3':>9}{'total W':>9}")
total_wh = 0
for i, time in enumerate(times):
    watts = [round(array[i]) for array in per_array_watts]
    total = sum(watts)
    total_wh += total
    if total > 0:
        print(f"{time:<18}{watts[0]:>9}{watts[1]:>9}{watts[2]:>9}{total:>9}")

print(f"\nTotal over 48h: {total_wh / 1000:.1f} kWh (before inverter and wiring losses)")
