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
