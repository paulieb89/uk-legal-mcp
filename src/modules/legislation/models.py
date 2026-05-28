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
    type: str = Field(..., description=(
        "Legislation type code, e.g. 'ukpga' (UK Public General Act), 'uksi' "
        "(Statutory Instrument), 'asp' (Scottish Parliament Act). Use with "
        "`year` and `number` as the triple {type}/{year}/{number} in the "
        "legislation_get_toc / legislation_get_section tools, or in "
        "legislation://{type}/{year}/{number}/section/{section}{?date} resource URIs."
    ))
    year: int = Field(..., description=(
        "Year of enactment or making. Part of the {type}/{year}/{number} triple "
        "that identifies an Act or SI."
    ))
    number: int = Field(..., description=(
        "Chapter (for Acts) or SI number. Part of the {type}/{year}/{number} "
        "triple that identifies an Act or SI."
    ))
    score: float | None = Field(None, description="Relevance score from Lex API (higher = more relevant)")
    url: str = Field(..., description="Canonical legislation.gov.uk URL")
    next_steps: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Canonical resource URIs for reading this Act/SI. Read `toc` to "
            "discover sections, then `section_template` (substitute the section "
            "number) for the section text. Both URIs accept an optional "
            "?date=YYYY-MM-DD query for point-in-time historical research."
        ),
    )


class LegislationSearchResult(BaseModel):
    """Container for legislation search results."""

    results: list[LegislationResult] = Field(..., description="Matching legislation items")
    total: int = Field(..., description="Total number of matches")


class LegislationTOC(BaseModel):
    """Paginated table of contents for an Act or Statutory Instrument.

    The upstream XML is fetched in one shot (legislation.gov.uk has no
    server-side pagination), then sliced client-side via offset/limit.
    For very large statutes like the Companies Act 2006 (1300+ items)
    use offset to page through. Check has_more and total_items to
    know the full size.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    type: str = Field(..., description="Legislation type code echoed from the request")
    year: int = Field(..., description="Year of enactment echoed from the request")
    number: int = Field(..., description="Chapter or SI number echoed from the request")
    offset: int = Field(..., description="Offset applied to the full TOC item list")
    limit: int = Field(..., description="Page size applied after offset")
    returned: int = Field(..., description="Number of items in this response")
    total_items: int = Field(
        ...,
        description=(
            "Total number of structural items parsed from the XML, "
            "before offset/limit. Use this to know the full size of the TOC."
        ),
    )
    has_more: bool = Field(
        ...,
        description="True if more items remain beyond offset+returned",
    )
    items: list[str] = Field(
        default_factory=list,
        description=(
            "TOC entries in XML document order, formatted as "
            "'<id>: <title>', e.g. 'section-47: Definitions'. When calling "
            "legislation_get_section pass only the numeric part ('47', not "
            "'section-47')."
        ),
    )


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
        description="Territorial extent: list of 'England', 'Wales', 'Scotland', 'Northern Ireland'. Empty list means unknown — do not assume full UK extent.",
    )
    version_date: date | None = Field(None, description="Date of the version retrieved")
    prospective: bool | None = Field(None, description="True if this section has not yet come into force; None if unknown")
    source_format: Literal["xml", "html_fallback"] = Field(
        "xml",
        description="Source parsed for this response. html_fallback means CLML XML was unavailable and text was parsed from the public HTML page.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal retrieval or parsing warnings the caller should disclose where relevant.",
    )
