"""Tools for the bills module.

Upstream API (public, no auth):
  - bills-api.parliament.uk — bill search, detail, stages, sponsors
"""

import json
from datetime import date
from typing import Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import BillDetail, BillSearchResult, BillSponsor, BillStage, BillSummary

BILLS_BASE = "https://bills-api.parliament.uk/api/v1"

HOUSE_MAP = {"Commons": 1, "Lords": 2}

STAGE_ID_MAP: dict[str, list[int]] = {
    "firstreading": [6, 1],
    "secondreading": [7, 2],
    "committee": [8, 3, 48, 49],
    "report": [9, 4],
    "thirdreading": [10, 5],
    "royalassent": [11],
}


class BillSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description=(
        "Search term for bill titles and descriptions, "
        "e.g. 'online safety' or 'financial services'."
    ), min_length=1, max_length=500)
    session: int | None = Field(None, description=(
        "Parliamentary session ID. Omit to search all sessions. "
        "Session numbers change each year (e.g. 40 = 2024-25, 39 = 2023-24)."
    ), ge=1)
    house: Literal["Commons", "Lords", "All"] | None = Field(None, description="Filter by originating house. Omit for all houses.")
    stage: Literal["firstreading", "secondreading", "committee", "report", "thirdreading", "royalassent"] | None = Field(
        None, description="Filter by current legislative stage."
    )
    offset: int = Field(
        0,
        ge=0,
        le=2000,
        description=(
            "Number of results to skip before this page. Default 0 for the "
            "first page. Re-call with offset=offset+returned while has_more "
            "is true to paginate."
        ),
    )
    limit: int = Field(
        20,
        ge=1,
        le=100,
        description=(
            "Maximum bills to return in this call. Default 20 keeps "
            "responses focused; raise up to 100 for bulk exports."
        ),
    )


class BillDetailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    bill_id: int = Field(..., description="Bill ID from bills_search_bills results.", ge=1)
    max_summary_chars: int = Field(
        5000,
        ge=500,
        le=50000,
        description=(
            "Maximum characters of the bill summary text to return. Default "
            "5,000 (~1,250 tokens) covers most bills. Raise for substantive "
            "government bills (Finance Act, Levelling-up) whose summary runs "
            "longer. Check summary_truncated in the response to see if it was cut."
        ),
    )


def _parse_house(house_val) -> str | None:
    if isinstance(house_val, dict):
        return house_val.get("name")
    if isinstance(house_val, str):
        return house_val
    return None


def _parse_bill_summary(item: dict) -> BillSummary:
    current_stage_raw = item.get("currentStage")
    current_stage = None
    if isinstance(current_stage_raw, dict):
        stage_name = current_stage_raw.get("stageName") or current_stage_raw.get("description")
        current_stage = stage_name

    return BillSummary(
        id=item.get("billId", 0),
        short_title=item.get("shortTitle", "Unknown"),
        long_title=item.get("longTitle"),
        current_house=_parse_house(item.get("currentHouse")),
        current_stage=current_stage,
        is_act=item.get("isAct", False),
        url=f"https://bills.parliament.uk/bills/{item.get('billId', 0)}",
    )


def _parse_bill_detail(data: dict, max_summary_chars: int) -> BillDetail:
    sponsors = []
    for s in data.get("sponsors", []):
        member = s.get("member", {})
        sponsors.append(BillSponsor(
            name=member.get("name", s.get("name", "Unknown")),
            party=member.get("party"),
            house=_parse_house(member.get("house")),
        ))

    stages = []
    current_stage_name = None
    current_stage_data = data.get("currentStage")
    if isinstance(current_stage_data, dict):
        stage_name = current_stage_data.get("description") or current_stage_data.get("stageName", "Unknown")
        current_stage_name = stage_name

        sitting_date = None
        sittings = current_stage_data.get("stageSittings", [])
        if sittings and isinstance(sittings, list):
            date_str = sittings[0].get("date", "")
            if date_str:
                try:
                    sitting_date = date.fromisoformat(date_str[:10])
                except ValueError:
                    pass

        stages.append(BillStage(
            name=stage_name,
            house=_parse_house(current_stage_data.get("house")),
            date=sitting_date,
            is_current=True,
        ))

    royal_assent_date = None

    raw_summary = data.get("summary")
    summary: str | None
    if raw_summary:
        summary_original_length = len(raw_summary)
        if summary_original_length > max_summary_chars:
            summary_truncated = True
            summary = raw_summary[:max_summary_chars] + " …[truncated]"
        else:
            summary_truncated = False
            summary = raw_summary
    else:
        summary = None
        summary_truncated = False
        summary_original_length = 0

    return BillDetail(
        id=data.get("billId", 0),
        short_title=data.get("shortTitle", "Unknown"),
        long_title=data.get("longTitle"),
        summary=summary,
        summary_truncated=summary_truncated,
        summary_original_length=summary_original_length,
        current_house=_parse_house(data.get("currentHouse")),
        originating_house=_parse_house(data.get("originatingHouse")),
        current_stage=current_stage_name,
        sponsors=sponsors,
        stages=stages,
        is_act=data.get("isAct", False),
        royal_assent_date=royal_assent_date,
        url=f"https://bills.parliament.uk/bills/{data.get('billId', 0)}",
    )


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_bills",
        annotations={"title": "Search Parliamentary Bills", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def bills_search_bills(params: BillSearchInput, ctx: Context) -> BillSearchResult:
        """Search UK parliamentary bills by keyword, session, house, or legislative stage.

        Returns a paginated page of bill summaries including title, current stage, and
        whether it has become an Act. Use bills_get_bill with the bill ID for full detail.

        Args:
            params: BillSearchInput with query, optional session/house/stage filters, pagination.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "SearchTerm": params.query,
            "Take": params.limit,
            "Skip": params.offset,
        }
        if params.session is not None:
            qp["Session"] = params.session
        if params.house and params.house != "All":
            qp["CurrentHouse"] = HOUSE_MAP.get(params.house)
        if params.stage:
            qp["BillStage"] = STAGE_ID_MAP[params.stage]

        resp = await client.get(f"{BILLS_BASE}/Bills", params=qp)
        resp.raise_for_status()
        data = resp.json()

        bills = [_parse_bill_summary(item) for item in data.get("items", [])]
        total = data.get("totalResults")
        if not isinstance(total, int):
            total = None
        has_more = (
            (params.offset + len(bills)) < total
            if total is not None
            else len(bills) == params.limit
        )

        return BillSearchResult(
            query=params.query,
            offset=params.offset,
            limit=params.limit,
            returned=len(bills),
            total=total,
            has_more=has_more,
            bills=bills,
        )

    @mcp.tool(
        name="get_bill",
        annotations={"title": "Get Bill Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def bills_get_bill(params: BillDetailInput, ctx: Context) -> BillDetail:
        """Get full detail for a specific parliamentary bill.

        Returns sponsors, current stage, long title, summary, and Royal Assent date
        if enacted. Summary text is capped per max_summary_chars — check
        summary_truncated in the response to see if it was cut.

        Args:
            params: BillDetailInput with bill_id and optional max_summary_chars.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        resp = await client.get(f"{BILLS_BASE}/Bills/{params.bill_id}")
        resp.raise_for_status()
        return _parse_bill_detail(resp.json(), params.max_summary_chars)
