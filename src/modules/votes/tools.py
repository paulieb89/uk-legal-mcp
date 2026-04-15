"""Tools for the votes module.

Upstream APIs (public, no auth):
  - commonsvotes-api.parliament.uk — Commons division records
  - lordsvotes-api.parliament.uk   — Lords division records
"""

import json
from datetime import date
from typing import Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import DivisionDetail, DivisionSummary, Voter

COMMONS_VOTES_BASE = "https://commonsvotes-api.parliament.uk"
LORDS_VOTES_BASE = "https://lordsvotes-api.parliament.uk"

MAX_VOTERS_PER_SIDE = 100


class DivisionSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str | None = Field(None, description=(
        "Search term for division titles, e.g. 'Rwanda' or 'Online Safety Bill'. "
        "Omit to browse recent divisions."
    ), max_length=500)
    house: Literal["Commons", "Lords"] = Field("Commons", description="Which house to search.")
    from_date: date | None = Field(None, description="Start date (YYYY-MM-DD).")
    to_date: date | None = Field(None, description="End date (YYYY-MM-DD).")
    member_id: int | None = Field(None, description=(
        "Filter to divisions where this member voted. "
        "Get the member ID from parliament_find_member."
    ), ge=1)


class DivisionDetailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    division_id: int = Field(..., description="Division ID from votes_search_divisions results.", ge=1)
    house: Literal["Commons", "Lords"] = Field("Commons", description="Which house this division belongs to.")


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
        is_government_win=item.get("isGovernmentContent", item.get("IsGovernmentContent")),
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
    async def votes_search_divisions(params: DivisionSearchInput, ctx: Context) -> str:
        """Search parliamentary divisions (votes) in the Commons or Lords.

        Returns division summaries including title, date, vote counts, and whether the motion passed.
        Use votes_get_division with the division ID for full voter lists.

        Args:
            params (DivisionSearchInput): optional query, house, date range, member filter.

        Returns:
            str: JSON array of DivisionSummary objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            url = _search_url(params.house)
            qp: dict = {"queryParameters.take": 25}

            if params.query:
                qp["queryParameters.searchTerm"] = params.query
            if params.from_date:
                qp["queryParameters.startDate"] = params.from_date.isoformat()
            if params.to_date:
                qp["queryParameters.endDate"] = params.to_date.isoformat()
            if params.member_id:
                qp["queryParameters.memberId"] = params.member_id

            resp = await client.get(url, params=qp)
            resp.raise_for_status()
            data = resp.json()

            items = data if isinstance(data, list) else data.get("results", data.get("items", []))

            if params.house == "Lords":
                divisions = [_parse_lords_summary(item) for item in items]
            else:
                divisions = [_parse_commons_summary(item) for item in items]

            return json.dumps([d.model_dump(mode="json") for d in divisions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="get_division",
        annotations={"title": "Get Division Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def votes_get_division(params: DivisionDetailInput, ctx: Context) -> DivisionDetail:
        """Get full detail for a parliamentary division including how each member voted.

        Voter lists are truncated to 100 per side to fit response limits.
        Total voter counts are always accurate regardless of truncation.

        Args:
            params: DivisionDetailInput with division_id and house.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        url = _detail_url(params.house, params.division_id)

        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        if params.house == "Lords":
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
            id=params.division_id,
            title=title,
            date=div_date,
            house=params.house,
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
