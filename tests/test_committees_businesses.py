"""Inquiry-first committee evidence regressions using the official paging fixture."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "committees_evidence_committee_2.json").read_text())
COMMITTEE_ID = FIXTURE["_capture"]["committee_id"]
BUSINESS = FIXTURE["written"]["items"][0]["committeeBusiness"]
BUSINESS_ID = BUSINESS["id"]


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as value:
        yield value


@pytest.fixture
def upstream(monkeypatch):
    async def fake_get(url, params=None, **kwargs):
        request = httpx.Request("GET", url)
        params = params or {}
        endpoint = url.rsplit("/", 1)[-1]
        if endpoint == "CommitteeBusiness":
            matches = [BUSINESS] if params.get("CommitteeBusinessIds", [BUSINESS_ID])[0] == BUSINESS_ID else []
            return httpx.Response(200, json={"items": matches, "totalResults": len(matches)}, request=request)
        if endpoint == "OralEvidence":
            items = FIXTURE["oral"]["items"]
        elif endpoint == "WrittenEvidence":
            items = FIXTURE["written"]["items"]
        else:
            raise AssertionError(f"unexpected upstream endpoint: {url}")
        skip = int(params.get("Skip", 0))
        take = int(params.get("Take", 30))
        return httpx.Response(200, json={"items": items[skip:skip + take], "totalResults": len(items)}, request=request)

    get = AsyncMock(side_effect=fake_get)
    monkeypatch.setattr("httpx.AsyncClient.get", get)
    return get


@pytest.mark.asyncio
async def test_business_discovery_returns_authoritative_inquiry_identity(client, upstream):
    result = await client.call_tool(
        "committees_search_businesses", {"committee_id": COMMITTEE_ID, "query": "General Election"},
    )

    business = result.structured_content["businesses"][0]
    assert business["id"] == BUSINESS_ID
    assert business["title"] == "General Election Planning"
    assert business["type"] == "Inquiry"
    assert business["is_inquiry"] is True
    (_,), kwargs = upstream.call_args
    assert kwargs["params"] == {"CommitteeId": COMMITTEE_ID, "Take": 50, "SearchTerm": "General Election"}


@pytest.mark.asyncio
async def test_evidence_filter_is_forwarded_before_pagination_and_oral_provenance_is_retained(client, upstream):
    result = await client.call_tool(
        "committees_search_evidence",
        {
            "committee_id": COMMITTEE_ID,
            "committee_business_id": BUSINESS_ID,
            "published_from": "2026-01-01",
            "published_to": "2026-12-31",
            "evidence_type": "oral",
            "limit": 1,
        },
    )

    item = result.structured_content["evidence"][0]
    assert item["meeting_date"] == "2026-07-07"
    assert item["publication_date"] == "2026-07-09"
    assert item["committee_businesses"] == [
        {"id": BUSINESS_ID, "title": "General Election Planning", "type": "Inquiry", "is_inquiry": True,
         "status": None, "open_date": "2025-11-11", "close_date": None, "latest_report": None},
    ]
    assert item["document"]["document_id"] == 301303
    assert item["document"]["files"][0]["format"] == "OriginalFormat"
    assert upstream.call_args_list[0].kwargs["params"] == {
        "CommitteeId": COMMITTEE_ID, "CommitteeBusinessIds": [BUSINESS_ID], "Take": 1,
    }
    assert upstream.call_args_list[1].kwargs["params"] == {
        "CommitteeId": COMMITTEE_ID, "Skip": 0, "Take": 1,
        "CommitteeBusinessId": BUSINESS_ID, "StartDate": "2026-01-01", "EndDate": "2026-12-31",
    }


@pytest.mark.asyncio
async def test_written_evidence_retains_reference_session_and_document_provenance(client, upstream):
    result = await client.call_tool(
        "committees_search_evidence",
        {"committee_id": COMMITTEE_ID, "evidence_type": "written", "limit": 1},
    )

    item = result.structured_content["evidence"][0]
    assert item["internal_reference"] == "GEP0009"
    assert item["hc_number"] == "HC 362"
    assert item["session"] == "2026-27"
    assert item["publication_date"] == "2026-06-24"
    assert item["document"]["files"][1]["file_name"] == "GEP0009.Pdf"


@pytest.mark.asyncio
async def test_mismatched_business_id_is_a_nonretryable_validation_failure(client, upstream):
    result = await client.call_tool(
        "committees_search_evidence",
        {"committee_id": COMMITTEE_ID, "committee_business_id": 999999, "evidence_type": "oral"},
        raise_on_error=False,
    )

    assert result.is_error is True
    assert "validation" in result.content[0].text
    assert "not associated" in result.content[0].text
