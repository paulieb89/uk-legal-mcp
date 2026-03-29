"""Pydantic models for the citations module."""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CitationType(str, Enum):
    NEUTRAL = "neutral"          # [2024] UKSC 12
    LAW_REPORT = "law_report"    # [2024] 1 WLR 100
    LEGISLATION = "legislation"  # s.47 Companies Act 2006
    SI = "si"                    # SI 2018/1234
    EU_RETAINED = "eu_retained"  # Regulation (EU) 2016/679


class ParsedCitation(BaseModel):
    """A single parsed OSCOLA citation with optional resolution."""

    model_config = ConfigDict(str_strip_whitespace=True)

    raw: str = Field(..., description="Original citation text as found in the source")
    type: CitationType = Field(..., description="Classification of the citation type")
    year: int | None = Field(None, description="Year component of the citation")
    court: str | None = Field(
        None,
        description=(
            "Court code: UKSC, UKPC, EWCA Civ, EWCA Crim, EWHC (KB), EWHC (Ch), "
            "EWHC (Comm), EWHC (Fam), EWHC (Pat), EWHC (IPEC), UKUT (IAC), UKUT (TCC), "
            "UKUT (AAC), UKUT (LC), EAT, UKFTT (TC), UKFTT (GRC)"
        ),
    )
    number: int | None = Field(None, description="Judgment number within the year")
    report_series: str | None = Field(
        None,
        description="Law report series abbreviation: WLR, AC, QB, KB, Ch, All ER, EWCA Civ, etc.",
    )
    volume: int | None = Field(None, description="Report volume number (for law reports)")
    page: int | None = Field(None, description="Starting page in the law report")
    legislation_title: str | None = Field(None, description="Title of legislation (for s.NN Act YYYY citations)")
    section: str | None = Field(None, description="Section number referenced")
    si_year: int | None = Field(None, description="SI year (for SI YYYY/NNN citations)")
    si_number: int | None = Field(None, description="SI number")
    resolved_url: str | None = Field(
        None,
        description="TNA Find Case Law or legislation.gov.uk URL if successfully resolved",
    )
    confidence: float = Field(
        ...,
        description="Parse confidence 0.0–1.0. Citations below 0.7 are ambiguous and may have been sent for LLM disambiguation.",
        ge=0.0,
        le=1.0,
    )


class CitationParseResult(BaseModel):
    """Result of parsing all OSCOLA citations from a body of text."""

    citations: list[ParsedCitation] = Field(
        ..., description="All successfully parsed citations (confidence >= 0.7)"
    )
    ambiguous: list[ParsedCitation] = Field(
        ..., description="Citations with confidence < 0.7; may have been partially disambiguated via sampling"
    )
    text_length: int = Field(..., description="Character length of the input text")
    parse_duration_ms: int = Field(..., description="Time taken to parse, in milliseconds")
