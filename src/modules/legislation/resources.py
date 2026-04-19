"""Resource templates for UK legislation (legislation.gov.uk CLML XML).

Registered on the GATEWAY, not on the sub-MCP, because mounted sub-MCPs
silently break RFC 6570 wildcard substitution (see issue #3).

URI scheme `legislation` is RFC-3986 compliant.

## Why these specific resources and no others

The earlier `legislation://{type}/{year}/{number}` (whole Act) and
`legislation://{type}/{year}/{number}/{date}` (whole Act at a date)
were removed 2026-04-19 after the fleet audit flagged them as a
context-blowout footgun — DPA 2018 is 5.9MB, Companies Act 2006 hits
AWS WAF. Bulk consumers can hit legislation.gov.uk directly.

Point-in-time research is preserved via an optional `{?date}` query
parameter on the two bounded resources below. An LLM doing compliance
research reads one section (or the TOC) at a specific historical date,
not the whole Act.
"""

from fastmcp import Context, FastMCP
from lxml import etree

LEGISLATION_BASE = "https://www.legislation.gov.uk"
ATOM_NS = {"leg": "http://www.legislation.gov.uk/namespaces/legislation"}


def _parse_toc(xml_text: str) -> list[str]:
    """Flatten CLML XML into 'id: title' strings, document order."""
    root = etree.fromstring(xml_text.encode())
    items: list[str] = []
    for el in root.iter():
        id_val = el.get("id")
        if id_val:
            title_el = el.find("leg:Title", ATOM_NS)
            if title_el is not None and title_el.text:
                items.append(f"{id_val}: {title_el.text.strip()}")
    return items


def register_legislation_resources(gateway: FastMCP) -> None:
    """Register legislation resource templates on the gateway."""

    @gateway.resource(
        "legislation://{type}/{year}/{number}/section/{section}{?date}",
        name="UK Legislation Section (CLML XML)",
        description=(
            "CLML XML for a specific section of an Act or SI. Example: "
            "legislation://ukpga/2006/46/section/172. "
            "Optional ?date=YYYY-MM-DD returns the section as it stood on "
            "that date (for compliance audits, pre/post-amendment comparisons, "
            "or historical legal research)."
        ),
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "clml", "section"},
    )
    async def legislation_section(
        type: str,
        year: str,
        number: str,
        section: str,
        ctx: Context,
        date: str | None = None,
    ) -> str:
        client = ctx.lifespan_context["legislation_http"]
        if date:
            url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}/{date}/data.xml"
        else:
            url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}/data.xml"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    @gateway.resource(
        "legislation://{type}/{year}/{number}/toc{?date}",
        name="UK Legislation Table of Contents",
        description=(
            "Flat table of contents (parts, chapters, sections, schedules) as a "
            "newline-separated 'id: title' list. Example: "
            "legislation://ukpga/2006/46/toc. Optional ?date=YYYY-MM-DD returns "
            "the TOC as it stood on that date (for historical research)."
        ),
        mime_type="text/plain",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "toc"},
    )
    async def legislation_toc(
        type: str,
        year: str,
        number: str,
        ctx: Context,
        date: str | None = None,
    ) -> str:
        client = ctx.lifespan_context["legislation_http"]
        if date:
            url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/{date}/data.xml"
        else:
            url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/data.xml"
        resp = await client.get(url)
        resp.raise_for_status()
        items = _parse_toc(resp.text)
        return "\n".join(items)
