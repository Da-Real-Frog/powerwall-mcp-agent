"""Home chat: a web page on the home network where anyone can ask Claude about the Powerwall.

The page (static/index.html) sends the conversation to POST /api/chat. This service holds the Claude API key,
runs the tool loop against the MCP server and streams the answer back as newline-delimited JSON events:
  {"type": "status", "text": "Checking the battery"}   a tool is running
  {"type": "text", "text": "..."}                       a piece of the answer
  {"type": "error", "text": "..."}                      something failed; the answer ends here
  {"type": "done"}                                      the answer is complete

Run with `uv run app.py` (port 8080). The key never reaches the browser.
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack
from datetime import datetime
from pathlib import Path

import anthropic
import uvicorn
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

load_dotenv()

log = logging.getLogger("home-chat")

STATIC = Path(__file__).parent / "static"
DEFAULT_SERVER_URL = "http://percula.local:8000/mcp"
CONNECT_TIMEOUT = 20  # seconds to reach the MCP server and list its tools
TOOL_TIMEOUT = 120  # seconds for one tool call; the forecast tools call out to Open-Meteo and NWS
MAX_TURNS = 40  # messages of history accepted from the page
MAX_CHARS = 4000  # characters per message accepted from the page
MAX_ROUNDS = 12  # model calls per question, so a tool loop cannot run up the bill

# What the page shows while a tool runs, in plain words
TOOL_LABELS = {
    "get_powerwall_status": "Checking the battery",
    "get_solar_forecast": "Looking at the solar forecast",
    "get_active_weather_alerts": "Checking weather alerts",
    "get_site_layout": "Looking at the panels",
    "get_string_status": "Checking the panels",
    "get_history": "Looking at recent history",
    "get_energy_summary": "Adding up the energy",
    "get_health_report": "Running a health check",
    "get_alerts": "Checking for problems",
    "explain_reserve_decision": "Checking the backup reserve",
    "get_reserve_log": "Looking at reserve changes",
}


def system_prompt(tool_count: int) -> str:
    return (
        "You are the friendly assistant for our home's solar panels and Tesla Powerwall batteries. "
        "The person asking is a member of the household, not an engineer, and reads your answer on a phone. "
        f"Answer using the {tool_count} tools of our Powerwall server, which read live readings, history, the "
        "solar forecast, weather alerts, health alerts and the backup reserve controller's decisions. "
        "Call the tools rather than guessing, and call several at once when a question needs them. "
        "Lead with the answer in a sentence or two of everyday language. Avoid jargon: say 'battery' rather "
        "than 'Powerwall SOE', and explain kW or kWh in plain terms the first time if it helps. "
        "Use **bold** and short '- ' bullet lists only; no tables or headings, the screen is narrow. "
        "If something looks wrong or a tool fails, say so calmly in one line and suggest checking with "
        "whoever looks after the system. "
        f"Today is {datetime.now().astimezone():%A %d %B %Y, %H:%M %Z}."
    )


def claude_tools(mcp_tools) -> list[dict]:
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
        }
        for tool in mcp_tools
    ]


def history_content(content) -> list:
    """The assistant turn to keep for the next request in this question's tool loop.

    After a refusal fallback, thinking and tool_use blocks before the last `fallback` block belong to the model
    that declined and are dropped, as the API requires.
    """
    blocks = list(content)
    last_fallback = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    if last_fallback < 0:
        return blocks
    dropped = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > last_fallback or b.type not in dropped]


class BadRequest(ValueError):
    pass


def parse_messages(body) -> list[dict]:
    """The page's conversation as Claude messages: plain text turns, alternating, ending with the question."""
    if not isinstance(body, dict) or not isinstance(body.get("messages"), list):
        raise BadRequest("Expected {\"messages\": [...]}.")
    turns = body["messages"][-MAX_TURNS:]
    messages = []
    for turn in turns:
        if not isinstance(turn, dict) or turn.get("role") not in ("user", "assistant"):
            raise BadRequest("Each message needs a role of user or assistant.")
        text = turn.get("content")
        if not isinstance(text, str) or not text.strip():
            continue  # an answer that was stopped before any text
        if len(text) > MAX_CHARS:
            raise BadRequest(f"Messages are limited to {MAX_CHARS} characters.")
        if messages and messages[-1]["role"] == turn["role"]:
            messages[-1]["content"] += "\n\n" + text  # a question asked again after an error
        else:
            messages.append({"role": turn["role"], "content": text})
    while messages and messages[0]["role"] != "user":
        messages.pop(0)  # trimming to MAX_TURNS can leave an answer first
    if not messages or messages[-1]["role"] != "user":
        raise BadRequest("The conversation has to end with a question.")
    return messages


async def call_tool(session, block) -> dict:
    """Runs one tool_use block on the MCP server and returns its tool_result, errors included."""
    try:
        result = await asyncio.wait_for(session.call_tool(block.name, block.input or {}), TOOL_TIMEOUT)
    except TimeoutError:
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"The server did not answer within {TOOL_TIMEOUT} seconds."}
    except Exception as e:  # a dropped connection should still get an answer
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"Calling the tool failed: {e}"}
    text = "\n".join(c.text for c in result.content if c.type == "text")
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content)
    return {"type": "tool_result", "tool_use_id": block.id, "content": text or "(empty)",
            "is_error": bool(result.is_error)}


def event(type_: str, text: str | None = None) -> bytes:
    return (json.dumps({"type": type_} if text is None else {"type": type_, "text": text}) + "\n").encode()


async def answer(client, session, tools: list[dict], system: str, messages: list):
    """Runs the tool loop for the question at the end of `messages`, yielding page events."""
    for _ in range(MAX_ROUNDS):
        async with client.beta.messages.stream(
            model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": os.getenv("CLAUDE_EFFORT", "medium")},
            # If a safety classifier declines, the API retries on its recommended fallback model
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        ) as stream:
            async for ev in stream:
                if ev.type == "text":
                    yield event("text", ev.text)
            response = await stream.get_final_message()
        messages.append({"role": "assistant", "content": history_content(response.content)})

        if response.stop_reason == "refusal":
            yield event("error", "Claude declined to answer that one.")
            return
        if response.stop_reason == "max_tokens":
            yield event("error", "The answer got too long and was cut off.")
            return
        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            yield event("done")
            return

        labels = dict.fromkeys(TOOL_LABELS.get(b.name, "Looking into it") for b in tool_uses)
        yield event("status", ", ".join(labels) + "…")
        # Run the tools Claude asked for in parallel; all results go back in one message
        results = await asyncio.gather(*(call_tool(session, b) for b in tool_uses))
        messages.append({"role": "user", "content": list(results)})
    yield event("error", "That question took too many steps. Try asking it more simply.")


async def open_mcp(stack: AsyncExitStack, url: str):
    """Connects to the MCP server and returns the session and its tools."""
    read, write, *_ = await stack.enter_async_context(streamable_http_client(url))
    session = await stack.enter_async_context(ClientSession(read, write))
    await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT)
    tools = (await asyncio.wait_for(session.list_tools(), CONNECT_TIMEOUT)).tools
    return session, tools


async def chat_events(client, server_url: str, messages: list):
    """One question, start to finish, as page events. Errors become an error event, never a broken stream."""
    try:
        async with AsyncExitStack() as stack:
            try:
                session, mcp_tools = await open_mcp(stack, server_url)
            except Exception as e:  # the transport wraps connection failures in exception groups
                log.warning("MCP server unreachable at %s: %r", server_url, e)
                yield event("error", "I can't reach the solar server right now. Please try again in a minute.")
                return
            tools = claude_tools(mcp_tools)
            async for chunk in answer(client, session, tools, system_prompt(len(tools)), messages):
                yield chunk
    except anthropic.AuthenticationError:
        log.error("The Claude API rejected the key")
        yield event("error", "The assistant isn't set up correctly (the API key was refused).")
    except anthropic.APIStatusError as e:
        log.error("Claude API error %s: %s", e.status_code, e.message)
        yield event("error", "The assistant is busy or unavailable. Please try again shortly.")
    except anthropic.APIConnectionError as e:
        log.error("Could not reach the Claude API: %s", e)
        yield event("error", "The house can't reach the assistant service. Is the internet up?")


def create_app(client=None, server_url: str | None = None) -> Starlette:
    server_url = server_url or os.getenv("MCP_SERVER_URL", DEFAULT_SERVER_URL)
    if client is None:
        key = os.getenv("ANTHROPIC_API_KEY")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY is not set. Put it in web/.env or the Docker env file.")
        client = anthropic.AsyncAnthropic(api_key=key, max_retries=4)

    async def index(request: Request):
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    async def chat(request: Request):
        try:
            messages = parse_messages(await request.json())
        except (BadRequest, ValueError) as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        return StreamingResponse(chat_events(client, server_url, messages), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    async def health(request: Request):
        return JSONResponse({"ok": True})

    return Starlette(routes=[
        Route("/", index),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/healthz", health),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
