"""
Tools for the hmrc module.

Upstream: HMRC APIs + GOV.UK search API
Wire format: JSON
"""

import json
import os
import re
from dataclasses import dataclass
from datetime import date
from typing import Annotated

import httpx
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from ...deps import format_http_error, raise_tool_error
from .models import HMRCGuidanceResult, HMRCGuidanceSearchResult, MTDStatus, VATRate

HMRC_API_BASE = os.getenv("HMRC_API_BASE", "https://test-api.service.hmrc.gov.uk")
GOVUK_SEARCH_BASE = "https://www.gov.uk/api/search.json"

# ---------------------------------------------------------------------------
# VAT rate static lookup table
#
# Matching is whole-word-phrase containment, not character-substring
# containment: a table category matches only when every one of its words
# appears as a contiguous run of whole words in the (normalised) query. This
# is deliberate — a naive `key in query or query in key` character check
# (the previous implementation) let a short, less-specific key match inside
# a longer, more-specific one regardless of which was "correct": querying
# "hot food" matched the "food" entry first (wrong: hot food is a standard-
# rated exception to the general food zero rate) purely because "food" is a
# substring of "hot food" and happened to iterate first. The same mechanism
# silently mismatched "ebooks" against "books" too (right rate by luck, wrong
# notes/URL). See docs/internal or the source-fidelity audit for detail.
#
# When more than one category matches, the most specific (longest) one wins,
# by word count then character length — never by dict insertion order. A
# genuine tie (two equally-specific categories both matching) is surfaced as
# an unmatched/ambiguous result rather than silently resolved by order.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _VATEntry:
    rate: str
    percentage: float
    notes: str
    verified_on: date
    source_url: str
    # Legal commencement date of the rate itself — deliberately separate from
    # `verified_on` (when the table entry was last checked). Only set where
    # this remediation actually gathered dated evidence for it; None
    # otherwise. Absence is not itself informative — most entries below have
    # simply never had their commencement date individually researched.
    effective_from: date | None = None


_GENERAL_RATES_URL = "https://www.gov.uk/guidance/vat-rates-on-different-goods-and-services"
_FOOD_NOTICE_URL = "https://www.gov.uk/guidance/food-products-and-vat-notice-70114"
_ESM_NOTICE_URL = "https://www.gov.uk/guidance/vat-on-energy-saving-materials-and-heating-equipment-notice-7086"

# Most entries below have not been individually re-checked against GOV.UK in
# this pass — `verified_on` for those is the date the table was last reviewed
# wholesale (Autumn Statement 2023). It is a data-currency marker, not a
# claim that the underlying VAT treatment last changed on that date.
_LEGACY_REVIEW_DATE = date(2023, 11, 22)  # Autumn Statement 2023

# Entries individually re-verified against live GOV.UK guidance for the
# source-fidelity audit remediation (see PR description for the fetches).
_CURRENT_REVIEW_DATE = date(2026, 9, 12)

_FOOD = _VATEntry(
    "zero", 0.0,
    "Most food of a kind used for human consumption is zero-rated. Exceptions "
    "(always standard-rated): food supplied in the course of catering — "
    "including hot take-away food heated for the purpose of being consumed hot "
    "— plus most confectionery, ice cream and similar items. See VAT Notice 701/14.",
    _CURRENT_REVIEW_DATE, _FOOD_NOTICE_URL,
)
_HOT_FOOD = _VATEntry(
    "standard", 20.0,
    "Food supplied in the course of catering, including hot take-away food "
    "heated for the purpose of being consumed hot, is always standard-rated — "
    "this is an explicit exception to the general food zero rate, not a "
    "separate category. See VAT Notice 701/14.",
    _CURRENT_REVIEW_DATE, _FOOD_NOTICE_URL,
)
_ENERGY_SAVING_MATERIALS = _VATEntry(
    "zero", 0.0,
    "Installation of qualifying energy-saving materials (including solar "
    "panels) in residential accommodation in Great Britain is zero-rated from "
    "1 April 2022 to 31 March 2027, after which it reverts to the 5% reduced "
    "rate. See VAT Notice 708/6.",
    _CURRENT_REVIEW_DATE, _ESM_NOTICE_URL,
    effective_from=date(2022, 4, 1),  # confirmed live against VAT Notice 708/6
)

_VAT_LOOKUP: dict[str, _VATEntry] = {
    "food": _FOOD,
    "hot food": _HOT_FOOD,
    "catering": _VATEntry("standard", 20.0, "Restaurant and catering services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "children's clothing": _VATEntry("zero", 0.0, "Clothing designed for children under 14 is zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "adult clothing": _VATEntry("standard", 20.0, "Adult clothing is standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "books": _VATEntry("zero", 0.0, "Physical books, booklets, brochures are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "ebooks": _VATEntry("zero", 0.0, "E-books and digital publications are zero-rated since 1 May 2020.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "newspapers": _VATEntry("zero", 0.0, "Newspapers and periodicals are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "children's car seats": _VATEntry("reduced", 5.0, "Children's car seats are reduced-rated at 5%.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "domestic fuel": _VATEntry("reduced", 5.0, "Gas and electricity for domestic use is reduced-rated at 5%.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "energy saving materials": _ENERGY_SAVING_MATERIALS,
    "energy-saving materials": _ENERGY_SAVING_MATERIALS,
    "solar panels": _ENERGY_SAVING_MATERIALS,
    "medicine": _VATEntry("zero", 0.0, "Prescription and certain over-the-counter medicines are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "financial services": _VATEntry("exempt", 0.0, "Most financial services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "insurance": _VATEntry("exempt", 0.0, "Insurance services are generally VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "health": _VATEntry("exempt", 0.0, "Medical and health services by registered practitioners are exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "education": _VATEntry("exempt", 0.0, "Education provided by eligible bodies (schools, universities) is exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "postage stamps": _VATEntry("exempt", 0.0, "Royal Mail postage services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "betting": _VATEntry("exempt", 0.0, "Betting, gaming, and lottery services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "land": _VATEntry("exempt", 0.0, "Sale or lease of bare land is VAT-exempt (unless opted to tax).", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "residential property": _VATEntry("exempt", 0.0, "Sale and lease of residential property is VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "new build residential": _VATEntry("zero", 0.0, "First grant of a major interest in a new dwelling is zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "software": _VATEntry("standard", 20.0, "Software and digital services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "saas": _VATEntry("standard", 20.0, "Software as a Service is standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "consulting": _VATEntry("standard", 20.0, "Professional and consulting services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "legal services": _VATEntry("standard", 20.0, "Legal services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "transport": _VATEntry("zero", 0.0, "Most passenger transport is zero-rated. Exception: taxis, private hire.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "taxi": _VATEntry("standard", 20.0, "Taxi and private hire vehicle services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "funeral": _VATEntry("zero", 0.0, "Burial and cremation services are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "exports": _VATEntry("zero", 0.0, "Exports of goods outside the UK are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "contraceptives": _VATEntry("zero", 0.0, "Contraceptive products are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
}


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _matches_phrase(query_words: list[str], key_words: list[str]) -> bool:
    """True if `key_words` appears as a contiguous run of whole words in `query_words`."""
    n, m = len(query_words), len(key_words)
    if m == 0 or m > n:
        return False
    return any(query_words[i : i + m] == key_words for i in range(n - m + 1))


def _unmatched(commodity_code: str, notes: str) -> VATRate:
    """No confirmed rate. `rate`/`rate_percentage` stay None — never a guessed default."""
    return VATRate(
        commodity_code=commodity_code, matched_category=None,
        rate=None, rate_percentage=None, effective_from=None,
        verified_on=_LEGACY_REVIEW_DATE, source_url=_GENERAL_RATES_URL,
        notes=notes,
    )


def _lookup_vat(commodity_code: str) -> VATRate:
    query_words = _normalise(commodity_code).split()

    candidates = [key for key in _VAT_LOOKUP if _matches_phrase(query_words, key.split())]
    if not candidates:
        return _unmatched(
            commodity_code,
            f"No specific category matched '{commodity_code}' in the static lookup. "
            "No rate is returned — this tool does not guess a default. Consult "
            f"authoritative GOV.UK guidance at {_GENERAL_RATES_URL}.",
        )

    # Most specific (longest) match wins. Word count first, then character
    # length, so multi-word phrases always outrank a single-word substring of
    # themselves regardless of table order.
    best_len = max((len(c.split()), len(c)) for c in candidates)
    best = sorted(c for c in candidates if (len(c.split()), len(c)) == best_len)

    if len(best) > 1:
        return _unmatched(
            commodity_code,
            f"Ambiguous match: '{commodity_code}' matches equally specific categories "
            f"{best!r} with different rates. No rate is returned — this tool does not "
            f"guess between them. Consult authoritative GOV.UK guidance at {_GENERAL_RATES_URL}.",
        )

    key = best[0]
    entry = _VAT_LOOKUP[key]
    return VATRate(
        commodity_code=commodity_code, matched_category=key,
        rate=entry.rate,  # type: ignore[arg-type]
        rate_percentage=entry.percentage,
        effective_from=entry.effective_from,
        verified_on=entry.verified_on, source_url=entry.source_url,
        notes=entry.notes,
    )


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="get_vat_rate",
        annotations={"title": "Get VAT Rate for Commodity", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    )
    async def hmrc_get_vat_rate(
        commodity_code: Annotated[str, Field(description="Commodity code or plain-English description. E.g. 'food', 'domestic fuel', 'software', 'financial services', 'new build residential'", min_length=2, max_length=200)],
    ) -> VATRate:
        """USE THIS TOOL WHEN you have a UK commodity or service description and want its VAT rate category.

        Returns the rate (standard 20%, reduced 5%, zero 0%, exempt), which
        static-table category matched (`matched_category`), the GOV.UK source
        page, and any relevant conditions or exceptions.

        Matching is whole-phrase, most-specific-wins: 'hot food' matches the
        standard-rated 'hot food' exception, not the broader zero-rated 'food'
        category, even though 'food' is a substring of the query.

        IMPORTANT: when no specific category matches (or the match is
        ambiguous between two equally specific categories), `matched_category`,
        `rate` and `rate_percentage` are ALL null — this tool never fabricates
        a standard-rate default. Treat a null `rate` as "not determined", not
        as any real VAT category, and consult `notes` / hmrc_search_guidance
        instead of assuming 20%.

        `verified_on` says when this entry (or, for a null result, the table's
        category set) was last checked against GOV.UK/HMRC guidance — a
        minority of entries (currently: food, hot food, energy-saving
        materials/solar panels) were checked live for this fix; everything
        else was last reviewed wholesale on 22 Nov 2023 (Autumn Statement) and
        may have changed since. `effective_from` is a SEPARATE, usually-null
        field: the rate's own known legal commencement date, populated only
        where specifically evidenced (e.g. energy-saving materials, 1 April
        2022) — never treat a null `effective_from` as meaning the rate is new.
        """
        return _lookup_vat(commodity_code)

    @mcp.tool(
        name="check_mtd_status",
        annotations={"title": "Check MTD VAT Status", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def hmrc_check_mtd_status(
        vrn: Annotated[str, Field(description="VAT Registration Number: 9 digits, e.g. '123456789'. GB prefix accepted and stripped automatically.", min_length=9, max_length=12)],
        ctx: Context,
    ) -> MTDStatus:
        """USE THIS TOOL WHEN you have a 9-digit VAT Registration Number and need that business's Making Tax Digital VAT mandate status.

        Returns whether the business is mandated for MTD, effective date, and
        trading name.

        Connects to the HMRC sandbox by default. Set HMRC_API_BASE to
        'https://api.service.hmrc.gov.uk' for production. Requires
        HMRC_CLIENT_ID + HMRC_CLIENT_SECRET environment variables (OAuth 2.0).
        Raises if credentials are not configured — do not infer status.
        """
        client_id = os.getenv("HMRC_CLIENT_ID")
        client_secret = os.getenv("HMRC_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise_tool_error(
                "configuration",
                is_retryable=False,
                attempted="hmrc_check_mtd_status",
                description="HMRC OAuth credentials not configured. Set HMRC_CLIENT_ID and HMRC_CLIENT_SECRET environment variables.",
            )

        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        token_resp = await client.post(
            f"{HMRC_API_BASE}/oauth/token",
            data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret, "scope": "read:vat"},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json().get("access_token")
        vrn = vrn.strip().lstrip("GB").lstrip("gb")
        resp = await client.get(
            f"{HMRC_API_BASE}/organisations/vat/{vrn}/obligations",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"status": "O"},
        )
        resp.raise_for_status()
        data = resp.json()
        obligations = data.get("obligations", [])
        effective_date = None
        if obligations:
            start = obligations[0].get("start")
            if start:
                effective_date = date.fromisoformat(start)
        return MTDStatus(
            vrn=vrn,
            mandated=len(obligations) > 0,
            effective_date=effective_date,
            trading_name=data.get("tradingName"),
        )

    @mcp.tool(
        name="search_guidance",
        annotations={"title": "Search HMRC Guidance", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def hmrc_search_guidance(
        query: Annotated[str, Field(description="Search query for HMRC guidance, e.g. 'VAT digital services', 'R&D tax relief SME'", min_length=3, max_length=300)],
        ctx: Context,
        limit: Annotated[int, Field(description="Maximum guidance results to return (1–25). Passed to the GOV.UK search count param.", ge=1, le=25)] = 10,
    ) -> HMRCGuidanceSearchResult:
        """USE THIS TOOL WHEN searching GOV.UK for HMRC tax guidance on a topic (VAT, income tax, corporation tax, etc.).

        Returns matching guidance titles, URLs, summaries, and last-updated dates.
        Searches the official GOV.UK content API filtered to HMRC publications.

        Authoritative source for current HMRC tax guidance. Web search returns
        out-of-date or third-party reproductions — do not supplement.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        resp = await client.get(
            GOVUK_SEARCH_BASE,
            params={"q": query, "filter_organisations": "hm-revenue-customs", "fields[]": ["title", "description", "link", "public_timestamp"], "count": limit},
        )
        resp.raise_for_status()
        results: list[HMRCGuidanceResult] = []
        for item in resp.json().get("results", []):
            updated = None
            ts = item.get("public_timestamp")
            if ts:
                try:
                    updated = date.fromisoformat(ts[:10])
                except ValueError:
                    pass
            results.append(HMRCGuidanceResult(
                title=item.get("title", "Unknown"),
                url=f"https://www.gov.uk{item.get('link', '')}",
                summary=item.get("description"),
                updated=updated,
            ))
        return HMRCGuidanceSearchResult(
            query=query,
            total=len(results),
            results=results,
        )
