import asyncio
from types import SimpleNamespace

import chat


def block(type_, **fields):
    return SimpleNamespace(type=type_, **fields)


def text_result(text, is_error=False):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], structured_content=None,
                           is_error=is_error)


class FakeStream:
    def __init__(self, message):
        self.message = message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self._events()

    async def _events(self):
        for b in self.message.content:
            if b.type == "text":
                yield SimpleNamespace(type="text", text=b.text)

    async def get_final_message(self):
        return self.message


class FakeClaude:
    """Returns the queued messages in order and records each request."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request):
        self.requests.append({**request, "messages": list(request["messages"])})
        return FakeStream(self.replies.pop(0))


class FakeSession:
    def __init__(self):
        self.calls = []

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        if name == "get_solar_forecast":
            return text_result("SOLAR_LAT is not set", is_error=True)
        return text_result('{"battery_level_percent": 80}')


def test_parallel_tools_then_answer():
    claude = FakeClaude(
        SimpleNamespace(stop_reason="tool_use", content=[
            block("thinking", thinking="", signature="sig"),
            block("tool_use", id="t1", name="get_powerwall_status", input={}),
            block("tool_use", id="t2", name="get_solar_forecast", input={}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[block("text", text="The battery is at **80%**.")]),
    )
    session = FakeSession()
    messages = [{"role": "user", "content": "How full is the battery?"}]

    asyncio.run(chat.answer(claude, session, [], "system", messages))

    assert [c[0] for c in session.calls] == ["get_powerwall_status", "get_solar_forecast"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    # Thinking blocks stay in the history unchanged
    assert messages[1]["content"][0].type == "thinking"
    results = messages[2]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]
    assert results[0]["is_error"] is False and results[1]["is_error"] is True
    assert results[1]["content"] == "SOLAR_LAT is not set"
    assert claude.requests[0]["fallbacks"] == "default"
    assert claude.requests[0]["model"] == "claude-opus-5-5"


def test_slow_tool_becomes_an_error_result(monkeypatch):
    class Hanging:
        async def call_tool(self, name, args):
            await asyncio.sleep(10)

    monkeypatch.setattr(chat, "TOOL_TIMEOUT", 0.01)
    result = asyncio.run(chat.call_tool(Hanging(), block("tool_use", id="t1", name="get_history", input={"hours": 6})))

    assert result["is_error"] is True
    assert "did not answer" in result["content"]


def test_history_drops_declined_blocks_before_fallback():
    content = [
        block("thinking", thinking=""),
        block("tool_use", id="old"),
        block("text", text="partial"),
        block("fallback"),
        block("thinking", thinking=""),
        block("text", text="answer"),
    ]

    kept = chat.history_content(content)

    assert [b.type for b in kept] == ["text", "fallback", "thinking", "text"]


def test_history_unchanged_without_fallback():
    content = [block("thinking", thinking=""), block("text", text="hi")]

    assert chat.history_content(content) == content


def test_root_cause_unwraps_groups():
    err = ConnectionError("refused")

    assert chat.root_cause(ExceptionGroup("outer", [ExceptionGroup("inner", [err])])) is err
