"""Fixture-backed failure behaviour for the two-endpoint Hansard search."""

from collections import Counter

import httpx
import pytest
from fastmcp import Client

from src.gateway import gateway
from src.modules.parliament import tools as parliament_tools


CONTRIBUTIONS = {
    "TotalResultCount": 1,
    "Results": [
        {
            "MemberName": "Example Member",
            "MemberId": 123,
            "AttributedTo": "Example Member",
            "SittingDate": "2026-10-20T00:00:00",
            "House": "Commons",
            "DebateSection": "Example debate",
            "DebateSectionId": 456,
            "DebateSectionExtId": "debate-guid",
            "ContributionExtId": "contribution-guid",
            "HansardSection": "AB-CD1",
            "Section": "Commons Chamber",
            "Rank": 10,
            "ContributionText": "Example source-backed contribution.",
        }
    ],
}

ENVELOPE = {
    "TotalContributions": 1,
    "TotalDebates": 1,
    "TotalDivisions": 0,
    "TotalWrittenStatements": 0,
    "TotalWrittenAnswers": 0,
    "TotalCorrections": 0,
    "TotalPetitions": 0,
    "TotalCommittees": 0,
    "TotalMembers": 1,
    "Debates": [],
    "Divisions": [],
}


def _response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_envelope_timeout_degrades_without_losing_contributions(monkeypatch):
    calls: Counter[str] = Counter()
    monkeypatch.setattr(parliament_tools, "HANSARD_SEARCH_RETRY_DELAY", 0)

    async def get(_self, url, *, params, timeout):
        calls[url] += 1
        if url.endswith("/search.json"):
            raise httpx.ReadTimeout("envelope unavailable")
        return _response(url, CONTRIBUTIONS)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        result = await client.call_tool("parliament_search_hansard", {"query": "resilience envelope"})

    payload = result.structured_content
    assert payload["enrichment_status"] == "degraded"
    assert payload["contributions"][0]["attributed_to"] == "Example Member"
    assert payload["total_debates"] is None
    assert payload["upstream_warnings"] == [{
        "endpoint": "/search.json",
        "retry_attempted": True,
        "error_category": "transient",
        "detail": "Error: Request timed out (30s). Upstream API may be slow — retry.",
    }]
    assert calls["https://hansard-api.parliament.uk/search/contributions/Spoken.json"] == 1
    assert calls["https://hansard-api.parliament.uk/search.json"] == 2


@pytest.mark.asyncio
async def test_envelope_retry_can_restore_complete_enrichment(monkeypatch):
    calls: Counter[str] = Counter()
    monkeypatch.setattr(parliament_tools, "HANSARD_SEARCH_RETRY_DELAY", 0)

    async def get(_self, url, *, params, timeout):
        calls[url] += 1
        if url.endswith("/search.json") and calls[url] == 1:
            raise httpx.ReadTimeout("temporary envelope timeout")
        return _response(url, ENVELOPE if url.endswith("/search.json") else CONTRIBUTIONS)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        result = await client.call_tool("parliament_search_hansard", {"query": "resilience retry"})

    assert result.structured_content["enrichment_status"] == "complete"
    assert result.structured_content["upstream_warnings"] == []
    assert result.structured_content["total_debates"] == 1
    assert calls["https://hansard-api.parliament.uk/search.json"] == 2


@pytest.mark.asyncio
async def test_degraded_hansard_result_is_not_cached(monkeypatch):
    calls: Counter[str] = Counter()
    monkeypatch.setattr(parliament_tools, "HANSARD_SEARCH_RETRY_DELAY", 0)

    async def get(_self, url, *, params, timeout):
        calls[url] += 1
        if url.endswith("/search.json") and calls[url] <= 2:
            raise httpx.ReadTimeout("first envelope attempt is unavailable")
        return _response(url, ENVELOPE if url.endswith("/search.json") else CONTRIBUTIONS)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        first = await client.call_tool("parliament_search_hansard", {"query": "uncached degradation"})
        second = await client.call_tool("parliament_search_hansard", {"query": "uncached degradation"})

    assert first.structured_content["enrichment_status"] == "degraded"
    assert second.structured_content["enrichment_status"] == "complete"
    assert calls["https://hansard-api.parliament.uk/search/contributions/Spoken.json"] == 2
    assert calls["https://hansard-api.parliament.uk/search.json"] == 3


@pytest.mark.asyncio
async def test_contribution_timeout_is_a_retryable_error_not_a_degraded_result(monkeypatch):
    monkeypatch.setattr(parliament_tools, "HANSARD_SEARCH_RETRY_DELAY", 0)

    async def get(_self, url, *, params, timeout):
        if "/search/contributions/" in url:
            raise httpx.ReadTimeout("contributions unavailable")
        return _response(url, ENVELOPE)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        result = await client.call_tool(
            "parliament_search_hansard",
            {"query": "resilience required"},
            raise_on_error=False,
        )

    assert result.is_error is True
    assert '"error_category": "transient"' in result.content[0].text
    assert "contributions" in result.content[0].text


PARTIAL_ENVELOPES = {
    "absent_totals": {"Debates": [], "Divisions": []},
    "null_and_invalid_totals": {**ENVELOPE, "TotalDebates": None, "TotalDivisions": "unknown", "TotalMembers": True},
}


@pytest.mark.asyncio
@pytest.mark.parametrize("shape", sorted(PARTIAL_ENVELOPES))
async def test_partial_envelope_is_degraded_not_complete(monkeypatch, shape):
    async def get(_self, url, *, params, timeout):
        if url.endswith("/search.json"):
            return _response(url, PARTIAL_ENVELOPES[shape])
        return _response(url, CONTRIBUTIONS)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        result = await client.call_tool("parliament_search_hansard", {"query": f"partial {shape}"})

    payload = result.structured_content
    assert payload["enrichment_status"] == "degraded"
    assert payload["contributions"][0]["attributed_to"] == "Example Member"
    assert payload["total_debates"] is None
    assert payload["total_divisions"] is None
    assert payload["total_members"] is None
    [warning] = payload["upstream_warnings"]
    assert warning["endpoint"] == "/search.json"
    assert warning["error_category"] == "malformed"
    assert warning["retry_attempted"] is False
    assert "TotalDebates" in warning["detail"]
    assert "not zero" in warning["detail"]


@pytest.mark.asyncio
async def test_partial_envelope_totals_are_never_reported_as_zero(monkeypatch):
    async def get(_self, url, *, params, timeout):
        if url.endswith("/search.json"):
            return _response(url, {"TotalDebates": 1})
        return _response(url, CONTRIBUTIONS)

    monkeypatch.setattr("httpx.AsyncClient.get", get)
    async with Client(gateway) as client:
        result = await client.call_tool("parliament_search_hansard", {"query": "zero check"})

    payload = result.structured_content
    assert payload["total_debates"] == 1
    assert payload["total_divisions"] is None
    assert payload["total_written_statements"] is None
    assert payload["enrichment_status"] == "degraded"
