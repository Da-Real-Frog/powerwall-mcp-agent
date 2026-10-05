# Gemini MCP Agent Client

An autonomous AI client that communicates with the Powerwall MCP Server using standard I/O (stdio). It dynamically discovers tools, evaluates user prompts, and loops through tool execution using the Google Genai SDK (`gemini-3.8-flash`).

## Setup & Execution
1. Ensure your `GEMINI_API_KEY` is set in your `.env` file.
2. Run the agent using `uv`:
   ```bash
   uv run client.py
