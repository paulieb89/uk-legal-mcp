"""
Tools for the legislation module.

Search upstream: legislation.gov.uk Atom feed
Full text upstream: legislation.gov.uk API — CLML XML
Rate limit: legislation.gov.uk 3,000 req / 5 min per IP.
"""

import json
import re
from datetime import date

import httpx
from fastmcp import FastMCP, Context
from lxml import etree
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import LegislationResult, LegislationSearchResult, LegislationSection

LEGISLATION_BASE = "https://www.legislation.gov.uk"

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}

_ID_RE = re.compile(r"/id/([a-z]+)/(\d{4})/(\d+)$")


class LegislationSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Search query, e.g. 'data protection personal data'", min_length=1, max_length=500)
    type: str | None = Field(None, description="Filter by type: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts)")
    year: int | None = Field(None, description="Filter by year of enactment", ge=1800, le=2100)


class LegislationGetSectionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: str = Field(..., description="Legislation type code: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts). Use the value from legislation_search results.", min_length=2, max_length=10)
    year: int = Field(..., description="Year of enactment", ge=1800, le=2100)
    number: int = Field(..., description="Chapter or SI number", ge=1)
    section: str = Field(..., description="Section number, e.g. '47' or '12A'. Use the numeric part only — not 'section-47'. Schedules are not currently supported.", min_length=1, max_length=50)


class LegislationGetTocInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    type: str = Field(..., description="Legislation type code: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts). Use the value from legislation_search results.", min_length=2, max_length=10)
    year: int = Field(..., description="Year of enactment", ge=1800, le=2100)
    number: int = Field(..., description="Chapter or SI number", ge=1)


def _parse_clml_section(xml_text: str, section: str) -> LegislationSection:
    """Extract a section from CLML XML."""
    from lxml import etree
    root = etree.fromstring(xml_text.encode())
    ns = {
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }

    def extract_text(el) -> str:
        return " ".join(el.itertext()).strip()

    # Try to find the specific section element
    section_el = root.find(f".//leg:P1[@id='section-{section}']", ns)
    content = extract_text(section_el) if section_el is not None else extract_text(root)

    extent_el = root.find(".//ukm:Extent", ns)
    extent_text = extent_el.get("Value", "") if extent_el is not None else ""
    extent = [e.strip() for e in extent_text.split("+") if e.strip()]

    prospective = "prospective" in xml_text.lower()

    version_date = None
    date_el = root.find(".//ukm:EnactmentDate", ns)
    if date_el is not None:
        try:
            version_date = date.fromisoformat(date_el.get("Date", ""))
        except ValueError:
            pass

    title_el = root.find(".//leg:Title", ns)
    title = title_el.text.strip() if title_el is not None and title_el.text else f"Section {section}"

    return LegislationSection(
        title=title, section_number=section, content=content[:10000],
        in_force=not prospective if extent else None,
        extent=extent or ["England", "Wales", "Scotland", "Northern Ireland"],
        version_date=version_date, prospective=prospective,
    )


def _parse_toc_xml(xml_text: str) -> list[str]:
    """Extract table of contents from CLML XML."""
    from lxml import etree
    root = etree.fromstring(xml_text.encode())
    ns = {"leg": "http://www.legislation.gov.uk/namespaces/legislation"}
    items = []
    for el in root.iter():
        id_val = el.get("id")
        if id_val:
            title_el = el.find("leg:Title", ns)
            if title_el is not None and title_el.text:
                items.append(f"{id_val}: {title_el.text.strip()}")
    return items[:200]


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search",
        annotations={"title": "Search UK Legislation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_search(params: LegislationSearchInput, ctx: Context) -> str:
        """Search UK legislation on legislation.gov.uk.

        Returns ranked results: title, type, year, number, and legislation.gov.uk URL.
        Use legislation_get_toc to explore structure, then legislation_get_section for provisions.

        Args:
            params (LegislationSearchInput): query, optional type filter, optional year.

        Returns:
            str: JSON with results (title, type, year, number, url) and total count.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
            path = f"/{params.type}" if params.type else "/search"
            qp: dict = {"text": params.query, "results-count": 20}
            if params.year:
                qp["year"] = params.year

            resp = await client.get(f"{LEGISLATION_BASE}{path}", params=qp)
            resp.raise_for_status()
            root = etree.fromstring(resp.content)

            total_el = root.findtext(".//os:totalResults", namespaces=ATOM_NS)
            total = int(total_el) if total_el else 0

            results = []
            for entry in root.findall(".//a:entry", namespaces=ATOM_NS):
                title = entry.findtext("a:title", namespaces=ATOM_NS) or "Unknown"
                entry_id = entry.findtext("a:id", namespaces=ATOM_NS) or ""

                m = _ID_RE.search(entry_id)
                if m:
                    leg_type, yr, num = m.group(1), int(m.group(2)), int(m.group(3))
                else:
                    leg_type, yr, num = "unknown", 0, 0

                results.append(LegislationResult(
                    title=title, type=leg_type, year=yr, number=num,
                    score=None, url=f"{LEGISLATION_BASE}/{leg_type}/{yr}/{num}",
                ))

            return LegislationSearchResult(
                results=results, total=total or len(results),
            ).model_dump_json(indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="get_section",
        annotations={"title": "Get Legislation Section", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_get_section(params: LegislationGetSectionInput, ctx: Context) -> str:
        """Retrieve a specific section of a UK Act or Statutory Instrument.

        Returns section text, territorial extent, in-force status, and prospective flag.

        IMPORTANT: Always check 'extent' — a section may apply to England & Wales
        but not Scotland or Northern Ireland.

        Args:
            params (LegislationGetSectionInput): type, year, number, section identifier.

        Returns:
            str: JSON with title, section_number, content, in_force, extent, version_date, prospective.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
            url = f"{LEGISLATION_BASE}/{params.type}/{params.year}/{params.number}/section/{params.section}/data.xml"
            resp = await client.get(url)
            resp.raise_for_status()
            return _parse_clml_section(resp.text, params.section).model_dump_json(indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="get_toc",
        annotations={"title": "Get Legislation Table of Contents", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_get_toc(params: LegislationGetTocInput, ctx: Context) -> str:
        """Retrieve the table of contents for a UK Act or SI.

        Returns structural elements (parts, chapters, sections, schedules) with XML id
        and title, e.g. 'section-47: Definitions'. When calling legislation_get_section,
        pass only the numeric part — use '47', not 'section-47'.

        Args:
            params (LegislationGetTocInput): type, year, number.

        Returns:
            str: JSON array of strings in format 'section-N: Title text'.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
            url = f"{LEGISLATION_BASE}/{params.type}/{params.year}/{params.number}/data.xml"
            resp = await client.get(url)
            resp.raise_for_status()
            return json.dumps(_parse_toc_xml(resp.text), indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})
