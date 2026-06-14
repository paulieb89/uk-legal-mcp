"""
Tools for the parliament module.

Upstream APIs (all public, no auth required):
  - hansard-api.parliament.uk  — debate contributions (search.json)
  - questions-statements-api.parliament.uk — written questions & answers
  - members-api.parliament.uk — MPs and Lords lookup
"""

import asyncio
import re
from datetime import date
from typing import Annotated, Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import Field

from collections import Counter

from ...deps import format_http_error
from .resources import _assign_columns, _item_is_contribution, _strip_html, hansard_source_label
from .models import (
    ColumnLookupResult,
    DebateDivisions,
    DivisionMatchLite,
    FacetCount,
    HansardContribution,
    HansardSearchResult,
    Interest,
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


def _parse_debate_item_as_contribution(
    item: dict,
    overview: dict,
    column_assignment: dict[int, tuple[int | None, int | None]],
    item_index: int,
    max_text_chars: int = 3000,
) -> HansardContribution | None:
    """Parse a single /debates/Debate/{ext}.json Items entry into HansardContribution.

    The Items shape differs from /search.json Contributions (no MemberName field,
    Value instead of ContributionText, ExternalId instead of ContributionExtId, and
    debate metadata lives on the Overview rather than the item itself), so we can't
    reuse _parse_hansard_contributions directly.

    `column_assignment` is the result of _assign_columns(items) for the full Items
    list — we look up this item's carry-forward column_start/end via item_index.

    Returns None on parse failure so the caller can filter and continue.
    """
    try:
        attr = (item.get("AttributedTo") or "").strip()
        if not attr:
            return None
        # Strip role/party suffix to recover the bare name. AttributedTo looks like
        # "Lord Pannick (CB)" or "The Parliamentary Under-Secretary (Baroness X) (Lab)";
        # the parenthesised tail is the party.
        name = attr
        party = None
        if "(" in attr and attr.endswith(")"):
            party = attr[attr.rfind("(") + 1:-1]
            name = attr[:attr.rfind("(")].strip()
            # If there's a role wrapper like "The Minister (Lord X)", peel one more.
            if name.endswith(")") and "(" in name:
                name = name[name.rfind("(") + 1:-1].strip()

        text = _strip_html(item.get("Value") or "")
        if not text:
            return None

        sitting_iso = (overview.get("Date") or "1970-01-01")[:10]
        sitting_date = date.fromisoformat(sitting_iso)
        house_raw = overview.get("House") or "Commons"
        house = house_raw if house_raw in ("Commons", "Lords") else "Commons"
        debate_ext_id = overview.get("ExtId") or ""
        debate_id = _safe_int(overview.get("Id"), 0)
        debate_title = (overview.get("Title") or "Unknown").strip() or "Unknown"
        chamber_section = overview.get("Location") or f"{house} Chamber"
        contribution_ext_id = item.get("ExternalId") or ""

        col_start, col_end = column_assignment.get(item_index, (None, None))

        url = _hansard_contribution_url(
            house, sitting_date, debate_ext_id, contribution_ext_id, debate_title
        ) if debate_ext_id and contribution_ext_id else ""

        return HansardContribution(
            member_name=name or "Unknown",
            member_id=item.get("MemberId"),
            attributed_to=attr,
            party=party,
            constituency=None,
            date=sitting_date,
            debate_title=debate_title,
            debate_id=debate_id,
            debate_ext_id=debate_ext_id,
            contribution_ext_id=contribution_ext_id,
            column_ref=item.get("HansardSection") or None,
            column_start=col_start,
            column_end=col_end,
            chamber_section=chamber_section,
            house=house,
            rank=None,
            text=text[:max_text_chars],
            url=url,
        )
    except Exception:
        return None


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
                relevance_rank=_safe_int(item.get("Rank"), 0),
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


async def _populate_votes_ids(
    client: httpx.AsyncClient, divisions: list[DivisionMatchLite]
) -> None:
    """Cross-resolve Hansard `id` to Lords/Commons Votes API `votes_id` in place.

    The Hansard-side `/debates/divisions/{ext}` endpoint returns its own
    integer IDs which are NOT the IDs the Lords/Commons Votes APIs use.
    Both APIs publish the same divisions but in different ID-spaces.
    The reliable join key is (date, house, number).

    One upstream HTTP per (date, house) group — typically one call per
    debate because all divisions in a debate share date+house.

    Lords endpoint: GET lordsvotes-api.parliament.uk/data/Divisions/search
      ?StartDate=YYYY-MM-DD&EndDate=YYYY-MM-DD
      → list[{divisionId, number, title, ...}] (camelCase JSON)
    Commons endpoint: GET commonsvotes-api.parliament.uk/data/divisions.json/search
      ?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
      → list[{DivisionId, Number, Title, ...}] (PascalCase JSON)

    Verified live 2026-05-29 against Lords debates of 14 Oct 2025
    (RRA Bill — Hansard Number=3 → Lords Votes divisionId=3392).
    """
    if not divisions:
        return
    # Group by (date, house) — usually all divisions share one group.
    groups: dict[tuple[date, str], list[DivisionMatchLite]] = {}
    for d in divisions:
        groups.setdefault((d.date, d.house), []).append(d)

    for (sitting_date, house), group in groups.items():
        date_iso = sitting_date.isoformat()
        if house == "Lords":
            url = "https://lordsvotes-api.parliament.uk/data/Divisions/search"
            params = {"StartDate": date_iso, "EndDate": date_iso}
        else:
            url = "https://commonsvotes-api.parliament.uk/data/divisions.json/search"
            params = {"startDate": date_iso, "endDate": date_iso}
        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError:
            continue
        if not isinstance(payload, list):
            continue
        # Build number → votes_id map (case-tolerant for the two key conventions)
        by_number: dict[str, int] = {}
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            n = entry.get("number") if "number" in entry else entry.get("Number")
            div_id = entry.get("divisionId") if "divisionId" in entry else entry.get("DivisionId")
            if n is None or div_id is None:
                continue
            try:
                by_number[str(n)] = int(div_id)
            except (TypeError, ValueError):
                continue
        # Apply the resolved IDs back to the divisions in this group
        for div in group:
            if div.number and div.number in by_number:
                div.votes_id = by_number[div.number]


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
    async def parliament_search_hansard(
        query: Annotated[str, Field(description="Phrase to find in Hansard contribution text bodies. Hansard searches the words members actually said in their speeches — NOT debate titles, topic metadata, or written headlines. Pass tokens that would appear in someone's speech: distinctive arguments ('disproportionate sanction'), statutory references ('section 21'), or specific phrases. Bill titles (e.g. 'Renters\\'s Rights Bill') often DON'T match because members refer to 'the Bill' or 'this legislation' in their speeches. Tokenised matching: 'housing benefit fraud' will match contributions saying 'fraud in housing benefit claims'. For 'all contributions in a specific debate' regardless of words used, drill via top_debates[].debate_ext_id into parliament_get_debate_contributions.", min_length=1, max_length=500)],
        ctx: Context,
        from_date: Annotated[date | None, Field(description="Start date (YYYY-MM-DD)")] = None,
        to_date: Annotated[date | None, Field(description="End date (YYYY-MM-DD)")] = None,
        house: Annotated[Literal["Commons", "Lords", "both"], Field(description="Restrict to one House. Default 'both' returns Commons + Lords contributions.")] = "both",
        member_id: Annotated[int | None, Field(description="Filter to contributions by a single member. Pass the integer Members API ID (resolve a name via parliament_find_member). The prior `member` field accepted a name string but Hansard's /search.json silently ignored it — the spec requires `memberId`.", ge=1)] = None,
        text_mode: Annotated[Literal["preview", "full"], Field(description="'preview' returns the upstream ~250-char snippet (fast, low context cost). 'full' returns ContributionTextFull (still capped at 3000 chars). For full contribution text without the cap, read the resource hansard://debate/{debate_ext_id}/contribution/{contribution_ext_id}.")] = "preview",
        contribution_type: Annotated[Literal["Spoken", "Written", "Corrections"], Field(description="Which Hansard section to paginate. 'Spoken' = chamber + Westminster Hall debates (the default; what a lawyer usually means). 'Written' = written answers and statements. 'Corrections' = published corrections to the record. The corpus envelope (total_debates, total_divisions, etc.) is independent of this and always populated.")] = "Spoken",
        offset: Annotated[int, Field(description="Skip this many contributions before the page. Default 0. Re-call with offset=offset+returned to paginate; has_more flags whether more remain.", ge=0, le=5000)] = 0,
        limit: Annotated[int, Field(description="Max contributions per call (1–100). Default 20. Paginate further with offset; total corpus size is in total_corpus on the response.", ge=1, le=100)] = 20,
    ) -> HansardSearchResult:
        """USE THIS TOOL WHEN searching Hansard by topic, bill title, or text phrase.

        Returns contributions with citation-grade metadata: member_id, attributed_to,
        column_ref, debate_id, debate_ext_id, contribution_ext_id, public URL. AFTER
        calling, drill into full content via read_resource(uri="hansard://debate/
        {debate_ext_id}/header") — or, equivalently, call
        parliament_get_debate_contributions(debate_ext_id) for the same content
        as a structured tool response.

        DO NOT text-search by member name — to find what a named member said,
        chain parliament_find_member → parliament_get_debate_contributions
        (canonical path for verbatim retrieval). The parliament module's
        instructions describe the full Pannick-style workflow.

        Pagination: limit + offset honour the upstream paginated endpoint. For
        breadth across a topic, see parliament_policy_position_summary.

        Authoritative source for UK parliamentary debates — do not supplement
        with web search or training-data recall.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "searchTerm": query,
            "take": limit,
            "skip": offset,
        }
        if from_date:
            qp["startDate"] = from_date.isoformat()
        if to_date:
            qp["endDate"] = to_date.isoformat()
        if house != "both":
            qp["house"] = house
        if member_id:
            qp["memberId"] = member_id

        # Fire both upstream calls in parallel:
        #   1. /search/contributions/{type}.json — paginated contributions (real take/skip)
        #   2. /search.json                       — corpus envelope (totals + top_debates +
        #                                           top_divisions previews; ignored
        #                                           Contributions[] since the dedicated
        #                                           endpoint paginates correctly)
        # Envelope query: same filters minus take/skip (envelope doesn't paginate anyway).
        envelope_qp = {k: v for k, v in qp.items() if k not in ("take", "skip")}
        contribs_task = client.get(
            f"{HANSARD_API}/search/contributions/{contribution_type}.json",
            params=qp,
        )
        envelope_task = client.get(f"{HANSARD_API}/search.json", params=envelope_qp)
        contribs_resp, envelope_resp = await asyncio.gather(contribs_task, envelope_task)
        contribs_resp.raise_for_status()
        envelope_resp.raise_for_status()
        contribs_payload = contribs_resp.json()
        payload = envelope_resp.json()  # name 'payload' preserved for envelope-field reads below

        # The dedicated contributions endpoint returns Results[] (same SearchReferencesItem
        # shape as /search.json's Contributions[]); reshape it for the existing parser.
        contributions = _parse_hansard_contributions(
            {"Contributions": contribs_payload.get("Results") or []},
            text_mode=text_mode,
        )
        # Use the dedicated endpoint's total when available — it's authoritative for the
        # contribution category we paginated; fall back to /search.json's TotalContributions.
        total_corpus = contribs_payload.get("TotalResultCount") or payload.get("TotalContributions")
        party_breakdown, house_breakdown, date_range = _compute_search_facets(contributions)
        return HansardSearchResult(
            query=query,
            from_date=from_date,
            to_date=to_date,
            house=house,
            member_id=member_id,
            text_mode=text_mode,
            offset=offset,
            limit=limit,
            total=len(contributions),
            total_corpus=total_corpus,
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
            has_more=len(contributions) == limit,
            contributions=contributions,
        )

    @mcp.tool(
        name="policy_position_summary",
        annotations={"title": "Hansard Policy Position Summary (deterministic facets)", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_policy_position_summary(
        topic: Annotated[str, Field(description="Phrase to find in Hansard contribution text bodies for the facet aggregation. Same semantics as parliament_search_hansard.query: tokens that appear in members' actual speeches, not bill titles or topic metadata. The aggregator sweeps top_debates[] returned by /search/Debates.json — those debates are matched on the phrase appearing in titles or contribution text, so passing a Bill title (e.g. 'Renters\\' Rights Bill') usually works for THIS tool even though it wouldn't for member-level text search, because debate-level matching uses metadata in addition to body text.", min_length=2, max_length=200)],
        ctx: Context,
        from_date: Annotated[date | None, Field(description="Start date (YYYY-MM-DD)")] = None,
        to_date: Annotated[date | None, Field(description="End date (YYYY-MM-DD)")] = None,
        house: Annotated[Literal["Commons", "Lords", "both"], Field(description="Restrict to one House. Default 'both'.")] = "both",
        max_debates_scanned: Annotated[int, Field(description="Hard cap on debates sampled from /search/Debates.json to compute facets. Default 200 issues ≤4 upstream calls (take=50 each). Raise to 2000 (≤40 calls) for an exhaustive sweep on a heavily-debated topic. Hansard rate limit: 1000 req/5min.", ge=50, le=2000)] = 200,
    ) -> PolicyPositionSummary:
        """USE THIS TOOL WHEN you want debate-level corpus signals on a topic — by_house, by_year, by_section breakdowns — without reading every contribution.

        Aggregates Hansard debate-level signals on a topic. Pure counts — no LLM,
        no editorial labels. Sweeps /search/Debates.json with pagination (up to
        max_debates_scanned), then aggregates by_house, by_section, by_year,
        by_month, and top_debates from debate metadata. Also captures the
        corpus-wide envelope counts (total_contributions, total_written_statements,
        total_divisions, etc.) from /search.json for cross-section scope.

        AFTER calling, pick a debate from top_debates and pass its debate_ext_id
        into parliament_get_debate_contributions to drill into who said what.

        Note on member-level facets: Hansard's search API exposes debate
        metadata, not per-contribution member identifiers, at the corpus
        level. by_party and top_contributors are therefore omitted from this
        deterministic summary. To see who spoke in a specific debate, read
        hansard://debate/{debate_ext_id}/header for an ordered contribution
        index, or call parliament_member_debates for one named member.

        This is the authoritative source for UK Hansard corpus-level signals.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]

        # Pull corpus-wide envelope counts from /search.json (one call).
        envelope_qp: dict = {"searchTerm": topic}
        if from_date:
            envelope_qp["startDate"] = from_date.isoformat()
        if to_date:
            envelope_qp["endDate"] = to_date.isoformat()
        if house != "both":
            envelope_qp["house"] = house
        envelope_resp = await client.get(f"{HANSARD_API}/search.json", params=envelope_qp)
        envelope_resp.raise_for_status()
        envelope = envelope_resp.json()

        # Paginate /search/Debates.json for per-debate facets.
        all_debates: list[dict] = []
        page_size = 50
        skip = 0
        target = max_debates_scanned

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
            house_val = house_raw if house_raw in ("Commons", "Lords") else "Commons"
            section = d.get("DebateSection") or house_val
            house_counter[house_val] += 1
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
                    house=house_val,
                    relevance_rank=int(d.get("Rank") or 0),
                ))

        recent_12 = sorted(ym_counter.items(), reverse=True)[:12]

        return PolicyPositionSummary(
            topic=topic,
            from_date=from_date,
            to_date=to_date,
            house=house,
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
    async def parliament_find_member(
        name: Annotated[str, Field(description="Name or partial name, e.g. 'Starmer', 'Baroness Hale'", min_length=2, max_length=200)],
        ctx: Context,
    ) -> MemberSearchResult:
        """USE THIS TOOL WHEN you have a member's name and need their integer member_id.

        Returns all members matching the name query, each with the integer `id`,
        party, constituency, house, and current-sitting status. Disambiguates
        common-name matches (e.g. "Lord Smith" returns multiple peers).

        CALL THIS BEFORE any tool that filters by member_id — including
        parliament_get_debate_contributions, parliament_member_debates, and
        parliament_member_interests. Name → ID first; ID-based filtering second.
        Skipping this step and text-searching by name returns unrelated results
        (see parliament_search_hansard's anti-bypass note for the Pannick case).
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        resp = await client.get(f"{MEMBERS_BASE}/Members/Search", params={"Name": name})
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

        return MemberSearchResult(query=name, total=len(members), members=members)

    @mcp.tool(
        name="member_debates",
        annotations={"title": "Get Member Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_debates(
        member_id: Annotated[int, Field(description="Parliament Members API integer ID. Obtain from parliament_find_member.", ge=1)],
        ctx: Context,
        topic: Annotated[str | None, Field(description="Optional phrase to find in THIS member's contribution text bodies. Hansard searches the words the member actually said, NOT the topic or title of the debate. Pass tokens this member would have spoken — distinctive arguments ('disproportionate sanction'), statutory references ('section 21'), or motion numbers ('Motion C1') — not the bill's name (members rarely say e.g. 'Renters\\' Rights Bill' verbatim in their speeches). If you want 'every contribution this member made in a specific debate' regardless of words used, find the debate_ext_id then use parliament_get_debate_contributions(debate_ext_id, member_id=...).")] = None,
        offset: Annotated[int, Field(description="Number of contributions to skip before this page. Default 0. Re-call with offset=offset+returned while has_more is true.", ge=0, le=2000)] = 0,
        limit: Annotated[int, Field(description="Maximum contributions to return. Default 20.", ge=1, le=100)] = 20,
    ) -> MemberDebatesResult:
        """USE THIS TOOL WHEN you have a member_id and want contributions where THAT member used a specific topic phrase verbatim (text-body search).

        CALL parliament_find_member(name) FIRST to obtain the integer member_id.

        This is a name-based text-body search — it matches contributions whose
        TEXT contains the topic phrase. A member who spoke in a debate but
        didn't use your phrase verbatim is filtered out. For verbatim retrieval
        of every contribution by a member in a known debate (regardless of
        vocabulary), use parliament_get_debate_contributions(debate_ext_id,
        member_id=...) instead.

        Each contribution's text field is capped at 3000 characters.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "memberId": member_id,
            "take": limit,
            "skip": offset,
        }
        if topic:
            qp["searchTerm"] = topic
        resp = await client.get(f"{HANSARD_API}/search.json", params=qp)
        resp.raise_for_status()
        contributions = _parse_hansard_contributions(resp.json())
        return MemberDebatesResult(
            member_id=member_id,
            topic=topic,
            offset=offset,
            limit=limit,
            total=len(contributions),
            has_more=len(contributions) == limit,
            contributions=contributions,
        )

    @mcp.tool(
        name="member_interests",
        annotations={"title": "Get Member Financial Interests", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_interests(
        member_id: Annotated[int, Field(description="Parliament Members API integer ID. Get from parliament_find_member.", ge=1)],
        ctx: Context,
        category: Annotated[Literal["employment", "employment_adhoc", "employment_ongoing", "donations", "gifts_uk", "overseas_visits", "gifts_overseas", "land", "shareholdings", "miscellaneous", "family_employed", "family_lobbying"] | None, Field(description="Filter by interest category. Common categories: 'donations' (donations and support), 'gifts_uk' (gifts/hospitality from UK), 'employment' (employment and earnings), 'land' (land and property), 'shareholdings', 'overseas_visits'. Omit for all categories.")] = None,
        offset: Annotated[int, Field(description="Number of interests to skip before this page. Default 0 for the first page. To paginate prolific members (100+ interests), re-call with offset=offset+returned while the previous response had has_more=true.", ge=0, le=500)] = 0,
        limit: Annotated[int, Field(description="Max interests per call. Hard-capped at 20 by the upstream interests-api.parliament.uk (verified live 2026-05-29: Take=100 still returns 20). For prolific members, paginate via offset; total size is in totalResults on the response.", ge=1, le=20)] = 20,
        max_description_chars: Annotated[int, Field(description="Per-entry cap on the free-text description field. Default 500 prevents context blow-up on members with lengthy donation or directorship narratives. Raise to 2000+ only for forensic provenance work.", ge=50, le=5000)] = 500,
    ) -> MemberInterestsPage:
        """USE THIS TOOL WHEN you have a member_id and need their registered financial interests (donations, directorships, land, gifts).

        CALL parliament_find_member(name) FIRST to obtain the integer member_id.

        Returns ONE PAGE of interests (default 20, caller controls via limit).
        For prolific members (big donors, many directorships, extensive land
        holdings), re-call with offset=offset+returned while has_more is true
        to paginate. Description text is capped per max_description_chars;
        raise it for forensic provenance work that needs the full narrative.

        This is the authoritative source for UK MP and peer financial-interest
        declarations (via the Members API). Web search returns stale snapshots.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "MemberId": member_id,
            "Skip": offset,
            "Take": limit,
        }
        if category:
            qp["CategoryId"] = INTEREST_CATEGORIES.get(category)

        resp = await client.get(f"{INTERESTS_BASE}/Interests", params=qp)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items", data.get("results", []))

        interests: list[Interest] = []
        for item in items:
            created = item.get("registrationDate") or item.get("publishedDate")
            category_obj = item.get("category", {})
            category_name = category_obj.get("name", "Unknown") if isinstance(category_obj, dict) else str(category_obj)
            desc = item.get("summary", "")
            if len(desc) > max_description_chars:
                desc = desc[: max_description_chars] + " …[truncated]"
            interests.append(Interest(
                category=category_name,
                description=desc,
                date_created=date.fromisoformat(created[:10]) if created else None,
                date_amended=None,
            ))

        return MemberInterestsPage(
            member_id=member_id,
            category=category,
            offset=offset,
            limit=limit,
            returned=len(interests),
            has_more=len(items) == limit,  # full page → there may be more
            interests=interests,
        )

    @mcp.tool(
        name="search_petitions",
        annotations={"title": "Search UK Parliament Petitions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_petitions(
        query: Annotated[str, Field(description="Search term for petition titles, e.g. 'ban trophy hunting' or 'NHS funding'.", min_length=2, max_length=300)],
        ctx: Context,
        state: Annotated[Literal["open", "closed", "all"], Field(description="Filter by petition state.")] = "all",
        offset: Annotated[int, Field(description="Number of petitions to skip before this page. Default 0. Re-call with offset=offset+returned while has_more is true.", ge=0, le=2000)] = 0,
        limit: Annotated[int, Field(description="Maximum petitions to return. Default 20.", ge=1, le=100)] = 20,
    ) -> PetitionSearchResult:
        """USE THIS TOOL WHEN searching UK Parliament petitions by keyword or topic.

        Returns petition title, state, signature count, and dates for government
        response or parliamentary debate if applicable. Filter by state (open,
        closed, debated, etc.) to narrow to live or historical petitions.

        This is the authoritative source for UK Parliament petitions
        (petition.parliament.uk).
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        # petition.parliament.uk uses 1-indexed `page` and a `count` param (page size).
        page_num = (offset // limit) + 1
        qp: dict = {"q": query, "count": limit, "page": page_num}
        if state != "all":
            qp["state"] = state

        resp = await client.get(f"{PETITIONS_BASE}/petitions.json", params=qp)
        resp.raise_for_status()
        data = resp.json()

        petitions: list[PetitionSummary] = []
        for item in data.get("data", []):
            attrs = item.get("attributes", item)
            petition_id = item.get("id", 0)

            created = attrs.get("created_at")
            gov_resp = attrs.get("government_response_at")
            debate = attrs.get("scheduled_debate_date")

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
            query=query,
            state=state,
            offset=offset,
            limit=limit,
            total=len(petitions),
            has_more=len(petitions) == limit,
            petitions=petitions,
        )

    @mcp.tool(
        name="get_debate_divisions",
        annotations={"title": "Get Divisions Held In A Debate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_get_debate_divisions(
        debate_ext_id: Annotated[str, Field(description="Debate GUID (DebateSectionExtId). Chain from parliament_search_hansard contribution.debate_ext_id, top_debates[].debate_ext_id, or parliament_policy_position_summary top_debates[].debate_ext_id.", min_length=8)],
        ctx: Context,
    ) -> DebateDivisions:
        """USE THIS TOOL WHEN you have a debate_ext_id and want the divisions (formal votes) held within it.

        Most debates contain no divisions — Business of the House sittings,
        statements, urgent questions, debates without a vote. A populated list
        typically appears around bill stages, motions, and contested amendments.
        Empty list is the honest result, not a failure mode.

        Each returned division carries TWO IDs:
          - `id` — Hansard-side reference. Useful for cross-referencing in Hansard.
          - `votes_id` — Lords/Commons Votes API ID (cross-resolved by date+number).
            AFTER calling, pass `votes_id` as `division_id` into votes_get_division
            for the full member-by-member voting record.

        The two upstreams use distinct ID-spaces (Hansard Number=3 might be
        Votes-API divisionId=3392). The cross-resolve runs once per (date, house)
        group — typically one extra HTTP per debate. `votes_id` is None when the
        cross-resolve found no match.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        try:
            resp = await client.get(
                f"{HANSARD_API}/debates/divisions/{debate_ext_id}.json"
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

        # Cross-resolve Hansard-side `id` to Lords/Commons Votes API `votes_id`
        # by (date, house) group. Same (date, house) → single upstream call.
        await _populate_votes_ids(client, divisions)

        return DebateDivisions(
            debate_ext_id=debate_ext_id,
            divisions=divisions,
        )

    @mcp.tool(
        name="get_debate_contributions",
        annotations={"title": "Get Contributions In A Debate", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_get_debate_contributions(
        debate_ext_id: Annotated[str, Field(description="Debate GUID (DebateSectionExtId). Chain from parliament_search_hansard top_debates[].debate_ext_id, parliament_lookup_by_column matches[].debate_ext_id, or any tool that surfaces a debate identifier.", min_length=8)],
        ctx: Context,
        member_id: Annotated[int | None, Field(description="Optional integer Members API ID. When given, only that member's contributions in this debate are returned — regardless of which words they used. Resolves via parliament_find_member. When omitted, every contribution in the debate is returned (typical debate: 100-200 items).", ge=1)] = None,
    ) -> MemberDebatesResult:
        """USE THIS TOOL WHEN you have a debate_ext_id and want verbatim contributions, optionally filtered to one member.

        Canonical path for "everything a member said in this debate" regardless
        of vocabulary — text-search tools (parliament_member_debates,
        parliament_search_hansard) filter by contribution TEXT, dropping members
        who spoke without using your phrase verbatim. This tool filters by
        MemberId on the debate's Items list, so vocabulary doesn't matter.

        Typical chain: parliament_find_member(name) → member_id, then
        parliament_search_hansard or parliament_lookup_by_column → debate_ext_id,
        then this tool. The parliament module's instructions describe the full
        composition pattern.

        Without member_id, returns every contribution (~100-200 for a long debate).

        If the wire returns no contributions for a member you expect to have
        spoken, report the empty result honestly — do NOT reconstruct quotes
        from training data. Authoritative source for member contributions.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        try:
            resp = await client.get(
                f"{HANSARD_API}/debates/Debate/{debate_ext_id}.json"
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise RuntimeError(format_http_error(e)) from e

        payload = resp.json() if resp.content else {}
        overview = payload.get("Overview") or {}
        items = payload.get("Items") or []

        # Carry-forward column data must be computed across the FULL Items list
        # (filtering first would lose column boundaries the parser needs).
        column_assignment = _assign_columns(items)

        contributions: list[HansardContribution] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if not _item_is_contribution(item):
                continue
            if member_id is not None and item.get("MemberId") != member_id:
                continue
            parsed = _parse_debate_item_as_contribution(item, overview, column_assignment, idx)
            if parsed is not None:
                contributions.append(parsed)

        return MemberDebatesResult(
            member_id=member_id if member_id is not None else 0,
            topic=None,
            offset=0,
            limit=len(contributions),
            total=len(contributions),
            has_more=False,
            contributions=contributions,
        )

    @mcp.tool(
        name="lookup_by_column",
        annotations={"title": "Resolve A Hansard Column Citation", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_lookup_by_column(
        column_number: Annotated[str, Field(description="Hansard column number from an OSCOLA footnote, e.g. '200' for 'HL Deb 14 Oct 2025, vol 849, col 200'. String (not integer) to accommodate column suffixes like '1162W' for written answers.", min_length=1, max_length=20)],
        volume_number: Annotated[int, Field(description="Hansard volume number (the 'vol 849' part of an OSCOLA citation). Required — the endpoint only resolves citations when given the volume; sitting date is NOT a substitute (verified live 2026-05-29).", gt=0)],
        ctx: Context,
        house: Annotated[Literal["Commons", "Lords", "both"], Field(description="Restrict to one House. Default 'both' searches across both Houses.")] = "both",
    ) -> ColumnLookupResult:
        """USE THIS TOOL WHEN you have an OSCOLA-style Hansard citation (column + volume + house) and need the debate.

        Example input: 'HL Deb 14 Oct 2025, vol 849, col 200'. AFTER calling, read
        the contribution at the cited column via
        read_resource(uri="hansard://debate/{debate_ext_id}/header") — or,
        equivalently, call parliament_get_debate_contributions(debate_ext_id) for
        the full list as a structured tool response.

        Each match carries:
          - `contribution_count` — real contribution count from the debate's Items
          - `source` / `source_code` — citation finality (1=Rolling, 2=Daily,
            3=BoundVolume, 4=Historic). Resolution is NOT gated on publication state.

        Empty `matches` typically means the volume_number is wrong (opposing
        counsel sometimes cites running-volume rather than bound-volume) or the
        column is in a Written Statement (use the 'W'-suffixed column as-is).
        It does NOT mean the citation is fabricated — surface the failure.

        Authoritative source for OSCOLA Hansard column resolution.
        """
        client: httpx.AsyncClient = ctx.lifespan_context["http"]
        qp: dict = {
            "columnNumber": column_number,
            "volumeNumber": volume_number,
        }
        if house != "both":
            qp["house"] = house

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

                # Secondary call: fetch the debate's Items list to populate the
                # real contribution_count. Column-lookup is the headline lawyer
                # use case (verifying opposing counsel's citation) and benefits
                # from the real count; one extra HTTP call per match is fine.
                # Uses the same _item_is_contribution filter as the
                # hansard://debate/{ext}/header resource so the two surfaces
                # agree on the count (Obs 173 — single source-of-truth for
                # "what counts as a contribution").
                contribution_count: int | None = None
                source_code: int | None = None
                try:
                    debate_resp = await client.get(f"{HANSARD_API}/debates/Debate/{ext_id}.json")
                    debate_resp.raise_for_status()
                    debate_payload = debate_resp.json() if debate_resp.content else {}
                    debate_items = debate_payload.get("Items") or []
                    contribution_count = sum(1 for i in debate_items if _item_is_contribution(i))
                    # The payload's Overview.Source gives the citation's publication
                    # state for free — surface it so the lawyer sees finality without
                    # a second fetch of hansard://debate/{ext}/header.
                    source_code = (debate_payload.get("Overview") or {}).get("Source")
                except httpx.HTTPError:
                    # Leave count/source as None rather than fabricate values.
                    pass

                matches.append(TopDebate(
                    debate_id=_safe_int(item.get("DebateSectionId"), 0),
                    debate_ext_id=ext_id,
                    debate_title=(item.get("Title") or item.get("DebateSection") or "Unknown").strip(),
                    date=sitting_date,
                    house=house_val,
                    relevance_rank=None,  # column-search endpoint does not rank
                    contribution_count=contribution_count,
                    source_code=source_code,
                    source=hansard_source_label(source_code),
                ))
            except (ValueError, TypeError):
                continue

        return ColumnLookupResult(
            column_number=column_number,
            volume_number=volume_number,
            house=house,
            total_results=_safe_int(payload.get("TotalResultCount"), len(matches)),
            matches=matches,
        )
