"""
Tools for the parliament module.

Upstream APIs (all public, no auth required):
  - hansard-api.parliament.uk  — debate contributions (search.json)
  - questions-statements-api.parliament.uk — written questions & answers
  - members-api.parliament.uk — MPs and Lords lookup
"""

import json
import re
from datetime import date
from typing import Literal

import httpx
from fastmcp import FastMCP, Context
from pydantic import BaseModel, ConfigDict, Field

from ...deps import format_http_error
from .models import HansardContribution, Interest, MemberInterests, MemberResult, PetitionSummary, PolicyVibeResult

HANSARD_API = "https://hansard-api.parliament.uk"
QS_BASE = "https://questions-statements-api.parliament.uk/api"
MEMBERS_BASE = "https://members-api.parliament.uk/api"
PETITIONS_BASE = "https://petition.parliament.uk"
INTERESTS_BASE = "https://interests-api.parliament.uk/api/v1"

INTEREST_CATEGORIES: dict[str, int] = {
    "employment": 1,
    "donations": 2,
    "gifts": 3,
    "overseas_visits": 4,
    "land": 6,
    "shareholdings": 7,
    "miscellaneous": 8,
    "family": 9,
    "lobbying": 10,
}

MAX_INTEREST_PAGES = 5
INTERESTS_PAGE_SIZE = 20


class HansardSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description=(
        "Full phrase to search in Hansard debates. Pass the complete topic, "
        "e.g. 'short selling regulation' or 'artificial intelligence liability'. "
        "Searched as an exact phrase — do not truncate to a single keyword."
    ), min_length=1, max_length=500)
    from_date: date | None = Field(None, description="Start date (YYYY-MM-DD)")
    to_date: date | None = Field(None, description="End date (YYYY-MM-DD)")
    member: str | None = Field(None, description="Filter by member name")


class PolicyVibeInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    policy_text: str = Field(..., description="Description of the policy proposal to assess", min_length=10, max_length=2000)
    topic: str = Field(..., description=(
        "Search terms for Hansard (keyword search, not phrase-matched). "
        "Use 2-5 key terms, e.g. 'artificial intelligence financial services regulation'. "
        "Broader terms return more contributions for sentiment analysis."
    ), min_length=2, max_length=200)


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


class PetitionSearchInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., description=(
        "Search term for petition titles, e.g. 'ban trophy hunting' or 'NHS funding'."
    ), min_length=2, max_length=300)
    state: Literal["open", "closed", "all"] = Field("all", description="Filter by petition state.")


class MemberInterestsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    member_id: int = Field(..., description=(
        "Parliament Members API integer ID. Get from parliament_find_member."
    ), ge=1)
    category: Literal[
        "employment", "donations", "gifts", "overseas_visits",
        "land", "shareholdings", "miscellaneous", "family", "lobbying",
    ] | None = Field(None, description="Filter by interest category. Omit for all categories.")


def _strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    clean = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", clean).strip()


def _parse_hansard_contributions(data: dict) -> list[HansardContribution]:
    """Parse hansard-api.parliament.uk search.json response."""
    contributions = []
    for item in data.get("Contributions", []):
        try:
            # Extract attribution like "Lord Carlile of Berriew (CB)"
            attr = item.get("AttributedTo", "")
            name = item.get("MemberName", "Unknown")
            party = None
            if "(" in attr and ")" in attr:
                party = attr[attr.rfind("(") + 1:attr.rfind(")")]

            text = _strip_html(item.get("ContributionText", ""))

            contributions.append(HansardContribution(
                member_name=name,
                party=party,
                constituency=None,
                date=date.fromisoformat(item.get("SittingDate", "1970-01-01")[:10]),
                debate_title=item.get("DebateSectionName", item.get("Section", "Unknown")),
                section=item.get("HansardSection", item.get("House", "Unknown")),
                text=text[:3000],
                url=item.get("Url", ""),
            ))
        except Exception:
            continue
    return contributions


def register_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="search_hansard",
        annotations={"title": "Search Hansard Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_hansard(params: HansardSearchInput, ctx: Context) -> str:
        """Search Hansard for parliamentary debates, questions, and speeches.

        Returns contributions from MPs and Lords including date, party, debate title,
        and text. Useful for understanding legislative intent or political context.

        Args:
            params (HansardSearchInput): query, optional date range, optional member filter.

        Returns:
            str: JSON array of contributions (member_name, party, date,
                debate_title, section, text, url).
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"searchTerm": f'"{params.query}"', "take": 20}
            if params.from_date:
                qp["startDate"] = params.from_date.isoformat()
            if params.to_date:
                qp["endDate"] = params.to_date.isoformat()
            if params.member:
                qp["member"] = params.member

            resp = await client.get(f"{HANSARD_API}/search.json", params=qp)
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())
            return json.dumps([c.model_dump(mode="json") for c in contributions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="vibe_check",
        annotations={"title": "Parliamentary Policy Vibe Check", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": False, "openWorldHint": True},
    )
    async def parliament_vibe_check(params: PolicyVibeInput, ctx: Context) -> str:
        """Assess the likely parliamentary reception of a policy proposal.

        Searches Hansard for relevant debate contributions, then uses LLM sampling
        to classify sentiment and extract supporters, opponents, and key concerns.

        Degrades gracefully if sampling is unavailable — returns contributions only.

        Args:
            params (PolicyVibeInput): policy_text (full description), topic (search keyword).

        Returns:
            str: JSON PolicyVibeResult with contributions, sentiment_summary,
                key_supporters, key_opponents, key_concerns.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            resp = await client.get(
                f"{HANSARD_API}/search.json",
                params={"searchTerm": params.topic, "take": 15},
            )
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())

            if not contributions:
                return PolicyVibeResult(
                    query=params.topic, contributions=[],
                    sentiment_summary="No Hansard contributions found for this topic.",
                    key_supporters=[], key_opponents=[], key_concerns=[],
                ).model_dump_json(indent=2)

            contributions_text = "\n\n".join(
                f"{c.member_name} ({c.party or 'Unknown'}, {c.date}):\n{c.text[:500]}"
                for c in contributions[:10]
            )
            sample_prompt = (
                f"Policy proposal: {params.policy_text}\n\n"
                f"Relevant Hansard contributions:\n{contributions_text}\n\n"
                f"Respond ONLY with a JSON object (no markdown fences):\n"
                '{"sentiment_summary": "...", "key_supporters": [...], '
                '"key_opponents": [...], "key_concerns": [...]}'
            )

            sentiment_summary = None
            key_supporters: list[str] = []
            key_opponents: list[str] = []
            key_concerns: list[str] = []

            try:
                result = await ctx.sample(sample_prompt, result_type=str)
                raw = (result.text or "").strip().lstrip("```json").lstrip("```").rstrip("```")
                parsed = json.loads(raw)
                sentiment_summary = parsed.get("sentiment_summary")
                key_supporters = parsed.get("key_supporters", [])
                key_opponents = parsed.get("key_opponents", [])
                key_concerns = parsed.get("key_concerns", [])
            except Exception:
                sentiment_summary = "Sentiment analysis unavailable (sampling not supported by this client)."

            return PolicyVibeResult(
                query=params.topic, contributions=contributions,
                sentiment_summary=sentiment_summary, key_supporters=key_supporters,
                key_opponents=key_opponents, key_concerns=key_concerns,
            ).model_dump_json(indent=2)

        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="find_member",
        annotations={"title": "Find Member of Parliament", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_find_member(params: FindMemberInput, ctx: Context) -> str:
        """Search for a current or former MP or Lord by name.

        Returns member ID, party, constituency, house, and current status.
        Use the integer member_id with parliament_member_debates.

        Args:
            params (FindMemberInput): name — full or partial member name.

        Returns:
            str: JSON array of MemberResult objects (id, name, party, constituency, house, is_current).
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            resp = await client.get(f"{MEMBERS_BASE}/Members/Search", params={"Name": params.name, "IsCurrentMember": "false"})
            resp.raise_for_status()
            data = resp.json()
            members = []
            for item in data.get("items", []):
                v = item.get("value", item)
                house_id = v.get("latestHouseMembership", {}).get("house", 1)
                members.append(MemberResult(
                    id=v.get("id", 0), name=v.get("nameDisplayAs", "Unknown"),
                    party=v.get("latestParty", {}).get("name", "Unknown"),
                    constituency=v.get("latestHouseMembership", {}).get("membershipFrom"),
                    house="Commons" if house_id == 1 else "Lords",
                    is_current=v.get("latestHouseMembership", {}).get("membershipStatus", {}).get("statusIsActive", False),
                ))
            return json.dumps([m.model_dump() for m in members], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="member_debates",
        annotations={"title": "Get Member Debates", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_debates(params: MemberDebatesInput, ctx: Context) -> str:
        """Retrieve Hansard contributions by a specific member, optionally filtered by topic.

        Use parliament_find_member first to obtain the integer member ID.

        Args:
            params (MemberDebatesInput): member_id (int), optional topic filter.

        Returns:
            str: JSON array of HansardContribution objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"member": params.member_id, "take": 20}
            if params.topic:
                qp["searchTerm"] = f'"{params.topic}"'
            resp = await client.get(f"{HANSARD_API}/search.json", params=qp)
            resp.raise_for_status()
            contributions = _parse_hansard_contributions(resp.json())
            return json.dumps([c.model_dump(mode="json") for c in contributions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="member_interests",
        annotations={"title": "Get Member Financial Interests", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_member_interests(params: MemberInterestsInput, ctx: Context) -> str:
        """Look up registered financial interests for a member of Parliament.

        Use parliament_find_member first to obtain the integer member ID.
        Returns interests across categories like employment, donations, land,
        shareholdings, and overseas visits.

        Args:
            params (MemberInterestsInput): member_id, optional category filter.

        Returns:
            str: JSON MemberInterests with list of Interest objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"MemberId": params.member_id, "Take": INTERESTS_PAGE_SIZE, "Skip": 0}
            if params.category:
                qp["CategoryId"] = INTEREST_CATEGORIES.get(params.category)

            all_interests: list[Interest] = []
            for page in range(MAX_INTEREST_PAGES):
                qp["Skip"] = page * INTERESTS_PAGE_SIZE
                resp = await client.get(f"{INTERESTS_BASE}/Interests", params=qp)
                resp.raise_for_status()
                data = resp.json()

                items = data.get("items", data.get("results", []))
                for item in items:
                    created = item.get("registrationDate") or item.get("publishedDate")
                    category_obj = item.get("category", {})
                    category_name = category_obj.get("name", "Unknown") if isinstance(category_obj, dict) else str(category_obj)
                    all_interests.append(Interest(
                        category=category_name,
                        description=item.get("summary", item.get("interest", "")),
                        date_created=date.fromisoformat(created[:10]) if created else None,
                        date_amended=None,
                    ))

                if len(items) < INTERESTS_PAGE_SIZE:
                    break

            result = MemberInterests(
                member_id=params.member_id,
                interests=all_interests,
                total=len(all_interests),
            )
            return result.model_dump_json(indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})

    @mcp.tool(
        name="search_petitions",
        annotations={"title": "Search UK Parliament Petitions", "readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": True},
    )
    async def parliament_search_petitions(params: PetitionSearchInput, ctx: Context) -> str:
        """Search UK Parliament petitions by keyword.

        Returns petition title, state, signature count, and dates for government response
        or parliamentary debate if applicable.

        Args:
            params (PetitionSearchInput): query and optional state filter.

        Returns:
            str: JSON array of PetitionSummary objects.
        """
        try:
            client: httpx.AsyncClient = ctx.lifespan_context["http"]
            qp: dict = {"q": params.query, "count": 20}
            if params.state != "all":
                qp["state"] = params.state

            resp = await client.get(f"{PETITIONS_BASE}/petitions.json", params=qp)
            resp.raise_for_status()
            data = resp.json()

            petitions = []
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
            return json.dumps([p.model_dump(mode="json") for p in petitions], indent=2)
        except Exception as e:
            return json.dumps({"error": format_http_error(e)})
