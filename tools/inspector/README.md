# MCP Inspector launcher (macOS)

One click starts [MCP Inspector](https://github.com/modelcontextprotocol/inspector) already set to the Powerwall server at `http://percula.local:8000/mcp` over streamable HTTP, and opens it in the browser. Press **Connect** and the tools are there.

## Install once
Needs Node.js 22.19 or newer (`brew install node`).
```bash
cd ~/Desktop/LLM/powerwall-mcp-agent/tools/inspector
chmod +x launch-inspector.sh make-app.sh
./make-app.sh
```
This builds **Powerwall Inspector.app** in `~/Applications`. Open it from Spotlight (⌘-Space, type "Powerwall Inspector") or drag it to the Dock. To give it an icon, copy any image, select the app in Finder, press ⌘-I and paste onto the small icon at the top left.

The app runs `launch-inspector.sh` from this folder, so re-run `./make-app.sh` if you move the repo.

## What it does
- Opens a Terminal window and runs `npx @modelcontextprotocol/inspector@2.10.1 --web --transport http --server-url …`. Closing the window or pressing Ctrl-C stops the Inspector.
- If an earlier Inspector is still holding port 6274, it stops that one first.
- Warns if the server is not answering, but starts anyway.

You can also run the script directly: `./launch-inspector.sh`. To inspect another server, set `MCP_SERVER_URL`, for example `MCP_SERVER_URL=http://localhost:8000/mcp ./launch-inspector.sh`. `INSPECTOR_VERSION` picks another Inspector release.

No passwords or keys are involved: the server needs none, and the Inspector creates a new session token on each run.
