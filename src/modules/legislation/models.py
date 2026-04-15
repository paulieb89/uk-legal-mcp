"""Pydantic models for the legislation module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

LegislationType = Literal[
    "ukpga",  # UK Public General Acts
    "uksi",   # UK Statutory Instruments
    "ukppa",  # UK Private and Personal Acts
    "asp",    # Acts of the Scottish Parliament
    "anaw",   # Acts of the National Assembly for Wales
    "nia",    # Northern Ireland Acts
    "ukcm",   # UK Church Measures
    "apni",   # Acts of the Parliament of Northern Ireland
]

VALID_EXTENTS = ["England", "Wales", "Scotland", "Northern Ireland"]


class LegislationResult(BaseModel):
    """Single legislation search result."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., description="Full title of the legislation")
    type: str = Field(..., description="Legislation type code, e.g. 'ukpga', 'uksi'")
    year: int = Field(..., description="Year of enactment or making")
    number: int = Field(..., description="Chapter or SI number")
    score: float | None = Field(None, description="Relevance score from Lex API (higher = more relevant)")
    url: str = Field(..., description="Canonical legislation.gov.uk URL")


class LegislationSearchResult(BaseModel):
    """Container for legislation search results."""

    results: list[LegislationResult] = Field(..., description="Matching legislation items")
    total: int = Field(..., description="Total number of matches")


class LegislationSection(BaseModel):
    """A single section of an Act or SI, with full metadata."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., description="Section title or heading")
    section_number: str = Field(..., description="Section number, e.g. '47', '12A', 'Schedule 2'")
    content: str = Field(..., description=(
        "Plain text content of the section, possibly truncated per max_chars. "
        "Check content_truncated and original_length for full-text information."
    ))
    content_truncated: bool = Field(
        False,
        description="True if content was cut to fit max_chars",
    )
    original_length: int = Field(
        0,
        description="Original plain-text length in characters before any truncation",
    )
    in_force: bool | None = Field(None, description="Whether this section is currently in force; None if unknown")
    extent: list[str] = Field(
        default_factory=list,
        description="Territorial extent: list of 'England', 'Wales', 'Scotland', 'Northern Ireland'",
    )
    version_date: date | None = Field(None, description="Date of the version retrieved")
    prospective: bool = Field(False, description="True if this section has not yet come into force")
