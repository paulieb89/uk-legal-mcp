"""Tools for the bills module.

Upstream API (public, no auth):
  - bills-api.parliament.uk — bill search, detail, stages, sponsors
"""

import json
from datetime import date
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

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
        stage_name = current_stage_raw.get("description") or current_stage_raw.get("stageName")
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
    async def bills_search_bills(
        query: Annotated[str, Field(description="Search term for bill titles and descriptions, e.g. 'online safety' or 'financial services'.", min_length=1, max_length=500)],
        session: Annotated[int | None, Field(description="Numeric parliamentary session ID (e.g. 40 = 2024-25, 39 = 2023-24). NOT a year string like '2025'. If you only know the year, omit this and filter the results instead. Omit to search all sessions.", ge=1)] = None,
        house: Annotated[Literal["Commons", "Lords", "All"] | None, Field(description="Filter by originating house. Omit for all houses.")] = None,
        stage: Annotated[Literal["firstreading", "secondreading", "committee", "report", "thirdreading", "royalassent"] | None, Field(description="Filter by current legislative stage.")] = None,
        offset: Annotated[int, Field(description="Number of results to skip before this page. Default 0 for the first page. Re-call with offset=offset+returned while has_more is true to paginate.", ge=0, le=2000)] = 0,
        limit: Annotated[int, Field(description="Maximum bills to return in this call. Default 20 keeps responses focused; raise up to 100 for bulk exports.", ge=1, le=100)] = 20,
        ctx: Context = None,
    ) -> BillSearchResult:
        """USE THIS TOOL WHEN searching UK parliamentary bills by keyword, session, house, or legislative stage.

        Returns a paginated page of bill summaries (title, current stage, whether
        it became an Act). AFTER calling, pass a bill_id into bills_get_bill for
        full detail (sponsors, long title, Royal Assent date).

        Authoritative source for UK parliamentary bill status.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "SearchTerm": query,
            "Take": limit,
            "Skip": offset,
        }
        if session is not None:
            qp["Session"] = session
        if house and house != "All":
            qp["CurrentHouse"] = HOUSE_MAP.get(house)
        if stage:
            qp["BillStage"] = STAGE_ID_MAP[stage]

        resp = await client.get(f"{BILLS_BASE}/Bills", params=qp)
        resp.raise_for_status()
        data = resp.json()

        bills = [_parse_bill_summary(item) for item in data.get("items", [])]
        total = data.get("totalResults")
        if not isinstance(total, int):
            total = None
        has_more = (
            (offset + len(bills)) < total
            if total is not None
            else len(bills) == limit
        )

        return BillSearchResult(
            query=query,
            offset=offset,
            limit=limit,
            returned=len(bills),
            total=total,
            has_more=has_more,
            bills=bills,
        )

    @mcp.tool(
        name="get_bill",
        annotations={"title": "Get Bill Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def bills_get_bill(
        bill_id: Annotated[int, Field(description="Bill ID from bills_search_bills results.", ge=1)],
        max_summary_chars: Annotated[int, Field(description="Maximum characters of the bill summary text to return. Default 5,000 (~1,250 tokens) covers most bills. Raise for substantive government bills (Finance Act, Levelling-up) whose summary runs longer. Check summary_truncated in the response to see if it was cut.", ge=500, le=50000)] = 5000,
        ctx: Context = None,
    ) -> BillDetail:
        """USE THIS TOOL WHEN you have a bill_id (from bills_search_bills) and want the full detail.

        Returns sponsors, current stage, long title, summary, and Royal Assent
        date if enacted. Summary text is capped per max_summary_chars — check
        summary_truncated in the response.

        AFTER calling, use parliament_search_hansard(query=bill_short_title) to
        find the bill's parliamentary debates, or bills_search_bills with a
        related keyword for adjacent bills.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        resp = await client.get(f"{BILLS_BASE}/Bills/{bill_id}")
        resp.raise_for_status()
        return _parse_bill_detail(resp.json(), max_summary_chars)
