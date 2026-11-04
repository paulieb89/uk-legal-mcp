"""Client connections must be attributed to the client that sent them."""

from types import SimpleNamespace

import pytest
from prometheus_client import REGISTRY
from starlette.testclient import TestClient

import src.gateway as gw
from src.gateway import REGION, TRANSPORT, ClientTrackingMiddleware, _client_identity, create_http_app

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _count(name, version):
    return REGISTRY.get_sample_value(
        "uk_legal_client_connections_total",
        {"client_name": name, "client_version": version, "transport": TRANSPORT, "region": REGION},
    ) or 0.0


@pytest.fixture(autouse=True)
def fresh_labels(monkeypatch):
    monkeypatch.setattr(gw, "_client_labels", gw._BoundedLabel(40))


def test_initialize_over_http_is_attributed_to_the_named_client():
    before = _count("tracked-client-xyz", "3.1")
    body = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                   "clientInfo": {"name": "tracked-client-xyz", "version": "3.1"}},
    }
    with TestClient(create_http_app()) as tc:
        assert tc.post("/mcp", json=body, headers=H).status_code == 200
    assert _count("tracked-client-xyz", "3.1") == before + 1


def test_discover_handshake_reads_client_info_from_meta():
    ctx = SimpleNamespace(
        method="server/discover",
        message=SimpleNamespace(params={"meta": {"io.modelcontextprotocol/clientInfo": {"name": "modern", "version": "2"}}}),
    )
    assert _client_identity(ctx) == ("modern", "2")


def test_a_client_that_sends_nothing_is_unknown():
    ctx = SimpleNamespace(method="server/discover", message=SimpleNamespace(params={}))
    assert _client_identity(ctx) == ("unknown", "unknown")


@pytest.mark.asyncio
async def test_discover_handshake_is_counted_not_ignored():
    before = _count("modern-client", "9")
    ctx = SimpleNamespace(
        method="server/discover",
        message=SimpleNamespace(params={"meta": {"io.modelcontextprotocol/clientInfo": {"name": "modern-client", "version": "9"}}}),
    )

    async def call_next(_):
        return "ok"

    assert await ClientTrackingMiddleware().on_request(ctx, call_next) == "ok"
    assert _count("modern-client", "9") == before + 1
