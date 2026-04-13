"""
Tools for the case_law module.

Upstream: TNA Find Case Law Public API
Wire format: Atom/XML parsed to Pydantic models.
Rate limit: 1,000 requests / 5 min per IP.
"""

import hashlib
import json
from datetime import date, datetime

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import JudgmentIdentifier, JudgmentSearchResult, JudgmentSummary

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"


class CaseLawSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description="Full-text search query, e.g. 'negligence duty of care'", min_length=1, max_length=500)
    court: str | None = Field(None, description=(
        "Filter by court slug. Values: 'uksc', 'ukpc', "
        "'ewca/civ', 'ewca/crim', 'ewhc/kb', 'ewhc/ch', 'ewhc/comm', "
        "'ewhc/fam', 'ewhc/pat', 'ewhc/ipec', 'ewhc/admin', 'ewhc/tcc', "
        "'ewhc/costs', 'ewfc', 'ewcop', 'eat', "
        "'ukut/iac', 'ukut/aac', 'ukut/tcc', 'ukut/lc', "
        "'ukftt/tc', 'ukftt/grc', 'nica', 'niqb'."
    ))
    judge: str | None = Field(None, description="Filter by judge surname")
    party: str | None = Field(None, description="Filter by party name")
    from_date: date | None = Field(None, description="Earliest judgment date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="Latest judgment date (YYYY-MM-DD)")
    page: int = Field(1, description="Result page number (1-indexed)", ge=1, le=50)


class CaseLawGetJudgmentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    uri: str = Field(
        ...,
        description=(
            "TNA judgment URI slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'. "
            "Always use the 'uri' field returned by case_law_search — do not construct manually."
        ),
        min_length=8,
    )


def _parse_atom_feed(xml_bytes: bytes) -> JudgmentSearchResult:
    """Parse TNA Atom feed into JudgmentSearchResult."""
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "uk": "https://caselaw.nationalarchives.gov.uk/ns/properties",
            "os": "http://a9.com/-/spec/opensearch/1.1/",
        }
        total_el = root.find(".//os:totalResults", ns)
        per_page_el = root.find(".//os:itemsPerPage", ns)
        start_el = root.find(".//os:startIndex", ns)
        total = int(total_el.text) if total_el is not None else 0
        per_page = int(per_page_el.text) if per_page_el is not None else 10
        start = int(start_el.text) if start_el is not None else 1
        current_page = max(1, (start - 1) // per_page + 1) if per_page else 1
        total_pages = (total + per_page - 1) // per_page if per_page else None
        entries = root.findall("atom:entry", ns)
        summaries = []
        for entry in entries:
            uri_el = entry.find("atom:id", ns)
            title_el = entry.find("atom:title", ns)
            published_el = entry.find("atom:published", ns)
            updated_el = entry.find("atom:updated", ns)
            court_el = entry.find(".//uk:court", ns)
            raw_uri = uri_el.text.strip() if uri_el is not None else ""
            slug = raw_uri.replace(f"{TNA_BASE}/", "").lstrip("/")
            identifiers = []
            for ncn_el in entry.findall(".//uk:cite", ns):
                ncn_text = ncn_el.text.strip() if ncn_el.text else ""
                if ncn_text:
                    identifiers.append(JudgmentIdentifier(type="ukncn", value=ncn_text, slug=slug))
            xml_url = f"{TNA_BASE}/{slug}/data.xml" if slug else None
            pdf_url = f"{TNA_BASE}/{slug}/data.pdf" if slug else None
            for link in entry.findall("atom:link", ns):
                if link.get("rel") == "alternate" and "xml" in link.get("type", ""):
                    xml_url = link.get("href", xml_url)
                elif "pdf" in link.get("type", ""):
                    pdf_url = link.get("href", pdf_url)
            title_text = title_el.text.strip() if title_el is not None and title_el.text else slug
            published_text = (published_el.text or "1970-01-01T00:00:00Z").strip()
            updated_text = (updated_el.text or published_text).strip()
            summaries.append(JudgmentSummary(
                uri=slug, title=title_text,
                court=court_el.text.strip() if court_el is not None else None,
                published=datetime.fromisoformat(published_text.replace("Z", "+00:00")),
                updated=datetime.fromisoformat(updated_text.replace("Z", "+00:00")),
                identifiers=identifiers,
                content_hash=hashlib.sha256((xml_url or "").encode()).hexdigest() if xml_url else None,
                xml_url=xml_url, pdf_url=pdf_url,
            ))
        return JudgmentSearchResult(
            results=summaries, page=current_page,
            has_more=(start + len(entries) - 1) < total, total_pages=total_pages,
        )
    except Exception:
        return JudgmentSearchResult(results=[], page=1, has_more=False, total_pages=None)


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search",
        annotations={"title": "Search UK Case Law", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def case_law_search(params: CaseLawSearchInput, ctx: Context) -> str:
        """Search UK case law via the TNA Find Case Law API.

        Returns paginated judgment summaries: neutral citations, court, dates, stable URIs.
        Use case_law_get_judgment with the returned uri to fetch full text.

        Args:
            params (CaseLawSearchInput): query, optional filters (court, judge, party,
                from_date, to_date), page number.

        Returns:
            str: JSON with results (uri, title, court, published, updated,
                identifiers, xml_url, pdf_url), page, has_more, total_pages.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
            qp: dict = {"query": params.query, "page": params.page}
            if params.court: qp["court"] = params.court
            if params.judge: qp["judge"] = params.judge
            if params.party: qp["party"] = params.party
            if params.from_date: qp["from"] = params.from_date.isoformat()
            if params.to_date: qp["to"] = params.to_date.isoformat()
            resp = await client.get(f"{TNA_BASE}/atom.xml", params=qp)
            resp.raise_for_status()
            return _parse_atom_feed(resp.content).model_dump_json(indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="get_judgment",
        annotations={"title": "Get Full Judgment Text", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def case_law_get_judgment(params: CaseLawGetJudgmentInput, ctx: Context) -> dict:
        """Retrieve the full LegalDocML XML for a judgment by TNA URI slug.

        Returns a dict containing the LegalDocML XML as a string value. The dict
        return type (rather than str) lets FastMCP auto-generate an MCP-spec-
        compliant structured output schema — matching the documented pattern
        for object-like tool returns. XML content is capped at 80,000 chars by
        gateway middleware.

        Args:
            params (CaseLawGetJudgmentInput): uri — TNA slug e.g. 'uksc/2024/12'.

        Returns:
            dict: Keys 'uri', 'format', 'content' (LegalDocML XML string),
                or 'error' on failure.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
            uri = params.uri.lstrip("/")
            resp = await client.get(f"{TNA_BASE}/{uri}/data.xml")
            resp.raise_for_status()
            return {
                "uri": uri,
                "format": "legaldocml-xml",
                "content": resp.text,
            }
        except Exception as e:
            return {"error": format_http_error(e)}
