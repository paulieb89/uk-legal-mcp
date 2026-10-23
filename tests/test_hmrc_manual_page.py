"""Fixture-backed coverage for bounded HMRC internal-manual retrieval."""

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway
from src.modules.hmrc.tools import _manual_page_from_html, _manual_target


FIXTURE = (Path(__file__).parent / "fixtures" / "hmrc_eim00500.html").read_bytes()
URL = "https://www.gov.uk/hmrc-internal-manuals/employment-income-manual/eim00500"


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as value:
        yield value


def test_manual_target_accepts_canonical_url():
    assert _manual_target(URL, None, None) == (URL, "employment-income-manual", "eim00500")


def test_manual_target_builds_canonical_url_from_reference():
    assert _manual_target(None, "employment-income-manual", "EIM00500") == (
        URL,
        "employment-income-manual",
        "eim00500",
    )


@pytest.mark.parametrize(
    "manual_url",
    [
        "http://www.gov.uk/hmrc-internal-manuals/employment-income-manual/eim00500",
        "https://gov.uk/hmrc-internal-manuals/employment-income-manual/eim00500",
        "https://www.gov.uk/guidance/vat-rates",
        "https://www.gov.uk/hmrc-internal-manuals/employment-income-manual/eim00500?x=1",
    ],
)
def test_manual_target_rejects_noncanonical_urls(manual_url):
    with pytest.raises(ValueError, match="canonical HTTPS GOV.UK"):
        _manual_target(manual_url, None, None)


def test_manual_html_extraction_preserves_main_content_and_excludes_chrome():
    result = _manual_page_from_html(
        FIXTURE,
        canonical_url=URL,
        manual_family="employment-income-manual",
        reference="eim00500",
        max_chars=10_000,
    )
    assert result.title == "EIM00500 - Employment income: contents"
    assert result.updated and result.updated.isoformat() == "2026-10-21"
    assert "EIM00505 General: contents" in result.content
    assert "Cookie and feedback" not in result.content
    assert "Related content" not in result.content


def test_manual_html_extraction_reports_truncation():
    result = _manual_page_from_html(
        FIXTURE,
        canonical_url=URL,
        manual_family="employment-income-manual",
        reference="eim00500",
        max_chars=60,
    )
    assert result.content_truncated is True
    assert result.original_length > 60
    assert result.content.endswith("…[truncated]")


@pytest.mark.asyncio
async def test_manual_tool_returns_bounded_source_page(client, monkeypatch):
    request = httpx.Request("GET", URL)
    get = AsyncMock(return_value=httpx.Response(200, content=FIXTURE, request=request))
    monkeypatch.setattr("httpx.AsyncClient.get", get)

    result = await client.call_tool("hmrc_get_manual_page", {"manual_url": URL, "max_chars": 10_000})

    assert result.structured_content["canonical_url"] == URL
    assert result.structured_content["reference"] == "eim00500"
    assert "EIM01000" in result.structured_content["content"]
    get.assert_awaited_once_with(URL, headers={"Accept": "text/html,application/xhtml+xml"})


@pytest.mark.asyncio
async def test_manual_tool_rejects_invalid_url_without_fetching(client, monkeypatch):
    get = AsyncMock()
    monkeypatch.setattr("httpx.AsyncClient.get", get)

    result = await client.call_tool(
        "hmrc_get_manual_page",
        {"manual_url": "https://www.gov.uk/guidance/vat-rates"},
        raise_on_error=False,
    )

    assert result.is_error is True
    assert "validation" in result.content[0].text
    assert "No manual-page content was retrieved" in result.content[0].text
    get.assert_not_awaited()
