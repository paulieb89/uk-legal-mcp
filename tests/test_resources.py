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
async def test_legislation_resource_templates_registered():
    """Phase 3 legislation resource templates must be exposed.

    Case-law resource templates are covered by tests/test_phase4_drilldown.py.
    """
    async with Client(gateway) as client:
        templates = {t.uriTemplate for t in await client.list_resource_templates()}

    assert "legislation://{type}/{year}/{number}/section/{section}{?date}" in templates
    assert "legislation://{type}/{year}/{number}/toc{?date}" in templates


@pytest.mark.asyncio
async def test_legislation_search_default_finds_named_act():
    """Regression: 'Housing Act 1988' must rank ukpga/1988/50 in the top 3.

    Before the curl_cffi Accept-header fix + title-search default, this query
    returned 0 hits (parser saw HTML not Atom) or ranked SIs/regs first.
    Caught by Codex during real-world s.21 query (2026-04-19).
    """
    async with Client(gateway) as client:
        result = await client.call_tool(
            "legislation_search",
            {"params": {"query": "Housing Act 1988"}},
        )
    top3 = [(r.type, r.year, r.number) for r in result.data.results[:3]]
    assert ("ukpga", 1988, 50) in top3, f"Top 3 was {top3}"


@pytest.mark.asyncio
async def test_judgment_wildcard_substitution_handles_deep_slug():
    """Multi-segment wildcard slug ('ewca/civ/2023/N') routes correctly via a sub-path."""
    async with Client(gateway) as client:
        # 404 is expected for a made-up slug; the test is that the URL is
        # constructed with the wildcard substituted, not that this case exists.
        with pytest.raises(Exception) as exc_info:
            await client.read_resource("judgment://ewca/civ/2099/99999/header")

    msg = str(exc_info.value)
    assert "ewca/civ/2099/99999" in msg, (
        f"Wildcard substitution failed — error message did not contain the slug: {msg}"
    )


@pytest.mark.asyncio
async def test_legislation_section_resource_fetches_clml():
    """Section resource (HRA 1998 s.1) returns bounded CLML XML."""
    async with Client(gateway) as client:
        try:
            result = await client.read_resource("legislation://ukpga/1998/42/section/1")
        except Exception as e:
            if "WAF" in str(e) or "437" in str(e):
                pytest.skip(f"legislation.gov.uk WAF challenge — see issue #4: {e}")
            raise

    text = result[0].text
    assert text.startswith("<Legislation"), f"Expected CLML, got: {text[:80]!r}"
    # Section reads are bounded; this should be small
    assert len(text) < 100_000


@pytest.mark.asyncio
async def test_legislation_section_point_in_time_via_date_query():
    """Optional ?date=YYYY-MM-DD returns the section as it stood on that date."""
    async with Client(gateway) as client:
        try:
            result = await client.read_resource(
                "legislation://ukpga/1998/42/section/1?date=2020-01-01"
            )
        except Exception as e:
            if "WAF" in str(e) or "437" in str(e):
                pytest.skip(f"legislation.gov.uk WAF challenge — see issue #4: {e}")
            raise

    text = result[0].text
    assert text.startswith("<Legislation"), f"Expected CLML, got: {text[:80]!r}"


@pytest.mark.asyncio
async def test_legislation_toc_resource_returns_lines():
    """TOC resource returns 'id: title' lines from a real Act."""
    async with Client(gateway) as client:
        try:
            result = await client.read_resource("legislation://ukpga/1998/42/toc")
        except Exception as e:
            if "WAF" in str(e) or "437" in str(e):
                pytest.skip(f"legislation.gov.uk WAF challenge — see issue #4: {e}")
            raise

    text = result[0].text
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) > 5, "HRA 1998 should produce a non-trivial TOC"
    assert all(":" in ln for ln in lines), "Each line should be 'id: title'"


@pytest.mark.asyncio
async def test_whole_act_resources_removed():
    """After the 2026-04-19 audit, the whole-Act resources were removed
    because they caused context blowout (5.9MB for DPA 2018) and duplicated
    the capability available via /toc + /section/{n}. Point-in-time is now
    a ?date query param on the bounded resources."""
    async with Client(gateway) as client:
        templates = {t.uriTemplate for t in await client.list_resource_templates()}
    # Deleted templates
    assert "legislation://{type}/{year}/{number}" not in templates
    assert "legislation://{type}/{year}/{number}/{date}" not in templates
    # Surviving bounded templates (with optional ?date)
    section_like = {t for t in templates if "section/{section}" in t}
    toc_like = {t for t in templates if t.endswith("/toc") or "/toc{?" in t}
    assert section_like, f"Expected a section template, got: {templates}"
    assert toc_like, f"Expected a toc template, got: {templates}"
