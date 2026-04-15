"""Pydantic models for the votes module."""

from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field


class Voter(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    member_id: int = Field(..., description="Parliament Members API member ID")
    name: str = Field(..., description="Member display name")
    party: str | None = Field(None, description="Political party")


class DivisionSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Division ID")
    title: str = Field(..., description="Division title / motion text")
    date: Date = Field(..., description="Date of the division")
    house: str = Field(..., description="Commons or Lords")
    ayes: int = Field(..., description="Number of Aye votes")
    noes: int = Field(..., description="Number of No votes")
    passed: bool = Field(..., description="Whether the motion passed")
    is_government_win: bool | None = Field(None, description="Whether the government won (Lords only)")


class DivisionDetail(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Division ID")
    title: str = Field(..., description="Division title / motion text")
    date: Date = Field(..., description="Date of the division")
    house: str = Field(..., description="Commons or Lords")
    ayes_count: int = Field(..., description="Total Aye votes")
    noes_count: int = Field(..., description="Total No votes")
    passed: bool = Field(..., description="Whether the motion passed")
    is_government_win: bool | None = Field(None, description="Whether the government won (Lords only)")
    aye_voters: list[Voter] = Field(default_factory=list, description="Members who voted Aye (may be truncated)")
    noe_voters: list[Voter] = Field(default_factory=list, description="Members who voted No (may be truncated)")
    truncated: bool = Field(False, description="Whether voter lists were truncated to fit response limits")
    total_aye_voters: int = Field(0, description="Total number of Aye voters before truncation")
    total_noe_voters: int = Field(0, description="Total number of No voters before truncation")


class DivisionsSearchResult(BaseModel):
    """Result of a parliamentary divisions search.

    Wraps the list of matching divisions with search metadata so the
    LLM client sees a real nested object on the wire rather than a
    stringified JSON blob.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    query: str | None = Field(None, description="The search term, if any (None = browse recent)")
    house: str = Field(..., description="Commons or Lords")
    total: int = Field(..., description="Number of divisions returned (capped upstream at 25)")
    divisions: list[DivisionSummary] = Field(
        default_factory=list,
        description=(
            "Matching divisions. Use the integer `id` field with "
            "votes_get_division to fetch the full voter list."
        ),
    )
