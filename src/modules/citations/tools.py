"""
Tools for the citations module — OSCOLA citation parser and resolver.

Fully self-contained: no external API. Pure Python regex + optional LLM sampling.
This is the primary differentiator of uk-legal-mcp.
"""

import asyncio
import json
import re
import time
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import Field

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


# ---------------------------------------------------------------------------
# OSCOLA formatting helpers
# ---------------------------------------------------------------------------

_COURT_DISPLAY: dict[str, str] = {
    "UKSC": "UKSC", "UKPC": "UKPC",
    "EWCA CIV": "EWCA Civ", "EWCA CRIM": "EWCA Crim",
    "EWHC": "EWHC",
    "EWHC (KB)": "EWHC (KB)", "EWHC (CH)": "EWHC (Ch)",
    "EWHC (COMM)": "EWHC (Comm)", "EWHC (FAM)": "EWHC (Fam)",
    "EWHC (PAT)": "EWHC (Pat)", "EWHC (IPEC)": "EWHC (IPEC)",
    "EWHC (ADMIN)": "EWHC (Admin)", "EWHC (TCC)": "EWHC (TCC)",
    "EWHC (COSTS)": "EWHC (Costs)",
    "UKUT": "UKUT",
    "UKUT (IAC)": "UKUT (IAC)", "UKUT (TCC)": "UKUT (TCC)",
    "UKUT (AAC)": "UKUT (AAC)", "UKUT (LC)": "UKUT (LC)",
    "EAT": "EAT",
    "UKFTT (TC)": "UKFTT (TC)", "UKFTT (GRC)": "UKFTT (GRC)",
}


def _build_oscola(
    citation_type: str,
    year: int | None,
    court: str | None,
    number: int | None,
    report_series: str | None,
    volume: int | None,
    page: int | None,
    legislation_title: str | None,
    section: str | None,
    si_year: int | None,
    si_number: int | None,
    raw: str | None,
) -> str:
    if citation_type == "neutral":
        display = _COURT_DISPLAY.get(court or "", court or "")
        return f"[{year}] {display} {number}"
    if citation_type == "law_report":
        if volume:
            return f"[{year}] {volume} {report_series} {page}"
        return f"[{year}] {report_series} {page}"
    if citation_type == "legislation":
        return f"s.{section} {legislation_title}"
    if citation_type == "si":
        return f"SI {si_year}/{si_number}"
    # eu_retained and unknown — raw text is the authoritative form
    return raw or ""


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
# TNA existence check helper
# ---------------------------------------------------------------------------

async def _tna_head_check(client: httpx.AsyncClient, url: str) -> float | None:
    """Verify a neutral citation against TNA Find Case Law.

    Returns 0.0 if TNA was reached and confirmed the document absent (non-200).
    Returns None if TNA confirmed it present (200) — caller leaves confidence unchanged.
    Raises ToolError with error_category/is_retryable/message on transport failure
    after one retry (~6.5s worst case with 3s per-request timeout).
    Non-transport exceptions propagate fail-loud.
    """
    last_exc: httpx.TransportError | None = None
    for attempt in range(2):
        try:
            resp = await client.head(url, timeout=3.0)
            return 0.0 if resp.status_code != 200 else None
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt == 0:
                await asyncio.sleep(0.5)
    assert last_exc is not None
    raise ToolError(json.dumps({
        "error_category": "transient",
        "is_retryable": True,
        "message": format_http_error(last_exc),
    })) from last_exc


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="parse",
        annotations={"title": "Parse OSCOLA Citations", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    )
    async def citations_parse(
        text: Annotated[str, Field(
            description=(
                "Free text containing OSCOLA citations to extract. Supported: "
                "neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR 100), "
                "legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234), "
                "retained EU law (Regulation (EU) 2016/679). Max 50,000 chars."
            ),
            min_length=1,
            max_length=50_000,
        )],
        ctx: Context,
        disambiguate: Annotated[bool, Field(
            description=(
                "Default False — pure-regex parsing, no model in the loop. If True, "
                "ambiguous citations (e.g. bare EWHC without a division) are sent to the "
                "connected client's own LLM, via MCP sampling, to resolve the division. "
                "Opt in only when you want best-effort division resolution and accept "
                "that a model shapes the result."
            ),
        )] = False,
    ) -> CitationParseResult:
        """USE THIS TOOL WHEN you have free text (a memo, an email, a clause) and want every OSCOLA-style citation it contains extracted and classified.

        Identifies: neutral citations ([2024] UKSC 12), law reports ([2024] 1 WLR
        100), legislation sections (s.47 Companies Act 2006), SIs (SI 2018/1234),
        retained EU law (Regulation (EU) 2016/679).

        Parsing is pure regex by default. Ambiguous citations (e.g. bare [2024]
        EWHC without division) can OPTIONALLY be disambiguated by setting
        disambiguate=True, which asks the CONNECTED CLIENT's own model (not this
        server) to resolve the division via MCP sampling — off by default.
        Citations resolve to TNA / legislation.gov.uk URLs when possible.

        AFTER calling, pass each citation through citations_resolve to verify it
        points at a real document before quoting or formatting it — the parser
        recognises the SHAPE of a citation but does not confirm the document
        exists.
        """
        t0 = time.monotonic()
        patterns = _compile_patterns()
        confident, ambiguous_list = _extract_all_citations(text, patterns)

        if disambiguate and ambiguous_list:
            still_ambiguous = []
            for c in ambiguous_list:
                result = await _disambiguate_citation(ctx, c)
                (confident if result.confidence >= 0.7 else still_ambiguous).append(result)
            ambiguous_list = still_ambiguous

        duration_ms = int((time.monotonic() - t0) * 1000)
        return CitationParseResult(
            citations=confident,
            ambiguous=ambiguous_list,
            text_length=len(text),
            parse_duration_ms=duration_ms,
        )

    @mcp.tool(
        name="resolve",
        annotations={"title": "Resolve Single OSCOLA Citation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def citations_resolve(
        citation: Annotated[str, Field(
            description="A single OSCOLA citation to parse and resolve. E.g. '[2024] UKSC 12', 'SI 2018/1234', 's.47 Companies Act 2006'",
            min_length=3,
            max_length=500,
        )],
        ctx: Context,
    ) -> ParsedCitation:
        """USE THIS TOOL BEFORE constructing an OSCOLA citation string from known fields, OR when you have a citation and want to confirm it points at a real document.

        Parses + resolves a single citation (neutral citation, SI, legislation
        section, retained EU law) and returns the parsed fields plus a
        resolved_url. Raises ValueError if nothing recognisable is found.

        For neutral citations, performs a live HTTP HEAD check against TNA Find
        Case Law to confirm the judgment exists. If TNA returns non-200,
        confidence is set to 0.0 — the citation parsed successfully but the
        document does not exist at the constructed URL. DO NOT format or quote
        a citation with confidence 0.0 as verified; surface the failure and ask
        the user for the source URL or better identifying details.

        If the TNA HEAD check fails due to a network error (timeout, connection
        failure, protocol error), raises ToolError with JSON content
        {"error_category": "transient", "is_retryable": true, "message": "..."}.
        One retry is attempted before raising. The citation parsed successfully —
        retry this call or proceed without TNA verification.

        Formatting a citation from "known" fields (year, court, number) without
        prior resolution is the most common citation-fabrication route — the
        formatter accepts whatever you give it and produces plausible-looking
        output for invented inputs. If this tool raises or returns no
        resolved_url, do NOT manufacture a citation — surface the failure and
        ask the user for the source URL or better identifying details.

        Authoritative source for UK legal-citation resolution.
        """
        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations(citation.strip(), patterns)
        all_found = confident + ambiguous
        if not all_found:
            raise ValueError(
                f"No recognised OSCOLA citation found in '{citation}'. "
                f"Supported: [YYYY] COURT N, [YYYY] N SERIES PAGE, s.N Act YYYY, SI YYYY/N, Regulation (EU) YYYY/N"
            )
        parsed = all_found[0]

        # Live existence check for neutral citations — a URL being constructable
        # is not the same as the judgment existing at that URL.
        if parsed.resolved_url and parsed.type == CitationType.NEUTRAL:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            new_confidence = await _tna_head_check(client, parsed.resolved_url)
            if new_confidence is not None:
                parsed = parsed.model_copy(update={"confidence": new_confidence})

        return parsed

    @mcp.tool(
        name="network",
        annotations={"title": "Get Case Citation Network", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def citations_network(
        case_uri: Annotated[str, Field(
            description=(
                "TNA judgment URI slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'. "
                "Use the 'uri' field from case_law_search results — not the full URL. "
                "Do not include the 'https://caselaw.nationalarchives.gov.uk/' prefix."
            ),
            min_length=5,
        )],
        ctx: Context,
    ) -> CitationNetwork:
        """USE THIS TOOL WHEN you have a judgment slug and want to map every citation it makes — cases cited, legislation referenced, SIs, retained EU law.

        Fetches the judgment XML from TNA and parses all OSCOLA citations
        within. Returns citations grouped by type, deduplicated and sorted.
        AFTER calling, pass any individual citation through citations_resolve
        to confirm it resolves and to retrieve its canonical URL.

        Useful for authority-network analysis (what did this judgment rely on?)
        and for surfacing the legislative landscape a case sits inside.
        """
        # xml_http has the right Accept headers (atom+xml, application/xml)
        # for data.xml endpoints. The JSON `http` client was previously used
        # here and caused content-negotiation issues on some URLs.
        client: httpx.AsyncClient = ctx.lifespan_context["xml_http"]
        uri = case_uri.lstrip("/")
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

    @mcp.tool(
        name="format_oscola",
        annotations={"title": "Format OSCOLA Citation String", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def citations_format_oscola(
        citation_type: Annotated[
            Literal["neutral", "law_report", "legislation", "si", "eu_retained"],
            Field(description="'type' field from citations_resolve result."),
        ],
        confidence: Annotated[
            float,
            Field(
                description=(
                    "'confidence' from citations_resolve. Refuses to format if 0.0 — "
                    "that means TNA confirmed the document does not exist. Pass only "
                    "the value citations_resolve returned; do not guess."
                ),
                ge=0.0,
                le=1.0,
            ),
        ],
        resolved_url: Annotated[
            str | None,
            Field(description="'resolved_url' from citations_resolve. Must be non-null for neutral citations."),
        ] = None,
        year: Annotated[int | None, Field(description="'year' from citations_resolve.")] = None,
        court: Annotated[str | None, Field(description="'court' from citations_resolve, e.g. 'UKSC', 'EWCA CIV', 'EWHC (KB)'.")] = None,
        number: Annotated[int | None, Field(description="'number' from citations_resolve (judgment number within the year).")] = None,
        report_series: Annotated[str | None, Field(description="'report_series' from citations_resolve, e.g. 'WLR', 'AC', 'QB'.")] = None,
        volume: Annotated[int | None, Field(description="'volume' from citations_resolve (law report volume, if any).")] = None,
        page: Annotated[int | None, Field(description="'page' from citations_resolve (starting page in the law report).")] = None,
        legislation_title: Annotated[str | None, Field(description="'legislation_title' from citations_resolve, e.g. 'Companies Act 2006'.")] = None,
        section: Annotated[str | None, Field(description="'section' from citations_resolve, e.g. '47', '12', '20A'.")] = None,
        si_year: Annotated[int | None, Field(description="'si_year' from citations_resolve.")] = None,
        si_number: Annotated[int | None, Field(description="'si_number' from citations_resolve.")] = None,
        raw: Annotated[str | None, Field(description="'raw' from citations_resolve. Used as-is for EU retained law — the original text preserves the Regulation/Directive distinction.")] = None,
    ) -> dict:
        """USE THIS TOOL AFTER citations_resolve to produce the correctly formatted OSCOLA citation string.

        Pass the parsed fields returned by citations_resolve directly into this
        tool. Formats per OSCOLA 4th edition rules for each citation type.

        Refuses (status: upstream_validation) if confidence is 0.0 — TNA confirmed
        the document does not exist — or if a neutral citation has no resolved_url
        (ambiguous court code, e.g. bare EWHC without a division). In either case,
        do NOT manufacture a citation; surface the failure and ask the user for
        the source URL or better identifying details.

        DO NOT construct the input fields yourself. The structured input must come
        from citations_resolve — guessing fields is the primary citation-fabrication
        route and this tool is the guard against it.

        Authoritative OSCOLA formatting for UK legal citations (no network call).
        """
        if confidence == 0.0:
            return {
                "status": "upstream_validation",
                "detail": (
                    "Cannot format: citations_resolve returned confidence 0.0 — "
                    "TNA confirmed this judgment does not exist at the resolved URL. "
                    "Do not manufacture a citation. Ask the user for the source URL "
                    "or better identifying details."
                ),
                "is_retryable": False,
            }

        if citation_type == "neutral" and resolved_url is None:
            return {
                "status": "upstream_validation",
                "detail": (
                    "Cannot format: neutral citation has no resolved_url — the court "
                    "code is ambiguous or unsupported (e.g. bare EWHC without a "
                    "division). Call citations_resolve with disambiguate=True or ask "
                    "the user for the full citation including the division."
                ),
                "is_retryable": False,
            }

        try:
            if citation_type == "neutral" and not all([year, court, number]):
                raise ValueError("Neutral citation requires year, court, and number.")
            if citation_type == "law_report" and not all([year, report_series, page]):
                raise ValueError("Law report citation requires year, report_series, and page.")
            if citation_type == "legislation" and not all([section, legislation_title]):
                raise ValueError("Legislation citation requires section and legislation_title.")
            if citation_type == "si" and not all([si_year, si_number]):
                raise ValueError("SI citation requires si_year and si_number.")
            if citation_type == "eu_retained" and not raw:
                raise ValueError("EU retained law citation requires the raw field.")

            oscola = _build_oscola(
                citation_type, year, court, number,
                report_series, volume, page,
                legislation_title, section,
                si_year, si_number, raw,
            )
        except ValueError as exc:
            return {
                "status": "upstream_validation",
                "detail": str(exc),
                "is_retryable": False,
            }

        return {
            "status": "ok",
            "oscola": oscola,
            "citation_type": citation_type,
            "resolved_url": resolved_url,
        }
