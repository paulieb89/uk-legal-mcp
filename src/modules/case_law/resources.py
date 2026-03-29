"""Resource templates for the case_law module."""

import httpx
from fastmcp import FastMCP

from ...deps import SHARED_HEADERS

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"


def register_resources(mcp: FastMCP) -> None:
    """Register case_law resource templates."""

    @mcp.resource("case_law://{uri}")
    async def case_law_document(uri: str) -> str:
        """Full judgment XML text for a TNA URI.

        URI format: court/year/number (e.g. 'uksc/2024/12') or UUID for post-April 2025 docs.
        """
        clean = uri.lstrip("/")
        url = f"{TNA_BASE}/{clean}/data.xml"
        async with httpx.AsyncClient(headers=SHARED_HEADERS, follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text

    @mcp.resource("case_law://{uri}/pdf")
    async def case_law_pdf_url(uri: str) -> str:
        """Returns the PDF URL for a judgment (redirect URL, not the binary content)."""
        clean = uri.lstrip("/")
        return f"{TNA_BASE}/{clean}/data.pdf"
