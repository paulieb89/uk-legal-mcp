"""Tools for the votes module.

Upstream APIs (public, no auth):
  - commonsvotes-api.parliament.uk — Commons division records
  - lordsvotes-api.parliament.uk   — Lords division records
"""

import asyncio
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

# The Commons search endpoint silently returns at most 25 rows whatever `take`
# asks for (verified live 2026-09-13: take=100/101/500 all return 25). The
# Lords endpoint honours `take` (verified up to 500).
COMMONS_MAX_TAKE = 25

# Both APIs' Swagger declares `skip` as int32. There is no smaller paging
# boundary upstream: Lords skip=2000/2500/2977 return the expected continuation
# of a 2,978-row result and skip past the end returns [] (verified live
# 2026-09-13), so offset is bounded only by the declared type.
UPSTREAM_SKIP_MAX = 2**31 - 1


def _search_url(house: str) -> str:
    if house == "Lords":
        return f"{LORDS_VOTES_BASE}/data/Divisions/search"
    return f"{COMMONS_VOTES_BASE}/data/divisions.json/search"


def _search_total_url(house: str) -> str:
    if house == "Lords":
        return f"{LORDS_VOTES_BASE}/data/Divisions/searchTotalResults"
    return f"{COMMONS_VOTES_BASE}/data/divisions.json/searchTotalResults"


def _search_filters(
    house: str, query: str | None, from_date: date | None, to_date: date | None, member_id: int | None,
) -> dict:
    """Filter params shared by an API's search and searchTotalResults endpoints.

    The two APIs name them differently (per each API's Swagger): Commons uses
    `queryParameters.*`, Lords uses bare PascalCase names.
    """
    if house == "Lords":
        names = {"query": "SearchTerm", "from": "StartDate", "to": "EndDate", "member": "MemberId"}
    else:
        names = {
            "query": "queryParameters.searchTerm", "from": "queryParameters.startDate",
            "to": "queryParameters.endDate", "member": "queryParameters.memberId",
        }
    qp: dict = {}
    if query:
        qp[names["query"]] = query
    if from_date:
        qp[names["from"]] = from_date.isoformat()
    if to_date:
        qp[names["to"]] = to_date.isoformat()
    if member_id:
        qp[names["member"]] = member_id
    return qp


def _search_paging(house: str, offset: int, limit: int) -> dict:
    if house == "Lords":
        return {"skip": offset, "take": limit}
    return {"queryParameters.skip": offset, "queryParameters.take": min(limit, COMMONS_MAX_TAKE)}


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
        offset: Annotated[int, Field(description="Number of divisions to skip before this page. Default 0. Re-call with offset=offset+returned while has_more is true.", ge=0, le=UPSTREAM_SKIP_MAX)] = 0,
        limit: Annotated[int, Field(description="Maximum divisions to return. Default 25. The Lords API honours up to 100; the Commons API returns at most 25 per call, so page by `returned`, not `limit`.", ge=1, le=100)] = 25,
        *,
        ctx: Context,
    ) -> DivisionsSearchResult:
        """USE THIS TOOL WHEN searching Commons or Lords formal votes by topic, date, or member.

        Returns one page of division summaries (title, date, vote counts,
        pass/fail) plus `total`, the source's count of all matching divisions.
        While `has_more` is true, re-call with offset=offset+returned. AFTER
        calling, pass division_id + house into votes_get_division for the full
        member-by-member voter lists.

        Authoritative source for UK parliamentary vote records.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        filters = _search_filters(house, query, from_date, to_date, member_id)

        try:
            resp, total_resp = await asyncio.gather(
                client.get(_search_url(house), params={**filters, **_search_paging(house, offset, limit)}),
                client.get(_search_total_url(house), params=filters),
            )
            resp.raise_for_status()
            total_resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"votes_search_divisions(query={query!r}, house={house!r})")
        data = resp.json()
        total = total_resp.json()

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
            returned=len(divisions),
            total=total,
            has_more=offset + len(divisions) < total,
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
