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
    title: str = Field(..., description="Evidence title or session description")
    date: Date | None = Field(None, description="Date the evidence was given or submitted")
    witnesses: list[str] | None = Field(None, description="Witness names (oral evidence only)")
    url: str | None = Field(None, description="URL to the evidence document")
