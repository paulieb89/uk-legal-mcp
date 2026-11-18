"""Regression tests for the 2026-11-17 validation audit, findings L4 and L5.

L1-L3 (citation verification and the shared existence check) are covered in
test_citations.py.

- L4: judgment_get_paragraph for a missing eId returned an anonymous
  "Error calling tool"; the eId was also interpolated into an XPath predicate.
- L5: legislation_get_section took a free-text type, so type="act" read as a
  missing section instead of an invalid argument.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client
from fastmcp.exceptions import ToolError

from src.gateway import gateway
from src.modules.case_law import parsers

JUDGMENT_XML = Path("tests/fixtures/uksc_2024_12_full.xml").read_text()


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture
def tna_serves_fixture(monkeypatch):
    requested: list[str] = []

    async def send(self, request, **kwargs):
        requested.append(str(request.url))
        return httpx.Response(200, text=JUDGMENT_XML, request=request,
                              headers={"content-type": "application/xml"})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    return requested


# ---------------------------------------------------------------------------
# L4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_missing_paragraph_is_not_found_naming_the_eid(client, tna_serves_fixture):
    with pytest.raises(ToolError) as exc_info:
        await client.call_tool(
            "judgment_get_paragraph", {"slug": "uksc/2024/12", "eId": "para_9999"},
        )
    payload = json.loads(str(exc_info.value))
    assert payload["error_category"] == "not_found"
    assert "para_9999" in payload["description"]
    assert payload["is_retryable"] is False


@pytest.mark.asyncio
async def test_existing_paragraph_still_returns(client, tna_serves_fixture):
    result = await client.call_tool(
        "judgment_get_paragraph", {"slug": "uksc/2024/12", "eId": "5"},
    )
    assert result.data["eId"] == "para_5"
    assert "<" in result.data["content"]


@pytest.mark.asyncio
async def test_tna_404_is_not_found(client, monkeypatch):
    async def send(self, request, **kwargs):
        return httpx.Response(404, text="", request=request)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    with pytest.raises(ToolError) as exc_info:
        await client.call_tool("judgment_get_header", {"slug": "uksc/2001/404"})
    assert json.loads(str(exc_info.value))["error_category"] == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("eid", ["para_1' or '1'='1", "para_1]", "a b"])
async def test_eid_outside_the_pattern_is_rejected_before_fetching(client, tna_serves_fixture, eid):
    with pytest.raises(ToolError):
        await client.call_tool("judgment_get_paragraph", {"slug": "uksc/2024/12", "eId": eid})
    assert tna_serves_fixture == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slug", ["https://caselaw.nationalarchives.gov.uk/uksc/2024/12", "uksc/2024/12?x=1", "../etc"])
async def test_slug_outside_the_pattern_is_rejected(client, tna_serves_fixture, slug):
    with pytest.raises(ToolError):
        await client.call_tool("judgment_get_header", {"slug": slug})
    assert tna_serves_fixture == []


def test_parser_does_not_interpolate_eid_into_xpath():
    # A quote used to break out of the [@eId='...'] predicate.
    with pytest.raises(KeyError):
        parsers.extract_paragraph(JUDGMENT_XML, "x' or @eId!='")


# ---------------------------------------------------------------------------
# L5
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["legislation_get_section", "legislation_get_toc"])
async def test_invalid_type_code_is_a_validation_error(client, tool, monkeypatch):
    from curl_cffi.requests import AsyncSession

    calls: list[str] = []

    async def request(self, method, url, **kwargs):
        calls.append(url)
        raise AssertionError("must not reach the network")

    monkeypatch.setattr(AsyncSession, "request", request)
    args = {"type": "act", "year": 2006, "number": 46}
    if tool == "legislation_get_section":
        args["section"] = "47"
    with pytest.raises(ToolError) as exc_info:
        await client.call_tool(tool, args)
    assert json.loads(str(exc_info.value))["error_category"] == "validation"
    assert calls == []


@pytest.mark.asyncio
async def test_schema_lists_the_type_codes(client):
    tools = {t.name: t for t in await client.list_tools()}
    schema = tools["legislation_get_section"].inputSchema["properties"]["type"]
    assert {"ukpga", "uksi", "asp", "ssi", "nia", "wsi", "eur"} <= set(schema["enum"])
