"""Every tool that returns Find Case Law content carries the Open Justice Licence attribution.

OJL v2.0 requires "the following attribution statement and, where possible, a
link to this licence" on any reuse. The statement is checked verbatim.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastmcp import Client

from src.gateway import gateway

STATEMENT = "Contains information licensed under the Open Justice - Licence v2.0."
LICENCE_URL = "https://caselaw.nationalarchives.gov.uk/open-justice-licence/version/2"

JUDGMENT_XML = Path("tests/fixtures/uksc_2024_12_full.xml").read_text()
EMPTY_ATOM = '<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

CALLS = {
    "case_law_search": {"query": "ojl attribution probe"},
    "case_law_grep_judgment": {"slug": "uksc/2024/12", "pattern": "ojl-probe|the"},
    "judgment_get_header": {"slug": "uksc/2024/12"},
    "judgment_get_index": {"slug": "uksc/2024/12"},
    "judgment_get_paragraph": {"slug": "uksc/2024/12", "eId": "para_5"},
    "citations_network": {"case_uri": "uksc/2024/12"},
}


@pytest_asyncio.fixture
async def client():
    async with Client(gateway) as c:
        yield c


@pytest.fixture(autouse=True)
def tna_serves_fixtures(monkeypatch):
    async def send(self, request, **kwargs):
        body = EMPTY_ATOM if request.url.path.endswith("atom.xml") else JUDGMENT_XML
        return httpx.Response(200, text=body, request=request,
                              headers={"content-type": "application/xml"})

    monkeypatch.setattr(httpx.AsyncClient, "send", send)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", sorted(CALLS))
async def test_case_law_output_carries_ojl_attribution(client, tool):
    result = await client.call_tool(tool, CALLS[tool])
    assert result.structured_content["attribution"] == {
        "statement": STATEMENT,
        "licence_url": LICENCE_URL,
    }
