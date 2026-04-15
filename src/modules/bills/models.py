"""Pydantic models for the bills module."""

from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field


class BillSponsor(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Sponsor's display name")
    party: str | None = Field(None, description="Political party")
    house: str | None = Field(None, description="Commons or Lords")


class BillStage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Stage name, e.g. 'Second Reading'")
    house: str | None = Field(None, description="House where stage occurred")
    date: Date | None = Field(None, description="Date the stage was reached")
    is_current: bool = Field(False, description="Whether this is the current stage")


class BillSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Bill ID")
    short_title: str = Field(..., description="Short title of the bill")
    long_title: str | None = Field(None, description="Full long title")
    current_house: str | None = Field(None, description="House where the bill currently sits")
    current_stage: str | None = Field(None, description="Current legislative stage")
    is_act: bool = Field(False, description="Whether the bill has received Royal Assent")
    url: str = Field(..., description="Parliament URL for this bill")


class BillSearchResult(BaseModel):
    """One page of bill search results.

    Wraps matching BillSummary records with search metadata so the LLM
    client sees a real nested object on the wire, and can page through
    large result sets.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The search term that was used")
    offset: int = Field(..., description="Number of results skipped before this page")
    limit: int = Field(..., description="Maximum results requested in this call")
    returned: int = Field(..., description="Number of results actually on this page")
    total: int | None = Field(
        None,
        description=(
            "Total results matching the query across all pages, if the "
            "upstream API reported it. None if unknown."
        ),
    )
    has_more: bool = Field(
        ...,
        description=(
            "True if more results exist beyond this page. Re-call with "
            "offset=offset+returned to fetch the next page."
        ),
    )
    bills: list[BillSummary] = Field(
        default_factory=list,
        description=(
            "Matching bills. Use the integer `id` field from any bill to "
            "call bills_get_bill for full detail."
        ),
    )


class BillDetail(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Bill ID")
    short_title: str = Field(..., description="Short title of the bill")
    long_title: str | None = Field(None, description="Full long title")
    summary: str | None = Field(None, description=(
        "Bill summary text, possibly truncated per max_summary_chars. "
        "Check summary_truncated and summary_original_length for full-text info."
    ))
    summary_truncated: bool = Field(
        False,
        description="True if summary was cut to fit max_summary_chars",
    )
    summary_original_length: int = Field(
        0,
        description="Original summary length in characters before any truncation",
    )
    current_house: str | None = Field(None, description="House where the bill currently sits")
    originating_house: str | None = Field(None, description="House where the bill was introduced")
    current_stage: str | None = Field(None, description="Current legislative stage")
    sponsors: list[BillSponsor] = Field(default_factory=list, description="Bill sponsors")
    stages: list[BillStage] = Field(default_factory=list, description="Legislative stages the bill has passed through")
    is_act: bool = Field(False, description="Whether the bill has received Royal Assent")
    royal_assent_date: Date | None = Field(None, description="Date Royal Assent was given")
    url: str = Field(..., description="Parliament URL for this bill")
