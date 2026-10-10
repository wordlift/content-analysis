# WordLift resolve() MCP adapter (experimental)

A thin, open-source MCP **server** exposing the hosted proprietary resolve()
engine. This adapter does not ship the engine, models, thresholds or indexes.

It calls the existing public API:
`POST https://resolve.wordlift.io/v1/resolve`
with `Authorization: Key <WL_KEY>`. No production endpoint changes required.

## Local use (stdio)

From the repository root:

```bash
python -m venv .venv
.venv/bin/pip install -r clients/mcp/requirements.txt
export WL_KEY='your-wordlift-key'
.venv/bin/python clients/mcp/server.py
```

Configure a local MCP client to launch the same Python command, with `WL_KEY`
in its environment. Do not commit the key or put it in tool arguments.
Tool: `resolve_text` with `text`, optional `language`, `dataset_uri`,
`mentions`, `dataset` and `include`. The response is the engine's JSON,
including `unresolved` / NIL reasons and optional evidence.

Example input:

```json
{"text":"Andrea Volpini founded WordLift in Rome.","include":["candidates","evidence"]}
```

## Deliberately not yet a remote ChatGPT connector

This prototype uses **stdio** and a server-side `WL_KEY`. It is useful for
local MCP clients and automated tests. ChatGPT's remote MCP connection needs
a deployed Streamable HTTP MCP endpoint with OAuth authorization, token
validation and an authenticated-user-to-WordLift-key/account mapping.
**Do not deploy this adapter as an unauthenticated public HTTP server.**
In particular, a single shared `WL_KEY` is not suitable for multi-tenant
customer access.

The old API stays untouched until endpoint/auth decisions are agreed.
No URL fetching, crawling, batch fan-out or inferred identities are added.
The model can supply text, explicit mentions and datasets within the existing
API contract. Transport/HTTP failures are surfaced as errors, never converted
to successful or guessed resolutions.

## Tests

```bash
.venv/bin/pip install pytest pytest-asyncio
.venv/bin/python -m pytest clients/mcp/test_server.py
```

Tests use httpx.MockTransport and never make network calls.
