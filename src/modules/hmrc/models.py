"""Pydantic models for the hmrc module."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VATRate(BaseModel):
    """VAT rate for a commodity or service."""

    model_config = ConfigDict(str_strip_whitespace=True)

    commodity_code: str = Field(..., description="Commodity code or description queried")
    rate: Literal["standard", "reduced", "zero", "exempt", "outside_scope"] = Field(
        ..., description="VAT rate category"
    )
    rate_percentage: float = Field(
        ..., description="Applicable rate as percentage: 20.0 (standard), 5.0 (reduced), 0.0 (zero/exempt)"
    )
    effective_from: date = Field(..., description="Date from which this rate applies")
    notes: str | None = Field(None, description="Any additional notes or conditions on this rate")


class MTDStatus(BaseModel):
    """Making Tax Digital VAT status for a VAT registration number."""

    model_config = ConfigDict(str_strip_whitespace=True)

    vrn: str = Field(..., description="VAT Registration Number queried")
    mandated: bool = Field(..., description="Whether this business is mandated for MTD VAT")
    effective_date: date | None = Field(None, description="Date from which MTD obligation applies")
    trading_name: str | None = Field(None, description="Registered trading name if available")


class HMRCGuidanceResult(BaseModel):
    """A single HMRC guidance document search result."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(..., description="Guidance document title")
    url: str = Field(..., description="GOV.UK URL for the guidance")
    summary: str | None = Field(None, description="Brief summary of the guidance content")
    updated: date | None = Field(None, description="Date the guidance was last updated")
