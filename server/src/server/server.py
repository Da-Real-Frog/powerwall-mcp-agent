"""MCP server exposing Powerwall telemetry, the solar forecast and weather alerts as read-only tools."""
import os
import time
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette

from . import collector, forecast, history, powerwall, weather
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


@mcp.tool()
def get_site_layout() -> dict:
    """
    Describes the solar system: each roof array (panel count, kWp, tilt, compass azimuth) and each inverter
    input (which Powerwall+, the gateway's letter for it, which array, panels per string and paralleled strings).
    """
    try:
        return history.site_layout(load_site())
    except ConfigError as e:
        raise ToolError(str(e)) from e


@mcp.tool()
def get_string_status() -> dict:
    """
    Latest volts, amps and watts for each solar input, as recorded by the collector (every 30 seconds by
    default), with its array and watts per panel where the input letter is mapped in site.toml.
    Use it to spot a string producing far less than its neighbours.
    """
    try:
        latest = collector.get_store().latest_strings()
        if latest is None:
            raise ToolError(f"No string readings recorded yet. Collector: {collector.info()}")
        result = history.string_status(load_site(), latest, int(time.time()), timezone())
    except ConfigError as e:
        raise ToolError(str(e)) from e
    result["collector"] = collector.info()
    return result


@mcp.tool()
def get_history(hours: float = 24, step_minutes: int = 15) -> dict:
    """
    Averaged history over the last `hours` (default 24) in `step_minutes` buckets (default 15, minimum 5):
    battery %, solar, home, grid and battery watts (grid positive = importing, battery positive = discharging),
    minutes the grid was down, and volts/amps/watts for each solar input.
    """
    if hours <= 0 or step_minutes <= 0:
        raise ToolError("hours and step_minutes must be positive")
    if hours * 60 / max(step_minutes, 5) > 2000:
        raise ToolError("That is more than 2000 points; use a larger step_minutes")
    return history.history(collector.get_store(), hours, step_minutes, int(time.time()), timezone())


@mcp.tool()
def get_energy_summary(days: int = 7) -> dict:
    """
    Energy in kWh for each of the last `days` local calendar days (default 7, today included): solar
    produced, home used, grid imported and exported, battery charged and discharged, plus minutes the grid
    was down. `coverage` is the share of each day the collector has data for; below 1 the totals are low.
    """
    if not 1 <= days <= 366:
        raise ToolError("days must be between 1 and 366")
    return history.energy_summary(collector.get_store(), days, int(time.time()), timezone())


def create_app() -> Starlette:
    """Serves both transports on one port.

    /mcp is Streamable HTTP with plain JSON replies (the Gemini client uses it). /sse is the SSE
    endpoint the server had originally, kept because MCP Inspector setups that used it still connect to it.
    """
    http_app = mcp.http_app(path="/mcp", json_response=True, stateless_http=True)
    sse_app = mcp.http_app(transport="sse")

    @asynccontextmanager
    async def lifespan(app):
        async with http_app.router.lifespan_context(app), sse_app.router.lifespan_context(app):
            yield

    return Starlette(
        routes=[*http_app.routes, *sse_app.routes],
        middleware=http_app.user_middleware,
        lifespan=lifespan,
    )


def main() -> None:
    if os.getenv("PW_COLLECT", "1") != "0":
        collector.start()
    # Clients connect to http://<host>:8000/mcp (Streamable HTTP) or http://<host>:8000/sse (SSE).
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
