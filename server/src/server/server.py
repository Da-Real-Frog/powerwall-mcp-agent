import os
import requests
import pypowerwall
from dotenv import load_dotenv
from fastmcp import FastMCP

# Load environment variables from .env
load_dotenv()

# Initialize the FastMCP Server
mcp = FastMCP("TeslaPowerwallServer")

# Powerwall Credentials
PW_HOST = os.environ.get("PW_HOST")
PW_PASSWORD = os.environ.get("PW_PASSWORD")
PW_EMAIL = os.environ.get("PW_EMAIL")
PW_TIMEZONE = "America/Chicago"


# --- TOOL 1: Powerwall Telemetry ---
@mcp.tool()
def get_powerwall_status() -> dict:
    """
    Fetches the current battery charge percentage, solar generation, 
    home usage, and grid status from the local Tesla Powerwall gateway.
    """
    try:
        pw = pypowerwall.Powerwall(PW_HOST, PW_PASSWORD, PW_EMAIL, PW_TIMEZONE)
        return {
            "battery_level_percent": pw.level(),
            "grid_status": pw.grid(),
            "solar_power_watts": pw.solar(),
            "home_usage_watts": pw.home(),
            "battery_power_watts": pw.battery()
        }
    except Exception as e:
        return {"error": f"Failed to connect to Powerwall: {str(e)}"}


# --- TOOL 2: Multi-Array Solar Weather Forecast ---
@mcp.tool()
def get_solar_forecast() -> dict:
    """
    Fetches estimated solar generation across three roof arrays 
    and returns the combined hourly wattage and daily totals.
    """
    lat = os.environ.get("SOLAR_LAT")
    lon = os.environ.get("SOLAR_LON")
    
    # Collect configurations for up to 3 arrays
    arrays = [
        (os.environ.get("SOLAR_DEC_1"), os.environ.get("SOLAR_AZ_1"), os.environ.get("SOLAR_KWP_1")),
        (os.environ.get("SOLAR_DEC_2"), os.environ.get("SOLAR_AZ_2"), os.environ.get("SOLAR_KWP_2")),
        (os.environ.get("SOLAR_DEC_3"), os.environ.get("SOLAR_AZ_3"), os.environ.get("SOLAR_KWP_3"))
    ]

    combined_watts = {}
    total_watt_hours_day = {}

    try:
        for dec, az, kwp in arrays:
            # Skip any array configuration that is missing in .env
            if not all([dec, az, kwp]):
                continue
                
            url = f"https://api.forecast.solar/estimate/{lat}/{lon}/{dec}/{az}/{kwp}"
            response = requests.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            
            data = response.json().get("result", {})
            watts = data.get("watts", {})
            wh_days = data.get("watt_hours_day", {})
            
            # Aggregate hourly watt estimates
            for time_stamp, watt_val in watts.items():
                combined_watts[time_stamp] = combined_watts.get(time_stamp, 0) + watt_val
                
            # Aggregate daily watt-hour totals
            for day, wh_val in wh_days.items():
                total_watt_hours_day[day] = total_watt_hours_day.get(day, 0) + wh_val

        return {
            "success": True,
            "forecast_watts_per_hour": combined_watts,
            "summary_watt_hours_per_day": total_watt_hours_day
        }
    except Exception as e:
        return {"error": f"Failed to fetch combined solar forecast: {str(e)}"}


if __name__ == "__main__":
    # Binds the server to all network interfaces on port 8000
    mcp.run(transport="sse", host="0.0.0.0", port=8000)
