"""bills_search_bills paging reaches every result the source reports.

Source-fidelity check (2026-09-13): OriginatingHouse=Commons has 3,196 bills,
but the tool capped `offset` at 2000, so has_more=True could point at a page
the input schema rejected. The cap was a local convention with no upstream
basis: the Bills API Swagger declares Skip as int32, and live Skip=2000, 2500
and 3195 continue the result set (pages at 1995+5 and 2000+5 join a single
1995+10 page), Skip past the end returns [] with the real totalResults, and
Skip=2**31 is a 400.

The large result set here is synthetic: the shape of a real captured
BillSummary (tests/fixtures/bills_search_house_combinations.json) repeated
with sequential ids, sized to the live Commons-originating total.
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway

TEMPLATE = json.loads(
    (Path(__file__).parent / "fixtures" / "bills_search_house_combinations.json").read_text()
)["response"]["items"][0]
TOTAL = 3196
BILLS = [{**TEMPLATE, "billId": n, "originatingHouse": "Commons"} for n in range(1, TOTAL + 1)]
IDS = [b["billId"] for b in BILLS]


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture
def upstream(monkeypatch):
    async def fake_get(url, params=None, **kwargs):
        params = params or {}
        skip, take = int(params.get("Skip", 0)), int(params.get("Take", 20))
        return httpx.Response(
            200,
            json={"items": BILLS[skip:skip + take], "totalResults": TOTAL, "itemsPerPage": take},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr("httpx.AsyncClient.get", AsyncMock(side_effect=fake_get))


async def _search(client: Client, **args) -> dict:
    result = await client.call_tool("bills_search_bills", {"query": "Bill", "house": "Commons", **args})
    assert not result.is_error, f"Tool error: {result.content}"
    return result.structured_content


def _ids(page: dict) -> list[int]:
    return [b["id"] for b in page["bills"]]


class TestPagingPastTwoThousand:
    @pytest.mark.asyncio
    async def test_pages_join_across_the_old_boundary(self, client, upstream):
        before = await _search(client, offset=1995, limit=5)
        after = await _search(client, offset=2000, limit=5)
        assert _ids(before) + _ids(after) == IDS[1995:2005]
        assert before["has_more"] and after["has_more"]

    @pytest.mark.asyncio
    async def test_walk_from_before_the_old_boundary_reaches_the_last_bill(self, client, upstream):
        seen, offset = [], 1990
        while True:
            page = await _search(client, offset=offset, limit=100)
            assert page["total"] == TOTAL
            seen += _ids(page)
            if not page["has_more"]:
                break
            offset += page["returned"]
        assert seen == IDS[1990:]
        assert (page["offset"], page["returned"]) == (3190, 6)

    @pytest.mark.asyncio
    async def test_offset_2500_is_accepted(self, client, upstream):
        page = await _search(client, offset=2500, limit=5)
        assert _ids(page) == IDS[2500:2505]

    @pytest.mark.asyncio
    async def test_offset_past_the_end_is_an_honest_empty_page(self, client, upstream):
        page = await _search(client, offset=5000, limit=5)
        assert (page["returned"], page["total"], page["has_more"]) == (0, TOTAL, False)
