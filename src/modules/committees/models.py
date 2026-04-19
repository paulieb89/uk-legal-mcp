"""Pydantic models for the committees module."""

from datetime import date as Date
from typing import Literal

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


class EvidenceItem(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Evidence item ID")
    type: Literal["oral", "written"] = Field(..., description="Type of evidence")
    title: str = Field(..., description="Evidence title or session description (may be truncated per max_title_chars)")
    date: Date | None = Field(None, description="Date the evidence was given or submitted")
    witnesses: list[str] | None = Field(None, description="Witness names (oral evidence only, capped at 10 per item)")
    url: str | None = Field(None, description="URL to the evidence document")


class CommitteeEvidencePage(BaseModel):
    """A page of evidence submissions to a parliamentary committee.

    Returned by committees_search_evidence. Callers paginate by
    re-calling with offset=offset+returned while has_more is True.
    When evidence_type="both", oral and written evidence are
    interleaved in a single `evidence` list and the limit is split
    across both.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    committee_id: int = Field(..., description="Committee ID this page belongs to")
    evidence_type: Literal["oral", "written", "both"] = Field(
        ..., description="Evidence type filter applied to this query"
    )
    offset: int = Field(..., description="Number of evidence items skipped before this page")
    limit: int = Field(..., description="Max evidence items requested for this page")
    returned: int = Field(..., description="Number of evidence items actually returned in this call")
    has_more: bool = Field(
        ...,
        description=(
            "True if there may be more evidence beyond this page. Re-call with "
            "offset=offset+returned to fetch the next page. Conservative: when "
            "evidence_type='both', True if either oral or written upstream page "
            "came back full."
        ),
    )
    evidence: list[EvidenceItem] = Field(
        default_factory=list,
        description=(
            "Evidence items in this page. Titles are capped per max_title_chars; "
            "witness lists are capped at 10 per item."
        ),
    )
