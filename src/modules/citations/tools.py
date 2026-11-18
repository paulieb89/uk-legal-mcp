"""
Tools for the citations module — OSCOLA citation parser, resolver and formatter.

Parsing is pure regex (+ optional LLM sampling). citations_resolve and
citations_format_oscola check that the cited document exists, through
deps.check_document_exists, against TNA (neutral citations) and
legislation.gov.uk (SIs). Existence is reported as ParsedCitation.verified.
"""

import re
import time
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

from ...deps import (
    LEGISLATION_ABSENT_STATUSES,
    TNA_ABSENT_STATUSES,
    check_document_exists,
    raise_http_tool_error,
    raise_tool_error,
)

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
    "EWHC (QB)": "EWHC (QB)", "EWHC (ADMLTY)": "EWHC (Admlty)",
    "EWHC (BP)": "EWHC (BP)",
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
        # OSCOLA (and TNA's own NCN guidance) place the division qualifier AFTER
        # the judgment number: "[2026] EWHC 2355 (Comm)", never
        # "[2026] EWHC (Comm) 2355". _COURT_DISPLAY stores the fused form, so
        # split it and re-attach the division at the end.
        base, sep, division = display.partition(" (")
        if sep:
            return f"[{year}] {base} {number} ({division.rstrip(')')})"
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
        trailing_division = match.group(4)
        if trailing_division and court_raw in AMBIGUOUS_COURTS:
            # .upper(), not .title(): _COURT_DISPLAY / _TNA_COURT_SLUGS keys are
            # uppercase and deliberately preserve acronyms (KB, TCC, GRC, IPEC).
            # .title() produced "EWHC (Kb)", which missed every lookup key and
            # silently fell through to the raw value.
            court_raw = f"{court_raw} ({trailing_division.upper()})"
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
        # A title search is not a link to the Act (audit finding L2), so it goes
        # in search_url and resolved_url stays null.
        search = resolve_legislation(title, section)
        return ParsedCitation(raw=raw, type=ctype, year=year, legislation_title=title, section=section, search_url=search, confidence=0.95)

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
        f"Respond with ONLY one abbreviation from: KB, QB, Ch, Comm, Fam, Pat, IPEC, Admin, TCC, Costs, Admlty, BP, unknown\n"
        f"No explanation, no other text."
    )
    try:
        result = await ctx.sample(prompt, result_type=str)
        division = result.text.strip().upper()
        if division not in {"KB", "QB", "CH", "COMM", "FAM", "PAT", "IPEC", "ADMIN", "TCC", "COSTS", "ADMLTY", "BP", "UNKNOWN"}:
            return citation
        if division == "UNKNOWN":
            return citation
        # Tables are keyed by uppercase division; .title() would produce "Kb"/"Bp".
        new_court = f"{citation.court} ({division.upper()})"
        new_url = resolve_neutral_citation(citation.year, new_court, citation.number) if citation.year and citation.number else None
        return citation.model_copy(update={"court": new_court, "resolved_url": new_url, "confidence": 0.75})
    except Exception:
        return citation


# ---------------------------------------------------------------------------
# Existence check
# ---------------------------------------------------------------------------

async def _verify_existence(ctx: Context, parsed: ParsedCitation, *, tool: str) -> ParsedCitation:
    """Check the cited document exists and record the answer on the citation.

    Neutral citations are checked against TNA and SIs against legislation.gov.uk.
    Present → verified=True. Confirmed absent → verified=False, confidence=0.0.
    A check that could not run raises a transient ToolError (never "absent").
    Other types are returned unchanged with verified=False: this server has no
    cheap authoritative check for them.
    """
    if not parsed.resolved_url:
        return parsed
    if parsed.type == CitationType.NEUTRAL:
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        head, absent, source = client.head, TNA_ABSENT_STATUSES, "TNA Find Case Law"
    elif parsed.type == CitationType.SI:
        head = ctx.lifespan_context["legislation_http"].head
        absent, source = LEGISLATION_ABSENT_STATUSES, "legislation.gov.uk"
    else:
        return parsed

    exists = await check_document_exists(
        head,
        parsed.resolved_url,
        attempted=f"{tool}({parsed.raw!r}): {source} existence check",
        absent_statuses=absent,
    )
    if exists:
        return parsed.model_copy(update={"verified": True})
    return parsed.model_copy(update={"verified": False, "confidence": 0.0})


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
        Canonical TNA / legislation.gov.uk URLs are constructed where possible,
        but nothing is checked: every result has verified=false.

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
        """USE THIS TOOL BEFORE constructing an OSCOLA citation string from known fields, OR to confirm a citation points at a real document.

        Returns parsed fields plus resolved_url. Neutral citations are checked
        live against TNA, SIs against legislation.gov.uk: verified=true means
        the document exists; if the source confirms it does not, verified=false
        and confidence=0.0 — do NOT format or quote that citation. Act
        sections, law reports and retained EU law cannot be checked and come
        back verified=false (Acts with a search_url, not a resolved_url):
        present them as unverified.

        A transient error means the check could not run (timeout, 429, 5xx) —
        NOT that the document is absent; retry, or proceed without claiming
        verification.

        AFTER calling, if this tool raises or returns no resolved_url, do NOT
        manufacture a citation — surface the failure and ask the user for the
        source URL.

        Authoritative source for UK legal-citation resolution.
        """
        patterns = _compile_patterns()
        confident, ambiguous = _extract_all_citations(citation.strip(), patterns)
        all_found = confident + ambiguous
        if not all_found:
            raise_tool_error(
                "validation",
                is_retryable=False,
                attempted=f"citations_resolve({citation!r})",
                description=(
                    f"No recognised OSCOLA citation found in {citation!r}. Supported: "
                    "[YYYY] COURT N, [YYYY] N SERIES PAGE, s.N Act YYYY, SI YYYY/N, "
                    "Regulation (EU) YYYY/N"
                ),
            )
        return await _verify_existence(ctx, all_found[0], tool="citations_resolve")

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
        try:
            resp = await client.get(f"{TNA_BASE}/{uri}/data.xml")
            resp.raise_for_status()
        except Exception as exc:
            raise_http_tool_error(exc, attempted=f"citations_network({case_uri!r})")

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
        annotations={"title": "Format OSCOLA Citation String", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def citations_format_oscola(
        citation_type: Annotated[
            Literal["neutral", "law_report", "legislation", "si", "eu_retained"],
            Field(description="'type' field from citations_resolve result."),
        ],
        ctx: Context,
        confidence: Annotated[
            float | None,
            Field(
                description=(
                    "Ignored; accepted for compatibility with older callers. This tool "
                    "verifies the citation against the source itself and does not "
                    "trust a caller-reported confidence."
                ),
                ge=0.0,
                le=1.0,
            ),
        ] = None,
        resolved_url: Annotated[
            str | None,
            Field(description="Ignored; accepted for compatibility. The URL is rebuilt from the citation fields and returned."),
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

        Pass the fields citations_resolve returned. Formats per OSCOLA 4th
        edition. This tool re-checks existence itself — neutral citations
        against TNA, SIs against legislation.gov.uk — and does not take the
        caller's word. Raises not_found if the source confirms the document
        does not exist, validation for incomplete fields or an ambiguous court
        (e.g. bare EWHC), transient if the check could not run (NOT evidence
        of absence).

        Returns the OSCOLA string with verified=true only when the document was
        confirmed; Act sections, law reports and retained EU law are formatted
        with verified=false — present them as unverified.

        AFTER a refusal, do NOT manufacture a citation; ask the user for the
        source URL or better identifying details. Never construct the input
        fields yourself — guessed fields are the main fabrication route.

        Authoritative OSCOLA formatting for UK legal citations.
        """
        attempted = f"citations_format_oscola(citation_type={citation_type!r})"

        def invalid(detail: str) -> None:
            raise_tool_error("validation", is_retryable=False, attempted=attempted, description=detail)

        if citation_type == "neutral" and not all([year, court, number]):
            invalid("Neutral citation requires year, court, and number.")
        if citation_type == "law_report" and not all([year, report_series, page]):
            invalid("Law report citation requires year, report_series, and page.")
        if citation_type == "legislation" and not all([section, legislation_title]):
            invalid("Legislation citation requires section and legislation_title.")
        if citation_type == "si" and not all([si_year, si_number]):
            invalid("SI citation requires si_year and si_number.")
        if citation_type == "eu_retained" and not raw:
            invalid("EU retained law citation requires the raw field.")

        oscola = _build_oscola(
            citation_type, year, court, number,
            report_series, volume, page,
            legislation_title, section,
            si_year, si_number, raw,
        )

        canonical_url: str | None = None
        if citation_type == "neutral":
            court_key = re.sub(r"\s+", " ", court or "").strip().upper()
            canonical_url = resolve_neutral_citation(year, court_key, number)
            if canonical_url is None:
                invalid(
                    f"Cannot format {oscola!r}: the court code {court!r} is ambiguous "
                    "or unsupported (e.g. bare EWHC without a division), so it cannot "
                    "be checked. Call citations_resolve, or ask the user for the full "
                    "citation including the division."
                )
        elif citation_type == "si":
            canonical_url = resolve_si(si_year, si_number)

        check = ParsedCitation(
            raw=oscola,
            type=CitationType(citation_type),
            resolved_url=canonical_url,
            confidence=1.0,
        )
        checked = await _verify_existence(ctx, check, tool="citations_format_oscola")
        if canonical_url and not checked.verified:
            raise_tool_error(
                "not_found",
                is_retryable=False,
                attempted=attempted,
                description=(
                    f"Refusing to format {oscola!r}: the source confirmed no document "
                    f"exists at {canonical_url}. Do not manufacture a citation. Ask the "
                    "user for the source URL or better identifying details."
                ),
            )

        return {
            "status": "ok",
            "oscola": oscola,
            "citation_type": citation_type,
            "resolved_url": canonical_url,
            "verified": checked.verified,
        }
