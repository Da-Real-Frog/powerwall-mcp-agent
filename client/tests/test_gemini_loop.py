import asyncio
from types import SimpleNamespace

from google.genai import types

import client


def text_result(text, is_error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], structured_content=None,
                           is_error=is_error)


class FakeChat:
    """Asks for two tools in parallel, then answers once it has both results."""

    def __init__(self):
        self.sent = []

    async def send_message(self, message):
        self.sent.append(message)
        if len(self.sent) == 1:
            return SimpleNamespace(function_calls=[
                SimpleNamespace(name="get_powerwall_status", args={}),
                SimpleNamespace(name="get_solar_forecast", args=None),
            ], text=None)
        return SimpleNamespace(function_calls=None, text="Set the reserve to 40%.")


class FakeSession:
    async def call_tool(self, name, args):
        if name == "get_solar_forecast":
            return text_result("SOLAR_LAT is not set", is_error=True)
        return text_result('{"battery_level_percent": 80}')


def test_parallel_tool_results_go_back_in_one_message(monkeypatch):
    chat = FakeChat()
    fake_client = SimpleNamespace(aio=SimpleNamespace(chats=SimpleNamespace(create=lambda **_: chat)))
    monkeypatch.setattr(client.genai, "Client", lambda: fake_client)

    report = asyncio.run(client.run_gemini(FakeSession(), [], "prompt"))

    assert report == "Set the reserve to 40%."
    assert len(chat.sent) == 2
    responses = chat.sent[1]
    assert [p.function_response.name for p in responses] == ["get_powerwall_status", "get_solar_forecast"]
    assert responses[0].function_response.response == {"result": {"battery_level_percent": 80}}
    assert responses[1].function_response.response == {"error": "SOLAR_LAT is not set"}
    assert all(isinstance(p, types.Part) for p in responses)


def test_retry_gives_up_after_last_attempt(monkeypatch):
    class Failing:
        async def send_message(self, message):
            raise client.ServerError(503, {"error": {"message": "overloaded"}})

    async def no_sleep(_):
        return None

    monkeypatch.setattr(client.asyncio, "sleep", no_sleep)
    try:
        asyncio.run(client.send_message_with_retry(Failing(), "hi", max_retries=2))
    except SystemExit as e:
        assert e.code == 1
    else:
        raise AssertionError("expected the client to exit")
