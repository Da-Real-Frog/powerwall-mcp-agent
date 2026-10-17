# Powerwall & Solar MCP Server

An MCP (Model Context Protocol) server built with Python and FastMCP that exposes local Powerwall telemetry, a solar forecast and weather alerts as read-only tools. It serves streamable HTTP on port 8000 at `/mcp`, and SSE at `/sse` for clients such as MCP Inspector that were set up for the original SSE endpoint.

## Tools Provided
1. `get_powerwall_status`: Reads the local Tesla Powerwall gateway: battery charge (Tesla app scale), grid status (`UP`, `DOWN` or `SYNCING`), grid power, solar generation, home load and battery power, in watts.
2. `get_solar_forecast`: Queries Open-Meteo for the tilted-panel irradiance of each roof array (one request per array), converts it to watts using each array's size and the derate in `site.toml`, and returns the next 48 hours from the current hour, per array and in total, plus expected kWh per day. Night hours are left out.
3. `get_active_weather_alerts`: Returns active National Weather Service alerts, watches and warnings for the home location (US only).
4. `get_site_layout`: The arrays and inverter inputs from `site.toml`.
5. `get_string_status`: The latest volts, amps and watts of each solar input, from the collector, with its array and watts per panel once its letter is mapped in `site.toml`.
6. `get_history`: Averaged battery, solar, home, grid and per-input readings over the last N hours.
7. `get_energy_summary`: kWh per day for solar, home, grid import and export, battery charge and discharge, and grid-down minutes.
8. `get_health_report`: Runs the health checks now and returns what they find, plus the open alerts.
9. `get_alerts`: Open alerts and those resolved in the last N days, with when each opened and closed.
10. `explain_reserve_decision`: The backup reserve the controller would choose now, the rule and inputs behind it, and any safeguard holding it back. Changes nothing.
11. `get_reserve_log`: The controller's decisions over the last N days.

## Reserve controller
Every 15 minutes the collector picks a backup reserve with ordered rules; the first match wins. The percentages are in `[reserve]` in `site.toml` and are starting values to tune.

| Rule | Condition | Reserve |
| --- | --- | --- |
| 1 | Grid is down | Unchanged |
| 2 | Severe NWS warning (tornado, severe thunderstorm, hurricane, tropical storm, winter storm, ice storm, blizzard, extreme cold or heat, high wind), or an ERCOT Energy Emergency Alert (grid reserves below 2,500 MW), held 6 hours after it ends | 100% |
| 3 | Severe NWS watch for the same events; a Wind Advisory (what usually sits behind a CPS Energy high wind alert), Winter Weather or Freezing Rain Advisory; or ERCOT grid conditions that are not normal (a conservation appeal, or reserves below 3,000 MW) | 80% |
| 4 | Tomorrow's solar forecast is below daytime home use | 60% |
| 5 | Tomorrow's solar covers daytime use but not a full recharge (27 kWh) | 40% |
| 6 | Otherwise | 20% |
| 7 | Rules 5 or 6 matched, but the chance of rain is over 20% in any of the next 24 hours: at least | 40% |

Other advisories (heat, fog, air quality) don't count: they rarely mean an outage. ERCOT's state comes from the public dashboard behind its [Grid Conditions](https://www.ercot.com/gridmktinfo/dashboards/gridconditions) page; ERCOT has no feed for its TXANS notices, so a Weather Watch days ahead only shows up once reserves tighten.

If the NWS alerts, ERCOT's grid conditions or the forecasts can't be fetched, nothing changes. Daytime use (07:00 to 19:00) is the median of the last 14 days once the collector has them, and the estimate in `site.toml` until then. Safeguards: changes of under 5 points are skipped, at most one change per hour, and a change made in the Tesla app pauses the controller for 24 hours.

`RESERVE_MODE` is `dry-run` by default: decisions are recorded and announced on ntfy ("Would set reserve to 100%") but nothing is written. With `RESERVE_MODE=live` it writes through the local gateway and falls back to Tesla's cloud if the gateway refuses. The cloud fallback needs a one-time Tesla sign-in saved in `PW_CACHE_DIR`; without it, a refused local write is recorded as failed.

## Health checks and alerts
Every five minutes the collector runs fixed rules over the readings, no LLM involved:
- **String dead / low:** each mapped input is compared, in watts per panel averaged over 15 minutes, with the other inputs on the same array. Under 2 W per panel is dead; under half of the neighbours' median is low. No verdict while the neighbours make under 20 W per panel (night, heavy cloud).
- **Input missing:** a mapped input letter is absent from the gateway's latest readings, which usually means its inverter is offline.
- **Grid down:** the gateway reports the grid as anything but `UP`.
- **Collector stale:** no successful reading for 5 minutes.

A new finding opens an alert and sends an ntfy notification. The alert closes, with a "Resolved" notification, after three checks in a row without it. Alerts are kept in the database, so `get_alerts` gives the history. Inputs listed in `unused_inputs` in `site.toml` are never flagged.

## Collector
While the server runs, a background thread reads the gateway every 30 seconds and saves the readings to SQLite (`powerwall.db` in `PW_CACHE_DIR`, so `/data` in Docker). Raw readings are kept for 90 days; five-minute averages are kept forever and back the history and energy tools. A failed read is logged and retried on the next poll.

Inputs are recorded as `PW<n>-<letter>` (for example `PW2-A`), using the Powerwall numbers from the permit plans; `site.toml` maps each inverter's serial to its number. With `PW_GW_PWD` set, the collector reads every input on both inverters over TEDAPI. TEDAPI is only served at the gateway's internal address, 192.168.91.1, which on this site is reached through a Wi-Fi bridge next to the gateway (see [bridge/README.md](../bridge/README.md)). Without it, the gateway's local API on current firmware reports only the inputs of one inverter (`local_api_powerwall` in `site.toml`). If TEDAPI stops answering, battery and grid readings keep being recorded and the health check reports the stale string data.

Failures are reported as MCP tool errors, so clients can tell an error from a reading. Neither weather API needs a key.

## Site layout (site.toml)
`site.toml` describes the arrays, panels and which strings feed which Powerwall+ input, taken from the Tesla permit plans. It holds layout only, no credentials or location, and is checked on load: the panels wired to inputs must add up to each array's panel count. Point `SITE_CONFIG` at another file to override it.

## Configuration (.env)
Copy `.env.example` to `src/server/.env` for local runs, and fill it in. The file is gitignored; never commit it.
- `PW_HOST`, `PW_PASSWORD`, `PW_EMAIL`: Powerwall gateway credentials. `PW_EMAIL` is also sent as the contact in the NWS request header.
- `SOLAR_LAT`, `SOLAR_LON`: Home location, used for both the forecast and the alerts. Required; there is no default.
- `PW_TIMEZONE` (optional): defaults to `America/Chicago`.
- `PW_CACHE_DIR` (optional): where pypowerwall keeps its login cache and the collector its database, default the current directory. The Docker image uses `/data`.
- `PW_GW_PWD` (optional): the gateway's Wi-Fi password from its label, for per-string data on both inverters (see Collector).
- `NTFY_URL` (optional): the full ntfy topic URL alerts are posted to, such as `https://ntfy.sh/<your-topic>`. Pick a hard-to-guess topic name: anyone who knows it can read the alerts. `NTFY_TOKEN` is the access token if the topic is protected. Without `NTFY_URL`, alerts are only recorded.
- `HEARTBEAT_URL` (optional): a dead-man check URL, such as a healthchecks.io ping URL, pinged after every health check. If the server or percula stops, the pings stop and that service alerts you.
- `RESERVE_MODE` (optional): `dry-run` (default) or `live`. See Reserve controller.
- `PW_POLL_SECONDS` (optional): collector interval, default 30. `PW_COLLECT=0` turns the collector off; `PW_DB_PATH` overrides the database file.

Put comments on their own lines, not after a value. Docker's `--env-file` does not strip inline comments.

## Running Locally
```bash
cd server
uv run server
```
The client then connects with `MCP_SERVER_URL=http://localhost:8000/mcp`.

These scripts check each data source without starting the server:
```bash
uv run scripts/check_powerwall.py   # Powerwall connection and live readings
uv run scripts/check_forecast.py    # Per-array and total solar forecast
uv run scripts/check_ercot.py       # ERCOT grid condition and how the controller reads it
```

## Tests
```bash
uv run pytest
uv run ruff check .
```
CI runs both for the server and the client on every pull request, on GitHub-hosted runners, plus a gitleaks secret scan.

## Deployment
Pushing changes under `server/` to `main` triggers `.github/workflows/deploy.yml`, which builds the Docker image on the self-hosted runner and restarts the `mcp-server` container on port 8000 with a `powerwall-data` volume on `/data`. The container reads its configuration from the env file on that host, not from the repo, so that file needs the variables above.
