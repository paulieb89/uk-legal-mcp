"""The /mcp endpoint is stateless POST-only: GET and DELETE are refused by FastMCP.

The synthetic GET stream was removed after interoperability testing showed clients
work with a native 405. These tests pin the refusal and the POST handshake.
"""

import httpx
import pytest

from src.gateway import create_http_app

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}


def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_http_app()), base_url="http://testserver")


@pytest.mark.asyncio
async def test_get_mcp_is_refused_with_405_and_post_allow_header():
    async with _client() as c:
        resp = await c.get("/mcp", headers={"Accept": "text/event-stream"})

    assert resp.status_code == 405
    assert "POST" in resp.headers["allow"]


def test_post_initialize_succeeds_with_no_get_stream():
    from starlette.testclient import TestClient

    with TestClient(create_http_app()) as tc:
        resp = tc.post(
            "/mcp", json=INIT,
            headers={"Accept": "application/json, text/event-stream", "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json()["result"]["serverInfo"]["name"] == "uk-legal-mcp"


@pytest.mark.asyncio
async def test_health_no_longer_reports_stream_count():
    async with _client() as c:
        resp = await c.get("/health")

    assert resp.status_code == 200
    assert "open_streams" not in resp.json()
