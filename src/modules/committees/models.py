"""Pydantic models for the committees module."""

from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CommitteeMember(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Member display name")
    party: str | None = Field(None, description="Political party")
    role: str | None = Field(None, description="Role on the committee (e.g. 'Chair')")


class CommitteeSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Committee ID")
    name: str = Field(..., description="Committee name")
    house: str | None = Field(None, description="Commons, Lords, or Joint")
    is_active: bool | None = Field(None, description="Whether the committee is currently active (None if unknown)")
    url: str | None = Field(None, description="Parliament URL for this committee")


class CommitteeSearchResult(BaseModel):
    """Result of a committees_search_committees call."""

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str | None = Field(None, description="Name substring filter applied, or None")
    house: str | None = Field(None, description="House filter applied, or None")
    active_only: bool = Field(..., description="Whether results were restricted to currently active committees")
    total: int = Field(..., description="Number of committees returned in this call")
    committees: list[CommitteeSummary] = Field(
        default_factory=list,
        description="Matching committees. Use committees_get_committee for membership detail.",
    )


class CommitteeDetail(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Committee ID")
    name: str = Field(..., description="Committee name")
    house: str | None = Field(None, description="Commons, Lords, or Joint")
    phone: str | None = Field(None, description="Contact phone number")
    email: str | None = Field(None, description="Contact email")
    url: str | None = Field(None, description="Parliament URL for this committee")
    members: list[CommitteeMember] = Field(default_factory=list, description="Current committee members")


class CommitteeBusinessSummary(BaseModel):
    """Source-supplied parliamentary committee business or inquiry identity."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Official CommitteeBusiness ID; use as committee_business_id for exact evidence filtering")
    title: str = Field(..., description="Source-supplied inquiry or business title")
    type: str | None = Field(None, description="Source-supplied business type name, e.g. Inquiry")
    is_inquiry: bool | None = Field(None, description="Source-supplied type flag; null when absent")
    status: str | None = Field(None, description="Source-supplied business status where present")
    open_date: Date | None = Field(None, description="Source-supplied business opening date")
    close_date: Date | None = Field(None, description="Source-supplied business closing date")
    latest_report: dict[str, Any] | None = Field(None, description="Source-supplied latest-report metadata, unmodified when present")


class CommitteeBusinessSearchResult(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    committee_id: int = Field(..., description="Committee ID used to scope the discovery request")
    query: str | None = Field(None, description="Source search term applied, if any")
    total: int = Field(..., description="Number of businesses returned in this page")
    businesses: list[CommitteeBusinessSummary] = Field(default_factory=list, description="Matching source-supplied committee businesses or inquiries")


class EvidenceDocumentFile(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    file_name: str | None = Field(None, description="Source-supplied document filename")
    format: str | None = Field(None, description="Source-supplied document file format")
    size_bytes: int | None = Field(None, description="Source-supplied document file size in bytes")
    url: str | None = Field(None, description="Source-supplied file URL; null means the source supplied no direct URL")


class EvidenceDocument(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    document_id: int | None = Field(None, description="Source document ID")
    files: list[EvidenceDocumentFile] = Field(default_factory=list, description="Source-supplied document file metadata")


class EvidenceItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Evidence item ID")
    type: Literal["oral", "written"] = Field(..., description="Type of evidence")
    title: str = Field(..., description="Evidence title or session description (may be truncated per max_title_chars)")
    date: Date | None = Field(None, description="Compatibility date: oral meeting_date when present, otherwise publication_date; use the source-specific fields for research")
    meeting_date: Date | None = Field(None, description="Source oral-evidence meeting date; null for written evidence or absent source data")
    publication_date: Date | None = Field(None, description="Source publication date; null only when the upstream record omits it")
    committee_businesses: list[CommitteeBusinessSummary] = Field(default_factory=list, description="Source inquiry/business identities associated with this evidence item")
    internal_reference: str | None = Field(None, description="Source internal submission reference, usually for written evidence")
    hc_number: str | None = Field(None, description="Source HC paper number where supplied")
    session: str | None = Field(None, description="Source parliamentary session description where supplied")
    document: EvidenceDocument | None = Field(None, description="Source document metadata; null when no document is supplied")
    legacy_html_url: str | None = Field(None, description="Source legacy HTML URL where supplied")
    legacy_pdf_url: str | None = Field(None, description="Source legacy PDF URL where supplied")
    witnesses: list[str | None] | None = Field(None, description=(
        "Witness display names (oral evidence only, capped at 10 per item). "
        "An organisation witness (e.g. a regulator or company giving evidence "
        "collectively, not through one named individual) has no personal name "
        "upstream — that entry is rendered as '<Organisation> (<Role>)' "
        "instead, e.g. 'Bank of England (Governor)'. A null entry means "
        "committees-api.parliament.uk supplied neither a personal name nor "
        "an organisation for that witness slot — honest absence, not a "
        "fabricated placeholder; it is not omitted from the list, so the "
        "count of entries still matches the number of witnesses upstream."
    ))
    url: str | None = Field(None, description="URL to the evidence document")


class CommitteeEvidencePage(BaseModel):
    """A page of evidence submissions to a parliamentary committee.

    Returned by committees_search_evidence. Callers paginate by
    re-calling with offset=offset+returned while has_more is True.
    When evidence_type="both", the sequence being paged is all oral
    evidence followed by all written evidence.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    committee_id: int = Field(..., description="Committee ID this page belongs to")
    evidence_type: Literal["oral", "written", "both"] = Field(
        ..., description="Evidence type filter applied to this query"
    )
    offset: int = Field(..., description="Number of evidence items skipped before this page")
    limit: int = Field(..., description="Max evidence items requested for this page")
    returned: int = Field(..., description="Number of evidence items actually returned in this call")
    total: int = Field(..., description=(
        "Evidence items matching this query, from committees-api's totalResults "
        "(for evidence_type='both', the oral count plus the written count)"
    ))
    has_more: bool = Field(
        ...,
        description=(
            "True if evidence exists beyond this page (offset + returned < total). "
            "Re-call with offset=offset+returned to fetch the next page."
        ),
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description=(
            "Evidence items in this page. Titles are capped per max_title_chars; "
            "witness lists are capped at 10 per item."
        ),
    )
