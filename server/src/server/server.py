import httpx
import json
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
def get_solar_forecast() -> str:
    """Returns the forecasted total solar generation in Watts for the next 48 hours."""
    
    # Load configuration from existing environment variables
    try:
        LAT = os.getenv("SOLAR_LAT", "0.0")
        LON = os.getenv("SOLAR_LON", "0.0")

        # (tilt, azimuth, capacity in kW) for each array
        arrays = [
            (float(os.getenv("SOLAR_DEC_1", "44")), float(os.getenv("SOLAR_AZ_1", "30")), float(os.getenv("SOLAR_KWP_1", "0.0"))),
            (float(os.getenv("SOLAR_DEC_2", "30")), float(os.getenv("SOLAR_AZ_2", "30")), float(os.getenv("SOLAR_KWP_2", "0.0"))),
            (float(os.getenv("SOLAR_DEC_3", "30")), float(os.getenv("SOLAR_AZ_3", "120")), float(os.getenv("SOLAR_KWP_3", "0.0"))),
        ]
    except ValueError:
        return json.dumps({"status": "error", "message": "Invalid solar tilt, azimuth or capacity values in .env"})

    try:
        times = []
        total_watts = []

        # Open-Meteo only accepts a single tilt/azimuth per request, so each array is its own call
        for tilt, azimuth, kwp in arrays:
            response = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": LAT,
                    "longitude": LON,
                    "hourly": "global_tilted_irradiance",
                    "tilt": tilt,
                    "azimuth": azimuth,
                    "timezone": PW_TIMEZONE,
                    "forecast_days": 2,
                },
                timeout=10.0,
            )
            response.raise_for_status()
            hourly = response.json()["hourly"]

            times = hourly["time"]
            if not total_watts:
                total_watts = [0.0] * len(times)

            # Calculate actual Watts based on system kW size
            for i, gti in enumerate(hourly["global_tilted_irradiance"]):
                total_watts[i] += (gti or 0) * kwp

        forecast = []
        for time, watts in zip(times, total_watts):
            # Filter out the night to save LLM tokens
            if round(watts) > 0:
                forecast.append({
                    "time": time,
                    "expected_watts": round(watts)
                })

        return json.dumps({"status": "success", "forecast": forecast})

    except Exception as e:
        return json.dumps({"status": "error", "message": f"Forecast failed: {str(e)}"})

#tool 3 - Get alerts and watches
@mcp.tool()
def get_active_weather_alerts() -> str:
    """Returns active National Weather Service (NWS) alerts, watches, and warnings for the home location."""
    try:
        LAT = os.getenv("SOLAR_LAT", "0.0")
        LON = os.getenv("SOLAR_LON", "0.0")
        EMAIL = os.getenv("PW_EMAIL", "home-energy-agent@local")
    except ValueError:
        return json.dumps({"status": "error", "message": "Invalid coordinates in .env"})

    # NWS API endpoint for alerts based on GPS coordinates
    url = f"https://api.weather.gov/alerts/active?point={LAT},{LON}"
    
    # NWS strongly requests a unique User-Agent header with contact info
    headers = {"User-Agent": f"Powerwall-MCP-Agent, contact: {EMAIL}"}
    
    try:
        response = httpx.get(url, headers=headers, timeout=10.0)
        response.raise_for_status()
        data = response.json()
        
        alerts = []
        for feature in data.get("features", []):
            props = feature.get("properties", {})
            alerts.append({
                "event": props.get("event"),           # e.g., "Severe Thunderstorm Warning"
                "severity": props.get("severity"),     # e.g., "Severe", "Extreme"
                "urgency": props.get("urgency"),       # e.g., "Immediate", "Expected"
                "headline": props.get("headline")      # Detailed summary
            })
        
        if not alerts:
            return json.dumps({"status": "success", "message": "No active weather alerts. Weather is clear."})
            
        return json.dumps({"status": "success", "alerts": alerts})
        
    except Exception as e:
        return json.dumps({"status": "error", "message": f"NWS API request failed: {str(e)}"})


if __name__ == "__main__":
    # Binds the server to all network interfaces on port 8000
    mcp.run(transport="sse", host="0.0.0.0", port=8000)
