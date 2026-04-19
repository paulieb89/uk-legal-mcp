"""Pydantic models for the parliament module."""

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HansardContribution(BaseModel):
    """A single Hansard debate contribution."""

    model_config = ConfigDict(str_strip_whitespace=True)

    member_name: str = Field(..., description="Name of the contributing member")
    party: str | None = Field(None, description="Political party affiliation")
    constituency: str | None = Field(None, description="Constituency (Commons only; None for Lords)")
    date: Date = Field(..., description="Date the contribution was made")
    debate_title: str = Field(..., description="Title of the debate or question")
    section: str = Field(..., description="Hansard section (e.g. 'Commons Chamber', 'Written Answers')")
    text: str = Field(..., description="Full text of the contribution (capped at 3000 characters)")
    url: str = Field(..., description="Direct Hansard URL to this contribution")


class HansardSearchResult(BaseModel):
    """Result of a Hansard debate search.

    Wraps the list of matching contributions with the query and filters
    so the client sees a real nested object on the wire.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The phrase that was searched in Hansard")
    from_date: Date | None = Field(None, description="Start date filter applied, if any")
    to_date: Date | None = Field(None, description="End date filter applied, if any")
    member: str | None = Field(None, description="Member name filter applied, if any")
    total: int = Field(..., description="Number of contributions returned in this call")
    contributions: list[HansardContribution] = Field(
        default_factory=list,
        description="Matching Hansard contributions. Each `text` field is capped at 3000 characters.",
    )


class MemberDebatesResult(BaseModel):
    """Result of a member-filtered Hansard search.

    Wraps the contributions for one member (optionally filtered further
    by topic) with query metadata.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    topic: str | None = Field(None, description="Topic phrase filter applied, if any")
    total: int = Field(..., description="Number of contributions returned in this call")
    contributions: list[HansardContribution] = Field(
        default_factory=list,
        description="Hansard contributions for the member. Each `text` field is capped at 3000 characters.",
    )


class PolicyVibeResult(BaseModel):
    """Result of a parliamentary sentiment analysis on a policy topic."""

    query: str = Field(..., description="The policy query that was analysed")
    contributions: list[HansardContribution] = Field(..., description="Raw contributions retrieved from Hansard")
    sentiment_summary: str | None = Field(None, description="LLM-generated sentiment summary (None if sampling unavailable)")
    key_supporters: list[str] = Field(default_factory=list, description="Members identified as supportive")
    key_opponents: list[str] = Field(default_factory=list, description="Members identified as opposed or critical")
    key_concerns: list[str] = Field(default_factory=list, description="Main concerns raised in debate")


class MemberResult(BaseModel):
    """A current or former Member of Parliament or Lord."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Parliament Members API member ID")
    name: str = Field(..., description="Full display name")
    party: str = Field(..., description="Current or last party affiliation")
    constituency: str | None = Field(None, description="Constituency (Commons); None for Lords")
    house: Literal["Commons", "Lords"] = Field(..., description="House of Parliament")
    is_current: bool = Field(..., description="Whether the member currently sits")


class MemberSearchResult(BaseModel):
    """Result of a parliament member name search.

    Wraps the list of matching members with search metadata so the
    LLM client sees a real nested object on the wire rather than a
    stringified JSON blob.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The name that was searched")
    total: int = Field(..., description="Number of members matching the query")
    members: list[MemberResult] = Field(
        default_factory=list,
        description=(
            "Matching members. Use the integer `id` field from any member "
            "to call parliament_member_debates or parliament_member_interests."
        ),
    )


class Interest(BaseModel):
    """A single registered financial interest."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(..., description="Interest category (e.g. 'Directorships', 'Donations')")
    description: str = Field(..., description="Description of the interest")
    date_created: Date | None = Field(None, description="Date the interest was registered")
    date_amended: Date | None = Field(None, description="Date the interest was last amended")


class MemberInterestsPage(BaseModel):
    """A page of registered financial interests for a member.

    Returned by parliament_member_interests. Callers paginate by
    re-calling with offset=offset+returned while has_more is True.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    category: str | None = Field(
        None,
        description="Category filter applied to this query, or None for all categories",
    )
    offset: int = Field(..., description="Number of interests skipped before this page")
    limit: int = Field(..., description="Max interests requested for this page")
    returned: int = Field(..., description="Number of interests actually returned in this call")
    has_more: bool = Field(
        ...,
        description=(
            "True if there may be more interests beyond this page. "
            "Re-call with offset=offset+returned to fetch the next page."
        ),
    )
    interests: list[Interest] = Field(
        default_factory=list,
        description=(
            "The interests in this page. `description` text is capped per "
            "the max_description_chars input parameter."
        ),
    )


class PetitionSummary(BaseModel):
    """A UK Parliament petition."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Petition ID")
    action: str = Field(..., description="Petition title / call to action")
    state: str = Field(..., description="Petition state (open, closed, etc.)")
    signature_count: int = Field(..., description="Number of signatures")
    created_at: Date | None = Field(None, description="Date the petition was created")
    government_response_at: Date | None = Field(None, description="Date of government response, if any")
    debate_date: Date | None = Field(None, description="Date the petition was debated, if any")
    url: str = Field(..., description="Petition URL on petition.parliament.uk")


class PetitionSearchResult(BaseModel):
    """Result of a UK Parliament petitions search.

    Wraps the matching petitions with the originating query and state
    filter.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str = Field(..., description="The term that was searched in petitions")
    state: Literal["open", "closed", "all"] = Field(
        ..., description="Petition state filter applied to this query"
    )
    total: int = Field(..., description="Number of petitions returned in this call")
    petitions: list[PetitionSummary] = Field(
        default_factory=list,
        description="Matching petitions (title, state, signature count, key dates, URL).",
    )
