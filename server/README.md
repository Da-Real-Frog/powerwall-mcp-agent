# Powerwall & Solar MCP Server

An MCP (Model Context Protocol) server built with Python and FastMCP that exposes secure local telemetry and weather forecasts as standardized tools.

## Tools Provided
1. `get_powerwall_status`: Connects locally to the Tesla Powerwall gateway to return battery percentage, grid status, solar power generation, and home load.
2. `get_solar_forecast`: Queries the `forecast.solar` API across three separate configurable roof arrays, combining their hourly wattages and daily totals.

## Configuration (.env)
Ensure your `.env` file contains:
- `PW_HOST`, `PW_PASSWORD`, `PW_EMAIL`
- `SOLAR_LAT`, `SOLAR_LON`
- Array specs (`SOLAR_DEC_1/2/3`, `SOLAR_AZ_1/2/3`, `SOLAR_KWP_1/2/3`)

