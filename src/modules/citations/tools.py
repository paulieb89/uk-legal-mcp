"""
Tools for the citations module — OSCOLA citation parser and resolver.

Fully self-contained: no external API. Pure Python regex + optional LLM sampling.
This is the primary differentiator of uk-legal-mcp.
"""

import json
import re
import time

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import CitationNetwork, CitationParseResult, CitationType, ParsedCitation
from .patterns import (
    _compile_patterns,
    AMBIGUOUS_COURTS,
    court_is_ambiguous,
    resolve_neutral_citation,
    resolve_si,
    resolve_legislation,
    TNA_BASE,
)


class CitationsParseInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    text: str = Field(
        ...,
        description=(
            "Free text containing OSCOLA citations to extract. Supported: "
            "neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR 100), "
            "legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234), "
            "retained EU law (Regulation (EU) 2016/679). Max 50,000 chars."
        ),
        min_length=1,
        max_length=50_000,
    )
    disambiguate: bool = Field(
        True,
        description="If True, ambiguous citations (e.g. bare EWHC without division) are sent to the LLM for disambiguation via sampling.",
    )


class CitationsResolveInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    citation: str = Field(
        ...,
        description="A single OSCOLA citation to parse and resolve. E.g. '[2024] UKSC 12', 'SI 2018/1234', 's.47 Companies Act 2006'",
        min_length=3,
        max_length=500,
    )


class CitationsNetworkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    case_uri: str = Field(
        ...,
        description=(
            "TNA judgment URI slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'. "
            "Use the 'uri' field from case_law_search results — not the full URL. "
            "Do not include the 'https://caselaw.nationalarchives.gov.uk/' prefix."
        ),
        min_length=5,
    )


# ---------------------------------------------------------------------------
# Core parsing engine
# ---------------------------------------------------------------------------

def _parse_citation_from_match(
    match: re.Match,
    ctype: CitationType,
) -> ParsedCitation:
    """Convert a regex match into a ParsedCitation with appropriate fields filled."""
    raw = match.group(0)
    confidence = 1.0

    if ctype == CitationType.NEUTRAL:
        year = int(match.group(1))
        court_raw = re.sub(r"\s+", " ", match.group(2).strip()).upper()
        number = int(match.group(3))
        if court_is_ambiguous(court_raw):
            confidence = 0.5
        resolved = resolve_neutral_citation(year, court_raw, number)
        return ParsedCitation(raw=raw, type=ctype, year=year, court=court_raw, number=number, resolved_url=resolved, confidence=confidence)

    if ctype == CitationType.LAW_REPORT:
        year = int(match.group(1))
        volume = int(match.group(2)) if match.group(2) else None
        series = re.sub(r"\s+", " ", match.group(3).strip())
        page = int(match.group(4))
        return ParsedCitation(raw=raw, type=ctype, year=year, report_series=series, volume=volume, page=page, confidence=0.9)

    if ctype == CitationType.LEGISLATION:
        section = match.group(1)
        title = match.group(2).strip()
        year_m = re.search(r"\d{4}$", title)
        year = int(year_m.group()) if year_m else None
        resolved = resolve_legislation(title, section)
        return ParsedCitation(raw=raw, type=ctype, year=year, legislation_title=title, section=section, resolved_url=resolved, confidence=0.95)

    if ctype == CitationType.SI:
        si_year = int(match.group(1))
        si_number = int(match.group(2))
        return ParsedCitation(raw=raw, type=ctype, year=si_year, si_year=si_year, si_number=si_number, resolved_url=resolve_si(si_year, si_number), confidence=1.0)

    if ctype == CitationType.EU_RETAINED:
        year = int(match.group(1))
        number = int(match.group(2))
        return ParsedCitation(raw=raw, type=ctype, year=year, number=number, confidence=0.9)

    return ParsedCitation(raw=raw, type=ctype, confidence=0.5)


def _extract_all_citations(text: str, patterns: dict) -> tuple[list[ParsedCitation], list[ParsedCitation]]:
    """Run all compiled patterns against text. Returns (confident, ambiguous) lists."""
    seen_spans: set[tuple[int, int]] = set()
    confident: list[ParsedCitation] = []
    ambiguous: list[ParsedCitation] = []

    priority = [CitationType.NEUTRAL, CitationType.LEGISLATION, CitationType.SI, CitationType.EU_RETAINED, CitationType.LAW_REPORT]

    for ctype in priority:
        pattern = patterns.get(ctype)
        if not pattern:
            continue
        for match in pattern.finditer(text):
            span = match.span()
            if any(s <= span[0] < e or s < span[1] <= e for (s, e) in seen_spans):
                continue
            seen_spans.add(span)
            parsed = _parse_citation_from_match(match, ctype)
            (confident if parsed.confidence >= 0.7 else ambiguous).append(parsed)

    return confident, ambiguous


async def _disambiguate_citation(ctx: Context, citation: ParsedCitation) -> ParsedCitation:
    """Use LLM sampling to resolve ambiguous bare court codes (e.g. bare EWHC → EWHC (KB))."""
    if citation.court not in AMBIGUOUS_COURTS:
        return citation
    prompt = (
        f"The neutral citation '{citation.raw}' uses '{citation.court}' without a division qualifier.\n"
        f"Respond with ONLY one abbreviation from: KB, Ch, Comm, Fam, Pat, IPEC, Admin, TCC, Costs, unknown\n"
        f"No explanation, no other text."
    )
    try:
        result = await ctx.sample(prompt, result_type=str)
        division = result.text.strip().upper()
        if division not in {"KB", "CH", "COMM", "FAM", "PAT", "IPEC", "ADMIN", "TCC", "COSTS", "UNKNOWN"}:
            return citation
        if division == "UNKNOWN":
            return citation
        new_court = f"{citation.court} ({division.title()})"
        new_url = resolve_neutral_citation(citation.year, new_court, citation.number) if citation.year and citation.number else None
        return citation.model_copy(update={"court": new_court, "resolved_url": new_url, "confidence": 0.75})
    except Exception:
        return citation


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="parse",
        annotations={"title": "Parse OSCOLA Citations", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def citations_parse(params: CitationsParseInput, ctx: Context) -> CitationParseResult:
        """Extract and classify all OSCOLA legal citations from free text.

        Identifies: neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR 100),
        legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234),
        and retained EU law (Regulation (EU) 2016/679).

        Ambiguous citations (e.g. bare [2024] EWHC without division) are optionally
        disambiguated via LLM sampling. Resolves citations to TNA / legislation.gov.uk URLs.

        Args:
            params: CitationsParseInput with text (max 50,000 chars) and disambiguate flag.
        """
        t0 = time.monotonic()
        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations(params.text, patterns)

        if params.disambiguate and ambiguous:
            still_ambiguous = []
            for c in ambiguous:
                result = await _disambiguate_citation(ctx, c)
                (confident if result.confidence >= 0.7 else still_ambiguous).append(result)
            ambiguous = still_ambiguous

        duration_ms = int((time.monotonic() - t0) * 1000)
        return CitationParseResult(
            citations=confident,
            ambiguous=ambiguous,
            text_length=len(params.text),
            parse_duration_ms=duration_ms,
        )

    @mcp.tool(
        name="resolve",
        annotations={"title": "Resolve Single OSCOLA Citation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def citations_resolve(params: CitationsResolveInput) -> ParsedCitation:
        """Parse and resolve a single OSCOLA citation to its canonical URL.

        Supports: neutral citations, SIs, legislation sections, retained EU law.
        Returns parsed fields and resolved_url if resolvable. Raises ValueError
        if no recognised citation is found in the input.

        Args:
            params: CitationsResolveInput with a single citation string.
        """
        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations(params.citation.strip(), patterns)
        all_found = confident + ambiguous
        if not all_found:
            raise ValueError(
                f"No recognised OSCOLA citation found in '{params.citation}'. "
                f"Supported: [YYYY] COURT N, [YYYY] N SERIES PAGE, s.N Act YYYY, SI YYYY/N, Regulation (EU) YYYY/N"
            )
        return all_found[0]

    @mcp.tool(
        name="network",
        annotations={"title": "Get Case Citation Network", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def citations_network(params: CitationsNetworkInput, ctx: Context) -> CitationNetwork:
        """Map all citations within a judgment — cases cited, legislation referenced, SIs, EU law.

        Fetches the judgment XML from TNA and parses all OSCOLA citations within it.
        Returns citations grouped by type for easy analysis. Each bucket is
        de-duplicated and sorted.

        Args:
            params: CitationsNetworkInput with case_uri (TNA slug, e.g. 'uksc/2024/12').
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        uri = params.case_uri.lstrip("/")
        resp = await client.get(f"{TNA_BASE}/{uri}/data.xml")
        resp.raise_for_status()

        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations(resp.text, patterns)
        all_citations = confident + ambiguous

        buckets: dict[str, list[str]] = {
            "neutral_citations": [], "legislation_refs": [], "si_refs": [], "eu_refs": [], "law_report_refs": [],
        }
        type_map = {
            CitationType.NEUTRAL: "neutral_citations", CitationType.LEGISLATION: "legislation_refs",
            CitationType.SI: "si_refs", CitationType.EU_RETAINED: "eu_refs", CitationType.LAW_REPORT: "law_report_refs",
        }
        for c in all_citations:
            key = type_map.get(c.type)
            if key:
                buckets[key].append(c.raw)
        for key in buckets:
            buckets[key] = sorted(set(buckets[key]))

        return CitationNetwork(
            case_uri=uri,
            **buckets,
            total_citations=sum(len(v) for v in buckets.values()),
        )
