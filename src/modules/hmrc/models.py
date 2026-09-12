"""Pydantic models for the hmrc module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VATRate(BaseModel):
    """VAT rate for a commodity or service.

    Backed by a small static lookup table, not a live HMRC/GOV.UK query.

    `matched_category` is None whenever no specific table category matched
    (including a genuine tie between two equally-specific categories). In
    that case `rate` and `rate_percentage` are ALSO None — this tool never
    fabricates a rate for an unresolved query. Callers must not assume a
    default (e.g. standard 20%) when `rate` is None; read `notes` for
    guidance on where to check instead.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    commodity_code: str = Field(..., description="Commodity code or description queried")
    matched_category: str | None = Field(
        None,
        description=(
            "The static lookup table category actually matched (e.g. 'hot food'), or "
            "None when no specific category matched, or the match was ambiguous "
            "between two equally-specific categories. `rate`/`rate_percentage` are "
            "None whenever this is None — see `notes`."
        ),
    )
    rate: Literal["standard", "reduced", "zero", "exempt", "outside_scope"] | None = Field(
        None,
        description=(
            "VAT rate category, or None when no specific category was confidently "
            "matched. None here is NOT a stand-in for any real rate — it means the "
            "lookup did not resolve, not that the item is outside VAT's scope."
        ),
    )
    rate_percentage: float | None = Field(
        None,
        description=(
            "Applicable rate as percentage: 20.0 (standard), 5.0 (reduced), 0.0 "
            "(zero/exempt), or None when `rate` is None (no confident match)."
        ),
    )
    effective_from: date | None = Field(
        None,
        description=(
            "Date the represented VAT treatment is known, on researched evidence, "
            "to have taken legal effect. None when no such commencement date has "
            "been established for this entry — that is common and does NOT imply "
            "the rate is new, uncertain, or unmatched. For data currency (when this "
            "entry was last checked against GOV.UK/HMRC guidance) see `verified_on` "
            "instead — the two are deliberately independent."
        ),
    )
    verified_on: date = Field(
        ...,
        description=(
            "Date this entry (or, for an unresolved query, the lookup table's "
            "category set as a whole) was last checked against GOV.UK/HMRC "
            "guidance. A data-currency signal only — see `effective_from` for the "
            "rate's own legal commencement date, when known."
        ),
    )
    source_url: str = Field(..., description="GOV.UK/HMRC guidance page backing this rate")
    notes: str | None = Field(None, description="Any additional notes or conditions on this rate")


class MTDStatus(BaseModel):
    """Making Tax Digital VAT status for a VAT registration number."""

    model_config = ConfigDict(str_strip_whitespace=True)

    vrn: str = Field(..., description="VAT Registration Number queried")
    mandated: bool = Field(..., description="Whether this business is mandated for MTD VAT")
    effective_date: date | None = Field(None, description="Date from which MTD obligation applies")
    trading_name: str | None = Field(None, description="Registered trading name if available")


class HMRCGuidanceResult(BaseModel):
    """A single HMRC guidance document search result."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., description="Guidance document title")
    url: str = Field(..., description="GOV.UK URL for the guidance")
    summary: str | None = Field(None, description="Brief summary of the guidance content")
    updated: date | None = Field(None, description="Date the guidance was last updated")


class HMRCGuidanceSearchResult(BaseModel):
    """Result of an HMRC guidance search on GOV.UK.

    Wraps the list of matching guidance documents with search metadata so
    the LLM client sees a real nested object on the wire rather than a
    stringified JSON blob.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The search query that was run")
    total: int = Field(..., description="Number of guidance documents returned in this call")
    results: list[HMRCGuidanceResult] = Field(
        default_factory=list,
        description=(
            "Matching HMRC guidance pages. Each entry's `summary` is capped "
            "per the max_summary_chars input parameter."
        ),
    )
