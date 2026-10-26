"""parliament_lookup_by_column must not turn a failed or incomplete search into zero matches.

Upstream returns no results when `house` is omitted, even for a Lords citation,
so the default 'both' search runs one explicit search per House. A malformed
page is an error, not an empty success. Mocks route by URL so each House is
answered independently.
"""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import Client

from src.gateway import gateway

SEARCH = "/search/debatebycolumn.json"
LORDS_ITEM = {
    "DebateSectionExtId": "1A160C9B-71AC-4761-8A13-BCEBCCFC3224",
    "SittingDate": "2025-10-14T00:00:00",
    "House": "Lords",
    "Title": "Renters' Rights Bill",
    "DebateSection": "Lords Chamber",
}


def _response(payload) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.content = b"x"
    resp.raise_for_status = MagicMock()
    resp.json.return_value = payload
    return resp


def _route(monkeypatch, by_house: dict):
    """Answer debatebycolumn by its `house` param; everything else gets an empty debate."""
    calls: list[dict] = []

    async def get(url, *, params=None, **kwargs):
        if SEARCH in url:
            calls.append(dict(params or {}))
            # Upstream answers an omitted house with an empty page, as observed live.
            return by_house.get((params or {}).get("house"), _response({"Results": [], "TotalResultCount": 0}))
        return _response({"Overview": {"Id": 1, "Source": 2}, "Items": []})

    monkeypatch.setattr("httpx.AsyncClient.get", AsyncMock(side_effect=get))
    return calls


@pytest.mark.asyncio
async def test_default_search_finds_a_lords_citation(monkeypatch):
    # The regression: with house omitted, upstream answered empty for this Lords citation.
    _route(monkeypatch, {
        "Commons": _response({"Results": [], "TotalResultCount": 0}),
        "Lords": _response({"Results": [LORDS_ITEM], "TotalResultCount": 1}),
    })

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column", {"column_number": "301", "volume_number": 849},
        )

    assert result.data.total_results == 1
    assert [m.house for m in result.data.matches] == ["Lords"]


@pytest.mark.asyncio
async def test_default_search_queries_each_house_explicitly(monkeypatch):
    calls = _route(monkeypatch, {
        "Commons": _response({"Results": [], "TotalResultCount": 0}),
        "Lords": _response({"Results": [], "TotalResultCount": 0}),
    })

    async with Client(gateway) as client:
        await client.call_tool("parliament_lookup_by_column", {"column_number": "302", "volume_number": 849})

    assert sorted(c["house"] for c in calls) == ["Commons", "Lords"]
    assert all("house" in c for c in calls)


@pytest.mark.asyncio
async def test_valid_zero_match_in_both_houses_is_an_empty_success(monkeypatch):
    _route(monkeypatch, {
        "Commons": _response({"Results": [], "TotalResultCount": 0}),
        "Lords": _response({"Results": [], "TotalResultCount": 0}),
    })

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column", {"column_number": "303", "volume_number": 849},
        )

    assert not result.is_error
    assert result.data.total_results == 0
    assert result.data.matches == []


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {},
    {"Results": []},
    {"TotalResultCount": 0},
    {"Results": None, "TotalResultCount": 0},
])
async def test_malformed_page_is_an_error_not_zero_matches(monkeypatch, bad):
    _route(monkeypatch, {
        "Commons": _response(bad),
        "Lords": _response({"Results": [], "TotalResultCount": 0}),
    })

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            {"column_number": "304", "volume_number": 849, "house": "Commons"},
            raise_on_error=False,
        )

    assert result.is_error
    assert '"is_retryable": true' in str(result.content)


@pytest.mark.asyncio
async def test_one_house_failing_is_an_error_even_if_the_other_is_empty(monkeypatch):
    _route(monkeypatch, {
        "Commons": _response({}),
        "Lords": _response({"Results": [], "TotalResultCount": 0}),
    })

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            {"column_number": "305", "volume_number": 849},
            raise_on_error=False,
        )

    assert result.is_error


@pytest.mark.asyncio
async def test_reported_results_none_parseable_is_an_error_not_zero(monkeypatch):
    unparseable = {"Results": [{"House": "Lords"}], "TotalResultCount": 1}  # no DebateSectionExtId
    _route(monkeypatch, {
        "Commons": _response({"Results": [], "TotalResultCount": 0}),
        "Lords": _response(unparseable),
    })

    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_lookup_by_column",
            {"column_number": "306", "volume_number": 849},
            raise_on_error=False,
        )

    assert result.is_error
