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


def _evictions() -> float:
    from prometheus_client import REGISTRY

    from src.gateway import REGION

    return REGISTRY.get_sample_value("uk_legal_stream_evictions_total", {"region": REGION}) or 0.0


@pytest.mark.asyncio
async def test_eviction_increments_counter_and_logs():
    import logging

    from src.gateway import _stream_log

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    # FastMCP sets propagate=False on its "fastmcp" logger, so attach directly.
    handler = _Capture(level=logging.INFO)
    _stream_log.addHandler(handler)
    try:
        budget = _GetStreamBudget(cap=1)
        before = _evictions()
        first_id, first_evicted = budget.admit()
        budget.admit()
    finally:
        _stream_log.removeHandler(handler)

    assert first_evicted.is_set()
    assert budget.open == 1
    assert _evictions() == before + 1
    messages = [r.getMessage() for r in records]
    assert f"evicted oldest GET /mcp stream id={first_id} family=unknown age=0.0s at cap=1 admitted_by=unknown" in messages


@pytest.mark.asyncio
async def test_admit_below_cap_does_not_count_eviction():
    budget = _GetStreamBudget(cap=2)
    before = _evictions()

    budget.admit()
    budget.admit()

    assert _evictions() == before


def _family_evictions(evicted: str, admitting: str) -> float:
    from prometheus_client import REGISTRY

    from src.gateway import REGION

    return REGISTRY.get_sample_value(
        "uk_legal_stream_evictions_by_family_total",
        {"evicted_family": evicted, "admitting_family": admitting, "region": REGION},
    ) or 0.0


@pytest.mark.asyncio
async def test_eviction_is_counted_and_logged_by_family_pair():
    import logging

    from src.gateway import _stream_log

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.INFO)
    _stream_log.addHandler(handler)
    try:
        budget = _GetStreamBudget(cap=1)
        before = _family_evictions("chatgpt", "claude")
        budget.admit("chatgpt")
        budget.admit("claude")
    finally:
        _stream_log.removeHandler(handler)

    assert _family_evictions("chatgpt", "claude") == before + 1
    assert any("family=chatgpt" in r.getMessage() and "admitted_by=claude" in r.getMessage() for r in records)


@pytest.mark.asyncio
async def test_release_drops_family_for_closed_stream():
    budget = _GetStreamBudget(cap=2)
    stream_id, _ = budget.admit("codex")
    budget.release(stream_id)

    assert budget._families == {}


def test_client_family_buckets_user_agents_without_keeping_raw_header():
    from src.gateway import client_family

    assert client_family("Claude-User/1.0 (+https://www.anthropic.com)") == "claude"
    assert client_family("ChatGPT-User/1.0") == "chatgpt"
    assert client_family("OpenAI-Codex/0.1 codex_cli") == "codex"
    assert client_family("python-httpx/0.28.1") == "unknown"
    assert client_family(None) == "unknown"
    assert client_family("") == "unknown"


@pytest.mark.asyncio
async def test_guard_records_client_family_from_user_agent():
    budget = _GetStreamBudget(cap=5)
    connection = _Connection()
    scope = {
        "type": "http", "method": "GET", "path": "/mcp",
        "headers": [(b"user-agent", b"ChatGPT-User/1.0")],
    }
    task = asyncio.create_task(_guard(budget)(scope, connection.receive, connection.send))

    await _wait_for(lambda: budget.open == 1)

    assert list(budget._families.values()) == ["chatgpt"]
    connection.disconnect()
    await asyncio.wait_for(task, 2.0)


def _sample(name: str, labels: dict) -> float:
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(name, labels) or 0.0


@pytest.mark.asyncio
async def test_admissions_are_counted_by_family():
    from src.gateway import REGION

    before = _sample("uk_legal_stream_admissions_total", {"family": "codex", "region": REGION})
    budget = _GetStreamBudget(cap=5)
    budget.admit("codex")
    budget.admit("codex")

    assert _sample("uk_legal_stream_admissions_total", {"family": "codex", "region": REGION}) == before + 2


@pytest.mark.asyncio
async def test_eviction_records_evicted_stream_age_by_family_pair():
    import logging

    from src.gateway import REGION, _stream_log

    labels = {"evicted_family": "chatgpt", "admitting_family": "claude", "region": REGION}
    count_before = _sample("uk_legal_stream_eviction_age_seconds_count", labels)
    sum_before = _sample("uk_legal_stream_eviction_age_seconds_sum", labels)

    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.INFO)
    _stream_log.addHandler(handler)
    try:
        budget = _GetStreamBudget(cap=1)
        budget.admit("chatgpt", now=100.0)
        budget.admit("claude", now=112.5)
    finally:
        _stream_log.removeHandler(handler)

    assert _sample("uk_legal_stream_eviction_age_seconds_count", labels) == count_before + 1
    assert _sample("uk_legal_stream_eviction_age_seconds_sum", labels) == pytest.approx(sum_before + 12.5)
    assert any("family=chatgpt age=12.5s" in r.getMessage() for r in records)
