"""
Tools for the parliament module.

Upstream APIs (all public, no auth required):
  - hansard-api.parliament.uk  — debate contributions (search.json)
  - questions-statements-api.parliament.uk — written questions & answers
  - members-api.parliament.uk — MPs and Lords lookup
"""

import re
from datetime import date
from typing import Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from collections import Counter

from ...deps import format_http_error
from .models import (
    ColumnLookupResult,
    DebateDivisions,
    DivisionMatchLite,
    FacetCount,
    GetDebateDivisionsInput,
    HansardContribution,
    HansardSearchResult,
    Interest,
    LookupByColumnInput,
    MemberDebatesResult,
    MemberInterestsPage,
    MemberResult,
    MemberSearchResult,
    PetitionSearchResult,
    PetitionSummary,
    PolicyPositionSummary,
    TopContributor,
    TopDebate,
)

HANSARD_API = "https://hansard-api.parliament.uk"
QS_BASE = "https://questions-statements-api.parliament.uk/api"
MEMBERS_BASE = "https://members-api.parliament.uk/api"
PETITIONS_BASE = "https://petition.parliament.uk"
INTERESTS_BASE = "https://interests-api.parliament.uk/api/v1"

INTEREST_CATEGORIES: dict[str, int] = {
    "employment": 12,
    "employment_adhoc": 1,
    "employment_ongoing": 2,
    "donations": 3,
    "gifts_uk": 4,
    "overseas_visits": 5,
    "gifts_overseas": 6,
    "land": 7,
    "shareholdings": 8,
    "miscellaneous": 9,
    "family_employed": 10,
    "family_lobbying": 11,
}

class HansardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description=(
        "Full phrase to search in Hansard debates. Pass the complete topic, "
        "e.g. 'short selling regulation' or 'artificial intelligence liability'. "
        "Searched as an exact phrase — do not truncate to a single keyword."
    ), min_length=1, max_length=500)
    from_date: date | None = Field(None, description="Start date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="End date (YYYY-MM-DD)")
    house: Literal["Commons", "Lords", "both"] = Field("both", description=(
        "Restrict to one House. Default 'both' returns Commons + Lords contributions."
    ))
    member: str | None = Field(None, description="Filter by member name")
    text_mode: Literal["preview", "full"] = Field("preview", description=(
        "'preview' returns the upstream ~250-char snippet (fast, low context cost). "
        "'full' returns ContributionTextFull (still capped at 3000 chars). "
        "For full contribution text without the cap, read the resource "
        "hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}."
    ))
    offset: int = Field(0, ge=0, le=2000, description=(
        "Number of contributions to skip before this page. Default 0. "
        "Re-call with offset=offset+returned while has_more is true to paginate."
    ))
    limit: int = Field(20, ge=1, le=100, description=(
        "Maximum contributions to return in this call. Default 20 keeps "
        "responses focused; raise to 100 for a bulk sweep."
    ))


class PolicyPositionSummaryInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    topic: str = Field(..., description=(
        "Phrase to search in Hansard, e.g. 'short selling regulation'. "
        "Searched as an exact phrase. For broader recall, drop quotes by "
        "shortening the topic."
    ), min_length=2, max_length=200)
    from_date: date | None = Field(None, description="Start date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="End date (YYYY-MM-DD)")
    house: Literal["Commons", "Lords", "both"] = Field("both", description="Restrict to one House. Default 'both'.")
    max_debates_scanned: int = Field(200, ge=50, le=2000, description=(
        "Hard cap on debates sampled from /search/Debates.json to compute "
        "facets. Default 200 issues ≤4 upstream calls (take=50 each). Raise "
        "to 2000 (≤40 calls) for an exhaustive sweep on a heavily-debated "
        "topic. Hansard rate limit: 1000 req/5min."
    ))


class FindMemberInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    name: str = Field(..., description="Name or partial name, e.g. 'Starmer', 'Baroness Hale'", min_length=2, max_length=200)


class MemberDebatesInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member_id: int = Field(..., description="Parliament Members API integer ID. Obtain from parliament_find_member.", ge=1)
    topic: str | None = Field(None, description=(
        "Optional phrase to filter this member's contributions by topic, "
        "e.g. 'housing benefit' or 'net zero'. Searched as an exact phrase."
    ))
    offset: int = Field(0, ge=0, le=2000, description=(
        "Number of contributions to skip before this page. Default 0. "
        "Re-call with offset=offset+returned while has_more is true."
    ))
    limit: int = Field(20, ge=1, le=100, description=(
        "Maximum contributions to return. Default 20."
    ))


class PetitionSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description=(
        "Search term for petition titles, e.g. 'ban trophy hunting' or 'NHS funding'."
    ), min_length=2, max_length=300)
    state: Literal["open", "closed", "all"] = Field("all", description="Filter by petition state.")
    offset: int = Field(0, ge=0, le=2000, description=(
        "Number of petitions to skip before this page. Default 0. "
        "Re-call with offset=offset+returned while has_more is true."
    ))
    limit: int = Field(20, ge=1, le=100, description=(
        "Maximum petitions to return. Default 20."
    ))


class MemberInterestsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member_id: int = Field(..., description=(
        "Parliament Members API integer ID. Get from parliament_find_member."
    ), ge=1)
    category: Literal[
        "employment", "employment_adhoc", "employment_ongoing",
        "donations", "gifts_uk", "overseas_visits", "gifts_overseas",
        "land", "shareholdings", "miscellaneous", "family_employed", "family_lobbying",
    ] | None = Field(None, description=(
        "Filter by interest category. Common categories: "
        "'donations' (donations and support), 'gifts_uk' (gifts/hospitality from UK), "
        "'employment' (employment and earnings), 'land' (land and property), "
        "'shareholdings', 'overseas_visits'. Omit for all categories."
    ))
    offset: int = Field(
        0,
        ge=0,
        le=500,
        description=(
            "Number of interests to skip before this page. Default 0 for "
            "the first page. To paginate prolific members (100+ interests), "
            "re-call with offset=offset+returned while the previous response "
            "had has_more=true."
        ),
    )
    limit: int = Field(
        20,
        ge=1,
        le=100,
        description=(
            "Maximum interests to return in this call. Default 20 keeps "
            "responses focused; raise to 50 or 100 only when you need a "
            "bulk view and have context headroom to spend."
        ),
    )
    max_description_chars: int = Field(
        500,
        ge=50,
        le=5000,
        description=(
            "Per-entry cap on the free-text description field. Default 500 "
            "prevents context blow-up on members with lengthy donation or "
            "directorship narratives. Raise to 2000+ only for forensic "
            "provenance work."
        ),
    )


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", clean).strip()


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(title: str) -> str:
    return _SLUG_RE.sub("-", title.strip().lower()).strip("-") or "debate"


def _hansard_contribution_url(house: str, sitting_date: date, debate_ext_id: str, contribution_ext_id: str, debate_title: str) -> str:
    """Synthesise the public hansard.parliament.uk URL for a contribution.

    Matches the website's canonical pattern:
        https://hansard.parliament.uk/{house}/{date}/debates/{debate_ext_id}/{slug}#contribution-{contribution_ext_id}
    """
    house_seg = house.lower() if house in ("Commons", "Lords") else "commons"
    return (
        f"https://hansard.parliament.uk/{house_seg}/{sitting_date.isoformat()}"
        f"/debates/{debate_ext_id}/{_slugify(debate_title)}"
        f"#contribution-{contribution_ext_id}"
    )


def _parse_hansard_contributions(data: dict, text_mode: Literal["preview", "full"] = "preview", max_text_chars: int = 3000) -> list[HansardContribution]:
    """Parse hansard-api.parliament.uk search.json response.

    Reads citation-grade metadata from each upstream `Contributions[i]` entry.
    Skips rows where required identifiers are missing so the schema stays sound.
    """
    contributions: list[HansardContribution] = []
    for item in data.get("Contributions", []):
        try:
            attr = item.get("AttributedTo") or item.get("MemberName") or ""
            name = item.get("MemberName") or "Unknown"
            party = None
            if "(" in attr and ")" in attr:
                party = attr[attr.rfind("(") + 1:attr.rfind(")")]

            raw_text_field = "ContributionTextFull" if text_mode == "full" else "ContributionText"
            text = _strip_html(item.get(raw_text_field) or item.get("ContributionText") or "")

            sitting_iso = (item.get("SittingDate") or "1970-01-01")[:10]
            sitting_date = date.fromisoformat(sitting_iso)
            house = item.get("House") or "Commons"
            debate_ext_id = item.get("DebateSectionExtId") or ""
            contribution_ext_id = item.get("ContributionExtId") or ""
            debate_title = (item.get("DebateSection") or item.get("DebateSectionName") or "Unknown").strip() or "Unknown"

            url = _hansard_contribution_url(house, sitting_date, debate_ext_id, contribution_ext_id, debate_title) if debate_ext_id and contribution_ext_id else ""

            contributions.append(HansardContribution(
                member_name=name,
                member_id=item.get("MemberId"),
                attributed_to=attr or name,
                party=party,
                constituency=None,
                date=sitting_date,
                debate_title=debate_title,
                debate_id=int(item.get("DebateSectionId") or 0),
                debate_ext_id=debate_ext_id,
                contribution_ext_id=contribution_ext_id,
                column_ref=item.get("HansardSection") or None,
                chamber_section=item.get("Section") or house,
                house=house if house in ("Commons", "Lords") else "Commons",
                rank=item.get("Rank"),
                text=text[:max_text_chars],
                url=url,
            ))
        except Exception:
            continue
    return contributions


def _compute_search_facets(contributions: list[HansardContribution]) -> tuple[dict[str, int], dict[str, int], tuple[date, date] | None]:
    """Compute party / house breakdown and date range across a returned page."""
    party_counter: Counter[str] = Counter()
    house_counter: Counter[str] = Counter()
    for c in contributions:
        party_counter[c.party or "Unknown"] += 1
        house_counter[c.house] += 1
    date_range: tuple[date, date] | None = None
    if contributions:
        dates = [c.date for c in contributions]
        date_range = (min(dates), max(dates))
    return dict(party_counter), dict(house_counter), date_range


def _safe_int(value, default: int = 0) -> int:
    """Coerce a string or int to int, returning default on failure.

    Used because /debates/divisions/{ext}.json returns numeric fields as
    strings (verified live 2026-05-29) — Id, AyesCount, NoesCount are all
    "192" not 192.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_top_debates_preview(payload: dict) -> list[TopDebate]:
    """Parse `Debates[]` preview from /search.json into TopDebate entries.

    Each Debates[] item is a SearchDebateItem (Swagger), with DebateSection,
    SittingDate, House, Title, Rank, DebateSectionExtId. There is no
    contribution count at this level; we use Rank as a proxy and document it.
    """
    out: list[TopDebate] = []
    for item in payload.get("Debates") or []:
        try:
            ext_id = item.get("DebateSectionExtId") or ""
            if not ext_id:
                continue
            sitting_iso = (item.get("SittingDate") or "1970-01-01")[:10]
            sitting_date = date.fromisoformat(sitting_iso)
            house_raw = item.get("House") or "Commons"
            house = house_raw if house_raw in ("Commons", "Lords") else "Commons"
            out.append(TopDebate(
                debate_id=0,
                debate_ext_id=ext_id,
                debate_title=(item.get("Title") or item.get("DebateSection") or "Unknown").strip(),
                date=sitting_date,
                house=house,
                contribution_count=_safe_int(item.get("Rank"), 0),
            ))
        except (ValueError, TypeError):
            continue
    return out


def _parse_division_match(item: dict) -> DivisionMatchLite | None:
    """Parse one DivisionOverview-shaped item into DivisionMatchLite.

    All numeric and boolean fields come back as strings from the live
    endpoints (verified 2026-05-29). Returns None on unrecoverable rows.
    """
    try:
        ext_id = item.get("ExternalId") or ""
        if not ext_id:
            return None
        date_iso = (item.get("Date") or "1970-01-01")[:10]
        sitting_date = date.fromisoformat(date_iso)
        house_raw = item.get("House") or "Commons"
        house = house_raw if house_raw in ("Commons", "Lords") else "Commons"
        time_raw = item.get("Time")
        # Time can come as None or a string like '16:41:00' or full datetime
        time_clean: str | None = None
        if isinstance(time_raw, str) and time_raw and time_raw not in ("None", "null"):
            time_clean = time_raw[-8:] if "T" in time_raw else time_raw

        return DivisionMatchLite(
            id=_safe_int(item.get("Id"), 0),
            external_id=ext_id,
            number=str(item.get("Number") or ""),
            date=sitting_date,
            time=time_clean,
            house=house,
            ayes_count=_safe_int(item.get("AyesCount"), 0),
            noes_count=_safe_int(item.get("NoesCount"), 0),
            motion_text=(item.get("TextBeforeVote") or None) or None,
            result_text=(item.get("TextAfterVote") or None) or None,
            debate_section=(item.get("DebateSection") or None),
            debate_section_ext_id=(item.get("DebateSectionExtId") or None),
        )
    except (ValueError, TypeError):
        return None


def _parse_top_divisions_preview(payload: dict) -> list[DivisionMatchLite]:
    """Parse `Divisions[]` preview from /search.json into DivisionMatchLite entries."""
    out: list[DivisionMatchLite] = []
    for item in payload.get("Divisions") or []:
        parsed = _parse_division_match(item)
        if parsed is not None:
            out.append(parsed)
    return out


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_hansard",
        annotations={"title": "Search Hansard Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_hansard(params: HansardSearchInput, ctx: Context) -> HansardSearchResult:
        """Search Hansard for parliamentary debates, questions, and speeches.

        Returns contributions with citation-grade metadata: member_id, attributed_to
        (the citable form), column_ref, debate_id, debate_ext_id, contribution_ext_id,
        and a synthesised public hansard.parliament.uk URL. Use the returned
        debate_ext_id and contribution_ext_id to drill into full content via the
        hansard:// resource family.

        Args:
            params: HansardSearchInput with query, optional date range, house, member, text_mode.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "searchTerm": f'"{params.query}"',
            "take": params.limit,
            "skip": params.offset,
        }
        if params.from_date:
            qp["startDate"] = params.from_date.isoformat()
        if params.to_date:
            qp["endDate"] = params.to_date.isoformat()
        if params.house != "both":
            qp["house"] = params.house
        if params.member:
            qp["member"] = params.member

        resp = await client.get(f"{HANSARD_API}/search.json", params=qp)
        resp.raise_for_status()
        payload = resp.json()
        contributions = _parse_hansard_contributions(payload, text_mode=params.text_mode)
        party_breakdown, house_breakdown, date_range = _compute_search_facets(contributions)
        return HansardSearchResult(
            query=params.query,
            from_date=params.from_date,
            to_date=params.to_date,
            house=params.house,
            member=params.member,
            text_mode=params.text_mode,
            offset=params.offset,
            limit=params.limit,
            total=len(contributions),
            total_corpus=payload.get("TotalContributions"),
            total_debates=payload.get("TotalDebates"),
            total_divisions=payload.get("TotalDivisions"),
            total_written_statements=payload.get("TotalWrittenStatements"),
            total_written_answers=payload.get("TotalWrittenAnswers"),
            total_corrections=payload.get("TotalCorrections"),
            total_petitions=payload.get("TotalPetitions"),
            total_committees=payload.get("TotalCommittees"),
            total_members=payload.get("TotalMembers"),
            top_debates=_parse_top_debates_preview(payload),
            top_divisions=_parse_top_divisions_preview(payload),
            party_breakdown=party_breakdown,
            house_breakdown=house_breakdown,
            date_range=date_range,
            has_more=len(contributions) == params.limit,
            contributions=contributions,
        )

    @mcp.tool(
        name="policy_position_summary",
        annotations={"title": "Hansard Policy Position Summary (deterministic facets)", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_policy_position_summary(params: PolicyPositionSummaryInput, ctx: Context) -> PolicyPositionSummary:
        """Aggregate Hansard debate-level signals on a topic. Pure counts — no LLM, no editorial labels.

        Sweeps /search/Debates.json with pagination (up to max_debates_scanned),
        then aggregates by_house, by_section, by_year, by_month, and top_debates
        from debate metadata. Also captures the corpus-wide envelope counts
        (total_contributions, total_written_statements, total_divisions, etc.)
        from /search.json for cross-section scope.

        Note on member-level facets: Hansard's search API exposes debate
        metadata, not per-contribution member identifiers, at the corpus
        level. by_party and top_contributors are therefore omitted from this
        deterministic summary. To see who spoke in a specific debate, read
        hansard://debate/{debate_ext_id}/header for an ordered contribution
        index, or call parliament_member_debates for one named member.

        Args:
            params: PolicyPositionSummaryInput with topic, optional date range, house, max_debates_scanned.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]

        # Pull corpus-wide envelope counts from /search.json (one call).
        envelope_qp: dict = {"searchTerm": f'"{params.topic}"'}
        if params.from_date:
            envelope_qp["startDate"] = params.from_date.isoformat()
        if params.to_date:
            envelope_qp["endDate"] = params.to_date.isoformat()
        if params.house != "both":
            envelope_qp["house"] = params.house
        envelope_resp = await client.get(f"{HANSARD_API}/search.json", params=envelope_qp)
        envelope_resp.raise_for_status()
        envelope = envelope_resp.json()

        # Paginate /search/Debates.json for per-debate facets.
        all_debates: list[dict] = []
        page_size = 50
        skip = 0
        target = params.max_debates_scanned

        while skip < target:
            take = min(page_size, target - skip)
            qp = dict(envelope_qp)
            qp["take"] = take
            qp["skip"] = skip
            try:
                resp = await client.get(f"{HANSARD_API}/search/Debates.json", params=qp)
                resp.raise_for_status()
            except httpx.HTTPError:
                if not all_debates:
                    raise
                break
            data = resp.json()
            results = data.get("Results") or []
            if not results:
                break
            all_debates.extend(results)
            if len(results) < take:
                break
            skip += take

        house_counter: Counter[str] = Counter()
        section_counter: Counter[str] = Counter()
        year_counter: Counter[int] = Counter()
        ym_counter: Counter[str] = Counter()
        top_debate_models: list[TopDebate] = []

        for d in all_debates:
            try:
                sitting_date = date.fromisoformat((d.get("SittingDate") or "1970-01-01")[:10])
            except ValueError:
                continue
            house_raw = d.get("House") or "Commons"
            house = house_raw if house_raw in ("Commons", "Lords") else "Commons"
            section = d.get("DebateSection") or house
            house_counter[house] += 1
            section_counter[section] += 1
            year_counter[sitting_date.year] += 1
            ym_counter[sitting_date.strftime("%Y-%m")] += 1
            ext_id = d.get("DebateSectionExtId") or ""
            if ext_id and len(top_debate_models) < 20:
                top_debate_models.append(TopDebate(
                    debate_id=0,
                    debate_ext_id=ext_id,
                    debate_title=(d.get("Title") or section or "Unknown").strip(),
                    date=sitting_date,
                    house=house,
                    contribution_count=int(d.get("Rank") or 0),
                ))

        recent_12 = sorted(ym_counter.items(), reverse=True)[:12]

        return PolicyPositionSummary(
            topic=params.topic,
            from_date=params.from_date,
            to_date=params.to_date,
            house=params.house,
            total_contributions=int(envelope.get("TotalContributions") or 0),
            total_debates=int(envelope.get("TotalDebates") or 0),
            total_written_statements=int(envelope.get("TotalWrittenStatements") or 0),
            total_written_answers=int(envelope.get("TotalWrittenAnswers") or 0),
            total_divisions=int(envelope.get("TotalDivisions") or 0),
            debates_scanned=len(all_debates),
            by_party=[],
            by_house=[FacetCount(key=k, count=v) for k, v in house_counter.most_common()],
            by_section=[FacetCount(key=k, count=v) for k, v in section_counter.most_common()],
            by_year=[FacetCount(key=str(k), count=v) for k, v in sorted(year_counter.items(), reverse=True)],
            by_month_recent_12=[FacetCount(key=k, count=v) for k, v in recent_12],
            top_contributors=[],
            top_debates=top_debate_models,
        )

    @mcp.tool(
        name="find_member",
        annotations={"title": "Find Member of Parliament", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_find_member(params: FindMemberInput, ctx: Context) -> MemberSearchResult:
        """Search for a current or former MP or Lord by name.

        Returns all members matching the name query, each with the integer
        `id` required by parliament_member_debates and parliament_member_interests,
        plus party, constituency, house, and current-sitting status.

        Args:
            params: FindMemberInput with the name (full or partial).
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        resp = await client.get(f"{MEMBERS_BASE}/Members/Search", params={"Name": params.name})
        resp.raise_for_status()

        members: list[MemberResult] = []
        for item in resp.json().get("items", []):
            v = item.get("value", item)
            house_id = v.get("latestHouseMembership", {}).get("house", 1)
            members.append(MemberResult(
                id=v.get("id", 0),
                name=v.get("nameDisplayAs", "Unknown"),
                party=v.get("latestParty", {}).get("name", "Unknown"),
                constituency=v.get("latestHouseMembership", {}).get("membershipFrom"),
                house="Commons" if house_id == 1 else "Lords",
                is_current=v.get("latestHouseMembership", {}).get("membershipStatus", {}).get("statusIsActive", False),
            ))

        return MemberSearchResult(query=params.name, total=len(members), members=members)

    @mcp.tool(
        name="member_debates",
        annotations={"title": "Get Member Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_debates(params: MemberDebatesInput, ctx: Context) -> MemberDebatesResult:
        """Retrieve Hansard contributions by a specific member, optionally filtered by topic.

        Use parliament_find_member first to obtain the integer member ID. Each
        contribution's text field is capped at 3000 characters.

        Args:
            params: MemberDebatesInput with member_id and optional topic filter.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "member": params.member_id,
            "take": params.limit,
            "skip": params.offset,
        }
        if params.topic:
            qp["searchTerm"] = f'"{params.topic}"'
        resp = await client.get(f"{HANSARD_API}/search.json", params=qp)
        resp.raise_for_status()
        contributions = _parse_hansard_contributions(resp.json())
        return MemberDebatesResult(
            member_id=params.member_id,
            topic=params.topic,
            offset=params.offset,
            limit=params.limit,
            total=len(contributions),
            has_more=len(contributions) == params.limit,
            contributions=contributions,
        )

    @mcp.tool(
        name="member_interests",
        annotations={"title": "Get Member Financial Interests", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_interests(params: MemberInterestsInput, ctx: Context) -> MemberInterestsPage:
        """Look up registered financial interests for a member of Parliament.

        Returns ONE PAGE of interests (default 20, caller controls via limit).
        For prolific members (big donors, many directorships, extensive land
        holdings), re-call with offset=offset+returned while has_more is true
        to paginate. Description text is capped per max_description_chars;
        raise it for forensic provenance work that needs the full narrative.

        Use parliament_find_member first to obtain the integer member_id.

        Args:
            params: member_id, optional category filter, pagination (offset/limit),
                and max_description_chars content cap.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "MemberId": params.member_id,
            "Skip": params.offset,
            "Take": params.limit,
        }
        if params.category:
            qp["CategoryId"] = INTEREST_CATEGORIES.get(params.category)

        resp = await client.get(f"{INTERESTS_BASE}/Interests", params=qp)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", data.get("results", []))

        interests: list[Interest] = []
        for item in items:
            created = item.get("registrationDate") or item.get("publishedDate")
            category_obj = item.get("category", {})
            category_name = category_obj.get("name", "Unknown") if isinstance(category_obj, dict) else str(category_obj)
            desc = item.get("summary", item.get("interest", ""))
            if len(desc) > params.max_description_chars:
                desc = desc[: params.max_description_chars] + " …[truncated]"
            interests.append(Interest(
                category=category_name,
                description=desc,
                date_created=date.fromisoformat(created[:10]) if created else None,
                date_amended=None,
            ))

        return MemberInterestsPage(
            member_id=params.member_id,
            category=params.category,
            offset=params.offset,
            limit=params.limit,
            returned=len(interests),
            has_more=len(items) == params.limit,  # full page → there may be more
            interests=interests,
        )

    @mcp.tool(
        name="search_petitions",
        annotations={"title": "Search UK Parliament Petitions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_petitions(params: PetitionSearchInput, ctx: Context) -> PetitionSearchResult:
        """Search UK Parliament petitions by keyword.

        Returns petition title, state, signature count, and dates for government response
        or parliamentary debate if applicable.

        Args:
            params: PetitionSearchInput with query and optional state filter.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        # petition.parliament.uk uses 1-indexed `page` and a `count` param (page size).
        page_num = (params.offset // params.limit) + 1
        qp: dict = {"q": params.query, "count": params.limit, "page": page_num}
        if params.state != "all":
            qp["state"] = params.state

        resp = await client.get(f"{PETITIONS_BASE}/petitions.json", params=qp)
        resp.raise_for_status()
        data = resp.json()

        petitions: list[PetitionSummary] = []
        for item in data.get("data", []):
            attrs = item.get("attributes", item)
            petition_id = item.get("id", 0)

            created = attrs.get("created_at")
            gov_resp = attrs.get("government_response_at")
            debate = attrs.get("debate_date") or attrs.get("scheduled_debate_date")

            petitions.append(PetitionSummary(
                id=int(petition_id) if petition_id else 0,
                action=attrs.get("action", "Unknown"),
                state=attrs.get("state", "unknown"),
                signature_count=attrs.get("signature_count", 0),
                created_at=date.fromisoformat(created[:10]) if created else None,
                government_response_at=date.fromisoformat(gov_resp[:10]) if gov_resp else None,
                debate_date=date.fromisoformat(debate[:10]) if debate else None,
                url=f"https://petition.parliament.uk/petitions/{petition_id}",
            ))
        return PetitionSearchResult(
            query=params.query,
            state=params.state,
            offset=params.offset,
            limit=params.limit,
            total=len(petitions),
            has_more=len(petitions) == params.limit,
            petitions=petitions,
        )

    @mcp.tool(
        name="get_debate_divisions",
        annotations={"title": "Get Divisions Held In A Debate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_get_debate_divisions(params: GetDebateDivisionsInput, ctx: Context) -> DebateDivisions:
        """Return the divisions (formal votes) held within a specific debate.

        Most debates contain no divisions — Business of the House sittings,
        statements, urgent questions, debates without a vote. A populated list
        typically appears around bill stages, motions, and contested amendments.

        For one named member's voting record across many divisions, use
        votes_search_divisions or chain via the returned `id` to votes_get_division.

        Args:
            params: GetDebateDivisionsInput with the debate_ext_id GUID
                (chain from parliament_search_hansard contribution.debate_ext_id
                or top_debates[].debate_ext_id).
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        try:
            resp = await client.get(
                f"{HANSARD_API}/debates/divisions/{params.debate_ext_id}.json"
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            # Surface the error; an empty array is the normal "no divisions" case,
            # whereas an HTTP error means we couldn't reach upstream.
            raise RuntimeError(format_http_error(e)) from e

        items = resp.json()
        if not isinstance(items, list):
            items = []
        divisions: list[DivisionMatchLite] = []
        for item in items:
            parsed = _parse_division_match(item) if isinstance(item, dict) else None
            if parsed is not None:
                divisions.append(parsed)

        return DebateDivisions(
            debate_ext_id=params.debate_ext_id,
            divisions=divisions,
        )

    @mcp.tool(
        name="lookup_by_column",
        annotations={"title": "Resolve A Hansard Column Citation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_lookup_by_column(params: LookupByColumnInput, ctx: Context) -> ColumnLookupResult:
        """Resolve an OSCOLA-style Hansard citation to a debate.

        Use case: you have a citation like 'HL Deb 14 Oct 2025, vol 849, col 200'
        and need to verify what was said at that column. This tool calls
        /search/debatebycolumn and returns the matching debate section(s); you
        then read hansard://debate/{debate_ext_id}/header to find the
        contribution at the cited column.

        Empty `matches` typically means:
          - The column is from a Daily Part (not yet consolidated into a Bound
            Volume). The endpoint only resolves Bound Volume citations
            (verified live 2026-05-29 — both `Source: 2` (recent) and `Source: 3`
            (older) debates were probed, only the older one resolves by column).
          - The volume_number is wrong (sometimes opposing counsel cites the
            running-volume number rather than the bound-volume number).
          - The column is in a Written Statement or Written Answer (the
            citation usually has a 'W' suffix like '1162W' — pass it as-is).

        Args:
            params: LookupByColumnInput with column_number (string), volume_number
                (int), and optional house. Date is NOT a valid lookup key — the
                endpoint requires the volume number.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "columnNumber": params.column_number,
            "volumeNumber": params.volume_number,
        }
        if params.house != "both":
            qp["house"] = params.house

        try:
            resp = await client.get(
                f"{HANSARD_API}/search/debatebycolumn.json",
                params=qp,
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(format_http_error(e)) from e

        payload = resp.json() if resp.content else {}
        results = payload.get("Results") or []
        matches: list[TopDebate] = []
        for item in results:
            try:
                ext_id = item.get("DebateSectionExtId") or ""
                if not ext_id:
                    continue
                sitting_iso = (item.get("SittingDate") or "1970-01-01")[:10]
                sitting_date = date.fromisoformat(sitting_iso)
                house_raw = item.get("House") or "Commons"
                house_val = house_raw if house_raw in ("Commons", "Lords") else "Commons"
                matches.append(TopDebate(
                    debate_id=0,
                    debate_ext_id=ext_id,
                    debate_title=(item.get("Title") or item.get("DebateSection") or "Unknown").strip(),
                    date=sitting_date,
                    house=house_val,
                    contribution_count=_safe_int(item.get("Rank"), 0),
                ))
            except (ValueError, TypeError):
                continue

        return ColumnLookupResult(
            column_number=params.column_number,
            volume_number=params.volume_number,
            house=params.house,
            total_results=_safe_int(payload.get("TotalResultCount"), len(matches)),
            matches=matches,
        )
