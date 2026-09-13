"""bills_search_bills(house=...) filters by originating house.

Source-fidelity audit reproduction (2026-09-13): the `house` parameter is
documented as "Filter by originating house", but the tool sent the Bills API's
`CurrentHouse` filter. The two populations differ: of 4,048 bills, 851
originated in the Lords while 650 currently sit there (618 overlap; 75
Lords bills have moved to the Commons, 158 are Acts with currentHouse
"Unassigned", and 32 Commons bills now sit in the Lords). Search results only
exposed current_house, so an agent could not tell the wrong population had
been searched.

The Bills API Swagger declares a separate `OriginatingHouse` query filter
(All|Commons|Lords) alongside `CurrentHouse` (All|Commons|Lords|Unassigned),
and each BillSummary in the search payload carries `originatingHouse`. The
tool now sends OriginatingHouse and returns originating_house.

The stub applies both upstream filters to real bill summaries captured in
tests/fixtures/bills_search_house_combinations.json (one bill per
originating/current combination). Upstream accepts either the enum name or
its integer (Commons=1, Lords=2), verified live with identical counts.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "bills_search_house_combinations.json").read_text())
BILLS = FIXTURE["response"]["items"]
BY_TITLE = {b["shortTitle"]: b["billId"] for b in BILLS}
LORDS_TO_COMMONS = BY_TITLE["Social Housing Bill [HL]"]
COMMONS_TO_LORDS = BY_TITLE["Railways Bill"]
LORDS_ACT = BY_TITLE["General Cemetery Act 2025"]

_HOUSE_VALUES = {"1": "Commons", "2": "Lords"}


def _house(value) -> str:
    return _HOUSE_VALUES.get(str(value), str(value))


def _upstream(url: str, params: dict) -> httpx.Response:
    if url != "https://bills-api.parliament.uk/api/v1/Bills":
        raise AssertionError(f"unexpected upstream call: {url}")
    items = BILLS
    if params.get("OriginatingHouse") not in (None, "All"):
        items = [b for b in items if b["originatingHouse"] == _house(params["OriginatingHouse"])]
    if params.get("CurrentHouse") not in (None, "All"):
        items = [b for b in items if b["currentHouse"] == _house(params["CurrentHouse"])]
    skip, take = int(params.get("Skip", 0)), int(params.get("Take", 20))
    return httpx.Response(
        200,
        json={"items": items[skip:skip + take], "totalResults": len(items), "itemsPerPage": take},
        request=httpx.Request("GET", url),
    )


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture
def upstream(monkeypatch):
    async def fake_get(url, params=None, **kwargs):
        return _upstream(url, params or {})

    get = AsyncMock(side_effect=fake_get)
    monkeypatch.setattr("httpx.AsyncClient.get", get)
    return get


async def _search(client: Client, query: str = "Bill", **args) -> dict:
    # Distinct queries per request-inspecting test: the gateway's response
    # cache would otherwise serve an identical earlier call without an
    # upstream request.
    result = await client.call_tool("bills_search_bills", {"query": query, **args})
    assert not result.is_error, f"Tool error: {result.content}"
    return result.structured_content


def _ids(page: dict) -> set[int]:
    return {b["id"] for b in page["bills"]}


class TestOriginatingHouseFilter:
    @pytest.mark.asyncio
    async def test_lords_means_introduced_in_the_lords_wherever_it_is_now(self, client, upstream):
        page = await _search(client, house="Lords")
        assert _ids(page) == {b["billId"] for b in BILLS if b["originatingHouse"] == "Lords"}
        assert LORDS_TO_COMMONS in _ids(page) and LORDS_ACT in _ids(page)
        assert COMMONS_TO_LORDS not in _ids(page)

    @pytest.mark.asyncio
    async def test_commons_means_introduced_in_the_commons(self, client, upstream):
        page = await _search(client, house="Commons")
        assert _ids(page) == {b["billId"] for b in BILLS if b["originatingHouse"] == "Commons"}
        assert COMMONS_TO_LORDS in _ids(page)
        assert LORDS_TO_COMMONS not in _ids(page)

    @pytest.mark.asyncio
    async def test_moved_bill_reports_both_houses(self, client, upstream):
        page = await _search(client, house="Lords")
        (moved,) = [b for b in page["bills"] if b["id"] == LORDS_TO_COMMONS]
        assert (moved["originating_house"], moved["current_house"]) == ("Lords", "Commons")
        assert {b["originating_house"] for b in page["bills"]} == {"Lords"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("house", ["Lords", "Commons"])
    async def test_request_uses_the_originating_house_filter(self, client, upstream, house):
        await _search(client, query=f"request shape {house}", house=house)
        (call,) = upstream.call_args_list
        params = call.kwargs["params"]
        assert params["OriginatingHouse"] == house
        assert "CurrentHouse" not in params


class TestOtherFiltersUnchanged:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", [{}, {"house": "All"}])
    async def test_no_house_filter_sends_neither_house_param(self, client, upstream, args):
        query = f"no house {args}"
        page = await _search(client, query=query, **args)
        assert _ids(page) == {b["billId"] for b in BILLS}
        (call,) = upstream.call_args_list
        assert call.kwargs["params"] == {"SearchTerm": query, "Take": 20, "Skip": 0}

    @pytest.mark.asyncio
    async def test_session_stage_and_paging_are_sent_alongside_house(self, client, upstream):
        await _search(client, query="other filters", house="Lords", session=40, stage="committee", offset=2, limit=3)
        (call,) = upstream.call_args_list
        assert call.kwargs["params"] == {
            "SearchTerm": "other filters", "Take": 3, "Skip": 2, "Session": 40,
            "OriginatingHouse": "Lords", "BillStage": [8, 3, 48, 49],
        }

    @pytest.mark.asyncio
    async def test_paging_over_a_house_filtered_result(self, client, upstream):
        lords = [b["billId"] for b in BILLS if b["originatingHouse"] == "Lords"]
        first = await _search(client, house="Lords", limit=2)
        second = await _search(client, house="Lords", offset=2, limit=2)
        assert [b["id"] for b in first["bills"]] + [b["id"] for b in second["bills"]] == lords
        assert (first["total"], first["has_more"], second["has_more"]) == (len(lords), True, False)


class TestLive:
    """Bounded live check through the registered tool: a Lords bill that moved to the Commons."""

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_social_housing_bill_is_found_by_originating_house(self, client):
        lords = await client.call_tool("bills_search_bills", {"query": "Social Housing Bill", "house": "Lords"})
        commons = await client.call_tool("bills_search_bills", {"query": "Social Housing Bill", "house": "Commons"})
        assert 4126 in {b["id"] for b in lords.structured_content["bills"]}
        assert 4126 not in {b["id"] for b in commons.structured_content["bills"]}
