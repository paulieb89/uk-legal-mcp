"""Committee name search must query upstream before the result limit applies."""

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway


def _committee(i: int, name: str) -> dict:
    return {"id": 1000 + i, "name": name, "house": "Commons"}


# 147 current committees, like the live API. The target sits beyond the first 100 rows.
COMMITTEES = [_committee(i, f"Committee {i:03d}") for i in range(147)]
COMMITTEES[120] = _committee(120, "Public Accounts Committee")


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as value:
        yield value


@pytest.fixture
def upstream(monkeypatch):
    async def fake_get(url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        params = params or {}
        rows = COMMITTEES
        term = params.get("SearchTerm")
        if term:
            rows = [c for c in rows if term.lower() in c["name"].lower()]
        take = int(params.get("Take", 30))
        return httpx.Response(
            200, json={"items": rows[:take], "totalResults": len(rows)}, request=request,
        )

    get = AsyncMock(side_effect=fake_get)
    monkeypatch.setattr("httpx.AsyncClient.get", get)
    return get


@pytest.mark.asyncio
async def test_small_limit_still_finds_match_beyond_first_page(client, upstream):
    result = await client.call_tool(
        "committees_search_committees", {"query": "Public Accounts", "limit": 5},
    )

    names = [c["name"] for c in result.structured_content["committees"]]
    assert names == ["Public Accounts Committee"]


@pytest.mark.asyncio
async def test_query_is_sent_upstream_and_limit_applies_after_search(client, upstream):
    await client.call_tool("committees_search_committees", {"query": "Committee 1", "limit": 3})

    params = upstream.call_args.kwargs["params"]
    assert params["SearchTerm"] == "Committee 1"
    assert params["Take"] == 3


@pytest.mark.asyncio
async def test_listing_without_query_sends_no_search_term(client, upstream):
    await client.call_tool("committees_search_committees", {"limit": 10})

    params = upstream.call_args.kwargs["params"]
    assert "SearchTerm" not in params
    assert params["CommitteeStatus"] == "Current"


@pytest.mark.asyncio
async def test_no_match_is_empty_success_not_error(client, upstream):
    result = await client.call_tool(
        "committees_search_committees", {"query": "zzqx no such committee", "limit": 5},
    )

    assert not result.is_error
    assert result.structured_content["committees"] == []
