import os
import json
import asyncio
import re
import sys
from mcp import ClientSession
from mcp.client.sse import sse_client
import anthropic
from google import genai
from google.genai import types
from google.genai.errors import APIError, ServerError
from dotenv import load_dotenv

# Load environment variables from .env
load_dotenv()

async def send_message_with_retry(chat, message, max_retries=4, initial_delay=5):
    """Sends a message with smart quota parsing and exponential backoff."""
    for attempt in range(1, max_retries + 1):
        try:
            return chat.send_message(message)
        except (ServerError, APIError) as e:
            error_msg = e.message if hasattr(e, 'message') else str(e)
            
            # 1. Handle Hard Quota Limits (429)
            if "Quota exceeded" in error_msg or "429" in error_msg:
                print(f"\n⚠️ Gemini API Quota Exceeded")
                
                # Parse the "retry in 1h30m23.8s" string
                match = re.search(r'retry in (?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?', error_msg)
                if match:
                    hours = int(match.group(1) or 0)
                    minutes = int(match.group(2) or 0)
                    seconds = float(match.group(3) or 0.0)
                    wait_seconds = (hours * 3600) + (minutes * 60) + seconds
                    
                    # If the wait is longer than 2 minutes, abort to prevent an SSE timeout
                    if wait_seconds > 120:
                        print(f"❌ Wait time ({wait_seconds/60:.1f} minutes) is too long.")
                        print("Holding the connection open will cause a network timeout.")
                        print("Please close the script and run it again later.")
                        sys.exit(1)
                        
                    print(f"⏳ Sleeping for {wait_seconds:.1f} seconds to reset quota...")
                    await asyncio.sleep(wait_seconds + 1)
                    continue
                else:
                    print("❌ Quota exceeded with unknown wait format. Exiting.")
                    sys.exit(1)
            
            # 2. Handle Transient Server Demand (503)
            print(f"⚠️ Gemini API error: {error_msg}")
            if attempt < max_retries:
                wait_time = initial_delay * (2 ** (attempt - 1))
                print(f"⏳ Transient error. Retrying in {wait_time}s (Attempt {attempt}/{max_retries})...")
                await asyncio.sleep(wait_time)
            else:
                print("❌ Max retries reached. Exiting.")
                os._exit(1)


PROMPT = (
    "You are my home energy manager. Evaluate my power situation for tonight.\n"
    "1. Check my current battery level.\n"
    "2. Check the solar forecast across my 3 arrays.\n"
    "3. Check for active National Weather Service alerts.\n"
    "Synthesize this data. If there are severe weather warnings (like storms or freezes) that could threaten grid stability, "
    "prioritize backup readiness over cost and recommend charging the Powerwalls. Otherwise, base your recommendation on the solar forecast and give me an idea of what percentage I should use as a reserve."
)


async def run_gemini(session, mcp_tools, prompt):
    """Runs the agent loop against Gemini and returns the final report."""
    gemini_function_declarations = []

    for tool in mcp_tools:
        # Clean the schema to meet Gemini's strict OpenAPI requirements
        schema = tool.input_schema.copy() if tool.input_schema else {}
        schema.pop("additionalProperties", None)
        schema.pop("additional_properties", None)

        gemini_function_declarations.append(
            types.FunctionDeclaration(
                name=tool.name,
                description=tool.description,
                parameters=schema
            )
        )

    agent_tools = types.Tool(function_declarations=gemini_function_declarations)

    client = genai.Client()
    chat = client.chats.create(
        model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
        config=types.GenerateContentConfig(
            tools=[agent_tools],
            temperature=0.2,
        )
    )

    response = await send_message_with_retry(chat, prompt)

    while response.function_calls:
        for call in response.function_calls:
            print(f"🤖 Agent is executing tool: [{call.name}]")

            # Execute the requested tool via the MCP session
            result = await session.call_tool(call.name, call.args)

            # Extract the JSON string from the MCP TextContent block
            tool_output_string = result.content[0].text

            # Feed the raw telemetry back to the LLM using the retry wrapper
            response = await send_message_with_retry(
                chat,
                types.Part.from_function_response(
                    name=call.name,
                    response={"result": json.loads(tool_output_string)}
                )
            )

    return response.text


async def run_claude(session, mcp_tools, prompt):
    """Runs the agent loop against Claude and returns the final report."""
    claude_tools = [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema or {"type": "object", "properties": {}},
        }
        for tool in mcp_tools
    ]

    # The SDK retries 429s and 5xx with backoff on its own
    client = anthropic.AsyncAnthropic(max_retries=4)
    messages = [{"role": "user", "content": prompt}]

    while True:
        try:
            response = await client.messages.create(
                model=os.getenv("CLAUDE_MODEL", "claude-opus-5-5"),
                max_tokens=16000,
                tools=claude_tools,
                messages=messages,
            )
        except anthropic.AuthenticationError:
            print("❌ Claude API rejected the credentials. Check ANTHROPIC_API_KEY in the .env file.")
            sys.exit(1)
        except anthropic.RateLimitError as e:
            print(f"❌ Claude API rate limit still exceeded after retries: {e.message}")
            sys.exit(1)
        except anthropic.APIStatusError as e:
            print(f"❌ Claude API error ({e.status_code}): {e.message}")
            sys.exit(1)
        except anthropic.APIConnectionError as e:
            print(f"❌ Could not reach the Claude API: {e}")
            sys.exit(1)

        if response.stop_reason != "tool_use":
            break

        # Keep the full content (thinking + tool_use blocks) in the history
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            print(f"🤖 Agent is executing tool: [{block.name}]")

            # Execute the requested tool via the MCP session
            result = await session.call_tool(block.name, block.input)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": "\n".join(c.text for c in result.content if c.type == "text"),
                "is_error": bool(result.is_error),
            })

        # All results for one turn go back in a single user message
        messages.append({"role": "user", "content": tool_results})

    if response.stop_reason == "refusal":
        print("❌ Claude declined to answer this request.")
        sys.exit(1)
    if response.stop_reason == "max_tokens":
        print("⚠️ Claude hit the max_tokens limit, the report below is truncated.")

    return "".join(block.text for block in response.content if block.type == "text")


PROVIDERS = {"gemini": run_gemini, "claude": run_claude}


async def run_agent():
    # Fetch the server URL and the LLM provider from the .env file
    server_url = os.getenv("MCP_SERVER_URL")
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()

    if not server_url:
        raise ValueError("❌ MCP_SERVER_URL is not defined in the .env file.")
    if provider not in PROVIDERS:
        raise ValueError(f"❌ LLM_PROVIDER must be one of {sorted(PROVIDERS)}, got '{provider}'.")

    print(f"🔌 Connecting to remote MCP Server at {server_url}...")

    # Connect over the network using SSE
    async with sse_client(server_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("✅ Remote MCP Session initialized.\n")

            # 1. Discover tools dynamically from the MCP server
            mcp_tools = (await session.list_tools()).tools

            print(f"🧩 LLM provider: {provider}")
            print(f"👤 User: {PROMPT}\n")

            # 2. Agent Execution Loop
            report = await PROVIDERS[provider](session, mcp_tools, PROMPT)

            print(f"\n🧠 Agent Final Report:\n{report}")

if __name__ == "__main__":
    asyncio.run(run_agent())
