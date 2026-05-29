"""
Tools for the case_law module.

Upstream: TNA Find Case Law Public API
Wire format: Atom/XML parsed to Pydantic models.
Rate limit: 1,000 requests / 5 min per IP.
"""

import hashlib
from datetime import date, datetime

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from . import parsers
from .models import (
    CaseLawGrepInput,
    GrepHit,
    GrepResult,
    JudgmentIdentifier,
    JudgmentSearchResult,
    JudgmentSummary,
)

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
    judge: str | None = Field(None, description=(
        "Filter by judge surname. Case-insensitive substring match against the indexed form. "
        "Use the surname alone ('Reed', 'Sumption') or with the bare title ('Lord Reed'). "
        "Honorific suffixes silently zero the result set — do not append 'JSC', 'of Allermuir', "
        "'KC' etc. Speculating a fuller form than what TNA indexed will return 0 hits with no error."
    ))
    party: str | None = Field(None, description="Filter by party name")
    from_date: date | None = Field(None, description="Earliest judgment date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="Latest judgment date (YYYY-MM-DD)")
    page: int = Field(1, description="Result page number (1-indexed)", ge=1, le=50)


def _parse_atom_feed(xml_bytes: bytes) -> JudgmentSearchResult:
    """Parse TNA Atom feed into JudgmentSearchResult.

    Two TNA contract changes this parser now adopts:

    1. The namespace URI for TNA-specific elements is the bare host
       ``https://caselaw.nationalarchives.gov.uk`` with conventional
       prefix ``tna:`` — NOT the former ``/ns/properties`` path with
       prefix ``uk:``. All tna:-prefixed lookups use the new URI.
    2. The canonical slug (e.g. ``uksc/2024/12``) is carried as the
       ``slug`` attribute on ``<tna:identifier type="ukncn">`` — a
       dedicated, typed element. ``<atom:id>`` now carries an internal
       UUID and is no longer a valid slug source.

    See ``docs/patterns/pilot-case-law-get-judgment-discovery.md`` for
    the full root-cause analysis.
    """
    try:
        from lxml import etree
        root = etree.fromstring(xml_bytes)
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "tna": "https://caselaw.nationalarchives.gov.uk",
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
            title_el = entry.find("atom:title", ns)
            published_el = entry.find("atom:published", ns)
            updated_el = entry.find("atom:updated", ns)
            author_name_el = entry.find("atom:author/atom:name", ns)

            ncn_ident_el = entry.find('tna:identifier[@type="ukncn"]', ns)
            slug = ncn_ident_el.get("slug", "") if ncn_ident_el is not None else ""
            if not slug:
                for link in entry.findall("atom:link", ns):
                    if link.get("rel") == "alternate" and not link.get("type"):
                        href = link.get("href", "")
                        if href.startswith(f"{TNA_BASE}/"):
                            slug = href[len(TNA_BASE) + 1 :].rstrip("/")
                            break

            identifiers = []
            if ncn_ident_el is not None and ncn_ident_el.text:
                ncn_text = ncn_ident_el.text.strip()
                if ncn_text:
                    identifiers.append(
                        JudgmentIdentifier(type="ukncn", value=ncn_text, slug=slug)
                    )

            xml_url = f"{TNA_BASE}/{slug}/data.xml" if slug else None
            pdf_url = f"{TNA_BASE}/{slug}/data.pdf" if slug else None
            for link in entry.findall("atom:link", ns):
                link_type = link.get("type", "")
                if link.get("rel") == "alternate" and "xml" in link_type:
                    xml_url = link.get("href", xml_url)
                elif "pdf" in link_type:
                    pdf_url = link.get("href", pdf_url)

            title_text = title_el.text.strip() if title_el is not None and title_el.text else slug
            published_text = (published_el.text or "1970-01-01T00:00:00Z").strip()
            updated_text = (updated_el.text or published_text).strip()
            court_text = author_name_el.text.strip() if author_name_el is not None and author_name_el.text else None
            summaries.append(JudgmentSummary(
                uri=slug, title=title_text,
                court=court_text,
                published=datetime.fromisoformat(published_text.replace("Z", "+00:00")),
                updated=datetime.fromisoformat(updated_text.replace("Z", "+00:00")),
                identifiers=identifiers,
                content_hash=hashlib.sha256((xml_url or "").encode()).hexdigest() if xml_url else None,
                xml_url=xml_url, pdf_url=pdf_url,
                next_steps=({
                    "header": f"judgment://{slug}/header",
                    "index": f"judgment://{slug}/index",
                    "paragraph_template": f"judgment://{slug}/para/{{eId}}",
                    "grep_tool": f"case_law_grep_judgment(slug={slug!r}, pattern=...)",
                } if slug else {}),
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
    async def case_law_search(params: CaseLawSearchInput, ctx: Context) -> JudgmentSearchResult:
        """Search UK case law via the TNA Find Case Law API.

        Returns paginated judgment summaries: neutral citations, court, dates, stable URIs.
        Use the judgment://{slug}/header resource to inspect a result, then
        judgment://{slug}/index to discover paragraphs and judgment://{slug}/para/{eId}
        to read individual paragraphs. For content-based discovery within a
        judgment, use case_law_grep_judgment.

        Args:
            params: CaseLawSearchInput with query, optional filters (court, judge,
                party, from_date, to_date), and page number.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        qp: dict = {"query": params.query, "page": params.page}
        if params.court: qp["court"] = params.court
        if params.judge: qp["judge"] = params.judge
        if params.party: qp["party"] = params.party
        if params.from_date: qp["from"] = params.from_date.isoformat()
        if params.to_date: qp["to"] = params.to_date.isoformat()
        resp = await client.get(f"{TNA_BASE}/atom.xml", params=qp)
        resp.raise_for_status()
        return _parse_atom_feed(resp.content)

    @mcp.tool(
        name="grep_judgment",
        annotations={
            "title": "Search within a UK Court Judgment",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def case_law_grep_judgment(params: CaseLawGrepInput, ctx: Context) -> GrepResult:
        """Find paragraphs in a single judgment whose text matches a pattern.

        Returns a list of `{eId, snippet, match}` hits — small per-paragraph
        snippets centred on the match — so the LLM can decide which full
        paragraphs to read via judgment://{slug}/para/{eId}.

        Content-based search within one judgment (e.g. "negligence", "test for
        foreseeability", "Donoghue"). For paragraph-number navigation, read
        judgment://{slug}/index instead.

        Pattern is regex; if it doesn't compile, falls back to literal
        substring search.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        slug = params.slug.lstrip("/")
        resp = await client.get(f"{TNA_BASE}/{slug}/data.xml")
        resp.raise_for_status()

        hits = parsers.grep_paragraphs(
            resp.text,
            params.pattern,
            case_insensitive=params.case_insensitive,
            max_hits=params.max_hits,
        )
        return GrepResult(
            slug=slug,
            pattern=params.pattern,
            hits=[GrepHit(**h) for h in hits],
            truncated=len(hits) >= params.max_hits,
        )
