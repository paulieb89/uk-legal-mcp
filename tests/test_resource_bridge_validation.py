"""Regression coverage for the generated resource-reader bridge."""

import pytest
from fastmcp import Client

from src.gateway import gateway


@pytest.mark.asyncio
async def test_bridge_description_and_schema_explain_uri_only_contract():
    async with Client(gateway) as client:
        tools = {tool.name: tool for tool in await client.list_tools()}

    tool = tools["read_resource"]
    assert "not an ordinary web URL" in tool.description
    assert "hmrc_get_manual_page" in tool.description
    assert tool.inputSchema["properties"]["uri"]["type"] == "string"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "uri",
    [
        "https://www.gov.uk/hmrc-internal-manuals/employment-income-manual/eim00500",
        "ftp://example.test/resource",
        "not-a-resource-uri",
    ],
)
async def test_bridge_rejects_non_server_uris_with_actionable_validation_error(uri):
    async with Client(gateway) as client:
        result = await client.call_tool("read_resource", {"uri": uri}, raise_on_error=False)

    assert result.is_error is True
    text = result.content[0].text
    assert '"error_category": "validation"' in text
    assert '"is_retryable": false' in text
    # FastMCP's outer error middleware prefixes tool failures with its generic
    # transport label; the regression is that the response is no longer opaque.
    assert "Error calling tool 'read_resource'" not in text
    assert "hmrc_get_manual_page" in text or "source-specific tool" in text
