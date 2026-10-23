"""Regression coverage for the typed legislation-search WAF fallback."""

import httpx
import pytest
from fastmcp import Client

from src.deps import LegislationClient, LegislationUpstreamError
from src.gateway import gateway


ATOM_FEED = b'''<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:os="http://a9.com/-/spec/opensearch/1.1/">
  <os:totalResults>1</os:totalResults>
  <entry>
    <title>Housing Act 1988</title>
    <id>http://www.legislation.gov.uk/id/ukpga/1988/50</id>
  </entry>
</feed>'''


def _response(url: str) -> httpx.Response:
    return httpx.Response(200, content=ATOM_FEED, request=httpx.Request("GET", url))


@pytest.mark.asyncio
async def test_typed_waf_failure_uses_documented_fulltext_fallback(monkeypatch):
    calls: list[dict] = []
    query = "Housing Act 1988 WAF fallback"

    async def get(_self, url, **kwargs):
        calls.append(kwargs["params"])
        if len(calls) == 1:
            raise LegislationUpstreamError("WAF challenge", reason="waf_or_non_xml")
        return _response(url)

    monkeypatch.setattr(LegislationClient, "get", get)
    async with Client(gateway) as client:
        result = await client.call_tool("legislation_search", {"query": query})

    assert calls == [
        {"results-count": 20, "title": query},
        {"results-count": 20, "text": query},
    ]
    assert result.structured_content["search_mode"] == "fulltext_fallback"
    assert result.structured_content["fallback_reason"] == "waf_or_non_xml"
    assert result.structured_content["results"][0]["title"] == "Housing Act 1988"


@pytest.mark.asyncio
async def test_render_pending_failure_does_not_broaden_the_search(monkeypatch):
    calls: list[dict] = []
    query = "Housing Act 1988 render pending"

    async def get(_self, url, **kwargs):
        calls.append(kwargs["params"])
        raise LegislationUpstreamError("still rendering", reason="render_pending")

    monkeypatch.setattr(LegislationClient, "get", get)
    async with Client(gateway) as client:
        result = await client.call_tool(
            "legislation_search",
            {"query": query},
            raise_on_error=False,
        )

    assert result.is_error is True
    assert calls == [{"results-count": 20, "title": query}]
    assert '"error_category": "transient"' in result.content[0].text


@pytest.mark.asyncio
async def test_explicit_fulltext_never_retries_with_the_same_route(monkeypatch):
    calls: list[dict] = []
    query = "Housing Act 1988 explicit fulltext"

    async def get(_self, url, **kwargs):
        calls.append(kwargs["params"])
        raise LegislationUpstreamError("WAF challenge", reason="waf_or_non_xml")

    monkeypatch.setattr(LegislationClient, "get", get)
    async with Client(gateway) as client:
        result = await client.call_tool(
            "legislation_search",
            {"query": query, "fulltext": True},
            raise_on_error=False,
        )

    assert result.is_error is True
    assert calls == [{"results-count": 20, "text": query}]
