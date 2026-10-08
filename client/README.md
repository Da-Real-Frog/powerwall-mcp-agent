# Powerwall MCP Agent Client

An autonomous AI client that communicates with the Powerwall MCP Server over SSE. It dynamically discovers tools, evaluates the prompt, and loops through tool execution using either Gemini (Google Genai SDK) or Claude (Anthropic SDK).

The prompt is fixed in `client.py`: it asks the agent to check the battery level, the solar forecast and active weather alerts, then recommend a backup reserve percentage for the night, prioritizing backup readiness when severe weather threatens the grid.

## Setup & Execution
1. Configure your `.env` file:
   ```bash
   MCP_SERVER_URL=http://<server-host>:8000/sse
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

## Error Handling
- Gemini: quota errors are parsed for the wait time; the client sleeps and retries for waits up to 2 minutes and exits on longer ones. Transient server errors are retried with exponential backoff.
- Claude: the Anthropic SDK retries rate limits and server errors itself (4 retries); the client exits with a message on authentication, rate-limit or connection failures.
