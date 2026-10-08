# Powerwall MCP Agent

Trying to figure out how to configure my batteries based on the weather forecast.

An AI workflow lab integrating Model Context Protocol (MCP), local Tesla Powerwall telemetry, multi-array solar forecasting (Open-Meteo), National Weather Service alerts, and an LLM agent running on either Gemini or Claude.

## Project Structure
- `server/`: Python FastMCP server exposing the Powerwall status, the 3-array solar forecast and active weather alerts as MCP tools over SSE. See [server/README.md](server/README.md).
- `client/`: Agent client that discovers those tools, calls them, and recommends a backup reserve for the night. The LLM provider (Gemini or Claude) is selected in its `.env` file. See [client/README.md](client/README.md).
- `.github/workflows/deploy.yml`: Rebuilds and restarts the server's Docker container on a self-hosted runner whenever `server/` changes on `main`.

## Quick Start
1. Start the server (see [server/README.md](server/README.md)), or let the deploy workflow do it.
2. Point the client at it with `MCP_SERVER_URL` and run `uv run client.py` from `client/`.

`.env` files hold credentials and are ignored by git; each README lists the variables it needs.

More to follow as I will then start collecting the status of all strings and compare them against the solar forecast...
