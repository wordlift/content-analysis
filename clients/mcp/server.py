"""Thin MCP adapter for the hosted WordLift resolve() service.

No resolution decisions are made here. Credentials are supplied by the operator
and never exposed as MCP tool parameters.
"""
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

ENDPOINT = "https://resolve.wordlift.io/v1/resolve"
mcp = FastMCP("WordLift resolve")


async def call_resolve(payload: dict[str, Any], *, key: str | None = None,
                       transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """Forward a request unchanged; fail closed on upstream errors."""
    api_key = key or os.environ.get("WL_KEY")
    if not api_key:
        raise RuntimeError("WL_KEY is required")
    async with httpx.AsyncClient(transport=transport, timeout=60.0) as client:
        response = await client.post(
            ENDPOINT,
            headers={"Authorization": f"Key {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
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
