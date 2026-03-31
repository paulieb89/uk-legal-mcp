"""Tools for the committees module.

Upstream API (public, no auth):
  - committees-api.parliament.uk — select committees, membership, evidence
"""

import asyncio
import json
from datetime import date
from typing import Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import CommitteeDetail, CommitteeMember, CommitteeSummary, EvidenceItem

COMMITTEES_BASE = "https://committees-api.parliament.uk/api"

HOUSE_MAP = {"Commons": 1, "Lords": 2, "Joint": 0}


class CommitteeSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str | None = Field(None, description=(
        "Search term for committee names, e.g. 'defence' or 'treasury'. "
        "Filtered client-side against committee names. Omit to list all committees."
    ), max_length=300)
    house: Literal["Commons", "Lords", "Joint"] | None = Field(None, description="Filter by house.")
    active_only: bool = Field(True, description="If true, only return currently active committees.")


class CommitteeDetailInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    committee_id: int = Field(..., description="Committee ID from committees_search_committees results.", ge=1)


class CommitteeEvidenceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    committee_id: int = Field(..., description="Committee ID from committees_search_committees results.", ge=1)
    evidence_type: Literal["oral", "written", "both"] = Field("both", description="Type of evidence to search.")


def _parse_house(house_val) -> str | None:
    if isinstance(house_val, int):
        return {1: "Commons", 2: "Lords", 0: "Joint"}.get(house_val)
    if isinstance(house_val, str):
        return house_val
    if isinstance(house_val, dict):
        return house_val.get("name")
    return None


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_committees",
        annotations={"title": "Search Parliamentary Committees", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def committees_search_committees(params: CommitteeSearchInput, ctx: Context) -> str:
        """Search or list UK parliamentary select committees.

        Returns committee names, house, and active status.
        Use committees_get_committee with the committee ID for membership detail.

        Args:
            params (CommitteeSearchInput): optional query, house, active_only filters.

        Returns:
            str: JSON array of CommitteeSummary objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"Take": 200}
            if params.active_only:
                qp["CommitteeStatus"] = "Current"
            if params.house:
                qp["House"] = HOUSE_MAP.get(params.house)

            resp = await client.get(f"{COMMITTEES_BASE}/Committees", params=qp)
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
            if not isinstance(items, list):
                items = []

            committees = []
            for item in items:
                name = item.get("name", "Unknown")
                if params.query and params.query.lower() not in name.lower():
                    continue
                cid = item.get("id", 0)
                committees.append(CommitteeSummary(
                    id=cid,
                    name=name,
                    house=_parse_house(item.get("house")),
                    is_active=True if params.active_only else None,
                    url=f"https://committees.parliament.uk/committee/{cid}/",
                ))

            return json.dumps([c.model_dump() for c in committees], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="get_committee",
        annotations={"title": "Get Committee Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def committees_get_committee(params: CommitteeDetailInput, ctx: Context) -> str:
        """Get detail for a parliamentary committee including current membership.

        Fetches committee metadata and member list in parallel.

        Args:
            params (CommitteeDetailInput): committee_id from committees_search_committees.

        Returns:
            str: JSON CommitteeDetail with members list.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            detail_req = client.get(f"{COMMITTEES_BASE}/Committees/{params.committee_id}")
            members_req = client.get(f"{COMMITTEES_BASE}/Committees/{params.committee_id}/Members")

            detail_resp, members_resp = await asyncio.gather(detail_req, members_req)
            detail_resp.raise_for_status()
            members_resp.raise_for_status()

            detail_data = detail_resp.json()
            members_data = members_resp.json()

            members = []
            member_items = members_data.get("items", members_data.get("results", members_data)) if isinstance(members_data, dict) else members_data
            if not isinstance(member_items, list):
                member_items = []

            for m in member_items:
                member_info = m.get("memberInfo", {})
                roles = m.get("roles", [])
                role_name = None
                if roles:
                    role_obj = roles[0].get("role", {})
                    if isinstance(role_obj, dict):
                        role_name = role_obj.get("name")
                        if role_obj.get("isChair"):
                            role_name = "Chair"
                members.append(CommitteeMember(
                    name=m.get("name", "Unknown"),
                    party=member_info.get("party") if isinstance(member_info, dict) else None,
                    role=role_name,
                ))

            cid = params.committee_id
            result = CommitteeDetail(
                id=cid,
                name=detail_data.get("name", "Unknown"),
                house=_parse_house(detail_data.get("house")),
                phone=detail_data.get("phone"),
                email=detail_data.get("email"),
                url=f"https://committees.parliament.uk/committee/{cid}/",
                members=members,
            )
            return result.model_dump_json(indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="search_evidence",
        annotations={"title": "Search Committee Evidence", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def committees_search_evidence(params: CommitteeEvidenceInput, ctx: Context) -> str:
        """Search oral and written evidence submitted to a parliamentary committee.

        Returns evidence titles, dates, and witness names (for oral evidence).

        Args:
            params (CommitteeEvidenceInput): committee_id and evidence_type filter.

        Returns:
            str: JSON array of EvidenceItem objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            evidence: list[EvidenceItem] = []

            async def fetch_oral():
                resp = await client.get(
                    f"{COMMITTEES_BASE}/OralEvidence",
                    params={"CommitteeId": params.committee_id, "Take": 20},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
                if not isinstance(items, list):
                    return []
                results = []
                for item in items:
                    ev_date = item.get("evidenceDate") or item.get("date")
                    witnesses = []
                    for w in item.get("witnesses", []):
                        if isinstance(w, str):
                            witnesses.append(w)
                        elif isinstance(w, dict):
                            witnesses.append(w.get("name", str(w)))
                    results.append(EvidenceItem(
                        id=item.get("id", 0),
                        type="oral",
                        title=item.get("title", item.get("sessionTitle", "Oral evidence session")),
                        date=date.fromisoformat(ev_date[:10]) if ev_date else None,
                        witnesses=witnesses or None,
                        url=item.get("url"),
                    ))
                return results

            async def fetch_written():
                resp = await client.get(
                    f"{COMMITTEES_BASE}/WrittenEvidence",
                    params={"CommitteeId": params.committee_id, "Take": 20},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
                if not isinstance(items, list):
                    return []
                results = []
                for item in items:
                    ev_date = item.get("dateReceived") or item.get("date")
                    results.append(EvidenceItem(
                        id=item.get("id", 0),
                        type="written",
                        title=item.get("title", "Written evidence"),
                        date=date.fromisoformat(ev_date[:10]) if ev_date else None,
                        witnesses=None,
                        url=item.get("url"),
                    ))
                return results

            if params.evidence_type == "oral":
                evidence = await fetch_oral()
            elif params.evidence_type == "written":
                evidence = await fetch_written()
            else:
                oral, written = await asyncio.gather(fetch_oral(), fetch_written())
                evidence = oral + written

            return json.dumps([e.model_dump(mode="json") for e in evidence], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})
