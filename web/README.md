# Home Chat

A chat page for phones on the home Wi-Fi: anyone in the house can ask Claude about the batteries, solar panels, weather and grid in plain English. It runs on percula next to the MCP server. On an iPhone it is added to the Home Screen once and then opens like an app, with no App Store, Xcode or Apple developer account.

```text
iPhone (Safari, home Wi-Fi) ──► percula:8080  home chat  ──► percula:8000/mcp  MCP server
                                     │
                                     └──► Claude API (the key lives only here)
```

The page sends the conversation to `POST /api/chat`. This service adds the system prompt, runs Claude's tool loop against the MCP server and streams the answer back. The API key stays in the env file on percula; the phone never sees it. The conversation is kept on the phone (in Safari's storage) until "New chat" is tapped.

It is reachable only from the home network. Anyone on that network can use it, and each question uses the Claude API key, so keep guests on a separate Wi-Fi network if the router has one.

## Running on percula
The `Deploy Home Chat` workflow builds and restarts the `home-chat` container on the self-hosted runner whenever `web/` changes on `main`. Before the first deploy, create its env file on percula, readable only by you:

```bash
install -m 600 /dev/null ~/home-chat.env
nano ~/home-chat.env
```

with one line (no quotes, no comment after it):

```
ANTHROPIC_API_KEY=sk-ant-...
```

The workflow sets `MCP_SERVER_URL` to the MCP server on the same machine and publishes port 8080. `CLAUDE_MODEL` (default `claude-opus-5-5`) and `CLAUDE_EFFORT` (default `medium`) can be added to the env file to override them.

To check it from a Mac on the home network: open `http://percula.local:8080`.

## Adding it to an iPhone
1. On the home Wi-Fi, open Safari and go to `http://percula.local:8080`.
2. Tap the Share button, then **Add to Home Screen**, then **Add**.
3. The **Home Energy** icon (a sun over a battery) now opens the chat full screen.

If `percula.local` does not load on the phone, use percula's IP address instead (`http://192.168.x.x:8080`), ideally after giving percula a reserved address in the router.

## Running locally
```bash
cd web
cp .env.example .env    # then fill in ANTHROPIC_API_KEY
uv run app.py           # http://localhost:8080
```

## Tests
```bash
uv run pytest
uv run ruff check .
```
