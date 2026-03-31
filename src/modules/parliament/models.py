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
    text: str = Field(..., description="Full text of the contribution")
    url: str = Field(..., description="Direct Hansard URL to this contribution")


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


class Interest(BaseModel):
    """A single registered financial interest."""

    model_config = ConfigDict(str_strip_whitespace=True)

    category: str = Field(..., description="Interest category (e.g. 'Directorships', 'Donations')")
    description: str = Field(..., description="Description of the interest")
    date_created: Date | None = Field(None, description="Date the interest was registered")
    date_amended: Date | None = Field(None, description="Date the interest was last amended")


class MemberInterests(BaseModel):
    """A member's registered financial interests."""

    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    interests: list[Interest] = Field(default_factory=list, description="Registered interests")
    total: int = Field(0, description="Total number of interests returned")


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
