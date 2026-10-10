"""Offline contract tests for the thin MCP adapter."""
import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("resolve_mcp", Path(__file__).with_name("server.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.asyncio
async def test_forwards_request_and_preserves_nil():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"mentions": [{"text": "Unknown", "status": "unresolved",
            "entity": None, "reason": "no_candidates"}]})

    payload = {"text": "Unknown", "include": ["evidence"]}
    result = await module.call_resolve(payload, key="test-only",
        transport=httpx.MockTransport(handler))
    assert seen == {"url": module.ENDPOINT, "auth": "Key test-only", "json": payload}
    assert result["mentions"][0]["status"] == "unresolved"
    assert result["mentions"][0]["reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_upstream_failure_does_not_guess():
    def handler(request):
        return httpx.Response(503, json={"detail": "unavailable"})
    with pytest.raises(httpx.HTTPStatusError):
        await module.call_resolve({"text": "Apple"}, key="test-only",
            transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_missing_key(monkeypatch):
    monkeypatch.delenv("WL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WL_KEY"):
        await module.call_resolve({"text": "Apple"})
