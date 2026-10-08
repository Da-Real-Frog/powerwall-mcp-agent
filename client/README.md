# Powerwall MCP Agent Client

An autonomous AI client that communicates with the Powerwall MCP Server over SSE. It dynamically discovers tools, evaluates the prompt, and loops through tool execution using either Gemini (Google Genai SDK) or Claude (Anthropic SDK).

## Setup & Execution
1. Configure your `.env` file:
   ```bash
   MCP_SERVER_URL=...
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
