"""MCP server exposing Powerwall telemetry, the solar forecast and weather alerts as read-only tools."""
import os

from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from . import forecast, powerwall, weather
from .config import ConfigError, load_site, location, timezone

# Load environment variables from .env (Docker passes them with --env-file instead)
load_dotenv()

mcp = FastMCP("TeslaPowerwallServer")


@mcp.tool()
def get_powerwall_status() -> dict:
    """
    Fetches the current battery charge percentage (Tesla app scale), grid status (UP, DOWN or SYNCING),
    grid power, solar generation, home usage and battery power from the local Tesla Powerwall gateway.
    Power values are in watts.
    """
    try:
        return powerwall.read_status(powerwall.get_connection())
    except (ConfigError, powerwall.PowerwallError) as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        raise ToolError(f"Failed to read the Powerwall: {e}") from e


@mcp.tool()
def get_solar_forecast() -> dict:
    """
    Returns the expected solar generation in watts for each hour of the next 48 hours, starting at the
    current hour, in total and per roof array, plus the expected kWh for each day in that window.
    Hours with no sun are left out.
    """
    try:
        lat, lon = location()
        return forecast.get_forecast(load_site(), lat, lon, timezone())
    except ConfigError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        raise ToolError(f"Forecast failed: {e}") from e


@mcp.tool()
def get_active_weather_alerts() -> dict:
    """Returns active National Weather Service (NWS) alerts, watches and warnings for the home location."""
    try:
        lat, lon = location()
        alerts = weather.get_alerts(lat, lon, os.getenv("PW_EMAIL", "home-energy-agent@local"))
    except ConfigError as e:
        raise ToolError(str(e)) from e
    except Exception as e:
        raise ToolError(f"NWS API request failed: {e}") from e
    return {"alerts": alerts}


def main() -> None:
    # Streamable HTTP on all interfaces; clients connect to http://<host>:8000/mcp
    mcp.run(transport="http", host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
