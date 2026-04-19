"""Resource templates for UK legislation (legislation.gov.uk CLML XML).

Registered on the GATEWAY, not on the sub-MCP, because mounted sub-MCPs
silently break RFC 6570 wildcard substitution. See issue #3.

URI scheme `legislation` is RFC-3986 compliant.
"""

import httpx
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
        "legislation://{type}/{year}/{number}",
        name="UK Legislation (full CLML XML)",
        description="Full text of an Act or SI as CLML XML. Example: legislation://ukpga/2018/12",
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "clml", "full_text"},
    )
    async def legislation_full(type: str, year: str, number: str, ctx: Context) -> str:
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        resp = await client.get(f"{LEGISLATION_BASE}/{type}/{year}/{number}/data.xml")
        resp.raise_for_status()
        return resp.text

    @gateway.resource(
        "legislation://{type}/{year}/{number}/section/{section}",
        name="UK Legislation Section (CLML XML)",
        description="Specific section as CLML XML. Example: legislation://ukpga/2006/46/section/172",
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "clml", "section"},
    )
    async def legislation_section(
        type: str, year: str, number: str, section: str, ctx: Context,
    ) -> str:
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}/data.xml"
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text

    @gateway.resource(
        "legislation://{type}/{year}/{number}/toc",
        name="UK Legislation Table of Contents",
        description=(
            "Flat table of contents (parts, chapters, sections, schedules) as a "
            "newline-separated 'id: title' list. Example: legislation://ukpga/2006/46/toc"
        ),
        mime_type="text/plain",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "toc"},
    )
    async def legislation_toc(type: str, year: str, number: str, ctx: Context) -> str:
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        resp = await client.get(f"{LEGISLATION_BASE}/{type}/{year}/{number}/data.xml")
        resp.raise_for_status()
        items = _parse_toc(resp.text)
        return "\n".join(items)

    @gateway.resource(
        "legislation://{type}/{year}/{number}/{date}",
        name="UK Legislation point-in-time (CLML XML)",
        description=(
            "Act text as it stood on a specific date (point-in-time, immutable). "
            "Date format: YYYY-MM-DD. Example: legislation://ukpga/1998/42/2020-01-01"
        ),
        mime_type="application/xml",
        annotations={"readOnlyHint": True, "idempotentHint": True},
        tags={"legislation", "clml", "point_in_time"},
    )
    async def legislation_point_in_time(
        type: str, year: str, number: str, date: str, ctx: Context,
    ) -> str:
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        resp = await client.get(f"{LEGISLATION_BASE}/{type}/{year}/{number}/{date}/data.xml")
        resp.raise_for_status()
        return resp.text
