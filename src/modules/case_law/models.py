"""Pydantic models for the case_law module."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JudgmentIdentifier(BaseModel):
    """A structured citation identifier for a judgment."""

    model_config = ConfigDict(str_strip_whitespace=True)

    type: Literal["ukncn", "fclid"] = Field(
        ..., description="Identifier type: 'ukncn' for neutral citation, 'fclid' for Find Case Law internal ID"
    )
    value: str = Field(..., description="Human-readable identifier, e.g. '[2024] UKSC 12'")
    slug: str = Field(..., description="URL slug form, e.g. 'uksc/2024/12'")


class JudgmentSummary(BaseModel):
    """Summary metadata for a single judgment."""

    model_config = ConfigDict(str_strip_whitespace=True)

    uri: str = Field(..., description="TNA stable document URI (pre-2025: court/year/number; post-2025: UUID)")
    title: str = Field(..., description="Case title as published")
    court: str | None = Field(None, description="Court name, e.g. 'UK Supreme Court'")
    published: datetime = Field(..., description="Original publication date")
    updated: datetime = Field(..., description="Last updated timestamp")
    identifiers: list[JudgmentIdentifier] = Field(default_factory=list, description="Neutral citation and other IDs")
    content_hash: str | None = Field(None, description="SHA256 of body text — use for change detection in polling loops")
    xml_url: str | None = Field(None, description="URL to LegalDocML XML source")
    pdf_url: str | None = Field(None, description="URL to PDF version")


class JudgmentSearchResult(BaseModel):
    """Paginated search result container for judgments."""

    results: list[JudgmentSummary] = Field(..., description="Matching judgments for this page")
    page: int = Field(..., description="Current page number (1-indexed)")
    has_more: bool = Field(..., description="Whether additional pages exist")
    total_pages: int | None = Field(None, description="Total page count if available from API")
