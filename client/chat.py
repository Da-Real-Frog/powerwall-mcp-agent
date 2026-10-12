"""Interactive terminal chat with Claude about the Powerwall, using the MCP server's tools.

Run with `uv run chat.py`. Type a question, press Enter, and Claude answers by calling the server's tools.
Commands: /tools, /clear, /help, /quit (or Ctrl-D). Ctrl-C stops the current answer.
"""

import asyncio
import json
import os
import platform
import readline
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

load_dotenv()

DEFAULT_SERVER_URL = "http://percula.local:8000/mcp"
KEYCHAIN_SERVICE = "anthropic-api-key"
HISTORY_FILE = Path.home() / ".powerwall_chat_history"
CONNECT_TIMEOUT = 20  # seconds to reach the MCP server and list its tools
TOOL_TIMEOUT = 120  # seconds for one tool call; the forecast tools call out to Open-Meteo and NWS

HELP = """Ask anything about the batteries, solar, grid or weather, in plain English.
  /tools   list the server's tools
  /clear   forget the conversation so far
  /quit    exit (Ctrl-D works too)
Ctrl-C stops the current answer."""

console = Console()


def api_key() -> str | None:
    """ANTHROPIC_API_KEY from the environment or .env, else the macOS Keychain. Never from a committed file."""
    key = os.getenv("ANTHROPIC_API_KEY")
    if key:
        return key
    if platform.system() != "Darwin":
        return None
    account = os.getenv("USER", "")
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-a", account, "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def system_prompt(tool_count: int) -> str:
    return (
        "You are the assistant for my home solar and Tesla Powerwall system. I ask questions in a terminal; "
        f"you answer them using the {tool_count} tools of my Powerwall MCP server, which read live telemetry, "
        "history, the solar forecast, NWS weather alerts, health alerts and the reserve controller's decisions. "
        "Call the tools rather than guessing, and call several at once when a question needs them. "
        "If a tool returns an error, say what failed in one line and answer with what you have. "
        "Answer briefly and lead with the answer; give units (kW, kWh, %) and local times. "
        "The output is rendered as Markdown in a terminal, so keep tables narrow. "
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
    """The assistant turn to keep in the history.

    Keeps thinking and tool_use blocks unchanged so the next request can reuse them. After a refusal fallback,
    thinking and tool_use blocks before the last `fallback` block belong to the model that declined and are
    dropped, as the API requires.
    """
    blocks = list(content)
    last_fallback = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    if last_fallback < 0:
        return blocks
    dropped = {"thinking", "redacted_thinking", "tool_use"}
    return [b for i, b in enumerate(blocks) if i > last_fallback or b.type not in dropped]


async def call_tool(session, block) -> dict:
    """Runs one tool_use block on the MCP server and returns its tool_result, errors included."""
    args = ", ".join(f"{k}={v!r}" for k, v in (block.input or {}).items())
    console.print(f"[dim]  ↳ {block.name}({args})[/dim]")
    try:
        result = await asyncio.wait_for(session.call_tool(block.name, block.input or {}), TOOL_TIMEOUT)
    except TimeoutError:
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"The server did not answer within {TOOL_TIMEOUT} seconds."}
    except Exception as e:  # a dropped connection should not end the chat
        return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                "content": f"Calling the tool failed: {e}"}
    text = "\n".join(c.text for c in result.content if c.type == "text")
    if not text and result.structured_content is not None:
        text = json.dumps(result.structured_content)
    return {"type": "tool_result", "tool_use_id": block.id, "content": text or "(empty)",
            "is_error": bool(result.is_error)}


async def stream_turn(client, request: dict):
    """Streams one model response, rendering its text as Markdown as it arrives, and returns the final message."""
    text = ""
    async with client.beta.messages.stream(**request) as stream:
        with Live(console=console, refresh_per_second=8, vertical_overflow="visible") as live:
            live.update("[dim]…[/dim]")
            async for event in stream:
                if event.type == "text":
                    text += event.text
                    live.update(Markdown(text))
            if not text:
                live.update("")
        return await stream.get_final_message()


async def answer(client, session, tools: list[dict], system: str, messages: list) -> None:
    """Runs the tool loop for the question at the end of `messages`, appending every turn to it."""
    while True:
        response = await stream_turn(client, {
            "model": os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
            "max_tokens": 32000,
            "system": system,
            "tools": tools,
            "messages": messages,
            "output_config": {"effort": os.getenv("CLAUDE_EFFORT", "medium")},
            # If a safety classifier declines, the API retries on its recommended fallback model
            "betas": ["server-side-fallback-2026-07-01"],
            "fallbacks": "default",
        })
        messages.append({"role": "assistant", "content": history_content(response.content)})

        if response.stop_reason == "refusal":
            console.print("[yellow]Claude declined to answer that one.[/yellow]")
            return
        if response.stop_reason == "max_tokens":
            console.print("[yellow]The answer hit the length limit and is cut off.[/yellow]")
            return
        if response.stop_reason == "pause_turn":
            continue
        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if response.stop_reason != "tool_use" or not tool_uses:
            return

        # Run the tools Claude asked for in parallel; all results go back in one message
        results = await asyncio.gather(*(call_tool(session, b) for b in tool_uses))
        messages.append({"role": "user", "content": list(results)})


def root_cause(e: BaseException) -> BaseException:
    """The first leaf exception inside nested exception groups."""
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


def read_line() -> str:
    return input("\n› ")


async def chat(session, client, mcp_tools) -> None:
    tools = claude_tools(mcp_tools)
    system = system_prompt(len(tools))
    messages: list = []
    loop = asyncio.get_running_loop()

    while True:
        try:
            line = (await asyncio.to_thread(read_line)).strip()
        except EOFError:
            console.print()
            return
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return
        if line == "/help":
            console.print(HELP)
            continue
        if line == "/clear":
            messages.clear()
            console.print("[dim]Conversation cleared.[/dim]")
            continue
        if line == "/tools":
            for tool in mcp_tools:
                first_line = (tool.description or "").strip().splitlines()[0] if tool.description else ""
                console.print(f"[bold]{tool.name}[/bold]  [dim]{first_line}[/dim]")
            continue

        # Ctrl-C cancels this answer only; the history goes back to before the question
        start = len(messages)
        messages.append({"role": "user", "content": line})
        task = asyncio.ensure_future(answer(client, session, tools, system, messages))
        loop.add_signal_handler(signal.SIGINT, task.cancel)
        try:
            await task
        except asyncio.CancelledError:
            del messages[start:]
            console.print("\n[dim]Stopped.[/dim]")
        except anthropic.AuthenticationError:
            del messages[start:]
            console.print("[red]The Claude API rejected the key. Check ANTHROPIC_API_KEY or the Keychain entry.[/red]")
        except anthropic.APIStatusError as e:
            del messages[start:]
            console.print(f"[red]Claude API error ({e.status_code}): {e.message}[/red]")
        except anthropic.APIConnectionError as e:
            del messages[start:]
            console.print(f"[red]Could not reach the Claude API: {e}[/red]")
        finally:
            loop.remove_signal_handler(signal.SIGINT)


async def run() -> int:
    server_url = os.getenv("MCP_SERVER_URL", DEFAULT_SERVER_URL)
    key = api_key()
    if not key:
        console.print(
            "[red]No Claude API key found.[/red] Set ANTHROPIC_API_KEY in client/.env, or store it in the Keychain:\n"
            f'  security add-generic-password -s {KEYCHAIN_SERVICE} -a "$USER" -w'
        )
        return 1
    client = anthropic.AsyncAnthropic(api_key=key, max_retries=4)

    try:
        HISTORY_FILE.touch(exist_ok=True)
        readline.read_history_file(HISTORY_FILE)
    except OSError:
        pass

    console.print(f"[dim]Connecting to {server_url} …[/dim]")
    try:
        async with streamable_http_client(server_url) as (read, write):
            async with ClientSession(read, write) as session:
                try:
                    await asyncio.wait_for(session.initialize(), CONNECT_TIMEOUT)
                    mcp_tools = (await asyncio.wait_for(session.list_tools(), CONNECT_TIMEOUT)).tools
                except TimeoutError:
                    console.print(f"[red]The server at {server_url} did not answer within {CONNECT_TIMEOUT}s.[/red]")
                    return 1
                console.print(f"[green]Connected[/green], {len(mcp_tools)} tools. Type /help for commands.")
                await chat(session, client, mcp_tools)
    except Exception as e:  # the transport wraps connection failures in an exception group
        console.print(f"[red]Could not connect to {server_url}: {root_cause(e)}[/red]")
        console.print("[dim]Is the Mac on the home network, and is the server running on percula?[/dim]")
        return 1
    finally:
        try:
            readline.set_history_length(1000)
            readline.write_history_file(HISTORY_FILE)
        except OSError:
            pass
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
