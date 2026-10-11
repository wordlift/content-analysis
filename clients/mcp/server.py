"""Thin MCP adapter for the hosted WordLift resolve() service.

No resolution decisions are made here. Credentials are supplied by the operator
and never exposed as MCP tool parameters.
"""
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

#: The WordLift API. Requests go to POST {BASE_URL}/accounts/{accountId}/resolve, the
#: gateway's metered route; the account id is read from the key once (GET /accounts/me)
#: unless WL_ACCOUNT_ID is set. WL_RESOLVE_ENDPOINT sends every request to one URL as
#: given instead (the engine's older address https://resolve.wordlift.io/v1/resolve is
#: forwarded to the same metered route).
BASE_URL = os.environ.get("WL_BASE_URL", "https://api.wordlift.io").rstrip("/")
mcp = FastMCP("WordLift resolve")
_accounts: dict[str, str] = {}   # key -> account id, for the life of the process


async def endpoint_for(client: httpx.AsyncClient, api_key: str, headers: dict[str, str]) -> str:
    """Where this key's requests go; the account lookup happens once per key."""
    fixed = os.environ.get("WL_RESOLVE_ENDPOINT")
    if fixed:
        return fixed
    account = os.environ.get("WL_ACCOUNT_ID") or _accounts.get(api_key)
    if not account:
        me = await client.get(f"{BASE_URL}/accounts/me", headers=headers)
        me.raise_for_status()
        account = me.json().get("accountId")
        if account is None:
            raise RuntimeError(f"{BASE_URL}/accounts/me returned no accountId")
        _accounts[api_key] = str(account)
    return f"{BASE_URL}/accounts/{account}/resolve"


async def call_resolve(payload: dict[str, Any], *, key: str | None = None,
                       transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Forward a request unchanged to the account's resolve route; fail closed on upstream errors."""
    api_key = key or os.environ.get("WL_KEY")
    if not api_key:
        raise RuntimeError("WL_KEY is required")
    headers = {"Authorization": f"Key {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
        response = await client.post(await endpoint_for(client, api_key, headers), headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


@mcp.tool()
async def resolve_text(
    text: str,
    language: str | None = None,
    dataset_uri: str | None = None,
    mentions: list[dict[str, Any]] | None = None,
    dataset: dict[str, Any] | None = None,
    include: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve text mentions into entity identities or explicitly unresolved results.

    Omitting mentions lets the engine detect them. Use include=['candidates',
    'evidence'] for diagnostics. Scores are not uniformly calibrated across
    resolution paths. A NIL result must never be replaced with a guessed link.
    """
    if not text.strip():
        raise ValueError("text must not be empty")
    payload: dict[str, Any] = {"text": text}
    for field, value in (
        ("language", language), ("dataset_uri", dataset_uri),
        ("mentions", mentions), ("dataset", dataset), ("include", include)
    ):
        if value is not None:
            payload[field] = value
    return await call_resolve(payload)


def main() -> None:
    # Local stdio only. A public HTTP deployment requires OAuth, per-user key
    # mapping and tenant isolation; do not expose this process publicly as-is.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
