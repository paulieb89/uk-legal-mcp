"""Smoke tests for Phase 3 resource templates.

These tests validate the gateway-level resource registration end-to-end via
an in-process FastMCP `Client` (no HTTP transport needed).

Why gateway-level: see issue #3 — resources registered on a mounted sub-MCP
have their RFC 6570 wildcard substitution silently fail.

Why live: resources are thin wrappers over external HTTP APIs; mocking
them out tests almost nothing of value. The TNA Find Case Law endpoint is
reliable and free. The legislation.gov.uk endpoint may be blocked locally
by CloudFront (issue #4) — those tests are skipped on connection refusal
rather than failed.
"""

import pytest
from fastmcp import Client

from src.gateway import gateway


@pytest.mark.asyncio
async def test_resource_templates_registered_at_gateway():
    """The three Phase 3 resource templates must be exposed."""
    async with Client(gateway) as client:
        templates = {t.uriTemplate for t in await client.list_resource_templates()}

    assert "judgment://{slug*}" in templates
    assert "legislation://{type}/{year}/{number}/section/{section}" in templates
    assert "legislation://{type}/{year}/{number}/toc" in templates


@pytest.mark.asyncio
async def test_judgment_resource_returns_legaldocml():
    """Reading a known TNA judgment slug returns LegalDocML XML."""
    async with Client(gateway) as client:
        result = await client.read_resource("judgment://uksc/2024/12")

    assert len(result) == 1
    text = result[0].text
    assert text.startswith("<akomaNtoso")
    assert "legaldocml" in text or "akn/3.0" in text
    assert len(text) > 10_000  # Non-trivial judgment


@pytest.mark.asyncio
async def test_judgment_resource_handles_deep_slug():
    """Multi-segment wildcard slug ('ewca/civ/2023/N') routes correctly."""
    async with Client(gateway) as client:
        # 404 is expected for a made-up slug; the test is that the URL is
        # constructed with the wildcard substituted, not that this case exists.
        with pytest.raises(Exception) as exc_info:
            await client.read_resource("judgment://ewca/civ/2099/99999")

    msg = str(exc_info.value)
    assert "ewca/civ/2099/99999" in msg, (
        f"Wildcard substitution failed — error message did not contain the slug: {msg}"
    )


@pytest.mark.asyncio
async def test_legislation_section_resource_url_construction():
    """Reading a legislation section resource constructs the right upstream URL.

    Skipped when CloudFront blocks the local egress (issue #4).
    """
    async with Client(gateway) as client:
        try:
            result = await client.read_resource(
                "legislation://ukpga/2006/46/section/172"
            )
        except Exception as e:
            if "437" in str(e):
                pytest.skip("CloudFront 437 blocking local httpx — see issue #4")
            raise

    text = result[0].text
    assert "<" in text and ">" in text  # XML
    assert "172" in text or "Companies Act" in text


@pytest.mark.asyncio
async def test_legislation_toc_resource_returns_lines():
    """TOC resource returns a newline-separated list of 'id: title' rows.

    Skipped when CloudFront blocks the local egress (issue #4).
    """
    async with Client(gateway) as client:
        try:
            result = await client.read_resource("legislation://ukpga/2006/46/toc")
        except Exception as e:
            if "437" in str(e):
                pytest.skip("CloudFront 437 blocking local httpx — see issue #4")
            raise

    text = result[0].text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) > 100, "Companies Act 2006 should produce hundreds of TOC items"
    assert all(":" in ln for ln in lines[:10]), "Each line should be 'id: title'"
