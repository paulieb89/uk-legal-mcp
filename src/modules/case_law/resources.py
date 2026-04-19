"""Resource templates for case law (TNA Find Case Law).

Three sub-path templates that drill down into a LegalDocML judgment by
its native paragraph identifiers (`eId`). Avoids the 56k-token blowout
the bare `judgment://{slug*}` (Phase 3) caused.

Registered on the GATEWAY, not on the sub-MCP, because mounted sub-MCPs
silently break RFC 6570 wildcard substitution (issue #3).

URI scheme is `judgment` (not `case_law`) — RFC 3986 forbids underscores
in scheme names.
"""

import httpx
from fastmcp import Context, FastMCP

from . import parsers

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"


async def _fetch_xml(slug: str, ctx: Context) -> str:
    """Fetch the full LegalDocML XML for a judgment slug. Cached upstream
    by the case_law sub-module's ResponseCachingMiddleware (1h TTL)."""
    client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
    url = f"{TNA_BASE}/{slug.lstrip('/')}/data.xml"
    resp = await client.get(url)
    resp.raise_for_status()
    return resp.text


def register_case_law_resources(gateway: FastMCP) -> None:
    """Register case-law resource templates on the gateway."""

    @gateway.resource(
        "judgment://{slug*}/header",
        name="UK Court Judgment — metadata header",
        description=(
            "Metadata for a TNA judgment: parties, judges, neutral citation, "
            "court, dates. ~1,000 tokens. Call this first to understand what "
            "a judgment is. Slug examples: 'uksc/2024/12', 'ewca/civ/2023/450'."
        ),
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"case_law", "tna", "judgment"},
    )
    async def judgment_header(slug: str, ctx: Context) -> str:
        xml = await _fetch_xml(slug, ctx)
        return parsers.extract_header(xml)

    @gateway.resource(
        "judgment://{slug*}/index",
        name="UK Court Judgment — paragraph index",
        description=(
            "Navigation index: 'eId: first_line' rows for every paragraph "
            "in the judgment (~4,000 tokens for a typical Supreme Court case). "
            "Read this to discover paragraph identifiers, then drill into "
            "specific paragraphs via judgment://{slug}/para/{eId}. To find "
            "paragraphs by content, use the case_law_grep_judgment tool."
        ),
        mime_type="text/plain",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"case_law", "tna", "judgment", "navigation"},
    )
    async def judgment_index(slug: str, ctx: Context) -> str:
        xml = await _fetch_xml(slug, ctx)
        return parsers.extract_index(xml)

    @gateway.resource(
        "judgment://{slug*}/para/{eId}",
        name="UK Court Judgment — single paragraph",
        description=(
            "A single <paragraph> element by its LegalDocML eId (e.g. 'para_12'). "
            "Returns the paragraph and any nested sub-paragraphs. Typical size "
            "400-1,700 tokens. Use the index resource to discover available eIds."
        ),
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"case_law", "tna", "judgment", "paragraph"},
    )
    async def judgment_paragraph(slug: str, eId: str, ctx: Context) -> str:
        xml = await _fetch_xml(slug, ctx)
        return parsers.extract_paragraph(xml, eId)
