"""MCP server exposing Powerwall telemetry, the solar forecast and weather alerts as read-only tools."""
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo

import uvicorn
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.applications import Starlette

from . import collector, forecast, health, history, powerwall, reserve, weather
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


@mcp.tool()
def get_health_report() -> dict:
    """
    Runs the health checks now over the recorded readings and returns what they find: a string producing
    nothing or under half of its neighbours (per panel, averaged over 15 minutes), an input missing from
    the gateway's readings, the grid being down, or no recent reading from the gateway. Also lists the
    alerts currently open. Read-only: it does not open, close or send alerts.
    """
    try:
        site = load_site()
    except ConfigError as e:
        raise ToolError(str(e)) from e
    running = collector.get_collector()
    now = int(time.time())
    last_success = running.last_success if running else None
    started = running.started if running else now
    findings = health.evaluate(site, collector.get_store(), last_success, started, now)
    return {
        "healthy": not findings,
        "findings": [{"kind": f.kind, "input": f.subject, "severity": f.severity, "message": f.message}
                     for f in findings],
        "open_alerts": _alerts(collector.get_store().open_alerts()),
        "collector": collector.info(),
    }


@mcp.tool()
def get_alerts(days: int = 7) -> dict:
    """
    Alerts raised by the health checks: every open alert plus those resolved in the last `days` days
    (default 7), with when each opened, was last seen and resolved. Use it to build a history of faults,
    for example to report a failing string to Tesla.
    """
    if not 1 <= days <= 366:
        raise ToolError("days must be between 1 and 366")
    return {"alerts": _alerts(collector.get_store().alerts_since(int(time.time()) - days * 86400))}


@mcp.tool()
def explain_reserve_decision() -> dict:
    """
    Explains the backup reserve the controller would choose right now and why: the rule that matched
    (1 grid down: leave it; 2 severe NWS warning: 100%; 3 severe watch: 80%; 4 tomorrow's solar below
    daytime use: 60%; 5 solar covers daytime use but not a full recharge: 40%; 6 otherwise: 20%), the inputs
    behind it, and whether a safeguard (minimum step, one change per hour, pause after a manual change)
    holds it back. Also says whether the controller is in dry-run or live mode. Changes nothing.
    """
    try:
        site = load_site()
        now = int(time.time())
        inputs = collector.gather_reserve_inputs(collector.get_store(), site.reserve, now)
    except (ConfigError, powerwall.PowerwallError) as e:
        raise ToolError(str(e)) from e
    running = collector.get_collector()
    controller = running.controller if running and running.controller else reserve.Controller(
        collector.get_store(), site.reserve, live=False, tz=timezone())
    return {"mode": collector.reserve_mode(), "inputs": asdict(inputs), **controller.preview(inputs, now)}


@mcp.tool()
def get_reserve_log(days: int = 7) -> dict:
    """
    The reserve controller's decisions over the last `days` days (default 7), one entry each time the rule,
    target or action changed: when, which rule, the target and the reserve at the time, and what it did
    (set, would_set in dry-run, waiting, paused, none or failed), with the reason.
    """
    if not 1 <= days <= 90:
        raise ToolError("days must be between 1 and 90")
    zone = ZoneInfo(timezone())
    entries = []
    for d in collector.get_store().reserve_decisions(int(time.time()) - days * 86400):
        details = json.loads(d["details"] or "{}")
        entries.append({"time": datetime.fromtimestamp(d["ts"], zone).isoformat(timespec="minutes"),
                        "rule": d["rule"], "target": d["target"], "reserve_then": d["current"],
                        "action": d["action"], "mode": d["mode"], "reason": details.get("reason"),
                        "note": details.get("note")})
    return {"mode": collector.reserve_mode(), "decisions": entries}


def _alerts(rows: list[dict]) -> list[dict]:
    zone = ZoneInfo(timezone())

    def stamp(t):
        return None if t is None else datetime.fromtimestamp(t, zone).isoformat(timespec="minutes")

    return [{"kind": a["kind"], "input": a["subject"], "message": a["message"], "opened": stamp(a["opened_at"]),
             "last_seen": stamp(a["last_seen"]), "resolved": stamp(a["resolved_at"])} for a in rows]


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
