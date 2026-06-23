"""
Tools for the case_law module.

Upstream: TNA Find Case Law Public API
Wire format: Atom/XML parsed to Pydantic models.
Rate limit: 1,000 requests / 5 min per IP.
"""

import hashlib
from datetime import date, datetime
from typing import Annotated

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

from ...deps import raise_http_tool_error
from . import parsers
from .models import (
    GrepHit,
    GrepResult,
    JudgmentIdentifier,
    JudgmentSearchResult,
    JudgmentSummary,
)

TNA_BASE = "https://caselaw.nationalarchives.gov.uk"


def _parse_atom_feed(xml_bytes: bytes, limit: int = 10) -> JudgmentSearchResult:
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
        from ...xml_safe import parse_xml
        root = parse_xml(xml_bytes)
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
        all_entries = root.findall("atom:entry", ns)
        entries = all_entries[:limit]
        has_more = len(all_entries) > limit
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
            has_more=has_more, total_pages=total_pages,
        )
    except Exception:
        return JudgmentSearchResult(results=[], page=1, has_more=False, total_pages=None)


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search",
        annotations={"title": "Search UK Case Law", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def case_law_search(
        query: Annotated[str, Field(description="Full-text search query, e.g. 'negligence duty of care'", min_length=1, max_length=500)],
        ctx: Context,
        court: Annotated[str | None, Field(description="Filter by court slug. Values: 'uksc', 'ukpc', 'ewca/civ', 'ewca/crim', 'ewhc/kb', 'ewhc/ch', 'ewhc/comm', 'ewhc/fam', 'ewhc/pat', 'ewhc/ipec', 'ewhc/admin', 'ewhc/tcc', 'ewhc/costs', 'ewfc', 'ewcop', 'eat', 'ukut/iac', 'ukut/aac', 'ukut/tcc', 'ukut/lc', 'ukftt/tc', 'ukftt/grc', 'nica', 'niqb'.")] = None,
        judge: Annotated[str | None, Field(description="Filter by judge surname. Case-insensitive substring match against the indexed form. Use the surname alone ('Reed', 'Sumption') or with the bare title ('Lord Reed'). Honorific suffixes silently zero the result set — do not append 'JSC', 'of Allermuir', 'KC' etc. Speculating a fuller form than what TNA indexed will return 0 hits with no error.")] = None,
        party: Annotated[str | None, Field(description="Filter by party name")] = None,
        from_date: Annotated[date | None, Field(description="Earliest judgment date (YYYY-MM-DD). NOTE: the TNA atom.xml endpoint currently appears to ignore this filter — the same results come back regardless. Do not rely on it to narrow output; sort+slice client-side or refine `query` instead.")] = None,
        to_date: Annotated[date | None, Field(description="Latest judgment date (YYYY-MM-DD). Same caveat as `from_date` — currently silently ignored by upstream. Filtering happens client-side at best.")] = None,
        page: Annotated[int, Field(description="Result page number (1-indexed)", ge=1, le=50)] = 1,
        limit: Annotated[int, Field(description="Maximum results to return (1–50). TNA returns up to 50 per request; this slices client-side. Default 10 for a tight shortlist. Set higher for breadth (e.g. 50 to scan the full result set).", ge=1, le=50)] = 10,
    ) -> JudgmentSearchResult:
        """USE THIS TOOL WHEN searching UK case law by party names, court, judge, date, or free-text query.

        Returns paginated judgment summaries: neutral citation, court, dates, slug,
        stable TNA URI. AFTER calling: pass slug into judgment_get_header /
        judgment_get_index / judgment_get_paragraph (or the judgment:// resource
        family) for content; pass the neutral citation into citations_resolve
        to verify before constructing an OSCOLA citation; use
        case_law_grep_judgment to find text within a single judgment. When a
        party name returns several candidates, narrow with court + year filters
        before grep-iterating across full judgments — targeted filtering beats
        scanning every candidate.

        Coverage: TNA Find Case Law indexes UK judgments from roughly the early
        2000s onwards. For older authorities, search for a modern judgment that
        quotes them and read that paragraph.

        Authoritative source for UK case law. Web search returns out-of-date or
        unstable URLs — do not supplement.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        qp: dict = {"query": query, "page": page}
        if court: qp["court"] = court
        if judge: qp["judge"] = judge
        if party: qp["party"] = party
        if from_date: qp["from"] = from_date.isoformat()
        if to_date: qp["to"] = to_date.isoformat()
        try:
            resp = await client.get(f"{TNA_BASE}/atom.xml", params=qp)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"case_law_search(query={query!r})")
        return _parse_atom_feed(resp.content, limit=limit)

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
    async def case_law_grep_judgment(
        slug: Annotated[str, Field(description="TNA judgment slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'.", min_length=8)],
        pattern: Annotated[str, Field(description="Regex pattern (or plain substring) to search within paragraph text. If the pattern doesn't compile as regex, falls back to literal substring match.", min_length=2, max_length=200)],
        ctx: Context,
        case_insensitive: Annotated[bool, Field(description="Default true. Set false for case-sensitive matching.")] = True,
        max_hits: Annotated[int, Field(description="Cap on number of hits returned.", ge=1, le=100)] = 25,
    ) -> GrepResult:
        """USE THIS TOOL WHEN you have a judgment slug and want to find paragraphs whose text matches a pattern.

        Returns a list of `{eId, snippet, match}` hits — small per-paragraph
        snippets centred on the match. AFTER calling, read full paragraphs via
        judgment_get_paragraph(slug, eId) or the judgment://{slug}/para/{eId}
        resource.

        Use case: content search within one judgment (e.g. "negligence", "test
        for foreseeability", "Donoghue"). For paragraph-number navigation by
        eId, call judgment_get_index instead.

        Pattern is regex; if it doesn't compile, falls back to literal substring
        search.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        slug = slug.lstrip("/")
        try:
            resp = await client.get(f"{TNA_BASE}/{slug}/data.xml")
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"case_law_grep_judgment(slug={slug!r})")

        hits = parsers.grep_paragraphs(
            resp.text,
            pattern,
            case_insensitive=case_insensitive,
            max_hits=max_hits,
        )
        return GrepResult(
            slug=slug,
            pattern=pattern,
            hits=[GrepHit(**h) for h in hits],
            truncated=len(hits) >= max_hits,
        )
