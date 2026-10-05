# powewall-mcp-agent
Trying to figure how to configure my batteries based on weatger forecast

# Powerwall MCP Agent

An enterprise AI workflow lab integrating Model Context Protocol (MCP), local Tesla Powerwall telemetry, multi-array solar weather forecasting (`forecast.solar`), and Gemini agents.

## Project Structure
- `server/`: Python FastMCP server handling local Powerwall queries and 3-array solar estimation.
- `client/`: Gemini-powered agent client orchestrating autonomous energy management decisions.
