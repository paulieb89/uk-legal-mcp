"""Tools for the committees module.

Upstream API (public, no auth):
  - committees-api.parliament.uk — select committees, membership, evidence
"""

import asyncio
import json
from datetime import date
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

from ...deps import format_http_error
from .models import CommitteeDetail, CommitteeEvidencePage, CommitteeMember, CommitteeSearchResult, CommitteeSummary, EvidenceItem

COMMITTEES_BASE = "https://committees-api.parliament.uk/api"

HOUSE_MAP = {"Commons": 1, "Lords": 2, "Joint": 0}


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
    async def committees_search_committees(
        query: Annotated[str | None, Field(description="Search term for committee names, e.g. 'defence' or 'treasury'. Filtered client-side against committee names. Omit to list all committees.", max_length=300)] = None,
        house: Annotated[Literal["Commons", "Lords", "Joint"] | None, Field(description="Filter by house.")] = None,
        active_only: Annotated[bool, Field(description="If true, only return currently active committees.")] = True,
        limit: Annotated[int, Field(description="Maximum committees to return. Default 100 comfortably covers all currently-active UK select committees. Raise only for historical sweeps.", ge=1, le=500)] = 100,
        ctx: Context = None,
    ) -> CommitteeSearchResult:
        """USE THIS TOOL WHEN searching or listing UK parliamentary select committees by name, house, or active status.

        Returns committee summaries (name, house, active status, ID). AFTER
        calling, pass committee_id into committees_get_committee for current
        membership, or into committees_search_evidence to retrieve oral and
        written evidence submitted to that committee.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {"Take": limit}
        if active_only:
            qp["CommitteeStatus"] = "Current"
        if house:
            qp["House"] = HOUSE_MAP.get(house)

        resp = await client.get(f"{COMMITTEES_BASE}/Committees", params=qp)
        resp.raise_for_status()
        data = resp.json()

        items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
        if not isinstance(items, list):
            items = []

        committees: list[CommitteeSummary] = []
        for item in items:
            name = item.get("name", "Unknown")
            if query and query.lower() not in name.lower():
                continue
            cid = item.get("id", 0)
            committees.append(CommitteeSummary(
                id=cid,
                name=name,
                house=_parse_house(item.get("house")),
                is_active=True if active_only else None,
                url=f"https://committees.parliament.uk/committee/{cid}/",
            ))

        return CommitteeSearchResult(
            query=query,
            house=house,
            active_only=active_only,
            total=len(committees),
            committees=committees,
        )

    @mcp.tool(
        name="get_committee",
        annotations={"title": "Get Committee Detail", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def committees_get_committee(
        committee_id: Annotated[int, Field(description="Committee ID from committees_search_committees results.", ge=1)],
        ctx: Context = None,
    ) -> CommitteeDetail:
        """USE THIS TOOL WHEN you have a committee_id and want the metadata + current membership.

        Fetches committee detail and member list in parallel. AFTER calling,
        pass committee_id into committees_search_evidence to see what evidence
        has been submitted to this committee on what topics.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        detail_req = client.get(f"{COMMITTEES_BASE}/Committees/{committee_id}")
        members_req = client.get(f"{COMMITTEES_BASE}/Committees/{committee_id}/Members")

        detail_resp, members_resp = await asyncio.gather(detail_req, members_req)
        detail_resp.raise_for_status()
        members_resp.raise_for_status()

        detail_data = detail_resp.json()
        members_data = members_resp.json()

        member_items = members_data.get("items", members_data.get("results", members_data)) if isinstance(members_data, dict) else members_data
        if not isinstance(member_items, list):
            member_items = []

        members: list[CommitteeMember] = []
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

        cid = committee_id
        return CommitteeDetail(
            id=cid,
            name=detail_data.get("name", "Unknown"),
            house=_parse_house(detail_data.get("house")),
            phone=detail_data.get("phone"),
            email=detail_data.get("email"),
            url=f"https://committees.parliament.uk/committee/{cid}/",
            members=members,
        )

    @mcp.tool(
        name="search_evidence",
        annotations={"title": "Search Committee Evidence", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def committees_search_evidence(
        committee_id: Annotated[int, Field(description="Committee ID from committees_search_committees results.", ge=1)],
        evidence_type: Annotated[Literal["oral", "written", "both"], Field(description="Type of evidence to search.")] = "both",
        offset: Annotated[int, Field(description="Number of evidence items to skip before this page. Default 0. Re-call with offset=offset+returned while has_more is true.", ge=0, le=2000)] = 0,
        limit: Annotated[int, Field(description="Maximum evidence items to return. Default 20. When evidence_type='both' the limit is split across oral and written (roughly half each).", ge=1, le=100)] = 20,
        max_title_chars: Annotated[int, Field(description="Per-item cap on the free-text title field. Default 300 prevents context blow-up from verbose inquiry titles. Raise to 1000+ only when you need the full title text.", ge=50, le=2000)] = 300,
        ctx: Context = None,
    ) -> CommitteeEvidencePage:
        """USE THIS TOOL WHEN you have a committee_id and want the oral and written evidence submitted to it.

        Returns ONE PAGE of evidence (default 20). Free-text titles are capped
        per max_title_chars; witness lists are capped at 10 per item. For
        committees with many submissions, re-call with offset=offset+returned
        while has_more is true.

        Authoritative source for parliamentary committee evidence.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]

        def _cap_title(t: str) -> str:
            if len(t) > max_title_chars:
                return t[: max_title_chars] + " …[truncated]"
            return t

        async def fetch_oral(skip: int, take: int) -> tuple[list[EvidenceItem], int]:
            resp = await client.get(
                f"{COMMITTEES_BASE}/OralEvidence",
                params={"CommitteeId": committee_id, "Skip": skip, "Take": take},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return [], 0
            results: list[EvidenceItem] = []
            for item in items:
                ev_date = item.get("evidenceDate") or item.get("date")
                witnesses: list[str] = []
                for w in item.get("witnesses", []):
                    if isinstance(w, str):
                        witnesses.append(w)
                    elif isinstance(w, dict):
                        witnesses.append(w.get("name", str(w)))
                results.append(EvidenceItem(
                    id=item.get("id", 0),
                    type="oral",
                    title=_cap_title(item.get("title", item.get("sessionTitle", "Oral evidence session"))),
                    date=date.fromisoformat(ev_date[:10]) if ev_date else None,
                    witnesses=(witnesses[:10] or None),
                    url=item.get("url"),
                ))
            return results, len(items)

        async def fetch_written(skip: int, take: int) -> tuple[list[EvidenceItem], int]:
            resp = await client.get(
                f"{COMMITTEES_BASE}/WrittenEvidence",
                params={"CommitteeId": committee_id, "Skip": skip, "Take": take},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return [], 0
            results: list[EvidenceItem] = []
            for item in items:
                ev_date = item.get("dateReceived") or item.get("date")
                results.append(EvidenceItem(
                    id=item.get("id", 0),
                    type="written",
                    title=_cap_title(item.get("title", "Written evidence")),
                    date=date.fromisoformat(ev_date[:10]) if ev_date else None,
                    witnesses=None,
                    url=item.get("url"),
                ))
            return results, len(items)

        evidence: list[EvidenceItem] = []
        has_more = False

        if evidence_type == "oral":
            evidence, raw = await fetch_oral(offset, limit)
            has_more = raw == limit
        elif evidence_type == "written":
            evidence, raw = await fetch_written(offset, limit)
            has_more = raw == limit
        else:
            oral_take = (limit + 1) // 2  # remainder to oral
            written_take = limit // 2
            (oral, oral_raw), (written, written_raw) = await asyncio.gather(
                fetch_oral(offset, oral_take),
                fetch_written(offset, written_take),
            )
            evidence = oral + written
            has_more = (oral_raw == oral_take) or (written_raw == written_take)

        return CommitteeEvidencePage(
            committee_id=committee_id,
            evidence_type=evidence_type,
            offset=offset,
            limit=limit,
            returned=len(evidence),
            has_more=has_more,
            evidence=evidence,
        )
