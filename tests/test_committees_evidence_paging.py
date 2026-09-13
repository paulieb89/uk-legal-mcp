"""committees_search_evidence paging: every evidence item exactly once.

Source-fidelity audit reproduction (2026-09-13): with evidence_type="both",
the tool sent the same `offset` to both upstream endpoints (oral with
Take=ceil(limit/2), written with Take=floor(limit/2)) and returned the two
slices together. Following the advertised continuation offset=offset+returned
then skipped half of each stream: for committee 158 at limit 20, pages at
offsets 0/20/40 returned oral positions 0-9, 20-29 and 40-49, silently
omitting 10-19 and 30-39.

OralEvidence and WrittenEvidence are separately paginated (Skip/Take, each
with its own totalResults) and share no unique sort key (both are ordered by
publicationDate, which has ties), so no single offset can address an
interleaving without refetching everything before it. "both" is therefore the
concatenation: oral positions 0..oral_total-1, then written. `total` is the
sum of the two totalResults and has_more is offset + returned < total.

The stub serves the captured streams for committee 2
(tests/fixtures/committees_evidence_committee_2.json) with the upstream's
Skip/Take contract: default Take 30, Take=0 rejected with 400, Skip past the
end returns an empty page with the real totalResults.
"""

import json
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "committees_evidence_committee_2.json").read_text())
COMMITTEE_ID = FIXTURE["_capture"]["committee_id"]
STREAMS = {"OralEvidence": FIXTURE["oral"], "WrittenEvidence": FIXTURE["written"]}
ORAL = [("oral", i["id"]) for i in FIXTURE["oral"]["items"]]
WRITTEN = [("written", i["id"]) for i in FIXTURE["written"]["items"]]
COMBINED = ORAL + WRITTEN


def _upstream(url: str, params: dict) -> httpx.Response:
    request = httpx.Request("GET", url)
    endpoint = url.rsplit("/", 1)[-1]
    if endpoint not in STREAMS or int(params.get("CommitteeId", 0)) != COMMITTEE_ID:
        raise AssertionError(f"unexpected upstream call: {url} {params}")
    take = int(params.get("Take", 30))
    if take < 1:
        return httpx.Response(400, json={"errors": {"Take": ["must be positive"]}}, request=request)
    skip = int(params.get("Skip", 0))
    stream = STREAMS[endpoint]
    return httpx.Response(
        200,
        json={"items": stream["items"][skip:skip + take], "totalResults": stream["totalResults"], "itemsPerPage": take},
        request=request,
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


async def _page(client: Client, **args):
    result = await client.call_tool("committees_search_evidence", {"committee_id": COMMITTEE_ID, **args})
    assert not result.is_error, f"Tool error: {result.content}"
    page = result.structured_content
    page["items"] = [(e["type"], e["id"]) for e in page["evidence"]]
    return page


async def _walk(client: Client, evidence_type: str, limit: int) -> tuple[list, list]:
    seen, pages, offset = [], [], 0
    while True:
        page = await _page(client, evidence_type=evidence_type, offset=offset, limit=limit)
        seen += page["items"]
        pages.append(page)
        if not page["has_more"]:
            return seen, pages
        offset += page["returned"]


class TestBothWalk:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("limit", [1, 7, 20, 30, 100])
    async def test_walk_visits_every_oral_then_written_item_exactly_once(self, client, upstream, limit):
        seen, pages = await _walk(client, "both", limit)
        assert not [k for k, n in Counter(seen).items() if n > 1], "duplicates"
        assert not set(COMBINED) - set(seen), "gaps"
        assert seen == COMBINED
        assert {p["total"] for p in pages} == {len(COMBINED)}
        assert all(p["has_more"] for p in pages[:-1]) and not pages[-1]["has_more"]


class TestBothPages:
    @pytest.mark.asyncio
    async def test_page_spanning_the_oral_written_boundary(self, client, upstream):
        page = await _page(client, evidence_type="both", offset=25, limit=10)
        assert page["items"] == ORAL[25:] + WRITTEN[:5]
        assert (page["returned"], page["total"], page["has_more"]) == (10, 71, True)

    @pytest.mark.asyncio
    async def test_offset_at_oral_total_starts_written(self, client, upstream):
        page = await _page(client, evidence_type="both", offset=len(ORAL), limit=5)
        assert page["items"] == WRITTEN[:5]

    @pytest.mark.asyncio
    async def test_final_partial_page(self, client, upstream):
        page = await _page(client, evidence_type="both", offset=70, limit=20)
        assert page["items"] == WRITTEN[-1:]
        assert (page["returned"], page["total"], page["has_more"]) == (1, 71, False)

    @pytest.mark.asyncio
    async def test_final_page_that_exactly_fills_limit_has_no_more(self, client, upstream):
        page = await _page(client, evidence_type="both", offset=51, limit=20)
        assert page["items"] == WRITTEN[21:]
        assert (page["returned"], page["has_more"]) == (20, False)

    @pytest.mark.asyncio
    async def test_offset_past_the_end_is_empty(self, client, upstream):
        page = await _page(client, evidence_type="both", offset=5000, limit=20)
        assert (page["items"], page["total"], page["has_more"]) == ([], 71, False)


class TestSingleStream:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("evidence_type, expected", [("oral", ORAL), ("written", WRITTEN)])
    async def test_walk_visits_every_item_once(self, client, upstream, evidence_type, expected):
        seen, pages = await _walk(client, evidence_type, 20)
        assert seen == expected
        assert {p["total"] for p in pages} == {len(expected)}

    @pytest.mark.asyncio
    async def test_oral_page_requests_are_unchanged(self, client, upstream):
        page = await _page(client, evidence_type="oral", offset=5, limit=10)
        assert page["items"] == ORAL[5:15]
        (call,) = upstream.call_args_list
        assert call.args[0].endswith("/OralEvidence")
        assert call.kwargs["params"] == {"CommitteeId": COMMITTEE_ID, "Skip": 5, "Take": 10}

    @pytest.mark.asyncio
    async def test_written_page_requests_are_unchanged(self, client, upstream):
        page = await _page(client, evidence_type="written", offset=5, limit=10)
        assert page["items"] == WRITTEN[5:15]
        (call,) = upstream.call_args_list
        assert call.args[0].endswith("/WrittenEvidence")
        assert call.kwargs["params"] == {"CommitteeId": COMMITTEE_ID, "Skip": 5, "Take": 10}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("evidence_type, offset, expected_len", [("oral", 10, 30), ("written", 21, 41)])
    async def test_final_page_that_exactly_fills_limit_has_no_more(self, client, upstream, evidence_type, offset, expected_len):
        page = await _page(client, evidence_type=evidence_type, offset=offset, limit=20)
        assert page["returned"] == 20
        assert page["has_more"] is False
        assert page["total"] == expected_len


class TestLive:
    """Bounded live walk of the audited committee through the registered tool."""

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_committee_158_both_pages_continue_the_oral_stream(self, client):
        first = await client.call_tool("committees_search_evidence", {"committee_id": 158, "evidence_type": "both", "limit": 20})
        second = await client.call_tool("committees_search_evidence", {"committee_id": 158, "evidence_type": "both", "offset": 20, "limit": 20})
        oral = await client.call_tool("committees_search_evidence", {"committee_id": 158, "evidence_type": "oral", "limit": 40})
        ids = lambda r: [e["id"] for e in r.structured_content["evidence"]]
        assert ids(first) + ids(second) == ids(oral)
        assert first.structured_content["has_more"] is True
