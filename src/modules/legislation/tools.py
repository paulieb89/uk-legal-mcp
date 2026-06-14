"""
Tools for the legislation module.

Search upstream: legislation.gov.uk Atom feed
Full text upstream: legislation.gov.uk API — CLML XML
Rate limit: legislation.gov.uk 3,000 req / 5 min per IP.
"""

import json
import re
from datetime import date
from typing import Annotated

import httpx
from fastmcp import FastMCP, Context
from lxml import etree, html

from ...xml_safe import parse_xml
from pydantic import Field

from ...deps import LegislationUpstreamError, format_http_error
from .models import LegislationResult, LegislationSearchResult, LegislationSection, LegislationTOC

LEGISLATION_BASE = "https://www.legislation.gov.uk"

ATOM_NS = {
    "a": "http://www.w3.org/2005/Atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}

_ID_RE = re.compile(r"/id/([a-z]+)/(\d{4})/(\d+)$")
# Pre-1963 Acts use regnal year IDs: /id/ukpga/Eliz2/5-6/31
_REGNAL_ID_RE = re.compile(r"/id/([a-z]+)/[A-Za-z].+/(\d+)$")

# Welsh Acts (asc) and Welsh SIs (wsi) use <title type="xhtml"> with bilingual
# <span xml:lang="en|cy"> children. All other UK legislation types use plain text.
_XHTML_NS = "http://www.w3.org/1999/xhtml"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"


def _entry_title(entry) -> str:
    title_el = entry.find("a:title", namespaces=ATOM_NS)
    if title_el is None:
        return "Unknown"
    if title_el.get("type") == "xhtml":
        # Spans are wrapped in <div xmlns="...xhtml">, so search recursively.
        for span in title_el.findall(f".//{{{_XHTML_NS}}}span"):
            if span.get(_XML_LANG, "") == "en" and span.text:
                return span.text
        for span in title_el.findall(f".//{{{_XHTML_NS}}}span"):
            if span.text:
                return span.text
        return "Unknown"
    return title_el.text or "Unknown"


def _normalise_section_id(section: str) -> str:
    """Accept common TOC/resource forms and return the section token expected upstream."""
    value = section.strip()
    if ":" in value:
        value = value.split(":", 1)[0].strip()
    for prefix in ("section-", "article-", "regulation-"):
        if value.lower().startswith(prefix):
            return value[len(prefix):]
    return value


def _parse_html_section(html_text: str, section: str, max_chars: int, warning: str) -> LegislationSection:
    """Best-effort parser for legislation.gov.uk HTML section pages.

    This fallback intentionally returns conservative metadata. It is better to
    return the section text with explicit unknowns than to fail hard when the
    CLML endpoint is blocked for large Acts.
    """
    root = html.fromstring(html_text.encode())

    candidates = root.xpath(
        "//*[@id='content'] | //*[@id='viewLegSnippet'] | //*[@class='LegSnippet'] | "
        "//main | //article | //body"
    )
    container = candidates[0] if candidates else root

    # Remove navigation/chrome that tends to pollute section text.
    for noisy in container.xpath(".//script | .//style | .//nav | .//footer | .//header | .//form"):
        parent = noisy.getparent()
        if parent is not None:
            parent.remove(noisy)

    raw_content = " ".join(container.text_content().split())
    original_length = len(raw_content)
    truncated = original_length > max_chars
    content = raw_content[:max_chars] + " …[truncated]" if truncated else raw_content

    title = f"Section {section}"
    heading = container.xpath(".//h1/text() | .//h2/text() | .//h3/text()")
    if heading:
        title = " ".join(heading[0].split())

    return LegislationSection(
        title=title,
        section_number=section,
        content=content,
        content_truncated=truncated,
        original_length=original_length,
        in_force=None,
        extent=[],
        version_date=None,
        prospective=None,
        source_format="html_fallback",
        warnings=[
            warning,
            "CLML metadata such as exact extent, version date, and in-force status could not be reliably extracted from the HTML fallback.",
        ],
    )


_EXTENT_CODE_MAP = {
    "E": "England",
    "W": "Wales",
    "S": "Scotland",
    "N.I.": "Northern Ireland",
    "NI": "Northern Ireland",
}


def _find_restrict_start_date(section_el, root) -> str | None:
    """Walk from section_el up the ancestor chain for the most-specific
    RestrictStartDate. This is the section's effective version date — when
    the restriction (the current revised state of the section) took effect.

    For a section repealed on 2026-05-01, this returns "2026-05-01" — the
    date the repeal commenced. For a section never amended, returns the
    Act-level RestrictStartDate.
    """
    candidates = [section_el] if section_el is not None else []
    if section_el is not None:
        for ancestor in section_el.iterancestors():
            candidates.append(ancestor)
    if root is not None and root not in candidates:
        candidates.append(root)
    for el in candidates:
        rsd = el.get("RestrictStartDate")
        if rsd:
            return rsd
    return None


def _section_is_repealed(section_el, ns: dict) -> bool:
    """Detect whether a section's heading is wrapped in <Repeal RetainText="true">.

    In CLML revised legislation, repealed text is preserved for historical
    reading and marked with <Repeal> wrappers. The Housing Act 1988 s.21
    payload has 89 such elements (every <Text> and <Pnumber>) sharing one
    ChangeId — the wholesale repeal of Chapter II by the Renters' Rights
    Act 2025.

    The encoding is the OPPOSITE of what one might first guess: <Repeal>
    wraps the repealed runs of text and lives INSIDE the structural
    elements that semantically own them. So inside the section's <Title>
    there is a <Repeal> child wrapping the heading text. The reliable
    section-level signal is therefore: does the section's <Title> contain
    a <Repeal> child? If yes, the heading itself is marked repealed.
    """
    if section_el is None:
        return False
    title = section_el.find("leg:Title", ns)
    if title is None:
        # Fall back: any Title in the section's subtree
        title = section_el.find(".//leg:Title", ns)
    if title is None:
        return False
    return title.find(".//leg:Repeal", ns) is not None


def _extent_codes_to_names(code_string: str) -> list[str]:
    """Map a CLML extent code string (e.g. 'E+W+S+N.I.') to canonical names.

    Returns names in the order they appear. Unknown codes are skipped silently
    so a malformed upstream value can't fabricate jurisdictions.
    """
    if not code_string:
        return []
    names: list[str] = []
    for code in code_string.split("+"):
        code = code.strip()
        if code in _EXTENT_CODE_MAP:
            names.append(_EXTENT_CODE_MAP[code])
    return names


def _find_restrict_extent(section_el, root) -> tuple[str, bool]:
    """Walk from section_el up the ancestor chain to find the most-specific
    RestrictExtent attribute. Falls back to the root element's value.

    Returns (extent_code_string, is_section_specific) where is_section_specific
    is True if the extent came from the section's own element (rather than
    inherited from a parent / Act-wide default).
    """
    candidates = [section_el] if section_el is not None else []
    if section_el is not None:
        for ancestor in section_el.iterancestors():
            candidates.append(ancestor)
    if root is not None and root not in candidates:
        candidates.append(root)

    for idx, el in enumerate(candidates):
        ext = el.get("RestrictExtent")
        if ext:
            return ext, idx == 0
    return "", False


def _parse_clml_section(xml_text: str, section: str, max_chars: int) -> LegislationSection:
    """Extract a section from CLML XML, truncating content to max_chars.

    Extent comes from the RestrictExtent attribute on the section's element
    (or its nearest ancestor that carries one), mapped through the canonical
    code→name table. When no RestrictExtent is found, `extent` is the empty
    list per the documented contract — never fabricated.

    Older fixtures may carry a doctored `<ukm:Extent Value="..."/>` element;
    we fall back to that to keep test fixtures portable, but real CLML uses
    RestrictExtent.
    """
    root = parse_xml(xml_text)
    ns = {
        "leg": "http://www.legislation.gov.uk/namespaces/legislation",
        "ukm": "http://www.legislation.gov.uk/namespaces/metadata",
    }

    def extract_text(el) -> str:
        return " ".join(el.itertext()).strip()

    # Section element: prefer the P1group structural container because it
    # carries RestrictExtent AND wraps the section's <Title>. The id attribute
    # may live on either P1group or P1 depending on the Act's vintage:
    #   1. P1group[@id='section-N']  — newer revised CLML
    #   2. P1[@id='section-N']        — older / many real Acts (e.g. Housing
    #      Act 1988). When the id is on P1, walk up to its P1group parent so
    #      we still see the Title and RestrictExtent above.
    # NB: lxml elements have no useful truth-testing, so chain with `is None`.
    section_el = root.find(f".//leg:P1group[@id='section-{section}']", ns)
    if section_el is None:
        p1 = root.find(f".//leg:P1[@id='section-{section}']", ns)
        if p1 is not None:
            parent = p1.getparent()
            if parent is not None and etree.QName(parent).localname == "P1group":
                section_el = parent
            else:
                section_el = p1
    raw_content = extract_text(section_el) if section_el is not None else extract_text(root)
    original_length = len(raw_content)
    truncated = original_length > max_chars
    content = raw_content[:max_chars] + " …[truncated]" if truncated else raw_content

    # Extent: walk the ancestor chain for the most-specific RestrictExtent,
    # then fall back to legacy ukm:Extent element (used by older fixtures).
    extent_codes, _ = _find_restrict_extent(section_el, root)
    if not extent_codes:
        legacy_extent_el = root.find(".//ukm:Extent", ns)
        if legacy_extent_el is not None:
            extent_codes = legacy_extent_el.get("Value", "")
    extent = _extent_codes_to_names(extent_codes)

    # In-force / prospective:
    #   1. <Repeal> wrapping the section's <Title> is the strongest signal —
    #      the heading is marked repealed, so the section is no longer in
    #      force. This is how legislation.gov.uk encodes the Housing Act
    #      1988 s.21 repeal by the Renters' Rights Act 2025.
    #   2. Otherwise, look at the section's own <ukm:InForce> element if
    #      present at section level. The same element appears many times
    #      in affecting-provisions metadata, which is NOT this section's
    #      own status — so substring search on the whole document is
    #      unreliable.
    #   3. When neither signal is available, return None (unknown), same
    #      contract the HTML parser honours.
    in_force: bool | None = None
    prospective: bool | None = None
    if _section_is_repealed(section_el, ns):
        in_force = False
        prospective = False
    elif section_el is not None:
        in_force_el = section_el.find(".//ukm:InForce", ns)
        if in_force_el is not None:
            applied = (in_force_el.get("Applied") or "").lower() == "true"
            prospective_raw = (in_force_el.get("Prospective") or "").lower()
            if prospective_raw in ("true", "false"):
                prospective = prospective_raw == "true"
            in_force = applied if applied else (None if prospective is None else not prospective)

    # Version date: prefer RestrictStartDate (the date the current revised
    # state of the section took effect — what a lawyer cites as "valid
    # from"). Fall back to EnactmentDate (the Act's original enactment)
    # only when RestrictStartDate is absent, since for an amended section
    # the enactment date is misleadingly old.
    version_date = None
    rsd = _find_restrict_start_date(section_el, root)
    if rsd:
        try:
            version_date = date.fromisoformat(rsd)
        except ValueError:
            pass
    if version_date is None:
        date_el = root.find(".//ukm:EnactmentDate", ns)
        if date_el is not None:
            try:
                version_date = date.fromisoformat(date_el.get("Date", ""))
            except ValueError:
                pass

    # Section title: prefer the section element's direct <Title> child so
    # we don't grab the Part / Chapter / Act title that appears earlier in
    # the document. Fall back to root-wide search only when the section
    # element wasn't located.
    title = f"Section {section}"
    if section_el is not None:
        title_el = section_el.find("leg:Title", ns)
        if title_el is None:
            title_el = section_el.find(".//leg:Title", ns)
        if title_el is not None:
            # When the title is wrapped in <Repeal>, the text lives inside the
            # Repeal child; itertext() flattens that for us.
            title_text = " ".join(title_el.itertext()).strip()
            if title_text:
                title = title_text
    else:
        title_el = root.find(".//leg:Title", ns)
        if title_el is not None and title_el.text:
            title = title_el.text.strip()

    return LegislationSection(
        title=title,
        section_number=section,
        content=content,
        content_truncated=truncated,
        original_length=original_length,
        in_force=in_force,
        extent=extent,
        version_date=version_date,
        prospective=prospective,
        source_format="xml",
        warnings=[],
    )


def _parse_toc_xml(xml_text: str) -> list[str]:
    """Extract the full table of contents from CLML XML.

    Returns every structural element that has an @id and a <Title>, in
    document order. No slicing — callers apply offset/limit themselves.
    """
    root = parse_xml(xml_text)
    ns = {"leg": "http://www.legislation.gov.uk/namespaces/legislation"}
    items = []
    for el in root.iter():
        id_val = el.get("id")
        if id_val:
            title_el = el.find("leg:Title", ns)
            if title_el is not None and title_el.text:
                items.append(f"{id_val}: {title_el.text.strip()}")
    return items


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search",
        annotations={"title": "Search UK Legislation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_search(
        query: Annotated[str, Field(description="Search query, e.g. 'Housing Act 1988' or 'data protection personal data'", min_length=1, max_length=500)],
        type: Annotated[str | None, Field(description="Filter by type: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts). Exact-match — omit if you don't already know whether you're looking for an Act vs an SI.")] = None,
        year: Annotated[int | None, Field(description="Filter by year of enactment (exact-match — a single integer, not a range). Omit unless you already know the Act's year. Speculating a year (e.g. 'this is recent so it must be 2026') and getting it wrong will zero out the result set. Better workflow: query without `year`, then read the year from the returned results.", ge=1800, le=2100)] = None,
        limit: Annotated[int, Field(description="Maximum results to return (1–50). Passed to the upstream results-count param.", ge=1, le=50)] = 20,
        fulltext: Annotated[bool, Field(description="Default false → searches Act/SI titles only (best for finding a named Act, e.g. 'Housing Act 1988' returns ukpga/1988/50 first). Set true to search the full text of every Act/SI for the query (returns SIs and regulations that cite the term — e.g. 'rental deposits' would return many implementing instruments).")] = False,
        *,
        ctx: Context,
    ) -> LegislationSearchResult:
        """USE THIS TOOL WHEN searching UK Acts and Statutory Instruments by title, phrase, or full-text.

        Returns ranked results: title, type, year, number, legislation.gov.uk URL,
        and next_steps hints (toc URI, section template). AFTER calling, chain
        to legislation_get_toc then legislation_get_section for structural drill-in.

        Filter discipline: `type` and `year` are exact-match. Use only when you
        already know the value. For currency-driven searches ("the recent
        Renters' Rights Act"), query by phrase alone and read the year from the
        results — guessing a year and filtering by it zeroes results when wrong.
        For broader concept queries across content, set `fulltext=True`.

        Authoritative source for UK primary and secondary legislation
        (legislation.gov.uk).
        """
        client = ctx.lifespan_context["legislation_http"]
        path = f"/{type}" if type else "/search"
        # Title search by default — best ranking for "find me Act X". `fulltext`
        # opens up content search across every Act/SI, useful for concept queries.
        qp: dict = {"results-count": limit}
        qp["text" if fulltext else "title"] = query
        if year:
            qp["year"] = year

        resp = await client.get(f"{LEGISLATION_BASE}{path}", params=qp)
        resp.raise_for_status()
        root = parse_xml(resp.content)

        total_el = root.findtext(".//os:totalResults", namespaces=ATOM_NS)
        total = int(total_el) if total_el else 0

        results = []
        for entry in root.findall(".//a:entry", namespaces=ATOM_NS):
            title = _entry_title(entry)
            entry_id = entry.findtext("a:id", namespaces=ATOM_NS) or ""

            m = _ID_RE.search(entry_id)
            if m:
                leg_type, yr, num = m.group(1), int(m.group(2)), int(m.group(3))
            elif m2 := _REGNAL_ID_RE.search(entry_id):
                leg_type, num = m2.group(1), int(m2.group(2))
                yr_m = re.search(r"\b(\d{4})\b", title)
                yr = int(yr_m.group(1)) if yr_m else 0
            else:
                leg_type, yr, num = "unknown", 0, 0

            results.append(LegislationResult(
                title=title, type=leg_type, year=yr, number=num,
                score=None, url=f"{LEGISLATION_BASE}/{leg_type}/{yr}/{num}",
                next_steps=({
                    "toc": f"legislation://{leg_type}/{yr}/{num}/toc",
                    "section_template": f"legislation://{leg_type}/{yr}/{num}/section/{{section}}",
                    "point_in_time_hint": "Append ?date=YYYY-MM-DD to either URI for historical research",
                } if leg_type != "unknown" else {}),
            ))

        return LegislationSearchResult(results=results, total=total or len(results))

    @mcp.tool(
        name="get_section",
        annotations={"title": "Get Legislation Section", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_get_section(
        type: Annotated[str, Field(description="Legislation type code: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts). Use the value from legislation_search results.", min_length=2, max_length=10)],
        year: Annotated[int, Field(description="Year of enactment", ge=1800, le=2100)],
        number: Annotated[int, Field(description="Chapter or SI number", ge=1)],
        section: Annotated[str, Field(description="Section number, e.g. '47' or '12A'. Use the numeric part only — not 'section-47'. Schedules are not currently supported.", min_length=1, max_length=50)],
        max_chars: Annotated[int, Field(description="Maximum characters of section content to return. Default 10,000 (~2,500 tokens) covers almost every section. Raise to 50,000+ only for unusually long Finance Act definition sections. Check content_truncated in the response to see if it was cut.", ge=500, le=200000)] = 10000,
        *,
        ctx: Context,
    ) -> LegislationSection:
        """USE THIS TOOL WHEN you have a known Act / SI and want the parsed text of a specific section, with extent and in-force metadata.

        Returns full section text, territorial extent, in-force status, and
        prospective flag. Content capped per max_chars (default 10,000,
        ~2,500 tokens) — raise for unusually long definition sections; check
        content_truncated in the response.

        ALWAYS check `extent` — a section may apply to England & Wales but not
        Scotland or Northern Ireland. Reciting a section without checking
        extent is a recurring legal-research error.

        Alternative: call read_resource(uri="legislation://{type}/{year}/{number}/
        section/{section}") for raw CLML XML; use this tool when you want the
        parsed structured response instead.
        """
        client = ctx.lifespan_context["legislation_http"]
        section = _normalise_section_id(section)
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}/data.xml"
        try:
            resp = await client.get(url)
            resp.raise_for_status()
            return _parse_clml_section(resp.text, section, max_chars)
        except LegislationUpstreamError as exc:
            html_url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/section/{section}"
            html_resp = await client.get_html(html_url)
            html_resp.raise_for_status()
            return _parse_html_section(html_resp.text, section, max_chars, str(exc))

    @mcp.tool(
        name="get_toc",
        annotations={"title": "Get Legislation Table of Contents", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def legislation_get_toc(
        type: Annotated[str, Field(description="Legislation type code: 'ukpga' (Acts), 'uksi' (SIs), 'asp' (Scottish Acts), 'nia' (NI Acts). Use the value from legislation_search results.", min_length=2, max_length=10)],
        year: Annotated[int, Field(description="Year of enactment", ge=1800, le=2100)],
        number: Annotated[int, Field(description="Chapter or SI number", ge=1)],
        offset: Annotated[int, Field(description="Number of items to skip from the flattened TOC. Use with limit to page through very large statutes like the Companies Act 2006 (1300+ items).", ge=0)] = 0,
        limit: Annotated[int, Field(description="Maximum items to return in this call (default 200, max 1000). Raise only when you need a larger slice in one response. Check has_more and total_items to know if further pages exist.", ge=1, le=1000)] = 200,
        *,
        ctx: Context,
    ) -> LegislationTOC:
        """USE THIS TOOL WHEN you have a known Act / SI and want the structural table of contents (parts, chapters, sections, schedules).

        Returns structural elements with XML id and title, e.g. 'section-47:
        Definitions'. AFTER calling, pass the numeric section identifier (use
        '47', NOT 'section-47') into legislation_get_section for full text.

        Large statutes (Companies Act 2006 has many hundreds of items) are
        paginated via offset/limit. Check has_more and total_items.

        Alternative: call read_resource(uri="legislation://{type}/{year}/{number}/
        toc") for the full TOC as a newline-separated `id: title` string (no
        pagination). Use this tool when you need the structured response with
        offset / limit / has_more for stepping through large statutes.
        """
        client = ctx.lifespan_context["legislation_http"]
        url = f"{LEGISLATION_BASE}/{type}/{year}/{number}/data.xml"
        resp = await client.get(url)
        resp.raise_for_status()

        all_items = _parse_toc_xml(resp.text)
        total_items = len(all_items)
        page = all_items[offset : offset + limit]

        return LegislationTOC(
            type=type,
            year=year,
            number=number,
            offset=offset,
            limit=limit,
            returned=len(page),
            total_items=total_items,
            has_more=(offset + len(page)) < total_items,
            items=page,
        )
