"""Resource templates for case law (TNA Find Case Law).

Registered on the GATEWAY, not on the sub-MCP, because mounted sub-MCPs
silently break RFC 6570 wildcard substitution. See issue #3.

URI scheme is `judgment` (not `case_law`) because RFC 3986 forbids
underscores in scheme names — but allows them in the authority position.
"""

import httpx
from fastmcp import Context, FastMCP

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"


def register_case_law_resources(gateway: FastMCP) -> None:
    """Register case-law resource templates on the gateway."""

    @gateway.resource(
        "judgment://{slug*}",
        name="UK Court Judgment (LegalDocML XML)",
        description=(
            "Full LegalDocML XML for a TNA Find Case Law judgment. "
            "Slug formats: 'uksc/2024/12', 'ewca/civ/2023/450', or a UUID for "
            "post-April 2025 docs. Use `case_law_search` first to discover slugs."
        ),
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"case_law", "tna", "judgment"},
    )
    async def judgment_xml(slug: str, ctx: Context) -> str:
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        url = f"{TNA_BASE}/{slug.lstrip('/')}/data.xml"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
