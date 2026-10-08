# Powerwall & Solar MCP Server

An MCP (Model Context Protocol) server built with Python and FastMCP that exposes local Powerwall telemetry, a solar forecast and weather alerts as standardized tools. It serves SSE on port 8000.

## Tools Provided
1. `get_powerwall_status`: Connects locally to the Tesla Powerwall gateway to return battery percentage, grid status, solar power generation, home load and battery power.
2. `get_solar_forecast`: Queries the Open-Meteo API for the tilted-panel irradiance of each of the three roof arrays (one request per array), converts it to watts using each array's size, and returns the combined hourly total for the next 48 hours. Night hours are left out.
3. `get_active_weather_alerts`: Returns active National Weather Service alerts, watches and warnings for the home location (US only).

Neither weather API needs a key.

## Configuration (.env)
Ensure your `.env` file (in `src/server/`) contains:
- `PW_HOST`, `PW_PASSWORD`, `PW_EMAIL`: Powerwall gateway credentials. `PW_EMAIL` is also sent as the contact in the NWS request header.
- `SOLAR_LAT`, `SOLAR_LON`: Home location, used for both the forecast and the alerts.
- Array specs, one set per array (`_1`, `_2`, `_3`):
  - `SOLAR_DEC_n`: Panel tilt in degrees.
  - `SOLAR_AZ_n`: Panel orientation in degrees (0 = South, -90 = East, 90 = West).
  - `SOLAR_KWP_n`: Array size in kW.

The timezone is fixed to `America/Chicago` in `server.py`.

Put comments on their own lines, not after a value. Docker's `--env-file` does not strip inline comments, so `SOLAR_KWP_1=5.2  # my array` makes the forecast tool fail.

## Running Locally
```bash
cd src/server
uv run server.py
```
The client then connects with `MCP_SERVER_URL=http://localhost:8000/sse`.

Two standalone scripts in `src/server/` check each data source without starting the server:
```bash
uv run test_pw.py         # Powerwall connection and live readings
uv run test_forecast.py   # Per-array and total solar forecast
```

## Deployment
Pushing changes under `server/` to `main` triggers `.github/workflows/deploy.yml`, which builds the Docker image on the self-hosted runner and restarts the `mcp-server` container on port 8000. The container reads its configuration from `/home/nicolaskeller/mcp-server.env` on that host, not from the repo, so that file needs all the variables above.
