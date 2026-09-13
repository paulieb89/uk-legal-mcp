"""votes_search_divisions paging: limit, offset, total and has_more.

Source-fidelity audit reproduction (2026-09-13): Lords search for "Bill" had
2,978 matches upstream (searchTotalResults), but the tool returned 25 rows
with total=25 and has_more=False at both limit=10 and limit=100, and offset
did not advance. The tool sent the Commons API's `queryParameters.skip/take`
names to lordsvotes-api, whose Swagger declares bare `skip`/`take`; the
unrecognised paging names were ignored, so every call got the default first
25 rows. `total` was the page length and `has_more` was `returned == limit`.

The Commons API silently caps `take` at 25 (take=100/101/500 all return 25,
verified live 2026-09-13), so limit > 25 had the same false has_more=False.

Both APIs expose searchTotalResults with the same filters as search; `total`
now comes from it and has_more is offset + returned < total.

The stub below emulates each API's verified contract over a captured result
set (tests/fixtures/votes_search_employment_rights.json): Lords honours bare
skip/take and ignores unknown names (default take 25); Commons honours
queryParameters.skip/take, capped at 25.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "votes_search_employment_rights.json").read_text()
)
QUERY = FIXTURE["_capture"]["query"]
LORDS_ROWS = FIXTURE["lords"]["rows"]
COMMONS_ROWS = FIXTURE["commons"]["rows"]
LORDS_IDS = [r["divisionId"] for r in LORDS_ROWS]
COMMONS_IDS = [r["DivisionId"] for r in COMMONS_ROWS]


# (rows, searchTotalResults count) served per house; tests may swap a dataset.
DATA = {
    "lords": (LORDS_ROWS, FIXTURE["lords"]["count"]),
    "commons": (COMMONS_ROWS, FIXTURE["commons"]["count"]),
}


def _upstream(url: str, params: dict) -> object:
    if url.startswith("https://lordsvotes-api.parliament.uk/data/Divisions/"):
        rows, count = DATA["lords"]
        if url.endswith("/searchTotalResults"):
            return count
        skip, take = int(params.get("skip", 0)), int(params.get("take", 25))
        return rows[skip:skip + take]
    if url.startswith("https://commonsvotes-api.parliament.uk/data/divisions.json/"):
        rows, count = DATA["commons"]
        if url.endswith("/searchTotalResults"):
            return count
        skip = int(params.get("queryParameters.skip", 0))
        take = min(int(params.get("queryParameters.take", 25)), 25)
        return rows[skip:skip + take]
    raise AssertionError(f"unexpected upstream call: {url}")


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture
def upstream(monkeypatch):
    async def fake_get(url, params=None, **kwargs):
        return httpx.Response(200, json=_upstream(url, params or {}), request=httpx.Request("GET", url))

    get = AsyncMock(side_effect=fake_get)
    monkeypatch.setattr("httpx.AsyncClient.get", get)
    return get


def _calls(get: AsyncMock) -> dict[str, dict]:
    return {c.args[0]: c.kwargs.get("params") or {} for c in get.call_args_list}


async def _search(client: Client, **args):
    result = await client.call_tool("votes_search_divisions", {"query": QUERY, **args})
    assert not result.is_error, f"Tool error: {result.content}"
    return result.structured_content


class TestLordsParameterMapping:
    @pytest.mark.asyncio
    async def test_lords_uses_its_own_swagger_names(self, client, upstream):
        await _search(client, house="Lords", offset=5, limit=10, from_date="2025-01-01", to_date="2025-12-31", member_id=4291)
        calls = _calls(upstream)
        search = calls["https://lordsvotes-api.parliament.uk/data/Divisions/search"]
        count = calls["https://lordsvotes-api.parliament.uk/data/Divisions/searchTotalResults"]
        filters = {"SearchTerm": QUERY, "StartDate": "2025-01-01", "EndDate": "2025-12-31", "MemberId": 4291}
        assert search == {**filters, "skip": 5, "take": 10}
        assert count == filters


class TestLordsPaging:
    @pytest.mark.asyncio
    async def test_limit_is_honoured(self, client, upstream):
        page = await _search(client, house="Lords", limit=10)
        assert [d["id"] for d in page["divisions"]] == LORDS_IDS[:10]
        assert page["returned"] == 10

    @pytest.mark.asyncio
    async def test_limit_above_the_upstream_default_is_honoured(self, client, upstream):
        page = await _search(client, house="Lords", limit=30)
        assert [d["id"] for d in page["divisions"]] == LORDS_IDS[:30]
        assert page["returned"] == 30

    @pytest.mark.asyncio
    async def test_offset_advances_the_result_set(self, client, upstream):
        first = await _search(client, house="Lords", limit=10)
        second = await _search(client, house="Lords", offset=10, limit=10)
        assert [d["id"] for d in second["divisions"]] == LORDS_IDS[10:20]
        assert not {d["id"] for d in first["divisions"]} & {d["id"] for d in second["divisions"]}

    @pytest.mark.asyncio
    async def test_total_is_the_source_count_not_the_page_length(self, client, upstream):
        page = await _search(client, house="Lords", limit=10)
        assert page["total"] == 36
        assert page["has_more"] is True

    @pytest.mark.asyncio
    async def test_final_partial_page(self, client, upstream):
        page = await _search(client, house="Lords", offset=30, limit=10)
        assert [d["id"] for d in page["divisions"]] == LORDS_IDS[30:]
        assert (page["returned"], page["total"], page["has_more"]) == (6, 36, False)

    @pytest.mark.asyncio
    async def test_final_page_that_exactly_fills_limit_has_no_more(self, client, upstream):
        page = await _search(client, house="Lords", offset=26, limit=10)
        assert [d["id"] for d in page["divisions"]] == LORDS_IDS[26:]
        assert (page["returned"], page["has_more"]) == (10, False)

    @pytest.mark.asyncio
    async def test_walking_offset_plus_returned_visits_every_division_once(self, client, upstream):
        seen, offset = [], 0
        while True:
            page = await _search(client, house="Lords", offset=offset, limit=15)
            seen += [d["id"] for d in page["divisions"]]
            if not page["has_more"]:
                break
            offset += page["returned"]
        assert seen == LORDS_IDS


class TestCommonsPaging:
    @pytest.mark.asyncio
    async def test_commons_keeps_its_prefixed_names(self, client, upstream):
        await _search(client, house="Commons", offset=5, limit=10, member_id=172)
        calls = _calls(upstream)
        search = calls["https://commonsvotes-api.parliament.uk/data/divisions.json/search"]
        count = calls["https://commonsvotes-api.parliament.uk/data/divisions.json/searchTotalResults"]
        filters = {"queryParameters.searchTerm": QUERY, "queryParameters.memberId": 172}
        assert search == {**filters, "queryParameters.skip": 5, "queryParameters.take": 10}
        assert count == filters

    @pytest.mark.asyncio
    async def test_limit_above_the_commons_cap_reports_more(self, client, upstream):
        page = await _search(client, house="Commons", limit=40)
        assert [d["id"] for d in page["divisions"]] == COMMONS_IDS[:25]
        assert (page["total"], page["has_more"]) == (44, True)
        assert page["returned"] == 25

    @pytest.mark.asyncio
    async def test_commons_final_page(self, client, upstream):
        page = await _search(client, house="Commons", offset=25, limit=25)
        assert [d["id"] for d in page["divisions"]] == COMMONS_IDS[25:]
        assert (page["total"], page["has_more"]) == (44, False)
        assert page["returned"] == 19


class TestPagingPastTwoThousand:
    """The audited Lords "Bill" query has 2,978 matches, and the Lords API
    serves the expected continuation at skip=2000, 2500 and 2977 (verified live
    2026-09-13). The tool's offset used to be capped at 2000, so has_more=True
    could point at a page the input schema rejected.

    The deep result sets here are synthetic: the captured rows' shape repeated
    with sequential ids, sized to the audited Lords count and the Commons
    unfiltered count observed the same day.
    """

    LORDS_DEEP = [{**LORDS_ROWS[0], "divisionId": n} for n in range(2978, 0, -1)]
    COMMONS_DEEP = [{**COMMONS_ROWS[0], "DivisionId": n} for n in range(2376, 0, -1)]

    @pytest.fixture
    def deep(self, monkeypatch, upstream):
        monkeypatch.setitem(DATA, "lords", (self.LORDS_DEEP, len(self.LORDS_DEEP)))
        monkeypatch.setitem(DATA, "commons", (self.COMMONS_DEEP, len(self.COMMONS_DEEP)))

    @pytest.mark.asyncio
    async def test_lords_continues_across_the_old_boundary_to_the_last_division(self, client, deep):
        seen, offset = [], 1980
        while True:
            page = await _search(client, house="Lords", offset=offset, limit=100)
            seen += [d["id"] for d in page["divisions"]]
            if not page["has_more"]:
                break
            offset += page["returned"]
        assert seen == [r["divisionId"] for r in self.LORDS_DEEP[1980:]]
        assert (page["offset"], page["returned"], page["total"]) == (2880, 98, 2978)

    @pytest.mark.asyncio
    async def test_lords_offset_2500_is_accepted(self, client, deep):
        page = await _search(client, house="Lords", offset=2500, limit=5)
        assert [d["id"] for d in page["divisions"]] == [478, 477, 476, 475, 474]
        assert page["has_more"] is True

    @pytest.mark.asyncio
    async def test_lords_offset_past_the_end_is_empty_not_an_error(self, client, deep):
        page = await _search(client, house="Lords", offset=3000, limit=5)
        assert (page["returned"], page["total"], page["has_more"]) == (0, 2978, False)

    @pytest.mark.asyncio
    async def test_commons_offset_past_2000(self, client, deep):
        page = await _search(client, house="Commons", offset=2350, limit=25)
        assert [d["id"] for d in page["divisions"]] == list(range(26, 1, -1))
        assert (page["returned"], page["has_more"]) == (25, True)


class TestLive:
    """Bounded live check of the audited reproduction through the registered tool."""

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_lords_bill_query_pages(self, client):
        first = await _search_live(client, query="Bill", house="Lords", limit=10)
        second = await _search_live(client, query="Bill", house="Lords", offset=10, limit=10)
        assert first["returned"] == 10 and first["has_more"] is True
        assert first["total"] > 25
        assert not {d["id"] for d in first["divisions"]} & {d["id"] for d in second["divisions"]}


async def _search_live(client: Client, **args):
    result = await client.call_tool("votes_search_divisions", args)
    assert not result.is_error, f"Tool error: {result.content}"
    return result.structured_content
