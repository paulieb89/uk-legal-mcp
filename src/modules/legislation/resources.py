"""Resource templates for the legislation module."""

import httpx
from fastmcp import FastMCP

from ...deps import SHARED_HEADERS

LEGISLATION_BASE = "https://www.legislation.gov.uk"


def register_resources(mcp: FastMCP) -> None:

    @mcp.resource("legislation://{type}/{year}/{number}")
    async def legislation_full_text(type: str, year: str, number: str) -> str:
        """Full current text of a UK Act or SI as CLML XML.

        URI format: legislation://ukpga/2018/12 → UK GDPR Implementation Act 2018 c.12
        """
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/data.xml"
        async with httpx.AsyncClient(headers=SHARED_HEADERS, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    @mcp.resource("legislation://{type}/{year}/{number}/section/{section}")
    async def legislation_section_text(type: str, year: str, number: str, section: str) -> str:
        """Text of a specific section as CLML XML."""
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}/data.xml"
        async with httpx.AsyncClient(headers=SHARED_HEADERS, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    @mcp.resource("legislation://{type}/{year}/{number}/{date}")
    async def legislation_point_in_time(type: str, year: str, number: str, date: str) -> str:
        """Act text as it stood on a specific date (point-in-time version).

        Date format: YYYY-MM-DD. Point-in-time versions are immutable — safe to cache indefinitely.
        """
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/{date}/data.xml"
        async with httpx.AsyncClient(headers=SHARED_HEADERS, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
