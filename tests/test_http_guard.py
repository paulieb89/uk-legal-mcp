"""Tests for the bounded legacy GET /mcp compatibility stream."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.gateway import _GetStreamBudget, _HttpGuard, create_http_app


def _scope(method: str, path: str = "/mcp") -> dict:
    return {"type": "http", "method": method, "path": path, "headers": []}


class _Connection:
    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.queue.put_nowait({"type": "http.request", "body": b"", "more_body": False})

    async def receive(self) -> dict:
        return await self.queue.get()

    async def send(self, message: dict) -> None:
        self.sent.append(message)

    def disconnect(self) -> None:
        self.queue.put_nowait({"type": "http.disconnect"})

    def body(self) -> bytes:
        return b"".join(
            message.get("body", b"")
            for message in self.sent
            if message["type"] == "http.response.body"
        )

    def closed(self) -> bool:
        return any(
            message["type"] == "http.response.body" and not message.get("more_body", False)
            for message in self.sent
        )


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.005)


def _guard(budget: _GetStreamBudget, **overrides) -> _HttpGuard:
    async def app(scope, receive, send):
        raise AssertionError("GET /mcp must be handled by the guard")

    options = {"ttl": 60.0, "ping": 60.0, "retry_ms": 5000}
    options.update(overrides)
    return _HttpGuard(app, budget=budget, **options)


@pytest.mark.asyncio
async def test_get_emits_retry_hint_and_releases_on_disconnect():
    budget = _GetStreamBudget(cap=5)
    connection = _Connection()
    task = asyncio.create_task(_guard(budget)(_scope("GET"), connection.receive, connection.send))

    await _wait_for(lambda: budget.open == 1)

    assert connection.sent[0]["status"] == 200
    assert (b"content-type", b"text/event-stream") in connection.sent[0]["headers"]
    assert connection.body() == b"retry: 5000\n\n"
    assert not connection.closed()

    connection.disconnect()
    await asyncio.wait_for(task, 2.0)
    assert budget.open == 0


@pytest.mark.asyncio
async def test_cap_evicts_oldest_stream_without_exceeding_cap():
    budget = _GetStreamBudget(cap=2)
    guard = _guard(budget)
    first, second, third = _Connection(), _Connection(), _Connection()

    first_task = asyncio.create_task(guard(_scope("GET"), first.receive, first.send))
    await _wait_for(lambda: budget.open == 1)
    second_task = asyncio.create_task(guard(_scope("GET"), second.receive, second.send))
    await _wait_for(lambda: budget.open == 2)
    third_task = asyncio.create_task(guard(_scope("GET"), third.receive, third.send))

    await asyncio.wait_for(first_task, 2.0)
    assert first.closed()
    assert budget.open == 2
    assert not second.closed()
    assert not third.closed()

    second.disconnect()
    third.disconnect()
    await asyncio.wait_for(asyncio.gather(second_task, third_task), 2.0)
    assert budget.open == 0


@pytest.mark.asyncio
async def test_stream_expires_after_ttl_and_closes_cleanly():
    budget = _GetStreamBudget(cap=5)
    connection = _Connection()

    await asyncio.wait_for(
        _guard(budget, ttl=0.05, ping=10.0)(
            _scope("GET"), connection.receive, connection.send
        ),
        2.0,
    )

    assert connection.closed()
    assert budget.open == 0


@pytest.mark.asyncio
async def test_idle_stream_sends_ping_comments():
    budget = _GetStreamBudget(cap=5)
    connection = _Connection()

    await asyncio.wait_for(
        _guard(budget, ttl=0.2, ping=0.02)(
            _scope("GET"), connection.receive, connection.send
        ),
        2.0,
    )

    assert b": ping\n\n" in connection.body()


@pytest.mark.asyncio
async def test_delete_is_405_with_post_allow_header():
    budget = _GetStreamBudget(cap=5)
    connection = _Connection()

    await _guard(budget)(_scope("DELETE"), connection.receive, connection.send)

    assert connection.sent[0]["status"] == 405
    assert (b"allow", b"POST") in connection.sent[0]["headers"]
    assert budget.open == 0


@pytest.mark.asyncio
async def test_405_mode_passes_get_to_fastmcp():
    app = create_http_app(mode="405")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/mcp")

    assert response.status_code == 405


@pytest.mark.asyncio
async def test_wrapped_app_health_reports_open_stream_count_and_post_initializes():
    app = create_http_app()
    fastmcp_app = app.app.app
    async with fastmcp_app.router.lifespan_context(fastmcp_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            health = await client.get("/health")
            initialize = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                },
            )

    assert health.status_code == 200
    assert health.json()["open_streams"] == 0
    assert initialize.status_code == 200
    assert initialize.json()["result"]["protocolVersion"]
