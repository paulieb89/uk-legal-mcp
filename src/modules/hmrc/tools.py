"""
Tools for the hmrc module.

Upstream: HMRC APIs + GOV.UK search API
Wire format: JSON
"""

import json
import os
from datetime import date
from typing import Annotated

import httpx
from fastmcp import FastMCP, Context
from fastmcp.exceptions import ToolError
from pydantic import Field

from ...deps import format_http_error
from .models import HMRCGuidanceResult, HMRCGuidanceSearchResult, MTDStatus, VATRate

HMRC_API_BASE = os.getenv("HMRC_API_BASE", "https://test-api.service.hmrc.gov.uk")
GOVUK_SEARCH_BASE = "https://www.gov.uk/api/search.json"

# ---------------------------------------------------------------------------
# VAT rate static lookup table (updated at each Budget)
# ---------------------------------------------------------------------------

_VAT_LOOKUP: dict[str, tuple[str, float, str | None]] = {
    "food": ("zero", 0.0, "Most basic foods are zero-rated. Exceptions: hot food, catering, confectionery."),
    "hot food": ("standard", 20.0, "Hot food prepared for immediate consumption is standard-rated."),
    "catering": ("standard", 20.0, "Restaurant and catering services are standard-rated."),
    "children's clothing": ("zero", 0.0, "Clothing designed for children under 14 is zero-rated."),
    "adult clothing": ("standard", 20.0, "Adult clothing is standard-rated."),
    "books": ("zero", 0.0, "Physical books, booklets, brochures are zero-rated."),
    "ebooks": ("zero", 0.0, "E-books and digital publications are zero-rated since 1 May 2020."),
    "newspapers": ("zero", 0.0, "Newspapers and periodicals are zero-rated."),
    "children's car seats": ("reduced", 5.0, "Children's car seats are reduced-rated at 5%."),
    "domestic fuel": ("reduced", 5.0, "Gas and electricity for domestic use is reduced-rated at 5%."),
    "energy saving materials": ("reduced", 5.0, "Installation of energy-saving materials in residential properties."),
    "medicine": ("zero", 0.0, "Prescription and certain over-the-counter medicines are zero-rated."),
    "financial services": ("exempt", 0.0, "Most financial services are VAT-exempt."),
    "insurance": ("exempt", 0.0, "Insurance services are generally VAT-exempt."),
    "health": ("exempt", 0.0, "Medical and health services by registered practitioners are exempt."),
    "education": ("exempt", 0.0, "Education provided by eligible bodies (schools, universities) is exempt."),
    "postage stamps": ("exempt", 0.0, "Royal Mail postage services are VAT-exempt."),
    "betting": ("exempt", 0.0, "Betting, gaming, and lottery services are VAT-exempt."),
    "land": ("exempt", 0.0, "Sale or lease of bare land is VAT-exempt (unless opted to tax)."),
    "residential property": ("exempt", 0.0, "Sale and lease of residential property is VAT-exempt."),
    "new build residential": ("zero", 0.0, "First grant of a major interest in a new dwelling is zero-rated."),
    "software": ("standard", 20.0, "Software and digital services are standard-rated."),
    "saas": ("standard", 20.0, "Software as a Service is standard-rated."),
    "consulting": ("standard", 20.0, "Professional and consulting services are standard-rated."),
    "legal services": ("standard", 20.0, "Legal services are standard-rated."),
    "transport": ("zero", 0.0, "Most passenger transport is zero-rated. Exception: taxis, private hire."),
    "taxi": ("standard", 20.0, "Taxi and private hire vehicle services are standard-rated."),
    "funeral": ("zero", 0.0, "Burial and cremation services are zero-rated."),
    "exports": ("zero", 0.0, "Exports of goods outside the UK are zero-rated."),
    "contraceptives": ("zero", 0.0, "Contraceptive products are zero-rated."),
}

_EFFECTIVE_DATE = date(2023, 11, 22)  # Autumn Statement 2023


def _lookup_vat(commodity_code: str) -> VATRate:
    key = commodity_code.strip().lower()
    for k, (rate_name, percentage, notes) in _VAT_LOOKUP.items():
        if k in key or key in k:
            return VATRate(
                commodity_code=commodity_code, rate=rate_name,  # type: ignore[arg-type]
                rate_percentage=percentage, effective_from=_EFFECTIVE_DATE, notes=notes,
            )
    return VATRate(
        commodity_code=commodity_code, rate="standard", rate_percentage=20.0,
        effective_from=_EFFECTIVE_DATE,
        notes=(
            f"No specific exemption found for '{commodity_code}'. "
            "Standard 20% rate assumed. Verify at https://www.gov.uk/guidance/rates-of-vat-on-different-goods-and-services"
        ),
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

        Returns the rate (standard 20%, reduced 5%, zero 0%, exempt), effective
        date, and any relevant conditions or exceptions.

        IMPORTANT: Uses a static lookup table current as of 22 Nov 2023 (Autumn
        Statement). Rates may have changed in subsequent Budgets — for
        time-sensitive advice, verify against GOV.UK via hmrc_search_guidance.
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
            raise ToolError(
                "HMRC OAuth credentials not configured. "
                "Set HMRC_CLIENT_ID and HMRC_CLIENT_SECRET. "
                "Register at https://developer.service.hmrc.gov.uk"
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
