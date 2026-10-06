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
        
        # Tilts (Declination)
        DEC_1 = os.getenv("SOLAR_DEC_1", "44")
        DEC_2 = os.getenv("SOLAR_DEC_2", "30")
        DEC_3 = os.getenv("SOLAR_DEC_3", "30")
        
        # Azimuths
        AZ_1 = os.getenv("SOLAR_AZ_1", "30")
        AZ_2 = os.getenv("SOLAR_AZ_2", "30")
        AZ_3 = os.getenv("SOLAR_AZ_3", "120")
        
        # Capacities in kW
        KWP_1 = float(os.getenv("SOLAR_KWP_1", "0.0"))
        KWP_2 = float(os.getenv("SOLAR_KWP_2", "0.0"))
        KWP_3 = float(os.getenv("SOLAR_KWP_3", "0.0"))
    except ValueError:
        return json.dumps({"status": "error", "message": "Invalid solar capacity values in .env"})

    # Format parameters for Open-Meteo
    tilts = f"{DEC_1},{DEC_2},{DEC_3}"
    azimuths = f"{AZ_1},{AZ_2},{AZ_3}"

    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={LAT},{LAT},{LAT}"
        f"&longitude={LON},{LON},{LON}"
        "&hourly=global_tilted_irradiance"
        f"&tilt={tilts}"
        f"&azimuth={azimuths}"
        "&timezone=America%2FChicago"
        "&forecast_days=2"
    )
    
    try:
        response = httpx.get(url, timeout=10.0)
        response.raise_for_status() 
        raw_data = response.json()
        
        # Extract the GTI for all 3 arrays
        array_1_gti = raw_data[0]["hourly"]["global_tilted_irradiance"]
        array_2_gti = raw_data[1]["hourly"]["global_tilted_irradiance"]
        array_3_gti = raw_data[2]["hourly"]["global_tilted_irradiance"]
        times = raw_data[0]["hourly"]["time"]
        
        forecast = []
        # Calculate actual Watts based on system kW size
        for i in range(len(times)):
            array_1_watts = array_1_gti[i] * KWP_1
            array_2_watts = array_2_gti[i] * KWP_2
            array_3_watts = array_3_gti[i] * KWP_3
            
            total_watts = round(array_1_watts + array_2_watts + array_3_watts)
            
            # Filter out the night to save LLM tokens
            if total_watts > 0:
                forecast.append({
                    "time": times[i],
                    "expected_watts": total_watts
                })
                
        return json.dumps({"status": "success", "forecast": forecast})
        
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Forecast failed: {str(e)}"})


if __name__ == "__main__":
    # Binds the server to all network interfaces on port 8000
    mcp.run(transport="sse", host="0.0.0.0", port=8000)
