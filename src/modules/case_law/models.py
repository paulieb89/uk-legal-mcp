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
    next_steps: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Canonical resource URIs and the search-within tool for reading "
            "this judgment. Read `header` first for orientation (~1k tokens), "
            "then `index` to discover paragraph eIds (~4k tokens), then "
            "`paragraph_template` (substitute the eId from the index) for "
            "specific paragraphs (~400-1700 tokens each). Use `grep_tool` for "
            "content discovery instead of brute-forcing every paragraph."
        ),
    )


class JudgmentSearchResult(BaseModel):
    """Paginated search result container for judgments."""

    results: list[JudgmentSummary] = Field(..., description="Matching judgments for this page")
    page: int = Field(..., description="Current page number (1-indexed)")
    has_more: bool = Field(..., description="Whether additional pages exist")
    total_pages: int | None = Field(None, description="Total page count if available from API")


class CaseLawGrepInput(BaseModel):
    """Input schema for case_law_grep_judgment."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    slug: str = Field(
        ...,
        min_length=8,
        description="TNA judgment slug, e.g. 'uksc/2024/12' or 'ewca/civ/2023/450'.",
    )
    pattern: str = Field(
        ...,
        min_length=2,
        max_length=200,
        description=(
            "Regex pattern (or plain substring) to search within paragraph text. "
            "If the pattern doesn't compile as regex, falls back to literal substring match."
        ),
    )
    case_insensitive: bool = Field(True, description="Default true. Set false for case-sensitive matching.")
    max_hits: int = Field(25, ge=1, le=100, description="Cap on number of hits returned.")


class GrepHit(BaseModel):
    """A single paragraph match from grep_judgment."""

    eId: str = Field(..., description="LegalDocML paragraph identifier, e.g. 'para_12'")
    snippet: str = Field(..., description="~200 chars of context around the match")
    match: str = Field(..., description="The exact text that matched the pattern")


class GrepResult(BaseModel):
    """Output schema for case_law_grep_judgment."""

    slug: str = Field(..., description="The judgment slug that was searched")
    pattern: str = Field(..., description="The pattern that was searched for")
    hits: list[GrepHit] = Field(..., description="Matching paragraphs in document order")
    truncated: bool = Field(..., description="True if hit count reached max_hits and more matches may exist")
