import asyncio

from fastmcp import Client

from server.server import mcp


def test_tools_are_listed():
    async def run():
        async with Client(mcp) as client:
            return {t.name for t in await client.list_tools()}

    assert asyncio.run(run()) == {"get_powerwall_status", "get_solar_forecast", "get_active_weather_alerts"}


def test_missing_location_is_reported_as_a_tool_error(monkeypatch):
    monkeypatch.delenv("SOLAR_LAT", raising=False)
    monkeypatch.delenv("SOLAR_LON", raising=False)

    async def run():
        async with Client(mcp) as client:
            return await client.call_tool("get_solar_forecast", {}, raise_on_error=False)

    result = asyncio.run(run())
    assert result.is_error
    assert "SOLAR_LAT" in result.content[0].text
