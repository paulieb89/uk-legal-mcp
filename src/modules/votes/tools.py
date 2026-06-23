"""Tools for the votes module.

Upstream APIs (public, no auth):
  - commonsvotes-api.parliament.uk — Commons division records
  - lordsvotes-api.parliament.uk   — Lords division records
"""

import json
from datetime import date
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

from ...deps import format_http_error, raise_http_tool_error
from .models import DivisionDetail, DivisionSummary, DivisionsSearchResult, Voter

COMMONS_VOTES_BASE = "https://commonsvotes-api.parliament.uk"
LORDS_VOTES_BASE = "https://lordsvotes-api.parliament.uk"

MAX_VOTERS_PER_SIDE = 100


def _search_url(house: str) -> str:
    if house == "Lords":
        return f"{LORDS_VOTES_BASE}/data/Divisions/search"
    return f"{COMMONS_VOTES_BASE}/data/divisions.json/search"


def _detail_url(house: str, division_id: int) -> str:
    if house == "Lords":
        return f"{LORDS_VOTES_BASE}/data/Divisions/{division_id}"
    return f"{COMMONS_VOTES_BASE}/data/division/{division_id}.json"


def _parse_commons_summary(item: dict) -> DivisionSummary:
    return DivisionSummary(
        id=item.get("DivisionId", 0),
        title=item.get("Title", "Unknown"),
        date=date.fromisoformat(item.get("Date", "1970-01-01")[:10]),
        house="Commons",
        ayes=item.get("AyeCount", 0),
        noes=item.get("NoCount", 0),
        passed=item.get("AyeCount", 0) > item.get("NoCount", 0),
        is_government_win=None,
    )


def _parse_lords_summary(item: dict) -> DivisionSummary:
    ayes = item.get("authoritativeContentCount", item.get("AuthoritativeContentCount", 0))
    noes = item.get("authoritativeNotContentCount", item.get("AuthoritativeNotContentCount", 0))
    return DivisionSummary(
        id=item.get("divisionId", item.get("DivisionId", 0)),
        title=item.get("title", item.get("Title", "Unknown")),
        date=date.fromisoformat(item.get("date", item.get("Date", "1970-01-01"))[:10]),
        house="Lords",
        ayes=ayes,
        noes=noes,
        passed=ayes > noes,
        is_government_win=item.get("isGovernmentWin", item.get("IsGovernmentWin")),
    )


def _parse_voters(voter_list: list[dict]) -> list[Voter]:
    voters = []
    for v in voter_list:
        voters.append(Voter(
            member_id=v.get("MemberId", v.get("memberId", 0)),
            name=v.get("Name", v.get("name", "Unknown")),
            party=v.get("Party", v.get("party")),
        ))
    return voters


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_divisions",
        annotations={"title": "Search Parliamentary Divisions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def votes_search_divisions(
        query: Annotated[str | None, Field(description="Search term for division titles, e.g. 'Rwanda' or 'Online Safety Bill'. Omit to browse recent divisions.", max_length=500)] = None,
        house: Annotated[Literal["Commons", "Lords"], Field(description="Which house to search.")] = "Commons",
        from_date: Annotated[date | None, Field(description="Start date (YYYY-MM-DD).")] = None,
        to_date: Annotated[date | None, Field(description="End date (YYYY-MM-DD).")] = None,
        member_id: Annotated[int | None, Field(description="Filter to divisions where this member voted. Get the member ID from parliament_find_member.", ge=1)] = None,
        offset: Annotated[int, Field(description="Number of divisions to skip before this page. Default 0. Re-call with offset=offset+returned while has_more is true.", ge=0, le=2000)] = 0,
        limit: Annotated[int, Field(description="Maximum divisions to return. Default 25 (Commons API max-per-page).", ge=1, le=100)] = 25,
        *,
        ctx: Context,
    ) -> DivisionsSearchResult:
        """USE THIS TOOL WHEN searching Commons or Lords formal votes by topic, date, or member.

        Returns division summaries (title, date, vote counts, pass/fail). AFTER
        calling, pass division_id + house into votes_get_division for the full
        member-by-member voter lists.

        Authoritative source for UK parliamentary vote records.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        url = _search_url(house)
        qp: dict = {
            "queryParameters.take": limit,
            "queryParameters.skip": offset,
        }

        if query:
            qp["queryParameters.searchTerm"] = query
        if from_date:
            qp["queryParameters.startDate"] = from_date.isoformat()
        if to_date:
            qp["queryParameters.endDate"] = to_date.isoformat()
        if member_id:
            qp["queryParameters.memberId"] = member_id

        try:
            resp = await client.get(url, params=qp)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"votes_search_divisions(query={query!r}, house={house!r})")
        data = resp.json()

        items = data if isinstance(data, list) else data.get("results", data.get("items", []))

        if house == "Lords":
            divisions = [_parse_lords_summary(item) for item in items]
        else:
            divisions = [_parse_commons_summary(item) for item in items]

        return DivisionsSearchResult(
            query=query,
            house=house,
            offset=offset,
            limit=limit,
            total=len(divisions),
            has_more=len(divisions) == limit,
            divisions=divisions,
        )

    @mcp.tool(
        name="get_division",
        annotations={"title": "Get Division Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def votes_get_division(
        division_id: Annotated[int, Field(description="Division ID from votes_search_divisions results.", ge=1)],
        house: Annotated[Literal["Commons", "Lords"], Field(description="Which house this division belongs to.")] = "Commons",
        *,
        ctx: Context,
    ) -> DivisionDetail:
        """USE THIS TOOL WHEN you have a division_id + house and want the full member-by-member voting record.

        Voter lists are truncated to 100 per side to fit response limits; total
        voter counts are always accurate regardless of truncation. Chain from
        votes_search_divisions or parliament_get_debate_divisions (which
        cross-resolves Hansard division refs into votes-API division_ids).
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        url = _detail_url(house, division_id)

        try:
            resp = await client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"votes_get_division(division_id={division_id}, house={house!r})")
        data = resp.json()

        if house == "Lords":
            title = data.get("title", data.get("Title", "Unknown"))
            div_date = date.fromisoformat(data.get("date", data.get("Date", "1970-01-01"))[:10])
            aye_list = data.get("contents", data.get("Contents", []))
            noe_list = data.get("notContents", data.get("NotContents", []))
            is_gov_win = data.get("isGovernmentWin", data.get("IsGovernmentWin"))
        else:
            title = data.get("Title", "Unknown")
            div_date = date.fromisoformat(data.get("Date", "1970-01-01")[:10])
            aye_list = data.get("Ayes", [])
            noe_list = data.get("Noes", [])
            is_gov_win = None

        all_ayes = _parse_voters(aye_list)
        all_noes = _parse_voters(noe_list)

        truncated = len(all_ayes) > MAX_VOTERS_PER_SIDE or len(all_noes) > MAX_VOTERS_PER_SIDE

        return DivisionDetail(
            id=division_id,
            title=title,
            date=div_date,
            house=house,
            ayes_count=len(all_ayes),
            noes_count=len(all_noes),
            passed=len(all_ayes) > len(all_noes),
            is_government_win=is_gov_win,
            aye_voters=all_ayes[:MAX_VOTERS_PER_SIDE],
            noe_voters=all_noes[:MAX_VOTERS_PER_SIDE],
            truncated=truncated,
            total_aye_voters=len(all_ayes),
            total_noe_voters=len(all_noes),
        )
