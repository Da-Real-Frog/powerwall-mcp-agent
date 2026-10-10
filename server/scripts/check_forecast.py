"""Prints the per-array and total solar forecast, without starting the MCP server.

Run from the server/ directory:  uv run scripts/check_forecast.py
"""
from dotenv import load_dotenv

from server import forecast
from server.config import load_site, location, timezone

load_dotenv()

site = load_site()
lat, lon = location()
result = forecast.get_forecast(site, lat, lon, timezone())

names = [a.name for a in site.arrays]
print(f"{'time':<18}" + "".join(f"{n:>9}" for n in names) + f"{'total W':>9}")
for hour in result["hourly"]:
    per_array = "".join(f"{hour['per_array_watts'][n]:>9}" for n in names)
    print(f"{hour['time']:<18}{per_array}{hour['expected_watts']:>9}")

print(f"\nDerate applied: {result['derate']}")
for day, kwh in result["daily_kwh"].items():
    print(f"{day}: {kwh} kWh")
