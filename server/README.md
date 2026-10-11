# Powerwall & Solar MCP Server

An MCP (Model Context Protocol) server built with Python and FastMCP that exposes local Powerwall telemetry, a solar forecast and weather alerts as read-only tools. It serves streamable HTTP on port 8000 at `/mcp`, and SSE at `/sse` for clients such as MCP Inspector that were set up for the original SSE endpoint.

## Tools Provided
1. `get_powerwall_status`: Reads the local Tesla Powerwall gateway: battery charge (Tesla app scale), grid status (`UP`, `DOWN` or `SYNCING`), grid power, solar generation, home load and battery power, in watts.
2. `get_solar_forecast`: Queries Open-Meteo for the tilted-panel irradiance of each roof array (one request per array), converts it to watts using each array's size and the derate in `site.toml`, and returns the next 48 hours from the current hour, per array and in total, plus expected kWh per day. Night hours are left out.
3. `get_active_weather_alerts`: Returns active National Weather Service alerts, watches and warnings for the home location (US only).

Failures are reported as MCP tool errors, so clients can tell an error from a reading. Neither weather API needs a key.

## Site layout (site.toml)
`site.toml` describes the arrays, panels and which strings feed which Powerwall+ input, taken from the Tesla permit plans. It holds layout only, no credentials or location, and is checked on load: the panels wired to inputs must add up to each array's panel count. Point `SITE_CONFIG` at another file to override it.

## Configuration (.env)
Copy `.env.example` to `src/server/.env` for local runs, and fill it in. The file is gitignored; never commit it.
- `PW_HOST`, `PW_PASSWORD`, `PW_EMAIL`: Powerwall gateway credentials. `PW_EMAIL` is also sent as the contact in the NWS request header.
- `SOLAR_LAT`, `SOLAR_LON`: Home location, used for both the forecast and the alerts. Required; there is no default.
- `PW_TIMEZONE` (optional): defaults to `America/Chicago`.
- `PW_CACHE_DIR` (optional): where pypowerwall keeps its login cache, default the current directory. The Docker image uses `/data`.

Put comments on their own lines, not after a value. Docker's `--env-file` does not strip inline comments.

## Running Locally
```bash
cd server
uv run server
```
The client then connects with `MCP_SERVER_URL=http://localhost:8000/mcp`.

Two scripts check each data source without starting the server:
```bash
uv run scripts/check_powerwall.py   # Powerwall connection and live readings
uv run scripts/check_forecast.py    # Per-array and total solar forecast
```

## Tests
```bash
uv run pytest
uv run ruff check .
```
CI runs both for the server and the client on every pull request, on GitHub-hosted runners, plus a gitleaks secret scan.

## Deployment
Pushing changes under `server/` to `main` triggers `.github/workflows/deploy.yml`, which builds the Docker image on the self-hosted runner and restarts the `mcp-server` container on port 8000 with a `powerwall-data` volume on `/data`. The container reads its configuration from the env file on that host, not from the repo, so that file needs the variables above.
