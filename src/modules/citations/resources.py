"""Resource templates for the citations module."""

import json

from fastmcp import FastMCP

from .patterns import resolve_neutral_citation, resolve_si, TNA_BASE
from .models import CitationType


def register_resources(mcp: FastMCP) -> None:

    @mcp.resource("citations://resolve/{citation_slug}")
    async def resolve_citation_resource(citation_slug: str) -> str:
        """Canonical URL for a parsed citation.

        citation_slug format:
          - Neutral: 'uksc-2024-12'  (court-year-number, hyphens)
          - SI: 'si-2018-1234'
        """
        parts = citation_slug.lower().split("-")
        try:
            if parts[0] == "si" and len(parts) == 3:
                url = resolve_si(int(parts[1]), int(parts[2]))
                return json.dumps({"citation_slug": citation_slug, "resolved_url": url})
            elif len(parts) == 3:
                court = parts[0].upper()
                year = int(parts[1])
                number = int(parts[2])
                url = resolve_neutral_citation(year, court, number)
                return json.dumps({"citation_slug": citation_slug, "resolved_url": url})
        except (ValueError, IndexError):
            pass
        return json.dumps({"citation_slug": citation_slug, "resolved_url": None, "error": "Unrecognised slug format"})

    @mcp.resource("citations://network/{tna_uri}")
    async def citation_network_resource(tna_uri: str) -> str:
        """Citation graph as JSON for a TNA judgment URI.

        tna_uri: court/year/number (e.g. 'uksc/2024/12')
        Returns the same structure as citations_network tool.
        """
        import httpx
        from .patterns import AMBIGUOUS_COURTS
        # Delegate to a raw fetch — tools handle the real logic
        url = f"{TNA_BASE}/{tna_uri}/data.xml"
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return json.dumps({"tna_uri": tna_uri, "xml_length": len(resp.text)})
