"""Pydantic models for the bills module."""

from datetime import date as Date

from pydantic import BaseModel, ConfigDict, Field


class BillSponsor(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Sponsor's display name")
    party: str | None = Field(None, description="Political party")
    house: str | None = Field(None, description="Commons or Lords")


class BillStage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(..., description="Stage name, e.g. 'Second Reading'")
    house: str | None = Field(None, description="House where stage occurred")
    date: Date | None = Field(None, description="Date the stage was reached")
    is_current: bool = Field(False, description="Whether this is the current stage")


class BillSummary(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Bill ID")
    short_title: str = Field(..., description="Short title of the bill")
    long_title: str | None = Field(None, description="Full long title")
    current_house: str | None = Field(None, description="House where the bill currently sits")
    current_stage: str | None = Field(None, description="Current legislative stage")
    is_act: bool = Field(False, description="Whether the bill has received Royal Assent")
    url: str = Field(..., description="Parliament URL for this bill")


class BillDetail(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    id: int = Field(..., description="Bill ID")
    short_title: str = Field(..., description="Short title of the bill")
    long_title: str | None = Field(None, description="Full long title")
    summary: str | None = Field(None, description="Bill summary text")
    current_house: str | None = Field(None, description="House where the bill currently sits")
    originating_house: str | None = Field(None, description="House where the bill was introduced")
    current_stage: str | None = Field(None, description="Current legislative stage")
    sponsors: list[BillSponsor] = Field(default_factory=list, description="Bill sponsors")
    stages: list[BillStage] = Field(default_factory=list, description="Legislative stages the bill has passed through")
    is_act: bool = Field(False, description="Whether the bill has received Royal Assent")
    royal_assent_date: Date | None = Field(None, description="Date Royal Assent was given")
    url: str = Field(..., description="Parliament URL for this bill")
