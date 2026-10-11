"""Offline contract tests for the thin MCP adapter."""
import importlib.util
from pathlib import Path

import httpx
import pytest

spec = importlib.util.spec_from_file_location("resolve_mcp", Path(__file__).with_name("server.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture(autouse=True)
def fresh_process(monkeypatch):
    """No endpoint override, no remembered account: each test starts like a new process."""
    monkeypatch.delenv("WL_RESOLVE_ENDPOINT", raising=False)
    monkeypatch.delenv("WL_ACCOUNT_ID", raising=False)
    module._accounts.clear()


@pytest.mark.asyncio
async def test_reads_the_account_from_the_key_once_and_forwards_the_request_unchanged():
    seen = []
    payload = {"text": "Unknown", "include": ["evidence"]}

    def handler(request):
        seen.append((request.method, str(request.url), request.headers["authorization"]))
        if request.url.path == "/accounts/me":
            return httpx.Response(200, json={"accountId": 216})
        assert __import__("json").loads(request.content) == payload
        return httpx.Response(200, json={"mentions": [{"text": "Unknown", "status": "unresolved",
            "entity": None, "reason": "no_candidates"}]})

    transport = httpx.MockTransport(handler)
    result = await module.call_resolve(payload, key="test-only", transport=transport)
    await module.call_resolve(payload, key="test-only", transport=transport)
    assert seen == [("GET", f"{module.BASE_URL}/accounts/me", "Key test-only"),
                    ("POST", f"{module.BASE_URL}/accounts/216/resolve", "Key test-only"),
                    ("POST", f"{module.BASE_URL}/accounts/216/resolve", "Key test-only")]
    assert result["mentions"][0]["status"] == "unresolved"
    assert result["mentions"][0]["reason"] == "no_candidates"


@pytest.mark.asyncio
async def test_a_given_account_or_endpoint_skips_the_lookup(monkeypatch):
    seen = []
    handler = lambda request: seen.append(str(request.url)) or httpx.Response(200, json={"mentions": []})
    monkeypatch.setenv("WL_ACCOUNT_ID", "7")
    await module.call_resolve({"text": "Apple"}, key="test-only", transport=httpx.MockTransport(handler))
    assert seen == [f"{module.BASE_URL}/accounts/7/resolve"]
    monkeypatch.setenv("WL_RESOLVE_ENDPOINT", "https://resolve.wordlift.io/v1/resolve")
    await module.call_resolve({"text": "Apple"}, key="test-only", transport=httpx.MockTransport(handler))
    assert seen[-1] == "https://resolve.wordlift.io/v1/resolve"


@pytest.mark.asyncio
async def test_a_refused_key_or_a_wrong_api_fails_closed():
    refused = lambda request: httpx.Response(401, json={"code": "unauthorized"})
    with pytest.raises(httpx.HTTPStatusError):
        await module.call_resolve({"text": "Apple"}, key="bad", transport=httpx.MockTransport(refused))
    wrong = lambda request: httpx.Response(200, json={"something": "else"})
    with pytest.raises(RuntimeError, match="accountId"):
        await module.call_resolve({"text": "Apple"}, key="test-only", transport=httpx.MockTransport(wrong))


@pytest.mark.asyncio
async def test_upstream_failure_does_not_guess():
    def handler(request):
        if request.url.path == "/accounts/me":
            return httpx.Response(200, json={"accountId": 216})
        return httpx.Response(503, json={"detail": "unavailable"})
    with pytest.raises(httpx.HTTPStatusError):
        await module.call_resolve({"text": "Apple"}, key="test-only",
            transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_missing_key(monkeypatch):
    monkeypatch.delenv("WL_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WL_KEY"):
        await module.call_resolve({"text": "Apple"})
