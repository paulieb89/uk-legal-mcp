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

from ...deps import format_http_error, raise_http_tool_error
from .models import CommitteeDetail, CommitteeEvidencePage, CommitteeMember, CommitteeSearchResult, CommitteeSummary, EvidenceItem

COMMITTEES_BASE = "https://committees-api.parliament.uk/api"

HOUSE_MAP = {"Commons": 1, "Lords": 2, "Joint": 0}


def _witness_display_name(w: dict) -> str | None:
    """Derive a witness's display identity, preferring their personal name.

    committees-api.parliament.uk's witness object carries `name` for an
    individual submitter, but `name: null` for an Organisation submitter —
    there is no person to name. This was the confirmed source-fidelity
    crash: the old parser read `w.get("name", str(w))`, which only
    substitutes the fallback when the key is ABSENT, not when it's present
    with value None, so an Organisation witness's null `name` reached
    `EvidenceItem.witnesses: list[str]` directly and failed Pydantic
    validation.

    `organisations[0]` cardinality — verified live (2026-09) across 228
    real witnesses from three committees (Treasury 158, Justice 102, Home
    Affairs 83): the swagger schema declares `organisations` as an
    unbounded `IEnumerable<Organisation>`, and it genuinely is — 4 witnesses
    carried 2 or 3 entries. But every one of those was an Individual witness
    with a populated `name` (the array there lists that PERSON's several
    professional affiliations, e.g. "Swansea University" + "Vox Pol
    Institute" for one academic), so `name` wins before `organisations` is
    ever consulted. On the branch this function actually reaches —
    Organisation-type, `name` null, no person to name — cardinality was
    exactly 1 in all 51 instances observed, 0 counterexamples. `[0]` is
    therefore reading "the identifying organisation" (there is only ever
    one when it matters), not silently discarding a second one.

    Returns None (no fabricated string) if neither a personal name nor an
    organisation entry is available — not observed live, but the schema
    doesn't rule it out, so this stays honest rather than guessing.
    """
    name = w.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    organisations = w.get("organisations")
    if isinstance(organisations, list) and organisations and isinstance(organisations[0], dict):
        org_name = organisations[0].get("name")
        role = organisations[0].get("role")
        if org_name and role:
            return f"{org_name} ({role})"
        if org_name:
            return org_name
    return None


def _parse_witnesses(raw_witnesses) -> list[str | None]:
    """Map a raw oral-evidence `witnesses` array to display names.

    A string entry is used as-is (defensive — not observed live, the real
    shape is always objects, but cheap to keep honest). A dict entry is
    resolved via _witness_display_name, which can legitimately return None.
    Anything else is skipped, matching the pre-existing behaviour for
    malformed entries.
    """
    witnesses: list[str | None] = []
    for w in raw_witnesses or []:
        if isinstance(w, str):
            witnesses.append(w)
        elif isinstance(w, dict):
            witnesses.append(_witness_display_name(w))
    return witnesses


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
        *,
        ctx: Context,
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

        try:
            resp = await client.get(f"{COMMITTEES_BASE}/Committees", params=qp)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"committees_search_committees(query={query!r})")
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
        *,
        ctx: Context,
    ) -> CommitteeDetail:
        """USE THIS TOOL WHEN you have a committee_id and want the metadata + current membership.

        Fetches committee detail and member list in parallel. AFTER calling,
        pass committee_id into committees_search_evidence to see what evidence
        has been submitted to this committee on what topics.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        detail_req = client.get(f"{COMMITTEES_BASE}/Committees/{committee_id}")
        members_req = client.get(f"{COMMITTEES_BASE}/Committees/{committee_id}/Members")

        try:
            detail_resp, members_resp = await asyncio.gather(detail_req, members_req)
            detail_resp.raise_for_status()
            members_resp.raise_for_status()
        except httpx.HTTPError as e:
            raise_http_tool_error(e, attempted=f"committees_get_committee(committee_id={committee_id})")

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
        *,
        ctx: Context,
    ) -> CommitteeEvidencePage:
        """USE THIS TOOL WHEN you have a committee_id and want the oral and written evidence submitted to it.

        Returns ONE PAGE of evidence (default 20). Free-text titles are capped
        per max_title_chars; witness lists are capped at 10 per item. An
        organisation witness (no named individual) is rendered as
        '<Organisation> (<Role>)'; a null entry means the source gave no
        name for that witness at all. For committees with many submissions,
        re-call with offset=offset+returned while has_more is true.

        Authoritative source for parliamentary committee evidence.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]

        def _cap_title(t: str) -> str:
            if len(t) > max_title_chars:
                return t[: max_title_chars] + " …[truncated]"
            return t

        async def fetch_oral(skip: int, take: int) -> tuple[list[EvidenceItem], int]:
            try:
                resp = await client.get(
                    f"{COMMITTEES_BASE}/OralEvidence",
                    params={"CommitteeId": committee_id, "Skip": skip, "Take": take},
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise_http_tool_error(e, attempted=f"committees_search_evidence(committee_id={committee_id}, evidence_type='oral')")
            data = resp.json()
            items = data.get("items", data.get("results", data)) if isinstance(data, dict) else data
            if not isinstance(items, list):
                return [], 0
            results: list[EvidenceItem] = []
            for item in items:
                ev_date = item.get("evidenceDate") or item.get("date")
                witnesses = _parse_witnesses(item.get("witnesses", []))
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
            try:
                resp = await client.get(
                    f"{COMMITTEES_BASE}/WrittenEvidence",
                    params={"CommitteeId": committee_id, "Skip": skip, "Take": take},
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                raise_http_tool_error(e, attempted=f"committees_search_evidence(committee_id={committee_id}, evidence_type='written')")
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
