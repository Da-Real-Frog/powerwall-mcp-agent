# Powerwall MCP Agent Client

Two clients for the Powerwall MCP Server: `chat.py`, an interactive terminal chat (below), and `client.py`, an autonomous AI client that communicates with the Powerwall MCP Server over streamable HTTP. It dynamically discovers tools, evaluates the prompt, and loops through tool execution using either Gemini (Google Genai SDK) or Claude (Anthropic SDK).

The prompt is fixed in `client.py`: it asks the agent to check the battery level, the solar forecast and active weather alerts, then recommend a backup reserve percentage for the night, prioritizing backup readiness when severe weather threatens the grid.

## Setup & Execution
1. Configure your `.env` file:
   ```bash
   MCP_SERVER_URL=http://<server-host>:8000/mcp
   LLM_PROVIDER=gemini          # gemini (default) or claude
   GEMINI_API_KEY=...           # when LLM_PROVIDER=gemini
   ANTHROPIC_API_KEY=...        # when LLM_PROVIDER=claude
   # Optional model overrides
   # GEMINI_MODEL=gemini-3.8-flash
   # CLAUDE_MODEL=claude-opus-5-5
   ```
2. Run the agent using `uv`:
   ```bash
   uv run client.py
   ```

## Interactive chat (`chat.py`)
`chat.py` is a terminal chat with Claude: you type questions in plain English and Claude answers by calling the server's tools, with the conversation kept between questions.

```text
$ uv run chat.py
Connected, 11 tools. Type /help for commands.

› How much did the panels make today compared with the forecast?
  ↳ get_energy_summary(days=1)
  ↳ get_solar_forecast()
...
```

1. The server URL defaults to `http://percula.local:8000/mcp`; set `MCP_SERVER_URL` in `.env` to use another one. The Mac has to be on the home network.
2. The Claude API key is read from `ANTHROPIC_API_KEY` (the environment or `.env`), and otherwise from the macOS Keychain. To keep it in the Keychain instead of a file, run this once and paste the key at the prompt:
   ```bash
   security add-generic-password -s anthropic-api-key -a "$USER" -w
   ```
3. Run it from `client/`:
   ```bash
   uv run chat.py
   ```
   To start it from any folder, add an alias to `~/.zshrc`: `alias pw='uv run --project ~/Desktop/LLM/powerwall-mcp-agent/client ~/Desktop/LLM/powerwall-mcp-agent/client/chat.py'`.

Commands: `/tools` lists the server's tools, `/clear` starts a fresh conversation, `/quit` or Ctrl-D exits, and Ctrl-C stops the answer in progress. Input history is kept in `~/.powerwall_chat_history`.

It uses `claude-opus-5-5` at `medium` effort; `CLAUDE_MODEL` and `CLAUDE_EFFORT` (`low` to `max`) override them. If a safety classifier declines a question, the API answers it with its recommended fallback model instead (`fallbacks: "default"`). A tool that fails or takes more than two minutes is reported to Claude as an error and the chat carries on.

No build is needed for the alternative: with Claude Code installed, `claude mcp add --transport http powerwall http://percula.local:8000/mcp` once, then `claude` in any terminal.

## Error Handling
- Gemini: quota errors are parsed for the wait time; the client sleeps and retries for waits up to 2 minutes and exits on longer ones. Transient server errors are retried with exponential backoff.
- Gemini: when it asks for several tools at once, all their results go back in a single message.
- Tool errors from the server are passed to the model as errors, not as data.
- Claude: the Anthropic SDK retries rate limits and server errors itself (4 retries); the client exits with a message on authentication, rate-limit or connection failures.

## Tests
```bash
uv run pytest
uv run ruff check .
```
