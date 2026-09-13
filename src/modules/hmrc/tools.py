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
# Matching is exact: after normalising case, whitespace, hyphens and
# apostrophes, the whole query must equal a category name or an explicit
# alias. A category phrase found *inside* a longer query is not a match,
# because the extra words can change the treatment: "pet food" is
# standard-rated although "food" is zero-rated, over-the-counter medicine is
# standard-rated although dispensed prescriptions are zero-rated, and animal
# cremation is standard-rated although burial or cremation of the dead is
# exempt. Earlier versions matched by phrase containment (most specific key
# wins), which silently resolved any unanticipated qualifier into the broader
# category. An unmatched query returns no rate; the caller can retry with one
# of the category names listed in the notes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _VATEntry:
    rate: str
    # None for exempt supplies: exemption is not a 0% taxable rate.
    percentage: float | None
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
_BURIAL_NOTICE_URL = "https://www.gov.uk/guidance/burial-cremation-and-commemoration-of-the-dead-notice-70132"
_PHARMACEUTICAL_NOTICE_URL = "https://www.gov.uk/guidance/health-professionals-pharmaceutical-products-and-vat-notice-70157"

# Most entries below have not been individually re-checked against GOV.UK in
# this pass — `verified_on` for those is the date the table was last reviewed
# wholesale (Autumn Statement 2023). It is a data-currency marker, not a
# claim that the underlying VAT treatment last changed on that date.
_LEGACY_REVIEW_DATE = date(2023, 11, 22)  # Autumn Statement 2023

# Entries individually re-verified against live GOV.UK guidance for the
# source-fidelity audit remediation (see PR description for the fetches).
_CURRENT_REVIEW_DATE = date(2026, 9, 12)
# Entries added or corrected when matching became exact, each checked against
# the GOV.UK page in its source_url.
_QUALIFIER_REVIEW_DATE = date(2026, 9, 13)

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
    "pet food": _VATEntry(
        "standard", 20.0,
        "Products packaged as pet food are standard-rated, unlike most food for "
        "human consumption. Some animals, animal feeding stuffs, plants and seeds "
        "can be zero-rated only if the conditions in VAT Notice 701/15 are met.",
        _QUALIFIER_REVIEW_DATE, _GENERAL_RATES_URL,
    ),
    "catering": _VATEntry("standard", 20.0, "Restaurant and catering services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "children's clothing": _VATEntry("zero", 0.0, "Clothing designed for children under 14 is zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "adult clothing": _VATEntry("standard", 20.0, "Adult clothing is standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "books": _VATEntry("zero", 0.0, "Physical books, booklets, brochures are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "ebooks": _VATEntry("zero", 0.0, "E-books and digital publications are zero-rated since 1 May 2020.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "newspapers": _VATEntry("zero", 0.0, "Newspapers and periodicals are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "children's car seats": _VATEntry("reduced", 5.0, "Children's car seats are reduced-rated at 5%.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "domestic fuel": _VATEntry("reduced", 5.0, "Gas and electricity for domestic use is reduced-rated at 5%.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "energy saving materials": _ENERGY_SAVING_MATERIALS,
    "solar panels": _ENERGY_SAVING_MATERIALS,
    "prescriptions dispensed by a registered pharmacist": _VATEntry(
        "zero", 0.0,
        "Drugs, medicines and other qualifying goods are zero-rated only when all "
        "conditions are met: dispensed to an individual for their personal use; "
        "not for patients in hospital or a similar institution, and not "
        "administered, injected or applied by a health professional in the course "
        "of treatment; dispensed by a registered pharmacist (or under a relevant "
        "provision); and prescribed by a relevant practitioner. Hearing aids, "
        "dentures, spectacles and contact lenses are not qualifying goods. "
        "Medicines sold over the counter are a separate, standard-rated supply. "
        "See VAT Notice 701/57, sections 3.2 and 11.4.5.",
        _QUALIFIER_REVIEW_DATE, _PHARMACEUTICAL_NOTICE_URL,
    ),
    "financial services": _VATEntry("exempt", None, "Most financial services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "insurance": _VATEntry("exempt", None, "Insurance services are generally VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "health": _VATEntry("exempt", None, "Medical and health services by registered practitioners are exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "education": _VATEntry("exempt", None, "Education provided by eligible bodies (schools, universities) is exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "postage stamps": _VATEntry("exempt", None, "Royal Mail postage services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "betting": _VATEntry("exempt", None, "Betting, gaming, and lottery services are VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "land": _VATEntry("exempt", None, "Sale or lease of bare land is VAT-exempt (unless opted to tax).", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "residential property": _VATEntry("exempt", None, "Sale and lease of residential property is VAT-exempt.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "new build residential": _VATEntry("zero", 0.0, "First grant of a major interest in a new dwelling is zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "software": _VATEntry("standard", 20.0, "Software and digital services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "saas": _VATEntry("standard", 20.0, "Software as a Service is standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "consulting": _VATEntry("standard", 20.0, "Professional and consulting services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "legal services": _VATEntry("standard", 20.0, "Legal services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "transport": _VATEntry("zero", 0.0, "Most passenger transport is zero-rated. Exception: taxis, private hire.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "taxi": _VATEntry("standard", 20.0, "Taxi and private hire vehicle services are standard-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "burial or cremation of the dead": _VATEntry(
        "exempt", None,
        "Disposal of the remains of the dead (burial, cremation or burial at sea) "
        "and making arrangements for it are exempt. Goods and services an "
        "undertaker supplies as part of a funeral package that includes the "
        "disposal, such as the coffin, embalming, bearers and transport of the "
        "deceased, are also exempt. Flowers, wreaths, headstones and other "
        "commemorative items, newspaper announcements, and the burial or "
        "cremation of animals are standard-rated. See VAT Notice 701/32.",
        _QUALIFIER_REVIEW_DATE, _BURIAL_NOTICE_URL,
    ),
    "exports": _VATEntry("zero", 0.0, "Exports of goods outside the UK are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
    "contraceptives": _VATEntry("zero", 0.0, "Contraceptive products are zero-rated.", _LEGACY_REVIEW_DATE, _GENERAL_RATES_URL),
}


# Alternative exact wordings for a category (normalised form -> category).
_VAT_ALIASES: dict[str, str] = {
    "dispensing of prescriptions by a registered pharmacist": "prescriptions dispensed by a registered pharmacist",
    "dispensed prescriptions": "prescriptions dispensed by a registered pharmacist",
    "burial or cremation of dead people": "burial or cremation of the dead",
    "burial at sea": "burial or cremation of the dead",
}


def _normalise(text: str) -> str:
    text = text.lower().replace("\u2019", "'").replace("\u2018", "'").replace("-", " ")
    return re.sub(r"\s+", " ", text).strip()


def _unmatched(commodity_code: str, notes: str) -> VATRate:
    """No confirmed rate. `rate`/`rate_percentage` stay None — never a guessed default."""
    return VATRate(
        commodity_code=commodity_code, matched_category=None,
        rate=None, rate_percentage=None, effective_from=None,
        verified_on=None, source_url=_GENERAL_RATES_URL,
        notes=notes,
    )


def _lookup_vat(commodity_code: str) -> VATRate:
    query = _normalise(commodity_code)
    key = query if query in _VAT_LOOKUP else _VAT_ALIASES.get(query)
    if key is None:
        return _unmatched(
            commodity_code,
            f"'{commodity_code}' is not a category in the static lookup. Only an exact "
            "category name matches: extra words are never ignored, because a "
            "qualifier can change the VAT treatment. No rate is returned and this "
            "tool does not guess a default. Categories: "
            f"{', '.join(sorted(_VAT_LOOKUP))}. Otherwise consult authoritative "
            f"GOV.UK guidance at {_GENERAL_RATES_URL} or hmrc_search_guidance.",
        )

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
        """USE THIS TOOL WHEN you know the UK VAT category name for a good or service and want its VAT treatment.

        Exact named-category lookup, not a free-text VAT classifier or tax
        advice. The query must equal a category name, ignoring only case,
        spacing, hyphens and apostrophe style. Extra qualifying words are never
        discarded: 'pet food' is its own category, and 'baby food' is not
        'food'.

        A match returns `rate` (standard, reduced, zero or exempt),
        `rate_percentage` (None for exempt), conditions in `notes`, the GOV.UK
        `source_url`, and that entry's `verified_on` date.

        An unresolved query returns no rate: `matched_category`, `rate`,
        `rate_percentage` and `verified_on` are all null, never a default.
        Its `notes` list the category names. AFTER an unresolved result, retry
        with a listed name or call hmrc_search_guidance.
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
